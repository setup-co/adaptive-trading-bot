"""FastAPI application factory and liveness endpoint."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from adaptive_trading_bot.config import Settings, get_settings
from adaptive_trading_bot.logging import configure_logging


class HealthResponse(BaseModel):
    """Truthful process-liveness response; this is not a readiness check."""

    status: Literal["ok"] = "ok"
    service: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application without connecting to external services."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    application = FastAPI(title=resolved_settings.app_name)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(service=resolved_settings.app_name)

    return application


app = create_app()
