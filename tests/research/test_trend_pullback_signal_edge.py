"""Deterministic fixture checks for the research-only signal-edge screen."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.research.trend_pullback_signal_edge import (
    FROZEN_DATASET_VERSION,
    FROZEN_EXECUTION_COSTS,
    FROZEN_FOLLOW_UP_DURATION,
    FROZEN_MANIFEST_SHA256,
    FROZEN_RUNS,
    FROZEN_SOURCE_ID,
    CandidateEconomicOutcome,
    EconomicStratificationDimension,
    ProfitFactorState,
    SignalEdgeScreenStatus,
    SignalEdgeUnitStatus,
    _economics,
    _is_evaluable,
    _losing_streak,
    _screen_status,
    _strata,
    _unit_status,
)
from adaptive_trading_bot.storage import SourceId


def test_frozen_protocol_has_only_three_predeclared_units() -> None:
    assert tuple((item.run_id.value, str(item.symbol)) for item in FROZEN_RUNS) == (
        ("A", "BTC/USDT"),
        ("B", "ETH/USDT"),
        ("C", "ETH/USDT"),
    )
    assert tuple(item.decision_start.isoformat() for item in FROZEN_RUNS) == (
        "2024-07-01T00:00:00+00:00",
        "2024-07-01T00:00:00+00:00",
        "2025-04-02T00:00:00+00:00",
    )
    assert tuple(item.decision_end.isoformat() for item in FROZEN_RUNS) == (
        "2026-01-01T00:00:00+00:00",
        "2024-12-25T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
    )
    assert all(
        item.follow_up_boundary == item.decision_end + timedelta(hours=168) for item in FROZEN_RUNS
    )
    assert timedelta(hours=168) == FROZEN_FOLLOW_UP_DURATION
    assert SourceId("weex-v3-last-e5b0c4035a19d5a80ca7") == FROZEN_SOURCE_ID
    assert FROZEN_DATASET_VERSION == "weex-v1-5m-last-a629a29b580b2e103adba918"
    assert (
        FROZEN_MANIFEST_SHA256 == "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
    )
    assert (
        FROZEN_EXECUTION_COSTS.spread_bps,
        FROZEN_EXECUTION_COSTS.slippage_bps,
        FROZEN_EXECUTION_COSTS.fee_bps,
    ) == (Decimal(2), Decimal(2), Decimal(10))


def _outcome(
    index: int,
    pnl: str,
    risk: str = "1",
    *,
    side: str = "LONG",
    month: int = 1,
    exit_hour: int | None = None,
) -> CandidateEconomicOutcome:
    observed_at = datetime(2025, month, 1, tzinfo=UTC) + timedelta(minutes=index)
    exit_at = observed_at + timedelta(hours=exit_hour if exit_hour is not None else index + 1)
    return CandidateEconomicOutcome(
        identity=DecisionIdentity(
            SourceId("fixture"),
            TradingSymbol("BTC", "USDT"),
            observed_at,
            "trend_pullback",
            "1.0.0",
        ),
        entry_side=side,
        utc_entry_month=observed_at.strftime("%Y-%m"),
        exit_fill_at=exit_at,
        realized_net_pnl=Decimal(pnl),
        approved_risk_quote=Decimal(risk),
    )


def test_normalization_and_absolute_denominators_use_authorized_risk() -> None:
    outcomes = (
        _outcome(0, "7.5", "2.5"),
        _outcome(1, "-2.5", "5"),
        _outcome(2, "0", "4"),
    )
    assert tuple(item.net_r for item in outcomes) == (
        Decimal("3"),
        Decimal("-0.5"),
        Decimal("0"),
    )
    facts = _economics(outcomes)
    assert (
        facts.outcome_denominator,
        facts.win_count,
        facts.breakeven_count,
        facts.loss_count,
    ) == (
        3,
        1,
        1,
        1,
    )
    assert (facts.win_rate.numerator, facts.win_rate.denominator, facts.win_rate.value) == (
        1,
        3,
        Decimal("0.3333333333333333333333333333333333"),
    )
    assert (facts.positive_net_r, facts.negative_net_r, facts.total_realized_net_r) == (
        Decimal("3"),
        Decimal("-0.5"),
        Decimal("2.5"),
    )
    assert (facts.mean_net_r, facts.median_net_r) == (
        Decimal("0.8333333333333333333333333333333333"),
        Decimal(0),
    )
    assert (facts.minimum_net_r, facts.maximum_net_r) == (Decimal("-0.5"), Decimal(3))
    assert (facts.average_winning_r, facts.average_losing_r) == (
        Decimal(3),
        Decimal("-0.5"),
    )
    assert (facts.profit_factor_state, facts.profit_factor) == (
        ProfitFactorState.FINITE,
        Decimal(6),
    )


def test_profit_factor_zero_denominator_and_empty_outcomes_are_explicit() -> None:
    winning_only = _economics((_outcome(0, "2"), _outcome(1, "0")))
    assert winning_only.profit_factor_state is ProfitFactorState.INFINITE
    assert winning_only.profit_factor is None
    zero_only = _economics((_outcome(0, "0"),))
    assert zero_only.profit_factor_state is ProfitFactorState.UNAVAILABLE
    assert zero_only.profit_factor is None
    empty = _economics(())
    assert empty.outcome_denominator == 0
    assert empty.win_rate.denominator == 0
    assert empty.win_rate.value is None
    assert empty.mean_net_r is None
    assert empty.profit_factor_state is ProfitFactorState.UNAVAILABLE
    losing_only = _economics((_outcome(0, "-2"),))
    assert (losing_only.profit_factor_state, losing_only.profit_factor) == (
        ProfitFactorState.FINITE,
        Decimal(0),
    )


def test_chronological_streak_orders_by_exit_and_breakeven_resets() -> None:
    outcomes = (
        _outcome(0, "-1", exit_hour=5),
        _outcome(1, "-1", exit_hour=1),
        _outcome(2, "0", exit_hour=2),
        _outcome(3, "-1", exit_hour=3),
        _outcome(4, "-1", exit_hour=4),
    )
    assert _losing_streak(outcomes) == 3


def test_side_and_utc_entry_month_strata_are_absolute_not_paired_deltas() -> None:
    facts = _strata(
        (
            _outcome(0, "2", side="LONG", month=1),
            _outcome(1, "-1", side="SHORT", month=1),
            _outcome(2, "-3", side="LONG", month=2),
        )
    )
    assert tuple((item.dimension, item.value) for item in facts) == (
        (EconomicStratificationDimension.ENTRY_SIDE, "LONG"),
        (EconomicStratificationDimension.ENTRY_SIDE, "SHORT"),
        (EconomicStratificationDimension.UTC_ENTRY_MONTH, "2025-01"),
        (EconomicStratificationDimension.UTC_ENTRY_MONTH, "2025-02"),
    )
    assert tuple(item.economics.total_realized_net_r for item in facts) == (
        Decimal(-1),
        Decimal(-1),
        Decimal(1),
        Decimal(-3),
    )
    assert tuple(item.economics.outcome_denominator for item in facts) == (2, 1, 2, 1)


@pytest.mark.parametrize(
    ("closed_count", "closed_rate", "expected"),
    (
        (9, Decimal("1"), False),
        (10, Decimal("0.7999"), False),
        (10, Decimal("0.80"), True),
        (10, None, False),
    ),
)
def test_frozen_economic_evaluability_gates(
    closed_count: int, closed_rate: Decimal | None, expected: bool
) -> None:
    assert _is_evaluable(closed_count, closed_rate) is expected


def test_unit_and_cross_run_screen_rules_are_frozen() -> None:
    positive = _economics((_outcome(0, "2"), _outcome(1, "-1"), _outcome(2, "1")))
    negative = _economics((_outcome(0, "1"), _outcome(1, "-2"), _outcome(2, "-1")))
    mixed = _economics((_outcome(0, "10"), _outcome(1, "-1"), _outcome(2, "-1")))
    assert _unit_status(positive, True) is SignalEdgeUnitStatus.POSITIVE
    assert _unit_status(negative, True) is SignalEdgeUnitStatus.NEGATIVE
    assert _unit_status(mixed, True) is SignalEdgeUnitStatus.MIXED
    assert _unit_status(positive, False) is SignalEdgeUnitStatus.INSUFFICIENT
    assert (
        _screen_status(
            (
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.MIXED,
            )
        )
        is SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_POSITIVE
    )
    assert (
        _screen_status(
            (
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.NEGATIVE,
                SignalEdgeUnitStatus.NEGATIVE,
            )
        )
        is SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_NEGATIVE
    )
    assert (
        _screen_status(
            (
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.NEGATIVE,
                SignalEdgeUnitStatus.MIXED,
            )
        )
        is SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_MIXED
    )
    assert (
        _screen_status(
            (
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.POSITIVE,
                SignalEdgeUnitStatus.INSUFFICIENT,
            )
        )
        is SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_INSUFFICIENT
    )


def test_nonpositive_or_nonfinite_authorized_risk_fails_closed() -> None:
    with pytest.raises(ValueError, match="risk"):
        _outcome(0, "1", "0")
    with pytest.raises(ValueError, match="risk"):
        _outcome(0, "1", "NaN")
