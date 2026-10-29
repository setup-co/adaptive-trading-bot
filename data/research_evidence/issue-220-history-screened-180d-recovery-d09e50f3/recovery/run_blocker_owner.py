import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-220-execution").resolve(strict=True)
PYTHON = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-216-ci-env\Scripts\python.exe")
SCRIPT = ROOT / "record_resource_blocker.py"
STDOUT = ROOT / "blocker-recorder.stdout.log"
STDERR = ROOT / "blocker-recorder.stderr.log"
EXIT = ROOT / "blocker-recorder.exit.json"
if any(path.exists() for path in (STDOUT, STDERR, EXIT, ROOT / "recovery-status.json", ROOT / "preservation-after.json")):
    raise SystemExit("blocker owner refuses existing records")
env = os.environ.copy()
env["PYTHONPATH"] = os.pathsep.join((str(REPO / "src"), str(REPO)))
command = [str(PYTHON), str(SCRIPT), str(ROOT)]
started = datetime.now(timezone.utc)
with STDOUT.open("xb") as out, STDERR.open("xb") as err:
    completed = subprocess.run(command, cwd=str(REPO), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, check=False)
payload = json.dumps(
    {
        "command": command,
        "started_at_utc": started.isoformat(),
        "ended_at_utc": datetime.now(timezone.utc).isoformat(),
        "exit_code": completed.returncode,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode()
with EXIT.open("xb") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
raise SystemExit(completed.returncode)
