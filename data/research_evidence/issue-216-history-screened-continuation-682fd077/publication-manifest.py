"""Create the immutable Issue #216 publication inventory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "data/research_corpus/history-screened-196d-last-continuation-v1"
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "publication-manifest.json"
SOURCES = (
    "src/adaptive_trading_bot/research/history_screened_196d_continuation.py",
    "scripts/research/history_screened_196d_continuation.py",
    "scripts/research/history_screened_196d_continuation_wrapper.py",
    "tests/research/test_history_screened_196d_continuation.py",
    "docs/research/history_screened_196d_last_continuation.md",
    "docs/research/history_screened_196d_last_continuation_result.md",
    "docs/project_state.md",
)


def digest(path: Path) -> dict[str, int | str]:
    body = path.read_bytes()
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def inventory(root: Path, *, exclude: set[Path] | None = None) -> dict[str, Any]:
    skipped = exclude or set()
    return {
        path.relative_to(REPO).as_posix(): digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path not in skipped
    }


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_suffix(OUTPUT.suffix + ".pending").exists():
        raise RuntimeError("publication manifest already exists")
    corpus_files = inventory(CORPUS)
    evidence_files = inventory(EVIDENCE, exclude={OUTPUT})
    source_files = {name: digest(REPO / name) for name in SOURCES}
    manifest = {
        "schema": "issue-216-history-screened-continuation-publication-v1",
        "protocol": "HISTORY_SCREENED_196D_LAST_CONTINUATION_V1",
        "source_revision": "987dd81c2ba2643d162cad91c4ab9352be9f63bf",
        "execution_head": "682fd077ac99aa8b381d4c51e010168a333c7d98",
        "plan_sha256": "dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f",
        "corpus_manifest_sha256": (
            "756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d"
        ),
        "report_sha256": "bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66",
        "capture_status": "CAPTURE_COMPLETE",
        "dataset_qualification": "QUALIFIED",
        "requests": {"distinct": 1785, "attempts": 1785, "retries": 0},
        "positions": {
            "parent_reused": 234956,
            "parent_captured": 158188,
            "successor_captured": 171336,
            "combined": 564480,
        },
        "corpus_files": corpus_files,
        "evidence_files": evidence_files,
        "publication_sources": source_files,
    }
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    pending = OUTPUT.with_suffix(OUTPUT.suffix + ".pending")
    with pending.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, OUTPUT)
    print(hashlib.sha256(body).hexdigest())


if __name__ == "__main__":
    main()
