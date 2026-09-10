"""Backtest compatibility adapter over shared position accounting.

Backtest candle provenance and causal marks stay here. Fill identity, atomic batches,
chronology, and all average-cost arithmetic are delegated to the shared core.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingCoreError,
    PositionAccountingFill,
    PositionAccountingFillBatch,
    PositionAccountingState,
    PositionAccountingTransition,
)
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
    """Auditable Backtest transition caused by one accepted fill."""

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


def market_fill_to_position_accounting_fill(fill: MarketFill) -> PositionAccountingFill:
    """Copy a Backtest fill into the shared arithmetic input without changing values."""

    return PositionAccountingFill(
        fill_id=fill.order_id,
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        fill_at=fill.fill_at,
        fill_price=fill.fill_price,
        fee_quote=fill.fee_quote,
    )


class PositionAccountingEngine:
    """Preserve the Backtest accounting API over the shared deterministic core."""

    def __init__(self) -> None:
        self._core = PositionAccountingCore()
        self._events: tuple[FillAccountingEvent, ...] = ()

    def process(self, batch: AccountingFillBatch) -> AccountingBatchResult:
        """Validate Backtest provenance, then atomically delegate the fill batch."""

        observed_at = require_utc(batch.observed_at, field="batch observed_at")
        if not batch.fills:
            raise FillBatchError("accounting fill batch must not be empty")
        if self._core.last_fill_at is not None and observed_at <= self._core.last_fill_at:
            raise FillBatchError(
                "fill batches must advance strictly; submit equal-time fills together"
            )
        for fill in batch.fills:
            self._validate_backtest_provenance(fill, observed_at)
        try:
            shared_fills = tuple(
                market_fill_to_position_accounting_fill(fill) for fill in batch.fills
            )
            shared = self._core.process(PositionAccountingFillBatch(observed_at, shared_fills))
        except (PositionAccountingCoreError, TypeError) as error:
            raise FillBatchError(str(error)) from error

        fills_by_id = {fill.order_id: fill for fill in batch.fills}
        events = tuple(
            _backtest_event(fills_by_id[transition.fill.fill_id], transition)
            for transition in shared.transitions
        )
        self._events = (*self._events, *events)
        positions = tuple(
            _snapshot(symbol, observed_at, state, None) for symbol, state in shared.positions
        )
        return AccountingBatchResult(observed_at, events, positions)

    def snapshot(
        self,
        symbol: TradingSymbol,
        observed_at: datetime,
        *,
        mark: PositionMark | None = None,
    ) -> PositionSnapshot:
        """Return a causal snapshot without changing cost basis or realized state."""

        observed_at = require_utc(observed_at, field="snapshot observed_at")
        if self._core.last_fill_at is not None and observed_at < self._core.last_fill_at:
            raise MarkError("snapshot cannot be earlier than the latest applied fill")
        if mark is not None:
            if mark.symbol != symbol:
                raise MarkError("mark symbol must match snapshot symbol")
            if mark.observed_at > observed_at:
                raise MarkError("mark must be observed at or before the snapshot")
        return _snapshot(symbol, observed_at, self._core.position(symbol), mark)

    def result(
        self,
        observed_at: datetime,
        *,
        marks: tuple[PositionMark, ...] = (),
    ) -> AccountingLedgerResult:
        """Return the complete Backtest ledger without mutating shared state."""

        observed_at = require_utc(observed_at, field="result observed_at")
        marks_by_symbol: dict[TradingSymbol, PositionMark] = {}
        state_pairs = self._core.positions()
        state_symbols = {symbol for symbol, _ in state_pairs}
        for mark in marks:
            if mark.symbol in marks_by_symbol:
                raise MarkError("duplicate mark symbol is forbidden")
            if mark.symbol not in state_symbols:
                raise MarkError("mark symbol has no ledger position")
            marks_by_symbol[mark.symbol] = mark
        positions = tuple(
            self.snapshot(symbol, observed_at, mark=marks_by_symbol.get(symbol))
            for symbol, _ in state_pairs
        )
        with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
            portfolio_gross = sum(
                (state.cumulative_gross_realized_pnl for _, state in state_pairs), Decimal(0)
            )
            portfolio_fees = sum((state.cumulative_fees for _, state in state_pairs), Decimal(0))
            portfolio_net = portfolio_gross - portfolio_fees
        return AccountingLedgerResult(
            observed_at,
            self._events,
            positions,
            portfolio_gross,
            portfolio_fees,
            portfolio_net,
        )

    def _validate_backtest_provenance(self, fill: MarketFill, observed_at: datetime) -> None:
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


def _backtest_event(
    fill: MarketFill, transition: PositionAccountingTransition
) -> FillAccountingEvent:
    return FillAccountingEvent(
        order_id=fill.order_id,
        symbol=fill.symbol,
        fill_at=fill.fill_at,
        side=fill.side,
        fill_quantity=fill.quantity,
        fill_price=fill.fill_price,
        fee_quote=fill.fee_quote,
        position_quantity_before=transition.position_quantity_before,
        position_quantity_after=transition.position_quantity_after,
        average_entry_before=transition.average_entry_before,
        average_entry_after=transition.average_entry_after,
        closed_quantity=transition.closed_quantity,
        gross_realized_pnl_delta=transition.gross_realized_pnl_delta,
        cumulative_gross_realized_pnl=transition.cumulative_gross_realized_pnl,
        cumulative_fees=transition.cumulative_fees,
        cumulative_net_realized_pnl=transition.cumulative_net_realized_pnl,
        source_id=fill.source_id,
        execution_timeframe=fill.execution_timeframe,
    )


def _snapshot(
    symbol: TradingSymbol,
    observed_at: datetime,
    state: PositionAccountingState,
    mark: PositionMark | None,
) -> PositionSnapshot:
    if state.quantity > 0:
        side = PositionSide.LONG
    elif state.quantity < 0:
        side = PositionSide.SHORT
    else:
        side = PositionSide.FLAT

    with localcontext(_ACCOUNTING_DECIMAL_CONTEXT):
        net_realized = state.cumulative_net_realized_pnl
        if state.quantity == 0:
            unrealized: Decimal | None = Decimal(0)
            total_net: Decimal | None = net_realized
        elif mark is None:
            unrealized = None
            total_net = None
        else:
            assert state.average_entry_price is not None
            if state.quantity > 0:
                unrealized = (mark.price - state.average_entry_price) * abs(state.quantity)
            else:
                unrealized = (state.average_entry_price - mark.price) * abs(state.quantity)
            total_net = net_realized + unrealized

    return PositionSnapshot(
        symbol=symbol,
        observed_at=observed_at,
        quantity=state.quantity,
        side=side,
        average_entry_price=state.average_entry_price,
        cumulative_gross_realized_pnl=state.cumulative_gross_realized_pnl,
        cumulative_fees=state.cumulative_fees,
        cumulative_net_realized_pnl=net_realized,
        mark_price=None if mark is None else mark.price,
        unrealized_gross_pnl=unrealized,
        total_net_pnl=total_net,
    )
