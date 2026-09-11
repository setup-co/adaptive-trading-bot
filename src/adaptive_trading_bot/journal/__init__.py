"""Environment-neutral decision and completed-trade journal contracts."""

from typing import TYPE_CHECKING

from adaptive_trading_bot.journal.decision import (
    DecisionIdentity,
    DecisionJournalAppendError,
    DecisionJournalAppendResult,
    DecisionJournalBatch,
    DecisionJournalEntry,
    DecisionJournalSnapshot,
    DecisionJournalValidationError,
    InMemoryDecisionJournal,
)

if TYPE_CHECKING:
    from adaptive_trading_bot.journal.trade_outcome import (
        CompletedTradeIdentity,
        CompletedTradeJournalAppendError,
        CompletedTradeJournalAppendResult,
        CompletedTradeJournalBatch,
        CompletedTradeJournalEntry,
        CompletedTradeJournalSnapshot,
        CompletedTradeJournalValidationError,
        InMemoryCompletedTradeJournal,
        PaperOrderIdentityLike,
        TradeExitReason,
        TradeOutcomeClassification,
    )

_TRADE_OUTCOME_EXPORTS = frozenset(
    {
        "CompletedTradeIdentity",
        "CompletedTradeJournalAppendError",
        "CompletedTradeJournalAppendResult",
        "CompletedTradeJournalBatch",
        "CompletedTradeJournalEntry",
        "CompletedTradeJournalSnapshot",
        "CompletedTradeJournalValidationError",
        "InMemoryCompletedTradeJournal",
        "PaperOrderIdentityLike",
        "TradeExitReason",
        "TradeOutcomeClassification",
    }
)


def __getattr__(name: str) -> object:
    """Load outcome contracts lazily around the orders/journal package cycle."""

    if name in _TRADE_OUTCOME_EXPORTS:
        from adaptive_trading_bot.journal import trade_outcome

        return getattr(trade_outcome, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CompletedTradeIdentity",
    "CompletedTradeJournalAppendError",
    "CompletedTradeJournalAppendResult",
    "CompletedTradeJournalBatch",
    "CompletedTradeJournalEntry",
    "CompletedTradeJournalSnapshot",
    "CompletedTradeJournalValidationError",
    "DecisionIdentity",
    "DecisionJournalAppendError",
    "DecisionJournalAppendResult",
    "DecisionJournalBatch",
    "DecisionJournalEntry",
    "DecisionJournalSnapshot",
    "DecisionJournalValidationError",
    "InMemoryCompletedTradeJournal",
    "InMemoryDecisionJournal",
    "PaperOrderIdentityLike",
    "TradeExitReason",
    "TradeOutcomeClassification",
]
