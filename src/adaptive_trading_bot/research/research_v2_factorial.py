"""Frozen 2x2 cost/eligibility experiment over retained Research V2 admissions.

This module is research-only.  It does not discover candidates and it does not
grant execution.  The two diagonal cells are immutable references to Issue
#187; only the two off-diagonal cells may be produced after a separate grant.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from hmac import compare_digest
from pathlib import Path
from typing import Any, cast

from adaptive_trading_bot.backtest import ExecutionCostConfiguration, ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.research.all_fill_replay import (
    POLICY_VERSION as ALL_FILL_POLICY_VERSION,
)
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillBranch,
    AllFillFamily,
    AllFillStatus,
    ResearchAllFillReplayRunner,
    _family,
    _snapshot_digest,
)
from adaptive_trading_bot.research.breakout_retest_replay import BreakoutRetestReplayResult
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryIntentProvenance,
    HistoricalStrategyContextObservation,
    PairedTrendPullbackExitReplayResult,
)
from adaptive_trading_bot.research.range_mean_reversion_replay import (
    RangeMeanReversionReplayResult,
)
from adaptive_trading_bot.research.research_v2_admission import (
    DATASET_VERSION,
    MANIFEST_SHA256,
    PANEL,
    SOURCE_ID,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    _canonical,
    _digest,
    _iso,
    frozen_tasks,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    ScreenEvidenceError,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    decode as decode_authority,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    encode as encode_authority,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _integrity_seal

PROTOCOL_VERSION = "RESEARCH_V2_EXECUTION_FACTORIAL_V1"
SCHEMA_VERSION = "research-v2-execution-factorial-v1"
RESULT_SCHEMA_VERSION = "research-v2-factorial-all-fill-result-v1"
LEDGER_SCHEMA_VERSION = "research-v2-factorial-ledger-v1"
ORIGINAL_ARTIFACT_INDEX_SHA256 = "677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2"
ORIGINAL_PROJECTION_SHA256 = "46d7e10899f06f4d16b6ded46f8cba5c62d9d25c119d05e35ca11b5af5833667"
ORIGINAL_PROTOCOL_SHA256 = "ab78c022616bcffdb7af8b835064a14409e1d47bdc3e31649192de9cf0c63e1c"
ORIGINAL_TASK_PLAN_SHA256 = "03376c954ca51ca50754d925189cad7be55f14249cfec29e6cbbe710a1fcfca1"
DIAGNOSIS_MANIFEST_SHA256 = "dd7da381670944844a46e46ee23bcb540707f7e466120c7dedcf493cd294e637"
DIAGNOSIS_SUMMARY_SHA256 = "8b9f614655adc5481eb6329a0e9d1ff7d7b757fe6d15ba6f606c5ff92540486d"
EXPECTED_ADMISSIONS = 246
EXPECTED_NEW_OUTCOMES = 492
EXPECTED_MATRIX_RECORDS = 984
STAGE_A_SYMBOLS = ("BTCUSDT", "ETHUSDT")
STAGE_B_SYMBOLS = PANEL[2:]
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class FactorialError(ValueError):
    """The frozen factorial protocol or evidence failed closed."""


class FactorialCell(StrEnum):
    C0L0 = "C0L0"
    C1L0 = "C1L0"
    C0L1 = "C0L1"
    C1L1 = "C1L1"


class FactorialStage(StrEnum):
    A = "A"
    B = "B"


class FactorialRunState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED_AFTER_STAGE_A = "PAUSED_AFTER_STAGE_A"
    FINALIZED = "FINALIZED"


def cell_costs(cell: FactorialCell) -> ExecutionCostConfiguration:
    values = {
        FactorialCell.C0L0: ("2", "2", "10", 0),
        FactorialCell.C1L0: ("4", "4", "20", 0),
        FactorialCell.C0L1: ("2", "2", "10", 1),
        FactorialCell.C1L1: ("4", "4", "20", 1),
    }[cell]
    return ExecutionCostConfiguration(
        Decimal(values[0]),
        Decimal(values[1]),
        Decimal(values[2]),
        timedelta(seconds=values[3]),
    )


def is_imported_cell(cell: FactorialCell) -> bool:
    return cell in (FactorialCell.C0L0, FactorialCell.C1L1)


def stage_for_symbol(symbol: str) -> FactorialStage:
    if symbol in STAGE_A_SYMBOLS:
        return FactorialStage.A
    if symbol in STAGE_B_SYMBOLS:
        return FactorialStage.B
    raise FactorialError("symbol is outside the frozen ten-member panel")


def protocol_facts() -> dict[str, object]:
    return {
        "version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "input": {
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "artifact_index_sha256": ORIGINAL_ARTIFACT_INDEX_SHA256,
            "projection_sha256": ORIGINAL_PROJECTION_SHA256,
            "original_protocol_sha256": ORIGINAL_PROTOCOL_SHA256,
            "original_task_plan_sha256": ORIGINAL_TASK_PLAN_SHA256,
            "diagnosis_manifest_sha256": DIAGNOSIS_MANIFEST_SHA256,
            "diagnosis_summary_sha256": DIAGNOSIS_SUMMARY_SHA256,
        },
        "panel": PANEL,
        "tasks": tuple(task.to_record() for task in frozen_tasks()),
        "cells": tuple(
            {
                "cell": cell.value,
                "costs": (
                    str(cell_costs(cell).spread_bps),
                    str(cell_costs(cell).slippage_bps),
                    str(cell_costs(cell).fee_bps),
                    str(cell_costs(cell).latency.total_seconds()),
                ),
                "source": "IMPORTED_ISSUE_187" if is_imported_cell(cell) else "NEW_GRANTED",
            }
            for cell in FactorialCell
        ),
        "stages": (
            {"stage": "A", "symbols": STAGE_A_SYMBOLS, "task_count": 6},
            {"stage": "B", "symbols": STAGE_B_SYMBOLS, "task_count": 24},
        ),
        "controls": {
            "admissions": EXPECTED_ADMISSIONS,
            "new_outcomes": EXPECTED_NEW_OUTCOMES,
            "matrix_records": EXPECTED_MATRIX_RECORDS,
            "per_decision_follow_up_hours": 168,
        },
        "checkpoint": {
            "condition": "TECHNICAL_VALIDITY_AND_RESOURCE_FEASIBILITY_ONLY",
            "pause": "MANDATORY_AFTER_STAGE_A",
            "continuation": "SEPARATE_ARCHITECT_AUTHORIZATION_SAME_UUID_LEDGER",
            "pnl_gate": False,
        },
        "interpretation": "POST_RESULT_CONDITIONAL_SIMULATION_NOT_TRADING_AUTHORITY",
    }


PROTOCOL_SHA256 = _digest(protocol_facts())


@dataclass(frozen=True, slots=True)
class AdmittedDecisionReference:
    task_id: str
    decision_index: int
    decision_identity_sha256: str
    symbol: str
    family: str
    observed_at: datetime
    original_mask_member: bool
    population_sha256: str
    partition_sha256: str
    authority_sha256: str

    def to_record(self) -> dict[str, object]:
        require_utc(self.observed_at, field="admitted decision time")
        if (
            self.task_id not in {task.task_id for task in frozen_tasks()}
            or self.decision_index < 0
            or self.symbol not in PANEL
            or self.family not in {task.family.upper() for task in frozen_tasks()}
            or not all(
                _HEX64.fullmatch(value)
                for value in (
                    self.decision_identity_sha256,
                    self.population_sha256,
                    self.partition_sha256,
                    self.authority_sha256,
                )
            )
        ):
            raise FactorialError("admitted-decision reference is malformed")
        return {
            "task_id": self.task_id,
            "decision_index": self.decision_index,
            "decision_identity_sha256": self.decision_identity_sha256,
            "symbol": self.symbol,
            "family": self.family,
            "observed_at": _iso(self.observed_at),
            "original_mask_member": self.original_mask_member,
            "population_sha256": self.population_sha256,
            "partition_sha256": self.partition_sha256,
            "authority_sha256": self.authority_sha256,
        }


def admitted_inventory_digest(inventory: tuple[AdmittedDecisionReference, ...]) -> str:
    records = tuple(item.to_record() for item in inventory)
    if len(records) != len({(row["task_id"], row["decision_index"]) for row in records}):
        raise FactorialError("admitted inventory contains duplicate source decisions")
    return _digest(records)


def stage_counts(inventory: tuple[AdmittedDecisionReference, ...]) -> dict[str, object]:
    if len(inventory) != EXPECTED_ADMISSIONS:
        raise FactorialError("retained inventory does not contain exactly 246 admissions")
    expected_order = {task.task_id: task.index for task in frozen_tasks()}
    if tuple(inventory) != tuple(
        sorted(inventory, key=lambda row: (expected_order[row.task_id], row.decision_index))
    ):
        raise FactorialError("admitted inventory is not in frozen task/decision order")
    by_stage: dict[str, dict[str, int]] = {}
    for stage in FactorialStage:
        members = tuple(item for item in inventory if stage_for_symbol(item.symbol) is stage)
        task_ids = tuple(
            task.task_id for task in frozen_tasks() if stage_for_symbol(task.wire_symbol) is stage
        )
        by_stage[stage.value] = {
            "task_count": len(task_ids),
            "admission_count": len(members),
            "new_outcome_count": len(members) * 2,
            "matrix_record_count": len(members) * 4,
            "zero_admission_task_count": sum(
                not any(item.task_id == task_id for item in members) for task_id in task_ids
            ),
        }
    if (
        sum(row["admission_count"] for row in by_stage.values()) != EXPECTED_ADMISSIONS
        or sum(row["new_outcome_count"] for row in by_stage.values()) != EXPECTED_NEW_OUTCOMES
        or sum(row["matrix_record_count"] for row in by_stage.values()) != EXPECTED_MATRIX_RECORDS
    ):
        raise FactorialError("staged inventory does not reconcile to full controls")
    return {
        "stages": by_stage,
        "full": {
            "admission_count": EXPECTED_ADMISSIONS,
            "new_outcome_count": EXPECTED_NEW_OUTCOMES,
            "matrix_record_count": EXPECTED_MATRIX_RECORDS,
            "task_count": 30,
        },
    }


@dataclass(frozen=True, slots=True)
class FactorialAllFillReplayResult:
    """Off-diagonal all-fill result built only by the shared replay core."""

    decision: DecisionJournalEntry[object, object]
    family: AllFillFamily
    series: ExecutionSeries
    costs: ExecutionCostConfiguration
    cell: FactorialCell
    start: datetime
    end: datetime
    status: AllFillStatus
    entry_intent: EntryIntentProvenance | None
    entry_unfilled: object | None
    branches: tuple[AllFillBranch, ...]
    execution_snapshot: tuple[Candle, ...]
    execution_snapshot_digest: str
    legacy_replay: (
        PairedTrendPullbackExitReplayResult
        | BreakoutRetestReplayResult
        | RangeMeanReversionReplayResult
        | None
    )
    schema_version: str = field(init=False, default=RESULT_SCHEMA_VERSION)
    all_fill_policy_version: str = field(init=False, default=ALL_FILL_POLICY_VERSION)
    factorial_protocol_sha256: str = field(init=False, default=PROTOCOL_SHA256)
    is_trading_authority: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-v2-factorial-all-fill-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-v2-factorial-all-fill-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise FactorialError("factorial all-fill result integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.decision.validate()
        self.costs.validate()
        require_utc(self.start, field="factorial start")
        require_utc(self.end, field="factorial end")
        if (
            self.schema_version != RESULT_SCHEMA_VERSION
            or self.all_fill_policy_version != ALL_FILL_POLICY_VERSION
            or self.factorial_protocol_sha256 != PROTOCOL_SHA256
            or self.is_trading_authority
            or is_imported_cell(self.cell)
            or self.costs != cell_costs(self.cell)
            or self.start >= self.end
            or not self.start <= self.decision.identity.observed_at < self.end
            or self.series.source_id != self.decision.identity.source_id
            or self.series.symbol != self.decision.identity.symbol
            or self.series.timeframe is not Timeframe.MINUTES_15
            or self.family is not _family(self.decision)
            or not isinstance(self.execution_snapshot, tuple)
            or not compare_digest(
                self.execution_snapshot_digest, _snapshot_digest(self.execution_snapshot)
            )
        ):
            raise FactorialError("factorial result differs from frozen cell authority")
        authorization = EntryOrderIntentAuthorizer().authorize(self.decision)
        if authorization.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT:
            raise FactorialError("factorial result has inconsistent decision authority")
        if authorization.status is OrderIntentAuthorizationStatus.NO_INTENT:
            if any(
                (
                    self.entry_intent is not None,
                    self.entry_unfilled is not None,
                    bool(self.branches),
                    bool(self.execution_snapshot),
                    self.legacy_replay is not None,
                )
            ):
                raise FactorialError("non-admitted factorial result contains replay evidence")
            return
        if authorization.intent is None or self.entry_intent is None or self.legacy_replay is None:
            raise FactorialError("admitted factorial result lacks authority")
        if self.entry_intent != EntryIntentProvenance.from_authorized_intent(authorization.intent):
            raise FactorialError("factorial entry provenance differs from authorizer")
        self.legacy_replay.validate()
        if (
            self.legacy_replay.entry_intent != self.entry_intent
            or self.legacy_replay.execution_costs != self.costs
            or self.legacy_replay.execution_series != self.series
            or self.legacy_replay.end != self.end
        ):
            raise FactorialError("factorial replay is not bound to declared cell")
        if self.entry_unfilled is not None:
            if self.status is not AllFillStatus.ENTRY_UNFILLED or self.branches:
                raise FactorialError("factorial unfilled result has accounted branches")
            return
        if not self.branches or self.status is not self.branches[-1].status:
            raise FactorialError("factorial primary branch is incomplete")
        for branch in self.branches:
            branch.validate()
            if (
                branch.approved_risk_quote != self.entry_intent.approved_risk_quote
                or branch.final_position.observed_at != self.end
                or branch.entry_fill != self.legacy_replay.entry_fill
            ):
                raise FactorialError("factorial accounting branch differs from replay")


def execute_factorial_cell(
    runner: ResearchAllFillReplayRunner,
    decision: DecisionJournalEntry[object, object],
    contexts: tuple[HistoricalStrategyContextObservation, ...],
    series: ExecutionSeries,
    cell: FactorialCell,
    *,
    start: datetime,
    end: datetime,
) -> FactorialAllFillReplayResult:
    """Run one missing cell through the unchanged shared replay orchestration."""

    if is_imported_cell(cell):
        raise FactorialError("original diagonal outcomes must be referenced, never re-executed")
    result = runner._run_with_result_factory(
        decision,
        contexts,
        series,
        cell_costs(cell),
        start=start,
        end=end,
        result_label=cell,
        result_factory=FactorialAllFillReplayResult,
    )
    if not isinstance(result, FactorialAllFillReplayResult):
        raise FactorialError("shared replay core returned the wrong result schema")
    result.validate()
    return result


def factorial_result_record(
    result: FactorialAllFillReplayResult,
    *,
    decision_index: int,
    candles: tuple[Candle, ...],
) -> dict[str, object]:
    """Encode one result with bounded decision/candle references."""

    result.validate()
    opens = tuple(candle.open_time for candle in candles)
    snapshot = result.execution_snapshot
    if snapshot:
        first = bisect_left(opens, snapshot[0].open_time)
        last = first + len(snapshot)
        if candles[first:last] != snapshot:
            raise FactorialError("factorial snapshot differs from stored task population")
    else:
        first = last = 0
    encoded = tuple(
        (
            item.name,
            ("decision_ref",)
            if item.name == "decision"
            else ("candle_slice", first, last)
            if item.name == "execution_snapshot"
            else encode_authority(getattr(result, item.name)),
        )
        for item in fields(result)
    )
    record: dict[str, object] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "decision_index": decision_index,
        "cell": result.cell.value,
        "result": (
            "dataclass",
            f"{type(result).__module__}.{type(result).__qualname__}",
            encoded,
        ),
    }
    record["record_sha256"] = _digest(record)
    return record


def recover_factorial_result(
    record: object,
    *,
    decision: DecisionJournalEntry[object, object],
    decision_index: int,
    cell: FactorialCell,
    candles: tuple[Candle, ...],
) -> FactorialAllFillReplayResult:
    if not isinstance(record, dict):
        raise FactorialError("factorial authority record is not an object")
    value = dict(record)
    asserted = value.pop("record_sha256", None)
    if (
        asserted != _digest(value)
        or value.get("schema_version") != RESULT_SCHEMA_VERSION
        or value.get("decision_index") != decision_index
        or value.get("cell") != cell.value
    ):
        raise FactorialError("factorial authority slot is missing or relabeled")
    try:
        result = decode_authority(value["result"], decision=decision, candles=candles)
    except (KeyError, ScreenEvidenceError) as exc:
        raise FactorialError("factorial result failed bounded reconstruction") from exc
    if not isinstance(result, FactorialAllFillReplayResult):
        raise FactorialError("factorial authority is not the versioned result type")
    result.validate()
    return result


@dataclass(frozen=True, slots=True)
class FactorialOutcome:
    decision_identity_sha256: str
    task_id: str
    symbol: str
    family: str
    cell: FactorialCell
    imported: bool
    terminal_status: str
    closed_net_r: Decimal | None
    incurred_fees_quote: Decimal
    gross_realized_pnl_quote: Decimal | None
    net_realized_pnl_quote: Decimal | None
    residual_base_quantity: Decimal
    result_sha256: str
    source_artifact_sha256: str

    def validate(self) -> None:
        task = next((item for item in frozen_tasks() if item.task_id == self.task_id), None)
        if (
            not _HEX64.fullmatch(self.decision_identity_sha256)
            or not _HEX64.fullmatch(self.result_sha256)
            or not _HEX64.fullmatch(self.source_artifact_sha256)
            or self.imported != is_imported_cell(self.cell)
            or self.symbol not in PANEL
            or task is None
            or (self.symbol, self.family) != (task.wire_symbol, task.family.upper())
            or not self.terminal_status
            or (self.closed_net_r is not None and not self.closed_net_r.is_finite())
            or not self.incurred_fees_quote.is_finite()
            or self.incurred_fees_quote < 0
            or (
                self.gross_realized_pnl_quote is not None
                and not self.gross_realized_pnl_quote.is_finite()
            )
            or (
                self.net_realized_pnl_quote is not None
                and not self.net_realized_pnl_quote.is_finite()
            )
            or not self.residual_base_quantity.is_finite()
            or self.residual_base_quantity < 0
        ):
            raise FactorialError("factorial outcome is malformed or relabeled")


def outcome_from_factorial_result(
    result: FactorialAllFillReplayResult,
    *,
    task_id: str,
    decision_identity_sha256: str,
    source_artifact_sha256: str,
) -> FactorialOutcome:
    """Project compact facts only from a validated authoritative result."""

    result.validate()
    branch = result.branches[-1] if result.branches else None
    outcome = FactorialOutcome(
        decision_identity_sha256,
        task_id,
        f"{result.decision.identity.symbol.base}{result.decision.identity.symbol.quote}",
        result.family.value,
        result.cell,
        False,
        result.status.value,
        None if branch is None else branch.closed_net_r,
        Decimal(0) if branch is None else branch.final_position.cumulative_fees,
        None if branch is None else branch.final_position.cumulative_gross_realized_pnl,
        None if branch is None else branch.final_position.cumulative_net_realized_pnl,
        Decimal(0) if branch is None else branch.final_position.quantity.copy_abs(),
        result.integrity_seal,
        source_artifact_sha256,
    )
    outcome.validate()
    return outcome


def verify_outcome_against_factorial_result(
    outcome: FactorialOutcome,
    result: FactorialAllFillReplayResult,
    *,
    task_id: str,
    decision_identity_sha256: str,
    source_artifact_sha256: str,
) -> None:
    expected = outcome_from_factorial_result(
        result,
        task_id=task_id,
        decision_identity_sha256=decision_identity_sha256,
        source_artifact_sha256=source_artifact_sha256,
    )
    if outcome != expected:
        raise FactorialError("compact factorial row differs from replay/accounting authority")


def imported_outcome_from_record(record: object) -> FactorialOutcome:
    """Restore one immutable Issue-#187 diagonal row without replaying it."""

    if not isinstance(record, dict):
        raise FactorialError("imported diagonal record is malformed")

    def decimal_value(name: str, *, zero_if_none: bool = False) -> Decimal | None:
        value = record.get(name)
        if value is None:
            return Decimal(0) if zero_if_none else None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise FactorialError("imported diagonal Decimal is malformed") from exc
        if not parsed.is_finite():
            raise FactorialError("imported diagonal Decimal is non-finite")
        return parsed

    try:
        cell = FactorialCell(str(record["cell"]))
    except (KeyError, ValueError) as exc:
        raise FactorialError("imported diagonal record is malformed") from exc
    if not is_imported_cell(cell):
        raise FactorialError("new factorial cell cannot be imported")
    try:
        outcome = FactorialOutcome(
            str(record["decision_identity_sha256"]),
            str(record["task_id"]),
            str(record["symbol"]),
            str(record["family"]),
            cell,
            True,
            str(record["terminal_status"]),
            decimal_value("closed_net_r"),
            cast(Decimal, decimal_value("incurred_fees_quote", zero_if_none=True)),
            decimal_value("gross_realized_pnl_quote"),
            decimal_value("net_realized_pnl_quote"),
            cast(Decimal, decimal_value("residual_base_quantity", zero_if_none=True)),
            str(record["result_sha256"]),
            str(record["authority_sha256"]),
        )
    except (KeyError, ValueError) as exc:
        raise FactorialError("imported diagonal record is malformed") from exc
    outcome.validate()
    return outcome


def project_factorial_matrix(
    inventory: tuple[AdmittedDecisionReference, ...],
    outcomes: tuple[FactorialOutcome, ...],
    *,
    complete: bool,
) -> dict[str, object]:
    """Project fixed contrasts without replacing unavailable closed outcomes by zero."""

    stage_counts(inventory)
    references = {(item.decision_identity_sha256, item.task_id): item for item in inventory}
    seen: dict[tuple[str, str], dict[FactorialCell, FactorialOutcome]] = {
        key: {} for key in references
    }
    for outcome in outcomes:
        outcome.validate()
        key = (outcome.decision_identity_sha256, outcome.task_id)
        if key not in seen or outcome.cell in seen[key]:
            raise FactorialError("factorial outcome is orphaned or duplicated")
        reference = references[key]
        if (outcome.symbol, outcome.family) != (reference.symbol, reference.family):
            raise FactorialError("factorial outcome changed decision provenance")
        if outcome.imported and outcome.source_artifact_sha256 != reference.authority_sha256:
            raise FactorialError("imported factorial outcome changed authority artifact")
        seen[key][outcome.cell] = outcome
    required = set(FactorialCell)
    if complete and any(set(cells) != required for cells in seen.values()):
        raise FactorialError("complete factorial projection has missing cells")
    contrasts: list[dict[str, object]] = []
    status_transitions: dict[str, int] = {}
    residual_by_asset_cell: dict[str, Decimal] = {}
    fees_by_cell: dict[str, Decimal] = {}
    task_cell_counts = {
        f"{task.task_id}:{cell.value}": 0 for task in frozen_tasks() for cell in FactorialCell
    }
    for key, cells in seen.items():
        for outcome in cells.values():
            status_transitions[outcome.terminal_status] = (
                status_transitions.get(outcome.terminal_status, 0) + 1
            )
            task_cell_counts[f"{outcome.task_id}:{outcome.cell.value}"] += 1
            residual_key = f"{outcome.symbol}:{outcome.cell.value}"
            with localcontext(_DECIMAL_CONTEXT):
                residual_by_asset_cell[residual_key] = (
                    residual_by_asset_cell.get(residual_key, Decimal(0))
                    + outcome.residual_base_quantity
                )
                fees_by_cell[outcome.cell.value] = (
                    fees_by_cell.get(outcome.cell.value, Decimal(0)) + outcome.incurred_fees_quote
                )
        if set(cells) != required or any(cells[cell].closed_net_r is None for cell in required):
            continue
        y00 = cast(Decimal, cells[FactorialCell.C0L0].closed_net_r)
        y10 = cast(Decimal, cells[FactorialCell.C1L0].closed_net_r)
        y01 = cast(Decimal, cells[FactorialCell.C0L1].closed_net_r)
        y11 = cast(Decimal, cells[FactorialCell.C1L1].closed_net_r)
        with localcontext(_DECIMAL_CONTEXT):
            cost_l0 = y10 - y00
            cost_l1 = y11 - y01
            eligibility_c0 = y01 - y00
            eligibility_c1 = y11 - y10
            interaction = y11 - y10 - y01 + y00
            combined = y11 - y00
        contrasts.append(
            {
                "decision_identity_sha256": key[0],
                "task_id": key[1],
                "cost_contrast_l0": str(cost_l0),
                "cost_contrast_l1": str(cost_l1),
                "eligibility_contrast_c0": str(eligibility_c0),
                "eligibility_contrast_c1": str(eligibility_c1),
                "interaction": str(interaction),
                "combined_difference": str(combined),
            }
        )
    projection: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "complete": complete,
        "classification": (
            "DESCRIPTIVE_COMPLETE_NO_STRATEGY_PROMOTION"
            if complete
            else "PARTIAL_TECHNICAL_CHECKPOINT_NO_TEN_MEMBER_CLASSIFICATION"
        ),
        "admission_count": len(inventory),
        "record_count": len(outcomes),
        "complete_quadruple_count": len(contrasts),
        "original_mask_complete_quadruple_count": sum(
            1
            for row in contrasts
            if references[
                (str(row["decision_identity_sha256"]), str(row["task_id"]))
            ].original_mask_member
        ),
        "terminal_status_counts": dict(sorted(status_transitions.items())),
        "task_cell_record_counts": dict(sorted(task_cell_counts.items())),
        "incurred_fees_quote_by_cell": {
            cell.value: str(fees_by_cell.get(cell.value, Decimal(0))) for cell in FactorialCell
        },
        "residual_base_quantity_by_asset_cell": {
            key: str(value) for key, value in sorted(residual_by_asset_cell.items())
        },
        "contrasts": tuple(contrasts),
        "warnings": (
            "CLOSURE_ONLY_CONTRASTS_HAVE_DIFFERENT_SUBSETS",
            "OVERLAPPING_AND_CROSS_ASSET_OUTCOMES_ARE_DEPENDENT",
            "FIXED_RECORDED_PATH_SIMULATION_NOT_REAL_WORLD_CAUSAL_ATTRIBUTION",
        ),
    }
    projection["projection_sha256"] = _digest(projection)
    return projection


@dataclass(frozen=True, slots=True)
class FactorialExecutionGrant:
    kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    input_plan_sha256: str
    admitted_inventory_sha256: str
    source_id: str
    dataset_version: str
    manifest_sha256: str

    def validate(self, *, actual_sha: str, synthetic: bool = False) -> None:
        try:
            stable_uuid = str(uuid.UUID(self.run_uuid)) == self.run_uuid
        except (TypeError, ValueError):
            stable_uuid = False
        expected_kind = (
            "SYNTHETIC_FACTORIAL_GRANT" if synthetic else "EXTERNAL_REVIEWED_FACTORIAL_GRANT"
        )
        if (
            self.kind != expected_kind
            or not stable_uuid
            or not _HEX40.fullmatch(self.executable_sha)
            or self.executable_sha != actual_sha
            or self.protocol_sha256 != PROTOCOL_SHA256
            or not _HEX64.fullmatch(self.input_plan_sha256)
            or not _HEX64.fullmatch(self.admitted_inventory_sha256)
            or (
                not synthetic
                and (self.source_id, self.dataset_version, self.manifest_sha256)
                != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
            )
            or (
                synthetic
                and (self.source_id == SOURCE_ID.value or self.manifest_sha256 == MANIFEST_SHA256)
            )
        ):
            raise FactorialError("factorial grant does not bind reviewed code/protocol/input")


@dataclass(frozen=True, slots=True)
class StageContinuationAuthorization:
    kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    input_plan_sha256: str
    admitted_inventory_sha256: str
    stage_a_checkpoint_sha256: str

    def validate(self, grant: FactorialExecutionGrant, checkpoint_sha256: str) -> None:
        if (
            self.kind != "ARCHITECT_AUTHORIZED_STAGE_B_CONTINUATION"
            or self.run_uuid != grant.run_uuid
            or self.executable_sha != grant.executable_sha
            or self.protocol_sha256 != grant.protocol_sha256
            or self.input_plan_sha256 != grant.input_plan_sha256
            or self.admitted_inventory_sha256 != grant.admitted_inventory_sha256
            or self.stage_a_checkpoint_sha256 != checkpoint_sha256
            or not _HEX64.fullmatch(checkpoint_sha256)
        ):
            raise FactorialError("Stage B authorization changed frozen experiment identity")


@dataclass(frozen=True, slots=True)
class FactorialLedgerState:
    run_uuid: str
    executable_sha: str
    task_ids: tuple[str, ...]
    stage_a_count: int
    task_states: tuple[str, ...]
    revision: int
    run_state: FactorialRunState
    checkpoint_sha256: str | None
    final_projection_sha256: str | None


class FactorialLedgerStore:
    """Hash-linked append-only ledger with an explicit verified Stage-A pause."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        grant: FactorialExecutionGrant,
        *,
        actual_sha: str,
        task_ids: tuple[str, ...],
        stage_a_count: int,
        synthetic: bool = False,
    ) -> FactorialLedgerStore:
        grant.validate(actual_sha=actual_sha, synthetic=synthetic)
        if not 0 < stage_a_count < len(task_ids) or len(set(task_ids)) != len(task_ids):
            raise FactorialError("factorial ledger stage inventory is invalid")
        directory = root / grant.run_uuid
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise FactorialError("factorial run UUID already exists") from exc
        store = cls(directory)
        store._write(
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event": "CREATED",
                "revision": 0,
                "previous_sha256": None,
                "run_uuid": grant.run_uuid,
                "executable_sha": grant.executable_sha,
                "protocol_sha256": grant.protocol_sha256,
                "input_plan_sha256": grant.input_plan_sha256,
                "admitted_inventory_sha256": grant.admitted_inventory_sha256,
                "task_ids": task_ids,
                "stage_a_count": stage_a_count,
            }
        )
        return store

    def _write(self, event: dict[str, object]) -> str:
        revision = event.get("revision")
        if type(revision) is not int or revision < 0:
            raise FactorialError("invalid factorial ledger revision")
        destination = self.directory / f"{revision:04d}.json"
        pending = self.directory / f".{revision:04d}.pending"
        if destination.exists() or pending.exists():
            raise FactorialError("factorial ledger revision already exists")
        payload = _canonical(event)
        with pending.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, destination)
        return hashlib.sha256(payload).hexdigest()

    def _events(self) -> list[dict[str, Any]]:
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
        if not files or any(path.name != f"{i:04d}.json" for i, path in enumerate(files)):
            raise FactorialError("factorial ledger event sequence is incomplete")
        if any(self.directory.glob(".*.pending")) or (self.directory / ".transition.lock").exists():
            raise FactorialError("factorial ledger has an interrupted write")
        events: list[dict[str, Any]] = []
        previous: str | None = None
        for index, path in enumerate(files):
            payload = path.read_bytes()
            try:
                event = json.loads(payload)
            except (UnicodeError, ValueError) as exc:
                raise FactorialError("factorial ledger event is malformed") from exc
            if (
                not isinstance(event, dict)
                or _canonical(event) != payload
                or event.get("revision") != index
                or event.get("previous_sha256") != previous
                or event.get("schema_version") != LEDGER_SCHEMA_VERSION
            ):
                raise FactorialError("factorial ledger hash chain is contradictory")
            events.append(event)
            previous = hashlib.sha256(payload).hexdigest()
        return events

    def load(self) -> FactorialLedgerState:
        events = self._events()
        created = events[0]
        if created.get("event") != "CREATED" or created.get("protocol_sha256") != PROTOCOL_SHA256:
            raise FactorialError("factorial ledger creation differs from protocol")
        task_ids = tuple(created.get("task_ids", ()))
        stage_a_count = created.get("stage_a_count")
        if type(stage_a_count) is not int or not 0 < stage_a_count < len(task_ids):
            raise FactorialError("factorial ledger stage boundary is invalid")
        states = ["NOT_STARTED"] * len(task_ids)
        run_state = FactorialRunState.ACTIVE
        checkpoint: str | None = None
        final: str | None = None
        for event in events[1:]:
            name = event.get("event")
            index = event.get("task_index")
            if run_state is FactorialRunState.FINALIZED:
                raise FactorialError("factorial ledger changed after finalization")
            if name == "STAGE_A_CHECKPOINT":
                if (
                    run_state is not FactorialRunState.ACTIVE
                    or any(state != "COMPLETED" for state in states[:stage_a_count])
                    or any(state != "NOT_STARTED" for state in states[stage_a_count:])
                ):
                    raise FactorialError("Stage A checkpoint is not a clean completed prefix")
                checkpoint = str(event.get("checkpoint_sha256"))
                if not _HEX64.fullmatch(checkpoint):
                    raise FactorialError("Stage A checkpoint digest is malformed")
                run_state = FactorialRunState.PAUSED_AFTER_STAGE_A
                continue
            if name == "STAGE_B_AUTHORIZED":
                if run_state is not FactorialRunState.PAUSED_AFTER_STAGE_A:
                    raise FactorialError("Stage B authorization lacks deliberate pause")
                run_state = FactorialRunState.ACTIVE
                continue
            if name == "FINALIZED":
                if any(state != "COMPLETED" for state in states):
                    raise FactorialError("incomplete factorial run was finalized")
                final = str(event.get("projection_sha256"))
                if not _HEX64.fullmatch(final):
                    raise FactorialError("final factorial projection digest is malformed")
                run_state = FactorialRunState.FINALIZED
                continue
            if type(index) is not int or not 0 <= index < len(states):
                raise FactorialError("factorial ledger task index is invalid")
            if name == "STARTED":
                if (
                    run_state is not FactorialRunState.ACTIVE
                    or states[index] != "NOT_STARTED"
                    or any(state != "COMPLETED" for state in states[:index])
                ):
                    raise FactorialError("factorial task cannot start or rerun")
                if index >= stage_a_count and checkpoint is None:
                    raise FactorialError("Stage B cannot start before verified Stage A pause")
                states[index] = "STARTED"
            elif name == "COMPLETED":
                artifact_sha256 = str(event.get("artifact_sha256"))
                artifact = self.directory / f"{index:02d}.factorial.jsonl.gz"
                if (
                    states[index] != "STARTED"
                    or not _HEX64.fullmatch(artifact_sha256)
                    or not artifact.is_file()
                    or hashlib.sha256(artifact.read_bytes()).hexdigest() != artifact_sha256
                ):
                    raise FactorialError("factorial completion lacks one valid started task")
                states[index] = "COMPLETED"
            elif name == "FAILED":
                if states[index] != "STARTED":
                    raise FactorialError("factorial failure lacks one started task")
                states[index] = "FAILED"
            else:
                raise FactorialError("unknown factorial ledger event")
        return FactorialLedgerState(
            str(created.get("run_uuid")),
            str(created.get("executable_sha")),
            task_ids,
            stage_a_count,
            tuple(states),
            len(events) - 1,
            run_state,
            checkpoint,
            final,
        )

    def _append(self, event: str, **facts: object) -> None:
        state = self.load()
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
        previous = hashlib.sha256(files[-1].read_bytes()).hexdigest()
        self._write(
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event": event,
                "revision": state.revision + 1,
                "previous_sha256": previous,
                "at": _iso(datetime.now(UTC)),
                **facts,
            }
        )
        self.load()

    def start(self, task_index: int) -> None:
        state = self.load()
        if (
            not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] != "NOT_STARTED"
            or state.run_state is not FactorialRunState.ACTIVE
            or any(value != "COMPLETED" for value in state.task_states[:task_index])
            or (task_index >= state.stage_a_count and state.checkpoint_sha256 is None)
        ):
            raise FactorialError("factorial task cannot be started or retried")
        self._append("STARTED", task_index=task_index)

    def complete(self, task_index: int, artifact_sha256: str) -> None:
        state = self.load()
        artifact = self.directory / f"{task_index:02d}.factorial.jsonl.gz"
        if (
            state.task_states[task_index] != "STARTED"
            or not artifact.is_file()
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != artifact_sha256
        ):
            raise FactorialError("factorial task is not exclusively started")
        self._append("COMPLETED", task_index=task_index, artifact_sha256=artifact_sha256)

    def fail(self, task_index: int, reason_code: str) -> None:
        state = self.load()
        if state.task_states[task_index] != "STARTED" or not reason_code:
            raise FactorialError("factorial failure cannot be recorded")
        self._append("FAILED", task_index=task_index, reason_code=reason_code)

    def pause_after_stage_a(self, checkpoint_sha256: str) -> None:
        state = self.load()
        if (
            state.run_state is not FactorialRunState.ACTIVE
            or any(value != "COMPLETED" for value in state.task_states[: state.stage_a_count])
            or any(value != "NOT_STARTED" for value in state.task_states[state.stage_a_count :])
        ):
            raise FactorialError("factorial run is not eligible for Stage A pause")
        self._append("STAGE_A_CHECKPOINT", checkpoint_sha256=checkpoint_sha256)

    def authorize_stage_b(
        self,
        authorization: StageContinuationAuthorization,
        grant: FactorialExecutionGrant,
    ) -> None:
        state = self.load()
        if state.checkpoint_sha256 is None:
            raise FactorialError("Stage B lacks a verified Stage A checkpoint")
        authorization.validate(grant, state.checkpoint_sha256)
        self._append(
            "STAGE_B_AUTHORIZED",
            authorization_sha256=_digest(
                {name: getattr(authorization, name) for name in authorization.__dataclass_fields__}
            ),
        )

    def finalize(self, projection_sha256: str) -> None:
        if any(state != "COMPLETED" for state in self.load().task_states):
            raise FactorialError("partial factorial run cannot be finalized")
        self._append("FINALIZED", projection_sha256=projection_sha256)
