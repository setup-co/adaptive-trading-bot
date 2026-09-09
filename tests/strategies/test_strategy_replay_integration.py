from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.regime import (
    MultiTimeframeRegimeComposer,
    TechnicalRegimeEngine,
)
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
    TrendPullbackStrategy,
    assemble_strategy_contexts,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")
REPLAY_HOURS = 200
REPLAY_COUNTS = {
    Timeframe.MINUTES_15: REPLAY_HOURS * 4,
    Timeframe.HOUR_1: REPLAY_HOURS,
    Timeframe.HOURS_4: REPLAY_HOURS // 4,
}


def candle(index: int, timeframe: Timeframe) -> Candle:
    open_time = START + index * timeframe.duration
    close = Decimal(100 + index)
    low = close - Decimal(2)
    if timeframe is Timeframe.MINUTES_15 and index == REPLAY_COUNTS[timeframe] - 4:
        low = close - Decimal(6)
    if timeframe is Timeframe.MINUTES_15 and index == REPLAY_COUNTS[timeframe] - 1:
        low = close - Decimal(10)
    return Candle(
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=close - Decimal(1),
        high=close + Decimal(2),
        low=low,
        close=close,
        volume=Decimal(10 + index % 5),
    )


def evaluate_replay(
    path: Path, *, extra_candles: int = 0
) -> tuple[
    tuple[StrategyEvaluationContext, ...],
    tuple[StrategyEvaluation, ...],
]:
    store = ParquetHistoricalCandleStore(path)
    stored = tuple(
        candle(index, timeframe)
        for timeframe, replay_count in REPLAY_COUNTS.items()
        for index in range(replay_count + extra_candles)
    )
    store.merge(SOURCE, stored)
    series = tuple(
        ReplaySeries(
            SOURCE,
            SYMBOL,
            timeframe,
            START,
            START + replay_count * timeframe.duration,
        )
        for timeframe, replay_count in REPLAY_COUNTS.items()
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(series, clock=FrozenClock(START))
    feature_engine = FeatureEngine()
    structure_engine = MarketStructureEngine()
    regime_engine = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    strategy = TrendPullbackStrategy()
    contexts: list[StrategyEvaluationContext] = []
    evaluations: list[StrategyEvaluation] = []
    for replay_batch in replay:
        observed = observed_candles_from_replay(replay_batch)
        features = feature_engine.process(observed)
        structures = structure_engine.process(observed)
        technical = regime_engine.process(features, structures)
        regimes = composer.process(technical)
        current_contexts = assemble_strategy_contexts(observed, features, structures, regimes)
        contexts.extend(current_contexts)
        evaluations.extend(strategy.evaluate(context) for context in current_contexts)
    return tuple(contexts), tuple(evaluations)


def test_parquet_mtf_replay_evaluation_is_causal_bounded_and_reproducible(
    tmp_path: Path,
) -> None:
    bounded = evaluate_replay(tmp_path / "bounded")
    with_future = evaluate_replay(tmp_path / "future", extra_candles=4)
    repeated = evaluate_replay(tmp_path / "repeated")

    assert bounded == with_future == repeated
    contexts, evaluations = bounded
    assert len(contexts) == REPLAY_COUNTS[Timeframe.MINUTES_15]
    assert all(
        context.observed_at == context.candle_15m.close_time
        and context.multi_timeframe_regime.regime_15m.observed_at <= context.observed_at
        and (
            context.multi_timeframe_regime.regime_1h is None
            or context.multi_timeframe_regime.regime_1h.observed_at <= context.observed_at
        )
        and (
            context.multi_timeframe_regime.regime_4h is None
            or context.multi_timeframe_regime.regime_4h.observed_at <= context.observed_at
        )
        for context in contexts
    )
    final = evaluations[-1]
    assert final.action is CandidateAction.LONG_CANDIDATE
    assert final.reference_price == contexts[-1].candle_15m.close
    assert final.invalidation_reference_price == Decimal(890)
    assert not hasattr(final, "quantity")
