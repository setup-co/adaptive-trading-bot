"""Single-position, fixed-2R Breakout + Retest historical replay; never trading authority."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hmac import compare_digest

from adaptive_trading_bot.backtest import (
    AccountingFillBatch,
    BacktestExecutionSimulator,
    ExecutionCandleSource,
    ExecutionCostConfiguration,
    ExecutionSeries,
    FillAccountingEvent,
    MarketFill,
    MarketOrderRequest,
    OrderSide,
    PositionAccountingEngine,
    PositionSnapshot,
    UnfilledOrder,
)
from adaptive_trading_bot.domain import Candle, Timeframe
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.domain.entry_chronology import (
    CAUSAL_ENTRY_CHRONOLOGY_V2,
    entry_source_start,
    validate_entry_fill_chronology,
)
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryOrderIntentAuthorizer,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryFillInvalidationAudit,
    EntryIntentProvenance,
    HistoricalStrategyContextObservation,
    _BoundExecutionCandleSource,
    _entry_fill_geometry_is_valid,
    _entry_fill_invalidation_audit,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _integrity_seal
from adaptive_trading_bot.strategies import BreakoutRetestStrategy

POLICY_VERSION = "BREAKOUT_RETEST_FIXED_2R_V1"
_SCHEMA = "breakout-retest-fixed-2r-replay-v2"
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class BreakoutRetestReplayError(ValueError):
    """Historical context, authorization, or sealed replay is inconsistent."""


class BreakoutRetestReplayStatus(StrEnum):
    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    ENTRY_FILL_INVALIDATED = "ENTRY_FILL_INVALIDATED"
    REPLAYED = "REPLAYED"


class BreakoutRetestExitCause(StrEnum):
    PROTECTIVE_STOP = "PROTECTIVE_STOP"
    FIXED_2R_TARGET = "FIXED_2R_TARGET"


def fixed_2r_geometry(
    side: EntrySide, actual_fill_price: Decimal, stop_basis: Decimal
) -> tuple[Decimal, Decimal]:
    """The one frozen target formula, using the actual simulator fill."""
    if not isinstance(side, EntrySide) or any(
        not isinstance(value, Decimal) or not value.is_finite() or value <= 0
        for value in (actual_fill_price, stop_basis)
    ):
        raise BreakoutRetestReplayError("2R inputs are invalid")
    with localcontext(_DECIMAL_CONTEXT):
        distance = (
            actual_fill_price - stop_basis
            if side is EntrySide.BUY
            else stop_basis - actual_fill_price
        )
        target = (
            actual_fill_price + 2 * distance
            if side is EntrySide.BUY
            else actual_fill_price - 2 * distance
        )
    if distance <= 0 or target <= 0:
        raise BreakoutRetestReplayError("actual fill has invalid 2R geometry")
    return distance, target


def fixed_2r_touches(
    side: EntrySide, candle: Candle, stop_basis: Decimal, target_trigger_price: Decimal
) -> tuple[bool, bool]:
    """Closed-candle trigger facts; stop precedence is applied by the replay runner."""
    if side is EntrySide.BUY:
        return candle.low <= stop_basis, candle.high >= target_trigger_price
    if side is EntrySide.SELL:
        return candle.high >= stop_basis, candle.low <= target_trigger_price
    raise BreakoutRetestReplayError("2R trigger side is invalid")


@dataclass(frozen=True, slots=True)
class BreakoutRetestReplayResult:
    decision_identity: DecisionIdentity
    execution_series: ExecutionSeries
    execution_costs: ExecutionCostConfiguration
    end: datetime
    entry_intent: EntryIntentProvenance
    entry_request: MarketOrderRequest
    status: BreakoutRetestReplayStatus
    entry_fill: MarketFill | None = None
    entry_unfilled: UnfilledOrder | None = None
    entry_fill_invalidation: EntryFillInvalidationAudit | None = None
    stop_basis: Decimal | None = None
    risk_distance: Decimal | None = None
    target_trigger_price: Decimal | None = None
    trigger_candle_open_at: datetime | None = None
    exit_trigger_at: datetime | None = None
    stop_touched: bool = False
    target_touched: bool = False
    same_bar_stop_precedence: bool = False
    exit_cause: BreakoutRetestExitCause | None = None
    exit_request: MarketOrderRequest | None = None
    exit_fill: MarketFill | None = None
    exit_unfilled: UnfilledOrder | None = None
    accounting_events: tuple[FillAccountingEvent, ...] = ()
    final_position: PositionSnapshot | None = None
    position_closed: bool = False
    realized_gross_pnl: Decimal | None = None
    total_fees: Decimal | None = None
    realized_net_pnl: Decimal | None = None
    unresolved_quantity: Decimal | None = None
    unresolved_average_entry_price: Decimal | None = None
    policy_version: str = field(init=False, default=POLICY_VERSION)
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
    schema_version: str = field(init=False, default=_SCHEMA)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-fixed-2r-replay",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not isinstance(self.integrity_seal, str) or not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-fixed-2r-replay",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutRetestReplayError("replay integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        require_utc(self.end, field="replay end")
        self.entry_intent.validate()
        self.execution_costs.validate()
        if (
            self.schema_version != _SCHEMA
            or self.policy_version != POLICY_VERSION
            or self.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
            or self.decision_identity != self.entry_intent.decision_identity
            or self.execution_series.source_id != self.entry_intent.source_id
            or self.execution_series.symbol != self.entry_intent.symbol
            or self.execution_series.timeframe is not Timeframe.MINUTES_15
            or self.end <= self.decision_identity.observed_at
            or self.entry_request.symbol != self.entry_intent.symbol
            or self.entry_request.quantity != self.entry_intent.quantity
            or self.entry_request.created_at != self.entry_intent.authorized_at
            or self.entry_request.side
            is not (OrderSide.BUY if self.entry_intent.side is EntrySide.BUY else OrderSide.SELL)
        ):
            raise BreakoutRetestReplayError("replay identity or policy differs")
        if self.entry_fill is not None and (
            self.entry_fill.order_id != self.entry_request.order_id
            or self.entry_fill.quantity != self.entry_request.quantity
            or self.entry_fill.side is not self.entry_request.side
            or self.entry_fill.source_id != self.execution_series.source_id
            or self.entry_fill.execution_timeframe is not Timeframe.MINUTES_15
        ):
            raise BreakoutRetestReplayError("entry fill differs from request")
        if self.entry_fill is not None:
            validate_entry_fill_chronology(
                decision_at=self.decision_identity.observed_at,
                authorized_at=self.entry_request.created_at,
                latency=self.execution_costs.latency,
                eligible_at=self.entry_fill.eligible_at,
                fill_at=self.entry_fill.fill_at,
            )
            if self.entry_fill.fill_at != self.entry_fill.execution_candle_open_time:
                raise BreakoutRetestReplayError("entry fill is not at its execution open")
        if self.status is BreakoutRetestReplayStatus.ENTRY_UNFILLED:
            if (
                self.entry_unfilled is None
                or self.entry_unfilled.order != self.entry_request
                or self.entry_unfilled.eligible_at
                != self.entry_request.created_at + self.execution_costs.latency
                or self.entry_fill is not None
                or self.final_position is not None
                or self.exit_request is not None
                or self.realized_net_pnl is not None
            ):
                raise BreakoutRetestReplayError("unfilled entry has contradictory outcome")
        elif self.status is BreakoutRetestReplayStatus.ENTRY_FILL_INVALIDATED:
            if (
                self.entry_fill is None
                or self.entry_fill_invalidation is None
                or self.final_position is not None
                or self.exit_request is not None
                or self.realized_net_pnl is not None
            ):
                raise BreakoutRetestReplayError("invalidated entry has contradictory outcome")
            if _entry_fill_geometry_is_valid(self.entry_intent, self.entry_fill):
                raise BreakoutRetestReplayError("invalidated entry actually has valid geometry")
        elif self.status is BreakoutRetestReplayStatus.REPLAYED:
            if (
                self.entry_fill is None
                or self.entry_unfilled is not None
                or self.entry_fill_invalidation is not None
                or self.final_position is None
                or self.risk_distance is None
                or self.risk_distance <= 0
                or self.target_trigger_price is None
                or self.stop_basis != self.entry_intent.stop_basis
                or self.unresolved_quantity != abs(self.final_position.quantity)
                or self.unresolved_average_entry_price
                != (
                    None
                    if self.final_position.quantity == 0
                    else self.final_position.average_entry_price
                )
            ):
                raise BreakoutRetestReplayError("replayed position geometry is invalid")
            expected_distance, expected_target = fixed_2r_geometry(
                self.entry_intent.side, self.entry_fill.fill_price, self.stop_basis
            )
            if (
                self.risk_distance != expected_distance
                or self.target_trigger_price != expected_target
            ):
                raise BreakoutRetestReplayError("2R geometry differs from actual entry fill")
            if self.same_bar_stop_precedence != (self.stop_touched and self.target_touched):
                raise BreakoutRetestReplayError("same-bar precedence audit differs")
            if self.exit_cause is None:
                if (
                    self.exit_request is not None
                    or self.exit_fill is not None
                    or self.exit_trigger_at is not None
                ):
                    raise BreakoutRetestReplayError("untriggered position has an exit")
            elif (
                self.exit_request is None
                or self.exit_trigger_at is None
                or self.trigger_candle_open_at is None
                or (self.exit_cause is BreakoutRetestExitCause.PROTECTIVE_STOP) != self.stop_touched
                or self.exit_request.created_at != self.exit_trigger_at
                or self.exit_request.quantity != self.entry_fill.quantity
                or self.exit_request.side
                is not (
                    OrderSide.SELL if self.entry_intent.side is EntrySide.BUY else OrderSide.BUY
                )
                or (self.exit_fill is None) == (self.exit_unfilled is None)
            ):
                raise BreakoutRetestReplayError("exit trigger and execution differ")
            if self.exit_fill is not None and (
                self.exit_request is None
                or self.exit_trigger_at is None
                or self.exit_fill.order_id != self.exit_request.order_id
                or self.exit_fill.quantity != self.exit_request.quantity
                or self.exit_fill.execution_candle_open_time < self.exit_trigger_at
            ):
                raise BreakoutRetestReplayError("exit fill differs from causal request")
            if self.position_closed != (self.final_position.quantity == 0):
                raise BreakoutRetestReplayError("closure state differs from accounting")
            if self.position_closed:
                if (
                    self.exit_fill is None
                    or self.realized_gross_pnl != self.final_position.cumulative_gross_realized_pnl
                    or self.total_fees != self.final_position.cumulative_fees
                    or self.realized_net_pnl != self.final_position.cumulative_net_realized_pnl
                ):
                    raise BreakoutRetestReplayError("closed outcome differs from accounting")
            elif any(
                value is not None
                for value in (self.realized_gross_pnl, self.total_fees, self.realized_net_pnl)
            ):
                raise BreakoutRetestReplayError("open position cannot have realized outcome")
        else:
            raise BreakoutRetestReplayError("unknown replay status")


class BreakoutRetestFixed2RReplayRunner:
    """Use one exact historical candle snapshot for both triggers and simulator fills."""

    def __init__(self, source: ExecutionCandleSource) -> None:
        self._source = source

    def run(
        self,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        end: datetime,
    ) -> BreakoutRetestReplayResult:
        decision = deepcopy(decision)
        decision.validate()
        strategy = BreakoutRetestStrategy()
        if (
            decision.identity.strategy_id != strategy.strategy_id
            or decision.identity.strategy_version != strategy.strategy_version
        ):
            raise BreakoutRetestReplayError("entry is not Breakout + Retest V1")
        authorization = EntryOrderIntentAuthorizer().authorize(decision)
        if (
            authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
            or authorization.intent is None
        ):
            raise BreakoutRetestReplayError("journal entry has no authorized entry intent")
        intent = authorization.intent
        if (
            series.source_id != intent.source_id
            or series.symbol != intent.symbol
            or series.timeframe is not Timeframe.MINUTES_15
        ):
            raise BreakoutRetestReplayError("execution series differs from entry authority")
        end = require_utc(end, field="replay end")
        costs.validate()
        start = entry_source_start(
            decision_at=intent.observed_at,
            authorized_at=intent.authorized_at,
            requested_start=intent.observed_at,
            end=end,
        )
        candles = self._source.load(series, start=start, end=end)
        bound = _BoundExecutionCandleSource(series, start, end, candles)
        simulator = BacktestExecutionSimulator(bound)
        entry_request = MarketOrderRequest(
            f"breakout-retest-entry:{intent.identity}",
            intent.authorized_at,
            intent.symbol,
            OrderSide.BUY if intent.side is EntrySide.BUY else OrderSide.SELL,
            intent.quantity,
        )
        provenance = EntryIntentProvenance.from_authorized_intent(intent)
        entry_execution = simulator.run(series, costs, (entry_request,), start=start, end=end)
        if entry_execution.unfilled_count == 1:
            return BreakoutRetestReplayResult(
                decision_identity=deepcopy(decision.identity),
                execution_series=deepcopy(series),
                execution_costs=deepcopy(costs),
                end=end,
                entry_intent=provenance,
                entry_request=entry_request,
                status=BreakoutRetestReplayStatus.ENTRY_UNFILLED,
                entry_unfilled=entry_execution.unfilled_orders[0],
            )
        if entry_execution.filled_count != 1 or entry_execution.unfilled_count:
            raise BreakoutRetestReplayError("entry execution is inconsistent")
        entry_fill = entry_execution.fills[0]
        if not _entry_fill_geometry_is_valid(intent, entry_fill):
            return BreakoutRetestReplayResult(
                decision_identity=deepcopy(decision.identity),
                execution_series=deepcopy(series),
                execution_costs=deepcopy(costs),
                end=end,
                entry_intent=provenance,
                entry_request=entry_request,
                status=BreakoutRetestReplayStatus.ENTRY_FILL_INVALIDATED,
                entry_fill=entry_fill,
                entry_fill_invalidation=_entry_fill_invalidation_audit(intent, entry_fill),
            )
        distance, target = fixed_2r_geometry(intent.side, entry_fill.fill_price, intent.stop_basis)
        accounting = PositionAccountingEngine()
        entry_batch = accounting.process(AccountingFillBatch(entry_fill.fill_at, (entry_fill,)))
        events = list(entry_batch.events)
        by_close = {candle.close_time: candle for candle in bound.candles}
        if len(by_close) != len(candles):
            raise BreakoutRetestReplayError("execution candles have duplicate close times")
        eligible_candles = tuple(
            candle for candle in bound.candles if entry_fill.fill_at < candle.close_time < end
        )
        candle_cursor = 0
        previous: datetime | None = None
        trigger_candle_open_at: datetime | None = None
        trigger_at: datetime | None = None
        stop_touched = target_touched = False
        cause: BreakoutRetestExitCause | None = None
        exit_request: MarketOrderRequest | None = None
        exit_fill: MarketFill | None = None
        exit_unfilled: UnfilledOrder | None = None
        for sealed in contexts:
            sealed.validate()
            context = sealed.context
            if (
                context.source_id != decision.identity.source_id
                or context.symbol != intent.symbol
                or context.observed_at <= decision.identity.observed_at
                or context.observed_at >= end
                or (previous is not None and context.observed_at <= previous)
            ):
                raise BreakoutRetestReplayError("post-entry context chronology or identity differs")
            previous = context.observed_at
            if context.observed_at <= entry_fill.fill_at:
                continue
            candle = context.candle_15m
            if candle_cursor >= len(eligible_candles) or candle != eligible_candles[candle_cursor]:
                raise BreakoutRetestReplayError("sealed contexts omit or reorder execution candles")
            candle_cursor += 1
            if by_close.get(candle.close_time) != candle:
                raise BreakoutRetestReplayError("sealed context differs from execution candle")
            stop_touched, target_touched = fixed_2r_touches(
                intent.side, candle, intent.stop_basis, target
            )
            if not stop_touched and not target_touched:
                continue
            cause = (
                BreakoutRetestExitCause.PROTECTIVE_STOP
                if stop_touched
                else BreakoutRetestExitCause.FIXED_2R_TARGET
            )
            trigger_candle_open_at = candle.open_time
            trigger_at = context.observed_at
            exit_request = MarketOrderRequest(
                f"breakout-retest-exit:{intent.identity}",
                trigger_at,
                intent.symbol,
                OrderSide.SELL if intent.side is EntrySide.BUY else OrderSide.BUY,
                entry_fill.quantity,
            )
            exit_execution = simulator.run(series, costs, (exit_request,), start=start, end=end)
            if exit_execution.filled_count == 1 and not exit_execution.unfilled_count:
                exit_fill = exit_execution.fills[0]
                if exit_fill.execution_candle_open_time < candle.close_time:
                    raise BreakoutRetestReplayError("exit filled before completed trigger candle")
                events.extend(
                    accounting.process(AccountingFillBatch(exit_fill.fill_at, (exit_fill,))).events
                )
            elif exit_execution.unfilled_count == 1 and not exit_execution.filled_count:
                exit_unfilled = exit_execution.unfilled_orders[0]
            else:
                raise BreakoutRetestReplayError("exit execution is inconsistent")
            break
        if cause is None and candle_cursor != len(eligible_candles):
            raise BreakoutRetestReplayError("untriggered replay omits eligible closed candles")
        final = accounting.snapshot(intent.symbol, end)
        closed = final.quantity == 0
        return BreakoutRetestReplayResult(
            decision_identity=deepcopy(decision.identity),
            execution_series=deepcopy(series),
            execution_costs=deepcopy(costs),
            end=end,
            entry_intent=provenance,
            entry_request=entry_request,
            status=BreakoutRetestReplayStatus.REPLAYED,
            entry_fill=entry_fill,
            stop_basis=intent.stop_basis,
            risk_distance=distance,
            target_trigger_price=target,
            trigger_candle_open_at=trigger_candle_open_at,
            exit_trigger_at=trigger_at,
            stop_touched=stop_touched if cause else False,
            target_touched=target_touched if cause else False,
            same_bar_stop_precedence=bool(cause and stop_touched and target_touched),
            exit_cause=cause,
            exit_request=exit_request,
            exit_fill=exit_fill,
            exit_unfilled=exit_unfilled,
            accounting_events=tuple(events),
            final_position=final,
            position_closed=closed,
            realized_gross_pnl=final.cumulative_gross_realized_pnl if closed else None,
            total_fees=final.cumulative_fees if closed else None,
            realized_net_pnl=final.cumulative_net_realized_pnl if closed else None,
            unresolved_quantity=abs(final.quantity),
            unresolved_average_entry_price=None if closed else final.average_entry_price,
        )
