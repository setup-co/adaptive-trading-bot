import ast
import inspect
import runpy
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.experience.core as experience_module
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.experience import (
    ExperienceFallbackLevel,
    ExperienceProjectionError,
    ExperienceSampleProjector,
    ExperienceTradeSample,
    TrendStrengthBucket,
    trend_strength_bucket,
)
from adaptive_trading_bot.journal import (
    DecisionJournalEntry,
    DecisionJournalSnapshot,
    DecisionJournalValidationError,
)
from adaptive_trading_bot.journal.trade_outcome import (
    CompletedTradeJournalEntry,
    CompletedTradeJournalSnapshot,
)
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer
from adaptive_trading_bot.paper import PaperExecutionObservation, PaperPortfolioCoordinator
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    DirectionEvidence,
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeComposer,
    RegimeLabel,
    TechnicalRegimeBatch,
    VolatilityState,
    VolumeState,
    validate_multi_timeframe_regime_snapshot,
    validate_technical_regime_snapshot,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction, StrategyEvaluationContext

_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
NOW: datetime = _JOURNAL_HELPERS["NOW"]
BTC: TradingSymbol = _JOURNAL_HELPERS["BTC"]
ETH: TradingSymbol = _JOURNAL_HELPERS["ETH"]
_context = _JOURNAL_HELPERS["_context"]
_manual_evaluation = _JOURNAL_HELPERS["_manual_evaluation"]
_health = _JOURNAL_HELPERS["_health"]
PROJECTOR = ExperienceSampleProjector()


def _authoritative_context(context: StrategyEvaluationContext) -> StrategyEvaluationContext:
    feature = replace(context.features_15m, momentum_4=Decimal(0))
    anchor = replace(
        context.multi_timeframe_regime.regime_15m,
        direction_evidence=(
            DirectionEvidence(DirectionComponent.EMA_ALIGNMENT, 20, "ema_20 above ema_50"),
            DirectionEvidence(DirectionComponent.MOMENTUM_16, 20, "positive"),
            DirectionEvidence(DirectionComponent.MOMENTUM_4, 0, "zero"),
            DirectionEvidence(DirectionComponent.CONFIRMED_HIGH, 0, "unavailable"),
            DirectionEvidence(DirectionComponent.CONFIRMED_LOW, 0, "unavailable"),
        ),
    )
    regime_1h = context.multi_timeframe_regime.regime_1h
    regime_4h = context.multi_timeframe_regime.regime_4h
    assert regime_1h is not None
    assert regime_4h is not None
    multi = (
        MultiTimeframeRegimeComposer()
        .process(
            TechnicalRegimeBatch(
                context.observed_at,
                (
                    anchor,
                    regime_1h,
                    regime_4h,
                ),
            )
        )
        .snapshots[0]
    )
    return replace(context, features_15m=feature, multi_timeframe_regime=multi)


def _candidate(
    context: StrategyEvaluationContext,
    *,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
) -> PortfolioAdmissionCandidate[object, object]:
    evaluation = _manual_evaluation(
        context,
        strategy_id="trend-pullback",
        strategy_version="1.2.3",
        action=action,
        invalidation=Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75),
    )
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            context.observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    decision = DecisionJournalEntry(
        context,
        evaluation,
        risk,
        OperationalSafetyGate().evaluate(risk, _health(risk)),
    )
    intent = EntryOrderIntentAuthorizer().authorize(decision).intent
    assert intent is not None
    return PortfolioAdmissionCandidate(decision, intent)


def _complete(
    *,
    context: StrategyEvaluationContext | None = None,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    exit_price: Decimal | None = None,
) -> tuple[DecisionJournalEntry[object, object], CompletedTradeJournalEntry]:
    current = _authoritative_context(_context()) if context is None else context
    item = _candidate(current, action=action)
    paper = PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(Decimal(1), Decimal(1), Decimal(1))
    )
    paper.start_batch((item,))
    paper.observe_batch(
        (
            PaperExecutionObservation(
                current.source_id, current.symbol, current.observed_at, Decimal(100)
            ),
        )
    )
    trigger_at = current.observed_at + timedelta(minutes=1)
    stop = Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)
    paper.evaluate_protective_stops(
        (PaperExecutionObservation(current.source_id, current.symbol, trigger_at, stop),)
    )
    final_price = (
        exit_price
        if exit_price is not None
        else Decimal(140)
        if action is CandidateAction.SHORT_CANDIDATE
        else Decimal(70)
    )
    result = paper.observe_protective_exits(
        (
            PaperExecutionObservation(
                current.source_id,
                current.symbol,
                trigger_at + timedelta(microseconds=1),
                final_price,
            ),
        )
    )
    return item.journal_entry, result.completed_trade_journal_entries[0]


def _snapshots(
    decisions: tuple[DecisionJournalEntry[object, object], ...],
    trades: tuple[CompletedTradeJournalEntry, ...],
) -> tuple[DecisionJournalSnapshot[object, object], CompletedTradeJournalSnapshot]:
    return (
        DecisionJournalSnapshot(
            decisions,
            max((entry.identity.observed_at for entry in decisions), default=None),
        ),
        CompletedTradeJournalSnapshot(
            trades,
            max((entry.exit_fill_at for entry in trades), default=None),
        ),
    )


def _project_one(
    *,
    context: StrategyEvaluationContext | None = None,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    exit_price: Decimal | None = None,
) -> tuple[DecisionJournalEntry[object, object], CompletedTradeJournalEntry, ExperienceTradeSample]:
    decision, trade = _complete(context=context, action=action, exit_price=exit_price)
    sample = PROJECTOR.project(*_snapshots((decision,), (trade,))).samples[0]
    return decision, trade, sample


def _with_regime(
    context: StrategyEvaluationContext,
    *,
    regime_label: RegimeLabel | None = None,
    volatility_state: VolatilityState | None = None,
    volume_state: VolumeState | None = None,
    higher_timeframe_bias: HigherTimeframeBias | None = None,
    alignment: MultiTimeframeAlignment | None = None,
) -> StrategyEvaluationContext:
    context = _authoritative_context(context)
    multi = context.multi_timeframe_regime
    feature = context.features_15m
    anchor = multi.regime_15m
    if regime_label is RegimeLabel.STRONG_UPTREND:
        feature = replace(feature, momentum_4=Decimal("0.01"))
        anchor_evidence = list(anchor.direction_evidence)
        anchor_evidence[2] = DirectionEvidence(DirectionComponent.MOMENTUM_4, 20, "positive")
        anchor = replace(
            anchor,
            direction_score=60,
            trend_strength=60,
            direction_evidence=tuple(anchor_evidence),
            regime_label=regime_label,
        )
    if volatility_state is VolatilityState.HIGH:
        anchor = replace(
            anchor,
            volatility_percentile=Decimal(80),
            volatility_state=volatility_state,
        )
    if volume_state is VolumeState.HIGH:
        feature = replace(feature, volume_zscore_20=Decimal(1))
        anchor = replace(anchor, volume_state=volume_state)
    regime_4h = multi.regime_4h
    if higher_timeframe_bias is HigherTimeframeBias.MIXED:
        assert regime_4h is not None
        htf_evidence = tuple(
            replace(item, contribution=-20)
            if item.component in {DirectionComponent.EMA_ALIGNMENT, DirectionComponent.MOMENTUM_16}
            else item
            for item in regime_4h.direction_evidence
        )
        regime_4h = replace(
            regime_4h,
            direction=Direction.DOWN,
            direction_score=-40,
            trend_strength=40,
            direction_evidence=htf_evidence,
            regime_label=RegimeLabel.DOWNTREND,
        )
    assert multi.regime_1h is not None
    assert regime_4h is not None
    rebuilt = (
        MultiTimeframeRegimeComposer()
        .process(
            TechnicalRegimeBatch(
                context.observed_at,
                (anchor, multi.regime_1h, regime_4h),
            )
        )
        .snapshots[0]
    )
    assert higher_timeframe_bias is None or rebuilt.higher_timeframe_bias is higher_timeframe_bias
    assert alignment is None or rebuilt.alignment is alignment
    return replace(
        context,
        features_15m=feature,
        multi_timeframe_regime=rebuilt,
    )


@pytest.mark.parametrize(
    "action", (CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE)
)
def test_long_and_short_samples_copy_exact_authoritative_outcome(action: CandidateAction) -> None:
    decision, trade, sample = _project_one(action=action)

    assert sample.decision_identity == decision.identity
    assert sample.source_id == decision.identity.source_id
    assert sample.entry_decision_at == decision.identity.observed_at
    assert sample.entry_fill_at == trade.entry_fill_at
    assert sample.exit_fill_at == trade.exit_fill_at
    assert sample.candidate_action is action
    assert sample.entry_side is trade.entry_side
    assert sample.exit_side is trade.exit_side
    assert sample.quantity == trade.quantity
    assert sample.approved_risk_quote == trade.approved_risk_quote
    assert sample.gross_realized_pnl == trade.gross_realized_pnl
    assert sample.total_trade_fees_quote == trade.total_trade_fees_quote
    assert sample.net_realized_pnl == trade.net_realized_pnl
    assert sample.realized_r_net == trade.realized_r_net
    assert sample.outcome_classification is trade.outcome_classification
    assert sample.exit_reason is trade.exit_reason
    assert sample.duration == trade.duration
    assert sample.risk_policy_version == trade.risk_policy_version
    assert sample.safety_policy_version == trade.safety_policy_version
    assert sample.accounting_entry_fill_id == trade.accounting_entry_fill_id
    assert sample.accounting_exit_fill_id == trade.accounting_exit_fill_id


def test_fingerprint_comes_only_from_originating_pretrade_context() -> None:
    decision, _, sample = _project_one()
    anchor = decision.context.multi_timeframe_regime.regime_15m
    fingerprint = sample.fingerprint

    assert fingerprint.symbol == decision.context.symbol
    assert fingerprint.strategy_id == decision.strategy_evaluation.strategy_id
    assert fingerprint.strategy_version == decision.strategy_evaluation.strategy_version
    assert fingerprint.entry_timeframe == decision.context.candle_15m.timeframe
    assert fingerprint.regime_label is anchor.regime_label
    assert fingerprint.direction is anchor.direction
    assert fingerprint.trend_strength_bucket is TrendStrengthBucket.MODERATE
    assert fingerprint.volatility_state is anchor.volatility_state
    assert fingerprint.volume_state is anchor.volume_state
    assert (
        fingerprint.higher_timeframe_bias
        is decision.context.multi_timeframe_regime.higher_timeframe_bias
    )
    assert (
        fingerprint.multi_timeframe_alignment is decision.context.multi_timeframe_regime.alignment
    )


@pytest.mark.parametrize(
    ("strength", "expected"),
    (
        (0, TrendStrengthBucket.WEAK),
        (20, TrendStrengthBucket.WEAK),
        (40, TrendStrengthBucket.MODERATE),
        (60, TrendStrengthBucket.STRONG),
        (80, TrendStrengthBucket.STRONG),
        (100, TrendStrengthBucket.STRONG),
    ),
)
def test_trend_strength_bucket_accepts_only_exact_v1_values(
    strength: int, expected: TrendStrengthBucket
) -> None:
    assert trend_strength_bucket(strength) is expected


@pytest.mark.parametrize("strength", (-20, 1, 39, 41, 101, True, "40"))
def test_trend_strength_bucket_rejects_noncanonical_values(strength: object) -> None:
    with pytest.raises(ExperienceProjectionError, match="trend strength"):
        trend_strength_bucket(strength)  # type: ignore[arg-type]


def test_hierarchy_has_stable_levels_and_exact_dimensions() -> None:
    _, _, sample = _project_one()
    keys = sample.hierarchical_keys
    assert tuple(key.level for key in keys) == tuple(ExperienceFallbackLevel)

    optional_names = {
        "symbol",
        "entry_timeframe",
        "regime_label",
        "direction",
        "trend_strength_bucket",
        "volatility_state",
        "volume_state",
        "higher_timeframe_bias",
        "multi_timeframe_alignment",
    }
    present = [{name for name in optional_names if getattr(key, name) is not None} for key in keys]
    assert present == [
        optional_names,
        {"symbol", "regime_label", "higher_timeframe_bias", "multi_timeframe_alignment"},
        {"symbol", "regime_label"},
        {"symbol"},
        set(),
    ]
    assert all(key.key_schema_version == "1.0.0" for key in keys)


def test_outcome_changes_never_change_same_context_fingerprint() -> None:
    _, losing_trade, losing = _project_one(exit_price=Decimal(70))
    _, other_trade, other = _project_one(exit_price=Decimal(74))

    assert losing_trade.net_realized_pnl != other_trade.net_realized_pnl
    assert losing.fingerprint == other.fingerprint
    assert losing.hierarchical_keys == other.hierarchical_keys


@pytest.mark.parametrize(
    ("changes", "changed_levels"),
    (
        ({"regime_label": RegimeLabel.STRONG_UPTREND}, {0, 1, 2}),
        ({"volatility_state": VolatilityState.HIGH}, {0}),
        ({"volume_state": VolumeState.HIGH}, {0}),
        (
            {
                "higher_timeframe_bias": HigherTimeframeBias.MIXED,
                "alignment": MultiTimeframeAlignment.MIXED,
            },
            {0, 1},
        ),
    ),
)
def test_context_dimensions_change_only_expected_key_levels(
    changes: dict[str, object], changed_levels: set[int]
) -> None:
    base_context = _authoritative_context(cast(StrategyEvaluationContext, _context()))
    _, _, base = _project_one(context=base_context)
    changed_context = _with_regime(base_context, **changes)  # type: ignore[arg-type]
    _, _, changed = _project_one(context=changed_context)

    actual = {
        index
        for index, (left, right) in enumerate(
            zip(base.hierarchical_keys, changed.hierarchical_keys, strict=True)
        )
        if left != right
    }
    assert actual == changed_levels


def test_exact_decision_identity_join_and_reprojection_are_deterministic() -> None:
    decision, trade = _complete()
    snapshots = _snapshots((decision,), (trade,))

    first = PROJECTOR.project(*snapshots)
    second = PROJECTOR.project(*snapshots)

    assert first == second
    assert first.samples[0].decision_identity == trade.identity.originating_decision_identity


def test_sample_integrity_seal_is_deterministic_and_survives_detached_copy() -> None:
    _, _, sample = _project_one()
    _, _, equivalent = _project_one()
    detached = deepcopy(sample)

    assert sample.sample_integrity_seal == equivalent.sample_integrity_seal
    assert len(sample.sample_integrity_seal) == 64
    assert detached.sample_integrity_seal == sample.sample_integrity_seal
    detached.validate()

    object.__setattr__(detached, "accounting_exit_fill_id", "forged-exit-fill")
    with pytest.raises(ExperienceProjectionError, match="sample integrity seal mismatch"):
        detached.validate()


def test_contracts_are_immutable_and_schema_versioned() -> None:
    _, _, sample = _project_one()
    snapshot = experience_module.ExperienceTradeSampleSnapshot((sample,))

    assert sample.fingerprint.fingerprint_schema_version == "1.0.0"
    assert sample.sample_schema_version == "1.0.0"
    assert len(sample.sample_integrity_seal) == 64
    assert snapshot.sample_snapshot_schema_version == "1.0.0"
    assert all(key.key_schema_version == "1.0.0" for key in sample.hierarchical_keys)
    with pytest.raises(FrozenInstanceError):
        sample.fingerprint.regime_label = RegimeLabel.NEUTRAL  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        sample.quantity = Decimal(999)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.samples = ()  # type: ignore[misc]


@pytest.mark.parametrize("authority", ("decisions", "completed"))
def test_mutated_snapshot_schema_authority_rejects(authority: str) -> None:
    decision, trade = _complete()
    decisions, completed = _snapshots((decision,), (trade,))
    target = decisions if authority == "decisions" else completed
    object.__setattr__(target, "journal_schema_version", "forged")

    with pytest.raises(ExperienceProjectionError, match="schema version mismatch"):
        PROJECTOR.project(decisions, completed)


def test_orphan_completed_trade_rejects() -> None:
    _, trade = _complete()
    with pytest.raises(ExperienceProjectionError, match="no originating decision"):
        PROJECTOR.project(*_snapshots((), (trade,)))


def test_duplicate_decision_identity_rejects() -> None:
    decision, trade = _complete()
    with pytest.raises(ExperienceProjectionError, match="duplicate decision identity"):
        PROJECTOR.project(*_snapshots((decision, decision), (trade,)))


def test_duplicate_completed_outcome_per_decision_rejects() -> None:
    decision, trade = _complete()
    with pytest.raises(ExperienceProjectionError, match="multiple completed outcomes"):
        PROJECTOR.project(*_snapshots((decision,), (trade, trade)))


@pytest.mark.parametrize("mutation", ("no_trade", "risk_rejected", "safety_safe_mode"))
def test_non_authorized_linked_outcomes_reject(mutation: str) -> None:
    decision, trade = _complete()
    if mutation == "no_trade":
        object.__setattr__(decision.strategy_evaluation, "action", CandidateAction.NO_TRADE)
    elif mutation == "risk_rejected":
        object.__setattr__(decision.risk_decision, "status", RiskDecisionStatus.REJECTED)
    else:
        object.__setattr__(decision.safety_decision, "status", OperationalSafetyStatus.SAFE_MODE)
    object.__setattr__(trade, "pre_trade_decision", deepcopy(decision))

    with pytest.raises((ExperienceProjectionError, ValueError, TypeError)):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("source_id", SourceId("forged")),
        ("symbol", ETH),
        ("strategy_id", "forged"),
        ("strategy_version", "9.9.9"),
        ("quantity", Decimal("999")),
        ("approved_risk_quote", Decimal("999")),
        ("stop_basis", Decimal("99")),
        ("risk_policy_version", "forged"),
        ("safety_policy_version", "forged"),
    ),
)
def test_provenance_policy_and_approval_mismatches_reject(
    field_name: str, replacement: object
) -> None:
    decision, trade = _complete()
    object.__setattr__(trade, field_name, replacement)

    with pytest.raises((ExperienceProjectionError, ValueError, TypeError)):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))


def test_conflicting_pretrade_authority_with_same_identity_rejects() -> None:
    decision, trade = _complete()
    changed_context = _with_regime(decision.context, volatility_state=VolatilityState.HIGH)
    conflicting = replace(decision, context=changed_context)

    with pytest.raises(ExperienceProjectionError, match="inconsistent pre-trade authority"):
        PROJECTOR.project(*_snapshots((conflicting,), (trade,)))


def test_mutated_regime_and_outcome_authority_fail_closed() -> None:
    decision, trade = _complete()
    object.__setattr__(decision.context.multi_timeframe_regime.regime_15m, "trend_strength", 60)
    object.__setattr__(trade, "pre_trade_decision", deepcopy(decision))
    with pytest.raises(DecisionJournalValidationError, match="context integrity seal"):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))

    decision, trade = _complete()
    object.__setattr__(trade, "gross_realized_pnl", trade.gross_realized_pnl + Decimal(1))
    with pytest.raises(ValueError):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))


@pytest.mark.parametrize(
    ("target", "field_name", "forged_value"),
    (
        ("anchor", "direction", Direction.DOWN),
        ("anchor", "regime_label", RegimeLabel.STRONG_UPTREND),
        ("anchor", "volatility_state", VolatilityState.HIGH),
        ("anchor", "volume_state", VolumeState.HIGH),
        ("multi", "higher_timeframe_bias", HigherTimeframeBias.MIXED),
        ("multi", "alignment", MultiTimeframeAlignment.MIXED),
    ),
)
def test_coordinated_valid_enum_regime_mutations_fail_before_sample_emission(
    target: str, field_name: str, forged_value: object
) -> None:
    decision, trade = _complete()
    for authority in (decision, trade.pre_trade_decision):
        multi = authority.context.multi_timeframe_regime
        mutation_target = multi.regime_15m if target == "anchor" else multi
        object.__setattr__(mutation_target, field_name, forged_value)

    with pytest.raises(DecisionJournalValidationError, match="context integrity seal"):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))


def _apply_coherent_context_mutation(
    authority: DecisionJournalEntry[object, object], mutation: str
) -> None:
    context = authority.context
    multi = context.multi_timeframe_regime
    anchor = multi.regime_15m
    if mutation == "volume":
        object.__setattr__(context.features_15m, "volume_zscore_20", Decimal(1))
        object.__setattr__(anchor, "volume_state", VolumeState.HIGH)
        return
    if mutation == "direction":
        anchor_evidence = list(anchor.direction_evidence)
        anchor_evidence[0] = DirectionEvidence(
            DirectionComponent.EMA_ALIGNMENT, -20, "ema_20 below ema_50"
        )
        object.__setattr__(context.features_15m, "ema_20", Decimal(99))
        object.__setattr__(anchor, "direction_evidence", tuple(anchor_evidence))
        object.__setattr__(anchor, "direction_score", 0)
        object.__setattr__(anchor, "trend_strength", 0)
        object.__setattr__(anchor, "direction", Direction.NEUTRAL)
        object.__setattr__(anchor, "regime_label", RegimeLabel.NEUTRAL)
        object.__setattr__(multi, "alignment", MultiTimeframeAlignment.HTF_UP_ENTRY_DIVERGENT)
        return
    assert mutation == "higher_timeframe"
    regime_4h = multi.regime_4h
    assert regime_4h is not None
    htf_evidence = tuple(
        replace(item, contribution=-20)
        if item.component in {DirectionComponent.EMA_ALIGNMENT, DirectionComponent.MOMENTUM_16}
        else item
        for item in regime_4h.direction_evidence
    )
    object.__setattr__(regime_4h, "direction_evidence", htf_evidence)
    object.__setattr__(regime_4h, "direction_score", -40)
    object.__setattr__(regime_4h, "trend_strength", 40)
    object.__setattr__(regime_4h, "direction", Direction.DOWN)
    object.__setattr__(regime_4h, "regime_label", RegimeLabel.DOWNTREND)
    object.__setattr__(multi, "higher_timeframe_bias", HigherTimeframeBias.MIXED)
    object.__setattr__(multi, "alignment", MultiTimeframeAlignment.MIXED)


@pytest.mark.parametrize("mutation", ("volume", "direction", "higher_timeframe"))
def test_coherent_coordinated_context_mutation_is_rejected_by_original_seal(
    mutation: str,
) -> None:
    decision, trade = _complete()
    for authority in (decision, trade.pre_trade_decision):
        _apply_coherent_context_mutation(authority, mutation)
        context = authority.context
        validate_technical_regime_snapshot(
            context.multi_timeframe_regime.regime_15m,
            context.features_15m,
            context.structure_15m,
        )
        validate_multi_timeframe_regime_snapshot(context.multi_timeframe_regime)

    with pytest.raises(DecisionJournalValidationError, match="context integrity seal"):
        PROJECTOR.project(*_snapshots((decision,), (trade,)))


def test_input_order_invariance_and_canonical_btc_eth_order() -> None:
    btc_context = _authoritative_context(cast(StrategyEvaluationContext, _context()))
    btc_decision, btc_trade = _complete(context=btc_context)
    eth_context = _authoritative_context(cast(StrategyEvaluationContext, _context(symbol=ETH)))
    eth_decision, eth_trade = _complete(context=eth_context)

    forward = PROJECTOR.project(*_snapshots((btc_decision, eth_decision), (btc_trade, eth_trade)))
    reverse = PROJECTOR.project(*_snapshots((eth_decision, btc_decision), (eth_trade, btc_trade)))

    assert forward == reverse
    assert tuple(sample.fingerprint.symbol for sample in forward.samples) == (BTC, ETH)


def test_projected_output_is_detached_from_caller_mutation() -> None:
    decision, trade = _complete()
    projected = PROJECTOR.project(*_snapshots((decision,), (trade,)))
    before = deepcopy(projected)

    object.__setattr__(decision.context.multi_timeframe_regime, "alignment", "forged")
    object.__setattr__(trade, "net_realized_pnl", Decimal("999999"))
    object.__setattr__(trade.identity.originating_decision_identity.source_id, "value", "forged")

    assert projected == before


def test_experience_module_has_no_calculator_or_trading_side_effect_dependencies() -> None:
    tree = ast.parse(Path(experience_module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "adaptive_trading_bot.accounting",
        "adaptive_trading_bot.admission",
        "adaptive_trading_bot.backtest",
        "adaptive_trading_bot.exchange",
        "adaptive_trading_bot.execution",
        "adaptive_trading_bot.orders",
        "adaptive_trading_bot.paper",
        "adaptive_trading_bot.risk",
        "adaptive_trading_bot.strategies",
    )
    public = {
        name
        for name, value in inspect.getmembers(ExperienceSampleProjector)
        if not name.startswith("_") and inspect.isfunction(value)
    }

    assert not any(name.startswith(forbidden) for name in imported)
    assert public == {"project"}


def test_fingerprint_and_sample_exclude_future_or_recomputed_fields() -> None:
    fingerprint_fields = {
        item.name.lower() for item in fields(experience_module.ExperienceContextFingerprint)
    }
    sample_fields = {item.name.lower() for item in fields(experience_module.ExperienceTradeSample)}
    forbidden = {
        "fundamental",
        "funding",
        "open_interest",
        "liquidity",
        "event_risk",
        "mfe",
        "mae",
        "process_score",
        "target",
        "expected_reward_risk",
        "confidence",
        "edge",
        "win_rate",
        "profit_factor",
    }

    assert not any(token in name for name in fingerprint_fields for token in forbidden)
    assert not any(token in name for name in sample_fields for token in forbidden)
    assert "source_id" in sample_fields
    assert "source_id" not in fingerprint_fields
    assert "source_id" not in {item.name for item in fields(experience_module.ExperienceContextKey)}
