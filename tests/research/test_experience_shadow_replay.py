import ast
import inspect
import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.research.experience_shadow_replay as replay_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.experience import (
    ExperienceEstimationConfiguration,
    ExperienceProjectionError,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
)
from adaptive_trading_bot.journal import (
    DecisionJournalEntry,
    DecisionJournalSnapshot,
    DecisionJournalValidationError,
)
from adaptive_trading_bot.research import (
    ExperienceObservedOutcomeGroup,
    ExperienceShadowReplayConfiguration,
    ExperienceShadowReplayError,
    ExperienceShadowReplayEvaluator,
    ExperienceShadowReplayReport,
    ExperienceShadowResearchMode,
    ExperienceShadowStratificationDimension,
)
from adaptive_trading_bot.routing import (
    ExperienceAdvisoryConfiguration,
    ExperienceAdvisoryEvidenceState,
    ExperienceCandidateAdvisor,
)
from adaptive_trading_bot.strategies import CandidateAction, StrategyEvaluationContext

_SAMPLE_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "experience" / "test_sample_projection.py")
)
_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
_project_one = cast(
    Callable[..., tuple[DecisionJournalEntry[object, object], object, ExperienceTradeSample]],
    _SAMPLE_HELPERS["_project_one"],
)
_authoritative_context = cast(
    Callable[[StrategyEvaluationContext], StrategyEvaluationContext],
    _SAMPLE_HELPERS["_authoritative_context"],
)
_context = cast(Callable[..., StrategyEvaluationContext], _JOURNAL_HELPERS["_context"])
_entry = cast(Callable[..., DecisionJournalEntry[object, object]], _JOURNAL_HELPERS["_entry"])

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


def _candidate(
    observed_at: datetime,
    *,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    exit_price: Decimal | None = None,
) -> tuple[DecisionJournalEntry[object, object], ExperienceTradeSample]:
    context = _authoritative_context(_context(observed_at=observed_at))
    decision, _, sample = _project_one(
        context=context,
        action=action,
        exit_price=exit_price,
    )
    return decision, sample


def _no_trade(observed_at: datetime) -> DecisionJournalEntry[object, object]:
    return _entry(
        "no_action",
        context=_authoritative_context(_context(observed_at=observed_at)),
        strategy_id="trend-pullback",
        strategy_version="1.2.3",
    )


def _decisions(
    *entries: DecisionJournalEntry[object, object],
) -> DecisionJournalSnapshot[object, object]:
    return DecisionJournalSnapshot(
        entries,
        max((entry.identity.observed_at for entry in entries), default=None),
    )


def _samples(*samples: ExperienceTradeSample) -> ExperienceTradeSampleSnapshot:
    return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda item: item.sort_key)))


def _configuration(*, minimum_raw: int = 1) -> ExperienceShadowReplayConfiguration:
    return ExperienceShadowReplayConfiguration(
        estimation=ExperienceEstimationConfiguration(
            minimum_raw_sample_count=minimum_raw,
            minimum_effective_sample_size=Decimal("0.1"),
            medium_confidence_effective_sample_size=Decimal("0.1"),
            high_confidence_effective_sample_size=Decimal("0.1"),
            recency_horizon=timedelta(days=365),
            minimum_recency_weight=Decimal("0.25"),
        ),
        time_slice_size=timedelta(days=10),
    )


def _evaluate(
    decisions: DecisionJournalSnapshot[object, object],
    samples: ExperienceTradeSampleSnapshot,
    configuration: ExperienceShadowReplayConfiguration | None = None,
) -> ExperienceShadowReplayReport:
    return ExperienceShadowReplayEvaluator().evaluate(
        decisions,
        samples,
        configuration or _configuration(),
        START,
        END,
    )


def test_replay_is_chronological_input_order_invariant_and_keeps_no_trade() -> None:
    first, first_sample = _candidate(START + timedelta(days=1))
    second, _ = _candidate(START + timedelta(days=2), action=CandidateAction.SHORT_CANDIDATE)
    no_trade = _no_trade(START + timedelta(days=3))

    report = _evaluate(_decisions(no_trade, second, first), _samples(first_sample))
    again = _evaluate(_decisions(first, no_trade, second), _samples(first_sample))

    assert report == again
    assert tuple(record.identity for record in report.records) == (
        first.identity,
        second.identity,
        no_trade.identity,
    )
    assert report.population.total_decision_count == 3
    assert report.population.no_trade_decision_count == 1
    assert report.population.long_candidate_count == 1
    assert report.population.short_candidate_count == 1
    assert (
        report.records[-1].evidence_state is ExperienceAdvisoryEvidenceState.NOT_APPLICABLE_NO_TRADE
    )
    assert not report.records[-1].would_block_candidate


def test_each_decision_and_sample_seal_is_revalidated() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    broken_decision = deepcopy(decision)
    object.__setattr__(broken_decision.context.features_15m, "rsi_14", Decimal(99))
    with pytest.raises(DecisionJournalValidationError, match="integrity seal"):
        _evaluate(_decisions(broken_decision), _samples())

    broken_sample = deepcopy(sample)
    object.__setattr__(broken_sample, "realized_r_net", Decimal(999))
    snapshot = _samples(sample)
    object.__setattr__(snapshot, "samples", (broken_sample,))
    with pytest.raises(ExperienceProjectionError, match="integrity seal"):
        _evaluate(_decisions(decision), snapshot)


def test_advisories_are_causal_and_use_concrete_advisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    earlier, earlier_outcome = _candidate(START + timedelta(days=1))
    later, later_outcome = _candidate(START + timedelta(days=2))
    calls: list[datetime] = []

    class RecordingAdvisor(ExperienceCandidateAdvisor):
        def advise(self, context, evaluation, samples, as_of):  # type: ignore[no-untyped-def]
            calls.append(as_of)
            return super().advise(context, evaluation, samples, as_of)

    monkeypatch.setattr(replay_module, "ExperienceCandidateAdvisor", RecordingAdvisor)
    report = _evaluate(
        _decisions(later, earlier),
        _samples(earlier_outcome, later_outcome),
    )

    assert calls == [earlier.identity.observed_at, later.identity.observed_at]
    assert report.records[0].evidence_state is ExperienceAdvisoryEvidenceState.NO_HISTORY
    assert report.records[1].selected_raw_sample_count == 1
    assert report.records[1].evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
    expected = ExperienceCandidateAdvisor(_configuration().estimation).advise(
        later.context,
        later.strategy_evaluation,
        _samples(earlier_outcome, later_outcome),
        later.identity.observed_at,
    )
    assert report.records[1].would_block_candidate is expected.would_block_candidate
    assert report.records[1].outcome_bias is expected.outcome_bias
    assert report.records[1].posterior_mean_win_probability == (
        expected.posterior_mean_win_probability
    )
    assert report.records[1].recency_weighted_expectancy_r == (
        expected.recency_weighted_expectancy_r
    )


def test_completed_before_t_is_prior_evidence_even_when_decision_is_before_range() -> None:
    historical, historical_sample = _candidate(START - timedelta(days=2))
    current, _ = _candidate(START + timedelta(days=2))
    assert historical_sample.exit_fill_at < current.identity.observed_at

    report = _evaluate(_decisions(current), _samples(historical_sample))

    assert report.records[0].selected_raw_sample_count == 1
    assert report.records[0].evidence_state is ExperienceAdvisoryEvidenceState.USABLE_EVIDENCE
    assert historical.identity not in tuple(record.identity for record in report.records)


def test_population_counts_no_history_insufficient_low_and_usable_exactly() -> None:
    historical, historical_sample = _candidate(START - timedelta(days=2))
    current, _ = _candidate(START + timedelta(days=2))

    no_history = _evaluate(_decisions(current), _samples())
    insufficient = _evaluate(
        _decisions(current), _samples(historical_sample), _configuration(minimum_raw=2)
    )
    low_configuration = ExperienceShadowReplayConfiguration(
        estimation=ExperienceEstimationConfiguration(
            minimum_raw_sample_count=1,
            minimum_effective_sample_size=Decimal("0.1"),
            medium_confidence_effective_sample_size=Decimal(2),
            high_confidence_effective_sample_size=Decimal(3),
            recency_horizon=timedelta(days=365),
            minimum_recency_weight=Decimal("0.25"),
        )
    )
    low = _evaluate(_decisions(current), _samples(historical_sample), low_configuration)
    usable = _evaluate(_decisions(current), _samples(historical_sample))

    assert no_history.population.no_history_count == 1
    assert insufficient.population.insufficient_evidence_count == 1
    assert low.population.low_evidence_count == 1
    assert usable.population.usable_evidence_count == 1
    assert (
        sum(
            (
                usable.population.favorable_usable_evidence_count,
                usable.population.mixed_usable_evidence_count,
                usable.population.adverse_usable_evidence_count,
            )
        )
        == 1
    )
    del historical


def test_outcome_and_non_outcome_populations_are_separate_and_copy_authority() -> None:
    retained, retained_sample = _candidate(START + timedelta(days=1), exit_price=Decimal(110))
    blocked, blocked_sample = _candidate(START + timedelta(days=2), exit_price=Decimal(70))
    missing, _ = _candidate(START + timedelta(days=4))
    prior, prior_loss = _candidate(START - timedelta(days=2), exit_price=Decimal(70))
    prior_exit = START + timedelta(days=1, hours=12)
    prior_loss = replace(
        prior_loss,
        entry_fill_at=prior_exit - timedelta(microseconds=1),
        exit_fill_at=prior_exit,
        duration=timedelta(microseconds=1),
        accounting_entry_fill_id="delayed-prior-entry",
        accounting_exit_fill_id="delayed-prior-exit",
    )
    assert prior_loss.net_realized_pnl < 0

    report = _evaluate(
        _decisions(blocked, retained, missing),
        _samples(prior_loss, blocked_sample, retained_sample),
    )

    assert report.outcome_evaluable_count == 2
    assert report.non_outcome_evaluable_candidate_count == 1
    assert (
        report.shadow_blocked_non_outcome_evaluable_count
        + report.retained_non_outcome_evaluable_count
        == 1
    )
    retained_record = report.records[0]
    blocked_record = report.records[1]
    assert not retained_record.would_block_candidate
    assert blocked_record.would_block_candidate
    assert blocked_record.outcome_classification == blocked_sample.outcome_classification
    assert blocked_record.net_realized_pnl == blocked_sample.net_realized_pnl
    assert blocked_record.realized_r_net == blocked_sample.realized_r_net
    assert blocked_record.total_trade_fees_quote == blocked_sample.total_trade_fees_quote
    assert blocked_record.exit_fill_at == blocked_sample.exit_fill_at
    blocked_summary = report.observed_outcome_summaries[0]
    retained_summary = report.observed_outcome_summaries[1]
    assert blocked_summary.group is ExperienceObservedOutcomeGroup.BLOCKED_SHADOW_GROUP
    assert retained_summary.group is ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP
    assert blocked_summary.completed_trade_count == 1
    assert retained_summary.completed_trade_count == 1
    assert retained_record.net_realized_pnl == retained_sample.net_realized_pnl
    assert retained_record.realized_r_net == retained_sample.realized_r_net
    del prior


def test_exact_join_failures_are_not_silently_dropped() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    with pytest.raises(ExperienceShadowReplayError, match="orphaned"):
        _evaluate(_decisions(), _samples(sample))

    other, other_sample = _candidate(
        START + timedelta(days=2), action=CandidateAction.SHORT_CANDIDATE
    )
    mismatched = replace(other_sample, candidate_action=CandidateAction.LONG_CANDIDATE)
    with pytest.raises(ExperienceShadowReplayError, match="provenance"):
        _evaluate(_decisions(other), _samples(mismatched))

    duplicate_snapshot = _samples(sample)
    object.__setattr__(duplicate_snapshot, "samples", (sample, sample))
    with pytest.raises(ExperienceProjectionError, match="duplicate"):
        _evaluate(_decisions(decision), duplicate_snapshot)

    with pytest.raises(ExperienceShadowReplayError, match="duplicate DecisionIdentity"):
        _evaluate(_decisions(decision, decision), _samples(sample))


def test_mixed_quotes_are_separated_and_zero_denominators_are_explicit() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    second, second_sample = _candidate(START + timedelta(days=2), exit_price=Decimal(110))
    configuration = ExperienceShadowReplayConfiguration(
        estimation=_configuration().estimation,
        advisory=ExperienceAdvisoryConfiguration(shadow_blocking_enabled=False),
    )
    report = _evaluate(_decisions(decision, second), _samples(sample, second_sample), configuration)

    assert report.population.shadow_block_rate.denominator == 2
    empty_group = report.observed_outcome_summaries[0]
    assert empty_group.win_rate.denominator == 0
    assert empty_group.win_rate.value is None
    populated = report.observed_outcome_summaries[1]
    assert tuple(item.quote_currency for item in populated.currency_summaries) == ("USDT",)
    usdc_symbol = TradingSymbol("BTC", "USDC")
    usdc_record = replace(
        report.records[1],
        identity=replace(report.records[1].identity, symbol=usdc_symbol),
        target_fingerprint=replace(report.records[1].target_fingerprint, symbol=usdc_symbol),
        quote_currency="USDC",
    )
    mixed = replay_module._outcome_summary(
        ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP,
        (report.records[0], usdc_record),
    )
    assert tuple(item.quote_currency for item in mixed.currency_summaries) == (
        "USDC",
        "USDT",
    )
    assert sum(item.completed_trade_count for item in mixed.currency_summaries) == 2

    usdc_symbol = TradingSymbol("BTC", "USDC")
    usdc_record = replace(
        report.records[1],
        identity=replace(report.records[1].identity, symbol=usdc_symbol),
        target_fingerprint=replace(report.records[1].target_fingerprint, symbol=usdc_symbol),
        quote_currency="USDC",
    )
    separated = replay_module._outcome_summary(
        ExperienceObservedOutcomeGroup.RETAINED_SHADOW_GROUP,
        (report.records[0], usdc_record),
    )
    assert tuple(item.quote_currency for item in separated.currency_summaries) == (
        "USDC",
        "USDT",
    )
    assert sum(item.completed_trade_count for item in separated.currency_summaries) == 2


def test_strata_and_fixed_slices_are_canonical_with_raw_denominators() -> None:
    first, sample = _candidate(START + timedelta(days=1))
    second, _ = _candidate(START + timedelta(days=21))
    report = _evaluate(_decisions(second, first), _samples(sample))

    assert tuple(item.start for item in report.stability_summaries) == (
        START,
        START + timedelta(days=10),
        START + timedelta(days=20),
    )
    assert tuple(item.candidate_count for item in report.stability_summaries) == (1, 0, 1)
    dimensions = {item.dimension for item in report.stratified_summaries}
    assert dimensions == set(ExperienceShadowStratificationDimension)
    assert all(
        item.shadow_block_rate.denominator == item.candidate_count
        for item in report.stratified_summaries
    )


def test_future_sample_after_report_does_not_change_report() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    future_decision, future_sample = _candidate(END + timedelta(days=1))

    baseline = _evaluate(_decisions(decision), _samples(sample))
    with_future = _evaluate(_decisions(future_decision, decision), _samples(sample, future_sample))

    assert baseline == with_future


def test_decimal_context_cannot_change_results() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        first = _evaluate(_decisions(decision), _samples(sample))
        setcontext(Context(prec=50))
        second = _evaluate(_decisions(decision), _samples(sample))
    finally:
        setcontext(original)
    assert first == second


def test_report_and_records_are_immutable_sealed_and_truthfully_limited() -> None:
    decision, sample = _candidate(START + timedelta(days=1))
    report = _evaluate(_decisions(decision), _samples(sample))

    assert report.research_mode is ExperienceShadowResearchMode.SHADOW_REPLAY_ONLY
    assert "Retrospective attribution only" in report.limitation
    assert "not a counterfactual portfolio" in report.limitation
    with pytest.raises(FrozenInstanceError):
        report.start = END  # type: ignore[misc]
    broken = deepcopy(report)
    object.__setattr__(broken.records[0], "net_realized_pnl", Decimal(123456))
    with pytest.raises(ExperienceShadowReplayError, match="integrity seal"):
        broken.validate()


def test_range_and_research_dependency_guards() -> None:
    with pytest.raises(ValueError, match="UTC"):
        ExperienceShadowReplayEvaluator().evaluate(
            _decisions(), _samples(), _configuration(), START.replace(tzinfo=None), END
        )
    with pytest.raises(ExperienceShadowReplayError, match="start < end"):
        ExperienceShadowReplayEvaluator().evaluate(
            _decisions(), _samples(), _configuration(), END, START
        )

    tree = ast.parse(inspect.getsource(replay_module))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        forbidden in name
        for name in imported
        for forbidden in ("exchange", "orders", "paper", "live", "risk", "safety")
    )
    source = inspect.getsource(replay_module)
    for forbidden_name in (
        "counterfactual_equity",
        "counterfactual_portfolio_pnl",
        "counterfactual_drawdown",
        "create_order",
        "create_fill",
        "create_position",
    ):
        assert forbidden_name not in source
