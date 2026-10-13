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

import adaptive_trading_bot.strategies.exit_core as exit_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    HoldOnlyExitReasonCode,
    HoldOnlyExitRuleAudit,
    HoldOnlyExitStrategy,
    OpenPositionIdentity,
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitStrategy,
    PositionExitValidationError,
    PositionLifecycleStatus,
    PositionSide,
    StrategyEvaluationContext,
)

_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
_context = cast(Callable[..., StrategyEvaluationContext], _JOURNAL_HELPERS["_context"])

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
SOURCE = SourceId("exit-test")
BTC = TradingSymbol("BTC", "USDT")


def _exit_context(
    *,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    source_id: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
    observed_at: datetime = NOW,
) -> PositionExitEvaluationContext:
    decision_at = observed_at - timedelta(minutes=15)
    decision = DecisionIdentity(
        source_id,
        symbol,
        decision_at,
        "trend-pullback",
        "1.2.3",
    )
    side = PositionSide.LONG if action is CandidateAction.LONG_CANDIDATE else PositionSide.SHORT
    return PositionExitEvaluationContext(
        position_identity=OpenPositionIdentity(decision, f"entry-{source_id.value}"),
        originating_candidate_action=action,
        strategy_id=decision.strategy_id,
        strategy_version=decision.strategy_version,
        source_id=source_id,
        symbol=symbol,
        position_side=side,
        entry_fill_at=decision_at + timedelta(minutes=1),
        entry_fill_price=Decimal(100),
        open_quantity=Decimal("2.5"),
        invalidation_reference_price=(Decimal(75) if side is PositionSide.LONG else Decimal(125)),
        current_market_context=_context(
            source_id=source_id,
            symbol=symbol,
            observed_at=observed_at,
        ),
        observed_at=observed_at,
        lifecycle_status=PositionLifecycleStatus.OPEN,
        risk_policy_version="1.0.0",
        safety_policy_version="1.0.0",
    )


def test_long_open_position_context_preserves_exact_causal_provenance() -> None:
    context = _exit_context()

    context.validate()
    assert context.position_side is PositionSide.LONG
    assert context.originating_candidate_action is CandidateAction.LONG_CANDIDATE
    assert (
        context.strategy_id == context.position_identity.originating_decision_identity.strategy_id
    )
    assert context.source_id == context.current_market_context.source_id
    assert context.symbol == context.current_market_context.symbol
    assert context.entry_fill_at < context.observed_at


def test_short_open_position_context_preserves_exact_causal_provenance() -> None:
    context = _exit_context(action=CandidateAction.SHORT_CANDIDATE)

    context.validate()
    assert context.position_side is PositionSide.SHORT
    assert context.originating_candidate_action is CandidateAction.SHORT_CANDIDATE
    assert context.invalidation_reference_price > context.entry_fill_price


def test_originating_identity_strategy_source_and_symbol_must_match_exactly() -> None:
    valid = _exit_context()
    with pytest.raises(PositionExitValidationError, match="decision provenance"):
        replace(valid, strategy_id="other")
    with pytest.raises(PositionExitValidationError, match="decision provenance"):
        replace(valid, strategy_version="9.9.9")
    with pytest.raises(PositionExitValidationError, match="decision provenance"):
        replace(valid, source_id=SourceId("other"))
    with pytest.raises(PositionExitValidationError, match="decision provenance"):
        replace(valid, symbol=TradingSymbol("ETH", "USDT"))


def test_exit_context_enforces_strict_utc_and_entry_chronology() -> None:
    valid = _exit_context()
    equal_entry = replace(
        valid, entry_fill_at=valid.position_identity.originating_decision_identity.observed_at
    )
    equal_entry.validate()
    with pytest.raises(ValueError, match="UTC"):
        replace(valid, observed_at=NOW.replace(tzinfo=None))
    with pytest.raises(PositionExitValidationError, match="chronology"):
        replace(
            valid,
            entry_fill_at=valid.position_identity.originating_decision_identity.observed_at
            - timedelta(microseconds=1),
        )
    with pytest.raises(PositionExitValidationError, match="chronology"):
        replace(valid, entry_fill_at=NOW + timedelta(microseconds=1))
    with pytest.raises(PositionExitValidationError, match="chronology"):
        replace(valid, entry_fill_at=NOW)


def test_mutated_current_strategy_context_fails_closed() -> None:
    context = _exit_context()
    object.__setattr__(context.current_market_context.features_15m, "rsi_14", Decimal(99))

    with pytest.raises(PositionExitValidationError, match="integrity seal"):
        context.validate()


def test_side_candidate_and_open_lifecycle_are_enforced() -> None:
    valid = _exit_context()
    with pytest.raises(PositionExitValidationError, match="position side"):
        replace(valid, position_side=PositionSide.SHORT)
    with pytest.raises(PositionExitValidationError, match="entry candidate"):
        replace(valid, originating_candidate_action=CandidateAction.NO_TRADE)
    with pytest.raises(PositionExitValidationError, match="open position"):
        replace(valid, lifecycle_status=PositionLifecycleStatus.CLOSED)


@pytest.mark.parametrize("quantity", (Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")))
def test_open_quantity_must_be_positive_and_finite(quantity: Decimal) -> None:
    with pytest.raises(PositionExitValidationError, match="open_quantity"):
        replace(_exit_context(), open_quantity=quantity)


@pytest.mark.parametrize(
    "stop",
    (Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity"), Decimal(100), Decimal(101)),
)
def test_long_invalidation_provenance_must_be_valid(stop: Decimal) -> None:
    with pytest.raises(PositionExitValidationError, match="invalidation"):
        replace(_exit_context(), invalidation_reference_price=stop)


def test_hold_only_baseline_is_deterministic_detached_and_non_executable() -> None:
    context = _exit_context()
    strategy = HoldOnlyExitStrategy(context.strategy_id, context.strategy_version)

    first = strategy.evaluate_exit(context)
    second = strategy.evaluate_exit(deepcopy(context))

    assert isinstance(strategy, PositionExitStrategy)
    assert first == second
    assert first.action is PositionExitAction.HOLD
    assert first.reasons_for == (HoldOnlyExitReasonCode.NO_DISCRETIONARY_EXIT_RULE,)
    assert first.reasons_against == ()
    assert first.exit_reference_price is None
    assert first.is_execution_authority is False
    assert first.rule_audit == HoldOnlyExitRuleAudit(
        active_exit_hypothesis=False,
        protective_stop_delegated=True,
        policy_version=strategy.exit_policy_version,
    )
    object.__setattr__(context.current_market_context.features_15m, "rsi_14", Decimal(1))
    first.validate()


def test_hold_output_is_immutable_and_raw_mutation_is_detected() -> None:
    result = HoldOnlyExitStrategy("trend-pullback", "1.2.3").evaluate_exit(_exit_context())
    with pytest.raises(FrozenInstanceError):
        result.action = PositionExitAction.CLOSE_FULL  # type: ignore[misc]

    object.__setattr__(result, "action", PositionExitAction.CLOSE_FULL)
    with pytest.raises(PositionExitValidationError, match="integrity seal"):
        result.validate()


def test_exit_evaluation_cannot_claim_execution_authority() -> None:
    result = HoldOnlyExitStrategy("trend-pullback", "1.2.3").evaluate_exit(_exit_context())
    assert "is_execution_authority" not in {
        item.name for item in fields(PositionExitEvaluation) if item.init
    }
    object.__setattr__(result, "is_execution_authority", True)
    with pytest.raises(PositionExitValidationError, match="integrity seal"):
        result.validate()


def test_ambient_decimal_context_cannot_change_hold_output() -> None:
    context = _exit_context()
    strategy = HoldOnlyExitStrategy(context.strategy_id, context.strategy_version)
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        first = strategy.evaluate_exit(context)
        setcontext(Context(prec=50))
        second = strategy.evaluate_exit(context)
    finally:
        setcontext(original)
    assert first == second


def test_entry_candidate_contract_is_unchanged_and_exit_core_has_no_forbidden_dependencies() -> (
    None
):
    assert tuple(CandidateAction) == (
        CandidateAction.LONG_CANDIDATE,
        CandidateAction.SHORT_CANDIDATE,
        CandidateAction.NO_TRADE,
    )
    tree = ast.parse(inspect.getsource(exit_module))
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
        )
    )
    source = inspect.getsource(exit_module).lower()
    assert "pnl" not in source
    assert "outcome_classification" not in source
    for forbidden_rule in (
        "take_profit",
        "r_multiple",
        "trailing_stop",
        "time_stop",
        "break_even",
    ):
        assert forbidden_rule not in source
