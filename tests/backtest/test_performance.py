from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext

import pytest

from adaptive_trading_bot.backtest import (
    AccountingFillBatch,
    CompletedTrade,
    MarketFill,
    OrderSide,
    PerformanceBatchError,
    PerformanceEventBatch,
    PositionAccountingEngine,
    PositionSide,
    ProfitFactorUndefinedReason,
    TradeOutcome,
    TradePerformanceAnalyzer,
    calculate_trade_metrics,
)
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


def fill(
    order_id: str,
    *,
    side: OrderSide,
    quantity: str,
    price: str,
    fee: str = "0",
    minute: int,
    symbol: TradingSymbol = BTC,
) -> MarketFill:
    fill_at = START + timedelta(minutes=minute)
    return MarketFill(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        created_at=fill_at,
        eligible_at=fill_at,
        execution_candle_open_time=fill_at,
        fill_at=fill_at,
        reference_open_price=Decimal(price),
        spread_bps=Decimal(0),
        slippage_bps=Decimal(0),
        fill_price=Decimal(price),
        fee_bps=Decimal(0),
        fee_quote=Decimal(fee),
        source_id=SOURCE,
        execution_timeframe=Timeframe.MINUTES_15,
    )


def account(
    ledger: PositionAccountingEngine,
    item: MarketFill,
) -> PerformanceEventBatch:
    result = ledger.process(AccountingFillBatch(item.fill_at, (item,)))
    return PerformanceEventBatch(result.observed_at, result.events)


def completed_trade(
    trade_id: str,
    *,
    minute: int,
    net: str,
    duration_minutes: int = 10,
    symbol: TradingSymbol = BTC,
) -> CompletedTrade:
    closed_at = START + timedelta(minutes=minute)
    net_pnl = Decimal(net)
    outcome = (
        TradeOutcome.WIN
        if net_pnl > 0
        else TradeOutcome.LOSS
        if net_pnl < 0
        else TradeOutcome.BREAKEVEN
    )
    return CompletedTrade(
        trade_id=trade_id,
        symbol=symbol,
        side=PositionSide.LONG,
        opened_at=closed_at - timedelta(minutes=duration_minutes),
        closed_at=closed_at,
        opening_order_id=f"{trade_id}-open",
        closing_order_id=f"{trade_id}-close",
        entry_basis_price=Decimal(100),
        total_opened_quantity=Decimal(1),
        total_closed_quantity=Decimal(1),
        maximum_position_quantity=Decimal(1),
        gross_realized_pnl=net_pnl,
        allocated_fees=Decimal(0),
        net_pnl=net_pnl,
        outcome=outcome,
        duration=timedelta(minutes=duration_minutes),
        contributing_order_ids=(f"{trade_id}-open", f"{trade_id}-close"),
    )


def test_reconstructs_addition_partial_reduction_and_cross_with_exact_fee_split() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    items = (
        fill("open", side=OrderSide.BUY, quantity="1", price="100", fee="1", minute=0),
        fill("add", side=OrderSide.BUY, quantity="1", price="120", fee="2", minute=1),
        fill("reduce", side=OrderSide.SELL, quantity="0.5", price="130", fee="0.5", minute=2),
        fill("cross", side=OrderSide.SELL, quantity="2", price="90", fee="4", minute=3),
        fill("close-short", side=OrderSide.BUY, quantity="0.5", price="80", fee="0.8", minute=4),
    )

    for item in items:
        analyzer.process(account(ledger, item))
    result = analyzer.result()

    assert [trade.trade_id for trade in result.completed_trades] == [
        "trade-00000001",
        "trade-00000002",
    ]
    long_trade, short_trade = result.completed_trades
    assert long_trade.side is PositionSide.LONG
    assert long_trade.entry_basis_price == Decimal(110)
    assert long_trade.total_opened_quantity == Decimal(2)
    assert long_trade.total_closed_quantity == Decimal("2.0")
    assert long_trade.maximum_position_quantity == Decimal(2)
    assert long_trade.gross_realized_pnl == Decimal(-20)
    assert long_trade.allocated_fees == Decimal("6.5")
    assert long_trade.net_pnl == Decimal("-26.5")
    assert long_trade.outcome is TradeOutcome.LOSS
    assert long_trade.contributing_order_ids == ("open", "add", "reduce", "cross")
    assert short_trade.side is PositionSide.SHORT
    assert short_trade.opening_order_id == "cross"
    assert short_trade.closing_order_id == "close-short"
    assert short_trade.total_opened_quantity == Decimal("0.5")
    assert short_trade.gross_realized_pnl == Decimal(5)
    assert short_trade.allocated_fees == Decimal("1.8")
    assert short_trade.net_pnl == Decimal("3.2")
    assert sum((trade.allocated_fees for trade in result.completed_trades), Decimal(0)) == Decimal(
        "8.3"
    )
    assert result.open_trade_count == 0
    assert result.portfolio_metrics.net_profit == Decimal("-23.3")


def test_open_trade_is_reported_and_excluded_from_completed_metrics() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    analyzer.process(
        account(
            ledger,
            fill("open", side=OrderSide.BUY, quantity="2", price="100", fee="1", minute=0),
        )
    )
    analyzer.process(
        account(
            ledger,
            fill("reduce", side=OrderSide.SELL, quantity="1", price="120", fee="1", minute=1),
        )
    )

    result = analyzer.result()

    assert result.completed_trades == ()
    assert result.open_trade_count == 1
    assert result.open_trades[0].current_quantity == Decimal(1)
    assert result.open_trades[0].gross_realized_pnl == Decimal(20)
    assert result.open_trades[0].allocated_fees == Decimal(2)
    assert result.portfolio_metrics.completed_trade_count == 0
    assert result.portfolio_metrics.net_profit == 0
    assert (
        result.portfolio_metrics.profit_factor_undefined_reason
        is ProfitFactorUndefinedReason.NO_COMPLETED_TRADES
    )


@pytest.mark.parametrize(
    ("opening_side", "closing_side", "open_price", "close_price", "expected_side"),
    [
        (OrderSide.BUY, OrderSide.SELL, "100", "110", PositionSide.LONG),
        (OrderSide.SELL, OrderSide.BUY, "100", "90", PositionSide.SHORT),
    ],
)
def test_simple_long_and_short_flat_closes_each_complete_one_trade(
    opening_side: OrderSide,
    closing_side: OrderSide,
    open_price: str,
    close_price: str,
    expected_side: PositionSide,
) -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    analyzer.process(
        account(
            ledger,
            fill("open", side=opening_side, quantity="1", price=open_price, minute=0),
        )
    )
    batch = analyzer.process(
        account(
            ledger,
            fill("close", side=closing_side, quantity="1", price=close_price, minute=1),
        )
    )

    assert len(batch.completed_trades) == 1
    assert batch.completed_trades[0].side is expected_side
    assert batch.completed_trades[0].gross_realized_pnl == Decimal(10)
    assert batch.open_trades == ()


def test_short_to_long_cross_starts_opposite_trade_at_same_timestamp() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    analyzer.process(
        account(
            ledger,
            fill("short", side=OrderSide.SELL, quantity="1", price="100", fee="1", minute=0),
        )
    )
    crossing = analyzer.process(
        account(
            ledger,
            fill("cross", side=OrderSide.BUY, quantity="3", price="90", fee="3", minute=1),
        )
    )

    assert crossing.completed_trades[0].side is PositionSide.SHORT
    assert crossing.completed_trades[0].allocated_fees == Decimal(2)
    assert crossing.open_trades[0].side is PositionSide.LONG
    assert crossing.open_trades[0].opened_at == crossing.completed_trades[0].closed_at
    assert crossing.open_trades[0].current_quantity == Decimal(2)
    assert crossing.open_trades[0].allocated_fees == Decimal(2)


def test_classification_uses_net_pnl_after_opening_fee() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    analyzer.process(
        account(
            ledger,
            fill("open", side=OrderSide.BUY, quantity="1", price="100", fee="1", minute=0),
        )
    )
    analyzer.process(
        account(
            ledger,
            fill("close", side=OrderSide.SELL, quantity="1", price="101", minute=1),
        )
    )

    trade = analyzer.result().completed_trades[0]
    assert trade.gross_realized_pnl == Decimal(1)
    assert trade.net_pnl == 0
    assert trade.outcome is TradeOutcome.BREAKEVEN


def test_multi_symbol_state_and_metrics_are_independent() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    sequence = (
        fill("btc-open", side=OrderSide.BUY, quantity="1", price="100", minute=0),
        fill(
            "eth-open",
            side=OrderSide.SELL,
            quantity="1",
            price="200",
            minute=1,
            symbol=ETH,
        ),
        fill("btc-close", side=OrderSide.SELL, quantity="1", price="110", minute=2),
    )
    for item in sequence:
        analyzer.process(account(ledger, item))

    result = analyzer.result()

    assert [trade.symbol for trade in result.completed_trades] == [BTC]
    assert [trade.symbol for trade in result.open_trades] == [ETH]
    assert [item.symbol for item in result.per_symbol_metrics] == [BTC, ETH]
    assert result.per_symbol_metrics[0].metrics.completed_trade_count == 1
    assert result.per_symbol_metrics[1].metrics.completed_trade_count == 0


def test_metrics_include_breakeven_streak_durations_and_recovered_drawdown() -> None:
    trades = (
        completed_trade("four", minute=40, net="15", duration_minutes=40),
        completed_trade("two", minute=20, net="-4", duration_minutes=20),
        completed_trade("zero", minute=50, net="0", duration_minutes=50),
        completed_trade("one", minute=10, net="10", duration_minutes=10),
        completed_trade("three", minute=30, net="-8", duration_minutes=30),
    )

    metrics = calculate_trade_metrics(trades)

    assert metrics.completed_trade_count == 5
    assert (metrics.win_count, metrics.loss_count, metrics.breakeven_count) == (2, 2, 1)
    assert metrics.win_rate == Decimal("0.4")
    assert metrics.gross_profit == Decimal(25)
    assert metrics.gross_loss == Decimal(12)
    assert metrics.profit_factor == Decimal("2.083333333333333333333333333333333")
    assert metrics.net_profit == Decimal(13)
    assert metrics.average_trade_net_pnl == Decimal("2.6")
    assert metrics.median_trade_net_pnl == 0
    assert metrics.expectancy == Decimal("2.6")
    assert metrics.average_win == Decimal("12.5")
    assert metrics.average_loss == Decimal(6)
    assert metrics.largest_win == Decimal(15)
    assert metrics.largest_loss == Decimal(8)
    assert metrics.maximum_consecutive_losses == 2
    assert metrics.average_trade_duration == timedelta(minutes=30)
    assert metrics.median_trade_duration == timedelta(minutes=30)
    assert [point.trade_id for point in metrics.pnl_path] == ["one", "two", "three", "four", "zero"]
    assert [point.cumulative_net_pnl for point in metrics.pnl_path] == [10, 6, -2, 13, 13]
    assert metrics.maximum_absolute_drawdown == Decimal(12)
    assert metrics.drawdown_peak_at == START + timedelta(minutes=10)
    assert metrics.drawdown_trough_at == START + timedelta(minutes=30)
    assert metrics.drawdown_recovered_at == START + timedelta(minutes=40)


def test_zero_anchor_drawdown_can_have_no_trade_peak_or_recovery() -> None:
    metrics = calculate_trade_metrics(
        (
            completed_trade("loss", minute=10, net="-3"),
            completed_trade("partial", minute=20, net="2"),
        )
    )

    assert metrics.maximum_absolute_drawdown == Decimal(3)
    assert metrics.drawdown_peak_at is None
    assert metrics.drawdown_trough_at == START + timedelta(minutes=10)
    assert metrics.drawdown_recovered_at is None


def test_no_losses_has_explicit_undefined_profit_factor() -> None:
    metrics = calculate_trade_metrics((completed_trade("win", minute=10, net="2"),))

    assert metrics.profit_factor is None
    assert metrics.profit_factor_undefined_reason is ProfitFactorUndefinedReason.NO_GROSS_LOSS


def test_same_close_time_uses_symbol_then_trade_id_canonical_order() -> None:
    metrics = calculate_trade_metrics(
        (
            completed_trade("z", minute=10, net="1", symbol=ETH),
            completed_trade("b", minute=10, net="1"),
            completed_trade("a", minute=10, net="1"),
        )
    )

    assert [point.trade_id for point in metrics.pnl_path] == ["a", "b", "z"]


def test_rejects_invalid_transition_atomically() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    open_batch = account(
        ledger,
        fill("open", side=OrderSide.BUY, quantity="1", price="100", minute=0),
    )
    analyzer.process(open_batch)
    close_batch = account(
        ledger,
        fill("close", side=OrderSide.SELL, quantity="1", price="110", minute=1),
    )
    invalid_event = replace(close_batch.events[0], position_quantity_after=Decimal(1))

    with pytest.raises(PerformanceBatchError, match="side or quantity"):
        analyzer.process(PerformanceEventBatch(close_batch.observed_at, (invalid_event,)))

    result = analyzer.result()
    assert result.completed_trades == ()
    assert result.open_trades[0].current_quantity == Decimal(1)


def test_rejects_closing_event_without_active_trade() -> None:
    ledger = PositionAccountingEngine()
    account(
        ledger,
        fill("open", side=OrderSide.BUY, quantity="1", price="100", minute=0),
    )
    close_batch = account(
        ledger,
        fill("close", side=OrderSide.SELL, quantity="1", price="110", minute=1),
    )

    with pytest.raises(PerformanceBatchError, match="without an active trade"):
        TradePerformanceAnalyzer().process(close_batch)


def test_rejects_duplicate_event_and_backward_batch_time() -> None:
    ledger = PositionAccountingEngine()
    analyzer = TradePerformanceAnalyzer()
    first = account(
        ledger,
        fill("open", side=OrderSide.BUY, quantity="1", price="100", minute=1),
    )
    analyzer.process(first)

    with pytest.raises(PerformanceBatchError, match="strictly in time"):
        analyzer.process(first)

    duplicate_later = replace(first.events[0], fill_at=START + timedelta(minutes=2))
    with pytest.raises(PerformanceBatchError, match="duplicate"):
        analyzer.process(PerformanceEventBatch(duplicate_later.fill_at, (duplicate_later,)))


def test_repeated_analysis_is_identical_and_ignores_ambient_decimal_context() -> None:
    ledger = PositionAccountingEngine()
    batches = (
        account(
            ledger,
            fill("open", side=OrderSide.BUY, quantity="3", price="100", fee="1", minute=0),
        ),
        account(
            ledger,
            fill("cross", side=OrderSide.SELL, quantity="4", price="110", fee="1", minute=1),
        ),
        account(
            ledger,
            fill("close", side=OrderSide.BUY, quantity="1", price="90", fee="1", minute=2),
        ),
    )

    original_context = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        first = TradePerformanceAnalyzer()
        for batch in batches:
            first.process(batch)
        low_precision_result = first.result()

        setcontext(Context(prec=50))
        second = TradePerformanceAnalyzer()
        for batch in batches:
            second.process(batch)
        high_precision_result = second.result()
    finally:
        setcontext(original_context)

    assert low_precision_result == high_precision_result
