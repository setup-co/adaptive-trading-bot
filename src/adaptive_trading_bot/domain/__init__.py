"""Exchange-neutral domain contracts shared across runtime environments."""

from adaptive_trading_bot.domain.clock import Clock, FrozenClock, SystemClock
from adaptive_trading_bot.domain.market_data import (
    Candle,
    MarketDataFeed,
    Timeframe,
    TradingSymbol,
    align_timestamp,
    are_contiguous,
    next_candle_open,
)

__all__ = [
    "Candle",
    "Clock",
    "FrozenClock",
    "MarketDataFeed",
    "SystemClock",
    "Timeframe",
    "TradingSymbol",
    "align_timestamp",
    "are_contiguous",
    "next_candle_open",
]
