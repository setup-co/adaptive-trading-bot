import runpy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.risk import OperationalSafetyGate, RiskManager, RiskPortfolioSnapshot
from adaptive_trading_bot.strategies import (
    CandidateAction,
    FailedBreakoutReversionReasonCode,
    FailedBreakoutReversionStrategy,
)

HELPERS = runpy.run_path(str(Path(__file__).with_name("test_range_mean_reversion.py")))
_context = HELPERS["_context"]


def _warm(strategy: FailedBreakoutReversionStrategy) -> None:
    for index in range(1, 17):
        result = strategy.evaluate(
            _context(
                index,
                open_price=Decimal(100),
                high=Decimal(120),
                low=Decimal(80),
                close=Decimal(100),
            )
        )
        assert result.action is CandidateAction.NO_TRADE


def test_long_and_short_use_previous_16_and_seal_midpoint() -> None:
    long_strategy = FailedBreakoutReversionStrategy()
    _warm(long_strategy)
    long = long_strategy.evaluate(
        _context(
            17,
            open_price=Decimal(90),
            high=Decimal(110),
            low=Decimal(79),
            close=Decimal(90),
        )
    )
    assert long.action is CandidateAction.LONG_CANDIDATE
    assert long.reference_price == Decimal(90)
    assert long.invalidation_reference_price == Decimal(78)
    assert long.rule_audit.reference_high == Decimal(120)
    assert long.rule_audit.reference_low == Decimal(80)
    assert long.rule_audit.sealed_midpoint == Decimal(100)

    short_strategy = FailedBreakoutReversionStrategy()
    _warm(short_strategy)
    short = short_strategy.evaluate(
        _context(
            17,
            open_price=Decimal(110),
            high=Decimal(121),
            low=Decimal(90),
            close=Decimal(110),
        )
    )
    assert short.action is CandidateAction.SHORT_CANDIDATE
    assert short.invalidation_reference_price == Decimal(122)
    assert short.rule_audit.sealed_midpoint == Decimal(100)


def test_rejections_are_explicit_and_equality_is_not_breakout() -> None:
    both = FailedBreakoutReversionStrategy()
    _warm(both)
    result = both.evaluate(
        _context(
            17,
            open_price=Decimal(100),
            high=Decimal(121),
            low=Decimal(79),
            close=Decimal(90),
        )
    )
    assert result.reasons_against == (FailedBreakoutReversionReasonCode.BOTH_BOUNDARIES_CROSSED,)

    equality = FailedBreakoutReversionStrategy()
    _warm(equality)
    result = equality.evaluate(
        _context(
            17,
            open_price=Decimal(100),
            high=Decimal(120),
            low=Decimal(80),
            close=Decimal(90),
        )
    )
    assert result.reasons_against == (
        FailedBreakoutReversionReasonCode.NO_QUALIFYING_FAILED_EXCURSION,
    )


def test_base_cost_room_rejects_tiny_midpoint_distance() -> None:
    strategy = FailedBreakoutReversionStrategy()
    _warm(strategy)
    result = strategy.evaluate(
        _context(
            17,
            open_price=Decimal("99.99"),
            high=Decimal(110),
            low=Decimal(79),
            close=Decimal("99.99"),
        )
    )
    assert result.action is CandidateAction.NO_TRADE
    assert result.reasons_against == (FailedBreakoutReversionReasonCode.BASE_COST_ROOM_REJECTED,)
    assert result.rule_audit.modeled_net_target_pnl is not None
    assert result.rule_audit.modeled_net_target_pnl < 0


def test_readiness_and_extreme_volatility_block_full_authorization_chain() -> None:
    for changes, expected in (
        ({"is_fully_ready": False}, FailedBreakoutReversionReasonCode.REQUIRED_CONTEXT_NOT_READY),
        (
            {"has_extreme_volatility": True},
            FailedBreakoutReversionReasonCode.EXTREME_VOLATILITY_BLOCKED,
        ),
    ):
        strategy = FailedBreakoutReversionStrategy()
        _warm(strategy)
        base = _context(
            17,
            open_price=Decimal(90),
            high=Decimal(110),
            low=Decimal(79),
            close=Decimal(90),
        )
        context = replace(
            base,
            multi_timeframe_regime=replace(base.multi_timeframe_regime, **changes),
        )
        evaluation = strategy.evaluate(context)
        assert evaluation.action is CandidateAction.NO_TRADE
        assert evaluation.reasons_against == (expected,)
        risk = RiskManager().evaluate(
            evaluation,
            RiskPortfolioSnapshot(
                context.observed_at,
                "USDT",
                Decimal(10000),
                Decimal(10000),
                Decimal(10000),
            ),
        )
        safety = OperationalSafetyGate().evaluate(
            risk,
            _research_health(context, ResearchSafetyAssumptionProfile()),
        )
        journal = DecisionJournalEntry(context, evaluation, risk, safety)
        journal.validate()
        authorization = EntryOrderIntentAuthorizer().authorize(journal)
        assert authorization.status is OrderIntentAuthorizationStatus.NO_INTENT
        assert authorization.intent is None
