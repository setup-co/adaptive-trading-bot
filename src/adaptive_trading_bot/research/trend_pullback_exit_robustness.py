"""Corpus-wide, fixed-horizon paired Trend Pullback exit robustness research."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import Enum, StrEnum
from hashlib import sha256
from hmac import compare_digest
from json import dumps
from pathlib import Path

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.config import get_settings
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.domain.entry_chronology import CAUSAL_ENTRY_CHRONOLOGY_V2
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import (
    EntryOrderIntentAuthorizer,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.replay import ReplayError, ReplaySpeed
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryIntentProvenance,
    HistoricalExitCause,
    PairedExitComparisonState,
    PairedExitReplayStatus,
    PairedTrendPullbackExitReplayResult,
    PairedTrendPullbackExitReplayRunner,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    HistoricalTrendPullbackDecisionObservation,
    ResearchAdmissionConfiguration,
    TrendPullbackHistoricalCorpusGenerator,
    TrendPullbackHistoricalResearchCorpus,
    TrendPullbackResearchCorpusError,
)
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    SourceId,
)

_CONFIGURATION_SCHEMA_VERSION = "3.0.0"
_OBSERVATION_SCHEMA_VERSION = "3.0.0"
_REPORT_SCHEMA_VERSION = "3.0.0"
_ARTIFACT_SCHEMA_VERSION = "3.0.0"
_STRATIFICATION_POLICY_VERSION = "2.0.0"
_OVERLAP_POLICY_VERSION = "1.0.0"
_INTEGRITY_SEAL_VERSION = "1.0.0"
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class TrendPullbackExitRobustnessError(ValueError):
    """Corpus, history, paired authority, or report integrity failed closed."""


class RobustnessStratificationDimension(StrEnum):
    """Required descriptive breakdowns; none are ranking authorities."""

    SYMBOL = "SYMBOL"
    ENTRY_SIDE = "ENTRY_SIDE"
    REGIME_15M = "REGIME_15M"
    HIGHER_TIMEFRAME_BIAS = "HIGHER_TIMEFRAME_BIAS"
    MTF_ALIGNMENT = "MTF_ALIGNMENT"
    UTC_ENTRY_MONTH = "UTC_ENTRY_MONTH"


@dataclass(frozen=True, slots=True)
class RobustnessRate:
    """Lossless descriptive rate with explicit count provenance."""

    numerator: int
    denominator: int
    value: Decimal | None

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 0 or self.numerator > self.denominator:
            raise TrendPullbackExitRobustnessError("robustness rate counts are invalid")
        if self.denominator == 0:
            if self.value is not None:
                raise TrendPullbackExitRobustnessError("empty robustness rate must be unavailable")
        elif self.value is None or not self.value.is_finite() or not Decimal(0) <= self.value <= 1:
            raise TrendPullbackExitRobustnessError("robustness rate value is invalid")


@dataclass(frozen=True, slots=True)
class TrendPullbackExitRobustnessConfiguration:
    """Versioned fixed-horizon provenance for one robustness report."""

    source_corpus_id: str
    source_corpus_digest: str
    execution_costs: ExecutionCostConfiguration
    follow_up_duration: timedelta
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
    configuration_schema_version: str = field(init=False, default=_CONFIGURATION_SCHEMA_VERSION)
    report_schema_version: str = field(init=False, default=_REPORT_SCHEMA_VERSION)
    stratification_policy_version: str = field(init=False, default=_STRATIFICATION_POLICY_VERSION)
    overlap_clustering_policy_version: str = field(init=False, default=_OVERLAP_POLICY_VERSION)
    configuration_digest: str = field(init=False)
    configuration_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_costs", deepcopy(self.execution_costs))
        _validate_configuration(self)
        digest = _seal(
            "exit-robustness-configuration-digest",
            self,
            {"configuration_digest", "configuration_integrity_seal"},
        )
        object.__setattr__(self, "configuration_digest", digest)
        object.__setattr__(
            self,
            "configuration_integrity_seal",
            _seal("exit-robustness-configuration", self, {"configuration_integrity_seal"}),
        )

    def validate(self) -> None:
        """Revalidate the complete originally accepted configuration."""

        expected_digest = _seal(
            "exit-robustness-configuration-digest",
            self,
            {"configuration_digest", "configuration_integrity_seal"},
        )
        if self.configuration_digest != expected_digest:
            raise TrendPullbackExitRobustnessError("robustness configuration digest mismatch")
        expected_seal = _seal(
            "exit-robustness-configuration", self, {"configuration_integrity_seal"}
        )
        if not isinstance(self.configuration_integrity_seal, str) or not compare_digest(
            self.configuration_integrity_seal, expected_seal
        ):
            raise TrendPullbackExitRobustnessError("robustness configuration integrity mismatch")
        _validate_configuration(self)


@dataclass(frozen=True, slots=True)
class PairedExitRobustnessObservation:
    """Sealed paired result and its exact corpus/context provenance."""

    source_corpus_digest: str
    configuration_digest: str
    decision: HistoricalTrendPullbackDecisionObservation
    replay_end: datetime
    paired_result: PairedTrendPullbackExitReplayResult
    symbol: TradingSymbol = field(init=False)
    entry_side: str = field(init=False)
    regime_15m: str = field(init=False)
    higher_timeframe_bias: str = field(init=False)
    mtf_alignment: str = field(init=False)
    utc_entry_month: str = field(init=False)
    observation_schema_version: str = field(init=False, default=_OBSERVATION_SCHEMA_VERSION)
    observation_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision", deepcopy(self.decision))
        object.__setattr__(self, "paired_result", deepcopy(self.paired_result))
        object.__setattr__(self, "replay_end", require_utc(self.replay_end, field="replay end"))
        context = self.decision.decision.context.multi_timeframe_regime
        object.__setattr__(self, "symbol", deepcopy(self.decision.symbol))
        object.__setattr__(
            self,
            "entry_side",
            self.decision.action.value.removesuffix("_CANDIDATE"),
        )
        object.__setattr__(self, "regime_15m", self.decision.entry_regime_label.value)
        object.__setattr__(self, "higher_timeframe_bias", self.decision.higher_timeframe_bias.value)
        object.__setattr__(self, "mtf_alignment", context.alignment.value)
        object.__setattr__(
            self,
            "utc_entry_month",
            self.decision.observed_at.strftime("%Y-%m"),
        )
        _validate_observation(self)
        object.__setattr__(
            self,
            "observation_integrity_seal",
            _seal("paired-exit-robustness-observation", self, {"observation_integrity_seal"}),
        )

    @property
    def identity(self) -> DecisionIdentity:
        return self.decision.identity

    def validate(self) -> None:
        """Fail closed after any raw mutation of nested authority."""

        expected = _seal("paired-exit-robustness-observation", self, {"observation_integrity_seal"})
        if not isinstance(self.observation_integrity_seal, str) or not compare_digest(
            self.observation_integrity_seal, expected
        ):
            raise TrendPullbackExitRobustnessError("paired observation integrity mismatch")
        _validate_observation(self)


@dataclass(frozen=True, slots=True)
class PairedDeltaSummary:
    """Descriptive authoritative deltas for BOTH_CLOSED pairs only."""

    comparable_count: int
    mean_net_pnl_delta: Decimal | None
    median_net_pnl_delta: Decimal | None
    minimum_net_pnl_delta: Decimal | None
    maximum_net_pnl_delta: Decimal | None
    positive_count: int
    zero_count: int
    negative_count: int
    positive_rate: RobustnessRate
    zero_rate: RobustnessRate
    negative_rate: RobustnessRate
    mean_exit_fill_time_delta_seconds: Decimal | None
    median_exit_fill_time_delta_seconds: Decimal | None


@dataclass(frozen=True, slots=True)
class PairedPopulationSummary:
    """Population, censoring, closure, and comparability before numeric metrics."""

    candidate_count: int
    entry_filled_count: int
    entry_unfilled_count: int
    entry_fill_invalidated_count: int
    replayed_count: int
    both_closed_count: int
    both_open_count: int
    baseline_closed_candidate_open_count: int
    baseline_open_candidate_closed_count: int
    baseline_closed_count: int
    candidate_closed_count: int
    baseline_unresolved_count: int
    candidate_unresolved_count: int
    baseline_protective_stop_exit_count: int
    candidate_protective_stop_exit_count: int
    candidate_ema50_exit_count: int
    same_bar_protective_stop_precedence_count: int
    baseline_exit_unfilled_count: int
    candidate_exit_unfilled_count: int
    entry_filled_rate: RobustnessRate
    entry_fill_invalidated_rate: RobustnessRate
    baseline_closed_rate: RobustnessRate
    baseline_unresolved_rate: RobustnessRate
    candidate_closed_rate: RobustnessRate
    candidate_unresolved_rate: RobustnessRate
    comparable_fraction: RobustnessRate


@dataclass(frozen=True, slots=True)
class TrendPullbackExitRobustnessStratum:
    """One deterministic descriptive stratum with explicit zero-comparable state."""

    dimension: RobustnessStratificationDimension
    value: str
    population: PairedPopulationSummary
    deltas: PairedDeltaSummary


@dataclass(frozen=True, slots=True)
class ChronologicalStabilitySummary:
    """Exact UTC-month count/sign variation without activation interpretation."""

    month_count: int
    raw_candidate_counts: tuple[tuple[str, int], ...]
    comparable_counts: tuple[tuple[str, int], ...]
    median_delta_signs: tuple[tuple[str, str], ...]
    raw_candidate_count_varies: bool
    comparable_count_varies: bool
    comparable_median_sign_varies: bool


@dataclass(frozen=True, slots=True)
class OverlapComponent:
    """Connected [entry_fill, observation_end) intervals within source/symbol."""

    component_id: str
    source_id: SourceId
    symbol: TradingSymbol
    start: datetime
    end: datetime
    member_identities: tuple[DecisionIdentity, ...]
    comparable_count: int
    comparable_mean_net_pnl_delta: Decimal | None


@dataclass(frozen=True, slots=True)
class OverlapAudit:
    """Dependence audit; component balance is not an independence claim."""

    raw_replayed_candidate_count: int
    component_count: int
    largest_component_size: int
    median_component_size: Decimal | None
    singleton_component_count: int
    singleton_component_rate: RobustnessRate
    comparable_component_count: int
    equal_weight_component_mean_delta: Decimal | None
    median_component_mean_delta: Decimal | None
    components: tuple[OverlapComponent, ...]
    raw_candidate_count_is_independent_sample_size: bool = field(init=False, default=False)
    sensitivity_is_independence_proof: bool = field(init=False, default=False)


@dataclass(frozen=True, slots=True)
class TrendPullbackExitRobustnessReport:
    """Sealed corpus-wide paired report without portfolio or activation authority."""

    configuration: TrendPullbackExitRobustnessConfiguration
    source_corpus_id: str
    source_corpus_digest: str
    observations: tuple[PairedExitRobustnessObservation, ...]
    population: PairedPopulationSummary = field(init=False)
    deltas: PairedDeltaSummary = field(init=False)
    strata: tuple[TrendPullbackExitRobustnessStratum, ...] = field(init=False)
    chronological_stability: ChronologicalStabilitySummary = field(init=False)
    overlap_audit: OverlapAudit = field(init=False)
    report_digest: str = field(init=False)
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
    report_schema_version: str = field(init=False, default=_REPORT_SCHEMA_VERSION)
    is_trading_authority: bool = field(init=False, default=False)
    is_portfolio_performance: bool = field(init=False, default=False)
    contains_independence_claim: bool = field(init=False, default=False)
    report_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deepcopy(self.configuration))
        object.__setattr__(self, "observations", tuple(deepcopy(self.observations)))
        population, deltas, strata, chronology, overlap = _derive_report_facts(self.observations)
        object.__setattr__(self, "population", population)
        object.__setattr__(self, "deltas", deltas)
        object.__setattr__(self, "strata", strata)
        object.__setattr__(self, "chronological_stability", chronology)
        object.__setattr__(self, "overlap_audit", overlap)
        digest = _seal(
            "exit-robustness-report-digest", self, {"report_digest", "report_integrity_seal"}
        )
        object.__setattr__(self, "report_digest", digest)
        _validate_report(self)
        object.__setattr__(
            self,
            "report_integrity_seal",
            _seal("exit-robustness-report", self, {"report_integrity_seal"}),
        )

    def validate(self) -> None:
        """Recompute every total and fail closed after nested mutation."""

        expected = _seal("exit-robustness-report", self, {"report_integrity_seal"})
        if not isinstance(self.report_integrity_seal, str) or not compare_digest(
            self.report_integrity_seal, expected
        ):
            raise TrendPullbackExitRobustnessError("robustness report integrity mismatch")
        _validate_report(self)

    def to_json(self) -> str:
        """Return deterministic, lossless, machine-readable research artifact JSON."""

        self.validate()
        return dumps(
            {
                "artifact_schema_version": _ARTIFACT_SCHEMA_VERSION,
                "artifact_kind": "trend_pullback_exit_robustness_report",
                "research_only": True,
                "is_trading_authority": False,
                "is_portfolio_performance": False,
                "report": _canonical(self, set()),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class TrendPullbackExitRobustnessRunner:
    """Extend one accepted corpus and run every admissible paired experiment once."""

    __slots__ = ("_store",)

    def __init__(self, store: HistoricalCandleStore) -> None:
        if not hasattr(store, "read") or not hasattr(store, "scan_integrity"):
            raise TypeError("store must satisfy HistoricalCandleStore")
        self._store = store

    def run(
        self,
        corpus: TrendPullbackHistoricalResearchCorpus,
        configuration: TrendPullbackExitRobustnessConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> TrendPullbackExitRobustnessReport:
        """Run fixed-horizon paired experiments without portfolio interpretation."""

        if not isinstance(corpus, TrendPullbackHistoricalResearchCorpus):
            raise TypeError("corpus must be a TrendPullbackHistoricalResearchCorpus")
        if not isinstance(configuration, TrendPullbackExitRobustnessConfiguration):
            raise TypeError("configuration must be a robustness configuration")
        corpus.validate()
        configuration.validate()
        if (
            configuration.source_corpus_id != corpus.corpus_id
            or configuration.source_corpus_digest != corpus.corpus_digest
        ):
            raise TrendPullbackExitRobustnessError("configuration targets a different corpus")
        extended_end = corpus.configuration.end + configuration.follow_up_duration
        extended_configuration = replace(corpus.configuration, end=extended_end)
        extended_corpus = TrendPullbackHistoricalCorpusGenerator(self._store).generate(
            extended_configuration,
            replay_speed=replay_speed,
        )
        prefix = tuple(
            item
            for item in extended_corpus.historical_contexts
            if item.context.observed_at <= corpus.configuration.end
        )
        if prefix != corpus.historical_contexts:
            raise TrendPullbackExitRobustnessError(
                "extended historical context prefix differs from source corpus"
            )
        decisions_by_identity = {
            item.identity: item for item in corpus.decisions if item.is_research_admissible_entry
        }
        expected_identities = corpus.research_admissible_entry_identities
        if len(decisions_by_identity) != len(expected_identities) or set(
            decisions_by_identity
        ) != set(expected_identities):
            raise TrendPullbackExitRobustnessError(
                "admissible identities do not map exactly once to corpus decisions"
            )
        paired_runner = PairedTrendPullbackExitReplayRunner(HistoricalExecutionSource(self._store))
        observations: list[PairedExitRobustnessObservation] = []
        for identity in expected_identities:
            decision = decisions_by_identity[identity]
            replay_end = identity.observed_at + configuration.follow_up_duration
            contexts = tuple(
                item
                for item in extended_corpus.historical_contexts
                if item.context.source_id == identity.source_id
                and item.context.symbol == identity.symbol
                and identity.observed_at < item.context.observed_at < replay_end
            )
            result = paired_runner.run(
                decision.decision,
                contexts,
                ExecutionSeries(
                    SourceId(identity.source_id.value),
                    identity.symbol,
                    Timeframe.MINUTES_15,
                ),
                configuration.execution_costs,
                start=corpus.configuration.start,
                end=replay_end,
            )
            observations.append(
                PairedExitRobustnessObservation(
                    source_corpus_digest=corpus.corpus_digest,
                    configuration_digest=configuration.configuration_digest,
                    decision=decision,
                    replay_end=replay_end,
                    paired_result=result,
                )
            )
        ordered = tuple(sorted(observations, key=lambda item: item.identity.sort_key))
        if tuple(item.identity for item in ordered) != expected_identities:
            raise TrendPullbackExitRobustnessError(
                "paired observation population differs from admissible identities"
            )
        return TrendPullbackExitRobustnessReport(
            configuration=configuration,
            source_corpus_id=corpus.corpus_id,
            source_corpus_digest=corpus.corpus_digest,
            observations=ordered,
        )


def _validate_configuration(configuration: TrendPullbackExitRobustnessConfiguration) -> None:
    if (
        configuration.configuration_schema_version != _CONFIGURATION_SCHEMA_VERSION
        or configuration.report_schema_version != _REPORT_SCHEMA_VERSION
        or configuration.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
        or configuration.stratification_policy_version != _STRATIFICATION_POLICY_VERSION
        or configuration.overlap_clustering_policy_version != _OVERLAP_POLICY_VERSION
    ):
        raise TrendPullbackExitRobustnessError("robustness configuration version mismatch")
    if (
        not isinstance(configuration.source_corpus_id, str)
        or not configuration.source_corpus_id.strip()
        or not isinstance(configuration.source_corpus_digest, str)
        or not configuration.source_corpus_digest.strip()
    ):
        raise TrendPullbackExitRobustnessError("source corpus provenance must be non-empty")
    if not isinstance(configuration.execution_costs, ExecutionCostConfiguration):
        raise TypeError("execution_costs must be an ExecutionCostConfiguration")
    configuration.execution_costs.validate()
    duration = configuration.follow_up_duration
    if not isinstance(duration, timedelta) or duration <= timedelta():
        raise TrendPullbackExitRobustnessError("follow_up_duration must be positive")
    if duration % Timeframe.HOURS_4.duration != timedelta():
        raise TrendPullbackExitRobustnessError(
            "follow_up_duration must align to the V1 4h replay boundary"
        )


def _validate_observation(observation: PairedExitRobustnessObservation) -> None:
    if observation.observation_schema_version != _OBSERVATION_SCHEMA_VERSION:
        raise TrendPullbackExitRobustnessError("paired observation schema mismatch")
    observation.decision.validate()
    observation.paired_result.validate()
    if not observation.decision.is_research_admissible_entry:
        raise TrendPullbackExitRobustnessError("paired observation decision is not admissible")
    result = observation.paired_result
    authorization = EntryOrderIntentAuthorizer().authorize(observation.decision.decision)
    if (
        authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
        or authorization.intent is None
        or result.entry_intent != EntryIntentProvenance.from_authorized_intent(authorization.intent)
    ):
        raise TrendPullbackExitRobustnessError(
            "paired observation entry intent differs from decision authority"
        )
    if (
        not isinstance(observation.source_corpus_digest, str)
        or not observation.source_corpus_digest
        or not isinstance(observation.configuration_digest, str)
        or not observation.configuration_digest
        or result.originating_decision_identity != observation.identity
        or result.execution_series.source_id.value != observation.identity.source_id.value
        or result.execution_series.symbol != observation.identity.symbol
        or result.execution_series.timeframe is not Timeframe.MINUTES_15
        or not result.start <= observation.identity.observed_at < result.end
        or result.end != observation.replay_end
        or result.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
        or observation.replay_end <= observation.identity.observed_at
    ):
        raise TrendPullbackExitRobustnessError("paired observation provenance is inconsistent")
    context = observation.decision.decision.context.multi_timeframe_regime
    if (
        observation.symbol != observation.identity.symbol
        or observation.entry_side != observation.decision.action.value.removesuffix("_CANDIDATE")
        or observation.regime_15m != observation.decision.entry_regime_label.value
        or observation.higher_timeframe_bias != observation.decision.higher_timeframe_bias.value
        or observation.mtf_alignment != context.alignment.value
        or observation.utc_entry_month != observation.identity.observed_at.strftime("%Y-%m")
    ):
        raise TrendPullbackExitRobustnessError("paired observation stratum projection mismatch")


def _validate_report(report: TrendPullbackExitRobustnessReport) -> None:
    report.configuration.validate()
    if (
        report.report_schema_version != _REPORT_SCHEMA_VERSION
        or report.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2
        or report.configuration.entry_chronology_policy_version
        != report.entry_chronology_policy_version
        or report.is_trading_authority
        or report.is_portfolio_performance
        or report.contains_independence_claim
        or report.source_corpus_id != report.configuration.source_corpus_id
        or report.source_corpus_digest != report.configuration.source_corpus_digest
    ):
        raise TrendPullbackExitRobustnessError("report provenance or authority claim is invalid")
    if not isinstance(report.observations, tuple):
        raise TypeError("report observations must be an immutable tuple")
    for observation in report.observations:
        observation.validate()
        if (
            observation.source_corpus_digest != report.source_corpus_digest
            or observation.configuration_digest != report.configuration.configuration_digest
            or observation.paired_result.execution_costs != report.configuration.execution_costs
            or observation.paired_result.entry_chronology_policy_version
            != report.entry_chronology_policy_version
            or observation.replay_end - observation.identity.observed_at
            != report.configuration.follow_up_duration
        ):
            raise TrendPullbackExitRobustnessError("observation and report provenance differ")
    keys = tuple(item.identity.sort_key for item in report.observations)
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
        raise TrendPullbackExitRobustnessError("report observations are not canonical and unique")
    expected = _derive_report_facts(report.observations)
    if expected != (
        report.population,
        report.deltas,
        report.strata,
        report.chronological_stability,
        report.overlap_audit,
    ):
        raise TrendPullbackExitRobustnessError("report aggregate facts are inconsistent")
    digest = _seal(
        "exit-robustness-report-digest",
        report,
        {"report_digest", "report_integrity_seal"},
    )
    if report.report_digest != digest:
        raise TrendPullbackExitRobustnessError("report digest mismatch")


def _derive_report_facts(
    observations: tuple[PairedExitRobustnessObservation, ...],
) -> tuple[
    PairedPopulationSummary,
    PairedDeltaSummary,
    tuple[TrendPullbackExitRobustnessStratum, ...],
    ChronologicalStabilitySummary,
    OverlapAudit,
]:
    population = _population(observations)
    deltas = _deltas(observations)
    strata: list[TrendPullbackExitRobustnessStratum] = []
    dimensions = (
        (RobustnessStratificationDimension.SYMBOL, lambda item: str(item.symbol)),
        (RobustnessStratificationDimension.ENTRY_SIDE, lambda item: item.entry_side),
        (RobustnessStratificationDimension.REGIME_15M, lambda item: item.regime_15m),
        (
            RobustnessStratificationDimension.HIGHER_TIMEFRAME_BIAS,
            lambda item: item.higher_timeframe_bias,
        ),
        (RobustnessStratificationDimension.MTF_ALIGNMENT, lambda item: item.mtf_alignment),
        (
            RobustnessStratificationDimension.UTC_ENTRY_MONTH,
            lambda item: item.utc_entry_month,
        ),
    )
    for dimension, key_function in dimensions:
        grouped: dict[str, list[PairedExitRobustnessObservation]] = defaultdict(list)
        for observation in observations:
            grouped[key_function(observation)].append(observation)
        for value in sorted(grouped):
            members = tuple(grouped[value])
            strata.append(
                TrendPullbackExitRobustnessStratum(
                    dimension=dimension,
                    value=value,
                    population=_population(members),
                    deltas=_deltas(members),
                )
            )
    stratum_tuple = tuple(strata)
    months = tuple(
        item
        for item in stratum_tuple
        if item.dimension is RobustnessStratificationDimension.UTC_ENTRY_MONTH
    )
    raw_counts = tuple((item.value, item.population.candidate_count) for item in months)
    comparable_counts = tuple((item.value, item.deltas.comparable_count) for item in months)
    median_signs = tuple(
        (item.value, _decimal_sign(item.deltas.median_net_pnl_delta)) for item in months
    )
    chronology = ChronologicalStabilitySummary(
        month_count=len(months),
        raw_candidate_counts=raw_counts,
        comparable_counts=comparable_counts,
        median_delta_signs=median_signs,
        raw_candidate_count_varies=len({value for _, value in raw_counts}) > 1,
        comparable_count_varies=len({value for _, value in comparable_counts}) > 1,
        comparable_median_sign_varies=len({value for _, value in median_signs}) > 1,
    )
    return population, deltas, stratum_tuple, chronology, _overlap_audit(observations)


def _population(
    observations: tuple[PairedExitRobustnessObservation, ...],
) -> PairedPopulationSummary:
    results = tuple(item.paired_result for item in observations)
    replayed = tuple(item for item in results if item.status is PairedExitReplayStatus.REPLAYED)
    invalidated_count = sum(
        item.status is PairedExitReplayStatus.ENTRY_FILL_INVALIDATED for item in results
    )
    unfilled_count = sum(item.status is PairedExitReplayStatus.ENTRY_UNFILLED for item in results)
    if len(results) != unfilled_count + invalidated_count + len(replayed):
        raise TrendPullbackExitRobustnessError("paired population status partition is invalid")
    baseline_closed = sum(
        item.baseline is not None and item.baseline.position_closed for item in replayed
    )
    candidate_closed = sum(
        item.ema50_candidate is not None and item.ema50_candidate.position_closed
        for item in replayed
    )

    def state_count(state: PairedExitComparisonState) -> int:
        return sum(item.comparison_state is state for item in results)

    return PairedPopulationSummary(
        candidate_count=len(results),
        entry_filled_count=invalidated_count + len(replayed),
        entry_unfilled_count=unfilled_count,
        entry_fill_invalidated_count=invalidated_count,
        replayed_count=len(replayed),
        both_closed_count=state_count(PairedExitComparisonState.BOTH_CLOSED),
        both_open_count=state_count(PairedExitComparisonState.BOTH_OPEN),
        baseline_closed_candidate_open_count=state_count(
            PairedExitComparisonState.BASELINE_CLOSED_CANDIDATE_OPEN
        ),
        baseline_open_candidate_closed_count=state_count(
            PairedExitComparisonState.BASELINE_OPEN_CANDIDATE_CLOSED
        ),
        baseline_closed_count=baseline_closed,
        candidate_closed_count=candidate_closed,
        baseline_unresolved_count=len(replayed) - baseline_closed,
        candidate_unresolved_count=len(replayed) - candidate_closed,
        baseline_protective_stop_exit_count=sum(
            item.baseline is not None
            and item.baseline.winning_exit_cause is HistoricalExitCause.PROTECTIVE_STOP
            for item in replayed
        ),
        candidate_protective_stop_exit_count=sum(
            item.ema50_candidate is not None
            and item.ema50_candidate.winning_exit_cause is HistoricalExitCause.PROTECTIVE_STOP
            for item in replayed
        ),
        candidate_ema50_exit_count=sum(
            item.ema50_candidate is not None
            and item.ema50_candidate.winning_exit_cause is HistoricalExitCause.EMA50_THESIS_FAILURE
            for item in replayed
        ),
        same_bar_protective_stop_precedence_count=sum(
            item.ema50_candidate is not None
            and item.ema50_candidate.protective_stop_precedence_applied
            for item in replayed
        ),
        baseline_exit_unfilled_count=sum(
            item.baseline is not None and item.baseline.exit_unfilled is not None
            for item in replayed
        ),
        candidate_exit_unfilled_count=sum(
            item.ema50_candidate is not None and item.ema50_candidate.exit_unfilled is not None
            for item in replayed
        ),
        entry_filled_rate=_rate(invalidated_count + len(replayed), len(results)),
        entry_fill_invalidated_rate=_rate(invalidated_count, len(results)),
        baseline_closed_rate=_rate(baseline_closed, len(replayed)),
        baseline_unresolved_rate=_rate(len(replayed) - baseline_closed, len(replayed)),
        candidate_closed_rate=_rate(candidate_closed, len(replayed)),
        candidate_unresolved_rate=_rate(len(replayed) - candidate_closed, len(replayed)),
        comparable_fraction=_rate(
            state_count(PairedExitComparisonState.BOTH_CLOSED), len(replayed)
        ),
    )


def _deltas(observations: tuple[PairedExitRobustnessObservation, ...]) -> PairedDeltaSummary:
    comparable = tuple(
        item.paired_result
        for item in observations
        if item.paired_result.comparison_state is PairedExitComparisonState.BOTH_CLOSED
    )
    net = tuple(item.realized_net_pnl_delta_candidate_minus_baseline for item in comparable)
    times = tuple(item.exit_fill_time_delta_candidate_minus_baseline for item in comparable)
    if any(item is None for item in net) or any(item is None for item in times):
        raise TrendPullbackExitRobustnessError("BOTH_CLOSED pair lacks authoritative deltas")
    net_values = tuple(item for item in net if item is not None)
    time_seconds = tuple(_timedelta_seconds(item) for item in times if item is not None)
    count = len(net_values)
    positive = sum(item > 0 for item in net_values)
    zero = sum(item == 0 for item in net_values)
    negative = sum(item < 0 for item in net_values)
    return PairedDeltaSummary(
        comparable_count=count,
        mean_net_pnl_delta=_mean(net_values),
        median_net_pnl_delta=_median(net_values),
        minimum_net_pnl_delta=min(net_values, default=None),
        maximum_net_pnl_delta=max(net_values, default=None),
        positive_count=positive,
        zero_count=zero,
        negative_count=negative,
        positive_rate=_rate(positive, count),
        zero_rate=_rate(zero, count),
        negative_rate=_rate(negative, count),
        mean_exit_fill_time_delta_seconds=_mean(time_seconds),
        median_exit_fill_time_delta_seconds=_median(time_seconds),
    )


def _overlap_audit(
    observations: tuple[PairedExitRobustnessObservation, ...],
) -> OverlapAudit:
    replayed = tuple(
        item
        for item in observations
        if item.paired_result.status is PairedExitReplayStatus.REPLAYED
    )
    grouped: dict[tuple[SourceId, TradingSymbol], list[PairedExitRobustnessObservation]] = (
        defaultdict(list)
    )
    for item in replayed:
        grouped[(SourceId(item.identity.source_id.value), item.identity.symbol)].append(item)
    components: list[OverlapComponent] = []
    for (source_id, symbol), members in sorted(
        grouped.items(), key=lambda item: (item[0][0].value, str(item[0][1]))
    ):
        intervals = sorted(
            ((_interval(item), item) for item in members),
            key=lambda item: (item[0][0], item[1].identity.sort_key),
        )
        current: list[PairedExitRobustnessObservation] = []
        component_start: datetime | None = None
        component_end: datetime | None = None
        for (start, end), observation in intervals:
            if current and component_end is not None and start >= component_end:
                components.append(
                    _component(source_id, symbol, component_start, component_end, current)
                )
                current = []
                component_start = None
                component_end = None
            current.append(observation)
            component_start = start if component_start is None else min(component_start, start)
            component_end = end if component_end is None else max(component_end, end)
        if current and component_start is not None and component_end is not None:
            components.append(
                _component(source_id, symbol, component_start, component_end, current)
            )
    component_tuple = tuple(components)
    sizes = tuple(Decimal(len(item.member_identities)) for item in component_tuple)
    singletons = sum(len(item.member_identities) == 1 for item in component_tuple)
    comparable_means = tuple(
        item.comparable_mean_net_pnl_delta
        for item in component_tuple
        if item.comparable_mean_net_pnl_delta is not None
    )
    return OverlapAudit(
        raw_replayed_candidate_count=len(replayed),
        component_count=len(component_tuple),
        largest_component_size=max(
            (len(item.member_identities) for item in component_tuple), default=0
        ),
        median_component_size=_median(sizes),
        singleton_component_count=singletons,
        singleton_component_rate=_rate(singletons, len(component_tuple)),
        comparable_component_count=len(comparable_means),
        equal_weight_component_mean_delta=_mean(comparable_means),
        median_component_mean_delta=_median(comparable_means),
        components=component_tuple,
    )


def _interval(observation: PairedExitRobustnessObservation) -> tuple[datetime, datetime]:
    result = observation.paired_result
    if result.entry_fill is None:
        raise TrendPullbackExitRobustnessError("replayed observation lacks entry fill")
    end = observation.replay_end
    if result.comparison_state is PairedExitComparisonState.BOTH_CLOSED:
        assert result.baseline is not None and result.baseline.exit_fill is not None
        assert result.ema50_candidate is not None and result.ema50_candidate.exit_fill is not None
        end = max(result.baseline.exit_fill.fill_at, result.ema50_candidate.exit_fill.fill_at)
    if result.entry_fill.fill_at >= end:
        raise TrendPullbackExitRobustnessError("overlap observation interval is empty")
    return result.entry_fill.fill_at, end


def _component(
    source_id: SourceId,
    symbol: TradingSymbol,
    start: datetime | None,
    end: datetime | None,
    members: list[PairedExitRobustnessObservation],
) -> OverlapComponent:
    if start is None or end is None:
        raise TrendPullbackExitRobustnessError("overlap component lacks interval")
    ordered = tuple(sorted(members, key=lambda item: item.identity.sort_key))
    deltas = tuple(
        item.paired_result.realized_net_pnl_delta_candidate_minus_baseline
        for item in ordered
        if item.paired_result.comparison_state is PairedExitComparisonState.BOTH_CLOSED
    )
    if any(value is None for value in deltas):
        raise TrendPullbackExitRobustnessError("comparable overlap member lacks delta")
    values = tuple(value for value in deltas if value is not None)
    component_key = _seal(
        "exit-robustness-overlap-component",
        (source_id, symbol, start, end, tuple(item.identity for item in ordered)),
        set(),
    )
    return OverlapComponent(
        component_id=f"overlap-component:{component_key}",
        source_id=deepcopy(source_id),
        symbol=deepcopy(symbol),
        start=start,
        end=end,
        member_identities=tuple(deepcopy(item.identity) for item in ordered),
        comparable_count=len(values),
        comparable_mean_net_pnl_delta=_mean(values),
    )


def _rate(numerator: int, denominator: int) -> RobustnessRate:
    if denominator == 0:
        return RobustnessRate(numerator, denominator, None)
    with localcontext(_DECIMAL_CONTEXT):
        return RobustnessRate(numerator, denominator, Decimal(numerator) / denominator)


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    with localcontext(_DECIMAL_CONTEXT):
        return sum(values, Decimal(0)) / len(values)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = tuple(sorted(values))
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_DECIMAL_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / 2


def _timedelta_seconds(value: timedelta) -> Decimal:
    microseconds = (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds
    with localcontext(_DECIMAL_CONTEXT):
        return Decimal(microseconds) / Decimal(1_000_000)


def _decimal_sign(value: Decimal | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "ZERO"


def _seal(namespace: str, value: object, excluded: set[str]) -> str:
    payload = dumps(
        (
            f"adaptive-trading-bot/{namespace}",
            _INTEGRITY_SEAL_VERSION,
            _canonical(value, excluded),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical(value: object, excluded: set[str]) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return (
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical(value.value, excluded),
        )
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"), value.fold)
    if isinstance(value, timedelta):
        return ("timedelta", value.days, value.seconds, value.microseconds)
    if isinstance(value, Decimal):
        item = value.as_tuple()
        return ("decimal", item.sign, tuple(item.digits), str(item.exponent))
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, tuple):
        return ("tuple", tuple(_canonical(item, excluded) for item in value))
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            f"{type(value).__module__}.{type(value).__qualname__}",
            tuple(
                (item.name, _canonical(getattr(value, item.name), excluded))
                for item in fields(value)
                if item.name not in excluded
            ),
        )
    raise TrendPullbackExitRobustnessError(
        f"unsupported robustness integrity type {type(value).__qualname__}"
    )


def _parse_utc(value: str) -> datetime:
    return require_utc(
        datetime.fromisoformat(value.strip().replace("Z", "+00:00")),
        field="CLI timestamp",
    )


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Run research-only Trend Pullback exit robustness")
    parser.add_argument(
        "--data-root",
        type=Path,
        help="optional generic historical root override; never used for qualified WEEX history",
    )
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument(
        "--dataset-version",
        help=(
            "required for qualified WEEX source IDs; opens the immutable version under "
            "ATB_QUALIFIED_RESEARCH_CORPUS_ROOT"
        ),
    )
    parser.add_argument(
        "--symbol", action="append", choices=("BTC/USDT", "ETH/USDT"), required=True
    )
    parser.add_argument("--start", type=_parse_utc, required=True)
    parser.add_argument("--end", type=_parse_utc, required=True)
    parser.add_argument("--research-equity", type=Decimal, required=True)
    parser.add_argument("--follow-up-hours", type=int, required=True)
    parser.add_argument("--spread-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--fee-bps", type=Decimal, default=Decimal(0))
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate a deterministic report from an explicitly selected historical store."""

    from adaptive_trading_bot.historical_sources import (
        HistoricalSourceSelectionError,
        open_historical_store,
    )

    arguments = _parser().parse_args(argv)
    try:
        source_ids = tuple(SourceId(value) for value in arguments.source)
        settings = get_settings()
        store = open_historical_store(
            source_ids=source_ids,
            generic_root=arguments.data_root or settings.historical_market_data_root,
            qualified_corpus_root=settings.qualified_research_corpus_root,
            dataset_version=arguments.dataset_version,
        )
        admission = ResearchAdmissionConfiguration(
            source_ids=source_ids,
            symbols=tuple(
                TradingSymbol(*value.split("/", maxsplit=1)) for value in arguments.symbol
            ),
            start=arguments.start,
            end=arguments.end,
            research_equity_quote=arguments.research_equity,
        )
        corpus = TrendPullbackHistoricalCorpusGenerator(store).generate(admission)
        configuration = TrendPullbackExitRobustnessConfiguration(
            source_corpus_id=corpus.corpus_id,
            source_corpus_digest=corpus.corpus_digest,
            execution_costs=ExecutionCostConfiguration(
                spread_bps=arguments.spread_bps,
                slippage_bps=arguments.slippage_bps,
                fee_bps=arguments.fee_bps,
            ),
            follow_up_duration=timedelta(hours=arguments.follow_up_hours),
        )
        artifact = TrendPullbackExitRobustnessRunner(store).run(corpus, configuration).to_json()
        if arguments.output is None:
            print(artifact)
        else:
            arguments.output.write_text(artifact + "\n", encoding="utf-8")
    except (
        HistoricalSourceSelectionError,
        ReplayError,
        TrendPullbackExitRobustnessError,
        TrendPullbackResearchCorpusError,
        ValueError,
    ) as error:
        print(f"exit robustness failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
