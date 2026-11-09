from __future__ import annotations

import importlib.util
import json
import runpy
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from adaptive_trading_bot.execution import (  # noqa: E402
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, EntrySide  # noqa: E402
from adaptive_trading_bot.research.breakout_history_screened_comparison import (  # noqa: E402
    ComparisonArm,
    ComparisonScenario,
    scenario_costs,
)
from adaptive_trading_bot.research.breakout_history_screened_execution import (  # noqa: E402
    execute_task,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import (  # noqa: E402
    HistoryScreenedLedgerStore,
)
from adaptive_trading_bot.research.breakout_retest_replay import fixed_2r_geometry  # noqa: E402
from adaptive_trading_bot.research.last_5m_protective_execution import (  # noqa: E402
    ExitPolicy,
    Last5mBar,
    Last5mExecutionBinding,
    Last5mExecutionStatus,
    Last5mProtectionConfiguration,
    ProtectionAcknowledgement,
    TriggerBasis,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.storage import SourceId  # noqa: E402

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


def test_actual_ledger_codec_authorizer_profile_and_output_round_trip(tmp_path: Path) -> None:
    helpers = runpy.run_path(
        str(Path(__file__).with_name("test_breakout_history_screened_comparison.py"))
    )
    store, configuration = helpers["_synthetic"]()
    grant = helpers["_grant"](configuration.input_binding, "actual-driver-path")
    executable = "1" * 40
    ledger = HistoryScreenedLedgerStore.create(
        tmp_path / "ledger-root",
        grant,
        actual_sha=executable,
        synthetic_task_ids=(configuration.task.task_id,),
    )
    ledger.start(0)
    artifacts = execute_task(
        store,
        configuration,
        grant=grant,
        actual_sha=executable,
        directory=ledger.directory,
        task_index=0,
        synthetic=True,
    )
    ledger.complete(
        0,
        population_sha256=artifacts.population_sha256,
        results_sha256=artifacts.results_sha256,
        report_sha256=artifacts.report_sha256,
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    retained = evidence / "original-ledger"
    ledger.directory.rename(retained)
    rows: list[dict[str, object]] = []
    for path in sorted(retained.iterdir()):
        if not path.is_file():
            continue
        body = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(evidence).as_posix(),
                "bytes": len(body),
                "sha256": sha256(body).hexdigest(),
            }
        )
    final_event = retained / "0002.json"
    events = subject._ledger_events(
        evidence,
        rows,
        expected_final_sha=sha256(final_event.read_bytes()).hexdigest(),
        expected_revision=2,
    )
    assert events[-1]["event"] == "COMPLETED"
    sources = subject._select_task_inputs(evidence, rows, events, (configuration.task,))
    source = sources[configuration.task.wire_symbol]
    report = json.loads(Path(source["report"]).read_bytes())
    expected_times = tuple(
        configuration.task.signal_start + (index + 1) * timedelta(minutes=15)
        for index in range(report["decision_slots_per_arm"])
    )
    state = subject.RunState(tmp_path / "output", time.monotonic() + 180)
    state.output.mkdir()
    typed, _ = subject._stream_task(source, report, state, expected_times=expected_times)
    authorized = next(
        value
        for (arm, scenario, _), value in typed.items()
        if arm == ComparisonArm.BASELINE.value
        and scenario == ComparisonScenario.BASE.value
        and EntryOrderIntentAuthorizer().authorize(value.decision).intent is not None
    )
    intent = EntryOrderIntentAuthorizer().authorize(authorized.decision).intent
    assert intent is not None
    opened = intent.authorized_at
    source_id = SourceId(intent.source_id.value)
    epoch = "synthetic-profile-epoch"

    def bar(
        index: int,
        price: Decimal,
        *,
        high: Decimal | None = None,
        low: Decimal | None = None,
    ) -> Last5mBar:
        value = price
        high_value = value if high is None else high
        low_value = value if low is None else low
        start = opened + index * timedelta(minutes=5)
        return Last5mBar(
            source_id,
            intent.symbol,
            epoch,
            start,
            start + timedelta(minutes=5),
            value,
            max(value, high_value),
            min(value, low_value),
            value,
            Decimal(1),
        )

    base_costs = scenario_costs(ComparisonScenario.BASE)
    side = (
        SimulatedExecutionSide.BUY if intent.side is EntrySide.BUY else SimulatedExecutionSide.SELL
    )
    priced = price_simulated_market_fill(
        side=side,
        quantity=intent.quantity,
        reference_price=intent.reference_price,
        costs=base_costs,
    )
    _, target = fixed_2r_geometry(intent.side, priced.fill_price, intent.stop_basis)
    neutral = intent.reference_price
    target_bar = bar(1, neutral, high=max(neutral, target), low=min(neutral, target))
    ordinary_bars = (bar(0, neutral), target_bar)
    ordinary = run_last_5m_protective_execution(
        intent=intent,
        binding=Last5mExecutionBinding(
            source_id, intent.symbol, epoch, opened, opened + timedelta(minutes=10)
        ),
        bars=ordinary_bars,
        configuration=Last5mProtectionConfiguration(base_costs, ExitPolicy.FIXED_2R),
    )
    open_result = run_last_5m_protective_execution(
        intent=intent,
        binding=Last5mExecutionBinding(
            source_id, intent.symbol, epoch, opened, opened + timedelta(minutes=10)
        ),
        bars=(bar(0, neutral), bar(1, neutral)),
        configuration=Last5mProtectionConfiguration(base_costs, ExitPolicy.FIXED_2R),
    )
    unfilled = run_last_5m_protective_execution(
        intent=intent,
        binding=Last5mExecutionBinding(
            source_id, intent.symbol, epoch, opened, opened + timedelta(minutes=5)
        ),
        bars=(bar(0, neutral),),
        configuration=Last5mProtectionConfiguration(
            scenario_costs(ComparisonScenario.STRESS),
            ExitPolicy.FIXED_2R,
            acknowledgement=ProtectionAcknowledgement.ACCEPTED,
            trigger_basis=TriggerBasis.LAST,
        ),
    )
    invalid_open = (
        intent.stop_basis * type(intent.stop_basis)("0.99")
        if intent.side is EntrySide.BUY
        else intent.stop_basis * type(intent.stop_basis)("1.01")
    )
    recovered = run_last_5m_protective_execution(
        intent=intent,
        binding=Last5mExecutionBinding(
            source_id, intent.symbol, epoch, opened, opened + timedelta(minutes=10)
        ),
        bars=(bar(0, invalid_open), bar(1, invalid_open)),
        configuration=Last5mProtectionConfiguration(base_costs, ExitPolicy.FIXED_2R),
    )
    assert ordinary.status is Last5mExecutionStatus.ORDINARY_CLOSED
    assert open_result.status is Last5mExecutionStatus.TERMINAL_OPEN
    assert unfilled.status is Last5mExecutionStatus.ENTRY_UNFILLED
    assert recovered.status is Last5mExecutionStatus.INVALID_GEOMETRY_RECOVERED
    saved = {
        "ordinary": subject._new_row(ordinary),
        "open": subject._new_row(open_result),
        "unfilled": subject._new_row(unfilled),
        "recovered": subject._new_row(recovered),
    }
    subject._replace_json(tmp_path / "synthetic-output.json", saved)
    assert json.loads((tmp_path / "synthetic-output.json").read_bytes())["ordinary"]["closed"]

    with pytest.raises(subject.ComparisonError, match="missing"):
        subject._select_task_inputs(
            evidence,
            rows,
            [replace_event for replace_event in events if replace_event["event"] != "COMPLETED"],
            (configuration.task,),
        )
    with pytest.raises(subject.ComparisonError, match="duplicated"):
        subject._select_task_inputs(
            evidence,
            rows,
            [*events, dict(events[-1])],
            (configuration.task,),
        )
