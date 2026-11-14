"""Build Issue #241's metadata-only weekly eligibility calendar.

This helper reads only the accepted v3 quality manifest and inert proposed
parameters.  It does not read candles, produce signals, or calculate PnL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SYMBOLS = ("BTCUSDT", "ETHUSDT")
PRICE_FIELDS = frozenset({"open", "high", "low", "close"})
VOLUME_FIELDS = frozenset({"volume", "quote_volume"})
CALENDAR_CONTRACT = {
    "lookback_4h_bars": 42,
    "start_weekday_utc": "MONDAY",
    "start_time_utc": "00:00:00",
    "initial_order_activation_delay_minutes": 1,
    "scheduled_exit_weekday_utc": "SUNDAY",
    "scheduled_exit_decision_time_utc": "20:00:00",
    "scheduled_flatten_activation_delay_minutes": 1,
}


class CalendarError(ValueError):
    """The inert metadata inputs or calendar contract are inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CalendarError(f"expected an object: {path}")
    return value


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise CalendarError(f"timestamp is not canonical UTC: {value}")
    return parsed


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def intersects(
    left_start: datetime, left_end: datetime, right_start: datetime, right_end: datetime
) -> bool:
    return left_start < right_end and right_start < left_end


def first_monday_at_or_after(value: datetime) -> datetime:
    return value + timedelta(days=(7 - value.weekday()) % 7)


def partition_for(start: datetime) -> str:
    if start < datetime(2023, 1, 2, tzinfo=UTC):
        return "DEVELOPMENT"
    if start < datetime(2025, 1, 6, tzinfo=UTC):
        return "VALIDATION"
    return "RETROSPECTIVE_ASSESSMENT"


def _intervals(rows: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    return [(parse_utc(row["start"]), parse_utc(row["end_exclusive"])) for row in rows]


def _validate_calendar_contract(parameters: dict[str, Any]) -> None:
    actual = {
        "lookback_4h_bars": parameters["geometry"]["lookback_4h_bars"],
        **{
            key: parameters["episode"][key]
            for key in CALENDAR_CONTRACT
            if key != "lookback_4h_bars"
        },
    }
    if actual != CALENDAR_CONTRACT:
        raise CalendarError(
            "parameters differ from the fixed calendar timing/lookback contract: "
            f"expected={CALENDAR_CONTRACT!r}, actual={actual!r}"
        )


def _episode(
    symbol: str,
    start: datetime,
    data_start: datetime,
    data_end: datetime,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    warmup_start = start - timedelta(hours=42 * 4)
    activation = start + timedelta(minutes=1)
    exit_decision = start + timedelta(days=6, hours=20)
    flatten_at = exit_decision + timedelta(minutes=1)
    required_end = flatten_at + timedelta(minutes=1)
    reasons: set[str] = set()
    flags: set[str] = set()

    if warmup_start < data_start:
        reasons.add("WARMUP_OUTSIDE_ACCEPTED_INTERVAL")
    if required_end > data_end:
        reasons.add("SCHEDULED_EXIT_DATA_OUTSIDE_ACCEPTED_INTERVAL")

    try:
        reconciliation = coverage["reconciliation"]
        discrepancies = reconciliation["discrepancies"]
        trade_gaps = _intervals(coverage["trade_price_1m"]["grid"]["gaps"])
        derived_gaps = _intervals(coverage["derived_4h"]["grid"]["gaps"])
        mark_gaps = _intervals(coverage["mark_1m"]["grid"]["gaps"])
        funding_status = coverage["funding"]["continuity_status"]
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "symbol": symbol,
            "episode_start": iso(start),
            "partition": partition_for(start),
            "status": "UNKNOWN",
            "reason_codes": ["REQUIRED_QUALITY_METADATA_UNAVAILABLE"],
            "flag_codes": [],
            "metadata_error": type(exc).__name__,
            "warmup_start": iso(warmup_start),
            "initial_order_activation": iso(activation),
            "scheduled_exit_decision": iso(exit_decision),
            "scheduled_flatten_at": iso(flatten_at),
            "required_data_end_exclusive": iso(required_end),
        }

    required_price_start = max(warmup_start, data_start)
    for gap_start, gap_end in (*trade_gaps, *derived_gaps):
        if intersects(required_price_start, min(required_end, data_end), gap_start, gap_end):
            reasons.add("REQUIRED_TRADE_OR_DERIVED_PRICE_GAP")
    for gap_start, gap_end in mark_gaps:
        if intersects(start, min(required_end, data_end), gap_start, gap_end):
            reasons.add("REQUIRED_ACTIVE_MARK_GAP")

    for row in discrepancies:
        bar_start = datetime.fromtimestamp(int(row["open_time_ms"]) / 1000, tz=UTC)
        bar_end = bar_start + timedelta(hours=4)
        fields = frozenset(str(field) for field in row["mismatched_fields"])
        if not intersects(required_price_start, min(required_end, data_end), bar_start, bar_end):
            continue
        if fields & PRICE_FIELDS:
            reasons.add(
                "PRICE_DISAGREEMENT_IN_WARMUP"
                if bar_start < start
                else "PRICE_DISAGREEMENT_IN_ACTIVE_OR_EXIT_WINDOW"
            )
        if fields & VOLUME_FIELDS:
            flags.add("VOLUME_DISCREPANCY_PRESENT_UNUSED_BY_MODEL")

    if funding_status != "SCHEDULE_COMPLETE_UNDER_REPORTED_INTERVALS":
        flags.add("FUNDING_CONTINUITY_PARTIAL_OR_UNCERTAIN")

    status = "METADATA_INELIGIBLE" if reasons else "METADATA_ELIGIBLE"
    return {
        "symbol": symbol,
        "episode_start": iso(start),
        "partition": partition_for(start),
        "status": status,
        "reason_codes": sorted(reasons),
        "flag_codes": sorted(flags),
        "warmup_start": iso(warmup_start),
        "initial_order_activation": iso(activation),
        "scheduled_exit_decision": iso(exit_decision),
        "scheduled_flatten_at": iso(flatten_at),
        "required_data_end_exclusive": iso(required_end),
    }


def generate_calendar(
    manifest: dict[str, Any], parameters: dict[str, Any], parameter_sha: str
) -> dict[str, Any]:
    _validate_calendar_contract(parameters)
    accepted = parameters["accepted_data"]
    if manifest.get("schema") != "binance-usdm-qualification-manifest-v3":
        raise CalendarError("calendar requires the accepted v3 manifest schema")
    if manifest.get("classification") != accepted["classification"]:
        raise CalendarError("accepted data classification differs")
    data_start = parse_utc(accepted["interval_start"])
    data_end = parse_utc(accepted["interval_end_exclusive"])
    first = first_monday_at_or_after(data_start)
    rows: list[dict[str, Any]] = []
    start = first
    while start < data_end:
        for symbol in SYMBOLS:
            rows.append(
                _episode(
                    symbol,
                    start,
                    data_start,
                    data_end,
                    manifest["component_coverage"][symbol],
                )
            )
        start += timedelta(days=7)

    summaries: dict[str, Any] = {}
    for symbol in SYMBOLS:
        owned = [row for row in rows if row["symbol"] == symbol]
        summaries[symbol] = {
            "episodes": len(owned),
            "status_counts": dict(sorted(Counter(row["status"] for row in owned).items())),
            "reason_counts": dict(
                sorted(Counter(reason for row in owned for reason in row["reason_codes"]).items())
            ),
            "flag_counts": dict(
                sorted(Counter(flag for row in owned for flag in row["flag_codes"]).items())
            ),
            "partition_counts": dict(sorted(Counter(row["partition"] for row in owned).items())),
        }
    return {
        "schema": "btc-eth-grid-4h-v1-metadata-eligibility-calendar",
        "status": "PROPOSED_NOT_EXECUTABLE",
        "protocol_id": parameters["protocol_id"],
        "protocol_version": parameters["protocol_version"],
        "parameter_sha256": parameter_sha,
        "v3_manifest_sha256": accepted["v3_manifest_sha256"],
        "v3_report_sha256": accepted["v3_report_sha256"],
        "classification": accepted["classification"],
        "calendar_semantics": "RETROSPECTIVE_METADATA_SAMPLE_ELIGIBILITY_NOT_CAUSAL_LIVE_FILTER",
        "required_dependencies": {
            "warmup": "42 completed canonical-derived 4h bars; MARK not required",
            "active_and_exit": "complete trade-price 1m, canonical-derived 4h and MARK 1m",
            "funding": (
                "actual retained source-venue events under the explicit archive-completeness "
                "assumption; latest completed MARK maximum age 120000 ms"
            ),
            "volume": (
                "not used by model; volume-only discrepancies are flags; any OHLC component "
                "still excludes"
            ),
        },
        "summary": summaries,
        "episodes": rows,
        "strategy_evaluation_performed": False,
        "price_rows_read": 0,
        "pnl_calculated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    parameter_path = args.parameters.resolve()
    if (
        sha256_file(manifest_path)
        != read_json(parameter_path)["accepted_data"]["v3_manifest_sha256"]
    ):
        raise CalendarError("v3 manifest bytes differ from the accepted identity")
    result = generate_calendar(
        read_json(manifest_path), read_json(parameter_path), sha256_file(parameter_path)
    )
    body = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(body)
    print(
        json.dumps(
            {
                "episodes": len(result["episodes"]),
                "output": str(output),
                "sha256": hashlib.sha256(body).hexdigest(),
                "strategy_evaluation_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
