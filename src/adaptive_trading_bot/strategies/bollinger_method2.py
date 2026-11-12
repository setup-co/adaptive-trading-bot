"""Research-only Bollinger Method-II entry ablation with project exits."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Candle, Timeframe
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

CONTROL_ID = "bb_price_control_1h"
CONFIRMED_ID = "bb_mfi10_hourly"
STRATEGY_VERSION = "0.2.0"
POLICY_ID = "BB_METHOD2_BOOK_ENTRY_1H_PROJECT_30M_EXIT_V1"
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class BollingerMethod2HourlyContext(StrategyEvaluationContext):
    """Normal project context plus the actual completed hourly OHLCV candle."""

    candle_1h: Candle

    def validate(self) -> None:
        StrategyEvaluationContext.validate(self)
        if (
            self.candle_1h.symbol != self.symbol
            or self.candle_1h.timeframe is not Timeframe.HOUR_1
            or self.candle_1h.close_time != self.observed_at
        ):
            raise StrategyContextError("hourly decision candle identity or close time mismatch")


class BollingerMethod2ReasonCode(StrEnum):
    PRICE_LONG = "PRICE_LONG"
    PRICE_SHORT = "PRICE_SHORT"
    MFI_LONG_CONFIRMED = "MFI_LONG_CONFIRMED"
    MFI_SHORT_CONFIRMED = "MFI_SHORT_CONFIRMED"
    PROJECT_GEOMETRY_VALID = "PROJECT_GEOMETRY_VALID"
    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
    REQUIRED_CONTEXT_NOT_READY = "REQUIRED_CONTEXT_NOT_READY"
    EXTREME_VOLATILITY_BLOCKED = "EXTREME_VOLATILITY_BLOCKED"
    CONTEXT_NOT_FULLY_WARMED = "CONTEXT_NOT_FULLY_WARMED"
    BAND_WIDTH_UNAVAILABLE = "BAND_WIDTH_UNAVAILABLE"
    PRICE_CONDITION_NOT_MET = "PRICE_CONDITION_NOT_MET"
    MFI_UNAVAILABLE = "MFI_UNAVAILABLE"
    MFI_NOT_CONFIRMED = "MFI_NOT_CONFIRMED"
    PROJECT_GEOMETRY_INVALID = "PROJECT_GEOMETRY_INVALID"
    HISTORY_GAP_RESET = "HISTORY_GAP_RESET"


@dataclass(frozen=True, slots=True)
class BollingerMethod2Audit:
    policy_id: str
    arm: str
    close_count: int
    sma20: Decimal | None
    sigma20_population: Decimal | None
    upper_band: Decimal | None
    lower_band: Decimal | None
    percent_b: Decimal | None
    mfi10: Decimal | None
    mfi_positive_flow: Decimal | None
    mfi_negative_flow: Decimal | None
    decision_reference: Decimal
    frozen_stop: Decimal | None
    frozen_target: Decimal | None


Evaluation = StrategyEvaluation[BollingerMethod2ReasonCode, BollingerMethod2Audit]


@dataclass(frozen=True, slots=True)
class BollingerMethod2Configuration:
    require_mfi: bool

    @property
    def strategy_id(self) -> str:
        return CONFIRMED_ID if self.require_mfi else CONTROL_ID


class BollingerMethod2Strategy:
    """Evaluate the same causal price window with optional MFI(10) confirmation."""

    strategy_version = STRATEGY_VERSION

    def __init__(self, configuration: BollingerMethod2Configuration) -> None:
        self.configuration = configuration
        self.strategy_id = configuration.strategy_id
        self._history: dict[tuple[object, object], tuple[Candle, ...]] = {}

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        context.validate()
        candle = _hourly_candle(context)
        key = (context.source_id, context.symbol)
        history = self._history.get(key, ())
        if history and candle.open_time < history[-1].close_time:
            raise StrategyContextError("Bollinger history moved backward or duplicated")
        if history and candle.open_time != history[-1].close_time:
            self._history[key] = (candle,)
            return self._no_trade(context, (), BollingerMethod2ReasonCode.HISTORY_GAP_RESET)
        try:
            return self._evaluate_before_insert(context, history)
        finally:
            self._history[key] = (*history, candle)[-20:]

    def _evaluate_before_insert(
        self, context: StrategyEvaluationContext, history: tuple[Candle, ...]
    ) -> Evaluation:
        candle = _hourly_candle(context)
        if len(history) < 19:
            return self._no_trade(context, history, BollingerMethod2ReasonCode.WARMUP_INCOMPLETE)
        if not context.multi_timeframe_regime.is_fully_ready:
            return self._no_trade(
                context, history, BollingerMethod2ReasonCode.REQUIRED_CONTEXT_NOT_READY
            )
        if context.multi_timeframe_regime.has_extreme_volatility:
            return self._no_trade(
                context, history, BollingerMethod2ReasonCode.EXTREME_VOLATILITY_BLOCKED
            )
        if not context.features_15m.is_fully_warmed:
            return self._no_trade(
                context, history, BollingerMethod2ReasonCode.CONTEXT_NOT_FULLY_WARMED
            )
        with localcontext(_CONTEXT):
            closes = (*tuple(item.close for item in history[-19:]), candle.close)
            sma = sum(closes, Decimal(0)) / Decimal(20)
            variance = sum(((value - sma) ** 2 for value in closes), Decimal(0)) / Decimal(20)
            sigma = variance.sqrt()
            upper = sma + Decimal(2) * sigma
            lower = sma - Decimal(2) * sigma
            width = upper - lower
            if not width.is_finite() or width <= 0:
                return self._no_trade(
                    context,
                    history,
                    BollingerMethod2ReasonCode.BAND_WIDTH_UNAVAILABLE,
                    sma=sma,
                    sigma=sigma,
                    upper=upper,
                    lower=lower,
                )
            percent_b = (candle.close - lower) / width
            mfi, positive, negative = _mfi10((*history[-10:], candle))
            long = percent_b > Decimal("0.8")
            short = percent_b < Decimal("0.2")
            if not long and not short:
                return self._no_trade(
                    context,
                    history,
                    BollingerMethod2ReasonCode.PRICE_CONDITION_NOT_MET,
                    sma=sma,
                    sigma=sigma,
                    upper=upper,
                    lower=lower,
                    percent_b=percent_b,
                    mfi=mfi,
                    positive=positive,
                    negative=negative,
                )
            if self.configuration.require_mfi:
                if mfi is None:
                    return self._no_trade(
                        context,
                        history,
                        BollingerMethod2ReasonCode.MFI_UNAVAILABLE,
                        sma=sma,
                        sigma=sigma,
                        upper=upper,
                        lower=lower,
                        percent_b=percent_b,
                        positive=positive,
                        negative=negative,
                    )
                confirmed = (long and mfi > Decimal(80)) or (short and mfi < Decimal(20))
                if not confirmed:
                    return self._no_trade(
                        context,
                        history,
                        BollingerMethod2ReasonCode.MFI_NOT_CONFIRMED,
                        sma=sma,
                        sigma=sigma,
                        upper=upper,
                        lower=lower,
                        percent_b=percent_b,
                        mfi=mfi,
                        positive=positive,
                        negative=negative,
                    )
            stop = lower if long else upper
            distance = abs(candle.close - stop)
            target = (
                candle.close + Decimal(2) * distance
                if long
                else candle.close - Decimal(2) * distance
            )
            if (
                stop <= 0
                or target <= 0
                or distance <= 0
                or (long and not stop < candle.close < target)
                or (short and not target < candle.close < stop)
            ):
                return self._no_trade(
                    context,
                    history,
                    BollingerMethod2ReasonCode.PROJECT_GEOMETRY_INVALID,
                    sma=sma,
                    sigma=sigma,
                    upper=upper,
                    lower=lower,
                    percent_b=percent_b,
                    mfi=mfi,
                    positive=positive,
                    negative=negative,
                    stop=stop,
                    target=target,
                )
            reasons = [
                BollingerMethod2ReasonCode.PRICE_LONG
                if long
                else BollingerMethod2ReasonCode.PRICE_SHORT
            ]
            if self.configuration.require_mfi:
                reasons.append(
                    BollingerMethod2ReasonCode.MFI_LONG_CONFIRMED
                    if long
                    else BollingerMethod2ReasonCode.MFI_SHORT_CONFIRMED
                )
            reasons.append(BollingerMethod2ReasonCode.PROJECT_GEOMETRY_VALID)
            return StrategyEvaluation(
                self.strategy_id,
                self.strategy_version,
                context.source_id,
                context.symbol,
                context.observed_at,
                CandidateAction.LONG_CANDIDATE if long else CandidateAction.SHORT_CANDIDATE,
                candle.close,
                stop,
                tuple(reasons),
                (),
                self._audit(
                    candle,
                    len(history),
                    sma,
                    sigma,
                    upper,
                    lower,
                    percent_b,
                    mfi,
                    positive,
                    negative,
                    stop,
                    target,
                ),
            )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        history: tuple[Candle, ...],
        reason: BollingerMethod2ReasonCode,
        **facts: Decimal | None,
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
            (reason,),
            self._audit(
                _hourly_candle(context),
                len(history),
                facts.get("sma"),
                facts.get("sigma"),
                facts.get("upper"),
                facts.get("lower"),
                facts.get("percent_b"),
                facts.get("mfi"),
                facts.get("positive"),
                facts.get("negative"),
                facts.get("stop"),
                facts.get("target"),
            ),
        )

    def _audit(
        self,
        candle: Candle,
        count: int,
        sma: Decimal | None,
        sigma: Decimal | None,
        upper: Decimal | None,
        lower: Decimal | None,
        percent_b: Decimal | None,
        mfi: Decimal | None,
        positive: Decimal | None,
        negative: Decimal | None,
        stop: Decimal | None,
        target: Decimal | None,
    ) -> BollingerMethod2Audit:
        return BollingerMethod2Audit(
            POLICY_ID,
            self.strategy_id,
            count,
            sma,
            sigma,
            upper,
            lower,
            percent_b,
            mfi,
            positive,
            negative,
            candle.close,
            stop,
            target,
        )


def _hourly_candle(context: StrategyEvaluationContext) -> Candle:
    if not isinstance(context, BollingerMethod2HourlyContext):
        raise StrategyContextError("Bollinger Method II hourly strategy requires hourly context")
    return context.candle_1h


def _mfi10(candles: tuple[Candle, ...]) -> tuple[Decimal | None, Decimal, Decimal]:
    if len(candles) != 11:
        return None, Decimal(0), Decimal(0)
    with localcontext(_CONTEXT):
        typical = tuple((item.high + item.low + item.close) / Decimal(3) for item in candles)
        positive = Decimal(0)
        negative = Decimal(0)
        for index in range(1, 11):
            flow = typical[index] * candles[index].volume
            if typical[index] > typical[index - 1]:
                positive += flow
            elif typical[index] < typical[index - 1]:
                negative += flow
        total = positive + negative
        return (None if total == 0 else Decimal(100) * positive / total), positive, negative
