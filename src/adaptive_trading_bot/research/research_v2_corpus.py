"""Bounded, research-data-only WEEX V3 LAST corpus for Issue #179.

This module does not admit symbols to a strategy, replay or runtime source.
All requested ranges and identities are fixed before public acquisition.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, Inexact, InvalidOperation, Rounded, localcontext
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

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
START = datetime(2026, 7, 16, tzinfo=UTC)
END = datetime(2026, 10, 14, tzinfo=UTC)
RECENT_START = datetime(2026, 9, 14, tzinfo=UTC)
DAY = timedelta(days=1)
FIVE = timedelta(minutes=5)
HOST = "https://api-contract.weex.com"
ROUTE = "/capi/v3/market/historyKlines"
SCHEMA = "research-v2-weex-last-90d-v1"
DATASET_VERSION = "weex-research-v2-last-90d-issue-179-v1"
OUTPUT_ROOT = "data/research_corpus/issue-179-weex-last-90d-v1"
MAX_BODY = 2_000_000
SELECTION_SHA = "80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4"
SELECTION_MANIFEST_SHA = "de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79"
OLD_MANIFESTS = {
    "issue-173-weex-public-feasibility-332db22": (
        "88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac"
    ),
    "issue-173-weex-prefix-supplement-2dd18db": (
        "34c6e5153f7654eb6e66b004a0d57f9ce5df628229043209c48b809d5a906a87"
    ),
    "issue-175-weex-current-panel-cf541a3": (
        "ea5378964cea4b0aa767940281bf781bac3511269ba19f25e57ef46fd642c0ff"
    ),
}
OLD_ROOT = Path("data/research_evidence")
HELPERS = (
    Path("scripts/research/research_v2_corpus.py"),
    Path("scripts/research/research_v2_universe_feasibility.py"),
    Path("scripts/research/research_v2_snapshot_selection.py"),
)
DECIMAL_CONTEXT = Context(prec=100)
DECIMAL_CONTEXT.traps[Inexact] = True
DECIMAL_CONTEXT.traps[Rounded] = True
ORIGINAL_PLAN_SHA = "b98eb68a1d9d62f04c05a6245d2c2ddd70a85c0cde6b8b5b8559e78cb9dffac6"
ORIGINAL_SOURCE_SHA = "fb95f011706a3c00b94cebdf10f943cee62e3fb4f8866e51e811acbcd96601ed"
ORIGINAL_DOCS_SHA = "7eaa3a08c4f570f4cde8c110e616f55d271719eb45e318b6c81e81ae808582ce"
ORIGINAL_HELPERS = {
    "scripts/research/research_v2_corpus.py": (
        "4a8724ea5b1c461f41d5b73bcdb8bfc257bccdd50e3607930d30cb76632c5640"
    ),
    "scripts/research/research_v2_snapshot_selection.py": (
        "c5e7cb15ad58527b4febbf764068cdc9374cdc064f282003491ac6c111af60a7"
    ),
    "scripts/research/research_v2_universe_feasibility.py": (
        "623ef9825ef09e75549f1222d035efc49e27abe9f1a93180cd8ab5ae93f66fd1"
    ),
}
FIELDS = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "close_time_ms",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)


class CorpusError(ValueError):
    """Frozen corpus provenance or contents are unavailable or inconsistent."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def utc_ms(value: datetime) -> int:
    return int(value.timestamp()) * 1000


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def now() -> str:
    return stamp(datetime.now(UTC))


def checked_old(repo: Path) -> dict[str, Path]:
    roots = {name: repo / OLD_ROOT / name for name in OLD_MANIFESTS}
    for name, expected in OLD_MANIFESTS.items():
        if sha((roots[name] / "manifest.json").read_bytes()) != expected:
            raise CorpusError(f"locked predecessor manifest mismatch: {name}")
    selected = repo / OLD_ROOT / "issue-175-weex-kas-completion-v1"
    if sha((selected / "manifest.json").read_bytes()) != SELECTION_MANIFEST_SHA:
        raise CorpusError("locked selection manifest mismatch")
    if sha((selected / "combined_selection.json").read_bytes()) != SELECTION_SHA:
        raise CorpusError("locked selection projection mismatch")
    roots["selection"] = selected
    return roots


def query(symbol: str, interval: str, start: datetime, end: datetime) -> dict[str, str]:
    if symbol not in PANEL or interval not in {"5m", "1d"} or start >= end:
        raise CorpusError("query identity is outside the frozen panel")
    return {
        "symbol": symbol,
        "interval": interval,
        "startTime": str(utc_ms(start)),
        "endTime": str(utc_ms(end) - 1),
        "limit": "100",
        "priceType": "LAST",
    }


def _reuse(
    repo: Path, path: Path, expected_sha: str, q: dict[str, str], captured_at: str
) -> dict[str, object]:
    if sha(path.read_bytes()) != expected_sha:
        raise CorpusError(f"locked reused response mismatch: {path.name}")
    return {
        "path": path.relative_to(repo).as_posix(),
        "sha256": expected_sha,
        "bytes": path.stat().st_size,
        "query": q,
        "captured_at": captured_at,
    }


def make_plan(repo: Path, source_sha: str, source_revision: str, docs_sha: str) -> dict[str, Any]:
    """Freeze exact query/reuse inventory using accepted predecessor bytes only."""
    roots = checked_old(repo)
    selection = read_json(roots["selection"] / "combined_selection.json")
    if (
        selection["selected_symbols"] != list(PANEL)
        or selection["status"] != "SELECTED_FOR_DATA_QUALIFICATION"
    ):
        raise CorpusError("selection membership/status differs from accepted projection")
    records = {item["symbol"]: item for item in selection["candidate_records"]}
    old_attempts: list[tuple[Path, dict[str, Any]]] = []
    for name in list(OLD_MANIFESTS)[:2]:
        root = roots[name]
        for attempt in read_json(root / "attempts.json"):
            if attempt.get("status") == "HTTP_OK" and attempt.get("file"):
                old_attempts.append((root, attempt))
    anchor_start = END - DAY
    reused: list[dict[str, object]] = []
    for symbol in PANEL[:2]:
        matching = [
            (root, row)
            for root, row in old_attempts
            if row["query"].get("symbol") == symbol
            and row["query"].get("interval") == "5m"
            and row["query"].get("startTime") == str(utc_ms(anchor_start))
            and row["query"]["endTime"] <= str(utc_ms(END) - 1)
        ]
        # Original 100-row suffix plus two prefix requests, all required as separate generations.
        matching = [(root, row) for root, row in matching if "recent" in row["name"]]
        if len(matching) != 3:
            raise CorpusError(f"expected three locked recent anchor responses: {symbol}")
        for root, row in matching:
            reused.append(
                _reuse(repo, root / row["file"], row["sha256"], row["query"], row["captured_at"])
            )
    for symbol in PANEL:
        record = records[symbol]
        if (
            record["metadata_status"] != "METADATA_ELIGIBLE"
            or record["base"] != symbol[:-4]
            or record["quote"] != "USDT"
            or record["margin"] != "USDT"
            or record["contract_type"] != "PERPETUAL"
            or record["underlying_type"] != "COIN"
            or record["daily_status"] != "COMPLETE_30D"
        ):
            raise CorpusError(f"selection identity/unit fact unavailable: {symbol}")
        ref = record["raw_reference"]
        if ref["source"] == "ORIGINAL_173":
            root = roots["issue-173-weex-public-feasibility-332db22"]
            matching_capture = [row for _, row in old_attempts if row.get("file") == ref["file"]]
            if len(matching_capture) != 1:
                raise CorpusError(f"recent daily capture provenance unavailable: {symbol}")
            captured_at = matching_capture[0]["captured_at"]
        elif ref["source"] == "NEW_175":
            root = roots["issue-175-weex-current-panel-cf541a3"]
            result = read_json(root / ref["file"].replace(".raw.json", ".result.json"))
            if result["sha256"] != ref["sha256"] or result["raw_file"] != ref["file"]:
                raise CorpusError(f"recent daily result provenance mismatch: {symbol}")
            captured_at = result["captured_at"]
        else:
            raise CorpusError(f"unrecognized recent daily provenance: {symbol}")
        reused.append(
            _reuse(
                repo,
                root / ref["file"],
                ref["sha256"],
                query(symbol, "1d", RECENT_START, END),
                captured_at,
            )
        )
    requests: list[dict[str, object]] = []
    for symbol in PANEL:
        for day_index in range(90):
            day = START + day_index * DAY
            if symbol in PANEL[:2] and day == anchor_start:
                continue
            for chunk_index, (offset, length) in enumerate(((0, 100), (100, 100), (200, 88))):
                begin = day + offset * FIVE
                end = begin + length * FIVE
                requests.append(
                    {
                        "id": f"{symbol}-{day_index:02d}-{chunk_index}",
                        "query": query(symbol, "5m", begin, end),
                        "expected_rows": length,
                    }
                )
        requests.append(
            {
                "id": f"{symbol}-earlier-daily",
                "query": query(symbol, "1d", START, RECENT_START),
                "expected_rows": 60,
            }
        )
    if len(requests) != 2704 or len(reused) != 16:
        raise CorpusError("frozen request/reuse count mismatch")
    return {
        "schema": SCHEMA,
        "dataset_version": DATASET_VERSION,
        "output_root": OUTPUT_ROOT,
        "source_revision": source_revision,
        "source_sha256": source_sha,
        "helper_sha256": {
            path.as_posix(): sha((repo / path).read_bytes().replace(b"\r\n", b"\n"))
            for path in HELPERS
        },
        "docs_sha256": docs_sha,
        "selection_sha256": SELECTION_SHA,
        "selection_manifest_sha256": SELECTION_MANIFEST_SHA,
        "old_manifests": OLD_MANIFESTS,
        "panel": list(PANEL),
        "start": stamp(START),
        "end": stamp(END),
        "endpoint": HOST + ROUTE,
        "price_type": "LAST",
        "requests": requests,
        "reused": reused,
        "distinct_budget": len(requests),
        "attempt_budget": len(requests) * 2,
        "minimum_seconds_between_attempts": 1,
        "timeout_seconds": 20,
        "max_response_bytes": MAX_BODY,
        "request_weight": 5,
        "archive_schema": "raw-response-zip-deflate-v1",
        "quantity_model": (
            "one normalized base-asset unit; quote exposure=P*base quantity; "
            "not historical venue contract count"
        ),
        "historical_contract_specification": "UNRESOLVED",
    }


def validate_plan(repo: Path, plan: dict[str, Any], source_file: Path, docs_file: Path) -> None:
    if plan.get("source_sha256") == ORIGINAL_SOURCE_SHA:
        # The captured generation is immutable. Its source bytes are identified by
        # the original plan, not by whichever verifier revision is installed now.
        expected = make_plan(repo, ORIGINAL_SOURCE_SHA, plan["source_revision"], ORIGINAL_DOCS_SHA)
        expected["helper_sha256"] = ORIGINAL_HELPERS
        if plan["source_revision"] != "1c723a523544f1e13ab9ae5ac1b63b660b0e3343":
            raise CorpusError("original capture source revision mismatch")
    else:
        expected = make_plan(
            repo,
            sha(source_file.read_bytes().replace(b"\r\n", b"\n")),
            plan["source_revision"],
            sha(docs_file.read_bytes().replace(b"\r\n", b"\n")),
        )
    if plan != expected:
        raise CorpusError("frozen plan/source/old evidence mismatch")
    ids = [entry["id"] for entry in plan["requests"]]
    if len(ids) != len(set(ids)) or len(ids) != plan["distinct_budget"]:
        raise CorpusError("duplicate or missing query identity")
    validate_original_inventory(plan)


def validate_original_inventory(plan: dict[str, Any]) -> None:
    """Check fixed query semantics even when a plan/start hash is forged together."""
    expected: list[dict[str, object]] = []
    for symbol in PANEL:
        for day_index in range(90):
            day = START + day_index * DAY
            if symbol in PANEL[:2] and day_index == 89:
                continue
            for chunk_index, (offset, length) in enumerate(((0, 100), (100, 100), (200, 88))):
                begin = day + offset * FIVE
                expected.append(
                    {
                        "id": f"{symbol}-{day_index:02d}-{chunk_index}",
                        "query": query(symbol, "5m", begin, begin + length * FIVE),
                        "expected_rows": length,
                    }
                )
        expected.append(
            {
                "id": f"{symbol}-earlier-daily",
                "query": query(symbol, "1d", START, RECENT_START),
                "expected_rows": 60,
            }
        )
    if (
        plan.get("requests") != expected
        or plan.get("panel") != list(PANEL)
        or plan.get("start") != stamp(START)
        or plan.get("end") != stamp(END)
        or plan.get("endpoint") != HOST + ROUTE
        or plan.get("price_type") != "LAST"
        or plan.get("distinct_budget") != 2704
        or plan.get("attempt_budget") != 5408
    ):
        raise CorpusError("plan request inventory/policy differs from frozen protocol")


def _number(value: object, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise CorpusError("wire number must be a decimal string/integer")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CorpusError("wire number is malformed") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise CorpusError("wire number is nonfinite or outside bounds")
    return result


@dataclass(frozen=True, slots=True)
class Row:
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    base_volume: Decimal
    close_time_ms: int
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal

    def record(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {}
        for field in FIELDS:
            value = getattr(self, field)
            result[field] = value if isinstance(value, int) else str(value)
        return result


def parse_rows(body: bytes, q: dict[str, str]) -> list[Row]:
    if len(body) > MAX_BODY:
        raise CorpusError("raw response exceeds body cap")
    try:
        payload = json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CorpusError("response is not JSON") from exc
    if not isinstance(payload, list) or len(payload) > 100:
        raise CorpusError("response is not a bounded row array")
    step = 300_000 if q["interval"] == "5m" else 86_400_000
    start, end = int(q["startTime"]), int(q["endTime"])
    rows: list[Row] = []
    seen: set[int] = set()
    for item in payload:
        if not isinstance(item, list) or len(item) != 11:
            raise CorpusError("wire candle does not have exactly 11 fields")
        opened, closed, trades = item[0], item[6], item[8]
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (opened, closed, trades)
        ):
            raise CorpusError("wire timestamp/count is not an integer")
        if opened < start or opened > end or opened % step or closed != opened + step:
            raise CorpusError("wire candle is off-grid/out-of-request or has invalid close")
        if opened in seen or trades < 0:
            raise CorpusError("wire candle duplicate or invalid trade count")
        seen.add(opened)
        o, h, low, c = (_number(item[i], positive=True) for i in (1, 2, 3, 4))
        if low > min(o, c) or h < max(o, c) or low > h:
            raise CorpusError("wire OHLC ordering invalid")
        base, quote, taker_base, taker_quote = (_number(item[i]) for i in (5, 7, 9, 10))
        if taker_base > base or taker_quote > quote:
            raise CorpusError("taker volume exceeds total")
        rows.append(Row(opened, o, h, low, c, base, closed, quote, trades, taker_base, taker_quote))
    return sorted(rows, key=lambda row: row.open_time_ms)


def epochs(rows: dict[int, Row], step: int = 300_000) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for opened in sorted(rows):
        if not result or opened != result[-1][1]:
            result.append((opened, opened + step))
        else:
            result[-1] = (result[-1][0], opened + step)
    return result


def derive(rows: dict[int, Row], spans: list[tuple[int, int]], size: int) -> list[Row]:
    step = size * 300_000
    derived: list[Row] = []
    try:
        with localcontext(DECIMAL_CONTEXT):
            for begin, end in spans:
                aligned = ((begin + step - 1) // step) * step
                for opened in range(aligned, end - step + 1, step):
                    parts = [rows[opened + i * 300_000] for i in range(size)]
                    derived.append(
                        Row(
                            opened,
                            parts[0].open,
                            max(row.high for row in parts),
                            min(row.low for row in parts),
                            parts[-1].close,
                            sum((row.base_volume for row in parts), Decimal(0)),
                            opened + step,
                            sum((row.quote_volume for row in parts), Decimal(0)),
                            sum(row.trade_count for row in parts),
                            sum((row.taker_buy_base_volume for row in parts), Decimal(0)),
                            sum((row.taker_buy_quote_volume for row in parts), Decimal(0)),
                        )
                    )
    except (Inexact, Rounded) as exc:
        raise CorpusError(
            "derived Decimal arithmetic is not exact within the checked bound"
        ) from exc
    return derived


def exclusions(rows: dict[int, Row], reasons: dict[int, str]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for opened in range(utc_ms(START), utc_ms(END), 300_000):
        if opened in rows:
            continue
        reason = reasons.get(opened, "MISSING_GRID_ROW")
        if result and result[-1]["end_ms"] == opened and result[-1]["reason"] == reason:
            result[-1]["end_ms"] = opened + 300_000
            result[-1]["count"] = int(str(result[-1]["count"])) + 1
        else:
            result.append(
                {"start_ms": opened, "end_ms": opened + 300_000, "count": 1, "reason": reason}
            )
    return result


class Transport(Protocol):
    def get(self, q: dict[str, str], timeout: float) -> tuple[int, bytes]: ...


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


class PublicHistoryTransport:
    """One fixed unauthenticated GET route; redirects and ambient proxies disabled."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, q: dict[str, str], timeout: float) -> tuple[int, bytes]:
        validate_market_query(q)
        if timeout > 20 or timeout <= 0:
            raise CorpusError("transport policy rejected request")
        url = HOST + ROUTE + "?" + urllib.parse.urlencode(q)
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "atb-research-v2-corpus/1"}
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise CorpusError("response exceeds body cap")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise CorpusError("error response exceeds body cap") from None
            return exc.code, body


def retryable(status: int | None = None, error: BaseException | None = None) -> bool:
    if status is not None:
        return 500 <= status <= 599
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, (TimeoutError, socket.timeout))
    return isinstance(error, (TimeoutError, socket.timeout))


def validate_market_query(q: dict[str, str]) -> None:
    if set(q) != {"symbol", "interval", "startTime", "endTime", "limit", "priceType"}:
        raise CorpusError("market query fields differ from frozen route")
    symbol = q["symbol"]
    if symbol not in PANEL:
        raise CorpusError("market symbol outside selected panel")
    if q["interval"] == "1d":
        expected = query(symbol, "1d", START, RECENT_START)
    elif q["interval"] == "5m":
        try:
            opened = int(q["startTime"])
        except ValueError as exc:
            raise CorpusError("market 5m start is not milliseconds") from exc
        offset = opened - utc_ms(START)
        if offset < 0 or offset >= 90 * 86_400_000:
            raise CorpusError("market 5m start outside fixed window")
        day, within = divmod(offset, 86_400_000)
        if within not in {0, 100 * 300_000, 200 * 300_000}:
            raise CorpusError("market 5m chunk is not predeclared")
        if symbol in PANEL[:2] and day == 89:
            raise CorpusError("reused anchor day cannot be reacquired")
        length = 88 if within == 200 * 300_000 else 100
        begin = START + day * DAY + timedelta(milliseconds=within)
        expected = query(symbol, "5m", begin, begin + length * FIVE)
    else:
        raise CorpusError("market interval outside fixed protocol")
    if q != expected:
        raise CorpusError("market query differs from frozen 100/100/88 or daily shape")


def durable_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.with_suffix(path.suffix + ".pending").exists():
        raise CorpusError(f"existing/ambiguous evidence path: {path.name}")
    pending = path.with_suffix(path.suffix + ".pending")
    with pending.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)


def attempt_state(root: Path, request_id: str, number: int) -> dict[str, Any] | None:
    stem = root / "working" / request_id / str(number)
    if any(
        stem.with_suffix(suffix + ".pending").exists()
        for suffix in (".started.json", ".result.json", ".raw")
    ):
        raise CorpusError(f"ambiguous pending attempt: {request_id}/{number}")
    started, result = stem.with_suffix(".started.json"), stem.with_suffix(".result.json")
    if not started.exists() and not result.exists():
        return None
    if not started.exists() or not result.exists():
        raise CorpusError(f"ambiguous STARTED attempt: {request_id}/{number}")
    state: dict[str, Any] = read_json(result)
    if state["request_id"] != request_id or state["attempt"] != number:
        raise CorpusError("attempt identity mismatch")
    if "raw_sha256" in state:
        raw = stem.with_suffix(".raw")
        if not raw.exists() or sha(raw.read_bytes()) != state["raw_sha256"]:
            raise CorpusError("attempt raw bytes missing or tampered")
    return state


def _utc_event(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CorpusError("attempt timestamp is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CorpusError("attempt timestamp is malformed") from exc
    if parsed.tzinfo != UTC or stamp(parsed) != value:
        raise CorpusError("attempt timestamp is not exact UTC")
    return parsed


def _record_retryable(result: dict[str, Any]) -> bool:
    if "status" in result:
        status: object = result["status"]
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise CorpusError("attempt HTTP status invalid")
        expected_outcome = (
            "HTTP_OK"
            if status == 200
            else "DENIED"
            if status in {401, 403, 429}
            else "POLICY_FAILURE"
            if 300 <= status <= 399
            else "HTTP_FAILURE"
        )
        if result.get("outcome") != expected_outcome:
            raise CorpusError("attempt outcome contradicts HTTP status")
        if any(key in result for key in ("error_class", "reason_class", "errno")):
            raise CorpusError("HTTP attempt contains network-error facts")
        return 500 <= status <= 599
    if result.get("outcome") != "NETWORK_UNAVAILABLE":
        raise CorpusError("network attempt outcome/status inconsistent")
    error_class = result.get("error_class")
    reason_class = result.get("reason_class")
    if not isinstance(error_class, str) or not error_class:
        raise CorpusError("network error class missing")
    if reason_class is not None and not isinstance(reason_class, str):
        raise CorpusError("network reason class malformed")
    errno = result.get("errno")
    if errno is not None and (isinstance(errno, bool) or not isinstance(errno, int)):
        raise CorpusError("network errno malformed")
    return error_class in {"TimeoutError", "timeout"} or (
        error_class == "URLError" and reason_class in {"TimeoutError", "timeout"}
    )


def audit_ledger(
    root: Path,
    plan: dict[str, Any],
    members_by_symbol: dict[str, dict[str, bytes]] | None = None,
) -> dict[str, Any]:
    """Validate every durable attempt before capture or raw-to-report projection.

    Recorded retry flags are audit claims, never retry authority. Archive and
    working copies, when both present, must be byte-identical.
    """
    plan_path = root / "plan.json"
    plan_bytes = plan_path.read_bytes()
    if plan_bytes != canonical(plan):
        raise CorpusError("plan bytes differ from frozen canonical plan")
    plan_sha = sha(plan_bytes)
    if plan.get("schema") == SCHEMA:
        validate_original_inventory(plan)
        if plan.get("source_sha256") == ORIGINAL_SOURCE_SHA and plan_sha != ORIGINAL_PLAN_SHA:
            raise CorpusError("original plan identity changed")
    expected: dict[str, dict[str, Any]] = {entry["id"]: entry for entry in plan["requests"]}
    if len(expected) != len(plan["requests"]):
        raise CorpusError("duplicate planned request ID")
    allowed: set[str] = set()
    states: dict[tuple[str, int], dict[str, Any]] = {}
    previous_finished: datetime | None = None
    retries = 0
    unfinished_prefix = False
    for entry in plan["requests"]:
        request_id = entry["id"]
        previous: dict[str, Any] | None = None
        for number in (0, 1):
            stem = f"{request_id}/{number}"
            names = {
                suffix: f"{stem}{suffix}" for suffix in (".started.json", ".result.json", ".raw")
            }
            archive = (members_by_symbol or {}).get(entry["query"]["symbol"], {})
            record_bytes: dict[str, bytes] = {}
            for suffix, member_name in names.items():
                work_path = root / "working" / member_name
                if work_path.with_suffix(work_path.suffix + ".pending").exists():
                    raise CorpusError("ambiguous pending attempt")
                from_work = work_path.read_bytes() if work_path.exists() else None
                from_archive = archive.get(member_name)
                if from_work is not None and from_archive is not None and from_work != from_archive:
                    raise CorpusError("working/archive attempt copies disagree")
                content = from_archive if from_archive is not None else from_work
                if content is not None:
                    record_bytes[suffix] = content
                    allowed.add(member_name)
            if not record_bytes:
                if number == 0 or (
                    number == 1 and previous is not None and _record_retryable(previous)
                ):
                    unfinished_prefix = True
                continue
            if unfinished_prefix:
                raise CorpusError("attempt appears after an unattempted/pending plan cursor")
            if set(record_bytes) not in (
                {".started.json", ".result.json"},
                {".started.json", ".result.json", ".raw"},
            ):
                raise CorpusError("ambiguous STARTED attempt or orphaned raw/result")
            if number == 1 and (previous is None or not _record_retryable(previous)):
                raise CorpusError("retry lacks a genuinely retryable predecessor")
            if number == 1:
                retries += 1
            try:
                started = json.loads(record_bytes[".started.json"])
                result = json.loads(record_bytes[".result.json"])
            except (ValueError, UnicodeDecodeError) as exc:
                raise CorpusError("attempt JSON malformed") from exc
            if started != {
                "request_id": request_id,
                "attempt": number,
                "query": entry["query"],
                "started_at": started.get("started_at"),
                "plan_sha256": plan_sha,
            }:
                raise CorpusError("attempt start differs from planned query/generation")
            began = _utc_event(started["started_at"])
            ended = _utc_event(result.get("completed_at"))
            if ended < began or (previous_finished is not None and began < previous_finished):
                raise CorpusError("attempt chronology is inconsistent")
            previous_finished = ended
            if result.get("request_id") != request_id or result.get("attempt") != number:
                raise CorpusError("attempt result identity mismatch")
            actual_retryable = _record_retryable(result)
            if result.get("retryable") is not actual_retryable:
                raise CorpusError("recorded retry eligibility contradicts status/error")
            raw = record_bytes.get(".raw")
            if "status" in result:
                if (
                    raw is None
                    or result.get("raw_sha256") != sha(raw)
                    or result.get("raw_bytes") != len(raw)
                ):
                    raise CorpusError("HTTP raw presence/hash/length mismatch")
            elif raw is not None or "raw_sha256" in result or "raw_bytes" in result:
                raise CorpusError("network error cannot claim raw HTTP bytes")
            states[(request_id, number)] = result
            previous = result
    working = root / "working"
    if working.exists():
        for path in working.rglob("*"):
            if path.is_file():
                name = path.relative_to(working).as_posix()
                if name not in allowed:
                    raise CorpusError("unplanned/orphaned working evidence file")
                if members_by_symbol is not None and not any(
                    name in members for members in members_by_symbol.values()
                ):
                    raise CorpusError("working attempt missing from frozen archive")
    if members_by_symbol is not None:
        for members in members_by_symbol.values():
            if set(members) - allowed:
                raise CorpusError("unplanned/orphaned archived evidence member")
    if len(states) > plan["attempt_budget"]:
        raise CorpusError("cumulative attempt budget exceeded")
    return {
        "states": states,
        "attempts": len(states),
        "distinct": len({request_id for request_id, _ in states}),
        "retries": retries,
    }


def capture(
    root: Path, plan: dict[str, Any], transport: Transport | None = None, *, sleep: Any = None
) -> dict[str, int | str]:
    """Resume only unattempted shapes/retryable second attempts on one frozen ledger."""
    transport = transport or PublicHistoryTransport()
    sleep = sleep or __import__("time").sleep
    requests = plan["requests"]
    if plan.get("schema") != SCHEMA or len(requests) != 2704 or plan["attempt_budget"] != 5408:
        raise CorpusError("capture requires the exact frozen inventory/budget")
    audit_ledger(root, plan)
    transmitted = 0
    last_sent: float | None = None
    for entry in requests:
        request_id = entry["id"]
        q = entry["query"]
        for number in (0, 1):
            state = attempt_state(root, request_id, number)
            if state is not None:
                if state["outcome"] == "HTTP_OK":
                    break
                if not state["retryable"]:
                    if state["outcome"] in {"DENIED", "NETWORK_UNAVAILABLE", "POLICY_FAILURE"}:
                        return {"status": state["outcome"], "new_attempts": transmitted}
                    break
                if number == 1:
                    break
                continue
            if number == 1:
                predecessor = attempt_state(root, request_id, 0)
                if predecessor is None or not predecessor["retryable"]:
                    raise CorpusError("retry lacks a recorded retryable predecessor")
            stem = root / "working" / request_id / str(number)
            durable_new(
                stem.with_suffix(".started.json"),
                canonical(
                    {
                        "request_id": request_id,
                        "attempt": number,
                        "query": q,
                        "started_at": now(),
                        "plan_sha256": sha((root / "plan.json").read_bytes()),
                    }
                ),
            )
            if last_sent is not None:
                wait = 1.0 - (__import__("time").monotonic() - last_sent)
                if wait > 0:
                    sleep(wait)
            last_sent = __import__("time").monotonic()
            transmitted += 1
            try:
                status, body = transport.get(q, 20)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                result = {
                    "request_id": request_id,
                    "attempt": number,
                    "completed_at": now(),
                    "outcome": "NETWORK_UNAVAILABLE",
                    "retryable": retryable(error=exc),
                    "error_class": type(exc).__name__,
                    "reason_class": type(exc.reason).__name__
                    if isinstance(exc, urllib.error.URLError)
                    else None,
                    "errno": exc.errno if isinstance(exc, OSError) else None,
                }
            else:
                # Local persistence failures remain ambiguous STARTED, never a network retry.
                durable_new(stem.with_suffix(".raw"), body)
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
                    "completed_at": now(),
                    "status": status,
                    "outcome": outcome,
                    "retryable": retryable(status=status),
                    "raw_sha256": sha(body),
                    "raw_bytes": len(body),
                }
            durable_new(stem.with_suffix(".result.json"), canonical(result))
            outcome = result["outcome"]
            if (
                outcome in {"DENIED", "NETWORK_UNAVAILABLE", "POLICY_FAILURE"}
                and not result["retryable"]
            ):
                return {"status": outcome, "new_attempts": transmitted}
            if outcome == "HTTP_OK":
                break
            if not result["retryable"]:
                break
    return {"status": "CAPTURE_COMPLETE_OR_CONTENT_PARTIAL", "new_attempts": transmitted}


def _archive_path(root: Path, symbol: str) -> Path:
    return root / "archives" / f"{symbol}.zip"


def pack_attempts(root: Path, plan: dict[str, Any]) -> dict[str, str]:
    """Losslessly pack durable attempts; working files remain for safe same-ledger resume."""
    hashes: dict[str, str] = {}
    for symbol in PANEL:
        target = _archive_path(root, symbol)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise CorpusError("archive already exists; do not replace captured evidence")
        pending = target.with_suffix(".zip.pending")
        if pending.exists():
            raise CorpusError("ambiguous archive packaging state")
        with zipfile.ZipFile(
            pending, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=False
        ) as archive:
            for entry in plan["requests"]:
                if entry["query"]["symbol"] != symbol:
                    continue
                request_id = entry["id"]
                for number in (0, 1):
                    state = attempt_state(root, request_id, number)
                    if state is None:
                        continue
                    stem = root / "working" / request_id / str(number)
                    for suffix in (".started.json", ".raw", ".result.json"):
                        source = stem.with_suffix(suffix)
                        if not source.exists():
                            continue
                        member = f"{request_id}/{number}{suffix}"
                        info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        info.external_attr = 0o600 << 16
                        archive.writestr(
                            info,
                            source.read_bytes(),
                            compress_type=zipfile.ZIP_DEFLATED,
                            compresslevel=9,
                        )
        if pending.stat().st_size > 16 * 1024 * 1024:
            raise CorpusError("per-symbol archive exceeds 16 MiB")
        os.replace(pending, target)
        hashes[symbol] = sha(target.read_bytes())
    return hashes


def _archive_members(root: Path, symbol: str, expected_sha: str) -> dict[str, bytes]:
    path = _archive_path(root, symbol)
    if sha(path.read_bytes()) != expected_sha or path.stat().st_size > 16 * 1024 * 1024:
        raise CorpusError("archive hash/size mismatch")
    members: dict[str, bytes] = {}
    expanded_bytes = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename
            parts = name.split("/")
            if (
                len(parts) != 2
                or not parts[0].startswith(symbol + "-")
                or ".." in parts
                or name in members
                or info.file_size > MAX_BODY
                or not parts[1].startswith(("0.", "1."))
                or not parts[1].endswith((".started.json", ".result.json", ".raw"))
            ):
                raise CorpusError("archive member is unsafe or duplicated")
            expanded_bytes += info.file_size
            if expanded_bytes > 64 * 1024 * 1024:
                raise CorpusError("archive expansion exceeds per-symbol bound")
            members[name] = archive.read(info)
    return members


def _new_response(
    root: Path, entry: dict[str, Any], members: dict[str, bytes] | None
) -> tuple[bytes | None, str]:
    request_id = entry["id"]
    for number in (0, 1):
        if members is None:
            state = attempt_state(root, request_id, number)
            if state is None:
                return None, "NOT_ATTEMPTED"
            raw = (root / "working" / request_id / str(number)).with_suffix(".raw")
            body = raw.read_bytes() if raw.exists() else None
        else:
            result_name = f"{request_id}/{number}.result.json"
            started_name = f"{request_id}/{number}.started.json"
            if result_name not in members:
                if started_name in members:
                    raise CorpusError("archived attempt is STARTED without result")
                return None, "NOT_ATTEMPTED"
            if started_name not in members:
                raise CorpusError("archived result lacks attempt start")
            state = json.loads(members[result_name])
            body = members.get(f"{request_id}/{number}.raw")
        if state["request_id"] != request_id or state["attempt"] != number:
            raise CorpusError("response attempt identity mismatch")
        if body is not None and (
            sha(body) != state.get("raw_sha256") or len(body) != state.get("raw_bytes")
        ):
            raise CorpusError("response raw body mismatch")
        if state["outcome"] == "HTTP_OK":
            if body is None or state["status"] != 200:
                raise CorpusError("successful response has no valid body")
            if (
                number == 0
                and members is not None
                and any(name.startswith(f"{request_id}/1.") for name in members)
            ):
                raise CorpusError("successful first attempt cannot have a retry")
            return body, "HTTP_OK"
        if not state["retryable"] or number == 1:
            return None, state["outcome"]
    return None, "RETRY_UNAVAILABLE"


def _old_body(repo: Path, reference: dict[str, Any]) -> bytes:
    path = Path(reference["path"])
    if path.is_absolute() or ".." in path.parts or not path.parts[0] == "data":
        raise CorpusError("unsafe predecessor path")
    body = (repo / path).read_bytes()
    if sha(body) != reference["sha256"] or len(body) != reference["bytes"]:
        raise CorpusError("old response bytes do not match frozen reference")
    return body


def _rows_for_symbol(
    repo: Path, root: Path, plan: dict[str, Any], symbol: str, members: dict[str, bytes] | None
) -> tuple[dict[int, Row], dict[int, Row], dict[int, str], Counter[str]]:
    five: dict[int, Row] = {}
    daily: dict[int, Row] = {}
    reasons: dict[int, str] = {}
    counts: Counter[str] = Counter()
    references: list[tuple[dict[str, str], bytes, str]] = []
    for ref in plan["reused"]:
        if ref["query"]["symbol"] == symbol:
            references.append((ref["query"], _old_body(repo, ref), "REUSED"))
    for entry in plan["requests"]:
        if entry["query"]["symbol"] != symbol:
            continue
        body, status = _new_response(root, entry, members)
        counts[status] += 1
        if body is None:
            if entry["query"]["interval"] == "5m":
                for opened in range(
                    int(entry["query"]["startTime"]), int(entry["query"]["endTime"]) + 1, 300_000
                ):
                    reasons[opened] = status
            continue
        references.append((entry["query"], body, "NEW"))
    for q, body, provenance in references:
        try:
            parsed = parse_rows(body, q)
        except CorpusError:
            counts["MALFORMED_RESPONSE"] += 1
            if q["interval"] == "5m":
                for opened in range(int(q["startTime"]), int(q["endTime"]) + 1, 300_000):
                    reasons[opened] = "MALFORMED_RESPONSE"
            continue
        target = five if q["interval"] == "5m" else daily
        for row in parsed:
            previous = target.get(row.open_time_ms)
            if previous is not None and previous != row:
                raise CorpusError(f"conflicting {provenance} duplicate for {symbol}")
            target[row.open_time_ms] = row
        counts[f"{provenance}_{q['interval']}_ROWS"] += len(parsed)
    return five, daily, reasons, counts


def _daily_comparisons(five: dict[int, Row], daily: dict[int, Row]) -> list[dict[str, Any]]:
    compared: list[dict[str, Any]] = []
    try:
        with localcontext(DECIMAL_CONTEXT):
            for day in range(90):
                opened = utc_ms(START + day * DAY)
                parts = [five.get(opened + i * 300_000) for i in range(288)]
                if any(row is None for row in parts):
                    continue
                full = [row for row in parts if row is not None]
                daily_row = daily.get(opened)
                if daily_row is None:
                    compared.append({"day_open_ms": opened, "status": "DAILY_UNAVAILABLE"})
                    continue
                actual = {
                    "open": full[0].open,
                    "high": max(row.high for row in full),
                    "low": min(row.low for row in full),
                    "close": full[-1].close,
                    "base_volume": sum((row.base_volume for row in full), Decimal(0)),
                    "quote_volume": sum((row.quote_volume for row in full), Decimal(0)),
                    "taker_buy_base_volume": sum(
                        (row.taker_buy_base_volume for row in full), Decimal(0)
                    ),
                    "taker_buy_quote_volume": sum(
                        (row.taker_buy_quote_volume for row in full), Decimal(0)
                    ),
                    "trade_count": sum(row.trade_count for row in full),
                }
                deltas = {
                    field: str(value - getattr(daily_row, field)) for field, value in actual.items()
                }
                compared.append(
                    {
                        "day_open_ms": opened,
                        "status": "MATCH"
                        if all(Decimal(value) == 0 for value in deltas.values())
                        else "MISMATCH",
                        "deltas": deltas,
                    }
                )
    except (Inexact, Rounded) as exc:
        raise CorpusError("daily Decimal arithmetic is not exact within the checked bound") from exc
    return compared


def _report_symbol(
    repo: Path, root: Path, plan: dict[str, Any], symbol: str, members: dict[str, bytes] | None
) -> tuple[dict[str, Any], dict[str, list[Row]]]:
    five, daily, reasons, counts = _rows_for_symbol(repo, root, plan, symbol, members)
    spans = epochs(five)
    derived = {
        name: derive(five, spans, factor) for name, factor in (("15m", 3), ("1h", 12), ("4h", 48))
    }
    comparisons = _daily_comparisons(five, daily)
    warm = warmup_intervals(derived, spans)
    missing = exclusions(five, reasons)
    planned = [entry for entry in plan["requests"] if entry["query"]["symbol"] == symbol]
    new_attempts = (
        sum(name.endswith(".started.json") for name in members)
        if members is not None
        else sum(
            attempt_state(root, entry["id"], number) is not None
            for entry in planned
            for number in (0, 1)
        )
    )
    report: dict[str, Any] = {
        "symbol": symbol,
        "new_query_count": len(planned),
        "new_attempt_count": new_attempts,
        "reused_response_count": sum(ref["query"]["symbol"] == symbol for ref in plan["reused"]),
        "expected_5m_rows": 25920,
        "valid_5m_rows": len(five),
        "first_open_ms": min(five) if five else None,
        "last_open_ms": max(five) if five else None,
        "epochs": [
            {"start_ms": start, "end_ms": end, "rows": (end - start) // 300_000}
            for start, end in spans
        ],
        "exclusions": missing,
        "excluded_5m_rows": sum(int(str(item["count"])) for item in missing),
        "derived_rows": {name: len(value) for name, value in derived.items()},
        "usable_intervals": warm,
        "daily_comparisons": comparisons,
        "request_counts": dict(sorted(counts.items())),
        "identity_status": "CURRENT_CATALOG_ELIGIBLE_HISTORICAL_SPECS_UNRESOLVED",
        "normalized_quantity": "one base asset unit; no historical contract multiplier inferred",
        "status": "CORPUS_REVIEW_READY"
        if len(five) == 25920
        and len(daily) == 90
        and all(row["status"] == "MATCH" for row in comparisons)
        else "PARTIAL_OR_BLOCKED",
    }
    return report, {"5m": [five[key] for key in sorted(five)], **derived}


def warmup_intervals(
    derived: dict[str, list[Row]], spans: list[tuple[int, int]]
) -> list[dict[str, object]]:
    """Fifty *complete* 4h closes per epoch, then the first subsequent 15m open."""
    warm: list[dict[str, object]] = []
    for begin, end in spans:
        complete_4h = [
            row for row in derived["4h"] if begin <= row.open_time_ms and row.close_time_ms <= end
        ]
        if len(complete_4h) >= 50:
            boundary = complete_4h[49].close_time_ms
            usable = [row for row in derived["15m"] if row.open_time_ms >= boundary]
            usable = [row for row in usable if row.close_time_ms <= end]
            if usable:
                warm.append(
                    {
                        "start_ms": usable[0].open_time_ms,
                        "end_ms": usable[-1].close_time_ms,
                        "complete_4h_warmup": 50,
                        "boundary_observation_excluded": True,
                    }
                )
    return warm


def _table(symbol: str, timeframe: str, rows: list[Row]) -> pa.Table:
    values: dict[str, list[int | str]] = {
        "symbol": [symbol] * len(rows),
        "timeframe": [timeframe] * len(rows),
    }
    for field in FIELDS:
        values[field] = [row.record()[field] for row in rows]
    schema = pa.schema(
        [pa.field("symbol", pa.string()), pa.field("timeframe", pa.string())]
        + [
            pa.field(
                field,
                pa.int64() if field.endswith("_ms") or field == "trade_count" else pa.string(),
            )
            for field in FIELDS
        ]
    )
    return pa.Table.from_pydict(values, schema=schema)


def build(repo: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Project from raw evidence only; no market request or strategy execution."""
    checked_old(repo)
    if any((root / name).exists() for name in ("manifest.json", "report.json")):
        raise CorpusError("corpus already built; immutable output cannot be replaced")
    audit_ledger(root, plan)
    archive_hashes = pack_attempts(root, plan)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "dataset_version": DATASET_VERSION,
        "panel": list(PANEL),
        "start": stamp(START),
        "end": stamp(END),
        "selection_scope": "CURRENT_SNAPSHOT_EXPLORATORY",
        "source": HOST + ROUTE,
        "price_type": "LAST",
        "symbols": {},
    }
    artifact_hashes: dict[str, str] = {}
    for symbol in PANEL:
        members = _archive_members(root, symbol, archive_hashes[symbol])
        member_report, series = _report_symbol(repo, root, plan, symbol, members)
        report["symbols"][symbol] = member_report
        for timeframe, rows in series.items():
            path = root / "parquet" / f"{symbol}-{timeframe}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(_table(symbol, timeframe, rows), path, compression="zstd", version="2.6")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise CorpusError("Parquet shard exceeds per-file ceiling")
            artifact_hashes[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    report["ten_member_usable_overlap"] = _overlap(report["symbols"])
    durable_new(root / "report.json", canonical(report))
    artifact_hashes["report.json"] = sha((root / "report.json").read_bytes())
    artifact_hashes.update(
        {f"archives/{symbol}.zip": digest for symbol, digest in archive_hashes.items()}
    )
    manifest = {
        "schema": SCHEMA,
        "dataset_version": DATASET_VERSION,
        "status": "CORPUS_REVIEW_READY"
        if all(item["status"] == "CORPUS_REVIEW_READY" for item in report["symbols"].values())
        else "PARTIAL_OR_BLOCKED",
        "plan_sha256": sha((root / "plan.json").read_bytes()),
        "source_sha256": plan["source_sha256"],
        "selection_sha256": SELECTION_SHA,
        "panel": list(PANEL),
        "files": dict(sorted(artifact_hashes.items())),
    }
    durable_new(root / "manifest.json", canonical(manifest))
    total = sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and "working" not in path.parts
    )
    if total > 96 * 1024 * 1024:
        raise CorpusError("durable corpus exceeds 96 MiB")
    return {
        "manifest_sha256": sha((root / "manifest.json").read_bytes()),
        "report_sha256": artifact_hashes["report.json"],
        "bytes": total,
        "status": manifest["status"],
    }


def _overlap(reports: dict[str, Any]) -> list[dict[str, int]]:
    if not reports:
        return []
    boundaries = sorted(
        {
            int(item[key])
            for symbol in PANEL
            for item in reports[symbol]["usable_intervals"]
            for key in ("start_ms", "end_ms")
        }
    )
    result: list[dict[str, int]] = []
    for start, end in pairwise(boundaries):
        if all(
            any(
                item["start_ms"] <= start and end <= item["end_ms"]
                for item in reports[symbol]["usable_intervals"]
            )
            for symbol in PANEL
        ):
            if result and result[-1]["end_ms"] == start:
                result[-1]["end_ms"] = end
            else:
                result.append({"start_ms": start, "end_ms": end})
    return result


def verify(repo: Path, root: Path, source_file: Path, docs_file: Path) -> dict[str, Any]:
    """Independently reconstruct all persisted claims from predecessor/new raw bytes."""
    plan = read_json(root / "plan.json")
    validate_plan(repo, plan, source_file, docs_file)
    manifest = read_json(root / "manifest.json")
    if (
        manifest["schema"] != SCHEMA
        or manifest["dataset_version"] != DATASET_VERSION
        or manifest["panel"] != list(PANEL)
        or manifest["selection_sha256"] != SELECTION_SHA
        or manifest["plan_sha256"] != sha((root / "plan.json").read_bytes())
    ):
        raise CorpusError("manifest identity mismatch")
    if set(manifest["files"]) != (
        {"report.json"}
        | {f"archives/{symbol}.zip" for symbol in PANEL}
        | {
            f"parquet/{symbol}-{frame}.parquet"
            for symbol in PANEL
            for frame in ("5m", "15m", "1h", "4h")
        }
    ):
        raise CorpusError("manifest file inventory mismatch")
    for name, expected in manifest["files"].items():
        path = root / name
        if sha(path.read_bytes()) != expected:
            raise CorpusError(f"artifact hash mismatch: {name}")
    archived = {
        symbol: _archive_members(root, symbol, manifest["files"][f"archives/{symbol}.zip"])
        for symbol in PANEL
    }
    ledger = audit_ledger(root, plan, archived)
    recalculated: dict[str, Any] = {
        "schema": SCHEMA,
        "dataset_version": DATASET_VERSION,
        "panel": list(PANEL),
        "start": stamp(START),
        "end": stamp(END),
        "selection_scope": "CURRENT_SNAPSHOT_EXPLORATORY",
        "source": HOST + ROUTE,
        "price_type": "LAST",
        "symbols": {},
    }
    for symbol in PANEL:
        members = archived[symbol]
        allowed_ids = {
            entry["id"] for entry in plan["requests"] if entry["query"]["symbol"] == symbol
        }
        if any(name.split("/", 1)[0] not in allowed_ids for name in members):
            raise CorpusError("archive contains an unplanned request identity")
        report, series = _report_symbol(repo, root, plan, symbol, members)
        recalculated["symbols"][symbol] = report
        for frame, rows in series.items():
            actual = pq.read_table(root / "parquet" / f"{symbol}-{frame}.parquet")
            if not actual.equals(_table(symbol, frame, rows), check_metadata=True):
                raise CorpusError(f"Parquet projection differs from raw: {symbol}/{frame}")
    recalculated["ten_member_usable_overlap"] = _overlap(recalculated["symbols"])
    if canonical(recalculated) != (root / "report.json").read_bytes():
        raise CorpusError("coverage/qualification report differs from raw evidence")
    expected_status = (
        "CORPUS_REVIEW_READY"
        if all(item["status"] == "CORPUS_REVIEW_READY" for item in recalculated["symbols"].values())
        else "PARTIAL_OR_BLOCKED"
    )
    if manifest["status"] != expected_status:
        raise CorpusError("manifest qualification status differs from raw evidence")
    return {
        "manifest_sha256": sha((root / "manifest.json").read_bytes()),
        "status": expected_status,
        "symbols": recalculated["symbols"],
        "ledger": {key: ledger[key] for key in ("distinct", "attempts", "retries")},
    }


class LockedResearchV2CorpusReader:
    """Explicit offline data-only reader, never a legacy executable source."""

    def __init__(
        self,
        repo: Path,
        root: Path,
        *,
        dataset_version: str,
        manifest_sha256: str,
        source_file: Path,
        docs_file: Path,
    ) -> None:
        if dataset_version != DATASET_VERSION:
            raise CorpusError("unknown dataset version")
        if sha((root / "manifest.json").read_bytes()) != manifest_sha256:
            raise CorpusError("unknown/changed manifest version")
        self._verified = verify(repo, root, source_file, docs_file)
        manifest = read_json(root / "manifest.json")
        self._tables: dict[tuple[str, str], pa.Table] = {}
        for symbol in PANEL:
            for timeframe in ("5m", "15m", "1h", "4h"):
                name = f"parquet/{symbol}-{timeframe}.parquet"
                content = (root / name).read_bytes()
                if sha(content) != manifest["files"][name]:
                    raise CorpusError("Parquet changed during locked snapshot construction")
                self._tables[(symbol, timeframe)] = pq.read_table(pa.BufferReader(content))

    def data_only_epochs(self, symbol: str) -> tuple[tuple[int, int], ...]:
        """Validated contiguous price-data spans; not research admission or execution authority."""
        if symbol not in PANEL:
            raise CorpusError("foreign symbol")
        return tuple(
            (span["start_ms"], span["end_ms"])
            for span in self._verified["symbols"][symbol]["epochs"]
        )

    def read(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pa.Table:
        if symbol not in PANEL or timeframe not in {"5m", "15m", "1h", "4h"}:
            raise CorpusError("foreign symbol/timeframe")
        if (
            isinstance(start_ms, bool)
            or isinstance(end_ms, bool)
            or not isinstance(start_ms, int)
            or not isinstance(end_ms, int)
            or start_ms >= end_ms
        ):
            raise CorpusError("invalid read interval")
        step = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[timeframe]
        if start_ms % step or end_ms % step:
            raise CorpusError("read interval is off the exact timeframe grid")
        if not any(
            begin <= start_ms and end_ms <= finish
            for begin, finish in self.data_only_epochs(symbol)
        ):
            raise CorpusError("unqualified or cross-gap read")
        table = self._tables[(symbol, timeframe)]
        opened = table.column("open_time_ms").to_pylist()
        selected = [
            index
            for index, value in enumerate(opened)
            if isinstance(value, int) and start_ms <= value < end_ms
        ]
        if [opened[index] for index in selected] != list(range(start_ms, end_ms, step)):
            raise CorpusError("requested interval lacks complete exact timeframe coverage")
        return table.take(pa.array(selected, type=pa.int64()))
