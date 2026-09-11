import inspect
import runpy
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Never, cast

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.api import create_app
from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.dashboard import (
    DashboardAvailability,
    DashboardChartCandle,
    DashboardChartIntegrityIssue,
    DashboardChartMarker,
    DashboardChartMarkerKind,
    DashboardChartQuery,
    DashboardChartSnapshot,
    DashboardChartValidationError,
    HistoricalDashboardChartProvider,
    chart_response,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import CompletedTradeJournalSnapshot
from adaptive_trading_bot.paper import (
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorSnapshot,
)
from adaptive_trading_bot.storage import (
    IntegrityIssue,
    IntegrityIssueKind,
    IntegrityReport,
    ParquetHistoricalCandleStore,
    SourceId,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
SOURCE = SourceId("archive")
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "paper" / "protective_exit_helpers.py"))
PAPER_NOW = cast(datetime, _HELPERS["NOW"])
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]


def _candle(
    open_time: datetime,
    *,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    price: Decimal = Decimal("100.123456789012345678"),
) -> Candle:
    return Candle(
        BTC,
        timeframe,
        open_time,
        open_time + timeframe.duration,
        price,
        price + Decimal(10),
        price - Decimal(10),
        price + Decimal(1),
        Decimal("12.000000000000000001"),
    )


def _query(
    *,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    start: datetime = NOW,
    end: datetime | None = None,
    symbol: TradingSymbol = BTC,
    limit: int = 2_000,
) -> DashboardChartQuery:
    return DashboardChartQuery(
        SOURCE,
        symbol,
        timeframe,
        start,
        start + timeframe.duration if end is None else end,
        limit,
    )


class _RecordingStore:
    def __init__(
        self,
        candles: tuple[Candle, ...],
        report: IntegrityReport,
    ) -> None:
        self.candles = candles
        self.report = report
        self.calls: list[tuple[str, object, object, object, object, object]] = []

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> Never:
        raise AssertionError("chart provider must never call merge")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        self.calls.append(("read", source_id, symbol, timeframe, start, end))
        return self.candles

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        self.calls.append(("scan_integrity", source_id, symbol, timeframe, start, end))
        return self.report


class _FailingChartProvider:
    def chart(self, query: DashboardChartQuery) -> DashboardChartSnapshot:
        raise RuntimeError("C:\\private\\candles.parquet api_key=never-leak")


class _StaticChartProvider:
    def __init__(self, snapshot: DashboardChartSnapshot) -> None:
        self.snapshot = snapshot

    def chart(self, query: DashboardChartQuery) -> DashboardChartSnapshot:
        return self.snapshot


def _report(
    query: DashboardChartQuery,
    *,
    rows_scanned: int = 0,
    issues: tuple[IntegrityIssue, ...] = (),
) -> IntegrityReport:
    return IntegrityReport(
        query.source_id,
        query.symbol,
        query.timeframe,
        query.start,
        query.end,
        rows_scanned,
        issues,
    )


@pytest.mark.parametrize(
    "timeframe",
    (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4),
)
async def test_native_timeframe_storage_values_are_copied_losslessly(
    tmp_path: Path,
    timeframe: Timeframe,
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candle = _candle(NOW, timeframe=timeframe)
    store.merge(SOURCE, (candle,))
    query = _query(timeframe=timeframe)
    provider = HistoricalDashboardChartProvider(store)

    snapshot = provider.chart(query)
    app = create_app(Settings(_env_file=None), chart_provider=provider)  # type: ignore[call-arg]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/chart", params=_params(query))

    assert snapshot.candles[0].open_time == candle.open_time
    assert snapshot.candles[0].close_time == candle.close_time
    assert (
        snapshot.candles[0].open,
        snapshot.candles[0].high,
        snapshot.candles[0].low,
        snapshot.candles[0].close,
        snapshot.candles[0].volume,
    ) == (candle.open, candle.high, candle.low, candle.close, candle.volume)
    assert response.status_code == 200
    assert response.json()["candles"][0]["open"] == "100.123456789012345678"
    assert response.json()["candles"][0]["volume"] == "12.000000000000000001"
    assert response.json()["candles"][0]["open_time"].endswith("Z")


def test_provider_uses_exact_half_open_range_for_read_and_integrity(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = _candle(NOW)
    second = _candle(first.close_time)
    third = _candle(second.close_time)
    store.merge(SOURCE, (third, first, second))
    query = _query(start=second.open_time, end=third.open_time)

    snapshot = HistoricalDashboardChartProvider(store).chart(query)

    assert tuple(item.open_time for item in snapshot.candles) == (second.open_time,)
    assert snapshot.integrity.is_clean
    assert snapshot.integrity.rows_scanned == 1


def test_gap_is_visible_without_interpolation(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    first = _candle(NOW)
    after_gap = _candle(NOW + timedelta(minutes=30))
    store.merge(SOURCE, (after_gap, first))
    query = _query(start=NOW, end=after_gap.close_time)

    snapshot = HistoricalDashboardChartProvider(store).chart(query)

    assert tuple(item.open_time for item in snapshot.candles) == (NOW, after_gap.open_time)
    assert not snapshot.integrity.is_clean
    assert tuple(item.kind for item in snapshot.integrity.issues) == (IntegrityIssueKind.GAP.value,)
    assert snapshot.integrity.issues[0].message == "historical candle coverage gap"


def test_empty_non_empty_range_is_truthful_and_not_fabricated(tmp_path: Path) -> None:
    snapshot = HistoricalDashboardChartProvider(ParquetHistoricalCandleStore(tmp_path)).chart(
        _query()
    )

    assert snapshot.candles == ()
    assert snapshot.integrity.rows_scanned == 0
    assert not snapshot.integrity.is_clean
    assert snapshot.integrity.issues[0].kind == IntegrityIssueKind.GAP.value


@pytest.mark.parametrize(
    "kind",
    (
        IntegrityIssueKind.GAP,
        IntegrityIssueKind.DUPLICATE_OPEN_TIME,
        IntegrityIssueKind.OVERLAP,
        IntegrityIssueKind.OUT_OF_ORDER,
    ),
)
async def test_readable_integrity_findings_are_sanitized_and_exposed(
    kind: IntegrityIssueKind,
) -> None:
    query = _query()
    candle = _candle(NOW)
    issue = IntegrityIssue(
        kind,
        "C:\\private\\partitions\\candles.parquet api_key=never-leak",
        "C:\\private\\partitions\\candles.parquet",
        NOW,
    )
    store = _RecordingStore((candle,), _report(query, rows_scanned=1, issues=(issue,)))
    provider = HistoricalDashboardChartProvider(store)
    app = create_app(Settings(_env_file=None), chart_provider=provider)  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/chart", params=_params(query))

    assert response.status_code == 200
    assert response.json()["integrity"]["issues"][0]["kind"] == kind.value
    assert "private" not in response.text.lower()
    assert "api_key" not in response.text.lower()
    assert "partition" not in response.json()["integrity"]["issues"][0]
    expected_call = ("read", SOURCE, BTC, Timeframe.MINUTES_15, query.start, query.end)
    assert store.calls == [
        expected_call,
        ("scan_integrity", *expected_call[1:]),
    ]


@pytest.mark.parametrize(
    "kind",
    (IntegrityIssueKind.SCHEMA_MISMATCH, IntegrityIssueKind.MALFORMED_ROW),
)
async def test_unsafe_integrity_fails_closed_without_internal_details(
    kind: IntegrityIssueKind,
) -> None:
    query = _query()
    issue = IntegrityIssue(kind, "C:\\secret\\broken.parquet", "secret", NOW)
    provider = HistoricalDashboardChartProvider(
        _RecordingStore((_candle(NOW),), _report(query, rows_scanned=1, issues=(issue,)))
    )
    app = create_app(Settings(_env_file=None), chart_provider=provider)  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/chart", params=_params(query))

    assert response.status_code == 503
    assert response.json()["code"] == "DASHBOARD_CHART_PROVIDER_FAILURE"
    assert "secret" not in response.text.lower()
    assert "parquet" not in response.text.lower()


def _overlay_snapshots() -> tuple[
    PaperPortfolioCoordinatorSnapshot,
    CompletedTradeJournalSnapshot,
]:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    first = cast(PortfolioAdmissionCandidate[object, object], candidate())
    paper.start_batch((first,))
    paper.observe_batch((observation(BTC, PAPER_NOW, Decimal(100)),))
    trigger_at = PAPER_NOW + timedelta(minutes=1)
    trigger = paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    exit_at = trigger.triggered[0].authorized_at + timedelta(microseconds=1)
    paper.observe_protective_exits((observation(BTC, exit_at, Decimal(70)),))

    open_at = PAPER_NOW + timedelta(minutes=15)
    portfolio = paper.risk_portfolio_snapshot(open_at)
    btc = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(observed_at=open_at, symbol=BTC, portfolio=portfolio),
    )
    eth = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(observed_at=open_at, symbol=ETH, portfolio=portfolio),
    )
    paper.start_batch((eth, btc))
    paper.observe_batch(
        (
            observation(ETH, open_at, Decimal(200)),
            observation(BTC, open_at, Decimal(100)),
        )
    )
    return paper.snapshot(), paper.completed_trade_journal_snapshot()


def test_markers_copy_matching_authoritative_open_and_completed_facts() -> None:
    paper, journal = _overlay_snapshots()
    btc_position = next(item for item in paper.open_positions if item.symbol == BTC)
    unrelated_source = replace(
        btc_position,
        source_id=SourceId("other-archive"),
        accounting_fill_id="other-source-fill",
    )
    paper = replace(paper, open_positions=(*paper.open_positions, unrelated_source))
    query = _query(start=PAPER_NOW, end=PAPER_NOW + timedelta(minutes=30))
    provider = HistoricalDashboardChartProvider(
        _RecordingStore((), _report(query)),
        paper=paper,
        completed_trades=journal,
    )

    snapshot = provider.chart(query)

    assert tuple(item.kind for item in snapshot.markers) == (
        DashboardChartMarkerKind.COMPLETED_TRADE_ENTRY,
        DashboardChartMarkerKind.COMPLETED_TRADE_EXIT,
        DashboardChartMarkerKind.OPEN_POSITION_ENTRY,
        DashboardChartMarkerKind.OPEN_POSITION_STOP,
    )
    trade = journal.entries[0]
    open_position = btc_position
    eth_position = next(item for item in paper.open_positions if item.symbol == ETH)
    assert snapshot.markers[0].timestamp == trade.entry_fill_at
    assert snapshot.markers[0].price == trade.entry_fill_price
    assert snapshot.markers[1].timestamp == trade.exit_fill_at
    assert snapshot.markers[1].price == trade.exit_fill_price
    assert snapshot.markers[2].timestamp == open_position.fill_at
    assert snapshot.markers[2].price == open_position.fill_price
    assert snapshot.markers[3].price == open_position.stop_basis
    assert all(item.identity for item in snapshot.markers)
    identities = {item.identity for item in snapshot.markers}
    assert eth_position.accounting_fill_id not in identities
    assert unrelated_source.accounting_fill_id not in identities


def test_markers_use_half_open_window_and_are_detached_from_sources() -> None:
    paper, journal = _overlay_snapshots()
    trade = journal.entries[0]
    query = _query(start=trade.exit_fill_at, end=PAPER_NOW + timedelta(minutes=15))
    provider = HistoricalDashboardChartProvider(
        _RecordingStore((), _report(query)),
        paper=paper,
        completed_trades=journal,
    )
    snapshot = provider.chart(query)
    expected = chart_response(snapshot).model_dump(mode="json")

    assert tuple(item.kind for item in snapshot.markers) == (
        DashboardChartMarkerKind.COMPLETED_TRADE_EXIT,
    )
    object.__setattr__(journal.entries[0], "exit_fill_price", Decimal("999999"))
    object.__setattr__(paper.open_positions[0], "fill_price", Decimal("888888"))
    assert chart_response(snapshot).model_dump(mode="json") == expected


def test_indicator_overlays_are_explicitly_unavailable() -> None:
    query = _query()
    snapshot = HistoricalDashboardChartProvider(_RecordingStore((), _report(query))).chart(query)

    values = (
        snapshot.overlays.ema_20,
        snapshot.overlays.ema_50,
        snapshot.overlays.vwap,
        snapshot.overlays.key_levels,
    )
    assert all(item.availability is DashboardAvailability.UNAVAILABLE for item in values)
    assert all(item.value is None and item.reason for item in values)


def test_query_limit_and_utc_are_explicit_and_context_independent() -> None:
    with pytest.raises(DashboardChartValidationError, match="range exceeds"):
        _query(end=NOW + timedelta(minutes=30), limit=1)
    with pytest.raises(DashboardChartValidationError, match="between"):
        _query(limit=2_001)
    with pytest.raises(ValueError, match="UTC"):
        _query(start=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        _query(start=NOW.astimezone(timezone(timedelta(hours=1))))

    with localcontext() as context:
        context.prec = 4
        low = _query()
    with localcontext() as context:
        context.prec = 50
        high = _query()
    assert low == high


def test_chart_candle_marker_and_integrity_timestamps_require_utc() -> None:
    naive = NOW.replace(tzinfo=None)
    candle = _candle(NOW)
    with pytest.raises(ValueError, match="UTC"):
        DashboardChartCandle(
            naive,
            candle.close_time,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
    with pytest.raises(ValueError, match="UTC"):
        DashboardChartMarker(
            DashboardChartMarkerKind.COMPLETED_TRADE_ENTRY,
            naive,
            Decimal(100),
            "fill-1",
            "BUY",
            "entry",
        )
    with pytest.raises(ValueError, match="UTC"):
        DashboardChartIntegrityIssue("gap", "gap", naive)


@pytest.mark.parametrize("target", ("candle", "marker", "integrity"))
async def test_mutated_non_utc_chart_fails_closed_before_api_200(target: str) -> None:
    query = _query()
    issue = IntegrityIssue(IntegrityIssueKind.GAP, "gap", "partition", NOW)
    snapshot = HistoricalDashboardChartProvider(
        _RecordingStore((_candle(NOW),), _report(query, rows_scanned=1, issues=(issue,)))
    ).chart(query)
    marker = DashboardChartMarker(
        DashboardChartMarkerKind.COMPLETED_TRADE_ENTRY,
        NOW,
        Decimal(100),
        "fill-1",
        "BUY",
        "entry",
    )
    object.__setattr__(snapshot, "markers", (marker,))
    malformed = NOW.replace(tzinfo=None)
    if target == "candle":
        object.__setattr__(snapshot.candles[0], "open_time", malformed)
    elif target == "marker":
        object.__setattr__(marker, "timestamp", malformed)
    else:
        object.__setattr__(snapshot.integrity.issues[0], "open_time", malformed)
    app = create_app(Settings(_env_file=None), chart_provider=_StaticChartProvider(snapshot))  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/chart", params=_params(query))

    assert response.status_code == 503
    assert response.json()["code"] == "DASHBOARD_CHART_PROVIDER_FAILURE"


async def test_chart_provider_absence_and_failure_are_distinct_structured_503s() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    no_provider = create_app(settings)
    failing = create_app(settings, chart_provider=_FailingChartProvider())

    async with AsyncClient(
        transport=ASGITransport(app=no_provider), base_url="http://test"
    ) as client:
        missing = await client.get("/api/v1/dashboard/chart", params=_params(_query()))
        snapshot = await client.get("/api/v1/dashboard/snapshot")
        health = await client.get("/health")
    async with AsyncClient(transport=ASGITransport(app=failing), base_url="http://test") as client:
        failure = await client.get("/api/v1/dashboard/chart", params=_params(_query()))

    assert missing.status_code == 503
    assert missing.json()["code"] == "DASHBOARD_CHART_PROVIDER_UNAVAILABLE"
    assert failure.status_code == 503
    assert failure.json()["code"] == "DASHBOARD_CHART_PROVIDER_FAILURE"
    assert "private" not in failure.text.lower()
    assert "key" not in failure.text.lower()
    assert snapshot.json()["code"] == "DASHBOARD_PROVIDER_UNAVAILABLE"
    assert health.status_code == 200


@pytest.mark.parametrize(
    "updates",
    (
        {"source_id": "../secret"},
        {"symbol": "SOL/USDT"},
        {"timeframe": "5m"},
        {"start": "2026-09-11T12:00:00"},
        {"start": "2026-09-11T15:30:00+03:30"},
        {"end": "2026-09-11T12:00:00Z"},
        {"limit": "2001"},
    ),
)
async def test_invalid_chart_queries_return_stable_4xx(updates: dict[str, str]) -> None:
    query = _query()
    params = _params(query)
    params.update(updates)
    app = create_app(Settings(_env_file=None), chart_provider=_FailingChartProvider())  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/chart", params=params)

    assert 400 <= response.status_code < 500
    if response.status_code == 400:
        assert response.json()["code"] == "DASHBOARD_CHART_INVALID_QUERY"


async def test_chart_route_is_get_only_and_openapi_documented() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/dashboard/chart"
    ]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        write = await client.post("/api/v1/dashboard/chart")
        openapi = (await client.get("/openapi.json")).json()

    assert write.status_code == 405
    assert "DashboardChartSnapshotResponse" in str(
        openapi["paths"]["/api/v1/dashboard/chart"]["get"]["responses"]["200"]
    )


def test_chart_provider_has_no_mutation_or_trading_formula_authority() -> None:
    import adaptive_trading_bot.dashboard.chart_provider as provider_module

    source = inspect.getsource(provider_module)
    forbidden = (
        ".merge(",
        "RiskManager",
        "authorize(",
        "start_batch(",
        "observe_batch(",
        "price_simulated_market_fill",
        "gross_realized_pnl",
        "net_realized_pnl",
        "resample",
        "interpolate",
    )
    assert not any(value in source for value in forbidden)


def _params(query: DashboardChartQuery) -> dict[str, str]:
    return {
        "source_id": query.source_id.value,
        "symbol": str(query.symbol),
        "timeframe": query.timeframe.value,
        "start": query.start.isoformat().replace("+00:00", "Z"),
        "end": query.end.isoformat().replace("+00:00", "Z"),
        "limit": str(query.limit),
    }
