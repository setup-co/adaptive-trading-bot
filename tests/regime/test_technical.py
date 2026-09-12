from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext

import pytest

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureBatch,
    FeatureSnapshot,
    StructureBatch,
    StructureRelation,
    StructureSnapshot,
)
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    EventRiskState,
    LiquidityState,
    RegimeBatchError,
    RegimeLabel,
    RegimeSeriesError,
    TechnicalRegimeConfiguration,
    TechnicalRegimeEngine,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
    validate_technical_regime_snapshot,
)
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("source-a")
SOURCE_B = SourceId("source-b")


def feature_snapshot(
    *,
    source_id: SourceId = SOURCE_A,
    symbol: TradingSymbol = BTC,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    observed_at: datetime = START,
    bars_seen: int = 50,
    ema_20: Decimal | None = Decimal(100),
    ema_50: Decimal | None = Decimal(100),
    momentum_16: Decimal | None = Decimal(0),
    momentum_4: Decimal | None = Decimal(0),
    volume_zscore_20: Decimal | None = Decimal(0),
    realized_volatility_20: Decimal | None = Decimal(1),
    is_fully_warmed: bool = True,
) -> FeatureSnapshot:
    return FeatureSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        candle_open_time=observed_at - timeframe.duration,
        candle_close_time=observed_at,
        bars_seen=bars_seen,
        ema_20=ema_20,
        ema_50=ema_50,
        atr_14=Decimal(1),
        rsi_14=Decimal(50),
        vwap_utc_day=Decimal(100),
        return_1=Decimal(0),
        momentum_4=momentum_4,
        momentum_16=momentum_16,
        volume_zscore_20=volume_zscore_20,
        realized_volatility_20=realized_volatility_20,
        is_fully_warmed=is_fully_warmed,
    )


def structure_snapshot(
    feature: FeatureSnapshot,
    *,
    high_relation: StructureRelation | None = None,
    low_relation: StructureRelation | None = None,
) -> StructureSnapshot:
    return StructureSnapshot(
        source_id=feature.source_id,
        symbol=feature.symbol,
        timeframe=feature.timeframe,
        observed_at=feature.observed_at,
        bars_seen=feature.bars_seen,
        newly_confirmed=(),
        latest_confirmed_high=None,
        latest_confirmed_low=None,
        latest_high_relation=high_relation,
        latest_low_relation=low_relation,
    )


def batches(
    features: tuple[FeatureSnapshot, ...],
    structures: tuple[StructureSnapshot, ...] | None = None,
) -> tuple[FeatureBatch, StructureBatch]:
    observed_at = features[0].observed_at
    structure_items = structures or tuple(structure_snapshot(item) for item in features)
    return (
        FeatureBatch(observed_at, features),
        StructureBatch(observed_at, structure_items, ()),
    )


def classify(
    feature: FeatureSnapshot,
    *,
    high_relation: StructureRelation | None = None,
    low_relation: StructureRelation | None = None,
    minimum_samples: int = 1,
) -> TechnicalRegimeSnapshot:
    engine = TechnicalRegimeEngine(
        TechnicalRegimeConfiguration(minimum_volatility_samples=minimum_samples)
    )
    feature_batch, structure_batch = batches(
        (feature,),
        (structure_snapshot(feature, high_relation=high_relation, low_relation=low_relation),),
    )
    return engine.process(feature_batch, structure_batch).snapshots[0]


def test_authoritative_technical_validator_rederives_all_classification_facts() -> None:
    feature = feature_snapshot(
        ema_20=Decimal(101),
        momentum_16=Decimal(1),
        volume_zscore_20=Decimal(1),
    )
    structure = structure_snapshot(feature)
    snapshot = (
        TechnicalRegimeEngine(TechnicalRegimeConfiguration(minimum_volatility_samples=1))
        .process(*batches((feature,), (structure,)))
        .snapshots[0]
    )

    validate_technical_regime_snapshot(snapshot, feature, structure)

    for field_name, value in (
        ("direction", Direction.DOWN),
        ("regime_label", RegimeLabel.DOWNTREND),
        ("volatility_state", VolatilityState.HIGH),
        ("volume_state", VolumeState.NORMAL),
    ):
        with pytest.raises(RegimeSeriesError, match="inconsistent"):
            validate_technical_regime_snapshot(
                replace(snapshot, **{field_name: value}),  # type: ignore[arg-type]
                feature,
                structure,
            )


@pytest.mark.parametrize(
    ("component", "feature_changes", "high_relation", "low_relation", "expected"),
    [
        (DirectionComponent.EMA_ALIGNMENT, {"ema_20": Decimal(101)}, None, None, 20),
        (DirectionComponent.EMA_ALIGNMENT, {"ema_20": Decimal(99)}, None, None, -20),
        (DirectionComponent.EMA_ALIGNMENT, {}, None, None, 0),
        (DirectionComponent.MOMENTUM_16, {"momentum_16": Decimal(1)}, None, None, 20),
        (DirectionComponent.MOMENTUM_16, {"momentum_16": Decimal(-1)}, None, None, -20),
        (DirectionComponent.MOMENTUM_16, {}, None, None, 0),
        (DirectionComponent.MOMENTUM_4, {"momentum_4": Decimal(1)}, None, None, 20),
        (DirectionComponent.MOMENTUM_4, {"momentum_4": Decimal(-1)}, None, None, -20),
        (DirectionComponent.MOMENTUM_4, {}, None, None, 0),
        (DirectionComponent.CONFIRMED_HIGH, {}, StructureRelation.HH, None, 20),
        (DirectionComponent.CONFIRMED_HIGH, {}, StructureRelation.LH, None, -20),
        (DirectionComponent.CONFIRMED_HIGH, {}, StructureRelation.EQUAL, None, 0),
        (DirectionComponent.CONFIRMED_LOW, {}, None, StructureRelation.HL, 20),
        (DirectionComponent.CONFIRMED_LOW, {}, None, StructureRelation.LL, -20),
        (DirectionComponent.CONFIRMED_LOW, {}, None, StructureRelation.EQUAL, 0),
    ],
)
def test_each_direction_component_has_fixed_positive_negative_and_neutral_behavior(
    component: DirectionComponent,
    feature_changes: dict[str, Decimal],
    high_relation: StructureRelation | None,
    low_relation: StructureRelation | None,
    expected: int,
) -> None:
    feature = feature_snapshot()
    if "ema_20" in feature_changes:
        feature = replace(feature, ema_20=feature_changes["ema_20"])
    if "momentum_16" in feature_changes:
        feature = replace(feature, momentum_16=feature_changes["momentum_16"])
    if "momentum_4" in feature_changes:
        feature = replace(feature, momentum_4=feature_changes["momentum_4"])
    snapshot = classify(
        feature,
        high_relation=high_relation,
        low_relation=low_relation,
    )

    contributions = {item.component: item.contribution for item in snapshot.direction_evidence}
    assert contributions[component] == expected


def mature_classification(
    *,
    ema: int = 0,
    momentum_16: int = 0,
    momentum_4: int = 0,
    high_relation: StructureRelation | None = None,
    low_relation: StructureRelation | None = None,
) -> TechnicalRegimeSnapshot:
    engine = TechnicalRegimeEngine(TechnicalRegimeConfiguration(minimum_volatility_samples=2))
    seed = feature_snapshot(
        observed_at=START,
        bars_seen=49,
        realized_volatility_20=Decimal(2),
        is_fully_warmed=False,
    )
    seed_batches = batches((seed,))
    engine.process(*seed_batches)
    current = feature_snapshot(
        observed_at=START + timedelta(minutes=15),
        bars_seen=50,
        ema_20=Decimal(100 + ema),
        momentum_16=Decimal(momentum_16),
        momentum_4=Decimal(momentum_4),
        realized_volatility_20=Decimal(1),
    )
    current_batches = batches(
        (current,),
        (
            structure_snapshot(
                current,
                high_relation=high_relation,
                low_relation=low_relation,
            ),
        ),
    )
    return engine.process(*current_batches).snapshots[0]


@pytest.mark.parametrize(
    (
        "ema",
        "momentum_16",
        "momentum_4",
        "high_relation",
        "low_relation",
        "score",
        "direction",
        "label",
    ),
    [
        (1, 1, 0, None, None, 40, Direction.UP, RegimeLabel.UPTREND),
        (-1, -1, 0, None, None, -40, Direction.DOWN, RegimeLabel.DOWNTREND),
        (1, 1, 1, None, None, 60, Direction.UP, RegimeLabel.STRONG_UPTREND),
        (-1, -1, -1, None, None, -60, Direction.DOWN, RegimeLabel.STRONG_DOWNTREND),
    ],
)
def test_exact_direction_and_strength_boundaries(
    ema: int,
    momentum_16: int,
    momentum_4: int,
    high_relation: StructureRelation | None,
    low_relation: StructureRelation | None,
    score: int,
    direction: Direction,
    label: RegimeLabel,
) -> None:
    snapshot = mature_classification(
        ema=ema,
        momentum_16=momentum_16,
        momentum_4=momentum_4,
        high_relation=high_relation,
        low_relation=low_relation,
    )

    assert snapshot.direction_score == score
    assert snapshot.trend_strength == abs(score)
    assert snapshot.direction is direction
    assert snapshot.regime_label is label


def test_missing_structure_is_zero_without_score_renormalization() -> None:
    snapshot = mature_classification(momentum_4=1)

    assert snapshot.direction_score == 20
    assert snapshot.trend_strength == 20
    assert snapshot.direction is Direction.NEUTRAL
    assert snapshot.direction_evidence[-2].contribution == 0
    assert snapshot.direction_evidence[-1].contribution == 0


def test_pre_warmup_is_not_a_mature_regime_even_with_available_volatility() -> None:
    snapshot = classify(feature_snapshot(bars_seen=49, is_fully_warmed=False))

    assert snapshot.direction is Direction.UNKNOWN
    assert snapshot.regime_label is RegimeLabel.INSUFFICIENT_DATA
    assert snapshot.is_fully_classified is False


def process_volatility_values(
    values: tuple[Decimal, ...], *, minimum_samples: int, window: int = 100
) -> tuple[TechnicalRegimeSnapshot, ...]:
    engine = TechnicalRegimeEngine(
        TechnicalRegimeConfiguration(
            volatility_window=window,
            minimum_volatility_samples=minimum_samples,
        )
    )
    outputs = []
    for index, value in enumerate(values):
        item = feature_snapshot(
            observed_at=START + index * Timeframe.MINUTES_15.duration,
            bars_seen=50 + index,
            realized_volatility_20=value,
        )
        outputs.append(engine.process(*batches((item,))).snapshots[0])
    return tuple(outputs)


def test_volatility_percentile_waits_for_minimum_and_uses_right_inclusive_ties() -> None:
    outputs = process_volatility_values(
        (Decimal(1), Decimal(2), Decimal(4), Decimal(2)), minimum_samples=4
    )

    assert [item.volatility_percentile for item in outputs[:3]] == [None, None, None]
    assert all(item.volatility_state is VolatilityState.UNKNOWN for item in outputs[:3])
    assert outputs[-1].volatility_percentile == Decimal(75)
    assert outputs[-1].volatility_state is VolatilityState.NORMAL


def test_volatility_history_retains_only_latest_one_hundred_values() -> None:
    values = (Decimal(0),) + (Decimal(100),) * 99 + (Decimal(50),)
    final = process_volatility_values(values, minimum_samples=1)[-1]

    assert final.volatility_percentile == Decimal(1)


@pytest.mark.parametrize(
    ("values", "expected_percentile", "expected_state"),
    [
        ((2, 3, 4, 1), Decimal(25), VolatilityState.LOW),
        ((1, 2, 4, 3), Decimal(75), VolatilityState.NORMAL),
        ((1, 2, 3, 4, 6, 7, 8, 9, 10, 5), Decimal(50), VolatilityState.NORMAL),
        ((1, 2, 3, 4, 5, 6, 7, 9, 10, 8), Decimal(80), VolatilityState.HIGH),
        ((1, 2, 3, 4, 5, 6, 7, 8, 10, 9), Decimal(90), VolatilityState.EXTREME),
    ],
)
def test_volatility_state_thresholds_are_exact(
    values: tuple[int, ...],
    expected_percentile: Decimal,
    expected_state: VolatilityState,
) -> None:
    final = process_volatility_values(
        tuple(Decimal(value) for value in values), minimum_samples=len(values)
    )[-1]

    assert final.volatility_percentile == expected_percentile
    assert final.volatility_state is expected_state


@pytest.mark.parametrize(
    ("zscore", "expected"),
    [
        (None, VolumeState.UNKNOWN),
        (Decimal("-1.01"), VolumeState.LOW),
        (Decimal(-1), VolumeState.LOW),
        (Decimal("-0.99"), VolumeState.NORMAL),
        (Decimal("0.99"), VolumeState.NORMAL),
        (Decimal(1), VolumeState.HIGH),
        (Decimal("1.01"), VolumeState.HIGH),
    ],
)
def test_volume_thresholds_do_not_infer_liquidity_or_event_risk(
    zscore: Decimal | None, expected: VolumeState
) -> None:
    snapshot = classify(
        feature_snapshot(
            volume_zscore_20=zscore,
            realized_volatility_20=Decimal(999),
        )
    )

    assert snapshot.volume_state is expected
    assert snapshot.liquidity_state is LiquidityState.UNKNOWN
    assert snapshot.event_risk_state is EventRiskState.UNKNOWN


def test_neutral_and_extreme_volatile_uncertain_label_priority() -> None:
    neutral = mature_classification()
    extreme = classify(feature_snapshot(momentum_4=Decimal(1), realized_volatility_20=Decimal(10)))

    assert neutral.regime_label is RegimeLabel.NEUTRAL
    assert extreme.direction_score == 20
    assert extreme.volatility_state is VolatilityState.EXTREME
    assert extreme.regime_label is RegimeLabel.VOLATILE_UNCERTAIN


def test_source_symbol_and_timeframe_histories_are_independent_and_canonical() -> None:
    identities = (
        (SOURCE_B, BTC, Timeframe.MINUTES_15, Decimal(20), Decimal(10)),
        (SOURCE_A, ETH, Timeframe.MINUTES_15, Decimal(30), Decimal(40)),
        (SOURCE_A, BTC, Timeframe.HOURS_4, Decimal(50), Decimal(25)),
        (SOURCE_A, BTC, Timeframe.HOUR_1, Decimal(60), Decimal(60)),
        (SOURCE_A, BTC, Timeframe.MINUTES_15, Decimal(70), Decimal(80)),
    )
    engine = TechnicalRegimeEngine(TechnicalRegimeConfiguration(minimum_volatility_samples=2))
    first = tuple(
        feature_snapshot(
            source_id=source,
            symbol=symbol,
            timeframe=timeframe,
            observed_at=START,
            bars_seen=1,
            realized_volatility_20=prior,
            is_fully_warmed=False,
        )
        for source, symbol, timeframe, prior, _current in identities
    )
    engine.process(*batches(first))
    second = tuple(
        feature_snapshot(
            source_id=source,
            symbol=symbol,
            timeframe=timeframe,
            observed_at=START + timedelta(hours=4),
            bars_seen=2,
            realized_volatility_20=current,
            is_fully_warmed=True,
        )
        for source, symbol, timeframe, _prior, current in identities
    )

    result = engine.process(*batches(second))

    assert [(item.source_id, item.symbol, item.timeframe) for item in result.snapshots] == [
        (SOURCE_A, BTC, Timeframe.MINUTES_15),
        (SOURCE_A, BTC, Timeframe.HOUR_1),
        (SOURCE_A, BTC, Timeframe.HOURS_4),
        (SOURCE_A, ETH, Timeframe.MINUTES_15),
        (SOURCE_B, BTC, Timeframe.MINUTES_15),
    ]
    assert [item.volatility_percentile for item in result.snapshots] == [
        Decimal(100),
        Decimal(100),
        Decimal(50),
        Decimal(100),
        Decimal(50),
    ]


def test_mismatched_series_sets_observation_times_and_bars_fail_closed() -> None:
    btc = feature_snapshot()
    eth = feature_snapshot(symbol=ETH)
    feature_batch, structure_batch = batches((btc,))

    with pytest.raises(RegimeBatchError, match="series sets"):
        TechnicalRegimeEngine().process(FeatureBatch(START, (btc, eth)), structure_batch)
    with pytest.raises(RegimeBatchError, match="observed_at"):
        TechnicalRegimeEngine().process(
            feature_batch,
            replace(structure_batch, observed_at=START + timedelta(minutes=15)),
        )
    with pytest.raises(RegimeSeriesError, match="bars_seen mismatch"):
        TechnicalRegimeEngine().process(
            feature_batch,
            replace(
                structure_batch,
                snapshots=(replace(structure_batch.snapshots[0], bars_seen=49),),
            ),
        )
    mismatched_snapshot_time = replace(
        structure_batch,
        snapshots=(
            replace(
                structure_batch.snapshots[0],
                observed_at=START + timedelta(minutes=15),
            ),
        ),
    )
    with pytest.raises(RegimeBatchError, match="snapshot observed_at"):
        TechnicalRegimeEngine().process(feature_batch, mismatched_snapshot_time)


def test_invalid_multi_series_batch_causes_zero_partial_mutation() -> None:
    engine = TechnicalRegimeEngine()
    btc = feature_snapshot(bars_seen=1, is_fully_warmed=False)
    engine.process(*batches((btc,)))
    later = START + timedelta(minutes=15)
    btc_next = replace(btc, observed_at=later, bars_seen=2)
    eth_bad = feature_snapshot(
        symbol=ETH,
        observed_at=later,
        bars_seen=1,
        is_fully_warmed=False,
    )
    structures = (
        structure_snapshot(btc_next),
        replace(structure_snapshot(eth_bad), bars_seen=2),
    )

    with pytest.raises(RegimeSeriesError, match="bars_seen mismatch"):
        engine.process(*batches((btc_next, eth_bad), structures))

    result = engine.process(*batches((btc_next,)))
    assert result.snapshots[0].bars_seen == 2


def test_duplicate_backward_and_skipped_series_progression_are_rejected() -> None:
    first = feature_snapshot(bars_seen=10)
    engine = TechnicalRegimeEngine()
    engine.process(*batches((first,)))

    with pytest.raises(RegimeSeriesError, match="strictly"):
        engine.process(*batches((first,)))
    skipped = replace(
        first,
        observed_at=START + timedelta(minutes=15),
        bars_seen=12,
    )
    with pytest.raises(RegimeSeriesError, match="advance by one"):
        engine.process(*batches((skipped,)))


def test_repeated_stream_is_deterministic_and_ambient_decimal_context_independent() -> None:
    values = tuple(Decimal(index % 7 + 1) for index in range(25))

    def run(precision: int) -> tuple[TechnicalRegimeSnapshot, ...]:
        setcontext(Context(prec=precision))
        return process_volatility_values(values, minimum_samples=20)

    original = getcontext().copy()
    try:
        low_precision = run(6)
        high_precision = run(50)
    finally:
        setcontext(original)

    assert low_precision == high_precision
