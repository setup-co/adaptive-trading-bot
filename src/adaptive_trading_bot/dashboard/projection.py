"""Pure projection from authoritative domain snapshots into Dashboard read models."""

from datetime import datetime

from adaptive_trading_bot.dashboard.models import (
    DashboardCompletedTrade,
    DashboardMarketSnapshot,
    DashboardOpenPosition,
    DashboardPaperAccountSnapshot,
    DashboardSnapshot,
    DashboardSourceSystemSnapshot,
    DashboardSystemSnapshot,
    DashboardValue,
)
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import CompletedTradeJournalSnapshot
from adaptive_trading_bot.market_data import LiveMarketQuote
from adaptive_trading_bot.paper import PaperAccountSnapshot, PaperPortfolioCoordinatorSnapshot
from adaptive_trading_bot.regime import MultiTimeframeRegimeSnapshot
from adaptive_trading_bot.risk import OperationalHealthSnapshot, OperationalSafetyDecision

_FUTURE_PHASE_REASON = "not available from an authoritative V1 source"


class DashboardProjectionError(ValueError):
    """Supplied authorities cannot form one truthful coherent snapshot."""


def project_dashboard_snapshot(
    observed_at: datetime,
    paper: PaperPortfolioCoordinatorSnapshot,
    account: PaperAccountSnapshot,
    completed_trades: CompletedTradeJournalSnapshot,
    *,
    quotes: tuple[LiveMarketQuote, ...] = (),
    regimes: tuple[MultiTimeframeRegimeSnapshot, ...] = (),
    health: tuple[OperationalHealthSnapshot, ...] = (),
    safety: tuple[OperationalSafetyDecision, ...] = (),
    live_trading_enabled: bool = False,
    last_error: str | None = None,
) -> DashboardSnapshot:
    """Copy authoritative values exactly; never derive trading or accounting facts."""

    instant = require_utc(observed_at, field="dashboard observed_at")
    if account.observed_at != instant:
        raise DashboardProjectionError("account snapshot time must match dashboard time")
    if type(live_trading_enabled) is not bool:
        raise TypeError("live_trading_enabled must be a bool")
    for values, name in (
        (quotes, "quotes"),
        (regimes, "regimes"),
        (health, "health"),
        (safety, "safety"),
    ):
        if not isinstance(values, tuple):
            raise TypeError(f"dashboard {name} must be an immutable tuple")

    regime_by_key = _unique_by_market(regimes, name="regime")
    _require_unique_market_quotes(quotes)
    markets = tuple(
        sorted(
            (
                _project_market(item, regime_by_key.get((item.source_id.value, str(item.symbol))))
                for item in quotes
            ),
            key=lambda item: (item.source_id, item.symbol),
        )
    )
    open_positions = tuple(
        sorted(
            (
                DashboardOpenPosition(
                    item.accounting_fill_id,
                    item.source_id.value,
                    str(item.symbol),
                    item.side.value,
                    item.quantity,
                    item.authorized_at,
                    item.eligible_at,
                    item.fill_at,
                    item.reference_price,
                    item.fill_price,
                    item.fee_quote,
                    item.stop_basis,
                    item.approved_risk_quote,
                    item.approved_notional_quote,
                    item.locked_open_risk_quote,
                    item.locked_gross_exposure_quote,
                    "FILLED",
                    item.risk_policy_version,
                    item.safety_policy_version,
                )
                for item in paper.open_positions
            ),
            key=lambda item: (item.symbol, item.accounting_fill_id),
        )
    )
    trades = tuple(
        sorted(
            (
                DashboardCompletedTrade(
                    item.accounting_entry_fill_id,
                    item.accounting_exit_fill_id,
                    item.source_id.value,
                    str(item.symbol),
                    item.strategy_id,
                    item.strategy_version,
                    item.entry_side.value,
                    item.exit_side.value,
                    item.quantity,
                    item.entry_fill_at,
                    item.entry_fill_price,
                    item.exit_fill_at,
                    item.exit_fill_price,
                    item.stop_basis,
                    item.gross_realized_pnl,
                    item.total_trade_fees_quote,
                    item.net_realized_pnl,
                    item.realized_r_net,
                    item.duration,
                    item.outcome_classification.value,
                    DashboardValue.unavailable(_FUTURE_PHASE_REASON),
                    DashboardValue.unavailable(_FUTURE_PHASE_REASON),
                    DashboardValue.unavailable(_FUTURE_PHASE_REASON),
                )
                for item in completed_trades.entries
            ),
            key=lambda item: (item.exit_fill_at, item.accounting_exit_fill_id),
        )
    )
    system_sources = _project_system_sources(health, safety)
    return DashboardSnapshot(
        instant,
        markets,
        DashboardPaperAccountSnapshot(
            account.observed_at,
            account.quote_currency,
            account.initial_equity_quote,
            account.account_equity_quote,
            account.day_start_equity_quote,
            account.peak_equity_quote,
            account.realized_gross_pnl_quote,
            account.cumulative_fees_quote,
            account.cumulative_net_pnl_quote,
            paper.locked_open_risk_quote,
            paper.locked_gross_exposure_quote,
            account.account_policy_version,
        ),
        open_positions,
        trades,
        DashboardSystemSnapshot(
            instant,
            system_sources,
            DashboardValue.unavailable("no authoritative global safety status exists"),
            (
                DashboardValue.unavailable("no authoritative error status supplied")
                if last_error is None
                else DashboardValue.available(last_error)
            ),
            DashboardValue.unavailable(_FUTURE_PHASE_REASON),
            live_trading_enabled,
        ),
    )


def _project_market(
    quote: LiveMarketQuote,
    regime: MultiTimeframeRegimeSnapshot | None,
) -> DashboardMarketSnapshot:
    quote.validate()
    if regime is not None and (
        regime.source_id != quote.source_id or regime.symbol != quote.symbol
    ):
        raise DashboardProjectionError("regime identity does not match live quote")
    return DashboardMarketSnapshot(
        quote.source_id.value,
        str(quote.symbol),
        quote.observed_at,
        (
            DashboardValue.unavailable("public quote source timestamp unavailable")
            if quote.source_timestamp is None
            else DashboardValue.available(quote.source_timestamp)
        ),
        quote.midpoint,
        quote.spread_bps,
        (
            DashboardValue.unavailable("no authoritative regime snapshot supplied")
            if regime is None
            else DashboardValue.available(regime.regime_15m.regime_label.value)
        ),
        (
            DashboardValue.unavailable("no authoritative volatility snapshot supplied")
            if regime is None
            else DashboardValue.available(regime.regime_15m.volatility_state.value)
        ),
        (
            DashboardValue.unavailable("no authoritative technical score supplied")
            if regime is None
            else DashboardValue.available(regime.regime_15m.direction_score)
        ),
        DashboardValue.unavailable(_FUTURE_PHASE_REASON),
        DashboardValue.unavailable(_FUTURE_PHASE_REASON),
    )


def _project_system_sources(
    health: tuple[OperationalHealthSnapshot, ...],
    safety: tuple[OperationalSafetyDecision, ...],
) -> tuple[DashboardSourceSystemSnapshot, ...]:
    health_by_key = _unique_health(health)
    safety_by_key = _unique_safety(safety)
    projected = []
    for key in sorted(set(health_by_key) | set(safety_by_key)):
        item = health_by_key.get(key)
        decision = safety_by_key.get(key)
        if item is not None and decision is not None:
            _require_matching_health_audit(item, decision)
        source = item if item is not None else decision
        assert source is not None
        projected.append(
            DashboardSourceSystemSnapshot(
                key[0],
                key[1],
                key[2],
                source.market_data_observed_at,
                source.feed_integrity_state.value,
                source.exchange_api_health_state.value,
                source.spread_bps,
                source.time_sync_offset_ms,
                source.volatility_safety_state.value,
                source.suspicious_price_anomaly,
                source.consecutive_order_rejections,
                (
                    DashboardValue.unavailable("no authoritative safety decision supplied")
                    if decision is None
                    else DashboardValue.available(decision.status.value)
                ),
                (
                    DashboardValue.unavailable("no authoritative safety decision supplied")
                    if decision is None
                    else DashboardValue.available(
                        tuple(reason.value for reason in decision.reasons)
                    )
                ),
            )
        )
    return tuple(sorted(projected, key=lambda item: (item.source_id, item.symbol)))


def _unique_by_market(
    regimes: tuple[MultiTimeframeRegimeSnapshot, ...],
    *,
    name: str,
) -> dict[tuple[str, str], MultiTimeframeRegimeSnapshot]:
    result: dict[tuple[str, str], MultiTimeframeRegimeSnapshot] = {}
    for item in regimes:
        key = item.source_id.value, str(item.symbol)
        if key in result:
            raise DashboardProjectionError(f"duplicate {name} identity")
        result[key] = item
    return result


def _require_unique_market_quotes(quotes: tuple[LiveMarketQuote, ...]) -> None:
    keys = tuple((item.source_id.value, str(item.symbol)) for item in quotes)
    if len(set(keys)) != len(keys):
        raise DashboardProjectionError("duplicate live quote identity")


def _unique_health(
    items: tuple[OperationalHealthSnapshot, ...],
) -> dict[tuple[str, str, datetime], OperationalHealthSnapshot]:
    result: dict[tuple[str, str, datetime], OperationalHealthSnapshot] = {}
    for item in items:
        key = item.source_id.value, str(item.symbol), item.observed_at
        if key in result:
            raise DashboardProjectionError("duplicate operational health identity")
        result[key] = item
    return result


def _unique_safety(
    items: tuple[OperationalSafetyDecision, ...],
) -> dict[tuple[str, str, datetime], OperationalSafetyDecision]:
    result: dict[tuple[str, str, datetime], OperationalSafetyDecision] = {}
    for item in items:
        key = item.health_source_id.value, str(item.health_symbol), item.health_observed_at
        if key in result:
            raise DashboardProjectionError("duplicate operational safety identity")
        result[key] = item
    return result


def _require_matching_health_audit(
    health: OperationalHealthSnapshot,
    safety: OperationalSafetyDecision,
) -> None:
    if (
        health.market_data_observed_at != safety.market_data_observed_at
        or health.feed_integrity_state is not safety.feed_integrity_state
        or health.exchange_api_health_state is not safety.exchange_api_health_state
        or health.spread_bps != safety.spread_bps
        or health.time_sync_offset_ms != safety.time_sync_offset_ms
        or health.volatility_safety_state is not safety.volatility_safety_state
        or health.suspicious_price_anomaly is not safety.suspicious_price_anomaly
        or health.consecutive_order_rejections != safety.consecutive_order_rejections
    ):
        raise DashboardProjectionError(
            "operational health facts do not match the supplied safety audit"
        )
