"""Deterministic fill-driven average-cost position and PnL accounting.

Fees remain separate from entry price and gross realized PnL. Marks are explicit already-observed
inputs and never mutate position state. No cash, margin, sizing, or storage access belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.backtest.execution import MarketFill, OrderSide
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import SourceId

_ACCOUNTING_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class PositionAccountingError(Exception):
    """Base error for invalid fill progression or causal marking."""


class FillBatchError(PositionAccountingError):
    """A fill batch violates identity, time, or atomicity constraints."""


class MarkError(PositionAccountingError):
    """A mark is invalid or was not observable by the requested snapshot time."""


class PositionSide(StrEnum):
    """Economic direction derived from signed position quantity."""

    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class PositionMark:
    """An external positive price already observed at a UTC timestamp."""

    symbol: TradingSymbol
    observed_at: datetime
    price: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.price, Decimal):
            raise TypeError("mark price must be a Decimal")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("mark price must be finite and positive")
        object.__setattr__(
            self, "observed_at", require_utc(self.observed_at, field="mark observed_at")
        )


def _fill_order(fill: MarketFill) -> tuple[str, str]:
    return str(fill.symbol), fill.order_id


@dataclass(frozen=True, slots=True)
class AccountingFillBatch:
    """All accepted fills at one timestamp, canonically ordered by symbol and order id."""

    observed_at: datetime
    fills: tuple[MarketFill, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fills", tuple(sorted(self.fills, key=_fill_order)))


@dataclass(frozen=True, slots=True)
class FillAccountingEvent:
    """Auditable accounting transition caused by one accepted fill."""

    order_id: str
    symbol: TradingSymbol
    fill_at: datetime
    side: OrderSide
    fill_quantity: Decimal
    fill_price: Decimal
    fee_quote: Decimal
    position_quantity_before: Decimal
    position_quantity_after: Decimal
    average_entry_before: Decimal | None
    average_entry_after: Decimal | None
    closed_quantity: Decimal
    gross_realized_pnl_delta: Decimal
    cumulative_gross_realized_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_net_realized_pnl: Decimal
    source_id: SourceId
    execution_timeframe: Timeframe


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Immutable realized and optionally marked PnL for one economic symbol position."""

    symbol: TradingSymbol
    observed_at: datetime
    quantity: Decimal
    side: PositionSide
    average_entry_price: Decimal | None
    cumulative_gross_realized_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_net_realized_pnl: Decimal
    mark_price: Decimal | None
    unrealized_gross_pnl: Decimal | None
    total_net_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class AccountingBatchResult:
    """Atomic fill transitions and resulting unmarked per-symbol snapshots."""

    observed_at: datetime
    events: tuple[FillAccountingEvent, ...]
    positions: tuple[PositionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class AccountingLedgerResult:
    """Complete deterministic ledger events, latest positions, and portfolio totals."""

    observed_at: datetime
    events: tuple[FillAccountingEvent, ...]
    latest_positions: tuple[PositionSnapshot, ...]
    portfolio_gross_realized_pnl: Decimal
    portfolio_fees: Decimal
    portfolio_net_realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class _PositionState:
    quantity: Decimal = Decimal(0)
    average_entry: Decimal | None = None
    cumulative_gross_realized_pnl: Decimal = Decimal(0)
    cumulative_fees: Decimal = Decimal(0)

    def apply(self, fill: MarketFill) -> tuple[_PositionState, FillAccountingEvent]:
        with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
            before_quantity = self.quantity
            before_average = self.average_entry
            signed_fill = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
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

            cumulative_gross = self.cumulative_gross_realized_pnl + gross_delta
            cumulative_fees = self.cumulative_fees + fill.fee_quote
            cumulative_net = cumulative_gross - cumulative_fees

        next_state = _PositionState(
            quantity=after_quantity,
            average_entry=after_average,
            cumulative_gross_realized_pnl=cumulative_gross,
            cumulative_fees=cumulative_fees,
        )
        event = FillAccountingEvent(
            order_id=fill.order_id,
            symbol=fill.symbol,
            fill_at=fill.fill_at,
            side=fill.side,
            fill_quantity=fill.quantity,
            fill_price=fill.fill_price,
            fee_quote=fill.fee_quote,
            position_quantity_before=before_quantity,
            position_quantity_after=after_quantity,
            average_entry_before=before_average,
            average_entry_after=after_average,
            closed_quantity=closed_quantity,
            gross_realized_pnl_delta=gross_delta,
            cumulative_gross_realized_pnl=cumulative_gross,
            cumulative_fees=cumulative_fees,
            cumulative_net_realized_pnl=cumulative_net,
            source_id=fill.source_id,
            execution_timeframe=fill.execution_timeframe,
        )
        return next_state, event


class PositionAccountingEngine:
    """Apply ordered fill batches atomically and derive causal marked snapshots."""

    def __init__(self) -> None:
        self._states: dict[TradingSymbol, _PositionState] = {}
        self._seen_order_ids: set[str] = set()
        self._events: tuple[FillAccountingEvent, ...] = ()
        self._last_fill_at: datetime | None = None

    def process(self, batch: AccountingFillBatch) -> AccountingBatchResult:
        """Validate a complete same-time batch, then commit all transitions together."""

        observed_at = require_utc(batch.observed_at, field="batch observed_at")
        if not batch.fills:
            raise FillBatchError("accounting fill batch must not be empty")
        if self._last_fill_at is not None and observed_at <= self._last_fill_at:
            raise FillBatchError(
                "fill batches must advance strictly; submit equal-time fills together"
            )

        batch_ids: set[str] = set()
        for fill in batch.fills:
            self._validate_fill(fill, observed_at)
            if fill.order_id in batch_ids or fill.order_id in self._seen_order_ids:
                raise FillBatchError("duplicate fill/order identity is forbidden")
            batch_ids.add(fill.order_id)

        pending = dict(self._states)
        events: list[FillAccountingEvent] = []
        for fill in batch.fills:
            state = pending.get(fill.symbol, _PositionState())
            next_state, event = state.apply(fill)
            pending[fill.symbol] = next_state
            events.append(event)

        self._states = pending
        self._seen_order_ids.update(batch_ids)
        self._events = (*self._events, *events)
        self._last_fill_at = observed_at
        positions = tuple(self.snapshot(symbol, observed_at) for symbol in sorted(pending, key=str))
        return AccountingBatchResult(observed_at, tuple(events), positions)

    def snapshot(
        self,
        symbol: TradingSymbol,
        observed_at: datetime,
        *,
        mark: PositionMark | None = None,
    ) -> PositionSnapshot:
        """Return a causal snapshot without changing cost basis or realized state."""

        observed_at = require_utc(observed_at, field="snapshot observed_at")
        if self._last_fill_at is not None and observed_at < self._last_fill_at:
            raise MarkError("snapshot cannot be earlier than the latest applied fill")
        if mark is not None:
            if mark.symbol != symbol:
                raise MarkError("mark symbol must match snapshot symbol")
            if mark.observed_at > observed_at:
                raise MarkError("mark must be observed at or before the snapshot")
        state = self._states.get(symbol, _PositionState())
        return _snapshot(symbol, observed_at, state, mark)

    def result(
        self,
        observed_at: datetime,
        *,
        marks: tuple[PositionMark, ...] = (),
    ) -> AccountingLedgerResult:
        """Return the complete ledger and latest snapshots without mutating state."""

        observed_at = require_utc(observed_at, field="result observed_at")
        marks_by_symbol: dict[TradingSymbol, PositionMark] = {}
        for mark in marks:
            if mark.symbol in marks_by_symbol:
                raise MarkError("duplicate mark symbol is forbidden")
            if mark.symbol not in self._states:
                raise MarkError("mark symbol has no ledger position")
            marks_by_symbol[mark.symbol] = mark
        positions = tuple(
            self.snapshot(symbol, observed_at, mark=marks_by_symbol.get(symbol))
            for symbol in sorted(self._states, key=str)
        )
        with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
            portfolio_gross = sum(
                (state.cumulative_gross_realized_pnl for state in self._states.values()),
                Decimal(0),
            )
            portfolio_fees = sum(
                (state.cumulative_fees for state in self._states.values()), Decimal(0)
            )
            portfolio_net = portfolio_gross - portfolio_fees
        return AccountingLedgerResult(
            observed_at,
            self._events,
            positions,
            portfolio_gross,
            portfolio_fees,
            portfolio_net,
        )

    def _validate_fill(self, fill: MarketFill, observed_at: datetime) -> None:
        if (
            not isinstance(fill.order_id, str)
            or not fill.order_id.strip()
            or fill.order_id != fill.order_id.strip()
        ):
            raise FillBatchError("fill order_id must be a canonical non-empty string")
        if not isinstance(fill.side, OrderSide):
            raise FillBatchError("fill side must be an OrderSide")
        for name, value in (
            ("quantity", fill.quantity),
            ("fill_price", fill.fill_price),
            ("fee_quote", fill.fee_quote),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise FillBatchError(f"fill {name} must be a finite Decimal")
        if fill.quantity <= 0 or fill.fill_price <= 0:
            raise FillBatchError("fill quantity and price must be positive")
        if fill.fee_quote < 0:
            raise FillBatchError("fill fee must be non-negative")

        fill_at = require_utc(fill.fill_at, field="fill_at")
        created_at = require_utc(fill.created_at, field="fill created_at")
        eligible_at = require_utc(fill.eligible_at, field="fill eligible_at")
        execution_open = require_utc(
            fill.execution_candle_open_time, field="execution candle open_time"
        )
        if fill_at != observed_at:
            raise FillBatchError("every fill must occur at batch observed_at")
        if fill_at != execution_open or fill_at < eligible_at or eligible_at < created_at:
            raise FillBatchError("fill timestamps violate execution causality")


def _snapshot(
    symbol: TradingSymbol,
    observed_at: datetime,
    state: _PositionState,
    mark: PositionMark | None,
) -> PositionSnapshot:
    if state.quantity > 0:
        side = PositionSide.LONG
    elif state.quantity < 0:
        side = PositionSide.SHORT
    else:
        side = PositionSide.FLAT

    with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
        net_realized = state.cumulative_gross_realized_pnl - state.cumulative_fees
        if state.quantity == 0:
            unrealized: Decimal | None = Decimal(0)
            total_net: Decimal | None = net_realized
        elif mark is None:
            unrealized = None
            total_net = None
        else:
            assert state.average_entry is not None
            if state.quantity > 0:
                unrealized = (mark.price - state.average_entry) * abs(state.quantity)
            else:
                unrealized = (state.average_entry - mark.price) * abs(state.quantity)
            total_net = net_realized + unrealized

    return PositionSnapshot(
        symbol=symbol,
        observed_at=observed_at,
        quantity=state.quantity,
        side=side,
        average_entry_price=state.average_entry,
        cumulative_gross_realized_pnl=state.cumulative_gross_realized_pnl,
        cumulative_fees=state.cumulative_fees,
        cumulative_net_realized_pnl=net_realized,
        mark_price=None if mark is None else mark.price,
        unrealized_gross_pnl=unrealized,
        total_net_pnl=total_net,
    )
