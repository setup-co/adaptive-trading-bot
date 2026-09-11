"""FastAPI application factory and liveness endpoint."""

from typing import Literal

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adaptive_trading_bot.config import Settings, get_settings
from adaptive_trading_bot.dashboard import (
    DashboardSnapshot,
    DashboardSnapshotProvider,
    DashboardSnapshotResponse,
    DashboardUnavailableResponse,
    dashboard_response,
)
from adaptive_trading_bot.logging import configure_logging


class HealthResponse(BaseModel):
    """Truthful process-liveness response; this is not a readiness check."""

    status: Literal["ok"] = "ok"
    service: str


def create_app(
    settings: Settings | None = None,
    dashboard_provider: DashboardSnapshotProvider | None = None,
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
            response = dashboard_response(snapshot)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_PROVIDER_FAILURE",
                "dashboard snapshot is temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    return application


def _dashboard_unavailable(code: str, message: str) -> JSONResponse:
    response = DashboardUnavailableResponse(code=code, message=message)
    return JSONResponse(status_code=503, content=response.model_dump(mode="json"))


app = create_app()
