from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, localcontext, setcontext

import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureBatchError,
    FeatureEngine,
    FeatureSeries,
    FeatureSeriesError,
    FeatureSnapshot,
    ObservedCandle,
    ObservedCandleBatch,
)
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 9, 1, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("source-a")
SOURCE_B = SourceId("source-b")


def candle(
    index: int,
    *,
    price: Decimal | None = None,
    volume: Decimal = Decimal(1),
    symbol: TradingSymbol = BTC,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    start: datetime = START,
) -> Candle:
    close = price if price is not None else Decimal(100 + index)
    open_time = start + index * timeframe.duration
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=close,
        high=close + Decimal(1),
        low=close - Decimal(1),
        close=close,
        volume=volume,
    )


def observed(
    item: Candle,
    *,
    source_id: SourceId = SOURCE_A,
    series_symbol: TradingSymbol | None = None,
    series_timeframe: Timeframe | None = None,
) -> ObservedCandle:
    return ObservedCandle(
        FeatureSeries(
            source_id,
            series_symbol or item.symbol,
            series_timeframe or item.timeframe,
        ),
        item,
    )


def apply(
    engine: FeatureEngine, item: Candle, *, source_id: SourceId = SOURCE_A
) -> FeatureSnapshot:
    event = observed(item, source_id=source_id)
    return engine.process(ObservedCandleBatch(item.close_time, (event,))).snapshots[0]


def test_warmups_and_formula_boundaries_are_causal() -> None:
    engine = FeatureEngine()
    snapshots = [apply(engine, candle(index)) for index in range(50)]

    assert snapshots[0].return_1 is None
    assert snapshots[1].return_1 == Decimal("0.01")
    assert snapshots[3].momentum_4 is None
    assert snapshots[4].momentum_4 == Decimal("0.04")
    assert snapshots[15].momentum_16 is None
    assert snapshots[16].momentum_16 == Decimal("0.16")
    assert snapshots[12].atr_14 is None
    assert snapshots[13].atr_14 == Decimal(2)
    assert snapshots[14].atr_14 == Decimal(2)
    assert snapshots[13].rsi_14 is None
    assert snapshots[14].rsi_14 == Decimal(100)
    assert snapshots[18].ema_20 is None
    assert snapshots[19].ema_20 == Decimal("109.5")
    assert snapshots[20].ema_20 == Decimal("110.5")
    assert snapshots[48].ema_50 is None
    assert snapshots[49].ema_50 == Decimal("124.5")
    assert snapshots[48].is_fully_warmed is False
    assert snapshots[49].is_fully_warmed is True
    assert snapshots[18].volume_zscore_20 is None
    assert snapshots[19].volume_zscore_20 == Decimal(0)
    assert snapshots[19].realized_volatility_20 is None
    assert snapshots[20].realized_volatility_20 is not None

    first = snapshots[0]
    assert (
        first.ema_20,
        first.ema_50,
        first.atr_14,
        first.rsi_14,
        first.return_1,
        first.momentum_4,
        first.momentum_16,
        first.volume_zscore_20,
        first.realized_volatility_20,
    ) == (None,) * 9


@pytest.mark.parametrize(
    ("prices", "expected"),
    [
        ((Decimal(100),) * 15, Decimal(50)),
        (tuple(Decimal(115 - index) for index in range(15)), Decimal(0)),
    ],
)
def test_rsi_zero_gain_and_loss_edges(prices: tuple[Decimal, ...], expected: Decimal) -> None:
    engine = FeatureEngine()
    snapshots = [apply(engine, candle(index, price=price)) for index, price in enumerate(prices)]

    assert snapshots[-1].rsi_14 == expected


def test_atr_first_wilder_update_uses_prior_atr_and_current_true_range() -> None:
    engine = FeatureEngine()
    snapshots = [apply(engine, candle(index)) for index in range(14)]
    base = Decimal(114)
    open_time = START + 14 * Timeframe.MINUTES_15.duration
    wide = Candle(
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=base,
        high=base + Decimal(8),
        low=base - Decimal(8),
        close=base,
        volume=Decimal(1),
    )

    updated = apply(engine, wide)

    assert snapshots[-1].atr_14 == Decimal(2)
    assert updated.atr_14 == Decimal(3)


def test_volume_zscore_and_realized_volatility_use_population_std() -> None:
    engine = FeatureEngine()
    snapshots = [apply(engine, candle(index, volume=Decimal(index + 1))) for index in range(21)]

    with localcontext(Context(prec=34)):
        volumes = tuple(Decimal(value) for value in range(1, 21))
        mean = sum(volumes) / Decimal(20)
        std = (sum((value - mean) ** 2 for value in volumes) / Decimal(20)).sqrt()
        expected_zscore = (Decimal(20) - mean) / std
        returns = tuple(
            Decimal(100 + index) / Decimal(99 + index) - Decimal(1) for index in range(1, 21)
        )
        returns_mean = sum(returns) / Decimal(20)
        expected_volatility = (
            sum((value - returns_mean) ** 2 for value in returns) / Decimal(20)
        ).sqrt()

    assert snapshots[19].volume_zscore_20 == expected_zscore
    assert snapshots[20].realized_volatility_20 == expected_volatility

    flat_engine = FeatureEngine()
    flat = [apply(flat_engine, candle(index, price=Decimal(100))) for index in range(21)]
    assert flat[-1].volume_zscore_20 == Decimal(0)
    assert flat[-1].realized_volatility_20 == Decimal(0)


def test_session_vwap_uses_open_date_resets_and_handles_zero_volume() -> None:
    start = datetime(2026, 9, 1, 23, 15, tzinfo=UTC)
    engine = FeatureEngine()
    first = apply(engine, candle(0, price=Decimal(100), volume=Decimal(2), start=start))
    weighted = apply(engine, candle(1, price=Decimal(200), volume=Decimal(1), start=start))
    zero = apply(engine, candle(2, price=Decimal(300), volume=Decimal(0), start=start))
    reset_zero = apply(engine, candle(3, price=Decimal(400), volume=Decimal(0), start=start))
    next_value = apply(engine, candle(4, price=Decimal(500), volume=Decimal(3), start=start))
    with localcontext(Context(prec=34)):
        expected_weighted = Decimal(400) / Decimal(3)

    assert first.vwap_utc_day == Decimal(100)
    assert weighted.vwap_utc_day == expected_weighted
    assert zero.vwap_utc_day == weighted.vwap_utc_day
    assert reset_zero.vwap_utc_day is None
    assert next_value.vwap_utc_day == Decimal(500)


def test_equal_time_output_is_canonical_and_source_state_is_independent() -> None:
    engine = FeatureEngine()
    same = candle(0)
    batch = ObservedCandleBatch(
        same.close_time,
        (observed(same, source_id=SOURCE_B), observed(same, source_id=SOURCE_A)),
    )

    output = engine.process(batch)

    assert tuple(snapshot.source_id for snapshot in output.snapshots) == (SOURCE_A, SOURCE_B)
    assert tuple(snapshot.bars_seen for snapshot in output.snapshots) == (1, 1)
    assert apply(engine, candle(1), source_id=SOURCE_A).bars_seen == 2
    assert apply(engine, candle(1), source_id=SOURCE_B).bars_seen == 2


def test_symbol_and_timeframe_states_are_independent() -> None:
    engine = FeatureEngine()
    fifteen = candle(3)
    hourly = candle(0, timeframe=Timeframe.HOUR_1)
    four_hour = candle(0, timeframe=Timeframe.HOURS_4)
    eth = candle(3, symbol=ETH)

    first = engine.process(
        ObservedCandleBatch(
            fifteen.close_time,
            (observed(fifteen), observed(hourly), observed(eth)),
        )
    )
    second = engine.process(ObservedCandleBatch(four_hour.close_time, (observed(four_hour),)))

    assert tuple(snapshot.bars_seen for snapshot in first.snapshots) == (1, 1, 1)
    assert second.snapshots[0].bars_seen == 1


@pytest.mark.parametrize("kind", ["duplicate", "backward", "gap"])
def test_chronology_failures_leave_state_unchanged(kind: str) -> None:
    engine = FeatureEngine()
    first = candle(2)
    apply(engine, first)
    invalid = {
        "duplicate": first,
        "backward": candle(1),
        "gap": candle(4),
    }[kind]

    with pytest.raises(FeatureSeriesError):
        apply(engine, invalid)

    assert apply(engine, candle(3)).bars_seen == 2


def test_invalid_multi_series_batch_has_no_partial_mutation() -> None:
    engine = FeatureEngine()
    first_btc = candle(0)
    apply(engine, first_btc)
    valid_btc = candle(1)
    mismatched = ObservedCandle(
        FeatureSeries(SOURCE_B, ETH, Timeframe.MINUTES_15),
        candle(1),
    )

    with pytest.raises(FeatureSeriesError, match="identity"):
        engine.process(
            ObservedCandleBatch(
                valid_btc.close_time,
                (observed(valid_btc), mismatched),
            )
        )

    assert apply(engine, valid_btc).bars_seen == 2


def test_batch_rejects_close_mismatch_duplicate_series_and_identity_mismatch() -> None:
    item = candle(0)
    event = observed(item)
    with pytest.raises(FeatureBatchError, match="close"):
        FeatureEngine().process(
            ObservedCandleBatch(item.close_time + timedelta(minutes=15), (event,))
        )
    with pytest.raises(FeatureBatchError, match="duplicate"):
        FeatureEngine().process(ObservedCandleBatch(item.close_time, (event, event)))
    with pytest.raises(FeatureSeriesError, match="identity"):
        FeatureEngine().process(
            ObservedCandleBatch(
                item.close_time,
                (observed(item, series_symbol=ETH),),
            )
        )


def test_results_do_not_depend_on_ambient_decimal_context() -> None:
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        engine = FeatureEngine()
        low_precision = [apply(engine, candle(index)) for index in range(21)]
        setcontext(Context(prec=28))
        engine = FeatureEngine()
        normal_precision = [apply(engine, candle(index)) for index in range(21)]
    finally:
        setcontext(original)

    assert low_precision == normal_precision
