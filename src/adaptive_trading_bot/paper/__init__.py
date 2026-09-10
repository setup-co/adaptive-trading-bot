"""Deterministic Paper execution and process-local coordination."""

from adaptive_trading_bot.paper.coordinator import (
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorError,
    PaperPortfolioCoordinatorSnapshot,
    PaperPortfolioStartBatchResult,
    PaperPortfolioStartBatchStatus,
    PaperPortfolioStartError,
    PaperStartedIntentSnapshot,
)
from adaptive_trading_bot.paper.execution import (
    PaperAuthorizationError,
    PaperAuthorizationSnapshot,
    PaperExecutionError,
    PaperExecutionObservation,
    PaperLifecycleError,
    PaperMarketFill,
    PaperObservationError,
    PaperOrderIdentity,
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
)

__all__ = [
    "PaperAuthorizationError",
    "PaperAuthorizationSnapshot",
    "PaperExecutionError",
    "PaperExecutionObservation",
    "PaperLifecycleError",
    "PaperMarketFill",
    "PaperObservationError",
    "PaperOrderIdentity",
    "PaperOrderLifecycle",
    "PaperOrderSimulator",
    "PaperOrderStatus",
    "PaperPortfolioBatchError",
    "PaperPortfolioCoordinator",
    "PaperPortfolioCoordinatorError",
    "PaperPortfolioCoordinatorSnapshot",
    "PaperPortfolioStartBatchResult",
    "PaperPortfolioStartBatchStatus",
    "PaperPortfolioStartError",
    "PaperStartedIntentSnapshot",
]
