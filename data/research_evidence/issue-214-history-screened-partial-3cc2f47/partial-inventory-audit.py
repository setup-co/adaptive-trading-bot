from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Context, Decimal, Inexact, Rounded, localcontext
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

CHECKOUT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-214-history-screened-196d-corpus")
ROOT = CHECKOUT / "data/research_corpus/history-screened-196d-last-v1"
RUN = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-214-run")
OUTPUT = RUN / "partial-inventory-report.json"
PLAN_SHA = "0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713"
PANEL = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ZECUSDT",
    "ZAMAUSDT", "BCHUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT",
)
SHARED = PANEL[:-1]
START = int(datetime(2026, 4, 1, tzinfo=UTC).timestamp()) * 1000
END = int(datetime(2026, 10, 14, tzinfo=UTC).timestamp()) * 1000
STEP = 300_000
DAY = 86_400_000
EXPECTED = 56_448
FIELDS = (
    "open_time_ms", "open", "high", "low", "close", "base_volume",
    "close_time_ms", "quote_volume", "trade_count",
    "taker_buy_base_volume", "taker_buy_quote_volume",
)
CTX = Context(prec=100)
CTX.traps[Inexact] = True
CTX.traps[Rounded] = True


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def number(value: object, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("invalid numeric field")
    result = Decimal(value)
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError("numeric field outside bounds")
    return result


def parse(body: bytes, query: dict[str, str], expected: int) -> dict[int, tuple[Any, ...]]:
    payload = json.loads(body, parse_float=Decimal)
    if not isinstance(payload, list) or len(payload) != expected or len(payload) > 100:
        raise ValueError("response row count changed")
    step = STEP if query["interval"] == "5m" else DAY
    start, end = int(query["startTime"]), int(query["endTime"])
    rows: dict[int, tuple[Any, ...]] = {}
    for item in payload:
        if not isinstance(item, list) or len(item) != 11:
            raise ValueError("wire row shape changed")
        opened, closed, trades = item[0], item[6], item[8]
        if (
            isinstance(opened, bool) or not isinstance(opened, int)
            or isinstance(closed, bool) or not isinstance(closed, int)
            or isinstance(trades, bool) or not isinstance(trades, int)
            or opened < start or opened > end or opened % step
            or closed != opened + step or opened in rows or trades < 0
        ):
            raise ValueError("wire row grid/count changed")
        o, high, low, close = (number(item[index], True) for index in (1, 2, 3, 4))
        base, quote, taker_base, taker_quote = (
            number(item[index]) for index in (5, 7, 9, 10)
        )
        if low > min(o, close) or high < max(o, close) or low > high:
            raise ValueError("OHLC relationship changed")
        if taker_base > base or taker_quote > quote:
            raise ValueError("taker volume exceeds total")
        rows[opened] = (
            opened, o, high, low, close, base, closed, quote, trades,
            taker_base, taker_quote,
        )
    return rows


def parquet_rows(path: Path, expected_sha: str) -> dict[int, tuple[Any, ...]]:
    if sha(path) != expected_sha:
        raise ValueError("predecessor Parquet hash changed")
    rows: dict[int, tuple[Any, ...]] = {}
    for item in pq.read_table(path).to_pylist():
        opened = int(item["open_time_ms"])
        if START <= opened < END:
            rows[opened] = (
                opened,
                number(item["open"], True),
                number(item["high"], True),
                number(item["low"], True),
                number(item["close"], True),
                number(item["base_volume"]),
                int(item["close_time_ms"]),
                number(item["quote_volume"]),
                int(item["trade_count"]),
                number(item["taker_buy_base_volume"]),
                number(item["taker_buy_quote_volume"]),
            )
    return rows


def merge(target: dict[int, tuple[Any, ...]], additions: dict[int, tuple[Any, ...]]) -> None:
    for opened, row in additions.items():
        if opened in target and target[opened] != row:
            raise ValueError("conflicting reused/new candle")
        target[opened] = row


def ranges(values: set[int]) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    for opened in sorted(values):
        if result and result[-1]["end_ms"] == opened:
            result[-1]["end_ms"] = opened + STEP
            result[-1]["rows"] += 1
        else:
            result.append({"start_ms": opened, "end_ms": opened + STEP, "rows": 1})
    return result


def frame_count(spans: list[dict[str, int]], factor: int) -> int:
    size = factor * STEP
    count = 0
    for span in spans:
        aligned = ((span["start_ms"] + size - 1) // size) * size
        if aligned + size <= span["end_ms"]:
            count += (span["end_ms"] - aligned) // size
    return count


def daily_status(
    rows: dict[int, tuple[Any, ...]], daily: dict[int, tuple[Any, ...]]
) -> Counter[str]:
    counts: Counter[str] = Counter()
    with localcontext(CTX):
        for day in range(196):
            opened = START + day * DAY
            parts = [rows.get(opened + index * STEP) for index in range(288)]
            if any(part is None for part in parts):
                counts["5M_INCOMPLETE"] += 1
                continue
            complete = [part for part in parts if part is not None]
            authority = daily[opened]
            actual = (
                complete[0][1],
                max(part[2] for part in complete),
                min(part[3] for part in complete),
                complete[-1][4],
                sum((part[5] for part in complete), Decimal(0)),
                sum((part[7] for part in complete), Decimal(0)),
                sum(part[8] for part in complete),
                sum((part[9] for part in complete), Decimal(0)),
                sum((part[10] for part in complete), Decimal(0)),
            )
            reference = (
                authority[1], authority[2], authority[3], authority[4], authority[5],
                authority[7], authority[8], authority[9], authority[10],
            )
            counts["MATCH" if actual == reference else "MISMATCH"] += 1
    return counts


def main() -> None:
    if sha(ROOT / "plan.json") != PLAN_SHA:
        raise ValueError("frozen plan changed")
    plan = json.loads((ROOT / "plan.json").read_bytes())
    entries = {entry["id"]: entry for entry in plan["requests"]}
    result_paths = list((ROOT / "working").rglob("*.result.json"))
    start_paths = list((ROOT / "working").rglob("*.started.json"))
    states: dict[str, dict[str, Any]] = {}
    successful_rows: dict[str, dict[int, tuple[Any, ...]]] = {symbol: {} for symbol in PANEL}
    for path in result_paths:
        state = json.loads(path.read_bytes())
        request_id = state["request_id"]
        entry = entries[request_id]
        started = path.with_name(path.name.replace(".result.json", ".started.json"))
        raw = path.with_name(path.name.replace(".result.json", ".raw"))
        if not started.is_file() or request_id in states:
            raise ValueError("attempt identity/STARTED record changed")
        states[request_id] = state
        if state["outcome"] == "HTTP_OK":
            if sha(raw) != state["raw_sha256"] or raw.stat().st_size != state["raw_bytes"]:
                raise ValueError("successful raw hash/size changed")
            parsed = parse(raw.read_bytes(), entry["query"], entry["expected_rows"])
            merge(successful_rows[entry["query"]["symbol"]], parsed)
        elif raw.exists():
            raise ValueError("network failure unexpectedly has raw bytes")
    if len(start_paths) != 1649 or len(result_paths) != 1649:
        raise ValueError("ledger count changed")

    failed = [
        {"id": request_id, "query": entries[request_id]["query"],
         "expected_rows": entries[request_id]["expected_rows"], "result": state}
        for request_id, state in states.items() if state["outcome"] != "HTTP_OK"
    ]
    unattempted = [
        {"id": entry["id"], "query": entry["query"], "expected_rows": entry["expected_rows"]}
        for entry in plan["requests"] if entry["id"] not in states
    ]

    c2_tail = CHECKOUT / "data/research_corpus/issue-179-weex-last-90d-tail-v1"
    c2_cont = CHECKOUT / "data/research_corpus/issue-179-weex-last-90d-continuation-v1"
    reused: dict[str, dict[int, tuple[Any, ...]]] = {}
    for symbol in SHARED:
        source = c2_tail if symbol in {"AVAXUSDT", "DOGEUSDT"} else c2_cont
        name = f"combined/parquet/{symbol}-5m.parquet"
        rows = parquet_rows(source / name, plan["predecessors"]["c2_5m_shards"][symbol])
        reused[symbol] = rows
    reused["LINKUSDT"] = {}
    probe_root = CHECKOUT / "data/research_evidence/issue-206-weex-history-probe-3884cf7"
    for reference in plan["predecessors"]["probe_references"]:
        path = probe_root / reference["path"]
        if sha(path) != reference["sha256"]:
            raise ValueError("probe raw changed")
        merge(reused[reference["symbol"]], parse(path.read_bytes(), reference["query"], reference["rows"]))

    daily: dict[str, dict[int, tuple[Any, ...]]] = {symbol: {} for symbol in PANEL}
    daily_root = CHECKOUT / "data/research_evidence/issue-212-history-screened-shortlist-adbf3af"
    for reference in plan["predecessors"]["daily_references"]:
        path = daily_root / reference["path"]
        if sha(path) != reference["sha256"]:
            raise ValueError("daily raw changed")
        merge(daily[reference["symbol"]], parse(path.read_bytes(), reference["query"], reference["rows"]))

    symbols: dict[str, object] = {}
    for symbol in PANEL:
        available = dict(reused[symbol])
        merge(available, successful_rows[symbol])
        epochs = ranges(set(available))
        symbol_failed = [row for row in failed if row["query"]["symbol"] == symbol]
        symbol_unattempted = [row for row in unattempted if row["query"]["symbol"] == symbol]
        symbols[symbol] = {
            "expected_5m_positions": EXPECTED,
            "validated_reused_5m_rows": len(reused[symbol]),
            "validated_new_5m_rows": len(successful_rows[symbol]),
            "validated_available_5m_rows": len(available),
            "unavailable_5m_positions": EXPECTED - len(available),
            "successful_response_internal_absences": 0,
            "failed_requests": symbol_failed,
            "failed_request_expected_positions": sum(row["expected_rows"] for row in symbol_failed),
            "unattempted_requests": symbol_unattempted,
            "unattempted_expected_positions": sum(row["expected_rows"] for row in symbol_unattempted),
            "available_epochs": epochs,
            "derived_complete_row_counts": {
                "15m": frame_count(epochs, 3), "1h": frame_count(epochs, 12),
                "4h": frame_count(epochs, 48),
            },
            "daily_comparison_counts": dict(daily_status(available, daily[symbol])),
            "qualification": "PARTIAL_ROUTE_STOPPED",
        }

    total_new = sum(len(rows) for rows in successful_rows.values())
    total_reused = sum(len(rows) for rows in reused.values())
    report = {
        "schema": "history-screened-196d-partial-publication-v1",
        "protocol": "HISTORY_SCREENED_196D_LAST_CORPUS_V1",
        "capture_status": "ROUTE_STOPPED",
        "capture_exit_status": "CAPTURE_EXIT_UNKNOWN_NOT_RECORDED",
        "dataset_qualification": "BLOCKED_NOT_ESTABLISHED",
        "full_panel_180_day_readiness": "BLOCKED_NOT_ESTABLISHED",
        "executable_sha": "3cc2f475cddc0466d28edd5f11587b7e8321e17a",
        "plan_sha256": PLAN_SHA,
        "ledger": {"attempts": 1649, "distinct": 1649, "successful": 1648,
                   "failed": len(failed), "retries": 0, "unattempted": len(unattempted)},
        "position_totals": {
            "expected": len(PANEL) * EXPECTED,
            "validated_reused": total_reused,
            "validated_new": total_new,
            "validated_available": total_reused + total_new,
            "successful_response_internal_absences": 0,
            "failed_request_expected_positions": sum(row["expected_rows"] for row in failed),
            "unattempted_expected_positions": sum(row["expected_rows"] for row in unattempted),
        },
        "failed_requests": failed,
        "unattempted_requests": unattempted,
        "symbols": symbols,
        "outputs": {
            "canonical_5m": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
            "derived_15m_1h_4h": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
            "immutable_dataset_manifest": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
        },
        "interpretation": "Validated data accounting only; no exchange-availability or strategy claim.",
    }
    if total_reused != 234956 or total_new != 158188 or len(failed) != 1 or len(unattempted) != 1784:
        raise ValueError("partial totals changed")
    OUTPUT.write_bytes(canonical(report))
    print(json.dumps({"report_sha256": sha(OUTPUT), "totals": report["position_totals"]}, sort_keys=True))


if __name__ == "__main__":
    main()
