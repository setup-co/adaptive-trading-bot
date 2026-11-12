import json
import runpy
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_trading_bot.features import PivotKind
from adaptive_trading_bot.features.wilder_adx import WilderAdxCalculator, WilderAdxSnapshot
from adaptive_trading_bot.research.raschke_pullback_screen import ARMS, _capacity
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


def test_capacity_conservatively_rejects_equal_boundary() -> None:
    def row(opened: str, holding: int) -> dict[str, object]:
        return {
            "decision_at": opened,
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
        row("2026-01-01T00:00:00+00:00", 900),
        row("2026-01-01T00:10:00+00:00", 300),
        row("2026-01-01T00:15:00+00:00", 300),
    ]
    result = _capacity(values)
    assert result["selected"] == 1
    assert result["overlap_rejected"] == 2
