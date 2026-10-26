"""Issue #212 fixed top-20 daily-history screening batch.

This external diagnostic is intentionally not a reusable collector. Its only
network capability is the literal 57-request allowlist declared in Issue
#212. It imports the accepted corpus module's parsing, hashing, canonical JSON,
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

MAIN_SHA = "a82f1461991e52c26d25ee37b0de46beb7605d21"
MAIN_TREE = "5dcf108d62e2b253b8b1e1fac4d3031ff94ace45"
ISSUE_URL = "https://github.com/setup-co/adaptive-trading-bot/issues/212"
HOST = "https://api-contract.weex.com"
ROUTE = "/capi/v3/market/historyKlines"
ENDPOINT = HOST + ROUTE
QUERY_KEYS = {"symbol", "interval", "priceType", "limit", "startTime", "endTime"}
MAX_BODY = 2_000_000
TIMEOUT_SECONDS = 20
MIN_SECONDS_BETWEEN_ATTEMPTS = 2
DAY_MS = 86_400_000
SELECTION_RELATIVE = Path(
    "data/research_evidence/issue-175-weex-kas-completion-v1/combined_selection.json"
)
SELECTION_SHA256 = "80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4"
SELECTION_MANIFEST_RELATIVE = Path(
    "data/research_evidence/issue-175-weex-kas-completion-v1/manifest.json"
)
SELECTION_MANIFEST_SHA256 = "de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79"
BTW_EVIDENCE_RELATIVE = Path("data/research_evidence/issue-210-btw-daily-pages-52564a2")
BTW_MANIFEST_SHA256 = "8359e3aa106d5a75875e7422f0d607d362f3439ed18338bc81399ff88fed9b1f"
TOP20_SYMBOLS = (
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
    "LINKUSDT",
    "LTCUSDT",
    "BNBUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "DOTUSDT",
    "XLMUSDT",
    "TRXUSDT",
    "HYPEUSDT",
)
PAGES = (
    ("A", "1775001600000", "1782777599999", 90),
    ("B", "1782777600000", "1790553599999", 90),
    ("C", "1790553600000", "1791935999999", 16),
)
REQUESTS = tuple(
    {
        "id": f"{symbol}-{page}",
        "method": "GET",
        "endpoint": ENDPOINT,
        "expected_rows": expected,
        "rank": rank,
        "query": {
            "symbol": symbol,
            "interval": "1d",
            "priceType": "LAST",
            "limit": "100",
            "startTime": start,
            "endTime": end,
        },
    }
    for rank, symbol in enumerate(TOP20_SYMBOLS, start=1)
    if symbol != "BTWUSDT"
    for page, start, end, expected in PAGES
)
PRIOR_REFERENCES = {
    "issue_175_combined_selection_sha256": SELECTION_SHA256,
    "issue_175_supplement_manifest_sha256": SELECTION_MANIFEST_SHA256,
    "issue_210_manifest_sha256": BTW_MANIFEST_SHA256,
    "issue_210_plan_sha256": "52564a208acbea68c71a162ed048fd058ee225ae07036fbe82ef6f2805746d95",
    "issue_210_summary_sha256": "ef5b009301c47d1fc3d7421ab97122c452c72182479946fbd16b8a6f024a42bd",
}


class ProbeError(RuntimeError):
    """The fixed probe identity, evidence, transport, or response is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat().replace("+00:00", "Z")


def load_helpers(repo: Path) -> ModuleType:
    source = repo / "src" / "adaptive_trading_bot" / "research" / "research_v2_corpus.py"
    spec = importlib.util.spec_from_file_location("issue212_frozen_corpus_helpers", source)
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


def verify_manifest(root: Path, expected_sha256: str, helpers: ModuleType) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or helpers.sha(manifest_path.read_bytes()) != expected_sha256:
        raise ProbeError(f"manifest identity mismatch: {root}")
    manifest = helpers.read_json(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ProbeError("manifest files are missing")
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ProbeError("manifest file entry is malformed")
        path = root / entry["path"]
        if (
            not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or helpers.sha(path.read_bytes()) != entry.get("sha256")
        ):
            raise ProbeError(f"manifest file mismatch: {entry['path']}")
    return manifest


def run_selection_verifier(repo: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "scripts.research.research_v2_snapshot_selection",
        "kas-verify",
        "--repo",
        str(repo),
        "--evidence",
        str(repo / SELECTION_RELATIVE.parent),
    ]
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProbeError("accepted Issue #175 offline verifier failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("accepted Issue #175 verifier output is malformed") from exc
    if (
        result.get("status") != "SELECTED_FOR_DATA_QUALIFICATION"
        or result.get("missing_queries") != 0
    ):
        raise ProbeError("accepted Issue #175 ranking is not complete")
    return {
        "command": "kas-verify",
        "exit_code": completed.returncode,
        "status": result["status"],
        "missing_queries": result["missing_queries"],
    }


def source_raw_path(repo: Path, record: dict[str, Any]) -> Path:
    reference = record.get("raw_reference")
    if not isinstance(reference, dict):
        raise ProbeError("candidate raw reference is missing")
    source = reference.get("source")
    file_name = reference.get("file")
    if not isinstance(file_name, str):
        raise ProbeError("candidate raw file is missing")
    if source == "ORIGINAL_173":
        return repo / "data/research_evidence/issue-173-weex-public-feasibility-332db22" / file_name
    if source == "NEW_175":
        return repo / "data/research_evidence/issue-175-weex-current-panel-cf541a3" / file_name
    if source == "KAS_SUPPLEMENT":
        return repo / SELECTION_RELATIVE.parent / file_name
    raise ProbeError(f"unknown candidate raw source: {source}")


def candidate_authority(repo: Path, helpers: ModuleType) -> list[dict[str, Any]]:
    selection_path = repo / SELECTION_RELATIVE
    manifest_path = repo / SELECTION_MANIFEST_RELATIVE
    if (
        helpers.sha(selection_path.read_bytes()) != SELECTION_SHA256
        or helpers.sha(manifest_path.read_bytes()) != SELECTION_MANIFEST_SHA256
    ):
        raise ProbeError("Issue #175 combined ranking identity mismatch")
    selection = helpers.read_json(selection_path)
    if selection.get("status") != "SELECTED_FOR_DATA_QUALIFICATION" or selection.get("blockers"):
        raise ProbeError("Issue #175 combined ranking is incomplete or blocked")
    records = selection.get("candidate_records")
    if not isinstance(records, list):
        raise ProbeError("Issue #175 candidate records are missing")
    ranked = [record for record in records if isinstance(record.get("rank"), int)]
    if len(ranked) != 176 or {record["rank"] for record in ranked} != set(range(1, 177)):
        raise ProbeError("Issue #175 ranked population identity mismatch")
    top = sorted((record for record in ranked if record["rank"] <= 20), key=lambda row: row["rank"])
    if tuple(record.get("symbol") for record in top) != TOP20_SYMBOLS:
        raise ProbeError("Issue #175 top-20 order differs from frozen pool")
    exposures: set[str] = set()
    authority: list[dict[str, Any]] = []
    for expected_rank, record in enumerate(top, start=1):
        if (
            record.get("rank") != expected_rank
            or record.get("metadata_status") != "METADATA_ELIGIBLE"
            or record.get("daily_status") != "COMPLETE_30D"
            or record.get("quote") != "USDT"
            or record.get("margin") != "USDT"
            or record.get("contract_type") != "PERPETUAL"
        ):
            raise ProbeError("Issue #175 top-20 candidate eligibility mismatch")
        exposure = record.get("canonical_exposure")
        if not isinstance(exposure, str) or exposure in exposures:
            raise ProbeError("Issue #175 top-20 economic exposures are not unique")
        exposures.add(exposure)
        raw_path = source_raw_path(repo, record)
        reference = record["raw_reference"]
        if not raw_path.is_file() or helpers.sha(raw_path.read_bytes()) != reference.get("sha256"):
            raise ProbeError("Issue #175 candidate raw reference mismatch")
        authority.append(
            {
                "rank": record["rank"],
                "symbol": record["symbol"],
                "base": record["base"],
                "canonical_exposure": exposure,
                "metadata_status": record["metadata_status"],
                "daily_status": record["daily_status"],
                "median_daily_usdt_quote_turnover": record["median_daily_usdt_quote_turnover"],
                "raw_reference": reference,
            }
        )
    if not {"BTCUSDT", "ETHUSDT", "BTWUSDT"}.issubset(TOP20_SYMBOLS):
        raise ProbeError("required anchors/BTW are missing from the frozen pool")
    return authority


def validate_reused_btw(repo: Path, helpers: ModuleType) -> dict[str, Any]:
    root = repo / BTW_EVIDENCE_RELATIVE
    verify_manifest(root, BTW_MANIFEST_SHA256, helpers)
    summary = helpers.read_json(root / "summary.json")
    if (
        summary.get("terminal_status") != "DAILY_HISTORY_MISSING_OR_PARTIAL"
        or summary.get("received_daily_opens") != 132
        or summary.get("missing_daily_opens_from_successful_requests") != 64
        or summary.get("unobserved_daily_opens_due_to_error_or_unattempted_pages") != 0
    ):
        raise ProbeError("Issue #210 reused BTW coverage identity mismatch")
    rows: dict[int, Any] = {}
    raw_authorities: list[dict[str, Any]] = []
    for page, start, end, expected in PAGES:
        result_path = (
            root
            / "attempts"
            / f"00{len(raw_authorities) + 1}-BTW_DAILY_{expected}_{page}.result.json"
        )
        result = helpers.read_json(result_path)
        if result.get("attempt_status") != "HTTP_OK":
            raise ProbeError("Issue #210 reused BTW page was not successful")
        raw_path = root / "attempts" / result["raw_file"]
        parsed = helpers.parse_rows(
            raw_path.read_bytes(),
            {
                "symbol": "BTWUSDT",
                "interval": "1d",
                "priceType": "LAST",
                "limit": "100",
                "startTime": start,
                "endTime": end,
            },
        )
        for row in parsed:
            if row.open_time_ms in rows:
                raise ProbeError("Issue #210 reused BTW rows overlap")
            rows[row.open_time_ms] = row
        raw_authorities.append(
            {
                "page": page,
                "file": str(raw_path.relative_to(repo)).replace("\\", "/"),
                "sha256": helpers.sha(raw_path.read_bytes()),
                "received_rows": len(parsed),
            }
        )
    if len(rows) != 132:
        raise ProbeError("Issue #210 reused BTW raw coverage does not rederive")
    return {
        "manifest_sha256": BTW_MANIFEST_SHA256,
        "received_daily_opens": len(rows),
        "missing_daily_opens": 196 - len(rows),
        "unobserved_daily_opens": 0,
        "raw_authorities": raw_authorities,
    }


def literal_inventory() -> list[dict[str, Any]]:
    return [json.loads(json.dumps(record)) for record in REQUESTS]


def validate_request(record: dict[str, Any]) -> None:
    inventory = {item["id"]: item for item in literal_inventory()}
    request_id = record.get("id")
    if request_id not in inventory or record != inventory[request_id]:
        raise ProbeError("request differs from literal Issue #212 allowlist")
    query = record["query"]
    if set(query) != QUERY_KEYS or record["endpoint"] != ENDPOINT or record["method"] != "GET":
        raise ProbeError("request host/route/method/query keys differ from policy")
    start = int(query["startTime"])
    end = int(query["endTime"])
    if end < start or end - start + 1 > 90 * DAY_MS:
        raise ProbeError("request span exceeds the Issue #212 90-day maximum")
    if end - start + 1 != int(record["expected_rows"]) * DAY_MS:
        raise ProbeError("request span and expected daily rows disagree")


def expected_plan(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    import pyarrow

    launcher = Path(__file__).resolve()
    wrapper = root / "run-once.ps1"
    helper_source = Path(str(helpers.__file__)).resolve()
    return {
        "schema": "issue-212-history-screened-snapshot-shortlist-plan-v1",
        "protocol": "HISTORY_SCREENED_SNAPSHOT_SHORTLIST_V1",
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
        "selection_verification": run_selection_verifier(repo),
        "candidate_pool": candidate_authority(repo, helpers),
        "reused_btw_authority": validate_reused_btw(repo, helpers),
        "requests": literal_inventory(),
        "pool_size": 20,
        "new_symbol_count": 19,
        "reused_symbol_count": 1,
        "distinct_request_budget": 57,
        "http_attempt_budget": 57,
        "new_expected_daily_positions": 3724,
        "total_candidate_daily_positions": 3920,
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
    if (
        len(plan["requests"]) != 57
        or sum(item["expected_rows"] for item in plan["requests"]) != 3724
    ):
        raise ProbeError("literal request inventory/budget mismatch")
    for index in range(0, len(plan["requests"]), 3):
        group = plan["requests"][index : index + 3]
        if len({item["query"]["symbol"] for item in group}) != 1:
            raise ProbeError("literal request group mixes symbols")
        for offset in range(2):
            left, right = group[offset], group[offset + 1]
            if int(left["query"]["endTime"]) + 1 != int(right["query"]["startTime"]):
                raise ProbeError("literal request pages are not contiguous")
        if sum(item["expected_rows"] for item in group) != 196:
            raise ProbeError("literal symbol inventory does not cover 196 days")
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


def select_shortlist(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol = {record["symbol"]: record for record in candidates}
    if len(by_symbol) != 20:
        raise ProbeError("candidate result population is not the frozen top 20")
    unavailable = [
        record["symbol"] for record in candidates if record["coverage_status"] == "UNAVAILABLE"
    ]
    if unavailable:
        return {
            "status": "BLOCKED_UNAVAILABLE_CENSUS",
            "selected_symbols": [],
            "blockers": [f"UNAVAILABLE:{symbol}" for symbol in unavailable],
        }
    failed_anchors = [
        symbol
        for symbol in ("BTCUSDT", "ETHUSDT")
        if by_symbol[symbol]["coverage_status"] != "PASS"
    ]
    passing = [record for record in candidates if record["coverage_status"] == "PASS"]
    if failed_anchors:
        return {
            "status": "BLOCKED_ANCHOR_DAILY_COVERAGE",
            "selected_symbols": [],
            "blockers": [f"ANCHOR_FAILED:{symbol}" for symbol in failed_anchors],
        }
    if len(passing) < 10:
        return {
            "status": "BLOCKED_FEWER_THAN_TEN_DAILY_ELIGIBLE",
            "selected_symbols": [],
            "blockers": [f"PASS_COUNT:{len(passing)}"],
        }
    others = [
        record["symbol"]
        for record in sorted(passing, key=lambda row: row["rank"])
        if record["symbol"] not in {"BTCUSDT", "ETHUSDT"}
    ]
    selected = ["BTCUSDT", "ETHUSDT", *others[:8]]
    if len(selected) != 10:
        raise ProbeError("shortlist construction did not produce ten members")
    return {
        "status": "SELECTED_FOR_FINE_GRID_DATA_QUALIFICATION",
        "selected_symbols": selected,
        "blockers": [],
    }


def synthetic_checks(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    mutations: list[dict[str, Any]] = []
    for change in ("symbol", "date", "host", "query_key", "interval", "over_90_day_span"):
        candidate = json.loads(json.dumps(plan["requests"][0]))
        if change == "symbol":
            candidate["query"]["symbol"] = "KASUSDT"
        elif change == "date":
            candidate["query"]["startTime"] = "1775088000000"
        elif change == "host":
            candidate["endpoint"] = "https://example.invalid" + ROUTE
        elif change == "query_key":
            candidate["query"]["extra"] = "forbidden"
        elif change == "interval":
            candidate["query"]["interval"] = "5m"
        else:
            candidate["query"]["endTime"] = str(
                int(candidate["query"]["startTime"]) + 91 * DAY_MS - 1
            )
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
        "duplicate": helpers.canonical([synthetic_row(opened), synthetic_row(opened)]),
    }
    rejected: list[dict[str, str]] = []
    for label, body in rejected_payloads.items():
        try:
            helpers.parse_rows(body, query)
        except helpers.CorpusError:
            rejected.append({"payload": label, "status": "REJECTED"})
        else:
            raise ProbeError(f"invalid synthetic response was accepted: {label}")
    synthetic_candidates = [
        {"rank": rank, "symbol": symbol, "coverage_status": "PASS"}
        for rank, symbol in enumerate(TOP20_SYMBOLS, start=1)
    ]
    positive = select_shortlist(synthetic_candidates)
    anchor_failed = select_shortlist(
        [
            {**record, "coverage_status": "FAIL"} if record["symbol"] == "BTCUSDT" else record
            for record in synthetic_candidates
        ]
    )
    nine_only = select_shortlist(
        [
            {**record, "coverage_status": "FAIL"} if record["rank"] >= 10 else record
            for record in synthetic_candidates
        ]
    )
    unavailable = select_shortlist(
        [
            {**record, "coverage_status": "UNAVAILABLE"}
            if record["symbol"] == "HYPEUSDT"
            else record
            for record in synthetic_candidates
        ]
    )
    if positive["selected_symbols"] != list(TOP20_SYMBOLS[:10]):
        raise ProbeError("synthetic selection did not use rank order")
    if (
        anchor_failed["status"] != "BLOCKED_ANCHOR_DAILY_COVERAGE"
        or nine_only["status"] != "BLOCKED_FEWER_THAN_TEN_DAILY_ELIGIBLE"
        or unavailable["status"] != "BLOCKED_UNAVAILABLE_CENSUS"
    ):
        raise ProbeError("synthetic blocker semantics failed")
    if any("selected" in record for record in plan["candidate_pool"]):
        raise ProbeError("old selected flags leaked into the new selection authority")
    result = {
        "at": utc_now(),
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "inventory_count": len(plan["requests"]),
        "new_expected_rows": sum(item["expected_rows"] for item in plan["requests"]),
        "total_expected_rows_with_reused_btw": 3920,
        "maximum_request_span_days": max(item["expected_rows"] for item in plan["requests"]),
        "rejected_policy_mutations": mutations,
        "valid_daily_row_status": "ACCEPTED",
        "zero_volume_status": "PRESENT_VALID_ROW",
        "empty_array_status": "VALID_EMPTY",
        "rejected_payloads": rejected,
        "selection_rule_status": "PASS",
        "old_selected_flags_used": False,
        "anchor_failure_status": anchor_failed["status"],
        "nine_pass_status": nine_only["status"],
        "unavailable_status": unavailable["status"],
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
            headers={"User-Agent": "atb-issue-212-history-screened-shortlist/1"},
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


def old_daily_rows(repo: Path, authority: dict[str, Any], helpers: ModuleType) -> dict[int, Any]:
    source_record = next(
        record
        for record in helpers.read_json(repo / SELECTION_RELATIVE)["candidate_records"]
        if record.get("rank") == authority["rank"]
    )
    raw_path = source_raw_path(repo, source_record)
    rows = helpers.parse_rows(
        raw_path.read_bytes(),
        {
            "symbol": authority["symbol"],
            "interval": "1d",
            "priceType": "LAST",
            "limit": "100",
            "startTime": "1789344000000",
            "endTime": "1791935999999",
        },
    )
    if len(rows) != 30:
        raise ProbeError("Issue #175 overlap authority is not a complete 30-day grid")
    return {row.open_time_ms: row for row in rows}


def reused_btw_rows(repo: Path, helpers: ModuleType) -> dict[int, Any]:
    root = repo / BTW_EVIDENCE_RELATIVE
    rows: dict[int, Any] = {}
    for index, (page, start, end, expected) in enumerate(PAGES, start=1):
        result = helpers.read_json(
            root / "attempts" / f"{index:03d}-BTW_DAILY_{expected}_{page}.result.json"
        )
        raw_path = root / "attempts" / result["raw_file"]
        parsed = helpers.parse_rows(
            raw_path.read_bytes(),
            {
                "symbol": "BTWUSDT",
                "interval": "1d",
                "priceType": "LAST",
                "limit": "100",
                "startTime": start,
                "endTime": end,
            },
        )
        rows.update({row.open_time_ms: row for row in parsed})
    return rows


def build_summary(
    repo: Path,
    root: Path,
    plan: dict[str, Any],
    helpers: ModuleType,
    at: str,
) -> dict[str, Any]:
    full_grid = {1775001600000 + offset * DAY_MS for offset in range(196)}
    authorities = {record["symbol"]: record for record in plan["candidate_pool"]}
    request_indexes = {
        request["id"]: index for index, request in enumerate(plan["requests"], start=1)
    }
    all_results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for symbol in TOP20_SYMBOLS:
        actual: dict[int, Any] = {}
        observed_grid: set[int] = set()
        pages: list[dict[str, Any]] = []
        if symbol == "BTWUSDT":
            actual = reused_btw_rows(repo, helpers)
            observed_grid = set(full_grid)
            for page, start, _end, expected in PAGES:
                page_grid = {int(start) + offset * DAY_MS for offset in range(expected)}
                page_actual = set(actual) & page_grid
                pages.append(
                    {
                        "page": page,
                        "authority": "REUSED_ISSUE_210",
                        "attempt_status": "REUSED_HTTP_OK",
                        "expected_daily_opens": expected,
                        "received_daily_opens": len(page_actual),
                        "missing_daily_opens": len(page_grid - page_actual),
                        "missing_daily_intervals": contiguous_spans(page_grid - page_actual),
                    }
                )
        else:
            for page, start, _end, expected in PAGES:
                request_id = f"{symbol}-{page}"
                index = request_indexes[request_id]
                request = plan["requests"][index - 1]
                page_grid = {int(start) + offset * DAY_MS for offset in range(expected)}
                result_path = root / "attempts" / f"{index:03d}-{request_id}.result.json"
                page_record: dict[str, Any] = {
                    "page": page,
                    "authority": "NEW_ISSUE_212",
                    "attempt_status": "UNATTEMPTED",
                    "expected_daily_opens": expected,
                    "received_daily_opens": None,
                    "missing_daily_opens": None,
                    "missing_daily_intervals": [],
                }
                if result_path.is_file():
                    result = helpers.read_json(result_path)
                    all_results.append(result)
                    page_record["attempt_status"] = result["attempt_status"]
                    if result.get("attempt_status") == "HTTP_OK":
                        observed_grid.update(page_grid)
                        raw_path = root / "attempts" / result["raw_file"]
                        parsed = helpers.parse_rows(raw_path.read_bytes(), request["query"])
                        for row in parsed:
                            if row.open_time_ms in actual:
                                raise ProbeError("new candidate daily pages overlap")
                            actual[row.open_time_ms] = row
                        page_actual = {row.open_time_ms for row in parsed}
                        page_record.update(
                            {
                                "received_daily_opens": len(parsed),
                                "missing_daily_opens": len(page_grid - page_actual),
                                "missing_daily_intervals": contiguous_spans(
                                    page_grid - page_actual
                                ),
                            }
                        )
                pages.append(page_record)
        missing = observed_grid - set(actual)
        unobserved = full_grid - observed_grid
        coverage_status = (
            "UNAVAILABLE" if unobserved else "PASS" if set(actual) == full_grid else "FAIL"
        )
        old_rows = old_daily_rows(repo, authorities[symbol], helpers)
        overlap_points = sorted(set(actual) & set(old_rows))
        overlap_differences = [
            opened for opened in overlap_points if actual[opened] != old_rows[opened]
        ]
        candidates.append(
            {
                **authorities[symbol],
                "coverage_status": coverage_status,
                "expected_daily_opens": 196,
                "received_daily_opens": len(actual),
                "missing_daily_opens_from_successful_pages": len(missing),
                "missing_daily_intervals_from_successful_pages": contiguous_spans(missing),
                "unobserved_daily_opens": len(unobserved),
                "unobserved_daily_intervals": contiguous_spans(unobserved),
                "first_returned_daily_open": iso_ms(min(actual)) if actual else None,
                "last_returned_daily_open": iso_ms(max(actual)) if actual else None,
                "pages": pages,
                "overlap_identity": {
                    "source_raw_reference": authorities[symbol]["raw_reference"],
                    "compared_daily_opens": len(overlap_points),
                    "exact_numeric_matches": len(overlap_points) - len(overlap_differences),
                    "exact_numeric_mismatches": len(overlap_differences),
                    "mismatch_open_times": [iso_ms(value) for value in overlap_differences],
                    "qualification": (
                        "EXACT_NUMERIC_MATCH"
                        if not overlap_differences and len(overlap_points) == 16
                        else "REVISION_OR_COVERAGE_DIFFERENCE"
                    ),
                },
            }
        )
    shortlist = select_shortlist(candidates)
    selected = set(shortlist["selected_symbols"])
    for candidate in candidates:
        if candidate["symbol"] in selected:
            candidate["disposition"] = "SELECTED_FOR_FINE_GRID_DATA_QUALIFICATION"
        elif candidate["coverage_status"] == "UNAVAILABLE":
            candidate["disposition"] = "UNAVAILABLE"
        elif candidate["coverage_status"] == "FAIL":
            candidate["disposition"] = "DAILY_COVERAGE_INELIGIBLE"
        else:
            candidate["disposition"] = "ELIGIBLE_NOT_SELECTED"
    return {
        "schema": "issue-212-history-screened-snapshot-shortlist-summary-v1",
        "protocol": "HISTORY_SCREENED_SNAPSHOT_SHORTLIST_V1",
        "at": at,
        "plan_sha256": helpers.sha((root / "plan.json").read_bytes()),
        "status": shortlist["status"],
        "selected_symbols": shortlist["selected_symbols"],
        "blockers": shortlist["blockers"],
        "planned_new_requests": len(plan["requests"]),
        "attempted_new_requests": len(all_results),
        "unattempted_new_requests": len(plan["requests"]) - len(all_results),
        "http_200_valid_arrays": sum(
            result.get("attempt_status") == "HTTP_OK" for result in all_results
        ),
        "reused_requests": 3,
        "new_received_daily_opens": sum(
            candidate["received_daily_opens"]
            for candidate in candidates
            if candidate["symbol"] != "BTWUSDT"
        ),
        "total_received_daily_opens": sum(
            candidate["received_daily_opens"] for candidate in candidates
        ),
        "pass_count": sum(candidate["coverage_status"] == "PASS" for candidate in candidates),
        "fail_count": sum(candidate["coverage_status"] == "FAIL" for candidate in candidates),
        "unavailable_count": sum(
            candidate["coverage_status"] == "UNAVAILABLE" for candidate in candidates
        ),
        "candidate_records": candidates,
        "results": all_results,
        "prior_references": PRIOR_REFERENCES,
        "interpretation_limits": [
            "selection_is_current_snapshot_and_selection_informed",
            "daily_presence_does_not_prove_complete_5m_history",
            "daily_presence_does_not_qualify_units_epochs_or_historical_contract_facts",
            "earliest_returned_open_is_not_a_listing_date",
            "no_strategy_or_economic_evaluation",
            "ranks_21_and_later_are_not_evaluated_by_this_protocol",
        ],
    }


def write_summary(
    repo: Path, root: Path, plan: dict[str, Any], helpers: ModuleType
) -> dict[str, Any]:
    summary = build_summary(repo, root, plan, helpers, utc_now())
    helpers.durable_new(root / "summary.json", helpers.canonical(summary))
    return summary


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
                exit_code = 2
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
                    exit_code = 2
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
            exit_code = 2
        helpers.durable_new(stem.with_suffix(".result.json"), helpers.canonical(result))
        if exit_code:
            break
        if index < len(plan["requests"]):
            time.sleep(MIN_SECONDS_BETWEEN_ATTEMPTS)
    summary = write_summary(repo, root, plan, helpers)
    helpers.durable_new(
        root / "run-exit.json",
        helpers.canonical(
            {
                "at": utc_now(),
                "exit_code": exit_code,
                "status": summary["status"],
                "attempted_requests": summary["attempted_new_requests"],
            }
        ),
    )
    return exit_code


def verify(repo: Path, root: Path, helpers: ModuleType) -> dict[str, Any]:
    assert_frozen_checkout(repo)
    plan = validate_plan(repo, root, helpers)
    summary_path = root / "summary.json"
    exit_path = root / "run-exit.json"
    if not summary_path.is_file() or not exit_path.is_file():
        raise ProbeError("run summary/exit is missing")
    summary = helpers.read_json(summary_path)
    rederived = build_summary(repo, root, plan, helpers, summary["at"])
    if helpers.canonical(rederived) != summary_path.read_bytes():
        raise ProbeError("stored summary differs from raw-to-shortlist rederivation")
    exit_record = helpers.read_json(exit_path)
    if exit_record.get("exit_code") not in {0, 2}:
        raise ProbeError("run exit code is invalid")
    if exit_record.get("attempted_requests") != summary["attempted_new_requests"]:
        raise ProbeError("run exit attempt count disagrees with summary")
    if exit_record["exit_code"] == 0 and summary["attempted_new_requests"] != 57:
        raise ProbeError("successful run did not complete the literal inventory")
    return {
        "status": summary["status"],
        "selected_symbols": summary["selected_symbols"],
        "attempted_new_requests": summary["attempted_new_requests"],
        "http_200_valid_arrays": summary["http_200_valid_arrays"],
        "pass_count": summary["pass_count"],
        "fail_count": summary["fail_count"],
        "unavailable_count": summary["unavailable_count"],
        "summary_sha256": helpers.sha(summary_path.read_bytes()),
        "network_attempts": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "run", "verify"))
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
    if args.command == "run":
        return run(repo, root, helpers)
    print(json.dumps(verify(repo, root, helpers), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
