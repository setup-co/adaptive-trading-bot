"""Environment-neutral deterministic simulated market-fill pricing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

_SIMULATION_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_BASIS_POINTS = Decimal("10000")


class InvalidSimulatedFillPriceError(ValueError):
    """Configured SELL costs would produce a non-positive fill price."""


class SimulatedExecutionSide(StrEnum):
    """Direction used by environment-neutral simulated fill pricing."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class SimulatedExecutionCostConfiguration:
    """Explicit deterministic basis-point costs and non-negative latency."""

    spread_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    fee_bps: Decimal = Decimal("0")
    latency: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate costs before each simulation to reject raw mutation."""

        for name, value in (
            ("spread_bps", self.spread_bps),
            ("slippage_bps", self.slippage_bps),
            ("fee_bps", self.fee_bps),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not isinstance(self.latency, timedelta):
            raise TypeError("latency must be a timedelta")
        if self.latency < timedelta(0):
            raise ValueError("latency must be non-negative")


@dataclass(frozen=True, slots=True)
class SimulatedFillQuote:
    """Immutable result of generic deterministic pricing and fee arithmetic."""

    side: SimulatedExecutionSide
    quantity: Decimal
    reference_price: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    fill_price: Decimal
    fee_bps: Decimal
    fee_quote: Decimal


def price_simulated_market_fill(
    *,
    side: SimulatedExecutionSide,
    quantity: Decimal,
    reference_price: Decimal,
    costs: SimulatedExecutionCostConfiguration,
) -> SimulatedFillQuote:
    """Price one full simulated market fill without external state or side effects."""

    if not isinstance(side, SimulatedExecutionSide):
        raise TypeError("side must be a SimulatedExecutionSide")
    if not isinstance(costs, SimulatedExecutionCostConfiguration):
        raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
    costs.validate()
    for name, value in (("quantity", quantity), ("reference_price", reference_price)):
        if not isinstance(value, Decimal):
            raise TypeError(f"{name} must be a Decimal")
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be finite and positive")

    with localcontext(_SIMULATION_DECIMAL_CONTEXT):
        half_spread = costs.spread_bps / Decimal(2) / _BASIS_POINTS
        slippage = costs.slippage_bps / _BASIS_POINTS
        adjustment = half_spread + slippage
        direction = Decimal(1) if side is SimulatedExecutionSide.BUY else Decimal(-1)
        fill_price = reference_price * (Decimal(1) + direction * adjustment)
        if fill_price <= 0:
            raise InvalidSimulatedFillPriceError(
                "configured execution costs produce non-positive price"
            )
        fee_quote = abs(quantity * fill_price) * costs.fee_bps / _BASIS_POINTS

    return SimulatedFillQuote(
        side=side,
        quantity=quantity,
        reference_price=reference_price,
        spread_bps=costs.spread_bps,
        slippage_bps=costs.slippage_bps,
        fill_price=fill_price,
        fee_bps=costs.fee_bps,
        fee_quote=fee_quote,
    )
