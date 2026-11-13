"""Bounded Binance USD-M archive acquisition for the BTC/ETH 4h Grid route.

This is a research-data boundary only.  It deliberately does not expose a
runtime candle store or strategy interface.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

PROTOCOL = "BTC_ETH_GRID_4H_EXTERNAL_HISTORY_DATA_V1"
PLAN_SCHEMA = "binance-usdm-monthly-plan-v1"
ROW_SCHEMA = "binance-usdm-research-row-v1"
DATASET_VERSION = "binance-usdm-btc-eth-2020-01_2026-10-v1"
BASE_URL = "https://data.binance.vision/data/futures/um/monthly"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
COMPONENTS = ("trade_price_1m", "native_4h", "funding", "mark_1m")
START = datetime(2020, 1, 1, tzinfo=UTC)
END = datetime(2026, 11, 1, tzinfo=UTC)
MONTHS = tuple(f"{year:04d}-{month:02d}" for year in range(2020, 2027) for month in range(1, 13))[
    :82
]
EXPECTED_RESOURCES = 1_312
MAX_ATTEMPTS = 3
MAX_ARCHIVE_ATTEMPTS = 3_936
MAX_PAYLOAD_BYTES = 5 * 1024**3
MAX_WORKERS = 2
EXPECTED_ONE_MINUTE = 3_594_240
EXPECTED_FOUR_HOUR = 14_976
USER_AGENT = "adaptive-trading-bot-issue-239/1"


class CorpusError(RuntimeError):
    """The frozen acquisition or qualification contract was violated."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode()


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def durable_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    if pending.exists():
        raise CorpusError(f"ambiguous pending write: {pending}")
    with pending.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise CorpusError(f"JSON object required: {path}")
    return value


def _component_path(component: str, symbol: str, month: str) -> str:
    if component == "trade_price_1m":
        return f"klines/{symbol}/1m/{symbol}-1m-{month}.zip"
    if component == "native_4h":
        return f"klines/{symbol}/4h/{symbol}-4h-{month}.zip"
    if component == "mark_1m":
        return f"markPriceKlines/{symbol}/1m/{symbol}-1m-{month}.zip"
    if component == "funding":
        return f"fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
    raise CorpusError(f"unknown component: {component}")


def make_plan(executable_sha: str) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for month in MONTHS:
            for component in COMPONENTS:
                archive_path = _component_path(component, symbol, month)
                stem = f"{symbol}-{month}-{component}"
                for kind, suffix in (("zip", ""), ("checksum", ".CHECKSUM")):
                    relative = archive_path + suffix
                    resources.append(
                        {
                            "id": f"{stem}-{kind}",
                            "symbol": symbol,
                            "month": month,
                            "component": component,
                            "kind": kind,
                            "url": f"{BASE_URL}/{relative}",
                            "relative_path": relative,
                        }
                    )
    plan = {
        "schema": PLAN_SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "executable_sha": executable_sha,
        "interval": {"start": START.isoformat(), "end": END.isoformat()},
        "symbols": list(SYMBOLS),
        "components": list(COMPONENTS),
        "months": list(MONTHS),
        "retry_policy": {
            "max_attempts_per_resource": MAX_ATTEMPTS,
            "retryable": ["transport", "HTTP_429", "HTTP_5XX"],
            "workers": MAX_WORKERS,
            "payload_cap_bytes": MAX_PAYLOAD_BYTES,
            "maximum_planned_attempts": MAX_ARCHIVE_ATTEMPTS,
        },
        "resources": resources,
    }
    validate_plan(plan, executable_sha)
    return plan


def validate_plan(plan: dict[str, Any], executable_sha: str | None = None) -> None:
    if plan.get("schema") != PLAN_SCHEMA or plan.get("protocol") != PROTOCOL:
        raise CorpusError("plan identity differs")
    if executable_sha is not None and plan.get("executable_sha") != executable_sha:
        raise CorpusError("plan executable differs")
    if plan.get("symbols") != list(SYMBOLS) or plan.get("components") != list(COMPONENTS):
        raise CorpusError("plan scope differs")
    if plan.get("months") != list(MONTHS):
        raise CorpusError("plan month inventory differs")
    resources = plan.get("resources")
    if not isinstance(resources, list) or len(resources) != EXPECTED_RESOURCES:
        raise CorpusError("plan must contain exactly 1,312 resources")
    ids: set[str] = set()
    urls: set[str] = set()
    for item in resources:
        if item["id"] in ids or item["url"] in urls:
            raise CorpusError("duplicate planned resource")
        ids.add(item["id"])
        urls.add(item["url"])
        expected = BASE_URL + "/" + item["relative_path"]
        if item["url"] != expected or not item["url"].startswith(BASE_URL + "/"):
            raise CorpusError("resource URL outside frozen host/path")
    policy = plan.get("retry_policy")
    if policy != {
        "max_attempts_per_resource": 3,
        "retryable": ["transport", "HTTP_429", "HTTP_5XX"],
        "workers": 2,
        "payload_cap_bytes": MAX_PAYLOAD_BYTES,
        "maximum_planned_attempts": MAX_ARCHIVE_ATTEMPTS,
    }:
        raise CorpusError("retry policy differs")


class Response(Protocol):
    status: int
    headers: Any

    def read(self, size: int = -1) -> bytes: ...
    def __enter__(self) -> Response: ...
    def __exit__(self, *args: Any) -> None: ...


class Transport(Protocol):
    def open(self, url: str, timeout: float) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class PublicTransport:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def open(self, url: str, timeout: float) -> Response:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
        return self._opener.open(request, timeout=timeout)  # type: ignore[no-any-return]


@dataclass
class _PayloadBudget:
    used: int
    lock: Lock

    def add(self, count: int) -> None:
        with self.lock:
            if self.used + count > MAX_PAYLOAD_BYTES:
                raise CorpusError("network payload cap exceeded")
            self.used += count


def _existing_payload_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in (root / "raw").rglob("*") if path.is_file())


def _attempts(root: Path, resource_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    directory = root / "ledger" / resource_id
    if not directory.exists():
        return result
    for path in sorted(directory.glob("attempt-*.json")):
        result.append(read_json(path))
    return result


def _success(root: Path, item: dict[str, Any]) -> bool:
    attempts = _attempts(root, item["id"])
    if any(row.get("status") == "STARTED" for row in attempts):
        raise CorpusError(f"ambiguous STARTED attempt: {item['id']}")
    successful = [row for row in attempts if row.get("outcome") == "HTTP_OK"]
    if len(successful) > 1:
        raise CorpusError(f"duplicate successful attempt: {item['id']}")
    if not successful:
        return False
    path = root / successful[0]["payload_path"]
    if not path.is_file() or path.stat().st_size != successful[0]["bytes"]:
        raise CorpusError(f"successful payload missing: {item['id']}")
    if sha256_file(path) != successful[0]["sha256"]:
        raise CorpusError(f"successful payload changed: {item['id']}")
    return True


def _record_attempt(root: Path, item: dict[str, Any], attempt: int, record: dict[str, Any]) -> None:
    durable_new(root / "ledger" / item["id"] / f"attempt-{attempt}.json", canonical(record))


def _download_once(
    root: Path,
    item: dict[str, Any],
    attempt: int,
    transport: Transport,
    budget: _PayloadBudget,
) -> dict[str, Any]:
    started = datetime.now(UTC).isoformat()
    record: dict[str, Any] = {
        "resource_id": item["id"],
        "attempt": attempt,
        "url": item["url"],
        "started_at": started,
        "status": "FINISHED",
    }
    target = root / "raw" / item["relative_path"]
    pending = target.with_suffix(target.suffix + f".attempt-{attempt}.part")
    if target.exists() or pending.exists():
        raise CorpusError(f"ambiguous or unrecorded payload: {item['id']}")
    try:
        try:
            response = transport.open(item["url"], timeout=30.0)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retryable = status == 429 or 500 <= status <= 599
            record.update(
                outcome=f"HTTP_{status}",
                http_status=status,
                retryable=retryable,
                retry_after=exc.headers.get("Retry-After"),
            )
            return record
        with response:
            status = int(response.status)
            if status != 200:
                record.update(
                    outcome=f"HTTP_{status}",
                    http_status=status,
                    retryable=status == 429 or 500 <= status <= 599,
                    retry_after=response.headers.get("Retry-After"),
                )
                return record
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            count = 0
            with pending.open("xb") as handle:
                while block := response.read(1024 * 1024):
                    budget.add(len(block))
                    handle.write(block)
                    digest.update(block)
                    count += len(block)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, target)
            record.update(
                outcome="HTTP_OK",
                http_status=200,
                retryable=False,
                bytes=count,
                sha256=digest.hexdigest(),
                payload_path=target.relative_to(root).as_posix(),
            )
            return record
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
        if pending.exists():
            pending.unlink()
        record.update(
            outcome="TRANSPORT_ERROR",
            retryable=True,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return record
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()


def _capture_resource(
    root: Path,
    item: dict[str, Any],
    transport: Transport,
    budget: _PayloadBudget,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    if _success(root, item):
        return {"id": item["id"], "outcome": "REUSED_SUCCESS"}
    prior = _attempts(root, item["id"])
    if len(prior) >= MAX_ATTEMPTS or (prior and not prior[-1].get("retryable", False)):
        return {"id": item["id"], "outcome": "EXHAUSTED_OR_PERMANENT"}
    for attempt in range(len(prior), MAX_ATTEMPTS):
        record = _download_once(root, item, attempt, transport, budget)
        _record_attempt(root, item, attempt, record)
        if record["outcome"] == "HTTP_OK":
            return {"id": item["id"], "outcome": "HTTP_OK"}
        status = record.get("http_status")
        if status in {401, 403, 451} or (isinstance(status, int) and 300 <= status < 400):
            raise CorpusError(f"global access/redirect stop: {item['id']} {record['outcome']}")
        if not record.get("retryable"):
            return {"id": item["id"], "outcome": record["outcome"]}
        retry_after = record.get("retry_after")
        delay = (
            min(float(retry_after), 30.0)
            if retry_after and str(retry_after).isdigit()
            else 2**attempt
        )
        sleep(delay)
    return {"id": item["id"], "outcome": "ATTEMPTS_EXHAUSTED"}


def capture(
    root: Path,
    plan: dict[str, Any],
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_plan(plan)
    if (root / "capture-complete.json").exists():
        return read_json(root / "capture-complete.json")
    budget = _PayloadBudget(_existing_payload_bytes(root), Lock())
    client = transport or PublicTransport()
    outcomes: Counter[str] = Counter()
    resources = [item for item in plan["resources"] if not _success(root, item)]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_capture_resource, root, item, client, budget, sleep): item
            for item in resources
        }
        for future in as_completed(futures):
            result = future.result()
            outcomes[result["outcome"]] += 1
    state = audit_capture(root, plan)
    result = {
        "status": "CAPTURE_COMPLETE",
        "finished_at": datetime.now(UTC).isoformat(),
        "payload_bytes": budget.used,
        "outcomes_this_invocation": dict(sorted(outcomes.items())),
        **state,
    }
    durable_new(root / "capture-complete.json", canonical(result))
    return result


def audit_capture(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan)
    counts: Counter[str] = Counter()
    attempts = 0
    for item in plan["resources"]:
        rows = _attempts(root, item["id"])
        attempts += len(rows)
        if any(row.get("status") == "STARTED" for row in rows):
            raise CorpusError(f"ambiguous attempt: {item['id']}")
        if _success(root, item):
            counts["successful"] += 1
        elif not rows:
            counts["unattempted"] += 1
        elif rows[-1].get("retryable") and len(rows) < MAX_ATTEMPTS:
            counts["retryable_remaining"] += 1
        else:
            counts["failed_or_exhausted"] += 1
    if attempts > MAX_ARCHIVE_ATTEMPTS:
        raise CorpusError("attempt budget exceeded")
    return {"resources": dict(sorted(counts.items())), "attempts": attempts}


def _official_checksum(body: bytes, expected_name: str) -> str:
    text = body.decode("ascii", errors="strict").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", text)
    if not match or match.group(2) != expected_name:
        raise CorpusError("official checksum record malformed or names another file")
    return match.group(1).lower()


def _pair_paths(root: Path, component: str, symbol: str, month: str) -> tuple[Path, Path]:
    relative = _component_path(component, symbol, month)
    return root / "raw" / relative, root / "raw" / (relative + ".CHECKSUM")


KLINE_NAMES = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "wire_close_time_ms",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def _decimal(text: str, *, positive: bool = False, nonnegative: bool = True) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise CorpusError(f"invalid decimal: {text!r}") from exc
    if (
        not value.is_finite()
        or (positive and value <= 0)
        or (not positive and nonnegative and value < 0)
    ):
        raise CorpusError(f"decimal outside bounds: {text!r}")
    return value


def _zip_csv_rows(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) != 1:
            raise CorpusError("archive must contain exactly one CSV")
        info = infos[0]
        if (
            info.is_dir()
            or Path(info.filename).name != info.filename
            or not info.filename.endswith(".csv")
        ):
            raise CorpusError("unsafe archive member")
        with archive.open(info) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
            yield from csv.reader(text)


def parse_klines(path: Path, interval_ms: int) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for ordinal, row in enumerate(_zip_csv_rows(path)):
        if ordinal == 0 and row and not row[0].strip().isdigit():
            continue
        if len(row) < 12:
            raise CorpusError("kline row has fewer than 12 fields")
        open_ms = int(row[0])
        if open_ms in seen:
            raise CorpusError("duplicate kline open")
        seen.add(open_ms)
        if open_ms % interval_ms:
            raise CorpusError("kline open is off-grid")
        wire_close = int(row[6])
        if wire_close not in {open_ms + interval_ms - 1, open_ms + interval_ms}:
            raise CorpusError("unexpected wire close timestamp")
        open_value = _decimal(row[1], positive=True)
        high = _decimal(row[2], positive=True)
        low = _decimal(row[3], positive=True)
        close = _decimal(row[4], positive=True)
        if high < max(open_value, close) or low > min(open_value, close) or low > high:
            raise CorpusError("OHLC relationship is invalid")
        for index in (5, 7, 9, 10):
            _decimal(row[index])
        trade_count = int(row[8])
        if trade_count < 0:
            raise CorpusError("negative trade count")
        parsed.append(
            {
                **dict(zip(KLINE_NAMES, row[:12], strict=True)),
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + interval_ms,
                "wire_close_time_ms": wire_close,
                "trade_count": trade_count,
            }
        )
    return parsed


def parse_funding(path: Path) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[int] = set()
    header: list[str] | None = None
    for ordinal, row in enumerate(_zip_csv_rows(path)):
        if ordinal == 0 and row and not row[0].strip().isdigit():
            header = [item.strip() for item in row]
            continue
        if len(row) < 3:
            raise CorpusError("funding row has fewer than three fields")
        timestamp = int(row[0])
        if timestamp in seen:
            raise CorpusError("duplicate funding event")
        seen.add(timestamp)
        interval = int(row[1])
        if interval <= 0:
            raise CorpusError("funding interval is not positive")
        _decimal(row[2], nonnegative=False)
        parsed.append(
            {
                "calc_time_ms": timestamp,
                "funding_interval_hours": interval,
                "last_funding_rate": row[2],
                "source_header": header or [],
            }
        )
    return parsed


def _write_parquet(path: Path, rows: list[dict[str, Any]], metadata: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".parquet.pending")
    if path.exists() or pending.exists():
        raise CorpusError(f"normalized partition already exists: {path}")
    table = pa.Table.from_pylist(rows)
    table = table.replace_schema_metadata(
        {key.encode(): value.encode() for key, value in metadata.items()}
    )
    pq.write_table(table, pending, compression="zstd", version="2.6")
    os.replace(pending, path)


def _volume_equal(derived: Decimal, native_text: str) -> bool:
    native = Decimal(native_text)
    exponent = native.as_tuple().exponent
    if not isinstance(exponent, int):
        raise CorpusError("native volume is not finite")
    quantum = Decimal(1).scaleb(exponent)
    return derived.quantize(quantum) == native


def derive_and_reconcile(
    minute: list[dict[str, Any]], native: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_open = {int(row["open_time_ms"]): row for row in native}
    derived: list[dict[str, Any]] = []
    for offset in range(0, len(minute), 240):
        group = minute[offset : offset + 240]
        if len(group) != 240:
            continue
        start = int(group[0]["open_time_ms"])
        if start % 14_400_000 or any(
            int(row["open_time_ms"]) != start + index * 60_000 for index, row in enumerate(group)
        ):
            continue
        expected = by_open.get(start)
        if expected is None:
            raise CorpusError("complete derived 4h bucket absent from native archive")
        open_text = str(group[0]["open"])
        close_text = str(group[-1]["close"])
        high = max(Decimal(str(row["high"])) for row in group)
        low = min(Decimal(str(row["low"])) for row in group)
        if (
            Decimal(open_text) != Decimal(str(expected["open"]))
            or high != Decimal(str(expected["high"]))
            or low != Decimal(str(expected["low"]))
            or Decimal(close_text) != Decimal(str(expected["close"]))
        ):
            raise CorpusError("derived/native 4h OHLC mismatch")
        base = sum((Decimal(str(row["volume"])) for row in group), start=Decimal(0))
        quote = sum((Decimal(str(row["quote_volume"])) for row in group), start=Decimal(0))
        if not _volume_equal(base, str(expected["volume"])) or not _volume_equal(
            quote, str(expected["quote_volume"])
        ):
            raise CorpusError("derived/native 4h volume mismatch beyond native lexical precision")
        derived.append(
            {
                "open_time_ms": start,
                "close_time_ms": start + 14_400_000,
                "open": open_text,
                "high": str(high),
                "low": str(low),
                "close": close_text,
                "volume": str(base),
                "quote_volume": str(quote),
                "source_minutes": 240,
            }
        )
    return derived


def _month_bounds(month: str) -> tuple[int, int]:
    year, number = map(int, month.split("-"))
    start = datetime(year, number, 1, tzinfo=UTC)
    end = datetime(year + (number == 12), 1 if number == 12 else number + 1, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _pair_valid(root: Path, component: str, symbol: str, month: str) -> tuple[Path, str]:
    archive, checksum = _pair_paths(root, component, symbol, month)
    if not archive.is_file() or not checksum.is_file():
        raise CorpusError("resource pair unavailable")
    expected = _official_checksum(checksum.read_bytes(), archive.name)
    actual = sha256_file(archive)
    if actual != expected:
        raise CorpusError("official checksum mismatch")
    return archive, actual


def qualify(
    root: Path,
    plan: dict[str, Any],
    *,
    generation: str = "v1",
    qualification_executable_sha: str | None = None,
) -> dict[str, Any]:
    validate_plan(plan)
    if generation not in {"v1", "v2"}:
        raise CorpusError("unknown qualification generation")
    manifest_path = root / (
        "qualification-manifest.json" if generation == "v1" else "qualification-manifest-v2.json"
    )
    if manifest_path.exists():
        raise CorpusError("qualification already exists")
    output_prefix = (
        Path("normalized") if generation == "v1" else Path("derived-generations/v2/normalized")
    )
    predecessor_sha: str | None = None
    if generation == "v2":
        predecessor = root / "qualification-manifest.json"
        if not predecessor.is_file():
            raise CorpusError("v2 requires the preserved v1 qualification manifest")
        predecessor_sha = sha256_file(predecessor)
        if not qualification_executable_sha:
            raise CorpusError("v2 requires its executable identity")
    coverage: dict[str, dict[str, Any]] = {}
    files: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        coverage[symbol] = {}
        parsed_by_month: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for component in COMPONENTS:
            component_rows = 0
            qualified_months = 0
            first: int | None = None
            last: int | None = None
            failures: list[dict[str, str]] = []
            intervals: Counter[int] = Counter()
            for month in MONTHS:
                try:
                    archive, archive_sha = _pair_valid(root, component, symbol, month)
                    rows = (
                        parse_funding(archive)
                        if component == "funding"
                        else parse_klines(
                            archive, 14_400_000 if component == "native_4h" else 60_000
                        )
                    )
                    start_ms, end_ms = _month_bounds(month)
                    key = "calc_time_ms" if component == "funding" else "open_time_ms"
                    if any(not start_ms <= int(row[key]) < end_ms for row in rows):
                        raise CorpusError("row outside named month")
                    parsed_by_month.setdefault(month, {})[component] = rows
                    relative = output_prefix / component / symbol / f"{month}.parquet"
                    _write_parquet(
                        root / relative,
                        rows,
                        {
                            "schema": ROW_SCHEMA,
                            "dataset_version": DATASET_VERSION,
                            "component": component,
                            "symbol": symbol,
                            "month": month,
                            "raw_sha256": archive_sha,
                        },
                    )
                    files[relative.as_posix()] = {
                        "sha256": sha256_file(root / relative),
                        "bytes": (root / relative).stat().st_size,
                        "rows": len(rows),
                        "raw_sha256": archive_sha,
                    }
                    component_rows += len(rows)
                    qualified_months += 1
                    if rows:
                        first = (
                            int(rows[0][key]) if first is None else min(first, int(rows[0][key]))
                        )
                        last = int(rows[-1][key]) if last is None else max(last, int(rows[-1][key]))
                    if component == "funding":
                        intervals.update(int(row["funding_interval_hours"]) for row in rows)
                except (CorpusError, OSError, zipfile.BadZipFile, ValueError) as exc:
                    failures.append({"month": month, "error": str(exc)})
            expected = (
                EXPECTED_ONE_MINUTE
                if component in {"trade_price_1m", "mark_1m"}
                else EXPECTED_FOUR_HOUR
                if component == "native_4h"
                else None
            )
            status = (
                "QUALIFIED"
                if qualified_months == len(MONTHS)
                and (expected is None or component_rows == expected)
                else "PARTIAL_OR_FAILED"
            )
            coverage[symbol][component] = {
                "status": status,
                "planned_months": len(MONTHS),
                "qualified_months": qualified_months,
                "rows": component_rows,
                "expected_rows": expected,
                "first_timestamp_ms": first,
                "last_timestamp_ms": last,
                "funding_interval_hours": dict(sorted(intervals.items())),
                "failures": failures,
            }
        derived_rows = 0
        derived_months = 0
        derived_failures: list[dict[str, str]] = []
        for month in MONTHS:
            try:
                parsed = parsed_by_month[month]
                rows = derive_and_reconcile(parsed["trade_price_1m"], parsed["native_4h"])
                relative = output_prefix / "derived_4h" / symbol / f"{month}.parquet"
                _write_parquet(
                    root / relative,
                    rows,
                    {
                        "schema": ROW_SCHEMA,
                        "dataset_version": DATASET_VERSION,
                        "component": "derived_4h",
                        "symbol": symbol,
                        "month": month,
                        "derivation": "240-complete-aligned-trade-price-1m",
                    },
                )
                files[relative.as_posix()] = {
                    "sha256": sha256_file(root / relative),
                    "bytes": (root / relative).stat().st_size,
                    "rows": len(rows),
                }
                derived_rows += len(rows)
                derived_months += 1
            except (CorpusError, KeyError, OSError) as exc:
                derived_failures.append({"month": month, "error": str(exc)})
        coverage[symbol]["derived_4h"] = {
            "status": "QUALIFIED"
            if derived_months == len(MONTHS) and derived_rows == EXPECTED_FOUR_HOUR
            else "PARTIAL_OR_FAILED",
            "planned_months": len(MONTHS),
            "qualified_months": derived_months,
            "rows": derived_rows,
            "expected_rows": EXPECTED_FOUR_HOUR,
            "failures": derived_failures,
        }
    price_qualified = all(
        coverage[symbol][component]["status"] == "QUALIFIED"
        for symbol in SYMBOLS
        for component in ("trade_price_1m", "native_4h", "derived_4h")
    )
    manifest = {
        "schema": "binance-usdm-qualification-manifest-v1",
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "generation": generation,
        "capture_executable_sha": plan["executable_sha"],
        "qualification_executable_sha": qualification_executable_sha or plan["executable_sha"],
        "predecessor_manifest_sha256": predecessor_sha,
        "plan_sha256": sha256_file(root / "plan.json"),
        "created_at": datetime.now(UTC).isoformat(),
        "classification": "PRICE_CORPUS_QUALIFIED" if price_qualified else "PRICE_CORPUS_PARTIAL",
        "component_coverage": coverage,
        "files": files,
        "interpretation": {
            "external_price_with_weex_cost_overlay_only": True,
            "binance_mark_and_funding_are_not_weex_history": True,
            "strategy_evaluation_performed": False,
            "prior_market_period_exposure": "SELECTION_INFORMED_NOT_UNTOUCHED_HOLDOUT",
        },
    }
    durable_new(manifest_path, canonical(manifest))
    return manifest


def verify(root: Path, plan: dict[str, Any], *, generation: str = "v1") -> dict[str, Any]:
    validate_plan(plan)
    capture_state = audit_capture(root, plan)
    manifest_path = root / (
        "qualification-manifest.json" if generation == "v1" else "qualification-manifest-v2.json"
    )
    manifest = read_json(manifest_path)
    if manifest.get("generation") != generation:
        raise CorpusError("qualification generation differs")
    if manifest.get("plan_sha256") != sha256_file(root / "plan.json"):
        raise CorpusError("qualification plan binding differs")
    for relative, record in manifest["files"].items():
        path = root / relative
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise CorpusError(f"normalized file missing: {relative}")
        if sha256_file(path) != record["sha256"]:
            raise CorpusError(f"normalized file changed: {relative}")
        metadata = pq.read_metadata(path).metadata or {}
        if metadata.get(b"dataset_version") != DATASET_VERSION.encode():
            raise CorpusError(f"normalized provenance differs: {relative}")
    return {
        "status": "VERIFIED",
        "classification": manifest["classification"],
        "plan_sha256": manifest["plan_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "capture": capture_state,
        "files": len(manifest["files"]),
    }
