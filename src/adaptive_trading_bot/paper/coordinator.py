"""Process-local atomic admission, Paper observation, and entry accounting."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFill,
    PositionAccountingFillBatch,
    PositionAccountingState,
    PositionAccountingTransition,
)
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
    ExitOrderIntent,
    ExitSide,
    OrderIntentAuthorizationStatus,
    exit_order_intent_is_authoritative,
)
from adaptive_trading_bot.orders.exit_intent import (
    _authorize_protective_stop_full_close,
    _copy_authoritative_exit_intent,
)
from adaptive_trading_bot.paper.accounting import (
    paper_fill_to_position_accounting_fill as _paper_fill_to_position_accounting_fill,
)
from adaptive_trading_bot.paper.execution import (
    PaperAuthorizationSnapshot,
    PaperExecutionObservation,
    PaperMarketFill,
    PaperOrderIdentity,
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
)
from adaptive_trading_bot.strategies.core import SourceIdentity

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)


class PaperPortfolioCoordinatorError(Exception):
    """Base error for invalid coordinator input, state, or staged transitions."""


class PaperPortfolioBatchError(PaperPortfolioCoordinatorError):
    """A batch is structurally invalid or violates committed chronology."""


class PaperPortfolioStartError(PaperPortfolioCoordinatorError):
    """Admitted Paper lifecycles could not all be staged consistently."""


class PaperPortfolioObservationError(PaperPortfolioCoordinatorError):
    """A Paper observation batch could not be staged and committed exactly."""


class PaperProtectiveExitAuthorizationError(PaperPortfolioCoordinatorError):
    """Protective exit authority could not be staged and committed exactly."""


class PaperPortfolioStartBatchStatus(StrEnum):
    """Outcome of one coordinator submission."""

    STARTED = "STARTED"
    NO_START = "NO_START"
    REJECTED_INCONSISTENT = "REJECTED_INCONSISTENT"


class PaperProtectiveStopStatus(StrEnum):
    """Outcome for one open position in a stop-evaluation batch."""

    TRIGGERED = "TRIGGERED"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    ALREADY_PENDING = "ALREADY_PENDING"


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
class PaperOpenPositionSnapshot:
    """Detached entry authority and conservatively locked Paper capacity."""

    paper_order_identity: PaperOrderIdentity
    intent_identity: EntryOrderIntentIdentity
    decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    side: EntrySide
    quantity: Decimal
    authorized_at: datetime
    eligible_at: datetime
    fill_at: datetime
    reference_price: Decimal
    fill_price: Decimal
    fee_quote: Decimal
    stop_basis: Decimal
    approved_risk_quote: Decimal
    approved_notional_quote: Decimal
    locked_open_risk_quote: Decimal
    locked_gross_exposure_quote: Decimal
    risk_policy_version: str
    safety_policy_version: str
    accounting_fill_id: str


@dataclass(frozen=True, slots=True)
class PaperPortfolioStartBatchResult:
    """Immutable audit of one finalized or integrity-rejected submission."""

    observed_at: datetime
    status: PaperPortfolioStartBatchStatus
    admission_result: PortfolioAdmissionBatchResult
    started: tuple[PaperStartedIntentSnapshot, ...]
    active_reservations: PendingReservationSnapshot


@dataclass(frozen=True, slots=True)
class PaperPortfolioObservationBatchResult:
    """Atomic Paper lifecycle, accounting, and capacity transition audit."""

    observed_at: datetime
    newly_filled: tuple[PaperOpenPositionSnapshot, ...]
    still_pending: tuple[PaperStartedIntentSnapshot, ...]
    active_pending_reservations: PendingReservationSnapshot
    open_positions: tuple[PaperOpenPositionSnapshot, ...]
    accounting_positions: tuple[tuple[TradingSymbol, PositionAccountingState], ...]
    accounting_transitions: tuple[PositionAccountingTransition, ...]
    locked_open_risk_quote: Decimal
    locked_gross_exposure_quote: Decimal


@dataclass(frozen=True, slots=True)
class PaperProtectiveStopEvaluation:
    """Detached audit for one coordinator-owned open position observation."""

    paper_order_identity: PaperOrderIdentity
    originating_entry_intent_identity: EntryOrderIntentIdentity
    originating_decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    reference_price: Decimal
    stop_basis: Decimal
    status: PaperProtectiveStopStatus
    exit_intent: ExitOrderIntent | None


@dataclass(frozen=True, slots=True)
class PaperProtectiveExitBatchResult:
    """Immutable authorization audit; no exit execution has occurred."""

    observed_at: datetime
    evaluations: tuple[PaperProtectiveStopEvaluation, ...]
    triggered: tuple[ExitOrderIntent, ...]
    active_exit_intents: tuple[ExitOrderIntent, ...]
    open_positions: tuple[PaperOpenPositionSnapshot, ...]
    accounting_positions: tuple[tuple[TradingSymbol, PositionAccountingState], ...]
    locked_open_risk_quote: Decimal
    locked_gross_exposure_quote: Decimal


@dataclass(frozen=True, slots=True)
class PaperPortfolioCoordinatorSnapshot:
    """Detached read-only view of process-local coordinator state."""

    last_finalized_at: datetime | None
    started: tuple[PaperStartedIntentSnapshot, ...]
    reservations: tuple[PendingPortfolioReservation, ...]
    last_observed_at: datetime | None
    open_positions: tuple[PaperOpenPositionSnapshot, ...]
    accounting_positions: tuple[tuple[TradingSymbol, PositionAccountingState], ...]
    locked_open_risk_quote: Decimal
    locked_gross_exposure_quote: Decimal
    active_exit_intents: tuple[ExitOrderIntent, ...]


@dataclass(frozen=True, slots=True)
class _ActivePaperIntent:
    lifecycle: PaperOrderLifecycle
    reservation: PendingPortfolioReservation


@dataclass(frozen=True, slots=True)
class _OpenPaperPosition:
    lifecycle: PaperOrderLifecycle
    accounting_fill: PositionAccountingFill
    locked_open_risk_quote: Decimal
    locked_gross_exposure_quote: Decimal


@dataclass(frozen=True, slots=True)
class _PendingProtectiveExit:
    open_identity: PaperOrderIdentity
    intent: ExitOrderIntent


class PaperPortfolioCoordinator:
    """Atomically coordinate admitted Paper entries through open positions."""

    __slots__ = (
        "_accounting",
        "_active",
        "_active_exit_intents",
        "_costs",
        "_last_finalized_at",
        "_last_observed_at",
        "_open_positions",
    )

    def __init__(self, costs: SimulatedExecutionCostConfiguration) -> None:
        if not isinstance(costs, SimulatedExecutionCostConfiguration):
            raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
        costs.validate()
        self._costs = _copy_costs(costs)
        self._active: tuple[_ActivePaperIntent, ...] = ()
        self._accounting = PositionAccountingCore()
        self._open_positions: tuple[_OpenPaperPosition, ...] = ()
        self._active_exit_intents: tuple[_PendingProtectiveExit, ...] = ()
        self._last_finalized_at: datetime | None = None
        self._last_observed_at: datetime | None = None

    def start_batch[ReasonT, AuditT](
        self,
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
    ) -> PaperPortfolioStartBatchResult:
        """Admit and stage one same-time batch, then commit all state at once."""

        observed_at = self._validate_submission(candidates)
        self._validate_internal_state()
        self._validate_candidate_open_baseline(candidates)
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
                lifecycle = simulator.start(owned_entry, owned_intent, self._costs)
                reservation = _reservation_from_permit(permit)
                _require_staged_authority(lifecycle, permit, reservation, self._costs)
                staged.append(_ActivePaperIntent(lifecycle, reservation))
        except Exception as error:
            raise PaperPortfolioStartError(
                "every admitted Paper lifecycle must stage before commit"
            ) from error

        next_active = _sort_active((*self._active, *staged))
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

    def observe_batch(
        self,
        observations: tuple[PaperExecutionObservation, ...],
    ) -> PaperPortfolioObservationBatchResult:
        """Atomically advance coordinator-owned lifecycles and account new fills."""

        observed_at, ordered = self._validate_observations(observations)
        self._validate_internal_state()
        active_by_key = {
            (item.reservation.source_id.value, item.reservation.symbol): item
            for item in self._active
        }
        mapped: list[tuple[_ActivePaperIntent, PaperExecutionObservation]] = []
        for observation in ordered:
            item = active_by_key.get((observation.source_id.value, observation.symbol))
            if item is None:
                raise PaperPortfolioBatchError(
                    "every observation must match one pending source and symbol"
                )
            mapped.append((item, _copy_observation(observation)))

        simulator = PaperOrderSimulator()
        staged_by_identity = {item.reservation.intent_identity: item for item in self._active}
        newly_filled: list[tuple[PaperOrderLifecycle, PositionAccountingFill]] = []
        try:
            for prior, observation in mapped:
                staged_lifecycle = simulator.clone(prior.lifecycle)
                advanced = simulator.observe(staged_lifecycle, observation)
                _require_observed_authority(prior, advanced, observation, self._costs)
                if advanced.status is PaperOrderStatus.FILLED:
                    if advanced.fill is None:
                        raise PaperPortfolioObservationError("FILLED lifecycle has no fill")
                    adapted = _paper_fill_to_position_accounting_fill(advanced.fill)
                    newly_filled.append((advanced, adapted))
                else:
                    staged_by_identity[prior.reservation.intent_identity] = _ActivePaperIntent(
                        advanced, prior.reservation
                    )

            staged_accounting = self._accounting.clone()
            accounting_transitions: tuple[PositionAccountingTransition, ...] = ()
            staged_new_open: list[_OpenPaperPosition] = []
            if newly_filled:
                for lifecycle, accounting_fill in newly_filled:
                    if staged_accounting.position(lifecycle.accepted_authorization.symbol).quantity:
                        raise PaperPortfolioObservationError(
                            "entry accounting symbol must be flat before fill"
                        )
                    staged_new_open.append(_open_position(lifecycle, accounting_fill))
                accounting_fills = tuple(item[1] for item in newly_filled)
                accounting_batch = staged_accounting.process(
                    PositionAccountingFillBatch(observed_at, accounting_fills)
                )
                accounting_transitions = accounting_batch.transitions
                _require_entry_transitions(newly_filled, accounting_transitions, self._accounting)

            filled_identities = {
                lifecycle.identity.intent_identity for lifecycle, _ in newly_filled
            }
            next_active = _sort_active(
                tuple(
                    item
                    for identity, item in staged_by_identity.items()
                    if identity not in filled_identities
                )
            )
            staged_open = list(self._open_positions)
            staged_open.extend(staged_new_open)
            next_open = _sort_open(tuple(staged_open))
            _validate_staged_state(
                next_active,
                next_open,
                self._active_exit_intents,
                staged_accounting,
                self._costs,
            )
        except Exception as error:
            if isinstance(error, PaperPortfolioObservationError):
                raise
            raise PaperPortfolioObservationError(
                "Paper observation batch failed before atomic commit"
            ) from error

        newly_filled_snapshots = tuple(
            _open_snapshot(item)
            for item in next_open
            if item.lifecycle.identity.intent_identity in filled_identities
        )
        open_snapshots = tuple(_open_snapshot(item) for item in next_open)
        locked_risk, locked_gross = _locked_totals(next_open)
        result = PaperPortfolioObservationBatchResult(
            observed_at=observed_at,
            newly_filled=newly_filled_snapshots,
            still_pending=tuple(_started_snapshot(item.lifecycle) for item in next_active),
            active_pending_reservations=_pending_snapshot_for(next_active, observed_at),
            open_positions=open_snapshots,
            accounting_positions=staged_accounting.positions(),
            accounting_transitions=accounting_transitions,
            locked_open_risk_quote=locked_risk,
            locked_gross_exposure_quote=locked_gross,
        )
        self._active = next_active
        self._open_positions = next_open
        self._accounting = staged_accounting
        self._last_observed_at = observed_at
        return result

    def evaluate_protective_stops(
        self,
        observations: tuple[PaperExecutionObservation, ...],
    ) -> PaperProtectiveExitBatchResult:
        """Authorize full-close stop exits from coordinator-owned open state only."""

        observed_at, ordered = self._validate_protective_observations(observations)
        self._validate_internal_state()
        open_by_key = {
            (
                item.lifecycle.accepted_authorization.source_id_value,
                item.lifecycle.accepted_authorization.symbol,
            ): item
            for item in self._open_positions
        }
        mapped: list[tuple[_OpenPaperPosition, PaperExecutionObservation]] = []
        for observation in ordered:
            item = open_by_key.get((observation.source_id.value, observation.symbol))
            if item is None:
                raise PaperPortfolioBatchError(
                    "every protective-stop observation must match one open source and symbol"
                )
            mapped.append((item, _copy_observation(observation)))

        pending_by_identity = {item.open_identity: item for item in self._active_exit_intents}
        staged = list(self._active_exit_intents)
        evaluations: list[PaperProtectiveStopEvaluation] = []
        newly_triggered: list[ExitOrderIntent] = []
        try:
            for open_item, observation in mapped:
                lifecycle = open_item.lifecycle
                fill = lifecycle.fill
                if fill is None or observation.observed_at <= fill.fill_at:
                    raise PaperProtectiveExitAuthorizationError(
                        "protective-stop observation must strictly follow entry fill"
                    )
                existing = pending_by_identity.get(lifecycle.identity)
                if existing is not None:
                    evaluations.append(
                        _protective_evaluation(
                            open_item,
                            observation,
                            PaperProtectiveStopStatus.ALREADY_PENDING,
                            existing.intent,
                        )
                    )
                    continue

                authority = lifecycle.accepted_authorization
                triggered = (
                    authority.side is EntrySide.BUY
                    and observation.reference_price <= authority.stop_basis
                ) or (
                    authority.side is EntrySide.SELL
                    and observation.reference_price >= authority.stop_basis
                )
                if not triggered:
                    evaluations.append(
                        _protective_evaluation(
                            open_item,
                            observation,
                            PaperProtectiveStopStatus.NOT_TRIGGERED,
                            None,
                        )
                    )
                    continue

                intent = _authorize_protective_stop_full_close(
                    originating_entry_intent_identity=lifecycle.identity.intent_identity,
                    originating_decision_identity=(
                        lifecycle.identity.intent_identity.decision_identity
                    ),
                    source_id=fill.source_id,
                    symbol=fill.symbol,
                    trigger_observed_at=observation.observed_at,
                    side=ExitSide.SELL if authority.side is EntrySide.BUY else ExitSide.BUY,
                    quantity=fill.quantity,
                    stop_basis=authority.stop_basis,
                    trigger_reference_price=observation.reference_price,
                    risk_policy_version=authority.risk_policy_version,
                    safety_policy_version=authority.safety_policy_version,
                )
                _require_exit_matches_open(intent, open_item, self._accounting)
                owned = _copy_authoritative_exit_intent(intent)
                pending = _PendingProtectiveExit(lifecycle.identity, owned)
                staged.append(pending)
                pending_by_identity[lifecycle.identity] = pending
                newly_triggered.append(intent)
                evaluations.append(
                    _protective_evaluation(
                        open_item,
                        observation,
                        PaperProtectiveStopStatus.TRIGGERED,
                        intent,
                    )
                )

            next_exits = _sort_pending_exits(tuple(staged))
            _validate_staged_state(
                self._active,
                self._open_positions,
                next_exits,
                self._accounting,
                self._costs,
            )
        except Exception as error:
            if isinstance(error, PaperProtectiveExitAuthorizationError):
                raise
            raise PaperProtectiveExitAuthorizationError(
                "protective exit batch failed before atomic commit"
            ) from error

        locked_risk, locked_gross = _locked_totals(self._open_positions)
        result = PaperProtectiveExitBatchResult(
            observed_at,
            tuple(sorted(evaluations, key=lambda item: (str(item.symbol), item.source_id.value))),
            tuple(
                _copy_authoritative_exit_intent(item)
                for item in sorted(newly_triggered, key=_exit_order_key)
            ),
            tuple(_copy_authoritative_exit_intent(item.intent) for item in next_exits),
            tuple(_open_snapshot(item) for item in self._open_positions),
            self._accounting.positions(),
            locked_risk,
            locked_gross,
        )
        self._active_exit_intents = next_exits
        self._last_observed_at = observed_at
        return result

    def snapshot(self) -> PaperPortfolioCoordinatorSnapshot:
        """Return detached state that cannot mutate coordinator-owned authority."""

        self._validate_internal_state()
        locked_risk, locked_gross = _locked_totals(self._open_positions)
        return PaperPortfolioCoordinatorSnapshot(
            self._last_finalized_at,
            tuple(_started_snapshot(item.lifecycle) for item in self._active),
            tuple(_copy_reservation(item.reservation) for item in self._active),
            self._last_observed_at,
            tuple(_open_snapshot(item) for item in self._open_positions),
            self._accounting.positions(),
            locked_risk,
            locked_gross,
            tuple(
                _copy_authoritative_exit_intent(item.intent) for item in self._active_exit_intents
            ),
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
        latest = _latest_time(self._last_finalized_at, self._last_observed_at)
        if latest is not None and observed_at <= latest:
            raise PaperPortfolioBatchError(
                "batch observed_at must strictly advance after committed Paper state"
            )
        return observed_at

    def _validate_observations(
        self,
        observations: tuple[PaperExecutionObservation, ...],
    ) -> tuple[datetime, tuple[PaperExecutionObservation, ...]]:
        if not isinstance(observations, tuple):
            raise TypeError("observations must be an immutable tuple")
        if not observations:
            raise PaperPortfolioBatchError("Paper observation batch must be non-empty")
        if not all(isinstance(item, PaperExecutionObservation) for item in observations):
            raise TypeError("observations must contain PaperExecutionObservation values")
        try:
            for item in observations:
                item.validate()
            times = tuple(
                require_utc(item.observed_at, field="Paper observation batch observed_at")
                for item in observations
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise PaperPortfolioBatchError("Paper observation batch is malformed") from error
        observed_at = times[0]
        if any(value != observed_at for value in times[1:]):
            raise PaperPortfolioBatchError("every observation must share one observed_at")
        symbols = tuple(item.symbol for item in observations)
        if len(set(symbols)) != len(symbols):
            raise PaperPortfolioBatchError("at most one observation per symbol is allowed")
        if self._last_observed_at is not None and observed_at <= self._last_observed_at:
            raise PaperPortfolioBatchError("observation timestamps must strictly advance")
        if self._last_finalized_at is not None and observed_at < self._last_finalized_at:
            raise PaperPortfolioBatchError(
                "observation cannot precede the latest finalized start batch"
            )
        ordered = tuple(
            sorted(
                observations,
                key=lambda item: (str(item.symbol), item.source_id.value),
            )
        )
        return observed_at, ordered

    def _validate_candidate_open_baseline[ReasonT, AuditT](
        self,
        candidates: tuple[PortfolioAdmissionCandidate[ReasonT, AuditT], ...],
    ) -> None:
        locked_risk, locked_gross = _locked_totals(self._open_positions)
        open_symbols = {
            item.lifecycle.accepted_authorization.symbol for item in self._open_positions
        }
        try:
            for candidate in candidates:
                risk = candidate.journal_entry.risk_decision
                if (
                    risk.current_open_risk_quote != locked_risk
                    or risk.current_gross_exposure_quote != locked_gross
                ):
                    raise PaperPortfolioStartError(
                        "candidate RiskDecision baseline does not match Paper open capacity"
                    )
                if candidate.intent.symbol in open_symbols:
                    raise PaperPortfolioStartError(
                        "entry candidate targets an occupied Paper symbol"
                    )
        except AttributeError as error:
            raise PaperPortfolioStartError("candidate open-capacity audit is malformed") from error

    def _validate_protective_observations(
        self,
        observations: tuple[PaperExecutionObservation, ...],
    ) -> tuple[datetime, tuple[PaperExecutionObservation, ...]]:
        if not isinstance(observations, tuple):
            raise TypeError("observations must be an immutable tuple")
        if not observations:
            raise PaperPortfolioBatchError("protective-stop observation batch must be non-empty")
        if not all(isinstance(item, PaperExecutionObservation) for item in observations):
            raise TypeError("observations must contain PaperExecutionObservation values")
        try:
            for item in observations:
                item.validate()
            times = tuple(
                require_utc(item.observed_at, field="protective-stop observed_at")
                for item in observations
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise PaperPortfolioBatchError(
                "protective-stop observation batch is malformed"
            ) from error
        observed_at = times[0]
        if any(value != observed_at for value in times[1:]):
            raise PaperPortfolioBatchError("every observation must share one observed_at")
        symbols = tuple(item.symbol for item in observations)
        if len(set(symbols)) != len(symbols):
            raise PaperPortfolioBatchError("at most one observation per symbol is allowed")
        latest = _latest_time(self._last_finalized_at, self._last_observed_at)
        if latest is not None and observed_at <= latest:
            raise PaperPortfolioBatchError(
                "protective-stop observed_at must strictly advance Paper chronology"
            )
        return observed_at, tuple(
            sorted(observations, key=lambda item: (str(item.symbol), item.source_id.value))
        )

    def _pending_snapshot(self, observed_at: datetime) -> PendingReservationSnapshot:
        return _pending_snapshot_for(self._active, observed_at)

    def _validate_internal_state(self) -> None:
        _validate_staged_state(
            self._active,
            self._open_positions,
            self._active_exit_intents,
            self._accounting,
            self._costs,
        )

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
        open_symbols = {
            item.lifecycle.accepted_authorization.symbol for item in self._open_positions
        }
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
                or permit.symbol in open_symbols
            ):
                raise PaperPortfolioStartError("admission would duplicate Paper authority")
            staged_identities.add(permit.intent_identity)
            staged_symbols.add(permit.symbol)


def _latest_time(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _sort_active(active: tuple[_ActivePaperIntent, ...]) -> tuple[_ActivePaperIntent, ...]:
    return tuple(
        sorted(
            active,
            key=lambda item: item.reservation.intent_identity.decision_identity.sort_key,
        )
    )


def _sort_open(open_positions: tuple[_OpenPaperPosition, ...]) -> tuple[_OpenPaperPosition, ...]:
    return tuple(
        sorted(
            open_positions,
            key=lambda item: (
                str(item.lifecycle.accepted_authorization.symbol),
                item.accounting_fill.fill_id,
            ),
        )
    )


def _exit_order_key(intent: ExitOrderIntent) -> tuple[str, tuple[object, ...]]:
    return str(intent.symbol), intent.originating_decision_identity.sort_key


def _sort_pending_exits(
    exits: tuple[_PendingProtectiveExit, ...],
) -> tuple[_PendingProtectiveExit, ...]:
    return tuple(sorted(exits, key=lambda item: _exit_order_key(item.intent)))


def _copy_costs(costs: SimulatedExecutionCostConfiguration) -> SimulatedExecutionCostConfiguration:
    return SimulatedExecutionCostConfiguration(
        costs.spread_bps, costs.slippage_bps, costs.fee_bps, costs.latency
    )


def _copy_observation(observation: PaperExecutionObservation) -> PaperExecutionObservation:
    return PaperExecutionObservation(
        deepcopy(observation.source_id),
        TradingSymbol(observation.symbol.base, observation.symbol.quote),
        observation.observed_at,
        observation.reference_price,
    )


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


def _open_snapshot(item: _OpenPaperPosition) -> PaperOpenPositionSnapshot:
    lifecycle = item.lifecycle
    fill = lifecycle.fill
    if fill is None:
        raise PaperPortfolioCoordinatorError("open Paper position has no fill")
    identity = _copy_intent_identity(lifecycle.identity.intent_identity)
    decision = identity.decision_identity
    return PaperOpenPositionSnapshot(
        PaperOrderIdentity(identity),
        identity,
        decision,
        decision.source_id,
        decision.symbol,
        fill.side,
        fill.quantity,
        fill.authorized_at,
        fill.eligible_at,
        fill.fill_at,
        fill.reference_price,
        fill.fill_price,
        fill.fee_quote,
        lifecycle.accepted_authorization.stop_basis,
        lifecycle.accepted_authorization.approved_risk_quote,
        lifecycle.accepted_authorization.approved_notional_quote,
        item.locked_open_risk_quote,
        item.locked_gross_exposure_quote,
        fill.risk_policy_version,
        fill.safety_policy_version,
        item.accounting_fill.fill_id,
    )


def _protective_evaluation(
    item: _OpenPaperPosition,
    observation: PaperExecutionObservation,
    status: PaperProtectiveStopStatus,
    intent: ExitOrderIntent | None,
) -> PaperProtectiveStopEvaluation:
    lifecycle = item.lifecycle
    authority = lifecycle.accepted_authorization
    identity = _copy_intent_identity(lifecycle.identity.intent_identity)
    decision = identity.decision_identity
    return PaperProtectiveStopEvaluation(
        PaperOrderIdentity(identity),
        identity,
        decision,
        deepcopy(decision.source_id),
        TradingSymbol(decision.symbol.base, decision.symbol.quote),
        observation.observed_at,
        observation.reference_price,
        authority.stop_basis,
        status,
        None if intent is None else _copy_authoritative_exit_intent(intent),
    )


def _require_exit_matches_open(
    intent: ExitOrderIntent,
    item: _OpenPaperPosition,
    accounting: PositionAccountingCore,
) -> None:
    lifecycle = item.lifecycle
    authority = lifecycle.accepted_authorization
    fill = lifecycle.fill
    if fill is None:
        raise PaperProtectiveExitAuthorizationError("exit authority requires an open fill")
    state = accounting.position(fill.symbol)
    with localcontext(_DECIMAL_CONTEXT):
        expected_quantity = fill.quantity if fill.side is EntrySide.BUY else -fill.quantity
    expected_side = ExitSide.SELL if fill.side is EntrySide.BUY else ExitSide.BUY
    triggered = (
        fill.side is EntrySide.BUY and intent.trigger_reference_price <= authority.stop_basis
    ) or (fill.side is EntrySide.SELL and intent.trigger_reference_price >= authority.stop_basis)
    if (
        not exit_order_intent_is_authoritative(intent)
        or lifecycle.status is not PaperOrderStatus.FILLED
        or intent.identity.originating_entry_intent_identity != lifecycle.identity.intent_identity
        or intent.originating_entry_intent_identity != lifecycle.identity.intent_identity
        or intent.originating_decision_identity
        != lifecycle.identity.intent_identity.decision_identity
        or intent.source_id != fill.source_id
        or intent.symbol != fill.symbol
        or intent.trigger_observed_at <= fill.fill_at
        or intent.authorized_at != intent.trigger_observed_at
        or intent.side is not expected_side
        or intent.quantity != fill.quantity
        or intent.stop_basis != authority.stop_basis
        or intent.risk_policy_version != authority.risk_policy_version
        or intent.safety_policy_version != authority.safety_policy_version
        or state.quantity != expected_quantity
        or state.average_entry_price != fill.fill_price
        or not triggered
    ):
        raise PaperProtectiveExitAuthorizationError(
            "protective exit diverged from coordinator-owned open authority"
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


def _require_observed_authority(
    prior: _ActivePaperIntent,
    advanced: PaperOrderLifecycle,
    observation: PaperExecutionObservation,
    costs: SimulatedExecutionCostConfiguration,
) -> None:
    before = prior.lifecycle
    authority = before.accepted_authorization
    try:
        validated = PaperOrderSimulator().clone(advanced)
    except Exception as error:
        raise PaperPortfolioObservationError(
            "observed lifecycle diverged from accepted authority"
        ) from error
    if (
        type(advanced) is not PaperOrderLifecycle
        or validated != advanced
        or advanced.identity != before.identity
        or advanced.accepted_authorization != authority
        or advanced.costs != costs
        or advanced.eligible_at != before.eligible_at
        or advanced.last_observed_at != observation.observed_at
        or advanced.status not in {PaperOrderStatus.PENDING, PaperOrderStatus.FILLED}
    ):
        raise PaperPortfolioObservationError("observed lifecycle changed accepted authority")
    if advanced.status is PaperOrderStatus.PENDING:
        if advanced.fill is not None or observation.observed_at >= advanced.eligible_at:
            raise PaperPortfolioObservationError("pending observation result is inconsistent")
        return
    fill = advanced.fill
    if fill is None or observation.observed_at < advanced.eligible_at:
        raise PaperPortfolioObservationError("filled observation result is inconsistent")
    if not _fill_matches_authority(fill, advanced, observation):
        raise PaperPortfolioObservationError("Paper fill diverged from lifecycle authority")


def _fill_matches_authority(
    fill: PaperMarketFill,
    lifecycle: PaperOrderLifecycle,
    observation: PaperExecutionObservation,
) -> bool:
    authority = lifecycle.accepted_authorization
    try:
        return (
            fill.intent_identity == lifecycle.identity.intent_identity
            and fill.decision_identity == lifecycle.intent.decision_identity
            and fill.source_id.value == authority.source_id_value
            and fill.symbol == authority.symbol == observation.symbol
            and fill.side is authority.side
            and fill.quantity == authority.quantity
            and fill.authorized_at == authority.authorized_at
            and fill.eligible_at == lifecycle.eligible_at
            and fill.fill_at == observation.observed_at
            and fill.reference_price == observation.reference_price
            and fill.spread_bps == lifecycle.costs.spread_bps
            and fill.slippage_bps == lifecycle.costs.slippage_bps
            and fill.fee_bps == lifecycle.costs.fee_bps
            and fill.risk_policy_version == authority.risk_policy_version
            and fill.safety_policy_version == authority.safety_policy_version
        )
    except AttributeError:
        return False


def _require_entry_transitions(
    filled: list[tuple[PaperOrderLifecycle, PositionAccountingFill]],
    transitions: tuple[PositionAccountingTransition, ...],
    prior_core: PositionAccountingCore,
) -> None:
    expected = {item.fill_id: (lifecycle, item) for lifecycle, item in filled}
    if len(transitions) != len(expected):
        raise PaperPortfolioObservationError("accounting transition count is inconsistent")
    for transition in transitions:
        matched = expected.get(transition.fill.fill_id)
        if matched is None:
            raise PaperPortfolioObservationError("accounting returned an unknown fill")
        lifecycle, fill = matched
        prior = prior_core.position(fill.symbol)
        with localcontext(_DECIMAL_CONTEXT):
            expected_fees = prior.cumulative_fees + fill.fee_quote
            signed = (
                fill.quantity
                if lifecycle.accepted_authorization.side is EntrySide.BUY
                else -fill.quantity
            )
        if (
            transition.fill != fill
            or prior.quantity != 0
            or transition.position_quantity_before != 0
            or transition.position_quantity_after != signed
            or transition.average_entry_before is not None
            or transition.average_entry_after != fill.fill_price
            or transition.closed_quantity != 0
            or transition.gross_realized_pnl_delta != 0
            or transition.cumulative_gross_realized_pnl != prior.cumulative_gross_realized_pnl
            or transition.cumulative_fees != expected_fees
        ):
            raise PaperPortfolioObservationError("accounting entry transition is inconsistent")


def _open_position(
    lifecycle: PaperOrderLifecycle,
    accounting_fill: PositionAccountingFill,
) -> _OpenPaperPosition:
    locked_risk, locked_gross = _locked_capacity(lifecycle)
    return _OpenPaperPosition(
        PaperOrderSimulator().clone(lifecycle),
        PositionAccountingFill(
            accounting_fill.fill_id,
            TradingSymbol(accounting_fill.symbol.base, accounting_fill.symbol.quote),
            accounting_fill.side,
            accounting_fill.quantity,
            accounting_fill.fill_at,
            accounting_fill.fill_price,
            accounting_fill.fee_quote,
        ),
        locked_risk,
        locked_gross,
    )


def _locked_capacity(lifecycle: PaperOrderLifecycle) -> tuple[Decimal, Decimal]:
    fill = lifecycle.fill
    if fill is None:
        raise PaperPortfolioObservationError("open position requires one Paper fill")
    authority = lifecycle.accepted_authorization
    if (authority.side is EntrySide.BUY and authority.stop_basis >= fill.fill_price) or (
        authority.side is EntrySide.SELL and authority.stop_basis <= fill.fill_price
    ):
        raise PaperPortfolioObservationError("entry fill crossed or reached its stop basis")
    with localcontext(_DECIMAL_CONTEXT):
        actual_notional = fill.quantity * fill.fill_price
        actual_stop_risk = fill.quantity * abs(fill.fill_price - authority.stop_basis)
        locked_gross = max(authority.approved_notional_quote, actual_notional)
        locked_risk = max(authority.approved_risk_quote, actual_stop_risk)
    if not all(value.is_finite() and value > 0 for value in (locked_gross, locked_risk)):
        raise PaperPortfolioObservationError("locked Paper capacity is invalid")
    return locked_risk, locked_gross


def _locked_totals(open_positions: tuple[_OpenPaperPosition, ...]) -> tuple[Decimal, Decimal]:
    with localcontext(_DECIMAL_CONTEXT):
        return (
            sum((item.locked_open_risk_quote for item in open_positions), _ZERO),
            sum((item.locked_gross_exposure_quote for item in open_positions), _ZERO),
        )


def _validate_staged_state(
    active: tuple[_ActivePaperIntent, ...],
    open_positions: tuple[_OpenPaperPosition, ...],
    active_exit_intents: tuple[_PendingProtectiveExit, ...],
    accounting: PositionAccountingCore,
    costs: SimulatedExecutionCostConfiguration,
) -> None:
    pending_identities: set[EntryOrderIntentIdentity] = set()
    symbols: set[TradingSymbol] = set()
    for active_item in active:
        if (
            active_item.reservation.intent_identity in pending_identities
            or active_item.reservation.symbol in symbols
            or not _lifecycle_matches_reservation(active_item, costs)
        ):
            raise PaperPortfolioCoordinatorError(
                "coordinator active reservation state is inconsistent"
            )
        pending_identities.add(active_item.reservation.intent_identity)
        symbols.add(active_item.reservation.symbol)

    fill_ids: set[str] = set()
    for open_item in open_positions:
        lifecycle = open_item.lifecycle
        fill = lifecycle.fill
        try:
            cloned = PaperOrderSimulator().clone(lifecycle)
        except (AttributeError, TypeError, ValueError) as error:
            raise PaperPortfolioCoordinatorError("open lifecycle is invalid") from error
        if (
            cloned != lifecycle
            or lifecycle.status is not PaperOrderStatus.FILLED
            or fill is None
            or lifecycle.accepted_authorization.symbol in symbols
            or open_item.accounting_fill.fill_id in fill_ids
        ):
            raise PaperPortfolioCoordinatorError("open Paper authority is inconsistent")
        adapted = _paper_fill_to_position_accounting_fill(fill)
        state = accounting.position(fill.symbol)
        try:
            expected_locked_risk, expected_locked_gross = _locked_capacity(lifecycle)
        except PaperPortfolioObservationError as error:
            raise PaperPortfolioCoordinatorError("open capacity is invalid") from error
        with localcontext(_DECIMAL_CONTEXT):
            expected_quantity = fill.quantity if fill.side is EntrySide.BUY else -fill.quantity
        if (
            adapted != open_item.accounting_fill
            or state.quantity != expected_quantity
            or state.average_entry_price != fill.fill_price
            or state.cumulative_gross_realized_pnl != 0
            or state.cumulative_fees != fill.fee_quote
            or open_item.locked_open_risk_quote != expected_locked_risk
            or open_item.locked_gross_exposure_quote != expected_locked_gross
        ):
            raise PaperPortfolioCoordinatorError("open position and accounting diverged")
        symbols.add(fill.symbol)
        fill_ids.add(open_item.accounting_fill.fill_id)
    if {symbol for symbol, _ in accounting.positions()} != {
        item.lifecycle.accepted_authorization.symbol for item in open_positions
    }:
        raise PaperPortfolioCoordinatorError("accounting contains unowned Paper symbols")

    open_by_identity = {item.lifecycle.identity: item for item in open_positions}
    seen_exit_identities: set[PaperOrderIdentity] = set()
    for pending_exit in active_exit_intents:
        pending_open = open_by_identity.get(pending_exit.open_identity)
        if (
            pending_open is None
            or pending_exit.open_identity in seen_exit_identities
            or not exit_order_intent_is_authoritative(pending_exit.intent)
        ):
            raise PaperPortfolioCoordinatorError("active protective exit state is inconsistent")
        try:
            _require_exit_matches_open(pending_exit.intent, pending_open, accounting)
        except PaperProtectiveExitAuthorizationError as error:
            raise PaperPortfolioCoordinatorError(
                "active protective exit diverged from open authority"
            ) from error
        seen_exit_identities.add(pending_exit.open_identity)


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
        cloned = PaperOrderSimulator().clone(lifecycle)
    except (AttributeError, TypeError, ValueError):
        return False
    last_observation_valid = lifecycle.last_observed_at is None or (
        authority.authorized_at <= lifecycle.last_observed_at < lifecycle.eligible_at
    )
    return (
        cloned == lifecycle
        and lifecycle.status is PaperOrderStatus.PENDING
        and lifecycle.fill is None
        and last_observation_valid
        and lifecycle.costs == costs
        and lifecycle.identity.intent_identity == reservation.intent_identity
        and authority.source_id_value == reservation.source_id.value
        and authority.symbol == reservation.symbol
        and authority.approved_risk_quote == reservation.reserved_risk_quote
        and authority.approved_notional_quote == reservation.reserved_notional_quote
        and lifecycle.eligible_at == authority.authorized_at + costs.latency
    )
