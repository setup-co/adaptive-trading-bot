import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_REPO = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-220-execution").resolve(strict=True)
RUN_ROOT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run").resolve(strict=True)
PYTHON = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe").resolve(strict=True)
BOOTSTRAP = (RUN_ROOT / "bootstrap_grant_attempt_02.py").resolve(strict=True)
MATERIALIZER = (RUN_ROOT / "materialize_grant.py").resolve(strict=True)
OLD_GRANT = (RUN_ROOT / "grant.json").resolve(strict=True)
NEW_GRANT = RUN_ROOT / "grant.canonical.attempt-02.json"
LOGS = RUN_ROOT / "logs"
STDOUT = LOGS / "grant-materialize-attempt-02.stdout.log"
STDERR = LOGS / "grant-materialize-attempt-02.stderr.log"
START = LOGS / "grant-materialize-attempt-02-start.json"
EXIT = LOGS / "grant-materialize-attempt-02-exit.json"

for path in (NEW_GRANT, STDOUT, STDERR, START, EXIT):
    if path.exists():
        raise SystemExit(f"refusing existing attempt-02 path: {path}")

child_env = os.environ.copy()
child_env["PYTHONPATH"] = os.pathsep.join((str(RUN_REPO / "src"), str(RUN_REPO)))
command = [
    str(PYTHON),
    str(BOOTSTRAP),
    str(RUN_REPO),
    str(MATERIALIZER),
    str(OLD_GRANT),
    str(NEW_GRANT),
]
started = datetime.now(timezone.utc)
START.write_text(
    json.dumps(
        {
            "started_at_utc": started.isoformat(),
            "interpreter": str(PYTHON),
            "cwd": str(RUN_REPO),
            "explicit_import_paths": [str(RUN_REPO / "src"), str(RUN_REPO)],
            "command": command,
        },
        sort_keys=True,
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
with STDOUT.open("xb") as stdout_handle, STDERR.open("xb") as stderr_handle:
    completed = subprocess.run(
        command,
        cwd=str(RUN_REPO),
        env=child_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        check=False,
    )
ended = datetime.now(timezone.utc)
EXIT.write_text(
    json.dumps(
        {
            "started_at_utc": started.isoformat(),
            "ended_at_utc": ended.isoformat(),
            "exit_code": completed.returncode,
            "stdout": str(STDOUT),
            "stderr": str(STDERR),
        },
        sort_keys=True,
        separators=(",", ":"),
    ),
    encoding="utf-8",
)
raise SystemExit(completed.returncode)
