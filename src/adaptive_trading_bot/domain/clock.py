"""Clock abstractions for system time and deterministic replay/test time."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


def require_utc(value: datetime, *, field: str = "timestamp") -> datetime:
    """Validate and normalize an aware, zero-offset datetime to canonical UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")
    return value.astimezone(UTC)


class Clock(Protocol):
    """Source of current UTC time for domain behavior."""

    def now(self) -> datetime:
        """Return the current aware UTC datetime."""
        ...


class SystemClock:
    """Production clock backed by the operating system."""

    def now(self) -> datetime:
        """Return the current operating-system time in UTC."""

        return datetime.now(UTC)


class FrozenClock:
    """Explicitly controlled clock for deterministic tests and replay."""

    __slots__ = ("_current",)

    def __init__(self, current: datetime) -> None:
        self._current = require_utc(current, field="current")

    def now(self) -> datetime:
        """Return the clock's current fixed time."""

        return self._current

    def set(self, current: datetime) -> None:
        """Set the clock to an explicit UTC datetime."""

        self._current = require_utc(current, field="current")

    def advance(self, delta: timedelta) -> datetime:
        """Advance by a non-negative duration and return the new time."""

        if delta < timedelta(0):
            raise ValueError("clock advancement cannot be negative; use set() explicitly")
        self._current += delta
        return self._current
