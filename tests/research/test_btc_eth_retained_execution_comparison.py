from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from decimal import getcontext
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location(
    "btc_eth_retained_execution_comparison",
    ROOT / "scripts/research/btc_eth_retained_execution_comparison.py",
)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subject
SPEC.loader.exec_module(subject)


def tagged(value: Any) -> Any:
    if value is None:
        return ["none"]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat(), value.fold]
    raise TypeError(value)


def dataclass_tag(name: str, **fields: Any) -> list[Any]:
    return ["dataclass", name, [[key, value] for key, value in fields.items()]]


def enum(value: str) -> list[Any]:
    return ["enum", "example.Enum", ["str", value]]


def raw_record(*, observed: datetime | None = None, action: str = "LONG_CANDIDATE") -> list[Any]:
    observed = observed or datetime(2026, 4, 10, 0, 15, tzinfo=UTC)
    identity = dataclass_tag(
        "example.Identity",
        source_id=enum("source"),
        symbol=dataclass_tag("example.Symbol"),
        observed_at=tagged(observed),
        strategy_id=tagged("breakout_retest"),
        strategy_version=tagged("1.0.0"),
    )
    decision = dataclass_tag(
        "example.Decision",
        context=["none"],
        strategy_evaluation=dataclass_tag("example.Evaluation", action=enum(action)),
        risk_decision=dataclass_tag("example.Risk", status=enum("APPROVED")),
        safety_decision=dataclass_tag("example.Safety", status=enum("CLEARED")),
        identity=identity,
    )
    return dataclass_tag(
        "example.Result",
        decision=decision,
        arm=enum("BASELINE"),
        scenario=enum("BASE"),
        task_id=tagged("task"),
        population_sha256=tagged("a" * 64),
    )


def outcome(r: str | None, *, status: str = "ORDINARY_CLOSED") -> dict[str, object]:
    return {
        "status": status,
        "filled": status != "ENTRY_UNFILLED",
        "closed": r is not None,
        "recovered": status == "INVALID_GEOMETRY_RECOVERED",
        "unfilled": status == "ENTRY_UNFILLED",
        "unresolved": r is None and status != "ENTRY_UNFILLED",
        "closed_net_r": r,
        "gross_pnl_quote": "10",
        "fees_quote": "1",
        "net_pnl_quote": "9",
        "holding_upper_seconds": "300" if r is not None else None,
        "ambiguous": False,
        "gap": False,
        "entry_grid_wait_seconds": "0",
    }


def test_raw_candidate_facts_are_bounded_and_preserve_decision_identity() -> None:
    facts = subject._raw_facts(raw_record())
    assert facts["arm"] == "BASELINE"
    assert facts["scenario"] == "BASE"
    assert facts["action"] == "LONG_CANDIDATE"
    assert facts["risk"] == "APPROVED"
    assert facts["safety"] == "CLEARED"
    assert facts["observed_at"] == datetime(2026, 4, 10, 0, 15, tzinfo=UTC)


def test_duplicate_or_missing_canonical_fields_fail_closed() -> None:
    raw = raw_record()
    raw[2] = [pair for pair in raw[2] if pair[0] != "task_id"]
    with pytest.raises(subject.ComparisonError, match="task_id"):
        subject._raw_facts(raw)


def test_spacing_mask_is_frozen_before_outcomes() -> None:
    start = datetime(2026, 4, 10, tzinfo=UTC)
    values = [start, start + timedelta(hours=1), start + timedelta(hours=168)]
    assert subject._mask(values) == {start, start + timedelta(hours=168)}


def test_exact_aggregation_and_pairing_are_decimal_context_independent() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 5
        rows = [outcome("1.25"), outcome("-0.5"), outcome(None, status="TERMINAL_OPEN")]
        result = subject._aggregate(rows)
        paired = subject._paired(
            [
                {"legacy": outcome("-1"), "new": outcome("1.5")},
                {
                    "legacy": outcome(None, status="ENTRY_UNFILLED"),
                    "new": outcome(None, status="TERMINAL_OPEN"),
                },
            ]
        )
    finally:
        getcontext().prec = original
    assert result["net_r_mean"] == "0.375"
    assert result["r_profit_factor"] == "2.5"
    assert paired["new_minus_old_closed_net_r_mean"] == "2.5"
    assert paired["state_partitions"]["OLD_ENTRY_UNFILLED|NEW_TERMINAL_OPEN"] == 1


def test_budget_timeout_and_partial_rows_are_durable(tmp_path: Path) -> None:
    state = subject.RunState(tmp_path, 0.0)
    subject._append_jsonl(tmp_path / "rows.jsonl", {"completed": 1})
    with pytest.raises(subject.BudgetExceeded):
        state.check()
    assert (tmp_path / "rows.jsonl").read_text() == '{"completed":1}\n'


def test_expected_grid_is_exact_and_complete() -> None:
    values = subject._expected_times()
    assert len(values) == 17_280
    assert values[0] == subject.SIGNAL_START
    assert values[-1] == subject.SIGNAL_END_INCLUSIVE
