"""Causal V1 Range Mean Reversion candidate evaluation on validated ranges."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Protocol

from adaptive_trading_bot.domain import Timeframe
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
)
from adaptive_trading_bot.strategies.range_context import (
    RANGE_PARTIAL_EVIDENCE_RETENTION_V1,
    RangeContextSnapshot,
    RangeReasonCode,
    RangeStatus,
    ValidatedRangeDetector,
)

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class RangeMeanReversionSide(StrEnum):
    """Boundary side selected for candidate evaluation."""

    LONG = "LONG"
    SHORT = "SHORT"


class RangeMeanReversionReasonCode(StrEnum):
    """Canonical Range Mean Reversion evidence and counter-evidence."""

    VALIDATED_RANGE_ACTIVE = "VALIDATED_RANGE_ACTIVE"
    LOWER_BOUNDARY_ZONE_TOUCHED = "LOWER_BOUNDARY_ZONE_TOUCHED"
    UPPER_BOUNDARY_ZONE_TOUCHED = "UPPER_BOUNDARY_ZONE_TOUCHED"
    BOUNDARY_EXCURSION_ACCEPTABLE = "BOUNDARY_EXCURSION_ACCEPTABLE"
    RANGE_RECLAIM_CONFIRMED = "RANGE_RECLAIM_CONFIRMED"
    RANGE_REJECTION_CONFIRMED = "RANGE_REJECTION_CONFIRMED"
    MIDPOINT_CHASE_GUARD_PASSED = "MIDPOINT_CHASE_GUARD_PASSED"
    CANDLE_REVERSAL_CONFIRMED = "CANDLE_REVERSAL_CONFIRMED"
    RSI_EXHAUSTION_CONFIRMED = "RSI_EXHAUSTION_CONFIRMED"
    MOMENTUM_EXHAUSTION_CONFIRMED = "MOMENTUM_EXHAUSTION_CONFIRMED"
    INVALIDATION_GEOMETRY_VALID = "INVALIDATION_GEOMETRY_VALID"

    RANGE_NOT_VALID = "RANGE_NOT_VALID"
    RANGE_INSUFFICIENT_DATA = "RANGE_INSUFFICIENT_DATA"
    RANGE_STALE = "RANGE_STALE"
    RANGE_BROKEN = "RANGE_BROKEN"
    RANGE_SNAPSHOT_INCONSISTENT = "RANGE_SNAPSHOT_INCONSISTENT"
    PRICE_NOT_AT_RANGE_EDGE = "PRICE_NOT_AT_RANGE_EDGE"
    BOTH_BOUNDARIES_TOUCHED = "BOTH_BOUNDARIES_TOUCHED"
    BOUNDARY_EXCURSION_TOO_DEEP = "BOUNDARY_EXCURSION_TOO_DEEP"
    RANGE_RECLAIM_FAILED = "RANGE_RECLAIM_FAILED"
    RANGE_REJECTION_FAILED = "RANGE_REJECTION_FAILED"
    MIDPOINT_ALREADY_CROSSED = "MIDPOINT_ALREADY_CROSSED"
    CANDLE_REVERSAL_FAILED = "CANDLE_REVERSAL_FAILED"
    RSI_EXHAUSTION_FAILED = "RSI_EXHAUSTION_FAILED"
    MOMENTUM_EXHAUSTION_FAILED = "MOMENTUM_EXHAUSTION_FAILED"
    INVALIDATION_WRONG_SIDE = "INVALIDATION_WRONG_SIDE"
    INVALIDATION_TOO_WIDE = "INVALIDATION_TOO_WIDE"


@dataclass(frozen=True, slots=True)
class RangeMeanReversionRuleAudit:
    """Range evidence and exact Decimal geometry used by one evaluation."""

    range_status: RangeStatus
    range_reasons: tuple[RangeReasonCode, ...]
    lower_boundary: Decimal | None
    upper_boundary: Decimal | None
    midpoint: Decimal | None
    range_width: Decimal | None
    width_in_atr: Decimal | None
    current_atr: Decimal | None
    lower_entry_zone_ceiling: Decimal | None
    upper_entry_zone_floor: Decimal | None
    lower_excursion_floor: Decimal | None
    upper_excursion_ceiling: Decimal | None
    candle_low: Decimal
    candle_high: Decimal
    candle_close: Decimal
    rsi_14: Decimal | None
    momentum_4: Decimal | None
    selected_side: RangeMeanReversionSide | None
    invalidation_reference: Decimal | None
    invalidation_distance: Decimal | None
    partial_evidence_retained: bool = False
    partial_evidence_reset: bool = False
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )


@dataclass(frozen=True, slots=True)
class RangeMeanReversionConfiguration:
    """Fixed, validated Range Mean Reversion V1 thresholds."""

    entry_timeframe: Timeframe = Timeframe.MINUTES_15
    boundary_entry_tolerance_atr: Decimal = Decimal("0.25")
    maximum_boundary_excursion_atr: Decimal = Decimal("0.20")
    long_max_rsi: Decimal = Decimal(45)
    short_min_rsi: Decimal = Decimal(55)
    max_invalidation_distance_atr: Decimal = Decimal("3.0")

    def __post_init__(self) -> None:
        if self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ValueError("Range Mean Reversion V1 entry_timeframe must be 15m")
        for name, value in (
            ("boundary_entry_tolerance_atr", self.boundary_entry_tolerance_atr),
            ("maximum_boundary_excursion_atr", self.maximum_boundary_excursion_atr),
            ("long_max_rsi", self.long_max_rsi),
            ("short_min_rsi", self.short_min_rsi),
            ("max_invalidation_distance_atr", self.max_invalidation_distance_atr),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.boundary_entry_tolerance_atr < 0:
            raise ValueError("boundary_entry_tolerance_atr must be non-negative")
        if self.maximum_boundary_excursion_atr < 0:
            raise ValueError("maximum_boundary_excursion_atr must be non-negative")
        if self.max_invalidation_distance_atr <= 0:
            raise ValueError("max_invalidation_distance_atr must be positive")
        for name, value in (
            ("long_max_rsi", self.long_max_rsi),
            ("short_min_rsi", self.short_min_rsi),
        ):
            if value < 0 or value > 100:
                raise ValueError(f"{name} must be in [0, 100]")


class RangeContextDetector(Protocol):
    """Minimal detector seam used once by each strategy evaluation."""

    def process(self, context: StrategyEvaluationContext) -> RangeContextSnapshot:
        """Return range context for the same observed close."""


Evaluation = StrategyEvaluation[RangeMeanReversionReasonCode, RangeMeanReversionRuleAudit]


@dataclass(frozen=True, slots=True)
class RangeMeanReversionStrategy:
    """Evaluate ordered 15m contexts using one dedicated validated-range authority."""

    configuration: RangeMeanReversionConfiguration = field(
        default_factory=RangeMeanReversionConfiguration
    )
    range_detector: RangeContextDetector = field(
        default_factory=ValidatedRangeDetector, repr=False, compare=False
    )
    strategy_id: str = field(init=False, default="range_mean_reversion")
    strategy_version: str = field(init=False, default="2.0.0")

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        """Process range state once, then return a candidate or explicit NO_TRADE."""

        range_snapshot = self.range_detector.process(context)
        with localcontext(_DECIMAL_CONTEXT):
            if self._identity_inconsistent(context, range_snapshot):
                return self._no_trade(
                    context,
                    range_snapshot,
                    (RangeMeanReversionReasonCode.RANGE_SNAPSHOT_INCONSISTENT,),
                )
            if range_snapshot.status is not RangeStatus.VALID_RANGE:
                return self._no_trade(
                    context,
                    range_snapshot,
                    (self._status_reason(range_snapshot.status),),
                )
            if self._valid_range_inconsistent(context, range_snapshot):
                return self._no_trade(
                    context,
                    range_snapshot,
                    (RangeMeanReversionReasonCode.RANGE_SNAPSHOT_INCONSISTENT,),
                )

            lower = range_snapshot.lower_boundary
            upper = range_snapshot.upper_boundary
            midpoint = range_snapshot.midpoint
            atr = range_snapshot.current_atr
            assert lower is not None and upper is not None and midpoint is not None
            assert atr is not None
            lower_entry = lower + self.configuration.boundary_entry_tolerance_atr * atr
            upper_entry = upper - self.configuration.boundary_entry_tolerance_atr * atr
            lower_excursion = lower - self.configuration.maximum_boundary_excursion_atr * atr
            upper_excursion = upper + self.configuration.maximum_boundary_excursion_atr * atr
            candle = context.candle_15m
            lower_touched = candle.low <= lower_entry
            upper_touched = candle.high >= upper_entry
            audit = self._audit(
                context,
                range_snapshot,
                lower_entry,
                upper_entry,
                lower_excursion,
                upper_excursion,
            )
            if lower_touched and upper_touched:
                return self._no_trade(
                    context,
                    range_snapshot,
                    (RangeMeanReversionReasonCode.BOTH_BOUNDARIES_TOUCHED,),
                    audit,
                )
            if not lower_touched and not upper_touched:
                return self._no_trade(
                    context,
                    range_snapshot,
                    (RangeMeanReversionReasonCode.PRICE_NOT_AT_RANGE_EDGE,),
                    audit,
                )
            if lower_touched:
                return self._evaluate_long(
                    context, range_snapshot, audit, lower_excursion, midpoint
                )
            return self._evaluate_short(context, range_snapshot, audit, upper_excursion, midpoint)

    def _evaluate_long(
        self,
        context: StrategyEvaluationContext,
        snapshot: RangeContextSnapshot,
        audit: RangeMeanReversionRuleAudit,
        excursion_floor: Decimal,
        midpoint: Decimal,
    ) -> Evaluation:
        candle = context.candle_15m
        feature = context.features_15m
        lower = snapshot.lower_boundary
        invalidation = snapshot.lower_breakout_threshold
        atr = snapshot.current_atr
        assert lower is not None and invalidation is not None and atr is not None
        failures: list[RangeMeanReversionReasonCode] = []
        if candle.low < excursion_floor:
            failures.append(RangeMeanReversionReasonCode.BOUNDARY_EXCURSION_TOO_DEEP)
        if candle.close <= lower:
            failures.append(RangeMeanReversionReasonCode.RANGE_RECLAIM_FAILED)
        if candle.close > midpoint:
            failures.append(RangeMeanReversionReasonCode.MIDPOINT_ALREADY_CROSSED)
        if candle.close <= candle.open:
            failures.append(RangeMeanReversionReasonCode.CANDLE_REVERSAL_FAILED)
        if feature.rsi_14 is None or feature.rsi_14 > self.configuration.long_max_rsi:
            failures.append(RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED)
        if feature.momentum_4 is None or feature.momentum_4 > 0:
            failures.append(RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_FAILED)
        distance = candle.close - invalidation
        if distance <= 0:
            failures.append(RangeMeanReversionReasonCode.INVALIDATION_WRONG_SIDE)
        elif distance > self.configuration.max_invalidation_distance_atr * atr:
            failures.append(RangeMeanReversionReasonCode.INVALIDATION_TOO_WIDE)
        audit = self._selected_audit(audit, RangeMeanReversionSide.LONG, invalidation, distance)
        if failures:
            return self._no_trade(context, snapshot, tuple(failures), audit)
        return self._candidate(
            context,
            CandidateAction.LONG_CANDIDATE,
            invalidation,
            audit,
            RangeMeanReversionReasonCode.LOWER_BOUNDARY_ZONE_TOUCHED,
            RangeMeanReversionReasonCode.RANGE_RECLAIM_CONFIRMED,
        )

    def _evaluate_short(
        self,
        context: StrategyEvaluationContext,
        snapshot: RangeContextSnapshot,
        audit: RangeMeanReversionRuleAudit,
        excursion_ceiling: Decimal,
        midpoint: Decimal,
    ) -> Evaluation:
        candle = context.candle_15m
        feature = context.features_15m
        upper = snapshot.upper_boundary
        invalidation = snapshot.upper_breakout_threshold
        atr = snapshot.current_atr
        assert upper is not None and invalidation is not None and atr is not None
        failures: list[RangeMeanReversionReasonCode] = []
        if candle.high > excursion_ceiling:
            failures.append(RangeMeanReversionReasonCode.BOUNDARY_EXCURSION_TOO_DEEP)
        if candle.close >= upper:
            failures.append(RangeMeanReversionReasonCode.RANGE_REJECTION_FAILED)
        if candle.close < midpoint:
            failures.append(RangeMeanReversionReasonCode.MIDPOINT_ALREADY_CROSSED)
        if candle.close >= candle.open:
            failures.append(RangeMeanReversionReasonCode.CANDLE_REVERSAL_FAILED)
        if feature.rsi_14 is None or feature.rsi_14 < self.configuration.short_min_rsi:
            failures.append(RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED)
        if feature.momentum_4 is None or feature.momentum_4 < 0:
            failures.append(RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_FAILED)
        distance = invalidation - candle.close
        if distance <= 0:
            failures.append(RangeMeanReversionReasonCode.INVALIDATION_WRONG_SIDE)
        elif distance > self.configuration.max_invalidation_distance_atr * atr:
            failures.append(RangeMeanReversionReasonCode.INVALIDATION_TOO_WIDE)
        audit = self._selected_audit(audit, RangeMeanReversionSide.SHORT, invalidation, distance)
        if failures:
            return self._no_trade(context, snapshot, tuple(failures), audit)
        return self._candidate(
            context,
            CandidateAction.SHORT_CANDIDATE,
            invalidation,
            audit,
            RangeMeanReversionReasonCode.UPPER_BOUNDARY_ZONE_TOUCHED,
            RangeMeanReversionReasonCode.RANGE_REJECTION_CONFIRMED,
        )

    def _candidate(
        self,
        context: StrategyEvaluationContext,
        action: CandidateAction,
        invalidation: Decimal,
        audit: RangeMeanReversionRuleAudit,
        touch_reason: RangeMeanReversionReasonCode,
        recovery_reason: RangeMeanReversionReasonCode,
    ) -> Evaluation:
        return StrategyEvaluation(
            self.strategy_id,
            self.strategy_version,
            context.source_id,
            context.symbol,
            context.observed_at,
            action,
            context.candle_15m.close,
            invalidation,
            (
                RangeMeanReversionReasonCode.VALIDATED_RANGE_ACTIVE,
                touch_reason,
                RangeMeanReversionReasonCode.BOUNDARY_EXCURSION_ACCEPTABLE,
                recovery_reason,
                RangeMeanReversionReasonCode.MIDPOINT_CHASE_GUARD_PASSED,
                RangeMeanReversionReasonCode.CANDLE_REVERSAL_CONFIRMED,
                RangeMeanReversionReasonCode.RSI_EXHAUSTION_CONFIRMED,
                RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_CONFIRMED,
                RangeMeanReversionReasonCode.INVALIDATION_GEOMETRY_VALID,
            ),
            (),
            audit,
        )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        snapshot: RangeContextSnapshot,
        failures: tuple[RangeMeanReversionReasonCode, ...],
        audit: RangeMeanReversionRuleAudit | None = None,
    ) -> Evaluation:
        return StrategyEvaluation(
            self.strategy_id,
            self.strategy_version,
            context.source_id,
            context.symbol,
            context.observed_at,
            CandidateAction.NO_TRADE,
            None,
            None,
            (),
            failures,
            self._audit(context, snapshot) if audit is None else audit,
        )

    @staticmethod
    def _status_reason(status: RangeStatus) -> RangeMeanReversionReasonCode:
        return {
            RangeStatus.INSUFFICIENT_DATA: RangeMeanReversionReasonCode.RANGE_INSUFFICIENT_DATA,
            RangeStatus.NOT_RANGE: RangeMeanReversionReasonCode.RANGE_NOT_VALID,
            RangeStatus.RANGE_STALE: RangeMeanReversionReasonCode.RANGE_STALE,
            RangeStatus.RANGE_BROKEN: RangeMeanReversionReasonCode.RANGE_BROKEN,
            RangeStatus.VALID_RANGE: RangeMeanReversionReasonCode.RANGE_SNAPSHOT_INCONSISTENT,
        }[status]

    @staticmethod
    def _identity_inconsistent(
        context: StrategyEvaluationContext, snapshot: RangeContextSnapshot
    ) -> bool:
        return (
            snapshot.source_id != context.source_id
            or snapshot.symbol != context.symbol
            or snapshot.observed_at != context.observed_at
            or snapshot.partial_evidence_policy_version != RANGE_PARTIAL_EVIDENCE_RETENTION_V1
        )

    @staticmethod
    def _valid_range_inconsistent(
        context: StrategyEvaluationContext, snapshot: RangeContextSnapshot
    ) -> bool:
        values = (
            snapshot.lower_boundary,
            snapshot.upper_boundary,
            snapshot.midpoint,
            snapshot.range_width,
            snapshot.width_in_atr,
            snapshot.current_atr,
            snapshot.lower_breakout_threshold,
            snapshot.upper_breakout_threshold,
        )
        if not snapshot.is_valid_range or any(
            not isinstance(value, Decimal) or not value.is_finite() for value in values
        ):
            return True
        lower, upper, midpoint, width, width_atr, atr, lower_break, upper_break = values
        assert isinstance(lower, Decimal)
        assert isinstance(upper, Decimal)
        assert isinstance(midpoint, Decimal)
        assert isinstance(width, Decimal)
        assert isinstance(width_atr, Decimal)
        assert isinstance(atr, Decimal)
        assert isinstance(lower_break, Decimal)
        assert isinstance(upper_break, Decimal)
        return (
            atr <= 0
            or context.features_15m.atr_14 != atr
            or not lower < midpoint < upper
            or width <= 0
            or width != upper - lower
            or width_atr != width / atr
            or lower_break >= lower
            or upper_break <= upper
        )

    @staticmethod
    def _audit(
        context: StrategyEvaluationContext,
        snapshot: RangeContextSnapshot,
        lower_entry: Decimal | None = None,
        upper_entry: Decimal | None = None,
        lower_excursion: Decimal | None = None,
        upper_excursion: Decimal | None = None,
    ) -> RangeMeanReversionRuleAudit:
        candle = context.candle_15m
        feature = context.features_15m
        return RangeMeanReversionRuleAudit(
            snapshot.status,
            snapshot.reasons,
            snapshot.lower_boundary,
            snapshot.upper_boundary,
            snapshot.midpoint,
            snapshot.range_width,
            snapshot.width_in_atr,
            snapshot.current_atr,
            lower_entry,
            upper_entry,
            lower_excursion,
            upper_excursion,
            candle.low,
            candle.high,
            candle.close,
            feature.rsi_14,
            feature.momentum_4,
            None,
            None,
            None,
            snapshot.partial_evidence_retained,
            snapshot.partial_evidence_reset,
        )

    @staticmethod
    def _selected_audit(
        audit: RangeMeanReversionRuleAudit,
        side: RangeMeanReversionSide,
        invalidation: Decimal,
        distance: Decimal,
    ) -> RangeMeanReversionRuleAudit:
        return RangeMeanReversionRuleAudit(
            audit.range_status,
            audit.range_reasons,
            audit.lower_boundary,
            audit.upper_boundary,
            audit.midpoint,
            audit.range_width,
            audit.width_in_atr,
            audit.current_atr,
            audit.lower_entry_zone_ceiling,
            audit.upper_entry_zone_floor,
            audit.lower_excursion_floor,
            audit.upper_excursion_ceiling,
            audit.candle_low,
            audit.candle_high,
            audit.candle_close,
            audit.rsi_14,
            audit.momentum_4,
            side,
            invalidation,
            distance,
            audit.partial_evidence_retained,
            audit.partial_evidence_reset,
        )
