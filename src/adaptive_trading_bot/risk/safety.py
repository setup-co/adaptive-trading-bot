"""Stateless deterministic operational safety gates for risk decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.risk.core import RiskDecision, RiskDecisionStatus, RiskReasonCode
from adaptive_trading_bot.strategies.core import CandidateAction, SourceIdentity

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class FeedIntegrityState(StrEnum):
    """Upstream assessment of market-feed integrity."""

    OK = "OK"
    CORRUPT = "CORRUPT"
    BROKEN = "BROKEN"


class ExchangeApiHealthState(StrEnum):
    """Upstream assessment of exchange API health."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class VolatilitySafetyState(StrEnum):
    """Environment-neutral volatility classification for entry safety."""

    CLASSIFIED_NORMAL = "CLASSIFIED_NORMAL"
    CLASSIFIED_HIGH = "CLASSIFIED_HIGH"
    EXTREME = "EXTREME"
    UNCLASSIFIED = "UNCLASSIFIED"


class OperationalSafetyStatus(StrEnum):
    """Outcome after operational safety restricts a risk decision."""

    NO_ACTION = "NO_ACTION"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"
    SAFE_MODE = "SAFE_MODE"
    CLEARED = "CLEARED"


class OperationalSafetyReasonCode(StrEnum):
    """Canonical evidence and blockers emitted in deterministic gate order."""

    RISK_APPROVAL_VALID = "RISK_APPROVAL_VALID"
    RISK_NO_ACTION = "RISK_NO_ACTION"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_DECISION_INCONSISTENT = "RISK_DECISION_INCONSISTENT"
    HEALTH_IDENTITY_MISMATCH = "HEALTH_IDENTITY_MISMATCH"
    HEALTH_TIME_MISMATCH = "HEALTH_TIME_MISMATCH"
    DAILY_LOSS_HALT = "DAILY_LOSS_HALT"
    DRAWDOWN_HALT_REVIEW = "DRAWDOWN_HALT_REVIEW"
    FUTURE_MARKET_DATA_TIMESTAMP = "FUTURE_MARKET_DATA_TIMESTAMP"
    MARKET_DATA_FRESH = "MARKET_DATA_FRESH"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    FEED_INTEGRITY_OK = "FEED_INTEGRITY_OK"
    CORRUPT_FEED = "CORRUPT_FEED"
    BROKEN_FEED = "BROKEN_FEED"
    EXCHANGE_API_HEALTHY = "EXCHANGE_API_HEALTHY"
    EXCHANGE_API_DEGRADED = "EXCHANGE_API_DEGRADED"
    EXCHANGE_API_DOWN = "EXCHANGE_API_DOWN"
    SPREAD_WITHIN_LIMIT = "SPREAD_WITHIN_LIMIT"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    TIME_SYNC_WITHIN_LIMIT = "TIME_SYNC_WITHIN_LIMIT"
    TIME_SYNC_FAULT = "TIME_SYNC_FAULT"
    VOLATILITY_CLASSIFIED_SAFE = "VOLATILITY_CLASSIFIED_SAFE"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    UNCLASSIFIED_VOLATILITY = "UNCLASSIFIED_VOLATILITY"
    NO_PRICE_ANOMALY = "NO_PRICE_ANOMALY"
    SUSPICIOUS_PRICE_ANOMALY = "SUSPICIOUS_PRICE_ANOMALY"
    ORDER_REJECTION_COUNT_WITHIN_LIMIT = "ORDER_REJECTION_COUNT_WITHIN_LIMIT"
    REPEATED_ORDER_REJECTIONS = "REPEATED_ORDER_REJECTIONS"
    ENTRY_CLEARED = "ENTRY_CLEARED"


_SAFE_MODE_REASONS = frozenset(
    {
        OperationalSafetyReasonCode.RISK_DECISION_INCONSISTENT,
        OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH,
        OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH,
        OperationalSafetyReasonCode.DAILY_LOSS_HALT,
        OperationalSafetyReasonCode.DRAWDOWN_HALT_REVIEW,
        OperationalSafetyReasonCode.FUTURE_MARKET_DATA_TIMESTAMP,
        OperationalSafetyReasonCode.STALE_MARKET_DATA,
        OperationalSafetyReasonCode.CORRUPT_FEED,
        OperationalSafetyReasonCode.BROKEN_FEED,
        OperationalSafetyReasonCode.EXCHANGE_API_DEGRADED,
        OperationalSafetyReasonCode.EXCHANGE_API_DOWN,
        OperationalSafetyReasonCode.SPREAD_TOO_WIDE,
        OperationalSafetyReasonCode.TIME_SYNC_FAULT,
        OperationalSafetyReasonCode.EXTREME_VOLATILITY,
        OperationalSafetyReasonCode.UNCLASSIFIED_VOLATILITY,
        OperationalSafetyReasonCode.SUSPICIOUS_PRICE_ANOMALY,
        OperationalSafetyReasonCode.REPEATED_ORDER_REJECTIONS,
    }
)


@dataclass(frozen=True, slots=True)
class OperationalSafetyConfiguration:
    """Versioned V1 operational circuit-breaker thresholds."""

    max_market_data_age_seconds: int = 120
    max_spread_bps: Decimal = Decimal("10.0")
    max_abs_time_sync_offset_ms: Decimal = Decimal("1000")
    max_consecutive_order_rejections: int = 3
    safety_policy_version: str = field(init=False, default="1.0.0")

    def __post_init__(self) -> None:
        for integer_name, integer_value in (
            ("max_market_data_age_seconds", self.max_market_data_age_seconds),
            ("max_consecutive_order_rejections", self.max_consecutive_order_rejections),
        ):
            if type(integer_value) is not int:
                raise TypeError(f"{integer_name} must be an int and not bool")
        if self.max_market_data_age_seconds < 0:
            raise ValueError("max_market_data_age_seconds must be non-negative")
        if self.max_consecutive_order_rejections < 1:
            raise ValueError("max_consecutive_order_rejections must be at least one")
        for decimal_name, decimal_value in (
            ("max_spread_bps", self.max_spread_bps),
            ("max_abs_time_sync_offset_ms", self.max_abs_time_sync_offset_ms),
        ):
            if not isinstance(decimal_value, Decimal):
                raise TypeError(f"{decimal_name} must be a Decimal")
            if not decimal_value.is_finite() or decimal_value < 0:
                raise ValueError(f"{decimal_name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class OperationalHealthSnapshot:
    """Explicit already-observed operational facts for one risk decision."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    market_data_observed_at: datetime
    feed_integrity_state: FeedIntegrityState
    exchange_api_health_state: ExchangeApiHealthState
    spread_bps: Decimal
    time_sync_offset_ms: Decimal
    volatility_safety_state: VolatilitySafetyState
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceIdentity):
            raise TypeError("source_id must implement SourceIdentity")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("symbol must be a TradingSymbol")
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="operational health observed_at"),
        )
        object.__setattr__(
            self,
            "market_data_observed_at",
            require_utc(
                self.market_data_observed_at,
                field="operational health market_data_observed_at",
            ),
        )
        if not isinstance(self.feed_integrity_state, FeedIntegrityState):
            raise TypeError("feed_integrity_state must be a FeedIntegrityState")
        if not isinstance(self.exchange_api_health_state, ExchangeApiHealthState):
            raise TypeError("exchange_api_health_state must be an ExchangeApiHealthState")
        if not isinstance(self.volatility_safety_state, VolatilitySafetyState):
            raise TypeError("volatility_safety_state must be a VolatilitySafetyState")
        if not isinstance(self.spread_bps, Decimal):
            raise TypeError("spread_bps must be a Decimal")
        if not self.spread_bps.is_finite() or self.spread_bps < 0:
            raise ValueError("spread_bps must be finite and non-negative")
        if not isinstance(self.time_sync_offset_ms, Decimal):
            raise TypeError("time_sync_offset_ms must be a Decimal")
        if not self.time_sync_offset_ms.is_finite():
            raise ValueError("time_sync_offset_ms must be finite")
        if type(self.suspicious_price_anomaly) is not bool:
            raise TypeError("suspicious_price_anomaly must be a bool")
        if type(self.consecutive_order_rejections) is not int:
            raise TypeError("consecutive_order_rejections must be an int and not bool")
        if self.consecutive_order_rejections < 0:
            raise ValueError("consecutive_order_rejections must be non-negative")


@dataclass(frozen=True, slots=True)
class OperationalSafetyDecision:
    """Immutable operational result; CLEARED still is not an order."""

    safety_policy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    health_source_id: SourceIdentity
    health_symbol: TradingSymbol
    health_observed_at: datetime
    input_risk_status: RiskDecisionStatus
    risk_policy_version: str
    status: OperationalSafetyStatus
    reasons: tuple[OperationalSafetyReasonCode, ...]
    market_data_observed_at: datetime
    market_data_age: timedelta
    max_market_data_age_seconds: int
    feed_integrity_state: FeedIntegrityState
    exchange_api_health_state: ExchangeApiHealthState
    spread_bps: Decimal
    max_spread_bps: Decimal
    time_sync_offset_ms: Decimal
    max_abs_time_sync_offset_ms: Decimal
    volatility_safety_state: VolatilitySafetyState
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int
    max_consecutive_order_rejections: int
    risk_requires_halt_review: bool
    approved_risk_stop_basis: Decimal | None
    approved_quantity: Decimal | None
    approved_notional_quote: Decimal | None
    approved_risk_quote: Decimal | None
    entry_permitted: bool


@dataclass(frozen=True, slots=True)
class OperationalSafetyGate:
    """Preserve or restrict a Risk Manager decision using explicit health facts."""

    configuration: OperationalSafetyConfiguration = field(
        default_factory=OperationalSafetyConfiguration
    )

    def evaluate(
        self,
        risk_decision: RiskDecision,
        health: OperationalHealthSnapshot,
    ) -> OperationalSafetyDecision:
        """Evaluate every circuit breaker and collect canonical audit evidence."""

        with localcontext(_DECIMAL_CONTEXT):
            reasons: list[OperationalSafetyReasonCode] = []
            risk_consistent = _risk_decision_is_consistent(risk_decision)
            if not risk_consistent:
                reasons.append(OperationalSafetyReasonCode.RISK_DECISION_INCONSISTENT)
            elif risk_decision.status is RiskDecisionStatus.APPROVED:
                reasons.append(OperationalSafetyReasonCode.RISK_APPROVAL_VALID)
            elif risk_decision.status is RiskDecisionStatus.NO_ACTION:
                reasons.append(OperationalSafetyReasonCode.RISK_NO_ACTION)
            else:
                reasons.append(OperationalSafetyReasonCode.RISK_REJECTED)

            if health.source_id != risk_decision.source_id or health.symbol != risk_decision.symbol:
                reasons.append(OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH)
            if health.observed_at != risk_decision.observed_at:
                reasons.append(OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH)

            if RiskReasonCode.DAILY_LOSS_LIMIT_REACHED in risk_decision.reasons:
                reasons.append(OperationalSafetyReasonCode.DAILY_LOSS_HALT)
            if (
                risk_decision.requires_halt_review
                or RiskReasonCode.DRAWDOWN_LIMIT_REACHED in risk_decision.reasons
            ):
                reasons.append(OperationalSafetyReasonCode.DRAWDOWN_HALT_REVIEW)

            market_data_age = health.observed_at - health.market_data_observed_at
            if market_data_age < timedelta(0):
                reasons.append(OperationalSafetyReasonCode.FUTURE_MARKET_DATA_TIMESTAMP)
            elif _timedelta_exceeds_seconds(
                market_data_age, self.configuration.max_market_data_age_seconds
            ):
                reasons.append(OperationalSafetyReasonCode.STALE_MARKET_DATA)
            else:
                reasons.append(OperationalSafetyReasonCode.MARKET_DATA_FRESH)

            reasons.append(_feed_reason(health.feed_integrity_state))
            reasons.append(_exchange_reason(health.exchange_api_health_state))
            reasons.append(
                OperationalSafetyReasonCode.SPREAD_TOO_WIDE
                if health.spread_bps > self.configuration.max_spread_bps
                else OperationalSafetyReasonCode.SPREAD_WITHIN_LIMIT
            )
            reasons.append(
                OperationalSafetyReasonCode.TIME_SYNC_FAULT
                if abs(health.time_sync_offset_ms) > self.configuration.max_abs_time_sync_offset_ms
                else OperationalSafetyReasonCode.TIME_SYNC_WITHIN_LIMIT
            )
            reasons.append(_volatility_reason(health.volatility_safety_state))
            reasons.append(
                OperationalSafetyReasonCode.SUSPICIOUS_PRICE_ANOMALY
                if health.suspicious_price_anomaly
                else OperationalSafetyReasonCode.NO_PRICE_ANOMALY
            )
            reasons.append(
                OperationalSafetyReasonCode.REPEATED_ORDER_REJECTIONS
                if health.consecutive_order_rejections
                >= self.configuration.max_consecutive_order_rejections
                else OperationalSafetyReasonCode.ORDER_REJECTION_COUNT_WITHIN_LIMIT
            )

            has_safe_mode_reason = any(reason in _SAFE_MODE_REASONS for reason in reasons)
            if has_safe_mode_reason:
                status = OperationalSafetyStatus.SAFE_MODE
            elif risk_decision.status is RiskDecisionStatus.NO_ACTION:
                status = OperationalSafetyStatus.NO_ACTION
            elif risk_decision.status is RiskDecisionStatus.REJECTED:
                status = OperationalSafetyStatus.BLOCKED_BY_RISK
            else:
                status = OperationalSafetyStatus.CLEARED
                reasons.append(OperationalSafetyReasonCode.ENTRY_CLEARED)

            cleared = status is OperationalSafetyStatus.CLEARED
            return OperationalSafetyDecision(
                safety_policy_version=self.configuration.safety_policy_version,
                source_id=risk_decision.source_id,
                symbol=risk_decision.symbol,
                observed_at=risk_decision.observed_at,
                health_source_id=health.source_id,
                health_symbol=health.symbol,
                health_observed_at=health.observed_at,
                input_risk_status=risk_decision.status,
                risk_policy_version=risk_decision.risk_policy_version,
                status=status,
                reasons=tuple(reasons),
                market_data_observed_at=health.market_data_observed_at,
                market_data_age=market_data_age,
                max_market_data_age_seconds=self.configuration.max_market_data_age_seconds,
                feed_integrity_state=health.feed_integrity_state,
                exchange_api_health_state=health.exchange_api_health_state,
                spread_bps=health.spread_bps,
                max_spread_bps=self.configuration.max_spread_bps,
                time_sync_offset_ms=health.time_sync_offset_ms,
                max_abs_time_sync_offset_ms=self.configuration.max_abs_time_sync_offset_ms,
                volatility_safety_state=health.volatility_safety_state,
                suspicious_price_anomaly=health.suspicious_price_anomaly,
                consecutive_order_rejections=health.consecutive_order_rejections,
                max_consecutive_order_rejections=(
                    self.configuration.max_consecutive_order_rejections
                ),
                risk_requires_halt_review=risk_decision.requires_halt_review,
                approved_risk_stop_basis=(
                    risk_decision.approved_risk_stop_basis if cleared else None
                ),
                approved_quantity=risk_decision.approved_quantity if cleared else None,
                approved_notional_quote=(
                    risk_decision.approved_notional_quote if cleared else None
                ),
                approved_risk_quote=risk_decision.approved_risk_quote if cleared else None,
                entry_permitted=cleared,
            )


def _risk_decision_is_consistent(decision: RiskDecision) -> bool:
    if type(decision.requires_halt_review) is not bool:
        return False
    if decision.status is RiskDecisionStatus.NO_ACTION:
        return decision.strategy_candidate_action is CandidateAction.NO_TRADE
    if decision.status is RiskDecisionStatus.REJECTED:
        return True
    if decision.status is not RiskDecisionStatus.APPROVED:
        return False
    if decision.strategy_candidate_action not in {
        CandidateAction.LONG_CANDIDATE,
        CandidateAction.SHORT_CANDIDATE,
    }:
        return False
    if decision.quote_currency != "USDT" or decision.symbol.quote != "USDT":
        return False
    if decision.risk_snapshot_observed_at != decision.observed_at:
        return False
    positive_fields = (
        decision.reference_price,
        decision.approved_risk_stop_basis,
        decision.stop_distance,
        decision.approved_quantity,
        decision.approved_notional_quote,
        decision.approved_risk_quote,
        decision.per_trade_risk_budget_quote,
        decision.simultaneous_risk_cap_quote,
        decision.gross_exposure_cap_quote,
    )
    if not all(_is_positive_decimal(value) for value in positive_fields):
        return False
    nonnegative_fields = (
        decision.current_open_risk_quote,
        decision.current_gross_exposure_quote,
        decision.resulting_open_risk_quote,
        decision.resulting_gross_exposure_quote,
    )
    if not all(_is_nonnegative_decimal(value) for value in nonnegative_fields):
        return False
    assert isinstance(decision.reference_price, Decimal)
    assert isinstance(decision.approved_risk_stop_basis, Decimal)
    assert isinstance(decision.stop_distance, Decimal)
    assert isinstance(decision.approved_quantity, Decimal)
    assert isinstance(decision.approved_notional_quote, Decimal)
    assert isinstance(decision.approved_risk_quote, Decimal)
    assert isinstance(decision.resulting_open_risk_quote, Decimal)
    assert isinstance(decision.resulting_gross_exposure_quote, Decimal)
    expected_distance = (
        decision.reference_price - decision.approved_risk_stop_basis
        if decision.strategy_candidate_action is CandidateAction.LONG_CANDIDATE
        else decision.approved_risk_stop_basis - decision.reference_price
    )
    return (
        expected_distance == decision.stop_distance
        and decision.approved_notional_quote
        == decision.approved_quantity * decision.reference_price
        and decision.approved_risk_quote == decision.approved_quantity * decision.stop_distance
        and decision.resulting_open_risk_quote
        == decision.current_open_risk_quote + decision.approved_risk_quote
        and decision.resulting_gross_exposure_quote
        == decision.current_gross_exposure_quote + decision.approved_notional_quote
        and decision.approved_risk_quote <= decision.per_trade_risk_budget_quote
        and decision.resulting_open_risk_quote <= decision.simultaneous_risk_cap_quote
        and decision.resulting_gross_exposure_quote <= decision.gross_exposure_cap_quote
        and not decision.requires_halt_review
        and RiskReasonCode.DAILY_LOSS_LIMIT_REACHED not in decision.reasons
        and RiskReasonCode.DRAWDOWN_LIMIT_REACHED not in decision.reasons
    )


def _is_positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _is_nonnegative_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _timedelta_exceeds_seconds(value: timedelta, seconds: int) -> bool:
    """Compare without float conversion or overflowing a large configured timedelta."""

    allowed_days, allowed_seconds = divmod(seconds, 86_400)
    return (value.days, value.seconds, value.microseconds) > (
        allowed_days,
        allowed_seconds,
        0,
    )


def _feed_reason(state: FeedIntegrityState) -> OperationalSafetyReasonCode:
    if state is FeedIntegrityState.OK:
        return OperationalSafetyReasonCode.FEED_INTEGRITY_OK
    if state is FeedIntegrityState.CORRUPT:
        return OperationalSafetyReasonCode.CORRUPT_FEED
    return OperationalSafetyReasonCode.BROKEN_FEED


def _exchange_reason(state: ExchangeApiHealthState) -> OperationalSafetyReasonCode:
    if state is ExchangeApiHealthState.HEALTHY:
        return OperationalSafetyReasonCode.EXCHANGE_API_HEALTHY
    if state is ExchangeApiHealthState.DEGRADED:
        return OperationalSafetyReasonCode.EXCHANGE_API_DEGRADED
    return OperationalSafetyReasonCode.EXCHANGE_API_DOWN


def _volatility_reason(state: VolatilitySafetyState) -> OperationalSafetyReasonCode:
    if state in {
        VolatilitySafetyState.CLASSIFIED_NORMAL,
        VolatilitySafetyState.CLASSIFIED_HIGH,
    }:
        return OperationalSafetyReasonCode.VOLATILITY_CLASSIFIED_SAFE
    if state is VolatilitySafetyState.EXTREME:
        return OperationalSafetyReasonCode.EXTREME_VOLATILITY
    return OperationalSafetyReasonCode.UNCLASSIFIED_VOLATILITY
