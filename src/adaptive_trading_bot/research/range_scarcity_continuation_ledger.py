"""Append-only B/C-only execution ledger for the scarcity continuation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Self

from adaptive_trading_bot.research.range_mean_reversion_scarcity import (
    DATASET_VERSION,
    MANIFEST_SHA256,
    SOURCE_ID,
)
from adaptive_trading_bot.research.range_scarcity_continuation import (
    BTC_A_ANCHOR,
    CONTINUATION_PROTOCOL_VERSION,
    FrozenBtcAAnchor,
    verify_locked_a_evidence,
)
from adaptive_trading_bot.research.range_scarcity_ledger import (
    ScarcityLedgerError,
    UnitExecutionState,
    _parse_time,
    _utc,
)

CONTINUATION_LEDGER_SCHEMA_VERSION = "range-scarcity-continuation-ledger-v1"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_SHA1 = re.compile(r"[0-9a-f]{40}\Z")


class ContinuationLedgerError(ScarcityLedgerError):
    """A B/C-only ledger is invalid, interrupted, or already consumed."""


@dataclass(frozen=True, slots=True)
class ContinuationUnitExecution:
    run_id: str
    state: UnitExecutionState = UnitExecutionState.NOT_STARTED
    process_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    corpus_digest: str | None = None
    report_digest: str | None = None
    compact_record_digest: str | None = None

    def validate(self) -> None:
        if self.run_id not in {"B", "C"} or not isinstance(self.state, UnitExecutionState):
            raise ContinuationLedgerError("continuation unit identity or state is invalid")
        if self.started_at is not None:
            _utc(self.started_at, "unit started_at")
        if self.ended_at is not None:
            _utc(self.ended_at, "unit ended_at")
        digests = (self.corpus_digest, self.report_digest, self.compact_record_digest)
        if self.state is UnitExecutionState.NOT_STARTED:
            if any(
                value is not None
                for value in (
                    self.process_session_id,
                    self.started_at,
                    self.ended_at,
                    *digests,
                )
            ):
                raise ContinuationLedgerError("NOT_STARTED unit carries execution facts")
            return
        if not self.process_session_id or self.started_at is None:
            raise ContinuationLedgerError("started unit lacks process/session or start time")
        if self.state is UnitExecutionState.STARTED:
            if self.ended_at is not None or any(value is not None for value in digests):
                raise ContinuationLedgerError("STARTED unit carries terminal facts")
            return
        if self.ended_at is None or self.ended_at < self.started_at:
            raise ContinuationLedgerError("terminal unit has invalid end time")
        if self.state is UnitExecutionState.COMPLETED:
            if any(value is None or not _HEX_SHA256.fullmatch(value) for value in digests):
                raise ContinuationLedgerError("completed unit lacks exact digests")
        elif any(value is not None for value in digests):
            raise ContinuationLedgerError("FAILED unit cannot claim completed digests")


@dataclass(frozen=True, slots=True)
class ContinuationExecutionLedger:
    run_uuid: str
    frozen_code_sha: str
    dataset_version: str
    source_id: str
    manifest_sha256: str
    anchor_integrity_seal: str
    anchor_report_digest: str
    anchor_ledger_snapshot_sha256: str
    anchor_manifest_sha256: str
    initial_process_session_id: str
    created_at: datetime
    units: tuple[ContinuationUnitExecution, ContinuationUnitExecution]
    revision: int = 0
    previous_snapshot_sha256: str | None = None
    final_projection_digest: str | None = None
    schema_version: str = CONTINUATION_LEDGER_SCHEMA_VERSION
    protocol_version: str = CONTINUATION_PROTOCOL_VERSION
    integrity_seal: str = ""

    def __post_init__(self) -> None:
        self._validate_facts()
        expected = hashlib.sha256(self._body_json().encode("utf-8")).hexdigest()
        if self.integrity_seal and self.integrity_seal != expected:
            raise ContinuationLedgerError("continuation ledger seal mismatch")
        object.__setattr__(self, "integrity_seal", expected)

    def _validate_facts(self) -> None:
        BTC_A_ANCHOR.validate()
        try:
            parsed_uuid = uuid.UUID(self.run_uuid)
        except (ValueError, AttributeError) as exc:
            raise ContinuationLedgerError("run UUID is invalid") from exc
        if (
            str(parsed_uuid) != self.run_uuid
            or not _HEX_SHA1.fullmatch(self.frozen_code_sha)
            or self.dataset_version != DATASET_VERSION
            or self.source_id != SOURCE_ID.value
            or self.manifest_sha256 != MANIFEST_SHA256
            or self.anchor_integrity_seal != BTC_A_ANCHOR.integrity_seal
            or self.anchor_report_digest != BTC_A_ANCHOR.report_digest
            or self.anchor_ledger_snapshot_sha256 != BTC_A_ANCHOR.issue_157_ledger_snapshot_sha256
            or self.anchor_manifest_sha256 != BTC_A_ANCHOR.issue_157_manifest_sha256
            or not self.initial_process_session_id
            or self.schema_version != CONTINUATION_LEDGER_SCHEMA_VERSION
            or self.protocol_version != CONTINUATION_PROTOCOL_VERSION
            or type(self.revision) is not int
            or self.revision < 0
            or len(self.units) != 2
            or tuple(unit.run_id for unit in self.units) != ("B", "C")
        ):
            raise ContinuationLedgerError("B/C ledger identity differs")
        _utc(self.created_at, "ledger created_at")
        if self.revision == 0 and self.previous_snapshot_sha256 is not None:
            raise ContinuationLedgerError("initial ledger has a predecessor")
        if self.revision > 0 and (
            self.previous_snapshot_sha256 is None
            or not _HEX_SHA256.fullmatch(self.previous_snapshot_sha256)
        ):
            raise ContinuationLedgerError("ledger predecessor hash is missing")
        for unit in self.units:
            unit.validate()
        states = tuple(unit.state for unit in self.units)
        if self.revision == 0 and (
            states != (UnitExecutionState.NOT_STARTED, UnitExecutionState.NOT_STARTED)
            or self.final_projection_digest is not None
        ):
            raise ContinuationLedgerError("revision 0 must predate B and C")
        if (
            states[0] is not UnitExecutionState.COMPLETED
            and states[1] is not UnitExecutionState.NOT_STARTED
        ):
            raise ContinuationLedgerError("C cannot start before B completes")
        if self.final_projection_digest is not None and (
            not _HEX_SHA256.fullmatch(self.final_projection_digest)
            or states != (UnitExecutionState.COMPLETED, UnitExecutionState.COMPLETED)
        ):
            raise ContinuationLedgerError("final projection lacks B and C completion")

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "run_uuid": self.run_uuid,
            "frozen_code_sha": self.frozen_code_sha,
            "dataset_version": self.dataset_version,
            "source_id": self.source_id,
            "manifest_sha256": self.manifest_sha256,
            "anchor_integrity_seal": self.anchor_integrity_seal,
            "anchor_report_digest": self.anchor_report_digest,
            "anchor_ledger_snapshot_sha256": self.anchor_ledger_snapshot_sha256,
            "anchor_manifest_sha256": self.anchor_manifest_sha256,
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
                    "compact_record_digest": unit.compact_record_digest,
                }
                for unit in self.units
            ],
        }

    def _body_json(self) -> str:
        return json.dumps(self._body(), sort_keys=True, separators=(",", ":"))

    def validate(self) -> None:
        self._validate_facts()
        if self.integrity_seal != hashlib.sha256(self._body_json().encode("utf-8")).hexdigest():
            raise ContinuationLedgerError("continuation ledger seal mismatch")

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
                "anchor_integrity_seal",
                "anchor_report_digest",
                "anchor_ledger_snapshot_sha256",
                "anchor_manifest_sha256",
                "initial_process_session_id",
                "created_at",
                "revision",
                "previous_snapshot_sha256",
                "final_projection_digest",
                "units",
                "integrity_seal",
            }:
                raise ContinuationLedgerError("unknown ledger JSON fields")
            units_data = value["units"]
            if not isinstance(units_data, list) or len(units_data) != 2:
                raise ContinuationLedgerError("B/C ledger units are malformed")
            units: list[ContinuationUnitExecution] = []
            for item in units_data:
                if not isinstance(item, dict) or set(item) != {
                    "run_id",
                    "state",
                    "process_session_id",
                    "started_at",
                    "ended_at",
                    "corpus_digest",
                    "report_digest",
                    "compact_record_digest",
                }:
                    raise ContinuationLedgerError("unit JSON fields are malformed")
                units.append(
                    ContinuationUnitExecution(
                        item["run_id"],
                        UnitExecutionState(item["state"]),
                        item["process_session_id"],
                        _parse_time(item["started_at"], "unit started_at"),
                        _parse_time(item["ended_at"], "unit ended_at"),
                        item["corpus_digest"],
                        item["report_digest"],
                        item["compact_record_digest"],
                    )
                )
            created = _parse_time(value["created_at"], "ledger created_at")
            if created is None:
                raise ContinuationLedgerError("ledger created_at is missing")
            result = cls(
                value["run_uuid"],
                value["frozen_code_sha"],
                value["dataset_version"],
                value["source_id"],
                value["manifest_sha256"],
                value["anchor_integrity_seal"],
                value["anchor_report_digest"],
                value["anchor_ledger_snapshot_sha256"],
                value["anchor_manifest_sha256"],
                value["initial_process_session_id"],
                created,
                (units[0], units[1]),
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
            raise ContinuationLedgerError("continuation ledger JSON is invalid") from exc


class ContinuationLedgerStore:
    """One fresh B/C UUID directory of hash-linked, fsync-backed snapshots."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        frozen_code_sha: str,
        process_session_id: str,
        created_at: datetime,
        anchor: FrozenBtcAAnchor = BTC_A_ANCHOR,
        run_uuid: str | None = None,
    ) -> Self:
        anchor.validate()
        verify_locked_a_evidence(anchor)
        stable_uuid = run_uuid or str(uuid.uuid4())
        ledger = ContinuationExecutionLedger(
            stable_uuid,
            frozen_code_sha,
            DATASET_VERSION,
            SOURCE_ID.value,
            MANIFEST_SHA256,
            anchor.integrity_seal,
            anchor.report_digest,
            anchor.issue_157_ledger_snapshot_sha256,
            anchor.issue_157_manifest_sha256,
            process_session_id,
            created_at,
            (ContinuationUnitExecution("B"), ContinuationUnitExecution("C")),
        )
        directory = root / stable_uuid
        directory.mkdir(parents=True, exist_ok=False)
        store = cls(directory)
        store._write_snapshot(ledger)
        return store

    def _snapshots(self) -> tuple[Path, ...]:
        if not self.directory.is_dir() or (self.directory / ".transition.lock").exists():
            raise ContinuationLedgerError("ledger is missing or transition interrupted")
        paths = sorted(self.directory.iterdir())
        if not paths or any(path.name != f"{index:04d}.json" for index, path in enumerate(paths)):
            raise ContinuationLedgerError("ledger snapshot sequence is missing or ambiguous")
        return tuple(paths)

    def load(self) -> ContinuationExecutionLedger:
        paths = self._snapshots()
        previous_sha: str | None = None
        previous: ContinuationExecutionLedger | None = None
        for revision, path in enumerate(paths):
            raw = path.read_bytes()
            try:
                current = ContinuationExecutionLedger.from_json(raw.decode("utf-8"))
            except UnicodeError as exc:
                raise ContinuationLedgerError("ledger encoding is invalid") from exc
            if (
                current.revision != revision
                or current.previous_snapshot_sha256 != previous_sha
                or current.run_uuid != self.directory.name
            ):
                raise ContinuationLedgerError("ledger chain identity differs")
            if previous is not None:
                _validate_transition(previous, current)
            previous_sha = hashlib.sha256(raw).hexdigest()
            previous = current
        assert previous is not None
        return previous

    def _write_snapshot(self, ledger: ContinuationExecutionLedger) -> None:
        payload = ledger.to_json().encode("utf-8")
        destination = self.directory / f"{ledger.revision:04d}.json"
        pending = self.directory / f".{ledger.revision:04d}.pending"
        if destination.exists() or pending.exists():
            raise ContinuationLedgerError("snapshot revision exists or is ambiguous")
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

    def _append(
        self, prior: ContinuationExecutionLedger, updated: ContinuationExecutionLedger
    ) -> None:
        lock_path = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ContinuationLedgerError("concurrent or interrupted transition") from exc
        try:
            os.close(descriptor)
            paths = sorted(self.directory.glob("[0-9][0-9][0-9][0-9].json"))
            if len(paths) != prior.revision + 1:
                raise ContinuationLedgerError("ledger changed during transition")
            raw = paths[-1].read_bytes()
            if ContinuationExecutionLedger.from_json(raw.decode("utf-8")) != prior:
                raise ContinuationLedgerError("ledger changed during transition")
            if updated.previous_snapshot_sha256 != hashlib.sha256(raw).hexdigest():
                raise ContinuationLedgerError("predecessor SHA differs")
            _validate_transition(prior, updated)
            self._write_snapshot(updated)
        finally:
            lock_path.unlink(missing_ok=True)

    def _transition(
        self,
        *,
        units: tuple[ContinuationUnitExecution, ContinuationUnitExecution] | None = None,
        final_projection_digest: str | None = None,
    ) -> ContinuationExecutionLedger:
        prior = self.load()
        raw = (self.directory / f"{prior.revision:04d}.json").read_bytes()
        updated = replace(
            prior,
            revision=prior.revision + 1,
            previous_snapshot_sha256=hashlib.sha256(raw).hexdigest(),
            units=units if units is not None else prior.units,
            final_projection_digest=(
                final_projection_digest
                if final_projection_digest is not None
                else prior.final_projection_digest
            ),
            integrity_seal="",
        )
        self._append(prior, updated)
        return updated

    def start_unit(
        self, run_id: str, *, current_code_sha: str, process_session_id: str, at: datetime
    ) -> ContinuationExecutionLedger:
        current = self.load()
        index = _run_index(run_id)
        if current_code_sha != current.frozen_code_sha:
            raise ContinuationLedgerError("running code SHA differs from frozen ledger")
        if current.units[index].state is not UnitExecutionState.NOT_STARTED:
            raise ContinuationLedgerError("unit already started or terminated; no rerun")
        if index == 1 and current.units[0].state is not UnitExecutionState.COMPLETED:
            raise ContinuationLedgerError("B must complete before C starts")
        if current.final_projection_digest is not None:
            raise ContinuationLedgerError("finalized continuation cannot start a unit")
        units = list(current.units)
        units[index] = replace(
            units[index],
            state=UnitExecutionState.STARTED,
            process_session_id=process_session_id,
            started_at=_utc(at, "unit started_at"),
        )
        return self._transition(units=(units[0], units[1]))

    def complete_unit(
        self,
        run_id: str,
        *,
        at: datetime,
        corpus_digest: str,
        report_digest: str,
        compact_record_digest: str,
    ) -> ContinuationExecutionLedger:
        current = self.load()
        index = _run_index(run_id)
        if current.units[index].state is not UnitExecutionState.STARTED:
            raise ContinuationLedgerError("unit was not exclusively STARTED")
        units = list(current.units)
        units[index] = replace(
            units[index],
            state=UnitExecutionState.COMPLETED,
            ended_at=_utc(at, "unit completed_at"),
            corpus_digest=corpus_digest,
            report_digest=report_digest,
            compact_record_digest=compact_record_digest,
        )
        return self._transition(units=(units[0], units[1]))

    def fail_unit(self, run_id: str, *, at: datetime) -> ContinuationExecutionLedger:
        current = self.load()
        index = _run_index(run_id)
        if current.units[index].state is not UnitExecutionState.STARTED:
            raise ContinuationLedgerError("unit was not exclusively STARTED")
        units = list(current.units)
        units[index] = replace(
            units[index], state=UnitExecutionState.FAILED, ended_at=_utc(at, "unit failed_at")
        )
        return self._transition(units=(units[0], units[1]))

    def finalize(self, projection_digest: str) -> ContinuationExecutionLedger:
        current = self.load()
        if current.final_projection_digest is not None or any(
            unit.state is not UnitExecutionState.COMPLETED for unit in current.units
        ):
            raise ContinuationLedgerError("continuation lacks two completed units")
        return self._transition(final_projection_digest=projection_digest)


def _run_index(run_id: str) -> int:
    if run_id not in {"B", "C"}:
        raise ContinuationLedgerError("continuation authorizes only B/C")
    return ("B", "C").index(run_id)


def _validate_transition(
    previous: ContinuationExecutionLedger, current: ContinuationExecutionLedger
) -> None:
    identity = (
        "run_uuid",
        "frozen_code_sha",
        "dataset_version",
        "source_id",
        "manifest_sha256",
        "anchor_integrity_seal",
        "anchor_report_digest",
        "anchor_ledger_snapshot_sha256",
        "anchor_manifest_sha256",
        "initial_process_session_id",
        "created_at",
        "schema_version",
        "protocol_version",
    )
    if current.revision != previous.revision + 1 or any(
        getattr(current, name) != getattr(previous, name) for name in identity
    ):
        raise ContinuationLedgerError("ledger identity changed across transition")
    changed = tuple(
        index
        for index, (old, new) in enumerate(zip(previous.units, current.units, strict=True))
        if old != new
    )
    if changed:
        if len(changed) != 1 or current.final_projection_digest != previous.final_projection_digest:
            raise ContinuationLedgerError("multiple unit facts changed in transition")
        old, new = previous.units[changed[0]], current.units[changed[0]]
        if (old.state, new.state) not in {
            (UnitExecutionState.NOT_STARTED, UnitExecutionState.STARTED),
            (UnitExecutionState.STARTED, UnitExecutionState.COMPLETED),
            (UnitExecutionState.STARTED, UnitExecutionState.FAILED),
        }:
            raise ContinuationLedgerError("unit transition is not monotonic")
        if old.state is UnitExecutionState.STARTED and (
            old.started_at != new.started_at or old.process_session_id != new.process_session_id
        ):
            raise ContinuationLedgerError("unit start provenance changed")
    elif previous.final_projection_digest is not None or current.final_projection_digest is None:
        raise ContinuationLedgerError("empty or repeated ledger transition")
