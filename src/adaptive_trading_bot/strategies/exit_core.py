"""Environment-neutral position-exit evaluation contracts.

This module defines strategy-owned observations only. It has no order, fill, accounting,
protective-stop, or execution authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from hashlib import sha256
from hmac import compare_digest
from json import dumps
from typing import Protocol, runtime_checkable

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.domain.entry_chronology import CAUSAL_ENTRY_CHRONOLOGY_V2
from adaptive_trading_bot.journal.decision import DecisionIdentity
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluationContext,
)

_EXIT_CONTEXT_SCHEMA_VERSION = "2.0.0"
_EXIT_EVALUATION_SCHEMA_VERSION = "2.0.0"
_EXIT_INTEGRITY_SEAL_VERSION = "2.0.0"
_HOLD_ONLY_EXIT_POLICY_VERSION = "hold-only-exit-v1"


class PositionExitValidationError(ValueError):
    """Exit context or evaluation authority is malformed or contradictory."""


class PositionExitAction(StrEnum):
    """Position-management actions, separate from entry candidate actions."""

    HOLD = "HOLD"
    CLOSE_FULL = "CLOSE_FULL"


class PositionSide(StrEnum):
    """Economic side of one currently open position."""

    LONG = "LONG"
    SHORT = "SHORT"


class PositionLifecycleStatus(StrEnum):
    """Lifecycle fact supplied at the evaluation boundary."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class HoldOnlyExitReasonCode(StrEnum):
    """Typed explanation emitted by the fail-safe baseline."""

    NO_DISCRETIONARY_EXIT_RULE = "NO_DISCRETIONARY_EXIT_RULE"


@dataclass(frozen=True, slots=True)
class HoldOnlyExitRuleAudit:
    """Explicit proof that no active exit hypothesis was evaluated."""

    active_exit_hypothesis: bool
    protective_stop_delegated: bool
    policy_version: str

    def __post_init__(self) -> None:
        if self.active_exit_hypothesis is not False:
            raise PositionExitValidationError("HOLD-only policy cannot expose an active hypothesis")
        if self.protective_stop_delegated is not True:
            raise PositionExitValidationError("protective stops must remain independently owned")
        if self.policy_version != _HOLD_ONLY_EXIT_POLICY_VERSION:
            raise PositionExitValidationError("HOLD-only audit policy version mismatch")


@dataclass(frozen=True, slots=True)
class OpenPositionIdentity:
    """Environment-neutral identity rooted in accepted entry provenance."""

    originating_decision_identity: DecisionIdentity
    accounting_entry_fill_id: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.originating_decision_identity, DecisionIdentity):
            raise TypeError("originating_decision_identity must be a DecisionIdentity")
        _canonical_text("accounting_entry_fill_id", self.accounting_entry_fill_id)

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, str, str]:
        return (*self.originating_decision_identity.sort_key, self.accounting_entry_fill_id)


@dataclass(frozen=True, slots=True)
class PositionExitEvaluationContext:
    """Sealed causal facts observable for one open position at one instant."""

    position_identity: OpenPositionIdentity
    originating_candidate_action: CandidateAction
    strategy_id: str
    strategy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    position_side: PositionSide
    entry_fill_at: datetime
    entry_fill_price: Decimal
    open_quantity: Decimal
    invalidation_reference_price: Decimal
    current_market_context: StrategyEvaluationContext
    observed_at: datetime
    lifecycle_status: PositionLifecycleStatus
    risk_policy_version: str
    safety_policy_version: str
    entry_chronology_policy_version: str = field(init=False, default=CAUSAL_ENTRY_CHRONOLOGY_V2)
    context_schema_version: str = field(init=False, default=_EXIT_CONTEXT_SCHEMA_VERSION)
    context_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "context_integrity_seal", _integrity_seal("context", self))

    def validate(self) -> None:
        seal = self.context_integrity_seal
        if not isinstance(seal, str) or not compare_digest(seal, _integrity_seal("context", self)):
            raise PositionExitValidationError("exit context integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.context_schema_version != _EXIT_CONTEXT_SCHEMA_VERSION:
            raise PositionExitValidationError("exit context schema version mismatch")
        if self.entry_chronology_policy_version != CAUSAL_ENTRY_CHRONOLOGY_V2:
            raise PositionExitValidationError("exit context entry chronology policy mismatch")
        if not isinstance(self.position_identity, OpenPositionIdentity):
            raise TypeError("position_identity must be an OpenPositionIdentity")
        self.position_identity.validate()
        identity = self.position_identity.originating_decision_identity
        if self.originating_candidate_action not in {
            CandidateAction.LONG_CANDIDATE,
            CandidateAction.SHORT_CANDIDATE,
        }:
            raise PositionExitValidationError(
                "open position must originate from an entry candidate"
            )
        _canonical_text("strategy_id", self.strategy_id)
        _canonical_text("strategy_version", self.strategy_version)
        if not isinstance(self.source_id, SourceIdentity):
            raise TypeError("source_id must satisfy SourceIdentity")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("symbol must be a TradingSymbol")
        _canonical_text("source_id", self.source_id.value)
        if (
            self.strategy_id != identity.strategy_id
            or self.strategy_version != identity.strategy_version
            or self.source_id != identity.source_id
            or self.symbol != identity.symbol
        ):
            raise PositionExitValidationError("open-position decision provenance is inconsistent")
        if not isinstance(self.position_side, PositionSide):
            raise PositionExitValidationError("position side is invalid")
        expected_side = (
            PositionSide.LONG
            if self.originating_candidate_action is CandidateAction.LONG_CANDIDATE
            else PositionSide.SHORT
        )
        if self.position_side is not expected_side:
            raise PositionExitValidationError("position side contradicts originating candidate")
        decision_at = require_utc(identity.observed_at, field="originating decision observed_at")
        entry_fill_at = require_utc(self.entry_fill_at, field="position entry_fill_at")
        observed_at = require_utc(self.observed_at, field="exit evaluation observed_at")
        if not decision_at <= entry_fill_at < observed_at:
            raise PositionExitValidationError("exit evaluation entry chronology is invalid")
        for name, value in (
            ("entry_fill_price", self.entry_fill_price),
            ("open_quantity", self.open_quantity),
            ("invalidation_reference_price", self.invalidation_reference_price),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise PositionExitValidationError(f"{name} must be a positive finite Decimal")
        if (
            self.position_side is PositionSide.LONG
            and self.invalidation_reference_price >= self.entry_fill_price
        ) or (
            self.position_side is PositionSide.SHORT
            and self.invalidation_reference_price <= self.entry_fill_price
        ):
            raise PositionExitValidationError("invalidation reference contradicts position side")
        if not isinstance(self.current_market_context, StrategyEvaluationContext):
            raise TypeError("current_market_context must be a StrategyEvaluationContext")
        self.current_market_context.validate()
        if (
            self.current_market_context.source_id != self.source_id
            or self.current_market_context.symbol != self.symbol
            or self.current_market_context.observed_at != observed_at
        ):
            raise PositionExitValidationError("current market context provenance is inconsistent")
        if self.lifecycle_status is not PositionLifecycleStatus.OPEN:
            raise PositionExitValidationError("only an open position may be evaluated")
        _canonical_text("risk_policy_version", self.risk_policy_version)
        _canonical_text("safety_policy_version", self.safety_policy_version)


@dataclass(frozen=True, slots=True)
class PositionExitEvaluationIdentity:
    """Stable identity for one position/policy evaluation instant."""

    position_identity: OpenPositionIdentity
    observed_at: datetime
    exit_policy_version: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.position_identity, OpenPositionIdentity):
            raise TypeError("position_identity must be an OpenPositionIdentity")
        self.position_identity.validate()
        require_utc(self.observed_at, field="exit evaluation identity observed_at")
        _canonical_text("exit_policy_version", self.exit_policy_version)

    @property
    def sort_key(self) -> tuple[datetime, datetime, str, str, str, str, str, str]:
        return (
            self.observed_at,
            *self.position_identity.sort_key,
            self.exit_policy_version,
        )


@dataclass(frozen=True, slots=True)
class PositionExitEvaluation:
    """Sealed strategy observation that is never execution authority."""

    identity: PositionExitEvaluationIdentity
    originating_decision_identity: DecisionIdentity
    strategy_id: str
    strategy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    position_side: PositionSide
    action: PositionExitAction
    reasons_for: tuple[object, ...]
    reasons_against: tuple[object, ...]
    rule_audit: object
    exit_reference_price: Decimal | None
    evaluated_context_integrity_seal: str
    exit_policy_version: str
    is_execution_authority: bool = field(init=False, default=False)
    evaluation_schema_version: str = field(init=False, default=_EXIT_EVALUATION_SCHEMA_VERSION)
    evaluation_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "evaluation_integrity_seal", _integrity_seal("evaluation", self))

    @classmethod
    def create(
        cls,
        context: PositionExitEvaluationContext,
        *,
        action: PositionExitAction,
        reasons_for: tuple[object, ...],
        reasons_against: tuple[object, ...],
        rule_audit: object,
        exit_policy_version: str,
        exit_reference_price: Decimal | None = None,
    ) -> PositionExitEvaluation:
        """Create a detached evaluation bound to one validated context seal."""

        if not isinstance(context, PositionExitEvaluationContext):
            raise TypeError("context must be a PositionExitEvaluationContext")
        context.validate()
        owned = deepcopy(context)
        return cls(
            identity=PositionExitEvaluationIdentity(
                deepcopy(owned.position_identity), owned.observed_at, exit_policy_version
            ),
            originating_decision_identity=deepcopy(
                owned.position_identity.originating_decision_identity
            ),
            strategy_id=owned.strategy_id,
            strategy_version=owned.strategy_version,
            source_id=deepcopy(owned.source_id),
            symbol=deepcopy(owned.symbol),
            observed_at=owned.observed_at,
            position_side=owned.position_side,
            action=action,
            reasons_for=deepcopy(reasons_for),
            reasons_against=deepcopy(reasons_against),
            rule_audit=deepcopy(rule_audit),
            exit_reference_price=exit_reference_price,
            evaluated_context_integrity_seal=owned.context_integrity_seal,
            exit_policy_version=exit_policy_version,
        )

    def validate(self) -> None:
        seal = self.evaluation_integrity_seal
        if not isinstance(seal, str) or not compare_digest(
            seal, _integrity_seal("evaluation", self)
        ):
            raise PositionExitValidationError("exit evaluation integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.evaluation_schema_version != _EXIT_EVALUATION_SCHEMA_VERSION:
            raise PositionExitValidationError("exit evaluation schema version mismatch")
        if not isinstance(self.identity, PositionExitEvaluationIdentity):
            raise TypeError("evaluation identity is invalid")
        self.identity.validate()
        if (
            self.identity.position_identity.originating_decision_identity
            != self.originating_decision_identity
            or self.identity.observed_at != self.observed_at
            or self.identity.exit_policy_version != self.exit_policy_version
        ):
            raise PositionExitValidationError("exit evaluation identity is inconsistent")
        identity = self.originating_decision_identity
        if not isinstance(self.source_id, SourceIdentity):
            raise TypeError("evaluation source_id must satisfy SourceIdentity")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("evaluation symbol must be a TradingSymbol")
        if (
            self.strategy_id != identity.strategy_id
            or self.strategy_version != identity.strategy_version
            or self.source_id != identity.source_id
            or self.symbol != identity.symbol
        ):
            raise PositionExitValidationError("exit evaluation provenance is inconsistent")
        require_utc(self.observed_at, field="exit evaluation observed_at")
        if not isinstance(self.position_side, PositionSide):
            raise PositionExitValidationError("exit evaluation position side is invalid")
        if not isinstance(self.action, PositionExitAction):
            raise PositionExitValidationError("exit evaluation action is invalid")
        if not isinstance(self.reasons_for, tuple) or not isinstance(self.reasons_against, tuple):
            raise TypeError("exit evaluation reasons must be immutable tuples")
        if not self.reasons_for:
            raise PositionExitValidationError("exit evaluation must explain its action")
        if self.rule_audit is None:
            raise PositionExitValidationError("exit evaluation rule audit is required")
        if self.exit_reference_price is not None and (
            not isinstance(self.exit_reference_price, Decimal)
            or not self.exit_reference_price.is_finite()
            or self.exit_reference_price <= 0
        ):
            raise PositionExitValidationError("exit reference price must be positive and finite")
        if self.action is PositionExitAction.HOLD and self.exit_reference_price is not None:
            raise PositionExitValidationError("HOLD cannot expose an exit reference price")
        if self.is_execution_authority is not False:
            raise PositionExitValidationError("exit evaluation cannot claim execution authority")
        _canonical_text("evaluated_context_integrity_seal", self.evaluated_context_integrity_seal)
        _canonical_text("exit_policy_version", self.exit_policy_version)


@runtime_checkable
class PositionExitStrategy(Protocol):
    """Environment-neutral strategy interface shared by every future adapter."""

    @property
    def strategy_id(self) -> str: ...

    @property
    def strategy_version(self) -> str: ...

    @property
    def exit_policy_version(self) -> str: ...

    def evaluate_exit(self, context: PositionExitEvaluationContext) -> PositionExitEvaluation: ...


class HoldOnlyExitStrategy:
    """Fail-safe baseline that never requests a discretionary position close."""

    __slots__ = ("_strategy_id", "_strategy_version")

    def __init__(self, strategy_id: str, strategy_version: str) -> None:
        _canonical_text("strategy_id", strategy_id)
        _canonical_text("strategy_version", strategy_version)
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def strategy_version(self) -> str:
        return self._strategy_version

    @property
    def exit_policy_version(self) -> str:
        return _HOLD_ONLY_EXIT_POLICY_VERSION

    def evaluate_exit(self, context: PositionExitEvaluationContext) -> PositionExitEvaluation:
        """Return a detached HOLD audit without any close or order authority."""

        if not isinstance(context, PositionExitEvaluationContext):
            raise TypeError("context must be a PositionExitEvaluationContext")
        context.validate()
        if (
            context.strategy_id != self.strategy_id
            or context.strategy_version != self.strategy_version
        ):
            raise PositionExitValidationError("exit strategy and position strategy differ")
        result = PositionExitEvaluation.create(
            context,
            action=PositionExitAction.HOLD,
            reasons_for=(HoldOnlyExitReasonCode.NO_DISCRETIONARY_EXIT_RULE,),
            reasons_against=(),
            rule_audit=HoldOnlyExitRuleAudit(False, True, self.exit_policy_version),
            exit_policy_version=self.exit_policy_version,
        )
        result.validate()
        return deepcopy(result)


def _canonical_text(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PositionExitValidationError(f"{field_name} must be a canonical non-empty string")


def _integrity_seal(namespace: str, value: object) -> str:
    payload = dumps(
        (
            f"adaptive-trading-bot/position-exit-{namespace}",
            _EXIT_INTEGRITY_SEAL_VERSION,
            _canonical_integrity_value(value),
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
                if not item.name.endswith("integrity_seal")
            ),
        )
    raise PositionExitValidationError(
        f"exit authority contains unsupported integrity type {type(value).__qualname__}"
    )
