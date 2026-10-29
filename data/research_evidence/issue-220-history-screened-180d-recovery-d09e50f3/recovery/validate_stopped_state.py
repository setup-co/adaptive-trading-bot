import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

if len(sys.argv) != 6:
    raise SystemExit("usage: validate_stopped_state.py REPO LEDGER GRANT RUN_ROOT OUTPUT")

repo = Path(sys.argv[1]).resolve(strict=True)
ledger_directory = Path(sys.argv[2]).resolve(strict=True)
grant_path = Path(sys.argv[3]).resolve(strict=True)
run_root = Path(sys.argv[4]).resolve(strict=True)
output = Path(sys.argv[5]).resolve()
package_root = repo / "src" / "adaptive_trading_bot"
sys.path[:0] = [str(repo / "src"), str(repo)]
os.chdir(repo)

comparison = importlib.import_module(
    "adaptive_trading_bot.research.breakout_history_screened_comparison"
)
execution = importlib.import_module(
    "adaptive_trading_bot.research.breakout_history_screened_execution"
)
ledger_module = importlib.import_module(
    "adaptive_trading_bot.research.breakout_history_screened_ledger"
)
for name, module in tuple(sys.modules.items()):
    if name == "adaptive_trading_bot" or name.startswith("adaptive_trading_bot."):
        origin = getattr(module, "__file__", None)
        paths = list(getattr(module, "__path__", ()))
        if origin is not None and not Path(origin).resolve().is_relative_to(package_root):
            raise SystemExit("mixed project origin: " + name)
        if any(not Path(item).resolve().is_relative_to(package_root) for item in paths):
            raise SystemExit("mixed project package path: " + name)

expected_sha = "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e"
expected_uuid = "ec10ca5d-01e6-436f-8153-fe9c5b5439b5"
grant_bytes = grant_path.read_bytes()
if len(grant_bytes) != 540 or hashlib.sha256(grant_bytes).hexdigest() != (
    "f674cb1dffdd87801f51f2eba27ecebc8130497ea0c92f5ae8a0526659f4963d"
):
    raise SystemExit("canonical grant identity changed")
grant = comparison.HistoryScreenedExecutionGrant.from_file(grant_path)
grant.validate(actual_sha=expected_sha)
state = ledger_module.HistoryScreenedLedgerStore(ledger_directory).load()
if (
    state.run_uuid != expected_uuid
    or state.executable_sha != expected_sha
    or state.input_kind != "EXTERNAL_REVIEWED_EXECUTION_GRANT"
    or state.revision != 20
    or state.task_ids != tuple(task.task_id for task in comparison.TASKS)
    or state.task_states != ("COMPLETED",) * 10
    or state.projection_sha256 is not None
):
    raise SystemExit("original revision-20 state differs from recovery authority")
association = execution.HistoryScreenedRunAssociation(
    state.input_kind,
    state.run_uuid,
    state.executable_sha,
    state.protocol_sha256,
    state.task_plan_sha256,
    state.source_id,
    state.dataset_version,
    state.manifest_sha256,
    state.input_report_sha256,
    state.input_plan_sha256,
)
association.validate(comparison.ACCEPTED_BINDING, actual_sha=expected_sha)

event_names = tuple(f"{index:04d}.json" for index in range(21))
artifact_names = tuple(
    f"{index:02d}.{suffix}"
    for index in range(10)
    for suffix in ("population.json.gz", "results.jsonl.gz", "report.json")
)
if tuple(sorted(path.name for path in ledger_directory.glob("[0-9][0-9][0-9][0-9].json"))) != event_names:
    raise SystemExit("original event inventory differs")
if tuple(sorted(path.name for path in ledger_directory.iterdir() if path.name in artifact_names)) != tuple(sorted(artifact_names)):
    raise SystemExit("original task artifact inventory differs")
if (ledger_directory / "projection.json").exists():
    raise SystemExit("original projection unexpectedly exists")
if (run_root / "logs" / "run-attempt-02-exit.json").exists():
    raise SystemExit("original attempt-02 exit unexpectedly exists")

def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

files = []
for path in sorted(run_root.rglob("*")):
    if not path.is_file() or (run_root / "recovery-01") in path.parents:
        continue
    files.append(
        {
            "path": path.relative_to(run_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": digest(path),
        }
    )
revision_20 = ledger_directory / "0020.json"
record = {
    "status": "ORIGINAL_REVISION_20_VALIDATED",
    "python": sys.executable,
    "cwd": str(Path.cwd()),
    "module_origins": {
        "comparison": comparison.__file__,
        "execution": execution.__file__,
        "ledger": ledger_module.__file__,
    },
    "run": association.record(),
    "ledger_revision": state.revision,
    "task_states": list(state.task_states),
    "projection_sha256": state.projection_sha256,
    "revision_20_sha256": digest(revision_20),
    "original_attempt_02_exit": "UNKNOWN_NOT_RECORDED",
    "inventory": files,
}
payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
with output.open("xb") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
if output.read_bytes() != payload:
    raise SystemExit("validation record read-back changed")
print(json.dumps({"status": record["status"], "files": len(files), "revision_20_sha256": record["revision_20_sha256"]}, sort_keys=True))
