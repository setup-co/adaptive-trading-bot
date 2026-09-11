import ast
import inspect
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, getcontext, setcontext
from pathlib import Path

import pytest

import adaptive_trading_bot.market_data.ccxt_public as ccxt_module
import adaptive_trading_bot.market_data.live as live_module
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.market_data import (
    CcxtPublicLiveMarketSource,
    ConflictingLiveCandleError,
    DuplicateLiveCandleError,
    LiveClosedCandleObservation,
    LiveMarketDataValidationError,
    LiveMarketFeedSnapshot,
    LiveMarketQuote,
    LiveMarketRequestError,
    LiveMarketRequestKind,
    MalformedSourceRowError,
    MalformedTickerError,
    OHLCVUnsupportedError,
    SymbolUnsupportedError,
    TickerUnsupportedError,
    TimeframeUnsupportedError,
    UnsupportedExchangeError,
)
from adaptive_trading_bot.storage import SourceId

BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
START = datetime(2026, 1, 1, tzinfo=UTC)
START_MS = 1_767_225_600_000


class FakeLiveExchange:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.has: dict[str, object] = {"fetchTicker": True, "fetchOHLCV": True}
        self.timeframes: dict[str, str] | None = {
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
        }
        self.markets: dict[str, dict[str, object]] = {
            "BTC/USDT": {"spot": True},
            "ETH/USDT": {"spot": True},
        }
        self.ticker: object = {
            "bid": "99.75",
            "ask": "100.25",
            "last": "100.125",
            "timestamp": START_MS,
        }
        self.rows: object = []
        self.load_calls = 0
        self.ticker_calls: list[str] = []
        self.ohlcv_calls: list[tuple[str, str, int, int]] = []
        self.ticker_error: Exception | None = None
        self.ohlcv_error: Exception | None = None

    def load_markets(self) -> object:
        self.load_calls += 1
        return self.markets

    def fetch_ticker(self, symbol: str) -> object:
        self.ticker_calls.append(symbol)
        if self.ticker_error is not None:
            raise self.ticker_error
        return self.ticker

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> object:
        self.ohlcv_calls.append((symbol, timeframe, since, limit))
        if self.ohlcv_error is not None:
            raise self.ohlcv_error
        return self.rows


class FakeLiveCcxt:
    exchanges: Sequence[str] = ("fake",)

    def __init__(self) -> None:
        self.exchange: FakeLiveExchange | None = None

    def fake(self, config: dict[str, object]) -> FakeLiveExchange:
        self.exchange = FakeLiveExchange(config)
        return self.exchange


def source() -> tuple[CcxtPublicLiveMarketSource, FakeLiveExchange]:
    fake = FakeLiveCcxt()
    adapter = CcxtPublicLiveMarketSource("FAKE", timeout_ms=7_500, ccxt_module=fake)
    assert fake.exchange is not None
    return adapter, fake.exchange


def milliseconds(value: datetime) -> int:
    return int((value - datetime(1970, 1, 1, tzinfo=UTC)) / timedelta(milliseconds=1))


def row(open_time: datetime, *, close: str = "101") -> list[object]:
    return [milliseconds(open_time), "100", "110", "90", close, "12.5"]


def quote(
    symbol: TradingSymbol = BTC,
    *,
    observed_at: datetime = START + timedelta(hours=1),
) -> LiveMarketQuote:
    return LiveMarketQuote(
        SourceId("ccxt-fake-spot"),
        symbol,
        observed_at,
        START,
        Decimal("99.75"),
        Decimal("100.25"),
        Decimal("100.125"),
    )


def test_constructor_is_public_only_rate_limited_and_eager() -> None:
    adapter, exchange = source()
    parameters = inspect.signature(CcxtPublicLiveMarketSource).parameters

    assert tuple(parameters) == ("exchange_id", "timeout_ms", "ccxt_module")
    assert not {
        "api_key",
        "apiKey",
        "secret",
        "password",
        "passphrase",
        "account",
    }.intersection(parameters)
    assert exchange.config == {"enableRateLimit": True, "timeout": 7_500}
    assert exchange.load_calls == 1
    assert adapter.source_id == SourceId("ccxt-fake-spot")
    for method in (
        "create_order",
        "cancel_order",
        "edit_order",
        "fetch_balance",
        "fetch_positions",
        "withdraw",
    ):
        assert not hasattr(adapter, method)


@pytest.mark.parametrize("symbol", (BTC, ETH))
def test_btc_and_eth_quote_normalization_preserves_public_facts(
    symbol: TradingSymbol,
) -> None:
    adapter, exchange = source()
    observed_at = START + timedelta(seconds=3)

    result = adapter.fetch_quote(symbol, observed_at=observed_at)

    assert exchange.ticker_calls == [str(symbol)]
    assert result.source_id == SourceId("ccxt-fake-spot")
    assert result.symbol == symbol
    assert result.observed_at == observed_at
    assert result.source_timestamp == START
    assert result.bid == Decimal("99.75")
    assert result.ask == Decimal("100.25")
    assert result.last == Decimal("100.125")
    assert result.midpoint == Decimal("100.00")
    assert result.spread_bps == Decimal("50.000")


def test_quote_arithmetic_ignores_ambient_decimal_context() -> None:
    original = getcontext().copy()
    try:
        low = Context(prec=4)
        low.traps[InvalidOperation] = False
        setcontext(low)
        left = quote()
        setcontext(Context(prec=50))
        right = quote()
    finally:
        setcontext(original)

    assert left.midpoint == right.midpoint
    assert left.spread_bps == right.spread_bps


def test_mutating_returned_quote_or_source_identity_cannot_change_adapter() -> None:
    adapter, _ = source()
    returned_source_id = adapter.source_id
    first = adapter.fetch_quote(BTC, observed_at=START)
    object.__setattr__(returned_source_id, "value", "forged")
    object.__setattr__(first, "bid", Decimal(1))
    object.__setattr__(first.source_id, "value", "forged")

    second = adapter.fetch_quote(BTC, observed_at=START)

    assert adapter.source_id == SourceId("ccxt-fake-spot")
    assert second.source_id == SourceId("ccxt-fake-spot")
    assert second.bid == Decimal("99.75")


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("bid", None),
        ("bid", "NaN"),
        ("bid", "Infinity"),
        ("bid", 0),
        ("bid", -1),
        ("ask", None),
        ("ask", "-1"),
        ("last", "NaN"),
        ("last", "0"),
    ),
)
def test_malformed_nonfinite_or_nonpositive_ticker_fails_closed(
    field_name: str, value: object
) -> None:
    adapter, exchange = source()
    assert isinstance(exchange.ticker, dict)
    exchange.ticker[field_name] = value

    with pytest.raises(MalformedTickerError):
        adapter.fetch_quote(BTC, observed_at=START)


def test_inverted_ticker_and_non_mapping_ticker_fail_closed() -> None:
    adapter, exchange = source()
    exchange.ticker = {"bid": "101", "ask": "100", "last": None, "timestamp": None}
    with pytest.raises(MalformedTickerError, match="bid cannot exceed ask"):
        adapter.fetch_quote(BTC, observed_at=START)

    exchange.ticker = []
    with pytest.raises(MalformedTickerError, match="must be a mapping"):
        adapter.fetch_quote(BTC, observed_at=START)


def test_optional_and_invalid_source_timestamps_are_explicit() -> None:
    adapter, exchange = source()
    assert isinstance(exchange.ticker, dict)
    exchange.ticker["timestamp"] = None
    assert adapter.fetch_quote(BTC, observed_at=START).source_timestamp is None

    for invalid in (True, 1.5, "1767225600000", 10**30):
        exchange.ticker["timestamp"] = invalid
        with pytest.raises(MalformedTickerError, match="timestamp"):
            adapter.fetch_quote(BTC, observed_at=START)


def test_ticker_request_failure_has_typed_descriptive_facts() -> None:
    adapter, exchange = source()
    exchange.ticker_error = TimeoutError("offline")
    observed_at = START + timedelta(seconds=5)

    with pytest.raises(LiveMarketRequestError) as captured:
        adapter.fetch_quote(BTC, observed_at=observed_at)

    error = captured.value
    assert error.source_id == adapter.source_id
    assert error.symbol == BTC
    assert error.observed_at == observed_at
    assert error.request_kind is LiveMarketRequestKind.TICKER


def test_missing_ticker_or_ohlcv_capability_fails_during_initialization() -> None:
    class MissingCapabilityCcxt(FakeLiveCcxt):
        def __init__(self, capability: str) -> None:
            super().__init__()
            self.capability = capability

        def fake(self, config: dict[str, object]) -> FakeLiveExchange:
            exchange = super().fake(config)
            exchange.has[self.capability] = False
            return exchange

    with pytest.raises(TickerUnsupportedError):
        CcxtPublicLiveMarketSource("fake", ccxt_module=MissingCapabilityCcxt("fetchTicker"))
    with pytest.raises(OHLCVUnsupportedError):
        CcxtPublicLiveMarketSource("fake", ccxt_module=MissingCapabilityCcxt("fetchOHLCV"))


def test_unknown_exchange_non_v1_or_nonspot_symbol_and_timeframe_fail_before_request() -> None:
    with pytest.raises(UnsupportedExchangeError):
        CcxtPublicLiveMarketSource("missing", ccxt_module=FakeLiveCcxt())
    adapter, exchange = source()
    with pytest.raises(SymbolUnsupportedError, match="V1 allow-list"):
        adapter.fetch_quote(TradingSymbol("SOL", "USDT"), observed_at=START)
    exchange.markets["BTC/USDT"] = {"spot": False, "swap": True}
    with pytest.raises(SymbolUnsupportedError, match="spot market"):
        adapter.fetch_quote(BTC, observed_at=START)
    exchange.markets["BTC/USDT"] = {"spot": True}
    exchange.timeframes = {"1h": "1h"}
    with pytest.raises(TimeframeUnsupportedError):
        adapter.fetch_closed_candles(BTC, Timeframe.MINUTES_15, observed_at=START)
    assert exchange.ticker_calls == []
    assert exchange.ohlcv_calls == []


@pytest.mark.parametrize("timeframe", tuple(Timeframe))
def test_closed_candle_poll_emits_closed_rows_and_withholds_forming(
    timeframe: Timeframe,
) -> None:
    adapter, exchange = source()
    observed_at = START + timedelta(hours=8)
    current_open = align_timestamp(observed_at, timeframe)
    exchange.rows = [
        row(current_open),
        row(current_open - 2 * timeframe.duration),
        row(current_open - timeframe.duration),
    ]

    result = adapter.fetch_closed_candles(BTC, timeframe, observed_at=observed_at, limit=3)

    assert [item.candle.open_time for item in result] == [
        current_open - 2 * timeframe.duration,
        current_open - timeframe.duration,
    ]
    assert all(item.candle.close_time <= observed_at for item in result)
    assert all(item.source_id == adapter.source_id for item in result)
    expected_since = milliseconds(current_open - 2 * timeframe.duration)
    assert exchange.ohlcv_calls == [("BTC/USDT", timeframe.value, expected_since, 3)]


def test_duplicate_and_conflicting_live_rows_fail_closed() -> None:
    adapter, exchange = source()
    exchange.rows = [row(START), row(START)]
    with pytest.raises(DuplicateLiveCandleError):
        adapter.fetch_closed_candles(
            BTC, Timeframe.MINUTES_15, observed_at=START + timedelta(hours=1)
        )

    exchange.rows = [row(START), row(START, close="102")]
    with pytest.raises(ConflictingLiveCandleError):
        adapter.fetch_closed_candles(
            BTC, Timeframe.MINUTES_15, observed_at=START + timedelta(hours=1)
        )


def test_malformed_ohlcv_and_request_failure_remain_typed() -> None:
    adapter, exchange = source()
    exchange.rows = [[START_MS, "100", "bad", "90", "101", "1"]]
    with pytest.raises(MalformedSourceRowError):
        adapter.fetch_closed_candles(
            BTC, Timeframe.MINUTES_15, observed_at=START + timedelta(hours=1)
        )

    exchange.ohlcv_error = TimeoutError("offline")
    with pytest.raises(LiveMarketRequestError) as captured:
        adapter.fetch_closed_candles(
            BTC, Timeframe.MINUTES_15, observed_at=START + timedelta(hours=1)
        )
    assert captured.value.request_kind is LiveMarketRequestKind.OHLCV


def test_feed_snapshot_is_canonical_immutable_and_detached() -> None:
    observed_at = START + timedelta(hours=1)
    btc_quote = quote(BTC, observed_at=observed_at)
    eth_quote = quote(ETH, observed_at=observed_at)
    candle = Candle(
        BTC,
        Timeframe.MINUTES_15,
        START,
        START + timedelta(minutes=15),
        Decimal(100),
        Decimal(110),
        Decimal(90),
        Decimal(101),
        Decimal(1),
    )
    candle_observation = LiveClosedCandleObservation(
        SourceId("ccxt-fake-spot"), candle, observed_at
    )
    snapshot = LiveMarketFeedSnapshot(
        observed_at,
        (eth_quote, btc_quote),
        (candle_observation,),
    )

    assert tuple(item.symbol for item in snapshot.quotes) == (BTC, ETH)
    object.__setattr__(btc_quote.source_id, "value", "forged")
    object.__setattr__(candle, "close", Decimal(999))
    assert snapshot.quotes[0].source_id == SourceId("ccxt-fake-spot")
    assert snapshot.closed_candles[0].candle.close == Decimal(101)
    with pytest.raises(FrozenInstanceError):
        snapshot.observed_at = START  # type: ignore[misc]


def test_live_contract_rejects_forming_non_v1_and_non_utc_observations() -> None:
    candle = Candle(
        BTC,
        Timeframe.MINUTES_15,
        START,
        START + timedelta(minutes=15),
        Decimal(100),
        Decimal(110),
        Decimal(90),
        Decimal(101),
        Decimal(1),
    )
    with pytest.raises(LiveMarketDataValidationError, match="forming"):
        LiveClosedCandleObservation(
            SourceId("ccxt-fake-spot"), candle, START + timedelta(minutes=14)
        )
    with pytest.raises(LiveMarketDataValidationError, match="V1"):
        quote(TradingSymbol("SOL", "USDT"))
    with pytest.raises(ValueError, match="UTC"):
        quote(observed_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="UTC"):
        quote(observed_at=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))))


def test_adapter_and_contracts_have_no_private_or_trading_side_effect_surface() -> None:
    forbidden_imports = (
        "adaptive_trading_bot.paper",
        "adaptive_trading_bot.risk",
        "adaptive_trading_bot.orders",
        "adaptive_trading_bot.strategies",
        "sqlalchemy",
    )
    for module in (ccxt_module, live_module):
        path = Path(module.__file__ or "")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            imported.startswith(forbidden)
            for imported in imports
            for forbidden in forbidden_imports
        )
    adapter_fields = {field.name for field in fields(LiveMarketQuote)}
    assert not {
        "api_key",
        "secret",
        "account_id",
        "order_id",
        "quantity",
        "side",
    }.intersection(adapter_fields)
