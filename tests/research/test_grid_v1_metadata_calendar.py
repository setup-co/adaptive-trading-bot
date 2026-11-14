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
    eligible_by_symbol_partition = {
        (symbol, partition): sum(
            row["symbol"] == symbol
            and row["partition"] == partition
            and row["status"] == "METADATA_ELIGIBLE"
            for row in actual["episodes"]
        )
        for symbol in ("BTCUSDT", "ETHUSDT")
        for partition in ("DEVELOPMENT", "VALIDATION", "RETROSPECTIVE_ASSESSMENT")
    }
    assert eligible_by_symbol_partition == {
        ("BTCUSDT", "DEVELOPMENT"): 148,
        ("BTCUSDT", "VALIDATION"): 99,
        ("BTCUSDT", "RETROSPECTIVE_ASSESSMENT"): 91,
        ("ETHUSDT", "DEVELOPMENT"): 151,
        ("ETHUSDT", "VALIDATION"): 101,
        ("ETHUSDT", "RETROSPECTIVE_ASSESSMENT"): 93,
    }


def test_episode_metadata_distinguishes_price_volume_and_mark_findings() -> None:
    helper = _load_helper()
    coverage = {
        "reconciliation": {
            "discrepancies": [
                {
                    "open_time_ms": 1578182400000,
                    "mismatched_fields": ["close", "volume"],
                },
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
        "VOLUME_DISCREPANCY_PRESENT_UNUSED_BY_MODEL",
    ]


def test_calendar_rejects_parameter_timing_or_lookback_drift() -> None:
    helper = _load_helper()
    parameters = _json(PARAMETERS)
    parameters["geometry"]["lookback_4h_bars"] = 43

    try:
        helper.generate_calendar(_json(MANIFEST), parameters, "synthetic")
    except helper.CalendarError as exc:
        assert "fixed calendar timing/lookback contract" in str(exc)
    else:
        raise AssertionError("calendar accepted a parameter identity it cannot represent")


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

    boundary = examples["FLAT_TO_LOWER_BOUNDARY_BASE"]
    boundary_inputs = boundary["inputs"]
    boundary_outputs = boundary["outputs"]
    quantity = Decimal(boundary_inputs["quantity_per_rung"])
    buys = [Decimal(value) for value in boundary_inputs["buy_prices"]]
    forced_fill = Decimal(boundary_inputs["lower_boundary"]) * (
        Decimal(1) - Decimal(boundary_inputs["forced_slippage_rate"])
    )
    boundary_gross = quantity * sum((forced_fill - price for price in buys), Decimal(0))
    boundary_entry_fees = (
        quantity * sum(buys, Decimal(0)) * Decimal(boundary_inputs["passive_fee_rate"])
    )
    boundary_exit_fee = (
        quantity * Decimal(len(buys)) * forced_fill * Decimal(boundary_inputs["forced_fee_rate"])
    )
    assert forced_fill == Decimal(boundary_outputs["forced_fill_price"])
    assert sum(buys, Decimal(0)) / Decimal(len(buys)) == Decimal(boundary_outputs["average_entry"])
    assert boundary_gross == Decimal(boundary_outputs["gross_pnl"])
    assert boundary_entry_fees == Decimal(boundary_outputs["entry_fees"])
    assert boundary_exit_fee == Decimal(boundary_outputs["forced_exit_fee"])
    assert boundary_gross - boundary_entry_fees - boundary_exit_fee == Decimal(
        boundary_outputs["net_cash_change"]
    )

    existing = examples["EXISTING_LONG_PLUS_NEW_RUNGS_TO_BOUNDARY_BASE"]
    existing_inputs = existing["inputs"]
    existing_outputs = existing["outputs"]
    existing_cost = Decimal(existing_inputs["starting_lots"]) * Decimal(
        existing_inputs["starting_average_entry"]
    ) + sum(
        (Decimal(value) for value in existing_inputs["new_buy_prices"]),
        Decimal(0),
    )
    existing_lots = Decimal(existing_outputs["closing_lots"])
    existing_quantity = Decimal(existing_inputs["quantity_per_rung"])
    existing_fill = Decimal(existing_inputs["forced_fill_price"])
    existing_gross = existing_quantity * (existing_lots * existing_fill - existing_cost)
    existing_new_fees = (
        existing_quantity
        * sum(
            (Decimal(value) for value in existing_inputs["new_buy_prices"]),
            Decimal(0),
        )
        * Decimal(existing_inputs["passive_fee_rate"])
    )
    existing_exit_fee = (
        existing_lots
        * existing_quantity
        * existing_fill
        * Decimal(existing_inputs["forced_fee_rate"])
    )
    assert existing_cost / existing_lots == Decimal(existing_outputs["preclose_average_entry"])
    assert existing_gross == Decimal(existing_outputs["gross_pnl"])
    assert existing_new_fees == Decimal(existing_outputs["new_entry_fees"])
    assert existing_exit_fee == Decimal(existing_outputs["forced_exit_fee"])
    assert existing_gross - existing_new_fees - existing_exit_fee == Decimal(
        existing_outputs["incremental_minute_cash_change_excluding_prior_fees"]
    )

    ownership = examples["GLOBAL_CROSSING_AND_LOT_OWNERSHIP"]["outputs"]
    assert ownership["ordered_events"] == [
        "EXIT_L1_99.03",
        "EXIT_L2_100",
        "ENTRY_SHORT_100.98",
        "ENTRY_SHORT_101.97",
    ]
    assert ownership["ending_side"] == "SHORT"
    assert ownership["ending_lots"] == 2
    assert ownership["orphan_exits"] == 0

    marketable = examples["MARKETABLE_POST_ONLY_ACTIVATION"]["outputs"]
    assert marketable == {
        "status": "POST_ONLY_REJECTED",
        "fill_quantity": "0",
        "fee": "0",
        "rearm_time": "2026-01-05T00:02:00Z",
    }

    equal_time = examples["FUNDING_AT_OPEN_THEN_CLOSE"]["outputs"]
    later_time = examples["CLOSE_AT_OPEN_THEN_LATER_FUNDING"]["outputs"]
    assert equal_time["event_order"] == ["FUNDING", "FORCED_CLOSE"]
    assert Decimal(equal_time["base_funding_cash_change"]) == Decimal("-0.0002")
    assert later_time["event_order"] == ["FORCED_CLOSE", "FUNDING"]
    assert Decimal(later_time["quantity_at_funding"]) == 0
    assert Decimal(later_time["base_funding_cash_change"]) == 0

    termination = examples["ORIGINAL_CAPITAL_FLOOR_TERMINATION"]
    termination_inputs = termination["inputs"]
    termination_outputs = termination["outputs"]
    assert Decimal(termination_inputs["original_capital"]) * Decimal(
        termination_inputs["floor_fraction"]
    ) == Decimal(termination_outputs["floor_equity"])
    assert Decimal(termination_inputs["completed_minute_equity"]) < Decimal(
        termination_outputs["floor_equity"]
    )
    assert termination_outputs["immediate_actions"] == [
        "CANCEL_ALL_ENTRIES",
        "CANCEL_ALL_EXITS",
    ]
    assert termination_outputs["subsequent_symbol_state"] == ("PERMANENTLY_TERMINATED_CASH_ONLY")

    terminal = examples["SCHEDULED_TERMINAL_FLATTEN_LONG_BASE"]
    output = {key: Decimal(value) for key, value in terminal["outputs"].items()}
    assert (
        output["final_cash"] - output["preclose_equity"]
        == output["close_impact_vs_preclose_equity"]
    )


def test_inert_evaluation_contract_is_fully_bound() -> None:
    parameters = _json(PARAMETERS)
    assert parameters["protocol_version"] == "0.2.0-proposed"
    assert parameters["latency_and_fill"]["activation_policy"] == ("POST_ONLY_AT_MINUTE_OPEN_PROXY")
    assert parameters["risk"]["account_termination_equity_floor_fraction_of_original_capital"] == (
        "0.80"
    )
    assert parameters["mark_and_funding"]["unusable_event_policy"] == ("INTEGRITY_BLOCKED_UNKNOWN")
    bootstrap = parameters["evaluation"]["bootstrap"]
    assert bootstrap == {
        "method": "OVERLAPPING_MOVING_BLOCK",
        "unit": "ELIGIBLE_CALENDAR_WEEK",
        "block_length_weeks": 4,
        "blocks_may_cross_partition": False,
        "blocks_may_cross_ineligible_week": False,
        "repetitions": 10000,
        "prng": "PCG64",
        "seed": 241,
        "lower_confidence_quantile": "0.025_NEAREST_RANK",
    }
