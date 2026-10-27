"""Plan, capture, build, and verify the frozen Issue #214 corpus."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import history_screened_196d_corpus as corpus
from adaptive_trading_bot.research import research_v2_corpus as core


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "preflight", "capture", "build", "verify"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--root", type=Path, default=corpus.ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = (repo / args.root).resolve()
    if root != (repo / corpus.ROOT).resolve():
        raise core.CorpusError("output root differs from frozen repository location")
    if args.command == "plan":
        if root.exists():
            raise core.CorpusError("frozen corpus root already exists")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        plan = corpus.make_plan(repo, revision)
        core.durable_new(root / "plan.json", core.canonical(plan))
        print(
            {
                "plan_sha256": core.sha((root / "plan.json").read_bytes()),
                "requests": len(plan["requests"]),
                "reused_positions": plan["reused_position_total"],
                "new_positions": plan["new_position_total"],
            }
        )
        return 0
    plan = core.read_json(root / "plan.json")
    corpus.validate_plan(repo, root, plan)
    if args.command == "preflight":
        snapshot = corpus.predecessor_snapshot(repo)
        print(
            {
                "status": "READY",
                "requests": len(plan["requests"]),
                "reused_positions": sum(len(rows) for rows in snapshot["reused"].values()),
            }
        )
    elif args.command == "capture":
        print(corpus.capture(root, plan))
    elif args.command == "build":
        print(corpus.build(repo, root, plan))
    else:
        print(corpus.verify(repo, root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
