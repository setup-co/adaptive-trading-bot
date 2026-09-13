"""Append-only process-local journal for every position-exit evaluation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.strategies.exit_core import (
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitEvaluationIdentity,
    PositionExitValidationError,
)

_EXIT_JOURNAL_SCHEMA_VERSION = "1.0.0"
_EXIT_JOURNAL_ENTRY_SEAL_VERSION = "1.0.0"


class ExitEvaluationJournalValidationError(ValueError):
    """An exit evaluation journal value is internally inconsistent."""


class ExitEvaluationJournalAppendError(ValueError):
    """An append violates journal uniqueness or chronology."""


@dataclass(frozen=True, slots=True)
class ExitEvaluationJournalEntry:
    """Sealed causal context and its non-executable strategy evaluation."""

    context: PositionExitEvaluationContext
    evaluation: PositionExitEvaluation
    identity: PositionExitEvaluationIdentity = field(init=False)
    journal_schema_version: str = field(init=False, default=_EXIT_JOURNAL_SCHEMA_VERSION)
    journal_entry_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", deepcopy(self.evaluation.identity))
        self._validate_fields()
        object.__setattr__(self, "journal_entry_integrity_seal", _entry_seal(self))

    def validate(self) -> None:
        seal = self.journal_entry_integrity_seal
        if not isinstance(seal, str) or not compare_digest(seal, _entry_seal(self)):
            raise ExitEvaluationJournalValidationError("exit journal entry integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.journal_schema_version != _EXIT_JOURNAL_SCHEMA_VERSION:
            raise ExitEvaluationJournalValidationError("exit journal schema version mismatch")
        if not isinstance(self.context, PositionExitEvaluationContext):
            raise TypeError("exit journal context is invalid")
        if not isinstance(self.evaluation, PositionExitEvaluation):
            raise TypeError("exit journal evaluation is invalid")
        try:
            self.context.validate()
            self.evaluation.validate()
        except (PositionExitValidationError, TypeError, ValueError) as error:
            raise ExitEvaluationJournalValidationError(
                "exit journal authority is invalid"
            ) from error
        if self.identity != self.evaluation.identity:
            raise ExitEvaluationJournalValidationError("exit journal identity mismatch")
        if (
            self.evaluation.identity.position_identity != self.context.position_identity
            or self.evaluation.originating_decision_identity
            != self.context.position_identity.originating_decision_identity
            or self.evaluation.strategy_id != self.context.strategy_id
            or self.evaluation.strategy_version != self.context.strategy_version
            or self.evaluation.source_id != self.context.source_id
            or self.evaluation.symbol != self.context.symbol
            or self.evaluation.observed_at != self.context.observed_at
            or self.evaluation.position_side is not self.context.position_side
            or self.evaluation.evaluated_context_integrity_seal
            != self.context.context_integrity_seal
        ):
            raise ExitEvaluationJournalValidationError(
                "exit evaluation does not match its causal context"
            )


@dataclass(frozen=True, slots=True)
class ExitEvaluationJournalBatch:
    """One non-empty same-time atomic append unit in canonical order."""

    observed_at: datetime
    entries: tuple[ExitEvaluationJournalEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="exit journal batch observed_at"),
        )
        if not isinstance(self.entries, tuple):
            raise TypeError("exit journal entries must be an immutable tuple")
        if not self.entries:
            raise ExitEvaluationJournalValidationError("exit journal batch must be non-empty")
        for entry in self.entries:
            if not isinstance(entry, ExitEvaluationJournalEntry):
                raise TypeError("exit journal batch contains an invalid entry")
            entry.validate()
            if entry.identity.observed_at != self.observed_at:
                raise ExitEvaluationJournalValidationError("exit journal batch timestamps differ")
        ordered = tuple(sorted(self.entries, key=lambda item: item.identity.sort_key))
        identities = tuple(entry.identity for entry in ordered)
        if len(set(identities)) != len(identities):
            raise ExitEvaluationJournalValidationError(
                "duplicate exit evaluation identity within batch"
            )
        object.__setattr__(self, "entries", ordered)


@dataclass(frozen=True, slots=True)
class ExitEvaluationJournalAppendResult:
    observed_at: datetime
    appended_identities: tuple[PositionExitEvaluationIdentity, ...]
    appended_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class ExitEvaluationJournalSnapshot:
    entries: tuple[ExitEvaluationJournalEntry, ...]
    last_observed_at: datetime | None
    journal_schema_version: str = field(init=False, default=_EXIT_JOURNAL_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.journal_schema_version != _EXIT_JOURNAL_SCHEMA_VERSION:
            raise ExitEvaluationJournalValidationError("exit snapshot schema version mismatch")
        if not isinstance(self.entries, tuple):
            raise TypeError("exit snapshot entries must be an immutable tuple")
        for entry in self.entries:
            if not isinstance(entry, ExitEvaluationJournalEntry):
                raise TypeError("exit snapshot contains an invalid entry")
            entry.validate()
        ordered = tuple(sorted(self.entries, key=lambda item: item.identity.sort_key))
        if self.entries != ordered:
            raise ExitEvaluationJournalValidationError("exit snapshot order is not canonical")
        identities = tuple(entry.identity for entry in self.entries)
        if len(set(identities)) != len(identities):
            raise ExitEvaluationJournalValidationError("exit snapshot identities are duplicated")
        expected_last = max((entry.identity.observed_at for entry in self.entries), default=None)
        if self.last_observed_at != expected_last:
            raise ExitEvaluationJournalValidationError(
                "exit snapshot last_observed_at is inconsistent"
            )


class InMemoryExitEvaluationJournal:
    """Deterministic append-only ledger for HOLD and future exit evaluations."""

    __slots__ = ("_entries", "_identities", "_last_observed_at")

    def __init__(self) -> None:
        self._entries: tuple[ExitEvaluationJournalEntry, ...] = ()
        self._identities: frozenset[PositionExitEvaluationIdentity] = frozenset()
        self._last_observed_at: datetime | None = None

    def append_batch(self, batch: ExitEvaluationJournalBatch) -> ExitEvaluationJournalAppendResult:
        if not isinstance(batch, ExitEvaluationJournalBatch):
            raise TypeError("batch must be an ExitEvaluationJournalBatch")
        owned = deepcopy(batch.entries)
        for entry in owned:
            entry.validate()
        identities = tuple(entry.identity for entry in owned)
        if any(identity in self._identities for identity in identities):
            raise ExitEvaluationJournalAppendError("exit evaluation identity already exists")
        if self._last_observed_at is not None and batch.observed_at <= self._last_observed_at:
            raise ExitEvaluationJournalAppendError("exit evaluation batches must strictly advance")
        self._entries = (*self._entries, *owned)
        self._identities = self._identities.union(identities)
        self._last_observed_at = batch.observed_at
        return ExitEvaluationJournalAppendResult(
            batch.observed_at,
            deepcopy(identities),
            len(identities),
            len(self._entries),
        )

    def snapshot(self) -> ExitEvaluationJournalSnapshot:
        return ExitEvaluationJournalSnapshot(deepcopy(self._entries), self._last_observed_at)


def _entry_seal(entry: ExitEvaluationJournalEntry) -> str:
    payload = "|".join(
        (
            "adaptive-trading-bot/exit-evaluation-journal-entry",
            _EXIT_JOURNAL_ENTRY_SEAL_VERSION,
            entry.context.context_integrity_seal,
            entry.evaluation.evaluation_integrity_seal,
            entry.identity.observed_at.isoformat(timespec="microseconds"),
            entry.identity.exit_policy_version,
            entry.identity.position_identity.accounting_entry_fill_id,
        )
    ).encode("utf-8")
    return sha256(payload).hexdigest()
