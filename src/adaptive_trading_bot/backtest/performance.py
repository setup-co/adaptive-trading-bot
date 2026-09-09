"""Deterministic completed-trade reconstruction and absolute-PnL performance analysis.

The analyzer consumes accepted accounting events only.  It does not read storage, force-close
positions, or infer percentage returns, capital, risk, or strategy intent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.backtest.accounting import FillAccountingEvent, PositionSide
from adaptive_trading_bot.backtest.execution import OrderSide
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc

_PERFORMANCE_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)


class PerformanceAnalysisError(Exception):
    """An accounting-event stream cannot be reconstructed unambiguously."""


class PerformanceBatchError(PerformanceAnalysisError):
    """A performance batch violates identity, time, or transition constraints."""


class TradeOutcome(StrEnum):
    """Completed-trade classification based on net quote-currency PnL."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"


class ProfitFactorUndefinedReason(StrEnum):
    """Why a metric has no finite profit-factor value."""

    NO_COMPLETED_TRADES = "NO_COMPLETED_TRADES"
    NO_GROSS_LOSS = "NO_GROSS_LOSS"


@dataclass(frozen=True, slots=True)
class CompletedTrade:
    """One immutable flat-to-flat (or crossing-to-crossing) economic trade."""

    trade_id: str
    symbol: TradingSymbol
    side: PositionSide
    opened_at: datetime
    closed_at: datetime
    opening_order_id: str
    closing_order_id: str
    entry_basis_price: Decimal
    total_opened_quantity: Decimal
    total_closed_quantity: Decimal
    maximum_position_quantity: Decimal
    gross_realized_pnl: Decimal
    allocated_fees: Decimal
    net_pnl: Decimal
    outcome: TradeOutcome
    duration: timedelta
    contributing_order_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenTrade:
    """Explicit end-of-stream state for an economic trade that remains open."""

    trade_id: str
    symbol: TradingSymbol
    side: PositionSide
    opened_at: datetime
    opening_order_id: str
    entry_basis_price: Decimal
    current_quantity: Decimal
    total_opened_quantity: Decimal
    total_closed_quantity: Decimal
    maximum_position_quantity: Decimal
    gross_realized_pnl: Decimal
    allocated_fees: Decimal
    contributing_order_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PnlPathPoint:
    """Canonical completed-trade cumulative net PnL and zero-anchored drawdown."""

    closed_at: datetime
    symbol: TradingSymbol
    trade_id: str
    cumulative_net_pnl: Decimal
    running_peak_net_pnl: Decimal
    drawdown: Decimal


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """Absolute quote-currency metrics calculated from completed trades only."""

    completed_trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    profit_factor_undefined_reason: ProfitFactorUndefinedReason | None
    net_profit: Decimal
    average_trade_net_pnl: Decimal | None
    median_trade_net_pnl: Decimal | None
    expectancy: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    maximum_consecutive_losses: int
    average_trade_duration: timedelta | None
    median_trade_duration: timedelta | None
    pnl_path: tuple[PnlPathPoint, ...]
    maximum_absolute_drawdown: Decimal
    drawdown_peak_at: datetime | None
    drawdown_trough_at: datetime | None
    drawdown_recovered_at: datetime | None


@dataclass(frozen=True, slots=True)
class SymbolTradeMetrics:
    """Deterministically ordered per-symbol completed-trade metrics."""

    symbol: TradingSymbol
    metrics: TradeMetrics


@dataclass(frozen=True, slots=True)
class PerformanceAnalysisResult:
    """Current immutable portfolio analysis, including open trades separately."""

    completed_trades: tuple[CompletedTrade, ...]
    open_trades: tuple[OpenTrade, ...]
    open_trade_count: int
    portfolio_metrics: TradeMetrics
    per_symbol_metrics: tuple[SymbolTradeMetrics, ...]


def _event_order(event: FillAccountingEvent) -> tuple[str, str]:
    return str(event.symbol), event.order_id


@dataclass(frozen=True, slots=True)
class PerformanceEventBatch:
    """All accepted accounting events at one timestamp, canonically ordered."""

    observed_at: datetime
    events: tuple[FillAccountingEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", require_utc(self.observed_at, field="batch observed_at")
        )
        object.__setattr__(self, "events", tuple(sorted(self.events, key=_event_order)))


@dataclass(frozen=True, slots=True)
class PerformanceBatchResult:
    """Trades completed by one atomic batch and the resulting open trades."""

    observed_at: datetime
    completed_trades: tuple[CompletedTrade, ...]
    open_trades: tuple[OpenTrade, ...]


@dataclass(frozen=True, slots=True)
class _AccountingTotals:
    gross: Decimal = _ZERO
    fees: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class _OpenTradeState:
    trade_id: str
    symbol: TradingSymbol
    side: PositionSide
    opened_at: datetime
    opening_order_id: str
    entry_basis_price: Decimal
    current_signed_quantity: Decimal
    total_opened_quantity: Decimal
    total_closed_quantity: Decimal
    maximum_position_quantity: Decimal
    gross_realized_pnl: Decimal
    allocated_fees: Decimal
    contributing_order_ids: tuple[str, ...]

    def public(self) -> OpenTrade:
        return OpenTrade(
            trade_id=self.trade_id,
            symbol=self.symbol,
            side=self.side,
            opened_at=self.opened_at,
            opening_order_id=self.opening_order_id,
            entry_basis_price=self.entry_basis_price,
            current_quantity=abs(self.current_signed_quantity),
            total_opened_quantity=self.total_opened_quantity,
            total_closed_quantity=self.total_closed_quantity,
            maximum_position_quantity=self.maximum_position_quantity,
            gross_realized_pnl=self.gross_realized_pnl,
            allocated_fees=self.allocated_fees,
            contributing_order_ids=self.contributing_order_ids,
        )


def _side_for_quantity(quantity: Decimal) -> PositionSide:
    if quantity > 0:
        return PositionSide.LONG
    if quantity < 0:
        return PositionSide.SHORT
    return PositionSide.FLAT


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
        return sum(values, start=_ZERO) / Decimal(len(values))


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _duration_mean(values: tuple[timedelta, ...]) -> timedelta | None:
    if not values:
        return None
    return sum(values, start=timedelta()) / len(values)


def _duration_median(values: tuple[timedelta, ...]) -> timedelta | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def calculate_trade_metrics(trades: tuple[CompletedTrade, ...]) -> TradeMetrics:
    """Calculate deterministic metrics in canonical close order."""

    with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
        return _calculate_trade_metrics(trades)


def _calculate_trade_metrics(trades: tuple[CompletedTrade, ...]) -> TradeMetrics:
    ordered = tuple(
        sorted(trades, key=lambda trade: (trade.closed_at, str(trade.symbol), trade.trade_id))
    )
    wins = tuple(trade.net_pnl for trade in ordered if trade.net_pnl > 0)
    losses = tuple(-trade.net_pnl for trade in ordered if trade.net_pnl < 0)
    net_values = tuple(trade.net_pnl for trade in ordered)
    durations = tuple(trade.duration for trade in ordered)

    with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
        gross_profit = sum(wins, start=_ZERO)
        gross_loss = sum(losses, start=_ZERO)
        net_profit = sum(net_values, start=_ZERO)
        win_rate = Decimal(len(wins)) / Decimal(len(ordered)) if ordered else None
        profit_factor = gross_profit / gross_loss if gross_loss else None

    if not ordered:
        undefined_reason = ProfitFactorUndefinedReason.NO_COMPLETED_TRADES
    elif not gross_loss:
        undefined_reason = ProfitFactorUndefinedReason.NO_GROSS_LOSS
    else:
        undefined_reason = None

    maximum_streak = 0
    current_streak = 0
    for trade in ordered:
        if trade.net_pnl < 0:
            current_streak += 1
            maximum_streak = max(maximum_streak, current_streak)
        else:
            current_streak = 0

    path: list[PnlPathPoint] = []
    cumulative = _ZERO
    running_peak = _ZERO
    running_peak_at: datetime | None = None
    peak_times: list[datetime | None] = []
    for trade in ordered:
        cumulative += trade.net_pnl
        if cumulative > running_peak:
            running_peak = cumulative
            running_peak_at = trade.closed_at
        path.append(
            PnlPathPoint(
                closed_at=trade.closed_at,
                symbol=trade.symbol,
                trade_id=trade.trade_id,
                cumulative_net_pnl=cumulative,
                running_peak_net_pnl=running_peak,
                drawdown=running_peak - cumulative,
            )
        )
        peak_times.append(running_peak_at)

    maximum_drawdown = _ZERO
    maximum_drawdown_index: int | None = None
    for index, point in enumerate(path):
        if point.drawdown > maximum_drawdown:
            maximum_drawdown = point.drawdown
            maximum_drawdown_index = index

    drawdown_peak_at: datetime | None = None
    drawdown_trough_at: datetime | None = None
    drawdown_recovered_at: datetime | None = None
    if maximum_drawdown_index is not None:
        maximum_point = path[maximum_drawdown_index]
        drawdown_peak_at = peak_times[maximum_drawdown_index]
        drawdown_trough_at = maximum_point.closed_at
        for later_point in path[maximum_drawdown_index + 1 :]:
            if later_point.cumulative_net_pnl >= maximum_point.running_peak_net_pnl:
                drawdown_recovered_at = later_point.closed_at
                break

    return TradeMetrics(
        completed_trade_count=len(ordered),
        win_count=len(wins),
        loss_count=len(losses),
        breakeven_count=len(ordered) - len(wins) - len(losses),
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        profit_factor_undefined_reason=undefined_reason,
        net_profit=net_profit,
        average_trade_net_pnl=_mean(net_values),
        median_trade_net_pnl=_median(net_values),
        expectancy=_mean(net_values),
        average_win=_mean(wins),
        average_loss=_mean(losses),
        largest_win=max(wins, default=None),
        largest_loss=max(losses, default=None),
        maximum_consecutive_losses=maximum_streak,
        average_trade_duration=_duration_mean(durations),
        median_trade_duration=_duration_median(durations),
        pnl_path=tuple(path),
        maximum_absolute_drawdown=maximum_drawdown,
        drawdown_peak_at=drawdown_peak_at,
        drawdown_trough_at=drawdown_trough_at,
        drawdown_recovered_at=drawdown_recovered_at,
    )


class TradePerformanceAnalyzer:
    """Incrementally reconstruct completed trades from atomic accounting-event batches."""

    def __init__(self) -> None:
        self._last_observed_at: datetime | None = None
        self._seen_order_ids: set[str] = set()
        self._open: dict[TradingSymbol, _OpenTradeState] = {}
        self._totals: dict[TradingSymbol, _AccountingTotals] = {}
        self._completed: list[CompletedTrade] = []
        self._next_trade_number = 1

    def process(self, batch: PerformanceEventBatch) -> PerformanceBatchResult:
        """Validate and apply one same-time batch atomically."""

        with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
            return self._process(batch)

    def _process(self, batch: PerformanceEventBatch) -> PerformanceBatchResult:
        self._validate_batch(batch)
        open_states = dict(self._open)
        totals = dict(self._totals)
        completed_now: list[CompletedTrade] = []
        next_trade_number = self._next_trade_number

        def start_trade(
            event: FillAccountingEvent, *, fee: Decimal, opened_quantity: Decimal
        ) -> _OpenTradeState:
            nonlocal next_trade_number
            average = event.average_entry_after
            assert average is not None
            state = _OpenTradeState(
                trade_id=f"trade-{next_trade_number:08d}",
                symbol=event.symbol,
                side=_side_for_quantity(event.position_quantity_after),
                opened_at=event.fill_at,
                opening_order_id=event.order_id,
                entry_basis_price=average,
                current_signed_quantity=event.position_quantity_after,
                total_opened_quantity=opened_quantity,
                total_closed_quantity=_ZERO,
                maximum_position_quantity=abs(event.position_quantity_after),
                gross_realized_pnl=_ZERO,
                allocated_fees=fee,
                contributing_order_ids=(event.order_id,),
            )
            next_trade_number += 1
            return state

        for event in batch.events:
            prior_totals = totals.get(event.symbol, _AccountingTotals())
            totals[event.symbol] = _AccountingTotals(
                gross=prior_totals.gross + event.gross_realized_pnl_delta,
                fees=prior_totals.fees + event.fee_quote,
            )
            if event.position_quantity_before == 0:
                open_states[event.symbol] = start_trade(
                    event, fee=event.fee_quote, opened_quantity=event.fill_quantity
                )
                continue

            active = open_states[event.symbol]
            crosses = event.position_quantity_before * event.position_quantity_after < 0
            if crosses:
                with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
                    closing_fee = event.fee_quote * (event.closed_quantity / event.fill_quantity)
                    opening_fee = event.fee_quote - closing_fee
                closing_state = replace(
                    active,
                    current_signed_quantity=_ZERO,
                    total_closed_quantity=(active.total_closed_quantity + event.closed_quantity),
                    gross_realized_pnl=(active.gross_realized_pnl + event.gross_realized_pnl_delta),
                    allocated_fees=active.allocated_fees + closing_fee,
                    contributing_order_ids=(*active.contributing_order_ids, event.order_id),
                )
                completed_now.append(self._complete(closing_state, event))
                open_states[event.symbol] = start_trade(
                    event,
                    fee=opening_fee,
                    opened_quantity=abs(event.position_quantity_after),
                )
                continue

            next_state = replace(
                active,
                current_signed_quantity=event.position_quantity_after,
                total_closed_quantity=active.total_closed_quantity + event.closed_quantity,
                gross_realized_pnl=(active.gross_realized_pnl + event.gross_realized_pnl_delta),
                allocated_fees=active.allocated_fees + event.fee_quote,
                contributing_order_ids=(*active.contributing_order_ids, event.order_id),
            )
            if abs(event.position_quantity_after) > abs(event.position_quantity_before):
                assert event.average_entry_after is not None
                next_state = replace(
                    next_state,
                    entry_basis_price=event.average_entry_after,
                    total_opened_quantity=(active.total_opened_quantity + event.fill_quantity),
                    maximum_position_quantity=max(
                        active.maximum_position_quantity,
                        abs(event.position_quantity_after),
                    ),
                )
            if event.position_quantity_after == 0:
                completed_now.append(self._complete(next_state, event))
                del open_states[event.symbol]
            else:
                open_states[event.symbol] = next_state

        self._open = open_states
        self._totals = totals
        self._completed.extend(completed_now)
        self._next_trade_number = next_trade_number
        self._seen_order_ids.update(event.order_id for event in batch.events)
        self._last_observed_at = batch.observed_at
        return PerformanceBatchResult(
            observed_at=batch.observed_at,
            completed_trades=tuple(completed_now),
            open_trades=self._public_open(open_states),
        )

    def result(self) -> PerformanceAnalysisResult:
        """Return portfolio and per-symbol metrics without force-closing open trades."""

        completed = tuple(
            sorted(
                self._completed,
                key=lambda trade: (trade.closed_at, str(trade.symbol), trade.trade_id),
            )
        )
        symbols = sorted({trade.symbol for trade in completed} | set(self._open), key=str)
        per_symbol = tuple(
            SymbolTradeMetrics(
                symbol=symbol,
                metrics=calculate_trade_metrics(
                    tuple(trade for trade in completed if trade.symbol == symbol)
                ),
            )
            for symbol in symbols
        )
        open_trades = self._public_open(self._open)
        return PerformanceAnalysisResult(
            completed_trades=completed,
            open_trades=open_trades,
            open_trade_count=len(open_trades),
            portfolio_metrics=calculate_trade_metrics(completed),
            per_symbol_metrics=per_symbol,
        )

    @staticmethod
    def _public_open(states: dict[TradingSymbol, _OpenTradeState]) -> tuple[OpenTrade, ...]:
        return tuple(states[symbol].public() for symbol in sorted(states, key=str))

    @staticmethod
    def _complete(state: _OpenTradeState, event: FillAccountingEvent) -> CompletedTrade:
        net = state.gross_realized_pnl - state.allocated_fees
        if net > 0:
            outcome = TradeOutcome.WIN
        elif net < 0:
            outcome = TradeOutcome.LOSS
        else:
            outcome = TradeOutcome.BREAKEVEN
        return CompletedTrade(
            trade_id=state.trade_id,
            symbol=state.symbol,
            side=state.side,
            opened_at=state.opened_at,
            closed_at=event.fill_at,
            opening_order_id=state.opening_order_id,
            closing_order_id=event.order_id,
            entry_basis_price=state.entry_basis_price,
            total_opened_quantity=state.total_opened_quantity,
            total_closed_quantity=state.total_closed_quantity,
            maximum_position_quantity=state.maximum_position_quantity,
            gross_realized_pnl=state.gross_realized_pnl,
            allocated_fees=state.allocated_fees,
            net_pnl=net,
            outcome=outcome,
            duration=event.fill_at - state.opened_at,
            contributing_order_ids=state.contributing_order_ids,
        )

    def _validate_batch(self, batch: PerformanceEventBatch) -> None:
        if not batch.events:
            raise PerformanceBatchError("performance batch must contain at least one event")
        if self._last_observed_at is not None and batch.observed_at <= self._last_observed_at:
            raise PerformanceBatchError("performance batches must advance strictly in time")

        batch_order_ids: set[str] = set()
        quantities = {symbol: state.current_signed_quantity for symbol, state in self._open.items()}
        totals = dict(self._totals)
        for event in batch.events:
            if event.fill_at != batch.observed_at:
                raise PerformanceBatchError("every event must match the batch timestamp")
            require_utc(event.fill_at, field="event fill_at")
            if not event.order_id.strip():
                raise PerformanceBatchError("event order_id must not be empty")
            if event.order_id in self._seen_order_ids or event.order_id in batch_order_ids:
                raise PerformanceBatchError(
                    f"duplicate accounting event order_id: {event.order_id}"
                )
            batch_order_ids.add(event.order_id)
            self._validate_decimal_fields(event)

            expected_before = quantities.get(event.symbol, _ZERO)
            if event.position_quantity_before != expected_before:
                if expected_before == 0:
                    raise PerformanceBatchError(
                        f"event closes or changes {event.symbol} without an active trade"
                    )
                raise PerformanceBatchError(
                    f"event position before is inconsistent for {event.symbol}"
                )
            signed_fill = (
                event.fill_quantity if event.side is OrderSide.BUY else -event.fill_quantity
            )
            expected_after = expected_before + signed_fill
            if event.position_quantity_after != expected_after:
                raise PerformanceBatchError(
                    f"event side or quantity is inconsistent for {event.symbol}"
                )
            expected_closed = (
                min(abs(expected_before), event.fill_quantity)
                if expected_before * signed_fill < 0
                else _ZERO
            )
            if event.closed_quantity != expected_closed:
                raise PerformanceBatchError(
                    f"event closed quantity is inconsistent for {event.symbol}"
                )
            self._validate_averages(event, expected_before, expected_after, signed_fill)
            if expected_closed == 0 and event.gross_realized_pnl_delta != 0:
                raise PerformanceBatchError(
                    f"non-closing event has realized PnL for {event.symbol}"
                )

            prior = totals.get(event.symbol, _AccountingTotals())
            expected_gross = prior.gross + event.gross_realized_pnl_delta
            expected_fees = prior.fees + event.fee_quote
            if event.cumulative_gross_realized_pnl != expected_gross:
                raise PerformanceBatchError(
                    f"cumulative gross realized PnL is inconsistent for {event.symbol}"
                )
            if event.cumulative_fees != expected_fees:
                raise PerformanceBatchError(f"cumulative fees are inconsistent for {event.symbol}")
            if event.cumulative_net_realized_pnl != expected_gross - expected_fees:
                raise PerformanceBatchError(
                    f"cumulative net realized PnL is inconsistent for {event.symbol}"
                )
            quantities[event.symbol] = expected_after
            totals[event.symbol] = _AccountingTotals(gross=expected_gross, fees=expected_fees)

    @staticmethod
    def _validate_decimal_fields(event: FillAccountingEvent) -> None:
        positive = {
            "fill_quantity": event.fill_quantity,
            "fill_price": event.fill_price,
        }
        nonnegative = {
            "fee_quote": event.fee_quote,
            "closed_quantity": event.closed_quantity,
            "cumulative_fees": event.cumulative_fees,
        }
        unrestricted = {
            "position_quantity_before": event.position_quantity_before,
            "position_quantity_after": event.position_quantity_after,
            "gross_realized_pnl_delta": event.gross_realized_pnl_delta,
            "cumulative_gross_realized_pnl": event.cumulative_gross_realized_pnl,
            "cumulative_net_realized_pnl": event.cumulative_net_realized_pnl,
        }
        for name, value in positive.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise PerformanceBatchError(f"event {name} must be a finite positive Decimal")
        for name, value in nonnegative.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise PerformanceBatchError(f"event {name} must be a finite non-negative Decimal")
        for name, value in unrestricted.items():
            if not isinstance(value, Decimal) or not value.is_finite():
                raise PerformanceBatchError(f"event {name} must be a finite Decimal")
        if not isinstance(event.side, OrderSide):
            raise PerformanceBatchError("event side must be an OrderSide")

    @staticmethod
    def _validate_averages(
        event: FillAccountingEvent,
        before: Decimal,
        after: Decimal,
        signed_fill: Decimal,
    ) -> None:
        for name, value in (
            ("average_entry_before", event.average_entry_before),
            ("average_entry_after", event.average_entry_after),
        ):
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise PerformanceBatchError(f"event {name} must be None or a positive Decimal")
        if before == 0:
            if (
                event.average_entry_before is not None
                or event.average_entry_after != event.fill_price
            ):
                raise PerformanceBatchError("flat opening event has inconsistent entry averages")
            return
        if event.average_entry_before is None:
            raise PerformanceBatchError("active-position event must have an entry average before")
        if after == 0:
            if event.average_entry_after is not None:
                raise PerformanceBatchError("flat result must not retain an entry average")
            return
        if before * after < 0:
            if event.average_entry_after != event.fill_price:
                raise PerformanceBatchError("crossing event must use fill price as the new basis")
            return
        if before * signed_fill < 0:
            if event.average_entry_after != event.average_entry_before:
                raise PerformanceBatchError("partial reduction must preserve its entry basis")
            return
        with localcontext(_PERFORMANCE_DECIMAL_CONTEXT):
            expected_average = (
                abs(before) * event.average_entry_before + abs(signed_fill) * event.fill_price
            ) / abs(after)
        if event.average_entry_after != expected_average:
            raise PerformanceBatchError("position addition has an inconsistent entry basis")
