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
from pathlib import Path
from unittest.mock import patch

from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    ExploratoryScreenError,
    StudyExecutionGrant,
    _canonical,
    data_only_plan,
    execute_granted_task,
    frozen_tasks,
    project_frozen_screen,
    project_task_partition,
    verify_task_partition,
)
from adaptive_trading_bot.research.research_v2_screen_ledger import StudyLedgerStore
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _INTEGRITY_SEAL_VERSION,
    _canonical_integrity_value,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
            population, base, stress = execute_granted_task(
                store, task, grant, actual_code_sha=actual_sha
            )
            partition = project_task_partition(task, population, base, stress)
            canonical_population = _canonical_integrity_value(
                population, excluded_names=frozenset({"integrity_seal"})
            )
            population_bytes = gzip.compress(
                _canonical(
                    {
                        "schema_version": population.schema_version,
                        "population_integrity_seal": population.integrity_seal,
                        "canonical_population": canonical_population,
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
            )
        projection = _verify_complete(ledger)
        projection_hash = _immutable_write(
            ledger.directory / "projection.json", _canonical(projection)
        )
        ledger.finalize(projection_hash)
        return projection


def _verify_complete(ledger: StudyLedgerStore) -> dict[str, object]:
    state = ledger.load()
    if any(value != "COMPLETED" for value in state.task_states):
        raise ExploratoryScreenError("partial task ledger has no final economic screen")
    task_rows = []
    for task in frozen_tasks():
        partition_bytes = (ledger.directory / f"{task.index:02d}.partition.json").read_bytes()
        population_bytes = (ledger.directory / f"{task.index:02d}.population.json.gz").read_bytes()
        try:
            population = json.loads(gzip.decompress(population_bytes))
            if population["schema_version"] != "research-v2-study-decision-population-v1":
                raise ExploratoryScreenError("stored population has an unknown study schema")
            population_seal = population["population_integrity_seal"]
            original_seal = _sha(
                _canonical(
                    (
                        "adaptive-trading-bot/research-v2-decision-population",
                        _INTEGRITY_SEAL_VERSION,
                        population["canonical_population"],
                    )
                )
            )
            if population_seal != original_seal:
                raise ExploratoryScreenError("stored population differs from its creation seal")
        except (OSError, KeyError, ValueError) as exc:
            raise ExploratoryScreenError("stored task population is unavailable") from exc
        base, stress = verify_task_partition(
            partition_bytes, task, expected_population_sha256=population_seal
        )
        task_rows.append((task, base, stress))
    return project_frozen_screen(tuple(task_rows))


def _verify(ledger_dir: Path) -> dict[str, object]:
    ledger = StudyLedgerStore(ledger_dir)
    state = ledger.load()
    result = _verify_complete(ledger)
    projection_bytes = (ledger_dir / "projection.json").read_bytes()
    if state.finalized_sha256 != _sha(projection_bytes) or _canonical(result) != projection_bytes:
        raise ExploratoryScreenError("final projection differs from completed immutable tasks")
    return result


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
