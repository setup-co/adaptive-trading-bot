from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adaptive_trading_bot.research.grid_approval_enrollment import (
    PUBLIC_INFO_SCHEMA,
    GridApprovalEnrollmentError,
    GridApprovalPublicInfo,
    generate_owner_key_files,
    main,
    verify_public_info,
)
from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_DATASET_VERSION,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_V3_MANIFEST_SHA256,
    ACCEPTED_V3_REPORT_SHA256,
    STUDY_ID,
    STUDY_PLAN_SHA256,
    canonical_json,
)
from adaptive_trading_bot.research.grid_history_runner import (
    ACCEPTED_ENGINE_HEAD,
    ACCEPTED_PREFLIGHT_HEAD,
    ACCEPTED_SOURCE_ID,
    APPROVAL_SCHEMA,
    ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX,
    EXTERNAL_GRANT_KIND,
    RUNNER_SCHEMA,
    TASK_IDS,
    GridGrantApproval,
    GridHistoryRunnerError,
    GridStudyGrant,
)
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ACCEPTED_PROTOCOL_HEAD,
    GRID_ENGINE_VERSION,
    GRID_PARAMETERS_SHA256,
    GRID_PROTOCOL_DOCUMENT_SHA256,
    GRID_PROTOCOL_VERSION,
    GRID_STRATEGY_ID,
)


def _external_grant() -> GridStudyGrant:
    return GridStudyGrant(
        schema=RUNNER_SCHEMA,
        kind=EXTERNAL_GRANT_KIND,
        run_uuid="7c1032f4-da06-4af7-ab80-fdcae86bb154",
        executable_sha="1" * 40,
        executable_tree="2" * 40,
        study_id=STUDY_ID,
        study_plan_sha256=STUDY_PLAN_SHA256,
        preflight_head=ACCEPTED_PREFLIGHT_HEAD,
        engine_head=ACCEPTED_ENGINE_HEAD,
        engine_version=GRID_ENGINE_VERSION,
        protocol_id=GRID_STRATEGY_ID,
        protocol_version=GRID_PROTOCOL_VERSION,
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
        max_wall_seconds=3600,
    )


def _signed_approval(
    grant: GridStudyGrant,
    private_key: Ed25519PrivateKey,
) -> tuple[GridGrantApproval, str]:
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        GridGrantApproval(
            schema=APPROVAL_SCHEMA,
            key_id=hashlib.sha256(public_raw).hexdigest(),
            grant_sha256=grant.sha256,
            signature_ed25519_hex=private_key.sign(canonical_json(asdict(grant))).hex(),
        ),
        public_raw.hex(),
    )


def test_public_key_round_trip_and_independent_fingerprint() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_hex = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )

    info = GridApprovalPublicInfo.from_public_key_hex(public_hex)
    verified = verify_public_info(
        public_key_hex=public_hex,
        expected_key_id=hashlib.sha256(bytes.fromhex(public_hex)).hexdigest(),
    )

    assert info == verified
    assert info.schema == PUBLIC_INFO_SCHEMA
    assert json.loads(info.canonical_bytes) == asdict(info)
    with pytest.raises(GridApprovalEnrollmentError, match="fingerprint does not match"):
        verify_public_info(public_key_hex=public_hex, expected_key_id="0" * 64)


def test_owner_approved_public_key_and_independent_fingerprint_are_pinned() -> None:
    expected_public_key = "500c6dbcafaa57c5ea534d44001714210f29cb7a384428db97d120a8d40872c1"
    expected_key_id = "384cbb9e327433b3ade48f77510cdf90e97e37e2d0461be337a8bfcd34b28ace"

    assert expected_public_key == ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX
    assert len(bytes.fromhex(expected_public_key)) == 32
    assert hashlib.sha256(bytes.fromhex(expected_public_key)).hexdigest() == expected_key_id
    assert (
        verify_public_info(
            public_key_hex=expected_public_key,
            expected_key_id=expected_key_id,
        ).key_id_sha256
        == expected_key_id
    )


def test_temporary_fixture_signature_matches_exact_canonical_grant_bytes() -> None:
    grant = _external_grant()
    private_key = Ed25519PrivateKey.generate()
    approval, public_hex = _signed_approval(grant, private_key)

    approval.verify(grant, trusted_public_key_hex=public_hex)

    with pytest.raises(GridHistoryRunnerError, match="provenance differs"):
        approval.verify(replace(grant, executable_tree="3" * 40), trusted_public_key_hex=public_hex)
    with pytest.raises(GridHistoryRunnerError, match="provenance differs"):
        replace(approval, grant_sha256="4" * 64).verify(grant, trusted_public_key_hex=public_hex)
    with pytest.raises(GridHistoryRunnerError, match="provenance differs"):
        replace(approval, key_id="5" * 64).verify(grant, trusted_public_key_hex=public_hex)
    with pytest.raises(GridHistoryRunnerError, match="provenance differs"):
        replace(approval, signature_ed25519_hex="00").verify(
            grant, trusted_public_key_hex=public_hex
        )
    with pytest.raises(GridHistoryRunnerError, match="encoding is invalid"):
        approval.verify(grant, trusted_public_key_hex="not-hex")
    wrong_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    forged = replace(approval, key_id=hashlib.sha256(wrong_key).hexdigest())
    with pytest.raises(GridHistoryRunnerError, match="signature is invalid"):
        forged.verify(grant, trusted_public_key_hex=wrong_key.hex())


def test_owner_file_generation_rejects_repository_path_and_overwrite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    outside = tmp_path / "owner-offline"
    outside.mkdir()
    private_path = outside / "grid-owner-key.pem"
    public_path = outside / "grid-owner-public.json"
    fixture_key = Ed25519PrivateKey.generate()
    private_raw_hex = fixture_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()

    result = main(
        [
            "generate",
            "--repo",
            str(repository),
            "--private-key",
            str(private_path),
            "--public-info",
            str(public_path),
        ],
        passphrase_reader=lambda _prompt: "test-only-passphrase-32-characters",
        key_factory=lambda: fixture_key,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert private_path.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert private_raw_hex not in captured.out
    assert "PRIVATE KEY" not in captured.out
    assert (
        GridApprovalPublicInfo.from_file(public_path).canonical_bytes + b"\n"
        == captured.out.encode()
    )

    with pytest.raises(GridApprovalEnrollmentError, match="overwrite"):
        generate_owner_key_files(
            repository_root=repository,
            private_key_path=private_path,
            public_info_path=outside / "another-public.json",
            passphrase="test-only-passphrase-32-characters",
            confirmed_passphrase="test-only-passphrase-32-characters",
            key_factory=Ed25519PrivateKey.generate,
        )
    with pytest.raises(GridApprovalEnrollmentError, match="outside the repository"):
        generate_owner_key_files(
            repository_root=repository,
            private_key_path=repository / "forbidden.pem",
            public_info_path=outside / "unused-public.json",
            passphrase="test-only-passphrase-32-characters",
            confirmed_passphrase="test-only-passphrase-32-characters",
            key_factory=Ed25519PrivateKey.generate,
        )
    assert not (repository / "forbidden.pem").exists()


@pytest.mark.parametrize(
    "public_key_hex",
    ("AA" * 32, "00" * 31, "00" * 33, "zz" * 32),
)
def test_public_info_rejects_noncanonical_or_malformed_keys(public_key_hex: str) -> None:
    with pytest.raises(GridApprovalEnrollmentError):
        GridApprovalPublicInfo.from_public_key_hex(public_key_hex)


def test_fixture_key_cannot_enable_real_history_with_owner_pin() -> None:
    assert ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX is not None
    grant = _external_grant()
    approval, _public_hex = _signed_approval(grant, Ed25519PrivateKey.generate())

    with pytest.raises(GridHistoryRunnerError, match="approval provenance differs"):
        grant.validate(
            actual_sha=grant.executable_sha,
            actual_tree=grant.executable_tree,
            synthetic=False,
            approval=approval,
        )


def test_public_info_cli_never_requires_private_key(capsys: pytest.CaptureFixture[str]) -> None:
    public_hex = "ab" * 32
    expected = hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()

    assert main(["public-info", "--public-key-hex", public_hex]) == 0
    info_output = json.loads(capsys.readouterr().out)
    assert info_output["key_id_sha256"] == expected

    assert (
        main(
            [
                "verify-public-info",
                "--public-key-hex",
                public_hex,
                "--expected-key-id",
                expected,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "key_id_sha256": expected,
        "status": "PUBLIC_FINGERPRINT_VERIFIED",
    }
