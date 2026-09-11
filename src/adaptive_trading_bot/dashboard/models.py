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
        self.validate()

    def validate(self) -> None:
        """Revalidate availability and any datetime carried across a boundary."""

        if self.availability is DashboardAvailability.AVAILABLE:
            if self.value is None or self.reason is not None:
                raise ValueError("available dashboard values require only a value")
            if isinstance(self.value, datetime):
                require_utc(self.value, field="available dashboard datetime")
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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Require UTC market and optional source provenance timestamps."""

        require_utc(self.observed_at, field="dashboard market observed_at")
        _validate_datetime_value(
            self.source_timestamp,
            field="dashboard market source_timestamp",
        )


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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Require a UTC account observation timestamp."""

        require_utc(self.observed_at, field="dashboard account observed_at")


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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Require UTC timestamps throughout the entry lifecycle."""

        require_utc(self.authorized_at, field="dashboard position authorized_at")
        require_utc(self.eligible_at, field="dashboard position eligible_at")
        require_utc(self.entry_fill_at, field="dashboard position entry_fill_at")


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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Require UTC timestamps for both authoritative fills."""

        require_utc(self.entry_fill_at, field="dashboard trade entry_fill_at")
        require_utc(self.exit_fill_at, field="dashboard trade exit_fill_at")


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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Require UTC system-observation and source-data timestamps."""

        require_utc(self.observed_at, field="dashboard source-system observed_at")
        require_utc(
            self.market_data_observed_at,
            field="dashboard source-system market_data_observed_at",
        )


@dataclass(frozen=True, slots=True)
class DashboardSystemSnapshot:
    observed_at: datetime
    sources: tuple[DashboardSourceSystemSnapshot, ...]
    global_safety_status: DashboardValue[str]
    last_error: DashboardValue[str]
    last_news_update: DashboardValue[datetime]
    live_trading_enabled: bool

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate UTC across system facts and optional news provenance."""

        require_utc(self.observed_at, field="dashboard system observed_at")
        for source in self.sources:
            source.validate()
        _validate_datetime_value(
            self.last_news_update,
            field="dashboard system last_news_update",
        )


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    observed_at: datetime
    markets: tuple[DashboardMarketSnapshot, ...]
    account: DashboardPaperAccountSnapshot
    open_positions: tuple[DashboardOpenPosition, ...]
    recent_completed_trades: tuple[DashboardCompletedTrade, ...]
    system: DashboardSystemSnapshot

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate the complete detached Dashboard graph before serving it."""

        require_utc(self.observed_at, field="dashboard observed_at")
        for market in self.markets:
            market.validate()
        self.account.validate()
        for position in self.open_positions:
            position.validate()
        for trade in self.recent_completed_trades:
            trade.validate()
        self.system.validate()
        if (
            self.account.observed_at != self.observed_at
            or self.system.observed_at != self.observed_at
        ):
            raise ValueError("dashboard account and system times must match observed_at")


def _validate_datetime_value(
    value: DashboardValue[datetime],
    *,
    field: str,
) -> None:
    value.validate()
    if value.availability is DashboardAvailability.AVAILABLE:
        if not isinstance(value.value, datetime):
            raise TypeError(f"{field} must carry a datetime when available")
        require_utc(value.value, field=field)
