"""Frozen, research-only Breakout + Retest 2R signal-edge screen."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hmac import compare_digest
from json import dumps

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
from adaptive_trading_bot.research.breakout_retest_corpus import (
    BreakoutRetestDecisionObservation,
    BreakoutRetestHistoricalCorpusGenerator,
    BreakoutRetestHistoricalResearchCorpus,
    BreakoutRetestResearchConfiguration,
)
from adaptive_trading_bot.research.breakout_retest_replay import (
    BreakoutRetestExitCause,
    BreakoutRetestFixed2RReplayRunner,
    BreakoutRetestReplayResult,
    BreakoutRetestReplayStatus,
)
from adaptive_trading_bot.research.paired_exit_replay import EntryIntentProvenance
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

_SCHEMA = "breakout-retest-signal-edge-v2"
_FROZEN_SCHEMA = "breakout-retest-signal-edge-v1"
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


class BreakoutRetestScreenError(ValueError):
    """Locked protocol, report, or screen authority failed closed."""


class BreakoutRetestUnitStatus(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class BreakoutRetestScreenStatus(StrEnum):
    POSITIVE = "BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_POSITIVE"
    NEGATIVE = "BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_NEGATIVE"
    MIXED = "BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_MIXED"
    INSUFFICIENT = "BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class BreakoutRetestRunDefinition:
    run_id: str
    symbol: TradingSymbol
    start: datetime
    end: datetime
    follow_up_boundary: datetime

    def __post_init__(self) -> None:
        if self.run_id not in {"A", "B", "C"} or self.follow_up_boundary != self.end + FOLLOW_UP:
            raise BreakoutRetestScreenError("invalid predeclared run")


FROZEN_RUNS = (
    BreakoutRetestRunDefinition(
        "A",
        TradingSymbol("BTC", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
    BreakoutRetestRunDefinition(
        "B",
        TradingSymbol("ETH", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 12, 25, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ),
    BreakoutRetestRunDefinition(
        "C",
        TradingSymbol("ETH", "USDT"),
        datetime(2025, 4, 2, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
)


@dataclass(frozen=True, slots=True)
class BreakoutRetestObservation:
    decision: BreakoutRetestDecisionObservation
    replay_end: datetime
    result: BreakoutRetestReplayResult
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-observation", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    @property
    def identity(self) -> DecisionIdentity:
        return self.decision.identity

    def validate(self) -> None:
        if not isinstance(self.integrity_seal, str) or not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-observation", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise BreakoutRetestScreenError("observation seal mismatch")
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
            or self.result.decision_identity != self.identity
            or self.result.end != self.replay_end
            or self.replay_end <= self.identity.observed_at
        ):
            raise BreakoutRetestScreenError("observation authority differs")


@dataclass(frozen=True, slots=True)
class BreakoutRetestPopulation:
    raw_candidate_count: int
    entry_unfilled_count: int
    entry_fill_invalidated_count: int
    replayed_count: int
    closed_count: int
    unresolved_count: int
    economic_outcome_denominator: int
    closure_rate: RobustnessRate
    protective_stop_count: int
    fixed_2r_target_count: int
    same_bar_stop_precedence_count: int
    exit_unfilled_count: int


@dataclass(frozen=True, slots=True)
class BreakoutRetestOverlapAudit:
    replayed_count: int
    component_count: int
    largest_component_size: int
    overlapping_component_count: int
    component_member_identities: tuple[tuple[DecisionIdentity, ...], ...]


@dataclass(frozen=True, slots=True)
class BreakoutRetestSignalEdgeReport:
    source_corpus_id: str
    source_corpus_digest: str
    follow_up_duration: timedelta
    execution_costs: ExecutionCostConfiguration
    observations: tuple[BreakoutRetestObservation, ...]
    population: BreakoutRetestPopulation = field(init=False)
    outcomes: tuple[CandidateEconomicOutcome, ...] = field(init=False)
    economics: CandidateAbsoluteEconomics = field(init=False)
    maximum_chronological_losing_streak: int = field(init=False)
    strata: tuple[CandidateEconomicStratum, ...] = field(init=False)
    overlap_audit: BreakoutRetestOverlapAudit = field(init=False)
    economically_evaluable: bool = field(init=False)
    unit_status: BreakoutRetestUnitStatus = field(init=False)
    report_digest: str = field(init=False)
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
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
            ("overlap_audit", _overlap(self.observations)),
            ("economically_evaluable", evaluable),
            ("unit_status", _unit_status(economics, evaluable)),
        ):
            object.__setattr__(self, name, value)
        self._validate_facts()
        digest = _integrity_seal(
            "breakout-retest-report-digest",
            self,
            excluded_names=frozenset({"report_digest", "integrity_seal"}),
        )
        object.__setattr__(self, "report_digest", digest)
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not isinstance(self.integrity_seal, str) or not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise BreakoutRetestScreenError("report integrity mismatch")
        self._validate_facts()
        if self.report_digest != _integrity_seal(
            "breakout-retest-report-digest",
            self,
            excluded_names=frozenset({"report_digest", "integrity_seal"}),
        ):
            raise BreakoutRetestScreenError("report digest mismatch")

    def _validate_facts(self) -> None:
        self.execution_costs.validate()
        if (
            self.schema_version != _SCHEMA
            or self.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
            or self.is_trading_authority
            or self.is_portfolio_performance
            or self.dependence_warnings != DEPENDENCE_WARNINGS
            or not self.source_corpus_id
            or not self.source_corpus_digest
            or self.follow_up_duration <= timedelta()
            or self.follow_up_duration % Timeframe.HOURS_4.duration != timedelta()
        ):
            raise BreakoutRetestScreenError("report configuration is invalid")
        keys = tuple(item.identity.sort_key for item in self.observations)
        if keys != tuple(sorted(set(keys))):
            raise BreakoutRetestScreenError("observations are not canonical and unique")
        for item in self.observations:
            item.validate()
            if (
                item.replay_end - item.identity.observed_at != self.follow_up_duration
                or item.result.execution_costs != self.execution_costs
                or item.result.entry_chronology_policy_version
                != self.entry_chronology_policy_version
            ):
                raise BreakoutRetestScreenError("observation does not match report protocol")
        outcomes = _outcomes(self.observations)
        population = _population(self.observations)
        expected = (
            population,
            outcomes,
            _economics(outcomes),
            _losing_streak(outcomes),
            _strata(outcomes),
            _overlap(self.observations),
            _is_evaluable(population.closed_count, population.closure_rate.value),
        )
        actual = (
            self.population,
            self.outcomes,
            self.economics,
            self.maximum_chronological_losing_streak,
            self.strata,
            self.overlap_audit,
            self.economically_evaluable,
        )
        if expected != actual or self.unit_status != _unit_status(
            self.economics, self.economically_evaluable
        ):
            raise BreakoutRetestScreenError("report metrics differ from sealed observations")

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


class BreakoutRetestSignalEdgeRunner:
    """Extend the causal context timeline and replay every admissible identity."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def run(
        self,
        corpus: BreakoutRetestHistoricalResearchCorpus,
        follow_up_duration: timedelta,
        costs: ExecutionCostConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> BreakoutRetestSignalEdgeReport:
        corpus.validate()
        if any(is_locked_weex_source(source) for source in corpus.configuration.source_ids) and (
            not isinstance(self._store, LockedWeexResearchCorpusStore)
            or corpus.configuration.source_ids != (self._store.source_id,)
        ):
            raise BreakoutRetestScreenError("qualified WEEX history requires its locked store")
        if follow_up_duration <= timedelta() or follow_up_duration % Timeframe.HOURS_4.duration:
            raise BreakoutRetestScreenError("follow-up duration must align to 4h")
        extended = BreakoutRetestHistoricalCorpusGenerator(self._store).generate(
            replace(corpus.configuration, end=corpus.configuration.end + follow_up_duration),
            replay_speed=replay_speed,
        )
        prefix = tuple(
            item
            for item in extended.historical_contexts
            if item.context.observed_at <= corpus.configuration.end
        )
        if prefix != corpus.historical_contexts:
            raise BreakoutRetestScreenError("extended causal prefix differs from corpus")
        by_id = {
            item.identity: item for item in corpus.decisions if item.is_research_admissible_entry
        }
        if len(by_id) != len(corpus.research_admissible_entry_identities):
            raise BreakoutRetestScreenError("admissible identity is duplicated")
        replay = BreakoutRetestFixed2RReplayRunner(HistoricalExecutionSource(self._store))
        observations: list[BreakoutRetestObservation] = []
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
            observations.append(BreakoutRetestObservation(decision, replay_end, result))
        return BreakoutRetestSignalEdgeReport(
            corpus.corpus_id, corpus.corpus_digest, follow_up_duration, costs, tuple(observations)
        )


def _population(items: tuple[BreakoutRetestObservation, ...]) -> BreakoutRetestPopulation:
    results = tuple(item.result for item in items)
    unfilled = sum(item.status is BreakoutRetestReplayStatus.ENTRY_UNFILLED for item in results)
    invalid = sum(
        item.status is BreakoutRetestReplayStatus.ENTRY_FILL_INVALIDATED for item in results
    )
    replayed = tuple(item for item in results if item.status is BreakoutRetestReplayStatus.REPLAYED)
    closed = sum(item.position_closed for item in replayed)
    return BreakoutRetestPopulation(
        len(results),
        unfilled,
        invalid,
        len(replayed),
        closed,
        len(replayed) - closed,
        closed,
        _rate(closed, len(replayed)),
        sum(item.exit_cause is BreakoutRetestExitCause.PROTECTIVE_STOP for item in replayed),
        sum(item.exit_cause is BreakoutRetestExitCause.FIXED_2R_TARGET for item in replayed),
        sum(item.same_bar_stop_precedence for item in replayed),
        sum(item.exit_unfilled is not None for item in replayed),
    )


def _outcomes(items: tuple[BreakoutRetestObservation, ...]) -> tuple[CandidateEconomicOutcome, ...]:
    values: list[CandidateEconomicOutcome] = []
    for item in items:
        result = item.result
        if not result.position_closed:
            continue
        if result.exit_fill is None or result.realized_net_pnl is None:
            raise BreakoutRetestScreenError("closed position has no authoritative outcome")
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


def _overlap(items: tuple[BreakoutRetestObservation, ...]) -> BreakoutRetestOverlapAudit:
    groups: dict[tuple[str, str], list[tuple[datetime, datetime, DecisionIdentity]]] = {}
    for item in items:
        result = item.result
        if result.status is not BreakoutRetestReplayStatus.REPLAYED or result.entry_fill is None:
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
    return BreakoutRetestOverlapAudit(
        sum(map(len, components)),
        len(components),
        max(map(len, components), default=0),
        sum(len(item) > 1 for item in components),
        tuple(components),
    )


def _unit_status(
    economics: CandidateAbsoluteEconomics, evaluable: bool
) -> BreakoutRetestUnitStatus:
    if not evaluable:
        return BreakoutRetestUnitStatus.INSUFFICIENT
    mean, median, factor = economics.mean_net_r, economics.median_net_r, economics.profit_factor
    if mean is None or median is None:
        raise BreakoutRetestScreenError("evaluable run has no outcomes")
    if (
        mean > 0
        and (
            economics.profit_factor_state is ProfitFactorState.INFINITE
            or (factor is not None and factor > 1)
        )
        and median >= 0
    ):
        return BreakoutRetestUnitStatus.POSITIVE
    if mean <= 0 and factor is not None and factor <= 1:
        return BreakoutRetestUnitStatus.NEGATIVE
    return BreakoutRetestUnitStatus.MIXED


def classify_screen(units: tuple[BreakoutRetestUnitStatus, ...]) -> BreakoutRetestScreenStatus:
    if len(units) != 3:
        raise BreakoutRetestScreenError("screen requires exactly A/B/C")
    if BreakoutRetestUnitStatus.INSUFFICIENT in units:
        return BreakoutRetestScreenStatus.INSUFFICIENT
    if (
        units.count(BreakoutRetestUnitStatus.POSITIVE) >= 2
        and BreakoutRetestUnitStatus.NEGATIVE not in units
    ):
        return BreakoutRetestScreenStatus.POSITIVE
    if units.count(BreakoutRetestUnitStatus.NEGATIVE) >= 2:
        return BreakoutRetestScreenStatus.NEGATIVE
    return BreakoutRetestScreenStatus.MIXED


def validate_frozen_run(
    store: LockedWeexResearchCorpusStore,
    corpus: BreakoutRetestHistoricalResearchCorpus,
    report: BreakoutRetestSignalEdgeReport,
    definition: BreakoutRetestRunDefinition,
) -> None:
    """Gate future execution to exact reviewed store, identity, window, and costs."""
    if report.schema_version != _FROZEN_SCHEMA:
        raise BreakoutRetestScreenError("new chronology report is not frozen V1 evidence")
    if not isinstance(store, LockedWeexResearchCorpusStore):
        raise BreakoutRetestScreenError("only locked WEEX corpus store is authorized")
    if (
        definition not in FROZEN_RUNS
        or store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
    ):
        raise BreakoutRetestScreenError("unknown dataset or run identity")
    corpus.validate()
    report.validate()
    expected = BreakoutRetestResearchConfiguration(
        (SOURCE_ID,), (definition.symbol,), definition.start, definition.end, RESEARCH_EQUITY
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
        raise BreakoutRetestScreenError("report differs from frozen run protocol")


def run_predeclared_unit(
    store: LockedWeexResearchCorpusStore,
    definition: BreakoutRetestRunDefinition,
) -> tuple[BreakoutRetestHistoricalResearchCorpus, BreakoutRetestSignalEdgeReport]:
    """Future-only entry point: reject non-locked stores before reading a candle."""
    if not isinstance(store, LockedWeexResearchCorpusStore):
        raise BreakoutRetestScreenError("predeclared run requires locked WEEX corpus store")
    if (
        definition not in FROZEN_RUNS
        or store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
    ):
        raise BreakoutRetestScreenError("predeclared run identity is not qualified")
    if _SCHEMA != _FROZEN_SCHEMA:
        raise BreakoutRetestScreenError("frozen V1 run cannot execute under new chronology")
    configuration = BreakoutRetestResearchConfiguration(
        (SOURCE_ID,), (definition.symbol,), definition.start, definition.end, RESEARCH_EQUITY
    )
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(configuration)
    report = BreakoutRetestSignalEdgeRunner(store).run(corpus, FOLLOW_UP, COSTS)
    validate_frozen_run(store, corpus, report, definition)
    return corpus, report


@dataclass(frozen=True, slots=True)
class BreakoutRetestScreenProjection:
    """Exactly three sealed, predeclared units; no economic authority of its own."""

    run_ids: tuple[str, str, str]
    report_digests: tuple[str, str, str]
    unit_statuses: tuple[
        BreakoutRetestUnitStatus, BreakoutRetestUnitStatus, BreakoutRetestUnitStatus
    ]
    status: BreakoutRetestScreenStatus
    schema_version: str = field(init=False, default=_FROZEN_SCHEMA)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.run_ids != ("A", "B", "C") or self.status != classify_screen(self.unit_statuses):
            raise BreakoutRetestScreenError("screen identity or status differs")
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-screen", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if (
            self.schema_version != _FROZEN_SCHEMA
            or self.run_ids != ("A", "B", "C")
            or self.status != classify_screen(self.unit_statuses)
            or not compare_digest(
                self.integrity_seal,
                _integrity_seal(
                    "breakout-retest-screen", self, excluded_names=frozenset({"integrity_seal"})
                ),
            )
        ):
            raise BreakoutRetestScreenError("screen integrity differs")


def project_frozen_screen(
    units: tuple[
        tuple[
            LockedWeexResearchCorpusStore,
            BreakoutRetestHistoricalResearchCorpus,
            BreakoutRetestSignalEdgeReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            BreakoutRetestHistoricalResearchCorpus,
            BreakoutRetestSignalEdgeReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            BreakoutRetestHistoricalResearchCorpus,
            BreakoutRetestSignalEdgeReport,
        ],
    ],
) -> BreakoutRetestScreenProjection:
    for definition, (store, corpus, report) in zip(FROZEN_RUNS, units, strict=True):
        validate_frozen_run(store, corpus, report, definition)
    statuses = (
        units[0][2].unit_status,
        units[1][2].unit_status,
        units[2][2].unit_status,
    )
    return BreakoutRetestScreenProjection(
        ("A", "B", "C"),
        (units[0][2].report_digest, units[1][2].report_digest, units[2][2].report_digest),
        statuses,
        classify_screen(statuses),
    )
