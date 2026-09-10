from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    RangeMeanReversionStrategy,
    RangeStatus,
    StrategyEvaluation,
    StrategyEvaluationContext,
    assemble_strategy_contexts,
)

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")
REPLAY_COUNTS = {
    Timeframe.MINUTES_15: 820,
    Timeframe.HOUR_1: 205,
    Timeframe.HOURS_4: 52,
}

Evaluation = StrategyEvaluation[RangeMeanReversionReasonCode, RangeMeanReversionRuleAudit]


def _candle(index: int, timeframe: Timeframe, *, breakout: bool = False) -> Candle:
    open_time = START + index * timeframe.duration
    amplitude = Decimal(2) if index < REPLAY_COUNTS[timeframe] - 41 else Decimal("0.1")
    close = Decimal(100) + Decimal(index % 4) * amplitude
    open_price = close
    high = Decimal(108)
    low = Decimal(92)
    if timeframe is Timeframe.MINUTES_15:
        if 740 <= index < 760:
            close = Decimal(50) if index % 2 == 0 else Decimal(150)
            open_price = close
            high = Decimal(151)
            low = Decimal(49)
        if index == 801:
            open_price = Decimal(90)
            close = Decimal(90)
            low = Decimal(89)
        if index in {802, 810}:
            low = Decimal(80)
        if index in {806, 814}:
            high = Decimal(120)
        if index == 817:
            open_price = Decimal(90)
            close = Decimal(95)
            low = Decimal(80)
        if breakout and index == 818:
            open_price = Decimal(122)
            close = Decimal("123.1")
            high = Decimal(124)
    return Candle(
        SYMBOL,
        timeframe,
        open_time,
        open_time + timeframe.duration,
        open_price,
        high,
        low,
        close,
        Decimal(10 + index % 5),
    )


def _run(
    path: Path, *, extra_candles: int = 0, breakout: bool = False
) -> tuple[tuple[StrategyEvaluationContext, ...], tuple[Evaluation, ...]]:
    store = ParquetHistoricalCandleStore(path)
    store.merge(
        SOURCE,
        tuple(
            _candle(index, timeframe, breakout=breakout)
            for timeframe, count in REPLAY_COUNTS.items()
            for index in range(count + extra_candles)
        ),
    )
    series = tuple(
        ReplaySeries(
            SOURCE,
            SYMBOL,
            timeframe,
            START,
            START + count * timeframe.duration,
        )
        for timeframe, count in REPLAY_COUNTS.items()
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(series, clock=FrozenClock(START))
    feature_engine = FeatureEngine()
    structure_engine = MarketStructureEngine()
    regime_engine = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    strategy = RangeMeanReversionStrategy()
    contexts: list[StrategyEvaluationContext] = []
    evaluations: list[Evaluation] = []
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        features = feature_engine.process(observed)
        structures = structure_engine.process(observed)
        technical = regime_engine.process(features, structures)
        regimes = composer.process(technical)
        current = assemble_strategy_contexts(observed, features, structures, regimes)
        contexts.extend(current)
        evaluations.extend(strategy.evaluate(context) for context in current)
    return tuple(contexts), tuple(evaluations)


def test_range_mean_reversion_replay_is_causal_bounded_and_reproducible(
    tmp_path: Path,
) -> None:
    bounded = _run(tmp_path / "bounded")
    with_future = _run(tmp_path / "future", extra_candles=4)
    repeated = _run(tmp_path / "repeated")

    assert bounded == with_future == repeated
    contexts, evaluations = bounded
    assert len(evaluations) == REPLAY_COUNTS[Timeframe.MINUTES_15]
    assert all(item.action is CandidateAction.NO_TRADE for item in evaluations[:817])
    assert evaluations[816].rule_audit.range_status is RangeStatus.VALID_RANGE
    candidate = evaluations[817]
    assert candidate.action is CandidateAction.LONG_CANDIDATE
    assert candidate.rule_audit.range_status is RangeStatus.VALID_RANGE
    assert candidate.observed_at == contexts[817].observed_at
    assert candidate.reference_price == contexts[817].candle_15m.close == Decimal(95)
    assert candidate.invalidation_reference_price == candidate.rule_audit.invalidation_reference
    assert not hasattr(candidate, "quantity")
    assert not hasattr(candidate, "fill_price")


def test_range_break_replay_bar_is_no_trade_and_clears_old_context(tmp_path: Path) -> None:
    _, evaluations = _run(tmp_path / "breakout", breakout=True)

    assert evaluations[817].action is CandidateAction.LONG_CANDIDATE
    assert evaluations[818].action is CandidateAction.NO_TRADE
    assert evaluations[818].reasons_against == (RangeMeanReversionReasonCode.RANGE_BROKEN,)
    assert evaluations[818].rule_audit.range_status is RangeStatus.RANGE_BROKEN
    assert evaluations[819].action is CandidateAction.NO_TRADE
    assert evaluations[819].rule_audit.lower_boundary is None
    assert evaluations[819].rule_audit.upper_boundary is None
