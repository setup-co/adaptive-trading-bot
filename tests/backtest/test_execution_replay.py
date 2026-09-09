from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.backtest import (
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
    MarketOrderRequest,
    OrderSide,
)
from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def candle(index: int) -> Candle:
    open_time = START + index * Timeframe.MINUTES_15.duration
    value = Decimal(100 + index * 10)
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=value,
        high=value + Decimal(10),
        low=value - Decimal(10),
        close=value + Decimal(1),
        volume=Decimal(5),
    )


def test_replay_observation_creates_order_that_fills_only_at_next_open(tmp_path: Path) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candles = (candle(0), candle(1), candle(2))
    store.merge(SOURCE, candles)
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        (
            ReplaySeries(
                SOURCE,
                SYMBOL,
                Timeframe.MINUTES_15,
                START,
                candle(0).close_time,
            ),
        ),
        clock=FrozenClock(START),
    )
    replay_batch = next(replay)
    observed = observed_candles_from_replay(replay_batch)
    FeatureEngine().process(observed)
    MarketStructureEngine().process(observed)
    request = MarketOrderRequest(
        "scripted-after-observation",
        replay_batch.observed_at,
        SYMBOL,
        OrderSide.BUY,
        Decimal(1),
    )

    result = BacktestExecutionSimulator(HistoricalExecutionSource(store)).run(
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(),
        (request,),
        start=START,
        end=candle(2).close_time,
    )

    fill = result.fills[0]
    assert request.created_at == candle(0).close_time
    assert fill.fill_at == candle(1).open_time
    assert fill.reference_open_price == candle(1).open
    assert fill.reference_open_price != candle(0).close
