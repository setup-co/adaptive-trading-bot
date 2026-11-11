"""Research-only three-bar impulse/pullback/resumption strategy."""

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

STRATEGY_ID = "trend_impulse_pullback"
STRATEGY_VERSION = "0.1.0"
POLICY_ID = "THREE_BAR_IMPULSE_PULLBACK_RESUMPTION_V1"
BASE_COSTS = SimulatedExecutionCostConfiguration(
    spread_bps=Decimal("2"),
    slippage_bps=Decimal("2"),
    fee_bps=Decimal("8"),
)
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class TrendImpulsePullbackReasonCode(StrEnum):
    """Predeclared supporting and rejecting rule facts."""

    THREE_CANDLES_COMPLETE = "THREE_CANDLES_COMPLETE"
    REQUIRED_CONTEXT_READY = "REQUIRED_CONTEXT_READY"
    EXTREME_VOLATILITY_CLEAR = "EXTREME_VOLATILITY_CLEAR"
    ATR14_POSITIVE = "ATR14_POSITIVE"
    IMPULSE_QUALIFIED = "IMPULSE_QUALIFIED"
    PULLBACK_QUALIFIED = "PULLBACK_QUALIFIED"
    RESUMPTION_CONFIRMED = "RESUMPTION_CONFIRMED"
    STRUCTURAL_STOP_VALID = "STRUCTURAL_STOP_VALID"
    BASE_COST_ROOM_PASSED = "BASE_COST_ROOM_PASSED"

    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
    REQUIRED_CONTEXT_NOT_READY = "REQUIRED_CONTEXT_NOT_READY"
    EXTREME_VOLATILITY_BLOCKED = "EXTREME_VOLATILITY_BLOCKED"
    CONTEXT_NOT_FULLY_WARMED = "CONTEXT_NOT_FULLY_WARMED"
    ATR14_UNAVAILABLE = "ATR14_UNAVAILABLE"
    IMPULSE_NOT_QUALIFIED = "IMPULSE_NOT_QUALIFIED"
    PULLBACK_NOT_QUALIFIED = "PULLBACK_NOT_QUALIFIED"
    RESUMPTION_NOT_CONFIRMED = "RESUMPTION_NOT_CONFIRMED"
    STRUCTURAL_STOP_INVALID = "STRUCTURAL_STOP_INVALID"
    BASE_COST_ROOM_REJECTED = "BASE_COST_ROOM_REJECTED"
    HISTORY_GAP_RESET = "HISTORY_GAP_RESET"


@dataclass(frozen=True, slots=True)
class TrendImpulsePullbackRuleAudit:
    """Exact causal candles, ATR references, geometry, and cost-room facts."""

    policy_id: str
    history_count: int
    impulse_open_time: datetime | None
    impulse_open: Decimal | None
    impulse_high: Decimal | None
    impulse_low: Decimal | None
    impulse_close: Decimal | None
    pullback_open_time: datetime | None
    pullback_open: Decimal | None
    pullback_high: Decimal | None
    pullback_low: Decimal | None
    pullback_close: Decimal | None
    resumption_open_time: datetime
    resumption_open: Decimal
    resumption_high: Decimal
    resumption_low: Decimal
    resumption_close: Decimal
    atr_before_impulse: Decimal | None
    atr_at_resumption: Decimal | None
    impulse_body: Decimal | None
    impulse_range: Decimal | None
    retracement_boundary: Decimal | None
    structural_stop: Decimal | None
    sealed_target: Decimal | None
    gross_target_distance: Decimal | None
    modeled_cost_room: Decimal | None
    modeled_net_target_pnl: Decimal | None
    base_costs: SimulatedExecutionCostConfiguration


@dataclass(frozen=True, slots=True)
class _Observed:
    candle: Candle
    atr_14: Decimal | None
    fully_warmed: bool
    context_ready: bool
    extreme_volatility: bool


Evaluation = StrategyEvaluation[TrendImpulsePullbackReasonCode, TrendImpulsePullbackRuleAudit]


class TrendImpulsePullbackStrategy:
    """Evaluate the immediately preceding I/P bars with the current closed S bar."""

    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION

    def __init__(self) -> None:
        self._history: dict[tuple[object, object], tuple[_Observed, ...]] = {}

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        context.validate()
        key = (context.source_id, context.symbol)
        history = self._history.get(key, ())
        candle = context.candle_15m
        if history and candle.open_time < history[-1].candle.close_time:
            raise StrategyContextError("impulse/pullback history moved backward or duplicated")
        current = _Observed(
            candle,
            context.features_15m.atr_14,
            context.features_15m.is_fully_warmed,
            context.multi_timeframe_regime.is_fully_ready,
            context.multi_timeframe_regime.has_extreme_volatility,
        )
        if history and candle.open_time != history[-1].candle.close_time:
            self._history[key] = (current,)
            return self._no_trade(context, (), (TrendImpulsePullbackReasonCode.HISTORY_GAP_RESET,))
        try:
            return self._evaluate_before_insert(context, history)
        finally:
            self._history[key] = (*history, current)[-3:]

    def _evaluate_before_insert(
        self, context: StrategyEvaluationContext, history: tuple[_Observed, ...]
    ) -> Evaluation:
        if len(history) != 3:
            return self._no_trade(
                context, history, (TrendImpulsePullbackReasonCode.WARMUP_INCOMPLETE,)
            )
        prior, impulse_state, pullback_state = history
        current_ready = context.multi_timeframe_regime.is_fully_ready
        if not (impulse_state.context_ready and pullback_state.context_ready and current_ready):
            return self._no_trade(
                context,
                history,
                (TrendImpulsePullbackReasonCode.REQUIRED_CONTEXT_NOT_READY,),
            )
        if (
            impulse_state.extreme_volatility
            or pullback_state.extreme_volatility
            or context.multi_timeframe_regime.has_extreme_volatility
        ):
            return self._no_trade(
                context,
                history,
                (TrendImpulsePullbackReasonCode.EXTREME_VOLATILITY_BLOCKED,),
            )
        if not (
            impulse_state.fully_warmed
            and pullback_state.fully_warmed
            and context.features_15m.is_fully_warmed
        ):
            return self._no_trade(
                context,
                history,
                (TrendImpulsePullbackReasonCode.CONTEXT_NOT_FULLY_WARMED,),
            )
        a0 = prior.atr_14
        a_s = context.features_15m.atr_14
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in (a0, a_s)
        ):
            return self._no_trade(
                context, history, (TrendImpulsePullbackReasonCode.ATR14_UNAVAILABLE,)
            )
        assert isinstance(a0, Decimal) and isinstance(a_s, Decimal)
        impulse = impulse_state.candle
        pullback = pullback_state.candle
        resumption = context.candle_15m
        with localcontext(_CONTEXT):
            body = abs(impulse.close - impulse.open)
            width = impulse.high - impulse.low
            long = impulse.close > impulse.open
            short = impulse.close < impulse.open
            if (
                width <= 0
                or not (long or short)
                or body < Decimal("1.50") * a0
                or body < Decimal("0.70") * width
            ):
                return self._no_trade(
                    context,
                    history,
                    (TrendImpulsePullbackReasonCode.IMPULSE_NOT_QUALIFIED,),
                    body=body,
                    width=width,
                )
            boundary = (
                impulse.close - Decimal("0.50") * body
                if long
                else impulse.close + Decimal("0.50") * body
            )
            pullback_ok = (
                pullback.close <= pullback.open
                and pullback.close < impulse.close
                and pullback.low >= boundary
                and pullback.high <= impulse.high
                if long
                else pullback.close >= pullback.open
                and pullback.close > impulse.close
                and pullback.high <= boundary
                and pullback.low >= impulse.low
            )
            if not pullback_ok:
                return self._no_trade(
                    context,
                    history,
                    (TrendImpulsePullbackReasonCode.PULLBACK_NOT_QUALIFIED,),
                    body=body,
                    width=width,
                    boundary=boundary,
                )
            resumption_ok = (
                resumption.close > resumption.open
                and resumption.close > pullback.high
                and resumption.low >= boundary
                if long
                else resumption.close < resumption.open
                and resumption.close < pullback.low
                and resumption.high <= boundary
            )
            if not resumption_ok:
                return self._no_trade(
                    context,
                    history,
                    (TrendImpulsePullbackReasonCode.RESUMPTION_NOT_CONFIRMED,),
                    body=body,
                    width=width,
                    boundary=boundary,
                )
            stop = (
                min(pullback.low, resumption.low) - Decimal("0.10") * a_s
                if long
                else max(pullback.high, resumption.high) + Decimal("0.10") * a_s
            )
            reference = resumption.close
            target = (
                reference + Decimal(2) * (reference - stop)
                if long
                else reference - Decimal(2) * (stop - reference)
            )
            if (
                stop <= 0
                or target <= 0
                or (long and not stop < reference < target)
                or (short and not target < reference < stop)
            ):
                return self._no_trade(
                    context,
                    history,
                    (TrendImpulsePullbackReasonCode.STRUCTURAL_STOP_INVALID,),
                    body=body,
                    width=width,
                    boundary=boundary,
                    stop=stop,
                    target=target,
                )
            gross, costs, net = _cost_room(reference, target, long=long)
            audit = self._audit(
                context,
                history,
                body=body,
                width=width,
                boundary=boundary,
                stop=stop,
                target=target,
                gross=gross,
                costs=costs,
                net=net,
            )
            if gross < Decimal(3) * costs or net <= 0:
                return self._no_trade(
                    context,
                    history,
                    (TrendImpulsePullbackReasonCode.BASE_COST_ROOM_REJECTED,),
                    audit=audit,
                )
            return StrategyEvaluation(
                self.strategy_id,
                self.strategy_version,
                context.source_id,
                context.symbol,
                context.observed_at,
                CandidateAction.LONG_CANDIDATE if long else CandidateAction.SHORT_CANDIDATE,
                reference,
                stop,
                (
                    TrendImpulsePullbackReasonCode.THREE_CANDLES_COMPLETE,
                    TrendImpulsePullbackReasonCode.REQUIRED_CONTEXT_READY,
                    TrendImpulsePullbackReasonCode.EXTREME_VOLATILITY_CLEAR,
                    TrendImpulsePullbackReasonCode.ATR14_POSITIVE,
                    TrendImpulsePullbackReasonCode.IMPULSE_QUALIFIED,
                    TrendImpulsePullbackReasonCode.PULLBACK_QUALIFIED,
                    TrendImpulsePullbackReasonCode.RESUMPTION_CONFIRMED,
                    TrendImpulsePullbackReasonCode.STRUCTURAL_STOP_VALID,
                    TrendImpulsePullbackReasonCode.BASE_COST_ROOM_PASSED,
                ),
                (),
                audit,
            )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        history: tuple[_Observed, ...],
        reasons: tuple[TrendImpulsePullbackReasonCode, ...],
        *,
        body: Decimal | None = None,
        width: Decimal | None = None,
        boundary: Decimal | None = None,
        stop: Decimal | None = None,
        target: Decimal | None = None,
        audit: TrendImpulsePullbackRuleAudit | None = None,
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
                body=body,
                width=width,
                boundary=boundary,
                stop=stop,
                target=target,
            ),
        )

    def _audit(
        self,
        context: StrategyEvaluationContext,
        history: tuple[_Observed, ...],
        *,
        body: Decimal | None = None,
        width: Decimal | None = None,
        boundary: Decimal | None = None,
        stop: Decimal | None = None,
        target: Decimal | None = None,
        gross: Decimal | None = None,
        costs: Decimal | None = None,
        net: Decimal | None = None,
    ) -> TrendImpulsePullbackRuleAudit:
        impulse = history[-2].candle if len(history) == 3 else None
        pullback = history[-1].candle if len(history) == 3 else None
        resumption = context.candle_15m
        return TrendImpulsePullbackRuleAudit(
            POLICY_ID,
            len(history),
            None if impulse is None else impulse.open_time,
            None if impulse is None else impulse.open,
            None if impulse is None else impulse.high,
            None if impulse is None else impulse.low,
            None if impulse is None else impulse.close,
            None if pullback is None else pullback.open_time,
            None if pullback is None else pullback.open,
            None if pullback is None else pullback.high,
            None if pullback is None else pullback.low,
            None if pullback is None else pullback.close,
            resumption.open_time,
            resumption.open,
            resumption.high,
            resumption.low,
            resumption.close,
            history[-3].atr_14 if len(history) == 3 else None,
            context.features_15m.atr_14,
            body,
            width,
            boundary,
            stop,
            target,
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
        modeled_cost = (
            abs(entry.fill_price - reference)
            + abs(exit_fill.fill_price - target)
            + entry.fee_quote
            + exit_fill.fee_quote
        )
        direction = Decimal(1) if long else Decimal(-1)
        net = (exit_fill.fill_price - entry.fill_price) * direction
        net -= entry.fee_quote + exit_fill.fee_quote
    return gross, modeled_cost, net
