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
    from adaptive_trading_bot.journal.exit_evaluation import (
        ExitEvaluationJournalAppendError,
        ExitEvaluationJournalAppendResult,
        ExitEvaluationJournalBatch,
        ExitEvaluationJournalEntry,
        ExitEvaluationJournalSnapshot,
        ExitEvaluationJournalValidationError,
        InMemoryExitEvaluationJournal,
    )
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
_EXIT_EVALUATION_EXPORTS = frozenset(
    {
        "ExitEvaluationJournalAppendError",
        "ExitEvaluationJournalAppendResult",
        "ExitEvaluationJournalBatch",
        "ExitEvaluationJournalEntry",
        "ExitEvaluationJournalSnapshot",
        "ExitEvaluationJournalValidationError",
        "InMemoryExitEvaluationJournal",
    }
)


def __getattr__(name: str) -> object:
    """Load journal contracts lazily around package dependency cycles."""

    if name in _TRADE_OUTCOME_EXPORTS:
        from adaptive_trading_bot.journal import trade_outcome

        return getattr(trade_outcome, name)
    if name in _EXIT_EVALUATION_EXPORTS:
        from adaptive_trading_bot.journal import exit_evaluation

        return getattr(exit_evaluation, name)
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
    "ExitEvaluationJournalAppendError",
    "ExitEvaluationJournalAppendResult",
    "ExitEvaluationJournalBatch",
    "ExitEvaluationJournalEntry",
    "ExitEvaluationJournalSnapshot",
    "ExitEvaluationJournalValidationError",
    "InMemoryCompletedTradeJournal",
    "InMemoryDecisionJournal",
    "InMemoryExitEvaluationJournal",
    "PaperOrderIdentityLike",
    "TradeExitReason",
    "TradeOutcomeClassification",
]
