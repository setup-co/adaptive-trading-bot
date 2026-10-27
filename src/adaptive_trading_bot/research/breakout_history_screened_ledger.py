"""Append-only one-shot ledger for the fixed 180-day Breakout comparison."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    DATASET_VERSION,
    MANIFEST_SHA256,
    PROTOCOL_SHA256,
    SOURCE_ID,
    TASK_PLAN_SHA256,
    TASKS,
    HistoryScreenedComparisonError,
    HistoryScreenedExecutionGrant,
    _canonical,
)

LEDGER_SCHEMA_VERSION = "history-screened-180d-ledger-v1"
_ARTIFACT_SUFFIXES = ("population.json.gz", "results.jsonl.gz", "report.json")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True, slots=True)
class HistoryScreenedLedgerState:
    run_uuid: str
    executable_sha: str
    input_kind: str
    revision: int
    task_states: tuple[str, ...]
    task_ids: tuple[str, ...]
    population_sha256: tuple[str | None, ...]
    results_sha256: tuple[str | None, ...]
    report_sha256: tuple[str | None, ...]
    projection_sha256: str | None


class HistoryScreenedLedgerStore:
    """Sequential durable transitions; terminal or STARTED tasks cannot rerun."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        grant: HistoryScreenedExecutionGrant,
        *,
        actual_sha: str,
        synthetic_task_ids: tuple[str, ...] = (),
    ) -> Self:
        synthetic = grant.kind == "SYNTHETIC_TEST_GRANT"
        if synthetic:
            _validate_synthetic_grant(grant, actual_sha=actual_sha)
        else:
            grant.validate(actual_sha=actual_sha)
        task_ids = synthetic_task_ids if synthetic else tuple(task.task_id for task in TASKS)
        if not task_ids:
            raise HistoryScreenedComparisonError("ledger has no frozen tasks")
        directory = root / grant.run_uuid
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise HistoryScreenedComparisonError("run UUID already has a ledger") from exc
        store = cls(directory)
        store._write(
            {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event": "CREATED",
                "revision": 0,
                "previous_sha256": None,
                "run_uuid": grant.run_uuid,
                "executable_sha": grant.executable_sha,
                "input_kind": grant.kind,
                "protocol_sha256": PROTOCOL_SHA256,
                "task_plan_sha256": TASK_PLAN_SHA256,
                "source_id": grant.source_id,
                "dataset_version": grant.dataset_version,
                "manifest_sha256": grant.manifest_sha256,
                "task_ids": list(task_ids),
            }
        )
        store.load()
        return store

    def _write(self, event: dict[str, object]) -> None:
        revision = event.get("revision")
        if type(revision) is not int or revision < 0:
            raise HistoryScreenedComparisonError("ledger revision is invalid")
        destination = self.directory / f"{revision:04d}.json"
        pending = self.directory / f".{revision:04d}.pending"
        if destination.exists() or pending.exists():
            raise HistoryScreenedComparisonError("ledger write is ambiguous")
        payload = _canonical(event)
        with pending.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, destination)
        if os.name != "nt":
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def load(self, *, _owned_lock: bool = False) -> HistoryScreenedLedgerState:
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
        if not files or any(path.name != f"{index:04d}.json" for index, path in enumerate(files)):
            raise HistoryScreenedComparisonError("ledger event sequence is incomplete")
        extras = set(self.directory.iterdir()) - set(files)
        if _owned_lock:
            extras.discard(self.directory / ".transition.lock")
        if any(
            path.name.endswith((".pending", ".lock"))
            or (path.name != "projection.json" and not path.name.endswith(_ARTIFACT_SUFFIXES))
            for path in extras
        ):
            raise HistoryScreenedComparisonError("ledger contains foreign or pending evidence")
        previous: str | None = None
        states: list[str] = []
        populations: list[str | None] = []
        results: list[str | None] = []
        reports: list[str | None] = []
        task_ids: tuple[str, ...] = ()
        run_uuid = executable_sha = input_kind = ""
        projection: str | None = None
        for revision, path in enumerate(files):
            raw = path.read_bytes()
            try:
                event = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise HistoryScreenedComparisonError("ledger event is malformed") from exc
            if (
                not isinstance(event, dict)
                or _canonical(event) != raw
                or event.get("schema_version") != LEDGER_SCHEMA_VERSION
                or event.get("revision") != revision
                or event.get("previous_sha256") != previous
            ):
                raise HistoryScreenedComparisonError("ledger hash chain is contradictory")
            if revision == 0:
                if (
                    event.get("event") != "CREATED"
                    or event.get("run_uuid") != self.directory.name
                    or event.get("protocol_sha256") != PROTOCOL_SHA256
                    or event.get("task_plan_sha256") != TASK_PLAN_SHA256
                ):
                    raise HistoryScreenedComparisonError("ledger creation identity changed")
                run_uuid = str(event["run_uuid"])
                executable_sha = str(event["executable_sha"])
                input_kind = str(event["input_kind"])
                task_ids = tuple(event.get("task_ids", ()))
                if input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT" and (
                    task_ids != tuple(task.task_id for task in TASKS)
                    or event.get("source_id") != SOURCE_ID.value
                    or event.get("dataset_version") != DATASET_VERSION
                    or event.get("manifest_sha256") != MANIFEST_SHA256
                ):
                    raise HistoryScreenedComparisonError("reviewed ledger inventory changed")
                if (
                    input_kind
                    not in {
                        "EXTERNAL_REVIEWED_EXECUTION_GRANT",
                        "SYNTHETIC_TEST_GRANT",
                    }
                    or not task_ids
                ):
                    raise HistoryScreenedComparisonError("ledger input kind is invalid")
                states = ["NOT_STARTED"] * len(task_ids)
                populations = [None] * len(task_ids)
                results = [None] * len(task_ids)
                reports = [None] * len(task_ids)
            elif event.get("event") == "FINALIZED":
                if projection is not None or any(state != "COMPLETED" for state in states):
                    raise HistoryScreenedComparisonError("incomplete ledger was finalized")
                projection = _digest(event.get("projection_sha256"))
                path = self.directory / "projection.json"
                if not path.is_file() or _file_sha(path) != projection:
                    raise HistoryScreenedComparisonError("final projection bytes changed")
            else:
                index = event.get("task_index")
                if projection is not None or type(index) is not int or not 0 <= index < len(states):
                    raise HistoryScreenedComparisonError("ledger task transition is invalid")
                name = event.get("event")
                if name == "STARTED":
                    if states[index] != "NOT_STARTED" or any(
                        state != "COMPLETED" for state in states[:index]
                    ):
                        raise HistoryScreenedComparisonError("task was retried or reordered")
                    states[index] = "STARTED"
                elif name == "FAILED":
                    if states[index] != "STARTED":
                        raise HistoryScreenedComparisonError("task failure lacks STARTED")
                    states[index] = "FAILED"
                elif name == "COMPLETED":
                    if states[index] != "STARTED":
                        raise HistoryScreenedComparisonError("task completion lacks STARTED")
                    digests = tuple(
                        _digest(event.get(key))
                        for key in ("population_sha256", "results_sha256", "report_sha256")
                    )
                    for suffix, digest in zip(_ARTIFACT_SUFFIXES, digests, strict=True):
                        artifact = self.directory / f"{index:02d}.{suffix}"
                        if not artifact.is_file() or _file_sha(artifact) != digest:
                            raise HistoryScreenedComparisonError("completed task artifact changed")
                    populations[index], results[index], reports[index] = digests
                    states[index] = "COMPLETED"
                else:
                    raise HistoryScreenedComparisonError("unknown ledger event")
            previous = _sha(raw)
        return HistoryScreenedLedgerState(
            run_uuid,
            executable_sha,
            input_kind,
            len(files) - 1,
            tuple(states),
            task_ids,
            tuple(populations),
            tuple(results),
            tuple(reports),
            projection,
        )

    def _append(self, event: str, *, task_index: int | None = None, **facts: object) -> None:
        lock = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise HistoryScreenedComparisonError("ledger transition is concurrent") from exc
        try:
            os.close(descriptor)
            state = self.load(_owned_lock=True)
            files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
            record: dict[str, object] = {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event": event,
                "revision": state.revision + 1,
                "previous_sha256": _sha(files[-1].read_bytes()),
                "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            if task_index is not None:
                record["task_index"] = task_index
            record.update(facts)
            self._write(record)
        finally:
            lock.unlink(missing_ok=True)
        self.load()

    def start(self, task_index: int) -> None:
        state = self.load()
        if (
            not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] != "NOT_STARTED"
            or any(value != "COMPLETED" for value in state.task_states[:task_index])
        ):
            raise HistoryScreenedComparisonError("task cannot start or rerun")
        self._append("STARTED", task_index=task_index)

    def complete(
        self,
        task_index: int,
        *,
        population_sha256: str,
        results_sha256: str,
        report_sha256: str,
    ) -> None:
        if self.load().task_states[task_index] != "STARTED":
            raise HistoryScreenedComparisonError("task is not STARTED")
        self._append(
            "COMPLETED",
            task_index=task_index,
            population_sha256=_digest(population_sha256),
            results_sha256=_digest(results_sha256),
            report_sha256=_digest(report_sha256),
        )

    def fail(self, task_index: int, reason_code: str = "TASK_FAILURE") -> None:
        if self.load().task_states[task_index] != "STARTED" or not reason_code:
            raise HistoryScreenedComparisonError("task failure lacks STARTED/reason")
        self._append("FAILED", task_index=task_index, reason_code=reason_code)

    def finalize(self, projection_sha256: str) -> None:
        state = self.load()
        if state.projection_sha256 is not None or any(
            value != "COMPLETED" for value in state.task_states
        ):
            raise HistoryScreenedComparisonError("ledger cannot finalize")
        self._append("FINALIZED", projection_sha256=_digest(projection_sha256))


def _digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise HistoryScreenedComparisonError("artifact digest is malformed")
    return value


def _validate_synthetic_grant(grant: HistoryScreenedExecutionGrant, *, actual_sha: str) -> None:
    if (
        grant.kind != "SYNTHETIC_TEST_GRANT"
        or grant.executable_sha != actual_sha
        or grant.protocol_sha256 != PROTOCOL_SHA256
        or grant.task_plan_sha256 != TASK_PLAN_SHA256
        or not grant.run_uuid
    ):
        raise HistoryScreenedComparisonError("synthetic ledger grant is invalid")
