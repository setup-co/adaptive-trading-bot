from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from pathlib import Path

import numpy as np
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from numpy.random import PCG64, Generator

import adaptive_trading_bot.research.grid_history_runner as runner_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    CalendarEpisode,
    CalendarStatus,
    GridHistoryWeek,
    LedgerWeekRecord,
    V3SourceIdentity,
    VerifiedCalendar,
    canonical_json,
)
from adaptive_trading_bot.research.grid_history_runner import (
    ACCEPTED_ENGINE_HEAD,
    ACCEPTED_PREFLIGHT_HEAD,
    ACCEPTED_SOURCE_ID,
    APPROVAL_SCHEMA,
    EXTERNAL_GRANT_KIND,
    RUNNER_SCHEMA,
    SYNTHETIC_GRANT_KIND,
    TASK_IDS,
    GridGrantApproval,
    GridHistoryRunnerError,
    GridStudyGrant,
    GridStudyLedgerStore,
    GridWeekSummary,
    TaskState,
    classify_task_reports,
    moving_block_lower_bound,
    run_one_shot,
    summarize_task,
    verified_checkout_identity,
    verify_finalized,
)
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_ENGINE_VERSION,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
    GRID_PROTOCOL_VERSION,
    GRID_STRATEGY_ID,
    GridEpisodeInput,
    GridEpisodeReport,
    GridHistoricalSourceAdmission,
    GridLedgerCarry,
    GridMinute,
    GridScenario,
    GridV1Engine,
    GridV1Error,
    healthy_grid_health,
    verified_grid_contract_identity,
)
from adaptive_trading_bot.storage import SourceId

REPO = Path(__file__).resolve().parents[2]


def _git(revision: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", revision],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _calendar() -> VerifiedCalendar:
    rows: list[CalendarEpisode] = []
    start = datetime(2026, 1, 5, tzinfo=UTC)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for offset, status in enumerate(
            (CalendarStatus.METADATA_ELIGIBLE, CalendarStatus.METADATA_INELIGIBLE)
        ):
            episode = start + timedelta(days=7 * offset)
            rows.append(
                CalendarEpisode(
                    symbol,
                    episode,
                    status,
                    "VALIDATION",
                    episode - timedelta(days=7),
                    episode + timedelta(days=6, hours=20, minutes=2),
                    () if status is CalendarStatus.METADATA_ELIGIBLE else ("FIXTURE_EXCLUDED",),
                    (),
                )
            )
    return VerifiedCalendar.synthetic(tuple(rows), fixture_id="one-shot")


def _fixture_identity(fixture_sha: str) -> V3SourceIdentity:
    return V3SourceIdentity(
        fixture_sha,
        fixture_sha,
        "synthetic-grid-history-runner-v1",
        "SYNTHETIC_GRID_HISTORY_RUNNER_V1",
        "FIXTURE_ONLY",
    )


def _loader(
    identity: V3SourceIdentity,
    *,
    incomplete: bool = False,
    corrupt_source: bool = False,
) -> Callable[[CalendarEpisode], GridHistoryWeek]:
    def load(row: CalendarEpisode) -> GridHistoryWeek:
        price = Decimal("100") if row.symbol == "BTCUSDT" else Decimal("50")
        count = 6 * 24 * 60 + 20 * 60 + (1 if incomplete else 2)
        minutes = tuple(
            GridMinute(
                row.episode_start + timedelta(minutes=index),
                price,
                price,
                price,
                price,
                row.episode_start + timedelta(minutes=index + 1),
                price,
            )
            for index in range(count)
        )
        source = replace(identity, manifest_sha256="f" * 64) if corrupt_source else identity
        return GridHistoryWeek(
            source,
            row,
            (price,) * 42,
            minutes,
            (("fixture.json", identity.manifest_sha256),),
        )

    return load


def _grant(
    fixture_sha: str, *, run_uuid: str = "f02fd9cc-3784-4b96-9b61-d2a272d2d6af"
) -> GridStudyGrant:
    return GridStudyGrant(
        RUNNER_SCHEMA,
        SYNTHETIC_GRANT_KIND,
        run_uuid,
        _git("HEAD"),
        _git("HEAD^{tree}"),
        "BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1_HISTORY_STUDY",
        "a598c41bcd9775f42e2faa46cb88170563a17ed110ab0e3612850cd2303ffd6c",
        ACCEPTED_PREFLIGHT_HEAD,
        ACCEPTED_ENGINE_HEAD,
        GRID_ENGINE_VERSION,
        GRID_STRATEGY_ID,
        GRID_PROTOCOL_VERSION,
        GRID_ACCEPTED_PROTOCOL_HEAD,
        GRID_PROTOCOL_DOCUMENT_SHA256,
        GRID_PARAMETERS_SHA256,
        fixture_sha,
        "synthetic-grid-history-runner-v1",
        "SYNTHETIC_GRID_HISTORY_RUNNER_V1",
        fixture_sha,
        fixture_sha,
        "synthetic-grid-runner-fixture",
        TASK_IDS,
        ("BASE", "STRESS"),
        0,
        1,
        120,
    )


def _external_grant() -> GridStudyGrant:
    return replace(
        _grant("6" * 64),
        kind=EXTERNAL_GRANT_KIND,
        calendar_sha256=ACCEPTED_CALENDAR_SHA256,
        dataset_version=ACCEPTED_DATASET_VERSION,
        qualification_protocol=ACCEPTED_QUALIFICATION_PROTOCOL,
        manifest_sha256=ACCEPTED_V3_MANIFEST_SHA256,
        report_sha256=ACCEPTED_V3_REPORT_SHA256,
        source_id=ACCEPTED_SOURCE_ID,
    )


def _signed_approval(
    grant: GridStudyGrant, private_key: Ed25519PrivateKey
) -> tuple[GridGrantApproval, str]:
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    approval = GridGrantApproval(
        APPROVAL_SCHEMA,
        hashlib.sha256(public_bytes).hexdigest(),
        grant.sha256,
        private_key.sign(canonical_json(asdict(grant))).hex(),
    )
    return approval, public_bytes.hex()


def _minute_audit(sequence: int, equity: str, peak: str) -> dict[str, object]:
    return {
        "sequence": sequence,
        "observed_at": datetime(2026, 1, 5, 0, sequence, tzinfo=UTC).isoformat(),
        "event_type": "MINUTE_MARKED",
        "order_id": None,
        "lot_id": None,
        "details": {"equity": equity, "peak_equity": peak},
    }


def _recovered_report(*, episode_start: datetime, final_equity: str) -> GridEpisodeReport:
    return GridEpisodeReport(
        "btc-eth-grid-v1-synthetic-report",
        GRID_STRATEGY_ID,
        GRID_PROTOCOL_VERSION,
        GRID_ENGINE_VERSION,
        _git("HEAD"),
        {key: str(value) for key, value in asdict(verified_grid_contract_identity()).items()},
        "synthetic-grid-runner-fixture",
        "BTC/USDT",
        "BASE",
        episode_start.isoformat(),
        "COMPLETE",
        "GRID_ACTIVE",
        None,
        {"current_equity": "10000"},
        {"current_equity": final_equity},
        {"required_minute_count": 3},
        {},
        {},
        {"running_peak_drawdown_fraction": "0.04166666666666666666666666667"},
        (
            _minute_audit(1, "12000", "12000"),
            _minute_audit(2, "9000", "12000"),
            _minute_audit(3, final_equity, "12000"),
        ),
    )


def _classification_report(task_id: str) -> dict[str, object]:
    partition = {
        "scheduled_weeks": 80,
        "eligible_weeks": 80,
        "excluded_weeks": 0,
        "fill_weeks": 40,
        "opening_equity": "10000",
        "closing_equity": "10100",
        "total_net_equity_change": "100",
        "total_net_return": "0.01",
        "maximum_running_peak_drawdown_fraction": "0.10",
    }
    return {
        "task_id": task_id,
        "validation_eligible_weeks": 80,
        "validation_fill_weeks": 40,
        "validation_median_weekly_return": "0.001",
        "validation_mean_lower_95_bound": "0.0001",
        "retrospective_median_weekly_return": "0.001",
        "partition_metrics": {
            "VALIDATION": dict(partition),
            "RETROSPECTIVE_ASSESSMENT": dict(partition),
        },
        "terminated": False,
    }


def test_synthetic_four_task_flow_is_durable_and_read_only_verifiable(tmp_path: Path) -> None:
    fixture_sha = "1" * 64
    grant = _grant(fixture_sha)
    store, projection = run_one_shot(
        grant=grant,
        calendar=_calendar(),
        ledger_root=tmp_path,
        actual_sha=_git("HEAD"),
        actual_tree=_git("HEAD^{tree}"),
        week_loader=_loader(_fixture_identity(fixture_sha)),
        preflight={"schema": "synthetic-preflight-v1", "network_requests": 0},
        synthetic=True,
    )
    state = store.load()
    assert state.task_states == (TaskState.COMPLETED,) * 4
    assert state.week_counts == (2, 2, 2, 2)
    assert state.revision == 18
    assert json.loads((store.directory / "run-exit.json").read_bytes())["exit_code"] == 0
    assert projection["classification"] == "INSUFFICIENT_EVIDENCE"
    task_reports = projection["task_reports"]
    assert isinstance(task_reports, list)
    assert all(report["eligible_denominator"] == 1 for report in task_reports)
    assert all(report["metadata_ineligible"] == 1 for report in task_reports)
    assert all(report["fill_count"] == 0 for report in task_reports)
    verified = verify_finalized(store.directory)
    assert verified["status"] == "ARTIFACT_CONSISTENCY_VERIFIED"
    verification_scope = verified["verification_scope"]
    assert isinstance(verification_scope, str)
    assert "NOT_INDEPENDENT_ECONOMIC_REPLICATION" in verification_scope
    assert verified["task_states"] == ["COMPLETED"] * 4


def test_grant_gate_rejects_wrong_identity_and_synthetic_promotion() -> None:
    fixture_sha = "2" * 64
    grant = _grant(fixture_sha)
    with pytest.raises(GridHistoryRunnerError, match="identity or budget"):
        replace(grant, executable_sha="0" * 40).validate(
            actual_sha=_git("HEAD"), actual_tree=_git("HEAD^{tree}"), synthetic=True
        )
    for changed in (
        replace(grant, study_plan_sha256="0" * 64),
        replace(grant, preflight_head="0" * 40),
        replace(grant, parameters_sha256="0" * 64),
    ):
        with pytest.raises(GridHistoryRunnerError):
            changed.validate(
                actual_sha=_git("HEAD"), actual_tree=_git("HEAD^{tree}"), synthetic=True
            )
    with pytest.raises(GridHistoryRunnerError, match="fixture-only"):
        replace(
            grant,
            manifest_sha256="9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214",
        ).validate(actual_sha=_git("HEAD"), actual_tree=_git("HEAD^{tree}"), synthetic=True)
    with pytest.raises(GridHistoryRunnerError, match="historical grant identity differs"):
        grant.validate(actual_sha=_git("HEAD"), actual_tree=_git("HEAD^{tree}"), synthetic=False)
    fixture_approval, _public_key_hex = _signed_approval(
        grant,
        Ed25519PrivateKey.generate(),
    )
    with pytest.raises(GridHistoryRunnerError, match="fixture-only"):
        grant.validate(
            actual_sha=_git("HEAD"),
            actual_tree=_git("HEAD^{tree}"),
            synthetic=True,
            approval=fixture_approval,
        )


def test_external_grant_requires_independent_ed25519_approval() -> None:
    grant = _external_grant()
    with pytest.raises(GridHistoryRunnerError, match="approval is absent"):
        grant.validate(
            actual_sha=_git("HEAD"),
            actual_tree=_git("HEAD^{tree}"),
            synthetic=False,
        )
    approval, public_key_hex = _signed_approval(grant, Ed25519PrivateKey.generate())
    approval.verify(grant, trusted_public_key_hex=public_key_hex)
    forged, _ = _signed_approval(grant, Ed25519PrivateKey.generate())
    forged = replace(forged, key_id=approval.key_id)
    with pytest.raises(GridHistoryRunnerError, match="signature is invalid"):
        forged.verify(grant, trusted_public_key_hex=public_key_hex)
    with pytest.raises(GridHistoryRunnerError, match="approval provenance differs"):
        grant.validate(
            actual_sha=_git("HEAD"),
            actual_tree=_git("HEAD^{tree}"),
            synthetic=False,
            approval=approval,
        )
    with pytest.raises(GridHistoryRunnerError, match="approval provenance differs"):
        approval.verify(
            replace(grant, max_wall_seconds=grant.max_wall_seconds + 1),
            trusted_public_key_hex=public_key_hex,
        )
    with pytest.raises(GridHistoryRunnerError, match="approval provenance differs"):
        replace(approval, key_id="0" * 64).verify(
            grant,
            trusted_public_key_hex=public_key_hex,
        )
    for changed in (
        replace(grant, executable_sha="0" * 40),
        replace(grant, executable_tree="0" * 40),
        replace(grant, source_id="different-source"),
    ):
        with pytest.raises(GridHistoryRunnerError):
            changed.validate(
                actual_sha=_git("HEAD"),
                actual_tree=_git("HEAD^{tree}"),
                synthetic=False,
                approval=approval,
            )


def test_ledger_refuses_duplicate_uuid_start_resume_and_changed_artifact(tmp_path: Path) -> None:
    grant = _grant("3" * 64)
    store = GridStudyLedgerStore.create(tmp_path, grant)
    store.record_preflight({"schema": "synthetic-preflight-v1"})
    store.start(0)
    with pytest.raises(GridHistoryRunnerError, match="start, resume or rerun"):
        store.start(0)
    assert store.load().task_states[0] is TaskState.STARTED
    with pytest.raises(GridHistoryRunnerError, match="already exists"):
        GridStudyLedgerStore.create(tmp_path, grant)
    (store.directory / "preflight.json").write_bytes(b"changed")
    with pytest.raises(GridHistoryRunnerError, match="preflight artifact differs"):
        store.load()


@pytest.mark.parametrize("incomplete,corrupt", [(True, False), (False, True)])
def test_incomplete_or_source_mismatched_episode_becomes_unknown(
    tmp_path: Path, incomplete: bool, corrupt: bool
) -> None:
    fixture_sha = "4" * 64
    grant = _grant(fixture_sha)
    with pytest.raises(GridHistoryRunnerError, match="UNKNOWN"):
        run_one_shot(
            grant=grant,
            calendar=_calendar(),
            ledger_root=tmp_path,
            actual_sha=_git("HEAD"),
            actual_tree=_git("HEAD^{tree}"),
            week_loader=_loader(
                _fixture_identity(fixture_sha), incomplete=incomplete, corrupt_source=corrupt
            ),
            preflight={"schema": "synthetic-preflight-v1"},
            synthetic=True,
        )
    state = GridStudyLedgerStore(tmp_path / grant.run_uuid).load()
    assert state.task_states[0] is TaskState.UNKNOWN
    assert state.task_states[1:] == (TaskState.NOT_STARTED,) * 3


def test_non_synthetic_engine_input_requires_matching_admission() -> None:
    symbol = TradingSymbol("BTC", "USDT")
    start = datetime(2026, 1, 5, tzinfo=UTC)
    episode = GridEpisodeInput(
        SourceId("fixture-retained-v3"),
        symbol,
        start,
        (Decimal("100"),) * 42,
        "a" * 40,
        verified_grid_contract_identity(),
        GridLedgerCarry.initial(symbol),
    )
    health = healthy_grid_health(episode)
    with pytest.raises(GridV1Error, match="verified historical admission"):
        GridV1Engine(episode, GridScenario.BASE, health)
    with pytest.raises(GridV1Error, match="verified-grant factory"):
        GridHistoricalSourceAdmission(
            episode.source_id,
            "a" * 40,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            "0" * 64,
        )
    admission = GridHistoricalSourceAdmission.from_verified_grant(
        episode.source_id,
        "a" * 40,
        "b" * 40,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "0" * 64,
    )
    engine = GridV1Engine(
        episode,
        GridScenario.BASE,
        health,
        historical_admission=admission,
    )
    assert engine.report().schema == "btc-eth-grid-v1-historical-report"
    assert engine.report().input_contract["source_mode"] == "HISTORICAL_RESEARCH_GRANT"


def test_fixed_pcg64_bootstrap_is_deterministic_and_context_independent() -> None:
    assert [int(value) for value in PCG64(241).random_raw(5)] == [
        4068375204327186869,
        6550784330610757275,
        13618598653077127767,
        7851333619796801121,
        4116955697039603463,
    ]
    assert Generator(PCG64(241)).integers(0, 7, size=20, dtype=np.uint64).tolist() == [
        3,
        1,
        0,
        2,
        0,
        5,
        1,
        2,
        5,
        1,
        4,
        3,
        3,
        0,
        1,
        3,
        1,
        3,
        0,
        0,
    ]
    values = tuple(Decimal(index) / Decimal("10000") for index in range(-5, 7))
    first = moving_block_lower_bound(values)
    with localcontext(Context(prec=7, rounding=ROUND_DOWN)):
        second = moving_block_lower_bound(values)
    assert first == second == Decimal("-0.00025")


def test_intrawEEK_drawdown_uses_every_minute_and_survives_terminal_recovery() -> None:
    first_start = datetime(2026, 1, 5, tzinfo=UTC)
    second_start = first_start + timedelta(days=7)
    first = _recovered_report(episode_start=first_start, final_equity="11500")
    second = replace(
        first,
        episode_start=second_start.isoformat(),
        carry_in={"current_equity": "11500"},
        carry_out={"current_equity": "11800"},
        snapshot={"running_peak_drawdown_fraction": "0.01666666666666666666666666667"},
        audit=(
            _minute_audit(1, "11500", "12000"),
            _minute_audit(2, "11700", "12000"),
            _minute_audit(3, "11800", "12000"),
        ),
    )
    records = tuple(
        LedgerWeekRecord(
            index,
            "BTCUSDT",
            "BASE",
            start.isoformat(),
            "VALIDATION",
            CalendarStatus.METADATA_ELIGIBLE.value,
            "ELIGIBLE_ENGINE_RESULT",
            True,
            weekly_return,
            (),
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "COMPLETE",
            "GRID_ACTIVE",
            None,
        )
        for index, start, weekly_return in (
            (1, first_start, "0.15"),
            (2, second_start, "0.0260869565217391304347826087"),
        )
    )
    final_carry = replace(
        GridLedgerCarry.initial(TradingSymbol("BTC", "USDT")),
        current_cash=Decimal("11800"),
        current_equity=Decimal("11800"),
        cumulative_gross_realized_pnl=Decimal("1800"),
        peak_equity=Decimal("12000"),
        previous_episode_start=second_start,
    )
    summary = summarize_task(
        task_id="BTCUSDT:BASE",
        records=records,
        week_summaries=(
            runner_module._summarize_episode_report(1, first),
            runner_module._summarize_episode_report(2, second),
        ),
        final_carry=final_carry,
    )
    assert summary["maximum_running_peak_drawdown_fraction"] == "0.25"
    partitions = summary["partition_metrics"]
    assert isinstance(partitions, dict)
    assert partitions["VALIDATION"]["maximum_running_peak_drawdown_fraction"] == "0.25"


def test_economic_classification_boundaries_are_explicit() -> None:
    reports = [_classification_report(task_id) for task_id in TASK_IDS]
    for report in reports:
        partitions = report["partition_metrics"]
        assert isinstance(partitions, dict)
        for partition in partitions.values():
            assert isinstance(partition, dict)
            partition["maximum_running_peak_drawdown_fraction"] = "0.20"
    assert classify_task_reports(reports[:-1]) == "TECHNICAL_OR_INTEGRITY_INVALID"

    insufficient = deepcopy(reports)
    insufficient[0]["validation_eligible_weeks"] = 79
    assert classify_task_reports(insufficient) == "INSUFFICIENT_EVIDENCE"

    negative = deepcopy(reports)
    partitions = negative[0]["partition_metrics"]
    assert isinstance(partitions, dict)
    validation = partitions["VALIDATION"]
    assert isinstance(validation, dict)
    validation["maximum_running_peak_drawdown_fraction"] = "0.2000000000000000000000000001"
    assert classify_task_reports(negative) == "NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION"

    assert classify_task_reports(reports) == "CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION"

    zero_economic_gate = deepcopy(reports)
    zero_economic_gate[0]["validation_median_weekly_return"] = "0"
    assert classify_task_reports(zero_economic_gate) == "NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION"


def test_actual_cli_writes_and_reads_final_synthetic_projection(tmp_path: Path) -> None:
    weeks: list[dict[str, object]] = []
    fixture = {
        "schema": "grid-history-synthetic-fixture-v1",
        "fixture_id": "cli-round-trip",
        "weeks": weeks,
    }
    start = datetime(2026, 1, 5, tzinfo=UTC)
    for symbol, price in (("BTCUSDT", "100"), ("ETHUSDT", "50")):
        for offset, status in enumerate(("METADATA_ELIGIBLE", "METADATA_INELIGIBLE")):
            weeks.append(
                {
                    "symbol": symbol,
                    "episode_start": (start + timedelta(days=7 * offset)).isoformat(),
                    "status": status,
                    "partition": "VALIDATION",
                    "reason_codes": [] if offset == 0 else ["FIXTURE_EXCLUDED"],
                    "flag_codes": [],
                    "price": price,
                }
            )
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_bytes(canonical_json(fixture))
    fixture_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    grant = _grant(fixture_sha, run_uuid="a0370908-1617-4b05-a4d0-46182d953482")
    grant_path = tmp_path / "grant.json"
    grant_path.write_bytes(canonical_json(asdict(grant)))
    ledger_root = tmp_path / "ledgers"
    command = [
        sys.executable,
        "-m",
        "scripts.research.grid_history_runner",
        "--repo",
        str(REPO),
        "synthetic-run",
        "--grant",
        str(grant_path),
        "--fixture",
        str(fixture_path),
        "--ledger-root",
        str(ledger_root),
    ]
    completed = subprocess.run(command, cwd=REPO, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    projection = json.loads(completed.stdout)
    assert projection["classification"] == "INSUFFICIENT_EVIDENCE"
    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.grid_history_runner",
            "--repo",
            str(REPO),
            "verify",
            "--ledger",
            str(ledger_root / grant.run_uuid),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["status"] == "ARTIFACT_CONSISTENCY_VERIFIED"


def test_no_grant_cli_fails_before_creating_a_ledger(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.grid_history_runner",
            "--repo",
            str(REPO),
            "run",
            "--grant",
            str(tmp_path / "missing-grant.json"),
            "--data-root",
            str(tmp_path),
            "--ledger-root",
            str(tmp_path / "ledgers"),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert not (tmp_path / "ledgers").exists()


def test_historical_checkout_gate_rejects_dirty_and_import_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    relative_paths = (
        "scripts/research/grid_history_runner.py",
        "src/adaptive_trading_bot/research/grid_history_runner.py",
        "src/adaptive_trading_bot/research/grid_history_preflight.py",
        "src/adaptive_trading_bot/research/grid_v1.py",
    )
    imported: dict[str, Path] = {}
    for index, relative in enumerate(relative_paths):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture {index}\n", encoding="utf-8", newline="\n")
        imported[relative] = path
    for command in (
        ("init",),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
        ("add", "."),
        ("commit", "-m", "fixture"),
    ):
        subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)

    head, tree = verified_checkout_identity(repo, imported_files=imported)
    assert len(head) == 40
    assert len(tree) == 40

    first = imported[relative_paths[0]]
    original = first.read_bytes()
    first.write_bytes(original + b"# dirty\n")
    with pytest.raises(GridHistoryRunnerError, match="exact clean checkout"):
        verified_checkout_identity(repo, imported_files=imported)
    first.write_bytes(original)

    outside = tmp_path / "outside.py"
    outside.write_bytes(original)
    mismatched = {**imported, relative_paths[0]: outside}
    with pytest.raises(GridHistoryRunnerError, match="outside the named checkout"):
        verified_checkout_identity(repo, imported_files=mismatched)


def test_week_summary_drops_full_audit_after_extracting_authoritative_facts() -> None:
    report = _recovered_report(
        episode_start=datetime(2026, 1, 5, tzinfo=UTC),
        final_equity="11500",
    )
    large_audit: tuple[dict[str, object], ...] = tuple(
        {
            "sequence": index,
            "observed_at": "2026-01-05T00:00:00+00:00",
            "event_type": "MINUTE_MARKED",
            "order_id": None,
            "lot_id": None,
            "details": {"equity": "11500", "peak_equity": "12000"},
        }
        for index in range(1, 1001)
    )
    report = replace(report, input_contract={"required_minute_count": 1000}, audit=large_audit)

    summary = runner_module._summarize_episode_report(7, report)

    assert isinstance(summary, GridWeekSummary)
    assert asdict(summary) == {
        "sequence": 7,
        "current_equity": Decimal("11500"),
        "order_fill_count": 0,
        "fill_count": 0,
        "forced_close_count": 0,
        "maximum_running_peak_drawdown_fraction": Decimal("0.04166666666666666666666666666666667"),
    }
    assert not hasattr(summary, "audit")
    assert len(repr(summary)) < 300


def test_incremental_append_hash_reads_scale_linearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant = _grant("8" * 64)
    store = GridStudyLedgerStore.create(tmp_path, grant)
    observed_week_hashes = 0
    original_sha_file = runner_module._sha_file

    def counted_sha_file(path: Path) -> str:
        nonlocal observed_week_hashes
        if path.name.endswith(".week.json"):
            observed_week_hashes += 1
        return original_sha_file(path)

    monkeypatch.setattr(runner_module, "_sha_file", counted_sha_file)
    store.record_preflight({"schema": "synthetic-preflight-v1"})
    store.start(0)
    week_count = 64
    for ordinal in range(week_count):
        store.commit_week(
            0,
            ordinal,
            {
                "episode_start": f"synthetic-{ordinal:03d}",
                "transition": "SYNTHETIC",
            },
        )
    assert observed_week_hashes == week_count

    verified = store.load()

    assert verified.week_counts[0] == week_count
    assert observed_week_hashes == week_count * 2


def test_fixed_wall_budget_stops_once_and_records_actual_failure(tmp_path: Path) -> None:
    fixture_sha = "5" * 64
    grant = replace(_grant(fixture_sha), max_wall_seconds=1)
    observations = iter((0.0, 0.0, 0.0, 2.0, 2.0))

    def clock() -> float:
        return next(observations, 2.0)

    with pytest.raises(GridHistoryRunnerError, match="budget exhausted"):
        run_one_shot(
            grant=grant,
            calendar=_calendar(),
            ledger_root=tmp_path,
            actual_sha=_git("HEAD"),
            actual_tree=_git("HEAD^{tree}"),
            week_loader=_loader(_fixture_identity(fixture_sha)),
            preflight={"schema": "synthetic-preflight-v1"},
            synthetic=True,
            clock=clock,
        )
    store = GridStudyLedgerStore(tmp_path / grant.run_uuid)
    state = store.load()
    assert state.finalized_sha256 is None
    assert json.loads((store.directory / "run-exit.json").read_bytes()) == {
        "elapsed_seconds": "2.0",
        "exit_code": 1,
        "run_uuid": grant.run_uuid,
        "schema": "btc-eth-grid-history-run-exit-v1",
        "status": "FAILED_OR_UNKNOWN",
    }
