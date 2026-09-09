from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext

import pytest

from adaptive_trading_bot.backtest import (
    AccountingBatchResult,
    AccountingFillBatch,
    AccountingLedgerResult,
    FillBatchError,
    MarkError,
    MarketFill,
    OrderSide,
    PositionAccountingEngine,
    PositionMark,
    PositionSide,
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
    quantity: Decimal,
    price: Decimal,
    fee: Decimal = Decimal(0),
    minute: int = 0,
    symbol: TradingSymbol = BTC,
) -> MarketFill:
    fill_at = START + timedelta(minutes=minute)
    return MarketFill(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        created_at=fill_at,
        eligible_at=fill_at,
        execution_candle_open_time=fill_at,
        fill_at=fill_at,
        reference_open_price=price,
        spread_bps=Decimal(0),
        slippage_bps=Decimal(0),
        fill_price=price,
        fee_bps=Decimal(0),
        fee_quote=fee,
        source_id=SOURCE,
        execution_timeframe=Timeframe.MINUTES_15,
    )


def apply(engine: PositionAccountingEngine, item: MarketFill) -> AccountingBatchResult:
    return engine.process(AccountingFillBatch(item.fill_at, (item,)))


def test_open_add_reduce_and_close_long_uses_average_cost() -> None:
    engine = PositionAccountingEngine()
    opened = apply(
        engine,
        fill("open", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100), fee=Decimal(1)),
    ).events[0]
    added = apply(
        engine,
        fill(
            "add",
            side=OrderSide.BUY,
            quantity=Decimal(1),
            price=Decimal(120),
            fee=Decimal(2),
            minute=1,
        ),
    ).events[0]
    reduced = apply(
        engine,
        fill(
            "reduce",
            side=OrderSide.SELL,
            quantity=Decimal("0.5"),
            price=Decimal(130),
            fee=Decimal("0.5"),
            minute=2,
        ),
    ).events[0]
    closed = apply(
        engine,
        fill(
            "close",
            side=OrderSide.SELL,
            quantity=Decimal("1.5"),
            price=Decimal(90),
            minute=3,
        ),
    ).events[0]

    assert opened.position_quantity_before == 0
    assert opened.position_quantity_after == 1
    assert opened.average_entry_after == 100
    assert opened.gross_realized_pnl_delta == 0
    assert opened.cumulative_net_realized_pnl == -1
    assert added.position_quantity_after == 2
    assert added.average_entry_after == 110
    assert added.cumulative_fees == 3
    assert reduced.closed_quantity == Decimal("0.5")
    assert reduced.gross_realized_pnl_delta == 10
    assert reduced.position_quantity_after == Decimal("1.5")
    assert reduced.average_entry_after == 110
    assert reduced.cumulative_net_realized_pnl == Decimal("6.5")
    assert closed.gross_realized_pnl_delta == -30
    assert closed.cumulative_gross_realized_pnl == -20
    assert closed.position_quantity_after == 0
    assert closed.average_entry_after is None

    snapshot = engine.snapshot(BTC, closed.fill_at)
    assert snapshot.side is PositionSide.FLAT
    assert snapshot.average_entry_price is None
    assert snapshot.unrealized_gross_pnl == 0
    assert snapshot.total_net_pnl == Decimal("-23.5")


def test_open_add_reduce_and_close_short_uses_average_cost() -> None:
    engine = PositionAccountingEngine()
    apply(engine, fill("open", side=OrderSide.SELL, quantity=Decimal(1), price=Decimal(120)))
    added = apply(
        engine,
        fill(
            "add",
            side=OrderSide.SELL,
            quantity=Decimal(1),
            price=Decimal(100),
            minute=1,
        ),
    ).events[0]
    reduced = apply(
        engine,
        fill(
            "reduce",
            side=OrderSide.BUY,
            quantity=Decimal("0.5"),
            price=Decimal(90),
            minute=2,
        ),
    ).events[0]
    closed = apply(
        engine,
        fill(
            "close",
            side=OrderSide.BUY,
            quantity=Decimal("1.5"),
            price=Decimal(130),
            minute=3,
        ),
    ).events[0]

    assert added.position_quantity_after == -2
    assert added.average_entry_after == 110
    assert reduced.closed_quantity == Decimal("0.5")
    assert reduced.gross_realized_pnl_delta == 10
    assert reduced.position_quantity_after == Decimal("-1.5")
    assert reduced.average_entry_after == 110
    assert closed.gross_realized_pnl_delta == -30
    assert closed.cumulative_gross_realized_pnl == -20
    assert closed.position_quantity_after == 0
    assert closed.average_entry_after is None


def test_long_to_short_crossing_closes_then_opens_remainder_at_fill_price() -> None:
    engine = PositionAccountingEngine()
    apply(engine, fill("long", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100)))

    crossing = apply(
        engine,
        fill(
            "cross",
            side=OrderSide.SELL,
            quantity=Decimal("1.5"),
            price=Decimal(120),
            minute=1,
        ),
    ).events[0]

    assert crossing.closed_quantity == 1
    assert crossing.gross_realized_pnl_delta == 20
    assert crossing.position_quantity_after == Decimal("-0.5")
    assert crossing.average_entry_after == 120
    assert engine.snapshot(BTC, crossing.fill_at).side is PositionSide.SHORT


def test_short_to_long_crossing_closes_then_opens_remainder_at_fill_price() -> None:
    engine = PositionAccountingEngine()
    apply(engine, fill("short", side=OrderSide.SELL, quantity=Decimal(1), price=Decimal(120)))

    crossing = apply(
        engine,
        fill(
            "cross",
            side=OrderSide.BUY,
            quantity=Decimal("1.5"),
            price=Decimal(100),
            minute=1,
        ),
    ).events[0]

    assert crossing.closed_quantity == 1
    assert crossing.gross_realized_pnl_delta == 20
    assert crossing.position_quantity_after == Decimal("0.5")
    assert crossing.average_entry_after == 100
    assert engine.snapshot(BTC, crossing.fill_at).side is PositionSide.LONG


def test_long_and_short_marks_are_causal_and_do_not_mutate_state() -> None:
    long_engine = PositionAccountingEngine()
    long_fill = fill(
        "long", side=OrderSide.BUY, quantity=Decimal(2), price=Decimal(100), fee=Decimal(1)
    )
    apply(long_engine, long_fill)
    mark = PositionMark(BTC, START + timedelta(minutes=1), Decimal(110))
    marked = long_engine.snapshot(BTC, mark.observed_at, mark=mark)
    unmarked_again = long_engine.snapshot(BTC, mark.observed_at)

    assert marked.mark_price == 110
    assert marked.unrealized_gross_pnl == 20
    assert marked.total_net_pnl == 19
    assert unmarked_again.average_entry_price == 100
    assert unmarked_again.cumulative_gross_realized_pnl == 0
    assert unmarked_again.unrealized_gross_pnl is None

    short_engine = PositionAccountingEngine()
    apply(
        short_engine,
        fill("short", side=OrderSide.SELL, quantity=Decimal(2), price=Decimal(120)),
    )
    short_mark = PositionMark(BTC, START + timedelta(minutes=1), Decimal(100))
    assert (
        short_engine.snapshot(BTC, short_mark.observed_at, mark=short_mark).unrealized_gross_pnl
        == 40
    )


@pytest.mark.parametrize("price", [Decimal(0), Decimal(-1), Decimal("NaN")])
def test_mark_rejects_invalid_price(price: Decimal) -> None:
    with pytest.raises(ValueError, match="mark price"):
        PositionMark(BTC, START, price)


def test_future_or_wrong_symbol_mark_and_backdated_snapshot_fail() -> None:
    engine = PositionAccountingEngine()
    applied = fill("open", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100), minute=1)
    apply(engine, applied)
    with pytest.raises(MarkError, match="earlier"):
        engine.snapshot(BTC, START)
    with pytest.raises(MarkError, match="before"):
        engine.snapshot(
            BTC,
            applied.fill_at,
            mark=PositionMark(BTC, applied.fill_at + timedelta(seconds=1), Decimal(100)),
        )
    with pytest.raises(MarkError, match="symbol"):
        engine.snapshot(
            BTC,
            applied.fill_at,
            mark=PositionMark(ETH, applied.fill_at, Decimal(100)),
        )


def test_duplicate_identity_and_backward_batch_fail_closed() -> None:
    engine = PositionAccountingEngine()
    first = fill("same", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100), minute=1)
    apply(engine, first)
    with pytest.raises(FillBatchError, match="duplicate"):
        apply(
            engine,
            fill("same", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100), minute=2),
        )
    with pytest.raises(FillBatchError, match="strictly"):
        apply(
            engine,
            fill("older", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100)),
        )

    snapshot = engine.snapshot(BTC, first.fill_at)
    assert snapshot.quantity == 1
    assert snapshot.cumulative_fees == 0


def test_invalid_same_time_batch_has_zero_partial_mutation() -> None:
    engine = PositionAccountingEngine()
    valid = fill("valid", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100))
    invalid = replace(
        fill(
            "invalid",
            side=OrderSide.BUY,
            quantity=Decimal(1),
            price=Decimal(200),
            symbol=ETH,
        ),
        fee_quote=Decimal(-1),
    )

    with pytest.raises(FillBatchError, match="fee"):
        engine.process(AccountingFillBatch(START, (valid, invalid)))

    event = apply(engine, valid).events[0]
    assert event.position_quantity_before == 0
    assert engine.snapshot(ETH, START).quantity == 0


def test_same_time_multi_symbol_output_is_canonical_and_state_isolated() -> None:
    engine = PositionAccountingEngine()
    result = engine.process(
        AccountingFillBatch(
            START,
            (
                fill(
                    "z",
                    side=OrderSide.SELL,
                    quantity=Decimal(2),
                    price=Decimal(200),
                    symbol=ETH,
                ),
                fill("b", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(110)),
                fill("a", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(90)),
            ),
        )
    )

    assert tuple((event.symbol, event.order_id) for event in result.events) == (
        (BTC, "a"),
        (BTC, "b"),
        (ETH, "z"),
    )
    btc, eth = result.positions
    assert (btc.symbol, btc.quantity, btc.average_entry_price) == (BTC, Decimal(2), Decimal(100))
    assert (eth.symbol, eth.quantity, eth.average_entry_price) == (ETH, Decimal(-2), Decimal(200))


def test_ledger_result_has_ordered_events_latest_positions_and_portfolio_totals() -> None:
    engine = PositionAccountingEngine()
    engine.process(
        AccountingFillBatch(
            START,
            (
                fill(
                    "btc",
                    side=OrderSide.BUY,
                    quantity=Decimal(1),
                    price=Decimal(100),
                    fee=Decimal(1),
                ),
                fill(
                    "eth",
                    side=OrderSide.SELL,
                    quantity=Decimal(1),
                    price=Decimal(200),
                    fee=Decimal(2),
                    symbol=ETH,
                ),
            ),
        )
    )
    ledger = engine.result(
        START,
        marks=(PositionMark(BTC, START, Decimal(110)), PositionMark(ETH, START, Decimal(190))),
    )

    assert isinstance(ledger, AccountingLedgerResult)
    assert tuple(event.order_id for event in ledger.events) == ("btc", "eth")
    assert tuple(position.symbol for position in ledger.latest_positions) == (BTC, ETH)
    assert tuple(position.unrealized_gross_pnl for position in ledger.latest_positions) == (
        Decimal(10),
        Decimal(10),
    )
    assert ledger.portfolio_gross_realized_pnl == 0
    assert ledger.portfolio_fees == 3
    assert ledger.portfolio_net_realized_pnl == -3


def test_repeated_stream_is_deterministic_and_ambient_context_independent() -> None:
    stream = (
        fill(
            "one",
            side=OrderSide.BUY,
            quantity=Decimal("1.111111111111111111"),
            price=Decimal("100.333333333333333333"),
            fee=Decimal("0.123456789012345678"),
        ),
        fill(
            "two",
            side=OrderSide.BUY,
            quantity=Decimal("2.222222222222222222"),
            price=Decimal("120.777777777777777777"),
            fee=Decimal("0.234567890123456789"),
            minute=1,
        ),
    )

    def run() -> AccountingLedgerResult:
        engine = PositionAccountingEngine()
        for item in stream:
            apply(engine, item)
        return engine.result(stream[-1].fill_at)

    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = run()
        setcontext(Context(prec=28))
        regular_precision = run()
    finally:
        setcontext(original)

    assert low_precision == regular_precision


def test_fill_validation_rejects_invalid_timestamp_and_decimal() -> None:
    engine = PositionAccountingEngine()
    valid = fill("bad", side=OrderSide.BUY, quantity=Decimal(1), price=Decimal(100))
    with pytest.raises(ValueError, match="UTC"):
        engine.process(
            AccountingFillBatch(
                START,
                (replace(valid, fill_at=datetime(2026, 9, 1, 12, 0)),),
            )
        )
    with pytest.raises(FillBatchError, match="finite Decimal"):
        engine.process(AccountingFillBatch(START, (replace(valid, fill_price=Decimal("NaN")),)))
