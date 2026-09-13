"""Pure offline research contracts with no trading authority."""

from adaptive_trading_bot.research.experience_shadow_replay import (
    ExperienceObservedOutcomeGroup,
    ExperienceObservedOutcomeSummary,
    ExperienceOutcomeCurrencySummary,
    ExperienceShadowDecisionRecord,
    ExperienceShadowPopulationSummary,
    ExperienceShadowReplayConfiguration,
    ExperienceShadowReplayError,
    ExperienceShadowReplayEvaluator,
    ExperienceShadowReplayReport,
    ExperienceShadowResearchMode,
    ExperienceShadowStabilitySlice,
    ExperienceShadowStratificationDimension,
    ExperienceShadowStratum,
    ResearchDecimalRate,
)
from adaptive_trading_bot.research.trend_pullback_exit import (
    DescriptiveResearchRate,
    TrendPullbackExitPositionSummary,
    TrendPullbackExitResearchError,
    TrendPullbackExitResearchEvaluator,
    TrendPullbackExitResearchReport,
    TrendPullbackExitStratum,
)

__all__ = [
    "DescriptiveResearchRate",
    "ExperienceObservedOutcomeGroup",
    "ExperienceObservedOutcomeSummary",
    "ExperienceOutcomeCurrencySummary",
    "ExperienceShadowDecisionRecord",
    "ExperienceShadowPopulationSummary",
    "ExperienceShadowReplayConfiguration",
    "ExperienceShadowReplayError",
    "ExperienceShadowReplayEvaluator",
    "ExperienceShadowReplayReport",
    "ExperienceShadowResearchMode",
    "ExperienceShadowStabilitySlice",
    "ExperienceShadowStratificationDimension",
    "ExperienceShadowStratum",
    "ResearchDecimalRate",
    "TrendPullbackExitPositionSummary",
    "TrendPullbackExitResearchError",
    "TrendPullbackExitResearchEvaluator",
    "TrendPullbackExitResearchReport",
    "TrendPullbackExitStratum",
]
