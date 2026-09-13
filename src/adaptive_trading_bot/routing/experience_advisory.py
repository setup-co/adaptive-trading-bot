"""Shadow-only Experience advice with no runtime trading authority."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from hashlib import sha256
from hmac import compare_digest
from json import dumps

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceContextKey,
    ExperienceEstimate,
    ExperienceEstimateAvailability,
    ExperienceEstimationConfiguration,
    ExperienceEstimator,
    ExperienceEvidenceConfidence,
    ExperienceFallbackLevel,
    ExperiencePriorSource,
    ExperienceTradeSampleSnapshot,
    experience_context_fingerprint,
    experience_context_hierarchy,
)
from adaptive_trading_bot.strategies import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_ADVISORY_CONFIGURATION_SCHEMA_VERSION = "1.0.0"
_ADVISORY_POLICY_VERSION = "shadow-experience-advisory-v1"
_ADVISORY_SCHEMA_VERSION = "1.0.0"
_ADVISORY_INTEGRITY_SEAL_VERSION = "1.0.0"
_CANDIDATE_ACTIONS = frozenset({CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE})
_CONFIDENCE_ORDER = {
    ExperienceEvidenceConfidence.LOW: 0,
    ExperienceEvidenceConfidence.MEDIUM: 1,
    ExperienceEvidenceConfidence.HIGH: 2,
}


class ExperienceAdvisoryError(ValueError):
    """Pre-trade or Experience authority cannot produce a safe shadow advisory."""


class ExperienceAdvisoryEvidenceState(StrEnum):
    """Usability of historical evidence under the configured advisory policy."""

    NOT_APPLICABLE_NO_TRADE = "NOT_APPLICABLE_NO_TRADE"
    NO_HISTORY = "NO_HISTORY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    LOW_EVIDENCE = "LOW_EVIDENCE"
    USABLE_EVIDENCE = "USABLE_EVIDENCE"


class ExperienceOutcomeBias(StrEnum):
    """Transparent outcome classification, never an execution recommendation."""

    UNAVAILABLE = "UNAVAILABLE"
    FAVORABLE = "FAVORABLE"
    MIXED = "MIXED"
    ADVERSE = "ADVERSE"


class ExperienceAdvisoryMode(StrEnum):
    """The only mode supported before a separately reviewed routing phase."""

    SHADOW_ONLY = "SHADOW_ONLY"


@dataclass(frozen=True, slots=True)
class ExperienceAdvisoryConfiguration:
    """Versioned counterfactual policy with zero runtime execution authority."""

    minimum_actionable_confidence: ExperienceEvidenceConfidence = ExperienceEvidenceConfidence.HIGH
    shadow_blocking_enabled: bool = True
    configuration_schema_version: str = field(
        init=False, default=_ADVISORY_CONFIGURATION_SCHEMA_VERSION
    )
    advisory_policy_version: str = field(init=False, default=_ADVISORY_POLICY_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.configuration_schema_version != _ADVISORY_CONFIGURATION_SCHEMA_VERSION:
            raise ExperienceAdvisoryError("advisory configuration schema version mismatch")
        if self.advisory_policy_version != _ADVISORY_POLICY_VERSION:
            raise ExperienceAdvisoryError("advisory policy version mismatch")
        if self.minimum_actionable_confidence not in _CONFIDENCE_ORDER:
            raise ExperienceAdvisoryError("minimum actionable confidence is invalid")
        if type(self.shadow_blocking_enabled) is not bool:
            raise ExperienceAdvisoryError("shadow blocking flag is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceCandidateAdvisory:
    """Sealed audit record describing a counterfactual shadow disposition."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    strategy_id: str
    strategy_version: str
    original_action: CandidateAction
    target_fingerprint: ExperienceContextFingerprint
    estimator_availability: ExperienceEstimateAvailability | None
    selected_hierarchy_level: ExperienceFallbackLevel | None
    selected_hierarchy_key: ExperienceContextKey | None
    evidence_confidence: ExperienceEvidenceConfidence | None
    posterior_mean_win_probability: Decimal | None
    recency_weighted_expectancy_r: Decimal | None
    selected_raw_sample_count: int
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
    estimation_policy_version: str
    recency_policy_version: str
    estimate_schema_version: str | None
    evidence_state: ExperienceAdvisoryEvidenceState
    outcome_bias: ExperienceOutcomeBias
    would_block_candidate: bool
    advisory_mode: ExperienceAdvisoryMode
    may_authorize_trade: bool
    may_modify_strategy_action: bool
    may_bypass_risk: bool
    minimum_actionable_confidence: ExperienceEvidenceConfidence
    shadow_blocking_enabled: bool
    advisory_configuration_schema_version: str
    advisory_policy_version: str
    advisory_schema_version: str = field(init=False, default=_ADVISORY_SCHEMA_VERSION)
    advisory_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "advisory_integrity_seal", _advisory_integrity_seal(self))

    def validate(self) -> None:
        seal = self.advisory_integrity_seal
        if not isinstance(seal, str) or not compare_digest(seal, _advisory_integrity_seal(self)):
            raise ExperienceAdvisoryError("advisory integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.advisory_schema_version != _ADVISORY_SCHEMA_VERSION:
            raise ExperienceAdvisoryError("advisory schema version mismatch")
        if not isinstance(self.source_id, SourceIdentity):
            raise ExperienceAdvisoryError("advisory source identity is invalid")
        _canonical_text("source identity", self.source_id.value)
        if not isinstance(self.symbol, TradingSymbol) or self.symbol.base not in {"BTC", "ETH"}:
            raise ExperienceAdvisoryError("advisory symbol is outside V1")
        require_utc(self.observed_at, field="Experience advisory observed_at")
        _canonical_text("strategy_id", self.strategy_id)
        _canonical_text("strategy_version", self.strategy_version)
        if not isinstance(self.original_action, CandidateAction):
            raise ExperienceAdvisoryError("original strategy action is invalid")
        self.target_fingerprint.validate()
        if (
            self.target_fingerprint.symbol != self.symbol
            or self.target_fingerprint.strategy_id != self.strategy_id
            or self.target_fingerprint.strategy_version != self.strategy_version
        ):
            raise ExperienceAdvisoryError("advisory fingerprint identity is inconsistent")
        for name, count in (
            ("selected raw sample count", self.selected_raw_sample_count),
            ("excluded future sample count", self.excluded_future_sample_count),
        ):
            if type(count) is not int or count < 0:
                raise ExperienceAdvisoryError(f"{name} is invalid")
        _finite_decimal(
            self.selected_effective_sample_size,
            field_name="selected effective sample size",
            non_negative=True,
        )
        if self.selected_effective_sample_size > Decimal(self.selected_raw_sample_count):
            raise ExperienceAdvisoryError("selected effective sample size exceeds raw count")
        if self.advisory_mode is not ExperienceAdvisoryMode.SHADOW_ONLY:
            raise ExperienceAdvisoryError("advisory mode must remain shadow-only")
        if any(
            value is not False
            for value in (
                self.may_authorize_trade,
                self.may_modify_strategy_action,
                self.may_bypass_risk,
            )
        ):
            raise ExperienceAdvisoryError("advisory exposes forbidden trading authority")
        if type(self.would_block_candidate) is not bool:
            raise ExperienceAdvisoryError("shadow block flag is invalid")
        if self.minimum_actionable_confidence not in _CONFIDENCE_ORDER:
            raise ExperienceAdvisoryError("minimum actionable confidence is invalid")
        if type(self.shadow_blocking_enabled) is not bool:
            raise ExperienceAdvisoryError("shadow blocking flag is invalid")
        if self.advisory_policy_version != _ADVISORY_POLICY_VERSION:
            raise ExperienceAdvisoryError("advisory policy version mismatch")
        _canonical_text("estimation policy version", self.estimation_policy_version)
        _canonical_text("recency policy version", self.recency_policy_version)
        _canonical_text(
            "advisory configuration schema version",
            self.advisory_configuration_schema_version,
        )
        if self.original_action is CandidateAction.NO_TRADE:
            self._validate_no_trade()
        else:
            self._validate_candidate()

    def _validate_no_trade(self) -> None:
        unavailable = (
            self.estimator_availability,
            self.selected_hierarchy_level,
            self.selected_hierarchy_key,
            self.evidence_confidence,
            self.posterior_mean_win_probability,
            self.recency_weighted_expectancy_r,
            self.prior_source,
            self.prior_source_level,
            self.prior_source_key,
            self.prior_external_effective_sample_size,
            self.prior_alpha,
            self.prior_beta,
            self.posterior_alpha,
            self.posterior_beta,
            self.estimate_schema_version,
        )
        if (
            self.evidence_state is not ExperienceAdvisoryEvidenceState.NOT_APPLICABLE_NO_TRADE
            or self.outcome_bias is not ExperienceOutcomeBias.UNAVAILABLE
            or self.would_block_candidate
            or self.selected_raw_sample_count != 0
            or self.selected_effective_sample_size != 0
            or self.excluded_future_sample_count != 0
            or any(value is not None for value in unavailable)
        ):
            raise ExperienceAdvisoryError("NO_TRADE advisory contains actionable evidence")

    def _validate_candidate(self) -> None:
        if self.original_action not in _CANDIDATE_ACTIONS:
            raise ExperienceAdvisoryError("candidate advisory action is invalid")
        if not isinstance(self.estimator_availability, ExperienceEstimateAvailability):
            raise ExperienceAdvisoryError("estimator availability is invalid")
        if not isinstance(self.evidence_confidence, ExperienceEvidenceConfidence):
            raise ExperienceAdvisoryError("evidence confidence is invalid")
        _canonical_text("estimate schema version", self.estimate_schema_version)
        if self.selected_hierarchy_key is None:
            if self.selected_hierarchy_level is not None:
                raise ExperienceAdvisoryError("selected hierarchy provenance is inconsistent")
        elif self.selected_hierarchy_level is not self.selected_hierarchy_key.level:
            raise ExperienceAdvisoryError("selected hierarchy provenance is inconsistent")
        else:
            self.selected_hierarchy_key.validate()
            if self.selected_hierarchy_key not in experience_context_hierarchy(
                self.target_fingerprint
            ):
                raise ExperienceAdvisoryError("selected hierarchy key is not canonical")
        for name, value in (
            ("posterior mean win probability", self.posterior_mean_win_probability),
            ("recency-weighted expectancy R", self.recency_weighted_expectancy_r),
            ("prior external effective sample size", self.prior_external_effective_sample_size),
            ("prior alpha", self.prior_alpha),
            ("prior beta", self.prior_beta),
            ("posterior alpha", self.posterior_alpha),
            ("posterior beta", self.posterior_beta),
        ):
            if value is not None:
                _finite_decimal(value, field_name=name)
        if (
            self.prior_external_effective_sample_size is not None
            and self.prior_external_effective_sample_size < 0
        ):
            raise ExperienceAdvisoryError("prior external effective sample size is negative")
        if any(
            value is not None and value <= 0
            for value in (
                self.prior_alpha,
                self.prior_beta,
                self.posterior_alpha,
                self.posterior_beta,
            )
        ):
            raise ExperienceAdvisoryError("prior/posterior parameters must be positive")
        if self.posterior_mean_win_probability is not None and not Decimal(
            0
        ) <= self.posterior_mean_win_probability <= Decimal(1):
            raise ExperienceAdvisoryError("posterior mean is outside [0, 1]")
        self._validate_estimator_provenance()
        expected_state = _evidence_state_from_facts(
            self.estimator_availability,
            self.evidence_confidence,
            self.minimum_actionable_confidence,
        )
        expected_bias = _outcome_bias_from_facts(
            self.posterior_mean_win_probability,
            self.recency_weighted_expectancy_r,
            expected_state,
        )
        expected_block = (
            self.shadow_blocking_enabled
            and expected_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
            and expected_bias is ExperienceOutcomeBias.ADVERSE
        )
        if (
            self.evidence_state is not expected_state
            or self.outcome_bias is not expected_bias
            or self.would_block_candidate is not expected_block
        ):
            raise ExperienceAdvisoryError("advisory shadow classification is inconsistent")

    def _validate_estimator_provenance(self) -> None:
        statistical = (
            self.posterior_mean_win_probability,
            self.recency_weighted_expectancy_r,
            self.prior_external_effective_sample_size,
            self.prior_alpha,
            self.prior_beta,
            self.posterior_alpha,
            self.posterior_beta,
        )
        if self.estimator_availability is ExperienceEstimateAvailability.NO_HISTORY:
            if (
                self.evidence_confidence is not ExperienceEvidenceConfidence.NO_HISTORY
                or self.selected_hierarchy_key is not None
                or self.selected_raw_sample_count != 0
                or self.selected_effective_sample_size != 0
                or self.prior_source is not None
                or self.prior_source_level is not None
                or self.prior_source_key is not None
                or any(value is not None for value in statistical)
            ):
                raise ExperienceAdvisoryError("NO_HISTORY estimator provenance is inconsistent")
            return
        if self.selected_hierarchy_key is None or self.selected_raw_sample_count == 0:
            raise ExperienceAdvisoryError("selected estimator evidence is unavailable")
        if any(value is None for value in statistical):
            raise ExperienceAdvisoryError("estimator statistical provenance is incomplete")
        if self.estimator_availability is ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE:
            if self.evidence_confidence is not ExperienceEvidenceConfidence.INSUFFICIENT:
                raise ExperienceAdvisoryError("insufficient estimator confidence is inconsistent")
        elif self.evidence_confidence not in _CONFIDENCE_ORDER:
            raise ExperienceAdvisoryError("available estimator confidence is invalid")
        if self.prior_source is ExperiencePriorSource.NEUTRAL_PRIOR:
            if self.prior_source_level is not None or self.prior_source_key is not None:
                raise ExperienceAdvisoryError("neutral prior provenance is inconsistent")
        elif self.prior_source is ExperiencePriorSource.HIERARCHICAL:
            if (
                self.prior_source_key is None
                or self.prior_source_level is not self.prior_source_key.level
                or self.prior_source_key
                not in experience_context_hierarchy(self.target_fingerprint)
            ):
                raise ExperienceAdvisoryError("hierarchical prior provenance is inconsistent")
        else:
            raise ExperienceAdvisoryError("prior provenance is invalid")


class ExperienceCandidateAdvisor:
    """Pure adapter from current strategy authority to sealed shadow advice."""

    __slots__ = ("_advisory_configuration", "_estimator")

    def __init__(
        self,
        estimation_configuration: ExperienceEstimationConfiguration | None = None,
        advisory_configuration: ExperienceAdvisoryConfiguration | None = None,
    ) -> None:
        if advisory_configuration is not None and not isinstance(
            advisory_configuration, ExperienceAdvisoryConfiguration
        ):
            raise TypeError("advisory_configuration must be ExperienceAdvisoryConfiguration")
        self._estimator = ExperienceEstimator(deepcopy(estimation_configuration))
        self._advisory_configuration = deepcopy(
            advisory_configuration or ExperienceAdvisoryConfiguration()
        )
        self._advisory_configuration.validate()

    def advise(
        self,
        context: StrategyEvaluationContext,
        evaluation: StrategyEvaluation[object, object],
        samples: ExperienceTradeSampleSnapshot,
        as_of: datetime,
    ) -> ExperienceCandidateAdvisory:
        """Return detached counterfactual audit data without changing runtime behavior."""

        if not isinstance(samples, ExperienceTradeSampleSnapshot):
            raise TypeError("samples must be an ExperienceTradeSampleSnapshot")
        samples.validate()
        owned_samples = deepcopy(samples)
        fingerprint = _validate_current_authority(context, evaluation, as_of)
        if evaluation.action is CandidateAction.NO_TRADE:
            result = _no_trade_advisory(
                context,
                evaluation,
                fingerprint,
                self._estimator.configuration,
                self._advisory_configuration,
            )
        else:
            estimate = self._estimator.estimate(fingerprint, owned_samples, as_of)
            result = _candidate_advisory(
                context,
                evaluation,
                estimate,
                self._advisory_configuration,
            )
        result.validate()
        return deepcopy(result)


def _validate_current_authority(
    context: StrategyEvaluationContext,
    evaluation: StrategyEvaluation[object, object],
    as_of: datetime,
) -> ExperienceContextFingerprint:
    if not isinstance(context, StrategyEvaluationContext):
        raise TypeError("context must be a StrategyEvaluationContext")
    if not isinstance(evaluation, StrategyEvaluation):
        raise TypeError("evaluation must be a StrategyEvaluation")
    observed_at = require_utc(as_of, field="Experience advisory as_of")
    context.validate()
    if require_utc(evaluation.observed_at, field="strategy evaluation observed_at") != observed_at:
        raise ExperienceAdvisoryError("strategy evaluation and as_of timestamps differ")
    if context.observed_at != observed_at:
        raise ExperienceAdvisoryError("strategy context and as_of timestamps differ")
    if evaluation.source_id != context.source_id or evaluation.symbol != context.symbol:
        raise ExperienceAdvisoryError("strategy context and evaluation identity differ")
    _canonical_text("strategy_id", evaluation.strategy_id)
    _canonical_text("strategy_version", evaluation.strategy_version)
    if not isinstance(evaluation.action, CandidateAction):
        raise ExperienceAdvisoryError("strategy evaluation action is invalid")
    _validate_strategy_prices(evaluation)
    return experience_context_fingerprint(
        context,
        evaluation.strategy_id,
        evaluation.strategy_version,
    )


def _validate_strategy_prices(evaluation: StrategyEvaluation[object, object]) -> None:
    prices = (evaluation.reference_price, evaluation.invalidation_reference_price)
    if evaluation.action is CandidateAction.NO_TRADE:
        if any(value is not None for value in prices):
            raise ExperienceAdvisoryError("NO_TRADE evaluation contains candidate prices")
        return
    if any(
        not isinstance(value, Decimal) or not value.is_finite() or value <= 0 for value in prices
    ):
        raise ExperienceAdvisoryError("candidate evaluation prices are invalid")


def _candidate_advisory(
    context: StrategyEvaluationContext,
    evaluation: StrategyEvaluation[object, object],
    estimate: ExperienceEstimate,
    configuration: ExperienceAdvisoryConfiguration,
) -> ExperienceCandidateAdvisory:
    estimate.validate()
    state = _evidence_state(estimate, configuration)
    bias = _outcome_bias(estimate, state)
    would_block = (
        configuration.shadow_blocking_enabled
        and state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
        and bias is ExperienceOutcomeBias.ADVERSE
    )
    return ExperienceCandidateAdvisory(
        source_id=deepcopy(context.source_id),
        symbol=deepcopy(context.symbol),
        observed_at=context.observed_at,
        strategy_id=evaluation.strategy_id,
        strategy_version=evaluation.strategy_version,
        original_action=evaluation.action,
        target_fingerprint=deepcopy(estimate.target_fingerprint),
        estimator_availability=estimate.availability,
        selected_hierarchy_level=estimate.selected_level,
        selected_hierarchy_key=deepcopy(estimate.selected_key),
        evidence_confidence=estimate.confidence,
        posterior_mean_win_probability=estimate.posterior_mean_win_probability,
        recency_weighted_expectancy_r=estimate.recency_weighted_expectancy_r,
        selected_raw_sample_count=estimate.eligible_selected_raw_sample_count,
        selected_effective_sample_size=estimate.selected_effective_sample_size,
        excluded_future_sample_count=estimate.excluded_future_sample_count,
        prior_source=estimate.prior_source,
        prior_source_level=estimate.prior_source_level,
        prior_source_key=deepcopy(estimate.prior_source_key),
        prior_external_effective_sample_size=estimate.prior_external_effective_sample_size,
        prior_alpha=estimate.prior_alpha,
        prior_beta=estimate.prior_beta,
        posterior_alpha=estimate.posterior_alpha,
        posterior_beta=estimate.posterior_beta,
        estimation_policy_version=estimate.estimation_policy_version,
        recency_policy_version=estimate.recency_policy_version,
        estimate_schema_version=estimate.estimate_schema_version,
        evidence_state=state,
        outcome_bias=bias,
        would_block_candidate=would_block,
        advisory_mode=ExperienceAdvisoryMode.SHADOW_ONLY,
        may_authorize_trade=False,
        may_modify_strategy_action=False,
        may_bypass_risk=False,
        minimum_actionable_confidence=configuration.minimum_actionable_confidence,
        shadow_blocking_enabled=configuration.shadow_blocking_enabled,
        advisory_configuration_schema_version=configuration.configuration_schema_version,
        advisory_policy_version=configuration.advisory_policy_version,
    )


def _no_trade_advisory(
    context: StrategyEvaluationContext,
    evaluation: StrategyEvaluation[object, object],
    fingerprint: ExperienceContextFingerprint,
    estimation_configuration: ExperienceEstimationConfiguration,
    advisory_configuration: ExperienceAdvisoryConfiguration,
) -> ExperienceCandidateAdvisory:
    return ExperienceCandidateAdvisory(
        source_id=deepcopy(context.source_id),
        symbol=deepcopy(context.symbol),
        observed_at=context.observed_at,
        strategy_id=evaluation.strategy_id,
        strategy_version=evaluation.strategy_version,
        original_action=evaluation.action,
        target_fingerprint=deepcopy(fingerprint),
        estimator_availability=None,
        selected_hierarchy_level=None,
        selected_hierarchy_key=None,
        evidence_confidence=None,
        posterior_mean_win_probability=None,
        recency_weighted_expectancy_r=None,
        selected_raw_sample_count=0,
        selected_effective_sample_size=Decimal(0),
        excluded_future_sample_count=0,
        prior_source=None,
        prior_source_level=None,
        prior_source_key=None,
        prior_external_effective_sample_size=None,
        prior_alpha=None,
        prior_beta=None,
        posterior_alpha=None,
        posterior_beta=None,
        estimation_policy_version=estimation_configuration.estimation_policy_version,
        recency_policy_version=estimation_configuration.recency_policy_version,
        estimate_schema_version=None,
        evidence_state=ExperienceAdvisoryEvidenceState.NOT_APPLICABLE_NO_TRADE,
        outcome_bias=ExperienceOutcomeBias.UNAVAILABLE,
        would_block_candidate=False,
        advisory_mode=ExperienceAdvisoryMode.SHADOW_ONLY,
        may_authorize_trade=False,
        may_modify_strategy_action=False,
        may_bypass_risk=False,
        minimum_actionable_confidence=advisory_configuration.minimum_actionable_confidence,
        shadow_blocking_enabled=advisory_configuration.shadow_blocking_enabled,
        advisory_configuration_schema_version=(advisory_configuration.configuration_schema_version),
        advisory_policy_version=advisory_configuration.advisory_policy_version,
    )


def _evidence_state(
    estimate: ExperienceEstimate,
    configuration: ExperienceAdvisoryConfiguration,
) -> ExperienceAdvisoryEvidenceState:
    return _evidence_state_from_facts(
        estimate.availability,
        estimate.confidence,
        configuration.minimum_actionable_confidence,
    )


def _evidence_state_from_facts(
    availability: ExperienceEstimateAvailability,
    confidence: ExperienceEvidenceConfidence,
    minimum_confidence: ExperienceEvidenceConfidence,
) -> ExperienceAdvisoryEvidenceState:
    if availability is ExperienceEstimateAvailability.NO_HISTORY:
        return ExperienceAdvisoryEvidenceState.NO_HISTORY
    if availability is ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE:
        return ExperienceAdvisoryEvidenceState.INSUFFICIENT_EVIDENCE
    if _confidence_meets(confidence, minimum_confidence):
        return ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
    return ExperienceAdvisoryEvidenceState.LOW_EVIDENCE


def _confidence_meets(
    actual: ExperienceEvidenceConfidence,
    required: ExperienceEvidenceConfidence,
) -> bool:
    return actual in _CONFIDENCE_ORDER and _CONFIDENCE_ORDER[actual] >= _CONFIDENCE_ORDER[required]


def _outcome_bias(
    estimate: ExperienceEstimate,
    state: ExperienceAdvisoryEvidenceState,
) -> ExperienceOutcomeBias:
    return _outcome_bias_from_facts(
        estimate.posterior_mean_win_probability,
        estimate.recency_weighted_expectancy_r,
        state,
    )


def _outcome_bias_from_facts(
    probability: Decimal | None,
    expectancy: Decimal | None,
    state: ExperienceAdvisoryEvidenceState,
) -> ExperienceOutcomeBias:
    if state is not ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE:
        return ExperienceOutcomeBias.UNAVAILABLE
    if probability is None or expectancy is None:
        raise ExperienceAdvisoryError("usable Experience evidence lacks required metrics")
    if probability > Decimal("0.5") and expectancy > 0:
        return ExperienceOutcomeBias.FAVORABLE
    if probability < Decimal("0.5") and expectancy < 0:
        return ExperienceOutcomeBias.ADVERSE
    return ExperienceOutcomeBias.MIXED


def _canonical_text(name: str, value: str | None) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ExperienceAdvisoryError(f"{name} must be canonical and non-empty")


def _finite_decimal(value: Decimal, *, field_name: str, non_negative: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ExperienceAdvisoryError(f"{field_name} must be a finite Decimal")
    if non_negative and value < 0:
        raise ExperienceAdvisoryError(f"{field_name} cannot be negative")


def _advisory_integrity_seal(advisory: ExperienceCandidateAdvisory) -> str:
    values = tuple(
        (item.name, getattr(advisory, item.name))
        for item in fields(advisory)
        if item.name != "advisory_integrity_seal"
    )
    payload = dumps(
        (
            "adaptive-trading-bot/experience-candidate-advisory",
            _ADVISORY_INTEGRITY_SEAL_VERSION,
            _canonical_integrity_value(values),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_integrity_value(value: object) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return (
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical_integrity_value(value.value),
        )
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"), value.fold)
    if isinstance(value, timedelta):
        return ("timedelta", value.days, value.seconds, value.microseconds)
    if isinstance(value, Decimal):
        parts = value.as_tuple()
        return ("decimal", parts.sign, tuple(parts.digits), str(parts.exponent))
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, tuple):
        return ("tuple", tuple(_canonical_integrity_value(item) for item in value))
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            f"{type(value).__module__}.{type(value).__qualname__}",
            tuple(
                (item.name, _canonical_integrity_value(getattr(value, item.name)))
                for item in fields(value)
            ),
        )
    raise ExperienceAdvisoryError(
        f"advisory contains unsupported integrity type {type(value).__qualname__}"
    )
