"""Single-position midpoint exit replay using existing execution/accounting authorities."""

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
    EntryIntentProvenance,
    HistoricalStrategyContextObservation,
    _BoundExecutionCandleSource,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _integrity_seal
from adaptive_trading_bot.strategies import CandidateAction, RangeMeanReversionStrategy
from adaptive_trading_bot.strategies.range_context import RangeStatus
from adaptive_trading_bot.strategies.range_mean_reversion import (
    RangeMeanReversionRuleAudit,
    RangeMeanReversionSide,
)

POLICY_VERSION = "RANGE_MEAN_REVERSION_ENTRY_MIDPOINT_V1"
_SCHEMA = "range-mean-reversion-midpoint-replay-v2"
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class RangeMeanReversionReplayError(ValueError):
    """Historical context, authorization, geometry or sealed result is inconsistent."""


class RangeMeanReversionReplayStatus(StrEnum):
    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    ENTRY_FILL_INVALIDATED = "ENTRY_FILL_INVALIDATED"
    REPLAYED = "REPLAYED"


class RangeEntryInvalidationReason(StrEnum):
    STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL = "STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL"
    MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL = "MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL"


class RangeMeanReversionExitCause(StrEnum):
    PROTECTIVE_STOP = "PROTECTIVE_STOP"
    RANGE_MIDPOINT_TARGET = "RANGE_MIDPOINT_TARGET"


def entry_geometry_reasons(
    side: EntrySide, fill_price: Decimal, stop_basis: Decimal, midpoint: Decimal
) -> tuple[RangeEntryInvalidationReason, ...]:
    """Strict post-fill inequalities, with deterministic reason ordering."""
    if not isinstance(side, EntrySide) or any(
        not isinstance(value, Decimal) or not value.is_finite() or value <= 0
        for value in (fill_price, stop_basis, midpoint)
    ):
        raise RangeMeanReversionReplayError("midpoint geometry inputs are invalid")
    stop_bad = stop_basis >= fill_price if side is EntrySide.BUY else stop_basis <= fill_price
    midpoint_bad = midpoint <= fill_price if side is EntrySide.BUY else midpoint >= fill_price
    reasons: list[RangeEntryInvalidationReason] = []
    if stop_bad:
        reasons.append(RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL)
    if midpoint_bad:
        reasons.append(RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL)
    return tuple(reasons)


def midpoint_touches(
    side: EntrySide, candle: Candle, stop_basis: Decimal, midpoint: Decimal
) -> tuple[bool, bool]:
    """Evaluate only the completed 15m candle; stop wins if both are true."""
    if side is EntrySide.BUY:
        return candle.low <= stop_basis, candle.high >= midpoint
    if side is EntrySide.SELL:
        return candle.high >= stop_basis, candle.low <= midpoint
    raise RangeMeanReversionReplayError("midpoint trigger side is invalid")


@dataclass(frozen=True, slots=True)
class RangeEntryInvalidationAudit:
    reasons: tuple[RangeEntryInvalidationReason, ...]
    actual_fill: MarketFill
    stop_basis: Decimal
    sealed_midpoint: Decimal


def _entry_audit(
    side: EntrySide, fill: MarketFill, stop: Decimal, midpoint: Decimal
) -> RangeEntryInvalidationAudit:
    return RangeEntryInvalidationAudit(
        entry_geometry_reasons(side, fill.fill_price, stop, midpoint), fill, stop, midpoint
    )


def _entry_rule_audit(
    decision: DecisionJournalEntry[object, object],
) -> RangeMeanReversionRuleAudit:
    evaluation = decision.strategy_evaluation
    audit = evaluation.rule_audit
    if not isinstance(audit, RangeMeanReversionRuleAudit):
        raise RangeMeanReversionReplayError("entry has no Range Mean Reversion rule audit")
    expected_side = (
        RangeMeanReversionSide.LONG
        if evaluation.action is CandidateAction.LONG_CANDIDATE
        else RangeMeanReversionSide.SHORT
    )
    if (
        audit.range_status is not RangeStatus.VALID_RANGE
        or audit.selected_side is not expected_side
        or audit.lower_boundary is None
        or audit.upper_boundary is None
        or audit.midpoint is None
        or audit.invalidation_reference != evaluation.invalidation_reference_price
        or not audit.lower_boundary < audit.midpoint < audit.upper_boundary
        or audit.range_width != audit.upper_boundary - audit.lower_boundary
    ):
        raise RangeMeanReversionReplayError("sealed entry range audit is inconsistent")
    return audit


@dataclass(frozen=True, slots=True)
class RangeMeanReversionReplayResult:
    decision_identity: DecisionIdentity
    execution_series: ExecutionSeries
    execution_costs: ExecutionCostConfiguration
    end: datetime
    entry_intent: EntryIntentProvenance
    entry_rule_audit: RangeMeanReversionRuleAudit
    entry_request: MarketOrderRequest
    status: RangeMeanReversionReplayStatus
    entry_fill: MarketFill | None = None
    entry_unfilled: UnfilledOrder | None = None
    entry_fill_invalidation: RangeEntryInvalidationAudit | None = None
    stop_basis: Decimal | None = None
    target_distance: Decimal | None = None
    stop_distance: Decimal | None = None
    geometric_reward_risk: Decimal | None = None
    trigger_candle_open_at: datetime | None = None
    exit_trigger_at: datetime | None = None
    stop_touched: bool = False
    target_touched: bool = False
    same_bar_stop_precedence: bool = False
    exit_cause: RangeMeanReversionExitCause | None = None
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
                "range-midpoint-replay", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not isinstance(self.integrity_seal, str) or not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-midpoint-replay", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeMeanReversionReplayError("replay integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        require_utc(self.end, field="replay end")
        self.entry_intent.validate()
        self.execution_costs.validate()
        audit = self.entry_rule_audit
        midpoint = audit.midpoint
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
            or not self.entry_intent.authorized_for_execution
            or self.entry_request.side
            is not (OrderSide.BUY if self.entry_intent.side is EntrySide.BUY else OrderSide.SELL)
            or audit.range_status is not RangeStatus.VALID_RANGE
            or audit.lower_boundary is None
            or audit.upper_boundary is None
            or midpoint is None
            or not audit.lower_boundary < midpoint < audit.upper_boundary
            or audit.range_width != audit.upper_boundary - audit.lower_boundary
            or audit.invalidation_reference != self.entry_intent.stop_basis
            or audit.selected_side
            is not (
                RangeMeanReversionSide.LONG
                if self.entry_intent.side is EntrySide.BUY
                else RangeMeanReversionSide.SHORT
            )
        ):
            raise RangeMeanReversionReplayError("replay identity or range policy differs")
        if self.entry_fill is not None and (
            self.entry_fill.order_id != self.entry_request.order_id
            or self.entry_fill.symbol != self.entry_request.symbol
            or self.entry_fill.created_at != self.entry_request.created_at
            or self.entry_fill.quantity != self.entry_request.quantity
            or self.entry_fill.side is not self.entry_request.side
            or self.entry_fill.source_id != self.execution_series.source_id
            or self.entry_fill.execution_timeframe is not Timeframe.MINUTES_15
            or self.entry_fill.spread_bps != self.execution_costs.spread_bps
            or self.entry_fill.slippage_bps != self.execution_costs.slippage_bps
            or self.entry_fill.fee_bps != self.execution_costs.fee_bps
        ):
            raise RangeMeanReversionReplayError("entry fill differs from request")
        if self.entry_fill is not None:
            validate_entry_fill_chronology(
                decision_at=self.decision_identity.observed_at,
                authorized_at=self.entry_request.created_at,
                latency=self.execution_costs.latency,
                eligible_at=self.entry_fill.eligible_at,
                fill_at=self.entry_fill.fill_at,
            )
            if self.entry_fill.fill_at != self.entry_fill.execution_candle_open_time:
                raise RangeMeanReversionReplayError("entry fill is not at its execution open")
        if self.status is RangeMeanReversionReplayStatus.ENTRY_UNFILLED:
            if (
                self.entry_unfilled is None
                or self.entry_unfilled.order != self.entry_request
                or self.entry_unfilled.eligible_at
                != self.entry_request.created_at + self.execution_costs.latency
                or self.entry_fill is not None
                or self.entry_fill_invalidation is not None
                or self.final_position is not None
                or self.exit_request is not None
                or self.exit_fill is not None
                or self.exit_unfilled is not None
                or self.accounting_events
                or self.position_closed
                or self.realized_net_pnl is not None
            ):
                raise RangeMeanReversionReplayError("unfilled entry has contradictory outcome")
        elif self.status is RangeMeanReversionReplayStatus.ENTRY_FILL_INVALIDATED:
            if (
                self.entry_fill is None
                or self.entry_fill_invalidation is None
                or self.entry_unfilled is not None
                or self.final_position is not None
                or self.exit_request is not None
                or self.exit_fill is not None
                or self.exit_unfilled is not None
                or self.accounting_events
                or self.position_closed
                or self.realized_net_pnl is not None
            ):
                raise RangeMeanReversionReplayError("invalidated entry has contradictory outcome")
            if (
                self.entry_fill_invalidation
                != _entry_audit(
                    self.entry_intent.side, self.entry_fill, self.entry_intent.stop_basis, midpoint
                )
                or not self.entry_fill_invalidation.reasons
            ):
                raise RangeMeanReversionReplayError("entry invalidation audit differs")
        elif self.status is RangeMeanReversionReplayStatus.REPLAYED:
            if (
                self.entry_fill is None
                or self.entry_unfilled is not None
                or self.entry_fill_invalidation is not None
                or self.final_position is None
                or self.final_position.symbol != self.entry_intent.symbol
                or self.final_position.observed_at != self.end
                or self.stop_basis != self.entry_intent.stop_basis
                or self.unresolved_quantity != abs(self.final_position.quantity)
                or self.unresolved_average_entry_price
                != (
                    None
                    if self.final_position.quantity == 0
                    else self.final_position.average_entry_price
                )
            ):
                raise RangeMeanReversionReplayError("replayed position state is invalid")
            if entry_geometry_reasons(
                self.entry_intent.side, self.entry_fill.fill_price, self.stop_basis, midpoint
            ):
                raise RangeMeanReversionReplayError("replayed entry has invalid geometry")
            expected_stop = abs(self.entry_fill.fill_price - self.stop_basis)
            expected_target = abs(midpoint - self.entry_fill.fill_price)
            with localcontext(_DECIMAL_CONTEXT):
                expected_ratio = expected_target / expected_stop
            if (
                self.stop_distance != expected_stop
                or self.target_distance != expected_target
                or self.geometric_reward_risk != expected_ratio
            ):
                raise RangeMeanReversionReplayError("descriptive geometry differs from actual fill")
            if self.same_bar_stop_precedence != (self.stop_touched and self.target_touched):
                raise RangeMeanReversionReplayError("same-bar precedence audit differs")
            if self.exit_cause is None:
                if (
                    self.exit_request is not None
                    or self.exit_fill is not None
                    or self.exit_unfilled is not None
                    or self.exit_trigger_at is not None
                    or self.trigger_candle_open_at is not None
                    or self.stop_touched
                    or self.target_touched
                ):
                    raise RangeMeanReversionReplayError("untriggered position has an exit")
            elif (
                self.exit_request is None
                or self.exit_trigger_at is None
                or self.trigger_candle_open_at is None
                or self.trigger_candle_open_at + Timeframe.MINUTES_15.duration
                != self.exit_trigger_at
                or (self.exit_cause is RangeMeanReversionExitCause.PROTECTIVE_STOP)
                != self.stop_touched
                or (
                    self.exit_cause is RangeMeanReversionExitCause.RANGE_MIDPOINT_TARGET
                    and not self.target_touched
                )
                or self.exit_request.created_at != self.exit_trigger_at
                or self.exit_request.quantity != self.entry_fill.quantity
                or self.exit_request.side
                is not (
                    OrderSide.SELL if self.entry_intent.side is EntrySide.BUY else OrderSide.BUY
                )
                or (self.exit_fill is None) == (self.exit_unfilled is None)
            ):
                raise RangeMeanReversionReplayError("exit trigger and execution differ")
            if self.exit_fill is not None and (
                self.exit_request is None
                or self.exit_trigger_at is None
                or self.exit_fill.order_id != self.exit_request.order_id
                or self.exit_fill.symbol != self.exit_request.symbol
                or self.exit_fill.created_at != self.exit_request.created_at
                or self.exit_fill.quantity != self.exit_request.quantity
                or self.exit_fill.side is not self.exit_request.side
                or self.exit_fill.source_id != self.execution_series.source_id
                or self.exit_fill.execution_timeframe is not Timeframe.MINUTES_15
                or self.exit_fill.spread_bps != self.execution_costs.spread_bps
                or self.exit_fill.slippage_bps != self.execution_costs.slippage_bps
                or self.exit_fill.fee_bps != self.execution_costs.fee_bps
                or self.exit_fill.execution_candle_open_time < self.exit_trigger_at
            ):
                raise RangeMeanReversionReplayError("exit fill differs from causal request")
            expected_fill_ids = (
                (self.entry_fill.order_id, self.exit_fill.order_id)
                if self.exit_fill is not None
                else (self.entry_fill.order_id,)
            )
            if tuple(event.order_id for event in self.accounting_events) != expected_fill_ids:
                raise RangeMeanReversionReplayError("accounting event provenance differs")
            last_event = self.accounting_events[-1]
            if (
                last_event.position_quantity_after != self.final_position.quantity
                or last_event.average_entry_after != self.final_position.average_entry_price
                or last_event.cumulative_gross_realized_pnl
                != self.final_position.cumulative_gross_realized_pnl
                or last_event.cumulative_fees != self.final_position.cumulative_fees
                or last_event.cumulative_net_realized_pnl
                != self.final_position.cumulative_net_realized_pnl
            ):
                raise RangeMeanReversionReplayError("accounting snapshot differs from events")
            if self.position_closed != (self.final_position.quantity == 0):
                raise RangeMeanReversionReplayError("closure state differs from accounting")
            if self.position_closed:
                if (
                    self.exit_fill is None
                    or self.realized_gross_pnl != self.final_position.cumulative_gross_realized_pnl
                    or self.total_fees != self.final_position.cumulative_fees
                    or self.realized_net_pnl != self.final_position.cumulative_net_realized_pnl
                ):
                    raise RangeMeanReversionReplayError("closed outcome differs from accounting")
            elif any(
                x is not None
                for x in (self.realized_gross_pnl, self.total_fees, self.realized_net_pnl)
            ):
                raise RangeMeanReversionReplayError("open position cannot have realized outcome")
        else:
            raise RangeMeanReversionReplayError("unknown replay status")


class RangeMeanReversionMidpointReplayRunner:
    """Bind sealed contexts and execution to one exact historical candle snapshot."""

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
    ) -> RangeMeanReversionReplayResult:
        decision = deepcopy(decision)
        decision.validate()
        strategy = RangeMeanReversionStrategy()
        if (
            decision.identity.strategy_id != strategy.strategy_id
            or decision.identity.strategy_version != strategy.strategy_version
        ):
            raise RangeMeanReversionReplayError("entry is not Range Mean Reversion V1")
        audit = _entry_rule_audit(decision)
        authorization = EntryOrderIntentAuthorizer().authorize(decision)
        if (
            authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
            or authorization.intent is None
        ):
            raise RangeMeanReversionReplayError("journal entry has no authorized entry intent")
        intent = authorization.intent
        if (
            series.source_id != intent.source_id
            or series.symbol != intent.symbol
            or series.timeframe is not Timeframe.MINUTES_15
        ):
            raise RangeMeanReversionReplayError("execution series differs from entry authority")
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
        previous_context_at: datetime | None = None
        for sealed in contexts:
            sealed.validate()
            context = sealed.context
            if (
                context.source_id != decision.identity.source_id
                or context.symbol != intent.symbol
                or context.observed_at <= decision.identity.observed_at
                or context.observed_at >= end
                or (previous_context_at is not None and context.observed_at <= previous_context_at)
            ):
                raise RangeMeanReversionReplayError(
                    "post-entry context chronology or identity differs"
                )
            previous_context_at = context.observed_at
        simulator = BacktestExecutionSimulator(bound)
        entry_request = MarketOrderRequest(
            f"range-midpoint-entry:{intent.identity}",
            intent.authorized_at,
            intent.symbol,
            OrderSide.BUY if intent.side is EntrySide.BUY else OrderSide.SELL,
            intent.quantity,
        )
        provenance = EntryIntentProvenance.from_authorized_intent(intent)
        entry_execution = simulator.run(series, costs, (entry_request,), start=start, end=end)
        if entry_execution.unfilled_count == 1:
            return RangeMeanReversionReplayResult(
                decision_identity=deepcopy(decision.identity),
                execution_series=deepcopy(series),
                execution_costs=deepcopy(costs),
                end=end,
                entry_intent=provenance,
                entry_rule_audit=deepcopy(audit),
                entry_request=entry_request,
                status=RangeMeanReversionReplayStatus.ENTRY_UNFILLED,
                entry_unfilled=entry_execution.unfilled_orders[0],
            )
        if entry_execution.filled_count != 1 or entry_execution.unfilled_count:
            raise RangeMeanReversionReplayError("entry execution is inconsistent")
        entry_fill = entry_execution.fills[0]
        assert audit.midpoint is not None
        invalidation = _entry_audit(intent.side, entry_fill, intent.stop_basis, audit.midpoint)
        if invalidation.reasons:
            return RangeMeanReversionReplayResult(
                decision_identity=deepcopy(decision.identity),
                execution_series=deepcopy(series),
                execution_costs=deepcopy(costs),
                end=end,
                entry_intent=provenance,
                entry_rule_audit=deepcopy(audit),
                entry_request=entry_request,
                status=RangeMeanReversionReplayStatus.ENTRY_FILL_INVALIDATED,
                entry_fill=entry_fill,
                entry_fill_invalidation=invalidation,
            )
        stop_distance = abs(entry_fill.fill_price - intent.stop_basis)
        target_distance = abs(audit.midpoint - entry_fill.fill_price)
        accounting = PositionAccountingEngine()
        entry_batch = accounting.process(AccountingFillBatch(entry_fill.fill_at, (entry_fill,)))
        events = list(entry_batch.events)
        by_close = {candle.close_time: candle for candle in bound.candles}
        if len(by_close) != len(candles):
            raise RangeMeanReversionReplayError("execution candles have duplicate close times")
        eligible = tuple(c for c in bound.candles if entry_fill.fill_at < c.close_time < end)
        cursor = 0
        previous: datetime | None = None
        trigger_open: datetime | None = None
        trigger_at: datetime | None = None
        stop_touched = target_touched = False
        cause: RangeMeanReversionExitCause | None = None
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
                raise RangeMeanReversionReplayError(
                    "post-entry context chronology or identity differs"
                )
            previous = context.observed_at
            if context.observed_at <= entry_fill.fill_at:
                continue
            candle = context.candle_15m
            if cursor >= len(eligible) or candle != eligible[cursor]:
                raise RangeMeanReversionReplayError(
                    "sealed contexts omit or reorder execution candles"
                )
            cursor += 1
            if by_close.get(candle.close_time) != candle:
                raise RangeMeanReversionReplayError("sealed context differs from execution candle")
            stop_touched, target_touched = midpoint_touches(
                intent.side, candle, intent.stop_basis, audit.midpoint
            )
            if not stop_touched and not target_touched:
                continue
            cause = (
                RangeMeanReversionExitCause.PROTECTIVE_STOP
                if stop_touched
                else RangeMeanReversionExitCause.RANGE_MIDPOINT_TARGET
            )
            trigger_open = candle.open_time
            trigger_at = context.observed_at
            exit_request = MarketOrderRequest(
                f"range-midpoint-exit:{intent.identity}",
                trigger_at,
                intent.symbol,
                OrderSide.SELL if intent.side is EntrySide.BUY else OrderSide.BUY,
                entry_fill.quantity,
            )
            exit_execution = simulator.run(series, costs, (exit_request,), start=start, end=end)
            if exit_execution.filled_count == 1 and not exit_execution.unfilled_count:
                exit_fill = exit_execution.fills[0]
                if exit_fill.execution_candle_open_time < candle.close_time:
                    raise RangeMeanReversionReplayError(
                        "exit filled before completed trigger candle"
                    )
                events.extend(
                    accounting.process(AccountingFillBatch(exit_fill.fill_at, (exit_fill,))).events
                )
            elif exit_execution.unfilled_count == 1 and not exit_execution.filled_count:
                exit_unfilled = exit_execution.unfilled_orders[0]
            else:
                raise RangeMeanReversionReplayError("exit execution is inconsistent")
            break
        if cause is None and cursor != len(eligible):
            raise RangeMeanReversionReplayError("untriggered replay omits eligible closed candles")
        final = accounting.snapshot(intent.symbol, end)
        closed = final.quantity == 0
        with localcontext(_DECIMAL_CONTEXT):
            geometric_reward_risk = target_distance / stop_distance
        return RangeMeanReversionReplayResult(
            decision_identity=deepcopy(decision.identity),
            execution_series=deepcopy(series),
            execution_costs=deepcopy(costs),
            end=end,
            entry_intent=provenance,
            entry_rule_audit=deepcopy(audit),
            entry_request=entry_request,
            status=RangeMeanReversionReplayStatus.REPLAYED,
            entry_fill=entry_fill,
            stop_basis=intent.stop_basis,
            stop_distance=stop_distance,
            target_distance=target_distance,
            geometric_reward_risk=geometric_reward_risk,
            trigger_candle_open_at=trigger_open,
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
