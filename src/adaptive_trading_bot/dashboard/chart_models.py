"""Immutable read models for finite historical Dashboard charts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from adaptive_trading_bot.dashboard.models import DashboardValue
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import SourceId

MAX_DASHBOARD_CHART_CANDLES = 2_000
_V1_SYMBOLS = frozenset(
    {
        TradingSymbol("BTC", "USDT"),
        TradingSymbol("ETH", "USDT"),
    }
)


class DashboardChartValidationError(ValueError):
    """A chart query or detached chart result is invalid."""


class DashboardChartMarkerKind(StrEnum):
    """Authoritative Paper/Journal events displayed on a chart."""

    OPEN_POSITION_ENTRY = "OPEN_POSITION_ENTRY"
    OPEN_POSITION_STOP = "OPEN_POSITION_STOP"
    COMPLETED_TRADE_ENTRY = "COMPLETED_TRADE_ENTRY"
    COMPLETED_TRADE_EXIT = "COMPLETED_TRADE_EXIT"


@dataclass(frozen=True, slots=True)
class DashboardChartQuery:
    """One bounded native-timeframe historical candle request."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    start: datetime
    end: datetime
    limit: int = MAX_DASHBOARD_CHART_CANDLES

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Fail closed on ambiguous identity, time, or range size."""

        if not isinstance(self.source_id, SourceId):
            raise TypeError("chart source_id must be a SourceId")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("chart symbol must be a TradingSymbol")
        if self.symbol not in _V1_SYMBOLS:
            raise DashboardChartValidationError("chart symbol is outside the V1 universe")
        if not isinstance(self.timeframe, Timeframe):
            raise TypeError("chart timeframe must be a V1 Timeframe")
        require_utc(self.start, field="chart start")
        require_utc(self.end, field="chart end")
        if self.start >= self.end:
            raise DashboardChartValidationError("chart start must be before end")
        if type(self.limit) is not int:
            raise TypeError("chart limit must be an int and not bool")
        if not 1 <= self.limit <= MAX_DASHBOARD_CHART_CANDLES:
            raise DashboardChartValidationError(
                f"chart limit must be between 1 and {MAX_DASHBOARD_CHART_CANDLES}"
            )
        if self.end - self.start > self.timeframe.duration * self.limit:
            raise DashboardChartValidationError("chart range exceeds the requested candle limit")


@dataclass(frozen=True, slots=True)
class DashboardChartCandle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def validate(self) -> None:
        require_utc(self.open_time, field="chart candle open_time")
        require_utc(self.close_time, field="chart candle close_time")
        for name in ("open", "high", "low", "close", "volume"):
            _require_decimal(getattr(self, name), field=f"chart candle {name}")

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class DashboardChartIntegrityIssue:
    kind: str
    message: str
    open_time: datetime | None

    def validate(self) -> None:
        if self.open_time is not None:
            require_utc(self.open_time, field="chart integrity open_time")

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class DashboardChartIntegrity:
    start: datetime
    end: datetime
    is_clean: bool
    rows_scanned: int
    issues: tuple[DashboardChartIntegrityIssue, ...]

    def validate(self) -> None:
        require_utc(self.start, field="chart integrity start")
        require_utc(self.end, field="chart integrity end")
        if type(self.is_clean) is not bool:
            raise TypeError("chart integrity is_clean must be a bool")
        if type(self.rows_scanned) is not int or self.rows_scanned < 0:
            raise DashboardChartValidationError(
                "chart integrity rows_scanned must be a non-negative int"
            )
        if self.is_clean != (not self.issues):
            raise DashboardChartValidationError("chart integrity clean state is inconsistent")
        for issue in self.issues:
            issue.validate()
        issue_order = tuple(
            sorted(
                self.issues,
                key=lambda item: (
                    item.open_time is None,
                    item.open_time or self.start,
                    item.kind,
                    item.message,
                ),
            )
        )
        if self.issues != issue_order:
            raise DashboardChartValidationError("chart integrity issues must be chronological")

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class DashboardChartMarker:
    kind: DashboardChartMarkerKind
    timestamp: datetime
    price: Decimal
    identity: str
    side: str
    label: str

    def validate(self) -> None:
        require_utc(self.timestamp, field="chart marker timestamp")
        _require_decimal(self.price, field="chart marker price")

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class DashboardChartOverlays:
    ema_20: DashboardValue[str]
    ema_50: DashboardValue[str]
    vwap: DashboardValue[str]
    key_levels: DashboardValue[str]

    def validate(self) -> None:
        for value in (self.ema_20, self.ema_50, self.vwap, self.key_levels):
            value.validate()

    def __post_init__(self) -> None:
        self.validate()


@dataclass(frozen=True, slots=True)
class DashboardChartSnapshot:
    source_id: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    limit: int
    candles: tuple[DashboardChartCandle, ...]
    integrity: DashboardChartIntegrity
    markers: tuple[DashboardChartMarker, ...]
    overlays: DashboardChartOverlays

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate the complete detached chart before API serialization."""

        require_utc(self.start, field="chart snapshot start")
        require_utc(self.end, field="chart snapshot end")
        query = DashboardChartQuery(
            SourceId(self.source_id),
            TradingSymbol.parse(self.symbol),
            Timeframe(self.timeframe),
            self.start,
            self.end,
            self.limit,
        )
        if (
            query.source_id.value != self.source_id
            or str(query.symbol) != self.symbol
            or query.timeframe.value != self.timeframe
        ):
            raise DashboardChartValidationError("chart snapshot identity is not canonical")
        if len(self.candles) > self.limit:
            raise DashboardChartValidationError("chart candle count exceeds the requested limit")
        for candle in self.candles:
            candle.validate()
            if not self.start <= candle.open_time < self.end:
                raise DashboardChartValidationError("chart candle is outside the requested range")
        if self.candles != tuple(sorted(self.candles, key=lambda item: item.open_time)):
            raise DashboardChartValidationError("chart candles must be chronological")
        self.integrity.validate()
        if self.integrity.start != self.start or self.integrity.end != self.end:
            raise DashboardChartValidationError("chart integrity range does not match query")
        for marker in self.markers:
            marker.validate()
            if not self.start <= marker.timestamp < self.end:
                raise DashboardChartValidationError("chart marker is outside the requested range")
        marker_order = tuple(
            sorted(
                self.markers,
                key=lambda item: (item.timestamp, item.kind.value, item.identity),
            )
        )
        if self.markers != marker_order:
            raise DashboardChartValidationError("chart markers must be chronological")
        self.overlays.validate()


def _require_decimal(value: object, *, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    if not value.is_finite():
        raise DashboardChartValidationError(f"{field} must be finite")
