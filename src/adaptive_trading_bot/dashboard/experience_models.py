"""Detached read models for process-local Experience observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceContextKey,
    ExperienceEstimateAvailability,
    ExperienceEvidenceConfidence,
    ExperienceFallbackLevel,
    ExperiencePriorSource,
    TrendStrengthBucket,
    experience_context_hierarchy,
)
from adaptive_trading_bot.regime import (
    Direction,
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    RegimeLabel,
    VolatilityState,
    VolumeState,
)

MAX_DASHBOARD_EXPERIENCE_CONTEXTS = 200
PROCESS_LOCAL_HISTORY_SCOPE = "PROCESS_LOCAL"
PROCESS_LOCAL_HISTORY_LIMITATION = (
    "Experience history is process-local and is not durable across process restarts."
)


class DashboardExperienceValidationError(ValueError):
    """An Experience query or projected read model is inconsistent."""


@dataclass(frozen=True, slots=True)
class DashboardExperienceContextQuery:
    as_of: datetime
    limit: int = MAX_DASHBOARD_EXPERIENCE_CONTEXTS

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        require_utc(self.as_of, field="dashboard Experience contexts as_of")
        _validate_limit(self.limit)


@dataclass(frozen=True, slots=True)
class DashboardExperienceEstimateQuery:
    target_fingerprint: ExperienceContextFingerprint
    as_of: datetime

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.target_fingerprint, ExperienceContextFingerprint):
            raise TypeError("Experience target must be an ExperienceContextFingerprint")
        self.target_fingerprint.validate()
        require_utc(self.as_of, field="dashboard Experience estimate as_of")


@dataclass(frozen=True, slots=True)
class DashboardExperienceFingerprint:
    symbol: str
    strategy_id: str
    strategy_version: str
    entry_timeframe: str
    regime_label: str
    direction: str
    trend_strength_bucket: str
    volatility_state: str
    volume_state: str
    higher_timeframe_bias: str
    multi_timeframe_alignment: str
    fingerprint_schema_version: str

    def validate(self) -> None:
        _domain_fingerprint(self)

    @property
    def sort_key(self) -> tuple[str, ...]:
        return (
            self.strategy_id,
            self.strategy_version,
            self.symbol,
            self.entry_timeframe,
            self.regime_label,
            self.direction,
            self.trend_strength_bucket,
            self.volatility_state,
            self.volume_state,
            self.higher_timeframe_bias,
            self.multi_timeframe_alignment,
        )


@dataclass(frozen=True, slots=True)
class DashboardExperienceContextKey:
    level: str
    strategy_id: str
    strategy_version: str
    symbol: str | None
    entry_timeframe: str | None
    regime_label: str | None
    direction: str | None
    trend_strength_bucket: str | None
    volatility_state: str | None
    volume_state: str | None
    higher_timeframe_bias: str | None
    multi_timeframe_alignment: str | None
    key_schema_version: str

    def validate(self) -> None:
        _domain_key(self)


@dataclass(frozen=True, slots=True)
class DashboardExperienceHierarchyEvidence:
    key: DashboardExperienceContextKey
    raw_eligible_sample_count: int
    effective_sample_size: Decimal
    sufficient: bool
    hierarchy_evidence_schema_version: str

    def validate(self) -> None:
        self.key.validate()
        if type(self.raw_eligible_sample_count) is not int or self.raw_eligible_sample_count < 0:
            raise DashboardExperienceValidationError("Experience raw sample count is invalid")
        _finite_decimal(
            self.effective_sample_size,
            field="Experience effective sample size",
            non_negative=True,
        )
        if self.effective_sample_size > Decimal(self.raw_eligible_sample_count):
            raise DashboardExperienceValidationError(
                "Experience effective sample size exceeds raw count"
            )
        if type(self.sufficient) is not bool:
            raise DashboardExperienceValidationError("Experience sufficiency flag is invalid")
        _canonical_text(
            self.hierarchy_evidence_schema_version,
            field="Experience hierarchy evidence schema version",
        )


@dataclass(frozen=True, slots=True)
class DashboardExperienceContextPage:
    as_of: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    contexts: tuple[DashboardExperienceFingerprint, ...]
    history_scope: str = PROCESS_LOCAL_HISTORY_SCOPE
    history_limitation: str = PROCESS_LOCAL_HISTORY_LIMITATION

    def validate(self) -> None:
        require_utc(self.as_of, field="dashboard Experience contexts as_of")
        _validate_limit(self.limit)
        _validate_page_counts(
            self.total_matched,
            self.returned_count,
            self.truncated,
            len(self.contexts),
            self.limit,
        )
        for context in self.contexts:
            if not isinstance(context, DashboardExperienceFingerprint):
                raise TypeError("Experience contexts page contains an invalid context")
            context.validate()
        if self.contexts != tuple(sorted(self.contexts, key=lambda item: item.sort_key)):
            raise DashboardExperienceValidationError("Experience contexts are not canonical")
        if len(set(self.contexts)) != len(self.contexts):
            raise DashboardExperienceValidationError("Experience contexts are not distinct")
        _validate_process_local(self.history_scope, self.history_limitation)


@dataclass(frozen=True, slots=True)
class DashboardExperienceEstimate:
    target_fingerprint: DashboardExperienceFingerprint
    as_of: datetime
    availability: str
    selected_key: DashboardExperienceContextKey | None
    selected_level: str | None
    confidence: str
    examined_hierarchy: tuple[DashboardExperienceHierarchyEvidence, ...]
    eligible_selected_raw_sample_count: int
    selected_effective_sample_size: Decimal
    excluded_future_sample_count: int
    prior_source: str | None
    prior_source_level: str | None
    prior_source_key: DashboardExperienceContextKey | None
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
    estimate_schema_version: str
    history_scope: str = PROCESS_LOCAL_HISTORY_SCOPE
    history_limitation: str = PROCESS_LOCAL_HISTORY_LIMITATION

    def validate(self) -> None:
        self.target_fingerprint.validate()
        require_utc(self.as_of, field="dashboard Experience estimate as_of")
        try:
            availability = ExperienceEstimateAvailability(self.availability)
            confidence = ExperienceEvidenceConfidence(self.confidence)
        except ValueError as error:
            raise DashboardExperienceValidationError("Experience status is invalid") from error
        hierarchy = tuple(
            _project_domain_key(key)
            for key in experience_context_hierarchy(_domain_fingerprint(self.target_fingerprint))
        )
        if (
            len(self.examined_hierarchy) != len(hierarchy)
            or tuple(item.key for item in self.examined_hierarchy) != hierarchy
        ):
            raise DashboardExperienceValidationError("Experience hierarchy provenance is invalid")
        for item in self.examined_hierarchy:
            if not isinstance(item, DashboardExperienceHierarchyEvidence):
                raise TypeError("Experience hierarchy contains an invalid row")
            item.validate()
        for name, count in (
            ("selected raw sample count", self.eligible_selected_raw_sample_count),
            ("excluded future sample count", self.excluded_future_sample_count),
        ):
            if type(count) is not int or count < 0:
                raise DashboardExperienceValidationError(f"Experience {name} is invalid")
        _finite_decimal(
            self.selected_effective_sample_size,
            field="Experience selected effective sample size",
            non_negative=True,
        )
        _canonical_text(self.estimation_policy_version, field="Experience estimation policy")
        _canonical_text(self.recency_policy_version, field="Experience recency policy")
        _canonical_text(self.estimate_schema_version, field="Experience estimate schema")
        _validate_process_local(self.history_scope, self.history_limitation)
        if availability is ExperienceEstimateAvailability.NO_HISTORY:
            self._validate_no_history(confidence)
        else:
            self._validate_estimate(hierarchy, availability, confidence)

    def _validate_no_history(self, confidence: ExperienceEvidenceConfidence) -> None:
        unavailable = (
            self.selected_key,
            self.selected_level,
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
            confidence is not ExperienceEvidenceConfidence.NO_HISTORY
            or self.eligible_selected_raw_sample_count != 0
            or self.selected_effective_sample_size != 0
            or any(value is not None for value in unavailable)
            or any(
                item.raw_eligible_sample_count != 0
                or item.effective_sample_size != 0
                or item.sufficient
                for item in self.examined_hierarchy
            )
        ):
            raise DashboardExperienceValidationError("NO_HISTORY Experience state is inconsistent")

    def _validate_estimate(
        self,
        hierarchy: tuple[DashboardExperienceContextKey, ...],
        availability: ExperienceEstimateAvailability,
        confidence: ExperienceEvidenceConfidence,
    ) -> None:
        if self.selected_key not in hierarchy or self.selected_level != self.selected_key.level:
            raise DashboardExperienceValidationError("Experience selected hierarchy is invalid")
        selected = next(item for item in self.examined_hierarchy if item.key == self.selected_key)
        if (
            selected.raw_eligible_sample_count != self.eligible_selected_raw_sample_count
            or selected.effective_sample_size != self.selected_effective_sample_size
            or selected.raw_eligible_sample_count == 0
        ):
            raise DashboardExperienceValidationError("Experience selected evidence is inconsistent")
        if availability is ExperienceEstimateAvailability.AVAILABLE:
            if not selected.sufficient or confidence not in {
                ExperienceEvidenceConfidence.LOW,
                ExperienceEvidenceConfidence.MEDIUM,
                ExperienceEvidenceConfidence.HIGH,
            }:
                raise DashboardExperienceValidationError("available Experience state is invalid")
        elif (
            availability is not ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE
            or selected.sufficient
            or confidence is not ExperienceEvidenceConfidence.INSUFFICIENT
        ):
            raise DashboardExperienceValidationError("insufficient Experience state is invalid")
        for name, value in (
            ("prior external effective N", self.prior_external_effective_sample_size),
            ("prior alpha", self.prior_alpha),
            ("prior beta", self.prior_beta),
            ("posterior alpha", self.posterior_alpha),
            ("posterior beta", self.posterior_beta),
            ("posterior win probability", self.posterior_mean_win_probability),
            ("weighted win rate", self.recency_weighted_win_rate),
            ("weighted expectancy R", self.recency_weighted_expectancy_r),
            ("weighted net quote expectancy", self.recency_weighted_expectancy_net_quote),
        ):
            _finite_decimal(value, field=f"Experience {name}")
        for probability in (
            self.posterior_mean_win_probability,
            self.recency_weighted_win_rate,
        ):
            if probability is None or not Decimal(0) <= probability <= Decimal(1):
                raise DashboardExperienceValidationError("Experience probability is invalid")
        _canonical_text(self.quote_currency, field="Experience quote currency")
        if self.prior_source is None:
            raise DashboardExperienceValidationError("Experience prior source is invalid")
        try:
            source = ExperiencePriorSource(self.prior_source)
        except ValueError as error:
            raise DashboardExperienceValidationError(
                "Experience prior source is invalid"
            ) from error
        if source is ExperiencePriorSource.NEUTRAL_PRIOR:
            if self.prior_source_level is not None or self.prior_source_key is not None:
                raise DashboardExperienceValidationError("neutral Experience prior names a source")
        else:
            if self.prior_source_key not in hierarchy:
                raise DashboardExperienceValidationError("Experience prior key is invalid")
            if self.prior_source_level != self.prior_source_key.level:
                raise DashboardExperienceValidationError("Experience prior level is invalid")
            if hierarchy.index(self.prior_source_key) <= hierarchy.index(self.selected_key):
                raise DashboardExperienceValidationError("Experience prior is not broader")


def project_experience_fingerprint(
    fingerprint: ExperienceContextFingerprint,
) -> DashboardExperienceFingerprint:
    fingerprint.validate()
    return DashboardExperienceFingerprint(
        str(fingerprint.symbol),
        fingerprint.strategy_id,
        fingerprint.strategy_version,
        fingerprint.entry_timeframe.value,
        fingerprint.regime_label.value,
        fingerprint.direction.value,
        fingerprint.trend_strength_bucket.value,
        fingerprint.volatility_state.value,
        fingerprint.volume_state.value,
        fingerprint.higher_timeframe_bias.value,
        fingerprint.multi_timeframe_alignment.value,
        fingerprint.fingerprint_schema_version,
    )


def _project_domain_key(key: ExperienceContextKey) -> DashboardExperienceContextKey:
    key.validate()
    return DashboardExperienceContextKey(
        key.level.value,
        key.strategy_id,
        key.strategy_version,
        str(key.symbol) if key.symbol is not None else None,
        key.entry_timeframe.value if key.entry_timeframe is not None else None,
        key.regime_label.value if key.regime_label is not None else None,
        key.direction.value if key.direction is not None else None,
        key.trend_strength_bucket.value if key.trend_strength_bucket is not None else None,
        key.volatility_state.value if key.volatility_state is not None else None,
        key.volume_state.value if key.volume_state is not None else None,
        key.higher_timeframe_bias.value if key.higher_timeframe_bias is not None else None,
        (
            key.multi_timeframe_alignment.value
            if key.multi_timeframe_alignment is not None
            else None
        ),
        key.key_schema_version,
    )


def _domain_fingerprint(value: DashboardExperienceFingerprint) -> ExperienceContextFingerprint:
    try:
        fingerprint = ExperienceContextFingerprint(
            TradingSymbol.parse(value.symbol),
            value.strategy_id,
            value.strategy_version,
            Timeframe(value.entry_timeframe),
            RegimeLabel(value.regime_label),
            Direction(value.direction),
            TrendStrengthBucket(value.trend_strength_bucket),
            VolatilityState(value.volatility_state),
            VolumeState(value.volume_state),
            HigherTimeframeBias(value.higher_timeframe_bias),
            MultiTimeframeAlignment(value.multi_timeframe_alignment),
        )
    except (TypeError, ValueError) as error:
        raise DashboardExperienceValidationError("Experience fingerprint is invalid") from error
    if fingerprint.fingerprint_schema_version != value.fingerprint_schema_version:
        raise DashboardExperienceValidationError("Experience fingerprint schema is invalid")
    return fingerprint


def _domain_key(value: DashboardExperienceContextKey) -> ExperienceContextKey:
    try:
        key = ExperienceContextKey(
            ExperienceFallbackLevel(value.level),
            value.strategy_id,
            value.strategy_version,
            symbol=TradingSymbol.parse(value.symbol) if value.symbol is not None else None,
            entry_timeframe=(
                Timeframe(value.entry_timeframe) if value.entry_timeframe is not None else None
            ),
            regime_label=RegimeLabel(value.regime_label)
            if value.regime_label is not None
            else None,
            direction=Direction(value.direction) if value.direction is not None else None,
            trend_strength_bucket=(
                TrendStrengthBucket(value.trend_strength_bucket)
                if value.trend_strength_bucket is not None
                else None
            ),
            volatility_state=(
                VolatilityState(value.volatility_state)
                if value.volatility_state is not None
                else None
            ),
            volume_state=VolumeState(value.volume_state)
            if value.volume_state is not None
            else None,
            higher_timeframe_bias=(
                HigherTimeframeBias(value.higher_timeframe_bias)
                if value.higher_timeframe_bias is not None
                else None
            ),
            multi_timeframe_alignment=(
                MultiTimeframeAlignment(value.multi_timeframe_alignment)
                if value.multi_timeframe_alignment is not None
                else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise DashboardExperienceValidationError("Experience hierarchy key is invalid") from error
    if key.key_schema_version != value.key_schema_version:
        raise DashboardExperienceValidationError("Experience key schema is invalid")
    return key


def _validate_limit(limit: int) -> None:
    if type(limit) is not int:
        raise TypeError("Experience context limit must be an integer")
    if not 1 <= limit <= MAX_DASHBOARD_EXPERIENCE_CONTEXTS:
        raise DashboardExperienceValidationError("Experience context limit is outside range")


def _validate_page_counts(
    total: int, returned: int, truncated: bool, actual: int, limit: int
) -> None:
    if type(total) is not int or type(returned) is not int or total < 0 or returned < 0:
        raise DashboardExperienceValidationError("Experience context counts are invalid")
    if returned != actual or returned > limit or total < returned:
        raise DashboardExperienceValidationError("Experience context page counts are inconsistent")
    if type(truncated) is not bool or truncated != (total > returned):
        raise DashboardExperienceValidationError("Experience context truncation is inconsistent")


def _validate_process_local(scope: str, limitation: str) -> None:
    if scope != PROCESS_LOCAL_HISTORY_SCOPE or limitation != PROCESS_LOCAL_HISTORY_LIMITATION:
        raise DashboardExperienceValidationError("Experience history scope is untruthful")


def _canonical_text(value: str | None, *, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DashboardExperienceValidationError(f"{field} is invalid")


def _finite_decimal(value: Decimal | None, *, field: str, non_negative: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DashboardExperienceValidationError(f"{field} is invalid")
    if non_negative and value < 0:
        raise DashboardExperienceValidationError(f"{field} is negative")
