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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import Any

PROTOCOL = "RESEARCH_V2_RETAINED_DIAGNOSIS_V1"
SCHEMA = "research-v2-retained-diagnosis-v3"
SUPERSEDED_SCHEMA = "research-v2-retained-diagnosis-v2"
SUPERSEDED_MANIFEST_SHA256 = "98ee47e5dbaf803cf301a545c65300757007630c55a684b005ff28de8ffb2e4e"
V1_MANIFEST_SHA256 = "5092e2ceac9134f2feb1358dc50170a359dfdd70f9366854b932bda33f2c0526"
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
FAMILY_IDS = ("trend_pullback", "breakout_retest", "range_mean_reversion")
FAMILY_VERSIONS = {
    "trend_pullback": "1.0.0",
    "breakout_retest": "1.0.0",
    "range_mean_reversion": "2.0.0",
}
BLOCKS = ("P1", "P2", "P3")
SIDES = ("NONE", "LONG", "SHORT")
REGIMES = (
    "DOWNTREND",
    "INSUFFICIENT_DATA",
    "NEUTRAL",
    "STRONG_DOWNTREND",
    "STRONG_UPTREND",
    "UPTREND",
    "VOLATILE_UNCERTAIN",
)
DECISION_START = datetime(2026, 8, 3, tzinfo=UTC)
DECISION_END = datetime(2026, 10, 5, tzinfo=UTC)
FOLLOW_UP_END = datetime(2026, 10, 12, tzinfo=UTC)
DEFAULT_CONTEXT_START = datetime(2026, 7, 16, tzinfo=UTC)
CONTEXT_STARTS = {
    "BTCUSDT": datetime(2026, 7, 21, tzinfo=UTC),
    "BTWUSDT": datetime(2026, 7, 25, 12, tzinfo=UTC),
}
DESCRIPTIVE_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
ACCEPTED_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
HISTORICAL_QUANTITY_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
EXPECTED_COSTS = {
    SCENARIOS[0]: (Decimal("2"), Decimal("2"), Decimal("10"), timedelta()),
    SCENARIOS[1]: (
        Decimal("4"),
        Decimal("4"),
        Decimal("20"),
        timedelta(seconds=1),
    ),
}


@dataclass(frozen=True, slots=True)
class TaskExpectation:
    """Finite primitive contract for one retained task; no project constructor."""

    index: int
    task_id: str
    symbol: str
    family: str
    context_start: datetime
    decision_start: datetime
    decision_end: datetime
    follow_up_end: datetime
    decision_count: int
    input_kind: str
    source_id: str
    dataset_version: str
    manifest_sha256: str
    profile: str
    protocol_sha256: str
    task_plan_sha256: str


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


def _source_value(value: object, label: str) -> str:
    source = _require_dict(value, label)
    result = source.get("value")
    if not isinstance(result, str):
        raise DiagnosisError(f"{label} has no source value")
    return result


def _symbol_parts(value: object, label: str) -> tuple[str, str]:
    symbol = _require_dict(value, label)
    base, quote = symbol.get("base"), symbol.get("quote")
    if not isinstance(base, str) or not isinstance(quote, str):
        raise DiagnosisError(f"{label} is malformed")
    return base, quote


def _wire_symbol(value: object, label: str) -> str:
    return "".join(_symbol_parts(value, label))


def _task_expectation(index: int) -> TaskExpectation:
    if not 0 <= index < TASKS:
        raise DiagnosisError("task index is outside the frozen inventory")
    symbol = PANEL[index // len(FAMILY_IDS)]
    family = FAMILY_IDS[index % len(FAMILY_IDS)]
    return TaskExpectation(
        index=index,
        task_id=f"{index:02d}-{symbol}-{family}",
        symbol=symbol,
        family=family,
        context_start=CONTEXT_STARTS.get(symbol, DEFAULT_CONTEXT_START),
        decision_start=DECISION_START,
        decision_end=DECISION_END,
        follow_up_end=FOLLOW_UP_END,
        decision_count=DECISIONS_PER_TASK,
        input_kind="ACCEPTED_C2",
        source_id=SOURCE_ID,
        dataset_version=DATASET_VERSION,
        manifest_sha256=MANIFEST_SHA256,
        profile="NORMALIZED_BASE_SIGNAL_V1",
        protocol_sha256=PROTOCOL_SHA256,
        task_plan_sha256=TASK_PLAN_SHA256,
    )


def _identity_facts(identity_value: object, label: str) -> tuple[str, str, datetime, str, str]:
    identity = _require_dict(identity_value, label)
    observed_at = identity.get("observed_at")
    strategy_id = identity.get("strategy_id")
    strategy_version = identity.get("strategy_version")
    if (
        not isinstance(observed_at, datetime)
        or not isinstance(strategy_id, str)
        or not isinstance(strategy_version, str)
    ):
        raise DiagnosisError(f"{label} is malformed")
    return (
        _source_value(identity.get("source_id"), f"{label} source"),
        _wire_symbol(identity.get("symbol"), f"{label} symbol"),
        observed_at,
        strategy_id,
        strategy_version,
    )


def _identity_digest(identity_value: object, label: str) -> str:
    identity = _require_dict(identity_value, label)
    source, _, observed_at, strategy_id, strategy_version = _identity_facts(identity, label)
    base, quote = _symbol_parts(identity.get("symbol"), f"{label} symbol")
    display = (
        f"DecisionIdentity(source_id=SourceId(value={source!r}), "
        f"symbol=TradingSymbol(base={base!r}, quote={quote!r}), "
        f"observed_at={observed_at!r}, strategy_id={strategy_id!r}, "
        f"strategy_version={strategy_version!r})"
    )
    return _sha(_canonical(display))


def _decimal_sum(values: Iterable[Decimal]) -> Decimal:
    with localcontext(DESCRIPTIVE_CONTEXT):
        return sum(values, Decimal(0))


def _lossless_decimal_abs(value: Decimal) -> Decimal:
    """Return the stored magnitude without applying the ambient Decimal context."""

    return value.copy_abs()


def _historical_quantity_abs(value: Decimal) -> Decimal:
    """Reproduce #187 row serialization's default 28-digit quantity context."""

    with localcontext(HISTORICAL_QUANTITY_CONTEXT):
        return +value.copy_abs()


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
    delta = later - earlier
    microseconds = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    with localcontext(DESCRIPTIVE_CONTEXT):
        rendered = format(Decimal(microseconds) / Decimal(1_000_000), "f")
    return rendered if "." in rendered else f"{rendered}.0"


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


def _population(
    payload: bytes, *, expected_count: int = DECISIONS_PER_TASK
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
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
    if len(decisions) != expected_count:
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
        if (
            _canonical(raw) != line
            or raw.get("schema_version") != "research-v2-screen-authority-evidence-v1"
        ):
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


def _parse_iso(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise DiagnosisError(f"{label} is not an ISO timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiagnosisError(f"{label} is not an ISO timestamp") from exc
    if result.utcoffset() != timedelta(0):
        raise DiagnosisError(f"{label} is not UTC")
    return result


def _parse_partition(payload: bytes) -> dict[str, Any]:
    try:
        partition = _require_dict(json.loads(payload), "partition")
    except json.JSONDecodeError as exc:
        raise DiagnosisError("partition evidence is unavailable") from exc
    if _canonical(partition) != payload:
        raise DiagnosisError("partition evidence is not canonical")
    asserted = partition.get("partition_sha256")
    unsealed = dict(partition)
    unsealed.pop("partition_sha256", None)
    if not isinstance(asserted, str) or asserted != _sha(_canonical(unsealed)):
        raise DiagnosisError("partition integrity identity differs")
    return partition


def _validate_population_task(
    population: dict[str, Any],
    decisions: tuple[dict[str, Any], ...],
    partition: dict[str, Any],
    expected: TaskExpectation,
) -> None:
    task = _require_dict(partition.get("task"), "partition task")
    expected_task = {
        "context_start": _iso(expected.context_start),
        "decision_end": _iso(expected.decision_end),
        "decision_slots": expected.decision_count,
        "decision_start": _iso(expected.decision_start),
        "family": expected.family,
        "index": expected.index,
        "last_follow_up_end": _iso(expected.follow_up_end),
        "scenario_rows": expected.decision_count * len(SCENARIOS),
        "symbol": expected.symbol,
        "task_id": expected.task_id,
    }
    configuration = _require_dict(population.get("configuration"), "population configuration")
    bounds = _require_dict(configuration.get("bounds"), "population bounds")
    binding = _require_dict(configuration.get("input_binding"), "population input binding")
    population_source = _source_value(population.get("source_id"), "population source")
    if (
        task != expected_task
        or partition.get("schema_version") != "research-v2-exploratory-screen-v4"
        or partition.get("protocol_sha256") != expected.protocol_sha256
        or partition.get("task_plan_sha256") != expected.task_plan_sha256
        or partition.get("source_id") != expected.source_id
        or partition.get("dataset_version") != expected.dataset_version
        or partition.get("manifest_sha256") != expected.manifest_sha256
        or partition.get("population_decision_count") != expected.decision_count
        or population.get("schema_version") != "research-v2-study-decision-population-v1"
        or population.get("protocol_sha256") != expected.protocol_sha256
        or population_source != expected.source_id
        or population.get("dataset_version") != expected.dataset_version
        or population.get("manifest_sha256") != expected.manifest_sha256
        or population.get("profile") != expected.profile
        or configuration.get("family") != expected.family
        or configuration.get("schema_version") != "research-v2-admission-configuration-v2"
        or binding.get("kind") != expected.input_kind
        or _source_value(binding.get("source_id"), "input binding source") != expected.source_id
        or binding.get("dataset_version") != expected.dataset_version
        or binding.get("manifest_sha256") != expected.manifest_sha256
        or binding.get("schema_version") != "research-v2-input-binding-v1"
        or _wire_symbol(bounds.get("symbol"), "population bounds symbol") != expected.symbol
        or _source_value(bounds.get("source_id"), "population bounds source") != expected.source_id
        or bounds.get("context_start") != expected.context_start
        or bounds.get("decision_start") != expected.decision_start
        or bounds.get("decision_end") != expected.decision_end
        or bounds.get("follow_up_end") != expected.follow_up_end
        or bounds.get("profile") != expected.profile
        or bounds.get("admission_version") != "RESEARCH_V2_ISOLATED_ADMISSION_V1"
        or partition.get("population_sha256") != population.get("integrity_seal")
    ):
        raise DiagnosisError("population, partition, task, or accepted provenance differs")
    if len(decisions) != expected.decision_count:
        raise DiagnosisError("population decision count differs")
    for index, decision in enumerate(decisions):
        identity = decision.get("identity")
        source, symbol, observed_at, strategy_id, strategy_version = _identity_facts(
            identity, "population decision identity"
        )
        expected_observed_at = expected.decision_start + timedelta(minutes=15 * (index + 1))
        context = _require_dict(decision.get("context"), "decision context")
        strategy = _require_dict(decision.get("strategy_evaluation"), "strategy evaluation")
        if (
            source != expected.source_id
            or symbol != expected.symbol
            or observed_at != expected_observed_at
            or strategy_id != expected.family
            or strategy_version != FAMILY_VERSIONS[expected.family]
            or _source_value(context.get("source_id"), "decision context source") != source
            or _wire_symbol(context.get("symbol"), "decision context symbol") != symbol
            or context.get("observed_at") != observed_at
            or _source_value(strategy.get("source_id"), "strategy source") != source
            or _wire_symbol(strategy.get("symbol"), "strategy symbol") != symbol
            or strategy.get("observed_at") != observed_at
            or strategy.get("strategy_id") != strategy_id
            or strategy.get("strategy_version") != strategy_version
        ):
            raise DiagnosisError("population decision chronology or logical identity differs")


def _expected_side(decision: dict[str, Any]) -> tuple[str, str | None]:
    action = _require_dict(decision.get("strategy_evaluation"), "strategy evaluation").get("action")
    if action == "LONG_CANDIDATE":
        return "LONG", "BUY"
    if action == "SHORT_CANDIDATE":
        return "SHORT", "SELL"
    if action == "NO_TRADE":
        return "NONE", None
    raise DiagnosisError("decision action is unknown")


def _screen_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiagnosisError("partition Decimal fact is malformed")
    try:
        result = Decimal(value)
    except ValueError as exc:
        raise DiagnosisError("partition Decimal fact is malformed") from exc
    if not result.is_finite():
        raise DiagnosisError("partition Decimal fact is non-finite")
    return result


def _branch_screen_facts(branch: dict[str, Any] | None) -> tuple[object, ...]:
    if branch is None:
        return (None, Decimal(0), Decimal(0), None, None)
    position = _require_dict(branch.get("final_position"), "branch final position")
    quantity = position.get("quantity")
    fees = position.get("cumulative_fees")
    gross = position.get("cumulative_gross_realized_pnl")
    net = position.get("cumulative_net_realized_pnl")
    if not all(isinstance(value, Decimal) for value in (quantity, fees, gross, net)):
        raise DiagnosisError("branch accounting facts are not exact Decimals")
    assert isinstance(quantity, Decimal)
    return (
        branch.get("closed_net_r"),
        fees,
        _historical_quantity_abs(quantity),
        gross,
        net,
    )


def _validate_screen_result(
    *,
    expected: TaskExpectation,
    decision_index: int,
    decision: dict[str, Any],
    scenario: str,
    screen: dict[str, Any],
    result: dict[str, Any],
) -> None:
    identity = decision.get("identity")
    source, symbol, observed_at, strategy_id, strategy_version = _identity_facts(
        identity, "decision identity"
    )
    side, order_side = _expected_side(decision)
    expected_family = expected.family.upper()
    entry_intent_raw = result.get("entry_intent")
    entry_intent = (
        None if entry_intent_raw is None else _require_dict(entry_intent_raw, "result entry intent")
    )
    branches = tuple(
        _require_dict(item, "all-fill branch")
        for item in _require_tuple(result.get("branches"), "result branches")
    )
    expected_start = expected.context_start if expected.family == "trend_pullback" else observed_at
    series = _require_dict(result.get("series"), "result series")
    costs = _require_dict(result.get("costs"), "result costs")
    expected_costs = EXPECTED_COSTS[scenario]
    if (
        result.get("decision") != {"__reference__": "decision"}
        or result.get("family") != expected_family
        or result.get("scenario") != scenario
        or result.get("start") != expected_start
        or result.get("end") != observed_at + timedelta(hours=168)
        or result.get("schema_version") != "research-all-fill-economics-v1"
        or result.get("policy_version") != "RESEARCH_ALL_FILL_ECONOMICS_V1"
        or result.get("is_trading_authority") is not False
        or _source_value(series.get("source_id"), "result series source") != source
        or _wire_symbol(series.get("symbol"), "result series symbol") != symbol
        or series.get("timeframe") != "15m"
        or (
            costs.get("spread_bps"),
            costs.get("slippage_bps"),
            costs.get("fee_bps"),
            costs.get("latency"),
        )
        != expected_costs
    ):
        raise DiagnosisError("all-fill result has incompatible task or scenario provenance")
    seal = result.get("integrity_seal")
    if not isinstance(seal, str) or len(seal) != 64 or screen.get("all_fill_result_sha256") != seal:
        raise DiagnosisError("partition/result integrity identity differs")
    if (
        screen.get("identity") != _identity_digest(identity, "decision identity")
        or _parse_iso(screen.get("observed_at"), "partition observed_at") != observed_at
        or screen.get("symbol") != symbol
        or screen.get("family") != expected_family
        or screen.get("scenario") != scenario
        or screen.get("source_id") != source
        or screen.get("dataset_version") != expected.dataset_version
        or screen.get("manifest_sha256") != expected.manifest_sha256
        or screen.get("input_kind") != expected.input_kind
        or screen.get("side") != side
        or screen.get("status") != result.get("status")
        or screen.get("research_admissible") is not (entry_intent is not None)
    ):
        raise DiagnosisError("partition row differs from decision or all-fill identity")
    if entry_intent is None:
        if branches or result.get("entry_unfilled") is not None:
            raise DiagnosisError("non-admitted result contains simulated evidence")
    else:
        entry_identity = _require_dict(entry_intent.get("identity"), "entry intent identity")
        if (
            entry_intent.get("decision_identity") != identity
            or entry_identity.get("decision_identity") != identity
            or entry_identity.get("purpose") != "ENTRY"
            or _source_value(entry_intent.get("source_id"), "entry intent source") != source
            or _wire_symbol(entry_intent.get("symbol"), "entry intent symbol") != symbol
            or entry_intent.get("observed_at") != observed_at
            or entry_intent.get("strategy_id") != strategy_id
            or entry_intent.get("strategy_version") != strategy_version
            or entry_intent.get("side") != order_side
            or entry_intent.get("authorized_for_execution") is not True
        ):
            raise DiagnosisError("entry intent differs from population decision identity")
        if not branches and (
            result.get("status") != "ENTRY_UNFILLED" or result.get("entry_unfilled") is None
        ):
            raise DiagnosisError("admitted result has no accounting branch or unfilled status")
        if branches and result.get("entry_unfilled") is not None:
            raise DiagnosisError("filled result also contains entry-unfilled evidence")
    primary = branches[-1] if branches else None
    if len(branches) > 2 or (primary is not None and primary.get("status") != result.get("status")):
        raise DiagnosisError("branch topology or terminal status differs")
    closed_r, fees, open_quantity, gross, net = _branch_screen_facts(primary)
    if (
        _screen_decimal(screen.get("primary_closed_net_r")) != closed_r
        or _screen_decimal(screen.get("primary_fee_quote")) != fees
        or _screen_decimal(screen.get("primary_open_quantity")) != open_quantity
        or _screen_decimal(screen.get("primary_gross_pnl_quote")) != gross
        or _screen_decimal(screen.get("primary_net_pnl_quote")) != net
    ):
        raise DiagnosisError("partition primary accounting facts differ from authority")
    hold = branches[0] if len(branches) == 2 else None
    hold_r, hold_fees, hold_open, _, _ = _branch_screen_facts(hold)
    if (
        screen.get("hold_only_status") != (None if hold is None else hold.get("status"))
        or _screen_decimal(screen.get("hold_only_closed_net_r")) != hold_r
        or _screen_decimal(screen.get("hold_only_fee_quote"))
        != (None if hold is None else hold_fees)
        or _screen_decimal(screen.get("hold_only_open_quantity"))
        != (None if hold is None else hold_open)
        or screen.get("hold_only_branch_sha256")
        != (None if hold is None else hold.get("integrity_seal"))
    ):
        raise DiagnosisError("partition hold-only facts differ from authority")


def extract_task_payloads(
    *,
    expected: TaskExpectation,
    population_payload: bytes,
    partition_payload: bytes,
    authority_payload: bytes,
    authority_path: str,
    authority_sha: str,
) -> dict[str, Any]:
    """Validate and project one task through the same reusable extraction core."""

    population, decisions = _population(population_payload, expected_count=expected.decision_count)
    partition = _parse_partition(partition_payload)
    _validate_population_task(population, decisions, partition, expected)
    scenarios = _require_dict(partition.get("scenarios"), "partition scenarios")
    if set(scenarios) != set(SCENARIOS):
        raise DiagnosisError("partition scenarios differ from the frozen pair")
    screens: dict[str, tuple[dict[str, Any], ...]] = {}
    for scenario in SCENARIOS:
        scenario_row = _require_dict(scenarios.get(scenario), "partition scenario")
        raw_rows = scenario_row.get("rows")
        if not isinstance(raw_rows, list):
            raise DiagnosisError("partition rows are not a JSON array")
        rows = tuple(_require_dict(item, "partition row") for item in raw_rows)
        if len(rows) != expected.decision_count:
            raise DiagnosisError("partition scenario is incomplete")
        screens[scenario] = rows

    slots: set[tuple[int, str]] = set()
    trades: list[dict[str, object]] = []
    base_counts: Counter[str] = Counter()
    for record in _authority_records(authority_payload):
        decision_index = record["decision_index"]
        scenario = record["scenario"]
        if type(decision_index) is not int or not 0 <= decision_index < expected.decision_count:
            raise DiagnosisError("authority decision index is outside population")
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise DiagnosisError("authority scenario is unknown")
        slot = (decision_index, scenario)
        if slot in slots:
            raise DiagnosisError("authority scenario slot is duplicated")
        slots.add(slot)
        result = _require_dict(record["result"], "all-fill result")
        screen = screens[scenario][decision_index]
        decision = decisions[decision_index]
        _validate_screen_result(
            expected=expected,
            decision_index=decision_index,
            decision=decision,
            scenario=scenario,
            screen=screen,
            result=result,
        )
        status = result.get("status")
        if not isinstance(status, str) or status not in TERMINAL_STATUSES:
            raise DiagnosisError("authority terminal status is unknown")
        if scenario == SCENARIOS[0]:
            base_counts[status] += 1
        if result.get("entry_intent") is not None:
            trades.append(
                _trade_row(
                    task_id=expected.task_id,
                    decision_index=decision_index,
                    decision=decision,
                    screen=screen,
                    result=result,
                    authority_path=authority_path,
                    authority_sha=authority_sha,
                )
            )
    required = expected.decision_count * len(SCENARIOS)
    if len(slots) != required:
        raise DiagnosisError("authority evidence has incomplete identity coverage")
    return {
        "population": population,
        "decisions": decisions,
        "partition": partition,
        "screens": screens,
        "base_counts": base_counts,
        "trades": trades,
    }


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
    branch = branches[-1] if branches else None
    entry = None if branch is None else _require_dict(branch.get("entry_fill"), "entry fill")
    if branch is None and result.get("status") != "ENTRY_UNFILLED":
        raise DiagnosisError("admitted result lacks an accounting branch")
    exit_fill_raw = None if branch is None else branch.get("exit_fill")
    exit_fill = None if exit_fill_raw is None else _require_dict(exit_fill_raw, "exit fill")
    exit_request_raw = None if branch is None else branch.get("exit_request")
    exit_request = (
        None if exit_request_raw is None else _require_dict(exit_request_raw, "exit request")
    )
    position = (
        None if branch is None else _require_dict(branch.get("final_position"), "final position")
    )
    legacy = _require_dict(result.get("legacy_replay"), "legacy replay")
    recovery_raw = None if branch is None else branch.get("recovery")
    recovery = None if recovery_raw is None else _require_dict(recovery_raw, "recovery")
    reference = entry_intent.get("reference_price")
    stop = entry_intent.get("stop_basis")
    approved_risk = entry_intent.get("approved_risk_quote")
    approved_notional = entry_intent.get("approved_notional_quote")
    fees = None if position is None else position.get("cumulative_fees")
    if not all(
        isinstance(value, Decimal) for value in (reference, stop, approved_risk, approved_notional)
    ):
        raise DiagnosisError("trade economics are not exact Decimal evidence")
    assert isinstance(reference, Decimal)
    assert isinstance(stop, Decimal)
    assert isinstance(approved_risk, Decimal)
    assert isinstance(approved_notional, Decimal)
    if fees is not None and not isinstance(fees, Decimal):
        raise DiagnosisError("trade fees are not exact Decimal evidence")
    with localcontext(DESCRIPTIVE_CONTEXT):
        geometry = abs(reference - stop) / reference
        fee_risk = None if fees is None else fees / approved_risk
        fee_notional = None if fees is None else fees / approved_notional
    source, _symbol, observed_at, strategy_id, strategy_version = _identity_facts(
        decision.get("identity"), "trade decision identity"
    )
    result_seal = result.get("integrity_seal")
    if result_seal != screen.get("all_fill_result_sha256"):
        raise DiagnosisError("partition/result integrity identity differs")
    return {
        "task_id": task_id,
        "decision_index": decision_index,
        "decision_identity_sha256": screen.get("identity"),
        "decision_source_id": source,
        "decision_observed_at": _iso(observed_at),
        "decision_strategy_id": strategy_id,
        "decision_strategy_version": strategy_version,
        "input_kind": screen.get("input_kind"),
        "dataset_version": screen.get("dataset_version"),
        "manifest_sha256": screen.get("manifest_sha256"),
        "scenario": result.get("scenario"),
        "family": result.get("family"),
        "symbol": screen.get("symbol"),
        "block": screen.get("block"),
        "week": screen.get("week"),
        "regime": screen.get("regime"),
        "side": entry_intent.get("side"),
        "status": result.get("status") if branch is None else branch.get("status"),
        "primary_exit_category": None if branch is None else branch.get("label"),
        "primary_exit_reason": None
        if branch is None
        else _exit_reason(str(result.get("family")), legacy, branch),
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
        "entry_created_at": None if entry is None else _iso(entry.get("created_at")),
        "entry_eligible_at": None if entry is None else _iso(entry.get("eligible_at")),
        "entry_execution_candle_open_at": None
        if entry is None
        else _iso(entry.get("execution_candle_open_time")),
        "entry_fill_at": None if entry is None else _iso(entry.get("fill_at")),
        "entry_wait_seconds": None
        if entry is None
        else _seconds(entry.get("fill_at"), entry.get("created_at")),
        "entry_configured_latency_seconds": _seconds(
            None if entry is None else entry.get("eligible_at"),
            None if entry is None else entry.get("created_at"),
        ),
        "entry_post_eligibility_wait_seconds": None
        if entry is None
        else _seconds(entry.get("execution_candle_open_time"), entry.get("eligible_at")),
        "entry_reference_open_price": None
        if entry is None
        else _str_decimal(entry.get("reference_open_price")),
        "entry_fill_price": None if entry is None else _str_decimal(entry.get("fill_price")),
        "entry_fee_quote": None if entry is None else _str_decimal(entry.get("fee_quote")),
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
        "gross_realized_pnl_quote": None
        if position is None
        else _str_decimal(position.get("cumulative_gross_realized_pnl")),
        "net_realized_pnl_quote": None
        if position is None
        else _str_decimal(position.get("cumulative_net_realized_pnl")),
        "net_approved_risk_r": None if branch is None else _str_decimal(branch.get("closed_net_r")),
        "remaining_base_quantity": _str_decimal(_lossless_decimal_abs(quantity))
        if position is not None and isinstance((quantity := position.get("quantity")), Decimal)
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
                    "candidate_terminal_count": sum(
                        counter[status] for status in TERMINAL_STATUSES if status != "NO_SIGNAL"
                    ),
                    "admitted": sum(
                        counter[status]
                        for status in (
                            "ENTRY_UNFILLED",
                            "ORDINARY_CLOSED",
                            "ORDINARY_OPEN",
                            "RECOVERY_CLOSED",
                            "RECOVERY_OPEN",
                        )
                    ),
                    "terminal_statuses": {status: counter[status] for status in TERMINAL_STATUSES},
                }
                for value, counter in sorted(values.items())
            }
            for dimension, values in sorted(dimensions.items())
        }
        for family, dimensions in sorted(grouped.items())
    }


def _new_grouped() -> dict[str, dict[str, dict[str, Counter[str]]]]:
    values = {
        "asset": PANEL,
        "block": BLOCKS,
        "side": SIDES,
        "regime": REGIMES,
    }
    return {
        family: {
            dimension: {value: Counter() for value in dimension_values}
            for dimension, dimension_values in values.items()
        }
        for family in FAMILIES
    }


def _range_summary(decisions: Iterable[dict[str, Any]]) -> dict[str, object]:
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    no_trade: Counter[str] = Counter()
    combinations: Counter[str] = Counter()
    selected_sides: Counter[str] = Counter()
    retained = reset = 0
    evaluated_failure_occurrences = 0
    audit_fields: dict[str, Counter[str]] = {
        field: Counter()
        for field in (
            "lower_boundary",
            "upper_boundary",
            "midpoint",
            "range_width",
            "width_in_atr",
            "current_atr",
            "lower_entry_zone_ceiling",
            "upper_entry_zone_floor",
            "lower_excursion_floor",
            "upper_excursion_ceiling",
            "candle_close",
            "candle_high",
            "candle_low",
            "rsi_14",
            "momentum_4",
            "invalidation_reference",
            "invalidation_distance",
        )
    }
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
        for field, availability in audit_fields.items():
            availability["PRESENT" if audit.get(field) is not None else "UNAVAILABLE"] += 1
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
        "audit_field_availability": {
            field: _counter_dict(counts) for field, counts in sorted(audit_fields.items())
        },
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
    with localcontext(DESCRIPTIVE_CONTEXT):
        return str(Decimal(str(after)) - Decimal(str(before)))


def _timestamp_delta(before: object, after: object) -> str | None:
    if before is None or after is None:
        return None
    return _seconds(
        datetime.fromisoformat(str(after).replace("Z", "+00:00")),
        datetime.fromisoformat(str(before).replace("Z", "+00:00")),
    )


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
        identity_fields = (
            "task_id",
            "decision_index",
            "decision_identity_sha256",
            "decision_source_id",
            "decision_observed_at",
            "decision_strategy_id",
            "decision_strategy_version",
            "input_kind",
            "dataset_version",
            "manifest_sha256",
            "family",
            "symbol",
            "block",
            "week",
            "regime",
            "side",
            "reference_price",
            "stop_basis",
            "quantity",
            "approved_notional_quote",
            "approved_risk_quote",
        )
        if any(base.get(field) != stress.get(field) for field in identity_fields):
            raise DiagnosisError("BASE/STRESS rows do not share exact decision authority")
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
                "entry_fill_time_delta_seconds": _timestamp_delta(
                    base["entry_fill_at"], stress["entry_fill_at"]
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
                _decimal_sum(
                    Decimal(str(row["delta_gross_quote"]))
                    for row in selected
                    if row["delta_gross_quote"] is not None
                )
            ),
            "delta_fees_quote": str(
                _decimal_sum(
                    Decimal(str(row["delta_fees_quote"]))
                    for row in selected
                    if row["delta_fees_quote"] is not None
                )
            ),
            "delta_net_quote": str(
                _decimal_sum(
                    Decimal(str(row["delta_net_quote"]))
                    for row in selected
                    if row["delta_net_quote"] is not None
                )
            ),
            "configured_entry_latency_seconds": {"BASE": "0", "STRESS": "1"},
            "descriptive_decimal_context": {
                "precision": DESCRIPTIVE_CONTEXT.prec,
                "rounding": str(DESCRIPTIVE_CONTEXT.rounding),
            },
        }
    return by_family


def _hold_only_projection(projection: dict[str, Any]) -> dict[str, Any]:
    families = _require_dict(projection.get("families"), "accepted projection families")
    output: dict[str, Any] = {}
    for family in FAMILIES:
        stored = _require_dict(families.get(family.lower()), "accepted family projection")
        output[family] = {}
        for label, key in (("BASE", "base"), ("STRESS", "combined_stress")):
            scenario = _require_dict(stored.get(key), "accepted scenario projection")
            hold = _require_dict(
                scenario.get("hold_only_diagnostic"), "accepted hold-only diagnostic"
            )
            residuals = _require_dict(
                hold.get("remaining_absolute_base_quantity_by_asset"),
                "hold-only residual quantities",
            )
            if set(residuals) != set(PANEL) or any(
                not isinstance(value, str) for value in residuals.values()
            ):
                raise DiagnosisError("hold-only residual quantities differ from the panel")
            output[family][label] = {
                "branches": hold.get("branches"),
                "closed": hold.get("closed"),
                "unresolved": hold.get("unresolved"),
                "incurred_fees_quote": hold.get("incurred_fees_quote"),
                "remaining_absolute_base_quantity_by_asset": residuals,
                "source_projection_sha256": PROJECTION_SHA256,
            }
    return output


def _projection_reconciliation(
    projection: dict[str, Any], pair_summary: dict[str, Any]
) -> dict[str, Any]:
    families = _require_dict(projection.get("families"), "accepted projection families")
    output: dict[str, Any] = {}
    fields = {
        "gross": "impact_inclusive_gross_realized_quote",
        "fees": "incurred_fees_quote",
        "net": "accounted_net_realized_quote",
    }
    for family in FAMILIES:
        stored = _require_dict(families.get(family.lower()), "accepted family projection")
        base = _require_dict(stored.get("base"), "accepted BASE projection")
        stress = _require_dict(stored.get("combined_stress"), "accepted STRESS projection")
        accepted: dict[str, str] = {}
        descriptive: dict[str, str] = {}
        residuals: dict[str, str] = {}
        for short, field in fields.items():
            with localcontext(ACCEPTED_CONTEXT):
                accepted_value = Decimal(str(stress.get(field))) - Decimal(str(base.get(field)))
            descriptive_value = Decimal(str(pair_summary[family][f"delta_{short}_quote"]))
            with localcontext(DESCRIPTIVE_CONTEXT):
                residual = descriptive_value - accepted_value
            accepted[short] = str(accepted_value)
            descriptive[short] = str(descriptive_value)
            residuals[short] = str(residual)
        with localcontext(DESCRIPTIVE_CONTEXT):
            descriptive_bridge = Decimal(descriptive["net"]) - (
                Decimal(descriptive["gross"]) - Decimal(descriptive["fees"])
            )
        with localcontext(ACCEPTED_CONTEXT):
            accepted_bridge = Decimal(accepted["net"]) - (
                Decimal(accepted["gross"]) - Decimal(accepted["fees"])
            )
        output[family] = {
            "accepted_projection_delta": accepted,
            "descriptive_row_delta": descriptive,
            "descriptive_minus_accepted": residuals,
            "accepted_bridge_rounding_residual": str(accepted_bridge),
            "descriptive_bridge_rounding_residual": str(descriptive_bridge),
            "accepted_projection_context": {
                "precision": ACCEPTED_CONTEXT.prec,
                "rounding": str(ACCEPTED_CONTEXT.rounding),
            },
            "descriptive_context": {
                "precision": DESCRIPTIVE_CONTEXT.prec,
                "rounding": str(DESCRIPTIVE_CONTEXT.rounding),
            },
            "interpretation": "ROUNDING_RECONCILED_NOT_ASSERTED_AS_EXACT_EQUALITY",
        }
    return output


def _decimal_stats(values: list[Decimal]) -> dict[str, str | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    with localcontext(DESCRIPTIVE_CONTEXT):
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
                    [
                        Decimal(str(row["fees_over_approved_risk"]))
                        for row in selected
                        if row["fees_over_approved_risk"] is not None
                    ]
                ),
                "fees_over_approved_notional": _decimal_stats(
                    [
                        Decimal(str(row["fees_over_approved_notional"]))
                        for row in selected
                        if row["fees_over_approved_notional"] is not None
                    ]
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
        "supersedes": {
            "schema_version": SUPERSEDED_SCHEMA,
            "manifest_sha256": SUPERSEDED_MANIFEST_SHA256,
            "reason": "QUANTITY_CONTEXT_AND_FILLED_PATH_COVERAGE_CORRECTION",
        },
        "decimal_policies": {
            "descriptive": {
                "precision": DESCRIPTIVE_CONTEXT.prec,
                "rounding": str(DESCRIPTIVE_CONTEXT.rounding),
            },
            "accepted_projection": {
                "precision": ACCEPTED_CONTEXT.prec,
                "rounding": str(ACCEPTED_CONTEXT.rounding),
            },
            "quantity_output": "LOSSLESS_DECIMAL_COPY_ABS",
            "historical_quantity_compatibility": {
                "operation": "COPY_ABS_THEN_UNARY_PLUS",
                "precision": HISTORICAL_QUANTITY_CONTEXT.prec,
                "rounding": str(HISTORICAL_QUANTITY_CONTEXT.rounding),
            },
        },
        "grouping_rules": ["family", "asset", "P1-P3", "side", "regime", "scenario"],
        "outputs": [
            "summary.json",
            "trades.csv",
            "pairs.csv",
            "hold-only.json",
            "reconciliation.json",
            "manifest.json",
        ],
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

    task_artifacts_raw = projection.get("task_artifacts")
    if not isinstance(task_artifacts_raw, list) or len(task_artifacts_raw) != TASKS:
        raise DiagnosisError("accepted projection task inventory differs")
    task_artifacts = {
        str(_require_dict(item, "projection task artifact").get("task_id")): _require_dict(
            item, "projection task artifact"
        )
        for item in task_artifacts_raw
    }
    if len(task_artifacts) != TASKS:
        raise DiagnosisError("accepted projection task identity is duplicated")
    family_counts: dict[str, Counter[str]] = {family: Counter() for family in FAMILIES}
    family_actions: dict[str, Counter[str]] = {family: Counter() for family in FAMILIES}
    grouped = _new_grouped()
    range_decisions: list[dict[str, Any]] = []
    trades: list[dict[str, object]] = []
    consumed: list[dict[str, object]] = []
    seen_task_ids: set[str] = set()
    for index in range(TASKS):
        expected = _task_expectation(index)
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
        projected = task_artifacts.get(expected.task_id)
        if projected is None or any(
            projected.get(f"{name}_sha256") != _sha(payloads[name])
            for name in ("population", "partition", "authority")
        ):
            raise DiagnosisError("task artifact bytes differ from accepted projection identity")
        task_result = extract_task_payloads(
            expected=expected,
            population_payload=payloads["population"],
            partition_payload=payloads["partition"],
            authority_payload=payloads["authority"],
            authority_path=paths["authority"],
            authority_sha=_sha(payloads["authority"]),
        )
        decisions = task_result["decisions"]
        screens = task_result["screens"]
        seen_task_ids.add(expected.task_id)
        family = expected.family.upper()
        family_counts[family].update(task_result["base_counts"])
        trades.extend(task_result["trades"])
        for decision in decisions:
            facts = _decision_facts(decision)
            family_actions[family][str(facts["action"])] += 1
        if family == "RANGE_MEAN_REVERSION":
            range_decisions.extend(decisions)
        for screen in screens[SCENARIOS[0]]:
            status = str(screen.get("status"))
            for dimension, field in (
                ("asset", "symbol"),
                ("block", "block"),
                ("side", "side"),
                ("regime", "regime"),
            ):
                value = str(screen.get(field))
                if value not in grouped[family][dimension]:
                    raise DiagnosisError("partition grouping value is outside the frozen domain")
                grouped[family][dimension][value][status] += 1

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
    paired_summary = _summarize_pairs(pairs)
    hold_only = _hold_only_projection(projection)
    projection_reconciliation = _projection_reconciliation(projection, paired_summary)
    correction = {
        "schema_version": SCHEMA,
        "superseded_schema_version": SUPERSEDED_SCHEMA,
        "superseded_manifest_sha256": SUPERSEDED_MANIFEST_SHA256,
        "preserved_v1_manifest_sha256": V1_MANIFEST_SHA256,
        "preserved_input_index_sha256": INDEX_SHA256,
        "preserved_projection_sha256": PROJECTION_SHA256,
        "corrections": [
            "ACTUAL_DECISION_TASK_SOURCE_IDENTITY_BINDING",
            "CONTEXT_INDEPENDENT_DECIMAL_DERIVATION",
            "ROUNDING_RECONCILIATION_WITHOUT_EXACT_EQUALITY_CLAIM",
            "ENTRY_UNFILLED_OPEN_HOLD_ONLY_AND_ZERO_CELL_COVERAGE",
            "LOSSLESS_QUANTITY_OUTPUT_WITH_EXPLICIT_HISTORICAL_COMPATIBILITY",
        ],
    }
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
        "predeclared_groupings_base_population": _grouping_summary(grouped),
        "range_diagnosis": _range_summary(range_decisions),
        "trade_economics": _summarize_trades(trades),
        "paired_execution": paired_summary,
        "hold_only_diagnostic": hold_only,
        "projection_rounding_reconciliation": projection_reconciliation,
        "limitations": [
            "short-circuited later rules are unavailable as pass/fail facts",
            "multi-label reason counts overlap",
            "BASE and STRESS change costs and latency together",
            "the diagnosis is post-result and descriptive, not causal or independent replication",
        ],
    }
    _immutable_write(output / "summary.json", _canonical(summary))
    _immutable_write(output / "hold-only.json", _canonical(hold_only))
    _immutable_write(output / "reconciliation.json", _canonical(correction))
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
