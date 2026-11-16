from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from adaptive_trading_bot.research.grid_history_offline import (
    ACCEPTED_REFERENCE_SHA,
    ACCEPTED_REFERENCE_TREE,
    MAX_WALL_SECONDS,
    OFFLINE_GRANT_KIND,
    OFFLINE_MANIFEST_SCHEMA,
    OWNER_DIRECTIVE_COMMENT_ID,
    OWNER_DIRECTIVE_URL,
    OfflineResearchManifest,
    grant_from_manifest,
)
from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    STUDY_PLAN_SHA256,
)
from adaptive_trading_bot.research.grid_history_runner import (
    ACCEPTED_SOURCE_ID,
    EXTERNAL_GRANT_KIND,
    TASK_IDS,
    GridHistoryRunnerError,
    GridStudyGrant,
)
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
)


@pytest.fixture
def offline_manifest() -> OfflineResearchManifest:
    return OfflineResearchManifest(
        OFFLINE_MANIFEST_SCHEMA,
        "OWNER_WAIVED_SIGNATURE_OFFLINE_RESEARCH_ONLY",
        OWNER_DIRECTIVE_COMMENT_ID,
        OWNER_DIRECTIVE_URL,
        "e9c2a31a-7af9-4cc7-b49f-6922d47c39e9",
        ACCEPTED_REFERENCE_SHA,
        ACCEPTED_REFERENCE_TREE,
        "1" * 40,
        "2" * 40,
        "3" * 64,
        "4" * 64,
        "5" * 64,
        "6" * 64,
        "7" * 64,
        STUDY_PLAN_SHA256,
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
        "C:/retained-v3",
        "C:/external-ledger",
    )


def test_owner_waiver_is_isolated_from_signed_production_grant(
    offline_manifest: OfflineResearchManifest,
) -> None:
    grant = grant_from_manifest(offline_manifest)
    assert grant.kind == OFFLINE_GRANT_KIND
    grant.validate(
        actual_sha=offline_manifest.executable_sha,
        actual_tree=offline_manifest.executable_tree,
        synthetic=False,
        approval=None,
    )

    signed_path_grant = GridStudyGrant(**{**asdict(grant), "kind": EXTERNAL_GRANT_KIND})
    with pytest.raises(GridHistoryRunnerError, match="approval is absent"):
        signed_path_grant.validate(
            actual_sha=offline_manifest.executable_sha,
            actual_tree=offline_manifest.executable_tree,
            synthetic=False,
            approval=None,
        )


def test_owner_waiver_rejects_changed_identity(
    offline_manifest: OfflineResearchManifest,
) -> None:
    grant = grant_from_manifest(offline_manifest)
    for changed in (
        replace(grant, manifest_sha256="0" * 64),
        replace(grant, task_ids=("BTCUSDT:BASE",)),
        replace(grant, max_wall_seconds=MAX_WALL_SECONDS + 1),
        replace(grant, network_requests=1),
    ):
        with pytest.raises(GridHistoryRunnerError, match="identity differs"):
            changed.validate(
                actual_sha=offline_manifest.executable_sha,
                actual_tree=offline_manifest.executable_tree,
                synthetic=False,
                approval=None,
            )
