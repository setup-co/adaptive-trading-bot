from __future__ import annotations

import hashlib
import http.client
import json
import os
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
STEP_MS = 5 * 60 * 1000
START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2025, 4, 2, tzinfo=UTC)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
PAGE_LIMIT = 100
MAX_ATTEMPTS = 4
REQUEST_PAUSE_SECONDS = 0.15
RUN_LABEL = os.environ.get("RUN_LABEL", "run")
OUTPUT_DIR = Path(f".audit/issue-113/{RUN_LABEL}")

KNOWN_PROBES = (
    ("BTCUSDT", "15m", "2024-01-01T02:45:00Z", 15),
    ("BTCUSDT", "15m", "2024-01-02T03:45:00Z", 15),
    ("BTCUSDT", "5m", "2024-06-26T18:40:00Z", 5),
    ("BTCUSDT", "5m", "2024-06-26T18:45:00Z", 5),
    ("ETHUSDT", "5m", "2025-03-04T11:20:00Z", 5),
    ("ETHUSDT", "5m", "2025-03-04T15:50:00Z", 5),
    ("ETHUSDT", "5m", "2025-03-04T15:55:00Z", 5),
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

    def canonical(self) -> tuple[Any, ...]:
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


def dt_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def dec(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise InvalidOperation(f"invalid decimal {value!r}")
    text = str(value)
    if not text.strip():
        raise InvalidOperation("blank decimal")
    parsed = Decimal(text)
    if not parsed.is_finite():
        raise InvalidOperation(f"non-finite decimal {value!r}")
    return parsed


def integer(value: Any) -> int:
    if value is None or isinstance(value, bool):
        raise ValueError(f"invalid integer {value!r}")
    return int(str(value))


def parse_row(raw: Any) -> Row:
    if not isinstance(raw, list) or len(raw) != 11:
        raise ValueError(f"expected 11 fields, got {raw!r}")
    return Row(
        open_time=integer(raw[0]),
        open_price=dec(raw[1]),
        high_price=dec(raw[2]),
        low_price=dec(raw[3]),
        close_price=dec(raw[4]),
        base_volume=dec(raw[5]),
        close_time=integer(raw[6]),
        quote_volume=dec(raw[7]),
        trade_count=dec(raw[8]),
        taker_buy_base=dec(raw[9]),
        taker_buy_quote=dec(raw[10]),
    )


def fetch(params: dict[str, Any]) -> tuple[list[Any], int, list[str]]:
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    errors: list[str] = []
    retries = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "adaptive-trading-bot-cloud-revalidation/1.0"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected response type {type(payload).__name__}")
            return payload, retries, errors
        except urllib.error.HTTPError as exc:
            errors.append(f"HTTP {exc.code}: {exc.reason}")
            if not (exc.code == 429 or 500 <= exc.code <= 599) or attempt == MAX_ATTEMPTS:
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


def targeted_probes() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for symbol, interval, target_text, minutes in KNOWN_PROBES:
        target = dt_ms(target_text)
        step = minutes * 60 * 1000
        start = target - 2 * step
        end = target + 2 * step
        repetitions: list[dict[str, Any]] = []
        for rep in range(1, 4):
            rows, retries, errors = fetch(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": start,
                    "endTime": end,
                    "limit": 5,
                    "priceType": "LAST",
                }
            )
            opens: list[str] = []
            raw_hash = hashlib.sha256(
                json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            for raw in rows:
                try:
                    opens.append(iso_ms(integer(raw[0])))
                except Exception:
                    opens.append("UNPARSEABLE")
            repetitions.append(
                {
                    "rep": rep,
                    "retries": retries,
                    "errors": errors,
                    "returned_opens": opens,
                    "target_present": target_text in opens,
                    "raw_sha256": raw_hash,
                }
            )
            time.sleep(0.25)
        output.append(
            {
                "symbol": symbol,
                "interval": interval,
                "target": target_text,
                "repetitions": repetitions,
                "stable_response": len({r["raw_sha256"] for r in repetitions}) == 1,
            }
        )
    return output


def audit_symbol(symbol: str) -> dict[str, Any]:
    rows_by_open: dict[int, Row] = {}
    duplicates: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    malformed: list[dict[str, Any]] = []
    off_grid: set[int] = set()
    invalid_ohlc: set[int] = set()
    non_positive_price: set[int] = set()
    invalid_additive: set[int] = set()
    close_misalignment: set[int] = set()
    request_count = retry_count = 0
    unrecovered: list[dict[str, Any]] = []

    page_end = END_MS - STEP_MS
    while page_end >= START_MS:
        page_start = max(START_MS, page_end - (PAGE_LIMIT - 1) * STEP_MS)
        request_count += 1
        try:
            raw_rows, retries, _ = fetch(
                {
                    "symbol": symbol,
                    "interval": "5m",
                    "startTime": page_start,
                    "endTime": page_end,
                    "limit": ((page_end - page_start) // STEP_MS) + 1,
                    "priceType": "LAST",
                }
            )
        except Exception as exc:
            unrecovered.append(
                {
                    "page_start": iso_ms(page_start),
                    "page_end": iso_ms(page_end),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            break
        retry_count += retries

        for raw in raw_rows:
            try:
                candidate = integer(raw[0]) if isinstance(raw, list) and raw else None
            except Exception:
                candidate = None
            if candidate is None:
                malformed.append({"open_time": None, "reason": "unparseable open time", "raw": raw})
                continue
            if candidate < page_start or candidate > page_end:
                continue
            try:
                row = parse_row(raw)
            except (InvalidOperation, TypeError, ValueError) as exc:
                malformed.append(
                    {"open_time": iso_ms(candidate), "reason": f"{type(exc).__name__}: {exc}", "raw": raw}
                )
                continue
            if not (START_MS <= row.open_time < END_MS):
                continue
            if (row.open_time - START_MS) % STEP_MS:
                off_grid.add(row.open_time)
            if row.close_time != row.open_time + STEP_MS:
                close_misalignment.add(row.open_time)
            prices = (row.open_price, row.high_price, row.low_price, row.close_price)
            if any(x <= 0 for x in prices):
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
            if any(x < 0 for x in additive):
                invalid_additive.add(row.open_time)
            previous = rows_by_open.get(row.open_time)
            if previous is None:
                rows_by_open[row.open_time] = row
            else:
                duplicates[row.open_time].append(row.canonical())

        if request_count % 200 == 0:
            print(f"{RUN_LABEL} {symbol}: {request_count} pages, through {iso_ms(page_start)}", flush=True)
        page_end = page_start - STEP_MS
        time.sleep(REQUEST_PAUSE_SECONDS)

    expected = set(range(START_MS, END_MS, STEP_MS))
    missing = sorted(expected.difference(rows_by_open))
    duplicate_times = sorted(duplicates)
    conflicts = sorted(
        ts for ts, values in duplicates.items()
        if any(v != rows_by_open[ts].canonical() for v in values)
    )
    return {
        "symbol": symbol,
        "expected_grid_points": len(expected),
        "unique_rows": len(rows_by_open),
        "missing_count": len(missing),
        "missing_timestamps": [iso_ms(x) for x in missing],
        "duplicate_count": len(duplicate_times),
        "duplicate_timestamps": [iso_ms(x) for x in duplicate_times],
        "conflicting_duplicate_count": len(conflicts),
        "conflicting_duplicate_timestamps": [iso_ms(x) for x in conflicts],
        "malformed_count": len(malformed),
        "malformed_rows": malformed,
        "off_grid_count": len(off_grid),
        "off_grid_timestamps": [iso_ms(x) for x in sorted(off_grid)],
        "invalid_ohlc_count": len(invalid_ohlc),
        "invalid_ohlc_timestamps": [iso_ms(x) for x in sorted(invalid_ohlc)],
        "non_positive_price_count": len(non_positive_price),
        "non_positive_price_timestamps": [iso_ms(x) for x in sorted(non_positive_price)],
        "invalid_additive_count": len(invalid_additive),
        "invalid_additive_timestamps": [iso_ms(x) for x in sorted(invalid_additive)],
        "close_misalignment_count": len(close_misalignment),
        "close_misalignment_timestamps": [iso_ms(x) for x in sorted(close_misalignment)],
        "request_count": request_count,
        "retry_count": retry_count,
        "unrecovered_count": len(unrecovered),
        "unrecovered_requests": unrecovered,
    }


def main() -> None:
    started = datetime.now(UTC)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    probes = targeted_probes()
    symbols = [audit_symbol(symbol) for symbol in SYMBOLS]
    result = {
        "schema": "atb.weex_old_history_cloud_revalidation.v1",
        "run_label": RUN_LABEL,
        "endpoint": ENDPOINT,
        "locked_range": f"[{START.isoformat()}, {END.isoformat()})",
        "execution_started_at": started.isoformat(),
        "execution_finished_at": datetime.now(UTC).isoformat(),
        "targeted_probes": probes,
        "symbols": symbols,
    }
    json_path = OUTPUT_DIR / "result.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "run_label": RUN_LABEL,
        "probe_summary": [
            {
                "symbol": p["symbol"],
                "interval": p["interval"],
                "target": p["target"],
                "present": [r["target_present"] for r in p["repetitions"]],
                "stable_response": p["stable_response"],
            }
            for p in probes
        ],
        "symbol_summary": [
            {k: s[k] for k in (
                "symbol", "expected_grid_points", "unique_rows", "missing_count",
                "duplicate_count", "conflicting_duplicate_count", "malformed_count",
                "off_grid_count", "invalid_ohlc_count", "non_positive_price_count",
                "invalid_additive_count", "close_misalignment_count", "request_count",
                "retry_count", "unrecovered_count"
            )}
            for s in symbols
        ],
        "result_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
