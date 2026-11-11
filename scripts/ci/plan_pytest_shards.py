"""Collect the complete pytest inventory and partition whole modules into four CI shards."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

EXPLICIT_MODULES: dict[int, tuple[str, ...]] = {
    1: ("tests/research/test_breakout_zone_comparison.py",),
    2: (
        "tests/research/test_trend_pullback_corpus.py",
        "tests/research/test_breakout_history_screened_comparison.py",
    ),
    3: (
        "tests/research/test_research_v2_admission.py",
        "tests/research/test_research_v2_corpus_tail.py",
        "tests/research/test_research_v2_exploratory_screen.py",
        "tests/research/test_btc_eth_retained_execution_comparison.py",
    ),
}


def _module(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].replace("\\", "/")


def _digest(nodeids: list[str]) -> str:
    return hashlib.sha256(("\n".join(nodeids) + "\n").encode()).hexdigest()


def partition_nodeids(nodeids: list[str]) -> dict[int, list[str]]:
    """Assign each collected node ID exactly once while preserving collection order."""

    counts = Counter(nodeids)
    duplicates = sorted(nodeid for nodeid, count in counts.items() if count != 1)
    if duplicates:
        raise ValueError(f"duplicate collected node IDs: {duplicates[:5]}")
    explicit_owner = {
        module: shard for shard, modules in EXPLICIT_MODULES.items() for module in modules
    }
    collected_modules = {_module(nodeid) for nodeid in nodeids}
    missing_modules = sorted(set(explicit_owner) - collected_modules)
    if missing_modules:
        raise ValueError(f"explicit shard modules missing from collection: {missing_modules}")
    shards = {index: [] for index in range(1, 5)}
    for nodeid in nodeids:
        shards[explicit_owner.get(_module(nodeid), 4)].append(nodeid)
    flattened = [nodeid for shard in shards.values() for nodeid in shard]
    assigned = Counter(flattened)
    overlap = sorted(nodeid for nodeid, count in assigned.items() if count != 1)
    missing = sorted(set(nodeids) - set(flattened))
    extra = sorted(set(flattened) - set(nodeids))
    if overlap or missing or extra or len(flattened) != len(nodeids):
        raise ValueError(
            f"invalid shard union: overlap={overlap[:5]} missing={missing[:5]} extra={extra[:5]}"
        )
    if any(not values for values in shards.values()):
        raise ValueError("every required shard must contain at least one test")
    return shards


class _CollectionPlugin:
    def __init__(self) -> None:
        self.nodeids: list[str] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.nodeids = [item.nodeid for item in session.items]


def collect_nodeids() -> list[str]:
    plugin = _CollectionPlugin()
    collection_log = io.StringIO()
    with contextlib.redirect_stdout(collection_log), contextlib.redirect_stderr(collection_log):
        status = pytest.main(["--collect-only", "-q"], plugins=[plugin])
    if status != pytest.ExitCode.OK:
        print(collection_log.getvalue(), file=sys.stderr)
        raise RuntimeError(f"pytest collection failed with exit code {int(status)}")
    if not plugin.nodeids:
        raise RuntimeError("pytest collection returned no node IDs")
    return plugin.nodeids


def write_plan(output: Path, nodeids: list[str]) -> dict[str, object]:
    shards = partition_nodeids(nodeids)
    output.mkdir(parents=True, exist_ok=False)
    (output / "full-nodeids.txt").write_text("\n".join(nodeids) + "\n", encoding="utf-8")
    shard_records: dict[str, object] = {}
    for index, values in shards.items():
        (output / f"shard-{index}.txt").write_text("\n".join(values) + "\n", encoding="utf-8")
        shard_records[str(index)] = {
            "count": len(values),
            "nodeids_sha256": _digest(values),
            "modules": list(dict.fromkeys(_module(nodeid) for nodeid in values)),
        }
    manifest: dict[str, object] = {
        "schema": "pytest-four-shard-plan-v1",
        "full_count": len(nodeids),
        "full_nodeids_sha256": _digest(nodeids),
        "explicit_modules": {str(key): list(value) for key, value in EXPLICIT_MODULES.items()},
        "shards": shard_records,
        "union_verified": True,
        "overlap_count": 0,
        "missing_count": 0,
    }
    (output / "inventory.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = write_plan(args.output, collect_nodeids())
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
