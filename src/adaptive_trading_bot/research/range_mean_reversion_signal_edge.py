"""Frozen, research-only Range Mean Reversion midpoint signal-edge screen."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hmac import compare_digest
from json import dumps
from statistics import median

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.entry_chronology import CAUSAL_ENTRY_CHRONOLOGY_V2
from adaptive_trading_bot.historical_sources import is_locked_weex_source
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import (
    EntryOrderIntentAuthorizer,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.replay import ReplaySpeed
from adaptive_trading_bot.research.paired_exit_replay import EntryIntentProvenance
from adaptive_trading_bot.research.range_mean_reversion_corpus import (
    RangeMeanReversionDecisionObservation,
    RangeMeanReversionHistoricalCorpusGenerator,
    RangeMeanReversionHistoricalResearchCorpus,
    RangeMeanReversionResearchConfiguration,
    locked_warmup_exclusions,
)
from adaptive_trading_bot.research.range_mean_reversion_replay import (
    RangeEntryInvalidationReason,
    RangeMeanReversionExitCause,
    RangeMeanReversionMidpointReplayRunner,
    RangeMeanReversionReplayResult,
    RangeMeanReversionReplayStatus,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _canonical_integrity_value,
    _integrity_seal,
)
from adaptive_trading_bot.research.trend_pullback_exit_robustness import RobustnessRate
from adaptive_trading_bot.research.trend_pullback_signal_edge import (
    CandidateAbsoluteEconomics,
    CandidateEconomicOutcome,
    CandidateEconomicStratum,
    ProfitFactorState,
    _economics,
    _is_evaluable,
    _losing_streak,
    _rate,
    _strata,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    QUALIFIED_DATASET_MANIFEST_SHA256,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.storage import HistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies.range_context import RANGE_PARTIAL_EVIDENCE_RETENTION_V1

_SCHEMA = "range-mean-reversion-signal-edge-v3"
_FROZEN_SCHEMA = "range-mean-reversion-signal-edge-v1"
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
SOURCE_ID = SourceId("weex-v3-last-e5b0c4035a19d5a80ca7")
DATASET_VERSION = "weex-v1-5m-last-a629a29b580b2e103adba918"
MANIFEST_SHA256 = "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
RESEARCH_EQUITY = Decimal("10000")
FOLLOW_UP = timedelta(hours=168)
COSTS = ExecutionCostConfiguration(
    spread_bps=Decimal("2"), slippage_bps=Decimal("2"), fee_bps=Decimal("10")
)
DEPENDENCE_WARNINGS = (
    "Raw candidates are not an independent sample size.",
    "Overlapping positions may share market path.",
    "Total net R is descriptive, not portfolio PnL.",
    "Raw count alone supports no significance claim.",
)


class RangeMeanReversionScreenError(ValueError):
    """Locked protocol, report, or screen authority failed closed."""


class RangeMeanReversionUnitStatus(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class RangeMeanReversionScreenStatus(StrEnum):
    POSITIVE = "RANGE_MEAN_REVERSION_SIGNAL_EDGE_SCREEN_POSITIVE"
    NEGATIVE = "RANGE_MEAN_REVERSION_SIGNAL_EDGE_SCREEN_NEGATIVE"
    MIXED = "RANGE_MEAN_REVERSION_SIGNAL_EDGE_SCREEN_MIXED"
    INSUFFICIENT = "RANGE_MEAN_REVERSION_SIGNAL_EDGE_SCREEN_INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class RangeMeanReversionRunDefinition:
    run_id: str
    symbol: TradingSymbol
    start: datetime
    end: datetime
    follow_up_boundary: datetime

    def __post_init__(self) -> None:
        if self.run_id not in {"A", "B", "C"} or self.follow_up_boundary != self.end + FOLLOW_UP:
            raise RangeMeanReversionScreenError("invalid predeclared run")


FROZEN_RUNS = (
    RangeMeanReversionRunDefinition(
        "A",
        TradingSymbol("BTC", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
    RangeMeanReversionRunDefinition(
        "B",
        TradingSymbol("ETH", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 12, 25, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ),
    RangeMeanReversionRunDefinition(
        "C",
        TradingSymbol("ETH", "USDT"),
        datetime(2025, 4, 2, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
)


@dataclass(frozen=True, slots=True)
class RangeMeanReversionObservation:
    decision: RangeMeanReversionDecisionObservation
    replay_end: datetime
    result: RangeMeanReversionReplayResult
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-observation", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    @property
    def identity(self) -> DecisionIdentity:
        return self.decision.identity

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-observation", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeMeanReversionScreenError("observation seal mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.decision.validate()
        self.result.validate()
        authorization = EntryOrderIntentAuthorizer().authorize(self.decision.decision)
        if (
            not self.decision.is_research_admissible_entry
            or authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
            or authorization.intent is None
            or self.result.entry_intent
            != EntryIntentProvenance.from_authorized_intent(authorization.intent)
            or self.result.entry_rule_audit != self.decision.decision.strategy_evaluation.rule_audit
            or self.result.decision_identity != self.identity
            or self.result.end != self.replay_end
            or self.replay_end <= self.identity.observed_at
        ):
            raise RangeMeanReversionScreenError("observation authority differs")


@dataclass(frozen=True, slots=True)
class RangeMeanReversionPopulation:
    raw_candidate_count: int
    entry_unfilled_count: int
    entry_fill_invalidated_count: int
    stop_geometry_invalidated_count: int
    midpoint_geometry_invalidated_count: int
    replayed_count: int
    closed_count: int
    unresolved_count: int
    economic_outcome_denominator: int
    closure_rate: RobustnessRate
    protective_stop_count: int
    range_midpoint_target_count: int
    same_bar_stop_precedence_count: int
    exit_unfilled_count: int


@dataclass(frozen=True, slots=True)
class RangeMeanReversionGeometrySummary:
    replayed_count: int
    target_distances_quote: tuple[Decimal, ...]
    stop_distances_quote: tuple[Decimal, ...]
    reward_risk_ratios: tuple[Decimal, ...]
    median_reward_risk: Decimal | None
    minimum_reward_risk: Decimal | None
    maximum_reward_risk: Decimal | None
    descriptive_only: bool = True


@dataclass(frozen=True, slots=True)
class RangeMeanReversionOverlapAudit:
    replayed_count: int
    component_count: int
    largest_component_size: int
    overlapping_component_count: int
    component_member_identities: tuple[tuple[DecisionIdentity, ...], ...]


@dataclass(frozen=True, slots=True)
class RangeMeanReversionSignalEdgeReport:
    source_corpus_id: str
    source_corpus_digest: str
    follow_up_duration: timedelta
    execution_costs: ExecutionCostConfiguration
    observations: tuple[RangeMeanReversionObservation, ...]
    population: RangeMeanReversionPopulation = field(init=False)
    outcomes: tuple[CandidateEconomicOutcome, ...] = field(init=False)
    economics: CandidateAbsoluteEconomics = field(init=False)
    maximum_chronological_losing_streak: int = field(init=False)
    strata: tuple[CandidateEconomicStratum, ...] = field(init=False)
    geometry: RangeMeanReversionGeometrySummary = field(init=False)
    overlap_audit: RangeMeanReversionOverlapAudit = field(init=False)
    economically_evaluable: bool = field(init=False)
    unit_status: RangeMeanReversionUnitStatus = field(init=False)
    report_digest: str = field(init=False)
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )
    schema_version: str = field(init=False, default=_SCHEMA)
    is_trading_authority: bool = field(init=False, default=False)
    is_portfolio_performance: bool = field(init=False, default=False)
    dependence_warnings: tuple[str, ...] = field(init=False, default=DEPENDENCE_WARNINGS)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_costs", deepcopy(self.execution_costs))
        object.__setattr__(self, "observations", deepcopy(self.observations))
        population = _population(self.observations)
        outcomes = _outcomes(self.observations)
        economics = _economics(outcomes)
        evaluable = _is_evaluable(population.closed_count, population.closure_rate.value)
        for name, value in (
            ("population", population),
            ("outcomes", outcomes),
            ("economics", economics),
            ("maximum_chronological_losing_streak", _losing_streak(outcomes)),
            ("strata", _strata(outcomes)),
            ("geometry", _geometry(self.observations)),
            ("overlap_audit", _overlap(self.observations)),
            ("economically_evaluable", evaluable),
            ("unit_status", _unit_status(economics, evaluable)),
        ):
            object.__setattr__(self, name, value)
        self._validate_facts()
        digest = _integrity_seal(
            "range-report-digest",
            self,
            excluded_names=frozenset({"report_digest", "integrity_seal"}),
        )
        object.__setattr__(self, "report_digest", digest)
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal("range-report", self, excluded_names=frozenset({"integrity_seal"})),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal("range-report", self, excluded_names=frozenset({"integrity_seal"})),
        ):
            raise RangeMeanReversionScreenError("report integrity mismatch")
        self._validate_facts()
        if self.report_digest != _integrity_seal(
            "range-report-digest",
            self,
            excluded_names=frozenset({"report_digest", "integrity_seal"}),
        ):
            raise RangeMeanReversionScreenError("report digest mismatch")

    def _validate_facts(self) -> None:
        self.execution_costs.validate()
        if (
            self.schema_version != _SCHEMA
            or self.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
            or self.partial_evidence_policy_version != RANGE_PARTIAL_EVIDENCE_RETENTION_V1
            or self.is_trading_authority
            or self.is_portfolio_performance
            or self.dependence_warnings != DEPENDENCE_WARNINGS
            or not self.source_corpus_id
            or not self.source_corpus_digest
            or self.follow_up_duration <= timedelta()
            or self.follow_up_duration % Timeframe.HOURS_4.duration
        ):
            raise RangeMeanReversionScreenError("report configuration is invalid")
        keys = tuple(item.identity.sort_key for item in self.observations)
        if keys != tuple(sorted(set(keys))):
            raise RangeMeanReversionScreenError("observations are not canonical and unique")
        for item in self.observations:
            item.validate()
            if (
                item.replay_end - item.identity.observed_at != self.follow_up_duration
                or item.result.execution_costs != self.execution_costs
                or item.result.entry_chronology_policy_version
                != self.entry_chronology_policy_version
                or item.result.partial_evidence_policy_version
                != self.partial_evidence_policy_version
            ):
                raise RangeMeanReversionScreenError("observation does not match report protocol")
        outcomes = _outcomes(self.observations)
        population = _population(self.observations)
        expected = (
            population,
            outcomes,
            _economics(outcomes),
            _losing_streak(outcomes),
            _strata(outcomes),
            _geometry(self.observations),
            _overlap(self.observations),
            _is_evaluable(population.closed_count, population.closure_rate.value),
        )
        actual = (
            self.population,
            self.outcomes,
            self.economics,
            self.maximum_chronological_losing_streak,
            self.strata,
            self.geometry,
            self.overlap_audit,
            self.economically_evaluable,
        )
        if expected != actual or self.unit_status != _unit_status(
            self.economics, self.economically_evaluable
        ):
            raise RangeMeanReversionScreenError("report metrics differ from sealed observations")

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


class RangeMeanReversionSignalEdgeRunner:
    """Extend the same causal context timeline and replay each admissible identity."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def run(
        self,
        corpus: RangeMeanReversionHistoricalResearchCorpus,
        follow_up_duration: timedelta,
        costs: ExecutionCostConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> RangeMeanReversionSignalEdgeReport:
        corpus.validate()
        if any(is_locked_weex_source(s) for s in corpus.configuration.source_ids) and (
            not isinstance(self._store, LockedWeexResearchCorpusStore)
            or corpus.configuration.source_ids != (self._store.source_id,)
        ):
            raise RangeMeanReversionScreenError("qualified WEEX history requires its locked store")
        if follow_up_duration <= timedelta() or follow_up_duration % Timeframe.HOURS_4.duration:
            raise RangeMeanReversionScreenError("follow-up duration must align to 4h")
        extended = RangeMeanReversionHistoricalCorpusGenerator(self._store).generate(
            replace(corpus.configuration, end=corpus.configuration.end + follow_up_duration),
            replay_speed=replay_speed,
        )
        prefix = tuple(
            item
            for item in extended.historical_contexts
            if item.context.observed_at <= corpus.configuration.end
        )
        if prefix != corpus.historical_contexts:
            raise RangeMeanReversionScreenError("extended causal prefix differs from corpus")
        admitted = set(corpus.research_admissible_entry_identities)
        by_id = {item.identity: item for item in corpus.decisions if item.identity in admitted}
        if len(by_id) != len(corpus.research_admissible_entry_identities):
            raise RangeMeanReversionScreenError("admissible identity is duplicated")
        replay = RangeMeanReversionMidpointReplayRunner(HistoricalExecutionSource(self._store))
        observations: list[RangeMeanReversionObservation] = []
        for identity in corpus.research_admissible_entry_identities:
            decision = by_id[identity]
            replay_end = identity.observed_at + follow_up_duration
            contexts = tuple(
                item
                for item in extended.historical_contexts
                if item.context.source_id == identity.source_id
                and item.context.symbol == identity.symbol
                and identity.observed_at < item.context.observed_at < replay_end
            )
            result = replay.run(
                decision.decision,
                contexts,
                ExecutionSeries(
                    SourceId(identity.source_id.value), identity.symbol, Timeframe.MINUTES_15
                ),
                costs,
                end=replay_end,
            )
            observations.append(RangeMeanReversionObservation(decision, replay_end, result))
        return RangeMeanReversionSignalEdgeReport(
            corpus.corpus_id, corpus.corpus_digest, follow_up_duration, costs, tuple(observations)
        )


def _population(items: tuple[RangeMeanReversionObservation, ...]) -> RangeMeanReversionPopulation:
    results = tuple(item.result for item in items)
    invalid = tuple(
        item
        for item in results
        if item.status is RangeMeanReversionReplayStatus.ENTRY_FILL_INVALIDATED
    )
    replayed = tuple(
        item for item in results if item.status is RangeMeanReversionReplayStatus.REPLAYED
    )
    closed = sum(item.position_closed for item in replayed)
    return RangeMeanReversionPopulation(
        len(results),
        sum(item.status is RangeMeanReversionReplayStatus.ENTRY_UNFILLED for item in results),
        len(invalid),
        sum(
            item.entry_fill_invalidation is not None
            and RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL
            in item.entry_fill_invalidation.reasons
            for item in invalid
        ),
        sum(
            item.entry_fill_invalidation is not None
            and RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL
            in item.entry_fill_invalidation.reasons
            for item in invalid
        ),
        len(replayed),
        closed,
        len(replayed) - closed,
        closed,
        _rate(closed, len(replayed)),
        sum(item.exit_cause is RangeMeanReversionExitCause.PROTECTIVE_STOP for item in replayed),
        sum(
            item.exit_cause is RangeMeanReversionExitCause.RANGE_MIDPOINT_TARGET
            for item in replayed
        ),
        sum(item.same_bar_stop_precedence for item in replayed),
        sum(item.exit_unfilled is not None for item in replayed),
    )


def _outcomes(
    items: tuple[RangeMeanReversionObservation, ...],
) -> tuple[CandidateEconomicOutcome, ...]:
    values: list[CandidateEconomicOutcome] = []
    for item in items:
        result = item.result
        if not result.position_closed:
            continue
        if result.exit_fill is None or result.realized_net_pnl is None:
            raise RangeMeanReversionScreenError("closed position has no authoritative outcome")
        values.append(
            CandidateEconomicOutcome(
                item.identity,
                "LONG" if result.entry_intent.side is EntrySide.BUY else "SHORT",
                item.identity.observed_at.strftime("%Y-%m"),
                result.exit_fill.fill_at,
                result.realized_net_pnl,
                result.entry_intent.approved_risk_quote,
            )
        )
    return tuple(values)


def _geometry(
    items: tuple[RangeMeanReversionObservation, ...],
) -> RangeMeanReversionGeometrySummary:
    replayed = tuple(
        item.result
        for item in items
        if item.result.status is RangeMeanReversionReplayStatus.REPLAYED
    )
    targets = tuple(item.target_distance for item in replayed)
    stops = tuple(item.stop_distance for item in replayed)
    ratios = tuple(item.geometric_reward_risk for item in replayed)
    if any(not isinstance(x, Decimal) for x in (*targets, *stops, *ratios)):
        raise RangeMeanReversionScreenError("replayed geometry is unavailable")
    # Values are Decimal after replay validation; retain exact arithmetic.
    decimal_targets = tuple(x for x in targets if isinstance(x, Decimal))
    decimal_stops = tuple(x for x in stops if isinstance(x, Decimal))
    decimal_ratios = tuple(x for x in ratios if isinstance(x, Decimal))
    with localcontext(_DECIMAL_CONTEXT):
        median_ratio = median(decimal_ratios) if decimal_ratios else None
    return RangeMeanReversionGeometrySummary(
        len(replayed),
        decimal_targets,
        decimal_stops,
        decimal_ratios,
        median_ratio,
        min(decimal_ratios, default=None),
        max(decimal_ratios, default=None),
    )


def _overlap(items: tuple[RangeMeanReversionObservation, ...]) -> RangeMeanReversionOverlapAudit:
    groups: dict[tuple[str, str], list[tuple[datetime, datetime, DecisionIdentity]]] = {}
    for item in items:
        result = item.result
        if (
            result.status is not RangeMeanReversionReplayStatus.REPLAYED
            or result.entry_fill is None
        ):
            continue
        end = result.exit_fill.fill_at if result.exit_fill is not None else item.replay_end
        groups.setdefault((item.identity.source_id.value, str(item.identity.symbol)), []).append(
            (result.entry_fill.fill_at, end, item.identity)
        )
    components: list[tuple[DecisionIdentity, ...]] = []
    for key in sorted(groups):
        current: list[DecisionIdentity] = []
        current_end: datetime | None = None
        for start, end, identity in sorted(groups[key], key=lambda row: (row[0], row[2].sort_key)):
            if current and current_end is not None and start >= current_end:
                components.append(tuple(current))
                current = []
            current.append(identity)
            current_end = max(current_end, end) if current_end else end
        if current:
            components.append(tuple(current))
    return RangeMeanReversionOverlapAudit(
        sum(map(len, components)),
        len(components),
        max(map(len, components), default=0),
        sum(len(item) > 1 for item in components),
        tuple(components),
    )


def _unit_status(
    economics: CandidateAbsoluteEconomics, evaluable: bool
) -> RangeMeanReversionUnitStatus:
    if not evaluable:
        return RangeMeanReversionUnitStatus.INSUFFICIENT
    mean, med, factor = economics.mean_net_r, economics.median_net_r, economics.profit_factor
    if mean is None or med is None:
        raise RangeMeanReversionScreenError("evaluable run has no outcomes")
    if (
        mean > 0
        and (
            economics.profit_factor_state is ProfitFactorState.INFINITE
            or (factor is not None and factor > 1)
        )
        and med >= 0
    ):
        return RangeMeanReversionUnitStatus.POSITIVE
    if mean <= 0 and factor is not None and factor <= 1:
        return RangeMeanReversionUnitStatus.NEGATIVE
    return RangeMeanReversionUnitStatus.MIXED


def classify_screen(
    units: tuple[RangeMeanReversionUnitStatus, ...],
) -> RangeMeanReversionScreenStatus:
    if len(units) != 3:
        raise RangeMeanReversionScreenError("screen requires exactly A/B/C")
    if RangeMeanReversionUnitStatus.INSUFFICIENT in units:
        return RangeMeanReversionScreenStatus.INSUFFICIENT
    if (
        units.count(RangeMeanReversionUnitStatus.POSITIVE) >= 2
        and RangeMeanReversionUnitStatus.NEGATIVE not in units
    ):
        return RangeMeanReversionScreenStatus.POSITIVE
    if units.count(RangeMeanReversionUnitStatus.NEGATIVE) >= 2:
        return RangeMeanReversionScreenStatus.NEGATIVE
    return RangeMeanReversionScreenStatus.MIXED


def _check_frozen_store(
    store: LockedWeexResearchCorpusStore, definition: RangeMeanReversionRunDefinition
) -> None:
    if not isinstance(store, LockedWeexResearchCorpusStore):
        raise RangeMeanReversionScreenError("only locked WEEX corpus store is authorized")
    if (
        definition not in FROZEN_RUNS
        or store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
    ):
        raise RangeMeanReversionScreenError("unknown dataset or run identity")


def validate_frozen_run(
    store: LockedWeexResearchCorpusStore,
    corpus: RangeMeanReversionHistoricalResearchCorpus,
    report: RangeMeanReversionSignalEdgeReport,
    definition: RangeMeanReversionRunDefinition,
) -> None:
    if report.schema_version != _FROZEN_SCHEMA:
        raise RangeMeanReversionScreenError("new chronology report is not frozen V1 evidence")
    _check_frozen_store(store, definition)
    corpus.validate()
    report.validate()
    expected = RangeMeanReversionResearchConfiguration(
        (SOURCE_ID,),
        (definition.symbol,),
        definition.start,
        definition.end,
        RESEARCH_EQUITY,
        warmup_exclusions=locked_warmup_exclusions(store, (definition.symbol,)),
    )
    if (
        corpus.configuration != expected
        or report.source_corpus_id != corpus.corpus_id
        or report.source_corpus_digest != corpus.corpus_digest
        or report.follow_up_duration != FOLLOW_UP
        or report.execution_costs != COSTS
        or tuple(item.identity for item in report.observations)
        != corpus.research_admissible_entry_identities
    ):
        raise RangeMeanReversionScreenError("report differs from frozen run protocol")


def run_predeclared_unit(
    store: LockedWeexResearchCorpusStore, definition: RangeMeanReversionRunDefinition
) -> tuple[RangeMeanReversionHistoricalResearchCorpus, RangeMeanReversionSignalEdgeReport]:
    """Future-only entry point; this issue never invokes it on the real corpus."""
    _check_frozen_store(store, definition)
    if _SCHEMA != _FROZEN_SCHEMA:
        raise RangeMeanReversionScreenError("frozen V1 run cannot execute under new chronology")
    config = RangeMeanReversionResearchConfiguration(
        (SOURCE_ID,),
        (definition.symbol,),
        definition.start,
        definition.end,
        RESEARCH_EQUITY,
        warmup_exclusions=locked_warmup_exclusions(store, (definition.symbol,)),
    )
    corpus = RangeMeanReversionHistoricalCorpusGenerator(store).generate(config)
    report = RangeMeanReversionSignalEdgeRunner(store).run(corpus, FOLLOW_UP, COSTS)
    validate_frozen_run(store, corpus, report, definition)
    return corpus, report


@dataclass(frozen=True, slots=True)
class RangeMeanReversionScreenProjection:
    run_ids: tuple[str, str, str]
    report_digests: tuple[str, str, str]
    unit_statuses: tuple[
        RangeMeanReversionUnitStatus, RangeMeanReversionUnitStatus, RangeMeanReversionUnitStatus
    ]
    status: RangeMeanReversionScreenStatus
    schema_version: str = field(init=False, default=_FROZEN_SCHEMA)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.run_ids != ("A", "B", "C") or self.status != classify_screen(self.unit_statuses):
            raise RangeMeanReversionScreenError("screen identity or status differs")
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal("range-screen", self, excluded_names=frozenset({"integrity_seal"})),
        )

    def validate(self) -> None:
        if (
            self.schema_version != _FROZEN_SCHEMA
            or self.run_ids != ("A", "B", "C")
            or self.status != classify_screen(self.unit_statuses)
            or not compare_digest(
                self.integrity_seal,
                _integrity_seal("range-screen", self, excluded_names=frozenset({"integrity_seal"})),
            )
        ):
            raise RangeMeanReversionScreenError("screen integrity differs")


def project_frozen_screen(
    units: tuple[
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeMeanReversionSignalEdgeReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeMeanReversionSignalEdgeReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeMeanReversionSignalEdgeReport,
        ],
    ],
) -> RangeMeanReversionScreenProjection:
    for definition, (store, corpus, report) in zip(FROZEN_RUNS, units, strict=True):
        validate_frozen_run(store, corpus, report, definition)
    statuses = (units[0][2].unit_status, units[1][2].unit_status, units[2][2].unit_status)
    return RangeMeanReversionScreenProjection(
        ("A", "B", "C"),
        (units[0][2].report_digest, units[1][2].report_digest, units[2][2].report_digest),
        statuses,
        classify_screen(statuses),
    )
