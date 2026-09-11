"""CCXT public REST adapter for normalized spot OHLCV candles.

CCXT may decode JSON numbers as binary floats before this boundary sees them. Converting with
``Decimal(str(value))`` is deterministic and avoids adding further float arithmetic, but cannot
recover lexical precision already lost by the upstream JSON decoder.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from re import fullmatch
from typing import Protocol, cast

import ccxt

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.market_data.acquisition import (
    MalformedSourceRowError,
    MalformedTickerError,
    OHLCVUnsupportedError,
    SourceRequestError,
    SymbolUnsupportedError,
    TickerUnsupportedError,
    TimeframeUnsupportedError,
    UnsupportedExchangeError,
)
from adaptive_trading_bot.market_data.live import (
    ConflictingLiveCandleError,
    DuplicateLiveCandleError,
    LiveClosedCandleObservation,
    LiveMarketQuote,
    LiveMarketRequestError,
    LiveMarketRequestKind,
)
from adaptive_trading_bot.storage import SourceId

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class _CcxtExchange(Protocol):
    has: Mapping[str, object]
    timeframes: Mapping[str, str] | None
    markets: Mapping[str, Mapping[str, object]]

    def load_markets(self) -> object: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
    ) -> object: ...

    def fetch_ticker(self, symbol: str) -> object: ...


class _CcxtModule(Protocol):
    exchanges: Sequence[str]


class CcxtPublicOHLCVSource:
    """Public-only CCXT spot adapter with eager capability validation."""

    def __init__(
        self,
        exchange_id: str,
        *,
        timeout_ms: int = 10_000,
        ccxt_module: _CcxtModule = ccxt,
    ) -> None:
        exchange_id = exchange_id.strip().lower()
        if not fullmatch(r"[a-z0-9]+", exchange_id) or exchange_id not in ccxt_module.exchanges:
            raise UnsupportedExchangeError(f"unsupported CCXT exchange: {exchange_id!r}")
        if not 1 <= timeout_ms <= 60_000:
            raise ValueError("timeout_ms must be between 1 and 60000")

        try:
            exchange_factory = cast(
                Callable[[dict[str, object]], _CcxtExchange],
                getattr(ccxt_module, exchange_id),
            )
            # Public requests need no API key, secret, password, or account identifier.
            self._exchange = exchange_factory({"enableRateLimit": True, "timeout": timeout_ms})
            self._exchange.load_markets()
        except Exception as error:
            raise SourceRequestError(
                f"failed to initialize public CCXT exchange {exchange_id}"
            ) from error

        if not self._exchange.has.get("fetchOHLCV"):
            raise OHLCVUnsupportedError(f"exchange {exchange_id} does not support fetchOHLCV")
        self._source_id = SourceId(f"ccxt-{exchange_id}-spot")

    @property
    def source_id(self) -> SourceId:
        """Return deterministic, path-safe public spot provenance."""

        return SourceId(self._source_id.value)

    def fetch_page(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[Candle, ...]:
        """Fetch and normalize one public REST page without leaking CCXT rows."""

        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        canonical_symbol = str(symbol)
        market = self._exchange.markets.get(canonical_symbol)
        if market is None or market.get("spot") is not True:
            raise SymbolUnsupportedError(
                f"exchange does not expose supported spot market {canonical_symbol}"
            )
        if self._exchange.timeframes is None or timeframe.value not in self._exchange.timeframes:
            raise TimeframeUnsupportedError(
                f"exchange does not support timeframe {timeframe.value}"
            )

        since_ms = _datetime_to_milliseconds(require_utc(since, field="since"))
        try:
            raw_page = self._exchange.fetch_ohlcv(
                canonical_symbol,
                timeframe.value,
                since_ms,
                limit,
            )
        except Exception as error:
            raise SourceRequestError(
                f"public OHLCV request failed for {self.source_id}/{canonical_symbol}/"
                f"{timeframe.value}"
            ) from error
        if not isinstance(raw_page, Sequence) or isinstance(raw_page, str | bytes):
            raise MalformedSourceRowError("CCXT fetchOHLCV response must be a sequence")

        candles = [self._normalize_row(row, symbol, timeframe) for row in raw_page]
        return tuple(sorted(candles, key=lambda candle: candle.open_time))

    @staticmethod
    def _normalize_row(
        row: object,
        symbol: TradingSymbol,
        timeframe: Timeframe,
    ) -> Candle:
        if not isinstance(row, Sequence) or isinstance(row, str | bytes) or len(row) < 6:
            raise MalformedSourceRowError("CCXT OHLCV row must contain at least six values")
        timestamp = row[0]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise MalformedSourceRowError("CCXT candle timestamp must be an integer millisecond")
        try:
            open_time = _EPOCH + timedelta(milliseconds=timestamp)
            values = tuple(
                _decimal_from_source(value, field=field)
                for value, field in zip(
                    row[1:6], ("open", "high", "low", "close", "volume"), strict=True
                )
            )
            return Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + timeframe.duration,
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
                volume=values[4],
            )
        except (ArithmeticError, OverflowError, TypeError, ValueError) as error:
            raise MalformedSourceRowError(f"invalid CCXT OHLCV row: {error}") from error


def _decimal_from_source(value: object, *, field: str) -> Decimal:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, int | float | str | Decimal)
    ):
        raise MalformedSourceRowError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise MalformedSourceRowError(f"{field} must be numeric") from error
    if not result.is_finite():
        raise MalformedSourceRowError(f"{field} must be finite")
    return result


def _datetime_to_milliseconds(value: datetime) -> int:
    delta = value - _EPOCH
    if delta.microseconds % 1_000:
        raise ValueError("since must have exact millisecond precision")
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


class CcxtPublicLiveMarketSource(CcxtPublicOHLCVSource):
    """Finite, public-only CCXT ticker and closed-candle polling adapter."""

    def __init__(
        self,
        exchange_id: str,
        *,
        timeout_ms: int = 10_000,
        ccxt_module: _CcxtModule = ccxt,
    ) -> None:
        super().__init__(exchange_id, timeout_ms=timeout_ms, ccxt_module=ccxt_module)
        if not self._exchange.has.get("fetchTicker"):
            raise TickerUnsupportedError(
                f"exchange {exchange_id.strip().lower()} does not support fetchTicker"
            )

    def fetch_quote(
        self,
        symbol: TradingSymbol,
        *,
        observed_at: datetime,
    ) -> LiveMarketQuote:
        """Fetch one public spot ticker at an explicit local receipt instant."""

        observed_at = require_utc(observed_at, field="live quote observed_at")
        canonical_symbol = self._require_v1_spot_symbol(symbol)
        try:
            raw_ticker = self._exchange.fetch_ticker(canonical_symbol)
        except Exception as error:
            raise LiveMarketRequestError(
                source_id=self.source_id,
                symbol=symbol,
                observed_at=observed_at,
                request_kind=LiveMarketRequestKind.TICKER,
            ) from error
        if not isinstance(raw_ticker, Mapping):
            raise MalformedTickerError("CCXT fetchTicker response must be a mapping")
        try:
            bid = _decimal_from_source(raw_ticker.get("bid"), field="bid")
            ask = _decimal_from_source(raw_ticker.get("ask"), field="ask")
            raw_last = raw_ticker.get("last")
            last = None if raw_last is None else _decimal_from_source(raw_last, field="last")
            source_timestamp = _optional_source_timestamp(raw_ticker.get("timestamp"))
            return LiveMarketQuote(
                source_id=self.source_id,
                symbol=symbol,
                observed_at=observed_at,
                source_timestamp=source_timestamp,
                bid=bid,
                ask=ask,
                last=last,
            )
        except (
            ArithmeticError,
            MalformedSourceRowError,
            OverflowError,
            TypeError,
            ValueError,
        ) as error:
            raise MalformedTickerError(f"invalid CCXT ticker: {error}") from error

    def fetch_closed_candles(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        observed_at: datetime,
        limit: int = 3,
    ) -> tuple[LiveClosedCandleObservation, ...]:
        """Poll recent OHLCV and emit only rows closed by the receipt instant."""

        observed_at = require_utc(observed_at, field="live candle observed_at")
        self._require_v1_spot_symbol(symbol)
        if not isinstance(timeframe, Timeframe):
            raise TimeframeUnsupportedError("live timeframe is outside the V1 allow-list")
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        since = align_timestamp(observed_at, timeframe) - (limit - 1) * timeframe.duration
        try:
            candles = self.fetch_page(
                symbol,
                timeframe,
                since=since,
                limit=limit,
            )
        except SourceRequestError as error:
            raise LiveMarketRequestError(
                source_id=self.source_id,
                symbol=symbol,
                observed_at=observed_at,
                request_kind=LiveMarketRequestKind.OHLCV,
            ) from error

        by_open: dict[datetime, Candle] = {}
        for candle in candles:
            existing = by_open.get(candle.open_time)
            if existing is not None:
                if existing == candle:
                    raise DuplicateLiveCandleError(
                        f"duplicate live candle at {candle.open_time.isoformat()}"
                    )
                raise ConflictingLiveCandleError(
                    f"conflicting live candle at {candle.open_time.isoformat()}"
                )
            by_open[candle.open_time] = candle
        return tuple(
            LiveClosedCandleObservation(self.source_id, candle, observed_at)
            for candle in by_open.values()
            if candle.close_time <= observed_at
        )

    def _require_v1_spot_symbol(self, symbol: TradingSymbol) -> str:
        if not isinstance(symbol, TradingSymbol):
            raise TypeError("live symbol must be a TradingSymbol")
        if symbol not in {
            TradingSymbol("BTC", "USDT"),
            TradingSymbol("ETH", "USDT"),
        }:
            raise SymbolUnsupportedError(f"symbol {symbol} is outside the V1 allow-list")
        canonical_symbol = str(symbol)
        market = self._exchange.markets.get(canonical_symbol)
        if market is None or market.get("spot") is not True:
            raise SymbolUnsupportedError(
                f"exchange does not expose supported spot market {canonical_symbol}"
            )
        return canonical_symbol


def _optional_source_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("ticker timestamp must be an integer millisecond or null")
    try:
        return _EPOCH + timedelta(milliseconds=value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("ticker timestamp is outside the supported range") from error
