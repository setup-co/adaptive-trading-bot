"""Data-only preflight of the exact accepted Research V2 C2 epoch binding.

There is deliberately no study or market-network command in this entry point.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_trading_bot.research.research_v2_admission import (
    ADMISSION_VERSION,
    DATASET_VERSION,
    MANIFEST_SHA256,
    PLAN_SHA256,
    PROFILE,
    REPORT_SHA256,
    SOURCE_ID,
    data_only_preflight,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preflight", choices=("preflight",))
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args(argv)
    members = data_only_preflight(args.repo)
    print(
        json.dumps(
            {
                "admission_version": ADMISSION_VERSION,
                "status": "DATA_ONLY_PREFLIGHT; STUDY_NOT_AUTHORIZED",
                "source_id": SOURCE_ID.value,
                "dataset_version": DATASET_VERSION,
                "manifest_sha256": MANIFEST_SHA256,
                "plan_sha256": PLAN_SHA256,
                "report_sha256": REPORT_SHA256,
                "profile": PROFILE,
                "members": members,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
