"""Immutable read models for detached Dashboard observations."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.domain.clock import require_utc


class DashboardAvailability(StrEnum):
    """Whether one dashboard fact has authoritative backing."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class DashboardValue[T]:
    """One explicitly available or unavailable dashboard value."""

    availability: DashboardAvailability
    value: T | None
    reason: str | None

    @classmethod
    def available(cls, value: T) -> "DashboardValue[T]":
        return cls(DashboardAvailability.AVAILABLE, value, None)

    @classmethod
    def unavailable(cls, reason: str) -> "DashboardValue[T]":
        return cls(DashboardAvailability.UNAVAILABLE, None, reason)

    def __post_init__(self) -> None:
        if self.availability is DashboardAvailability.AVAILABLE:
            if self.value is None or self.reason is not None:
                raise ValueError("available dashboard values require only a value")
        elif self.value is not None or not self.reason:
            raise ValueError("unavailable dashboard values require only a reason")


@dataclass(frozen=True, slots=True)
class DashboardMarketSnapshot:
    source_id: str
    symbol: str
    observed_at: datetime
    source_timestamp: DashboardValue[datetime]
    midpoint: Decimal
    spread_bps: Decimal
    regime: DashboardValue[str]
    volatility: DashboardValue[str]
    technical_score: DashboardValue[int]
    fundamental_score: DashboardValue[Decimal]
    experience_metrics: DashboardValue[str]


@dataclass(frozen=True, slots=True)
class DashboardPaperAccountSnapshot:
    observed_at: datetime
    quote_currency: str
    initial_equity: Decimal
    equity: Decimal
    day_start_equity: Decimal
    peak_equity: Decimal
    realized_gross_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_net_pnl: Decimal
    locked_open_risk: Decimal
    locked_gross_exposure: Decimal
    account_policy_version: str


@dataclass(frozen=True, slots=True)
class DashboardOpenPosition:
    accounting_fill_id: str
    source_id: str
    symbol: str
    side: str
    quantity: Decimal
    authorized_at: datetime
    eligible_at: datetime
    entry_fill_at: datetime
    entry_reference_price: Decimal
    entry_fill_price: Decimal
    entry_fee: Decimal
    stop_basis: Decimal
    approved_risk: Decimal
    approved_notional: Decimal
    locked_open_risk: Decimal
    locked_gross_exposure: Decimal
    lifecycle_state: str
    risk_policy_version: str
    safety_policy_version: str


@dataclass(frozen=True, slots=True)
class DashboardCompletedTrade:
    accounting_entry_fill_id: str
    accounting_exit_fill_id: str
    source_id: str
    symbol: str
    strategy_id: str
    strategy_version: str
    entry_side: str
    exit_side: str
    quantity: Decimal
    entry_fill_at: datetime
    entry_fill_price: Decimal
    exit_fill_at: datetime
    exit_fill_price: Decimal
    stop_basis: Decimal
    gross_realized_pnl: Decimal
    total_fees: Decimal
    net_realized_pnl: Decimal
    realized_r: Decimal
    duration: timedelta
    outcome_classification: str
    mfe: DashboardValue[Decimal]
    mae: DashboardValue[Decimal]
    process_score: DashboardValue[Decimal]


@dataclass(frozen=True, slots=True)
class DashboardSourceSystemSnapshot:
    source_id: str
    symbol: str
    observed_at: datetime
    market_data_observed_at: datetime
    feed_integrity_state: str
    exchange_api_health_state: str
    spread_bps: Decimal
    time_sync_offset_ms: Decimal
    volatility_safety_state: str
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int
    safety_status: DashboardValue[str]
    safety_reasons: DashboardValue[tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class DashboardSystemSnapshot:
    observed_at: datetime
    sources: tuple[DashboardSourceSystemSnapshot, ...]
    global_safety_status: DashboardValue[str]
    last_error: DashboardValue[str]
    last_news_update: DashboardValue[datetime]
    live_trading_enabled: bool


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    observed_at: datetime
    markets: tuple[DashboardMarketSnapshot, ...]
    account: DashboardPaperAccountSnapshot
    open_positions: tuple[DashboardOpenPosition, ...]
    recent_completed_trades: tuple[DashboardCompletedTrade, ...]
    system: DashboardSystemSnapshot

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="dashboard observed_at"),
        )
        if (
            self.account.observed_at != self.observed_at
            or self.system.observed_at != self.observed_at
        ):
            raise ValueError("dashboard account and system times must match observed_at")
