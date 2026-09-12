import ast
import inspect
import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.routing.experience_advisory as advisory_module
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceEstimate,
    ExperienceEstimationConfiguration,
    ExperienceEstimator,
    ExperienceEvidenceConfidence,
    ExperienceProjectionError,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
    experience_context_fingerprint,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.regime import Direction
from adaptive_trading_bot.routing import (
    ExperienceAdvisoryConfiguration,
    ExperienceAdvisoryError,
    ExperienceAdvisoryEvidenceState,
    ExperienceAdvisoryMode,
    ExperienceCandidateAdvisor,
    ExperienceCandidateAdvisory,
    ExperienceOutcomeBias,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_SAMPLE_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "experience" / "test_sample_projection.py")
)
_ESTIMATION_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "experience" / "test_estimation.py")
)
_project_one = cast(
    Callable[..., tuple[DecisionJournalEntry[object, object], object, ExperienceTradeSample]],
    _SAMPLE_HELPERS["_project_one"],
)
_manual_evaluation = cast(
    Callable[..., StrategyEvaluation[object, object]], _SAMPLE_HELPERS["_manual_evaluation"]
)
_sample = cast(Callable[..., ExperienceTradeSample], _ESTIMATION_HELPERS["_sample"])
NOW = cast(datetime, _SAMPLE_HELPERS["NOW"])


def _authority(
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
) -> tuple[StrategyEvaluationContext, StrategyEvaluation[object, object], ExperienceTradeSample]:
    decision, _, base = _project_one()
    evaluation = decision.strategy_evaluation
    if action is not CandidateAction.LONG_CANDIDATE:
        evaluation = _manual_evaluation(
            decision.context,
            strategy_id=evaluation.strategy_id,
            strategy_version=evaluation.strategy_version,
            action=action,
            invalidation=(
                Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)
            ),
        )
    return decision.context, evaluation, base


def _configuration(*, minimum_raw: int = 1) -> ExperienceEstimationConfiguration:
    return ExperienceEstimationConfiguration(
        minimum_raw_sample_count=minimum_raw,
        minimum_effective_sample_size=Decimal("0.9"),
        medium_confidence_effective_sample_size=Decimal("2.9"),
        high_confidence_effective_sample_size=Decimal("4.9"),
        recency_horizon=timedelta(days=100),
        minimum_recency_weight=Decimal("0.25"),
    )


def _snapshot(*samples: ExperienceTradeSample) -> ExperienceTradeSampleSnapshot:
    return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda item: item.sort_key)))


def _history(
    base: ExperienceTradeSample,
    count: int,
    *,
    outcome: str = "PROFIT",
    realized_r: Decimal = Decimal(1),
    future: bool = False,
) -> tuple[ExperienceTradeSample, ...]:
    return tuple(
        _sample(
            base,
            index,
            exit_at=NOW + (timedelta(seconds=index) if future else -timedelta(seconds=index)),
            outcome=outcome,
            net=realized_r,
            realized_r=realized_r,
        )
        for index in range(1, count + 1)
    )


def _advise(
    action: CandidateAction,
    samples: tuple[ExperienceTradeSample, ...],
    *,
    minimum_raw: int = 1,
    advisory_configuration: ExperienceAdvisoryConfiguration | None = None,
) -> ExperienceCandidateAdvisory:
    context, evaluation, _ = _authority(action)
    advisor = ExperienceCandidateAdvisor(
        _configuration(minimum_raw=minimum_raw), advisory_configuration
    )
    return advisor.advise(context, evaluation, _snapshot(*samples), NOW)


def test_canonical_helper_exactly_matches_completed_trade_sample_fingerprint() -> None:
    context, evaluation, sample = _authority()

    fingerprint = experience_context_fingerprint(
        context, evaluation.strategy_id, evaluation.strategy_version
    )

    assert fingerprint == sample.fingerprint
    assert fingerprint is not sample.fingerprint
    assert fingerprint.fingerprint_schema_version == "1.0.0"


def test_fingerprint_helper_is_deterministic_and_revalidates_regime_authority() -> None:
    context, evaluation, _ = _authority()
    first = experience_context_fingerprint(
        context, evaluation.strategy_id, evaluation.strategy_version
    )
    second = experience_context_fingerprint(
        deepcopy(context), evaluation.strategy_id, evaluation.strategy_version
    )
    assert first == second

    object.__setattr__(context.multi_timeframe_regime.regime_15m, "direction", Direction.DOWN)
    with pytest.raises(ExperienceProjectionError, match="regime authority"):
        experience_context_fingerprint(context, evaluation.strategy_id, evaluation.strategy_version)


def test_long_candidate_with_no_history_is_non_authorizing_no_history() -> None:
    result = _advise(CandidateAction.LONG_CANDIDATE, ())

    assert result.original_action is CandidateAction.LONG_CANDIDATE
    assert result.evidence_state is ExperienceAdvisoryEvidenceState.NO_HISTORY
    assert result.outcome_bias is ExperienceOutcomeBias.UNAVAILABLE
    assert result.would_block_candidate is False
    assert result.posterior_mean_win_probability is None
    assert result.advisory_mode is ExperienceAdvisoryMode.SHADOW_ONLY
    assert not result.may_authorize_trade
    assert not result.may_modify_strategy_action
    assert not result.may_bypass_risk


def test_advisor_invokes_the_concrete_estimator_internally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, evaluation, base = _authority()
    calls: list[tuple[ExperienceContextFingerprint, datetime]] = []

    class RecordingEstimator(ExperienceEstimator):
        def estimate(
            self,
            target: ExperienceContextFingerprint,
            snapshot: ExperienceTradeSampleSnapshot,
            as_of: datetime,
        ) -> ExperienceEstimate:
            calls.append((target, as_of))
            return super().estimate(target, snapshot, as_of)

    monkeypatch.setattr(advisory_module, "ExperienceEstimator", RecordingEstimator)
    advisor = ExperienceCandidateAdvisor(_configuration())
    result = advisor.advise(context, evaluation, _snapshot(*_history(base, 1)), NOW)

    assert calls == [(result.target_fingerprint, NOW)]


def test_advisory_copies_exact_estimator_provenance_without_recalculation() -> None:
    context, evaluation, base = _authority()
    configuration = _configuration()
    snapshot = _snapshot(*_history(base, 5))
    target = experience_context_fingerprint(
        context, evaluation.strategy_id, evaluation.strategy_version
    )
    estimate = ExperienceEstimator(configuration).estimate(target, snapshot, NOW)

    result = ExperienceCandidateAdvisor(configuration).advise(context, evaluation, snapshot, NOW)

    assert result.estimator_availability is estimate.availability
    assert result.selected_hierarchy_level is estimate.selected_level
    assert result.selected_hierarchy_key == estimate.selected_key
    assert result.evidence_confidence is estimate.confidence
    assert result.posterior_mean_win_probability == estimate.posterior_mean_win_probability
    assert result.recency_weighted_expectancy_r == estimate.recency_weighted_expectancy_r
    assert result.selected_raw_sample_count == estimate.eligible_selected_raw_sample_count
    assert result.selected_effective_sample_size == estimate.selected_effective_sample_size
    assert result.prior_source is estimate.prior_source
    assert result.prior_source_key == estimate.prior_source_key
    assert result.prior_alpha == estimate.prior_alpha
    assert result.prior_beta == estimate.prior_beta
    assert result.posterior_alpha == estimate.posterior_alpha
    assert result.posterior_beta == estimate.posterior_beta
    assert result.estimation_policy_version == estimate.estimation_policy_version
    assert result.recency_policy_version == estimate.recency_policy_version
    assert result.estimate_schema_version == estimate.estimate_schema_version


def test_short_candidate_with_insufficient_history_preserves_action() -> None:
    _, _, base = _authority(CandidateAction.SHORT_CANDIDATE)
    result = _advise(
        CandidateAction.SHORT_CANDIDATE,
        _history(base, 1, outcome="LOSS", realized_r=Decimal(-1)),
        minimum_raw=2,
    )

    assert result.original_action is CandidateAction.SHORT_CANDIDATE
    assert result.evidence_state is ExperienceAdvisoryEvidenceState.INSUFFICIENT_EVIDENCE
    assert result.outcome_bias is ExperienceOutcomeBias.UNAVAILABLE
    assert result.would_block_candidate is False


def test_no_trade_is_terminal_and_skips_historical_classification() -> None:
    context, _, base = _authority()
    evaluation = _manual_evaluation(context, action=CandidateAction.NO_TRADE)
    result = ExperienceCandidateAdvisor(_configuration()).advise(
        context,
        evaluation,
        _snapshot(*_history(base, 5, outcome="PROFIT", realized_r=Decimal(10))),
        NOW,
    )

    assert result.original_action is CandidateAction.NO_TRADE
    assert result.evidence_state is ExperienceAdvisoryEvidenceState.NOT_APPLICABLE_NO_TRADE
    assert result.outcome_bias is ExperienceOutcomeBias.UNAVAILABLE
    assert result.estimator_availability is None
    assert result.would_block_candidate is False


@pytest.mark.parametrize(
    ("outcome", "realized_r", "expected_bias", "expected_block"),
    (
        ("PROFIT", Decimal(1), ExperienceOutcomeBias.FAVORABLE, False),
        ("BREAKEVEN", Decimal(0), ExperienceOutcomeBias.MIXED, False),
        ("LOSS", Decimal(-1), ExperienceOutcomeBias.ADVERSE, True),
    ),
)
def test_usable_high_evidence_is_transparently_classified_in_shadow_only(
    outcome: str,
    realized_r: Decimal,
    expected_bias: ExperienceOutcomeBias,
    expected_block: bool,
) -> None:
    _, _, base = _authority()
    result = _advise(
        CandidateAction.LONG_CANDIDATE,
        _history(base, 5, outcome=outcome, realized_r=realized_r),
    )

    assert result.evidence_confidence is ExperienceEvidenceConfidence.HIGH
    assert result.evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
    assert result.outcome_bias is expected_bias
    assert result.would_block_candidate is expected_block
    assert result.original_action is CandidateAction.LONG_CANDIDATE
    assert result.may_authorize_trade is False


@pytest.mark.parametrize(
    ("count", "confidence"),
    (
        (1, ExperienceEvidenceConfidence.LOW),
        (3, ExperienceEvidenceConfidence.MEDIUM),
    ),
)
def test_adverse_below_default_high_threshold_cannot_shadow_block(
    count: int, confidence: ExperienceEvidenceConfidence
) -> None:
    _, _, base = _authority()
    result = _advise(
        CandidateAction.LONG_CANDIDATE,
        _history(base, count, outcome="LOSS", realized_r=Decimal(-1)),
    )

    assert result.evidence_confidence is confidence
    assert result.evidence_state is ExperienceAdvisoryEvidenceState.LOW_EVIDENCE
    assert result.outcome_bias is ExperienceOutcomeBias.UNAVAILABLE
    assert result.would_block_candidate is False


def test_configured_medium_threshold_changes_only_counterfactual_audit() -> None:
    _, _, base = _authority()
    result = _advise(
        CandidateAction.SHORT_CANDIDATE,
        _history(base, 3, outcome="LOSS", realized_r=Decimal(-1)),
        advisory_configuration=ExperienceAdvisoryConfiguration(
            minimum_actionable_confidence=ExperienceEvidenceConfidence.MEDIUM
        ),
    )

    assert result.original_action is CandidateAction.SHORT_CANDIDATE
    assert result.outcome_bias is ExperienceOutcomeBias.ADVERSE
    assert result.would_block_candidate is True
    assert result.may_modify_strategy_action is False


def test_favorable_advisory_has_no_force_or_positive_authorization_path() -> None:
    _, _, base = _authority()
    result = _advise(CandidateAction.LONG_CANDIDATE, _history(base, 5))

    assert result.outcome_bias is ExperienceOutcomeBias.FAVORABLE
    names = {item.name for item in fields(result)}
    assert "would_force_trade" not in names
    assert not any("authorize" in name and getattr(result, name) is True for name in names)


@pytest.mark.parametrize("action", tuple(CandidateAction))
def test_original_strategy_action_is_preserved_exactly(action: CandidateAction) -> None:
    _, _, base = _authority()
    result = _advise(action, _history(base, 5, outcome="LOSS", realized_r=Decimal(-1)))

    assert result.original_action is action


def test_advisory_does_not_mutate_strategy_price_or_stop_authority() -> None:
    context, evaluation, base = _authority(CandidateAction.SHORT_CANDIDATE)
    accepted = deepcopy(evaluation)

    result = ExperienceCandidateAdvisor(_configuration()).advise(
        context,
        evaluation,
        _snapshot(*_history(base, 5, outcome="LOSS", realized_r=Decimal(-1))),
        NOW,
    )

    assert evaluation == accepted
    assert evaluation.reference_price == accepted.reference_price
    assert evaluation.invalidation_reference_price == accepted.invalidation_reference_price
    assert result.original_action is evaluation.action


@pytest.mark.parametrize("mismatch", ("source", "symbol", "time"))
def test_context_and_evaluation_identity_mismatch_fails_closed(mismatch: str) -> None:
    context, evaluation, _ = _authority()
    if mismatch == "source":
        evaluation = replace(evaluation, source_id=SourceId("other"))
    elif mismatch == "symbol":
        other = replace(context.symbol, base="ETH")
        evaluation = replace(evaluation, symbol=other)
    else:
        evaluation = replace(evaluation, observed_at=NOW + timedelta(microseconds=1))

    with pytest.raises(ExperienceAdvisoryError, match="differ"):
        ExperienceCandidateAdvisor().advise(context, evaluation, _snapshot(), NOW)


def test_as_of_must_equal_observed_at_and_be_strict_utc() -> None:
    context, evaluation, _ = _authority()
    advisor = ExperienceCandidateAdvisor()

    with pytest.raises(ExperienceAdvisoryError, match="as_of"):
        advisor.advise(context, evaluation, _snapshot(), NOW + timedelta(microseconds=1))
    with pytest.raises(ValueError, match="UTC"):
        advisor.advise(context, evaluation, _snapshot(), datetime(2026, 9, 10))
    with pytest.raises(ValueError, match="UTC"):
        advisor.advise(
            context,
            evaluation,
            _snapshot(),
            NOW.astimezone(timezone(timedelta(hours=1))),
        )


def test_future_completed_samples_cannot_influence_advisory() -> None:
    _, _, base = _authority()
    result = _advise(CandidateAction.LONG_CANDIDATE, _history(base, 5, future=True))

    assert result.evidence_state is ExperienceAdvisoryEvidenceState.NO_HISTORY
    assert result.excluded_future_sample_count == 5
    assert result.outcome_bias is ExperienceOutcomeBias.UNAVAILABLE


@pytest.mark.parametrize("action", (CandidateAction.LONG_CANDIDATE, CandidateAction.NO_TRADE))
def test_tampered_sample_seal_fails_before_any_advisory(action: CandidateAction) -> None:
    context, evaluation, base = _authority(action)
    sample = _history(base, 1)[0]
    snapshot = _snapshot(sample)
    object.__setattr__(sample, "realized_r_net", Decimal(999))

    with pytest.raises(ExperienceProjectionError, match="seal"):
        ExperienceCandidateAdvisor().advise(context, evaluation, snapshot, NOW)


def test_input_order_and_ambient_decimal_context_do_not_change_result() -> None:
    context, evaluation, base = _authority()
    samples = _history(base, 5, outcome="LOSS", realized_r=Decimal("-0.7"))
    advisor = ExperienceCandidateAdvisor(_configuration())
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        first = advisor.advise(context, evaluation, _snapshot(*samples), NOW)
        setcontext(Context(prec=60))
        second = advisor.advise(context, evaluation, _snapshot(*reversed(samples)), NOW)
    finally:
        setcontext(original)

    assert first == second


def test_output_is_immutable_detached_and_tamper_evident() -> None:
    _, _, base = _authority()
    sample = _history(base, 5)[0]
    result = _advise(CandidateAction.LONG_CANDIDATE, (sample, *_history(base, 4)[1:]))
    copied = deepcopy(result)

    with pytest.raises(FrozenInstanceError):
        result.would_block_candidate = True  # type: ignore[misc]
    object.__setattr__(sample.fingerprint, "strategy_version", "mutated")
    assert copied == result
    copied.validate()
    object.__setattr__(copied, "outcome_bias", ExperienceOutcomeBias.ADVERSE)
    with pytest.raises(ExperienceAdvisoryError, match="seal"):
        copied.validate()


def test_shadow_advisor_has_no_forbidden_runtime_dependencies_or_calculations() -> None:
    path = Path(advisory_module.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("dashboard", "exchange", "live", "orders", "paper", "risk")

    assert not any(token in module for module in imported for token in forbidden)
    assert "ExperienceEstimator(deepcopy(estimation_configuration))" in source
    assert tuple(inspect.signature(ExperienceCandidateAdvisor).parameters) == (
        "estimation_configuration",
        "advisory_configuration",
    )
    assert "estimate" not in inspect.signature(ExperienceCandidateAdvisor).parameters
    assert not any(
        token in source
        for token in (
            "approved_quantity",
            "position_size",
            "gross_realized_pnl",
            "net_realized_pnl",
            "RiskDecision",
            "OperationalSafetyDecision",
        )
    )
    public = {
        name
        for name, value in inspect.getmembers(ExperienceCandidateAdvisor)
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert public == {"advise"}
