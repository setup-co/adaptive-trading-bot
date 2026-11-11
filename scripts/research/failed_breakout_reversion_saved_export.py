"""Export the Issue #231 projection from retained compact saved records only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_trading_bot.research.failed_breakout_reversion_saved_export import (
    export_saved_projection,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exporter-sha", required=True)
    args = parser.parse_args(argv)
    result = export_saved_projection(
        args.repo,
        args.output,
        exporter_sha=args.exporter_sha,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
