"""Run the paired hourly Bollinger Method-II no-time-exit diagnosis."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    DEFAULT_PARENT_AUTHORITY,
    ParentAuthority,
    run,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    ScreenInputAuthority,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    authority: ScreenInputAuthority | None = None,
    parent: Path | None = None,
    parent_authority: ParentAuthority | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--executable-sha", required=True)
    args = parser.parse_args(argv)
    effective_authority = DEFAULT_INPUT_AUTHORITY if authority is None else authority
    effective_parent_authority = (
        DEFAULT_PARENT_AUTHORITY if parent_authority is None else parent_authority
    )
    result = run(
        args.repo,
        args.output,
        run_id=args.run_id,
        executable_sha=args.executable_sha,
        authority=effective_authority,
        parent=parent,
        parent_authority=effective_parent_authority,
        followup_end=effective_authority.tail_end,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
