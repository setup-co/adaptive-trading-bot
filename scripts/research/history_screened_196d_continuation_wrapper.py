"""Exclusive one-shot process wrapper for the Issue #216 continuation."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Any

from adaptive_trading_bot.research import research_v2_corpus as core


def run_child_once(log_root: Path, command: list[str], identity: dict[str, Any]) -> int:
    log_root.mkdir(parents=True, exist_ok=True)
    core.durable_new(
        log_root / "owner.json",
        core.canonical({**identity, "started_at": core.now(), "command": command}),
    )
    stdout_path, stderr_path = log_root / "stdout.log", log_root / "stderr.log"
    if stdout_path.exists() or stderr_path.exists():
        raise core.CorpusError("successor process logs already exist")
    started_at = core.now()
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        core.durable_new(
            log_root / "process.json",
            core.canonical({"pid": process.pid, "started_at": started_at, "command": command}),
        )
        exit_code = int(process.wait())
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())
    core.durable_new(
        log_root / "exit.json",
        core.canonical(
            {
                "started_at": started_at,
                "ended_at": core.now(),
                "pid": process.pid,
                "exit_code": exit_code,
            }
        ),
    )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--interpreter", type=Path, required=True)
    parser.add_argument("--executable-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--operation", choices=("capture", "build", "verify"), required=True)
    args = parser.parse_args()
    repo, root, interpreter = args.repo.resolve(), args.root.resolve(), args.interpreter.resolve()
    command = [
        str(interpreter),
        "-m",
        "scripts.research.history_screened_196d_continuation",
        args.operation,
        "--repo",
        str(repo),
        "--root",
        str(root),
    ]
    return run_child_once(
        args.log_root.resolve(),
        command,
        {
            "repo": str(repo),
            "root": str(root),
            "interpreter": str(interpreter),
            "executable_sha": args.executable_sha,
            "plan_sha256": args.plan_sha256,
            "operation": args.operation,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
