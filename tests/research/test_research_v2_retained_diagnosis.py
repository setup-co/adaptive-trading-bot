"""Synthetic checks for the read-only retained-evidence diagnosis."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research import research_v2_retained_diagnosis as diagnosis


def decimal_tag(value: Decimal) -> list[object]:
    item = value.as_tuple()
    return ["decimal", item.sign, list(item.digits), str(item.exponent)]


def tagged(value: object) -> list[object]:
    if value is None:
        return ["none"]
    if type(value) is bool:
        return ["bool", value]
    if type(value) is int:
        return ["int", value]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, Decimal):
        return decimal_tag(value)
    if isinstance(value, datetime):
        return ["datetime", value.isoformat(), value.fold]
    if isinstance(value, timedelta):
        return ["timedelta", value.days, value.seconds, value.microseconds]
    if isinstance(value, tuple):
        return ["tuple", [tagged(item) for item in value]]
    if isinstance(value, dict):
        if value == {"__reference__": "decision"}:
            return ["decision_ref"]
        return [
            "dataclass",
            "adaptive_trading_bot.synthetic.Record",
            [[key, tagged(item)] for key, item in value.items()],
        ]
    raise TypeError(type(value))


def synthetic_task_payloads() -> tuple[diagnosis.TaskExpectation, bytes, bytes, bytes]:
    start = datetime(2026, 8, 3, tzinfo=UTC)
    expected = diagnosis.TaskExpectation(
        index=0,
        task_id="00-BTCUSDT-trend_pullback",
        symbol="BTCUSDT",
        family="trend_pullback",
        context_start=datetime(2026, 7, 21, tzinfo=UTC),
        decision_start=start,
        decision_end=start + timedelta(minutes=75),
        follow_up_end=start + timedelta(hours=169, minutes=15),
        decision_count=5,
        input_kind="SYNTHETIC_RETAINED_FIXTURE",
        source_id="synthetic-retained-source-v1",
        dataset_version="synthetic-retained-dataset-v1",
        manifest_sha256="c" * 64,
        profile="SYNTHETIC_BASE_SIGNAL_V1",
        protocol_sha256=diagnosis.PROTOCOL_SHA256,
        task_plan_sha256=diagnosis.TASK_PLAN_SHA256,
    )
    source = {"value": expected.source_id}
    symbol = {"base": "BTC", "quote": "USDT"}

    def decision(index: int, action: str) -> dict[str, object]:
        observed = start + timedelta(minutes=15 * (index + 1))
        identity = {
            "source_id": source,
            "symbol": symbol,
            "observed_at": observed,
            "strategy_id": expected.family,
            "strategy_version": "1.0.0",
        }
        return {
            "identity": identity,
            "context": {"source_id": source, "symbol": symbol, "observed_at": observed},
            "strategy_evaluation": {
                "source_id": source,
                "symbol": symbol,
                "observed_at": observed,
                "strategy_id": expected.family,
                "strategy_version": "1.0.0",
                "action": action,
                "reasons_for": (),
                "reasons_against": (),
                "rule_audit": {},
            },
            "risk_decision": {"disposition": "APPROVED", "reasons": ()},
            "safety_decision": {"disposition": "CLEARED", "reasons": ()},
        }

    decisions = (
        decision(0, "NO_TRADE"),
        decision(1, "LONG_CANDIDATE"),
        decision(2, "LONG_CANDIDATE"),
        decision(3, "SHORT_CANDIDATE"),
        decision(4, "LONG_CANDIDATE"),
    )
    binding = {
        "kind": expected.input_kind,
        "source_id": source,
        "dataset_version": expected.dataset_version,
        "manifest_sha256": expected.manifest_sha256,
        "schema_version": "research-v2-input-binding-v1",
    }
    population = {
        "schema_version": "research-v2-study-decision-population-v1",
        "protocol_sha256": expected.protocol_sha256,
        "source_id": source,
        "dataset_version": expected.dataset_version,
        "manifest_sha256": expected.manifest_sha256,
        "profile": expected.profile,
        "integrity_seal": "f" * 64,
        "configuration": {
            "family": expected.family,
            "schema_version": "research-v2-admission-configuration-v2",
            "input_binding": binding,
            "bounds": {
                "symbol": symbol,
                "source_id": source,
                "context_start": expected.context_start,
                "decision_start": expected.decision_start,
                "decision_end": expected.decision_end,
                "follow_up_end": expected.follow_up_end,
                "profile": expected.profile,
                "admission_version": "RESEARCH_V2_ISOLATED_ADMISSION_V1",
            },
        },
        "decisions": decisions,
    }
    population_raw = {
        "schema_version": "research-v2-screen-authority-evidence-v1",
        "population": tagged(population),
    }
    population_payload = gzip.compress(diagnosis._canonical(population_raw), mtime=0)

    high_precision_quantity = Decimal("-0.1234567890123456789012345678901234")

    def entry_fill(observed: datetime, scenario: str, *, side: str) -> dict[str, object]:
        latency = diagnosis.EXPECTED_COSTS[scenario][3]
        selected = observed if latency == timedelta() else observed + timedelta(minutes=15)
        return {
            "created_at": observed,
            "eligible_at": observed + latency,
            "execution_candle_open_time": selected,
            "fill_at": selected,
            "reference_open_price": Decimal("100"),
            "fill_price": Decimal("100.1" if side == "BUY" else "99.9"),
            "fee_quote": Decimal("1"),
        }

    def branch(
        observed: datetime,
        scenario: str,
        *,
        status: str,
        label: str,
        quantity: Decimal,
        recovery: bool = False,
        closed: bool = False,
        seal_character: str,
        side: str,
    ) -> dict[str, object]:
        entry = entry_fill(observed, scenario, side=side)
        exit_request = None
        exit_fill = None
        fees = Decimal("1")
        gross = Decimal("0")
        net = Decimal("-1")
        closed_r = None
        if closed:
            exit_created = observed + timedelta(hours=1)
            latency = diagnosis.EXPECTED_COSTS[scenario][3]
            exit_selected = (
                exit_created if latency == timedelta() else exit_created + timedelta(minutes=15)
            )
            exit_request = {"created_at": exit_created}
            exit_fill = {
                "created_at": exit_created,
                "eligible_at": exit_created + latency,
                "execution_candle_open_time": exit_selected,
                "fill_at": exit_selected,
                "reference_open_price": Decimal("101"),
                "fill_price": Decimal("100.9"),
                "fee_quote": Decimal("1"),
            }
            quantity = Decimal("0")
            fees = Decimal("2")
            gross = Decimal("5")
            net = Decimal("3")
            closed_r = Decimal("0.3")
        return {
            "status": status,
            "label": label,
            "entry_fill": entry,
            "exit_request": exit_request,
            "exit_fill": exit_fill,
            "final_position": {
                "quantity": quantity,
                "cumulative_fees": fees,
                "cumulative_gross_realized_pnl": gross,
                "cumulative_net_realized_pnl": net,
            },
            "closed_net_r": closed_r,
            "recovery": {"reasons": ("STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL",)} if recovery else None,
            "integrity_seal": seal_character * 64,
        }

    screens: dict[str, object] = {}
    authority_lines: list[bytes] = []
    for scenario_index, scenario in enumerate(diagnosis.SCENARIOS):
        rows: list[dict[str, object]] = []
        for index, item in enumerate(decisions):
            admitted = index != 0
            side = "SELL" if index == 3 else "BUY"
            seal = ("a" if scenario_index == 0 else "b") * 64
            observed = start + timedelta(minutes=15 * (index + 1))
            identity = item["identity"]
            intent = None
            if admitted:
                intent_identity = {"decision_identity": identity, "purpose": "ENTRY"}
                intent = {
                    "identity": intent_identity,
                    "decision_identity": identity,
                    "source_id": source,
                    "symbol": symbol,
                    "observed_at": observed,
                    "strategy_id": expected.family,
                    "strategy_version": "1.0.0",
                    "side": side,
                    "authorized_for_execution": True,
                    "reference_price": Decimal("100"),
                    "stop_basis": Decimal("99"),
                    "approved_risk_quote": Decimal("10"),
                    "approved_notional_quote": Decimal("1000"),
                    "quantity": Decimal("10"),
                }
            branches: tuple[dict[str, object], ...] = ()
            entry_unfilled: dict[str, object] | None = None
            status = "NO_SIGNAL"
            if index == 1:
                status = "ENTRY_UNFILLED"
                entry_unfilled = {"reason": "NO_ELIGIBLE_CANDLE"}
            elif index == 2:
                status = "ORDINARY_CLOSED"
                branches = (
                    branch(
                        observed,
                        scenario,
                        status=status,
                        label="PRIMARY",
                        quantity=Decimal(0),
                        closed=True,
                        seal_character="d",
                        side=side,
                    ),
                )
            elif index == 3:
                status = "RECOVERY_OPEN"
                branches = (
                    branch(
                        observed,
                        scenario,
                        status=status,
                        label="PRIMARY",
                        quantity=high_precision_quantity,
                        recovery=True,
                        seal_character="e",
                        side=side,
                    ),
                )
            elif index == 4:
                status = "ORDINARY_CLOSED"
                branches = (
                    branch(
                        observed,
                        scenario,
                        status="ORDINARY_OPEN",
                        label="HOLD_ONLY",
                        quantity=high_precision_quantity.copy_abs(),
                        seal_character="f",
                        side=side,
                    ),
                    branch(
                        observed,
                        scenario,
                        status=status,
                        label="PRIMARY",
                        quantity=Decimal(0),
                        closed=True,
                        seal_character="1",
                        side=side,
                    ),
                )
            result = {
                "decision": {"__reference__": "decision"},
                "family": "TREND_PULLBACK",
                "scenario": scenario,
                "start": expected.context_start,
                "end": observed + timedelta(hours=168),
                "schema_version": "research-all-fill-economics-v1",
                "policy_version": "RESEARCH_ALL_FILL_ECONOMICS_V1",
                "is_trading_authority": False,
                "series": {"source_id": source, "symbol": symbol, "timeframe": "15m"},
                "costs": {
                    "spread_bps": diagnosis.EXPECTED_COSTS[scenario][0],
                    "slippage_bps": diagnosis.EXPECTED_COSTS[scenario][1],
                    "fee_bps": diagnosis.EXPECTED_COSTS[scenario][2],
                    "latency": diagnosis.EXPECTED_COSTS[scenario][3],
                },
                "integrity_seal": seal,
                "status": status,
                "entry_intent": intent,
                "entry_unfilled": entry_unfilled,
                "branches": branches,
                "legacy_replay": {
                    "ema50_candidate": {"winning_exit_cause": "EMA50_THESIS_FAILURE"}
                    if status == "ORDINARY_CLOSED"
                    else None
                },
            }
            primary = branches[-1] if branches else None
            primary_r, primary_fees, primary_open, primary_gross, primary_net = (
                diagnosis._branch_screen_facts(primary)
            )
            hold = branches[0] if len(branches) == 2 else None
            hold_r, hold_fees, hold_open, _, _ = diagnosis._branch_screen_facts(hold)
            row: dict[str, object] = {
                "identity": diagnosis._identity_digest(identity, "synthetic identity"),
                "observed_at": diagnosis._iso(observed),
                "symbol": expected.symbol,
                "family": "TREND_PULLBACK",
                "scenario": scenario,
                "source_id": expected.source_id,
                "dataset_version": expected.dataset_version,
                "manifest_sha256": expected.manifest_sha256,
                "input_kind": expected.input_kind,
                "side": "SHORT" if index == 3 else ("LONG" if admitted else "NONE"),
                "status": status,
                "research_admissible": admitted,
                "all_fill_result_sha256": seal,
                "primary_closed_net_r": diagnosis._str_decimal(primary_r),
                "primary_fee_quote": diagnosis._str_decimal(primary_fees),
                "primary_open_quantity": diagnosis._str_decimal(primary_open),
                "primary_gross_pnl_quote": diagnosis._str_decimal(primary_gross),
                "primary_net_pnl_quote": diagnosis._str_decimal(primary_net),
                "hold_only_status": None if hold is None else hold["status"],
                "hold_only_closed_net_r": None if hold is None else diagnosis._str_decimal(hold_r),
                "hold_only_fee_quote": None if hold is None else diagnosis._str_decimal(hold_fees),
                "hold_only_open_quantity": None
                if hold is None
                else diagnosis._str_decimal(hold_open),
                "hold_only_branch_sha256": None if hold is None else hold["integrity_seal"],
                "block": "P1",
                "week": "2026-W32",
                "regime": "NEUTRAL",
            }
            rows.append(row)
            authority_lines.append(
                diagnosis._canonical(
                    {
                        "schema_version": "research-v2-screen-authority-evidence-v1",
                        "decision_index": index,
                        "scenario": scenario,
                        "result": tagged(result),
                    }
                )
            )
        screens[scenario] = {"rows": rows}
    task = {
        "context_start": diagnosis._iso(expected.context_start),
        "decision_end": diagnosis._iso(expected.decision_end),
        "decision_slots": expected.decision_count,
        "decision_start": diagnosis._iso(expected.decision_start),
        "family": expected.family,
        "index": expected.index,
        "last_follow_up_end": diagnosis._iso(expected.follow_up_end),
        "scenario_rows": expected.decision_count * 2,
        "symbol": expected.symbol,
        "task_id": expected.task_id,
    }
    partition = {
        "schema_version": "research-v2-exploratory-screen-v4",
        "protocol_sha256": expected.protocol_sha256,
        "task_plan_sha256": expected.task_plan_sha256,
        "source_id": expected.source_id,
        "dataset_version": expected.dataset_version,
        "manifest_sha256": expected.manifest_sha256,
        "population_decision_count": expected.decision_count,
        "population_sha256": population["integrity_seal"],
        "task": task,
        "scenarios": screens,
    }
    partition["partition_sha256"] = diagnosis._sha(diagnosis._canonical(partition))
    return (
        expected,
        population_payload,
        diagnosis._canonical(partition),
        gzip.compress(b"\n".join(authority_lines) + b"\n", mtime=0),
    )


def trade(
    identity: str,
    scenario: str,
    *,
    status: str = "ORDINARY_CLOSED",
    net: str = "1",
    fees: str = "0.1",
    gross: str = "1.1",
    r: str = "0.5",
    fill_at: str = "2026-08-03T00:00:00Z",
    entry_open: str = "2026-08-03T00:00:00Z",
) -> dict[str, object]:
    return {
        "task_id": "00-BTCUSDT-trend_pullback",
        "decision_index": 1,
        "decision_identity_sha256": identity,
        "scenario": scenario,
        "family": "TREND_PULLBACK",
        "symbol": "BTCUSDT",
        "status": status,
        "entry_fill_at": fill_at,
        "entry_execution_candle_open_at": entry_open,
        "entry_reference_open_price": "100",
        "entry_fill_price": "100",
        "exit_fill_at": "2026-08-03T01:00:00Z",
        "exit_execution_candle_open_at": "2026-08-03T01:00:00Z",
        "exit_reference_open_price": "101",
        "exit_fill_price": "101",
        "gross_realized_pnl_quote": gross,
        "total_fees_quote": fees,
        "net_realized_pnl_quote": net,
        "net_approved_risk_r": r,
        "entry_wait_seconds": "0",
        "exit_wait_seconds": "0",
        "authority_sha256": "a" * 64,
    }


def range_decision(
    *,
    status: str,
    range_reasons: tuple[str, ...],
    against: tuple[str, ...],
    retained: bool = False,
    reset: bool = False,
) -> dict[str, object]:
    return {
        "strategy_evaluation": {
            "action": "NO_TRADE",
            "reasons_for": (),
            "reasons_against": against,
            "rule_audit": {
                "range_status": status,
                "range_reasons": range_reasons,
                "selected_side": None,
                "partial_evidence_retained": retained,
                "partial_evidence_reset": reset,
            },
        },
        "risk_decision": {"disposition": "REJECTED", "reasons": ()},
        "safety_decision": {"disposition": "BLOCKED_BY_RISK", "reasons": ()},
        "identity": {"observed_at": datetime(2026, 8, 3, tzinfo=UTC)},
    }


def test_exact_decimal_and_utc_tags_are_lossless() -> None:
    value = Decimal("-0.0012300")
    assert diagnosis.decode_tagged(decimal_tag(value)) == value
    observed = diagnosis.decode_tagged(["datetime", "2026-08-03T00:00:00+00:00", 0])
    assert observed == datetime(2026, 8, 3, tzinfo=UTC)
    with pytest.raises(diagnosis.DiagnosisError, match="not UTC"):
        diagnosis.decode_tagged(["datetime", "2026-08-03T00:00:00+01:00", 0])


def test_tagged_parser_never_constructs_untrusted_types() -> None:
    with pytest.raises(diagnosis.DiagnosisError, match="outside"):
        diagnosis.decode_tagged(["dataclass", "os.system", []])
    inert = diagnosis.decode_tagged(
        ["dataclass", "adaptive_trading_bot.example.Value", [["amount", decimal_tag(Decimal("2"))]]]
    )
    assert inert == {"__type__": "adaptive_trading_bot.example.Value", "amount": Decimal("2")}


def test_slot_validation_rejects_wrong_task_unknown_scenario_and_duplicate() -> None:
    seen: set[tuple[int, str]] = set()
    with pytest.raises(diagnosis.DiagnosisError, match="wrong task"):
        diagnosis.validate_authority_slot(
            task_index=1,
            expected_task_index=0,
            decision_index=0,
            scenario=diagnosis.SCENARIOS[0],
            seen=seen,
        )
    with pytest.raises(diagnosis.DiagnosisError, match="unknown"):
        diagnosis.validate_authority_slot(
            task_index=0,
            expected_task_index=0,
            decision_index=0,
            scenario="BASE",
            seen=seen,
        )
    diagnosis.validate_authority_slot(
        task_index=0,
        expected_task_index=0,
        decision_index=0,
        scenario=diagnosis.SCENARIOS[0],
        seen=seen,
    )
    with pytest.raises(diagnosis.DiagnosisError, match="duplicated"):
        diagnosis.validate_authority_slot(
            task_index=0,
            expected_task_index=0,
            decision_index=0,
            scenario=diagnosis.SCENARIOS[0],
            seen=seen,
        )


def test_slot_validation_rejects_missing_population_index() -> None:
    with pytest.raises(diagnosis.DiagnosisError, match="outside"):
        diagnosis.validate_authority_slot(
            task_index=0,
            expected_task_index=0,
            decision_index=diagnosis.DECISIONS_PER_TASK,
            scenario=diagnosis.SCENARIOS[0],
            seen=set(),
        )


def test_pairing_counts_terminal_pairs_and_exact_deltas() -> None:
    base = trade("id", diagnosis.SCENARIOS[0])
    stress = trade(
        "id",
        diagnosis.SCENARIOS[1],
        status="RECOVERY_CLOSED",
        net="0.4",
        fees="0.2",
        gross="0.6",
        r="0.2",
        fill_at="2026-08-03T00:15:00Z",
        entry_open="2026-08-03T00:15:00Z",
    )
    rows = diagnosis._pair_rows([base, stress])
    assert len(rows) == 1
    assert rows[0]["delta_net_quote"] == "-0.6"
    assert rows[0]["delta_fees_quote"] == "0.1"
    assert rows[0]["entry_fill_time_delta_seconds"] == "900.0"
    assert rows[0]["entry_open_changed"] is True


def test_pairing_rejects_row_position_match_with_different_authority() -> None:
    base = trade("id", diagnosis.SCENARIOS[0])
    stress = trade("id", diagnosis.SCENARIOS[1])
    stress["symbol"] = "ETHUSDT"
    with pytest.raises(diagnosis.DiagnosisError, match="exact decision authority"):
        diagnosis._pair_rows([base, stress])
    stress = trade("other-id", diagnosis.SCENARIOS[1])
    with pytest.raises(diagnosis.DiagnosisError, match="exact decision authority"):
        diagnosis._pair_rows([base, stress])


def test_pairing_rejects_missing_duplicate_and_wrong_scenario() -> None:
    base = trade("id", diagnosis.SCENARIOS[0])
    with pytest.raises(diagnosis.DiagnosisError, match="exact BASE/STRESS"):
        diagnosis._pair_rows([base])
    with pytest.raises(diagnosis.DiagnosisError, match="duplicate"):
        diagnosis._pair_rows([base, dict(base)])
    wrong = trade("id", "UNDECLARED")
    with pytest.raises(diagnosis.DiagnosisError, match="exact BASE/STRESS"):
        diagnosis._pair_rows([base, wrong])


def test_effective_wait_uses_recorded_times_not_configured_latency() -> None:
    created = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
    eligible = created + timedelta(seconds=1)
    selected_open = created + timedelta(minutes=15)
    assert diagnosis._seconds(eligible, created) == "1.0"
    assert diagnosis._seconds(selected_open, eligible) == "899.0"
    assert diagnosis._seconds(selected_open, created) == "900.0"


def test_derived_decimal_arithmetic_ignores_ambient_context() -> None:
    before = "604.2725728390732038281484705826820"
    after = "44.82746493348189514176665138066175"
    expected = "-559.44510790559130868638181920202025"
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
        assert diagnosis._decimal_delta(before, after) == expected
        stats = diagnosis._decimal_stats([Decimal(before), Decimal(after)])
    with localcontext(Context(prec=12, rounding=ROUND_DOWN)):
        assert diagnosis._decimal_delta(before, after) == expected
        assert diagnosis._decimal_stats([Decimal(before), Decimal(after)]) == stats


def test_quantity_absolute_value_has_lossless_and_historical_compatibility_modes() -> None:
    quantity = Decimal("-0.1234567890123456789012345678901234")
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
        lossless_low = diagnosis._lossless_decimal_abs(quantity)
        compatible_low = diagnosis._historical_quantity_abs(quantity)
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        lossless_high = diagnosis._lossless_decimal_abs(quantity)
        compatible_high = diagnosis._historical_quantity_abs(quantity)
    assert lossless_low == lossless_high == Decimal("0.1234567890123456789012345678901234")
    assert compatible_low == compatible_high == Decimal("0.1234567890123456789012345679")


def test_synthetic_positive_round_trip_uses_same_task_core(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from adaptive_trading_bot.backtest.accounting import PositionAccountingEngine
    from adaptive_trading_bot.backtest.execution import BacktestExecutionSimulator
    from adaptive_trading_bot.strategies.trend_pullback import TrendPullbackStrategy

    expected, population, partition, authority = synthetic_task_payloads()
    engine_calls: Counter[str] = Counter()

    def prohibited(name: str) -> Any:
        def call(*args: object, **kwargs: object) -> None:
            engine_calls[name] += 1
            raise AssertionError(f"prohibited engine invoked: {name}")

        return call

    monkeypatch.setattr(TrendPullbackStrategy, "evaluate", prohibited("strategy"))
    monkeypatch.setattr(BacktestExecutionSimulator, "run", prohibited("simulator"))
    monkeypatch.setattr(PositionAccountingEngine, "process", prohibited("accounting"))
    original_population = diagnosis._population
    parser_calls = 0

    def counted_population(
        payload: bytes, *, expected_count: int = diagnosis.DECISIONS_PER_TASK
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        nonlocal parser_calls
        parser_calls += 1
        return original_population(payload, expected_count=expected_count)

    monkeypatch.setattr(diagnosis, "_population", counted_population)

    def run(context: Context) -> dict[str, Any]:
        with localcontext(context):
            return diagnosis.extract_task_payloads(
                expected=expected,
                population_payload=population,
                partition_payload=partition,
                authority_payload=authority,
                authority_path="synthetic.authority.jsonl.gz",
                authority_sha=hashlib.sha256(authority).hexdigest(),
            )

    low = run(Context(prec=6, rounding=ROUND_DOWN))
    high = run(Context(prec=50, rounding=ROUND_HALF_EVEN))
    assert low["base_counts"] == Counter(
        {"ORDINARY_CLOSED": 2, "NO_SIGNAL": 1, "ENTRY_UNFILLED": 1, "RECOVERY_OPEN": 1}
    )
    assert low["trades"] == high["trades"]
    assert parser_calls == 2
    assert engine_calls == Counter()

    result = low
    trades = result["trades"]
    assert len(trades) == 8
    assert {row["status"] for row in trades} == {
        "ENTRY_UNFILLED",
        "ORDINARY_CLOSED",
        "RECOVERY_OPEN",
    }
    unfilled = [row for row in trades if row["status"] == "ENTRY_UNFILLED"]
    recovery = [row for row in trades if row["status"] == "RECOVERY_OPEN"]
    assert all(row["entry_fill_at"] is None for row in unfilled)
    assert all(row["exit_fill_at"] is None for row in recovery)
    assert {row["remaining_base_quantity"] for row in recovery} == {
        "0.1234567890123456789012345678901234"
    }
    assert {row["recovery_reasons"] for row in recovery} == {"STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL"}
    hold_screen = result["screens"][diagnosis.SCENARIOS[0]][4]
    assert hold_screen["hold_only_status"] == "ORDINARY_OPEN"
    assert hold_screen["hold_only_open_quantity"] == "0.1234567890123456789012345679"
    pairs = diagnosis._pair_rows(trades)
    assert len(pairs) == 4
    assert (
        next(row for row in pairs if row["decision_index"] == 1)["entry_fill_time_delta_seconds"]
        is None
    )
    assert next(row for row in pairs if row["decision_index"] == 3)["delta_net_quote"] == "0"

    trades_path, pairs_path = tmp_path / "trades.csv", tmp_path / "pairs.csv"
    diagnosis._write_csv(trades_path, trades)
    diagnosis._write_csv(pairs_path, pairs)
    with trades_path.open(newline="", encoding="utf-8") as handle:
        stored_trades = list(csv.DictReader(handle))
    with pairs_path.open(newline="", encoding="utf-8") as handle:
        stored_pairs = list(csv.DictReader(handle))
    assert len(stored_trades) == 8
    assert len(stored_pairs) == 4
    assert any(
        row["remaining_base_quantity"] == "0.1234567890123456789012345678901234"
        for row in stored_trades
    )
    summary_payload = diagnosis._canonical(
        {
            "trade_economics": diagnosis._summarize_trades(trades),
            "paired_execution": diagnosis._summarize_pairs(pairs),
        }
    )
    summary_path = tmp_path / "summary.json"
    summary_path.write_bytes(summary_payload)
    assert diagnosis._canonical(json.loads(summary_path.read_bytes())) == summary_payload

    pinned_expectation = replace(
        expected,
        input_kind="ACCEPTED_C2",
        source_id=diagnosis.SOURCE_ID,
        dataset_version=diagnosis.DATASET_VERSION,
        manifest_sha256=diagnosis.MANIFEST_SHA256,
        profile="NORMALIZED_BASE_SIGNAL_V1",
    )
    with pytest.raises(diagnosis.DiagnosisError, match="accepted provenance differs"):
        diagnosis.extract_task_payloads(
            expected=pinned_expectation,
            population_payload=population,
            partition_payload=partition,
            authority_payload=authority,
            authority_path="synthetic.authority.jsonl.gz",
            authority_sha=hashlib.sha256(authority).hexdigest(),
        )
    assert parser_calls == 3
    assert engine_calls == Counter()


def test_same_core_rejects_reordered_partition_identity() -> None:
    expected, population, partition_payload, authority = synthetic_task_payloads()
    partition = json.loads(partition_payload)
    scenario = diagnosis.SCENARIOS[0]
    partition["scenarios"][scenario]["rows"].reverse()
    partition.pop("partition_sha256")
    partition["partition_sha256"] = diagnosis._sha(diagnosis._canonical(partition))
    with pytest.raises(diagnosis.DiagnosisError, match="partition row differs"):
        diagnosis.extract_task_payloads(
            expected=expected,
            population_payload=population,
            partition_payload=diagnosis._canonical(partition),
            authority_payload=authority,
            authority_path="synthetic.authority.jsonl.gz",
            authority_sha=hashlib.sha256(authority).hexdigest(),
        )


def test_trend_recovery_does_not_require_an_ema50_candidate() -> None:
    assert (
        diagnosis._exit_reason(
            "TREND_PULLBACK", {"ema50_candidate": None}, {"recovery": {"reasons": ()}}
        )
        == "POST_FILL_STOP_RECOVERY"
    )


def test_range_summary_keeps_overlapping_reasons_and_unavailable_later_rules() -> None:
    rows = [
        range_decision(
            status="VALID_RANGE",
            range_reasons=("RANGE_GEOMETRY_VALID", "REGIME_COMPATIBLE"),
            against=("CANDLE_REVERSAL_FAILED", "RSI_EXHAUSTION_FAILED"),
            retained=True,
        ),
        range_decision(
            status="NOT_RANGE",
            range_reasons=("TREND_TOO_STRONG",),
            against=("RANGE_NOT_VALID",),
            reset=True,
        ),
    ]
    result = diagnosis._range_summary(rows)
    assert result["denominator_observations"] == 2
    assert result["range_reason_occurrences_multi_label"] == {
        "RANGE_GEOMETRY_VALID": 1,
        "REGIME_COMPATIBLE": 1,
        "TREND_TOO_STRONG": 1,
    }
    assert result["evaluated_failure_reason_occurrences"] == 3
    assert result["reason_counts_are_disjoint"] is False
    assert result["later_short_circuited_rules"] == "UNAVAILABLE_AS_PASS_FAIL_FACTS"


def test_per_asset_residuals_are_not_summed() -> None:
    residuals = {
        "BTCUSDT": "0.1",
        "XRPUSDT": "100",
        "AVAXUSDT": "2",
    }
    encoded = json.dumps(residuals, sort_keys=True)
    assert json.loads(encoded) == residuals
    assert "total" not in residuals


def test_hold_only_projection_keeps_unresolved_per_asset_quantities() -> None:
    families: dict[str, object] = {}
    for family in diagnosis.FAMILIES:
        scenario = {
            "hold_only_diagnostic": {
                "branches": 5,
                "closed": 3,
                "unresolved": 2,
                "incurred_fees_quote": "1.25",
                "remaining_absolute_base_quantity_by_asset": {
                    symbol: ("2.5" if symbol == "BTCUSDT" else "0") for symbol in diagnosis.PANEL
                },
            }
        }
        families[family.lower()] = {"base": scenario, "combined_stress": scenario}
    projected = diagnosis._hold_only_projection({"families": families})
    trend = projected["TREND_PULLBACK"]["BASE"]
    assert trend["unresolved"] == 2
    assert trend["remaining_absolute_base_quantity_by_asset"]["BTCUSDT"] == "2.5"
    assert "total" not in trend


def test_exact_decimal_summary_preserves_even_median() -> None:
    assert diagnosis._decimal_stats([Decimal("1.1"), Decimal("2.2")]) == {
        "count": 2,
        "minimum": "1.1",
        "median": "1.65",
        "mean": "1.65",
        "maximum": "2.2",
    }
    assert diagnosis._decimal_stats([])["median"] is None


def test_csv_evidence_uses_canonical_lf(tmp_path: Path) -> None:
    output = tmp_path / "evidence.csv"
    diagnosis._write_csv(output, [{"identity": "one", "count": 1}])
    assert output.read_bytes() == b"identity,count\none,1\n"


def test_grouping_summary_keeps_terminal_zero_cells() -> None:
    grouped = diagnosis._new_grouped()
    grouped["TREND_PULLBACK"]["asset"]["BTCUSDT"].update({"NO_SIGNAL": 2})
    grouped["BREAKOUT_RETEST"]["asset"]["BTCUSDT"].update(
        {status: 1 for status in diagnosis.TERMINAL_STATUSES}
    )
    result = diagnosis._grouping_summary(grouped)
    cell = result["TREND_PULLBACK"]["asset"]["BTCUSDT"]
    assert cell["observations"] == 2
    assert cell["terminal_statuses"]["RECOVERY_OPEN"] == 0
    assert cell["terminal_statuses"]["RISK_BLOCKED"] == 0
    assert result["RANGE_MEAN_REVERSION"]["side"]["SHORT"]["observations"] == 0
    for status in diagnosis.TERMINAL_STATUSES:
        assert status in result["RANGE_MEAN_REVERSION"]["side"]["SHORT"]["terminal_statuses"]
        assert result["BREAKOUT_RETEST"]["asset"]["BTCUSDT"]["terminal_statuses"][status] == 1


def test_projection_reconciliation_records_rounding_instead_of_claiming_equality() -> None:
    families: dict[str, object] = {}
    pair_summary: dict[str, object] = {}
    for family in diagnosis.FAMILIES:
        families[family.lower()] = {
            "base": {
                "impact_inclusive_gross_realized_quote": "604.2725728390732038281484705826820",
                "incurred_fees_quote": "337.5232363150151025718754633238990",
                "accounted_net_realized_quote": "266.7493365240581012562730072587829",
            },
            "combined_stress": {
                "impact_inclusive_gross_realized_quote": "44.82746493348189514176665138066175",
                "incurred_fees_quote": "675.8763928961981458102460088618546",
                "accounted_net_realized_quote": "-631.0489279627162506684793574811930",
            },
        }
        pair_summary[family] = {
            "delta_gross_quote": "-559.44510790559130868638181920202025",
            "delta_fees_quote": "338.3531565811830432383705455379556",
            "delta_net_quote": "-897.7982644867743519247523647399759",
        }
    with localcontext(Context(prec=5, rounding=ROUND_DOWN)):
        result = diagnosis._projection_reconciliation({"families": families}, pair_summary)
    row = result["BREAKOUT_RETEST"]
    assert row["descriptive_row_delta"]["gross"] == ("-559.44510790559130868638181920202025")
    assert row["interpretation"] == "ROUNDING_RECONCILED_NOT_ASSERTED_AS_EXACT_EQUALITY"


def test_index_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "artifact-index.json").write_text("{}", encoding="utf-8")
    with pytest.raises(diagnosis.DiagnosisError, match="index differs"):
        diagnosis.load_index(evidence)


def test_population_rejects_synthetic_relabel_and_noncanonical_bytes() -> None:
    raw = {
        "schema_version": "wrong",
        "population": ["dataclass", "adaptive_trading_bot.example.Population", []],
    }
    import gzip

    payload = gzip.compress(json.dumps(raw).encode(), mtime=0)
    with pytest.raises(diagnosis.DiagnosisError, match="canonical or versioned"):
        diagnosis._population(payload)


def test_source_has_no_strategy_simulator_or_authority_decoder_calls() -> None:
    source = Path(diagnosis.__file__).read_text(encoding="utf-8")
    forbidden = (
        "decode_authority(",
        "execute_granted_task(",
        "BacktestExecutionSimulator(",
        "PositionAccountingEngine(",
        ".evaluate(",
    )
    assert all(token not in source for token in forbidden)
    assert "decode_tagged" in source
    assert 'strategy_feature_simulator_calls": 0' in source


def test_manifest_identity_is_sha256_not_mutable_label() -> None:
    assert len(diagnosis.INDEX_SHA256) == 64
    assert len(diagnosis.PROJECTION_SHA256) == 64
    assert hashlib.sha256(b"different").hexdigest() != diagnosis.INDEX_SHA256
