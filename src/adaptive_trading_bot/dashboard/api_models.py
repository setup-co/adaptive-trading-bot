"""Lossless JSON transport models for Dashboard read data."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from adaptive_trading_bot.dashboard.models import DashboardSnapshot, DashboardValue


class DashboardApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class DashboardValueResponse(DashboardApiModel):
    availability: str
    value: str | int | bool | list[str] | None
    reason: str | None


class DashboardMarketResponse(DashboardApiModel):
    source_id: str
    symbol: str
    observed_at: datetime
    source_timestamp: DashboardValueResponse
    midpoint: str
    spread_bps: str
    regime: DashboardValueResponse
    volatility: DashboardValueResponse
    technical_score: DashboardValueResponse
    fundamental_score: DashboardValueResponse
    experience_metrics: DashboardValueResponse


class DashboardAccountResponse(DashboardApiModel):
    observed_at: datetime
    quote_currency: str
    initial_equity: str
    equity: str
    day_start_equity: str
    peak_equity: str
    realized_gross_pnl: str
    cumulative_fees: str
    cumulative_net_pnl: str
    locked_open_risk: str
    locked_gross_exposure: str
    account_policy_version: str


class DashboardOpenPositionResponse(DashboardApiModel):
    accounting_fill_id: str
    source_id: str
    symbol: str
    side: str
    quantity: str
    authorized_at: datetime
    eligible_at: datetime
    entry_fill_at: datetime
    entry_reference_price: str
    entry_fill_price: str
    entry_fee: str
    stop_basis: str
    approved_risk: str
    approved_notional: str
    locked_open_risk: str
    locked_gross_exposure: str
    lifecycle_state: str
    risk_policy_version: str
    safety_policy_version: str


class DashboardCompletedTradeResponse(DashboardApiModel):
    accounting_entry_fill_id: str
    accounting_exit_fill_id: str
    source_id: str
    symbol: str
    strategy_id: str
    strategy_version: str
    entry_side: str
    exit_side: str
    quantity: str
    entry_fill_at: datetime
    entry_fill_price: str
    exit_fill_at: datetime
    exit_fill_price: str
    stop_basis: str
    gross_realized_pnl: str
    total_fees: str
    net_realized_pnl: str
    realized_r: str
    duration: str
    outcome_classification: str
    mfe: DashboardValueResponse
    mae: DashboardValueResponse
    process_score: DashboardValueResponse


class DashboardSourceSystemResponse(DashboardApiModel):
    source_id: str
    symbol: str
    observed_at: datetime
    market_data_observed_at: datetime
    feed_integrity_state: str
    exchange_api_health_state: str
    spread_bps: str
    time_sync_offset_ms: str
    volatility_safety_state: str
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int
    safety_status: DashboardValueResponse
    safety_reasons: DashboardValueResponse


class DashboardSystemResponse(DashboardApiModel):
    observed_at: datetime
    sources: tuple[DashboardSourceSystemResponse, ...]
    global_safety_status: DashboardValueResponse
    last_error: DashboardValueResponse
    last_news_update: DashboardValueResponse
    live_trading_enabled: bool


class DashboardSnapshotResponse(DashboardApiModel):
    observed_at: datetime
    markets: tuple[DashboardMarketResponse, ...]
    account: DashboardAccountResponse
    open_positions: tuple[DashboardOpenPositionResponse, ...]
    recent_completed_trades: tuple[DashboardCompletedTradeResponse, ...]
    system: DashboardSystemResponse


class DashboardUnavailableResponse(DashboardApiModel):
    status: Literal["unavailable"] = "unavailable"
    code: str
    message: str


def dashboard_response(snapshot: DashboardSnapshot) -> DashboardSnapshotResponse:
    """Serialize Decimal values as exact strings without domain recomputation."""

    return DashboardSnapshotResponse(
        observed_at=snapshot.observed_at,
        markets=tuple(
            DashboardMarketResponse(
                source_id=item.source_id,
                symbol=item.symbol,
                observed_at=item.observed_at,
                source_timestamp=_value(item.source_timestamp),
                midpoint=str(item.midpoint),
                spread_bps=str(item.spread_bps),
                regime=_value(item.regime),
                volatility=_value(item.volatility),
                technical_score=_value(item.technical_score),
                fundamental_score=_value(item.fundamental_score),
                experience_metrics=_value(item.experience_metrics),
            )
            for item in snapshot.markets
        ),
        account=DashboardAccountResponse(
            observed_at=snapshot.account.observed_at,
            quote_currency=snapshot.account.quote_currency,
            initial_equity=str(snapshot.account.initial_equity),
            equity=str(snapshot.account.equity),
            day_start_equity=str(snapshot.account.day_start_equity),
            peak_equity=str(snapshot.account.peak_equity),
            realized_gross_pnl=str(snapshot.account.realized_gross_pnl),
            cumulative_fees=str(snapshot.account.cumulative_fees),
            cumulative_net_pnl=str(snapshot.account.cumulative_net_pnl),
            locked_open_risk=str(snapshot.account.locked_open_risk),
            locked_gross_exposure=str(snapshot.account.locked_gross_exposure),
            account_policy_version=snapshot.account.account_policy_version,
        ),
        open_positions=tuple(
            DashboardOpenPositionResponse(
                accounting_fill_id=item.accounting_fill_id,
                source_id=item.source_id,
                symbol=item.symbol,
                side=item.side,
                quantity=str(item.quantity),
                authorized_at=item.authorized_at,
                eligible_at=item.eligible_at,
                entry_fill_at=item.entry_fill_at,
                entry_reference_price=str(item.entry_reference_price),
                entry_fill_price=str(item.entry_fill_price),
                entry_fee=str(item.entry_fee),
                stop_basis=str(item.stop_basis),
                approved_risk=str(item.approved_risk),
                approved_notional=str(item.approved_notional),
                locked_open_risk=str(item.locked_open_risk),
                locked_gross_exposure=str(item.locked_gross_exposure),
                lifecycle_state=item.lifecycle_state,
                risk_policy_version=item.risk_policy_version,
                safety_policy_version=item.safety_policy_version,
            )
            for item in snapshot.open_positions
        ),
        recent_completed_trades=tuple(
            DashboardCompletedTradeResponse(
                accounting_entry_fill_id=item.accounting_entry_fill_id,
                accounting_exit_fill_id=item.accounting_exit_fill_id,
                source_id=item.source_id,
                symbol=item.symbol,
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
                entry_side=item.entry_side,
                exit_side=item.exit_side,
                quantity=str(item.quantity),
                entry_fill_at=item.entry_fill_at,
                entry_fill_price=str(item.entry_fill_price),
                exit_fill_at=item.exit_fill_at,
                exit_fill_price=str(item.exit_fill_price),
                stop_basis=str(item.stop_basis),
                gross_realized_pnl=str(item.gross_realized_pnl),
                total_fees=str(item.total_fees),
                net_realized_pnl=str(item.net_realized_pnl),
                realized_r=str(item.realized_r),
                duration=str(item.duration),
                outcome_classification=item.outcome_classification,
                mfe=_value(item.mfe),
                mae=_value(item.mae),
                process_score=_value(item.process_score),
            )
            for item in snapshot.recent_completed_trades
        ),
        system=DashboardSystemResponse(
            observed_at=snapshot.system.observed_at,
            sources=tuple(
                DashboardSourceSystemResponse(
                    source_id=item.source_id,
                    symbol=item.symbol,
                    observed_at=item.observed_at,
                    market_data_observed_at=item.market_data_observed_at,
                    feed_integrity_state=item.feed_integrity_state,
                    exchange_api_health_state=item.exchange_api_health_state,
                    spread_bps=str(item.spread_bps),
                    time_sync_offset_ms=str(item.time_sync_offset_ms),
                    volatility_safety_state=item.volatility_safety_state,
                    suspicious_price_anomaly=item.suspicious_price_anomaly,
                    consecutive_order_rejections=item.consecutive_order_rejections,
                    safety_status=_value(item.safety_status),
                    safety_reasons=_value(item.safety_reasons),
                )
                for item in snapshot.system.sources
            ),
            global_safety_status=_value(snapshot.system.global_safety_status),
            last_error=_value(snapshot.system.last_error),
            last_news_update=_value(snapshot.system.last_news_update),
            live_trading_enabled=snapshot.system.live_trading_enabled,
        ),
    )


def _value[T](value: DashboardValue[T]) -> DashboardValueResponse:
    raw = value.value
    encoded: str | int | bool | list[str] | None
    if isinstance(raw, datetime):
        encoded = raw.isoformat().replace("+00:00", "Z")
    elif isinstance(raw, Decimal | timedelta):
        encoded = str(raw)
    elif isinstance(raw, tuple):
        encoded = [str(item) for item in raw]
    else:
        encoded = raw if isinstance(raw, str | int | bool) else None
    return DashboardValueResponse(
        availability=value.availability.value,
        value=encoded,
        reason=value.reason,
    )
