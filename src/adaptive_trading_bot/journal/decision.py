"""Immutable pre-trade decision records and deterministic append-only ledger."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from hmac import compare_digest
from json import dumps

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.risk import (
    OperationalSafetyDecision,
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecision,
    RiskDecisionStatus,
)
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_JOURNAL_SCHEMA_VERSION = "1.0.0"
_PRE_TRADE_CONTEXT_SEAL_VERSION = "1.0.0"


class DecisionJournalValidationError(ValueError):
    """A proposed journal record or batch has contradictory audit facts."""


class DecisionJournalAppendError(ValueError):
    """An append would violate ledger chronology or uniqueness."""


@dataclass(frozen=True, slots=True)
class DecisionIdentity:
    """Stable non-trade identity for one strategy decision."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    strategy_id: str
    strategy_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceIdentity):
            raise DecisionJournalValidationError("decision source_id is invalid")
        if not isinstance(self.source_id.value, str) or not self.source_id.value.strip():
            raise DecisionJournalValidationError("decision source identity must be non-empty")
        if not isinstance(self.symbol, TradingSymbol):
            raise DecisionJournalValidationError("decision symbol is invalid")
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="decision identity observed_at"),
        )
        for name, value in (
            ("strategy_id", self.strategy_id),
            ("strategy_version", self.strategy_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise DecisionJournalValidationError(f"{name} must be non-empty")

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, str]:
        """Return the canonical ledger ordering key."""

        return (
            self.observed_at,
            self.source_id.value,
            str(self.symbol),
            self.strategy_id,
            self.strategy_version,
        )


@dataclass(frozen=True, slots=True)
class DecisionJournalEntry[ReasonT, AuditT]:
    """Complete currently-authoritative structured pre-trade decision chain."""

    context: StrategyEvaluationContext
    strategy_evaluation: StrategyEvaluation[ReasonT, AuditT]
    risk_decision: RiskDecision
    safety_decision: OperationalSafetyDecision
    identity: DecisionIdentity = field(init=False)
    pre_trade_context_integrity_seal: str = field(init=False, repr=False)
    journal_schema_version: str = field(init=False, default=_JOURNAL_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        identity = _identity_for(self.strategy_evaluation)
        object.__setattr__(self, "identity", identity)
        _validate_entry_chain(self)
        object.__setattr__(
            self,
            "pre_trade_context_integrity_seal",
            _pre_trade_context_integrity_seal(identity, self.context),
        )

    def validate(self) -> None:
        """Revalidate the immutable chain and its creation-time context seal."""

        if self.journal_schema_version != _JOURNAL_SCHEMA_VERSION:
            raise DecisionJournalValidationError("journal schema version mismatch")
        seal = self.pre_trade_context_integrity_seal
        if not isinstance(seal, str) or not compare_digest(
            seal, _pre_trade_context_integrity_seal(self.identity, self.context)
        ):
            raise DecisionJournalValidationError("pre-trade context integrity seal mismatch")
        _validate_entry_chain(self)


def _validate_entry_chain(entry: DecisionJournalEntry[object, object]) -> None:
    if entry.journal_schema_version != _JOURNAL_SCHEMA_VERSION:
        raise DecisionJournalValidationError("journal schema version mismatch")
    if entry.identity != _identity_for(entry.strategy_evaluation):
        raise DecisionJournalValidationError("decision identity does not match strategy")
    _validate_context_to_strategy(entry.context, entry.strategy_evaluation)
    _validate_strategy_to_risk(entry.strategy_evaluation, entry.risk_decision)
    _validate_risk_to_safety(entry.risk_decision, entry.safety_decision)
    _validate_final_permission(
        entry.strategy_evaluation,
        entry.risk_decision,
        entry.safety_decision,
    )


def _pre_trade_context_integrity_seal(
    identity: DecisionIdentity, context: StrategyEvaluationContext
) -> str:
    payload = dumps(
        (
            "adaptive-trading-bot/pre-trade-context",
            _PRE_TRADE_CONTEXT_SEAL_VERSION,
            _canonical_integrity_value(identity),
            _canonical_integrity_value(context),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_integrity_value(value: object) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return (
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical_integrity_value(value.value),
        )
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"), value.fold)
    if isinstance(value, timedelta):
        return ("timedelta", value.days, value.seconds, value.microseconds)
    if isinstance(value, Decimal):
        decimal_tuple = value.as_tuple()
        return (
            "decimal",
            decimal_tuple.sign,
            tuple(decimal_tuple.digits),
            str(decimal_tuple.exponent),
        )
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, tuple):
        return ("tuple", tuple(_canonical_integrity_value(item) for item in value))
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            f"{type(value).__module__}.{type(value).__qualname__}",
            tuple(
                (item.name, _canonical_integrity_value(getattr(value, item.name)))
                for item in fields(value)
            ),
        )
    raise DecisionJournalValidationError(
        f"pre-trade context contains unsupported integrity type {type(value).__qualname__}"
    )


@dataclass(frozen=True, slots=True)
class DecisionJournalBatch[ReasonT, AuditT]:
    """One non-empty, same-time, canonically ordered atomic append unit."""

    observed_at: datetime
    entries: tuple[DecisionJournalEntry[ReasonT, AuditT], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="decision journal batch observed_at"),
        )
        if not isinstance(self.entries, tuple):
            raise TypeError("decision journal batch entries must be an immutable tuple")
        if not self.entries:
            raise DecisionJournalValidationError("decision journal batch must be non-empty")
        for entry in self.entries:
            if not isinstance(entry, DecisionJournalEntry):
                raise TypeError("batch entries must be DecisionJournalEntry values")
            entry.validate()
            if entry.identity.observed_at != self.observed_at:
                raise DecisionJournalValidationError(
                    "every decision entry must match the batch observed_at"
                )
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.identity.sort_key))
        identities = tuple(entry.identity for entry in ordered)
        if any(identity in identities[:index] for index, identity in enumerate(identities)):
            raise DecisionJournalValidationError("duplicate decision identity within batch")
        object.__setattr__(self, "entries", ordered)


@dataclass(frozen=True, slots=True)
class DecisionJournalAppendResult:
    """Immutable acknowledgement of a completed atomic append."""

    observed_at: datetime
    appended_identities: tuple[DecisionIdentity, ...]
    appended_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class DecisionJournalSnapshot[ReasonT, AuditT]:
    """Immutable deterministic view of all accepted decision records."""

    entries: tuple[DecisionJournalEntry[ReasonT, AuditT], ...]
    last_observed_at: datetime | None
    journal_schema_version: str = field(init=False, default=_JOURNAL_SCHEMA_VERSION)


class InMemoryDecisionJournal[ReasonT, AuditT]:
    """Append-only in-memory journal with atomic batch validation."""

    __slots__ = ("_entries", "_identities", "_last_observed_at")

    def __init__(self) -> None:
        self._entries: tuple[DecisionJournalEntry[ReasonT, AuditT], ...] = ()
        self._identities: tuple[DecisionIdentity, ...] = ()
        self._last_observed_at: datetime | None = None

    def append_batch(
        self, batch: DecisionJournalBatch[ReasonT, AuditT]
    ) -> DecisionJournalAppendResult:
        """Validate an entire batch, then commit it once without replacement."""

        if not isinstance(batch, DecisionJournalBatch):
            raise TypeError("batch must be a DecisionJournalBatch")
        for entry in batch.entries:
            entry.validate()
        identities = tuple(entry.identity for entry in batch.entries)
        if any(identity in self._identities for identity in identities):
            raise DecisionJournalAppendError("decision identity already exists in journal")
        if self._last_observed_at is not None and batch.observed_at <= self._last_observed_at:
            raise DecisionJournalAppendError(
                "batch observed_at must strictly advance; equal-time entries belong together"
            )

        self._entries = self._entries + batch.entries
        self._identities = self._identities + identities
        self._last_observed_at = batch.observed_at
        return DecisionJournalAppendResult(
            observed_at=batch.observed_at,
            appended_identities=identities,
            appended_count=len(identities),
            total_count=len(self._entries),
        )

    def snapshot(self) -> DecisionJournalSnapshot[ReasonT, AuditT]:
        """Return an immutable snapshot without exposing mutable ledger state."""

        return DecisionJournalSnapshot(self._entries, self._last_observed_at)


def _identity_for(evaluation: StrategyEvaluation[object, object]) -> DecisionIdentity:
    return DecisionIdentity(
        source_id=evaluation.source_id,
        symbol=evaluation.symbol,
        observed_at=evaluation.observed_at,
        strategy_id=evaluation.strategy_id,
        strategy_version=evaluation.strategy_version,
    )


def _validate_context_to_strategy(
    context: StrategyEvaluationContext,
    evaluation: StrategyEvaluation[object, object],
) -> None:
    if not isinstance(context, StrategyEvaluationContext):
        raise TypeError("context must be a StrategyEvaluationContext")
    if not isinstance(evaluation, StrategyEvaluation):
        raise TypeError("strategy_evaluation must be a StrategyEvaluation")
    if (
        context.source_id != evaluation.source_id
        or context.symbol != evaluation.symbol
        or context.observed_at != evaluation.observed_at
    ):
        raise DecisionJournalValidationError("context and strategy identity mismatch")
    if (
        context.candle_15m.symbol != context.symbol
        or context.candle_15m.close_time != context.observed_at
    ):
        raise DecisionJournalValidationError("context candle does not represent observed close")


def _validate_strategy_to_risk(
    evaluation: StrategyEvaluation[object, object], risk: RiskDecision
) -> None:
    if not isinstance(risk, RiskDecision):
        raise TypeError("risk_decision must be a RiskDecision")
    if (
        risk.strategy_id != evaluation.strategy_id
        or risk.strategy_version != evaluation.strategy_version
        or risk.source_id != evaluation.source_id
        or risk.symbol != evaluation.symbol
        or risk.observed_at != evaluation.observed_at
        or risk.strategy_candidate_action is not evaluation.action
        or risk.risk_snapshot_observed_at != evaluation.observed_at
    ):
        raise DecisionJournalValidationError("strategy and risk decision chain mismatch")


def _validate_risk_to_safety(risk: RiskDecision, safety: OperationalSafetyDecision) -> None:
    if not isinstance(safety, OperationalSafetyDecision):
        raise TypeError("safety_decision must be an OperationalSafetyDecision")
    if (
        safety.source_id != risk.source_id
        or safety.symbol != risk.symbol
        or safety.observed_at != risk.observed_at
        or safety.input_risk_status is not risk.status
        or safety.risk_policy_version != risk.risk_policy_version
        or safety.risk_requires_halt_review is not risk.requires_halt_review
    ):
        raise DecisionJournalValidationError("risk and safety decision chain mismatch")

    health_identity_matches = (
        safety.health_source_id == safety.source_id and safety.health_symbol == safety.symbol
    )
    health_time_matches = safety.health_observed_at == safety.observed_at
    identity_mismatch_recorded = (
        OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH in safety.reasons
    )
    time_mismatch_recorded = OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH in safety.reasons
    if health_identity_matches is identity_mismatch_recorded:
        raise DecisionJournalValidationError("safety health identity audit is inconsistent")
    if health_time_matches is time_mismatch_recorded:
        raise DecisionJournalValidationError("safety health time audit is inconsistent")
    if (
        not health_identity_matches or not health_time_matches
    ) and safety.status is not OperationalSafetyStatus.SAFE_MODE:
        raise DecisionJournalValidationError("safety health mismatch requires SAFE_MODE")


def _validate_final_permission(
    evaluation: StrategyEvaluation[object, object],
    risk: RiskDecision,
    safety: OperationalSafetyDecision,
) -> None:
    if not isinstance(evaluation.action, CandidateAction):
        raise DecisionJournalValidationError("strategy action is invalid")
    if not isinstance(risk.status, RiskDecisionStatus):
        raise DecisionJournalValidationError("risk status is invalid")
    if not isinstance(safety.status, OperationalSafetyStatus):
        raise DecisionJournalValidationError("safety status is invalid")
    cleared = safety.status is OperationalSafetyStatus.CLEARED
    if safety.entry_permitted is not cleared:
        raise DecisionJournalValidationError("entry permission must exactly match CLEARED status")
    if (
        safety.status is OperationalSafetyStatus.NO_ACTION
        and risk.status is not RiskDecisionStatus.NO_ACTION
    ):
        raise DecisionJournalValidationError("safety NO_ACTION requires risk NO_ACTION")
    if (
        safety.status is OperationalSafetyStatus.BLOCKED_BY_RISK
        and risk.status is not RiskDecisionStatus.REJECTED
    ):
        raise DecisionJournalValidationError("BLOCKED_BY_RISK requires risk REJECTED")
    if cleared and risk.status is not RiskDecisionStatus.APPROVED:
        raise DecisionJournalValidationError("CLEARED requires risk APPROVED")
    if evaluation.action is CandidateAction.NO_TRADE and (
        risk.status is RiskDecisionStatus.APPROVED or cleared
    ):
        raise DecisionJournalValidationError("strategy NO_TRADE cannot be approved or cleared")

    safety_sizing = (
        safety.approved_risk_stop_basis,
        safety.approved_quantity,
        safety.approved_notional_quote,
        safety.approved_risk_quote,
    )
    risk_sizing = (
        risk.approved_risk_stop_basis,
        risk.approved_quantity,
        risk.approved_notional_quote,
        risk.approved_risk_quote,
    )
    if cleared and safety_sizing != risk_sizing:
        raise DecisionJournalValidationError("CLEARED sizing must exactly match risk approval")
    if not cleared and any(value is not None for value in safety_sizing):
        raise DecisionJournalValidationError("non-CLEARED safety decision exposes sizing")
