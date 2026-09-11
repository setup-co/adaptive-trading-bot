"""Strictly read-only Dashboard observation boundary."""

from adaptive_trading_bot.dashboard.api_models import (
    DashboardSnapshotResponse,
    DashboardUnavailableResponse,
    dashboard_response,
)
from adaptive_trading_bot.dashboard.chart_api_models import (
    DashboardChartInvalidQueryResponse,
    DashboardChartSnapshotResponse,
    chart_response,
)
from adaptive_trading_bot.dashboard.chart_models import (
    MAX_DASHBOARD_CHART_CANDLES,
    DashboardChartCandle,
    DashboardChartIntegrity,
    DashboardChartIntegrityIssue,
    DashboardChartMarker,
    DashboardChartMarkerKind,
    DashboardChartOverlays,
    DashboardChartQuery,
    DashboardChartSnapshot,
    DashboardChartValidationError,
)
from adaptive_trading_bot.dashboard.chart_provider import (
    DashboardChartProvider,
    DashboardChartProviderError,
    HistoricalDashboardChartProvider,
)
from adaptive_trading_bot.dashboard.models import (
    DashboardAvailability,
    DashboardCompletedTrade,
    DashboardMarketSnapshot,
    DashboardOpenPosition,
    DashboardPaperAccountSnapshot,
    DashboardSnapshot,
    DashboardSourceSystemSnapshot,
    DashboardSystemSnapshot,
    DashboardValue,
)
from adaptive_trading_bot.dashboard.projection import (
    DashboardProjectionError,
    project_dashboard_snapshot,
)
from adaptive_trading_bot.dashboard.provider import DashboardSnapshotProvider

__all__ = [
    "MAX_DASHBOARD_CHART_CANDLES",
    "DashboardAvailability",
    "DashboardChartCandle",
    "DashboardChartIntegrity",
    "DashboardChartIntegrityIssue",
    "DashboardChartInvalidQueryResponse",
    "DashboardChartMarker",
    "DashboardChartMarkerKind",
    "DashboardChartOverlays",
    "DashboardChartProvider",
    "DashboardChartProviderError",
    "DashboardChartQuery",
    "DashboardChartSnapshot",
    "DashboardChartSnapshotResponse",
    "DashboardChartValidationError",
    "DashboardCompletedTrade",
    "DashboardMarketSnapshot",
    "DashboardOpenPosition",
    "DashboardPaperAccountSnapshot",
    "DashboardProjectionError",
    "DashboardSnapshot",
    "DashboardSnapshotProvider",
    "DashboardSnapshotResponse",
    "DashboardSourceSystemSnapshot",
    "DashboardSystemSnapshot",
    "DashboardUnavailableResponse",
    "DashboardValue",
    "HistoricalDashboardChartProvider",
    "chart_response",
    "dashboard_response",
    "project_dashboard_snapshot",
]
