"""Guarded one-shot Grid historical-study runner and deterministic reports.

No grant is bundled with the repository.  Real retained history can enter this
module only through an exact external grant and the accepted read-only adapter.
Synthetic fixtures use a distinct grant kind and calendar authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Self, cast

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from numpy.random import PCG64, Generator

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    STUDY_ID,
    STUDY_PLAN_SHA256,
    CalendarEpisode,
    CalendarStatus,
    GridCalendarLedger,
    GridHistoryPreflightError,
    GridHistoryWeek,
    LedgerWeekRecord,
    VerifiedCalendar,
    canonical_json,
)
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_ENGINE_VERSION,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
    GRID_PROTOCOL_VERSION,
    GRID_STRATEGY_ID,
    GridEpisodeInput,
    GridEpisodeReport,
    GridHistoricalSourceAdmission,
    GridLedgerCarry,
    GridScenario,
    GridV1Engine,
    GridV1Error,
    healthy_grid_health,
    verified_grid_contract_identity,
)
from adaptive_trading_bot.storage import SourceId

RUNNER_SCHEMA = "btc-eth-grid-4h-v1-one-shot-runner-v1"
RUNNER_VERSION = "0.1.0-guarded"
APPROVAL_SCHEMA = "btc-eth-grid-history-ed25519-approval-v1"
# A later architect decision must pin a real public key before any external grant can pass.
ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX: str | None = None
ACCEPTED_PREFLIGHT_HEAD = "bbe6a33b5ca7f20537a23e5a050d81a454f6f7c3"
ACCEPTED_ENGINE_HEAD = "dc450a4a3a0f2dfe114a690459971bc79d2aba68"
ACCEPTED_SOURCE_ID = "binance-usdm-btc-eth-grid-v3"
EXTERNAL_GRANT_KIND = "EXTERNAL_ARCHITECT_APPROVED_GRID_HISTORY_V1"
SYNTHETIC_GRANT_KIND = "SYNTHETIC_GRID_HISTORY_FIXTURE_V1"
TASK_IDS = (
    "BTCUSDT:BASE",
    "BTCUSDT:STRESS",
    "ETHUSDT:BASE",
    "ETHUSDT:STRESS",
)
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
BOOTSTRAP_SEED = 241
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_BLOCK_WEEKS = 4
BOOTSTRAP_PRNG = "numpy.random.PCG64"
BOOTSTRAP_BOUNDED_SAMPLER = (
    "numpy.random.Generator.integers(low=0,high=exclusive,size=(10000,ceil(n/4)),dtype=uint64)"
)


class GridHistoryRunnerError(RuntimeError):
    """The one-shot grant, ledger, input or report contract is invalid."""


class TaskState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class TerminalClassification(StrEnum):
    TECHNICAL_OR_INTEGRITY_INVALID = "TECHNICAL_OR_INTEGRITY_INVALID"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION = "NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION"
    CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION = "CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class GridWeekSummary:
    """Compact authoritative facts retained after a full weekly report is persisted."""

    sequence: int
    current_equity: Decimal
    order_fill_count: int
    fill_count: int
    forced_close_count: int
    maximum_running_peak_drawdown_fraction: Decimal


def verified_checkout_identity(
    repo: Path,
    *,
    imported_files: dict[str, Path],
) -> tuple[str, str]:
    """Bind a clean Git tree to the exact Python files imported by a caller."""

    repo = repo.resolve(strict=True)

    def git_text(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    if Path(git_text("rev-parse", "--show-toplevel")).resolve() != repo:
        raise GridHistoryRunnerError("repository root identity differs")
    if git_text("status", "--porcelain=v1", "--untracked-files=all"):
        raise GridHistoryRunnerError("historical execution requires an exact clean checkout")
    for relative, imported in imported_files.items():
        expected = (repo / relative).resolve(strict=True)
        if imported.resolve(strict=True) != expected:
            raise GridHistoryRunnerError("imported execution module is outside the named checkout")
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout
        if expected.read_bytes() != committed:
            raise GridHistoryRunnerError("imported execution module bytes differ from HEAD")
    return git_text("rev-parse", "HEAD"), git_text("rev-parse", "HEAD^{tree}")


@dataclass(frozen=True, slots=True)
class GridGrantApproval:
    """Detached architect signature over the exact canonical grant bytes."""

    schema: str
    key_id: str
    grant_sha256: str
    signature_ed25519_hex: str

    @classmethod
    def from_file(cls, path: Path) -> Self:
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise GridHistoryRunnerError("grant approval is not valid JSON") from exc
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if not isinstance(value, dict) or canonical_json(value) != raw or set(value) != expected:
            raise GridHistoryRunnerError("grant approval must be exact canonical JSON")
        try:
            return cls(**value)
        except TypeError as exc:
            raise GridHistoryRunnerError("grant approval values are invalid") from exc

    def verify(self, grant: GridStudyGrant, *, trusted_public_key_hex: str | None) -> None:
        if trusted_public_key_hex is None:
            raise GridHistoryRunnerError("no architect approval key is pinned in this executable")
        try:
            public_key_bytes = bytes.fromhex(trusted_public_key_hex)
            signature = bytes.fromhex(self.signature_ed25519_hex)
        except ValueError as exc:
            raise GridHistoryRunnerError("grant approval encoding is invalid") from exc
        expected_key_id = hashlib.sha256(public_key_bytes).hexdigest()
        if (
            self.schema != APPROVAL_SCHEMA
            or self.key_id != expected_key_id
            or self.grant_sha256 != grant.sha256
            or len(public_key_bytes) != 32
            or len(signature) != 64
        ):
            raise GridHistoryRunnerError("grant approval provenance differs")
        try:
            Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                signature, canonical_json(asdict(grant))
            )
        except (InvalidSignature, ValueError) as exc:
            raise GridHistoryRunnerError("grant approval signature is invalid") from exc


@dataclass(frozen=True, slots=True)
class GridStudyGrant:
    schema: str
    kind: str
    run_uuid: str
    executable_sha: str
    executable_tree: str
    study_id: str
    study_plan_sha256: str
    preflight_head: str
    engine_head: str
    engine_version: str
    protocol_id: str
    protocol_version: str
    protocol_head: str
    protocol_document_sha256: str
    parameters_sha256: str
    calendar_sha256: str
    dataset_version: str
    qualification_protocol: str
    manifest_sha256: str
    report_sha256: str
    source_id: str
    task_ids: tuple[str, ...]
    requested_modes: tuple[str, ...]
    network_requests: int
    task_concurrency: int
    max_wall_seconds: int

    @classmethod
    def from_file(cls, path: Path) -> Self:
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise GridHistoryRunnerError("execution grant is not valid JSON") from exc
        if not isinstance(value, dict) or canonical_json(value) != raw:
            raise GridHistoryRunnerError("execution grant must be canonical immutable JSON")
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(value) != expected:
            raise GridHistoryRunnerError("execution grant fields differ")
        try:
            return cls(
                **{
                    **value,
                    "task_ids": tuple(value["task_ids"]),
                    "requested_modes": tuple(value["requested_modes"]),
                }
            )
        except (TypeError, ValueError) as exc:
            raise GridHistoryRunnerError("execution grant values are invalid") from exc

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def validate(
        self,
        *,
        actual_sha: str,
        actual_tree: str,
        synthetic: bool,
        approval: GridGrantApproval | None = None,
    ) -> None:
        try:
            stable_uuid = str(uuid.UUID(self.run_uuid)) == self.run_uuid
        except ValueError:
            stable_uuid = False
        git_ids = (self.executable_sha, self.executable_tree, actual_sha, actual_tree)
        if (
            self.schema != RUNNER_SCHEMA
            or not stable_uuid
            or any(not _hex(value, 40) for value in git_ids)
            or self.executable_sha != actual_sha
            or self.executable_tree != actual_tree
            or self.study_id != STUDY_ID
            or self.study_plan_sha256 != STUDY_PLAN_SHA256
            or self.preflight_head != ACCEPTED_PREFLIGHT_HEAD
            or self.engine_head != ACCEPTED_ENGINE_HEAD
            or self.engine_version != GRID_ENGINE_VERSION
            or self.protocol_id != GRID_STRATEGY_ID
            or self.protocol_version != GRID_PROTOCOL_VERSION
            or self.protocol_head != GRID_ACCEPTED_PROTOCOL_HEAD
            or self.protocol_document_sha256 != GRID_PROTOCOL_DOCUMENT_SHA256
            or self.parameters_sha256 != GRID_PARAMETERS_SHA256
            or self.task_ids != TASK_IDS
            or self.requested_modes != ("BASE", "STRESS")
            or self.network_requests != 0
            or self.task_concurrency != 1
            or self.max_wall_seconds <= 0
        ):
            raise GridHistoryRunnerError("execution grant identity or budget differs")
        if synthetic:
            if (
                approval is not None
                or self.kind != SYNTHETIC_GRANT_KIND
                or not self.source_id.startswith("synthetic-")
                or not self.dataset_version.startswith("synthetic-")
                or not self.qualification_protocol.startswith("SYNTHETIC_")
                or not all(
                    _hex(value, 64)
                    for value in (
                        self.calendar_sha256,
                        self.manifest_sha256,
                        self.report_sha256,
                    )
                )
                or self.calendar_sha256 == ACCEPTED_CALENDAR_SHA256
                or self.manifest_sha256 == ACCEPTED_V3_MANIFEST_SHA256
                or self.report_sha256 == ACCEPTED_V3_REPORT_SHA256
            ):
                raise GridHistoryRunnerError("synthetic grant is not fixture-only")
        else:
            if (
                self.kind != EXTERNAL_GRANT_KIND
                or self.source_id != ACCEPTED_SOURCE_ID
                or self.calendar_sha256 != ACCEPTED_CALENDAR_SHA256
                or self.dataset_version != ACCEPTED_DATASET_VERSION
                or self.qualification_protocol != ACCEPTED_QUALIFICATION_PROTOCOL
                or self.manifest_sha256 != ACCEPTED_V3_MANIFEST_SHA256
                or self.report_sha256 != ACCEPTED_V3_REPORT_SHA256
            ):
                raise GridHistoryRunnerError("reviewed historical grant identity differs")
            if approval is None:
                raise GridHistoryRunnerError("independent architect grant approval is absent")
            approval.verify(
                self,
                trusted_public_key_hex=ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX,
            )


@dataclass(frozen=True, slots=True)
class GridStudyLedgerState:
    run_uuid: str
    grant_sha256: str
    executable_sha: str
    executable_tree: str
    task_ids: tuple[str, ...]
    preflight_sha256: str | None
    task_states: tuple[TaskState, ...]
    week_counts: tuple[int, ...]
    task_report_sha256: tuple[str | None, ...]
    revision: int
    finalized_sha256: str | None


class GridStudyLedgerStore:
    """Hash-linked, fsync-backed, append-only one-shot task evidence."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._owned_state: GridStudyLedgerState | None = None
        self._last_event_sha256: str | None = None

    @classmethod
    def create(cls, root: Path, grant: GridStudyGrant) -> Self:
        directory = root / grant.run_uuid
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise GridHistoryRunnerError("run UUID already exists") from exc
        store = cls(directory)
        last_event_sha256 = store._write(
            {
                "schema": "btc-eth-grid-study-ledger-event-v1",
                "event": "CREATED",
                "revision": 0,
                "previous_sha256": None,
                "run_uuid": grant.run_uuid,
                "grant_sha256": grant.sha256,
                "executable_sha": grant.executable_sha,
                "executable_tree": grant.executable_tree,
                "task_ids": list(grant.task_ids),
            }
        )
        store._owned_state = GridStudyLedgerState(
            grant.run_uuid,
            grant.sha256,
            grant.executable_sha,
            grant.executable_tree,
            grant.task_ids,
            None,
            (TaskState.NOT_STARTED,) * len(grant.task_ids),
            (0,) * len(grant.task_ids),
            (None,) * len(grant.task_ids),
            0,
            None,
        )
        store._last_event_sha256 = last_event_sha256
        return store

    def _write(self, event: dict[str, object]) -> str:
        revision = event.get("revision")
        if type(revision) is not int or revision < 0:
            raise GridHistoryRunnerError("ledger revision is invalid")
        destination = self.directory / f"{revision:05d}.json"
        pending = self.directory / f".{revision:05d}.pending"
        if destination.exists() or pending.exists():
            raise GridHistoryRunnerError("ledger append is ambiguous")
        payload = canonical_json(event)
        with pending.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, destination)
        return hashlib.sha256(payload).hexdigest()

    def _state_for_append(self) -> GridStudyLedgerState:
        if self._owned_state is None or self._last_event_sha256 is None:
            raise GridHistoryRunnerError("ledger append ownership is unavailable")
        return self._owned_state

    def load(self, *, owned_lock: bool = False) -> GridStudyLedgerState:
        files = sorted(self.directory.glob("[0-9][0-9][0-9][0-9][0-9].json"))
        if not files or any(path.name != f"{index:05d}.json" for index, path in enumerate(files)):
            raise GridHistoryRunnerError("ledger event sequence is incomplete")
        extras = set(self.directory.iterdir()) - set(files)
        if owned_lock:
            extras.discard(self.directory / ".transition.lock")
        allowed_suffixes = (".week.json", ".task.json")
        if any(
            path.name != "projection.json"
            and path.name != "preflight.json"
            and not path.name.endswith(allowed_suffixes)
            and path.name != "run-exit.json"
            for path in extras
        ):
            raise GridHistoryRunnerError("ledger contains foreign or pending evidence")
        previous: str | None = None
        states: list[TaskState] = []
        week_counts: list[int] = []
        reports: list[str | None] = []
        run_uuid = grant_sha = executable_sha = executable_tree = ""
        task_ids: tuple[str, ...] = ()
        preflight_sha: str | None = None
        finalized: str | None = None
        for index, path in enumerate(files):
            raw = path.read_bytes()
            try:
                event = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise GridHistoryRunnerError("ledger event is malformed") from exc
            if (
                not isinstance(event, dict)
                or canonical_json(event) != raw
                or event.get("schema") != "btc-eth-grid-study-ledger-event-v1"
                or event.get("revision") != index
                or event.get("previous_sha256") != previous
            ):
                raise GridHistoryRunnerError("ledger hash chain differs")
            name = event.get("event")
            if index == 0:
                if name != "CREATED" or event.get("run_uuid") != self.directory.name:
                    raise GridHistoryRunnerError("ledger creation differs")
                run_uuid = str(event["run_uuid"])
                grant_sha = str(event["grant_sha256"])
                executable_sha = str(event["executable_sha"])
                executable_tree = str(event["executable_tree"])
                task_ids = tuple(str(value) for value in event.get("task_ids", ()))
                if task_ids != TASK_IDS:
                    raise GridHistoryRunnerError("ledger task inventory differs")
                states = [TaskState.NOT_STARTED] * len(task_ids)
                week_counts = [0] * len(task_ids)
                reports = [None] * len(task_ids)
            elif name == "PREFLIGHT_VERIFIED":
                if preflight_sha is not None or any(
                    state is not TaskState.NOT_STARTED for state in states
                ):
                    raise GridHistoryRunnerError("preflight is duplicated or late")
                preflight_sha = _required_digest(event.get("preflight_sha256"), "preflight")
                artifact = self.directory / "preflight.json"
                if not artifact.is_file() or _sha_file(artifact) != preflight_sha:
                    raise GridHistoryRunnerError("preflight artifact differs")
            elif name == "FINALIZED":
                if finalized is not None or any(
                    state is not TaskState.COMPLETED for state in states
                ):
                    raise GridHistoryRunnerError("incomplete ledger cannot finalize")
                finalized = _required_digest(event.get("projection_sha256"), "projection")
            else:
                task_index = event.get("task_index")
                if type(task_index) is not int or not 0 <= task_index < len(states):
                    raise GridHistoryRunnerError("ledger task index differs")
                if name == "TASK_STARTED":
                    if states[task_index] is not TaskState.NOT_STARTED or any(
                        state is not TaskState.COMPLETED for state in states[:task_index]
                    ):
                        raise GridHistoryRunnerError("task was retried or reordered")
                    states[task_index] = TaskState.STARTED
                elif name == "WEEK_COMMITTED":
                    if (
                        states[task_index] is not TaskState.STARTED
                        or event.get("week_ordinal") != week_counts[task_index]
                    ):
                        raise GridHistoryRunnerError("week commitment is reordered")
                    digest = _required_digest(event.get("artifact_sha256"), "week artifact")
                    artifact = self.directory / str(event.get("artifact_name"))
                    if not artifact.is_file() or _sha_file(artifact) != digest:
                        raise GridHistoryRunnerError("week artifact differs")
                    week_counts[task_index] += 1
                elif name in {"TASK_FAILED", "TASK_UNKNOWN"}:
                    if states[task_index] is not TaskState.STARTED:
                        raise GridHistoryRunnerError("terminal task state lacks STARTED")
                    states[task_index] = (
                        TaskState.FAILED if name == "TASK_FAILED" else TaskState.UNKNOWN
                    )
                elif name == "TASK_COMPLETED":
                    if states[task_index] is not TaskState.STARTED:
                        raise GridHistoryRunnerError("task completion lacks STARTED")
                    digest = _required_digest(event.get("report_sha256"), "task report")
                    artifact = self.directory / str(event.get("artifact_name"))
                    if not artifact.is_file() or _sha_file(artifact) != digest:
                        raise GridHistoryRunnerError("task report differs")
                    reports[task_index] = digest
                    states[task_index] = TaskState.COMPLETED
                else:
                    raise GridHistoryRunnerError("ledger event type differs")
            previous = hashlib.sha256(raw).hexdigest()
        return GridStudyLedgerState(
            run_uuid,
            grant_sha,
            executable_sha,
            executable_tree,
            task_ids,
            preflight_sha,
            tuple(states),
            tuple(week_counts),
            tuple(reports),
            len(files) - 1,
            finalized,
        )

    def _append(self, event: str, *, task_index: int | None = None, **facts: object) -> None:
        lock = self.directory / ".transition.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise GridHistoryRunnerError("ledger transition is concurrent or ambiguous") from exc
        try:
            os.close(descriptor)
            state = self._state_for_append()
            previous_path = self.directory / f"{state.revision:05d}.json"
            try:
                previous_raw = previous_path.read_bytes()
            except OSError as exc:
                raise GridHistoryRunnerError("latest ledger event is unavailable") from exc
            if (
                hashlib.sha256(previous_raw).hexdigest() != self._last_event_sha256
                or (self.directory / f"{state.revision + 1:05d}.json").exists()
                or (self.directory / f".{state.revision + 1:05d}.pending").exists()
            ):
                raise GridHistoryRunnerError("latest ledger event or next append differs")
            record: dict[str, object] = {
                "schema": "btc-eth-grid-study-ledger-event-v1",
                "event": event,
                "revision": state.revision + 1,
                "previous_sha256": self._last_event_sha256,
                "observed_at": datetime.now(UTC).isoformat(),
            }
            if task_index is not None:
                record["task_index"] = task_index
            record.update(facts)
            self._last_event_sha256 = self._write(record)
            self._owned_state = replace(state, revision=state.revision + 1)
        finally:
            lock.unlink(missing_ok=True)

    def start(self, task_index: int) -> None:
        state = self._state_for_append()
        if (
            state.preflight_sha256 is None
            or not 0 <= task_index < len(state.task_ids)
            or state.task_states[task_index] is not TaskState.NOT_STARTED
            or any(item is not TaskState.COMPLETED for item in state.task_states[:task_index])
        ):
            raise GridHistoryRunnerError("task cannot start, resume or rerun")
        self._append("TASK_STARTED", task_index=task_index, task_id=state.task_ids[task_index])
        states = list(state.task_states)
        states[task_index] = TaskState.STARTED
        self._owned_state = replace(self._state_for_append(), task_states=tuple(states))

    def record_preflight(self, preflight: dict[str, object]) -> str:
        state = self._state_for_append()
        if state.preflight_sha256 is not None or any(
            item is not TaskState.NOT_STARTED for item in state.task_states
        ):
            raise GridHistoryRunnerError("preflight cannot be replaced")
        path = self.directory / "preflight.json"
        _write_new(path, canonical_json(preflight))
        digest = _sha_file(path)
        self._append("PREFLIGHT_VERIFIED", preflight_sha256=digest)
        self._owned_state = replace(self._state_for_append(), preflight_sha256=digest)
        return digest

    def commit_week(self, task_index: int, week_ordinal: int, payload: dict[str, object]) -> None:
        state = self._state_for_append()
        if (
            state.task_states[task_index] is not TaskState.STARTED
            or state.week_counts[task_index] != week_ordinal
        ):
            raise GridHistoryRunnerError("week has no active task")
        name = f"task-{task_index:02d}-week-{week_ordinal:03d}.week.json"
        path = self.directory / name
        _write_new(path, canonical_json(payload))
        self._append(
            "WEEK_COMMITTED",
            task_index=task_index,
            week_ordinal=week_ordinal,
            artifact_name=name,
            artifact_sha256=_sha_file(path),
            episode_start=payload.get("episode_start"),
            transition=payload.get("transition"),
        )
        week_counts = list(state.week_counts)
        week_counts[task_index] += 1
        self._owned_state = replace(self._state_for_append(), week_counts=tuple(week_counts))

    def complete(self, task_index: int, report: dict[str, object]) -> str:
        state = self._state_for_append()
        if state.task_states[task_index] is not TaskState.STARTED:
            raise GridHistoryRunnerError("task completion lacks STARTED")
        name = f"task-{task_index:02d}.task.json"
        path = self.directory / name
        _write_new(path, canonical_json(report))
        digest = _sha_file(path)
        self._append(
            "TASK_COMPLETED",
            task_index=task_index,
            artifact_name=name,
            report_sha256=digest,
        )
        states = list(state.task_states)
        states[task_index] = TaskState.COMPLETED
        reports = list(state.task_report_sha256)
        reports[task_index] = digest
        self._owned_state = replace(
            self._state_for_append(),
            task_states=tuple(states),
            task_report_sha256=tuple(reports),
        )
        return digest

    def terminate(self, task_index: int, *, unknown: bool, reason: str) -> None:
        state = self._state_for_append()
        if state.task_states[task_index] is not TaskState.STARTED:
            raise GridHistoryRunnerError("task terminal transition lacks STARTED")
        self._append(
            "TASK_UNKNOWN" if unknown else "TASK_FAILED",
            task_index=task_index,
            reason=reason,
        )
        states = list(state.task_states)
        states[task_index] = TaskState.UNKNOWN if unknown else TaskState.FAILED
        self._owned_state = replace(self._state_for_append(), task_states=tuple(states))

    def finalize(self, projection: dict[str, object]) -> str:
        state = self._state_for_append()
        if state.finalized_sha256 is not None or any(
            item is not TaskState.COMPLETED for item in state.task_states
        ):
            raise GridHistoryRunnerError("run is not ready to finalize")
        verified_state = self.load()
        if verified_state != state:
            raise GridHistoryRunnerError("incremental ledger state differs at finalization")
        path = self.directory / "projection.json"
        _write_new(path, canonical_json(projection))
        digest = _sha_file(path)
        self._append("FINALIZED", projection_sha256=digest)
        self._owned_state = replace(self._state_for_append(), finalized_sha256=digest)
        return digest


def moving_block_lower_bound(
    returns: tuple[Decimal, ...],
    *,
    contiguous_runs: tuple[tuple[Decimal, ...], ...] | None = None,
) -> Decimal | None:
    """Frozen four-week moving-block mean lower bound (10,000 draws, seed 241)."""

    runs = contiguous_runs if contiguous_runs is not None else (returns,)
    blocks = tuple(run[index : index + 4] for run in runs for index in range(max(0, len(run) - 3)))
    if not returns or not blocks:
        return None
    generator = Generator(PCG64(BOOTSTRAP_SEED))
    means: list[Decimal] = []
    block_count = (len(returns) + 3) // 4
    sampled_indices = generator.integers(
        0,
        len(blocks),
        size=(BOOTSTRAP_DRAWS, block_count),
        dtype=np.uint64,
    )
    with localcontext(_CONTEXT):
        for replicate in sampled_indices:
            sample = tuple(
                value for block_index in replicate for value in blocks[int(block_index)]
            )[: len(returns)]
            means.append(sum(sample, Decimal(0)) / Decimal(len(sample)))
    means.sort()
    return means[249]


def summarize_task(
    *,
    task_id: str,
    records: tuple[LedgerWeekRecord, ...],
    week_summaries: tuple[GridWeekSummary, ...],
    final_carry: GridLedgerCarry,
) -> dict[str, object]:
    """Build the predeclared economic report from compact committed-week facts."""

    included = tuple(record for record in records if record.included_in_weekly_statistics)
    if len(included) != len(week_summaries):
        raise GridHistoryRunnerError("eligible record/summary denominator differs")
    summary_by_sequence = {summary.sequence: summary for summary in week_summaries}
    if set(summary_by_sequence) != {record.sequence for record in included}:
        raise GridHistoryRunnerError("eligible record/summary identity differs")
    partitions = _partition_metrics(records, summary_by_sequence)
    validation = tuple(
        Decimal(record.weekly_return or "0")
        for record in included
        if record.partition == "VALIDATION"
    )
    retrospective = tuple(
        Decimal(record.weekly_return or "0")
        for record in included
        if record.partition == "RETROSPECTIVE_ASSESSMENT"
    )
    fill_count = sum(summary.fill_count for summary in week_summaries)
    forced_close_count = sum(summary.forced_close_count for summary in week_summaries)
    max_drawdown = max(
        (summary.maximum_running_peak_drawdown_fraction for summary in week_summaries),
        default=Decimal(0),
    )
    validation_lower = moving_block_lower_bound(
        validation,
        contiguous_runs=_contiguous_returns(records, "VALIDATION"),
    )
    validation_median = _median(validation)
    retrospective_median = _median(retrospective)
    with localcontext(_CONTEXT):
        total_return = (
            final_carry.current_equity - final_carry.original_account_capital
        ) / final_carry.original_account_capital
    validation_fill_weeks = _required_int(partitions["VALIDATION"]["fill_weeks"])
    if len(validation) < 80 or validation_fill_weeks < 40 or validation_lower is None:
        classification = TerminalClassification.INSUFFICIENT_EVIDENCE
    elif (
        validation_median is None
        or validation_median <= 0
        or validation_lower is None
        or validation_lower <= 0
        or total_return <= 0
        or retrospective_median is None
        or retrospective_median <= 0
        or max_drawdown > Decimal("0.20")
    ):
        classification = TerminalClassification.NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION
    else:
        classification = TerminalClassification.CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION
    return {
        "schema": "btc-eth-grid-task-report-v1",
        "task_id": task_id,
        "scheduled_weeks": len(records),
        "eligible_denominator": len(included),
        "metadata_ineligible": sum(
            record.calendar_status == CalendarStatus.METADATA_INELIGIBLE.value for record in records
        ),
        "no_trade_cash_only": sum(
            record.week_disposition == "NO_TRADE_CASH_ONLY" for record in records
        ),
        "terminated_cash_only": sum(
            record.week_disposition == "TERMINATED_CASH_ONLY" for record in records
        ),
        "fill_count": fill_count,
        "forced_close_count": forced_close_count,
        "partition_metrics": partitions,
        "final_equity": str(final_carry.current_equity),
        "total_net_equity_change": str(
            final_carry.current_equity - final_carry.original_account_capital
        ),
        "total_net_return": str(total_return),
        "cumulative_gross_realized_pnl": str(final_carry.cumulative_gross_realized_pnl),
        "cumulative_fees": str(final_carry.cumulative_fees),
        "cumulative_funding": str(final_carry.cumulative_funding),
        "terminated": final_carry.terminated,
        "maximum_running_peak_drawdown_fraction": str(max_drawdown),
        "validation_eligible_weeks": len(validation),
        "validation_fill_weeks": validation_fill_weeks,
        "validation_median_weekly_return": _text(validation_median),
        "validation_mean_lower_95_bound": _text(validation_lower),
        "validation_bootstrap_contract": {
            "prng": BOOTSTRAP_PRNG,
            "numpy_version": np.__version__,
            "seed": BOOTSTRAP_SEED,
            "bounded_sampler": BOOTSTRAP_BOUNDED_SAMPLER,
            "block_weeks": BOOTSTRAP_BLOCK_WEEKS,
            "draws": BOOTSTRAP_DRAWS,
            "lower_quantile_sorted_index": 249,
        },
        "retrospective_eligible_weeks": len(retrospective),
        "retrospective_median_weekly_return": _text(retrospective_median),
        "classification": classification.value,
        "trading_authorization": False,
    }


WeekLoader = Callable[[CalendarEpisode], GridHistoryWeek]


def run_one_shot(
    *,
    grant: GridStudyGrant,
    calendar: VerifiedCalendar,
    ledger_root: Path,
    actual_sha: str,
    actual_tree: str,
    week_loader: WeekLoader,
    preflight: dict[str, object],
    synthetic: bool,
    approval: GridGrantApproval | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[GridStudyLedgerStore, dict[str, object]]:
    """Run once and durably record the actual command outcome after ledger creation."""

    observed_start = clock()
    try:
        return _run_one_shot(
            grant=grant,
            calendar=calendar,
            ledger_root=ledger_root,
            actual_sha=actual_sha,
            actual_tree=actual_tree,
            week_loader=week_loader,
            preflight=preflight,
            synthetic=synthetic,
            approval=approval,
            clock=clock,
        )
    except Exception:
        directory = ledger_root / grant.run_uuid
        exit_path = directory / "run-exit.json"
        if directory.is_dir() and not exit_path.exists():
            _write_new(
                exit_path,
                canonical_json(
                    {
                        "schema": "btc-eth-grid-history-run-exit-v1",
                        "run_uuid": grant.run_uuid,
                        "exit_code": 1,
                        "status": "FAILED_OR_UNKNOWN",
                        "elapsed_seconds": str(clock() - observed_start),
                    }
                ),
            )
        raise


def _run_one_shot(
    *,
    grant: GridStudyGrant,
    calendar: VerifiedCalendar,
    ledger_root: Path,
    actual_sha: str,
    actual_tree: str,
    week_loader: WeekLoader,
    preflight: dict[str, object],
    synthetic: bool,
    approval: GridGrantApproval | None,
    clock: Callable[[], float] = monotonic,
) -> tuple[GridStudyLedgerStore, dict[str, object]]:
    """Run four tasks once; callers own the already-validated bounded week loader."""

    grant.validate(
        actual_sha=actual_sha,
        actual_tree=actual_tree,
        synthetic=synthetic,
        approval=approval,
    )
    started_at = clock()
    deadline = started_at + grant.max_wall_seconds
    store = GridStudyLedgerStore.create(ledger_root, grant)
    preflight_sha = store.record_preflight(preflight)
    task_reports: list[dict[str, object]] = []
    admission = (
        None
        if synthetic
        else GridHistoricalSourceAdmission.from_verified_grant(
            SourceId(grant.source_id),
            grant.executable_sha,
            grant.executable_tree,
            grant.sha256,
            grant.study_plan_sha256,
            grant.calendar_sha256,
            grant.manifest_sha256,
            grant.report_sha256,
        )
    )
    for task_index, task_id in enumerate(grant.task_ids):
        _require_budget(clock, deadline)
        symbol_text, scenario_text = task_id.split(":", 1)
        symbol = _symbol(symbol_text)
        scenario = GridScenario(scenario_text)
        store.start(task_index)
        task_ledger = GridCalendarLedger(
            symbol,
            scenario.value,
            calendar=calendar,
            expected_source_id=grant.source_id,
            expected_supplied_code_sha=grant.executable_sha,
        )
        week_summaries: list[GridWeekSummary] = []
        try:
            for week_ordinal, row in enumerate(calendar.rows_for(symbol_text)):
                _require_budget(clock, deadline)
                if row.status is CalendarStatus.METADATA_INELIGIBLE:
                    record = task_ledger.advance_ineligible(row)
                    report = None
                    source_hashes: tuple[tuple[str, str], ...] = ()
                else:
                    week = week_loader(row)
                    if week.calendar != row:
                        raise GridHistoryRunnerError("week loader changed calendar identity")
                    if (
                        week.source_identity.dataset_version != grant.dataset_version
                        or week.source_identity.qualification_protocol
                        != grant.qualification_protocol
                        or week.source_identity.manifest_sha256 != grant.manifest_sha256
                        or week.source_identity.report_sha256 != grant.report_sha256
                    ):
                        raise GridHistoryRunnerError("week source identity differs from grant")
                    episode = GridEpisodeInput(
                        SourceId(grant.source_id),
                        symbol,
                        row.episode_start,
                        week.completed_4h_closes,
                        grant.executable_sha,
                        verified_grid_contract_identity(),
                        task_ledger.carry,
                    )
                    engine = GridV1Engine(
                        episode,
                        scenario,
                        healthy_grid_health(episode),
                        historical_admission=admission,
                    )
                    for minute in week.minutes:
                        _require_budget(clock, deadline)
                        engine.process_minute(minute)
                    report = engine.report()
                    if report.completion_status != "COMPLETE":
                        raise GridHistoryPreflightError(
                            f"episode is {report.completion_status}, not complete"
                        )
                    record = task_ledger.advance_eligible(
                        row,
                        report=report,
                        carry_out=engine.carry_out(),
                    )
                    week_summaries.append(_summarize_episode_report(record.sequence, report))
                    source_hashes = week.source_file_sha256
                store.commit_week(
                    task_index,
                    week_ordinal,
                    {
                        **asdict(record),
                        "engine_report": None if report is None else asdict(report),
                        "source_file_sha256": [list(item) for item in source_hashes],
                    },
                )
        except (GridHistoryPreflightError, GridHistoryRunnerError, GridV1Error) as exc:
            store.terminate(task_index, unknown=True, reason=str(exc))
            raise GridHistoryRunnerError(f"task integrity became UNKNOWN: {exc}") from exc
        except Exception:
            store.terminate(task_index, unknown=False, reason="TASK_FAILURE")
            raise
        task_report = summarize_task(
            task_id=task_id,
            records=tuple(task_ledger.records),
            week_summaries=tuple(week_summaries),
            final_carry=task_ledger.carry,
        )
        store.complete(task_index, task_report)
        task_reports.append(task_report)
    _require_budget(clock, deadline)
    projection = {
        "schema": "btc-eth-grid-history-projection-v1",
        "run_uuid": grant.run_uuid,
        "grant_sha256": grant.sha256,
        "executable_sha": grant.executable_sha,
        "executable_tree": grant.executable_tree,
        "study_plan_sha256": grant.study_plan_sha256,
        "preflight_sha256": preflight_sha,
        "task_reports": task_reports,
        "classification": classify_task_reports(task_reports),
        "historical_execution": "SYNTHETIC_FIXTURE_ONLY" if synthetic else "GRANT_AUTHORIZED",
        "trading_authorization": False,
    }
    projection_sha = store.finalize(projection)
    projection["projection_sha256"] = projection_sha
    _write_new(
        store.directory / "run-exit.json",
        canonical_json(
            {
                "schema": "btc-eth-grid-history-run-exit-v1",
                "run_uuid": grant.run_uuid,
                "exit_code": 0,
                "status": "FINALIZED",
                "elapsed_seconds": str(clock() - started_at),
            }
        ),
    )
    return store, projection


def verify_finalized(directory: Path) -> dict[str, object]:
    """Read-only verification of ledger chain, artifacts and projection identity."""

    store = GridStudyLedgerStore(directory)
    state = store.load()
    if state.finalized_sha256 is None:
        raise GridHistoryRunnerError("ledger is not finalized")
    projection_path = directory / "projection.json"
    if _sha_file(projection_path) != state.finalized_sha256:
        raise GridHistoryRunnerError("projection hash differs")
    value = json.loads(projection_path.read_bytes())
    task_reports = [
        json.loads((directory / f"task-{index:02d}.task.json").read_bytes())
        for index in range(len(state.task_ids))
    ]
    if (
        not isinstance(value, dict)
        or value.get("run_uuid") != state.run_uuid
        or value.get("grant_sha256") != state.grant_sha256
        or value.get("executable_sha") != state.executable_sha
        or value.get("executable_tree") != state.executable_tree
        or value.get("preflight_sha256") != state.preflight_sha256
        or value.get("task_reports") != task_reports
        or value.get("classification") != classify_task_reports(task_reports)
        or value.get("trading_authorization") is not False
    ):
        raise GridHistoryRunnerError("projection run identity differs")
    exit_path = directory / "run-exit.json"
    try:
        exit_raw = exit_path.read_bytes()
        run_exit = json.loads(exit_raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise GridHistoryRunnerError("successful run exit record is unavailable") from exc
    if (
        not isinstance(run_exit, dict)
        or canonical_json(run_exit) != exit_raw
        or run_exit.get("schema") != "btc-eth-grid-history-run-exit-v1"
        or run_exit.get("run_uuid") != state.run_uuid
        or run_exit.get("exit_code") != 0
        or run_exit.get("status") != "FINALIZED"
    ):
        raise GridHistoryRunnerError("successful run exit record differs")
    return {
        "status": "ARTIFACT_CONSISTENCY_VERIFIED",
        "verification_scope": (
            "HASH_CHAIN_IDENTITIES_AND_SAVED_REPORT_RECONCILIATION_ONLY_"
            "NOT_INDEPENDENT_ECONOMIC_REPLICATION"
        ),
        "run_uuid": state.run_uuid,
        "revision": state.revision,
        "task_states": [state.value for state in state.task_states],
        "projection_sha256": state.finalized_sha256,
    }


def _contiguous_returns(
    records: tuple[LedgerWeekRecord, ...], partition: str
) -> tuple[tuple[Decimal, ...], ...]:
    runs: list[tuple[Decimal, ...]] = []
    current: list[Decimal] = []
    for record in records:
        if record.partition != partition or not record.included_in_weekly_statistics:
            if current:
                runs.append(tuple(current))
                current = []
            continue
        current.append(Decimal(record.weekly_return or "0"))
    if current:
        runs.append(tuple(current))
    return tuple(runs)


def _partition_metrics(
    records: tuple[LedgerWeekRecord, ...],
    summaries: dict[int, GridWeekSummary],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    current_equity = Decimal("10000")
    for partition in ("DEVELOPMENT", "VALIDATION", "RETROSPECTIVE_ASSESSMENT"):
        partition_records = tuple(record for record in records if record.partition == partition)
        opening_equity = current_equity
        eligible = 0
        fill_weeks = 0
        maximum_drawdown = Decimal(0)
        for record in partition_records:
            if not record.included_in_weekly_statistics:
                continue
            eligible += 1
            summary = summaries.get(record.sequence)
            if summary is None:
                raise GridHistoryRunnerError("eligible partition summary is unavailable")
            if summary.order_fill_count > 0:
                fill_weeks += 1
            current_equity = summary.current_equity
            maximum_drawdown = max(
                maximum_drawdown,
                summary.maximum_running_peak_drawdown_fraction,
            )
        with localcontext(_CONTEXT):
            total_return = (
                (current_equity - opening_equity) / opening_equity
                if opening_equity != 0
                else Decimal(0)
            )
        result[partition] = {
            "scheduled_weeks": len(partition_records),
            "eligible_weeks": eligible,
            "excluded_weeks": len(partition_records) - eligible,
            "fill_weeks": fill_weeks,
            "opening_equity": str(opening_equity),
            "closing_equity": str(current_equity),
            "total_net_equity_change": str(current_equity - opening_equity),
            "total_net_return": str(total_return),
            "maximum_running_peak_drawdown_fraction": str(maximum_drawdown),
        }
    return result


def _summarize_episode_report(sequence: int, report: GridEpisodeReport) -> GridWeekSummary:
    if report.carry_out is None:
        raise GridHistoryRunnerError("eligible engine report carry-out is unavailable")
    try:
        current_equity = Decimal(str(report.carry_out["current_equity"]))
    except (KeyError, ArithmeticError) as exc:
        raise GridHistoryRunnerError("eligible engine report equity is invalid") from exc
    if not current_equity.is_finite():
        raise GridHistoryRunnerError("eligible engine report equity is invalid")
    return GridWeekSummary(
        sequence=sequence,
        current_equity=current_equity,
        order_fill_count=sum(event.get("event_type") == "ORDER_FILLED" for event in report.audit),
        fill_count=sum(
            event.get("event_type") in {"ORDER_FILLED", "FORCED_CLOSE_FILLED"}
            for event in report.audit
        ),
        forced_close_count=sum(
            event.get("event_type") == "FORCED_CLOSE_FILLED" for event in report.audit
        ),
        maximum_running_peak_drawdown_fraction=_report_maximum_drawdown(report),
    )


def _report_maximum_drawdown(report: GridEpisodeReport) -> Decimal:
    expected = report.input_contract.get("required_minute_count")
    if type(expected) is not int or expected <= 0:
        raise GridHistoryRunnerError("engine report minute denominator differs")
    maximum = Decimal(0)
    observed = 0
    for event in report.audit:
        if event.get("event_type") != "MINUTE_MARKED":
            continue
        observed += 1
        details = event.get("details")
        if not isinstance(details, dict):
            raise GridHistoryRunnerError("minute equity audit details differ")
        try:
            equity = Decimal(str(details["equity"]))
            peak = Decimal(str(details["peak_equity"]))
        except (KeyError, ArithmeticError) as exc:
            raise GridHistoryRunnerError("minute equity audit is invalid") from exc
        if not equity.is_finite() or not peak.is_finite() or peak <= 0 or equity > peak:
            raise GridHistoryRunnerError("minute equity/peak relation is invalid")
        with localcontext(_CONTEXT):
            maximum = max(maximum, (peak - equity) / peak)
    if observed != expected:
        raise GridHistoryRunnerError("complete engine report minute audit is incomplete")
    return maximum


def classify_task_reports(reports: Iterable[dict[str, object]]) -> str:
    """Apply the frozen paired economic gates to four validated task reports."""
    by_task = {str(report["task_id"]): report for report in reports}
    if set(by_task) != set(TASK_IDS):
        return TerminalClassification.TECHNICAL_OR_INTEGRITY_INVALID.value
    if any(
        _required_int(report["validation_eligible_weeks"]) < 80
        or _required_int(report["validation_fill_weeks"]) < 40
        or report["retrospective_median_weekly_return"] is None
        for report in by_task.values()
    ) or any(
        by_task[f"{symbol}:BASE"]["validation_median_weekly_return"] is None
        or by_task[f"{symbol}:BASE"]["validation_mean_lower_95_bound"] is None
        for symbol in ("BTCUSDT", "ETHUSDT")
    ):
        return TerminalClassification.INSUFFICIENT_EVIDENCE.value
    for symbol in ("BTCUSDT", "ETHUSDT"):
        base = by_task[f"{symbol}:BASE"]
        stress = by_task[f"{symbol}:STRESS"]
        base_validation = _report_partition(base, "VALIDATION")
        stress_validation = _report_partition(stress, "VALIDATION")
        base_retrospective = _report_partition(base, "RETROSPECTIVE_ASSESSMENT")
        stress_retrospective = _report_partition(stress, "RETROSPECTIVE_ASSESSMENT")
        economic_values = (
            Decimal(str(base["validation_median_weekly_return"])),
            Decimal(str(base["validation_mean_lower_95_bound"])),
            Decimal(str(stress_validation["total_net_return"])),
            Decimal(str(base_retrospective["total_net_equity_change"])),
            Decimal(str(stress_retrospective["total_net_equity_change"])),
            Decimal(str(base["retrospective_median_weekly_return"])),
            Decimal(str(stress["retrospective_median_weekly_return"])),
        )
        drawdowns = (
            Decimal(str(base_validation["maximum_running_peak_drawdown_fraction"])),
            Decimal(str(stress_validation["maximum_running_peak_drawdown_fraction"])),
            Decimal(str(base_retrospective["maximum_running_peak_drawdown_fraction"])),
            Decimal(str(stress_retrospective["maximum_running_peak_drawdown_fraction"])),
        )
        if (
            any(value <= 0 for value in economic_values)
            or any(value > Decimal("0.20") for value in drawdowns)
            or bool(base["terminated"])
            or bool(stress["terminated"])
        ):
            return TerminalClassification.NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION.value
    return TerminalClassification.CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION.value


def _report_partition(report: dict[str, object], name: str) -> dict[str, object]:
    value = report.get("partition_metrics")
    if not isinstance(value, dict):
        raise GridHistoryRunnerError("task partition report differs")
    partition = value.get(name)
    if not isinstance(partition, dict):
        raise GridHistoryRunnerError("task partition report differs")
    return cast(dict[str, object], partition)


def _required_int(value: object) -> int:
    if type(value) is not int:
        raise GridHistoryRunnerError("report integer field differs")
    return value


def _require_budget(clock: Callable[[], float], deadline: float) -> None:
    if clock() > deadline:
        raise GridHistoryRunnerError("fixed processing budget exhausted")


def _median(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _symbol(value: str) -> TradingSymbol:
    if value == "BTCUSDT":
        return TradingSymbol("BTC", "USDT")
    if value == "ETHUSDT":
        return TradingSymbol("ETH", "USDT")
    raise GridHistoryRunnerError("task symbol differs")


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def _required_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _hex(value, 64):
        raise GridHistoryRunnerError(f"{field} digest is invalid")
    return value


def _sha_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


__all__ = [
    "ACCEPTED_PREFLIGHT_HEAD",
    "ACCEPTED_SOURCE_ID",
    "APPROVAL_SCHEMA",
    "ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX",
    "BOOTSTRAP_BOUNDED_SAMPLER",
    "BOOTSTRAP_DRAWS",
    "BOOTSTRAP_PRNG",
    "BOOTSTRAP_SEED",
    "EXTERNAL_GRANT_KIND",
    "RUNNER_SCHEMA",
    "RUNNER_VERSION",
    "SYNTHETIC_GRANT_KIND",
    "TASK_IDS",
    "GridGrantApproval",
    "GridHistoryRunnerError",
    "GridStudyGrant",
    "GridStudyLedgerState",
    "GridStudyLedgerStore",
    "GridWeekSummary",
    "TaskState",
    "TerminalClassification",
    "classify_task_reports",
    "moving_block_lower_bound",
    "run_one_shot",
    "summarize_task",
    "verified_checkout_identity",
    "verify_finalized",
]
