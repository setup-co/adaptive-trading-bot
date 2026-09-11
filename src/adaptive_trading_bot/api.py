"""FastAPI application factory and read-only observation endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adaptive_trading_bot.config import Settings, get_settings
from adaptive_trading_bot.dashboard import (
    MAX_DASHBOARD_CHART_CANDLES,
    DashboardChartInvalidQueryResponse,
    DashboardChartProvider,
    DashboardChartQuery,
    DashboardChartSnapshot,
    DashboardChartSnapshotResponse,
    DashboardSnapshot,
    DashboardSnapshotProvider,
    DashboardSnapshotResponse,
    DashboardUnavailableResponse,
    chart_response,
    dashboard_response,
)
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.logging import configure_logging
from adaptive_trading_bot.storage import SourceId


class HealthResponse(BaseModel):
    """Truthful process-liveness response; this is not a readiness check."""

    status: Literal["ok"] = "ok"
    service: str


def create_app(
    settings: Settings | None = None,
    dashboard_provider: DashboardSnapshotProvider | None = None,
    chart_provider: DashboardChartProvider | None = None,
) -> FastAPI:
    """Build the API application without connecting to external services."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    application = FastAPI(title=resolved_settings.app_name)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service=resolved_settings.app_name)

    @application.get(
        "/api/v1/dashboard/snapshot",
        response_model=DashboardSnapshotResponse,
        responses={503: {"model": DashboardUnavailableResponse}},
        tags=["dashboard"],
    )
    async def dashboard_snapshot() -> Response:
        if dashboard_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_PROVIDER_UNAVAILABLE",
                "dashboard snapshot provider is not configured",
            )
        try:
            snapshot = dashboard_provider.snapshot()
            if not isinstance(snapshot, DashboardSnapshot):
                raise TypeError("provider returned an invalid dashboard snapshot")
            snapshot.validate()
            response = dashboard_response(snapshot)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_PROVIDER_FAILURE",
                "dashboard snapshot is temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    @application.get(
        "/api/v1/dashboard/chart",
        response_model=DashboardChartSnapshotResponse,
        responses={
            400: {"model": DashboardChartInvalidQueryResponse},
            503: {"model": DashboardUnavailableResponse},
        },
        tags=["dashboard"],
    )
    async def dashboard_chart(
        source_id: str,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        limit: int = MAX_DASHBOARD_CHART_CANDLES,
    ) -> Response:
        try:
            query = _chart_query(source_id, symbol, timeframe, start, end, limit)
        except (TypeError, ValueError):
            return _dashboard_invalid_chart_query()
        if chart_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_CHART_PROVIDER_UNAVAILABLE",
                "dashboard chart provider is not configured",
            )
        try:
            snapshot = chart_provider.chart(query)
            if not isinstance(snapshot, DashboardChartSnapshot):
                raise TypeError("provider returned an invalid chart snapshot")
            snapshot.validate()
            response = chart_response(snapshot)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_CHART_PROVIDER_FAILURE",
                "dashboard chart is temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    return application


def _dashboard_unavailable(code: str, message: str) -> JSONResponse:
    response = DashboardUnavailableResponse(code=code, message=message)
    return JSONResponse(status_code=503, content=response.model_dump(mode="json"))


def _dashboard_invalid_chart_query() -> JSONResponse:
    response = DashboardChartInvalidQueryResponse()
    return JSONResponse(status_code=400, content=response.model_dump(mode="json"))


def _chart_query(
    source_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    limit: int,
) -> DashboardChartQuery:
    return DashboardChartQuery(
        SourceId(source_id),
        TradingSymbol.parse(symbol),
        Timeframe(timeframe),
        _parse_datetime(start),
        _parse_datetime(end),
        limit,
    )


def _parse_datetime(value: str) -> datetime:
    encoded = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(encoded)


app = create_app()
