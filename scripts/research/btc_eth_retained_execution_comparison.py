"""One-shot BTC/ETH retained-decision execution-profile comparison (Issue #225).

The driver does not evaluate strategies.  It streams the two retained result
authorities, asks the existing entry authorizer for executable intents, and
passes only those intents through the accepted 5m LAST protection adapter.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Buffer, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from statistics import median
from typing import Any, BinaryIO, cast

from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    DATASET_VERSION as RETAINED_DATASET_VERSION,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    FOLLOW_UP,
    TASKS,
    ComparisonArm,
    ComparisonScenario,
    HistoryScreenedReplayResult,
    scenario_costs,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    MANIFEST_SHA256 as RETAINED_MANIFEST_SHA256,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    PLAN_SHA256 as RETAINED_PLAN_SHA256,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    PROTOCOL_SHA256 as RETAINED_PROTOCOL_SHA256,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    REPORT_SHA256 as RETAINED_REPORT_SHA256,
)
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    TASK_PLAN_SHA256 as RETAINED_TASK_PLAN_SHA256,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import LEDGER_SCHEMA_VERSION
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mExecutionBinding,
    Last5mExecutionStatus,
    Last5mParquetSource,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    TriggerBasis,
    TriggerKind,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import decode
from adaptive_trading_bot.storage import SourceId

PROTOCOL = "BTC_ETH_RETAINED_DECISION_EXECUTION_COMPARISON_V1"
MODEL_SHA = "90101c22d9465ba2fb41eedb3164ac0285c8b9e3"
MODEL_TREE = "8ecd7eabc46a2c3008475eaa7cbb6c568d509bfc"
ORIGINAL_EXECUTABLE = "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e"
ORIGINAL_UUID = "ec10ca5d-01e6-436f-8153-fe9c5b5439b5"
ORIGINAL_EVENT_SHA = "0db0661d022a8c0c4260852bfd6490a2afab45416ffceb83e8f04dfff5eacbe2"
ORIGINAL_EVENT_REVISION = 20
ARTIFACT_INDEX_SHA = "e45e9a1a23f763ddba1bc4c2e087717f843eec2eaafdf325c5a9b5e8ad974ed6"
CORPUS_MANIFEST_SHA = "756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d"
CORPUS_REPORT_SHA = "bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66"
CORPUS_PLAN_SHA = "dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f"
DATASET_VERSION = "weex-history-screened-196d-last-continuation-v1"
EVIDENCE_ROOT = Path("data/research_evidence/issue-220-history-screened-180d-recovery-d09e50f3")
CORPUS_ROOT = Path("data/research_corpus/history-screened-196d-last-continuation-v1")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
SIGNAL_START = datetime(2026, 4, 10, 0, 15, tzinfo=UTC)
SIGNAL_END_INCLUSIVE = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)
FOLLOW_UP_END = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
EXPECTED_SLOTS = 17_280
EXPECTED_RECORDS = 69_120
WALL_BUDGET_SECONDS = 3_600
NEW_EVIDENCE_LIMIT = 20 * 1024 * 1024
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class ComparisonError(RuntimeError):
    """The bounded comparison cannot safely continue."""


class BudgetExceeded(ComparisonError):
    """The one cumulative real-processing budget has expired."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha_path(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def _tagged_field(value: object, name: str) -> object:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or value[0] != "dataclass"
        or not isinstance(value[2], list)
    ):
        raise ComparisonError(f"{name} owner is not a canonical dataclass")
    for pair in value[2]:
        if isinstance(pair, list) and len(pair) == 2 and pair[0] == name:
            return pair[1]
    raise ComparisonError(f"canonical dataclass lacks {name}")


def _tagged_scalar(value: object, tag: str) -> object:
    if not isinstance(value, list) or len(value) < 2 or value[0] != tag:
        raise ComparisonError(f"expected canonical {tag}")
    if tag == "enum":
        return _tagged_scalar(value[2], "str")
    return value[1]


def _raw_facts(raw: object) -> dict[str, object]:
    decision = _tagged_field(raw, "decision")
    identity = _tagged_field(decision, "identity")
    evaluation = _tagged_field(decision, "strategy_evaluation")
    risk = _tagged_field(decision, "risk_decision")
    safety = _tagged_field(decision, "safety_decision")
    observed = datetime.fromisoformat(
        cast(str, _tagged_scalar(_tagged_field(identity, "observed_at"), "datetime"))
    )
    return {
        "arm": _tagged_scalar(_tagged_field(raw, "arm"), "enum"),
        "scenario": _tagged_scalar(_tagged_field(raw, "scenario"), "enum"),
        "task_id": _tagged_scalar(_tagged_field(raw, "task_id"), "str"),
        "population_sha256": _tagged_scalar(_tagged_field(raw, "population_sha256"), "str"),
        "observed_at": observed,
        "strategy_id": _tagged_scalar(_tagged_field(identity, "strategy_id"), "str"),
        "strategy_version": _tagged_scalar(_tagged_field(identity, "strategy_version"), "str"),
        "action": _tagged_scalar(_tagged_field(evaluation, "action"), "enum"),
        "risk": _tagged_scalar(_tagged_field(risk, "status"), "enum"),
        "safety": _tagged_scalar(_tagged_field(safety, "status"), "enum"),
        "decision": decision,
    }


class _HashingReader(io.RawIOBase):
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, target: Buffer) -> int:
        view = memoryview(target)
        data = self.handle.read(view.nbytes)
        view[: len(data)] = data
        self.digest.update(data)
        self.count += len(data)
        return len(data)


@dataclass
class Progress:
    started_at: str
    phase: str = "STARTING"
    records_completed: int = 0
    trades_completed: int = 0
    members_completed: int = 0
    last_heartbeat: str = ""
    status: str = "RUNNING"


class RunState:
    def __init__(self, output: Path, deadline: float) -> None:
        self.output = output
        self.deadline = deadline
        self.progress = Progress(datetime.now(UTC).isoformat())
        self._last_flush = 0.0

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("3600-second cumulative real-processing budget expired")
        self.flush()

    def flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_flush >= 30:
            self.progress.last_heartbeat = datetime.now(UTC).isoformat()
            _replace_json(self.output / "progress.json", asdict(self.progress))
            self._last_flush = now


def _replace_json(path: Path, value: object) -> None:
    payload = _canonical(value) + b"\n"
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_bytes(payload)
    os.replace(pending, path)


def _append_jsonl(path: Path, value: object) -> None:
    with path.open("ab") as handle:
        handle.write(_canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _index_rows(index: object) -> list[dict[str, object]]:
    rows: object
    if isinstance(index, list):
        rows = index
    elif isinstance(index, dict):
        rows = index.get("artifacts") or index.get("files")
        if isinstance(rows, dict):
            rows = [{"path": path, "sha256": digest} for path, digest in rows.items()]
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ComparisonError("publication artifact index is malformed")
    return cast(list[dict[str, object]], rows)


def _indexed_path(root: Path, row: dict[str, object]) -> Path:
    name = row.get("path") or row.get("name")
    if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ComparisonError("artifact index path is unsafe")
    return root / name


def _indexed_size(row: dict[str, object]) -> int:
    value = row.get("bytes") if row.get("bytes") is not None else row.get("size")
    if type(value) is not int or value < 0:
        raise ComparisonError("artifact index size is invalid")
    return value


def _indexed_digest(row: dict[str, object]) -> str:
    value = row.get("sha256")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ComparisonError("artifact index digest is invalid")
    return value


def _read_indexed(root: Path, row: dict[str, object]) -> bytes:
    path = _indexed_path(root, row)
    body = path.read_bytes()
    if len(body) != _indexed_size(row) or _sha(body) != _indexed_digest(row):
        raise ComparisonError(f"indexed artifact bytes changed: {path.name}")
    return body


def _ledger_events(
    evidence: Path,
    rows: list[dict[str, object]],
    *,
    expected_final_sha: str = ORIGINAL_EVENT_SHA,
    expected_revision: int = 20,
) -> list[dict[str, object]]:
    event_rows: dict[int, dict[str, object]] = {}
    for row in rows:
        name = row.get("path") or row.get("name")
        if not isinstance(name, str):
            continue
        match = re.search(r"(?:^|/)original-ledger/(\d{4})\.json$", name)
        if match is None:
            continue
        revision = int(match.group(1))
        if revision in event_rows:
            raise ComparisonError("retained ledger revision is duplicated in artifact index")
        event_rows[revision] = row
    if set(event_rows) != set(range(expected_revision + 1)):
        raise ComparisonError("retained ledger event inventory is incomplete or extra")
    events: list[dict[str, object]] = []
    previous: str | None = None
    for revision in range(expected_revision + 1):
        body = _read_indexed(evidence, event_rows[revision])
        try:
            event = json.loads(body)
        except (UnicodeError, ValueError) as exc:
            raise ComparisonError("retained ledger event is malformed") from exc
        if (
            not isinstance(event, dict)
            or _canonical(event) != body
            or event.get("schema_version") != LEDGER_SCHEMA_VERSION
            or event.get("revision") != revision
            or event.get("previous_sha256") != previous
        ):
            raise ComparisonError("retained ledger hash chain is contradictory")
        events.append(event)
        previous = _sha(body)
    if previous != expected_final_sha:
        raise ComparisonError("retained ledger final event identity changed")
    return events


def _validate_original_creation(events: list[dict[str, object]]) -> None:
    created = events[0]
    task_ids = created.get("task_ids")
    if (
        created.get("event") != "CREATED"
        or created.get("run_uuid") != ORIGINAL_UUID
        or created.get("executable_sha") != ORIGINAL_EXECUTABLE
        or created.get("protocol_sha256") != RETAINED_PROTOCOL_SHA256
        or created.get("task_plan_sha256") != RETAINED_TASK_PLAN_SHA256
        or created.get("dataset_version") != RETAINED_DATASET_VERSION
        or created.get("manifest_sha256") != RETAINED_MANIFEST_SHA256
        or created.get("report_sha256") != RETAINED_REPORT_SHA256
        or created.get("plan_sha256") != RETAINED_PLAN_SHA256
        or not isinstance(task_ids, list)
        or tuple(task_ids) != tuple(task.task_id for task in TASKS)
    ):
        raise ComparisonError("retained ledger creation provenance changed")


def _select_task_inputs(
    evidence: Path,
    rows: list[dict[str, object]],
    events: list[dict[str, object]],
    tasks: Iterable[object],
) -> dict[str, dict[str, object]]:
    by_digest = {_indexed_digest(row): row for row in rows}
    found: dict[str, dict[str, object]] = {}
    for raw_task in tasks:
        task = cast(Any, raw_task)
        symbol = cast(str, task.wire_symbol)
        matching = [
            event
            for event in events
            if event.get("event") == "COMPLETED" and event.get("task_index") == task.index
        ]
        if not matching:
            raise ComparisonError(f"{symbol} committed task event is missing")
        if len(matching) > 1:
            raise ComparisonError(f"{symbol} committed task event is duplicated")
        event = matching[0]
        result_row = by_digest.get(str(event.get("results_sha256")))
        report_row = by_digest.get(str(event.get("report_sha256")))
        if result_row is None or report_row is None:
            raise ComparisonError(f"{symbol} task artifacts are absent from indexed inventory")
        report_body = _read_indexed(evidence, report_row)
        try:
            report = json.loads(report_body)
        except (UnicodeError, ValueError) as exc:
            raise ComparisonError(f"{symbol} retained task report is malformed") from exc
        if (
            not isinstance(report, dict)
            or report.get("task_id") != task.task_id
            or report.get("task_index") != task.index
            or report.get("wire_symbol") != symbol
            or report.get("population_sha256") != event.get("population_sha256")
        ):
            raise ComparisonError(f"{symbol} task report differs from committed task authority")
        found[symbol] = {
            "task_id": task.task_id,
            "task_index": task.index,
            "results": _indexed_path(evidence, result_row),
            "results_sha256": event["results_sha256"],
            "results_bytes": _indexed_size(result_row),
            "report": _indexed_path(evidence, report_row),
            "report_sha256": event["report_sha256"],
            "report_bytes": _indexed_size(report_row),
            "population_sha256": event["population_sha256"],
        }
    return found


def _discover_inputs(repo: Path) -> dict[str, dict[str, object]]:
    evidence = repo / EVIDENCE_ROOT
    index_path = evidence / "artifact-index.json"
    index_body = index_path.read_bytes()
    if _sha(index_body) != ARTIFACT_INDEX_SHA:
        raise ComparisonError("Issue #220 artifact index identity changed")
    rows = _index_rows(json.loads(index_body))
    events = _ledger_events(
        evidence,
        rows,
        expected_final_sha=ORIGINAL_EVENT_SHA,
        expected_revision=ORIGINAL_EVENT_REVISION,
    )
    _validate_original_creation(events)
    tasks = {task.wire_symbol: task for task in TASKS if task.wire_symbol in SYMBOLS}
    found = _select_task_inputs(evidence, rows, events, tasks.values())
    if set(found) != set(SYMBOLS):
        raise ComparisonError("BTC/ETH task inventory is incomplete")
    return found


def _corpus_inputs(repo: Path) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    root = repo / CORPUS_ROOT
    manifest_body = (root / "manifest.json").read_bytes()
    report_body = (root / "report.json").read_bytes()
    plan_body = (root / "plan.json").read_bytes()
    if (_sha(manifest_body), _sha(report_body), _sha(plan_body)) != (
        CORPUS_MANIFEST_SHA,
        CORPUS_REPORT_SHA,
        CORPUS_PLAN_SHA,
    ):
        raise ComparisonError("accepted corpus identities changed")
    manifest = json.loads(manifest_body)
    report = json.loads(report_body)
    if manifest.get("dataset_version") != DATASET_VERSION or manifest.get("status") != "QUALIFIED":
        raise ComparisonError("accepted corpus is not the qualified bound dataset")
    files = manifest.get("files")
    symbols = report.get("symbols")
    if not isinstance(files, dict) or not isinstance(symbols, dict):
        raise ComparisonError("accepted corpus manifest/report is malformed")
    found: dict[str, dict[str, object]] = {}
    for symbol in SYMBOLS:
        matches = [name for name in files if name.endswith(f"/{symbol}-5m.parquet")]
        member = symbols.get(symbol)
        if len(matches) != 1 or not isinstance(member, dict) or member.get("status") != "QUALIFIED":
            raise ComparisonError(f"{symbol} canonical 5m authority is not uniquely qualified")
        epochs = member.get("epochs")
        if not isinstance(epochs, list) or not epochs:
            raise ComparisonError(f"{symbol} has no explicit valid epoch")
        covering = [
            epoch
            for epoch in epochs
            if isinstance(epoch, dict)
            and int(epoch["start_ms"]) <= int(SIGNAL_START.timestamp() * 1000)
            and int(epoch["end_ms"]) >= int(FOLLOW_UP_END.timestamp() * 1000)
        ]
        if len(covering) != 1:
            raise ComparisonError(f"{symbol} has no unique epoch covering decisions/follow-up")
        name = matches[0]
        found[symbol] = {
            "path": root / name,
            "sha256": files[name],
            "epoch": covering[0],
            "epoch_id": f"{covering[0]['start_ms']}:{covering[0]['end_ms']}",
        }
    return manifest, found


def _expected_times() -> tuple[datetime, ...]:
    return tuple(SIGNAL_START + index * timedelta(minutes=15) for index in range(EXPECTED_SLOTS))


def _stream_task(
    source: dict[str, object],
    report: dict[str, object],
    state: RunState,
    *,
    expected_times: tuple[datetime, ...] | None = None,
) -> tuple[dict[tuple[str, str, datetime], HistoryScreenedReplayResult], dict[str, Counter[str]]]:
    path = cast(Path, source["results"])
    expected_size = source["results_bytes"]
    typed: dict[tuple[str, str, datetime], HistoryScreenedReplayResult] = {}
    counts: dict[str, Counter[str]] = {arm.value: Counter() for arm in ComparisonArm}
    cells: dict[tuple[str, str], list[datetime]] = {
        (arm.value, scenario.value): [] for arm in ComparisonArm for scenario in ComparisonScenario
    }
    decision_hashes: dict[tuple[str, datetime], str] = {}
    with path.open("rb") as raw_handle:
        hashing = _HashingReader(raw_handle)
        buffered = io.BufferedReader(hashing, buffer_size=1024 * 1024)
        with gzip.GzipFile(fileobj=buffered, mode="rb") as compressed:
            for line in compressed:
                state.check()
                raw = json.loads(line)
                facts = _raw_facts(raw)
                raw_arm = cast(str, facts["arm"])
                raw_scenario = cast(str, facts["scenario"])
                observed = cast(datetime, facts["observed_at"])
                if facts["task_id"] != source["task_id"]:
                    raise ComparisonError("retained result task linkage changed")
                if facts["population_sha256"] != source["population_sha256"]:
                    raise ComparisonError("retained result population linkage changed")
                cells[(raw_arm, raw_scenario)].append(observed)
                decision_digest = _sha(_canonical(facts["decision"]))
                prior = decision_hashes.setdefault((raw_arm, observed), decision_digest)
                if prior != decision_digest:
                    raise ComparisonError("BASE/STRESS retained decisions differ")
                action, risk, safety = facts["action"], facts["risk"], facts["safety"]
                counts[raw_arm][f"action:{action}"] += 1
                if action != "NO_TRADE":
                    counts[raw_arm][f"risk:{risk}"] += 1
                    counts[raw_arm][f"safety:{safety}"] += 1
                    value = decode(raw)
                    if not isinstance(value, HistoryScreenedReplayResult):
                        raise ComparisonError("candidate record has a foreign typed authority")
                    value.validate()
                    typed[(raw_arm, raw_scenario, observed)] = value
                state.progress.records_completed += 1
        buffered.read()
        digest = hashing.digest.hexdigest()
        size = hashing.count
    if digest != source["results_sha256"] or (
        isinstance(expected_size, int) and size != expected_size
    ):
        raise ComparisonError("retained result stream hash/size changed")
    expected_times = _expected_times() if expected_times is None else expected_times
    for key, values in cells.items():
        if tuple(values) != expected_times:
            raise ComparisonError(f"retained decision grid is incomplete or unordered for {key}")
    if sum(len(values) for values in cells.values()) != len(expected_times) * 4:
        raise ComparisonError("retained task record count changed")
    if report.get("population_sha256") != source["population_sha256"]:
        raise ComparisonError("retained report population linkage changed")
    return typed, counts


def _mask(times: Iterable[datetime]) -> set[datetime]:
    selected: set[datetime] = set()
    last: datetime | None = None
    for value in sorted(times):
        if last is None or value >= last + FOLLOW_UP:
            selected.add(value)
            last = value
    return selected


def _legacy_row(result: HistoryScreenedReplayResult) -> dict[str, object]:
    branches = result.branches
    branch = branches[0] if len(branches) == 1 else None
    position = branch.final_position if branch is not None else None
    intent = result.entry_intent
    entry_fill = branch.entry_fill if branch is not None else None
    exit_fill = branch.exit_fill if branch is not None else None
    return {
        "status": result.status.value,
        "filled": bool(branches),
        "closed": bool(branch and branch.closed_net_r is not None),
        "recovered": result.status.value.startswith("RECOVERY"),
        "unfilled": result.status.value == "ENTRY_UNFILLED",
        "unresolved": bool(position and position.quantity != 0),
        "closed_net_r": None if branch is None else _text(branch.closed_net_r),
        "gross_pnl_quote": None
        if position is None
        else str(position.cumulative_gross_realized_pnl),
        "fees_quote": None if position is None else str(position.cumulative_fees),
        "net_pnl_quote": None if position is None else str(position.cumulative_net_realized_pnl),
        "residual_quantity": None if position is None else str(position.quantity.copy_abs()),
        "quantity": None if intent is None else str(intent.quantity),
        "approved_risk_quote": None if intent is None else str(intent.approved_risk_quote),
        "stop_basis": None if intent is None else str(intent.stop_basis),
        "decision_reference_price": None if intent is None else str(intent.reference_price),
        "entry_reference_price": None
        if entry_fill is None
        else str(entry_fill.reference_open_price),
        "entry_fill_price": None if entry_fill is None else str(entry_fill.fill_price),
        "entry_fee_quote": None if entry_fill is None else str(entry_fill.fee_quote),
        "exit_reference_price": None if exit_fill is None else str(exit_fill.reference_open_price),
        "exit_fill_price": None if exit_fill is None else str(exit_fill.fill_price),
        "exit_fee_quote": None if exit_fill is None else str(exit_fill.fee_quote),
    }


def _new_row(result: Last5mProtectiveExecutionResult) -> dict[str, object]:
    trigger = result.trigger
    entry_fill = result.entry_fill
    exit_fill = result.exit_fill
    activation_ambiguous = bool(trigger and trigger.kind is TriggerKind.ACTIVATION_ORDER_AMBIGUOUS)
    dual_touch = bool(trigger and trigger.kind is TriggerKind.DUAL_TOUCH_STOP_FIRST)
    return {
        "status": result.status.value,
        "filled": result.entry_fill is not None,
        "closed": result.position_closed,
        "recovered": result.status is Last5mExecutionStatus.INVALID_GEOMETRY_RECOVERED,
        "unfilled": result.status is Last5mExecutionStatus.ENTRY_UNFILLED,
        "unresolved": result.unresolved_quantity != 0,
        "closed_net_r": _text(result.closed_net_approved_risk_r),
        "gross_pnl_quote": str(result.realized_gross_pnl),
        "fees_quote": str(result.total_fees),
        "net_pnl_quote": str(result.realized_net_pnl),
        "residual_quantity": str(result.unresolved_quantity),
        "quantity": str(result.intent_audit.quantity),
        "approved_risk_quote": str(result.approved_risk_quote),
        "stop_basis": str(result.original_stop_basis),
        "decision_reference_price": str(result.intent_audit.reference_price),
        "risk_distance": _text(result.risk_distance),
        "actual_fill_risk_quote": _text(result.actual_fill_risk_quote),
        "target_price": _text(result.target_price),
        "entry_reference_price": None if entry_fill is None else str(entry_fill.reference_price),
        "entry_fill_price": None if entry_fill is None else str(entry_fill.fill_price),
        "entry_fee_quote": None if entry_fill is None else str(entry_fill.fee_quote),
        "exit_reference_price": None if exit_fill is None else str(exit_fill.reference_price),
        "exit_fill_price": None if exit_fill is None else str(exit_fill.fill_price),
        "exit_fee_quote": None if exit_fill is None else str(exit_fill.fee_quote),
        "entry_grid_wait_seconds": None
        if result.entry_grid_wait is None
        else str(Decimal(str(result.entry_grid_wait.total_seconds()))),
        "trigger": None if trigger is None else trigger.kind.value,
        "gap": bool(trigger and trigger.gap_through_stop),
        "ambiguous": activation_ambiguous or dual_touch,
        "activation_order_ambiguous": activation_ambiguous,
        "dual_touch_stop_first": dual_touch,
        "holding_lower_seconds": None
        if result.holding_duration_lower is None
        else str(Decimal(str(result.holding_duration_lower.total_seconds()))),
        "holding_upper_seconds": None
        if result.holding_duration_upper is None
        else str(Decimal(str(result.holding_duration_upper.total_seconds()))),
        "holding_exact": result.holding_time_exact,
    }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _exact_sum(values: Iterable[Decimal]) -> Decimal:
    values = tuple(values)
    with localcontext(_DECIMAL_CONTEXT):
        return sum(values, Decimal(0))


def _aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    closed = [
        Decimal(cast(str, row["closed_net_r"])) for row in rows if row["closed_net_r"] is not None
    ]
    gains = _exact_sum(value for value in closed if value > 0)
    losses = -_exact_sum(value for value in closed if value < 0)
    with localcontext(_DECIMAL_CONTEXT):
        mean = _exact_sum(closed) / len(closed) if closed else None
        pf = gains / losses if losses else None
    durations = sorted(
        Decimal(cast(str, row["holding_upper_seconds"]))
        for row in rows
        if row.get("holding_upper_seconds") is not None
    )
    p90 = durations[(len(durations) * 9 - 1) // 10] if durations else None
    return {
        "denominator": len(rows),
        "filled": sum(bool(row["filled"]) for row in rows),
        "closed": len(closed),
        "recovered": sum(bool(row["recovered"]) for row in rows),
        "unfilled": sum(bool(row["unfilled"]) for row in rows),
        "unresolved": sum(bool(row["unresolved"]) for row in rows),
        "wins": sum(value > 0 for value in closed),
        "losses": sum(value < 0 for value in closed),
        "net_r_mean": _text(mean),
        "net_r_median": _text(cast(Decimal | None, median(closed)) if closed else None),
        "r_profit_factor": _text(pf),
        "gross_pnl_quote": str(
            _exact_sum(
                Decimal(cast(str, row["gross_pnl_quote"]))
                for row in rows
                if row["gross_pnl_quote"] is not None
            )
        ),
        "fees_quote": str(
            _exact_sum(
                Decimal(cast(str, row["fees_quote"]))
                for row in rows
                if row["fees_quote"] is not None
            )
        ),
        "net_pnl_quote": str(
            _exact_sum(
                Decimal(cast(str, row["net_pnl_quote"]))
                for row in rows
                if row["net_pnl_quote"] is not None
            )
        ),
        "ambiguity_count": sum(bool(row.get("ambiguous")) for row in rows),
        "activation_order_ambiguity_count": sum(
            bool(row.get("activation_order_ambiguous")) for row in rows
        ),
        "dual_touch_stop_first_count": sum(bool(row.get("dual_touch_stop_first")) for row in rows),
        "gap_count": sum(bool(row.get("gap")) for row in rows),
        "grid_wait_count": sum(row.get("entry_grid_wait_seconds") is not None for row in rows),
        "recorded_grid_wait_count": sum(
            row.get("entry_grid_wait_seconds") is not None for row in rows
        ),
        "positive_grid_delay_count": sum(
            Decimal(cast(str, row["entry_grid_wait_seconds"])) > 0
            for row in rows
            if row.get("entry_grid_wait_seconds") is not None
        ),
        "entry_fees_quote": str(
            _exact_sum(
                Decimal(cast(str, row["entry_fee_quote"]))
                for row in rows
                if row.get("entry_fee_quote") is not None
            )
        ),
        "exit_fees_quote": str(
            _exact_sum(
                Decimal(cast(str, row["exit_fee_quote"]))
                for row in rows
                if row.get("exit_fee_quote") is not None
            )
        ),
        "holding_upper_seconds_median": _text(
            cast(Decimal | None, median(durations)) if durations else None
        ),
        "holding_upper_seconds_p90": _text(p90),
        "holding_upper_seconds_max": _text(durations[-1] if durations else None),
    }


def _finite_window(
    bars: tuple[Any, ...],
    bar_index: dict[datetime, int],
    start: datetime,
    end: datetime,
) -> tuple[Any, ...]:
    """Return one complete half-open interval, including a master-series end boundary."""

    first = bar_index.get(start)
    if not bars:
        raise ComparisonError("decision finite 168h 5m interval is unavailable")
    stop = len(bars) if end == bars[-1].close_time else bar_index.get(end)
    expected = int((end - start) / timedelta(minutes=5))
    if first is None or stop is None or stop - first != expected:
        raise ComparisonError("decision finite 168h 5m interval is unavailable")
    return bars[first:stop]


def _paired(rows: list[dict[str, object]]) -> dict[str, object]:
    deltas: list[Decimal] = []
    partitions: Counter[str] = Counter()
    for row in rows:
        old = cast(dict[str, object], row["legacy"])
        new = cast(dict[str, object], row["new"])
        if old["closed_net_r"] is not None and new["closed_net_r"] is not None:
            with localcontext(_DECIMAL_CONTEXT):
                deltas.append(
                    Decimal(cast(str, new["closed_net_r"]))
                    - Decimal(cast(str, old["closed_net_r"]))
                )
            partitions["BOTH_CLOSED"] += 1
        else:
            partitions[f"OLD_{old['status']}|NEW_{new['status']}"] += 1
    return {
        "denominator": len(rows),
        "both_closed": len(deltas),
        "new_minus_old_closed_net_r_mean": _text(
            _exact_sum(deltas) / len(deltas) if deltas else None
        ),
        "new_minus_old_closed_net_r_median": _text(
            cast(Decimal | None, median(deltas)) if deltas else None
        ),
        "state_partitions": dict(sorted(partitions.items())),
    }


def _run(repo: Path, output: Path, run_record: Path) -> dict[str, object]:
    if output.exists() and any(output.iterdir()):
        raise ComparisonError("output directory is not empty")
    output.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    state = RunState(output, start + WALL_BUDGET_SECONDS)
    record = json.loads(run_record.read_bytes())
    if record.get("protocol") != PROTOCOL or record.get("model_sha") != MODEL_SHA:
        raise ComparisonError("run record differs from frozen protocol/model")
    state.progress.phase = "FREEZE_INPUT_INVENTORY"
    state.flush(force=True)
    tasks = _discover_inputs(repo)
    _, corpus = _corpus_inputs(repo)
    inventory = {
        "protocol": PROTOCOL,
        "model_sha": MODEL_SHA,
        "model_tree": MODEL_TREE,
        "original_executable": ORIGINAL_EXECUTABLE,
        "original_uuid": ORIGINAL_UUID,
        "original_event_sha256": ORIGINAL_EVENT_SHA,
        "artifact_index_sha256": ARTIFACT_INDEX_SHA,
        "corpus_manifest_sha256": CORPUS_MANIFEST_SHA,
        "corpus_report_sha256": CORPUS_REPORT_SHA,
        "corpus_plan_sha256": CORPUS_PLAN_SHA,
        "tasks": {
            symbol: {key: str(value) for key, value in data.items()}
            for symbol, data in tasks.items()
        },
        "corpus": {
            symbol: {key: str(value) for key, value in data.items()}
            for symbol, data in corpus.items()
        },
    }
    _replace_json(output / "input-inventory.json", inventory)
    summaries: dict[str, object] = {}
    rows_path = output / "per-trade.jsonl"
    for symbol in SYMBOLS:
        state.progress.phase = f"STREAM_{symbol}_RETAINED"
        report_path = cast(Path, tasks[symbol]["report"])
        report_body = report_path.read_bytes()
        if _sha(report_body) != tasks[symbol]["report_sha256"]:
            raise ComparisonError(f"{symbol} retained task report changed")
        report = json.loads(report_body)
        if (
            report.get("decision_slots_per_arm") != EXPECTED_SLOTS
            or report.get("task_id") != tasks[symbol]["task_id"]
        ):
            raise ComparisonError(f"{symbol} retained task report identity/count changed")
        typed, funnel = _stream_task(tasks[symbol], report, state)
        authorized: dict[str, list[tuple[datetime, HistoryScreenedReplayResult]]] = {
            arm.value: [] for arm in ComparisonArm
        }
        for (key_arm, key_scenario, observed), result in typed.items():
            auth = EntryOrderIntentAuthorizer().authorize(result.decision)
            if (
                key_scenario == ComparisonScenario.BASE.value
                and auth.status is OrderIntentAuthorizationStatus.AUTHORIZED
            ):
                authorized[key_arm].append((observed, result))
        report_admissions = cast(dict[str, object], report["admissions"])
        for arm in ComparisonArm:
            if len(authorized[arm.value]) != report_admissions[arm.value.lower()]:
                raise ComparisonError(f"{symbol} {arm.value} retained admission count changed")
        masks = {
            arm.value: _mask(time for time, _ in authorized[arm.value]) for arm in ComparisonArm
        }
        report_masks = cast(dict[str, object], report["masks"])
        for arm in ComparisonArm:
            if len(masks[arm.value]) != len(cast(list[object], report_masks[arm.value.lower()])):
                raise ComparisonError(f"{symbol} {arm.value} spacing-mask count changed")
        state.progress.phase = f"READ_{symbol}_5M_ONCE"
        epoch_id = cast(str, corpus[symbol]["epoch_id"])
        source_id = SourceId(DATASET_VERSION)
        symbol_task = next(task for task in TASKS if task.wire_symbol == symbol)
        master_binding = Last5mExecutionBinding(
            source_id, symbol_task.symbol, epoch_id, SIGNAL_START, FOLLOW_UP_END
        )
        bars = Last5mParquetSource(
            cast(Path, corpus[symbol]["path"]), expected_sha256=cast(str, corpus[symbol]["sha256"])
        ).read(master_binding)
        bar_index = {bar.open_time: index for index, bar in enumerate(bars)}
        member_rows: list[dict[str, object]] = []
        for arm in ComparisonArm:
            for observed, base_result in authorized[arm.value]:
                auth = EntryOrderIntentAuthorizer().authorize(base_result.decision)
                if (
                    auth.status is not OrderIntentAuthorizationStatus.AUTHORIZED
                    or auth.intent is None
                ):
                    raise ComparisonError("retained authorized decision no longer yields an intent")
                decision_bars = _finite_window(bars, bar_index, observed, observed + FOLLOW_UP)
                binding = Last5mExecutionBinding(
                    source_id, symbol_task.symbol, epoch_id, observed, observed + FOLLOW_UP
                )
                for scenario in ComparisonScenario:
                    state.check()
                    old = typed[(arm.value, scenario.value, observed)]
                    config = Last5mProtectionConfiguration(
                        scenario_costs(scenario),
                        ExitPolicy.FIXED_2R,
                        acknowledgement=ProtectionAcknowledgement.ACCEPTED,
                        activation_latency=timedelta(0),
                        trigger_basis=TriggerBasis.LAST,
                    )
                    new = run_last_5m_protective_execution(
                        intent=auth.intent,
                        binding=binding,
                        bars=decision_bars,
                        configuration=config,
                    )
                    row: dict[str, object] = {
                        "symbol": symbol,
                        "arm": arm.value,
                        "scenario": scenario.value,
                        "decision_at": observed.isoformat(),
                        "masked": observed in masks[arm.value],
                        "legacy": _legacy_row(old),
                        "new": _new_row(new),
                    }
                    _append_jsonl(rows_path, row)
                    member_rows.append(row)
                    state.progress.trades_completed += 1
        cells: dict[str, object] = {}
        for arm in ComparisonArm:
            for scenario in ComparisonScenario:
                cell_rows = [
                    row
                    for row in member_rows
                    if row["arm"] == arm.value and row["scenario"] == scenario.value
                ]
                full_old = [cast(dict[str, object], row["legacy"]) for row in cell_rows]
                full_new = [cast(dict[str, object], row["new"]) for row in cell_rows]
                masked_rows = [row for row in cell_rows if row["masked"]]
                cells[f"{arm.value}:{scenario.value}"] = {
                    "full": {
                        "legacy": _aggregate(full_old),
                        "new": _aggregate(full_new),
                        "paired": _paired(cell_rows),
                    },
                    "mask": {
                        "legacy": _aggregate(
                            [cast(dict[str, object], row["legacy"]) for row in masked_rows]
                        ),
                        "new": _aggregate(
                            [cast(dict[str, object], row["new"]) for row in masked_rows]
                        ),
                        "paired": _paired(masked_rows),
                    },
                }
        summaries[symbol] = {
            "funnel_counts_repeated_across_scenarios": {
                key: dict(value) for key, value in funnel.items()
            },
            "admissions": {arm.value: len(authorized[arm.value]) for arm in ComparisonArm},
            "mask_sizes": {arm.value: len(masks[arm.value]) for arm in ComparisonArm},
            "cells": cells,
        }
        _replace_json(output / f"{symbol.lower()}-aggregate.json", summaries[symbol])
        state.progress.members_completed += 1
        state.flush(force=True)
    projection: dict[str, object] = {
        "schema": "btc-eth-retained-decision-execution-comparison-v1",
        "protocol": PROTOCOL,
        "status": "COMPLETE",
        "interpretation": (
            "FIXED_NEGATIVE_ENDS_ROUND;MIXED_OR_SPARSE_NOT_PROMOTION;"
            "POSITIVE_BOTH_ASSETS_BOTH_SCENARIOS_EXPLORATORY_ONLY"
        ),
        "scientific_limits": [
            "SELECTION_INFORMED_NOT_PROTECTED_HOLDOUT",
            "RETAINED_DECISIONS_NOT_CAPITAL_CONSTRAINED_PORTFOLIO",
            "OLD_EXIT_UNKNOWN_NOT_RECORDED",
            "FULL_ORIGINAL_TEN_MEMBER_EVIDENCE_NOT_INDEPENDENTLY_REVERIFIED",
            "PROFILE_PACKAGE_NOT_COMPONENT_CAUSAL_IDENTIFICATION",
        ],
        "members": summaries,
    }
    _replace_json(output / "projection.json", projection)
    state.progress.phase = "COMPLETE"
    state.progress.status = "COMPLETE"
    state.flush(force=True)
    if (
        sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
        > NEW_EVIDENCE_LIMIT
    ):
        raise ComparisonError("new evidence exceeds 20 MiB ceiling")
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-record", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        projection = _run(args.repo.resolve(), args.output.resolve(), args.run_record.resolve())
    except BudgetExceeded as exc:
        if args.output.exists():
            _replace_json(
                args.output / "terminal.json",
                {"status": "INCOMPLETE_BUDGET_LIMIT", "error": str(exc)},
            )
        print(json.dumps({"status": "INCOMPLETE_BUDGET_LIMIT", "error": str(exc)}, sort_keys=True))
        return 2
    except Exception as exc:
        if args.output.exists():
            _replace_json(
                args.output / "terminal.json",
                {
                    "status": "INCOMPLETE_TECHNICAL_BLOCKER",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
        print(
            json.dumps(
                {
                    "status": "INCOMPLETE_TECHNICAL_BLOCKER",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {"status": projection["status"], "projection": str(args.output / "projection.json")},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
