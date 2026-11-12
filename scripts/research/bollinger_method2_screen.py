"""BTC/ETH hourly Bollinger Method-II MFI entry ablation route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_trading_bot.research.bollinger_method2_screen import run
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    ScreenInputAuthority,
)


def main(
    argv: list[str] | None = None,
    *,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--executable-sha", required=True)
    args = parser.parse_args(argv)
    result = run(
        args.repo,
        args.output,
        run_id=args.run_id,
        executable_sha=args.executable_sha,
        authority=authority,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
