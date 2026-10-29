import hashlib
import json
import os
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: build_publication_index.py BUNDLE_ROOT")
root = Path(sys.argv[1]).resolve(strict=True)
index_path = root / "artifact-index.json"
manifest_path = root / "manifest.json"
if index_path.exists() or manifest_path.exists():
    raise SystemExit("publication index or manifest already exists")

def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

files = []
for path in sorted(root.rglob("*")):
    if path.is_file() and path not in {index_path, manifest_path}:
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
        )
index = {
    "schema": "issue-220-recovery-artifact-index-v1",
    "file_count": len(files),
    "total_bytes": sum(item["size"] for item in files),
    "files": files,
}

def write(path: Path, value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

write(index_path, index)
status = root / "recovery" / "recovery-status-attempt-03.json"
inventory = root / "recovery" / "original-inventory.json"
revision = root / "original-ledger" / "0020.json"
manifest = {
    "schema": "issue-220-recovery-publication-v1",
    "issue": 220,
    "frozen_executable_sha": "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e",
    "run_uuid": "ec10ca5d-01e6-436f-8153-fe9c5b5439b5",
    "historical_invocations": 2,
    "original_attempt_01_exit": 1,
    "original_attempt_02_exit": "UNKNOWN_NOT_RECORDED",
    "original_ledger_revision": 20,
    "original_completed_tasks": 10,
    "original_finalized": False,
    "recovery_status": "RECOVERY_BLOCKED_RESOURCE_FEASIBILITY",
    "aggregate_classification": "AGGREGATE_CLASSIFICATION_UNAVAILABLE_UNVERIFIED",
    "recovery_construction_invocations": 0,
    "recovery_verification_invocations": 0,
    "revision_20_sha256": digest(revision),
    "original_inventory_sha256": digest(inventory),
    "recovery_status_sha256": digest(status),
    "artifact_index": {
        "size": index_path.stat().st_size,
        "sha256": digest(index_path),
        "file_count": index["file_count"],
        "total_bytes": index["total_bytes"],
    },
}
write(manifest_path, manifest)
print(json.dumps(manifest, sort_keys=True))
