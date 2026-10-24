"""Bounded Issue #206 public WEEX history availability probe.

This retained external diagnostic is intentionally not a reusable collector.
Its only network capability is the literal 30-request inventory declared in
Issue #206.  It imports only the accepted corpus module's pure parsing/hash/
durable-write helpers; it does not use or weaken the old transport validator.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

MAIN_SHA = "270be3d5d0d95112fa28cf31a2f14e3349db9062"
MAIN_TREE = "d27a11efe9cae5079d9a1eb310ad0ed723352c10"
ISSUE_URL = "https://github.com/setup-co/adaptive-trading-bot/issues/206"
HOST = "https://api-contract.weex.com"
ROUTE = "/capi/v3/market/historyKlines"
ENDPOINT = HOST + ROUTE
PANEL = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZAMAUSDT",
    "BCHUSDT",
    "BTWUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
)
SHAPES = (
    ("RECENT_CONTROL", "1792800000000", "1792829999999", 100),
    ("OLDEST_PREFIX", "1775059200000", "1775087999999", 96),
    ("MID_PREFIX", "1780272000000", "1780301999999", 100),
)
QUERY_KEYS = {"symbol", "interval", "priceType", "limit", "startTime", "endTime"}
MAX_BODY = 2_000_000
TIMEOUT_SECONDS = 20
MIN_SECONDS_BETWEEN_ATTEMPTS = 2


class ProbeError(RuntimeError):
    """The fixed probe identity, evidence, transport, or response is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_helpers(repo: Path) -> ModuleType:
    source = repo / "src" / "adaptive_trading_bot" / "research" / "research_v2_corpus.py"
    spec = importlib.util.spec_from_file_location("issue206_frozen_corpus_helpers", source)
    if spec is None or spec.loader is None:
        raise ProbeError("frozen helper module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if Path(str(module.__file__)).resolve() != source.resolve():
        raise ProbeError("helper module origin differs from frozen checkout")
    return module


def git(repo: Path, *args: str) -> str:
    import subprocess

    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def assert_frozen_checkout(repo: Path) -> None:
    if git(repo, "rev-parse", "HEAD") != MAIN_SHA:
        raise ProbeError("checkout HEAD differs from frozen main")
    if git(repo, "rev-parse", "HEAD^{tree}") != MAIN_TREE:
        raise ProbeError("checkout tree differs from frozen main")
    if git(repo, "status", "--porcelain"):
        raise ProbeError("frozen checkout is not clean")


def request_record(symbol: str, label: str, start: str, end: str, expected: int) -> dict[str, Any]:
    return {
        "id": f"{symbol}-{label}",
        "label": label,
        "expected_rows": expected,
        "method": "GET",
        "endpoint": ENDPOINT,
        "query": {
            "symbol": symbol,
            "interval": "5m",
            "priceType": "LAST",
            "limit": "100",
            "startTime": start,
            "endTime": end,
        },
    }


def literal_inventory() -> list[dict[str, Any]]:
    return [
        request_record(symbol, label, start, end, expected)
        for symbol in PANEL
        for label, start, end, expected in SHAPES
    ]


def validate_request(record: dict[str, Any]) -> None:
    inventory = {item["id"]: item for item in literal_inventory()}
    request_id = record.get("id")
    if request_id not in inventory or record != inventory[request_id]:
        raise ProbeError("request differs from literal Issue #206 allowlist")
    query = record["query"]
    if set(query) != QUERY_KEYS or record["endpoint"] != ENDPOINT or record["method"] != "GET":
        raise ProbeError("request host/route/method/query keys differ from policy")


def expected_plan(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    import pyarrow

    launcher = Path(__file__).resolve()
    wrapper = root / "run-once.ps1"
    helper_source = Path(str(helpers.__file__)).resolve()
    return {
        "schema": "issue-206-public-history-availability-probe-v1",
        "issue": ISSUE_URL,
        "main_sha": MAIN_SHA,
        "main_tree": MAIN_TREE,
        "repo": str(repo.resolve()),
        "root": str(root.resolve()),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version,
        "pyarrow_version": pyarrow.__version__,
        "helper_module_origin": str(helper_source),
        "helper_module_sha256": helpers.sha(helper_source.read_bytes()),
        "launcher": str(launcher),
        "launcher_sha256": helpers.sha(launcher.read_bytes()),
        "wrapper": str(wrapper.resolve()),
        "wrapper_sha256": helpers.sha(wrapper.read_bytes()),
        "endpoint": ENDPOINT,
        "panel": list(PANEL),
        "requests": literal_inventory(),
        "distinct_request_budget": 30,
        "http_attempt_budget": 30,
        "retry_budget": 0,
        "minimum_seconds_between_attempts": MIN_SECONDS_BETWEEN_ATTEMPTS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_response_bytes": MAX_BODY,
        "redirects": "REJECTED",
        "automatic_pagination": "DISABLED",
    }


def validate_plan(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise ProbeError("frozen plan is missing")
    plan = helpers.read_json(plan_path)
    if plan != expected_plan(repo, root, helpers):
        raise ProbeError("plan, source, environment, or literal inventory identity differs")
    requests = plan["requests"]
    if len(requests) != 30 or len({item["id"] for item in requests}) != 30:
        raise ProbeError("request inventory count/uniqueness differs")
    for item in requests:
        validate_request(item)
    return plan


def prepare(repo: Path, root: Path, helpers: ModuleType) -> None:
    assert_frozen_checkout(repo)
    plan = expected_plan(repo, root, helpers)
    for item in plan["requests"]:
        validate_request(item)
    forbidden = (root / "attempts", root / "run-owner.json", root / "run-exit.json")
    if any(path.exists() for path in forbidden):
        raise ProbeError("prior launch/evidence exists")
    helpers.durable_new(root / "plan.json", helpers.canonical(plan))
    helpers.durable_new(
        root / "prepare.json",
        helpers.canonical(
            {
                "at": utc_now(),
                "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
                "request_count": 30,
                "expected_maximum_rows": 2960,
                "network_attempts": 0,
            }
        ),
    )


def synthetic_checks(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    sample = json.loads(json.dumps(plan["requests"][0]))
    rejected: list[str] = []
    for label, mutate in (
        ("extra_symbol", lambda row: row["query"].__setitem__("symbol", "ADAUSDT")),
        ("changed_date", lambda row: row["query"].__setitem__("startTime", "0")),
        (
            "changed_host",
            lambda row: row.__setitem__("endpoint", "https://example.invalid" + ROUTE),
        ),
        ("extra_query_key", lambda row: row["query"].__setitem__("page", "1")),
    ):
        candidate = json.loads(json.dumps(sample))
        mutate(candidate)
        try:
            validate_request(candidate)
        except ProbeError:
            rejected.append(label)
        else:
            raise ProbeError(f"synthetic policy violation was accepted: {label}")
    query = sample["query"]
    if helpers.parse_rows(b"[]", query) != []:
        raise ProbeError("valid empty response was not preserved")
    malformed_rejected = False
    try:
        helpers.parse_rows(b"{}", query)
    except helpers.CorpusError:
        malformed_rejected = True
    if not malformed_rejected:
        raise ProbeError("malformed response was accepted")
    out_of_window = [[
        int(query["startTime"]) - 300_000,
        "1", "1", "1", "1", "0",
        int(query["startTime"]),
        "0", 0, "0", "0",
    ]]
    bounds_rejected = False
    try:
        helpers.parse_rows(helpers.canonical(out_of_window), query)
    except helpers.CorpusError:
        bounds_rejected = True
    if not bounds_rejected:
        raise ProbeError("out-of-window row was accepted")
    result = {
        "at": utc_now(),
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "inventory_count": len(plan["requests"]),
        "rejected_policy_mutations": rejected,
        "empty_array_status": "VALID_EMPTY",
        "malformed_status": "REJECTED",
        "out_of_window_status": "REJECTED",
        "network_attempts": 0,
    }
    helpers.durable_new(root / "synthetic-checks.json", helpers.canonical(result))
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
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


class LiteralTransport:
    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, record: dict[str, Any]) -> tuple[int, bytes]:
        validate_request(record)
        query = record["query"]
        url = ENDPOINT + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "atb-issue-206-public-probe/1"},
        )
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.geturl() != url:
                    raise ProbeError("response URL differs; redirect is forbidden")
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ProbeError("response exceeds body cap")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ProbeError("error response exceeds body cap") from None
            return exc.code, body


def summarize_rows(rows: list[Any], expected: int, query: dict[str, str]) -> dict[str, Any]:
    start = int(query["startTime"])
    expected_grid = {start + index * 300_000 for index in range(expected)}
    actual_grid = {row.open_time_ms for row in rows}
    missing = sorted(expected_grid - actual_grid)
    if not rows:
        status = "NO_ROWS_RETURNED_FOR_REQUEST"
    elif len(rows) == expected and not missing:
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    return {
        "coverage_status": status,
        "expected_rows": expected,
        "received_rows": len(rows),
        "missing_rows": len(missing),
        "first_open_time_ms": rows[0].open_time_ms if rows else None,
        "last_open_time_ms": rows[-1].open_time_ms if rows else None,
        "first_missing_open_time_ms": missing[0] if missing else None,
        "last_missing_open_time_ms": missing[-1] if missing else None,
    }


def write_summary(root: Path, plan: dict[str, Any], helpers: ModuleType, terminal: str) -> None:
    results = []
    for index, request in enumerate(plan["requests"], start=1):
        path = root / "attempts" / f"{index:03d}-{request['id']}.result.json"
        if path.exists():
            results.append(helpers.read_json(path))
    attempted = len(results)
    http_ok = sum(row.get("attempt_status") == "HTTP_OK" for row in results)
    coverage = {"COMPLETE": 0, "PARTIAL": 0, "NO_ROWS_RETURNED_FOR_REQUEST": 0}
    for row in results:
        status = row.get("coverage_status")
        if status in coverage:
            coverage[status] += 1
    summary = {
        "schema": "issue-206-public-history-availability-summary-v1",
        "at": utc_now(),
        "terminal_status": terminal,
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "planned_requests": 30,
        "attempted_requests": attempted,
        "unattempted_requests": 30 - attempted,
        "http_200_valid_arrays": http_ok,
        "coverage_counts": coverage,
        "results": results,
        "not_established": [
            "complete_180_day_grids",
            "daily_reconciliation",
            "historical_identity_or_contract_units",
            "all_intervening_epochs",
            "independence_from_inspected_history",
            "strategy_viability",
        ],
    }
    helpers.durable_new(root / "summary.json", helpers.canonical(summary))


def run(repo: Path, root: Path, helpers: ModuleType) -> int:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    checks = root / "synthetic-checks.json"
    if not checks.is_file() or helpers.read_json(checks).get("network_attempts") != 0:
        raise ProbeError("offline synthetic checks are missing or invalid")
    if any((root / name).exists() for name in ("run-owner.json", "run-exit.json", "summary.json")):
        raise ProbeError("prior run ownership/exit/summary exists")
    attempts_root = root / "attempts"
    if attempts_root.exists() and any(attempts_root.iterdir()):
        raise ProbeError("prior attempt evidence exists")
    owner = {
        "at": utc_now(),
        "pid": os.getpid(),
        "python_executable": str(Path(sys.executable).resolve()),
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "command": "run",
    }
    helpers.durable_new(root / "run-owner.json", helpers.canonical(owner))
    transport = LiteralTransport()
    terminal = "COMPLETED"
    exit_code = 0
    for index, record in enumerate(plan["requests"], start=1):
        validate_request(record)
        stem = attempts_root / f"{index:03d}-{record['id']}"
        started = {
            "attempt_index": index,
            "request_id": record["id"],
            "at": utc_now(),
            "plan_sha256": owner["plan_sha256"],
            "method": "GET",
            "endpoint": ENDPOINT,
            "query": record["query"],
            "retry": 0,
        }
        helpers.durable_new(stem.with_suffix(".started.json"), helpers.canonical(started))
        result: dict[str, Any] = {
            "attempt_index": index,
            "request_id": record["id"],
            "started_at": started["at"],
            "completed_at": None,
            "attempt_status": "STARTED",
            "http_status": None,
            "error_type": None,
            "error_message": None,
            "raw_file": None,
            "raw_bytes": None,
            "raw_sha256": None,
        }
        try:
            status, body = transport.get(record)
            raw_path = stem.with_suffix(".raw.json")
            helpers.durable_new(raw_path, body)
            result.update(
                {
                    "completed_at": utc_now(),
                    "http_status": status,
                    "raw_file": raw_path.name,
                    "raw_bytes": len(body),
                    "raw_sha256": helpers.sha(body),
                }
            )
            if status != 200:
                result["attempt_status"] = "HTTP_NON_200"
                terminal, exit_code = "TRANSPORT_OR_SCHEMA_BLOCKED", 2
            else:
                try:
                    rows = helpers.parse_rows(body, record["query"])
                except helpers.CorpusError as exc:
                    result.update(
                        {
                            "attempt_status": "SCHEMA_OR_DATA_BLOCKED",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        }
                    )
                    terminal, exit_code = "TRANSPORT_OR_SCHEMA_BLOCKED", 2
                else:
                    result["attempt_status"] = "HTTP_OK"
                    result.update(summarize_rows(rows, record["expected_rows"], record["query"]))
        except (urllib.error.URLError, TimeoutError, OSError, ProbeError) as exc:
            result.update(
                {
                    "completed_at": utc_now(),
                    "attempt_status": "TRANSPORT_ERROR",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            terminal, exit_code = "TRANSPORT_OR_SCHEMA_BLOCKED", 2
        helpers.durable_new(stem.with_suffix(".result.json"), helpers.canonical(result))
        if exit_code:
            break
        if index < len(plan["requests"]):
            time.sleep(MIN_SECONDS_BETWEEN_ATTEMPTS)
    write_summary(root, plan, helpers, terminal)
    helpers.durable_new(
        root / "run-exit.json",
        helpers.canonical(
            {
                "at": utc_now(),
                "exit_code": exit_code,
                "terminal_status": terminal,
                "attempted_requests": len(helpers.read_json(root / "summary.json")["results"]),
            }
        ),
    )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "run"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    repo, root = args.repo.resolve(), args.root.resolve()
    helpers = load_helpers(repo)
    if args.command == "prepare":
        prepare(repo, root, helpers)
        return 0
    if args.command == "check":
        print(json.dumps(synthetic_checks(repo, root, helpers), sort_keys=True))
        return 0
    return run(repo, root, helpers)


if __name__ == "__main__":
    raise SystemExit(main())
