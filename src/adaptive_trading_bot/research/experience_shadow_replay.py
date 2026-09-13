"""Causal offline attribution for the shadow-only Experience advisory.

This module replays accepted decisions; it does not simulate an alternate trading path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import Enum, StrEnum
from hashlib import sha256
from hmac import compare_digest
from json import dumps

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceContextKey,
    ExperienceEstimationConfiguration,
    ExperienceEvidenceConfidence,
    ExperienceFallbackLevel,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
    experience_context_fingerprint,
)
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalEntry,
    DecisionJournalSnapshot,
)
from adaptive_trading_bot.routing import (
    ExperienceAdvisoryConfiguration,
    ExperienceAdvisoryEvidenceState,
    ExperienceCandidateAdvisor,
    ExperienceOutcomeBias,
)
from adaptive_trading_bot.strategies import CandidateAction

_CONFIGURATION_SCHEMA_VERSION = "1.0.0"
_REPORTING_POLICY_VERSION = "experience-shadow-replay-v1"
_RECORD_SCHEMA_VERSION = "1.0.0"
_REPORT_SCHEMA_VERSION = "1.0.0"
_SUMMARY_SCHEMA_VERSION = "1.0.0"
_INTEGRITY_SEAL_VERSION = "1.0.0"
_DECISION_JOURNAL_SCHEMA_VERSION = "1.0.0"
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_LIMITATION = (
    "Retrospective attribution only: observed outcomes are from trades executed by the "
    "original system and are not a counterfactual portfolio, PnL, equity, drawdown, or "
    "expected live-improvement simulation."
)
_CANDIDATES = frozenset({CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE})


class ExperienceShadowReplayError(ValueError):
    """The supplied authority cannot produce a trustworthy research report."""


class ExperienceShadowResearchMode(StrEnum):
    SHADOW_REPLAY_ONLY = "SHADOW_REPLAY_ONLY"


class ExperienceObservedOutcomeGroup(StrEnum):
    BLOCKED_SHADOW_GROUP = "BLOCKED_SHADOW_GROUP"
    RETAINED_SHADOW_GROUP = "RETAINED_SHADOW_GROUP"


class ExperienceShadowStratificationDimension(StrEnum):
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    CANDIDATE_ACTION = "CANDIDATE_ACTION"
    REGIME_15M = "REGIME_15M"
    HIGHER_TIMEFRAME_CONTEXT = "HIGHER_TIMEFRAME_CONTEXT"
    SELECTED_FALLBACK_LEVEL = "SELECTED_FALLBACK_LEVEL"
    EVIDENCE_CONFIDENCE = "EVIDENCE_CONFIDENCE"


@dataclass(frozen=True, slots=True)
class ResearchDecimalRate:
    """Exact ratio with its denominator and explicit zero-denominator state."""

    numerator: int
    denominator: int
    value: Decimal | None

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (self.numerator, self.denominator)):
            raise ExperienceShadowReplayError("rate counts are invalid")
        if self.numerator > self.denominator:
            raise ExperienceShadowReplayError("rate numerator exceeds denominator")
        expected = _ratio(self.numerator, self.denominator)
        if self.value != expected:
            raise ExperienceShadowReplayError("rate value is inconsistent")


@dataclass(frozen=True, slots=True)
class ExperienceShadowReplayConfiguration:
    """Versioned supplied policy for one offline replay; never a trading control."""

    estimation: ExperienceEstimationConfiguration = field(
        default_factory=ExperienceEstimationConfiguration
    )
    advisory: ExperienceAdvisoryConfiguration = field(
        default_factory=ExperienceAdvisoryConfiguration
    )
    time_slice_size: timedelta = timedelta(days=30)
    configuration_schema_version: str = field(init=False, default=_CONFIGURATION_SCHEMA_VERSION)
    reporting_policy_version: str = field(init=False, default=_REPORTING_POLICY_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.configuration_schema_version != _CONFIGURATION_SCHEMA_VERSION:
            raise ExperienceShadowReplayError("research configuration schema version mismatch")
        if self.reporting_policy_version != _REPORTING_POLICY_VERSION:
            raise ExperienceShadowReplayError("research reporting policy version mismatch")
        if not isinstance(self.estimation, ExperienceEstimationConfiguration):
            raise TypeError("estimation must be ExperienceEstimationConfiguration")
        if not isinstance(self.advisory, ExperienceAdvisoryConfiguration):
            raise TypeError("advisory must be ExperienceAdvisoryConfiguration")
        self.estimation.validate()
        self.advisory.validate()
        if not isinstance(self.time_slice_size, timedelta) or self.time_slice_size <= timedelta():
            raise ExperienceShadowReplayError("time slice size must be positive")


@dataclass(frozen=True, slots=True)
class ExperienceShadowDecisionRecord:
    """One sealed replay observation and optional exact authoritative outcome."""

    identity: DecisionIdentity
    original_action: CandidateAction
    target_fingerprint: ExperienceContextFingerprint
    evidence_state: ExperienceAdvisoryEvidenceState
    outcome_bias: ExperienceOutcomeBias
    evidence_confidence: ExperienceEvidenceConfidence | None
    selected_hierarchy_level: ExperienceFallbackLevel | None
    selected_hierarchy_key: ExperienceContextKey | None
    selected_raw_sample_count: int
    selected_effective_sample_size: Decimal
    posterior_mean_win_probability: Decimal | None
    recency_weighted_expectancy_r: Decimal | None
    would_block_candidate: bool
    completed_outcome_available: bool
    outcome_classification: str | None
    total_trade_fees_quote: Decimal | None
    net_realized_pnl: Decimal | None
    realized_r_net: Decimal | None
    quote_currency: str | None
    exit_fill_at: datetime | None
    research_mode: ExperienceShadowResearchMode
    estimation_policy_version: str
    recency_policy_version: str
    advisory_policy_version: str
    reporting_policy_version: str
    record_schema_version: str = field(init=False, default=_RECORD_SCHEMA_VERSION)
    record_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "record_integrity_seal", _integrity_seal("record", self))

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, str]:
        return self.identity.sort_key

    def validate(self) -> None:
        if not isinstance(self.record_integrity_seal, str) or not compare_digest(
            self.record_integrity_seal, _integrity_seal("record", self)
        ):
            raise ExperienceShadowReplayError("research record integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.record_schema_version != _RECORD_SCHEMA_VERSION:
            raise ExperienceShadowReplayError("research record schema version mismatch")
        if not isinstance(self.identity, DecisionIdentity):
            raise TypeError("research record identity is invalid")
        if self.original_action not in CandidateAction:
            raise ExperienceShadowReplayError("research record action is invalid")
        self.target_fingerprint.validate()
        if (
            self.target_fingerprint.symbol != self.identity.symbol
            or self.target_fingerprint.strategy_id != self.identity.strategy_id
            or self.target_fingerprint.strategy_version != self.identity.strategy_version
        ):
            raise ExperienceShadowReplayError("research record fingerprint identity mismatch")
        if not isinstance(self.evidence_state, ExperienceAdvisoryEvidenceState):
            raise ExperienceShadowReplayError("research evidence state is invalid")
        if not isinstance(self.outcome_bias, ExperienceOutcomeBias):
            raise ExperienceShadowReplayError("research outcome bias is invalid")
        if self.evidence_confidence is not None and not isinstance(
            self.evidence_confidence, ExperienceEvidenceConfidence
        ):
            raise ExperienceShadowReplayError("research confidence is invalid")
        if self.selected_hierarchy_key is None:
            if self.selected_hierarchy_level is not None:
                raise ExperienceShadowReplayError("research hierarchy provenance is inconsistent")
        elif self.selected_hierarchy_level is not self.selected_hierarchy_key.level:
            raise ExperienceShadowReplayError("research hierarchy provenance is inconsistent")
        if type(self.selected_raw_sample_count) is not int or self.selected_raw_sample_count < 0:
            raise ExperienceShadowReplayError("research raw sample count is invalid")
        _finite_decimal(self.selected_effective_sample_size, "effective sample size")
        if self.selected_effective_sample_size < 0:
            raise ExperienceShadowReplayError("effective sample size is negative")
        for name, value in (
            ("posterior mean", self.posterior_mean_win_probability),
            ("weighted expectancy R", self.recency_weighted_expectancy_r),
        ):
            if value is not None:
                _finite_decimal(value, name)
        if type(self.would_block_candidate) is not bool:
            raise ExperienceShadowReplayError("research shadow-block flag is invalid")
        if self.original_action is CandidateAction.NO_TRADE and self.would_block_candidate:
            raise ExperienceShadowReplayError("NO_TRADE cannot be shadow-blocked")
        if type(self.completed_outcome_available) is not bool:
            raise ExperienceShadowReplayError("outcome availability flag is invalid")
        outcome = (
            self.outcome_classification,
            self.total_trade_fees_quote,
            self.net_realized_pnl,
            self.realized_r_net,
            self.quote_currency,
            self.exit_fill_at,
        )
        if self.completed_outcome_available:
            if self.original_action not in _CANDIDATES or any(value is None for value in outcome):
                raise ExperienceShadowReplayError("available research outcome is incomplete")
            if self.outcome_classification not in {"PROFIT", "LOSS", "BREAKEVEN"}:
                raise ExperienceShadowReplayError("research outcome classification is invalid")
            fees = _required_decimal(self.total_trade_fees_quote)
            _finite_decimal(_required_decimal(self.net_realized_pnl), "net realized PnL")
            _finite_decimal(_required_decimal(self.realized_r_net), "realized R")
            _finite_decimal(fees, "total trade fees")
            if fees < 0:
                raise ExperienceShadowReplayError("research total trade fees are negative")
            if (
                not isinstance(self.quote_currency, str)
                or self.quote_currency != self.quote_currency.strip().upper()
            ):
                raise ExperienceShadowReplayError("research quote currency is invalid")
            if not isinstance(self.exit_fill_at, datetime):
                raise ExperienceShadowReplayError("research outcome exit time is invalid")
            require_utc(self.exit_fill_at, field="research outcome exit_fill_at")
        elif any(value is not None for value in outcome):
            raise ExperienceShadowReplayError("unavailable research outcome contains facts")
        if self.research_mode is not ExperienceShadowResearchMode.SHADOW_REPLAY_ONLY:
            raise ExperienceShadowReplayError("research mode is invalid")
        for name, version in (
            ("estimation policy", self.estimation_policy_version),
            ("recency policy", self.recency_policy_version),
            ("advisory policy", self.advisory_policy_version),
            ("reporting policy", self.reporting_policy_version),
        ):
            _canonical_text(name, version)


@dataclass(frozen=True, slots=True)
class ExperienceShadowPopulationSummary:
    total_decision_count: int
    no_trade_decision_count: int
    long_candidate_count: int
    short_candidate_count: int
    no_history_count: int
    insufficient_evidence_count: int
    low_evidence_count: int
    usable_evidence_count: int
    favorable_usable_evidence_count: int
    mixed_usable_evidence_count: int
    adverse_usable_evidence_count: int
    would_block_candidate_count: int
    shadow_block_rate: ResearchDecimalRate
    usable_evidence_coverage_rate: ResearchDecimalRate
    summary_schema_version: str = field(init=False, default=_SUMMARY_SCHEMA_VERSION)

    @property
    def candidate_count(self) -> int:
        return self.long_candidate_count + self.short_candidate_count

    def __post_init__(self) -> None:
        counts = tuple(
            getattr(self, item.name) for item in fields(self) if item.name.endswith("_count")
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ExperienceShadowReplayError("population count is invalid")
        if self.total_decision_count != self.no_trade_decision_count + self.candidate_count:
            raise ExperienceShadowReplayError("decision populations do not sum")
        if self.candidate_count != (
            self.no_history_count
            + self.insufficient_evidence_count
            + self.low_evidence_count
            + self.usable_evidence_count
        ):
            raise ExperienceShadowReplayError("candidate evidence populations do not sum")
        if self.usable_evidence_count != (
            self.favorable_usable_evidence_count
            + self.mixed_usable_evidence_count
            + self.adverse_usable_evidence_count
        ):
            raise ExperienceShadowReplayError("usable evidence populations do not sum")
        if self.would_block_candidate_count > self.adverse_usable_evidence_count:
            raise ExperienceShadowReplayError("shadow-block count is inconsistent")
        if self.shadow_block_rate != _rate(self.would_block_candidate_count, self.candidate_count):
            raise ExperienceShadowReplayError("shadow-block rate is inconsistent")
        if self.usable_evidence_coverage_rate != _rate(
            self.usable_evidence_count, self.candidate_count
        ):
            raise ExperienceShadowReplayError("usable coverage rate is inconsistent")


@dataclass(frozen=True, slots=True)
class ExperienceOutcomeCurrencySummary:
    quote_currency: str
    completed_trade_count: int
    net_realized_pnl_sum: Decimal
    mean_net_realized_pnl: Decimal
    total_fees_sum: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.quote_currency, str)
            or not self.quote_currency
            or self.quote_currency != self.quote_currency.strip().upper()
        ):
            raise ExperienceShadowReplayError("outcome quote currency is invalid")
        if type(self.completed_trade_count) is not int or self.completed_trade_count <= 0:
            raise ExperienceShadowReplayError("currency trade count is invalid")
        for name, value in (
            ("net PnL sum", self.net_realized_pnl_sum),
            ("mean net PnL", self.mean_net_realized_pnl),
            ("fees sum", self.total_fees_sum),
        ):
            _finite_decimal(value, name)
        with localcontext(_DECIMAL_CONTEXT):
            if self.mean_net_realized_pnl != self.net_realized_pnl_sum / Decimal(
                self.completed_trade_count
            ):
                raise ExperienceShadowReplayError("currency mean net PnL is inconsistent")
        if self.total_fees_sum < 0:
            raise ExperienceShadowReplayError("currency fees sum is negative")


@dataclass(frozen=True, slots=True)
class ExperienceObservedOutcomeSummary:
    group: ExperienceObservedOutcomeGroup
    completed_trade_count: int
    profit_count: int
    loss_count: int
    breakeven_count: int
    win_rate: ResearchDecimalRate
    realized_r_sum: Decimal
    mean_realized_r_net: Decimal | None
    median_realized_r_net: Decimal | None
    currency_summaries: tuple[ExperienceOutcomeCurrencySummary, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.group, ExperienceObservedOutcomeGroup):
            raise ExperienceShadowReplayError("outcome group is invalid")
        for value in (
            self.completed_trade_count,
            self.profit_count,
            self.loss_count,
            self.breakeven_count,
        ):
            if type(value) is not int or value < 0:
                raise ExperienceShadowReplayError("outcome count is invalid")
        if self.completed_trade_count != self.profit_count + self.loss_count + self.breakeven_count:
            raise ExperienceShadowReplayError("outcome populations do not sum")
        if self.win_rate != _rate(self.profit_count, self.completed_trade_count):
            raise ExperienceShadowReplayError("outcome win rate is inconsistent")
        _finite_decimal(self.realized_r_sum, "realized R sum")
        if self.completed_trade_count:
            _finite_decimal(self.mean_realized_r_net, "mean realized R")
            _finite_decimal(self.median_realized_r_net, "median realized R")
            with localcontext(_DECIMAL_CONTEXT):
                if self.mean_realized_r_net != self.realized_r_sum / Decimal(
                    self.completed_trade_count
                ):
                    raise ExperienceShadowReplayError("mean realized R is inconsistent")
        elif self.mean_realized_r_net is not None or self.median_realized_r_net is not None:
            raise ExperienceShadowReplayError("empty outcome summary exposes R averages")
        if not isinstance(self.currency_summaries, tuple):
            raise TypeError("currency summaries must be an immutable tuple")
        if tuple(item.quote_currency for item in self.currency_summaries) != tuple(
            sorted(item.quote_currency for item in self.currency_summaries)
        ):
            raise ExperienceShadowReplayError("currency summaries are not canonical")
        if (
            sum(item.completed_trade_count for item in self.currency_summaries)
            != self.completed_trade_count
        ):
            raise ExperienceShadowReplayError("currency summary populations do not sum")


@dataclass(frozen=True, slots=True)
class ExperienceShadowStratum:
    dimension: ExperienceShadowStratificationDimension
    value: tuple[str, ...]
    decision_count: int
    candidate_count: int
    usable_evidence_count: int
    outcome_evaluable_count: int
    would_block_candidate_count: int
    usable_evidence_coverage_rate: ResearchDecimalRate
    shadow_block_rate: ResearchDecimalRate
    blocked_outcomes: ExperienceObservedOutcomeSummary
    retained_outcomes: ExperienceObservedOutcomeSummary

    def __post_init__(self) -> None:
        if not isinstance(self.dimension, ExperienceShadowStratificationDimension):
            raise ExperienceShadowReplayError("stratification dimension is invalid")
        if (
            not isinstance(self.value, tuple)
            or not self.value
            or any(not isinstance(value, str) or not value for value in self.value)
        ):
            raise ExperienceShadowReplayError("stratification value is invalid")
        for value in (
            self.decision_count,
            self.candidate_count,
            self.usable_evidence_count,
            self.outcome_evaluable_count,
            self.would_block_candidate_count,
        ):
            if type(value) is not int or value < 0:
                raise ExperienceShadowReplayError("stratification count is invalid")
        if (
            self.candidate_count > self.decision_count
            or self.usable_evidence_count > self.candidate_count
            or self.outcome_evaluable_count > self.candidate_count
        ):
            raise ExperienceShadowReplayError("stratification populations are inconsistent")
        if self.usable_evidence_coverage_rate != _rate(
            self.usable_evidence_count, self.candidate_count
        ) or self.shadow_block_rate != _rate(
            self.would_block_candidate_count, self.candidate_count
        ):
            raise ExperienceShadowReplayError("stratification rate is inconsistent")
        if (
            self.blocked_outcomes.group is not ExperienceObservedOutcomeGroup.BLOCKED_SHADOW_GROUP
            or self.retained_outcomes.group
            is not ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP
            or self.blocked_outcomes.completed_trade_count
            + self.retained_outcomes.completed_trade_count
            != self.outcome_evaluable_count
        ):
            raise ExperienceShadowReplayError("stratification outcome groups are inconsistent")


@dataclass(frozen=True, slots=True)
class ExperienceShadowStabilitySlice:
    start: datetime
    end: datetime
    candidate_count: int
    usable_evidence_count: int
    would_block_candidate_count: int
    usable_evidence_coverage_rate: ResearchDecimalRate
    shadow_block_rate: ResearchDecimalRate
    blocked_outcomes: ExperienceObservedOutcomeSummary
    retained_outcomes: ExperienceObservedOutcomeSummary

    def __post_init__(self) -> None:
        start = require_utc(self.start, field="stability slice start")
        end = require_utc(self.end, field="stability slice end")
        if end <= start:
            raise ExperienceShadowReplayError("stability slice range is invalid")
        for value in (
            self.candidate_count,
            self.usable_evidence_count,
            self.would_block_candidate_count,
        ):
            if type(value) is not int or value < 0:
                raise ExperienceShadowReplayError("stability count is invalid")
        if self.usable_evidence_coverage_rate != _rate(
            self.usable_evidence_count, self.candidate_count
        ) or self.shadow_block_rate != _rate(
            self.would_block_candidate_count, self.candidate_count
        ):
            raise ExperienceShadowReplayError("stability rates are inconsistent")


@dataclass(frozen=True, slots=True)
class ExperienceShadowReplayReport:
    start: datetime
    end: datetime
    records: tuple[ExperienceShadowDecisionRecord, ...]
    population: ExperienceShadowPopulationSummary
    outcome_evaluable_count: int
    non_outcome_evaluable_candidate_count: int
    shadow_blocked_non_outcome_evaluable_count: int
    retained_non_outcome_evaluable_count: int
    observed_outcome_summaries: tuple[ExperienceObservedOutcomeSummary, ...]
    stratified_summaries: tuple[ExperienceShadowStratum, ...]
    stability_summaries: tuple[ExperienceShadowStabilitySlice, ...]
    limitation: str
    research_mode: ExperienceShadowResearchMode
    reporting_policy_version: str
    report_schema_version: str = field(init=False, default=_REPORT_SCHEMA_VERSION)
    report_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "report_integrity_seal", _integrity_seal("report", self))

    def validate(self) -> None:
        if not isinstance(self.report_integrity_seal, str) or not compare_digest(
            self.report_integrity_seal, _integrity_seal("report", self)
        ):
            raise ExperienceShadowReplayError("research report integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        start = require_utc(self.start, field="research report start")
        end = require_utc(self.end, field="research report end")
        if end <= start:
            raise ExperienceShadowReplayError("research report range is invalid")
        if self.report_schema_version != _REPORT_SCHEMA_VERSION:
            raise ExperienceShadowReplayError("research report schema version mismatch")
        if not isinstance(self.records, tuple):
            raise TypeError("research records must be an immutable tuple")
        for record in self.records:
            record.validate()
            if not start <= record.identity.observed_at < end:
                raise ExperienceShadowReplayError("research record is outside reporting range")
        if self.records != tuple(sorted(self.records, key=lambda item: item.sort_key)):
            raise ExperienceShadowReplayError("research records are not canonical")
        identities = tuple(record.identity for record in self.records)
        if len(set(identities)) != len(identities):
            raise ExperienceShadowReplayError("research records contain duplicate decisions")
        if self.population.total_decision_count != len(self.records):
            raise ExperienceShadowReplayError("report population does not match records")
        for value in (
            self.outcome_evaluable_count,
            self.non_outcome_evaluable_candidate_count,
            self.shadow_blocked_non_outcome_evaluable_count,
            self.retained_non_outcome_evaluable_count,
        ):
            if type(value) is not int or value < 0:
                raise ExperienceShadowReplayError("report outcome population is invalid")
        if (
            self.outcome_evaluable_count + self.non_outcome_evaluable_candidate_count
            != self.population.candidate_count
        ):
            raise ExperienceShadowReplayError("report candidate outcome populations do not sum")
        if self.non_outcome_evaluable_candidate_count != (
            self.shadow_blocked_non_outcome_evaluable_count
            + self.retained_non_outcome_evaluable_count
        ):
            raise ExperienceShadowReplayError("non-outcome populations do not sum")
        if tuple(item.group for item in self.observed_outcome_summaries) != tuple(
            ExperienceObservedOutcomeGroup
        ):
            raise ExperienceShadowReplayError("observed outcome summaries are not canonical")
        if (
            sum(item.completed_trade_count for item in self.observed_outcome_summaries)
            != self.outcome_evaluable_count
        ):
            raise ExperienceShadowReplayError("observed outcome populations do not sum")
        if self.limitation != _LIMITATION:
            raise ExperienceShadowReplayError("research limitation text is missing or changed")
        if self.research_mode is not ExperienceShadowResearchMode.SHADOW_REPLAY_ONLY:
            raise ExperienceShadowReplayError("report research mode is invalid")
        if self.reporting_policy_version != _REPORTING_POLICY_VERSION:
            raise ExperienceShadowReplayError("reporting policy version mismatch")


class ExperienceShadowReplayEvaluator:
    """Pure causal evaluator over sealed decisions and sealed completed samples."""

    __slots__ = ()

    def evaluate(
        self,
        decisions: DecisionJournalSnapshot[object, object],
        samples: ExperienceTradeSampleSnapshot,
        configuration: ExperienceShadowReplayConfiguration,
        start: datetime,
        end: datetime,
    ) -> ExperienceShadowReplayReport:
        if not isinstance(decisions, DecisionJournalSnapshot):
            raise TypeError("decisions must be a DecisionJournalSnapshot")
        if not isinstance(samples, ExperienceTradeSampleSnapshot):
            raise TypeError("samples must be an ExperienceTradeSampleSnapshot")
        if not isinstance(configuration, ExperienceShadowReplayConfiguration):
            raise TypeError("configuration must be ExperienceShadowReplayConfiguration")
        start = require_utc(start, field="research start")
        end = require_utc(end, field="research end")
        if end <= start:
            raise ExperienceShadowReplayError("research range must satisfy start < end")
        configuration.validate()
        owned_decisions = deepcopy(decisions)
        owned_samples = deepcopy(samples)
        decision_by_identity = _validated_decisions(owned_decisions)
        owned_samples.validate()
        ordered_samples = tuple(sorted(owned_samples.samples, key=lambda item: item.sort_key))
        _validate_sample_joins(ordered_samples, decision_by_identity, start, end)
        outcome_by_identity = {
            sample.decision_identity: sample
            for sample in ordered_samples
            if _eligible_for_reporting_attribution(sample, start, end)
        }
        # Outcomes completed after the report end cannot affect any in-range advisory.
        advisory_samples = ExperienceTradeSampleSnapshot(
            tuple(sample for sample in ordered_samples if sample.exit_fill_at <= end)
        )
        advisor = ExperienceCandidateAdvisor(configuration.estimation, configuration.advisory)
        in_range = tuple(
            sorted(
                (
                    entry
                    for entry in decision_by_identity.values()
                    if start <= entry.identity.observed_at < end
                ),
                key=lambda entry: entry.identity.sort_key,
            )
        )
        records = tuple(
            _replay_record(
                entry,
                outcome_by_identity.get(entry.identity),
                advisor,
                advisory_samples,
                configuration,
            )
            for entry in in_range
        )
        population = _population(records)
        candidates = tuple(record for record in records if record.original_action in _CANDIDATES)
        evaluable = tuple(record for record in candidates if record.completed_outcome_available)
        not_evaluable = tuple(
            record for record in candidates if not record.completed_outcome_available
        )
        outcome_summaries = tuple(
            _outcome_summary(group, evaluable) for group in ExperienceObservedOutcomeGroup
        )
        report = ExperienceShadowReplayReport(
            start=start,
            end=end,
            records=deepcopy(records),
            population=population,
            outcome_evaluable_count=len(evaluable),
            non_outcome_evaluable_candidate_count=len(not_evaluable),
            shadow_blocked_non_outcome_evaluable_count=sum(
                record.would_block_candidate for record in not_evaluable
            ),
            retained_non_outcome_evaluable_count=sum(
                not record.would_block_candidate for record in not_evaluable
            ),
            observed_outcome_summaries=outcome_summaries,
            stratified_summaries=_strata(records),
            stability_summaries=_stability(records, start, end, configuration.time_slice_size),
            limitation=_LIMITATION,
            research_mode=ExperienceShadowResearchMode.SHADOW_REPLAY_ONLY,
            reporting_policy_version=configuration.reporting_policy_version,
        )
        report.validate()
        return deepcopy(report)


def _validated_decisions(
    snapshot: DecisionJournalSnapshot[object, object],
) -> dict[DecisionIdentity, DecisionJournalEntry[object, object]]:
    if snapshot.journal_schema_version != _DECISION_JOURNAL_SCHEMA_VERSION:
        raise ExperienceShadowReplayError("decision snapshot schema version mismatch")
    if not isinstance(snapshot.entries, tuple):
        raise TypeError("decision snapshot entries must be an immutable tuple")
    expected_last = max((entry.identity.observed_at for entry in snapshot.entries), default=None)
    if snapshot.last_observed_at != expected_last:
        raise ExperienceShadowReplayError("decision snapshot last_observed_at is inconsistent")
    indexed: dict[DecisionIdentity, DecisionJournalEntry[object, object]] = {}
    for entry in snapshot.entries:
        if not isinstance(entry, DecisionJournalEntry):
            raise TypeError("decision snapshot contains an invalid entry")
        entry.validate()
        if entry.identity in indexed:
            raise ExperienceShadowReplayError("duplicate DecisionIdentity in decision snapshot")
        indexed[entry.identity] = entry
    return indexed


def _validate_sample_joins(
    samples: tuple[ExperienceTradeSample, ...],
    decisions: dict[DecisionIdentity, DecisionJournalEntry[object, object]],
    start: datetime,
    end: datetime,
) -> None:
    for sample in samples:
        sample.validate()
        decision = decisions.get(sample.decision_identity)
        used_for_attribution = _eligible_for_reporting_attribution(sample, start, end)
        if decision is None:
            if used_for_attribution:
                raise ExperienceShadowReplayError("in-scope completed sample is orphaned")
            continue
        # A sample completing after the inclusive report-end boundary is validated as sealed
        # sample authority above, but it is not joined into this earlier attribution report.
        if sample.exit_fill_at > end:
            continue
        expected_fingerprint = experience_context_fingerprint(
            decision.context,
            decision.identity.strategy_id,
            decision.identity.strategy_version,
        )
        risk = decision.risk_decision
        safety = decision.safety_decision
        expected_entry_side = (
            "BUY"
            if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
            else "SELL"
        )
        if (
            decision.strategy_evaluation.action not in _CANDIDATES
            or sample.fingerprint != expected_fingerprint
            or sample.candidate_action != decision.strategy_evaluation.action
            or sample.entry_decision_at != decision.identity.observed_at
            or sample.entry_side != expected_entry_side
            or sample.approved_risk_quote != risk.approved_risk_quote
            or sample.quantity != risk.approved_quantity
            or sample.risk_policy_version != risk.risk_policy_version
            or sample.risk_policy_version != safety.risk_policy_version
            or sample.safety_policy_version != safety.safety_policy_version
            or not decision.identity.observed_at <= sample.entry_fill_at <= sample.exit_fill_at
        ):
            raise ExperienceShadowReplayError(
                "completed sample and journal decision provenance are inconsistent"
            )


def _eligible_for_reporting_attribution(
    sample: ExperienceTradeSample, start: datetime, end: datetime
) -> bool:
    """Use an in-window decision outcome only when it completed by inclusive ``end``."""

    return start <= sample.decision_identity.observed_at < end and sample.exit_fill_at <= end


def _replay_record(
    entry: DecisionJournalEntry[object, object],
    outcome: ExperienceTradeSample | None,
    advisor: ExperienceCandidateAdvisor,
    samples: ExperienceTradeSampleSnapshot,
    configuration: ExperienceShadowReplayConfiguration,
) -> ExperienceShadowDecisionRecord:
    entry.validate()
    advisory = advisor.advise(
        entry.context,
        entry.strategy_evaluation,
        samples,
        entry.identity.observed_at,
    )
    advisory.validate()
    return ExperienceShadowDecisionRecord(
        identity=deepcopy(entry.identity),
        original_action=advisory.original_action,
        target_fingerprint=deepcopy(advisory.target_fingerprint),
        evidence_state=advisory.evidence_state,
        outcome_bias=advisory.outcome_bias,
        evidence_confidence=advisory.evidence_confidence,
        selected_hierarchy_level=advisory.selected_hierarchy_level,
        selected_hierarchy_key=deepcopy(advisory.selected_hierarchy_key),
        selected_raw_sample_count=advisory.selected_raw_sample_count,
        selected_effective_sample_size=advisory.selected_effective_sample_size,
        posterior_mean_win_probability=advisory.posterior_mean_win_probability,
        recency_weighted_expectancy_r=advisory.recency_weighted_expectancy_r,
        would_block_candidate=advisory.would_block_candidate,
        completed_outcome_available=outcome is not None,
        outcome_classification=(outcome.outcome_classification if outcome else None),
        total_trade_fees_quote=(outcome.total_trade_fees_quote if outcome else None),
        net_realized_pnl=(outcome.net_realized_pnl if outcome else None),
        realized_r_net=(outcome.realized_r_net if outcome else None),
        quote_currency=(outcome.fingerprint.symbol.quote if outcome else None),
        exit_fill_at=(outcome.exit_fill_at if outcome else None),
        research_mode=ExperienceShadowResearchMode.SHADOW_REPLAY_ONLY,
        estimation_policy_version=advisory.estimation_policy_version,
        recency_policy_version=advisory.recency_policy_version,
        advisory_policy_version=advisory.advisory_policy_version,
        reporting_policy_version=configuration.reporting_policy_version,
    )


def _population(
    records: tuple[ExperienceShadowDecisionRecord, ...],
) -> ExperienceShadowPopulationSummary:
    candidates = tuple(record for record in records if record.original_action in _CANDIDATES)
    usable = tuple(
        record
        for record in candidates
        if record.evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
    )
    return ExperienceShadowPopulationSummary(
        total_decision_count=len(records),
        no_trade_decision_count=sum(
            record.original_action is CandidateAction.NO_TRADE for record in records
        ),
        long_candidate_count=sum(
            record.original_action is CandidateAction.LONG_CANDIDATE for record in records
        ),
        short_candidate_count=sum(
            record.original_action is CandidateAction.SHORT_CANDIDATE for record in records
        ),
        no_history_count=sum(
            record.evidence_state is ExperienceAdvisoryEvidenceState.NO_HISTORY
            for record in candidates
        ),
        insufficient_evidence_count=sum(
            record.evidence_state is ExperienceAdvisoryEvidenceState.INSUFFICIENT_EVIDENCE
            for record in candidates
        ),
        low_evidence_count=sum(
            record.evidence_state is ExperienceAdvisoryEvidenceState.LOW_EVIDENCE
            for record in candidates
        ),
        usable_evidence_count=len(usable),
        favorable_usable_evidence_count=sum(
            record.outcome_bias is ExperienceOutcomeBias.FAVORABLE for record in usable
        ),
        mixed_usable_evidence_count=sum(
            record.outcome_bias is ExperienceOutcomeBias.MIXED for record in usable
        ),
        adverse_usable_evidence_count=sum(
            record.outcome_bias is ExperienceOutcomeBias.ADVERSE for record in usable
        ),
        would_block_candidate_count=sum(record.would_block_candidate for record in candidates),
        shadow_block_rate=_rate(
            sum(record.would_block_candidate for record in candidates), len(candidates)
        ),
        usable_evidence_coverage_rate=_rate(len(usable), len(candidates)),
    )


def _outcome_summary(
    group: ExperienceObservedOutcomeGroup,
    records: tuple[ExperienceShadowDecisionRecord, ...],
) -> ExperienceObservedOutcomeSummary:
    selected = tuple(
        record
        for record in records
        if record.completed_outcome_available
        and (
            record.would_block_candidate
            is (group is ExperienceObservedOutcomeGroup.BLOCKED_SHADOW_GROUP)
        )
    )
    realized_rs = tuple(_required_decimal(record.realized_r_net) for record in selected)
    with localcontext(_DECIMAL_CONTEXT):
        realized_sum = sum(realized_rs, Decimal(0))
        mean_r = realized_sum / Decimal(len(selected)) if selected else None
    currencies = sorted({_required_text(record.quote_currency) for record in selected})
    currency_summaries = tuple(_currency_summary(currency, selected) for currency in currencies)
    return ExperienceObservedOutcomeSummary(
        group=group,
        completed_trade_count=len(selected),
        profit_count=sum(record.outcome_classification == "PROFIT" for record in selected),
        loss_count=sum(record.outcome_classification == "LOSS" for record in selected),
        breakeven_count=sum(record.outcome_classification == "BREAKEVEN" for record in selected),
        win_rate=_rate(
            sum(record.outcome_classification == "PROFIT" for record in selected),
            len(selected),
        ),
        realized_r_sum=realized_sum,
        mean_realized_r_net=mean_r,
        median_realized_r_net=_median(realized_rs),
        currency_summaries=currency_summaries,
    )


def _currency_summary(
    currency: str, records: tuple[ExperienceShadowDecisionRecord, ...]
) -> ExperienceOutcomeCurrencySummary:
    selected = tuple(record for record in records if record.quote_currency == currency)
    with localcontext(_DECIMAL_CONTEXT):
        net_sum = sum(
            (_required_decimal(record.net_realized_pnl) for record in selected), Decimal(0)
        )
        fees_sum = sum(
            (_required_decimal(record.total_trade_fees_quote) for record in selected),
            Decimal(0),
        )
        mean = net_sum / Decimal(len(selected))
    return ExperienceOutcomeCurrencySummary(currency, len(selected), net_sum, mean, fees_sum)


def _strata(
    records: tuple[ExperienceShadowDecisionRecord, ...],
) -> tuple[ExperienceShadowStratum, ...]:
    grouped: dict[
        tuple[ExperienceShadowStratificationDimension, tuple[str, ...]],
        list[ExperienceShadowDecisionRecord],
    ] = {}
    for record in records:
        fingerprint = record.target_fingerprint
        values = (
            (
                ExperienceShadowStratificationDimension.STRATEGY,
                (record.identity.strategy_id, record.identity.strategy_version),
            ),
            (ExperienceShadowStratificationDimension.SYMBOL, (str(record.identity.symbol),)),
            (
                ExperienceShadowStratificationDimension.CANDIDATE_ACTION,
                (record.original_action.value,),
            ),
            (
                ExperienceShadowStratificationDimension.REGIME_15M,
                (fingerprint.regime_label.value,),
            ),
            (
                ExperienceShadowStratificationDimension.HIGHER_TIMEFRAME_CONTEXT,
                (
                    fingerprint.higher_timeframe_bias.value,
                    fingerprint.multi_timeframe_alignment.value,
                ),
            ),
            (
                ExperienceShadowStratificationDimension.SELECTED_FALLBACK_LEVEL,
                (
                    record.selected_hierarchy_level.value
                    if record.selected_hierarchy_level is not None
                    else "UNAVAILABLE",
                ),
            ),
            (
                ExperienceShadowStratificationDimension.EVIDENCE_CONFIDENCE,
                (
                    record.evidence_confidence.value
                    if record.evidence_confidence is not None
                    else "NOT_APPLICABLE",
                ),
            ),
        )
        for key in values:
            grouped.setdefault(key, []).append(record)
    return tuple(
        _stratum(dimension, value, tuple(grouped[(dimension, value)]))
        for dimension, value in sorted(grouped, key=lambda item: (item[0].value, item[1]))
    )


def _stratum(
    dimension: ExperienceShadowStratificationDimension,
    value: tuple[str, ...],
    records: tuple[ExperienceShadowDecisionRecord, ...],
) -> ExperienceShadowStratum:
    candidates = tuple(record for record in records if record.original_action in _CANDIDATES)
    usable = sum(
        record.evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
        for record in candidates
    )
    blocked = sum(record.would_block_candidate for record in candidates)
    outcomes = tuple(record for record in candidates if record.completed_outcome_available)
    return ExperienceShadowStratum(
        dimension,
        value,
        len(records),
        len(candidates),
        usable,
        sum(record.completed_outcome_available for record in candidates),
        blocked,
        _rate(usable, len(candidates)),
        _rate(blocked, len(candidates)),
        _outcome_summary(ExperienceObservedOutcomeGroup.BLOCKED_SHADOW_GROUP, outcomes),
        _outcome_summary(ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP, outcomes),
    )


def _stability(
    records: tuple[ExperienceShadowDecisionRecord, ...],
    start: datetime,
    end: datetime,
    size: timedelta,
) -> tuple[ExperienceShadowStabilitySlice, ...]:
    slices: list[ExperienceShadowStabilitySlice] = []
    current = start
    while current < end:
        slice_end = min(current + size, end)
        selected = tuple(
            record for record in records if current <= record.identity.observed_at < slice_end
        )
        candidates = tuple(record for record in selected if record.original_action in _CANDIDATES)
        usable = sum(
            record.evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
            for record in candidates
        )
        blocked = sum(record.would_block_candidate for record in candidates)
        outcomes = tuple(record for record in candidates if record.completed_outcome_available)
        slices.append(
            ExperienceShadowStabilitySlice(
                current,
                slice_end,
                len(candidates),
                usable,
                blocked,
                _rate(usable, len(candidates)),
                _rate(blocked, len(candidates)),
                _outcome_summary(ExperienceObservedOutcomeGroup.BLOCKED_SHADOW_GROUP, outcomes),
                _outcome_summary(ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP, outcomes),
            )
        )
        current = slice_end
    return tuple(slices)


def _rate(numerator: int, denominator: int) -> ResearchDecimalRate:
    return ResearchDecimalRate(numerator, denominator, _ratio(numerator, denominator))


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext(_DECIMAL_CONTEXT):
        return Decimal(numerator) / Decimal(denominator)


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    with localcontext(_DECIMAL_CONTEXT):
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _required_decimal(value: Decimal | None) -> Decimal:
    if not isinstance(value, Decimal):
        raise ExperienceShadowReplayError("required authoritative Decimal is unavailable")
    return value


def _required_text(value: str | None) -> str:
    if not isinstance(value, str):
        raise ExperienceShadowReplayError("required authoritative text is unavailable")
    return value


def _finite_decimal(value: object, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExperienceShadowReplayError(f"{field_name} must be a finite Decimal")


def _canonical_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ExperienceShadowReplayError(f"{field_name} version is invalid")


def _integrity_seal(namespace: str, value: object) -> str:
    payload = dumps(
        (
            f"adaptive-trading-bot/experience-shadow-{namespace}",
            _INTEGRITY_SEAL_VERSION,
            _canonical_integrity_value(value, excluded_suffix="_integrity_seal"),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_integrity_value(value: object, *, excluded_suffix: str) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return ("enum", f"{type(value).__module__}.{type(value).__qualname__}", value.value)
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"), value.fold)
    if isinstance(value, timedelta):
        return ("timedelta", value.days, value.seconds, value.microseconds)
    if isinstance(value, Decimal):
        decimal_tuple = value.as_tuple()
        return (
            "decimal",
            decimal_tuple.sign,
            tuple(decimal_tuple.digits),
            str(decimal_tuple.exponent),
        )
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, tuple):
        return (
            "tuple",
            tuple(
                _canonical_integrity_value(item, excluded_suffix=excluded_suffix) for item in value
            ),
        )
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            f"{type(value).__module__}.{type(value).__qualname__}",
            tuple(
                (
                    item.name,
                    _canonical_integrity_value(
                        getattr(value, item.name), excluded_suffix=excluded_suffix
                    ),
                )
                for item in fields(value)
                if not item.name.endswith(excluded_suffix)
            ),
        )
    raise ExperienceShadowReplayError(
        f"research integrity contains unsupported type {type(value).__qualname__}"
    )
