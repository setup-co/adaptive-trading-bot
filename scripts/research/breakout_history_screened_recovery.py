"""Bounded-memory read-only recovery for retained Issue #220 task evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import breakout_history_screened_recovery as recovery


def _clean_sha(repo: Path) -> str:
    status = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise recovery.BoundedRecoveryError("recovery checkout is not clean")
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("identity")
    for name in ("build", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--evidence-root", type=Path, required=True)
        command.add_argument("--mirror", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "identity":
        result = recovery.source_identity()
    else:
        repo = args.repo.resolve(strict=True)
        actual_sha = _clean_sha(repo)
        function = recovery.build if args.command == "build" else recovery.verify
        result = function(
            args.evidence_root.resolve(strict=True),
            args.mirror.resolve(),
            args.output.resolve(),
            recovery_executable_sha=actual_sha,
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
