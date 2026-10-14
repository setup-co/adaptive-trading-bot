"""Bounded public WEEX availability probe; no trading or runtime authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path
from statistics import median
from typing import Any, Protocol

HOST = "https://api-contract.weex.com"
ROUTES = frozenset(
    {
        "/capi/v3/market/time",
        "/capi/v3/market/exchangeInfo",
        "/capi/v3/market/apiTradingSymbols",
        "/capi/v3/market/historyKlines",
    }
)
H = datetime(2026, 1, 9, tzinfo=UTC)
DAY = timedelta(days=1)
FIVE = timedelta(minutes=5)
BUDGET = 64
MAX_BODY = 2_000_000
STABLE_UNDERLYINGS = frozenset({"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USD1"})
LEVERAGED = re.compile(r"(?:[235]L|[235]S|UP|DOWN|BULL|BEAR)$")
ORIGINAL_SOURCE_SHA256 = "89933757701faa8e1c078cfb6eb6c5ffedf9617e02fc186dd73fe553fe960ff3"
ORIGINAL_PLAN_SHA256 = "36ad7ba77ad52c254f48e55308aaf6e5ba77e4b5dac279934ea5d323ed0a9c71"
ORIGINAL_MANIFEST_SHA256 = "88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac"
DECIMAL_CONTEXT = Context(prec=80)
SUPPLEMENT_SCHEMA = "research-v2-universe-feasibility-prefix-supplement-v1"


class FeasibilityError(ValueError):
    """An unavailable or contradictory public-data fact."""


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


class PublicGetTransport:
    """Fixed host, no ambient proxy/auth/cookies, no redirect follow."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        validate_route("GET", path, query)
        url = HOST + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "atb-rv2-feasibility/1"}
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise FeasibilityError("response exceeds bounded size")
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise FeasibilityError("error response exceeds bounded size") from None
            return exc.code, body


def validate_route(method: str, path: str, query: dict[str, str]) -> None:
    if method != "GET" or path not in ROUTES:
        raise FeasibilityError("public GET route is not allowlisted")
    if path == "/capi/v3/market/historyKlines":
        if set(query) != {"symbol", "interval", "startTime", "endTime", "limit", "priceType"}:
            raise FeasibilityError("historical query shape is not allowlisted")
        if (
            not re.fullmatch(r"[A-Z0-9]+", query["symbol"])
            or query["interval"] not in {"1d", "5m"}
            or query["priceType"] != "LAST"
            or query["limit"] != "100"
            or not query["startTime"].isdigit()
            or not query["endTime"].isdigit()
            or int(query["startTime"]) > int(query["endTime"])
        ):
            raise FeasibilityError("historical query value is not allowlisted")
    elif query:
        raise FeasibilityError("catalog/time queries must be unfiltered")


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def utc_from_ms(value: object) -> datetime:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FeasibilityError("timestamp is not an integer millisecond value")
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def write_new(path: Path, body: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def raw_json(body: bytes) -> object:
    return json.loads(body.decode("utf-8"), parse_float=Decimal)


def number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise FeasibilityError("numeric field has unsupported type")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise FeasibilityError("numeric field is invalid") from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise FeasibilityError("numeric field is non-finite or out of bounds")
    return result


def candle_rows(
    body: bytes, start: datetime, end: datetime, interval: str
) -> list[tuple[int, Decimal]]:
    payload = raw_json(body)
    if not isinstance(payload, list):
        raise FeasibilityError("historical response is not the documented V3 array")
    step = DAY if interval == "1d" else FIVE
    step_ms = ms(start + step) - ms(start)
    rows: list[tuple[int, Decimal]] = []
    seen: set[int] = set()
    for item in payload:
        if not isinstance(item, list) or len(item) != 11:
            raise FeasibilityError("historical row is not the documented V3 11-field array")
        opened, closed = item[0], item[6]
        if not isinstance(opened, int) or isinstance(opened, bool) or not isinstance(closed, int):
            raise FeasibilityError("candle timestamp type is invalid")
        if opened < ms(start) or opened >= ms(end) or (opened - ms(start)) % step_ms:
            raise FeasibilityError("candle is outside requested UTC grid")
        if closed != opened + step_ms:
            raise FeasibilityError("candle close time is inconsistent")
        if opened in seen:
            raise FeasibilityError("duplicate candle open")
        seen.add(opened)
        op, high, low, close = (number(item[i], positive=True) for i in range(1, 5))
        if low > min(op, close) or high < max(op, close) or high < low:
            raise FeasibilityError("OHLC is incoherent")
        base, quote = number(item[5]), number(item[7])
        if (base == 0) != (quote == 0):
            raise FeasibilityError("base/quote volume zero inconsistency")
        if not isinstance(item[8], int) or isinstance(item[8], bool) or item[8] < 0:
            raise FeasibilityError("trade count is invalid")
        taker_base, taker_quote = number(item[9]), number(item[10])
        if taker_base > base or taker_quote > quote:
            raise FeasibilityError("taker volume exceeds total")
        rows.append((opened, quote))
    rows.sort()
    return rows


def complete(
    rows: list[tuple[int, Decimal]], start: datetime, end: datetime, interval: str
) -> bool:
    step = DAY if interval == "1d" else FIVE
    expected = [ms(start + i * step) for i in range((end - start) // step)]
    return [opened for opened, _ in rows] == expected


def select_probes(exchange_body: bytes, api_body: bytes) -> tuple[list[dict[str, str]], list[str]]:
    exchange = raw_json(exchange_body)
    api = raw_json(api_body)
    if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list):
        raise FeasibilityError("exchangeInfo schema is unsupported")
    if not isinstance(api, list) or not all(isinstance(item, str) for item in api):
        raise FeasibilityError("apiTradingSymbols schema is unsupported")
    api_set = set(api)
    considered: list[dict[str, str]] = []
    eligible: dict[str, str] = {}
    symbols = exchange["symbols"]
    if not all(isinstance(item, dict) and isinstance(item.get("symbol"), str) for item in symbols):
        raise FeasibilityError("exchangeInfo symbol item is malformed")
    for item in sorted(symbols, key=lambda value: str(value["symbol"])):
        if not isinstance(item, dict):
            raise FeasibilityError("exchangeInfo symbol item is malformed")
        symbol, base, quote, margin = (
            item.get(key) for key in ("symbol", "baseAsset", "quoteAsset", "marginAsset")
        )
        assert isinstance(symbol, str)
        base_text = base if isinstance(base, str) else ""
        reason = "ELIGIBLE_CURRENT"
        if not all(isinstance(v, str) and v for v in (base, quote, margin)):
            reason = "IDENTITY_INCOMPLETE"
        elif symbol not in api_set:
            reason = "NOT_API_SUPPORTED_CURRENT"
        elif item.get("contractType") != "PERPETUAL" or item.get("underlyingType") != "COIN":
            reason = "NOT_CRYPTO_LINEAR_PERPETUAL"
        elif quote != "USDT" or margin != "USDT":
            reason = "NOT_USDT_LINEAR"
        elif base_text in STABLE_UNDERLYINGS or LEVERAGED.search(base_text):
            reason = "EXCLUDED_UNDERLYING"
        elif not re.fullmatch(r"[A-Z0-9]+", symbol) or not re.fullmatch(r"[A-Z0-9]+", base_text):
            reason = "IDENTITY_UNSUPPORTED"
        elif symbol != f"{base_text}USDT":
            reason = "SYMBOL_BASE_QUOTE_MISMATCH"
        elif base_text in eligible:
            reason = "DUPLICATE_EXPOSURE"
        if reason == "ELIGIBLE_CURRENT":
            eligible[base_text] = symbol
        considered.append(
            {
                "symbol": symbol,
                "base": str(base) if base is not None else "",
                "quote": str(quote),
                "margin": str(margin),
                "reason": reason,
            }
        )
    if len({item["symbol"] for item in considered}) != len(considered):
        raise FeasibilityError("duplicate exchangeInfo symbol identity")
    for symbol in sorted(api_set - {item["symbol"] for item in considered}):
        considered.append(
            {
                "symbol": symbol,
                "base": "",
                "quote": "",
                "margin": "",
                "reason": "API_ONLY_NO_METADATA",
            }
        )
    anchors = [eligible[base] for base in ("BTC", "ETH") if base in eligible]
    others = sorted(symbol for base, symbol in eligible.items() if base not in {"BTC", "ETH"})[:8]
    return considered, anchors + others


class Capture:
    def __init__(self, root: Path, transport: Transport, *, budget: int = BUDGET) -> None:
        self.root = root
        self.transport = transport
        self.budget = budget
        self.attempts: list[dict[str, object]] = []
        self.blocked_routes: set[str] = set()
        self._last_start = 0.0
        self.root.mkdir(parents=True, exist_ok=False)

    def get(self, path: str, query: dict[str, str], name: str) -> bytes | None:
        validate_route("GET", path, query)
        if path in self.blocked_routes:
            raise FeasibilityError("market route blocked by prior denial")
        if len(self.attempts) >= self.budget:
            raise FeasibilityError("market request budget exhausted")
        for retry in range(2):
            if len(self.attempts) >= self.budget:
                raise FeasibilityError("market request budget exhausted")
            wait = 1.05 - (time.monotonic() - self._last_start)
            if wait > 0 and self._last_start:
                time.sleep(wait)
            self._last_start = time.monotonic()
            captured_at = iso(datetime.now(UTC))
            entry: dict[str, object] = {
                "method": "GET",
                "path": path,
                "query": query,
                "captured_at": captured_at,
                "retry": retry,
                "name": name,
                "status": "TRANSPORT_UNAVAILABLE",
            }
            self.attempts.append(entry)
            try:
                status, body = self.transport.get(path, query, 20.0)
                entry["http_status"] = status
                entry["byte_length"] = len(body)
                entry["sha256"] = digest(body)
                entry["file"] = f"{len(self.attempts):03d}-{name}.raw.json"
                write_new(self.root / str(entry["file"]), body)
                entry["status"] = "HTTP_OK" if status == 200 else "HTTP_REJECTED"
                if status == 200:
                    return body
                if status in {401, 403, 429}:
                    self.blocked_routes.add(path)
                    return None
                if status < 500 or retry:
                    return None
            except (TimeoutError, urllib.error.URLError, OSError):
                entry["status"] = "TRANSPORT_UNAVAILABLE"
                if retry:
                    return None
            except FeasibilityError:
                entry["status"] = "RESPONSE_SIZE_OR_TRANSPORT_POLICY_REJECTED"
                return None
            finally:
                self._save_attempts()
        return None

    def _save_attempts(self) -> None:
        target = self.root / "attempts.json"
        temp = self.root / "attempts.tmp"
        with temp.open("wb") as handle:
            handle.write(canonical(self.attempts))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)


def history_query(symbol: str, interval: str, start: datetime, end: datetime) -> dict[str, str]:
    return {
        "symbol": symbol,
        "interval": interval,
        "startTime": str(ms(start)),
        "endTime": str(ms(end) - 1),
        "limit": "100",
        "priceType": "LAST",
    }


def capture_history(
    capture: Capture, symbol: str, interval: str, start: datetime, end: datetime, tag: str
) -> dict[str, object]:
    gathered: list[tuple[int, Decimal]] = []
    page_end = end
    pages = 0
    error: str | None = None
    max_pages = 1 if interval == "1d" else 3
    while page_end > start and pages < max_pages:
        pages += 1
        try:
            body = capture.get(
                "/capi/v3/market/historyKlines",
                history_query(symbol, interval, start, page_end),
                f"{tag}-{symbol}-{interval}-{pages}",
            )
        except FeasibilityError as exc:
            error = (
                "BUDGET_EXHAUSTED"
                if "budget" in str(exc)
                else "ROUTE_BLOCKED"
                if "route blocked" in str(exc)
                else "REQUEST_REJECTED"
            )
            break
        if body is None:
            error = "TRANSPORT_OR_HTTP_UNAVAILABLE"
            break
        try:
            rows = candle_rows(body, start, page_end, interval)
        except (FeasibilityError, ValueError, TypeError):
            error = "UNSUPPORTED_OR_INVALID_HISTORY_SCHEMA"
            break
        if not rows or (gathered and rows[-1][0] >= gathered[0][0]):
            error = "EMPTY_OR_NON_ADVANCING_PAGE"
            break
        if len(rows) > 100:
            error = "PAGE_EXCEEDS_LIMIT"
            break
        gathered = rows + gathered
        next_end = utc_from_ms(rows[0][0])
        if next_end >= page_end:
            error = "EMPTY_OR_NON_ADVANCING_PAGE"
            break
        page_end = next_end
        if len(rows) < 100 and page_end > start:
            break
    if error is None and page_end > start and pages >= max_pages:
        error = "PAGE_LIMIT_INCOMPLETE"
    return result_from_rows(symbol, interval, start, end, gathered, pages, error)


def result_from_rows(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    gathered: list[tuple[int, Decimal]],
    pages: int,
    error: str | None = None,
) -> dict[str, object]:
    gathered = sorted(gathered)
    if len({opened for opened, _ in gathered}) != len(gathered):
        raise FeasibilityError("duplicate candle across pages")
    is_complete = error is None and complete(gathered, start, end, interval)
    quote = [str(value) for _, value in gathered]
    with localcontext(DECIMAL_CONTEXT):
        diagnostic = (
            str(median(Decimal(v) for v in quote)) if is_complete and interval == "1d" else None
        )
    return {
        "symbol": symbol,
        "interval": interval,
        "start": iso(start),
        "end": iso(end),
        "pages": pages,
        "rows": len(gathered),
        "complete": is_complete,
        "status": error or ("COMPLETE" if is_complete else "INCOMPLETE_GRID"),
        "open_times": [opened for opened, _ in gathered],
        "quote_volumes": quote,
        "median_quote_volume": diagnostic,
    }


def source_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def capture_bundle(root: Path, transport: Transport | None = None) -> dict[str, object]:
    source = Path(__file__).resolve()
    source_hash = digest(source.read_bytes())
    capture = Capture(root, transport or PublicGetTransport())
    boot: dict[str, bytes] = {}
    for key, path in (
        ("server_time", "/capi/v3/market/time"),
        ("exchange_info", "/capi/v3/market/exchangeInfo"),
        ("api_trading_symbols", "/capi/v3/market/apiTradingSymbols"),
    ):
        body = capture.get(path, {}, key)
        if body is None:
            return finalize(capture, source_hash, None, [], [], "BOOTSTRAP_UNAVAILABLE")
        boot[key] = body
    try:
        server = raw_json(boot["server_time"])
        if not isinstance(server, dict):
            raise FeasibilityError("server time shape is invalid")
        server_at = utc_from_ms(server["serverTime"])
        recent = server_at.replace(hour=0, minute=0, second=0, microsecond=0)
        considered, probes = select_probes(boot["exchange_info"], boot["api_trading_symbols"])
    except (FeasibilityError, KeyError, ValueError, TypeError):
        return finalize(capture, source_hash, None, [], [], "BOOTSTRAP_SCHEMA_UNSUPPORTED")
    plan: dict[str, object] = {
        "protocol": "RESEARCH_V2_UNIVERSE_FEASIBILITY_V1",
        "source_sha256": source_hash,
        "code_revision": source_revision(),
        "H": iso(H),
        "R": iso(recent),
        "catalog_sha256": {key: digest(value) for key, value in boot.items()},
        "considered": considered,
        "probe_identifiers": probes,
        "daily_windows": [[iso(H - 30 * DAY), iso(H)], [iso(recent - 30 * DAY), iso(recent)]],
        "anchor_5m_windows": [[iso(H - DAY), iso(H)], [iso(recent - DAY), iso(recent)]],
        "row_bounds": {"daily": 30, "anchor_5m": 288, "page_limit": 100},
        "market_request_budget": capture.budget,
        "label": "PROBE_ONLY_NOT_RESEARCH_UNIVERSE",
    }
    write_new(root / "plan.json", canonical(plan))
    results: list[dict[str, object]] = []
    for symbol in probes:
        for tag, boundary in (("historical", H), ("recent", recent)):
            results.append(
                capture_history(capture, symbol, "1d", boundary - 30 * DAY, boundary, tag)
            )
    for symbol in ("BTCUSDT", "ETHUSDT"):
        if symbol not in probes:
            continue
        for tag, boundary in (("historical", H), ("recent", recent)):
            results.append(capture_history(capture, symbol, "5m", boundary - DAY, boundary, tag))
    reconciliations: list[dict[str, object]] = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for tag, boundary in (("historical", H), ("recent", recent)):
            daily = next(
                (
                    item
                    for item in results
                    if item["symbol"] == symbol
                    and item["interval"] == "1d"
                    and item["end"] == iso(boundary)
                ),
                None,
            )
            five = next(
                (
                    item
                    for item in results
                    if item["symbol"] == symbol
                    and item["interval"] == "5m"
                    and item["end"] == iso(boundary)
                ),
                None,
            )
            row: dict[str, object] = {
                "symbol": symbol,
                "window": tag,
                "day_open": iso(boundary - DAY),
                "status": "UNAVAILABLE",
                "daily_quote": None,
                "five_minute_quote": None,
                "delta": None,
            }
            if daily is not None and five is not None and daily["complete"] and five["complete"]:
                opens = daily["open_times"]
                values = daily["quote_volumes"]
                assert isinstance(opens, list) and isinstance(values, list)
                index = opens.index(ms(boundary - DAY))
                daily_quote = Decimal(values[index])
                five_values = five["quote_volumes"]
                assert isinstance(five_values, list)
                five_quote = sum((Decimal(value) for value in five_values), Decimal(0))
                delta = five_quote - daily_quote
                row.update(
                    status="EXACT_MATCH" if delta == 0 else "MISMATCH",
                    daily_quote=str(daily_quote),
                    five_minute_quote=str(five_quote),
                    delta=str(delta),
                )
            reconciliations.append(row)
    return finalize(capture, source_hash, plan, results, considered, "CAPTURED", reconciliations)


def finalize(
    capture: Capture,
    source_hash: str,
    plan: dict[str, object] | None,
    results: list[dict[str, object]],
    considered: list[dict[str, str]],
    status: str,
    reconciliations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "status": status,
        "request_count": len(capture.attempts),
        "plan_sha256": digest(canonical(plan)) if plan is not None else None,
        "current_catalog_considered_count": len(considered),
        "results": results,
        "reconciliations": reconciliations or [],
        "historical_as_of_population": "NOT_ESTABLISHED_BY_CAPTURE",
        "final_panel": "NOT_SELECTED",
        "study": "NOT_AUTHORIZED",
    }
    write_new(capture.root / "summary.json", canonical(summary))
    manifest = {
        "schema": "research-v2-universe-feasibility-v1",
        "source_sha256": source_hash,
        "request_count": len(capture.attempts),
        "attempts": capture.attempts,
        "plan_sha256": summary["plan_sha256"],
        "summary_sha256": digest(canonical(summary)),
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
            for path in sorted(capture.root.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
    }
    write_new(capture.root / "manifest.json", canonical(manifest))
    return summary


def original_analysis(root: Path, attempts: list[dict[str, object]]) -> dict[str, object]:
    """Re-derive the frozen first-generation facts from raw bytes, without I/O."""

    if len(attempts) == 1 and attempts[0].get("status") != "HTTP_OK":
        return {
            "status": "BOOTSTRAP_UNAVAILABLE",
            "request_count": 1,
            "plan_sha256": None,
            "current_catalog_considered_count": 0,
            "results": [],
            "reconciliations": [],
            "historical_as_of_population": "NOT_ESTABLISHED_BY_CAPTURE",
            "final_panel": "NOT_SELECTED",
            "study": "NOT_AUTHORIZED",
        }
    if len(attempts) != 27:
        raise FeasibilityError("original capture attempt population differs")

    def saved(index: int, name: str, path: str, query: dict[str, str]) -> bytes:
        attempt = attempts[index]
        if (
            attempt.get("name") != name
            or attempt.get("path") != path
            or attempt.get("query") != query
            or attempt.get("status") != "HTTP_OK"
            or attempt.get("retry") != 0
        ):
            raise FeasibilityError("attempt differs from frozen capture plan")
        filename = attempt.get("file")
        if not isinstance(filename, str):
            raise FeasibilityError("raw response file is missing")
        return (root / filename).read_bytes()

    boot = {
        key: saved(index, key, path, {})
        for index, (key, path) in enumerate(
            (
                ("server_time", "/capi/v3/market/time"),
                ("exchange_info", "/capi/v3/market/exchangeInfo"),
                ("api_trading_symbols", "/capi/v3/market/apiTradingSymbols"),
            )
        )
    }
    server = raw_json(boot["server_time"])
    if not isinstance(server, dict):
        raise FeasibilityError("saved server-time response is malformed")
    recent = utc_from_ms(server["serverTime"]).replace(hour=0, minute=0, second=0, microsecond=0)
    considered, probes = select_probes(boot["exchange_info"], boot["api_trading_symbols"])
    plan_bytes = (root / "plan.json").read_bytes()
    plan = raw_json(plan_bytes)
    if not isinstance(plan, dict) or any(
        (
            plan.get("protocol") != "RESEARCH_V2_UNIVERSE_FEASIBILITY_V1",
            plan.get("H") != iso(H),
            plan.get("R") != iso(recent),
            plan.get("catalog_sha256") != {key: digest(value) for key, value in boot.items()},
            plan.get("considered") != considered,
            plan.get("probe_identifiers") != probes,
            plan.get("daily_windows")
            != [[iso(H - 30 * DAY), iso(H)], [iso(recent - 30 * DAY), iso(recent)]],
            plan.get("anchor_5m_windows")
            != [[iso(H - DAY), iso(H)], [iso(recent - DAY), iso(recent)]],
            plan.get("row_bounds") != {"daily": 30, "anchor_5m": 288, "page_limit": 100},
            plan.get("market_request_budget") != BUDGET,
            plan.get("label") != "PROBE_ONLY_NOT_RESEARCH_UNIVERSE",
        )
    ):
        raise FeasibilityError("frozen request plan contradicts raw bootstrap evidence")

    results: list[dict[str, object]] = []
    index = 3
    for symbol in probes:
        for tag, boundary in (("historical", H), ("recent", recent)):
            start = boundary - 30 * DAY
            name = f"{tag}-{symbol}-1d-1"
            body = saved(
                index,
                name,
                "/capi/v3/market/historyKlines",
                history_query(symbol, "1d", start, boundary),
            )
            results.append(
                result_from_rows(
                    symbol, "1d", start, boundary, candle_rows(body, start, boundary, "1d"), 1
                )
            )
            index += 1
    for symbol in ("BTCUSDT", "ETHUSDT"):
        if symbol not in probes:
            continue
        for tag, boundary in (("historical", H), ("recent", recent)):
            start = boundary - DAY
            body = saved(
                index,
                f"{tag}-{symbol}-5m-1",
                "/capi/v3/market/historyKlines",
                history_query(symbol, "5m", start, boundary),
            )
            results.append(
                result_from_rows(
                    symbol, "5m", start, boundary, candle_rows(body, start, boundary, "5m"), 1
                )
            )
            index += 1
    if index != len(attempts):
        raise FeasibilityError("original capture contains unplanned attempts")
    return {
        "status": "CAPTURED",
        "request_count": len(attempts),
        "plan_sha256": digest(plan_bytes),
        "current_catalog_considered_count": len(considered),
        "results": results,
        "reconciliations": reconcile_results(results, H, recent),
        "historical_as_of_population": "NOT_ESTABLISHED_BY_CAPTURE",
        "final_panel": "NOT_SELECTED",
        "study": "NOT_AUTHORIZED",
    }


def reconcile_results(
    results: list[dict[str, object]], historical: datetime, recent: datetime
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for tag, boundary in (("historical", historical), ("recent", recent)):
            daily = next(
                (
                    x
                    for x in results
                    if x["symbol"] == symbol and x["interval"] == "1d" and x["end"] == iso(boundary)
                ),
                None,
            )
            five = next(
                (
                    x
                    for x in results
                    if x["symbol"] == symbol and x["interval"] == "5m" and x["end"] == iso(boundary)
                ),
                None,
            )
            row: dict[str, object] = {
                "symbol": symbol,
                "window": tag,
                "day_open": iso(boundary - DAY),
                "status": "UNAVAILABLE",
                "daily_quote": None,
                "five_minute_quote": None,
                "delta": None,
            }
            if daily is not None and five is not None and daily["complete"] and five["complete"]:
                opens, values, five_values = (
                    daily["open_times"],
                    daily["quote_volumes"],
                    five["quote_volumes"],
                )
                assert isinstance(opens, list) and isinstance(values, list)
                assert isinstance(five_values, list)
                with localcontext(DECIMAL_CONTEXT):
                    daily_quote = Decimal(values[opens.index(ms(boundary - DAY))])
                    five_quote = sum((Decimal(value) for value in five_values), Decimal(0))
                    delta = five_quote - daily_quote
                row.update(
                    status="EXACT_MATCH" if delta == 0 else "MISMATCH",
                    daily_quote=str(daily_quote),
                    five_minute_quote=str(five_quote),
                    delta=str(delta),
                )
            output.append(row)
    return output


def verify_bundle(root: Path) -> dict[str, object]:
    manifest = raw_json((root / "manifest.json").read_bytes())
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "research-v2-universe-feasibility-v1"
    ):
        raise FeasibilityError("manifest schema is invalid")
    if manifest.get("source_sha256") not in {
        ORIGINAL_SOURCE_SHA256,
        digest(Path(__file__).resolve().read_bytes()),
    }:
        raise FeasibilityError("capture utility source digest differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {
        p.name for p in root.iterdir() if p.is_file() and p.name != "manifest.json"
    }:
        raise FeasibilityError("manifest file set differs")
    for name, meta in files.items():
        if not isinstance(name, str) or not isinstance(meta, dict):
            raise FeasibilityError("manifest file entry is invalid")
        if Path(name).name != name or name == "manifest.json":
            raise FeasibilityError("unsafe manifest file path")
        body = (root / name).read_bytes()
        if len(body) != meta.get("bytes") or digest(body) != meta.get("sha256"):
            raise FeasibilityError("evidence file hash/length differs")
    summary = raw_json((root / "summary.json").read_bytes())
    if not isinstance(summary, dict) or digest(
        (root / "summary.json").read_bytes()
    ) != manifest.get("summary_sha256"):
        raise FeasibilityError("summary hash differs")
    attempts = raw_json((root / "attempts.json").read_bytes())
    if not isinstance(attempts, list) or len(attempts) != manifest.get("request_count"):
        raise FeasibilityError("attempt count differs")
    if summary.get("request_count") != len(attempts) or len(attempts) > BUDGET:
        raise FeasibilityError("summary request count or budget differs")
    if (root / "plan.json").exists():
        plan = (root / "plan.json").read_bytes()
        if digest(plan) != manifest.get("plan_sha256"):
            raise FeasibilityError("plan hash differs")
        parsed_plan = raw_json(plan)
        if not isinstance(parsed_plan, dict) or parsed_plan.get("source_sha256") != manifest.get(
            "source_sha256"
        ):
            raise FeasibilityError("plan source identity differs")
    elif manifest.get("plan_sha256") is not None:
        raise FeasibilityError("plan is missing")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise FeasibilityError("attempt entry is invalid")
        path, query = attempt.get("path"), attempt.get("query")
        if (
            not isinstance(path, str)
            or not isinstance(query, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in query.items())
        ):
            raise FeasibilityError("attempt route is invalid")
        method = attempt.get("method")
        if not isinstance(method, str):
            raise FeasibilityError("attempt method is invalid")
        validate_route(method, path, query)
        name = attempt.get("file")
        if name is not None:
            if not isinstance(name, str) or name not in files:
                raise FeasibilityError("attempt raw file is absent")
            raw = (root / name).read_bytes()
            if digest(raw) != attempt.get("sha256") or len(raw) != attempt.get("byte_length"):
                raise FeasibilityError("attempt body digest differs")
    derived = original_analysis(root, attempts)
    if canonical(derived) != canonical(summary):
        raise FeasibilityError("saved derived summary contradicts raw responses")
    return summary


def supplement_request_plan(original: Path) -> dict[str, object]:
    if digest((original / "manifest.json").read_bytes()) != ORIGINAL_MANIFEST_SHA256:
        raise FeasibilityError("original manifest identity differs")
    if digest((original / "plan.json").read_bytes()) != ORIGINAL_PLAN_SHA256:
        raise FeasibilityError("original plan identity differs")
    original_manifest = raw_json((original / "manifest.json").read_bytes())
    if (
        not isinstance(original_manifest, dict)
        or original_manifest.get("source_sha256") != ORIGINAL_SOURCE_SHA256
    ):
        raise FeasibilityError("original source identity differs")
    verify_bundle(original)
    attempts = raw_json((original / "attempts.json").read_bytes())
    if not isinstance(attempts, list):
        raise FeasibilityError("original attempts are malformed")
    requests: list[dict[str, object]] = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for tag, boundary in (("historical", H), ("recent", datetime(2026, 10, 14, tzinfo=UTC))):
            day_start = boundary - DAY
            name = f"{tag}-{symbol}-5m-1"
            matched = [
                attempt
                for attempt in attempts
                if isinstance(attempt, dict) and attempt.get("name") == name
            ]
            if len(matched) != 1 or not isinstance(matched[0].get("file"), str):
                raise FeasibilityError("original suffix attempt differs")
            suffix = candle_rows(
                (original / str(matched[0]["file"])).read_bytes(), day_start, boundary, "5m"
            )
            expected_suffix = [ms(day_start + i * FIVE) for i in range(188, 288)]
            if [opened for opened, _ in suffix] != expected_suffix:
                raise FeasibilityError("original saved suffix is not the expected 100 rows")
            for page, upper, expected_count in (
                (1, day_start + 188 * FIVE, 100),
                (2, day_start + 88 * FIVE, 88),
            ):
                requests.append(
                    {
                        "name": f"prefix-{tag}-{symbol}-5m-{page}",
                        "symbol": symbol,
                        "window": tag,
                        "day_start": iso(day_start),
                        "day_end": iso(boundary),
                        "expected_count": expected_count,
                        "expected_first_open": ms(day_start + (88 if page == 1 else 0) * FIVE),
                        "expected_last_open": ms(upper - FIVE),
                        "path": "/capi/v3/market/historyKlines",
                        "query": history_query(symbol, "5m", day_start, upper),
                        "original_suffix_file": matched[0]["file"],
                        "original_suffix_sha256": matched[0]["sha256"],
                    }
                )
    return {
        "schema": SUPPLEMENT_SCHEMA,
        "source_sha256": digest(Path(__file__).resolve().read_bytes()),
        "code_revision": source_revision(),
        "original_source_sha256": ORIGINAL_SOURCE_SHA256,
        "original_plan_sha256": ORIGINAL_PLAN_SHA256,
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
        "original_request_count": 27,
        "new_distinct_page_limit": 8,
        "new_attempt_limit": 16,
        "cumulative_attempt_limit": 43,
        "requests": requests,
    }


def supplement_analysis(original: Path, supplement: Path) -> dict[str, object]:
    """Pure raw-to-derived projection across the pinned first and second generations."""

    plan = raw_json((supplement / "plan.json").read_bytes())
    attempts = raw_json((supplement / "attempts.json").read_bytes())
    if not isinstance(plan, dict) or not isinstance(attempts, list):
        raise FeasibilityError("supplement plan or attempts are malformed")
    expected_plan = supplement_request_plan(original)
    # The source revision/hash were frozen before transmission, not reinterpreted
    # from the current checkout while reviewing an older artifact.
    expected_plan["source_sha256"] = plan.get("source_sha256")
    expected_plan["code_revision"] = plan.get("code_revision")
    if canonical(plan) != canonical(expected_plan):
        raise FeasibilityError("supplement request plan contradicts pinned originals")
    if len(attempts) > 16 or len(attempts) + 27 > 43:
        raise FeasibilityError("supplement attempt budget exceeded")
    requests = plan["requests"]
    if not isinstance(requests, list):
        raise FeasibilityError("supplement request list is malformed")
    by_name: dict[str, list[dict[str, object]]] = {}
    last_order = -1
    for attempt in attempts:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("name"), str):
            raise FeasibilityError("supplement attempt is malformed")
        matching = [
            i
            for i, request in enumerate(requests)
            if isinstance(request, dict) and request.get("name") == attempt["name"]
        ]
        if len(matching) != 1 or matching[0] < last_order:
            raise FeasibilityError("supplement attempt order is unplanned")
        last_order = matching[0]
        request = requests[last_order]
        if not isinstance(request, dict) or any(
            (
                attempt.get("method") != "GET",
                attempt.get("path") != request["path"],
                attempt.get("query") != request["query"],
            )
        ):
            raise FeasibilityError("supplement attempt route differs from plan")
        by_name.setdefault(str(attempt["name"]), []).append(attempt)
        if (
            len(by_name[str(attempt["name"])]) > 2
            or attempt.get("retry") != len(by_name[str(attempt["name"])]) - 1
        ):
            raise FeasibilityError("supplement retry count differs")
        same_request = by_name[str(attempt["name"])]
        if len(same_request) == 2 and not (
            same_request[0].get("status") == "TRANSPORT_UNAVAILABLE"
            or (
                isinstance(same_request[0].get("http_status"), int)
                and int(str(same_request[0]["http_status"])) >= 500
            )
        ):
            raise FeasibilityError("supplement retry was not transient")
    original_summary = verify_bundle(original)
    original_results = original_summary["results"]
    assert isinstance(original_results, list)
    original_attempts = raw_json((original / "attempts.json").read_bytes())
    assert isinstance(original_attempts, list)
    anchors: list[dict[str, object]] = []
    combined_results = [dict(item) for item in original_results if isinstance(item, dict)]
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for tag, boundary in (("historical", H), ("recent", datetime(2026, 10, 14, tzinfo=UTC))):
            start = boundary - DAY
            prefix: list[tuple[int, Decimal]] = []
            page_files: list[str] = []
            reason = "COMPLETE"
            for page in (1, 2):
                name = f"prefix-{tag}-{symbol}-5m-{page}"
                request = next(x for x in requests if isinstance(x, dict) and x["name"] == name)
                assert isinstance(request, dict)
                page_attempts = by_name.get(name, [])
                if not page_attempts or page_attempts[-1].get("status") != "HTTP_OK":
                    reason = "UNAVAILABLE_OR_NOT_ATTEMPTED"
                    break
                filename = page_attempts[-1].get("file")
                if not isinstance(filename, str):
                    raise FeasibilityError("successful supplement page has no raw file")
                page_files.append(filename)
                upper = utc_from_ms(int(str(request["query"]["endTime"])) + 1)
                try:
                    rows = candle_rows((supplement / filename).read_bytes(), start, upper, "5m")
                except (FeasibilityError, ValueError, TypeError):
                    reason = "UNSUPPORTED_OR_INVALID_HISTORY_SCHEMA"
                    break
                if (
                    not rows
                    or len(rows) != request["expected_count"]
                    or rows[0][0] != request["expected_first_open"]
                    or rows[-1][0] != request["expected_last_open"]
                    or not complete(rows, utc_from_ms(rows[0][0]), upper, "5m")
                ):
                    reason = "INCOMPLETE_OR_NON_ADVANCING_PREFIX"
                    break
                prefix.extend(rows)
            suffix_name = f"{tag}-{symbol}-5m-1"
            suffix_attempt = next(
                x for x in original_attempts if isinstance(x, dict) and x.get("name") == suffix_name
            )
            assert isinstance(suffix_attempt, dict) and isinstance(suffix_attempt.get("file"), str)
            suffix = candle_rows(
                (original / str(suffix_attempt["file"])).read_bytes(), start, boundary, "5m"
            )
            rows = sorted(prefix + suffix)
            full = reason == "COMPLETE" and complete(rows, start, boundary, "5m")
            if not full and reason == "COMPLETE":
                reason = "INCOMPLETE_GRID"
            combined_results = [
                item
                for item in combined_results
                if not (
                    item["symbol"] == symbol
                    and item["interval"] == "5m"
                    and item["end"] == iso(boundary)
                )
            ]
            combined_results.append(
                result_from_rows(
                    symbol,
                    "5m",
                    start,
                    boundary,
                    rows,
                    len(page_files) + 1,
                    None if full else reason,
                )
            )
            anchors.append(
                {
                    "symbol": symbol,
                    "window": tag,
                    "status": "COMPLETE" if full else reason,
                    "rows": len(rows),
                    "original_suffix_file": suffix_attempt["file"],
                    "original_suffix_sha256": suffix_attempt["sha256"],
                    "supplement_page_files": page_files,
                    "original_capture_at": suffix_attempt["captured_at"],
                    "supplement_capture_at": [
                        by_name[f"prefix-{tag}-{symbol}-5m-{p}"][-1]["captured_at"]
                        for p in (1, 2)
                        if by_name.get(f"prefix-{tag}-{symbol}-5m-{p}")
                    ],
                }
            )
    recent = datetime(2026, 10, 14, tzinfo=UTC)
    return {
        "schema": SUPPLEMENT_SCHEMA,
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
        "supplement_plan_sha256": digest((supplement / "plan.json").read_bytes()),
        "original_request_count": 27,
        "supplement_request_count": len(attempts),
        "cumulative_request_count": 27 + len(attempts),
        "historical_as_of_population": "NOT_ESTABLISHED_BY_INSPECTED_SOURCES",
        "identity_units": "CURRENT_LABELS_ONLY_HISTORICAL_SPECIFICATIONS_UNRESOLVED",
        "final_panel": "NOT_SELECTED",
        "study": "NOT_AUTHORIZED",
        "anchors": anchors,
        "reconciliations": reconcile_results(combined_results, H, recent),
    }


def capture_supplement(
    original: Path, root: Path, transport: Transport | None = None
) -> dict[str, object]:
    plan = supplement_request_plan(original)
    capture = Capture(root, transport or PublicGetTransport(), budget=16)
    write_new(root / "plan.json", canonical(plan))
    requests = plan["requests"]
    assert isinstance(requests, list)
    for request in requests:
        assert isinstance(request, dict)
        query = request["query"]
        assert isinstance(query, dict)
        try:
            body = capture.get(str(request["path"]), query, str(request["name"]))
        except FeasibilityError:
            break
        if body is None:
            break
        upper = utc_from_ms(int(query["endTime"]) + 1)
        start = utc_from_ms(int(query["startTime"]))
        try:
            rows = candle_rows(body, start, upper, "5m")
        except (FeasibilityError, ValueError, TypeError):
            break
        if (
            not rows
            or len(rows) != request["expected_count"]
            or rows[0][0] != request["expected_first_open"]
            or rows[-1][0] != request["expected_last_open"]
            or not complete(rows, utc_from_ms(rows[0][0]), upper, "5m")
        ):
            break
    if not (root / "attempts.json").exists():
        write_new(root / "attempts.json", canonical([]))
    analysis = supplement_analysis(original, root)
    write_new(root / "analysis.json", canonical(analysis))
    files = {
        path.name: {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "schema": SUPPLEMENT_SCHEMA,
        "source_sha256": plan["source_sha256"],
        "code_revision": plan["code_revision"],
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
        "plan_sha256": digest((root / "plan.json").read_bytes()),
        "analysis_sha256": digest((root / "analysis.json").read_bytes()),
        "request_count": len(capture.attempts),
        "files": files,
    }
    write_new(root / "manifest.json", canonical(manifest))
    return analysis


def verify_supplement(original: Path, root: Path) -> dict[str, object]:
    manifest = raw_json((root / "manifest.json").read_bytes())
    if not isinstance(manifest, dict) or manifest.get("schema") != SUPPLEMENT_SCHEMA:
        raise FeasibilityError("supplement manifest schema differs")
    plan_bytes = (root / "plan.json").read_bytes()
    plan = raw_json(plan_bytes)
    if not isinstance(plan, dict) or any(
        (
            manifest.get("source_sha256") != digest(Path(__file__).resolve().read_bytes()),
            plan.get("source_sha256") != manifest.get("source_sha256"),
            manifest.get("code_revision") != plan.get("code_revision"),
            manifest.get("original_manifest_sha256") != ORIGINAL_MANIFEST_SHA256,
            manifest.get("plan_sha256") != digest(plan_bytes),
        )
    ):
        raise FeasibilityError("supplement source/plan lineage differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {
        path.name for path in root.iterdir() if path.is_file() and path.name != "manifest.json"
    }:
        raise FeasibilityError("supplement file population differs")
    for filename, meta in files.items():
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not isinstance(meta, dict)
        ):
            raise FeasibilityError("supplement file name/metadata is unsafe")
        body = (root / filename).read_bytes()
        if meta.get("bytes") != len(body) or meta.get("sha256") != digest(body):
            raise FeasibilityError("supplement raw/hash/length differs")
    attempts = raw_json((root / "attempts.json").read_bytes())
    if not isinstance(attempts, list) or manifest.get("request_count") != len(attempts):
        raise FeasibilityError("supplement attempt count differs")
    for attempt in attempts:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("query"), dict):
            raise FeasibilityError("supplement attempt route is malformed")
        validate_route(str(attempt.get("method")), str(attempt.get("path")), attempt["query"])
        filename = attempt.get("file")
        if filename is not None:
            if not isinstance(filename, str) or filename not in files:
                raise FeasibilityError("supplement raw file differs")
            raw = (root / filename).read_bytes()
            if attempt.get("sha256") != digest(raw) or attempt.get("byte_length") != len(raw):
                raise FeasibilityError("supplement attempt raw digest differs")
    analysis = supplement_analysis(original, root)
    body = (root / "analysis.json").read_bytes()
    if digest(body) != manifest.get("analysis_sha256") or canonical(analysis) != body:
        raise FeasibilityError("supplement derived analysis contradicts raw evidence")
    return analysis


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_subparsers(dest="action", required=True)
    for command in ("capture", "verify"):
        action.add_parser(command).add_argument("--bundle", required=True, type=Path)
    for command in ("supplement", "verify-supplement"):
        supplement_parser = action.add_parser(command)
        supplement_parser.add_argument("--original", required=True, type=Path)
        supplement_parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "capture":
            result = capture_bundle(args.bundle)
        elif args.action == "verify":
            result = verify_bundle(args.bundle)
        elif args.action == "supplement":
            result = capture_supplement(args.original, args.bundle)
        else:
            result = verify_supplement(args.original, args.bundle)
    except (FeasibilityError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "reason": type(exc).__name__}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": result.get("status", "SUPPLEMENT_ANALYZED"),
                "request_count": result.get(
                    "request_count", result.get("cumulative_request_count")
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
