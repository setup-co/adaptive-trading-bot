"""FastAPI application factory and read-only observation endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adaptive_trading_bot.config import Settings, get_settings
from adaptive_trading_bot.dashboard import (
    MAX_DASHBOARD_CHART_CANDLES,
    MAX_DASHBOARD_EXPERIENCE_CONTEXTS,
    MAX_DASHBOARD_JOURNAL_RECORDS,
    DashboardChartInvalidQueryResponse,
    DashboardChartProvider,
    DashboardChartQuery,
    DashboardChartSnapshot,
    DashboardChartSnapshotResponse,
    DashboardDecisionPage,
    DashboardDecisionPageResponse,
    DashboardDecisionTradeQuery,
    DashboardExperienceContextPage,
    DashboardExperienceContextPageResponse,
    DashboardExperienceContextQuery,
    DashboardExperienceEstimate,
    DashboardExperienceEstimateQuery,
    DashboardExperienceEstimateResponse,
    DashboardExperienceInvalidQueryResponse,
    DashboardExperienceProvider,
    DashboardJournalInvalidQueryResponse,
    DashboardJournalProvider,
    DashboardSnapshot,
    DashboardSnapshotProvider,
    DashboardSnapshotResponse,
    DashboardTradePage,
    DashboardTradePageResponse,
    DashboardUnavailableResponse,
    chart_response,
    dashboard_response,
    decision_page_response,
    experience_context_page_response,
    experience_estimate_response,
    trade_page_response,
)
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.experience import ExperienceContextFingerprint, TrendStrengthBucket
from adaptive_trading_bot.journal import TradeOutcomeClassification
from adaptive_trading_bot.logging import configure_logging
from adaptive_trading_bot.regime import (
    Direction,
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    RegimeLabel,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.risk import OperationalSafetyStatus, RiskDecisionStatus
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction


class HealthResponse(BaseModel):
    """Truthful process-liveness response; this is not a readiness check."""

    status: Literal["ok"] = "ok"
    service: str


def create_app(
    settings: Settings | None = None,
    dashboard_provider: DashboardSnapshotProvider | None = None,
    chart_provider: DashboardChartProvider | None = None,
    journal_provider: DashboardJournalProvider | None = None,
    experience_provider: DashboardExperienceProvider | None = None,
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

    @application.get(
        "/api/v1/dashboard/decisions",
        response_model=DashboardDecisionPageResponse,
        responses={
            400: {"model": DashboardJournalInvalidQueryResponse},
            503: {"model": DashboardUnavailableResponse},
        },
        tags=["dashboard"],
    )
    async def dashboard_decisions(
        start: str,
        end: str,
        source_id: str | None = None,
        symbol: str | None = None,
        strategy_id: str | None = None,
        action: str | None = None,
        risk_status: str | None = None,
        safety_status: str | None = None,
        limit: int = MAX_DASHBOARD_JOURNAL_RECORDS,
    ) -> Response:
        try:
            query = _journal_query(
                start,
                end,
                source_id=source_id,
                symbol=symbol,
                strategy_id=strategy_id,
                action=CandidateAction(action) if action is not None else None,
                risk_status=RiskDecisionStatus(risk_status) if risk_status is not None else None,
                safety_status=(
                    OperationalSafetyStatus(safety_status) if safety_status is not None else None
                ),
                limit=limit,
            )
        except (TypeError, ValueError):
            return _dashboard_invalid_journal_query()
        if journal_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_DECISION_PROVIDER_UNAVAILABLE",
                "dashboard decision provider is not configured",
            )
        try:
            page = journal_provider.decisions(query)
            if not isinstance(page, DashboardDecisionPage):
                raise TypeError("provider returned an invalid decision page")
            page.validate()
            response = decision_page_response(page)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_DECISION_PROVIDER_FAILURE",
                "dashboard decisions are temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    @application.get(
        "/api/v1/dashboard/trades",
        response_model=DashboardTradePageResponse,
        responses={
            400: {"model": DashboardJournalInvalidQueryResponse},
            503: {"model": DashboardUnavailableResponse},
        },
        tags=["dashboard"],
    )
    async def dashboard_trades(
        start: str,
        end: str,
        source_id: str | None = None,
        symbol: str | None = None,
        strategy_id: str | None = None,
        classification: str | None = None,
        limit: int = MAX_DASHBOARD_JOURNAL_RECORDS,
    ) -> Response:
        try:
            query = _journal_query(
                start,
                end,
                source_id=source_id,
                symbol=symbol,
                strategy_id=strategy_id,
                classification=(
                    TradeOutcomeClassification(classification)
                    if classification is not None
                    else None
                ),
                limit=limit,
            )
        except (TypeError, ValueError):
            return _dashboard_invalid_journal_query()
        if journal_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_TRADE_PROVIDER_UNAVAILABLE",
                "dashboard trade provider is not configured",
            )
        try:
            page = journal_provider.trades(query)
            if not isinstance(page, DashboardTradePage):
                raise TypeError("provider returned an invalid trade page")
            page.validate()
            response = trade_page_response(page)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_TRADE_PROVIDER_FAILURE",
                "dashboard trades are temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    @application.get(
        "/api/v1/dashboard/experience/contexts",
        response_model=DashboardExperienceContextPageResponse,
        responses={
            400: {"model": DashboardExperienceInvalidQueryResponse},
            503: {"model": DashboardUnavailableResponse},
        },
        tags=["dashboard"],
    )
    async def dashboard_experience_contexts(
        as_of: str,
        limit: int = MAX_DASHBOARD_EXPERIENCE_CONTEXTS,
    ) -> Response:
        try:
            query = DashboardExperienceContextQuery(_parse_datetime(as_of), limit)
        except (TypeError, ValueError):
            return _dashboard_invalid_experience_query()
        if experience_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_EXPERIENCE_UNAVAILABLE",
                "dashboard Experience provider is not configured",
            )
        try:
            page = experience_provider.contexts(query)
            if not isinstance(page, DashboardExperienceContextPage):
                raise TypeError("provider returned an invalid Experience context page")
            page.validate()
            response = experience_context_page_response(page)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_EXPERIENCE_PROVIDER_FAILURE",
                "dashboard Experience observation is temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    @application.get(
        "/api/v1/dashboard/experience/estimate",
        response_model=DashboardExperienceEstimateResponse,
        responses={
            400: {"model": DashboardExperienceInvalidQueryResponse},
            503: {"model": DashboardUnavailableResponse},
        },
        tags=["dashboard"],
    )
    async def dashboard_experience_estimate(
        as_of: str,
        symbol: str,
        strategy_id: str,
        strategy_version: str,
        entry_timeframe: str,
        regime_label: str,
        direction: str,
        trend_strength_bucket: str,
        volatility_state: str,
        volume_state: str,
        higher_timeframe_bias: str,
        multi_timeframe_alignment: str,
    ) -> Response:
        try:
            fingerprint = ExperienceContextFingerprint(
                TradingSymbol.parse(symbol),
                strategy_id,
                strategy_version,
                Timeframe(entry_timeframe),
                RegimeLabel(regime_label),
                Direction(direction),
                TrendStrengthBucket(trend_strength_bucket),
                VolatilityState(volatility_state),
                VolumeState(volume_state),
                HigherTimeframeBias(higher_timeframe_bias),
                MultiTimeframeAlignment(multi_timeframe_alignment),
            )
            query = DashboardExperienceEstimateQuery(fingerprint, _parse_datetime(as_of))
        except (TypeError, ValueError):
            return _dashboard_invalid_experience_query()
        if experience_provider is None:
            return _dashboard_unavailable(
                "DASHBOARD_EXPERIENCE_UNAVAILABLE",
                "dashboard Experience provider is not configured",
            )
        try:
            estimate = experience_provider.estimate(query)
            if not isinstance(estimate, DashboardExperienceEstimate):
                raise TypeError("provider returned an invalid Experience estimate")
            estimate.validate()
            response = experience_estimate_response(estimate)
        except Exception:
            return _dashboard_unavailable(
                "DASHBOARD_EXPERIENCE_PROVIDER_FAILURE",
                "dashboard Experience observation is temporarily unavailable",
            )
        return JSONResponse(response.model_dump(mode="json"))

    return application


def _dashboard_unavailable(code: str, message: str) -> JSONResponse:
    response = DashboardUnavailableResponse(code=code, message=message)
    return JSONResponse(status_code=503, content=response.model_dump(mode="json"))


def _dashboard_invalid_chart_query() -> JSONResponse:
    response = DashboardChartInvalidQueryResponse()
    return JSONResponse(status_code=400, content=response.model_dump(mode="json"))


def _dashboard_invalid_journal_query() -> JSONResponse:
    response = DashboardJournalInvalidQueryResponse()
    return JSONResponse(status_code=400, content=response.model_dump(mode="json"))


def _dashboard_invalid_experience_query() -> JSONResponse:
    response = DashboardExperienceInvalidQueryResponse()
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


def _journal_query(
    start: str,
    end: str,
    *,
    source_id: str | None,
    symbol: str | None,
    strategy_id: str | None,
    action: CandidateAction | None = None,
    risk_status: RiskDecisionStatus | None = None,
    safety_status: OperationalSafetyStatus | None = None,
    classification: TradeOutcomeClassification | None = None,
    limit: int,
) -> DashboardDecisionTradeQuery:
    canonical_symbol = str(TradingSymbol.parse(symbol)) if symbol is not None else None
    return DashboardDecisionTradeQuery(
        _parse_datetime(start),
        _parse_datetime(end),
        source_id,
        canonical_symbol,
        strategy_id,
        action,
        risk_status,
        safety_status,
        classification,
        limit,
    )


app = create_app()
