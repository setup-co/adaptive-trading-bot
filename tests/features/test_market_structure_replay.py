from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    PivotKind,
    StructureBatch,
    observed_candles_from_replay,
)
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")
HIGHS = (51, 52, 60, 52, 51, 50, 50)


def candle(index: int) -> Candle:
    open_time = START + index * Timeframe.MINUTES_15.duration
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=Decimal(50),
        high=Decimal(HIGHS[index]),
        low=Decimal(40),
        close=Decimal(50),
        volume=Decimal(1),
    )


def replay_structure(
    path: Path, *, stored_count: int, replay_count: int
) -> tuple[StructureBatch, ...]:
    store = ParquetHistoricalCandleStore(path)
    store.merge(SOURCE, tuple(candle(index) for index in range(stored_count)))
    series = ReplaySeries(
        SOURCE,
        SYMBOL,
        Timeframe.MINUTES_15,
        START,
        START + replay_count * Timeframe.MINUTES_15.duration,
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (series,), clock=FrozenClock(START)
    )
    features = FeatureEngine()
    structure = MarketStructureEngine()
    outputs: list[StructureBatch] = []
    for replay_batch in replay:
        observed = observed_candles_from_replay(replay_batch)
        features.process(observed)
        outputs.append(structure.process(observed))
    return tuple(outputs)


def test_parquet_replay_confirms_only_at_second_right_close_and_is_repeatable(
    tmp_path: Path,
) -> None:
    first = replay_structure(tmp_path / "first", stored_count=5, replay_count=5)
    repeated = replay_structure(tmp_path / "repeated", stored_count=5, replay_count=5)

    assert first == repeated
    assert not first[2].newly_confirmed_pivots
    assert not first[3].newly_confirmed_pivots
    pivot = first[4].newly_confirmed_pivots[0]
    assert pivot.kind is PivotKind.HIGH
    assert pivot.pivot_open_time == candle(2).open_time
    assert pivot.confirmed_at == candle(4).close_time


def test_future_parquet_candles_outside_replay_do_not_change_earlier_structure(
    tmp_path: Path,
) -> None:
    bounded = replay_structure(tmp_path / "bounded", stored_count=5, replay_count=5)
    with_future = replay_structure(tmp_path / "future", stored_count=7, replay_count=5)

    assert bounded == with_future
