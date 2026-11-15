import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext
from enum import StrEnum
from pathlib import Path

import pytest

import adaptive_trading_bot.risk.core as risk_core
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.risk import (
    RiskDecision,
    RiskDecisionStatus,
    RiskDrawdownEnforcement,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    RiskPositionExposure,
    RiskReasonCode,
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
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


class DummyReason(StrEnum):
    ACCEPTED = "ACCEPTED"


def _evaluation(
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    *,
    symbol: TradingSymbol = BTC,
    observed_at: datetime = NOW,
    reference: Decimal | None = Decimal(100),
    invalidation: Decimal | None = Decimal(90),
    reason: object = DummyReason.ACCEPTED,
    audit: object = "strategy-specific-audit",
) -> StrategyEvaluation[object, object]:
    return StrategyEvaluation(
        "test_strategy",
        "1.0.0",
        SOURCE,
        symbol,
        observed_at,
        action,
        reference,
        invalidation,
        (reason,),
        (),
        audit,
    )


def _position(
    symbol: TradingSymbol,
    *,
    quantity: Decimal = Decimal(1),
    gross: Decimal = Decimal(100),
    open_risk: Decimal = Decimal(10),
) -> RiskPositionExposure:
    return RiskPositionExposure(symbol, quantity, gross, open_risk)


def _portfolio(
    *,
    observed_at: datetime = NOW,
    equity: Decimal = Decimal(10000),
    day_start: Decimal = Decimal(10000),
    peak: Decimal = Decimal(10000),
    positions: tuple[RiskPositionExposure, ...] = (),
) -> RiskPortfolioSnapshot:
    return RiskPortfolioSnapshot(observed_at, "USDT", equity, day_start, peak, positions)


def _approve(
    evaluation: StrategyEvaluation[object, object] | None = None,
    portfolio: RiskPortfolioSnapshot | None = None,
) -> RiskDecision:
    return RiskManager().evaluate(
        _evaluation() if evaluation is None else evaluation,
        _portfolio() if portfolio is None else portfolio,
    )


def test_configuration_defaults_are_exact_and_reject_invalid_values() -> None:
    assert RiskManagerConfiguration() == RiskManagerConfiguration(
        risk_per_trade_fraction=Decimal("0.0025"),
        max_simultaneous_risk_fraction=Decimal("0.0050"),
        max_daily_loss_fraction=Decimal("0.0100"),
        max_drawdown_fraction=Decimal("0.0500"),
        max_gross_exposure_fraction=Decimal("1.0000"),
    )
    with pytest.raises(TypeError, match="Decimal"):
        RiskManagerConfiguration(risk_per_trade_fraction=0.0025)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        RiskManagerConfiguration(max_daily_loss_fraction=Decimal("NaN"))
    with pytest.raises(ValueError, match="cannot exceed"):
        RiskManagerConfiguration(
            risk_per_trade_fraction=Decimal("0.006"),
            max_simultaneous_risk_fraction=Decimal("0.005"),
        )
    with pytest.raises(ValueError, match="between"):
        RiskManagerConfiguration(max_drawdown_fraction=Decimal(1))
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        RiskManagerConfiguration(max_gross_exposure_fraction=Decimal("1.01"))


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": Decimal(0)},
        {"quantity": Decimal("NaN")},
        {"gross_exposure_quote": Decimal(-1)},
        {"open_risk_quote": Decimal(-1)},
    ],
)
def test_position_exposure_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(_position(BTC), **changes)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Decimal"):
        RiskPositionExposure(BTC, 1, Decimal(100), Decimal(10))  # type: ignore[arg-type]


def test_portfolio_validation_and_derived_canonical_aggregation() -> None:
    btc = _position(BTC, quantity=Decimal(-1), gross=Decimal(300), open_risk=Decimal(12))
    eth = _position(ETH, gross=Decimal(200), open_risk=Decimal(8))
    portfolio = _portfolio(positions=(eth, btc))
    assert portfolio.positions == (btc, eth)
    assert portfolio.current_gross_exposure_quote == Decimal(500)
    assert portfolio.current_open_risk_quote == Decimal(20)
    with pytest.raises(ValueError, match="duplicate"):
        _portfolio(positions=(btc, replace(btc, quantity=Decimal(2))))
    with pytest.raises(ValueError, match="peak equity"):
        _portfolio(equity=Decimal(10001))
    with pytest.raises(ValueError, match="peak equity"):
        _portfolio(day_start=Decimal(10001))
    with pytest.raises(ValueError, match="USDT"):
        RiskPortfolioSnapshot(NOW, "USD", Decimal(1), Decimal(1), Decimal(1))
    with pytest.raises(ValueError, match="USDT"):
        _portfolio(positions=(_position(TradingSymbol("BTC", "USD")),))
    with pytest.raises(TypeError, match="tuple"):
        RiskPortfolioSnapshot(
            NOW,
            "USDT",
            Decimal(1),
            Decimal(1),
            Decimal(1),
            [],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="UTC"):
        _portfolio(observed_at=NOW.replace(tzinfo=None))


def test_risk_package_has_no_environment_or_concrete_strategy_dependency() -> None:
    path = Path(risk_core.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("adaptive_trading_bot.backtest", "adaptive_trading_bot.exchange")
    concrete = (
        "adaptive_trading_bot.strategies.trend_pullback",
        "adaptive_trading_bot.strategies.breakout_retest",
        "adaptive_trading_bot.strategies.range_mean_reversion",
    )
    assert not any(name.startswith((*forbidden, *concrete)) for name in imported)


def test_strategy_no_trade_can_never_be_upgraded() -> None:
    decision = _approve(
        _evaluation(
            CandidateAction.NO_TRADE,
            reference=Decimal(100),
            invalidation=Decimal(90),
        )
    )
    assert decision.status is RiskDecisionStatus.NO_ACTION
    assert decision.reasons == (RiskReasonCode.STRATEGY_NO_TRADE,)
    assert decision.approved_quantity is None
    assert decision.approved_risk_quote is None


@pytest.mark.parametrize(
    ("action", "reference", "invalidation", "expected_quantity"),
    [
        (CandidateAction.LONG_CANDIDATE, Decimal(100), Decimal(90), Decimal("2.5")),
        (CandidateAction.SHORT_CANDIDATE, Decimal(100), Decimal(110), Decimal("2.5")),
    ],
)
def test_valid_long_and_short_geometry_are_risk_sized(
    action: CandidateAction,
    reference: Decimal,
    invalidation: Decimal,
    expected_quantity: Decimal,
) -> None:
    decision = _approve(_evaluation(action, reference=reference, invalidation=invalidation))
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.risk_policy_version == "1.0.0"
    assert decision.max_daily_loss_fraction == Decimal("0.0100")
    assert decision.max_drawdown_fraction == Decimal("0.0500")
    assert decision.stop_distance == Decimal(10)
    assert decision.approved_quantity == expected_quantity
    assert decision.approved_notional_quote == Decimal(250)
    assert decision.approved_risk_quote == Decimal(25)
    assert decision.resulting_open_risk_quote == Decimal(25)
    assert decision.resulting_gross_exposure_quote == Decimal(250)
    assert decision.reasons == (
        RiskReasonCode.STRATEGY_CANDIDATE_ACCEPTED,
        RiskReasonCode.CANDIDATE_GEOMETRY_VALID,
        RiskReasonCode.DAILY_LOSS_WITHIN_LIMIT,
        RiskReasonCode.DRAWDOWN_WITHIN_LIMIT,
        RiskReasonCode.SYMBOL_FLAT,
        RiskReasonCode.SIMULTANEOUS_RISK_AVAILABLE,
        RiskReasonCode.GROSS_EXPOSURE_AVAILABLE,
        RiskReasonCode.RISK_SIZE_APPROVED,
    )


@pytest.mark.parametrize(
    ("action", "invalidation"),
    [
        (CandidateAction.LONG_CANDIDATE, Decimal(100)),
        (CandidateAction.LONG_CANDIDATE, Decimal(101)),
        (CandidateAction.SHORT_CANDIDATE, Decimal(100)),
        (CandidateAction.SHORT_CANDIDATE, Decimal(99)),
    ],
)
def test_wrong_side_or_equal_invalidation_is_rejected(
    action: CandidateAction, invalidation: Decimal
) -> None:
    decision = _approve(_evaluation(action, invalidation=invalidation))
    assert decision.status is RiskDecisionStatus.REJECTED
    assert RiskReasonCode.INVALIDATION_WRONG_SIDE in decision.reasons


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("reference_price", None, RiskReasonCode.REFERENCE_PRICE_INVALID),
        ("reference_price", Decimal(0), RiskReasonCode.REFERENCE_PRICE_INVALID),
        ("reference_price", Decimal("NaN"), RiskReasonCode.REFERENCE_PRICE_INVALID),
        ("reference_price", 100.0, RiskReasonCode.REFERENCE_PRICE_INVALID),
        ("invalidation_reference_price", None, RiskReasonCode.INVALIDATION_PRICE_INVALID),
        ("invalidation_reference_price", Decimal(-1), RiskReasonCode.INVALIDATION_PRICE_INVALID),
        (
            "invalidation_reference_price",
            Decimal("Infinity"),
            RiskReasonCode.INVALIDATION_PRICE_INVALID,
        ),
        ("invalidation_reference_price", 90.0, RiskReasonCode.INVALIDATION_PRICE_INVALID),
    ],
)
def test_malformed_candidate_prices_are_rejected(
    field: str, value: object, reason: RiskReasonCode
) -> None:
    malformed = replace(_evaluation(), **{field: value})  # type: ignore[arg-type]
    decision = _approve(malformed)
    assert decision.status is RiskDecisionStatus.REJECTED
    assert reason in decision.reasons


def test_timestamp_and_non_usdt_candidate_gates_reject() -> None:
    stale = _approve(_evaluation(observed_at=NOW - timedelta(minutes=1)))
    non_usdt = _approve(_evaluation(symbol=TradingSymbol("BTC", "USD")))
    assert stale.reasons == (RiskReasonCode.RISK_SNAPSHOT_TIME_MISMATCH,)
    assert non_usdt.reasons == (RiskReasonCode.NON_USDT_V1_SYMBOL,)


@pytest.mark.parametrize("quantity", [Decimal(1), Decimal(-1)])
def test_existing_same_symbol_position_rejects_new_candidate(quantity: Decimal) -> None:
    portfolio = _portfolio(positions=(_position(BTC, quantity=quantity),))
    decision = _approve(portfolio=portfolio)
    assert decision.status is RiskDecisionStatus.REJECTED
    assert RiskReasonCode.EXISTING_SYMBOL_POSITION in decision.reasons


def test_independent_other_symbol_position_is_permitted_within_aggregate_limits() -> None:
    portfolio = _portfolio(positions=(_position(ETH, gross=Decimal(500), open_risk=Decimal(25)),))
    decision = _approve(portfolio=portfolio)
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.approved_risk_quote == Decimal(25)
    assert decision.resulting_open_risk_quote == Decimal(50)


@pytest.mark.parametrize(
    ("equity", "expected"),
    [
        (Decimal("9900.01"), RiskDecisionStatus.APPROVED),
        (Decimal(9900), RiskDecisionStatus.REJECTED),
        (Decimal("9899.99"), RiskDecisionStatus.REJECTED),
    ],
)
def test_daily_loss_boundary_is_exact(equity: Decimal, expected: RiskDecisionStatus) -> None:
    decision = _approve(portfolio=_portfolio(equity=equity))
    assert decision.status is expected
    assert (RiskReasonCode.DAILY_LOSS_LIMIT_REACHED in decision.reasons) is (
        expected is RiskDecisionStatus.REJECTED
    )


@pytest.mark.parametrize(
    ("equity", "expected", "halt"),
    [
        (Decimal("9500.01"), RiskDecisionStatus.APPROVED, False),
        (Decimal(9500), RiskDecisionStatus.REJECTED, True),
        (Decimal("9499.99"), RiskDecisionStatus.REJECTED, True),
    ],
)
def test_drawdown_boundary_is_exact_and_flags_review(
    equity: Decimal, expected: RiskDecisionStatus, halt: bool
) -> None:
    decision = _approve(portfolio=_portfolio(equity=equity, day_start=equity))
    assert decision.status is expected
    assert decision.requires_halt_review is halt
    assert (RiskReasonCode.DRAWDOWN_LIMIT_REACHED in decision.reasons) is halt


def test_opt_in_report_only_drawdown_preserves_peak_without_halt() -> None:
    manager = RiskManager(
        RiskManagerConfiguration(
            drawdown_enforcement=RiskDrawdownEnforcement.REPORT_ONLY,
        )
    )
    decision = manager.evaluate(
        _evaluation(),
        _portfolio(equity=Decimal("9000"), day_start=Decimal("9000"), peak=Decimal("12000")),
    )

    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.peak_equity_quote == Decimal("12000")
    assert decision.drawdown_fraction == Decimal("0.25")
    assert decision.drawdown_enforcement is RiskDrawdownEnforcement.REPORT_ONLY
    assert RiskReasonCode.DRAWDOWN_RECORDED_REPORT_ONLY in decision.reasons
    assert RiskReasonCode.DRAWDOWN_LIMIT_REACHED not in decision.reasons
    assert not decision.requires_halt_review


@pytest.mark.parametrize(
    ("open_risk", "expected_status", "expected_new_risk"),
    [
        (Decimal(25), RiskDecisionStatus.APPROVED, Decimal(25)),
        (Decimal(40), RiskDecisionStatus.APPROVED, Decimal(10)),
        (Decimal(50), RiskDecisionStatus.REJECTED, None),
        (Decimal(51), RiskDecisionStatus.REJECTED, None),
    ],
)
def test_simultaneous_risk_budget_boundaries(
    open_risk: Decimal,
    expected_status: RiskDecisionStatus,
    expected_new_risk: Decimal | None,
) -> None:
    portfolio = _portfolio(positions=(_position(ETH, gross=Decimal(100), open_risk=open_risk),))
    decision = _approve(portfolio=portfolio)
    assert decision.status is expected_status
    assert decision.approved_risk_quote == expected_new_risk
    if expected_status is RiskDecisionStatus.REJECTED:
        assert RiskReasonCode.SIMULTANEOUS_RISK_EXHAUSTED in decision.reasons
    else:
        assert decision.resulting_open_risk_quote is not None
        assert decision.resulting_open_risk_quote <= decision.simultaneous_risk_cap_quote


@pytest.mark.parametrize(
    ("gross", "expected"),
    [
        (Decimal(9900), RiskDecisionStatus.APPROVED),
        (Decimal(10000), RiskDecisionStatus.REJECTED),
        (Decimal(10001), RiskDecisionStatus.REJECTED),
    ],
)
def test_gross_exposure_cap_reduces_or_rejects_quantity(
    gross: Decimal, expected: RiskDecisionStatus
) -> None:
    portfolio = _portfolio(positions=(_position(ETH, gross=gross, open_risk=Decimal(0)),))
    decision = _approve(portfolio=portfolio)
    assert decision.status is expected
    if expected is RiskDecisionStatus.APPROVED:
        assert decision.approved_quantity == Decimal(1)
        assert decision.approved_notional_quote == Decimal(100)
        assert decision.approved_risk_quote == Decimal(10)
        assert decision.resulting_gross_exposure_quote == Decimal(10000)
    else:
        assert RiskReasonCode.GROSS_EXPOSURE_EXHAUSTED in decision.reasons


@pytest.mark.parametrize(
    "reason",
    [
        StrategyReasonCode.CONTEXT_READY,
        BreakoutRetestReasonCode.RETEST_ZONE_TOUCHED,
        RangeMeanReversionReasonCode.VALIDATED_RANGE_ACTIVE,
    ],
)
def test_all_v1_strategy_reason_and_audit_types_use_the_same_risk_api(reason: object) -> None:
    decision = _approve(_evaluation(reason=reason, audit={"opaque": str(reason)}))
    assert decision.status is RiskDecisionStatus.APPROVED
    assert decision.approved_quantity == Decimal("2.5")


def test_repeated_inputs_and_ambient_decimal_context_are_deterministic() -> None:
    evaluation = _evaluation(reference=Decimal(3), invalidation=Decimal(2))
    portfolio = _portfolio()
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = RiskManager().evaluate(evaluation, portfolio)
        setcontext(Context(prec=50))
        high_precision = RiskManager().evaluate(evaluation, portfolio)
    finally:
        setcontext(original)
    assert low_precision == high_precision == RiskManager().evaluate(evaluation, portfolio)


def test_rejection_reason_order_is_canonical_and_no_order_is_emitted() -> None:
    portfolio = _portfolio(
        observed_at=NOW,
        equity=Decimal(9000),
        day_start=Decimal(10000),
        peak=Decimal(10000),
        positions=(_position(BTC, gross=Decimal(10000), open_risk=Decimal(100)),),
    )
    candidate = _evaluation(
        symbol=TradingSymbol("BTC", "USD"),
        observed_at=NOW - timedelta(minutes=1),
        reference=None,
        invalidation=None,
    )
    decision = _approve(candidate, portfolio)
    assert decision.reasons == (
        RiskReasonCode.RISK_SNAPSHOT_TIME_MISMATCH,
        RiskReasonCode.NON_USDT_V1_SYMBOL,
        RiskReasonCode.REFERENCE_PRICE_INVALID,
        RiskReasonCode.INVALIDATION_PRICE_INVALID,
        RiskReasonCode.DAILY_LOSS_LIMIT_REACHED,
        RiskReasonCode.DRAWDOWN_LIMIT_REACHED,
        RiskReasonCode.SIMULTANEOUS_RISK_EXHAUSTED,
        RiskReasonCode.GROSS_EXPOSURE_EXHAUSTED,
    )
    assert decision.requires_halt_review
    assert not hasattr(decision, "order_id")
    assert not hasattr(decision, "exchange_quantity")
    assert not hasattr(_evaluation(), "quantity")
