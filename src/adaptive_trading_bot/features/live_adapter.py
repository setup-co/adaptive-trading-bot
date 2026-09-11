"""Thin translation from public live candles into the shared feature input."""

from copy import deepcopy

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.features.core import (
    FeatureBatchError,
    FeatureSeries,
    ObservedCandle,
    ObservedCandleBatch,
)
from adaptive_trading_bot.market_data.live import LiveMarketFeedSnapshot
from adaptive_trading_bot.storage import SourceId


def observed_candles_from_live(snapshot: LiveMarketFeedSnapshot) -> ObservedCandleBatch:
    """Detach validated live candle facts without calculating any feature."""

    if not isinstance(snapshot, LiveMarketFeedSnapshot):
        raise TypeError("snapshot must be a LiveMarketFeedSnapshot")
    snapshot.validate()
    if not snapshot.closed_candles:
        raise FeatureBatchError("live snapshot must contain at least one closed candle")
    return ObservedCandleBatch(
        observed_at=snapshot.observed_at,
        events=tuple(
            ObservedCandle(
                series=FeatureSeries(
                    source_id=SourceId(observation.source_id.value),
                    symbol=TradingSymbol(
                        observation.symbol.base,
                        observation.symbol.quote,
                    ),
                    timeframe=observation.timeframe,
                ),
                candle=deepcopy(observation.candle),
            )
            for observation in snapshot.closed_candles
        ),
    )
