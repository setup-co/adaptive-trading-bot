"""Stateless causal Trend Pullback V1 candidate evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Timeframe
from adaptive_trading_bot.features import FeatureSnapshot
from adaptive_trading_bot.regime import Direction, HigherTimeframeBias
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_STRATEGY_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class StrategyReasonCode(StrEnum):
    """Stable, canonically ordered Trend Pullback evidence and failure codes."""

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
class TrendPullbackConfiguration:
    """Fixed, validated Trend Pullback V1 thresholds; no optimization is performed."""

    entry_timeframe: Timeframe = Timeframe.MINUTES_15
    value_zone_atr_tolerance: Decimal = Decimal("0.25")
    max_invalidation_distance_atr: Decimal = Decimal("3.0")
    minimum_volume_zscore: Decimal = Decimal(0)
    long_min_rsi: Decimal = Decimal(50)
    short_max_rsi: Decimal = Decimal(50)

    def __post_init__(self) -> None:
        if self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ValueError("Trend Pullback V1 entry_timeframe must be 15m")
        for name, value in (
            ("value_zone_atr_tolerance", self.value_zone_atr_tolerance),
            ("max_invalidation_distance_atr", self.max_invalidation_distance_atr),
            ("minimum_volume_zscore", self.minimum_volume_zscore),
            ("long_min_rsi", self.long_min_rsi),
            ("short_max_rsi", self.short_max_rsi),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.value_zone_atr_tolerance < 0:
            raise ValueError("value_zone_atr_tolerance must be non-negative")
        if self.max_invalidation_distance_atr <= 0:
            raise ValueError("max_invalidation_distance_atr must be positive")
        for name, value in (
            ("long_min_rsi", self.long_min_rsi),
            ("short_max_rsi", self.short_max_rsi),
        ):
            if value < 0 or value > 100:
                raise ValueError(f"{name} must be in [0, 100]")


@dataclass(frozen=True, slots=True)
class TrendPullbackStrategy:
    """Evaluate one closed 15m bar without memory, orders, sizing, or risk approval."""

    configuration: TrendPullbackConfiguration = field(default_factory=TrendPullbackConfiguration)
    strategy_id: str = field(init=False, default="trend_pullback")
    strategy_version: str = field(init=False, default="1.0.0")

    def evaluate(
        self, context: StrategyEvaluationContext
    ) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
        """Return a deterministic candidate or explicit NO_TRADE assessment."""

        with localcontext(_STRATEGY_DECIMAL_CONTEXT):
            common_failures = self._common_failures(context)
            bias = context.multi_timeframe_regime.higher_timeframe_bias
            if bias is HigherTimeframeBias.UP:
                return self._evaluate_long(context, common_failures)
            if bias is HigherTimeframeBias.DOWN:
                return self._evaluate_short(context, common_failures)
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (*common_failures, StrategyReasonCode.HTF_BIAS_NOT_DIRECTIONAL),
                TrendPullbackRuleAudit(
                    ema_20=context.features_15m.ema_20,
                    ema_50=context.features_15m.ema_50,
                    atr_14=context.features_15m.atr_14,
                    rsi_14=context.features_15m.rsi_14,
                    momentum_4=context.features_15m.momentum_4,
                    volume_zscore_20=context.features_15m.volume_zscore_20,
                    value_zone_boundary=None,
                    structural_pivot_price=None,
                    invalidation_distance=None,
                ),
            )

    def _common_failures(
        self, context: StrategyEvaluationContext
    ) -> tuple[StrategyReasonCode, ...]:
        feature = context.features_15m
        regime = context.multi_timeframe_regime
        failures: list[StrategyReasonCode] = []
        if not regime.is_fully_ready:
            failures.append(StrategyReasonCode.CONTEXT_NOT_READY)
        if not feature.is_fully_warmed:
            failures.append(StrategyReasonCode.FEATURES_NOT_FULLY_WARMED)
        for value, reason in (
            (feature.ema_20, StrategyReasonCode.MISSING_EMA_20),
            (feature.ema_50, StrategyReasonCode.MISSING_EMA_50),
            (feature.atr_14, StrategyReasonCode.MISSING_ATR_14),
            (feature.rsi_14, StrategyReasonCode.MISSING_RSI_14),
            (feature.momentum_4, StrategyReasonCode.MISSING_MOMENTUM_4),
            (feature.volume_zscore_20, StrategyReasonCode.MISSING_VOLUME_ZSCORE_20),
        ):
            if value is None:
                failures.append(reason)
        if feature.atr_14 is not None and feature.atr_14 <= 0:
            failures.append(StrategyReasonCode.ATR_NOT_POSITIVE)
        if regime.has_extreme_volatility:
            failures.append(StrategyReasonCode.EXTREME_VOLATILITY)
        return tuple(failures)

    def _evaluate_long(
        self,
        context: StrategyEvaluationContext,
        common_failures: tuple[StrategyReasonCode, ...],
    ) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
        feature = context.features_15m
        candle = context.candle_15m
        regime = context.multi_timeframe_regime
        failures = list(common_failures)
        if regime.regime_1h is None or regime.regime_1h.direction is not Direction.UP:
            failures.append(StrategyReasonCode.ONE_HOUR_DIRECTION_MISMATCH)
        if regime.regime_4h is None or regime.regime_4h.direction is not Direction.UP:
            failures.append(StrategyReasonCode.FOUR_HOUR_DIRECTION_MISMATCH)
        if regime.regime_15m.direction not in {Direction.UP, Direction.NEUTRAL}:
            failures.append(StrategyReasonCode.ENTRY_DIRECTION_OPPOSED)

        ema20 = feature.ema_20
        ema50 = feature.ema_50
        atr = feature.atr_14
        boundary = None
        if ema20 is not None and ema50 is not None and ema20 <= ema50:
            failures.append(StrategyReasonCode.EMA_ALIGNMENT_FAILED)
        if ema50 is not None and atr is not None:
            boundary = ema50 - self.configuration.value_zone_atr_tolerance * atr
        if ema20 is not None and candle.low > ema20:
            failures.append(StrategyReasonCode.VALUE_ZONE_NOT_TOUCHED)
        if boundary is not None and candle.low < boundary:
            failures.append(StrategyReasonCode.PULLBACK_TOO_DEEP)
        if ema20 is not None and candle.close <= ema20:
            failures.append(StrategyReasonCode.EMA_RECLAIM_FAILED)
        if candle.close <= candle.open:
            failures.append(StrategyReasonCode.CANDLE_DIRECTION_FAILED)
        if feature.momentum_4 is not None and feature.momentum_4 <= 0:
            failures.append(StrategyReasonCode.MOMENTUM_RECOVERY_FAILED)
        if feature.rsi_14 is not None and feature.rsi_14 < self.configuration.long_min_rsi:
            failures.append(StrategyReasonCode.RSI_CONFIRMATION_FAILED)
        if (
            feature.volume_zscore_20 is not None
            and feature.volume_zscore_20 < self.configuration.minimum_volume_zscore
        ):
            failures.append(StrategyReasonCode.VOLUME_CONFIRMATION_FAILED)

        pivot = context.structure_15m.latest_confirmed_low
        pivot_price = None if pivot is None else pivot.pivot_price
        distance = None
        if pivot_price is None:
            failures.append(StrategyReasonCode.MISSING_CONFIRMED_LOW)
        elif pivot_price >= candle.close:
            failures.append(StrategyReasonCode.INVALIDATION_WRONG_SIDE)
        else:
            distance = candle.close - pivot_price
            if (
                atr is not None
                and distance > self.configuration.max_invalidation_distance_atr * atr
            ):
                failures.append(StrategyReasonCode.INVALIDATION_TOO_WIDE)
        audit = self._audit(feature, boundary, pivot_price, distance)
        if failures:
            return self._result(context, CandidateAction.NO_TRADE, tuple(failures), audit)
        return self._candidate(
            context,
            CandidateAction.LONG_CANDIDATE,
            pivot_price,
            audit,
        )

    def _evaluate_short(
        self,
        context: StrategyEvaluationContext,
        common_failures: tuple[StrategyReasonCode, ...],
    ) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
        feature = context.features_15m
        candle = context.candle_15m
        regime = context.multi_timeframe_regime
        failures = list(common_failures)
        if regime.regime_1h is None or regime.regime_1h.direction is not Direction.DOWN:
            failures.append(StrategyReasonCode.ONE_HOUR_DIRECTION_MISMATCH)
        if regime.regime_4h is None or regime.regime_4h.direction is not Direction.DOWN:
            failures.append(StrategyReasonCode.FOUR_HOUR_DIRECTION_MISMATCH)
        if regime.regime_15m.direction not in {Direction.DOWN, Direction.NEUTRAL}:
            failures.append(StrategyReasonCode.ENTRY_DIRECTION_OPPOSED)

        ema20 = feature.ema_20
        ema50 = feature.ema_50
        atr = feature.atr_14
        boundary = None
        if ema20 is not None and ema50 is not None and ema20 >= ema50:
            failures.append(StrategyReasonCode.EMA_ALIGNMENT_FAILED)
        if ema50 is not None and atr is not None:
            boundary = ema50 + self.configuration.value_zone_atr_tolerance * atr
        if ema20 is not None and candle.high < ema20:
            failures.append(StrategyReasonCode.VALUE_ZONE_NOT_TOUCHED)
        if boundary is not None and candle.high > boundary:
            failures.append(StrategyReasonCode.PULLBACK_TOO_DEEP)
        if ema20 is not None and candle.close >= ema20:
            failures.append(StrategyReasonCode.EMA_RECLAIM_FAILED)
        if candle.close >= candle.open:
            failures.append(StrategyReasonCode.CANDLE_DIRECTION_FAILED)
        if feature.momentum_4 is not None and feature.momentum_4 >= 0:
            failures.append(StrategyReasonCode.MOMENTUM_RECOVERY_FAILED)
        if feature.rsi_14 is not None and feature.rsi_14 > self.configuration.short_max_rsi:
            failures.append(StrategyReasonCode.RSI_CONFIRMATION_FAILED)
        if (
            feature.volume_zscore_20 is not None
            and feature.volume_zscore_20 < self.configuration.minimum_volume_zscore
        ):
            failures.append(StrategyReasonCode.VOLUME_CONFIRMATION_FAILED)

        pivot = context.structure_15m.latest_confirmed_high
        pivot_price = None if pivot is None else pivot.pivot_price
        distance = None
        if pivot_price is None:
            failures.append(StrategyReasonCode.MISSING_CONFIRMED_HIGH)
        elif pivot_price <= candle.close:
            failures.append(StrategyReasonCode.INVALIDATION_WRONG_SIDE)
        else:
            distance = pivot_price - candle.close
            if (
                atr is not None
                and distance > self.configuration.max_invalidation_distance_atr * atr
            ):
                failures.append(StrategyReasonCode.INVALIDATION_TOO_WIDE)
        audit = self._audit(feature, boundary, pivot_price, distance)
        if failures:
            return self._result(context, CandidateAction.NO_TRADE, tuple(failures), audit)
        return self._candidate(
            context,
            CandidateAction.SHORT_CANDIDATE,
            pivot_price,
            audit,
        )

    @staticmethod
    def _audit(
        feature: FeatureSnapshot,
        boundary: Decimal | None,
        pivot_price: Decimal | None,
        distance: Decimal | None,
    ) -> TrendPullbackRuleAudit:
        return TrendPullbackRuleAudit(
            ema_20=feature.ema_20,
            ema_50=feature.ema_50,
            atr_14=feature.atr_14,
            rsi_14=feature.rsi_14,
            momentum_4=feature.momentum_4,
            volume_zscore_20=feature.volume_zscore_20,
            value_zone_boundary=boundary,
            structural_pivot_price=pivot_price,
            invalidation_distance=distance,
        )

    def _candidate(
        self,
        context: StrategyEvaluationContext,
        action: CandidateAction,
        pivot_price: Decimal | None,
        audit: TrendPullbackRuleAudit,
    ) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
        assert pivot_price is not None
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            action=action,
            reference_price=context.candle_15m.close,
            invalidation_reference_price=pivot_price,
            reasons_for=(
                StrategyReasonCode.CONTEXT_READY,
                StrategyReasonCode.REQUIRED_FEATURES_AVAILABLE,
                StrategyReasonCode.VOLATILITY_ACCEPTABLE,
                StrategyReasonCode.HTF_TREND_CONFIRMED,
                StrategyReasonCode.ENTRY_DIRECTION_COMPATIBLE,
                StrategyReasonCode.EMA_TREND_ALIGNED,
                StrategyReasonCode.VALUE_ZONE_TOUCHED,
                StrategyReasonCode.PULLBACK_DEPTH_ACCEPTABLE,
                StrategyReasonCode.EMA_RECLAIM_CONFIRMED,
                StrategyReasonCode.CANDLE_DIRECTION_CONFIRMED,
                StrategyReasonCode.MOMENTUM_RECOVERY_CONFIRMED,
                StrategyReasonCode.RSI_CONFIRMATION,
                StrategyReasonCode.VOLUME_CONFIRMATION,
                StrategyReasonCode.STRUCTURAL_PIVOT_AVAILABLE,
                StrategyReasonCode.INVALIDATION_GEOMETRY_VALID,
            ),
            reasons_against=(),
            rule_audit=audit,
        )

    def _result(
        self,
        context: StrategyEvaluationContext,
        action: CandidateAction,
        failures: tuple[StrategyReasonCode, ...],
        audit: TrendPullbackRuleAudit,
    ) -> StrategyEvaluation[StrategyReasonCode, TrendPullbackRuleAudit]:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            action=action,
            reference_price=None,
            invalidation_reference_price=None,
            reasons_for=(),
            reasons_against=failures,
            rule_audit=audit,
        )
