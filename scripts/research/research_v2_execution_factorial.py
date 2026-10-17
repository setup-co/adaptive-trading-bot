"""Prepare/execute the separately granted staged Research V2 factorial study.

Issue #191 itself authorizes only ``preflight``.  ``run-stage-a`` requires a
future exact-SHA external grant; ``continue-stage-b`` additionally requires a
checkpoint-bound architect continuation authorization.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import socket
import subprocess
import time
from bisect import bisect_left
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from adaptive_trading_bot.backtest import ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.research.all_fill_replay import ResearchAllFillReplayRunner
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    _canonical,
    _digest,
    causal_follow_up_slice,
    decision_cutoff,
    frozen_tasks,
)
from adaptive_trading_bot.research.research_v2_factorial import (
    EXPECTED_ADMISSIONS,
    EXPECTED_MATRIX_RECORDS,
    EXPECTED_NEW_OUTCOMES,
    ORIGINAL_ARTIFACT_INDEX_SHA256,
    ORIGINAL_PROJECTION_SHA256,
    PROTOCOL_SHA256,
    AdmittedDecisionReference,
    FactorialCell,
    FactorialError,
    FactorialExecutionGrant,
    FactorialLedgerStore,
    FactorialOutcome,
    FactorialRunState,
    FactorialStage,
    StageContinuationAuthorization,
    admitted_inventory_digest,
    execute_factorial_cell,
    factorial_result_record,
    imported_outcome_from_record,
    outcome_from_factorial_result,
    project_factorial_matrix,
    protocol_facts,
    recover_factorial_result,
    stage_counts,
    stage_for_symbol,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    ScreenEvidenceError,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    decode as decode_authority,
)
from adaptive_trading_bot.storage import SourceId
from scripts.research import research_v2_retained_diagnosis as retained

EVIDENCE_RELATIVE = Path("data/research_evidence/issue-187-exploratory-screen-316744efa9c4b50a")
LEDGER_RELATIVE = Path("ledger/686bd4a1-ffff-4391-8562-f1e4caf7a575")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _exact_int(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise FactorialError(f"{field} is not an exact integer")
    return value


def _inventory(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, dict) for item in value):
        raise FactorialError("factorial admitted inventory is malformed")
    return tuple(cast(dict[str, Any], item) for item in value)


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *arguments),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _clean_sha(repo: Path) -> str:
    if _git(repo, "status", "--porcelain"):
        raise FactorialError("factorial execution requires a clean reviewed checkout")
    return _git(repo, "rev-parse", "HEAD")


def _immutable_write(path: Path, payload: bytes) -> str:
    if path.exists():
        raise FactorialError("factorial evidence is immutable and already exists")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha(payload)


@contextmanager
def _no_network() -> Iterator[None]:
    def rejected(*args: object, **kwargs: object) -> None:
        raise FactorialError("network use is prohibited during retained-evidence research")

    with (
        patch.object(socket.socket, "connect", rejected),
        patch.object(socket.socket, "connect_ex", rejected),
        patch.object(socket, "create_connection", rejected),
    ):
        yield


def _task_paths(index: int) -> dict[str, str]:
    number = f"{index:02d}"
    return {
        "population": (LEDGER_RELATIVE / f"{number}.population.json.gz").as_posix(),
        "partition": (LEDGER_RELATIVE / f"{number}.partition.json").as_posix(),
        "authority": (LEDGER_RELATIVE / f"{number}.authority.jsonl.gz").as_posix(),
    }


def _mask_members(rows: Sequence[dict[str, object]]) -> set[str]:
    selected: set[str] = set()
    by_group: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        by_group.setdefault((str(row["symbol"]), str(row["family"])), []).append(row)
    for members in by_group.values():
        last: datetime | None = None
        for row in sorted(members, key=lambda item: str(item["decision_observed_at"])):
            observed = datetime.fromisoformat(
                str(row["decision_observed_at"]).replace("Z", "+00:00")
            )
            if last is None or observed >= last + timedelta(hours=168):
                selected.add(str(row["decision_identity_sha256"]))
                last = observed
    return selected


def data_only_factorial_plan(repo: Path) -> dict[str, object]:
    """Verify original indexed bytes and derive the retained 246-source inventory."""

    repo = repo.resolve(strict=True)
    evidence = (repo / EVIDENCE_RELATIVE).resolve(strict=True)
    files, index_payload = retained.load_index(evidence)
    if _sha(index_payload) != ORIGINAL_ARTIFACT_INDEX_SHA256:
        raise FactorialError("Issue #187 artifact index identity changed")
    projection_path = (LEDGER_RELATIVE / "projection.json").as_posix()
    projection_payload = retained.verify_indexed_file(evidence, projection_path, files)
    if _sha(projection_payload) != ORIGINAL_PROJECTION_SHA256:
        raise FactorialError("Issue #187 projection identity changed")
    projection = retained._require_dict(json.loads(projection_payload), "projection")
    task_artifacts = {
        str(row["task_id"]): retained._require_dict(row, "task artifact")
        for raw in projection["task_artifacts"]
        for row in (retained._require_dict(raw, "task artifact"),)
    }
    all_trades: list[dict[str, object]] = []
    task_payload_hashes: dict[str, dict[str, str]] = {}
    for task in frozen_tasks():
        expected = retained._task_expectation(task.index)
        paths = _task_paths(task.index)
        payloads = {
            name: retained.verify_indexed_file(evidence, relative, files)
            for name, relative in paths.items()
        }
        projected = task_artifacts.get(task.task_id)
        if projected is None or any(
            projected.get(f"{name}_sha256") != _sha(payloads[name]) for name in payloads
        ):
            raise FactorialError("task payload differs from accepted projection")
        result = retained.extract_task_payloads(
            expected=expected,
            population_payload=payloads["population"],
            partition_payload=payloads["partition"],
            authority_payload=payloads["authority"],
            authority_path=paths["authority"],
            authority_sha=_sha(payloads["authority"]),
        )
        screens = cast(dict[str, tuple[dict[str, Any], ...]], result["screens"])
        for trade in cast(list[dict[str, object]], result["trades"]):
            screen = screens[str(trade["scenario"])][
                _exact_int(trade["decision_index"], field="decision index")
            ]
            trade["hold_only_facts"] = {
                "status": screen.get("hold_only_status"),
                "closed_net_r": screen.get("hold_only_closed_net_r"),
                "incurred_fees_quote": screen.get("hold_only_fee_quote"),
                "residual_base_quantity": screen.get("hold_only_open_quantity"),
                "branch_sha256": screen.get("hold_only_branch_sha256"),
            }
            all_trades.append(trade)
        task_payload_hashes[task.task_id] = {
            name: _sha(payload) for name, payload in payloads.items()
        }
    all_base_trades = tuple(row for row in all_trades if row["scenario"] == retained.SCENARIOS[0])
    if len(all_base_trades) != EXPECTED_ADMISSIONS or len(all_trades) != 2 * EXPECTED_ADMISSIONS:
        raise FactorialError("original authorities do not contain exact paired admissions")
    mask = _mask_members(all_base_trades)
    inventory = tuple(
        AdmittedDecisionReference(
            task_id=str(row["task_id"]),
            decision_index=_exact_int(row["decision_index"], field="decision index"),
            decision_identity_sha256=str(row["decision_identity_sha256"]),
            symbol=str(row["symbol"]),
            family=str(row["family"]),
            observed_at=datetime.fromisoformat(
                str(row["decision_observed_at"]).replace("Z", "+00:00")
            ),
            original_mask_member=str(row["decision_identity_sha256"]) in mask,
            population_sha256=task_payload_hashes[str(row["task_id"])]["population"],
            partition_sha256=task_payload_hashes[str(row["task_id"])]["partition"],
            authority_sha256=task_payload_hashes[str(row["task_id"])]["authority"],
            block=str(row["block"]),
            week=str(row["week"]),
            side=str(row["side"]),
        )
        for row in sorted(
            all_base_trades,
            key=lambda item: (
                int(str(item["task_id"]).split("-", 1)[0]),
                _exact_int(item["decision_index"], field="decision index"),
            ),
        )
    )
    counts = stage_counts(inventory)
    record: dict[str, object] = {
        "schema_version": "research-v2-factorial-input-plan-v2",
        "status": "DATA_ONLY_PREPARATION; NO_EXECUTION_GRANT",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol": protocol_facts(),
        "source_artifact_index_sha256": _sha(index_payload),
        "source_projection_sha256": _sha(projection_payload),
        "admitted_inventory": tuple(item.to_record() for item in inventory),
        "admitted_inventory_sha256": admitted_inventory_digest(inventory),
        "original_mask_sha256": _digest(tuple(sorted(mask))),
        "imported_diagonal_inventory": tuple(
            {
                "task_id": str(row["task_id"]),
                "decision_index": _exact_int(row["decision_index"], field="decision index"),
                "decision_identity_sha256": str(row["decision_identity_sha256"]),
                "cell": (
                    FactorialCell.C0L0.value
                    if row["scenario"] == retained.SCENARIOS[0]
                    else FactorialCell.C1L1.value
                ),
                "terminal_status": str(row["status"]),
                "symbol": str(row["symbol"]),
                "family": str(row["family"]),
                "closed_net_r": row["net_approved_risk_r"],
                "incurred_fees_quote": row["total_fees_quote"],
                "gross_realized_pnl_quote": row["gross_realized_pnl_quote"],
                "net_realized_pnl_quote": row["net_realized_pnl_quote"],
                "residual_base_quantity": row["remaining_base_quantity"],
                "result_sha256": str(row["all_fill_result_sha256"]),
                "authority_sha256": str(row["authority_sha256"]),
                "evidence_facts": {
                    name: row.get(name)
                    for name in (
                        "decision_observed_at",
                        "side",
                        "approved_risk_quote",
                        "entry_created_at",
                        "entry_eligible_at",
                        "entry_execution_candle_open_at",
                        "entry_fill_at",
                        "entry_reference_open_price",
                        "entry_fill_price",
                        "entry_fee_quote",
                        "primary_exit_category",
                        "primary_exit_reason",
                        "exit_created_at",
                        "exit_eligible_at",
                        "exit_execution_candle_open_at",
                        "exit_fill_at",
                        "exit_reference_open_price",
                        "exit_fill_price",
                        "exit_fee_quote",
                        "recovery_reasons",
                    )
                },
                "hold_only_facts": row["hold_only_facts"],
            }
            for row in sorted(
                all_trades,
                key=lambda item: (
                    int(str(item["task_id"]).split("-", 1)[0]),
                    _exact_int(item["decision_index"], field="decision index"),
                    retained.SCENARIOS.index(str(item["scenario"])),
                ),
            )
        ),
        "counts": counts,
        "new_task_cell_inventory": tuple(
            {
                "unit_id": f"{task.task_id}:{cell.value}",
                "stage": stage_for_symbol(task.wire_symbol).value,
                "task_id": task.task_id,
                "cell": cell.value,
                "admission_count": sum(item.task_id == task.task_id for item in inventory),
            }
            for task in frozen_tasks()
            for cell in (FactorialCell.C1L0, FactorialCell.C0L1)
        ),
        "controls": {
            "admissions": EXPECTED_ADMISSIONS,
            "new_outcomes": EXPECTED_NEW_OUTCOMES,
            "matrix_records": EXPECTED_MATRIX_RECORDS,
        },
    }
    record["imported_diagonal_inventory_sha256"] = _digest(record["imported_diagonal_inventory"])
    record["input_plan_sha256"] = _digest(record)
    return record


def _grant(path: Path) -> FactorialExecutionGrant:
    try:
        raw = json.loads(path.read_bytes())
        if not isinstance(raw, dict) or set(raw) != set(
            FactorialExecutionGrant.__dataclass_fields__
        ):
            raise ValueError("grant fields differ")
        return FactorialExecutionGrant(**raw)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise FactorialError("factorial execution grant is malformed") from exc


def _continuation(path: Path) -> StageContinuationAuthorization:
    try:
        raw = json.loads(path.read_bytes())
        if not isinstance(raw, dict) or set(raw) != set(
            StageContinuationAuthorization.__dataclass_fields__
        ):
            raise ValueError("authorization fields differ")
        return StageContinuationAuthorization(**raw)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise FactorialError("Stage B continuation authorization is malformed") from exc


@dataclass(frozen=True, slots=True)
class _RetainedTaskView:
    """Inertly checked retained task with only selected decisions reconstructed."""

    task_index: int
    source_id: SourceId
    symbol: TradingSymbol
    family: str
    context_start: datetime
    follow_up_end: datetime
    decisions: dict[int, DecisionJournalEntry[object, object]]
    contexts: tuple[HistoricalStrategyContextObservation, ...]


class _RetainedTaskExecutionSource:
    def __init__(self, view: _RetainedTaskView) -> None:
        self._series = ExecutionSeries(view.source_id, view.symbol, Timeframe.MINUTES_15)
        self._candles = tuple(item.context.candle_15m for item in view.contexts)
        self._opens = tuple(candle.open_time for candle in self._candles)
        expected = view.context_start
        for candle in self._candles:
            if candle.open_time != expected:
                raise FactorialError("retained task execution context has a missing candle")
            expected = candle.close_time
        if expected != view.follow_up_end:
            raise FactorialError("retained task execution context lacks follow-up coverage")

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        if (
            series != self._series
            or not self._candles
            or start < self._opens[0]
            or end > self._candles[-1].close_time
            or start >= end
        ):
            raise FactorialError("execution source differs from retained task")
        selected = self._candles[bisect_left(self._opens, start) : bisect_left(self._opens, end)]
        if not selected:
            raise FactorialError("retained execution snapshot is unexpectedly empty")
        return selected


def _tagged_fields(value: object, expected_type: str) -> dict[str, object]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or value[0] != "dataclass"
        or value[1] != expected_type
        or not isinstance(value[2], list)
    ):
        raise FactorialError("retained authority has the wrong tagged type")
    result: dict[str, object] = {}
    for item in value[2]:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or item[0] in result
        ):
            raise FactorialError("retained authority fields are malformed")
        result[item[0]] = item[1]
    return result


def _tagged_tuple(value: object, field: str) -> tuple[object, ...]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or value[0] != "tuple"
        or not isinstance(value[1], list)
    ):
        raise FactorialError(f"retained {field} is not a tagged tuple")
    return tuple(value[1])


def _load_task_view(
    repo: Path,
    task_index: int,
    selected: tuple[dict[str, Any], ...],
) -> _RetainedTaskView:
    evidence = repo / EVIDENCE_RELATIVE
    files, _ = retained.load_index(evidence)
    paths = _task_paths(task_index)
    population_payload = retained.verify_indexed_file(evidence, paths["population"], files)
    partition_payload = retained.verify_indexed_file(evidence, paths["partition"], files)
    authority_payload = retained.verify_indexed_file(evidence, paths["authority"], files)
    expected = retained._task_expectation(task_index)
    if any(
        (
            reference.get("population_sha256") != _sha(population_payload)
            or reference.get("partition_sha256") != _sha(partition_payload)
            or reference.get("authority_sha256") != _sha(authority_payload)
        )
        for reference in selected
    ):
        raise FactorialError("retained task bytes differ from admitted inventory")
    try:
        inert_population, inert_decisions = retained._population(
            population_payload, expected_count=expected.decision_count
        )
        partition = retained._parse_partition(partition_payload)
        retained._validate_population_task(inert_population, inert_decisions, partition, expected)
        envelope = json.loads(gzip.decompress(population_payload))
        fields_by_name = _tagged_fields(
            envelope["population"],
            "adaptive_trading_bot.research.research_v2_admission.ResearchV2StudyDecisionPopulation",
        )
        tagged_decisions = _tagged_tuple(fields_by_name["decisions"], "decisions")
        tagged_contexts = _tagged_tuple(fields_by_name["contexts"], "contexts")
        decisions = {
            int(reference["decision_index"]): cast(
                DecisionJournalEntry[object, object],
                decode_authority(tagged_decisions[int(reference["decision_index"])]),
            )
            for reference in selected
        }
        contexts = tuple(
            cast(HistoricalStrategyContextObservation, decode_authority(item))
            for item in tagged_contexts
        )
    except (
        OSError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        retained.DiagnosisError,
        ScreenEvidenceError,
    ) as exc:
        raise FactorialError("retained task cannot be reconstructed safely") from exc
    for reference in selected:
        index = int(reference["decision_index"])
        decision = decisions[index]
        decision.validate()
        if str(reference["decision_identity_sha256"]) != _digest(str(decision.identity)):
            raise FactorialError("retained admission changed decision identity")
    if not contexts:
        raise FactorialError("retained task has no causal contexts")
    source_id = cast(SourceId, contexts[0].context.source_id)
    symbol = contexts[0].context.symbol
    return _RetainedTaskView(
        task_index,
        source_id,
        symbol,
        expected.family,
        expected.context_start,
        expected.follow_up_end,
        decisions,
        contexts,
    )


def _execute_unit(
    repo: Path,
    task_index: int,
    cell: FactorialCell,
    inventory: tuple[dict[str, Any], ...],
    output: Path,
    *,
    task_view: _RetainedTaskView | None = None,
) -> tuple[str, dict[str, object]]:
    task = frozen_tasks()[task_index]
    selected = tuple(row for row in inventory if row["task_id"] == task.task_id)
    if not selected:
        payload = gzip.compress(b"", mtime=0)
        digest = _immutable_write(output, payload)
        return digest, {
            "unit_id": f"{task.task_id}:{cell.value}",
            "admission_count": 0,
            "outcome_count": 0,
            "elapsed_seconds": "0",
            "artifact_sha256": digest,
            "artifact_size_bytes": len(payload),
        }
    task_view = _load_task_view(repo, task_index, selected) if task_view is None else task_view
    source = _RetainedTaskExecutionSource(task_view)
    runner = ResearchAllFillReplayRunner(source)
    series = ExecutionSeries(task_view.source_id, task_view.symbol, Timeframe.MINUTES_15)
    observed_times = tuple(item.context.observed_at for item in task_view.contexts)
    candles = tuple(item.context.candle_15m for item in task_view.contexts)
    started = time.monotonic()
    records: list[dict[str, object]] = []
    for reference in selected:
        index = int(reference["decision_index"])
        decision = task_view.decisions[index]
        contexts = task_view.contexts[
            causal_follow_up_slice(observed_times, decision.identity.observed_at)
        ]
        result = execute_factorial_cell(
            runner,
            decision,
            contexts,
            series,
            cell,
            start=(
                task_view.context_start
                if task_view.family == "trend_pullback"
                else decision.identity.observed_at
            ),
            end=decision_cutoff(decision.identity.observed_at),
        )
        records.append(factorial_result_record(result, decision_index=index, candles=candles))
    payload = gzip.compress(b"".join(_canonical(row) + b"\n" for row in records), mtime=0)
    digest = _immutable_write(output, payload)
    return digest, {
        "unit_id": f"{task.task_id}:{cell.value}",
        "admission_count": len(selected),
        "outcome_count": len(records),
        "elapsed_seconds": str(time.monotonic() - started),
        "artifact_sha256": digest,
        "artifact_size_bytes": len(payload),
    }


def _verify_unit_artifact(
    repo: Path,
    unit: dict[str, Any],
    inventory: tuple[dict[str, Any], ...],
    artifact: Path,
    *,
    task_view: _RetainedTaskView | None = None,
) -> tuple[dict[str, object], tuple[FactorialOutcome, ...]]:
    """Rebuild one new-cell artifact from its retained authorities without execution."""

    task_index = int(str(unit["task_id"]).split("-", 1)[0])
    task = frozen_tasks()[task_index]
    selected = tuple(row for row in inventory if row["task_id"] == task.task_id)
    artifact_payload = artifact.read_bytes()
    artifact_sha256 = hashlib.sha256(artifact_payload).hexdigest()
    cell = FactorialCell(str(unit["cell"]))
    try:
        raw_records = tuple(
            json.loads(line) for line in gzip.decompress(artifact_payload).splitlines() if line
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise FactorialError("factorial result artifact is malformed") from exc
    if len(raw_records) != len(selected):
        raise FactorialError("factorial artifact coverage differs from frozen inventory")
    if not selected:
        return (
            {
                "unit_id": str(unit["unit_id"]),
                "admission_count": 0,
                "outcome_count": 0,
                "artifact_sha256": artifact_sha256,
                "artifact_size_bytes": len(artifact_payload),
                "terminal_status_counts": {},
            },
            (),
        )
    task_view = _load_task_view(repo, task_index, selected) if task_view is None else task_view
    candles = tuple(item.context.candle_15m for item in task_view.contexts)
    outcomes: list[FactorialOutcome] = []
    terminal: dict[str, int] = {}
    for reference, record in zip(selected, raw_records, strict=True):
        index = int(reference["decision_index"])
        result = recover_factorial_result(
            record,
            decision=task_view.decisions[index],
            decision_index=index,
            cell=cell,
            candles=candles,
            task_id=task.task_id,
            expected_end=decision_cutoff(task_view.decisions[index].identity.observed_at),
        )
        outcome = outcome_from_factorial_result(
            result,
            task_id=task.task_id,
            source_artifact_sha256=artifact_sha256,
        )
        outcomes.append(outcome)
        terminal[outcome.terminal_status] = terminal.get(outcome.terminal_status, 0) + 1
    return (
        {
            "unit_id": str(unit["unit_id"]),
            "admission_count": len(selected),
            "outcome_count": len(outcomes),
            "artifact_sha256": artifact_sha256,
            "artifact_size_bytes": len(artifact_payload),
            "terminal_status_counts": dict(sorted(terminal.items())),
        },
        tuple(outcomes),
    )


def _verify_completed_prefix(
    repo: Path,
    plan: dict[str, Any],
    ledger: FactorialLedgerStore,
    *,
    end: int,
) -> tuple[tuple[dict[str, object], ...], tuple[FactorialOutcome, ...]]:
    state = ledger.load()
    units = _task_units(plan)
    if (
        end < 0
        or end > len(units)
        or any(value != "COMPLETED" for value in state.task_states[:end])
        or any(value != "NOT_STARTED" for value in state.task_states[end:])
    ):
        raise FactorialError("factorial verification requires one clean completed prefix")
    summaries: list[dict[str, object]] = []
    outcomes: list[FactorialOutcome] = []
    cached_index: int | None = None
    task_view: _RetainedTaskView | None = None
    for index, unit in enumerate(units[:end]):
        task_index = int(str(unit["task_id"]).split("-", 1)[0])
        if cached_index != task_index:
            selected = tuple(
                row
                for row in _inventory(plan["admitted_inventory"])
                if row["task_id"] == str(unit["task_id"])
            )
            task_view = None if not selected else _load_task_view(repo, task_index, selected)
            cached_index = task_index
        summary, unit_outcomes = _verify_unit_artifact(
            repo,
            unit,
            _inventory(plan["admitted_inventory"]),
            ledger.directory / f"{index:02d}.factorial.jsonl.gz",
            task_view=task_view,
        )
        summaries.append(summary)
        outcomes.extend(unit_outcomes)
    return tuple(summaries), tuple(outcomes)


def _task_units(plan: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(plan["new_task_cell_inventory"])


def _admitted_references(plan: dict[str, Any]) -> tuple[AdmittedDecisionReference, ...]:
    return tuple(
        AdmittedDecisionReference(
            task_id=str(row["task_id"]),
            decision_index=int(row["decision_index"]),
            decision_identity_sha256=str(row["decision_identity_sha256"]),
            symbol=str(row["symbol"]),
            family=str(row["family"]),
            observed_at=datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00")),
            original_mask_member=bool(row["original_mask_member"]),
            population_sha256=str(row["population_sha256"]),
            partition_sha256=str(row["partition_sha256"]),
            authority_sha256=str(row["authority_sha256"]),
            block=str(row.get("block", "")),
            week=str(row.get("week", "")),
            side=str(row.get("side", "")),
        )
        for row in _inventory(plan["admitted_inventory"])
    )


def _run_stage_a(repo: Path, grant_path: Path, ledger_root: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    actual_sha = _clean_sha(repo)
    preflight_wall_started = time.monotonic()
    preflight_cpu_started = time.process_time()
    plan = data_only_factorial_plan(repo)
    preflight_wall_seconds = time.monotonic() - preflight_wall_started
    preflight_cpu_seconds = time.process_time() - preflight_cpu_started
    grant = _grant(grant_path.resolve(strict=True))
    grant.validate(actual_sha=actual_sha)
    if (
        grant.input_plan_sha256 != plan["input_plan_sha256"]
        or grant.admitted_inventory_sha256 != plan["admitted_inventory_sha256"]
    ):
        raise FactorialError("grant differs from inert preflight identities")
    units = _task_units(plan)
    stage_a_count = sum(unit["stage"] == FactorialStage.A.value for unit in units)
    ledger = FactorialLedgerStore.create(
        ledger_root.resolve(strict=True),
        grant,
        actual_sha=actual_sha,
        task_ids=tuple(str(unit["unit_id"]) for unit in units),
        stage_a_count=stage_a_count,
    )
    summaries: list[dict[str, object]] = []
    cached_index: int | None = None
    task_view: _RetainedTaskView | None = None
    execution_wall_started = time.monotonic()
    execution_cpu_started = time.process_time()
    with _no_network():
        for index, unit in enumerate(units[:stage_a_count]):
            task_index = int(str(unit["task_id"]).split("-", 1)[0])
            if cached_index != task_index:
                selected = tuple(
                    row
                    for row in _inventory(plan["admitted_inventory"])
                    if row["task_id"] == str(unit["task_id"])
                )
                task_view = None if not selected else _load_task_view(repo, task_index, selected)
                cached_index = task_index
            ledger.start(index)
            digest, summary = _execute_unit(
                repo,
                task_index,
                FactorialCell(str(unit["cell"])),
                _inventory(plan["admitted_inventory"]),
                ledger.directory / f"{index:02d}.factorial.jsonl.gz",
                task_view=task_view,
            )
            ledger.complete(index, digest)
            summaries.append(summary)
    execution_wall_seconds = time.monotonic() - execution_wall_started
    execution_cpu_seconds = time.process_time() - execution_cpu_started
    verification_wall_started = time.monotonic()
    verification_cpu_started = time.process_time()
    verified_units, _ = _verify_completed_prefix(repo, plan, ledger, end=stage_a_count)
    verification_wall_seconds = time.monotonic() - verification_wall_started
    verification_cpu_seconds = time.process_time() - verification_cpu_started
    stage_a_tasks = {str(unit["task_id"]) for unit in units[:stage_a_count]}
    imported_stage_a = tuple(
        row
        for row in _inventory(plan["imported_diagonal_inventory"])
        if str(row["task_id"]) in stage_a_tasks
    )
    if len(imported_stage_a) != 96:
        raise FactorialError("Stage A imported diagonal coverage is not exactly 96 records")
    checkpoint: dict[str, object] = {
        "schema_version": "research-v2-factorial-stage-a-checkpoint-v2",
        "protocol_sha256": PROTOCOL_SHA256,
        "run_uuid": grant.run_uuid,
        "executable_sha": actual_sha,
        "input_plan_sha256": plan["input_plan_sha256"],
        "admitted_inventory_sha256": plan["admitted_inventory_sha256"],
        "stage": "A",
        "technically_complete": True,
        "pnl_continuation_gate": False,
        "units": tuple(summaries),
        "verified_units": verified_units,
        "imported_diagonal_record_count": len(imported_stage_a),
        "imported_diagonal_identity_sha256": _digest(imported_stage_a),
        "resource_observations": {
            "preflight_initialization_wall_seconds": str(preflight_wall_seconds),
            "preflight_initialization_cpu_seconds": str(preflight_cpu_seconds),
            "result_execution_wall_seconds": str(execution_wall_seconds),
            "result_execution_cpu_seconds": str(execution_cpu_seconds),
            "verification_wall_seconds": str(verification_wall_seconds),
            "verification_cpu_seconds": str(verification_cpu_seconds),
            "artifact_bytes": sum(
                _exact_int(row["artifact_size_bytes"], field="artifact size")
                for row in verified_units
            ),
            "admissions": sum(
                _exact_int(row["admission_count"], field="admission count")
                for row in verified_units[::2]
            ),
            "new_outcomes": sum(
                _exact_int(row["outcome_count"], field="outcome count") for row in verified_units
            ),
            "matrix_records_with_imported_diagonals": (
                len(imported_stage_a)
                + sum(
                    _exact_int(row["outcome_count"], field="outcome count")
                    for row in verified_units
                )
            ),
        },
        "resource_note": (
            "Stage A is an unequal two-asset prefix; any Stage B estimate is qualified and "
            "must account for family/admission workload, parsing and verification separately."
        ),
    }
    checkpoint["checkpoint_sha256"] = _digest(checkpoint)
    checkpoint_payload = _canonical(checkpoint)
    _immutable_write(ledger.directory / "stage-a-checkpoint.json", checkpoint_payload)
    ledger.pause_after_stage_a(str(checkpoint["checkpoint_sha256"]))
    return checkpoint


def _continue_stage_b(
    repo: Path,
    grant_path: Path,
    authorization_path: Path,
    ledger_dir: Path,
) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    actual_sha = _clean_sha(repo)
    plan = data_only_factorial_plan(repo)
    grant = _grant(grant_path.resolve(strict=True))
    grant.validate(actual_sha=actual_sha)
    if (
        grant.input_plan_sha256 != plan["input_plan_sha256"]
        or grant.admitted_inventory_sha256 != plan["admitted_inventory_sha256"]
    ):
        raise FactorialError("Stage B grant differs from frozen inert preflight identities")
    ledger = FactorialLedgerStore(ledger_dir.resolve(strict=True))
    state = ledger.load()
    units = _task_units(plan)
    if (
        state.run_uuid != grant.run_uuid
        or state.executable_sha != grant.executable_sha
        or state.grant_kind != grant.kind
        or state.protocol_sha256 != grant.protocol_sha256
        or state.input_plan_sha256 != grant.input_plan_sha256
        or state.admitted_inventory_sha256 != grant.admitted_inventory_sha256
        or state.source_id != grant.source_id
        or state.dataset_version != grant.dataset_version
        or state.manifest_sha256 != grant.manifest_sha256
        or state.task_ids != tuple(str(unit["unit_id"]) for unit in units)
        or state.stage_a_count != sum(unit["stage"] == FactorialStage.A.value for unit in units)
    ):
        raise FactorialError("Stage B attempted with a different run or executable")
    checkpoint, _ = _validate_stage_a_checkpoint(repo, plan, grant, ledger)
    authorization = _continuation(authorization_path.resolve(strict=True))
    authorization.validate(grant, str(checkpoint["checkpoint_sha256"]))
    ledger.authorize_stage_b(authorization, grant)
    summaries: list[dict[str, object]] = []
    cached_index: int | None = None
    task_view: _RetainedTaskView | None = None
    with _no_network():
        for index, unit in enumerate(units[state.stage_a_count :], start=state.stage_a_count):
            task_index = int(str(unit["task_id"]).split("-", 1)[0])
            if cached_index != task_index:
                selected = tuple(
                    row
                    for row in _inventory(plan["admitted_inventory"])
                    if row["task_id"] == str(unit["task_id"])
                )
                task_view = None if not selected else _load_task_view(repo, task_index, selected)
                cached_index = task_index
            ledger.start(index)
            digest, summary = _execute_unit(
                repo,
                task_index,
                FactorialCell(str(unit["cell"])),
                _inventory(plan["admitted_inventory"]),
                ledger.directory / f"{index:02d}.factorial.jsonl.gz",
                task_view=task_view,
            )
            ledger.complete(index, digest)
            summaries.append(summary)
    return {
        "status": "STAGE_B_COMPLETED; OFFLINE_FINAL_VERIFICATION_REQUIRED",
        "run_uuid": grant.run_uuid,
        "units": tuple(summaries),
    }


def _validate_stage_a_checkpoint(
    repo: Path,
    plan: dict[str, Any],
    grant: FactorialExecutionGrant,
    ledger: FactorialLedgerStore,
) -> tuple[dict[str, Any], tuple[dict[str, object], ...]]:
    """Read and reconstruct the exact persisted Stage-A pause evidence."""

    state = ledger.load()
    units = _task_units(plan)
    expected_ids = tuple(str(unit["unit_id"]) for unit in units)
    checkpoint_path = ledger.directory / "stage-a-checkpoint.json"
    try:
        checkpoint_payload = checkpoint_path.read_bytes()
        checkpoint = json.loads(checkpoint_payload)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FactorialError("Stage A checkpoint is unavailable or malformed") from exc
    asserted = checkpoint.get("checkpoint_sha256") if isinstance(checkpoint, dict) else None
    unsigned = dict(checkpoint) if isinstance(checkpoint, dict) else {}
    unsigned.pop("checkpoint_sha256", None)
    if (
        state.run_state is not FactorialRunState.PAUSED_AFTER_STAGE_A
        or state.run_uuid != grant.run_uuid
        or state.executable_sha != grant.executable_sha
        or state.grant_kind != grant.kind
        or state.protocol_sha256 != grant.protocol_sha256
        or state.input_plan_sha256 != grant.input_plan_sha256
        or state.admitted_inventory_sha256 != grant.admitted_inventory_sha256
        or state.source_id != grant.source_id
        or state.dataset_version != grant.dataset_version
        or state.manifest_sha256 != grant.manifest_sha256
        or state.task_ids != expected_ids
        or state.stage_a_count != sum(unit["stage"] == FactorialStage.A.value for unit in units)
        or grant.input_plan_sha256 != plan["input_plan_sha256"]
        or grant.admitted_inventory_sha256 != plan["admitted_inventory_sha256"]
        or asserted != _digest(unsigned)
        or asserted != state.checkpoint_sha256
        or checkpoint_payload != _canonical(checkpoint)
        or checkpoint.get("run_uuid") != grant.run_uuid
        or checkpoint.get("executable_sha") != grant.executable_sha
        or checkpoint.get("input_plan_sha256") != grant.input_plan_sha256
        or checkpoint.get("admitted_inventory_sha256") != grant.admitted_inventory_sha256
        or checkpoint.get("stage") != "A"
        or checkpoint.get("technically_complete") is not True
    ):
        raise FactorialError("Stage A pause does not bind the frozen experiment")
    verified, _ = _verify_completed_prefix(repo, plan, ledger, end=state.stage_a_count)
    if tuple(checkpoint.get("verified_units", ())) != verified:
        raise FactorialError("Stage A checkpoint differs from reconstructed prefix")
    observations = checkpoint.get("resource_observations")
    if not isinstance(observations, dict) or (
        observations.get("admissions") != 48
        or observations.get("new_outcomes") != 96
        or observations.get("matrix_records_with_imported_diagonals") != 192
    ):
        raise FactorialError("Stage A checkpoint denominators are contradictory")
    return cast(dict[str, Any], checkpoint), verified


def _verify_stage_a(repo: Path, grant_path: Path, ledger_dir: Path) -> dict[str, object]:
    """Read-only verification of the deliberate Stage-A pause boundary."""

    repo = repo.resolve(strict=True)
    actual_sha = _clean_sha(repo)
    plan = data_only_factorial_plan(repo)
    grant = _grant(grant_path.resolve(strict=True))
    grant.validate(actual_sha=actual_sha)
    ledger = FactorialLedgerStore(ledger_dir.resolve(strict=True))
    started = time.monotonic()
    checkpoint, verified = _validate_stage_a_checkpoint(repo, plan, grant, ledger)
    return {
        "status": "STAGE_A_VERIFIED_PAUSE; ARCHITECT_AUTHORIZATION_REQUIRED_FOR_STAGE_B",
        "run_uuid": grant.run_uuid,
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "verified_units": verified,
        "verification_wall_seconds": str(time.monotonic() - started),
        "economic_continuation_gate": False,
        "ten_member_classification": None,
    }


def _build_final_projection(
    repo: Path,
    plan: dict[str, Any],
    grant: FactorialExecutionGrant,
    ledger: FactorialLedgerStore,
) -> dict[str, object]:
    """Reconstruct the final report read-only from precise retained artifacts."""

    state = ledger.load()
    units = _task_units(plan)
    if (
        state.run_uuid != grant.run_uuid
        or state.executable_sha != grant.executable_sha
        or state.grant_kind != grant.kind
        or state.input_plan_sha256 != grant.input_plan_sha256
        or state.admitted_inventory_sha256 != grant.admitted_inventory_sha256
        or state.source_id != grant.source_id
        or state.dataset_version != grant.dataset_version
        or state.manifest_sha256 != grant.manifest_sha256
        or state.task_ids != tuple(str(unit["unit_id"]) for unit in units)
        or any(value != "COMPLETED" for value in state.task_states)
        or state.checkpoint_sha256 is None
    ):
        raise FactorialError("full factorial run is not eligible for final verification")
    verified_units, new_outcomes = _verify_completed_prefix(repo, plan, ledger, end=len(units))
    imported_outcomes = tuple(
        imported_outcome_from_record(row) for row in _inventory(plan["imported_diagonal_inventory"])
    )
    projection = project_factorial_matrix(
        _admitted_references(plan),
        (*imported_outcomes, *new_outcomes),
        complete=True,
    )
    projection["run_uuid"] = grant.run_uuid
    projection["executable_sha"] = grant.executable_sha
    projection["input_kind"] = grant.kind
    projection["input_plan_sha256"] = plan["input_plan_sha256"]
    projection["admitted_inventory_sha256"] = plan["admitted_inventory_sha256"]
    projection["imported_diagonal_inventory_sha256"] = plan["imported_diagonal_inventory_sha256"]
    projection["new_artifact_identities"] = tuple(
        {
            "unit_id": row["unit_id"],
            "artifact_sha256": row["artifact_sha256"],
            "artifact_size_bytes": row["artifact_size_bytes"],
        }
        for row in verified_units
    )
    projection["verified_units"] = verified_units
    projection["projection_sha256"] = _digest(
        {key: value for key, value in projection.items() if key != "projection_sha256"}
    )
    return projection


def _finalize_verified_run(repo: Path, grant_path: Path, ledger_dir: Path) -> dict[str, object]:
    """Verify all new outcomes, join immutable diagonals, and finalize once."""

    repo = repo.resolve(strict=True)
    actual_sha = _clean_sha(repo)
    plan = data_only_factorial_plan(repo)
    grant = _grant(grant_path.resolve(strict=True))
    grant.validate(actual_sha=actual_sha)
    if (
        grant.input_plan_sha256 != plan["input_plan_sha256"]
        or grant.admitted_inventory_sha256 != plan["admitted_inventory_sha256"]
    ):
        raise FactorialError("final verification differs from frozen preflight identities")
    ledger = FactorialLedgerStore(ledger_dir.resolve(strict=True))
    if ledger.load().run_state is not FactorialRunState.ACTIVE:
        raise FactorialError("factorial run is not eligible for finalization")
    projection = _build_final_projection(repo, plan, grant, ledger)
    payload = _canonical(projection)
    _immutable_write(ledger.directory / "projection.json", payload)
    ledger.finalize(str(projection["projection_sha256"]))
    return projection


def _verify_finalized_run(repo: Path, grant_path: Path, ledger_dir: Path) -> dict[str, object]:
    """Verify a finalized run without writing, replaying entry populations, or resealing."""

    repo = repo.resolve(strict=True)
    actual_sha = _clean_sha(repo)
    plan = data_only_factorial_plan(repo)
    grant = _grant(grant_path.resolve(strict=True))
    grant.validate(actual_sha=actual_sha)
    ledger = FactorialLedgerStore(ledger_dir.resolve(strict=True))
    state = ledger.load()
    if state.run_state is not FactorialRunState.FINALIZED:
        raise FactorialError("read-only final verification requires a finalized ledger")
    expected = _build_final_projection(repo, plan, grant, ledger)
    path = ledger.directory / "projection.json"
    try:
        payload = path.read_bytes()
        stored = json.loads(payload)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FactorialError("final projection is unavailable or malformed") from exc
    if (
        payload != _canonical(stored)
        or stored != expected
        or stored.get("projection_sha256") != state.final_projection_sha256
    ):
        raise FactorialError("final projection differs from reconstructed authority")
    return {
        "status": "FINALIZED_RUN_VERIFIED_READ_ONLY",
        "run_uuid": grant.run_uuid,
        "projection_sha256": state.final_projection_sha256,
        "record_count": stored.get("record_count"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--repo", required=True, type=Path)
    preflight.add_argument("--output", type=Path)
    stage_a = commands.add_parser("run-stage-a")
    stage_a.add_argument("--repo", required=True, type=Path)
    stage_a.add_argument("--grant", required=True, type=Path)
    stage_a.add_argument("--ledger-root", required=True, type=Path)
    stage_b = commands.add_parser("continue-stage-b")
    stage_b.add_argument("--repo", required=True, type=Path)
    stage_b.add_argument("--grant", required=True, type=Path)
    stage_b.add_argument("--authorization", required=True, type=Path)
    stage_b.add_argument("--ledger", required=True, type=Path)
    verify_stage_a = commands.add_parser("verify-stage-a")
    verify_stage_a.add_argument("--repo", required=True, type=Path)
    verify_stage_a.add_argument("--grant", required=True, type=Path)
    verify_stage_a.add_argument("--ledger", required=True, type=Path)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--repo", required=True, type=Path)
    finalize.add_argument("--grant", required=True, type=Path)
    finalize.add_argument("--ledger", required=True, type=Path)
    verify_finalized = commands.add_parser("verify-finalized")
    verify_finalized.add_argument("--repo", required=True, type=Path)
    verify_finalized.add_argument("--grant", required=True, type=Path)
    verify_finalized.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = data_only_factorial_plan(args.repo)
        if args.output is not None:
            _immutable_write(args.output, _canonical(result))
    elif args.command == "run-stage-a":
        result = _run_stage_a(args.repo, args.grant, args.ledger_root)
    elif args.command == "continue-stage-b":
        result = _continue_stage_b(args.repo, args.grant, args.authorization, args.ledger)
    elif args.command == "verify-stage-a":
        result = _verify_stage_a(args.repo, args.grant, args.ledger)
    elif args.command == "finalize":
        result = _finalize_verified_run(args.repo, args.grant, args.ledger)
    else:
        result = _verify_finalized_run(args.repo, args.grant, args.ledger)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
