"""Causal deterministic per-series technical market-regime classification.

The engine consumes synchronized feature and confirmed-structure snapshots only. Liquidity and
event risk remain explicitly unknown until dedicated data sources exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import (
    FeatureBatch,
    FeatureSeries,
    FeatureSnapshot,
    StructureBatch,
    StructureRelation,
    StructureSnapshot,
)
from adaptive_trading_bot.storage import SourceId

_REGIME_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class TechnicalRegimeError(Exception):
    """Base error for invalid regime configuration or input."""


class RegimeBatchError(TechnicalRegimeError):
    """Feature and structure batches cannot be applied atomically."""


class RegimeSeriesError(TechnicalRegimeError):
    """A per-series snapshot violates identity or chronology constraints."""


class Direction(StrEnum):
    """Technical direction supported by the fixed evidence score."""

    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class VolumeState(StrEnum):
    """State derived only from the existing 20-bar volume z-score."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class VolatilityState(StrEnum):
    """State derived from the causal realized-volatility percentile."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class LiquidityState(StrEnum):
    """Liquidity is deliberately unavailable in Phase 6.1."""

    UNKNOWN = "UNKNOWN"


class EventRiskState(StrEnum):
    """Event risk is deliberately unavailable in Phase 6.1."""

    UNKNOWN = "UNKNOWN"


class RegimeLabel(StrEnum):
    """Supported Phase 6.1 technical labels in rule-priority order."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STRONG_UPTREND = "STRONG_UPTREND"
    UPTREND = "UPTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    DOWNTREND = "DOWNTREND"
    NEUTRAL = "NEUTRAL"
    VOLATILE_UNCERTAIN = "VOLATILE_UNCERTAIN"


class DirectionComponent(StrEnum):
    """The five fixed, equally weighted directional evidence components."""

    EMA_ALIGNMENT = "EMA_ALIGNMENT"
    MOMENTUM_16 = "MOMENTUM_16"
    MOMENTUM_4 = "MOMENTUM_4"
    CONFIRMED_HIGH = "CONFIRMED_HIGH"
    CONFIRMED_LOW = "CONFIRMED_LOW"


@dataclass(frozen=True, slots=True)
class DirectionEvidence:
    """One explainable fixed-score component and its deterministic contribution."""

    component: DirectionComponent
    contribution: int
    reason: str


@dataclass(frozen=True, slots=True)
class TechnicalRegimeConfiguration:
    """Causal volatility-history bounds for Phase 6.1."""

    volatility_window: int = 100
    minimum_volatility_samples: int = 20

    def __post_init__(self) -> None:
        for name, value in (
            ("volatility_window", self.volatility_window),
            ("minimum_volatility_samples", self.minimum_volatility_samples),
        ):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.minimum_volatility_samples > self.volatility_window:
            raise ValueError("minimum_volatility_samples cannot exceed volatility_window")


@dataclass(frozen=True, slots=True)
class TechnicalRegimeSnapshot:
    """Immutable technical regime after one causal per-series observation."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    observed_at: datetime
    bars_seen: int
    direction: Direction
    direction_score: int
    trend_strength: int
    direction_evidence: tuple[DirectionEvidence, ...]
    volatility_percentile: Decimal | None
    volatility_state: VolatilityState
    volume_state: VolumeState
    liquidity_state: LiquidityState
    event_risk_state: EventRiskState
    regime_label: RegimeLabel
    is_fully_classified: bool


@dataclass(frozen=True, slots=True)
class TechnicalRegimeBatch:
    """Canonical technical-regime snapshots for one atomic observation time."""

    observed_at: datetime
    snapshots: tuple[TechnicalRegimeSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _RegimeState:
    observed_at: datetime
    bars_seen: int
    volatility_history: tuple[Decimal, ...]


def _series(snapshot: FeatureSnapshot | StructureSnapshot) -> FeatureSeries:
    return FeatureSeries(snapshot.source_id, snapshot.symbol, snapshot.timeframe)


def _series_order(series: FeatureSeries) -> tuple[str, str, int, str]:
    return (
        series.source_id.value,
        str(series.symbol),
        int(series.timeframe.duration.total_seconds()),
        series.timeframe.value,
    )


def _signed_component(
    component: DirectionComponent,
    left: Decimal | None,
    right: Decimal | None,
    *,
    positive_reason: str,
    negative_reason: str,
    neutral_reason: str,
) -> DirectionEvidence:
    if left is None or right is None or left == right:
        return DirectionEvidence(component, 0, neutral_reason)
    if left > right:
        return DirectionEvidence(component, 20, positive_reason)
    return DirectionEvidence(component, -20, negative_reason)


def _momentum_component(component: DirectionComponent, value: Decimal | None) -> DirectionEvidence:
    if value is None:
        return DirectionEvidence(component, 0, "unavailable")
    if value > 0:
        return DirectionEvidence(component, 20, "positive")
    if value < 0:
        return DirectionEvidence(component, -20, "negative")
    return DirectionEvidence(component, 0, "zero")


def _relation_component(
    component: DirectionComponent,
    relation: StructureRelation | None,
    positive: StructureRelation,
    negative: StructureRelation,
) -> DirectionEvidence:
    if relation is positive:
        return DirectionEvidence(component, 20, relation.value)
    if relation is negative:
        return DirectionEvidence(component, -20, relation.value)
    return DirectionEvidence(
        component,
        0,
        "unavailable" if relation is None else relation.value,
    )


def _direction_evidence(
    feature: FeatureSnapshot, structure: StructureSnapshot
) -> tuple[DirectionEvidence, ...]:
    return (
        _signed_component(
            DirectionComponent.EMA_ALIGNMENT,
            feature.ema_20,
            feature.ema_50,
            positive_reason="ema_20 above ema_50",
            negative_reason="ema_20 below ema_50",
            neutral_reason="equal or unavailable",
        ),
        _momentum_component(DirectionComponent.MOMENTUM_16, feature.momentum_16),
        _momentum_component(DirectionComponent.MOMENTUM_4, feature.momentum_4),
        _relation_component(
            DirectionComponent.CONFIRMED_HIGH,
            structure.latest_high_relation,
            StructureRelation.HH,
            StructureRelation.LH,
        ),
        _relation_component(
            DirectionComponent.CONFIRMED_LOW,
            structure.latest_low_relation,
            StructureRelation.HL,
            StructureRelation.LL,
        ),
    )


def _direction(score: int, is_fully_warmed: bool) -> Direction:
    if not is_fully_warmed:
        return Direction.UNKNOWN
    if score >= 40:
        return Direction.UP
    if score <= -40:
        return Direction.DOWN
    return Direction.NEUTRAL


def _volatility_state(percentile: Decimal | None) -> VolatilityState:
    if percentile is None:
        return VolatilityState.UNKNOWN
    if percentile <= Decimal(25):
        return VolatilityState.LOW
    if percentile <= Decimal(75):
        return VolatilityState.NORMAL
    if percentile < Decimal(90):
        return VolatilityState.HIGH
    return VolatilityState.EXTREME


def _volume_state(zscore: Decimal | None) -> VolumeState:
    if zscore is None:
        return VolumeState.UNKNOWN
    if zscore <= Decimal(-1):
        return VolumeState.LOW
    if zscore >= Decimal(1):
        return VolumeState.HIGH
    return VolumeState.NORMAL


def _regime_label(
    *,
    fully_warmed: bool,
    percentile: Decimal | None,
    volatility: VolatilityState,
    direction: Direction,
    strength: int,
) -> RegimeLabel:
    if not fully_warmed or percentile is None:
        return RegimeLabel.INSUFFICIENT_DATA
    if volatility is VolatilityState.EXTREME and strength < 60:
        return RegimeLabel.VOLATILE_UNCERTAIN
    if direction is Direction.UP:
        return RegimeLabel.STRONG_UPTREND if strength >= 60 else RegimeLabel.UPTREND
    if direction is Direction.DOWN:
        return RegimeLabel.STRONG_DOWNTREND if strength >= 60 else RegimeLabel.DOWNTREND
    return RegimeLabel.NEUTRAL


class TechnicalRegimeEngine:
    """Classify synchronized causal feature/structure batches with isolated state."""

    def __init__(self, configuration: TechnicalRegimeConfiguration | None = None) -> None:
        self.configuration = configuration or TechnicalRegimeConfiguration()
        self._states: dict[FeatureSeries, _RegimeState] = {}

    def process(
        self, feature_batch: FeatureBatch, structure_batch: StructureBatch
    ) -> TechnicalRegimeBatch:
        """Validate and atomically classify one synchronized batch pair."""

        observed_at, feature_by_series, structure_by_series = self._validate_batches(
            feature_batch, structure_batch
        )
        pending: dict[FeatureSeries, _RegimeState] = {}
        snapshots: list[TechnicalRegimeSnapshot] = []
        with localcontext(_REGIME_DECIMAL_CONTEXT):
            for series in sorted(feature_by_series, key=_series_order):
                feature = feature_by_series[series]
                structure = structure_by_series[series]
                previous = self._states.get(series)
                history = () if previous is None else previous.volatility_history
                if feature.realized_volatility_20 is not None:
                    history = (*history, feature.realized_volatility_20)[
                        -self.configuration.volatility_window :
                    ]
                percentile = self._percentile(history, feature.realized_volatility_20)
                evidence = _direction_evidence(feature, structure)
                score = sum(item.contribution for item in evidence)
                direction = _direction(score, feature.is_fully_warmed)
                strength = abs(score)
                volatility = _volatility_state(percentile)
                fully_classified = feature.is_fully_warmed and percentile is not None
                pending[series] = _RegimeState(observed_at, feature.bars_seen, history)
                snapshots.append(
                    TechnicalRegimeSnapshot(
                        source_id=series.source_id,
                        symbol=series.symbol,
                        timeframe=series.timeframe,
                        observed_at=observed_at,
                        bars_seen=feature.bars_seen,
                        direction=direction,
                        direction_score=score,
                        trend_strength=strength,
                        direction_evidence=evidence,
                        volatility_percentile=percentile,
                        volatility_state=volatility,
                        volume_state=_volume_state(feature.volume_zscore_20),
                        liquidity_state=LiquidityState.UNKNOWN,
                        event_risk_state=EventRiskState.UNKNOWN,
                        regime_label=_regime_label(
                            fully_warmed=feature.is_fully_warmed,
                            percentile=percentile,
                            volatility=volatility,
                            direction=direction,
                            strength=strength,
                        ),
                        is_fully_classified=fully_classified,
                    )
                )
        self._states.update(pending)
        return TechnicalRegimeBatch(observed_at, tuple(snapshots))

    def _percentile(self, history: tuple[Decimal, ...], current: Decimal | None) -> Decimal | None:
        if current is None or len(history) < self.configuration.minimum_volatility_samples:
            return None
        return (
            Decimal(100)
            * Decimal(sum(value <= current for value in history))
            / Decimal(len(history))
        )

    def _validate_batches(
        self, feature_batch: FeatureBatch, structure_batch: StructureBatch
    ) -> tuple[
        datetime,
        dict[FeatureSeries, FeatureSnapshot],
        dict[FeatureSeries, StructureSnapshot],
    ]:
        feature_time = require_utc(feature_batch.observed_at, field="feature observed_at")
        structure_time = require_utc(structure_batch.observed_at, field="structure observed_at")
        if feature_time != structure_time:
            raise RegimeBatchError("feature and structure batch observed_at values must match")
        if not feature_batch.snapshots or not structure_batch.snapshots:
            raise RegimeBatchError("regime input batches must contain at least one snapshot")

        feature_by_series = self._feature_snapshots(feature_batch, feature_time)
        structure_by_series = self._structure_snapshots(structure_batch, structure_time)
        if set(feature_by_series) != set(structure_by_series):
            raise RegimeBatchError("feature and structure series sets must match exactly")

        for series, feature in feature_by_series.items():
            structure = structure_by_series[series]
            if type(feature.bars_seen) is not int or type(structure.bars_seen) is not int:
                raise RegimeSeriesError(f"bars_seen must be an integer for {series}")
            if feature.bars_seen != structure.bars_seen:
                raise RegimeSeriesError(f"bars_seen mismatch for {series}")
            if feature.bars_seen < 1:
                raise RegimeSeriesError(f"bars_seen must be positive for {series}")
            previous = self._states.get(series)
            if previous is not None:
                if feature_time <= previous.observed_at:
                    raise RegimeSeriesError(f"observation time must advance strictly for {series}")
                if feature.bars_seen != previous.bars_seen + 1:
                    raise RegimeSeriesError(f"bars_seen must advance by one for {series}")
            self._validate_feature_values(feature)
            self._validate_structure_relations(structure)
        return feature_time, feature_by_series, structure_by_series

    @staticmethod
    def _feature_snapshots(
        batch: FeatureBatch, observed_at: datetime
    ) -> dict[FeatureSeries, FeatureSnapshot]:
        snapshots: dict[FeatureSeries, FeatureSnapshot] = {}
        for snapshot in batch.snapshots:
            series = _series(snapshot)
            if series in snapshots:
                raise RegimeBatchError("feature batch contains duplicate series")
            if (
                require_utc(snapshot.observed_at, field="feature snapshot observed_at")
                != observed_at
            ):
                raise RegimeBatchError("feature snapshot observed_at must match its batch")
            snapshots[series] = snapshot
        return snapshots

    @staticmethod
    def _structure_snapshots(
        batch: StructureBatch, observed_at: datetime
    ) -> dict[FeatureSeries, StructureSnapshot]:
        snapshots: dict[FeatureSeries, StructureSnapshot] = {}
        for snapshot in batch.snapshots:
            series = _series(snapshot)
            if series in snapshots:
                raise RegimeBatchError("structure batch contains duplicate series")
            if (
                require_utc(snapshot.observed_at, field="structure snapshot observed_at")
                != observed_at
            ):
                raise RegimeBatchError("structure snapshot observed_at must match its batch")
            snapshots[series] = snapshot
        return snapshots

    @staticmethod
    def _validate_feature_values(snapshot: FeatureSnapshot) -> None:
        if type(snapshot.is_fully_warmed) is not bool:
            raise RegimeSeriesError("is_fully_warmed must be a boolean")
        for name, value in (
            ("ema_20", snapshot.ema_20),
            ("ema_50", snapshot.ema_50),
            ("momentum_16", snapshot.momentum_16),
            ("momentum_4", snapshot.momentum_4),
            ("volume_zscore_20", snapshot.volume_zscore_20),
            ("realized_volatility_20", snapshot.realized_volatility_20),
        ):
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise RegimeSeriesError(f"{name} must be None or a finite Decimal")
        if snapshot.realized_volatility_20 is not None and snapshot.realized_volatility_20 < 0:
            raise RegimeSeriesError("realized_volatility_20 cannot be negative")

    @staticmethod
    def _validate_structure_relations(snapshot: StructureSnapshot) -> None:
        for relation in (snapshot.latest_high_relation, snapshot.latest_low_relation):
            if relation is not None and not isinstance(relation, StructureRelation):
                raise RegimeSeriesError("structure relation must be a StructureRelation")
        if snapshot.latest_high_relation not in {
            None,
            StructureRelation.HH,
            StructureRelation.LH,
            StructureRelation.EQUAL,
        }:
            raise RegimeSeriesError("latest high relation is invalid")
        if snapshot.latest_low_relation not in {
            None,
            StructureRelation.HL,
            StructureRelation.LL,
            StructureRelation.EQUAL,
        }:
            raise RegimeSeriesError("latest low relation is invalid")
