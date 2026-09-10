import ast
import runpy
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import adaptive_trading_bot.orders.intent as intent_module
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import (
    EntryIntentPurpose,
    EntryOrderIntentAuthorizer,
    EntrySide,
    InMemoryOrderIntentLedger,
    OrderIntentAuthorizationReasonCode,
    OrderIntentAuthorizationResult,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskReasonCode,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    CandidateAction,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
    RangeStatus,
    StrategyReasonCode,
    TrendPullbackRuleAudit,
)

_JOURNAL_TEST_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
BTC = _JOURNAL_TEST_HELPERS["BTC"]
ETH = _JOURNAL_TEST_HELPERS["ETH"]
NOW = _JOURNAL_TEST_HELPERS["NOW"]
_context = _JOURNAL_TEST_HELPERS["_context"]
_entry = _JOURNAL_TEST_HELPERS["_entry"]


def _authorize(outcome: str = "cleared") -> OrderIntentAuthorizationResult:
    return EntryOrderIntentAuthorizer().authorize(_entry(outcome))


def test_long_cleared_chain_creates_exact_buy_intent() -> None:
    entry = _entry()

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert result.reasons == (
        OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
        OrderIntentAuthorizationReasonCode.SAFETY_CLEARED,
        OrderIntentAuthorizationReasonCode.RISK_APPROVAL_PRESENT,
        OrderIntentAuthorizationReasonCode.SIZING_EXACTLY_PRESERVED,
        OrderIntentAuthorizationReasonCode.ENTRY_INTENT_AUTHORIZED,
    )
    intent = result.intent
    assert intent is not None
    assert intent.side is EntrySide.BUY
    assert (
        intent.quantity
        == entry.safety_decision.approved_quantity
        == (entry.risk_decision.approved_quantity)
    )
    assert (
        intent.approved_notional_quote
        == entry.safety_decision.approved_notional_quote
        == (entry.risk_decision.approved_notional_quote)
    )
    assert (
        intent.approved_risk_quote
        == entry.safety_decision.approved_risk_quote
        == (entry.risk_decision.approved_risk_quote)
    )
    assert (
        intent.stop_basis
        == entry.safety_decision.approved_risk_stop_basis
        == (entry.risk_decision.approved_risk_stop_basis)
    )
    assert intent.reference_price == entry.risk_decision.reference_price
    assert intent.observed_at == intent.authorized_at == NOW
    assert intent.risk_policy_version == entry.risk_decision.risk_policy_version
    assert intent.safety_policy_version == entry.safety_decision.safety_policy_version
    assert intent.authorized_for_execution is True


def test_short_cleared_chain_creates_sell_intent() -> None:
    entry = _entry()
    evaluation = replace(
        entry.strategy_evaluation,
        action=CandidateAction.SHORT_CANDIDATE,
        invalidation_reference_price=Decimal(135),
    )
    # Build the full chain again so Risk remains the only sizing authority.
    from adaptive_trading_bot.journal import DecisionJournalEntry
    from adaptive_trading_bot.risk import OperationalSafetyGate, RiskManager, RiskPortfolioSnapshot

    _health = _JOURNAL_TEST_HELPERS["_health"]

    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
    )
    short_entry = DecisionJournalEntry(
        entry.context,
        evaluation,
        risk,
        OperationalSafetyGate().evaluate(risk, _health(risk)),
    )

    result = EntryOrderIntentAuthorizer().authorize(short_entry)

    assert result.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert result.intent is not None
    assert result.intent.side is EntrySide.SELL


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (
            "no_action",
            (
                OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
                OrderIntentAuthorizationReasonCode.STRATEGY_NO_TRADE,
                OrderIntentAuthorizationReasonCode.RISK_NO_ACTION,
                OrderIntentAuthorizationReasonCode.SAFETY_NO_ACTION,
            ),
        ),
        (
            "risk_blocked",
            (
                OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
                OrderIntentAuthorizationReasonCode.RISK_REJECTED,
                OrderIntentAuthorizationReasonCode.SAFETY_BLOCKED_BY_RISK,
            ),
        ),
        (
            "safe_mode",
            (
                OrderIntentAuthorizationReasonCode.JOURNAL_CHAIN_VALID,
                OrderIntentAuthorizationReasonCode.SAFETY_SAFE_MODE,
            ),
        ),
    ],
)
def test_valid_noncleared_chains_create_no_intent(
    outcome: str, expected: tuple[OrderIntentAuthorizationReasonCode, ...]
) -> None:
    result = _authorize(outcome)

    assert result.status is OrderIntentAuthorizationStatus.NO_INTENT
    assert result.intent is None
    assert result.reasons == expected


@pytest.mark.parametrize(
    ("outcome", "status", "permitted"),
    [
        ("cleared", OperationalSafetyStatus.CLEARED, False),
        ("safe_mode", OperationalSafetyStatus.SAFE_MODE, True),
    ],
)
def test_forged_permission_rejects(
    outcome: str, status: OperationalSafetyStatus, permitted: bool
) -> None:
    entry = _entry(outcome)
    object.__setattr__(
        entry,
        "safety_decision",
        replace(entry.safety_decision, status=status, entry_permitted=permitted),
    )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.PERMISSION_INCONSISTENT in result.reasons
    assert result.intent is None


def test_forged_cleared_with_nonapproved_risk_rejects() -> None:
    entry = _entry("risk_blocked")
    object.__setattr__(
        entry,
        "safety_decision",
        replace(
            entry.safety_decision,
            status=OperationalSafetyStatus.CLEARED,
            entry_permitted=True,
        ),
    )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.PERMISSION_INCONSISTENT in result.reasons


@pytest.mark.parametrize(
    "field_name",
    [
        "approved_quantity",
        "approved_notional_quote",
        "approved_risk_quote",
        "approved_risk_stop_basis",
    ],
)
def test_forged_sizing_mismatch_rejects(field_name: str) -> None:
    entry = _entry()
    original = getattr(entry.safety_decision, field_name)
    assert isinstance(original, Decimal)
    object.__setattr__(
        entry,
        "safety_decision",
        replace(
            entry.safety_decision,
            **{field_name: original + Decimal(1)},
        ),
    )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.SIZING_MISMATCH in result.reasons


@pytest.mark.parametrize(
    "field_name",
    [
        "approved_quantity",
        "approved_notional_quote",
        "approved_risk_quote",
        "approved_risk_stop_basis",
        "reference_price",
    ],
)
@pytest.mark.parametrize("value", [None, Decimal(0), Decimal(-1), Decimal("NaN")])
def test_missing_nonfinite_or_nonpositive_approved_decimal_rejects(
    field_name: str, value: Decimal | None
) -> None:
    entry = _entry()
    risk = replace(
        entry.risk_decision,
        **{field_name: value},
    )
    safety = entry.safety_decision
    evaluation = entry.strategy_evaluation
    if field_name != "reference_price":
        safety = replace(
            safety,
            **{field_name: value},
        )
    if field_name == "approved_risk_stop_basis":
        evaluation = replace(evaluation, invalidation_reference_price=value)
    elif field_name == "reference_price":
        evaluation = replace(evaluation, reference_price=value)
    object.__setattr__(entry, "risk_decision", risk)
    object.__setattr__(entry, "safety_decision", safety)
    object.__setattr__(entry, "strategy_evaluation", evaluation)

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.INVALID_APPROVED_DECIMAL in result.reasons


@pytest.mark.parametrize(
    ("component", "changes"),
    [
        ("identity", {"source_id": SourceId("forged")}),
        ("identity", {"symbol": ETH}),
        ("identity", {"observed_at": NOW + timedelta(minutes=15)}),
        ("identity", {"strategy_id": "forged"}),
        ("identity", {"strategy_version": "forged"}),
        ("risk", {"source_id": SourceId("forged")}),
        ("safety", {"symbol": ETH}),
    ],
)
def test_forged_lineage_rejects(component: str, changes: dict[str, object]) -> None:
    entry = _entry()
    if component == "identity":
        object.__setattr__(
            entry,
            "identity",
            replace(entry.identity, **changes),
        )
    elif component == "risk":
        object.__setattr__(
            entry,
            "risk_decision",
            replace(entry.risk_decision, **changes),
        )
    else:
        object.__setattr__(
            entry,
            "safety_decision",
            replace(entry.safety_decision, **changes),
        )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert result.intent is None


def test_blocker_reason_cannot_coexist_with_authorization() -> None:
    entry = _entry()
    object.__setattr__(
        entry,
        "safety_decision",
        replace(
            entry.safety_decision,
            reasons=(
                *entry.safety_decision.reasons,
                OperationalSafetyReasonCode.STALE_MARKET_DATA,
            ),
        ),
    )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.PERMISSION_INCONSISTENT in result.reasons


@pytest.mark.parametrize(
    "blocker",
    [RiskReasonCode.DAILY_LOSS_LIMIT_REACHED, RiskReasonCode.DRAWDOWN_LIMIT_REACHED],
)
def test_risk_halt_reason_cannot_coexist_with_authorization(
    blocker: RiskReasonCode,
) -> None:
    entry = _entry()
    object.__setattr__(
        entry,
        "risk_decision",
        replace(entry.risk_decision, reasons=(*entry.risk_decision.reasons, blocker)),
    )

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert OrderIntentAuthorizationReasonCode.PERMISSION_INCONSISTENT in result.reasons


def test_identity_is_deterministic_and_rooted_in_decision() -> None:
    left = _authorize().intent
    right = _authorize().intent
    assert left is not None and right is not None

    assert left.identity == right.identity
    assert left.identity.decision_identity == left.decision_identity
    assert left.identity.purpose is EntryIntentPurpose.ENTRY
    assert left.identity.intent_schema_version == "1.0.0"


def test_intent_result_and_snapshot_are_immutable() -> None:
    ledger = InMemoryOrderIntentLedger()
    result = ledger.authorize_and_append(_entry())
    snapshot = ledger.snapshot()
    assert result.intent is not None

    with pytest.raises(FrozenInstanceError):
        result.intent.quantity = Decimal(999)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = OrderIntentAuthorizationStatus.NO_INTENT  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.intents = ()  # type: ignore[misc]


def test_duplicate_decision_is_rejected_without_mutation() -> None:
    entry = _entry()
    ledger = InMemoryOrderIntentLedger()
    first = ledger.authorize_and_append(entry)
    before = ledger.snapshot()

    duplicate = ledger.authorize_and_append(entry)

    assert first.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert duplicate.status is OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
    assert duplicate.reasons == (OrderIntentAuthorizationReasonCode.DUPLICATE_DECISION_INTENT,)
    assert duplicate.intent is None
    assert ledger.snapshot() == before


def test_no_intent_is_not_stored() -> None:
    ledger = InMemoryOrderIntentLedger()

    result = ledger.authorize_and_append(_entry("safe_mode"))

    assert result.status is OrderIntentAuthorizationStatus.NO_INTENT
    assert ledger.snapshot().intents == ()


def test_distinct_strategies_same_time_remain_distinct_and_canonically_ordered() -> None:
    ledger = InMemoryOrderIntentLedger()
    beta = _entry(strategy_id="beta")
    alpha = _entry(strategy_id="alpha")

    ledger.authorize_and_append(beta)
    ledger.authorize_and_append(alpha)

    assert tuple(item.strategy_id for item in ledger.snapshot().intents) == ("alpha", "beta")


def test_symbol_and_source_identity_are_isolated_in_canonical_order() -> None:
    ledger = InMemoryOrderIntentLedger()
    entries = (
        _entry(context=_context(source_id=SourceId("z-source"))),
        _entry(context=_context(symbol=ETH)),
        _entry(),
    )
    for entry in entries:
        assert (
            ledger.authorize_and_append(entry).status is OrderIntentAuthorizationStatus.AUTHORIZED
        )

    identities = tuple(
        (item.source_id.value, str(item.symbol)) for item in ledger.snapshot().intents
    )
    assert identities == (
        ("archive", "BTC/USDT"),
        ("archive", "ETH/USDT"),
        ("z-source", "BTC/USDT"),
    )


def _strategy_specific_evidence() -> tuple[tuple[object, object], ...]:
    return (
        (
            StrategyReasonCode.CONTEXT_READY,
            TrendPullbackRuleAudit(*(None for _ in range(9))),
        ),
        (
            next(iter(BreakoutRetestReasonCode)),
            BreakoutRetestRuleAudit(*(None for _ in range(16))),
        ),
        (
            next(iter(RangeMeanReversionReasonCode)),
            RangeMeanReversionRuleAudit(
                range_status=RangeStatus.INSUFFICIENT_DATA,
                range_reasons=(),
                lower_boundary=None,
                upper_boundary=None,
                midpoint=None,
                range_width=None,
                width_in_atr=None,
                current_atr=None,
                lower_entry_zone_ceiling=None,
                upper_entry_zone_floor=None,
                lower_excursion_floor=None,
                upper_excursion_ceiling=None,
                candle_low=Decimal(100),
                candle_high=Decimal(101),
                candle_close=Decimal(100),
                rsi_14=None,
                momentum_4=None,
                selected_side=None,
                invalidation_reference=None,
                invalidation_distance=None,
            ),
        ),
    )


@pytest.mark.parametrize(("reason", "audit"), _strategy_specific_evidence())
def test_all_v1_strategy_reason_and_audit_types_use_same_public_authorizer(
    reason: object, audit: object
) -> None:
    result = EntryOrderIntentAuthorizer().authorize(_entry(reason=reason, audit=audit))

    assert result.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert result.intent is not None


def test_orders_module_has_no_execution_path_or_forbidden_dependency() -> None:
    path = Path(intent_module.__file__)
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
        "adaptive_trading_bot.storage",
        "adaptive_trading_bot.replay",
        "adaptive_trading_bot.market_data",
        "adaptive_trading_bot.strategies.trend_pullback",
        "adaptive_trading_bot.strategies.breakout_retest",
        "adaptive_trading_bot.strategies.range_mean_reversion",
        "sqlalchemy",
        "ccxt",
    )
    ledger = InMemoryOrderIntentLedger()
    result = ledger.authorize_and_append(_entry())
    assert result.intent is not None

    assert not any(name.startswith(forbidden) for name in imported)
    for forbidden_method in ("submit", "send", "execute", "fill", "replace", "delete"):
        assert not hasattr(ledger, forbidden_method)
    for forbidden_field in (
        "order_id",
        "client_order_id",
        "fill_price",
        "fill_time",
        "trade_id",
        "realized_pnl",
        "position",
        "take_profit",
        "leverage",
        "margin",
    ):
        assert not hasattr(result.intent, forbidden_field)


def test_result_retains_decision_and_stage_statuses() -> None:
    entry = _entry("risk_blocked")

    result = EntryOrderIntentAuthorizer().authorize(entry)

    assert result.decision_identity == entry.identity
    assert result.strategy_action is CandidateAction.LONG_CANDIDATE
    assert result.risk_status is RiskDecisionStatus.REJECTED
    assert result.safety_status is OperationalSafetyStatus.BLOCKED_BY_RISK


def test_identity_contract_has_no_random_component() -> None:
    expected = DecisionIdentity(SourceId("archive"), BTC, NOW, "manual", "1.0.0")
    intent = _authorize().intent
    assert intent is not None

    assert intent.decision_identity == expected
    assert not hasattr(intent.identity, "uuid")
