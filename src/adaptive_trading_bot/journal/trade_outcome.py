"""Deterministic append-only completed-trade audit contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import InitVar, dataclass, field, fields
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Any, Protocol, Self, cast, runtime_checkable

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.journal.decision import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryOrderIntentIdentity,
    EntrySide,
    ExitOrderIntentIdentity,
    ExitSide,
)
from adaptive_trading_bot.risk import OperationalSafetyStatus, RiskDecisionStatus
from adaptive_trading_bot.strategies.core import CandidateAction, SourceIdentity

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_JOURNAL_SCHEMA_VERSION = "1.0.0"
_PAPER_SIMULATION_SCHEMA_VERSION = "1.0.0"
_COMPLETED_TRADE_ENTRY_TOKEN = object()


class CompletedTradeJournalValidationError(ValueError):
    """A completed-trade record or batch contains inconsistent facts."""


class CompletedTradeJournalAppendError(ValueError):
    """An outcome append violates identity or chronology invariants."""


class TradeExitReason(StrEnum):
    """Currently authoritative Paper close reasons."""

    PROTECTIVE_STOP = "PROTECTIVE_STOP"


class TradeOutcomeClassification(StrEnum):
    """Mechanical classification of exact net realized PnL."""

    PROFIT = "PROFIT"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"


@runtime_checkable
class PaperOrderIdentityLike(Protocol):
    """Structural boundary avoiding a journal-to-Paper package dependency."""

    @property
    def intent_identity(self) -> EntryOrderIntentIdentity: ...

    @property
    def simulation_schema_version(self) -> str: ...


@dataclass(frozen=True, slots=True)
class CompletedTradeIdentity:
    """Unique linkage across decision, entry, Paper, and exit authority."""

    originating_decision_identity: DecisionIdentity
    entry_intent_identity: EntryOrderIntentIdentity
    paper_order_identity: PaperOrderIdentityLike
    exit_intent_identity: ExitOrderIntentIdentity

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, str]:
        """Return canonical order rooted in the originating decision."""

        return self.originating_decision_identity.sort_key


@dataclass(frozen=True, slots=True)
class CompletedTradeJournalEntry:
    """Exact pre-trade provenance plus currently observable realized outcome."""

    identity: CompletedTradeIdentity
    pre_trade_decision: DecisionJournalEntry[object, object]
    source_id: SourceIdentity
    symbol: TradingSymbol
    strategy_id: str
    strategy_version: str
    entry_side: EntrySide
    exit_side: ExitSide
    quantity: Decimal
    approved_notional_quote: Decimal
    approved_risk_quote: Decimal
    stop_basis: Decimal
    approved_reference_price: Decimal
    entry_authorized_at: datetime
    entry_reference_price: Decimal
    entry_fill_at: datetime
    entry_fill_price: Decimal
    entry_spread_bps: Decimal
    entry_slippage_bps: Decimal
    entry_fee_bps: Decimal
    entry_fee_quote: Decimal
    exit_reason: TradeExitReason
    trigger_observed_at: datetime
    trigger_reference_price: Decimal
    exit_authorized_at: datetime
    exit_reference_price: Decimal
    exit_fill_at: datetime
    exit_fill_price: Decimal
    exit_spread_bps: Decimal
    exit_slippage_bps: Decimal
    exit_fee_bps: Decimal
    exit_fee_quote: Decimal
    gross_realized_pnl: Decimal
    total_trade_fees_quote: Decimal
    net_realized_pnl: Decimal
    realized_r_net: Decimal
    duration: timedelta
    risk_policy_version: str
    safety_policy_version: str
    accounting_entry_fill_id: str
    accounting_exit_fill_id: str
    outcome_classification: TradeOutcomeClassification
    _creation_token: InitVar[object] = None
    journal_schema_version: str = field(init=False, default=_JOURNAL_SCHEMA_VERSION)
    _creation_seal: object = field(init=False, repr=False, compare=False)

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        if args or kwargs.get("_creation_token") is not _COMPLETED_TRADE_ENTRY_TOKEN:
            raise TypeError(
                "CompletedTradeJournalEntry can only be created from an authoritative close"
            )
        return object.__new__(cls)

    def __post_init__(self, _creation_token: object) -> None:
        if _creation_token is not _COMPLETED_TRADE_ENTRY_TOKEN:
            raise TypeError("completed trade creation authority is invalid")
        object.__setattr__(self, "_creation_seal", _COMPLETED_TRADE_ENTRY_TOKEN)
        self.validate()

    @classmethod
    def _from_authoritative_close(cls, **values: object) -> Self:
        """Create a sealed record for the coordinator's validated close path."""

        constructor = cast(Any, cls)
        return cast(
            Self,
            constructor(**values, _creation_token=_COMPLETED_TRADE_ENTRY_TOKEN),
        )

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        """Copy a sealed record without exposing its creation authority."""

        copied = object.__new__(type(self))
        memo[id(self)] = copied
        for value_field in fields(self):
            value = (
                _COMPLETED_TRADE_ENTRY_TOKEN
                if value_field.name == "_creation_seal"
                else deepcopy(getattr(self, value_field.name), memo)
            )
            object.__setattr__(copied, value_field.name, value)
        return copied

    def validate(self) -> None:
        """Revalidate every authoritative link before append or snapshot copy."""

        _validate_identity_and_decision(self)
        _validate_execution_and_accounting(self)


@dataclass(frozen=True, slots=True)
class CompletedTradeJournalBatch:
    """One non-empty, same-close-time, canonical atomic append unit."""

    closed_at: datetime
    entries: tuple[CompletedTradeJournalEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "closed_at", require_utc(self.closed_at, field="completed batch closed_at")
        )
        if not isinstance(self.entries, tuple):
            raise TypeError("completed batch entries must be an immutable tuple")
        if not self.entries:
            raise CompletedTradeJournalValidationError("completed trade batch must be non-empty")
        for entry in self.entries:
            if not isinstance(entry, CompletedTradeJournalEntry):
                raise TypeError("batch entries must be CompletedTradeJournalEntry values")
            entry.validate()
            if entry.exit_fill_at != self.closed_at:
                raise CompletedTradeJournalValidationError(
                    "every completed entry must match batch closed_at"
                )
        ordered = tuple(sorted(self.entries, key=lambda item: item.identity.sort_key))
        identities = tuple(item.identity for item in ordered)
        if len(set(identities)) != len(identities):
            raise CompletedTradeJournalValidationError(
                "duplicate completed trade identity within batch"
            )
        object.__setattr__(self, "entries", ordered)


@dataclass(frozen=True, slots=True)
class CompletedTradeJournalAppendResult:
    """Detached acknowledgement of an atomic outcome append."""

    closed_at: datetime
    appended_identities: tuple[CompletedTradeIdentity, ...]
    appended_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class CompletedTradeJournalSnapshot:
    """Detached immutable view of every completed outcome."""

    entries: tuple[CompletedTradeJournalEntry, ...]
    last_closed_at: datetime | None
    journal_schema_version: str = field(init=False, default=_JOURNAL_SCHEMA_VERSION)


class InMemoryCompletedTradeJournal:
    """Process-local append-only completed-trade ledger."""

    __slots__ = ("_entries", "_identities", "_last_closed_at")

    def __init__(self) -> None:
        self._entries: tuple[CompletedTradeJournalEntry, ...] = ()
        self._identities: tuple[CompletedTradeIdentity, ...] = ()
        self._last_closed_at: datetime | None = None

    def append_batch(self, batch: CompletedTradeJournalBatch) -> CompletedTradeJournalAppendResult:
        """Validate, own, and atomically append one complete close batch."""

        if not isinstance(batch, CompletedTradeJournalBatch):
            raise TypeError("batch must be a CompletedTradeJournalBatch")
        owned = tuple(_copy_entry(entry) for entry in batch.entries)
        identities = tuple(entry.identity for entry in owned)
        if any(identity in self._identities for identity in identities):
            raise CompletedTradeJournalAppendError(
                "completed trade identity already exists in journal"
            )
        if self._last_closed_at is not None and batch.closed_at <= self._last_closed_at:
            raise CompletedTradeJournalAppendError(
                "closed_at must strictly advance; equal-time entries belong together"
            )
        self._entries = (*self._entries, *owned)
        self._identities = (*self._identities, *identities)
        self._last_closed_at = batch.closed_at
        return CompletedTradeJournalAppendResult(
            batch.closed_at,
            tuple(deepcopy(identity) for identity in identities),
            len(owned),
            len(self._entries),
        )

    def clone(self) -> InMemoryCompletedTradeJournal:
        """Return an independent validated ledger for atomic staging."""

        cloned = InMemoryCompletedTradeJournal()
        closed_times: list[datetime] = []
        for entry in self._entries:
            if not closed_times or closed_times[-1] != entry.exit_fill_at:
                closed_times.append(entry.exit_fill_at)
        for closed_at in closed_times:
            cloned.append_batch(
                CompletedTradeJournalBatch(
                    closed_at,
                    tuple(
                        _copy_entry(entry)
                        for entry in self._entries
                        if entry.exit_fill_at == closed_at
                    ),
                )
            )
        return cloned

    def snapshot(self) -> CompletedTradeJournalSnapshot:
        """Return copies that cannot mutate ledger-owned provenance."""

        return CompletedTradeJournalSnapshot(
            tuple(_copy_entry(entry) for entry in self._entries),
            self._last_closed_at,
        )


def _validate_identity_and_decision(entry: CompletedTradeJournalEntry) -> None:
    if entry._creation_seal is not _COMPLETED_TRADE_ENTRY_TOKEN:
        raise CompletedTradeJournalValidationError("completed trade creation seal is invalid")
    if entry.journal_schema_version != _JOURNAL_SCHEMA_VERSION:
        raise CompletedTradeJournalValidationError("completed journal schema mismatch")
    if not isinstance(entry.identity, CompletedTradeIdentity):
        raise TypeError("completed identity is invalid")
    if not isinstance(entry.pre_trade_decision, DecisionJournalEntry):
        raise TypeError("pre_trade_decision must be a DecisionJournalEntry")
    entry.pre_trade_decision.validate()
    decision = entry.pre_trade_decision
    risk = decision.risk_decision
    safety = decision.safety_decision
    expected_entry_identity = EntryOrderIntentIdentity(decision.identity)
    paper_identity = entry.identity.paper_order_identity
    expected_entry_side = (
        EntrySide.BUY
        if risk.strategy_candidate_action is CandidateAction.LONG_CANDIDATE
        else EntrySide.SELL
    )
    if not isinstance(paper_identity, PaperOrderIdentityLike):
        raise CompletedTradeJournalValidationError("Paper order identity is invalid")
    if (
        entry.identity.originating_decision_identity != decision.identity
        or entry.identity.entry_intent_identity != expected_entry_identity
        or paper_identity.intent_identity != expected_entry_identity
        or paper_identity.simulation_schema_version != _PAPER_SIMULATION_SCHEMA_VERSION
        or entry.identity.exit_intent_identity.originating_entry_intent_identity
        != expected_entry_identity
        or entry.source_id != decision.identity.source_id
        or entry.symbol != decision.identity.symbol
        or entry.strategy_id != decision.identity.strategy_id
        or entry.strategy_version != decision.identity.strategy_version
        or risk.status is not RiskDecisionStatus.APPROVED
        or safety.status is not OperationalSafetyStatus.CLEARED
        or safety.entry_permitted is not True
        or entry.entry_side is not expected_entry_side
        or entry.quantity != risk.approved_quantity
        or entry.approved_notional_quote != risk.approved_notional_quote
        or entry.approved_risk_quote != risk.approved_risk_quote
        or entry.stop_basis != risk.approved_risk_stop_basis
        or entry.approved_reference_price != risk.reference_price
        or entry.risk_policy_version != risk.risk_policy_version
        or entry.safety_policy_version != safety.safety_policy_version
    ):
        raise CompletedTradeJournalValidationError(
            "completed trade does not preserve pre-trade authority"
        )


def _validate_execution_and_accounting(entry: CompletedTradeJournalEntry) -> None:
    if not isinstance(entry.source_id, SourceIdentity):
        raise TypeError("completed source_id must implement SourceIdentity")
    if not isinstance(entry.symbol, TradingSymbol):
        raise TypeError("completed symbol must be a TradingSymbol")
    if not isinstance(entry.entry_side, EntrySide) or not isinstance(entry.exit_side, ExitSide):
        raise TypeError("completed trade sides are invalid")
    if entry.exit_reason is not TradeExitReason.PROTECTIVE_STOP:
        raise CompletedTradeJournalValidationError("unsupported completed exit reason")
    if entry.outcome_classification not in set(TradeOutcomeClassification):
        raise CompletedTradeJournalValidationError("outcome classification is invalid")
    for name, value in (
        ("strategy_id", entry.strategy_id),
        ("strategy_version", entry.strategy_version),
        ("risk_policy_version", entry.risk_policy_version),
        ("safety_policy_version", entry.safety_policy_version),
        ("accounting_entry_fill_id", entry.accounting_entry_fill_id),
        ("accounting_exit_fill_id", entry.accounting_exit_fill_id),
    ):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise CompletedTradeJournalValidationError(f"{name} must be canonical and non-empty")
    times = (
        entry.pre_trade_decision.identity.observed_at,
        require_utc(entry.entry_authorized_at, field="entry_authorized_at"),
        require_utc(entry.entry_fill_at, field="entry_fill_at"),
        require_utc(entry.trigger_observed_at, field="trigger_observed_at"),
        require_utc(entry.exit_authorized_at, field="exit_authorized_at"),
        require_utc(entry.exit_fill_at, field="exit_fill_at"),
    )
    if not (
        times[0] <= times[1] <= times[2] < times[3] == times[4] <= times[5]
        and entry.identity.exit_intent_identity.trigger_observed_at == times[3]
    ):
        raise CompletedTradeJournalValidationError("completed trade chronology is invalid")
    expected_duration = entry.exit_fill_at - entry.entry_fill_at
    if not isinstance(entry.duration, timedelta) or entry.duration != expected_duration:
        raise CompletedTradeJournalValidationError("completed trade duration is inconsistent")

    positive = (
        entry.quantity,
        entry.approved_notional_quote,
        entry.approved_risk_quote,
        entry.stop_basis,
        entry.approved_reference_price,
        entry.entry_reference_price,
        entry.entry_fill_price,
        entry.trigger_reference_price,
        entry.exit_reference_price,
        entry.exit_fill_price,
    )
    nonnegative = (
        entry.entry_spread_bps,
        entry.entry_slippage_bps,
        entry.entry_fee_bps,
        entry.entry_fee_quote,
        entry.exit_spread_bps,
        entry.exit_slippage_bps,
        entry.exit_fee_bps,
        entry.exit_fee_quote,
    )
    outcome = (
        entry.gross_realized_pnl,
        entry.total_trade_fees_quote,
        entry.net_realized_pnl,
        entry.realized_r_net,
    )
    if not all(
        isinstance(value, Decimal) and value.is_finite() and value > 0 for value in positive
    ):
        raise CompletedTradeJournalValidationError("completed positive monetary fields are invalid")
    if not all(
        isinstance(value, Decimal) and value.is_finite() and value >= 0 for value in nonnegative
    ):
        raise CompletedTradeJournalValidationError("completed execution costs are invalid")
    if not all(isinstance(value, Decimal) and value.is_finite() for value in outcome):
        raise CompletedTradeJournalValidationError("completed outcome fields are invalid")
    expected_exit_side = ExitSide.SELL if entry.entry_side is EntrySide.BUY else ExitSide.BUY
    trigger_valid = (
        entry.entry_side is EntrySide.BUY and entry.trigger_reference_price <= entry.stop_basis
    ) or (entry.entry_side is EntrySide.SELL and entry.trigger_reference_price >= entry.stop_basis)
    if entry.exit_side is not expected_exit_side or not trigger_valid:
        raise CompletedTradeJournalValidationError("protective exit geometry is inconsistent")

    entry_quote = price_simulated_market_fill(
        side=(
            SimulatedExecutionSide.BUY
            if entry.entry_side is EntrySide.BUY
            else SimulatedExecutionSide.SELL
        ),
        quantity=entry.quantity,
        reference_price=entry.entry_reference_price,
        costs=SimulatedExecutionCostConfiguration(
            entry.entry_spread_bps,
            entry.entry_slippage_bps,
            entry.entry_fee_bps,
        ),
    )
    exit_quote = price_simulated_market_fill(
        side=(
            SimulatedExecutionSide.BUY
            if entry.exit_side is ExitSide.BUY
            else SimulatedExecutionSide.SELL
        ),
        quantity=entry.quantity,
        reference_price=entry.exit_reference_price,
        costs=SimulatedExecutionCostConfiguration(
            entry.exit_spread_bps,
            entry.exit_slippage_bps,
            entry.exit_fee_bps,
        ),
    )
    with localcontext(_DECIMAL_CONTEXT):
        expected_fees = entry.entry_fee_quote + entry.exit_fee_quote
        expected_net = entry.gross_realized_pnl - expected_fees
        expected_r = expected_net / entry.approved_risk_quote
    expected_classification = (
        TradeOutcomeClassification.PROFIT
        if expected_net > 0
        else TradeOutcomeClassification.LOSS
        if expected_net < 0
        else TradeOutcomeClassification.BREAKEVEN
    )
    if (
        entry.entry_fill_price != entry_quote.fill_price
        or entry.entry_fee_quote != entry_quote.fee_quote
        or entry.exit_fill_price != exit_quote.fill_price
        or entry.exit_fee_quote != exit_quote.fee_quote
        or entry.total_trade_fees_quote != expected_fees
        or entry.net_realized_pnl != expected_net
        or entry.realized_r_net != expected_r
        or entry.outcome_classification is not expected_classification
    ):
        raise CompletedTradeJournalValidationError(
            "completed execution or outcome arithmetic is inconsistent"
        )


def _copy_entry(entry: CompletedTradeJournalEntry) -> CompletedTradeJournalEntry:
    owned = deepcopy(entry)
    owned.validate()
    return owned
