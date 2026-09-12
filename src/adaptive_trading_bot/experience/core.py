"""Deterministic causal Experience fingerprints and completed-trade sample projection.

This module only joins and copies existing journal authority. It does not calculate performance,
score a strategy, evaluate Risk or Safety, authorize an order, or mutate trading state.
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

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalEntry,
    DecisionJournalSnapshot,
)
from adaptive_trading_bot.journal.trade_outcome import (
    CompletedTradeJournalEntry,
    CompletedTradeJournalSnapshot,
)
from adaptive_trading_bot.regime import (
    Direction,
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    RegimeCompositionError,
    RegimeLabel,
    TechnicalRegimeError,
    VolatilityState,
    VolumeState,
    validate_multi_timeframe_regime_snapshot,
    validate_technical_regime_snapshot,
)

_FINGERPRINT_SCHEMA_VERSION = "1.0.0"
_FALLBACK_KEY_SCHEMA_VERSION = "1.0.0"
_SAMPLE_SCHEMA_VERSION = "1.0.0"
_SAMPLE_INTEGRITY_SEAL_VERSION = "1.0.0"
_SAMPLE_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
_DECISION_JOURNAL_SCHEMA_VERSION = "1.0.0"
_COMPLETED_TRADE_JOURNAL_SCHEMA_VERSION = "1.0.0"
_ENTRY_CANDIDATES = frozenset({"LONG_CANDIDATE", "SHORT_CANDIDATE"})


class ExperienceProjectionError(ValueError):
    """Input authority cannot be projected into an unambiguous causal sample."""


@runtime_checkable
class _SourceIdentity(Protocol):
    @property
    def value(self) -> str: ...


class TrendStrengthBucket(StrEnum):
    """Version 1 deterministic buckets for absolute 20-point direction scores."""

    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


class ExperienceFallbackLevel(StrEnum):
    """Most-specific to broadest future aggregation hierarchy."""

    FULL_CONTEXT = "FULL_CONTEXT"
    REGIME_HTF = "REGIME_HTF"
    REGIME = "REGIME"
    STRATEGY_SYMBOL = "STRATEGY_SYMBOL"
    STRATEGY = "STRATEGY"


def trend_strength_bucket(strength: int) -> TrendStrengthBucket:
    """Map one canonical absolute direction score to its V1 bucket."""

    if type(strength) is not int or strength not in {0, 20, 40, 60, 80, 100}:
        raise ExperienceProjectionError("trend strength must be one of 0, 20, 40, 60, 80, or 100")
    if strength <= 20:
        return TrendStrengthBucket.WEAK
    if strength == 40:
        return TrendStrengthBucket.MODERATE
    return TrendStrengthBucket.STRONG


@dataclass(frozen=True, slots=True)
class ExperienceContextFingerprint:
    """Small causal context identity copied only from the entry decision context."""

    symbol: TradingSymbol
    strategy_id: str
    strategy_version: str
    entry_timeframe: Timeframe
    regime_label: RegimeLabel
    direction: Direction
    trend_strength_bucket: TrendStrengthBucket
    volatility_state: VolatilityState
    volume_state: VolumeState
    higher_timeframe_bias: HigherTimeframeBias
    multi_timeframe_alignment: MultiTimeframeAlignment
    fingerprint_schema_version: str = field(init=False, default=_FINGERPRINT_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.fingerprint_schema_version != _FINGERPRINT_SCHEMA_VERSION:
            raise ExperienceProjectionError("fingerprint schema version mismatch")
        if not isinstance(self.symbol, TradingSymbol) or self.symbol.base not in {"BTC", "ETH"}:
            raise ExperienceProjectionError("fingerprint symbol is outside V1")
        _require_canonical_text("strategy_id", self.strategy_id)
        _require_canonical_text("strategy_version", self.strategy_version)
        if self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ExperienceProjectionError("fingerprint entry timeframe must be 15m")
        for name, value, expected in (
            ("regime_label", self.regime_label, RegimeLabel),
            ("direction", self.direction, Direction),
            ("trend_strength_bucket", self.trend_strength_bucket, TrendStrengthBucket),
            ("volatility_state", self.volatility_state, VolatilityState),
            ("volume_state", self.volume_state, VolumeState),
            ("higher_timeframe_bias", self.higher_timeframe_bias, HigherTimeframeBias),
            (
                "multi_timeframe_alignment",
                self.multi_timeframe_alignment,
                MultiTimeframeAlignment,
            ),
        ):
            if not isinstance(value, expected):
                raise ExperienceProjectionError(f"fingerprint {name} is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceContextKey:
    """Typed sparse key whose populated fields are fixed by its hierarchy level."""

    level: ExperienceFallbackLevel
    strategy_id: str
    strategy_version: str
    symbol: TradingSymbol | None = None
    entry_timeframe: Timeframe | None = None
    regime_label: RegimeLabel | None = None
    direction: Direction | None = None
    trend_strength_bucket: TrendStrengthBucket | None = None
    volatility_state: VolatilityState | None = None
    volume_state: VolumeState | None = None
    higher_timeframe_bias: HigherTimeframeBias | None = None
    multi_timeframe_alignment: MultiTimeframeAlignment | None = None
    key_schema_version: str = field(init=False, default=_FALLBACK_KEY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.level, ExperienceFallbackLevel):
            raise ExperienceProjectionError("fallback level is invalid")
        if self.key_schema_version != _FALLBACK_KEY_SCHEMA_VERSION:
            raise ExperienceProjectionError("fallback key schema version mismatch")
        _require_canonical_text("strategy_id", self.strategy_id)
        _require_canonical_text("strategy_version", self.strategy_version)
        present = frozenset(
            name
            for name in (
                "symbol",
                "entry_timeframe",
                "regime_label",
                "direction",
                "trend_strength_bucket",
                "volatility_state",
                "volume_state",
                "higher_timeframe_bias",
                "multi_timeframe_alignment",
            )
            if getattr(self, name) is not None
        )
        required = {
            ExperienceFallbackLevel.FULL_CONTEXT: frozenset(
                {
                    "symbol",
                    "entry_timeframe",
                    "regime_label",
                    "direction",
                    "trend_strength_bucket",
                    "volatility_state",
                    "volume_state",
                    "higher_timeframe_bias",
                    "multi_timeframe_alignment",
                }
            ),
            ExperienceFallbackLevel.REGIME_HTF: frozenset(
                {
                    "symbol",
                    "regime_label",
                    "higher_timeframe_bias",
                    "multi_timeframe_alignment",
                }
            ),
            ExperienceFallbackLevel.REGIME: frozenset({"symbol", "regime_label"}),
            ExperienceFallbackLevel.STRATEGY_SYMBOL: frozenset({"symbol"}),
            ExperienceFallbackLevel.STRATEGY: frozenset(),
        }[self.level]
        if present != required:
            raise ExperienceProjectionError(
                f"fallback key dimensions do not match {self.level.value}"
            )
        if self.symbol is not None and (
            not isinstance(self.symbol, TradingSymbol) or self.symbol.base not in {"BTC", "ETH"}
        ):
            raise ExperienceProjectionError("fallback symbol is outside V1")
        if self.entry_timeframe is not None and self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ExperienceProjectionError("fallback entry timeframe must be 15m")
        for name, value, expected in (
            ("regime_label", self.regime_label, RegimeLabel),
            ("direction", self.direction, Direction),
            ("trend_strength_bucket", self.trend_strength_bucket, TrendStrengthBucket),
            ("volatility_state", self.volatility_state, VolatilityState),
            ("volume_state", self.volume_state, VolumeState),
            ("higher_timeframe_bias", self.higher_timeframe_bias, HigherTimeframeBias),
            (
                "multi_timeframe_alignment",
                self.multi_timeframe_alignment,
                MultiTimeframeAlignment,
            ),
        ):
            if value is not None and not isinstance(value, expected):
                raise ExperienceProjectionError(f"fallback {name} is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceTradeSample:
    """Detached exact outcome joined to its causal pre-trade fingerprint."""

    decision_identity: DecisionIdentity
    source_id: _SourceIdentity
    fingerprint: ExperienceContextFingerprint
    hierarchical_keys: tuple[ExperienceContextKey, ...]
    entry_decision_at: datetime
    entry_fill_at: datetime
    exit_fill_at: datetime
    candidate_action: str
    entry_side: str
    exit_side: str
    quantity: Decimal
    approved_risk_quote: Decimal
    gross_realized_pnl: Decimal
    total_trade_fees_quote: Decimal
    net_realized_pnl: Decimal
    realized_r_net: Decimal
    outcome_classification: str
    exit_reason: str
    duration: timedelta
    risk_policy_version: str
    safety_policy_version: str
    accounting_entry_fill_id: str
    accounting_exit_fill_id: str
    sample_schema_version: str = field(init=False, default=_SAMPLE_SCHEMA_VERSION)
    sample_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(self, "sample_integrity_seal", _sample_integrity_seal(self))

    @property
    def sort_key(self) -> tuple[datetime, datetime, str, str, str, str, str]:
        return (
            self.entry_decision_at,
            self.entry_fill_at,
            self.source_id.value,
            str(self.fingerprint.symbol),
            self.fingerprint.strategy_id,
            self.fingerprint.strategy_version,
            self.accounting_entry_fill_id,
        )

    def validate(self) -> None:
        seal = self.sample_integrity_seal
        if not isinstance(seal, str) or not compare_digest(seal, _sample_integrity_seal(self)):
            raise ExperienceProjectionError("sample integrity seal mismatch")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if self.sample_schema_version != _SAMPLE_SCHEMA_VERSION:
            raise ExperienceProjectionError("sample schema version mismatch")
        if not isinstance(self.decision_identity, DecisionIdentity):
            raise ExperienceProjectionError("sample decision identity is invalid")
        self.fingerprint.validate()
        if (
            not isinstance(self.source_id, _SourceIdentity)
            or self.source_id != self.decision_identity.source_id
        ):
            raise ExperienceProjectionError("sample source provenance is inconsistent")
        _require_canonical_text("source_id", self.source_id.value)
        if (
            self.fingerprint.symbol != self.decision_identity.symbol
            or self.fingerprint.strategy_id != self.decision_identity.strategy_id
            or self.fingerprint.strategy_version != self.decision_identity.strategy_version
            or self.entry_decision_at != self.decision_identity.observed_at
        ):
            raise ExperienceProjectionError("sample decision provenance is inconsistent")
        _validate_hierarchy(self.fingerprint, self.hierarchical_keys)
        times = tuple(
            require_utc(value, field=name)
            for name, value in (
                ("entry_decision_at", self.entry_decision_at),
                ("entry_fill_at", self.entry_fill_at),
                ("exit_fill_at", self.exit_fill_at),
            )
        )
        if not times[0] <= times[1] <= times[2]:
            raise ExperienceProjectionError("sample chronology is invalid")
        if self.candidate_action not in _ENTRY_CANDIDATES:
            raise ExperienceProjectionError("sample action must be an entry candidate")
        if self.entry_side not in {"BUY", "SELL"} or self.exit_side not in {"BUY", "SELL"}:
            raise ExperienceProjectionError("sample sides are invalid")
        if self.entry_side == self.exit_side:
            raise ExperienceProjectionError("sample exit side must oppose entry side")
        for name, value in (
            ("quantity", self.quantity),
            ("approved_risk_quote", self.approved_risk_quote),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ExperienceProjectionError(f"sample {name} must be a positive finite Decimal")
        for name, value in (
            ("gross_realized_pnl", self.gross_realized_pnl),
            ("total_trade_fees_quote", self.total_trade_fees_quote),
            ("net_realized_pnl", self.net_realized_pnl),
            ("realized_r_net", self.realized_r_net),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ExperienceProjectionError(f"sample {name} must be a finite Decimal")
        if self.total_trade_fees_quote < 0:
            raise ExperienceProjectionError("sample total trade fees cannot be negative")
        if (
            not isinstance(self.duration, timedelta)
            or self.duration < timedelta()
            or self.duration != self.exit_fill_at - self.entry_fill_at
        ):
            raise ExperienceProjectionError("sample duration is invalid")
        for text_name, text_value in (
            ("outcome_classification", self.outcome_classification),
            ("exit_reason", self.exit_reason),
            ("risk_policy_version", self.risk_policy_version),
            ("safety_policy_version", self.safety_policy_version),
            ("accounting_entry_fill_id", self.accounting_entry_fill_id),
            ("accounting_exit_fill_id", self.accounting_exit_fill_id),
        ):
            _require_canonical_text(text_name, text_value)
        if self.outcome_classification not in {"PROFIT", "LOSS", "BREAKEVEN"}:
            raise ExperienceProjectionError("sample outcome classification is invalid")
        if self.exit_reason != "PROTECTIVE_STOP":
            raise ExperienceProjectionError("sample exit reason is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceTradeSampleSnapshot:
    """Immutable canonically ordered output of one pure projection."""

    samples: tuple[ExperienceTradeSample, ...]
    sample_snapshot_schema_version: str = field(init=False, default=_SAMPLE_SNAPSHOT_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if not isinstance(self.samples, tuple):
            raise TypeError("Experience samples must be an immutable tuple")
        if self.sample_snapshot_schema_version != _SAMPLE_SNAPSHOT_SCHEMA_VERSION:
            raise ExperienceProjectionError("sample snapshot schema version mismatch")
        for sample in self.samples:
            if not isinstance(sample, ExperienceTradeSample):
                raise TypeError("sample snapshot values must be ExperienceTradeSample")
            sample.validate()
        if self.samples != tuple(sorted(self.samples, key=lambda sample: sample.sort_key)):
            raise ExperienceProjectionError("sample snapshot order is not canonical")
        identities = tuple(sample.decision_identity for sample in self.samples)
        if len(set(identities)) != len(identities):
            raise ExperienceProjectionError("sample snapshot contains duplicate decisions")


class ExperienceSampleProjector:
    """Pure exact join from detached journal snapshots to causal Experience samples."""

    __slots__ = ()

    def project(
        self,
        decisions: DecisionJournalSnapshot[object, object],
        completed_trades: CompletedTradeJournalSnapshot,
    ) -> ExperienceTradeSampleSnapshot:
        if not isinstance(decisions, DecisionJournalSnapshot):
            raise TypeError("decisions must be a DecisionJournalSnapshot")
        if not isinstance(completed_trades, CompletedTradeJournalSnapshot):
            raise TypeError("completed_trades must be a CompletedTradeJournalSnapshot")

        owned_decisions = deepcopy(decisions)
        owned_completed = deepcopy(completed_trades)
        decision_by_identity = _validated_decisions(owned_decisions)
        completed = _validated_completed(owned_completed)

        samples: list[ExperienceTradeSample] = []
        for trade in completed:
            identity = trade.identity.originating_decision_identity
            decision = decision_by_identity.get(identity)
            if decision is None:
                raise ExperienceProjectionError("completed trade has no originating decision")
            if trade.pre_trade_decision != decision:
                raise ExperienceProjectionError(
                    "completed trade and decision snapshot contain inconsistent pre-trade authority"
                )
            samples.append(_project_sample(decision, trade))
        return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda item: item.sort_key)))


def _validated_decisions(
    snapshot: DecisionJournalSnapshot[object, object],
) -> dict[DecisionIdentity, DecisionJournalEntry[object, object]]:
    if snapshot.journal_schema_version != _DECISION_JOURNAL_SCHEMA_VERSION:
        raise ExperienceProjectionError("decision snapshot schema version mismatch")
    if not isinstance(snapshot.entries, tuple):
        raise TypeError("decision snapshot entries must be an immutable tuple")
    expected_last = max((entry.identity.observed_at for entry in snapshot.entries), default=None)
    if snapshot.last_observed_at != expected_last:
        raise ExperienceProjectionError("decision snapshot last_observed_at is inconsistent")
    if snapshot.last_observed_at is not None:
        require_utc(snapshot.last_observed_at, field="decision snapshot last_observed_at")
    indexed: dict[DecisionIdentity, DecisionJournalEntry[object, object]] = {}
    for entry in snapshot.entries:
        if not isinstance(entry, DecisionJournalEntry):
            raise TypeError("decision snapshot contains an invalid entry")
        entry.validate()
        if entry.identity in indexed:
            raise ExperienceProjectionError("duplicate decision identity in snapshot")
        indexed[entry.identity] = entry
    return indexed


def _validated_completed(
    snapshot: CompletedTradeJournalSnapshot,
) -> tuple[CompletedTradeJournalEntry, ...]:
    if snapshot.journal_schema_version != _COMPLETED_TRADE_JOURNAL_SCHEMA_VERSION:
        raise ExperienceProjectionError("completed snapshot schema version mismatch")
    if not isinstance(snapshot.entries, tuple):
        raise TypeError("completed snapshot entries must be an immutable tuple")
    expected_last = max((entry.exit_fill_at for entry in snapshot.entries), default=None)
    if snapshot.last_closed_at != expected_last:
        raise ExperienceProjectionError("completed snapshot last_closed_at is inconsistent")
    if snapshot.last_closed_at is not None:
        require_utc(snapshot.last_closed_at, field="completed snapshot last_closed_at")
    origins: set[DecisionIdentity] = set()
    for entry in snapshot.entries:
        if not isinstance(entry, CompletedTradeJournalEntry):
            raise TypeError("completed snapshot contains an invalid entry")
        entry.validate()
        origin = entry.identity.originating_decision_identity
        if origin in origins:
            raise ExperienceProjectionError("multiple completed outcomes share one decision")
        origins.add(origin)
    return snapshot.entries


def _project_sample(
    decision: DecisionJournalEntry[object, object],
    trade: CompletedTradeJournalEntry,
) -> ExperienceTradeSample:
    action = decision.strategy_evaluation.action.value
    risk = decision.risk_decision
    safety = decision.safety_decision
    if action not in _ENTRY_CANDIDATES:
        raise ExperienceProjectionError("completed outcome is linked to NO_TRADE")
    if risk.status.value != "APPROVED":
        raise ExperienceProjectionError("completed outcome risk decision is not APPROVED")
    if safety.status.value != "CLEARED" or safety.entry_permitted is not True:
        raise ExperienceProjectionError("completed outcome safety decision is not CLEARED")
    if (
        trade.quantity != risk.approved_quantity
        or trade.approved_risk_quote != risk.approved_risk_quote
        or trade.stop_basis != risk.approved_risk_stop_basis
        or trade.quantity != safety.approved_quantity
        or trade.approved_risk_quote != safety.approved_risk_quote
        or trade.stop_basis != safety.approved_risk_stop_basis
        or trade.risk_policy_version != risk.risk_policy_version
        or trade.risk_policy_version != safety.risk_policy_version
        or trade.safety_policy_version != safety.safety_policy_version
    ):
        raise ExperienceProjectionError("completed trade approval provenance is inconsistent")
    if not (
        decision.identity.observed_at
        <= trade.entry_authorized_at
        <= trade.entry_fill_at
        <= trade.exit_fill_at
    ):
        raise ExperienceProjectionError("completed trade learning chronology is invalid")

    fingerprint = _fingerprint(decision)
    return ExperienceTradeSample(
        decision_identity=deepcopy(decision.identity),
        source_id=deepcopy(decision.identity.source_id),
        fingerprint=fingerprint,
        hierarchical_keys=experience_context_hierarchy(fingerprint),
        entry_decision_at=decision.identity.observed_at,
        entry_fill_at=trade.entry_fill_at,
        exit_fill_at=trade.exit_fill_at,
        candidate_action=decision.strategy_evaluation.action,
        entry_side=trade.entry_side,
        exit_side=trade.exit_side,
        quantity=trade.quantity,
        approved_risk_quote=trade.approved_risk_quote,
        gross_realized_pnl=trade.gross_realized_pnl,
        total_trade_fees_quote=trade.total_trade_fees_quote,
        net_realized_pnl=trade.net_realized_pnl,
        realized_r_net=trade.realized_r_net,
        outcome_classification=trade.outcome_classification,
        exit_reason=trade.exit_reason,
        duration=trade.duration,
        risk_policy_version=trade.risk_policy_version,
        safety_policy_version=trade.safety_policy_version,
        accounting_entry_fill_id=trade.accounting_entry_fill_id,
        accounting_exit_fill_id=trade.accounting_exit_fill_id,
    )


def _fingerprint(
    decision: DecisionJournalEntry[object, object],
) -> ExperienceContextFingerprint:
    context = decision.context
    regime = context.multi_timeframe_regime
    anchor = regime.regime_15m
    if context.candle_15m.timeframe is not Timeframe.MINUTES_15:
        raise ExperienceProjectionError("pre-trade fingerprint context identity is inconsistent")
    try:
        validate_technical_regime_snapshot(anchor, context.features_15m, context.structure_15m)
        validate_multi_timeframe_regime_snapshot(regime)
    except (TechnicalRegimeError, RegimeCompositionError, TypeError, ValueError) as error:
        raise ExperienceProjectionError("pre-trade regime authority is inconsistent") from error
    bucket = trend_strength_bucket(anchor.trend_strength)
    return ExperienceContextFingerprint(
        symbol=deepcopy(context.symbol),
        strategy_id=decision.identity.strategy_id,
        strategy_version=decision.identity.strategy_version,
        entry_timeframe=context.candle_15m.timeframe,
        regime_label=anchor.regime_label,
        direction=anchor.direction,
        trend_strength_bucket=bucket,
        volatility_state=anchor.volatility_state,
        volume_state=anchor.volume_state,
        higher_timeframe_bias=regime.higher_timeframe_bias,
        multi_timeframe_alignment=regime.alignment,
    )


def experience_context_hierarchy(
    fingerprint: ExperienceContextFingerprint,
) -> tuple[ExperienceContextKey, ...]:
    """Return the one canonical most-specific-to-broadest Experience hierarchy."""

    if not isinstance(fingerprint, ExperienceContextFingerprint):
        raise TypeError("fingerprint must be an ExperienceContextFingerprint")
    fingerprint.validate()
    return (
        ExperienceContextKey(
            ExperienceFallbackLevel.FULL_CONTEXT,
            fingerprint.strategy_id,
            fingerprint.strategy_version,
            symbol=fingerprint.symbol,
            entry_timeframe=fingerprint.entry_timeframe,
            regime_label=fingerprint.regime_label,
            direction=fingerprint.direction,
            trend_strength_bucket=fingerprint.trend_strength_bucket,
            volatility_state=fingerprint.volatility_state,
            volume_state=fingerprint.volume_state,
            higher_timeframe_bias=fingerprint.higher_timeframe_bias,
            multi_timeframe_alignment=fingerprint.multi_timeframe_alignment,
        ),
        ExperienceContextKey(
            ExperienceFallbackLevel.REGIME_HTF,
            fingerprint.strategy_id,
            fingerprint.strategy_version,
            symbol=fingerprint.symbol,
            regime_label=fingerprint.regime_label,
            higher_timeframe_bias=fingerprint.higher_timeframe_bias,
            multi_timeframe_alignment=fingerprint.multi_timeframe_alignment,
        ),
        ExperienceContextKey(
            ExperienceFallbackLevel.REGIME,
            fingerprint.strategy_id,
            fingerprint.strategy_version,
            symbol=fingerprint.symbol,
            regime_label=fingerprint.regime_label,
        ),
        ExperienceContextKey(
            ExperienceFallbackLevel.STRATEGY_SYMBOL,
            fingerprint.strategy_id,
            fingerprint.strategy_version,
            symbol=fingerprint.symbol,
        ),
        ExperienceContextKey(
            ExperienceFallbackLevel.STRATEGY,
            fingerprint.strategy_id,
            fingerprint.strategy_version,
        ),
    )


def _validate_hierarchy(
    fingerprint: ExperienceContextFingerprint,
    keys: tuple[ExperienceContextKey, ...],
) -> None:
    if not isinstance(keys, tuple):
        raise TypeError("hierarchical keys must be an immutable tuple")
    for key in keys:
        if not isinstance(key, ExperienceContextKey):
            raise TypeError("hierarchical keys contain an invalid value")
        key.validate()
    if keys != experience_context_hierarchy(fingerprint):
        raise ExperienceProjectionError("sample hierarchy does not match its fingerprint")


def _require_canonical_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ExperienceProjectionError(f"{name} must be canonical and non-empty")


def _sample_integrity_seal(sample: ExperienceTradeSample) -> str:
    authoritative_fields = (
        ("decision_identity", sample.decision_identity),
        ("source_id", sample.source_id),
        ("fingerprint", sample.fingerprint),
        ("hierarchical_keys", sample.hierarchical_keys),
        ("entry_decision_at", sample.entry_decision_at),
        ("entry_fill_at", sample.entry_fill_at),
        ("exit_fill_at", sample.exit_fill_at),
        ("candidate_action", sample.candidate_action),
        ("entry_side", sample.entry_side),
        ("exit_side", sample.exit_side),
        ("quantity", sample.quantity),
        ("approved_risk_quote", sample.approved_risk_quote),
        ("gross_realized_pnl", sample.gross_realized_pnl),
        ("total_trade_fees_quote", sample.total_trade_fees_quote),
        ("net_realized_pnl", sample.net_realized_pnl),
        ("realized_r_net", sample.realized_r_net),
        ("outcome_classification", sample.outcome_classification),
        ("exit_reason", sample.exit_reason),
        ("duration", sample.duration),
        ("risk_policy_version", sample.risk_policy_version),
        ("safety_policy_version", sample.safety_policy_version),
        ("accounting_entry_fill_id", sample.accounting_entry_fill_id),
        ("accounting_exit_fill_id", sample.accounting_exit_fill_id),
        ("sample_schema_version", sample.sample_schema_version),
    )
    payload = dumps(
        (
            "adaptive-trading-bot/experience-trade-sample-integrity",
            _SAMPLE_INTEGRITY_SEAL_VERSION,
            tuple(
                (name, _canonical_sample_integrity_value(value))
                for name, value in authoritative_fields
            ),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_sample_integrity_value(value: object) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return (
            "enum",
            value.__class__.__module__,
            value.__class__.__qualname__,
            value.name,
            _canonical_sample_integrity_value(value.value),
        )
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"))
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
        return ("tuple", tuple(_canonical_sample_integrity_value(item) for item in value))
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            value.__class__.__module__,
            value.__class__.__qualname__,
            tuple(
                (
                    item.name,
                    _canonical_sample_integrity_value(getattr(value, item.name)),
                )
                for item in fields(value)
            ),
        )
    raise TypeError(f"unsupported sample integrity value: {type(value).__qualname__}")
