"""Run the selective hourly no-time-exit completion."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    DEFAULT_PARENT_AUTHORITY,
    FOLLOWUP_END,
    ParentAuthority,
)
from adaptive_trading_bot.research.bollinger_method2_no_time_exit_completion import (
    DEFAULT_SHORTER_AUTHORITY,
    PROTOCOL_SHA256,
    ShorterAuthority,
    protocol_record,
    run,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    ScreenInputAuthority,
    _canonical,
    _sha,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    authority: ScreenInputAuthority | None = None,
    comparator_root: Path | None = None,
    comparator_authority: ParentAuthority | None = None,
    shorter_root: Path | None = None,
    shorter_authority: ShorterAuthority | None = None,
    endpoint: datetime | None = None,
    enforce_default_counts: bool = True,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--executable-sha", required=True)
    args = parser.parse_args(argv)
    effective_authority = DEFAULT_INPUT_AUTHORITY if authority is None else authority
    effective_comparator = (
        DEFAULT_PARENT_AUTHORITY if comparator_authority is None else comparator_authority
    )
    effective_shorter = (
        DEFAULT_SHORTER_AUTHORITY if shorter_authority is None else shorter_authority
    )
    effective_endpoint = FOLLOWUP_END if endpoint is None else endpoint
    effective_protocol_sha256 = _sha(
        _canonical(
            protocol_record(
                comparator=effective_comparator,
                shorter=effective_shorter,
                endpoint=effective_endpoint,
            )
        )
    )
    production_defaults = all(
        value is None
        for value in (
            authority,
            comparator_root,
            comparator_authority,
            shorter_root,
            shorter_authority,
            endpoint,
        )
    )
    expected = PROTOCOL_SHA256 if production_defaults else effective_protocol_sha256
    result = run(
        args.repo,
        args.output,
        run_id=args.run_id,
        executable_sha=args.executable_sha,
        authority=effective_authority,
        comparator_root=comparator_root,
        comparator_authority=effective_comparator,
        shorter_root=shorter_root,
        shorter_authority=effective_shorter,
        endpoint=effective_endpoint,
        expected_protocol_sha256=expected,
        enforce_default_counts=enforce_default_counts,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
