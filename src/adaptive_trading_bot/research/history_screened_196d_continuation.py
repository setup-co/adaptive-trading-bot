"""Issue #216 bounded continuation of the stopped 196-day LAST capture.

The adapter imports immutable Issue #214 evidence, permits only its exact
unresolved request shapes, and builds honest complete or partial data products.
It contains no strategy, admission, account, or runtime authority.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import zipfile
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Protocol

import pyarrow.parquet as pq

from adaptive_trading_bot.research import history_screened_196d_corpus as parent
from adaptive_trading_bot.research import research_v2_corpus as core

PROTOCOL = "HISTORY_SCREENED_196D_LAST_CONTINUATION_V1"
SCHEMA = "history-screened-196d-last-continuation-v1"
DATASET_VERSION = "weex-history-screened-196d-last-continuation-v1"
ROOT = Path("data/research_corpus/history-screened-196d-last-continuation-v1")
SOURCE = Path("src/adaptive_trading_bot/research/history_screened_196d_continuation.py")
CLI_SOURCE = Path("scripts/research/history_screened_196d_continuation.py")
WRAPPER_SOURCE = Path("scripts/research/history_screened_196d_continuation_wrapper.py")
DOC = Path("docs/research/history_screened_196d_last_continuation.md")
PARENT_ROOT = parent.ROOT
PARENT_PLAN_SHA = "0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713"
PARENT_PUBLICATION_ROOT = Path("data/research_evidence/issue-214-history-screened-partial-3cc2f47")
PARENT_PUBLICATION_MANIFEST_SHA = "d2cae3a8c1eccbed8d77dee43553b65fb7e50405040f7722ff501bee22668dbe"
PARENT_INVENTORY_SHA = "606cec24dd4620386ddc9bf8cfebdfd9e01ab4e4a4a68a82ee37fb23232ae82f"
EXPECTED_REQUESTS = 1_785
EXPECTED_POSITIONS = 171_336
EXPECTED_SYMBOL_REQUESTS = {
    "ZAMAUSDT": 249,
    "BCHUSDT": 316,
    "AVAXUSDT": 316,
    "DOGEUSDT": 316,
    "LINKUSDT": 588,
}
EXPECTED_SYMBOL_POSITIONS = {
    "ZAMAUSDT": 23_892,
    "BCHUSDT": 30_332,
    "AVAXUSDT": 30_332,
    "DOGEUSDT": 30_332,
    "LINKUSDT": 56_448,
}
ALLOWED_NETWORK_FAILURES = {"TIMEOUT", "DNS_RESOLUTION", "REMOTE_DISCONNECTED"}


class Transport(Protocol):
    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]: ...


class PublicTransport(parent.PublicTransport):
    """The unchanged public route with the successor user agent."""

    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        validate_query(query)
        return super().get(query, timeout)


def _publication_manifest(repo: Path) -> dict[str, Any]:
    path = repo / PARENT_PUBLICATION_ROOT / "manifest.json"
    body = path.read_bytes()
    if core.sha(body) != PARENT_PUBLICATION_MANIFEST_SHA:
        raise core.CorpusError("Issue #214 publication manifest changed")
    manifest: dict[str, Any] = json.loads(body)
    for section in ("corpus_files", "evidence_files", "publication_sources"):
        records = manifest.get(section)
        if not isinstance(records, dict):
            raise core.CorpusError("Issue #214 publication file inventory malformed")
        for relative, record in records.items():
            candidate = repo / relative
            if (
                not candidate.is_file()
                or candidate.stat().st_size != record.get("bytes")
                or core.sha(candidate.read_bytes()) != record.get("sha256")
            ):
                raise core.CorpusError(f"Issue #214 published artifact changed: {relative}")
    inventory = repo / PARENT_PUBLICATION_ROOT / "partial-inventory-report.json"
    if core.sha(inventory.read_bytes()) != PARENT_INVENTORY_SHA:
        raise core.CorpusError("Issue #214 partial inventory changed")
    return manifest


def _expected_opens(entry: dict[str, Any]) -> list[int]:
    query = entry["query"]
    start = int(query["startTime"])
    end = int(query["endTime"]) + 1
    opens = list(range(start, end, parent.STEP_MS))
    if len(opens) != entry["expected_rows"]:
        raise core.CorpusError("planned expected row count differs from query grid")
    return opens


def _parent_snapshot(repo: Path) -> dict[str, Any]:
    """Validate every parent authority and return its reusable rows/remainder."""
    _publication_manifest(repo)
    root = repo / PARENT_ROOT
    plan_path = root / "plan.json"
    if core.sha(plan_path.read_bytes()) != PARENT_PLAN_SHA:
        raise core.CorpusError("Issue #214 plan changed")
    plan: dict[str, Any] = core.read_json(plan_path)
    parent.validate_plan(repo, root, plan)
    ledger = core.audit_ledger(root, plan)
    if ledger["attempts"] != 1_649 or ledger["distinct"] != 1_649 or ledger["retries"]:
        raise core.CorpusError("Issue #214 ledger counts changed")

    successful: dict[str, dict[int, core.Row]] = {symbol: {} for symbol in parent.PANEL}
    remainder: list[dict[str, Any]] = []
    failed: list[str] = []
    for entry in plan["requests"]:
        state = ledger["states"].get((entry["id"], 0))
        if state is None:
            remainder.append({**entry, "parent_status": "NOT_ATTEMPTED"})
            continue
        if state.get("outcome") != "HTTP_OK" or state.get("validation_status") is not None:
            failed.append(entry["id"])
            remainder.append({**entry, "parent_status": "FAILED"})
            continue
        raw = root / "working" / entry["id"] / "0.raw"
        parsed = core.parse_rows(raw.read_bytes(), entry["query"])
        if len(parsed) != entry["expected_rows"]:
            raise core.CorpusError("Issue #214 successful row count changed")
        symbol_rows = successful[entry["query"]["symbol"]]
        for row in parsed:
            if row.open_time_ms in symbol_rows and symbol_rows[row.open_time_ms] != row:
                raise core.CorpusError("Issue #214 successful responses conflict")
            symbol_rows[row.open_time_ms] = row

    if failed != ["ZAMAUSDT-0068"]:
        raise core.CorpusError("Issue #214 failed request identity changed")
    if len(remainder) != EXPECTED_REQUESTS:
        raise core.CorpusError("successor unresolved request count changed")
    counts = Counter(item["query"]["symbol"] for item in remainder)
    positions: Counter[str] = Counter()
    for item in remainder:
        positions[item["query"]["symbol"]] += item["expected_rows"]
    if counts != Counter(EXPECTED_SYMBOL_REQUESTS) or positions != Counter(
        EXPECTED_SYMBOL_POSITIONS
    ):
        raise core.CorpusError("successor unresolved inventory changed")
    if sum(positions.values()) != EXPECTED_POSITIONS:
        raise core.CorpusError("successor unresolved position total changed")
    if remainder[0]["id"] != "ZAMAUSDT-0068" or remainder[-1]["id"] != "LINKUSDT-0588":
        raise core.CorpusError("successor request order changed")

    reused = parent.predecessor_snapshot(repo)
    return {
        "plan": plan,
        "ledger": ledger,
        "reused": reused["reused"],
        "daily": reused["daily"],
        "parent_successful": successful,
        "remainder": remainder,
    }


def make_plan(repo: Path, source_revision: str) -> dict[str, Any]:
    snapshot = _parent_snapshot(repo)
    helpers = (SOURCE, CLI_SOURCE, WRAPPER_SOURCE, DOC)
    requests = [dict(item) for item in snapshot["remainder"]]
    reused_positions = sum(len(rows) for rows in snapshot["reused"].values())
    parent_captured_positions = sum(len(rows) for rows in snapshot["parent_successful"].values())
    if reused_positions != 234_956 or parent_captured_positions != 158_188:
        raise core.CorpusError("Issue #214 retained position counts changed")
    return {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "source_revision": source_revision,
        "source_hashes": {
            path.as_posix(): core.sha((repo / path).read_bytes().replace(b"\r\n", b"\n"))
            for path in helpers
        },
        "output_root": ROOT.as_posix(),
        "endpoint": parent.HOST + parent.ROUTE,
        "panel": list(parent.PANEL),
        "start": core.stamp(parent.START),
        "end": core.stamp(parent.END),
        "signal_start": core.stamp(parent.SIGNAL_START),
        "signal_end": core.stamp(parent.SIGNAL_END),
        "parent": {
            "root": PARENT_ROOT.as_posix(),
            "plan_sha256": PARENT_PLAN_SHA,
            "publication_root": PARENT_PUBLICATION_ROOT.as_posix(),
            "publication_manifest_sha256": PARENT_PUBLICATION_MANIFEST_SHA,
            "inventory_report_sha256": PARENT_INVENTORY_SHA,
            "capture_status": "ROUTE_STOPPED",
            "capture_exit_status": "CAPTURE_EXIT_UNKNOWN_NOT_RECORDED",
            "successful_requests": 1_648,
            "failed_requests": 1,
            "unattempted_requests": 1_784,
        },
        "requests": requests,
        "distinct_budget": EXPECTED_REQUESTS,
        "attempt_budget": EXPECTED_REQUESTS * 2,
        "expected_positions": EXPECTED_POSITIONS,
        "parent_reused_positions": reused_positions,
        "parent_captured_positions": parent_captured_positions,
        "parent_available_positions": reused_positions + parent_captured_positions,
        "minimum_seconds_between_attempts": 2,
        "dns_retry_delay_seconds": 5,
        "timeout_seconds": 20,
        "max_response_bytes": parent.MAX_BODY,
        "retry_policy": "timeout-http5xx-dns-once; valid-partial-or-empty-never-retried",
    }


def validate_plan(repo: Path, root: Path, plan: dict[str, Any]) -> None:
    if root.resolve() != (repo / ROOT).resolve():
        raise core.CorpusError("successor output root differs from frozen location")
    expected = make_plan(repo, plan.get("source_revision", ""))
    if plan != expected or (root / "plan.json").read_bytes() != core.canonical(plan):
        raise core.CorpusError("successor plan/source/predecessor identity changed")
    if len({item["id"] for item in plan["requests"]}) != EXPECTED_REQUESTS:
        raise core.CorpusError("successor request identities are not unique")


def validate_query(value: dict[str, str]) -> None:
    parent.validate_query(value)
    if value["symbol"] not in EXPECTED_SYMBOL_REQUESTS:
        raise core.CorpusError("query symbol is outside successor remainder")


def _network_classification(exc: BaseException) -> tuple[str, str | None, int | None]:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else None
    target = reason if reason is not None else exc
    errno = target.errno if isinstance(target, OSError) else None
    if isinstance(target, (TimeoutError, socket.timeout)):
        return "TIMEOUT", type(reason).__name__ if reason is not None else None, errno
    if isinstance(target, socket.gaierror):
        return "DNS_RESOLUTION", type(reason).__name__ if reason is not None else None, errno
    if (
        isinstance(target, ConnectionResetError)
        or target.__class__.__name__ == "RemoteDisconnected"
    ):
        return "REMOTE_DISCONNECTED", type(reason).__name__ if reason is not None else None, errno
    return "NETWORK_TERMINAL", type(reason).__name__ if reason is not None else None, errno


def _result_retryable(result: dict[str, Any], attempt: int) -> bool:
    if attempt != 0:
        return False
    status = result.get("status")
    if isinstance(status, int) and not isinstance(status, bool):
        return 500 <= status <= 599
    return result.get("transport_classification") in {"TIMEOUT", "DNS_RESOLUTION"}


def _validate_success_result(
    result: dict[str, Any], raw: bytes, entry: dict[str, Any]
) -> list[core.Row]:
    try:
        parsed = core.parse_rows(raw, entry["query"])
    except core.CorpusError as exc:
        if result.get("validation_status") == "CONTENT_TERMINAL" and result.get(
            "content_error"
        ) == str(exc):
            return []
        raise
    if result.get("validation_status") == "CONTENT_TERMINAL":
        raise core.CorpusError("success response incorrectly claims terminal content")
    expected = _expected_opens(entry)
    actual = [row.open_time_ms for row in parsed]
    missing = [opened for opened in expected if opened not in set(actual)]
    status = "COMPLETE" if not missing else "EMPTY" if not parsed else "PARTIAL"
    if result.get("validation_status") != status:
        raise core.CorpusError("success response validation status differs")
    if result.get("parsed_rows") != len(parsed) or result.get("missing_open_times_ms") != missing:
        raise core.CorpusError("success response gap record differs")
    return parsed


def audit_ledger(
    root: Path,
    plan: dict[str, Any],
    members_by_symbol: dict[str, dict[str, bytes]] | None = None,
) -> dict[str, Any]:
    plan_bytes = (root / "plan.json").read_bytes()
    if plan_bytes != core.canonical(plan):
        raise core.CorpusError("successor plan bytes differ")
    plan_sha = core.sha(plan_bytes)
    states: dict[tuple[str, int], dict[str, Any]] = {}
    allowed: set[str] = set()
    previous_finished: datetime | None = None
    retries = 0
    cursor_open = False
    for entry in plan["requests"]:
        previous: dict[str, Any] | None = None
        for attempt in (0, 1):
            stem = f"{entry['id']}/{attempt}"
            names = {
                suffix: f"{stem}{suffix}" for suffix in (".started.json", ".raw", ".result.json")
            }
            archive = (members_by_symbol or {}).get(entry["query"]["symbol"], {})
            records: dict[str, bytes] = {}
            for suffix, name in names.items():
                work = root / "working" / name
                if work.with_suffix(work.suffix + ".pending").exists():
                    raise core.CorpusError("ambiguous successor pending attempt")
                live = work.read_bytes() if work.exists() else None
                frozen = archive.get(name)
                if live is not None and frozen is not None and live != frozen:
                    raise core.CorpusError("successor working/archive copies disagree")
                body = frozen if frozen is not None else live
                if body is not None:
                    records[suffix] = body
                    allowed.add(name)
            if not records:
                if attempt == 0 or (previous is not None and _result_retryable(previous, 0)):
                    cursor_open = True
                continue
            if cursor_open:
                raise core.CorpusError("successor attempt appears after open cursor")
            if set(records) not in (
                {".started.json", ".result.json"},
                {".started.json", ".raw", ".result.json"},
            ):
                raise core.CorpusError("ambiguous successor STARTED/raw/result set")
            if attempt == 1 and (previous is None or not _result_retryable(previous, 0)):
                raise core.CorpusError("successor retry lacks eligible predecessor")
            if attempt == 1:
                retries += 1
            try:
                started = json.loads(records[".started.json"])
                result = json.loads(records[".result.json"])
            except (ValueError, UnicodeDecodeError) as exc:
                raise core.CorpusError("successor attempt JSON malformed") from exc
            if started != {
                "request_id": entry["id"],
                "attempt": attempt,
                "query": entry["query"],
                "started_at": started.get("started_at"),
                "plan_sha256": plan_sha,
            }:
                raise core.CorpusError("successor start differs from plan")
            began = core._utc_event(started["started_at"])
            ended = core._utc_event(result.get("completed_at"))
            if ended < began or (previous_finished is not None and began < previous_finished):
                raise core.CorpusError("successor attempt chronology differs")
            previous_finished = ended
            if result.get("request_id") != entry["id"] or result.get("attempt") != attempt:
                raise core.CorpusError("successor result identity differs")
            raw = records.get(".raw")
            if "status" in result:
                status = result["status"]
                if (
                    isinstance(status, bool)
                    or not isinstance(status, int)
                    or not 100 <= status <= 599
                ):
                    raise core.CorpusError("successor HTTP status malformed")
                expected_outcome = (
                    "HTTP_OK"
                    if status == 200
                    else "DENIED"
                    if status in {401, 403, 429}
                    else "POLICY_FAILURE"
                    if 300 <= status <= 399
                    else "HTTP_FAILURE"
                )
                if result.get("outcome") != expected_outcome or raw is None:
                    raise core.CorpusError("successor HTTP outcome/raw differs")
                if result.get("raw_sha256") != core.sha(raw) or result.get("raw_bytes") != len(raw):
                    raise core.CorpusError("successor raw hash/size differs")
                if status == 200:
                    _validate_success_result(result, raw, entry)
            else:
                if result.get("outcome") != "NETWORK_UNAVAILABLE" or raw is not None:
                    raise core.CorpusError("successor network outcome/raw differs")
                if result.get("transport_classification") not in ALLOWED_NETWORK_FAILURES | {
                    "NETWORK_TERMINAL"
                }:
                    raise core.CorpusError("successor network classification malformed")
            if result.get("retryable") is not _result_retryable(result, attempt):
                raise core.CorpusError("successor retry claim differs")
            states[(entry["id"], attempt)] = result
            previous = result
    working = root / "working"
    if working.exists():
        for path in working.rglob("*"):
            if path.is_file() and path.relative_to(working).as_posix() not in allowed:
                raise core.CorpusError("unplanned successor working evidence")
    if members_by_symbol is not None:
        for members in members_by_symbol.values():
            if set(members) - allowed:
                raise core.CorpusError("unplanned successor archive member")
    if len(states) > plan["attempt_budget"]:
        raise core.CorpusError("successor attempt budget exceeded")
    return {
        "states": states,
        "attempts": len(states),
        "distinct": len({request_id for request_id, _ in states}),
        "retries": retries,
        "last_completed_at": core.stamp(previous_finished) if previous_finished else None,
    }


def _final_state(
    entry: dict[str, Any], states: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any] | None:
    second = states.get((entry["id"], 1))
    return second or states.get((entry["id"], 0))


def _failure_streak(plan: dict[str, Any], states: dict[tuple[str, int], dict[str, Any]]) -> int:
    streak = 0
    for entry in plan["requests"]:
        state = _final_state(entry, states)
        if state is None:
            break
        if state.get("outcome") == "HTTP_OK":
            streak = 0
        elif (
            state.get("transport_classification")
            in {"TIMEOUT", "REMOTE_DISCONNECTED", "SERVER_TERMINAL"}
            or (state.get("status") is not None and 500 <= state["status"] <= 599)
        ) and not state.get("retryable"):
            streak += 1
    return streak


def _stop(root: Path, entry: dict[str, Any], result: dict[str, Any], reason: str) -> dict[str, Any]:
    core.durable_new(
        root / "route_stop.json",
        core.canonical(
            {
                "request_id": entry["id"],
                "outcome": reason,
                "at": result["completed_at"],
            }
        ),
    )
    return {"status": "ROUTE_STOPPED", "outcome": reason}


def _attempt(
    root: Path,
    plan_sha: str,
    entry: dict[str, Any],
    attempt: int,
    transport: Transport,
) -> dict[str, Any]:
    stem = root / "working" / entry["id"] / str(attempt)
    core.durable_new(
        stem.with_suffix(".started.json"),
        core.canonical(
            {
                "request_id": entry["id"],
                "attempt": attempt,
                "query": entry["query"],
                "started_at": core.now(),
                "plan_sha256": plan_sha,
            }
        ),
    )
    try:
        status, body = transport.get(entry["query"], 20)
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        classification, reason_class, errno = _network_classification(exc)
        result: dict[str, Any] = {
            "request_id": entry["id"],
            "attempt": attempt,
            "completed_at": core.now(),
            "outcome": "NETWORK_UNAVAILABLE",
            "retryable": attempt == 0 and classification in {"TIMEOUT", "DNS_RESOLUTION"},
            "transport_classification": classification,
            "error_class": type(exc).__name__,
            "reason_class": reason_class,
            "errno": errno,
            "diagnostic": str(exc)[:500],
        }
    else:
        if len(body) > parent.MAX_BODY:
            raise core.CorpusError("successor response exceeds body cap")
        core.durable_new(stem.with_suffix(".raw"), body)
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
            "request_id": entry["id"],
            "attempt": attempt,
            "completed_at": core.now(),
            "status": status,
            "outcome": outcome,
            "retryable": attempt == 0 and 500 <= status <= 599,
            "raw_sha256": core.sha(body),
            "raw_bytes": len(body),
        }
        if status == 200:
            try:
                parsed = core.parse_rows(body, entry["query"])
            except core.CorpusError as exc:
                result["validation_status"] = "CONTENT_TERMINAL"
                result["content_error"] = str(exc)
            else:
                actual = {row.open_time_ms for row in parsed}
                missing = [opened for opened in _expected_opens(entry) if opened not in actual]
                result["validation_status"] = (
                    "COMPLETE" if not missing else "EMPTY" if not parsed else "PARTIAL"
                )
                result["parsed_rows"] = len(parsed)
                result["missing_open_times_ms"] = missing
        elif 500 <= status <= 599:
            result["transport_classification"] = "SERVER_TERMINAL"
        else:
            result["transport_classification"] = "HTTP_TERMINAL"
    core.durable_new(stem.with_suffix(".result.json"), core.canonical(result))
    return result


def capture_requests(
    root: Path,
    plan: dict[str, Any],
    transport: Transport,
    *,
    sleep: Any = time.sleep,
    monotonic: Any = time.monotonic,
    wall_clock: Any = lambda: datetime.now(tz=UTC),
) -> dict[str, Any]:
    """Execute or cleanly resume one successor ledger under the frozen policy."""
    if any(
        (root / name).exists()
        for name in ("route_stop.json", "capture_complete.json", "manifest.json", "report.json")
    ):
        raise core.CorpusError("successor generation is stopped or finalized")
    ledger = audit_ledger(root, plan)
    states = ledger["states"]
    transmitted = 0
    terminal_streak = _failure_streak(plan, states)
    last_sent: float | None = None
    plan_sha = core.sha((root / "plan.json").read_bytes())
    if ledger["last_completed_at"] is not None:
        elapsed = (wall_clock() - core._utc_event(ledger["last_completed_at"])).total_seconds()
        if elapsed < 2.0:
            sleep(2.0 - elapsed)
    for entry in plan["requests"]:
        final = _final_state(entry, states)
        if final is not None and (
            final.get("outcome") == "HTTP_OK"
            or not final.get("retryable")
            or (entry["id"], 1) in states
        ):
            continue
        attempt = 1 if (entry["id"], 0) in states else 0
        prior = states.get((entry["id"], 0))
        if attempt == 1 and (prior is None or not prior.get("retryable")):
            raise core.CorpusError("successor retry lacks recorded authority")
        if attempt == 1 and prior.get("transport_classification") == "DNS_RESOLUTION":
            sleep(5.0)
        if last_sent is not None:
            wait = 2.0 - (monotonic() - last_sent)
            if wait > 0:
                sleep(wait)
        last_sent = monotonic()
        transmitted += 1
        result = _attempt(root, plan_sha, entry, attempt, transport)
        states[(entry["id"], attempt)] = result

        if result.get("outcome") == "HTTP_OK" and result.get("validation_status") in {
            "COMPLETE",
            "PARTIAL",
            "EMPTY",
        }:
            terminal_streak = 0
            continue
        if result.get("retryable"):
            # Re-enter this request immediately for its single authorized retry.
            if result.get("transport_classification") == "DNS_RESOLUTION":
                sleep(5.0)
            if last_sent is not None:
                wait = 2.0 - (monotonic() - last_sent)
                if wait > 0:
                    sleep(wait)
            attempt = 1
            last_sent = monotonic()
            transmitted += 1
            result = _attempt(root, plan_sha, entry, 1, transport)
            states[(entry["id"], 1)] = result
            if result.get("outcome") == "HTTP_OK" and result.get("validation_status") in {
                "COMPLETE",
                "PARTIAL",
                "EMPTY",
            }:
                terminal_streak = 0
                continue

        classification = str(
            result.get("validation_status")
            or result.get("transport_classification")
            or result.get("outcome")
        )
        if classification in {"CONTENT_TERMINAL", "HTTP_TERMINAL", "NETWORK_TERMINAL"}:
            stopped = _stop(root, entry, result, classification)
            return {**stopped, "new_attempts": transmitted}
        if classification == "DNS_RESOLUTION":
            stopped = _stop(root, entry, result, "DNS_RETRY_EXHAUSTED")
            return {**stopped, "new_attempts": transmitted}
        terminal_streak += 1
        if terminal_streak >= 3:
            stopped = _stop(root, entry, result, "THREE_CONSECUTIVE_TERMINAL_FAILURES")
            return {**stopped, "new_attempts": transmitted}

    marker = {
        "status": "CAPTURE_COMPLETE",
        "completed_at": core.now(),
        "attempts": len(states),
        "distinct": len({request_id for request_id, _ in states}),
    }
    core.durable_new(root / "capture_complete.json", core.canonical(marker))
    return {"status": "CAPTURE_COMPLETE", "new_attempts": transmitted}


def capture(
    repo: Path,
    root: Path,
    plan: dict[str, Any],
    transport: Transport | None = None,
    *,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    validate_plan(repo, root, plan)
    if len(plan["requests"]) != EXPECTED_REQUESTS or plan["attempt_budget"] != 3_570:
        raise core.CorpusError("capture requires exact successor inventory")
    return capture_requests(root, plan, transport or PublicTransport(), sleep=sleep)


def _pack(root: Path, plan: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for symbol in EXPECTED_SYMBOL_REQUESTS:
        target = root / "archives" / f"{symbol}.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise core.CorpusError("successor archive already exists")
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
        hashes[symbol] = core.sha(target.read_bytes())
    return hashes


def _members(root: Path, symbol: str, digest: str) -> dict[str, bytes]:
    path = root / "archives" / f"{symbol}.zip"
    if core.sha(path.read_bytes()) != digest:
        raise core.CorpusError("successor archive changed")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(".." in Path(name).parts for name in names):
            raise core.CorpusError("successor archive inventory unsafe")
        return {name: archive.read(name) for name in names}


def _successor_rows(
    plan: dict[str, Any],
    symbol: str,
    members: dict[str, bytes],
) -> tuple[
    dict[int, core.Row],
    Counter[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    rows: dict[int, core.Row] = {}
    counts: Counter[str] = Counter()
    valid_gaps: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    unattempted: list[dict[str, Any]] = []
    for entry in plan["requests"]:
        if entry["query"]["symbol"] != symbol:
            continue
        chosen: tuple[dict[str, Any], bytes | None] | None = None
        for attempt in (0, 1):
            name = f"{entry['id']}/{attempt}.result.json"
            if name not in members:
                break
            state = json.loads(members[name])
            raw = members.get(f"{entry['id']}/{attempt}.raw")
            chosen = (state, raw)
            if state.get("outcome") == "HTTP_OK" or not state.get("retryable"):
                break
        if chosen is None:
            counts["NOT_ATTEMPTED"] += 1
            unattempted.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "expected_rows": entry["expected_rows"],
                }
            )
            continue
        state, raw = chosen
        counts[state["outcome"]] += 1
        if (
            state.get("outcome") != "HTTP_OK"
            or state.get("validation_status") == "CONTENT_TERMINAL"
        ):
            if state.get("validation_status") == "CONTENT_TERMINAL":
                counts["CONTENT_TERMINAL"] += 1
            failures.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "expected_rows": entry["expected_rows"],
                    "result": state,
                }
            )
            continue
        if raw is None:
            raise core.CorpusError("successor success raw unavailable")
        parsed = _validate_success_result(state, raw, entry)
        missing = state["missing_open_times_ms"]
        if missing:
            valid_gaps.append(
                {"id": entry["id"], "query": entry["query"], "missing_open_times_ms": missing}
            )
        for row in parsed:
            if row.open_time_ms in rows and rows[row.open_time_ms] != row:
                raise core.CorpusError("successor rows conflict")
            rows[row.open_time_ms] = row
    return rows, counts, valid_gaps, failures, unattempted


def _exact_decimal_delta(left: Decimal, right: Decimal) -> Decimal:
    left_exponent = left.as_tuple().exponent
    right_exponent = right.as_tuple().exponent
    if not isinstance(left_exponent, int) or not isinstance(right_exponent, int):
        raise core.CorpusError("nonfinite Decimal cannot be compared")
    left_digits = len(left.as_tuple().digits) + abs(left_exponent)
    right_digits = len(right.as_tuple().digits) + abs(right_exponent)
    with localcontext() as context:
        context.prec = max(100, left_digits + right_digits + 10)
        return left - right


def daily_compare(five: dict[int, core.Row], daily: dict[int, core.Row]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    decimal_fields = (
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "quote_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    )
    for day in range(196):
        opened = core.utc_ms(parent.START + day * parent.DAY)
        parts = [five.get(opened + index * parent.STEP_MS) for index in range(288)]
        if any(row is None for row in parts):
            results.append({"day_open_ms": opened, "status": "5M_INCOMPLETE"})
            continue
        full = [row for row in parts if row is not None]
        derived = core.derive(
            {row.open_time_ms: row for row in full}, [(opened, opened + 86_400_000)], 288
        )[0]
        authority = daily[opened]
        equal = all(
            getattr(derived, field) == getattr(authority, field) for field in decimal_fields
        )
        equal = equal and derived.trade_count == authority.trade_count
        deltas = {
            field: str(_exact_decimal_delta(getattr(derived, field), getattr(authority, field)))
            for field in decimal_fields
        }
        deltas["trade_count"] = str(derived.trade_count - authority.trade_count)
        results.append(
            {
                "day_open_ms": opened,
                "status": "MATCH" if equal else "MISMATCH",
                "derived": {field: str(getattr(derived, field)) for field in decimal_fields}
                | {"trade_count": derived.trade_count},
                "authority": {field: str(getattr(authority, field)) for field in decimal_fields}
                | {"trade_count": authority.trade_count},
                "signed_deltas": deltas,
            }
        )
    return results


def _gap_intervals(opens: list[int]) -> list[dict[str, int]]:
    if not opens:
        return []
    values = sorted(set(opens))
    intervals: list[dict[str, int]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + parent.STEP_MS:
            intervals.append(
                {
                    "start_ms": start,
                    "end_ms": previous + parent.STEP_MS,
                    "rows": (previous - start) // parent.STEP_MS + 1,
                }
            )
            start = value
        previous = value
    intervals.append(
        {
            "start_ms": start,
            "end_ms": previous + parent.STEP_MS,
            "rows": (previous - start) // parent.STEP_MS + 1,
        }
    )
    return intervals


def project(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    members_by_symbol: dict[str, dict[str, bytes]],
    capture_status: str,
) -> tuple[dict[str, Any], dict[tuple[str, str], list[core.Row]]]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "panel": list(parent.PANEL),
        "start": core.stamp(parent.START),
        "end": core.stamp(parent.END),
        "signal_window": {
            "start": core.stamp(parent.SIGNAL_START),
            "end": core.stamp(parent.SIGNAL_END),
        },
        "capture_status": capture_status,
        "symbols": {},
    }
    series: dict[tuple[str, str], list[core.Row]] = {}
    for symbol in parent.PANEL:
        successor, counts, valid_gaps, failures, unattempted = _successor_rows(
            plan, symbol, members_by_symbol.get(symbol, {})
        )
        five = dict(snapshot["reused"].get(symbol, {}))
        for source_rows in (snapshot["parent_successful"].get(symbol, {}), successor):
            for opened, row in source_rows.items():
                if opened in five and five[opened] != row:
                    raise core.CorpusError("combined authorities conflict")
                five[opened] = row
        spans = core.epochs(five)
        frames = {"5m": [five[key] for key in sorted(five)]}
        frames.update(
            {
                name: core.derive(five, spans, size)
                for name, size in (("15m", 3), ("1h", 12), ("4h", 48))
            }
        )
        comparisons = daily_compare(five, snapshot["daily"][symbol])
        expected = set(range(core.utc_ms(parent.START), core.utc_ms(parent.END), parent.STEP_MS))
        missing = sorted(expected - set(five))
        status = (
            "QUALIFIED"
            if not missing and all(item["status"] == "MATCH" for item in comparisons)
            else "PARTIAL_OR_BLOCKED"
        )
        warm = parent._warmup(frames, spans)
        report["symbols"][symbol] = {
            "status": status,
            "expected_5m_rows": parent.EXPECTED_ROWS,
            "reused_5m_rows": len(snapshot["reused"].get(symbol, {})),
            "parent_captured_5m_rows": len(snapshot["parent_successful"].get(symbol, {})),
            "successor_5m_rows": len(successor),
            "valid_5m_rows": len(five),
            "missing_5m_rows": len(missing),
            "missing_intervals": _gap_intervals(missing),
            "valid_response_gaps": valid_gaps,
            "failed_requests": failures,
            "unattempted_requests": unattempted,
            "epochs": [
                {"start_ms": start, "end_ms": end, "rows": (end - start) // parent.STEP_MS}
                for start, end in spans
            ],
            "derived_rows": {name: len(rows) for name, rows in frames.items() if name != "5m"},
            "usable_intervals": warm,
            "daily_comparisons": comparisons,
            "request_counts": dict(sorted(counts.items())),
            "signal_and_168h_followup_supported": status == "QUALIFIED",
        }
        for frame, rows in frames.items():
            series[(symbol, frame)] = rows
    report["status"] = (
        "QUALIFIED"
        if all(item["status"] == "QUALIFIED" for item in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED"
    )
    report["signal_and_168h_followup_supported"] = report["status"] == "QUALIFIED"
    return report, series


def _capture_status(root: Path) -> str:
    if (root / "capture_complete.json").is_file():
        return "CAPTURE_COMPLETE"
    if (root / "route_stop.json").is_file():
        return "ROUTE_STOPPED"
    return "CAPTURE_PARTIAL_INTERRUPTED"


def _build_from_snapshot(
    root: Path, plan: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    if (
        (root / "manifest.json").exists()
        or (root / "report.json").exists()
        or (root / "archives").exists()
        or (root / "parquet").exists()
    ):
        raise core.CorpusError("successor outputs already exist")
    ledger = audit_ledger(root, plan)
    archive_hashes = _pack(root, plan)
    members = {symbol: _members(root, symbol, digest) for symbol, digest in archive_hashes.items()}
    ledger = audit_ledger(root, plan, members)
    report, series = project(snapshot, plan, members, _capture_status(root))
    files = {f"archives/{symbol}.zip": digest for symbol, digest in archive_hashes.items()}
    for (symbol, frame), rows in series.items():
        path = root / "parquet" / f"{symbol}-{frame}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(core._table(symbol, frame, rows), path, compression="zstd", version="2.6")
        files[path.relative_to(root).as_posix()] = core.sha(path.read_bytes())
    core.durable_new(root / "report.json", core.canonical(report))
    files["report.json"] = core.sha((root / "report.json").read_bytes())
    manifest = {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "dataset_version": DATASET_VERSION,
        "status": report["status"],
        "capture_status": report["capture_status"],
        "plan_sha256": core.sha((root / "plan.json").read_bytes()),
        "parent": plan["parent"],
        "ledger": {key: ledger[key] for key in ("attempts", "distinct", "retries")},
        "files": dict(sorted(files.items())),
    }
    core.durable_new(root / "manifest.json", core.canonical(manifest))
    return {
        "status": report["status"],
        "capture_status": report["capture_status"],
        "manifest_sha256": core.sha((root / "manifest.json").read_bytes()),
        "report_sha256": files["report.json"],
    }


def build(repo: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(repo, root, plan)
    return _build_from_snapshot(root, plan, _parent_snapshot(repo))


def _verify_from_snapshot(
    root: Path, plan: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    manifest: dict[str, Any] = core.read_json(root / "manifest.json")
    if (
        manifest.get("plan_sha256") != core.sha((root / "plan.json").read_bytes())
        or manifest.get("parent") != plan["parent"]
    ):
        raise core.CorpusError("successor manifest provenance differs")
    for name, digest in manifest["files"].items():
        path = root / name
        if not path.is_file() or core.sha(path.read_bytes()) != digest:
            raise core.CorpusError(f"successor artifact changed: {name}")
    members = {
        symbol: _members(root, symbol, manifest["files"][f"archives/{symbol}.zip"])
        for symbol in EXPECTED_SYMBOL_REQUESTS
    }
    ledger = audit_ledger(root, plan, members)
    report, series = project(snapshot, plan, members, manifest["capture_status"])
    if (
        core.canonical(report) != (root / "report.json").read_bytes()
        or manifest["status"] != report["status"]
    ):
        raise core.CorpusError("successor report differs from raw reconstruction")
    for (symbol, frame), rows in series.items():
        table = pq.read_table(root / "parquet" / f"{symbol}-{frame}.parquet")
        if not table.equals(core._table(symbol, frame, rows), check_metadata=True):
            raise core.CorpusError("successor Parquet differs from raw reconstruction")
    return {
        "status": report["status"],
        "capture_status": report["capture_status"],
        "manifest_sha256": core.sha((root / "manifest.json").read_bytes()),
        "ledger": {key: ledger[key] for key in ("attempts", "distinct", "retries")},
    }


def verify(repo: Path, root: Path) -> dict[str, Any]:
    plan: dict[str, Any] = core.read_json(root / "plan.json")
    validate_plan(repo, root, plan)
    return _verify_from_snapshot(root, plan, _parent_snapshot(repo))
