"""Deterministic average-cost arithmetic shared by simulation environments.

The core owns fill identity, atomic same-time batches, chronology, and position state.
Environment adapters remain responsible only for validating their additional provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import SimulatedExecutionSide

_ACCOUNTING_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class PositionAccountingCoreError(ValueError):
    """A shared fill or batch violates accounting invariants."""


@dataclass(frozen=True, slots=True)
class PositionAccountingFill:
    """Minimum environment-neutral facts required for position accounting."""

    fill_id: str
    symbol: TradingSymbol
    side: SimulatedExecutionSide
    quantity: Decimal
    fill_at: datetime
    fill_price: Decimal
    fee_quote: Decimal

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate the fill so raw mutation cannot bypass the core boundary."""

        if (
            not isinstance(self.fill_id, str)
            or not self.fill_id.strip()
            or self.fill_id != self.fill_id.strip()
        ):
            raise PositionAccountingCoreError("fill_id must be a canonical non-empty string")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("fill symbol must be a TradingSymbol")
        if not isinstance(self.side, SimulatedExecutionSide):
            raise TypeError("fill side must be a SimulatedExecutionSide")
        for name, value in (
            ("quantity", self.quantity),
            ("fill_price", self.fill_price),
            ("fee_quote", self.fee_quote),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise PositionAccountingCoreError(f"fill {name} must be a finite Decimal")
        if self.quantity <= 0 or self.fill_price <= 0:
            raise PositionAccountingCoreError("fill quantity and price must be positive")
        if self.fee_quote < 0:
            raise PositionAccountingCoreError("fill fee must be non-negative")
        require_utc(self.fill_at, field="fill_at")


def _fill_order(fill: PositionAccountingFill) -> tuple[str, str]:
    return str(fill.symbol), fill.fill_id


@dataclass(frozen=True, slots=True)
class PositionAccountingFillBatch:
    """One non-empty immutable same-time batch in canonical order."""

    observed_at: datetime
    fills: tuple[PositionAccountingFill, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", require_utc(self.observed_at, field="batch observed_at")
        )
        if not isinstance(self.fills, tuple):
            raise TypeError("accounting fills must be an immutable tuple")
        if not self.fills:
            raise PositionAccountingCoreError("accounting fill batch must not be empty")
        object.__setattr__(self, "fills", tuple(sorted(self.fills, key=_fill_order)))


@dataclass(frozen=True, slots=True)
class PositionAccountingState:
    """Realized position state for one economic symbol."""

    quantity: Decimal = Decimal(0)
    average_entry_price: Decimal | None = None
    cumulative_gross_realized_pnl: Decimal = Decimal(0)
    cumulative_fees: Decimal = Decimal(0)

    @property
    def cumulative_net_realized_pnl(self) -> Decimal:
        """Return gross realized PnL less separately accumulated fees."""

        with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
            return self.cumulative_gross_realized_pnl - self.cumulative_fees


@dataclass(frozen=True, slots=True)
class PositionAccountingTransition:
    """One deterministic state transition caused by an accepted fill."""

    fill: PositionAccountingFill
    position_quantity_before: Decimal
    position_quantity_after: Decimal
    average_entry_before: Decimal | None
    average_entry_after: Decimal | None
    closed_quantity: Decimal
    gross_realized_pnl_delta: Decimal
    cumulative_gross_realized_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_net_realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class PositionAccountingTransitionBatch:
    """Atomic transitions and resulting states from a shared fill batch."""

    observed_at: datetime
    transitions: tuple[PositionAccountingTransition, ...]
    positions: tuple[tuple[TradingSymbol, PositionAccountingState], ...]


class PositionAccountingCore:
    """Apply validated fills atomically using one average-cost implementation."""

    __slots__ = ("_last_fill_at", "_seen_fill_ids", "_states", "_transitions")

    def __init__(self) -> None:
        self._states: dict[TradingSymbol, PositionAccountingState] = {}
        self._seen_fill_ids: set[str] = set()
        self._transitions: tuple[PositionAccountingTransition, ...] = ()
        self._last_fill_at: datetime | None = None

    @property
    def last_fill_at(self) -> datetime | None:
        """Return the timestamp of the latest committed batch."""

        return self._last_fill_at

    @property
    def transitions(self) -> tuple[PositionAccountingTransition, ...]:
        """Return every committed transition in deterministic order."""

        return tuple(_copy_transition(transition) for transition in self._transitions)

    def position(self, symbol: TradingSymbol) -> PositionAccountingState:
        """Return the immutable realized state for one symbol."""

        if not isinstance(symbol, TradingSymbol):
            raise TypeError("symbol must be a TradingSymbol")
        return _copy_state(self._states.get(symbol, PositionAccountingState()))

    def positions(self) -> tuple[tuple[TradingSymbol, PositionAccountingState], ...]:
        """Return all committed states in canonical symbol order."""

        return tuple(
            (TradingSymbol(symbol.base, symbol.quote), _copy_state(self._states[symbol]))
            for symbol in sorted(self._states, key=str)
        )

    def clone(self) -> PositionAccountingCore:
        """Return an independent deterministic fork for higher-level atomic staging."""

        cloned = PositionAccountingCore()
        cloned._states = {
            TradingSymbol(symbol.base, symbol.quote): _copy_state(state)
            for symbol, state in self._states.items()
        }
        cloned._seen_fill_ids = set(self._seen_fill_ids)
        cloned._transitions = tuple(
            _copy_transition(transition) for transition in self._transitions
        )
        cloned._last_fill_at = self._last_fill_at
        return cloned

    def process(self, batch: PositionAccountingFillBatch) -> PositionAccountingTransitionBatch:
        """Validate every fill, then atomically commit the complete batch."""

        if not isinstance(batch, PositionAccountingFillBatch):
            raise TypeError("batch must be a PositionAccountingFillBatch")
        observed_at = require_utc(batch.observed_at, field="batch observed_at")
        if not isinstance(batch.fills, tuple) or not batch.fills:
            raise PositionAccountingCoreError("accounting fill batch must not be empty")
        if self._last_fill_at is not None and observed_at <= self._last_fill_at:
            raise PositionAccountingCoreError(
                "fill batches must advance strictly; submit equal-time fills together"
            )

        for fill in batch.fills:
            if not isinstance(fill, PositionAccountingFill):
                raise TypeError("batch fills must be PositionAccountingFill values")
        ordered = tuple(sorted(batch.fills, key=_fill_order))
        batch_ids: set[str] = set()
        owned: list[PositionAccountingFill] = []
        for fill in ordered:
            fill.validate()
            if fill.fill_at != observed_at:
                raise PositionAccountingCoreError("every fill must occur at batch observed_at")
            if fill.fill_id in batch_ids or fill.fill_id in self._seen_fill_ids:
                raise PositionAccountingCoreError("duplicate fill/order identity is forbidden")
            batch_ids.add(fill.fill_id)
            owned.append(_copy_fill(fill))

        pending = dict(self._states)
        transitions: list[PositionAccountingTransition] = []
        for fill in owned:
            next_state, transition = _apply(
                pending.get(fill.symbol, PositionAccountingState()), fill
            )
            pending[fill.symbol] = next_state
            transitions.append(transition)

        self._states = pending
        self._seen_fill_ids.update(batch_ids)
        self._transitions = (*self._transitions, *transitions)
        self._last_fill_at = observed_at
        return PositionAccountingTransitionBatch(
            observed_at,
            tuple(_copy_transition(transition) for transition in transitions),
            self.positions(),
        )


def _copy_fill(fill: PositionAccountingFill) -> PositionAccountingFill:
    return PositionAccountingFill(
        fill.fill_id,
        TradingSymbol(fill.symbol.base, fill.symbol.quote),
        fill.side,
        fill.quantity,
        fill.fill_at,
        fill.fill_price,
        fill.fee_quote,
    )


def _copy_state(state: PositionAccountingState) -> PositionAccountingState:
    return PositionAccountingState(
        state.quantity,
        state.average_entry_price,
        state.cumulative_gross_realized_pnl,
        state.cumulative_fees,
    )


def _copy_transition(
    transition: PositionAccountingTransition,
) -> PositionAccountingTransition:
    return PositionAccountingTransition(
        _copy_fill(transition.fill),
        transition.position_quantity_before,
        transition.position_quantity_after,
        transition.average_entry_before,
        transition.average_entry_after,
        transition.closed_quantity,
        transition.gross_realized_pnl_delta,
        transition.cumulative_gross_realized_pnl,
        transition.cumulative_fees,
        transition.cumulative_net_realized_pnl,
    )


def _apply(
    state: PositionAccountingState,
    fill: PositionAccountingFill,
) -> tuple[PositionAccountingState, PositionAccountingTransition]:
    """Apply the project's sole average-cost position/PnL formula."""

    with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
        before_quantity = state.quantity
        before_average = state.average_entry_price
        signed_fill = fill.quantity if fill.side is SimulatedExecutionSide.BUY else -fill.quantity
        after_quantity = before_quantity + signed_fill
        closed_quantity = Decimal(0)
        gross_delta = Decimal(0)

        if before_quantity == 0:
            after_average: Decimal | None = fill.fill_price
        elif before_quantity * signed_fill > 0:
            assert before_average is not None
            after_average = (
                abs(before_quantity) * before_average + abs(signed_fill) * fill.fill_price
            ) / abs(after_quantity)
        else:
            assert before_average is not None
            closed_quantity = min(abs(before_quantity), abs(signed_fill))
            if before_quantity > 0:
                gross_delta = (fill.fill_price - before_average) * closed_quantity
            else:
                gross_delta = (before_average - fill.fill_price) * closed_quantity
            if after_quantity == 0:
                after_quantity = Decimal(0)
                after_average = None
            elif before_quantity * after_quantity > 0:
                after_average = before_average
            else:
                after_average = fill.fill_price

        cumulative_gross = state.cumulative_gross_realized_pnl + gross_delta
        cumulative_fees = state.cumulative_fees + fill.fee_quote
        cumulative_net = cumulative_gross - cumulative_fees

    next_state = PositionAccountingState(
        quantity=after_quantity,
        average_entry_price=after_average,
        cumulative_gross_realized_pnl=cumulative_gross,
        cumulative_fees=cumulative_fees,
    )
    return next_state, PositionAccountingTransition(
        fill=fill,
        position_quantity_before=before_quantity,
        position_quantity_after=after_quantity,
        average_entry_before=before_average,
        average_entry_after=after_average,
        closed_quantity=closed_quantity,
        gross_realized_pnl_delta=gross_delta,
        cumulative_gross_realized_pnl=cumulative_gross,
        cumulative_fees=cumulative_fees,
        cumulative_net_realized_pnl=cumulative_net,
    )
