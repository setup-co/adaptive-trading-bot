"""Synthetic acceptance of the staged Research V2 factorial boundary."""

from __future__ import annotations

import hashlib
import json
import runpy
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.backtest import ExecutionCostConfiguration, ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillReplayError,
    ResearchAllFillReplayRunner,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    HistoricalStrategyContextObservation,
)
from adaptive_trading_bot.research.research_v2_admission import PANEL
from adaptive_trading_bot.research.research_v2_exploratory_screen import frozen_tasks
from adaptive_trading_bot.research.research_v2_factorial import (
    EXPECTED_ADMISSIONS,
    EXPECTED_MATRIX_RECORDS,
    EXPECTED_NEW_OUTCOMES,
    PROTOCOL_SHA256,
    AdmittedDecisionReference,
    FactorialCell,
    FactorialError,
    FactorialExecutionGrant,
    FactorialLedgerStore,
    FactorialOutcome,
    FactorialRunState,
    StageContinuationAuthorization,
    admitted_inventory_digest,
    cell_costs,
    execute_factorial_cell,
    factorial_result_record,
    imported_outcome_from_record,
    outcome_from_factorial_result,
    project_factorial_matrix,
    protocol_facts,
    recover_factorial_result,
    stage_counts,
    verify_outcome_against_factorial_result,
)
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_paired_exit_replay.py")))
NOW = cast(datetime, _HELPERS["NOW"])
SOURCE = _HELPERS["SOURCE"]
BTC = _HELPERS["BTC"]
_decision = _HELPERS["_decision"]
_execution_path = _HELPERS["_execution_path"]
PREFLIGHT_ROOT = (
    Path(__file__).parents[2]
    / "data/research_evidence/issue-191-factorial-preflight-fc3fc5096350d087"
)


class _Source:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        return tuple(
            candle
            for candle in self.candles
            if start <= candle.open_time < end
            and candle.symbol == series.symbol
            and candle.timeframe is series.timeframe
        )


@pytest.mark.parametrize("side", (CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE))
@pytest.mark.parametrize("cell", (FactorialCell.C1L0, FactorialCell.C0L1))
def test_missing_cells_use_shared_all_fill_authorities_for_long_and_short(
    side: CandidateAction, cell: FactorialCell
) -> None:
    decision = cast(DecisionJournalEntry[object, object], _decision(side=side, outcome="cleared"))
    candles = cast(tuple[Candle, ...], _execution_path(100, 110, 120, 130))
    runner = ResearchAllFillReplayRunner(_Source(candles))
    result = execute_factorial_cell(
        runner,
        decision,
        (),
        ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
        cell,
        start=NOW,
        end=candles[-1].close_time,
    )
    assert result.cell is cell
    assert result.costs == cell_costs(cell)
    assert result.branches
    assert result.legacy_replay is not None
    expected_entry_open = NOW if cell is FactorialCell.C1L0 else NOW + timedelta(minutes=15)
    assert result.branches[-1].entry_fill.execution_candle_open_time == expected_entry_open
    result.validate()

    record = json.loads(
        json.dumps(factorial_result_record(result, decision_index=0, candles=candles))
    )
    restored = recover_factorial_result(
        record,
        decision=decision,
        decision_index=0,
        cell=cell,
        candles=candles,
    )
    assert restored == result
    outcome = outcome_from_factorial_result(
        restored,
        task_id=frozen_tasks()[0].task_id,
        decision_identity_sha256="1" * 64,
        source_artifact_sha256="2" * 64,
    )
    verify_outcome_against_factorial_result(
        outcome,
        restored,
        task_id=frozen_tasks()[0].task_id,
        decision_identity_sha256="1" * 64,
        source_artifact_sha256="2" * 64,
    )
    with pytest.raises(FactorialError, match="compact factorial row"):
        verify_outcome_against_factorial_result(
            replace(outcome, incurred_fees_quote=outcome.incurred_fees_quote + Decimal("1")),
            restored,
            task_id=frozen_tasks()[0].task_id,
            decision_identity_sha256="1" * 64,
            source_artifact_sha256="2" * 64,
        )

    with pytest.raises(AllFillReplayError, match="two-scenario"):
        runner.run(
            decision,
            (),
            ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
            cell_costs(cell),
            start=NOW,
            end=candles[-1].close_time,
        )


def _inventory() -> tuple[AdmittedDecisionReference, ...]:
    # Exact retained per-task counts: Stage A 48 admissions, Stage B 198.
    counts = (
        21,
        1,
        0,
        23,
        3,
        0,
        28,
        4,
        0,
        22,
        4,
        0,
        18,
        6,
        0,
        16,
        1,
        0,
        12,
        1,
        0,
        34,
        4,
        0,
        30,
        3,
        0,
        13,
        1,
        1,
    )
    rows: list[AdmittedDecisionReference] = []
    for task, count in zip(frozen_tasks(), counts, strict=True):
        for index in range(count):
            rows.append(
                AdmittedDecisionReference(
                    task.task_id,
                    index,
                    f"{len(rows) + 1:064x}",
                    task.wire_symbol,
                    task.family.upper(),
                    task.decision_start + (index + 1) * Timeframe.MINUTES_15.duration,
                    index == 0,
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                )
            )
    assert len(rows) == EXPECTED_ADMISSIONS
    return tuple(rows)


def test_protocol_and_stages_freeze_full_experiment_before_stage_a() -> None:
    facts = protocol_facts()
    assert facts["controls"] == {
        "admissions": 246,
        "new_outcomes": 492,
        "matrix_records": 984,
        "per_decision_follow_up_hours": 168,
    }
    checkpoint = cast(dict[str, object], facts["checkpoint"])
    assert checkpoint["pnl_gate"] is False
    counts = stage_counts(_inventory())
    assert counts["stages"] == {
        "A": {
            "task_count": 6,
            "admission_count": 48,
            "new_outcome_count": 96,
            "matrix_record_count": 192,
            "zero_admission_task_count": 2,
        },
        "B": {
            "task_count": 24,
            "admission_count": 198,
            "new_outcome_count": 396,
            "matrix_record_count": 792,
            "zero_admission_task_count": 7,
        },
    }
    assert counts["full"] == {
        "admission_count": EXPECTED_ADMISSIONS,
        "new_outcome_count": EXPECTED_NEW_OUTCOMES,
        "matrix_record_count": EXPECTED_MATRIX_RECORDS,
        "task_count": 30,
    }
    with pytest.raises(FactorialError, match="exactly 246"):
        stage_counts(_inventory()[:-1])
    duplicated = (*_inventory()[:-1], _inventory()[0])
    with pytest.raises(FactorialError, match="frozen task/decision order"):
        stage_counts(duplicated)


def _grant(inventory: tuple[AdmittedDecisionReference, ...]) -> FactorialExecutionGrant:
    return FactorialExecutionGrant(
        "SYNTHETIC_FACTORIAL_GRANT",
        str(uuid.uuid4()),
        "d" * 40,
        PROTOCOL_SHA256,
        "e" * 64,
        admitted_inventory_digest(inventory),
        "synthetic-source",
        "synthetic-dataset",
        "f" * 64,
    )


def test_ledger_pauses_cleanly_and_continues_only_with_exact_authorization(
    tmp_path: Path,
) -> None:
    inventory = _inventory()
    grant = _grant(inventory)
    store = FactorialLedgerStore.create(
        tmp_path,
        grant,
        actual_sha="d" * 40,
        task_ids=("a:C1L0", "a:C0L1", "b:C1L0", "b:C0L1"),
        stage_a_count=2,
        synthetic=True,
    )
    for index in range(2):
        store.start(index)
        payload = f"stage-a-{index}".encode()
        (store.directory / f"{index:02d}.factorial.jsonl.gz").write_bytes(payload)
        store.complete(index, hashlib.sha256(payload).hexdigest())
    checkpoint = "9" * 64
    store.pause_after_stage_a(checkpoint)
    state = store.load()
    assert state.run_state is FactorialRunState.PAUSED_AFTER_STAGE_A
    assert state.task_states == ("COMPLETED", "COMPLETED", "NOT_STARTED", "NOT_STARTED")
    with pytest.raises(FactorialError, match="cannot be started"):
        store.start(0)
    with pytest.raises(FactorialError, match="cannot be started"):
        store.start(2)
    wrong = StageContinuationAuthorization(
        "ARCHITECT_AUTHORIZED_STAGE_B_CONTINUATION",
        grant.run_uuid,
        grant.executable_sha,
        grant.protocol_sha256,
        "0" * 64,
        grant.admitted_inventory_sha256,
        checkpoint,
    )
    with pytest.raises(FactorialError, match="changed frozen"):
        store.authorize_stage_b(wrong, grant)
    authorization = replace(wrong, input_plan_sha256=grant.input_plan_sha256)
    store.authorize_stage_b(authorization, grant)
    for index in range(2, 4):
        store.start(index)
        payload = f"stage-b-{index}".encode()
        (store.directory / f"{index:02d}.factorial.jsonl.gz").write_bytes(payload)
        store.complete(index, hashlib.sha256(payload).hexdigest())
    store.finalize("8" * 64)
    assert store.load().run_state is FactorialRunState.FINALIZED


def test_failed_or_ambiguous_work_never_retries(tmp_path: Path) -> None:
    inventory = _inventory()
    grant = _grant(inventory)
    store = FactorialLedgerStore.create(
        tmp_path,
        grant,
        actual_sha="d" * 40,
        task_ids=("a", "b"),
        stage_a_count=1,
        synthetic=True,
    )
    store.start(0)
    store.fail(0, "TASK_FAILURE")
    with pytest.raises(FactorialError, match="retried"):
        store.start(0)
    with pytest.raises(FactorialError, match="not eligible"):
        store.pause_after_stage_a("1" * 64)


def test_completed_ledger_artifact_remains_required_for_pause(tmp_path: Path) -> None:
    grant = _grant(_inventory())
    store = FactorialLedgerStore.create(
        tmp_path,
        grant,
        actual_sha="d" * 40,
        task_ids=("a", "b"),
        stage_a_count=1,
        synthetic=True,
    )
    store.start(0)
    artifact = store.directory / "00.factorial.jsonl.gz"
    artifact.write_bytes(b"sealed")
    store.complete(0, hashlib.sha256(b"sealed").hexdigest())
    artifact.unlink()
    with pytest.raises(FactorialError, match="lacks one valid started task"):
        store.load()


def test_grant_rejects_changed_code_protocol_inputs_and_accepted_source_relabeling() -> None:
    inventory = _inventory()
    grant = _grant(inventory)
    grant.validate(actual_sha="d" * 40, synthetic=True)
    for changed in (
        replace(grant, executable_sha="0" * 40),
        replace(grant, protocol_sha256="0" * 64),
        replace(grant, source_id="weex-research-v2-last-18912cf7e7f97d518a74551f"),
    ):
        with pytest.raises(FactorialError, match="does not bind"):
            changed.validate(actual_sha="d" * 40, synthetic=True)


def test_partial_projection_is_not_a_ten_member_classification_and_decimal_is_local() -> None:
    inventory = _inventory()
    first = inventory[0]
    outcomes = tuple(
        FactorialOutcome(
            first.decision_identity_sha256,
            first.task_id,
            first.symbol,
            first.family,
            cell,
            cell in (FactorialCell.C0L0, FactorialCell.C1L1),
            "ORDINARY_CLOSED",
            value,
            Decimal("1"),
            Decimal("2"),
            Decimal("1"),
            Decimal("0"),
            f"{index + 20:064x}",
            "c" * 64 if cell in (FactorialCell.C0L0, FactorialCell.C1L1) else f"{index + 30:064x}",
        )
        for index, (cell, value) in enumerate(
            (
                (FactorialCell.C0L0, Decimal("1")),
                (FactorialCell.C1L0, Decimal("0.5")),
                (FactorialCell.C0L1, Decimal("0.75")),
                (FactorialCell.C1L1, Decimal("0.1")),
            )
        )
    )
    original = getcontext().copy()
    try:
        getcontext().prec = 2
        projection = project_factorial_matrix(inventory, outcomes, complete=False)
    finally:
        setcontext(original)
    assert projection["classification"] == (
        "PARTIAL_TECHNICAL_CHECKPOINT_NO_TEN_MEMBER_CLASSIFICATION"
    )
    assert projection["complete_quadruple_count"] == 1
    contrast = cast(tuple[dict[str, object], ...], projection["contrasts"])[0]
    assert contrast == {
        "decision_identity_sha256": first.decision_identity_sha256,
        "task_id": first.task_id,
        "cost_contrast_l0": "-0.5",
        "cost_contrast_l1": "-0.65",
        "eligibility_contrast_c0": "-0.25",
        "eligibility_contrast_c1": "-0.4",
        "interaction": "-0.15",
        "combined_difference": "-0.9",
    }
    with pytest.raises(FactorialError, match="missing cells"):
        project_factorial_matrix(inventory, outcomes, complete=True)


def test_imported_diagonals_cannot_be_executed() -> None:
    decision = cast(DecisionJournalEntry[object, object], _decision(outcome="cleared"))
    candles = cast(tuple[Candle, ...], _execution_path(100, 110))
    runner = ResearchAllFillReplayRunner(_Source(candles))
    with pytest.raises(FactorialError, match="referenced"):
        execute_factorial_cell(
            runner,
            decision,
            tuple[HistoricalStrategyContextObservation](),
            ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
            FactorialCell.C0L0,
            start=NOW,
            end=candles[-1].close_time,
        )


def test_positive_latency_without_a_later_open_is_entry_unfilled() -> None:
    decision = cast(DecisionJournalEntry[object, object], _decision(outcome="cleared"))
    candles = cast(tuple[Candle, ...], _execution_path(100))
    result = execute_factorial_cell(
        ResearchAllFillReplayRunner(_Source(candles)),
        decision,
        (),
        ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
        FactorialCell.C0L1,
        start=NOW,
        end=candles[-1].close_time,
    )
    assert result.status.value == "ENTRY_UNFILLED"
    assert result.branches == ()


def test_imported_diagonal_record_is_lossless_and_cannot_claim_new_cell() -> None:
    first = _inventory()[0]
    record = {
        "task_id": first.task_id,
        "decision_identity_sha256": first.decision_identity_sha256,
        "cell": FactorialCell.C0L0.value,
        "terminal_status": "ORDINARY_OPEN",
        "symbol": first.symbol,
        "family": first.family,
        "closed_net_r": None,
        "incurred_fees_quote": "0.1250000000000000001",
        "gross_realized_pnl_quote": "0",
        "net_realized_pnl_quote": "-0.1250000000000000001",
        "residual_base_quantity": "0.00000001",
        "result_sha256": "a" * 64,
        "authority_sha256": first.authority_sha256,
    }
    outcome = imported_outcome_from_record(record)
    assert outcome.incurred_fees_quote == Decimal("0.1250000000000000001")
    assert outcome.residual_base_quantity == Decimal("0.00000001")
    with pytest.raises(FactorialError, match="cannot be imported"):
        imported_outcome_from_record({**record, "cell": FactorialCell.C1L0.value})


def test_cost_cells_are_exact_and_not_an_optimization_grid() -> None:
    assert cell_costs(FactorialCell.C1L0) == ExecutionCostConfiguration(
        Decimal(4), Decimal(4), Decimal(20), timedelta()
    )
    assert cell_costs(FactorialCell.C0L1) == ExecutionCostConfiguration(
        Decimal(2), Decimal(2), Decimal(10), timedelta(seconds=1)
    )
    assert PANEL[:2] == ("BTCUSDT", "ETHUSDT")


def test_locked_data_only_preflight_artifact_matches_manifest() -> None:
    manifest_payload = (PREFLIGHT_ROOT / "manifest.json").read_bytes()
    manifest = json.loads(manifest_payload)
    plan_payload = (PREFLIGHT_ROOT / "input-plan.json").read_bytes()
    assert hashlib.sha256(manifest_payload).hexdigest() == (
        "d006b6e5f19bc990efa4010ea9ece668a7a69fe24b4ba93688935f76995daaba"
    )
    assert len(plan_payload) == manifest["artifact"]["size_bytes"] == 489029
    assert (
        hashlib.sha256(plan_payload).hexdigest()
        == manifest["artifact"]["sha256"]
        == "299ebc3b4b920ff78b382fa56efb86dc6975c4f0176f8803486a6cca13e95cd2"
    )
    plan = json.loads(plan_payload)
    assert plan["status"] == "DATA_ONLY_PREPARATION; NO_EXECUTION_GRANT"
    assert plan["protocol_sha256"] == PROTOCOL_SHA256
    assert plan["input_plan_sha256"] == manifest["input_plan_sha256"]
    assert plan["counts"]["stages"]["A"]["admission_count"] == 48
    assert plan["counts"]["stages"]["B"]["admission_count"] == 198
