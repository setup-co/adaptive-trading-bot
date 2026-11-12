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
    validate_saved_rows,
)


def main(argv: list[str] | None = None, *, authority: ScreenInputAuthority | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--executable-sha")
    parser.add_argument("--protocol-sha256", default=PROTOCOL_SHA256)
    parser.add_argument("--saved-rows", type=Path)
    parser.add_argument("--expected-gzip-sha256")
    parser.add_argument("--expected-raw-sha256")
    parser.add_argument("--expected-rows", type=int)
    parser.add_argument("--report-path-proof", action="store_true")
    args = parser.parse_args(argv)
    if args.saved_rows is not None:
        if (
            args.expected_gzip_sha256 is None
            or args.expected_raw_sha256 is None
            or args.expected_rows is None
        ):
            parser.error("saved-row validation requires both hashes and expected row count")
        validate_saved_rows(
            args.saved_rows,
            args.output,
            expected_gzip_sha256=args.expected_gzip_sha256,
            expected_raw_sha256=args.expected_raw_sha256,
            expected_rows=args.expected_rows,
            include_report_path_proof=args.report_path_proof,
        )
        return 0
    if args.repo is None or args.run_id is None or args.executable_sha is None:
        parser.error("historical run requires --repo, --run-id and --executable-sha")
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
