"""Append-only one-shot ledger for BREAKOUT_RETEST_ZONE_COMPARISON_V1."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from adaptive_trading_bot.research.breakout_zone_comparison import (
    LEDGER_SCHEMA_VERSION,
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    TASKS,
    BreakoutZoneComparisonError,
    ComparisonExecutionGrant,
    _canonical,
    _iso,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True, slots=True)
class ComparisonLedgerState:
    run_uuid: str
    executable_sha: str
    input_kind: str
    task_ids: tuple[str, ...]
    revision: int
    task_states: tuple[str, ...]
    population_sha256: tuple[str | None, ...]
    results_sha256: tuple[str | None, ...]
    report_sha256: tuple[str | None, ...]
    finalized_sha256: str | None


class ComparisonLedgerStore:
    """Sequential fsync-backed evidence; STARTED/FAILED/COMPLETED never reruns."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        grant: ComparisonExecutionGrant,
        *,
        actual_sha: str,
        synthetic: bool = False,
        synthetic_task_ids: tuple[str, ...] = (),
    ) -> Self:
        grant.validate(actual_sha=actual_sha, synthetic=synthetic)
        task_ids = synthetic_task_ids if synthetic else tuple(task.task_id for task in TASKS)
        if not task_ids or (synthetic_task_ids and not synthetic):
            raise BreakoutZoneComparisonError("comparison ledger task inventory is invalid")
        directory = root / grant.run_uuid
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise BreakoutZoneComparisonError("comparison run UUID already exists") from exc
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
                "artifact_index_sha256": grant.artifact_index_sha256,
                "task_ids": task_ids,
            }
        )
        return store

    def _write(self, event: dict[str, object]) -> None:
        revision = event.get("revision")
        if type(revision) is not int or revision < 0:
            raise BreakoutZoneComparisonError("comparison ledger revision is invalid")
        destination = self.directory / f"{revision:04d}.json"
        pending = self.directory / f".{revision:04d}.pending"
        if destination.exists() or pending.exists():
            raise BreakoutZoneComparisonError("comparison ledger write is ambiguous")
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

    def load(self, *, _owned_lock: bool = False) -> ComparisonLedgerState:
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
        if not files or any(path.name != f"{index:04d}.json" for index, path in enumerate(files)):
            raise BreakoutZoneComparisonError("comparison ledger event sequence is incomplete")
        extras = set(self.directory.iterdir()) - set(files)
        if _owned_lock:
            extras.discard(self.directory / ".transition.lock")
        allowed = (".population.json.gz", ".results.jsonl.gz", ".report.json")
        if any(
            path.name.endswith((".pending", ".lock"))
            or (path.name != "projection.json" and not path.name.endswith(allowed))
            for path in extras
        ):
            raise BreakoutZoneComparisonError("comparison ledger contains foreign evidence")
        previous: str | None = None
        task_ids: tuple[str, ...] = ()
        states: list[str] = []
        populations: list[str | None] = []
        results: list[str | None] = []
        reports: list[str | None] = []
        run_uuid = executable = input_kind = ""
        final: str | None = None
        for index, path in enumerate(files):
            raw = path.read_bytes()
            try:
                event = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise BreakoutZoneComparisonError("comparison ledger event is malformed") from exc
            if (
                not isinstance(event, dict)
                or _canonical(event) != raw
                or event.get("schema_version") != LEDGER_SCHEMA_VERSION
                or event.get("revision") != index
                or event.get("previous_sha256") != previous
            ):
                raise BreakoutZoneComparisonError("comparison ledger hash chain is contradictory")
            if index == 0:
                if (
                    event.get("event") != "CREATED"
                    or event.get("protocol_sha256") != PROTOCOL_SHA256
                    or event.get("task_plan_sha256") != TASK_PLAN_SHA256
                    or event.get("run_uuid") != self.directory.name
                ):
                    raise BreakoutZoneComparisonError("comparison ledger creation differs")
                run_uuid = str(event["run_uuid"])
                executable = str(event["executable_sha"])
                input_kind = str(event["input_kind"])
                task_ids = tuple(event.get("task_ids", ()))
                if not task_ids:
                    raise BreakoutZoneComparisonError("comparison ledger has no tasks")
                if input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT" and task_ids != tuple(
                    task.task_id for task in TASKS
                ):
                    raise BreakoutZoneComparisonError(
                        "reviewed comparison ledger task inventory changed"
                    )
                states = ["NOT_STARTED"] * len(task_ids)
                populations = [None] * len(task_ids)
                results = [None] * len(task_ids)
                reports = [None] * len(task_ids)
            elif event.get("event") == "FINALIZED":
                if final is not None or any(state != "COMPLETED" for state in states):
                    raise BreakoutZoneComparisonError("incomplete comparison was finalized")
                final = str(event.get("projection_sha256"))
            else:
                task_index = event.get("task_index")
                if (
                    final is not None
                    or type(task_index) is not int
                    or not 0 <= task_index < len(states)
                ):
                    raise BreakoutZoneComparisonError(
                        "comparison ledger task transition is invalid"
                    )
                name = event.get("event")
                if name == "STARTED":
                    if states[task_index] != "NOT_STARTED" or any(
                        state != "COMPLETED" for state in states[:task_index]
                    ):
                        raise BreakoutZoneComparisonError(
                            "comparison task was retried or reordered"
                        )
                    states[task_index] = "STARTED"
                elif name == "FAILED":
                    if states[task_index] != "STARTED":
                        raise BreakoutZoneComparisonError("comparison failure lacks STARTED")
                    states[task_index] = "FAILED"
                elif name == "COMPLETED":
                    if states[task_index] != "STARTED":
                        raise BreakoutZoneComparisonError("comparison completion lacks STARTED")
                    digests = tuple(
                        str(event.get(key))
                        for key in ("population_sha256", "results_sha256", "report_sha256")
                    )
                    if any(
                        len(value) != 64
                        or any(character not in "0123456789abcdef" for character in value)
                        for value in digests
                    ):
                        raise BreakoutZoneComparisonError("comparison artifact digest is malformed")
                    for suffix, digest in zip(
                        ("population.json.gz", "results.jsonl.gz", "report.json"),
                        digests,
                        strict=True,
                    ):
                        artifact = self.directory / f"{task_index:02d}.{suffix}"
                        if not artifact.is_file() or _file_sha(artifact) != digest:
                            raise BreakoutZoneComparisonError(
                                "completed comparison artifact changed"
                            )
                    populations[task_index], results[task_index], reports[task_index] = digests
                    states[task_index] = "COMPLETED"
                else:
                    raise BreakoutZoneComparisonError("unknown comparison ledger event")
            previous = _sha(raw)
        return ComparisonLedgerState(
            run_uuid,
            executable,
            input_kind,
            task_ids,
            len(files) - 1,
            tuple(states),
            tuple(populations),
            tuple(results),
            tuple(reports),
            final,
        )

    def _append(self, event: str, *, task_index: int | None = None, **facts: object) -> None:
        lock = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise BreakoutZoneComparisonError(
                "comparison transition is concurrent/ambiguous"
            ) from exc
        try:
            os.close(descriptor)
            state = self.load(_owned_lock=True)
            files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
            event_record: dict[str, object] = {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event": event,
                "revision": state.revision + 1,
                "previous_sha256": _sha(files[-1].read_bytes()),
                "at": _iso(datetime.now(UTC)),
            }
            if task_index is not None:
                event_record["task_index"] = task_index
            event_record.update(facts)
            self._write(event_record)
        finally:
            lock.unlink()
        self.load()

    def start(self, task_index: int) -> None:
        state = self.load()
        if (
            not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] != "NOT_STARTED"
            or any(value != "COMPLETED" for value in state.task_states[:task_index])
        ):
            raise BreakoutZoneComparisonError("comparison task cannot start or rerun")
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
            raise BreakoutZoneComparisonError("comparison task is not STARTED")
        self._append(
            "COMPLETED",
            task_index=task_index,
            population_sha256=population_sha256,
            results_sha256=results_sha256,
            report_sha256=report_sha256,
        )

    def fail(self, task_index: int) -> None:
        if self.load().task_states[task_index] != "STARTED":
            raise BreakoutZoneComparisonError("comparison failure lacks STARTED")
        self._append("FAILED", task_index=task_index, reason_code="TASK_FAILURE")

    def finalize(self, projection_sha256: str) -> None:
        if len(projection_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in projection_sha256
        ):
            raise BreakoutZoneComparisonError("projection digest is malformed")
        if any(value != "COMPLETED" for value in self.load().task_states):
            raise BreakoutZoneComparisonError("partial comparison cannot finalize")
        self._append("FINALIZED", projection_sha256=projection_sha256)
