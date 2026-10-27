from __future__ import annotations

import json
import socket
import sys
from collections import Counter
from pathlib import Path

CHECKOUT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\work\issue-214-history-screened-196d-corpus")
ROOT = CHECKOUT / "data/research_corpus/history-screened-196d-last-v1"
OUTPUT = Path(r"C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-214-run\partial-report.json")


def _offline(*args: object, **kwargs: object) -> None:
    raise RuntimeError("network disabled for Issue #214 partial audit")


socket.socket = _offline  # type: ignore[assignment]
socket.create_connection = _offline  # type: ignore[assignment]
sys.path.insert(0, str(CHECKOUT / "src"))

from adaptive_trading_bot.research import history_screened_196d_corpus as subject  # noqa: E402
from adaptive_trading_bot.research import research_v2_corpus as core  # noqa: E402


def main() -> None:
    plan = core.read_json(ROOT / "plan.json")
    subject.validate_plan(CHECKOUT, ROOT, plan)
    ledger = core.audit_ledger(ROOT, plan)
    snapshot = subject.predecessor_snapshot(CHECKOUT)
    states = ledger["states"]
    requests = {entry["id"]: entry for entry in plan["requests"]}
    failed = []
    unattempted = []
    for entry in plan["requests"]:
        records = [states.get((entry["id"], attempt)) for attempt in (0, 1)]
        present = [record for record in records if record is not None]
        if not present:
            unattempted.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "expected_rows": entry["expected_rows"],
                }
            )
        elif present[-1]["outcome"] != "HTTP_OK":
            failed.append(
                {
                    "id": entry["id"],
                    "query": entry["query"],
                    "expected_rows": entry["expected_rows"],
                    "result": present[-1],
                }
            )

    symbols: dict[str, object] = {}
    total_success_rows = 0
    for symbol in subject.PANEL:
        new_rows, statuses = subject._new_rows(ROOT, plan, symbol, None)
        reused_rows = snapshot["reused"].get(symbol, {})
        combined = dict(reused_rows)
        for opened, row in new_rows.items():
            previous = combined.get(opened)
            if previous is not None and previous != row:
                raise core.CorpusError("partial audit found conflicting reused/new row")
            combined[opened] = row
        spans = core.epochs(combined)
        frames = {
            name: core.derive(combined, spans, size)
            for name, size in (("15m", 3), ("1h", 12), ("4h", 48))
        }
        daily = subject._daily_compare(combined, snapshot["daily"][symbol])
        symbol_failed = [entry for entry in failed if entry["query"]["symbol"] == symbol]
        symbol_unattempted = [
            entry for entry in unattempted if entry["query"]["symbol"] == symbol
        ]
        total_success_rows += len(new_rows)
        symbols[symbol] = {
            "expected_5m_positions": subject.EXPECTED_ROWS,
            "validated_reused_5m_rows": len(reused_rows),
            "validated_new_5m_rows": len(new_rows),
            "validated_available_5m_rows": len(combined),
            "unavailable_5m_positions": subject.EXPECTED_ROWS - len(combined),
            "successful_response_internal_absences": 0,
            "failed_requests": symbol_failed,
            "failed_request_expected_positions": sum(
                entry["expected_rows"] for entry in symbol_failed
            ),
            "unattempted_requests": symbol_unattempted,
            "unattempted_expected_positions": sum(
                entry["expected_rows"] for entry in symbol_unattempted
            ),
            "available_epochs": [
                {
                    "start_ms": start,
                    "end_ms": end,
                    "rows": (end - start) // subject.STEP_MS,
                }
                for start, end in spans
            ],
            "derived_complete_rows": {name: len(rows) for name, rows in frames.items()},
            "daily_comparison_counts": dict(Counter(item["status"] for item in daily)),
            "request_status_counts": dict(statuses),
            "qualification": "PARTIAL_ROUTE_STOPPED",
        }

    if (
        ledger["distinct"] != 1_649
        or ledger["attempts"] != 1_649
        or ledger["retries"] != 0
        or total_success_rows != 164_800
        or len(failed) != 1
        or len(unattempted) != 1_784
    ):
        raise core.CorpusError("partial ledger/count reconciliation changed")

    report = {
        "schema": "history-screened-196d-partial-publication-v1",
        "protocol": subject.PROTOCOL,
        "capture_status": "ROUTE_STOPPED",
        "capture_exit_status": "CAPTURE_EXIT_UNKNOWN_NOT_RECORDED",
        "dataset_qualification": "BLOCKED_NOT_ESTABLISHED",
        "full_panel_180_day_readiness": "BLOCKED_NOT_ESTABLISHED",
        "executable_sha": "3cc2f475cddc0466d28edd5f11587b7e8321e17a",
        "plan_sha256": core.sha((ROOT / "plan.json").read_bytes()),
        "ledger": {
            "attempts": ledger["attempts"],
            "distinct": ledger["distinct"],
            "successful": 1_648,
            "failed": len(failed),
            "retries": ledger["retries"],
            "unattempted": len(unattempted),
        },
        "position_totals": {
            "expected": len(subject.PANEL) * subject.EXPECTED_ROWS,
            "validated_reused": subject.EXPECTED_REUSED,
            "validated_new": total_success_rows,
            "validated_available": subject.EXPECTED_REUSED + total_success_rows,
            "successful_response_internal_absences": 0,
            "failed_request_expected_positions": sum(
                entry["expected_rows"] for entry in failed
            ),
            "unattempted_expected_positions": sum(
                entry["expected_rows"] for entry in unattempted
            ),
        },
        "failed_requests": failed,
        "unattempted_requests": unattempted,
        "symbols": symbols,
        "outputs": {
            "canonical_5m": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
            "derived_15m_1h_4h": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
            "immutable_dataset_manifest": "UNAVAILABLE_FROZEN_BUILD_REJECTS_ROUTE_STOPPED_STATE",
        },
        "interpretation": (
            "Request-status and validated-candle accounting only. Unattempted or failed "
            "requests are not evidence that exchange history is unavailable. No strategy, "
            "candidate, PnL, or historical-mechanics result was produced."
        ),
    }
    OUTPUT.write_bytes(core.canonical(report))
    print(
        json.dumps(
            {
                "status": report["dataset_qualification"],
                "validated_available": report["position_totals"]["validated_available"],
                "failed": len(failed),
                "unattempted": len(unattempted),
                "report_sha256": core.sha(OUTPUT.read_bytes()),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
