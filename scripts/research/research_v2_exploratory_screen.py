"""Offline plan, externally granted one-shot execution, and result verification."""

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
from datetime import datetime
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

from adaptive_trading_bot.domain import Candle
from adaptive_trading_bot.research.all_fill_replay import AllFillReplayResult, AllFillScenario
from adaptive_trading_bot.research.research_v2_admission import (
    ResearchV2DecisionPopulation,
    ResearchV2StudyDecisionPopulation,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    ExploratoryScreenError,
    ScreenObservation,
    StudyExecutionGrant,
    _canonical,
    _cell_summary,
    _parse_row,
    _screen_row,
    _validate_task_population_facts,
    authority_result_record,
    data_only_plan,
    execute_granted_task,
    frozen_tasks,
    project_frozen_screen,
    project_task_partition,
    recover_authority_result,
    verify_task_partition,
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
from adaptive_trading_bot.research.research_v2_screen_ledger import (
    StudyLedgerState,
    StudyLedgerStore,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _clean_sha(repo: Path) -> str:
    if _git(repo, "status", "--porcelain"):
        raise ExploratoryScreenError("real study requires a clean reviewed code checkout")
    return _git(repo, "rev-parse", "HEAD")


def _immutable_write(path: Path, payload: bytes) -> str:
    if path.exists():
        raise ExploratoryScreenError("study artifact already exists; no overwrite")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha(payload)


@contextmanager
def _no_network() -> Iterator[None]:
    def rejected(*args: object, **kwargs: object) -> None:
        raise ExploratoryScreenError("network use is prohibited during locked-corpus study")

    with (
        patch.object(socket.socket, "connect", rejected),
        patch.object(socket.socket, "connect_ex", rejected),
        patch.object(socket, "create_connection", rejected),
    ):
        yield


def _run(repo: Path, grant_path: Path, ledger_root: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    grant_path = grant_path.resolve(strict=True)
    ledger_root = ledger_root.resolve(strict=True)
    if repo == grant_path or repo in grant_path.parents or repo in ledger_root.parents:
        raise ExploratoryScreenError(
            "grant and execution ledger must remain outside the repository"
        )
    actual_sha = _clean_sha(repo)
    grant = StudyExecutionGrant.from_file(grant_path)
    grant.validate(actual_sha=actual_sha)
    # All hash/epoch/calendar checks complete before a STARTED record or strategy evaluation.
    plan = data_only_plan(repo)
    if plan["protocol_sha256"] != PROTOCOL_SHA256 or plan["task_plan_sha256"] != TASK_PLAN_SHA256:
        raise ExploratoryScreenError("data-only task plan differs from frozen protocol")
    ledger = StudyLedgerStore.create(ledger_root, grant, actual_sha=actual_sha)
    with _no_network():
        from adaptive_trading_bot.research.research_v2_admission import (
            LockedResearchV2CandleStore,
        )

        store = LockedResearchV2CandleStore(repo)
        for task in frozen_tasks():
            ledger.start(task.index)
            # A STARTED task is never retried: any exception leaves durable ambiguity.
            pending = ledger.directory / f".{task.index:02d}.authority.pending"
            authority_path = ledger.directory / f"{task.index:02d}.authority.jsonl.gz"
            with pending.open("xb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                    cached_candles: tuple[Candle, ...] | None = None
                    cached_opens: tuple[datetime, ...] | None = None

                    def retain(
                        population: ResearchV2DecisionPopulation,
                        index: int,
                        scenario: AllFillScenario,
                        result: AllFillReplayResult,
                    ) -> None:
                        nonlocal cached_candles, cached_opens
                        if cached_candles is None:
                            cached_candles = tuple(
                                item.context.candle_15m for item in population.contexts
                            )
                            cached_opens = tuple(candle.open_time for candle in cached_candles)
                        compressed.write(
                            _canonical(
                                authority_result_record(
                                    population,
                                    index,
                                    scenario,
                                    result,
                                    candles=cached_candles,
                                    candle_opens=cached_opens,
                                )
                            )
                            + b"\n"
                        )

                    population, base, stress = execute_granted_task(
                        store,
                        task,
                        grant,
                        actual_code_sha=actual_sha,
                        result_sink=retain,
                    )
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(pending, authority_path)
            authority_hash = _file_sha(authority_path)
            partition = project_task_partition(task, population, base, stress)
            population_bytes = gzip.compress(
                _canonical(
                    {
                        "schema_version": EVIDENCE_SCHEMA,
                        "population": encode_authority(population),
                    }
                ),
                mtime=0,
            )
            population_hash = _immutable_write(
                ledger.directory / f"{task.index:02d}.population.json.gz", population_bytes
            )
            partition_hash = _immutable_write(
                ledger.directory / f"{task.index:02d}.partition.json", _canonical(partition)
            )
            ledger.complete(
                task.index,
                population_sha256=population_hash,
                partition_sha256=partition_hash,
                authority_sha256=authority_hash,
            )
        projection = _verify_complete(ledger)
        projection_hash = _immutable_write(
            ledger.directory / "projection.json", _canonical(projection)
        )
        ledger.finalize(projection_hash)
        return projection


def _verify_complete(ledger: StudyLedgerStore) -> dict[str, object]:
    state = ledger.load()
    if state.input_kind != "EXTERNAL_REVIEWED_EXECUTION_GRANT":
        raise ExploratoryScreenError("synthetic ledger cannot be relabeled as accepted history")
    if any(value != "COMPLETED" for value in state.task_states):
        raise ExploratoryScreenError("partial task ledger has no final economic screen")
    task_rows = []
    for task in frozen_tasks():
        partition_bytes = (ledger.directory / f"{task.index:02d}.partition.json").read_bytes()
        population_bytes = (ledger.directory / f"{task.index:02d}.population.json.gz").read_bytes()
        try:
            decompressed = gzip.decompress(population_bytes)
            population = json.loads(decompressed)
            if (
                not isinstance(population, dict)
                or set(population) != {"schema_version", "population"}
                or _canonical(population) != decompressed
                or population["schema_version"] != EVIDENCE_SCHEMA
            ):
                raise ExploratoryScreenError("stored population has an unknown study schema")
            rebuilt = decode_authority(population["population"])
            if not isinstance(rebuilt, ResearchV2StudyDecisionPopulation):
                raise ExploratoryScreenError("stored population is not accepted authority")
            if _canonical(encode_authority(rebuilt)) != _canonical(population["population"]):
                raise ExploratoryScreenError("stored population is not canonical authority")
            _validate_task_population_facts(rebuilt, task)
            population_seal = rebuilt.integrity_seal
        except (OSError, KeyError, ValueError, ScreenEvidenceError) as exc:
            raise ExploratoryScreenError("stored task population is unavailable") from exc
        base, stress = verify_task_partition(
            partition_bytes, task, expected_population_sha256=population_seal
        )
        cached_candles = tuple(item.context.candle_15m for item in rebuilt.contexts)
        authority_path = ledger.directory / f"{task.index:02d}.authority.jsonl.gz"
        with gzip.open(authority_path, "rb") as authority:
            _verify_authority_rows(authority, rebuilt, base, stress, candles=cached_candles)
        task_rows.append((task, base, stress))
    return _bind_final_projection(project_frozen_screen(tuple(task_rows)), state)


def _bind_final_projection(
    projection: dict[str, object], state: StudyLedgerState
) -> dict[str, object]:
    """Bind the published projection to its run, code, input kind and artifacts."""

    bound = dict(projection)
    bound.pop("projection_sha256", None)
    bound["run_uuid"] = state.run_uuid
    bound["executable_sha"] = state.executable_sha
    bound["input_kind"] = state.input_kind
    bound["task_artifacts"] = tuple(
        {
            "task_id": task_id,
            "population_sha256": population_sha,
            "partition_sha256": partition_sha,
            "authority_sha256": authority_sha,
        }
        for task_id, population_sha, partition_sha, authority_sha in zip(
            state.task_ids,
            state.population_sha256,
            state.partition_sha256,
            state.authority_sha256,
            strict=True,
        )
    )
    bound["projection_sha256"] = _sha(_canonical(bound))
    return bound


def _verify_authority_rows(
    authority: _EvidenceReader,
    population: ResearchV2DecisionPopulation,
    base: tuple[ScreenObservation, ...],
    stress: tuple[ScreenObservation, ...],
    *,
    candles: tuple[Candle, ...] | None = None,
    synthetic: bool = False,
) -> None:
    """One bounded pass: reconstruct replay/accounting and compare every compact row."""

    if len(base) != len(population.decisions) or len(stress) != len(population.decisions):
        raise ExploratoryScreenError("replay rows omit an authoritative decision")
    if candles is None:
        candles = tuple(item.context.candle_15m for item in population.contexts)
    for scenario, rows in ((AllFillScenario.BASE, base), (AllFillScenario.COMBINED_STRESS, stress)):
        for index, row in enumerate(rows):
            line = authority.readline()
            if not line.endswith(b"\n"):
                raise ExploratoryScreenError("replay authority evidence is incomplete")
            try:
                record = json.loads(line)
            except (UnicodeError, ValueError) as exc:
                raise ExploratoryScreenError("replay authority evidence is malformed") from exc
            if _canonical(record) + b"\n" != line:
                raise ExploratoryScreenError("replay authority evidence is not canonical")
            result = recover_authority_result(record, population, index, scenario, candles=candles)
            if (
                _screen_row(result, population, population.decisions[index], synthetic=synthetic)
                != row
            ):
                raise ExploratoryScreenError("compact row differs from replay authority")
    if authority.read(1):
        raise ExploratoryScreenError("replay authority has undeclared results")


class _EvidenceReader(Protocol):
    def readline(self, size: int = -1) -> bytes: ...
    def read(self, size: int = -1) -> bytes: ...


def _verify(ledger_dir: Path) -> dict[str, object]:
    ledger = StudyLedgerStore(ledger_dir)
    state = ledger.load()
    result = _verify_complete(ledger)
    projection_bytes = (ledger_dir / "projection.json").read_bytes()
    if state.finalized_sha256 != _sha(projection_bytes) or _canonical(result) != projection_bytes:
        raise ExploratoryScreenError("final projection differs from completed immutable tasks")
    return result


def _verify_synthetic_complete(ledger_dir: Path) -> dict[str, object]:
    """Tiny fixture-only final verifier; cannot certify accepted historical input."""

    ledger = StudyLedgerStore(ledger_dir)
    state = ledger.load()
    if (
        state.input_kind != "SYNTHETIC_TEST_GRANT"
        or state.task_ids != ("SYNTHETIC_ONE_TASK",)
        or state.task_states != ("COMPLETED",)
        or state.finalized_sha256 is None
    ):
        raise ExploratoryScreenError("fixture-only verifier requires one completed synthetic task")
    raw_population = json.loads(
        gzip.decompress((ledger_dir / "00.population.json.gz").read_bytes())
    )
    if raw_population.get("schema_version") != EVIDENCE_SCHEMA:
        raise ExploratoryScreenError("synthetic authority evidence schema differs")
    population = decode_authority(raw_population["population"])
    if type(population) is not ResearchV2DecisionPopulation:
        raise ExploratoryScreenError("fixture population was relabeled as accepted history")
    if population.configuration.input_binding.kind != "SYNTHETIC_FIXTURE":
        raise ExploratoryScreenError("fixture input binding is not synthetic")
    partition_bytes = (ledger_dir / "00.partition.json").read_bytes()
    partition = json.loads(partition_bytes)
    if (
        _canonical(partition) != partition_bytes
        or partition.get("schema_version") != EVIDENCE_SCHEMA
        or partition.get("population_seal") != population.integrity_seal
    ):
        raise ExploratoryScreenError("fixture partition differs from stored population")
    base = tuple(_parse_row(row) for row in partition["base"])
    stress = tuple(_parse_row(row) for row in partition["stress"])
    with gzip.open(ledger_dir / "00.authority.jsonl.gz", "rb") as authority:
        _verify_authority_rows(authority, population, base, stress, synthetic=True)
    projection = _bind_final_projection(
        {"base": _cell_summary(base), "stress": _cell_summary(stress)}, state
    )
    final_bytes = (ledger_dir / "projection.json").read_bytes()
    if _sha(final_bytes) != state.finalized_sha256 or final_bytes != _canonical(projection):
        raise ExploratoryScreenError("synthetic final projection differs from replay evidence")
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("preflight", help="verify locked data and print the 30-task plan")
    plan.add_argument("--repo", required=True, type=Path)
    execute = commands.add_parser("run", help="requires external exact-SHA execution grant")
    execute.add_argument("--repo", required=True, type=Path)
    execute.add_argument("--grant", required=True, type=Path)
    execute.add_argument("--ledger-root", required=True, type=Path)
    verify = commands.add_parser("verify", help="offline final-result verification")
    verify.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = data_only_plan(args.repo)
    elif args.command == "run":
        result = _run(args.repo, args.grant, args.ledger_root)
    else:
        result = _verify(args.ledger)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
