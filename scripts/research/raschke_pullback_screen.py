"""One-shot CLI for the frozen Raschke pullback depth ablation."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adaptive_trading_bot.research.failed_breakout_reversion_screen import (  # noqa: E402
    ScreenInputAuthority,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (  # noqa: E402
    PROTOCOL_SHA256,
    run,
)


def main(argv: list[str] | None = None, *, authority: ScreenInputAuthority | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--executable-sha", required=True)
    parser.add_argument("--protocol-sha256", default=PROTOCOL_SHA256)
    args = parser.parse_args(argv)
    if authority is None:
        run(
            args.repo,
            args.output,
            run_id=args.run_id,
            executable_sha=args.executable_sha,
            expected_protocol_sha256=args.protocol_sha256,
        )
    else:
        run(
            args.repo,
            args.output,
            run_id=args.run_id,
            executable_sha=args.executable_sha,
            authority=authority,
            followup_end=datetime.fromisoformat(authority.report_end.replace("Z", "+00:00")),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
