import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: record_resource_blocker_attempt_02.py RECOVERY_ROOT")
root = Path(sys.argv[1]).resolve(strict=True)
run_root = root.parent.resolve(strict=True)
inventory_path = root / "original-inventory.json"
diagnostics_path = root / "host-diagnostics-attempt-02.json"
inventory = json.loads(inventory_path.read_bytes())
diagnostics = json.loads(diagnostics_path.read_bytes())

def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

observed = []
for expected in inventory["inventory"]:
    path = run_root / expected["path"]
    actual = {"path": expected["path"], "size": path.stat().st_size, "sha256": digest(path)}
    if actual != expected:
        raise SystemExit("original evidence changed: " + expected["path"])
    observed.append(actual)
current_paths = {
    path.relative_to(run_root).as_posix()
    for path in run_root.rglob("*")
    if path.is_file() and root not in path.parents
}
if current_paths != {item["path"] for item in inventory["inventory"]}:
    raise SystemExit("original evidence inventory gained or lost a path")

memory = diagnostics["memory"]
commit_headroom = memory["commit_limit_bytes"] - memory["committed_bytes"]
results_isize = diagnostics["results_uncompressed_isize_total"]
if diagnostics["gzip_file_count"] != 20 or results_isize <= commit_headroom:
    raise SystemExit("resource blocker is not supported by corrected measurement")

def write_exclusive(path: Path, value: object) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

preservation = {
    "status": "ORIGINAL_EVIDENCE_UNCHANGED_AFTER_RECOVERY_ASSESSMENT",
    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    "source_inventory_sha256": digest(inventory_path),
    "files": observed,
}
write_exclusive(root / "preservation-after-attempt-02.json", preservation)
status = {
    "schema": "issue-220-offline-recovery-status-v1",
    "status": "RECOVERY_BLOCKED_RESOURCE_FEASIBILITY",
    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
    "historical_invocations": 2,
    "original_attempt_01_exit": 1,
    "original_attempt_02_exit": "UNKNOWN_NOT_RECORDED",
    "original_ledger": {
        "revision": 20,
        "completed_tasks": 10,
        "failed_tasks": 0,
        "finalized": False,
        "projection_present": False,
        "revision_20_sha256": inventory["revision_20_sha256"],
    },
    "termination_cause": diagnostics["termination_cause"],
    "recovery_construction": {"status": "NOT_STARTED_RESOURCE_BLOCKER", "command_invocations": 0},
    "recovery_verification": {"status": "NOT_STARTED", "command_invocations": 0},
    "resource_blocker": {
        "gzip_file_count": diagnostics["gzip_file_count"],
        "gzip_compressed_total": diagnostics["gzip_compressed_total"],
        "gzip_uncompressed_isize_total": diagnostics["gzip_uncompressed_isize_total"],
        "results_uncompressed_isize_total": results_isize,
        "free_physical_bytes": memory["free_physical_bytes"],
        "available_bytes": memory["available_bytes"],
        "commit_headroom_bytes": commit_headroom,
        "reason": "frozen final_projection retains decoded results for all tasks; raw retained result JSON alone exceeds current commit headroom before Python object expansion",
    },
    "classification": "AGGREGATE_CLASSIFICATION_UNAVAILABLE_UNVERIFIED",
    "original_inventory_sha256": digest(inventory_path),
    "preservation_after_sha256": digest(root / "preservation-after-attempt-02.json"),
    "host_diagnostics_sha256": digest(diagnostics_path),
    "superseded_diagnostics_sha256": digest(root / "host-diagnostics.json"),
    "failed_blocker_attempt_exit_sha256": digest(root / "blocker-recorder.exit.json"),
}
write_exclusive(root / "recovery-status-attempt-02.json", status)
print(json.dumps({"status": status["status"], "commit_headroom_bytes": commit_headroom, "results_uncompressed_isize_total": results_isize}, sort_keys=True))
