"""Exchange-neutral market-data values and read contract.

Candles use half-open intervals: ``[open_time, close_time)``. The close timestamp is the
exclusive boundary and must equal ``open_time + timeframe.duration``.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from re import fullmatch
from typing import Protocol

from adaptive_trading_bot.domain.clock import require_utc


class Timeframe(StrEnum):
    """Approved V1 candle intervals."""

    MINUTES_15 = "15m"
    HOUR_1 = "1h"
    HOURS_4 = "4h"

    @property
    def duration(self) -> timedelta:
        """Return the exact duration represented by this timeframe."""

        match self:
            case Timeframe.MINUTES_15:
                return timedelta(minutes=15)
            case Timeframe.HOUR_1:
                return timedelta(hours=1)
            case Timeframe.HOURS_4:
                return timedelta(hours=4)


@dataclass(frozen=True, slots=True)
class TradingSymbol:
    """Normalized exchange-neutral spot-market symbol."""

    base: str
    quote: str

    def __post_init__(self) -> None:
        base = self.base.strip().upper()
        quote = self.quote.strip().upper()
        if not fullmatch(r"[A-Z0-9]+", base):
            raise ValueError("base asset must contain only ASCII letters and digits")
        if not fullmatch(r"[A-Z0-9]+", quote):
            raise ValueError("quote asset must contain only ASCII letters and digits")
        if base == quote:
            raise ValueError("base and quote assets must differ")
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "quote", quote)

    @classmethod
    def parse(cls, value: str) -> "TradingSymbol":
        """Parse the canonical ``BASE/QUOTE`` representation."""

        components = value.split("/")
        if len(components) != 2:
            raise ValueError("symbol must use BASE/QUOTE form")
        return cls(base=components[0], quote=components[1])

    def __str__(self) -> str:
        return f"{self.base}/{self.quote}"


@dataclass(frozen=True, slots=True)
class Candle:
    """Immutable OHLCV candle covering ``[open_time, close_time)``."""

    symbol: TradingSymbol
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_time", require_utc(self.open_time, field="open_time"))
        object.__setattr__(self, "close_time", require_utc(self.close_time, field="close_time"))
        if self.open_time != align_timestamp(self.open_time, self.timeframe):
            raise ValueError("candle open_time must be aligned to its timeframe boundary")
        if self.close_time - self.open_time != self.timeframe.duration:
            raise ValueError("candle interval duration must match its timeframe")

        values = {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }
        for name, value in values.items():
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")

        if any(price <= 0 for price in (self.open, self.high, self.low, self.close)):
            raise ValueError("candle prices must be positive")
        if self.volume < 0:
            raise ValueError("candle volume must be non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must be greater than or equal to every OHLC price")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be less than or equal to every OHLC price")

    def serialize(self) -> dict[str, str]:
        """Return a stable, precision-preserving representation for replay/persistence."""

        return {
            "symbol": str(self.symbol),
            "timeframe": self.timeframe.value,
            "open_time": self.open_time.isoformat(),
            "close_time": self.close_time.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
        }


class MarketDataFeed(Protocol):
    """Minimal async candle source implemented by future live and historical feeds."""

    def stream_candles(self, symbol: TradingSymbol, timeframe: Timeframe) -> AsyncIterator[Candle]:
        """Yield candles in chronological order for a symbol and timeframe."""
        ...


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def align_timestamp(timestamp: datetime, timeframe: Timeframe) -> datetime:
    """Floor a UTC timestamp to its V1 candle-bucket start."""

    timestamp = require_utc(timestamp)
    elapsed_buckets = (timestamp - _EPOCH) // timeframe.duration
    return _EPOCH + elapsed_buckets * timeframe.duration


def next_candle_open(open_time: datetime, timeframe: Timeframe) -> datetime:
    """Return the expected opening boundary immediately after ``open_time``."""

    return require_utc(open_time, field="open_time") + timeframe.duration


def are_contiguous(previous: Candle, current: Candle) -> bool:
    """Return whether two same-market candles touch without overlap or a gap."""

    return (
        previous.symbol == current.symbol
        and previous.timeframe is current.timeframe
        and previous.close_time == current.open_time
    )
