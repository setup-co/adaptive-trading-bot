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
from adaptive_trading_bot.strategies.context import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    Strategy,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
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


def __getattr__(name: str) -> object:
    """Load exit contracts lazily around the Journal/Risk/Strategies cycle."""

    if name in _EXIT_CORE_EXPORTS:
        from adaptive_trading_bot.strategies import exit_core

        return getattr(exit_core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ArmedBreakout",
    "BreakoutRetestConfiguration",
    "BreakoutRetestReasonCode",
    "BreakoutRetestRuleAudit",
    "BreakoutRetestStateError",
    "BreakoutRetestStrategy",
    "BreakoutSide",
    "CandidateAction",
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
    "TrendPullbackRuleAudit",
    "TrendPullbackStrategy",
    "ValidatedRange",
    "ValidatedRangeConfiguration",
    "ValidatedRangeDetector",
    "assemble_strategy_contexts",
]
