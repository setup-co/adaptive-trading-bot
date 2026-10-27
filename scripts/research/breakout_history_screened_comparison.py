"""Inert preflight and externally granted 180-day Breakout comparison route."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    ACCEPTED_BINDING,
    TASKS,
    HistoryScreenedComparisonError,
    HistoryScreenedConfiguration,
    HistoryScreenedExecutionGrant,
    LockedHistoryScreenedCandleStore,
    _canonical,
    preflight,
)
from adaptive_trading_bot.research.breakout_history_screened_execution import (
    HistoryScreenedRunAssociation,
    _write_once,
    execute_task,
    final_projection,
    verify_task,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import (
    HistoryScreenedLedgerStore,
)


def _clean_sha(repo: Path) -> str:
    status = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise HistoryScreenedComparisonError("execution checkout is not clean")
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@contextmanager
def _no_network() -> Iterator[None]:
    def rejected(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise HistoryScreenedComparisonError("network is disabled during locked execution")

    with (
        patch.object(socket.socket, "connect", rejected),
        patch.object(socket.socket, "connect_ex", rejected),
        patch.object(socket, "create_connection", rejected),
    ):
        yield


def run(repo: Path, grant_path: Path, ledger_root: Path) -> dict[str, object]:
    repo = repo.resolve(strict=True)
    grant_path = grant_path.resolve(strict=True)
    ledger_root = ledger_root.resolve()
    if repo in grant_path.parents or repo in ledger_root.parents:
        raise HistoryScreenedComparisonError("grant and ledger must remain outside repository")
    actual_sha = _clean_sha(repo)
    grant = HistoryScreenedExecutionGrant.from_file(grant_path)
    grant.validate(actual_sha=actual_sha)
    run_association = HistoryScreenedRunAssociation.from_grant(
        grant, ACCEPTED_BINDING, actual_sha=actual_sha
    )
    preflight(repo)
    store = LockedHistoryScreenedCandleStore(repo)
    ledger = HistoryScreenedLedgerStore.create(ledger_root, grant, actual_sha=actual_sha)
    with _no_network():
        for task in TASKS:
            ledger.start(task.index)
            try:
                artifacts = execute_task(
                    store,
                    HistoryScreenedConfiguration(task, ACCEPTED_BINDING),
                    grant=grant,
                    actual_sha=actual_sha,
                    directory=ledger.directory,
                    task_index=task.index,
                )
                ledger.complete(
                    task.index,
                    population_sha256=artifacts.population_sha256,
                    results_sha256=artifacts.results_sha256,
                    report_sha256=artifacts.report_sha256,
                )
            except Exception:
                ledger.fail(task.index)
                raise
        projection = final_projection(ledger.directory, run=run_association)
        projection_sha = _write_once(ledger.directory / "projection.json", _canonical(projection))
        ledger.finalize(projection_sha)
    return projection


def verify(ledger_directory: Path) -> dict[str, object]:
    ledger = HistoryScreenedLedgerStore(ledger_directory.resolve(strict=True))
    state = ledger.load()
    if (
        state.input_kind != "EXTERNAL_REVIEWED_EXECUTION_GRANT"
        or any(value != "COMPLETED" for value in state.task_states)
        or state.projection_sha256 is None
    ):
        raise HistoryScreenedComparisonError("ledger is not finalized reviewed authority")
    run_association = HistoryScreenedRunAssociation(
        state.input_kind,
        state.run_uuid,
        state.executable_sha,
        state.protocol_sha256,
        state.task_plan_sha256,
        state.source_id,
        state.dataset_version,
        state.manifest_sha256,
        state.input_report_sha256,
        state.input_plan_sha256,
    )
    run_association.validate(ACCEPTED_BINDING, actual_sha=state.executable_sha)
    for task in TASKS:
        facts = verify_task(
            ledger.directory,
            task.index,
            expected_task=task,
            run=run_association,
        )
        if (
            facts["task_id"] != task.task_id
            or facts["task_index"] != task.index
            or state.task_ids[task.index] != task.task_id
            or facts["population_sha256"] != state.population_sha256[task.index]
            or facts["results_sha256"] != state.results_sha256[task.index]
            or facts["report_sha256"] != state.report_sha256[task.index]
        ):
            raise HistoryScreenedComparisonError("ledger task digest changed")
    projection = final_projection(ledger.directory, run=run_association)
    raw = (ledger.directory / "projection.json").read_bytes()
    if _canonical(projection) != raw:
        raise HistoryScreenedComparisonError("final projection differs from authorities")
    return projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--grant", type=Path, required=True)
    run_parser.add_argument("--ledger-root", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = preflight(args.repo.resolve(strict=True))
    elif args.command == "run":
        result = run(args.repo, args.grant, args.ledger_root)
    else:
        result = verify(args.ledger)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
