"""Frozen Issue #181 C2 tail plan, bounded public capture and offline verification."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_tail as tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "capture", "build", "verify"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = repo / tail.ROOT
    if args.command == "plan":
        if root.exists():
            raise c.CorpusError("C2 root already exists")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        plan = tail.make_plan(repo, revision)
        c.durable_new(root / "plan.json", c.canonical(plan))
        print({"plan_sha256": c.sha((root / "plan.json").read_bytes()), "pending": 785})
        return 0
    plan = c.read_json(root / "plan.json")
    tail.validate_plan(repo, root, plan)
    if args.command == "capture":
        print(tail.capture(repo, root, plan))
    elif args.command == "build":
        print(tail.build(repo, root, plan))
    else:
        checked = tail.verify(repo, root)
        print(
            {
                "manifest_sha256": checked["manifest_sha256"],
                "status": checked["status"],
                "ledger": checked["ledger"],
                "cumulative": checked["cumulative"],
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
