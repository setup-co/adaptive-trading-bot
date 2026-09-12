import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import adaptive_trading_bot.journal.decision as journal_module
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureSnapshot,
    PivotKind,
    StructureSnapshot,
)
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalAppendError,
    DecisionJournalBatch,
    DecisionJournalEntry,
    DecisionJournalValidationError,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    HigherTimeframeBias,
    LiquidityState,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeSnapshot,
    RegimeLabel,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    OperationalSafetyGate,
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecision,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    CandidateAction,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    RangeStatus,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyReasonCode,
    TrendPullbackRuleAudit,
    TrendPullbackStrategy,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
SOURCE = SourceId("archive")
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")


def _technical(
    source_id: SourceId,
    symbol: TradingSymbol,
    observed_at: datetime,
    timeframe: Timeframe,
) -> TechnicalRegimeSnapshot:
    contributions = (20, 20, 0, 0, 0)
    return TechnicalRegimeSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        bars_seen=50,
        direction=Direction.UP,
        direction_score=40,
        trend_strength=40,
        direction_evidence=tuple(
            DirectionEvidence(component, contribution, "journal test")
            for component, contribution in zip(DirectionComponent, contributions, strict=True)
        ),
        volatility_percentile=Decimal(50),
        volatility_state=VolatilityState.NORMAL,
        volume_state=VolumeState.NORMAL,
        liquidity_state=LiquidityState.UNKNOWN,
        event_risk_state=EventRiskState.UNKNOWN,
        regime_label=RegimeLabel.UPTREND,
        is_fully_classified=True,
    )


def _context(
    *,
    source_id: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
    observed_at: datetime = NOW,
) -> StrategyEvaluationContext:
    candle = Candle(
        symbol,
        Timeframe.MINUTES_15,
        observed_at - timedelta(minutes=15),
        observed_at,
        Decimal(104),
        Decimal(106),
        Decimal("97.5"),
        Decimal(105),
        Decimal(20),
    )
    features = FeatureSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        observed_at=observed_at,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        bars_seen=50,
        ema_20=Decimal(102),
        ema_50=Decimal(100),
        atr_14=Decimal(10),
        rsi_14=Decimal(50),
        vwap_utc_day=Decimal(100),
        return_1=Decimal(0),
        momentum_4=Decimal("0.01"),
        momentum_16=Decimal("0.01"),
        volume_zscore_20=Decimal(0),
        realized_volatility_20=Decimal("0.01"),
        is_fully_warmed=True,
    )
    low = ConfirmedPivot(
        source_id=source_id,
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        kind=PivotKind.LOW,
        pivot_open_time=observed_at - timedelta(hours=1),
        pivot_close_time=observed_at - timedelta(minutes=45),
        pivot_price=Decimal(75),
        confirmed_at=observed_at - timedelta(minutes=15),
        left_bars=2,
        right_bars=2,
        pivot_bar_number=45,
        relation=None,
    )
    structure = StructureSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        observed_at=observed_at,
        bars_seen=50,
        newly_confirmed=(),
        latest_confirmed_high=None,
        latest_confirmed_low=low,
        latest_high_relation=None,
        latest_low_relation=None,
    )
    regime_15m = _technical(source_id, symbol, observed_at, Timeframe.MINUTES_15)
    regime_1h = _technical(source_id, symbol, observed_at, Timeframe.HOUR_1)
    regime_4h = _technical(source_id, symbol, observed_at, Timeframe.HOURS_4)
    regime = MultiTimeframeRegimeSnapshot(
        source_id=source_id,
        symbol=symbol,
        observed_at=observed_at,
        regime_15m=regime_15m,
        regime_1h=regime_1h,
        regime_4h=regime_4h,
        age_1h=timedelta(),
        age_4h=timedelta(),
        is_1h_fresh=True,
        is_4h_fresh=True,
        is_fully_ready=True,
        higher_timeframe_bias=HigherTimeframeBias.UP,
        alignment=MultiTimeframeAlignment.ALIGNED_UP,
        has_extreme_volatility=False,
        extreme_volatility_timeframes=(),
        readiness_reasons=(),
    )
    return StrategyEvaluationContext(
        source_id, symbol, observed_at, candle, features, structure, regime
    )


def _manual_evaluation(
    context: StrategyEvaluationContext,
    *,
    strategy_id: str = "manual",
    strategy_version: str = "1.0.0",
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    reason: object = StrategyReasonCode.CONTEXT_READY,
    audit: object = "opaque-audit",
    invalidation: Decimal = Decimal(75),
) -> StrategyEvaluation[object, object]:
    candidate = action is not CandidateAction.NO_TRADE
    return StrategyEvaluation(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        source_id=context.source_id,
        symbol=context.symbol,
        observed_at=context.observed_at,
        action=action,
        reference_price=context.candle_15m.close if candidate else None,
        invalidation_reference_price=invalidation if candidate else None,
        reasons_for=(reason,),
        reasons_against=(),
        rule_audit=audit,
    )


def _health(risk: RiskDecision, *, safe_mode: bool = False) -> OperationalHealthSnapshot:
    risk_source = risk.source_id
    risk_symbol = risk.symbol
    risk_observed_at = risk.observed_at
    return OperationalHealthSnapshot(
        source_id=risk_source,
        symbol=risk_symbol,
        observed_at=risk_observed_at,
        market_data_observed_at=risk_observed_at - timedelta(seconds=30),
        feed_integrity_state=(FeedIntegrityState.BROKEN if safe_mode else FeedIntegrityState.OK),
        exchange_api_health_state=ExchangeApiHealthState.HEALTHY,
        spread_bps=Decimal(1),
        time_sync_offset_ms=Decimal(0),
        volatility_safety_state=VolatilitySafetyState.CLASSIFIED_NORMAL,
        suspicious_price_anomaly=False,
        consecutive_order_rejections=0,
    )


def _entry(
    outcome: str = "cleared",
    *,
    context: StrategyEvaluationContext | None = None,
    strategy_id: str = "manual",
    strategy_version: str = "1.0.0",
    reason: object = StrategyReasonCode.CONTEXT_READY,
    audit: object = "opaque-audit",
) -> DecisionJournalEntry[object, object]:
    current_context = _context() if context is None else context
    action = CandidateAction.NO_TRADE if outcome == "no_action" else CandidateAction.LONG_CANDIDATE
    invalidation = Decimal(106) if outcome == "risk_blocked" else Decimal(75)
    evaluation = _manual_evaluation(
        current_context,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        action=action,
        reason=reason,
        audit=audit,
        invalidation=invalidation,
    )
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            current_context.observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    safety = OperationalSafetyGate().evaluate(risk, _health(risk, safe_mode=outcome == "safe_mode"))
    return DecisionJournalEntry(current_context, evaluation, risk, safety)


def _batch(
    *entries: DecisionJournalEntry[object, object],
) -> DecisionJournalBatch[object, object]:
    return DecisionJournalBatch(entries[0].identity.observed_at, entries)


def test_complete_strategy_risk_safety_journal_integration_chain() -> None:
    context = _context()
    evaluation = TrendPullbackStrategy().evaluate(context)
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    safety = OperationalSafetyGate().evaluate(risk, _health(risk))
    entry = DecisionJournalEntry(context, evaluation, risk, safety)
    journal = InMemoryDecisionJournal[StrategyReasonCode, TrendPullbackRuleAudit]()

    result = journal.append_batch(DecisionJournalBatch(NOW, (entry,)))

    assert evaluation.action is CandidateAction.LONG_CANDIDATE
    assert safety.status is OperationalSafetyStatus.CLEARED
    assert result.appended_count == 1
    assert journal.snapshot().entries == (entry,)
    assert entry.context is context
    assert entry.strategy_evaluation is evaluation
    assert entry.risk_decision is risk
    assert entry.safety_decision is safety


def test_identity_entry_batch_and_snapshot_are_immutable_and_versioned() -> None:
    entry = _entry()
    batch = _batch(entry)
    journal = InMemoryDecisionJournal[object, object]()
    result = journal.append_batch(batch)
    snapshot = journal.snapshot()

    assert entry.journal_schema_version == "1.0.0"
    assert snapshot.journal_schema_version == "1.0.0"
    assert entry.identity == DecisionIdentity(SOURCE, BTC, NOW, "manual", "1.0.0")
    with pytest.raises(FrozenInstanceError):
        entry.identity.strategy_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        batch.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.total_count = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.entries = ()  # type: ignore[misc]


def test_pretrade_context_integrity_seal_survives_deepcopy_and_rejects_raw_mutation() -> None:
    entry = _entry()
    detached = deepcopy(entry)

    assert _entry().pre_trade_context_integrity_seal == entry.pre_trade_context_integrity_seal
    assert detached.pre_trade_context_integrity_seal == entry.pre_trade_context_integrity_seal
    assert len(detached.pre_trade_context_integrity_seal) == 64
    detached.validate()

    object.__setattr__(detached.context.features_15m, "volume_zscore_20", Decimal(1))
    with pytest.raises(DecisionJournalValidationError, match="context integrity seal"):
        detached.validate()


@pytest.mark.parametrize(
    ("outcome", "risk_status", "safety_status"),
    [
        ("no_action", RiskDecisionStatus.NO_ACTION, OperationalSafetyStatus.NO_ACTION),
        ("risk_blocked", RiskDecisionStatus.REJECTED, OperationalSafetyStatus.BLOCKED_BY_RISK),
        ("safe_mode", RiskDecisionStatus.APPROVED, OperationalSafetyStatus.SAFE_MODE),
        ("cleared", RiskDecisionStatus.APPROVED, OperationalSafetyStatus.CLEARED),
    ],
)
def test_every_decision_outcome_is_accepted_and_retained(
    outcome: str,
    risk_status: RiskDecisionStatus,
    safety_status: OperationalSafetyStatus,
) -> None:
    entry = _entry(outcome)
    journal = InMemoryDecisionJournal[object, object]()

    journal.append_batch(_batch(entry))

    retained = journal.snapshot().entries[0]
    assert retained.risk_decision.status is risk_status
    assert retained.safety_decision.status is safety_status
    assert retained.strategy_evaluation.reasons_for


def test_cleared_entry_retains_exact_risk_approved_sizing() -> None:
    entry = _entry()

    assert entry.safety_decision.approved_quantity == entry.risk_decision.approved_quantity
    assert entry.safety_decision.approved_notional_quote == (
        entry.risk_decision.approved_notional_quote
    )
    assert entry.safety_decision.approved_risk_quote == entry.risk_decision.approved_risk_quote
    assert entry.safety_decision.approved_risk_stop_basis == (
        entry.risk_decision.approved_risk_stop_basis
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": SourceId("other")},
        {"symbol": ETH},
        {"observed_at": NOW + timedelta(minutes=15)},
    ],
)
def test_context_strategy_identity_mismatch_rejects(changes: dict[str, object]) -> None:
    entry = _entry()
    evaluation = replace(entry.strategy_evaluation, **changes)  # type: ignore[arg-type]

    with pytest.raises(DecisionJournalValidationError, match="context and strategy"):
        DecisionJournalEntry(entry.context, evaluation, entry.risk_decision, entry.safety_decision)


@pytest.mark.parametrize(
    "changes",
    [
        {"strategy_id": "other"},
        {"strategy_version": "2.0.0"},
        {"strategy_candidate_action": CandidateAction.SHORT_CANDIDATE},
        {"source_id": SourceId("other")},
        {"symbol": ETH},
        {"observed_at": NOW + timedelta(minutes=15)},
        {"risk_snapshot_observed_at": NOW + timedelta(minutes=15)},
    ],
)
def test_strategy_risk_chain_mismatch_rejects(changes: dict[str, object]) -> None:
    entry = _entry()
    risk = replace(entry.risk_decision, **changes)  # type: ignore[arg-type]

    with pytest.raises(DecisionJournalValidationError, match="strategy and risk"):
        DecisionJournalEntry(entry.context, entry.strategy_evaluation, risk, entry.safety_decision)


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": SourceId("other")},
        {"symbol": ETH},
        {"observed_at": NOW + timedelta(minutes=15)},
        {"input_risk_status": RiskDecisionStatus.REJECTED},
        {"risk_policy_version": "other"},
        {"risk_requires_halt_review": True},
    ],
)
def test_risk_safety_chain_mismatch_rejects(changes: dict[str, object]) -> None:
    entry = _entry()
    safety = replace(entry.safety_decision, **changes)  # type: ignore[arg-type]

    with pytest.raises(DecisionJournalValidationError, match="risk and safety"):
        DecisionJournalEntry(entry.context, entry.strategy_evaluation, entry.risk_decision, safety)


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        (
            "health_source_id",
            SourceId("other"),
            OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH,
        ),
        ("health_symbol", ETH, OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH),
        (
            "health_observed_at",
            NOW + timedelta(seconds=1),
            OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH,
        ),
    ],
)
def test_safety_health_audit_requires_matching_mismatch_reason(
    field_name: str,
    value: object,
    reason: OperationalSafetyReasonCode,
) -> None:
    entry = _entry("safe_mode")
    safety = replace(
        entry.safety_decision,
        **{field_name: value, "reasons": (reason,)},  # type: ignore[arg-type]
    )
    accepted = DecisionJournalEntry(
        entry.context, entry.strategy_evaluation, entry.risk_decision, safety
    )
    assert accepted.safety_decision.status is OperationalSafetyStatus.SAFE_MODE

    contradictory = replace(safety, reasons=())
    with pytest.raises(DecisionJournalValidationError, match="health"):
        DecisionJournalEntry(
            entry.context,
            entry.strategy_evaluation,
            entry.risk_decision,
            contradictory,
        )


def test_health_mismatch_cannot_be_stitched_to_cleared_safety() -> None:
    entry = _entry()
    safety = replace(
        entry.safety_decision,
        health_source_id=SourceId("other"),
        reasons=(
            *entry.safety_decision.reasons,
            OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH,
        ),
    )

    with pytest.raises(DecisionJournalValidationError, match="requires SAFE_MODE"):
        DecisionJournalEntry(entry.context, entry.strategy_evaluation, entry.risk_decision, safety)


def test_non_cleared_entry_permission_forgery_rejects() -> None:
    entry = _entry("risk_blocked")
    safety = replace(entry.safety_decision, entry_permitted=True)

    with pytest.raises(DecisionJournalValidationError, match="entry permission"):
        DecisionJournalEntry(entry.context, entry.strategy_evaluation, entry.risk_decision, safety)


def test_cleared_with_nonapproved_risk_rejects() -> None:
    blocked = _entry("risk_blocked")
    safety = replace(
        blocked.safety_decision,
        status=OperationalSafetyStatus.CLEARED,
        entry_permitted=True,
    )

    with pytest.raises(DecisionJournalValidationError, match="risk APPROVED"):
        DecisionJournalEntry(
            blocked.context,
            blocked.strategy_evaluation,
            blocked.risk_decision,
            safety,
        )


@pytest.mark.parametrize("changes", [{"strategy_id": ""}, {"strategy_version": "   "}])
def test_empty_strategy_identity_rejects(changes: dict[str, object]) -> None:
    entry = _entry()
    evaluation = replace(entry.strategy_evaluation, **changes)  # type: ignore[arg-type]

    with pytest.raises(DecisionJournalValidationError, match="non-empty"):
        DecisionJournalEntry(entry.context, evaluation, entry.risk_decision, entry.safety_decision)


@pytest.mark.parametrize(
    ("field_name", "delta"),
    [
        ("approved_quantity", Decimal(1)),
        ("approved_notional_quote", Decimal(1)),
        ("approved_risk_quote", Decimal(1)),
        ("approved_risk_stop_basis", Decimal(1)),
    ],
)
def test_cleared_sizing_mismatch_rejects(field_name: str, delta: Decimal) -> None:
    entry = _entry()
    current = getattr(entry.safety_decision, field_name)
    assert isinstance(current, Decimal)
    if field_name == "approved_quantity":
        safety = replace(entry.safety_decision, approved_quantity=current + delta)
    elif field_name == "approved_notional_quote":
        safety = replace(entry.safety_decision, approved_notional_quote=current + delta)
    elif field_name == "approved_risk_quote":
        safety = replace(entry.safety_decision, approved_risk_quote=current + delta)
    else:
        safety = replace(entry.safety_decision, approved_risk_stop_basis=current + delta)

    with pytest.raises(DecisionJournalValidationError, match="sizing"):
        DecisionJournalEntry(entry.context, entry.strategy_evaluation, entry.risk_decision, safety)


def test_strategy_no_trade_stitched_to_approval_rejects() -> None:
    no_action = _entry("no_action")
    cleared = _entry()
    risk = replace(
        cleared.risk_decision,
        strategy_id=no_action.strategy_evaluation.strategy_id,
        strategy_version=no_action.strategy_evaluation.strategy_version,
        strategy_candidate_action=CandidateAction.NO_TRADE,
    )
    safety = replace(
        cleared.safety_decision,
        input_risk_status=risk.status,
        risk_policy_version=risk.risk_policy_version,
    )

    with pytest.raises(DecisionJournalValidationError, match="NO_TRADE"):
        DecisionJournalEntry(no_action.context, no_action.strategy_evaluation, risk, safety)


def _strategy_audits() -> tuple[tuple[object, object], ...]:
    return (
        (
            StrategyReasonCode.CONTEXT_READY,
            TrendPullbackRuleAudit(None, None, None, None, None, None, None, None, None),
        ),
        (
            BreakoutRetestReasonCode.RETEST_ZONE_TOUCHED,
            BreakoutRetestRuleAudit(
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ),
        (
            RangeMeanReversionReasonCode.VALIDATED_RANGE_ACTIVE,
            RangeMeanReversionRuleAudit(
                range_status=RangeStatus.INSUFFICIENT_DATA,
                range_reasons=(),
                lower_boundary=None,
                upper_boundary=None,
                midpoint=None,
                range_width=None,
                width_in_atr=None,
                current_atr=None,
                lower_entry_zone_ceiling=None,
                upper_entry_zone_floor=None,
                lower_excursion_floor=None,
                upper_excursion_ceiling=None,
                candle_low=Decimal(1),
                candle_high=Decimal(2),
                candle_close=Decimal("1.5"),
                rsi_14=None,
                momentum_4=None,
                selected_side=None,
                invalidation_reference=None,
                invalidation_distance=None,
            ),
        ),
    )


@pytest.mark.parametrize(("reason", "audit"), _strategy_audits())
def test_all_v1_strategy_reason_and_audit_types_use_one_journal_contract(
    reason: object, audit: object
) -> None:
    entry = _entry(reason=reason, audit=audit)
    journal = InMemoryDecisionJournal[object, object]()

    journal.append_batch(_batch(entry))

    retained = journal.snapshot().entries[0]
    assert retained.strategy_evaluation.reasons_for == (reason,)
    assert retained.strategy_evaluation.rule_audit is audit


def test_batch_requires_nonempty_immutable_tuple() -> None:
    with pytest.raises(DecisionJournalValidationError, match="non-empty"):
        DecisionJournalBatch[object, object](NOW, ())
    with pytest.raises(TypeError, match="immutable tuple"):
        DecisionJournalBatch[object, object](NOW, [_entry()])  # type: ignore[arg-type]


def test_batch_rejects_entry_from_another_observation_time() -> None:
    later = _entry(context=_context(observed_at=NOW + timedelta(minutes=15)))

    with pytest.raises(DecisionJournalValidationError, match="batch observed_at"):
        DecisionJournalBatch[object, object](NOW, (later,))


def test_duplicate_identity_within_batch_rejects() -> None:
    entry = _entry()

    with pytest.raises(DecisionJournalValidationError, match="within batch"):
        _batch(entry, entry)


def test_duplicate_identity_across_batches_rejects_without_mutation() -> None:
    entry = _entry()
    journal = InMemoryDecisionJournal[object, object]()
    journal.append_batch(_batch(entry))
    before = journal.snapshot()

    with pytest.raises(DecisionJournalAppendError, match="already exists"):
        journal.append_batch(_batch(entry))

    assert journal.snapshot() == before


def test_batch_is_canonical_across_symbols_sources_and_strategies() -> None:
    source_b = SourceId("zz-archive")
    entries = (
        _entry(context=_context(source_id=source_b), strategy_id="beta"),
        _entry(context=_context(symbol=ETH), strategy_id="alpha"),
        _entry(strategy_id="beta"),
        _entry(strategy_id="alpha"),
    )

    batch = _batch(*entries)

    assert tuple(
        (entry.identity.source_id.value, str(entry.identity.symbol), entry.identity.strategy_id)
        for entry in batch.entries
    ) == (
        ("archive", "BTC/USDT", "alpha"),
        ("archive", "BTC/USDT", "beta"),
        ("archive", "ETH/USDT", "alpha"),
        ("zz-archive", "BTC/USDT", "beta"),
    )


def test_invalid_multi_entry_append_is_atomic_and_preserves_prior_snapshot() -> None:
    existing = _entry(strategy_id="existing")
    new_same_time = _entry(strategy_id="new")
    journal = InMemoryDecisionJournal[object, object]()
    journal.append_batch(_batch(existing))
    before = journal.snapshot()

    with pytest.raises(DecisionJournalAppendError):
        journal.append_batch(_batch(new_same_time, existing))

    assert journal.snapshot() == before


@pytest.mark.parametrize("delta", [timedelta(), -timedelta(minutes=15)])
def test_equal_or_earlier_timestamp_cannot_be_silently_reordered(delta: timedelta) -> None:
    journal = InMemoryDecisionJournal[object, object]()
    journal.append_batch(_batch(_entry()))
    nonadvancing = _entry(context=_context(observed_at=NOW + delta), strategy_id="later-submission")

    with pytest.raises(DecisionJournalAppendError, match="strictly advance"):
        journal.append_batch(_batch(nonadvancing))


def test_repeated_streams_match_and_future_append_does_not_change_past_snapshot() -> None:
    first = _entry(strategy_id="first")
    second = _entry(context=_context(observed_at=NOW + timedelta(minutes=15)), strategy_id="second")

    left = InMemoryDecisionJournal[object, object]()
    right = InMemoryDecisionJournal[object, object]()
    for journal in (left, right):
        journal.append_batch(_batch(first))
    bounded = left.snapshot()
    left.append_batch(_batch(second))
    right.append_batch(_batch(second))

    assert left.snapshot() == right.snapshot()
    assert bounded.entries == (first,)
    assert left.snapshot().entries[:1] == bounded.entries


def test_journal_has_no_execution_path_or_forbidden_dependency() -> None:
    path = Path(journal_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "adaptive_trading_bot.backtest",
        "adaptive_trading_bot.exchange",
        "adaptive_trading_bot.storage",
        "adaptive_trading_bot.replay",
        "adaptive_trading_bot.market_data",
        "adaptive_trading_bot.strategies.trend_pullback",
        "adaptive_trading_bot.strategies.breakout_retest",
        "adaptive_trading_bot.strategies.range_mean_reversion",
        "sqlalchemy",
    )
    journal = InMemoryDecisionJournal[object, object]()

    assert not any(name.startswith(forbidden) for name in imported)
    assert not hasattr(journal, "execute")
    assert not hasattr(journal, "replace")
    assert not hasattr(_entry(), "trade_id")
    assert not hasattr(_entry(), "order_id")


def test_journal_does_not_fabricate_unavailable_stage_or_outcome_fields() -> None:
    entry = _entry()

    for field_name in (
        "fundamental_score",
        "experience_confidence",
        "target_price",
        "expected_reward_risk",
        "trade_id",
        "order_id",
        "fill_price",
        "exit_price",
        "realized_pnl",
        "realized_r",
        "mfe",
        "mae",
        "process_score",
        "outcome_classification",
    ):
        assert not hasattr(entry, field_name)
