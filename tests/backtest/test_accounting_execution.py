from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.backtest import (
    AccountingFillBatch,
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    FillAccountingEvent,
    HistoricalExecutionSource,
    MarketOrderRequest,
    OrderSide,
    PerformanceEventBatch,
    PositionAccountingEngine,
    PositionMark,
    PositionSide,
    TradePerformanceAnalyzer,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def candle(index: int, open_price: int, close_price: int) -> Candle:
    open_time = START + index * Timeframe.MINUTES_15.duration
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=Decimal(open_price),
        high=Decimal(max(open_price, close_price) + 10),
        low=Decimal(min(open_price, close_price) - 10),
        close=Decimal(close_price),
        volume=Decimal(5),
    )


def test_parquet_execution_fills_feed_deterministic_accounting_and_causal_mark(
    tmp_path: Path,
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = candle(0, 100, 105)
    second = candle(1, 120, 125)
    store.merge(SOURCE, (first, second))
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal(10),
        slippage_bps=Decimal(20),
        fee_bps=Decimal(20),
    )
    requests = (
        MarketOrderRequest("buy", START, SYMBOL, OrderSide.BUY, Decimal(1)),
        MarketOrderRequest(
            "sell",
            START + timedelta(seconds=1),
            SYMBOL,
            OrderSide.SELL,
            Decimal(1),
        ),
    )
    execution = BacktestExecutionSimulator(HistoricalExecutionSource(store)).run(
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        costs,
        requests,
        start=START,
        end=second.close_time,
    )
    ledger = PositionAccountingEngine()
    buy_event = ledger.process(
        AccountingFillBatch(execution.fills[0].fill_at, (execution.fills[0],))
    ).events[0]
    causal_mark = PositionMark(SYMBOL, first.close_time, first.close)
    marked = ledger.snapshot(SYMBOL, causal_mark.observed_at, mark=causal_mark)
    sell_event = ledger.process(
        AccountingFillBatch(execution.fills[1].fill_at, (execution.fills[1],))
    ).events[0]

    assert buy_event.fill_price == Decimal("100.25")
    assert buy_event.fee_quote == Decimal("0.2005")
    assert marked.side is PositionSide.LONG
    assert marked.unrealized_gross_pnl == Decimal("4.75")
    assert marked.total_net_pnl == Decimal("4.5495")
    assert sell_event.fill_price == Decimal("119.700")
    assert sell_event.gross_realized_pnl_delta == Decimal("19.450")
    assert sell_event.cumulative_fees == Decimal("0.439900")
    assert sell_event.cumulative_net_realized_pnl == Decimal("19.010100")
    assert sell_event.position_quantity_after == 0
    assert sell_event.average_entry_after is None

    analyzer = TradePerformanceAnalyzer()
    analyzer.process(PerformanceEventBatch(buy_event.fill_at, (buy_event,)))
    analyzer.process(PerformanceEventBatch(sell_event.fill_at, (sell_event,)))
    performance = analyzer.result()
    assert performance.open_trade_count == 0
    assert performance.portfolio_metrics.completed_trade_count == 1
    assert performance.completed_trades[0].gross_realized_pnl == Decimal("19.450")
    assert performance.completed_trades[0].allocated_fees == Decimal("0.439900")
    assert performance.completed_trades[0].net_pnl == Decimal("19.010100")


@pytest.mark.parametrize(
    ("entry_side", "exit_side", "exit_open", "entry_fill", "exit_fill", "fees", "net"),
    [
        (
            OrderSide.BUY,
            OrderSide.SELL,
            90,
            Decimal("100.03"),
            Decimal("89.973"),
            Decimal("0.190003"),
            Decimal("-10.247003"),
        ),
        (
            OrderSide.SELL,
            OrderSide.BUY,
            110,
            Decimal("99.97"),
            Decimal("110.033"),
            Decimal("0.210003"),
            Decimal("-10.273003"),
        ),
    ],
)
def test_two_two_ten_bps_gap_exit_keeps_actual_fill_and_fee_arithmetic(
    tmp_path: Path,
    entry_side: OrderSide,
    exit_side: OrderSide,
    exit_open: int,
    entry_fill: Decimal,
    exit_fill: Decimal,
    fees: Decimal,
    net: Decimal,
) -> None:
    # Synthetic stop basis is 95 for LONG / 105 for SHORT: nominal stop
    # distance is 5, but the gap-open market exit can realize a larger loss.
    store = ParquetHistoricalCandleStore(tmp_path)
    candles = (candle(0, 100, 100), candle(1, exit_open, exit_open))
    store.merge(SOURCE, candles)
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal(2), slippage_bps=Decimal(2), fee_bps=Decimal(10)
    )
    requests = (
        MarketOrderRequest("entry", START, SYMBOL, entry_side, Decimal(1)),
        MarketOrderRequest("exit", candles[1].open_time, SYMBOL, exit_side, Decimal(1)),
    )
    execution = BacktestExecutionSimulator(HistoricalExecutionSource(store)).run(
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        costs,
        requests,
        start=START,
        end=candles[-1].close_time,
    )
    accounting = PositionAccountingEngine()
    for fill in execution.fills:
        accounting.process(AccountingFillBatch(fill.fill_at, (fill,)))
    final = accounting.snapshot(SYMBOL, candles[-1].close_time)

    assert tuple(fill.fill_price for fill in execution.fills) == (entry_fill, exit_fill)
    assert final.cumulative_gross_realized_pnl == net + fees
    assert final.cumulative_fees == fees
    assert final.cumulative_net_realized_pnl == net
    assert net < Decimal(-5)


def test_parquet_to_performance_handles_partial_crossing_wins_losses_and_fees(
    tmp_path: Path,
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candles = tuple(
        candle(index, price, price) for index, price in enumerate((100, 120, 80, 90, 70, 100))
    )
    store.merge(SOURCE, candles)
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal(0),
        slippage_bps=Decimal(0),
        fee_bps=Decimal(10),
    )
    requests = (
        MarketOrderRequest("open-long", START, SYMBOL, OrderSide.BUY, Decimal(2)),
        MarketOrderRequest(
            "partial-long",
            START + timedelta(seconds=1),
            SYMBOL,
            OrderSide.SELL,
            Decimal(1),
        ),
        MarketOrderRequest(
            "cross-short",
            START + timedelta(minutes=15, seconds=1),
            SYMBOL,
            OrderSide.SELL,
            Decimal(2),
        ),
        MarketOrderRequest(
            "close-short",
            START + timedelta(minutes=30, seconds=1),
            SYMBOL,
            OrderSide.BUY,
            Decimal(1),
        ),
        MarketOrderRequest(
            "open-winner",
            START + timedelta(minutes=45, seconds=1),
            SYMBOL,
            OrderSide.BUY,
            Decimal(1),
        ),
        MarketOrderRequest(
            "close-winner",
            START + timedelta(minutes=60, seconds=1),
            SYMBOL,
            OrderSide.SELL,
            Decimal(1),
        ),
    )
    execution = BacktestExecutionSimulator(HistoricalExecutionSource(store)).run(
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        costs,
        requests,
        start=START,
        end=candles[-1].close_time,
    )
    ledger = PositionAccountingEngine()
    accounting_events: list[FillAccountingEvent] = []
    analyzer = TradePerformanceAnalyzer()
    for execution_fill in execution.fills:
        accounting = ledger.process(AccountingFillBatch(execution_fill.fill_at, (execution_fill,)))
        accounting_events.extend(accounting.events)
        analyzer.process(PerformanceEventBatch(accounting.observed_at, accounting.events))

    performance = analyzer.result()
    repeated = TradePerformanceAnalyzer()
    for event in accounting_events:
        repeated.process(PerformanceEventBatch(event.fill_at, (event,)))

    assert performance == repeated.result()
    assert performance.open_trade_count == 0
    assert performance.portfolio_metrics.completed_trade_count == 3
    assert performance.portfolio_metrics.win_count == 1
    assert performance.portfolio_metrics.loss_count == 2
    assert performance.completed_trades[0].contributing_order_ids == (
        "open-long",
        "partial-long",
        "cross-short",
    )
    assert performance.completed_trades[1].opening_order_id == "cross-short"
    assert sum((trade.allocated_fees for trade in performance.completed_trades), Decimal(0)) == sum(
        (item.fee_quote for item in execution.fills), Decimal(0)
    )
    final_ledger = ledger.result(execution.fills[-1].fill_at)
    assert performance.portfolio_metrics.net_profit == final_ledger.portfolio_net_realized_pnl
