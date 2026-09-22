import ast
import inspect
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, getcontext, localcontext, setcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import adaptive_trading_bot.research.trend_pullback_corpus as corpus_module
import adaptive_trading_bot.research.trend_pullback_exit_robustness as robustness_module
from adaptive_trading_bot.backtest import ExecutionCostConfiguration
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.journal import (
    DecisionJournalEntry,
    DecisionJournalValidationError,
)
from adaptive_trading_bot.replay import ReplayIntegrityError, ReplaySeries, ReplaySpeed
from adaptive_trading_bot.research import (
    HistoricalStrategyContextObservation,
    HistoricalTrendPullbackDecisionObservation,
    PairedExitComparisonState,
    PairedExitReplayError,
    PairedExitReplayStatus,
    PairedExitRobustnessObservation,
    ResearchAdmissionConfiguration,
    ResearchDecisionCategory,
    ResearchSafetyAssumptionProfile,
    RobustnessStratificationDimension,
    TrendPullbackExitRobustnessConfiguration,
    TrendPullbackExitRobustnessError,
    TrendPullbackExitRobustnessReport,
    TrendPullbackExitRobustnessRunner,
    TrendPullbackHistoricalCorpusGenerator,
    TrendPullbackHistoricalResearchCorpus,
    TrendPullbackResearchCorpusError,
)
from adaptive_trading_bot.research.trend_pullback_signal_edge import (
    FROZEN_RUNS,
    SignalEdgeProjectionError,
    project_frozen_run,
    project_run,
)
from adaptive_trading_bot.research.weex_historical_corpus import LockedWeexResearchCorpusStore
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    RiskManagerConfiguration,
)
from adaptive_trading_bot.storage import (
    IntegrityIssue,
    IntegrityIssueKind,
    IntegrityReport,
    MergeResult,
    ParquetHistoricalCandleStore,
    SourceId,
)
from adaptive_trading_bot.strategies import CandidateAction

START = datetime(2026, 1, 1, tzinfo=UTC)
HOURS = 204
END = START + timedelta(hours=HOURS)
SOURCE = SourceId("research-archive")
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
COUNTS = {
    Timeframe.MINUTES_15: HOURS * 4,
    Timeframe.HOUR_1: HOURS,
    Timeframe.HOURS_4: HOURS // 4,
}
MIRROR_AXIS = Decimal(2000)


def _up_candle(symbol: TradingSymbol, index: int, timeframe: Timeframe) -> Candle:
    open_time = START + index * timeframe.duration
    close = Decimal(100 + index)
    low = close - Decimal(2)
    if timeframe is Timeframe.MINUTES_15 and index == 796:
        low = close - Decimal(6)
    if timeframe is Timeframe.MINUTES_15 and index == 799:
        low = close - Decimal(10)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=close - Decimal(1),
        high=close + Decimal(2),
        low=low,
        close=close,
        volume=Decimal(10 + index % 5),
    )


def _candle(symbol: TradingSymbol, index: int, timeframe: Timeframe) -> Candle:
    upward = _up_candle(symbol, index, timeframe)
    if symbol == BTC:
        return upward
    volatility_training_offset = Decimal(0)
    noisy_ranges = {
        Timeframe.MINUTES_15: (650, 750),
        Timeframe.HOUR_1: (100, 150),
        Timeframe.HOURS_4: (20, 35),
    }
    noise_start, noise_end = noisy_ranges[timeframe]
    if noise_start <= index < noise_end:
        volatility_training_offset = Decimal(8 if index % 2 else -8)
    return replace(
        upward,
        open=MIRROR_AXIS - upward.open + volatility_training_offset,
        high=MIRROR_AXIS - upward.low + volatility_training_offset,
        low=MIRROR_AXIS - upward.high + volatility_training_offset,
        close=MIRROR_AXIS - upward.close + volatility_training_offset,
    )


class MemoryHistoricalStore:
    def __init__(
        self,
        symbols: tuple[TradingSymbol, ...] = (BTC, ETH),
        *,
        hours: int = HOURS,
    ) -> None:
        counts = {
            Timeframe.MINUTES_15: hours * 4,
            Timeframe.HOUR_1: hours,
            Timeframe.HOURS_4: hours // 4,
        }
        self._candles = {
            (SOURCE, symbol, timeframe): tuple(
                _candle(symbol, index, timeframe) for index in range(count)
            )
            for symbol in symbols
            for timeframe, count in counts.items()
        }
        self.reads: list[ReplaySeries] = []

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        raise AssertionError("research generation must not mutate historical storage")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        assert start is not None and end is not None
        if align_timestamp(start, timeframe) == start and align_timestamp(end, timeframe) == end:
            self.reads.append(ReplaySeries(source_id, symbol, timeframe, start, end))
        return tuple(
            candle
            for candle in self._candles[(source_id, symbol, timeframe)]
            if start <= candle.open_time < end
        )

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        assert start is not None and end is not None
        rows = sum(
            start <= candle.open_time < end
            for candle in self._candles[(source_id, symbol, timeframe)]
        )
        return IntegrityReport(source_id, symbol, timeframe, start, end, rows, ())

    def exact_candle(
        self, symbol: TradingSymbol, timeframe: Timeframe, open_time: datetime
    ) -> Candle:
        return next(
            candle
            for candle in self._candles[(SOURCE, symbol, timeframe)]
            if candle.open_time == open_time
        )


def _configuration(
    *,
    symbols: tuple[TradingSymbol, ...] = (BTC, ETH),
    equity: Decimal = Decimal(10000),
    safety: ResearchSafetyAssumptionProfile | None = None,
) -> ResearchAdmissionConfiguration:
    return ResearchAdmissionConfiguration(
        source_ids=(SOURCE,),
        symbols=symbols,
        start=START,
        end=END,
        research_equity_quote=equity,
        safety_assumptions=safety or ResearchSafetyAssumptionProfile(),
    )


@pytest.fixture(scope="module")
def generated() -> tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus]:
    store = MemoryHistoricalStore()
    corpus = TrendPullbackHistoricalCorpusGenerator(store).generate(_configuration())
    return store, corpus


@pytest.fixture(scope="module")
def robustness_report(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport]:
    _, corpus = generated
    store = MemoryHistoricalStore(hours=HOURS + 8)
    configuration = TrendPullbackExitRobustnessConfiguration(
        source_corpus_id=corpus.corpus_id,
        source_corpus_digest=corpus.corpus_digest,
        execution_costs=ExecutionCostConfiguration(
            spread_bps=Decimal(1),
            slippage_bps=Decimal(2),
            fee_bps=Decimal(3),
        ),
        follow_up_duration=timedelta(hours=4),
    )
    return store, TrendPullbackExitRobustnessRunner(store).run(corpus, configuration)


def test_btc_eth_replay_produces_complete_causal_population(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    store, corpus = generated

    assert corpus.total_evaluation_count == COUNTS[Timeframe.MINUTES_15] * 2
    assert corpus.no_trade_count > 0
    assert corpus.long_candidate_count >= 1
    assert corpus.short_candidate_count >= 1
    assert corpus.research_admissible_entry_count == (
        corpus.long_candidate_count + corpus.short_candidate_count
    )
    assert corpus.not_ready_count > 0
    assert all(
        record.decision.context.multi_timeframe_regime.regime_1h is None
        or record.decision.context.multi_timeframe_regime.regime_1h.observed_at
        <= record.observed_at
        for record in corpus.decisions
    )
    assert all(
        record.decision.context.multi_timeframe_regime.regime_4h is None
        or record.decision.context.multi_timeframe_regime.regime_4h.observed_at
        <= record.observed_at
        for record in corpus.decisions
    )
    assert len(store.reads) == 6
    corpus.validate()


def test_warmup_is_no_trade_and_all_no_trade_decisions_remain_visible(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    first = corpus.decisions[0]

    assert first.is_context_ready is False
    assert first.action is CandidateAction.NO_TRADE
    assert first.category is ResearchDecisionCategory.NO_TRADE
    assert corpus.no_trade_count == sum(
        item.action is CandidateAction.NO_TRADE for item in corpus.decisions
    )


def test_candidates_use_fixed_equity_zero_exposure_and_declared_safety_assumptions(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    candidates = tuple(
        item for item in corpus.decisions if item.action is not CandidateAction.NO_TRADE
    )

    assert candidates
    for item in candidates:
        risk = item.decision.risk_decision
        safety = item.decision.safety_decision
        assert risk.account_equity_quote == Decimal(10000)
        assert risk.day_start_equity_quote == Decimal(10000)
        assert risk.peak_equity_quote == Decimal(10000)
        assert risk.current_open_risk_quote == 0
        assert risk.current_gross_exposure_quote == 0
        assert safety.market_data_observed_at == item.observed_at
    assert corpus.configuration.safety_assumptions.historical_operational_facts_observed is False
    assert all(item.is_research_admissible_entry for item in candidates)


def test_same_data_is_independent_of_speed_symbol_order_and_decimal_context(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, expected = generated
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        first = TrendPullbackHistoricalCorpusGenerator(MemoryHistoricalStore()).generate(
            _configuration(symbols=(ETH, BTC)), replay_speed=ReplaySpeed.X1
        )
        setcontext(Context(prec=50))
        second = TrendPullbackHistoricalCorpusGenerator(MemoryHistoricalStore()).generate(
            _configuration(), replay_speed=ReplaySpeed.X100
        )
    finally:
        setcontext(original)

    assert first == second == expected
    assert first.corpus_digest == second.corpus_digest == expected.corpus_digest
    assert first.to_json() == second.to_json() == expected.to_json()


def test_data_beyond_bounded_range_cannot_change_earlier_decisions(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, expected = generated
    store = MemoryHistoricalStore()
    future_open = END
    for symbol in (BTC, ETH):
        for timeframe in COUNTS:
            original = store._candles[(SOURCE, symbol, timeframe)]
            future = Candle(
                symbol,
                timeframe,
                future_open,
                future_open + timeframe.duration,
                Decimal(1),
                Decimal(2),
                Decimal(1),
                Decimal(2),
                Decimal(999999),
            )
            store._candles[(SOURCE, symbol, timeframe)] = (*original, future)

    actual = TrendPullbackHistoricalCorpusGenerator(store).generate(_configuration())
    assert actual == expected


def test_configuration_change_changes_digest_and_is_sealed() -> None:
    original = TrendPullbackHistoricalCorpusGenerator(MemoryHistoricalStore((BTC,))).generate(
        _configuration(symbols=(BTC,))
    )
    changed = TrendPullbackHistoricalCorpusGenerator(MemoryHistoricalStore((BTC,))).generate(
        _configuration(symbols=(BTC,), equity=Decimal(20000))
    )

    assert original.corpus_digest != changed.corpus_digest
    assert original.decisions != changed.decisions
    with pytest.raises(FrozenInstanceError):
        original.configuration.research_equity_quote = Decimal(1)  # type: ignore[misc]


def test_safety_blocked_candidate_is_preserved_not_admissible() -> None:
    assumptions = ResearchSafetyAssumptionProfile(
        exchange_api_health_state=ExchangeApiHealthState.DOWN
    )
    corpus = TrendPullbackHistoricalCorpusGenerator(MemoryHistoricalStore((BTC,))).generate(
        _configuration(symbols=(BTC,), safety=assumptions)
    )
    candidates = tuple(
        item for item in corpus.decisions if item.action is CandidateAction.LONG_CANDIDATE
    )

    assert candidates
    assert corpus.safety_blocked_count == len(candidates)
    assert corpus.research_admissible_entry_count == 0
    assert all(item.category is ResearchDecisionCategory.SAFETY_BLOCKED for item in candidates)


def test_internally_valid_forged_strategy_evaluation_is_rejected(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    accepted = next(
        item for item in corpus.decisions if item.action is CandidateAction.LONG_CANDIDATE
    )
    evaluation = accepted.decision.strategy_evaluation
    assert len(evaluation.reasons_for) > 1
    forged = replace(
        evaluation,
        reasons_for=tuple(reversed(evaluation.reasons_for)),
    )
    context = accepted.decision.context
    entry = DecisionJournalEntry(
        context,
        forged,
        accepted.decision.risk_decision,
        accepted.decision.safety_decision,
    )
    observation = HistoricalTrendPullbackDecisionObservation(entry)

    assert observation.action is CandidateAction.LONG_CANDIDATE
    with pytest.raises(TrendPullbackResearchCorpusError, match="Trend Pullback authority"):
        TrendPullbackHistoricalResearchCorpus(
            configuration=corpus.configuration,
            decisions=(observation,),
            historical_contexts=(HistoricalStrategyContextObservation(context),),
        )


@pytest.mark.parametrize(
    "changed_risk",
    (
        RiskManagerConfiguration(risk_per_trade_fraction=Decimal("0.0010")),
        RiskManagerConfiguration(max_simultaneous_risk_fraction=Decimal("0.0100")),
        RiskManagerConfiguration(max_gross_exposure_fraction=Decimal("0.5000")),
    ),
)
def test_decisions_are_bound_to_complete_research_risk_configuration(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
    changed_risk: RiskManagerConfiguration,
) -> None:
    _, corpus = generated
    changed_configuration = replace(
        corpus.configuration,
        risk_manager_configuration=changed_risk,
    )

    with pytest.raises(TrendPullbackResearchCorpusError, match="RiskManager authority"):
        TrendPullbackHistoricalResearchCorpus(
            configuration=changed_configuration,
            decisions=corpus.decisions,
            historical_contexts=corpus.historical_contexts,
        )


def test_post_decision_contexts_are_sealed_and_exactly_from_same_source_path(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    store, corpus = generated
    identity = corpus.research_admissible_entry_identities[0]
    contexts = corpus.contexts_after_decision(identity)

    assert contexts
    for observation in contexts:
        observation.validate()
        context = observation.context
        assert context.source_id == identity.source_id
        assert context.symbol == identity.symbol
        assert context.observed_at > identity.observed_at
        assert context.candle_15m == store.exact_candle(
            identity.symbol,
            Timeframe.MINUTES_15,
            context.candle_15m.open_time,
        )


def test_context_decision_configuration_and_count_mutations_fail_closed(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, original = generated

    context_mutation = deepcopy(original)
    object.__setattr__(
        context_mutation.historical_contexts[-1].context.features_15m,
        "ema_50",
        Decimal(123),
    )
    with pytest.raises(TrendPullbackResearchCorpusError, match="corpus integrity"):
        context_mutation.validate()
    detached_context = deepcopy(original.historical_contexts[-1])
    object.__setattr__(detached_context.context.features_15m, "ema_50", Decimal(123))
    with pytest.raises(PairedExitReplayError, match="integrity seal"):
        detached_context.validate()

    decision_mutation = deepcopy(original)
    object.__setattr__(
        decision_mutation.decisions[-1].decision.context.features_15m,
        "ema_50",
        Decimal(123),
    )
    with pytest.raises(TrendPullbackResearchCorpusError, match="corpus integrity"):
        decision_mutation.validate()
    detached_decision = deepcopy(original.decisions[-1].decision)
    object.__setattr__(detached_decision.context.features_15m, "ema_50", Decimal(123))
    with pytest.raises(DecisionJournalValidationError, match="integrity seal"):
        detached_decision.validate()

    configuration_mutation = deepcopy(original)
    object.__setattr__(configuration_mutation.configuration, "research_equity_quote", Decimal(1))
    with pytest.raises(TrendPullbackResearchCorpusError, match="corpus integrity"):
        configuration_mutation.validate()

    count_mutation = deepcopy(original)
    object.__setattr__(count_mutation, "no_trade_count", 0)
    with pytest.raises(TrendPullbackResearchCorpusError, match="corpus integrity"):
        count_mutation.validate()


def test_dirty_historical_range_fails_through_replay_integrity_authority() -> None:
    class DirtyStore(MemoryHistoricalStore):
        def scan_integrity(
            self,
            source_id: SourceId,
            symbol: TradingSymbol,
            timeframe: Timeframe,
            *,
            start: datetime | None = None,
            end: datetime | None = None,
        ) -> IntegrityReport:
            assert start is not None and end is not None
            return IntegrityReport(
                source_id,
                symbol,
                timeframe,
                start,
                end,
                0,
                (
                    IntegrityIssue(
                        IntegrityIssueKind.GAP,
                        "test gap",
                        "test partition",
                        start,
                    ),
                ),
            )

    with pytest.raises(ReplayIntegrityError):
        TrendPullbackHistoricalCorpusGenerator(DirtyStore((BTC,))).generate(
            _configuration(symbols=(BTC,))
        )


def test_observations_are_outcome_free_and_artifact_is_research_only_lossless(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    record = corpus.decisions[-1]
    forbidden = (
        "outcome",
        "exit_fill",
        "realized_pnl",
        "realized_r",
        "performance",
    )

    assert not any(hasattr(record, name) for name in forbidden)
    artifact = corpus.to_json()
    assert '"research_only":true' in artifact
    assert '"is_trading_authority":false' in artifact
    assert '"decimal"' in artifact
    assert corpus.corpus_digest in artifact


def test_cli_reads_parquet_and_writes_deterministic_research_artifact(tmp_path: Path) -> None:
    data_root = tmp_path / "history"
    output = tmp_path / "corpus.json"
    store = ParquetHistoricalCandleStore(data_root)
    store.merge(
        SOURCE,
        tuple(
            _candle(BTC, index, timeframe)
            for timeframe, count in COUNTS.items()
            for index in range(count)
        ),
    )

    assert (
        corpus_module.main(
            [
                "--data-root",
                str(data_root),
                "--source",
                SOURCE.value,
                "--symbol",
                "BTC/USDT",
                "--start",
                START.isoformat(),
                "--end",
                END.isoformat(),
                "--research-equity",
                "10000",
                "--speed",
                "10x",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    artifact = output.read_text(encoding="utf-8")
    assert '"artifact_kind":"trend_pullback_historical_research_corpus"' in artifact
    assert '"research_only":true' in artifact


def test_module_has_no_runtime_or_experience_dependency_and_no_duplicate_formulas() -> None:
    tree = ast.parse(inspect.getsource(corpus_module))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        forbidden in name
        for name in imports
        for forbidden in ("experience", "paper", "live", "orders", "execution")
    )
    source = inspect.getsource(corpus_module)
    assert "FeatureEngine()" in source
    assert "TechnicalRegimeEngine()" in source
    assert "TrendPullbackStrategy()" in source
    assert "RiskManager(" in source
    assert "OperationalSafetyGate(" in source
    assert "_advance_ema" not in source
    assert "_direction_evidence" not in source
    assert "_evaluate_long" not in source


def test_exit_robustness_replays_exact_admissible_population_with_full_horizon(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, corpus = generated
    store, report = robustness_report

    report.validate()
    assert tuple(item.identity for item in report.observations) == (
        corpus.research_admissible_entry_identities
    )
    assert all(
        item.replay_end == item.identity.observed_at + report.configuration.follow_up_duration
        for item in report.observations
    )
    boundary_identity = next(
        item for item in corpus.research_admissible_entry_identities if item.symbol == BTC
    )
    boundary_end = boundary_identity.observed_at
    boundary_store = MemoryHistoricalStore((BTC,), hours=HOURS + 8)
    boundary_corpus = TrendPullbackHistoricalCorpusGenerator(boundary_store).generate(
        replace(corpus.configuration, symbols=(BTC,), end=boundary_end)
    )
    assert boundary_corpus.research_admissible_entry_identities[-1].observed_at == boundary_end
    execution_key = (SOURCE, BTC, Timeframe.MINUTES_15)
    execution_candles = list(boundary_store._candles[execution_key])
    stop_index = next(
        index
        for index, candle in enumerate(execution_candles)
        if candle.open_time == boundary_end + timedelta(minutes=15)
    )
    stop_candle = execution_candles[stop_index]
    execution_candles[stop_index] = replace(stop_candle, low=Decimal(1))
    boundary_store._candles[execution_key] = tuple(execution_candles)
    boundary_configuration = TrendPullbackExitRobustnessConfiguration(
        boundary_corpus.corpus_id,
        boundary_corpus.corpus_digest,
        report.configuration.execution_costs,
        timedelta(hours=4),
    )
    boundary_report = TrendPullbackExitRobustnessRunner(boundary_store).run(
        boundary_corpus, boundary_configuration
    )
    late = next(
        item for item in boundary_report.observations if item.identity.observed_at == boundary_end
    )
    assert late.replay_end == boundary_end + timedelta(hours=4)
    assert boundary_report.population.both_closed_count == 1
    assert boundary_report.deltas.comparable_count == 1
    assert boundary_report.deltas.mean_net_pnl_delta == (
        late.paired_result.realized_net_pnl_delta_candidate_minus_baseline
    )
    economic = project_run(boundary_report)
    assert economic.population.economic_outcome_denominator == 1
    assert economic.outcomes[0].identity == late.identity
    assert late.paired_result.ema50_candidate is not None
    assert late.paired_result.ema50_candidate.realized_net_pnl is not None
    with localcontext(Context(prec=34)):
        expected_net_r = (
            late.paired_result.ema50_candidate.realized_net_pnl
            / late.paired_result.entry_intent.approved_risk_quote
        )
    assert economic.outcomes[0].net_r == expected_net_r
    assert economic.paired_candidate_minus_baseline_deltas == boundary_report.deltas
    assert not economic.economically_evaluable
    with pytest.raises(SignalEdgeProjectionError, match="locked WEEX corpus store"):
        project_frozen_run(
            cast(LockedWeexResearchCorpusStore, boundary_store),
            boundary_corpus,
            boundary_report,
            FROZEN_RUNS[0],
        )
    excluded = {item.identity for item in corpus.decisions if not item.is_research_admissible_entry}
    assert excluded.isdisjoint(item.identity for item in report.observations)
    assert any(read.end == corpus.configuration.end + timedelta(hours=4) for read in store.reads)


def test_exit_robustness_validates_source_corpus_before_reading_history(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    tampered = deepcopy(corpus)
    object.__setattr__(tampered, "no_trade_count", 0)
    store = MemoryHistoricalStore(hours=HOURS + 4)
    configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        ExecutionCostConfiguration(),
        timedelta(hours=4),
    )

    with pytest.raises(TrendPullbackResearchCorpusError, match="integrity"):
        TrendPullbackExitRobustnessRunner(store).run(tampered, configuration)
    assert store.reads == []


@pytest.mark.parametrize("duration", (timedelta(0), timedelta(hours=5)))
def test_exit_robustness_requires_positive_four_hour_aligned_horizon(
    duration: timedelta,
) -> None:
    with pytest.raises(TrendPullbackExitRobustnessError, match="follow_up_duration"):
        TrendPullbackExitRobustnessConfiguration(
            "corpus:id",
            "digest",
            ExecutionCostConfiguration(),
            duration,
        )


def test_exit_robustness_preserves_unresolved_and_zero_comparable_strata(
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, report = robustness_report

    economic = project_run(report)
    assert economic.to_json() == project_run(deepcopy(report)).to_json()
    assert economic.population.paired_population == report.population
    assert economic.population.economic_outcome_denominator == 0
    assert economic.outcomes == ()
    assert economic.candidate_economics.mean_net_r is None
    assert economic.paired_candidate_minus_baseline_deltas == report.deltas

    assert report.population.candidate_count == len(report.observations)
    assert report.population.entry_filled_count == len(report.observations)
    assert report.population.entry_fill_invalidated_count == 0
    assert report.population.candidate_count == (
        report.population.entry_unfilled_count
        + report.population.entry_fill_invalidated_count
        + report.population.replayed_count
    )
    assert report.population.both_open_count == len(report.observations)
    assert report.population.baseline_unresolved_count == len(report.observations)
    assert report.population.candidate_unresolved_count == len(report.observations)
    assert report.deltas.comparable_count == 0
    assert report.deltas.mean_net_pnl_delta is None
    assert report.deltas.median_exit_fill_time_delta_seconds is None
    assert {item.dimension for item in report.strata} == set(RobustnessStratificationDimension)
    month = tuple(
        item
        for item in report.strata
        if item.dimension is RobustnessStratificationDimension.UTC_ENTRY_MONTH
    )
    assert month
    assert all(item.deltas.comparable_count == 0 for item in month)
    assert report.chronological_stability.month_count == len(month)
    assert report.chronological_stability.median_delta_signs == tuple(
        (item.value, "UNAVAILABLE") for item in month
    )
    assert report.overlap_audit.raw_candidate_count_is_independent_sample_size is False
    assert report.overlap_audit.sensitivity_is_independence_proof is False
    artifact = report.to_json()
    assert report.configuration.configuration_schema_version == "2.0.0"
    assert report.configuration.report_schema_version == "2.0.0"
    assert report.configuration.stratification_policy_version == "2.0.0"
    assert report.report_schema_version == "2.0.0"
    assert all(item.observation_schema_version == "2.0.0" for item in report.observations)
    assert '"artifact_schema_version":"2.0.0"' in artifact
    assert '"artifact_kind":"trend_pullback_exit_robustness_report"' in artifact
    assert '"is_portfolio_performance":false' in artifact
    assert '"decimal"' in artifact


def test_signal_edge_projection_rejects_mutated_sealed_report(
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, report = robustness_report
    forged = deepcopy(report)
    object.__setattr__(forged, "report_digest", "0" * 64)

    with pytest.raises(TrendPullbackExitRobustnessError, match="integrity"):
        project_run(forged)


def test_entry_unfilled_and_future_after_fixed_end_are_preserved(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    store = MemoryHistoricalStore(hours=HOURS + 8)
    configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        ExecutionCostConfiguration(latency=timedelta(hours=4)),
        timedelta(hours=4),
    )
    first = TrendPullbackExitRobustnessRunner(store).run(corpus, configuration)

    assert first.population.entry_unfilled_count == len(first.observations)
    economic = project_run(first)
    assert economic.population.paired_population.entry_unfilled_count == len(first.observations)
    assert economic.population.entry_unfilled_rate.value == Decimal(1)
    assert economic.population.economic_outcome_denominator == 0
    assert economic.outcomes == ()
    assert all(
        item.paired_result.comparison_state.value == "ENTRY_UNFILLED" for item in first.observations
    )
    key = (SOURCE, BTC, Timeframe.MINUTES_15)
    candles = list(store._candles[key])
    future_index = next(
        index
        for index, candle in enumerate(candles)
        if candle.open_time >= END + timedelta(hours=4)
    )
    changed = candles[future_index]
    candles[future_index] = replace(
        changed,
        high=changed.high + Decimal(1),
        close=changed.close + Decimal("0.5"),
    )
    store._candles[key] = tuple(candles)
    second = TrendPullbackExitRobustnessRunner(store).run(corpus, configuration)

    assert first == second
    assert first.report_digest == second.report_digest


def test_incomplete_extended_history_fails_closed_before_outcome_projection(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    class ExtendedGapStore(MemoryHistoricalStore):
        def scan_integrity(
            self,
            source_id: SourceId,
            symbol: TradingSymbol,
            timeframe: Timeframe,
            *,
            start: datetime | None = None,
            end: datetime | None = None,
        ) -> IntegrityReport:
            assert start is not None and end is not None
            if symbol == BTC and timeframe is Timeframe.HOUR_1 and end > END:
                return IntegrityReport(
                    source_id,
                    symbol,
                    timeframe,
                    start,
                    end,
                    0,
                    (
                        IntegrityIssue(
                            IntegrityIssueKind.GAP,
                            "missing extended candle",
                            "test partition",
                            END,
                        ),
                    ),
                )
            return super().scan_integrity(
                source_id,
                symbol,
                timeframe,
                start=start,
                end=end,
            )

    _, corpus = generated
    store = ExtendedGapStore(hours=HOURS + 4)
    configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        ExecutionCostConfiguration(),
        timedelta(hours=4),
    )

    with pytest.raises(ReplayIntegrityError):
        TrendPullbackExitRobustnessRunner(store).run(corpus, configuration)


def test_extended_context_prefix_mismatch_fails_closed(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
) -> None:
    _, corpus = generated
    store = MemoryHistoricalStore(hours=HOURS + 4)
    key = (SOURCE, BTC, Timeframe.MINUTES_15)
    candles = list(store._candles[key])
    changed = candles[500]
    candles[500] = replace(
        changed,
        high=changed.high + Decimal(1),
        close=changed.close + Decimal("0.5"),
    )
    store._candles[key] = tuple(candles)
    configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        ExecutionCostConfiguration(),
        timedelta(hours=4),
    )

    with pytest.raises(TrendPullbackExitRobustnessError, match="prefix differs"):
        TrendPullbackExitRobustnessRunner(store).run(corpus, configuration)


def test_report_provenance_decimal_determinism_and_nested_tamper(
    generated: tuple[MemoryHistoricalStore, TrendPullbackHistoricalResearchCorpus],
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, corpus = generated
    _, report = robustness_report
    original_context = getcontext().copy()
    try:
        setcontext(Context(prec=7, rounding=ROUND_DOWN))
        rebuilt = TrendPullbackExitRobustnessRunner(MemoryHistoricalStore(hours=HOURS + 8)).run(
            corpus, report.configuration
        )
    finally:
        setcontext(original_context)

    assert rebuilt == report
    assert rebuilt.to_json() == report.to_json()
    changed_costs = replace(report.configuration.execution_costs, fee_bps=Decimal(4))
    cost_configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        changed_costs,
        timedelta(hours=4),
    )
    duration_configuration = TrendPullbackExitRobustnessConfiguration(
        corpus.corpus_id,
        corpus.corpus_digest,
        report.configuration.execution_costs,
        timedelta(hours=8),
    )
    assert cost_configuration.configuration_digest != report.configuration.configuration_digest
    assert duration_configuration.configuration_digest != (
        report.configuration.configuration_digest
    )
    changed_cost_report = TrendPullbackExitRobustnessRunner(
        MemoryHistoricalStore(hours=HOURS + 8)
    ).run(corpus, cost_configuration)
    changed_duration_report = TrendPullbackExitRobustnessRunner(
        MemoryHistoricalStore(hours=HOURS + 8)
    ).run(corpus, duration_configuration)
    assert changed_cost_report.report_digest != report.report_digest
    assert changed_duration_report.report_digest != report.report_digest
    tampered = deepcopy(report)
    object.__setattr__(
        tampered.observations[0].paired_result,
        "realized_net_pnl_delta_candidate_minus_baseline",
        Decimal(999),
    )
    with pytest.raises(TrendPullbackExitRobustnessError, match="integrity"):
        tampered.validate()


def test_overlap_components_are_input_order_invariant(
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, report = robustness_report
    first = report.observations[0]
    duplicate = deepcopy(first)
    audit_forward = robustness_module._overlap_audit((first, duplicate))
    audit_reverse = robustness_module._overlap_audit((duplicate, first))

    assert audit_forward == audit_reverse
    assert audit_forward.raw_replayed_candidate_count == 2
    assert audit_forward.component_count == 1
    assert audit_forward.largest_component_size == 2
    assert audit_forward.components[0].comparable_count == 0
    assert audit_forward.equal_weight_component_mean_delta is None


def test_all_comparison_states_and_authoritative_comparable_deltas_are_counted() -> None:
    def branch(closed: bool) -> SimpleNamespace:
        return SimpleNamespace(
            position_closed=closed,
            winning_exit_cause=None,
            protective_stop_precedence_applied=False,
            exit_unfilled=None,
        )

    def observation(
        state: PairedExitComparisonState,
        *,
        delta: Decimal | None = None,
    ) -> SimpleNamespace:
        replayed = state not in {
            PairedExitComparisonState.ENTRY_UNFILLED,
            PairedExitComparisonState.ENTRY_FILL_INVALIDATED,
        }
        baseline_closed = state in {
            PairedExitComparisonState.BOTH_CLOSED,
            PairedExitComparisonState.BASELINE_CLOSED_CANDIDATE_OPEN,
        }
        candidate_closed = state in {
            PairedExitComparisonState.BOTH_CLOSED,
            PairedExitComparisonState.BASELINE_OPEN_CANDIDATE_CLOSED,
        }
        return SimpleNamespace(
            paired_result=SimpleNamespace(
                status=(
                    PairedExitReplayStatus.REPLAYED
                    if replayed
                    else PairedExitReplayStatus(state.value)
                ),
                comparison_state=state,
                baseline=branch(baseline_closed) if replayed else None,
                ema50_candidate=branch(candidate_closed) if replayed else None,
                realized_net_pnl_delta_candidate_minus_baseline=delta,
                exit_fill_time_delta_candidate_minus_baseline=(
                    timedelta(minutes=-15) if delta is not None else None
                ),
            )
        )

    observations = cast(
        tuple[PairedExitRobustnessObservation, ...],
        (
            observation(PairedExitComparisonState.ENTRY_UNFILLED),
            observation(PairedExitComparisonState.ENTRY_FILL_INVALIDATED),
            observation(
                PairedExitComparisonState.BOTH_CLOSED,
                delta=Decimal(2),
            ),
            observation(PairedExitComparisonState.BOTH_OPEN),
            observation(PairedExitComparisonState.BASELINE_CLOSED_CANDIDATE_OPEN),
            observation(PairedExitComparisonState.BASELINE_OPEN_CANDIDATE_CLOSED),
        ),
    )
    population = robustness_module._population(observations)
    deltas = robustness_module._deltas(observations)

    assert population.candidate_count == 6
    assert population.entry_unfilled_count == 1
    assert population.entry_fill_invalidated_count == 1
    assert population.entry_filled_count == 5
    assert population.replayed_count == 4
    assert population.both_closed_count == 1
    assert population.both_open_count == 1
    assert population.baseline_closed_candidate_open_count == 1
    assert population.baseline_open_candidate_closed_count == 1
    assert population.entry_fill_invalidated_rate.numerator == 1
    assert population.entry_fill_invalidated_rate.denominator == 6
    assert population.comparable_fraction.denominator == 4
    invalidated_only = cast(
        tuple[PairedExitRobustnessObservation, ...],
        (observation(PairedExitComparisonState.ENTRY_FILL_INVALIDATED),),
    )
    invalidated_overlap = robustness_module._overlap_audit(invalidated_only)
    assert invalidated_overlap.raw_replayed_candidate_count == 0
    assert invalidated_overlap.component_count == 0
    assert deltas.comparable_count == 1
    assert deltas.mean_net_pnl_delta == Decimal(2)
    assert deltas.median_exit_fill_time_delta_seconds == Decimal(-900)


def test_component_balanced_sensitivity_gives_each_component_equal_weight(
    robustness_report: tuple[MemoryHistoricalStore, TrendPullbackExitRobustnessReport],
) -> None:
    _, report = robustness_report
    btc = next(item for item in report.observations if item.symbol == BTC)
    eth = next(item for item in report.observations if item.symbol == ETH)

    def comparable_copy(
        source: PairedExitRobustnessObservation,
        delta: Decimal,
    ) -> PairedExitRobustnessObservation:
        item = deepcopy(source)
        result = item.paired_result
        assert result.entry_fill is not None
        assert result.baseline is not None and result.ema50_candidate is not None
        fill_at = result.entry_fill.fill_at + timedelta(hours=1)
        object.__setattr__(result, "comparison_state", PairedExitComparisonState.BOTH_CLOSED)
        object.__setattr__(result, "realized_net_pnl_delta_candidate_minus_baseline", delta)
        object.__setattr__(result.baseline, "exit_fill", SimpleNamespace(fill_at=fill_at))
        object.__setattr__(result.ema50_candidate, "exit_fill", SimpleNamespace(fill_at=fill_at))
        return item

    observations = (
        comparable_copy(btc, Decimal(1)),
        comparable_copy(btc, Decimal(1)),
        comparable_copy(btc, Decimal(1)),
        comparable_copy(eth, Decimal(9)),
    )
    audit = robustness_module._overlap_audit(observations)

    assert audit.component_count == 2
    assert audit.comparable_component_count == 2
    assert audit.equal_weight_component_mean_delta == Decimal(5)
    assert audit.median_component_mean_delta == Decimal(5)


def test_robustness_surface_has_no_portfolio_or_runtime_integration() -> None:
    report_fields = {item.name for item in fields(TrendPullbackExitRobustnessReport)}
    forbidden = {
        "portfolio_equity",
        "equity_curve",
        "drawdown",
        "sharpe",
        "cagr",
        "portfolio_pnl",
    }
    source = inspect.getsource(robustness_module)

    assert report_fields.isdisjoint(forbidden)
    assert "adaptive_trading_bot.experience" not in source
    assert "adaptive_trading_bot.paper" not in source
    assert "adaptive_trading_bot.live" not in source
    assert "PairedTrendPullbackExitReplayRunner" in source
    assert "TrendPullbackHistoricalCorpusGenerator" in source
    assert "PositionAccounting" not in source
    assert "protective_stop_breached" not in source
    assert "price_simulated_market_fill" not in source
    assert "atb-research-exit-robustness" in Path("pyproject.toml").read_text(encoding="utf-8")
