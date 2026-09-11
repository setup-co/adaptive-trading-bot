import inspect
import runpy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
    DashboardDecisionPage,
    DashboardDecisionTradeQuery,
    DashboardJournalProviderError,
    DashboardTradePage,
    DashboardValue,
    SnapshotDashboardJournalProvider,
)
from adaptive_trading_bot.journal import (
    CompletedTradeJournalSnapshot,
    DecisionJournalSnapshot,
)
from adaptive_trading_bot.paper import PaperPortfolioCoordinator
from adaptive_trading_bot.risk import RiskDecisionStatus, RiskPortfolioSnapshot
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "paper" / "protective_exit_helpers.py"))
NOW = cast(datetime, _HELPERS["NOW"])
BTC = _HELPERS["BTC"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]


def _snapshots(
    *, observed_at: datetime = NOW
) -> tuple[DecisionJournalSnapshot[object, object], CompletedTradeJournalSnapshot]:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    item = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(observed_at=observed_at),
    )
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, observed_at, Decimal(100)),))
    trigger_at = observed_at + timedelta(minutes=1)
    trigger = paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    exit_at = trigger.triggered[0].authorized_at + timedelta(microseconds=1)
    paper.observe_protective_exits((observation(BTC, exit_at, Decimal(70)),))
    return (
        DecisionJournalSnapshot((item.journal_entry,), observed_at),
        paper.completed_trade_journal_snapshot(),
    )


def _query(**changes: object) -> DashboardDecisionTradeQuery:
    values: dict[str, object] = {
        "start": NOW - timedelta(minutes=1),
        "end": NOW + timedelta(minutes=5),
    }
    values.update(changes)
    return DashboardDecisionTradeQuery(**values)  # type: ignore[arg-type]


def _two_record_pages() -> tuple[DashboardDecisionPage, DashboardTradePage]:
    first_decisions, first_trades = _snapshots()
    second_decisions, second_trades = _snapshots(observed_at=NOW + timedelta(minutes=15))
    provider = SnapshotDashboardJournalProvider(
        DecisionJournalSnapshot(
            (first_decisions.entries[0], second_decisions.entries[0]),
            second_decisions.last_observed_at,
        ),
        CompletedTradeJournalSnapshot(
            (first_trades.entries[0], second_trades.entries[0]),
            second_trades.last_closed_at,
        ),
    )
    query = _query(end=NOW + timedelta(minutes=20))
    return provider.decisions(query), provider.trades(query)


def test_provider_projects_separate_authoritative_layers_and_exact_outcome() -> None:
    decisions, trades = _snapshots()
    provider = SnapshotDashboardJournalProvider(decisions, trades)

    record = provider.decisions(_query()).records[0]
    source = decisions.entries[0]
    completed = trades.entries[0]

    assert record.identity.observed_at == source.identity.observed_at
    assert record.strategy.action == source.strategy_evaluation.action.value
    assert record.strategy.reference_price == source.strategy_evaluation.reference_price
    assert record.strategy.rule_audit.availability is DashboardAvailability.UNAVAILABLE
    assert record.risk.status == source.risk_decision.status.value
    assert record.risk.reasons == tuple(reason.value for reason in source.risk_decision.reasons)
    assert record.safety.status == source.safety_decision.status.value
    assert record.safety.reasons == tuple(reason.value for reason in source.safety_decision.reasons)
    assert record.completed_trade.availability is DashboardAvailability.AVAILABLE
    outcome = record.completed_trade.value
    assert outcome is not None
    assert outcome.originating_decision_identity == record.identity
    assert outcome.gross_realized_pnl == completed.gross_realized_pnl
    assert outcome.net_realized_pnl == completed.net_realized_pnl
    assert outcome.realized_r_net == completed.realized_r_net
    assert outcome.mfe.availability is DashboardAvailability.UNAVAILABLE
    assert outcome.mae.availability is DashboardAvailability.UNAVAILABLE
    assert outcome.process_score.availability is DashboardAvailability.UNAVAILABLE


def test_similar_but_not_exact_identity_does_not_link() -> None:
    decisions, _ = _snapshots()
    _, other_trade = _snapshots(observed_at=NOW + timedelta(minutes=15))
    provider = SnapshotDashboardJournalProvider(decisions, other_trade)

    result = provider.decisions(_query())

    assert result.records[0].completed_trade.availability is DashboardAvailability.UNAVAILABLE


def test_orphan_trade_remains_truthful_in_trade_endpoint() -> None:
    original_decisions, trades = _snapshots()
    provider = SnapshotDashboardJournalProvider(DecisionJournalSnapshot((), None), trades)

    assert provider.decisions(_query()).records == ()
    outcome = provider.trades(_query()).records[0]
    assert (
        outcome.originating_decision_identity.observed_at
        == original_decisions.entries[0].identity.observed_at
    )
    assert (
        outcome.originating_decision_identity.strategy_id
        == original_decisions.entries[0].identity.strategy_id
    )


def test_duplicate_completed_outcome_for_one_identity_fails_closed() -> None:
    decisions, trades = _snapshots()
    forged = CompletedTradeJournalSnapshot(
        (trades.entries[0], trades.entries[0]),
        trades.last_closed_at,
    )
    provider = SnapshotDashboardJournalProvider(decisions, forged)

    with pytest.raises(DashboardJournalProviderError):
        provider.decisions(_query())


def test_matching_identity_with_conflicting_authoritative_pretrade_fails_closed() -> None:
    _, trades = _snapshots()
    conflicting = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(
            portfolio=RiskPortfolioSnapshot(
                NOW,
                "USDT",
                Decimal(12000),
                Decimal(12000),
                Decimal(12000),
            )
        ),
    )
    decisions = DecisionJournalSnapshot((conflicting.journal_entry,), NOW)
    assert decisions.entries[0].identity == trades.entries[0].identity.originating_decision_identity
    assert decisions.entries[0] != trades.entries[0].pre_trade_decision
    provider = SnapshotDashboardJournalProvider(decisions, trades)

    with pytest.raises(DashboardJournalProviderError, match="contradicts"):
        provider.decisions(_query())
    with pytest.raises(DashboardJournalProviderError, match="contradicts"):
        provider.trades(_query())


def test_filters_half_open_range_order_limit_and_no_silent_truncation() -> None:
    first, _ = _snapshots()
    second, _ = _snapshots(observed_at=NOW + timedelta(minutes=15))
    snapshot = DecisionJournalSnapshot(
        (second.entries[0], first.entries[0]),
        second.last_observed_at,
    )
    provider = SnapshotDashboardJournalProvider(snapshot, CompletedTradeJournalSnapshot((), None))

    page = provider.decisions(
        _query(
            end=NOW + timedelta(minutes=16),
            action=CandidateAction.LONG_CANDIDATE,
            risk_status=RiskDecisionStatus.APPROVED,
            limit=1,
        )
    )

    assert page.total_matched == 2
    assert page.returned_count == 1
    assert page.truncated is True
    assert page.records[0].identity.observed_at == NOW
    assert provider.decisions(_query(start=NOW + timedelta(microseconds=1))).records == ()


def test_read_models_reject_completed_trade_identity_and_fact_contradictions() -> None:
    decisions, trades = _snapshots()
    record = SnapshotDashboardJournalProvider(decisions, trades).trades(_query()).records[0]
    different_decision = replace(
        record.originating_decision_identity,
        strategy_version="contradictory-version",
    )

    with pytest.raises(ValueError, match="identity chain"):
        replace(
            record,
            paper_order_identity=replace(
                record.paper_order_identity,
                intent_identity=replace(
                    record.paper_order_identity.intent_identity,
                    decision_identity=different_decision,
                ),
            ),
        ).validate()
    with pytest.raises(ValueError, match="originating decision"):
        replace(record, source_id="contradictory-source").validate()


def test_read_model_pages_reject_out_of_range_and_noncanonical_records() -> None:
    first, _ = _snapshots()
    second, _ = _snapshots(observed_at=NOW + timedelta(minutes=15))
    provider = SnapshotDashboardJournalProvider(
        DecisionJournalSnapshot(
            (first.entries[0], second.entries[0]),
            second.last_observed_at,
        ),
        CompletedTradeJournalSnapshot((), None),
    )
    page = provider.decisions(_query(end=NOW + timedelta(minutes=20)))

    with pytest.raises(ValueError, match="outside the requested range"):
        replace(page, start=NOW + timedelta(microseconds=1)).validate()
    with pytest.raises(ValueError, match="canonical unique order"):
        replace(page, records=tuple(reversed(page.records))).validate()


@pytest.mark.parametrize("page_index", (0, 1))
def test_read_model_pages_reject_more_returned_records_than_limit(page_index: int) -> None:
    page = _two_record_pages()[page_index]

    with pytest.raises(ValueError, match="exceeds the requested limit"):
        replace(page, limit=1).validate()


@pytest.mark.parametrize("page_index", (0, 1))
def test_read_model_pages_reject_fewer_matches_than_returned(page_index: int) -> None:
    page = _two_record_pages()[page_index]

    with pytest.raises(ValueError, match="fewer matches than returned records"):
        replace(page, total_matched=1).validate()


def test_projection_is_detached_and_queries_do_not_mutate_snapshots() -> None:
    decisions, trades = _snapshots()
    before = (decisions, trades)
    provider = SnapshotDashboardJournalProvider(decisions, trades)

    page = provider.decisions(_query())
    object.__setattr__(page.records[0].identity, "strategy_id", "mutated-copy")

    assert (decisions, trades) == before
    assert decisions.entries[0].identity.strategy_id != "mutated-copy"


def test_post_snapshot_source_mutation_fails_closed() -> None:
    decisions, trades = _snapshots()
    provider = SnapshotDashboardJournalProvider(decisions, trades)
    object.__setattr__(decisions.entries[0].identity, "observed_at", NOW.replace(tzinfo=None))

    with pytest.raises(ValueError):
        provider.decisions(_query())


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (NOW.replace(tzinfo=None), NOW + timedelta(minutes=1)),
        (
            NOW.astimezone(timezone(timedelta(hours=3, minutes=30))),
            NOW + timedelta(minutes=1),
        ),
        (NOW, NOW),
    ),
)
def test_query_rejects_non_utc_and_empty_ranges(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError):
        DashboardDecisionTradeQuery(start, end)


async def test_api_is_get_only_lossless_and_reports_endpoint_failures() -> None:
    decisions, trades = _snapshots()
    provider = SnapshotDashboardJournalProvider(decisions, trades)
    app = create_app(Settings(_env_file=None), None, None, provider)  # type: ignore[call-arg]
    query = {"start": NOW.isoformat(), "end": (NOW + timedelta(minutes=5)).isoformat()}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decision_response = await client.get("/api/v1/dashboard/decisions", params=query)
        trade_response = await client.get("/api/v1/dashboard/trades", params=query)
        invalid = await client.get(
            "/api/v1/dashboard/decisions",
            params={"start": "not-a-time", "end": query["end"]},
        )

    assert decision_response.status_code == 200
    assert trade_response.status_code == 200
    assert invalid.status_code == 400
    body = trade_response.json()["records"][0]
    assert body["approved_risk_quote"] == str(trades.entries[0].approved_risk_quote)
    assert body["mfe"]["availability"] == "UNAVAILABLE"
    paths = {
        route.path: route.methods
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.endswith(("/decisions", "/trades"))
    }
    assert paths == {
        "/api/v1/dashboard/decisions": {"GET"},
        "/api/v1/dashboard/trades": {"GET"},
    }


async def test_missing_journal_provider_has_endpoint_specific_503() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    params = {"start": NOW.isoformat(), "end": (NOW + timedelta(minutes=1)).isoformat()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decisions = await client.get("/api/v1/dashboard/decisions", params=params)
        trades = await client.get("/api/v1/dashboard/trades", params=params)

    assert decisions.status_code == 503
    assert decisions.json()["code"] == "DASHBOARD_DECISION_PROVIDER_UNAVAILABLE"
    assert trades.status_code == 503
    assert trades.json()["code"] == "DASHBOARD_TRADE_PROVIDER_UNAVAILABLE"


async def test_api_fails_closed_on_non_finite_projected_fact() -> None:
    decisions, trades = _snapshots()
    real = SnapshotDashboardJournalProvider(decisions, trades)
    page = real.decisions(_query())
    malformed = replace(
        page,
        records=(
            replace(
                page.records[0],
                risk=replace(page.records[0].risk, daily_loss_quote=Decimal("NaN")),
            ),
        ),
    )

    class MalformedProvider:
        def decisions(self, query: DashboardDecisionTradeQuery) -> object:
            return malformed

        def trades(self, query: DashboardDecisionTradeQuery) -> object:
            return real.trades(query)

    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        None,
        None,
        MalformedProvider(),  # type: ignore[arg-type]
    )
    params = {"start": NOW.isoformat(), "end": (NOW + timedelta(minutes=5)).isoformat()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/decisions", params=params)

    assert response.status_code == 503
    assert response.json()["code"] == "DASHBOARD_DECISION_PROVIDER_FAILURE"


async def test_api_fails_closed_on_mismatched_linked_outcome_graph() -> None:
    decisions, trades = _snapshots()
    real = SnapshotDashboardJournalProvider(decisions, trades)
    page = real.decisions(_query())
    other_decisions, other_trades = _snapshots(observed_at=NOW + timedelta(minutes=15))
    other = (
        SnapshotDashboardJournalProvider(other_decisions, other_trades)
        .trades(_query(end=NOW + timedelta(minutes=20)))
        .records[0]
    )
    malformed = replace(
        page,
        records=(
            replace(
                page.records[0],
                completed_trade=DashboardValue.available(other),
            ),
        ),
    )

    class MalformedLinkProvider:
        def decisions(self, query: DashboardDecisionTradeQuery) -> object:
            return malformed

        def trades(self, query: DashboardDecisionTradeQuery) -> object:
            return real.trades(query)

    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        None,
        None,
        MalformedLinkProvider(),  # type: ignore[arg-type]
    )
    params = {"start": NOW.isoformat(), "end": (NOW + timedelta(minutes=5)).isoformat()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/decisions", params=params)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_DECISION_PROVIDER_FAILURE",
        "message": "dashboard decisions are temporarily unavailable",
    }


async def test_api_fails_closed_on_malformed_provider_page_counts() -> None:
    decision_page, trade_page = _two_record_pages()
    oversized_decisions = replace(decision_page, limit=1)
    understated_trades = replace(trade_page, total_matched=1)

    class MalformedCountProvider:
        def decisions(self, query: DashboardDecisionTradeQuery) -> object:
            return oversized_decisions

        def trades(self, query: DashboardDecisionTradeQuery) -> object:
            return understated_trades

    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        None,
        None,
        MalformedCountProvider(),  # type: ignore[arg-type]
    )
    params = {
        "start": NOW.isoformat(),
        "end": (NOW + timedelta(minutes=20)).isoformat(),
        "limit": "1",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decisions = await client.get("/api/v1/dashboard/decisions", params=params)
        trades = await client.get("/api/v1/dashboard/trades", params=params)

    assert decisions.status_code == 503
    assert decisions.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_DECISION_PROVIDER_FAILURE",
        "message": "dashboard decisions are temporarily unavailable",
    }
    assert trades.status_code == 503
    assert trades.json() == {
        "status": "unavailable",
        "code": "DASHBOARD_TRADE_PROVIDER_FAILURE",
        "message": "dashboard trades are temporarily unavailable",
    }


def test_provider_has_no_mutating_or_evaluation_surface() -> None:
    public = {
        name
        for name, value in inspect.getmembers(SnapshotDashboardJournalProvider)
        if not name.startswith("_") and inspect.isfunction(value)
    }
    assert public == {"decisions", "trades"}
