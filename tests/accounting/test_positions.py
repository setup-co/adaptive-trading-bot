import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext
from pathlib import Path

import pytest

import adaptive_trading_bot.accounting.positions as accounting_module
from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingCoreError,
    PositionAccountingFill,
    PositionAccountingFillBatch,
    PositionAccountingTransition,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionSide

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")


def fill(
    fill_id: str,
    *,
    side: SimulatedExecutionSide,
    quantity: Decimal,
    price: Decimal,
    minute: int = 0,
    fee: Decimal = Decimal(0),
    symbol: TradingSymbol = BTC,
) -> PositionAccountingFill:
    return PositionAccountingFill(
        fill_id,
        symbol,
        side,
        quantity,
        START + timedelta(minutes=minute),
        price,
        fee,
    )


def apply(core: PositionAccountingCore, item: PositionAccountingFill) -> None:
    core.process(PositionAccountingFillBatch(item.fill_at, (item,)))


def test_shared_core_preserves_average_cost_crossing_fees_and_net_pnl() -> None:
    core = PositionAccountingCore()
    stream = (
        fill(
            "open-long",
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal(2),
            price=Decimal(100),
            fee=Decimal(1),
        ),
        fill(
            "add-long",
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal(1),
            price=Decimal(130),
            minute=1,
            fee=Decimal("0.5"),
        ),
        fill(
            "reduce-long",
            side=SimulatedExecutionSide.SELL,
            quantity=Decimal(1),
            price=Decimal(140),
            minute=2,
            fee=Decimal("0.25"),
        ),
        fill(
            "cross-short",
            side=SimulatedExecutionSide.SELL,
            quantity=Decimal(3),
            price=Decimal(90),
            minute=3,
        ),
        fill(
            "close-short",
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal(1),
            price=Decimal(80),
            minute=4,
        ),
    )

    transitions: list[PositionAccountingTransition] = []
    for item in stream:
        result = core.process(PositionAccountingFillBatch(item.fill_at, (item,)))
        transitions.extend(result.transitions)

    assert transitions[1].position_quantity_after == 3
    assert transitions[1].average_entry_after == 110
    assert transitions[2].closed_quantity == 1
    assert transitions[2].gross_realized_pnl_delta == 30
    assert transitions[2].average_entry_after == 110
    assert transitions[3].position_quantity_after == -1
    assert transitions[3].average_entry_after == 90
    assert transitions[3].gross_realized_pnl_delta == -40
    assert transitions[4].gross_realized_pnl_delta == 10
    state = core.position(BTC)
    assert state.quantity == 0
    assert state.average_entry_price is None
    assert state.cumulative_gross_realized_pnl == 0
    assert state.cumulative_fees == Decimal("1.75")
    assert state.cumulative_net_realized_pnl == Decimal("-1.75")


def test_batch_is_canonical_and_invalid_fill_causes_zero_partial_mutation() -> None:
    core = PositionAccountingCore()
    btc = fill("z", side=SimulatedExecutionSide.BUY, quantity=Decimal(1), price=Decimal(100))
    eth = fill(
        "a",
        side=SimulatedExecutionSide.SELL,
        quantity=Decimal(2),
        price=Decimal(200),
        symbol=ETH,
    )
    invalid = replace(eth, fill_id="bad")
    object.__setattr__(invalid, "fee_quote", Decimal(-1))

    with pytest.raises(PositionAccountingCoreError, match="fee"):
        core.process(PositionAccountingFillBatch(START, (btc, invalid)))

    assert core.positions() == ()
    result = core.process(PositionAccountingFillBatch(START, (eth, btc)))
    assert tuple(item.fill.fill_id for item in result.transitions) == ("z", "a")


def test_duplicate_backdated_and_separate_same_time_batches_fail_closed() -> None:
    core = PositionAccountingCore()
    first = fill("one", side=SimulatedExecutionSide.BUY, quantity=Decimal(1), price=Decimal(100))
    apply(core, first)

    with pytest.raises(PositionAccountingCoreError, match="strictly"):
        apply(
            core,
            fill(
                "two",
                side=SimulatedExecutionSide.BUY,
                quantity=Decimal(1),
                price=Decimal(100),
            ),
        )
    with pytest.raises(PositionAccountingCoreError, match="duplicate"):
        apply(
            core,
            fill(
                "one",
                side=SimulatedExecutionSide.BUY,
                quantity=Decimal(1),
                price=Decimal(100),
                minute=1,
            ),
        )
    with pytest.raises(PositionAccountingCoreError, match="strictly"):
        apply(
            core,
            fill(
                "older",
                side=SimulatedExecutionSide.BUY,
                quantity=Decimal(1),
                price=Decimal(100),
                minute=-1,
            ),
        )

    assert core.position(BTC).quantity == 1
    assert len(core.transitions) == 1


def test_shared_arithmetic_is_independent_of_ambient_decimal_context() -> None:
    stream = (
        fill(
            "one",
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal("1.111111111111111111"),
            price=Decimal("100.333333333333333333"),
        ),
        fill(
            "two",
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal("2.222222222222222222"),
            price=Decimal("120.777777777777777777"),
            minute=1,
        ),
    )

    def run() -> object:
        core = PositionAccountingCore()
        for item in stream:
            apply(core, item)
        return core.position(BTC)

    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = run()
        setcontext(Context(prec=28))
        regular_precision = run()
    finally:
        setcontext(original)

    assert low_precision == regular_precision


def test_committed_state_is_detached_from_raw_mutation_of_inputs_and_outputs() -> None:
    core = PositionAccountingCore()
    item = fill("owned", side=SimulatedExecutionSide.BUY, quantity=Decimal(1), price=Decimal(100))
    result = core.process(PositionAccountingFillBatch(START, (item,)))

    object.__setattr__(item, "quantity", Decimal(999))
    object.__setattr__(result.transitions[0].fill, "quantity", Decimal(888))
    exposed_state = core.position(BTC)
    object.__setattr__(exposed_state, "quantity", Decimal(777))

    assert core.position(BTC).quantity == 1
    assert core.transitions[0].fill.quantity == 1


def test_shared_accounting_has_no_environment_or_side_effect_dependencies() -> None:
    assert accounting_module.__file__ is not None
    tree = ast.parse(Path(accounting_module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "adaptive_trading_bot.backtest",
        "adaptive_trading_bot.paper",
        "adaptive_trading_bot.exchange",
        "adaptive_trading_bot.strategies",
        "ccxt",
        "sqlalchemy",
        "database",
        "network",
        "ui",
    )
    assert not any(name.startswith(forbidden) for name in imported)
    for method in ("submit", "send", "size", "observe", "release", "cash", "persist"):
        assert not hasattr(PositionAccountingCore(), method)
