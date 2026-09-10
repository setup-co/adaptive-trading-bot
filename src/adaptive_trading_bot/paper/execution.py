"""Causal single-intent Paper execution without account or exchange behavior.

This is deliberately not a multi-intent broker. Strategy routing, portfolio risk
reservation, positions, balances, exits, persistence, and live execution are deferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryIntentPurpose,
    EntryOrderIntent,
    EntryOrderIntentAuthorizer,
    EntryOrderIntentIdentity,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.strategies.core import SourceIdentity

_PAPER_SIMULATION_SCHEMA_VERSION = "1.0.0"
_PAPER_LIFECYCLE_TOKEN = object()


class PaperExecutionError(Exception):
    """Base error for invalid Paper authority, observations, or lifecycle state."""


class PaperAuthorizationError(PaperExecutionError):
    """A journal entry and supplied intent do not establish exact authority."""


class PaperObservationError(PaperExecutionError):
    """An observed execution price violates identity, time, or price invariants."""


class PaperLifecycleError(PaperExecutionError):
    """A lifecycle object is malformed or internally contradictory."""


class PaperOrderStatus(StrEnum):
    """The only states in the Phase 10.2 single-intent lifecycle."""

    PENDING = "PENDING"
    FILLED = "FILLED"


@dataclass(frozen=True, slots=True)
class PaperOrderIdentity:
    """Deterministic Paper identity rooted in the authorized intent identity."""

    intent_identity: EntryOrderIntentIdentity
    simulation_schema_version: str = field(init=False, default=_PAPER_SIMULATION_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True)
class PaperAuthorizationSnapshot:
    """Detached immutable values accepted from an exact authorization at start."""

    source_id_value: str
    symbol: TradingSymbol
    observed_at: datetime
    strategy_id: str
    strategy_version: str
    intent_identity_decision: tuple[str, str, str, datetime, str, str]
    decision_identity: tuple[str, str, str, datetime, str, str]
    intent_purpose: EntryIntentPurpose
    intent_schema_version: str
    authorized_at: datetime
    risk_policy_version: str
    safety_policy_version: str
    side: EntrySide
    quantity: Decimal
    approved_notional_quote: Decimal
    approved_risk_quote: Decimal
    stop_basis: Decimal
    reference_price: Decimal
    quote_currency: str
    authorized_for_execution: bool


@dataclass(frozen=True, slots=True)
class PaperExecutionObservation:
    """One already-observed Paper execution reference supplied by an adapter."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    reference_price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="paper observation observed_at"),
        )
        self.validate()

    def validate(self) -> None:
        """Revalidate an observation before every state transition."""

        if not isinstance(self.source_id, SourceIdentity):
            raise TypeError("observation source_id must implement SourceIdentity")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("observation symbol must be a TradingSymbol")
        require_utc(self.observed_at, field="paper observation observed_at")
        if not isinstance(self.reference_price, Decimal):
            raise TypeError("observation reference_price must be a Decimal")
        if not self.reference_price.is_finite() or self.reference_price <= 0:
            raise ValueError("observation reference_price must be finite and positive")


@dataclass(frozen=True, slots=True)
class PaperMarketFill:
    """Immutable deterministic full Paper fill with complete intent provenance."""

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
    spread_bps: Decimal
    slippage_bps: Decimal
    fill_price: Decimal
    fee_bps: Decimal
    fee_quote: Decimal
    risk_policy_version: str
    safety_policy_version: str
    simulation_schema_version: str = field(init=False, default=_PAPER_SIMULATION_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True, init=False)
class PaperOrderLifecycle:
    """Immutable state for exactly one reauthorized entry intent."""

    identity: PaperOrderIdentity
    intent: EntryOrderIntent
    accepted_authorization: PaperAuthorizationSnapshot
    costs: SimulatedExecutionCostConfiguration
    status: PaperOrderStatus
    eligible_at: datetime
    last_observed_at: datetime | None
    fill: PaperMarketFill | None
    _supplied_intent: EntryOrderIntent = field(repr=False, compare=False)
    _authorization_seal: object = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        token = kwargs.pop("_paper_lifecycle_token", None)
        if args or kwargs or token is not _PAPER_LIFECYCLE_TOKEN:
            raise TypeError("PaperOrderLifecycle can only be created by PaperOrderSimulator")
        return object.__new__(cls)

    def __init__(self, *, _paper_lifecycle_token: object) -> None:
        del _paper_lifecycle_token


class PaperOrderSimulator:
    """Start and causally advance one authorized Paper lifecycle at a time."""

    __slots__ = ()

    def start[ReasonT, AuditT](
        self,
        journal_entry: DecisionJournalEntry[ReasonT, AuditT],
        intent: EntryOrderIntent,
        costs: SimulatedExecutionCostConfiguration,
    ) -> PaperOrderLifecycle:
        """Reauthorize exact provenance before creating PENDING state."""

        if not isinstance(journal_entry, DecisionJournalEntry):
            raise TypeError("journal_entry must be a DecisionJournalEntry")
        if type(intent) is not EntryOrderIntent:
            raise PaperAuthorizationError("intent type is not authoritative")
        if not isinstance(costs, SimulatedExecutionCostConfiguration):
            raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
        costs.validate()
        try:
            journal_entry.validate()
            authorization = EntryOrderIntentAuthorizer().authorize(journal_entry)
            matches = authorization.intent == intent
        except (AttributeError, TypeError, ValueError) as error:
            raise PaperAuthorizationError("journal or intent provenance is invalid") from error
        if (
            authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
            or authorization.intent is None
            or not matches
            or intent.authorized_for_execution is not True
        ):
            raise PaperAuthorizationError(
                "intent does not exactly match reauthorized journal authority"
            )
        accepted_intent = authorization.intent
        accepted_authorization = _snapshot_authorization(accepted_intent)
        return _create_lifecycle(
            intent=accepted_intent,
            supplied_intent=intent,
            accepted_authorization=accepted_authorization,
            costs=costs,
            status=PaperOrderStatus.PENDING,
            last_observed_at=None,
            fill=None,
        )

    def observe(
        self,
        lifecycle: PaperOrderLifecycle,
        observation: PaperExecutionObservation,
    ) -> PaperOrderLifecycle:
        """Process exactly one observed price without searching future data."""

        if type(lifecycle) is not PaperOrderLifecycle:
            raise TypeError("lifecycle must be a PaperOrderLifecycle")
        if not isinstance(observation, PaperExecutionObservation):
            raise TypeError("observation must be a PaperExecutionObservation")
        _validate_lifecycle(lifecycle)
        try:
            observation.validate()
        except (TypeError, ValueError) as error:
            raise PaperObservationError("paper observation is invalid") from error
        intent = lifecycle.intent
        authority = lifecycle.accepted_authorization
        if (
            observation.source_id.value != authority.source_id_value
            or observation.symbol != authority.symbol
        ):
            raise PaperObservationError("observation identity does not match intent")
        if observation.observed_at < authority.authorized_at:
            raise PaperObservationError("observation precedes intent authorization")
        if (
            lifecycle.last_observed_at is not None
            and observation.observed_at <= lifecycle.last_observed_at
        ):
            raise PaperObservationError("observation time must strictly advance")
        if lifecycle.status is PaperOrderStatus.FILLED:
            return lifecycle
        if observation.observed_at < lifecycle.eligible_at:
            return _create_lifecycle(
                intent=intent,
                supplied_intent=lifecycle._supplied_intent,
                accepted_authorization=authority,
                costs=lifecycle.costs,
                status=PaperOrderStatus.PENDING,
                last_observed_at=observation.observed_at,
                fill=None,
            )

        side = (
            SimulatedExecutionSide.BUY
            if authority.side is EntrySide.BUY
            else SimulatedExecutionSide.SELL
        )
        quote = price_simulated_market_fill(
            side=side,
            quantity=authority.quantity,
            reference_price=observation.reference_price,
            costs=lifecycle.costs,
        )
        fill = PaperMarketFill(
            intent_identity=intent.identity,
            decision_identity=intent.decision_identity,
            source_id=intent.source_id,
            symbol=intent.symbol,
            side=authority.side,
            quantity=authority.quantity,
            authorized_at=authority.authorized_at,
            eligible_at=lifecycle.eligible_at,
            fill_at=observation.observed_at,
            reference_price=observation.reference_price,
            spread_bps=lifecycle.costs.spread_bps,
            slippage_bps=lifecycle.costs.slippage_bps,
            fill_price=quote.fill_price,
            fee_bps=lifecycle.costs.fee_bps,
            fee_quote=quote.fee_quote,
            risk_policy_version=authority.risk_policy_version,
            safety_policy_version=authority.safety_policy_version,
        )
        return _create_lifecycle(
            intent=intent,
            supplied_intent=lifecycle._supplied_intent,
            accepted_authorization=authority,
            costs=lifecycle.costs,
            status=PaperOrderStatus.FILLED,
            last_observed_at=observation.observed_at,
            fill=fill,
        )


def _create_lifecycle(
    *,
    intent: EntryOrderIntent,
    supplied_intent: EntryOrderIntent,
    accepted_authorization: PaperAuthorizationSnapshot,
    costs: SimulatedExecutionCostConfiguration,
    status: PaperOrderStatus,
    last_observed_at: datetime | None,
    fill: PaperMarketFill | None,
) -> PaperOrderLifecycle:
    lifecycle = PaperOrderLifecycle(_paper_lifecycle_token=_PAPER_LIFECYCLE_TOKEN)
    values: tuple[tuple[str, object], ...] = (
        ("identity", PaperOrderIdentity(intent.identity)),
        ("intent", intent),
        ("accepted_authorization", accepted_authorization),
        ("costs", costs),
        ("status", status),
        ("eligible_at", accepted_authorization.authorized_at + costs.latency),
        ("last_observed_at", last_observed_at),
        ("fill", fill),
        ("_supplied_intent", supplied_intent),
        ("_authorization_seal", _PAPER_LIFECYCLE_TOKEN),
    )
    for name, value in values:
        object.__setattr__(lifecycle, name, value)
    _validate_lifecycle(lifecycle)
    return lifecycle


def _validate_lifecycle(lifecycle: PaperOrderLifecycle) -> None:
    intent = lifecycle.intent
    authority = lifecycle.accepted_authorization
    costs = lifecycle.costs
    if lifecycle._authorization_seal is not _PAPER_LIFECYCLE_TOKEN:
        raise PaperLifecycleError("lifecycle was not created by PaperOrderSimulator")
    if (
        _snapshot_authorization(intent) != authority
        or _snapshot_authorization(lifecycle._supplied_intent) != authority
    ):
        raise PaperLifecycleError("intent authority drifted after Paper start")
    try:
        costs.validate()
    except (TypeError, ValueError) as error:
        raise PaperLifecycleError("lifecycle costs are invalid") from error
    if (
        lifecycle.identity != PaperOrderIdentity(intent.identity)
        or lifecycle.eligible_at != authority.authorized_at + costs.latency
        or lifecycle.status not in {PaperOrderStatus.PENDING, PaperOrderStatus.FILLED}
        or (
            lifecycle.last_observed_at is not None
            and lifecycle.last_observed_at < authority.authorized_at
        )
    ):
        raise PaperLifecycleError("lifecycle identity, time, or status is inconsistent")
    if lifecycle.status is PaperOrderStatus.PENDING:
        if lifecycle.fill is not None:
            raise PaperLifecycleError("PENDING lifecycle cannot contain a fill")
        return
    fill = lifecycle.fill
    if fill is None or lifecycle.last_observed_at != fill.fill_at:
        raise PaperLifecycleError("FILLED lifecycle must contain its sole observed fill")
    try:
        quote = price_simulated_market_fill(
            side=(
                SimulatedExecutionSide.BUY
                if authority.side is EntrySide.BUY
                else SimulatedExecutionSide.SELL
            ),
            quantity=authority.quantity,
            reference_price=fill.reference_price,
            costs=costs,
        )
    except (TypeError, ValueError) as error:
        raise PaperLifecycleError("fill pricing audit is invalid") from error
    if (
        fill.intent_identity != intent.identity
        or fill.decision_identity != intent.decision_identity
        or fill.source_id != intent.source_id
        or fill.symbol != intent.symbol
        or fill.side is not authority.side
        or fill.quantity != authority.quantity
        or fill.authorized_at != authority.authorized_at
        or fill.eligible_at != lifecycle.eligible_at
        or fill.fill_at < lifecycle.eligible_at
        or fill.spread_bps != costs.spread_bps
        or fill.slippage_bps != costs.slippage_bps
        or fill.fee_bps != costs.fee_bps
        or fill.fill_price != quote.fill_price
        or fill.fee_quote != quote.fee_quote
        or fill.risk_policy_version != authority.risk_policy_version
        or fill.safety_policy_version != authority.safety_policy_version
    ):
        raise PaperLifecycleError("fill does not exactly preserve intent authority")


def _snapshot_authorization(intent: EntryOrderIntent) -> PaperAuthorizationSnapshot:
    """Copy every execution-relevant value without retaining mutable identity aliases."""

    return PaperAuthorizationSnapshot(
        source_id_value=intent.source_id.value,
        symbol=TradingSymbol(intent.symbol.base, intent.symbol.quote),
        observed_at=intent.observed_at,
        strategy_id=intent.strategy_id,
        strategy_version=intent.strategy_version,
        intent_identity_decision=_identity_fingerprint(intent.identity.decision_identity),
        decision_identity=_identity_fingerprint(intent.decision_identity),
        intent_purpose=intent.identity.purpose,
        intent_schema_version=intent.identity.intent_schema_version,
        authorized_at=intent.authorized_at,
        risk_policy_version=intent.risk_policy_version,
        safety_policy_version=intent.safety_policy_version,
        side=intent.side,
        quantity=intent.quantity,
        approved_notional_quote=intent.approved_notional_quote,
        approved_risk_quote=intent.approved_risk_quote,
        stop_basis=intent.stop_basis,
        reference_price=intent.reference_price,
        quote_currency=intent.quote_currency,
        authorized_for_execution=intent.authorized_for_execution,
    )


def _identity_fingerprint(
    identity: DecisionIdentity,
) -> tuple[str, str, str, datetime, str, str]:
    return (
        identity.source_id.value,
        identity.symbol.base,
        identity.symbol.quote,
        identity.observed_at,
        identity.strategy_id,
        identity.strategy_version,
    )
