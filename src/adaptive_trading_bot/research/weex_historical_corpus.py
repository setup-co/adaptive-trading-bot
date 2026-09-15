"""Immutable WEEX Futures LAST research-corpus construction and locked reads.

The canonical authority in this module is the public WEEX Futures V3 5-minute LAST
series.  Five-minute rows never enter the V1 trading-domain ``Timeframe`` enum;
instead they are strictly validated and snapshotted here, then the existing V1
15m/1h/4h ``Candle`` values are derived from complete UTC buckets only.
"""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    IntegrityReport,
    MergeResult,
    ParquetHistoricalCandleStore,
    SourceId,
)

WEEX_HISTORY_ENDPOINT: Final = "https://api-contract.weex.com/capi/v3/market/historyKlines"
CANONICAL_INTERVAL: Final = "5m"
PRICE_TYPE: Final = "LAST"
CORPUS_SCHEMA_VERSION: Final = "weex-v1-research-corpus-v1"
CANONICAL_SCHEMA_VERSION: Final = "weex-futures-v3-last-5m-v1"
_FIVE_MINUTES: Final = timedelta(minutes=5)
_PAGE_LIMIT: Final = 100
_DECIMAL_TYPE: Final = pa.decimal128(38, 18)
_TIMESTAMP_TYPE: Final = pa.timestamp("ms", tz="UTC")
_CANONICAL_SCHEMA: Final = pa.schema(
    (
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("open_time", _TIMESTAMP_TYPE, nullable=False),
        pa.field("close_time", _TIMESTAMP_TYPE, nullable=False),
        pa.field("open", _DECIMAL_TYPE, nullable=False),
        pa.field("high", _DECIMAL_TYPE, nullable=False),
        pa.field("low", _DECIMAL_TYPE, nullable=False),
        pa.field("close", _DECIMAL_TYPE, nullable=False),
        pa.field("base_volume", _DECIMAL_TYPE, nullable=False),
        pa.field("quote_volume", _DECIMAL_TYPE, nullable=False),
        pa.field("trade_count", pa.int64(), nullable=False),
        pa.field("taker_buy_base_volume", _DECIMAL_TYPE, nullable=False),
        pa.field("taker_buy_quote_volume", _DECIMAL_TYPE, nullable=False),
    )
).with_metadata(
    {
        b"adaptive_trading_bot.schema_version": CANONICAL_SCHEMA_VERSION.encode(),
        b"adaptive_trading_bot.source_endpoint": WEEX_HISTORY_ENDPOINT.encode(),
        b"adaptive_trading_bot.price_type": PRICE_TYPE.encode(),
    }
)

# This registry is the reviewable project-owned authority for datasets accepted by name.
# Adding a future corpus requires a new qualification issue and code review.
QUALIFIED_DATASET_MANIFEST_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "weex-v1-5m-last-a629a29b580b2e103adba918": (
            "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
        )
    }
)


class WeexHistoricalCorpusError(Exception):
    """Base error for qualification, construction, or locked-corpus use."""


class WeexSourceRequestError(WeexHistoricalCorpusError):
    """The public source could not be traversed without ambiguity."""


class WeexSourceIntegrityError(WeexHistoricalCorpusError):
    """Source content failed the canonical integrity contract."""


class CorpusManifestError(WeexHistoricalCorpusError):
    """A manifest is unknown, inconsistent, or not qualified."""


class CorpusHashMismatchError(WeexHistoricalCorpusError):
    """A locked artifact no longer matches its accepted SHA-256."""


class CorpusRangeError(WeexHistoricalCorpusError):
    """A requested range is not wholly inside one qualified interval."""


class CorpusQualificationStatus(StrEnum):
    """Explicit dataset qualification result."""

    QUALIFIED = "QUALIFIED"
    PARTIAL = "PARTIAL"
    NOT_QUALIFIED = "NOT_QUALIFIED"


@dataclass(frozen=True, slots=True, order=True)
class CorpusInterval:
    """One immutable half-open UTC interval."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = require_utc(self.start, field="interval.start")
        end = require_utc(self.end, field="interval.end")
        if start >= end:
            raise ValueError("corpus interval must be non-empty")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, start: datetime, end: datetime) -> bool:
        """Return whether ``[start, end)`` is wholly contained."""

        return self.start <= start <= end <= self.end


@dataclass(frozen=True, slots=True)
class CanonicalFiveMinuteCandle:
    """Strict lossless projection of one 11-field WEEX V3 row."""

    symbol: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal

    def __post_init__(self) -> None:
        if self.symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise WeexSourceIntegrityError("canonical symbol is outside the V1 allow-list")
        open_time = require_utc(self.open_time, field="canonical open_time")
        close_time = require_utc(self.close_time, field="canonical close_time")
        if _milliseconds(open_time) % 300_000:
            raise WeexSourceIntegrityError("canonical open_time is off the UTC 5m grid")
        if close_time != open_time + _FIVE_MINUTES:
            raise WeexSourceIntegrityError("canonical close_time is not open_time + 5m")
        object.__setattr__(self, "open_time", open_time)
        object.__setattr__(self, "close_time", close_time)
        decimals = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.base_volume,
            self.quote_volume,
            self.taker_buy_base_volume,
            self.taker_buy_quote_volume,
        )
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals):
            raise WeexSourceIntegrityError("canonical numeric fields must be finite Decimals")
        if any(value <= 0 for value in (self.open, self.high, self.low, self.close)):
            raise WeexSourceIntegrityError("canonical OHLC prices must be positive")
        if self.high < max(self.open, self.low, self.close):
            raise WeexSourceIntegrityError("canonical high violates OHLC semantics")
        if self.low > min(self.open, self.high, self.close):
            raise WeexSourceIntegrityError("canonical low violates OHLC semantics")
        if any(value < 0 for value in decimals[4:]):
            raise WeexSourceIntegrityError("canonical additive fields must be non-negative")
        if type(self.trade_count) is not int or self.trade_count < 0:
            raise WeexSourceIntegrityError("canonical trade_count must be a non-negative integer")

    @classmethod
    def from_wire_row(cls, symbol: str, row: object) -> CanonicalFiveMinuteCandle:
        """Parse one raw response row without normalization or repair."""

        if not isinstance(row, list) or len(row) != 11:
            raise WeexSourceIntegrityError("WEEX row must contain exactly 11 fields")
        try:
            open_ms = _strict_integer(row[0], "open timestamp")
            close_ms = _strict_integer(row[6], "close timestamp")
            trade_count = _strict_integer(row[8], "trade count")
            values = tuple(
                _strict_decimal(row[index], f"row field {index}")
                for index in (1, 2, 3, 4, 5, 7, 9, 10)
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            raise WeexSourceIntegrityError(f"malformed WEEX row: {error}") from error
        return cls(
            symbol=symbol,
            open_time=datetime.fromtimestamp(open_ms / 1_000, tz=UTC),
            close_time=datetime.fromtimestamp(close_ms / 1_000, tz=UTC),
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            base_volume=values[4],
            quote_volume=values[5],
            trade_count=trade_count,
            taker_buy_base_volume=values[6],
            taker_buy_quote_volume=values[7],
        )


@dataclass(frozen=True, slots=True)
class WeexFetchResult:
    """One deterministic complete source traversal."""

    candles: tuple[CanonicalFiveMinuteCandle, ...]
    request_count: int
    retry_count: int
    identical_duplicate_count: int = 0


class WeexV3PublicHistorySource:
    """Public-only fixed WEEX Futures V3 5m LAST source."""

    def __init__(self, *, timeout_seconds: float = 30, max_retries: int = 3) -> None:
        if timeout_seconds <= 0 or max_retries < 0:
            raise ValueError("timeout must be positive and max_retries non-negative")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> WeexFetchResult:
        """Traverse one aligned half-open range newest-first without content repair."""

        start = require_utc(start, field="start")
        end = require_utc(end, field="end")
        if symbol not in {"BTCUSDT", "ETHUSDT"} or start >= end:
            raise ValueError("unsupported symbol or empty source range")
        if _milliseconds(start) % 300_000 or _milliseconds(end) % 300_000:
            raise ValueError("source range must align to the UTC 5m grid")

        by_open: dict[datetime, CanonicalFiveMinuteCandle] = {}
        current_end = end - _FIVE_MINUTES
        request_count = 0
        retry_count = 0
        identical_duplicate_count = 0
        while current_end >= start:
            parameters = {
                "symbol": symbol,
                "interval": CANONICAL_INTERVAL,
                "priceType": PRICE_TYPE,
                "startTime": str(_milliseconds(start)),
                "endTime": str(_milliseconds(current_end)),
                "limit": str(_PAGE_LIMIT),
            }
            payload, retries = self._request(parameters)
            request_count += 1
            retry_count += retries
            if not isinstance(payload, list) or not payload:
                raise WeexSourceRequestError(
                    f"empty/non-list page before {current_end.isoformat()}"
                )

            page_opens: list[datetime] = []
            for raw_row in payload:
                if not isinstance(raw_row, list) or not raw_row:
                    raise WeexSourceIntegrityError("WEEX row cannot expose an open timestamp")
                try:
                    raw_open = datetime.fromtimestamp(
                        _strict_integer(raw_row[0], "open timestamp") / 1_000,
                        tz=UTC,
                    )
                except (TypeError, ValueError, OSError) as error:
                    raise WeexSourceIntegrityError(
                        "WEEX row has no valid range-filter timestamp"
                    ) from error
                if raw_open > current_end:
                    raise WeexSourceRequestError("WEEX page exceeded its inclusive end boundary")
                if raw_open < start:
                    continue
                try:
                    candle = CanonicalFiveMinuteCandle.from_wire_row(symbol, raw_row)
                except WeexSourceIntegrityError as error:
                    raise WeexSourceIntegrityError(
                        f"{symbol} row at {raw_open.isoformat()} is invalid: {error}"
                    ) from error
                page_opens.append(candle.open_time)
                previous = by_open.get(candle.open_time)
                if previous is not None and previous != candle:
                    raise WeexSourceIntegrityError(
                        f"conflicting duplicate at {candle.open_time.isoformat()}"
                    )
                if previous is not None:
                    identical_duplicate_count += 1
                by_open[candle.open_time] = candle
            if not page_opens:
                raise WeexSourceRequestError("WEEX page made no in-range backward progress")
            next_end = min(page_opens) - _FIVE_MINUTES
            if next_end >= current_end:
                raise WeexSourceRequestError("WEEX pagination failed to move backward")
            current_end = next_end

        ordered = tuple(by_open[key] for key in sorted(by_open))
        _validate_exact_grid(ordered, symbol=symbol, start=start, end=end)
        return WeexFetchResult(
            ordered,
            request_count,
            retry_count,
            identical_duplicate_count,
        )

    def _request(self, parameters: dict[str, str]) -> tuple[object, int]:
        url = f"{WEEX_HISTORY_ENDPOINT}?{urllib.parse.urlencode(parameters)}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "adaptive-trading-bot-qualified-corpus/1"},
        )
        for attempt in range(self._max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                    return json.load(response), attempt
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt == self._max_retries:
                    raise WeexSourceRequestError(
                        f"WEEX HTTP request failed with status {error.code}"
                    ) from error
            except (
                TimeoutError,
                urllib.error.URLError,
                http.client.HTTPException,
                OSError,
            ) as error:
                if attempt == self._max_retries:
                    raise WeexSourceRequestError("WEEX transport request failed") from error
            time.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable retry loop")


class WeexHistorySource(Protocol):
    """Narrow build-time source boundary; locked readers never receive one."""

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> WeexFetchResult:
        """Return one strictly qualified canonical interval."""
        ...


@dataclass(frozen=True, slots=True)
class CorpusArtifact:
    """Hash-bound artifact entry stored in the version manifest."""

    path: str
    sha256: str
    size_bytes: int
    row_count: int
    symbol: str
    timeframe: str


@dataclass(frozen=True, slots=True)
class WeexResearchCorpusManifest:
    """Versioned immutable description of one accepted local corpus."""

    schema_version: str
    dataset_version: str
    source_id: str
    source_endpoint: str
    price_type: str
    canonical_interval: str
    source_code_commit: str
    built_at: datetime
    btc_status: CorpusQualificationStatus
    eth_status: CorpusQualificationStatus
    source_status: str
    canonical_intervals: Mapping[str, tuple[CorpusInterval, ...]]
    derived_intervals: Mapping[str, Mapping[str, tuple[CorpusInterval, ...]]]
    quarantines: Mapping[str, tuple[CorpusInterval, ...]]
    warmup_exclusions: Mapping[str, tuple[CorpusInterval, ...]]
    source_request_counts: Mapping[str, int]
    source_retry_counts: Mapping[str, int]
    identical_duplicate_counts: Mapping[str, int]
    artifacts: tuple[CorpusArtifact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "built_at", require_utc(self.built_at, field="built_at"))
        for name in ("canonical_intervals", "quarantines", "warmup_exclusions"):
            value = getattr(self, name)
            object.__setattr__(self, name, MappingProxyType(dict(value)))
        object.__setattr__(
            self,
            "derived_intervals",
            MappingProxyType(
                {
                    symbol: MappingProxyType(dict(by_timeframe))
                    for symbol, by_timeframe in self.derived_intervals.items()
                }
            ),
        )
        for name in (
            "source_request_counts",
            "source_retry_counts",
            "identical_duplicate_counts",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, MappingProxyType(dict(value)))

    def validate(self) -> None:
        """Fail closed on unsupported schema, policy, identity, or ordering."""

        if self.schema_version != CORPUS_SCHEMA_VERSION:
            raise CorpusManifestError("unknown corpus schema version")
        if not self.dataset_version.startswith("weex-v1-5m-last-"):
            raise CorpusManifestError("unknown dataset version identity")
        if self.source_endpoint != WEEX_HISTORY_ENDPOINT or self.price_type != PRICE_TYPE:
            raise CorpusManifestError("manifest source provenance is not canonical WEEX LAST")
        if self.canonical_interval != CANONICAL_INTERVAL:
            raise CorpusManifestError("manifest canonical interval is not 5m")
        if self.btc_status is not CorpusQualificationStatus.QUALIFIED:
            raise CorpusManifestError("BTC corpus is not qualified")
        if self.eth_status not in {
            CorpusQualificationStatus.QUALIFIED,
            CorpusQualificationStatus.PARTIAL,
        }:
            raise CorpusManifestError("ETH corpus is not accepted")
        if self.source_status != "QUALIFIED_WITH_BOUNDARIES":
            raise CorpusManifestError("historical source is not boundary-qualified")
        if not self.artifacts or tuple(self.artifacts) != tuple(
            sorted(self.artifacts, key=lambda item: item.path)
        ):
            raise CorpusManifestError("artifacts must be non-empty and canonically ordered")
        if len({item.path for item in self.artifacts}) != len(self.artifacts):
            raise CorpusManifestError("artifact paths must be unique")
        for artifact in self.artifacts:
            relative = PurePosixPath(artifact.path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not artifact.path.endswith(".parquet")
                or len(artifact.sha256) != 64
                or any(character not in "0123456789abcdef" for character in artifact.sha256)
                or type(artifact.size_bytes) is not int
                or artifact.size_bytes <= 0
                or type(artifact.row_count) is not int
                or artifact.row_count <= 0
                or artifact.symbol not in {"BTCUSDT", "ETHUSDT"}
                or artifact.timeframe not in {"5m", "15m", "1h", "4h"}
            ):
                raise CorpusManifestError("artifact inventory entry is invalid")
        for mapping in (self.canonical_intervals, self.quarantines, self.warmup_exclusions):
            _validate_interval_mapping(mapping)
        for counts in (
            self.source_request_counts,
            self.source_retry_counts,
            self.identical_duplicate_counts,
        ):
            if set(counts) != {"BTCUSDT", "ETHUSDT"} or any(
                type(value) is not int or value < 0 for value in counts.values()
            ):
                raise CorpusManifestError("source audit counts are invalid")
        if set(self.derived_intervals) != {"BTCUSDT", "ETHUSDT"}:
            raise CorpusManifestError("derived interval symbol set is invalid")
        for symbol, by_timeframe in self.derived_intervals.items():
            if set(by_timeframe) != {item.value for item in Timeframe}:
                raise CorpusManifestError(f"derived timeframe set is invalid for {symbol}")
            for intervals in by_timeframe.values():
                _validate_intervals(intervals)
        expected = _dataset_version_payload(self)
        digest = sha256(_canonical_json(expected).encode()).hexdigest()
        if self.dataset_version != f"weex-v1-5m-last-{digest[:24]}":
            raise CorpusManifestError("dataset version does not match manifest facts")

    def to_json(self) -> str:
        """Serialize with deterministic ordering and Decimal-safe UTC values."""

        self.validate()
        return _canonical_json(_manifest_to_mapping(self))

    @classmethod
    def from_path(cls, path: Path) -> WeexResearchCorpusManifest:
        """Load only the supported manifest schema."""

        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            manifest = _manifest_from_mapping(value)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise CorpusManifestError("manifest cannot be decoded") from error
        manifest.validate()
        return manifest


@dataclass(frozen=True, slots=True)
class WeexCorpusBuildConfiguration:
    """Predeclared V1 qualification boundaries and evidence provenance."""

    output_root: Path
    source_code_commit: str
    built_at: datetime
    canonical_intervals: dict[str, tuple[CorpusInterval, ...]]
    quarantines: dict[str, tuple[CorpusInterval, ...]]
    warmup_exclusions: dict[str, tuple[CorpusInterval, ...]]

    def validate(self) -> None:
        require_utc(self.built_at, field="built_at")
        if not self.source_code_commit.strip():
            raise ValueError("source_code_commit must be non-empty")
        for mapping in (self.canonical_intervals, self.quarantines, self.warmup_exclusions):
            _validate_interval_mapping(mapping)
        if set(self.canonical_intervals) != {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("configuration must contain BTCUSDT and ETHUSDT")


class WeexQualifiedCorpusBuilder:
    """Snapshot accepted 5m intervals and derive complete V1 UTC buckets."""

    def __init__(self, source: WeexHistorySource) -> None:
        self._source = source

    def build(self, configuration: WeexCorpusBuildConfiguration) -> WeexResearchCorpusManifest:
        """Build a fresh immutable directory; partial/dirty output is never accepted."""

        configuration.validate()
        if configuration.output_root.exists() and any(configuration.output_root.iterdir()):
            raise WeexHistoricalCorpusError("output root must be fresh and empty")
        configuration.output_root.mkdir(parents=True, exist_ok=True)
        canonical_by_symbol: dict[str, tuple[CanonicalFiveMinuteCandle, ...]] = {}
        request_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
        retry_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
        duplicate_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
        for symbol in ("BTCUSDT", "ETHUSDT"):
            fetched_rows: list[CanonicalFiveMinuteCandle] = []
            for interval in configuration.canonical_intervals[symbol]:
                result = self._source.fetch_range(symbol, start=interval.start, end=interval.end)
                request_counts[symbol] += result.request_count
                retry_counts[symbol] += result.retry_count
                duplicate_counts[symbol] += result.identical_duplicate_count
                fetched_rows.extend(result.candles)
            ordered = tuple(sorted(fetched_rows, key=lambda item: item.open_time))
            if len({item.open_time for item in ordered}) != len(ordered):
                raise WeexSourceIntegrityError("accepted canonical intervals overlap")
            canonical_by_symbol[symbol] = ordered

        derived_by_symbol: dict[str, dict[Timeframe, tuple[Candle, ...]]] = {}
        derived_intervals: dict[str, dict[str, tuple[CorpusInterval, ...]]] = {}
        source_digest = _canonical_content_digest(
            canonical_by_symbol, configuration.canonical_intervals, configuration.quarantines
        )
        source_id = SourceId(f"weex-v3-last-{source_digest[:20]}")
        derived_store = ParquetHistoricalCandleStore(configuration.output_root / "derived")
        for symbol in ("BTCUSDT", "ETHUSDT"):
            trading_symbol = TradingSymbol("BTC" if symbol == "BTCUSDT" else "ETH", "USDT")
            derived_by_symbol[symbol] = {}
            derived_intervals[symbol] = {}
            for timeframe in Timeframe:
                candles, intervals = _derive_timeframe(
                    canonical_by_symbol[symbol],
                    trading_symbol,
                    timeframe,
                    configuration.canonical_intervals[symbol],
                )
                derived_store.merge(source_id, candles)
                report = _scan_each_interval(
                    derived_store, source_id, trading_symbol, timeframe, intervals
                )
                if not report:
                    raise WeexSourceIntegrityError("derived corpus failed integrity scan")
                derived_by_symbol[symbol][timeframe] = candles
                derived_intervals[symbol][timeframe.value] = intervals

        for symbol, canonical_rows in canonical_by_symbol.items():
            _write_canonical(
                configuration.output_root / "canonical" / f"{symbol}-5m-LAST.parquet",
                canonical_rows,
            )

        artifacts = _artifact_inventory(configuration.output_root)
        provisional = WeexResearchCorpusManifest(
            schema_version=CORPUS_SCHEMA_VERSION,
            dataset_version="weex-v1-5m-last-pending",
            source_id=source_id.value,
            source_endpoint=WEEX_HISTORY_ENDPOINT,
            price_type=PRICE_TYPE,
            canonical_interval=CANONICAL_INTERVAL,
            source_code_commit=configuration.source_code_commit,
            built_at=configuration.built_at,
            btc_status=CorpusQualificationStatus.QUALIFIED,
            eth_status=(
                CorpusQualificationStatus.PARTIAL
                if configuration.quarantines["ETHUSDT"]
                else CorpusQualificationStatus.QUALIFIED
            ),
            source_status="QUALIFIED_WITH_BOUNDARIES",
            canonical_intervals=configuration.canonical_intervals,
            derived_intervals=derived_intervals,
            quarantines=configuration.quarantines,
            warmup_exclusions=configuration.warmup_exclusions,
            source_request_counts=request_counts,
            source_retry_counts=retry_counts,
            identical_duplicate_counts=duplicate_counts,
            artifacts=artifacts,
        )
        digest = sha256(_canonical_json(_dataset_version_payload(provisional)).encode()).hexdigest()
        manifest = replace(
            provisional,
            dataset_version=f"weex-v1-5m-last-{digest[:24]}",
        )
        manifest.validate()
        manifest_path = configuration.output_root / "manifest.json"
        _atomic_write_bytes(manifest_path, (manifest.to_json() + "\n").encode())
        for path in configuration.output_root.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        return manifest


class LockedWeexResearchCorpusStore(HistoricalCandleStore):
    """Hash-verifying, network-free HistoricalCandleStore over one dataset version."""

    def __init__(self, root: Path, *, dataset_version: str) -> None:
        self._root = root
        manifest_path = root / "manifest.json"
        self._manifest = WeexResearchCorpusManifest.from_path(manifest_path)
        if dataset_version != self._manifest.dataset_version:
            raise CorpusManifestError("requested dataset version is unknown")
        accepted_manifest_hash = QUALIFIED_DATASET_MANIFEST_SHA256.get(dataset_version)
        if accepted_manifest_hash is None:
            raise CorpusManifestError("requested dataset version is not project-qualified")
        if _file_sha256(manifest_path) != accepted_manifest_hash:
            raise CorpusHashMismatchError("qualified corpus manifest hash mismatch")
        self._source_id = SourceId(self._manifest.source_id)
        self._store = ParquetHistoricalCandleStore(root / "derived")
        self._verify_artifacts()

    @property
    def source_id(self) -> SourceId:
        """Return the exact version-bound source identity used by Replay."""

        return self._source_id

    @property
    def manifest(self) -> WeexResearchCorpusManifest:
        """Return immutable accepted provenance."""

        return self._manifest

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        """Reject mutation: an accepted corpus is permanently read-only."""

        raise WeexHistoricalCorpusError("locked WEEX corpus is immutable")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        self._validate_request(source_id, symbol, timeframe, start, end)
        self._verify_artifacts()
        assert start is not None and end is not None
        return self._store.read(source_id, symbol, timeframe, start=start, end=end)

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        self._validate_request(source_id, symbol, timeframe, start, end)
        self._verify_artifacts()
        assert start is not None and end is not None
        return self._store.scan_integrity(source_id, symbol, timeframe, start=start, end=end)

    def is_evaluation_excluded(self, symbol: TradingSymbol, observed_at: datetime) -> bool:
        """Expose the manifest's fixed initial/post-quarantine warm-up exclusion."""

        observed_at = require_utc(observed_at, field="observed_at")
        return any(
            item.start < observed_at <= item.end
            for item in self._manifest.warmup_exclusions[_wire_symbol(symbol)]
        )

    def _validate_request(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
    ) -> None:
        if source_id != self._source_id:
            raise CorpusManifestError("requested source identity does not match dataset version")
        if start is None or end is None:
            raise CorpusRangeError("locked corpus reads require an explicit finite range")
        start = require_utc(start, field="start")
        end = require_utc(end, field="end")
        if start > end:
            raise CorpusRangeError("start must not follow end")
        intervals = self._manifest.derived_intervals[_wire_symbol(symbol)][timeframe.value]
        if not any(item.contains(start, end) for item in intervals):
            raise CorpusRangeError("requested range crosses an unqualified corpus boundary")

    def _verify_artifacts(self) -> None:
        self._manifest.validate()
        for artifact in self._manifest.artifacts:
            path = self._root / Path(artifact.path)
            if not path.is_file():
                raise CorpusHashMismatchError(f"locked artifact is missing: {artifact.path}")
            digest = _file_sha256(path)
            if digest != artifact.sha256 or path.stat().st_size != artifact.size_bytes:
                raise CorpusHashMismatchError(f"locked artifact hash mismatch: {artifact.path}")


def _strict_integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value and value.isascii() and value.isdigit():
        return int(value)
    raise TypeError(f"{field} must be an integer without coercion")


def _strict_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise TypeError(f"{field} must be a non-empty exact string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _milliseconds(value: datetime) -> int:
    value = require_utc(value)
    return int(value.timestamp() * 1_000)


def _validate_exact_grid(
    candles: tuple[CanonicalFiveMinuteCandle, ...],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    expected_count = int((end - start) / _FIVE_MINUTES)
    if len(candles) != expected_count:
        raise WeexSourceIntegrityError(
            f"{symbol} grid count mismatch: expected {expected_count}, received {len(candles)}"
        )
    for index, candle in enumerate(candles):
        expected = start + index * _FIVE_MINUTES
        if candle.symbol != symbol or candle.open_time != expected:
            raise WeexSourceIntegrityError(
                f"{symbol} missing/off-grid candle at {expected.isoformat()}"
            )


def _derive_timeframe(
    rows: tuple[CanonicalFiveMinuteCandle, ...],
    symbol: TradingSymbol,
    timeframe: Timeframe,
    accepted_intervals: tuple[CorpusInterval, ...],
) -> tuple[tuple[Candle, ...], tuple[CorpusInterval, ...]]:
    by_open = {item.open_time: item for item in rows}
    candles: list[Candle] = []
    intervals: list[CorpusInterval] = []
    constituent_count = int(timeframe.duration / _FIVE_MINUTES)
    for accepted in accepted_intervals:
        bucket_start = _ceil_timeframe(accepted.start, timeframe)
        interval_rows: list[Candle] = []
        while bucket_start + timeframe.duration <= accepted.end:
            constituents = tuple(
                by_open.get(bucket_start + index * _FIVE_MINUTES)
                for index in range(constituent_count)
            )
            if any(item is None for item in constituents):
                raise WeexSourceIntegrityError(
                    f"derived {timeframe.value} bucket lacks a canonical constituent"
                )
            complete = tuple(item for item in constituents if item is not None)
            interval_rows.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=bucket_start,
                    close_time=bucket_start + timeframe.duration,
                    open=complete[0].open,
                    high=max(item.high for item in complete),
                    low=min(item.low for item in complete),
                    close=complete[-1].close,
                    volume=sum((item.base_volume for item in complete), Decimal(0)),
                )
            )
            bucket_start += timeframe.duration
        if interval_rows:
            candles.extend(interval_rows)
            intervals.append(
                CorpusInterval(interval_rows[0].open_time, interval_rows[-1].close_time)
            )
    return tuple(candles), tuple(intervals)


def _ceil_timeframe(value: datetime, timeframe: Timeframe) -> datetime:
    aligned = align_timestamp(value, timeframe)
    return aligned if aligned == value else aligned + timeframe.duration


def _scan_each_interval(
    store: ParquetHistoricalCandleStore,
    source_id: SourceId,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    intervals: tuple[CorpusInterval, ...],
) -> bool:
    return bool(intervals) and all(
        store.scan_integrity(source_id, symbol, timeframe, start=item.start, end=item.end).is_clean
        for item in intervals
    )


def _write_canonical(path: Path, rows: tuple[CanonicalFiveMinuteCandle, ...]) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "symbol": item.symbol,
                "open_time": item.open_time,
                "close_time": item.close_time,
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "base_volume": item.base_volume,
                "quote_volume": item.quote_volume,
                "trade_count": item.trade_count,
                "taker_buy_base_volume": item.taker_buy_base_volume,
                "taker_buy_quote_volume": item.taker_buy_quote_volume,
            }
            for item in rows
        ],
        schema=_CANONICAL_SCHEMA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        pq.write_table(table, temporary_path, compression="zstd", version="2.6")
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _artifact_inventory(root: Path) -> tuple[CorpusArtifact, ...]:
    artifacts: list[CorpusArtifact] = []
    for path in sorted(root.rglob("*.parquet")):
        relative = path.relative_to(root).as_posix()
        table = pq.ParquetFile(path)
        parts = relative.split("/")
        if relative.startswith("canonical/"):
            symbol = path.name.split("-", maxsplit=1)[0]
            timeframe = "5m"
        else:
            base = next(item.split("=", 1)[1] for item in parts if item.startswith("base="))
            quote = next(item.split("=", 1)[1] for item in parts if item.startswith("quote="))
            symbol = f"{base}{quote}"
            timeframe = next(
                item.split("=", 1)[1] for item in parts if item.startswith("timeframe=")
            )
        artifacts.append(
            CorpusArtifact(
                path=relative,
                sha256=_file_sha256(path),
                size_bytes=path.stat().st_size,
                row_count=table.metadata.num_rows,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return tuple(artifacts)


def _canonical_content_digest(
    rows: dict[str, tuple[CanonicalFiveMinuteCandle, ...]],
    intervals: Mapping[str, tuple[CorpusInterval, ...]],
    quarantines: Mapping[str, tuple[CorpusInterval, ...]],
) -> str:
    digest = sha256()
    digest.update(CANONICAL_SCHEMA_VERSION.encode())
    digest.update(_canonical_json(_interval_mapping_to_json(intervals)).encode())
    digest.update(_canonical_json(_interval_mapping_to_json(quarantines)).encode())
    for symbol in sorted(rows):
        for item in rows[symbol]:
            digest.update(
                _canonical_json(
                    (
                        item.symbol,
                        _iso(item.open_time),
                        _iso(item.close_time),
                        str(item.open),
                        str(item.high),
                        str(item.low),
                        str(item.close),
                        str(item.base_volume),
                        str(item.quote_volume),
                        item.trade_count,
                        str(item.taker_buy_base_volume),
                        str(item.taker_buy_quote_volume),
                    )
                ).encode()
            )
    return digest.hexdigest()


def _dataset_version_payload(manifest: WeexResearchCorpusManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "source_id": manifest.source_id,
        "source_endpoint": manifest.source_endpoint,
        "price_type": manifest.price_type,
        "canonical_interval": manifest.canonical_interval,
        "source_code_commit": manifest.source_code_commit,
        "btc_status": manifest.btc_status.value,
        "eth_status": manifest.eth_status.value,
        "source_status": manifest.source_status,
        "canonical_intervals": _interval_mapping_to_json(manifest.canonical_intervals),
        "derived_intervals": {
            symbol: {
                timeframe: _intervals_to_json(intervals)
                for timeframe, intervals in sorted(by_timeframe.items())
            }
            for symbol, by_timeframe in sorted(manifest.derived_intervals.items())
        },
        "quarantines": _interval_mapping_to_json(manifest.quarantines),
        "warmup_exclusions": _interval_mapping_to_json(manifest.warmup_exclusions),
        "source_request_counts": dict(manifest.source_request_counts),
        "source_retry_counts": dict(manifest.source_retry_counts),
        "identical_duplicate_counts": dict(manifest.identical_duplicate_counts),
        "artifacts": [asdict(artifact) for artifact in manifest.artifacts],
    }


def _manifest_to_mapping(manifest: WeexResearchCorpusManifest) -> dict[str, object]:
    payload = dict(_dataset_version_payload(manifest))
    payload["dataset_version"] = manifest.dataset_version
    payload["built_at"] = _iso(manifest.built_at)
    return payload


def _manifest_from_mapping(value: object) -> WeexResearchCorpusManifest:
    if not isinstance(value, dict):
        raise TypeError("manifest root must be an object")
    artifacts_value = value["artifacts"]
    if not isinstance(artifacts_value, list):
        raise TypeError("artifacts must be a list")
    return WeexResearchCorpusManifest(
        schema_version=str(value["schema_version"]),
        dataset_version=str(value["dataset_version"]),
        source_id=str(value["source_id"]),
        source_endpoint=str(value["source_endpoint"]),
        price_type=str(value["price_type"]),
        canonical_interval=str(value["canonical_interval"]),
        source_code_commit=str(value["source_code_commit"]),
        built_at=_parse_utc(str(value["built_at"])),
        btc_status=CorpusQualificationStatus(str(value["btc_status"])),
        eth_status=CorpusQualificationStatus(str(value["eth_status"])),
        source_status=str(value["source_status"]),
        canonical_intervals=_parse_interval_mapping(value["canonical_intervals"]),
        derived_intervals={
            str(symbol): {
                str(timeframe): _parse_intervals(intervals)
                for timeframe, intervals in _mapping(by_timeframe).items()
            }
            for symbol, by_timeframe in _mapping(value["derived_intervals"]).items()
        },
        quarantines=_parse_interval_mapping(value["quarantines"]),
        warmup_exclusions=_parse_interval_mapping(value["warmup_exclusions"]),
        source_request_counts=_parse_count_mapping(value["source_request_counts"]),
        source_retry_counts=_parse_count_mapping(value["source_retry_counts"]),
        identical_duplicate_counts=_parse_count_mapping(value["identical_duplicate_counts"]),
        artifacts=tuple(_parse_artifact(item) for item in artifacts_value),
    )


def _parse_artifact(value: object) -> CorpusArtifact:
    item = _mapping(value)
    return CorpusArtifact(
        path=str(item["path"]),
        sha256=str(item["sha256"]),
        size_bytes=_json_integer(item["size_bytes"], "artifact size_bytes"),
        row_count=_json_integer(item["row_count"], "artifact row_count"),
        symbol=str(item["symbol"]),
        timeframe=str(item["timeframe"]),
    )


def _json_integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    return value


def _parse_count_mapping(value: object) -> dict[str, int]:
    return {key: _json_integer(item, f"count {key}") for key, item in _mapping(value).items()}


def _validate_interval_mapping(mapping: Mapping[str, tuple[CorpusInterval, ...]]) -> None:
    if set(mapping) != {"BTCUSDT", "ETHUSDT"}:
        raise CorpusManifestError("interval mapping must contain BTCUSDT and ETHUSDT")
    for intervals in mapping.values():
        _validate_intervals(intervals)


def _validate_intervals(intervals: tuple[CorpusInterval, ...]) -> None:
    if not isinstance(intervals, tuple) or intervals != tuple(sorted(intervals)):
        raise CorpusManifestError("intervals must be immutable and canonically ordered")
    if any(previous.end > current.start for previous, current in pairwise(intervals)):
        raise CorpusManifestError("intervals must not overlap")


def _interval_mapping_to_json(
    mapping: Mapping[str, tuple[CorpusInterval, ...]],
) -> dict[str, list[list[str]]]:
    return {symbol: _intervals_to_json(intervals) for symbol, intervals in sorted(mapping.items())}


def _intervals_to_json(intervals: tuple[CorpusInterval, ...]) -> list[list[str]]:
    return [[_iso(item.start), _iso(item.end)] for item in intervals]


def _parse_interval_mapping(value: object) -> dict[str, tuple[CorpusInterval, ...]]:
    return {
        str(symbol): _parse_intervals(intervals) for symbol, intervals in _mapping(value).items()
    }


def _parse_intervals(value: object) -> tuple[CorpusInterval, ...]:
    if not isinstance(value, list):
        raise TypeError("interval collection must be a list")
    parsed: list[CorpusInterval] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise TypeError("interval must be a two-element list")
        parsed.append(CorpusInterval(_parse_utc(str(item[0])), _parse_utc(str(item[1]))))
    return tuple(parsed)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError("expected an object with string keys")
    return value


def _wire_symbol(symbol: TradingSymbol) -> str:
    value = f"{symbol.base}{symbol.quote}"
    if value not in {"BTCUSDT", "ETHUSDT"}:
        raise CorpusRangeError("symbol is outside the locked V1 corpus")
    return value


def _iso(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return require_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_bytes(payload)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def issue_115_configuration(
    output_root: Path, *, source_code_commit: str, built_at: datetime
) -> WeexCorpusBuildConfiguration:
    """Return the fixed evidence-driven Issue #115 corpus policy."""

    start = datetime(2024, 7, 1, tzinfo=UTC)
    end = datetime(2026, 1, 8, tzinfo=UTC)
    quarantine_start = datetime(2025, 1, 1, tzinfo=UTC)
    quarantine_end = datetime(2025, 4, 2, tzinfo=UTC)
    warmup_end = datetime(2025, 4, 10, 8, tzinfo=UTC)
    return WeexCorpusBuildConfiguration(
        output_root=output_root,
        source_code_commit=source_code_commit,
        built_at=built_at,
        canonical_intervals={
            "BTCUSDT": (CorpusInterval(start, end),),
            "ETHUSDT": (
                CorpusInterval(start, quarantine_start),
                CorpusInterval(quarantine_end, end),
            ),
        },
        quarantines={
            "BTCUSDT": (),
            "ETHUSDT": (CorpusInterval(quarantine_start, quarantine_end),),
        },
        warmup_exclusions={
            "BTCUSDT": (CorpusInterval(start, start + timedelta(hours=200)),),
            "ETHUSDT": (
                CorpusInterval(start, start + timedelta(hours=200)),
                CorpusInterval(quarantine_start, warmup_end),
            ),
        },
    )


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Build the locked qualified WEEX V1 research corpus")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-code-commit", required=True)
    parser.add_argument("--built-at", type=_parse_utc, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build Issue #115 artifacts from public data; never trade or call private APIs."""

    arguments = _parser().parse_args(argv)
    configuration = issue_115_configuration(
        arguments.output_root,
        source_code_commit=arguments.source_code_commit,
        built_at=arguments.built_at,
    )
    manifest = WeexQualifiedCorpusBuilder(WeexV3PublicHistorySource()).build(configuration)
    print(manifest.to_json())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
