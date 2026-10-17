"""Read-only diagnosis of the immutable Issue #187 retained evidence.

This module deliberately parses the tagged JSON as data.  It never imports or
constructs the stored project dataclasses and never calls a strategy, feature,
risk, safety, simulator, or accounting authority.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

PROTOCOL = "RESEARCH_V2_RETAINED_DIAGNOSIS_V1"
SCHEMA = "research-v2-retained-diagnosis-v1"
RUN_UUID = "686bd4a1-ffff-4391-8562-f1e4caf7a575"
EXECUTABLE_SHA = "316744efa9c4b50aa8d999b3c4d11fd874b4c28c"
SOURCE_ID = "weex-research-v2-last-18912cf7e7f97d518a74551f"
DATASET_VERSION = "weex-research-v2-last-90d-issue-179-combined-v2"
MANIFEST_SHA256 = "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
PROTOCOL_SHA256 = "ab78c022616bcffdb7af8b835064a14409e1d47bdc3e31649192de9cf0c63e1c"
TASK_PLAN_SHA256 = "03376c954ca51ca50754d925189cad7be55f14249cfec29e6cbbe710a1fcfca1"
INDEX_SHA256 = "677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2"
PROJECTION_SHA256 = "46d7e10899f06f4d16b6ded46f8cba5c62d9d25c119d05e35ca11b5af5833667"
PROJECTION_INTEGRITY = "7566025c965ae095e90f4032297e6e7c7bb6f90f18b82f0b2a2744380a50b522"
EVIDENCE_RELATIVE = Path("data/research_evidence/issue-187-exploratory-screen-316744efa9c4b50a")
LEDGER_RELATIVE = Path("ledger") / RUN_UUID
SCENARIOS = ("BASE_2_2_10_ZERO_LATENCY", "COMBINED_STRESS_4_4_20_ONE_SECOND")
FAMILIES = ("TREND_PULLBACK", "BREAKOUT_RETEST", "RANGE_MEAN_REVERSION")
TERMINAL_STATUSES = (
    "NO_SIGNAL",
    "RISK_BLOCKED",
    "SAFETY_BLOCKED",
    "ENTRY_UNFILLED",
    "ORDINARY_CLOSED",
    "ORDINARY_OPEN",
    "RECOVERY_CLOSED",
    "RECOVERY_OPEN",
)
TASKS = 30
DECISIONS_PER_TASK = 6048


class DiagnosisError(ValueError):
    """Retained evidence is missing, malformed, relabeled, or contradictory."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _decimal(raw: list[object]) -> Decimal:
    if len(raw) != 4 or type(raw[1]) is not int or not isinstance(raw[2], list):
        raise DiagnosisError("invalid exact Decimal tag")
    digits = raw[2]
    if any(type(item) is not int or not 0 <= item <= 9 for item in digits):
        raise DiagnosisError("invalid exact Decimal digits")
    try:
        value = Decimal((raw[1], tuple(digits), int(str(raw[3]))))
    except (ValueError, TypeError) as exc:
        raise DiagnosisError("invalid exact Decimal evidence") from exc
    if not value.is_finite():
        raise DiagnosisError("non-finite Decimal evidence")
    return value


def decode_tagged(raw: object) -> object:
    """Decode canonical tags to inert Python values without class construction."""

    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise DiagnosisError("value is not tagged JSON")
    tag = raw[0]
    if tag == "none" and len(raw) == 1:
        return None
    if tag == "bool" and len(raw) == 2 and type(raw[1]) is bool:
        return raw[1]
    if tag == "int" and len(raw) == 2 and type(raw[1]) is int:
        return raw[1]
    if tag == "str" and len(raw) == 2 and isinstance(raw[1], str):
        return raw[1]
    if tag == "decimal":
        return _decimal(raw)
    if tag == "datetime" and len(raw) == 3 and isinstance(raw[1], str):
        try:
            value = datetime.fromisoformat(raw[1]).replace(fold=raw[2])
        except (ValueError, TypeError) as exc:
            raise DiagnosisError("invalid datetime evidence") from exc
        if value.utcoffset() != timedelta(0):
            raise DiagnosisError("datetime evidence is not UTC")
        return value
    if tag == "timedelta" and len(raw) == 4:
        return timedelta(days=raw[1], seconds=raw[2], microseconds=raw[3])
    if tag == "tuple" and len(raw) == 2 and isinstance(raw[1], list):
        return tuple(decode_tagged(item) for item in raw[1])
    if tag == "enum" and len(raw) == 3 and isinstance(raw[1], str):
        return decode_tagged(raw[2])
    if tag == "dataclass" and len(raw) == 3 and isinstance(raw[1], str):
        if not raw[1].startswith("adaptive_trading_bot.") or not isinstance(raw[2], list):
            raise DiagnosisError("stored dataclass is outside the project schema")
        result: dict[str, object] = {"__type__": raw[1]}
        for pair in raw[2]:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] in result
            ):
                raise DiagnosisError("stored dataclass fields are malformed")
            result[pair[0]] = decode_tagged(pair[1])
        return result
    if tag == "decision_ref" and len(raw) == 1:
        return {"__reference__": "decision"}
    if (
        tag == "candle_slice"
        and len(raw) == 3
        and type(raw[1]) is int
        and type(raw[2]) is int
        and 0 <= raw[1] <= raw[2]
    ):
        return {"__reference__": "candle_slice", "start": raw[1], "end": raw[2]}
    raise DiagnosisError(f"unknown tagged evidence shape: {tag}")


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiagnosisError(f"{label} is not an object")
    return value


def _require_tuple(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise DiagnosisError(f"{label} is not a tuple")
    return value


def _str_decimal(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise DiagnosisError("expected exact Decimal evidence")
    return str(value)


def _iso(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise DiagnosisError("expected UTC datetime evidence")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _seconds(later: object | None, earlier: object | None) -> str | None:
    if later is None or earlier is None:
        return None
    if not isinstance(later, datetime) or not isinstance(earlier, datetime):
        raise DiagnosisError("wait endpoints are not datetimes")
    with localcontext() as context:
        context.prec = 34
        return str(Decimal(str((later - earlier).total_seconds())))


def load_index(evidence: Path) -> tuple[dict[str, tuple[int, str]], bytes]:
    path = evidence / "artifact-index.json"
    payload = path.read_bytes()
    if _sha(payload) != INDEX_SHA256:
        raise DiagnosisError("artifact index differs from accepted Issue #187 identity")
    raw = _require_dict(json.loads(payload), "artifact index")
    if raw.get("run_uuid") != RUN_UUID or raw.get("executable_sha") != EXECUTABLE_SHA:
        raise DiagnosisError("artifact index run identity differs")
    files: dict[str, tuple[int, str]] = {}
    for item in raw.get("files", []):
        row = _require_dict(item, "artifact index row")
        name, size, digest = row.get("path"), row.get("size_bytes"), row.get("sha256")
        if not isinstance(name, str) or type(size) is not int or not isinstance(digest, str):
            raise DiagnosisError("artifact index row is malformed")
        if name in files:
            raise DiagnosisError("artifact index contains a duplicate path")
        files[name] = (size, digest)
    return files, payload


def verify_indexed_file(evidence: Path, relative: str, files: dict[str, tuple[int, str]]) -> bytes:
    expected = files.get(relative)
    if expected is None:
        raise DiagnosisError(f"required artifact is absent from index: {relative}")
    path = evidence / relative
    payload = path.read_bytes()
    if len(payload) != expected[0] or _sha(payload) != expected[1]:
        raise DiagnosisError(f"required artifact bytes differ: {relative}")
    return payload


def _population(payload: bytes) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    try:
        decompressed = gzip.decompress(payload)
        raw = _require_dict(json.loads(decompressed), "population envelope")
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosisError("population evidence is unavailable") from exc
    if (
        _canonical(raw) != decompressed
        or raw.get("schema_version") != "research-v2-screen-authority-evidence-v1"
    ):
        raise DiagnosisError("population evidence is not canonical or versioned")
    decoded = _require_dict(decode_tagged(raw.get("population")), "population")
    decisions = tuple(
        _require_dict(item, "decision")
        for item in _require_tuple(decoded.get("decisions"), "population decisions")
    )
    if len(decisions) != DECISIONS_PER_TASK:
        raise DiagnosisError("task population is incomplete")
    return decoded, decisions


def _authority_records(payload: bytes) -> Iterable[dict[str, Any]]:
    try:
        decompressed = gzip.decompress(payload)
    except OSError as exc:
        raise DiagnosisError("authority evidence is unavailable") from exc
    for line in decompressed.splitlines():
        try:
            raw = _require_dict(json.loads(line), "authority row")
        except json.JSONDecodeError as exc:
            raise DiagnosisError("authority row is not JSON") from exc
        if raw.get("schema_version") != "research-v2-screen-authority-evidence-v1":
            raise DiagnosisError("authority row schema differs")
        result = _require_dict(decode_tagged(raw.get("result")), "all-fill result")
        yield {
            "decision_index": raw.get("decision_index"),
            "scenario": raw.get("scenario"),
            "result": result,
        }


def validate_authority_slot(
    *,
    task_index: int,
    expected_task_index: int,
    decision_index: object,
    scenario: object,
    seen: set[tuple[int, str]],
) -> tuple[int, str]:
    """Reject missing/misassigned/duplicate task-scenario evidence slots."""

    if task_index != expected_task_index:
        raise DiagnosisError("authority row belongs to the wrong task")
    if type(decision_index) is not int or not 0 <= decision_index < DECISIONS_PER_TASK:
        raise DiagnosisError("authority decision index is outside population")
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise DiagnosisError("authority scenario is unknown")
    slot = (decision_index, scenario)
    if slot in seen:
        raise DiagnosisError("authority scenario slot is duplicated")
    seen.add(slot)
    return slot


def _family_name(task_family: str) -> str:
    return task_family.upper()


def _decision_facts(decision: dict[str, Any]) -> dict[str, Any]:
    strategy = _require_dict(decision.get("strategy_evaluation"), "strategy evaluation")
    risk = _require_dict(decision.get("risk_decision"), "risk decision")
    safety = _require_dict(decision.get("safety_decision"), "safety decision")
    identity = _require_dict(decision.get("identity"), "decision identity")
    audit = _require_dict(strategy.get("rule_audit"), "strategy rule audit")
    return {
        "action": strategy.get("action"),
        "reasons_for": _require_tuple(strategy.get("reasons_for"), "reasons_for"),
        "reasons_against": _require_tuple(strategy.get("reasons_against"), "reasons_against"),
        "audit": audit,
        "risk_disposition": risk.get("disposition"),
        "risk_reasons": _require_tuple(risk.get("reasons"), "risk reasons"),
        "safety_disposition": safety.get("disposition"),
        "safety_reasons": _require_tuple(safety.get("reasons"), "safety reasons"),
        "observed_at": identity.get("observed_at"),
    }


def _exit_reason(family: str, legacy: dict[str, Any], branch: dict[str, Any]) -> object:
    if family == "TREND_PULLBACK":
        candidate = legacy.get("ema50_candidate")
        if isinstance(candidate, dict):
            return candidate.get("winning_exit_cause")
        if isinstance(branch.get("recovery"), dict):
            return "POST_FILL_STOP_RECOVERY"
        return "UNAVAILABLE"
    if family in {"BREAKOUT_RETEST", "RANGE_MEAN_REVERSION"}:
        return legacy.get("exit_cause")
    return branch.get("label")


def _trade_row(
    *,
    task_id: str,
    decision_index: int,
    decision: dict[str, Any],
    screen: dict[str, Any],
    result: dict[str, Any],
    authority_path: str,
    authority_sha: str,
) -> dict[str, Any]:
    entry_intent = _require_dict(result.get("entry_intent"), "entry intent")
    branches = tuple(
        _require_dict(item, "all-fill branch")
        for item in _require_tuple(result.get("branches"), "branches")
    )
    if not branches:
        raise DiagnosisError("admitted result has no accounting branch")
    branch = branches[-1]
    entry = _require_dict(branch.get("entry_fill"), "entry fill")
    exit_fill_raw = branch.get("exit_fill")
    exit_fill = None if exit_fill_raw is None else _require_dict(exit_fill_raw, "exit fill")
    exit_request_raw = branch.get("exit_request")
    exit_request = (
        None if exit_request_raw is None else _require_dict(exit_request_raw, "exit request")
    )
    position = _require_dict(branch.get("final_position"), "final position")
    legacy = _require_dict(result.get("legacy_replay"), "legacy replay")
    recovery_raw = branch.get("recovery")
    recovery = None if recovery_raw is None else _require_dict(recovery_raw, "recovery")
    reference = entry_intent.get("reference_price")
    stop = entry_intent.get("stop_basis")
    approved_risk = entry_intent.get("approved_risk_quote")
    approved_notional = entry_intent.get("approved_notional_quote")
    fees = position.get("cumulative_fees")
    if not all(
        isinstance(value, Decimal)
        for value in (reference, stop, approved_risk, approved_notional, fees)
    ):
        raise DiagnosisError("trade economics are not exact Decimal evidence")
    assert isinstance(reference, Decimal)
    assert isinstance(stop, Decimal)
    assert isinstance(approved_risk, Decimal)
    assert isinstance(approved_notional, Decimal)
    assert isinstance(fees, Decimal)
    with localcontext() as context:
        context.prec = 50
        geometry = abs(reference - stop) / reference
        fee_risk = fees / approved_risk
        fee_notional = fees / approved_notional
    result_seal = result.get("integrity_seal")
    if result_seal != screen.get("all_fill_result_sha256"):
        raise DiagnosisError("partition/result integrity identity differs")
    return {
        "task_id": task_id,
        "decision_index": decision_index,
        "decision_identity_sha256": screen.get("identity"),
        "scenario": result.get("scenario"),
        "family": result.get("family"),
        "symbol": screen.get("symbol"),
        "block": screen.get("block"),
        "week": screen.get("week"),
        "regime": screen.get("regime"),
        "side": entry_intent.get("side"),
        "status": branch.get("status"),
        "primary_exit_category": branch.get("label"),
        "primary_exit_reason": _exit_reason(str(result.get("family")), legacy, branch),
        "recovery_reasons": None
        if recovery is None
        else "|".join(
            str(item) for item in _require_tuple(recovery.get("reasons"), "recovery reasons")
        ),
        "reference_price": _str_decimal(reference),
        "stop_basis": _str_decimal(stop),
        "reference_stop_distance_ratio": _str_decimal(geometry),
        "quantity": _str_decimal(entry_intent.get("quantity")),
        "approved_notional_quote": _str_decimal(approved_notional),
        "approved_risk_quote": _str_decimal(approved_risk),
        "entry_created_at": _iso(entry.get("created_at")),
        "entry_eligible_at": _iso(entry.get("eligible_at")),
        "entry_execution_candle_open_at": _iso(entry.get("execution_candle_open_time")),
        "entry_fill_at": _iso(entry.get("fill_at")),
        "entry_wait_seconds": _seconds(entry.get("fill_at"), entry.get("created_at")),
        "entry_configured_latency_seconds": _seconds(
            entry.get("eligible_at"), entry.get("created_at")
        ),
        "entry_post_eligibility_wait_seconds": _seconds(
            entry.get("execution_candle_open_time"), entry.get("eligible_at")
        ),
        "entry_reference_open_price": _str_decimal(entry.get("reference_open_price")),
        "entry_fill_price": _str_decimal(entry.get("fill_price")),
        "entry_fee_quote": _str_decimal(entry.get("fee_quote")),
        "exit_created_at": None if exit_request is None else _iso(exit_request.get("created_at")),
        "exit_eligible_at": None if exit_fill is None else _iso(exit_fill.get("eligible_at")),
        "exit_execution_candle_open_at": None
        if exit_fill is None
        else _iso(exit_fill.get("execution_candle_open_time")),
        "exit_fill_at": None if exit_fill is None else _iso(exit_fill.get("fill_at")),
        "exit_wait_seconds": None
        if exit_fill is None or exit_request is None
        else _seconds(exit_fill.get("fill_at"), exit_request.get("created_at")),
        "exit_configured_latency_seconds": None
        if exit_fill is None
        else _seconds(exit_fill.get("eligible_at"), exit_fill.get("created_at")),
        "exit_post_eligibility_wait_seconds": None
        if exit_fill is None
        else _seconds(exit_fill.get("execution_candle_open_time"), exit_fill.get("eligible_at")),
        "exit_reference_open_price": None
        if exit_fill is None
        else _str_decimal(exit_fill.get("reference_open_price")),
        "exit_fill_price": None if exit_fill is None else _str_decimal(exit_fill.get("fill_price")),
        "exit_fee_quote": None if exit_fill is None else _str_decimal(exit_fill.get("fee_quote")),
        "total_fees_quote": _str_decimal(fees),
        "fees_over_approved_risk": _str_decimal(fee_risk),
        "fees_over_approved_notional": _str_decimal(fee_notional),
        "gross_realized_pnl_quote": _str_decimal(position.get("cumulative_gross_realized_pnl")),
        "net_realized_pnl_quote": _str_decimal(position.get("cumulative_net_realized_pnl")),
        "net_approved_risk_r": _str_decimal(branch.get("closed_net_r")),
        "remaining_base_quantity": _str_decimal(abs(quantity))
        if isinstance((quantity := position.get("quantity")), Decimal)
        else None,
        "all_fill_result_sha256": result_seal,
        "authority_path": authority_path,
        "authority_sha256": authority_sha,
    }


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _grouping_summary(
    grouped: dict[str, dict[str, dict[str, Counter[str]]]],
) -> dict[str, Any]:
    return {
        family: {
            dimension: {
                value: {
                    "observations": sum(counter.values()),
                    "candidate_or_admitted": sum(
                        counter[status] for status in TERMINAL_STATUSES if status != "NO_SIGNAL"
                    ),
                    "terminal_statuses": {status: counter[status] for status in TERMINAL_STATUSES},
                }
                for value, counter in sorted(values.items())
            }
            for dimension, values in sorted(dimensions.items())
        }
        for family, dimensions in sorted(grouped.items())
    }


def _range_summary(decisions: Iterable[dict[str, Any]]) -> dict[str, object]:
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    no_trade: Counter[str] = Counter()
    combinations: Counter[str] = Counter()
    selected_sides: Counter[str] = Counter()
    retained = reset = 0
    evaluated_failure_occurrences = 0
    for decision in decisions:
        facts = _decision_facts(decision)
        audit = facts["audit"]
        status = str(audit.get("range_status"))
        status_reasons = tuple(
            str(item) for item in _require_tuple(audit.get("range_reasons"), "range reasons")
        )
        statuses[status] += 1
        for reason in status_reasons:
            reasons[reason] += 1
        combinations[f"{status}|{'&'.join(status_reasons) if status_reasons else 'NONE'}"] += 1
        retained += audit.get("partial_evidence_retained") is True
        reset += audit.get("partial_evidence_reset") is True
        selected_sides[str(audit.get("selected_side") or "NONE")] += 1
        against = tuple(str(item) for item in facts["reasons_against"])
        for reason in against:
            no_trade[reason] += 1
        evaluated_failure_occurrences += len(against)
    return {
        "denominator_observations": sum(statuses.values()),
        "range_status_counts": _counter_dict(statuses),
        "range_reason_occurrences_multi_label": _counter_dict(reasons),
        "range_status_reason_combinations": _counter_dict(combinations),
        "strategy_no_trade_reason_occurrences_multi_label": _counter_dict(no_trade),
        "selected_side_counts": _counter_dict(selected_sides),
        "partial_evidence_retained_observations": retained,
        "partial_evidence_reset_observations": reset,
        "evaluated_failure_reason_occurrences": evaluated_failure_occurrences,
        "later_short_circuited_rules": "UNAVAILABLE_AS_PASS_FAIL_FACTS",
        "reason_counts_are_disjoint": False,
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise DiagnosisError("diagnostic table is unexpectedly empty")
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


def _decimal_delta(before: object, after: object) -> str | None:
    if before is None or after is None:
        return None
    return str(Decimal(str(after)) - Decimal(str(before)))


def _pair_rows(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, int], dict[str, dict[str, object]]] = defaultdict(dict)
    for row in trades:
        decision_index = row["decision_index"]
        if type(decision_index) is not int:
            raise DiagnosisError("trade decision index is not an integer")
        key = (str(row["task_id"]), decision_index)
        scenario = str(row["scenario"])
        if scenario in grouped[key]:
            raise DiagnosisError("duplicate trade scenario")
        grouped[key][scenario] = row
    output: list[dict[str, object]] = []
    for key in sorted(grouped):
        pair = grouped[key]
        if set(pair) != set(SCENARIOS):
            raise DiagnosisError("admitted decision lacks an exact BASE/STRESS pair")
        base, stress = (pair[item] for item in SCENARIOS)
        output.append(
            {
                "task_id": key[0],
                "decision_index": key[1],
                "decision_identity_sha256": base["decision_identity_sha256"],
                "family": base["family"],
                "symbol": base["symbol"],
                "base_status": base["status"],
                "stress_status": stress["status"],
                "base_entry_fill_at": base["entry_fill_at"],
                "stress_entry_fill_at": stress["entry_fill_at"],
                "entry_fill_time_delta_seconds": str(
                    (
                        datetime.fromisoformat(str(stress["entry_fill_at"]).replace("Z", "+00:00"))
                        - datetime.fromisoformat(str(base["entry_fill_at"]).replace("Z", "+00:00"))
                    ).total_seconds()
                ),
                "base_entry_reference_open": base["entry_reference_open_price"],
                "stress_entry_reference_open": stress["entry_reference_open_price"],
                "base_entry_fill_price": base["entry_fill_price"],
                "stress_entry_fill_price": stress["entry_fill_price"],
                "base_exit_fill_at": base["exit_fill_at"],
                "stress_exit_fill_at": stress["exit_fill_at"],
                "base_exit_reference_open": base["exit_reference_open_price"],
                "stress_exit_reference_open": stress["exit_reference_open_price"],
                "base_exit_fill_price": base["exit_fill_price"],
                "stress_exit_fill_price": stress["exit_fill_price"],
                "delta_gross_quote": _decimal_delta(
                    base["gross_realized_pnl_quote"], stress["gross_realized_pnl_quote"]
                ),
                "delta_fees_quote": _decimal_delta(
                    base["total_fees_quote"], stress["total_fees_quote"]
                ),
                "delta_net_quote": _decimal_delta(
                    base["net_realized_pnl_quote"], stress["net_realized_pnl_quote"]
                ),
                "delta_net_r": _decimal_delta(
                    base["net_approved_risk_r"], stress["net_approved_risk_r"]
                ),
                "base_entry_wait_seconds": base["entry_wait_seconds"],
                "stress_entry_wait_seconds": stress["entry_wait_seconds"],
                "base_exit_wait_seconds": base["exit_wait_seconds"],
                "stress_exit_wait_seconds": stress["exit_wait_seconds"],
                "entry_open_changed": base["entry_execution_candle_open_at"]
                != stress["entry_execution_candle_open_at"],
                "exit_open_changed": base["exit_execution_candle_open_at"]
                != stress["exit_execution_candle_open_at"],
                "base_authority_sha256": base["authority_sha256"],
                "stress_authority_sha256": stress["authority_sha256"],
            }
        )
    return output


def _summarize_pairs(rows: list[dict[str, object]]) -> dict[str, Any]:
    by_family: dict[str, dict[str, object]] = {}
    for family in FAMILIES:
        selected = [row for row in rows if row["family"] == family]
        by_family[family] = {
            "pairs": len(selected),
            "entry_open_changed": sum(row["entry_open_changed"] is True for row in selected),
            "exit_open_changed": sum(row["exit_open_changed"] is True for row in selected),
            "status_changed": sum(row["base_status"] != row["stress_status"] for row in selected),
            "delta_gross_quote": str(
                sum((Decimal(str(row["delta_gross_quote"])) for row in selected), Decimal(0))
            ),
            "delta_fees_quote": str(
                sum((Decimal(str(row["delta_fees_quote"])) for row in selected), Decimal(0))
            ),
            "delta_net_quote": str(
                sum((Decimal(str(row["delta_net_quote"])) for row in selected), Decimal(0))
            ),
            "configured_entry_latency_seconds": {"BASE": "0", "STRESS": "1"},
        }
    return by_family


def _decimal_stats(values: list[Decimal]) -> dict[str, str | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    with localcontext() as context:
        context.prec = 50
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
        )
        mean = sum(ordered, Decimal(0)) / Decimal(len(ordered))
    return {
        "count": len(ordered),
        "minimum": str(ordered[0]),
        "median": str(median),
        "mean": str(mean),
        "maximum": str(ordered[-1]),
    }


def _summarize_trades(rows: list[dict[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for family in FAMILIES:
        scenarios: dict[str, object] = {}
        for scenario in SCENARIOS:
            selected = [
                row for row in rows if row["family"] == family and row["scenario"] == scenario
            ]
            exits = Counter(str(row["primary_exit_reason"]) for row in selected)
            recovery = Counter(
                str(row["recovery_reasons"])
                for row in selected
                if row["recovery_reasons"] is not None
            )
            entry_waits = Counter(str(row["entry_wait_seconds"]) for row in selected)
            exit_waits = Counter(
                str(row["exit_wait_seconds"])
                for row in selected
                if row["exit_wait_seconds"] is not None
            )
            scenarios[scenario] = {
                "admitted_trade_rows": len(selected),
                "primary_status_counts": _counter_dict(
                    Counter(str(row["status"]) for row in selected)
                ),
                "primary_exit_reason_counts": _counter_dict(exits),
                "recovery_reason_counts": _counter_dict(recovery),
                "fees_over_approved_risk": _decimal_stats(
                    [Decimal(str(row["fees_over_approved_risk"])) for row in selected]
                ),
                "fees_over_approved_notional": _decimal_stats(
                    [Decimal(str(row["fees_over_approved_notional"])) for row in selected]
                ),
                "reference_stop_distance_ratio": _decimal_stats(
                    [Decimal(str(row["reference_stop_distance_ratio"])) for row in selected]
                ),
                "net_approved_risk_r": _decimal_stats(
                    [
                        Decimal(str(row["net_approved_risk_r"]))
                        for row in selected
                        if row["net_approved_risk_r"] is not None
                    ]
                ),
                "entry_effective_wait_seconds_counts": _counter_dict(entry_waits),
                "exit_effective_wait_seconds_counts": _counter_dict(exit_waits),
            }
        output[family] = scenarios
    return output


def _immutable_write(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def extract(repo: Path, output: Path) -> dict[str, object]:
    started = time.monotonic()
    repo = repo.resolve(strict=True)
    if output.exists() and any(output.iterdir()):
        raise DiagnosisError("diagnosis output already exists")
    evidence = (repo / EVIDENCE_RELATIVE).resolve(strict=True)
    files, _ = load_index(evidence)
    source_path = Path(__file__).resolve(strict=True)
    source_sha = _sha(source_path.read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": SCHEMA,
        "protocol": PROTOCOL,
        "post_result_descriptive": True,
        "historical_executable_sha": EXECUTABLE_SHA,
        "historical_run_uuid": RUN_UUID,
        "source_id": SOURCE_ID,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "artifact_index_sha256": INDEX_SHA256,
        "projection_sha256": PROJECTION_SHA256,
        "projection_integrity_sha256": PROJECTION_INTEGRITY,
        "utility_source_sha256": source_sha,
        "grouping_rules": ["family", "asset", "P1-P3", "side", "regime", "scenario"],
        "outputs": ["summary.json", "trades.csv", "pairs.csv", "manifest.json"],
    }
    _immutable_write(output / "plan.json", _canonical(plan))

    projection_rel = (LEDGER_RELATIVE / "projection.json").as_posix()
    projection_payload = verify_indexed_file(evidence, projection_rel, files)
    if _sha(projection_payload) != PROJECTION_SHA256:
        raise DiagnosisError("projection differs from accepted identity")
    projection = _require_dict(json.loads(projection_payload), "projection")
    if (
        projection.get("run_uuid") != RUN_UUID
        or projection.get("executable_sha") != EXECUTABLE_SHA
        or projection.get("protocol_sha256") != PROTOCOL_SHA256
        or projection.get("task_plan_sha256") != TASK_PLAN_SHA256
        or projection.get("projection_sha256") != PROJECTION_INTEGRITY
    ):
        raise DiagnosisError("projection identity differs")

    family_counts: dict[str, Counter[str]] = {family: Counter() for family in FAMILIES}
    family_actions: dict[str, Counter[str]] = {family: Counter() for family in FAMILIES}
    grouped: dict[str, dict[str, dict[str, Counter[str]]]] = {
        family: {
            dimension: defaultdict(Counter) for dimension in ("asset", "block", "side", "regime")
        }
        for family in FAMILIES
    }
    range_decisions: list[dict[str, Any]] = []
    trades: list[dict[str, object]] = []
    consumed: list[dict[str, object]] = []
    seen_task_ids: set[str] = set()
    for index in range(TASKS):
        number = f"{index:02d}"
        paths = {
            "population": (LEDGER_RELATIVE / f"{number}.population.json.gz").as_posix(),
            "partition": (LEDGER_RELATIVE / f"{number}.partition.json").as_posix(),
            "authority": (LEDGER_RELATIVE / f"{number}.authority.jsonl.gz").as_posix(),
        }
        payloads = {
            name: verify_indexed_file(evidence, path, files) for name, path in paths.items()
        }
        for name, path in paths.items():
            consumed.append(
                {"path": path, "sha256": _sha(payloads[name]), "size_bytes": len(payloads[name])}
            )
        _, decisions = _population(payloads["population"])
        partition = _require_dict(json.loads(payloads["partition"]), "partition")
        task = _require_dict(partition.get("task"), "partition task")
        task_id = task.get("task_id")
        task_family = task.get("family")
        if (
            not isinstance(task_id, str)
            or task_id in seen_task_ids
            or not isinstance(task_family, str)
        ):
            raise DiagnosisError("partition task identity is missing or duplicated")
        seen_task_ids.add(task_id)
        family = _family_name(task_family)
        if family not in FAMILIES or task.get("index") != index:
            raise DiagnosisError("partition task is misassigned")
        scenarios = _require_dict(partition.get("scenarios"), "partition scenarios")
        screens: dict[str, tuple[dict[str, Any], ...]] = {}
        for scenario in SCENARIOS:
            scenario_row = _require_dict(scenarios.get(scenario), "partition scenario")
            rows = tuple(
                _require_dict(item, "partition row") for item in scenario_row.get("rows", [])
            )
            if len(rows) != DECISIONS_PER_TASK:
                raise DiagnosisError("partition scenario is incomplete")
            screens[scenario] = rows
        for decision in decisions:
            facts = _decision_facts(decision)
            family_actions[family][str(facts["action"])] += 1
        if family == "RANGE_MEAN_REVERSION":
            range_decisions.extend(decisions)

        authority_rel = paths["authority"]
        authority_sha = _sha(payloads["authority"])
        slots: set[tuple[int, str]] = set()
        for record in _authority_records(payloads["authority"]):
            decision_index, scenario, result = (
                record["decision_index"],
                record["scenario"],
                record["result"],
            )
            expected_task_index = task.get("index")
            if type(expected_task_index) is not int:
                raise DiagnosisError("projection task index is not an integer")
            decision_index, scenario = validate_authority_slot(
                task_index=index,
                expected_task_index=expected_task_index,
                decision_index=decision_index,
                scenario=scenario,
                seen=slots,
            )
            if result.get("scenario") != scenario or result.get("family") != family:
                raise DiagnosisError("authority result is relabeled")
            screen = screens[str(scenario)][decision_index]
            status = str(result.get("status"))
            if screen.get("status") != status or screen.get("scenario") != scenario:
                raise DiagnosisError("partition terminal status differs from authority")
            if scenario == SCENARIOS[0]:
                family_counts[family][status] += 1
                for dimension, field in (
                    ("asset", "symbol"),
                    ("block", "block"),
                    ("side", "side"),
                    ("regime", "regime"),
                ):
                    grouped[family][dimension][str(screen.get(field))][status] += 1
            entry_intent = result.get("entry_intent")
            if entry_intent is not None:
                trades.append(
                    _trade_row(
                        task_id=task_id,
                        decision_index=decision_index,
                        decision=decisions[decision_index],
                        screen=screen,
                        result=result,
                        authority_path=authority_rel,
                        authority_sha=authority_sha,
                    )
                )
        if len(slots) != DECISIONS_PER_TASK * len(SCENARIOS):
            raise DiagnosisError("authority evidence has incomplete identity coverage")

    if len(seen_task_ids) != TASKS:
        raise DiagnosisError("task inventory is incomplete")
    pairs = _pair_rows(trades)
    funnel = {
        family: {
            "observations": sum(family_counts[family].values()),
            "actions": _counter_dict(family_actions[family]),
            "terminal_statuses_one_population": _counter_dict(family_counts[family]),
            "candidates": sum(
                value for key, value in family_actions[family].items() if key != "NO_TRADE"
            ),
            "risk_blocked": family_counts[family]["RISK_BLOCKED"],
            "safety_blocked": family_counts[family]["SAFETY_BLOCKED"],
            "admitted": sum(
                family_counts[family][key]
                for key in (
                    "ENTRY_UNFILLED",
                    "ORDINARY_CLOSED",
                    "ORDINARY_OPEN",
                    "RECOVERY_CLOSED",
                    "RECOVERY_OPEN",
                )
            ),
            "filled": sum(
                family_counts[family][key]
                for key in ("ORDINARY_CLOSED", "ORDINARY_OPEN", "RECOVERY_CLOSED", "RECOVERY_OPEN")
            ),
            "closed": family_counts[family]["ORDINARY_CLOSED"]
            + family_counts[family]["RECOVERY_CLOSED"],
            "unresolved": family_counts[family]["ORDINARY_OPEN"]
            + family_counts[family]["RECOVERY_OPEN"],
        }
        for family in FAMILIES
    }
    controls = {
        "TREND_PULLBACK": (229, 217),
        "BREAKOUT_RETEST": (29, 28),
        "RANGE_MEAN_REVERSION": (1, 1),
    }
    for family, (candidates, admitted) in controls.items():
        if (
            funnel[family]["observations"] != 60480
            or funnel[family]["candidates"] != candidates
            or funnel[family]["admitted"] != admitted
        ):
            raise DiagnosisError(f"{family} funnel differs from accepted reconciliation controls")
    summary = {
        "schema_version": SCHEMA,
        "protocol": PROTOCOL,
        "post_result_descriptive": True,
        "classifications_unchanged": {
            "TREND_PULLBACK": "NEGATIVE_EVIDENCE",
            "BREAKOUT_RETEST": "INSUFFICIENT_EVIDENCE",
            "RANGE_MEAN_REVERSION": "INSUFFICIENT_EVIDENCE",
        },
        "input_identities": plan,
        "consumed_artifacts": consumed,
        "reconciliation": {
            "tasks": len(seen_task_ids),
            "decisions": TASKS * DECISIONS_PER_TASK,
            "scenario_rows": TASKS * DECISIONS_PER_TASK * 2,
            "admitted_decisions": len(pairs),
            "trade_rows": len(trades),
        },
        "funnel": funnel,
        "predeclared_groupings_one_population": _grouping_summary(grouped),
        "range_diagnosis": _range_summary(range_decisions),
        "trade_economics": _summarize_trades(trades),
        "paired_execution": _summarize_pairs(pairs),
        "limitations": [
            "short-circuited later rules are unavailable as pass/fail facts",
            "multi-label reason counts overlap",
            "BASE and STRESS change costs and latency together",
            "the diagnosis is post-result and descriptive, not causal or independent replication",
        ],
    }
    _immutable_write(output / "summary.json", _canonical(summary))
    _write_csv(output / "trades.csv", trades)
    _write_csv(output / "pairs.csv", pairs)
    elapsed = Decimal(str(time.monotonic() - started)).quantize(Decimal("0.001"))
    consumed_bytes = 0
    for consumed_row in consumed:
        size_bytes = consumed_row["size_bytes"]
        if type(size_bytes) is not int:
            raise DiagnosisError("consumed file size is not an integer")
        consumed_bytes += size_bytes
    extraction = {
        "schema_version": SCHEMA,
        "elapsed_seconds": str(elapsed),
        "consumed_file_count": len(consumed) + 1,
        "consumed_file_bytes": consumed_bytes + len(projection_payload),
        "network_requests": 0,
        "strategy_feature_simulator_calls": 0,
    }
    _immutable_write(output / "extraction.json", _canonical(extraction))
    manifest_files = []
    for output_path in sorted(output.iterdir()):
        payload = output_path.read_bytes()
        manifest_files.append(
            {"path": output_path.name, "size_bytes": len(payload), "sha256": _sha(payload)}
        )
    manifest = {"schema_version": SCHEMA, "protocol": PROTOCOL, "files": manifest_files}
    _immutable_write(output / "manifest.json", _canonical(manifest))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = extract(args.repo, args.output)
    print(json.dumps(summary["reconciliation"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
