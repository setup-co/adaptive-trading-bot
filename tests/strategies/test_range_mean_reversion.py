from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext

import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.features import (
    ConfirmedPivot,
    FeatureSnapshot,
    PivotKind,
    StructureSnapshot,
)
from adaptive_trading_bot.regime import (
    Direction,
    DirectionComponent,
    DirectionEvidence,
    EventRiskState,
    HigherTimeframeBias,
    LiquidityState,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeSnapshot,
    RegimeLabel,
    TechnicalRegimeSnapshot,
    VolatilityState,
    VolumeState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    RangeContextSnapshot,
    RangeMeanReversionConfiguration,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    RangeMeanReversionSide,
    RangeMeanReversionStrategy,
    RangeReasonCode,
    RangeStateError,
    RangeStatus,
    Strategy,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


def _technical(
    source: SourceId,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    observed_at: datetime,
) -> TechnicalRegimeSnapshot:
    evidence = tuple(DirectionEvidence(component, 0, "test") for component in DirectionComponent)
    return TechnicalRegimeSnapshot(
        source,
        symbol,
        timeframe,
        observed_at,
        100,
        Direction.NEUTRAL,
        0,
        0,
        evidence,
        Decimal(50),
        VolatilityState.NORMAL,
        VolumeState.NORMAL,
        LiquidityState.UNKNOWN,
        EventRiskState.UNKNOWN,
        RegimeLabel.NEUTRAL,
        True,
    )


def _pivot(
    kind: PivotKind,
    price: Decimal,
    confirmed_at: datetime,
    occurrence: int,
    source: SourceId,
    symbol: TradingSymbol,
) -> ConfirmedPivot:
    pivot_open = START - timedelta(hours=4) + occurrence * Timeframe.MINUTES_15.duration
    return ConfirmedPivot(
        source,
        symbol,
        Timeframe.MINUTES_15,
        kind,
        pivot_open,
        pivot_open + Timeframe.MINUTES_15.duration,
        price,
        confirmed_at,
        2,
        2,
        occurrence,
        None,
    )


def _context(
    sequence: int = 1,
    *,
    source: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
    open_price: Decimal = Decimal(85),
    high: Decimal = Decimal(95),
    low: Decimal = Decimal("82.5"),
    close: Decimal = Decimal(90),
    rsi: Decimal | None = Decimal(45),
    momentum: Decimal | None = Decimal(0),
    pivots: tuple[tuple[PivotKind, Decimal, int], ...] = (),
) -> StrategyEvaluationContext:
    observed_at = START + (sequence - 1) * Timeframe.MINUTES_15.duration
    candle = Candle(
        symbol,
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
        source,
        symbol,
        Timeframe.MINUTES_15,
        observed_at,
        candle.open_time,
        candle.close_time,
        100,
        Decimal(100),
        Decimal(100),
        Decimal(10),
        rsi,
        Decimal(100),
        Decimal(0),
        momentum,
        Decimal(0),
        Decimal(0),
        Decimal("0.01"),
        True,
    )
    confirmed = tuple(
        _pivot(kind, price, observed_at, occurrence, source, symbol)
        for kind, price, occurrence in pivots
    )
    structure = StructureSnapshot(
        source,
        symbol,
        Timeframe.MINUTES_15,
        observed_at,
        100,
        confirmed,
        next((item for item in reversed(confirmed) if item.kind is PivotKind.HIGH), None),
        next((item for item in reversed(confirmed) if item.kind is PivotKind.LOW), None),
        None,
        None,
    )
    entry = _technical(source, symbol, Timeframe.MINUTES_15, observed_at)
    one = _technical(source, symbol, Timeframe.HOUR_1, observed_at)
    four = _technical(source, symbol, Timeframe.HOURS_4, observed_at)
    regime = MultiTimeframeRegimeSnapshot(
        source,
        symbol,
        observed_at,
        entry,
        one,
        four,
        timedelta(),
        timedelta(),
        True,
        True,
        True,
        HigherTimeframeBias.NEUTRAL,
        MultiTimeframeAlignment.NEUTRAL,
        False,
        (),
        (),
    )
    return StrategyEvaluationContext(
        source, symbol, observed_at, candle, feature, structure, regime
    )


def _valid_snapshot(context: StrategyEvaluationContext, **changes: object) -> RangeContextSnapshot:
    snapshot = RangeContextSnapshot(
        context.source_id,
        context.symbol,
        context.observed_at,
        RangeStatus.VALID_RANGE,
        True,
        Decimal(80),
        Decimal(120),
        Decimal(100),
        Decimal(40),
        Decimal(4),
        Decimal(10),
        Decimal(0),
        Decimal(0),
        (),
        0,
        Decimal("121.5"),
        Decimal("78.5"),
        (RangeReasonCode.RANGE_GEOMETRY_VALID, RangeReasonCode.REGIME_COMPATIBLE),
    )
    return replace(snapshot, **changes)  # type: ignore[arg-type]


@dataclass
class StubDetector:
    snapshot: RangeContextSnapshot
    calls: int = 0

    def process(self, context: StrategyEvaluationContext) -> RangeContextSnapshot:
        self.calls += 1
        return self.snapshot


def _evaluate(
    context: StrategyEvaluationContext, snapshot: RangeContextSnapshot | None = None
) -> tuple[
    StrategyEvaluation[RangeMeanReversionReasonCode, RangeMeanReversionRuleAudit], StubDetector
]:
    detector = StubDetector(_valid_snapshot(context) if snapshot is None else snapshot)
    result = RangeMeanReversionStrategy(range_detector=detector).evaluate(context)
    return result, detector


def test_shared_strategy_contract_and_identity_are_unchanged() -> None:
    context = _context()
    strategy: Strategy[RangeMeanReversionReasonCode, RangeMeanReversionRuleAudit] = (
        RangeMeanReversionStrategy(range_detector=StubDetector(_valid_snapshot(context)))
    )

    result = strategy.evaluate(context)

    assert isinstance(strategy, Strategy)
    assert strategy.strategy_id == "range_mean_reversion"
    assert strategy.strategy_version == "1.0.0"
    assert result.action is CandidateAction.LONG_CANDIDATE


def test_configuration_defaults_and_validation_are_exact() -> None:
    assert RangeMeanReversionConfiguration() == RangeMeanReversionConfiguration(
        entry_timeframe=Timeframe.MINUTES_15,
        boundary_entry_tolerance_atr=Decimal("0.25"),
        maximum_boundary_excursion_atr=Decimal("0.20"),
        long_max_rsi=Decimal(45),
        short_min_rsi=Decimal(55),
        max_invalidation_distance_atr=Decimal("3.0"),
    )
    with pytest.raises(TypeError, match="Decimal"):
        RangeMeanReversionConfiguration(boundary_entry_tolerance_atr=0.25)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        RangeMeanReversionConfiguration(long_max_rsi=Decimal("NaN"))
    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        RangeMeanReversionConfiguration(short_min_rsi=Decimal(101))
    with pytest.raises(ValueError, match="positive"):
        RangeMeanReversionConfiguration(max_invalidation_distance_atr=Decimal(0))


def test_detector_is_processed_exactly_once_per_evaluation() -> None:
    result, detector = _evaluate(_context())
    assert detector.calls == 1
    assert result.action is CandidateAction.LONG_CANDIDATE


@pytest.mark.parametrize("invalid_sequence", [1, 0, 3])
def test_detector_chronology_failures_propagate_without_hidden_reset(
    invalid_sequence: int,
) -> None:
    strategy = RangeMeanReversionStrategy()
    strategy.evaluate(_context(1))
    with pytest.raises(RangeStateError, match="exactly one"):
        strategy.evaluate(_context(invalid_sequence))
    assert strategy.evaluate(_context(2)).action is CandidateAction.NO_TRADE


def test_no_candidate_before_fourth_causally_confirmed_pivot() -> None:
    strategy = RangeMeanReversionStrategy()
    order = (
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
        (PivotKind.LOW, Decimal(80)),
        (PivotKind.HIGH, Decimal(120)),
    )
    results = tuple(
        strategy.evaluate(_context(index, pivots=((kind, price, index),)))
        for index, (kind, price) in enumerate(order, start=1)
    )
    assert all(result.action is CandidateAction.NO_TRADE for result in results[:3])
    assert results[3].action is CandidateAction.LONG_CANDIDATE
    assert results[3].rule_audit.range_status is RangeStatus.VALID_RANGE


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (RangeStatus.INSUFFICIENT_DATA, RangeMeanReversionReasonCode.RANGE_INSUFFICIENT_DATA),
        (RangeStatus.NOT_RANGE, RangeMeanReversionReasonCode.RANGE_NOT_VALID),
        (RangeStatus.RANGE_STALE, RangeMeanReversionReasonCode.RANGE_STALE),
        (RangeStatus.RANGE_BROKEN, RangeMeanReversionReasonCode.RANGE_BROKEN),
    ],
)
def test_every_nonvalid_range_status_is_no_trade(
    status: RangeStatus, reason: RangeMeanReversionReasonCode
) -> None:
    context = _context()
    snapshot = _valid_snapshot(context, status=status, is_valid_range=False)
    result, _ = _evaluate(context, snapshot)
    assert result.action is CandidateAction.NO_TRADE
    assert result.reasons_against == (reason,)
    assert result.rule_audit.range_status is status
    assert result.rule_audit.range_reasons == snapshot.reasons


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": SourceId("wrong")},
        {"symbol": ETH},
        {"observed_at": START + timedelta(minutes=15)},
        {"is_valid_range": False},
        {"lower_boundary": None},
        {"range_width": Decimal(39)},
        {"width_in_atr": Decimal(5)},
        {"current_atr": Decimal(11)},
        {"midpoint": Decimal(80)},
        {"lower_breakout_threshold": Decimal(80)},
        {"upper_breakout_threshold": Decimal(120)},
    ],
)
def test_valid_range_snapshot_mismatch_or_inconsistency_fails_closed(
    changes: dict[str, object],
) -> None:
    context = _context()
    result, _ = _evaluate(context, _valid_snapshot(context, **changes))
    assert result.action is CandidateAction.NO_TRADE
    assert result.reasons_against == (RangeMeanReversionReasonCode.RANGE_SNAPSHOT_INCONSISTENT,)


def test_lower_only_touch_emits_long_candidate_with_detector_invalidation() -> None:
    context = _context()
    result, _ = _evaluate(context)
    assert result.action is CandidateAction.LONG_CANDIDATE
    assert result.reference_price == context.candle_15m.close == Decimal(90)
    assert result.invalidation_reference_price == Decimal("78.5")
    assert result.invalidation_reference_price != context.candle_15m.low
    assert result.rule_audit.selected_side is RangeMeanReversionSide.LONG
    assert result.rule_audit.lower_entry_zone_ceiling == Decimal("82.50")
    assert result.rule_audit.lower_excursion_floor == Decimal("78.00")


def test_upper_only_touch_emits_exact_mirror_short_candidate() -> None:
    context = _context(
        open_price=Decimal(115),
        high=Decimal("117.5"),
        low=Decimal(105),
        close=Decimal(110),
        rsi=Decimal(55),
    )
    result, _ = _evaluate(context)
    assert result.action is CandidateAction.SHORT_CANDIDATE
    assert result.reference_price == Decimal(110)
    assert result.invalidation_reference_price == Decimal("121.5")
    assert result.rule_audit.selected_side is RangeMeanReversionSide.SHORT
    assert result.rule_audit.upper_entry_zone_floor == Decimal("117.50")
    assert result.rule_audit.upper_excursion_ceiling == Decimal("122.00")


@pytest.mark.parametrize(
    ("side", "excursion", "expected"),
    [
        (RangeMeanReversionSide.LONG, Decimal(78), CandidateAction.LONG_CANDIDATE),
        (RangeMeanReversionSide.LONG, Decimal("77.99"), CandidateAction.NO_TRADE),
        (RangeMeanReversionSide.SHORT, Decimal(122), CandidateAction.SHORT_CANDIDATE),
        (RangeMeanReversionSide.SHORT, Decimal("122.01"), CandidateAction.NO_TRADE),
    ],
)
def test_maximum_excursion_boundaries_are_inclusive(
    side: RangeMeanReversionSide, excursion: Decimal, expected: CandidateAction
) -> None:
    context = (
        _context(low=excursion)
        if side is RangeMeanReversionSide.LONG
        else _context(
            open_price=Decimal(115),
            high=excursion,
            low=Decimal(105),
            close=Decimal(110),
            rsi=Decimal(55),
        )
    )
    result, _ = _evaluate(context)
    assert result.action is expected
    if expected is CandidateAction.NO_TRADE:
        assert RangeMeanReversionReasonCode.BOUNDARY_EXCURSION_TOO_DEEP in result.reasons_against


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (
            _context(open_price=Decimal(79), high=Decimal(90), low=Decimal(78), close=Decimal(80)),
            RangeMeanReversionReasonCode.RANGE_RECLAIM_FAILED,
        ),
        (
            _context(
                open_price=Decimal(121),
                high=Decimal(122),
                low=Decimal(110),
                close=Decimal(120),
                rsi=Decimal(55),
            ),
            RangeMeanReversionReasonCode.RANGE_REJECTION_FAILED,
        ),
    ],
)
def test_close_exactly_on_boundary_fails_strict_reentry(
    context: StrategyEvaluationContext, reason: RangeMeanReversionReasonCode
) -> None:
    result, _ = _evaluate(context)
    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


@pytest.mark.parametrize("side", [RangeMeanReversionSide.LONG, RangeMeanReversionSide.SHORT])
def test_midpoint_exact_boundary_is_accepted(side: RangeMeanReversionSide) -> None:
    context = (
        _context(open_price=Decimal(90), high=Decimal(105), close=Decimal(100))
        if side is RangeMeanReversionSide.LONG
        else _context(
            open_price=Decimal(110),
            high=Decimal("117.5"),
            low=Decimal(95),
            close=Decimal(100),
            rsi=Decimal(55),
        )
    )
    assert _evaluate(context)[0].action is (
        CandidateAction.LONG_CANDIDATE
        if side is RangeMeanReversionSide.LONG
        else CandidateAction.SHORT_CANDIDATE
    )


@pytest.mark.parametrize(
    "context",
    [
        _context(open_price=Decimal(90), high=Decimal(105), close=Decimal("100.01")),
        _context(
            open_price=Decimal(110),
            high=Decimal("117.5"),
            low=Decimal(95),
            close=Decimal("99.99"),
            rsi=Decimal(55),
        ),
    ],
)
def test_midpoint_chase_guard_rejects_crossed_price(
    context: StrategyEvaluationContext,
) -> None:
    result, _ = _evaluate(context)
    assert result.action is CandidateAction.NO_TRADE
    assert RangeMeanReversionReasonCode.MIDPOINT_ALREADY_CROSSED in result.reasons_against


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (
            _context(open_price=Decimal(90), close=Decimal(90)),
            RangeMeanReversionReasonCode.CANDLE_REVERSAL_FAILED,
        ),
        (
            _context(
                open_price=Decimal(110),
                high=Decimal("117.5"),
                low=Decimal(105),
                close=Decimal(110),
                rsi=Decimal(55),
            ),
            RangeMeanReversionReasonCode.CANDLE_REVERSAL_FAILED,
        ),
        (_context(rsi=Decimal("45.01")), RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED),
        (
            _context(
                open_price=Decimal(115),
                high=Decimal("117.5"),
                low=Decimal(105),
                close=Decimal(110),
                rsi=Decimal("54.99"),
            ),
            RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED,
        ),
        (
            _context(momentum=Decimal("0.01")),
            RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_FAILED,
        ),
        (
            _context(
                open_price=Decimal(115),
                high=Decimal("117.5"),
                low=Decimal(105),
                close=Decimal(110),
                rsi=Decimal(55),
                momentum=Decimal("-0.01"),
            ),
            RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_FAILED,
        ),
    ],
)
def test_reversal_rsi_and_momentum_failures_are_mirrored(
    context: StrategyEvaluationContext, reason: RangeMeanReversionReasonCode
) -> None:
    result, _ = _evaluate(context)
    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


def test_neither_and_both_boundary_touches_fail_closed() -> None:
    neither, _ = _evaluate(
        _context(open_price=Decimal(90), low=Decimal(90), high=Decimal(110), close=Decimal(95))
    )
    both, _ = _evaluate(_context(high=Decimal("117.5")))
    assert neither.reasons_against == (RangeMeanReversionReasonCode.PRICE_NOT_AT_RANGE_EDGE,)
    assert both.reasons_against == (RangeMeanReversionReasonCode.BOTH_BOUNDARIES_TOUCHED,)


@pytest.mark.parametrize("side", [RangeMeanReversionSide.LONG, RangeMeanReversionSide.SHORT])
def test_invalidation_distance_exact_three_atr_is_inclusive_and_above_rejects(
    side: RangeMeanReversionSide,
) -> None:
    if side is RangeMeanReversionSide.LONG:
        context = _context(
            open_price=Decimal(80), high=Decimal(95), low=Decimal("62.5"), close=Decimal("88.5")
        )
        snapshot = _valid_snapshot(
            context,
            lower_boundary=Decimal(60),
            upper_boundary=Decimal(120),
            midpoint=Decimal(90),
            range_width=Decimal(60),
            width_in_atr=Decimal(6),
            lower_breakout_threshold=Decimal("58.5"),
        )
        wider = replace(snapshot, lower_breakout_threshold=Decimal("58.49"))
    else:
        context = _context(
            open_price=Decimal(100),
            high=Decimal("117.5"),
            low=Decimal(90),
            close=Decimal("91.5"),
            rsi=Decimal(55),
        )
        snapshot = _valid_snapshot(
            context,
            lower_boundary=Decimal(60),
            upper_boundary=Decimal(120),
            midpoint=Decimal(90),
            range_width=Decimal(60),
            width_in_atr=Decimal(6),
            lower_breakout_threshold=Decimal("58.5"),
            upper_breakout_threshold=Decimal("121.5"),
        )
        wider = replace(snapshot, upper_breakout_threshold=Decimal("121.51"))
    accepted, _ = _evaluate(context, snapshot)
    rejected, _ = _evaluate(context, wider)
    assert accepted.action is (
        CandidateAction.LONG_CANDIDATE
        if side is RangeMeanReversionSide.LONG
        else CandidateAction.SHORT_CANDIDATE
    )
    assert accepted.rule_audit.invalidation_distance == Decimal(30)
    assert rejected.action is CandidateAction.NO_TRADE
    assert RangeMeanReversionReasonCode.INVALIDATION_TOO_WIDE in rejected.reasons_against


def test_source_and_symbol_state_are_isolated_by_owned_detector() -> None:
    strategy = RangeMeanReversionStrategy()
    results = (
        strategy.evaluate(_context(source=SOURCE, symbol=BTC)),
        strategy.evaluate(_context(source=SOURCE, symbol=ETH)),
        strategy.evaluate(_context(source=SourceId("other"), symbol=BTC)),
    )
    assert all(result.action is CandidateAction.NO_TRADE for result in results)


def test_failure_reason_order_and_decimal_context_are_deterministic() -> None:
    context = _context(
        open_price=Decimal(90),
        high=Decimal(95),
        low=Decimal("77.99"),
        close=Decimal(79),
        rsi=Decimal(46),
        momentum=Decimal(1),
    )
    snapshot = _valid_snapshot(context, lower_breakout_threshold=Decimal("79.5"))
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = _evaluate(context, snapshot)[0]
        setcontext(Context(prec=50))
        high_precision = _evaluate(context, snapshot)[0]
    finally:
        setcontext(original)
    assert low_precision == high_precision
    assert low_precision.reasons_against == (
        RangeMeanReversionReasonCode.BOUNDARY_EXCURSION_TOO_DEEP,
        RangeMeanReversionReasonCode.RANGE_RECLAIM_FAILED,
        RangeMeanReversionReasonCode.CANDLE_REVERSAL_FAILED,
        RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED,
        RangeMeanReversionReasonCode.MOMENTUM_EXHAUSTION_FAILED,
        RangeMeanReversionReasonCode.INVALIDATION_WRONG_SIDE,
    )
    assert not hasattr(low_precision, "quantity")
