"""Append-only, durable execution provenance for a future scarcity diagnostic."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.research.range_mean_reversion_scarcity import PROTOCOL_VERSION

LEDGER_SCHEMA_VERSION = "range-scarcity-execution-ledger-v1"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


class ScarcityLedgerError(ValueError):
    """An execution ledger is invalid, interrupted, or already consumed."""


class UnitExecutionState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _utc(value: datetime, name: str) -> datetime:
    result = require_utc(value, field=name)
    offset = result.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ScarcityLedgerError(f"{name} must be UTC")
    return result


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_time(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScarcityLedgerError(f"{name} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ScarcityLedgerError(f"{name} must be an ISO UTC timestamp") from exc
    return _utc(parsed, name)


@dataclass(frozen=True, slots=True)
class UnitExecution:
    run_id: str
    state: UnitExecutionState = UnitExecutionState.NOT_STARTED
    process_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    corpus_digest: str | None = None
    report_digest: str | None = None

    def validate(self) -> None:
        if self.run_id not in {"A", "B", "C"} or not isinstance(self.state, UnitExecutionState):
            raise ScarcityLedgerError("unknown unit identity or state")
        if self.started_at is not None:
            _utc(self.started_at, "unit started_at")
        if self.ended_at is not None:
            _utc(self.ended_at, "unit ended_at")
        if self.state is UnitExecutionState.NOT_STARTED:
            if any(
                value is not None
                for value in (
                    self.process_session_id,
                    self.started_at,
                    self.ended_at,
                    self.corpus_digest,
                    self.report_digest,
                )
            ):
                raise ScarcityLedgerError("NOT_STARTED unit carries execution facts")
            return
        if not self.process_session_id or self.started_at is None:
            raise ScarcityLedgerError("started unit lacks session or start time")
        if self.state is UnitExecutionState.STARTED:
            if any(
                value is not None
                for value in (self.ended_at, self.corpus_digest, self.report_digest)
            ):
                raise ScarcityLedgerError("STARTED unit carries terminal facts")
            return
        if self.ended_at is None or self.ended_at < self.started_at:
            raise ScarcityLedgerError("terminal unit has invalid end time")
        if self.state is UnitExecutionState.COMPLETED:
            if not self.corpus_digest or not self.report_digest:
                raise ScarcityLedgerError("completed unit lacks authoritative digests")
            if not _HEX_SHA256.fullmatch(self.corpus_digest) or not _HEX_SHA256.fullmatch(
                self.report_digest
            ):
                raise ScarcityLedgerError("completed unit digest is malformed")
        elif self.corpus_digest is not None or self.report_digest is not None:
            raise ScarcityLedgerError("FAILED unit must not claim completed digests")


@dataclass(frozen=True, slots=True)
class ScarcityExecutionLedger:
    run_uuid: str
    frozen_code_sha: str
    dataset_version: str
    source_id: str
    manifest_sha256: str
    initial_process_session_id: str
    created_at: datetime
    units: tuple[UnitExecution, UnitExecution, UnitExecution]
    revision: int = 0
    previous_snapshot_sha256: str | None = None
    final_projection_digest: str | None = None
    schema_version: str = LEDGER_SCHEMA_VERSION
    protocol_version: str = PROTOCOL_VERSION
    integrity_seal: str = ""

    def __post_init__(self) -> None:
        self._validate_facts()
        expected = _sha256(self._body_json())
        if self.integrity_seal and self.integrity_seal != expected:
            raise ScarcityLedgerError("execution-ledger integrity seal mismatch")
        object.__setattr__(self, "integrity_seal", expected)

    def _validate_facts(self) -> None:
        try:
            parsed_uuid = uuid.UUID(self.run_uuid)
        except (ValueError, AttributeError) as exc:
            raise ScarcityLedgerError("stable run UUID is invalid") from exc
        if (
            str(parsed_uuid) != self.run_uuid
            or not _HEX_SHA1.fullmatch(self.frozen_code_sha)
            or not self.dataset_version
            or not self.source_id
            or not _HEX_SHA256.fullmatch(self.manifest_sha256)
            or not self.initial_process_session_id
            or self.schema_version != LEDGER_SCHEMA_VERSION
            or self.protocol_version != PROTOCOL_VERSION
            or type(self.revision) is not int
            or self.revision < 0
            or len(self.units) != 3
            or tuple(unit.run_id for unit in self.units) != ("A", "B", "C")
        ):
            raise ScarcityLedgerError("execution-ledger identity is invalid")
        _utc(self.created_at, "ledger created_at")
        if self.revision == 0 and self.previous_snapshot_sha256 is not None:
            raise ScarcityLedgerError("initial ledger has a predecessor")
        if self.revision > 0 and (
            self.previous_snapshot_sha256 is None
            or not _HEX_SHA256.fullmatch(self.previous_snapshot_sha256)
        ):
            raise ScarcityLedgerError("ledger predecessor hash is missing")
        for unit in self.units:
            unit.validate()
        states = tuple(unit.state for unit in self.units)
        if self.revision == 0 and (
            any(state is not UnitExecutionState.NOT_STARTED for state in states)
            or self.final_projection_digest is not None
        ):
            raise ScarcityLedgerError("initial ledger must predate every unit")
        if UnitExecutionState.FAILED in states and any(
            state is not UnitExecutionState.NOT_STARTED
            for state in states[states.index(UnitExecutionState.FAILED) + 1 :]
        ):
            raise ScarcityLedgerError("a later unit ran after failure")
        if any(
            states[index] is not UnitExecutionState.COMPLETED
            and states[index + 1] is not UnitExecutionState.NOT_STARTED
            for index in range(2)
        ):
            raise ScarcityLedgerError("units did not start in A/B/C order")
        if self.final_projection_digest is not None and (
            not _HEX_SHA256.fullmatch(self.final_projection_digest)
            or any(state is not UnitExecutionState.COMPLETED for state in states)
        ):
            raise ScarcityLedgerError("final projection lacks three completed units")

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "run_uuid": self.run_uuid,
            "frozen_code_sha": self.frozen_code_sha,
            "dataset_version": self.dataset_version,
            "source_id": self.source_id,
            "manifest_sha256": self.manifest_sha256,
            "initial_process_session_id": self.initial_process_session_id,
            "created_at": self.created_at.isoformat(),
            "revision": self.revision,
            "previous_snapshot_sha256": self.previous_snapshot_sha256,
            "final_projection_digest": self.final_projection_digest,
            "units": [
                {
                    "run_id": unit.run_id,
                    "state": unit.state.value,
                    "process_session_id": unit.process_session_id,
                    "started_at": unit.started_at.isoformat() if unit.started_at else None,
                    "ended_at": unit.ended_at.isoformat() if unit.ended_at else None,
                    "corpus_digest": unit.corpus_digest,
                    "report_digest": unit.report_digest,
                }
                for unit in self.units
            ],
        }

    def _body_json(self) -> str:
        return json.dumps(self._body(), sort_keys=True, separators=(",", ":"))

    def to_json(self) -> str:
        self.validate()
        return (
            json.dumps(
                {**self._body(), "integrity_seal": self.integrity_seal},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    def validate(self) -> None:
        self._validate_facts()
        if self.integrity_seal != _sha256(self._body_json()):
            raise ScarcityLedgerError("execution-ledger integrity seal mismatch")

    @classmethod
    def from_json(cls, raw: str) -> Self:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {
                "schema_version",
                "protocol_version",
                "run_uuid",
                "frozen_code_sha",
                "dataset_version",
                "source_id",
                "manifest_sha256",
                "initial_process_session_id",
                "created_at",
                "revision",
                "previous_snapshot_sha256",
                "final_projection_digest",
                "units",
                "integrity_seal",
            }:
                raise ScarcityLedgerError("unknown ledger schema fields")
            units_data = value["units"]
            if not isinstance(units_data, list) or len(units_data) != 3:
                raise ScarcityLedgerError("ledger units are malformed")
            units: list[UnitExecution] = []
            for item in units_data:
                if not isinstance(item, dict) or set(item) != {
                    "run_id",
                    "state",
                    "process_session_id",
                    "started_at",
                    "ended_at",
                    "corpus_digest",
                    "report_digest",
                }:
                    raise ScarcityLedgerError("unit schema fields are malformed")
                units.append(
                    UnitExecution(
                        item["run_id"],
                        UnitExecutionState(item["state"]),
                        item["process_session_id"],
                        _parse_time(item["started_at"], "unit started_at"),
                        _parse_time(item["ended_at"], "unit ended_at"),
                        item["corpus_digest"],
                        item["report_digest"],
                    )
                )
            created = _parse_time(value["created_at"], "ledger created_at")
            if created is None:
                raise ScarcityLedgerError("ledger created_at is missing")
            result = cls(
                value["run_uuid"],
                value["frozen_code_sha"],
                value["dataset_version"],
                value["source_id"],
                value["manifest_sha256"],
                value["initial_process_session_id"],
                created,
                (units[0], units[1], units[2]),
                value["revision"],
                value["previous_snapshot_sha256"],
                value["final_projection_digest"],
                value["schema_version"],
                value["protocol_version"],
                value["integrity_seal"],
            )
            result.validate()
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise ScarcityLedgerError("execution-ledger JSON is invalid") from exc


class ScarcityLedgerStore:
    """One UUID directory of append-only, hash-linked, atomic JSON snapshots."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        frozen_code_sha: str,
        dataset_version: str,
        source_id: str,
        manifest_sha256: str,
        process_session_id: str,
        created_at: datetime,
        run_uuid: str | None = None,
    ) -> Self:
        stable_uuid = run_uuid or str(uuid.uuid4())
        ledger = ScarcityExecutionLedger(
            stable_uuid,
            frozen_code_sha,
            dataset_version,
            source_id,
            manifest_sha256,
            process_session_id,
            created_at,
            (UnitExecution("A"), UnitExecution("B"), UnitExecution("C")),
        )
        directory = root / stable_uuid
        directory.mkdir(parents=True, exist_ok=False)
        store = cls(directory)
        store._write_snapshot(ledger)
        return store

    def _snapshots(self) -> tuple[Path, ...]:
        if not self.directory.is_dir() or (self.directory / ".transition.lock").exists():
            raise ScarcityLedgerError("ledger is missing or has an interrupted transition")
        names = sorted(self.directory.iterdir())
        if not names or any(path.name != f"{index:04d}.json" for index, path in enumerate(names)):
            raise ScarcityLedgerError("ledger snapshot sequence is missing or ambiguous")
        return tuple(names)

    def load(self) -> ScarcityExecutionLedger:
        snapshots = self._snapshots()
        previous_sha: str | None = None
        previous: ScarcityExecutionLedger | None = None
        for revision, path in enumerate(snapshots):
            raw = path.read_bytes()
            try:
                current = ScarcityExecutionLedger.from_json(raw.decode("utf-8"))
            except UnicodeError as exc:
                raise ScarcityLedgerError("ledger snapshot encoding is invalid") from exc
            if (
                current.revision != revision
                or current.previous_snapshot_sha256 != previous_sha
                or current.run_uuid != self.directory.name
            ):
                raise ScarcityLedgerError("ledger snapshot chain differs")
            if previous is not None:
                _validate_transition(previous, current)
            previous_sha = hashlib.sha256(raw).hexdigest()
            previous = current
        assert previous is not None
        return previous

    def _write_snapshot(self, ledger: ScarcityExecutionLedger) -> None:
        payload = ledger.to_json().encode("utf-8")
        destination = self.directory / f"{ledger.revision:04d}.json"
        temporary = self.directory / f".{ledger.revision:04d}.pending"
        if destination.exists() or temporary.exists():
            raise ScarcityLedgerError("snapshot revision already exists or is ambiguous")
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        # Directory fsync is available on POSIX; Windows still has a flushed file
        # and same-directory atomic replacement. An orphan pending file fails closed.
        if os.name != "nt":
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _append(self, prior: ScarcityExecutionLedger, updated: ScarcityExecutionLedger) -> None:
        lock_path = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ScarcityLedgerError("concurrent or interrupted ledger transition") from exc
        try:
            os.close(descriptor)
            # Inspect snapshots directly while our own exclusive lock is held.
            snapshots = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
            if len(snapshots) != prior.revision + 1:
                raise ScarcityLedgerError("ledger changed during transition")
            prior_raw = snapshots[-1].read_bytes()
            if ScarcityExecutionLedger.from_json(prior_raw.decode("utf-8")) != prior:
                raise ScarcityLedgerError("ledger changed during transition")
            expected_previous = hashlib.sha256(prior_raw).hexdigest()
            if updated.previous_snapshot_sha256 != expected_previous:
                raise ScarcityLedgerError("new ledger predecessor hash differs")
            _validate_transition(prior, updated)
            self._write_snapshot(updated)
        finally:
            lock_path.unlink(missing_ok=True)

    def _transition(
        self,
        *,
        units: tuple[UnitExecution, UnitExecution, UnitExecution] | None = None,
        final_projection_digest: str | None = None,
    ) -> ScarcityExecutionLedger:
        prior = self.load()
        prior_path = self.directory / f"{prior.revision:04d}.json"
        updated = replace(
            prior,
            revision=prior.revision + 1,
            previous_snapshot_sha256=hashlib.sha256(prior_path.read_bytes()).hexdigest(),
            integrity_seal="",
            units=units if units is not None else prior.units,
            final_projection_digest=(
                final_projection_digest
                if final_projection_digest is not None
                else prior.final_projection_digest
            ),
        )
        self._append(prior, updated)
        return updated

    def start_unit(
        self, run_id: str, *, current_code_sha: str, process_session_id: str, at: datetime
    ) -> ScarcityExecutionLedger:
        current = self.load()
        if current_code_sha != current.frozen_code_sha:
            raise ScarcityLedgerError("running code SHA differs from frozen ledger")
        index = _run_index(run_id)
        if current.units[index].state is not UnitExecutionState.NOT_STARTED:
            raise ScarcityLedgerError("unit already started or terminated; no rerun")
        if any(unit.state is not UnitExecutionState.COMPLETED for unit in current.units[:index]):
            raise ScarcityLedgerError("prior unit did not complete")
        if current.final_projection_digest is not None:
            raise ScarcityLedgerError("finalized diagnostic cannot start a unit")
        units = list(current.units)
        units[index] = replace(
            units[index],
            state=UnitExecutionState.STARTED,
            process_session_id=process_session_id,
            started_at=_utc(at, "unit started_at"),
        )
        return self._transition(units=(units[0], units[1], units[2]))

    def complete_unit(
        self, run_id: str, *, at: datetime, corpus_digest: str, report_digest: str
    ) -> ScarcityExecutionLedger:
        current = self.load()
        index = _run_index(run_id)
        if current.units[index].state is not UnitExecutionState.STARTED:
            raise ScarcityLedgerError("unit was not exclusively STARTED")
        units = list(current.units)
        units[index] = replace(
            units[index],
            state=UnitExecutionState.COMPLETED,
            ended_at=_utc(at, "unit completed_at"),
            corpus_digest=corpus_digest,
            report_digest=report_digest,
        )
        return self._transition(units=(units[0], units[1], units[2]))

    def fail_unit(self, run_id: str, *, at: datetime) -> ScarcityExecutionLedger:
        current = self.load()
        index = _run_index(run_id)
        if current.units[index].state is not UnitExecutionState.STARTED:
            raise ScarcityLedgerError("unit was not exclusively STARTED")
        units = list(current.units)
        units[index] = replace(
            units[index],
            state=UnitExecutionState.FAILED,
            ended_at=_utc(at, "unit failed_at"),
        )
        return self._transition(units=(units[0], units[1], units[2]))

    def finalize(self, projection_digest: str) -> ScarcityExecutionLedger:
        current = self.load()
        if current.final_projection_digest is not None or any(
            unit.state is not UnitExecutionState.COMPLETED for unit in current.units
        ):
            raise ScarcityLedgerError("diagnostic is not ready for one final projection")
        return self._transition(final_projection_digest=projection_digest)


def _run_index(run_id: str) -> int:
    if run_id not in {"A", "B", "C"}:
        raise ScarcityLedgerError("unknown diagnostic unit")
    return ("A", "B", "C").index(run_id)


def _validate_transition(
    previous: ScarcityExecutionLedger, current: ScarcityExecutionLedger
) -> None:
    if current.revision != previous.revision + 1 or any(
        getattr(current, name) != getattr(previous, name)
        for name in (
            "run_uuid",
            "frozen_code_sha",
            "dataset_version",
            "source_id",
            "manifest_sha256",
            "initial_process_session_id",
            "created_at",
            "schema_version",
            "protocol_version",
        )
    ):
        raise ScarcityLedgerError("ledger identity changed across a transition")
    changed = tuple(
        index
        for index, (old, new) in enumerate(zip(previous.units, current.units, strict=True))
        if old != new
    )
    if changed:
        if len(changed) != 1 or current.final_projection_digest != previous.final_projection_digest:
            raise ScarcityLedgerError("more than one unit changed in a transition")
        old = previous.units[changed[0]]
        new = current.units[changed[0]]
        if (old.state, new.state) not in {
            (UnitExecutionState.NOT_STARTED, UnitExecutionState.STARTED),
            (UnitExecutionState.STARTED, UnitExecutionState.COMPLETED),
            (UnitExecutionState.STARTED, UnitExecutionState.FAILED),
        }:
            raise ScarcityLedgerError("unit transition is not monotonic")
        if old.state is UnitExecutionState.STARTED and (
            new.started_at != old.started_at or new.process_session_id != old.process_session_id
        ):
            raise ScarcityLedgerError("unit start provenance changed")
    elif previous.final_projection_digest is not None or current.final_projection_digest is None:
        raise ScarcityLedgerError("empty or repeated ledger transition")
