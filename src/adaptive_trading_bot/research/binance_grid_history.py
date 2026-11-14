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
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Event, Lock
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

PROTOCOL = "BTC_ETH_GRID_4H_EXTERNAL_HISTORY_DATA_V1"
V3_PROTOCOL = "BTC_ETH_GRID_4H_EXTERNAL_HISTORY_QUALIFICATION_V3"
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
ACCEPTED_PLAN_SHA256 = "ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace"
ACCEPTED_RAW_INVENTORY_SHA256 = "e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0"
V1_MANIFEST_SHA256 = "d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79"
V2_MANIFEST_SHA256 = "c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097"


class CorpusError(RuntimeError):
    """The frozen acquisition or qualification contract was violated."""


class GlobalCaptureStop(CorpusError):
    """A route-wide condition forbids scheduling further network work."""


class _InterruptedCapture(BaseException):
    def __init__(self, record: dict[str, Any], original: BaseException) -> None:
        super().__init__(str(original))
        self.record = record
        self.original = original


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

    def next_read_size(self, maximum: int) -> int:
        """Bound the next read to the remaining allowance plus one overflow byte."""

        with self.lock:
            remaining = MAX_PAYLOAD_BYTES - self.used
            return min(maximum, max(1, remaining + 1))

    def add(self, count: int) -> None:
        if count < 0:
            raise CorpusError("received-byte count is negative")
        with self.lock:
            self.used += count
            if self.used > MAX_PAYLOAD_BYTES:
                raise CorpusError("network payload cap exceeded")


def _accounted_payload_bytes(root: Path, plan: dict[str, Any]) -> int:
    """Restore known usage and fail closed when any prior usage is unresolved."""

    total = 0
    unresolved: list[str] = []
    for item in plan["resources"]:
        for row in _attempts(root, item["id"]):
            attempt = int(row["attempt"])
            recorded = int(row.get("bytes_received", row.get("bytes", 0)))
            if recorded < 0:
                raise CorpusError("durable received-byte count is negative")
            target = root / "raw" / item["relative_path"]
            pending = target.with_suffix(target.suffix + f".attempt-{attempt}.part")
            retained = root / "partials" / item["id"] / f"attempt-{attempt}.bin"
            known = max(
                recorded,
                pending.stat().st_size if pending.is_file() else 0,
                retained.stat().st_size if retained.is_file() else 0,
            )
            total += known
            if row.get("status") == "STARTED" or row.get("outcome") == "UNKNOWN_INTERRUPTED":
                unresolved.append(f"{item['id']}:attempt-{attempt}:{known}")
    if unresolved:
        raise CorpusError(
            "prior received-byte usage is unresolved; no network continuation is allowed "
            f"(known_received_bytes={total}; attempts={','.join(unresolved)})"
        )
    if total > MAX_PAYLOAD_BYTES:
        raise CorpusError(
            f"prior received-byte usage exceeds the payload cap: {total}>{MAX_PAYLOAD_BYTES}"
        )
    return total


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


def _attempt_path(root: Path, item: dict[str, Any], attempt: int) -> Path:
    return root / "ledger" / str(item["id"]) / f"attempt-{attempt}.json"


def _reserve_attempt(
    root: Path, plan: dict[str, Any], item: dict[str, Any], attempt: int
) -> dict[str, Any]:
    record = {
        "resource_id": item["id"],
        "attempt": attempt,
        "url": item["url"],
        "status": "STARTED",
        "started_at": datetime.now(UTC).isoformat(),
        "plan_sha256": sha256_file(root / "plan.json"),
        "capture_executable_sha": plan["executable_sha"],
        "bytes_received": 0,
    }
    durable_new(_attempt_path(root, item, attempt), canonical(record))
    return record


def _finish_attempt(root: Path, item: dict[str, Any], attempt: int, record: dict[str, Any]) -> None:
    atomic_write(_attempt_path(root, item, attempt), canonical(record))


def _download_once(
    root: Path,
    item: dict[str, Any],
    attempt: int,
    transport: Transport,
    budget: _PayloadBudget,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "resource_id": item["id"],
        "attempt": attempt,
        "url": item["url"],
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
            body = exc.read(budget.next_read_size(1024 * 1024))
            partial_path: str | None = None
            if body:
                partial = root / "partials" / item["id"] / f"attempt-{attempt}.bin"
                durable_new(partial, body)
                partial_path = partial.relative_to(root).as_posix()
            record.update(
                outcome=f"HTTP_{status}",
                http_status=status,
                retryable=retryable,
                retry_after=exc.headers.get("Retry-After"),
                bytes_received=len(body),
                received_sha256=sha256_bytes(body),
                partial_payload_path=partial_path,
            )
            budget.add(len(body))
            return record
        with response:
            status = int(response.status)
            if status != 200:
                record.update(
                    outcome=f"HTTP_{status}",
                    http_status=status,
                    retryable=status == 429 or 500 <= status <= 599,
                    retry_after=response.headers.get("Retry-After"),
                    bytes_received=0,
                )
                return record
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            count = 0
            with pending.open("xb") as handle:
                while block := response.read(budget.next_read_size(1024 * 1024)):
                    handle.write(block)
                    digest.update(block)
                    count += len(block)
                    record["bytes_received"] = count
                    handle.flush()
                    os.fsync(handle.fileno())
                    budget.add(len(block))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, target)
            record.update(
                outcome="HTTP_OK",
                http_status=200,
                retryable=False,
                bytes=count,
                bytes_received=count,
                sha256=digest.hexdigest(),
                payload_path=target.relative_to(root).as_posix(),
            )
            return record
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
        count = pending.stat().st_size if pending.exists() else 0
        retained_partial_path: str | None = None
        if pending.exists():
            partial = root / "partials" / item["id"] / f"attempt-{attempt}.bin"
            partial.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, partial)
            retained_partial_path = partial.relative_to(root).as_posix()
        record.update(
            outcome="TRANSPORT_ERROR",
            retryable=True,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
            bytes_received=count,
            received_sha256=(
                sha256_file(root / retained_partial_path)
                if retained_partial_path
                else sha256_bytes(b"")
            ),
            partial_payload_path=retained_partial_path,
        )
        return record
    except BaseException as exc:
        count = max(
            int(record.get("bytes_received", 0)),
            pending.stat().st_size if pending.exists() else 0,
        )
        interrupted_partial_path = record.get("partial_payload_path")
        if pending.exists():
            partial = root / "partials" / item["id"] / f"attempt-{attempt}.bin"
            partial.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending, partial)
            interrupted_partial_path = partial.relative_to(root).as_posix()
        record.update(
            outcome="UNKNOWN_INTERRUPTED",
            retryable=False,
            error_type=type(exc).__name__,
            bytes_received=count,
            received_sha256=(
                sha256_file(root / interrupted_partial_path)
                if interrupted_partial_path
                else sha256_bytes(b"")
            ),
            partial_payload_path=interrupted_partial_path,
        )
        raise _InterruptedCapture(record, exc) from exc
    finally:
        record["finished_at"] = datetime.now(UTC).isoformat()


def _capture_resource(
    root: Path,
    plan: dict[str, Any],
    item: dict[str, Any],
    transport: Transport,
    budget: _PayloadBudget,
    sleep: Callable[[float], None],
    stop: Event,
) -> dict[str, Any]:
    if _success(root, item):
        return {"id": item["id"], "outcome": "REUSED_SUCCESS"}
    prior = _attempts(root, item["id"])
    if len(prior) >= MAX_ATTEMPTS or (prior and not prior[-1].get("retryable", False)):
        return {"id": item["id"], "outcome": "EXHAUSTED_OR_PERMANENT"}
    for attempt in range(len(prior), MAX_ATTEMPTS):
        if stop.is_set():
            return {"id": item["id"], "outcome": "GLOBAL_STOP_NOT_STARTED"}
        reserved = _reserve_attempt(root, plan, item, attempt)
        try:
            record = _download_once(root, item, attempt, transport, budget)
        except _InterruptedCapture as exc:
            _finish_attempt(
                root,
                item,
                attempt,
                {
                    **reserved,
                    **exc.record,
                    "finished_at": datetime.now(UTC).isoformat(),
                },
            )
            stop.set()
            raise exc.original from exc
        record = {**reserved, **record, "finished_at": datetime.now(UTC).isoformat()}
        _finish_attempt(root, item, attempt, record)
        if record["outcome"] == "HTTP_OK":
            return {"id": item["id"], "outcome": "HTTP_OK"}
        status = record.get("http_status")
        if status in {401, 403, 451} or (isinstance(status, int) and 300 <= status < 400):
            stop.set()
            raise GlobalCaptureStop(
                f"global access/redirect stop: {item['id']} {record['outcome']}"
            )
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


def _capture_items(
    root: Path,
    plan: dict[str, Any],
    resources: list[dict[str, Any]],
    transport: Transport,
    budget: _PayloadBudget,
    sleep: Callable[[float], None],
) -> Counter[str]:
    """Run at most two in-flight resources and stop scheduling on a global condition."""

    outcomes: Counter[str] = Counter()
    stop = Event()
    iterator = iter(resources)
    first_error: BaseException | None = None
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures: dict[Any, dict[str, Any]] = {}

        def submit_one() -> bool:
            if stop.is_set():
                return False
            try:
                item = next(iterator)
            except StopIteration:
                return False
            future = pool.submit(
                _capture_resource, root, plan, item, transport, budget, sleep, stop
            )
            futures[future] = item
            return True

        for _ in range(MAX_WORKERS):
            submit_one()
        while futures:
            future = next(as_completed(tuple(futures)))
            futures.pop(future)
            try:
                result = future.result()
                outcomes[result["outcome"]] += 1
            except GlobalCaptureStop as exc:
                stop.set()
                first_error = first_error or exc
            if not stop.is_set():
                submit_one()
        if first_error is not None:
            raise first_error
    return outcomes


def capture(
    root: Path,
    plan: dict[str, Any],
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    validate_plan(plan)
    if (root / "capture-complete.json").exists():
        return read_json(root / "capture-complete.json")
    budget = _PayloadBudget(_accounted_payload_bytes(root, plan), Lock())
    client = transport or PublicTransport()
    resources = [item for item in plan["resources"] if not _success(root, item)]
    outcomes = _capture_items(root, plan, resources, client, budget, sleep)
    state = audit_capture(root, plan)
    if state["resources"] != {"successful": len(plan["resources"])}:
        raise CorpusError(f"capture remains incomplete: {state['resources']}")
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
        if _success(root, item):
            counts["successful"] += 1
        elif not rows:
            counts["unattempted"] += 1
        elif rows[-1].get("status") == "STARTED":
            counts["unknown_interrupted"] += 1
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
KLINE_HEADER = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
FUNDING_HEADER = ("calc_time", "funding_interval_hours", "last_funding_rate")


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
    previous: int | None = None
    for ordinal, row in enumerate(_zip_csv_rows(path)):
        if ordinal == 0 and row and not row[0].strip().isdigit():
            if tuple(item.strip() for item in row) != KLINE_HEADER:
                raise CorpusError("unrecognized kline header")
            continue
        if len(row) < 12:
            raise CorpusError("kline row has fewer than 12 fields")
        open_ms = int(row[0])
        if open_ms in seen:
            raise CorpusError("duplicate kline open")
        if previous is not None and open_ms <= previous:
            raise CorpusError("kline rows are not strictly chronological")
        seen.add(open_ms)
        previous = open_ms
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
    previous: int | None = None
    for ordinal, row in enumerate(_zip_csv_rows(path)):
        if ordinal == 0 and row and not row[0].strip().isdigit():
            header = [item.strip() for item in row]
            if tuple(header) != FUNDING_HEADER:
                raise CorpusError("unrecognized funding header")
            continue
        if len(row) < 3:
            raise CorpusError("funding row has fewer than three fields")
        timestamp = int(row[0])
        if timestamp in seen:
            raise CorpusError("duplicate funding event")
        if previous is not None and timestamp <= previous:
            raise CorpusError("funding events are not strictly chronological")
        seen.add(timestamp)
        previous = timestamp
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


def _comparison_record(
    start: int,
    derived: dict[str, Any] | None,
    native: dict[str, Any] | None,
    *,
    minute_count: int,
    missing_minute_opens: list[int],
) -> dict[str, Any]:
    if derived is None:
        status = "INCOMPLETE_DERIVATION_BUCKET"
        fields: list[str] = []
    elif native is None:
        status = "NATIVE_COMPARISON_UNAVAILABLE"
        fields = []
    else:
        fields = []
        for field in ("open", "high", "low", "close"):
            if Decimal(str(derived[field])) != Decimal(str(native[field])):
                fields.append(field)
        for field in ("volume", "quote_volume"):
            if not _volume_equal(Decimal(str(derived[field])), str(native[field])):
                fields.append(field)
        has_ohlc = any(field in {"open", "high", "low", "close"} for field in fields)
        has_volume = any(field in {"volume", "quote_volume"} for field in fields)
        status = (
            "OHLC_AND_VOLUME_MISMATCH"
            if has_ohlc and has_volume
            else "OHLC_MISMATCH"
            if has_ohlc
            else "VOLUME_MISMATCH"
            if has_volume
            else "MATCH"
        )
    record: dict[str, Any] = {
        "open_time_ms": start,
        "close_time_ms": start + 14_400_000,
        "status": status,
        "mismatched_fields": fields,
        "minute_count": minute_count,
        "missing_minute_opens": missing_minute_opens,
    }
    for prefix, row in (("derived", derived), ("native", native)):
        for field in ("open", "high", "low", "close", "volume", "quote_volume"):
            record[f"{prefix}_{field}"] = None if row is None else str(row[field])
    return record


def derive_and_reconcile(
    minute: list[dict[str, Any]], native: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive complete UTC buckets independently and retain every comparison result."""

    native_by_open = {int(row["open_time_ms"]): row for row in native}
    minute_by_bucket: dict[int, dict[int, dict[str, Any]]] = {}
    for row in minute:
        at = int(row["open_time_ms"])
        start = at - at % 14_400_000
        bucket = minute_by_bucket.setdefault(start, {})
        if at in bucket:
            raise CorpusError("duplicate minute inside a UTC 4h bucket")
        bucket[at] = row
    derived_rows: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    for start in sorted(set(minute_by_bucket) | set(native_by_open)):
        bucket = minute_by_bucket.get(start, {})
        expected_opens = [start + index * 60_000 for index in range(240)]
        missing = [at for at in expected_opens if at not in bucket]
        native_row = native_by_open.get(start)
        if missing or len(bucket) != 240:
            comparisons.append(
                _comparison_record(
                    start,
                    None,
                    native_row,
                    minute_count=len(bucket),
                    missing_minute_opens=missing,
                )
            )
            continue
        group = [bucket[at] for at in expected_opens]
        row = {
            "open_time_ms": start,
            "close_time_ms": start + 14_400_000,
            "open": str(group[0]["open"]),
            "high": str(max(Decimal(str(item["high"])) for item in group)),
            "low": str(min(Decimal(str(item["low"])) for item in group)),
            "close": str(group[-1]["close"]),
            "volume": str(sum((Decimal(str(item["volume"])) for item in group), start=Decimal(0))),
            "quote_volume": str(
                sum((Decimal(str(item["quote_volume"])) for item in group), start=Decimal(0))
            ),
            "source_minutes": 240,
            "available_at_ms": start + 14_400_000,
        }
        derived_rows.append(row)
        comparisons.append(
            _comparison_record(
                start,
                row,
                native_row,
                minute_count=240,
                missing_minute_opens=[],
            )
        )
    return derived_rows, comparisons


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
                rows, _ = derive_and_reconcile(parsed["trade_price_1m"], parsed["native_4h"])
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


@dataclass
class GridTracker:
    step_ms: int
    observed_rows: int = 0
    previous: int | None = None
    epoch_start: int | None = None
    gaps: list[dict[str, Any]] | None = None
    epochs: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        self.gaps = []
        self.epochs = []

    def add(self, values: Iterable[int]) -> None:
        for at in values:
            if at < int(START.timestamp() * 1000) or at >= int(END.timestamp() * 1000):
                raise CorpusError("grid observation outside frozen interval")
            if at % self.step_ms:
                raise CorpusError("grid observation is off-grid")
            if self.previous is not None and at <= self.previous:
                raise CorpusError("grid observations are not globally chronological")
            expected = (
                int(START.timestamp() * 1000)
                if self.previous is None
                else self.previous + self.step_ms
            )
            if at > expected:
                assert self.gaps is not None
                self.gaps.append(
                    {
                        "start": _iso(expected),
                        "end_exclusive": _iso(at),
                        "missing_rows": (at - expected) // self.step_ms,
                    }
                )
                if self.epoch_start is not None and self.previous is not None:
                    assert self.epochs is not None
                    self.epochs.append(
                        {
                            "start": _iso(self.epoch_start),
                            "end_exclusive": _iso(self.previous + self.step_ms),
                        }
                    )
                self.epoch_start = at
            elif self.epoch_start is None:
                self.epoch_start = at
            self.previous = at
            self.observed_rows += 1

    def finish(self) -> dict[str, Any]:
        assert self.gaps is not None and self.epochs is not None
        end = int(END.timestamp() * 1000)
        expected = (
            int(START.timestamp() * 1000) if self.previous is None else self.previous + self.step_ms
        )
        if expected < end:
            self.gaps.append(
                {
                    "start": _iso(expected),
                    "end_exclusive": _iso(end),
                    "missing_rows": (end - expected) // self.step_ms,
                }
            )
        if self.epoch_start is not None and self.previous is not None:
            self.epochs.append(
                {
                    "start": _iso(self.epoch_start),
                    "end_exclusive": _iso(self.previous + self.step_ms),
                }
            )
        return {
            "observed_rows": self.observed_rows,
            "missing_rows": sum(int(item["missing_rows"]) for item in self.gaps),
            "gap_count": len(self.gaps),
            "gaps": self.gaps,
            "maximal_valid_epochs": self.epochs,
        }


@dataclass
class FundingTracker:
    observed_events: int = 0
    first_time: int | None = None
    previous_time: int | None = None
    previous_interval_hours: int | None = None
    previous_month: str | None = None
    interval_counts: Counter[int] | None = None
    continuity_findings: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.interval_counts = Counter()
        self.continuity_findings = []

    def add(self, rows: list[dict[str, Any]], month: str) -> None:
        assert self.interval_counts is not None and self.continuity_findings is not None
        for row in rows:
            at = int(row["calc_time_ms"])
            interval = int(row["funding_interval_hours"])
            if self.previous_time is not None and self.previous_interval_hours is not None:
                expected_delta = self.previous_interval_hours * 3_600_000
                actual_delta = at - self.previous_time
                if actual_delta != expected_delta:
                    self.continuity_findings.append(
                        {
                            "previous_time_ms": self.previous_time,
                            "current_time_ms": at,
                            "previous_time": _iso(self.previous_time),
                            "current_time": _iso(at),
                            "expected_delta_ms": expected_delta,
                            "actual_delta_ms": actual_delta,
                            "previous_interval_hours": self.previous_interval_hours,
                            "cross_month": month != self.previous_month,
                        }
                    )
            self.interval_counts[interval] += 1
            self.observed_events += 1
            if self.first_time is None:
                self.first_time = at
            self.previous_time = at
            self.previous_interval_hours = interval
            self.previous_month = month

    def finish(self) -> dict[str, Any]:
        assert self.interval_counts is not None and self.continuity_findings is not None
        findings = list(self.continuity_findings)
        start_ms = int(START.timestamp() * 1000)
        end_ms = int(END.timestamp() * 1000)
        if self.first_time is None:
            findings.append({"kind": "NO_EVENTS"})
        else:
            if self.first_time != start_ms:
                findings.append(
                    {
                        "kind": "START_BOUNDARY_UNESTABLISHED",
                        "expected_first_time_ms": start_ms,
                        "actual_first_time_ms": self.first_time,
                    }
                )
            if (
                self.previous_time is None
                or self.previous_interval_hours is None
                or self.previous_time + self.previous_interval_hours * 3_600_000 != end_ms
            ):
                findings.append(
                    {
                        "kind": "END_BOUNDARY_UNESTABLISHED",
                        "expected_end_ms": end_ms,
                        "actual_last_time_ms": self.previous_time,
                        "last_reported_interval_hours": self.previous_interval_hours,
                    }
                )
        return {
            "observed_events": self.observed_events,
            "reported_interval_hours": dict(sorted(self.interval_counts.items())),
            "continuity_status": (
                "SCHEDULE_COMPLETE_UNDER_REPORTED_INTERVALS"
                if not findings
                else "SCHEDULE_PARTIAL_OR_UNCERTAIN"
            ),
            "continuity_finding_count": len(findings),
            "continuity_findings": findings,
            "first_event_ms": self.first_time,
            "last_event_ms": self.previous_time,
        }


def _validate_retained_raw(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    if sha256_file(root / "plan.json") != ACCEPTED_PLAN_SHA256:
        raise CorpusError("retained plan identity differs")
    if sha256_file(root / "raw-inventory.json") != ACCEPTED_RAW_INVENTORY_SHA256:
        raise CorpusError("retained raw inventory identity differs")
    inventory = read_json(root / "raw-inventory.json")
    records = {row["id"]: row for row in inventory["resources"]}
    if set(records) != {row["id"] for row in plan["resources"]}:
        raise CorpusError("raw inventory differs from frozen plan")
    for item in plan["resources"]:
        record = records[item["id"]]
        if record["url"] != item["url"] or record["attempt"] != 0:
            raise CorpusError("raw inventory request identity differs")
        path = root / "raw" / item["relative_path"]
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise CorpusError("retained raw size differs")
        if sha256_file(path) != record["sha256"]:
            raise CorpusError("retained raw hash differs")
    for item in plan["resources"]:
        if item["kind"] != "zip":
            continue
        archive_record = records[item["id"]]
        checksum_item = next(
            candidate
            for candidate in plan["resources"]
            if candidate["relative_path"] == item["relative_path"] + ".CHECKSUM"
        )
        checksum_path = root / "raw" / checksum_item["relative_path"]
        if (
            _official_checksum(checksum_path.read_bytes(), Path(item["relative_path"]).name)
            != (archive_record["sha256"])
        ):
            raise CorpusError("retained raw differs from official checksum authority")
    if sha256_file(root / "qualification-manifest.json") != V1_MANIFEST_SHA256:
        raise CorpusError("v1 qualification evidence changed")
    if sha256_file(root / "qualification-manifest-v2.json") != V2_MANIFEST_SHA256:
        raise CorpusError("v2 qualification evidence changed")
    return inventory


def _v3_partition(
    root: Path,
    files: dict[str, dict[str, Any]],
    relative: Path,
    rows: list[dict[str, Any]],
    metadata: dict[str, str],
    *,
    raw_sha256: str | None = None,
) -> None:
    _write_parquet(
        root / relative,
        rows,
        {
            "schema": ROW_SCHEMA,
            "dataset_version": DATASET_VERSION,
            "qualification_protocol": V3_PROTOCOL,
            **metadata,
        },
    )
    record: dict[str, Any] = {
        "sha256": sha256_file(root / relative),
        "bytes": (root / relative).stat().st_size,
        "rows": len(rows),
    }
    if raw_sha256 is not None:
        record["raw_sha256"] = raw_sha256
    files[relative.as_posix()] = record


def qualify_v3(
    root: Path, plan: dict[str, Any], *, qualification_executable_sha: str
) -> dict[str, Any]:
    """Construct the bounded monthly v3 result from retained raw archives only."""

    validate_plan(plan)
    manifest_path = root / "qualification-manifest-v3.json"
    output_root = root / "derived-generations" / "v3"
    if manifest_path.exists() or output_root.exists():
        raise CorpusError("v3 qualification generation already exists or is partial")
    inventory = _validate_retained_raw(root, plan)
    files: dict[str, dict[str, Any]] = {}
    coverage: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        component_rows: Counter[str] = Counter()
        component_months: Counter[str] = Counter()
        component_failures: dict[str, list[dict[str, str]]] = {
            component: [] for component in COMPONENTS
        }
        trackers = {
            "trade_price_1m": GridTracker(60_000),
            "native_4h": GridTracker(14_400_000),
            "mark_1m": GridTracker(60_000),
            "derived_4h": GridTracker(14_400_000),
        }
        funding_tracker = FundingTracker()
        reconciliation_counts: Counter[str] = Counter()
        discrepancies: list[dict[str, Any]] = []
        for month in MONTHS:
            parsed: dict[str, list[dict[str, Any]]] = {}
            for component in COMPONENTS:
                try:
                    archive, raw_sha = _pair_valid(root, component, symbol, month)
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
                    parsed[component] = rows
                    relative = (
                        Path("derived-generations/v3/normalized")
                        / component
                        / symbol
                        / f"{month}.parquet"
                    )
                    _v3_partition(
                        root,
                        files,
                        relative,
                        rows,
                        {"component": component, "symbol": symbol, "month": month},
                        raw_sha256=raw_sha,
                    )
                    component_rows[component] += len(rows)
                    component_months[component] += 1
                    if component == "funding":
                        funding_tracker.add(rows, month)
                    else:
                        trackers[component].add(int(row["open_time_ms"]) for row in rows)
                except (CorpusError, OSError, ValueError, zipfile.BadZipFile) as exc:
                    component_failures[component].append({"month": month, "error": str(exc)})
            if "trade_price_1m" in parsed:
                native = parsed.get("native_4h", [])
                derived, comparisons = derive_and_reconcile(parsed["trade_price_1m"], native)
                trackers["derived_4h"].add(int(row["open_time_ms"]) for row in derived)
                reconciliation_counts.update(str(row["status"]) for row in comparisons)
                discrepancies.extend(row for row in comparisons if row["status"] != "MATCH")
                for component, rows in (
                    ("derived_4h", derived),
                    ("reconciliation", comparisons),
                ):
                    relative = (
                        Path("derived-generations/v3/normalized")
                        / component
                        / symbol
                        / f"{month}.parquet"
                    )
                    _v3_partition(
                        root,
                        files,
                        relative,
                        rows,
                        {
                            "component": component,
                            "symbol": symbol,
                            "month": month,
                            "derivation": "timestamp-keyed-240-complete-aligned-trade-price-1m",
                        },
                    )
                component_rows["derived_4h"] += len(derived)
                component_months["derived_4h"] += 1
                component_rows["reconciliation"] += len(comparisons)
                component_months["reconciliation"] += 1
            del parsed
        grid_results = {name: tracker.finish() for name, tracker in trackers.items()}
        funding_result = funding_tracker.finish()
        component_coverage: dict[str, Any] = {}
        for component, expected in (
            ("trade_price_1m", EXPECTED_ONE_MINUTE),
            ("native_4h", EXPECTED_FOUR_HOUR),
            ("derived_4h", EXPECTED_FOUR_HOUR),
            ("mark_1m", EXPECTED_ONE_MINUTE),
        ):
            grid = grid_results[component]
            complete = (
                component_months[component] == len(MONTHS)
                and component_rows[component] == expected
                and grid["missing_rows"] == 0
                and not component_failures.get(component)
            )
            component_coverage[component] = {
                "status": (
                    "QUALIFIED_CANONICAL_DERIVED"
                    if component == "derived_4h" and complete
                    else "QUALIFIED"
                    if complete
                    else "PARTIAL_OR_FAILED"
                ),
                "planned_months": len(MONTHS),
                "qualified_months": component_months[component],
                "rows": component_rows[component],
                "expected_rows": expected,
                "grid": grid,
                "failures": component_failures.get(component, []),
            }
        mismatches = sum(
            count for status, count in reconciliation_counts.items() if status != "MATCH"
        )
        component_coverage["reconciliation"] = {
            "status": "QUALIFIED_EXACT_MATCH"
            if mismatches == 0 and component_rows["reconciliation"] == EXPECTED_FOUR_HOUR
            else "PARTIAL_DISCREPANCIES",
            "rows": component_rows["reconciliation"],
            "expected_rows": EXPECTED_FOUR_HOUR,
            "status_counts": dict(sorted(reconciliation_counts.items())),
            "discrepancy_count": mismatches,
            "discrepancies": discrepancies,
            "comparison_rule": {
                "ohlc": "exact Decimal equality",
                "volume": "derived sum quantized to native lexical precision then exact equality",
            },
        }
        component_coverage["funding"] = {
            "status": (
                "QUALIFIED_EVENTS_AND_SCHEDULE"
                if component_months["funding"] == len(MONTHS)
                and not component_failures["funding"]
                and funding_result["continuity_status"]
                == "SCHEDULE_COMPLETE_UNDER_REPORTED_INTERVALS"
                else "EVENTS_PARSED_SCHEDULE_PARTIAL_OR_UNCERTAIN"
            ),
            "planned_months": len(MONTHS),
            "qualified_months": component_months["funding"],
            "rows": component_rows["funding"],
            "failures": component_failures["funding"],
            **funding_result,
        }
        coverage[symbol] = component_coverage
    price_complete = all(
        coverage[symbol][component]["status"] in {"QUALIFIED", "QUALIFIED_CANONICAL_DERIVED"}
        for symbol in SYMBOLS
        for component in ("trade_price_1m", "native_4h", "derived_4h")
    )
    reconciliation_complete = all(
        coverage[symbol]["reconciliation"]["status"] == "QUALIFIED_EXACT_MATCH"
        for symbol in SYMBOLS
    )
    manifest = {
        "schema": "binance-usdm-qualification-manifest-v3",
        "protocol": V3_PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "generation": "v3",
        "capture_executable_sha": plan["executable_sha"],
        "qualification_executable_sha": qualification_executable_sha,
        "plan_sha256": ACCEPTED_PLAN_SHA256,
        "raw_inventory_sha256": ACCEPTED_RAW_INVENTORY_SHA256,
        "predecessor_manifests": {
            "v1": V1_MANIFEST_SHA256,
            "v2": V2_MANIFEST_SHA256,
        },
        "created_at": datetime.now(UTC).isoformat(),
        "classification": (
            "PRICE_CORPUS_QUALIFIED"
            if price_complete and reconciliation_complete
            else "PRICE_CORPUS_PARTIAL_RECONCILIATION"
        ),
        "component_coverage": coverage,
        "files": files,
        "raw_resource_count": inventory["resource_count"],
        "raw_payload_bytes": inventory["payload_bytes"],
        "bounded_orchestration": "one-asset-one-month buffers; compact continuity state only",
        "interpretation": {
            "canonical_derived_rows_are_not_automatically_reconciliation_approved": True,
            "mark_limitations_do_not_erase_last": True,
            "funding_gaps_are_not_zero_funding": True,
            "strategy_evaluation_performed": False,
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


def verify_v3(
    root: Path, plan: dict[str, Any], *, verification_executable_sha: str | None = None
) -> dict[str, Any]:
    """Verify v3 bindings, file identities and reported scientific invariants."""

    _validate_retained_raw(root, plan)
    manifest_path = root / "qualification-manifest-v3.json"
    manifest = read_json(manifest_path)
    if (
        manifest.get("protocol") != V3_PROTOCOL
        or manifest.get("generation") != "v3"
        or manifest.get("plan_sha256") != ACCEPTED_PLAN_SHA256
        or manifest.get("raw_inventory_sha256") != ACCEPTED_RAW_INVENTORY_SHA256
        or manifest.get("predecessor_manifests")
        != {"v1": V1_MANIFEST_SHA256, "v2": V2_MANIFEST_SHA256}
    ):
        raise CorpusError("v3 manifest binding differs")
    file_rows: Counter[tuple[str, str]] = Counter()
    reconciliation: dict[str, Counter[str]] = {symbol: Counter() for symbol in SYMBOLS}
    discrepancy_rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    funding: dict[str, FundingTracker] = {symbol: FundingTracker() for symbol in SYMBOLS}
    for relative, record in manifest["files"].items():
        path = root / relative
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise CorpusError(f"v3 file missing: {relative}")
        if sha256_file(path) != record["sha256"]:
            raise CorpusError(f"v3 file changed: {relative}")
        metadata = pq.read_metadata(path).metadata or {}
        if (
            metadata.get(b"dataset_version") != DATASET_VERSION.encode()
            or metadata.get(b"qualification_protocol") != V3_PROTOCOL.encode()
        ):
            raise CorpusError(f"v3 file provenance differs: {relative}")
        parts = Path(relative).parts
        component, symbol = parts[-3], parts[-2]
        file_rows[(symbol, component)] += int(record["rows"])
        if component == "reconciliation":
            rows = pq.read_table(path).to_pylist()
            for row in rows:
                derived = (
                    None
                    if row["derived_open"] is None
                    else {
                        field: row[f"derived_{field}"]
                        for field in ("open", "high", "low", "close", "volume", "quote_volume")
                    }
                )
                native = (
                    None
                    if row["native_open"] is None
                    else {
                        field: row[f"native_{field}"]
                        for field in ("open", "high", "low", "close", "volume", "quote_volume")
                    }
                )
                expected = _comparison_record(
                    int(row["open_time_ms"]),
                    derived,
                    native,
                    minute_count=int(row["minute_count"]),
                    missing_minute_opens=[int(value) for value in row["missing_minute_opens"]],
                )
                if row != expected:
                    raise CorpusError("v3 reconciliation row does not recompute exactly")
                reconciliation[symbol][str(row["status"])] += 1
                if row["status"] != "MATCH":
                    discrepancy_rows[symbol].append(row)
        elif component == "derived_4h":
            table = pq.read_table(
                path, columns=["close_time_ms", "available_at_ms", "source_minutes"]
            ).to_pylist()
            if any(
                row["close_time_ms"] != row["available_at_ms"] or row["source_minutes"] != 240
                for row in table
            ):
                raise CorpusError("v3 derived chronology differs")
        elif component == "funding":
            month = Path(relative).stem
            funding[symbol].add(pq.read_table(path).to_pylist(), month)
    for symbol in SYMBOLS:
        reported = manifest["component_coverage"][symbol]
        for component in (
            "trade_price_1m",
            "native_4h",
            "mark_1m",
            "derived_4h",
            "reconciliation",
            "funding",
        ):
            if file_rows[(symbol, component)] != reported[component]["rows"]:
                raise CorpusError("v3 reported/file row totals differ")
        if (
            dict(sorted(reconciliation[symbol].items()))
            != reported["reconciliation"]["status_counts"]
        ):
            raise CorpusError("v3 reconciliation status totals differ")
        if discrepancy_rows[symbol] != reported["reconciliation"]["discrepancies"]:
            raise CorpusError("v3 discrepancy details differ from retained rows")
        funding_result = funding[symbol].finish()
        funding_result["reported_interval_hours"] = {
            str(key): value for key, value in funding_result["reported_interval_hours"].items()
        }
        for field in (
            "observed_events",
            "reported_interval_hours",
            "continuity_status",
            "continuity_finding_count",
            "continuity_findings",
            "first_event_ms",
            "last_event_ms",
        ):
            if funding_result[field] != reported["funding"][field]:
                raise CorpusError("v3 funding continuity report differs")
        derived = reported["derived_4h"]
        if derived["rows"] + derived["grid"]["missing_rows"] != EXPECTED_FOUR_HOUR:
            raise CorpusError("v3 derived coverage does not reconcile")
        trade = reported["trade_price_1m"]
        if trade["rows"] + trade["grid"]["missing_rows"] != EXPECTED_ONE_MINUTE:
            raise CorpusError("v3 LAST coverage does not reconcile")
    return {
        "status": "VERIFIED",
        "classification": manifest["classification"],
        "manifest_sha256": sha256_file(manifest_path),
        "plan_sha256": ACCEPTED_PLAN_SHA256,
        "raw_inventory_sha256": ACCEPTED_RAW_INVENTORY_SHA256,
        "verification_executable_sha": verification_executable_sha,
        "files": len(manifest["files"]),
    }


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _grid_intervals(values: list[int], step_ms: int) -> dict[str, Any]:
    expected_start = int(START.timestamp() * 1000)
    expected_end = int(END.timestamp() * 1000)
    actual = sorted(set(values))
    gaps: list[dict[str, Any]] = []
    epochs: list[dict[str, Any]] = []
    cursor = expected_start
    epoch_start: int | None = None
    previous: int | None = None
    for at in actual:
        if at < expected_start or at >= expected_end or at % step_ms:
            raise CorpusError("reported grid timestamp is outside the frozen interval")
        if at > cursor:
            gaps.append(
                {
                    "start": _iso(cursor),
                    "end_exclusive": _iso(at),
                    "missing_rows": (at - cursor) // step_ms,
                }
            )
        if previous is None or at != previous + step_ms:
            if epoch_start is not None and previous is not None:
                epochs.append(
                    {"start": _iso(epoch_start), "end_exclusive": _iso(previous + step_ms)}
                )
            epoch_start = at
        previous = at
        cursor = max(cursor, at + step_ms)
    if cursor < expected_end:
        gaps.append(
            {
                "start": _iso(cursor),
                "end_exclusive": _iso(expected_end),
                "missing_rows": (expected_end - cursor) // step_ms,
            }
        )
    if epoch_start is not None and previous is not None:
        epochs.append({"start": _iso(epoch_start), "end_exclusive": _iso(previous + step_ms)})
    return {
        "observed_rows": len(actual),
        "missing_rows": sum(item["missing_rows"] for item in gaps),
        "gap_count": len(gaps),
        "gaps": gaps,
        "maximal_valid_epochs": epochs,
    }


def publication_report(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Write compact, reproducible coverage and raw-hash inventories for publication."""

    verified = verify(root, plan, generation="v2")
    manifest = read_json(root / "qualification-manifest-v2.json")
    raw_inventory: list[dict[str, Any]] = []
    for item in plan["resources"]:
        successes = [row for row in _attempts(root, item["id"]) if row.get("outcome") == "HTTP_OK"]
        if len(successes) != 1:
            raise CorpusError(f"publication requires one successful resource: {item['id']}")
        row = successes[0]
        raw_inventory.append(
            {
                **item,
                "attempt": row["attempt"],
                "bytes": row["bytes"],
                "sha256": row["sha256"],
                "request_time": row["started_at"],
                "finished_at": row["finished_at"],
                "http_status": row["http_status"],
            }
        )
    inventory = {
        "schema": "binance-usdm-raw-inventory-v1",
        "plan_sha256": sha256_file(root / "plan.json"),
        "resources": raw_inventory,
        "resource_count": len(raw_inventory),
        "payload_bytes": sum(row["bytes"] for row in raw_inventory),
    }
    durable_new(root / "raw-inventory.json", canonical(inventory))
    grids: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        grids[symbol] = {}
        for component, step in (
            ("trade_price_1m", 60_000),
            ("mark_1m", 60_000),
            ("native_4h", 14_400_000),
            ("derived_4h", 14_400_000),
        ):
            values: list[int] = []
            prefix = f"derived-generations/v2/normalized/{component}/{symbol}/"
            for relative, record in manifest["files"].items():
                if not relative.startswith(prefix):
                    continue
                path = root / relative
                if sha256_file(path) != record["sha256"]:
                    raise CorpusError("publication partition hash differs")
                raw_values = (
                    pq.read_table(path, columns=["open_time_ms"]).column("open_time_ms").to_pylist()
                )
                if any(value is None for value in raw_values):
                    raise CorpusError("publication partition contains a null open time")
                values.extend(int(value) for value in raw_values if value is not None)
            grids[symbol][component] = _grid_intervals(values, step)
    report = {
        "schema": "binance-grid-history-publication-report-v1",
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "capture_executable_sha": plan["executable_sha"],
        "qualification_executable_sha": manifest["qualification_executable_sha"],
        "plan_sha256": sha256_file(root / "plan.json"),
        "raw_inventory_sha256": sha256_file(root / "raw-inventory.json"),
        "qualification_manifest_sha256": sha256_file(root / "qualification-manifest-v2.json"),
        "verification": verified,
        "classification": manifest["classification"],
        "component_coverage": manifest["component_coverage"],
        "grids": grids,
        "weex_cost_reference": {
            "as_of": "2026-11-14",
            "public_standard_maker_rate": "0.0002",
            "public_standard_taker_rate": "0.0008",
            "account_or_api_rates_verified": False,
            "current_exchange_info_requests_made": 0,
        },
        "strategy_evaluation_performed": False,
        "next_action": "freeze one finite-capital Grid protocol before economic evaluation",
    }
    durable_new(root / "publication-report.json", canonical(report))
    return report


def publication_report_v3(
    root: Path, plan: dict[str, Any], verified: dict[str, Any]
) -> dict[str, Any]:
    manifest = read_json(root / "qualification-manifest-v3.json")
    if (
        verified.get("status") != "VERIFIED"
        or verified.get("manifest_sha256") != sha256_file(root / "qualification-manifest-v3.json")
        or verified.get("plan_sha256") != ACCEPTED_PLAN_SHA256
        or verified.get("raw_inventory_sha256") != ACCEPTED_RAW_INVENTORY_SHA256
    ):
        raise CorpusError("v3 publication requires the matching successful verifier result")
    report = {
        "schema": "binance-grid-history-publication-report-v3",
        "protocol": V3_PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "classification": manifest["classification"],
        "capture_executable_sha": manifest["capture_executable_sha"],
        "qualification_executable_sha": manifest["qualification_executable_sha"],
        "plan_sha256": ACCEPTED_PLAN_SHA256,
        "raw_inventory_sha256": ACCEPTED_RAW_INVENTORY_SHA256,
        "predecessor_manifests": manifest["predecessor_manifests"],
        "qualification_manifest_sha256": verified["manifest_sha256"],
        "verification": verified,
        "component_coverage": manifest["component_coverage"],
        "retained_external_root": str(root),
        "strategy_evaluation_performed": False,
        "next_action": "freeze one finite-capital Grid protocol before economic evaluation",
    }
    durable_new(root / "publication-report-v3.json", canonical(report))
    return report
