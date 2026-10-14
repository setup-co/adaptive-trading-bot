"""Offline characterization of the separate all-fill accounting path."""

from __future__ import annotations

import runpy
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.backtest import ExecutionCostConfiguration, ExecutionSeries, OrderSide
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillFamily,
    AllFillReplayError,
    AllFillReplayResult,
    AllFillScenario,
    AllFillStatus,
    ResearchAllFillReplayRunner,
    project_all_fill_population,
)
from adaptive_trading_bot.research.breakout_retest_corpus import (
    BreakoutRetestHistoricalCorpusGenerator,
    BreakoutRetestResearchConfiguration,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    HistoricalStrategyContextObservation,
    PairedTrendPullbackExitReplayResult,
)
from adaptive_trading_bot.strategies import CandidateAction, StrategyEvaluationContext

_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_paired_exit_replay.py")))
NOW = cast(datetime, _HELPERS["NOW"])
SOURCE = _HELPERS["SOURCE"]
BTC = _HELPERS["BTC"]
_decision = _HELPERS["_decision"]
_current_context = _HELPERS["_current_context"]
_execution_path = _HELPERS["_execution_path"]


class RecordingExecutionSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles
        self.loads: list[tuple[ExecutionSeries, datetime, datetime]] = []

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        self.loads.append((series, start, end))
        return tuple(
            candle
            for candle in self.candles
            if start <= candle.open_time < end
            and candle.symbol == series.symbol
            and candle.timeframe is series.timeframe
        )


def _run(
    *,
    side: CandidateAction = CandidateAction.LONG_CANDIDATE,
    outcome: str = "cleared",
    opens: tuple[int | Decimal, ...] = (100, 110, 120, 130),
    contexts: tuple[StrategyEvaluationContext, ...] = (),
    end_count: int | None = None,
    stress: bool = False,
) -> tuple[AllFillReplayResult, RecordingExecutionSource]:
    decision = cast(DecisionJournalEntry[object, object], _decision(side=side, outcome=outcome))
    candles = list(cast(tuple[Candle, ...], _execution_path(*opens)))
    sealed = tuple(HistoricalStrategyContextObservation(context) for context in contexts)
    for observation in sealed:
        candle = observation.context.candle_15m
        index = int((candle.open_time - NOW) / Timeframe.MINUTES_15.duration)
        candles[index] = candle
    source = RecordingExecutionSource(tuple(candles))
    costs = (
        ExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(20), timedelta(seconds=1))
        if stress
        else ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10))
    )
    result = ResearchAllFillReplayRunner(source).run(
        decision,
        sealed,
        ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
        costs,
        start=NOW,
        end=NOW + (end_count or len(candles)) * Timeframe.MINUTES_15.duration,
    )
    return result, source


@pytest.mark.parametrize(
    ("side", "entry_open", "expected_exit_side"),
    (
        (CandidateAction.LONG_CANDIDATE, 70, OrderSide.SELL),
        (CandidateAction.SHORT_CANDIDATE, 140, OrderSide.BUY),
    ),
)
def test_stop_invalidated_fill_is_accounted_and_recovered_at_next_open(
    side: CandidateAction, entry_open: int, expected_exit_side: OrderSide
) -> None:
    result, source = _run(side=side, opens=(entry_open, 110, 120))
    assert result.status is AllFillStatus.RECOVERY_CLOSED
    assert result.scenario is AllFillScenario.BASE
    assert len(source.loads) == 1
    assert len(result.branches) == 2
    assert result.branches[0].entry_fill == result.branches[1].entry_fill
    for branch in result.branches:
        assert branch.recovery is not None
        assert branch.recovery.request.side is expected_exit_side
        assert branch.exit_fill is not None
        assert branch.exit_fill.execution_candle_open_time == NOW + Timeframe.MINUTES_15.duration
        assert len(branch.accounting_events) == 2
        assert branch.final_position.quantity == 0
        assert branch.final_position.cumulative_fees > 0
        with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
            assert branch.closed_net_r == (
                branch.final_position.cumulative_net_realized_pnl / branch.approved_risk_quote
            )
    result.validate()


def test_unfilled_recovery_retains_fee_and_exposure() -> None:
    result, _ = _run(opens=(70,), end_count=1)
    assert result.status is AllFillStatus.RECOVERY_OPEN
    for branch in result.branches:
        assert branch.exit_unfilled is not None
        assert branch.exit_fill is None
        assert branch.closed_net_r is None
        assert branch.final_position.quantity > 0
        assert branch.final_position.cumulative_fees > 0
        assert len(branch.accounting_events) == 1


@pytest.mark.parametrize(
    ("side", "first_open", "next_open"),
    (
        (CandidateAction.LONG_CANDIDATE, 70, 60),
        (CandidateAction.SHORT_CANDIDATE, 140, 150),
    ),
)
def test_adverse_recovery_is_not_assumed_to_be_a_fixed_stop_loss(
    side: CandidateAction, first_open: int, next_open: int
) -> None:
    result, _ = _run(side=side, opens=(first_open, next_open))
    branch = result.branches[-1]
    assert branch.status is AllFillStatus.RECOVERY_CLOSED
    assert branch.final_position.cumulative_gross_realized_pnl < 0
    assert branch.final_position.cumulative_net_realized_pnl < 0


@pytest.mark.parametrize("side", (CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE))
def test_stop_equality_is_invalidated_after_actual_simulator_fill(side: CandidateAction) -> None:
    stop = Decimal(75) if side is CandidateAction.LONG_CANDIDATE else Decimal(135)
    multiplier = Decimal("1.0003") if side is CandidateAction.LONG_CANDIDATE else Decimal("0.9997")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        opening = stop / multiplier
    result, _ = _run(side=side, opens=(opening, 100))
    assert result.branches[0].entry_fill.fill_price == stop
    assert result.status is AllFillStatus.RECOVERY_CLOSED


def test_positive_latency_and_entry_unfilled_do_not_reuse_entry_open() -> None:
    result, _ = _run(opens=(100, 70, 110, 120), stress=True)
    assert result.scenario is AllFillScenario.COMBINED_STRESS
    assert result.branches[0].entry_fill.execution_candle_open_time == NOW + timedelta(minutes=15)
    assert result.branches[0].exit_fill is not None
    assert result.branches[0].exit_fill.execution_candle_open_time == NOW + timedelta(minutes=30)
    unfilled, _ = _run(opens=(100,), stress=True, end_count=1)
    assert unfilled.status is AllFillStatus.ENTRY_UNFILLED
    assert not unfilled.branches


def test_no_signal_does_not_load_market_or_create_accounting() -> None:
    result, source = _run(outcome="no_action")
    assert result.status is AllFillStatus.NO_SIGNAL
    assert result.branches == ()
    assert source.loads == []


@pytest.mark.parametrize(
    ("outcome", "status"),
    (("risk_blocked", AllFillStatus.RISK_BLOCKED), ("safe_mode", AllFillStatus.SAFETY_BLOCKED)),
)
def test_risk_and_safety_rejections_do_not_simulate(outcome: str, status: AllFillStatus) -> None:
    result, source = _run(outcome=outcome)
    assert result.status is status
    assert result.branches == ()
    assert source.loads == []


def test_mutated_decision_and_wrong_execution_identity_fail_before_source() -> None:
    decision = cast(DecisionJournalEntry[object, object], _decision())
    source = RecordingExecutionSource(cast(tuple[Candle, ...], _execution_path(100, 110)))
    costs = ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10))
    with pytest.raises(AllFillReplayError, match="series/range"):
        ResearchAllFillReplayRunner(source).run(
            decision,
            (),
            ExecutionSeries(SOURCE, TradingSymbol("ETH", "USDT"), Timeframe.MINUTES_15),
            costs,
            start=NOW,
            end=NOW + timedelta(minutes=30),
        )
    assert source.loads == []
    object.__setattr__(decision.context.candle_15m, "close", Decimal(999))
    with pytest.raises(ValueError, match="integrity"):
        ResearchAllFillReplayRunner(source).run(
            decision,
            (),
            ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
            costs,
            start=NOW,
            end=NOW + timedelta(minutes=30),
        )
    assert source.loads == []


def test_ordinary_branch_matches_legacy_authority_and_report_reconciles() -> None:
    context = cast(StrategyEvaluationContext, _current_context(30, close=Decimal(101)))
    ordinary, _ = _run(contexts=(context,))
    assert ordinary.branches[0].recovery is None
    assert isinstance(ordinary.legacy_replay, PairedTrendPullbackExitReplayResult)
    assert ordinary.legacy_replay.baseline is not None
    assert (
        ordinary.branches[0].accounting_events == ordinary.legacy_replay.baseline.accounting_events
    )
    no_signal, _ = _run(outcome="no_action")
    # A single decision cannot appear twice in the same population.
    with pytest.raises(AllFillReplayError, match="duplicate"):
        project_all_fill_population(
            (ordinary.decision, deepcopy(ordinary.decision)),
            (ordinary, deepcopy(ordinary)),
            (("fixture", None), ("fixture", None)),
        )
    report = project_all_fill_population((no_signal.decision,), (no_signal,), (("fixture", None),))
    assert sum(count for _, count in report.terminal_counts) == 1
    assert report.closed_denominator == 0
    assert report.mean_closed_net_r is None
    assert report.cells[0].closed_count == 0
    report.validate()
    object.__setattr__(report, "closed_denominator", 1)
    with pytest.raises(AllFillReplayError, match="integrity"):
        report.validate()


def test_ordinary_closed_path_and_primary_pair_report_counts_one_opportunity() -> None:
    context = cast(
        StrategyEvaluationContext, _current_context(15, close=Decimal(101), low=Decimal(75))
    )
    ordinary, _ = _run(contexts=(context,), opens=(100, 110, 120))
    assert ordinary.status is AllFillStatus.ORDINARY_CLOSED
    assert all(branch.recovery is None for branch in ordinary.branches)
    report = project_all_fill_population(
        (ordinary.decision,), (ordinary,), (("fixture", "synthetic-regime"),)
    )
    assert report.closed_denominator == 1
    assert report.paired_both_closed_count == 1
    assert report.incurred_fees == ordinary.branches[-1].final_position.cumulative_fees
    assert report.cells[0].closed_count == 1
    report.validate()


def test_recovery_and_report_are_independent_of_ambient_decimal_context() -> None:
    with localcontext(Context(prec=9, rounding=ROUND_DOWN)):
        low_precision, _ = _run(opens=(70, 110))
        low_report = project_all_fill_population(
            (low_precision.decision,), (low_precision,), (("fixture", None),)
        )
    with localcontext(Context(prec=60, rounding=ROUND_HALF_EVEN)):
        high_precision, _ = _run(opens=(70, 110))
        high_report = project_all_fill_population(
            (high_precision.decision,), (high_precision,), (("fixture", None),)
        )
    assert low_precision.branches == high_precision.branches
    assert low_report.mean_closed_net_r == high_report.mean_closed_net_r
    assert low_report.incurred_fees == high_report.incurred_fees


def test_mixed_explicit_population_reconciles_terminal_funnel_and_zero_cell() -> None:
    builder = _HELPERS["_journal_entry"]
    no_signal_decision = cast(
        DecisionJournalEntry[object, object],
        builder("no_action", strategy_id="breakout_retest", strategy_version="1.0.0"),
    )
    risk_decision = cast(
        DecisionJournalEntry[object, object],
        builder("risk_blocked", strategy_id="range_mean_reversion", strategy_version="2.0.0"),
    )
    source = RecordingExecutionSource(())
    runner = ResearchAllFillReplayRunner(source)
    series = ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15)
    costs = ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10))
    no_signal = runner.run(
        no_signal_decision, (), series, costs, start=NOW, end=NOW + timedelta(hours=1)
    )
    risk_blocked = runner.run(
        risk_decision, (), series, costs, start=NOW, end=NOW + timedelta(hours=1)
    )
    recovery, _ = _run(opens=(70, 110))
    assert source.loads == []
    first_report = project_all_fill_population(
        (no_signal_decision, risk_decision, recovery.decision),
        (no_signal, risk_blocked, recovery),
        (("fixture", None), ("fixture", None), ("fixture", None)),
    )
    first_key = first_report.declared_cell_keys[0]
    empty_key = (
        first_key[0],
        first_key[1],
        first_key[2],
        first_key[3],
        first_key[4],
        "declared-empty",
        None,
    )
    report = project_all_fill_population(
        (no_signal_decision, risk_decision, recovery.decision),
        (no_signal, risk_blocked, recovery),
        (("fixture", None), ("fixture", None), ("fixture", None)),
        declared_cell_keys=(empty_key,),
    )
    assert sum(count for _, count in report.terminal_counts) == 3
    assert dict(report.terminal_counts)[AllFillStatus.NO_SIGNAL] == 1
    assert dict(report.terminal_counts)[AllFillStatus.RISK_BLOCKED] == 1
    assert dict(report.terminal_counts)[AllFillStatus.RECOVERY_CLOSED] == 1
    assert report.closed_denominator == 1
    assert report.unresolved_count == 0
    assert report.incurred_fees == recovery.branches[-1].final_position.cumulative_fees
    assert report.cells[0].observation_count == 0
    assert (
        next(
            cell.side for cell in report.cells if cell.family is AllFillFamily.RANGE_MEAN_REVERSION
        )
        == "BUY"
    )
    report.validate()


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_range_midpoint_invalidated_fill_enters_accounting_and_recovery(side: str) -> None:
    helpers = runpy.run_path(
        str(Path(__file__).with_name("test_range_mean_reversion_signal_edge.py"))
    )
    _, decision = helpers["_decision_prefix"](side)
    start = cast(datetime, helpers["START"])
    symbol = helpers["SYMBOL"]
    source_id = helpers["SOURCE"]
    candles = tuple(
        Candle(
            symbol,
            Timeframe.MINUTES_15,
            start + index * Timeframe.MINUTES_15.duration,
            start + (index + 1) * Timeframe.MINUTES_15.duration,
            Decimal(100),
            Decimal(102),
            Decimal(98),
            Decimal(100),
            Decimal(100),
        )
        for index in range(4, 7)
    )
    result = ResearchAllFillReplayRunner(RecordingExecutionSource(candles)).run(
        decision,
        (),
        ExecutionSeries(source_id, symbol, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
        start=decision.identity.observed_at,
        end=candles[-1].close_time,
    )
    assert result.status is AllFillStatus.RECOVERY_CLOSED
    assert len(result.branches) == 1
    branch = result.branches[0]
    assert branch.recovery is not None
    assert "MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL" in branch.recovery.reasons
    assert branch.exit_fill is not None
    assert branch.exit_fill.execution_candle_open_time > branch.entry_fill.fill_at
    assert branch.final_position.quantity == 0
    result.validate()
    valid_price = Decimal(90) if side == "LONG" else Decimal(110)
    ordinary_candle = replace(
        candles[0],
        open=valid_price,
        high=valid_price + Decimal(1),
        low=valid_price - Decimal(1),
        close=valid_price,
    )
    ordinary = ResearchAllFillReplayRunner(RecordingExecutionSource((ordinary_candle,))).run(
        decision,
        (),
        ExecutionSeries(source_id, symbol, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
        start=decision.identity.observed_at,
        end=ordinary_candle.close_time,
    )
    assert ordinary.status is AllFillStatus.ORDINARY_OPEN
    assert ordinary.branches[0].recovery is None
    assert ordinary.branches[0].final_position.quantity != 0
    ordinary.validate()


@pytest.mark.parametrize("side", ("LONG", "SHORT"))
def test_breakout_actual_entry_fill_is_accounted_after_invalidation(
    tmp_path: Path, side: str
) -> None:
    helpers = runpy.run_path(str(Path(__file__).with_name("test_breakout_retest_signal_edge.py")))
    source_id = helpers["SOURCE"]
    symbol = helpers["SYMBOL"]
    start = cast(datetime, helpers["START"])
    store = helpers["_store"](tmp_path) if side == "LONG" else helpers["_short_store"](tmp_path)
    end = start + timedelta(hours=204)
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(
        BreakoutRetestResearchConfiguration((source_id,), (symbol,), start, end, Decimal(10000))
    )
    action = CandidateAction.LONG_CANDIDATE if side == "LONG" else CandidateAction.SHORT_CANDIDATE
    decision = next(
        item.decision
        for item in corpus.decisions
        if item.is_research_admissible_entry and item.decision.strategy_evaluation.action is action
    )
    authorization = EntryOrderIntentAuthorizer().authorize(decision)
    assert authorization.intent is not None
    stop = authorization.intent.stop_basis
    original = store.read(source_id, symbol, Timeframe.MINUTES_15, start=start, end=end)
    index = next(
        index
        for index, candle in enumerate(original)
        if candle.open_time == decision.identity.observed_at
    )
    opening = original[index]
    price = stop - Decimal(1) if side == "LONG" else stop + Decimal(1)
    changed = replace(
        opening, open=price, high=price + Decimal(4), low=price - Decimal(1), close=price
    )
    candles = (*original[:index], changed, *original[index + 1 :])
    source = RecordingExecutionSource(candles)
    result = ResearchAllFillReplayRunner(source).run(
        decision,
        (),
        ExecutionSeries(source_id, symbol, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
        start=decision.identity.observed_at,
        end=original[index + 2].close_time,
    )
    assert result.status is AllFillStatus.RECOVERY_CLOSED
    assert len(source.loads) == 1
    assert len(result.branches[0].accounting_events) == 2
    assert result.branches[0].recovery is not None
    assert result.branches[0].recovery.reasons == ("STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL",)
    result.validate()
    opening = original[index]
    ordinary = ResearchAllFillReplayRunner(RecordingExecutionSource((opening,))).run(
        decision,
        (),
        ExecutionSeries(source_id, symbol, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
        start=decision.identity.observed_at,
        end=opening.close_time,
    )
    assert ordinary.status is AllFillStatus.ORDINARY_OPEN
    assert ordinary.branches[0].recovery is None
    ordinary.validate()
