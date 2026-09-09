from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.regime import (
    MultiTimeframeRegimeComposer,
    MultiTimeframeRegimeSnapshot,
    TechnicalRegimeEngine,
)
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def candle(index: int, timeframe: Timeframe) -> Candle:
    open_time = START + index * timeframe.duration
    close = Decimal(100 + index + int(timeframe.duration.total_seconds() // 900))
    return Candle(
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=close,
        high=close + Decimal(2 + index % 2),
        low=close - Decimal(2),
        close=close,
        volume=Decimal(10 + index % 5),
    )


def replay_contexts(
    path: Path,
    *,
    stored_15m: int,
    stored_1h: int,
    stored_4h: int,
) -> tuple[MultiTimeframeRegimeSnapshot, ...]:
    store = ParquetHistoricalCandleStore(path)
    stored = tuple(
        candle(index, timeframe)
        for timeframe, count in (
            (Timeframe.MINUTES_15, stored_15m),
            (Timeframe.HOUR_1, stored_1h),
            (Timeframe.HOURS_4, stored_4h),
        )
        for index in range(count)
    )
    store.merge(SOURCE, stored)
    specifications = tuple(
        ReplaySeries(SOURCE, SYMBOL, timeframe, START, START + count * timeframe.duration)
        for timeframe, count in (
            (Timeframe.MINUTES_15, 32),
            (Timeframe.HOUR_1, 4),
            (Timeframe.HOURS_4, 1),
        )
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        specifications, clock=FrozenClock(START)
    )
    features = FeatureEngine()
    structure = MarketStructureEngine()
    technical = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    contexts: list[MultiTimeframeRegimeSnapshot] = []
    for replay_batch in replay:
        observed = observed_candles_from_replay(replay_batch)
        feature_batch = features.process(observed)
        structure_batch = structure.process(observed)
        technical_batch = technical.process(feature_batch, structure_batch)
        contexts.extend(composer.process(technical_batch).snapshots)
    return tuple(contexts)


def test_multitimeframe_parquet_replay_is_as_of_bounded_and_stale_aware(
    tmp_path: Path,
) -> None:
    bounded = replay_contexts(
        tmp_path / "bounded",
        stored_15m=32,
        stored_1h=4,
        stored_4h=1,
    )
    with_future = replay_contexts(
        tmp_path / "future",
        stored_15m=36,
        stored_1h=6,
        stored_4h=2,
    )
    repeated = replay_contexts(
        tmp_path / "repeated",
        stored_15m=32,
        stored_1h=4,
        stored_4h=1,
    )

    assert bounded == with_future == repeated
    assert len(bounded) == 32
    assert all(
        context.observed_at == candle(index, Timeframe.MINUTES_15).close_time
        for index, context in enumerate(bounded)
    )
    assert all(
        context.regime_1h is None or context.regime_1h.observed_at <= context.observed_at
        for context in bounded
    )
    assert all(
        context.regime_4h is None or context.regime_4h.observed_at <= context.observed_at
        for context in bounded
    )
    assert bounded[2].regime_1h is None
    assert bounded[3].regime_1h is not None
    assert bounded[3].regime_1h.observed_at == START + timedelta(hours=1)
    assert bounded[14].regime_4h is None
    assert bounded[15].regime_1h is not None
    assert bounded[15].regime_4h is not None
    assert bounded[15].regime_1h.observed_at == bounded[15].observed_at
    assert bounded[15].regime_4h.observed_at == bounded[15].observed_at
    assert bounded[19].age_1h == timedelta(hours=1)
    assert bounded[19].is_1h_fresh is False
    assert bounded[-1].age_4h == timedelta(hours=4)
    assert bounded[-1].is_4h_fresh is False
