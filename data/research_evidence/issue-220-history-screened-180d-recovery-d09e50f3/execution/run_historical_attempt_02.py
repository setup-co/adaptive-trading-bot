import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RUN_REPO = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-220-execution").resolve(strict=True)
RUN_ROOT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-220-run").resolve(strict=True)
PYTHON = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe").resolve(strict=True)
GRANT = (RUN_ROOT / "grant.canonical.attempt-02.json").resolve(strict=True)
OLD_GRANT = (RUN_ROOT / "grant.json").resolve(strict=True)
LEDGER_ROOT = (RUN_ROOT / "ledgers").resolve(strict=True)
UUID = "ec10ca5d-01e6-436f-8153-fe9c5b5439b5"
LOGS = RUN_ROOT / "logs"
START = LOGS / "run-attempt-02-start.json"
INVOKED = LOGS / "run-attempt-02-command-invoked.json"
EXIT = LOGS / "run-attempt-02-exit.json"
STDOUT = LOGS / "run-attempt-02.stdout.log"
STDERR = LOGS / "run-attempt-02.stderr.log"

for path in (START, INVOKED, EXIT, STDOUT, STDERR, LEDGER_ROOT / UUID):
    if path.exists():
        raise SystemExit(f"attempt-02 guard refused existing path: {path}")
if len(GRANT.read_bytes()) != 540 or hashlib.sha256(GRANT.read_bytes()).hexdigest() != "f674cb1dffdd87801f51f2eba27ecebc8130497ea0c92f5ae8a0526659f4963d":
    raise SystemExit("canonical grant identity changed")
if len(OLD_GRANT.read_bytes()) != 541 or hashlib.sha256(OLD_GRANT.read_bytes()).hexdigest() != "13b8026d682b349ea37413d7addf2820199718034a2a4251badbbb5bed4fb300":
    raise SystemExit("preserved attempt-01 grant identity changed")

child_env = os.environ.copy()
child_env["PYTHONPATH"] = os.pathsep.join((str(RUN_REPO / "src"), str(RUN_REPO)))
command = [
    str(PYTHON),
    "-m",
    "scripts.research.breakout_history_screened_comparison",
    "--repo",
    str(RUN_REPO),
    "run",
    "--grant",
    str(GRANT),
    "--ledger-root",
    str(LEDGER_ROOT),
]
started = datetime.now(timezone.utc)

def write_atomic(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)

write_atomic(
    START,
    {
        "wrapper_pid": os.getpid(),
        "started_at_utc": started.isoformat(),
        "frozen_executable_sha": "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e",
        "run_uuid": UUID,
        "python": str(PYTHON),
        "checkout": str(RUN_REPO),
        "explicit_import_paths": [str(RUN_REPO / "src"), str(RUN_REPO)],
    },
)
write_atomic(
    INVOKED,
    {
        "historical_invocation": 2,
        "cumulative_historical_invocations": 2,
        "invoked_at_utc": datetime.now(timezone.utc).isoformat(),
        "wrapper_pid": os.getpid(),
        "command": command,
    },
)
exit_code = 255
try:
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
    exit_code = completed.returncode
except BaseException as exc:
    with STDERR.open("ab") as stderr_handle:
        stderr_handle.write((repr(exc) + "\n").encode("utf-8"))
    exit_code = 254
finally:
    write_atomic(
        EXIT,
        {
            "started_at_utc": started.isoformat(),
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "wrapper_pid": os.getpid(),
            "run_uuid": UUID,
        },
    )
raise SystemExit(exit_code)
