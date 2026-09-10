"""Environment-neutral strategy contracts and candidate evaluators."""

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

__all__ = [
    "ArmedBreakout",
    "BreakoutRetestConfiguration",
    "BreakoutRetestReasonCode",
    "BreakoutRetestRuleAudit",
    "BreakoutRetestStateError",
    "BreakoutRetestStrategy",
    "BreakoutSide",
    "CandidateAction",
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
