"""Plan, capture, qualify, and verify Issue #239's Binance USD-M corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import binance_grid_history as corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "preflight", "capture", "qualify", "verify", "qualify-v2", "verify-v2"),
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = args.root.resolve()
    if args.command == "plan":
        if root.exists():
            raise corpus.CorpusError("dataset root already exists")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        plan = corpus.make_plan(revision)
        corpus.durable_new(root / "plan.json", corpus.canonical(plan))
        print(
            json.dumps(
                {
                    "resources": len(plan["resources"]),
                    "plan_sha256": corpus.sha256_file(root / "plan.json"),
                }
            )
        )
        return 0
    plan = corpus.read_json(root / "plan.json")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if args.command in {"capture", "qualify", "verify", "preflight"}:
        corpus.validate_plan(plan, revision)
    else:
        corpus.validate_plan(plan)
    if args.command == "preflight":
        usage = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", f"(Get-PSDrive -Name '{root.drive[0]}').Free"],
            text=True,
        ).strip()
        print(
            json.dumps(
                {"status": "READY", "free_bytes": int(usage), "resources": len(plan["resources"])}
            )
        )
    elif args.command == "capture":
        print(json.dumps(corpus.capture(root, plan), sort_keys=True))
    elif args.command in {"qualify", "qualify-v2"}:
        result = corpus.qualify(
            root,
            plan,
            generation="v2" if args.command == "qualify-v2" else "v1",
            qualification_executable_sha=revision,
        )
        print(
            json.dumps({"classification": result["classification"], "files": len(result["files"])})
        )
    else:
        print(
            json.dumps(
                corpus.verify(root, plan, generation="v2" if args.command == "verify-v2" else "v1"),
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
