"""Read-only Grid history binding and inert one-shot preflight.

This module deliberately has no historical execution function.  It validates
the accepted v3 Parquet authority, exposes bounded week-sized inputs, and
models calendar/ledger continuity without constructing a market-data Grid
engine.  Only explicit synthetic fixtures may be handed to ``GridV1Engine``.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from itertools import pairwise
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_ENGINE_VERSION,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
    GRID_PROTOCOL_VERSION,
    GRID_STRATEGY_ID,
    GridCompletionStatus,
    GridEpisodeReport,
    GridFundingEvent,
    GridLedgerCarry,
    GridMinute,
    GridWeekDisposition,
    verified_grid_contract_identity,
)

ACCEPTED_DATASET_VERSION = "binance-usdm-btc-eth-2020-01_2026-10-v1"
ACCEPTED_QUALIFICATION_PROTOCOL = "BTC_ETH_GRID_4H_EXTERNAL_HISTORY_QUALIFICATION_V3"
ACCEPTED_ROW_SCHEMA = "binance-usdm-research-row-v1"
ACCEPTED_V3_MANIFEST_SHA256 = "9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214"
ACCEPTED_V3_REPORT_SHA256 = "22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d"
ACCEPTED_CLASSIFICATION = "PRICE_CORPUS_PARTIAL_RECONCILIATION"
ACCEPTED_CALENDAR_SHA256 = "977b21c4aa342cd3d7391a86a84e2c26ca7afb065fe65259df909652db2933f1"
STUDY_PLAN_SHA256 = "a598c41bcd9775f42e2faa46cb88170563a17ed110ab0e3612850cd2303ffd6c"
PREPARATION_ENGINE_HEAD = "dc450a4a3a0f2dfe114a690459971bc79d2aba68"
STUDY_ID = "BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1_HISTORY_STUDY"
STUDY_PLAN_VERSION = "0.1.0-preflight"

_MINUTE = timedelta(minutes=1)
_FOUR_HOURS = timedelta(hours=4)
_WARMUP = timedelta(days=7)
_EPISODE_LAST_MINUTE_OFFSET = timedelta(days=6, hours=20, minutes=1)
_EPISODE_END_OFFSET = _EPISODE_LAST_MINUTE_OFFSET + _MINUTE
_COMPONENTS = ("trade_price_1m", "mark_1m", "derived_4h", "funding")
_SYMBOLS = ("BTCUSDT", "ETHUSDT")
_RETURN_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class GridHistoryPreflightError(RuntimeError):
    """The frozen input or preflight contract is invalid."""


class CalendarStatus(StrEnum):
    METADATA_ELIGIBLE = "METADATA_ELIGIBLE"
    METADATA_INELIGIBLE = "METADATA_INELIGIBLE"


class CalendarTransition(StrEnum):
    ELIGIBLE_RESULT = "ELIGIBLE_RESULT"
    INELIGIBLE_CARRY_ONLY = "INELIGIBLE_CARRY_ONLY"
    INTEGRITY_BLOCKED = "INTEGRITY_BLOCKED"


class CalendarAuthorityKind(StrEnum):
    ACCEPTED_V3 = "ACCEPTED_V3"
    SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"


@dataclass(frozen=True, slots=True)
class V3SourceIdentity:
    manifest_sha256: str
    report_sha256: str
    dataset_version: str
    qualification_protocol: str
    classification: str
    row_schema: str = ACCEPTED_ROW_SCHEMA


ACCEPTED_V3_IDENTITY = V3SourceIdentity(
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_CLASSIFICATION,
)


@dataclass(frozen=True, slots=True)
class CalendarEpisode:
    symbol: str
    episode_start: datetime
    status: CalendarStatus
    partition: str
    warmup_start: datetime
    required_data_end_exclusive: datetime
    reason_codes: tuple[str, ...]
    flag_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.symbol not in _SYMBOLS:
            raise GridHistoryPreflightError("calendar symbol is outside the fixed panel")
        for field in ("episode_start", "warmup_start", "required_data_end_exclusive"):
            value = getattr(self, field)
            if value.tzinfo != UTC:
                raise GridHistoryPreflightError(f"{field} must be UTC")
        if self.episode_start.weekday() != 0 or self.episode_start.time() != datetime.min.time():
            raise GridHistoryPreflightError("calendar episode must start Monday 00:00 UTC")
        if self.warmup_start != self.episode_start - _WARMUP:
            raise GridHistoryPreflightError("calendar warm-up boundary differs")
        if self.required_data_end_exclusive != self.episode_start + _EPISODE_END_OFFSET:
            raise GridHistoryPreflightError("calendar episode boundary differs")
        if self.status is CalendarStatus.METADATA_ELIGIBLE and self.reason_codes:
            raise GridHistoryPreflightError("eligible calendar row has exclusion reasons")


@dataclass(frozen=True, slots=True)
class GridHistoryWeek:
    source_identity: V3SourceIdentity
    calendar: CalendarEpisode
    completed_4h_closes: tuple[Decimal, ...]
    minutes: tuple[GridMinute, ...]
    source_file_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LedgerWeekRecord:
    sequence: int
    symbol: str
    scenario: str
    episode_start: str
    partition: str
    calendar_status: str
    transition: str
    included_in_weekly_statistics: bool
    weekly_return: str | None
    reason_codes: tuple[str, ...]
    carry_in_sha256: str
    carry_out_sha256: str | None
    engine_report_sha256: str | None
    completion_status: str | None
    week_disposition: str | None
    no_trade_reason: str | None


class VerifiedCalendar:
    """Exact calendar authority; fixtures require an explicit separate boundary."""

    __factory_token = object()

    def __init__(
        self,
        rows: tuple[CalendarEpisode, ...],
        *,
        kind: CalendarAuthorityKind,
        identity: str,
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not VerifiedCalendar.__factory_token:
            raise GridHistoryPreflightError(
                "calendar authority must be established by a verified factory"
            )
        if not identity:
            raise GridHistoryPreflightError("calendar authority identity is required")
        by_key: dict[tuple[str, datetime], CalendarEpisode] = {}
        by_symbol: dict[str, list[CalendarEpisode]] = {symbol: [] for symbol in _SYMBOLS}
        for row in rows:
            key = (row.symbol, row.episode_start)
            if key in by_key:
                raise GridHistoryPreflightError("calendar authority contains a duplicate week")
            by_key[key] = row
            by_symbol[row.symbol].append(row)
        for symbol_rows in by_symbol.values():
            symbol_rows.sort(key=lambda item: item.episode_start)
            if any(
                right.episode_start != left.episode_start + timedelta(days=7)
                for left, right in pairwise(symbol_rows)
            ):
                raise GridHistoryPreflightError("calendar authority weeks are not contiguous")
        if kind is CalendarAuthorityKind.ACCEPTED_V3:
            if identity != ACCEPTED_CALENDAR_SHA256 or len(rows) != 712:
                raise GridHistoryPreflightError("accepted calendar authority identity differs")
            if any(len(by_symbol[symbol]) != 356 for symbol in _SYMBOLS):
                raise GridHistoryPreflightError(
                    "accepted calendar must contain 356 weeks per symbol"
                )
        elif not identity.startswith("synthetic-fixture-"):
            raise GridHistoryPreflightError("synthetic calendar identity must be fixture-only")
        self.rows = rows
        self.kind = kind
        self.identity = identity
        self._by_key = by_key
        self._by_symbol = {key: tuple(value) for key, value in by_symbol.items()}

    @classmethod
    def accepted(cls, path: Path) -> VerifiedCalendar:
        return cls(
            load_calendar(path),
            kind=CalendarAuthorityKind.ACCEPTED_V3,
            identity=ACCEPTED_CALENDAR_SHA256,
            _factory_token=cls.__factory_token,
        )

    @classmethod
    def synthetic(cls, rows: tuple[CalendarEpisode, ...], *, fixture_id: str) -> VerifiedCalendar:
        return cls(
            rows,
            kind=CalendarAuthorityKind.SYNTHETIC_FIXTURE,
            identity=f"synthetic-fixture-{fixture_id}",
            _factory_token=cls.__factory_token,
        )

    def require(self, row: CalendarEpisode) -> CalendarEpisode:
        expected = self._by_key.get((row.symbol, row.episode_start))
        if expected is None or expected != row:
            raise GridHistoryPreflightError("calendar row differs from its exact authority")
        return expected

    def rows_for(self, symbol: str) -> tuple[CalendarEpisode, ...]:
        return self._by_symbol[symbol]


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _carry_payload(carry: GridLedgerCarry) -> dict[str, object]:
    payload = asdict(carry)
    return {
        key: value.isoformat()
        if isinstance(value, datetime)
        else str(value)
        if isinstance(value, (Decimal, TradingSymbol))
        else value
        for key, value in payload.items()
    }


def _carry_sha256(carry: GridLedgerCarry) -> str:
    return hashlib.sha256(canonical_json(_carry_payload(carry))).hexdigest()


def calendar_episode(value: dict[str, object]) -> CalendarEpisode:
    """Parse one exact accepted calendar row without reading market values."""

    try:
        return CalendarEpisode(
            symbol=str(value["symbol"]),
            episode_start=_utc(str(value["episode_start"])),
            status=CalendarStatus(str(value["status"])),
            partition=str(value["partition"]),
            warmup_start=_utc(str(value["warmup_start"])),
            required_data_end_exclusive=_utc(str(value["required_data_end_exclusive"])),
            reason_codes=_text_tuple(value["reason_codes"], "reason_codes"),
            flag_codes=_text_tuple(value["flag_codes"], "flag_codes"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GridHistoryPreflightError("calendar row is invalid") from exc


def load_calendar(
    path: Path, *, expected_sha256: str = ACCEPTED_CALENDAR_SHA256
) -> tuple[CalendarEpisode, ...]:
    if sha256_file(path) != expected_sha256:
        raise GridHistoryPreflightError("eligibility calendar hash differs")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("schema") != (
        "btc-eth-grid-4h-v1-metadata-eligibility-calendar"
    ):
        raise GridHistoryPreflightError("eligibility calendar schema differs")
    if (
        value.get("protocol_id") != GRID_STRATEGY_ID
        or value.get("protocol_version") != GRID_PROTOCOL_VERSION
        or value.get("v3_manifest_sha256") != ACCEPTED_V3_MANIFEST_SHA256
        or value.get("v3_report_sha256") != ACCEPTED_V3_REPORT_SHA256
    ):
        raise GridHistoryPreflightError("eligibility calendar identity differs")
    rows = value.get("episodes")
    if not isinstance(rows, list) or len(rows) != 712:
        raise GridHistoryPreflightError("eligibility calendar must contain 712 rows")
    parsed = tuple(calendar_episode(row) for row in rows if isinstance(row, dict))
    if len(parsed) != len(rows):
        raise GridHistoryPreflightError("eligibility calendar contains a non-object row")
    if len({(row.symbol, row.episode_start) for row in parsed}) != len(parsed):
        raise GridHistoryPreflightError("eligibility calendar identity is duplicated")
    return parsed


class GridCalendarLedger:
    """Minimal chronological symbol/scenario carry contract for a future runner."""

    def __init__(
        self,
        symbol: TradingSymbol,
        scenario: str,
        *,
        calendar: VerifiedCalendar,
        expected_source_id: str,
        expected_supplied_code_sha: str,
    ) -> None:
        if scenario not in {"BASE", "STRESS"}:
            raise GridHistoryPreflightError("ledger scenario must be BASE or STRESS")
        if not expected_source_id or not expected_supplied_code_sha:
            raise GridHistoryPreflightError("ledger execution identities are required")
        self.symbol = symbol
        self.scenario = scenario
        self.calendar = calendar
        self.expected_source_id = expected_source_id
        self.expected_supplied_code_sha = expected_supplied_code_sha
        self._calendar_rows = calendar.rows_for(str(symbol).replace("/", ""))
        if not self._calendar_rows:
            raise GridHistoryPreflightError("calendar has no rows for ledger symbol")
        self.carry = GridLedgerCarry.initial(symbol)
        self.records: list[LedgerWeekRecord] = []
        self.blocked = False

    def _check_row(self, row: CalendarEpisode) -> None:
        if self.blocked:
            raise GridHistoryPreflightError("integrity-blocked ledger cannot advance")
        if row.symbol != str(self.symbol).replace("/", ""):
            raise GridHistoryPreflightError("calendar/ledger symbol differs")
        self.calendar.require(row)
        if (
            len(self.records) >= len(self._calendar_rows)
            or row != self._calendar_rows[len(self.records)]
        ):
            raise GridHistoryPreflightError("calendar/ledger row is not the next authorized week")

    def advance_ineligible(self, row: CalendarEpisode) -> LedgerWeekRecord:
        self._check_row(row)
        if row.status is not CalendarStatus.METADATA_INELIGIBLE:
            raise GridHistoryPreflightError("only an ineligible row may skip an episode")
        carry_in = self.carry
        self.carry = replace(carry_in, previous_episode_start=row.episode_start)
        record = LedgerWeekRecord(
            sequence=len(self.records) + 1,
            symbol=row.symbol,
            scenario=self.scenario,
            episode_start=row.episode_start.isoformat(),
            partition=row.partition,
            calendar_status=row.status.value,
            transition=CalendarTransition.INELIGIBLE_CARRY_ONLY.value,
            included_in_weekly_statistics=False,
            weekly_return=None,
            reason_codes=row.reason_codes,
            carry_in_sha256=_carry_sha256(carry_in),
            carry_out_sha256=_carry_sha256(self.carry),
            engine_report_sha256=None,
            completion_status=None,
            week_disposition=None,
            no_trade_reason=None,
        )
        self.records.append(record)
        return record

    def advance_eligible(
        self,
        row: CalendarEpisode,
        *,
        report: GridEpisodeReport,
        carry_out: GridLedgerCarry,
    ) -> LedgerWeekRecord:
        self._check_row(row)
        if row.status is not CalendarStatus.METADATA_ELIGIBLE:
            raise GridHistoryPreflightError("eligible result cannot bind an excluded row")
        if not isinstance(report, GridEpisodeReport):
            raise GridHistoryPreflightError("eligible result must be a typed Grid engine report")
        expected_contract = {
            key: str(value) for key, value in asdict(verified_grid_contract_identity()).items()
        }
        expected_report_identity = (
            report.schema
            == (
                "btc-eth-grid-v1-synthetic-report"
                if self.expected_source_id.startswith("synthetic-")
                else "btc-eth-grid-v1-historical-report"
            )
            and report.strategy_id == GRID_STRATEGY_ID
            and report.protocol_version == GRID_PROTOCOL_VERSION
            and report.engine_version == GRID_ENGINE_VERSION
            and report.supplied_code_sha == self.expected_supplied_code_sha
            and report.verified_contract == expected_contract
            and report.source_id == self.expected_source_id
            and report.symbol == str(self.symbol)
            and report.scenario == self.scenario
            and report.episode_start == row.episode_start.isoformat()
        )
        if not expected_report_identity:
            raise GridHistoryPreflightError("Grid engine report execution identity differs")
        if report.completion_status != GridCompletionStatus.COMPLETE.value:
            raise GridHistoryPreflightError("Grid engine report is not complete")
        try:
            disposition = GridWeekDisposition(report.week_disposition)
        except ValueError as exc:
            raise GridHistoryPreflightError("Grid engine report disposition is invalid") from exc
        if (
            (disposition is GridWeekDisposition.GRID_ACTIVE and report.no_trade_reason is not None)
            or (
                disposition is GridWeekDisposition.NO_TRADE_CASH_ONLY
                and report.no_trade_reason is None
            )
            or (
                disposition is GridWeekDisposition.TERMINATED_CASH_ONLY
                and report.no_trade_reason is not None
            )
        ):
            raise GridHistoryPreflightError("Grid engine report disposition/reason differs")
        carry_in = self.carry
        if report.carry_in != _carry_payload(carry_in):
            raise GridHistoryPreflightError("Grid engine report carry-in differs")
        if report.carry_out != _carry_payload(carry_out):
            raise GridHistoryPreflightError("Grid engine report carry-out differs")
        if carry_out.symbol != self.symbol:
            raise GridHistoryPreflightError("eligible carry-out has the wrong symbol")
        if carry_out.previous_episode_start != row.episode_start:
            raise GridHistoryPreflightError("eligible carry-out has the wrong week identity")
        with localcontext(_RETURN_CONTEXT):
            expected_return = (
                carry_out.current_equity - carry_in.current_equity
            ) / carry_in.current_equity
        self.carry = carry_out
        record = LedgerWeekRecord(
            sequence=len(self.records) + 1,
            symbol=row.symbol,
            scenario=self.scenario,
            episode_start=row.episode_start.isoformat(),
            partition=row.partition,
            calendar_status=row.status.value,
            transition=CalendarTransition.ELIGIBLE_RESULT.value,
            included_in_weekly_statistics=True,
            weekly_return=str(expected_return),
            reason_codes=(),
            carry_in_sha256=_carry_sha256(carry_in),
            carry_out_sha256=_carry_sha256(carry_out),
            engine_report_sha256=report.sha256,
            completion_status=report.completion_status,
            week_disposition=disposition.value,
            no_trade_reason=report.no_trade_reason,
        )
        self.records.append(record)
        return record


class V3GridHistoryAdapter:
    """Bounded read-only adapter for an accepted or fixture v3 corpus root."""

    def __init__(
        self,
        root: Path,
        identity: V3SourceIdentity = ACCEPTED_V3_IDENTITY,
        *,
        calendar: VerifiedCalendar,
    ) -> None:
        if identity == ACCEPTED_V3_IDENTITY:
            if calendar.kind is not CalendarAuthorityKind.ACCEPTED_V3:
                raise GridHistoryPreflightError("accepted corpus requires the accepted calendar")
        elif calendar.kind is not CalendarAuthorityKind.SYNTHETIC_FIXTURE:
            raise GridHistoryPreflightError("fixture corpus requires a fixture-only calendar")
        self.root = root.resolve(strict=True)
        self.identity = identity
        self.calendar = calendar
        manifest_path = self.root / "qualification-manifest-v3.json"
        report_path = self.root / "publication-report-v3.json"
        if sha256_file(manifest_path) != identity.manifest_sha256:
            raise GridHistoryPreflightError("v3 manifest hash differs")
        if sha256_file(report_path) != identity.report_sha256:
            raise GridHistoryPreflightError("v3 report hash differs")
        manifest = json.loads(manifest_path.read_bytes())
        report = json.loads(report_path.read_bytes())
        if not isinstance(manifest, dict) or not isinstance(report, dict):
            raise GridHistoryPreflightError("v3 authorities must be JSON objects")
        if (
            manifest.get("schema") != "binance-usdm-qualification-manifest-v3"
            or manifest.get("generation") != "v3"
            or manifest.get("dataset_version") != identity.dataset_version
            or manifest.get("protocol") != identity.qualification_protocol
            or manifest.get("classification") != identity.classification
        ):
            raise GridHistoryPreflightError("v3 manifest identity differs")
        if report.get("classification") != identity.classification:
            raise GridHistoryPreflightError("v3 report classification differs")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise GridHistoryPreflightError("v3 file inventory is unavailable")
        self._files: dict[str, dict[str, object]] = {}
        for key, value in files.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                raise GridHistoryPreflightError("v3 file inventory row is invalid")
            self._files[key] = value
        self._source_preflight_complete = False

    def preflight_sources(self) -> tuple[int, int]:
        """Validate every study-relevant source hash/header before value access."""

        validated = 0
        bytes_validated = 0
        for relative, authority in sorted(self._files.items()):
            if not any(f"/{component}/" in f"/{relative}" for component in _COMPONENTS):
                continue
            path = self.root / relative
            if not path.is_file() or sha256_file(path) != authority.get("sha256"):
                raise GridHistoryPreflightError(f"retained source file differs: {relative}")
            metadata = pq.read_metadata(path)
            custom = metadata.metadata or {}
            if (
                custom.get(b"schema") != self.identity.row_schema.encode()
                or custom.get(b"dataset_version") != self.identity.dataset_version.encode()
                or custom.get(b"qualification_protocol")
                != self.identity.qualification_protocol.encode()
            ):
                raise GridHistoryPreflightError(f"retained source header differs: {relative}")
            expected_rows = authority.get("rows")
            if not isinstance(expected_rows, int) or metadata.num_rows != expected_rows:
                raise GridHistoryPreflightError(f"retained source row count differs: {relative}")
            validated += 1
            bytes_validated += path.stat().st_size
        self._source_preflight_complete = True
        return validated, bytes_validated

    def _validated_table(self, relative: str, columns: list[str]) -> pa.Table:
        authority = self._files.get(relative)
        if authority is None:
            raise GridHistoryPreflightError(f"source file is absent from v3 manifest: {relative}")
        path = self.root / relative
        if not path.is_file():
            raise GridHistoryPreflightError(f"source file is unavailable: {relative}")
        expected_hash = authority.get("sha256")
        if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            raise GridHistoryPreflightError(f"source file hash differs: {relative}")
        metadata = pq.read_metadata(path)
        expected_rows = authority.get("rows")
        if not isinstance(expected_rows, int) or metadata.num_rows != expected_rows:
            raise GridHistoryPreflightError(f"source file row count differs: {relative}")
        custom = metadata.metadata or {}
        expected = {
            b"schema": self.identity.row_schema.encode(),
            b"dataset_version": self.identity.dataset_version.encode(),
            b"qualification_protocol": self.identity.qualification_protocol.encode(),
        }
        if any(custom.get(key) != value for key, value in expected.items()):
            raise GridHistoryPreflightError(f"source Parquet provenance differs: {relative}")
        return pq.read_table(path, columns=columns)

    @staticmethod
    def _months(start: datetime, end_exclusive: datetime) -> tuple[str, ...]:
        cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
        months: list[str] = []
        while cursor < end_exclusive:
            months.append(cursor.strftime("%Y-%m"))
            cursor = (
                datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
                if cursor.month == 12
                else datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
            )
        return tuple(months)

    def _rows(
        self,
        component: str,
        symbol: str,
        start: datetime,
        end_exclusive: datetime,
        columns: list[str],
    ) -> tuple[list[dict[str, object]], tuple[tuple[str, str], ...]]:
        if component not in _COMPONENTS or symbol not in _SYMBOLS:
            raise GridHistoryPreflightError("component or symbol is outside the frozen adapter")
        rows: list[dict[str, object]] = []
        identities: list[tuple[str, str]] = []
        for month in self._months(start, end_exclusive):
            relative = f"derived-generations/v3/normalized/{component}/{symbol}/{month}.parquet"
            table = self._validated_table(relative, columns)
            rows.extend(table.to_pylist())
            file_hash = self._files[relative].get("sha256")
            assert isinstance(file_hash, str)
            identities.append((relative, file_hash))
        return rows, tuple(identities)

    def load_week(self, row: CalendarEpisode) -> GridHistoryWeek:
        """Read exactly one eligible week after validating every source file hash."""

        row = self.calendar.require(row)
        if row.status is not CalendarStatus.METADATA_ELIGIBLE:
            raise GridHistoryPreflightError("market rows may not be read for an excluded week")
        if not self._source_preflight_complete:
            raise GridHistoryPreflightError("source-wide hash/header preflight is required")
        start = row.episode_start
        end = row.required_data_end_exclusive
        closes_rows, close_ids = self._rows(
            "derived_4h",
            row.symbol,
            row.warmup_start,
            start + _FOUR_HOURS,
            ["open_time_ms", "close_time_ms", "close", "source_minutes", "available_at_ms"],
        )
        start_ms = _milliseconds(start)
        warmup_ms = _milliseconds(row.warmup_start)
        closes_rows = [
            item
            for item in closes_rows
            if warmup_ms < _integer(item, "available_at_ms") <= start_ms
        ]
        closes_rows.sort(key=lambda item: _integer(item, "available_at_ms"))
        if len(closes_rows) != 42:
            raise GridHistoryPreflightError("week does not have exactly 42 causal completed closes")
        for index, item in enumerate(closes_rows):
            if _integer(item, "source_minutes") != 240:
                raise GridHistoryPreflightError("derived 4h bar is not based on 240 minutes")
            expected_available = start_ms - (41 - index) * 4 * 60 * 60 * 1000
            if _integer(item, "available_at_ms") != expected_available:
                raise GridHistoryPreflightError("derived 4h warm-up chronology differs")

        minute_columns = [
            "open_time_ms",
            "open",
            "high",
            "low",
            "close",
            "close_time_ms",
        ]
        trade_rows, trade_ids = self._rows("trade_price_1m", row.symbol, start, end, minute_columns)
        mark_rows, mark_ids = self._rows(
            "mark_1m", row.symbol, start - _MINUTE, end, minute_columns
        )
        funding_rows, funding_ids = self._rows(
            "funding",
            row.symbol,
            start,
            end,
            ["calc_time_ms", "funding_interval_hours", "last_funding_rate"],
        )
        start_ms = _milliseconds(start)
        end_ms = _milliseconds(end)
        trade = _minute_rows(trade_rows, start_ms, end_ms, "trade")
        mark = _minute_rows(mark_rows, start_ms - 60_000, end_ms, "MARK", allow_prefix=True)
        required_starts = tuple(range(start_ms, end_ms, 60_000))
        if tuple(trade) != required_starts or any(value not in mark for value in required_starts):
            raise GridHistoryPreflightError("required TRADE/MARK minute grid is incomplete")

        funding_by_minute: dict[int, list[GridFundingEvent]] = defaultdict(list)
        previous_funding_ms: int | None = None
        for item in sorted(funding_rows, key=lambda value: _integer(value, "calc_time_ms")):
            observed_ms = _integer(item, "calc_time_ms")
            if observed_ms < start_ms or observed_ms >= end_ms:
                continue
            if previous_funding_ms is not None and observed_ms <= previous_funding_ms:
                raise GridHistoryPreflightError("funding timestamps are not unique and increasing")
            previous_funding_ms = observed_ms
            if _integer(item, "funding_interval_hours") <= 0:
                raise GridHistoryPreflightError("funding interval is invalid")
            rate = _decimal(item, "last_funding_rate")
            completed_mark_start = ((observed_ms // 60_000) * 60_000) - 60_000
            mark_item = mark.get(completed_mark_start)
            if mark_item is None:
                raise GridHistoryPreflightError("funding lacks a completed MARK authority")
            event = GridFundingEvent(
                f"{row.symbol}:{observed_ms}",
                _from_milliseconds(observed_ms),
                rate,
                _from_milliseconds(_integer(mark_item, "close_time_ms")),
                _decimal(mark_item, "close"),
            )
            funding_by_minute[(observed_ms // 60_000) * 60_000].append(event)

        minutes: list[GridMinute] = []
        for observed_ms in required_starts:
            trade_item = trade[observed_ms]
            mark_item = mark[observed_ms]
            if _integer(trade_item, "close_time_ms") != observed_ms + 60_000:
                raise GridHistoryPreflightError("TRADE close boundary differs")
            if _integer(mark_item, "close_time_ms") != observed_ms + 60_000:
                raise GridHistoryPreflightError("MARK close boundary differs")
            minutes.append(
                GridMinute(
                    _from_milliseconds(observed_ms),
                    _decimal(trade_item, "open"),
                    _decimal(trade_item, "high"),
                    _decimal(trade_item, "low"),
                    _decimal(trade_item, "close"),
                    _from_milliseconds(observed_ms + 60_000),
                    _decimal(mark_item, "close"),
                    tuple(funding_by_minute[observed_ms]),
                )
            )
        identities = tuple(sorted(set(close_ids + trade_ids + mark_ids + funding_ids)))
        return GridHistoryWeek(
            self.identity,
            row,
            tuple(_decimal(item, "close") for item in closes_rows),
            tuple(minutes),
            identities,
        )


def metadata_only_preflight(repo: Path, data_root: Path | None = None) -> dict[str, object]:
    """Validate locked identities and, optionally, retained file headers/hashes only."""

    repo = repo.resolve(strict=True)
    calendar_path = repo / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    plan_path = repo / "docs/research/btc_eth_grid_4h_v1_history_study_plan.json"
    if sha256_file(plan_path) != STUDY_PLAN_SHA256:
        raise GridHistoryPreflightError("history study plan hash differs")
    plan = json.loads(plan_path.read_bytes())
    if (
        not isinstance(plan, dict)
        or plan.get("study_id") != STUDY_ID
        or plan.get("plan_version") != STUDY_PLAN_VERSION
        or plan.get("historical_execution") != "NOT_AUTHORIZED"
        or plan.get("status") != "LOCKED_PROPOSED_NOT_AUTHORIZED"
    ):
        raise GridHistoryPreflightError("history study plan identity differs")
    calendar = VerifiedCalendar.accepted(calendar_path)
    rows = calendar.rows
    counts = {
        symbol: {
            status.value: sum(1 for row in rows if row.symbol == symbol and row.status is status)
            for status in CalendarStatus
        }
        for symbol in _SYMBOLS
    }
    result: dict[str, object] = {
        "schema": "btc-eth-grid-4h-v1-history-preflight-v1",
        "status": "DATA_ONLY_PREFLIGHT_STUDY_NOT_AUTHORIZED",
        "historical_execution": "NOT_AUTHORIZED",
        "engine_head": PREPARATION_ENGINE_HEAD,
        "engine_version": GRID_ENGINE_VERSION,
        "protocol_head": GRID_ACCEPTED_PROTOCOL_HEAD,
        "protocol_document_sha256": GRID_PROTOCOL_DOCUMENT_SHA256,
        "parameters_sha256": GRID_PARAMETERS_SHA256,
        "calendar_sha256": ACCEPTED_CALENDAR_SHA256,
        "study_plan_sha256": STUDY_PLAN_SHA256,
        "v3_manifest_sha256": ACCEPTED_V3_MANIFEST_SHA256,
        "v3_report_sha256": ACCEPTED_V3_REPORT_SHA256,
        "calendar_counts": counts,
        "price_rows_read": 0,
        "strategy_evaluations": 0,
        "historical_pnl_calculated": False,
        "retained_root": None,
        "validated_source_files": 0,
    }
    if data_root is None:
        return result
    adapter = V3GridHistoryAdapter(data_root, calendar=calendar)
    validated, bytes_validated = adapter.preflight_sources()
    result.update(
        {
            "retained_root": str(adapter.root),
            "validated_source_files": validated,
            "validated_source_bytes": bytes_validated,
        }
    )
    return result


def refuse_historical_execution() -> None:
    """Permanent Issue #245 safeguard: no grant means no historical executor."""

    raise GridHistoryPreflightError(
        "historical Grid execution is NOT_AUTHORIZED; a separate exact-SHA grant is required"
    )


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise GridHistoryPreflightError("timestamp must use UTC")
    return parsed


def _text_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GridHistoryPreflightError(f"{field} must be a text list")
    return tuple(value)


def _milliseconds(value: datetime) -> int:
    if value.tzinfo != UTC:
        raise GridHistoryPreflightError("timestamp must use UTC")
    return int(value.timestamp() * 1000)


def _from_milliseconds(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _integer(item: dict[str, object], key: str) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GridHistoryPreflightError(f"{key} must be an integer")
    return value


def _decimal(item: dict[str, object], key: str) -> Decimal:
    value = item.get(key)
    if not isinstance(value, str):
        raise GridHistoryPreflightError(f"{key} must preserve Decimal text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise GridHistoryPreflightError(f"{key} is not Decimal text") from exc
    if not parsed.is_finite():
        raise GridHistoryPreflightError(f"{key} must be finite")
    return parsed


def _minute_rows(
    rows: list[dict[str, object]],
    start_ms: int,
    end_ms: int,
    label: str,
    *,
    allow_prefix: bool = False,
) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for item in rows:
        observed = _integer(item, "open_time_ms")
        if observed < start_ms or observed >= end_ms:
            continue
        if observed % 60_000:
            raise GridHistoryPreflightError(f"{label} minute is off-grid")
        if observed in result:
            raise GridHistoryPreflightError(f"{label} minute is duplicated")
        for key in ("open", "high", "low", "close"):
            _decimal(item, key)
        result[observed] = item
    if not allow_prefix and result and min(result) != start_ms:
        raise GridHistoryPreflightError(f"{label} minute prefix is missing")
    return dict(sorted(result.items()))


__all__ = [
    "ACCEPTED_CALENDAR_SHA256",
    "ACCEPTED_V3_IDENTITY",
    "STUDY_ID",
    "STUDY_PLAN_SHA256",
    "STUDY_PLAN_VERSION",
    "CalendarAuthorityKind",
    "CalendarEpisode",
    "CalendarStatus",
    "GridCalendarLedger",
    "GridHistoryPreflightError",
    "GridHistoryWeek",
    "LedgerWeekRecord",
    "V3GridHistoryAdapter",
    "V3SourceIdentity",
    "VerifiedCalendar",
    "calendar_episode",
    "canonical_json",
    "load_calendar",
    "metadata_only_preflight",
    "refuse_historical_execution",
    "sha256_file",
]
