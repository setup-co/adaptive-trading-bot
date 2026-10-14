"""Prospective, offline all-fill economics over the three existing research replays.

This is a separate schema. Legacy invalidated-entry results remain unchanged; a
filled entry is accounted here before one causal recovery is authorized.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
from adaptive_trading_bot.domain.entry_chronology import entry_source_start
from adaptive_trading_bot.execution import price_simulated_market_fill
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.research.breakout_retest_replay import (
    BreakoutRetestFixed2RReplayRunner,
    BreakoutRetestReplayResult,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryFillInvalidationAudit,
    EntryIntentProvenance,
    ExitReplayBranchPolicy,
    HistoricalExitBranchResult,
    HistoricalStrategyContextObservation,
    PairedTrendPullbackExitReplayResult,
    PairedTrendPullbackExitReplayRunner,
    _BoundExecutionCandleSource,
)
from adaptive_trading_bot.research.range_mean_reversion_replay import (
    RangeEntryInvalidationAudit,
    RangeMeanReversionMidpointReplayRunner,
    RangeMeanReversionReplayResult,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _integrity_seal
from adaptive_trading_bot.strategies import (
    BreakoutRetestStrategy,
    CandidateAction,
    RangeMeanReversionStrategy,
    TrendPullbackStrategy,
)

POLICY_VERSION = "RESEARCH_ALL_FILL_ECONOMICS_V1"
SCHEMA_VERSION = "research-all-fill-economics-v1"
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class AllFillReplayError(ValueError):
    """An authority, provenance, or all-fill invariant failed closed."""


class AllFillFamily(StrEnum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    RANGE_MEAN_REVERSION = "RANGE_MEAN_REVERSION"


class AllFillStatus(StrEnum):
    NO_SIGNAL = "NO_SIGNAL"
    RISK_BLOCKED = "RISK_BLOCKED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    ORDINARY_CLOSED = "ORDINARY_CLOSED"
    ORDINARY_OPEN = "ORDINARY_OPEN"
    RECOVERY_CLOSED = "RECOVERY_CLOSED"
    RECOVERY_OPEN = "RECOVERY_OPEN"


class AllFillScenario(StrEnum):
    BASE = "BASE_2_2_10_ZERO_LATENCY"
    COMBINED_STRESS = "COMBINED_STRESS_4_4_20_ONE_SECOND"


CellKey = tuple[str, AllFillFamily, AllFillScenario, str, str, str, str | None]


def _scenario(costs: ExecutionCostConfiguration) -> AllFillScenario:
    costs.validate()
    facts = (costs.spread_bps, costs.slippage_bps, costs.fee_bps, costs.latency)
    if facts == (Decimal(2), Decimal(2), Decimal(10), timedelta()):
        return AllFillScenario.BASE
    if facts == (Decimal(4), Decimal(4), Decimal(20), timedelta(seconds=1)):
        return AllFillScenario.COMBINED_STRESS
    raise AllFillReplayError("costs are outside the frozen two-scenario protocol")


@dataclass(frozen=True, slots=True)
class RecoveryAuthorization:
    """One full-close request derived from an already-accounted entry position."""

    decision_identity: DecisionIdentity
    entry_intent: EntryIntentProvenance
    entry_order_id: str
    detected_at: datetime
    reasons: tuple[str, ...]
    sealed_midpoint: Decimal | None
    request: MarketOrderRequest
    accounted_entry_quantity: Decimal
    schema_version: str = field(init=False, default=SCHEMA_VERSION)

    def validate(self) -> None:
        self.entry_intent.validate()
        require_utc(self.detected_at, field="recovery detection")
        expected_side = OrderSide.SELL if self.entry_intent.side.value == "BUY" else OrderSide.BUY
        if (
            self.schema_version != SCHEMA_VERSION
            or self.decision_identity != self.entry_intent.decision_identity
            or not self.reasons
            or not self.entry_order_id
            or (
                self.sealed_midpoint is not None
                and (not self.sealed_midpoint.is_finite() or self.sealed_midpoint <= 0)
            )
            or (
                "MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL" in self.reasons
                and self.sealed_midpoint is None
            )
            or self.request.side is not expected_side
            or self.request.symbol != self.entry_intent.symbol
            or self.request.quantity != self.accounted_entry_quantity
            or self.accounted_entry_quantity != self.entry_intent.quantity
            or self.request.created_at != self.detected_at
            or self.request.order_id != f"all-fill-recovery:{self.decision_identity}"
        ):
            raise AllFillReplayError("recovery authorization contradicts accounted entry")


@dataclass(frozen=True, slots=True)
class AllFillBranch:
    """One accounted branch, never a portfolio or runtime position."""

    label: str
    status: AllFillStatus
    entry_fill: MarketFill
    recovery: RecoveryAuthorization | None
    exit_request: MarketOrderRequest | None
    exit_fill: MarketFill | None
    exit_unfilled: UnfilledOrder | None
    accounting_events: tuple[FillAccountingEvent, ...]
    final_position: PositionSnapshot
    approved_risk_quote: Decimal
    closed_net_r: Decimal | None
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-all-fill-branch", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-all-fill-branch", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise AllFillReplayError("all-fill branch integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.approved_risk_quote <= 0:
            raise AllFillReplayError("all-fill branch schema or risk is invalid")
        if self.status not in (
            AllFillStatus.ORDINARY_CLOSED,
            AllFillStatus.ORDINARY_OPEN,
            AllFillStatus.RECOVERY_CLOSED,
            AllFillStatus.RECOVERY_OPEN,
        ):
            raise AllFillReplayError("accounted branch has invalid terminal status")
        if self.recovery is not None:
            self.recovery.validate()
            if self.status not in (AllFillStatus.RECOVERY_CLOSED, AllFillStatus.RECOVERY_OPEN):
                raise AllFillReplayError("recovery has ordinary status")
            if self.exit_request != self.recovery.request:
                raise AllFillReplayError("recovery request differs from authorization")
            if self.recovery.detected_at != self.entry_fill.fill_at:
                raise AllFillReplayError("recovery must follow the accounted entry fill")
            if self.recovery.entry_order_id != self.entry_fill.order_id:
                raise AllFillReplayError("recovery entry order differs from accounted fill")
            if self.exit_fill is not None and (
                self.exit_fill.execution_candle_open_time <= self.recovery.detected_at
                or self.exit_fill.order_id != self.recovery.request.order_id
            ):
                raise AllFillReplayError("recovery filled at a noncausal open")
        elif self.status in (AllFillStatus.RECOVERY_CLOSED, AllFillStatus.RECOVERY_OPEN):
            raise AllFillReplayError("recovery status lacks authorization")
        if self.exit_fill is not None and self.exit_unfilled is not None:
            raise AllFillReplayError("exit cannot be both filled and unfilled")
        if self.exit_fill is not None and self.exit_request is None:
            raise AllFillReplayError("exit fill lacks request")
        if self.exit_unfilled is not None and self.exit_unfilled.order != self.exit_request:
            raise AllFillReplayError("exit unfilled differs from request")
        if self.exit_request is not None and self.exit_fill is None and self.exit_unfilled is None:
            raise AllFillReplayError("exit request has no outcome")
        if self.exit_fill is not None and (
            self.exit_fill.symbol != self.entry_fill.symbol
            or self.exit_fill.quantity != self.entry_fill.quantity
            or self.exit_fill.side is self.entry_fill.side
        ):
            raise AllFillReplayError("exit is not an opposite-side full close")
        engine = PositionAccountingEngine()
        events = list(
            engine.process(AccountingFillBatch(self.entry_fill.fill_at, (self.entry_fill,))).events
        )
        if self.exit_fill is not None:
            events.extend(
                engine.process(
                    AccountingFillBatch(self.exit_fill.fill_at, (self.exit_fill,))
                ).events
            )
        if tuple(events) != self.accounting_events:
            raise AllFillReplayError("branch events differ from accounting authority")
        if (
            engine.snapshot(self.entry_fill.symbol, observed_at=self.final_position.observed_at)
            != self.final_position
        ):
            raise AllFillReplayError("branch final position differs from accounting authority")
        closed = self.final_position.quantity == 0
        if closed != (
            self.status in (AllFillStatus.ORDINARY_CLOSED, AllFillStatus.RECOVERY_CLOSED)
        ):
            raise AllFillReplayError("branch status contradicts accounted position")
        with localcontext(_CONTEXT):
            expected_r = (
                self.final_position.cumulative_net_realized_pnl / self.approved_risk_quote
                if closed
                else None
            )
        if self.closed_net_r != expected_r:
            raise AllFillReplayError("closed R differs from authoritative net accounting")


@dataclass(frozen=True, slots=True)
class AllFillReplayResult:
    decision: DecisionJournalEntry[object, object]
    family: AllFillFamily
    series: ExecutionSeries
    costs: ExecutionCostConfiguration
    scenario: AllFillScenario
    start: datetime
    end: datetime
    status: AllFillStatus
    entry_intent: EntryIntentProvenance | None
    entry_unfilled: UnfilledOrder | None
    branches: tuple[AllFillBranch, ...]
    legacy_replay: (
        PairedTrendPullbackExitReplayResult
        | BreakoutRetestReplayResult
        | RangeMeanReversionReplayResult
        | None
    )
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    policy_version: str = field(init=False, default=POLICY_VERSION)
    is_trading_authority: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-all-fill-result", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-all-fill-result", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise AllFillReplayError("all-fill result integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.decision.validate()
        require_utc(self.start, field="all-fill start")
        require_utc(self.end, field="all-fill end")
        self.costs.validate()
        if (
            self.schema_version != SCHEMA_VERSION
            or self.policy_version != POLICY_VERSION
            or self.is_trading_authority
            or self.start >= self.end
            or self.series.source_id != self.decision.identity.source_id
            or self.series.symbol != self.decision.identity.symbol
            or self.series.timeframe is not Timeframe.MINUTES_15
            or not self.start <= self.decision.identity.observed_at < self.end
            or self.family is not _family(self.decision)
            or self.scenario is not _scenario(self.costs)
        ):
            raise AllFillReplayError("all-fill identity or range differs from authority")
        authorization = EntryOrderIntentAuthorizer().authorize(self.decision)
        if authorization.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT:
            raise AllFillReplayError("decision chain is inconsistent")
        if authorization.status is OrderIntentAuthorizationStatus.NO_INTENT:
            if (
                self.entry_intent is not None
                or self.legacy_replay is not None
                or self.branches
                or self.entry_unfilled is not None
            ):
                raise AllFillReplayError("non-admitted decision has simulated evidence")
            if self.status is not _non_admitted_status(self.decision):
                raise AllFillReplayError("non-admitted terminal status contradicts decision")
            return
        if authorization.intent is None or self.entry_intent is None:
            raise AllFillReplayError("authorized decision lacks entry provenance")
        if self.entry_intent != EntryIntentProvenance.from_authorized_intent(authorization.intent):
            raise AllFillReplayError("entry provenance differs from authorizer")
        if self.legacy_replay is None:
            raise AllFillReplayError("authorized decision lacks legacy replay provenance")
        self.legacy_replay.validate()
        if self.legacy_replay.entry_intent != self.entry_intent:
            raise AllFillReplayError("legacy replay entry provenance mismatch")
        if self.entry_unfilled is not None:
            if (
                self.status is not AllFillStatus.ENTRY_UNFILLED
                or self.branches
                or self.entry_unfilled != self.legacy_replay.entry_unfilled
            ):
                raise AllFillReplayError("unfilled entry has accounted branch")
            return
        if not self.branches or self.status is AllFillStatus.ENTRY_UNFILLED:
            raise AllFillReplayError("filled entry lacks accounted branches")
        if self.family is AllFillFamily.TREND_PULLBACK:
            if tuple(branch.label for branch in self.branches) != (
                ExitReplayBranchPolicy.HOLD_ONLY.value,
                ExitReplayBranchPolicy.EMA50_THESIS_EXIT.value,
            ):
                raise AllFillReplayError("paired branches are incomplete")
        elif len(self.branches) != 1:
            raise AllFillReplayError("single-family result has multiple branches")
        for branch in self.branches:
            branch.validate()
            if branch.approved_risk_quote != self.entry_intent.approved_risk_quote:
                raise AllFillReplayError("branch risk differs from authorized intent")
            if branch.entry_fill != self.legacy_replay.entry_fill:
                raise AllFillReplayError("accounted entry differs from replay fill")
        if len({branch.entry_fill for branch in self.branches}) != 1:
            raise AllFillReplayError("paired branches have different entry fills")
        if self.status is not self.branches[-1].status:
            raise AllFillReplayError("primary terminal status differs from candidate branch")
        if self.legacy_replay.entry_fill_invalidation is None:
            ordinary: tuple[
                HistoricalExitBranchResult
                | BreakoutRetestReplayResult
                | RangeMeanReversionReplayResult
                | None,
                ...,
            ]
            if isinstance(self.legacy_replay, PairedTrendPullbackExitReplayResult):
                ordinary = (self.legacy_replay.baseline, self.legacy_replay.ema50_candidate)
            else:
                ordinary = (self.legacy_replay,)
            for branch, source_branch in zip(self.branches, ordinary, strict=True):
                if (
                    source_branch is None
                    or branch.recovery is not None
                    or (
                        branch.exit_request != source_branch.exit_request
                        or branch.exit_fill != source_branch.exit_fill
                        or branch.exit_unfilled != source_branch.exit_unfilled
                        or branch.accounting_events != source_branch.accounting_events
                        or branch.final_position != source_branch.final_position
                    )
                ):
                    raise AllFillReplayError("ordinary branch differs from legacy authority")
        elif any(branch.recovery is None for branch in self.branches):
            raise AllFillReplayError("invalidated entry lacks accounted recovery")
        else:
            invalidation = self.legacy_replay.entry_fill_invalidation
            if isinstance(invalidation, RangeEntryInvalidationAudit):
                expected_reasons = tuple(reason.value for reason in invalidation.reasons)
            elif isinstance(invalidation, EntryFillInvalidationAudit):
                expected_reasons = (invalidation.reason.value,)
            else:
                raise AllFillReplayError("invalidated replay has no known geometry audit")
            expected_midpoint = (
                self.legacy_replay.entry_rule_audit.midpoint
                if isinstance(self.legacy_replay, RangeMeanReversionReplayResult)
                else None
            )
            for branch in self.branches:
                if branch.recovery is None:
                    raise AllFillReplayError("invalidated entry lacks recovery authorization")
                if (
                    branch.recovery.sealed_midpoint != expected_midpoint
                    or branch.recovery.reasons != expected_reasons
                ):
                    raise AllFillReplayError("recovery geometry differs from sealed entry audit")
                if branch.exit_fill is not None:
                    fill = branch.exit_fill
                    expected_quote = price_simulated_market_fill(
                        side=branch.recovery.request.side,
                        quantity=branch.recovery.request.quantity,
                        reference_price=fill.reference_open_price,
                        costs=self.costs,
                    )
                    if (
                        fill.source_id != self.series.source_id
                        or fill.execution_timeframe is not self.series.timeframe
                        or fill.created_at != branch.recovery.request.created_at
                        or fill.eligible_at
                        != branch.recovery.request.created_at + self.costs.latency
                        or fill.execution_candle_open_time != fill.fill_at
                        or fill.fill_at < fill.eligible_at
                        or fill.fill_price != expected_quote.fill_price
                        or fill.fee_quote != expected_quote.fee_quote
                        or fill.spread_bps != self.costs.spread_bps
                        or fill.slippage_bps != self.costs.slippage_bps
                        or fill.fee_bps != self.costs.fee_bps
                    ):
                        raise AllFillReplayError("recovery fill differs from shared simulator")
                elif (
                    branch.exit_unfilled is None
                    or branch.exit_unfilled.order != branch.recovery.request
                    or branch.exit_unfilled.eligible_at
                    != branch.recovery.request.created_at + self.costs.latency
                ):
                    raise AllFillReplayError("recovery-unfilled audit differs from request")


def _family(decision: DecisionJournalEntry[object, object]) -> AllFillFamily:
    strategy = (decision.identity.strategy_id, decision.identity.strategy_version)
    for family, instance in (
        (AllFillFamily.TREND_PULLBACK, TrendPullbackStrategy()),
        (AllFillFamily.BREAKOUT_RETEST, BreakoutRetestStrategy()),
        (AllFillFamily.RANGE_MEAN_REVERSION, RangeMeanReversionStrategy()),
    ):
        if strategy == (instance.strategy_id, instance.strategy_version):
            return family
    raise AllFillReplayError("decision strategy is outside the three-family research policy")


def _non_admitted_status(decision: DecisionJournalEntry[object, object]) -> AllFillStatus:
    # Use the existing source-stage authority, not a second Risk/Safety evaluation.
    if decision.strategy_evaluation.action is CandidateAction.NO_TRADE:
        return AllFillStatus.NO_SIGNAL
    if decision.risk_decision.status.value != "APPROVED":
        return AllFillStatus.RISK_BLOCKED
    return AllFillStatus.SAFETY_BLOCKED


class _AfterDetectionSource:
    """Narrow recovery-only view of the same immutable execution snapshot."""

    def __init__(self, bound: _BoundExecutionCandleSource, detected_at: datetime) -> None:
        self._bound = bound
        self._detected_at = detected_at

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        return tuple(
            candle
            for candle in self._bound.load(series, start=start, end=end)
            if candle.open_time > self._detected_at
        )


def _branch(
    label: str,
    entry_fill: MarketFill,
    exit_request: MarketOrderRequest | None,
    exit_fill: MarketFill | None,
    exit_unfilled: UnfilledOrder | None,
    risk: Decimal,
    end: datetime,
    recovery: RecoveryAuthorization | None = None,
) -> AllFillBranch:
    engine = PositionAccountingEngine()
    events = list(engine.process(AccountingFillBatch(entry_fill.fill_at, (entry_fill,))).events)
    if exit_fill is not None:
        events.extend(engine.process(AccountingFillBatch(exit_fill.fill_at, (exit_fill,))).events)
    final = engine.snapshot(entry_fill.symbol, observed_at=end)
    closed = final.quantity == 0
    with localcontext(_CONTEXT):
        net_r = final.cumulative_net_realized_pnl / risk if closed else None
    status = (
        (AllFillStatus.RECOVERY_CLOSED if closed else AllFillStatus.RECOVERY_OPEN)
        if recovery is not None
        else (AllFillStatus.ORDINARY_CLOSED if closed else AllFillStatus.ORDINARY_OPEN)
    )
    return AllFillBranch(
        label,
        status,
        entry_fill,
        recovery,
        exit_request,
        exit_fill,
        exit_unfilled,
        tuple(events),
        final,
        risk,
        net_r,
    )


class ResearchAllFillReplayRunner:
    """Compose accepted research replays and account every actual entry fill."""

    def __init__(self, source: ExecutionCandleSource) -> None:
        self._source = source

    def run(
        self,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        start: datetime,
        end: datetime,
    ) -> AllFillReplayResult:
        owned = deepcopy(decision)
        owned.validate()
        start = require_utc(start, field="all-fill start")
        end = require_utc(end, field="all-fill end")
        costs.validate()
        scenario = _scenario(costs)
        family = _family(owned)
        if (
            start >= end
            or not start <= owned.identity.observed_at < end
            or series.source_id != owned.identity.source_id
            or series.symbol != owned.identity.symbol
            or series.timeframe is not Timeframe.MINUTES_15
        ):
            raise AllFillReplayError("decision is outside exact execution series/range")
        if not isinstance(contexts, tuple):
            raise TypeError("historical contexts must be an immutable tuple")
        previous_context_at: datetime | None = None
        for sealed in contexts:
            if not isinstance(sealed, HistoricalStrategyContextObservation):
                raise TypeError("historical context must be a sealed observation")
            sealed.validate()
            context = sealed.context
            if (
                context.source_id != owned.identity.source_id
                or context.symbol != owned.identity.symbol
                or context.observed_at <= owned.identity.observed_at
                or context.observed_at >= end
                or (previous_context_at is not None and context.observed_at <= previous_context_at)
            ):
                raise AllFillReplayError("sealed context identity or chronology is invalid")
            previous_context_at = context.observed_at
        authorization = EntryOrderIntentAuthorizer().authorize(owned)
        if authorization.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT:
            raise AllFillReplayError("decision chain fails entry authorization")
        if authorization.status is OrderIntentAuthorizationStatus.NO_INTENT:
            return AllFillReplayResult(
                owned,
                family,
                deepcopy(series),
                deepcopy(costs),
                scenario,
                start,
                end,
                _non_admitted_status(owned),
                None,
                None,
                (),
                None,
            )
        intent = authorization.intent
        if intent is None:
            raise AllFillReplayError("authorized entry intent is missing")
        source_start = entry_source_start(
            decision_at=intent.observed_at,
            authorized_at=intent.authorized_at,
            requested_start=start if family is AllFillFamily.TREND_PULLBACK else intent.observed_at,
            end=end,
        )
        # Load the original source once. Legacy replay and recovery only see this detached snapshot.
        candles = self._source.load(series, start=source_start, end=end)
        bound = _BoundExecutionCandleSource(series, source_start, end, candles)
        by_close = {candle.close_time: candle for candle in bound.candles}
        if len(by_close) != len(bound.candles):
            raise AllFillReplayError("execution snapshot has duplicate close times")
        for sealed in contexts:
            if (
                sealed.context.observed_at >= source_start
                and by_close.get(sealed.context.observed_at) != sealed.context.candle_15m
            ):
                raise AllFillReplayError("sealed context differs from the execution snapshot")
        if family is AllFillFamily.TREND_PULLBACK:
            paired = PairedTrendPullbackExitReplayRunner(bound).run(
                owned, contexts, series, costs, start=start, end=end
            )
            legacy: (
                PairedTrendPullbackExitReplayResult
                | BreakoutRetestReplayResult
                | RangeMeanReversionReplayResult
            ) = paired
            entry_request = paired.entry_order
        elif family is AllFillFamily.BREAKOUT_RETEST:
            legacy = BreakoutRetestFixed2RReplayRunner(bound).run(
                owned, contexts, series, costs, end=end
            )
            entry_request = legacy.entry_request
        else:
            legacy = RangeMeanReversionMidpointReplayRunner(bound).run(
                owned, contexts, series, costs, end=end
            )
            entry_request = legacy.entry_request
        legacy.validate()
        provenance = EntryIntentProvenance.from_authorized_intent(intent)
        if legacy.entry_unfilled is not None:
            return AllFillReplayResult(
                owned,
                family,
                deepcopy(series),
                deepcopy(costs),
                scenario,
                start,
                end,
                AllFillStatus.ENTRY_UNFILLED,
                provenance,
                legacy.entry_unfilled,
                (),
                legacy,
            )
        fill = legacy.entry_fill
        if fill is None or fill.order_id != entry_request.order_id:
            raise AllFillReplayError("legacy replay lacks actual authorized entry fill")
        invalidation = legacy.entry_fill_invalidation
        labels = (
            (ExitReplayBranchPolicy.HOLD_ONLY.value, ExitReplayBranchPolicy.EMA50_THESIS_EXIT.value)
            if family is AllFillFamily.TREND_PULLBACK
            else (family.value,)
        )
        branches: list[AllFillBranch] = []
        if invalidation is not None:
            if isinstance(invalidation, RangeEntryInvalidationAudit):
                reasons = tuple(reason.value for reason in invalidation.reasons)
            elif isinstance(invalidation, EntryFillInvalidationAudit):
                reasons = (invalidation.reason.value,)
            else:
                raise AllFillReplayError("unknown invalidation authority")
            # Entry accounting precedes deriving the recovery order. Each paired branch
            # accounts the same entry independently; no doubled opportunity is reported.
            for label in labels:
                entry_engine = PositionAccountingEngine()
                entry_engine.process(AccountingFillBatch(fill.fill_at, (fill,)))
                position = entry_engine.snapshot(fill.symbol, observed_at=fill.fill_at)
                request = MarketOrderRequest(
                    f"all-fill-recovery:{owned.identity}",
                    fill.fill_at,
                    fill.symbol,
                    OrderSide.SELL if position.quantity > 0 else OrderSide.BUY,
                    position.quantity.copy_abs(),
                )
                recovery = RecoveryAuthorization(
                    deepcopy(owned.identity),
                    provenance,
                    fill.order_id,
                    fill.fill_at,
                    reasons,
                    (
                        legacy.entry_rule_audit.midpoint
                        if isinstance(legacy, RangeMeanReversionReplayResult)
                        else None
                    ),
                    request,
                    position.quantity.copy_abs(),
                )
                recovery.validate()
                execution = BacktestExecutionSimulator(
                    _AfterDetectionSource(bound, fill.fill_at)
                ).run(series, costs, (request,), start=source_start, end=end)
                if execution.filled_count + execution.unfilled_count != 1:
                    raise AllFillReplayError("recovery execution is contradictory")
                branches.append(
                    _branch(
                        label,
                        fill,
                        request,
                        execution.fills[0] if execution.fills else None,
                        execution.unfilled_orders[0] if execution.unfilled_orders else None,
                        provenance.approved_risk_quote,
                        end,
                        recovery,
                    )
                )
        elif isinstance(legacy, PairedTrendPullbackExitReplayResult):
            if legacy.baseline is None or legacy.ema50_candidate is None:
                raise AllFillReplayError("valid paired replay lacks branches")
            for label, branch in zip(
                labels, (legacy.baseline, legacy.ema50_candidate), strict=True
            ):
                branches.append(
                    _branch(
                        label,
                        fill,
                        branch.exit_request,
                        branch.exit_fill,
                        branch.exit_unfilled,
                        provenance.approved_risk_quote,
                        end,
                    )
                )
        else:
            branches.append(
                _branch(
                    labels[0],
                    fill,
                    legacy.exit_request,
                    legacy.exit_fill,
                    legacy.exit_unfilled,
                    provenance.approved_risk_quote,
                    end,
                )
            )
        return AllFillReplayResult(
            owned,
            family,
            deepcopy(series),
            deepcopy(costs),
            scenario,
            start,
            end,
            branches[-1].status,
            provenance,
            None,
            tuple(branches),
            legacy,
        )


@dataclass(frozen=True, slots=True)
class AllFillPopulationRow:
    """One supplied observation, including non-candidates and rejected candidates."""

    decision: DecisionJournalEntry[object, object]
    result: AllFillReplayResult
    cohort: str
    regime: str | None

    def validate(self) -> None:
        self.decision.validate()
        self.result.validate()
        if self.decision != self.result.decision or not self.cohort:
            raise AllFillReplayError("population row lacks exact decision or cohort provenance")


@dataclass(frozen=True, slots=True)
class AllFillReportCell:
    symbol: str
    family: AllFillFamily
    scenario: AllFillScenario
    utc_month: str
    side: str
    cohort: str
    regime: str | None
    observation_count: int
    closed_count: int
    unresolved_count: int
    incurred_fees: Decimal
    closed_net_r_total: Decimal


def _row_key(
    row: AllFillPopulationRow,
) -> CellKey:
    primary = row.result.branches[-1] if row.result.branches else None
    action = row.decision.strategy_evaluation.action
    side = (
        primary.entry_fill.side.value
        if primary is not None
        else "BUY"
        if action is CandidateAction.LONG_CANDIDATE
        else "SELL"
        if action is CandidateAction.SHORT_CANDIDATE
        else "NONE"
    )
    return (
        str(row.decision.identity.symbol),
        row.result.family,
        row.result.scenario,
        row.decision.identity.observed_at.strftime("%Y-%m"),
        side,
        row.cohort,
        row.regime,
    )


def _metrics(
    rows: tuple[AllFillPopulationRow, ...],
    declared_keys: tuple[CellKey, ...],
) -> tuple[
    tuple[tuple[AllFillStatus, int], ...],
    int,
    int,
    Decimal,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    tuple[AllFillReportCell, ...],
    int,
]:
    counts = {status: 0 for status in AllFillStatus}
    fee_values: list[Decimal] = []
    closed_r: list[Decimal] = []
    unresolved = 0
    paired_both_closed = 0
    cells: list[AllFillReportCell] = []
    for row in rows:
        row.validate()
        counts[row.result.status] += 1
        primary = row.result.branches[-1] if row.result.branches else None
        if primary is not None:
            fee_values.append(primary.final_position.cumulative_fees)
            if primary.closed_net_r is None:
                unresolved += 1
            else:
                closed_r.append(primary.closed_net_r)
            if len(row.result.branches) == 2 and all(
                branch.closed_net_r is not None for branch in row.result.branches
            ):
                paired_both_closed += 1
    with localcontext(_CONTEXT):
        fees = sum(fee_values, Decimal(0))
        profit = sum((value for value in closed_r if value > 0), Decimal(0))
        loss_magnitude = -sum((value for value in closed_r if value < 0), Decimal(0))
        mean = sum(closed_r, Decimal(0)) / Decimal(len(closed_r)) if closed_r else None
        ordered_r = sorted(closed_r)
        if not ordered_r:
            median = None
        elif len(ordered_r) % 2:
            median = ordered_r[len(ordered_r) // 2]
        else:
            median = (
                ordered_r[len(ordered_r) // 2 - 1] + ordered_r[len(ordered_r) // 2]
            ) / Decimal(2)
        factor = profit / loss_magnitude if loss_magnitude > 0 else None
        for key in declared_keys:
            members = tuple(row for row in rows if _row_key(row) == key)
            cell_branches = tuple(row.result.branches[-1] for row in members if row.result.branches)
            cells.append(
                AllFillReportCell(
                    *key,
                    len(members),
                    sum(branch.closed_net_r is not None for branch in cell_branches),
                    sum(branch.closed_net_r is None for branch in cell_branches),
                    sum(
                        (branch.final_position.cumulative_fees for branch in cell_branches),
                        Decimal(0),
                    ),
                    sum(
                        (
                            branch.closed_net_r
                            for branch in cell_branches
                            if branch.closed_net_r is not None
                        ),
                        Decimal(0),
                    ),
                )
            )
    return (
        tuple((status, counts[status]) for status in AllFillStatus),
        len(closed_r),
        unresolved,
        fees,
        mean,
        median,
        factor,
        tuple(cells),
        paired_both_closed,
    )


@dataclass(frozen=True, slots=True)
class AllFillPopulationReport:
    """Independent-opportunity descriptive projection; never a portfolio claim."""

    rows: tuple[AllFillPopulationRow, ...]
    declared_cell_keys: tuple[CellKey, ...]
    terminal_counts: tuple[tuple[AllFillStatus, int], ...]
    closed_denominator: int
    unresolved_count: int
    incurred_fees: Decimal
    mean_closed_net_r: Decimal | None
    median_closed_net_r: Decimal | None
    closed_r_profit_factor: Decimal | None
    cells: tuple[AllFillReportCell, ...]
    paired_both_closed_count: int
    policy_version: str = field(init=False, default=POLICY_VERSION)
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    is_portfolio_performance: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-all-fill-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-all-fill-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise AllFillReplayError("all-fill report integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        if (
            self.policy_version != POLICY_VERSION
            or self.schema_version != SCHEMA_VERSION
            or self.is_portfolio_performance
        ):
            raise AllFillReplayError("report has incorrect authority or schema")
        identities = tuple(row.decision.identity for row in self.rows)
        if len(set(identities)) != len(identities):
            raise AllFillReplayError("duplicate decision in supplied population")
        if len({row.decision.identity.symbol.quote for row in self.rows}) > 1:
            raise AllFillReplayError("mixed quote currencies cannot share economic totals")
        if (
            tuple(
                sorted(
                    identities,
                    key=lambda identity: (
                        identity.observed_at,
                        str(identity.symbol),
                        identity.strategy_id,
                        identity.strategy_version,
                        identity.source_id.value,
                    ),
                )
            )
            != identities
        ):
            raise AllFillReplayError("supplied population must be chronological and canonical")
        if len(set(self.declared_cell_keys)) != len(self.declared_cell_keys):
            raise AllFillReplayError("duplicate declared report cell")
        if any(_row_key(row) not in self.declared_cell_keys for row in self.rows):
            raise AllFillReplayError("reported population has an undeclared cell")
        expected = _metrics(self.rows, self.declared_cell_keys)
        actual = (
            self.terminal_counts,
            self.closed_denominator,
            self.unresolved_count,
            self.incurred_fees,
            self.mean_closed_net_r,
            self.median_closed_net_r,
            self.closed_r_profit_factor,
            self.cells,
            self.paired_both_closed_count,
        )
        if actual != expected or sum(count for _, count in self.terminal_counts) != len(self.rows):
            raise AllFillReplayError("report metrics or terminal partition do not reconcile")


def project_all_fill_population(
    decisions: tuple[DecisionJournalEntry[object, object], ...],
    results: tuple[AllFillReplayResult, ...],
    labels: tuple[tuple[str, str | None], ...],
    *,
    declared_cell_keys: tuple[CellKey, ...] = (),
) -> AllFillPopulationReport:
    """Require the complete explicitly supplied population; never infer missing decisions."""

    if len(decisions) != len(results) or len(results) != len(labels):
        raise AllFillReplayError("missing or orphan replay result in population")
    rows = tuple(
        AllFillPopulationRow(deepcopy(decision), deepcopy(result), cohort, regime)
        for decision, result, (cohort, regime) in zip(decisions, results, labels, strict=True)
    )
    keys = tuple(dict.fromkeys((*declared_cell_keys, *(_row_key(row) for row in rows))))
    return AllFillPopulationReport(rows, keys, *_metrics(rows, keys))
