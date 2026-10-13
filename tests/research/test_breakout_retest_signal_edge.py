"""Synthetic-only checks for the frozen Breakout + Retest research path."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.orders import EntrySide
from adaptive_trading_bot.research.breakout_retest_corpus import (
    BreakoutRetestHistoricalCorpusGenerator,
    BreakoutRetestResearchConfiguration,
)
from adaptive_trading_bot.research.breakout_retest_replay import (
    BreakoutRetestExitCause,
    BreakoutRetestFixed2RReplayRunner,
    BreakoutRetestReplayResult,
    BreakoutRetestReplayStatus,
    fixed_2r_geometry,
    fixed_2r_touches,
)
from adaptive_trading_bot.research.breakout_retest_signal_edge import (
    FROZEN_RUNS,
    SOURCE_ID,
    BreakoutRetestScreenError,
    BreakoutRetestScreenStatus,
    BreakoutRetestSignalEdgeRunner,
    BreakoutRetestUnitStatus,
    _unit_status,
    classify_screen,
    run_predeclared_unit,
)
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.trend_pullback_signal_edge import (
    CandidateEconomicOutcome,
    ProfitFactorState,
    _economics,
    _is_evaluable,
    _losing_streak,
    _strata,
)
from adaptive_trading_bot.research.weex_historical_corpus import LockedWeexResearchCorpusStore
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import BreakoutRetestConfiguration

START = datetime(2026, 9, 1, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("synthetic-breakout")


def _candle(index: int, timeframe: Timeframe) -> Candle:
    open_at = START + index * timeframe.duration
    close = Decimal(100 + index + index % 4)
    open_price = close - 1
    high = close + 2
    low = close - 2
    volume = Decimal(10 + index % 5)
    if timeframe is Timeframe.MINUTES_15:
        if index in {794, 795, 797, 798}:
            high = close + Decimal("0.5")
        elif index == 796:
            high = Decimal(901)
        elif index == 799:
            close, open_price, high, low = map(Decimal, (902, 901, 903, 900))
        elif index == 800:
            close, open_price, high, low, volume = map(Decimal, (902, 901, 903, 901, 14))
    return Candle(
        SYMBOL,
        timeframe,
        open_at,
        open_at + timeframe.duration,
        open_price,
        high,
        low,
        close,
        volume,
    )


def _store(tmp_path: Path, *, continuation: int = 0) -> ParquetHistoricalCandleStore:
    store = ParquetHistoricalCandleStore(tmp_path)
    store.merge(
        SOURCE,
        tuple(
            _candle(i, timeframe)
            for timeframe, count in (
                (Timeframe.MINUTES_15, 816 + continuation),
                (Timeframe.HOUR_1, 204 + continuation // 4),
                (Timeframe.HOURS_4, 51 + continuation // 16),
            )
            for i in range(count)
        ),
    )
    return store


def test_stateful_corpus_preserves_complete_population_and_default_authority(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    end = START + timedelta(hours=204)
    config = BreakoutRetestResearchConfiguration((SOURCE,), (SYMBOL,), START, end, Decimal("10000"))
    assert config.strategy_configuration == BreakoutRetestConfiguration()
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(config)
    corpus.validate()
    assert len(corpus.decisions) == 816
    assert any(item.is_research_admissible_entry for item in corpus.decisions)
    assert corpus.decisions[800].decision.strategy_evaluation.reference_price == Decimal(902)
    assert corpus.decisions[
        800
    ].decision.strategy_evaluation.invalidation_reference_price == Decimal(901)
    assert corpus == BreakoutRetestHistoricalCorpusGenerator(store).generate(config)
    object.__setattr__(corpus.historical_contexts[0].context.candle_15m, "high", Decimal("999"))
    with pytest.raises(ValueError, match="integrity"):
        corpus.validate()


def test_replay_actual_fill_geometry_and_unresolved_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    end = START + timedelta(hours=204)
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(
        BreakoutRetestResearchConfiguration((SOURCE,), (SYMBOL,), START, end, Decimal("10000"))
    )
    decision = next(item.decision for item in corpus.decisions if item.is_research_admissible_entry)
    contexts = tuple(
        HistoricalStrategyContextObservation(item.context)
        for item in corpus.historical_contexts
        if item.context.observed_at > decision.identity.observed_at
    )
    result = BreakoutRetestFixed2RReplayRunner(HistoricalExecutionSource(store)).run(
        decision,
        contexts,
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(
            spread_bps=Decimal("2"), slippage_bps=Decimal("2"), fee_bps=Decimal("10")
        ),
        end=end,
    )
    result.validate()
    assert result.status is BreakoutRetestReplayStatus.REPLAYED
    assert result.entry_fill is not None
    assert result.risk_distance == result.entry_fill.fill_price - Decimal(901)
    assert result.target_trigger_price == result.entry_fill.fill_price + 2 * result.risk_distance
    assert (
        result.realized_net_pnl is None
        if not result.position_closed
        else result.realized_net_pnl is not None
    )


class _SyntheticSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        return tuple(candle for candle in self.candles if start <= candle.open_time < end)


def test_fixed_2r_trigger_and_entry_boundary_cases(tmp_path: Path) -> None:
    store = _store(tmp_path)
    end = START + timedelta(hours=204)
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(
        BreakoutRetestResearchConfiguration((SOURCE,), (SYMBOL,), START, end, Decimal("10000"))
    )
    decision = next(item.decision for item in corpus.decisions if item.is_research_admissible_entry)
    original = store.read(SOURCE, SYMBOL, Timeframe.MINUTES_15, start=START, end=end)
    contexts = tuple(
        item
        for item in corpus.historical_contexts
        if item.context.observed_at > decision.identity.observed_at
    )
    series = ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15)
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal("2"), slippage_bps=Decimal("2"), fee_bps=Decimal("10")
    )

    def scenario(kind: str) -> BreakoutRetestReplayResult:
        changed: dict[datetime, Candle] = {}
        for index, candle in enumerate(original):
            if index == 802 and kind == "entry-invalid":
                changed[candle.close_time] = replace(
                    candle, open=Decimal(900), high=Decimal(904), low=Decimal(899)
                )
            elif index == 802 and kind == "gap-stop":
                changed[candle.close_time] = replace(candle, low=Decimal(900))
            elif index == 803 and kind == "gap-stop":
                changed[candle.close_time] = replace(candle, open=Decimal(850), low=Decimal(840))
            elif index >= 802 and kind in {"none", "unfilled"}:
                changed[candle.close_time] = replace(
                    candle,
                    open=Decimal(904),
                    high=Decimal(905),
                    low=Decimal(902),
                    close=Decimal(904),
                )
            elif index == 802 and kind in {"stop", "target", "both", "unfilled"}:
                changed[candle.close_time] = replace(
                    candle,
                    high=Decimal(909) if kind in {"target", "both"} else candle.high,
                    low=Decimal(900) if kind in {"stop", "both"} else candle.low,
                )
        if kind == "unfilled":
            first = original[802]
            changed[first.close_time] = replace(first, high=Decimal(909), low=Decimal(902))
        candles = tuple(changed.get(candle.close_time, candle) for candle in original)
        if kind == "unfilled":
            candles = candles[:803]
        sealed = tuple(
            HistoricalStrategyContextObservation(
                replace(
                    item.context,
                    candle_15m=changed.get(
                        item.context.candle_15m.close_time, item.context.candle_15m
                    ),
                )
            )
            for item in contexts
            if item.context.observed_at < end
        )
        return BreakoutRetestFixed2RReplayRunner(_SyntheticSource(candles)).run(
            decision, sealed, series, costs, end=end
        )

    invalid = scenario("entry-invalid")
    assert invalid.status is BreakoutRetestReplayStatus.ENTRY_FILL_INVALIDATED
    assert invalid.final_position is None
    for kind, expected in (
        ("stop", BreakoutRetestExitCause.PROTECTIVE_STOP),
        ("target", BreakoutRetestExitCause.FIXED_2R_TARGET),
        ("both", BreakoutRetestExitCause.PROTECTIVE_STOP),
    ):
        result = scenario(kind)
        assert result.status is BreakoutRetestReplayStatus.REPLAYED
        assert result.exit_cause is expected
        assert result.exit_trigger_at == original[802].close_time
        assert result.exit_fill is not None
        assert result.exit_fill.execution_candle_open_time >= original[802].close_time
        assert result.position_closed, (
            kind,
            result.entry_fill.quantity if result.entry_fill else None,
            result.exit_request.quantity if result.exit_request else None,
            result.exit_fill.quantity,
            result.final_position.quantity if result.final_position else None,
        )
        assert result.final_position is not None
        assert result.realized_net_pnl == result.final_position.cumulative_net_realized_pnl
        assert result.same_bar_stop_precedence is (kind == "both")
    unresolved = scenario("none")
    assert unresolved.status is BreakoutRetestReplayStatus.REPLAYED
    assert unresolved.exit_cause is None and unresolved.exit_fill is None
    assert not unresolved.position_closed and unresolved.realized_net_pnl is None
    exit_unfilled = scenario("unfilled")
    assert exit_unfilled.exit_cause is BreakoutRetestExitCause.FIXED_2R_TARGET
    assert exit_unfilled.exit_unfilled is not None
    assert not exit_unfilled.position_closed
    gap_stop = scenario("gap-stop")
    assert gap_stop.exit_cause is BreakoutRetestExitCause.PROTECTIVE_STOP
    assert gap_stop.exit_trigger_at == original[802].close_time
    assert gap_stop.exit_fill is not None
    assert gap_stop.stop_basis is not None
    assert gap_stop.exit_fill.reference_open_price == Decimal(850)
    assert gap_stop.exit_fill.fill_price < gap_stop.stop_basis
    assert gap_stop.realized_net_pnl is not None and gap_stop.realized_net_pnl < 0
    with pytest.raises(ValueError, match="omit"):
        BreakoutRetestFixed2RReplayRunner(_SyntheticSource(original)).run(
            decision, (), series, costs, end=end
        )


def test_sealed_report_population_and_locked_run_gate(tmp_path: Path) -> None:
    store = _store(tmp_path, continuation=16)
    end = START + timedelta(hours=204)
    corpus = BreakoutRetestHistoricalCorpusGenerator(store).generate(
        BreakoutRetestResearchConfiguration((SOURCE,), (SYMBOL,), START, end, Decimal("10000"))
    )
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal("2"), slippage_bps=Decimal("2"), fee_bps=Decimal("10")
    )
    report = BreakoutRetestSignalEdgeRunner(store).run(corpus, timedelta(hours=4), costs)
    report.validate()
    assert report.population.raw_candidate_count == len(corpus.research_admissible_entry_identities)
    assert report.population.raw_candidate_count == (
        report.population.entry_unfilled_count
        + report.population.entry_fill_invalidated_count
        + report.population.replayed_count
    )
    assert report.population.replayed_count == (
        report.population.closed_count + report.population.unresolved_count
    )
    assert report.population.economic_outcome_denominator == len(report.outcomes)
    assert report.economics.outcome_denominator == len(report.outcomes)
    assert report.overlap_audit.replayed_count == report.population.replayed_count
    assert report.to_json() == report.to_json()
    object.__setattr__(report, "unit_status", BreakoutRetestUnitStatus.POSITIVE)
    with pytest.raises(BreakoutRetestScreenError, match="integrity"):
        report.validate()
    with pytest.raises(BreakoutRetestScreenError, match="locked WEEX"):
        run_predeclared_unit(cast(LockedWeexResearchCorpusStore, store), FROZEN_RUNS[0])
    with pytest.raises(ValueError, match="locked store"):
        BreakoutRetestHistoricalCorpusGenerator(store).generate(
            BreakoutRetestResearchConfiguration(
                (SOURCE_ID,), (SYMBOL,), START, end, Decimal("10000")
            )
        )


def test_exact_long_and_short_2r_geometry() -> None:
    assert fixed_2r_geometry(EntrySide.BUY, Decimal("101.25"), Decimal("100")) == (
        Decimal("1.25"),
        Decimal("103.75"),
    )
    assert fixed_2r_geometry(EntrySide.SELL, Decimal("101.25"), Decimal("102")) == (
        Decimal("0.75"),
        Decimal("99.75"),
    )
    with pytest.raises(ValueError, match="geometry"):
        fixed_2r_geometry(EntrySide.BUY, Decimal("100"), Decimal("100"))
    candle = replace(_candle(0, Timeframe.MINUTES_15), high=Decimal("104"), low=Decimal("98"))
    assert fixed_2r_touches(EntrySide.BUY, candle, Decimal("99"), Decimal("103")) == (True, True)
    assert fixed_2r_touches(EntrySide.SELL, candle, Decimal("103"), Decimal("99")) == (True, True)


def test_economics_profit_factor_streak_and_strata_are_decimal() -> None:
    from adaptive_trading_bot.journal import DecisionIdentity
    from adaptive_trading_bot.strategies import BreakoutRetestStrategy

    strategy = BreakoutRetestStrategy()
    values = (Decimal("2"), Decimal("-1"), Decimal("-1"), Decimal("0"))
    outcomes = tuple(
        CandidateEconomicOutcome(
            DecisionIdentity(
                SOURCE,
                SYMBOL,
                START + timedelta(minutes=15 * i),
                strategy.strategy_id,
                strategy.strategy_version,
            ),
            "LONG" if i % 2 == 0 else "SHORT",
            "2026-09",
            START + timedelta(hours=i + 1),
            value * Decimal("5"),
            Decimal("5"),
        )
        for i, value in enumerate(values)
    )
    economics = _economics(outcomes)
    assert tuple(item.net_r for item in outcomes) == values
    assert economics.outcome_denominator == 4
    assert economics.total_realized_net_r == 0
    assert economics.profit_factor_state is ProfitFactorState.FINITE
    assert economics.profit_factor == 1
    assert _losing_streak(outcomes) == 2
    assert len(_strata(outcomes)) == 3
    assert _unit_status(economics, True) is BreakoutRetestUnitStatus.NEGATIVE
    assert _unit_status(economics, False) is BreakoutRetestUnitStatus.INSUFFICIENT
    assert _economics((outcomes[0],)).profit_factor_state is ProfitFactorState.INFINITE
    assert _economics((outcomes[3],)).profit_factor_state is ProfitFactorState.UNAVAILABLE
    assert not _is_evaluable(9, Decimal("0.80"))
    assert not _is_evaluable(10, Decimal("0.79"))
    assert _is_evaluable(10, Decimal("0.80"))


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        (("POSITIVE", "POSITIVE", "MIXED"), BreakoutRetestScreenStatus.POSITIVE),
        (("NEGATIVE", "NEGATIVE", "POSITIVE"), BreakoutRetestScreenStatus.NEGATIVE),
        (("POSITIVE", "NEGATIVE", "MIXED"), BreakoutRetestScreenStatus.MIXED),
        (("POSITIVE", "POSITIVE", "INSUFFICIENT"), BreakoutRetestScreenStatus.INSUFFICIENT),
    ],
)
def test_screen_is_frozen(
    units: tuple[str, str, str], expected: BreakoutRetestScreenStatus
) -> None:
    assert classify_screen(tuple(BreakoutRetestUnitStatus(unit) for unit in units)) is expected
