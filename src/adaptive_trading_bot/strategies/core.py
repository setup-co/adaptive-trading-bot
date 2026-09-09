"""Environment-neutral immutable strategy input, output, and protocol contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import FeatureSnapshot, StructureSnapshot
from adaptive_trading_bot.regime import MultiTimeframeRegimeSnapshot


class StrategyContextError(Exception):
    """Synchronized strategy inputs are incomplete or inconsistent."""


@runtime_checkable
class SourceIdentity(Protocol):
    """Environment-neutral identity contract implemented by the project source id."""

    @property
    def value(self) -> str:
        """Return the stable source identifier value."""


class CandidateAction(StrEnum):
    """A strategy evaluation result; candidates are not orders or risk approval."""

    LONG_CANDIDATE = "LONG_CANDIDATE"
    SHORT_CANDIDATE = "SHORT_CANDIDATE"
    NO_TRADE = "NO_TRADE"


class StrategyReasonCode(StrEnum):
    """Stable, canonically ordered evidence and failure codes."""

    CONTEXT_READY = "CONTEXT_READY"
    REQUIRED_FEATURES_AVAILABLE = "REQUIRED_FEATURES_AVAILABLE"
    VOLATILITY_ACCEPTABLE = "VOLATILITY_ACCEPTABLE"
    HTF_TREND_CONFIRMED = "HTF_TREND_CONFIRMED"
    ENTRY_DIRECTION_COMPATIBLE = "ENTRY_DIRECTION_COMPATIBLE"
    EMA_TREND_ALIGNED = "EMA_TREND_ALIGNED"
    VALUE_ZONE_TOUCHED = "VALUE_ZONE_TOUCHED"
    PULLBACK_DEPTH_ACCEPTABLE = "PULLBACK_DEPTH_ACCEPTABLE"
    EMA_RECLAIM_CONFIRMED = "EMA_RECLAIM_CONFIRMED"
    CANDLE_DIRECTION_CONFIRMED = "CANDLE_DIRECTION_CONFIRMED"
    MOMENTUM_RECOVERY_CONFIRMED = "MOMENTUM_RECOVERY_CONFIRMED"
    RSI_CONFIRMATION = "RSI_CONFIRMATION"
    VOLUME_CONFIRMATION = "VOLUME_CONFIRMATION"
    STRUCTURAL_PIVOT_AVAILABLE = "STRUCTURAL_PIVOT_AVAILABLE"
    INVALIDATION_GEOMETRY_VALID = "INVALIDATION_GEOMETRY_VALID"

    CONTEXT_NOT_READY = "CONTEXT_NOT_READY"
    FEATURES_NOT_FULLY_WARMED = "FEATURES_NOT_FULLY_WARMED"
    MISSING_EMA_20 = "MISSING_EMA_20"
    MISSING_EMA_50 = "MISSING_EMA_50"
    MISSING_ATR_14 = "MISSING_ATR_14"
    MISSING_RSI_14 = "MISSING_RSI_14"
    MISSING_MOMENTUM_4 = "MISSING_MOMENTUM_4"
    MISSING_VOLUME_ZSCORE_20 = "MISSING_VOLUME_ZSCORE_20"
    ATR_NOT_POSITIVE = "ATR_NOT_POSITIVE"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    HTF_BIAS_NOT_DIRECTIONAL = "HTF_BIAS_NOT_DIRECTIONAL"
    ONE_HOUR_DIRECTION_MISMATCH = "ONE_HOUR_DIRECTION_MISMATCH"
    FOUR_HOUR_DIRECTION_MISMATCH = "FOUR_HOUR_DIRECTION_MISMATCH"
    ENTRY_DIRECTION_OPPOSED = "ENTRY_DIRECTION_OPPOSED"
    EMA_ALIGNMENT_FAILED = "EMA_ALIGNMENT_FAILED"
    VALUE_ZONE_NOT_TOUCHED = "VALUE_ZONE_NOT_TOUCHED"
    PULLBACK_TOO_DEEP = "PULLBACK_TOO_DEEP"
    EMA_RECLAIM_FAILED = "EMA_RECLAIM_FAILED"
    CANDLE_DIRECTION_FAILED = "CANDLE_DIRECTION_FAILED"
    MOMENTUM_RECOVERY_FAILED = "MOMENTUM_RECOVERY_FAILED"
    RSI_CONFIRMATION_FAILED = "RSI_CONFIRMATION_FAILED"
    VOLUME_CONFIRMATION_FAILED = "VOLUME_CONFIRMATION_FAILED"
    MISSING_CONFIRMED_LOW = "MISSING_CONFIRMED_LOW"
    MISSING_CONFIRMED_HIGH = "MISSING_CONFIRMED_HIGH"
    INVALIDATION_WRONG_SIDE = "INVALIDATION_WRONG_SIDE"
    INVALIDATION_TOO_WIDE = "INVALIDATION_TOO_WIDE"


@dataclass(frozen=True, slots=True)
class StrategyEvaluationContext:
    """Synchronized information observable for one symbol at one closed 15m bar."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    candle_15m: Candle
    features_15m: FeatureSnapshot
    structure_15m: StructureSnapshot
    multi_timeframe_regime: MultiTimeframeRegimeSnapshot

    def __post_init__(self) -> None:
        observed_at = require_utc(self.observed_at, field="strategy context observed_at")
        candle = self.candle_15m
        feature = self.features_15m
        structure = self.structure_15m
        regime = self.multi_timeframe_regime
        if self.symbol.base not in {"BTC", "ETH"}:
            raise StrategyContextError("strategy symbol is outside V1")
        if (
            candle.symbol != self.symbol
            or candle.timeframe is not Timeframe.MINUTES_15
            or candle.close_time != observed_at
        ):
            raise StrategyContextError("15m candle identity or close time mismatch")
        if (
            feature.source_id != self.source_id
            or feature.symbol != self.symbol
            or feature.timeframe is not Timeframe.MINUTES_15
            or feature.observed_at != observed_at
            or feature.candle_open_time != candle.open_time
            or feature.candle_close_time != candle.close_time
        ):
            raise StrategyContextError("15m feature identity or candle time mismatch")
        if (
            structure.source_id != self.source_id
            or structure.symbol != self.symbol
            or structure.timeframe is not Timeframe.MINUTES_15
            or structure.observed_at != observed_at
            or structure.bars_seen != feature.bars_seen
        ):
            raise StrategyContextError("15m structure identity, time, or bars_seen mismatch")
        if (
            regime.source_id != self.source_id
            or regime.symbol != self.symbol
            or regime.observed_at != observed_at
            or regime.regime_15m.source_id != self.source_id
            or regime.regime_15m.symbol != self.symbol
            or regime.regime_15m.timeframe is not Timeframe.MINUTES_15
            or regime.regime_15m.observed_at != observed_at
        ):
            raise StrategyContextError("multi-timeframe anchor mismatch")
        for pivot in (structure.latest_confirmed_high, structure.latest_confirmed_low):
            if pivot is not None and (
                pivot.source_id != self.source_id
                or pivot.symbol != self.symbol
                or pivot.timeframe is not Timeframe.MINUTES_15
                or pivot.confirmed_at > observed_at
            ):
                raise StrategyContextError("confirmed pivot identity or causality mismatch")


@dataclass(frozen=True, slots=True)
class TrendPullbackRuleAudit:
    """Decimal rule inputs and derived geometry used by one evaluation."""

    ema_20: Decimal | None
    ema_50: Decimal | None
    atr_14: Decimal | None
    rsi_14: Decimal | None
    momentum_4: Decimal | None
    volume_zscore_20: Decimal | None
    value_zone_boundary: Decimal | None
    structural_pivot_price: Decimal | None
    invalidation_distance: Decimal | None


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    """Immutable candidate assessment; reference prices are not execution fills/stops."""

    strategy_id: str
    strategy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    action: CandidateAction
    reference_price: Decimal | None
    invalidation_reference_price: Decimal | None
    reasons_for: tuple[StrategyReasonCode, ...]
    reasons_against: tuple[StrategyReasonCode, ...]
    rule_audit: TrendPullbackRuleAudit


@runtime_checkable
class Strategy(Protocol):
    """Shared strategy interface usable unchanged by every environment adapter."""

    @property
    def strategy_id(self) -> str:
        """Return the immutable strategy identifier."""

    @property
    def strategy_version(self) -> str:
        """Return the immutable strategy rule version."""

    def evaluate(self, context: StrategyEvaluationContext) -> StrategyEvaluation:
        """Evaluate one already-observed context without side effects."""
