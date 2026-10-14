"""Causal Paper execution for authoritative full-close exit purposes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import (
    EntryOrderIntentIdentity,
    ExitIntentPurpose,
    ExitOrderIntent,
    ExitOrderIntentIdentity,
    ExitSide,
    exit_order_intent_fingerprint,
    exit_order_intent_is_authoritative,
)
from adaptive_trading_bot.orders.exit_intent import _copy_authoritative_exit_intent
from adaptive_trading_bot.strategies.core import SourceIdentity

if TYPE_CHECKING:
    from adaptive_trading_bot.paper.execution import PaperExecutionObservation

_PAPER_EXIT_SCHEMA_VERSION = "2.0.0"
_PAPER_EXIT_LIFECYCLE_TOKEN = object()


class PaperProtectiveExitExecutionError(ValueError):
    """An exit lifecycle, observation, or fill failed closed."""


class PaperExitOrderStatus(StrEnum):
    """States in the causal protective-exit lifecycle."""

    PENDING = "PENDING"
    FILLED = "FILLED"


@dataclass(frozen=True, slots=True)
class PaperExitAuthorizationSnapshot:
    """Detached values permanently accepted when exit execution starts."""

    fingerprint: tuple[object, ...]
    exit_intent_identity: ExitOrderIntentIdentity
    originating_entry_intent_identity: EntryOrderIntentIdentity
    originating_decision_identity: DecisionIdentity
    source_id_value: str
    symbol: TradingSymbol
    trigger_observed_at: datetime
    authorized_at: datetime
    side: ExitSide
    quantity: Decimal
    stop_basis: Decimal
    trigger_reference_price: Decimal
    risk_policy_version: str
    safety_policy_version: str
    originating_accounting_fill_id: str | None
    recovery_policy_version: str | None
    authorized_for_execution: bool


@dataclass(frozen=True, slots=True)
class PaperExitMarketFill:
    """One immutable full-close exit fill with complete authority provenance."""

    exit_intent_identity: ExitOrderIntentIdentity
    originating_entry_intent_identity: EntryOrderIntentIdentity
    originating_decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    side: ExitSide
    quantity: Decimal
    authorized_at: datetime
    eligible_at: datetime
    fill_at: datetime
    trigger_observed_at: datetime
    trigger_reference_price: Decimal
    stop_basis: Decimal
    reference_price: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    fill_price: Decimal
    fee_bps: Decimal
    fee_quote: Decimal
    risk_policy_version: str
    safety_policy_version: str
    originating_accounting_fill_id: str | None
    recovery_policy_version: str | None
    simulation_schema_version: str = field(init=False, default=_PAPER_EXIT_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True, init=False)
class PaperProtectiveExitLifecycle:
    """Non-forgeable execution state bound to one copied exit authority."""

    intent: ExitOrderIntent
    accepted_authority: PaperExitAuthorizationSnapshot
    costs: SimulatedExecutionCostConfiguration
    eligible_at: datetime
    last_observed_at: datetime | None
    status: PaperExitOrderStatus
    fill: PaperExitMarketFill | None
    _authorization_seal: object = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        token = kwargs.pop("_paper_exit_lifecycle_token", None)
        if args or kwargs or token is not _PAPER_EXIT_LIFECYCLE_TOKEN:
            raise TypeError("PaperProtectiveExitLifecycle can only be created by its simulator")
        return object.__new__(cls)

    def __init__(self, *, _paper_exit_lifecycle_token: object) -> None:
        del _paper_exit_lifecycle_token


class PaperProtectiveExitSimulator:
    """Start and causally advance one authoritative protective exit."""

    __slots__ = ()

    def start(
        self,
        intent: ExitOrderIntent,
        costs: SimulatedExecutionCostConfiguration,
    ) -> PaperProtectiveExitLifecycle:
        """Copy and seal exact exit authority before any execution observation."""

        if type(intent) is not ExitOrderIntent or not exit_order_intent_is_authoritative(intent):
            raise PaperProtectiveExitExecutionError("exit intent is not authoritative")
        if not isinstance(costs, SimulatedExecutionCostConfiguration):
            raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
        costs.validate()
        owned = _copy_authoritative_exit_intent(intent)
        return _create_lifecycle(
            intent=owned,
            accepted_authority=_snapshot_authority(owned),
            costs=_copy_costs(costs),
            last_observed_at=None,
            status=PaperExitOrderStatus.PENDING,
            fill=None,
        )

    def clone(self, lifecycle: PaperProtectiveExitLifecycle) -> PaperProtectiveExitLifecycle:
        """Return an independent authoritative lifecycle for atomic staging."""

        if type(lifecycle) is not PaperProtectiveExitLifecycle:
            raise TypeError("lifecycle must be a PaperProtectiveExitLifecycle")
        _validate_lifecycle(lifecycle)
        intent = _copy_authoritative_exit_intent(lifecycle.intent)
        return _create_lifecycle(
            intent=intent,
            accepted_authority=_snapshot_authority(intent),
            costs=_copy_costs(lifecycle.costs),
            last_observed_at=lifecycle.last_observed_at,
            status=lifecycle.status,
            fill=None if lifecycle.fill is None else _copy_fill(lifecycle.fill),
        )

    def observe(
        self,
        lifecycle: PaperProtectiveExitLifecycle,
        observation: PaperExecutionObservation,
    ) -> PaperProtectiveExitLifecycle:
        """Advance only from the explicit observation supplied by the coordinator."""

        from adaptive_trading_bot.paper.execution import PaperExecutionObservation

        if type(lifecycle) is not PaperProtectiveExitLifecycle:
            raise TypeError("lifecycle must be a PaperProtectiveExitLifecycle")
        if not isinstance(observation, PaperExecutionObservation):
            raise TypeError("observation must be a PaperExecutionObservation")
        _validate_lifecycle(lifecycle)
        try:
            observation.validate()
        except (TypeError, ValueError) as error:
            raise PaperProtectiveExitExecutionError("exit observation is invalid") from error
        authority = lifecycle.accepted_authority
        if (
            observation.source_id.value != authority.source_id_value
            or observation.symbol != authority.symbol
        ):
            raise PaperProtectiveExitExecutionError(
                "exit observation identity does not match authority"
            )
        if observation.observed_at < authority.authorized_at or (
            authority.exit_intent_identity.purpose
            is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
            and observation.observed_at == authority.authorized_at
        ):
            raise PaperProtectiveExitExecutionError("exit observation precedes authorization")
        if (
            lifecycle.last_observed_at is not None
            and observation.observed_at <= lifecycle.last_observed_at
        ):
            raise PaperProtectiveExitExecutionError("exit observation time must strictly advance")
        if lifecycle.status is PaperExitOrderStatus.FILLED:
            return lifecycle
        if observation.observed_at < lifecycle.eligible_at:
            return _create_lifecycle(
                intent=lifecycle.intent,
                accepted_authority=authority,
                costs=lifecycle.costs,
                last_observed_at=observation.observed_at,
                status=PaperExitOrderStatus.PENDING,
                fill=None,
            )

        side = (
            SimulatedExecutionSide.BUY
            if authority.side is ExitSide.BUY
            else SimulatedExecutionSide.SELL
        )
        quote = price_simulated_market_fill(
            side=side,
            quantity=authority.quantity,
            reference_price=observation.reference_price,
            costs=lifecycle.costs,
        )
        fill = PaperExitMarketFill(
            exit_intent_identity=authority.exit_intent_identity,
            originating_entry_intent_identity=authority.originating_entry_intent_identity,
            originating_decision_identity=authority.originating_decision_identity,
            source_id=deepcopy(lifecycle.intent.source_id),
            symbol=TradingSymbol(authority.symbol.base, authority.symbol.quote),
            side=authority.side,
            quantity=authority.quantity,
            authorized_at=authority.authorized_at,
            eligible_at=lifecycle.eligible_at,
            fill_at=observation.observed_at,
            trigger_observed_at=authority.trigger_observed_at,
            trigger_reference_price=authority.trigger_reference_price,
            stop_basis=authority.stop_basis,
            reference_price=observation.reference_price,
            spread_bps=lifecycle.costs.spread_bps,
            slippage_bps=lifecycle.costs.slippage_bps,
            fill_price=quote.fill_price,
            fee_bps=lifecycle.costs.fee_bps,
            fee_quote=quote.fee_quote,
            risk_policy_version=authority.risk_policy_version,
            safety_policy_version=authority.safety_policy_version,
            originating_accounting_fill_id=authority.originating_accounting_fill_id,
            recovery_policy_version=authority.recovery_policy_version,
        )
        return _create_lifecycle(
            intent=lifecycle.intent,
            accepted_authority=authority,
            costs=lifecycle.costs,
            last_observed_at=observation.observed_at,
            status=PaperExitOrderStatus.FILLED,
            fill=fill,
        )


def _create_lifecycle(
    *,
    intent: ExitOrderIntent,
    accepted_authority: PaperExitAuthorizationSnapshot,
    costs: SimulatedExecutionCostConfiguration,
    last_observed_at: datetime | None,
    status: PaperExitOrderStatus,
    fill: PaperExitMarketFill | None,
) -> PaperProtectiveExitLifecycle:
    lifecycle = PaperProtectiveExitLifecycle(
        _paper_exit_lifecycle_token=_PAPER_EXIT_LIFECYCLE_TOKEN
    )
    values: tuple[tuple[str, object], ...] = (
        ("intent", _copy_authoritative_exit_intent(intent)),
        ("accepted_authority", accepted_authority),
        ("costs", _copy_costs(costs)),
        ("eligible_at", accepted_authority.authorized_at + costs.latency),
        ("last_observed_at", last_observed_at),
        ("status", status),
        ("fill", fill),
        ("_authorization_seal", _PAPER_EXIT_LIFECYCLE_TOKEN),
    )
    for name, value in values:
        object.__setattr__(lifecycle, name, value)
    _validate_lifecycle(lifecycle)
    return lifecycle


def _validate_lifecycle(lifecycle: PaperProtectiveExitLifecycle) -> None:
    if lifecycle._authorization_seal is not _PAPER_EXIT_LIFECYCLE_TOKEN:
        raise PaperProtectiveExitExecutionError("exit lifecycle seal is invalid")
    intent = lifecycle.intent
    authority = lifecycle.accepted_authority
    try:
        lifecycle.costs.validate()
    except (TypeError, ValueError) as error:
        raise PaperProtectiveExitExecutionError("exit lifecycle costs are invalid") from error
    if (
        not exit_order_intent_is_authoritative(intent)
        or _snapshot_authority(intent) != authority
        or lifecycle.eligible_at != authority.authorized_at + lifecycle.costs.latency
        or lifecycle.status not in {PaperExitOrderStatus.PENDING, PaperExitOrderStatus.FILLED}
        or (
            lifecycle.last_observed_at is not None
            and lifecycle.last_observed_at < authority.authorized_at
        )
    ):
        raise PaperProtectiveExitExecutionError("exit lifecycle authority drifted")
    if lifecycle.status is PaperExitOrderStatus.PENDING:
        if lifecycle.fill is not None:
            raise PaperProtectiveExitExecutionError("pending exit cannot contain a fill")
        return
    fill = lifecycle.fill
    if fill is None or lifecycle.last_observed_at != fill.fill_at:
        raise PaperProtectiveExitExecutionError("filled exit must contain its observed fill")
    quote = price_simulated_market_fill(
        side=(
            SimulatedExecutionSide.BUY
            if authority.side is ExitSide.BUY
            else SimulatedExecutionSide.SELL
        ),
        quantity=authority.quantity,
        reference_price=fill.reference_price,
        costs=lifecycle.costs,
    )
    if (
        fill.exit_intent_identity != authority.exit_intent_identity
        or fill.originating_entry_intent_identity != authority.originating_entry_intent_identity
        or fill.originating_decision_identity != authority.originating_decision_identity
        or fill.source_id.value != authority.source_id_value
        or fill.symbol != authority.symbol
        or fill.side is not authority.side
        or fill.quantity != authority.quantity
        or fill.authorized_at != authority.authorized_at
        or fill.eligible_at != lifecycle.eligible_at
        or fill.fill_at < lifecycle.eligible_at
        or (
            authority.exit_intent_identity.purpose
            is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
            and fill.fill_at <= authority.authorized_at
        )
        or fill.trigger_observed_at != authority.trigger_observed_at
        or fill.trigger_reference_price != authority.trigger_reference_price
        or fill.stop_basis != authority.stop_basis
        or fill.spread_bps != lifecycle.costs.spread_bps
        or fill.slippage_bps != lifecycle.costs.slippage_bps
        or fill.fill_price != quote.fill_price
        or fill.fee_bps != lifecycle.costs.fee_bps
        or fill.fee_quote != quote.fee_quote
        or fill.risk_policy_version != authority.risk_policy_version
        or fill.safety_policy_version != authority.safety_policy_version
        or fill.originating_accounting_fill_id != authority.originating_accounting_fill_id
        or fill.recovery_policy_version != authority.recovery_policy_version
        or fill.simulation_schema_version != _PAPER_EXIT_SCHEMA_VERSION
    ):
        raise PaperProtectiveExitExecutionError("exit fill diverged from accepted authority")


def _snapshot_authority(intent: ExitOrderIntent) -> PaperExitAuthorizationSnapshot:
    decision = intent.originating_decision_identity
    copied_decision = DecisionIdentity(
        deepcopy(decision.source_id),
        TradingSymbol(decision.symbol.base, decision.symbol.quote),
        decision.observed_at,
        decision.strategy_id,
        decision.strategy_version,
    )
    entry_identity = EntryOrderIntentIdentity(copied_decision)
    exit_identity = ExitOrderIntentIdentity(
        entry_identity, intent.trigger_observed_at, intent.identity.purpose
    )
    return PaperExitAuthorizationSnapshot(
        tuple(exit_order_intent_fingerprint(intent)),
        exit_identity,
        entry_identity,
        copied_decision,
        intent.source_id.value,
        TradingSymbol(intent.symbol.base, intent.symbol.quote),
        intent.trigger_observed_at,
        intent.authorized_at,
        intent.side,
        intent.quantity,
        intent.stop_basis,
        intent.trigger_reference_price,
        intent.risk_policy_version,
        intent.safety_policy_version,
        intent.originating_accounting_fill_id,
        intent.recovery_policy_version,
        intent.authorized_for_execution,
    )


def _copy_costs(
    costs: SimulatedExecutionCostConfiguration,
) -> SimulatedExecutionCostConfiguration:
    return SimulatedExecutionCostConfiguration(
        costs.spread_bps, costs.slippage_bps, costs.fee_bps, costs.latency
    )


def _copy_fill(fill: PaperExitMarketFill) -> PaperExitMarketFill:
    decision = fill.originating_decision_identity
    copied_decision = DecisionIdentity(
        deepcopy(decision.source_id),
        TradingSymbol(decision.symbol.base, decision.symbol.quote),
        decision.observed_at,
        decision.strategy_id,
        decision.strategy_version,
    )
    entry_identity = EntryOrderIntentIdentity(copied_decision)
    return PaperExitMarketFill(
        ExitOrderIntentIdentity(
            entry_identity, fill.trigger_observed_at, fill.exit_intent_identity.purpose
        ),
        entry_identity,
        copied_decision,
        copied_decision.source_id,
        copied_decision.symbol,
        fill.side,
        fill.quantity,
        fill.authorized_at,
        fill.eligible_at,
        fill.fill_at,
        fill.trigger_observed_at,
        fill.trigger_reference_price,
        fill.stop_basis,
        fill.reference_price,
        fill.spread_bps,
        fill.slippage_bps,
        fill.fill_price,
        fill.fee_bps,
        fill.fee_quote,
        fill.risk_policy_version,
        fill.safety_policy_version,
        fill.originating_accounting_fill_id,
        fill.recovery_policy_version,
    )
