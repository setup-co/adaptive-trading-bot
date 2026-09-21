"""Qualification and immutable snapshots for WEEX leveraged-research inputs.

This module is build-time/public-data infrastructure only.  It does not implement
leveraged positions, liquidation, funding transfers, execution, or accounting.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import pairwise
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusArtifact,
    CorpusHashMismatchError,
    CorpusInterval,
    CorpusManifestError,
    WeexHistoricalCorpusError,
    WeexSourceIntegrityError,
    WeexSourceRequestError,
)

WEEX_HISTORY_ENDPOINT: Final = "https://api-contract.weex.com/capi/v3/market/historyKlines"
WEEX_FUNDING_ENDPOINT: Final = "https://api-contract.weex.com/capi/v3/market/fundingRate"
LEVERAGED_INPUT_SCHEMA_VERSION: Final = "weex-v1-leveraged-input-v1"
MARK_SCHEMA_VERSION: Final = "weex-futures-v3-mark-5m-v1"
FUNDING_SCHEMA_VERSION: Final = "weex-futures-v3-funding-v1"
_FIVE_MINUTES: Final = timedelta(minutes=5)
_MAX_FUNDING_WINDOW: Final = timedelta(days=7)
_MARK_PAGE_LIMIT: Final = 100
_FUNDING_PAGE_LIMIT: Final = 1000
_DECIMAL_TYPE: Final = pa.decimal128(38, 18)
_TIMESTAMP_TYPE: Final = pa.timestamp("ms", tz="UTC")

_MARK_SCHEMA: Final = pa.schema(
    (
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("open_time", _TIMESTAMP_TYPE, nullable=False),
        pa.field("close_time", _TIMESTAMP_TYPE, nullable=False),
        pa.field("open", _DECIMAL_TYPE, nullable=False),
        pa.field("high", _DECIMAL_TYPE, nullable=False),
        pa.field("low", _DECIMAL_TYPE, nullable=False),
        pa.field("close", _DECIMAL_TYPE, nullable=False),
    )
).with_metadata(
    {
        b"adaptive_trading_bot.schema_version": MARK_SCHEMA_VERSION.encode(),
        b"adaptive_trading_bot.source_endpoint": WEEX_HISTORY_ENDPOINT.encode(),
        b"adaptive_trading_bot.price_type": b"MARK",
    }
)

_FUNDING_SCHEMA: Final = pa.schema(
    (
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("funding_time", _TIMESTAMP_TYPE, nullable=False),
        pa.field("funding_rate", _DECIMAL_TYPE, nullable=False),
        pa.field("mark_price", _DECIMAL_TYPE, nullable=False),
    )
).with_metadata(
    {
        b"adaptive_trading_bot.schema_version": FUNDING_SCHEMA_VERSION.encode(),
        b"adaptive_trading_bot.source_endpoint": WEEX_FUNDING_ENDPOINT.encode(),
    }
)

# Review-owned registry. New accepted bytes require a qualification PR.
QUALIFIED_LEVERAGED_INPUT_MANIFEST_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "weex-v1-5m-mark-97c879a06fc900676dc5c339": (
            "3e5ed9bc81877fa9c1d45fe33145d82f36487afeb5e49e9502ae087ec0749275"
        ),
        "weex-v1-funding-87c1f6e88bd64d96c015fe39": (
            "6ecc0c5717a0ec3e5c0c28a96c542226a48e007ccdda48934e5900d304746c52"
        ),
    }
)


@dataclass(frozen=True, slots=True, order=True)
class MarkFiveMinuteCandle:
    """Lossless MARK OHLC/time projection; additive LAST fields are ignored."""

    symbol: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        _require_symbol(self.symbol)
        open_time = require_utc(self.open_time, field="mark open_time")
        close_time = require_utc(self.close_time, field="mark close_time")
        if _milliseconds(open_time) % 300_000:
            raise WeexSourceIntegrityError("MARK open_time is off the UTC 5m grid")
        if close_time != open_time + _FIVE_MINUTES:
            raise WeexSourceIntegrityError("MARK close_time is not open_time + 5m")
        object.__setattr__(self, "open_time", open_time)
        object.__setattr__(self, "close_time", close_time)
        values = (self.open, self.high, self.low, self.close)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise WeexSourceIntegrityError("MARK OHLC must be finite Decimals")
        if any(value <= 0 for value in values):
            raise WeexSourceIntegrityError("MARK OHLC must be positive")
        if self.high < max(values) or self.low > min(values):
            raise WeexSourceIntegrityError("MARK row violates OHLC semantics")

    @classmethod
    def from_wire_row(cls, symbol: str, row: object) -> MarkFiveMinuteCandle:
        if not isinstance(row, list) or len(row) != 11:
            raise WeexSourceIntegrityError("WEEX MARK row must contain exactly 11 fields")
        try:
            values = tuple(
                _strict_decimal(row[index], f"MARK field {index}") for index in range(1, 5)
            )
            open_ms = _strict_integer(row[0], "MARK open timestamp")
            close_ms = _strict_integer(row[6], "MARK close timestamp")
        except (InvalidOperation, TypeError, ValueError) as error:
            raise WeexSourceIntegrityError(f"malformed MARK row: {error}") from error
        return cls(
            symbol=symbol,
            open_time=_from_milliseconds(open_ms),
            close_time=_from_milliseconds(close_ms),
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
        )


@dataclass(frozen=True, slots=True, order=True)
class FundingEvent:
    """One exact observed WEEX funding settlement event."""

    symbol: str
    funding_time: datetime
    funding_rate: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        _require_symbol(self.symbol)
        funding_time = require_utc(self.funding_time, field="funding_time")
        if funding_time.microsecond or funding_time.minute or funding_time.second:
            raise WeexSourceIntegrityError("funding_time is not aligned to an exact UTC hour")
        object.__setattr__(self, "funding_time", funding_time)
        if not self.funding_rate.is_finite():
            raise WeexSourceIntegrityError("funding_rate must be a finite Decimal")
        if not self.mark_price.is_finite() or self.mark_price <= 0:
            raise WeexSourceIntegrityError("funding mark_price must be a positive Decimal")

    @classmethod
    def from_wire_item(cls, expected_symbol: str, item: object) -> FundingEvent:
        if not isinstance(item, dict) or set(item) != {
            "symbol",
            "fundingRate",
            "fundingTime",
            "markPrice",
        }:
            raise WeexSourceIntegrityError("funding item has an unexpected schema")
        if item["symbol"] != expected_symbol:
            raise WeexSourceIntegrityError("funding item symbol does not match the request")
        try:
            return cls(
                symbol=expected_symbol,
                funding_time=_from_milliseconds(
                    _strict_integer(item["fundingTime"], "fundingTime")
                ),
                funding_rate=_strict_decimal(item["fundingRate"], "fundingRate"),
                mark_price=_strict_decimal(item["markPrice"], "markPrice"),
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            raise WeexSourceIntegrityError(f"malformed funding item: {error}") from error


@dataclass(frozen=True, slots=True)
class SourceFetchResult:
    records: tuple[MarkFiveMinuteCandle | FundingEvent, ...]
    request_count: int
    retry_count: int
    identical_duplicate_count: int


class _PublicJsonSource:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 4,
        minimum_request_interval_seconds: float = 0.11,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or minimum_request_interval_seconds < 0:
            raise ValueError("invalid public-source request policy")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._minimum_request_interval_seconds = minimum_request_interval_seconds
        self._last_request_at = 0.0

    def _request(self, endpoint: str, parameters: dict[str, str]) -> tuple[object, int]:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._minimum_request_interval_seconds:
            time.sleep(self._minimum_request_interval_seconds - elapsed)
        request = urllib.request.Request(
            f"{endpoint}?{urllib.parse.urlencode(parameters)}",
            headers={"User-Agent": "adaptive-trading-bot-leveraged-input-qualification/1"},
        )
        for attempt in range(self._max_retries + 1):
            self._last_request_at = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                    return json.load(response, parse_float=Decimal), attempt
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or 500 <= error.code <= 599
                if not retryable or attempt == self._max_retries:
                    body = error.read().decode(errors="replace")[:500]
                    raise WeexSourceRequestError(f"WEEX HTTP {error.code}: {body}") from error
            except (
                TimeoutError,
                urllib.error.URLError,
                http.client.HTTPException,
                OSError,
            ) as error:
                if attempt == self._max_retries:
                    raise WeexSourceRequestError("WEEX transport request failed") from error
            time.sleep(min(2**attempt, 10))
        raise AssertionError("unreachable retry loop")


class WeexV3MarkHistorySource(_PublicJsonSource):
    """Public-only deterministic WEEX V3 5m MARK traversal."""

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> SourceFetchResult:
        start, end = _validate_aligned_range(symbol, start, end)
        by_open: dict[datetime, MarkFiveMinuteCandle] = {}
        request_count = retry_count = duplicate_count = 0
        current_end = end - _FIVE_MINUTES
        while current_end >= start:
            payload, retries = self._request(
                WEEX_HISTORY_ENDPOINT,
                {
                    "symbol": symbol,
                    "interval": "5m",
                    "priceType": "MARK",
                    "startTime": str(_milliseconds(start)),
                    "endTime": str(_milliseconds(current_end)),
                    "limit": str(_MARK_PAGE_LIMIT),
                },
            )
            request_count += 1
            retry_count += retries
            if not isinstance(payload, list) or not payload:
                raise WeexSourceRequestError(f"empty MARK page before {current_end.isoformat()}")
            page_opens: list[datetime] = []
            for row in payload:
                if not isinstance(row, list) or not row:
                    raise WeexSourceIntegrityError("MARK row has no open timestamp")
                raw_open = _from_milliseconds(_strict_integer(row[0], "MARK open timestamp"))
                if raw_open > current_end:
                    raise WeexSourceRequestError("MARK page exceeded its inclusive end")
                if raw_open < start:
                    continue
                candle = MarkFiveMinuteCandle.from_wire_row(symbol, row)
                page_opens.append(candle.open_time)
                previous = by_open.get(candle.open_time)
                if previous is not None and previous != candle:
                    raise WeexSourceIntegrityError(
                        f"conflicting MARK duplicate at {candle.open_time.isoformat()}"
                    )
                if previous is not None:
                    duplicate_count += 1
                by_open[candle.open_time] = candle
            if not page_opens:
                raise WeexSourceRequestError("MARK page made no in-range backward progress")
            next_end = min(page_opens) - _FIVE_MINUTES
            if next_end >= current_end:
                raise WeexSourceRequestError("MARK pagination failed to move backward")
            current_end = next_end
        records = tuple(by_open[key] for key in sorted(by_open))
        _validate_mark_grid(records, symbol=symbol, start=start, end=end)
        return SourceFetchResult(records, request_count, retry_count, duplicate_count)


class WeexV3FundingHistorySource(_PublicJsonSource):
    """Public-only deterministic WEEX V3 funding traversal in bounded windows."""

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> SourceFetchResult:
        _require_symbol(symbol)
        start = require_utc(start, field="funding start")
        end = require_utc(end, field="funding end")
        if start >= end:
            raise ValueError("funding range must be non-empty")
        by_time: dict[datetime, FundingEvent] = {}
        request_count = retry_count = duplicate_count = 0
        cursor = start
        while cursor < end:
            inclusive_end = min(cursor + _MAX_FUNDING_WINDOW, end)
            payload, retries = self._request(
                WEEX_FUNDING_ENDPOINT,
                {
                    "symbol": symbol,
                    "startTime": str(_milliseconds(cursor)),
                    "endTime": str(_milliseconds(inclusive_end)),
                    "limit": str(_FUNDING_PAGE_LIMIT),
                },
            )
            request_count += 1
            retry_count += retries
            if not isinstance(payload, list) or not payload:
                raise WeexSourceRequestError(
                    f"empty funding window {cursor.isoformat()} to {inclusive_end.isoformat()}"
                )
            for item in payload:
                event = FundingEvent.from_wire_item(symbol, item)
                if not cursor <= event.funding_time <= inclusive_end:
                    raise WeexSourceRequestError("funding response exceeded requested boundaries")
                if event.funding_time >= end:
                    continue
                previous = by_time.get(event.funding_time)
                if previous is not None and previous != event:
                    raise WeexSourceIntegrityError(
                        f"conflicting funding duplicate at {event.funding_time.isoformat()}"
                    )
                if previous is not None:
                    duplicate_count += 1
                by_time[event.funding_time] = event
            cursor = inclusive_end
        records = tuple(by_time[key] for key in sorted(by_time))
        _validate_funding_coverage(records, symbol=symbol, start=start, end=end)
        return SourceFetchResult(records, request_count, retry_count, duplicate_count)


@dataclass(frozen=True, slots=True)
class WeexContractSpecification:
    """Current public V3 contract snapshot; not a historical tier schedule."""

    symbol: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    contract_val: Decimal
    price_precision: int
    quantity_precision: int
    min_order_size: Decimal
    max_order_size: Decimal
    max_position_size: Decimal
    market_open_limit_size: Decimal
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal

    def __post_init__(self) -> None:
        _require_symbol(self.symbol)
        if self.quote_asset != "USDT" or self.margin_asset != "USDT":
            raise ValueError("V1 contract must be USDT quoted and margined")
        decimals = (
            self.contract_val,
            self.min_order_size,
            self.max_order_size,
            self.max_position_size,
            self.market_open_limit_size,
            self.maker_fee_rate,
            self.taker_fee_rate,
        )
        if any(not value.is_finite() or value < 0 for value in decimals):
            raise ValueError("contract decimal facts must be finite and non-negative")
        if self.contract_val <= 0 or self.min_order_size <= 0:
            raise ValueError("contract value and minimum order size must be positive")

    def contracts_for_base_quantity(self, base_quantity: Decimal) -> Decimal:
        """Convert exact V3 base quantity to legacy/help-center contract count."""

        contracts = base_quantity / self.contract_val
        if contracts != contracts.to_integral_value():
            raise ValueError("base quantity is not an exact contractVal multiple")
        return contracts

    def base_quantity_for_contracts(self, contracts: Decimal) -> Decimal:
        if not contracts.is_finite() or contracts < 0 or contracts != contracts.to_integral_value():
            raise ValueError("contract count must be a non-negative integer Decimal")
        return contracts * self.contract_val

    def effective_notional(self, base_quantity: Decimal, price: Decimal) -> Decimal:
        """USDT linear-perpetual notional using canonical base quantity."""

        if not base_quantity.is_finite() or base_quantity < 0:
            raise ValueError("base quantity must be finite and non-negative")
        if not price.is_finite() or price <= 0:
            raise ValueError("price must be finite and positive")
        return base_quantity * price


@dataclass(frozen=True, slots=True)
class LeveragedInputManifest:
    schema_version: str
    dataset_version: str
    input_kind: str
    source_id: str
    source_endpoint: str
    source_code_commit: str
    built_at: datetime
    status: str
    symbol_status: Mapping[str, str]
    required_intervals: Mapping[str, tuple[CorpusInterval, ...]]
    qualified_intervals: Mapping[str, tuple[CorpusInterval, ...]]
    unavailable_intervals: Mapping[str, tuple[CorpusInterval, ...]]
    observed_cadence_seconds: Mapping[str, tuple[int, ...]]
    source_request_counts: Mapping[str, int]
    source_retry_counts: Mapping[str, int]
    identical_duplicate_counts: Mapping[str, int]
    artifacts: tuple[CorpusArtifact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "built_at", require_utc(self.built_at, field="built_at"))
        for name in (
            "symbol_status",
            "required_intervals",
            "qualified_intervals",
            "unavailable_intervals",
            "observed_cadence_seconds",
            "source_request_counts",
            "source_retry_counts",
            "identical_duplicate_counts",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def validate(self) -> None:
        if self.schema_version != LEVERAGED_INPUT_SCHEMA_VERSION:
            raise CorpusManifestError("unknown leveraged-input schema")
        prefixes = {"MARK": "weex-v1-5m-mark-", "FUNDING": "weex-v1-funding-"}
        if self.input_kind not in prefixes or not self.dataset_version.startswith(
            prefixes[self.input_kind]
        ):
            raise CorpusManifestError("dataset kind/version identity is invalid")
        expected_endpoint = (
            WEEX_HISTORY_ENDPOINT if self.input_kind == "MARK" else WEEX_FUNDING_ENDPOINT
        )
        if self.source_endpoint != expected_endpoint or not self.source_code_commit.strip():
            raise CorpusManifestError("source provenance is invalid")
        if set(self.symbol_status) != {"BTCUSDT", "ETHUSDT"}:
            raise CorpusManifestError("symbol status set is invalid")
        expected_status = {
            "MARK": (
                "PARTIAL_INTEGRITY_GAPS",
                {"BTCUSDT": "NOT_QUALIFIED", "ETHUSDT": "PARTIAL"},
            ),
            "FUNDING": (
                "PARTIAL_RETENTION_BOUNDARY",
                {"BTCUSDT": "PARTIAL", "ETHUSDT": "PARTIAL"},
            ),
        }[self.input_kind]
        if self.status != expected_status[0] or dict(self.symbol_status) != expected_status[1]:
            raise CorpusManifestError("qualification status is inconsistent with input kind")
        for interval_mapping in (
            self.required_intervals,
            self.qualified_intervals,
            self.unavailable_intervals,
        ):
            _validate_interval_mapping(interval_mapping)
        if dict(self.required_intervals) != issue_128_required_intervals():
            raise CorpusManifestError(
                "required intervals differ from locked LAST-corpus boundaries"
            )
        for symbol in ("BTCUSDT", "ETHUSDT"):
            _validate_coverage_partition(
                self.required_intervals[symbol],
                self.qualified_intervals[symbol],
                self.unavailable_intervals[symbol],
            )
        for count_mapping in (
            self.source_request_counts,
            self.source_retry_counts,
            self.identical_duplicate_counts,
        ):
            if set(count_mapping) != {"BTCUSDT", "ETHUSDT"} or any(
                type(value) is not int or value < 0 for value in count_mapping.values()
            ):
                raise CorpusManifestError("source count mapping is invalid")
        if set(self.observed_cadence_seconds) != {"BTCUSDT", "ETHUSDT"}:
            raise CorpusManifestError("cadence mapping is invalid")
        if self.input_kind == "MARK" and any(self.observed_cadence_seconds.values()):
            raise CorpusManifestError("MARK manifest cannot claim funding cadence")
        if self.input_kind == "FUNDING" and any(
            cadence not in {14_400, 28_800}
            for cadences in self.observed_cadence_seconds.values()
            for cadence in cadences
        ):
            raise CorpusManifestError("funding cadence is outside official 4h/8h values")
        if not self.artifacts or tuple(self.artifacts) != tuple(
            sorted(self.artifacts, key=lambda item: item.path)
        ):
            raise CorpusManifestError("artifact inventory is empty or unordered")
        for artifact in self.artifacts:
            path = PurePosixPath(artifact.path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not artifact.path.endswith(".parquet")
                or artifact.symbol not in {"BTCUSDT", "ETHUSDT"}
                or len(artifact.sha256) != 64
                or artifact.row_count <= 0
                or artifact.size_bytes <= 0
                or artifact.timeframe != ("5m-MARK" if self.input_kind == "MARK" else "FUNDING")
            ):
                raise CorpusManifestError("artifact entry is invalid")
        expected_artifact_symbols = {
            symbol for symbol, intervals in self.qualified_intervals.items() if intervals
        }
        if {artifact.symbol for artifact in self.artifacts} != expected_artifact_symbols:
            raise CorpusManifestError(
                "artifact inventory must exactly match symbols with qualified intervals"
            )
        digest = sha256(_canonical_json(_manifest_identity(self)).encode()).hexdigest()
        if self.dataset_version != f"{prefixes[self.input_kind]}{digest[:24]}":
            raise CorpusManifestError("dataset version does not match manifest facts")

    def to_json(self) -> str:
        self.validate()
        value = _manifest_identity(self)
        value["dataset_version"] = self.dataset_version
        value["built_at"] = _iso(self.built_at)
        return _canonical_json(value)

    @classmethod
    def from_path(cls, path: Path) -> LeveragedInputManifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls(
                schema_version=str(raw["schema_version"]),
                dataset_version=str(raw["dataset_version"]),
                input_kind=str(raw["input_kind"]),
                source_id=str(raw["source_id"]),
                source_endpoint=str(raw["source_endpoint"]),
                source_code_commit=str(raw["source_code_commit"]),
                built_at=_parse_utc(str(raw["built_at"])),
                status=str(raw["status"]),
                symbol_status={str(key): str(value) for key, value in raw["symbol_status"].items()},
                required_intervals=_parse_intervals_mapping(raw["required_intervals"]),
                qualified_intervals=_parse_intervals_mapping(raw["qualified_intervals"]),
                unavailable_intervals=_parse_intervals_mapping(raw["unavailable_intervals"]),
                observed_cadence_seconds={
                    str(key): tuple(_json_int(item) for item in value)
                    for key, value in raw["observed_cadence_seconds"].items()
                },
                source_request_counts=_parse_int_mapping(raw["source_request_counts"]),
                source_retry_counts=_parse_int_mapping(raw["source_retry_counts"]),
                identical_duplicate_counts=_parse_int_mapping(raw["identical_duplicate_counts"]),
                artifacts=tuple(CorpusArtifact(**item) for item in raw["artifacts"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise CorpusManifestError("leveraged-input manifest cannot be decoded") from error
        manifest.validate()
        return manifest


class LockedWeexLeveragedInputDataset:
    """Network-free, hash-verifying reader for one reviewed input dataset."""

    def __init__(self, root: Path, *, dataset_version: str) -> None:
        self._root = root
        self._manifest = LeveragedInputManifest.from_path(root / "manifest.json")
        if self._manifest.dataset_version != dataset_version:
            raise CorpusManifestError("requested leveraged-input version is unknown")
        accepted = QUALIFIED_LEVERAGED_INPUT_MANIFEST_SHA256.get(dataset_version)
        if accepted is None:
            raise CorpusManifestError("leveraged-input version is not project-qualified")
        if _file_sha256(root / "manifest.json") != accepted:
            raise CorpusHashMismatchError("leveraged-input manifest hash mismatch")
        self._verify_artifacts()

    @property
    def manifest(self) -> LeveragedInputManifest:
        return self._manifest

    def read_symbol(self, symbol: str) -> tuple[MarkFiveMinuteCandle | FundingEvent, ...]:
        _require_symbol(symbol)
        self._verify_artifacts()
        artifact = next((item for item in self._manifest.artifacts if item.symbol == symbol), None)
        if artifact is None:
            raise CorpusManifestError(
                f"{symbol} has no qualified {self._manifest.input_kind} artifact"
            )
        table = pq.read_table(self._root / artifact.path)
        records = table.to_pylist()
        if self._manifest.input_kind == "MARK":
            return tuple(MarkFiveMinuteCandle(**item) for item in records)
        return tuple(FundingEvent(**item) for item in records)

    def _verify_artifacts(self) -> None:
        for artifact in self._manifest.artifacts:
            path = self._root / artifact.path
            if (
                not path.is_file()
                or path.stat().st_size != artifact.size_bytes
                or _file_sha256(path) != artifact.sha256
            ):
                raise CorpusHashMismatchError(f"artifact hash mismatch: {artifact.path}")


def issue_128_required_intervals() -> dict[str, tuple[CorpusInterval, ...]]:
    return {
        "BTCUSDT": (CorpusInterval(_utc("2024-07-01T00:00:00Z"), _utc("2026-01-08T00:00:00Z")),),
        "ETHUSDT": (
            CorpusInterval(_utc("2024-07-01T00:00:00Z"), _utc("2025-01-01T00:00:00Z")),
            CorpusInterval(_utc("2025-04-02T00:00:00Z"), _utc("2026-01-08T00:00:00Z")),
        ),
    }


def issue_128_funding_qualified_intervals() -> dict[str, tuple[CorpusInterval, ...]]:
    supported = CorpusInterval(_utc("2025-09-18T00:00:00Z"), _utc("2026-01-08T00:00:00Z"))
    return {"BTCUSDT": (supported,), "ETHUSDT": (supported,)}


def issue_128_mark_qualified_intervals() -> dict[str, tuple[CorpusInterval, ...]]:
    """Intervals that independently passed the complete Issue #128 MARK grid audit."""

    return {
        "BTCUSDT": (),
        "ETHUSDT": (CorpusInterval(_utc("2025-04-02T00:00:00Z"), _utc("2026-01-08T00:00:00Z")),),
    }


def issue_128_mark_unavailable_intervals() -> dict[str, tuple[CorpusInterval, ...]]:
    """Whole accepted LAST intervals rejected after a missing MARK open was found."""

    return {
        "BTCUSDT": (CorpusInterval(_utc("2024-07-01T00:00:00Z"), _utc("2026-01-08T00:00:00Z")),),
        "ETHUSDT": (CorpusInterval(_utc("2024-07-01T00:00:00Z"), _utc("2025-01-01T00:00:00Z")),),
    }


def issue_128_funding_unavailable_intervals() -> dict[str, tuple[CorpusInterval, ...]]:
    boundary = _utc("2025-09-18T00:00:00Z")
    return {
        "BTCUSDT": (CorpusInterval(_utc("2024-07-01T00:00:00Z"), boundary),),
        "ETHUSDT": (
            CorpusInterval(_utc("2024-07-01T00:00:00Z"), _utc("2025-01-01T00:00:00Z")),
            CorpusInterval(_utc("2025-04-02T00:00:00Z"), boundary),
        ),
    }


def build_mark_dataset(
    output_root: Path,
    *,
    source_code_commit: str,
    built_at: datetime,
    source: WeexV3MarkHistorySource | None = None,
) -> LeveragedInputManifest:
    return _build_dataset(
        output_root,
        input_kind="MARK",
        source=source or WeexV3MarkHistorySource(),
        source_code_commit=source_code_commit,
        built_at=built_at,
        required_intervals=issue_128_required_intervals(),
        qualified_intervals=issue_128_mark_qualified_intervals(),
        unavailable_intervals=issue_128_mark_unavailable_intervals(),
        status="PARTIAL_INTEGRITY_GAPS",
        symbol_status={"BTCUSDT": "NOT_QUALIFIED", "ETHUSDT": "PARTIAL"},
    )


def build_funding_dataset(
    output_root: Path,
    *,
    source_code_commit: str,
    built_at: datetime,
    source: WeexV3FundingHistorySource | None = None,
) -> LeveragedInputManifest:
    return _build_dataset(
        output_root,
        input_kind="FUNDING",
        source=source or WeexV3FundingHistorySource(),
        source_code_commit=source_code_commit,
        built_at=built_at,
        required_intervals=issue_128_required_intervals(),
        qualified_intervals=issue_128_funding_qualified_intervals(),
        unavailable_intervals=issue_128_funding_unavailable_intervals(),
        status="PARTIAL_RETENTION_BOUNDARY",
        symbol_status={"BTCUSDT": "PARTIAL", "ETHUSDT": "PARTIAL"},
    )


def _build_dataset(
    output_root: Path,
    *,
    input_kind: str,
    source: WeexV3MarkHistorySource | WeexV3FundingHistorySource,
    source_code_commit: str,
    built_at: datetime,
    required_intervals: dict[str, tuple[CorpusInterval, ...]],
    qualified_intervals: dict[str, tuple[CorpusInterval, ...]],
    unavailable_intervals: dict[str, tuple[CorpusInterval, ...]],
    status: str,
    symbol_status: dict[str, str],
) -> LeveragedInputManifest:
    require_utc(built_at, field="built_at")
    if not source_code_commit.strip():
        raise ValueError("source_code_commit must be non-empty")
    if output_root.exists() and any(output_root.iterdir()):
        raise WeexHistoricalCorpusError("output root must be fresh and empty")
    output_root.mkdir(parents=True, exist_ok=True)
    request_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
    retry_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
    duplicate_counts = {"BTCUSDT": 0, "ETHUSDT": 0}
    cadence: dict[str, tuple[int, ...]] = {"BTCUSDT": (), "ETHUSDT": ()}
    records_by_symbol: dict[str, tuple[MarkFiveMinuteCandle | FundingEvent, ...]] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        collected: list[MarkFiveMinuteCandle | FundingEvent] = []
        for interval in qualified_intervals[symbol]:
            result = source.fetch_range(symbol, start=interval.start, end=interval.end)
            if input_kind == "MARK":
                mark_records = tuple(
                    item for item in result.records if isinstance(item, MarkFiveMinuteCandle)
                )
                if len(mark_records) != len(result.records):
                    raise WeexSourceIntegrityError("MARK source returned a non-MARK record")
                _validate_mark_grid(
                    mark_records,
                    symbol=symbol,
                    start=interval.start,
                    end=interval.end,
                )
            else:
                funding_records = tuple(
                    item for item in result.records if isinstance(item, FundingEvent)
                )
                if len(funding_records) != len(result.records):
                    raise WeexSourceIntegrityError("funding source returned a non-funding record")
                _validate_funding_coverage(
                    funding_records,
                    symbol=symbol,
                    start=interval.start,
                    end=interval.end,
                )
            collected.extend(result.records)
            request_counts[symbol] += result.request_count
            retry_counts[symbol] += result.retry_count
            duplicate_counts[symbol] += result.identical_duplicate_count
        ordered = tuple(sorted(collected, key=_record_time))
        if len({_record_time(item) for item in ordered}) != len(ordered):
            raise WeexSourceIntegrityError("qualified intervals contain duplicate identities")
        records_by_symbol[symbol] = ordered
        if input_kind == "FUNDING":
            funding_records = tuple(item for item in ordered if isinstance(item, FundingEvent))
            cadence[symbol] = tuple(
                sorted(
                    {
                        int((current.funding_time - previous.funding_time).total_seconds())
                        for previous, current in pairwise(funding_records)
                    }
                )
            )
        if not ordered:
            continue
        path = output_root / f"{symbol}-{'5m-MARK' if input_kind == 'MARK' else 'funding'}.parquet"
        if input_kind == "MARK":
            _write_mark(
                path, tuple(item for item in ordered if isinstance(item, MarkFiveMinuteCandle))
            )
        else:
            _write_funding(path, tuple(item for item in ordered if isinstance(item, FundingEvent)))
    artifacts = _artifact_inventory(output_root, input_kind)
    content = sha256()
    for symbol in sorted(records_by_symbol):
        for item in records_by_symbol[symbol]:
            content.update(repr(item).encode())
    source_id = f"weex-v3-{input_kind.lower()}-{content.hexdigest()[:20]}"
    provisional = LeveragedInputManifest(
        schema_version=LEVERAGED_INPUT_SCHEMA_VERSION,
        dataset_version=(
            "weex-v1-5m-mark-pending" if input_kind == "MARK" else "weex-v1-funding-pending"
        ),
        input_kind=input_kind,
        source_id=source_id,
        source_endpoint=(WEEX_HISTORY_ENDPOINT if input_kind == "MARK" else WEEX_FUNDING_ENDPOINT),
        source_code_commit=source_code_commit,
        built_at=built_at,
        status=status,
        symbol_status=symbol_status,
        required_intervals=required_intervals,
        qualified_intervals=qualified_intervals,
        unavailable_intervals=unavailable_intervals,
        observed_cadence_seconds=cadence,
        source_request_counts=request_counts,
        source_retry_counts=retry_counts,
        identical_duplicate_counts=duplicate_counts,
        artifacts=artifacts,
    )
    digest = sha256(_canonical_json(_manifest_identity(provisional)).encode()).hexdigest()
    prefix = "weex-v1-5m-mark-" if input_kind == "MARK" else "weex-v1-funding-"
    manifest = replace(provisional, dataset_version=f"{prefix}{digest[:24]}")
    manifest.validate()
    _atomic_write(output_root / "manifest.json", (manifest.to_json() + "\n").encode())
    for path in output_root.iterdir():
        if path.is_file():
            path.chmod(0o444)
    return manifest


def _validate_mark_grid(
    records: tuple[MarkFiveMinuteCandle, ...],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    expected = int((end - start) / _FIVE_MINUTES)
    if len(records) != expected:
        actual = {item.open_time for item in records}
        missing = next(
            (
                start + index * _FIVE_MINUTES
                for index in range(expected)
                if start + index * _FIVE_MINUTES not in actual
            ),
            None,
        )
        missing_text = missing.isoformat() if missing else "unknown"
        raise WeexSourceIntegrityError(
            f"{symbol} MARK grid is incomplete; first missing={missing_text}"
        )
    for index, record in enumerate(records):
        if record.symbol != symbol or record.open_time != start + index * _FIVE_MINUTES:
            raise WeexSourceIntegrityError(f"{symbol} MARK grid is misordered or off-grid")


def _validate_funding_coverage(
    records: tuple[FundingEvent, ...],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> None:
    if not records:
        raise WeexSourceIntegrityError(f"{symbol} funding range is empty")
    if records[0].funding_time < start or records[-1].funding_time >= end:
        raise WeexSourceIntegrityError(f"{symbol} funding event is outside the half-open range")
    if records[0].funding_time - start > timedelta(hours=8) or end - records[
        -1
    ].funding_time > timedelta(hours=8):
        raise WeexSourceIntegrityError(f"{symbol} funding boundary coverage exceeds 8h")
    for previous, current in pairwise(records):
        spacing = current.funding_time - previous.funding_time
        if spacing not in {timedelta(hours=4), timedelta(hours=8)}:
            previous_text = previous.funding_time.isoformat()
            raise WeexSourceIntegrityError(
                f"{symbol} unexplained funding spacing {spacing} after {previous_text}"
            )


def _record_time(item: MarkFiveMinuteCandle | FundingEvent) -> datetime:
    if isinstance(item, MarkFiveMinuteCandle):
        return item.open_time
    return item.funding_time


def _validate_aligned_range(
    symbol: str, start: datetime, end: datetime
) -> tuple[datetime, datetime]:
    _require_symbol(symbol)
    start = require_utc(start, field="start")
    end = require_utc(end, field="end")
    if start >= end or _milliseconds(start) % 300_000 or _milliseconds(end) % 300_000:
        raise ValueError("MARK range must be non-empty and aligned to the UTC 5m grid")
    return start, end


def _write_mark(path: Path, records: tuple[MarkFiveMinuteCandle, ...]) -> None:
    table = pa.Table.from_pylist([asdict(item) for item in records], schema=_MARK_SCHEMA)
    pq.write_table(table, path, compression="zstd", version="2.6")


def _write_funding(path: Path, records: tuple[FundingEvent, ...]) -> None:
    table = pa.Table.from_pylist([asdict(item) for item in records], schema=_FUNDING_SCHEMA)
    pq.write_table(table, path, compression="zstd", version="2.6")


def _artifact_inventory(root: Path, input_kind: str) -> tuple[CorpusArtifact, ...]:
    timeframe = "5m-MARK" if input_kind == "MARK" else "FUNDING"
    artifacts: list[CorpusArtifact] = []
    for path in sorted(root.glob("*.parquet")):
        symbol = path.name.split("-", 1)[0]
        artifacts.append(
            CorpusArtifact(
                path=path.name,
                sha256=_file_sha256(path),
                size_bytes=path.stat().st_size,
                row_count=pq.read_metadata(path).num_rows,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return tuple(artifacts)


def _manifest_identity(manifest: LeveragedInputManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "input_kind": manifest.input_kind,
        "source_id": manifest.source_id,
        "source_endpoint": manifest.source_endpoint,
        "source_code_commit": manifest.source_code_commit,
        "status": manifest.status,
        "symbol_status": dict(sorted(manifest.symbol_status.items())),
        "required_intervals": _intervals_mapping_json(manifest.required_intervals),
        "qualified_intervals": _intervals_mapping_json(manifest.qualified_intervals),
        "unavailable_intervals": _intervals_mapping_json(manifest.unavailable_intervals),
        "observed_cadence_seconds": {
            key: list(value) for key, value in sorted(manifest.observed_cadence_seconds.items())
        },
        "source_request_counts": dict(sorted(manifest.source_request_counts.items())),
        "source_retry_counts": dict(sorted(manifest.source_retry_counts.items())),
        "identical_duplicate_counts": dict(sorted(manifest.identical_duplicate_counts.items())),
        "artifacts": [asdict(item) for item in manifest.artifacts],
    }


def _validate_interval_mapping(mapping: Mapping[str, tuple[CorpusInterval, ...]]) -> None:
    if set(mapping) != {"BTCUSDT", "ETHUSDT"}:
        raise CorpusManifestError("interval mapping must contain BTCUSDT and ETHUSDT")
    for intervals in mapping.values():
        if intervals != tuple(sorted(intervals)) or any(
            previous.end > current.start for previous, current in pairwise(intervals)
        ):
            raise CorpusManifestError("intervals must be ordered and non-overlapping")


def _validate_coverage_partition(
    required: tuple[CorpusInterval, ...],
    qualified: tuple[CorpusInterval, ...],
    unavailable: tuple[CorpusInterval, ...],
) -> None:
    boundaries = sorted({item for interval in required for item in (interval.start, interval.end)})
    boundaries.extend(item for interval in qualified for item in (interval.start, interval.end))
    boundaries.extend(item for interval in unavailable for item in (interval.start, interval.end))
    ordered = sorted(set(boundaries))
    for start, end in pairwise(ordered):
        midpoint = start + (end - start) / 2
        required_count = sum(interval.start <= midpoint < interval.end for interval in required)
        covered_count = sum(
            interval.start <= midpoint < interval.end for interval in (*qualified, *unavailable)
        )
        if required_count != covered_count:
            raise CorpusManifestError(
                "qualified/unavailable intervals do not partition requirements"
            )


def _intervals_mapping_json(
    mapping: Mapping[str, tuple[CorpusInterval, ...]],
) -> dict[str, list[list[str]]]:
    return {
        symbol: [[_iso(item.start), _iso(item.end)] for item in intervals]
        for symbol, intervals in sorted(mapping.items())
    }


def _parse_intervals_mapping(value: object) -> dict[str, tuple[CorpusInterval, ...]]:
    if not isinstance(value, dict):
        raise TypeError("interval mapping must be an object")
    return {
        str(symbol): tuple(
            CorpusInterval(_parse_utc(item[0]), _parse_utc(item[1])) for item in intervals
        )
        for symbol, intervals in value.items()
    }


def _parse_int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError("count mapping must be an object")
    return {str(key): _json_int(item) for key, item in value.items()}


def _strict_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"{field} must be an exact decimal token")
    if isinstance(value, str) and (not value or value.strip() != value):
        raise ValueError(f"{field} is blank or padded")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _strict_integer(value: object, field: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise TypeError(f"{field} must be an integer token")


def _json_int(value: object) -> int:
    if type(value) is not int:
        raise TypeError("JSON value must be an integer")
    return value


def _require_symbol(symbol: str) -> None:
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("symbol is outside the V1 allow-list")


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _from_milliseconds(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _utc(value: str) -> datetime:
    return _parse_utc(value)


def _parse_utc(value: str) -> datetime:
    return require_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), field="timestamp")


def _iso(value: datetime) -> str:
    return require_utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description="Build Issue #128 WEEX MARK/funding snapshots")
    parser.add_argument("--mark-output-root", type=Path, required=True)
    parser.add_argument("--funding-output-root", type=Path, required=True)
    parser.add_argument("--source-code-commit", required=True)
    parser.add_argument("--built-at", required=True)
    arguments = parser.parse_args(argv)
    built_at = _parse_utc(arguments.built_at)
    mark = build_mark_dataset(
        arguments.mark_output_root,
        source_code_commit=arguments.source_code_commit,
        built_at=built_at,
    )
    funding = build_funding_dataset(
        arguments.funding_output_root,
        source_code_commit=arguments.source_code_commit,
        built_at=built_at,
    )
    print(
        _canonical_json(
            {"mark": json.loads(mark.to_json()), "funding": json.loads(funding.to_json())}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
