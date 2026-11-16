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
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pyarrow.parquet as pq

from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    STUDY_ID,
    STUDY_PLAN_SHA256,
    CalendarStatus,
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

OFFLINE_MANIFEST_SCHEMA = "btc-eth-grid-4h-owner-waived-offline-manifest-v2"
OFFLINE_GRANT_KIND = "OWNER_WAIVED_OFFLINE_RESEARCH_GRID_HISTORY_AMENDMENT_1"
OWNER_DIRECTIVE_COMMENT_ID = 6927588719
OWNER_DIRECTIVE_URL = (
    "https://github.com/setup-co/adaptive-trading-bot/issues/255#issuecomment-6927588719"
)
ACCEPTED_REFERENCE_SHA = "159461e8cbc349d3f58d91d9688f5a0ffb05f7b9"
ACCEPTED_REFERENCE_TREE = "f34aeba0089a4464f771d676cc016f97005946f9"
MAX_WALL_SECONDS = 43_200
SOURCE_COVERAGE_AMENDMENT_LABEL = "SOURCE_COVERAGE_AMENDMENT_1"
SOURCE_COVERAGE_AMENDMENT_SHA256 = (
    "af58e69f806fa436f8a3727096ff5ee9081bc713d8ec7b22c964c45d6118bb63"
)
SOURCE_COVERAGE_REASON = "FIRST_MINUTE_FUNDING_PRECEDING_MARK_MISSING"
AMENDMENT_REVIEW_COMMENT_ID = 6930289415
AMENDMENT_REVIEW_URL = (
    "https://github.com/setup-co/adaptive-trading-bot/pull/256#issuecomment-6930289415"
)
_MINUTE_MS = 60_000


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
    parent_calendar_sha256: str
    calendar_amendment_label: str
    calendar_amendment_sha256: str
    amendment_review_comment_id: int
    amendment_review_url: str
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
            or self.calendar_sha256 != SOURCE_COVERAGE_AMENDMENT_SHA256
            or self.parent_calendar_sha256 != ACCEPTED_CALENDAR_SHA256
            or self.calendar_amendment_label != SOURCE_COVERAGE_AMENDMENT_LABEL
            or self.calendar_amendment_sha256 != SOURCE_COVERAGE_AMENDMENT_SHA256
            or self.amendment_review_comment_id != AMENDMENT_REVIEW_COMMENT_ID
            or self.amendment_review_url != AMENDMENT_REVIEW_URL
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
            or self.calendar_sha256 != SOURCE_COVERAGE_AMENDMENT_SHA256
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
    amendment = repo / "docs/research/btc_eth_grid_4h_v1_source_coverage_amendment_1.json"
    resolved_data = data_root.resolve(strict=True)
    if sha256_file(resolved_data / "qualification-manifest-v3.json") != ACCEPTED_V3_MANIFEST_SHA256:
        raise GridHistoryRunnerError("accepted v3 manifest hash differs")
    if sha256_file(resolved_data / "publication-report-v3.json") != ACCEPTED_V3_REPORT_SHA256:
        raise GridHistoryRunnerError("accepted v3 report hash differs")
    if sha256_file(calendar) != ACCEPTED_CALENDAR_SHA256:
        raise GridHistoryRunnerError("accepted calendar hash differs")
    if sha256_file(amendment) != SOURCE_COVERAGE_AMENDMENT_SHA256:
        raise GridHistoryRunnerError("source coverage amendment hash differs")
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
        calendar_sha256=SOURCE_COVERAGE_AMENDMENT_SHA256,
        parent_calendar_sha256=ACCEPTED_CALENDAR_SHA256,
        calendar_amendment_label=SOURCE_COVERAGE_AMENDMENT_LABEL,
        calendar_amendment_sha256=SOURCE_COVERAGE_AMENDMENT_SHA256,
        amendment_review_comment_id=AMENDMENT_REVIEW_COMMENT_ID,
        amendment_review_url=AMENDMENT_REVIEW_URL,
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
        SOURCE_COVERAGE_AMENDMENT_SHA256,
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
    parent_calendar = VerifiedCalendar.accepted(
        repo / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    )
    calendar, coverage = source_coverage_amended_calendar(
        repo=repo,
        data_root=data_root,
        parent=parent_calendar,
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
        "parent_calendar_sha256": parent_calendar.identity,
        "calendar_amendment_label": SOURCE_COVERAGE_AMENDMENT_LABEL,
        "calendar_amendment_sha256": SOURCE_COVERAGE_AMENDMENT_SHA256,
        "source_coverage_requalification": coverage,
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


def source_coverage_amended_calendar(
    *,
    repo: Path,
    data_root: Path,
    parent: VerifiedCalendar,
) -> tuple[VerifiedCalendar, dict[str, object]]:
    """Requalify the accepted calendar using only the uniform MARK authority rule."""

    amendment_path = repo / "docs/research/btc_eth_grid_4h_v1_source_coverage_amendment_1.json"
    if sha256_file(amendment_path) != SOURCE_COVERAGE_AMENDMENT_SHA256:
        raise GridHistoryRunnerError("source coverage amendment hash differs")
    amendment = json.loads(amendment_path.read_bytes())
    if (
        not isinstance(amendment, dict)
        or canonical_json(amendment) != amendment_path.read_bytes()
        or amendment.get("revision_label") != SOURCE_COVERAGE_AMENDMENT_LABEL
        or amendment.get("parent_calendar_sha256") != ACCEPTED_CALENDAR_SHA256
        or amendment.get("v3_manifest_sha256") != ACCEPTED_V3_MANIFEST_SHA256
        or amendment.get("strategy_or_pnl_used") is not False
        or amendment.get("network_requests") != 0
    ):
        raise GridHistoryRunnerError("source coverage amendment identity differs")

    manifest_path = data_root / "qualification-manifest-v3.json"
    if sha256_file(manifest_path) != ACCEPTED_V3_MANIFEST_SHA256:
        raise GridHistoryRunnerError("accepted v3 manifest hash differs")
    manifest = json.loads(manifest_path.read_bytes())
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise GridHistoryRunnerError("accepted v3 file inventory is unavailable")

    verified_files: dict[str, str] = {}

    def verified_table(relative: str, columns: list[str]) -> list[dict[str, object]]:
        authority = files.get(relative)
        if not isinstance(authority, dict):
            raise GridHistoryRunnerError(f"source authority is missing: {relative}")
        expected = authority.get("sha256")
        path = data_root / relative
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise GridHistoryRunnerError(f"source file differs: {relative}")
        verified_files[relative] = expected
        return pq.read_table(path, columns=columns).to_pylist()

    eligible = [row for row in parent.rows if row.status is CalendarStatus.METADATA_ELIGIBLE]
    funding: dict[str, list[dict[str, object]]] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        rows: list[dict[str, object]] = []
        months = sorted(
            {row.episode_start.strftime("%Y-%m") for row in eligible if row.symbol == symbol}
        )
        for month in months:
            relative = f"derived-generations/v3/normalized/funding/{symbol}/{month}.parquet"
            rows.extend(
                verified_table(
                    relative,
                    ["calc_time_ms", "funding_interval_hours", "last_funding_rate"],
                )
            )
        funding[symbol] = rows

    requirements: list[dict[str, object]] = []
    mark_requirements: dict[str, set[int]] = defaultdict(set)
    for row in eligible:
        start_ms = int(row.episode_start.timestamp() * 1000)
        events = sorted(
            (
                item
                for item in funding[row.symbol]
                if start_ms <= _integer(item, "calc_time_ms") < start_ms + _MINUTE_MS
            ),
            key=lambda item: _integer(item, "calc_time_ms"),
        )
        if len(events) != 1:
            raise GridHistoryRunnerError("eligible week first-minute funding cardinality differs")
        event = events[0]
        calc_ms = _integer(event, "calc_time_ms")
        expected_mark_ms = (calc_ms // _MINUTE_MS) * _MINUTE_MS - _MINUTE_MS
        mark_month = datetime.fromtimestamp(expected_mark_ms / 1000, tz=UTC).strftime("%Y-%m")
        relative = f"derived-generations/v3/normalized/mark_1m/{row.symbol}/{mark_month}.parquet"
        mark_requirements[relative].add(expected_mark_ms)
        funding_relative = (
            "derived-generations/v3/normalized/funding/"
            f"{row.symbol}/{row.episode_start.strftime('%Y-%m')}.parquet"
        )
        requirements.append(
            {
                "symbol": row.symbol,
                "episode_start": row.episode_start.isoformat().replace("+00:00", "Z"),
                "funding_calc_time_ms": calc_ms,
                "expected_completed_mark_open_ms": expected_mark_ms,
                "funding_sha256": verified_files[funding_relative],
                "mark_relative_path": relative,
            }
        )

    available: set[tuple[str, int]] = set()
    for relative, expected in sorted(mark_requirements.items()):
        for item in verified_table(relative, ["open_time_ms"]):
            observed = _integer(item, "open_time_ms")
            if observed in expected:
                available.add((relative, observed))

    findings = []
    for item in requirements:
        key = (
            str(item["mark_relative_path"]),
            _integer(item, "expected_completed_mark_open_ms"),
        )
        if key not in available:
            findings.append(
                {
                    **item,
                    "mark_sha256": verified_files[str(item["mark_relative_path"])],
                }
            )
    findings.sort(key=lambda item: (str(item["episode_start"]), str(item["symbol"])))
    expected_findings = amendment.get("findings")
    if findings != expected_findings:
        raise GridHistoryRunnerError("source coverage findings differ from frozen amendment")

    finding_keys = {(str(item["symbol"]), str(item["episode_start"])) for item in findings}
    amended_rows = tuple(
        replace(
            row,
            status=CalendarStatus.METADATA_INELIGIBLE,
            reason_codes=(SOURCE_COVERAGE_REASON,),
        )
        if (row.symbol, row.episode_start.isoformat().replace("+00:00", "Z")) in finding_keys
        else row
        for row in parent.rows
    )
    calendar = VerifiedCalendar.source_coverage_amendment(
        parent,
        amended_rows,
        amendment_sha256=SOURCE_COVERAGE_AMENDMENT_SHA256,
    )
    counts = {
        symbol: {
            "scheduled": len(calendar.rows_for(symbol)),
            "eligible": sum(
                row.status is CalendarStatus.METADATA_ELIGIBLE for row in calendar.rows_for(symbol)
            ),
            "excluded": sum(
                row.status is CalendarStatus.METADATA_INELIGIBLE
                for row in calendar.rows_for(symbol)
            ),
        }
        for symbol in ("BTCUSDT", "ETHUSDT")
    }
    if counts != amendment.get("amended_cohort"):
        raise GridHistoryRunnerError("source coverage amended cohort differs")
    return calendar, {
        "revision_label": SOURCE_COVERAGE_AMENDMENT_LABEL,
        "post_discovery": True,
        "strategy_or_pnl_used": False,
        "findings": findings,
        "counts": counts,
        "verified_source_files": [
            [relative, digest] for relative, digest in sorted(verified_files.items())
        ],
    }


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical_json(value))
        handle.flush()
        os.fsync(handle.fileno())


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def _integer(item: dict[str, object], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GridHistoryRunnerError(f"{key} must be an integer")
    return value


__all__ = [
    "ACCEPTED_REFERENCE_SHA",
    "ACCEPTED_REFERENCE_TREE",
    "MAX_WALL_SECONDS",
    "OFFLINE_GRANT_KIND",
    "OFFLINE_MANIFEST_SCHEMA",
    "OWNER_DIRECTIVE_COMMENT_ID",
    "OWNER_DIRECTIVE_URL",
    "SOURCE_COVERAGE_AMENDMENT_LABEL",
    "SOURCE_COVERAGE_AMENDMENT_SHA256",
    "SOURCE_COVERAGE_REASON",
    "OfflineResearchManifest",
    "OwnerWaivedGridStudyGrant",
    "build_manifest",
    "grant_from_manifest",
    "run_offline_study",
    "source_coverage_amended_calendar",
    "write_new",
]
