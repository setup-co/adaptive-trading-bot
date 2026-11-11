import runpy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
    RiskReasonCode,
)
from adaptive_trading_bot.strategies import (
    CandidateAction,
    StrategyContextError,
    TrendImpulsePullbackReasonCode,
    TrendImpulsePullbackStrategy,
)
from adaptive_trading_bot.strategies.trend_impulse_pullback import Evaluation

HELPERS = runpy.run_path(str(Path(__file__).with_name("test_range_mean_reversion.py")))
_context = HELPERS["_context"]


def _long(
    strategy: TrendImpulsePullbackStrategy, *, impulse_body: Decimal = Decimal(15)
) -> Evaluation:
    strategy.evaluate(_context(1))
    impulse_close = Decimal(100) + impulse_body
    strategy.evaluate(
        _context(
            2,
            open_price=Decimal(100),
            high=Decimal(116),
            low=Decimal(96),
            close=impulse_close,
        )
    )
    strategy.evaluate(
        _context(
            3,
            open_price=Decimal(114),
            high=Decimal(114),
            low=Decimal(108),
            close=Decimal(110),
        )
    )
    return strategy.evaluate(
        _context(
            4,
            open_price=Decimal(110),
            high=Decimal(116),
            low=Decimal(108),
            close=Decimal(115),
        )
    )


def test_long_threshold_equality_uses_lagged_atr_and_seals_fixed_2r() -> None:
    result = _long(TrendImpulsePullbackStrategy())
    assert result.action is CandidateAction.LONG_CANDIDATE
    assert result.reference_price == Decimal(115)
    assert result.invalidation_reference_price == Decimal(107)
    assert result.rule_audit.atr_before_impulse == Decimal(10)
    assert result.rule_audit.atr_at_resumption == Decimal(10)
    assert result.rule_audit.retracement_boundary == Decimal("107.5")
    assert result.rule_audit.sealed_target == Decimal(131)
    assert TrendImpulsePullbackReasonCode.BASE_COST_ROOM_PASSED in result.reasons_for


def test_short_is_exactly_symmetric() -> None:
    strategy = TrendImpulsePullbackStrategy()
    strategy.evaluate(_context(1))
    strategy.evaluate(
        _context(
            2,
            open_price=Decimal(115),
            high=Decimal(119),
            low=Decimal(99),
            close=Decimal(100),
        )
    )
    strategy.evaluate(
        _context(
            3,
            open_price=Decimal(101),
            high=Decimal("107.5"),
            low=Decimal(101),
            close=Decimal(105),
        )
    )
    result = strategy.evaluate(
        _context(
            4,
            open_price=Decimal(105),
            high=Decimal(107),
            low=Decimal(99),
            close=Decimal(100),
        )
    )
    assert result.action is CandidateAction.SHORT_CANDIDATE
    assert result.invalidation_reference_price == Decimal("108.5")
    assert result.rule_audit.sealed_target == Decimal(83)


def test_strict_resumption_and_impulse_threshold_rejections() -> None:
    too_small = _long(TrendImpulsePullbackStrategy(), impulse_body=Decimal("14.999"))
    assert too_small.action is CandidateAction.NO_TRADE
    assert too_small.reasons_against == (TrendImpulsePullbackReasonCode.IMPULSE_NOT_QUALIFIED,)

    strategy = TrendImpulsePullbackStrategy()
    strategy.evaluate(_context(1))
    strategy.evaluate(
        _context(2, open_price=Decimal(100), high=Decimal(116), low=Decimal(96), close=Decimal(115))
    )
    strategy.evaluate(
        _context(
            3, open_price=Decimal(114), high=Decimal(114), low=Decimal(108), close=Decimal(110)
        )
    )
    equality = strategy.evaluate(
        _context(
            4, open_price=Decimal(110), high=Decimal(115), low=Decimal(108), close=Decimal(114)
        )
    )
    assert equality.reasons_against == (TrendImpulsePullbackReasonCode.RESUMPTION_NOT_CONFIRMED,)


@pytest.mark.parametrize(
    ("sequence", "changes", "expected"),
    [
        (2, {"is_fully_ready": False}, TrendImpulsePullbackReasonCode.REQUIRED_CONTEXT_NOT_READY),
        (
            3,
            {"has_extreme_volatility": True},
            TrendImpulsePullbackReasonCode.EXTREME_VOLATILITY_BLOCKED,
        ),
    ],
)
def test_readiness_and_extreme_guards_cover_the_whole_pattern(
    sequence: int, changes: dict[str, bool], expected: TrendImpulsePullbackReasonCode
) -> None:
    strategy = TrendImpulsePullbackStrategy()
    strategy.evaluate(_context(1))
    contexts = [
        _context(
            2, open_price=Decimal(100), high=Decimal(116), low=Decimal(96), close=Decimal(115)
        ),
        _context(
            3, open_price=Decimal(114), high=Decimal(114), low=Decimal(108), close=Decimal(110)
        ),
    ]
    index = sequence - 2
    contexts[index] = replace(
        contexts[index],
        multi_timeframe_regime=replace(contexts[index].multi_timeframe_regime, **changes),
    )
    for context in contexts:
        strategy.evaluate(context)
    current = _context(
        4, open_price=Decimal(110), high=Decimal(116), low=Decimal(108), close=Decimal(115)
    )
    evaluation = strategy.evaluate(current)
    assert evaluation.action is CandidateAction.NO_TRADE
    assert evaluation.reasons_against == (expected,)
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            current.observed_at, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)
        ),
    )
    safety = OperationalSafetyGate().evaluate(
        risk, _research_health(current, ResearchSafetyAssumptionProfile())
    )
    journal = DecisionJournalEntry(current, evaluation, risk, safety)
    journal.validate()
    authorization = EntryOrderIntentAuthorizer().authorize(journal)
    assert authorization.status is OrderIntentAuthorizationStatus.NO_INTENT


def test_gap_resets_and_duplicate_is_rejected() -> None:
    strategy = TrendImpulsePullbackStrategy()
    first = _context(1)
    strategy.evaluate(first)
    with pytest.raises(StrategyContextError):
        strategy.evaluate(first)
    gap = strategy.evaluate(_context(3))
    assert gap.reasons_against == (TrendImpulsePullbackReasonCode.HISTORY_GAP_RESET,)


def test_shared_risk_accepts_geometry_and_rejects_exhausted_daily_loss() -> None:
    evaluation = _long(TrendImpulsePullbackStrategy())
    observed_at = evaluation.observed_at
    accepted = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(observed_at, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    assert accepted.status is RiskDecisionStatus.APPROVED
    assert accepted.approved_risk_quote == Decimal(25)

    rejected = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(observed_at, "USDT", Decimal(9900), Decimal(10000), Decimal(10000)),
    )
    assert rejected.status is RiskDecisionStatus.REJECTED
    assert RiskReasonCode.DAILY_LOSS_LIMIT_REACHED in rejected.reasons
