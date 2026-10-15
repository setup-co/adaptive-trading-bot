"""Append-only one-shot task provenance for a separately granted V2 study."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from adaptive_trading_bot.research.research_v2_admission import (
    DATASET_VERSION,
    MANIFEST_SHA256,
    SOURCE_ID,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    _HEX40,
    _HEX64,
    PROTOCOL_SHA256,
    SCHEMA_VERSION,
    TASK_PLAN_SHA256,
    ExploratoryScreenError,
    StudyExecutionGrant,
    _canonical,
    _iso,
    frozen_tasks,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True, slots=True)
class StudyLedgerState:
    run_uuid: str
    executable_sha: str
    input_kind: str
    task_ids: tuple[str, ...]
    revision: int
    task_states: tuple[str, ...]
    population_sha256: tuple[str | None, ...]
    partition_sha256: tuple[str | None, ...]
    authority_sha256: tuple[str | None, ...]
    finalized_sha256: str | None


class StudyLedgerStore:
    """One run UUID, sequential immutable fsync-backed hash-linked events."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        grant: StudyExecutionGrant,
        *,
        actual_sha: str,
        synthetic: bool = False,
        synthetic_single_task: bool = False,
    ) -> Self:
        grant.validate(actual_sha=actual_sha, synthetic=synthetic)
        if synthetic_single_task and not synthetic:
            raise ExploratoryScreenError("compact fixture ledger requires synthetic grant")
        directory = root / grant.run_uuid
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ExploratoryScreenError("study run UUID or ledger already exists") from exc
        store = cls(directory)
        store._write(
            {
                "schema_version": SCHEMA_VERSION,
                "event": "CREATED",
                "revision": 0,
                "previous_sha256": None,
                "run_uuid": grant.run_uuid,
                "executable_sha": grant.executable_sha,
                "protocol_sha256": PROTOCOL_SHA256,
                "task_plan_sha256": TASK_PLAN_SHA256,
                "input_kind": grant.kind,
                "source_id": grant.source_id,
                "dataset_version": grant.dataset_version,
                "manifest_sha256": grant.manifest_sha256,
                "task_ids": (
                    ("SYNTHETIC_ONE_TASK",)
                    if synthetic_single_task
                    else tuple(task.task_id for task in frozen_tasks())
                ),
            }
        )
        return store

    def _write(self, event: dict[str, object]) -> str:
        revision = event["revision"]
        if type(revision) is not int or revision < 0:
            raise ExploratoryScreenError("invalid ledger revision")
        destination = self.directory / f"{revision:04d}.json"
        pending = self.directory / f".{revision:04d}.pending"
        if destination.exists() or pending.exists():
            raise ExploratoryScreenError("ledger revision already exists or is ambiguous")
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
        return _sha(payload)

    def load(self) -> StudyLedgerState:
        if not self.directory.is_dir():
            raise ExploratoryScreenError("study ledger is absent")
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
        extras = set(self.directory.iterdir()) - set(files)
        # Completed result artifacts are permitted; orphan pending/lock files are not.
        if any(path.name.endswith(".pending") or path.name.endswith(".lock") for path in extras):
            raise ExploratoryScreenError("study ledger has an interrupted write")
        if any(
            not (
                path.name.endswith(".population.json.gz")
                or path.name.endswith(".partition.json")
                or path.name.endswith(".authority.jsonl.gz")
                or path.name == "projection.json"
            )
            for path in extras
        ):
            raise ExploratoryScreenError("study ledger has an unrecognized file")
        if not files or any(path.name != f"{index:04d}.json" for index, path in enumerate(files)):
            raise ExploratoryScreenError("study ledger event sequence is incomplete")
        states: list[str] = []
        population_hashes: list[str | None] = []
        partition_hashes: list[str | None] = []
        authority_hashes: list[str | None] = []
        previous: str | None = None
        run_uuid = ""
        executable_sha = ""
        input_kind = ""
        task_ids: tuple[str, ...] = ()
        final: str | None = None
        for index, path in enumerate(files):
            raw = path.read_bytes()
            try:
                event = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise ExploratoryScreenError("study ledger event is malformed") from exc
            if (
                not isinstance(event, dict)
                or _canonical(event) != raw
                or event.get("revision") != index
                or event.get("previous_sha256") != previous
                or event.get("schema_version") != SCHEMA_VERSION
            ):
                raise ExploratoryScreenError("study ledger hash chain is contradictory")
            if index == 0:
                if (
                    event.get("event") != "CREATED"
                    or event.get("protocol_sha256") != PROTOCOL_SHA256
                    or event.get("task_plan_sha256") != TASK_PLAN_SHA256
                    or (
                        event.get("input_kind") == "EXTERNAL_REVIEWED_EXECUTION_GRANT"
                        and (
                            event.get("source_id"),
                            event.get("dataset_version"),
                            event.get("manifest_sha256"),
                        )
                        != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
                    )
                    or (
                        event.get("input_kind") == "SYNTHETIC_TEST_GRANT"
                        and (
                            event.get("source_id") == SOURCE_ID.value
                            or event.get("manifest_sha256") == MANIFEST_SHA256
                        )
                    )
                    or event.get("input_kind")
                    not in {"EXTERNAL_REVIEWED_EXECUTION_GRANT", "SYNTHETIC_TEST_GRANT"}
                    or event.get("run_uuid") != self.directory.name
                ):
                    raise ExploratoryScreenError("study ledger creation differs from frozen plan")
                run_uuid = str(event["run_uuid"])
                executable_sha = str(event["executable_sha"])
                input_kind = str(event["input_kind"])
                task_ids = tuple(event.get("task_ids", ()))
                if task_ids not in (
                    tuple(task.task_id for task in frozen_tasks()),
                    ("SYNTHETIC_ONE_TASK",),
                ) or (task_ids == ("SYNTHETIC_ONE_TASK",) and input_kind != "SYNTHETIC_TEST_GRANT"):
                    raise ExploratoryScreenError("study ledger task inventory is invalid")
                states = ["NOT_STARTED"] * len(task_ids)
                population_hashes = [None] * len(task_ids)
                partition_hashes = [None] * len(task_ids)
                authority_hashes = [None] * len(task_ids)
                try:
                    if str(uuid.UUID(run_uuid)) != run_uuid:
                        raise ValueError("noncanonical UUID")
                except ValueError as exc:
                    raise ExploratoryScreenError("study run UUID is invalid") from exc
                if not _HEX40.fullmatch(executable_sha):
                    raise ExploratoryScreenError("frozen executable SHA is invalid")
            else:
                name = event.get("event")
                task_index = event.get("task_index")
                if final is not None:
                    raise ExploratoryScreenError("study ledger changed after finalization")
                if name == "FINALIZED":
                    if any(state != "COMPLETED" for state in states):
                        raise ExploratoryScreenError("incomplete study was finalized")
                    final = str(event.get("projection_sha256"))
                    if not _HEX64.fullmatch(final):
                        raise ExploratoryScreenError("final projection digest is malformed")
                elif type(task_index) is not int or not 0 <= task_index < len(task_ids):
                    raise ExploratoryScreenError("study ledger task index is invalid")
                elif name == "STARTED":
                    if states[task_index] != "NOT_STARTED" or any(
                        state != "COMPLETED" for state in states[:task_index]
                    ):
                        raise ExploratoryScreenError("study task was restarted or ran out of order")
                    states[task_index] = "STARTED"
                elif name == "COMPLETED":
                    if states[task_index] != "STARTED":
                        raise ExploratoryScreenError("study completion lacks one start")
                    population_hashes[task_index] = str(event.get("population_sha256"))
                    partition_hashes[task_index] = str(event.get("partition_sha256"))
                    authority_hashes[task_index] = str(event.get("authority_sha256"))
                    if not all(
                        digest is not None and _HEX64.fullmatch(digest)
                        for digest in (
                            population_hashes[task_index],
                            partition_hashes[task_index],
                            authority_hashes[task_index],
                        )
                    ):
                        raise ExploratoryScreenError("task artifact digest is malformed")
                    for suffix, digest in (
                        ("population.json.gz", population_hashes[task_index]),
                        ("partition.json", partition_hashes[task_index]),
                        ("authority.jsonl.gz", authority_hashes[task_index]),
                    ):
                        artifact = self.directory / f"{task_index:02d}.{suffix}"
                        if not artifact.is_file() or _file_sha(artifact) != digest:
                            raise ExploratoryScreenError(
                                "completed task artifact is missing or changed"
                            )
                    states[task_index] = "COMPLETED"
                elif name == "FAILED":
                    if states[task_index] != "STARTED":
                        raise ExploratoryScreenError("study failure lacks one start")
                    states[task_index] = "FAILED"
                else:
                    raise ExploratoryScreenError("unknown study ledger event")
            previous = _sha(raw)
        return StudyLedgerState(
            run_uuid,
            executable_sha,
            input_kind,
            task_ids,
            len(files) - 1,
            tuple(states),
            tuple(population_hashes),
            tuple(partition_hashes),
            tuple(authority_hashes),
            final,
        )

    def _append(self, event: str, *, task_index: int | None = None, **facts: object) -> None:
        lock = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ExploratoryScreenError("concurrent or interrupted study transition") from exc
        try:
            os.close(descriptor)
            # Read while holding our own lock; the external load path must fail on it.
            files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
            if not files:
                raise ExploratoryScreenError("study ledger disappeared during transition")
            prior_path = files[-1]
            prior = json.loads(prior_path.read_bytes())
            if prior.get("revision") != len(files) - 1:
                raise ExploratoryScreenError("study ledger changed during transition")
            payload: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "event": event,
                "revision": len(files),
                "previous_sha256": _sha(prior_path.read_bytes()),
                "at": _iso(datetime.now(UTC)),
            }
            if task_index is not None:
                payload["task_index"] = task_index
            payload.update(facts)
            self._write(payload)
        except Exception:
            # Ambiguity is evidence; never silently clear a failed transition.
            raise
        else:
            lock.unlink()
        self.load()

    def start(self, task_index: int) -> None:
        state = self.load()
        if (
            not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] != "NOT_STARTED"
            or any(value != "COMPLETED" for value in state.task_states[:task_index])
        ):
            raise ExploratoryScreenError("study task cannot be started or retried")
        self._append("STARTED", task_index=task_index)

    def complete(
        self,
        task_index: int,
        *,
        population_sha256: str,
        partition_sha256: str,
        authority_sha256: str,
    ) -> None:
        state = self.load()
        if state.task_states[task_index] != "STARTED":
            raise ExploratoryScreenError("study task is not exclusively started")
        self._append(
            "COMPLETED",
            task_index=task_index,
            population_sha256=population_sha256,
            partition_sha256=partition_sha256,
            authority_sha256=authority_sha256,
        )

    def fail(self, task_index: int, *, reason_code: str) -> None:
        state = self.load()
        if (
            not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] != "STARTED"
            or reason_code not in {"TASK_FAILURE", "SERIALIZATION_FAILURE"}
        ):
            raise ExploratoryScreenError("study failure cannot be recorded from this state")
        self._append("FAILED", task_index=task_index, reason_code=reason_code)

    def finalize(self, projection_sha256: str) -> None:
        if any(value != "COMPLETED" for value in self.load().task_states):
            raise ExploratoryScreenError("partial study cannot have an economic screen")
        self._append("FINALIZED", projection_sha256=projection_sha256)
