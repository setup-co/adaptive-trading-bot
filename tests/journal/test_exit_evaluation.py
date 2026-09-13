import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.journal import (
    DecisionIdentity,
    ExitEvaluationJournalAppendError,
    ExitEvaluationJournalBatch,
    ExitEvaluationJournalEntry,
    ExitEvaluationJournalSnapshot,
    ExitEvaluationJournalValidationError,
    InMemoryExitEvaluationJournal,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    HoldOnlyExitStrategy,
    OpenPositionIdentity,
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionLifecycleStatus,
    PositionSide,
    StrategyEvaluationContext,
)

_JOURNAL_HELPERS = runpy.run_path(str(Path(__file__).parent / "test_decision_journal.py"))
_context = cast(Callable[..., StrategyEvaluationContext], _JOURNAL_HELPERS["_context"])

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _exit_context(
    source: str = "exit-journal",
    *,
    observed_at: datetime = NOW,
) -> PositionExitEvaluationContext:
    source_id = SourceId(source)
    current = _context(source_id=source_id, observed_at=observed_at)
    decision = DecisionIdentity(
        source_id,
        current.symbol,
        observed_at - timedelta(minutes=15),
        "trend-pullback",
        "1.2.3",
    )
    return PositionExitEvaluationContext(
        OpenPositionIdentity(decision, f"entry-{source}"),
        CandidateAction.LONG_CANDIDATE,
        decision.strategy_id,
        decision.strategy_version,
        source_id,
        current.symbol,
        PositionSide.LONG,
        decision.observed_at + timedelta(minutes=1),
        Decimal(100),
        Decimal(2),
        Decimal(75),
        current,
        observed_at,
        PositionLifecycleStatus.OPEN,
        "1.0.0",
        "1.0.0",
    )


def _hold_entry(
    source: str = "exit-journal", *, observed_at: datetime = NOW
) -> ExitEvaluationJournalEntry:
    context = _exit_context(source, observed_at=observed_at)
    evaluation = HoldOnlyExitStrategy(context.strategy_id, context.strategy_version).evaluate_exit(
        context
    )
    return ExitEvaluationJournalEntry(context, evaluation)


def _future_close_entry() -> ExitEvaluationJournalEntry:
    context = _exit_context("future-close")
    evaluation = PositionExitEvaluation.create(
        context,
        action=PositionExitAction.CLOSE_FULL,
        reasons_for=("future validated rule",),
        reasons_against=(),
        rule_audit="future close audit",
        exit_policy_version="future-close-policy-v1",
        exit_reference_price=context.current_market_context.candle_15m.close,
    )
    return ExitEvaluationJournalEntry(context, evaluation)


def test_journal_records_hold_and_future_close_capable_evaluations_without_execution() -> None:
    hold = _hold_entry()
    future_close = _future_close_entry()
    journal = InMemoryExitEvaluationJournal()

    result = journal.append_batch(ExitEvaluationJournalBatch(NOW, (future_close, hold)))
    snapshot = journal.snapshot()

    assert result.appended_count == 2
    assert tuple(entry.evaluation.action for entry in snapshot.entries) == (
        PositionExitAction.HOLD,
        PositionExitAction.CLOSE_FULL,
    )
    assert all(not entry.evaluation.is_execution_authority for entry in snapshot.entries)
    assert snapshot.entries == tuple(
        sorted(snapshot.entries, key=lambda item: item.identity.sort_key)
    )


def test_duplicate_exit_evaluation_identity_fails_closed() -> None:
    entry = _hold_entry()
    journal = InMemoryExitEvaluationJournal()
    batch = ExitEvaluationJournalBatch(NOW, (entry,))
    journal.append_batch(batch)

    with pytest.raises(ExitEvaluationJournalAppendError, match="already exists"):
        journal.append_batch(batch)
    with pytest.raises(ExitEvaluationJournalValidationError, match="duplicate"):
        ExitEvaluationJournalBatch(NOW, (entry, entry))


def test_journal_chronology_strictly_advances_and_same_time_order_is_deterministic() -> None:
    first = _hold_entry("z-source")
    second = _hold_entry("a-source")
    batch = ExitEvaluationJournalBatch(NOW, (first, second))
    assert batch.entries == tuple(sorted((first, second), key=lambda item: item.identity.sort_key))

    journal = InMemoryExitEvaluationJournal()
    journal.append_batch(batch)
    earlier = _hold_entry("earlier", observed_at=NOW - timedelta(minutes=15))
    with pytest.raises(ExitEvaluationJournalAppendError, match="strictly advance"):
        journal.append_batch(ExitEvaluationJournalBatch(earlier.identity.observed_at, (earlier,)))


def test_snapshot_is_immutable_detached_and_revalidates_entry_seals() -> None:
    entry = _hold_entry()
    journal = InMemoryExitEvaluationJournal()
    journal.append_batch(ExitEvaluationJournalBatch(NOW, (entry,)))
    snapshot = journal.snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.last_observed_at = None  # type: ignore[misc]
    object.__setattr__(entry.evaluation, "action", PositionExitAction.CLOSE_FULL)
    fresh = journal.snapshot()
    fresh.validate()
    assert fresh.entries[0].evaluation.action is PositionExitAction.HOLD

    broken = deepcopy(snapshot)
    object.__setattr__(broken.entries[0].evaluation, "action", PositionExitAction.CLOSE_FULL)
    with pytest.raises(ExitEvaluationJournalValidationError, match="authority is invalid"):
        broken.validate()


def test_entry_fails_when_evaluation_and_causal_context_differ() -> None:
    first = _exit_context("first")
    second = _exit_context("second")
    evaluation = HoldOnlyExitStrategy(first.strategy_id, first.strategy_version).evaluate_exit(
        first
    )

    with pytest.raises(ExitEvaluationJournalValidationError, match="causal context"):
        ExitEvaluationJournalEntry(second, evaluation)


def test_snapshot_requires_canonical_order_unique_identity_and_exact_last_time() -> None:
    first = _hold_entry("z-source")
    second = _hold_entry("a-source")
    ordered = tuple(sorted((first, second), key=lambda item: item.identity.sort_key))
    with pytest.raises(ExitEvaluationJournalValidationError, match="canonical"):
        ExitEvaluationJournalSnapshot(tuple(reversed(ordered)), NOW)
    with pytest.raises(ExitEvaluationJournalValidationError, match="duplicated"):
        ExitEvaluationJournalSnapshot((first, first), NOW)
    with pytest.raises(ExitEvaluationJournalValidationError, match="last_observed_at"):
        ExitEvaluationJournalSnapshot(ordered, NOW + timedelta(microseconds=1))


def test_journal_entry_raw_mutation_fails_its_creation_time_seal() -> None:
    entry = _hold_entry()
    replacement = replace(
        entry.evaluation,
        reasons_for=("different but structurally valid reason",),
    )
    object.__setattr__(entry, "evaluation", replacement)

    with pytest.raises(ExitEvaluationJournalValidationError, match="integrity seal"):
        entry.validate()
