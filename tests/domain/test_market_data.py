from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

from adaptive_trading_bot.domain import (
    Candle,
    Timeframe,
    TradingSymbol,
    align_timestamp,
    are_contiguous,
    next_candle_open,
)

BTC_USDT = TradingSymbol("BTC", "USDT")


def make_candle(
    *,
    open_time: datetime = datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    timeframe: Timeframe = Timeframe.MINUTES_15,
    symbol: TradingSymbol = BTC_USDT,
    open_price: Decimal = Decimal("100.00"),
    high: Decimal = Decimal("110.00"),
    low: Decimal = Decimal("90.00"),
    close: Decimal = Decimal("105.00"),
    volume: Decimal = Decimal("12.500"),
    close_time: datetime | None = None,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time or open_time + timeframe.duration,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


@pytest.mark.parametrize(
    ("timeframe", "duration"),
    [
        (Timeframe.MINUTES_15, timedelta(minutes=15)),
        (Timeframe.HOUR_1, timedelta(hours=1)),
        (Timeframe.HOURS_4, timedelta(hours=4)),
    ],
)
def test_v1_timeframe_durations(timeframe: Timeframe, duration: timedelta) -> None:
    assert timeframe.duration == duration
    assert Timeframe(timeframe.value) is timeframe


def test_unsupported_timeframe_is_rejected() -> None:
    with pytest.raises(ValueError):
        Timeframe("5m")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [(" btc/usdt ", "BTC/USDT"), ("eth/usdt", "ETH/USDT")],
)
def test_symbol_parsing_normalizes_casing(raw: str, canonical: str) -> None:
    assert str(TradingSymbol.parse(raw)) == canonical


@pytest.mark.parametrize(
    "base,quote",
    [("", "USDT"), ("BTC", ""), ("BTC/USD", "USDT"), ("BTC", "BTC")],
)
def test_invalid_symbol_components_are_rejected(base: str, quote: str) -> None:
    with pytest.raises(ValueError):
        TradingSymbol(base, quote)


def test_valid_candle_is_immutable_and_serializes_stably() -> None:
    candle = make_candle()

    assert candle.serialize() == {
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "open_time": "2026-09-09T12:00:00+00:00",
        "close_time": "2026-09-09T12:15:00+00:00",
        "open": "100.00",
        "high": "110.00",
        "low": "90.00",
        "close": "105.00",
        "volume": "12.500",
    }
    with pytest.raises(FrozenInstanceError):
        candle.__setattr__("close", Decimal("106"))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: make_candle(high=Decimal("104")),
        lambda: make_candle(low=Decimal("101")),
        lambda: make_candle(open_price=Decimal("0")),
        lambda: make_candle(close=Decimal("-1")),
        lambda: make_candle(volume=Decimal("-0.01")),
        lambda: make_candle(high=Decimal("NaN")),
    ],
)
def test_invalid_candle_prices_and_volume_are_rejected(
    factory: Callable[[], Candle],
) -> None:
    with pytest.raises(ValueError):
        factory()


def test_candle_rejects_binary_floating_point_values() -> None:
    with pytest.raises(TypeError, match="open must be a Decimal"):
        make_candle(open_price=cast(Decimal, 100.0))


def test_candle_rejects_naive_or_non_utc_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        make_candle(open_time=datetime(2026, 9, 9, 12, 0))

    non_utc = timezone(timedelta(hours=3, minutes=30))
    with pytest.raises(ValueError, match="must use UTC"):
        make_candle(open_time=datetime(2026, 9, 9, 12, 0, tzinfo=non_utc))


def test_candle_rejects_timeframe_duration_mismatch() -> None:
    with pytest.raises(ValueError, match="duration"):
        make_candle(
            timeframe=Timeframe.HOUR_1,
            close_time=datetime(2026, 9, 9, 12, 15, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("timestamp", "timeframe", "expected"),
    [
        (
            datetime(2026, 9, 9, 23, 59, 59, 999999, tzinfo=UTC),
            Timeframe.MINUTES_15,
            datetime(2026, 9, 9, 23, 45, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
            Timeframe.HOUR_1,
            datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 10, 3, 59, 59, tzinfo=UTC),
            Timeframe.HOURS_4,
            datetime(2026, 9, 10, 0, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 10, 4, 0, tzinfo=UTC),
            Timeframe.HOURS_4,
            datetime(2026, 9, 10, 4, 0, tzinfo=UTC),
        ),
    ],
)
def test_timestamp_alignment_across_boundaries(
    timestamp: datetime, timeframe: Timeframe, expected: datetime
) -> None:
    assert align_timestamp(timestamp, timeframe) == expected


def test_next_open_and_contiguous_vs_gap_detection() -> None:
    first = make_candle()
    contiguous = make_candle(open_time=first.close_time)
    gap = make_candle(open_time=first.close_time + timedelta(minutes=15))
    other_symbol = make_candle(open_time=first.close_time, symbol=TradingSymbol("ETH", "USDT"))

    assert next_candle_open(first.open_time, first.timeframe) == first.close_time
    assert are_contiguous(first, contiguous)
    assert not are_contiguous(first, gap)
    assert not are_contiguous(first, other_symbol)
