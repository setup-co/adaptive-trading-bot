"""Issue #214 bounded public LAST corpus for the history-screened panel.

This adapter reuses verified predecessor data, captures only the frozen missing
5m grid positions, and projects a research-only corpus.  It has no strategy,
admission, account, or runtime boundary.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_continuation as continuation
from adaptive_trading_bot.research import research_v2_corpus_tail as tail

PROTOCOL = "HISTORY_SCREENED_196D_LAST_CORPUS_V1"
SCHEMA = "history-screened-196d-last-corpus-v1"
DATASET_VERSION = "weex-history-screened-196d-last-v1"
ROOT = Path("data/research_corpus/history-screened-196d-last-v1")
SOURCE = Path("src/adaptive_trading_bot/research/history_screened_196d_corpus.py")
CLI_SOURCE = Path("scripts/research/history_screened_196d_corpus.py")
DOC = Path("docs/research/history_screened_196d_last_corpus.md")
PANEL = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZAMAUSDT",
    "BCHUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
)
SHARED = PANEL[:-1]
START = datetime(2026, 4, 1, tzinfo=UTC)
END = datetime(2026, 10, 14, tzinfo=UTC)
SIGNAL_START = datetime(2026, 4, 10, tzinfo=UTC)
SIGNAL_END = datetime(2026, 10, 7, tzinfo=UTC)
DAY = timedelta(days=1)
FIVE = timedelta(minutes=5)
STEP_MS = 300_000
EXPECTED_ROWS = 56_448
EXPECTED_REUSED = 234_956
EXPECTED_NEW = 329_524
EXPECTED_REQUESTS = 3_433
HOST = c.HOST
ROUTE = c.ROUTE
MAX_BODY = c.MAX_BODY
C2_ROOT = tail.ROOT
C2_MANIFEST_SHA = "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
PROBE_ROOT = Path("data/research_evidence/issue-206-weex-history-probe-3884cf7")
PROBE_MANIFEST_SHA = "e49e61eb4e6c77b09c55381aceda61178322c37a6f1ee56e65e2adba6192012e"
DAILY_ROOT = Path("data/research_evidence/issue-212-history-screened-shortlist-adbf3af")
DAILY_MANIFEST_SHA = "9259da6973e5c910912c6da1b69942a3f7a0edbffd583e5b4cd9ea0395301bbb"
DAILY_SUMMARY_SHA = "3dc81c33d3d12983973c69474e376561524b4dce1ef75723f1117851f88f1c81"


class Transport(Protocol):
    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class PublicTransport:
    """The single public GET route, with proxies and redirects disabled."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        validate_query(query)
        if not 0 < timeout <= 20:
            raise c.CorpusError("timeout outside frozen bound")
        url = HOST + ROUTE + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "atb-history-screened-corpus/1"}
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise c.CorpusError("response exceeds body cap")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise c.CorpusError("error response exceeds body cap") from None
            return exc.code, body


def _manifest_files(root: Path, expected: str) -> dict[str, str]:
    path = root / "manifest.json"
    if c.sha(path.read_bytes()) != expected:
        raise c.CorpusError(f"predecessor manifest changed: {root.name}")
    manifest = c.read_json(path)
    files: dict[str, str] = {}
    raw_files = manifest.get("files")
    if isinstance(raw_files, dict):
        files = dict(raw_files)
    elif isinstance(raw_files, list):
        files = {item["path"]: item["sha256"] for item in raw_files}
    else:
        raise c.CorpusError("predecessor manifest file inventory malformed")
    for name, digest in files.items():
        path = root / name
        if not path.is_file() or c.sha(path.read_bytes()) != digest:
            raise c.CorpusError(f"predecessor artifact changed: {root.name}/{name}")
    return files


def _row_from_record(record: dict[str, Any]) -> c.Row:
    return c.Row(
        int(record["open_time_ms"]),
        c._number(record["open"], positive=True),
        c._number(record["high"], positive=True),
        c._number(record["low"], positive=True),
        c._number(record["close"], positive=True),
        c._number(record["base_volume"]),
        int(record["close_time_ms"]),
        c._number(record["quote_volume"]),
        int(record["trade_count"]),
        c._number(record["taker_buy_base_volume"]),
        c._number(record["taker_buy_quote_volume"]),
    )


def _validated_c2_rows(repo: Path) -> tuple[dict[str, dict[int, c.Row]], dict[str, str]]:
    root = repo / C2_ROOT
    verified = tail.verify(repo, root)
    if verified["manifest_sha256"] != C2_MANIFEST_SHA:
        raise c.CorpusError("accepted C2 verification identity changed")
    manifest = c.read_json(root / "manifest.json")
    result: dict[str, dict[int, c.Row]] = {}
    shard_hashes: dict[str, str] = {}
    start = c.utc_ms(datetime(2026, 7, 16, tzinfo=UTC))
    end = c.utc_ms(END)
    for symbol in SHARED:
        name = f"combined/parquet/{symbol}-5m.parquet"
        source = root if symbol in tail.TAIL_SYMBOLS else repo / continuation.ROOT
        expected = manifest["files"].get(name) or manifest["reused_c1_shards"].get(name)
        body = (source / name).read_bytes()
        if expected is None or c.sha(body) != expected:
            raise c.CorpusError("accepted C2 5m shard changed")
        table = pq.read_table(pa.BufferReader(body))
        rows = {
            row.open_time_ms: row
            for row in (_row_from_record(item) for item in table.to_pylist())
            if start <= row.open_time_ms < end
        }
        result[symbol] = rows
        shard_hashes[symbol] = expected
    return result, shard_hashes


def _parse_predecessor_raw(path: Path, digest: str, query: dict[str, str]) -> list[c.Row]:
    body = path.read_bytes()
    if c.sha(body) != digest:
        raise c.CorpusError(f"predecessor raw changed: {path.name}")
    return c.parse_rows(body, query)


def predecessor_snapshot(repo: Path) -> dict[str, Any]:
    """Validate all reused authorities and reconcile the exact reused 5m set."""
    c2_rows, c2_shards = _validated_c2_rows(repo)
    probe = repo / PROBE_ROOT
    probe_files = _manifest_files(probe, PROBE_MANIFEST_SHA)
    probe_plan = c.read_json(probe / "plan.json")
    probe_entries = {entry["id"]: entry for entry in probe_plan["requests"]}
    reused: dict[str, dict[int, c.Row]] = {}
    probe_refs: list[dict[str, Any]] = []
    for symbol in SHARED:
        symbol_index = c.PANEL.index(symbol)
        rows = dict(c2_rows[symbol])
        for suffix, ordinal in (("OLDEST_PREFIX", 2), ("MID_PREFIX", 3)):
            number = symbol_index * 3 + ordinal
            request_id = f"{symbol}-{suffix}"
            entry = probe_entries[request_id]
            name = f"attempts/{number:03d}-{request_id}.raw.json"
            digest = probe_files[name]
            parsed = _parse_predecessor_raw(probe / name, digest, entry["query"])
            for row in parsed:
                if row.open_time_ms in rows and rows[row.open_time_ms] != row:
                    raise c.CorpusError("conflicting reused 5m predecessor row")
                rows[row.open_time_ms] = row
            probe_refs.append(
                {
                    "symbol": symbol,
                    "request_id": request_id,
                    "path": name,
                    "sha256": digest,
                    "query": entry["query"],
                    "rows": len(parsed),
                }
            )
        expected = 26_028 if symbol == "BTCUSDT" else 26_116
        if len(rows) != expected:
            raise c.CorpusError(f"reused row count differs for {symbol}: {len(rows)}")
        reused[symbol] = rows
    if sum(map(len, reused.values())) != EXPECTED_REUSED:
        raise c.CorpusError("reused position total differs from 234,956")

    daily = repo / DAILY_ROOT
    daily_files = _manifest_files(daily, DAILY_MANIFEST_SHA)
    if c.sha((daily / "summary.json").read_bytes()) != DAILY_SUMMARY_SHA:
        raise c.CorpusError("accepted shortlist summary changed")
    summary = c.read_json(daily / "summary.json")
    selected = [
        row["symbol"]
        for row in summary["candidate_records"]
        if row["disposition"] == "SELECTED_FOR_FINE_GRID_DATA_QUALIFICATION"
    ]
    if selected != list(PANEL):
        raise c.CorpusError("accepted selected panel identity/order changed")
    daily_plan = c.read_json(daily / "plan.json")
    daily_refs: list[dict[str, Any]] = []
    daily_rows: dict[str, dict[int, c.Row]] = {}
    request_index = {
        entry["id"]: (index + 1, entry) for index, entry in enumerate(daily_plan["requests"])
    }
    for symbol in PANEL:
        symbol_daily_rows: dict[int, c.Row] = {}
        for page in "ABC":
            index, entry = request_index[f"{symbol}-{page}"]
            name = f"attempts/{index:03d}-{symbol}-{page}.raw.json"
            digest = daily_files[name]
            parsed = _parse_predecessor_raw(daily / name, digest, entry["query"])
            for row in parsed:
                if (
                    row.open_time_ms in symbol_daily_rows
                    and symbol_daily_rows[row.open_time_ms] != row
                ):
                    raise c.CorpusError("conflicting daily predecessor row")
                symbol_daily_rows[row.open_time_ms] = row
            daily_refs.append(
                {
                    "symbol": symbol,
                    "page": page,
                    "path": name,
                    "sha256": digest,
                    "query": entry["query"],
                    "rows": len(parsed),
                }
            )
        if len(symbol_daily_rows) != 196:
            raise c.CorpusError(f"daily authority is not complete for {symbol}")
        daily_rows[symbol] = symbol_daily_rows
    return {
        "reused": reused,
        "daily": daily_rows,
        "c2_shards": c2_shards,
        "probe_refs": probe_refs,
        "daily_refs": daily_refs,
    }


def query(symbol: str, start_ms: int, end_ms: int) -> dict[str, str]:
    if symbol not in PANEL or start_ms >= end_ms or start_ms % STEP_MS or end_ms % STEP_MS:
        raise c.CorpusError("query is outside frozen grid")
    return {
        "symbol": symbol,
        "interval": "5m",
        "startTime": str(start_ms),
        "endTime": str(end_ms - 1),
        "limit": "100",
        "priceType": "LAST",
    }


def _inventory(reused: dict[str, dict[int, c.Row]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    global_start, global_end = c.utc_ms(START), c.utc_ms(END)
    for symbol in PANEL:
        present = set(reused.get(symbol, {}))
        sequence = 0
        for day in range(196):
            day_start = global_start + day * 86_400_000
            missing = [
                day_start + i * STEP_MS
                for i in range(288)
                if day_start + i * STEP_MS not in present
            ]
            cursor = 0
            while cursor < len(missing):
                run_end = cursor + 1
                while run_end < len(missing) and missing[run_end] == missing[run_end - 1] + STEP_MS:
                    run_end += 1
                run = missing[cursor:run_end]
                for offset in range(0, len(run), 100):
                    chunk = run[offset : offset + 100]
                    sequence += 1
                    requests.append(
                        {
                            "id": f"{symbol}-{sequence:04d}",
                            "query": query(symbol, chunk[0], chunk[-1] + STEP_MS),
                            "expected_rows": len(chunk),
                            "day_index": day,
                        }
                    )
                cursor = run_end
        if any(opened < global_start or opened >= global_end for opened in present):
            raise c.CorpusError("reused row outside corpus interval")
    return requests


def make_plan(repo: Path, source_revision: str) -> dict[str, Any]:
    snapshot = predecessor_snapshot(repo)
    requests = _inventory(snapshot["reused"])
    counts = Counter(entry["query"]["symbol"] for entry in requests)
    expected_counts = {
        symbol: (317 if symbol == "BTCUSDT" else 588 if symbol == "LINKUSDT" else 316)
        for symbol in PANEL
    }
    if len(requests) != EXPECTED_REQUESTS or counts != Counter(expected_counts):
        raise c.CorpusError(f"frozen request inventory differs: {len(requests)} {dict(counts)}")
    requested_rows = sum(entry["expected_rows"] for entry in requests)
    if requested_rows != EXPECTED_NEW:
        raise c.CorpusError("new grid-position total differs from 329,524")
    helpers = (SOURCE, CLI_SOURCE, DOC)
    return {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "source_revision": source_revision,
        "source_hashes": {
            path.as_posix(): c.sha((repo / path).read_bytes().replace(b"\r\n", b"\n"))
            for path in helpers
        },
        "output_root": ROOT.as_posix(),
        "panel": list(PANEL),
        "start": c.stamp(START),
        "end": c.stamp(END),
        "signal_start": c.stamp(SIGNAL_START),
        "signal_end": c.stamp(SIGNAL_END),
        "endpoint": HOST + ROUTE,
        "price_type": "LAST",
        "predecessors": {
            "c2_manifest_sha256": C2_MANIFEST_SHA,
            "c2_5m_shards": snapshot["c2_shards"],
            "probe_manifest_sha256": PROBE_MANIFEST_SHA,
            "probe_references": snapshot["probe_refs"],
            "daily_manifest_sha256": DAILY_MANIFEST_SHA,
            "daily_summary_sha256": DAILY_SUMMARY_SHA,
            "daily_references": snapshot["daily_refs"],
        },
        "reused_positions": {symbol: len(snapshot["reused"].get(symbol, {})) for symbol in PANEL},
        "reused_position_total": EXPECTED_REUSED,
        "new_position_total": EXPECTED_NEW,
        "requests": requests,
        "distinct_budget": EXPECTED_REQUESTS,
        "attempt_budget": EXPECTED_REQUESTS * 2,
        "minimum_seconds_between_attempts": 2,
        "timeout_seconds": 20,
        "max_response_bytes": MAX_BODY,
        "retry_policy": "timeout-or-5xx-once; all other failures terminal",
    }


def validate_plan(repo: Path, root: Path, plan: dict[str, Any]) -> None:
    if root.resolve() != (repo / ROOT).resolve():
        raise c.CorpusError("output root differs from frozen location")
    expected = make_plan(repo, plan.get("source_revision", ""))
    if plan != expected or (root / "plan.json").read_bytes() != c.canonical(plan):
        raise c.CorpusError("plan/source/predecessor identity changed")
    ids = [entry["id"] for entry in plan["requests"]]
    if len(ids) != len(set(ids)):
        raise c.CorpusError("duplicate request identity")


def validate_query(value: dict[str, str]) -> None:
    if set(value) != {"symbol", "interval", "startTime", "endTime", "limit", "priceType"}:
        raise c.CorpusError("query fields differ")
    if (
        value["symbol"] not in PANEL
        or value["interval"] != "5m"
        or value["limit"] != "100"
        or value["priceType"] != "LAST"
    ):
        raise c.CorpusError("query route semantics differ")
    start, end = int(value["startTime"]), int(value["endTime"]) + 1
    if not c.utc_ms(START) <= start < end <= c.utc_ms(END) or start % STEP_MS or end % STEP_MS:
        raise c.CorpusError("query range outside frozen grid")
    if (end - start) // STEP_MS > 100 or start // 86_400_000 != (end - 1) // 86_400_000:
        raise c.CorpusError("query crosses day or row cap")


def _state(root: Path, request_id: str, attempt: int) -> dict[str, Any] | None:
    return c.attempt_state(root, request_id, attempt)


def _classify_error(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT", True
    if isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout)
    ):
        return "TIMEOUT", True
    if isinstance(exc, ConnectionResetError) or exc.__class__.__name__ == "RemoteDisconnected":
        return "TRANSPORT_TERMINAL", False
    return "NETWORK_TERMINAL", False


def _capture_closed(root: Path) -> bool:
    return (
        (root / "manifest.json").exists()
        or (root / "report.json").exists()
        or (root / "route_stop.json").exists()
        or (root / "archives").exists()
        or (root / "parquet").exists()
    )


def capture(
    root: Path, plan: dict[str, Any], transport: Transport | None = None, *, sleep: Any = time.sleep
) -> dict[str, Any]:
    if (
        len(plan["requests"]) != EXPECTED_REQUESTS
        or plan["attempt_budget"] != 6866
        or _capture_closed(root)
    ):
        raise c.CorpusError("capture is not the frozen open generation")
    c.audit_ledger(root, plan)
    transport = transport or PublicTransport()
    transmitted = 0
    consecutive_terminal = 0
    last_sent: float | None = None
    for entry in plan["requests"]:
        request_id = entry["id"]
        for attempt in (0, 1):
            existing = _state(root, request_id, attempt)
            if existing is not None:
                if existing["outcome"] == "HTTP_OK" or not existing["retryable"] or attempt == 1:
                    break
                continue
            if attempt == 1:
                prior = _state(root, request_id, 0)
                if prior is None or not prior["retryable"]:
                    raise c.CorpusError("retry lacks retryable predecessor")
            stem = root / "working" / request_id / str(attempt)
            c.durable_new(
                stem.with_suffix(".started.json"),
                c.canonical(
                    {
                        "request_id": request_id,
                        "attempt": attempt,
                        "query": entry["query"],
                        "started_at": c.now(),
                        "plan_sha256": c.sha((root / "plan.json").read_bytes()),
                    }
                ),
            )
            if last_sent is not None:
                wait = 2.0 - (time.monotonic() - last_sent)
                if wait > 0:
                    sleep(wait)
            last_sent = time.monotonic()
            transmitted += 1
            try:
                status, body = transport.get(entry["query"], 20)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                outcome, retry = _classify_error(exc)
                result: dict[str, Any] = {
                    "request_id": request_id,
                    "attempt": attempt,
                    "completed_at": c.now(),
                    "outcome": "NETWORK_UNAVAILABLE",
                    "retryable": retry,
                    "transport_classification": outcome,
                    "error_class": type(exc).__name__,
                    "reason_class": type(exc.reason).__name__
                    if isinstance(exc, urllib.error.URLError)
                    else None,
                }
            else:
                c.durable_new(stem.with_suffix(".raw"), body)
                outcome = (
                    "HTTP_OK"
                    if status == 200
                    else "DENIED"
                    if status in {401, 403, 429}
                    else "POLICY_FAILURE"
                    if 300 <= status <= 399
                    else "HTTP_FAILURE"
                )
                result = {
                    "request_id": request_id,
                    "attempt": attempt,
                    "completed_at": c.now(),
                    "status": status,
                    "outcome": outcome,
                    "retryable": 500 <= status <= 599,
                    "raw_sha256": c.sha(body),
                    "raw_bytes": len(body),
                }
                if status != 200:
                    result["transport_classification"] = (
                        "SERVER_TERMINAL" if 500 <= status <= 599 else "HTTP_TERMINAL"
                    )
                if status == 200:
                    try:
                        parsed = c.parse_rows(body, entry["query"])
                    except c.CorpusError as exc:
                        result["validation_status"] = "CONTENT_TERMINAL"
                        result["content_error"] = str(exc)
                    else:
                        if len(parsed) != entry["expected_rows"]:
                            result["validation_status"] = "CONTENT_PARTIAL"
                            result["parsed_rows"] = len(parsed)
            c.durable_new(stem.with_suffix(".result.json"), c.canonical(result))
            if result["outcome"] == "HTTP_OK" and "validation_status" not in result:
                consecutive_terminal = 0
                break
            if result["retryable"] and attempt == 0:
                continue
            consecutive_terminal += 1
            terminal = str(
                result.get("validation_status")
                or result.get("transport_classification")
                or result["outcome"]
            )
            if (
                terminal
                in {
                    "NETWORK_TERMINAL",
                    "HTTP_TERMINAL",
                    "CONTENT_TERMINAL",
                    "CONTENT_PARTIAL",
                    "DENIED",
                    "POLICY_FAILURE",
                }
                or consecutive_terminal >= 3
            ):
                c.durable_new(
                    root / "route_stop.json",
                    c.canonical(
                        {
                            "request_id": request_id,
                            "outcome": terminal,
                            "at": result["completed_at"],
                        }
                    ),
                )
                return {"status": "ROUTE_STOPPED", "new_attempts": transmitted, "outcome": terminal}
            break
    return {"status": "CAPTURE_COMPLETE", "new_attempts": transmitted}


def _pack(root: Path, plan: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for symbol in PANEL:
        target = root / "archives" / f"{symbol}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise c.CorpusError("archive already exists")
        pending = target.with_suffix(".zip.pending")
        with zipfile.ZipFile(pending, "x", zipfile.ZIP_DEFLATED, allowZip64=False) as archive:
            for entry in plan["requests"]:
                if entry["query"]["symbol"] != symbol:
                    continue
                for attempt in (0, 1):
                    stem = root / "working" / entry["id"] / str(attempt)
                    for suffix in (".started.json", ".raw", ".result.json"):
                        path = stem.with_suffix(suffix)
                        if path.exists():
                            info = zipfile.ZipInfo(
                                f"{entry['id']}/{attempt}{suffix}", (1980, 1, 1, 0, 0, 0)
                            )
                            info.compress_type = zipfile.ZIP_DEFLATED
                            info.external_attr = 0o600 << 16
                            archive.writestr(info, path.read_bytes())
        os.replace(pending, target)
        hashes[symbol] = c.sha(target.read_bytes())
    return hashes


def _members(root: Path, symbol: str, digest: str) -> dict[str, bytes]:
    path = root / "archives" / f"{symbol}.zip"
    if c.sha(path.read_bytes()) != digest:
        raise c.CorpusError("archive changed")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(".." in Path(name).parts for name in names):
            raise c.CorpusError("archive inventory unsafe")
        return {name: archive.read(name) for name in names}


def _new_rows(
    root: Path, plan: dict[str, Any], symbol: str, members: dict[str, bytes] | None
) -> tuple[dict[int, c.Row], Counter[str]]:
    rows: dict[int, c.Row] = {}
    counts: Counter[str] = Counter()
    for entry in plan["requests"]:
        if entry["query"]["symbol"] != symbol:
            continue
        body: bytes | None = None
        outcome = "NOT_ATTEMPTED"
        for attempt in (0, 1):
            if members is None:
                state = _state(root, entry["id"], attempt)
                if state is None:
                    break
                raw_path = (root / "working" / entry["id"] / str(attempt)).with_suffix(".raw")
                raw = raw_path.read_bytes() if raw_path.exists() else None
            else:
                name = f"{entry['id']}/{attempt}.result.json"
                if name not in members:
                    break
                state = json.loads(members[name])
                raw = members.get(f"{entry['id']}/{attempt}.raw")
            outcome = state["outcome"]
            if outcome == "HTTP_OK":
                body = raw
                break
            if not state["retryable"]:
                break
        counts[outcome] += 1
        if body is None:
            continue
        parsed = c.parse_rows(body, entry["query"])
        if len(parsed) != entry["expected_rows"]:
            raise c.CorpusError("successful response row count differs")
        for row in parsed:
            if row.open_time_ms in rows and rows[row.open_time_ms] != row:
                raise c.CorpusError("conflicting new row")
            rows[row.open_time_ms] = row
    return rows, counts


def _daily_compare(five: dict[int, c.Row], daily: dict[int, c.Row]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for day in range(196):
        opened = c.utc_ms(START + day * DAY)
        parts = [five.get(opened + i * STEP_MS) for i in range(288)]
        if any(row is None for row in parts):
            results.append({"day_open_ms": opened, "status": "5M_INCOMPLETE"})
            continue
        full = [row for row in parts if row is not None]
        derived = c.derive(
            {row.open_time_ms: row for row in full}, [(opened, opened + 86_400_000)], 288
        )[0]
        authority = daily[opened]
        fields = (
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "quote_volume",
            "trade_count",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        )
        deltas = {
            field: str(getattr(derived, field) - getattr(authority, field)) for field in fields
        }
        results.append(
            {
                "day_open_ms": opened,
                "status": "MATCH"
                if all(c._number(value) == 0 for value in deltas.values())
                else "MISMATCH",
                "deltas": deltas,
            }
        )
    return results


def _warmup(frames: dict[str, list[c.Row]], spans: list[tuple[int, int]]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for begin, end in spans:
        complete = [
            row for row in frames["4h"] if begin <= row.open_time_ms and row.close_time_ms <= end
        ]
        if len(complete) < 50:
            continue
        boundary = complete[49].close_time_ms
        usable = [
            row
            for row in frames["15m"]
            if row.open_time_ms >= boundary and row.close_time_ms <= end
        ]
        if usable:
            intervals.append(
                {
                    "start_ms": usable[0].open_time_ms,
                    "end_ms": usable[-1].close_time_ms,
                    "complete_4h_warmup": 50,
                    "boundary_observation_excluded": True,
                }
            )
    return intervals


def _project(
    repo: Path, root: Path, plan: dict[str, Any], archives: dict[str, dict[str, bytes]] | None
) -> tuple[dict[str, Any], dict[tuple[str, str], list[c.Row]]]:
    snapshot = predecessor_snapshot(repo)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "panel": list(PANEL),
        "start": c.stamp(START),
        "end": c.stamp(END),
        "signal_window": {"start": c.stamp(SIGNAL_START), "end": c.stamp(SIGNAL_END)},
        "symbols": {},
    }
    series: dict[tuple[str, str], list[c.Row]] = {}
    for symbol in PANEL:
        new, counts = _new_rows(root, plan, symbol, None if archives is None else archives[symbol])
        five = dict(snapshot["reused"].get(symbol, {}))
        for opened, row in new.items():
            if opened in five and five[opened] != row:
                raise c.CorpusError("new response overlaps reused row differently")
            five[opened] = row
        spans = c.epochs(five)
        frames = {"5m": [five[key] for key in sorted(five)]}
        frames.update(
            {
                name: c.derive(five, spans, size)
                for name, size in (("15m", 3), ("1h", 12), ("4h", 48))
            }
        )
        warm = _warmup(frames, spans)
        comparisons = _daily_compare(five, snapshot["daily"][symbol])
        gaps = EXPECTED_ROWS - len(five)
        status = (
            "QUALIFIED"
            if gaps == 0 and all(item["status"] == "MATCH" for item in comparisons)
            else "PARTIAL_OR_BLOCKED"
        )
        report["symbols"][symbol] = {
            "status": status,
            "expected_5m_rows": EXPECTED_ROWS,
            "reused_5m_rows": len(snapshot["reused"].get(symbol, {})),
            "new_5m_rows": len(new),
            "valid_5m_rows": len(five),
            "missing_5m_rows": gaps,
            "epochs": [
                {"start_ms": start, "end_ms": end, "rows": (end - start) // STEP_MS}
                for start, end in spans
            ],
            "derived_rows": {name: len(rows) for name, rows in frames.items() if name != "5m"},
            "usable_intervals": warm,
            "daily_comparisons": comparisons,
            "request_counts": dict(sorted(counts.items())),
        }
        for frame, rows in frames.items():
            series[(symbol, frame)] = rows
    report["status"] = (
        "QUALIFIED"
        if all(value["status"] == "QUALIFIED" for value in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED"
    )
    return report, series


def build(repo: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(repo, root, plan)
    if _capture_closed(root):
        raise c.CorpusError("generation is already finalized")
    ledger = c.audit_ledger(root, plan)
    if ledger["distinct"] != EXPECTED_REQUESTS or any(
        state["outcome"] != "HTTP_OK" for state in ledger["states"].values()
    ):
        raise c.CorpusError("capture is incomplete or terminal; preserve blocker")
    archive_hashes = _pack(root, plan)
    archives = {symbol: _members(root, symbol, archive_hashes[symbol]) for symbol in PANEL}
    report, series = _project(repo, root, plan, archives)
    files = {f"archives/{symbol}.zip": digest for symbol, digest in archive_hashes.items()}
    for (symbol, frame), rows in series.items():
        path = root / "parquet" / f"{symbol}-{frame}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(c._table(symbol, frame, rows), path, compression="zstd", version="2.6")
        files[path.relative_to(root).as_posix()] = c.sha(path.read_bytes())
    c.durable_new(root / "report.json", c.canonical(report))
    files["report.json"] = c.sha((root / "report.json").read_bytes())
    manifest = {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "status": report["status"],
        "plan_sha256": c.sha((root / "plan.json").read_bytes()),
        "predecessors": plan["predecessors"],
        "ledger": {key: ledger[key] for key in ("attempts", "distinct", "retries")},
        "files": dict(sorted(files.items())),
    }
    c.durable_new(root / "manifest.json", c.canonical(manifest))
    return {
        "status": manifest["status"],
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "report_sha256": files["report.json"],
    }


def verify(repo: Path, root: Path) -> dict[str, Any]:
    plan = c.read_json(root / "plan.json")
    validate_plan(repo, root, plan)
    manifest = c.read_json(root / "manifest.json")
    if (
        manifest.get("plan_sha256") != c.sha((root / "plan.json").read_bytes())
        or manifest.get("predecessors") != plan["predecessors"]
    ):
        raise c.CorpusError("manifest provenance mismatch")
    for name, digest in manifest["files"].items():
        if c.sha((root / name).read_bytes()) != digest:
            raise c.CorpusError(f"artifact changed: {name}")
    archives = {
        symbol: _members(root, symbol, manifest["files"][f"archives/{symbol}.zip"])
        for symbol in PANEL
    }
    ledger = c.audit_ledger(root, plan, archives)
    report, series = _project(repo, root, plan, archives)
    if (
        c.canonical(report) != (root / "report.json").read_bytes()
        or manifest["status"] != report["status"]
    ):
        raise c.CorpusError("report differs from raw reconstruction")
    for (symbol, frame), rows in series.items():
        table = pq.read_table(root / "parquet" / f"{symbol}-{frame}.parquet")
        if not table.equals(c._table(symbol, frame, rows), check_metadata=True):
            raise c.CorpusError("Parquet differs from raw reconstruction")
    return {
        "status": report["status"],
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "ledger": {key: ledger[key] for key in ("attempts", "distinct", "retries")},
    }
