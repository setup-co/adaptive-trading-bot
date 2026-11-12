import gzip
import json
import runpy
import subprocess
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.features import PivotKind
from adaptive_trading_bot.features.wilder_adx import WilderAdxCalculator, WilderAdxSnapshot
from adaptive_trading_bot.research.bollinger_method2_no_time_exit import _terminal_valuation
from adaptive_trading_bot.research.failed_breakout_reversion_screen import _text
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
    _same_opportunity,
    protocol_record,
)
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


def test_actual_cli_reads_canonical_parquet_and_writes_final_report(tmp_path: Path) -> None:
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
            ],
            authority=authority,
        )
        == 0
    )
    projection = json.loads((output / "projection.json").read_text())
    assert json.loads((output / "complete.json").read_text())["status"] == "COMPLETE"
    assert projection["promotion"] == "NO_PROMOTION"
    assert set(projection["members"][0]["counts"]) == set(ARMS)


def _execute(
    *, target: str, prices: tuple[str, ...], costs: SimulatedExecutionCostConfiguration
) -> tuple[Last5mProtectiveExecutionResult, tuple[Any, ...]]:
    intent = make_intent()
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


def _saved_row(
    result: Last5mProtectiveExecutionResult,
    terminal_bar: Any,
    *,
    arm: str,
    episode: str,
    scenario: str,
) -> dict[str, Any]:
    target = result.configuration.frozen_target_price
    assert target is not None
    valuation = _terminal_valuation(result, terminal_bar)
    return {
        "pair_key": ["BTCUSDT", arm, scenario, episode],
        "opportunity_key": [
            "BTCUSDT",
            episode,
            result.decision_identity.observed_at.isoformat(),
        ],
        "symbol": "BTCUSDT",
        "arm": arm,
        "scenario": scenario,
        "decision_at": result.decision_identity.observed_at.isoformat(),
        "side": result.intent_audit.side.value,
        "audit": {"frozen_target": str(target), "depth_available": True},
        "entry": None if result.entry_fill is None else _text(asdict(result.entry_fill)),
        "authority": {
            "approved_risk_quote": str(result.approved_risk_quote),
            "quantity": str(result.intent_audit.quantity),
            "stop_basis": str(result.original_stop_basis),
            "intended_target_price": str(target),
            "configured_target_price": str(target),
            "execution_target_price": None
            if result.target_price is None
            else str(result.target_price),
            "configuration": _text(asdict(result.configuration)),
        },
        "new": {
            "status": result.status.value,
            "trigger": None if result.trigger is None else result.trigger.kind.value,
            "natural_closed": result.position_closed,
            "gross_pnl_quote": str(result.realized_gross_pnl),
            "total_fees_quote": str(result.total_fees),
            "net_pnl_quote": str(result.realized_net_pnl),
            "closed_net_r": None
            if result.closed_net_approved_risk_r is None
            else str(result.closed_net_approved_risk_r),
            "unresolved_quantity": str(result.unresolved_quantity),
            "holding_lower_seconds": None
            if result.holding_duration_lower is None
            else str(result.holding_duration_lower.total_seconds()),
            "holding_upper_seconds": None
            if result.holding_duration_upper is None
            else str(result.holding_duration_upper.total_seconds()),
        },
        "terminal_valuation": _text(valuation),
    }


def test_saved_row_cli_proves_recovery_ordinary_open_and_strict_equality(
    tmp_path: Path,
) -> None:
    base = SimulatedExecutionCostConfiguration(Decimal(0), Decimal(0), Decimal(8))
    stress = SimulatedExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(16))
    delayed = SimulatedExecutionCostConfiguration(
        Decimal(0), Decimal(0), Decimal(8), timedelta(minutes=11)
    )
    recovered, recovered_bars = _execute(target="105", prices=("105", "104"), costs=stress)
    ordinary, ordinary_bars = _execute(target="105.5", prices=("105",), costs=base)
    opened, open_bars = _execute(target="120", prices=("105", "106"), costs=base)
    unfilled, unfilled_bars = _execute(target="120", prices=("105", "106"), costs=delayed)
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

    rows: list[dict[str, Any]] = []
    for arm in ARMS:
        rows.extend(
            (
                _saved_row(
                    recovered,
                    recovered_bars[-1],
                    arm=arm,
                    episode="recovered",
                    scenario="STRESS",
                ),
                _saved_row(
                    ordinary,
                    ordinary_bars[-1],
                    arm=arm,
                    episode="ordinary",
                    scenario="BASE",
                ),
                _saved_row(
                    opened,
                    open_bars[-1],
                    arm=arm,
                    episode="open",
                    scenario="BASE",
                ),
                _saved_row(
                    unfilled,
                    unfilled_bars[-1],
                    arm=arm,
                    episode="unfilled",
                    scenario="BASE",
                ),
            )
        )
    raw = b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in rows
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
    assert open_metrics["data_end_censored"] == 2
    assert (
        report["counts"][f"BTCUSDT:{RaschkePullbackArm.REFERENCE.value}:BASE:ENTRY_UNFILLED"] == 1
    )

    disagreement = json.loads(json.dumps(rows))
    disagreement[3]["new"]["total_fees_quote"] = "999"
    with pytest.raises(Exception, match="outcome economics"):
        _same_opportunity(disagreement)
    target_disagreement = json.loads(json.dumps(rows))
    target_disagreement[0]["authority"]["configured_target_price"] = "106"
    with pytest.raises(Exception, match="target authorities disagree"):
        _same_opportunity(target_disagreement)
    unexpected_null = json.loads(json.dumps(rows))
    unexpected_null[1]["authority"]["execution_target_price"] = None
    with pytest.raises(Exception, match="unexpectedly unavailable"):
        _same_opportunity(unexpected_null)


def test_default_protocol_binds_october_14_endpoint() -> None:
    protocol = protocol_record()
    assert protocol["calendar"]["followup_end"] == "2026-10-14T00:00:00+00:00"  # type: ignore[index]
    assert (
        sha256(
            json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        == PROTOCOL_SHA256
    )


def test_capacity_conservatively_rejects_equal_boundary() -> None:
    def row(decided: str, opened: str, holding: int) -> dict[str, object]:
        return {
            "decision_at": decided,
            "entry": {"fill_at": opened},
            "new": {
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
