from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.regime import TechnicalRegimeBatch, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def candle(index: int) -> Candle:
    open_time = START + index * Timeframe.MINUTES_15.duration
    close = Decimal(100 + index + index % 3)
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=close,
        high=close + Decimal(2 + index % 2),
        low=close - Decimal(2),
        close=close,
        volume=Decimal(10 + index % 5),
    )


def replay_regimes(
    path: Path, *, stored_count: int, replay_count: int
) -> tuple[TechnicalRegimeBatch, ...]:
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
    regime = TechnicalRegimeEngine()
    outputs: list[TechnicalRegimeBatch] = []
    for replay_batch in replay:
        observed = observed_candles_from_replay(replay_batch)
        feature_batch = features.process(observed)
        structure_batch = structure.process(observed)
        outputs.append(regime.process(feature_batch, structure_batch))
    return tuple(outputs)


def test_parquet_replay_regime_is_causal_bounded_and_reproducible(tmp_path: Path) -> None:
    bounded = replay_regimes(tmp_path / "bounded", stored_count=60, replay_count=60)
    with_future = replay_regimes(tmp_path / "future", stored_count=70, replay_count=60)
    repeated = replay_regimes(tmp_path / "repeated", stored_count=60, replay_count=60)

    assert bounded == with_future == repeated
    assert all(
        batch.observed_at == batch.snapshots[0].observed_at == candle(index).close_time
        for index, batch in enumerate(bounded)
    )
    assert bounded[38].snapshots[0].volatility_percentile is None
    assert bounded[39].snapshots[0].volatility_percentile is not None
    assert bounded[48].snapshots[0].is_fully_classified is False
    assert bounded[49].snapshots[0].is_fully_classified is True
