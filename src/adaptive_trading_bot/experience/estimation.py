"""Causal, deterministic statistical estimates over authoritative Experience samples.

This module is read-only research infrastructure. It does not rank strategies, calculate an edge
score, make decisions, authorize trades, or alter Risk or Safety behavior.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.experience.aggregation import (
    ExperienceAggregationError,
    ExperienceAggregator,
)
from adaptive_trading_bot.experience.core import (
    ExperienceContextFingerprint,
    ExperienceContextKey,
    ExperienceFallbackLevel,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
    experience_context_hierarchy,
)

_CONFIGURATION_SCHEMA_VERSION = "1.0.0"
_ESTIMATE_SCHEMA_VERSION = "1.0.0"
_HIERARCHY_EVIDENCE_SCHEMA_VERSION = "1.0.0"
_ESTIMATION_POLICY_VERSION = "1.0.0"
_RECENCY_POLICY_VERSION = "bounded-linear-v1"
_SAMPLE_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
_ESTIMATION_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_MICROSECONDS_PER_SECOND = 1_000_000
_SECONDS_PER_DAY = 86_400


class ExperienceEstimationError(ValueError):
    """Authoritative samples or estimation inputs cannot produce a trustworthy estimate."""


class ExperienceEstimateAvailability(StrEnum):
    """Whether an estimate exists and meets the configured evidence minimums."""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_HISTORY = "NO_HISTORY"


class ExperienceEvidenceConfidence(StrEnum):
    """Evidence sufficiency tier, not a calibrated probability of correctness."""

    NO_HISTORY = "NO_HISTORY"
    INSUFFICIENT = "INSUFFICIENT"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ExperiencePriorSource(StrEnum):
    """Provenance of the Beta prior used by one available estimate."""

    HIERARCHICAL = "HIERARCHICAL"
    NEUTRAL_PRIOR = "NEUTRAL_PRIOR"


@dataclass(frozen=True, slots=True)
class ExperienceEstimationConfiguration:
    """Versioned research policy for causal Experience estimates."""

    minimum_raw_sample_count: int = 20
    minimum_effective_sample_size: Decimal = Decimal("12")
    medium_confidence_effective_sample_size: Decimal = Decimal("30")
    high_confidence_effective_sample_size: Decimal = Decimal("60")
    hierarchical_prior_strength_cap: Decimal = Decimal("20")
    recency_horizon: timedelta = timedelta(days=180)
    minimum_recency_weight: Decimal = Decimal("0.25")
    neutral_prior_alpha: Decimal = Decimal(1)
    neutral_prior_beta: Decimal = Decimal(1)
    configuration_schema_version: str = field(init=False, default=_CONFIGURATION_SCHEMA_VERSION)
    estimation_policy_version: str = field(init=False, default=_ESTIMATION_POLICY_VERSION)
    recency_policy_version: str = field(init=False, default=_RECENCY_POLICY_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.configuration_schema_version != _CONFIGURATION_SCHEMA_VERSION:
            raise ExperienceEstimationError("configuration schema version mismatch")
        if self.estimation_policy_version != _ESTIMATION_POLICY_VERSION:
            raise ExperienceEstimationError("estimation policy version mismatch")
        if self.recency_policy_version != _RECENCY_POLICY_VERSION:
            raise ExperienceEstimationError("recency policy version mismatch")
        if type(self.minimum_raw_sample_count) is not int or self.minimum_raw_sample_count <= 0:
            raise ExperienceEstimationError("minimum raw sample count must be a positive integer")
        thresholds = (
            self.minimum_effective_sample_size,
            self.medium_confidence_effective_sample_size,
            self.high_confidence_effective_sample_size,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in thresholds
        ):
            raise ExperienceEstimationError(
                "effective sample thresholds must be positive finite Decimals"
            )
        if not thresholds[0] <= thresholds[1] <= thresholds[2]:
            raise ExperienceEstimationError("effective sample thresholds are not ordered")
        if (
            not isinstance(self.hierarchical_prior_strength_cap, Decimal)
            or not self.hierarchical_prior_strength_cap.is_finite()
            or self.hierarchical_prior_strength_cap < 0
        ):
            raise ExperienceEstimationError("hierarchical prior strength cap is invalid")
        if not isinstance(self.recency_horizon, timedelta) or self.recency_horizon <= timedelta():
            raise ExperienceEstimationError("recency horizon must be positive")
        if (
            not isinstance(self.minimum_recency_weight, Decimal)
            or not self.minimum_recency_weight.is_finite()
            or not Decimal(0) < self.minimum_recency_weight <= Decimal(1)
        ):
            raise ExperienceEstimationError("minimum recency weight must be in (0, 1]")
        for name, value in (
            ("neutral prior alpha", self.neutral_prior_alpha),
            ("neutral prior beta", self.neutral_prior_beta),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ExperienceEstimationError(f"{name} must be a positive finite Decimal")


@dataclass(frozen=True, slots=True)
class ExperienceHierarchyEvidence:
    """Counts explaining fallback eligibility at one canonical hierarchy level."""

    key: ExperienceContextKey
    raw_eligible_sample_count: int
    effective_sample_size: Decimal
    sufficient: bool
    hierarchy_evidence_schema_version: str = field(
        init=False, default=_HIERARCHY_EVIDENCE_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if self.hierarchy_evidence_schema_version != _HIERARCHY_EVIDENCE_SCHEMA_VERSION:
            raise ExperienceEstimationError("hierarchy evidence schema version mismatch")
        if not isinstance(self.key, ExperienceContextKey):
            raise TypeError("hierarchy evidence key must be an ExperienceContextKey")
        self.key.validate()
        if type(self.raw_eligible_sample_count) is not int or self.raw_eligible_sample_count < 0:
            raise ExperienceEstimationError("hierarchy raw sample count is invalid")
        if (
            not isinstance(self.effective_sample_size, Decimal)
            or not self.effective_sample_size.is_finite()
            or self.effective_sample_size < 0
            or self.effective_sample_size > Decimal(self.raw_eligible_sample_count)
        ):
            raise ExperienceEstimationError("hierarchy effective sample size is invalid")
        if type(self.sufficient) is not bool:
            raise ExperienceEstimationError("hierarchy sufficiency flag is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceEstimate:
    """Explainable as-of estimate with no trading or ranking authority."""

    target_fingerprint: ExperienceContextFingerprint
    as_of: datetime
    availability: ExperienceEstimateAvailability
    selected_key: ExperienceContextKey | None
    confidence: ExperienceEvidenceConfidence
    examined_hierarchy: tuple[ExperienceHierarchyEvidence, ...]
    eligible_selected_raw_sample_count: int
    selected_effective_sample_size: Decimal
    excluded_future_sample_count: int
    prior_source: ExperiencePriorSource | None
    prior_source_level: ExperienceFallbackLevel | None
    prior_source_key: ExperienceContextKey | None
    prior_external_effective_sample_size: Decimal | None
    prior_alpha: Decimal | None
    prior_beta: Decimal | None
    posterior_alpha: Decimal | None
    posterior_beta: Decimal | None
    posterior_mean_win_probability: Decimal | None
    recency_weighted_win_rate: Decimal | None
    recency_weighted_expectancy_r: Decimal | None
    quote_currency: str | None
    recency_weighted_expectancy_net_quote: Decimal | None
    estimation_policy_version: str
    recency_policy_version: str
    estimate_schema_version: str = field(init=False, default=_ESTIMATE_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    @property
    def selected_level(self) -> ExperienceFallbackLevel | None:
        return None if self.selected_key is None else self.selected_key.level

    def validate(self) -> None:
        if self.estimate_schema_version != _ESTIMATE_SCHEMA_VERSION:
            raise ExperienceEstimationError("estimate schema version mismatch")
        if not isinstance(self.target_fingerprint, ExperienceContextFingerprint):
            raise TypeError("estimate target must be an ExperienceContextFingerprint")
        self.target_fingerprint.validate()
        require_utc(self.as_of, field="experience estimate as_of")
        if not isinstance(self.availability, ExperienceEstimateAvailability):
            raise ExperienceEstimationError("estimate availability is invalid")
        if not isinstance(self.confidence, ExperienceEvidenceConfidence):
            raise ExperienceEstimationError("estimate confidence is invalid")
        expected_keys = experience_context_hierarchy(self.target_fingerprint)
        if (
            not isinstance(self.examined_hierarchy, tuple)
            or tuple(item.key for item in self.examined_hierarchy) != expected_keys
        ):
            raise ExperienceEstimationError("examined hierarchy is not canonical")
        for item in self.examined_hierarchy:
            if not isinstance(item, ExperienceHierarchyEvidence):
                raise TypeError("examined hierarchy contains an invalid value")
        for name, count in (
            ("eligible selected raw sample count", self.eligible_selected_raw_sample_count),
            ("excluded future sample count", self.excluded_future_sample_count),
        ):
            if type(count) is not int or count < 0:
                raise ExperienceEstimationError(f"{name} is invalid")
        if (
            not isinstance(self.selected_effective_sample_size, Decimal)
            or not self.selected_effective_sample_size.is_finite()
            or self.selected_effective_sample_size < 0
        ):
            raise ExperienceEstimationError("selected effective sample size is invalid")
        if self.estimation_policy_version != _ESTIMATION_POLICY_VERSION:
            raise ExperienceEstimationError("estimate policy version mismatch")
        if self.recency_policy_version != _RECENCY_POLICY_VERSION:
            raise ExperienceEstimationError("estimate recency policy version mismatch")
        if self.availability is ExperienceEstimateAvailability.NO_HISTORY:
            self._validate_no_history()
            return
        self._validate_available_fields(expected_keys)

    def _validate_no_history(self) -> None:
        nullable_values = (
            self.selected_key,
            self.prior_source,
            self.prior_source_level,
            self.prior_source_key,
            self.prior_external_effective_sample_size,
            self.prior_alpha,
            self.prior_beta,
            self.posterior_alpha,
            self.posterior_beta,
            self.posterior_mean_win_probability,
            self.recency_weighted_win_rate,
            self.recency_weighted_expectancy_r,
            self.quote_currency,
            self.recency_weighted_expectancy_net_quote,
        )
        if (
            self.confidence is not ExperienceEvidenceConfidence.NO_HISTORY
            or self.eligible_selected_raw_sample_count != 0
            or self.selected_effective_sample_size != 0
            or any(value is not None for value in nullable_values)
            or any(item.raw_eligible_sample_count for item in self.examined_hierarchy)
        ):
            raise ExperienceEstimationError("NO_HISTORY estimate contains fabricated evidence")

    def _validate_available_fields(self, expected_keys: tuple[ExperienceContextKey, ...]) -> None:
        if self.selected_key not in expected_keys:
            raise ExperienceEstimationError("selected key is outside the target hierarchy")
        selected_evidence = next(
            item for item in self.examined_hierarchy if item.key == self.selected_key
        )
        if (
            self.eligible_selected_raw_sample_count != selected_evidence.raw_eligible_sample_count
            or self.selected_effective_sample_size != selected_evidence.effective_sample_size
            or self.eligible_selected_raw_sample_count <= 0
        ):
            raise ExperienceEstimationError("selected evidence provenance is inconsistent")
        if self.availability is ExperienceEstimateAvailability.AVAILABLE:
            if not selected_evidence.sufficient or self.confidence not in {
                ExperienceEvidenceConfidence.LOW,
                ExperienceEvidenceConfidence.MEDIUM,
                ExperienceEvidenceConfidence.HIGH,
            }:
                raise ExperienceEstimationError("available estimate sufficiency is inconsistent")
        elif (
            self.availability is not ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE
            or selected_evidence.sufficient
            or self.confidence is not ExperienceEvidenceConfidence.INSUFFICIENT
        ):
            raise ExperienceEstimationError("insufficient estimate state is inconsistent")
        decimals = (
            self.prior_external_effective_sample_size,
            self.prior_alpha,
            self.prior_beta,
            self.posterior_alpha,
            self.posterior_beta,
            self.posterior_mean_win_probability,
            self.recency_weighted_win_rate,
            self.recency_weighted_expectancy_r,
            self.recency_weighted_expectancy_net_quote,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals):
            raise ExperienceEstimationError("available estimate contains an invalid Decimal")
        probabilities = (self.posterior_mean_win_probability, self.recency_weighted_win_rate)
        if any(value is None or not Decimal(0) <= value <= Decimal(1) for value in probabilities):
            raise ExperienceEstimationError("estimate probability is outside [0, 1]")
        if (
            not isinstance(self.quote_currency, str)
            or not self.quote_currency
            or self.quote_currency != self.quote_currency.strip().upper()
        ):
            raise ExperienceEstimationError("estimate quote currency is invalid")
        if self.prior_source is ExperiencePriorSource.NEUTRAL_PRIOR:
            if self.prior_source_level is not None or self.prior_source_key is not None:
                raise ExperienceEstimationError("neutral prior cannot name a hierarchy source")
        elif self.prior_source is ExperiencePriorSource.HIERARCHICAL:
            if (
                self.prior_source_key not in expected_keys
                or self.prior_source_level is not self.prior_source_key.level
                or expected_keys.index(self.prior_source_key)
                <= expected_keys.index(self.selected_key)
            ):
                raise ExperienceEstimationError("hierarchical prior provenance is invalid")
        else:
            raise ExperienceEstimationError("prior source is invalid")


class ExperienceEstimator:
    """Pure causal estimator over sealed Phase 12.1 samples."""

    __slots__ = ("_configuration",)

    def __init__(self, configuration: ExperienceEstimationConfiguration | None = None) -> None:
        if configuration is not None and not isinstance(
            configuration, ExperienceEstimationConfiguration
        ):
            raise TypeError("configuration must be an ExperienceEstimationConfiguration")
        self._configuration = configuration or ExperienceEstimationConfiguration()
        self._configuration.validate()

    @property
    def configuration(self) -> ExperienceEstimationConfiguration:
        return self._configuration

    def estimate(
        self,
        target: ExperienceContextFingerprint,
        snapshot: ExperienceTradeSampleSnapshot,
        as_of: datetime,
    ) -> ExperienceEstimate:
        if not isinstance(target, ExperienceContextFingerprint):
            raise TypeError("target must be an ExperienceContextFingerprint")
        if not isinstance(snapshot, ExperienceTradeSampleSnapshot):
            raise TypeError("snapshot must be an ExperienceTradeSampleSnapshot")
        target.validate()
        require_utc(as_of, field="experience estimate as_of")
        self._configuration.validate()
        owned_samples = _validated_owned_samples(snapshot)
        excluded_future = sum(sample.exit_fill_at > as_of for sample in owned_samples)
        eligible = tuple(sample for sample in owned_samples if sample.exit_fill_at <= as_of)
        hierarchy = experience_context_hierarchy(target)
        matching_by_key = tuple(
            tuple(sample for sample in eligible if key in sample.hierarchical_keys)
            for key in hierarchy
        )
        evidence = tuple(
            _hierarchy_evidence(key, samples, as_of, self._configuration)
            for key, samples in zip(hierarchy, matching_by_key, strict=True)
        )
        if not matching_by_key[-1]:
            return _no_history_estimate(
                deepcopy(target), as_of, evidence, excluded_future, self._configuration
            )

        selected_index = next(
            (index for index, item in enumerate(evidence) if item.sufficient),
            len(hierarchy) - 1,
        )
        selected_key = hierarchy[selected_index]
        selected_samples = matching_by_key[selected_index]
        selected_evidence = evidence[selected_index]
        selected_snapshot = ExperienceTradeSampleSnapshot(
            tuple(sorted(selected_samples, key=lambda sample: sample.sort_key))
        )
        try:
            descriptive = ExperienceAggregator().aggregate(selected_snapshot)
        except ExperienceAggregationError as error:
            raise ExperienceEstimationError(
                "selected samples have incompatible descriptive authority"
            ) from error
        selected_aggregate = next(
            aggregate for aggregate in descriptive.aggregates if aggregate.key == selected_key
        )
        if selected_aggregate.sample_count != selected_evidence.raw_eligible_sample_count:
            raise ExperienceEstimationError("selected aggregate sample authority is inconsistent")
        weighted = _weighted_evidence(selected_samples, as_of, self._configuration)
        prior = _hierarchical_prior(
            hierarchy,
            matching_by_key,
            selected_index,
            selected_samples,
            as_of,
            self._configuration,
        )
        with localcontext(_ESTIMATION_DECIMAL_CONTEXT):
            posterior_alpha = prior.alpha + weighted.successes
            posterior_beta = prior.beta + weighted.failures
            posterior_mean = posterior_alpha / (posterior_alpha + posterior_beta)
            weighted_win_rate = weighted.successes / weighted.effective_sample_size
            weighted_expectancy_r = weighted.realized_r_sum / weighted.effective_sample_size
            weighted_expectancy_quote = weighted.net_quote_sum / weighted.effective_sample_size
        sufficient = selected_evidence.sufficient
        return ExperienceEstimate(
            target_fingerprint=deepcopy(target),
            as_of=as_of,
            availability=(
                ExperienceEstimateAvailability.AVAILABLE
                if sufficient
                else ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE
            ),
            selected_key=deepcopy(selected_key),
            confidence=_confidence(selected_evidence, self._configuration),
            examined_hierarchy=deepcopy(evidence),
            eligible_selected_raw_sample_count=selected_aggregate.sample_count,
            selected_effective_sample_size=selected_evidence.effective_sample_size,
            excluded_future_sample_count=excluded_future,
            prior_source=prior.source,
            prior_source_level=prior.source_level,
            prior_source_key=deepcopy(prior.source_key),
            prior_external_effective_sample_size=prior.external_effective_sample_size,
            prior_alpha=prior.alpha,
            prior_beta=prior.beta,
            posterior_alpha=posterior_alpha,
            posterior_beta=posterior_beta,
            posterior_mean_win_probability=posterior_mean,
            recency_weighted_win_rate=weighted_win_rate,
            recency_weighted_expectancy_r=weighted_expectancy_r,
            quote_currency=selected_aggregate.quote_currency,
            recency_weighted_expectancy_net_quote=weighted_expectancy_quote,
            estimation_policy_version=self._configuration.estimation_policy_version,
            recency_policy_version=self._configuration.recency_policy_version,
        )


@dataclass(frozen=True, slots=True)
class _WeightedEvidence:
    effective_sample_size: Decimal
    successes: Decimal
    failures: Decimal
    realized_r_sum: Decimal
    net_quote_sum: Decimal


@dataclass(frozen=True, slots=True)
class _Prior:
    source: ExperiencePriorSource
    source_level: ExperienceFallbackLevel | None
    source_key: ExperienceContextKey | None
    external_effective_sample_size: Decimal
    alpha: Decimal
    beta: Decimal


def _validated_owned_samples(
    snapshot: ExperienceTradeSampleSnapshot,
) -> tuple[ExperienceTradeSample, ...]:
    if snapshot.sample_snapshot_schema_version != _SAMPLE_SNAPSHOT_SCHEMA_VERSION:
        raise ExperienceEstimationError("sample snapshot schema version mismatch")
    if not isinstance(snapshot.samples, tuple):
        raise TypeError("sample snapshot samples must be an immutable tuple")
    owned = deepcopy(snapshot.samples)
    identities: set[object] = set()
    for sample in owned:
        if not isinstance(sample, ExperienceTradeSample):
            raise TypeError("sample snapshot contains an invalid sample")
        sample.validate()
        if sample.decision_identity in identities:
            raise ExperienceEstimationError("duplicate DecisionIdentity sample")
        identities.add(sample.decision_identity)
    return tuple(sorted(owned, key=lambda sample: sample.sort_key))


def _hierarchy_evidence(
    key: ExperienceContextKey,
    samples: tuple[ExperienceTradeSample, ...],
    as_of: datetime,
    configuration: ExperienceEstimationConfiguration,
) -> ExperienceHierarchyEvidence:
    effective = _weighted_evidence(samples, as_of, configuration).effective_sample_size
    return ExperienceHierarchyEvidence(
        key=deepcopy(key),
        raw_eligible_sample_count=len(samples),
        effective_sample_size=effective,
        sufficient=(
            len(samples) >= configuration.minimum_raw_sample_count
            and effective >= configuration.minimum_effective_sample_size
        ),
    )


def _weighted_evidence(
    samples: tuple[ExperienceTradeSample, ...],
    as_of: datetime,
    configuration: ExperienceEstimationConfiguration,
) -> _WeightedEvidence:
    effective = Decimal(0)
    successes = Decimal(0)
    failures = Decimal(0)
    realized_r_sum = Decimal(0)
    net_quote_sum = Decimal(0)
    with localcontext(_ESTIMATION_DECIMAL_CONTEXT):
        for sample in samples:
            weight = _recency_weight(as_of - sample.exit_fill_at, configuration)
            effective += weight
            if sample.outcome_classification == "PROFIT":
                successes += weight
            else:
                failures += weight
            realized_r_sum += weight * sample.realized_r_net
            net_quote_sum += weight * sample.net_realized_pnl
    return _WeightedEvidence(effective, successes, failures, realized_r_sum, net_quote_sum)


def _recency_weight(age: timedelta, configuration: ExperienceEstimationConfiguration) -> Decimal:
    if age < timedelta():
        raise ExperienceEstimationError("future samples cannot receive a recency weight")
    if age >= configuration.recency_horizon:
        return configuration.minimum_recency_weight
    age_microseconds = Decimal(_timedelta_microseconds(age))
    horizon_microseconds = Decimal(_timedelta_microseconds(configuration.recency_horizon))
    with localcontext(_ESTIMATION_DECIMAL_CONTEXT):
        fraction = age_microseconds / horizon_microseconds
        return Decimal(1) - (Decimal(1) - configuration.minimum_recency_weight) * fraction


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        value.days * _SECONDS_PER_DAY + value.seconds
    ) * _MICROSECONDS_PER_SECOND + value.microseconds


def _hierarchical_prior(
    hierarchy: tuple[ExperienceContextKey, ...],
    matching_by_key: tuple[tuple[ExperienceTradeSample, ...], ...],
    selected_index: int,
    selected_samples: tuple[ExperienceTradeSample, ...],
    as_of: datetime,
    configuration: ExperienceEstimationConfiguration,
) -> _Prior:
    selected_identities = {sample.decision_identity for sample in selected_samples}
    for parent_index in range(selected_index + 1, len(hierarchy)):
        external = tuple(
            sample
            for sample in matching_by_key[parent_index]
            if sample.decision_identity not in selected_identities
        )
        if not external:
            continue
        evidence = _weighted_evidence(external, as_of, configuration)
        with localcontext(_ESTIMATION_DECIMAL_CONTEXT):
            parent_mean = evidence.successes / evidence.effective_sample_size
            strength = min(
                evidence.effective_sample_size,
                configuration.hierarchical_prior_strength_cap,
            )
            alpha = configuration.neutral_prior_alpha + parent_mean * strength
            beta = configuration.neutral_prior_beta + (Decimal(1) - parent_mean) * strength
        return _Prior(
            ExperiencePriorSource.HIERARCHICAL,
            hierarchy[parent_index].level,
            deepcopy(hierarchy[parent_index]),
            evidence.effective_sample_size,
            alpha,
            beta,
        )
    return _Prior(
        ExperiencePriorSource.NEUTRAL_PRIOR,
        None,
        None,
        Decimal(0),
        configuration.neutral_prior_alpha,
        configuration.neutral_prior_beta,
    )


def _confidence(
    evidence: ExperienceHierarchyEvidence,
    configuration: ExperienceEstimationConfiguration,
) -> ExperienceEvidenceConfidence:
    if not evidence.sufficient:
        return ExperienceEvidenceConfidence.INSUFFICIENT
    if evidence.effective_sample_size >= configuration.high_confidence_effective_sample_size:
        return ExperienceEvidenceConfidence.HIGH
    if evidence.effective_sample_size >= configuration.medium_confidence_effective_sample_size:
        return ExperienceEvidenceConfidence.MEDIUM
    return ExperienceEvidenceConfidence.LOW


def _no_history_estimate(
    target: ExperienceContextFingerprint,
    as_of: datetime,
    evidence: tuple[ExperienceHierarchyEvidence, ...],
    excluded_future: int,
    configuration: ExperienceEstimationConfiguration,
) -> ExperienceEstimate:
    return ExperienceEstimate(
        target_fingerprint=target,
        as_of=as_of,
        availability=ExperienceEstimateAvailability.NO_HISTORY,
        selected_key=None,
        confidence=ExperienceEvidenceConfidence.NO_HISTORY,
        examined_hierarchy=deepcopy(evidence),
        eligible_selected_raw_sample_count=0,
        selected_effective_sample_size=Decimal(0),
        excluded_future_sample_count=excluded_future,
        prior_source=None,
        prior_source_level=None,
        prior_source_key=None,
        prior_external_effective_sample_size=None,
        prior_alpha=None,
        prior_beta=None,
        posterior_alpha=None,
        posterior_beta=None,
        posterior_mean_win_probability=None,
        recency_weighted_win_rate=None,
        recency_weighted_expectancy_r=None,
        quote_currency=None,
        recency_weighted_expectancy_net_quote=None,
        estimation_policy_version=configuration.estimation_policy_version,
        recency_policy_version=configuration.recency_policy_version,
    )
