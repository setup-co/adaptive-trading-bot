import ast
import runpy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import MAX_PREC, Decimal, getcontext, setcontext
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_trading_bot.admission import (
    PendingPortfolioReservation,
    PendingReservationSnapshot,
    PortfolioAdmissionBatchResult,
    PortfolioAdmissionCandidate,
    PortfolioAdmissionGate,
    PortfolioAdmissionPermit,
    PortfolioAdmissionReasonCode,
    PortfolioAdmissionStatus,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    CandidateAction,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    StrategyReasonCode,
    TrendPullbackRuleAudit,
)

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py"))
NOW = _HELPERS["NOW"]
BTC = _HELPERS["BTC"]
ETH = _HELPERS["ETH"]
SOURCE = SourceId("archive")
_context = _HELPERS["_context"]
_manual_evaluation = _HELPERS["_manual_evaluation"]
_health = _HELPERS["_health"]


def _candidate(
    *,
    symbol: TradingSymbol = BTC,
    source_id: SourceId = SOURCE,
    strategy_id: str = "manual",
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    configuration: RiskManagerConfiguration | None = None,
    portfolio: RiskPortfolioSnapshot | None = None,
    reason: object = "reason",
    audit: object = "audit",
) -> PortfolioAdmissionCandidate[object, object]:
    context = _context(source_id=source_id, symbol=symbol)
    evaluation = _manual_evaluation(
        context,
        strategy_id=strategy_id,
        action=action,
        reason=reason,
        audit=audit,
        invalidation=(Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)),
    )
    snapshot = portfolio or RiskPortfolioSnapshot(
        NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)
    )
    risk = RiskManager(configuration or RiskManagerConfiguration()).evaluate(evaluation, snapshot)
    safety = OperationalSafetyGate().evaluate(risk, _health(risk))
    entry = DecisionJournalEntry(context, evaluation, risk, safety)
    authorization = EntryOrderIntentAuthorizer().authorize(entry)
    assert authorization.intent is not None
    return PortfolioAdmissionCandidate(entry, authorization.intent)


def _empty_pending() -> PendingReservationSnapshot:
    return PendingReservationSnapshot(NOW, "USDT")


def _evaluate(
    *candidates: PortfolioAdmissionCandidate[object, object],
    pending: PendingReservationSnapshot | None = None,
) -> PortfolioAdmissionBatchResult:
    return PortfolioAdmissionGate().evaluate_batch(
        candidates,
        pending or _empty_pending(),
    )


@pytest.mark.parametrize("symbol", [BTC, ETH])
def test_one_valid_intent_is_admitted(symbol: TradingSymbol) -> None:
    candidate = _candidate(symbol=symbol)

    result = _evaluate(candidate)

    assert result.status is PortfolioAdmissionStatus.ADMITTED
    assert len(result.permits) == 1
    permit = result.permits[0]
    assert permit.intent_identity == candidate.intent.identity
    assert permit.quantity == candidate.intent.quantity
    assert permit.approved_risk_quote == candidate.intent.approved_risk_quote
    assert permit.approved_notional_quote == candidate.intent.approved_notional_quote
    assert permit.side is candidate.intent.side
    assert permit.stop_basis == candidate.intent.stop_basis
    assert permit.reference_price == candidate.intent.reference_price


def test_btc_and_eth_are_atomically_admitted_at_exact_combined_risk_cap() -> None:
    btc = _candidate(symbol=BTC)
    eth = _candidate(symbol=ETH)

    result = _evaluate(btc, eth)

    assert result.status is PortfolioAdmissionStatus.ADMITTED
    assert len(result.permits) == 2
    assert result.reasons[-3:] == (
        PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP,
        PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_WITHIN_CAP,
        PortfolioAdmissionReasonCode.ADMITTED_FOR_RESERVATION,
    )
    assert sum((permit.approved_risk_quote for permit in result.permits), Decimal(0)) == Decimal(50)


def test_one_unit_above_combined_risk_cap_rejects_whole_set() -> None:
    configuration = RiskManagerConfiguration(
        risk_per_trade_fraction=Decimal("0.0025"),
        max_simultaneous_risk_fraction=Decimal("0.0049"),
    )
    btc = _candidate(symbol=BTC, configuration=configuration)
    eth = _candidate(symbol=ETH, configuration=configuration)

    result = _evaluate(btc, eth)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert not result.permits
    assert PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED in result.reasons
    assert all(
        item.status is PortfolioAdmissionStatus.NO_ADMISSION for item in result.candidate_results
    )


@pytest.mark.parametrize(
    ("gross_fraction", "expected_status", "reason"),
    [
        (
            Decimal("0.0175"),
            PortfolioAdmissionStatus.ADMITTED,
            PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_WITHIN_CAP,
        ),
        (
            Decimal("0.01749"),
            PortfolioAdmissionStatus.NO_ADMISSION,
            PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED,
        ),
    ],
)
def test_exact_and_above_combined_gross_exposure_boundary(
    gross_fraction: Decimal,
    expected_status: PortfolioAdmissionStatus,
    reason: PortfolioAdmissionReasonCode,
) -> None:
    configuration = RiskManagerConfiguration(max_gross_exposure_fraction=gross_fraction)

    result = _evaluate(
        _candidate(symbol=BTC, configuration=configuration),
        _candidate(symbol=ETH, configuration=configuration),
    )

    assert result.status is expected_status
    assert reason in result.reasons


def test_both_cap_failures_have_canonical_order_and_no_subset() -> None:
    configuration = RiskManagerConfiguration(
        max_simultaneous_risk_fraction=Decimal("0.0049"),
        max_gross_exposure_fraction=Decimal("0.01749"),
    )

    result = _evaluate(
        _candidate(symbol=ETH, configuration=configuration),
        _candidate(symbol=BTC, configuration=configuration),
    )

    exceeded = tuple(reason for reason in result.reasons if "EXCEEDED" in reason)
    assert exceeded == (
        PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED,
        PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_CAP_EXCEEDED,
    )
    assert result.permits == ()


def _reservation(
    candidate: PortfolioAdmissionCandidate[object, object],
) -> PendingPortfolioReservation:
    return PendingPortfolioReservation(
        candidate.intent.identity,
        candidate.intent.source_id,
        candidate.intent.symbol,
        Decimal(1),
        Decimal(1),
    )


def test_pending_reservations_consume_capacity_before_new_batch() -> None:
    btc = _candidate(symbol=BTC)
    prior = _candidate(symbol=ETH, strategy_id="prior")
    pending = PendingReservationSnapshot(
        NOW,
        "USDT",
        (
            PendingPortfolioReservation(
                prior.intent.identity,
                prior.intent.source_id,
                prior.intent.symbol,
                Decimal("25.00000000000000000000000000000001"),
                Decimal(1),
            ),
        ),
    )

    result = _evaluate(btc, pending=pending)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_CAP_EXCEEDED in result.reasons


def test_duplicate_pending_reservation_rejects_batch() -> None:
    candidate = _candidate()
    reservation = _reservation(_candidate(symbol=ETH, strategy_id="prior"))

    result = _evaluate(
        candidate,
        pending=PendingReservationSnapshot(NOW, "USDT", (reservation, reservation)),
    )

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.DUPLICATE_PENDING_RESERVATION,)


def test_already_reserved_candidate_rejects_batch() -> None:
    candidate = _candidate()

    result = _evaluate(
        candidate,
        pending=PendingReservationSnapshot(NOW, "USDT", (_reservation(candidate),)),
    )

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.ALREADY_RESERVED_INTENT,)


def test_different_candidate_for_pending_symbol_receives_no_admission() -> None:
    pending_candidate = _candidate(
        symbol=BTC,
        source_id=SourceId("different-source"),
        strategy_id="earlier",
    )
    later_candidate = _candidate(symbol=BTC, strategy_id="later")
    pending = PendingReservationSnapshot(
        NOW,
        "USDT",
        (_reservation(pending_candidate),),
    )

    result = _evaluate(later_candidate, pending=pending)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert result.permits == ()
    assert result.candidate_results[0].reasons == (
        PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATED,
        PortfolioAdmissionReasonCode.PENDING_SYMBOL_ALREADY_RESERVED,
    )


def test_pending_btc_blocks_btc_from_cap_set_but_unrelated_eth_admits_at_boundary() -> None:
    configuration = RiskManagerConfiguration(max_gross_exposure_fraction=Decimal("0.0175"))
    pending_candidate = _candidate(symbol=BTC, strategy_id="earlier")
    blocked_btc = _candidate(symbol=BTC, strategy_id="later", configuration=configuration)
    eth = _candidate(symbol=ETH, configuration=configuration)
    pending = PendingReservationSnapshot(
        NOW,
        "USDT",
        (
            PendingPortfolioReservation(
                pending_candidate.intent.identity,
                pending_candidate.intent.source_id,
                pending_candidate.intent.symbol,
                Decimal(25),
                Decimal("87.5"),
            ),
        ),
    )

    result = _evaluate(blocked_btc, eth, pending=pending)
    permuted = _evaluate(eth, blocked_btc, pending=pending)

    assert result == permuted
    assert result.status is PortfolioAdmissionStatus.ADMITTED
    assert tuple(permit.symbol for permit in result.permits) == (ETH,)
    btc_result = next(
        item for item in result.candidate_results if item.decision_identity.symbol == BTC
    )
    eth_result = next(
        item for item in result.candidate_results if item.decision_identity.symbol == ETH
    )
    assert btc_result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert btc_result.reasons[-1] is PortfolioAdmissionReasonCode.PENDING_SYMBOL_ALREADY_RESERVED
    assert eth_result.status is PortfolioAdmissionStatus.ADMITTED
    assert PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP in eth_result.reasons
    assert PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_WITHIN_CAP in eth_result.reasons


def test_distinct_pending_intents_for_same_symbol_are_inconsistent() -> None:
    first = _candidate(symbol=BTC, strategy_id="first")
    second = _candidate(symbol=BTC, strategy_id="second")
    candidate = _candidate(symbol=ETH)

    result = _evaluate(
        candidate,
        pending=PendingReservationSnapshot(
            NOW,
            "USDT",
            (_reservation(second), _reservation(first)),
        ),
    )

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.permits == ()
    assert result.reasons == (PortfolioAdmissionReasonCode.DUPLICATE_PENDING_SYMBOL_RESERVATION,)


def test_pending_snapshot_must_match_batch_instant() -> None:
    result = _evaluate(
        _candidate(),
        pending=PendingReservationSnapshot(NOW + timedelta(microseconds=1), "USDT"),
    )

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.PENDING_SNAPSHOT_TIME_MISMATCH,)


@pytest.mark.parametrize("opposite_side", [False, True])
def test_same_symbol_multi_strategy_conflict_has_no_winner(opposite_side: bool) -> None:
    first = _candidate(strategy_id="z-strategy")
    second = _candidate(strategy_id="a-strategy")
    if opposite_side:
        context = second.journal_entry.context
        evaluation = replace(
            second.journal_entry.strategy_evaluation,
            action=CandidateAction.SHORT_CANDIDATE,
            invalidation_reference_price=Decimal(135),
        )
        risk = RiskManager().evaluate(
            evaluation,
            RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
        )
        entry = DecisionJournalEntry(
            context, evaluation, risk, OperationalSafetyGate().evaluate(risk, _health(risk))
        )
        intent = EntryOrderIntentAuthorizer().authorize(entry).intent
        assert intent is not None
        second = PortfolioAdmissionCandidate(entry, intent)

    result = _evaluate(first, second)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert result.permits == ()
    assert all(
        item.reasons[-1] is PortfolioAdmissionReasonCode.STRATEGY_CONFLICT_NO_SELECTION
        for item in result.candidate_results
    )


def test_same_symbol_cross_source_same_side_conflict_has_no_winner() -> None:
    first = _candidate(source_id=SourceId("source-a"), strategy_id="same-family")
    second = _candidate(source_id=SourceId("source-b"), strategy_id="same-family")

    result = _evaluate(first, second)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert result.permits == ()
    assert {item.decision_identity.source_id.value for item in result.candidate_results} == {
        "source-a",
        "source-b",
    }
    assert all(
        item.reasons[-1] is PortfolioAdmissionReasonCode.STRATEGY_CONFLICT_NO_SELECTION
        for item in result.candidate_results
    )


def test_same_symbol_cross_source_opposite_side_conflict_has_no_winner() -> None:
    long_candidate = _candidate(source_id=SourceId("source-a"), strategy_id="same-family")
    short_candidate = _candidate(
        source_id=SourceId("source-b"),
        strategy_id="same-family",
        action=CandidateAction.SHORT_CANDIDATE,
    )

    result = _evaluate(short_candidate, long_candidate)

    assert result.status is PortfolioAdmissionStatus.NO_ADMISSION
    assert result.permits == ()
    assert all(
        item.reasons[-1] is PortfolioAdmissionReasonCode.STRATEGY_CONFLICT_NO_SELECTION
        for item in result.candidate_results
    )


def test_cross_source_btc_conflict_contributes_zero_and_eth_admits_at_exact_caps() -> None:
    configuration = RiskManagerConfiguration(
        max_simultaneous_risk_fraction=Decimal("0.0025"),
        max_gross_exposure_fraction=Decimal("0.00875"),
    )
    btc_a = _candidate(
        source_id=SourceId("source-a"),
        strategy_id="same-family",
        configuration=configuration,
    )
    btc_b = _candidate(
        source_id=SourceId("source-b"),
        strategy_id="same-family",
        configuration=configuration,
    )
    eth = _candidate(symbol=ETH, configuration=configuration)

    result = _evaluate(btc_b, eth, btc_a)
    permuted = _evaluate(eth, btc_a, btc_b)

    assert result == permuted
    assert result.status is PortfolioAdmissionStatus.ADMITTED
    assert tuple(permit.symbol for permit in result.permits) == (ETH,)
    assert result.permits[0].approved_risk_quote == Decimal(25)
    assert result.permits[0].approved_notional_quote == Decimal("87.5")
    assert PortfolioAdmissionReasonCode.COMBINED_SIMULTANEOUS_RISK_WITHIN_CAP in result.reasons
    assert PortfolioAdmissionReasonCode.COMBINED_GROSS_EXPOSURE_WITHIN_CAP in result.reasons
    assert (
        sum(
            item.status is PortfolioAdmissionStatus.NO_ADMISSION
            for item in result.candidate_results
        )
        == 2
    )


def test_unrelated_eth_is_still_admitted_when_btc_conflicts() -> None:
    btc_a = _candidate(strategy_id="a")
    btc_b = _candidate(strategy_id="b")
    eth = _candidate(symbol=ETH, strategy_id="c")

    result = _evaluate(btc_b, eth, btc_a)

    assert result.status is PortfolioAdmissionStatus.ADMITTED
    assert tuple(permit.symbol for permit in result.permits) == (ETH,)
    assert (
        sum(
            item.status is PortfolioAdmissionStatus.NO_ADMISSION
            for item in result.candidate_results
        )
        == 2
    )


def test_exact_duplicate_decision_is_integrity_rejection_not_conflict() -> None:
    candidate = _candidate()

    result = _evaluate(candidate, candidate)

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.DUPLICATE_DECISION,)


def test_independently_valid_different_account_baselines_reject() -> None:
    btc = _candidate(symbol=BTC)
    different_portfolio = RiskPortfolioSnapshot(
        NOW, "USDT", Decimal(9000), Decimal(9000), Decimal(9000)
    )
    eth = _candidate(symbol=ETH, portfolio=different_portfolio)

    result = _evaluate(btc, eth)

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.PORTFOLIO_BASELINE_MISMATCH,)


def test_independently_valid_different_risk_limit_policy_rejects() -> None:
    btc = _candidate(symbol=BTC)
    configuration = RiskManagerConfiguration(max_daily_loss_fraction=Decimal("0.02"))
    eth = _candidate(symbol=ETH, configuration=configuration)

    result = _evaluate(btc, eth)

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.PORTFOLIO_BASELINE_MISMATCH,)


@pytest.mark.parametrize(
    "field_name",
    [
        "account_equity_quote",
        "current_open_risk_quote",
        "simultaneous_risk_cap_quote",
        "gross_exposure_cap_quote",
        "risk_policy_version",
        "max_daily_loss_fraction",
    ],
)
def test_inconsistent_portfolio_baseline_rejects(field_name: str) -> None:
    btc = _candidate(symbol=BTC)
    eth = _candidate(symbol=ETH)
    changed = "other" if field_name == "risk_policy_version" else Decimal("999")
    object.__setattr__(eth.journal_entry.risk_decision, field_name, changed)

    result = _evaluate(btc, eth)

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.permits == ()


def test_raw_mutated_intent_is_rejected_by_exact_reauthorization() -> None:
    candidate = _candidate()
    object.__setattr__(candidate.intent, "quantity", candidate.intent.quantity + Decimal(1))

    result = _evaluate(candidate)

    assert result.status is PortfolioAdmissionStatus.REJECTED_INCONSISTENT
    assert result.reasons == (PortfolioAdmissionReasonCode.AUTHORITY_REVALIDATION_FAILED,)


def test_input_permutation_is_exactly_invariant() -> None:
    btc = _candidate(symbol=BTC, strategy_id="z")
    eth = _candidate(symbol=ETH, strategy_id="a")

    assert _evaluate(btc, eth) == _evaluate(eth, btc)


def test_decimal_math_is_independent_of_ambient_context() -> None:
    candidates = (_candidate(symbol=BTC), _candidate(symbol=ETH))
    original = getcontext().copy()
    try:
        getcontext().prec = 2
        getcontext().rounding = "ROUND_DOWN"
        low_precision = _evaluate(*candidates)
        getcontext().prec = MAX_PREC
        high_precision = _evaluate(*candidates)
    finally:
        setcontext(original)

    assert low_precision == high_precision


def test_results_snapshots_and_permits_are_immutable_and_permit_is_not_forgeable() -> None:
    candidate = _candidate()
    result = _evaluate(candidate)
    permit = result.permits[0]

    with pytest.raises(FrozenInstanceError):
        result.permits = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        permit.quantity = Decimal("1E+99")  # type: ignore[misc]
    with pytest.raises(TypeError, match="only be created by its gate"):
        cast(Any, PortfolioAdmissionPermit)(quantity=Decimal("1E+99"))
    with pytest.raises(TypeError, match="only be created by its gate"):
        replace(permit, quantity=Decimal("1E+99"))


@pytest.mark.parametrize(
    ("reason", "audit_type"),
    [
        (StrategyReasonCode.CONTEXT_READY, TrendPullbackRuleAudit),
        (BreakoutRetestReasonCode.CONTEXT_READY, BreakoutRetestRuleAudit),
        (RangeMeanReversionReasonCode.VALIDATED_RANGE_ACTIVE, RangeMeanReversionRuleAudit),
    ],
)
def test_all_v1_strategy_reason_and_audit_types_are_generic(
    reason: object, audit_type: type[object]
) -> None:
    audit = cast(Any, audit_type)(**{item.name: None for item in cast(Any, fields)(audit_type)})
    result = _evaluate(_candidate(reason=reason, audit=audit))

    assert result.status is PortfolioAdmissionStatus.ADMITTED


def test_production_module_has_no_forbidden_dependencies_or_execution_behavior() -> None:
    path = Path(__file__).parents[2] / "src" / "adaptive_trading_bot" / "admission" / "portfolio.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        forbidden in imported
        for imported in imports
        for forbidden in ("paper", "backtest", "exchange", "sqlalchemy", "requests", "httpx")
    )
    result = _evaluate(_candidate())
    assert not hasattr(result, "fill")
    assert not hasattr(result, "order")
    assert not hasattr(result, "position")
