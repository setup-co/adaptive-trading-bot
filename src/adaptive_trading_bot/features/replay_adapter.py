"""Thin translation from Replay's public batch model into feature input."""

from adaptive_trading_bot.features.core import (
    FeatureSeries,
    ObservedCandle,
    ObservedCandleBatch,
)
from adaptive_trading_bot.replay import ReplayBatch


def observed_candles_from_replay(batch: ReplayBatch) -> ObservedCandleBatch:
    """Preserve Replay's atomic observation boundary and source provenance."""

    return ObservedCandleBatch(
        observed_at=batch.observed_at,
        events=tuple(
            ObservedCandle(
                series=FeatureSeries(
                    source_id=event.source_id,
                    symbol=event.candle.symbol,
                    timeframe=event.candle.timeframe,
                ),
                candle=event.candle,
            )
            for event in batch.events
        ),
    )
