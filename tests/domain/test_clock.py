from datetime import UTC, datetime, timedelta

import pytest

from adaptive_trading_bot.domain import FrozenClock, SystemClock


def test_system_clock_returns_aware_utc_time() -> None:
    before = datetime.now(UTC)
    current = SystemClock().now()
    after = datetime.now(UTC)

    assert current.tzinfo is UTC
    assert before <= current <= after


def test_frozen_clock_is_deterministic_and_controllable() -> None:
    initial = datetime(2026, 9, 9, 10, 0, tzinfo=UTC)
    clock = FrozenClock(initial)

    assert clock.now() == initial
    assert clock.advance(timedelta(minutes=15)) == datetime(2026, 9, 9, 10, 15, tzinfo=UTC)
    assert clock.now() == datetime(2026, 9, 9, 10, 15, tzinfo=UTC)

    replacement = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)
    clock.set(replacement)
    assert clock.now() == replacement


def test_frozen_clock_rejects_naive_time_and_negative_advancement() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        FrozenClock(datetime(2026, 9, 9, 10, 0))

    clock = FrozenClock(datetime(2026, 9, 9, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="cannot be negative"):
        clock.advance(timedelta(seconds=-1))
