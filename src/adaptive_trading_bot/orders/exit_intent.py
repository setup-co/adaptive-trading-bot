"""Non-forgeable, environment-neutral protective exit authority.

Exit intents are authorization records, not fills or exchange orders.  Their only
supported creation path is the private factory used after the Paper coordinator has
validated its own open-position and accounting state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders.intent import EntryOrderIntentIdentity
from adaptive_trading_bot.strategies.core import SourceIdentity

_EXIT_INTENT_SCHEMA_VERSION = "1.0.0"
_EXIT_INTENT_AUTHORIZATION_TOKEN = object()


class ExitIntentPurpose(StrEnum):
    """The sole risk-reducing exit authority supported in V1."""

    PROTECTIVE_STOP_FULL_CLOSE = "PROTECTIVE_STOP_FULL_CLOSE"


class ExitSide(StrEnum):
    """Direction required to close an existing position."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class ExitOrderIntentIdentity:
    """Deterministic identity rooted in one entry and one observed stop trigger."""

    originating_entry_intent_identity: EntryOrderIntentIdentity
    trigger_observed_at: datetime
    purpose: ExitIntentPurpose = field(
        init=False, default=ExitIntentPurpose.PROTECTIVE_STOP_FULL_CLOSE
    )
    exit_intent_schema_version: str = field(init=False, default=_EXIT_INTENT_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.originating_entry_intent_identity, EntryOrderIntentIdentity):
            raise TypeError("originating entry intent identity is invalid")
        object.__setattr__(
            self,
            "trigger_observed_at",
            require_utc(self.trigger_observed_at, field="exit trigger_observed_at"),
        )


@dataclass(frozen=True, slots=True, init=False)
class ExitOrderIntent:
    """Full-close authority that can only be minted by its internal authorizer."""

    identity: ExitOrderIntentIdentity
    originating_entry_intent_identity: EntryOrderIntentIdentity
    originating_decision_identity: DecisionIdentity
    source_id: SourceIdentity
    symbol: TradingSymbol
    trigger_observed_at: datetime
    authorized_at: datetime
    side: ExitSide
    quantity: Decimal
    stop_basis: Decimal
    trigger_reference_price: Decimal
    risk_policy_version: str
    safety_policy_version: str
    authorized_for_execution: bool = field(init=False, default=True)
    _authority_fingerprint: tuple[object, ...] = field(repr=False, compare=False)
    _authorization_seal: object = field(repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        token = kwargs.pop("_exit_intent_authorization_token", None)
        if args or kwargs or token is not _EXIT_INTENT_AUTHORIZATION_TOKEN:
            raise TypeError("ExitOrderIntent can only be created by its authorizer")
        return object.__new__(cls)

    def __init__(self, *, _exit_intent_authorization_token: object) -> None:
        del _exit_intent_authorization_token


def exit_order_intent_fingerprint(intent: ExitOrderIntent) -> tuple[object, ...]:
    """Return the primitive authority fingerprint used by future consumers."""

    if type(intent) is not ExitOrderIntent:
        raise TypeError("intent must be an ExitOrderIntent")
    return _current_fingerprint(intent)


def exit_order_intent_is_authoritative(intent: object) -> bool:
    """Revalidate the seal, provenance, exact fields, and full-close authority."""

    if type(intent) is not ExitOrderIntent:
        return False
    try:
        assert isinstance(intent, ExitOrderIntent)
        identity = intent.identity
        decision = intent.originating_decision_identity
        if (
            intent._authorization_seal is not _EXIT_INTENT_AUTHORIZATION_TOKEN
            or not isinstance(identity, ExitOrderIntentIdentity)
            or identity.originating_entry_intent_identity
            != intent.originating_entry_intent_identity
            or identity.trigger_observed_at != intent.trigger_observed_at
            or intent.originating_entry_intent_identity.decision_identity != decision
            or decision.source_id != intent.source_id
            or decision.symbol != intent.symbol
            or intent.authorized_at != intent.trigger_observed_at
            or not isinstance(intent.side, ExitSide)
            or not _positive_finite(intent.quantity)
            or not _positive_finite(intent.stop_basis)
            or not _positive_finite(intent.trigger_reference_price)
            or not _canonical_version(intent.risk_policy_version)
            or not _canonical_version(intent.safety_policy_version)
            or intent.authorized_for_execution is not True
        ):
            return False
        require_utc(intent.trigger_observed_at, field="exit trigger_observed_at")
        return intent._authority_fingerprint == _current_fingerprint(intent)
    except (AttributeError, TypeError, ValueError):
        return False


def _authorize_protective_stop_full_close(
    *,
    originating_entry_intent_identity: EntryOrderIntentIdentity,
    originating_decision_identity: DecisionIdentity,
    source_id: SourceIdentity,
    symbol: TradingSymbol,
    trigger_observed_at: datetime,
    side: ExitSide,
    quantity: Decimal,
    stop_basis: Decimal,
    trigger_reference_price: Decimal,
    risk_policy_version: str,
    safety_policy_version: str,
) -> ExitOrderIntent:
    """Mint authority from values already validated as coordinator-owned state."""

    decision = _copy_decision_identity(originating_decision_identity)
    entry_identity = EntryOrderIntentIdentity(decision)
    if (
        originating_entry_intent_identity != entry_identity
        or source_id != decision.source_id
        or symbol != decision.symbol
    ):
        raise ValueError("exit provenance diverged from originating entry authority")
    trigger_at = require_utc(trigger_observed_at, field="exit trigger_observed_at")
    if not isinstance(side, ExitSide):
        raise TypeError("exit side must be an ExitSide")
    if not all(
        _positive_finite(value) for value in (quantity, stop_basis, trigger_reference_price)
    ):
        raise ValueError("exit prices and quantity must be finite and positive")
    if not _canonical_version(risk_policy_version) or not _canonical_version(safety_policy_version):
        raise ValueError("exit policy versions must be canonical non-empty strings")

    intent = ExitOrderIntent(_exit_intent_authorization_token=_EXIT_INTENT_AUTHORIZATION_TOKEN)
    identity = ExitOrderIntentIdentity(entry_identity, trigger_at)
    values: tuple[tuple[str, object], ...] = (
        ("identity", identity),
        ("originating_entry_intent_identity", entry_identity),
        ("originating_decision_identity", decision),
        ("source_id", deepcopy(decision.source_id)),
        ("symbol", TradingSymbol(decision.symbol.base, decision.symbol.quote)),
        ("trigger_observed_at", trigger_at),
        ("authorized_at", trigger_at),
        ("side", side),
        ("quantity", quantity),
        ("stop_basis", stop_basis),
        ("trigger_reference_price", trigger_reference_price),
        ("risk_policy_version", risk_policy_version),
        ("safety_policy_version", safety_policy_version),
        ("authorized_for_execution", True),
        ("_authorization_seal", _EXIT_INTENT_AUTHORIZATION_TOKEN),
    )
    for name, value in values:
        object.__setattr__(intent, name, value)
    object.__setattr__(intent, "_authority_fingerprint", _current_fingerprint(intent))
    if not exit_order_intent_is_authoritative(intent):
        raise ValueError("created exit intent failed authority validation")
    return intent


def _copy_authoritative_exit_intent(intent: ExitOrderIntent) -> ExitOrderIntent:
    """Return a detached sealed copy after revalidating existing authority."""

    if not exit_order_intent_is_authoritative(intent):
        raise ValueError("exit intent is not authoritative")
    return _authorize_protective_stop_full_close(
        originating_entry_intent_identity=intent.originating_entry_intent_identity,
        originating_decision_identity=intent.originating_decision_identity,
        source_id=intent.source_id,
        symbol=intent.symbol,
        trigger_observed_at=intent.trigger_observed_at,
        side=intent.side,
        quantity=intent.quantity,
        stop_basis=intent.stop_basis,
        trigger_reference_price=intent.trigger_reference_price,
        risk_policy_version=intent.risk_policy_version,
        safety_policy_version=intent.safety_policy_version,
    )


def _current_fingerprint(intent: ExitOrderIntent) -> tuple[object, ...]:
    decision = intent.originating_decision_identity
    identity = intent.identity
    return (
        decision.source_id.value,
        decision.symbol.base,
        decision.symbol.quote,
        decision.observed_at,
        decision.strategy_id,
        decision.strategy_version,
        identity.originating_entry_intent_identity.decision_identity.sort_key,
        identity.trigger_observed_at,
        identity.purpose,
        identity.exit_intent_schema_version,
        intent.source_id.value,
        intent.symbol.base,
        intent.symbol.quote,
        intent.trigger_observed_at,
        intent.authorized_at,
        intent.side,
        intent.quantity,
        intent.stop_basis,
        intent.trigger_reference_price,
        intent.risk_policy_version,
        intent.safety_policy_version,
        intent.authorized_for_execution,
    )


def _copy_decision_identity(identity: DecisionIdentity) -> DecisionIdentity:
    if not isinstance(identity, DecisionIdentity):
        raise TypeError("originating decision identity is invalid")
    return DecisionIdentity(
        deepcopy(identity.source_id),
        TradingSymbol(identity.symbol.base, identity.symbol.quote),
        identity.observed_at,
        identity.strategy_id,
        identity.strategy_version,
    )


def _positive_finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _canonical_version(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()
