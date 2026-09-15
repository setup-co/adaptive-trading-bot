from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ENDPOINT = "https://api-contract.weex.com/capi/v3/market/historyKlines"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL = "5m"
PRICE_TYPE = "LAST"
STEP_MS = 5 * 60 * 1000
PAGE_LIMIT = 100
START = datetime(2025, 4, 2, tzinfo=UTC)
END = datetime(2026, 1, 8, tzinfo=UTC)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
OUTPUT_DIR = Path(".audit/issue-111")
MAX_ATTEMPTS = 4
REQUEST_PAUSE_SECONDS = 0.20


@dataclass(frozen=True)
class ParsedRow:
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

    def canonical_tuple(self) -> tuple[Any, ...]:
        return (
            self.open_time,
            str(self.open_price),
            str(self.high_price),
            str(self.low_price),
            str(self.close_price),
            str(self.base_volume),
            self.close_time,
            str(self.quote_volume),
            str(self.trade_count),
            str(self.taker_buy_base),
            str(self.taker_buy_quote),
        )


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidOperation(f"invalid decimal scalar: {value!r}")
    text = str(value)
    if not text.strip():
        raise InvalidOperation("blank decimal scalar")
    parsed = Decimal(text)
    if not parsed.is_finite():
        raise InvalidOperation(f"non-finite decimal scalar: {value!r}")
    return parsed


def parse_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid integer scalar: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value)
    if not text.strip():
        raise ValueError("blank integer scalar")
    return int(text)


def parse_row(raw: Any) -> ParsedRow:
    if not isinstance(raw, list) or len(raw) != 11:
        raise ValueError(f"expected 11-field row, got {raw!r}")
    return ParsedRow(
        open_time=parse_int(raw[0]),
        open_price=parse_decimal(raw[1]),
        high_price=parse_decimal(raw[2]),
        low_price=parse_decimal(raw[3]),
        close_price=parse_decimal(raw[4]),
        base_volume=parse_decimal(raw[5]),
        close_time=parse_int(raw[6]),
        quote_volume=parse_decimal(raw[7]),
        trade_count=parse_decimal(raw[8]),
        taker_buy_base=parse_decimal(raw[9]),
        taker_buy_quote=parse_decimal(raw[10]),
    )


def raw_open_time(raw: Any) -> int | None:
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return parse_int(raw[0])
    except (TypeError, ValueError):
        return None


def fetch_rows(params: dict[str, Any]) -> tuple[list[Any], int, list[str]]:
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    errors: list[str] = []
    retries = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "adaptive-trading-bot-research-audit/1.0"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status != 200:
                    raise RuntimeError(f"unexpected HTTP status {response.status}")
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected response shape: {type(payload).__name__}")
            return payload, retries, errors
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            errors.append(f"HTTP {exc.code}: {exc.reason}")
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
            http.client.RemoteDisconnected,
        ) as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt == MAX_ATTEMPTS:
                raise
        retries += 1
        time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def validate_symbol(symbol: str) -> dict[str, Any]:
    observed_at_ms = int(time.time() * 1000)
    rows_by_open: dict[int, ParsedRow] = {}
    duplicate_rows: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    malformed: list[dict[str, Any]] = []
    off_grid: set[int] = set()
    invalid_ohlc: set[int] = set()
    non_positive_price: set[int] = set()
    invalid_additive: set[int] = set()
    close_misalignment: set[int] = set()
    still_open: set[int] = set()
    out_of_page_rows = 0
    request_count = 0
    retry_count = 0
    recovered_after_error = 0
    error_breakdown: dict[str, int] = defaultdict(int)
    unrecovered: list[dict[str, Any]] = []

    page_end = END_MS - STEP_MS
    while page_end >= START_MS:
        page_start = max(START_MS, page_end - (PAGE_LIMIT - 1) * STEP_MS)
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": page_start,
            "endTime": page_end,
            "limit": ((page_end - page_start) // STEP_MS) + 1,
            "priceType": PRICE_TYPE,
        }
        request_count += 1
        try:
            raw_rows, retries, errors = fetch_rows(params)
        except Exception as exc:  # fail-closed audit evidence
            unrecovered.append(
                {
                    "page_start": iso_ms(page_start),
                    "page_end": iso_ms(page_end),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break

        retry_count += retries
        if retries:
            recovered_after_error += 1
        for error in errors:
            error_breakdown[error.split(":", 1)[0]] += 1

        for raw in raw_rows:
            candidate_open = raw_open_time(raw)
            if candidate_open is None:
                malformed.append({"open_time": None, "reason": "unparseable open time", "raw": raw})
                continue
            if candidate_open < page_start or candidate_open > page_end:
                out_of_page_rows += 1
                continue
            try:
                row = parse_row(raw)
            except (InvalidOperation, TypeError, ValueError) as exc:
                malformed.append(
                    {
                        "open_time": iso_ms(candidate_open),
                        "reason": f"{type(exc).__name__}: {exc}",
                        "raw": raw,
                    }
                )
                continue

            if not (START_MS <= row.open_time < END_MS):
                out_of_page_rows += 1
                continue
            if (row.open_time - START_MS) % STEP_MS != 0:
                off_grid.add(row.open_time)
            if row.close_time != row.open_time + STEP_MS:
                close_misalignment.add(row.open_time)
            if row.close_time > observed_at_ms:
                still_open.add(row.open_time)

            prices = (row.open_price, row.high_price, row.low_price, row.close_price)
            if any(price <= 0 for price in prices):
                non_positive_price.add(row.open_time)
            if not (
                row.high_price >= max(row.open_price, row.close_price)
                and row.low_price <= min(row.open_price, row.close_price)
                and row.high_price >= row.low_price
            ):
                invalid_ohlc.add(row.open_time)

            additive = (
                row.base_volume,
                row.quote_volume,
                row.trade_count,
                row.taker_buy_base,
                row.taker_buy_quote,
            )
            if any(value < 0 for value in additive):
                invalid_additive.add(row.open_time)

            existing = rows_by_open.get(row.open_time)
            if existing is None:
                rows_by_open[row.open_time] = row
            else:
                duplicate_rows[row.open_time].append(row.canonical_tuple())

        if request_count % 100 == 0:
            print(
                f"{symbol}: {request_count} pages; "
                f"{iso_ms(page_start)}..{iso_ms(page_end)}",
                flush=True,
            )
        page_end = page_start - STEP_MS
        time.sleep(REQUEST_PAUSE_SECONDS)

    expected = set(range(START_MS, END_MS, STEP_MS))
    missing = sorted(expected.difference(rows_by_open))
    duplicate_times = sorted(duplicate_rows)
    conflicting = sorted(
        open_time
        for open_time, candidates in duplicate_rows.items()
        if any(candidate != rows_by_open[open_time].canonical_tuple() for candidate in candidates)
    )

    first_open = min(rows_by_open) if rows_by_open else None
    last_open = max(rows_by_open) if rows_by_open else None
    return {
        "symbol": symbol,
        "expected_grid_points": (END_MS - START_MS) // STEP_MS,
        "received_in_range_unique_rows": len(rows_by_open),
        "missing_count": len(missing),
        "missing_timestamps": [iso_ms(value) for value in missing],
        "duplicate_open_time_count": len(duplicate_times),
        "duplicate_open_times": [iso_ms(value) for value in duplicate_times],
        "conflicting_duplicate_count": len(conflicting),
        "conflicting_duplicate_times": [iso_ms(value) for value in conflicting],
        "off_grid_count": len(off_grid),
        "off_grid_times": [iso_ms(value) for value in sorted(off_grid)],
        "malformed_row_count": len(malformed),
        "malformed_rows": malformed,
        "invalid_ohlc_count": len(invalid_ohlc),
        "invalid_ohlc_times": [iso_ms(value) for value in sorted(invalid_ohlc)],
        "non_positive_price_count": len(non_positive_price),
        "non_positive_price_times": [iso_ms(value) for value in sorted(non_positive_price)],
        "negative_or_invalid_additive_count": len(invalid_additive),
        "negative_or_invalid_additive_times": [
            iso_ms(value) for value in sorted(invalid_additive)
        ],
        "close_time_misalignment_count": len(close_misalignment),
        "close_time_misalignment_times": [
            iso_ms(value) for value in sorted(close_misalignment)
        ],
        "still_open_count": len(still_open),
        "still_open_times": [iso_ms(value) for value in sorted(still_open)],
        "first_valid_open": iso_ms(first_open) if first_open is not None else None,
        "last_valid_open": iso_ms(last_open) if last_open is not None else None,
        "request_count": request_count,
        "retry_count": retry_count,
        "requests_recovered_after_error": recovered_after_error,
        "attempt_error_breakdown": dict(sorted(error_breakdown.items())),
        "unrecovered_request_count": len(unrecovered),
        "unrecovered_requests": unrecovered,
        "out_of_page_rows_discarded": out_of_page_rows,
    }


def symbol_qualified(result: dict[str, Any]) -> bool:
    zero_keys = (
        "missing_count",
        "conflicting_duplicate_count",
        "off_grid_count",
        "malformed_row_count",
        "invalid_ohlc_count",
        "non_positive_price_count",
        "negative_or_invalid_additive_count",
        "close_time_misalignment_count",
        "still_open_count",
        "unrecovered_request_count",
    )
    return all(result[key] == 0 for key in zero_keys)


def write_outputs(result: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "weex_recent_5m_audit_result.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    by_symbol = {entry["symbol"]: entry for entry in result["symbols"]}
    lines = [
        "# WEEX recent 5m audit summary",
        "",
        f"Execution end: `{result['execution_finished_at']}`",
        f"Locked interval: `{result['locked_interval']}`",
        f"Final status: `{result['status']}`",
        "",
        "| Metric | BTCUSDT | ETHUSDT |",
        "| --- | ---: | ---: |",
    ]
    metrics = (
        ("Expected grid points", "expected_grid_points"),
        ("Unique rows", "received_in_range_unique_rows"),
        ("Missing", "missing_count"),
        ("Duplicate opens", "duplicate_open_time_count"),
        ("Conflicting duplicates", "conflicting_duplicate_count"),
        ("Malformed", "malformed_row_count"),
        ("Off-grid", "off_grid_count"),
        ("Invalid OHLC", "invalid_ohlc_count"),
        ("Non-positive prices", "non_positive_price_count"),
        ("Negative/invalid additive", "negative_or_invalid_additive_count"),
        ("Close misalignment", "close_time_misalignment_count"),
        ("Unrecovered requests", "unrecovered_request_count"),
        ("Retries", "retry_count"),
    )
    for label, key in metrics:
        lines.append(
            f"| {label} | {by_symbol['BTCUSDT'][key]} | {by_symbol['ETHUSDT'][key]} |"
        )
    lines.extend(["", "## Offending timestamps", ""])
    timestamp_keys = (
        "missing_timestamps",
        "duplicate_open_times",
        "conflicting_duplicate_times",
        "off_grid_times",
        "invalid_ohlc_times",
        "non_positive_price_times",
        "negative_or_invalid_additive_times",
        "close_time_misalignment_times",
    )
    for symbol in SYMBOLS:
        entry = by_symbol[symbol]
        lines.append(f"### {symbol}")
        for key in timestamp_keys:
            lines.append(f"- `{key}`: {json.dumps(entry[key], ensure_ascii=False)}")
        lines.append(
            f"- `malformed_rows`: {json.dumps(entry['malformed_rows'], ensure_ascii=False)}"
        )
        lines.append("")
    (OUTPUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    started = datetime.now(UTC)
    print(f"Auditing {SYMBOLS} over [{START.isoformat()}, {END.isoformat()})", flush=True)
    symbol_results = [validate_symbol(symbol) for symbol in SYMBOLS]
    qualified = all(symbol_qualified(entry) for entry in symbol_results)
    result = {
        "schema": "atb.weex_recent_5m_last_audit.v1",
        "endpoint": ENDPOINT,
        "interval": INTERVAL,
        "price_type": PRICE_TYPE,
        "locked_interval": f"[{START.isoformat()}, {END.isoformat()})",
        "execution_started_at": started.isoformat(),
        "execution_finished_at": datetime.now(UTC).isoformat(),
        "symbols": symbol_results,
        "status": (
            "WEEX_FUTURES_RECENT_5M_LAST = QUALIFIED"
            if qualified
            else "WEEX_FUTURES_RECENT_5M_LAST = NOT_QUALIFIED"
        ),
    }
    write_outputs(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
