"""Thin translation from public live candles into the shared feature input."""

from copy import deepcopy
from datetime import datetime

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.features.core import (
    FeatureBatchError,
    FeatureSeries,
    ObservedCandle,
    ObservedCandleBatch,
)
from adaptive_trading_bot.market_data.live import LiveMarketFeedSnapshot
from adaptive_trading_bot.storage import SourceId


def observed_candles_from_live(
    snapshot: LiveMarketFeedSnapshot,
) -> tuple[ObservedCandleBatch, ...]:
    """Group detached live candle facts by their actual close-time feature boundary."""

    if not isinstance(snapshot, LiveMarketFeedSnapshot):
        raise TypeError("snapshot must be a LiveMarketFeedSnapshot")
    snapshot.validate()
    if not snapshot.closed_candles:
        raise FeatureBatchError("live snapshot must contain at least one closed candle")
    events_by_close: dict[datetime, list[ObservedCandle]] = {}
    for observation in snapshot.closed_candles:
        events_by_close.setdefault(observation.candle.close_time, []).append(
            ObservedCandle(
                series=FeatureSeries(
                    source_id=SourceId(observation.source_id.value),
                    symbol=TradingSymbol(observation.symbol.base, observation.symbol.quote),
                    timeframe=observation.timeframe,
                ),
                candle=deepcopy(observation.candle),
            )
        )
    return tuple(
        ObservedCandleBatch(observed_at=close_time, events=tuple(events_by_close[close_time]))
        for close_time in sorted(events_by_close)
    )
