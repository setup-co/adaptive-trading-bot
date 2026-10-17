"""Prospective, offline all-fill economics over the three existing research replays.

This is a separate schema. Legacy invalidated-entry results remain unchanged; a
filled entry is accounted here before one causal recovery is authorized.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hmac import compare_digest
from typing import Any, NoReturn

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


def _snapshot_digest(candles: tuple[Candle, ...]) -> str:
    return _integrity_seal(
        "research-all-fill-execution-snapshot", candles, excluded_names=frozenset()
    )


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
    execution_snapshot: tuple[Candle, ...]
    execution_snapshot_digest: str
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
        if not isinstance(self.execution_snapshot, tuple) or not compare_digest(
            self.execution_snapshot_digest, _snapshot_digest(self.execution_snapshot)
        ):
            raise AllFillReplayError("execution snapshot digest differs from original evidence")
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
        validate_shared_all_fill_relations(
            decision=self.decision,
            family=self.family,
            series=self.series,
            costs=self.costs,
            start=self.start,
            end=self.end,
            status=self.status,
            entry_intent=self.entry_intent,
            entry_unfilled=self.entry_unfilled,
            branches=self.branches,
            execution_snapshot=self.execution_snapshot,
            legacy_replay=self.legacy_replay,
            error_type=AllFillReplayError,
        )
        return
        authorization = EntryOrderIntentAuthorizer().authorize(self.decision)
        if authorization.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT:
            raise AllFillReplayError("decision chain is inconsistent")
        if authorization.status is OrderIntentAuthorizationStatus.NO_INTENT:
            if (
                self.entry_intent is not None
                or self.legacy_replay is not None
                or self.branches
                or self.entry_unfilled is not None
                or self.execution_snapshot
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
        legacy = self.legacy_replay
        if (
            (
                self.family is AllFillFamily.TREND_PULLBACK
                and not isinstance(legacy, PairedTrendPullbackExitReplayResult)
            )
            or (
                self.family is AllFillFamily.BREAKOUT_RETEST
                and not isinstance(legacy, BreakoutRetestReplayResult)
            )
            or (
                self.family is AllFillFamily.RANGE_MEAN_REVERSION
                and not isinstance(legacy, RangeMeanReversionReplayResult)
            )
        ):
            raise AllFillReplayError("legacy replay family differs from decision")
        legacy.validate()
        legacy_identity = (
            legacy.originating_decision_identity
            if isinstance(legacy, PairedTrendPullbackExitReplayResult)
            else legacy.decision_identity
        )
        if (
            legacy.entry_intent != self.entry_intent
            or legacy_identity != self.decision.identity
            or legacy.execution_series != self.series
            or legacy.execution_costs != self.costs
            or legacy.end != self.end
            or (
                isinstance(legacy, PairedTrendPullbackExitReplayResult)
                and legacy.start != self.start
            )
            or (
                not isinstance(legacy, PairedTrendPullbackExitReplayResult)
                and self.start != self.decision.identity.observed_at
            )
        ):
            raise AllFillReplayError("legacy replay entry provenance mismatch")
        entry_request = (
            legacy.entry_order
            if isinstance(legacy, PairedTrendPullbackExitReplayResult)
            else legacy.entry_request
        )
        source_start = entry_source_start(
            decision_at=self.entry_intent.decision_identity.observed_at,
            authorized_at=self.entry_intent.authorized_at,
            requested_start=(
                self.start
                if self.family is AllFillFamily.TREND_PULLBACK
                else self.entry_intent.decision_identity.observed_at
            ),
            end=self.end,
        )
        bound = _BoundExecutionCandleSource(
            self.series, source_start, self.end, self.execution_snapshot
        )
        entry_execution = BacktestExecutionSimulator(bound).run(
            self.series, self.costs, (entry_request,), start=source_start, end=self.end
        )
        if entry_execution.fills != (
            () if legacy.entry_fill is None else (legacy.entry_fill,)
        ) or entry_execution.unfilled_orders != (
            () if legacy.entry_unfilled is None else (legacy.entry_unfilled,)
        ):
            raise AllFillReplayError("entry outcome differs from bound execution snapshot")
        if self.entry_unfilled is not None:
            if (
                self.status is not AllFillStatus.ENTRY_UNFILLED
                or self.branches
                or self.entry_unfilled != self.legacy_replay.entry_unfilled
                or self.legacy_replay.entry_fill is not None
            ):
                raise AllFillReplayError("unfilled entry has accounted branch")
            return
        if (
            not self.branches
            or self.status is AllFillStatus.ENTRY_UNFILLED
            or self.legacy_replay.entry_fill is None
            or self.legacy_replay.entry_unfilled is not None
        ):
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
            if branch.final_position.observed_at != self.end:
                raise AllFillReplayError("accounting snapshot differs from declared end")
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
                    or branch.status
                    is not (
                        AllFillStatus.ORDINARY_CLOSED
                        if source_branch.position_closed
                        else AllFillStatus.ORDINARY_OPEN
                    )
                    or (
                        branch.exit_request != source_branch.exit_request
                        or branch.exit_fill != source_branch.exit_fill
                        or branch.exit_unfilled != source_branch.exit_unfilled
                        or branch.accounting_events != source_branch.accounting_events
                        or branch.final_position != source_branch.final_position
                    )
                ):
                    raise AllFillReplayError("ordinary branch differs from legacy authority")
                if branch.exit_request is not None:
                    exit_execution = BacktestExecutionSimulator(bound).run(
                        self.series,
                        self.costs,
                        (branch.exit_request,),
                        start=source_start,
                        end=self.end,
                    )
                    if exit_execution.fills != (
                        () if branch.exit_fill is None else (branch.exit_fill,)
                    ) or exit_execution.unfilled_orders != (
                        () if branch.exit_unfilled is None else (branch.exit_unfilled,)
                    ):
                        raise AllFillReplayError("ordinary exit differs from bound snapshot")
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
                    branch.recovery.decision_identity != self.decision.identity
                    or branch.recovery.entry_intent != self.entry_intent
                    or branch.recovery.sealed_midpoint != expected_midpoint
                    or branch.recovery.reasons != expected_reasons
                ):
                    raise AllFillReplayError("recovery geometry differs from sealed entry audit")
                recovery_execution = BacktestExecutionSimulator(
                    _AfterDetectionSource(bound, branch.recovery.detected_at)
                ).run(
                    self.series,
                    self.costs,
                    (branch.recovery.request,),
                    start=source_start,
                    end=self.end,
                )
                if recovery_execution.fills != (
                    () if branch.exit_fill is None else (branch.exit_fill,)
                ) or recovery_execution.unfilled_orders != (
                    () if branch.exit_unfilled is None else (branch.exit_unfilled,)
                ):
                    raise AllFillReplayError("recovery differs from bound execution snapshot")


def validate_shared_all_fill_relations(
    *,
    decision: DecisionJournalEntry[object, object],
    family: AllFillFamily,
    series: ExecutionSeries,
    costs: ExecutionCostConfiguration,
    start: datetime,
    end: datetime,
    status: AllFillStatus,
    entry_intent: EntryIntentProvenance | None,
    entry_unfilled: UnfilledOrder | None,
    branches: tuple[AllFillBranch, ...],
    execution_snapshot: tuple[Candle, ...],
    legacy_replay: (
        PairedTrendPullbackExitReplayResult
        | BreakoutRetestReplayResult
        | RangeMeanReversionReplayResult
        | None
    ),
    error_type: type[ValueError],
) -> None:
    """Validate relations shared by legacy and factorial all-fill envelopes.

    Schema/scenario seals stay owned by their envelopes.  This seam owns the
    replay-to-entry, snapshot, branch topology, recovery and accounting graph.
    """

    def fail(message: str) -> NoReturn:
        raise error_type(message)

    authorization = EntryOrderIntentAuthorizer().authorize(decision)
    if authorization.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT:
        fail("all-fill decision chain is inconsistent")
    if authorization.status is OrderIntentAuthorizationStatus.NO_INTENT:
        if entry_intent is not None or entry_unfilled is not None or branches or legacy_replay:
            fail("non-admitted all-fill result contains replay evidence")
        if status is not _non_admitted_status(decision):
            fail("non-admitted all-fill terminal status contradicts decision")
        return
    if authorization.intent is None or entry_intent is None or legacy_replay is None:
        fail("admitted all-fill result lacks authority")
    assert authorization.intent is not None
    assert entry_intent is not None
    assert legacy_replay is not None
    if entry_intent != EntryIntentProvenance.from_authorized_intent(authorization.intent):
        fail("all-fill entry provenance differs from authorizer")
    expected_type = {
        AllFillFamily.TREND_PULLBACK: PairedTrendPullbackExitReplayResult,
        AllFillFamily.BREAKOUT_RETEST: BreakoutRetestReplayResult,
        AllFillFamily.RANGE_MEAN_REVERSION: RangeMeanReversionReplayResult,
    }[family]
    if not isinstance(legacy_replay, expected_type):
        fail("all-fill legacy replay family differs from decision")
    legacy_replay.validate()
    legacy_identity = (
        legacy_replay.originating_decision_identity
        if isinstance(legacy_replay, PairedTrendPullbackExitReplayResult)
        else legacy_replay.decision_identity
    )
    if (
        legacy_replay.entry_intent != entry_intent
        or legacy_identity != decision.identity
        or legacy_replay.execution_series != series
        or legacy_replay.execution_costs != costs
        or legacy_replay.end != end
        or (
            isinstance(legacy_replay, PairedTrendPullbackExitReplayResult)
            and legacy_replay.start != start
        )
        or (
            not isinstance(legacy_replay, PairedTrendPullbackExitReplayResult)
            and start != decision.identity.observed_at
        )
    ):
        fail("all-fill legacy replay entry provenance mismatch")
    source_start = entry_source_start(
        decision_at=entry_intent.decision_identity.observed_at,
        authorized_at=entry_intent.authorized_at,
        requested_start=(
            start if family is AllFillFamily.TREND_PULLBACK else decision.identity.observed_at
        ),
        end=end,
    )
    bound = _BoundExecutionCandleSource(series, source_start, end, execution_snapshot)
    entry_request = (
        legacy_replay.entry_order
        if isinstance(legacy_replay, PairedTrendPullbackExitReplayResult)
        else legacy_replay.entry_request
    )
    entry_execution = BacktestExecutionSimulator(bound).run(
        series, costs, (entry_request,), start=source_start, end=end
    )
    if entry_execution.fills != (
        () if legacy_replay.entry_fill is None else (legacy_replay.entry_fill,)
    ):
        fail("all-fill entry outcome differs from bound execution snapshot")
    if entry_execution.unfilled_orders != (
        () if legacy_replay.entry_unfilled is None else (legacy_replay.entry_unfilled,)
    ):
        fail("all-fill entry outcome differs from bound execution snapshot")
    if entry_unfilled is not None:
        if (
            status is not AllFillStatus.ENTRY_UNFILLED
            or branches
            or entry_unfilled != legacy_replay.entry_unfilled
            or legacy_replay.entry_fill is not None
        ):
            fail("all-fill unfilled entry contradicts legacy replay")
        return
    if (
        legacy_replay.entry_fill is None
        or legacy_replay.entry_unfilled is not None
        or not branches
        or status is AllFillStatus.ENTRY_UNFILLED
    ):
        fail("all-fill filled entry lacks accounted branches")
    if family is AllFillFamily.TREND_PULLBACK:
        if tuple(branch.label for branch in branches) != (
            ExitReplayBranchPolicy.HOLD_ONLY.value,
            ExitReplayBranchPolicy.EMA50_THESIS_EXIT.value,
        ):
            fail("all-fill paired branches are incomplete")
    elif len(branches) != 1:
        fail("all-fill single-family result has multiple branches")
    if status is not branches[-1].status:
        fail("all-fill primary terminal status differs from candidate branch")
    if len({branch.entry_fill for branch in branches}) != 1:
        fail("all-fill paired branches have different entry fills")
    for branch in branches:
        branch.validate()
        if (
            branch.approved_risk_quote != entry_intent.approved_risk_quote
            or branch.entry_fill != legacy_replay.entry_fill
            or branch.final_position.observed_at != end
        ):
            fail("all-fill branch differs from entry/accounting authority")
    if legacy_replay.entry_fill_invalidation is None:
        ordinary = (
            (legacy_replay.baseline, legacy_replay.ema50_candidate)
            if isinstance(legacy_replay, PairedTrendPullbackExitReplayResult)
            else (legacy_replay,)
        )
        for branch, source_branch in zip(branches, ordinary, strict=True):
            if (
                source_branch is None
                or branch.recovery is not None
                or branch.status
                is not (
                    AllFillStatus.ORDINARY_CLOSED
                    if source_branch.position_closed
                    else AllFillStatus.ORDINARY_OPEN
                )
                or branch.exit_request != source_branch.exit_request
                or branch.exit_fill != source_branch.exit_fill
                or branch.exit_unfilled != source_branch.exit_unfilled
                or branch.accounting_events != source_branch.accounting_events
                or branch.final_position != source_branch.final_position
            ):
                fail("all-fill ordinary branch differs from legacy authority")
            if branch.exit_request is not None:
                exit_execution = BacktestExecutionSimulator(bound).run(
                    series, costs, (branch.exit_request,), start=source_start, end=end
                )
                if exit_execution.fills != (
                    () if branch.exit_fill is None else (branch.exit_fill,)
                ) or exit_execution.unfilled_orders != (
                    () if branch.exit_unfilled is None else (branch.exit_unfilled,)
                ):
                    fail("all-fill ordinary exit differs from bound snapshot")
    elif any(branch.recovery is None for branch in branches):
        fail("all-fill invalidated entry lacks accounted recovery")
    else:
        invalidation = legacy_replay.entry_fill_invalidation
        if isinstance(invalidation, RangeEntryInvalidationAudit):
            expected_reasons = tuple(reason.value for reason in invalidation.reasons)
        elif isinstance(invalidation, EntryFillInvalidationAudit):
            expected_reasons = (invalidation.reason.value,)
        else:
            fail("all-fill invalidated replay has no known geometry audit")
        expected_midpoint = (
            legacy_replay.entry_rule_audit.midpoint
            if isinstance(legacy_replay, RangeMeanReversionReplayResult)
            else None
        )
        for branch in branches:
            recovery = branch.recovery
            if recovery is None or (
                recovery.decision_identity != decision.identity
                or recovery.entry_intent != entry_intent
                or recovery.sealed_midpoint != expected_midpoint
                or recovery.reasons != expected_reasons
            ):
                fail("all-fill recovery geometry differs from sealed entry audit")
            assert recovery is not None
            recovery_execution = BacktestExecutionSimulator(
                _AfterDetectionSource(bound, recovery.detected_at)
            ).run(series, costs, (recovery.request,), start=source_start, end=end)
            if recovery_execution.fills != (
                () if branch.exit_fill is None else (branch.exit_fill,)
            ) or recovery_execution.unfilled_orders != (
                () if branch.exit_unfilled is None else (branch.exit_unfilled,)
            ):
                fail("all-fill recovery differs from bound execution snapshot")
        if len(branches) == 2 and (
            branches[0].recovery != branches[1].recovery
            or branches[0].exit_fill != branches[1].exit_fill
            or branches[0].exit_unfilled != branches[1].exit_unfilled
        ):
            fail("all-fill paired recovery execution differs")


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
        result = self._run_with_result_factory(
            decision,
            contexts,
            series,
            costs,
            start=start,
            end=end,
            result_label=None,
            result_factory=AllFillReplayResult,
        )
        if not isinstance(result, AllFillReplayResult):  # pragma: no cover - fixed factory
            raise AllFillReplayError("legacy all-fill factory returned the wrong result")
        return result

    def _run_with_result_factory(
        self,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        start: datetime,
        end: datetime,
        result_label: object | None,
        result_factory: Callable[..., Any],
    ) -> Any:
        """Private shared orchestration seam for separately versioned research results."""

        owned = deepcopy(decision)
        owned.validate()
        start = require_utc(start, field="all-fill start")
        end = require_utc(end, field="all-fill end")
        costs.validate()
        scenario = _scenario(costs) if result_label is None else result_label
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
            return result_factory(
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
                (),
                _snapshot_digest(()),
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
            return result_factory(
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
                bound.candles,
                _snapshot_digest(bound.candles),
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
        return result_factory(
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
            bound.candles,
            _snapshot_digest(bound.candles),
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
    mean_closed_net_r: Decimal | None
    median_closed_net_r: Decimal | None
    closed_r_profit_factor: Decimal | None


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


def _closed_distribution(
    values: tuple[Decimal, ...],
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Describe only closed approved-risk R, never unresolved exposure."""

    with localcontext(_CONTEXT):
        if not values:
            return None, None, None
        ordered = sorted(values)
        mean = sum(values, Decimal(0)) / Decimal(len(values))
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
        )
        positive = sum((value for value in values if value > 0), Decimal(0))
        negative = -sum((value for value in values if value < 0), Decimal(0))
        factor = positive / negative if negative > 0 else None
        return mean, median, factor


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
        mean, median, factor = _closed_distribution(tuple(closed_r))
        for key in declared_keys:
            members = tuple(row for row in rows if _row_key(row) == key)
            cell_branches = tuple(row.result.branches[-1] for row in members if row.result.branches)
            cell_r = tuple(
                branch.closed_net_r for branch in cell_branches if branch.closed_net_r is not None
            )
            cell_mean, cell_median, cell_factor = _closed_distribution(cell_r)
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
                        cell_r,
                        Decimal(0),
                    ),
                    cell_mean,
                    cell_median,
                    cell_factor,
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
    scenario: AllFillScenario
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
            not isinstance(self.scenario, AllFillScenario)
            or any(row.result.scenario is not self.scenario for row in self.rows)
            or any(key[2] is not self.scenario for key in self.declared_cell_keys)
        ):
            raise AllFillReplayError("population report must declare one exact scenario")
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
    scenario: AllFillScenario,
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
    return AllFillPopulationReport(rows, scenario, keys, *_metrics(rows, keys))
