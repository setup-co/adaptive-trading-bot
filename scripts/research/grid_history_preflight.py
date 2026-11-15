"""Inert Issue #245 Grid history preflight CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_trading_bot.research.grid_history_preflight import (
    metadata_only_preflight,
    refuse_historical_execution,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--data-root", type=Path)
    commands.add_parser("run")
    args = parser.parse_args()
    if args.command == "run":
        refuse_historical_execution()
    result = metadata_only_preflight(args.repo, args.data_root)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
