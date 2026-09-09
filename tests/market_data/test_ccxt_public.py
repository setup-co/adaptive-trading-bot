from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.market_data import (
    CcxtPublicOHLCVSource,
    MalformedSourceRowError,
    OHLCVUnsupportedError,
    SourceRequestError,
    SymbolUnsupportedError,
    TimeframeUnsupportedError,
    UnsupportedExchangeError,
)

BTC_USDT = TradingSymbol("BTC", "USDT")
START = datetime(2026, 1, 1, tzinfo=UTC)
START_MS = 1_767_225_600_000


class FakeExchange:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.has: dict[str, object] = {"fetchOHLCV": True}
        self.timeframes: dict[str, str] | None = {"15m": "15m", "1h": "1h", "4h": "4h"}
        self.markets: dict[str, dict[str, object]] = {"BTC/USDT": {"spot": True}}
        self.rows: object = []
        self.loaded = False
        self.fetch_calls: list[tuple[str, str, int, int]] = []
        self.load_error: Exception | None = None
        self.fetch_error: Exception | None = None

    def load_markets(self) -> object:
        self.loaded = True
        if self.load_error is not None:
            raise self.load_error
        return self.markets

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int) -> object:
        self.fetch_calls.append((symbol, timeframe, since, limit))
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.rows


class FakeCcxt:
    exchanges: Sequence[str] = ("fake",)

    def __init__(self) -> None:
        self.exchange: FakeExchange | None = None

    def fake(self, config: dict[str, object]) -> FakeExchange:
        self.exchange = FakeExchange(config)
        return self.exchange


def build_source() -> tuple[CcxtPublicOHLCVSource, FakeExchange]:
    module = FakeCcxt()
    source = CcxtPublicOHLCVSource("FAKE", timeout_ms=7_500, ccxt_module=module)
    assert module.exchange is not None
    return source, module.exchange


def test_adapter_uses_public_rate_limited_finite_timeout_and_stable_identity() -> None:
    source, exchange = build_source()

    assert exchange.loaded is True
    assert exchange.config == {"enableRateLimit": True, "timeout": 7_500}
    assert str(source.source_id) == "ccxt-fake-spot"
    assert not ({"apiKey", "secret", "password"} & exchange.config.keys())


def test_adapter_rejects_unknown_exchange() -> None:
    with pytest.raises(UnsupportedExchangeError, match="unsupported CCXT exchange"):
        CcxtPublicOHLCVSource("missing", ccxt_module=FakeCcxt())


def test_adapter_wraps_market_loading_failure() -> None:
    class BrokenCcxt(FakeCcxt):
        def fake(self, config: dict[str, object]) -> FakeExchange:
            exchange = super().fake(config)
            exchange.load_error = OSError("offline")
            return exchange

    with pytest.raises(SourceRequestError, match="failed to initialize"):
        CcxtPublicOHLCVSource("fake", ccxt_module=BrokenCcxt())


def test_adapter_rejects_missing_ohlcv_capability() -> None:
    class NoOhlcvCcxt(FakeCcxt):
        def fake(self, config: dict[str, object]) -> FakeExchange:
            exchange = super().fake(config)
            exchange.has["fetchOHLCV"] = False
            return exchange

    with pytest.raises(OHLCVUnsupportedError):
        CcxtPublicOHLCVSource("fake", ccxt_module=NoOhlcvCcxt())


def test_adapter_rejects_non_spot_or_missing_symbol() -> None:
    source, exchange = build_source()
    exchange.markets["BTC/USDT"] = {"spot": False, "swap": True}

    with pytest.raises(SymbolUnsupportedError):
        source.fetch_page(BTC_USDT, Timeframe.MINUTES_15, since=START, limit=100)


def test_adapter_rejects_missing_timeframe() -> None:
    source, exchange = build_source()
    exchange.timeframes = {"1h": "1h"}

    with pytest.raises(TimeframeUnsupportedError):
        source.fetch_page(BTC_USDT, Timeframe.MINUTES_15, since=START, limit=100)


def test_adapter_normalizes_exact_decimals_milliseconds_and_order() -> None:
    source, exchange = build_source()
    exchange.rows = [
        [START_MS + 900_000, 101.25, "103.000", "100.5", "102.1", "2.7500"],
        [START_MS, "100.000000000000000001", "102", "99", "101", "1.5"],
    ]

    candles = source.fetch_page(BTC_USDT, Timeframe.MINUTES_15, since=START, limit=2)

    assert exchange.fetch_calls == [("BTC/USDT", "15m", START_MS, 2)]
    assert [candle.open_time for candle in candles] == [
        START,
        datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
    ]
    assert candles[0].open == Decimal("100.000000000000000001")
    assert candles[1].open == Decimal("101.25")
    assert candles[1].volume == Decimal("2.7500")


@pytest.mark.parametrize(
    "row",
    [
        [float(START_MS), "1", "1", "1", "1", "1"],
        [10**30, "1", "1", "1", "1", "1"],
        [START_MS + 1, "1", "1", "1", "1", "1"],
        [START_MS, None, "1", "1", "1", "1"],
        [START_MS, "NaN", "1", "1", "1", "1"],
        [START_MS, "1", "Infinity", "1", "1", "1"],
        [START_MS, "not-a-number", "1", "1", "1", "1"],
        [START_MS, "1", "1", "1", "1"],
    ],
)
def test_adapter_rejects_malformed_rows(row: list[object]) -> None:
    source, exchange = build_source()
    exchange.rows = [row]

    with pytest.raises(MalformedSourceRowError):
        source.fetch_page(BTC_USDT, Timeframe.MINUTES_15, since=START, limit=100)


def test_adapter_wraps_public_request_failure() -> None:
    source, exchange = build_source()
    exchange.fetch_error = TimeoutError("timed out")

    with pytest.raises(SourceRequestError, match="public OHLCV request failed"):
        source.fetch_page(BTC_USDT, Timeframe.MINUTES_15, since=START, limit=100)
