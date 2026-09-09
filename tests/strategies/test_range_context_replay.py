from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    RangeContextSnapshot,
    RangeStatus,
    ValidatedRangeDetector,
    assemble_strategy_contexts,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")
REPLAY_COUNTS = {
    Timeframe.MINUTES_15: 819,
    Timeframe.HOUR_1: 205,
    Timeframe.HOURS_4: 52,
}


def _candle(index: int, timeframe: Timeframe, *, breakout: bool = False) -> Candle:
    open_time = START + index * timeframe.duration
    amplitude = Decimal(2) if index < REPLAY_COUNTS[timeframe] - 40 else Decimal("0.1")
    close = Decimal(100) + Decimal(index % 4) * amplitude
    high = Decimal(108)
    low = Decimal(92)
    if timeframe is Timeframe.MINUTES_15:
        if 740 <= index < 760:
            close = Decimal(50) if index % 2 == 0 else Decimal(150)
            high = Decimal(151)
            low = Decimal(49)
        if index in {802, 810}:
            low = Decimal(80)
        if index in {806, 814}:
            high = Decimal(120)
        if breakout and index == 817:
            close = Decimal(123)
            high = Decimal(124)
    return Candle(
        SYMBOL,
        timeframe,
        open_time,
        open_time + timeframe.duration,
        close,
        high,
        low,
        close,
        Decimal(10 + index % 5),
    )


def _run(
    path: Path, *, extra_candles: int = 0, breakout: bool = False
) -> tuple[RangeContextSnapshot, ...]:
    store = ParquetHistoricalCandleStore(path)
    store.merge(
        SOURCE,
        tuple(
            _candle(index, timeframe, breakout=breakout)
            for timeframe, count in REPLAY_COUNTS.items()
            for index in range(count + extra_candles)
        ),
    )
    series = tuple(
        ReplaySeries(
            SOURCE,
            SYMBOL,
            timeframe,
            START,
            START + count * timeframe.duration,
        )
        for timeframe, count in REPLAY_COUNTS.items()
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(series, clock=FrozenClock(START))
    feature_engine = FeatureEngine()
    structure_engine = MarketStructureEngine()
    regime_engine = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    detector = ValidatedRangeDetector()
    outputs: list[RangeContextSnapshot] = []
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        features = feature_engine.process(observed)
        structures = structure_engine.process(observed)
        technical = regime_engine.process(features, structures)
        regimes = composer.process(technical)
        contexts = assemble_strategy_contexts(observed, features, structures, regimes)
        outputs.extend(detector.process(context) for context in contexts)
    return tuple(outputs)


def test_parquet_replay_range_detection_is_causal_bounded_and_reproducible(
    tmp_path: Path,
) -> None:
    bounded = _run(tmp_path / "bounded")
    with_future = _run(tmp_path / "future", extra_candles=4)
    repeated = _run(tmp_path / "repeated")

    assert bounded == with_future == repeated
    assert len(bounded) == REPLAY_COUNTS[Timeframe.MINUTES_15]
    valid_indexes = tuple(
        index
        for index, snapshot in enumerate(bounded)
        if snapshot.status is RangeStatus.VALID_RANGE
    )
    assert valid_indexes
    first = bounded[valid_indexes[0]]
    assert valid_indexes[0] == 816
    assert all(not snapshot.is_valid_range for snapshot in bounded[:816])
    assert tuple(item.pivot.confirmed_at for item in first.qualifying_pivots) == (
        START + 805 * Timeframe.MINUTES_15.duration,
        START + 809 * Timeframe.MINUTES_15.duration,
        START + 813 * Timeframe.MINUTES_15.duration,
        START + 817 * Timeframe.MINUTES_15.duration,
    )
    assert all(item.pivot.confirmed_at <= first.observed_at for item in first.qualifying_pivots)
    assert not hasattr(first, "action")


def test_parquet_replay_close_breakout_clears_old_range(tmp_path: Path) -> None:
    outputs = _run(tmp_path / "breakout", breakout=True)

    assert outputs[816].status is RangeStatus.VALID_RANGE
    assert outputs[817].status is RangeStatus.RANGE_BROKEN
    assert outputs[818].status is RangeStatus.INSUFFICIENT_DATA
    assert outputs[818].lower_boundary is None
    assert outputs[818].upper_boundary is None
