"""Create the immutable Issue #214 partial-publication file index."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
CORPUS = REPO / "data/research_corpus/history-screened-196d-last-v1"
TARGET = EVIDENCE / "manifest.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def indexed(paths: list[Path]) -> dict[str, dict[str, int | str]]:
    return {
        path.relative_to(REPO).as_posix(): {"bytes": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(paths)
    }


def main() -> None:
    if TARGET.exists():
        raise RuntimeError("publication manifest already exists")
    corpus_files = [path for path in CORPUS.rglob("*") if path.is_file()]
    evidence_files = [path for path in EVIDENCE.rglob("*") if path.is_file() and path != TARGET]
    source_files = [
        REPO / "src/adaptive_trading_bot/research/history_screened_196d_corpus.py",
        REPO / "scripts/research/history_screened_196d_corpus.py",
        REPO / "tests/research/test_history_screened_196d_corpus.py",
        REPO / "docs/research/history_screened_196d_last_corpus.md",
        REPO / "docs/research/history_screened_196d_last_partial_result.md",
        REPO / "docs/project_state.md",
    ]
    value = {
        "schema": "issue-214-history-screened-partial-publication-manifest-v1",
        "capture_status": "ROUTE_STOPPED",
        "capture_exit_status": "CAPTURE_EXIT_UNKNOWN_NOT_RECORDED",
        "dataset_qualification": "BLOCKED_NOT_ESTABLISHED",
        "executable_sha": "3cc2f475cddc0466d28edd5f11587b7e8321e17a",
        "plan_sha256": "0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713",
        "partial_report_sha256": "606cec24dd4620386ddc9bf8cfebdfd9e01ab4e4a4a68a82ee37fb23232ae82f",
        "counts": {
            "successful_requests": 1648,
            "failed_requests": 1,
            "unattempted_requests": 1784,
            "raw_response_files": 1648,
            "corpus_files": len(corpus_files),
        },
        "corpus_files": indexed(corpus_files),
        "evidence_files": indexed(evidence_files),
        "publication_sources": indexed(source_files),
    }
    TARGET.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    print(digest(TARGET))


if __name__ == "__main__":
    main()
