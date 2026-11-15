from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from pathlib import Path

import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.grid_history_preflight import (
    CalendarEpisode,
    CalendarStatus,
    GridHistoryWeek,
    V3SourceIdentity,
    VerifiedCalendar,
    canonical_json,
)
from adaptive_trading_bot.research.grid_history_runner import (
    ACCEPTED_ENGINE_HEAD,
    ACCEPTED_PREFLIGHT_HEAD,
    RUNNER_SCHEMA,
    SYNTHETIC_GRANT_KIND,
    TASK_IDS,
    GridHistoryRunnerError,
    GridStudyGrant,
    GridStudyLedgerStore,
    TaskState,
    moving_block_lower_bound,
    run_one_shot,
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
    assert verified["status"] == "VERIFIED"
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
    with pytest.raises(GridHistoryRunnerError, match="reviewed historical grant is absent"):
        grant.validate(actual_sha=_git("HEAD"), actual_tree=_git("HEAD^{tree}"), synthetic=False)


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
    values = tuple(Decimal(index) / Decimal("10000") for index in range(-5, 7))
    first = moving_block_lower_bound(values)
    with localcontext(Context(prec=7, rounding=ROUND_DOWN)):
        second = moving_block_lower_bound(values)
    assert first == second
    assert first is not None


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
    assert json.loads(verify.stdout)["status"] == "VERIFIED"


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
