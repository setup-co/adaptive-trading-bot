"""One-shot ETH-only completion CLI for the Issue #237 Raschke comparison."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adaptive_trading_bot.research.raschke_pullback_completion import (  # noqa: E402
    PROTOCOL_SHA256,
    run_completion,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--parent-rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--executable-sha", required=True)
    parser.add_argument("--protocol-sha256", default=PROTOCOL_SHA256)
    args = parser.parse_args(argv)
    run_completion(
        args.repo,
        args.parent_rows,
        args.output,
        run_id=args.run_id,
        executable_sha=args.executable_sha,
        expected_protocol_sha256=args.protocol_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
