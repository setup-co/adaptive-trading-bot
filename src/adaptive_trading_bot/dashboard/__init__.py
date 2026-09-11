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
from adaptive_trading_bot.dashboard.journal_api_models import (
    DashboardDecisionPageResponse,
    DashboardJournalInvalidQueryResponse,
    DashboardTradePageResponse,
    decision_page_response,
    trade_page_response,
)
from adaptive_trading_bot.dashboard.journal_models import (
    MAX_DASHBOARD_JOURNAL_RECORDS,
    DashboardCompletedTradeRecord,
    DashboardDecisionIdentity,
    DashboardDecisionPage,
    DashboardDecisionQuery,
    DashboardDecisionRecord,
    DashboardDecisionTradeQuery,
    DashboardTradePage,
    DashboardTradeQuery,
)
from adaptive_trading_bot.dashboard.journal_provider import (
    DashboardJournalProvider,
    DashboardJournalProviderError,
    SnapshotDashboardJournalProvider,
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
    "MAX_DASHBOARD_JOURNAL_RECORDS",
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
    "DashboardCompletedTradeRecord",
    "DashboardDecisionIdentity",
    "DashboardDecisionPage",
    "DashboardDecisionPageResponse",
    "DashboardDecisionQuery",
    "DashboardDecisionRecord",
    "DashboardDecisionTradeQuery",
    "DashboardJournalInvalidQueryResponse",
    "DashboardJournalProvider",
    "DashboardJournalProviderError",
    "DashboardMarketSnapshot",
    "DashboardOpenPosition",
    "DashboardPaperAccountSnapshot",
    "DashboardProjectionError",
    "DashboardSnapshot",
    "DashboardSnapshotProvider",
    "DashboardSnapshotResponse",
    "DashboardSourceSystemSnapshot",
    "DashboardSystemSnapshot",
    "DashboardTradePage",
    "DashboardTradePageResponse",
    "DashboardTradeQuery",
    "DashboardUnavailableResponse",
    "DashboardValue",
    "HistoricalDashboardChartProvider",
    "SnapshotDashboardJournalProvider",
    "chart_response",
    "dashboard_response",
    "decision_page_response",
    "project_dashboard_snapshot",
    "trade_page_response",
]
