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
    "SourceIdentity",
    "Strategy",
    "StrategyContextError",
    "StrategyEvaluation",
    "StrategyEvaluationContext",
    "StrategyReasonCode",
    "TrendPullbackConfiguration",
    "TrendPullbackRuleAudit",
    "TrendPullbackStrategy",
    "assemble_strategy_contexts",
]
