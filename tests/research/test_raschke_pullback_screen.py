import gzip
import json
import runpy
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

import adaptive_trading_bot.research.raschke_pullback_completion as COMPLETION
import adaptive_trading_bot.research.raschke_pullback_screen as SCREEN_MODULE
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.features import PivotKind
from adaptive_trading_bot.features.wilder_adx import WilderAdxCalculator, WilderAdxSnapshot
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    Scenario,
    ScreenError,
    _canonical,
    _text,
)
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (
    ARMS,
    PROTOCOL_SHA256,
    _capacity,
    _projection,
    _row,
    _same_opportunity,
    _study_metrics,
    protocol_record,
)
from adaptive_trading_bot.risk import OperationalSafetyGate, RiskManager, RiskPortfolioSnapshot
from adaptive_trading_bot.strategies.core import CandidateAction
from adaptive_trading_bot.strategies.raschke_pullback import (
    Evaluation,
    RaschkePullbackArm,
    RaschkePullbackStrategy,
    _bounds,
)

FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_trend_impulse_pullback_screen.py")))
STRATEGY_FIXTURE = runpy.run_path(
    str(Path(__file__).parents[1] / "strategies" / "test_range_mean_reversion.py")
)
_context = STRATEGY_FIXTURE["_context"]
_write_authority = FIXTURE["_write_authority"]
CLI = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "research" / "raschke_pullback_screen.py")
)
EXECUTION_FIXTURE = runpy.run_path(
    str(Path(__file__).with_name("test_last_5m_protective_execution.py"))
)
make_intent = EXECUTION_FIXTURE["intent"]
_entry = EXECUTION_FIXTURE["_entry"]
_health = EXECUTION_FIXTURE["_health"]
bar = EXECUTION_FIXTURE["bar"]
binding = EXECUTION_FIXTURE["binding"]


class _Adx:
    def __init__(self) -> None:
        self.index = 0

    def advance(self, candle: object) -> WilderAdxSnapshot:
        del candle
        values = (
            WilderAdxSnapshot(
                Decimal(35), Decimal(34), Decimal(30), Decimal(10), Decimal(50), 28, False
            ),
            WilderAdxSnapshot(
                Decimal(36), Decimal(35), Decimal(31), Decimal(9), Decimal(55), 29, False
            ),
            WilderAdxSnapshot(
                Decimal(37), Decimal(36), Decimal(32), Decimal(8), Decimal(60), 30, False
            ),
            WilderAdxSnapshot(
                Decimal(38), Decimal(37), Decimal(33), Decimal(7), Decimal(65), 31, False
            ),
        )
        value = values[self.index]
        self.index += 1
        return value


def _candidate(
    arm: RaschkePullbackArm,
    *,
    pullback_low: Decimal = Decimal(100),
    origin_price: Decimal = Decimal(80),
) -> Evaluation:
    strategy = RaschkePullbackStrategy(arm)
    first = _context(
        1,
        open_price=Decimal(108),
        high=Decimal(115),
        low=Decimal(105),
        close=Decimal(110),
        pivots=((PivotKind.LOW, origin_price, 0),),
    )
    strategy._adx[(first.source_id, first.symbol)] = _Adx()  # type: ignore[assignment]
    strategy.evaluate(first)
    strategy.evaluate(
        _context(
            2, open_price=Decimal(110), high=Decimal(120), low=Decimal(105), close=Decimal(118)
        )
    )
    strategy.evaluate(
        _context(
            3, open_price=Decimal(110), high=Decimal(115), low=pullback_low, close=Decimal(108)
        )
    )
    return strategy.evaluate(
        _context(
            4,
            open_price=Decimal(108),
            high=Decimal(118),
            low=pullback_low + 1,
            close=Decimal(116),
        )
    )


def _short_candidate(arm: RaschkePullbackArm) -> Evaluation:
    strategy = RaschkePullbackStrategy(arm)
    first = _context(
        1,
        open_price=Decimal(92),
        high=Decimal(95),
        low=Decimal(85),
        close=Decimal(90),
        pivots=((PivotKind.HIGH, Decimal(120), 0),),
    )
    adx = _Adx()
    original = adx.advance

    def short_advance(candle: object) -> WilderAdxSnapshot:
        value = original(candle)
        return replace(value, plus_di=value.minus_di, minus_di=value.plus_di)

    adx.advance = short_advance  # type: ignore[method-assign]
    strategy._adx[(first.source_id, first.symbol)] = adx  # type: ignore[assignment]
    strategy.evaluate(first)
    strategy.evaluate(
        _context(2, open_price=Decimal(90), high=Decimal(95), low=Decimal(80), close=Decimal(82))
    )
    strategy.evaluate(
        _context(3, open_price=Decimal(88), high=Decimal(100), low=Decimal(85), close=Decimal(90))
    )
    return strategy.evaluate(
        _context(4, open_price=Decimal(90), high=Decimal(96), low=Decimal(82), close=Decimal(84))
    )


def test_three_arms_share_causal_geometry_and_exact_depth_boundaries() -> None:
    evaluations = [_candidate(arm) for arm in RaschkePullbackArm]
    assert all(value.action is CandidateAction.LONG_CANDIDATE for value in evaluations)
    assert {value.reference_price for value in evaluations} == {Decimal(116)}
    assert {value.invalidation_reference_price for value in evaluations} == {Decimal(100)}
    assert {value.rule_audit.frozen_target for value in evaluations} == {Decimal(120)}
    assert {value.rule_audit.depth for value in evaluations} == {Decimal("0.5")}
    assert _bounds(RaschkePullbackArm.FIB_ZONE) == (Decimal("0.382"), Decimal("0.618"))
    assert _bounds(RaschkePullbackArm.ROUND_ZONE) == (Decimal("0.40"), Decimal("0.60"))
    rejected = [_candidate(arm, pullback_low=Decimal(95)) for arm in RaschkePullbackArm]
    assert rejected[0].action is CandidateAction.LONG_CANDIDATE
    assert all(value.action is CandidateAction.NO_TRADE for value in rejected[1:])
    for boundary in (Decimal("0.382"), Decimal("0.618")):
        exact = _candidate(
            RaschkePullbackArm.FIB_ZONE,
            pullback_low=120 - 60 * boundary,
            origin_price=Decimal(60),
        )
        assert exact.action is CandidateAction.LONG_CANDIDATE
        assert exact.rule_audit.depth == boundary
    short = _short_candidate(RaschkePullbackArm.FIB_ZONE)
    assert short.action is CandidateAction.SHORT_CANDIDATE
    assert short.reference_price == Decimal(84)
    assert short.invalidation_reference_price == Decimal(100)
    assert short.rule_audit.frozen_target == Decimal(80)


def test_wilder_adx_seed_flat_ties_and_gap_reset() -> None:
    calculator = WilderAdxCalculator()
    snapshots = []
    for index in range(1, 30):
        base = Decimal(100 + index)
        snapshots.append(
            calculator.advance(
                _context(
                    index,
                    open_price=base,
                    high=base + 1,
                    low=base,
                    close=base + Decimal("0.5"),
                ).candle_15m
            )
        )
    assert snapshots[26].adx is None
    assert snapshots[27].adx == Decimal(100)
    assert snapshots[28].previous_adx == Decimal(100)
    gap = calculator.advance(_context(31).candle_15m)
    assert gap.gap_reset is True
    assert gap.adx is None

    ties = WilderAdxCalculator()
    ties.advance(
        _context(
            1,
            open_price=Decimal(100),
            high=Decimal(110),
            low=Decimal(90),
            close=Decimal(100),
        ).candle_15m
    )
    tied = None
    for index in range(2, 16):
        tied = ties.advance(
            _context(
                index,
                open_price=Decimal(100),
                high=Decimal(110 + index),
                low=Decimal(90 - index),
                close=Decimal(100),
            ).candle_15m
        )
    assert tied is not None
    assert tied.plus_di == 0
    assert tied.minus_di == 0
    assert tied.dx == 0


def _protocol_hash(authority: Any) -> str:
    followup_end = datetime.fromisoformat(authority.report_end.replace("Z", "+00:00"))
    body = json.dumps(
        _text(protocol_record(authority, followup_end=followup_end)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return sha256(body).hexdigest()


def test_actual_cli_reads_canonical_parquet_and_writes_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = _write_authority(repo)
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.email", "fixture@example.invalid"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Fixture"), cwd=repo, check=True)
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture"), cwd=repo, check=True)
    executable = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()
    output = tmp_path / "output"
    assert (
        CLI["main"](
            [
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--run-id",
                "synthetic",
                "--executable-sha",
                executable,
                "--protocol-sha256",
                _protocol_hash(authority),
            ],
            authority=authority,
        )
        == 0
    )
    projection = json.loads((output / "projection.json").read_text())
    assert json.loads((output / "complete.json").read_text())["status"] == "COMPLETE"
    assert projection["promotion"] == "NO_PROMOTION"
    assert set(projection["members"][0]["counts"]) == set(ARMS)

    class _PayloadMustNotLoad:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("payload loading occurred before protocol rejection")

    monkeypatch.setattr(SCREEN_MODULE, "AcceptedInput", _PayloadMustNotLoad)
    with pytest.raises(ScreenError, match="effective protocol differs"):
        CLI["main"](
            [
                "--repo",
                str(repo),
                "--output",
                str(tmp_path / "mismatch-output"),
                "--run-id",
                "synthetic-mismatch",
                "--executable-sha",
                executable,
                "--protocol-sha256",
                "f" * 64,
            ],
            authority=authority,
        )


def _execute(
    *,
    target: str,
    prices: tuple[str, ...],
    costs: SimulatedExecutionCostConfiguration,
    order_intent: Any = None,
) -> tuple[Last5mProtectiveExecutionResult, tuple[Any, ...]]:
    intent = make_intent() if order_intent is None else order_intent
    bars = tuple(
        bar(
            index,
            open_price=price,
            high=str(Decimal(price) + 1),
            low=str(Decimal(price) - 1),
            close=price,
        )
        for index, price in enumerate(prices)
    )
    result = run_last_5m_protective_execution(
        intent=intent,
        binding=binding(len(bars)),
        bars=bars,
        configuration=Last5mProtectionConfiguration(
            costs,
            ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT,
            ProtectionAcknowledgement.ACCEPTED,
            timedelta(0),
            frozen_target_price=Decimal(target),
            holding_period=None,
        ),
    )
    return result, bars


def _production_row(
    *,
    arm: RaschkePullbackArm,
    episode: str,
    scenario: Scenario,
    target: str,
    prices: tuple[str, ...],
    costs: SimulatedExecutionCostConfiguration,
) -> tuple[dict[str, Any], Last5mProtectiveExecutionResult]:
    base = _entry(strategy_id=arm.value)
    audit = replace(
        _candidate(arm).rule_audit,
        arm=arm.value,
        episode_id=episode,
        event="CONFIRMATION",
        confirmation_at=base.context.observed_at,
        frozen_target=Decimal(target),
    )
    evaluation = replace(base.strategy_evaluation, strategy_id=arm.value, rule_audit=audit)
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            base.context.observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    decision = DecisionJournalEntry(
        base.context,
        evaluation,
        risk,
        OperationalSafetyGate().evaluate(risk, _health(risk)),
    )
    authorized = EntryOrderIntentAuthorizer().authorize(decision)
    assert authorized.intent is not None
    result, bars = _execute(
        target=target, prices=prices, costs=costs, order_intent=authorized.intent
    )
    return _row("BTCUSDT", arm.value, scenario, decision, result, bars[-1]), result


def test_saved_row_cli_proves_recovery_ordinary_open_and_strict_equality(
    tmp_path: Path,
) -> None:
    base = SimulatedExecutionCostConfiguration(Decimal(0), Decimal(0), Decimal(8))
    stress = SimulatedExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(16))
    delayed = SimulatedExecutionCostConfiguration(
        Decimal(0), Decimal(0), Decimal(8), timedelta(minutes=11)
    )
    rows: list[dict[str, Any]] = []
    results: dict[tuple[str, str], Last5mProtectiveExecutionResult] = {}
    for arm in RaschkePullbackArm:
        for episode, scenario, target, prices, costs in (
            ("recovered", Scenario.STRESS, "105", ("105", "104"), stress),
            ("ordinary", Scenario.BASE, "105.5", ("105",), base),
            ("open", Scenario.BASE, "120", ("105", "106"), base),
            ("unfilled", Scenario.BASE, "120", ("105", "106"), delayed),
        ):
            row, result = _production_row(
                arm=arm,
                episode=episode,
                scenario=scenario,
                target=target,
                prices=prices,
                costs=costs,
            )
            rows.append(row)
            results[(arm.value, episode)] = result
    recovered = results[(RaschkePullbackArm.REFERENCE.value, "recovered")]
    ordinary = results[(RaschkePullbackArm.REFERENCE.value, "ordinary")]
    opened = results[(RaschkePullbackArm.REFERENCE.value, "open")]
    unfilled = results[(RaschkePullbackArm.REFERENCE.value, "unfilled")]
    assert recovered.status.value == "INVALID_GEOMETRY_RECOVERED"
    assert recovered.target_price is None
    assert ordinary.status.value == "ORDINARY_CLOSED"
    assert opened.status.value == "TERMINAL_OPEN"
    assert unfilled.status.value == "ENTRY_UNFILLED"
    assert unfilled.entry_fill is None

    quantity = recovered.intent_audit.quantity
    # Four-bps total spread contributes two bps per side, plus four-bps slippage.
    expected_entry = Decimal("105.0630")
    expected_recovery = Decimal("103.9376")
    with localcontext(Context(prec=34)):
        expected_fees = (expected_entry + expected_recovery) * quantity * Decimal("0.0016")
        expected_gross = (expected_recovery - expected_entry) * quantity
    assert recovered.entry_fill is not None and recovered.exit_fill is not None
    assert recovered.entry_fill.fill_price == expected_entry
    assert recovered.exit_fill.fill_price == expected_recovery
    assert recovered.total_fees == expected_fees
    assert recovered.realized_net_pnl == expected_gross - expected_fees

    raw = b"".join(
        json.dumps(_text(row), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in rows
    )
    saved = tmp_path / "rows.jsonl.gz"
    saved.write_bytes(gzip.compress(raw, mtime=0))
    output = tmp_path / "validation.json"
    assert (
        CLI["main"](
            [
                "--output",
                str(output),
                "--saved-rows",
                str(saved),
                "--expected-gzip-sha256",
                sha256(saved.read_bytes()).hexdigest(),
                "--expected-raw-sha256",
                sha256(raw).hexdigest(),
                "--expected-rows",
                str(len(rows)),
                "--report-path-proof",
            ]
        )
        == 0
    )
    report = json.loads(output.read_text())
    assert report["rows"] == 12
    assert report["recovered_records"] == 3
    assert report["recovered_underlying_opportunities"] == 1
    recovered_geometry = report["report_path_proof"][
        f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:STRESS"
    ]["metrics"]["geometry"]
    assert recovered_geometry["execution_target_unavailable"] == 1
    assert recovered_geometry["valid_executed_reward_risk_denominator"] == 0
    open_metrics = report["report_path_proof"][
        f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE"
    ]["metrics"]
    assert open_metrics["data_end_censored"] == 1
    assert open_metrics["ordinary_endpoint_open"] == 1
    assert open_metrics["entry_unfilled"] == 1
    assert open_metrics["filled_admissions"] == 2
    assert open_metrics["economic_denominator"] == 2
    assert (
        report["counts"][f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE:ENTRY_UNFILLED"] == 1
    )

    members = [
        {
            "symbol": wire,
            "counts": {
                arm: {
                    "underlying_opportunities": 4 if wire == "BTCUSDT" else 0,
                    "anchor_valid_opportunities": 4 if wire == "BTCUSDT" else 0,
                    "signals": 4 if wire == "BTCUSDT" else 0,
                    "admitted": 4 if wire == "BTCUSDT" else 0,
                }
                for arm in ARMS
            },
            "underlying_opportunity_ids": (
                ["ordinary", "open", "recovered", "unfilled"] if wire == "BTCUSDT" else []
            ),
        }
        for wire in DEFAULT_INPUT_AUTHORITY.shard_map
    ]
    projection = _text(
        _projection(
            "synthetic-report-path",
            "0" * 40,
            members,
            rows,
            DEFAULT_INPUT_AUTHORITY,
            PROTOCOL_SHA256,
        )
    )
    final_report = tmp_path / "projection.json"
    final_report.write_text(json.dumps(projection, sort_keys=True, separators=(",", ":")))
    read_back = json.loads(final_report.read_text())
    final_base = read_back["economic_table"][f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE"][
        "terminal"
    ]
    assert final_base["data_end_censored"] == 1
    assert final_base["ordinary_endpoint_open"] == 1
    assert final_base["entry_unfilled"] == 1
    capacity_record = read_back["capacity_diagnostic"][
        f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE"
    ]
    assert capacity_record["all_admissions"] == 3
    assert capacity_record["selected"] == 1
    assert capacity_record["overlap_rejected"] == 2
    assert capacity_record["terminal"]["execution_status_counts"] == {"ORDINARY_CLOSED": 1}
    selection_base = read_back["paired_selection_comparisons"]["BTCUSDT:BASE"]["reference_all"][
        "terminal"
    ]
    assert selection_base["ordinary_endpoint_open"] == 1
    assert selection_base["entry_unfilled"] == 1
    block_prefix = f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE:"
    assert (
        sum(
            value["terminal"]["ordinary_endpoint_open"]
            for key, value in read_back["chronological_blocks"].items()
            if key.startswith(block_prefix)
        )
        == 1
    )
    assert (
        sum(
            value["terminal"]["entry_unfilled"]
            for key, value in read_back["chronological_blocks"].items()
            if key.startswith(block_prefix)
        )
        == 1
    )
    final_recovered = read_back["economic_table"][
        f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:STRESS"
    ]["terminal"]
    assert final_recovered["recovered_closed"] == 1
    assert Decimal(final_recovered["natural_fees_quote"]) == expected_fees
    assert Decimal(final_recovered["net_pnl_quote"]) == recovered.realized_net_pnl

    unresolved = json.loads(json.dumps(rows[-1], default=str))
    unresolved["new"]["status"] = "INVALID_GEOMETRY_UNRESOLVED"
    unresolved_metrics = _study_metrics([unresolved], terminal=True)
    assert unresolved_metrics["data_end_censored"] == 0
    assert unresolved_metrics["entry_unfilled"] == 0
    assert unresolved_metrics["incomplete_or_error"] == 1
    assert unresolved_metrics["incomplete_or_error_status_counts"] == {
        "INVALID_GEOMETRY_UNRESOLVED": 1
    }
    assert unresolved_metrics["valued"] == 0

    disagreement = json.loads(json.dumps(_text(rows)))
    disagreement[4]["new"]["total_fees_quote"] = "999"
    with pytest.raises(Exception, match="outcome economics"):
        _same_opportunity(disagreement)
    target_disagreement = json.loads(json.dumps(_text(rows)))
    target_disagreement[0]["authority"]["configured_target_price"] = "106"
    with pytest.raises(Exception, match="target authorities disagree"):
        _same_opportunity(target_disagreement)
    unexpected_null = json.loads(json.dumps(_text(rows)))
    unexpected_null[1]["authority"]["execution_target_price"] = None
    with pytest.raises(Exception, match="unexpectedly unavailable"):
        _same_opportunity(unexpected_null)


def _closed_completion_rows() -> list[dict[str, Any]]:
    base = SimulatedExecutionCostConfiguration(Decimal(0), Decimal(0), Decimal(8))
    stress = SimulatedExecutionCostConfiguration(
        Decimal(4), Decimal(4), Decimal(16), timedelta(seconds=1)
    )
    rows: list[dict[str, Any]] = []
    for arm in RaschkePullbackArm:
        for scenario, prices, costs in (
            (Scenario.BASE, ("105",), base),
            (Scenario.STRESS, ("105", "105"), stress),
        ):
            row, result = _production_row(
                arm=arm,
                episode="completion",
                scenario=scenario,
                target="105.5",
                prices=prices,
                costs=costs,
            )
            assert result.status.value == "ORDINARY_CLOSED"
            rows.append(row)
    return rows


def test_completion_parent_integrity_rejects_missing_and_duplicate_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _closed_completion_rows()

    def write_and_bind(name: str, values: list[dict[str, Any]]) -> Path:
        raw = b"".join(_canonical(_text(row)) + b"\n" for row in values)
        path = tmp_path / name
        path.write_bytes(gzip.compress(raw, mtime=0))
        monkeypatch.setattr(COMPLETION, "PARENT_ROWS", len(values))
        monkeypatch.setattr(COMPLETION, "PARENT_RAW_BYTES", len(raw))
        monkeypatch.setattr(COMPLETION, "PARENT_RAW_SHA256", sha256(raw).hexdigest())
        monkeypatch.setattr(COMPLETION, "PARENT_GZIP_BYTES", path.stat().st_size)
        monkeypatch.setattr(COMPLETION, "PARENT_GZIP_SHA256", sha256(path.read_bytes()).hexdigest())
        return path

    monkeypatch.setattr(
        COMPLETION,
        "EXPECTED_PARENT_COUNTS",
        {(arm, scenario.value): 1 for arm in ARMS for scenario in Scenario},
    )
    monkeypatch.setattr(COMPLETION, "EXPECTED_RECOVERED_COUNTS", {})
    valid = write_and_bind("valid.gz", rows)
    loaded, integrity = COMPLETION.load_parent_rows(valid)
    assert len(loaded) == 6
    assert integrity["unique_pair_keys"] == 6

    missing = write_and_bind("missing.gz", rows[:-1])
    with pytest.raises(ScreenError, match="arm/scenario population differs"):
        COMPLETION.load_parent_rows(missing)

    duplicate_rows = [*rows[:-1], rows[0]]
    duplicate = write_and_bind("duplicate.gz", duplicate_rows)
    with pytest.raises(ScreenError, match="missing or duplicated"):
        COMPLETION.load_parent_rows(duplicate)


def test_completion_reuses_btc_and_calls_only_eth_member_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.email", "fixture@example.invalid"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Fixture"), cwd=repo, check=True)
    marker = repo / "marker"
    marker.write_text("completion")
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "fixture"), cwd=repo, check=True)
    executable = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()

    btc_rows = _closed_completion_rows()
    eth_rows = json.loads(json.dumps(_text(btc_rows)))
    for row in eth_rows:
        row["symbol"] = "ETHUSDT"
        row["pair_key"][0] = "ETHUSDT"
        row["opportunity_key"][0] = "ETHUSDT"
    parent_integrity = {
        "metadata_complete": False,
        "funnel_metadata": "NOT_RETAINED",
        "rows": len(btc_rows),
        "unique_pair_keys": len(btc_rows),
    }
    calls: list[str] = []

    class _Accepted:
        def __init__(self, _repo: Path, _authority: Any) -> None:
            pass

        def load(self, wire: str, *, tail_end: datetime) -> tuple[object, tuple[object, ...]]:
            assert tail_end.isoformat() == "2026-10-14T00:00:00+00:00"
            calls.append(wire)
            return object(), ()

    def fake_member(
        wire: str, _store: object, _five: tuple[object, ...], _output: Path, *, authority: Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert wire == "ETHUSDT"
        assert authority is DEFAULT_INPUT_AUTHORITY
        counts = {
            arm: {
                "observations": 17_280,
                "underlying_opportunities": 1,
                "anchor_valid_opportunities": 1,
                "signals": 1,
                "admitted": 1,
            }
            for arm in ARMS
        }
        return {
            "symbol": wire,
            "counts": counts,
            "underlying_opportunity_ids": ["completion"],
        }, eth_rows

    monkeypatch.setattr(COMPLETION, "load_parent_rows", lambda _path: (btc_rows, parent_integrity))
    monkeypatch.setattr(COMPLETION, "PARENT_ROWS", len(btc_rows))
    monkeypatch.setattr(COMPLETION, "AcceptedInput", _Accepted)
    monkeypatch.setattr(COMPLETION, "run_member", fake_member)
    output = tmp_path / "output"
    projection = COMPLETION.run_completion(
        repo,
        tmp_path / "parent.gz",
        output,
        run_id="synthetic-completion",
        executable_sha=executable,
        expected_protocol_sha256=COMPLETION._sha(  # type: ignore[attr-defined]
            COMPLETION._canonical(COMPLETION.protocol_record())  # type: ignore[attr-defined]
        ),
    )
    assert calls == ["ETHUSDT"]
    assert projection["composite_provenance"]["reused_btc_rows"] == 6
    assert projection["composite_provenance"]["new_eth_rows"] == 6
    assert projection["members"][0]["counts"][ARMS[0]]["observations"] is None
    assert projection["members"][0]["counts"][ARMS[0]]["admitted"] == 1
    complete = json.loads((output / "complete.json").read_text())
    assert complete["status"] == "COMPLETE"
    assert complete["total_rows"] == 12
    with pytest.raises(ScreenError, match="effective completion protocol differs"):
        COMPLETION.run_completion(
            repo,
            tmp_path / "parent.gz",
            tmp_path / "wrong-protocol",
            run_id="synthetic-mismatch",
            executable_sha=executable,
            expected_protocol_sha256="f" * 64,
        )
    assert calls == ["ETHUSDT"]


def test_default_protocol_binds_october_14_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    protocol = protocol_record()
    assert protocol["calendar"]["followup_end"] == "2026-10-14T00:00:00+00:00"  # type: ignore[index]
    assert (
        sha256(
            json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        == PROTOCOL_SHA256
    )
    captured: dict[str, Any] = {}

    def capture_run(repo: Path, output: Path, **kwargs: Any) -> dict[str, Any]:
        captured.update({"repo": repo, "output": output, **kwargs})
        return {}

    monkeypatch.setitem(CLI["main"].__globals__, "run", capture_run)
    assert (
        CLI["main"](
            [
                "--repo",
                "synthetic-repo",
                "--output",
                "synthetic-output",
                "--run-id",
                "synthetic-default-binding",
                "--executable-sha",
                "1" * 40,
            ]
        )
        == 0
    )
    assert captured["expected_protocol_sha256"] == PROTOCOL_SHA256
    assert "followup_end" not in captured


def test_capacity_conservatively_rejects_equal_boundary() -> None:
    def row(decided: str, opened: str, holding: int) -> dict[str, object]:
        return {
            "decision_at": decided,
            "entry": {"fill_at": opened},
            "new": {
                "status": "ORDINARY_CLOSED",
                "natural_closed": True,
                "holding_upper_seconds": holding,
                "holding_lower_seconds": holding,
                "trigger": "TARGET_INTRABAR",
                "closed_net_r": "1",
                "gross_pnl_quote": "1",
                "total_fees_quote": "0",
                "net_pnl_quote": "1",
            },
            "terminal_valuation": {
                "valuation_at": opened,
                "net_approved_risk_r": "1",
                "gross_pnl_quote": "1",
                "total_fees_quote": "0",
                "net_pnl_quote": "1",
                "depends_on_open_position": False,
            },
        }

    values: list[dict[str, Any]] = [
        row("2026-01-01T00:00:00+00:00", "2026-01-01T00:05:00+00:00", 600),
        row("2026-01-01T00:15:00+00:00", "2026-01-01T00:20:00+00:00", 300),
        row("2026-01-01T00:16:00+00:00", "2026-01-01T00:21:00+00:00", 300),
    ]
    result = _capacity(values)
    assert result["selected"] == 2
    assert result["overlap_rejected"] == 1
