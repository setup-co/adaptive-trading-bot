"""Exchange-neutral immutable observations for public live spot market data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Protocol

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.market_data.acquisition import SourceRequestError
from adaptive_trading_bot.storage import SourceId

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_V1_LIVE_SYMBOLS = frozenset({TradingSymbol("BTC", "USDT"), TradingSymbol("ETH", "USDT")})


class LiveMarketDataValidationError(ValueError):
    """A public live observation is malformed or outside the V1 boundary."""


class DuplicateLiveCandleError(LiveMarketDataValidationError):
    """One live polling response repeated an identical candle key."""


class ConflictingLiveCandleError(LiveMarketDataValidationError):
    """One live polling response disagreed on values for a candle key."""


class LiveMarketRequestKind(StrEnum):
    """Finite public CCXT operations supported by the live adapter."""

    TICKER = "TICKER"
    OHLCV = "OHLCV"


class LiveMarketRequestError(SourceRequestError):
    """One public request failed with explicit provenance and receipt time."""

    def __init__(
        self,
        *,
        source_id: SourceId,
        symbol: TradingSymbol,
        observed_at: datetime,
        request_kind: LiveMarketRequestKind,
    ) -> None:
        self.source_id = deepcopy(source_id)
        self.symbol = TradingSymbol(symbol.base, symbol.quote)
        self.observed_at = require_utc(observed_at, field="live request failure observed_at")
        self.request_kind = request_kind
        super().__init__(
            f"public {request_kind.value} request failed for "
            f"{self.source_id.value}/{self.symbol} at {self.observed_at.isoformat()}"
        )


class PublicLiveMarketSource(Protocol):
    """Exchange-neutral finite public quote and closed-candle polling boundary."""

    @property
    def source_id(self) -> SourceId:
        """Return deterministic public source provenance."""
        ...

    def fetch_quote(
        self,
        symbol: TradingSymbol,
        *,
        observed_at: datetime,
    ) -> LiveMarketQuote:
        """Return one normalized public quote or raise a typed failure."""
        ...

    def fetch_closed_candles(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        observed_at: datetime,
        limit: int = 3,
    ) -> tuple[LiveClosedCandleObservation, ...]:
        """Return canonical fully closed candles from one finite poll."""
        ...


@dataclass(frozen=True, slots=True)
class LiveMarketQuote:
    """A normalized public spot quote observed at one explicit receipt instant."""

    source_id: SourceId
    symbol: TradingSymbol
    observed_at: datetime
    source_timestamp: datetime | None
    bid: Decimal
    ask: Decimal
    last: Decimal | None
    midpoint: Decimal = field(init=False)
    spread_bps: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId):
            raise TypeError("live quote source_id must be a SourceId")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("live quote symbol must be a TradingSymbol")
        _require_v1_live_symbol(self.symbol)
        object.__setattr__(self, "source_id", deepcopy(self.source_id))
        object.__setattr__(self, "symbol", TradingSymbol(self.symbol.base, self.symbol.quote))
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="live quote observed_at"),
        )
        if self.source_timestamp is not None:
            object.__setattr__(
                self,
                "source_timestamp",
                require_utc(self.source_timestamp, field="live quote source_timestamp"),
            )
        _require_positive_decimal(self.bid, field="bid")
        _require_positive_decimal(self.ask, field="ask")
        if self.bid > self.ask:
            raise LiveMarketDataValidationError("live quote bid cannot exceed ask")
        if self.last is not None:
            _require_positive_decimal(self.last, field="last")
        midpoint, spread_bps = _quote_arithmetic(self.bid, self.ask)
        object.__setattr__(self, "midpoint", midpoint)
        object.__setattr__(self, "spread_bps", spread_bps)

    def validate(self) -> None:
        """Revalidate a quote after crossing a caller-controlled boundary."""

        if not isinstance(self.source_id, SourceId):
            raise TypeError("live quote source_id must be a SourceId")
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("live quote symbol must be a TradingSymbol")
        _require_v1_live_symbol(self.symbol)
        require_utc(self.observed_at, field="live quote observed_at")
        if self.source_timestamp is not None:
            require_utc(self.source_timestamp, field="live quote source_timestamp")
        _require_positive_decimal(self.bid, field="bid")
        _require_positive_decimal(self.ask, field="ask")
        if self.bid > self.ask:
            raise LiveMarketDataValidationError("live quote bid cannot exceed ask")
        if self.last is not None:
            _require_positive_decimal(self.last, field="last")
        midpoint, spread_bps = _quote_arithmetic(self.bid, self.ask)
        if self.midpoint != midpoint or self.spread_bps != spread_bps:
            raise LiveMarketDataValidationError(
                "live quote midpoint or spread arithmetic is inconsistent"
            )


@dataclass(frozen=True, slots=True)
class LiveClosedCandleObservation:
    """One existing domain candle confirmed closed by an explicit receipt time."""

    source_id: SourceId
    candle: Candle
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId):
            raise TypeError("live candle source_id must be a SourceId")
        if not isinstance(self.candle, Candle):
            raise TypeError("live candle observation requires a Candle")
        _require_v1_live_symbol(self.candle.symbol)
        object.__setattr__(self, "source_id", deepcopy(self.source_id))
        object.__setattr__(self, "candle", deepcopy(self.candle))
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="live candle observed_at"),
        )
        self.validate()

    @property
    def symbol(self) -> TradingSymbol:
        """Return the normalized candle symbol."""

        return self.candle.symbol

    @property
    def timeframe(self) -> Timeframe:
        """Return the normalized candle timeframe."""

        return self.candle.timeframe

    def validate(self) -> None:
        """Revalidate identity and fully-closed semantics."""

        if not isinstance(self.source_id, SourceId):
            raise TypeError("live candle source_id must be a SourceId")
        if not isinstance(self.candle, Candle):
            raise TypeError("live candle observation requires a Candle")
        _require_v1_live_symbol(self.candle.symbol)
        require_utc(self.observed_at, field="live candle observed_at")
        if self.candle.close_time > self.observed_at:
            raise LiveMarketDataValidationError(
                "forming candle cannot be emitted as a closed observation"
            )


@dataclass(frozen=True, slots=True)
class LiveMarketFeedSnapshot:
    """Canonical detached observations from one finite public polling cycle."""

    observed_at: datetime
    quotes: tuple[LiveMarketQuote, ...] = ()
    closed_candles: tuple[LiveClosedCandleObservation, ...] = ()

    def __post_init__(self) -> None:
        observed_at = require_utc(self.observed_at, field="live market snapshot observed_at")
        if not isinstance(self.quotes, tuple):
            raise TypeError("live market snapshot quotes must be an immutable tuple")
        if not isinstance(self.closed_candles, tuple):
            raise TypeError("live market snapshot closed_candles must be an immutable tuple")
        quotes = tuple(deepcopy(quote) for quote in self.quotes)
        candles = tuple(deepcopy(observation) for observation in self.closed_candles)
        quote_keys: set[tuple[str, TradingSymbol]] = set()
        candle_keys: set[tuple[str, TradingSymbol, Timeframe, datetime]] = set()
        for quote in quotes:
            if not isinstance(quote, LiveMarketQuote):
                raise TypeError("snapshot quotes must be LiveMarketQuote values")
            quote.validate()
            quote_key = (quote.source_id.value, quote.symbol)
            if quote.observed_at != observed_at or quote_key in quote_keys:
                raise LiveMarketDataValidationError(
                    "snapshot quote receipt time or identity is inconsistent"
                )
            quote_keys.add(quote_key)
        for observation in candles:
            if not isinstance(observation, LiveClosedCandleObservation):
                raise TypeError("snapshot candles must be LiveClosedCandleObservation values")
            observation.validate()
            candle_key = (
                observation.source_id.value,
                observation.symbol,
                observation.timeframe,
                observation.candle.open_time,
            )
            if observation.observed_at != observed_at or candle_key in candle_keys:
                raise LiveMarketDataValidationError(
                    "snapshot candle receipt time or identity is inconsistent"
                )
            candle_keys.add(candle_key)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(
            self,
            "quotes",
            tuple(
                sorted(
                    quotes,
                    key=lambda quote: (
                        quote.source_id.value,
                        quote.symbol.base,
                        quote.symbol.quote,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "closed_candles",
            tuple(
                sorted(
                    candles,
                    key=lambda observation: (
                        observation.source_id.value,
                        observation.symbol.base,
                        observation.symbol.quote,
                        observation.timeframe.value,
                        observation.candle.open_time,
                    ),
                )
            ),
        )


def _require_v1_live_symbol(symbol: TradingSymbol) -> None:
    if symbol not in _V1_LIVE_SYMBOLS:
        raise LiveMarketDataValidationError(f"live symbol {symbol} is outside the V1 allow-list")


def _require_positive_decimal(value: object, *, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"live quote {field} must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise LiveMarketDataValidationError(f"live quote {field} must be finite and positive")


def _quote_arithmetic(bid: Decimal, ask: Decimal) -> tuple[Decimal, Decimal]:
    with localcontext(_DECIMAL_CONTEXT):
        midpoint = (bid + ask) / Decimal(2)
        spread_bps = (ask - bid) / midpoint * Decimal(10_000)
    return midpoint, spread_bps
