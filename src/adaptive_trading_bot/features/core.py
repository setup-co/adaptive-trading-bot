"""Causal deterministic features over observed closed candles.

All derived arithmetic uses 34-digit ``ROUND_HALF_EVEN`` Decimal operations. EMA values use
SMA seeds, ATR and RSI use Wilder smoothing, and ``vwap_utc_day`` resets by candle-open UTC date.
``realized_volatility_20`` is an unannualized population deviation of simple returns.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import SourceId

_FEATURE_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class FeatureError(Exception):
    """Base error for invalid feature input or progression."""


class FeatureBatchError(FeatureError):
    """An observed batch violates the atomic batch contract."""


class FeatureSeriesError(FeatureError):
    """A candle violates its series identity or chronological contract."""


@dataclass(frozen=True, slots=True)
class FeatureSeries:
    """The complete identity of one independently tracked candle series."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe


def _series_order(series: FeatureSeries) -> tuple[str, str, int, str]:
    return (
        series.source_id.value,
        str(series.symbol),
        int(series.timeframe.duration.total_seconds()),
        series.timeframe.value,
    )


@dataclass(frozen=True, slots=True)
class ObservedCandle:
    """A closed candle paired with the series identity under which it was observed."""

    series: FeatureSeries
    candle: Candle


@dataclass(frozen=True, slots=True)
class ObservedCandleBatch:
    """Candles that became observable simultaneously at one close timestamp."""

    observed_at: datetime
    events: tuple[ObservedCandle, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "events",
            tuple(sorted(self.events, key=lambda event: _series_order(event.series))),
        )


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """Immutable V1 feature values after accepting one observed candle."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    observed_at: datetime
    candle_open_time: datetime
    candle_close_time: datetime
    bars_seen: int
    ema_20: Decimal | None
    ema_50: Decimal | None
    atr_14: Decimal | None
    rsi_14: Decimal | None
    vwap_utc_day: Decimal | None
    return_1: Decimal | None
    momentum_4: Decimal | None
    momentum_16: Decimal | None
    volume_zscore_20: Decimal | None
    realized_volatility_20: Decimal | None
    is_fully_warmed: bool


@dataclass(frozen=True, slots=True)
class FeatureBatch:
    """Canonical snapshots produced for one atomic observation time."""

    observed_at: datetime
    snapshots: tuple[FeatureSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _SeriesState:
    last_candle: Candle | None = None
    bars_seen: int = 0
    recent_closes: tuple[Decimal, ...] = ()
    ema20_seed_sum: Decimal = Decimal(0)
    ema50_seed_sum: Decimal = Decimal(0)
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    tr_count: int = 0
    tr_seed_sum: Decimal = Decimal(0)
    atr14: Decimal | None = None
    rsi_change_count: int = 0
    rsi_gain_seed_sum: Decimal = Decimal(0)
    rsi_loss_seed_sum: Decimal = Decimal(0)
    rsi_avg_gain: Decimal | None = None
    rsi_avg_loss: Decimal | None = None
    session_date: date | None = None
    cumulative_tpv: Decimal = Decimal(0)
    cumulative_volume: Decimal = Decimal(0)
    recent_volumes: tuple[Decimal, ...] = ()
    recent_returns: tuple[Decimal, ...] = ()

    def advance(
        self, series: FeatureSeries, candle: Candle, observed_at: datetime
    ) -> tuple[_SeriesState, FeatureSnapshot]:
        with localcontext(_FEATURE_DECIMAL_CONTEXT):
            bars_seen = self.bars_seen + 1
            return_1 = (
                None
                if self.last_candle is None
                else candle.close / self.last_candle.close - Decimal(1)
            )
            momentum4 = (
                candle.close / self.recent_closes[-4] - Decimal(1)
                if len(self.recent_closes) >= 4
                else None
            )
            momentum16 = (
                candle.close / self.recent_closes[-16] - Decimal(1)
                if len(self.recent_closes) >= 16
                else None
            )

            ema20_seed_sum, ema20 = _advance_ema(
                candle.close, bars_seen, 20, self.ema20_seed_sum, self.ema20
            )
            ema50_seed_sum, ema50 = _advance_ema(
                candle.close, bars_seen, 50, self.ema50_seed_sum, self.ema50
            )

            true_range = _true_range(candle, self.last_candle)
            tr_count = self.tr_count + 1
            tr_seed_sum = self.tr_seed_sum
            atr14 = self.atr14
            if tr_count <= 14:
                tr_seed_sum += true_range
                if tr_count == 14:
                    atr14 = tr_seed_sum / Decimal(14)
            else:
                assert atr14 is not None
                atr14 = (atr14 * Decimal(13) + true_range) / Decimal(14)

            (
                rsi_change_count,
                rsi_gain_seed_sum,
                rsi_loss_seed_sum,
                rsi_avg_gain,
                rsi_avg_loss,
                rsi14,
            ) = _advance_rsi(self, candle)

            candle_date = candle.open_time.date()
            if self.session_date != candle_date:
                cumulative_tpv = Decimal(0)
                cumulative_volume = Decimal(0)
            else:
                cumulative_tpv = self.cumulative_tpv
                cumulative_volume = self.cumulative_volume
            typical_price = (candle.high + candle.low + candle.close) / Decimal(3)
            cumulative_tpv += typical_price * candle.volume
            cumulative_volume += candle.volume
            session_vwap = (
                cumulative_tpv / cumulative_volume if cumulative_volume != Decimal(0) else None
            )

            recent_volumes = (*self.recent_volumes, candle.volume)[-20:]
            volume_zscore20 = _zscore(recent_volumes) if len(recent_volumes) == 20 else None

            recent_returns = self.recent_returns
            if return_1 is not None:
                recent_returns = (*recent_returns, return_1)[-20:]
            realized_volatility20 = (
                _population_std(recent_returns) if len(recent_returns) == 20 else None
            )

            new_state = replace(
                self,
                last_candle=candle,
                bars_seen=bars_seen,
                recent_closes=(*self.recent_closes, candle.close)[-16:],
                ema20_seed_sum=ema20_seed_sum,
                ema50_seed_sum=ema50_seed_sum,
                ema20=ema20,
                ema50=ema50,
                tr_count=tr_count,
                tr_seed_sum=tr_seed_sum,
                atr14=atr14,
                rsi_change_count=rsi_change_count,
                rsi_gain_seed_sum=rsi_gain_seed_sum,
                rsi_loss_seed_sum=rsi_loss_seed_sum,
                rsi_avg_gain=rsi_avg_gain,
                rsi_avg_loss=rsi_avg_loss,
                session_date=candle_date,
                cumulative_tpv=cumulative_tpv,
                cumulative_volume=cumulative_volume,
                recent_volumes=recent_volumes,
                recent_returns=recent_returns,
            )
            snapshot = FeatureSnapshot(
                source_id=series.source_id,
                symbol=series.symbol,
                timeframe=series.timeframe,
                observed_at=observed_at,
                candle_open_time=candle.open_time,
                candle_close_time=candle.close_time,
                bars_seen=bars_seen,
                ema_20=ema20,
                ema_50=ema50,
                atr_14=atr14,
                rsi_14=rsi14,
                vwap_utc_day=session_vwap,
                return_1=return_1,
                momentum_4=momentum4,
                momentum_16=momentum16,
                volume_zscore_20=volume_zscore20,
                realized_volatility_20=realized_volatility20,
                is_fully_warmed=bars_seen >= 50,
            )
            return new_state, snapshot


def _advance_ema(
    close: Decimal,
    bars_seen: int,
    period: int,
    seed_sum: Decimal,
    previous: Decimal | None,
) -> tuple[Decimal, Decimal | None]:
    if bars_seen <= period:
        seed_sum += close
        if bars_seen == period:
            return seed_sum, seed_sum / Decimal(period)
        return seed_sum, None
    assert previous is not None
    alpha = Decimal(2) / Decimal(period + 1)
    return seed_sum, alpha * close + (Decimal(1) - alpha) * previous


def _true_range(candle: Candle, previous: Candle | None) -> Decimal:
    if previous is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - previous.close),
        abs(candle.low - previous.close),
    )


def _advance_rsi(
    state: _SeriesState, candle: Candle
) -> tuple[int, Decimal, Decimal, Decimal | None, Decimal | None, Decimal | None]:
    if state.last_candle is None:
        return (0, Decimal(0), Decimal(0), None, None, None)

    change = candle.close - state.last_candle.close
    gain = max(change, Decimal(0))
    loss = max(-change, Decimal(0))
    count = state.rsi_change_count + 1
    gain_sum = state.rsi_gain_seed_sum
    loss_sum = state.rsi_loss_seed_sum
    avg_gain = state.rsi_avg_gain
    avg_loss = state.rsi_avg_loss
    if count <= 14:
        gain_sum += gain
        loss_sum += loss
        if count < 14:
            return count, gain_sum, loss_sum, None, None, None
        avg_gain = gain_sum / Decimal(14)
        avg_loss = loss_sum / Decimal(14)
    else:
        assert avg_gain is not None and avg_loss is not None
        avg_gain = (avg_gain * Decimal(13) + gain) / Decimal(14)
        avg_loss = (avg_loss * Decimal(13) + loss) / Decimal(14)
    return count, gain_sum, loss_sum, avg_gain, avg_loss, _rsi(avg_gain, avg_loss)


def _rsi(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == Decimal(0):
        return Decimal(50) if avg_gain == Decimal(0) else Decimal(100)
    if avg_gain == Decimal(0):
        return Decimal(0)
    relative_strength = avg_gain / avg_loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)


def _population_std(values: tuple[Decimal, ...]) -> Decimal:
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / Decimal(len(values))
    return variance.sqrt()


def _zscore(values: tuple[Decimal, ...]) -> Decimal:
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    standard_deviation = _population_std(values)
    if standard_deviation == Decimal(0):
        return Decimal(0)
    return (values[-1] - mean) / standard_deviation


class FeatureEngine:
    """Advance isolated feature state from atomic batches of already observed candles."""

    def __init__(self) -> None:
        self._states: dict[FeatureSeries, _SeriesState] = {}

    def process(self, batch: ObservedCandleBatch) -> FeatureBatch:
        """Validate and atomically apply a simultaneous batch."""

        observed_at = require_utc(batch.observed_at, field="observed_at")
        if not batch.events:
            raise FeatureBatchError("feature batch must contain at least one candle")

        seen: set[FeatureSeries] = set()
        for event in batch.events:
            if event.series in seen:
                raise FeatureBatchError("feature batch contains duplicate series")
            seen.add(event.series)
            if (
                event.candle.symbol != event.series.symbol
                or event.candle.timeframe is not event.series.timeframe
            ):
                raise FeatureSeriesError("candle identity does not match its feature series")
            if event.candle.close_time != observed_at:
                raise FeatureBatchError("every candle must close at observed_at")
            self._validate_progression(event)

        pending: dict[FeatureSeries, _SeriesState] = {}
        snapshots: list[FeatureSnapshot] = []
        for event in batch.events:
            state = self._states.get(event.series, _SeriesState())
            next_state, snapshot = state.advance(event.series, event.candle, observed_at)
            pending[event.series] = next_state
            snapshots.append(snapshot)
        self._states.update(pending)
        return FeatureBatch(observed_at=observed_at, snapshots=tuple(snapshots))

    def _validate_progression(self, event: ObservedCandle) -> None:
        previous = self._states.get(event.series, _SeriesState()).last_candle
        if previous is None:
            return
        if event.candle.open_time == previous.open_time:
            raise FeatureSeriesError("duplicate candle open_time")
        if event.candle.open_time < previous.open_time:
            raise FeatureSeriesError("candle open_time moved backward")
        if event.candle.open_time != previous.close_time:
            raise FeatureSeriesError("candle gap is forbidden")
