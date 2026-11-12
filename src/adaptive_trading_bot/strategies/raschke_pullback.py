"""Raschke-informed closed-bar, retest-target first-pullback adaptation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256

from adaptive_trading_bot.features import ConfirmedPivot
from adaptive_trading_bot.features.wilder_adx import WilderAdxCalculator, WilderAdxSnapshot
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

POLICY_ID = "RASCHKE_INFORMED_CLOSED_BAR_RETEST_TARGET_V1"
STRATEGY_VERSION = "0.1.0"
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class RaschkePullbackArm(StrEnum):
    REFERENCE = "raschke_pullback_reference"
    FIB_ZONE = "raschke_pullback_fib_zone"
    ROUND_ZONE = "raschke_pullback_round_zone"


class PullbackDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class RaschkePullbackReasonCode(StrEnum):
    ADX_UNAVAILABLE = "ADX_UNAVAILABLE"
    REQUIRED_CONTEXT_NOT_READY = "REQUIRED_CONTEXT_NOT_READY"
    EXTREME_VOLATILITY_BLOCKED = "EXTREME_VOLATILITY_BLOCKED"
    HISTORY_GAP_RESET = "HISTORY_GAP_RESET"
    EPISODE_ARMED = "EPISODE_ARMED"
    EPISODE_ACTIVE = "EPISODE_ACTIVE"
    EMA_TOUCH = "EMA_TOUCH"
    GAP_THROUGH_EMA = "GAP_THROUGH_EMA"
    AMBIGUOUS_TOUCH_EXTREME = "AMBIGUOUS_TOUCH_EXTREME"
    DI_REVERSAL = "DI_REVERSAL"
    ORIGIN_BROKEN = "ORIGIN_BROKEN"
    CONFIRMATION = "CONFIRMATION"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    ANCHOR_UNAVAILABLE = "ANCHOR_UNAVAILABLE"
    DEPTH_FILTER_REJECTED = "DEPTH_FILTER_REJECTED"
    DEPTH_FILTER_PASSED = "DEPTH_FILTER_PASSED"
    SETUP_CONSUMED = "SETUP_CONSUMED"
    RESET_OBSERVED = "RESET_OBSERVED"
    NO_SETUP = "NO_SETUP"


@dataclass(frozen=True, slots=True)
class RaschkePullbackAudit:
    policy_id: str
    arm: str
    episode_id: str | None
    event: str
    direction: str | None
    adx: Decimal | None
    previous_adx: Decimal | None
    plus_di: Decimal | None
    minus_di: Decimal | None
    ema_20: Decimal | None
    armed_at: datetime | None
    touch_at: datetime | None
    confirmation_at: datetime | None
    origin_kind: str | None
    origin_open_time: datetime | None
    origin_confirmed_at: datetime | None
    origin_price: Decimal | None
    favorable_extreme: Decimal | None
    favorable_extreme_known_at: datetime | None
    pullback_extreme: Decimal | None
    pullback_extreme_known_at: datetime | None
    entry_reference: Decimal | None
    structural_stop: Decimal | None
    frozen_target: Decimal | None
    depth: Decimal | None
    depth_available: bool
    filter_lower: Decimal | None
    filter_upper: Decimal | None
    rejection: str | None


@dataclass(frozen=True, slots=True)
class _Episode:
    identity: str
    direction: PullbackDirection
    armed_at: datetime
    origin: ConfirmedPivot | None
    favorable_extreme: Decimal
    favorable_known_at: datetime
    touch_at: datetime | None = None
    pullback_extreme: Decimal | None = None
    pullback_known_at: datetime | None = None


Evaluation = StrategyEvaluation[RaschkePullbackReasonCode, RaschkePullbackAudit]


class RaschkePullbackStrategy:
    """Track one causal first-pullback episode for one fixed depth arm."""

    strategy_version = STRATEGY_VERSION

    def __init__(self, arm: RaschkePullbackArm) -> None:
        self.arm = arm
        self.strategy_id = arm.value
        self._adx: dict[tuple[object, object], WilderAdxCalculator] = {}
        self._episode: dict[tuple[object, object], _Episode | None] = {}
        self._reset_eligible: dict[tuple[object, object], bool] = {}
        self._blocked_direction: dict[tuple[object, object], PullbackDirection | None] = {}
        self._last_context: dict[tuple[object, object], StrategyEvaluationContext] = {}
        self._last_result: dict[tuple[object, object], Evaluation] = {}

    def evaluate(self, context: StrategyEvaluationContext) -> Evaluation:
        context.validate()
        key = (context.source_id, context.symbol)
        previous_context = self._last_context.get(key)
        if previous_context is not None:
            if context.observed_at < previous_context.observed_at:
                raise StrategyContextError("Raschke pullback observation moved backward")
            if context.observed_at == previous_context.observed_at:
                if context == previous_context:
                    return self._last_result[key]
                raise StrategyContextError("duplicate Raschke timestamp changed its facts")

        snapshot = self._adx.setdefault(key, WilderAdxCalculator()).advance(context.candle_15m)
        if snapshot.gap_reset:
            self._episode[key] = None
            self._reset_eligible[key] = True
            self._blocked_direction[key] = None
            result = self._no_trade(context, snapshot, "HISTORY_GAP_RESET", None)
            return self._remember(key, context, result)

        episode = self._episode.get(key)
        reset_eligible = self._reset_eligible.get(key, True)
        blocked = self._blocked_direction.get(key)
        reset_now = False
        if not reset_eligible and _reset_condition(snapshot, blocked):
            reset_eligible = True
            blocked = None
            reset_now = True

        ready = (
            context.features_15m.is_fully_warmed and context.multi_timeframe_regime.is_fully_ready
        )
        extreme = context.multi_timeframe_regime.has_extreme_volatility
        if episode is not None and (not ready or extreme):
            blocked = episode.direction
            episode = None
        if episode is not None and _direction_reversed(snapshot, episode.direction):
            episode = None
            reset_eligible = True
            blocked = None
            reset_now = True

        if episode is None:
            self._episode[key] = None
            self._reset_eligible[key] = reset_eligible
            self._blocked_direction[key] = blocked
            if reset_now:
                result = self._no_trade(context, snapshot, "RESET_OBSERVED", None)
            elif not ready:
                result = self._no_trade(context, snapshot, "REQUIRED_CONTEXT_NOT_READY", None)
            elif extreme:
                result = self._no_trade(context, snapshot, "EXTREME_VOLATILITY_BLOCKED", None)
            elif reset_eligible:
                direction = _arming_direction(context, snapshot)
                if direction is not None:
                    episode = _arm(context, direction)
                    self._episode[key] = episode
                    self._reset_eligible[key] = False
                    self._blocked_direction[key] = direction
                    result = self._no_trade(context, snapshot, "EPISODE_ARMED", episode)
                else:
                    result = self._no_trade(context, snapshot, "NO_SETUP", None)
            else:
                result = self._no_trade(context, snapshot, "NO_SETUP", None)
            return self._remember(key, context, result)

        ema = context.features_15m.ema_20
        if ema is None:
            self._episode[key] = None
            result = self._no_trade(context, snapshot, "REQUIRED_CONTEXT_NOT_READY", episode)
            return self._remember(key, context, result)
        candle = context.candle_15m
        if _origin_broken(episode, candle.low, candle.high):
            self._episode[key] = None
            result = self._no_trade(context, snapshot, "ORIGIN_BROKEN", episode)
            return self._remember(key, context, result)

        if episode.touch_at is None:
            if _gapped_through(episode.direction, candle.low, candle.high, ema):
                self._episode[key] = None
                result = self._no_trade(context, snapshot, "GAP_THROUGH_EMA", episode)
                return self._remember(key, context, result)
            if candle.low <= ema <= candle.high:
                if _favorable_beyond(
                    episode.direction, candle.low, candle.high, episode.favorable_extreme
                ):
                    self._episode[key] = None
                    result = self._no_trade(context, snapshot, "AMBIGUOUS_TOUCH_EXTREME", episode)
                    return self._remember(key, context, result)
                episode = replace(
                    episode,
                    touch_at=context.observed_at,
                    pullback_extreme=(
                        candle.low if episode.direction is PullbackDirection.LONG else candle.high
                    ),
                    pullback_known_at=context.observed_at,
                )
                self._episode[key] = episode
                result = self._no_trade(context, snapshot, "EMA_TOUCH", episode)
                return self._remember(key, context, result)
            episode = _advance_favorable(episode, candle.low, candle.high, context.observed_at)
            self._episode[key] = episode
            result = self._no_trade(context, snapshot, "EPISODE_ACTIVE", episode)
            return self._remember(key, context, result)

        episode = _advance_pullback(episode, candle.low, candle.high, context.observed_at)
        assert previous_context is not None
        confirmed = _confirmed(
            episode.direction,
            candle.close,
            previous_context.candle_15m.high,
            previous_context.candle_15m.low,
            ema,
        )
        if not confirmed:
            self._episode[key] = episode
            result = self._no_trade(context, snapshot, "EPISODE_ACTIVE", episode)
            return self._remember(key, context, result)

        self._episode[key] = None
        self._reset_eligible[key] = False
        self._blocked_direction[key] = episode.direction
        result = self._confirmation(context, snapshot, episode)
        return self._remember(key, context, result)

    def _confirmation(
        self,
        context: StrategyEvaluationContext,
        snapshot: WilderAdxSnapshot,
        episode: _Episode,
    ) -> Evaluation:
        entry = context.candle_15m.close
        stop = episode.pullback_extreme
        target = episode.favorable_extreme
        assert stop is not None
        valid = (
            stop < entry < target
            if episode.direction is PullbackDirection.LONG
            else target < entry < stop
        ) and all(value.is_finite() and value > 0 for value in (entry, stop, target))
        depth = _depth(episode)
        lower, upper = _bounds(self.arm)
        rejection: str | None = None
        if not valid:
            rejection = "INVALID_GEOMETRY"
        elif self.arm is not RaschkePullbackArm.REFERENCE and depth is None:
            rejection = "ANCHOR_UNAVAILABLE"
        elif (
            lower is not None
            and upper is not None
            and depth is not None
            and not lower <= depth <= upper
        ):
            rejection = "DEPTH_FILTER_REJECTED"
        audit = self._audit(
            context,
            snapshot,
            "CONFIRMATION",
            episode,
            entry=entry,
            stop=stop,
            target=target,
            depth=depth,
            rejection=rejection,
        )
        if rejection is not None:
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
                (
                    RaschkePullbackReasonCode.ANCHOR_UNAVAILABLE
                    if rejection == "ANCHOR_UNAVAILABLE"
                    else RaschkePullbackReasonCode.DEPTH_FILTER_REJECTED
                    if rejection == "DEPTH_FILTER_REJECTED"
                    else RaschkePullbackReasonCode.INVALID_GEOMETRY,
                    RaschkePullbackReasonCode.SETUP_CONSUMED,
                ),
                audit,
            )
        return StrategyEvaluation(
            self.strategy_id,
            self.strategy_version,
            context.source_id,
            context.symbol,
            context.observed_at,
            (
                CandidateAction.LONG_CANDIDATE
                if episode.direction is PullbackDirection.LONG
                else CandidateAction.SHORT_CANDIDATE
            ),
            entry,
            stop,
            (
                RaschkePullbackReasonCode.CONFIRMATION,
                RaschkePullbackReasonCode.DEPTH_FILTER_PASSED,
                RaschkePullbackReasonCode.SETUP_CONSUMED,
            ),
            (),
            audit,
        )

    def _no_trade(
        self,
        context: StrategyEvaluationContext,
        snapshot: WilderAdxSnapshot,
        event: str,
        episode: _Episode | None,
    ) -> Evaluation:
        reason = RaschkePullbackReasonCode(event)
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
            self._audit(context, snapshot, event, episode),
        )

    def _audit(
        self,
        context: StrategyEvaluationContext,
        snapshot: WilderAdxSnapshot,
        event: str,
        episode: _Episode | None,
        *,
        entry: Decimal | None = None,
        stop: Decimal | None = None,
        target: Decimal | None = None,
        depth: Decimal | None = None,
        rejection: str | None = None,
    ) -> RaschkePullbackAudit:
        origin = None if episode is None else episode.origin
        lower, upper = _bounds(self.arm)
        return RaschkePullbackAudit(
            POLICY_ID,
            self.arm.value,
            None if episode is None else episode.identity,
            event,
            None if episode is None else episode.direction.value,
            snapshot.adx,
            snapshot.previous_adx,
            snapshot.plus_di,
            snapshot.minus_di,
            context.features_15m.ema_20,
            None if episode is None else episode.armed_at,
            None if episode is None else episode.touch_at,
            context.observed_at if event == "CONFIRMATION" else None,
            None if origin is None else origin.kind.value,
            None if origin is None else origin.pivot_open_time,
            None if origin is None else origin.confirmed_at,
            None if origin is None else origin.pivot_price,
            None if episode is None else episode.favorable_extreme,
            None if episode is None else episode.favorable_known_at,
            None if episode is None else episode.pullback_extreme,
            None if episode is None else episode.pullback_known_at,
            entry,
            stop,
            target,
            depth,
            depth is not None,
            lower,
            upper,
            rejection,
        )

    def _remember(
        self, key: tuple[object, object], context: StrategyEvaluationContext, result: Evaluation
    ) -> Evaluation:
        self._last_context[key] = context
        self._last_result[key] = result
        return result


def _arming_direction(
    context: StrategyEvaluationContext, snapshot: WilderAdxSnapshot
) -> PullbackDirection | None:
    adx, previous, plus, minus = (
        snapshot.adx,
        snapshot.previous_adx,
        snapshot.plus_di,
        snapshot.minus_di,
    )
    ema = context.features_15m.ema_20
    if None in (adx, previous, plus, minus, ema):
        return None
    assert adx is not None and previous is not None and plus is not None and minus is not None
    assert ema is not None
    candle = context.candle_15m
    if adx > 30 and adx > previous and plus > minus and candle.low > ema:
        return PullbackDirection.LONG
    if adx > 30 and adx > previous and minus > plus and candle.high < ema:
        return PullbackDirection.SHORT
    return None


def _arm(context: StrategyEvaluationContext, direction: PullbackDirection) -> _Episode:
    origin = (
        context.structure_15m.latest_confirmed_low
        if direction is PullbackDirection.LONG
        else context.structure_15m.latest_confirmed_high
    )
    favorable = (
        context.candle_15m.high if direction is PullbackDirection.LONG else context.candle_15m.low
    )
    raw = "|".join(
        (
            context.source_id.value,
            str(context.symbol),
            direction.value,
            context.observed_at.isoformat(),
        )
    )
    return _Episode(
        sha256(raw.encode()).hexdigest(),
        direction,
        context.observed_at,
        origin,
        favorable,
        context.observed_at,
    )


def _reset_condition(snapshot: WilderAdxSnapshot, blocked: PullbackDirection | None) -> bool:
    if snapshot.adx is not None and snapshot.adx <= 30:
        return True
    return blocked is not None and _direction_reversed(snapshot, blocked)


def _direction_reversed(snapshot: WilderAdxSnapshot, direction: PullbackDirection) -> bool:
    if snapshot.plus_di is None or snapshot.minus_di is None:
        return False
    return (
        snapshot.minus_di > snapshot.plus_di
        if direction is PullbackDirection.LONG
        else snapshot.plus_di > snapshot.minus_di
    )


def _origin_broken(episode: _Episode, low: Decimal, high: Decimal) -> bool:
    if episode.origin is None:
        return False
    return (
        low < episode.origin.pivot_price
        if episode.direction is PullbackDirection.LONG
        else high > episode.origin.pivot_price
    )


def _gapped_through(
    direction: PullbackDirection, low: Decimal, high: Decimal, ema: Decimal
) -> bool:
    return high < ema if direction is PullbackDirection.LONG else low > ema


def _favorable_beyond(
    direction: PullbackDirection, low: Decimal, high: Decimal, frozen: Decimal
) -> bool:
    return high > frozen if direction is PullbackDirection.LONG else low < frozen


def _advance_favorable(
    episode: _Episode, low: Decimal, high: Decimal, known_at: datetime
) -> _Episode:
    candidate = high if episode.direction is PullbackDirection.LONG else low
    improved = (
        candidate > episode.favorable_extreme
        if episode.direction is PullbackDirection.LONG
        else candidate < episode.favorable_extreme
    )
    return (
        replace(episode, favorable_extreme=candidate, favorable_known_at=known_at)
        if improved
        else episode
    )


def _advance_pullback(
    episode: _Episode, low: Decimal, high: Decimal, known_at: datetime
) -> _Episode:
    assert episode.pullback_extreme is not None
    candidate = low if episode.direction is PullbackDirection.LONG else high
    extended = (
        candidate < episode.pullback_extreme
        if episode.direction is PullbackDirection.LONG
        else candidate > episode.pullback_extreme
    )
    return (
        replace(episode, pullback_extreme=candidate, pullback_known_at=known_at)
        if extended
        else episode
    )


def _confirmed(
    direction: PullbackDirection,
    close: Decimal,
    previous_high: Decimal,
    previous_low: Decimal,
    ema: Decimal,
) -> bool:
    return (
        close > previous_high and close > ema
        if direction is PullbackDirection.LONG
        else close < previous_low and close < ema
    )


def _depth(episode: _Episode) -> Decimal | None:
    origin = episode.origin
    pullback = episode.pullback_extreme
    if origin is None or pullback is None or origin.pivot_open_time >= episode.armed_at:
        return None
    with localcontext(_CONTEXT):
        if episode.direction is PullbackDirection.LONG:
            denominator = episode.favorable_extreme - origin.pivot_price
            numerator = episode.favorable_extreme - pullback
        else:
            denominator = origin.pivot_price - episode.favorable_extreme
            numerator = pullback - episode.favorable_extreme
        if denominator <= 0:
            return None
        return numerator / denominator


def _bounds(arm: RaschkePullbackArm) -> tuple[Decimal | None, Decimal | None]:
    if arm is RaschkePullbackArm.FIB_ZONE:
        return Decimal("0.382"), Decimal("0.618")
    if arm is RaschkePullbackArm.ROUND_ZONE:
        return Decimal("0.40"), Decimal("0.60")
    return None, None
