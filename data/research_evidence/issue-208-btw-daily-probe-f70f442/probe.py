"""Two-request Issue #208 public BTWUSDT daily-history availability probe.

This external diagnostic is intentionally not a reusable collector. Its only
network capability is the literal two-request allowlist declared in Issue
#208. It imports the accepted corpus module's parsing, hashing, canonical JSON,
and durable-write helpers without changing repository code.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

MAIN_SHA = "7fde39838fb16f0fac223239c865bc58d9060057"
MAIN_TREE = "dee9d11a5b3e4b554e561158e90776d9f3cc845d"
ISSUE_URL = "https://github.com/setup-co/adaptive-trading-bot/issues/208"
HOST = "https://api-contract.weex.com"
ROUTE = "/capi/v3/market/historyKlines"
ENDPOINT = HOST + ROUTE
QUERY_KEYS = {"symbol", "interval", "priceType", "limit", "startTime", "endTime"}
MAX_BODY = 2_000_000
TIMEOUT_SECONDS = 20
MIN_SECONDS_BETWEEN_ATTEMPTS = 2
DAY_MS = 86_400_000
REQUESTS = (
    {
        "id": "BTW_DAILY_A",
        "method": "GET",
        "endpoint": ENDPOINT,
        "expected_rows": 100,
        "query": {
            "symbol": "BTWUSDT",
            "interval": "1d",
            "priceType": "LAST",
            "limit": "100",
            "startTime": "1775001600000",
            "endTime": "1783641599999",
        },
    },
    {
        "id": "BTW_DAILY_B",
        "method": "GET",
        "endpoint": ENDPOINT,
        "expected_rows": 96,
        "query": {
            "symbol": "BTWUSDT",
            "interval": "1d",
            "priceType": "LAST",
            "limit": "100",
            "startTime": "1783641600000",
            "endTime": "1791935999999",
        },
    },
)
PRIOR_REFERENCES = {
    "issue_206_manifest_sha256": "e49e61eb4e6c77b09c55381aceda61178322c37a6f1ee56e65e2adba6192012e",
    "issue_206_btw_5m_oldest_raw_sha256": (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    ),
    "issue_206_btw_5m_mid_raw_sha256": (
        "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    ),
    "issue_181_c2_manifest_sha256": (
        "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
    ),
    "issue_181_c2_report_sha256": (
        "efd51747baf786ad637ec0814eb94ade83bee9a46c4fe5941f4d0fd97a9f2ca9"
    ),
}


class ProbeError(RuntimeError):
    """The fixed probe identity, evidence, transport, or response is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat().replace("+00:00", "Z")


def load_helpers(repo: Path) -> ModuleType:
    source = repo / "src" / "adaptive_trading_bot" / "research" / "research_v2_corpus.py"
    spec = importlib.util.spec_from_file_location("issue208_frozen_corpus_helpers", source)
    if spec is None or spec.loader is None:
        raise ProbeError("frozen helper module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if Path(str(module.__file__)).resolve() != source.resolve():
        raise ProbeError("helper module origin differs from frozen checkout")
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def assert_frozen_checkout(repo: Path) -> None:
    if git(repo, "rev-parse", "HEAD") != MAIN_SHA:
        raise ProbeError("checkout HEAD differs from frozen main")
    if git(repo, "rev-parse", "HEAD^{tree}") != MAIN_TREE:
        raise ProbeError("checkout tree differs from frozen main")
    if git(repo, "status", "--porcelain"):
        raise ProbeError("frozen checkout is not clean")


def literal_inventory() -> list[dict[str, Any]]:
    return [json.loads(json.dumps(record)) for record in REQUESTS]


def validate_request(record: dict[str, Any]) -> None:
    inventory = {item["id"]: item for item in literal_inventory()}
    request_id = record.get("id")
    if request_id not in inventory or record != inventory[request_id]:
        raise ProbeError("request differs from literal Issue #208 allowlist")
    query = record["query"]
    if set(query) != QUERY_KEYS or record["endpoint"] != ENDPOINT or record["method"] != "GET":
        raise ProbeError("request host/route/method/query keys differ from policy")


def expected_plan(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    import pyarrow

    launcher = Path(__file__).resolve()
    wrapper = root / "run-once.ps1"
    helper_source = Path(str(helpers.__file__)).resolve()
    return {
        "schema": "issue-208-btw-daily-history-probe-v1",
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
        "requests": literal_inventory(),
        "distinct_request_budget": 2,
        "http_attempt_budget": 2,
        "retry_budget": 0,
        "timeout_seconds": TIMEOUT_SECONDS,
        "maximum_response_bytes": MAX_BODY,
        "minimum_seconds_between_attempts": MIN_SECONDS_BETWEEN_ATTEMPTS,
        "automatic_pagination": "DISABLED",
        "redirects": "REJECTED",
        "ambient_proxy_use": "DISABLED",
        "credentials_and_cookies": "DISABLED",
        "prior_references": PRIOR_REFERENCES,
    }


def prepare(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    assert_frozen_checkout(repo)
    if not root.is_dir():
        raise ProbeError("external evidence root is missing")
    allowed = {"probe.py", "run-once.ps1"}
    unexpected = {path.name for path in root.iterdir()} - allowed
    if unexpected:
        raise ProbeError(f"external root is already used: {sorted(unexpected)}")
    plan = expected_plan(repo, root, helpers)
    for request in plan["requests"]:
        validate_request(request)
    helpers.durable_new(root / "plan.json", helpers.canonical(plan))
    prepared = {
        "at": utc_now(),
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "launcher_sha256": plan["launcher_sha256"],
        "wrapper_sha256": plan["wrapper_sha256"],
        "helper_module_sha256": plan["helper_module_sha256"],
        "network_attempts": 0,
    }
    helpers.durable_new(root / "prepare.json", helpers.canonical(prepared))
    return prepared


def validate_plan(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise ProbeError("frozen plan is missing")
    plan = helpers.read_json(plan_path)
    if plan != expected_plan(repo, root, helpers):
        raise ProbeError("frozen plan/source/environment identity mismatch")
    for request in plan["requests"]:
        validate_request(request)
    return plan


def synthetic_row(opened: int) -> list[object]:
    return [opened, "10", "11", "9", "10", "0", opened + DAY_MS, "0", 0, "0", "0"]


def contiguous_spans(points: set[int]) -> list[dict[str, str]]:
    spans: list[tuple[int, int]] = []
    for point in sorted(points):
        if not spans or point != spans[-1][1]:
            spans.append((point, point + DAY_MS))
        else:
            spans[-1] = (spans[-1][0], point + DAY_MS)
    return [{"start": iso_ms(start), "end": iso_ms(end)} for start, end in spans]


def summarize_rows(rows: list[Any], record: dict[str, Any]) -> dict[str, Any]:
    query = record["query"]
    expected = record["expected_rows"]
    start = int(query["startTime"])
    expected_grid = {start + index * DAY_MS for index in range(expected)}
    actual_grid = {row.open_time_ms for row in rows}
    missing_grid = expected_grid - actual_grid
    if not rows:
        status = "NO_ROWS_RETURNED_FOR_REQUEST"
    elif actual_grid == expected_grid:
        status = "COMPLETE"
    else:
        status = "PARTIAL"
    return {
        "coverage_status": status,
        "expected_rows": expected,
        "received_rows": len(rows),
        "missing_rows": len(missing_grid),
        "first_returned_daily_candle_within_requested_interval": (
            iso_ms(min(actual_grid)) if actual_grid else None
        ),
        "last_returned_daily_candle_within_requested_interval": (
            iso_ms(max(actual_grid)) if actual_grid else None
        ),
        "available_daily_spans": contiguous_spans(actual_grid),
        "missing_daily_intervals": contiguous_spans(missing_grid),
    }


def synthetic_checks(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    mutations: list[dict[str, Any]] = []
    for change in ("symbol", "date", "host", "query_key", "interval"):
        candidate = json.loads(json.dumps(plan["requests"][0]))
        if change == "symbol":
            candidate["query"]["symbol"] = "BTCUSDT"
        elif change == "date":
            candidate["query"]["startTime"] = "1775088000000"
        elif change == "host":
            candidate["endpoint"] = "https://example.invalid" + ROUTE
        elif change == "query_key":
            candidate["query"]["extra"] = "forbidden"
        else:
            candidate["query"]["interval"] = "5m"
        try:
            validate_request(candidate)
        except ProbeError:
            mutations.append({"mutation": change, "status": "REJECTED"})
        else:
            raise ProbeError(f"offline policy mutation was accepted: {change}")
    query = plan["requests"][0]["query"]
    opened = int(query["startTime"])
    valid = helpers.canonical([synthetic_row(opened)])
    if len(helpers.parse_rows(valid, query)) != 1:
        raise ProbeError("valid synthetic daily row did not parse")
    if helpers.parse_rows(b"[]", query) != []:
        raise ProbeError("empty array did not remain valid and empty")
    rejected_payloads = {
        "malformed": b"{}",
        "out_of_range": helpers.canonical([synthetic_row(opened - DAY_MS)]),
    }
    rejected: list[dict[str, str]] = []
    for label, body in rejected_payloads.items():
        try:
            helpers.parse_rows(body, query)
        except helpers.CorpusError:
            rejected.append({"payload": label, "status": "REJECTED"})
        else:
            raise ProbeError(f"invalid synthetic response was accepted: {label}")
    result = {
        "at": utc_now(),
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "inventory_count": len(plan["requests"]),
        "rejected_policy_mutations": mutations,
        "valid_daily_row_status": "ACCEPTED",
        "zero_volume_status": "PRESENT_VALID_ROW",
        "empty_array_status": "VALID_EMPTY",
        "rejected_payloads": rejected,
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
        url = ENDPOINT + "?" + urllib.parse.urlencode(record["query"])
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "atb-issue-208-btw-daily-probe/1"},
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


def write_summary(root: Path, plan: dict[str, Any], helpers: ModuleType, terminal: str) -> None:
    results = []
    actual_grid: set[int] = set()
    expected_grid: set[int] = set()
    for index, request in enumerate(plan["requests"], start=1):
        path = root / "attempts" / f"{index:03d}-{request['id']}.result.json"
        start = int(request["query"]["startTime"])
        expected_grid.update(start + offset * DAY_MS for offset in range(request["expected_rows"]))
        if path.exists():
            result = helpers.read_json(path)
            results.append(result)
            if result.get("attempt_status") == "HTTP_OK":
                raw_path = root / "attempts" / str(result["raw_file"])
                rows = helpers.parse_rows(raw_path.read_bytes(), request["query"])
                actual_grid.update(row.open_time_ms for row in rows)
    if terminal == "TRANSPORT_OR_SCHEMA_BLOCKED":
        classification = terminal
    elif actual_grid == expected_grid:
        classification = "DAILY_GRID_PRESENT_FINE_GRID_UNQUALIFIED"
    else:
        classification = "DAILY_HISTORY_MISSING_OR_PARTIAL"
    summary = {
        "schema": "issue-208-btw-daily-history-summary-v1",
        "at": utc_now(),
        "terminal_status": classification,
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "planned_requests": 2,
        "attempted_requests": len(results),
        "unattempted_requests": 2 - len(results),
        "http_200_valid_arrays": sum(row.get("attempt_status") == "HTTP_OK" for row in results),
        "expected_daily_opens": len(expected_grid),
        "received_daily_opens": len(actual_grid),
        "missing_daily_opens": len(expected_grid - actual_grid),
        "first_returned_daily_candle_within_requested_interval": (
            iso_ms(min(actual_grid)) if actual_grid else None
        ),
        "last_returned_daily_candle_within_requested_interval": (
            iso_ms(max(actual_grid)) if actual_grid else None
        ),
        "available_daily_spans": contiguous_spans(actual_grid),
        "missing_daily_intervals": contiguous_spans(expected_grid - actual_grid),
        "results": results,
        "prior_references": PRIOR_REFERENCES,
        "interpretation_limits": [
            "first_returned_daily_candle_is_not_a_listing_date",
            "request_boundary_is_not_a_discovered_retention_boundary",
            "daily_presence_does_not_prove_complete_5m_history",
            "daily_candles_cannot_substitute_for_canonical_5m_last",
            "daily_and_5m_availability_may_differ_without_invalidating_either_capture",
            "no_strategy_or_economic_evaluation",
        ],
    }
    helpers.durable_new(root / "summary.json", helpers.canonical(summary))


def run(repo: Path, root: Path, helpers: ModuleType) -> int:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    checks_path = root / "synthetic-checks.json"
    if not checks_path.is_file() or helpers.read_json(checks_path).get("network_attempts") != 0:
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
                    result.update(summarize_rows(rows, record))
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
                "terminal_status": helpers.read_json(root / "summary.json")["terminal_status"],
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
        print(json.dumps(prepare(repo, root, helpers), sort_keys=True))
        return 0
    if args.command == "check":
        print(json.dumps(synthetic_checks(repo, root, helpers), sort_keys=True))
        return 0
    return run(repo, root, helpers)


if __name__ == "__main__":
    raise SystemExit(main())
