"""Environment-neutral strategy contracts and candidate evaluators."""

from typing import TYPE_CHECKING

from adaptive_trading_bot.strategies.breakout_retest import (
    ArmedBreakout,
    BreakoutRetestConfiguration,
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    BreakoutRetestStateError,
    BreakoutRetestStrategy,
    BreakoutSide,
)
from adaptive_trading_bot.strategies.breakout_retest_zone_invalidation import (
    BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID,
    BreakoutRetestZoneBoundary,
    BreakoutRetestZoneInvalidationAudit,
    BreakoutRetestZoneInvalidationReasonCode,
    BreakoutRetestZoneInvalidationStrategy,
)
from adaptive_trading_bot.strategies.context import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    Strategy,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
)
from adaptive_trading_bot.strategies.failed_breakout_reversion import (
    BASE_COSTS as FAILED_BREAKOUT_BASE_COSTS,
)
from adaptive_trading_bot.strategies.failed_breakout_reversion import (
    POLICY_ID as FAILED_BREAKOUT_REVERSION_POLICY_ID,
)
from adaptive_trading_bot.strategies.failed_breakout_reversion import (
    FailedBreakoutReversionReasonCode,
    FailedBreakoutReversionRuleAudit,
    FailedBreakoutReversionStrategy,
)
from adaptive_trading_bot.strategies.range_context import (
    RangeContextSnapshot,
    RangeDetectorSeriesState,
    RangePivotIdentity,
    RangePivotReference,
    RangeReasonCode,
    RangeStateError,
    RangeStatus,
    ValidatedRange,
    ValidatedRangeConfiguration,
    ValidatedRangeDetector,
)
from adaptive_trading_bot.strategies.range_mean_reversion import (
    RangeContextDetector,
    RangeMeanReversionConfiguration,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    RangeMeanReversionSide,
    RangeMeanReversionStrategy,
)
from adaptive_trading_bot.strategies.trend_pullback import (
    StrategyReasonCode,
    TrendPullbackConfiguration,
    TrendPullbackRuleAudit,
    TrendPullbackStrategy,
)

if TYPE_CHECKING:
    from adaptive_trading_bot.strategies.exit_core import (
        HoldOnlyExitReasonCode,
        HoldOnlyExitRuleAudit,
        HoldOnlyExitStrategy,
        OpenPositionIdentity,
        PositionExitAction,
        PositionExitEvaluation,
        PositionExitEvaluationContext,
        PositionExitEvaluationIdentity,
        PositionExitStrategy,
        PositionExitValidationError,
        PositionLifecycleStatus,
        PositionSide,
    )
    from adaptive_trading_bot.strategies.trend_pullback_exit import (
        Ema50ComparisonRelation,
        TrendPullbackEma50ExitStrategy,
        TrendPullbackExitReasonCode,
        TrendPullbackExitRuleAudit,
    )

_EXIT_CORE_EXPORTS = frozenset(
    {
        "HoldOnlyExitReasonCode",
        "HoldOnlyExitRuleAudit",
        "HoldOnlyExitStrategy",
        "OpenPositionIdentity",
        "PositionExitAction",
        "PositionExitEvaluation",
        "PositionExitEvaluationContext",
        "PositionExitEvaluationIdentity",
        "PositionExitStrategy",
        "PositionExitValidationError",
        "PositionLifecycleStatus",
        "PositionSide",
    }
)

_TREND_PULLBACK_EXIT_EXPORTS = frozenset(
    {
        "Ema50ComparisonRelation",
        "TrendPullbackEma50ExitStrategy",
        "TrendPullbackExitReasonCode",
        "TrendPullbackExitRuleAudit",
    }
)


def __getattr__(name: str) -> object:
    """Load exit contracts lazily around the Journal/Risk/Strategies cycle."""

    if name in _EXIT_CORE_EXPORTS:
        from adaptive_trading_bot.strategies import exit_core

        return getattr(exit_core, name)
    if name in _TREND_PULLBACK_EXIT_EXPORTS:
        from adaptive_trading_bot.strategies import trend_pullback_exit

        return getattr(trend_pullback_exit, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID",
    "FAILED_BREAKOUT_BASE_COSTS",
    "FAILED_BREAKOUT_REVERSION_POLICY_ID",
    "ArmedBreakout",
    "BreakoutRetestConfiguration",
    "BreakoutRetestReasonCode",
    "BreakoutRetestRuleAudit",
    "BreakoutRetestStateError",
    "BreakoutRetestStrategy",
    "BreakoutRetestZoneBoundary",
    "BreakoutRetestZoneInvalidationAudit",
    "BreakoutRetestZoneInvalidationReasonCode",
    "BreakoutRetestZoneInvalidationStrategy",
    "BreakoutSide",
    "CandidateAction",
    "Ema50ComparisonRelation",
    "FailedBreakoutReversionReasonCode",
    "FailedBreakoutReversionRuleAudit",
    "FailedBreakoutReversionStrategy",
    "HoldOnlyExitReasonCode",
    "HoldOnlyExitRuleAudit",
    "HoldOnlyExitStrategy",
    "OpenPositionIdentity",
    "PositionExitAction",
    "PositionExitEvaluation",
    "PositionExitEvaluationContext",
    "PositionExitEvaluationIdentity",
    "PositionExitStrategy",
    "PositionExitValidationError",
    "PositionLifecycleStatus",
    "PositionSide",
    "RangeContextDetector",
    "RangeContextSnapshot",
    "RangeDetectorSeriesState",
    "RangeMeanReversionConfiguration",
    "RangeMeanReversionReasonCode",
    "RangeMeanReversionRuleAudit",
    "RangeMeanReversionSide",
    "RangeMeanReversionStrategy",
    "RangePivotIdentity",
    "RangePivotReference",
    "RangeReasonCode",
    "RangeStateError",
    "RangeStatus",
    "SourceIdentity",
    "Strategy",
    "StrategyContextError",
    "StrategyEvaluation",
    "StrategyEvaluationContext",
    "StrategyReasonCode",
    "TrendPullbackConfiguration",
    "TrendPullbackEma50ExitStrategy",
    "TrendPullbackExitReasonCode",
    "TrendPullbackExitRuleAudit",
    "TrendPullbackRuleAudit",
    "TrendPullbackStrategy",
    "ValidatedRange",
    "ValidatedRangeConfiguration",
    "ValidatedRangeDetector",
    "assemble_strategy_contexts",
]
