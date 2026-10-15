"""Synthetic-only characterization of the frozen exploratory study boundary."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from scripts.research.research_v2_exploratory_screen import (
    _bind_final_projection,
    _verify_authority_rows,
    _verify_synthetic_complete,
)
from test_all_fill_replay import _run as _run_all_fill_fixture
from test_research_v2_admission import SYMBOL, SyntheticEpochStore, _config

from adaptive_trading_bot.backtest import ExecutionCostConfiguration
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillFamily,
    AllFillReplayResult,
    AllFillScenario,
    AllFillStatus,
)
from adaptive_trading_bot.research.research_v2_admission import (
    MANIFEST_SHA256,
    PANEL,
    SOURCE_ID,
    LockedResearchV2CandleStore,
    ResearchV2AdmissionConfiguration,
    ResearchV2AdmissionError,
    ResearchV2DecisionPopulation,
    ResearchV2EpochBounds,
    _evaluate_synthetic_epoch,
    _replay_synthetic_all_fill,
    _validate_frozen_study_configuration,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    BLOCKS,
    DECISION_END,
    DECISION_START,
    FOLLOW_UP,
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    ExploratoryScreenError,
    ScreenClassification,
    ScreenObservation,
    StudyExecutionGrant,
    _canonical,
    _cell_summary,
    _parse_row,
    _row_record,
    authority_result_record,
    causal_follow_up_slice,
    classify_family,
    decision_cutoff,
    execute_granted_task,
    execute_synthetic_task,
    frozen_tasks,
    horizon_mask,
    observation_bin,
    project_frozen_screen,
    protocol_facts,
    recover_authority_result,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    EVIDENCE_SCHEMA,
    ScreenEvidenceError,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    decode as decode_authority,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    encode as encode_authority,
)
from adaptive_trading_bot.research.research_v2_screen_ledger import StudyLedgerStore
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _INTEGRITY_SEAL_VERSION,
    _canonical_integrity_value,
)
from adaptive_trading_bot.risk import OperationalSafetyConfiguration, RiskManagerConfiguration
from adaptive_trading_bot.strategies import CandidateAction


def _row(
    wire: str,
    week: int,
    sequence: int,
    *,
    scenario: AllFillScenario = AllFillScenario.BASE,
    net_r: Decimal | None = Decimal(1),
    status: AllFillStatus = AllFillStatus.ORDINARY_CLOSED,
    admitted: bool = True,
) -> ScreenObservation:
    observed = DECISION_START + timedelta(days=7 * week, minutes=15 * (sequence + 1))
    return ScreenObservation(
        f"{wire}-{week}-{sequence}",
        observed,
        wire,
        AllFillFamily.BREAKOUT_RETEST,
        scenario,
        status,
        admitted,
        net_r,
        Decimal(1) if admitted else Decimal(0),
        Decimal(1)
        if status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
        else Decimal(0),
        Decimal(1) if admitted else None,
        Decimal(0) if admitted else None,
        "LONG" if admitted else "NONE",
        "NEUTRAL",
        "a" * 64,
    )


def _population(
    *,
    net_r: Decimal = Decimal(1),
    asset_count: int = 5,
    week_count: int = 6,
    rows_per_week: int = 2,
) -> tuple[tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]]:
    base = tuple(
        _row(wire, week, sequence, net_r=net_r)
        for wire in PANEL[:asset_count]
        for week in range(week_count)
        for sequence in range(rows_per_week)
    )
    stress = tuple(replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in base)
    return base, stress


def test_exact_protocol_calendar_inventory_and_hash_are_predeclared() -> None:
    tasks = frozen_tasks()
    assert len(tasks) == 30
    assert tasks[0].wire_symbol == "BTCUSDT"
    assert tasks[0].family == "trend_pullback"
    assert tasks[-1].wire_symbol == "DOGEUSDT"
    assert tasks[-1].family == "range_mean_reversion"
    assert tasks[0].context_start == datetime(2026, 7, 21, tzinfo=UTC)
    assert tasks[21].wire_symbol == "BTWUSDT"
    assert tasks[21].context_start == datetime(2026, 7, 25, 12, tzinfo=UTC)
    assert all(
        task.decision_start == DECISION_START and task.decision_end == DECISION_END
        for task in tasks
    )
    assert all(task.last_follow_up_end == DECISION_END + FOLLOW_UP for task in tasks)
    assert (
        hashlib.sha256(
            json.dumps(protocol_facts(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == PROTOCOL_SHA256
    )
    assert len(TASK_PLAN_SHA256) == 64
    assert len(tasks) * 6048 == 181440
    assert tuple((end - start).days for _, start, end in BLOCKS) == (21, 21, 21)


def test_first_last_boundary_week_and_per_decision_cutoff() -> None:
    assert observation_bin(DECISION_START + timedelta(minutes=15)) == ("P1", "W1")
    assert observation_bin(BLOCKS[0][2]) == ("P1", "W3")
    assert observation_bin(BLOCKS[0][2] + timedelta(minutes=15)) == ("P2", "W4")
    assert observation_bin(DECISION_END) == ("P3", "W9")
    assert decision_cutoff(BLOCKS[0][2]) == BLOCKS[0][2] + timedelta(hours=168)
    assert decision_cutoff(DECISION_END) == datetime(2026, 10, 12, tzinfo=UTC)
    observed = (
        DECISION_START,
        DECISION_START + timedelta(minutes=15),
        DECISION_START + timedelta(minutes=30),
        DECISION_START + timedelta(hours=168),
        DECISION_START + timedelta(hours=168, minutes=15),
    )
    assert observed[causal_follow_up_slice(observed, DECISION_START + timedelta(minutes=15))] == (
        DECISION_START + timedelta(minutes=30),
        DECISION_START + timedelta(hours=168),
    )
    with pytest.raises(ExploratoryScreenError):
        observation_bin(DECISION_START)
    with pytest.raises(ExploratoryScreenError):
        observation_bin(DECISION_END + timedelta(minutes=15))


def test_cooldown_mask_uses_admissions_before_outcomes_across_blocks() -> None:
    first = _row("BTCUSDT", 0, 0, status=AllFillStatus.ENTRY_UNFILLED, net_r=None)
    nearby_winner = _row("BTCUSDT", 0, 1)
    next_week = _row("BTCUSDT", 1, 0, status=AllFillStatus.RECOVERY_OPEN, net_r=None)
    other_asset = _row("ETHUSDT", 0, 0)
    rows = (first, nearby_winner, next_week, other_asset)
    assert horizon_mask(rows) == (first.identity, next_week.identity, other_asset.identity)
    assert horizon_mask(
        tuple(replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in rows)
    ) == horizon_mask(rows)

    boundary = replace(
        _row("BTCUSDT", 2, 0),
        observed_at=BLOCKS[0][2],
        identity="BTCUSDT-P1-LAST",
    )
    adjacent = replace(
        _row("BTCUSDT", 3, 0),
        observed_at=BLOCKS[0][2] + timedelta(minutes=15),
        identity="BTCUSDT-P2-FIRST",
    )
    assert horizon_mask((boundary, adjacent)) == (boundary.identity,)


def test_exact_sufficiency_and_economic_classification_boundaries() -> None:
    base, stress = _population()
    label, facts = classify_family(base, stress, AllFillFamily.BREAKOUT_RETEST)
    assert label is ScreenClassification.CANDIDATE_FOR_FURTHER_VALIDATION
    assert facts["full_closed"] == (60, 60)
    assert facts["mask_closed"] == (30, 30)
    assert facts["same_asset_family_horizon_overlaps"] == 30
    assert (
        classify_family(*_population(net_r=Decimal(-1)), AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.NEGATIVE_EVIDENCE
    )
    assert (
        classify_family(base[:-1], stress[:-1], AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.INSUFFICIENT_EVIDENCE
    )
    with pytest.raises(ExploratoryScreenError, match="populations differ"):
        classify_family(base, stress[:-1], AllFillFamily.BREAKOUT_RETEST)
    stress_negative = tuple(replace(row, primary_closed_net_r=Decimal(-1)) for row in stress)
    assert (
        classify_family(base, stress_negative, AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.TECHNICALLY_VALID_MIXED_EVIDENCE
    )
    open_base = (
        replace(
            base[0],
            status=AllFillStatus.ORDINARY_OPEN,
            primary_closed_net_r=None,
            primary_open_quantity=Decimal(1),
        ),
        *base[1:],
    )
    open_stress = (
        replace(
            stress[0],
            status=AllFillStatus.ORDINARY_OPEN,
            primary_closed_net_r=None,
            primary_open_quantity=Decimal(1),
        ),
        *stress[1:],
    )
    assert (
        classify_family(open_base, open_stress, AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.INSUFFICIENT_EVIDENCE
    )


def test_initial_asset_and_week_sufficiency_is_per_scenario() -> None:
    base, stress = _population(asset_count=6, week_count=7)
    base = tuple(
        replace(row, status=AllFillStatus.ENTRY_UNFILLED, primary_closed_net_r=None)
        if row.wire_symbol == PANEL[5] and row.identity.endswith("-0")
        else row
        for row in base
    )
    stress = tuple(
        replace(row, status=AllFillStatus.ENTRY_UNFILLED, primary_closed_net_r=None)
        if row.wire_symbol == PANEL[0] and row.identity.endswith("-0")
        else row
        for row in stress
    )
    classification, facts = classify_family(base, stress, AllFillFamily.BREAKOUT_RETEST)
    assert classification is not ScreenClassification.INSUFFICIENT_EVIDENCE
    by_scenario = cast(
        tuple[tuple[str, ...], tuple[str, ...]],
        facts["mask_assets_with_three_closures_by_scenario"],
    )
    assert tuple(len(group) for group in by_scenario) == (5, 5)
    assert len(cast(tuple[str, ...], facts["mask_assets_with_three_closures_both"])) == 4

    base_weeks = tuple(
        replace(row, status=AllFillStatus.ENTRY_UNFILLED, primary_closed_net_r=None)
        if observation_bin(row.observed_at)[1] == "W7" and row.identity.endswith("-0")
        else row
        for row in base
    )
    stress_weeks = tuple(
        replace(row, status=AllFillStatus.ENTRY_UNFILLED, primary_closed_net_r=None)
        if observation_bin(row.observed_at)[1] == "W1" and row.identity.endswith("-0")
        else row
        for row in stress
    )
    classification, facts = classify_family(base_weeks, stress_weeks, AllFillFamily.BREAKOUT_RETEST)
    assert classification is not ScreenClassification.INSUFFICIENT_EVIDENCE
    by_scenario = cast(
        tuple[tuple[str, ...], tuple[str, ...]], facts["mask_weeks_with_three_closures_by_scenario"]
    )
    assert tuple(len(group) for group in by_scenario) == (6, 6)
    assert len(cast(tuple[str, ...], facts["mask_weeks_with_three_closures_both"])) == 5


def test_residual_base_quantity_never_sums_different_assets() -> None:
    btc = replace(
        _row("BTCUSDT", 0, 0, status=AllFillStatus.ORDINARY_OPEN, net_r=None),
        primary_open_quantity=Decimal("0.2"),
    )
    doge = replace(
        _row("DOGEUSDT", 0, 0, status=AllFillStatus.ORDINARY_OPEN, net_r=None),
        primary_open_quantity=Decimal("900"),
    )
    cell = _cell_summary((btc, doge))
    assert "remaining_absolute_base_quantity" not in cell
    residual = cast(dict[str, str], cell["remaining_absolute_base_quantity_by_asset"])
    assert residual["BTCUSDT"] == "0.2"
    assert residual["DOGEUSDT"] == "900"
    paired_btc = replace(
        btc,
        family=AllFillFamily.TREND_PULLBACK,
        hold_only_status=AllFillStatus.ORDINARY_OPEN,
        hold_only_fee_quote=Decimal("0.1"),
        hold_only_open_quantity=Decimal("0.3"),
        hold_only_branch_sha256="b" * 64,
    )
    paired_doge = replace(
        doge,
        family=AllFillFamily.TREND_PULLBACK,
        hold_only_status=AllFillStatus.ORDINARY_OPEN,
        hold_only_fee_quote=Decimal("0.2"),
        hold_only_open_quantity=Decimal("800"),
        hold_only_branch_sha256="c" * 64,
    )
    diagnostic = cast(
        dict[str, object], _cell_summary((paired_btc, paired_doge))["hold_only_diagnostic"]
    )
    hold_residual = cast(dict[str, str], diagnostic["remaining_absolute_base_quantity_by_asset"])
    assert hold_residual["BTCUSDT"] == "0.3"
    assert hold_residual["DOGEUSDT"] == "800"


def test_mask_29_and_five_asset_six_week_gates() -> None:
    base, _ = _population()
    # Keep 60 full closures but remove one mask week by replacing its two
    # observations with NO_SIGNAL and adding two same-week overlapping closes.
    replaced_base = tuple(
        _row("BTCUSDT", 0, row_index, status=AllFillStatus.NO_SIGNAL, net_r=None, admitted=False)
        if row.identity == f"BTCUSDT-0-{row_index}"
        else row
        for row in base
        for row_index in (int(row.identity[-1]),)
    )
    insert_at = next(
        index for index, row in enumerate(replaced_base) if row.identity == "BTCUSDT-2-0"
    )
    replaced_base = (
        *replaced_base[:insert_at],
        _row("BTCUSDT", 1, 2),
        _row("BTCUSDT", 1, 3),
        *replaced_base[insert_at:],
    )
    replaced_stress = tuple(
        replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in replaced_base
    )
    assert len(horizon_mask(replaced_base)) == 29
    assert (
        classify_family(replaced_base, replaced_stress, AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.INSUFFICIENT_EVIDENCE
    )
    four_assets = tuple(row for row in base if row.wire_symbol != PANEL[4])
    four_assets_stress = tuple(
        replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in four_assets
    )
    assert (
        classify_family(four_assets, four_assets_stress, AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.INSUFFICIENT_EVIDENCE
    )
    five_weeks = tuple(row for row in base if observation_bin(row.observed_at)[1] != "W6")
    five_weeks_stress = tuple(
        replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in five_weeks
    )
    assert (
        classify_family(five_weeks, five_weeks_stress, AllFillFamily.BREAKOUT_RETEST)[0]
        is ScreenClassification.INSUFFICIENT_EVIDENCE
    )


def test_grant_fails_closed_before_any_real_study_and_separates_synthetic() -> None:
    real = StudyExecutionGrant(
        "EXTERNAL_REVIEWED_EXECUTION_GRANT",
        str(uuid.uuid4()),
        "a" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        SOURCE_ID.value,
        "weex-research-v2-last-90d-issue-179-combined-v2",
        MANIFEST_SHA256,
    )
    real.validate(actual_sha="a" * 40)
    for changed in (
        replace(real, executable_sha="b" * 40),
        replace(real, protocol_sha256="0" * 64),
        replace(real, task_plan_sha256="0" * 64),
        replace(real, manifest_sha256="0" * 64),
        replace(real, source_id="synthetic-research-v2-fixture"),
    ):
        with pytest.raises(ExploratoryScreenError):
            changed.validate(actual_sha="a" * 40)
    with pytest.raises(ExploratoryScreenError):
        real.validate(actual_sha="a" * 40, synthetic=True)
    synthetic = replace(
        real,
        kind="SYNTHETIC_TEST_GRANT",
        source_id="synthetic-research-v2-fixture",
        dataset_version="synthetic-fixture-v1",
        manifest_sha256="b" * 64,
    )
    synthetic.validate(actual_sha="a" * 40, synthetic=True)
    with pytest.raises(ExploratoryScreenError):
        synthetic.validate(actual_sha="a" * 40)
    with pytest.raises(ExploratoryScreenError, match="grant"):
        execute_granted_task(
            cast(LockedResearchV2CandleStore, object()),
            frozen_tasks()[0],
            synthetic,
            actual_code_sha="a" * 40,
        )


def test_grant_mismatch_precedes_every_strategy_population_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaptive_trading_bot.research import research_v2_exploratory_screen as screen

    calls = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise AssertionError("strategy population must not run")

    monkeypatch.setattr(screen, "_evaluate_granted_study_epoch", counted)
    real = StudyExecutionGrant(
        "EXTERNAL_REVIEWED_EXECUTION_GRANT",
        str(uuid.uuid4()),
        "a" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        SOURCE_ID.value,
        "weex-research-v2-last-90d-issue-179-combined-v2",
        MANIFEST_SHA256,
    )
    for wrong in (
        replace(real, executable_sha="b" * 40),
        replace(real, protocol_sha256="0" * 64),
        replace(real, manifest_sha256="0" * 64),
        replace(real, kind="SYNTHETIC_TEST_GRANT"),
    ):
        with pytest.raises(ExploratoryScreenError, match="grant"):
            execute_granted_task(
                cast(LockedResearchV2CandleStore, object()),
                frozen_tasks()[0],
                wrong,
                actual_code_sha="a" * 40,
            )
    assert calls == 0
    with pytest.raises(ExploratoryScreenError, match="partial"):
        project_frozen_screen(())


def test_study_configuration_rejects_changed_calendar_equity_risk_and_safety() -> None:
    task = frozen_tasks()[0]
    bounds = ResearchV2EpochBounds(
        TradingSymbol("BTC", "USDT"),
        task.context_start,
        task.decision_start,
        task.decision_end,
        task.last_follow_up_end,
    )
    accepted = ResearchV2AdmissionConfiguration(bounds, task.family, Decimal("10000"))
    _validate_frozen_study_configuration(accepted)
    alternatives = (
        ResearchV2AdmissionConfiguration(
            replace(bounds, decision_end=bounds.decision_end - timedelta(minutes=15)),
            task.family,
            Decimal("10000"),
        ),
        ResearchV2AdmissionConfiguration(bounds, task.family, Decimal("9000")),
        ResearchV2AdmissionConfiguration(
            bounds,
            task.family,
            Decimal("10000"),
            risk_configuration=replace(
                RiskManagerConfiguration(), risk_per_trade_fraction=Decimal("0.003")
            ),
        ),
        ResearchV2AdmissionConfiguration(
            bounds,
            task.family,
            Decimal("10000"),
            safety_configuration=replace(
                OperationalSafetyConfiguration(), max_market_data_age_seconds=121
            ),
        ),
    )
    for changed in alternatives:
        with pytest.raises(ResearchV2AdmissionError, match="frozen task and defaults"):
            _validate_frozen_study_configuration(changed)


def test_ledger_preserves_started_ambiguity_and_rejects_restart(tmp_path: Path) -> None:
    grant = StudyExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        str(uuid.uuid4()),
        "a" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        "synthetic-research-v2-fixture",
        "synthetic-fixture-v1",
        "b" * 64,
    )
    ledger = StudyLedgerStore.create(tmp_path, grant, actual_sha="a" * 40, synthetic=True)
    assert ledger.load().task_states == ("NOT_STARTED",) * 30
    ledger.start(0)
    assert ledger.load().task_states[0] == "STARTED"
    with pytest.raises(ExploratoryScreenError):
        ledger.start(0)
    with pytest.raises(ExploratoryScreenError):
        ledger.start(1)
    with pytest.raises(ExploratoryScreenError):
        StudyLedgerStore.create(tmp_path, grant, actual_sha="a" * 40, synthetic=True)
    (ledger.directory / ".0002.pending").write_bytes(b"interrupted")
    with pytest.raises(ExploratoryScreenError, match="interrupted"):
        ledger.load()


def test_completed_task_artifacts_are_hash_bound_and_cannot_rerun(tmp_path: Path) -> None:
    grant = StudyExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        str(uuid.uuid4()),
        "b" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        "synthetic-research-v2-fixture",
        "synthetic-fixture-v1",
        "c" * 64,
    )
    ledger = StudyLedgerStore.create(tmp_path, grant, actual_sha="b" * 40, synthetic=True)
    ledger.start(0)
    population_bytes, partition_bytes = b"sealed fixture population", b"sealed fixture partition"
    authority_bytes = b"sealed fixture replay authority"
    (ledger.directory / "00.population.json.gz").write_bytes(population_bytes)
    (ledger.directory / "00.partition.json").write_bytes(partition_bytes)
    (ledger.directory / "00.authority.jsonl.gz").write_bytes(authority_bytes)
    ledger.complete(
        0,
        population_sha256=hashlib.sha256(population_bytes).hexdigest(),
        partition_sha256=hashlib.sha256(partition_bytes).hexdigest(),
        authority_sha256=hashlib.sha256(authority_bytes).hexdigest(),
    )
    assert StudyLedgerStore(ledger.directory).load().task_states[0] == "COMPLETED"
    with pytest.raises(ExploratoryScreenError):
        ledger.start(0)
    with pytest.raises(ExploratoryScreenError):
        ledger.finalize("d" * 64)
    (ledger.directory / "00.authority.jsonl.gz").unlink()
    with pytest.raises(ExploratoryScreenError, match="missing or changed"):
        ledger.load()
    (ledger.directory / "00.authority.jsonl.gz").write_bytes(authority_bytes)
    (ledger.directory / "00.partition.json").write_bytes(b"mutated")
    with pytest.raises(ExploratoryScreenError, match="changed"):
        ledger.load()


def test_compact_row_preserves_exact_decimal_and_rejects_derived_label_change() -> None:
    original = _row("SOLUSDT", 0, 0, net_r=Decimal("0.125000"))
    record = _row_record(original)
    assert _parse_row(json.loads(json.dumps(record))) == original
    assert record["primary_closed_net_r"] == "0.125000"
    altered = {**record, "week": "W2"}
    with pytest.raises(ExploratoryScreenError, match="malformed"):
        _parse_row(altered)


def test_synthetic_population_roundtrip_uses_one_canonical_authority_seal() -> None:
    store = SyntheticEpochStore(SYMBOL)
    population = _evaluate_synthetic_epoch(store, _config(store=store))
    population.validate()
    canonical = _canonical_integrity_value(population, excluded_names=frozenset({"integrity_seal"}))
    stored = gzip.compress(
        _canonical(
            {
                "schema_version": population.schema_version,
                "population_integrity_seal": population.integrity_seal,
                "canonical_population": canonical,
            }
        ),
        mtime=0,
    )
    restored = json.loads(gzip.decompress(stored))
    assert (
        hashlib.sha256(
            _canonical(
                (
                    "adaptive-trading-bot/research-v2-decision-population",
                    _INTEGRITY_SEAL_VERSION,
                    restored["canonical_population"],
                )
            )
        ).hexdigest()
        == restored["population_integrity_seal"]
        == population.integrity_seal
    )
    reconstructed = decode_authority(json.loads(_canonical(encode_authority(population))))
    assert reconstructed == population
    reconstructed.validate()


def test_authority_codec_never_imports_artifact_selected_classes() -> None:
    with pytest.raises(ScreenEvidenceError, match="versioned schema"):
        decode_authority(["dataclass", "os.system", []])


def test_result_authority_roundtrip_reconstructs_accounted_synthetic_fill() -> None:
    store = SyntheticEpochStore(SYMBOL)
    population = _evaluate_synthetic_epoch(store, _config(family="breakout_retest", store=store))
    index = next(
        index
        for index, decision in enumerate(population.decisions)
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        population.decisions[index],
        store,
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    record = authority_result_record(population, index, wrapped.result.scenario, wrapped.result)
    encoded_result = cast(tuple[str, str, tuple[tuple[str, object], ...]], record["result"])
    stored_fields = dict(encoded_result[2])
    assert stored_fields["decision"] == ("decision_ref",)
    assert cast(tuple[object, ...], stored_fields["execution_snapshot"])[0] == "candle_slice"
    rebuilt = recover_authority_result(
        json.loads(_canonical(record)), population, index, wrapped.result.scenario
    )
    assert rebuilt == wrapped.result
    assert rebuilt.branches[0].final_position == wrapped.result.branches[0].final_position


def test_codec_roundtrips_all_terminal_all_fill_evidence_without_market_calls() -> None:
    cases = (
        _run_all_fill_fixture(outcome="no_action"),
        _run_all_fill_fixture(outcome="risk_blocked"),
        _run_all_fill_fixture(outcome="safe_mode"),
        _run_all_fill_fixture(opens=(100,), stress=True, end_count=1),
        _run_all_fill_fixture(opens=(100,), end_count=1),
        _run_all_fill_fixture(opens=(70,), end_count=1),
        _run_all_fill_fixture(opens=(70, 110, 120)),
    )
    statuses: set[AllFillStatus] = set()
    for result, source in cases:
        loads_before = len(source.loads)
        restored = decode_authority(json.loads(_canonical(encode_authority(result))))
        assert restored == result
        assert isinstance(restored, AllFillReplayResult)
        restored.validate()
        assert len(source.loads) == loads_before
        statuses.add(restored.status)
    assert statuses >= {
        AllFillStatus.NO_SIGNAL,
        AllFillStatus.RISK_BLOCKED,
        AllFillStatus.SAFETY_BLOCKED,
        AllFillStatus.ENTRY_UNFILLED,
        AllFillStatus.ORDINARY_OPEN,
        AllFillStatus.RECOVERY_OPEN,
        AllFillStatus.RECOVERY_CLOSED,
    }


@pytest.mark.parametrize(
    ("family", "pattern", "decision_end_hours"),
    [
        ("trend_pullback", "trend", 204),
        ("breakout_retest", "short", 204),
        ("range_mean_reversion", "range", 206),
    ],
)
def test_replay_codec_retains_paired_short_and_range_accounting(
    family: str, pattern: str, decision_end_hours: int
) -> None:
    store = SyntheticEpochStore(SYMBOL, pattern=pattern)
    configuration = _config(family=family, store=store, decision_end_hours=decision_end_hours)
    if family == "range_mean_reversion":
        configuration = replace(configuration, research_equity_quote=Decimal(9500))
    population = _evaluate_synthetic_epoch(store, configuration)
    index = next(
        index
        for index, decision in enumerate(population.decisions)
        if decision.strategy_evaluation.action is not CandidateAction.NO_TRADE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        population.decisions[index],
        store,
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    record = authority_result_record(population, index, wrapped.result.scenario, wrapped.result)
    rebuilt = recover_authority_result(
        json.loads(_canonical(record)), population, index, wrapped.result.scenario
    )
    assert rebuilt == wrapped.result
    assert tuple(branch.final_position for branch in rebuilt.branches) == tuple(
        branch.final_position for branch in wrapped.result.branches
    )


@pytest.mark.parametrize(
    ("family", "pattern", "decision_start_hours", "decision_end_hours", "expected_side"),
    [
        ("trend_pullback", "trend", 200, 201, "LONG"),
        ("breakout_retest", "short", 200, 201, "SHORT"),
        ("range_mean_reversion", "range", 204, 205, "LONG"),
    ],
)
def test_shared_synthetic_task_loop_replays_each_family_from_raw_candles(
    family: str,
    pattern: str,
    decision_start_hours: int,
    decision_end_hours: int,
    expected_side: str,
) -> None:
    store = SyntheticEpochStore(SYMBOL, pattern=pattern, hours=376)
    configuration = _config(
        family=family,
        store=store,
        decision_start_hours=decision_start_hours,
        decision_end_hours=decision_end_hours,
        follow_up_hours=376,
    )
    if family == "range_mean_reversion":
        configuration = replace(configuration, research_equity_quote=Decimal(9500))
    population = _evaluate_synthetic_epoch(store, configuration)
    binding = population.configuration.input_binding
    grant = StudyExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        str(uuid.uuid4()),
        "a" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        binding.source_id.value,
        binding.dataset_version,
        binding.manifest_sha256,
    )
    records: list[dict[str, object]] = []

    def retain(
        population: ResearchV2DecisionPopulation,
        index: int,
        scenario: AllFillScenario,
        result: AllFillReplayResult,
    ) -> None:
        assert result.end == population.decisions[index].identity.observed_at + FOLLOW_UP
        records.append(authority_result_record(population, index, scenario, result))

    _, base, stress = execute_synthetic_task(
        population, grant, actual_code_sha="a" * 40, result_sink=retain
    )
    assert any(row.side == expected_side and row.research_admissible for row in base)
    assert tuple(row.identity for row in base) == tuple(row.identity for row in stress)
    assert tuple(row.research_admissible for row in base) == tuple(
        row.research_admissible for row in stress
    )
    assert len(records) == 2 * len(population.decisions)
    _verify_authority_rows(
        io.BytesIO(b"".join(_canonical(record) + b"\n" for record in records)),
        population,
        base,
        stress,
        synthetic=True,
    )


@pytest.fixture(scope="module")
def compact_synthetic_execution() -> tuple[
    ResearchV2DecisionPopulation,
    StudyExecutionGrant,
    tuple[ScreenObservation, ...],
    tuple[ScreenObservation, ...],
    bytes,
]:
    store = SyntheticEpochStore(SYMBOL, hours=372)
    population = _evaluate_synthetic_epoch(
        store,
        _config(family="breakout_retest", store=store, decision_end_hours=201, follow_up_hours=372),
    )
    binding = population.configuration.input_binding
    grant = StudyExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        str(uuid.uuid4()),
        "a" * 40,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        binding.source_id.value,
        binding.dataset_version,
        binding.manifest_sha256,
    )
    records: list[dict[str, object]] = []

    def retain(
        population: ResearchV2DecisionPopulation,
        index: int,
        scenario: AllFillScenario,
        result: AllFillReplayResult,
    ) -> None:
        assert result.end == population.decisions[index].identity.observed_at + FOLLOW_UP
        records.append(authority_result_record(population, index, scenario, result))

    _, base, stress = execute_synthetic_task(
        population, grant, actual_code_sha="a" * 40, result_sink=retain
    )
    assert len(base) == len(stress) == len(population.decisions) == 4
    assert len(records) == 8
    authority = gzip.compress(b"".join(_canonical(record) + b"\n" for record in records), mtime=0)
    return population, grant, base, stress, authority


def test_compact_synthetic_task_replays_horizons_and_verifies_completed_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compact_synthetic_execution: tuple[
        ResearchV2DecisionPopulation,
        StudyExecutionGrant,
        tuple[ScreenObservation, ...],
        tuple[ScreenObservation, ...],
        bytes,
    ],
) -> None:
    population, grant, base, stress, authority = compact_synthetic_execution
    _verify_authority_rows(
        io.BytesIO(gzip.decompress(authority)), population, base, stress, synthetic=True
    )
    ledger = StudyLedgerStore.create(
        tmp_path, grant, actual_sha="a" * 40, synthetic=True, synthetic_single_task=True
    )
    ledger.start(0)
    population_bytes = gzip.compress(
        _canonical({"schema_version": EVIDENCE_SCHEMA, "population": encode_authority(population)}),
        mtime=0,
    )
    partition_bytes = _canonical(
        {
            "schema_version": EVIDENCE_SCHEMA,
            "population_seal": population.integrity_seal,
            "base": tuple(_row_record(row) for row in base),
            "stress": tuple(_row_record(row) for row in stress),
        }
    )
    (ledger.directory / "00.population.json.gz").write_bytes(population_bytes)
    (ledger.directory / "00.partition.json").write_bytes(partition_bytes)
    (ledger.directory / "00.authority.jsonl.gz").write_bytes(authority)
    ledger.complete(
        0,
        population_sha256=hashlib.sha256(population_bytes).hexdigest(),
        partition_sha256=hashlib.sha256(partition_bytes).hexdigest(),
        authority_sha256=hashlib.sha256(authority).hexdigest(),
    )
    projection = _bind_final_projection(
        {"base": _cell_summary(base), "stress": _cell_summary(stress)}, ledger.load()
    )
    projection_bytes = _canonical(projection)
    (ledger.directory / "projection.json").write_bytes(projection_bytes)
    ledger.finalize(hashlib.sha256(projection_bytes).hexdigest())
    original_validation = ResearchV2DecisionPopulation._validate_population_facts
    validation_calls = 0

    def counted_validation(self: ResearchV2DecisionPopulation, kind: str, schema: str) -> None:
        nonlocal validation_calls
        validation_calls += 1
        original_validation(self, kind, schema)

    monkeypatch.setattr(
        ResearchV2DecisionPopulation, "_validate_population_facts", counted_validation
    )
    assert _verify_synthetic_complete(ledger.directory) == projection
    assert validation_calls == 1
    with pytest.raises(ExploratoryScreenError, match="synthetic ledger"):
        from scripts.research.research_v2_exploratory_screen import _verify_complete

        _verify_complete(ledger)


def test_offline_replay_evidence_rejects_forged_compact_facts_and_missing_result(
    compact_synthetic_execution: tuple[
        ResearchV2DecisionPopulation,
        StudyExecutionGrant,
        tuple[ScreenObservation, ...],
        tuple[ScreenObservation, ...],
        bytes,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    population, _, base, stress, authority = compact_synthetic_execution
    raw = gzip.decompress(authority)
    closed_index = next(
        index for index, row in enumerate(base) if row.primary_closed_net_r is not None
    )
    closed = base[closed_index]
    assert closed.primary_closed_net_r is not None
    for changed in (
        replace(closed, primary_closed_net_r=closed.primary_closed_net_r + Decimal("0.01")),
        replace(closed, primary_fee_quote=closed.primary_fee_quote + Decimal("0.01")),
        replace(closed, side="SHORT"),
        replace(closed, identity="0" * 64),
        replace(
            closed,
            status=AllFillStatus.NO_SIGNAL,
            research_admissible=False,
            primary_closed_net_r=None,
            primary_fee_quote=Decimal(0),
            primary_gross_pnl_quote=None,
            primary_net_pnl_quote=None,
            side="NONE",
        ),
    ):
        forged = (*base[:closed_index], changed, *base[closed_index + 1 :])
        recomputed_container = {
            "rows": tuple(_row_record(row) for row in forged),
            "summary": _cell_summary(forged),
        }
        assert hashlib.sha256(_canonical(recomputed_container)).hexdigest()
        with pytest.raises(ExploratoryScreenError, match="differs from replay authority"):
            _verify_authority_rows(io.BytesIO(raw), population, forged, stress, synthetic=True)
    with pytest.raises(ExploratoryScreenError, match="incomplete"):
        _verify_authority_rows(
            io.BytesIO(b"".join(raw.splitlines(keepends=True)[:-1])),
            population,
            base,
            stress,
            synthetic=True,
        )
    shifted_lines = raw.splitlines(keepends=True)
    shifted_record = json.loads(shifted_lines[closed_index])
    for name, value in shifted_record["result"][2]:
        if name == "execution_snapshot":
            assert value[0] == "candle_slice" and value[2] > value[1]
            value[1] += 1
            value[2] += 1
    shifted_lines[closed_index] = _canonical(shifted_record) + b"\n"
    with pytest.raises(ExploratoryScreenError, match="stored replay evidence"):
        _verify_authority_rows(
            io.BytesIO(b"".join(shifted_lines)), population, base, stress, synthetic=True
        )

    def forbidden_population_revalidation(self: ResearchV2DecisionPopulation) -> None:
        raise AssertionError("per-result population revalidation is unbounded")

    monkeypatch.setattr(ResearchV2DecisionPopulation, "validate", forbidden_population_revalidation)
    _verify_authority_rows(io.BytesIO(raw), population, base, stress, synthetic=True)


def test_final_projection_rejects_thirty_empty_task_labels() -> None:
    with pytest.raises(ExploratoryScreenError, match="incomplete task population"):
        project_frozen_screen(tuple((task, (), ()) for task in frozen_tasks()))


def test_complete_zero_opportunity_inventory_is_insufficient_not_missing() -> None:
    inventory = []
    for task in frozen_tasks():
        base = tuple(
            ScreenObservation(
                identity=f"{task.task_id}:{index}",
                observed_at=DECISION_START + (index + 1) * timedelta(minutes=15),
                wire_symbol=task.wire_symbol,
                family=AllFillFamily(task.family.upper()),
                scenario=AllFillScenario.BASE,
                status=AllFillStatus.NO_SIGNAL,
                research_admissible=False,
                primary_closed_net_r=None,
                primary_fee_quote=Decimal(0),
                primary_open_quantity=Decimal(0),
                primary_gross_pnl_quote=None,
                primary_net_pnl_quote=None,
                side="NONE",
                regime="NEUTRAL",
                result_sha256="a" * 64,
            )
            for index in range(6048)
        )
        stress = tuple(replace(row, scenario=AllFillScenario.COMBINED_STRESS) for row in base)
        inventory.append((task, base, stress))
    final = project_frozen_screen(tuple(inventory))
    assert final["complete_task_count"] == 30
    families = cast(dict[str, dict[str, object]], final["families"])
    assert all(
        family["classification"] == ScreenClassification.INSUFFICIENT_EVIDENCE.value
        for family in families.values()
    )
    task, base, stress = inventory[0]
    with pytest.raises(ExploratoryScreenError, match="incomplete task population"):
        project_frozen_screen(((task, base[:-1], stress[:-1]), *inventory[1:]))
    with pytest.raises(ExploratoryScreenError, match="incomplete task population"):
        project_frozen_screen(((task, (*base, base[-1]), stress), *inventory[1:]))
    with pytest.raises(ExploratoryScreenError, match="foreign or misordered"):
        project_frozen_screen(((task, (base[0], base[0], *base[2:]), stress), *inventory[1:]))
    with pytest.raises(ExploratoryScreenError, match="foreign or misordered"):
        project_frozen_screen(
            ((task, (replace(base[0], wire_symbol="DOGEUSDT"), *base[1:]), stress), *inventory[1:])
        )
    with pytest.raises(ExploratoryScreenError, match="foreign or misordered"):
        project_frozen_screen(
            (
                (
                    task,
                    (
                        replace(
                            base[0],
                            input_kind="SYNTHETIC_FIXTURE",
                            source_id="synthetic-fixture",
                            dataset_version="fixture-v1",
                            manifest_sha256="b" * 64,
                        ),
                        *base[1:],
                    ),
                    stress,
                ),
                *inventory[1:],
            )
        )


def test_hold_only_pair_is_a_separate_diagnostic_not_a_second_primary_trade() -> None:
    primary = replace(
        _row("BTCUSDT", 0, 0),
        family=AllFillFamily.TREND_PULLBACK,
        hold_only_status=AllFillStatus.ORDINARY_CLOSED,
        hold_only_closed_net_r=Decimal("-0.2500"),
        hold_only_fee_quote=Decimal("0.0300"),
        hold_only_open_quantity=Decimal(0),
        hold_only_branch_sha256="b" * 64,
    )
    assert _parse_row(json.loads(json.dumps(_row_record(primary)))) == primary
    cell = _cell_summary((primary,))
    hold = cast(dict[str, object], cell["hold_only_diagnostic"])
    assert cell["closed_primary"] == 1
    assert cell["mean_closed_net_r"] == "1"
    assert hold["mean_closed_net_r"] == "-0.2500"
    assert hold["closed"] == 1
    with pytest.raises(ExploratoryScreenError, match="hold-only"):
        replace(primary, family=AllFillFamily.BREAKOUT_RETEST).validate()
