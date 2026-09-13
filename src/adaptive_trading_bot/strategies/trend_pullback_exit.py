"""Causal Trend Pullback EMA50 thesis-failure exit hypothesis.

The policy emits strategy observations only.  It has no order, fill, protective-stop,
accounting, or execution authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.strategies.exit_core import (
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitValidationError,
    PositionSide,
)
from adaptive_trading_bot.strategies.trend_pullback import TrendPullbackStrategy

_EXIT_POLICY_VERSION = "trend-pullback-ema50-thesis-exit-v1"


class TrendPullbackExitReasonCode(StrEnum):
    """Typed evidence for the fixed EMA50 thesis-failure hypothesis."""

    LONG_CLOSED_BELOW_EMA50 = "LONG_CLOSED_BELOW_EMA50"
    SHORT_CLOSED_ABOVE_EMA50 = "SHORT_CLOSED_ABOVE_EMA50"
    THESIS_REMAINS_VALID = "THESIS_REMAINS_VALID"
    EMA50_UNAVAILABLE = "EMA50_UNAVAILABLE"
    FEATURES_NOT_READY = "FEATURES_NOT_READY"
    CONTEXT_NOT_READY = "CONTEXT_NOT_READY"


class Ema50ComparisonRelation(StrEnum):
    """Exact relation between the current closed-bar close and EMA50."""

    BELOW = "BELOW"
    EQUAL = "EQUAL"
    ABOVE = "ABOVE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TrendPullbackExitRuleAudit:
    """Exact causal inputs and relation used by one exit evaluation."""

    current_close: Decimal
    ema_50: Decimal | None
    position_side: PositionSide
    comparison_relation: Ema50ComparisonRelation
    features_fully_warmed: bool
    ema50_available: bool
    context_fully_ready: bool
    exit_policy_version: str
    protective_stop_delegated: bool = True

    def __post_init__(self) -> None:
        _positive_decimal("current_close", self.current_close)
        if self.ema_50 is not None:
            _positive_decimal("ema_50", self.ema_50)
        if not isinstance(self.position_side, PositionSide):
            raise PositionExitValidationError("exit audit position side is invalid")
        if not isinstance(self.comparison_relation, Ema50ComparisonRelation):
            raise PositionExitValidationError("EMA50 comparison relation is invalid")
        for name, value in (
            ("features_fully_warmed", self.features_fully_warmed),
            ("ema50_available", self.ema50_available),
            ("context_fully_ready", self.context_fully_ready),
            ("protective_stop_delegated", self.protective_stop_delegated),
        ):
            if not isinstance(value, bool):
                raise PositionExitValidationError(f"{name} must be a bool")
        if self.ema50_available is not (self.ema_50 is not None):
            raise PositionExitValidationError("EMA50 availability audit is inconsistent")
        if self.comparison_relation is Ema50ComparisonRelation.UNAVAILABLE:
            if self.features_fully_warmed and self.ema50_available and self.context_fully_ready:
                raise PositionExitValidationError("available evidence requires an EMA50 relation")
        elif not (self.features_fully_warmed and self.ema50_available and self.context_fully_ready):
            raise PositionExitValidationError("unready evidence cannot claim an EMA50 relation")
        if self.exit_policy_version != _EXIT_POLICY_VERSION:
            raise PositionExitValidationError("Trend Pullback exit policy version mismatch")
        if self.protective_stop_delegated is not True:
            raise PositionExitValidationError("protective stops must remain independently owned")


class TrendPullbackEma50ExitStrategy:
    """Emit CLOSE_FULL only for a strict closed-bar cross through EMA50."""

    __slots__ = ("_strategy_id", "_strategy_version")

    def __init__(self) -> None:
        entry_strategy = TrendPullbackStrategy()
        self._strategy_id = entry_strategy.strategy_id
        self._strategy_version = entry_strategy.strategy_version

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def strategy_version(self) -> str:
        return self._strategy_version

    @property
    def exit_policy_version(self) -> str:
        return _EXIT_POLICY_VERSION

    def evaluate_exit(self, context: PositionExitEvaluationContext) -> PositionExitEvaluation:
        """Evaluate only the supplied causal closed 15m context."""

        if not isinstance(context, PositionExitEvaluationContext):
            raise TypeError("context must be a PositionExitEvaluationContext")
        context.validate()
        if (
            context.strategy_id != self.strategy_id
            or context.strategy_version != self.strategy_version
        ):
            raise PositionExitValidationError("exit strategy and position strategy differ")

        market = context.current_market_context
        close = market.candle_15m.close
        features = market.features_15m
        ema50 = features.ema_50
        _positive_decimal("current closed-candle close", close)
        if not isinstance(features.is_fully_warmed, bool):
            raise PositionExitValidationError("feature warmed state must be a bool")
        if not isinstance(market.multi_timeframe_regime.is_fully_ready, bool):
            raise PositionExitValidationError("context readiness state must be a bool")
        if ema50 is not None:
            _positive_decimal("EMA50", ema50)

        unavailable: list[TrendPullbackExitReasonCode] = []
        if not market.multi_timeframe_regime.is_fully_ready:
            unavailable.append(TrendPullbackExitReasonCode.CONTEXT_NOT_READY)
        if not features.is_fully_warmed:
            unavailable.append(TrendPullbackExitReasonCode.FEATURES_NOT_READY)
        if ema50 is None:
            unavailable.append(TrendPullbackExitReasonCode.EMA50_UNAVAILABLE)
        if unavailable:
            return self._result(
                context,
                action=PositionExitAction.HOLD,
                reasons=tuple(unavailable),
                relation=Ema50ComparisonRelation.UNAVAILABLE,
                close=close,
                ema50=ema50,
            )

        assert ema50 is not None
        if close < ema50:
            relation = Ema50ComparisonRelation.BELOW
        elif close > ema50:
            relation = Ema50ComparisonRelation.ABOVE
        else:
            relation = Ema50ComparisonRelation.EQUAL

        should_close = (
            context.position_side is PositionSide.LONG and relation is Ema50ComparisonRelation.BELOW
        ) or (
            context.position_side is PositionSide.SHORT
            and relation is Ema50ComparisonRelation.ABOVE
        )
        if should_close:
            reason = (
                TrendPullbackExitReasonCode.LONG_CLOSED_BELOW_EMA50
                if context.position_side is PositionSide.LONG
                else TrendPullbackExitReasonCode.SHORT_CLOSED_ABOVE_EMA50
            )
            return self._result(
                context,
                action=PositionExitAction.CLOSE_FULL,
                reasons=(reason,),
                relation=relation,
                close=close,
                ema50=ema50,
            )
        return self._result(
            context,
            action=PositionExitAction.HOLD,
            reasons=(TrendPullbackExitReasonCode.THESIS_REMAINS_VALID,),
            relation=relation,
            close=close,
            ema50=ema50,
        )

    def _result(
        self,
        context: PositionExitEvaluationContext,
        *,
        action: PositionExitAction,
        reasons: tuple[TrendPullbackExitReasonCode, ...],
        relation: Ema50ComparisonRelation,
        close: Decimal,
        ema50: Decimal | None,
    ) -> PositionExitEvaluation:
        audit = TrendPullbackExitRuleAudit(
            current_close=close,
            ema_50=ema50,
            position_side=context.position_side,
            comparison_relation=relation,
            features_fully_warmed=context.current_market_context.features_15m.is_fully_warmed,
            ema50_available=ema50 is not None,
            context_fully_ready=(
                context.current_market_context.multi_timeframe_regime.is_fully_ready
            ),
            exit_policy_version=self.exit_policy_version,
        )
        result = PositionExitEvaluation.create(
            context,
            action=action,
            reasons_for=reasons,
            reasons_against=(),
            rule_audit=audit,
            exit_policy_version=self.exit_policy_version,
            exit_reference_price=(close if action is PositionExitAction.CLOSE_FULL else None),
        )
        result.validate()
        return deepcopy(result)


def _positive_decimal(field_name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PositionExitValidationError(f"{field_name} must be a positive finite Decimal")
