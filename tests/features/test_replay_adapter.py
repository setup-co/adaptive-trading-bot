from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import FeatureBatch, FeatureEngine, observed_candles_from_replay
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def candle(index: int) -> Candle:
    value = Decimal(100 + index)
    open_time = START + index * Timeframe.MINUTES_15.duration
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=value,
        high=value + Decimal(1),
        low=value - Decimal(1),
        close=value,
        volume=Decimal(1),
    )


def replay_snapshots(
    path: Path, *, stored_count: int, replay_count: int
) -> tuple[FeatureBatch, ...]:
    store = ParquetHistoricalCandleStore(path)
    store.merge(SOURCE, tuple(candle(index) for index in range(stored_count)))
    series = ReplaySeries(
        SOURCE,
        SYMBOL,
        Timeframe.MINUTES_15,
        START,
        START + replay_count * Timeframe.MINUTES_15.duration,
    )
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (series,), clock=FrozenClock(START)
    )
    features = FeatureEngine()
    return tuple(features.process(observed_candles_from_replay(batch)) for batch in session)


def test_real_parquet_replay_is_close_time_causal_and_reproducible(tmp_path: Path) -> None:
    first = replay_snapshots(tmp_path / "first", stored_count=21, replay_count=20)
    repeated = replay_snapshots(tmp_path / "second", stored_count=21, replay_count=20)

    assert first == repeated
    assert first[0].observed_at == candle(0).close_time
    assert first[0].snapshots[0].bars_seen == 1
    assert first[0].snapshots[0].return_1 is None
    assert first[12].snapshots[0].atr_14 is None
    assert first[13].snapshots[0].atr_14 == Decimal(2)
    assert first[13].snapshots[0].rsi_14 is None
    assert first[14].snapshots[0].rsi_14 == Decimal(100)
    assert first[-1].snapshots[0].ema_20 == Decimal("109.5")
    assert first[-1].snapshots[0].realized_volatility_20 is None


def test_future_stored_candles_do_not_change_earlier_replay_features(tmp_path: Path) -> None:
    without_future = replay_snapshots(tmp_path / "bounded", stored_count=20, replay_count=20)
    with_future = replay_snapshots(tmp_path / "future", stored_count=25, replay_count=20)

    assert without_future == with_future
