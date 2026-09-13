import ast
import inspect
import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.strategies.trend_pullback_exit as exit_policy_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    Ema50ComparisonRelation,
    OpenPositionIdentity,
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitStrategy,
    PositionExitValidationError,
    PositionLifecycleStatus,
    PositionSide,
    StrategyEvaluationContext,
    TrendPullbackEma50ExitStrategy,
    TrendPullbackExitReasonCode,
    TrendPullbackExitRuleAudit,
    TrendPullbackStrategy,
)

_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
_market_context = cast(Callable[..., StrategyEvaluationContext], _JOURNAL_HELPERS["_context"])

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
SOURCE = SourceId("ema50-exit-test")
BTC = TradingSymbol("BTC", "USDT")


def _context(
    *,
    observed_at: datetime = NOW,
    close: Decimal = Decimal(105),
    ema50: Decimal | None = Decimal(100),
    warmed: bool = True,
    context_ready: bool = True,
    side: PositionSide = PositionSide.LONG,
    decision_at: datetime | None = None,
    fill_id: str = "entry-1",
) -> PositionExitEvaluationContext:
    strategy = TrendPullbackStrategy()
    decision_at = decision_at or NOW - timedelta(hours=1)
    action = (
        CandidateAction.LONG_CANDIDATE
        if side is PositionSide.LONG
        else CandidateAction.SHORT_CANDIDATE
    )
    market = _market_context(source_id=SOURCE, symbol=BTC, observed_at=observed_at)
    candle = replace(
        market.candle_15m,
        high=max(market.candle_15m.high, close),
        low=min(market.candle_15m.low, close),
        close=close,
    )
    features = replace(market.features_15m, ema_50=ema50, is_fully_warmed=warmed)
    regime = replace(market.multi_timeframe_regime, is_fully_ready=context_ready)
    market = replace(
        market,
        candle_15m=candle,
        features_15m=features,
        multi_timeframe_regime=regime,
    )
    identity = DecisionIdentity(
        SOURCE,
        BTC,
        decision_at,
        strategy.strategy_id,
        strategy.strategy_version,
    )
    return PositionExitEvaluationContext(
        position_identity=OpenPositionIdentity(identity, fill_id),
        originating_candidate_action=action,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        source_id=SOURCE,
        symbol=BTC,
        position_side=side,
        entry_fill_at=decision_at + timedelta(minutes=1),
        entry_fill_price=Decimal(100),
        open_quantity=Decimal("2.5"),
        invalidation_reference_price=(Decimal(75) if side is PositionSide.LONG else Decimal(125)),
        current_market_context=market,
        observed_at=observed_at,
        lifecycle_status=PositionLifecycleStatus.OPEN,
        risk_policy_version="1.0.0",
        safety_policy_version="1.0.0",
    )


@pytest.mark.parametrize(
    ("side", "close", "expected_action", "expected_reason", "relation"),
    (
        (
            PositionSide.LONG,
            Decimal(99),
            PositionExitAction.CLOSE_FULL,
            TrendPullbackExitReasonCode.LONG_CLOSED_BELOW_EMA50,
            Ema50ComparisonRelation.BELOW,
        ),
        (
            PositionSide.LONG,
            Decimal(100),
            PositionExitAction.HOLD,
            TrendPullbackExitReasonCode.THESIS_REMAINS_VALID,
            Ema50ComparisonRelation.EQUAL,
        ),
        (
            PositionSide.LONG,
            Decimal(101),
            PositionExitAction.HOLD,
            TrendPullbackExitReasonCode.THESIS_REMAINS_VALID,
            Ema50ComparisonRelation.ABOVE,
        ),
        (
            PositionSide.SHORT,
            Decimal(101),
            PositionExitAction.CLOSE_FULL,
            TrendPullbackExitReasonCode.SHORT_CLOSED_ABOVE_EMA50,
            Ema50ComparisonRelation.ABOVE,
        ),
        (
            PositionSide.SHORT,
            Decimal(100),
            PositionExitAction.HOLD,
            TrendPullbackExitReasonCode.THESIS_REMAINS_VALID,
            Ema50ComparisonRelation.EQUAL,
        ),
        (
            PositionSide.SHORT,
            Decimal(99),
            PositionExitAction.HOLD,
            TrendPullbackExitReasonCode.THESIS_REMAINS_VALID,
            Ema50ComparisonRelation.BELOW,
        ),
    ),
)
def test_strict_symmetric_ema50_rule(
    side: PositionSide,
    close: Decimal,
    expected_action: PositionExitAction,
    expected_reason: TrendPullbackExitReasonCode,
    relation: Ema50ComparisonRelation,
) -> None:
    strategy = TrendPullbackEma50ExitStrategy()
    result = strategy.evaluate_exit(_context(side=side, close=close))

    assert isinstance(strategy, PositionExitStrategy)
    assert result.action is expected_action
    assert result.reasons_for == (expected_reason,)
    audit = cast(TrendPullbackExitRuleAudit, result.rule_audit)
    assert audit.comparison_relation is relation
    assert result.exit_reference_price == (
        close if expected_action is PositionExitAction.CLOSE_FULL else None
    )
    assert result.is_execution_authority is False


def test_unavailable_and_unready_evidence_holds_explicitly() -> None:
    strategy = TrendPullbackEma50ExitStrategy()
    unavailable = strategy.evaluate_exit(_context(close=Decimal(99), ema50=None))
    unready = strategy.evaluate_exit(_context(close=Decimal(99), warmed=False, context_ready=False))

    assert unavailable.action is PositionExitAction.HOLD
    assert unavailable.reasons_for == (TrendPullbackExitReasonCode.EMA50_UNAVAILABLE,)
    unavailable_audit = cast(TrendPullbackExitRuleAudit, unavailable.rule_audit)
    assert unavailable_audit.comparison_relation is Ema50ComparisonRelation.UNAVAILABLE
    assert unready.action is PositionExitAction.HOLD
    assert unready.reasons_for == (
        TrendPullbackExitReasonCode.CONTEXT_NOT_READY,
        TrendPullbackExitReasonCode.FEATURES_NOT_READY,
    )


@pytest.mark.parametrize("malformed", (Decimal("NaN"), Decimal("Infinity"), Decimal(0)))
def test_malformed_ema50_fails_closed(malformed: Decimal) -> None:
    with pytest.raises(PositionExitValidationError, match="EMA50"):
        TrendPullbackEma50ExitStrategy().evaluate_exit(_context(ema50=malformed))


def test_policy_identity_exactly_matches_entry_strategy_and_is_enforced() -> None:
    entry = TrendPullbackStrategy()
    exit_strategy = TrendPullbackEma50ExitStrategy()
    assert (exit_strategy.strategy_id, exit_strategy.strategy_version) == (
        entry.strategy_id,
        entry.strategy_version,
    )

    wrong_identity = DecisionIdentity(SOURCE, BTC, NOW - timedelta(hours=1), "other", "1.0.0")
    valid = _context()
    wrong = replace(
        valid,
        position_identity=OpenPositionIdentity(wrong_identity, "entry-1"),
        strategy_id="other",
    )
    with pytest.raises(PositionExitValidationError, match="strategy differ"):
        exit_strategy.evaluate_exit(wrong)


def test_current_context_is_the_only_causal_bar_used() -> None:
    strategy = TrendPullbackEma50ExitStrategy()
    current = _context(observed_at=NOW, close=Decimal(101))
    future = _context(observed_at=NOW + timedelta(minutes=15), close=Decimal(99))

    assert strategy.evaluate_exit(current).action is PositionExitAction.HOLD
    assert strategy.evaluate_exit(future).action is PositionExitAction.CLOSE_FULL
    assert strategy.evaluate_exit(current).action is PositionExitAction.HOLD


def test_mutated_context_and_evaluation_fail_closed_and_output_is_immutable() -> None:
    strategy = TrendPullbackEma50ExitStrategy()
    context = _context(close=Decimal(99))
    object.__setattr__(context.current_market_context.candle_15m, "close", Decimal(101))
    with pytest.raises(PositionExitValidationError, match="integrity seal"):
        strategy.evaluate_exit(context)

    result = strategy.evaluate_exit(_context(close=Decimal(99)))
    with pytest.raises(FrozenInstanceError):
        result.action = PositionExitAction.HOLD  # type: ignore[misc]
    object.__setattr__(result.rule_audit, "current_close", Decimal(101))
    with pytest.raises(PositionExitValidationError, match="integrity seal"):
        result.validate()


def test_same_inputs_are_exactly_deterministic_and_detached() -> None:
    context = _context(close=Decimal("99.0000000000000000001"))
    strategy = TrendPullbackEma50ExitStrategy()
    first = strategy.evaluate_exit(context)
    second = strategy.evaluate_exit(deepcopy(context))
    assert first == second
    audit = cast(TrendPullbackExitRuleAudit, first.rule_audit)
    assert audit.current_close == Decimal("99.0000000000000000001")
    assert "is_execution_authority" not in {
        item.name for item in fields(PositionExitEvaluation) if item.init
    }


def test_ambient_decimal_context_does_not_change_policy_output() -> None:
    context = _context(close=Decimal("99.99999999999999999999999999999999"))
    strategy = TrendPullbackEma50ExitStrategy()
    original = getcontext().copy()
    try:
        setcontext(Context(prec=4, rounding=ROUND_DOWN))
        first = strategy.evaluate_exit(context)
        setcontext(Context(prec=50))
        second = strategy.evaluate_exit(context)
    finally:
        setcontext(original)
    assert first == second


def test_audit_is_typed_complete_and_protective_stop_remains_delegated() -> None:
    result = TrendPullbackEma50ExitStrategy().evaluate_exit(_context(close=Decimal(99)))
    assert result.rule_audit == TrendPullbackExitRuleAudit(
        current_close=Decimal(99),
        ema_50=Decimal(100),
        position_side=PositionSide.LONG,
        comparison_relation=Ema50ComparisonRelation.BELOW,
        features_fully_warmed=True,
        ema50_available=True,
        context_fully_ready=True,
        exit_policy_version="trend-pullback-ema50-thesis-exit-v1",
        protective_stop_delegated=True,
    )


def test_strategy_module_has_no_forbidden_runtime_or_research_dependencies() -> None:
    tree = ast.parse(inspect.getsource(exit_policy_module))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        forbidden in name
        for name in imports
        for forbidden in (
            "paper",
            "orders",
            "exchange",
            "live",
            "dashboard",
            "experience",
            "research",
            "accounting",
        )
    )
    source = inspect.getsource(exit_policy_module).lower()
    assert not any(
        forbidden in source
        for forbidden in (
            "take_profit",
            "r_multiple",
            "trailing_stop",
            "time_stop",
            "break_even",
        )
    )
    assert tuple(CandidateAction) == (
        CandidateAction.LONG_CANDIDATE,
        CandidateAction.SHORT_CANDIDATE,
        CandidateAction.NO_TRADE,
    )
