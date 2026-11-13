"""Deterministic Wilder ADX/DI over one causal closed-candle series."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from adaptive_trading_bot.domain import Candle

_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_PERIOD = 14


@dataclass(frozen=True, slots=True)
class WilderAdxSnapshot:
    """ADX/DI values observable after accepting the current closed candle."""

    adx: Decimal | None
    previous_adx: Decimal | None
    plus_di: Decimal | None
    minus_di: Decimal | None
    dx: Decimal | None
    movement_count: int
    gap_reset: bool


@dataclass(frozen=True, slots=True)
class _State:
    previous: Candle | None = None
    movement_count: int = 0
    tr_sum: Decimal = Decimal(0)
    plus_sum: Decimal = Decimal(0)
    minus_sum: Decimal = Decimal(0)
    smoothed_tr: Decimal | None = None
    smoothed_plus: Decimal | None = None
    smoothed_minus: Decimal | None = None
    dx_seed: tuple[Decimal, ...] = ()
    adx: Decimal | None = None


class WilderAdxCalculator:
    """Advance ADX(14) with a 14-DM seed followed by a 14-DX ADX seed."""

    def __init__(self) -> None:
        self._state = _State()

    def advance(self, candle: Candle) -> WilderAdxSnapshot:
        state = self._state
        gap = state.previous is not None and candle.open_time != state.previous.close_time
        if gap:
            state = _State()
        previous = state.previous
        if previous is None:
            self._state = _State(previous=candle)
            return WilderAdxSnapshot(None, None, None, None, None, 0, gap)

        with localcontext(_CONTEXT):
            upward = candle.high - previous.high
            downward = previous.low - candle.low
            plus_dm = upward if upward > downward and upward > 0 else Decimal(0)
            minus_dm = downward if downward > upward and downward > 0 else Decimal(0)
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous.close),
                abs(candle.low - previous.close),
            )
            count = state.movement_count + 1
            tr_sum, plus_sum, minus_sum = state.tr_sum, state.plus_sum, state.minus_sum
            smoothed_tr = state.smoothed_tr
            smoothed_plus = state.smoothed_plus
            smoothed_minus = state.smoothed_minus
            if count <= _PERIOD:
                tr_sum += true_range
                plus_sum += plus_dm
                minus_sum += minus_dm
                if count == _PERIOD:
                    smoothed_tr, smoothed_plus, smoothed_minus = tr_sum, plus_sum, minus_sum
            else:
                assert smoothed_tr is not None
                assert smoothed_plus is not None
                assert smoothed_minus is not None
                smoothed_tr = smoothed_tr - smoothed_tr / Decimal(_PERIOD) + true_range
                smoothed_plus = smoothed_plus - smoothed_plus / Decimal(_PERIOD) + plus_dm
                smoothed_minus = smoothed_minus - smoothed_minus / Decimal(_PERIOD) + minus_dm

            plus_di = minus_di = dx = None
            if smoothed_tr is not None and smoothed_tr > 0:
                assert smoothed_plus is not None
                assert smoothed_minus is not None
                plus_di = Decimal(100) * smoothed_plus / smoothed_tr
                minus_di = Decimal(100) * smoothed_minus / smoothed_tr
                denominator = plus_di + minus_di
                dx = (
                    Decimal(0)
                    if denominator == 0
                    else Decimal(100) * abs(plus_di - minus_di) / denominator
                )

            dx_seed = state.dx_seed
            previous_adx = state.adx
            adx = state.adx
            if dx is not None:
                if len(dx_seed) < _PERIOD:
                    dx_seed = (*dx_seed, dx)
                    if len(dx_seed) == _PERIOD:
                        adx = sum(dx_seed, Decimal(0)) / Decimal(_PERIOD)
                else:
                    assert adx is not None
                    adx = (adx * Decimal(_PERIOD - 1) + dx) / Decimal(_PERIOD)

        self._state = _State(
            candle,
            count,
            tr_sum,
            plus_sum,
            minus_sum,
            smoothed_tr,
            smoothed_plus,
            smoothed_minus,
            dx_seed,
            adx,
        )
        return WilderAdxSnapshot(adx, previous_adx, plus_di, minus_di, dx, count, gap)
