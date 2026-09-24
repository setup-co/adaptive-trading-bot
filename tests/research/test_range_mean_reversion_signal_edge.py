"""Synthetic-only checks for the frozen Range Mean Reversion research path."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.backtest import ExecutionCostConfiguration, ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureSnapshot,
    PivotKind,
    StructureSnapshot,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntrySide
from adaptive_trading_bot.regime import (
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    LiquidityState,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.regime.composition import MultiTimeframeRegimeComposer
from adaptive_trading_bot.regime.technical import (
    TechnicalRegimeBatch,
    _direction,
    _direction_evidence,
    _regime_label,
)
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.range_mean_reversion_corpus import (
    RangeMeanReversionDecisionObservation,
    RangeMeanReversionHistoricalCorpusGenerator,
    RangeMeanReversionHistoricalResearchCorpus,
    RangeMeanReversionResearchConfiguration,
)
from adaptive_trading_bot.research.range_mean_reversion_replay import (
    RangeEntryInvalidationReason,
    RangeMeanReversionExitCause,
    RangeMeanReversionMidpointReplayRunner,
    RangeMeanReversionReplayResult,
    RangeMeanReversionReplayStatus,
    entry_geometry_reasons,
    midpoint_touches,
)
from adaptive_trading_bot.research.range_mean_reversion_signal_edge import (
    COSTS,
    FOLLOW_UP,
    FROZEN_RUNS,
    SOURCE_ID,
    RangeMeanReversionObservation,
    RangeMeanReversionScreenError,
    RangeMeanReversionScreenStatus,
    RangeMeanReversionSignalEdgeReport,
    RangeMeanReversionSignalEdgeRunner,
    RangeMeanReversionUnitStatus,
    _population,
    _unit_status,
    classify_screen,
    run_predeclared_unit,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.research.trend_pullback_signal_edge import (
    CandidateEconomicOutcome,
    ProfitFactorState,
    _economics,
    _is_evaluable,
    _losing_streak,
    _strata,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusInterval,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    RangeMeanReversionConfiguration,
    RangeMeanReversionRuleAudit,
    RangeMeanReversionStrategy,
    StrategyEvaluationContext,
)
from adaptive_trading_bot.strategies.range_context import ValidatedRangeConfiguration

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("synthetic-range")


def _technical(
    timeframe: Timeframe,
    observed_at: datetime,
    feature: FeatureSnapshot | None = None,
    structure: StructureSnapshot | None = None,
) -> TechnicalRegimeSnapshot:
    evidence = (
        _direction_evidence(feature, structure)
        if feature is not None and structure is not None
        else tuple(DirectionEvidence(item, 0, "test") for item in DirectionComponent)
    )
    score = sum(item.contribution for item in evidence)
    direction = _direction(score, True)
    return TechnicalRegimeSnapshot(
        SOURCE,
        SYMBOL,
        timeframe,
        observed_at,
        100,
        direction,
        score,
        abs(score),
        evidence,
        Decimal(50),
        VolatilityState.NORMAL,
        VolumeState.NORMAL,
        LiquidityState.UNKNOWN,
        EventRiskState.UNKNOWN,
        _regime_label(
            fully_warmed=True,
            percentile=Decimal(50),
            volatility=VolatilityState.NORMAL,
            direction=direction,
            strength=abs(score),
        ),
        True,
    )


def _context(
    index: int,
    *,
    side: str = "LONG",
    pivot: tuple[PivotKind, Decimal] | None = None,
    candle: Candle | None = None,
) -> StrategyEvaluationContext:
    observed_at = START + index * Timeframe.MINUTES_15.duration
    if candle is None:
        open_price, high, low, close = (
            (Decimal(85), Decimal(95), Decimal("82.5"), Decimal(90))
            if side == "LONG"
            else (Decimal(115), Decimal("117.5"), Decimal(105), Decimal(110))
        )
        candle = Candle(
            SYMBOL,
            Timeframe.MINUTES_15,
            observed_at - Timeframe.MINUTES_15.duration,
            observed_at,
            open_price,
            high,
            low,
            close,
            Decimal(100),
        )
    feature = FeatureSnapshot(
        SOURCE,
        SYMBOL,
        Timeframe.MINUTES_15,
        observed_at,
        candle.open_time,
        candle.close_time,
        100,
        Decimal(100),
        Decimal(100),
        Decimal(10),
        Decimal(45 if side == "LONG" else 55),
        Decimal(100),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal("0.01"),
        True,
    )
    confirmed: tuple[ConfirmedPivot, ...] = ()
    if pivot is not None:
        kind, price = pivot
        pivot_open = START - timedelta(hours=4) + index * Timeframe.MINUTES_15.duration
        confirmed = (
            ConfirmedPivot(
                SOURCE,
                SYMBOL,
                Timeframe.MINUTES_15,
                kind,
                pivot_open,
                pivot_open + Timeframe.MINUTES_15.duration,
                price,
                observed_at,
                2,
                2,
                index,
                None,
            ),
        )
    structure = StructureSnapshot(
        SOURCE,
        SYMBOL,
        Timeframe.MINUTES_15,
        observed_at,
        100,
        confirmed,
        next((x for x in confirmed if x.kind is PivotKind.HIGH), None),
        next((x for x in confirmed if x.kind is PivotKind.LOW), None),
        None,
        None,
    )
    entry = _technical(Timeframe.MINUTES_15, observed_at, feature, structure)
    one = _technical(Timeframe.HOUR_1, observed_at)
    four = _technical(Timeframe.HOURS_4, observed_at)
    regime = (
        MultiTimeframeRegimeComposer()
        .process(TechnicalRegimeBatch(observed_at, (entry, one, four)))
        .snapshots[0]
    )
    return StrategyEvaluationContext(
        SOURCE, SYMBOL, observed_at, candle, feature, structure, regime
    )


def _decision_prefix(
    side: str = "LONG",
) -> tuple[RangeMeanReversionHistoricalResearchCorpus, DecisionJournalEntry[object, object]]:
    strategy = RangeMeanReversionStrategy()
    pivots = (
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
    )
    entries: list[RangeMeanReversionDecisionObservation] = []
    contexts: list[HistoricalStrategyContextObservation] = []
    for index in range(1, 17):
        pivot = pivots[index - 1] if index <= 4 else None
        quiet_candle = None
        if index > 4:
            observed = START + index * Timeframe.MINUTES_15.duration
            quiet_candle = Candle(
                SYMBOL,
                Timeframe.MINUTES_15,
                observed - Timeframe.MINUTES_15.duration,
                observed,
                Decimal(100),
                Decimal(102),
                Decimal(98),
                Decimal(100),
                Decimal(100),
            )
        context = _context(index, side=side, pivot=pivot, candle=quiet_candle)
        evaluation = strategy.evaluate(context)
        equity = Decimal(10000)
        portfolio = RiskPortfolioSnapshot(context.observed_at, "USDT", equity, equity, equity, ())
        risk = RiskManager().evaluate(evaluation, portfolio)
        safety = OperationalSafetyGate().evaluate(
            risk, _research_health(context, ResearchSafetyAssumptionProfile())
        )
        decision = DecisionJournalEntry(context, evaluation, risk, safety)
        entries.append(RangeMeanReversionDecisionObservation(decision))
        contexts.append(HistoricalStrategyContextObservation(context))
    assert entries[3].decision.strategy_evaluation.action is (
        CandidateAction.LONG_CANDIDATE if side == "LONG" else CandidateAction.SHORT_CANDIDATE
    )
    config = RangeMeanReversionResearchConfiguration(
        (SOURCE,), (SYMBOL,), START, START + timedelta(hours=4), Decimal(10000)
    )
    # The four observations form only the prefix of the declared research window.
    corpus = RangeMeanReversionHistoricalResearchCorpus(config, tuple(entries), tuple(contexts))
    return corpus, entries[3].decision


class _SyntheticSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        return tuple(c for c in self.candles if start <= c.open_time < end)


def _replay(
    side: str, entry_open: Decimal, trigger: str = "none", *, end_after_bars: int = 5
) -> tuple[RangeMeanReversionReplayResult, DecisionJournalEntry[object, object]]:
    corpus, decision = _decision_prefix(side)
    del corpus
    contexts: list[HistoricalStrategyContextObservation] = []
    candles: list[Candle] = []
    for index in range(5, 5 + end_after_bars):
        observed = START + index * Timeframe.MINUTES_15.duration
        opening = entry_open if index == 6 else (Decimal(90) if side == "LONG" else Decimal(110))
        high = opening + Decimal(1)
        low = opening - Decimal(1)
        if index == 7 and trigger in {"stop", "both"}:
            low = Decimal(78) if side == "LONG" else low
            high = Decimal(122) if side == "SHORT" else high
        if index == 7 and trigger in {"target", "both"}:
            high = Decimal(101) if side == "LONG" else high
            low = Decimal(99) if side == "SHORT" else low
        candle = Candle(
            SYMBOL,
            Timeframe.MINUTES_15,
            observed - Timeframe.MINUTES_15.duration,
            observed,
            opening,
            high,
            low,
            opening,
            Decimal(100),
        )
        candles.append(candle)
        contexts.append(
            HistoricalStrategyContextObservation(_context(index, side=side, candle=candle))
        )
    result = RangeMeanReversionMidpointReplayRunner(_SyntheticSource(tuple(candles))).run(
        decision,
        tuple(contexts),
        ExecutionSeries(SOURCE, SYMBOL, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal("0"), Decimal("0"), Decimal("0")),
        end=START + (5 + end_after_bars) * Timeframe.MINUTES_15.duration,
    )
    return result, decision


def test_defaults_and_stateful_sealed_corpus() -> None:
    assert RangeMeanReversionConfiguration() == RangeMeanReversionConfiguration(
        Timeframe.MINUTES_15,
        Decimal("0.25"),
        Decimal("0.20"),
        Decimal(45),
        Decimal(55),
        Decimal("3.0"),
    )
    assert ValidatedRangeConfiguration() == ValidatedRangeConfiguration(
        Timeframe.MINUTES_15,
        12,
        Decimal("0.40"),
        Decimal("2.0"),
        Decimal("8.0"),
        Decimal("0.15"),
        16,
        40,
    )
    corpus, decision = _decision_prefix()
    corpus.validate()
    assert all(
        item.decision.strategy_evaluation.action is CandidateAction.NO_TRADE
        for item in corpus.decisions[:3]
    )
    assert len(corpus.research_admissible_entry_identities) == 1
    audit = decision.strategy_evaluation.rule_audit
    assert isinstance(audit, RangeMeanReversionRuleAudit)
    assert audit.midpoint == Decimal(100)
    assert audit.lower_boundary == Decimal(80)
    assert audit.upper_boundary == Decimal(120)
    assert audit.invalidation_reference == Decimal("78.5")
    assert _decision_prefix()[0] == corpus
    object.__setattr__(corpus.historical_contexts[0].context.candle_15m, "high", Decimal(999))
    with pytest.raises(ValueError, match="integrity"):
        corpus.validate()


def test_synthetic_replay_generation_is_complete_deterministic_and_isolated(
    tmp_path: Path,
) -> None:
    store = ParquetHistoricalCandleStore(tmp_path)
    candles = tuple(
        Candle(
            SYMBOL,
            frame,
            START + index * frame.duration,
            START + (index + 1) * frame.duration,
            Decimal(100),
            Decimal(101),
            Decimal(99),
            Decimal(100),
            Decimal(100),
        )
        for frame, count in (
            (Timeframe.MINUTES_15, 32),
            (Timeframe.HOUR_1, 8),
            (Timeframe.HOURS_4, 2),
        )
        for index in range(count)
    )
    store.merge(SOURCE, candles)
    config = RangeMeanReversionResearchConfiguration(
        (SOURCE,), (SYMBOL,), START, START + timedelta(hours=4), Decimal(10000)
    )
    first = RangeMeanReversionHistoricalCorpusGenerator(store).generate(config)
    second = RangeMeanReversionHistoricalCorpusGenerator(store).generate(config)
    first.validate()
    assert first == second
    assert len(first.decisions) == 16
    assert len(first.historical_contexts) == 16
    assert first.decisions[0].decision.strategy_evaluation.action is CandidateAction.NO_TRADE
    assert first.research_admissible_entry_identities == ()
    report = RangeMeanReversionSignalEdgeRunner(store).run(
        first,
        timedelta(hours=4),
        ExecutionCostConfiguration(Decimal("2"), Decimal("2"), Decimal("10")),
    )
    report.validate()
    assert report.population.raw_candidate_count == 0
    assert report.unit_status is RangeMeanReversionUnitStatus.INSUFFICIENT
    with pytest.raises(ValueError, match="population"):
        RangeMeanReversionHistoricalResearchCorpus(
            config, first.decisions[:-1], first.historical_contexts[:-1]
        )


def test_warmup_suppresses_replay_admission_but_keeps_all_decisions() -> None:
    corpus, decision = _decision_prefix()
    config = replace(
        corpus.configuration,
        warmup_exclusions=((SYMBOL, CorpusInterval(START, START + timedelta(hours=1))),),
    )
    excluded = RangeMeanReversionHistoricalResearchCorpus(
        config, corpus.decisions, corpus.historical_contexts
    )
    excluded.validate()
    assert len(excluded.decisions) == len(corpus.decisions)
    assert excluded.research_admissible_entry_identities == ()
    assert excluded.warmup_excluded_entry_identities == (decision.identity,)


@pytest.mark.parametrize(
    ("side", "fill", "expected"),
    [
        (
            "LONG",
            Decimal("78.5"),
            (RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
        (
            "LONG",
            Decimal(100),
            (RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
        (
            "LONG",
            Decimal(101),
            (RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
        (
            "SHORT",
            Decimal(100),
            (RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
        (
            "SHORT",
            Decimal(99),
            (RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
        (
            "SHORT",
            Decimal("121.5"),
            (RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL,),
        ),
    ],
)
def test_actual_fill_invalidation(
    side: str, fill: Decimal, expected: tuple[RangeEntryInvalidationReason, ...]
) -> None:
    stop = Decimal("78.5") if side == "LONG" else Decimal("121.5")
    assert (
        entry_geometry_reasons(
            EntrySide.BUY if side == "LONG" else EntrySide.SELL,
            fill,
            stop,
            Decimal(100),
        )
        == expected
    )


@pytest.mark.parametrize(
    ("side", "fill", "reason"),
    [
        ("LONG", Decimal("78.5"), RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL),
        ("LONG", Decimal(100), RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL),
        ("LONG", Decimal(101), RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL),
        ("SHORT", Decimal(100), RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL),
        ("SHORT", Decimal(99), RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL),
    ],
)
def test_simulated_fill_invalidates_before_accounting(
    side: str, fill: Decimal, reason: RangeEntryInvalidationReason
) -> None:
    result, _ = _replay(side, fill)
    assert result.status is RangeMeanReversionReplayStatus.ENTRY_FILL_INVALIDATED
    assert result.entry_fill is not None
    assert result.entry_fill_invalidation is not None
    assert reason in result.entry_fill_invalidation.reasons
    assert result.entry_fill_invalidation.actual_fill == result.entry_fill
    assert result.entry_fill_invalidation.sealed_midpoint == Decimal(100)
    assert result.final_position is None
    assert result.accounting_events == ()
    assert result.realized_net_pnl is None


def test_both_post_fill_geometry_reasons_are_canonical() -> None:
    assert entry_geometry_reasons(EntrySide.BUY, Decimal(105), Decimal(110), Decimal(100)) == (
        RangeEntryInvalidationReason.STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL,
        RangeEntryInvalidationReason.MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL,
    )


def test_midpoint_and_stop_triggers_and_causal_execution() -> None:
    valid_long, _ = _replay("LONG", Decimal(85), "target")
    assert valid_long.status is RangeMeanReversionReplayStatus.REPLAYED
    assert valid_long.exit_cause is RangeMeanReversionExitCause.RANGE_MIDPOINT_TARGET
    assert valid_long.exit_fill is not None
    assert valid_long.exit_trigger_at is not None
    assert valid_long.exit_fill.execution_candle_open_time >= valid_long.exit_trigger_at
    assert valid_long.target_distance == Decimal(15)
    assert valid_long.stop_distance == Decimal("6.5")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        assert valid_long.geometric_reward_risk == Decimal(15) / Decimal("6.5")
    assert valid_long.final_position is not None
    assert valid_long.realized_net_pnl == valid_long.final_position.cumulative_net_realized_pnl
    for side in ("LONG", "SHORT"):
        for trigger, cause in (
            ("stop", RangeMeanReversionExitCause.PROTECTIVE_STOP),
            ("both", RangeMeanReversionExitCause.PROTECTIVE_STOP),
            ("target", RangeMeanReversionExitCause.RANGE_MIDPOINT_TARGET),
        ):
            result, _ = _replay(side, Decimal(85 if side == "LONG" else 110), trigger)
            assert result.exit_cause is cause
            assert result.same_bar_stop_precedence is (trigger == "both")
    candle = _context(1).candle_15m
    assert midpoint_touches(
        EntrySide.BUY,
        replace(candle, high=Decimal(101), low=Decimal(78)),
        Decimal("78.5"),
        Decimal(100),
    ) == (True, True)
    short_candle = _context(1, side="SHORT").candle_15m
    assert midpoint_touches(
        EntrySide.SELL,
        replace(short_candle, high=Decimal(122), low=Decimal(99)),
        Decimal("121.5"),
        Decimal(100),
    ) == (True, True)


def test_unfilled_and_unresolved_do_not_create_pnl() -> None:
    entry_unfilled, _ = _replay("LONG", Decimal(85), end_after_bars=1)
    assert entry_unfilled.status is RangeMeanReversionReplayStatus.ENTRY_UNFILLED
    assert entry_unfilled.entry_fill is None
    assert entry_unfilled.final_position is None
    unresolved, _ = _replay("LONG", Decimal(85), "none")
    assert unresolved.status is RangeMeanReversionReplayStatus.REPLAYED
    assert not unresolved.position_closed and unresolved.realized_net_pnl is None
    assert unresolved.exit_cause is None
    unfilled, _ = _replay("LONG", Decimal(85), "target", end_after_bars=3)
    assert unfilled.exit_unfilled is not None
    assert not unfilled.position_closed and unfilled.realized_net_pnl is None


def test_economic_helpers_and_frozen_screen() -> None:
    from adaptive_trading_bot.journal import DecisionIdentity

    values = (Decimal(2), Decimal(-1), Decimal(-1), Decimal(0))
    outcomes = tuple(
        CandidateEconomicOutcome(
            DecisionIdentity(
                SOURCE, SYMBOL, START + timedelta(minutes=15 * i), "range_mean_reversion", "1.0.0"
            ),
            "LONG" if i % 2 == 0 else "SHORT",
            "2026-09",
            START + timedelta(hours=i + 1),
            value * Decimal(5),
            Decimal(5),
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
    assert _unit_status(economics, True) is RangeMeanReversionUnitStatus.NEGATIVE
    assert _unit_status(_economics((outcomes[0],)), True) is RangeMeanReversionUnitStatus.POSITIVE
    mixed = tuple(
        replace(item, realized_net_pnl=value * Decimal(5))
        for item, value in zip(outcomes[:3], (Decimal(-1), Decimal(-1), Decimal(5)), strict=True)
    )
    assert _unit_status(_economics(mixed), True) is RangeMeanReversionUnitStatus.MIXED
    assert _unit_status(economics, False) is RangeMeanReversionUnitStatus.INSUFFICIENT
    assert _economics((outcomes[0],)).profit_factor_state is ProfitFactorState.INFINITE
    assert _economics((outcomes[3],)).profit_factor_state is ProfitFactorState.UNAVAILABLE
    assert not _is_evaluable(9, Decimal("0.80"))
    assert not _is_evaluable(10, Decimal("0.79"))
    assert _is_evaluable(10, Decimal("0.80"))
    for statuses, expected in (
        (("POSITIVE", "POSITIVE", "MIXED"), RangeMeanReversionScreenStatus.POSITIVE),
        (("NEGATIVE", "NEGATIVE", "POSITIVE"), RangeMeanReversionScreenStatus.NEGATIVE),
        (("POSITIVE", "NEGATIVE", "MIXED"), RangeMeanReversionScreenStatus.MIXED),
        (("POSITIVE", "POSITIVE", "INSUFFICIENT"), RangeMeanReversionScreenStatus.INSUFFICIENT),
    ):
        assert classify_screen(tuple(RangeMeanReversionUnitStatus(x) for x in statuses)) is expected


def test_report_population_geometry_and_locked_gate(tmp_path: Path) -> None:
    corpus, decision = _decision_prefix()
    result, _ = _replay("LONG", Decimal(85), "target", end_after_bars=15)
    observation = RangeMeanReversionObservation(
        RangeMeanReversionDecisionObservation(decision),
        result.end,
        result,
    )
    report = RangeMeanReversionSignalEdgeReport(
        corpus.corpus_id,
        corpus.corpus_digest,
        result.end - decision.identity.observed_at,
        result.execution_costs,
        (observation,),
    )
    report.validate()
    assert report.population.raw_candidate_count == 1
    assert report.population.closed_count == report.economics.outcome_denominator
    assert report.geometry.replayed_count == 1
    assert report.geometry.target_distances_quote == (Decimal(15),)
    assert report.overlap_audit.replayed_count == 1
    assert report.to_json() == report.to_json()
    object.__setattr__(report, "unit_status", RangeMeanReversionUnitStatus.POSITIVE)
    with pytest.raises(RangeMeanReversionScreenError, match="integrity"):
        report.validate()
    store = ParquetHistoricalCandleStore(tmp_path)
    with pytest.raises(RangeMeanReversionScreenError, match="locked WEEX"):
        run_predeclared_unit(cast(LockedWeexResearchCorpusStore, store), FROZEN_RUNS[0])
    with pytest.raises(ValueError, match="locked store"):
        RangeMeanReversionHistoricalCorpusGenerator(store).generate(
            RangeMeanReversionResearchConfiguration(
                (SOURCE_ID,), (SYMBOL,), START, START + timedelta(hours=4), Decimal(10000)
            )
        )
    assert SOURCE_ID.value.startswith("weex-v3-last-")
    assert timedelta(hours=168) == FOLLOW_UP
    assert COSTS.fee_bps == Decimal(10)
    assert tuple((item.run_id, str(item.symbol)) for item in FROZEN_RUNS) == (
        ("A", "BTC/USDT"),
        ("B", "ETH/USDT"),
        ("C", "ETH/USDT"),
    )
    assert FROZEN_RUNS[1].follow_up_boundary == datetime(2025, 1, 1, tzinfo=UTC)
    assert FROZEN_RUNS[2].start == datetime(2025, 4, 2, tzinfo=UTC)
    assert tuple((item.start.isoformat(), item.end.isoformat()) for item in FROZEN_RUNS) == (
        ("2024-07-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        ("2024-07-01T00:00:00+00:00", "2024-12-25T00:00:00+00:00"),
        ("2025-04-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )


def test_population_counts_invalidations_without_outcomes() -> None:
    invalid, decision = _replay("LONG", Decimal(100))
    unfilled, _ = _replay("LONG", Decimal(85), end_after_bars=1)
    closed, _ = _replay("LONG", Decimal(85), "target")
    authority = RangeMeanReversionDecisionObservation(decision)
    # The pure population projector is tested independently of a report's
    # canonical-identity uniqueness gate.
    items = tuple(
        RangeMeanReversionObservation(authority, result.end, result)
        for result in (invalid, unfilled, closed)
    )
    population = _population(items)
    assert population.raw_candidate_count == 3
    assert population.entry_fill_invalidated_count == 1
    assert population.midpoint_geometry_invalidated_count == 1
    assert population.stop_geometry_invalidated_count == 0
    assert population.entry_unfilled_count == 1
    assert population.replayed_count == population.closed_count == 1
    assert population.economic_outcome_denominator == 1
