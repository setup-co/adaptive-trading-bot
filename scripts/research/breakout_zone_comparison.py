"""Data-only plan plus externally granted Breakout zone comparison execution."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from adaptive_trading_bot.research.breakout_zone_comparison import (
    DECISION_END,
    DECISION_START,
    FOLLOW_UP_END,
    TASKS,
    BreakoutComparisonPopulation,
    BreakoutComparisonReplayResult,
    BreakoutZoneComparisonError,
    ComparisonArm,
    ComparisonExecutionGrant,
    ComparisonTask,
    _canonical,
    comparison_result_record,
    data_only_preflight,
    execute_granted_task,
    project_final_report,
    project_task_report,
    recover_comparison_result,
    verify_imported_baseline_authority,
    verify_retained_baseline_authority,
)
from adaptive_trading_bot.research.breakout_zone_comparison_ledger import (
    ComparisonLedgerState,
    ComparisonLedgerStore,
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


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _required_int(record: dict[str, object], key: str) -> int:
    value = record.get(key)
    if type(value) is not int:
        raise BreakoutZoneComparisonError(f"{key} is not an exact integer")
    return value


def _clean_sha(repo: Path) -> str:
    status = subprocess.run(
        ("git", "status", "--porcelain"), cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    if status:
        raise BreakoutZoneComparisonError("comparison execution checkout is not clean")
    return subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@contextmanager
def _no_network() -> Iterator[None]:
    def rejected(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise BreakoutZoneComparisonError("network disabled during locked comparison")

    with (
        patch.object(socket.socket, "connect", rejected),
        patch.object(socket.socket, "connect_ex", rejected),
        patch.object(socket, "create_connection", rejected),
    ):
        yield


def _immutable_write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(payload).hexdigest()


def _population_bytes(population: BreakoutComparisonPopulation) -> bytes:
    population.validate()
    payload = _canonical(
        {"schema_version": EVIDENCE_SCHEMA, "population": encode_authority(population)}
    )
    return gzip.compress(payload, mtime=0)


def _results_bytes(
    population: BreakoutComparisonPopulation,
    results: tuple[BreakoutComparisonReplayResult, ...],
) -> bytes:
    candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
    candle_opens = tuple(candle.open_time for candle in candles)
    decision_indexes = {
        arm: {
            decision.identity.observed_at: index
            for index, decision in enumerate(
                population.baseline_population.decisions
                if arm.value == "BASELINE_1_0_0"
                else population.zone_decisions
            )
        }
        for arm in ComparisonArm
    }
    lines = tuple(
        _canonical(
            comparison_result_record(
                result,
                population,
                candles=candles,
                candle_opens=candle_opens,
                decision_indexes=decision_indexes,
            )
        )
        for result in results
    )
    return gzip.compress(b"".join(line + b"\n" for line in lines), mtime=0)


def _run(repo: Path, grant_path: Path, ledger_root: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    grant_path = grant_path.resolve(strict=True)
    ledger_root = ledger_root.resolve()
    if repo == grant_path or repo in grant_path.parents or repo in ledger_root.parents:
        raise BreakoutZoneComparisonError("grant and ledger must remain outside repository")
    actual_sha = _clean_sha(repo)
    grant = ComparisonExecutionGrant.from_file(grant_path)
    grant.validate(actual_sha=actual_sha)
    data_only_preflight(repo)
    ledger = ComparisonLedgerStore.create(ledger_root, grant, actual_sha=actual_sha)
    evidence: list[
        tuple[
            BreakoutComparisonPopulation,
            tuple[BreakoutComparisonReplayResult, ...],
            dict[str, object],
        ]
    ] = []
    with _no_network():
        for task in TASKS:
            ledger.start(task.index)
            try:
                population, results = execute_granted_task(repo, task, grant, actual_sha=actual_sha)
                report = project_task_report(population, results)
                pop_sha = _immutable_write(
                    ledger.directory / f"{task.index:02d}.population.json.gz",
                    _population_bytes(population),
                )
                results_sha = _immutable_write(
                    ledger.directory / f"{task.index:02d}.results.jsonl.gz",
                    _results_bytes(population, results),
                )
                report_sha = _immutable_write(
                    ledger.directory / f"{task.index:02d}.report.json", _canonical(report)
                )
                ledger.complete(
                    task.index,
                    population_sha256=pop_sha,
                    results_sha256=results_sha,
                    report_sha256=report_sha,
                )
                evidence.append((population, results, report))
            except Exception:
                ledger.fail(task.index)
                raise
        projection = project_final_report(tuple(evidence))
        projection_sha = _immutable_write(
            ledger.directory / "projection.json", _canonical(projection)
        )
        ledger.finalize(projection_sha)
    return projection


def _read_task(
    repo: Path,
    ledger: ComparisonLedgerStore,
    state: ComparisonLedgerState,
    index: int,
    *,
    task: ComparisonTask | None = None,
    retained_authority: tuple[Path, str] | None = None,
) -> tuple[
    BreakoutComparisonPopulation,
    tuple[BreakoutComparisonReplayResult, ...],
    dict[str, object],
]:
    try:
        pop_raw = gzip.decompress(
            (ledger.directory / f"{index:02d}.population.json.gz").read_bytes()
        )
        payload = json.loads(pop_raw)
        if _canonical(payload) != pop_raw or payload.get("schema_version") != EVIDENCE_SCHEMA:
            raise ValueError("population")
        population = decode_authority(payload["population"])
        if not isinstance(population, BreakoutComparisonPopulation):
            raise ValueError("population type")
        population.validate()
        expected_task = (
            TASKS[index] if state.input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT" else task
        )
        population.provenance.validate(task=expected_task)
        if (
            population.provenance.input_kind != state.input_kind
            or population.provenance.run_uuid != state.run_uuid
            or population.provenance.executable_sha != state.executable_sha
            or population.provenance.protocol_sha256 != state.protocol_sha256
            or population.provenance.task_plan_sha256 != state.task_plan_sha256
            or population.provenance.source_id != state.source_id
            or population.provenance.dataset_version != state.dataset_version
            or population.provenance.manifest_sha256 != state.manifest_sha256
            or population.provenance.artifact_index_sha256 != state.artifact_index_sha256
            or population.task_id != state.task_ids[index]
            or (
                state.input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT"
                and expected_task is not None
                and (
                    population.baseline_population.configuration.bounds.symbol
                    != expected_task.symbol
                    or population.baseline_population.configuration.bounds.context_start
                    != expected_task.context_start
                    or population.baseline_population.configuration.bounds.decision_start
                    != DECISION_START
                    or population.baseline_population.configuration.bounds.decision_end
                    != DECISION_END
                    or population.baseline_population.configuration.bounds.follow_up_end
                    != FOLLOW_UP_END
                )
            )
        ):
            raise ValueError("task provenance")
        candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
        results: list[BreakoutComparisonReplayResult] = []
        with gzip.open(ledger.directory / f"{index:02d}.results.jsonl.gz", "rb") as handle:
            for line in handle:
                if not line.endswith(b"\n"):
                    raise ValueError("result line")
                record = json.loads(line)
                if _canonical(record) + b"\n" != line:
                    raise ValueError("result canonical")
                results.append(recover_comparison_result(record, population, candles=candles))
        report_path = ledger.directory / f"{index:02d}.report.json"
        report_raw = report_path.read_bytes()
        report = json.loads(report_raw)
        if _canonical(report) != report_raw:
            raise ValueError("report canonical")
    except (OSError, ValueError, KeyError, ScreenEvidenceError) as exc:
        raise BreakoutZoneComparisonError("stored comparison task failed verification") from exc
    result_tuple = tuple(results)
    rebuilt = project_task_report(population, result_tuple)
    if _canonical(rebuilt) != report_raw:
        raise BreakoutZoneComparisonError("stored report differs from retained authority")
    if state.input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT":
        verify_imported_baseline_authority(
            repo,
            TASKS[index],
            population,
            result_tuple,
        )
    elif retained_authority is not None:
        verify_retained_baseline_authority(
            retained_authority[0],
            retained_authority[1],
            population,
            result_tuple,
        )
    return population, result_tuple, rebuilt


def _verify(repo: Path, ledger_dir: Path) -> dict[str, object]:
    ledger = ComparisonLedgerStore(ledger_dir.resolve(strict=True))
    state = ledger.load()
    if (
        state.input_kind != "EXTERNAL_REVIEWED_EXECUTION_GRANT"
        or any(value != "COMPLETED" for value in state.task_states)
        or state.finalized_sha256 is None
    ):
        raise BreakoutZoneComparisonError("comparison ledger is not finalized authority")
    evidence = tuple(_read_task(repo, ledger, state, task.index) for task in TASKS)
    projection = project_final_report(evidence)
    projection_path = ledger.directory / "projection.json"
    stored_raw = projection_path.read_bytes()
    stored = json.loads(stored_raw)
    if (
        _canonical(stored) != stored_raw
        or _canonical(projection) != stored_raw
        or _file_sha(projection_path) != state.finalized_sha256
    ):
        raise BreakoutZoneComparisonError("final comparison projection changed")
    return projection


def _verify_synthetic(
    repo: Path,
    ledger_dir: Path,
    *,
    tasks: tuple[ComparisonTask, ...],
    retained_authorities: tuple[tuple[Path, str], ...],
) -> dict[str, object]:
    """Exercise the production persistence boundary with explicit synthetic authority."""

    ledger = ComparisonLedgerStore(ledger_dir.resolve(strict=True))
    state = ledger.load()
    if (
        state.input_kind != "SYNTHETIC_TEST_GRANT"
        or len(tasks) != len(retained_authorities)
        or len(tasks) != len(state.task_states)
        or any(value != "COMPLETED" for value in state.task_states)
        or state.finalized_sha256 is None
    ):
        raise BreakoutZoneComparisonError("synthetic comparison ledger is not finalized authority")
    evidence = tuple(
        _read_task(
            repo,
            ledger,
            state,
            index,
            task=task,
            retained_authority=retained_authorities[index],
        )
        for index, task in enumerate(tasks)
    )
    projection = project_final_report(
        evidence,
        tasks=tasks,
        enforce_real_candidate_bound=False,
    )
    projection_path = ledger.directory / "projection.json"
    stored_raw = projection_path.read_bytes()
    if (
        _canonical(json.loads(stored_raw)) != stored_raw
        or _canonical(projection) != stored_raw
        or _file_sha(projection_path) != state.finalized_sha256
    ):
        raise BreakoutZoneComparisonError("synthetic final comparison projection changed")
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    run = commands.add_parser("run")
    run.add_argument("--grant", type=Path, required=True)
    run.add_argument("--ledger-root", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = data_only_preflight(args.repo)
    elif args.command == "run":
        result = _run(args.repo, args.grant, args.ledger_root)
    else:
        result = _verify(args.repo.resolve(strict=True), args.ledger)
    print(_canonical(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
