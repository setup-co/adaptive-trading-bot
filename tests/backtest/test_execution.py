from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, getcontext, setcontext
from pathlib import Path

import pytest

from adaptive_trading_bot.backtest import (
    BacktestExecutionResult,
    BacktestExecutionSimulator,
    ExecutionConfigurationError,
    ExecutionCostConfiguration,
    ExecutionIntegrityError,
    ExecutionSeries,
    HistoricalExecutionSource,
    InvalidFillPriceError,
    MarketFill,
    MarketOrderRequest,
    OrderSide,
    UnfilledReason,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.entry_chronology import (
    CAUSAL_ENTRY_CHRONOLOGY_V2,
    entry_source_start,
    validate_entry_fill_chronology,
)
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


def candle(
    index: int,
    *,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    open_price: Decimal | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    close: Decimal | None = None,
) -> Candle:
    opening = open_price if open_price is not None else Decimal(100 + index)
    open_time = START + index * timeframe.duration
    return Candle(
        symbol=BTC,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=opening,
        high=high if high is not None else opening + Decimal(10),
        low=low if low is not None else opening - Decimal(10),
        close=close if close is not None else opening + Decimal(1),
        volume=Decimal(5),
    )


def order(
    order_id: str = "order-1",
    *,
    created_at: datetime = START,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal(2),
    symbol: TradingSymbol = BTC,
) -> MarketOrderRequest:
    return MarketOrderRequest(order_id, created_at, symbol, side, quantity)


def simulate(
    path: Path,
    candles: tuple[Candle, ...],
    orders: tuple[MarketOrderRequest, ...],
    *,
    costs: ExecutionCostConfiguration | None = None,
    source_id: SourceId = SOURCE,
) -> BacktestExecutionResult:
    store = ParquetHistoricalCandleStore(path)
    store.merge(source_id, candles)
    series = ExecutionSeries(source_id, BTC, candles[0].timeframe)
    return BacktestExecutionSimulator(HistoricalExecutionSource(store)).run(
        series,
        costs or ExecutionCostConfiguration(),
        orders,
        start=candles[0].open_time,
        end=candles[-1].close_time,
    )


def test_buy_fill_spread_slippage_fee_and_provenance(tmp_path: Path) -> None:
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal(10),
        slippage_bps=Decimal(20),
        fee_bps=Decimal(20),
    )
    result = simulate(tmp_path, (candle(0),), (order(),), costs=costs)

    fill = result.fills[0]
    assert fill.reference_open_price == Decimal(100)
    assert fill.fill_price == Decimal("100.25")
    assert fill.fee_quote == Decimal("0.401")
    assert fill.execution_candle_open_time == fill.fill_at == START
    assert fill.fill_at >= fill.eligible_at
    assert fill.source_id == SOURCE
    assert fill.execution_timeframe is Timeframe.MINUTES_15
    assert isinstance(fill.fill_price, Decimal)
    assert isinstance(fill.fee_quote, Decimal)
    assert (result.requested_order_count, result.filled_count, result.unfilled_count) == (1, 1, 0)


def test_sell_fill_uses_negative_cost_direction() -> None:
    item = candle(0)
    costs = ExecutionCostConfiguration(spread_bps=Decimal(10), slippage_bps=Decimal(20))
    fill = simulate_pathless(item, order(side=OrderSide.SELL), costs)

    assert fill.fill_price == Decimal("99.75")
    assert fill.fee_quote == Decimal(0)


def simulate_pathless(
    item: Candle,
    request: MarketOrderRequest,
    costs: ExecutionCostConfiguration,
) -> MarketFill:
    class Source:
        def load(
            self,
            series: ExecutionSeries,
            *,
            start: datetime,
            end: datetime,
        ) -> tuple[Candle, ...]:
            return (item,)

    simulator = BacktestExecutionSimulator(Source())
    result = simulator.run(
        ExecutionSeries(SOURCE, BTC, item.timeframe),
        costs,
        (request,),
        start=item.open_time,
        end=item.close_time,
    )
    return result.fills[0]


@pytest.mark.parametrize(
    "timeframe",
    [Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4],
)
def test_zero_cost_returns_raw_open_without_hard_coded_timeframe(timeframe: Timeframe) -> None:
    item = candle(0, timeframe=timeframe)
    fill = simulate_pathless(item, order(), ExecutionCostConfiguration())

    assert fill.fill_price == item.open
    assert fill.execution_timeframe is timeframe


@pytest.mark.parametrize(
    ("created_at", "latency", "expected_index"),
    [
        (START, timedelta(0), 0),
        (START + timedelta(seconds=1), timedelta(0), 1),
        (START, timedelta(minutes=20), 2),
    ],
)
def test_first_open_at_or_after_eligibility_is_selected(
    tmp_path: Path,
    created_at: datetime,
    latency: timedelta,
    expected_index: int,
) -> None:
    result = simulate(
        tmp_path,
        (candle(0), candle(1), candle(2)),
        (order(created_at=created_at),),
        costs=ExecutionCostConfiguration(latency=latency),
    )

    assert result.fills[0].fill_at == candle(expected_index).open_time
    assert result.fills[0].reference_open_price == candle(expected_index).open


def test_versioned_entry_chronology_preserves_bounds_and_actual_times(tmp_path: Path) -> None:
    assert CAUSAL_ENTRY_CHRONOLOGY_V2 == "CAUSAL_ENTRY_CHRONOLOGY_V2"
    series = ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15)
    candles = (candle(0), candle(1), candle(2), candle(3), candle(4))
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE, candles)
    simulator = BacktestExecutionSimulator(HistoricalExecutionSource(store))
    for created_at, latency, expected_index in (
        (START + timedelta(minutes=15), timedelta(), 1),
        (START + timedelta(minutes=15), timedelta(seconds=1), 2),
        (START + timedelta(minutes=15, seconds=1), timedelta(), 2),
        (START + timedelta(minutes=15), timedelta(minutes=31), 4),
    ):
        start = entry_source_start(
            decision_at=START + timedelta(minutes=15),
            authorized_at=created_at,
            requested_start=START + timedelta(minutes=15),
            end=candles[-1].close_time,
        )
        result = simulator.run(
            series,
            ExecutionCostConfiguration(latency=latency),
            (order(created_at=created_at),),
            start=start,
            end=candles[-1].close_time,
        )
        fill = result.fills[0]
        assert fill.fill_at == candles[expected_index].open_time
        validate_entry_fill_chronology(
            decision_at=START + timedelta(minutes=15),
            authorized_at=created_at,
            latency=latency,
            eligible_at=fill.eligible_at,
            fill_at=fill.fill_at,
        )
    boundary_only = simulator.run(
        series,
        ExecutionCostConfiguration(),
        (order(created_at=START + timedelta(minutes=15)),),
        start=START + timedelta(minutes=15),
        end=START + timedelta(minutes=30),
    )
    delayed_only = simulator.run(
        series,
        ExecutionCostConfiguration(latency=timedelta(seconds=1)),
        (order(created_at=START + timedelta(minutes=15)),),
        start=START + timedelta(minutes=15),
        end=START + timedelta(minutes=30),
    )
    assert boundary_only.fills[0].fill_at == START + timedelta(minutes=15)
    assert delayed_only.filled_count == 0 and delayed_only.unfilled_count == 1
    with pytest.raises(ValueError, match="chronology"):
        entry_source_start(
            decision_at=START + timedelta(minutes=15),
            authorized_at=START,
            requested_start=START,
            end=START + timedelta(minutes=15),
        )
    with pytest.raises(ValueError, match="chronology"):
        validate_entry_fill_chronology(
            decision_at=START,
            authorized_at=START,
            latency=timedelta(seconds=1),
            eligible_at=START + timedelta(seconds=1),
            fill_at=START,
        )


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
@pytest.mark.parametrize(
    ("latency", "direct_index", "bounded_index"),
    [(timedelta(), 1, 2), (timedelta(seconds=1), 2, 2)],
)
def test_source_lower_bound_and_latency_are_separate_entry_constraints(
    tmp_path: Path, side: OrderSide, latency: timedelta, direct_index: int, bounded_index: int
) -> None:
    # Synthetic expectation: at the 12:15 decision boundary, the simulator can use
    # the 12:15 open at zero latency; a source beginning one microsecond later cannot.
    candles = (candle(0), candle(1), candle(2))
    decision_at = candles[1].open_time
    costs = ExecutionCostConfiguration(latency=latency)
    series = ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15)
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE, candles)
    request = order(created_at=decision_at, side=side)
    simulator = BacktestExecutionSimulator(HistoricalExecutionSource(store))
    direct = simulator.run(
        series, costs, (request,), start=decision_at, end=candles[-1].close_time
    ).fills[0]
    bounded = simulator.run(
        series,
        costs,
        (request,),
        start=decision_at + timedelta(microseconds=1),
        end=candles[-1].close_time,
    ).fills[0]

    assert direct.created_at == bounded.created_at == decision_at
    assert direct.eligible_at == bounded.eligible_at == decision_at + latency
    assert direct.fill_at == candles[direct_index].open_time
    assert bounded.fill_at == candles[bounded_index].open_time
    assert direct.reference_open_price == candles[direct_index].open
    assert bounded.reference_open_price == candles[bounded_index].open


def test_order_created_at_close_uses_next_open_not_closed_candle_ohlc(tmp_path: Path) -> None:
    first = candle(0, high=Decimal(140), low=Decimal(70), close=Decimal(130))
    next_candle = candle(1, open_price=Decimal(111))
    request = order(created_at=first.close_time)
    baseline = simulate(tmp_path / "baseline", (first, next_candle), (request,)).fills[0]
    changed_first = candle(0, high=Decimal(190), low=Decimal(10), close=Decimal(180))
    changed = simulate(tmp_path / "changed", (changed_first, next_candle), (request,)).fills[0]

    assert baseline == changed
    assert baseline.fill_at == next_candle.open_time
    assert baseline.reference_open_price == Decimal(111)


def test_candles_after_first_eligible_open_cannot_change_fill(tmp_path: Path) -> None:
    request = order(created_at=START + timedelta(minutes=1))
    first = simulate(
        tmp_path / "first",
        (candle(0), candle(1), candle(2, open_price=Decimal(120))),
        (request,),
    ).fills[0]
    changed_future = simulate(
        tmp_path / "changed",
        (candle(0), candle(1), candle(2, open_price=Decimal(900))),
        (request,),
    ).fills[0]

    assert first == changed_future
    assert first.reference_open_price == candle(1).open


def test_same_time_orders_are_canonical_and_share_execution_open(tmp_path: Path) -> None:
    created_at = START + timedelta(minutes=1)
    result = simulate(
        tmp_path,
        (candle(0), candle(1)),
        (
            order("b", created_at=created_at, side=OrderSide.SELL),
            order("a", created_at=created_at),
        ),
    )

    assert tuple(fill.order_id for fill in result.fills) == ("a", "b")
    assert len({fill.fill_at for fill in result.fills}) == 1
    assert result.fills[0].fill_at == candle(1).open_time


def test_bounded_dataset_returns_explicit_unfilled_order(tmp_path: Path) -> None:
    request = order(created_at=candle(1).close_time)
    result = simulate(tmp_path, (candle(0), candle(1)), (request,))

    assert not result.fills
    assert result.unfilled_count == 1
    assert result.unfilled_orders[0].order == request
    assert result.unfilled_orders[0].reason is UnfilledReason.NO_ELIGIBLE_EXECUTION_CANDLE


def test_duplicate_order_ids_fail_before_simulation(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    item = candle(0)
    store.merge(SOURCE, (item,))
    simulator = BacktestExecutionSimulator(HistoricalExecutionSource(store))

    with pytest.raises(ExecutionConfigurationError, match="duplicate"):
        simulator.run(
            ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
            ExecutionCostConfiguration(),
            (order("same"), order(" same ")),
            start=item.open_time,
            end=item.close_time,
        )


@pytest.mark.parametrize("quantity", [Decimal(0), Decimal(-1), Decimal("NaN")])
def test_order_rejects_invalid_quantity(quantity: Decimal) -> None:
    with pytest.raises(ValueError, match="quantity"):
        order(quantity=quantity)


@pytest.mark.parametrize(
    "created_at",
    [datetime(2026, 9, 1, 12, 0), datetime(2026, 9, 1, 12, 0, tzinfo=timezone(timedelta(hours=1)))],
)
def test_order_rejects_non_utc_timestamp(created_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        order(created_at=created_at)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("spread_bps", Decimal(-1), ValueError),
        ("slippage_bps", Decimal("NaN"), ValueError),
        ("fee_bps", 1, TypeError),
        ("latency", timedelta(seconds=-1), ValueError),
    ],
)
def test_cost_configuration_rejects_invalid_values(
    field: str, value: object, error: type[Exception]
) -> None:
    arguments: dict[str, object] = {field: value}
    with pytest.raises(error):
        ExecutionCostConfiguration(**arguments)  # type: ignore[arg-type]


def test_dirty_gapped_execution_range_fails_before_any_result(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE, (candle(0), candle(2)))
    simulator = BacktestExecutionSimulator(HistoricalExecutionSource(store))

    with pytest.raises(ExecutionIntegrityError) as raised:
        simulator.run(
            ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
            ExecutionCostConfiguration(),
            (order(),),
            start=candle(0).open_time,
            end=candle(2).close_time,
        )

    assert raised.value.report.is_clean is False


def test_order_symbol_mismatch_and_invalid_bounds_fail_closed(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    simulator = BacktestExecutionSimulator(HistoricalExecutionSource(store))
    series = ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15)
    with pytest.raises(ExecutionConfigurationError, match="symbol"):
        simulator.run(
            series,
            ExecutionCostConfiguration(),
            (order(symbol=ETH),),
            start=START,
            end=START + timedelta(minutes=15),
        )
    with pytest.raises(ExecutionConfigurationError, match="before"):
        simulator.run(
            series,
            ExecutionCostConfiguration(),
            (),
            start=START,
            end=START,
        )


def test_non_positive_adjusted_sell_price_fails_closed() -> None:
    with pytest.raises(InvalidFillPriceError):
        simulate_pathless(
            candle(0),
            order(side=OrderSide.SELL),
            ExecutionCostConfiguration(spread_bps=Decimal(20_000)),
        )


def test_repeated_results_ignore_ambient_decimal_context(tmp_path: Path) -> None:
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal("3.333333333333333333"),
        slippage_bps=Decimal("1.111111111111111111"),
        fee_bps=Decimal("2.222222222222222222"),
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = simulate(tmp_path / "low", (candle(0),), (order(),), costs=costs)
        setcontext(Context(prec=28))
        normal_precision = simulate(tmp_path / "normal", (candle(0),), (order(),), costs=costs)
    finally:
        setcontext(original)

    assert low_precision == normal_precision
