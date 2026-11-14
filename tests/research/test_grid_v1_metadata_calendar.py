from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "research" / "grid_v1_metadata_calendar.py"
PARAMETERS = ROOT / "docs" / "research" / "btc_eth_grid_4h_v1_parameters.json"
CALENDAR = ROOT / "docs" / "research" / "btc_eth_grid_4h_v1_eligibility_calendar.json"
EXAMPLES = ROOT / "docs" / "research" / "btc_eth_grid_4h_v1_accounting_examples.json"
MANIFEST = (
    ROOT
    / "data"
    / "research_evidence"
    / "issue-239-binance-grid-history-v3-c9331a7e"
    / "dataset"
    / "qualification-manifest-v3.json"
)


def _load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grid_v1_metadata_calendar", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_checked_in_calendar_is_exact_metadata_only_generation() -> None:
    helper = _load_helper()
    parameters = _json(PARAMETERS)
    expected = helper.generate_calendar(_json(MANIFEST), parameters, helper.sha256_file(PARAMETERS))
    actual = _json(CALENDAR)

    assert actual == expected
    assert actual["strategy_evaluation_performed"] is False
    assert actual["price_rows_read"] == 0
    assert actual["pnl_calculated"] is False
    assert len(actual["episodes"]) == 712
    assert actual["summary"]["BTCUSDT"]["status_counts"] == {
        "METADATA_ELIGIBLE": 338,
        "METADATA_INELIGIBLE": 18,
    }
    assert actual["summary"]["ETHUSDT"]["status_counts"] == {
        "METADATA_ELIGIBLE": 345,
        "METADATA_INELIGIBLE": 11,
    }


def test_episode_metadata_distinguishes_price_volume_and_mark_findings() -> None:
    helper = _load_helper()
    coverage = {
        "reconciliation": {
            "discrepancies": [
                {"open_time_ms": 1578182400000, "mismatched_fields": ["close"]},
                {"open_time_ms": 1578355200000, "mismatched_fields": ["volume"]},
            ]
        },
        "trade_price_1m": {"grid": {"gaps": []}},
        "derived_4h": {"grid": {"gaps": []}},
        "mark_1m": {
            "grid": {
                "gaps": [
                    {
                        "start": "2020-01-08T00:00:00Z",
                        "end_exclusive": "2020-01-08T00:01:00Z",
                    }
                ]
            }
        },
        "funding": {"continuity_status": "SCHEDULE_PARTIAL_OR_UNCERTAIN"},
    }

    row = helper._episode(
        "BTCUSDT",
        helper.parse_utc("2020-01-06T00:00:00Z"),
        helper.parse_utc("2019-12-01T00:00:00Z"),
        helper.parse_utc("2020-02-01T00:00:00Z"),
        coverage,
    )

    assert row["status"] == "METADATA_INELIGIBLE"
    assert row["reason_codes"] == [
        "PRICE_DISAGREEMENT_IN_WARMUP",
        "REQUIRED_ACTIVE_MARK_GAP",
    ]
    assert row["flag_codes"] == [
        "FUNDING_CONTINUITY_PARTIAL_OR_UNCERTAIN",
        "VOLUME_ONLY_OR_COMBINED_DISCREPANCY_UNUSED_BY_MODEL",
    ]


def test_accounting_examples_use_exact_decimal_arithmetic() -> None:
    examples = {row["id"]: row for row in _json(EXAMPLES)["examples"]}
    long_row = examples["LONG_ADJACENT_RUNG_BASE"]
    inputs = {key: Decimal(value) for key, value in long_row["inputs"].items()}
    gross = inputs["quantity"] * (inputs["exit"] - inputs["entry"])
    entry_fee = inputs["quantity"] * inputs["entry"] * inputs["entry_fee_rate"]
    exit_fee = inputs["quantity"] * inputs["exit"] * inputs["exit_fee_rate"]
    assert gross == Decimal(long_row["outputs"]["gross_pnl"])
    assert entry_fee == Decimal(long_row["outputs"]["entry_fee"])
    assert exit_fee == Decimal(long_row["outputs"]["exit_fee"])
    assert gross - entry_fee - exit_fee == Decimal(long_row["outputs"]["net_cash_change"])

    terminal = examples["SCHEDULED_TERMINAL_FLATTEN_LONG_BASE"]
    output = {key: Decimal(value) for key, value in terminal["outputs"].items()}
    assert (
        output["final_cash"] - output["preclose_equity"]
        == output["close_impact_vs_preclose_equity"]
    )
