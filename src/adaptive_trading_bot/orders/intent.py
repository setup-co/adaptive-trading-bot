"""Authorization-safe entry intent contracts with deterministic in-memory dedupe.

An entry intent is not an exchange order. This module deliberately has no execution,
network, persistence, fill, position, or exchange-constraint behavior. In-memory
deduplication is process-local and is not crash-safe. Until a Strategy Router exists,
independently cleared strategy decisions at the same instant remain distinct intents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.journal.decision import DecisionJournalValidationError
from adaptive_trading_bot.risk import (
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskReasonCode,
)
from adaptive_trading_bot.strategies.core import CandidateAction, SourceIdentity

_INTENT_SCHEMA_VERSION = "1.0.0"


class EntryIntentPurpose(StrEnum):
    """Environment-neutral purpose of an authorized intent."""

    ENTRY = "ENTRY"


class EntrySide(StrEnum):
    """Environment-neutral entry direction for later adapters."""

    BUY = "BUY"
    SELL = "SELL"


class OrderIntentAuthorizationStatus(StrEnum):
    """Outcome of validating a journal entry for intent authorization."""

    NO_INTENT = "NO_INTENT"
    AUTHORIZED = "AUTHORIZED"
    REJECTED_INCONSISTENT = "REJECTED_INCONSISTENT"


class OrderIntentAuthorizationReasonCode(StrEnum):
    """Canonical authorization evidence in deterministic evaluation order."""

    JOURNAL_CHAIN_VALID = "JOURNAL_CHAIN_VALID"
    SAFETY_CLEARED = "SAFETY_CLEARED"
    RISK_APPROVAL_PRESENT = "RISK_APPROVAL_PRESENT"
    SIZING_EXACTLY_PRESERVED = "SIZING_EXACTLY_PRESERVED"
    ENTRY_INTENT_AUTHORIZED = "ENTRY_INTENT_AUTHORIZED"

    STRATEGY_NO_TRADE = "STRATEGY_NO_TRADE"
    RISK_NO_ACTION = "RISK_NO_ACTION"
    RISK_REJECTED = "RISK_REJECTED"
    SAFETY_NO_ACTION = "SAFETY_NO_ACTION"
    SAFETY_BLOCKED_BY_RISK = "SAFETY_BLOCKED_BY_RISK"
    SAFETY_SAFE_MODE = "SAFETY_SAFE_MODE"

    JOURNAL_CHAIN_INVALID = "JOURNAL_CHAIN_INVALID"
    DECISION_IDENTITY_MISMATCH = "DECISION_IDENTITY_MISMATCH"
    PERMISSION_INCONSISTENT = "PERMISSION_INCONSISTENT"
    SIZING_MISMATCH = "SIZING_MISMATCH"
    INVALID_APPROVED_DECIMAL = "INVALID_APPROVED_DECIMAL"
    DUPLICATE_DECISION_INTENT = "DUPLICATE_DECISION_INTENT"


@dataclass(frozen=True, slots=True)
class EntryOrderIntentIdentity:
    """Deterministic identity rooted in the complete journal decision identity."""

    decision_identity: DecisionIdentity
    purpose: EntryIntentPurpose = field(init=False, default=EntryIntentPurpose.ENTRY)
    intent_schema_version: str = field(init=False, default=_INTENT_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True)
class EntryOrderIntent:
    """Exact authorization carried forward for a later environment adapter."""

    identity: EntryOrderIntentIdentity
    decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    authorized_at: datetime
    strategy_id: str
    strategy_version: str
    risk_policy_version: str
    safety_policy_version: str
    side: EntrySide
    quantity: Decimal
    approved_notional_quote: Decimal
    approved_risk_quote: Decimal
    stop_basis: Decimal
    reference_price: Decimal
    quote_currency: str
    authorized_for_execution: bool = field(init=False, default=True)


@dataclass(frozen=True, slots=True)
class OrderIntentAuthorizationResult:
    """Typed authorization outcome retaining source-stage audit status."""

    status: OrderIntentAuthorizationStatus
    decision_identity: DecisionIdentity
    strategy_action: CandidateAction
    risk_status: RiskDecisionStatus
    safety_status: OperationalSafetyStatus
    reasons: tuple[OrderIntentAuthorizationReasonCode, ...]
    intent: EntryOrderIntent | None


@dataclass(frozen=True, slots=True)
class OrderIntentLedgerSnapshot:
    """Immutable, canonically ordered view of authorized entry intents."""

    intents: tuple[EntryOrderIntent, ...]
    intent_schema_version: str = field(init=False, default=_INTENT_SCHEMA_VERSION)


class EntryOrderIntentAuthorizer:
    """Validate existing Strategy -> Risk -> Safety authority without recomputing it."""

    __slots__ = ()

    def authorize[ReasonT, AuditT](
        self, journal_entry: DecisionJournalEntry[ReasonT, AuditT]
    ) -> OrderIntentAuthorizationResult:
        """Return an intent only for a fully consistent, operationally cleared chain."""

        if not isinstance(journal_entry, DecisionJournalEntry):
            raise TypeError("journal_entry must be a DecisionJournalEntry")

        invalid_reasons: list[OrderIntentAuthorizationReasonCode] = []
        try:
            invalid_reasons = _prevalidation_reasons(journal_entry)
            journal_entry.validate()
        except (DecisionJournalValidationError, TypeError, ValueError):
            if not invalid_reasons:
                invalid_reasons.append(OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_INVALID)
            return _result(
                journal_entry,
                OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT,
                tuple(invalid_reasons),
            )
        if invalid_reasons:
            return _result(
                journal_entry,
                OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT,
                tuple(invalid_reasons),
            )

        evaluation = journal_entry.strategy_evaluation
        risk = journal_entry.risk_decision
        safety = journal_entry.safety_decision
        no_intent_reasons = _no_intent_reasons(evaluation.action, risk.status, safety.status)
        if no_intent_reasons:
            return _result(
                journal_entry,
                OrderIntentAuthorizationStatus.NO_INTENT,
                (
                    OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
                    *no_intent_reasons,
                ),
            )

        quantity = _approved_decimal(safety.approved_quantity)
        notional = _approved_decimal(safety.approved_notional_quote)
        approved_risk = _approved_decimal(safety.approved_risk_quote)
        stop_basis = _approved_decimal(safety.approved_risk_stop_basis)
        reference_price = _approved_decimal(risk.reference_price)
        side = (
            EntrySide.BUY if evaluation.action is CandidateAction.LONG_CANDIDATE else EntrySide.SELL
        )
        identity = EntryOrderIntentIdentity(journal_entry.identity)
        intent = EntryOrderIntent(
            identity=identity,
            decision_identity=journal_entry.identity,
            source_id=journal_entry.identity.source_id,
            symbol=journal_entry.identity.symbol,
            observed_at=journal_entry.identity.observed_at,
            authorized_at=journal_entry.identity.observed_at,
            strategy_id=journal_entry.identity.strategy_id,
            strategy_version=journal_entry.identity.strategy_version,
            risk_policy_version=risk.risk_policy_version,
            safety_policy_version=safety.safety_policy_version,
            side=side,
            quantity=quantity,
            approved_notional_quote=notional,
            approved_risk_quote=approved_risk,
            stop_basis=stop_basis,
            reference_price=reference_price,
            quote_currency=risk.quote_currency,
        )
        return _result(
            journal_entry,
            OrderIntentAuthorizationStatus.AUTHORIZED,
            (
                OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
                OrderIntentAuthorizationReasonCode.SAFETY_CLEARED,
                OrderIntentAuthorizationReasonCode.RISK_APPROVAL_PRESENT,
                OrderIntentAuthorizationReasonCode.SIZING_EXACTLY_PRESERVED,
                OrderIntentAuthorizationReasonCode.ENTRY_INTENT_AUTHORIZED,
            ),
            intent,
        )


class InMemoryOrderIntentLedger:
    """Process-local append-only dedupe ledger; not durable or crash-safe."""

    __slots__ = ("_decision_identities", "_intent_identities", "_intents")

    def __init__(self) -> None:
        self._intents: tuple[EntryOrderIntent, ...] = ()
        self._decision_identities: frozenset[DecisionIdentity] = frozenset()
        self._intent_identities: frozenset[EntryOrderIntentIdentity] = frozenset()

    def authorize_and_append[ReasonT, AuditT](
        self, journal_entry: DecisionJournalEntry[ReasonT, AuditT]
    ) -> OrderIntentAuthorizationResult:
        """Authorize once and atomically append only a new valid intent."""

        result = EntryOrderIntentAuthorizer().authorize(journal_entry)
        intent = result.intent
        if result.status is not OrderIntentAuthorizationStatus.AUTHORIZED or intent is None:
            return result
        if (
            intent.decision_identity in self._decision_identities
            or intent.identity in self._intent_identities
        ):
            return _result(
                journal_entry,
                OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT,
                (OrderIntentAuthorizationReasonCode.DUPLICATE_DECISION_INTENT,),
            )

        self._intents = tuple(
            sorted((*self._intents, intent), key=lambda item: item.decision_identity.sort_key)
        )
        self._decision_identities = self._decision_identities | {intent.decision_identity}
        self._intent_identities = self._intent_identities | {intent.identity}
        return result

    def snapshot(self) -> OrderIntentLedgerSnapshot:
        """Return an immutable snapshot without exposing mutable ledger state."""

        return OrderIntentLedgerSnapshot(self._intents)


def _prevalidation_reasons(
    entry: DecisionJournalEntry[object, object],
) -> list[OrderIntentAuthorizationReasonCode]:
    evaluation = entry.strategy_evaluation
    risk = entry.risk_decision
    safety = entry.safety_decision
    reasons: list[OrderIntentAuthorizationReasonCode] = []
    expected_identity = DecisionIdentity(
        evaluation.source_id,
        evaluation.symbol,
        evaluation.observed_at,
        evaluation.strategy_id,
        evaluation.strategy_version,
    )
    if entry.identity != expected_identity:
        reasons.append(OrderIntentAuthorizationReasonCode.DECISION_IDENTITY_MISMATCH)
    cleared = safety.status is OperationalSafetyStatus.CLEARED
    if safety.entry_permitted is not cleared or (
        cleared
        and (
            risk.status is not RiskDecisionStatus.APPROVED
            or evaluation.action
            not in {CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE}
            or risk.requires_halt_review
            or safety.risk_requires_halt_review
            or RiskReasonCode.RISK_SIZE_APPROVED not in risk.reasons
            or any(reason in _RISK_AUTHORIZATION_BLOCKERS for reason in risk.reasons)
            or OperationalSafetyReasonCode.ENTRY_CLEARED not in safety.reasons
            or OperationalSafetyReasonCode.RISK_APPROVAL_VALID not in safety.reasons
            or any(reason in _AUTHORIZATION_BLOCKERS for reason in safety.reasons)
        )
    ):
        reasons.append(OrderIntentAuthorizationReasonCode.PERMISSION_INCONSISTENT)
    safety_sizing = (
        safety.approved_quantity,
        safety.approved_notional_quote,
        safety.approved_risk_quote,
        safety.approved_risk_stop_basis,
    )
    risk_sizing = (
        risk.approved_quantity,
        risk.approved_notional_quote,
        risk.approved_risk_quote,
        risk.approved_risk_stop_basis,
    )
    if cleared and safety_sizing != risk_sizing:
        reasons.append(OrderIntentAuthorizationReasonCode.SIZING_MISMATCH)
    if cleared and not all(
        _is_positive_finite_decimal(value) for value in (*safety_sizing, risk.reference_price)
    ):
        reasons.append(OrderIntentAuthorizationReasonCode.INVALID_APPROVED_DECIMAL)
    if cleared and (
        evaluation.reference_price != risk.reference_price
        or evaluation.invalidation_reference_price != risk.approved_risk_stop_basis
    ):
        reasons.append(OrderIntentAuthorizationReasonCode.SIZING_MISMATCH)
    if (
        not isinstance(risk.risk_policy_version, str)
        or not risk.risk_policy_version.strip()
        or not isinstance(safety.safety_policy_version, str)
        or not safety.safety_policy_version.strip()
    ):
        reasons.append(OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_INVALID)
    if cleared and (
        risk.quote_currency != entry.identity.symbol.quote or risk.quote_currency != "USDT"
    ):
        reasons.append(OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_INVALID)
    return reasons


_AUTHORIZATION_BLOCKERS = frozenset(
    reason
    for reason in OperationalSafetyReasonCode
    if reason
    not in {
        OperationalSafetyReasonCode.RISK_APPROVAL_VALID,
        OperationalSafetyReasonCode.MARKET_DATA_FRESH,
        OperationalSafetyReasonCode.FEED_INTEGRITY_OK,
        OperationalSafetyReasonCode.EXCHANGE_API_HEALTHY,
        OperationalSafetyReasonCode.SPREAD_WITHIN_LIMIT,
        OperationalSafetyReasonCode.TIME_SYNC_WITHIN_LIMIT,
        OperationalSafetyReasonCode.VOLATILITY_CLASSIFIED_SAFE,
        OperationalSafetyReasonCode.NO_PRICE_ANOMALY,
        OperationalSafetyReasonCode.ORDER_REJECTION_COUNT_WITHIN_LIMIT,
        OperationalSafetyReasonCode.ENTRY_CLEARED,
    }
)

_RISK_AUTHORIZATION_BLOCKERS = frozenset(
    reason
    for reason in RiskReasonCode
    if reason
    not in {
        RiskReasonCode.STRATEGY_CANDIDATE_ACCEPTED,
        RiskReasonCode.CANDIDATE_GEOMETRY_VALID,
        RiskReasonCode.DAILY_LOSS_WITHIN_LIMIT,
        RiskReasonCode.DRAWDOWN_WITHIN_LIMIT,
        RiskReasonCode.SYMBOL_FLAT,
        RiskReasonCode.SIMULTANEOUS_RISK_AVAILABLE,
        RiskReasonCode.GROSS_EXPOSURE_AVAILABLE,
        RiskReasonCode.RISK_SIZE_APPROVED,
    }
)


def _no_intent_reasons(
    action: CandidateAction,
    risk_status: RiskDecisionStatus,
    safety_status: OperationalSafetyStatus,
) -> tuple[OrderIntentAuthorizationReasonCode, ...]:
    reasons: list[OrderIntentAuthorizationReasonCode] = []
    if action is CandidateAction.NO_TRADE:
        reasons.append(OrderIntentAuthorizationReasonCode.STRATEGY_NO_TRADE)
    if risk_status is RiskDecisionStatus.NO_ACTION:
        reasons.append(OrderIntentAuthorizationReasonCode.RISK_NO_ACTION)
    elif risk_status is RiskDecisionStatus.REJECTED:
        reasons.append(OrderIntentAuthorizationReasonCode.RISK_REJECTED)
    safety_reason = {
        OperationalSafetyStatus.NO_ACTION: OrderIntentAuthorizationReasonCode.SAFETY_NO_ACTION,
        OperationalSafetyStatus.BLOCKED_BY_RISK: (
            OrderIntentAuthorizationReasonCode.SAFETY_BLOCKED_BY_RISK
        ),
        OperationalSafetyStatus.SAFE_MODE: OrderIntentAuthorizationReasonCode.SAFETY_SAFE_MODE,
    }.get(safety_status)
    if safety_reason is not None:
        reasons.append(safety_reason)
    return tuple(reasons)


def _approved_decimal(value: Decimal | None) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError("approved Decimal was not validated")
    return value


def _is_positive_finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _result(
    entry: DecisionJournalEntry[object, object],
    status: OrderIntentAuthorizationStatus,
    reasons: tuple[OrderIntentAuthorizationReasonCode, ...],
    intent: EntryOrderIntent | None = None,
) -> OrderIntentAuthorizationResult:
    return OrderIntentAuthorizationResult(
        status=status,
        decision_identity=entry.identity,
        strategy_action=entry.strategy_evaluation.action,
        risk_status=entry.risk_decision.status,
        safety_status=entry.safety_decision.status,
        reasons=reasons,
        intent=intent,
    )
