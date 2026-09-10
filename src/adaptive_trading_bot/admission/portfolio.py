"""Fail-closed arbitration and atomic portfolio-cap admission.

This gate validates already-created authority. It does not rank strategies, resize
intents, reserve durable state, or perform execution.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Self

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryOrderIntent,
    EntryOrderIntentAuthorizer,
    EntryOrderIntentIdentity,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.risk import OperationalSafetyStatus, RiskDecisionStatus
from adaptive_trading_bot.strategies.core import SourceIdentity

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)
_ADMISSION_SCHEMA_VERSION = "1.0.0"
_PERMIT_TOKEN = object()


class PortfolioAdmissionStatus(StrEnum):
    """Outcome for a batch or one candidate within it."""

    ADMITTED = "ADMITTED"
    NO_ADMISSION = "NO_ADMISSION"
    REJECTED_INCONSISTENT = "REJECTED_INCONSISTENT"


class PortfolioAdmissionReasonCode(StrEnum):
    """Canonical admission evidence and fail-closed reasons."""

    AUTHORITY_REVALIDATED = "AUTHORITY_REVALIDATED"
    UNIQUE_SYMBOL_CANDIDATE = "UNIQUE_SYMBOL_CANDIDATE"
    STRATEGY_CONFLICT_NO_SELECTION = "STRATEGY_CONFLICT_NO_SELECTION"
    PORTFOLIO_BASELINE_MISMATCH = "PORTFOLIO_BASELINE_MISMATCH"
    DUPLICATE_DECISION = "DUPLICATE_DECISION"
    DUPLICATE_PENDING_RESERVATION = "DUPLICATE_PENDING_RESERVATION"
    INVALID_PENDING_RESERVATION = "INVALID_PENDING_RESERVATION"
    ALREADY_RESERVED_INTENT = "ALREADY_RESERVED_INTENT"
    PENDING_SNAPSHOT_TIME_MISMATCH = "PENDING_SNAPSHOT_TIME_MISMATCH"
    AUTHORITY_REVALIDATION_FAILED = "AUTHORITY_REVALIDATION_FAILED"
    COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP = "COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP"
    COMBINED_GROSS_EXPOSURE_WITHIN_CAP = "COMBINED_GROSS_EXPOSURE_WITHIN_CAP"
    COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED = "COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED"
    COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED = "COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED"
    ADMITTED_FOR_RESERVATION = "ADMITTED_FOR_RESERVATION"


@dataclass(frozen=True, slots=True)
class PortfolioAdmissionCandidate[ReasonT, AuditT]:
    """An immutable journal and supplied-intent pair to reauthorize."""

    journal_entry: DecisionJournalEntry[ReasonT, AuditT]
    intent: EntryOrderIntent

    def __post_init__(self) -> None:
        if not isinstance(self.journal_entry, DecisionJournalEntry):
            raise TypeError("journal_entry must be a DecisionJournalEntry")
        if type(self.intent) is not EntryOrderIntent:
            raise TypeError("intent must be an authoritative EntryOrderIntent")


@dataclass(frozen=True, slots=True)
class PendingPortfolioReservation:
    """Explicit capacity consumed by one earlier, still-pending intent."""

    intent_identity: EntryOrderIntentIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    reserved_risk_quote: Decimal
    reserved_notional_quote: Decimal

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate explicit reservation facts before every admission."""

        if not isinstance(self.intent_identity, EntryOrderIntentIdentity):
            raise TypeError("reservation intent_identity is invalid")
        if not isinstance(self.source_id, SourceIdentity):
            raise TypeError("reservation source_id must implement SourceIdentity")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("reservation symbol must be a TradingSymbol")
        if self.symbol.quote != "USDT":
            raise ValueError("V1 reservations must quote in USDT")
        decision = self.intent_identity.decision_identity
        if decision.source_id != self.source_id or decision.symbol != self.symbol:
            raise ValueError("reservation identity does not match source and symbol")
        for name, value in (
            ("reserved_risk_quote", self.reserved_risk_quote),
            ("reserved_notional_quote", self.reserved_notional_quote),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class PendingReservationSnapshot:
    """Immutable explicit view of pending reservations at a batch instant."""

    observed_at: datetime
    quote_currency: str
    reservations: tuple[PendingPortfolioReservation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="pending reservation snapshot observed_at"),
        )
        self.validate()

    def validate(self) -> None:
        """Revalidate the snapshot and each item without trusting frozen storage."""

        require_utc(self.observed_at, field="pending reservation snapshot observed_at")
        if self.quote_currency != "USDT":
            raise ValueError("V1 pending reservation quote_currency must be USDT")
        if not isinstance(self.reservations, tuple):
            raise TypeError("reservations must be an immutable tuple")
        if not all(isinstance(item, PendingPortfolioReservation) for item in self.reservations):
            raise TypeError("reservations must contain PendingPortfolioReservation values")
        for item in self.reservations:
            item.validate()

    @property
    def reserved_risk_quote(self) -> Decimal:
        """Derive risk capacity consumption from reservation items."""

        with localcontext(_DECIMAL_CONTEXT):
            return sum((item.reserved_risk_quote for item in self.reservations), _ZERO)

    @property
    def reserved_notional_quote(self) -> Decimal:
        """Derive exposure capacity consumption from reservation items."""

        with localcontext(_DECIMAL_CONTEXT):
            return sum((item.reserved_notional_quote for item in self.reservations), _ZERO)


@dataclass(frozen=True, slots=True)
class PortfolioAdmissionPermitIdentity:
    """Deterministic identity rooted in the intent and batch instant."""

    intent_identity: EntryOrderIntentIdentity
    batch_observed_at: datetime
    admission_schema_version: str = field(init=False, default=_ADMISSION_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "batch_observed_at",
            require_utc(self.batch_observed_at, field="admission permit batch_observed_at"),
        )


@dataclass(frozen=True, slots=True, init=False)
class PortfolioAdmissionPermit:
    """Exact non-executable authority copy mintable only by the admission gate."""

    identity: PortfolioAdmissionPermitIdentity
    intent_identity: EntryOrderIntentIdentity
    decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    authorized_at: datetime
    strategy_id: str
    strategy_version: str
    side: EntrySide
    quantity: Decimal
    approved_risk_quote: Decimal
    approved_notional_quote: Decimal
    stop_basis: Decimal
    reference_price: Decimal
    risk_policy_version: str
    safety_policy_version: str
    quote_currency: str
    admitted_for_reservation: bool = field(init=False, default=True)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        token = kwargs.pop("_permit_token", None)
        if args or kwargs or token is not _PERMIT_TOKEN:
            raise TypeError("PortfolioAdmissionPermit can only be created by its gate")
        return object.__new__(cls)

    def __init__(self, *, _permit_token: object) -> None:
        del _permit_token


@dataclass(frozen=True, slots=True)
class PortfolioAdmissionCandidateResult:
    """Deterministic result for one canonical candidate."""

    decision_identity: DecisionIdentity
    status: PortfolioAdmissionStatus
    reasons: tuple[PortfolioAdmissionReasonCode, ...]
    permit: PortfolioAdmissionPermit | None


@dataclass(frozen=True, slots=True)
class PortfolioAdmissionBatchResult:
    """Pure atomic result; admitted permits have not yet been persisted or consumed."""

    observed_at: datetime
    status: PortfolioAdmissionStatus
    reasons: tuple[PortfolioAdmissionReasonCode, ...]
    candidate_results: tuple[PortfolioAdmissionCandidateResult, ...]
    permits: tuple[PortfolioAdmissionPermit, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedCandidate:
    entry: DecisionJournalEntry[object, object]
    intent: EntryOrderIntent


@dataclass(frozen=True, slots=True)
class _PortfolioBaseline:
    risk_snapshot_observed_at: datetime
    quote_currency: str
    account_equity_quote: Decimal
    day_start_equity_quote: Decimal
    peak_equity_quote: Decimal
    daily_loss_quote: Decimal
    daily_loss_fraction: Decimal
    max_daily_loss_fraction: Decimal
    drawdown_quote: Decimal
    drawdown_fraction: Decimal
    max_drawdown_fraction: Decimal
    current_open_risk_quote: Decimal
    simultaneous_risk_cap_quote: Decimal
    remaining_simultaneous_risk_quote: Decimal
    current_gross_exposure_quote: Decimal
    gross_exposure_cap_quote: Decimal
    remaining_gross_exposure_quote: Decimal
    per_trade_risk_budget_quote: Decimal
    risk_policy_version: str
    requires_halt_review: bool


class PortfolioAdmissionGate:
    """Revalidate authority, arbitrate conflicts, and atomically enforce caps."""

    __slots__ = ()

    def evaluate_batch[ReasonT, AuditT](
        self,
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
        pending_reservations: PendingReservationSnapshot,
    ) -> PortfolioAdmissionBatchResult:
        """Evaluate a non-empty same-instant batch without mutating any state."""

        if not isinstance(candidates, tuple):
            raise TypeError("candidates must be an immutable tuple")
        if not candidates:
            raise ValueError("admission candidates must be non-empty")
        if not isinstance(pending_reservations, PendingReservationSnapshot):
            raise TypeError("pending_reservations must be a PendingReservationSnapshot")
        if not all(isinstance(candidate, PortfolioAdmissionCandidate) for candidate in candidates):
            raise TypeError("candidates must contain PortfolioAdmissionCandidate values")
        try:
            pending_reservations.validate()
        except (AttributeError, TypeError, ValueError):
            return self._integrity_result(
                candidates,
                pending_reservations.observed_at,
                PortfolioAdmissionReasonCode.INVALID_PENDING_RESERVATION,
            )

        validated = self._reauthorize(candidates)
        if validated is None:
            return self._integrity_result(
                candidates,
                pending_reservations.observed_at,
                PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATION_FAILED,
            )
        ordered = tuple(sorted(validated, key=lambda item: item.intent.decision_identity.sort_key))
        identities = tuple(item.intent.decision_identity for item in ordered)
        if _has_duplicate(identities):
            return self._integrity_result(
                candidates,
                pending_reservations.observed_at,
                PortfolioAdmissionReasonCode.DUPLICATE_DECISION,
            )

        reservation_ids = tuple(item.intent_identity for item in pending_reservations.reservations)
        if _has_duplicate(reservation_ids):
            return self._integrity_result(
                candidates,
                pending_reservations.observed_at,
                PortfolioAdmissionReasonCode.DUPLICATE_PENDING_RESERVATION,
            )
        if any(
            identity in reservation_ids for identity in (item.intent.identity for item in ordered)
        ):
            return self._integrity_result(
                candidates,
                pending_reservations.observed_at,
                PortfolioAdmissionReasonCode.ALREADY_RESERVED_INTENT,
            )

        observed_at = ordered[0].intent.observed_at
        if pending_reservations.observed_at != observed_at:
            return self._integrity_result(
                candidates, observed_at, PortfolioAdmissionReasonCode.PENDING_SNAPSHOT_TIME_MISMATCH
            )
        baselines = tuple(_baseline(item.entry) for item in ordered)
        if any(baseline != baselines[0] for baseline in baselines[1:]):
            return self._integrity_result(
                candidates, observed_at, PortfolioAdmissionReasonCode.PORTFOLIO_BASELINE_MISMATCH
            )

        groups: dict[tuple[str, TradingSymbol, datetime], list[_ValidatedCandidate]] = {}
        for item in ordered:
            key = (item.intent.source_id.value, item.intent.symbol, item.intent.observed_at)
            groups.setdefault(key, []).append(item)
        unique = tuple(item for item in ordered if len(groups[_arbitration_key(item)]) == 1)
        conflicts = frozenset(
            item.intent.decision_identity
            for item in ordered
            if len(groups[_arbitration_key(item)]) > 1
        )

        cap_reasons = self._cap_reasons(baselines[0], pending_reservations, unique)
        caps_pass = all(
            reason
            not in {
                PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED,
                PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED,
            }
            for reason in cap_reasons
        )
        results: list[PortfolioAdmissionCandidateResult] = []
        permits: list[PortfolioAdmissionPermit] = []
        for item in ordered:
            identity = _copy_decision_identity(item.intent.decision_identity)
            if identity in conflicts:
                results.append(
                    PortfolioAdmissionCandidateResult(
                        identity,
                        PortfolioAdmissionStatus.NO_ADMISSION,
                        (
                            PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATED,
                            PortfolioAdmissionReasonCode.STRATEGY_CONFLICT_NO_SELECTION,
                        ),
                        None,
                    )
                )
            elif not caps_pass:
                results.append(
                    PortfolioAdmissionCandidateResult(
                        identity,
                        PortfolioAdmissionStatus.NO_ADMISSION,
                        (
                            PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATED,
                            PortfolioAdmissionReasonCode.UNIQUE_SYMBOL_CANDIDATE,
                            *cap_reasons,
                        ),
                        None,
                    )
                )
            else:
                permit = _create_permit(item.intent, observed_at)
                permits.append(permit)
                results.append(
                    PortfolioAdmissionCandidateResult(
                        identity,
                        PortfolioAdmissionStatus.ADMITTED,
                        (
                            PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATED,
                            PortfolioAdmissionReasonCode.UNIQUE_SYMBOL_CANDIDATE,
                            *cap_reasons,
                            PortfolioAdmissionReasonCode.ADMITTED_FOR_RESERVATION,
                        ),
                        permit,
                    )
                )
        batch_reasons = _canonical_reasons(
            reason for result in results for reason in result.reasons
        )
        return PortfolioAdmissionBatchResult(
            observed_at=observed_at,
            status=(
                PortfolioAdmissionStatus.ADMITTED
                if permits
                else PortfolioAdmissionStatus.NO_ADMISSION
            ),
            reasons=batch_reasons,
            candidate_results=tuple(results),
            permits=tuple(permits),
        )

    @staticmethod
    def _reauthorize[ReasonT, AuditT](
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
    ) -> tuple[_ValidatedCandidate, ...] | None:
        validated: list[_ValidatedCandidate] = []
        for candidate in candidates:
            try:
                candidate.journal_entry.validate()
                result = EntryOrderIntentAuthorizer().authorize(candidate.journal_entry)
            except (AttributeError, TypeError, ValueError):
                return None
            intent = result.intent
            if (
                result.status is not OrderIntentAuthorizationStatus.AUTHORIZED
                or intent is None
                or intent != candidate.intent
                or intent.authorized_for_execution is not True
                or candidate.journal_entry.risk_decision.status is not RiskDecisionStatus.APPROVED
                or candidate.journal_entry.safety_decision.status
                is not OperationalSafetyStatus.CLEARED
                or candidate.journal_entry.safety_decision.entry_permitted is not True
                or not _intent_decimals_valid(intent)
            ):
                return None
            validated.append(_ValidatedCandidate(candidate.journal_entry, intent))
        return tuple(validated)

    @staticmethod
    def _cap_reasons(
        baseline: _PortfolioBaseline,
        pending: PendingReservationSnapshot,
        candidates: tuple[_ValidatedCandidate, ...],
    ) -> tuple[PortfolioAdmissionReasonCode, ...]:
        with localcontext(_DECIMAL_CONTEXT):
            risk = (
                baseline.current_open_risk_quote
                + pending.reserved_risk_quote
                + sum((item.intent.approved_risk_quote for item in candidates), _ZERO)
            )
            exposure = (
                baseline.current_gross_exposure_quote
                + pending.reserved_notional_quote
                + sum((item.intent.approved_notional_quote for item in candidates), _ZERO)
            )
        return (
            PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED
            if risk > baseline.simultaneous_risk_cap_quote
            else PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP,
            PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED
            if exposure > baseline.gross_exposure_cap_quote
            else PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_WITHIN_CAP,
        )

    @staticmethod
    def _integrity_result[ReasonT, AuditT](
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
        observed_at: datetime,
        reason: PortfolioAdmissionReasonCode,
    ) -> PortfolioAdmissionBatchResult:
        identities = sorted(
            (_copy_decision_identity(candidate.journal_entry.identity) for candidate in candidates),
            key=lambda identity: identity.sort_key,
        )
        results = tuple(
            PortfolioAdmissionCandidateResult(
                identity,
                PortfolioAdmissionStatus.REJECTED_INCONSISTENT,
                (reason,),
                None,
            )
            for identity in identities
        )
        return PortfolioAdmissionBatchResult(
            observed_at=observed_at,
            status=PortfolioAdmissionStatus.REJECTED_INCONSISTENT,
            reasons=(reason,),
            candidate_results=results,
            permits=(),
        )


def _baseline(entry: DecisionJournalEntry[object, object]) -> _PortfolioBaseline:
    risk = entry.risk_decision
    return _PortfolioBaseline(
        risk.risk_snapshot_observed_at,
        risk.quote_currency,
        risk.account_equity_quote,
        risk.day_start_equity_quote,
        risk.peak_equity_quote,
        risk.daily_loss_quote,
        risk.daily_loss_fraction,
        risk.max_daily_loss_fraction,
        risk.drawdown_quote,
        risk.drawdown_fraction,
        risk.max_drawdown_fraction,
        risk.current_open_risk_quote,
        risk.simultaneous_risk_cap_quote,
        risk.remaining_simultaneous_risk_quote,
        risk.current_gross_exposure_quote,
        risk.gross_exposure_cap_quote,
        risk.remaining_gross_exposure_quote,
        risk.per_trade_risk_budget_quote,
        risk.risk_policy_version,
        risk.requires_halt_review,
    )


def _arbitration_key(item: _ValidatedCandidate) -> tuple[str, TradingSymbol, datetime]:
    intent = item.intent
    return (intent.source_id.value, intent.symbol, intent.observed_at)


def _intent_decimals_valid(intent: EntryOrderIntent) -> bool:
    return all(
        isinstance(value, Decimal) and value.is_finite() and value > 0
        for value in (
            intent.quantity,
            intent.approved_notional_quote,
            intent.approved_risk_quote,
            intent.stop_basis,
            intent.reference_price,
        )
    )


def _has_duplicate[T](values: tuple[T, ...]) -> bool:
    return any(value in values[:index] for index, value in enumerate(values))


def _canonical_reasons(
    reasons: Iterable[PortfolioAdmissionReasonCode],
) -> tuple[PortfolioAdmissionReasonCode, ...]:
    supplied = set(reasons)
    return tuple(reason for reason in PortfolioAdmissionReasonCode if reason in supplied)


def _create_permit(intent: EntryOrderIntent, observed_at: datetime) -> PortfolioAdmissionPermit:
    decision_identity = _copy_decision_identity(intent.decision_identity)
    intent_identity = EntryOrderIntentIdentity(decision_identity)
    permit = PortfolioAdmissionPermit(_permit_token=_PERMIT_TOKEN)
    values: tuple[tuple[str, object], ...] = (
        ("identity", PortfolioAdmissionPermitIdentity(intent_identity, observed_at)),
        ("intent_identity", intent_identity),
        ("decision_identity", decision_identity),
        ("source_id", decision_identity.source_id),
        ("symbol", decision_identity.symbol),
        ("observed_at", decision_identity.observed_at),
        ("authorized_at", intent.authorized_at),
        ("strategy_id", decision_identity.strategy_id),
        ("strategy_version", decision_identity.strategy_version),
        ("side", intent.side),
        ("quantity", intent.quantity),
        ("approved_risk_quote", intent.approved_risk_quote),
        ("approved_notional_quote", intent.approved_notional_quote),
        ("stop_basis", intent.stop_basis),
        ("reference_price", intent.reference_price),
        ("risk_policy_version", intent.risk_policy_version),
        ("safety_policy_version", intent.safety_policy_version),
        ("quote_currency", intent.quote_currency),
        ("admitted_for_reservation", True),
    )
    for name, value in values:
        object.__setattr__(permit, name, value)
    return permit


def _copy_decision_identity(identity: DecisionIdentity) -> DecisionIdentity:
    return DecisionIdentity(
        identity.source_id,
        TradingSymbol(identity.symbol.base, identity.symbol.quote),
        identity.observed_at,
        identity.strategy_id,
        identity.strategy_version,
    )
