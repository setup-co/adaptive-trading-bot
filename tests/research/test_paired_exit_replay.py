import ast
import inspect
import runpy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    getcontext,
    localcontext,
    setcontext,
)
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.research.paired_exit_replay as replay_module
from adaptive_trading_bot.backtest import (
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    OrderSide,
)
from adaptive_trading_bot.domain import Candle, Timeframe
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    DirectionEvidence,
    RegimeLabel,
)
from adaptive_trading_bot.research import (
    ExitReplayBranchPolicy,
    HistoricalExitCause,
    PairedExitComparisonState,
    PairedExitReplayError,
    PairedExitReplayStatus,
    PairedTrendPullbackExitReplayResult,
    PairedTrendPullbackExitReplayRunner,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.strategies import (
    CandidateAction,
    PositionExitAction,
    StrategyEvaluationContext,
)

_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
NOW = cast(datetime, _JOURNAL_HELPERS["NOW"])
SOURCE = _JOURNAL_HELPERS["SOURCE"]
BTC = _JOURNAL_HELPERS["BTC"]
_journal_context = _JOURNAL_HELPERS["_context"]
_journal_entry = _JOURNAL_HELPERS["_entry"]
_health = _JOURNAL_HELPERS["_health"]


class RecordingExecutionSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles
        self.loads: list[tuple[ExecutionSeries, datetime, datetime]] = []

    def load(
        self,
        series: ExecutionSeries,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        self.loads.append((series, start, end))
        return tuple(
            candle
            for candle in self.candles
            if start <= candle.open_time < end
            and candle.symbol == series.symbol
            and candle.timeframe is series.timeframe
        )


def _execution_candle(index: int, open_price: Decimal) -> Candle:
    open_at = NOW + index * Timeframe.MINUTES_15.duration
    return Candle(
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_at,
        close_time=open_at + Timeframe.MINUTES_15.duration,
        open=open_price,
        high=open_price + Decimal(10),
        low=open_price - Decimal(10),
        close=open_price,
        volume=Decimal(5),
    )


def _execution_path(*opens: int) -> tuple[Candle, ...]:
    return tuple(_execution_candle(index, Decimal(price)) for index, price in enumerate(opens))


def _decision(
    *, side: CandidateAction = CandidateAction.LONG_CANDIDATE, outcome: str = "cleared"
) -> DecisionJournalEntry[object, object]:
    entry = cast(
        DecisionJournalEntry[object, object],
        _journal_entry(
            outcome,
            strategy_id="trend_pullback",
            strategy_version="1.0.0",
        ),
    )
    if side is CandidateAction.LONG_CANDIDATE or outcome != "cleared":
        return entry
    evaluation = replace(
        entry.strategy_evaluation,
        action=CandidateAction.SHORT_CANDIDATE,
        invalidation_reference_price=Decimal(135),
    )
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    safety = OperationalSafetyGate().evaluate(risk, _health(risk))
    return DecisionJournalEntry(entry.context, evaluation, risk, safety)


def _current_context(
    minutes: int,
    *,
    close: Decimal = Decimal(101),
    low: Decimal | None = None,
    high: Decimal | None = None,
) -> StrategyEvaluationContext:
    observed_at = NOW + timedelta(minutes=minutes)
    context = cast(
        StrategyEvaluationContext,
        _journal_context(source_id=SOURCE, symbol=BTC, observed_at=observed_at),
    )
    candle = replace(
        context.candle_15m,
        close=close,
        low=(min(context.candle_15m.low, close) if low is None else low),
        high=(max(context.candle_15m.high, close) if high is None else high),
    )
    anchor = replace(
        context.multi_timeframe_regime.regime_15m,
        direction=Direction.UP,
        direction_score=60,
        trend_strength=60,
        direction_evidence=(
            DirectionEvidence(DirectionComponent.EMA_ALIGNMENT, 20, "ema_20 above ema_50"),
            DirectionEvidence(DirectionComponent.MOMENTUM_16, 20, "positive"),
            DirectionEvidence(DirectionComponent.MOMENTUM_4, 20, "positive"),
            DirectionEvidence(DirectionComponent.CONFIRMED_HIGH, 0, "unavailable"),
            DirectionEvidence(DirectionComponent.CONFIRMED_LOW, 0, "unavailable"),
        ),
        regime_label=RegimeLabel.STRONG_UPTREND,
    )
    regime = replace(context.multi_timeframe_regime, regime_15m=anchor)
    return replace(context, candle_15m=candle, multi_timeframe_regime=regime)


def _run(
    contexts: tuple[StrategyEvaluationContext, ...],
    *,
    decision: DecisionJournalEntry[object, object] | None = None,
    candles: tuple[Candle, ...] | None = None,
    costs: ExecutionCostConfiguration | None = None,
    end: datetime | None = None,
) -> tuple[PairedTrendPullbackExitReplayResult, RecordingExecutionSource]:
    source = RecordingExecutionSource(
        _execution_path(100, 110, 120, 130) if candles is None else candles
    )
    runner = PairedTrendPullbackExitReplayRunner(BacktestExecutionSimulator(source))
    result = runner.run(
        decision or _decision(),
        contexts,
        ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
        costs
        or ExecutionCostConfiguration(
            spread_bps=Decimal(10),
            slippage_bps=Decimal(20),
            fee_bps=Decimal(20),
        ),
        start=NOW,
        end=end or NOW + timedelta(hours=1),
    )
    return result, source


def test_authoritative_long_entry_uses_one_shared_fill_for_both_branches() -> None:
    result, _ = _run((_current_context(30),))

    assert result.status is PairedExitReplayStatus.REPLAYED
    assert result.baseline is not None
    assert result.ema50_candidate is not None
    assert result.identical_starting_entry_fill is True
    assert result.identical_execution_cost_configuration is True
    assert result.baseline.entry_fill == result.ema50_candidate.entry_fill == result.entry_fill
    assert result.entry_order.side is OrderSide.BUY
    assert result.entry_order.quantity == _decision().risk_decision.approved_quantity
    assert result.entry_fill is not None
    assert result.entry_fill.fill_price == Decimal("110.275")
    assert result.baseline.accounting_events[0].fill_price == result.entry_fill.fill_price


def test_authoritative_short_entry_and_exit_use_opposite_shared_sides() -> None:
    decision = _decision(side=CandidateAction.SHORT_CANDIDATE)
    result, _ = _run((_current_context(30, close=Decimal(101)),), decision=decision)

    assert result.entry_order.side is OrderSide.SELL
    assert result.entry_fill is not None
    assert result.entry_fill.fill_price == Decimal("109.725")
    assert result.ema50_candidate is not None
    assert result.ema50_candidate.exit_request is not None
    assert result.ema50_candidate.exit_request.side is OrderSide.BUY
    assert result.ema50_candidate.exit_fill is not None
    assert result.ema50_candidate.exit_fill.fill_price == Decimal("120.300")


@pytest.mark.parametrize("outcome", ("no_action", "risk_blocked", "safe_mode"))
def test_non_authorized_decisions_are_rejected(outcome: str) -> None:
    with pytest.raises(PairedExitReplayError, match="does not authorize"):
        _run((), decision=_decision(outcome=outcome))


def test_mutated_decision_and_wrong_strategy_fail_closed() -> None:
    mutated = _decision()
    object.__setattr__(mutated.context.features_15m, "ema_50", Decimal(999))
    with pytest.raises(PairedExitReplayError, match="journal authority"):
        _run((), decision=mutated)

    wrong = cast(
        DecisionJournalEntry[object, object],
        _journal_entry("cleared", strategy_id="other", strategy_version="1.0.0"),
    )
    with pytest.raises(PairedExitReplayError, match="current Trend Pullback"):
        _run((), decision=wrong)


def test_entry_unfilled_is_explicit_and_creates_no_branch_position() -> None:
    result, _ = _run((), candles=())

    assert result.status is PairedExitReplayStatus.ENTRY_UNFILLED
    assert result.entry_fill is None
    assert result.entry_unfilled is not None
    assert result.baseline is None
    assert result.ema50_candidate is None
    assert result.comparison_state is PairedExitComparisonState.ENTRY_UNFILLED


def test_candidate_ema50_exit_fills_at_next_execution_open_not_reference() -> None:
    result, _ = _run((_current_context(30, close=Decimal(99)),))
    assert result.baseline is not None
    assert result.ema50_candidate is not None
    baseline = result.baseline
    candidate = result.ema50_candidate

    assert baseline.branch_policy is ExitReplayBranchPolicy.HOLD_ONLY
    assert baseline.position_closed is False
    assert baseline.hold_count == 1
    assert baseline.discretionary_close_signal_count == 0
    assert candidate.winning_exit_cause is HistoricalExitCause.EMA50_THESIS_FAILURE
    assert candidate.exit_evaluations[0].action is PositionExitAction.CLOSE_FULL
    assert candidate.exit_evaluations[0].exit_reference_price == Decimal(99)
    assert candidate.exit_fill is not None
    assert candidate.exit_fill.reference_open_price == Decimal(120)
    assert candidate.exit_fill.fill_price == Decimal("119.700")
    assert candidate.exit_fill.fill_price != Decimal(99)
    assert candidate.position_closed is True
    assert result.comparison_state is PairedExitComparisonState.BASELINE_OPEN_CANDIDATE_CLOSED
    assert result.realized_net_pnl_delta_candidate_minus_baseline is None


def test_long_stop_uses_low_equality_and_wins_same_bar_over_ema50() -> None:
    result, _ = _run((_current_context(30, close=Decimal(70), low=Decimal(70)),))
    assert result.baseline is not None
    assert result.ema50_candidate is not None
    for branch in (result.baseline, result.ema50_candidate):
        assert branch.winning_exit_cause is HistoricalExitCause.PROTECTIVE_STOP
        assert branch.protective_stop_breach_at == NOW + timedelta(minutes=30)
        assert branch.exit_fill is not None
        assert branch.exit_fill.reference_open_price == Decimal(120)
        assert branch.exit_fill.fill_price != branch.stop_basis
    assert result.ema50_candidate.protective_stop_precedence_applied is True
    assert result.ema50_candidate.discretionary_close_signal_count == 1


def test_short_stop_uses_high_equality() -> None:
    decision = _decision(side=CandidateAction.SHORT_CANDIDATE)
    result, _ = _run(
        (_current_context(30, close=Decimal(130), high=Decimal(135)),),
        decision=decision,
    )
    assert result.baseline is not None
    assert result.ema50_candidate is not None
    assert result.baseline.winning_exit_cause is HistoricalExitCause.PROTECTIVE_STOP
    assert result.ema50_candidate.winning_exit_cause is HistoricalExitCause.PROTECTIVE_STOP
    assert result.ema50_candidate.protective_stop_precedence_applied is True


def test_both_closed_exposes_only_shared_accounting_realized_delta() -> None:
    contexts = (
        _current_context(30, close=Decimal(99)),
        _current_context(45, close=Decimal(101), low=Decimal(75)),
    )
    result, _ = _run(contexts)
    assert result.baseline is not None
    assert result.ema50_candidate is not None
    assert result.comparison_state is PairedExitComparisonState.BOTH_CLOSED
    assert result.baseline.exit_fill is not None
    assert result.ema50_candidate.exit_fill is not None
    assert result.baseline.exit_fill.fill_at == NOW + timedelta(minutes=45)
    assert result.ema50_candidate.exit_fill.fill_at == NOW + timedelta(minutes=30)
    assert result.exit_fill_time_delta_candidate_minus_baseline == timedelta(minutes=-15)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        expected_delta = (
            result.ema50_candidate.final_position.cumulative_net_realized_pnl
            - result.baseline.final_position.cumulative_net_realized_pnl
        )
    assert result.realized_net_pnl_delta_candidate_minus_baseline == expected_delta
    assert result.baseline.realized_gross_pnl == (
        result.baseline.accounting_events[-1].cumulative_gross_realized_pnl
    )
    assert result.ema50_candidate.total_fees == (
        result.ema50_candidate.accounting_events[-1].cumulative_fees
    )


def test_unresolved_and_exit_unfilled_states_are_not_force_closed() -> None:
    unresolved, _ = _run((_current_context(30, close=Decimal(101)),))
    assert unresolved.baseline is not None
    assert unresolved.baseline.position_closed is False
    assert unresolved.baseline.realized_net_pnl is None
    assert unresolved.baseline.unresolved_open_quantity > 0
    assert unresolved.baseline.unresolved_average_entry_price is not None

    unfilled, _ = _run(
        (_current_context(30, close=Decimal(99)),),
        candles=(
            _execution_candle(0, Decimal(100)),
            _execution_candle(1, Decimal(110)),
        ),
        end=NOW + timedelta(minutes=45),
    )
    assert unfilled.ema50_candidate is not None
    assert unfilled.ema50_candidate.exit_request is not None
    assert unfilled.ema50_candidate.exit_unfilled is not None
    assert unfilled.ema50_candidate.exit_fill is None
    assert unfilled.ema50_candidate.position_closed is False
    assert unfilled.ema50_candidate.realized_net_pnl is None


def test_post_end_execution_candle_cannot_change_bounded_result() -> None:
    context = (_current_context(30, close=Decimal(99)),)
    bounded = (
        _execution_candle(0, Decimal(100)),
        _execution_candle(1, Decimal(110)),
    )
    with_future = (*bounded, _execution_candle(3, Decimal(1000)))
    first, _ = _run(context, candles=bounded, end=NOW + timedelta(minutes=45))
    second, _ = _run(context, candles=with_future, end=NOW + timedelta(minutes=45))
    assert first == second


def test_duplicate_decreasing_or_mutated_current_context_fails_closed() -> None:
    first = _current_context(30)
    second = _current_context(45)
    with pytest.raises(PairedExitReplayError, match="strictly increase"):
        _run((second, first))
    with pytest.raises(PairedExitReplayError, match="strictly increase"):
        _run((first, first))

    mutated = _current_context(30)
    object.__setattr__(mutated.features_15m, "ema_50", Decimal("NaN"))
    with pytest.raises(PairedExitReplayError, match="context is invalid"):
        _run((mutated,))


def test_same_run_is_decimal_deterministic_immutable_and_detached() -> None:
    contexts = (_current_context(30, close=Decimal(99)),)
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal("1.23456789"),
        slippage_bps=Decimal("2.34567891"),
        fee_bps=Decimal("3.45678912"),
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        first, _ = _run(contexts, costs=costs)
        setcontext(Context(prec=50))
        second, _ = _run(contexts, costs=costs)
    finally:
        setcontext(original)
    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.status = PairedExitReplayStatus.ENTRY_UNFILLED  # type: ignore[misc]
    object.__setattr__(contexts[0].candle_15m, "close", Decimal(500))
    assert first.ema50_candidate is not None
    assert first.ema50_candidate.exit_evaluations[0].exit_reference_price == Decimal(99)


def test_research_module_has_no_runtime_integration_or_duplicate_formula_dependencies() -> None:
    tree = ast.parse(inspect.getsource(replay_module))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        forbidden in name
        for name in imports
        for forbidden in ("paper", "live", "dashboard", "experience")
    )
    source = inspect.getsource(replay_module)
    assert "price_simulated_market_fill" not in source
    assert "PositionAccountingCore(" not in source
    assert "TrendPullbackEma50ExitStrategy" in source
    assert "EntryOrderIntentAuthorizer" in source
    assert "BacktestExecutionSimulator" in source
