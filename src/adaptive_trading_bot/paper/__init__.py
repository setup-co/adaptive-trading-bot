"""Deterministic Paper execution and process-local coordination."""

from adaptive_trading_bot.paper.accounting import (
    PaperFillAccountingAdapterError,
    paper_fill_to_position_accounting_fill,
)
from adaptive_trading_bot.paper.coordinator import (
    PaperOpenPositionSnapshot,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorError,
    PaperPortfolioCoordinatorSnapshot,
    PaperPortfolioObservationBatchResult,
    PaperPortfolioObservationError,
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
    "PaperFillAccountingAdapterError",
    "PaperLifecycleError",
    "PaperMarketFill",
    "PaperObservationError",
    "PaperOpenPositionSnapshot",
    "PaperOrderIdentity",
    "PaperOrderLifecycle",
    "PaperOrderSimulator",
    "PaperOrderStatus",
    "PaperPortfolioBatchError",
    "PaperPortfolioCoordinator",
    "PaperPortfolioCoordinatorError",
    "PaperPortfolioCoordinatorSnapshot",
    "PaperPortfolioObservationBatchResult",
    "PaperPortfolioObservationError",
    "PaperPortfolioStartBatchResult",
    "PaperPortfolioStartBatchStatus",
    "PaperPortfolioStartError",
    "PaperStartedIntentSnapshot",
    "paper_fill_to_position_accounting_fill",
]
