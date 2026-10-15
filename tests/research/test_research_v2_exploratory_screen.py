"""Synthetic-only characterization of the frozen exploratory study boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from test_research_v2_admission import SYMBOL, SyntheticEpochStore, _config

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillFamily,
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
    ResearchV2EpochBounds,
    _evaluate_synthetic_epoch,
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
    causal_follow_up_slice,
    classify_family,
    decision_cutoff,
    execute_granted_task,
    frozen_tasks,
    horizon_mask,
    observation_bin,
    project_frozen_screen,
    protocol_facts,
)
from adaptive_trading_bot.research.research_v2_screen_ledger import StudyLedgerStore
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _INTEGRITY_SEAL_VERSION,
    _canonical_integrity_value,
)
from adaptive_trading_bot.risk import OperationalSafetyConfiguration, RiskManagerConfiguration


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
    (ledger.directory / "00.population.json.gz").write_bytes(population_bytes)
    (ledger.directory / "00.partition.json").write_bytes(partition_bytes)
    ledger.complete(
        0,
        population_sha256=hashlib.sha256(population_bytes).hexdigest(),
        partition_sha256=hashlib.sha256(partition_bytes).hexdigest(),
    )
    assert StudyLedgerStore(ledger.directory).load().task_states[0] == "COMPLETED"
    with pytest.raises(ExploratoryScreenError):
        ledger.start(0)
    with pytest.raises(ExploratoryScreenError):
        ledger.finalize("d" * 64)
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
