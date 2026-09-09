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
    BreakoutRetestConfiguration,
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    BreakoutRetestStateError,
    BreakoutRetestStrategy,
    BreakoutSide,
    CandidateAction,
    Strategy,
    StrategyContextError,
    StrategyEvaluationContext,
)

START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE_A = SourceId("archive-a")
SOURCE_B = SourceId("archive-b")


def technical(
    source_id: SourceId,
    symbol: TradingSymbol,
    timeframe: Timeframe,
    observed_at: datetime,
    direction: Direction,
) -> TechnicalRegimeSnapshot:
    score = {Direction.UP: 40, Direction.DOWN: -40, Direction.NEUTRAL: 0}[direction]
    contributions = {
        40: (20, 20, 0, 0, 0),
        -40: (-20, -20, 0, 0, 0),
        0: (0, 0, 0, 0, 0),
    }[score]
    return TechnicalRegimeSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        bars_seen=50,
        direction=direction,
        direction_score=score,
        trend_strength=abs(score),
        direction_evidence=tuple(
            DirectionEvidence(component, contribution, "test")
            for component, contribution in zip(DirectionComponent, contributions, strict=True)
        ),
        volatility_percentile=Decimal(50),
        volatility_state=VolatilityState.NORMAL,
        volume_state=VolumeState.NORMAL,
        liquidity_state=LiquidityState.UNKNOWN,
        event_risk_state=EventRiskState.UNKNOWN,
        regime_label=RegimeLabel.UPTREND if direction is Direction.UP else RegimeLabel.DOWNTREND,
        is_fully_classified=True,
    )


def strategy_context(
    *,
    observed_at: datetime = START,
    side: BreakoutSide = BreakoutSide.LONG,
    source_id: SourceId = SOURCE_A,
    symbol: TradingSymbol = BTC,
    candle_open: Decimal | None = None,
    candle_high: Decimal | None = None,
    candle_low: Decimal | None = None,
    candle_close: Decimal | None = None,
    atr: Decimal | None = Decimal(10),
    momentum: Decimal | None = None,
    rsi: Decimal | None = Decimal(50),
    volume_zscore: Decimal | None = Decimal("0.50"),
    ema_20: Decimal | None = None,
    ema_50: Decimal | None = Decimal(100),
    ready: bool = True,
    warmed: bool = True,
    extreme: bool = False,
    bias: HigherTimeframeBias | None = None,
    entry_direction: Direction | None = None,
    pivot_present: bool = True,
    pivot_kind: PivotKind | None = None,
    pivot_price: Decimal = Decimal(100),
) -> StrategyEvaluationContext:
    long = side is BreakoutSide.LONG
    expected_direction = Direction.UP if long else Direction.DOWN
    effective_bias = bias or (HigherTimeframeBias.UP if long else HigherTimeframeBias.DOWN)
    candle_open = candle_open if candle_open is not None else Decimal(100)
    candle_close = candle_close if candle_close is not None else Decimal(101 if long else 99)
    candle_high = candle_high if candle_high is not None else max(candle_open, candle_close) + 2
    candle_low = candle_low if candle_low is not None else min(candle_open, candle_close) - 2
    momentum = momentum if momentum is not None else Decimal("0.01" if long else "-0.01")
    ema_20 = ema_20 if ema_20 is not None else Decimal(102 if long else 98)
    candle = Candle(
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        open_time=observed_at - Timeframe.MINUTES_15.duration,
        close_time=observed_at,
        open=candle_open,
        high=candle_high,
        low=candle_low,
        close=candle_close,
        volume=Decimal(20),
    )
    feature = FeatureSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        observed_at=observed_at,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        bars_seen=50,
        ema_20=ema_20,
        ema_50=ema_50,
        atr_14=atr,
        rsi_14=rsi,
        vwap_utc_day=Decimal(100),
        return_1=Decimal(0),
        momentum_4=momentum,
        momentum_16=momentum,
        volume_zscore_20=volume_zscore,
        realized_volatility_20=Decimal("0.01"),
        is_fully_warmed=warmed,
    )
    effective_kind = pivot_kind or (PivotKind.HIGH if long else PivotKind.LOW)
    pivot = (
        ConfirmedPivot(
            source_id=source_id,
            symbol=symbol,
            timeframe=Timeframe.MINUTES_15,
            kind=effective_kind,
            pivot_open_time=observed_at - timedelta(hours=1),
            pivot_close_time=observed_at - timedelta(minutes=45),
            pivot_price=pivot_price,
            confirmed_at=observed_at - timedelta(minutes=15),
            left_bars=2,
            right_bars=2,
            pivot_bar_number=45,
            relation=None,
        )
        if pivot_present
        else None
    )
    structure = StructureSnapshot(
        source_id=source_id,
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        observed_at=observed_at,
        bars_seen=50,
        newly_confirmed=(),
        latest_confirmed_high=pivot if long else None,
        latest_confirmed_low=None if long else pivot,
        latest_high_relation=None,
        latest_low_relation=None,
    )
    entry = entry_direction or expected_direction
    regime_15m = technical(source_id, symbol, Timeframe.MINUTES_15, observed_at, entry)
    regime_1h = technical(source_id, symbol, Timeframe.HOUR_1, observed_at, expected_direction)
    regime_4h = technical(source_id, symbol, Timeframe.HOURS_4, observed_at, expected_direction)
    regime = MultiTimeframeRegimeSnapshot(
        source_id=source_id,
        symbol=symbol,
        observed_at=observed_at,
        regime_15m=regime_15m,
        regime_1h=regime_1h,
        regime_4h=regime_4h,
        age_1h=timedelta(),
        age_4h=timedelta(),
        is_1h_fresh=True,
        is_4h_fresh=True,
        is_fully_ready=ready,
        higher_timeframe_bias=effective_bias,
        alignment=MultiTimeframeAlignment.ALIGNED_UP
        if long
        else MultiTimeframeAlignment.ALIGNED_DOWN,
        has_extreme_volatility=extreme,
        extreme_volatility_timeframes=(Timeframe.MINUTES_15,) if extreme else (),
        readiness_reasons=() if ready else ("not ready",),
    )
    return StrategyEvaluationContext(
        source_id, symbol, observed_at, candle, feature, structure, regime
    )


def retest_context(
    side: BreakoutSide,
    *,
    observed_at: datetime = START + timedelta(minutes=15),
    source_id: SourceId = SOURCE_A,
    symbol: TradingSymbol = BTC,
    **changes: object,
) -> StrategyEvaluationContext:
    defaults: dict[str, object]
    if side is BreakoutSide.LONG:
        defaults = {
            "candle_open": Decimal(99),
            "candle_high": Decimal(102),
            "candle_low": Decimal(98),
            "candle_close": Decimal(101),
            "volume_zscore": Decimal(0),
        }
    else:
        defaults = {
            "candle_open": Decimal(101),
            "candle_high": Decimal(102),
            "candle_low": Decimal(98),
            "candle_close": Decimal(99),
            "volume_zscore": Decimal(0),
        }
    defaults.update(changes)
    return strategy_context(
        observed_at=observed_at,
        side=side,
        source_id=source_id,
        symbol=symbol,
        **defaults,  # type: ignore[arg-type]
    )


def waiting_context(
    side: BreakoutSide,
    *,
    observed_at: datetime = START + timedelta(minutes=15),
) -> StrategyEvaluationContext:
    if side is BreakoutSide.LONG:
        return retest_context(
            side,
            observed_at=observed_at,
            candle_open=Decimal(103),
            candle_high=Decimal(105),
            candle_low=Decimal(103),
            candle_close=Decimal(104),
        )
    return retest_context(
        side,
        observed_at=observed_at,
        candle_open=Decimal(97),
        candle_high=Decimal(97),
        candle_low=Decimal(95),
        candle_close=Decimal(96),
    )


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_breakout_arms_but_never_emits_candidate_on_same_bar(side: BreakoutSide) -> None:
    strategy: Strategy[BreakoutRetestReasonCode, BreakoutRetestRuleAudit] = BreakoutRetestStrategy()

    result = strategy.evaluate(strategy_context(side=side))

    assert result.action is CandidateAction.NO_TRADE
    assert result.reference_price is None
    assert result.invalidation_reference_price is None
    assert result.reasons_for[-1] is BreakoutRetestReasonCode.BREAKOUT_ARMED
    assert result.rule_audit.bars_waited == 0


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_breakout_exact_buffer_volume_and_rsi_boundaries_arm(side: BreakoutSide) -> None:
    strategy = BreakoutRetestStrategy()

    result = strategy.evaluate(strategy_context(side=side))

    assert result.reasons_against == ()
    assert strategy.armed_state(SOURCE_A, BTC) is not None
    assert result.rule_audit.required_breakout_close == Decimal(
        101 if side is BreakoutSide.LONG else 99
    )


@pytest.mark.parametrize(
    ("side", "close"),
    [(BreakoutSide.LONG, Decimal("100.99")), (BreakoutSide.SHORT, Decimal("99.01"))],
)
def test_breakout_just_inside_buffer_is_rejected(side: BreakoutSide, close: Decimal) -> None:
    strategy = BreakoutRetestStrategy()

    result = strategy.evaluate(strategy_context(side=side, candle_close=close))

    assert BreakoutRetestReasonCode.BREAKOUT_BUFFER_FAILED in result.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is None


def test_breakout_volume_below_threshold_is_rejected() -> None:
    strategy = BreakoutRetestStrategy()
    result = strategy.evaluate(strategy_context(volume_zscore=Decimal("0.49")))

    assert result.reasons_against[-1] is BreakoutRetestReasonCode.BREAKOUT_VOLUME_FAILED
    assert strategy.armed_state(SOURCE_A, BTC) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"pivot_present": False}, BreakoutRetestReasonCode.MISSING_BREAKOUT_PIVOT),
        ({"ema_20": Decimal(100)}, BreakoutRetestReasonCode.EMA_ALIGNMENT_FAILED),
        (
            {"candle_open": Decimal(101), "candle_close": Decimal(101)},
            BreakoutRetestReasonCode.CANDLE_DIRECTION_FAILED,
        ),
        ({"momentum": Decimal(0)}, BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION_FAILED),
        ({"rsi": Decimal("49.99")}, BreakoutRetestReasonCode.RSI_CONFIRMATION_FAILED),
        (
            {"bias": HigherTimeframeBias.MIXED},
            BreakoutRetestReasonCode.HTF_BIAS_NOT_DIRECTIONAL,
        ),
    ],
)
def test_arming_gates_fail_closed(
    changes: dict[str, object], reason: BreakoutRetestReasonCode
) -> None:
    result = BreakoutRetestStrategy().evaluate(
        strategy_context(**changes)  # type: ignore[arg-type]
    )

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


@pytest.mark.parametrize(
    ("field_name", "reason"),
    [
        ("ema_20", BreakoutRetestReasonCode.MISSING_EMA_20),
        ("ema_50", BreakoutRetestReasonCode.MISSING_EMA_50),
        ("atr_14", BreakoutRetestReasonCode.MISSING_ATR_14),
        ("rsi_14", BreakoutRetestReasonCode.MISSING_RSI_14),
        ("momentum_4", BreakoutRetestReasonCode.MISSING_MOMENTUM_4),
        ("volume_zscore_20", BreakoutRetestReasonCode.MISSING_VOLUME_ZSCORE_20),
    ],
)
def test_missing_common_feature_fails_closed_in_canonical_order(
    field_name: str, reason: BreakoutRetestReasonCode
) -> None:
    context = strategy_context()
    feature = replace(
        context.features_15m,
        **{field_name: None},  # type: ignore[arg-type]
    )

    result = BreakoutRetestStrategy().evaluate(replace(context, features_15m=feature))

    assert result.action is CandidateAction.NO_TRADE
    assert reason in result.reasons_against


@pytest.mark.parametrize("atr", [Decimal(0), Decimal(-1)])
def test_nonpositive_atr_fails_closed(atr: Decimal) -> None:
    context = strategy_context(atr=atr)

    result = BreakoutRetestStrategy().evaluate(context)

    assert BreakoutRetestReasonCode.ATR_NOT_POSITIVE in result.reasons_against


@pytest.mark.parametrize(
    ("side", "wrong_kind"),
    [(BreakoutSide.LONG, PivotKind.LOW), (BreakoutSide.SHORT, PivotKind.HIGH)],
)
def test_wrong_kind_breakout_pivot_fails_at_context_boundary(
    side: BreakoutSide, wrong_kind: PivotKind
) -> None:
    with pytest.raises(StrategyContextError, match="pivot identity, kind, or causality"):
        strategy_context(side=side, pivot_kind=wrong_kind)


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_first_post_breakout_bar_is_wait_one_and_boundary_retest_candidates(
    side: BreakoutSide,
) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))

    result = strategy.evaluate(retest_context(side))

    assert result.action is (
        CandidateAction.LONG_CANDIDATE
        if side is BreakoutSide.LONG
        else CandidateAction.SHORT_CANDIDATE
    )
    assert result.rule_audit.bars_waited == 1
    assert result.rule_audit.retest_extreme == Decimal(98 if side is BreakoutSide.LONG else 102)
    assert result.reference_price == Decimal(101 if side is BreakoutSide.LONG else 99)
    assert result.invalidation_reference_price == Decimal(98 if side is BreakoutSide.LONG else 102)
    assert strategy.armed_state(SOURCE_A, BTC) is None


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_too_deep_retest_cancels(side: BreakoutSide) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))
    change = (
        {"candle_low": Decimal("97.99"), "volume_zscore": Decimal("0.50")}
        if side is BreakoutSide.LONG
        else {"candle_high": Decimal("102.01"), "volume_zscore": Decimal("0.50")}
    )

    result = strategy.evaluate(retest_context(side, **change))  # type: ignore[arg-type]

    assert result.reasons_against == (BreakoutRetestReasonCode.RETEST_TOO_DEEP,)
    assert strategy.armed_state(SOURCE_A, BTC) is None


def test_deep_retest_precedes_trend_loss_and_cancels_once() -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())

    result = strategy.evaluate(
        retest_context(
            BreakoutSide.LONG,
            candle_low=Decimal("97.99"),
            bias=HigherTimeframeBias.MIXED,
        )
    )

    assert result.reasons_against == (BreakoutRetestReasonCode.RETEST_TOO_DEEP,)
    assert strategy.armed_state(SOURCE_A, BTC) is None


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_no_touch_keeps_state_armed(side: BreakoutSide) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))

    result = strategy.evaluate(waiting_context(side))

    assert result.reasons_for == (BreakoutRetestReasonCode.WAITING_FOR_RETEST,)
    assert strategy.armed_state(SOURCE_A, BTC) is not None


def test_touched_without_reclaim_stays_armed_and_later_reclaim_can_emit() -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())
    failed = strategy.evaluate(
        retest_context(
            BreakoutSide.LONG,
            candle_open=Decimal(99),
            candle_close=Decimal(100),
        )
    )

    assert BreakoutRetestReasonCode.RECLAIM_FAILED in failed.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is not None
    candidate = strategy.evaluate(
        retest_context(
            BreakoutSide.LONG,
            observed_at=START + timedelta(minutes=30),
        )
    )
    assert candidate.action is CandidateAction.LONG_CANDIDATE


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"entry_direction": Direction.DOWN}, BreakoutRetestReasonCode.ENTRY_DIRECTION_OPPOSED),
        ({"ema_20": Decimal(100)}, BreakoutRetestReasonCode.EMA_ALIGNMENT_FAILED),
    ],
)
def test_touched_entry_confirmation_failure_keeps_state_armed(
    changes: dict[str, object], reason: BreakoutRetestReasonCode
) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())

    result = strategy.evaluate(
        retest_context(BreakoutSide.LONG, **changes)  # type: ignore[arg-type]
    )

    assert reason in result.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is not None


def test_later_pivot_does_not_move_locked_breakout_level() -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(pivot_price=Decimal(100)))

    result = strategy.evaluate(retest_context(BreakoutSide.LONG, pivot_price=Decimal(110)))

    assert result.action is CandidateAction.LONG_CANDIDATE
    assert result.rule_audit.breakout_level == Decimal(100)
    assert result.rule_audit.breakout_pivot_price == Decimal(100)
    assert result.rule_audit.current_pivot_price == Decimal(110)


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_negative_retest_volume_and_zero_momentum_fail_without_corrupting_state(
    side: BreakoutSide,
) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))
    first = strategy.evaluate(retest_context(side, volume_zscore=Decimal("-0.01")))

    assert BreakoutRetestReasonCode.VOLUME_CONFIRMATION_FAILED in first.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is not None
    second = strategy.evaluate(
        retest_context(
            side,
            observed_at=START + timedelta(minutes=30),
            momentum=Decimal(0),
        )
    )
    assert BreakoutRetestReasonCode.MOMENTUM_CONFIRMATION_FAILED in second.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is not None


@pytest.mark.parametrize(
    ("side", "close", "expected"),
    [
        (BreakoutSide.LONG, Decimal(128), CandidateAction.LONG_CANDIDATE),
        (BreakoutSide.SHORT, Decimal(72), CandidateAction.SHORT_CANDIDATE),
    ],
)
def test_invalidation_distance_exactly_three_atr_is_accepted(
    side: BreakoutSide, close: Decimal, expected: CandidateAction
) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))
    open_price = close - 1 if side is BreakoutSide.LONG else close + 1
    range_change = (
        {"candle_high": close + 1} if side is BreakoutSide.LONG else {"candle_low": close - 1}
    )

    result = strategy.evaluate(
        retest_context(
            side,
            candle_open=open_price,
            candle_close=close,
            **range_change,  # type: ignore[arg-type]
        )
    )

    assert result.action is expected
    assert result.rule_audit.invalidation_distance == Decimal(30)


@pytest.mark.parametrize(
    ("side", "close"),
    [(BreakoutSide.LONG, Decimal("128.01")), (BreakoutSide.SHORT, Decimal("71.99"))],
)
def test_invalidation_over_three_atr_consumes_setup(side: BreakoutSide, close: Decimal) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context(side=side))
    open_price = close - 1 if side is BreakoutSide.LONG else close + 1
    range_change = (
        {"candle_high": close + 1} if side is BreakoutSide.LONG else {"candle_low": close - 1}
    )

    result = strategy.evaluate(
        retest_context(
            side,
            candle_open=open_price,
            candle_close=close,
            **range_change,  # type: ignore[arg-type]
        )
    )

    assert BreakoutRetestReasonCode.INVALIDATION_TOO_WIDE in result.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is None


def test_candidate_clear_prevents_duplicate_candidate_on_next_bar() -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())
    assert (
        strategy.evaluate(retest_context(BreakoutSide.LONG)).action
        is CandidateAction.LONG_CANDIDATE
    )

    next_result = strategy.evaluate(
        retest_context(
            BreakoutSide.LONG,
            observed_at=START + timedelta(minutes=30),
            volume_zscore=Decimal(0),
        )
    )
    assert next_result.action is CandidateAction.NO_TRADE


def test_setup_expires_after_window_without_same_bar_rearming() -> None:
    strategy = BreakoutRetestStrategy(BreakoutRetestConfiguration(max_retest_wait_bars=1))
    strategy.evaluate(strategy_context())
    strategy.evaluate(waiting_context(BreakoutSide.LONG))

    result = strategy.evaluate(strategy_context(observed_at=START + timedelta(minutes=30)))

    assert result.reasons_against == (BreakoutRetestReasonCode.SETUP_EXPIRED,)
    assert strategy.armed_state(SOURCE_A, BTC) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"bias": HigherTimeframeBias.MIXED}, BreakoutRetestReasonCode.HTF_TREND_LOST),
        ({"extreme": True}, BreakoutRetestReasonCode.EXTREME_VOLATILITY),
        ({"ready": False}, BreakoutRetestReasonCode.CONTEXT_NOT_READY),
    ],
)
def test_armed_context_loss_cancels_without_rearming(
    changes: dict[str, object], reason: BreakoutRetestReasonCode
) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())

    result = strategy.evaluate(
        strategy_context(
            observed_at=START + timedelta(minutes=15),
            **changes,  # type: ignore[arg-type]
        )
    )

    assert reason in result.reasons_against
    assert strategy.armed_state(SOURCE_A, BTC) is None


@pytest.mark.parametrize(
    "invalid_time",
    [START, START - timedelta(minutes=15), START + timedelta(minutes=30)],
)
def test_invalid_progression_raises_without_mutating_state(invalid_time: datetime) -> None:
    strategy = BreakoutRetestStrategy()
    strategy.evaluate(strategy_context())
    before = strategy.armed_state(SOURCE_A, BTC)

    with pytest.raises(BreakoutRetestStateError, match="exactly one 15m"):
        strategy.evaluate(strategy_context(observed_at=invalid_time))

    assert strategy.armed_state(SOURCE_A, BTC) == before


def test_symbol_and_source_states_are_isolated() -> None:
    strategy = BreakoutRetestStrategy()
    identities = ((SOURCE_A, BTC), (SOURCE_A, ETH), (SOURCE_B, BTC))
    for source_id, symbol in identities:
        strategy.evaluate(strategy_context(source_id=source_id, symbol=symbol))

    results = tuple(
        strategy.evaluate(retest_context(BreakoutSide.LONG, source_id=source_id, symbol=symbol))
        for source_id, symbol in identities
    )

    assert all(result.action is CandidateAction.LONG_CANDIDATE for result in results)
    assert all(strategy.armed_state(source_id, symbol) is None for source_id, symbol in identities)


def test_repeated_stream_and_ambient_decimal_context_are_deterministic() -> None:
    stream = (
        strategy_context(),
        waiting_context(BreakoutSide.LONG),
        retest_context(BreakoutSide.LONG, observed_at=START + timedelta(minutes=30)),
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        first_strategy = BreakoutRetestStrategy()
        first = tuple(first_strategy.evaluate(item) for item in stream)
        setcontext(Context(prec=50))
        second_strategy = BreakoutRetestStrategy()
        second = tuple(second_strategy.evaluate(item) for item in stream)
    finally:
        setcontext(original)

    assert first == second


def test_no_trade_reason_order_is_stable() -> None:
    context = strategy_context(ready=False, warmed=False, extreme=True)
    feature = replace(
        context.features_15m,
        ema_20=None,
        atr_14=None,
        momentum_4=None,
    )

    result = BreakoutRetestStrategy().evaluate(replace(context, features_15m=feature))

    assert result.reasons_against[:6] == (
        BreakoutRetestReasonCode.CONTEXT_NOT_READY,
        BreakoutRetestReasonCode.FEATURES_NOT_FULLY_WARMED,
        BreakoutRetestReasonCode.MISSING_EMA_20,
        BreakoutRetestReasonCode.MISSING_ATR_14,
        BreakoutRetestReasonCode.MISSING_MOMENTUM_4,
        BreakoutRetestReasonCode.EXTREME_VOLATILITY,
    )


def test_configuration_defaults_are_exact_and_reject_invalid_types() -> None:
    assert BreakoutRetestConfiguration() == BreakoutRetestConfiguration(
        entry_timeframe=Timeframe.MINUTES_15,
        breakout_buffer_atr=Decimal("0.10"),
        retest_tolerance_atr=Decimal("0.20"),
        max_retest_wait_bars=8,
        minimum_breakout_volume_zscore=Decimal("0.50"),
        minimum_retest_volume_zscore=Decimal(0),
        long_min_rsi=Decimal(50),
        short_max_rsi=Decimal(50),
        max_invalidation_distance_atr=Decimal("3.0"),
    )
    with pytest.raises(TypeError, match="Decimal"):
        BreakoutRetestConfiguration(breakout_buffer_atr=0.1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        BreakoutRetestConfiguration(max_retest_wait_bars=True)
