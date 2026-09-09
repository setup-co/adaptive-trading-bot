from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.regime import (
    CompositionBatchError,
    CompositionSeriesError,
    Direction,
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    HigherTimeframeBias,
    LiquidityState,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeComposer,
    MultiTimeframeRegimeSnapshot,
    RegimeLabel,
    TechnicalRegimeBatch,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("source-a")
SOURCE_B = SourceId("source-b")


def regime(
    timeframe: Timeframe,
    *,
    observed_at: datetime = START,
    source_id: SourceId = SOURCE_A,
    symbol: TradingSymbol = BTC,
    bars_seen: int = 50,
    direction: Direction = Direction.NEUTRAL,
    classified: bool = True,
    volatility: VolatilityState = VolatilityState.NORMAL,
) -> TechnicalRegimeSnapshot:
    score = {
        Direction.UP: 40,
        Direction.DOWN: -40,
        Direction.NEUTRAL: 0,
        Direction.UNKNOWN: 0,
    }[direction]
    contributions = {
        40: (20, 20, 0, 0, 0),
        -40: (-20, -20, 0, 0, 0),
        0: (0, 0, 0, 0, 0),
    }[score]
    evidence = tuple(
        DirectionEvidence(component, contribution, "test evidence")
        for component, contribution in zip(DirectionComponent, contributions, strict=True)
    )
    return TechnicalRegimeSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        bars_seen=bars_seen,
        direction=direction,
        direction_score=score,
        trend_strength=abs(score),
        direction_evidence=evidence,
        volatility_percentile=Decimal(50) if classified else None,
        volatility_state=volatility if classified else VolatilityState.UNKNOWN,
        volume_state=VolumeState.NORMAL,
        liquidity_state=LiquidityState.UNKNOWN,
        event_risk_state=EventRiskState.UNKNOWN,
        regime_label=RegimeLabel.NEUTRAL if classified else RegimeLabel.INSUFFICIENT_DATA,
        is_fully_classified=classified,
    )


def batch(*snapshots: TechnicalRegimeSnapshot) -> TechnicalRegimeBatch:
    return TechnicalRegimeBatch(snapshots[0].observed_at, snapshots)


def ready_context(
    entry_direction: Direction,
    one_hour_direction: Direction,
    four_hour_direction: Direction,
) -> MultiTimeframeRegimeSnapshot:
    composer = MultiTimeframeRegimeComposer()
    result = composer.process(
        batch(
            regime(Timeframe.HOURS_4, direction=four_hour_direction),
            regime(Timeframe.MINUTES_15, direction=entry_direction),
            regime(Timeframe.HOUR_1, direction=one_hour_direction),
        )
    )
    return result.snapshots[0]


def test_missing_one_hour_is_not_ready_with_unknown_bias() -> None:
    context = (
        MultiTimeframeRegimeComposer()
        .process(
            batch(
                regime(Timeframe.MINUTES_15),
                regime(Timeframe.HOURS_4),
            )
        )
        .snapshots[0]
    )

    assert context.regime_1h is None
    assert context.age_1h is None
    assert context.is_1h_fresh is False
    assert context.is_fully_ready is False
    assert context.higher_timeframe_bias is HigherTimeframeBias.UNKNOWN
    assert context.alignment is MultiTimeframeAlignment.INSUFFICIENT_DATA
    assert context.readiness_reasons == ("no observable 1h regime",)


def test_missing_four_hour_is_not_ready() -> None:
    context = (
        MultiTimeframeRegimeComposer()
        .process(
            batch(
                regime(Timeframe.MINUTES_15),
                regime(Timeframe.HOUR_1),
            )
        )
        .snapshots[0]
    )

    assert context.regime_4h is None
    assert context.is_4h_fresh is False
    assert context.is_fully_ready is False
    assert context.readiness_reasons == ("no observable 4h regime",)


@pytest.mark.parametrize("unclassified_timeframe", tuple(Timeframe))
def test_any_unclassified_timeframe_keeps_context_not_ready(
    unclassified_timeframe: Timeframe,
) -> None:
    items = tuple(
        regime(timeframe, classified=timeframe is not unclassified_timeframe)
        for timeframe in Timeframe
    )
    context = MultiTimeframeRegimeComposer().process(batch(*items)).snapshots[0]

    assert context.is_fully_ready is False
    expected_bias = (
        HigherTimeframeBias.NEUTRAL
        if unclassified_timeframe is Timeframe.MINUTES_15
        else HigherTimeframeBias.UNKNOWN
    )
    assert context.higher_timeframe_bias is expected_bias
    assert context.alignment is MultiTimeframeAlignment.INSUFFICIENT_DATA


def test_same_timestamp_one_and_four_hour_updates_are_used_immediately() -> None:
    one_hour = regime(Timeframe.HOUR_1, direction=Direction.UP)
    four_hour = regime(Timeframe.HOURS_4, direction=Direction.UP)
    context = (
        MultiTimeframeRegimeComposer()
        .process(
            batch(
                regime(Timeframe.MINUTES_15, direction=Direction.UP),
                four_hour,
                one_hour,
            )
        )
        .snapshots[0]
    )

    assert context.regime_1h is one_hour
    assert context.regime_4h is four_hour
    assert context.age_1h == context.age_4h == timedelta()
    assert context.is_1h_fresh is context.is_4h_fresh is True
    assert context.is_fully_ready is True
    assert context.alignment is MultiTimeframeAlignment.ALIGNED_UP


def test_older_fresh_higher_timeframes_are_reused_as_of_anchor() -> None:
    composer = MultiTimeframeRegimeComposer()
    one_hour = regime(Timeframe.HOUR_1)
    four_hour = regime(Timeframe.HOURS_4)
    assert composer.process(batch(one_hour, four_hour)).snapshots == ()
    anchor_at = START + timedelta(minutes=45)

    context = composer.process(
        batch(regime(Timeframe.MINUTES_15, observed_at=anchor_at))
    ).snapshots[0]

    assert context.regime_1h is one_hour
    assert context.regime_4h is four_hour
    assert context.age_1h == context.age_4h == timedelta(minutes=45)
    assert context.is_1h_fresh is context.is_4h_fresh is True
    assert context.is_fully_ready is True


@pytest.mark.parametrize(
    ("timeframe", "age", "expected_fresh"),
    [
        (Timeframe.HOUR_1, timedelta(minutes=59, seconds=59), True),
        (Timeframe.HOUR_1, timedelta(hours=1), False),
        (Timeframe.HOURS_4, timedelta(hours=3, minutes=59, seconds=59), True),
        (Timeframe.HOURS_4, timedelta(hours=4), False),
    ],
)
def test_higher_timeframe_freshness_boundary_is_strict(
    timeframe: Timeframe, age: timedelta, expected_fresh: bool
) -> None:
    composer = MultiTimeframeRegimeComposer()
    composer.process(batch(regime(timeframe)))
    anchor = regime(Timeframe.MINUTES_15, observed_at=START + age)
    context = composer.process(batch(anchor)).snapshots[0]

    actual = context.is_1h_fresh if timeframe is Timeframe.HOUR_1 else context.is_4h_fresh
    assert actual is expected_fresh


def test_future_higher_timeframe_snapshot_is_never_selected() -> None:
    composer = MultiTimeframeRegimeComposer()
    observable = regime(Timeframe.HOUR_1, bars_seen=49)
    composer.process(batch(observable))
    future = regime(
        Timeframe.HOUR_1,
        observed_at=START + timedelta(hours=2),
        bars_seen=50,
    )
    composer.process(batch(future))

    context = composer.process(
        batch(regime(Timeframe.MINUTES_15, observed_at=START + timedelta(hours=1)))
    ).snapshots[0]

    assert context.regime_1h is observable
    assert context.age_1h == timedelta(hours=1)
    assert context.is_1h_fresh is False


@pytest.mark.parametrize(
    ("one_hour", "four_hour", "expected"),
    [
        (Direction.UP, Direction.UP, HigherTimeframeBias.UP),
        (Direction.DOWN, Direction.DOWN, HigherTimeframeBias.DOWN),
        (Direction.NEUTRAL, Direction.NEUTRAL, HigherTimeframeBias.NEUTRAL),
        (Direction.UP, Direction.DOWN, HigherTimeframeBias.MIXED),
        (Direction.UP, Direction.NEUTRAL, HigherTimeframeBias.MIXED),
    ],
)
def test_higher_timeframe_bias_has_no_hidden_weighting(
    one_hour: Direction,
    four_hour: Direction,
    expected: HigherTimeframeBias,
) -> None:
    assert ready_context(Direction.NEUTRAL, one_hour, four_hour).higher_timeframe_bias is expected


@pytest.mark.parametrize(
    ("entry", "one_hour", "four_hour", "expected"),
    [
        (Direction.UP, Direction.UP, Direction.UP, MultiTimeframeAlignment.ALIGNED_UP),
        (
            Direction.DOWN,
            Direction.DOWN,
            Direction.DOWN,
            MultiTimeframeAlignment.ALIGNED_DOWN,
        ),
        (
            Direction.DOWN,
            Direction.UP,
            Direction.UP,
            MultiTimeframeAlignment.HTF_UP_ENTRY_DIVERGENT,
        ),
        (
            Direction.UP,
            Direction.DOWN,
            Direction.DOWN,
            MultiTimeframeAlignment.HTF_DOWN_ENTRY_DIVERGENT,
        ),
        (
            Direction.NEUTRAL,
            Direction.NEUTRAL,
            Direction.NEUTRAL,
            MultiTimeframeAlignment.NEUTRAL,
        ),
        (Direction.UP, Direction.UP, Direction.DOWN, MultiTimeframeAlignment.MIXED),
    ],
)
def test_alignment_rules_are_descriptive_and_exact(
    entry: Direction,
    one_hour: Direction,
    four_hour: Direction,
    expected: MultiTimeframeAlignment,
) -> None:
    assert ready_context(entry, one_hour, four_hour).alignment is expected


def test_extreme_volatility_list_is_exact_canonical_and_does_not_infer_context() -> None:
    context = (
        MultiTimeframeRegimeComposer()
        .process(
            batch(
                regime(Timeframe.HOURS_4, volatility=VolatilityState.EXTREME),
                regime(Timeframe.HOUR_1, volatility=VolatilityState.HIGH),
                regime(Timeframe.MINUTES_15, volatility=VolatilityState.EXTREME),
            )
        )
        .snapshots[0]
    )

    assert context.has_extreme_volatility is True
    assert context.extreme_volatility_timeframes == (
        Timeframe.MINUTES_15,
        Timeframe.HOURS_4,
    )
    assert context.regime_15m.liquidity_state is LiquidityState.UNKNOWN
    assert context.regime_1h is not None
    assert context.regime_1h.event_risk_state is EventRiskState.UNKNOWN


def test_source_and_symbol_contexts_are_independent_without_htf_substitution() -> None:
    composer = MultiTimeframeRegimeComposer()
    result = composer.process(
        batch(
            regime(Timeframe.MINUTES_15, source_id=SOURCE_B),
            regime(Timeframe.MINUTES_15, symbol=ETH),
            regime(Timeframe.HOUR_1),
            regime(Timeframe.HOURS_4),
            regime(Timeframe.MINUTES_15),
        )
    )

    assert [(item.source_id, item.symbol) for item in result.snapshots] == [
        (SOURCE_A, BTC),
        (SOURCE_A, ETH),
        (SOURCE_B, BTC),
    ]
    assert result.snapshots[0].is_fully_ready is True
    assert result.snapshots[1].regime_1h is None
    assert result.snapshots[2].regime_4h is None


def test_higher_timeframe_only_batch_updates_state_without_emitting_context() -> None:
    composer = MultiTimeframeRegimeComposer()
    one_hour = regime(Timeframe.HOUR_1)

    result = composer.process(batch(one_hour))
    later = composer.process(
        batch(regime(Timeframe.MINUTES_15, observed_at=START + timedelta(minutes=15)))
    )

    assert result.snapshots == ()
    assert later.snapshots[0].regime_1h is one_hour


def test_duplicate_invalid_multi_series_batch_does_not_partially_advance_state() -> None:
    composer = MultiTimeframeRegimeComposer()
    first = regime(Timeframe.HOUR_1, bars_seen=1)
    composer.process(batch(first))
    later = replace(first, observed_at=START + timedelta(hours=1), bars_seen=2)

    with pytest.raises(CompositionBatchError, match="duplicate"):
        composer.process(batch(later, later))

    assert composer.process(batch(later)).snapshots == ()


def test_bad_eth_snapshot_does_not_advance_valid_btc_update() -> None:
    composer = MultiTimeframeRegimeComposer()
    first = regime(Timeframe.HOUR_1, bars_seen=1)
    composer.process(batch(first))
    observed_at = START + timedelta(hours=1)
    btc_next = replace(first, observed_at=observed_at, bars_seen=2)
    eth_bad = replace(
        regime(
            Timeframe.MINUTES_15,
            observed_at=observed_at,
            symbol=ETH,
            bars_seen=1,
        ),
        direction_score=20,
    )

    with pytest.raises(CompositionSeriesError, match="trend_strength"):
        composer.process(batch(btc_next, eth_bad))

    assert composer.process(batch(btc_next)).snapshots == ()


def test_backward_and_skipped_series_progression_are_rejected() -> None:
    first = regime(Timeframe.HOUR_1, bars_seen=10)
    composer = MultiTimeframeRegimeComposer()
    composer.process(batch(first))

    with pytest.raises(CompositionSeriesError, match="strictly"):
        composer.process(batch(first))
    skipped = replace(first, observed_at=START + timedelta(hours=1), bars_seen=12)
    with pytest.raises(CompositionSeriesError, match="advance by one"):
        composer.process(batch(skipped))


def test_invalid_snapshot_and_batch_timestamp_fail_closed() -> None:
    snapshot = regime(Timeframe.MINUTES_15)
    malformed = replace(snapshot, trend_strength=20)
    with pytest.raises(CompositionSeriesError, match="trend_strength"):
        MultiTimeframeRegimeComposer().process(batch(malformed))
    with pytest.raises(CompositionBatchError, match="observed_at"):
        MultiTimeframeRegimeComposer().process(
            TechnicalRegimeBatch(START + timedelta(minutes=15), (snapshot,))
        )
    outside_v1 = regime(Timeframe.MINUTES_15, symbol=TradingSymbol("SOL", "USDT"))
    with pytest.raises(CompositionSeriesError, match="outside V1"):
        MultiTimeframeRegimeComposer().process(batch(outside_v1))


def test_repeated_stream_produces_identical_canonical_contexts() -> None:
    stream = (
        batch(regime(Timeframe.HOUR_1), regime(Timeframe.HOURS_4)),
        batch(
            regime(
                Timeframe.MINUTES_15,
                observed_at=START + timedelta(minutes=15),
                source_id=SOURCE_B,
            ),
            regime(
                Timeframe.MINUTES_15,
                observed_at=START + timedelta(minutes=15),
            ),
        ),
    )

    def run() -> tuple[MultiTimeframeRegimeSnapshot, ...]:
        composer = MultiTimeframeRegimeComposer()
        return tuple(context for item in stream for context in composer.process(item).snapshots)

    first = run()
    second = run()
    assert first == second
    assert [item.source_id for item in first] == [SOURCE_A, SOURCE_B]
