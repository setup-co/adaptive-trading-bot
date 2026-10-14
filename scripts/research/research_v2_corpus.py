"""Issue #179 frozen public-only corpus plan/capture/build/verify commands."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from adaptive_trading_bot.research import research_v2_corpus as corpus

DEFAULT_ROOT = Path(corpus.OUTPUT_ROOT)
SOURCE = Path("src/adaptive_trading_bot/research/research_v2_corpus.py")
SCRIPT = Path("scripts/research/research_v2_corpus.py")
DOCS = Path("docs/research/research_v2_corpus_qualification.md")


def _files(repo: Path) -> tuple[Path, Path]:
    return repo / SOURCE, repo / DOCS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "capture", "build", "verify"))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = (repo / args.root).resolve()
    if root != (repo / DEFAULT_ROOT).resolve():
        raise corpus.CorpusError("output root differs from the frozen repository location")
    source, docs = _files(repo)
    if args.command == "plan":
        if root.exists():
            raise corpus.CorpusError("frozen corpus root already exists")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        plan = corpus.make_plan(
            repo,
            corpus.sha(source.read_bytes().replace(b"\r\n", b"\n")),
            revision,
            corpus.sha(docs.read_bytes().replace(b"\r\n", b"\n")),
        )
        corpus.durable_new(root / "plan.json", corpus.canonical(plan))
        print(
            {
                "plan_sha256": corpus.sha((root / "plan.json").read_bytes()),
                "queries": len(plan["requests"]),
                "reused": len(plan["reused"]),
                "attempt_budget": plan["attempt_budget"],
            }
        )
        return 0
    plan = corpus.read_json(root / "plan.json")
    corpus.validate_plan(repo, plan, source, docs)
    if args.command == "capture":
        print(corpus.capture(root, plan))
    elif args.command == "build":
        print(corpus.build(repo, root, plan))
    else:
        print(corpus.verify(repo, root, source, docs)["manifest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
