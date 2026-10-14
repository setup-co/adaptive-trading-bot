"""Source-frozen, separate Issue #179 C1 continuation and combined data projection.

Only never-attempted shapes from the immutable first generation may be sent.
This is an offline research-data tool, not a strategy or executable source.
"""

from __future__ import annotations

import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.research import research_v2_corpus as c

ROOT = Path("data/research_corpus/issue-179-weex-last-90d-continuation-v1")
ORIGINAL_ROOT = Path(c.OUTPUT_ROOT)
SOURCE = Path("src/adaptive_trading_bot/research/research_v2_corpus.py")
THIS_SOURCE = Path("src/adaptive_trading_bot/research/research_v2_corpus_continuation.py")
CLI_SOURCE = Path("scripts/research/research_v2_corpus_continuation.py")
DOCS = Path("docs/research/research_v2_corpus_qualification.md")
CONTINUATION_SCHEMA = "research-v2-weex-last-90d-continuation-v1"
COMBINED_VERSION = "weex-research-v2-last-90d-issue-179-combined-v1"
ORIGINAL_MANIFEST_SHA = "870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3"
ORIGINAL_REPORT_SHA = "d71e0cf14e3a21ab5996e7debae6808dd3e6c534dc6ee14e39d2e27f511750cd"
FAILED_ORIGINAL_ID = "BTCUSDT-04-2"


def _files_sha(repo: Path) -> dict[str, str]:
    return {
        path.as_posix(): c.sha((repo / path).read_bytes().replace(b"\r\n", b"\n"))
        for path in (SOURCE, THIS_SOURCE, CLI_SOURCE)
    }


def _original(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = repo / ORIGINAL_ROOT
    if c.sha((root / "plan.json").read_bytes()) != c.ORIGINAL_PLAN_SHA:
        raise c.CorpusError("original plan identity changed")
    if c.sha((root / "manifest.json").read_bytes()) != ORIGINAL_MANIFEST_SHA:
        raise c.CorpusError("original manifest identity changed")
    if c.sha((root / "report.json").read_bytes()) != ORIGINAL_REPORT_SHA:
        raise c.CorpusError("original report identity changed")
    verified = c.verify(repo, root, repo / SOURCE, repo / DOCS)
    plan = c.read_json(root / "plan.json")
    archives = {
        symbol: c._archive_members(
            root, symbol, c.read_json(root / "manifest.json")["files"][f"archives/{symbol}.zip"]
        )
        for symbol in c.PANEL
    }
    ledger = c.audit_ledger(root, plan, archives)
    states = ledger["states"]
    if (
        verified["manifest_sha256"] != ORIGINAL_MANIFEST_SHA
        or ledger["attempts"] != 15
        or ledger["distinct"] != 15
        or ledger["retries"] != 0
        or states[(FAILED_ORIGINAL_ID, 0)]["outcome"] != "NETWORK_UNAVAILABLE"
        or states[(FAILED_ORIGINAL_ID, 0)]["error_class"] != "RemoteDisconnected"
        or sum(state["outcome"] == "HTTP_OK" for state in states.values()) != 14
    ):
        raise c.CorpusError("original stopped ledger no longer has the accepted 14+1 facts")
    return plan, {"verified": verified, "ledger": ledger, "archives": archives}


def make_plan(repo: Path, source_revision: str) -> dict[str, Any]:
    original, audit = _original(repo)
    attempted = {key[0] for key in audit["ledger"]["states"]}
    pending = [entry for entry in original["requests"] if entry["id"] not in attempted]
    if len(pending) != 2689 or FAILED_ORIGINAL_ID in {entry["id"] for entry in pending}:
        raise c.CorpusError("C1 must contain exactly 2,689 never-attempted original queries")
    return {
        "schema": CONTINUATION_SCHEMA,
        "dataset_version": COMBINED_VERSION,
        "source_revision": source_revision,
        "source_sha256": _files_sha(repo),
        "original_plan_sha256": c.ORIGINAL_PLAN_SHA,
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA,
        "original_report_sha256": ORIGINAL_REPORT_SHA,
        "original_attempts": 15,
        "original_distinct": 15,
        "original_retries": 0,
        "requests": pending,
        "distinct_budget": 2689,
        "attempt_budget": 5378,
        "cumulative_distinct_budget": 2704,
        "cumulative_attempt_budget": 5393,
        "endpoint": c.HOST + c.ROUTE,
        "price_type": "LAST",
        "panel": list(c.PANEL),
        "start": c.stamp(c.START),
        "end": c.stamp(c.END),
        "minimum_seconds_between_attempts": 1,
        "timeout_seconds": 20,
        "max_response_bytes": c.MAX_BODY,
        "max_consecutive_terminal_transport_failures": 3,
        "retry_policy": "one retry only for timeout or HTTP 5xx",
    }


def validate_plan(repo: Path, root: Path, plan: dict[str, Any]) -> None:
    if root.resolve() != (repo / ROOT).resolve():
        raise c.CorpusError("continuation root differs from frozen separate location")
    if (root / "plan.json").read_bytes() != c.canonical(plan):
        raise c.CorpusError("continuation plan bytes are not canonical")
    if not isinstance(plan.get("source_revision"), str) or len(plan["source_revision"]) != 40:
        raise c.CorpusError("continuation source revision malformed")
    if plan != make_plan(repo, plan["source_revision"]):
        raise c.CorpusError("continuation plan/source/original ledger mismatch")
    if any(entry["id"] == FAILED_ORIGINAL_ID for entry in plan["requests"]):
        raise c.CorpusError("completed original failed chunk cannot be retried")
    for entry in plan["requests"]:
        c.validate_market_query(entry["query"])


def _terminal_transport(state: dict[str, Any]) -> bool:
    return (
        state["outcome"] == "NETWORK_UNAVAILABLE"
        and not c._record_retryable(state)
        and state.get("error_class") == "RemoteDisconnected"
    )


def _route_state(
    states: dict[tuple[str, int], dict[str, Any]], requests: list[dict[str, Any]]
) -> tuple[int, bool]:
    failures = 0
    for entry in requests:
        attempts = [states.get((entry["id"], number)) for number in (0, 1)]
        terminal = attempts[1] or attempts[0]
        if terminal is None:
            break
        if terminal["outcome"] == "HTTP_OK":
            failures = 0
        elif _terminal_transport(terminal):
            failures += 1
            if failures >= 3:
                return failures, True
        elif c._record_retryable(terminal) and attempts[1] is None:
            break
        elif terminal["outcome"] in {
            "DENIED",
            "POLICY_FAILURE",
            "NETWORK_UNAVAILABLE",
            "HTTP_FAILURE",
        }:
            return failures, True
        else:
            failures = 0
    return failures, False


def capture(
    repo: Path,
    root: Path,
    plan: dict[str, Any],
    transport: c.Transport | None = None,
    *,
    sleep: Any = None,
) -> dict[str, int | str]:
    """One frozen C1 route; no network before complete old/new ledger validation."""
    validate_plan(repo, root, plan)
    snapshot = c.audit_ledger(root, plan)
    states = snapshot["states"]
    if snapshot["attempts"] + 15 > 5393:
        raise c.CorpusError("cumulative budget exceeded")
    consecutive, stopped = _route_state(states, plan["requests"])
    if stopped or (root / "route_stop.json").exists():
        return {"status": "ROUTE_STOPPED", "new_attempts": 0}
    transport = transport or c.PublicHistoryTransport()
    sleep = sleep or time.sleep
    transmitted = 0
    last_sent: float | None = None
    prior_starts = [
        c._utc_event(
            c.read_json(root / "working" / request_id / f"{number}.started.json")["started_at"]
        )
        for request_id, number in states
        if (root / "working" / request_id / f"{number}.started.json").exists()
    ]
    previous_wall_start = max(prior_starts) if prior_starts else None
    for entry in plan["requests"]:
        request_id, query = entry["id"], entry["query"]
        first = states.get((request_id, 0))
        second = states.get((request_id, 1))
        if second is not None or (first is not None and not c._record_retryable(first)):
            continue
        for number in (0, 1):
            if states.get((request_id, number)) is not None:
                continue
            if number == 1 and (first is None or not c._record_retryable(first)):
                break
            if snapshot["attempts"] + transmitted + 15 >= 5393:
                raise c.CorpusError("cumulative attempt budget exhausted")
            stem = root / "working" / request_id / str(number)
            if last_sent is not None:
                delay = 1.0 - (time.monotonic() - last_sent)
            elif previous_wall_start is not None:
                delay = 1.0 - (datetime.now(UTC) - previous_wall_start).total_seconds()
            else:
                delay = 0.0
            if delay > 0:
                sleep(delay)
            c.durable_new(
                stem.with_suffix(".started.json"),
                c.canonical(
                    {
                        "request_id": request_id,
                        "attempt": number,
                        "query": query,
                        "started_at": c.now(),
                        "plan_sha256": c.sha((root / "plan.json").read_bytes()),
                    }
                ),
            )
            last_sent = time.monotonic()
            transmitted += 1
            try:
                status, body = transport.get(query, 20)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                result: dict[str, Any] = {
                    "request_id": request_id,
                    "attempt": number,
                    "completed_at": c.now(),
                    "outcome": "NETWORK_UNAVAILABLE",
                    "retryable": c.retryable(error=exc),
                    "error_class": type(exc).__name__,
                    "reason_class": type(exc.reason).__name__
                    if isinstance(exc, urllib.error.URLError)
                    else None,
                    "errno": exc.errno if isinstance(exc, OSError) else None,
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
                    "attempt": number,
                    "completed_at": c.now(),
                    "status": status,
                    "outcome": outcome,
                    "retryable": c.retryable(status=status),
                    "raw_sha256": c.sha(body),
                    "raw_bytes": len(body),
                }
            c.durable_new(stem.with_suffix(".result.json"), c.canonical(result))
            states[(request_id, number)] = result
            if result["outcome"] == "HTTP_OK":
                consecutive = 0
                break
            if c._record_retryable(result) and number == 0:
                first = result
                continue
            if _terminal_transport(result):
                consecutive += 1
                if consecutive >= 3:
                    c.durable_new(
                        root / "route_stop.json",
                        c.canonical(
                            {
                                "reason": "THREE_CONSECUTIVE_TERMINAL_TRANSPORT_FAILURES",
                                "last_request_id": request_id,
                            }
                        ),
                    )
                    return {"status": "ROUTE_STOPPED", "new_attempts": transmitted}
                break
            if result["outcome"] != "HTTP_OK":
                c.durable_new(
                    root / "route_stop.json",
                    c.canonical(
                        {
                            "reason": result["outcome"],
                            "last_request_id": request_id,
                        }
                    ),
                )
                return {"status": result["outcome"], "new_attempts": transmitted}
    return {"status": "C1_COMPLETE", "new_attempts": transmitted}


def _all_members(
    repo: Path, root: Path, plan: dict[str, Any], hashes: dict[str, str]
) -> dict[str, dict[str, bytes]]:
    original_root = repo / ORIGINAL_ROOT
    original_manifest = c.read_json(original_root / "manifest.json")
    merged: dict[str, dict[str, bytes]] = {}
    original_archives: dict[str, dict[str, bytes]] = {}
    new_archives: dict[str, dict[str, bytes]] = {}
    for symbol in c.PANEL:
        old = c._archive_members(
            original_root, symbol, original_manifest["files"][f"archives/{symbol}.zip"]
        )
        new = c._archive_members(root, symbol, hashes[symbol])
        if set(old) & set(new):
            raise c.CorpusError("continuation overlaps an original attempted request")
        original_archives[symbol] = old
        new_archives[symbol] = new
        merged[symbol] = {**old, **new}
    c.audit_ledger(original_root, c.read_json(original_root / "plan.json"), original_archives)
    c.audit_ledger(root, plan, new_archives)
    return merged


def _projection(
    repo: Path, root: Path, merged: dict[str, dict[str, bytes]]
) -> tuple[dict[str, Any], dict[tuple[str, str], list[c.Row]]]:
    original_root = repo / ORIGINAL_ROOT
    original_plan = c.read_json(original_root / "plan.json")
    report: dict[str, Any] = {
        "schema": CONTINUATION_SCHEMA,
        "dataset_version": COMBINED_VERSION,
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA,
        "source": c.HOST + c.ROUTE,
        "price_type": "LAST",
        "start": c.stamp(c.START),
        "end": c.stamp(c.END),
        "panel": list(c.PANEL),
        "symbols": {},
    }
    series: dict[tuple[str, str], list[c.Row]] = {}
    for symbol in c.PANEL:
        item, frames = c._report_symbol(repo, original_root, original_plan, symbol, merged[symbol])
        report["symbols"][symbol] = item
        for frame, rows in frames.items():
            series[(symbol, frame)] = rows
    report["ten_member_usable_overlap"] = c._overlap(report["symbols"])
    return report, series


def build(repo: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(repo, root, plan)
    c.audit_ledger(root, plan)
    if (root / "manifest.json").exists() or (root / "combined").exists():
        raise c.CorpusError("continuation projection already exists")
    archive_hashes = c.pack_attempts(root, plan)
    merged = _all_members(repo, root, plan, archive_hashes)
    report, series = _projection(repo, root, merged)
    hashes: dict[str, str] = {
        f"archives/{symbol}.zip": digest for symbol, digest in archive_hashes.items()
    }
    for (symbol, frame), rows in series.items():
        path = root / "combined" / "parquet" / f"{symbol}-{frame}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(c._table(symbol, frame, rows), path, compression="zstd", version="2.6")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise c.CorpusError("combined Parquet shard exceeds 16 MiB")
        hashes[path.relative_to(root).as_posix()] = c.sha(path.read_bytes())
    c.durable_new(root / "combined" / "report.json", c.canonical(report))
    hashes["combined/report.json"] = c.sha((root / "combined" / "report.json").read_bytes())
    ledger = c.audit_ledger(
        root,
        plan,
        {symbol: c._archive_members(root, symbol, archive_hashes[symbol]) for symbol in c.PANEL},
    )
    manifest = {
        "schema": CONTINUATION_SCHEMA,
        "dataset_version": COMBINED_VERSION,
        "status": "CORPUS_REVIEW_READY"
        if all(item["status"] == "CORPUS_REVIEW_READY" for item in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED",
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA,
        "original_report_sha256": ORIGINAL_REPORT_SHA,
        "original_plan_sha256": c.ORIGINAL_PLAN_SHA,
        "plan_sha256": c.sha((root / "plan.json").read_bytes()),
        "source_sha256": plan["source_sha256"],
        "ledger": {key: ledger[key] for key in ("distinct", "attempts", "retries")},
        "cumulative": {
            "distinct": 15 + ledger["distinct"],
            "attempts": 15 + ledger["attempts"],
            "retries": ledger["retries"],
        },
        "files": dict(sorted(hashes.items())),
    }
    c.durable_new(root / "manifest.json", c.canonical(manifest))
    size = sum(
        p.stat().st_size
        for base in (repo / ORIGINAL_ROOT, root)
        for p in base.rglob("*")
        if p.is_file()
    )
    if size > 96 * 1024 * 1024:
        raise c.CorpusError("combined original+continuation evidence exceeds 96 MiB")
    return {
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "status": manifest["status"],
        "bytes": size,
    }


def verify(repo: Path, root: Path) -> dict[str, Any]:
    plan = c.read_json(root / "plan.json")
    validate_plan(repo, root, plan)
    manifest = c.read_json(root / "manifest.json")
    if (
        manifest.get("schema") != CONTINUATION_SCHEMA
        or manifest.get("dataset_version") != COMBINED_VERSION
        or manifest.get("original_manifest_sha256") != ORIGINAL_MANIFEST_SHA
        or manifest.get("original_report_sha256") != ORIGINAL_REPORT_SHA
        or manifest.get("original_plan_sha256") != c.ORIGINAL_PLAN_SHA
        or manifest.get("plan_sha256") != c.sha((root / "plan.json").read_bytes())
        or manifest.get("source_sha256") != plan["source_sha256"]
    ):
        raise c.CorpusError("combined manifest provenance mismatch")
    expected_files = (
        {"combined/report.json"}
        | {f"archives/{symbol}.zip" for symbol in c.PANEL}
        | {
            f"combined/parquet/{symbol}-{frame}.parquet"
            for symbol in c.PANEL
            for frame in ("5m", "15m", "1h", "4h")
        }
    )
    if set(manifest.get("files", {})) != expected_files:
        raise c.CorpusError("combined manifest file inventory mismatch")
    for name, digest in manifest["files"].items():
        if c.sha((root / name).read_bytes()) != digest:
            raise c.CorpusError("combined artifact hash mismatch: " + name)
    archive_hashes = {symbol: manifest["files"][f"archives/{symbol}.zip"] for symbol in c.PANEL}
    new_archives = {
        symbol: c._archive_members(root, symbol, archive_hashes[symbol]) for symbol in c.PANEL
    }
    ledger = c.audit_ledger(root, plan, new_archives)
    if manifest["ledger"] != {key: ledger[key] for key in ("distinct", "attempts", "retries")}:
        raise c.CorpusError("combined ledger count mismatch")
    if manifest["cumulative"] != {
        "distinct": 15 + ledger["distinct"],
        "attempts": 15 + ledger["attempts"],
        "retries": ledger["retries"],
    }:
        raise c.CorpusError("combined cumulative ledger mismatch")
    merged = _all_members(repo, root, plan, archive_hashes)
    report, series = _projection(repo, root, merged)
    if c.canonical(report) != (root / "combined" / "report.json").read_bytes():
        raise c.CorpusError("combined report differs from authoritative raw")
    for (symbol, frame), rows in series.items():
        table = pq.read_table(root / "combined" / "parquet" / f"{symbol}-{frame}.parquet")
        if not table.equals(c._table(symbol, frame, rows), check_metadata=True):
            raise c.CorpusError("combined Parquet differs from authoritative raw")
    status = (
        "CORPUS_REVIEW_READY"
        if all(item["status"] == "CORPUS_REVIEW_READY" for item in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED"
    )
    if manifest["status"] != status:
        raise c.CorpusError("combined status differs from raw")
    return {
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "status": status,
        "ledger": manifest["ledger"],
        "report": report,
    }


class LockedResearchV2CombinedReader(c.LockedResearchV2CorpusReader):
    """Verified in-memory combined data epochs only; not executable qualification."""

    def __init__(
        self, repo: Path, root: Path, *, dataset_version: str, manifest_sha256: str
    ) -> None:
        if (
            dataset_version != COMBINED_VERSION
            or c.sha((root / "manifest.json").read_bytes()) != manifest_sha256
        ):
            raise c.CorpusError("unknown or changed combined dataset version")
        verified = verify(repo, root)
        manifest = c.read_json(root / "manifest.json")
        self._verified = {"symbols": verified["report"]["symbols"]}
        self._tables: dict[tuple[str, str], pa.Table] = {}
        for symbol in c.PANEL:
            for frame in ("5m", "15m", "1h", "4h"):
                name = f"combined/parquet/{symbol}-{frame}.parquet"
                body = (root / name).read_bytes()
                if c.sha(body) != manifest["files"][name]:
                    raise c.CorpusError("combined shard changed during snapshot construction")
                self._tables[(symbol, frame)] = pq.read_table(pa.BufferReader(body))
