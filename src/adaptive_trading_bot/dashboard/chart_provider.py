"""Read-only chart projection over authoritative historical and Paper facts."""

from typing import Protocol

from adaptive_trading_bot.dashboard.chart_models import (
    DashboardChartCandle,
    DashboardChartIntegrity,
    DashboardChartIntegrityIssue,
    DashboardChartMarker,
    DashboardChartMarkerKind,
    DashboardChartOverlays,
    DashboardChartQuery,
    DashboardChartSnapshot,
)
from adaptive_trading_bot.dashboard.models import DashboardValue
from adaptive_trading_bot.domain import Candle
from adaptive_trading_bot.journal import CompletedTradeJournalSnapshot
from adaptive_trading_bot.paper import PaperPortfolioCoordinatorSnapshot
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    IntegrityIssue,
    IntegrityIssueKind,
    IntegrityReport,
)

_UNAVAILABLE_INDICATOR_REASON = "authoritative historical feature series is unavailable"
_UNSAFE_INTEGRITY_KINDS = frozenset(
    {
        IntegrityIssueKind.SCHEMA_MISMATCH,
        IntegrityIssueKind.MALFORMED_ROW,
    }
)
_SANITIZED_INTEGRITY_MESSAGES = {
    IntegrityIssueKind.DUPLICATE_OPEN_TIME: "duplicate historical candle open time",
    IntegrityIssueKind.OUT_OF_ORDER: "historical candles are not in storage order",
    IntegrityIssueKind.GAP: "historical candle coverage gap",
    IntegrityIssueKind.OVERLAP: "historical candle intervals overlap or are non-contiguous",
}


class DashboardChartProviderError(RuntimeError):
    """Authoritative chart data could not be projected safely."""


class DashboardChartProvider(Protocol):
    """Finite read-only chart query boundary consumed by the API."""

    def chart(self, query: DashboardChartQuery) -> DashboardChartSnapshot: ...


class HistoricalDashboardChartProvider:
    """Project chart facts using only public historical/Paper read contracts."""

    __slots__ = ("_completed_trades", "_paper", "_store")

    def __init__(
        self,
        store: HistoricalCandleStore,
        *,
        paper: PaperPortfolioCoordinatorSnapshot | None = None,
        completed_trades: CompletedTradeJournalSnapshot | None = None,
    ) -> None:
        self._store = store
        self._paper = paper
        self._completed_trades = completed_trades

    def chart(self, query: DashboardChartQuery) -> DashboardChartSnapshot:
        """Read and project one exact native-timeframe range without mutation."""

        if not isinstance(query, DashboardChartQuery):
            raise TypeError("query must be a DashboardChartQuery")
        query.validate()
        candles = self._store.read(
            query.source_id,
            query.symbol,
            query.timeframe,
            start=query.start,
            end=query.end,
        )
        report = self._store.scan_integrity(
            query.source_id,
            query.symbol,
            query.timeframe,
            start=query.start,
            end=query.end,
        )
        _validate_report_identity(query, report)
        _validate_returned_candles(query, candles)
        if any(issue.kind in _UNSAFE_INTEGRITY_KINDS for issue in report.issues):
            raise DashboardChartProviderError("historical storage cannot be read safely")
        if len(candles) > query.limit:
            raise DashboardChartProviderError("historical result exceeds the requested limit")

        chart_candles = tuple(
            DashboardChartCandle(
                candle.open_time,
                candle.close_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            for candle in candles
        )
        issues = tuple(
            sorted(
                (_project_integrity_issue(issue) for issue in report.issues),
                key=lambda item: (
                    item.open_time is None,
                    item.open_time or query.start,
                    item.kind,
                    item.message,
                ),
            )
        )
        markers = _project_markers(query, self._paper, self._completed_trades)
        return DashboardChartSnapshot(
            query.source_id.value,
            str(query.symbol),
            query.timeframe.value,
            query.start,
            query.end,
            query.limit,
            chart_candles,
            DashboardChartIntegrity(
                query.start,
                query.end,
                report.is_clean,
                report.rows_scanned,
                issues,
            ),
            markers,
            DashboardChartOverlays(
                DashboardValue.unavailable(_UNAVAILABLE_INDICATOR_REASON),
                DashboardValue.unavailable(_UNAVAILABLE_INDICATOR_REASON),
                DashboardValue.unavailable(_UNAVAILABLE_INDICATOR_REASON),
                DashboardValue.unavailable(_UNAVAILABLE_INDICATOR_REASON),
            ),
        )


def _validate_report_identity(query: DashboardChartQuery, report: IntegrityReport) -> None:
    if (
        report.source_id != query.source_id
        or report.symbol != query.symbol
        or report.timeframe is not query.timeframe
        or report.start != query.start
        or report.end != query.end
    ):
        raise DashboardChartProviderError("historical integrity report does not match query")


def _validate_returned_candles(
    query: DashboardChartQuery,
    candles: tuple[Candle, ...],
) -> None:
    """Reapply the existing domain contract before dropping candle identity fields."""

    if not isinstance(candles, tuple):
        raise DashboardChartProviderError("historical store returned an invalid candle collection")
    for candle in candles:
        if not isinstance(candle, Candle):
            raise DashboardChartProviderError("historical store returned an invalid candle")
        if candle.symbol != query.symbol or candle.timeframe != query.timeframe:
            raise DashboardChartProviderError(
                "historical candle identity does not match the chart query"
            )
        try:
            Candle(
                candle.symbol,
                candle.timeframe,
                candle.open_time,
                candle.close_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
        except (TypeError, ValueError) as exc:
            raise DashboardChartProviderError(
                "historical store returned a candle that violates the domain contract"
            ) from exc


def _project_integrity_issue(issue: IntegrityIssue) -> DashboardChartIntegrityIssue:
    message = _SANITIZED_INTEGRITY_MESSAGES.get(issue.kind)
    if message is None:
        raise DashboardChartProviderError("historical integrity issue is unsafe to expose")
    return DashboardChartIntegrityIssue(issue.kind.value, message, issue.open_time)


def _project_markers(
    query: DashboardChartQuery,
    paper: PaperPortfolioCoordinatorSnapshot | None,
    completed: CompletedTradeJournalSnapshot | None,
) -> tuple[DashboardChartMarker, ...]:
    markers: list[DashboardChartMarker] = []
    if paper is not None:
        for position in paper.open_positions:
            if position.source_id.value != query.source_id.value or position.symbol != query.symbol:
                continue
            markers.extend(
                (
                    DashboardChartMarker(
                        DashboardChartMarkerKind.OPEN_POSITION_ENTRY,
                        position.fill_at,
                        position.fill_price,
                        position.accounting_fill_id,
                        position.side.value,
                        "open position entry",
                    ),
                    DashboardChartMarker(
                        DashboardChartMarkerKind.OPEN_POSITION_STOP,
                        position.fill_at,
                        position.stop_basis,
                        position.accounting_fill_id,
                        position.side.value,
                        "open position stop basis",
                    ),
                )
            )
    if completed is not None:
        for trade in completed.entries:
            if trade.source_id.value != query.source_id.value or trade.symbol != query.symbol:
                continue
            markers.extend(
                (
                    DashboardChartMarker(
                        DashboardChartMarkerKind.COMPLETED_TRADE_ENTRY,
                        trade.entry_fill_at,
                        trade.entry_fill_price,
                        trade.accounting_entry_fill_id,
                        trade.entry_side.value,
                        "completed trade entry",
                    ),
                    DashboardChartMarker(
                        DashboardChartMarkerKind.COMPLETED_TRADE_EXIT,
                        trade.exit_fill_at,
                        trade.exit_fill_price,
                        trade.accounting_exit_fill_id,
                        trade.exit_side.value,
                        "completed trade exit",
                    ),
                )
            )
    in_range = (marker for marker in markers if query.start <= marker.timestamp < query.end)
    return tuple(
        sorted(
            in_range,
            key=lambda item: (item.timestamp, item.kind.value, item.identity),
        )
    )
