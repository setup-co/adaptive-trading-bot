"""Issue #179 C1 source-frozen, separately versioned public-data continuation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_continuation as continuation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "capture", "build", "verify"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = repo / continuation.ROOT
    if args.command == "plan":
        if root.exists():
            raise c.CorpusError("continuation root already exists")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        plan = continuation.make_plan(repo, revision)
        c.durable_new(root / "plan.json", c.canonical(plan))
        print(
            {
                "plan_sha256": c.sha((root / "plan.json").read_bytes()),
                "pending_queries": len(plan["requests"]),
            }
        )
        return 0
    plan = c.read_json(root / "plan.json")
    continuation.validate_plan(repo, root, plan)
    if args.command == "capture":
        print(continuation.capture(repo, root, plan))
    elif args.command == "build":
        print(continuation.build(repo, root, plan))
    else:
        result = continuation.verify(repo, root)
        print(
            {
                "manifest_sha256": result["manifest_sha256"],
                "status": result["status"],
                "ledger": result["ledger"],
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
