"""Process-local atomic admission and start coordination for Paper intents.

Active reservations intentionally remain pending forever in this phase. Observation,
fills, release, positions, accounting, persistence, and Live behavior are deferred.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.admission import (
    PendingPortfolioReservation,
    PendingReservationSnapshot,
    PortfolioAdmissionBatchResult,
    PortfolioAdmissionCandidate,
    PortfolioAdmissionGate,
    PortfolioAdmissionPermit,
    PortfolioAdmissionStatus,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import (
    EntryOrderIntent,
    EntryOrderIntentAuthorizer,
    EntryOrderIntentIdentity,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.paper.execution import (
    PaperAuthorizationSnapshot,
    PaperOrderIdentity,
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
)
from adaptive_trading_bot.strategies.core import SourceIdentity


class PaperPortfolioCoordinatorError(Exception):
    """Base error for invalid coordinator input, state, or staged starts."""


class PaperPortfolioBatchError(PaperPortfolioCoordinatorError):
    """A batch is structurally invalid or violates finalized chronology."""


class PaperPortfolioStartError(PaperPortfolioCoordinatorError):
    """Admitted Paper lifecycles could not all be staged consistently."""


class PaperPortfolioStartBatchStatus(StrEnum):
    """Outcome of one coordinator submission."""

    STARTED = "STARTED"
    NO_START = "NO_START"
    REJECTED_INCONSISTENT = "REJECTED_INCONSISTENT"


@dataclass(frozen=True, slots=True)
class PaperStartedIntentSnapshot:
    """Detached non-executable summary of one internally pending lifecycle."""

    paper_order_identity: PaperOrderIdentity
    intent_identity: EntryOrderIntentIdentity
    decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    side: EntrySide
    quantity: Decimal
    approved_risk_quote: Decimal
    approved_notional_quote: Decimal
    stop_basis: Decimal
    reference_price: Decimal
    authorized_at: datetime
    eligible_at: datetime
    status: PaperOrderStatus
    risk_policy_version: str
    safety_policy_version: str


@dataclass(frozen=True, slots=True)
class PaperPortfolioStartBatchResult:
    """Immutable audit of one finalized or integrity-rejected submission."""

    observed_at: datetime
    status: PaperPortfolioStartBatchStatus
    admission_result: PortfolioAdmissionBatchResult
    started: tuple[PaperStartedIntentSnapshot, ...]
    active_reservations: PendingReservationSnapshot


@dataclass(frozen=True, slots=True)
class PaperPortfolioCoordinatorSnapshot:
    """Detached read-only view of process-local coordinator state."""

    last_finalized_at: datetime | None
    started: tuple[PaperStartedIntentSnapshot, ...]
    reservations: tuple[PendingPortfolioReservation, ...]


@dataclass(frozen=True, slots=True)
class _ActivePaperIntent:
    lifecycle: PaperOrderLifecycle
    reservation: PendingPortfolioReservation


class PaperPortfolioCoordinator:
    """Own pending reservations and atomically start admitted Paper lifecycles."""

    __slots__ = ("_active", "_costs", "_last_finalized_at")

    def __init__(self, costs: SimulatedExecutionCostConfiguration) -> None:
        if not isinstance(costs, SimulatedExecutionCostConfiguration):
            raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
        costs.validate()
        self._costs = SimulatedExecutionCostConfiguration(
            spread_bps=costs.spread_bps,
            slippage_bps=costs.slippage_bps,
            fee_bps=costs.fee_bps,
            latency=costs.latency,
        )
        self._active: tuple[_ActivePaperIntent, ...] = ()
        self._last_finalized_at: datetime | None = None

    def start_batch[ReasonT, AuditT](
        self,
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
    ) -> PaperPortfolioStartBatchResult:
        """Admit and stage one same-time batch, then commit all state at once.

        Integrity-rejected admission results do not seal their timestamp, so a corrected
        batch may be retried. STARTED and NO_START results do seal it.
        """

        observed_at = self._validate_submission(candidates)
        self._validate_internal_state()
        pending = self._pending_snapshot(observed_at)
        admission = PortfolioAdmissionGate().evaluate_batch(candidates, pending)
        if admission.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT:
            return PaperPortfolioStartBatchResult(
                observed_at,
                PaperPortfolioStartBatchStatus.REJECTED_INCONSISTENT,
                admission,
                (),
                self._pending_snapshot(observed_at),
            )
        if admission.status is PortfolioAdmissionStatus.NO_ADMISSION:
            result = PaperPortfolioStartBatchResult(
                observed_at,
                PaperPortfolioStartBatchStatus.NO_START,
                admission,
                (),
                self._pending_snapshot(observed_at),
            )
            self._last_finalized_at = observed_at
            return result

        candidate_by_identity = {candidate.intent.identity: candidate for candidate in candidates}
        self._validate_new_permits(admission.permits, candidate_by_identity)
        staged: list[_ActivePaperIntent] = []
        simulator = PaperOrderSimulator()
        try:
            for permit in admission.permits:
                candidate = candidate_by_identity[permit.intent_identity]
                owned_entry = deepcopy(candidate.journal_entry)
                owned_authorization = EntryOrderIntentAuthorizer().authorize(owned_entry)
                owned_intent = owned_authorization.intent
                if (
                    owned_authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
                    or owned_intent is None
                    or not _permit_matches_intent(permit, owned_intent)
                ):
                    raise PaperPortfolioStartError(
                        "coordinator-owned authority diverged from admission permit"
                    )
                lifecycle = simulator.start(
                    owned_entry,
                    owned_intent,
                    self._costs,
                )
                reservation = _reservation_from_permit(permit)
                _require_staged_authority(lifecycle, permit, reservation, self._costs)
                staged.append(_ActivePaperIntent(lifecycle, reservation))
        except Exception as error:
            raise PaperPortfolioStartError(
                "every admitted Paper lifecycle must stage before commit"
            ) from error

        next_active = tuple(
            sorted(
                (*self._active, *staged),
                key=lambda item: item.reservation.intent_identity.decision_identity.sort_key,
            )
        )
        started = tuple(_started_snapshot(item.lifecycle) for item in staged)
        result = PaperPortfolioStartBatchResult(
            observed_at,
            PaperPortfolioStartBatchStatus.STARTED,
            admission,
            tuple(sorted(started, key=lambda item: item.decision_identity.sort_key)),
            _pending_snapshot_for(next_active, observed_at),
        )
        self._active = next_active
        self._last_finalized_at = observed_at
        return result

    def snapshot(self) -> PaperPortfolioCoordinatorSnapshot:
        """Return detached state that cannot mutate coordinator-owned authority."""

        self._validate_internal_state()
        return PaperPortfolioCoordinatorSnapshot(
            self._last_finalized_at,
            tuple(_started_snapshot(item.lifecycle) for item in self._active),
            tuple(_copy_reservation(item.reservation) for item in self._active),
        )

    def _validate_submission[ReasonT, AuditT](
        self,
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
    ) -> datetime:
        if not isinstance(candidates, tuple):
            raise TypeError("candidates must be an immutable tuple")
        if not candidates:
            raise PaperPortfolioBatchError("Paper start batch must be non-empty")
        if not all(isinstance(candidate, PortfolioAdmissionCandidate) for candidate in candidates):
            raise TypeError("candidates must contain PortfolioAdmissionCandidate values")
        try:
            times = tuple(
                require_utc(
                    candidate.journal_entry.identity.observed_at,
                    field="Paper start candidate observed_at",
                )
                for candidate in candidates
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise PaperPortfolioBatchError("candidate batch time is invalid") from error
        observed_at = times[0]
        if any(value != observed_at for value in times[1:]):
            raise PaperPortfolioBatchError("every candidate must share one observed_at")
        if self._last_finalized_at is not None and observed_at <= self._last_finalized_at:
            raise PaperPortfolioBatchError(
                "batch observed_at must strictly advance after a finalized batch"
            )
        return observed_at

    def _pending_snapshot(self, observed_at: datetime) -> PendingReservationSnapshot:
        return _pending_snapshot_for(self._active, observed_at)

    def _validate_internal_state(self) -> None:
        identities: set[EntryOrderIntentIdentity] = set()
        symbols: set[TradingSymbol] = set()
        for item in self._active:
            if (
                item.reservation.intent_identity in identities
                or item.reservation.symbol in symbols
                or not _lifecycle_matches_reservation(item, self._costs)
            ):
                raise PaperPortfolioCoordinatorError(
                    "coordinator active reservation state is inconsistent"
                )
            identities.add(item.reservation.intent_identity)
            symbols.add(item.reservation.symbol)

    def _validate_new_permits[ReasonT, AuditT](
        self,
        permits: tuple[PortfolioAdmissionPermit, ...],
        candidates: dict[
            EntryOrderIntentIdentity,
            PortfolioAdmissionCandidate[ReasonT, AuditT],
        ],
    ) -> None:
        if not permits:
            raise PaperPortfolioStartError("ADMITTED result must contain a permit")
        active_identities = {item.reservation.intent_identity for item in self._active}
        active_symbols = {item.reservation.symbol for item in self._active}
        staged_identities: set[EntryOrderIntentIdentity] = set()
        staged_symbols: set[TradingSymbol] = set()
        for permit in permits:
            candidate = candidates.get(permit.intent_identity)
            if candidate is None or not _permit_matches_intent(permit, candidate.intent):
                raise PaperPortfolioStartError("admission permit does not map to exact authority")
            if (
                permit.intent_identity in active_identities
                or permit.intent_identity in staged_identities
                or permit.symbol in active_symbols
                or permit.symbol in staged_symbols
            ):
                raise PaperPortfolioStartError("admission would duplicate active Paper authority")
            staged_identities.add(permit.intent_identity)
            staged_symbols.add(permit.symbol)


def _permit_matches_intent(permit: PortfolioAdmissionPermit, intent: EntryOrderIntent) -> bool:
    try:
        return (
            permit.intent_identity == intent.identity
            and permit.decision_identity == intent.decision_identity
            and permit.source_id == intent.source_id
            and permit.symbol == intent.symbol
            and permit.observed_at == intent.observed_at
            and permit.authorized_at == intent.authorized_at
            and permit.strategy_id == intent.strategy_id
            and permit.strategy_version == intent.strategy_version
            and permit.side is intent.side
            and permit.quantity == intent.quantity
            and permit.approved_risk_quote == intent.approved_risk_quote
            and permit.approved_notional_quote == intent.approved_notional_quote
            and permit.stop_basis == intent.stop_basis
            and permit.reference_price == intent.reference_price
            and permit.risk_policy_version == intent.risk_policy_version
            and permit.safety_policy_version == intent.safety_policy_version
            and permit.quote_currency == intent.quote_currency
            and permit.admitted_for_reservation is True
        )
    except AttributeError:
        return False


def _reservation_from_permit(permit: PortfolioAdmissionPermit) -> PendingPortfolioReservation:
    identity = _copy_intent_identity(permit.intent_identity)
    return PendingPortfolioReservation(
        identity,
        identity.decision_identity.source_id,
        identity.decision_identity.symbol,
        permit.approved_risk_quote,
        permit.approved_notional_quote,
    )


def _copy_reservation(reservation: PendingPortfolioReservation) -> PendingPortfolioReservation:
    identity = _copy_intent_identity(reservation.intent_identity)
    return PendingPortfolioReservation(
        identity,
        identity.decision_identity.source_id,
        identity.decision_identity.symbol,
        reservation.reserved_risk_quote,
        reservation.reserved_notional_quote,
    )


def _pending_snapshot_for(
    active: tuple[_ActivePaperIntent, ...],
    observed_at: datetime,
) -> PendingReservationSnapshot:
    return PendingReservationSnapshot(
        observed_at,
        "USDT",
        tuple(_copy_reservation(item.reservation) for item in active),
    )


def _copy_intent_identity(identity: EntryOrderIntentIdentity) -> EntryOrderIntentIdentity:
    decision = identity.decision_identity
    return EntryOrderIntentIdentity(
        DecisionIdentity(
            deepcopy(decision.source_id),
            TradingSymbol(decision.symbol.base, decision.symbol.quote),
            decision.observed_at,
            decision.strategy_id,
            decision.strategy_version,
        )
    )


def _started_snapshot(lifecycle: PaperOrderLifecycle) -> PaperStartedIntentSnapshot:
    authority = lifecycle.accepted_authorization
    intent_identity = _copy_intent_identity(lifecycle.identity.intent_identity)
    decision_identity = intent_identity.decision_identity
    return PaperStartedIntentSnapshot(
        PaperOrderIdentity(intent_identity),
        intent_identity,
        decision_identity,
        decision_identity.source_id,
        decision_identity.symbol,
        authority.side,
        authority.quantity,
        authority.approved_risk_quote,
        authority.approved_notional_quote,
        authority.stop_basis,
        authority.reference_price,
        authority.authorized_at,
        lifecycle.eligible_at,
        lifecycle.status,
        authority.risk_policy_version,
        authority.safety_policy_version,
    )


def _require_staged_authority(
    lifecycle: PaperOrderLifecycle,
    permit: PortfolioAdmissionPermit,
    reservation: PendingPortfolioReservation,
    costs: SimulatedExecutionCostConfiguration,
) -> None:
    authority = lifecycle.accepted_authorization
    if (
        lifecycle.status is not PaperOrderStatus.PENDING
        or lifecycle.fill is not None
        or lifecycle.last_observed_at is not None
        or lifecycle.costs != costs
        or lifecycle.identity.intent_identity != permit.intent_identity
        or lifecycle.eligible_at != permit.authorized_at + costs.latency
        or not _authority_matches_permit(authority, permit)
        or reservation.reserved_risk_quote != permit.approved_risk_quote
        or reservation.reserved_notional_quote != permit.approved_notional_quote
    ):
        raise PaperPortfolioStartError("staged lifecycle does not preserve admitted authority")


def _authority_matches_permit(
    authority: PaperAuthorizationSnapshot,
    permit: PortfolioAdmissionPermit,
) -> bool:
    return (
        authority.source_id_value == permit.source_id.value
        and authority.symbol == permit.symbol
        and authority.observed_at == permit.observed_at
        and authority.strategy_id == permit.strategy_id
        and authority.strategy_version == permit.strategy_version
        and authority.authorized_at == permit.authorized_at
        and authority.risk_policy_version == permit.risk_policy_version
        and authority.safety_policy_version == permit.safety_policy_version
        and authority.side is permit.side
        and authority.quantity == permit.quantity
        and authority.approved_notional_quote == permit.approved_notional_quote
        and authority.approved_risk_quote == permit.approved_risk_quote
        and authority.stop_basis == permit.stop_basis
        and authority.reference_price == permit.reference_price
        and authority.quote_currency == permit.quote_currency
        and authority.authorized_for_execution is True
    )


def _lifecycle_matches_reservation(
    active: _ActivePaperIntent,
    costs: SimulatedExecutionCostConfiguration,
) -> bool:
    lifecycle = active.lifecycle
    reservation = active.reservation
    authority = lifecycle.accepted_authorization
    try:
        reservation.validate()
        costs.validate()
        lifecycle.costs.validate()
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        lifecycle.status is PaperOrderStatus.PENDING
        and lifecycle.fill is None
        and lifecycle.last_observed_at is None
        and lifecycle.costs == costs
        and lifecycle.identity.intent_identity == reservation.intent_identity
        and authority.source_id_value == reservation.source_id.value
        and authority.symbol == reservation.symbol
        and authority.approved_risk_quote == reservation.reserved_risk_quote
        and authority.approved_notional_quote == reservation.reserved_notional_quote
        and lifecycle.eligible_at == authority.authorized_at + costs.latency
    )
