"""Synthetic-only Research V2 admission and locked data-boundary checks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

import pytest

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureSnapshot,
    PivotKind,
    StructureSnapshot,
)
from adaptive_trading_bot.historical_sources import (
    HistoricalSourceSelectionError,
    open_historical_store,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import (
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    LiquidityState,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.regime.technical import (
    TechnicalRegimeBatch,
    _direction,
    _direction_evidence,
    _regime_label,
)
from adaptive_trading_bot.research.all_fill_replay import ResearchAllFillReplayRunner
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.research_v2_admission import (
    ADMISSION_VERSION,
    DATASET_VERSION,
    MANIFEST_SHA256,
    PANEL,
    PROFILE,
    SOURCE_ID,
    LockedResearchV2CandleStore,
    ResearchV2AdmissionConfiguration,
    ResearchV2AdmissionError,
    ResearchV2ContextAuthorization,
    ResearchV2EpochBounds,
    ResearchV2RegimeComposer,
    ResearchV2StrategyContext,
    _evaluate_synthetic_epoch,
    _replay_synthetic_all_fill,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    OperationalSafetyGate,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import IntegrityReport, MergeResult, SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    RangeMeanReversionStrategy,
    StrategyEvaluationContext,
)
from adaptive_trading_bot.strategies.core import StrategyContextError

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=208)
SYMBOL = TradingSymbol("SOL", "USDT")


@pytest.fixture(scope="module")
def locked_store() -> LockedResearchV2CandleStore:
    return LockedResearchV2CandleStore(Path(__file__).resolve().parents[2])


class SyntheticEpochStore:
    """A test-only complete epoch, never an accepted production binding."""

    source_id = SOURCE_ID

    def __init__(self, symbol: TradingSymbol, *, pattern: str = "breakout") -> None:
        self.symbol = symbol
        self.pattern = pattern
        self.candles = {
            frame: tuple(
                self._candle(frame, index)
                for index in range(208 * 3600 // int(frame.duration.total_seconds()))
            )
            for frame in (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)
        }

    def _candle(self, frame: Timeframe, index: int) -> Candle:
        opened = START + index * frame.duration
        if self.pattern == "trend":
            close = Decimal(100 + index)
            low = close - Decimal(2)
            if frame is Timeframe.MINUTES_15 and index == 800:
                low = close - Decimal(6)
            if frame is Timeframe.MINUTES_15 and index == 803:
                low = close - Decimal(10)
            return Candle(
                self.symbol,
                frame,
                opened,
                opened + frame.duration,
                close - 1,
                close + 2,
                low,
                close,
                Decimal(10 + index % 5),
            )
        close = Decimal(100 + index + index % 4)
        opening, high, low = close - 1, close + 2, close - 2
        volume = Decimal(10 + index % 5)
        if frame is Timeframe.MINUTES_15:
            if index in {794, 795, 797, 798}:
                high = close + Decimal("0.5")
            elif index == 796:
                high = Decimal(901)
            elif index == 799:
                close, opening, high, low = map(Decimal, (902, 901, 903, 900))
            elif index == 800:
                close, opening, high, low, volume = map(Decimal, (902, 901, 903, 901, 14))
            elif index == 801 and self.pattern == "breakout_invalid":
                close, opening, high, low = map(Decimal, (900, 900, 902, 899))
        candle = Candle(
            self.symbol,
            frame,
            opened,
            opened + frame.duration,
            opening,
            high,
            low,
            close,
            volume,
        )
        if self.pattern != "short":
            return candle
        mirrored = replace(
            candle,
            open=Decimal(2000) - candle.open,
            high=Decimal(2000) - candle.low,
            low=Decimal(2000) - candle.high,
            close=Decimal(2000) - candle.close,
        )
        if frame is Timeframe.MINUTES_15 and index == 801:
            mirrored = replace(
                mirrored,
                open=Decimal(1099),
                high=Decimal(1099),
                low=Decimal(1097),
                close=Decimal(1098),
                volume=Decimal(25),
            )
        if (frame is Timeframe.HOUR_1 and index in {120, 130, 140, 150, 160}) or (
            frame is Timeframe.HOURS_4 and index in {25, 30, 35, 40}
        ):
            changed_close = mirrored.close + Decimal(50)
            mirrored = replace(
                mirrored, high=max(mirrored.high, changed_close + 1), close=changed_close
            )
        return mirrored

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        raise AssertionError("synthetic research store is read-only")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        assert source_id == SOURCE_ID and symbol == self.symbol
        assert start is not None and end is not None
        return tuple(c for c in self.candles[timeframe] if start <= c.open_time < end)

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        return IntegrityReport(
            source_id,
            symbol,
            timeframe,
            start,
            end,
            len(self.read(source_id, symbol, timeframe, start=start, end=end)),
            (),
        )

    def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        assert symbol == self.symbol
        return ((START, END),)

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        assert symbol == self.symbol
        return ((START + timedelta(hours=200), END),)


def _range_technical(
    frame: Timeframe,
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
        SOURCE_ID,
        SYMBOL,
        frame,
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


def _range_context(
    index: int,
    *,
    side: str,
    pivot: tuple[PivotKind, Decimal] | None = None,
    candle: Candle | None = None,
) -> ResearchV2StrategyContext:
    observed_at = START + timedelta(hours=200) + index * timedelta(minutes=15)
    if candle is None:
        opening, high, low, close = (
            (Decimal(85), Decimal(95), Decimal("82.5"), Decimal(90))
            if side == "LONG"
            else (Decimal(115), Decimal("117.5"), Decimal(105), Decimal(110))
        )
        candle = Candle(
            SYMBOL,
            Timeframe.MINUTES_15,
            observed_at - timedelta(minutes=15),
            observed_at,
            opening,
            high,
            low,
            close,
            Decimal(100),
        )
    feature = FeatureSnapshot(
        SOURCE_ID,
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
        pivot_open = observed_at - timedelta(hours=4)
        confirmed = (
            ConfirmedPivot(
                SOURCE_ID,
                SYMBOL,
                Timeframe.MINUTES_15,
                kind,
                pivot_open,
                pivot_open + timedelta(minutes=15),
                price,
                observed_at,
                2,
                2,
                index,
                None,
            ),
        )
    structure = StructureSnapshot(
        SOURCE_ID,
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
    composer = ResearchV2RegimeComposer(ResearchV2ContextAuthorization(SOURCE_ID, SYMBOL))
    mtf = composer.process(
        TechnicalRegimeBatch(
            observed_at,
            (
                _range_technical(Timeframe.MINUTES_15, observed_at, feature, structure),
                _range_technical(Timeframe.HOUR_1, observed_at),
                _range_technical(Timeframe.HOURS_4, observed_at),
            ),
        )
    ).snapshots[0]
    return ResearchV2StrategyContext(
        SOURCE_ID,
        SYMBOL,
        observed_at,
        candle,
        feature,
        structure,
        mtf,
        ResearchV2ContextAuthorization(SOURCE_ID, SYMBOL),
    )


class _RangeExecutionSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        assert series.source_id == SOURCE_ID and series.symbol == SYMBOL
        return tuple(c for c in self.candles if start <= c.open_time < end)


def _config(
    symbol: TradingSymbol = SYMBOL, family: str = "trend_pullback"
) -> ResearchV2AdmissionConfiguration:
    return ResearchV2AdmissionConfiguration(
        ResearchV2EpochBounds(
            symbol,
            START,
            START + timedelta(hours=200),
            START + timedelta(hours=204),
            END,
        ),
        family,
        Decimal("10000"),
    )


def test_all_ten_real_panel_identities_are_versioned_and_distinct() -> None:
    assert PANEL == (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "ZECUSDT",
        "ZAMAUSDT",
        "BCHUSDT",
        "BTWUSDT",
        "AVAXUSDT",
        "DOGEUSDT",
    )
    assert len(set(PANEL)) == 10
    for wire in PANEL:
        symbol = TradingSymbol(wire[:-4], "USDT")
        ResearchV2ContextAuthorization(SOURCE_ID, symbol).validate()
    assert PROFILE == "NORMALIZED_BASE_SIGNAL_V1"
    assert ADMISSION_VERSION == "RESEARCH_V2_ISOLATED_ADMISSION_V1"


@pytest.mark.parametrize("wire", PANEL)
def test_each_exact_panel_symbol_traverses_replay_to_cleared_non_anchor_entry(wire: str) -> None:
    symbol = TradingSymbol(wire[:-4], "USDT")
    population = _evaluate_synthetic_epoch(
        SyntheticEpochStore(symbol), _config(symbol, "breakout_retest")
    )
    assert any(
        decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.risk_decision.status.value == "APPROVED"
        and decision.safety_decision.status.value == "CLEARED"
        for decision in population.decisions
    )
    assert all(decision.identity.symbol == symbol for decision in population.decisions)


def test_normal_v1_context_still_rejects_non_anchor() -> None:
    context = _evaluate_synthetic_epoch(SyntheticEpochStore(SYMBOL), _config()).contexts[0].context
    with pytest.raises(StrategyContextError, match="outside V1"):
        StrategyEvaluationContext(
            context.source_id,
            context.symbol,
            context.observed_at,
            context.candle_15m,
            context.features_15m,
            context.structure_15m,
            context.multi_timeframe_regime,
        )


@pytest.mark.parametrize("family", ["trend_pullback", "breakout_retest", "range_mean_reversion"])
def test_synthetic_replay_uses_real_authority_chain_for_non_anchor(family: str) -> None:
    store = SyntheticEpochStore(SYMBOL)
    population = _evaluate_synthetic_epoch(store, _config(family=family))
    population.validate()
    assert len(population.decisions) == 16
    assert population.decisions[0].identity.symbol == SYMBOL
    assert all(decision.context.symbol == SYMBOL for decision in population.decisions)
    assert all(
        decision.strategy_evaluation.strategy_id == family for decision in population.decisions
    )
    assert all(
        decision.strategy_evaluation.action in CandidateAction for decision in population.decisions
    )
    assert population.source_id == SOURCE_ID
    assert population.profile == PROFILE
    if family == "breakout_retest":
        assert any(
            decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
            and decision.risk_decision.status.value == "APPROVED"
            and decision.safety_decision.status.value == "CLEARED"
            for decision in population.decisions
        )


def test_unknown_profile_source_and_epoch_are_rejected_before_replay() -> None:
    store = SyntheticEpochStore(SYMBOL)
    configuration = _config()
    with pytest.raises(ResearchV2AdmissionError):
        replace(configuration.bounds, profile="UNKNOWN").validate(store)
    with pytest.raises(ResearchV2AdmissionError):
        replace(configuration.bounds, source_id=SourceId("other")).validate(store)
    with pytest.raises(ResearchV2AdmissionError):
        replace(configuration.bounds, follow_up_end=END + timedelta(hours=4)).validate(store)
    with pytest.raises(ResearchV2AdmissionError):
        replace(
            ResearchV2ContextAuthorization(SOURCE_ID, SYMBOL),
            manifest_sha256="0" * 64,
        ).validate()
    with pytest.raises(ResearchV2AdmissionError):
        replace(
            ResearchV2ContextAuthorization(SOURCE_ID, SYMBOL),
            profile="VENUE_CONTRACT",
        ).validate()


def test_warmup_first_close_and_epoch_reset_boundaries() -> None:
    store = SyntheticEpochStore(SYMBOL, pattern="breakout")
    first = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    second = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    assert first.decisions == second.decisions
    assert first.decisions[0].identity.observed_at == START + timedelta(hours=200, minutes=15)
    assert first.decisions[0].context.candle_15m.open_time == START + timedelta(hours=200)

    class SplitEpochStore(SyntheticEpochStore):
        def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
            assert symbol == self.symbol
            return (
                (START, START + timedelta(hours=202)),
                (START + timedelta(hours=203), END),
            )

    with pytest.raises(ResearchV2AdmissionError, match="epoch"):
        _config(family="breakout_retest").validate(SplitEpochStore(SYMBOL))


def test_non_anchor_breakout_entry_uses_existing_all_fill_authorities() -> None:
    store = SyntheticEpochStore(SYMBOL)
    population = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    decision = next(
        decision
        for decision in population.decisions
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        decision,
        HistoricalExecutionSource(store),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    wrapped.validate()
    assert wrapped.profile == PROFILE
    assert wrapped.result.decision.identity.symbol == SYMBOL
    assert wrapped.result.entry_intent is not None
    assert wrapped.result.entry_intent.approved_risk_quote > 0
    assert wrapped.result.branches
    branch = wrapped.result.branches[0]
    assert branch.entry_fill.symbol == SYMBOL
    assert branch.entry_fill.quantity > 0
    assert branch.entry_fill.fee_quote > 0
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        assert branch.entry_fill.fee_quote == (
            branch.entry_fill.quantity * branch.entry_fill.fill_price * Decimal("0.001")
        )
        assert branch.closed_net_r == (
            branch.final_position.cumulative_net_realized_pnl / branch.approved_risk_quote
        )
    assert branch.entry_fill.quantity * branch.entry_fill.fill_price != (
        branch.entry_fill.quantity * branch.entry_fill.fill_price * Decimal("0.1")
    )


def test_non_anchor_trend_admission_reuses_paired_all_fill_branches() -> None:
    store = SyntheticEpochStore(SYMBOL, pattern="trend")
    population = _evaluate_synthetic_epoch(store, _config())
    decision = next(
        decision
        for decision in population.decisions
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        decision,
        HistoricalExecutionSource(store),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    wrapped.validate()
    assert len(wrapped.result.branches) == 2
    assert wrapped.result.branches[0].entry_fill == wrapped.result.branches[1].entry_fill
    assert wrapped.result.branches[1].approved_risk_quote == (
        decision.risk_decision.approved_risk_quote
    )


def test_non_anchor_short_breakout_passes_risk_safety_and_accounting() -> None:
    store = SyntheticEpochStore(SYMBOL, pattern="short")
    population = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    decision = next(
        decision
        for decision in population.decisions
        if decision.strategy_evaluation.action is CandidateAction.SHORT_CANDIDATE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        decision,
        HistoricalExecutionSource(store),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    wrapped.validate()
    assert wrapped.result.branches[0].entry_fill.symbol == SYMBOL
    assert wrapped.result.branches[0].entry_fill.quantity > 0


def test_non_anchor_invalidated_fill_remains_accounted_and_recovers_causally() -> None:
    store = SyntheticEpochStore(SYMBOL, pattern="breakout_invalid")
    population = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    decision = next(
        decision
        for decision in population.decisions
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.safety_decision.status.value == "CLEARED"
    )
    wrapped = _replay_synthetic_all_fill(
        population,
        decision,
        HistoricalExecutionSource(store),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
    )
    wrapped.validate()
    branch = wrapped.result.branches[0]
    assert branch.entry_fill.fee_quote > 0
    assert branch.recovery is not None
    assert branch.recovery.request.created_at == branch.entry_fill.fill_at
    assert branch.exit_fill is not None
    assert branch.exit_fill.fill_at > branch.entry_fill.fill_at
    assert branch.final_position.quantity == 0


def test_non_anchor_no_trade_risk_and_safety_gates_remain_separate() -> None:
    store = SyntheticEpochStore(SYMBOL)
    no_trade = _evaluate_synthetic_epoch(store, _config(family="range_mean_reversion"))
    assert all(
        decision.strategy_evaluation.action is CandidateAction.NO_TRADE
        for decision in no_trade.decisions
    )
    admitted = _evaluate_synthetic_epoch(store, _config(family="breakout_retest"))
    candidate = next(
        decision
        for decision in admitted.decisions
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    losing_portfolio = RiskPortfolioSnapshot(
        candidate.identity.observed_at,
        "USDT",
        Decimal(9900),
        Decimal(10000),
        Decimal(10000),
        (),
    )
    rejected = RiskManager().evaluate(candidate.strategy_evaluation, losing_portfolio)
    rejected_safety = OperationalSafetyGate().evaluate(
        rejected, _research_health(candidate.context, ResearchSafetyAssumptionProfile())
    )
    blocked = DecisionJournalEntry(
        candidate.context, candidate.strategy_evaluation, rejected, rejected_safety
    )
    blocked.validate()
    assert rejected.status.value == "REJECTED"
    # A daily-loss halt is sovereign Safety SAFE_MODE, even with Risk rejected.
    assert rejected_safety.status.value == "SAFE_MODE"
    assert EntryOrderIntentAuthorizer().authorize(blocked).status is (
        OrderIntentAuthorizationStatus.NO_INTENT
    )
    blocked_safety = _evaluate_synthetic_epoch(
        store,
        replace(
            _config(family="breakout_retest"),
            safety_assumptions=ResearchSafetyAssumptionProfile(
                exchange_api_health_state=ExchangeApiHealthState.DEGRADED
            ),
        ),
    )
    assert any(
        decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
        and decision.risk_decision.status.value == "APPROVED"
        and decision.safety_decision.status.value == "SAFE_MODE"
        for decision in blocked_safety.decisions
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_non_anchor_real_range_detector_to_risk_safety_journal_and_all_fill(side: str) -> None:
    strategy = RangeMeanReversionStrategy()
    pivots = (
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
    )
    decision: DecisionJournalEntry[object, object] | None = None
    for index, pivot in enumerate(pivots, start=1):
        context = _range_context(index, side=side, pivot=pivot)
        evaluation = strategy.evaluate(context)
        equity = Decimal(10000)
        portfolio = RiskPortfolioSnapshot(context.observed_at, "USDT", equity, equity, equity, ())
        risk = RiskManager().evaluate(evaluation, portfolio)
        safety = OperationalSafetyGate().evaluate(
            risk, _research_health(context, ResearchSafetyAssumptionProfile())
        )
        decision = DecisionJournalEntry(context, evaluation, risk, safety)
        decision.validate()
    assert decision is not None
    assert decision.strategy_evaluation.action is (
        CandidateAction.LONG_CANDIDATE if side == "LONG" else CandidateAction.SHORT_CANDIDATE
    )
    assert decision.risk_decision.status.value == "APPROVED"
    assert decision.safety_decision.status.value == "CLEARED"
    assert EntryOrderIntentAuthorizer().authorize(decision).status is (
        OrderIntentAuthorizationStatus.AUTHORIZED
    )
    contexts: list[HistoricalStrategyContextObservation] = []
    candles: list[Candle] = []
    for index in range(5, 10):
        observed_at = START + timedelta(hours=200) + index * timedelta(minutes=15)
        opening = Decimal(90) if side == "LONG" else Decimal(110)
        high, low = opening + 1, opening - 1
        if index == 7:
            if side == "LONG":
                high = Decimal(101)
            else:
                low = Decimal(99)
        candle = Candle(
            SYMBOL,
            Timeframe.MINUTES_15,
            observed_at - timedelta(minutes=15),
            observed_at,
            opening,
            high,
            low,
            opening,
            Decimal(100),
        )
        candles.append(candle)
        contexts.append(
            HistoricalStrategyContextObservation(_range_context(index, side=side, candle=candle))
        )
    result = ResearchAllFillReplayRunner(_RangeExecutionSource(tuple(candles))).run(
        decision,
        tuple(contexts),
        ExecutionSeries(SOURCE_ID, SYMBOL, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10)),
        start=decision.identity.observed_at,
        end=START + timedelta(hours=200) + timedelta(minutes=10 * 15),
    )
    result.validate()
    assert result.entry_intent is not None
    assert result.branches
    assert result.branches[0].entry_fill.symbol == SYMBOL
    assert result.branches[0].entry_fill.fee_quote > 0


def test_locked_reader_is_not_a_study_entry_point(
    locked_store: LockedResearchV2CandleStore,
) -> None:
    store = locked_store
    assert store.source_id == SOURCE_ID
    assert len(store.members) == 10
    assert all(member.profile == PROFILE for member in store.members)
    assert DATASET_VERSION == "weex-research-v2-last-90d-issue-179-combined-v2"
    assert MANIFEST_SHA256 == "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
    with pytest.raises(ResearchV2AdmissionError, match="later activation"):
        _evaluate_synthetic_epoch(store, _config())


def test_real_store_rejects_gap_off_grid_and_postconstruction_mutation(
    locked_store: LockedResearchV2CandleStore,
) -> None:
    store = locked_store
    btc = TradingSymbol("BTC", "USDT")
    epoch_start, _ = store.data_only_epochs(btc)[0]
    assert store.read(
        SOURCE_ID,
        btc,
        Timeframe.MINUTES_15,
        start=epoch_start,
        end=epoch_start + timedelta(minutes=15),
    )
    with pytest.raises(ResearchV2AdmissionError, match="epoch"):
        store.read(
            SOURCE_ID,
            btc,
            Timeframe.MINUTES_15,
            start=epoch_start,
            end=store.data_only_epochs(btc)[1][0],
        )
    with pytest.raises(ResearchV2AdmissionError, match="grid"):
        store.read(
            SOURCE_ID,
            btc,
            Timeframe.MINUTES_15,
            start=epoch_start + timedelta(minutes=1),
            end=epoch_start + timedelta(minutes=16),
        )
    key = ("BTCUSDT", "15m")
    original = store._reader._tables[key]
    try:
        store._reader._tables[key] = original.slice(1)
        with pytest.raises(ResearchV2AdmissionError, match="loaded research candle table"):
            store.read(
                SOURCE_ID,
                btc,
                Timeframe.MINUTES_15,
                start=epoch_start,
                end=epoch_start + timedelta(minutes=15),
            )
    finally:
        store._reader._tables[key] = original


def test_locked_read_rechecks_manifest_bytes_after_construction(
    locked_store: LockedResearchV2CandleStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adaptive_trading_bot.research import research_v2_admission as admission

    original_hash = admission._hash
    target = locked_store._root / "manifest.json"

    def changed_hash(path: Path) -> str:
        return "0" * 64 if path == target else original_hash(path)

    btc = TradingSymbol("BTC", "USDT")
    start = locked_store.data_only_epochs(btc)[0][0]
    monkeypatch.setattr(admission, "_hash", changed_hash)
    with pytest.raises(ResearchV2AdmissionError, match="locked research input changed"):
        locked_store.read(
            SOURCE_ID,
            btc,
            Timeframe.MINUTES_15,
            start=start,
            end=start + timedelta(minutes=15),
        )


def test_generic_and_legacy_paths_do_not_admit_research_v2(
    tmp_path: Path,
) -> None:
    with pytest.raises(HistoricalSourceSelectionError, match="separate accepted"):
        open_historical_store(
            source_ids=(SOURCE_ID,),
            generic_root=tmp_path,
            qualified_corpus_root=tmp_path,
            dataset_version=None,
        )
    with pytest.raises(ResearchV2AdmissionError):
        ResearchV2ContextAuthorization(SOURCE_ID, TradingSymbol("KAS", "USDT")).validate()
