from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext
from enum import StrEnum
from typing import cast

import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureBatch,
    FeatureSeries,
    FeatureSnapshot,
    ObservedCandle,
    ObservedCandleBatch,
    PivotKind,
    StructureBatch,
    StructureSnapshot,
)
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    HigherTimeframeBias,
    LiquidityState,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeBatch,
    MultiTimeframeRegimeSnapshot,
    RegimeLabel,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    Strategy,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyReasonCode,
    TrendPullbackConfiguration,
    TrendPullbackRuleAudit,
    TrendPullbackStrategy,
    assemble_strategy_contexts,
)

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def technical(
    timeframe: Timeframe,
    direction: Direction,
    *,
    classified: bool = True,
) -> TechnicalRegimeSnapshot:
    score = {Direction.UP: 40, Direction.DOWN: -40, Direction.NEUTRAL: 0}[direction]
    values = {
        40: (20, 20, 0, 0, 0),
        -40: (-20, -20, 0, 0, 0),
        0: (0, 0, 0, 0, 0),
    }[score]
    evidence = tuple(
        DirectionEvidence(component, contribution, "test")
        for component, contribution in zip(DirectionComponent, values, strict=True)
    )
    return TechnicalRegimeSnapshot(
        source_id=SOURCE,
        symbol=BTC,
        timeframe=timeframe,
        observed_at=START,
        bars_seen=50,
        direction=direction,
        direction_score=score,
        trend_strength=abs(score),
        direction_evidence=evidence,
        volatility_percentile=Decimal(50) if classified else None,
        volatility_state=VolatilityState.NORMAL if classified else VolatilityState.UNKNOWN,
        volume_state=VolumeState.NORMAL,
        liquidity_state=LiquidityState.UNKNOWN,
        event_risk_state=EventRiskState.UNKNOWN,
        regime_label=RegimeLabel.UPTREND if classified else RegimeLabel.INSUFFICIENT_DATA,
        is_fully_classified=classified,
    )


def pivot(kind: PivotKind, price: Decimal) -> ConfirmedPivot:
    return ConfirmedPivot(
        source_id=SOURCE,
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        kind=kind,
        pivot_open_time=START - timedelta(hours=1),
        pivot_close_time=START - timedelta(minutes=45),
        pivot_price=price,
        confirmed_at=START - timedelta(minutes=15),
        left_bars=2,
        right_bars=2,
        pivot_bar_number=45,
        relation=None,
    )


def evaluation_context(
    *,
    side: CandidateAction = CandidateAction.LONG_CANDIDATE,
    ready: bool = True,
    bias: HigherTimeframeBias | None = None,
    entry_direction: Direction | None = None,
    extreme: bool = False,
) -> StrategyEvaluationContext:
    long = side is CandidateAction.LONG_CANDIDATE
    direction = Direction.UP if long else Direction.DOWN
    entry = entry_direction or direction
    htf_bias = bias or (HigherTimeframeBias.UP if long else HigherTimeframeBias.DOWN)
    if long:
        candle = Candle(
            BTC,
            Timeframe.MINUTES_15,
            START - timedelta(minutes=15),
            START,
            Decimal(104),
            Decimal(106),
            Decimal("97.5"),
            Decimal(105),
            Decimal(20),
        )
        ema20, ema50, momentum = Decimal(102), Decimal(100), Decimal("0.01")
        latest_high, latest_low = None, pivot(PivotKind.LOW, Decimal(75))
    else:
        candle = Candle(
            BTC,
            Timeframe.MINUTES_15,
            START - timedelta(minutes=15),
            START,
            Decimal(96),
            Decimal("102.5"),
            Decimal(94),
            Decimal(95),
            Decimal(20),
        )
        ema20, ema50, momentum = Decimal(98), Decimal(100), Decimal("-0.01")
        latest_high, latest_low = pivot(PivotKind.HIGH, Decimal(125)), None
    feature = FeatureSnapshot(
        source_id=SOURCE,
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        observed_at=START,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        bars_seen=50,
        ema_20=ema20,
        ema_50=ema50,
        atr_14=Decimal(10),
        rsi_14=Decimal(50),
        vwap_utc_day=Decimal(100),
        return_1=Decimal(0),
        momentum_4=momentum,
        momentum_16=momentum,
        volume_zscore_20=Decimal(0),
        realized_volatility_20=Decimal("0.01"),
        is_fully_warmed=True,
    )
    structure = StructureSnapshot(
        source_id=SOURCE,
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        observed_at=START,
        bars_seen=50,
        newly_confirmed=(),
        latest_confirmed_high=latest_high,
        latest_confirmed_low=latest_low,
        latest_high_relation=None,
        latest_low_relation=None,
    )
    regime_15m = technical(Timeframe.MINUTES_15, entry)
    one_direction = (
        direction
        if htf_bias in {HigherTimeframeBias.UP, HigherTimeframeBias.DOWN}
        else Direction.NEUTRAL
    )
    four_direction = one_direction
    if htf_bias is HigherTimeframeBias.MIXED:
        one_direction, four_direction = Direction.UP, Direction.DOWN
    regime_1h = technical(Timeframe.HOUR_1, one_direction)
    regime_4h = technical(Timeframe.HOURS_4, four_direction)
    mtf = MultiTimeframeRegimeSnapshot(
        source_id=SOURCE,
        symbol=BTC,
        observed_at=START,
        regime_15m=regime_15m,
        regime_1h=regime_1h,
        regime_4h=regime_4h,
        age_1h=timedelta(),
        age_4h=timedelta(),
        is_1h_fresh=True,
        is_4h_fresh=True,
        is_fully_ready=ready,
        higher_timeframe_bias=htf_bias,
        alignment=MultiTimeframeAlignment.ALIGNED_UP
        if long
        else MultiTimeframeAlignment.ALIGNED_DOWN,
        has_extreme_volatility=extreme,
        extreme_volatility_timeframes=(Timeframe.MINUTES_15,) if extreme else (),
        readiness_reasons=() if ready else ("not ready",),
    )
    return StrategyEvaluationContext(SOURCE, BTC, START, candle, feature, structure, mtf)


def evaluate(
    context: StrategyEvaluationContext,
) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
    return TrendPullbackStrategy().evaluate(context)


def batches_for(
    *contexts: StrategyEvaluationContext,
) -> tuple[ObservedCandleBatch, FeatureBatch, StructureBatch, MultiTimeframeRegimeBatch]:
    observed_at = contexts[0].observed_at
    return (
        ObservedCandleBatch(
            observed_at,
            tuple(
                ObservedCandle(
                    FeatureSeries(
                        cast(SourceId, context.source_id),
                        context.symbol,
                        Timeframe.MINUTES_15,
                    ),
                    context.candle_15m,
                )
                for context in contexts
            ),
        ),
        FeatureBatch(observed_at, tuple(context.features_15m for context in contexts)),
        StructureBatch(
            observed_at,
            tuple(context.structure_15m for context in contexts),
            tuple(pivot for context in contexts for pivot in context.structure_15m.newly_confirmed),
        ),
        MultiTimeframeRegimeBatch(
            observed_at, tuple(context.multi_timeframe_regime for context in contexts)
        ),
    )


def with_identity(
    context: StrategyEvaluationContext,
    source_id: SourceId,
    symbol: TradingSymbol,
) -> StrategyEvaluationContext:
    candle = replace(context.candle_15m, symbol=symbol)
    feature = replace(context.features_15m, source_id=source_id, symbol=symbol)

    def clone_pivot(item: ConfirmedPivot | None) -> ConfirmedPivot | None:
        return None if item is None else replace(item, source_id=source_id, symbol=symbol)

    structure = replace(
        context.structure_15m,
        source_id=source_id,
        symbol=symbol,
        latest_confirmed_high=clone_pivot(context.structure_15m.latest_confirmed_high),
        latest_confirmed_low=clone_pivot(context.structure_15m.latest_confirmed_low),
    )

    def clone_regime(item: TechnicalRegimeSnapshot) -> TechnicalRegimeSnapshot:
        return replace(item, source_id=source_id, symbol=symbol)

    def clone_optional_regime(
        item: TechnicalRegimeSnapshot | None,
    ) -> TechnicalRegimeSnapshot | None:
        return None if item is None else replace(item, source_id=source_id, symbol=symbol)

    regime = replace(
        context.multi_timeframe_regime,
        source_id=source_id,
        symbol=symbol,
        regime_15m=clone_regime(context.multi_timeframe_regime.regime_15m),
        regime_1h=clone_optional_regime(context.multi_timeframe_regime.regime_1h),
        regime_4h=clone_optional_regime(context.multi_timeframe_regime.regime_4h),
    )
    assert regime.regime_15m is not None
    return StrategyEvaluationContext(
        source_id, symbol, context.observed_at, candle, feature, structure, regime
    )


class DummyReason(StrEnum):
    """Evidence vocabulary owned by a non-Trend-Pullback strategy."""

    OBSERVATION_ACCEPTED = "OBSERVATION_ACCEPTED"


@dataclass(frozen=True, slots=True)
class DummyAudit:
    """Audit shape unrelated to Trend Pullback indicators and geometry."""

    observation_label: str


@dataclass(frozen=True, slots=True)
class DummyStrategy:
    """Minimal independent implementation proving the shared contract is reusable."""

    @property
    def strategy_id(self) -> str:
        return "dummy"

    @property
    def strategy_version(self) -> str:
        return "0.0.1"

    def evaluate(
        self, context: StrategyEvaluationContext
    ) -> StrategyEvaluation[DummyReason, DummyAudit]:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            action=CandidateAction.NO_TRADE,
            reference_price=None,
            invalidation_reference_price=None,
            reasons_for=(DummyReason.OBSERVATION_ACCEPTED,),
            reasons_against=(),
            rule_audit=DummyAudit(observation_label="dummy-only"),
        )


def test_context_assembler_isolates_symbols_and_sources_in_canonical_order() -> None:
    source_b = SourceId("zz-archive")
    eth = TradingSymbol("ETH", "USDT")
    base = evaluation_context()
    contexts = (
        with_identity(base, source_b, BTC),
        with_identity(base, SOURCE, eth),
        base,
    )

    assembled = assemble_strategy_contexts(*batches_for(*contexts))

    assert tuple((item.source_id.value, str(item.symbol)) for item in assembled) == (
        ("archive", "BTC/USDT"),
        ("archive", "ETH/USDT"),
        ("zz-archive", "BTC/USDT"),
    )


def test_context_assembler_fails_atomically_on_missing_identity() -> None:
    batches = batches_for(evaluation_context())
    missing_features = replace(batches[1], snapshots=())

    with pytest.raises(StrategyContextError, match="identity sets must match exactly"):
        assemble_strategy_contexts(batches[0], missing_features, batches[2], batches[3])


def test_context_assembler_rejects_mismatched_batch_timestamp() -> None:
    batches = batches_for(evaluation_context())
    stale_structures = replace(batches[2], observed_at=START - timedelta(minutes=15))

    with pytest.raises(StrategyContextError, match="batch timestamps must match"):
        assemble_strategy_contexts(batches[0], batches[1], stale_structures, batches[3])


def test_context_assembler_rejects_high_slot_containing_low_pivot() -> None:
    context = evaluation_context(side=CandidateAction.SHORT_CANDIDATE)
    high = context.structure_15m.latest_confirmed_high
    assert high is not None
    malformed = replace(
        context.structure_15m,
        latest_confirmed_high=replace(high, kind=PivotKind.LOW),
    )
    batches = batches_for(context)
    structures = replace(batches[2], snapshots=(malformed,))

    with pytest.raises(StrategyContextError, match="pivot identity, kind, or causality"):
        assemble_strategy_contexts(batches[0], batches[1], structures, batches[3])


def test_context_assembler_rejects_low_slot_containing_high_pivot() -> None:
    context = evaluation_context()
    low = context.structure_15m.latest_confirmed_low
    assert low is not None
    malformed = replace(
        context.structure_15m,
        latest_confirmed_low=replace(low, kind=PivotKind.HIGH),
    )
    batches = batches_for(context)
    structures = replace(batches[2], snapshots=(malformed,))

    with pytest.raises(StrategyContextError, match="pivot identity, kind, or causality"):
        assemble_strategy_contexts(batches[0], batches[1], structures, batches[3])


def test_shared_strategy_protocol_is_environment_neutral_and_callable() -> None:
    strategy: Strategy[StrategyReasonCode, TrendPullbackRuleAudit] = TrendPullbackStrategy()

    assert isinstance(strategy, Strategy)
    assert strategy.strategy_id == "trend_pullback"
    assert strategy.strategy_version == "1.0.0"
    assert strategy.evaluate(evaluation_context()).action is CandidateAction.LONG_CANDIDATE


def test_shared_strategy_contract_accepts_independent_reason_and_audit_types() -> None:
    strategy: Strategy[DummyReason, DummyAudit] = DummyStrategy()

    assert isinstance(strategy, Strategy)
    result = strategy.evaluate(evaluation_context())
    assert result.reasons_for == (DummyReason.OBSERVATION_ACCEPTED,)
    assert result.rule_audit == DummyAudit(observation_label="dummy-only")


@pytest.mark.parametrize(
    ("side", "expected_action", "pivot_price"),
    [
        (CandidateAction.LONG_CANDIDATE, CandidateAction.LONG_CANDIDATE, Decimal(75)),
        (CandidateAction.SHORT_CANDIDATE, CandidateAction.SHORT_CANDIDATE, Decimal(125)),
    ],
)
def test_long_and_short_happy_paths_accept_exact_rsi_volume_depth_and_distance_boundaries(
    side: CandidateAction,
    expected_action: CandidateAction,
    pivot_price: Decimal,
) -> None:
    context = evaluation_context(side=side)
    result = evaluate(context)

    assert result.action is expected_action
    assert result.reference_price == context.candle_15m.close
    assert result.invalidation_reference_price == pivot_price
    expected_boundary = (
        Decimal("97.50") if side is CandidateAction.LONG_CANDIDATE else Decimal("102.50")
    )
    assert result.rule_audit.value_zone_boundary == expected_boundary
    assert result.rule_audit.invalidation_distance == Decimal(30)
    assert result.reasons_against == ()
    assert len(result.reasons_for) == 15


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"ready": False}, StrategyReasonCode.CONTEXT_NOT_READY),
        ({"extreme": True}, StrategyReasonCode.EXTREME_VOLATILITY),
        ({"bias": HigherTimeframeBias.MIXED}, StrategyReasonCode.HTF_BIAS_NOT_DIRECTIONAL),
        ({"bias": HigherTimeframeBias.NEUTRAL}, StrategyReasonCode.HTF_BIAS_NOT_DIRECTIONAL),
        ({"bias": HigherTimeframeBias.DOWN}, StrategyReasonCode.ONE_HOUR_DIRECTION_MISMATCH),
        ({"entry_direction": Direction.DOWN}, StrategyReasonCode.ENTRY_DIRECTION_OPPOSED),
    ],
)
def test_context_regime_gates_return_no_trade(
    changes: dict[str, object], reason: StrategyReasonCode
) -> None:
    context = evaluation_context(**changes)  # type: ignore[arg-type]
    result = evaluate(context)

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against
    assert result.reference_price is None


@pytest.mark.parametrize(
    ("field_name", "reason"),
    [
        ("ema_20", StrategyReasonCode.MISSING_EMA_20),
        ("ema_50", StrategyReasonCode.MISSING_EMA_50),
        ("atr_14", StrategyReasonCode.MISSING_ATR_14),
        ("rsi_14", StrategyReasonCode.MISSING_RSI_14),
        ("momentum_4", StrategyReasonCode.MISSING_MOMENTUM_4),
        ("volume_zscore_20", StrategyReasonCode.MISSING_VOLUME_ZSCORE_20),
    ],
)
def test_missing_required_feature_returns_no_trade(
    field_name: str, reason: StrategyReasonCode
) -> None:
    context = evaluation_context()
    feature = replace(context.features_15m, **{field_name: None})  # type: ignore[arg-type]

    result = evaluate(replace(context, features_15m=feature))

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


@pytest.mark.parametrize("atr", [Decimal(0), Decimal(-1)])
def test_nonpositive_atr_fails_closed(atr: Decimal) -> None:
    context = evaluation_context()
    feature = replace(context.features_15m, atr_14=atr)

    result = evaluate(replace(context, features_15m=feature))

    assert result.action is CandidateAction.NO_TRADE
    assert StrategyReasonCode.ATR_NOT_POSITIVE in result.reasons_against


@pytest.mark.parametrize(
    ("context_change", "candle_change", "feature_change", "structure_change", "reason"),
    [
        ({}, {}, {"ema_20": Decimal(100)}, {}, StrategyReasonCode.EMA_ALIGNMENT_FAILED),
        ({}, {"low": Decimal(103)}, {}, {}, StrategyReasonCode.VALUE_ZONE_NOT_TOUCHED),
        ({}, {"low": Decimal("97.49")}, {}, {}, StrategyReasonCode.PULLBACK_TOO_DEEP),
        ({}, {"close": Decimal(102)}, {}, {}, StrategyReasonCode.EMA_RECLAIM_FAILED),
        ({}, {"open": Decimal(105)}, {}, {}, StrategyReasonCode.CANDLE_DIRECTION_FAILED),
        ({}, {}, {"momentum_4": Decimal(0)}, {}, StrategyReasonCode.MOMENTUM_RECOVERY_FAILED),
        ({}, {}, {"rsi_14": Decimal("49.99")}, {}, StrategyReasonCode.RSI_CONFIRMATION_FAILED),
        (
            {},
            {},
            {"volume_zscore_20": Decimal("-0.01")},
            {},
            StrategyReasonCode.VOLUME_CONFIRMATION_FAILED,
        ),
        ({}, {}, {}, {"latest_confirmed_low": None}, StrategyReasonCode.MISSING_CONFIRMED_LOW),
        (
            {},
            {},
            {},
            {"latest_confirmed_low": pivot(PivotKind.LOW, Decimal(105))},
            StrategyReasonCode.INVALIDATION_WRONG_SIDE,
        ),
        (
            {},
            {},
            {},
            {"latest_confirmed_low": pivot(PivotKind.LOW, Decimal("74.99"))},
            StrategyReasonCode.INVALIDATION_TOO_WIDE,
        ),
    ],
)
def test_long_rule_failures_are_explicit(
    context_change: dict[str, object],
    candle_change: dict[str, Decimal],
    feature_change: dict[str, Decimal],
    structure_change: dict[str, object],
    reason: StrategyReasonCode,
) -> None:
    context = evaluation_context(**context_change)  # type: ignore[arg-type]
    candle = replace(context.candle_15m, **candle_change)  # type: ignore[arg-type]
    feature = replace(
        context.features_15m,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        **feature_change,  # type: ignore[arg-type]
    )
    structure = replace(context.structure_15m, **structure_change)  # type: ignore[arg-type]

    result = evaluate(
        replace(context, candle_15m=candle, features_15m=feature, structure_15m=structure)
    )

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


@pytest.mark.parametrize(
    ("candle_change", "feature_change", "structure_change", "reason"),
    [
        ({"high": Decimal(97)}, {}, {}, StrategyReasonCode.VALUE_ZONE_NOT_TOUCHED),
        ({"high": Decimal("102.51")}, {}, {}, StrategyReasonCode.PULLBACK_TOO_DEEP),
        ({"close": Decimal(98)}, {}, {}, StrategyReasonCode.EMA_RECLAIM_FAILED),
        ({"open": Decimal(95)}, {}, {}, StrategyReasonCode.CANDLE_DIRECTION_FAILED),
        ({}, {"momentum_4": Decimal(0)}, {}, StrategyReasonCode.MOMENTUM_RECOVERY_FAILED),
        ({}, {"rsi_14": Decimal("50.01")}, {}, StrategyReasonCode.RSI_CONFIRMATION_FAILED),
        (
            {},
            {"volume_zscore_20": Decimal("-0.01")},
            {},
            StrategyReasonCode.VOLUME_CONFIRMATION_FAILED,
        ),
        ({}, {}, {"latest_confirmed_high": None}, StrategyReasonCode.MISSING_CONFIRMED_HIGH),
        (
            {},
            {},
            {"latest_confirmed_high": pivot(PivotKind.HIGH, Decimal(95))},
            StrategyReasonCode.INVALIDATION_WRONG_SIDE,
        ),
        (
            {},
            {},
            {"latest_confirmed_high": pivot(PivotKind.HIGH, Decimal("125.01"))},
            StrategyReasonCode.INVALIDATION_TOO_WIDE,
        ),
    ],
)
def test_short_rules_are_exact_mirrors(
    candle_change: dict[str, Decimal],
    feature_change: dict[str, Decimal],
    structure_change: dict[str, object],
    reason: StrategyReasonCode,
) -> None:
    context = evaluation_context(side=CandidateAction.SHORT_CANDIDATE)
    candle = replace(context.candle_15m, **candle_change)  # type: ignore[arg-type]
    feature = replace(context.features_15m, **feature_change)  # type: ignore[arg-type]
    structure = replace(context.structure_15m, **structure_change)  # type: ignore[arg-type]

    result = evaluate(
        replace(context, candle_15m=candle, features_15m=feature, structure_15m=structure)
    )

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


def test_no_trade_reason_order_is_stable_and_complete() -> None:
    context = evaluation_context(ready=False, extreme=True)
    feature = replace(
        context.features_15m,
        is_fully_warmed=False,
        ema_20=None,
        atr_14=None,
        momentum_4=None,
    )

    result = evaluate(replace(context, features_15m=feature))

    assert result.reasons_against[:6] == (
        StrategyReasonCode.CONTEXT_NOT_READY,
        StrategyReasonCode.FEATURES_NOT_FULLY_WARMED,
        StrategyReasonCode.MISSING_EMA_20,
        StrategyReasonCode.MISSING_ATR_14,
        StrategyReasonCode.MISSING_MOMENTUM_4,
        StrategyReasonCode.EXTREME_VOLATILITY,
    )


def test_same_context_is_deterministic_under_different_decimal_contexts() -> None:
    context = evaluation_context()
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = evaluate(context)
        setcontext(Context(prec=50))
        high_precision = evaluate(context)
    finally:
        setcontext(original)

    assert low_precision == high_precision


def test_configuration_defaults_are_exact_decimals_and_reject_float() -> None:
    configuration = TrendPullbackConfiguration()

    assert configuration == TrendPullbackConfiguration(
        entry_timeframe=Timeframe.MINUTES_15,
        value_zone_atr_tolerance=Decimal("0.25"),
        max_invalidation_distance_atr=Decimal("3.0"),
        minimum_volume_zscore=Decimal(0),
        long_min_rsi=Decimal(50),
        short_max_rsi=Decimal(50),
    )
    with pytest.raises(TypeError, match="Decimal"):
        TrendPullbackConfiguration(value_zone_atr_tolerance=0.25)  # type: ignore[arg-type]
