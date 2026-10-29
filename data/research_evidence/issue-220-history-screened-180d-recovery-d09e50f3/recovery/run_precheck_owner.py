import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-220-execution").resolve(strict=True)
PYTHON = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe")
LEDGER = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\ledgers\ec10ca5d-01e6-436f-8153-fe9c5b5439b5")
GRANT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run\grant.canonical.attempt-02.json")
RUN_ROOT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run")
VALIDATOR = ROOT / "validate_stopped_state.py"

child_env = os.environ.copy()
child_env["PYTHONPATH"] = os.pathsep.join((str(REPO / "src"), str(REPO)))

def atomic(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)

def run_child(name: str, command: list[str]) -> int:
    stdout = ROOT / f"{name}.stdout.log"
    stderr = ROOT / f"{name}.stderr.log"
    exit_record = ROOT / f"{name}.exit.json"
    if any(path.exists() for path in (stdout, stderr, exit_record)):
        raise SystemExit("precheck owner refuses existing child record: " + name)
    started = datetime.now(timezone.utc)
    with stdout.open("xb") as out, stderr.open("xb") as err:
        completed = subprocess.run(
            command,
            cwd=str(REPO),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            check=False,
        )
    atomic(
        exit_record,
        {
            "command": command,
            "started_at_utc": started.isoformat(),
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "exit_code": completed.returncode,
        },
    )
    return completed.returncode

if run_child("owner-probe-zero", [str(PYTHON), "-c", "print('OWNER_EXIT_ZERO');raise SystemExit(0)"]) != 0:
    raise SystemExit("zero-exit owner probe failed")
if run_child("owner-probe-nonzero", [str(PYTHON), "-c", "print('OWNER_EXIT_NONZERO');raise SystemExit(17)"]) != 17:
    raise SystemExit("nonzero-exit owner probe failed")
code = run_child(
    "stopped-state-validation",
    [str(PYTHON), str(VALIDATOR), str(REPO), str(LEDGER), str(GRANT), str(RUN_ROOT), str(ROOT / "original-inventory.json")],
)
raise SystemExit(code)
