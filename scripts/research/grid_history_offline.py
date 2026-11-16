"""Issue #255 owner-waived offline-only Grid historical-study CLI."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import adaptive_trading_bot.research.grid_history_offline as offline_module
import adaptive_trading_bot.research.grid_history_preflight as preflight_module
import adaptive_trading_bot.research.grid_history_runner as runner_module
import adaptive_trading_bot.research.grid_v1 as grid_module
from adaptive_trading_bot.research.grid_history_offline import (
    OfflineResearchManifest,
    build_manifest,
    run_offline_study,
    write_new,
)
from adaptive_trading_bot.research.grid_history_preflight import canonical_json
from adaptive_trading_bot.research.grid_history_runner import verified_checkout_identity


def _identity(repo: Path) -> tuple[str, str]:
    return verified_checkout_identity(
        repo,
        imported_files={
            "scripts/research/grid_history_offline.py": Path(__file__),
            "src/adaptive_trading_bot/research/grid_history_offline.py": Path(
                str(offline_module.__file__)
            ),
            "src/adaptive_trading_bot/research/grid_history_runner.py": Path(
                str(runner_module.__file__)
            ),
            "src/adaptive_trading_bot/research/grid_history_preflight.py": Path(
                str(preflight_module.__file__)
            ),
            "src/adaptive_trading_bot/research/grid_v1.py": Path(str(grid_module.__file__)),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-manifest")
    prepare.add_argument("--run-uuid", required=True)
    prepare.add_argument("--data-root", required=True, type=Path)
    prepare.add_argument("--ledger-root", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    actual_sha, actual_tree = _identity(repo)
    if args.command == "prepare-manifest":
        manifest = build_manifest(
            repo=repo,
            data_root=args.data_root,
            ledger_root=args.ledger_root,
            run_uuid=args.run_uuid,
            actual_sha=actual_sha,
            actual_tree=actual_tree,
        )
        manifest.validate(actual_sha=actual_sha, actual_tree=actual_tree)
        write_new(args.output, asdict(manifest))
        print(
            json.dumps(
                {
                    "manifest_sha256": manifest.sha256,
                    "run_uuid": manifest.run_uuid,
                    "status": "OFFLINE_MANIFEST_FROZEN_BEFORE_VALUE_READ",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    manifest = OfflineResearchManifest.from_file(args.manifest)
    projection = run_offline_study(
        repo=repo,
        manifest=manifest,
        actual_sha=actual_sha,
        actual_tree=actual_tree,
    )
    print(canonical_json(projection).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
