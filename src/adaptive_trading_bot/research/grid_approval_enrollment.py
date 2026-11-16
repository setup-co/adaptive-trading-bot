"""Offline owner-operated Ed25519 key enrollment preparation.

This module never pins trust, creates a Grid execution grant, or authorizes a
historical run.  The private-key generation command is intended to be invoked
only by the owner on an offline owner-controlled machine.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Self

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adaptive_trading_bot.research.grid_history_preflight import canonical_json

PUBLIC_INFO_SCHEMA = "grid-history-ed25519-public-info-v1"
MINIMUM_PASSPHRASE_CHARACTERS = 16


class GridApprovalEnrollmentError(RuntimeError):
    """Owner-key enrollment input or output is unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class GridApprovalPublicInfo:
    schema: str
    algorithm: str
    public_key_hex: str
    key_id_sha256: str

    @classmethod
    def from_public_key_hex(cls, public_key_hex: str) -> Self:
        if public_key_hex != public_key_hex.lower():
            raise GridApprovalEnrollmentError("public key must use lowercase hexadecimal")
        try:
            raw = bytes.fromhex(public_key_hex)
        except ValueError as exc:
            raise GridApprovalEnrollmentError("public key encoding is invalid") from exc
        if len(public_key_hex) != 64 or len(raw) != 32:
            raise GridApprovalEnrollmentError("public key must contain exactly 32 raw bytes")
        return cls(
            schema=PUBLIC_INFO_SCHEMA,
            algorithm="Ed25519",
            public_key_hex=public_key_hex,
            key_id_sha256=hashlib.sha256(raw).hexdigest(),
        )

    @classmethod
    def from_file(cls, path: Path) -> Self:
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise GridApprovalEnrollmentError("public-info file is unavailable or invalid") from exc
        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if not isinstance(value, dict) or set(value) != expected or canonical_json(value) != raw:
            raise GridApprovalEnrollmentError("public-info file must be exact canonical JSON")
        if value.get("schema") != PUBLIC_INFO_SCHEMA or value.get("algorithm") != "Ed25519":
            raise GridApprovalEnrollmentError("public-info identity differs")
        info = cls.from_public_key_hex(str(value.get("public_key_hex")))
        if info.key_id_sha256 != value.get("key_id_sha256"):
            raise GridApprovalEnrollmentError("public-info fingerprint differs")
        return info

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json(asdict(self))


PrivateKeyFactory = Callable[[], Ed25519PrivateKey]
PassphraseReader = Callable[[str], str]


def generate_owner_key_files(
    *,
    repository_root: Path,
    private_key_path: Path,
    public_info_path: Path,
    passphrase: str,
    confirmed_passphrase: str,
    key_factory: PrivateKeyFactory = Ed25519PrivateKey.generate,
) -> GridApprovalPublicInfo:
    """Write one encrypted private key outside Git and its public fingerprint record."""

    repository_root = _verified_repository_root(repository_root)
    private_key_path = _resolved_new_path(private_key_path)
    public_info_path = _resolved_new_path(public_info_path)
    if _is_within(private_key_path, repository_root):
        raise GridApprovalEnrollmentError("private key path must be outside the repository")
    if private_key_path == public_info_path:
        raise GridApprovalEnrollmentError("private key and public-info paths must differ")
    if private_key_path.exists() or public_info_path.exists():
        raise GridApprovalEnrollmentError("refusing to overwrite an enrollment file")
    if passphrase != confirmed_passphrase:
        raise GridApprovalEnrollmentError("passphrase confirmation differs")
    if len(passphrase) < MINIMUM_PASSPHRASE_CHARACTERS:
        raise GridApprovalEnrollmentError(
            f"passphrase must contain at least {MINIMUM_PASSPHRASE_CHARACTERS} characters"
        )

    private_key = key_factory()
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_info = GridApprovalPublicInfo.from_public_key_hex(public_raw.hex())
    encrypted_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
    )
    _write_new_private(private_key_path, encrypted_private)
    _write_new(public_info_path, public_info.canonical_bytes)
    return public_info


def verify_public_info(*, public_key_hex: str, expected_key_id: str) -> GridApprovalPublicInfo:
    """Recompute and compare the public fingerprint without private-key access."""

    if (
        expected_key_id != expected_key_id.lower()
        or len(expected_key_id) != 64
        or any(character not in "0123456789abcdef" for character in expected_key_id)
    ):
        raise GridApprovalEnrollmentError("expected key ID must be 64 lowercase hexadecimal digits")
    info = GridApprovalPublicInfo.from_public_key_hex(public_key_hex)
    if not hmac.compare_digest(info.key_id_sha256, expected_key_id):
        raise GridApprovalEnrollmentError("public-key fingerprint does not match")
    return info


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="owner-only offline encrypted key generation")
    generate.add_argument("--repo", required=True, type=Path)
    generate.add_argument("--private-key", required=True, type=Path)
    generate.add_argument("--public-info", required=True, type=Path)

    public_info = commands.add_parser("public-info", help="derive public fingerprint information")
    public_info.add_argument("--public-key-hex", required=True)

    verify = commands.add_parser("verify-public-info", help="verify a supplied public fingerprint")
    verify.add_argument("--public-key-hex", required=True)
    verify.add_argument("--expected-key-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    passphrase_reader: PassphraseReader = getpass.getpass,
    key_factory: PrivateKeyFactory = Ed25519PrivateKey.generate,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            passphrase = passphrase_reader("Private-key encryption passphrase: ")
            confirmation = passphrase_reader("Confirm passphrase: ")
            info = generate_owner_key_files(
                repository_root=args.repo,
                private_key_path=args.private_key,
                public_info_path=args.public_info,
                passphrase=passphrase,
                confirmed_passphrase=confirmation,
                key_factory=key_factory,
            )
            print(info.canonical_bytes.decode("utf-8"))
        elif args.command == "public-info":
            info = GridApprovalPublicInfo.from_public_key_hex(args.public_key_hex)
            print(info.canonical_bytes.decode("utf-8"))
        else:
            info = verify_public_info(
                public_key_hex=args.public_key_hex,
                expected_key_id=args.expected_key_id,
            )
            print(
                canonical_json(
                    {
                        "key_id_sha256": info.key_id_sha256,
                        "status": "PUBLIC_FINGERPRINT_VERIFIED",
                    }
                ).decode("utf-8")
            )
    except GridApprovalEnrollmentError as exc:
        parser.exit(2, f"grid approval enrollment failed: {exc}\n")
    return 0


def _resolved_new_path(path: Path) -> Path:
    try:
        parent = path.expanduser().parent.resolve(strict=True)
    except OSError as exc:
        raise GridApprovalEnrollmentError("enrollment output parent does not exist") from exc
    if not parent.is_dir():
        raise GridApprovalEnrollmentError("enrollment output parent is not a directory")
    return parent / path.name


def _verified_repository_root(path: Path) -> Path:
    root = path.resolve(strict=True)
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GridApprovalEnrollmentError("named repository root is not a Git checkout") from exc
    if Path(completed.stdout.strip()).resolve(strict=True) != root:
        raise GridApprovalEnrollmentError("named repository path is not the checkout root")
    return root


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _write_new_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    if os.name != "nt":
        path.chmod(0o600)


def _write_new(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


if __name__ == "__main__":  # pragma: no cover - exercised through main() tests
    raise SystemExit(main())


__all__ = [
    "MINIMUM_PASSPHRASE_CHARACTERS",
    "PUBLIC_INFO_SCHEMA",
    "GridApprovalEnrollmentError",
    "GridApprovalPublicInfo",
    "generate_owner_key_files",
    "main",
    "verify_public_info",
]
