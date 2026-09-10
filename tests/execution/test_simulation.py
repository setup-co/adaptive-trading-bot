from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Context, Decimal, getcontext, setcontext

import pytest

from adaptive_trading_bot.execution import (
    InvalidSimulatedFillPriceError,
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)


@pytest.mark.parametrize("field_name", ["spread_bps", "slippage_bps", "fee_bps"])
@pytest.mark.parametrize("value", [Decimal(-1), Decimal("NaN"), Decimal("Infinity")])
def test_costs_reject_negative_or_nonfinite_decimals(field_name: str, value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        SimulatedExecutionCostConfiguration(**{field_name: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("field_name", ["spread_bps", "slippage_bps", "fee_bps"])
def test_costs_reject_float_inputs(field_name: str) -> None:
    with pytest.raises(TypeError, match="must be a Decimal"):
        SimulatedExecutionCostConfiguration(**{field_name: 1.0})  # type: ignore[arg-type]


def test_costs_reject_invalid_latency() -> None:
    with pytest.raises(TypeError, match="timedelta"):
        SimulatedExecutionCostConfiguration(latency=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-negative"):
        SimulatedExecutionCostConfiguration(latency=-timedelta(microseconds=1))


def test_cost_configuration_and_quote_are_immutable_and_exact() -> None:
    costs = SimulatedExecutionCostConfiguration(
        spread_bps=Decimal("1.25"),
        slippage_bps=Decimal("2.50"),
        fee_bps=Decimal("3.75"),
        latency=timedelta(microseconds=7),
    )
    quote = price_simulated_market_fill(
        side=SimulatedExecutionSide.BUY,
        quantity=Decimal(1),
        reference_price=Decimal(100),
        costs=costs,
    )

    assert (costs.spread_bps, costs.slippage_bps, costs.fee_bps, costs.latency) == (
        Decimal("1.25"),
        Decimal("2.50"),
        Decimal("3.75"),
        timedelta(microseconds=7),
    )
    with pytest.raises(FrozenInstanceError):
        costs.fee_bps = Decimal(0)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        quote.fill_price = Decimal(0)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("side", "expected_price", "expected_fee"),
    [
        (SimulatedExecutionSide.BUY, Decimal("100.25"), Decimal("0.401")),
        (SimulatedExecutionSide.SELL, Decimal("99.75"), Decimal("0.399")),
    ],
)
def test_shared_buy_sell_formula_boundaries(
    side: SimulatedExecutionSide, expected_price: Decimal, expected_fee: Decimal
) -> None:
    quote = price_simulated_market_fill(
        side=side,
        quantity=Decimal(2),
        reference_price=Decimal(100),
        costs=SimulatedExecutionCostConfiguration(
            spread_bps=Decimal(10),
            slippage_bps=Decimal(20),
            fee_bps=Decimal(20),
        ),
    )

    assert quote.fill_price == expected_price
    assert quote.fee_quote == expected_fee


@pytest.mark.parametrize("field_name", ["quantity", "reference_price"])
@pytest.mark.parametrize("value", [0.0, Decimal(0), Decimal(-1), Decimal("NaN")])
def test_pricing_rejects_invalid_decimal_inputs(field_name: str, value: object) -> None:
    arguments: dict[str, object] = {
        "side": SimulatedExecutionSide.BUY,
        "quantity": Decimal(1),
        "reference_price": Decimal(100),
        "costs": SimulatedExecutionCostConfiguration(),
    }
    arguments[field_name] = value

    with pytest.raises((TypeError, ValueError)):
        price_simulated_market_fill(**arguments)  # type: ignore[arg-type]


def test_sell_nonpositive_fill_price_fails_closed() -> None:
    with pytest.raises(InvalidSimulatedFillPriceError, match="non-positive"):
        price_simulated_market_fill(
            side=SimulatedExecutionSide.SELL,
            quantity=Decimal(1),
            reference_price=Decimal(100),
            costs=SimulatedExecutionCostConfiguration(slippage_bps=Decimal(10000)),
        )


def test_pricing_is_independent_of_ambient_decimal_context() -> None:
    original = getcontext().copy()
    costs = SimulatedExecutionCostConfiguration(
        spread_bps=Decimal("7.25"),
        slippage_bps=Decimal("3.125"),
        fee_bps=Decimal("8.75"),
    )
    try:
        setcontext(Context(prec=6))
        low_precision = price_simulated_market_fill(
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal("1.23456789"),
            reference_price=Decimal("98765.43210987654321"),
            costs=costs,
        )
        setcontext(Context(prec=50))
        high_precision = price_simulated_market_fill(
            side=SimulatedExecutionSide.BUY,
            quantity=Decimal("1.23456789"),
            reference_price=Decimal("98765.43210987654321"),
            costs=costs,
        )
    finally:
        setcontext(original)

    assert low_precision == high_precision
