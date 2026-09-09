"""Environment-neutral strategy contracts and candidate evaluators."""

from adaptive_trading_bot.strategies.context import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    Strategy,
    StrategyContextError,
    StrategyEvaluation,
    StrategyEvaluationContext,
    StrategyReasonCode,
    TrendPullbackRuleAudit,
)
from adaptive_trading_bot.strategies.trend_pullback import (
    TrendPullbackConfiguration,
    TrendPullbackStrategy,
)

__all__ = [
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
