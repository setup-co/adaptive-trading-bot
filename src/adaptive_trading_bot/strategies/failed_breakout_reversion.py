"""Research-only failed-breakout reversion strategy with a sealed midpoint target."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Candle
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

STRATEGY_ID = "range_failed_breakout_reversion"
STRATEGY_VERSION = "0.1.0"
POLICY_ID = "FAILED_BREAKOUT_PREVIOUS_16_MIDPOINT_30M_V1"
BASE_COSTS = SimulatedExecutionCostConfiguration(
    spread_bps=Decimal("2"),
    slippage_bps=Decimal("2"),
    fee_bps=Decimal("8"),
)
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class FailedBreakoutReversionReasonCode(StrEnum):
    """Predeclared supporting and rejecting rule facts."""

    PREVIOUS_16_COMPLETE = "PREVIOUS_16_COMPLETE"
    ATR14_POSITIVE = "ATR14_POSITIVE"
    OPEN_INSIDE_REFERENCE = "OPEN_INSIDE_REFERENCE"
    ONE_SIDED_EXCURSION = "ONE_SIDED_EXCURSION"
    CLOSE_REENTERED_TOWARD_MIDPOINT = "CLOSE_REENTERED_TOWARD_MIDPOINT"
    STRUCTURAL_STOP_VALID = "STRUCTURAL_STOP_VALID"
    BASE_COST_ROOM_PASSED = "BASE_COST_ROOM_PASSED"

    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
    REQUIRED_CONTEXT_NOT_READY = "REQUIRED_CONTEXT_NOT_READY"
    EXTREME_VOLATILITY_BLOCKED = "EXTREME_VOLATILITY_BLOCKED"
    CONTEXT_NOT_FULLY_WARMED = "CONTEXT_NOT_FULLY_WARMED"
    ATR14_UNAVAILABLE = "ATR14_UNAVAILABLE"
    REFERENCE_RANGE_INVALID = "REFERENCE_RANGE_INVALID"
    OPEN_OUTSIDE_REFERENCE = "OPEN_OUTSIDE_REFERENCE"
    BOTH_BOUNDARIES_CROSSED = "BOTH_BOUNDARIES_CROSSED"
    NO_QUALIFYING_FAILED_EXCURSION = "NO_QUALIFYING_FAILED_EXCURSION"
    STRUCTURAL_STOP_INVALID = "STRUCTURAL_STOP_INVALID"
    BASE_COST_ROOM_REJECTED = "BASE_COST_ROOM_REJECTED"
    HISTORY_GAP_RESET = "HISTORY_GAP_RESET"


@dataclass(frozen=True, slots=True)
class FailedBreakoutReversionRuleAudit:
    """Exact causal reference, geometry, and ex-ante cost-room facts."""

    policy_id: str
    history_count: int
    history_first_open: datetime | None
    history_last_close: datetime | None
    reference_high: Decimal | None
    reference_low: Decimal | None
    sealed_midpoint: Decimal | None
    atr_14: Decimal | None
    signal_open: Decimal
    signal_high: Decimal
    signal_low: Decimal
    signal_close: Decimal
    structural_stop: Decimal | None
    gross_target_distance: Decimal | None
    modeled_cost_room: Decimal | None
    modeled_net_target_pnl: Decimal | None
    base_costs: SimulatedExecutionCostConfiguration


Evaluation = StrategyEvaluation[FailedBreakoutReversionReasonCode, FailedBreakoutReversionRuleAudit]


class FailedBreakoutReversionStrategy:
    """Evaluate each 15m close before inserting it into a bounded 16-bar history."""

    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION

    def __init__(self) -> None:
        self._history: dict[tuple[object, object], tuple[Candle, ...]] = {}

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        context.validate()
        key = (context.source_id, context.symbol)
        history = self._history.get(key, ())
        candle = context.candle_15m
        if history and candle.open_time < history[-1].close_time:
            raise StrategyContextError("failed-breakout history moved backward or duplicated")
        if history and candle.open_time != history[-1].close_time:
            self._history[key] = (candle,)
            return self._no_trade(
                context,
                (),
                (FailedBreakoutReversionReasonCode.HISTORY_GAP_RESET,),
            )
        try:
            return self._evaluate_before_insert(context, history)
        finally:
            self._history[key] = (*history, candle)[-16:]

    def _evaluate_before_insert(
        self, context: StrategyEvaluationContext, history: tuple[Candle, ...]
    ) -> Evaluation:
        candle = context.candle_15m
        feature = context.features_15m
        if len(history) != 16:
            return self._no_trade(
                context, history, (FailedBreakoutReversionReasonCode.WARMUP_INCOMPLETE,)
            )
        regime = context.multi_timeframe_regime
        if not regime.is_fully_ready:
            return self._no_trade(
                context,
                history,
                (FailedBreakoutReversionReasonCode.REQUIRED_CONTEXT_NOT_READY,),
            )
        if regime.has_extreme_volatility:
            return self._no_trade(
                context,
                history,
                (FailedBreakoutReversionReasonCode.EXTREME_VOLATILITY_BLOCKED,),
            )
        if not feature.is_fully_warmed:
            return self._no_trade(
                context,
                history,
                (FailedBreakoutReversionReasonCode.CONTEXT_NOT_FULLY_WARMED,),
            )
        atr = feature.atr_14
        if not isinstance(atr, Decimal) or not atr.is_finite() or atr <= 0:
            return self._no_trade(
                context, history, (FailedBreakoutReversionReasonCode.ATR14_UNAVAILABLE,)
            )
        with localcontext(_CONTEXT):
            high = max(item.high for item in history)
            low = min(item.low for item in history)
            if high <= low:
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.REFERENCE_RANGE_INVALID,),
                )
            midpoint = (high + low) / Decimal(2)
            if not low <= candle.open <= high:
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.OPEN_OUTSIDE_REFERENCE,),
                    high=high,
                    low=low,
                    midpoint=midpoint,
                )
            crossed_low = candle.low < low
            crossed_high = candle.high > high
            if crossed_low and crossed_high:
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.BOTH_BOUNDARIES_CROSSED,),
                    high=high,
                    low=low,
                    midpoint=midpoint,
                )
            long = crossed_low and candle.high <= high and low < candle.close < midpoint
            short = crossed_high and candle.low >= low and midpoint < candle.close < high
            if not long and not short:
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.NO_QUALIFYING_FAILED_EXCURSION,),
                    high=high,
                    low=low,
                    midpoint=midpoint,
                )
            stop = (
                candle.low - Decimal("0.10") * atr if long else candle.high + Decimal("0.10") * atr
            )
            if stop <= 0 or (long and stop >= candle.close) or (short and stop <= candle.close):
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.STRUCTURAL_STOP_INVALID,),
                    high=high,
                    low=low,
                    midpoint=midpoint,
                    stop=stop,
                )
            gross, costs, net = _cost_room(candle.close, midpoint, long=long)
            audit = self._audit(
                context,
                history,
                high=high,
                low=low,
                midpoint=midpoint,
                stop=stop,
                gross=gross,
                costs=costs,
                net=net,
            )
            if gross < Decimal(3) * costs or net <= 0:
                return self._no_trade(
                    context,
                    history,
                    (FailedBreakoutReversionReasonCode.BASE_COST_ROOM_REJECTED,),
                    audit=audit,
                )
            return StrategyEvaluation(
                self.strategy_id,
                self.strategy_version,
                context.source_id,
                context.symbol,
                context.observed_at,
                CandidateAction.LONG_CANDIDATE if long else CandidateAction.SHORT_CANDIDATE,
                candle.close,
                stop,
                (
                    FailedBreakoutReversionReasonCode.PREVIOUS_16_COMPLETE,
                    FailedBreakoutReversionReasonCode.ATR14_POSITIVE,
                    FailedBreakoutReversionReasonCode.OPEN_INSIDE_REFERENCE,
                    FailedBreakoutReversionReasonCode.ONE_SIDED_EXCURSION,
                    FailedBreakoutReversionReasonCode.CLOSE_REENTERED_TOWARD_MIDPOINT,
                    FailedBreakoutReversionReasonCode.STRUCTURAL_STOP_VALID,
                    FailedBreakoutReversionReasonCode.BASE_COST_ROOM_PASSED,
                ),
                (),
                audit,
            )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        history: tuple[Candle, ...],
        reasons: tuple[FailedBreakoutReversionReasonCode, ...],
        *,
        high: Decimal | None = None,
        low: Decimal | None = None,
        midpoint: Decimal | None = None,
        stop: Decimal | None = None,
        audit: FailedBreakoutReversionRuleAudit | None = None,
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
            reasons,
            audit
            or self._audit(
                context,
                history,
                high=high,
                low=low,
                midpoint=midpoint,
                stop=stop,
            ),
        )

    def _audit(
        self,
        context: StrategyEvaluationContext,
        history: tuple[Candle, ...],
        *,
        high: Decimal | None = None,
        low: Decimal | None = None,
        midpoint: Decimal | None = None,
        stop: Decimal | None = None,
        gross: Decimal | None = None,
        costs: Decimal | None = None,
        net: Decimal | None = None,
    ) -> FailedBreakoutReversionRuleAudit:
        candle = context.candle_15m
        return FailedBreakoutReversionRuleAudit(
            POLICY_ID,
            len(history),
            history[0].open_time if history else None,
            history[-1].close_time if history else None,
            high,
            low,
            midpoint,
            context.features_15m.atr_14,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            stop,
            gross,
            costs,
            net,
            BASE_COSTS,
        )


def _cost_room(
    reference: Decimal, target: Decimal, *, long: bool
) -> tuple[Decimal, Decimal, Decimal]:
    entry_side = SimulatedExecutionSide.BUY if long else SimulatedExecutionSide.SELL
    exit_side = SimulatedExecutionSide.SELL if long else SimulatedExecutionSide.BUY
    entry = price_simulated_market_fill(
        side=entry_side, quantity=Decimal(1), reference_price=reference, costs=BASE_COSTS
    )
    exit_fill = price_simulated_market_fill(
        side=exit_side, quantity=Decimal(1), reference_price=target, costs=BASE_COSTS
    )
    with localcontext(_CONTEXT):
        gross = abs(reference - target)
        entry_adverse = abs(entry.fill_price - reference)
        exit_adverse = abs(exit_fill.fill_price - target)
        modeled_cost = entry_adverse + exit_adverse + entry.fee_quote + exit_fill.fee_quote
        direction = Decimal(1) if long else Decimal(-1)
        net = (exit_fill.fill_price - entry.fill_price) * direction
        net -= entry.fee_quote + exit_fill.fee_quote
    return gross, modeled_cost, net
