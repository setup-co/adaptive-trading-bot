from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureSeries,
    MarketStructureEngine,
    ObservedCandle,
    ObservedCandleBatch,
    PivotConfiguration,
    PivotKind,
    StructureBatch,
    StructureBatchError,
    StructureRelation,
    StructureSeriesError,
)
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("source-a")
SOURCE_B = SourceId("source-b")


def candle(
    index: int,
    *,
    high: int = 60,
    low: int = 40,
    symbol: TradingSymbol = BTC,
    timeframe: Timeframe = Timeframe.MINUTES_15,
) -> Candle:
    open_time = START + index * timeframe.duration
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=Decimal(50),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(50),
        volume=Decimal(1),
    )


def event(item: Candle, source_id: SourceId = SOURCE_A) -> ObservedCandle:
    return ObservedCandle(
        FeatureSeries(source_id, item.symbol, item.timeframe),
        item,
    )


def apply(
    engine: MarketStructureEngine, item: Candle, source_id: SourceId = SOURCE_A
) -> StructureBatch:
    return engine.process(ObservedCandleBatch(item.close_time, (event(item, source_id),)))


def stream(
    *,
    highs: tuple[int, ...],
    lows: tuple[int, ...] | None = None,
    engine: MarketStructureEngine | None = None,
    symbol: TradingSymbol = BTC,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    source_id: SourceId = SOURCE_A,
) -> tuple[StructureBatch, ...]:
    detector = engine or MarketStructureEngine()
    candle_lows = lows or (40,) * len(highs)
    return tuple(
        apply(
            detector,
            candle(index, high=high, low=low, symbol=symbol, timeframe=timeframe),
            source_id,
        )
        for index, (high, low) in enumerate(zip(highs, candle_lows, strict=True))
    )


@pytest.mark.parametrize(
    ("left_bars", "right_bars", "error"),
    [(0, 2, ValueError), (2, 0, ValueError), (True, 2, TypeError), (2, 1.5, TypeError)],
)
def test_pivot_configuration_rejects_invalid_widths(
    left_bars: object, right_bars: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        PivotConfiguration(left_bars=left_bars, right_bars=right_bars)  # type: ignore[arg-type]


def test_swing_high_is_emitted_only_at_second_right_close() -> None:
    outputs = stream(highs=(51, 52, 60, 52, 51))

    assert all(not output.newly_confirmed_pivots for output in outputs[:4])
    pivot = outputs[4].newly_confirmed_pivots[0]
    assert pivot.kind is PivotKind.HIGH
    assert pivot.pivot_open_time == candle(2).open_time
    assert pivot.pivot_close_time == candle(2).close_time
    assert pivot.pivot_price == Decimal(60)
    assert pivot.confirmed_at == candle(4).close_time
    assert pivot.confirmed_at > pivot.pivot_close_time
    assert pivot.left_bars == pivot.right_bars == 2
    assert pivot.pivot_bar_number == 3
    assert pivot.relation is None


def test_swing_low_is_emitted_only_at_second_right_close() -> None:
    outputs = stream(
        highs=(60,) * 5,
        lows=(49, 48, 40, 48, 49),
    )

    pivot = outputs[-1].newly_confirmed_pivots[0]
    assert pivot.kind is PivotKind.LOW
    assert pivot.pivot_open_time == candle(2).open_time
    assert pivot.pivot_price == Decimal(40)
    assert pivot.confirmed_at == outputs[-1].observed_at
    assert pivot.relation is None


@pytest.mark.parametrize(
    ("highs", "lows"),
    [
        ((51, 60, 60, 52, 51), (40,) * 5),
        ((60,) * 5, (49, 40, 40, 48, 49)),
    ],
)
def test_strict_neighbor_ties_do_not_create_pivots(
    highs: tuple[int, ...], lows: tuple[int, ...]
) -> None:
    assert not stream(highs=highs, lows=lows)[-1].newly_confirmed_pivots


@pytest.mark.parametrize(
    ("second", "expected"),
    [(65, StructureRelation.HH), (55, StructureRelation.LH), (60, StructureRelation.EQUAL)],
)
def test_confirmed_high_relations_compare_only_to_prior_confirmed_high(
    second: int, expected: StructureRelation
) -> None:
    outputs = stream(highs=(51, 52, 60, 52, 51, 52, second, 52, 51))
    pivots = tuple(pivot for output in outputs for pivot in output.newly_confirmed_pivots)

    assert tuple(pivot.relation for pivot in pivots) == (None, expected)
    assert outputs[-1].snapshots[0].latest_high_relation is expected


@pytest.mark.parametrize(
    ("second", "expected"),
    [(45, StructureRelation.HL), (35, StructureRelation.LL), (40, StructureRelation.EQUAL)],
)
def test_confirmed_low_relations_compare_only_to_prior_confirmed_low(
    second: int, expected: StructureRelation
) -> None:
    outputs = stream(
        highs=(60,) * 9,
        lows=(49, 48, 40, 48, 49, 48, second, 48, 49),
    )
    pivots = tuple(pivot for output in outputs for pivot in output.newly_confirmed_pivots)

    assert tuple(pivot.relation for pivot in pivots) == (None, expected)
    assert outputs[-1].snapshots[0].latest_low_relation is expected


def test_same_candle_dual_pivot_emits_high_then_low() -> None:
    output = stream(
        highs=(55, 54, 60, 54, 55),
        lows=(45, 46, 40, 46, 45),
    )[-1]

    assert tuple(pivot.kind for pivot in output.newly_confirmed_pivots) == (
        PivotKind.HIGH,
        PivotKind.LOW,
    )
    assert output.snapshots[0].latest_confirmed_high is output.newly_confirmed_pivots[0]
    assert output.snapshots[0].latest_confirmed_low is output.newly_confirmed_pivots[1]


def test_final_candidates_remain_unconfirmed_without_right_context() -> None:
    outputs = stream(highs=(51, 52, 53, 54, 60, 50))

    assert not any(output.newly_confirmed_pivots for output in outputs)


def test_source_symbol_and_timeframe_states_are_independent() -> None:
    detector = MarketStructureEngine()
    highs = (51, 52, 60, 52, 51)
    results: list[StructureBatch] = []
    for symbol, timeframe, source_id in (
        (BTC, Timeframe.MINUTES_15, SOURCE_A),
        (ETH, Timeframe.MINUTES_15, SOURCE_A),
        (BTC, Timeframe.HOUR_1, SOURCE_A),
        (BTC, Timeframe.HOURS_4, SOURCE_A),
        (BTC, Timeframe.MINUTES_15, SOURCE_B),
    ):
        results.extend(
            stream(
                highs=highs,
                engine=detector,
                symbol=symbol,
                timeframe=timeframe,
                source_id=source_id,
            )
        )

    pivots = tuple(pivot for result in results for pivot in result.newly_confirmed_pivots)
    assert len(pivots) == 5
    assert all(pivot.relation is None for pivot in pivots)
    assert len({(pivot.source_id, pivot.symbol, pivot.timeframe) for pivot in pivots}) == 5


def test_same_time_multi_series_and_pivots_use_canonical_order() -> None:
    detector = MarketStructureEngine()
    highs = (51, 52, 60, 52, 51)
    output: StructureBatch | None = None
    for index, high in enumerate(highs):
        item = candle(index, high=high)
        output = detector.process(
            ObservedCandleBatch(
                item.close_time,
                (event(item, SOURCE_B), event(item, SOURCE_A)),
            )
        )

    assert output is not None
    assert tuple(snapshot.source_id for snapshot in output.snapshots) == (SOURCE_A, SOURCE_B)
    assert tuple(pivot.source_id for pivot in output.newly_confirmed_pivots) == (
        SOURCE_A,
        SOURCE_B,
    )


@pytest.mark.parametrize("kind", ["duplicate", "backward", "gap"])
def test_chronology_errors_fail_closed_without_advancing(kind: str) -> None:
    detector = MarketStructureEngine()
    apply(detector, candle(2, high=51))
    invalid = {
        "duplicate": candle(2, high=51),
        "backward": candle(1, high=51),
        "gap": candle(4, high=51),
    }[kind]

    with pytest.raises(StructureSeriesError):
        apply(detector, invalid)

    assert apply(detector, candle(3, high=52)).snapshots[0].bars_seen == 2


def test_invalid_multi_event_batch_causes_zero_partial_mutation() -> None:
    detector = MarketStructureEngine()
    first = candle(0, high=51)
    apply(detector, first)
    valid = candle(1, high=52)
    mismatched = ObservedCandle(
        FeatureSeries(SOURCE_B, ETH, Timeframe.MINUTES_15),
        valid,
    )

    with pytest.raises(StructureSeriesError, match="identity"):
        detector.process(ObservedCandleBatch(valid.close_time, (event(valid), mismatched)))

    assert apply(detector, valid).snapshots[0].bars_seen == 2


def test_batch_rejects_wrong_close_and_duplicate_series() -> None:
    detector = MarketStructureEngine()
    item = candle(0)
    observed = event(item)
    with pytest.raises(StructureBatchError, match="close"):
        detector.process(ObservedCandleBatch(item.close_time + timedelta(minutes=15), (observed,)))
    with pytest.raises(StructureBatchError, match="duplicate"):
        detector.process(ObservedCandleBatch(item.close_time, (observed, observed)))


def test_confirmed_pivot_rejects_backdated_confirmation() -> None:
    item = candle(0)
    with pytest.raises(ValueError, match="after"):
        ConfirmedPivot(
            SOURCE_A,
            BTC,
            Timeframe.MINUTES_15,
            PivotKind.HIGH,
            item.open_time,
            item.close_time,
            item.high,
            item.close_time,
            2,
            2,
            1,
            None,
        )


def test_repeated_stream_is_deterministic() -> None:
    highs = (51, 52, 60, 52, 51, 52, 65, 52, 51)

    assert stream(highs=highs) == stream(highs=highs)
