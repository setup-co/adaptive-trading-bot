import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.replay import (
    HistoricalReplaySource,
    ReplayBatch,
    ReplayClockError,
    ReplayConfigurationError,
    ReplayEngine,
    ReplayEvent,
    ReplayIntegrityError,
    ReplaySeries,
)
from adaptive_trading_bot.storage import (
    IntegrityIssueKind,
    IntegrityReport,
    ParquetHistoricalCandleStore,
    SourceId,
)

BTC_USDT = TradingSymbol("BTC", "USDT")
ETH_USDT = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("archive-a")
SOURCE_B = SourceId("archive-b")
START = datetime(2026, 9, 1, tzinfo=UTC)


def make_candle(
    open_time: datetime,
    *,
    symbol: TradingSymbol = BTC_USDT,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    price: str = "100",
) -> Candle:
    value = Decimal(price)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=value,
        high=value + Decimal("10"),
        low=value - Decimal("10"),
        close=value + Decimal("1"),
        volume=Decimal("12.5"),
    )


def candles_for(
    start: datetime,
    count: int,
    *,
    symbol: TradingSymbol = BTC_USDT,
    timeframe: Timeframe = Timeframe.MINUTES_15,
) -> tuple[Candle, ...]:
    return tuple(
        make_candle(
            start + index * timeframe.duration,
            symbol=symbol,
            timeframe=timeframe,
            price=str(100 + index),
        )
        for index in range(count)
    )


def spec(
    *,
    source_id: SourceId = SOURCE_A,
    symbol: TradingSymbol = BTC_USDT,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    start: datetime = START,
    count: int = 1,
) -> ReplaySeries:
    return ReplaySeries(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=start + count * timeframe.duration,
    )


def test_one_series_is_invisible_at_open_and_replays_only_at_close(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first, second = candles_for(START, 2)
    store.merge(SOURCE_A, (first, second))
    clock = FrozenClock(datetime(2000, 1, 1, tzinfo=UTC))
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(count=2),),
        clock=clock,
    )

    assert clock.now() == START
    assert session.exhausted is False
    first_batch = session.step()
    assert first_batch is not None
    assert first_batch.observed_at == first.close_time
    assert first_batch.candles == (first,)
    assert clock.now() == first_batch.observed_at
    assert all(candle.close_time <= session.clock.now() for candle in first_batch.candles)

    second_batch = session.step()
    assert second_batch is not None
    assert second_batch.observed_at == second.close_time
    assert second_batch.observed_at == spec(count=2).end
    assert session.exhausted is True
    assert session.step() is None
    assert session.step() is None


def test_equal_close_15m_and_1h_candles_form_one_atomic_batch(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    fifteen = candles_for(START, 4)
    hourly = candles_for(START, 1, timeframe=Timeframe.HOUR_1)
    store.merge(SOURCE_A, (*fifteen, *hourly))
    clock = FrozenClock(START)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(count=4), spec(timeframe=Timeframe.HOUR_1)),
        clock=clock,
    )

    batches = tuple(session)

    assert [batch.observed_at for batch in batches] == [
        START + timedelta(minutes=15),
        START + timedelta(minutes=30),
        START + timedelta(minutes=45),
        START + timedelta(hours=1),
    ]
    assert batches[-1].candles == (fifteen[-1], hourly[0])
    assert clock.now() == START + timedelta(hours=1)


def test_15m_1h_4h_timeline_is_deterministic_over_several_hours(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    fifteen = candles_for(START, 16)
    hourly = candles_for(START, 4, timeframe=Timeframe.HOUR_1)
    four_hour = candles_for(START, 1, timeframe=Timeframe.HOURS_4)
    store.merge(SOURCE_A, (*fifteen, *hourly, *four_hour))
    clock = FrozenClock(START)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (
            spec(count=16),
            spec(timeframe=Timeframe.HOUR_1, count=4),
            spec(timeframe=Timeframe.HOURS_4),
        ),
        clock=clock,
    )

    batches = tuple(session)

    assert len(batches) == 16
    assert all(
        previous.observed_at < current.observed_at for previous, current in pairwise(batches)
    )
    assert [event.candle.timeframe for event in batches[3].events] == [
        Timeframe.MINUTES_15,
        Timeframe.HOUR_1,
    ]
    assert [event.candle.timeframe for event in batches[-1].events] == [
        Timeframe.MINUTES_15,
        Timeframe.HOUR_1,
        Timeframe.HOURS_4,
    ]


def test_btc_and_eth_same_close_are_grouped_in_canonical_order(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    btc = make_candle(START)
    eth = make_candle(START, symbol=ETH_USDT)
    store.merge(SOURCE_A, (eth, btc))
    clock = FrozenClock(START)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(symbol=ETH_USDT), spec(symbol=BTC_USDT)),
        clock=clock,
    )

    batch = session.step()

    assert batch is not None
    assert batch.candles == (btc, eth)
    assert batch.observed_at == btc.close_time == eth.close_time


def test_different_sources_remain_distinct_and_sort_by_source(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candle_a = make_candle(START, price="100")
    candle_b = make_candle(START, price="200")
    store.merge(SOURCE_A, (candle_a,))
    store.merge(SOURCE_B, (candle_b,))
    clock = FrozenClock(START)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(source_id=SOURCE_B), spec(source_id=SOURCE_A)),
        clock=clock,
    )

    batch = session.step()

    assert batch is not None
    assert [event.source_id for event in batch.events] == [SOURCE_A, SOURCE_B]
    assert batch.candles == (candle_a, candle_b)


def test_repeated_preparation_produces_identical_batches(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    requested = (spec(count=4),)
    store.merge(SOURCE_A, candles_for(START, 4))

    first = ReplayEngine(HistoricalReplaySource(store)).prepare(
        requested,
        clock=FrozenClock(START),
    )
    second = ReplayEngine(HistoricalReplaySource(store)).prepare(
        requested,
        clock=FrozenClock(START),
    )

    assert tuple(first) == tuple(second)


def test_duplicate_series_specification_is_rejected(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    requested = spec()

    with pytest.raises(ReplayConfigurationError, match="duplicate"):
        ReplayEngine(HistoricalReplaySource(store)).prepare(
            (requested, requested),
            clock=FrozenClock(START),
        )


def test_empty_series_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReplayConfigurationError, match="at least one"):
        ReplayEngine(HistoricalReplaySource(ParquetHistoricalCandleStore(tmp_path))).prepare(
            (),
            clock=FrozenClock(START),
        )


class RecordingSource:
    def __init__(self, delegate: HistoricalReplaySource) -> None:
        self.delegate = delegate
        self.scan_calls: list[ReplaySeries] = []
        self.read_calls: list[ReplaySeries] = []

    def scan_integrity(self, series: ReplaySeries) -> IntegrityReport:
        self.scan_calls.append(series)
        return self.delegate.scan_integrity(series)

    def read(self, series: ReplaySeries) -> tuple[Candle, ...]:
        self.read_calls.append(series)
        return self.delegate.read(series)


def test_one_dirty_series_gates_all_series_before_any_read_or_emission(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    clean = spec(symbol=BTC_USDT, count=2)
    dirty = spec(symbol=ETH_USDT, count=2)
    store.merge(SOURCE_A, candles_for(START, 2, symbol=BTC_USDT))
    store.merge(SOURCE_A, (make_candle(START, symbol=ETH_USDT),))
    source = RecordingSource(HistoricalReplaySource(store))

    with pytest.raises(ReplayIntegrityError) as captured:
        ReplayEngine(source).prepare((clean, dirty), clock=FrozenClock(START))

    assert source.scan_calls == [clean, dirty]
    assert source.read_calls == []
    assert [failure.series for failure in captured.value.failures] == [dirty]
    assert captured.value.failures[0].report.issues[0].kind is IntegrityIssueKind.GAP


def corrupt_partition(
    path: Path,
    transform: Callable[[pa.Table], pa.Table],
) -> None:
    table = pq.ParquetFile(path).read()
    pq.write_table(transform(table), path)


def test_schema_damaged_series_fails_integrity_gate(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE_A, (make_candle(START),))
    path = next(tmp_path.rglob("*.parquet"))
    corrupt_partition(path, lambda table: table.replace_schema_metadata(None))

    with pytest.raises(ReplayIntegrityError) as captured:
        ReplayEngine(HistoricalReplaySource(store)).prepare(
            (spec(),),
            clock=FrozenClock(START),
        )

    assert any(
        issue.kind is IntegrityIssueKind.SCHEMA_MISMATCH
        for issue in captured.value.failures[0].report.issues
    )


def test_malformed_series_fails_integrity_gate(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE_A, (make_candle(START),))
    path = next(tmp_path.rglob("*.parquet"))

    def invalidate_high(table: pa.Table) -> pa.Table:
        rows = table.to_pylist()
        rows[0]["high"] = Decimal("1")
        return pa.Table.from_pylist(rows, schema=table.schema)

    corrupt_partition(path, invalidate_high)

    with pytest.raises(ReplayIntegrityError) as captured:
        ReplayEngine(HistoricalReplaySource(store)).prepare(
            (spec(),),
            clock=FrozenClock(START),
        )

    assert any(
        issue.kind is IntegrityIssueKind.MALFORMED_ROW
        for issue in captured.value.failures[0].report.issues
    )


def test_month_partition_and_utc_day_boundaries_replay_normally(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    start = datetime(2026, 9, 30, 23, 45, tzinfo=UTC)
    values = candles_for(start, 2)
    store.merge(SOURCE_A, values)
    clock = FrozenClock(start)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(start=start, count=2),),
        clock=clock,
    )

    batches = tuple(session)

    assert [batch.observed_at for batch in batches] == [
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 0, 15, tzinfo=UTC),
    ]
    assert len(tuple(tmp_path.rglob("*.parquet"))) == 2


def test_ordinary_utc_day_boundary_replays_normally(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    start = datetime(2026, 9, 2, 23, 45, tzinfo=UTC)
    store.merge(SOURCE_A, candles_for(start, 2))
    clock = FrozenClock(start)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(start=start, count=2),),
        clock=clock,
    )

    assert [batch.observed_at for batch in session] == [
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 0, 15, tzinfo=UTC),
    ]


def test_core_never_uses_wall_clock_sleep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE_A, candles_for(START, 2))

    def forbidden_sleep(seconds: float) -> None:
        raise AssertionError(f"unexpected sleep({seconds})")

    monkeypatch.setattr(time, "sleep", forbidden_sleep)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (spec(count=2),),
        clock=FrozenClock(START),
    )

    assert len(tuple(session)) == 2


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (datetime(2026, 9, 1), datetime(2026, 9, 1, 0, 15), "timezone-aware UTC"),
        (
            datetime(2026, 9, 1, tzinfo=timezone(timedelta(hours=1))),
            datetime(2026, 9, 1, 0, 15, tzinfo=timezone(timedelta(hours=1))),
            "must use UTC",
        ),
        (START, START, "before end"),
        (START + timedelta(minutes=1), START + timedelta(minutes=15), "start must align"),
        (START, START + timedelta(minutes=16), "end must align"),
    ],
)
def test_replay_series_rejects_invalid_ranges(
    start: datetime,
    end: datetime,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ReplaySeries(SOURCE_A, BTC_USDT, Timeframe.MINUTES_15, start, end)


def test_batch_rejects_empty_or_mixed_observation_times() -> None:
    first, second = candles_for(START, 2)

    with pytest.raises(ValueError, match="at least one"):
        ReplayBatch(first.close_time, ())
    with pytest.raises(ValueError, match="close at observed_at"):
        ReplayBatch(first.close_time, (ReplayEvent(SOURCE_A, first), ReplayEvent(SOURCE_A, second)))


def test_external_forward_clock_mutation_fails_instead_of_moving_backward(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(SOURCE_A, (make_candle(START),))
    clock = FrozenClock(START)
    session = ReplayEngine(HistoricalReplaySource(store)).prepare((spec(),), clock=clock)
    clock.set(START + timedelta(hours=1))

    with pytest.raises(ReplayClockError, match="strictly increasing"):
        session.step()
