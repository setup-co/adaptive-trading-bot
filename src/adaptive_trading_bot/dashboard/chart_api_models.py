"""Lossless transport models for the read-only Dashboard chart endpoint."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from adaptive_trading_bot.dashboard.api_models import (
    DashboardValueResponse,
    dashboard_value_response,
)
from adaptive_trading_bot.dashboard.chart_models import DashboardChartSnapshot


class DashboardChartApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class DashboardChartCandleResponse(DashboardChartApiModel):
    open_time: datetime
    close_time: datetime
    open: str
    high: str
    low: str
    close: str
    volume: str


class DashboardChartIntegrityIssueResponse(DashboardChartApiModel):
    kind: str
    message: str
    open_time: datetime | None


class DashboardChartIntegrityResponse(DashboardChartApiModel):
    start: datetime
    end: datetime
    is_clean: bool
    rows_scanned: int
    issues: tuple[DashboardChartIntegrityIssueResponse, ...]


class DashboardChartMarkerResponse(DashboardChartApiModel):
    kind: str
    timestamp: datetime
    price: str
    identity: str
    side: str
    label: str


class DashboardChartOverlaysResponse(DashboardChartApiModel):
    ema_20: DashboardValueResponse
    ema_50: DashboardValueResponse
    vwap: DashboardValueResponse
    key_levels: DashboardValueResponse


class DashboardChartSnapshotResponse(DashboardChartApiModel):
    source_id: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    limit: int
    candles: tuple[DashboardChartCandleResponse, ...]
    integrity: DashboardChartIntegrityResponse
    markers: tuple[DashboardChartMarkerResponse, ...]
    overlays: DashboardChartOverlaysResponse


class DashboardChartInvalidQueryResponse(DashboardChartApiModel):
    status: Literal["invalid_request"] = "invalid_request"
    code: Literal["DASHBOARD_CHART_INVALID_QUERY"] = "DASHBOARD_CHART_INVALID_QUERY"
    message: Literal["dashboard chart query is invalid"] = "dashboard chart query is invalid"


def chart_response(snapshot: DashboardChartSnapshot) -> DashboardChartSnapshotResponse:
    """Encode authoritative Decimal values as exact JSON strings."""

    return DashboardChartSnapshotResponse(
        source_id=snapshot.source_id,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        start=snapshot.start,
        end=snapshot.end,
        limit=snapshot.limit,
        candles=tuple(
            DashboardChartCandleResponse(
                open_time=item.open_time,
                close_time=item.close_time,
                open=str(item.open),
                high=str(item.high),
                low=str(item.low),
                close=str(item.close),
                volume=str(item.volume),
            )
            for item in snapshot.candles
        ),
        integrity=DashboardChartIntegrityResponse(
            start=snapshot.integrity.start,
            end=snapshot.integrity.end,
            is_clean=snapshot.integrity.is_clean,
            rows_scanned=snapshot.integrity.rows_scanned,
            issues=tuple(
                DashboardChartIntegrityIssueResponse(
                    kind=item.kind,
                    message=item.message,
                    open_time=item.open_time,
                )
                for item in snapshot.integrity.issues
            ),
        ),
        markers=tuple(
            DashboardChartMarkerResponse(
                kind=item.kind.value,
                timestamp=item.timestamp,
                price=str(item.price),
                identity=item.identity,
                side=item.side,
                label=item.label,
            )
            for item in snapshot.markers
        ),
        overlays=DashboardChartOverlaysResponse(
            ema_20=dashboard_value_response(snapshot.overlays.ema_20),
            ema_50=dashboard_value_response(snapshot.overlays.ema_50),
            vwap=dashboard_value_response(snapshot.overlays.vwap),
            key_levels=dashboard_value_response(snapshot.overlays.key_levels),
        ),
    )
