import runpy
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.research import (
    DescriptiveResearchRate,
    TrendPullbackExitResearchError,
    TrendPullbackExitResearchEvaluator,
)
from adaptive_trading_bot.strategies import PositionExitEvaluationContext

_STRATEGY_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "strategies" / "test_trend_pullback_exit.py")
)
_context = cast(Callable[..., PositionExitEvaluationContext], _STRATEGY_HELPERS["_context"])

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_report_is_deterministic_input_order_invariant_and_stops_after_first_close() -> None:
    contexts = (
        _context(observed_at=NOW, close=Decimal(101)),
        _context(observed_at=NOW + timedelta(minutes=15), close=Decimal(99)),
        _context(observed_at=NOW + timedelta(minutes=30), close=Decimal(98)),
    )
    evaluator = TrendPullbackExitResearchEvaluator()

    chronological = evaluator.evaluate(contexts)
    reordered = evaluator.evaluate(tuple(reversed(contexts)))

    assert chronological == reordered
    assert chronological.supplied_context_count == 3
    assert chronological.evaluated_context_count == 2
    assert chronological.ignored_after_first_close_count == 1
    assert chronological.hold_count == 1
    assert chronological.close_signal_count == 1
    assert chronological.positions[0].first_close_signal_at == NOW + timedelta(minutes=15)
    assert chronological.positions[0].holding_duration_to_first_signal == timedelta(minutes=74)
    assert chronological.is_trading_authority is False
    assert chronological.contains_execution_or_performance_claims is False


def test_future_observations_cannot_change_the_first_close_signal() -> None:
    before = (
        _context(observed_at=NOW, close=Decimal(101)),
        _context(observed_at=NOW + timedelta(minutes=15), close=Decimal(99)),
    )
    after = (
        *before,
        _context(observed_at=NOW + timedelta(minutes=30), close=Decimal(101)),
        _context(observed_at=NOW + timedelta(minutes=45), close=Decimal(98)),
    )
    evaluator = TrendPullbackExitResearchEvaluator()

    first = evaluator.evaluate(before).positions[0].first_close_signal
    second = evaluator.evaluate(after).positions[0].first_close_signal
    assert first == second


def test_position_without_signal_remains_explicitly_unresolved() -> None:
    report = TrendPullbackExitResearchEvaluator().evaluate(
        (
            _context(observed_at=NOW, close=Decimal(101)),
            _context(observed_at=NOW + timedelta(minutes=15), close=Decimal(100)),
        )
    )

    position = report.positions[0]
    assert position.remained_unresolved is True
    assert position.first_close_signal is None
    assert position.first_close_signal_at is None
    assert position.holding_duration_to_first_signal is None
    assert report.unresolved_position_count == 1
    assert report.never_close_fraction == DescriptiveResearchRate(1, 1, Decimal(1))


def test_empty_descriptive_population_is_truthfully_unavailable() -> None:
    report = TrendPullbackExitResearchEvaluator().evaluate(())
    assert report.position_count == 0
    assert report.never_close_fraction == DescriptiveResearchRate(0, 0, None)
    assert report.strata == ()


def test_duplicate_observation_and_contradictory_position_facts_fail_closed() -> None:
    duplicate = _context(observed_at=NOW, close=Decimal(101))
    with pytest.raises(TrendPullbackExitResearchError, match="unique chronological"):
        TrendPullbackExitResearchEvaluator().evaluate((duplicate, duplicate))

    changed_quantity = _context(observed_at=NOW + timedelta(minutes=15), close=Decimal(99))
    object.__setattr__(changed_quantity, "open_quantity", Decimal(3))
    with pytest.raises(TrendPullbackExitResearchError, match="invalid sealed"):
        TrendPullbackExitResearchEvaluator().evaluate((duplicate, changed_quantity))


def test_descriptive_rate_is_independent_of_ambient_decimal_context() -> None:
    contexts = (
        _context(observed_at=NOW, close=Decimal(101), fill_id="entry-1"),
        _context(
            observed_at=NOW,
            close=Decimal(99),
            decision_at=NOW - timedelta(hours=2),
            fill_id="entry-2",
        ),
        _context(
            observed_at=NOW,
            close=Decimal(101),
            decision_at=NOW - timedelta(hours=3),
            fill_id="entry-3",
        ),
    )
    evaluator = TrendPullbackExitResearchEvaluator()
    original = getcontext().copy()
    try:
        setcontext(Context(prec=3, rounding=ROUND_DOWN))
        first = evaluator.evaluate(contexts)
        setcontext(Context(prec=50))
        second = evaluator.evaluate(contexts)
    finally:
        setcontext(original)
    assert first == second
    assert first.never_close_fraction.value == Decimal("0.6666666666666666666666666666666667")
