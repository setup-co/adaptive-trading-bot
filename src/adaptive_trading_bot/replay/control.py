"""Operator pacing around the deterministic Replay Core.

This layer may consult wall-clock monotonic time and sleep. It never changes replay batches,
historical observation timestamps, or the clock progression owned by ``ReplaySession``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from enum import IntEnum
from typing import Protocol

from adaptive_trading_bot.replay.core import ReplayBatch, ReplayError, ReplaySession


class ReplayPacingError(ReplayError):
    """The outer wall-clock timer failed while a batch remained pending."""


class ReplaySpeed(IntEnum):
    """Exact V1 mapping from historical time to wall-clock time."""

    X1 = 1
    X10 = 10
    X50 = 50
    X100 = 100

    @property
    def label(self) -> str:
        """Return the operator-facing speed label."""

        return f"{self.value}x"

    @classmethod
    def parse(cls, value: str) -> ReplaySpeed:
        """Parse one exact operator-facing speed label."""

        normalized = value.strip().lower()
        for speed in cls:
            if speed.label == normalized:
                return speed
        raise ValueError("replay speed must be one of: 1x, 10x, 50x, 100x")


class ReplayTimer(Protocol):
    """Injectable monotonic timer and sleeper used only by the pacing layer."""

    def monotonic(self) -> float:
        """Return an arbitrary monotonic time in seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for a non-negative number of wall-clock seconds."""
        ...


class SystemReplayTimer:
    """Production pacing timer backed by Python's monotonic clock and sleep."""

    def monotonic(self) -> float:
        """Return the operating system monotonic clock."""

        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Sleep for a validated non-negative wall-clock duration."""

        if seconds < 0:
            raise ValueError("replay sleep duration cannot be negative")
        time.sleep(seconds)


def planned_wall_delay(
    previous_observed_at: datetime,
    observed_at: datetime,
    speed: ReplaySpeed,
) -> float:
    """Map one positive historical observation delta to deterministic wall seconds."""

    historical_seconds = (observed_at - previous_observed_at).total_seconds()
    if historical_seconds <= 0:
        raise ValueError("replay observations must be strictly increasing")
    return historical_seconds / speed.value


class PacedReplayController:
    """Manual and monotonic-paced controls around one prepared ReplaySession."""

    __slots__ = (
        "_deadline",
        "_last_observed_at",
        "_pending_batch",
        "_session",
        "_speed",
        "_timer",
    )

    def __init__(
        self,
        session: ReplaySession,
        speed: ReplaySpeed,
        *,
        timer: ReplayTimer | None = None,
    ) -> None:
        self._session = session
        self._speed = speed
        self._timer = timer if timer is not None else SystemReplayTimer()
        self._last_observed_at: datetime | None = None
        self._deadline: float | None = None
        self._pending_batch: ReplayBatch | None = None

    @property
    def speed(self) -> ReplaySpeed:
        """Return the fixed speed for this controller."""

        return self._speed

    @property
    def exhausted(self) -> bool:
        """Return whether the underlying deterministic session is exhausted."""

        return self._pending_batch is None and self._session.exhausted

    def step(self) -> ReplayBatch | None:
        """Deliver exactly one batch immediately without sleeping."""

        batch = self._pending_batch
        if batch is None:
            batch = self._session.step()
        if batch is not None:
            self._pending_batch = None
            self._last_observed_at = batch.observed_at
            self._deadline = None
        return batch

    def paced_step(self) -> ReplayBatch | None:
        """Deliver one batch, applying monotonic pacing after the first batch."""

        batch = self._pending_batch
        if batch is None:
            batch = self._session.step()
            if batch is None:
                return None
            self._pending_batch = batch
            if self._last_observed_at is not None and self._deadline is not None:
                self._deadline += planned_wall_delay(
                    self._last_observed_at,
                    batch.observed_at,
                    self._speed,
                )

        try:
            now = self._timer.monotonic()
        except Exception as error:
            raise ReplayPacingError("replay monotonic timer failed") from error
        if self._last_observed_at is None or self._deadline is None:
            self._deadline = now
        else:
            remaining = max(0.0, self._deadline - now)
            try:
                self._timer.sleep(remaining)
            except Exception as error:
                raise ReplayPacingError("replay sleeper failed; batch remains pending") from error

        self._pending_batch = None
        self._last_observed_at = batch.observed_at
        return batch

    def run(self) -> Iterator[ReplayBatch]:
        """Yield every remaining batch with pacing and deterministic ordering."""

        while (batch := self.paced_step()) is not None:
            yield batch
