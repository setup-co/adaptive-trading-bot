from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.backtest import (
    AccountingFillBatch,
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
    MarketOrderRequest,
    OrderSide,
    PositionAccountingEngine,
    PositionMark,
    PositionSide,
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
