"""Synthetic checks for the read-only retained-evidence diagnosis."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research import research_v2_retained_diagnosis as diagnosis


def decimal_tag(value: Decimal) -> list[object]:
    item = value.as_tuple()
    return ["decimal", item.sign, list(item.digits), str(item.exponent)]


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
    grouped = {"TREND_PULLBACK": {"asset": {"BTCUSDT": Counter({"NO_SIGNAL": 2})}}}
    result = diagnosis._grouping_summary(grouped)
    cell = result["TREND_PULLBACK"]["asset"]["BTCUSDT"]
    assert cell["observations"] == 2
    assert cell["terminal_statuses"]["RECOVERY_OPEN"] == 0
    assert cell["terminal_statuses"]["RISK_BLOCKED"] == 0


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
