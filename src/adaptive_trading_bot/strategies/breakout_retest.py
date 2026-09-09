"""Causal stateful Breakout + Retest V1 candidate evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.features import ConfirmedPivot, PivotKind
from adaptive_trading_bot.regime import Direction, HigherTimeframeBias
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class BreakoutRetestStateError(Exception):
    """A context stream violates deterministic per-series progression."""


class BreakoutSide(StrEnum):
    """Direction of an immutable armed breakout."""

    LONG = "LONG"
    SHORT = "SHORT"


class BreakoutRetestReasonCode(StrEnum):
    """Canonical Breakout + Retest evidence and counter-evidence."""

    CONTEXT_READY = "CONTEXT_READY"
    REQUIRED_FEATURES_AVAILABLE = "REQUIRED_FEATURES_AVAILABLE"
    VOLATILITY_ACCEPTABLE = "VOLATILITY_ACCEPTABLE"
    HTF_TREND_CONFIRMED = "HTF_TREND_CONFIRMED"
    ENTRY_DIRECTION_COMPATIBLE = "ENTRY_DIRECTION_COMPATIBLE"
    EMA_TREND_ALIGNED = "EMA_TREND_ALIGNED"
    BREAKOUT_LEVEL_AVAILABLE = "BREAKOUT_LEVEL_AVAILABLE"
    BREAKOUT_BUFFER_CONFIRMED = "BREAKOUT_BUFFER_CONFIRMED"
    CANDLE_DIRECTION_CONFIRMED = "CANDLE_DIRECTION_CONFIRMED"
    MOMENTUM_CONFIRMATION = "MOMENTUM_CONFIRMATION"
    RSI_CONFIRMATION = "RSI_CONFIRMATION"
    BREAKOUT_VOLUME_CONFIRMED = "BREAKOUT_VOLUME_CONFIRMED"
    BREAKOUT_ARMED = "BREAKOUT_ARMED"
    WAITING_FOR_RETEST = "WAITING_FOR_RETEST"
    RETEST_ZONE_TOUCHED = "RETEST_ZONE_TOUCHED"
    RECLAIM_CONFIRMED = "RECLAIM_CONFIRMED"
    REJECTION_CONFIRMED = "REJECTION_CONFIRMED"
    RETEST_VOLUME_CONFIRMED = "RETEST_VOLUME_CONFIRMED"
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
    HTF_TREND_LOST = "HTF_TREND_LOST"
    ENTRY_DIRECTION_OPPOSED = "ENTRY_DIRECTION_OPPOSED"
    EMA_ALIGNMENT_FAILED = "EMA_ALIGNMENT_FAILED"
    MISSING_BREAKOUT_PIVOT = "MISSING_BREAKOUT_PIVOT"
    BREAKOUT_BUFFER_FAILED = "BREAKOUT_BUFFER_FAILED"
    CANDLE_DIRECTION_FAILED = "CANDLE_DIRECTION_FAILED"
    MOMENTUM_CONFIRMATION_FAILED = "MOMENTUM_CONFIRMATION_FAILED"
    RSI_CONFIRMATION_FAILED = "RSI_CONFIRMATION_FAILED"
    BREAKOUT_VOLUME_FAILED = "BREAKOUT_VOLUME_FAILED"
    SETUP_CANCELLED = "SETUP_CANCELLED"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    RETEST_TOO_DEEP = "RETEST_TOO_DEEP"
    RECLAIM_FAILED = "RECLAIM_FAILED"
    REJECTION_FAILED = "REJECTION_FAILED"
    VOLUME_CONFIRMATION_FAILED = "VOLUME_CONFIRMATION_FAILED"
    INVALIDATION_WRONG_SIDE = "INVALIDATION_WRONG_SIDE"
    INVALIDATION_TOO_WIDE = "INVALIDATION_TOO_WIDE"


@dataclass(frozen=True, slots=True)
class ArmedBreakout:
    """Locked causal breakout state for one source and symbol."""

    side: BreakoutSide
    breakout_level: Decimal
    breakout_observed_at: datetime
    breakout_bar_sequence: int
    breakout_pivot: ConfirmedPivot
    breakout_atr: Decimal
    bars_waited: int = 0


@dataclass(frozen=True, slots=True)
class BreakoutRetestRuleAudit:
    """Structured inputs and derived boundaries for one state transition."""

    armed_side: BreakoutSide | None
    breakout_level: Decimal | None
    breakout_observed_at: datetime | None
    breakout_bar_sequence: int | None
    breakout_pivot_kind: PivotKind | None
    breakout_pivot_open_time: datetime | None
    breakout_pivot_confirmed_at: datetime | None
    breakout_pivot_price: Decimal | None
    current_pivot_price: Decimal | None
    bars_waited: int | None
    atr_14: Decimal | None
    required_breakout_close: Decimal | None
    retest_floor: Decimal | None
    retest_ceiling: Decimal | None
    retest_extreme: Decimal | None
    invalidation_distance: Decimal | None


@dataclass(frozen=True, slots=True)
class BreakoutRetestConfiguration:
    """Fixed validated Breakout + Retest V1 thresholds."""

    entry_timeframe: Timeframe = Timeframe.MINUTES_15
    breakout_buffer_atr: Decimal = Decimal("0.10")
    retest_tolerance_atr: Decimal = Decimal("0.20")
    max_retest_wait_bars: int = 8
    minimum_breakout_volume_zscore: Decimal = Decimal("0.50")
    minimum_retest_volume_zscore: Decimal = Decimal(0)
    long_min_rsi: Decimal = Decimal(50)
    short_max_rsi: Decimal = Decimal(50)
    max_invalidation_distance_atr: Decimal = Decimal("3.0")

    def __post_init__(self) -> None:
        if self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ValueError("Breakout + Retest V1 entry_timeframe must be 15m")
        for name, value in (
            ("breakout_buffer_atr", self.breakout_buffer_atr),
            ("retest_tolerance_atr", self.retest_tolerance_atr),
            ("minimum_breakout_volume_zscore", self.minimum_breakout_volume_zscore),
            ("minimum_retest_volume_zscore", self.minimum_retest_volume_zscore),
            ("long_min_rsi", self.long_min_rsi),
            ("short_max_rsi", self.short_max_rsi),
            ("max_invalidation_distance_atr", self.max_invalidation_distance_atr),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.breakout_buffer_atr < 0 or self.retest_tolerance_atr < 0:
            raise ValueError("ATR buffers must be non-negative")
        if self.max_invalidation_distance_atr <= 0:
            raise ValueError("max_invalidation_distance_atr must be positive")
        if type(self.max_retest_wait_bars) is not int or self.max_retest_wait_bars < 1:
            raise ValueError("max_retest_wait_bars must be a positive integer")
        for name, value in (
            ("long_min_rsi", self.long_min_rsi),
            ("short_max_rsi", self.short_max_rsi),
        ):
            if value < 0 or value > 100:
                raise ValueError(f"{name} must be in [0, 100]")


@dataclass(frozen=True, slots=True)
class _SeriesKey:
    source_value: str
    symbol: TradingSymbol


@dataclass(frozen=True, slots=True)
class _SeriesState:
    last_observed_at: datetime
    sequence: int
    armed: ArmedBreakout | None


Evaluation = StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit]


@dataclass(frozen=True, slots=True)
class BreakoutRetestStrategy:
    """Evaluate ordered 15m contexts with explicit isolated armed state."""

    configuration: BreakoutRetestConfiguration = field(default_factory=BreakoutRetestConfiguration)
    strategy_id: str = field(init=False, default="breakout_retest")
    strategy_version: str = field(init=False, default="1.0.0")
    _states: dict[_SeriesKey, _SeriesState] = field(
        init=False, default_factory=dict, repr=False, compare=False
    )

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        """Validate, evaluate, and atomically commit one deterministic transition."""

        key = _SeriesKey(context.source_id.value, context.symbol)
        previous = self._states.get(key)
        if previous is not None:
            expected = previous.last_observed_at + Timeframe.MINUTES_15.duration
            if context.observed_at != expected:
                raise BreakoutRetestStateError(
                    "observed_at must advance by exactly one 15m interval"
                )
        sequence = 1 if previous is None else previous.sequence + 1
        with localcontext(_DECIMAL_CONTEXT):
            if previous is not None and previous.armed is not None:
                result, armed = self._evaluate_armed(context, previous.armed)
            else:
                result, armed = self._evaluate_unarmed(context, sequence)
        self._states[key] = _SeriesState(context.observed_at, sequence, armed)
        return result

    def armed_state(self, source_id: SourceIdentity, symbol: TradingSymbol) -> ArmedBreakout | None:
        """Return the immutable armed state for audit/testing, if one exists."""

        state = self._states.get(_SeriesKey(source_id.value, symbol))
        return None if state is None else state.armed

    def _evaluate_unarmed(
        self, context: StrategyEvaluationContext, sequence: int
    ) -> tuple[Evaluation, ArmedBreakout | None]:
        failures = self._common_failures(context)
        bias = context.multi_timeframe_regime.higher_timeframe_bias
        if bias is HigherTimeframeBias.UP:
            return self._arm(context, BreakoutSide.LONG, sequence, failures)
        if bias is HigherTimeframeBias.DOWN:
            return self._arm(context, BreakoutSide.SHORT, sequence, failures)
        failures = (*failures, BreakoutRetestReasonCode.HTF_BIAS_NOT_DIRECTIONAL)
        return self._no_trade(context, failures, self._empty_audit(context)), None

    def _arm(
        self,
        context: StrategyEvaluationContext,
        side: BreakoutSide,
        sequence: int,
        common_failures: tuple[BreakoutRetestReasonCode, ...],
    ) -> tuple[Evaluation, ArmedBreakout | None]:
        feature = context.features_15m
        candle = context.candle_15m
        failures = list(common_failures)
        failures.extend(self._trend_failures(context, side))
        pivot = (
            context.structure_15m.latest_confirmed_high
            if side is BreakoutSide.LONG
            else context.structure_15m.latest_confirmed_low
        )
        expected_kind = PivotKind.HIGH if side is BreakoutSide.LONG else PivotKind.LOW
        if pivot is None or pivot.kind is not expected_kind:
            failures.append(BreakoutRetestReasonCode.MISSING_BREAKOUT_PIVOT)
        atr = feature.atr_14
        level = None if pivot is None else pivot.pivot_price
        required_close = None
        if level is not None and atr is not None:
            offset = self.configuration.breakout_buffer_atr * atr
            required_close = level + offset if side is BreakoutSide.LONG else level - offset
            buffer_failed = (
                candle.close < required_close
                if side is BreakoutSide.LONG
                else candle.close > required_close
            )
            if buffer_failed:
                failures.append(BreakoutRetestReasonCode.BREAKOUT_BUFFER_FAILED)
        if (side is BreakoutSide.LONG and candle.close <= candle.open) or (
            side is BreakoutSide.SHORT and candle.close >= candle.open
        ):
            failures.append(BreakoutRetestReasonCode.CANDLE_DIRECTION_FAILED)
        if feature.momentum_4 is not None and (
            (side is BreakoutSide.LONG and feature.momentum_4 <= 0)
            or (side is BreakoutSide.SHORT and feature.momentum_4 >= 0)
        ):
            failures.append(BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION_FAILED)
        if feature.rsi_14 is not None and (
            (side is BreakoutSide.LONG and feature.rsi_14 < self.configuration.long_min_rsi)
            or (side is BreakoutSide.SHORT and feature.rsi_14 > self.configuration.short_max_rsi)
        ):
            failures.append(BreakoutRetestReasonCode.RSI_CONFIRMATION_FAILED)
        if (
            feature.volume_zscore_20 is not None
            and feature.volume_zscore_20 < self.configuration.minimum_breakout_volume_zscore
        ):
            failures.append(BreakoutRetestReasonCode.BREAKOUT_VOLUME_FAILED)
        audit = self._arming_audit(context, side, pivot, required_close, sequence)
        if failures or pivot is None or atr is None:
            return self._no_trade(context, tuple(failures), audit), None
        armed = ArmedBreakout(
            side=side,
            breakout_level=pivot.pivot_price,
            breakout_observed_at=context.observed_at,
            breakout_bar_sequence=sequence,
            breakout_pivot=pivot,
            breakout_atr=atr,
        )
        reasons = (
            BreakoutRetestReasonCode.CONTEXT_READY,
            BreakoutRetestReasonCode.REQUIRED_FEATURES_AVAILABLE,
            BreakoutRetestReasonCode.VOLATILITY_ACCEPTABLE,
            BreakoutRetestReasonCode.HTF_TREND_CONFIRMED,
            BreakoutRetestReasonCode.ENTRY_DIRECTION_COMPATIBLE,
            BreakoutRetestReasonCode.EMA_TREND_ALIGNED,
            BreakoutRetestReasonCode.BREAKOUT_LEVEL_AVAILABLE,
            BreakoutRetestReasonCode.BREAKOUT_BUFFER_CONFIRMED,
            BreakoutRetestReasonCode.CANDLE_DIRECTION_CONFIRMED,
            BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION,
            BreakoutRetestReasonCode.RSI_CONFIRMATION,
            BreakoutRetestReasonCode.BREAKOUT_VOLUME_CONFIRMED,
            BreakoutRetestReasonCode.BREAKOUT_ARMED,
        )
        return self._result(context, CandidateAction.NO_TRADE, reasons, (), audit), armed

    def _evaluate_armed(
        self, context: StrategyEvaluationContext, original: ArmedBreakout
    ) -> tuple[Evaluation, ArmedBreakout | None]:
        armed = replace(original, bars_waited=original.bars_waited + 1)
        common_failures = self._common_failures(context)
        if common_failures:
            reasons = (*common_failures, BreakoutRetestReasonCode.SETUP_CANCELLED)
            return self._no_trade(context, reasons, self._armed_audit(context, armed)), None
        if armed.bars_waited > self.configuration.max_retest_wait_bars:
            return self._no_trade(
                context,
                (BreakoutRetestReasonCode.SETUP_EXPIRED,),
                self._armed_audit(context, armed),
            ), None
        atr = context.features_15m.atr_14
        assert atr is not None
        tolerance = self.configuration.retest_tolerance_atr * atr
        floor = armed.breakout_level - tolerance
        ceiling = armed.breakout_level + tolerance
        extreme = (
            context.candle_15m.low if armed.side is BreakoutSide.LONG else context.candle_15m.high
        )
        too_deep = extreme < floor if armed.side is BreakoutSide.LONG else extreme > ceiling
        audit = self._armed_audit(context, armed, floor, ceiling, extreme)
        if too_deep:
            return self._no_trade(context, (BreakoutRetestReasonCode.RETEST_TOO_DEEP,), audit), None
        if self._higher_timeframe_trend_lost(context, armed.side):
            return self._no_trade(
                context,
                (BreakoutRetestReasonCode.HTF_TREND_LOST,),
                audit,
            ), None
        untouched = extreme > ceiling if armed.side is BreakoutSide.LONG else extreme < floor
        if untouched:
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (BreakoutRetestReasonCode.WAITING_FOR_RETEST,),
                (),
                audit,
            ), armed
        return self._evaluate_touched_retest(context, armed, audit)

    def _evaluate_touched_retest(
        self,
        context: StrategyEvaluationContext,
        armed: ArmedBreakout,
        audit: BreakoutRetestRuleAudit,
    ) -> tuple[Evaluation, ArmedBreakout | None]:
        candle = context.candle_15m
        feature = context.features_15m
        failures = list(self._entry_trend_failures(context, armed.side))
        if (armed.side is BreakoutSide.LONG and candle.close <= armed.breakout_level) or (
            armed.side is BreakoutSide.SHORT and candle.close >= armed.breakout_level
        ):
            failures.append(
                BreakoutRetestReasonCode.RECLAIM_FAILED
                if armed.side is BreakoutSide.LONG
                else BreakoutRetestReasonCode.REJECTION_FAILED
            )
        if (armed.side is BreakoutSide.LONG and candle.close <= candle.open) or (
            armed.side is BreakoutSide.SHORT and candle.close >= candle.open
        ):
            failures.append(BreakoutRetestReasonCode.CANDLE_DIRECTION_FAILED)
        if feature.momentum_4 is not None and (
            (armed.side is BreakoutSide.LONG and feature.momentum_4 <= 0)
            or (armed.side is BreakoutSide.SHORT and feature.momentum_4 >= 0)
        ):
            failures.append(BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION_FAILED)
        if feature.rsi_14 is not None and (
            (armed.side is BreakoutSide.LONG and feature.rsi_14 < self.configuration.long_min_rsi)
            or (
                armed.side is BreakoutSide.SHORT
                and feature.rsi_14 > self.configuration.short_max_rsi
            )
        ):
            failures.append(BreakoutRetestReasonCode.RSI_CONFIRMATION_FAILED)
        if (
            feature.volume_zscore_20 is not None
            and feature.volume_zscore_20 < self.configuration.minimum_retest_volume_zscore
        ):
            failures.append(BreakoutRetestReasonCode.VOLUME_CONFIRMATION_FAILED)

        invalidation = candle.low if armed.side is BreakoutSide.LONG else candle.high
        distance = (
            candle.close - invalidation
            if armed.side is BreakoutSide.LONG
            else invalidation - candle.close
        )
        if distance <= 0:
            failures.append(BreakoutRetestReasonCode.INVALIDATION_WRONG_SIDE)
        atr = feature.atr_14
        assert atr is not None
        too_wide = distance > self.configuration.max_invalidation_distance_atr * atr
        if too_wide:
            failures.append(BreakoutRetestReasonCode.INVALIDATION_TOO_WIDE)
        audit = replace(audit, invalidation_distance=distance)
        if failures:
            retained = None if too_wide else armed
            return self._no_trade(context, tuple(failures), audit), retained
        action = (
            CandidateAction.LONG_CANDIDATE
            if armed.side is BreakoutSide.LONG
            else CandidateAction.SHORT_CANDIDATE
        )
        recovery = (
            BreakoutRetestReasonCode.RECLAIM_CONFIRMED
            if armed.side is BreakoutSide.LONG
            else BreakoutRetestReasonCode.REJECTION_CONFIRMED
        )
        reasons = (
            BreakoutRetestReasonCode.CONTEXT_READY,
            BreakoutRetestReasonCode.HTF_TREND_CONFIRMED,
            BreakoutRetestReasonCode.ENTRY_DIRECTION_COMPATIBLE,
            BreakoutRetestReasonCode.EMA_TREND_ALIGNED,
            BreakoutRetestReasonCode.RETEST_ZONE_TOUCHED,
            recovery,
            BreakoutRetestReasonCode.CANDLE_DIRECTION_CONFIRMED,
            BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION,
            BreakoutRetestReasonCode.RSI_CONFIRMATION,
            BreakoutRetestReasonCode.RETEST_VOLUME_CONFIRMED,
            BreakoutRetestReasonCode.INVALIDATION_GEOMETRY_VALID,
        )
        return (
            self._result(
                context,
                action,
                reasons,
                (),
                audit,
                reference_price=candle.close,
                invalidation_reference_price=invalidation,
            ),
            None,
        )

    def _common_failures(
        self, context: StrategyEvaluationContext
    ) -> tuple[BreakoutRetestReasonCode, ...]:
        feature = context.features_15m
        failures: list[BreakoutRetestReasonCode] = []
        if not context.multi_timeframe_regime.is_fully_ready:
            failures.append(BreakoutRetestReasonCode.CONTEXT_NOT_READY)
        if not feature.is_fully_warmed:
            failures.append(BreakoutRetestReasonCode.FEATURES_NOT_FULLY_WARMED)
        for value, reason in (
            (feature.ema_20, BreakoutRetestReasonCode.MISSING_EMA_20),
            (feature.ema_50, BreakoutRetestReasonCode.MISSING_EMA_50),
            (feature.atr_14, BreakoutRetestReasonCode.MISSING_ATR_14),
            (feature.rsi_14, BreakoutRetestReasonCode.MISSING_RSI_14),
            (feature.momentum_4, BreakoutRetestReasonCode.MISSING_MOMENTUM_4),
            (
                feature.volume_zscore_20,
                BreakoutRetestReasonCode.MISSING_VOLUME_ZSCORE_20,
            ),
        ):
            if value is None:
                failures.append(reason)
        if feature.atr_14 is not None and feature.atr_14 <= 0:
            failures.append(BreakoutRetestReasonCode.ATR_NOT_POSITIVE)
        if context.multi_timeframe_regime.has_extreme_volatility:
            failures.append(BreakoutRetestReasonCode.EXTREME_VOLATILITY)
        return tuple(failures)

    @staticmethod
    def _trend_failures(
        context: StrategyEvaluationContext, side: BreakoutSide
    ) -> tuple[BreakoutRetestReasonCode, ...]:
        failures: list[BreakoutRetestReasonCode] = []
        if BreakoutRetestStrategy._higher_timeframe_trend_lost(context, side):
            failures.append(BreakoutRetestReasonCode.HTF_TREND_LOST)
        failures.extend(BreakoutRetestStrategy._entry_trend_failures(context, side))
        return tuple(failures)

    @staticmethod
    def _higher_timeframe_trend_lost(
        context: StrategyEvaluationContext, side: BreakoutSide
    ) -> bool:
        regime = context.multi_timeframe_regime
        expected_bias = (
            HigherTimeframeBias.UP if side is BreakoutSide.LONG else HigherTimeframeBias.DOWN
        )
        expected_direction = Direction.UP if side is BreakoutSide.LONG else Direction.DOWN
        return (
            regime.higher_timeframe_bias is not expected_bias
            or regime.regime_1h is None
            or regime.regime_1h.direction is not expected_direction
            or regime.regime_4h is None
            or regime.regime_4h.direction is not expected_direction
        )

    @staticmethod
    def _entry_trend_failures(
        context: StrategyEvaluationContext, side: BreakoutSide
    ) -> tuple[BreakoutRetestReasonCode, ...]:
        regime = context.multi_timeframe_regime
        expected_direction = Direction.UP if side is BreakoutSide.LONG else Direction.DOWN
        failures: list[BreakoutRetestReasonCode] = []
        if regime.regime_15m.direction not in {expected_direction, Direction.NEUTRAL}:
            failures.append(BreakoutRetestReasonCode.ENTRY_DIRECTION_OPPOSED)
        ema20 = context.features_15m.ema_20
        ema50 = context.features_15m.ema_50
        if (
            ema20 is not None
            and ema50 is not None
            and (
                (side is BreakoutSide.LONG and ema20 <= ema50)
                or (side is BreakoutSide.SHORT and ema20 >= ema50)
            )
        ):
            failures.append(BreakoutRetestReasonCode.EMA_ALIGNMENT_FAILED)
        return tuple(failures)

    @staticmethod
    def _empty_audit(context: StrategyEvaluationContext) -> BreakoutRetestRuleAudit:
        return BreakoutRetestRuleAudit(
            armed_side=None,
            breakout_level=None,
            breakout_observed_at=None,
            breakout_bar_sequence=None,
            breakout_pivot_kind=None,
            breakout_pivot_open_time=None,
            breakout_pivot_confirmed_at=None,
            breakout_pivot_price=None,
            current_pivot_price=None,
            bars_waited=None,
            atr_14=context.features_15m.atr_14,
            required_breakout_close=None,
            retest_floor=None,
            retest_ceiling=None,
            retest_extreme=None,
            invalidation_distance=None,
        )

    @staticmethod
    def _arming_audit(
        context: StrategyEvaluationContext,
        side: BreakoutSide,
        pivot: ConfirmedPivot | None,
        required_close: Decimal | None,
        sequence: int,
    ) -> BreakoutRetestRuleAudit:
        return BreakoutRetestRuleAudit(
            armed_side=side,
            breakout_level=None if pivot is None else pivot.pivot_price,
            breakout_observed_at=context.observed_at,
            breakout_bar_sequence=sequence,
            breakout_pivot_kind=None if pivot is None else pivot.kind,
            breakout_pivot_open_time=None if pivot is None else pivot.pivot_open_time,
            breakout_pivot_confirmed_at=None if pivot is None else pivot.confirmed_at,
            breakout_pivot_price=None if pivot is None else pivot.pivot_price,
            current_pivot_price=None if pivot is None else pivot.pivot_price,
            bars_waited=0,
            atr_14=context.features_15m.atr_14,
            required_breakout_close=required_close,
            retest_floor=None,
            retest_ceiling=None,
            retest_extreme=None,
            invalidation_distance=None,
        )

    @staticmethod
    def _armed_audit(
        context: StrategyEvaluationContext,
        armed: ArmedBreakout,
        floor: Decimal | None = None,
        ceiling: Decimal | None = None,
        extreme: Decimal | None = None,
    ) -> BreakoutRetestRuleAudit:
        pivot = armed.breakout_pivot
        current_pivot = (
            context.structure_15m.latest_confirmed_high
            if armed.side is BreakoutSide.LONG
            else context.structure_15m.latest_confirmed_low
        )
        return BreakoutRetestRuleAudit(
            armed_side=armed.side,
            breakout_level=armed.breakout_level,
            breakout_observed_at=armed.breakout_observed_at,
            breakout_bar_sequence=armed.breakout_bar_sequence,
            breakout_pivot_kind=pivot.kind,
            breakout_pivot_open_time=pivot.pivot_open_time,
            breakout_pivot_confirmed_at=pivot.confirmed_at,
            breakout_pivot_price=pivot.pivot_price,
            current_pivot_price=None if current_pivot is None else current_pivot.pivot_price,
            bars_waited=armed.bars_waited,
            atr_14=context.features_15m.atr_14,
            required_breakout_close=None,
            retest_floor=floor,
            retest_ceiling=ceiling,
            retest_extreme=extreme,
            invalidation_distance=None,
        )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        failures: tuple[BreakoutRetestReasonCode, ...],
        audit: BreakoutRetestRuleAudit,
    ) -> Evaluation:
        return self._result(context, CandidateAction.NO_TRADE, (), failures, audit)

    def _result(
        self,
        context: StrategyEvaluationContext,
        action: CandidateAction,
        reasons_for: tuple[BreakoutRetestReasonCode, ...],
        reasons_against: tuple[BreakoutRetestReasonCode, ...],
        audit: BreakoutRetestRuleAudit,
        *,
        reference_price: Decimal | None = None,
        invalidation_reference_price: Decimal | None = None,
    ) -> Evaluation:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            action=action,
            reference_price=reference_price,
            invalidation_reference_price=invalidation_reference_price,
            reasons_for=reasons_for,
            reasons_against=reasons_against,
            rule_audit=audit,
        )
