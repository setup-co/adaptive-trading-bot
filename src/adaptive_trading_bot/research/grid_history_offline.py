"""Owner-waived, offline-only entry boundary for the frozen Grid study.

This module is deliberately separate from the signed generic runner.  It accepts
only the exact retained v3 authority and exact frozen Grid contracts named by
Issue #255's owner directive.  It has no network, exchange, Paper, Shadow, Live,
or runtime registration surface.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    STUDY_ID,
    STUDY_PLAN_SHA256,
    V3GridHistoryAdapter,
    VerifiedCalendar,
    canonical_json,
    sha256_file,
)
from adaptive_trading_bot.research.grid_history_runner import (
    ACCEPTED_ENGINE_HEAD,
    ACCEPTED_PREFLIGHT_HEAD,
    ACCEPTED_SOURCE_ID,
    RUNNER_SCHEMA,
    TASK_IDS,
    GridGrantApproval,
    GridHistoryRunnerError,
    GridStudyGrant,
    run_one_shot,
)
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_ENGINE_VERSION,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
    GRID_PROTOCOL_VERSION,
    GRID_STRATEGY_ID,
)

OFFLINE_MANIFEST_SCHEMA = "btc-eth-grid-4h-owner-waived-offline-manifest-v1"
OFFLINE_GRANT_KIND = "OWNER_WAIVED_OFFLINE_RESEARCH_GRID_HISTORY_V1"
OWNER_DIRECTIVE_COMMENT_ID = 6927588719
OWNER_DIRECTIVE_URL = (
    "https://github.com/setup-co/adaptive-trading-bot/issues/255#issuecomment-6927588719"
)
ACCEPTED_REFERENCE_SHA = "159461e8cbc349d3f58d91d9688f5a0ffb05f7b9"
ACCEPTED_REFERENCE_TREE = "f34aeba0089a4464f771d676cc016f97005946f9"
MAX_WALL_SECONDS = 43_200


@dataclass(frozen=True, slots=True)
class OfflineResearchManifest:
    """Canonical pre-value-read identity record for the one offline study."""

    schema: str
    authorization: str
    owner_directive_comment_id: int
    owner_directive_url: str
    run_uuid: str
    accepted_reference_sha: str
    accepted_reference_tree: str
    executable_sha: str
    executable_tree: str
    offline_adapter_sha256: str
    offline_wrapper_sha256: str
    runner_sha256: str
    preflight_sha256: str
    engine_sha256: str
    study_plan_sha256: str
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
    data_root: str
    ledger_root: str

    @classmethod
    def from_file(cls, path: Path) -> Self:
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise GridHistoryRunnerError("offline manifest is not valid JSON") from exc
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if not isinstance(value, dict) or canonical_json(value) != raw or set(value) != expected:
            raise GridHistoryRunnerError("offline manifest must be exact canonical JSON")
        try:
            return cls(
                **{
                    **value,
                    "task_ids": tuple(value["task_ids"]),
                    "requested_modes": tuple(value["requested_modes"]),
                }
            )
        except (TypeError, ValueError) as exc:
            raise GridHistoryRunnerError("offline manifest values are invalid") from exc

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def validate(self, *, actual_sha: str, actual_tree: str) -> None:
        try:
            stable_uuid = str(uuid.UUID(self.run_uuid)) == self.run_uuid
        except ValueError:
            stable_uuid = False
        if (
            self.schema != OFFLINE_MANIFEST_SCHEMA
            or self.authorization != "OWNER_WAIVED_SIGNATURE_OFFLINE_RESEARCH_ONLY"
            or self.owner_directive_comment_id != OWNER_DIRECTIVE_COMMENT_ID
            or self.owner_directive_url != OWNER_DIRECTIVE_URL
            or not stable_uuid
            or self.accepted_reference_sha != ACCEPTED_REFERENCE_SHA
            or self.accepted_reference_tree != ACCEPTED_REFERENCE_TREE
            or self.executable_sha != actual_sha
            or self.executable_tree != actual_tree
            or self.study_plan_sha256 != STUDY_PLAN_SHA256
            or self.protocol_head != GRID_ACCEPTED_PROTOCOL_HEAD
            or self.protocol_document_sha256 != GRID_PROTOCOL_DOCUMENT_SHA256
            or self.parameters_sha256 != GRID_PARAMETERS_SHA256
            or self.calendar_sha256 != ACCEPTED_CALENDAR_SHA256
            or self.dataset_version != ACCEPTED_DATASET_VERSION
            or self.qualification_protocol != ACCEPTED_QUALIFICATION_PROTOCOL
            or self.manifest_sha256 != ACCEPTED_V3_MANIFEST_SHA256
            or self.report_sha256 != ACCEPTED_V3_REPORT_SHA256
            or self.source_id != ACCEPTED_SOURCE_ID
            or self.task_ids != TASK_IDS
            or self.requested_modes != ("BASE", "STRESS")
            or self.network_requests != 0
            or self.task_concurrency != 1
            or self.max_wall_seconds != MAX_WALL_SECONDS
        ):
            raise GridHistoryRunnerError("offline owner-waiver manifest identity differs")
        for value in (
            self.offline_adapter_sha256,
            self.offline_wrapper_sha256,
            self.runner_sha256,
            self.preflight_sha256,
            self.engine_sha256,
        ):
            if not _hex(value, 64):
                raise GridHistoryRunnerError("offline executable file identity differs")


class OwnerWaivedGridStudyGrant(GridStudyGrant):
    """Exact historical grant admitted only by the isolated owner-waiver CLI."""

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
            synthetic
            or approval is not None
            or self.schema != RUNNER_SCHEMA
            or self.kind != OFFLINE_GRANT_KIND
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
            or self.calendar_sha256 != ACCEPTED_CALENDAR_SHA256
            or self.dataset_version != ACCEPTED_DATASET_VERSION
            or self.qualification_protocol != ACCEPTED_QUALIFICATION_PROTOCOL
            or self.manifest_sha256 != ACCEPTED_V3_MANIFEST_SHA256
            or self.report_sha256 != ACCEPTED_V3_REPORT_SHA256
            or self.source_id != ACCEPTED_SOURCE_ID
            or self.task_ids != TASK_IDS
            or self.requested_modes != ("BASE", "STRESS")
            or self.network_requests != 0
            or self.task_concurrency != 1
            or self.max_wall_seconds != MAX_WALL_SECONDS
        ):
            raise GridHistoryRunnerError("offline owner-waiver grant identity differs")


def build_manifest(
    *,
    repo: Path,
    data_root: Path,
    ledger_root: Path,
    run_uuid: str,
    actual_sha: str,
    actual_tree: str,
) -> OfflineResearchManifest:
    """Build the canonical identity record without opening historical values."""

    source = repo / "src/adaptive_trading_bot/research"
    wrapper = repo / "scripts/research/grid_history_offline.py"
    calendar = repo / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    resolved_data = data_root.resolve(strict=True)
    if sha256_file(resolved_data / "qualification-manifest-v3.json") != ACCEPTED_V3_MANIFEST_SHA256:
        raise GridHistoryRunnerError("accepted v3 manifest hash differs")
    if sha256_file(resolved_data / "publication-report-v3.json") != ACCEPTED_V3_REPORT_SHA256:
        raise GridHistoryRunnerError("accepted v3 report hash differs")
    if sha256_file(calendar) != ACCEPTED_CALENDAR_SHA256:
        raise GridHistoryRunnerError("accepted calendar hash differs")
    return OfflineResearchManifest(
        schema=OFFLINE_MANIFEST_SCHEMA,
        authorization="OWNER_WAIVED_SIGNATURE_OFFLINE_RESEARCH_ONLY",
        owner_directive_comment_id=OWNER_DIRECTIVE_COMMENT_ID,
        owner_directive_url=OWNER_DIRECTIVE_URL,
        run_uuid=run_uuid,
        accepted_reference_sha=ACCEPTED_REFERENCE_SHA,
        accepted_reference_tree=ACCEPTED_REFERENCE_TREE,
        executable_sha=actual_sha,
        executable_tree=actual_tree,
        offline_adapter_sha256=sha256_file(source / "grid_history_offline.py"),
        offline_wrapper_sha256=sha256_file(wrapper),
        runner_sha256=sha256_file(source / "grid_history_runner.py"),
        preflight_sha256=sha256_file(source / "grid_history_preflight.py"),
        engine_sha256=sha256_file(source / "grid_v1.py"),
        study_plan_sha256=STUDY_PLAN_SHA256,
        protocol_head=GRID_ACCEPTED_PROTOCOL_HEAD,
        protocol_document_sha256=GRID_PROTOCOL_DOCUMENT_SHA256,
        parameters_sha256=GRID_PARAMETERS_SHA256,
        calendar_sha256=ACCEPTED_CALENDAR_SHA256,
        dataset_version=ACCEPTED_DATASET_VERSION,
        qualification_protocol=ACCEPTED_QUALIFICATION_PROTOCOL,
        manifest_sha256=ACCEPTED_V3_MANIFEST_SHA256,
        report_sha256=ACCEPTED_V3_REPORT_SHA256,
        source_id=ACCEPTED_SOURCE_ID,
        task_ids=TASK_IDS,
        requested_modes=("BASE", "STRESS"),
        network_requests=0,
        task_concurrency=1,
        max_wall_seconds=MAX_WALL_SECONDS,
        data_root=str(resolved_data),
        ledger_root=str(ledger_root.resolve()),
    )


def grant_from_manifest(manifest: OfflineResearchManifest) -> OwnerWaivedGridStudyGrant:
    return OwnerWaivedGridStudyGrant(
        RUNNER_SCHEMA,
        OFFLINE_GRANT_KIND,
        manifest.run_uuid,
        manifest.executable_sha,
        manifest.executable_tree,
        STUDY_ID,
        STUDY_PLAN_SHA256,
        ACCEPTED_PREFLIGHT_HEAD,
        ACCEPTED_ENGINE_HEAD,
        GRID_ENGINE_VERSION,
        GRID_STRATEGY_ID,
        GRID_PROTOCOL_VERSION,
        GRID_ACCEPTED_PROTOCOL_HEAD,
        GRID_PROTOCOL_DOCUMENT_SHA256,
        GRID_PARAMETERS_SHA256,
        ACCEPTED_CALENDAR_SHA256,
        ACCEPTED_DATASET_VERSION,
        ACCEPTED_QUALIFICATION_PROTOCOL,
        ACCEPTED_V3_MANIFEST_SHA256,
        ACCEPTED_V3_REPORT_SHA256,
        ACCEPTED_SOURCE_ID,
        TASK_IDS,
        ("BASE", "STRESS"),
        0,
        1,
        MAX_WALL_SECONDS,
    )


def run_offline_study(
    *,
    repo: Path,
    manifest: OfflineResearchManifest,
    actual_sha: str,
    actual_tree: str,
) -> dict[str, object]:
    """Execute the one retained-data study after immutable manifest admission."""

    manifest.validate(actual_sha=actual_sha, actual_tree=actual_tree)
    data_root = Path(manifest.data_root).resolve(strict=True)
    ledger_root = Path(manifest.ledger_root).resolve()
    calendar = VerifiedCalendar.accepted(
        repo / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    )
    adapter = V3GridHistoryAdapter(data_root, calendar=calendar)
    files, byte_count = adapter.preflight_sources()
    grant = grant_from_manifest(manifest)
    grant.validate(
        actual_sha=actual_sha,
        actual_tree=actual_tree,
        synthetic=False,
        approval=None,
    )
    preflight = {
        "schema": "grid-history-owner-waived-offline-preflight-v1",
        "authorization": manifest.authorization,
        "owner_directive_comment_id": manifest.owner_directive_comment_id,
        "owner_directive_url": manifest.owner_directive_url,
        "offline_manifest_sha256": manifest.sha256,
        "network_requests": 0,
        "validated_source_files": files,
        "validated_source_bytes": byte_count,
        "calendar_sha256": calendar.identity,
    }
    _, projection = run_one_shot(
        grant=grant,
        calendar=calendar,
        ledger_root=ledger_root,
        actual_sha=actual_sha,
        actual_tree=actual_tree,
        week_loader=adapter.load_week,
        preflight=preflight,
        synthetic=False,
        approval=None,
    )
    return projection


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical_json(value))
        handle.flush()
        os.fsync(handle.fileno())


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "ACCEPTED_REFERENCE_SHA",
    "ACCEPTED_REFERENCE_TREE",
    "MAX_WALL_SECONDS",
    "OFFLINE_GRANT_KIND",
    "OFFLINE_MANIFEST_SCHEMA",
    "OWNER_DIRECTIVE_COMMENT_ID",
    "OWNER_DIRECTIVE_URL",
    "OfflineResearchManifest",
    "OwnerWaivedGridStudyGrant",
    "build_manifest",
    "grant_from_manifest",
    "run_offline_study",
    "write_new",
]
