"""Issue #181 C2: only the never-attempted tail of the frozen Research V2 plan.

This is a data-only public-history adapter. It never admits a research symbol
to the executable source selector and never treats a failed predecessor query
as permission to retry it.
"""

from __future__ import annotations

import time
import urllib.error
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_continuation as x

ROOT = Path("data/research_corpus/issue-179-weex-last-90d-tail-v1")
SCHEMA = "research-v2-weex-last-90d-tail-v1"
VERSION = "weex-research-v2-last-90d-issue-179-combined-v2"
SOURCE = Path("src/adaptive_trading_bot/research/research_v2_corpus_tail.py")
CLI_SOURCE = Path("scripts/research/research_v2_corpus_tail.py")
C1_PLAN_SHA = x.CAPTURED_C1_PLAN_SHA
C1_MANIFEST_SHA = "1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed"
C1_REPORT_SHA = "b2ea8a706972e8355ceed410a73c21262e1b57d08528dbbc7f9ee52468f1ee45"
C1_STOP_SHA = "3b2886ae62205e4309da50b8fe0d64481b4f54589c7708dac14db4e18533eb0c"
FAILED_IDS = frozenset((x.FAILED_ORIGINAL_ID, "BTWUSDT-09-0"))
TAIL_SYMBOLS = frozenset(("BTWUSDT", "AVAXUSDT", "DOGEUSDT"))
MAX_RETAINED_BYTES = 96 * 1024 * 1024
BUILD_HEADROOM_BYTES = 16 * 1024 * 1024


def _source_hashes(repo: Path) -> dict[str, str]:
    return {
        path.as_posix(): c.sha((repo / path).read_bytes().replace(b"\r\n", b"\n"))
        for path in (x.SOURCE, x.THIS_SOURCE, SOURCE, CLI_SOURCE)
    }


def _predecessors(repo: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Verify both immutable generations and their actual archived attempt IDs."""
    original, accepted = x._original(repo)
    root = repo / x.ROOT
    for name, digest in (
        ("plan.json", C1_PLAN_SHA),
        ("manifest.json", C1_MANIFEST_SHA),
        ("combined/report.json", C1_REPORT_SHA),
        ("route_stop.json", C1_STOP_SHA),
    ):
        if c.sha((root / name).read_bytes()) != digest:
            raise c.CorpusError("C1 predecessor identity changed: " + name)
    checked = x.verify(repo, root)
    if checked["manifest_sha256"] != C1_MANIFEST_SHA:
        raise c.CorpusError("C1 predecessor verification changed")
    plan = c.read_json(root / "plan.json")
    manifest = c.read_json(root / "manifest.json")
    archives = {
        symbol: c._archive_members(root, symbol, manifest["files"][f"archives/{symbol}.zip"])
        for symbol in c.PANEL
    }
    ledger = c.audit_ledger(root, plan, archives)
    x._validate_route_stop(root, ledger["states"], plan["requests"])
    states = ledger["states"]
    stopped = states.get(("BTWUSDT-09-0", 0))
    if (
        accepted["ledger"]["attempts"] != 15
        or ledger["attempts"] != 1904
        or ledger["distinct"] != 1904
        or ledger["retries"] != 0
        or stopped is None
        or stopped["outcome"] != "NETWORK_UNAVAILABLE"
        or stopped.get("error_class") != "URLError"
        or stopped.get("reason_class") != "gaierror"
        or sum(row["outcome"] == "HTTP_OK" for row in states.values()) != 1903
    ):
        raise c.CorpusError("C1 stopped archive facts differ from accepted 1903+1")
    return original, accepted["ledger"], ledger


def make_plan(repo: Path, source_revision: str) -> dict[str, Any]:
    original, first, second = _predecessors(repo)
    attempted = {request_id for request_id, _ in first["states"]}
    attempted.update(request_id for request_id, _ in second["states"])
    pending = [entry for entry in original["requests"] if entry["id"] not in attempted]
    counts = Counter(entry["query"]["symbol"] for entry in pending)
    if (
        len(attempted) != 1919
        or len(pending) != 785
        or pending[0]["id"] != "BTWUSDT-09-1"
        or pending[-1]["id"] != "DOGEUSDT-earlier-daily"
        or counts != {"BTWUSDT": 243, "AVAXUSDT": 271, "DOGEUSDT": 271}
        or FAILED_IDS & {entry["id"] for entry in pending}
        or any(entry["query"]["symbol"] not in TAIL_SYMBOLS for entry in pending)
    ):
        raise c.CorpusError("tail is not the exact 785 never-attempted IDs")
    if sum(entry["query"]["interval"] == "5m" for entry in pending) != 782:
        raise c.CorpusError("tail 5m/daily split changed")
    return {
        "schema": SCHEMA,
        "dataset_version": VERSION,
        "authorization": "ISSUE_181_C2_UNATTEMPTED_TAIL_ONLY",
        "output_root": ROOT.as_posix(),
        "source_revision": source_revision,
        "source_sha256": _source_hashes(repo),
        "original_plan_sha256": c.ORIGINAL_PLAN_SHA,
        "original_manifest_sha256": x.ORIGINAL_MANIFEST_SHA,
        "original_report_sha256": x.ORIGINAL_REPORT_SHA,
        "c1_plan_sha256": C1_PLAN_SHA,
        "c1_manifest_sha256": C1_MANIFEST_SHA,
        "c1_report_sha256": C1_REPORT_SHA,
        "c1_stop_sha256": C1_STOP_SHA,
        "predecessor_attempts": 1919,
        "predecessor_distinct": 1919,
        "predecessor_retries": 0,
        "excluded_predecessor_failed_ids": sorted(FAILED_IDS),
        "requests": pending,
        "distinct_budget": 785,
        "attempt_budget": 1570,
        "cumulative_attempt_budget": 3489,
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
        raise c.CorpusError("C2 root differs from frozen separate location")
    if (root / "plan.json").read_bytes() != c.canonical(plan):
        raise c.CorpusError("C2 plan bytes are not canonical")
    revision = plan.get("source_revision")
    if not isinstance(revision, str) or len(revision) != 40:
        raise c.CorpusError("C2 source revision malformed")
    if plan != make_plan(repo, revision):
        raise c.CorpusError("C2 plan/source/predecessor mismatch")
    for entry in plan["requests"]:
        c.validate_market_query(entry["query"])


def _capture_closed(root: Path) -> bool:
    return (
        (root / "manifest.json").exists()
        or (root / "combined").exists()
        or any((root / "archives").glob("*.zip"))
    )


def capture(
    repo: Path,
    root: Path,
    plan: dict[str, Any],
    transport: c.Transport | None = None,
    *,
    sleep: Any = None,
) -> dict[str, int | str]:
    """Transmit only the frozen C2 tail after full offline predecessor/ledger checks."""
    validate_plan(repo, root, plan)
    if _capture_closed(root):
        raise c.CorpusError("finalized or archived C2 generation is capture-closed")
    snapshot = c.audit_ledger(root, plan)
    states = snapshot["states"]
    if snapshot["attempts"] + 1919 > 3489:
        raise c.CorpusError("C2 cumulative attempt budget exceeded")
    retained = _retained_bytes(repo, root)
    if retained > MAX_RETAINED_BYTES:
        raise c.CorpusError("three-generation retained evidence exceeds 96 MiB")
    consecutive, stopped = x._validate_route_stop(root, states, plan["requests"])
    if stopped is not None:
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
            if snapshot["attempts"] + transmitted >= 1570:
                raise c.CorpusError("C2 attempt budget exhausted")
            if retained + c.MAX_BODY + 4096 + BUILD_HEADROOM_BYTES > MAX_RETAINED_BYTES:
                raise c.CorpusError("insufficient C2 evidence storage headroom before GET")
            stem = root / "working" / request_id / str(number)
            if last_sent is not None:
                delay = 1.0 - (time.monotonic() - last_sent)
            elif previous_wall_start is not None:
                delay = 1.0 - (datetime.now(UTC) - previous_wall_start).total_seconds()
            else:
                delay = 0.0
            if delay > 0:
                sleep(delay)
            started_body = c.canonical(
                {
                    "request_id": request_id,
                    "attempt": number,
                    "query": query,
                    "started_at": c.now(),
                    "plan_sha256": c.sha((root / "plan.json").read_bytes()),
                }
            )
            c.durable_new(stem.with_suffix(".started.json"), started_body)
            retained += len(started_body)
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
                retained += len(body)
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
            result_body = c.canonical(result)
            c.durable_new(stem.with_suffix(".result.json"), result_body)
            retained += len(result_body)
            states[(request_id, number)] = result
            if result["outcome"] == "HTTP_OK":
                consecutive = 0
                break
            if c._record_retryable(result) and number == 0:
                first = result
                continue
            if x._terminal_transport(result):
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
            c.durable_new(
                root / "route_stop.json",
                c.canonical({"reason": result["outcome"], "last_request_id": request_id}),
            )
            return {"status": result["outcome"], "new_attempts": transmitted}
    return {"status": "C2_COMPLETE", "new_attempts": transmitted}


def _all_members(
    repo: Path, root: Path, plan: dict[str, Any], hashes: dict[str, str]
) -> dict[str, dict[str, bytes]]:
    c1root = repo / x.ROOT
    c1plan = c.read_json(c1root / "plan.json")
    c1manifest = c.read_json(c1root / "manifest.json")
    c1hashes = {symbol: c1manifest["files"][f"archives/{symbol}.zip"] for symbol in c.PANEL}
    previous = x._all_members(repo, c1root, c1plan, c1hashes)
    result: dict[str, dict[str, bytes]] = {}
    new_archives = {symbol: c._archive_members(root, symbol, hashes[symbol]) for symbol in c.PANEL}
    c.audit_ledger(root, plan, new_archives)
    for symbol in c.PANEL:
        if set(previous[symbol]) & set(new_archives[symbol]):
            raise c.CorpusError("C2 overlaps an archived predecessor attempt")
        result[symbol] = {**previous[symbol], **new_archives[symbol]}
    return result


def _projection(
    repo: Path, root: Path, members: dict[str, dict[str, bytes]]
) -> tuple[dict[str, Any], dict[tuple[str, str], list[c.Row]]]:
    report, series = x._projection(repo, root, members)
    report["schema"] = SCHEMA
    report["dataset_version"] = VERSION
    report["c1_manifest_sha256"] = C1_MANIFEST_SHA
    return report, series


def _status(report: dict[str, Any]) -> str:
    return (
        "CORPUS_REVIEW_READY"
        if all(item["status"] == "CORPUS_REVIEW_READY" for item in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED"
    )


def _retained_bytes(repo: Path, root: Path) -> int:
    return sum(
        path.stat().st_size
        for base in (repo / x.ORIGINAL_ROOT, repo / x.ROOT, root)
        for path in base.rglob("*")
        if path.is_file()
    )


def build(repo: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(repo, root, plan)
    if _retained_bytes(repo, root) > MAX_RETAINED_BYTES:
        raise c.CorpusError("three-generation retained evidence exceeds 96 MiB before build")
    snapshot = c.audit_ledger(root, plan)
    _, stop = x._validate_route_stop(root, snapshot["states"], plan["requests"])
    if _capture_closed(root):
        raise c.CorpusError("C2 projection or archives already exist")
    hashes = c.pack_attempts(root, plan)
    merged = _all_members(repo, root, plan, hashes)
    report, series = _projection(repo, root, merged)
    files = {f"archives/{symbol}.zip": digest for symbol, digest in hashes.items()}
    reused: dict[str, str] = {}
    c1manifest = c.read_json(repo / x.ROOT / "manifest.json")
    for (symbol, frame), rows in series.items():
        name = f"combined/parquet/{symbol}-{frame}.parquet"
        if symbol not in TAIL_SYMBOLS:
            reused[name] = c1manifest["files"][name]
            old_table = pq.read_table(repo / x.ROOT / name)
            if not old_table.equals(c._table(symbol, frame, rows), check_metadata=True):
                raise c.CorpusError("unchanged C1 shard disagrees with C2 raw reconstruction")
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(c._table(symbol, frame, rows), path, compression="zstd", version="2.6")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise c.CorpusError("C2 shard exceeds 16 MiB")
        files[name] = c.sha(path.read_bytes())
    c.durable_new(root / "combined" / "report.json", c.canonical(report))
    files["combined/report.json"] = c.sha((root / "combined" / "report.json").read_bytes())
    archived = {symbol: c._archive_members(root, symbol, hashes[symbol]) for symbol in c.PANEL}
    ledger = c.audit_ledger(root, plan, archived)
    manifest = {
        "schema": SCHEMA,
        "dataset_version": VERSION,
        "status": _status(report),
        "original_manifest_sha256": x.ORIGINAL_MANIFEST_SHA,
        "c1_manifest_sha256": C1_MANIFEST_SHA,
        "plan_sha256": c.sha((root / "plan.json").read_bytes()),
        "source_sha256": plan["source_sha256"],
        "route_stop_sha256": c.sha((root / "route_stop.json").read_bytes())
        if stop is not None
        else None,
        "ledger": {key: ledger[key] for key in ("distinct", "attempts", "retries")},
        "cumulative": {
            "distinct": 1919 + ledger["distinct"],
            "attempts": 1919 + ledger["attempts"],
            "retries": ledger["retries"],
        },
        "files": dict(sorted(files.items())),
        "reused_c1_shards": dict(sorted(reused.items())),
    }
    c.durable_new(root / "manifest.json", c.canonical(manifest))
    size = _retained_bytes(repo, root)
    if size > MAX_RETAINED_BYTES:
        raise c.CorpusError("three-generation retained evidence exceeds 96 MiB")
    return {
        "status": manifest["status"],
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "bytes": size,
    }


def verify(repo: Path, root: Path) -> dict[str, Any]:
    plan = c.read_json(root / "plan.json")
    validate_plan(repo, root, plan)
    manifest = c.read_json(root / "manifest.json")
    expected_files = (
        {"combined/report.json"}
        | {f"archives/{symbol}.zip" for symbol in c.PANEL}
        | {
            f"combined/parquet/{symbol}-{frame}.parquet"
            for symbol in TAIL_SYMBOLS
            for frame in ("5m", "15m", "1h", "4h")
        }
    )
    c1manifest = c.read_json(repo / x.ROOT / "manifest.json")
    expected_reused = {
        f"combined/parquet/{symbol}-{frame}.parquet": c1manifest["files"][
            f"combined/parquet/{symbol}-{frame}.parquet"
        ]
        for symbol in c.PANEL
        if symbol not in TAIL_SYMBOLS
        for frame in ("5m", "15m", "1h", "4h")
    }
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("dataset_version") != VERSION
        or manifest.get("original_manifest_sha256") != x.ORIGINAL_MANIFEST_SHA
        or manifest.get("c1_manifest_sha256") != C1_MANIFEST_SHA
        or manifest.get("plan_sha256") != c.sha((root / "plan.json").read_bytes())
        or manifest.get("source_sha256") != plan["source_sha256"]
        or set(manifest.get("files", {})) != expected_files
        or manifest.get("reused_c1_shards") != expected_reused
    ):
        raise c.CorpusError("C2 combined manifest provenance/inventory mismatch")
    for name, digest in manifest["files"].items():
        if c.sha((root / name).read_bytes()) != digest:
            raise c.CorpusError("C2 artifact hash mismatch: " + name)
    hashes = {symbol: manifest["files"][f"archives/{symbol}.zip"] for symbol in c.PANEL}
    archived = {symbol: c._archive_members(root, symbol, hashes[symbol]) for symbol in c.PANEL}
    ledger = c.audit_ledger(root, plan, archived)
    _, stop = x._validate_route_stop(root, ledger["states"], plan["requests"])
    stop_sha = c.sha((root / "route_stop.json").read_bytes()) if stop is not None else None
    if (
        manifest.get("route_stop_sha256") != stop_sha
        or manifest.get("ledger")
        != {key: ledger[key] for key in ("distinct", "attempts", "retries")}
        or manifest.get("cumulative")
        != {
            "distinct": 1919 + ledger["distinct"],
            "attempts": 1919 + ledger["attempts"],
            "retries": ledger["retries"],
        }
    ):
        raise c.CorpusError("C2 stop or cumulative ledger differs from raw")
    merged = _all_members(repo, root, plan, hashes)
    report, series = _projection(repo, root, merged)
    if c.canonical(report) != (root / "combined" / "report.json").read_bytes():
        raise c.CorpusError("C2 combined report differs from raw")
    for (symbol, frame), rows in series.items():
        name = f"combined/parquet/{symbol}-{frame}.parquet"
        source = root if symbol in TAIL_SYMBOLS else repo / x.ROOT
        body = (source / name).read_bytes()
        expected = manifest["files"][name] if symbol in TAIL_SYMBOLS else expected_reused[name]
        if c.sha(body) != expected:
            raise c.CorpusError("C2 combined shard hash mismatch")
        if not pq.read_table(pa.BufferReader(body)).equals(
            c._table(symbol, frame, rows), check_metadata=True
        ):
            raise c.CorpusError("C2 combined Parquet differs from raw")
    if manifest["status"] != _status(report):
        raise c.CorpusError("C2 qualification status differs from raw")
    return {
        "manifest_sha256": c.sha((root / "manifest.json").read_bytes()),
        "status": manifest["status"],
        "ledger": manifest["ledger"],
        "cumulative": manifest["cumulative"],
        "report": report,
    }


class LockedResearchV2TailReader(c.LockedResearchV2CorpusReader):
    """Exact-hash data-only epochs; no source selection or market fallback."""

    def __init__(self, repo: Path, root: Path, *, dataset_version: str, manifest_sha256: str):
        if (
            dataset_version != VERSION
            or c.sha((root / "manifest.json").read_bytes()) != manifest_sha256
        ):
            raise c.CorpusError("unknown or changed C2 dataset version")
        checked = verify(repo, root)
        manifest = c.read_json(root / "manifest.json")
        self._verified = {"symbols": checked["report"]["symbols"]}
        self._tables: dict[tuple[str, str], pa.Table] = {}
        for symbol in c.PANEL:
            for frame in ("5m", "15m", "1h", "4h"):
                name = f"combined/parquet/{symbol}-{frame}.parquet"
                source = root if symbol in TAIL_SYMBOLS else repo / x.ROOT
                body = (source / name).read_bytes()
                digest = (
                    manifest["files"][name]
                    if symbol in TAIL_SYMBOLS
                    else manifest["reused_c1_shards"][name]
                )
                if c.sha(body) != digest:
                    raise c.CorpusError("C2 shard changed during reader construction")
                self._tables[(symbol, frame)] = pq.read_table(pa.BufferReader(body))
