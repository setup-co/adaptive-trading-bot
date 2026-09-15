from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ENDPOINT = "https://api-contract.weex.com/capi/v3/market/historyKlines"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
PRICE_TYPE = "LAST"
OUTPUT_DIR = Path(".audit/issue-111")
MAX_ATTEMPTS = 4
PAGE_LIMIT = 100
STEPS_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}
SAMPLE_DAYS = (
    datetime(2025, 5, 1, tzinfo=UTC),
    datetime(2025, 6, 1, tzinfo=UTC),
    datetime(2025, 7, 1, tzinfo=UTC),
    datetime(2025, 8, 1, tzinfo=UTC),
    datetime(2025, 9, 1, tzinfo=UTC),
    datetime(2025, 10, 1, tzinfo=UTC),
    datetime(2025, 11, 1, tzinfo=UTC),
    datetime(2025, 12, 1, tzinfo=UTC),
    datetime(2026, 1, 1, tzinfo=UTC),
)


@dataclass(frozen=True)
class Row:
    open_time: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    base_volume: Decimal
    close_time: int
    quote_volume: Decimal
    trade_count: Decimal
    taker_buy_base: Decimal
    taker_buy_quote: Decimal


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def decimal_value(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation(f"invalid decimal scalar: {value!r}")
    text = str(value)
    if not text.strip():
        raise InvalidOperation("blank decimal scalar")
    parsed = Decimal(text)
    if not parsed.is_finite():
        raise InvalidOperation(f"non-finite decimal scalar: {value!r}")
    return parsed


def integer_value(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid integer scalar: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value)
    if not text.strip():
        raise ValueError("blank integer scalar")
    return int(text)


def parse_row(raw: Any) -> Row:
    if not isinstance(raw, list) or len(raw) != 11:
        raise ValueError(f"expected 11-field row, got {raw!r}")
    return Row(
        open_time=integer_value(raw[0]),
        open_price=decimal_value(raw[1]),
        high_price=decimal_value(raw[2]),
        low_price=decimal_value(raw[3]),
        close_price=decimal_value(raw[4]),
        base_volume=decimal_value(raw[5]),
        close_time=integer_value(raw[6]),
        quote_volume=decimal_value(raw[7]),
        trade_count=decimal_value(raw[8]),
        taker_buy_base=decimal_value(raw[9]),
        taker_buy_quote=decimal_value(raw[10]),
    )


def fetch_page(params: dict[str, Any]) -> list[Any]:
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "adaptive-trading-bot-research-audit/1.0"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected response shape: {type(payload).__name__}")
            return payload
        except urllib.error.HTTPError as exc:
            if not (exc.code == 429 or 500 <= exc.code <= 599) or attempt == MAX_ATTEMPTS:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == MAX_ATTEMPTS:
                raise
        time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def fetch_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[int, Row], dict[int, dict[str, Any]], int]:
    step_ms = STEPS_MS[interval]
    rows: dict[int, Row] = {}
    malformed: dict[int, dict[str, Any]] = {}
    request_count = 0
    page_end = end_ms - step_ms
    while page_end >= start_ms:
        page_start = max(start_ms, page_end - (PAGE_LIMIT - 1) * step_ms)
        limit = ((page_end - page_start) // step_ms) + 1
        raw_rows = fetch_page(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": page_start,
                "endTime": page_end,
                "limit": limit,
                "priceType": PRICE_TYPE,
            }
        )
        request_count += 1
        for raw in raw_rows:
            try:
                raw_open = integer_value(raw[0]) if isinstance(raw, list) and raw else None
            except (TypeError, ValueError):
                raw_open = None
            if raw_open is None or not (page_start <= raw_open <= page_end):
                continue
            try:
                parsed = parse_row(raw)
            except (InvalidOperation, TypeError, ValueError) as exc:
                malformed[raw_open] = {
                    "reason": f"{type(exc).__name__}: {exc}",
                    "raw": raw,
                }
                continue
            rows.setdefault(parsed.open_time, parsed)
        page_end = page_start - step_ms
    return rows, malformed, request_count


def aggregate(source: dict[int, Row], target_interval: str, day_start_ms: int) -> dict[int, Row]:
    target_step = STEPS_MS[target_interval]
    source_step = STEPS_MS["5m"]
    per_bucket = target_step // source_step
    day_end_ms = day_start_ms + 24 * 60 * 60 * 1000
    derived: dict[int, Row] = {}
    for bucket_start in range(day_start_ms, day_end_ms, target_step):
        opens = [bucket_start + index * source_step for index in range(per_bucket)]
        if any(open_time not in source for open_time in opens):
            continue
        members = [source[open_time] for open_time in opens]
        derived[bucket_start] = Row(
            open_time=bucket_start,
            open_price=members[0].open_price,
            high_price=max(row.high_price for row in members),
            low_price=min(row.low_price for row in members),
            close_price=members[-1].close_price,
            base_volume=sum((row.base_volume for row in members), Decimal(0)),
            close_time=bucket_start + target_step,
            quote_volume=sum((row.quote_volume for row in members), Decimal(0)),
            trade_count=sum((row.trade_count for row in members), Decimal(0)),
            taker_buy_base=sum((row.taker_buy_base for row in members), Decimal(0)),
            taker_buy_quote=sum((row.taker_buy_quote for row in members), Decimal(0)),
        )
    return derived


def ohlc(row: Row) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return (row.open_price, row.high_price, row.low_price, row.close_price)


def additive(row: Row) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    return (
        row.base_volume,
        row.quote_volume,
        row.trade_count,
        row.taker_buy_base,
        row.taker_buy_quote,
    )


def compare_day(symbol: str, day: datetime) -> dict[str, Any]:
    day_start_ms = int(day.timestamp() * 1000)
    day_end_ms = int((day + timedelta(days=1)).timestamp() * 1000)
    source, source_malformed, source_requests = fetch_range(
        symbol, "5m", day_start_ms, day_end_ms
    )
    expected_source = set(range(day_start_ms, day_end_ms, STEPS_MS["5m"]))
    source_missing = sorted(expected_source.difference(source))
    result: dict[str, Any] = {
        "day": day.date().isoformat(),
        "source_5m": {
            "expected": len(expected_source),
            "valid": len(source),
            "missing": [iso_ms(value) for value in source_missing],
            "malformed": {
                iso_ms(key): value for key, value in sorted(source_malformed.items())
            },
            "requests": source_requests,
        },
        "intervals": {},
    }

    for interval in ("15m", "1h", "4h"):
        derived = aggregate(source, interval, day_start_ms)
        native, native_malformed, native_requests = fetch_range(
            symbol, interval, day_start_ms, day_end_ms
        )
        target_step = STEPS_MS[interval]
        expected_opens = list(range(day_start_ms, day_end_ms, target_step))
        counters = {
            "expected": len(expected_opens),
            "derived_unavailable": 0,
            "native_missing_with_complete_derived": 0,
            "native_malformed": 0,
            "ohlc_exact": 0,
            "ohlc_mismatch": 0,
            "additive_exact": 0,
            "additive_mismatch": 0,
            "native_requests": native_requests,
        }
        details = {
            "derived_unavailable": [],
            "native_missing_with_complete_derived": [],
            "native_malformed": [],
            "ohlc_mismatch": [],
            "additive_mismatch": [],
        }
        for open_time in expected_opens:
            derived_row = derived.get(open_time)
            if derived_row is None:
                counters["derived_unavailable"] += 1
                details["derived_unavailable"].append(iso_ms(open_time))
                continue
            if open_time in native_malformed:
                counters["native_malformed"] += 1
                details["native_malformed"].append(
                    {"open_time": iso_ms(open_time), **native_malformed[open_time]}
                )
                continue
            native_row = native.get(open_time)
            if native_row is None:
                counters["native_missing_with_complete_derived"] += 1
                details["native_missing_with_complete_derived"].append(iso_ms(open_time))
                continue
            if ohlc(derived_row) == ohlc(native_row):
                counters["ohlc_exact"] += 1
            else:
                counters["ohlc_mismatch"] += 1
                details["ohlc_mismatch"].append(iso_ms(open_time))
            if additive(derived_row) == additive(native_row):
                counters["additive_exact"] += 1
            else:
                counters["additive_mismatch"] += 1
                details["additive_mismatch"].append(iso_ms(open_time))
        result["intervals"][interval] = {"counts": counters, "details": details}
    return result


def summarize(symbol_results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for symbol, days in symbol_results.items():
        interval_summary: dict[str, dict[str, int]] = {}
        for interval in ("15m", "1h", "4h"):
            keys = (
                "expected",
                "derived_unavailable",
                "native_missing_with_complete_derived",
                "native_malformed",
                "ohlc_exact",
                "ohlc_mismatch",
                "additive_exact",
                "additive_mismatch",
                "native_requests",
            )
            interval_summary[interval] = {
                key: sum(day["intervals"][interval]["counts"][key] for day in days)
                for key in keys
            }
        summary[symbol] = interval_summary
    return summary


def main() -> None:
    started = datetime.now(UTC)
    symbol_results: dict[str, list[dict[str, Any]]] = {}
    for symbol in SYMBOLS:
        symbol_results[symbol] = []
        for day in SAMPLE_DAYS:
            print(f"Comparing {symbol} {day.date().isoformat()}", flush=True)
            symbol_results[symbol].append(compare_day(symbol, day))
    result = {
        "schema": "atb.weex_recent_htf_consistency.v1",
        "endpoint": ENDPOINT,
        "source_interval": "5m",
        "price_type": PRICE_TYPE,
        "sample_days": [day.date().isoformat() for day in SAMPLE_DAYS],
        "execution_started_at": started.isoformat(),
        "execution_finished_at": datetime.now(UTC).isoformat(),
        "status": "WEEX_RECENT_HTF_DIAGNOSTIC = COMPLETE",
        "summary": summarize(symbol_results),
        "symbols": symbol_results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "weex_recent_htf_diagnostic.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# WEEX recent HTF consistency summary",
        "",
        f"Status: `{result['status']}`",
        "",
        "| Symbol | Interval | Expected | Derived unavailable | Native missing | Native malformed | OHLC exact | OHLC mismatch | Additive exact | Additive mismatch |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    summary = result["summary"]
    for symbol in SYMBOLS:
        for interval in ("15m", "1h", "4h"):
            counts = summary[symbol][interval]
            lines.append(
                f"| {symbol} | {interval} | {counts['expected']} | "
                f"{counts['derived_unavailable']} | "
                f"{counts['native_missing_with_complete_derived']} | "
                f"{counts['native_malformed']} | {counts['ohlc_exact']} | "
                f"{counts['ohlc_mismatch']} | {counts['additive_exact']} | "
                f"{counts['additive_mismatch']} |"
            )
    (OUTPUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
