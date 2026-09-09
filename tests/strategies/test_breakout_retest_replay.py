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
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    BreakoutRetestStrategy,
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
    assemble_strategy_contexts,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")
REPLAY_COUNTS = {
    Timeframe.MINUTES_15: 801,
    Timeframe.HOUR_1: 200,
    Timeframe.HOURS_4: 50,
}


def candle(index: int, timeframe: Timeframe) -> Candle:
    open_time = START + index * timeframe.duration
    close = Decimal(100 + index + index % 4)
    open_price = close - Decimal(1)
    high = close + Decimal(2)
    low = close - Decimal(2)
    volume = Decimal(10 + index % 5)
    if timeframe is Timeframe.MINUTES_15:
        if index in {794, 795, 797, 798}:
            high = close + Decimal("0.5")
        elif index == 796:
            high = Decimal(901)
        elif index == 799:
            close = Decimal(902)
            open_price = Decimal(901)
            high = Decimal(903)
            low = Decimal(900)
        elif index == 800:
            close = Decimal(902)
            open_price = Decimal(901)
            high = Decimal(903)
            low = Decimal(901)
            volume = Decimal(14)
    return Candle(
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


Evaluation = StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit]


def evaluate_replay(
    path: Path, *, extra_candles: int = 0
) -> tuple[tuple[StrategyEvaluationContext, ...], tuple[Evaluation, ...]]:
    store = ParquetHistoricalCandleStore(path)
    store.merge(
        SOURCE,
        tuple(
            candle(index, timeframe)
            for timeframe, replay_count in REPLAY_COUNTS.items()
            for index in range(replay_count + extra_candles)
        ),
    )
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
    strategy = BreakoutRetestStrategy()
    contexts: list[StrategyEvaluationContext] = []
    evaluations: list[Evaluation] = []
    for replay_batch in replay:
        observed = observed_candles_from_replay(replay_batch)
        features = feature_engine.process(observed)
        structures = structure_engine.process(observed)
        technical = regime_engine.process(features, structures)
        regimes = composer.process(technical)
        current = assemble_strategy_contexts(observed, features, structures, regimes)
        contexts.extend(current)
        evaluations.extend(strategy.evaluate(context) for context in current)
    return tuple(contexts), tuple(evaluations)


def test_breakout_retest_replay_is_bounded_causal_and_reproducible(
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
    breakout = evaluations[-2]
    candidate = evaluations[-1]
    assert breakout.action is CandidateAction.NO_TRADE
    assert breakout.reference_price is None
    assert breakout.reasons_for[-1] is BreakoutRetestReasonCode.BREAKOUT_ARMED
    assert candidate.action is CandidateAction.LONG_CANDIDATE
    assert candidate.rule_audit.bars_waited == 1
    assert candidate.reference_price == contexts[-1].candle_15m.close == Decimal(902)
    assert candidate.invalidation_reference_price == Decimal(901)
    assert not hasattr(candidate, "quantity")
