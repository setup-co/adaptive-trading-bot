"""One-shot, public-only Research V2 current-snapshot panel selection.

Plan and verification are offline. Capture is a separate explicit command that
cannot resume or reset its budget after an attempted transmission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from statistics import median
from typing import Any, Protocol, cast

from scripts.research import research_v2_universe_feasibility as prior

SCHEMA = "current-snapshot-exploratory-panel-v1"
HOST = "https://api-contract.weex.com"
ROUTE = "/capi/v3/market/historyKlines"
START = datetime(2026, 9, 14, tzinfo=UTC)
END = datetime(2026, 10, 14, tzinfo=UTC)
ORIGINAL = Path("data/research_evidence/issue-173-weex-public-feasibility-332db22")
SUPPLEMENT = Path("data/research_evidence/issue-173-weex-prefix-supplement-2dd18db")
ORIGINAL_MANIFEST = "88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac"
SUPPLEMENT_MANIFEST = "34c6e5153f7654eb6e66b004a0d57f9ce5df628229043209c48b809d5a906a87"
PRIOR_SOURCE = "55421acaf614e46d33cdd1f936e1daa900f7fc7d1ff679eb53cd1be58cade7fa"
VERIFIER_173_SOURCE_SHA256 = "623ef9825ef09e75549f1222d035efc49e27abe9f1a93180cd8ab5ae93f66fd1"
CAPTURE_SOURCE_SHA256 = "589ab7bf0fe5bc076fd3932578d43839665a4b73b4efec21bcc1d988c578e8e0"
CAPTURE_CODE_REVISION = "cf541a34873f743f3775943a5d39ae3ab81b59a2"
CAPTURE_PLAN_SHA256 = "ab7bae7844eea690599ef6b6fe5065c41e91a562adaef44d9557c2f3ca86de8f"
CAPTURE_SELECTION_SHA256 = "764d3de84f9686d9bcda0785f582efbfe046a18d4f31c4db637afee6ce7e1d50"
CAPTURE_MANIFEST_SHA256 = "ea5378964cea4b0aa767940281bf781bac3511269ba19f25e57ef46fd642c0ff"
CAPTURE_BUNDLE = Path("data/research_evidence/issue-175-weex-current-panel-cf541a3")
KAS_SUPPLEMENT = Path("data/research_evidence/issue-175-weex-kas-completion-v1")
KAS_SCHEMA = "current-snapshot-exploratory-kas-completion-v1"
KAS_QUERY = {
    "symbol": "KASUSDT",
    "interval": "1d",
    "priceType": "LAST",
    "limit": "100",
    "startTime": "1789344000000",
    "endTime": "1791935999999",
}
DOC = "https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api"
DECIMAL_CONTEXT = Context(prec=80)
MAX_BODY = 2_000_000
MAX_ATTEMPTS = 362
AMBIGUOUS_PREFIX = re.compile(r"^(?:1000|1M)[A-Z0-9]+$")


class SelectionError(ValueError):
    """A frozen input, acquisition or projection failed closed."""


class Transport(Protocol):
    def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]: ...


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


class PublicDailyTransport:
    """Fixed public GET, no ambient proxy/cookie/auth and no redirects."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        validate_query(path, query)
        if timeout > 20:
            raise SelectionError("timeout exceeds frozen maximum")
        url = HOST + path + "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "atb-rv2-panel/1"}
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise SelectionError("response exceeds bounded size")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise SelectionError("error response exceeds bounded size") from None
            return exc.code, body


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: object) -> bytes:
    return prior.canonical(value)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def atomic_new(path: Path, body: bytes) -> None:
    """Never overwrite evidence; fsync bytes before publishing final name."""
    if path.exists():
        raise SelectionError(f"existing evidence path: {path.name}")
    temporary = path.with_name(path.name + ".pending")
    if temporary.exists():
        raise SelectionError(f"ambiguous pending evidence path: {path.name}")
    try:
        prior.write_new(temporary, body)
        os.replace(temporary, path)
    except OSError as exc:
        raise SelectionError(f"local evidence persistence failed: {path.name}") from exc


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def source_sha() -> str:
    return sha(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))


def revision(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=repo).strip()


def query_for(symbol: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "interval": "1d",
        "startTime": str(prior.ms(START)),
        "endTime": str(prior.ms(END) - 1),
        "limit": "100",
        "priceType": "LAST",
    }


def validate_query(path: str, query: dict[str, str]) -> None:
    if path != ROUTE or not re.fullmatch(r"[A-Z0-9]+", query.get("symbol", "")):
        raise SelectionError("route/symbol is not allowlisted")
    if query != query_for(query["symbol"]):
        raise SelectionError("daily query differs from frozen shape/window")


def retryable(error: BaseException | None = None, status: int | None = None) -> bool:
    """Only a real network timeout or HTTP 5xx may consume retry two."""
    if status is not None:
        return 500 <= status <= 599
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, (TimeoutError, socket.timeout))
    return isinstance(error, (TimeoutError, socket.timeout))


def _original_paths(repo: Path) -> tuple[Path, Path]:
    original, supplement = repo / ORIGINAL, repo / SUPPLEMENT
    if sha((original / "manifest.json").read_bytes()) != ORIGINAL_MANIFEST:
        raise SelectionError("original #173 manifest mismatch")
    if sha((supplement / "manifest.json").read_bytes()) != SUPPLEMENT_MANIFEST:
        raise SelectionError("supplement #173 manifest mismatch")
    if (
        prior.ACCEPTED_SUPPLEMENT_SOURCE_SHA256 != PRIOR_SOURCE
        or prior.ACCEPTED_SUPPLEMENT_MANIFEST_SHA256 != SUPPLEMENT_MANIFEST
        or sha(Path(prior.__file__).read_bytes().replace(b"\r\n", b"\n"))
        != VERIFIER_173_SOURCE_SHA256
    ):
        raise SelectionError("accepted #173 helper generation mismatch")
    prior.verify_bundle(original)
    prior.verify_supplement(original, supplement)
    return original, supplement


def _catalog(repo: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    original, _ = _original_paths(repo)
    exchange = read_json(original / "002-exchange_info.raw.json")
    api = read_json(original / "003-api_trading_symbols.raw.json")
    plan = read_json(original / "plan.json")
    if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list):
        raise SelectionError("saved catalog schema mismatch")
    considered, _ = prior.select_probes(
        (original / "002-exchange_info.raw.json").read_bytes(),
        (original / "003-api_trading_symbols.raw.json").read_bytes(),
    )
    if (
        considered != plan["considered"]
        or sum(row["reason"] == "ELIGIBLE_CURRENT" for row in considered) != 181
    ):
        raise SelectionError("frozen 181 outer candidate set mismatch")
    if not isinstance(api, list):
        raise SelectionError("saved API symbol schema mismatch")
    return (
        exchange["symbols"],
        {row["symbol"]: row for row in considered},
        {
            "exchange": exchange,
            "api": api,
        },
    )


def eligibility(repo: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    symbols, considered, _ = _catalog(repo)
    by_symbol = {row["symbol"]: row for row in symbols}
    all_catalog = [dict(row) for row in considered.values()]
    outer = sorted(
        symbol for symbol, row in considered.items() if row["reason"] == "ELIGIBLE_CURRENT"
    )
    rows: list[dict[str, object]] = []
    exposure_counts: dict[str, int] = {}
    for symbol in outer:
        item = by_symbol[symbol]
        base = item.get("baseAsset")
        if isinstance(base, str):
            exposure_counts[base] = exposure_counts.get(base, 0) + 1
    for symbol in outer:
        item = by_symbol[symbol]
        base = item.get("baseAsset")
        quote = item.get("quoteAsset")
        margin = item.get("marginAsset")
        reason = "METADATA_ELIGIBLE"
        if not isinstance(base, str) or not base or symbol != base + "USDT":
            reason = "IDENTITY_UNRESOLVED"
        elif AMBIGUOUS_PREFIX.fullmatch(base):
            reason = "SCALED_UNDERLYING_UNRESOLVED"
        elif exposure_counts[base] != 1:
            reason = "DUPLICATE_EXPOSURE_UNRESOLVED"
        elif (quote, margin, item.get("contractType"), item.get("underlyingType")) != (
            "USDT",
            "USDT",
            "PERPETUAL",
            "COIN",
        ):
            reason = "CONTRACT_IDENTITY_UNRESOLVED"
        rows.append(
            {
                "symbol": symbol,
                "canonical_exposure": base,
                "base": base,
                "quote": quote,
                "margin": margin,
                "contract_type": item.get("contractType"),
                "underlying_type": item.get("underlyingType"),
                "display_symbol": item.get("displaySymbol"),
                "contract_val_current_unqualified": str(item.get("contractVal")),
                "scale_evidence": "DIRECT_BASE_LABEL_ONLY"
                if reason == "METADATA_ELIGIBLE"
                else "UNRESOLVED",
                "metadata_status": reason,
                "catalog_file": "002-exchange_info.raw.json",
                "api_file": "003-api_trading_symbols.raw.json",
            }
        )
    return all_catalog, rows


def _reused(repo: Path, eligible: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    original = repo / ORIGINAL
    manifest = read_json(original / "manifest.json")
    expected = {
        str(row["symbol"]) for row in eligible if row["metadata_status"] == "METADATA_ELIGIBLE"
    }
    reused: dict[str, dict[str, object]] = {}
    for attempt in manifest["attempts"]:
        query = attempt["query"]
        if (
            not isinstance(query, dict)
            or query.get("symbol") not in expected
            or query != query_for(query["symbol"])
        ):
            continue
        symbol = query["symbol"]
        if symbol in reused or attempt["http_status"] != 200:
            raise SelectionError("saved recent page is contradictory")
        file = attempt["file"]
        body = (original / file).read_bytes()
        if sha(body) != attempt["sha256"] or len(body) != attempt["byte_length"]:
            raise SelectionError("saved recent page hash/size mismatch")
        reused[symbol] = {
            "file": file,
            "sha256": attempt["sha256"],
            "captured_at": attempt["captured_at"],
            "query": query,
        }
    return dict(sorted(reused.items()))


def build_plan(repo: Path, *, code_revision: str) -> dict[str, Any]:
    all_catalog, rows = eligibility(repo)
    reused = _reused(repo, rows)
    missing = [
        str(row["symbol"])
        for row in rows
        if row["metadata_status"] == "METADATA_ELIGIBLE" and row["symbol"] not in reused
    ]
    if len(missing) > 181 or len(set(missing)) != len(missing):
        raise SelectionError("missing-query budget or uniqueness failure")
    original = repo / ORIGINAL
    old_manifest = read_json(original / "manifest.json")
    return {
        "schema": SCHEMA,
        "code_revision": code_revision,
        "source_sha256": source_sha(),
        "prior_source_sha256": PRIOR_SOURCE,
        "original_manifest_sha256": ORIGINAL_MANIFEST,
        "original_plan_sha256": sha((original / "plan.json").read_bytes()),
        "supplement_manifest_sha256": SUPPLEMENT_MANIFEST,
        "catalog_capture_times": {
            a["name"]: a["captured_at"] for a in old_manifest["attempts"][:3]
        },
        "official_schema_url": DOC,
        "official_schema_facts": {
            "exchange": "baseAsset/quoteAsset/marginAsset, PERPETUAL/COIN",
            "history": "inclusive ms bounds, 1d LAST limit 100, quote asset volume index 7",
        },
        "ranking_cutoff": prior.iso(END),
        "window_start": prior.iso(START),
        "window_end_exclusive": prior.iso(END),
        "selection_semantics": "CURRENT_SNAPSHOT_EXPLORATORY_PANEL_V1",
        "eligibility_policy": (
            "exact BASE+USDT, COIN/PERPETUAL, USDT quote/margin, saved API support, "
            "no ambiguous scale or duplicate exposure"
        ),
        "considered_catalog": all_catalog,
        "outer_eligibility": rows,
        "reused_recent": reused,
        "missing_symbols": missing,
        "queries": [{"path": ROUTE, "query": query_for(symbol)} for symbol in missing],
        "distinct_query_budget": len(missing),
        "attempt_budget": min(2 * len(missing), MAX_ATTEMPTS),
        "maximum_rows_per_response": 100,
        "maximum_response_bytes": MAX_BODY,
        "minimum_seconds_between_attempts": 1,
        "timeout_seconds": 20,
    }


def create_plan(repo: Path, evidence: Path) -> dict[str, Any]:
    if evidence.exists():
        raise SelectionError("evidence directory already exists")
    plan = build_plan(repo, code_revision=revision(repo))
    evidence.mkdir(parents=True)
    atomic_new(evidence / "plan.json", canonical(plan))
    return plan


def load_plan(repo: Path, evidence: Path) -> dict[str, Any]:
    plan = read_json(evidence / "plan.json")
    if sha((evidence / "plan.json").read_bytes()) != CAPTURE_PLAN_SHA256:
        raise SelectionError("unknown original #175 plan identity")
    if (
        plan.get("code_revision") != CAPTURE_CODE_REVISION
        or plan.get("source_sha256") != CAPTURE_SOURCE_SHA256
    ):
        raise SelectionError("unknown original #175 capture-source generation")
    expected = build_plan(repo, code_revision=CAPTURE_CODE_REVISION)
    expected["source_sha256"] = CAPTURE_SOURCE_SHA256
    if plan != expected:
        raise SelectionError("frozen plan is not derivable from pinned catalogs/source")
    if sha((evidence / "plan.json").read_bytes()) != sha(canonical(plan)):
        raise SelectionError("plan is not canonical")
    return cast(dict[str, Any], plan)


def _attempt_files(evidence: Path, index: int) -> tuple[Path, Path, Path]:
    stem = f"{index:03d}"
    return (
        evidence / (stem + ".started.json"),
        evidence / (stem + ".result.json"),
        evidence / (stem + ".raw.json"),
    )


def capture(repo: Path, evidence: Path, transport: Transport | None = None) -> dict[str, object]:
    plan = load_plan(repo, evidence)
    if any(evidence.glob("*.started.json")) or (evidence / "manifest.json").exists():
        raise SelectionError("capture already started; no automatic resume or new budget")
    transport = transport or PublicDailyTransport()
    attempt = 0
    previous_send: float | None = None
    stop_route = False
    for planned in plan["queries"]:
        if stop_route:
            break
        path, query = planned["path"], planned["query"]
        validate_query(path, query)
        for retry in range(2):
            if attempt >= plan["attempt_budget"] or attempt >= MAX_ATTEMPTS:
                raise SelectionError("frozen attempt budget exhausted")
            if previous_send is not None:
                time.sleep(max(0.0, 1.0 - (time.monotonic() - previous_send)))
            attempt += 1
            start_file, result_file, raw_file = _attempt_files(evidence, attempt)
            started = {
                "index": attempt,
                "at": utc_now(),
                "method": "GET",
                "path": path,
                "query": query,
                "retry": retry,
                "plan_sha256": sha((evidence / "plan.json").read_bytes()),
            }
            atomic_new(start_file, canonical(started))
            previous_send = time.monotonic()
            try:
                status, body = transport.get(path, query, 20)
            except Exception as exc:
                kind = "NETWORK_TIMEOUT" if retryable(exc) else "NETWORK_UNAVAILABLE"
                atomic_new(result_file, canonical({"status": kind, "raw_file": None}))
                if retry == 0 and kind == "NETWORK_TIMEOUT":
                    continue
                break
            if len(body) > MAX_BODY:
                raise SelectionError("response exceeds bounded size")
            atomic_new(raw_file, body)
            result = {
                "status": "HTTP",
                "http_status": status,
                "raw_file": raw_file.name,
                "byte_length": len(body),
                "sha256": sha(body),
                "captured_at": utc_now(),
            }
            atomic_new(result_file, canonical(result))
            if status in (401, 403, 429):
                stop_route = True
            if retry == 0 and retryable(status=status) and not stop_route:
                continue
            break
    return finalize(repo, evidence)


def _attempts(evidence: Path) -> list[dict[str, Any]]:
    starts = sorted(evidence.glob("*.started.json"))
    attempts: list[dict[str, Any]] = []
    for index, start_file in enumerate(starts, start=1):
        if start_file.name != f"{index:03d}.started.json":
            raise SelectionError("attempt sequence is not contiguous")
        _, result_file, raw_file = _attempt_files(evidence, index)
        if not result_file.exists():
            raise SelectionError("ambiguous started attempt has no result; no retry")
        start, result = read_json(start_file), read_json(result_file)
        if result["raw_file"] is not None:
            body = raw_file.read_bytes()
            if result["sha256"] != sha(body) or result["byte_length"] != len(body):
                raise SelectionError("raw response digest/size mismatch")
        elif raw_file.exists():
            raise SelectionError("unreferenced raw response")
        attempts.append({"started": start, "result": result})
    return attempts


def project(
    repo: Path,
    evidence: Path,
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    *,
    kas_supplement: tuple[bytes, str] | None = None,
) -> dict[str, Any]:
    if len(attempts) > plan["attempt_budget"]:
        raise SelectionError("attempt budget violated")
    expected = plan["queries"]
    observed: dict[str, tuple[bytes, str]] = {}
    issues: list[str] = []
    index = 0
    for planned in expected:
        symbol = planned["query"]["symbol"]
        if index >= len(attempts):
            issues.append("UNREQUESTED:" + symbol)
            continue
        for retry in range(2):
            if index >= len(attempts):
                break
            entry = attempts[index]
            start, result = entry["started"], entry["result"]
            if (
                start["query"] != planned["query"]
                or start["path"] != ROUTE
                or start["retry"] != retry
            ):
                raise SelectionError("attempt differs from frozen query order")
            if start["plan_sha256"] != sha((evidence / "plan.json").read_bytes()):
                raise SelectionError("attempt plan identity mismatch")
            index += 1
            if result["status"] == "HTTP" and result["http_status"] == 200:
                observed[symbol] = (
                    (evidence / str(result["raw_file"])).read_bytes(),
                    str(result["raw_file"]),
                )
                break
            is_retry = result["status"] == "NETWORK_TIMEOUT" or (
                result["status"] == "HTTP" and retryable(status=result["http_status"])
            )
            if (
                retry == 0
                and is_retry
                and index < len(attempts)
                and attempts[index]["started"]["query"] == planned["query"]
            ):
                continue
            if not (symbol == "KASUSDT" and kas_supplement is not None):
                issues.append(str(result["status"]) + ":" + symbol)
            if result["status"] == "HTTP" and result["http_status"] in (401, 403, 429):
                if index != len(attempts):
                    raise SelectionError("route denial was followed by another request")
                issues.append("ROUTE_DENIED")
            break
    if index != len(attempts):
        raise SelectionError("extra attempt beyond frozen plan")
    original = repo / ORIGINAL
    rows: list[dict[str, object]] = []
    qualified: list[tuple[str, Decimal]] = []
    for item in plan["outer_eligibility"]:
        symbol = str(item["symbol"])
        row = dict(item)
        body_ref = plan["reused_recent"].get(symbol)
        if body_ref is not None:
            body, file_ref = (original / body_ref["file"]).read_bytes(), str(body_ref["file"])
            source_ref = "ORIGINAL_173"
        elif symbol in observed:
            body, file_ref = observed[symbol]
            source_ref = "NEW_175"
        elif symbol == "KASUSDT" and kas_supplement is not None:
            body, file_ref = kas_supplement
            source_ref = "KAS_SUPPLEMENT_175"
        else:
            body, file_ref, source_ref = None, None, None
        row.update(
            {
                "daily_status": "NOT_APPLICABLE",
                "raw_reference": None,
                "daily_inputs": None,
                "daily_quote_values": None,
                "median_daily_usdt_quote_turnover": None,
                "rank": None,
                "selected": False,
                "selection_reason": row["metadata_status"],
            }
        )
        if row["metadata_status"] == "METADATA_ELIGIBLE":
            row["daily_status"] = "UNAVAILABLE"
            if body is not None:
                row["raw_reference"] = {"source": source_ref, "file": file_ref, "sha256": sha(body)}
                try:
                    candles = prior.candle_rows(body, START, END, "1d")
                except (prior.FeasibilityError, ValueError):
                    issues.append("MALFORMED_DAILY:" + symbol)
                    row["daily_status"] = "MALFORMED"
                else:
                    if not prior.complete(candles, START, END, "1d"):
                        row["daily_status"] = "INCOMPLETE_GRID"
                        row["selection_reason"] = "PREDECLARED_INCOMPLETE_HISTORY_EXCLUSION"
                    else:
                        values = [value for _, value in candles]
                        payload = prior.raw_json(body)
                        assert isinstance(payload, list)
                        row_indices = {item[0]: index for index, item in enumerate(payload)}
                        with localcontext(DECIMAL_CONTEXT):
                            med = median(values)
                        row["daily_status"] = "COMPLETE_30D"
                        row["daily_inputs"] = [
                            {
                                "open_time_ms": opened,
                                "raw_row_index": row_indices[opened],
                                "quote_volume_usdt": str(value),
                            }
                            for opened, value in candles
                        ]
                        row["daily_quote_values"] = [str(value) for value in values]
                        row["median_daily_usdt_quote_turnover"] = str(med)
                        qualified.append((symbol, med))
        rows.append(row)
    ranked = sorted(qualified, key=lambda pair: (-pair[1], pair[0]))
    ranks = {symbol: n for n, (symbol, _) in enumerate(ranked, 1)}
    for row in rows:
        if row["symbol"] in ranks:
            row["rank"] = ranks[row["symbol"]]
            row["selection_reason"] = "QUALIFIED_NOT_SELECTED"
    anchors = ("BTCUSDT", "ETHUSDT")
    complete_rank = not issues and len(attempts) >= len(expected)
    chosen: list[str] = []
    if complete_rank and all(anchor in ranks for anchor in anchors) and len(ranked) >= 10:
        chosen = list(anchors) + [symbol for symbol, _ in ranked if symbol not in anchors][:8]
    for row in rows:
        if row["symbol"] in chosen:
            row["selected"] = True
            row["selection_reason"] = "ANCHOR" if row["symbol"] in anchors else "TOP_EIGHT_OTHER"
    status = "SELECTED_FOR_DATA_QUALIFICATION" if len(chosen) == 10 else "BLOCKED"
    if not complete_rank:
        status = "RANKING_INCOMPLETE"
    return {
        "schema": SCHEMA,
        "status": status,
        "scientific_scope": "EXPLORATORY",
        "ranking_cutoff": plan["ranking_cutoff"],
        "selection_freeze_at": utc_now(),
        "source_plan_sha256": sha((evidence / "plan.json").read_bytes()),
        "catalog_considered": plan["considered_catalog"],
        "candidate_records": rows,
        "ranked_symbols": [symbol for symbol, _ in ranked],
        "selected_symbols": chosen,
        "blockers": sorted(
            set(
                issues
                + ([] if all(a in ranks for a in anchors) else ["ANCHOR_NOT_QUALIFIED"])
                + ([] if len(ranked) >= 10 else ["FEWER_THAN_TEN_QUALIFIED"])
            )
        ),
        "new_attempt_count": len(attempts),
        "reused_recent_count": len(plan["reused_recent"]),
    }


def finalize(repo: Path, evidence: Path) -> dict[str, object]:
    plan = load_plan(repo, evidence)
    attempts = _attempts(evidence)
    projection = project(repo, evidence, plan, attempts)
    atomic_new(evidence / "selection.json", canonical(projection))
    files = sorted(path for path in evidence.iterdir() if path.is_file())
    manifest = {
        "schema": SCHEMA,
        "plan_sha256": sha((evidence / "plan.json").read_bytes()),
        "files": [
            {
                "file": path.name,
                "sha256": sha(path.read_bytes()),
                "byte_length": path.stat().st_size,
            }
            for path in files
        ],
    }
    atomic_new(evidence / "manifest.json", canonical(manifest))
    return projection


def verify(repo: Path, evidence: Path) -> dict[str, object]:
    plan = load_plan(repo, evidence)
    if sha((evidence / "selection.json").read_bytes()) != CAPTURE_SELECTION_SHA256:
        raise SelectionError("unknown original #175 selection identity")
    if sha((evidence / "manifest.json").read_bytes()) != CAPTURE_MANIFEST_SHA256:
        raise SelectionError("unknown original #175 manifest identity")
    manifest = read_json(evidence / "manifest.json")
    names = {item["file"] for item in manifest["files"]}
    if names != {
        path.name for path in evidence.iterdir() if path.is_file() and path.name != "manifest.json"
    }:
        raise SelectionError("manifest file set mismatch")
    for item in manifest["files"]:
        body = (evidence / item["file"]).read_bytes()
        if sha(body) != item["sha256"] or len(body) != item["byte_length"]:
            raise SelectionError("manifest hash/length mismatch")
    if manifest["plan_sha256"] != sha((evidence / "plan.json").read_bytes()):
        raise SelectionError("manifest plan mismatch")
    attempts = _attempts(evidence)
    computed = project(repo, evidence, plan, attempts)
    saved = read_json(evidence / "selection.json")
    computed.pop("selection_freeze_at")
    saved.pop("selection_freeze_at")
    if computed != saved:
        raise SelectionError("saved selection differs from raw-derived projection")
    return cast(dict[str, object], saved)


def _original_175(repo: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    evidence = repo / CAPTURE_BUNDLE
    selection = verify(repo, evidence)
    plan = load_plan(repo, evidence)
    attempts = _attempts(evidence)
    if (
        selection["status"] != "RANKING_INCOMPLETE"
        or len(attempts) != 171
        or sum(a["result"].get("http_status") == 200 for a in attempts) != 170
    ):
        raise SelectionError("original #175 acquisition outcome differs from reviewed evidence")
    missing = attempts[78]
    if (
        missing["started"]["query"] != KAS_QUERY
        or missing["started"]["path"] != ROUTE
        or missing["result"] != {"raw_file": None, "status": "NETWORK_UNAVAILABLE"}
    ):
        raise SelectionError("reviewed KAS attempt 079 is not the sole unresolved input")
    return evidence, plan, attempts


def build_kas_plan(repo: Path, *, code_revision: str, created_at_utc: str) -> dict[str, object]:
    original, plan, attempts = _original_175(repo)
    try:
        parsed = datetime.fromisoformat(created_at_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SelectionError("supplement plan time is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.now(UTC).utcoffset():
        raise SelectionError("supplement plan time must be UTC")
    start_path, result_path, _ = _attempt_files(original, 79)
    return {
        "schema": KAS_SCHEMA,
        "created_at_utc": created_at_utc,
        "capture_code_revision": code_revision,
        "capture_source_sha256": source_sha(),
        "verification_source_sha256": source_sha(),
        "original_capture_code_revision": CAPTURE_CODE_REVISION,
        "original_capture_source_sha256": CAPTURE_SOURCE_SHA256,
        "original_plan_sha256": CAPTURE_PLAN_SHA256,
        "original_selection_sha256": CAPTURE_SELECTION_SHA256,
        "original_manifest_sha256": CAPTURE_MANIFEST_SHA256,
        "kas_original_started_sha256": sha(start_path.read_bytes()),
        "kas_original_result_sha256": sha(result_path.read_bytes()),
        "original_attempt_count": len(attempts),
        "original_success_count": 170,
        "original_reused_recent_count": len(plan["reused_recent"]),
        "method": "GET",
        "host": HOST,
        "path": ROUTE,
        "query": KAS_QUERY,
        "distinct_query_budget": 1,
        "attempt_budget": 2,
        "timeout_seconds": 20,
        "maximum_response_bytes": MAX_BODY,
        "minimum_seconds_between_attempts": 1,
    }


def create_kas_plan(repo: Path, evidence: Path) -> dict[str, object]:
    if evidence.resolve() != (repo / KAS_SUPPLEMENT).resolve():
        raise SelectionError("KAS supplement must use its single fixed evidence directory")
    if evidence.exists():
        raise SelectionError("KAS supplement already exists; no replacement budget")
    plan = build_kas_plan(repo, code_revision=revision(repo), created_at_utc=utc_now())
    evidence.mkdir(parents=True)
    atomic_new(evidence / "plan.json", canonical(plan))
    return plan


def load_kas_plan(repo: Path, evidence: Path) -> dict[str, Any]:
    if evidence.resolve() != (repo / KAS_SUPPLEMENT).resolve():
        raise SelectionError("KAS supplement path is not the frozen location")
    body = (evidence / "plan.json").read_bytes()
    plan = read_json(evidence / "plan.json")
    if plan.get("capture_source_sha256") != source_sha():
        raise SelectionError("unknown KAS capture/verification source generation")
    expected = build_kas_plan(
        repo,
        code_revision=plan["capture_code_revision"],
        created_at_utc=plan["created_at_utc"],
    )
    if plan != expected or body != canonical(plan):
        raise SelectionError("KAS supplement plan does not match reviewed original evidence")
    return cast(dict[str, Any], plan)


def _sanitized_transport_failure(exc: Exception) -> dict[str, object]:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        status = "NETWORK_TIMEOUT"
    elif isinstance(reason, socket.gaierror):
        status = "DNS_UNAVAILABLE"
    elif isinstance(reason, ssl.SSLError):
        status = "TLS_UNAVAILABLE"
    elif isinstance(exc, SelectionError):
        status = "TRANSPORT_POLICY_ERROR"
    elif isinstance(exc, urllib.error.URLError):
        status = "NETWORK_UNAVAILABLE"
    else:
        status = "UNEXPECTED_TRANSPORT_ERROR"
    numeric_errno = getattr(reason, "errno", None)
    return {
        "status": status,
        "raw_file": None,
        "exception_class": type(exc).__name__,
        "cause_class": type(reason).__name__,
        "errno": numeric_errno if isinstance(numeric_errno, int) else None,
        "captured_at": utc_now(),
    }


def capture_kas(repo: Path, evidence: Path, transport: Transport | None = None) -> dict[str, Any]:
    plan = load_kas_plan(repo, evidence)
    if any(evidence.glob("*.started.json")) or (evidence / "manifest.json").exists():
        raise SelectionError("KAS supplement already attempted; no automatic resume")
    if plan["query"] != KAS_QUERY or plan["attempt_budget"] != 2:
        raise SelectionError("KAS-only request/budget mismatch")
    transport = transport or PublicDailyTransport()
    previous_send: float | None = None
    for index in (1, 2):
        if previous_send is not None:
            time.sleep(max(0.0, 1.0 - (time.monotonic() - previous_send)))
        started_file, result_file, raw_file = _attempt_files(evidence, index)
        atomic_new(
            started_file,
            canonical(
                {
                    "index": index,
                    "at": utc_now(),
                    "method": "GET",
                    "path": ROUTE,
                    "query": KAS_QUERY,
                    "retry": index - 1,
                    "plan_sha256": sha((evidence / "plan.json").read_bytes()),
                }
            ),
        )
        previous_send = time.monotonic()
        try:
            status, body = transport.get(ROUTE, KAS_QUERY, 20)
        except Exception as exc:
            failure = _sanitized_transport_failure(exc)
            atomic_new(result_file, canonical(failure))
            if index == 1 and failure["status"] == "NETWORK_TIMEOUT":
                continue
            break
        if len(body) > MAX_BODY:
            atomic_new(
                result_file,
                canonical(
                    {
                        "status": "TRANSPORT_POLICY_ERROR",
                        "raw_file": None,
                        "exception_class": "ResponseSizeExceeded",
                        "cause_class": "ResponseSizeExceeded",
                        "errno": None,
                        "captured_at": utc_now(),
                    }
                ),
            )
            break
        atomic_new(raw_file, body)
        atomic_new(
            result_file,
            canonical(
                {
                    "status": "HTTP",
                    "http_status": status,
                    "raw_file": raw_file.name,
                    "byte_length": len(body),
                    "sha256": sha(body),
                    "captured_at": utc_now(),
                }
            ),
        )
        if index == 1 and retryable(status=status):
            continue
        break
    return finalize_kas(repo, evidence)


def _kas_attempts(evidence: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = _attempts(evidence)
    if not 1 <= len(attempts) <= 2:
        raise SelectionError("KAS supplement attempts outside 1-2 bound")
    for index, attempt in enumerate(attempts, 1):
        started, result = attempt["started"], attempt["result"]
        if (
            started["query"] != KAS_QUERY
            or started["path"] != ROUTE
            or started["method"] != "GET"
            or started["retry"] != index - 1
            or started["plan_sha256"] != sha((evidence / "plan.json").read_bytes())
        ):
            raise SelectionError("KAS attempt differs from frozen query")
        if result["status"] == "HTTP":
            allowed_retry = retryable(status=result["http_status"])
        else:
            allowed_retry = result["status"] == "NETWORK_TIMEOUT"
            if set(result) != {
                "status",
                "raw_file",
                "exception_class",
                "cause_class",
                "errno",
                "captured_at",
            }:
                raise SelectionError("sanitized KAS transport result schema mismatch")
        if index == 1 and len(attempts) == 2 and not allowed_retry:
            raise SelectionError("KAS retry not justified by timeout or HTTP 5xx")
        if result.get("http_status") in (401, 403, 429) and len(attempts) > index:
            raise SelectionError("KAS route denial followed by another request")
    return attempts


def combined_kas_projection(repo: Path, evidence: Path, plan: dict[str, Any]) -> dict[str, Any]:
    original, original_plan, original_attempts = _original_175(repo)
    attempts = _kas_attempts(evidence, plan)
    successful = [
        item["result"]
        for item in attempts
        if item["result"]["status"] == "HTTP" and item["result"]["http_status"] == 200
    ]
    if len(successful) > 1:
        raise SelectionError("duplicate KAS successful responses")
    supplement = None
    if successful:
        name = successful[0]["raw_file"]
        supplement = ((evidence / name).read_bytes(), name)
    combined = project(
        repo,
        original,
        original_plan,
        original_attempts,
        kas_supplement=supplement,
    )
    combined.update(
        {
            "schema": KAS_SCHEMA,
            "original_status": "RANKING_INCOMPLETE",
            "original_plan_sha256": CAPTURE_PLAN_SHA256,
            "original_selection_sha256": CAPTURE_SELECTION_SHA256,
            "original_manifest_sha256": CAPTURE_MANIFEST_SHA256,
            "original_capture_source_sha256": CAPTURE_SOURCE_SHA256,
            "verification_source_sha256": source_sha(),
            "supplement_plan_sha256": sha((evidence / "plan.json").read_bytes()),
            "supplement_attempt_count": len(attempts),
            "total_issue_175_attempt_count": 171 + len(attempts),
        }
    )
    return combined


def finalize_kas(repo: Path, evidence: Path) -> dict[str, Any]:
    plan = load_kas_plan(repo, evidence)
    combined = combined_kas_projection(repo, evidence, plan)
    atomic_new(evidence / "combined_selection.json", canonical(combined))
    files = sorted(path for path in evidence.iterdir() if path.is_file())
    manifest = {
        "schema": KAS_SCHEMA,
        "original_manifest_sha256": CAPTURE_MANIFEST_SHA256,
        "plan_sha256": sha((evidence / "plan.json").read_bytes()),
        "capture_source_sha256": plan["capture_source_sha256"],
        "files": [
            {
                "file": path.name,
                "sha256": sha(path.read_bytes()),
                "byte_length": path.stat().st_size,
            }
            for path in files
        ],
    }
    atomic_new(evidence / "manifest.json", canonical(manifest))
    return combined


def verify_kas(repo: Path, evidence: Path) -> dict[str, Any]:
    plan = load_kas_plan(repo, evidence)
    manifest = read_json(evidence / "manifest.json")
    names = {item["file"] for item in manifest["files"]}
    actual = {
        path.name for path in evidence.iterdir() if path.is_file() and path.name != "manifest.json"
    }
    if names != actual or manifest["schema"] != KAS_SCHEMA:
        raise SelectionError("KAS manifest file set/schema mismatch")
    if (
        manifest["original_manifest_sha256"] != CAPTURE_MANIFEST_SHA256
        or manifest["capture_source_sha256"] != source_sha()
        or manifest["plan_sha256"] != sha((evidence / "plan.json").read_bytes())
    ):
        raise SelectionError("KAS manifest lineage mismatch")
    for item in manifest["files"]:
        body = (evidence / item["file"]).read_bytes()
        if sha(body) != item["sha256"] or len(body) != item["byte_length"]:
            raise SelectionError("KAS file hash/size mismatch")
    expected = combined_kas_projection(repo, evidence, plan)
    saved = read_json(evidence / "combined_selection.json")
    expected.pop("selection_freeze_at")
    saved.pop("selection_freeze_at")
    if expected != saved:
        raise SelectionError("KAS combined selection differs from raw-derived projection")
    return cast(dict[str, Any], saved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "kas-plan", "kas-capture", "kas-verify"])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "verify":
        result = verify(args.repo, args.evidence)
    elif args.action == "kas-plan":
        result = create_kas_plan(args.repo, args.evidence)
    elif args.action == "kas-capture":
        result = capture_kas(args.repo, args.evidence)
    else:
        result = verify_kas(args.repo, args.evidence)
    print(
        canonical(
            {
                "status": result.get("status", "PLANNED"),
                "missing_queries": len(cast(list[str], result.get("missing_symbols", []))),
                "selected": result.get("selected_symbols", []),
            }
        ).decode()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
