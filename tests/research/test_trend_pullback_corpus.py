import ast
import inspect
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path

import pytest

import adaptive_trading_bot.research.trend_pullback_corpus as corpus_module
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import (
    DecisionJournalEntry,
    DecisionJournalValidationError,
)
from adaptive_trading_bot.replay import ReplayIntegrityError, ReplaySeries, ReplaySpeed
from adaptive_trading_bot.research import (
    HistoricalStrategyContextObservation,
    HistoricalTrendPullbackDecisionObservation,
    PairedExitReplayError,
    ResearchAdmissionConfiguration,
    ResearchDecisionCategory,
    ResearchSafetyAssumptionProfile,
    TrendPullbackHistoricalCorpusGenerator,
    TrendPullbackHistoricalResearchCorpus,
    TrendPullbackResearchCorpusError,
)
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
    def __init__(self, symbols: tuple[TradingSymbol, ...] = (BTC, ETH)) -> None:
        self._candles = {
            (SOURCE, symbol, timeframe): tuple(
                _candle(symbol, index, timeframe) for index in range(count)
            )
            for symbol in symbols
            for timeframe, count in COUNTS.items()
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
