"""No-look-ahead, multi-series historical candle replay core.

A candle covering ``[open_time, close_time)`` becomes observable only in the atomic batch whose
``observed_at`` equals that candle's ``close_time``. No wall-clock pacing belongs in this module.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from adaptive_trading_bot.domain import Candle, Clock, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    HistoricalCandleStoreError,
    IntegrityReport,
    SourceId,
)


class ReplayError(Exception):
    """Base error for deterministic replay preparation or progression."""


class ReplayConfigurationError(ReplayError):
    """The requested replay series configuration is invalid or duplicated."""


class ReplaySourceError(ReplayError):
    """A validated replay source returned data outside its series contract."""


class ReplayClockError(ReplayError):
    """The shared replay clock would need to move backward."""


class ReplayClock(Clock, Protocol):
    """Mutable historical clock shared by replay and future downstream consumers."""

    def set(self, current: datetime) -> None:
        """Set the controlled historical time."""
        ...


@dataclass(frozen=True, slots=True)
class ReplaySeries:
    """One historical-store series and aligned candle-open range ``[start, end)``."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = require_utc(self.start, field="start")
        end = require_utc(self.end, field="end")
        if start >= end:
            raise ValueError("replay series start must be before end")
        if align_timestamp(start, self.timeframe) != start:
            raise ValueError("replay series start must align to its timeframe boundary")
        if align_timestamp(end, self.timeframe) != end:
            raise ValueError("replay series end must align to its timeframe boundary")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


def _series_order(series: ReplaySeries) -> tuple[str, str, int, str, datetime, datetime]:
    return (
        series.source_id.value,
        str(series.symbol),
        int(series.timeframe.duration.total_seconds()),
        series.timeframe.value,
        series.start,
        series.end,
    )


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One immutable candle fact with the source provenance needed to distinguish series."""

    source_id: SourceId
    candle: Candle


def _event_order(event: ReplayEvent) -> tuple[str, str, int, str]:
    """Return canonical same-time order: source, symbol, duration, timeframe value."""

    return (
        event.source_id.value,
        str(event.candle.symbol),
        int(event.candle.timeframe.duration.total_seconds()),
        event.candle.timeframe.value,
    )


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    """Atomic, non-empty set of candle facts that become visible simultaneously."""

    observed_at: datetime
    events: tuple[ReplayEvent, ...]

    def __post_init__(self) -> None:
        observed_at = require_utc(self.observed_at, field="observed_at")
        events = tuple(sorted(self.events, key=_event_order))
        if not events:
            raise ValueError("replay batch must contain at least one event")
        if any(event.candle.close_time != observed_at for event in events):
            raise ValueError("every replay event must close at observed_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "events", events)

    @property
    def candles(self) -> tuple[Candle, ...]:
        """Return the immutable domain candles in canonical same-time order."""

        return tuple(event.candle for event in self.events)


@dataclass(frozen=True, slots=True)
class ReplayIntegrityFailure:
    """A requested series and the dirty integrity report that blocked replay."""

    series: ReplaySeries
    report: IntegrityReport


class ReplayIntegrityError(ReplayError):
    """One or more requested series failed the pre-emission integrity gate."""

    failures: tuple[ReplayIntegrityFailure, ...]

    def __init__(self, failures: Sequence[ReplayIntegrityFailure]) -> None:
        self.failures = tuple(failures)
        details = ", ".join(
            f"{failure.series.source_id}/{failure.series.symbol}/{failure.series.timeframe.value}"
            for failure in self.failures
        )
        super().__init__(f"replay integrity gate failed for: {details}")


class ReplaySource(Protocol):
    """Narrow immutable historical-data boundary used only during preparation."""

    def scan_integrity(self, series: ReplaySeries) -> IntegrityReport:
        """Inspect the complete requested candle-open range without mutation."""
        ...

    def read(self, series: ReplaySeries) -> tuple[Candle, ...]:
        """Read normalized immutable candles for the requested range."""
        ...


class HistoricalReplaySource:
    """Adapter from the existing HistoricalCandleStore contract into Replay."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def scan_integrity(self, series: ReplaySeries) -> IntegrityReport:
        """Delegate a non-mutating full-range integrity scan to the store contract."""

        return self._store.scan_integrity(
            series.source_id,
            series.symbol,
            series.timeframe,
            start=series.start,
            end=series.end,
        )

    def read(self, series: ReplaySeries) -> tuple[Candle, ...]:
        """Read one range through the store contract and enforce deterministic ordering."""

        try:
            candles = self._store.read(
                series.source_id,
                series.symbol,
                series.timeframe,
                start=series.start,
                end=series.end,
            )
        except HistoricalCandleStoreError as error:
            raise ReplaySourceError(f"historical read failed for replay series {series}") from error
        ordered = tuple(sorted(candles, key=lambda candle: candle.open_time))
        for candle in ordered:
            if (
                candle.symbol != series.symbol
                or candle.timeframe is not series.timeframe
                or candle.open_time < series.start
                or candle.open_time >= series.end
            ):
                raise ReplaySourceError(f"historical store violated replay series {series}")
        return ordered


class ReplayEngine:
    """Prepare integrity-gated immutable batches on one close-time timeline."""

    def __init__(self, source: ReplaySource) -> None:
        self._source = source

    def prepare(
        self,
        series: Iterable[ReplaySeries],
        *,
        clock: ReplayClock,
    ) -> ReplaySession:
        """Validate every series before reading any and return a step-capable session."""

        requested = tuple(sorted(series, key=_series_order))
        if not requested:
            raise ReplayConfigurationError("at least one replay series is required")
        if len(set(requested)) != len(requested):
            raise ReplayConfigurationError("duplicate replay series specifications are forbidden")

        reports = tuple((item, self._source.scan_integrity(item)) for item in requested)
        failures = tuple(
            ReplayIntegrityFailure(item, report) for item, report in reports if not report.is_clean
        )
        if failures:
            raise ReplayIntegrityError(failures)

        grouped: dict[datetime, list[ReplayEvent]] = defaultdict(list)
        for item in requested:
            candles = self._source.read(item)
            expected_open = item.start
            for candle in candles:
                if candle.open_time != expected_open:
                    raise ReplaySourceError(
                        f"replay source coverage changed after integrity scan for {item}"
                    )
                grouped[candle.close_time].append(ReplayEvent(item.source_id, candle))
                expected_open += item.timeframe.duration
            if expected_open != item.end:
                raise ReplaySourceError(
                    f"replay source coverage changed after integrity scan for {item}"
                )

        batches = tuple(
            ReplayBatch(observed_at, tuple(grouped[observed_at])) for observed_at in sorted(grouped)
        )
        initial_time = min(item.start for item in requested)
        clock.set(initial_time)
        if require_utc(clock.now(), field="replay clock") != initial_time:
            raise ReplayClockError("replay clock did not accept the deterministic initial time")
        return ReplaySession(batches, clock)


class ReplaySession(Iterator[ReplayBatch]):
    """Single-pass deterministic replay with explicit one-batch step mode."""

    __slots__ = ("_batches", "_clock", "_position")

    def __init__(self, batches: tuple[ReplayBatch, ...], clock: ReplayClock) -> None:
        self._batches = batches
        self._clock = clock
        self._position = 0

    @property
    def clock(self) -> Clock:
        """Expose the exact shared clock as a read-only Clock contract."""

        return self._clock

    @property
    def exhausted(self) -> bool:
        """Return whether every prepared atomic batch has been delivered."""

        return self._position >= len(self._batches)

    def step(self) -> ReplayBatch | None:
        """Advance exactly one batch, or return ``None`` after deterministic exhaustion."""

        if self.exhausted:
            return None
        batch = self._batches[self._position]
        current = require_utc(self._clock.now(), field="replay clock")
        if batch.observed_at <= current:
            raise ReplayClockError(
                "replay clock must remain before the next strictly increasing batch"
            )
        self._clock.set(batch.observed_at)
        if require_utc(self._clock.now(), field="replay clock") != batch.observed_at:
            raise ReplayClockError("replay clock did not advance to the batch observation time")
        self._position += 1
        return batch

    def __iter__(self) -> ReplaySession:
        return self

    def __next__(self) -> ReplayBatch:
        batch = self.step()
        if batch is None:
            raise StopIteration
        return batch
