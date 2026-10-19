from __future__ import annotations

import runpy
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, getcontext, localcontext, setcontext
from pathlib import Path
from typing import Literal, cast

import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import (
    DecisionJournalBatch,
    DecisionJournalEntry,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.orders import (
    EntryOrderIntentAuthorizer,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import FAMILIES
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    RiskPositionExposure,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID,
    BreakoutRetestConfiguration,
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    BreakoutRetestStateError,
    BreakoutRetestStrategy,
    BreakoutRetestZoneBoundary,
    BreakoutRetestZoneInvalidationAudit,
    BreakoutRetestZoneInvalidationReasonCode,
    BreakoutRetestZoneInvalidationStrategy,
    BreakoutSide,
    CandidateAction,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_breakout_retest.py")))
_strategy_context = cast(Callable[..., StrategyEvaluationContext], _HELPERS["strategy_context"])
_retest_context = cast(Callable[..., StrategyEvaluationContext], _HELPERS["retest_context"])
_waiting_context = cast(Callable[..., StrategyEvaluationContext], _HELPERS["waiting_context"])
_REPLAY_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_breakout_retest_replay.py")))
_evaluate_replay = cast(
    Callable[
        ...,
        tuple[
            tuple[StrategyEvaluationContext, ...],
            tuple[StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit], ...],
        ],
    ],
    _REPLAY_HELPERS["evaluate_replay"],
)

START = cast(datetime, _HELPERS["START"])
BTC = cast(TradingSymbol, _HELPERS["BTC"])
ETH = cast(TradingSymbol, _HELPERS["ETH"])
SOURCE_A = cast(SourceId, _HELPERS["SOURCE_A"])
SOURCE_B = cast(SourceId, _HELPERS["SOURCE_B"])
_NUMERIC_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def _decimal34(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext(_NUMERIC_CONTEXT):
        return numerator / denominator


def _candidate_pair(
    side: BreakoutSide,
    *,
    candle_extreme: Decimal | None = None,
    close: Decimal | None = None,
) -> tuple[
    StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit],
    StrategyEvaluation[
        BreakoutRetestZoneInvalidationReasonCode,
        BreakoutRetestZoneInvalidationAudit,
    ],
]:
    baseline = BreakoutRetestStrategy()
    variant = BreakoutRetestZoneInvalidationStrategy()
    opening = _strategy_context(side=side)
    baseline.evaluate(opening)
    variant.evaluate(opening)
    changes: dict[str, object] = {}
    if side is BreakoutSide.LONG:
        if candle_extreme is not None:
            changes["candle_low"] = candle_extreme
    elif candle_extreme is not None:
        changes["candle_high"] = candle_extreme
    if close is not None:
        changes["candle_close"] = close
        changes["candle_open"] = close - 1 if side is BreakoutSide.LONG else close + 1
        changes["candle_high" if side is BreakoutSide.LONG else "candle_low"] = (
            close + 1 if side is BreakoutSide.LONG else close - 1
        )
    context = _retest_context(side, **changes)
    return baseline.evaluate(context), variant.evaluate(context)


@pytest.mark.parametrize(
    ("side", "old_stop", "zone_stop", "boundary"),
    [
        (BreakoutSide.LONG, Decimal(99), Decimal(98), BreakoutRetestZoneBoundary.FLOOR),
        (BreakoutSide.SHORT, Decimal(101), Decimal(102), BreakoutRetestZoneBoundary.CEILING),
    ],
)
def test_real_stateful_candidate_selects_same_decision_zone_boundary(
    side: BreakoutSide,
    old_stop: Decimal,
    zone_stop: Decimal,
    boundary: BreakoutRetestZoneBoundary,
) -> None:
    baseline, variant = _candidate_pair(side, candle_extreme=old_stop)

    assert baseline.action is variant.action
    assert baseline.invalidation_reference_price == old_stop
    assert variant.strategy_id == "breakout_retest_zone_invalidation"
    assert variant.strategy_version == "0.1.0"
    assert variant.invalidation_reference_price == zone_stop
    assert variant.rule_audit.policy_id == BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID
    assert variant.rule_audit.selected_zone_boundary is boundary
    assert variant.rule_audit.original_candle_invalidation_reference == old_stop
    assert variant.rule_audit.selected_zone_invalidation_reference == zone_stop
    assert variant.rule_audit.selected_zone_invalidation_distance == Decimal(3)
    assert variant.rule_audit.underlying_strategy_id == "breakout_retest"
    assert variant.rule_audit.underlying_strategy_version == "1.0.0"


def test_underlying_no_trade_expiry_cancellation_and_stale_context_are_preserved() -> None:
    configuration = BreakoutRetestConfiguration(max_retest_wait_bars=1)
    variant = BreakoutRetestZoneInvalidationStrategy(configuration)
    armed = variant.evaluate(_strategy_context())
    waiting = variant.evaluate(_waiting_context(BreakoutSide.LONG))
    expired = variant.evaluate(_strategy_context(observed_at=START + timedelta(minutes=30)))

    assert armed.action is waiting.action is expired.action is CandidateAction.NO_TRADE
    assert armed.strategy_id == waiting.strategy_id == expired.strategy_id == variant.strategy_id
    assert BreakoutRetestReasonCode.SETUP_EXPIRED in (expired.rule_audit.underlying_reasons_against)

    cancelled = BreakoutRetestZoneInvalidationStrategy()
    cancelled.evaluate(_strategy_context())
    result = cancelled.evaluate(
        _strategy_context(
            observed_at=START + timedelta(minutes=15),
            ready=False,
        )
    )
    assert result.action is CandidateAction.NO_TRADE
    assert BreakoutRetestReasonCode.CONTEXT_NOT_READY in (
        result.rule_audit.underlying_reasons_against
    )
    assert BreakoutRetestReasonCode.SETUP_CANCELLED in (
        result.rule_audit.underlying_reasons_against
    )

    for change, expected in (
        ({"ready": False}, BreakoutRetestReasonCode.CONTEXT_NOT_READY),
        ({"warmed": False}, BreakoutRetestReasonCode.FEATURES_NOT_FULLY_WARMED),
    ):
        not_ready = BreakoutRetestZoneInvalidationStrategy().evaluate(_strategy_context(**change))
        assert not_ready.action is CandidateAction.NO_TRADE
        assert expected in not_ready.rule_audit.underlying_reasons_against


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_equal_candle_and_zone_boundary_is_truthfully_accepted(side: BreakoutSide) -> None:
    expected = Decimal(98 if side is BreakoutSide.LONG else 102)

    baseline, variant = _candidate_pair(side, candle_extreme=expected)

    assert variant.action is baseline.action
    assert baseline.invalidation_reference_price == expected
    assert variant.invalidation_reference_price == expected
    assert variant.rule_audit.original_candle_invalidation_reference == expected
    assert variant.rule_audit.selected_zone_invalidation_reference == expected


@pytest.mark.parametrize(
    ("side", "close"),
    [(BreakoutSide.LONG, Decimal("128.01")), (BreakoutSide.SHORT, Decimal("71.99"))],
)
def test_new_zone_distance_over_three_atr_fails_and_consumed_setup_cannot_retry(
    side: BreakoutSide, close: Decimal
) -> None:
    variant = BreakoutRetestZoneInvalidationStrategy()
    variant.evaluate(_strategy_context(side=side))
    changes = {
        "candle_open": close - 1 if side is BreakoutSide.LONG else close + 1,
        "candle_close": close,
        "candle_low": Decimal(99) if side is BreakoutSide.LONG else close - 1,
        "candle_high": close + 1 if side is BreakoutSide.LONG else Decimal(101),
    }

    rejected = variant.evaluate(_retest_context(side, **changes))

    assert rejected.action is CandidateAction.NO_TRADE
    assert rejected.reasons_against == (
        BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_TOO_WIDE,
    )
    assert variant.armed_state(SOURCE_A, BTC) is None
    later = variant.evaluate(_retest_context(side, observed_at=START + timedelta(minutes=30)))
    assert later.action is CandidateAction.NO_TRADE


class _StaticUnderlying:
    def __init__(
        self,
        evaluation: StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit],
    ) -> None:
        self._evaluation = evaluation

    @property
    def strategy_id(self) -> str:
        return "breakout_retest"

    @property
    def strategy_version(self) -> str:
        return "1.0.0"

    def evaluate(
        self, context: StrategyEvaluationContext
    ) -> StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit]:
        del context
        return self._evaluation


AuditMutation = Literal["missing-floor", "wrong-floor", "wrong-atr", "future-breakout"]


@pytest.mark.parametrize(
    "mutation",
    ["missing-floor", "wrong-floor", "wrong-atr", "future-breakout"],
)
def test_missing_or_inconsistent_candidate_audit_fails_closed(
    mutation: AuditMutation,
) -> None:
    baseline = BreakoutRetestStrategy()
    baseline.evaluate(_strategy_context())
    context = _retest_context(BreakoutSide.LONG, candle_low=Decimal(99))
    candidate = baseline.evaluate(context)
    if mutation == "missing-floor":
        forged_audit = replace(candidate.rule_audit, retest_floor=None)
    elif mutation == "wrong-floor":
        forged_audit = replace(candidate.rule_audit, retest_floor=Decimal("97.99"))
    elif mutation == "wrong-atr":
        forged_audit = replace(candidate.rule_audit, atr_14=Decimal(11))
    else:
        forged_audit = replace(
            candidate.rule_audit,
            breakout_observed_at=START + timedelta(minutes=15),
        )
    forged = replace(candidate, rule_audit=forged_audit)
    variant = BreakoutRetestZoneInvalidationStrategy()
    object.__setattr__(variant, "_underlying_strategy", _StaticUnderlying(forged))

    result = variant.evaluate(context)

    assert result.action is CandidateAction.NO_TRADE
    assert result.reference_price is None
    assert result.invalidation_reference_price is None
    assert result.reasons_against == (
        BreakoutRetestZoneInvalidationReasonCode.UNDERLYING_CANDIDATE_AUDIT_INVALID,
    )


def test_source_symbol_instances_and_timestamp_progression_are_isolated() -> None:
    variant = BreakoutRetestZoneInvalidationStrategy()
    for source_id, symbol in ((SOURCE_A, BTC), (SOURCE_A, ETH), (SOURCE_B, BTC)):
        variant.evaluate(_strategy_context(source_id=source_id, symbol=symbol))
    results = tuple(
        variant.evaluate(
            _retest_context(
                BreakoutSide.LONG,
                source_id=source_id,
                symbol=symbol,
                candle_low=Decimal(99),
            )
        )
        for source_id, symbol in ((SOURCE_A, BTC), (SOURCE_A, ETH), (SOURCE_B, BTC))
    )
    assert all(result.action is CandidateAction.LONG_CANDIDATE for result in results)

    with pytest.raises(BreakoutRetestStateError, match="exactly one 15m"):
        variant.evaluate(_strategy_context(observed_at=START, source_id=SOURCE_A, symbol=BTC))

    fresh_after_caller_gap_reset = BreakoutRetestZoneInvalidationStrategy()
    fresh = fresh_after_caller_gap_reset.evaluate(
        _strategy_context(observed_at=START + timedelta(hours=4))
    )
    assert fresh.action is CandidateAction.NO_TRADE
    assert BreakoutRetestReasonCode.BREAKOUT_ARMED in fresh.rule_audit.underlying_reasons_for


def test_ambient_decimal_context_does_not_change_zone_projection() -> None:
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        first = _candidate_pair(BreakoutSide.LONG, candle_extreme=Decimal("98.123456"))[1]
        setcontext(Context(prec=50))
        second = _candidate_pair(BreakoutSide.LONG, candle_extreme=Decimal("98.123456"))[1]
    finally:
        setcontext(original)

    assert first == second


def _portfolio(
    observed_at: datetime,
    *,
    equity: Decimal = Decimal(10000),
    positions: tuple[RiskPositionExposure, ...] = (),
) -> RiskPortfolioSnapshot:
    return RiskPortfolioSnapshot(
        observed_at=observed_at,
        quote_currency="USDT",
        account_equity_quote=equity,
        day_start_equity_quote=Decimal(10000),
        peak_equity_quote=Decimal(10000),
        positions=positions,
    )


def _healthy(
    context: StrategyEvaluationContext, *, stale: bool = False
) -> OperationalHealthSnapshot:
    return OperationalHealthSnapshot(
        source_id=context.source_id,
        symbol=context.symbol,
        observed_at=context.observed_at,
        market_data_observed_at=(
            context.observed_at - timedelta(minutes=5) if stale else context.observed_at
        ),
        feed_integrity_state=FeedIntegrityState.OK,
        exchange_api_health_state=ExchangeApiHealthState.HEALTHY,
        spread_bps=Decimal(2),
        time_sync_offset_ms=Decimal(0),
        volatility_safety_state=VolatilitySafetyState.CLASSIFIED_NORMAL,
        suspicious_price_anomaly=False,
        consecutive_order_rejections=0,
    )


@pytest.mark.parametrize("side", tuple(BreakoutSide))
def test_risk_safety_authorization_and_journal_preserve_zone_stop(side: BreakoutSide) -> None:
    variant = BreakoutRetestZoneInvalidationStrategy()
    variant.evaluate(_strategy_context(side=side))
    context = _retest_context(side, candle_low=Decimal(99), candle_high=Decimal(101))
    evaluation = variant.evaluate(context)
    risk = RiskManager().evaluate(evaluation, _portfolio(context.observed_at))
    safety = OperationalSafetyGate().evaluate(risk, _healthy(context))
    entry = DecisionJournalEntry(context, evaluation, risk, safety)
    journal = InMemoryDecisionJournal[
        BreakoutRetestZoneInvalidationReasonCode, BreakoutRetestZoneInvalidationAudit
    ]()
    journal.append_batch(DecisionJournalBatch(context.observed_at, (entry,)))
    authorization = EntryOrderIntentAuthorizer().authorize(journal.snapshot().entries[0])

    expected_reference = Decimal(101 if side is BreakoutSide.LONG else 99)
    expected_stop = Decimal(98 if side is BreakoutSide.LONG else 102)
    expected_quantity = _decimal34(Decimal(25), Decimal(3))
    assert risk.status is RiskDecisionStatus.APPROVED
    assert safety.status is OperationalSafetyStatus.CLEARED
    assert risk.reference_price == expected_reference
    assert risk.approved_risk_stop_basis == expected_stop
    assert risk.approved_quantity == expected_quantity
    with localcontext(_NUMERIC_CONTEXT):
        expected_notional = expected_quantity * expected_reference
    assert risk.approved_notional_quote == expected_notional
    assert risk.approved_risk_quote == Decimal(25)
    assert authorization.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert authorization.intent is not None
    assert authorization.intent.stop_basis == expected_stop
    assert authorization.intent.quantity == expected_quantity
    assert entry.strategy_evaluation.rule_audit.policy_id == (
        BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID
    )


def test_wider_stop_reduces_risk_limited_quantity_but_exposure_cap_is_sovereign() -> None:
    baseline, variant = _candidate_pair(BreakoutSide.LONG, candle_extreme=Decimal(99))
    observed_at = variant.observed_at
    portfolio = _portfolio(observed_at)
    baseline_risk = RiskManager().evaluate(baseline, portfolio)
    variant_risk = RiskManager().evaluate(variant, portfolio)

    assert baseline_risk.approved_quantity == Decimal("12.5")
    assert variant_risk.approved_quantity == _decimal34(Decimal(25), Decimal(3))
    assert baseline_risk.approved_risk_quote == variant_risk.approved_risk_quote == Decimal(25)

    capped = RiskManager(RiskManagerConfiguration(max_gross_exposure_fraction=Decimal("0.01")))
    capped_baseline = capped.evaluate(baseline, portfolio)
    capped_variant = capped.evaluate(variant, portfolio)
    assert (
        capped_baseline.approved_quantity
        == capped_variant.approved_quantity
        == _decimal34(Decimal(100), Decimal(101))
    )
    assert capped_variant.approved_risk_quote == _decimal34(Decimal(300), Decimal(101))
    assert capped_baseline.approved_risk_quote == _decimal34(Decimal(200), Decimal(101))


def test_actual_risk_and_safety_rejections_remain_authoritative() -> None:
    variant_strategy = BreakoutRetestZoneInvalidationStrategy()
    variant_strategy.evaluate(_strategy_context())
    context = _retest_context(BreakoutSide.LONG, candle_low=Decimal(99))
    evaluation = variant_strategy.evaluate(context)
    occupied = RiskPositionExposure(BTC, Decimal(1), Decimal(100), Decimal(3))
    rejected_risk = RiskManager().evaluate(
        evaluation,
        _portfolio(context.observed_at, positions=(occupied,)),
    )
    blocked = OperationalSafetyGate().evaluate(rejected_risk, _healthy(context))
    approved_risk = RiskManager().evaluate(evaluation, _portfolio(context.observed_at))
    safe_mode = OperationalSafetyGate().evaluate(approved_risk, _healthy(context, stale=True))

    assert rejected_risk.status is RiskDecisionStatus.REJECTED
    assert blocked.status is OperationalSafetyStatus.BLOCKED_BY_RISK
    assert safe_mode.status is OperationalSafetyStatus.SAFE_MODE
    assert (
        EntryOrderIntentAuthorizer()
        .authorize(DecisionJournalEntry(context, evaluation, rejected_risk, blocked))
        .status
        is OrderIntentAuthorizationStatus.NO_INTENT
    )
    assert (
        EntryOrderIntentAuthorizer()
        .authorize(DecisionJournalEntry(context, evaluation, approved_risk, safe_mode))
        .status
        is OrderIntentAuthorizationStatus.NO_INTENT
    )


def test_original_strategy_defaults_and_identity_remain_unchanged() -> None:
    baseline = BreakoutRetestStrategy()
    assert baseline.strategy_id == "breakout_retest"
    assert baseline.strategy_version == "1.0.0"
    assert baseline.configuration == BreakoutRetestConfiguration()
    assert BreakoutRetestZoneInvalidationStrategy.strategy_id not in {
        "trend_pullback",
        "breakout_retest",
        "range_mean_reversion",
    }
    assert BreakoutRetestZoneInvalidationStrategy.strategy_id not in FAMILIES


def test_synthetic_replay_feature_regime_pipeline_reaches_zone_candidate(
    tmp_path: Path,
) -> None:
    contexts, baseline = _evaluate_replay(tmp_path / "zone-pipeline")
    future_contexts, _ = _evaluate_replay(tmp_path / "zone-pipeline-with-future", extra_candles=4)
    strategy = BreakoutRetestZoneInvalidationStrategy()
    future_strategy = BreakoutRetestZoneInvalidationStrategy()

    projected = tuple(strategy.evaluate(context) for context in contexts)
    projected_with_future = tuple(future_strategy.evaluate(context) for context in future_contexts)

    assert projected == projected_with_future
    assert baseline[-1].action is CandidateAction.LONG_CANDIDATE
    assert projected[-1].action is CandidateAction.LONG_CANDIDATE
    assert projected[-1].observed_at == contexts[-1].observed_at
    assert projected[-1].reference_price == baseline[-1].reference_price == Decimal(902)
    assert baseline[-1].invalidation_reference_price == Decimal(901)
    assert projected[-1].invalidation_reference_price == (
        projected[-1].rule_audit.underlying_rule_audit.retest_floor
    )
    assert projected[-1].invalidation_reference_price != (baseline[-1].invalidation_reference_price)
