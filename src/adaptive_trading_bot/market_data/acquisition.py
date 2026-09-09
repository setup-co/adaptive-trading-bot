"""Exchange-neutral bounded OHLCV acquisition and deterministic pagination."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from adaptive_trading_bot.domain import Candle, Clock, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.storage import (
    CandleConflictError,
    HistoricalCandleStore,
    IntegrityReport,
    MergeResult,
    SourceId,
)

_V1_SYMBOLS = frozenset({TradingSymbol("BTC", "USDT"), TradingSymbol("ETH", "USDT")})


class MarketDataAcquisitionError(Exception):
    """Base error for public historical market-data acquisition."""


class UnsupportedExchangeError(MarketDataAcquisitionError):
    """The configured CCXT exchange does not exist."""


class OHLCVUnsupportedError(MarketDataAcquisitionError):
    """The source does not support public OHLCV retrieval."""


class SymbolUnsupportedError(MarketDataAcquisitionError):
    """The requested spot symbol is not supported."""


class TimeframeUnsupportedError(MarketDataAcquisitionError):
    """The requested timeframe is not supported."""


class MalformedSourceRowError(MarketDataAcquisitionError):
    """A source returned a row that cannot become a canonical Candle."""


class SourceRequestError(MarketDataAcquisitionError):
    """A public source request failed."""


class PaginationError(MarketDataAcquisitionError):
    """Pagination ended prematurely or failed to make forward progress."""


class ConflictingSourceCandleError(MarketDataAcquisitionError):
    """The source or stored dataset contains conflicting values for one candle key."""


class InvalidImportRequestError(MarketDataAcquisitionError):
    """A bounded import request violates the V1 contract."""


class PublicOHLCVSource(Protocol):
    """Exchange-neutral public source that emits normalized Candle pages."""

    @property
    def source_id(self) -> SourceId:
        """Return stable provenance identity for this source."""
        ...

    def fetch_page(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[Candle, ...]:
        """Fetch one bounded page at or after ``since`` as normalized candles."""
        ...


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Structured result of one bounded historical import."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    effective_end: datetime
    fetched: int
    merge: MergeResult
    integrity: IntegrityReport


class BoundedOHLCVImporter:
    """Import a UTC-aligned half-open range without persisting open candles."""

    def __init__(
        self,
        store: HistoricalCandleStore,
        clock: Clock,
        *,
        page_limit: int = 500,
    ) -> None:
        if not 1 <= page_limit <= 1_000:
            raise ValueError("page_limit must be between 1 and 1000")
        self._store = store
        self._clock = clock
        self._page_limit = page_limit

    def import_range(
        self,
        source: PublicOHLCVSource,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime,
        end: datetime,
    ) -> ImportResult:
        """Import complete candles in ``[start, end)`` and scan resulting coverage."""

        start, end = self.validate_request(symbol, timeframe, start, end)
        cutoff = min(end, require_utc(self._clock.now(), field="clock.now()"))
        effective_end = max(start, min(end, align_timestamp(cutoff, timeframe)))
        candles_by_open: dict[datetime, Candle] = {}
        cursor = start

        while cursor < effective_end:
            page = source.fetch_page(
                symbol,
                timeframe,
                since=cursor,
                limit=self._page_limit,
            )
            if not page:
                raise PaginationError(
                    f"source returned an empty page before effective end at {cursor.isoformat()}"
                )

            progressing_opens: list[datetime] = []
            for candle in page:
                self._validate_source_candle(candle, symbol, timeframe)
                if candle.open_time < start or candle.open_time >= end:
                    continue
                if candle.close_time > cutoff or candle.open_time >= effective_end:
                    continue

                existing = candles_by_open.get(candle.open_time)
                if existing is not None and existing != candle:
                    raise ConflictingSourceCandleError(
                        f"source returned conflicting candle at {candle.open_time.isoformat()}"
                    )
                candles_by_open[candle.open_time] = candle
                if candle.open_time >= cursor:
                    progressing_opens.append(candle.open_time)

            if not progressing_opens:
                raise PaginationError(
                    f"source page made no eligible forward progress from {cursor.isoformat()}"
                )
            next_cursor = max(progressing_opens) + timeframe.duration
            if next_cursor <= cursor:
                raise PaginationError(f"source pagination repeated cursor {cursor.isoformat()}")
            cursor = min(next_cursor, effective_end)

        candles = tuple(candles_by_open[key] for key in sorted(candles_by_open))
        try:
            merge = self._store.merge(source.source_id, candles)
        except CandleConflictError as error:
            raise ConflictingSourceCandleError(str(error)) from error
        integrity = self._store.scan_integrity(
            source.source_id,
            symbol,
            timeframe,
            start=start,
            end=effective_end,
        )
        return ImportResult(
            source_id=source.source_id,
            symbol=symbol,
            timeframe=timeframe,
            requested_start=start,
            requested_end=end,
            effective_end=effective_end,
            fetched=len(candles),
            merge=merge,
            integrity=integrity,
        )

    @staticmethod
    def validate_request(
        symbol: TradingSymbol,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime]:
        """Validate and canonicalize the application-level bounded import request."""

        if symbol not in _V1_SYMBOLS:
            raise InvalidImportRequestError(f"symbol {symbol} is outside the V1 allow-list")
        if not isinstance(timeframe, Timeframe):
            raise InvalidImportRequestError("timeframe is outside the V1 allow-list")
        try:
            start = require_utc(start, field="start")
            end = require_utc(end, field="end")
        except ValueError as error:
            raise InvalidImportRequestError(str(error)) from error
        if start >= end:
            raise InvalidImportRequestError("start must be before end")
        if align_timestamp(start, timeframe) != start:
            raise InvalidImportRequestError("start must align to the timeframe boundary")
        if align_timestamp(end, timeframe) != end:
            raise InvalidImportRequestError("end must align to the timeframe boundary")
        return start, end

    @staticmethod
    def _validate_source_candle(
        candle: Candle,
        symbol: TradingSymbol,
        timeframe: Timeframe,
    ) -> None:
        if candle.symbol != symbol or candle.timeframe is not timeframe:
            raise MalformedSourceRowError("source returned a candle for a different series")
