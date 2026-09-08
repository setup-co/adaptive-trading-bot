"""Lossless, provenance-isolated Parquet storage for historical candles."""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from re import fullmatch
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.domain import (
    Candle,
    Timeframe,
    TradingSymbol,
    align_timestamp,
    are_contiguous,
)
from adaptive_trading_bot.domain.clock import require_utc

SCHEMA_VERSION = "1"
DECIMAL_PRECISION = 38
DECIMAL_SCALE = 18
DECIMAL_TYPE = pa.decimal128(DECIMAL_PRECISION, DECIMAL_SCALE)
TIMESTAMP_TYPE = pa.timestamp("us", tz="UTC")

_BASE_FIELDS: tuple[pa.Field[pa.DataType], ...] = (
    pa.field("base", pa.string(), nullable=False),
    pa.field("quote", pa.string(), nullable=False),
    pa.field("timeframe", pa.string(), nullable=False),
    pa.field("open_time", TIMESTAMP_TYPE, nullable=False),
    pa.field("close_time", TIMESTAMP_TYPE, nullable=False),
    pa.field("open", DECIMAL_TYPE, nullable=False),
    pa.field("high", DECIMAL_TYPE, nullable=False),
    pa.field("low", DECIMAL_TYPE, nullable=False),
    pa.field("close", DECIMAL_TYPE, nullable=False),
    pa.field("volume", DECIMAL_TYPE, nullable=False),
)
_BASE_SCHEMA = pa.schema(_BASE_FIELDS)


class HistoricalCandleStoreError(Exception):
    """Base error for historical candle storage failures."""


class CandleConflictError(HistoricalCandleStoreError):
    """An existing series key has different candle field values."""


class DatasetIntegrityError(HistoricalCandleStoreError):
    """Stored data cannot be trusted or reconstructed as canonical candles."""


class StorageWriteError(HistoricalCandleStoreError):
    """A partition could not be serialized and atomically replaced."""


class UnrepresentableDecimalError(HistoricalCandleStoreError):
    """A Decimal cannot be represented exactly by the storage schema."""


@dataclass(frozen=True, slots=True)
class SourceId:
    """Normalized, path-safe provenance identifier for one logical data source."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", normalized):
            raise ValueError(
                "source_id must be 1-64 lowercase-normalizable ASCII letters, "
                "digits, '.', '_', or '-'"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Summary of an idempotent batch merge."""

    added: int
    unchanged: int
    partitions_written: int


class IntegrityIssueKind(StrEnum):
    """Machine-readable historical dataset integrity failures."""

    DUPLICATE_OPEN_TIME = "duplicate_open_time"
    OUT_OF_ORDER = "out_of_order"
    GAP = "gap"
    OVERLAP = "overlap"
    MALFORMED_ROW = "malformed_row"
    SCHEMA_MISMATCH = "schema_mismatch"


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    """One deterministic finding from a dataset integrity scan."""

    kind: IntegrityIssueKind
    message: str
    partition: str
    open_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """Structured result for a requested source/market/timeframe range."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    start: datetime | None
    end: datetime | None
    rows_scanned: int
    issues: tuple[IntegrityIssue, ...]

    @property
    def is_clean(self) -> bool:
        """Return whether the requested series/range passed every check."""

        return not self.issues


class HistoricalCandleStore(Protocol):
    """Small typed contract for deterministic historical candle storage."""

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        """Idempotently merge candles for one logical source."""
        ...

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        """Read chronological candles whose open times are in ``[start, end)``."""
        ...

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        """Report integrity failures without mutating the dataset."""
        ...


def candle_schema(source_id: SourceId, symbol: TradingSymbol, timeframe: Timeframe) -> pa.Schema:
    """Return the exact versioned Arrow schema for one partition series."""

    return _BASE_SCHEMA.with_metadata(
        {
            b"adaptive_trading_bot.schema_version": SCHEMA_VERSION.encode(),
            b"adaptive_trading_bot.source_id": source_id.value.encode(),
            b"adaptive_trading_bot.symbol": str(symbol).encode(),
            b"adaptive_trading_bot.timeframe": timeframe.value.encode(),
        }
    )


class ParquetHistoricalCandleStore:
    """Monthly-partitioned local Parquet implementation of the store contract."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        grouped: dict[tuple[TradingSymbol, Timeframe, int, int], list[Candle]] = defaultdict(list)
        for candle in candles:
            grouped[
                (candle.symbol, candle.timeframe, candle.open_time.year, candle.open_time.month)
            ].append(candle)

        added = 0
        unchanged = 0
        partitions_written = 0
        prepared_partitions: list[tuple[Path, pa.Table]] = []
        for (symbol, timeframe, year, month), incoming in sorted(
            grouped.items(), key=lambda item: _partition_sort_key(item[0])
        ):
            path = self._partition_path(source_id, symbol, timeframe, year, month)
            existing = self._read_partition(path, source_id, symbol, timeframe)
            merged_by_open = {candle.open_time: candle for candle in existing}
            if len(merged_by_open) != len(existing):
                raise DatasetIntegrityError(f"duplicate open_time in existing partition: {path}")

            changed = False
            for candle in sorted(incoming, key=lambda item: item.open_time):
                _validate_candle_decimals(candle)
                current = merged_by_open.get(candle.open_time)
                if current is None:
                    merged_by_open[candle.open_time] = candle
                    added += 1
                    changed = True
                elif current == candle:
                    unchanged += 1
                else:
                    raise CandleConflictError(
                        "conflicting candle for "
                        f"source={source_id}, symbol={symbol}, timeframe={timeframe.value}, "
                        f"open_time={candle.open_time.isoformat()}"
                    )

            if changed:
                ordered = sorted(merged_by_open.values(), key=lambda item: item.open_time)
                table = _candles_to_table(ordered, candle_schema(source_id, symbol, timeframe))
                prepared_partitions.append((path, table))

        for path, table in prepared_partitions:
            self._atomic_write(path, table)
            partitions_written += 1

        return MergeResult(added=added, unchanged=unchanged, partitions_written=partitions_written)

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        start, end = _validate_range(start, end)
        candles: list[Candle] = []
        for path in self._series_files(source_id, symbol, timeframe):
            candles.extend(self._read_partition(path, source_id, symbol, timeframe))

        selected = [
            candle
            for candle in candles
            if (start is None or candle.open_time >= start)
            and (end is None or candle.open_time < end)
        ]
        ordered = sorted(selected, key=lambda item: item.open_time)
        if len({candle.open_time for candle in ordered}) != len(ordered):
            raise DatasetIntegrityError("duplicate open_time across series partitions")
        return tuple(ordered)

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        start, end = _validate_range(start, end)
        issues: list[IntegrityIssue] = []
        physical_candles: list[tuple[Candle, str]] = []

        for path in self._series_files(source_id, symbol, timeframe):
            partition = str(path.relative_to(self._root))
            try:
                table = pq.ParquetFile(path).read()
            except Exception as exc:
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.SCHEMA_MISMATCH,
                        f"partition cannot be read as Parquet: {exc}",
                        partition,
                    )
                )
                continue

            expected_schema = candle_schema(source_id, symbol, timeframe)
            if not table.schema.equals(expected_schema, check_metadata=True):
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.SCHEMA_MISMATCH,
                        "Arrow schema or storage metadata does not match the requested series",
                        partition,
                    )
                )

            try:
                rows = table.to_pylist()
            except Exception as exc:
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.MALFORMED_ROW,
                        f"partition rows cannot be decoded: {exc}",
                        partition,
                    )
                )
                continue

            for index, row in enumerate(rows):
                try:
                    candle = _row_to_candle(row, symbol, timeframe)
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(
                        IntegrityIssue(
                            IntegrityIssueKind.MALFORMED_ROW,
                            f"row {index} cannot reconstruct a valid Candle: {exc}",
                            partition,
                        )
                    )
                    continue
                if (start is None or candle.open_time >= start) and (
                    end is None or candle.open_time < end
                ):
                    physical_candles.append((candle, partition))

        for (previous, _), (current, partition) in pairwise(physical_candles):
            if current.open_time < previous.open_time:
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.OUT_OF_ORDER,
                        "row open_time precedes the previous physical row",
                        partition,
                        current.open_time,
                    )
                )

        seen: set[datetime] = set()
        for candle, partition in physical_candles:
            if candle.open_time in seen:
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.DUPLICATE_OPEN_TIME,
                        "duplicate candle open_time key",
                        partition,
                        candle.open_time,
                    )
                )
            seen.add(candle.open_time)

        chronological = sorted(physical_candles, key=lambda item: item[0].open_time)
        issues.extend(
            _boundary_coverage_issues(
                chronological,
                timeframe=timeframe,
                start=start,
                end=end,
            )
        )
        for (previous, _), (current, partition) in pairwise(chronological):
            if current.open_time > previous.close_time:
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.GAP,
                        f"gap after {previous.close_time.isoformat()}",
                        partition,
                        current.open_time,
                    )
                )
            elif not are_contiguous(previous, current):
                issues.append(
                    IntegrityIssue(
                        IntegrityIssueKind.OVERLAP,
                        f"overlap/non-contiguous interval after {previous.open_time.isoformat()}",
                        partition,
                        current.open_time,
                    )
                )

        return IntegrityReport(
            source_id=source_id,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            rows_scanned=len(physical_candles),
            issues=tuple(issues),
        )

    def _series_files(
        self, source_id: SourceId, symbol: TradingSymbol, timeframe: Timeframe
    ) -> list[Path]:
        series_root = (
            self._root
            / f"source={source_id.value}"
            / f"base={symbol.base}"
            / f"quote={symbol.quote}"
            / f"timeframe={timeframe.value}"
        )
        return sorted(series_root.glob("year=*/month=*/candles.parquet"))

    def _partition_path(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        year: int,
        month: int,
    ) -> Path:
        return (
            self._root
            / f"source={source_id.value}"
            / f"base={symbol.base}"
            / f"quote={symbol.quote}"
            / f"timeframe={timeframe.value}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "candles.parquet"
        )

    def _read_partition(
        self,
        path: Path,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
    ) -> list[Candle]:
        if not path.exists():
            return []
        try:
            table = pq.ParquetFile(path).read()
        except Exception as exc:
            raise DatasetIntegrityError(f"cannot read partition {path}: {exc}") from exc
        if not table.schema.equals(
            candle_schema(source_id, symbol, timeframe), check_metadata=True
        ):
            raise DatasetIntegrityError(f"schema/version/provenance mismatch in {path}")

        try:
            rows = table.to_pylist()
        except Exception as exc:
            raise DatasetIntegrityError(f"cannot decode partition rows in {path}: {exc}") from exc

        candles: list[Candle] = []
        for index, row in enumerate(rows):
            try:
                candles.append(_row_to_candle(row, symbol, timeframe))
            except (KeyError, TypeError, ValueError) as exc:
                raise DatasetIntegrityError(f"malformed row {index} in {path}: {exc}") from exc
        if candles != sorted(candles, key=lambda item: item.open_time):
            raise DatasetIntegrityError(f"out-of-order rows in {path}")
        return candles

    def _atomic_write(self, path: Path, table: pa.Table) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            pq.write_table(table, temporary_path, compression="zstd", version="2.6")
            os.replace(temporary_path, path)
        except Exception as exc:
            temporary_path.unlink(missing_ok=True)
            raise StorageWriteError(f"atomic partition write failed for {path}: {exc}") from exc


def _partition_sort_key(
    key: tuple[TradingSymbol, Timeframe, int, int],
) -> tuple[str, str, int, int]:
    symbol, timeframe, year, month = key
    return str(symbol), timeframe.value, year, month


def _validate_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    normalized_start = require_utc(start, field="start") if start is not None else None
    normalized_end = require_utc(end, field="end") if end is not None else None
    if (
        normalized_start is not None
        and normalized_end is not None
        and normalized_start > normalized_end
    ):
        raise ValueError("start must be less than or equal to end")
    return normalized_start, normalized_end


def _boundary_coverage_issues(
    chronological: list[tuple[Candle, str]],
    *,
    timeframe: Timeframe,
    start: datetime | None,
    end: datetime | None,
) -> list[IntegrityIssue]:
    """Report missing expected opens at explicitly requested range boundaries."""

    expected_first: datetime | None = None
    if start is not None:
        expected_first = align_timestamp(start, timeframe)
        if expected_first < start:
            expected_first += timeframe.duration

    expected_last: datetime | None = None
    if end is not None:
        expected_last = align_timestamp(end, timeframe)
        if expected_last == end:
            expected_last -= timeframe.duration

    has_expected_open = not (
        expected_first is not None and expected_last is not None and expected_first > expected_last
    )
    if not has_expected_open:
        return []

    if not chronological:
        if expected_first is not None and expected_last is not None:
            return [
                IntegrityIssue(
                    IntegrityIssueKind.GAP,
                    "requested non-empty range contains no candles",
                    "<requested-range>",
                    expected_first,
                )
            ]
        return []

    issues: list[IntegrityIssue] = []
    actual_first = chronological[0][0].open_time
    actual_last = chronological[-1][0].open_time
    if expected_first is not None and actual_first != expected_first:
        issues.append(
            IntegrityIssue(
                IntegrityIssueKind.GAP,
                f"missing first expected candle at {expected_first.isoformat()}",
                "<requested-range>",
                expected_first,
            )
        )
    if expected_last is not None and actual_last != expected_last:
        issues.append(
            IntegrityIssue(
                IntegrityIssueKind.GAP,
                f"missing last expected candle at {expected_last.isoformat()}",
                "<requested-range>",
                expected_last,
            )
        )
    return issues


def _validate_candle_decimals(candle: Candle) -> None:
    for name in ("open", "high", "low", "close", "volume"):
        value = getattr(candle, name)
        if value.is_zero():
            continue
        decimal_tuple = value.as_tuple()
        shift = decimal_tuple.exponent + DECIMAL_SCALE
        digits = decimal_tuple.digits
        if shift < 0:
            discarded_digits = -shift
            exactly_scalable = discarded_digits <= len(digits) and not any(
                digits[-discarded_digits:]
            )
            scaled_digit_count = len(digits) - discarded_digits
        else:
            exactly_scalable = True
            scaled_digit_count = len(digits) + shift
        if not exactly_scalable or scaled_digit_count > DECIMAL_PRECISION:
            raise UnrepresentableDecimalError(
                f"{name}={value} is not exactly representable as decimal128"
                f"({DECIMAL_PRECISION}, {DECIMAL_SCALE})"
            )


def _candles_to_table(candles: Iterable[Candle], schema: pa.Schema) -> pa.Table:
    rows = [
        {
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
        for candle in candles
    ]
    return pa.Table.from_pylist(rows, schema=schema)


def _row_to_candle(
    row: dict[str, object], expected_symbol: TradingSymbol, expected_timeframe: Timeframe
) -> Candle:
    base = row["base"]
    quote = row["quote"]
    timeframe_value = row["timeframe"]
    open_time = row["open_time"]
    close_time = row["close_time"]
    if not isinstance(base, str) or not isinstance(quote, str):
        raise TypeError("symbol columns must be strings")
    if not isinstance(timeframe_value, str):
        raise TypeError("timeframe must be a string")
    if not isinstance(open_time, datetime) or not isinstance(close_time, datetime):
        raise TypeError("timestamp columns must decode to datetime")

    symbol = TradingSymbol(base, quote)
    timeframe = Timeframe(timeframe_value)
    if symbol != expected_symbol or timeframe is not expected_timeframe:
        raise ValueError("row symbol/timeframe does not match its partition")

    decimal_values: dict[str, Decimal] = {}
    for name in ("open", "high", "low", "close", "volume"):
        value = row[name]
        if not isinstance(value, Decimal):
            raise TypeError(f"{name} must decode to Decimal")
        decimal_values[name] = value

    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open=decimal_values["open"],
        high=decimal_values["high"],
        low=decimal_values["low"],
        close=decimal_values["close"],
        volume=decimal_values["volume"],
    )
