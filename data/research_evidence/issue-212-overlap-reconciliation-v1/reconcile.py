"""Offline overlap reconciliation for the retained Issue #212 evidence.

This utility has no transport code. It validates and compares only immutable
repository bytes from Issues #175, #179/#181, #210, and #212.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

DAY_MS = 86_400_000
SEP14_MS = 1_789_344_000_000
OCT14_MS = 1_791_936_000_000
JUL16_MS = 1_784_160_000_000
SUMMARY_SHA256 = "3dc81c33d3d12983973c69474e376561524b4dce1ef75723f1117851f88f1c81"
CAPTURE_MANIFEST_SHA256 = "9259da6973e5c910912c6da1b69942a3f7a0edbffd583e5b4cd9ea0395301bbb"
PLAN_SHA256 = "adbf3af07c25e32931656c896a5947dce96f15c84e4d4af7a93143a86c7cb100"
SELECTION_SHA256 = "80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4"
SELECTION_MANIFEST_SHA256 = "de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79"
BTW_MANIFEST_SHA256 = "8359e3aa106d5a75875e7422f0d607d362f3439ed18338bc81399ff88fed9b1f"
C1_MANIFEST_SHA256 = "1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed"
C2_MANIFEST_SHA256 = "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
C2_REPORT_SHA256 = "efd51747baf786ad637ec0814eb94ade83bee9a46c4fe5941f4d0fd97a9f2ca9"

CAPTURE = Path("data/research_evidence/issue-212-history-screened-shortlist-adbf3af")
SELECTION = Path("data/research_evidence/issue-175-weex-kas-completion-v1")
BTW = Path("data/research_evidence/issue-210-btw-daily-pages-52564a2")
C1 = Path("data/research_corpus/issue-179-weex-last-90d-continuation-v1")
C2 = Path("data/research_corpus/issue-179-weex-last-90d-tail-v1")

TOP20 = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZAMAUSDT",
    "BCHUSDT",
    "BTWUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "BNBUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "DOTUSDT",
    "XLMUSDT",
    "TRXUSDT",
    "HYPEUSDT",
)
ORIGINAL_TEN = TOP20[:10]
SELECTED = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZAMAUSDT",
    "BCHUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
)
FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "close_time_ms",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
)


class ReconciliationError(RuntimeError):
    """A retained evidence identity or comparison invariant failed."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def require_hash(path: Path, expected: str) -> bytes:
    if not path.is_file():
        raise ReconciliationError(f"required evidence is unavailable: {path.as_posix()}")
    body = path.read_bytes()
    if sha(body) != expected:
        raise ReconciliationError(f"retained evidence identity mismatch: {path.as_posix()}")
    return body


def load_helpers(repo: Path) -> tuple[ModuleType, str]:
    source = repo / "src/adaptive_trading_bot/research/research_v2_corpus.py"
    body = source.read_bytes()
    spec = importlib.util.spec_from_file_location("issue212_reconciliation_helpers", source)
    if spec is None or spec.loader is None:
        raise ReconciliationError("retained parser cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if Path(str(module.__file__)).resolve() != source.resolve():
        raise ReconciliationError("retained parser origin mismatch")
    return module, sha(body.replace(b"\r\n", b"\n"))


def verify_manifest(root: Path, expected: str) -> dict[str, Any]:
    manifest = json.loads(require_hash(root / "manifest.json", expected))
    files = manifest.get("files")
    if isinstance(files, list):
        for item in files:
            path = root / item["path"]
            if path.stat().st_size != item["bytes"] or sha(path.read_bytes()) != item["sha256"]:
                raise ReconciliationError(f"manifest file mismatch: {path.as_posix()}")
    elif not isinstance(files, dict):
        raise ReconciliationError(f"manifest file map is malformed: {root.as_posix()}")
    return manifest


def expected_times(start: int, count: int) -> tuple[int, ...]:
    return tuple(start + index * DAY_MS for index in range(count))


def row_fields(row: Any) -> dict[str, int | str]:
    record = row.record()
    return {field: record[field] for field in FIELDS}


def compare_rows(
    expected: tuple[int, ...], earlier: dict[int, Any] | None, later: dict[int, Any]
) -> dict[str, Any]:
    if earlier is None:
        return {
            "status": "AUTHORITY_UNAVAILABLE",
            "expected": len(expected),
            "compared": 0,
            "matching": 0,
            "different": 0,
            "unavailable": len(expected),
            "missing_from_earlier": list(expected),
            "missing_from_later": [],
            "differences": [],
        }
    expected_set = set(expected)
    earlier_set = set(earlier) & expected_set
    later_set = set(later) & expected_set
    compared = sorted(earlier_set & later_set)
    differences: list[dict[str, Any]] = []
    matching = 0
    for opened in compared:
        left = row_fields(earlier[opened])
        right = row_fields(later[opened])
        changed = [field for field in FIELDS if left[field] != right[field]]
        if changed:
            differences.append(
                {
                    "open_time_ms": opened,
                    "fields": [
                        {"field": field, "earlier": left[field], "later": right[field]}
                        for field in changed
                    ],
                }
            )
        else:
            matching += 1
    missing_earlier = sorted(expected_set - earlier_set)
    missing_later = sorted(expected_set - later_set)
    unavailable = len(expected_set - (earlier_set & later_set))
    if differences:
        status = "NUMERIC_DIFFERENCE"
    elif unavailable:
        status = "INCOMPLETE_OR_UNAVAILABLE"
    else:
        status = "COMPLETE_EXACT_MATCH"
    return {
        "status": status,
        "expected": len(expected),
        "compared": len(compared),
        "matching": matching,
        "different": len(differences),
        "unavailable": unavailable,
        "missing_from_earlier": missing_earlier,
        "missing_from_later": missing_later,
        "differences": differences,
    }


def parse(helpers: ModuleType, body: bytes, symbol: str, start: int, end: int) -> dict[int, Any]:
    rows = helpers.parse_rows(
        body,
        {
            "symbol": symbol,
            "interval": "1d",
            "priceType": "LAST",
            "limit": "100",
            "startTime": str(start),
            "endTime": str(end - 1),
        },
    )
    return {row.open_time_ms: row for row in rows}


def result_by_id(summary: dict[str, Any], request_id: str) -> dict[str, Any]:
    matches = [item for item in summary["results"] if item.get("request_id") == request_id]
    if len(matches) != 1 or matches[0].get("attempt_status") != "HTTP_OK":
        raise ReconciliationError(f"captured request result unavailable: {request_id}")
    return matches[0]


def captured_rows(
    repo: Path, helpers: ModuleType, summary: dict[str, Any], symbol: str
) -> tuple[dict[int, Any], dict[str, Any]]:
    rows: dict[int, Any] = {}
    files: list[dict[str, str]] = []
    if symbol == "BTWUSDT":
        manifest = verify_manifest(repo / BTW, BTW_MANIFEST_SHA256)
        pages = (
            ("002-BTW_DAILY_90_B.result.json", 1_782_777_600_000, 1_790_553_600_000),
            ("003-BTW_DAILY_16_C.result.json", 1_790_553_600_000, OCT14_MS),
        )
        for result_name, start, end in pages:
            result = read_json(repo / BTW / "attempts" / result_name)
            raw_path = repo / BTW / "attempts" / result["raw_file"]
            body = raw_path.read_bytes()
            if sha(body) != result["raw_sha256"]:
                raise ReconciliationError("BTW reused raw identity mismatch")
            for opened, row in parse(helpers, body, symbol, start, end).items():
                if opened in rows:
                    raise ReconciliationError("BTW reused daily pages overlap")
                rows[opened] = row
            files.append({"path": raw_path.relative_to(repo).as_posix(), "sha256": sha(body)})
        return rows, {
            "generation": "ISSUE_210_REUSED",
            "manifest_sha256": BTW_MANIFEST_SHA256,
            "files": files,
            "manifest_status": manifest["terminal_status"],
        }
    pages = (("B", 1_782_777_600_000, 1_790_553_600_000), ("C", 1_790_553_600_000, OCT14_MS))
    for page, start, end in pages:
        result = result_by_id(summary, f"{symbol}-{page}")
        raw_path = repo / CAPTURE / "attempts" / result["raw_file"]
        body = raw_path.read_bytes()
        if sha(body) != result["raw_sha256"]:
            raise ReconciliationError(f"Issue #212 raw identity mismatch: {symbol}")
        for opened, row in parse(helpers, body, symbol, start, end).items():
            if opened in rows:
                raise ReconciliationError(f"Issue #212 daily pages overlap: {symbol}")
            rows[opened] = row
        files.append({"path": raw_path.relative_to(repo).as_posix(), "sha256": sha(body)})
    return rows, {
        "generation": "ISSUE_212_CAPTURE",
        "manifest_sha256": CAPTURE_MANIFEST_SHA256,
        "files": files,
    }


def selection_raw_path(repo: Path, record: dict[str, Any]) -> Path:
    reference = record["raw_reference"]
    if reference["source"] == "ORIGINAL_173":
        root = repo / "data/research_evidence/issue-173-weex-public-feasibility-332db22"
    elif reference["source"] == "NEW_175":
        root = repo / "data/research_evidence/issue-175-weex-current-panel-cf541a3"
    elif reference["source"] == "KAS_SUPPLEMENT_175":
        root = repo / SELECTION
    else:
        raise ReconciliationError("unknown Issue #175 source generation")
    return root / reference["file"]


def zip_authority(repo: Path, symbol: str) -> tuple[bytes, dict[str, Any]]:
    generation = "ISSUE_179_C1" if symbol in ORIGINAL_TEN[:7] else "ISSUE_181_C2"
    root = repo / (C1 if generation == "ISSUE_179_C1" else C2)
    manifest_sha = C1_MANIFEST_SHA256 if generation == "ISSUE_179_C1" else C2_MANIFEST_SHA256
    manifest = verify_manifest(root, manifest_sha)
    relative = f"archives/{symbol}.zip"
    archive = root / relative
    expected_archive_sha = manifest["files"].get(relative)
    if (
        not isinstance(expected_archive_sha, str)
        or sha(archive.read_bytes()) != expected_archive_sha
    ):
        raise ReconciliationError(f"archive identity mismatch: {symbol}")
    entry_name = f"{symbol}-earlier-daily/0.raw"
    try:
        with zipfile.ZipFile(archive) as bundle:
            body = bundle.read(entry_name)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ReconciliationError(f"earlier-daily authority unavailable: {symbol}") from exc
    return body, {
        "generation": generation,
        "manifest_sha256": manifest_sha,
        "archive_path": archive.relative_to(repo).as_posix(),
        "archive_sha256": expected_archive_sha,
        "entry_path": entry_name,
        "entry_sha256": sha(body),
    }


def reconcile(repo: Path) -> dict[str, Any]:
    helpers, helper_sha = load_helpers(repo)
    capture_manifest = verify_manifest(repo / CAPTURE, CAPTURE_MANIFEST_SHA256)
    require_hash(repo / CAPTURE / "plan.json", PLAN_SHA256)
    summary = json.loads(require_hash(repo / CAPTURE / "summary.json", SUMMARY_SHA256))
    if (
        tuple(summary.get("selected_symbols", ())) != SELECTED
        or summary.get("status") != "SELECTED_FOR_FINE_GRID_DATA_QUALIFICATION"
    ):
        raise ReconciliationError("captured shortlist identity mismatch")
    require_hash(repo / SELECTION / "manifest.json", SELECTION_MANIFEST_SHA256)
    selection = json.loads(
        require_hash(repo / SELECTION / "combined_selection.json", SELECTION_SHA256)
    )
    records = {
        item["symbol"]: item
        for item in selection["candidate_records"]
        if item.get("symbol") in TOP20
    }
    if (
        tuple(item["symbol"] for item in sorted(records.values(), key=lambda item: item["rank"]))
        != TOP20
    ):
        raise ReconciliationError("Issue #175 top-20 identity mismatch")
    old_annotations = {
        item["symbol"]: item["overlap_identity"] for item in summary["candidate_records"]
    }

    current_by_symbol: dict[str, tuple[dict[int, Any], dict[str, Any]]] = {
        symbol: captured_rows(repo, helpers, summary, symbol) for symbol in TOP20
    }
    sep_expected = expected_times(SEP14_MS, 30)
    sep_records: list[dict[str, Any]] = []
    for symbol in TOP20:
        record = records[symbol]
        old_path = selection_raw_path(repo, record)
        body = require_hash(old_path, record["raw_reference"]["sha256"])
        old_rows = parse(helpers, body, symbol, SEP14_MS, OCT14_MS)
        current_rows, current_authority = current_by_symbol[symbol]
        comparison = compare_rows(sep_expected, old_rows, current_rows)
        sep_records.append(
            {
                "symbol": symbol,
                "captured_non_authoritative_label": old_annotations[symbol]["qualification"],
                "corrected_annotation": comparison,
                "earlier_authority": {
                    "generation": record["raw_reference"]["source"],
                    "path": old_path.relative_to(repo).as_posix(),
                    "sha256": sha(body),
                },
                "later_authority": current_authority,
            }
        )

    require_hash(repo / C2 / "combined/report.json", C2_REPORT_SHA256)
    jul_expected = expected_times(JUL16_MS, 60)
    jul_records: list[dict[str, Any]] = []
    for symbol in ORIGINAL_TEN:
        body, earlier_authority = zip_authority(repo, symbol)
        earlier_rows = parse(helpers, body, symbol, JUL16_MS, SEP14_MS)
        later_rows, later_authority = current_by_symbol[symbol]
        jul_records.append(
            {
                "symbol": symbol,
                "comparison": compare_rows(jul_expected, earlier_rows, later_rows),
                "earlier_authority": earlier_authority,
                "later_authority": later_authority,
            }
        )

    def totals(records_to_sum: list[dict[str, Any]], key: str) -> dict[str, int]:
        comparisons = [item[key] for item in records_to_sum]
        return {
            name: sum(item[name] for item in comparisons)
            for name in ("expected", "compared", "matching", "different", "unavailable")
        }

    september_totals = totals(sep_records, "corrected_annotation")
    july_totals = totals(jul_records, "comparison")
    if september_totals != {
        "expected": 600,
        "compared": 600,
        "matching": 600,
        "different": 0,
        "unavailable": 0,
    }:
        raise ReconciliationError("Issue #175 overlap result differs from retained evidence")
    if any(
        item["captured_non_authoritative_label"] != "REVISION_OR_COVERAGE_DIFFERENCE"
        for item in sep_records
    ):
        raise ReconciliationError("captured erroneous labels differ from reviewed evidence")
    return {
        "schema": "issue-212-overlap-reconciliation-v1",
        "network_attempts": 0,
        "capture_binding": {
            "manifest_sha256": CAPTURE_MANIFEST_SHA256,
            "summary_sha256": SUMMARY_SHA256,
            "plan_sha256": PLAN_SHA256,
            "manifest_terminal_status": capture_manifest["terminal_status"],
            "shortlist": list(SELECTED),
        },
        "source_bindings": {
            "issue_175_selection_sha256": SELECTION_SHA256,
            "issue_175_manifest_sha256": SELECTION_MANIFEST_SHA256,
            "issue_210_manifest_sha256": BTW_MANIFEST_SHA256,
            "issue_179_c1_manifest_sha256": C1_MANIFEST_SHA256,
            "issue_181_c2_manifest_sha256": C2_MANIFEST_SHA256,
            "issue_181_c2_report_sha256": C2_REPORT_SHA256,
            "parser_sha256": helper_sha,
        },
        "erratum": {
            "captured_condition": (
                "EXACT_NUMERIC_MATCH required no differences and len(overlap_points) == 16"
            ),
            "declared_expected_timestamp_count": 30,
            "captured_label_authoritative": False,
            "coverage_qualification_separate": True,
        },
        "issue_175_overlap": {
            "window": "[2026-09-14T00:00:00Z,2026-10-14T00:00:00Z)",
            "totals": september_totals,
            "records": sep_records,
        },
        "earlier_daily_overlap": {
            "window": "[2026-07-16T00:00:00Z,2026-09-14T00:00:00Z)",
            "totals": july_totals,
            "records": jul_records,
        },
        "selection_changed": False,
        "coverage_changed": False,
        "interpretation": "DESCRIPTIVE_OFFLINE_PROVENANCE_RECONCILIATION_ONLY",
    }


def synthetic() -> dict[str, Any]:
    class FakeRow:
        def __init__(self, opened: int, close: str = "1") -> None:
            self.opened = opened
            self.close = close

        def record(self) -> dict[str, int | str]:
            return {
                "open_time_ms": self.opened,
                "open": "1",
                "high": "1",
                "low": "1",
                "close": self.close,
                "base_volume": "0",
                "close_time_ms": self.opened + DAY_MS,
                "quote_volume": "0",
                "trade_count": 0,
                "taker_buy_base_volume": "0",
                "taker_buy_quote_volume": "0",
            }

    expected = expected_times(SEP14_MS, 30)
    full = {opened: FakeRow(opened) for opened in expected}
    sixteen = {opened: FakeRow(opened) for opened in expected[:16]}
    changed = dict(full)
    changed[expected[5]] = FakeRow(expected[5], close="2")
    cases = {
        "complete_equal": compare_rows(expected, full, full),
        "sixteen_equal_fourteen_absent": compare_rows(expected, sixteen, full),
        "full_one_field_changed": compare_rows(expected, full, changed),
        "authority_unavailable": compare_rows(expected, None, full),
    }
    if (
        cases["complete_equal"]["status"] != "COMPLETE_EXACT_MATCH"
        or cases["sixteen_equal_fourteen_absent"]["status"] != "INCOMPLETE_OR_UNAVAILABLE"
        or cases["sixteen_equal_fourteen_absent"]["unavailable"] != 14
        or cases["full_one_field_changed"]["status"] != "NUMERIC_DIFFERENCE"
        or cases["full_one_field_changed"]["differences"][0]["fields"][0]["field"] != "close"
        or cases["authority_unavailable"]["status"] != "AUTHORITY_UNAVAILABLE"
    ):
        raise ReconciliationError("synthetic reconciliation regression failed")
    return {
        "schema": "issue-212-overlap-reconciliation-synthetic-v1",
        "network_attempts": 0,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("synthetic", "run", "verify"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = synthetic() if args.command == "synthetic" else reconcile(args.repo.resolve())
    body = canonical(value)
    if args.command in {"run", "synthetic"}:
        if args.output is None or args.output.exists():
            raise ReconciliationError(f"{args.command} requires a new output path")
        args.output.write_bytes(body)
    elif args.command == "verify":
        if args.output is None or not args.output.is_file() or args.output.read_bytes() != body:
            raise ReconciliationError("saved reconciliation differs from retained evidence")
    print(
        json.dumps({"sha256": sha(body), "network_attempts": 0, "status": "PASS"}, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
