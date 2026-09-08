from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.storage import (
    CandleConflictError,
    DatasetIntegrityError,
    IntegrityIssueKind,
    ParquetHistoricalCandleStore,
    SourceId,
    StorageWriteError,
    UnrepresentableDecimalError,
)
from adaptive_trading_bot.storage.historical_candles import candle_schema

BTC_USDT = TradingSymbol("BTC", "USDT")
ETH_USDT = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive-a")


def make_candle(
    open_time: datetime,
    *,
    symbol: TradingSymbol = BTC_USDT,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    price: Decimal = Decimal("100.123456789012345678"),
    volume: Decimal = Decimal("12.000000000000000001"),
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=price,
        high=price + Decimal("10"),
        low=price - Decimal("10"),
        close=price + Decimal("1"),
        volume=volume,
    )


def partition_path(
    root: Path,
    source_id: SourceId,
    candle: Candle,
) -> Path:
    return (
        root
        / f"source={source_id.value}"
        / f"base={candle.symbol.base}"
        / f"quote={candle.symbol.quote}"
        / f"timeframe={candle.timeframe.value}"
        / f"year={candle.open_time.year:04d}"
        / f"month={candle.open_time.month:02d}"
        / "candles.parquet"
    )


def arrow_row(candle: Candle) -> dict[str, object]:
    return {
        "base": candle.symbol.base,
        "quote": candle.symbol.quote,
        "timeframe": candle.timeframe.value,
        "open_time": candle.open_time,
        "close_time": candle.close_time,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def write_raw_partition(
    root: Path,
    source_id: SourceId,
    candles: list[Candle],
    *,
    schema: pa.Schema | None = None,
    rows: list[dict[str, object]] | None = None,
) -> Path:
    first = candles[0]
    path = partition_path(root, source_id, first)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        rows or [arrow_row(candle) for candle in candles],
        schema=schema or candle_schema(source_id, first.symbol, first.timeframe),
    )
    pq.write_table(table, path)
    return path


def test_arrow_schema_is_explicit_lossless_and_versioned() -> None:
    schema = candle_schema(SOURCE, BTC_USDT, Timeframe.MINUTES_15)

    assert schema.field("open_time").type == pa.timestamp("us", tz="UTC")
    assert schema.field("close_time").type == pa.timestamp("us", tz="UTC")
    for name in ("open", "high", "low", "close", "volume"):
        assert schema.field(name).type == pa.decimal128(38, 18)
    assert schema.metadata == {
        b"adaptive_trading_bot.schema_version": b"1",
        b"adaptive_trading_bot.source_id": b"archive-a",
        b"adaptive_trading_bot.symbol": b"BTC/USDT",
        b"adaptive_trading_bot.timeframe": b"15m",
    }


@pytest.mark.parametrize(
    ("symbol", "timeframe", "open_time"),
    [
        (BTC_USDT, Timeframe.MINUTES_15, datetime(2026, 9, 9, 12, 0, tzinfo=UTC)),
        (ETH_USDT, Timeframe.HOUR_1, datetime(2026, 9, 9, 12, 0, tzinfo=UTC)),
        (BTC_USDT, Timeframe.HOURS_4, datetime(2026, 9, 9, 12, 0, tzinfo=UTC)),
    ],
)
def test_lossless_round_trip_for_symbols_timeframes_and_utc(
    tmp_path: Path,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    open_time: datetime,
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candle = make_candle(open_time, symbol=symbol, timeframe=timeframe)

    result = store.merge(SOURCE, [candle])
    restored = store.read(SOURCE, symbol, timeframe)

    assert result.added == 1
    assert restored == (candle,)
    assert restored[0].open.as_tuple() == candle.open.as_tuple()
    assert restored[0].volume.as_tuple() == candle.volume.as_tuple()
    assert restored[0].open_time == open_time
    assert restored[0].open_time.tzinfo is UTC


def test_sources_are_normalized_and_stored_separately(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candle = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    other = replace(
        candle,
        open=Decimal("200"),
        high=Decimal("210"),
        low=Decimal("190"),
        close=Decimal("205"),
    )
    first_source = SourceId(" Archive-A ")
    second_source = SourceId("archive-b")

    store.merge(first_source, [candle])
    store.merge(second_source, [other])

    assert first_source.value == "archive-a"
    assert store.read(first_source, BTC_USDT, Timeframe.MINUTES_15) == (candle,)
    assert store.read(second_source, BTC_USDT, Timeframe.MINUTES_15) == (other,)
    assert partition_path(tmp_path, first_source, candle).exists()
    assert partition_path(tmp_path, second_source, other).exists()


@pytest.mark.parametrize("value", ["", "bad/source", "../escape", "has space"])
def test_invalid_source_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="source_id"):
        SourceId(value)


def test_read_is_chronological_and_uses_half_open_range(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    second = make_candle(first.close_time)
    third = make_candle(second.close_time)

    store.merge(SOURCE, [third, first, second])

    assert store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15) == (first, second, third)
    assert store.read(
        SOURCE,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=second.open_time,
        end=third.open_time,
    ) == (second,)
    assert (
        store.read(
            SOURCE,
            BTC_USDT,
            Timeframe.MINUTES_15,
            start=second.open_time,
            end=second.open_time,
        )
        == ()
    )


def test_identical_and_overlapping_writes_are_idempotent(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    second = make_candle(first.close_time)
    third = make_candle(second.close_time)

    initial = store.merge(SOURCE, [first, second])
    repeated = store.merge(SOURCE, [first, second])
    overlapping = store.merge(SOURCE, [second, third])

    assert initial.added == 2
    assert initial.partitions_written == 1
    assert repeated.added == 0
    assert repeated.unchanged == 2
    assert repeated.partitions_written == 0
    assert overlapping.added == 1
    assert overlapping.unchanged == 1
    assert store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15) == (first, second, third)


def test_conflicting_duplicate_key_fails_closed(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    original = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    conflicting = replace(original, close=original.close + Decimal("1"))
    store.merge(SOURCE, [original])

    with pytest.raises(CandleConflictError, match="conflicting candle"):
        store.merge(SOURCE, [conflicting])

    assert store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15) == (original,)


def test_monthly_partition_boundary_uses_candle_open_month(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    september = make_candle(datetime(2026, 9, 30, 23, 45, tzinfo=UTC))
    october = make_candle(datetime(2026, 10, 1, 0, 0, tzinfo=UTC))

    result = store.merge(SOURCE, [october, september])

    assert result.partitions_written == 2
    assert partition_path(tmp_path, SOURCE, september).exists()
    assert partition_path(tmp_path, SOURCE, october).exists()
    assert store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15) == (september, october)


def test_clean_integrity_report_and_gap_detection(tmp_path: Path) -> None:
    clean_store = ParquetHistoricalCandleStore(tmp_path / "clean")
    first = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    second = make_candle(first.close_time)
    clean_store.merge(SOURCE, [first, second])

    clean_report = clean_store.scan_integrity(SOURCE, BTC_USDT, Timeframe.MINUTES_15)

    assert clean_report.is_clean
    assert clean_report.rows_scanned == 2
    assert clean_report.issues == ()

    gap_store = ParquetHistoricalCandleStore(tmp_path / "gap")
    after_gap = make_candle(second.close_time)
    gap_store.merge(SOURCE, [first, after_gap])

    gap_report = gap_store.scan_integrity(SOURCE, BTC_USDT, Timeframe.MINUTES_15)
    assert not gap_report.is_clean
    assert [issue.kind for issue in gap_report.issues] == [IntegrityIssueKind.GAP]


def test_integrity_reports_duplicates_order_and_overlap(tmp_path: Path) -> None:
    first = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    second = make_candle(first.close_time)
    write_raw_partition(tmp_path, SOURCE, [second, first, first])
    store = ParquetHistoricalCandleStore(tmp_path)

    report = store.scan_integrity(SOURCE, BTC_USDT, Timeframe.MINUTES_15)
    kinds = {issue.kind for issue in report.issues}

    assert IntegrityIssueKind.OUT_OF_ORDER in kinds
    assert IntegrityIssueKind.DUPLICATE_OPEN_TIME in kinds
    assert IntegrityIssueKind.OVERLAP in kinds


def test_malformed_row_is_reported_and_reader_rejects_it(tmp_path: Path) -> None:
    candle = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    malformed = arrow_row(candle)
    malformed["high"] = Decimal("50")
    path = write_raw_partition(
        tmp_path,
        SOURCE,
        [candle],
        rows=[malformed],
    )
    store = ParquetHistoricalCandleStore(tmp_path)

    report = store.scan_integrity(SOURCE, BTC_USDT, Timeframe.MINUTES_15)

    assert any(issue.kind is IntegrityIssueKind.MALFORMED_ROW for issue in report.issues)
    with pytest.raises(DatasetIntegrityError, match="malformed row"):
        store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15)
    assert path.exists()


def test_schema_mismatch_is_reported_and_reader_rejects_it(tmp_path: Path) -> None:
    candle = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    path = partition_path(tmp_path, SOURCE, candle)
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([arrow_row(candle)]), path)
    store = ParquetHistoricalCandleStore(tmp_path)

    report = store.scan_integrity(SOURCE, BTC_USDT, Timeframe.MINUTES_15)

    assert any(issue.kind is IntegrityIssueKind.SCHEMA_MISMATCH for issue in report.issues)
    with pytest.raises(DatasetIntegrityError, match="schema/version/provenance mismatch"):
        store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15)


def test_failed_serialization_does_not_replace_canonical_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    second = make_candle(first.close_time)
    store.merge(SOURCE, [first])
    canonical = partition_path(tmp_path, SOURCE, first)
    original_bytes = canonical.read_bytes()

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(pq, "write_table", fail_write)

    with pytest.raises(StorageWriteError, match="atomic partition write failed"):
        store.merge(SOURCE, [second])

    assert canonical.read_bytes() == original_bytes
    assert list(canonical.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "value",
    [
        Decimal("100.0000000000000000001"),
        Decimal("100000000000000000000"),
    ],
)
def test_unrepresentable_decimal_fails_instead_of_rounding(tmp_path: Path, value: Decimal) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candle = make_candle(datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
    unrepresentable = replace(
        candle,
        open=value,
        high=value,
        low=value,
        close=value,
    )

    with pytest.raises(UnrepresentableDecimalError, match="not exactly representable"):
        store.merge(SOURCE, [unrepresentable])

    assert store.read(SOURCE, BTC_USDT, Timeframe.MINUTES_15) == ()
