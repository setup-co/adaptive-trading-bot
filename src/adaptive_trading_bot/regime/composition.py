"""Causal 15m/1h/4h composition of already-classified technical regimes.

This module describes multi-timeframe context only. It does not read candles or storage, infer
liquidity/event risk, select strategies, make decisions, size positions, or generate orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.regime.technical import (
    Direction,
    DirectionComponent,
    EventRiskState,
    LiquidityState,
    RegimeLabel,
    TechnicalRegimeBatch,
    TechnicalRegimeError,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
    validate_technical_regime_snapshot_shape,
)
from adaptive_trading_bot.storage import SourceId


class RegimeCompositionError(Exception):
    """Base error for invalid multi-timeframe composition input."""


class CompositionBatchError(RegimeCompositionError):
    """An input batch cannot be committed atomically."""


class CompositionSeriesError(RegimeCompositionError):
    """A technical regime snapshot violates its series contract."""


class HigherTimeframeBias(StrEnum):
    """Equal-weight descriptive direction across ready 1h and 4h regimes."""

    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class MultiTimeframeAlignment(StrEnum):
    """Descriptive alignment of the 15m direction with higher timeframes."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    ALIGNED_UP = "ALIGNED_UP"
    ALIGNED_DOWN = "ALIGNED_DOWN"
    HTF_UP_ENTRY_DIVERGENT = "HTF_UP_ENTRY_DIVERGENT"
    HTF_DOWN_ENTRY_DIVERGENT = "HTF_DOWN_ENTRY_DIVERGENT"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"


@dataclass(frozen=True, slots=True)
class MultiTimeframeRegimeSnapshot:
    """Immutable as-of technical context anchored on one observable 15m close."""

    source_id: SourceId
    symbol: TradingSymbol
    observed_at: datetime
    regime_15m: TechnicalRegimeSnapshot
    regime_1h: TechnicalRegimeSnapshot | None
    regime_4h: TechnicalRegimeSnapshot | None
    age_1h: timedelta | None
    age_4h: timedelta | None
    is_1h_fresh: bool
    is_4h_fresh: bool
    is_fully_ready: bool
    higher_timeframe_bias: HigherTimeframeBias
    alignment: MultiTimeframeAlignment
    has_extreme_volatility: bool
    extreme_volatility_timeframes: tuple[Timeframe, ...]
    readiness_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MultiTimeframeRegimeBatch:
    """Canonical 15m-anchored contexts emitted for one technical-regime batch."""

    observed_at: datetime
    snapshots: tuple[MultiTimeframeRegimeSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _SeriesKey:
    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe


@dataclass(frozen=True, slots=True)
class _CompositionFacts:
    age_1h: timedelta | None
    age_4h: timedelta | None
    is_1h_fresh: bool
    is_4h_fresh: bool
    is_fully_ready: bool
    higher_timeframe_bias: HigherTimeframeBias
    alignment: MultiTimeframeAlignment
    extreme_volatility_timeframes: tuple[Timeframe, ...]
    readiness_reasons: tuple[str, ...]


def _key(snapshot: TechnicalRegimeSnapshot) -> _SeriesKey:
    return _SeriesKey(snapshot.source_id, snapshot.symbol, snapshot.timeframe)


def _series_order(key: _SeriesKey) -> tuple[str, str, int, str]:
    return (
        key.source_id.value,
        str(key.symbol),
        int(key.timeframe.duration.total_seconds()),
        key.timeframe.value,
    )


def _anchor_order(snapshot: TechnicalRegimeSnapshot) -> tuple[str, str]:
    return snapshot.source_id.value, str(snapshot.symbol)


def _as_of(
    state: dict[_SeriesKey, tuple[TechnicalRegimeSnapshot, ...]],
    source_id: SourceId,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    observed_at: datetime,
) -> TechnicalRegimeSnapshot | None:
    history = state.get(_SeriesKey(source_id, symbol, timeframe), ())
    return next(
        (snapshot for snapshot in reversed(history) if snapshot.observed_at <= observed_at),
        None,
    )


def _age_and_freshness(
    anchor_at: datetime,
    snapshot: TechnicalRegimeSnapshot | None,
    timeframe: Timeframe,
) -> tuple[timedelta | None, bool]:
    if snapshot is None:
        return None, False
    age = anchor_at - snapshot.observed_at
    return age, timedelta() <= age < timeframe.duration


def _higher_timeframe_bias(
    regime_1h: TechnicalRegimeSnapshot | None,
    regime_4h: TechnicalRegimeSnapshot | None,
    *,
    is_1h_fresh: bool,
    is_4h_fresh: bool,
) -> HigherTimeframeBias:
    if (
        regime_1h is None
        or regime_4h is None
        or not is_1h_fresh
        or not is_4h_fresh
        or not regime_1h.is_fully_classified
        or not regime_4h.is_fully_classified
    ):
        return HigherTimeframeBias.UNKNOWN
    if regime_1h.direction is Direction.UP and regime_4h.direction is Direction.UP:
        return HigherTimeframeBias.UP
    if regime_1h.direction is Direction.DOWN and regime_4h.direction is Direction.DOWN:
        return HigherTimeframeBias.DOWN
    if regime_1h.direction is Direction.NEUTRAL and regime_4h.direction is Direction.NEUTRAL:
        return HigherTimeframeBias.NEUTRAL
    return HigherTimeframeBias.MIXED


def _alignment(
    regime_15m: TechnicalRegimeSnapshot,
    bias: HigherTimeframeBias,
    *,
    fully_ready: bool,
) -> MultiTimeframeAlignment:
    if not fully_ready:
        return MultiTimeframeAlignment.INSUFFICIENT_DATA
    if regime_15m.direction is Direction.UP and bias is HigherTimeframeBias.UP:
        return MultiTimeframeAlignment.ALIGNED_UP
    if regime_15m.direction is Direction.DOWN and bias is HigherTimeframeBias.DOWN:
        return MultiTimeframeAlignment.ALIGNED_DOWN
    if bias is HigherTimeframeBias.UP:
        return MultiTimeframeAlignment.HTF_UP_ENTRY_DIVERGENT
    if bias is HigherTimeframeBias.DOWN:
        return MultiTimeframeAlignment.HTF_DOWN_ENTRY_DIVERGENT
    if regime_15m.direction is Direction.NEUTRAL and bias is HigherTimeframeBias.NEUTRAL:
        return MultiTimeframeAlignment.NEUTRAL
    return MultiTimeframeAlignment.MIXED


def _readiness_reasons(
    regime_15m: TechnicalRegimeSnapshot,
    regime_1h: TechnicalRegimeSnapshot | None,
    regime_4h: TechnicalRegimeSnapshot | None,
    *,
    is_1h_fresh: bool,
    is_4h_fresh: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not regime_15m.is_fully_classified:
        reasons.append("15m regime is not fully classified")
    for label, snapshot, fresh in (
        ("1h", regime_1h, is_1h_fresh),
        ("4h", regime_4h, is_4h_fresh),
    ):
        if snapshot is None:
            reasons.append(f"no observable {label} regime")
        elif not fresh:
            reasons.append(f"{label} regime is stale")
        elif not snapshot.is_fully_classified:
            reasons.append(f"{label} regime is not fully classified")
    return tuple(reasons)


def _composition_facts(
    anchor: TechnicalRegimeSnapshot,
    regime_1h: TechnicalRegimeSnapshot | None,
    regime_4h: TechnicalRegimeSnapshot | None,
) -> _CompositionFacts:
    age_1h, is_1h_fresh = _age_and_freshness(anchor.observed_at, regime_1h, Timeframe.HOUR_1)
    age_4h, is_4h_fresh = _age_and_freshness(anchor.observed_at, regime_4h, Timeframe.HOURS_4)
    fully_ready = (
        anchor.is_fully_classified
        and regime_1h is not None
        and is_1h_fresh
        and regime_1h.is_fully_classified
        and regime_4h is not None
        and is_4h_fresh
        and regime_4h.is_fully_classified
    )
    bias = _higher_timeframe_bias(
        regime_1h,
        regime_4h,
        is_1h_fresh=is_1h_fresh,
        is_4h_fresh=is_4h_fresh,
    )
    included = (
        (Timeframe.MINUTES_15, anchor, anchor.is_fully_classified),
        (Timeframe.HOUR_1, regime_1h, is_1h_fresh),
        (Timeframe.HOURS_4, regime_4h, is_4h_fresh),
    )
    extremes = tuple(
        timeframe
        for timeframe, snapshot, fresh in included
        if snapshot is not None
        and fresh
        and snapshot.is_fully_classified
        and snapshot.volatility_state is VolatilityState.EXTREME
    )
    return _CompositionFacts(
        age_1h,
        age_4h,
        is_1h_fresh,
        is_4h_fresh,
        fully_ready,
        bias,
        _alignment(anchor, bias, fully_ready=fully_ready),
        extremes,
        _readiness_reasons(
            anchor,
            regime_1h,
            regime_4h,
            is_1h_fresh=is_1h_fresh,
            is_4h_fresh=is_4h_fresh,
        ),
    )


def _validate_technical_snapshot(snapshot: TechnicalRegimeSnapshot) -> None:
    try:
        validate_technical_regime_snapshot_shape(snapshot)
    except TechnicalRegimeError as error:
        raise CompositionSeriesError("technical regime snapshot is inconsistent") from error


def validate_multi_timeframe_regime_snapshot(snapshot: MultiTimeframeRegimeSnapshot) -> None:
    """Revalidate one MTF snapshot using the composer's authoritative derivations."""

    if not isinstance(snapshot, MultiTimeframeRegimeSnapshot):
        raise TypeError("snapshot must be a MultiTimeframeRegimeSnapshot")
    observed_at = require_utc(snapshot.observed_at, field="composition observed_at")
    if type(snapshot.is_1h_fresh) is not bool or type(snapshot.is_4h_fresh) is not bool:
        raise CompositionSeriesError("freshness flags must be booleans")
    if type(snapshot.is_fully_ready) is not bool:
        raise CompositionSeriesError("is_fully_ready must be a boolean")
    if type(snapshot.has_extreme_volatility) is not bool:
        raise CompositionSeriesError("has_extreme_volatility must be a boolean")
    if not isinstance(snapshot.extreme_volatility_timeframes, tuple) or not isinstance(
        snapshot.readiness_reasons, tuple
    ):
        raise CompositionSeriesError("composition collections must be immutable tuples")
    anchor = snapshot.regime_15m
    if (
        anchor.source_id != snapshot.source_id
        or anchor.symbol != snapshot.symbol
        or anchor.timeframe is not Timeframe.MINUTES_15
        or anchor.observed_at != observed_at
    ):
        raise CompositionSeriesError("15m anchor identity is inconsistent")
    _validate_technical_snapshot(anchor)
    for timeframe, regime in (
        (Timeframe.HOUR_1, snapshot.regime_1h),
        (Timeframe.HOURS_4, snapshot.regime_4h),
    ):
        if regime is None:
            continue
        if (
            regime.source_id != snapshot.source_id
            or regime.symbol != snapshot.symbol
            or regime.timeframe is not timeframe
            or regime.observed_at > observed_at
        ):
            raise CompositionSeriesError("higher-timeframe identity or causality is inconsistent")
        _validate_technical_snapshot(regime)
    facts = _composition_facts(anchor, snapshot.regime_1h, snapshot.regime_4h)
    if (
        snapshot.age_1h != facts.age_1h
        or snapshot.age_4h != facts.age_4h
        or snapshot.is_1h_fresh is not facts.is_1h_fresh
        or snapshot.is_4h_fresh is not facts.is_4h_fresh
        or snapshot.is_fully_ready is not facts.is_fully_ready
        or snapshot.higher_timeframe_bias is not facts.higher_timeframe_bias
        or snapshot.alignment is not facts.alignment
        or snapshot.has_extreme_volatility is not bool(facts.extreme_volatility_timeframes)
        or snapshot.extreme_volatility_timeframes != facts.extreme_volatility_timeframes
        or snapshot.readiness_reasons != facts.readiness_reasons
    ):
        raise CompositionSeriesError("multi-timeframe composition derivation is inconsistent")


class MultiTimeframeRegimeComposer:
    """Compose causal HTF context only when a current 15m anchor arrives."""

    def __init__(self) -> None:
        self._histories: dict[_SeriesKey, tuple[TechnicalRegimeSnapshot, ...]] = {}

    def process(self, batch: TechnicalRegimeBatch) -> MultiTimeframeRegimeBatch:
        """Validate, stage all same-time updates, compose 15m anchors, then commit."""

        observed_at, incoming = self._validate_batch(batch)
        staged = dict(self._histories)
        for key, snapshot in incoming.items():
            staged[key] = (*staged.get(key, ()), snapshot)
        anchors = sorted(
            (
                snapshot
                for key, snapshot in incoming.items()
                if key.timeframe is Timeframe.MINUTES_15
            ),
            key=_anchor_order,
        )
        contexts = tuple(self._compose(anchor, staged) for anchor in anchors)
        self._histories = staged
        return MultiTimeframeRegimeBatch(observed_at, contexts)

    def _compose(
        self,
        anchor: TechnicalRegimeSnapshot,
        state: dict[_SeriesKey, tuple[TechnicalRegimeSnapshot, ...]],
    ) -> MultiTimeframeRegimeSnapshot:
        regime_1h = _as_of(
            state,
            anchor.source_id,
            anchor.symbol,
            Timeframe.HOUR_1,
            anchor.observed_at,
        )
        regime_4h = _as_of(
            state,
            anchor.source_id,
            anchor.symbol,
            Timeframe.HOURS_4,
            anchor.observed_at,
        )
        facts = _composition_facts(anchor, regime_1h, regime_4h)
        return MultiTimeframeRegimeSnapshot(
            source_id=anchor.source_id,
            symbol=anchor.symbol,
            observed_at=anchor.observed_at,
            regime_15m=anchor,
            regime_1h=regime_1h,
            regime_4h=regime_4h,
            age_1h=facts.age_1h,
            age_4h=facts.age_4h,
            is_1h_fresh=facts.is_1h_fresh,
            is_4h_fresh=facts.is_4h_fresh,
            is_fully_ready=facts.is_fully_ready,
            higher_timeframe_bias=facts.higher_timeframe_bias,
            alignment=facts.alignment,
            has_extreme_volatility=bool(facts.extreme_volatility_timeframes),
            extreme_volatility_timeframes=facts.extreme_volatility_timeframes,
            readiness_reasons=facts.readiness_reasons,
        )

    def _validate_batch(
        self, batch: TechnicalRegimeBatch
    ) -> tuple[datetime, dict[_SeriesKey, TechnicalRegimeSnapshot]]:
        observed_at = require_utc(batch.observed_at, field="batch observed_at")
        if not batch.snapshots:
            raise CompositionBatchError("composition batch must contain at least one snapshot")
        incoming: dict[_SeriesKey, TechnicalRegimeSnapshot] = {}
        for snapshot in batch.snapshots:
            key = _key(snapshot)
            if key in incoming:
                raise CompositionBatchError("composition batch contains duplicate series")
            if require_utc(snapshot.observed_at, field="snapshot observed_at") != observed_at:
                raise CompositionBatchError("snapshot observed_at must match its batch")
            if snapshot.timeframe not in {
                Timeframe.MINUTES_15,
                Timeframe.HOUR_1,
                Timeframe.HOURS_4,
            }:
                raise CompositionSeriesError("snapshot timeframe is outside V1")
            if snapshot.symbol.base not in {"BTC", "ETH"}:
                raise CompositionSeriesError("snapshot symbol is outside V1")
            self._validate_snapshot(snapshot)
            history = self._histories.get(key, ())
            if history:
                previous = history[-1]
                if snapshot.observed_at <= previous.observed_at:
                    raise CompositionSeriesError(
                        f"observation time must advance strictly for {key}"
                    )
                if snapshot.bars_seen != previous.bars_seen + 1:
                    raise CompositionSeriesError(f"bars_seen must advance by one for {key}")
            incoming[key] = snapshot
        return observed_at, incoming

    @staticmethod
    def _validate_snapshot(snapshot: TechnicalRegimeSnapshot) -> None:
        if type(snapshot.bars_seen) is not int or snapshot.bars_seen < 1:
            raise CompositionSeriesError("bars_seen must be a positive integer")
        if type(snapshot.is_fully_classified) is not bool:
            raise CompositionSeriesError("is_fully_classified must be a boolean")
        if not isinstance(snapshot.direction, Direction):
            raise CompositionSeriesError("direction must be a Direction")
        if (
            type(snapshot.direction_score) is not int
            or snapshot.direction_score < -100
            or snapshot.direction_score > 100
            or snapshot.direction_score % 20
        ):
            raise CompositionSeriesError("direction_score must be a 20-point step in [-100, 100]")
        if type(snapshot.trend_strength) is not int or snapshot.trend_strength != abs(
            snapshot.direction_score
        ):
            raise CompositionSeriesError("trend_strength must equal the absolute direction score")
        expected_components = tuple(DirectionComponent)
        if tuple(item.component for item in snapshot.direction_evidence) != expected_components:
            raise CompositionSeriesError(
                "direction evidence must contain the five canonical components"
            )
        if any(item.contribution not in {-20, 0, 20} for item in snapshot.direction_evidence):
            raise CompositionSeriesError("direction evidence contribution is invalid")
        if (
            sum(item.contribution for item in snapshot.direction_evidence)
            != snapshot.direction_score
        ):
            raise CompositionSeriesError("direction evidence does not sum to direction_score")
        percentile = snapshot.volatility_percentile
        if percentile is not None and (
            not isinstance(percentile, Decimal)
            or not percentile.is_finite()
            or percentile < 0
            or percentile > 100
        ):
            raise CompositionSeriesError("volatility_percentile must be in [0, 100]")
        for name, value, expected_type in (
            ("volatility_state", snapshot.volatility_state, VolatilityState),
            ("volume_state", snapshot.volume_state, VolumeState),
            ("liquidity_state", snapshot.liquidity_state, LiquidityState),
            ("event_risk_state", snapshot.event_risk_state, EventRiskState),
            ("regime_label", snapshot.regime_label, RegimeLabel),
        ):
            if not isinstance(value, expected_type):
                raise CompositionSeriesError(f"{name} has an invalid type")
        if snapshot.is_fully_classified and snapshot.direction is Direction.UNKNOWN:
            raise CompositionSeriesError(
                "a fully classified snapshot cannot have UNKNOWN direction"
            )
