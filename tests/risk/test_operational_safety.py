import ast
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext
from pathlib import Path

import pytest

import adaptive_trading_bot.risk.safety as safety_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    OperationalSafetyConfiguration,
    OperationalSafetyDecision,
    OperationalSafetyGate,
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecision,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
    RiskReasonCode,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestReasonCode,
    CandidateAction,
    RangeMeanReversionReasonCode,
    StrategyEvaluation,
    StrategyReasonCode,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive")


def _evaluation(
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    *,
    reason: object = StrategyReasonCode.CONTEXT_READY,
) -> StrategyEvaluation[object, object]:
    candidate = action is not CandidateAction.NO_TRADE
    return StrategyEvaluation(
        strategy_id="test_strategy",
        strategy_version="1.0.0",
        source_id=SOURCE,
        symbol=BTC,
        observed_at=NOW,
        action=action,
        reference_price=Decimal(100) if candidate else None,
        invalidation_reference_price=Decimal(90) if candidate else None,
        reasons_for=(reason,),
        reasons_against=(),
        rule_audit={"opaque": str(reason)},
    )


def _risk_decision(
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    *,
    equity: Decimal = Decimal(10000),
    day_start: Decimal = Decimal(10000),
    peak: Decimal = Decimal(10000),
    reason: object = StrategyReasonCode.CONTEXT_READY,
) -> RiskDecision:
    return RiskManager().evaluate(
        _evaluation(action, reason=reason),
        RiskPortfolioSnapshot(NOW, "USDT", equity, day_start, peak),
    )


def _health(**changes: object) -> OperationalHealthSnapshot:
    values: dict[str, object] = {
        "source_id": SOURCE,
        "symbol": BTC,
        "observed_at": NOW,
        "market_data_observed_at": NOW - timedelta(seconds=120),
        "feed_integrity_state": FeedIntegrityState.OK,
        "exchange_api_health_state": ExchangeApiHealthState.HEALTHY,
        "spread_bps": Decimal("10.0"),
        "time_sync_offset_ms": Decimal("1000"),
        "volatility_safety_state": VolatilitySafetyState.CLASSIFIED_NORMAL,
        "suspicious_price_anomaly": False,
        "consecutive_order_rejections": 2,
    }
    values.update(changes)
    return OperationalHealthSnapshot(**values)  # type: ignore[arg-type]


def _evaluate(
    risk_decision: RiskDecision | None = None,
    health: OperationalHealthSnapshot | None = None,
) -> OperationalSafetyDecision:
    return OperationalSafetyGate().evaluate(
        _risk_decision() if risk_decision is None else risk_decision,
        _health() if health is None else health,
    )


def test_configuration_defaults_are_exact_and_versioned() -> None:
    configuration = OperationalSafetyConfiguration()

    assert configuration == OperationalSafetyConfiguration(
        max_market_data_age_seconds=120,
        max_spread_bps=Decimal("10.0"),
        max_abs_time_sync_offset_ms=Decimal("1000"),
        max_consecutive_order_rejections=3,
    )
    assert configuration.safety_policy_version == "1.0.0"


@pytest.mark.parametrize(
    ("field_name", "value", "exception"),
    [
        ("max_market_data_age_seconds", True, TypeError),
        ("max_market_data_age_seconds", -1, ValueError),
        ("max_consecutive_order_rejections", False, TypeError),
        ("max_consecutive_order_rejections", 0, ValueError),
        ("max_spread_bps", 10.0, TypeError),
        ("max_spread_bps", Decimal("NaN"), ValueError),
        ("max_spread_bps", Decimal(-1), ValueError),
        ("max_abs_time_sync_offset_ms", 1000.0, TypeError),
        ("max_abs_time_sync_offset_ms", Decimal("Infinity"), ValueError),
        ("max_abs_time_sync_offset_ms", Decimal(-1), ValueError),
    ],
)
def test_configuration_rejects_invalid_thresholds(
    field_name: str, value: object, exception: type[Exception]
) -> None:
    with pytest.raises(exception):
        OperationalSafetyConfiguration(**{field_name: value})  # type: ignore[arg-type]


def test_health_snapshot_is_immutable_and_requires_utc() -> None:
    health = _health()

    with pytest.raises(FrozenInstanceError):
        health.spread_bps = Decimal(1)  # type: ignore[misc]
    with pytest.raises(ValueError, match="UTC"):
        _health(observed_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        _health(market_data_observed_at=NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("field_name", "value", "exception"),
    [
        ("spread_bps", 1.0, TypeError),
        ("spread_bps", Decimal("NaN"), ValueError),
        ("spread_bps", Decimal(-1), ValueError),
        ("time_sync_offset_ms", 1.0, TypeError),
        ("time_sync_offset_ms", Decimal("Infinity"), ValueError),
        ("suspicious_price_anomaly", 1, TypeError),
        ("consecutive_order_rejections", True, TypeError),
        ("consecutive_order_rejections", -1, ValueError),
        ("feed_integrity_state", "OK", TypeError),
        ("exchange_api_health_state", "HEALTHY", TypeError),
        ("volatility_safety_state", "CLASSIFIED_NORMAL", TypeError),
    ],
)
def test_health_snapshot_rejects_invalid_values(
    field_name: str, value: object, exception: type[Exception]
) -> None:
    with pytest.raises(exception):
        _health(**{field_name: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": SourceId("other")},
        {"symbol": TradingSymbol("ETH", "USDT")},
    ],
)
def test_health_identity_mismatch_enters_safe_mode(changes: dict[str, object]) -> None:
    decision = _evaluate(health=_health(**changes))

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert not decision.entry_permitted
    assert OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH in decision.reasons


def test_health_observed_at_mismatch_enters_safe_mode() -> None:
    decision = _evaluate(health=_health(observed_at=NOW - timedelta(seconds=1)))

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH in decision.reasons


def test_future_market_data_timestamp_fails_closed() -> None:
    decision = _evaluate(health=_health(market_data_observed_at=NOW + timedelta(microseconds=1)))

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert decision.market_data_age == -timedelta(microseconds=1)
    assert OperationalSafetyReasonCode.FUTURE_MARKET_DATA_TIMESTAMP in decision.reasons


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(seconds=120), OperationalSafetyStatus.CLEARED),
        (timedelta(seconds=120, microseconds=1), OperationalSafetyStatus.SAFE_MODE),
    ],
)
def test_market_data_age_boundary(age: timedelta, expected: OperationalSafetyStatus) -> None:
    decision = _evaluate(health=_health(market_data_observed_at=NOW - age))

    assert decision.status is expected
    assert decision.market_data_age == age
    expected_reason = (
        OperationalSafetyReasonCode.MARKET_DATA_FRESH
        if expected is OperationalSafetyStatus.CLEARED
        else OperationalSafetyReasonCode.STALE_MARKET_DATA
    )
    assert expected_reason in decision.reasons


@pytest.mark.parametrize(
    ("state", "expected", "reason"),
    [
        (
            FeedIntegrityState.OK,
            OperationalSafetyStatus.CLEARED,
            OperationalSafetyReasonCode.FEED_INTEGRITY_OK,
        ),
        (
            FeedIntegrityState.CORRUPT,
            OperationalSafetyStatus.SAFE_MODE,
            OperationalSafetyReasonCode.CORRUPT_FEED,
        ),
        (
            FeedIntegrityState.BROKEN,
            OperationalSafetyStatus.SAFE_MODE,
            OperationalSafetyReasonCode.BROKEN_FEED,
        ),
    ],
)
def test_feed_integrity_gate(
    state: FeedIntegrityState,
    expected: OperationalSafetyStatus,
    reason: OperationalSafetyReasonCode,
) -> None:
    decision = _evaluate(health=_health(feed_integrity_state=state))

    assert decision.status is expected
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("state", "expected", "reason"),
    [
        (
            ExchangeApiHealthState.HEALTHY,
            OperationalSafetyStatus.CLEARED,
            OperationalSafetyReasonCode.EXCHANGE_API_HEALTHY,
        ),
        (
            ExchangeApiHealthState.DEGRADED,
            OperationalSafetyStatus.SAFE_MODE,
            OperationalSafetyReasonCode.EXCHANGE_API_DEGRADED,
        ),
        (
            ExchangeApiHealthState.DOWN,
            OperationalSafetyStatus.SAFE_MODE,
            OperationalSafetyReasonCode.EXCHANGE_API_DOWN,
        ),
    ],
)
def test_exchange_api_health_gate(
    state: ExchangeApiHealthState,
    expected: OperationalSafetyStatus,
    reason: OperationalSafetyReasonCode,
) -> None:
    decision = _evaluate(health=_health(exchange_api_health_state=state))

    assert decision.status is expected
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("spread", "expected"),
    [
        (Decimal("10.0"), OperationalSafetyStatus.CLEARED),
        (Decimal("10.0000000000000000000000000001"), OperationalSafetyStatus.SAFE_MODE),
    ],
)
def test_spread_boundary(spread: Decimal, expected: OperationalSafetyStatus) -> None:
    decision = _evaluate(health=_health(spread_bps=spread))

    assert decision.status is expected


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (Decimal("1000"), OperationalSafetyStatus.CLEARED),
        (Decimal("-1000"), OperationalSafetyStatus.CLEARED),
        (Decimal("1000.000000000000000000000000001"), OperationalSafetyStatus.SAFE_MODE),
        (Decimal("-1000.000000000000000000000000001"), OperationalSafetyStatus.SAFE_MODE),
    ],
)
def test_absolute_time_sync_boundary(offset: Decimal, expected: OperationalSafetyStatus) -> None:
    decision = _evaluate(health=_health(time_sync_offset_ms=offset))

    assert decision.status is expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (VolatilitySafetyState.CLASSIFIED_NORMAL, OperationalSafetyStatus.CLEARED),
        (VolatilitySafetyState.CLASSIFIED_HIGH, OperationalSafetyStatus.CLEARED),
        (VolatilitySafetyState.EXTREME, OperationalSafetyStatus.SAFE_MODE),
        (VolatilitySafetyState.UNCLASSIFIED, OperationalSafetyStatus.SAFE_MODE),
    ],
)
def test_volatility_safety_gate(
    state: VolatilitySafetyState, expected: OperationalSafetyStatus
) -> None:
    decision = _evaluate(health=_health(volatility_safety_state=state))

    assert decision.status is expected


def test_suspicious_price_anomaly_enters_safe_mode() -> None:
    decision = _evaluate(health=_health(suspicious_price_anomaly=True))

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert OperationalSafetyReasonCode.SUSPICIOUS_PRICE_ANOMALY in decision.reasons


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (2, OperationalSafetyStatus.CLEARED),
        (3, OperationalSafetyStatus.SAFE_MODE),
        (4, OperationalSafetyStatus.SAFE_MODE),
    ],
)
def test_order_rejection_count_boundary(count: int, expected: OperationalSafetyStatus) -> None:
    decision = _evaluate(health=_health(consecutive_order_rejections=count))

    assert decision.status is expected


def test_daily_loss_risk_rejection_enters_safe_mode() -> None:
    risk = _risk_decision(equity=Decimal(9900), day_start=Decimal(10000))
    decision = _evaluate(risk)

    assert risk.status is RiskDecisionStatus.REJECTED
    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert OperationalSafetyReasonCode.DAILY_LOSS_HALT in decision.reasons


def test_drawdown_risk_rejection_enters_safe_mode_and_preserves_review() -> None:
    risk = _risk_decision(equity=Decimal(9500), day_start=Decimal(9500), peak=Decimal(10000))
    decision = _evaluate(risk)

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert decision.risk_requires_halt_review
    assert OperationalSafetyReasonCode.DRAWDOWN_HALT_REVIEW in decision.reasons


def test_ordinary_risk_rejection_is_blocked_without_safe_mode() -> None:
    evaluation = replace(_evaluation(), invalidation_reference_price=Decimal(101))
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    decision = _evaluate(risk)

    assert risk.reasons == (RiskReasonCode.INVALIDATION_WRONG_SIDE,)
    assert decision.status is OperationalSafetyStatus.BLOCKED_BY_RISK
    assert not decision.entry_permitted
    assert decision.approved_quantity is None


def test_no_action_stays_no_action_unless_operational_blocker_exists() -> None:
    risk = _risk_decision(CandidateAction.NO_TRADE)
    safe = _evaluate(risk)
    blocked = _evaluate(risk, _health(feed_integrity_state=FeedIntegrityState.BROKEN))

    assert safe.status is OperationalSafetyStatus.NO_ACTION
    assert blocked.status is OperationalSafetyStatus.SAFE_MODE
    assert not safe.entry_permitted
    assert not blocked.entry_permitted


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("strategy_candidate_action", CandidateAction.NO_TRADE),
        ("reference_price", None),
        ("reference_price", 100.0),
        ("reference_price", Decimal("NaN")),
        ("approved_risk_stop_basis", Decimal(101)),
        ("stop_distance", Decimal(11)),
        ("approved_quantity", Decimal(3)),
        ("approved_notional_quote", Decimal(251)),
        ("approved_risk_quote", Decimal(26)),
        ("resulting_open_risk_quote", Decimal(51)),
        ("resulting_open_risk_quote", Decimal("Infinity")),
        ("resulting_gross_exposure_quote", Decimal(10001)),
        ("per_trade_risk_budget_quote", Decimal(24)),
        ("simultaneous_risk_cap_quote", Decimal(24)),
        ("gross_exposure_cap_quote", Decimal(249)),
    ],
)
def test_forged_approved_risk_decisions_fail_closed(field_name: str, value: object) -> None:
    forged = replace(_risk_decision(), **{field_name: value})  # type: ignore[arg-type]
    decision = _evaluate(forged)

    assert decision.status is OperationalSafetyStatus.SAFE_MODE
    assert not decision.entry_permitted
    assert decision.approved_quantity is None
    assert OperationalSafetyReasonCode.RISK_DECISION_INCONSISTENT in decision.reasons


def test_forged_no_action_with_candidate_action_fails_closed() -> None:
    risk = _risk_decision(CandidateAction.NO_TRADE)
    forged = replace(risk, strategy_candidate_action=CandidateAction.LONG_CANDIDATE)

    assert _evaluate(forged).status is OperationalSafetyStatus.SAFE_MODE


def test_rejected_decision_never_exposes_forged_executable_sizing() -> None:
    rejected = RiskManager().evaluate(
        replace(_evaluation(), invalidation_reference_price=Decimal(101)),
        RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    approved = _risk_decision()
    forged = replace(
        rejected,
        approved_risk_stop_basis=approved.approved_risk_stop_basis,
        approved_quantity=approved.approved_quantity,
        approved_notional_quote=approved.approved_notional_quote,
        approved_risk_quote=approved.approved_risk_quote,
    )

    decision = _evaluate(forged)

    assert decision.status is OperationalSafetyStatus.BLOCKED_BY_RISK
    assert not decision.entry_permitted
    assert decision.approved_risk_stop_basis is None
    assert decision.approved_quantity is None
    assert decision.approved_notional_quote is None
    assert decision.approved_risk_quote is None


def test_cleared_decision_copies_phase_8_1_sizing_exactly() -> None:
    risk = _risk_decision()
    decision = _evaluate(risk)

    assert decision.status is OperationalSafetyStatus.CLEARED
    assert decision.entry_permitted
    assert decision.approved_quantity is risk.approved_quantity
    assert decision.approved_notional_quote is risk.approved_notional_quote
    assert decision.approved_risk_quote is risk.approved_risk_quote
    assert decision.approved_risk_stop_basis is risk.approved_risk_stop_basis
    assert decision.reasons == (
        OperationalSafetyReasonCode.RISK_APPROVAL_VALID,
        OperationalSafetyReasonCode.MARKET_DATA_FRESH,
        OperationalSafetyReasonCode.FEED_INTEGRITY_OK,
        OperationalSafetyReasonCode.EXCHANGE_API_HEALTHY,
        OperationalSafetyReasonCode.SPREAD_WITHIN_LIMIT,
        OperationalSafetyReasonCode.TIME_SYNC_WITHIN_LIMIT,
        OperationalSafetyReasonCode.VOLATILITY_CLASSIFIED_SAFE,
        OperationalSafetyReasonCode.NO_PRICE_ANOMALY,
        OperationalSafetyReasonCode.ORDER_REJECTION_COUNT_WITHIN_LIMIT,
        OperationalSafetyReasonCode.ENTRY_CLEARED,
    )
    assert not hasattr(decision, "order_id")
    assert not hasattr(decision, "exchange_side")


@pytest.mark.parametrize(
    "reason",
    [
        StrategyReasonCode.CONTEXT_READY,
        BreakoutRetestReasonCode.RETEST_ZONE_TOUCHED,
        RangeMeanReversionReasonCode.VALIDATED_RANGE_ACTIVE,
    ],
)
def test_all_v1_strategy_generic_types_use_the_same_safety_pipeline(reason: object) -> None:
    risk = _risk_decision(reason=reason)
    decision = _evaluate(risk)

    assert decision.status is OperationalSafetyStatus.CLEARED
    assert decision.approved_quantity == risk.approved_quantity


def test_multiple_blockers_are_complete_and_canonically_ordered() -> None:
    risk = _risk_decision(equity=Decimal(9000), day_start=Decimal(10000), peak=Decimal(10000))
    health = _health(
        source_id=SourceId("other"),
        symbol=TradingSymbol("ETH", "USDT"),
        observed_at=NOW + timedelta(seconds=1),
        market_data_observed_at=NOW + timedelta(seconds=2),
        feed_integrity_state=FeedIntegrityState.CORRUPT,
        exchange_api_health_state=ExchangeApiHealthState.DOWN,
        spread_bps=Decimal("10.1"),
        time_sync_offset_ms=Decimal("1001"),
        volatility_safety_state=VolatilitySafetyState.EXTREME,
        suspicious_price_anomaly=True,
        consecutive_order_rejections=3,
    )

    assert _evaluate(risk, health).reasons == (
        OperationalSafetyReasonCode.RISK_REJECTED,
        OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH,
        OperationalSafetyReasonCode.HEALTH_TIME_MISMATCH,
        OperationalSafetyReasonCode.DAILY_LOSS_HALT,
        OperationalSafetyReasonCode.DRAWDOWN_HALT_REVIEW,
        OperationalSafetyReasonCode.FUTURE_MARKET_DATA_TIMESTAMP,
        OperationalSafetyReasonCode.CORRUPT_FEED,
        OperationalSafetyReasonCode.EXCHANGE_API_DOWN,
        OperationalSafetyReasonCode.SPREAD_TOO_WIDE,
        OperationalSafetyReasonCode.TIME_SYNC_FAULT,
        OperationalSafetyReasonCode.EXTREME_VOLATILITY,
        OperationalSafetyReasonCode.SUSPICIOUS_PRICE_ANOMALY,
        OperationalSafetyReasonCode.REPEATED_ORDER_REJECTIONS,
    )


def test_identical_inputs_and_ambient_decimal_context_are_deterministic() -> None:
    risk = _risk_decision()
    health = _health(spread_bps=Decimal("9.9999999999999999999999999999"))
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = OperationalSafetyGate().evaluate(risk, health)
        setcontext(Context(prec=50))
        high_precision = OperationalSafetyGate().evaluate(risk, health)
    finally:
        setcontext(original)

    assert low_precision == high_precision == OperationalSafetyGate().evaluate(risk, health)


def test_safety_module_has_no_environment_or_concrete_strategy_dependency() -> None:
    path = Path(safety_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "adaptive_trading_bot.backtest",
        "adaptive_trading_bot.exchange",
        "adaptive_trading_bot.market_data",
        "adaptive_trading_bot.replay",
        "adaptive_trading_bot.storage",
        "adaptive_trading_bot.strategies.trend_pullback",
        "adaptive_trading_bot.strategies.breakout_retest",
        "adaptive_trading_bot.strategies.range_mean_reversion",
    )

    assert not any(name.startswith(forbidden) for name in imported)
