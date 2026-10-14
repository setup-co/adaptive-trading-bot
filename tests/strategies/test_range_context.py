from dataclasses import replace
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
    RangeReasonCode,
    RangeStateError,
    RangeStatus,
    StrategyEvaluationContext,
    ValidatedRangeConfiguration,
    ValidatedRangeDetector,
)

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


def _technical(
    observed_at: datetime,
    source: SourceId,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    trend_strength: int = 0,
) -> TechnicalRegimeSnapshot:
    score = trend_strength
    evidence = tuple(
        DirectionEvidence(component, 20 if index < score // 20 else 0, "test")
        for index, component in enumerate(DirectionComponent)
    )
    return TechnicalRegimeSnapshot(
        source,
        symbol,
        timeframe,
        observed_at,
        100,
        Direction.NEUTRAL if score == 0 else Direction.UP,
        score,
        trend_strength,
        evidence,
        Decimal(50),
        VolatilityState.NORMAL,
        VolumeState.NORMAL,
        LiquidityState.UNKNOWN,
        EventRiskState.UNKNOWN,
        RegimeLabel.NEUTRAL if score == 0 else RegimeLabel.UPTREND,
        True,
    )


def _pivot(
    kind: PivotKind,
    price: Decimal,
    confirmed_at: datetime,
    occurrence: int,
    *,
    source: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
) -> ConfirmedPivot:
    open_time = START - timedelta(hours=10) + occurrence * Timeframe.MINUTES_15.duration
    return ConfirmedPivot(
        source,
        symbol,
        Timeframe.MINUTES_15,
        kind,
        open_time,
        open_time + Timeframe.MINUTES_15.duration,
        price,
        confirmed_at,
        2,
        2,
        occurrence,
        None,
    )


def _context(
    sequence: int,
    pivots: tuple[tuple[PivotKind, Decimal, int], ...] = (),
    *,
    source: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
    atr: Decimal | None = Decimal(10),
    close: Decimal = Decimal(30),
    high: Decimal = Decimal(50),
    low: Decimal = Decimal(10),
    ready: bool = True,
    warmed: bool = True,
    extreme: bool = False,
    bias: HigherTimeframeBias = HigherTimeframeBias.NEUTRAL,
    trend_strength: int = 0,
) -> StrategyEvaluationContext:
    observed_at = START + (sequence - 1) * Timeframe.MINUTES_15.duration
    candle = Candle(
        symbol,
        Timeframe.MINUTES_15,
        observed_at - Timeframe.MINUTES_15.duration,
        observed_at,
        close,
        max(high, close),
        min(low, close),
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
        Decimal(30),
        Decimal(30),
        atr,
        Decimal(50),
        Decimal(30),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal("0.01"),
        warmed,
    )
    confirmed = tuple(
        _pivot(kind, price, observed_at, occurrence, source=source, symbol=symbol)
        for kind, price, occurrence in pivots
    )
    latest_high = next((item for item in reversed(confirmed) if item.kind is PivotKind.HIGH), None)
    latest_low = next((item for item in reversed(confirmed) if item.kind is PivotKind.LOW), None)
    structure = StructureSnapshot(
        source,
        symbol,
        Timeframe.MINUTES_15,
        observed_at,
        100,
        confirmed,
        latest_high,
        latest_low,
        None,
        None,
    )
    entry = _technical(observed_at, source, symbol, Timeframe.MINUTES_15, trend_strength)
    one = _technical(observed_at, source, symbol, Timeframe.HOUR_1)
    four = _technical(observed_at, source, symbol, Timeframe.HOURS_4)
    if bias is HigherTimeframeBias.MIXED:
        one = replace(one, direction=Direction.UP, direction_score=40, trend_strength=40)
        four = replace(four, direction=Direction.DOWN, direction_score=-40, trend_strength=40)
    mtf = MultiTimeframeRegimeSnapshot(
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
        ready,
        bias,
        MultiTimeframeAlignment.NEUTRAL,
        extreme,
        (Timeframe.MINUTES_15,) if extreme else (),
        () if ready else ("not ready",),
    )
    return StrategyEvaluationContext(source, symbol, observed_at, candle, feature, structure, mtf)


def _build_range(
    detector: ValidatedRangeDetector,
    *,
    order: tuple[tuple[PivotKind, Decimal], ...] = (
        (PivotKind.LOW, Decimal(20)),
        (PivotKind.HIGH, Decimal(40)),
        (PivotKind.LOW, Decimal(20)),
        (PivotKind.HIGH, Decimal(40)),
    ),
    atr: Decimal = Decimal(10),
    source: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
) -> object:
    result = None
    for sequence, (kind, price) in enumerate(order, start=1):
        result = detector.process(
            _context(
                sequence,
                ((kind, price, sequence),),
                atr=atr,
                source=source,
                symbol=symbol,
                close=(order[0][1] + order[1][1]) / Decimal(2),
            )
        )
    assert result is not None
    return result


def test_configuration_defaults_are_exact_and_reject_floats_and_bools() -> None:
    assert ValidatedRangeConfiguration() == ValidatedRangeConfiguration(
        entry_timeframe=Timeframe.MINUTES_15,
        pivot_history_limit=12,
        boundary_cluster_tolerance_atr=Decimal("0.40"),
        minimum_range_width_atr=Decimal("2.0"),
        maximum_range_width_atr=Decimal("8.0"),
        breakout_invalidation_buffer_atr=Decimal("0.15"),
        maximum_bars_since_latest_qualifying_pivot=16,
        maximum_15m_trend_strength=40,
    )
    with pytest.raises(TypeError, match="Decimal"):
        ValidatedRangeConfiguration(boundary_cluster_tolerance_atr=0.4)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="integer"):
        ValidatedRangeConfiguration(pivot_history_limit=True)


@pytest.mark.parametrize("invalid_sequence", [1, 0, 3])
def test_chronology_failure_is_atomic(invalid_sequence: int) -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    before = detector.state_for(SOURCE, BTC)
    malformed = _context(invalid_sequence)
    with pytest.raises(RangeStateError, match="exactly one"):
        detector.process(malformed)
    assert detector.state_for(SOURCE, BTC) == before


def test_series_state_is_isolated_by_source_and_symbol() -> None:
    detector = ValidatedRangeDetector()
    other = SourceId("other")
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    detector.process(_context(1, ((PivotKind.HIGH, Decimal(40), 2),), symbol=ETH))
    detector.process(_context(1, ((PivotKind.HIGH, Decimal(40), 3),), source=other))
    assert len(detector.state_for(SOURCE, BTC).pivot_history) == 1  # type: ignore[union-attr]
    assert len(detector.state_for(SOURCE, ETH).pivot_history) == 1  # type: ignore[union-attr]
    assert len(detector.state_for(other, BTC).pivot_history) == 1  # type: ignore[union-attr]


def test_fewer_than_four_is_insufficient_and_neutral_alone_is_not_a_range() -> None:
    detector = ValidatedRangeDetector()
    empty = detector.process(_context(1))
    one = detector.process(_context(2, ((PivotKind.LOW, Decimal(20), 1),)))
    assert empty.status is RangeStatus.INSUFFICIENT_DATA
    assert one.status is RangeStatus.INSUFFICIENT_DATA
    assert one.reasons == (RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS,)


@pytest.mark.parametrize(
    "order",
    [
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40)),
        ((PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20)),
    ],
)
def test_exact_alternating_four_touch_orders_validate(
    order: tuple[tuple[PivotKind, int], ...],
) -> None:
    converted = tuple((kind, Decimal(price)) for kind, price in order)
    result = _build_range(ValidatedRangeDetector(), order=converted)
    assert result.status is RangeStatus.VALID_RANGE  # type: ignore[attr-defined]
    assert result.reasons == (  # type: ignore[attr-defined]
        RangeReasonCode.RANGE_GEOMETRY_VALID,
        RangeReasonCode.REGIME_COMPATIBLE,
    )


def test_latest_four_same_kind_sequence_rejects() -> None:
    order = (
        (PivotKind.LOW, Decimal(20)),
        (PivotKind.HIGH, Decimal(40)),
        (PivotKind.HIGH, Decimal(40)),
        (PivotKind.LOW, Decimal(20)),
    )
    result = _build_range(ValidatedRangeDetector(), order=order)
    assert result.status is RangeStatus.NOT_RANGE  # type: ignore[attr-defined]
    assert result.reasons == (RangeReasonCode.PIVOTS_NOT_ALTERNATING,)  # type: ignore[attr-defined]


def test_dual_pivot_occurrence_is_excluded_from_both_sides() -> None:
    detector = ValidatedRangeDetector()
    result = detector.process(
        _context(
            1,
            (
                (PivotKind.HIGH, Decimal(40), 1),
                (PivotKind.LOW, Decimal(20), 1),
            ),
        )
    )
    assert result.status is RangeStatus.INSUFFICIENT_DATA
    assert result.qualifying_pivots == ()
    assert result.reasons == (
        RangeReasonCode.AMBIGUOUS_DUAL_PIVOT_EXCLUDED,
        RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS,
    )


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (
            ((PivotKind.LOW, 20), (PivotKind.HIGH, 38), (PivotKind.LOW, 20), (PivotKind.HIGH, 42)),
            RangeStatus.VALID_RANGE,
        ),
        (
            (
                (PivotKind.LOW, 20),
                (PivotKind.HIGH, "37.99"),
                (PivotKind.LOW, 20),
                (PivotKind.HIGH, 42),
            ),
            RangeStatus.NOT_RANGE,
        ),
        (
            ((PivotKind.LOW, 18), (PivotKind.HIGH, 40), (PivotKind.LOW, 22), (PivotKind.HIGH, 40)),
            RangeStatus.VALID_RANGE,
        ),
        (
            (
                (PivotKind.LOW, "17.99"),
                (PivotKind.HIGH, 40),
                (PivotKind.LOW, 22),
                (PivotKind.HIGH, 40),
            ),
            RangeStatus.NOT_RANGE,
        ),
    ],
)
def test_boundary_cluster_tolerance_is_inclusive(
    order: tuple[tuple[PivotKind, int | str], ...], expected: RangeStatus
) -> None:
    converted = tuple((kind, Decimal(price)) for kind, price in order)
    assert _build_range(ValidatedRangeDetector(), order=converted).status is expected  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [(20, 40, True), ("20.01", 40, False), (20, 100, True), (20, "100.01", False)],
)
def test_width_bounds_are_inclusive(lower: int | str, upper: int | str, expected: bool) -> None:
    order = (
        (PivotKind.LOW, Decimal(lower)),
        (PivotKind.HIGH, Decimal(upper)),
        (PivotKind.LOW, Decimal(lower)),
        (PivotKind.HIGH, Decimal(upper)),
    )
    result = _build_range(ValidatedRangeDetector(), order=order)
    assert result.is_valid_range is expected  # type: ignore[attr-defined]


def test_decimal_boundaries_are_arithmetic_means() -> None:
    order = (
        (PivotKind.LOW, Decimal(19)),
        (PivotKind.HIGH, Decimal(39)),
        (PivotKind.LOW, Decimal(21)),
        (PivotKind.HIGH, Decimal(41)),
    )
    result = _build_range(ValidatedRangeDetector(), order=order)
    assert (result.lower_boundary, result.upper_boundary, result.midpoint) == (  # type: ignore[attr-defined]
        Decimal(20),
        Decimal(40),
        Decimal(30),
    )


def test_age_16_is_valid_and_age_17_is_stale() -> None:
    detector = ValidatedRangeDetector()
    _build_range(detector)
    result = None
    for sequence in range(5, 21):
        result = detector.process(_context(sequence))
    assert result is not None and result.status is RangeStatus.VALID_RANGE
    assert result.latest_qualifying_pivot_age_bars == 16
    stale = detector.process(_context(21))
    assert stale.status is RangeStatus.RANGE_STALE
    assert detector.state_for(SOURCE, BTC).pivot_history == ()  # type: ignore[union-attr]


@pytest.mark.parametrize("bias", [HigherTimeframeBias.NEUTRAL, HigherTimeframeBias.MIXED])
def test_compatible_biases_and_exact_trend_strength_40_validate(
    bias: HigherTimeframeBias,
) -> None:
    detector = ValidatedRangeDetector()
    for sequence, (kind, price) in enumerate(
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20)), start=1
    ):
        detector.process(_context(sequence, ((kind, Decimal(price), sequence),)))
    result = detector.process(
        _context(4, ((PivotKind.HIGH, Decimal(40), 4),), bias=bias, trend_strength=40)
    )
    assert result.status is RangeStatus.VALID_RANGE


@pytest.mark.parametrize(
    "changes",
    [
        {"bias": HigherTimeframeBias.UP},
        {"bias": HigherTimeframeBias.DOWN},
        {"trend_strength": 60},
        {"ready": False},
        {"warmed": False},
        {"atr": None},
        {"atr": Decimal(0)},
        {"atr": Decimal(-1)},
        {"extreme": True},
    ],
)
def test_incompatible_context_invalidates_and_clears_history(
    changes: dict[str, object],
) -> None:
    detector = ValidatedRangeDetector()
    _build_range(detector)
    result = detector.process(_context(5, **changes))  # type: ignore[arg-type]
    assert result.status is RangeStatus.NOT_RANGE
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and state.active_range is None and state.pivot_history == ()


def test_temporary_directional_context_clears_pivots_but_retains_seen_identities() -> None:
    # Issue #169 prospectively supersedes the #163 pre-validation F4 reset:
    # confirmed evidence is retained, but incompatibility cannot authorize a range.
    sequence = (
        (PivotKind.LOW, Decimal(20)),
        (PivotKind.HIGH, Decimal(40)),
        (PivotKind.LOW, Decimal(20)),
        (PivotKind.HIGH, Decimal(40)),
    )
    continuous = ValidatedRangeDetector()
    interrupted = ValidatedRangeDetector()
    for index, (kind, price) in enumerate(sequence, start=1):
        context = _context(index, ((kind, price, index),))
        continuous_result = continuous.process(context)
        if index == 4:
            context = _context(index, ((kind, price, index),), bias=HigherTimeframeBias.UP)
        interrupted_result = interrupted.process(context)

    assert continuous_result.status is RangeStatus.VALID_RANGE
    assert interrupted_result.status is RangeStatus.NOT_RANGE
    assert interrupted_result.reasons == (RangeReasonCode.HTF_DIRECTIONAL,)
    state = interrupted.state_for(SOURCE, BTC)
    assert state is not None
    assert len(state.pivot_history) == 4 and state.active_range is None
    assert len(state.seen_pivot_identities) == 4

    returned = interrupted.process(_context(5))
    assert returned.status is RangeStatus.VALID_RANGE
    assert returned.latest_qualifying_pivot_age_bars == 1
    with pytest.raises(RangeStateError, match="duplicate"):
        interrupted.process(_context(6, ((PivotKind.HIGH, Decimal(40), 4),)))
    after = interrupted.state_for(SOURCE, BTC)
    assert after is not None and after.last_observed_at == _context(5).observed_at


@pytest.mark.parametrize(
    ("close", "reason"),
    [
        (Decimal("41.5"), RangeReasonCode.RANGE_BROKEN_UP),
        (Decimal("18.5"), RangeReasonCode.RANGE_BROKEN_DOWN),
    ],
)
def test_close_exactly_at_breakout_threshold_breaks_and_resets(
    close: Decimal, reason: RangeReasonCode
) -> None:
    detector = ValidatedRangeDetector()
    _build_range(detector)
    broken = detector.process(_context(5, close=close))
    assert broken.status is RangeStatus.RANGE_BROKEN
    assert broken.reasons == (reason,)
    next_bar = detector.process(_context(6))
    assert next_bar.status is RangeStatus.INSUFFICIENT_DATA
    assert next_bar.lower_boundary is None and next_bar.upper_boundary is None


def test_wick_beyond_threshold_does_not_break_range() -> None:
    detector = ValidatedRangeDetector()
    _build_range(detector)
    result = detector.process(_context(5, close=Decimal(40), high=Decimal(50)))
    assert result.status is RangeStatus.VALID_RANGE


def test_duplicate_pivot_is_rejected_without_state_mutation() -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    before = detector.state_for(SOURCE, BTC)
    with pytest.raises(RangeStateError, match="duplicate"):
        detector.process(_context(2, ((PivotKind.LOW, Decimal(20), 1),)))
    assert detector.state_for(SOURCE, BTC) == before


@pytest.mark.parametrize(
    "malformation", ["source", "symbol", "timeframe", "kind", "confirmation", "occurrence"]
)
def test_malformed_pivots_fail_closed(malformation: str) -> None:
    detector = ValidatedRangeDetector()
    context = _context(1, ((PivotKind.LOW, Decimal(20), 1),))
    pivot = context.structure_15m.newly_confirmed[0]
    changes_by_malformation: dict[str, dict[str, object]] = {
        "source": {"source_id": SourceId("wrong")},
        "symbol": {"symbol": ETH},
        "timeframe": {"timeframe": Timeframe.HOUR_1},
        "kind": {"kind": "LOW"},
        "confirmation": {"confirmed_at": context.observed_at - timedelta(minutes=1)},
        "occurrence": {"pivot_open_time": context.observed_at},
    }
    changes = changes_by_malformation[malformation]
    malformed = replace(pivot, **changes)  # type: ignore[arg-type]
    structure = replace(
        context.structure_15m,
        newly_confirmed=(malformed,),
        latest_confirmed_low=None,
    )
    with pytest.raises(RangeStateError):
        detector.process(replace(context, structure_15m=structure))
    assert detector.state_for(SOURCE, BTC) is None


def test_reason_order_and_decimal_context_are_deterministic() -> None:
    context = _context(
        1,
        ready=False,
        warmed=False,
        atr=None,
        extreme=True,
        bias=HigherTimeframeBias.UP,
        trend_strength=60,
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low = ValidatedRangeDetector().process(context)
        setcontext(Context(prec=50))
        high = ValidatedRangeDetector().process(context)
    finally:
        setcontext(original)
    assert low == high
    assert low.reasons == (
        RangeReasonCode.CONTEXT_NOT_READY,
        RangeReasonCode.FEATURES_NOT_WARMED,
        RangeReasonCode.ATR_MISSING,
        RangeReasonCode.EXTREME_VOLATILITY,
        RangeReasonCode.HTF_DIRECTIONAL,
        RangeReasonCode.ENTRY_TREND_TOO_STRONG,
    )


def test_usable_history_is_bounded() -> None:
    detector = ValidatedRangeDetector()
    for sequence in range(1, 14):
        kind = PivotKind.LOW if sequence % 2 else PivotKind.HIGH
        detector.process(
            _context(
                sequence,
                ((kind, Decimal(20 + 20 * (sequence % 2 == 0)), sequence),),
                bias=HigherTimeframeBias.UP,
            )
        )
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and state.active_range is None and len(state.pivot_history) == 12


@pytest.mark.parametrize(
    "soft",
    [
        {"bias": HigherTimeframeBias.UP},
        {"bias": HigherTimeframeBias.DOWN},
        {"trend_strength": 41},
        {"bias": HigherTimeframeBias.UP, "trend_strength": 41},
    ],
)
def test_prevalidation_soft_interruption_retains_once_and_recovers(
    soft: dict[str, object],
) -> None:
    detector = ValidatedRangeDetector()
    order = ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20))
    for sequence, (kind, price) in enumerate(order, start=1):
        detector.process(_context(sequence, ((kind, Decimal(price), sequence),)))
    fourth = detector.process(
        _context(4, ((PivotKind.HIGH, Decimal(40), 4),), **soft)  # type: ignore[arg-type]
    )
    assert fourth.status is RangeStatus.NOT_RANGE
    assert not fourth.is_valid_range and fourth.partial_evidence_retained
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and state.active_range is None
    assert len(state.pivot_history) == len(state.seen_pivot_identities) == 4
    assert tuple(item.confirmation_sequence for item in state.pivot_history) == (1, 2, 3, 4)
    recovered = detector.process(_context(5))
    assert recovered.status is RangeStatus.VALID_RANGE
    assert recovered.latest_qualifying_pivot_age_bars == 1


@pytest.mark.parametrize(
    "hard",
    [{"ready": False}, {"warmed": False}, {"atr": None}, {"atr": Decimal(0)}, {"extreme": True}],
)
def test_hard_failure_clears_partial_even_with_directional_reason(
    hard: dict[str, object],
) -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    result = detector.process(
        _context(2, ((PivotKind.HIGH, Decimal(40), 2),), bias=HigherTimeframeBias.UP, **hard)  # type: ignore[arg-type]
    )
    assert result.status is RangeStatus.NOT_RANGE
    assert result.partial_evidence_reset and not result.partial_evidence_retained
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and state.pivot_history == ()
    assert len(state.seen_pivot_identities) == 2
    assert detector.process(_context(3)).status is RangeStatus.INSUFFICIENT_DATA


def test_partial_inactivity_age_16_and_17_and_new_pivot_after_expiry() -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    for sequence in range(2, 18):
        result = detector.process(_context(sequence, bias=HigherTimeframeBias.UP))
    assert result.partial_evidence_retained
    assert len(detector.state_for(SOURCE, BTC).pivot_history) == 1  # type: ignore[union-attr]
    expired = detector.process(
        _context(18, ((PivotKind.HIGH, Decimal(40), 18),), bias=HigherTimeframeBias.UP)
    )
    assert expired.partial_evidence_reset and expired.partial_evidence_retained
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and len(state.pivot_history) == 1
    assert state.pivot_history[0].confirmation_sequence == 18
    assert len(state.seen_pivot_identities) == 2
    assert detector.process(_context(19)).status is RangeStatus.INSUFFICIENT_DATA


@pytest.mark.parametrize("return_sequence", [20, 21])
def test_complete_partial_box_age_boundary_cannot_be_renewed_by_compatibility(
    return_sequence: int,
) -> None:
    detector = ValidatedRangeDetector()
    for sequence, (kind, price) in enumerate(
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40)),
        start=1,
    ):
        detector.process(
            _context(sequence, ((kind, Decimal(price), sequence),), bias=HigherTimeframeBias.UP)
        )
    for sequence in range(5, return_sequence):
        detector.process(_context(sequence, bias=HigherTimeframeBias.UP))
    returned = detector.process(_context(return_sequence))
    if return_sequence == 20:
        assert returned.status is RangeStatus.VALID_RANGE
        assert returned.latest_qualifying_pivot_age_bars == 16
    else:
        assert returned.status is RangeStatus.INSUFFICIENT_DATA
        assert returned.partial_evidence_reset
        assert detector.state_for(SOURCE, BTC).pivot_history == ()  # type: ignore[union-attr]


def test_unknown_htf_is_not_soft_eligible() -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    result = detector.process(_context(2, bias=HigherTimeframeBias.UNKNOWN))
    assert result.reasons == (RangeReasonCode.HTF_DIRECTIONAL,)
    assert result.partial_evidence_reset
    assert detector.state_for(SOURCE, BTC).pivot_history == ()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("break_close", "reason"),
    [
        (Decimal("41.5"), RangeReasonCode.RANGE_BROKEN_UP),
        (Decimal("18.5"), RangeReasonCode.RANGE_BROKEN_DOWN),
    ],
)
def test_complete_partial_box_breaks_at_exact_closed_threshold_before_recovery(
    break_close: Decimal, reason: RangeReasonCode
) -> None:
    detector = ValidatedRangeDetector()
    order = ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40))
    for sequence, (kind, price) in enumerate(order, start=1):
        detector.process(
            _context(sequence, ((kind, Decimal(price), sequence),), bias=HigherTimeframeBias.UP)
        )
    # Wick alone has not broken the unvalidated box.
    still_retained = detector.process(
        _context(5, close=Decimal(30), high=Decimal(50), bias=HigherTimeframeBias.UP)
    )
    assert still_retained.partial_evidence_retained
    broken = detector.process(_context(6, close=break_close, bias=HigherTimeframeBias.UP))
    assert broken.status is RangeStatus.RANGE_BROKEN
    assert broken.reasons == (reason,)
    assert broken.partial_evidence_reset
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and state.pivot_history == ()
    assert detector.process(_context(7)).status is RangeStatus.INSUFFICIENT_DATA


def test_previous_complete_box_break_cannot_be_hidden_by_new_pivot() -> None:
    detector = ValidatedRangeDetector()
    for sequence, (kind, price) in enumerate(
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40)),
        start=1,
    ):
        detector.process(
            _context(sequence, ((kind, Decimal(price), sequence),), bias=HigherTimeframeBias.UP)
        )
    broken = detector.process(
        _context(
            5,
            ((PivotKind.LOW, Decimal(30), 5),),
            close=Decimal("41.5"),
            bias=HigherTimeframeBias.UP,
        )
    )
    assert broken.status is RangeStatus.RANGE_BROKEN
    assert detector.state_for(SOURCE, BTC).pivot_history == ()  # type: ignore[union-attr]
    assert len(detector.state_for(SOURCE, BTC).seen_pivot_identities) == 5  # type: ignore[union-attr]


def test_first_validation_does_not_publish_broken_unvalidated_box() -> None:
    detector = ValidatedRangeDetector()
    for sequence, (kind, price) in enumerate(
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20)), start=1
    ):
        detector.process(_context(sequence, ((kind, Decimal(price), sequence),)))
    result = detector.process(
        _context(4, ((PivotKind.HIGH, Decimal(40), 4),), close=Decimal("41.5"))
    )
    assert result.status is RangeStatus.RANGE_BROKEN
    assert result.partial_evidence_reset
    assert detector.process(_context(5)).status is RangeStatus.INSUFFICIENT_DATA


def test_recovery_bar_cannot_validate_previously_retained_box_after_close_break() -> None:
    detector = ValidatedRangeDetector()
    for sequence, (kind, price) in enumerate(
        ((PivotKind.LOW, 20), (PivotKind.HIGH, 40), (PivotKind.LOW, 20), (PivotKind.HIGH, 40)),
        start=1,
    ):
        detector.process(
            _context(sequence, ((kind, Decimal(price), sequence),), bias=HigherTimeframeBias.UP)
        )
    result = detector.process(_context(5, close=Decimal("18.5")))
    assert result.status is RangeStatus.RANGE_BROKEN
    assert result.reasons == (RangeReasonCode.RANGE_BROKEN_DOWN,)
    assert detector.process(_context(6)).status is RangeStatus.INSUFFICIENT_DATA


def test_ambiguous_occurrence_and_duplicate_survive_retention_and_reset() -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),), bias=HigherTimeframeBias.UP))
    detector.process(
        _context(
            2,
            ((PivotKind.HIGH, Decimal(40), 2), (PivotKind.LOW, Decimal(20), 2)),
            bias=HigherTimeframeBias.UP,
        )
    )
    state = detector.state_for(SOURCE, BTC)
    assert state is not None and len(state.pivot_history) == 1
    assert len(state.seen_pivot_identities) == 3
    detector.process(_context(3, ready=False))
    before = detector.state_for(SOURCE, BTC)
    with pytest.raises(RangeStateError, match="duplicate"):
        detector.process(_context(4, ((PivotKind.HIGH, Decimal(40), 2),)))
    assert detector.state_for(SOURCE, BTC) == before


def test_mutated_detector_policy_fails_before_state_commit() -> None:
    detector = ValidatedRangeDetector()
    detector.process(_context(1, ((PivotKind.LOW, Decimal(20), 1),)))
    before = detector.state_for(SOURCE, BTC)
    object.__setattr__(detector.configuration, "partial_evidence_policy_version", "forged")
    with pytest.raises(ValueError, match="unsupported range partial-evidence policy"):
        detector.process(_context(2))
    assert detector.state_for(SOURCE, BTC) == before
