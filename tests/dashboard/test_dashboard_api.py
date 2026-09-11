import inspect
import runpy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.api import create_app
from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.dashboard import (
    DashboardAvailability,
    DashboardSnapshot,
    DashboardValue,
    dashboard_response,
    project_dashboard_snapshot,
)
from adaptive_trading_bot.journal import CompletedTradeJournalSnapshot
from adaptive_trading_bot.market_data import LiveMarketQuote
from adaptive_trading_bot.paper import (
    PaperAccountSnapshot,
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorSnapshot,
)
from adaptive_trading_bot.risk import OperationalHealthSnapshot, OperationalSafetyDecision

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "paper" / "protective_exit_helpers.py"))
NOW = cast(datetime, _HELPERS["NOW"])
BTC = _HELPERS["BTC"]
ETH = _HELPERS["ETH"]
SOURCE = _HELPERS["SOURCE"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]
health_for = _HELPERS["_health"]
NAIVE_TIME = NOW.replace(tzinfo=None)
NON_UTC_TIME = NOW.astimezone(timezone(timedelta(hours=3, minutes=30)))


@dataclass(frozen=True, slots=True)
class _StaticProvider:
    value: DashboardSnapshot

    def snapshot(self, observed_at: datetime | None = None) -> DashboardSnapshot:
        return self.value


class _FailingProvider:
    def snapshot(self, observed_at: datetime | None = None) -> DashboardSnapshot:
        raise RuntimeError("exchange_api_key=should-never-leak")


def _dashboard_fixture() -> tuple[
    PaperPortfolioCoordinator,
    DashboardSnapshot,
    LiveMarketQuote,
    PaperPortfolioCoordinatorSnapshot,
    PaperAccountSnapshot,
    CompletedTradeJournalSnapshot,
    OperationalHealthSnapshot,
    OperationalSafetyDecision,
]:
    paper = cast(PaperPortfolioCoordinator, coordinator())

    closed_candidate = cast(PortfolioAdmissionCandidate[object, object], candidate())
    paper.start_batch((closed_candidate,))
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    trigger_at = NOW + timedelta(minutes=1)
    trigger = paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    exit_at = trigger.triggered[0].authorized_at + timedelta(microseconds=1)
    paper.observe_protective_exits((observation(BTC, exit_at, Decimal(70)),))

    observed_at = NOW + timedelta(minutes=15)
    open_candidate = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(
            observed_at=observed_at,
            symbol=ETH,
            portfolio=paper.risk_portfolio_snapshot(observed_at),
        ),
    )
    paper.start_batch((open_candidate,))
    paper.observe_batch((observation(ETH, observed_at, Decimal(100)),))

    quote = LiveMarketQuote(
        SOURCE,
        ETH,
        observed_at,
        observed_at - timedelta(seconds=7),
        Decimal("12345678901234567890.1234567890123"),
        Decimal("12345678901234567890.1234567890125"),
        Decimal("12345678901234567890.1234567890124"),
    )
    safety = open_candidate.journal_entry.safety_decision
    health = cast(OperationalHealthSnapshot, health_for(open_candidate.journal_entry.risk_decision))
    paper_snapshot = paper.snapshot()
    account_snapshot = paper.account_snapshot(observed_at)
    journal_snapshot = paper.completed_trade_journal_snapshot()
    dashboard = project_dashboard_snapshot(
        observed_at,
        paper_snapshot,
        account_snapshot,
        journal_snapshot,
        quotes=(quote,),
        health=(health,),
        safety=(safety,),
    )
    return (
        paper,
        dashboard,
        quote,
        paper_snapshot,
        account_snapshot,
        journal_snapshot,
        health,
        safety,
    )


async def test_missing_provider_is_truthful_and_health_remains_liveness() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/health")
        unavailable = await client.get("/api/v1/dashboard/snapshot")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "adaptive-trading-bot"}
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_PROVIDER_UNAVAILABLE",
        "message": "dashboard snapshot provider is not configured",
    }


async def test_injected_provider_returns_exact_lossless_detached_snapshot() -> None:
    paper, snapshot, _, _, _, _, _, _ = _dashboard_fixture()
    before = (
        paper.snapshot(),
        paper.account_snapshot(snapshot.observed_at),
        paper.completed_trade_journal_snapshot(),
    )
    app = create_app(Settings(_env_file=None), _StaticProvider(snapshot))  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/snapshot")

    assert response.status_code == 200
    assert response.json() == dashboard_response(snapshot).model_dump(mode="json")
    market = response.json()["markets"][0]
    assert market["midpoint"] == "12345678901234567890.1234567890124"
    assert market["observed_at"].endswith("Z")
    assert market["source_timestamp"]["value"].endswith("Z")
    assert before == (
        paper.snapshot(),
        paper.account_snapshot(snapshot.observed_at),
        paper.completed_trade_journal_snapshot(),
    )


async def test_provider_failure_is_stable_and_does_not_leak_secrets() -> None:
    app = create_app(Settings(_env_file=None), _FailingProvider())  # type: ignore[call-arg]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/snapshot")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_PROVIDER_FAILURE",
        "message": "dashboard snapshot is temporarily unavailable",
    }
    assert "key" not in response.text.lower()
    assert "traceback" not in response.text.lower()


async def test_dashboard_exposes_only_get_and_documents_response_schema() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    dashboard_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/dashboard")
    ]
    assert len(dashboard_routes) == 1
    assert dashboard_routes[0].methods == {"GET"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        write_response = await client.post("/api/v1/dashboard/snapshot")
        openapi = (await client.get("/openapi.json")).json()

    assert write_response.status_code == 405
    operation = openapi["paths"]["/api/v1/dashboard/snapshot"]
    assert set(operation) == {"get"}
    assert "DashboardSnapshotResponse" in str(operation["get"]["responses"]["200"])


def test_projection_copies_authoritative_paper_market_and_safety_facts_exactly() -> None:
    _, dashboard, quote, paper, authoritative_account, journal, health, safety = (
        _dashboard_fixture()
    )
    account = dashboard.account

    assert account.initial_equity == authoritative_account.initial_equity_quote
    assert account.equity == authoritative_account.account_equity_quote
    assert account.day_start_equity == authoritative_account.day_start_equity_quote
    assert account.peak_equity == authoritative_account.peak_equity_quote
    assert account.realized_gross_pnl == authoritative_account.realized_gross_pnl_quote
    assert account.cumulative_fees == authoritative_account.cumulative_fees_quote
    assert account.cumulative_net_pnl == authoritative_account.cumulative_net_pnl_quote
    source_open = paper.open_positions[0]
    projected_open = dashboard.open_positions[0]
    assert projected_open.accounting_fill_id == source_open.accounting_fill_id
    assert projected_open.symbol == str(source_open.symbol)
    assert projected_open.side == source_open.side.value
    assert projected_open.quantity == source_open.quantity
    assert projected_open.entry_fill_at == source_open.fill_at
    assert projected_open.entry_fill_price == source_open.fill_price
    assert projected_open.stop_basis == source_open.stop_basis
    assert projected_open.locked_open_risk == source_open.locked_open_risk_quote
    assert projected_open.locked_gross_exposure == source_open.locked_gross_exposure_quote
    assert account.locked_open_risk == paper.locked_open_risk_quote
    assert account.locked_gross_exposure == paper.locked_gross_exposure_quote

    source_trade = journal.entries[0]
    projected_trade = dashboard.recent_completed_trades[0]
    assert projected_trade.gross_realized_pnl == source_trade.gross_realized_pnl
    assert projected_trade.total_fees == source_trade.total_trade_fees_quote
    assert projected_trade.net_realized_pnl == source_trade.net_realized_pnl
    assert projected_trade.realized_r == source_trade.realized_r_net

    market = dashboard.markets[0]
    assert market.midpoint == quote.midpoint
    assert market.spread_bps == quote.spread_bps
    assert market.source_timestamp.value == quote.source_timestamp
    source_system = dashboard.system.sources[0]
    assert source_system.safety_status.value == safety.status.value
    assert source_system.safety_reasons.value == tuple(reason.value for reason in safety.reasons)
    assert source_system.market_data_observed_at == health.market_data_observed_at
    assert source_system.spread_bps == health.spread_bps
    assert source_system.time_sync_offset_ms == health.time_sync_offset_ms
    assert source_system.volatility_safety_state == health.volatility_safety_state.value

    unavailable = (
        market.regime,
        market.volatility,
        market.technical_score,
        market.fundamental_score,
        market.experience_metrics,
        projected_trade.mfe,
        projected_trade.mae,
        projected_trade.process_score,
        dashboard.system.global_safety_status,
        dashboard.system.last_news_update,
    )
    assert all(item.availability is DashboardAvailability.UNAVAILABLE for item in unavailable)
    assert all(item.value is None and item.reason for item in unavailable)


def test_projection_is_detached_and_ambient_decimal_context_independent() -> None:
    _, snapshot, quote, paper, _, _, _, _ = _dashboard_fixture()
    expected = dashboard_response(snapshot).model_dump(mode="json")

    object.__setattr__(quote, "midpoint", Decimal("1"))
    object.__setattr__(paper.open_positions[0], "quantity", Decimal("999999"))
    assert dashboard_response(snapshot).model_dump(mode="json") == expected

    with localcontext() as decimal_context:
        decimal_context.prec = 4
        low_precision = dashboard_response(snapshot).model_dump(mode="json")
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        high_precision = dashboard_response(snapshot).model_dump(mode="json")
    assert low_precision == high_precision == expected


def test_projection_orders_market_collections_canonically() -> None:
    _, _, eth_quote, paper, account, journal, _, _ = _dashboard_fixture()
    btc_quote = LiveMarketQuote(
        SOURCE,
        BTC,
        eth_quote.observed_at,
        None,
        Decimal("99.99"),
        Decimal("100.01"),
        Decimal(100),
    )

    snapshot = project_dashboard_snapshot(
        account.observed_at,
        paper,
        account,
        journal,
        quotes=(eth_quote, btc_quote),
    )

    assert tuple(item.symbol for item in snapshot.markets) == ("BTC/USDT", "ETH/USDT")


@pytest.mark.parametrize("malformed", (NAIVE_TIME, NON_UTC_TIME))
def test_available_dashboard_datetime_requires_utc(malformed: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        DashboardValue.available(malformed)


def test_every_nested_read_model_rejects_non_utc_construction() -> None:
    _, snapshot, _, _, _, _, _, _ = _dashboard_fixture()
    malformed = NON_UTC_TIME

    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.markets[0], observed_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.account, observed_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.open_positions[0], authorized_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.open_positions[0], eligible_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.open_positions[0], entry_fill_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.recent_completed_trades[0], entry_fill_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.recent_completed_trades[0], exit_fill_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.system.sources[0], observed_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.system.sources[0], market_data_observed_at=malformed)
    with pytest.raises(ValueError, match="UTC"):
        replace(snapshot.system, observed_at=malformed)


@pytest.mark.parametrize("malformed", (NAIVE_TIME, NON_UTC_TIME))
@pytest.mark.parametrize(
    "target",
    (
        "market_observed_at",
        "market_source_timestamp",
        "account_observed_at",
        "position_authorized_at",
        "position_eligible_at",
        "position_entry_fill_at",
        "trade_entry_fill_at",
        "trade_exit_fill_at",
        "source_system_observed_at",
        "source_system_market_data_observed_at",
        "system_observed_at",
        "system_last_news_update",
    ),
)
async def test_malformed_nested_timestamp_fails_closed_at_api_boundary(
    target: str,
    malformed: datetime,
) -> None:
    _, snapshot, _, _, _, _, _, _ = _dashboard_fixture()
    _corrupt_nested_timestamp(snapshot, target, malformed)
    app = create_app(Settings(_env_file=None), _StaticProvider(snapshot))  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/snapshot")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_PROVIDER_FAILURE",
        "message": "dashboard snapshot is temporarily unavailable",
    }


def _corrupt_nested_timestamp(
    snapshot: DashboardSnapshot,
    target: str,
    malformed: datetime,
) -> None:
    if target == "market_observed_at":
        object.__setattr__(snapshot.markets[0], "observed_at", malformed)
    elif target == "market_source_timestamp":
        object.__setattr__(snapshot.markets[0].source_timestamp, "value", malformed)
    elif target == "account_observed_at":
        object.__setattr__(snapshot.account, "observed_at", malformed)
    elif target.startswith("position_"):
        object.__setattr__(snapshot.open_positions[0], target.removeprefix("position_"), malformed)
    elif target.startswith("trade_"):
        object.__setattr__(
            snapshot.recent_completed_trades[0],
            target.removeprefix("trade_"),
            malformed,
        )
    elif target.startswith("source_system_"):
        object.__setattr__(
            snapshot.system.sources[0],
            target.removeprefix("source_system_"),
            malformed,
        )
    elif target == "system_observed_at":
        object.__setattr__(snapshot.system, "observed_at", malformed)
    elif target == "system_last_news_update":
        available_news_time = DashboardValue.available(NOW)
        object.__setattr__(snapshot.system, "last_news_update", available_news_time)
        object.__setattr__(available_news_time, "value", malformed)
    else:
        raise AssertionError(f"unknown corruption target: {target}")


def test_dashboard_modules_do_not_contain_trading_or_formula_authorities() -> None:
    import adaptive_trading_bot.dashboard.api_models as api_models_module
    import adaptive_trading_bot.dashboard.projection as projection_module
    import adaptive_trading_bot.dashboard.provider as provider_module

    source = "\n".join(
        inspect.getsource(module)
        for module in (api_models_module, projection_module, provider_module)
    )
    forbidden = (
        "RiskManager",
        "EntryOrderIntentAuthorizer",
        "PortfolioAdmissionGate",
        "PaperOrderSimulator",
        "price_simulated_market_fill",
        "authorize(",
        "start_batch(",
        "observe_batch(",
        "gross_realized_pnl =",
        "locked_open_risk =",
    )
    assert not any(name in source for name in forbidden)
