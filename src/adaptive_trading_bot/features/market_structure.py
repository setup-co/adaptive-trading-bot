"""Causal confirmed swing pivots and same-kind market-structure relations.

A pivot is emitted only after every configured right-side candle has closed. Equal neighboring
extremes are not pivots, while equal consecutive confirmed pivots receive an explicit ``EQUAL``
relation. If one candle is both a strict high and low, HIGH is emitted before LOW.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features.core import (
    FeatureSeries,
    ObservedCandle,
    ObservedCandleBatch,
)
from adaptive_trading_bot.storage import SourceId


class MarketStructureError(Exception):
    """Base error for invalid market-structure input or progression."""


class StructureBatchError(MarketStructureError):
    """An observed batch violates the atomic market-structure contract."""


class StructureSeriesError(MarketStructureError):
    """A candle violates its structure-series identity or chronology."""


@dataclass(frozen=True, slots=True)
class PivotConfiguration:
    """Fixed symmetric-window inputs used to confirm causal pivots."""

    left_bars: int = 2
    right_bars: int = 2

    def __post_init__(self) -> None:
        for name, value in (("left_bars", self.left_bars), ("right_bars", self.right_bars)):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be at least 1")

    @property
    def window_size(self) -> int:
        """Return the minimum candle count needed for one confirmation."""

        return self.left_bars + 1 + self.right_bars


class PivotKind(StrEnum):
    """Strict local-extreme kind, ordered HIGH before LOW for simultaneous events."""

    HIGH = "high"
    LOW = "low"


class StructureRelation(StrEnum):
    """Relation to the previous confirmed pivot of the same kind."""

    HH = "HH"
    LH = "LH"
    HL = "HL"
    LL = "LL"
    EQUAL = "EQUAL"


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    """A pivot fact that first became observable at ``confirmed_at``."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    kind: PivotKind
    pivot_open_time: datetime
    pivot_close_time: datetime
    pivot_price: Decimal
    confirmed_at: datetime
    left_bars: int
    right_bars: int
    pivot_bar_number: int
    relation: StructureRelation | None

    def __post_init__(self) -> None:
        if self.confirmed_at <= self.pivot_close_time:
            raise ValueError("confirmed_at must be after the pivot candle close")


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    """Latest confirmed structure for one series after one observed candle."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    observed_at: datetime
    bars_seen: int
    newly_confirmed: tuple[ConfirmedPivot, ...]
    latest_confirmed_high: ConfirmedPivot | None
    latest_confirmed_low: ConfirmedPivot | None
    latest_high_relation: StructureRelation | None
    latest_low_relation: StructureRelation | None


@dataclass(frozen=True, slots=True)
class StructureBatch:
    """Canonical per-series structure outputs for one observation batch."""

    observed_at: datetime
    snapshots: tuple[StructureSnapshot, ...]
    newly_confirmed_pivots: tuple[ConfirmedPivot, ...]


@dataclass(frozen=True, slots=True)
class _StructureState:
    candles: tuple[Candle, ...] = ()
    bars_seen: int = 0
    latest_high: ConfirmedPivot | None = None
    latest_low: ConfirmedPivot | None = None

    @property
    def last_candle(self) -> Candle | None:
        return self.candles[-1] if self.candles else None

    def advance(
        self,
        series: FeatureSeries,
        candle: Candle,
        observed_at: datetime,
        configuration: PivotConfiguration,
    ) -> tuple[_StructureState, StructureSnapshot]:
        bars_seen = self.bars_seen + 1
        evaluation_window = (*self.candles, candle)
        newly_confirmed: list[ConfirmedPivot] = []
        latest_high = self.latest_high
        latest_low = self.latest_low

        if len(evaluation_window) == configuration.window_size:
            candidate = evaluation_window[configuration.left_bars]
            neighbors = (
                *evaluation_window[: configuration.left_bars],
                *evaluation_window[configuration.left_bars + 1 :],
            )
            pivot_bar_number = bars_seen - configuration.right_bars
            if all(candidate.high > neighbor.high for neighbor in neighbors):
                relation = _high_relation(candidate.high, latest_high)
                latest_high = _pivot(
                    series,
                    candidate,
                    observed_at,
                    configuration,
                    PivotKind.HIGH,
                    candidate.high,
                    pivot_bar_number,
                    relation,
                )
                newly_confirmed.append(latest_high)
            if all(candidate.low < neighbor.low for neighbor in neighbors):
                relation = _low_relation(candidate.low, latest_low)
                latest_low = _pivot(
                    series,
                    candidate,
                    observed_at,
                    configuration,
                    PivotKind.LOW,
                    candidate.low,
                    pivot_bar_number,
                    relation,
                )
                newly_confirmed.append(latest_low)

        retained_candles = evaluation_window[-(configuration.window_size - 1) :]
        next_state = _StructureState(retained_candles, bars_seen, latest_high, latest_low)
        snapshot = StructureSnapshot(
            source_id=series.source_id,
            symbol=series.symbol,
            timeframe=series.timeframe,
            observed_at=observed_at,
            bars_seen=bars_seen,
            newly_confirmed=tuple(newly_confirmed),
            latest_confirmed_high=latest_high,
            latest_confirmed_low=latest_low,
            latest_high_relation=None if latest_high is None else latest_high.relation,
            latest_low_relation=None if latest_low is None else latest_low.relation,
        )
        return next_state, snapshot


def _pivot(
    series: FeatureSeries,
    candidate: Candle,
    confirmed_at: datetime,
    configuration: PivotConfiguration,
    kind: PivotKind,
    price: Decimal,
    pivot_bar_number: int,
    relation: StructureRelation | None,
) -> ConfirmedPivot:
    return ConfirmedPivot(
        source_id=series.source_id,
        symbol=series.symbol,
        timeframe=series.timeframe,
        kind=kind,
        pivot_open_time=candidate.open_time,
        pivot_close_time=candidate.close_time,
        pivot_price=price,
        confirmed_at=confirmed_at,
        left_bars=configuration.left_bars,
        right_bars=configuration.right_bars,
        pivot_bar_number=pivot_bar_number,
        relation=relation,
    )


def _high_relation(price: Decimal, previous: ConfirmedPivot | None) -> StructureRelation | None:
    if previous is None:
        return None
    if price > previous.pivot_price:
        return StructureRelation.HH
    if price < previous.pivot_price:
        return StructureRelation.LH
    return StructureRelation.EQUAL


def _low_relation(price: Decimal, previous: ConfirmedPivot | None) -> StructureRelation | None:
    if previous is None:
        return None
    if price > previous.pivot_price:
        return StructureRelation.HL
    if price < previous.pivot_price:
        return StructureRelation.LL
    return StructureRelation.EQUAL


class MarketStructureEngine:
    """Confirm strict pivots from causal observed-candle batches."""

    def __init__(self, configuration: PivotConfiguration | None = None) -> None:
        self.configuration = configuration or PivotConfiguration()
        self._states: dict[FeatureSeries, _StructureState] = {}

    def process(self, batch: ObservedCandleBatch) -> StructureBatch:
        """Validate and atomically advance every series in one observation batch."""

        observed_at = require_utc(batch.observed_at, field="observed_at")
        if not batch.events:
            raise StructureBatchError("structure batch must contain at least one candle")

        seen: set[FeatureSeries] = set()
        for event in batch.events:
            if event.series in seen:
                raise StructureBatchError("structure batch contains duplicate series")
            seen.add(event.series)
            self._validate_event(event, observed_at)

        pending: dict[FeatureSeries, _StructureState] = {}
        snapshots: list[StructureSnapshot] = []
        pivots: list[ConfirmedPivot] = []
        for event in batch.events:
            state = self._states.get(event.series, _StructureState())
            next_state, snapshot = state.advance(
                event.series,
                event.candle,
                observed_at,
                self.configuration,
            )
            pending[event.series] = next_state
            snapshots.append(snapshot)
            pivots.extend(snapshot.newly_confirmed)

        self._states.update(pending)
        return StructureBatch(observed_at, tuple(snapshots), tuple(pivots))

    def _validate_event(self, event: ObservedCandle, observed_at: datetime) -> None:
        if (
            event.candle.symbol != event.series.symbol
            or event.candle.timeframe is not event.series.timeframe
        ):
            raise StructureSeriesError("candle identity does not match its structure series")
        if event.candle.close_time != observed_at:
            raise StructureBatchError("every candle must close at observed_at")

        previous = self._states.get(event.series, _StructureState()).last_candle
        if previous is None:
            return
        if event.candle.open_time == previous.open_time:
            raise StructureSeriesError("duplicate candle open_time")
        if event.candle.open_time < previous.open_time:
            raise StructureSeriesError("candle open_time moved backward")
        if event.candle.open_time != previous.close_time:
            raise StructureSeriesError("candle gap is forbidden")
