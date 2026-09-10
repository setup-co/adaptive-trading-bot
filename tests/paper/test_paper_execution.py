import ast
import runpy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

import adaptive_trading_bot.execution.simulation as shared_execution_module
import adaptive_trading_bot.paper.execution as paper_module
from adaptive_trading_bot.backtest import (
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    MarketOrderRequest,
    OrderSide,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryOrderIntent,
    EntryOrderIntentAuthorizer,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.paper import (
    PaperAuthorizationError,
    PaperExecutionObservation,
    PaperObservationError,
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_JOURNAL_TEST_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
NOW = _JOURNAL_TEST_HELPERS["NOW"]
BTC = _JOURNAL_TEST_HELPERS["BTC"]
ETH = _JOURNAL_TEST_HELPERS["ETH"]
SOURCE = SourceId("archive")
_entry = _JOURNAL_TEST_HELPERS["_entry"]
_health = _JOURNAL_TEST_HELPERS["_health"]


def _authority(
    *, short: bool = False
) -> tuple[DecisionJournalEntry[object, object], EntryOrderIntent]:
    entry = _entry()
    if short:
        evaluation = replace(
            entry.strategy_evaluation,
            action=CandidateAction.SHORT_CANDIDATE,
            invalidation_reference_price=Decimal(135),
        )
        risk = RiskManager().evaluate(
            evaluation,
            RiskPortfolioSnapshot(NOW, "USDT", Decimal(10000), Decimal(10000), Decimal(10000)),
        )
        entry = DecisionJournalEntry(
            entry.context,
            evaluation,
            risk,
            OperationalSafetyGate().evaluate(risk, _health(risk)),
        )
    result = EntryOrderIntentAuthorizer().authorize(entry)
    assert result.status is OrderIntentAuthorizationStatus.AUTHORIZED
    assert result.intent is not None
    return entry, result.intent


def _observation(
    observed_at: datetime = NOW,
    *,
    source_id: SourceId = SOURCE,
    symbol: TradingSymbol = BTC,
    reference_price: Decimal = Decimal(100),
) -> PaperExecutionObservation:
    return PaperExecutionObservation(source_id, symbol, observed_at, reference_price)


@pytest.mark.parametrize(("short", "side"), [(False, EntrySide.BUY), (True, EntrySide.SELL)])
def test_valid_authority_starts_pending(short: bool, side: EntrySide) -> None:
    entry, intent = _authority(short=short)
    costs = SimulatedExecutionCostConfiguration(latency=timedelta(seconds=7))

    lifecycle = PaperOrderSimulator().start(entry, intent, costs)

    assert lifecycle.status is PaperOrderStatus.PENDING
    assert lifecycle.intent is intent
    assert lifecycle.intent.side is side
    assert lifecycle.eligible_at == intent.authorized_at + timedelta(seconds=7)
    assert lifecycle.last_observed_at is None
    assert lifecycle.fill is None
    assert lifecycle.identity.intent_identity == intent.identity


def test_noncleared_journal_cannot_start_with_another_intent() -> None:
    entry = _entry("safe_mode")
    _, intent = _authority()

    with pytest.raises(PaperAuthorizationError, match="does not exactly match"):
        PaperOrderSimulator().start(entry, intent, SimulatedExecutionCostConfiguration())


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("quantity", Decimal("1E+50")),
        ("side", EntrySide.SELL),
        ("reference_price", Decimal(999)),
        ("stop_basis", Decimal(1)),
        ("risk_policy_version", "forged"),
        ("safety_policy_version", "forged"),
        ("authorized_for_execution", False),
    ],
)
def test_raw_modified_intent_fields_are_rejected(field_name: str, value: object) -> None:
    entry, intent = _authority()
    object.__setattr__(intent, field_name, value)

    with pytest.raises(PaperAuthorizationError, match="does not exactly match"):
        PaperOrderSimulator().start(entry, intent, SimulatedExecutionCostConfiguration())


def test_raw_modified_intent_identity_is_rejected() -> None:
    entry, intent = _authority()
    object.__setattr__(intent, "decision_identity", replace(intent.decision_identity, symbol=ETH))

    with pytest.raises(PaperAuthorizationError, match="does not exactly match"):
        PaperOrderSimulator().start(entry, intent, SimulatedExecutionCostConfiguration())


def test_paper_lifecycle_cannot_be_started_through_public_constructor() -> None:
    constructor = cast(Any, PaperOrderLifecycle)

    with pytest.raises(TypeError, match="only be created by PaperOrderSimulator"):
        constructor(status=PaperOrderStatus.PENDING)


def test_zero_latency_and_exact_eligibility_fill_once() -> None:
    entry, intent = _authority()
    simulator = PaperOrderSimulator()
    lifecycle = simulator.start(entry, intent, SimulatedExecutionCostConfiguration())

    filled = simulator.observe(lifecycle, _observation(NOW))

    assert filled.status is PaperOrderStatus.FILLED
    assert filled.eligible_at == intent.authorized_at == NOW
    assert filled.fill is not None
    assert filled.fill.fill_at == filled.eligible_at
    assert filled.fill.quantity == intent.quantity
    assert filled.fill.side is intent.side
    assert filled.intent.approved_notional_quote == entry.risk_decision.approved_notional_quote
    assert filled.intent.approved_risk_quote == entry.risk_decision.approved_risk_quote
    assert filled.intent.stop_basis == entry.risk_decision.approved_risk_stop_basis


def test_short_intent_fills_as_sell_without_quantity_change() -> None:
    entry, intent = _authority(short=True)
    costs = SimulatedExecutionCostConfiguration(slippage_bps=Decimal(10))
    filled = PaperOrderSimulator().observe(
        PaperOrderSimulator().start(entry, intent, costs), _observation()
    )
    assert filled.fill is not None

    assert filled.fill.side is EntrySide.SELL
    assert filled.fill.quantity == intent.quantity
    assert filled.fill.fill_price == Decimal("99.900")


def test_preeligible_observation_remains_pending_then_first_eligible_fills() -> None:
    entry, intent = _authority()
    simulator = PaperOrderSimulator()
    lifecycle = simulator.start(
        entry,
        intent,
        SimulatedExecutionCostConfiguration(latency=timedelta(minutes=1)),
    )

    pending = simulator.observe(lifecycle, _observation(NOW + timedelta(seconds=30)))
    filled = simulator.observe(pending, _observation(NOW + timedelta(minutes=1)))

    assert pending.status is PaperOrderStatus.PENDING
    assert pending.last_observed_at == NOW + timedelta(seconds=30)
    assert pending.fill is None
    assert filled.status is PaperOrderStatus.FILLED
    assert filled.fill is not None
    assert filled.fill.reference_price == Decimal(100)


def test_observation_before_authorization_rejects() -> None:
    entry, intent = _authority()
    lifecycle = PaperOrderSimulator().start(entry, intent, SimulatedExecutionCostConfiguration())

    with pytest.raises(PaperObservationError, match="precedes"):
        PaperOrderSimulator().observe(lifecycle, _observation(NOW - timedelta(microseconds=1)))


def test_observation_requires_utc_aware_time() -> None:
    with pytest.raises(ValueError, match="UTC"):
        PaperExecutionObservation(
            SOURCE,
            BTC,
            NOW.replace(tzinfo=None),
            Decimal(100),
        )


@pytest.mark.parametrize(
    "observation",
    [
        _observation(source_id=SourceId("other")),
        _observation(symbol=ETH),
    ],
)
def test_observation_identity_mismatch_rejects(
    observation: PaperExecutionObservation,
) -> None:
    entry, intent = _authority()
    lifecycle = PaperOrderSimulator().start(entry, intent, SimulatedExecutionCostConfiguration())

    with pytest.raises(PaperObservationError, match="identity"):
        PaperOrderSimulator().observe(lifecycle, observation)


@pytest.mark.parametrize("value", [0.0, Decimal(0), Decimal(-1), Decimal("NaN")])
def test_observation_rejects_invalid_price(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        PaperExecutionObservation(SourceId("archive"), BTC, NOW, value)  # type: ignore[arg-type]


def test_observation_time_must_strictly_advance() -> None:
    entry, intent = _authority()
    simulator = PaperOrderSimulator()
    lifecycle = simulator.start(
        entry,
        intent,
        SimulatedExecutionCostConfiguration(latency=timedelta(minutes=1)),
    )
    pending = simulator.observe(lifecycle, _observation(NOW + timedelta(seconds=30)))

    with pytest.raises(PaperObservationError, match="strictly advance"):
        simulator.observe(pending, _observation(NOW + timedelta(seconds=30)))


def test_filled_lifecycle_never_creates_a_second_fill() -> None:
    entry, intent = _authority()
    simulator = PaperOrderSimulator()
    pending = simulator.start(entry, intent, SimulatedExecutionCostConfiguration())
    filled = simulator.observe(pending, _observation())

    unchanged = simulator.observe(filled, _observation(NOW + timedelta(minutes=1)))

    assert unchanged is filled
    assert unchanged.fill is filled.fill

    with pytest.raises(PaperObservationError, match="strictly advance"):
        simulator.observe(filled, _observation())


def test_repeated_equivalent_inputs_produce_equal_lifecycles_and_fills() -> None:
    left_entry, left_intent = _authority()
    right_entry, right_intent = _authority()
    costs = SimulatedExecutionCostConfiguration(
        spread_bps=Decimal(4), slippage_bps=Decimal(3), fee_bps=Decimal(2)
    )
    simulator = PaperOrderSimulator()

    left = simulator.observe(simulator.start(left_entry, left_intent, costs), _observation())
    right = simulator.observe(simulator.start(right_entry, right_intent, costs), _observation())

    assert left == right


def test_backtest_and_paper_use_exact_same_pricing_and_fee() -> None:
    entry, intent = _authority()
    costs = ExecutionCostConfiguration(
        spread_bps=Decimal(10), slippage_bps=Decimal(20), fee_bps=Decimal(20)
    )
    candle = Candle(
        BTC,
        Timeframe.MINUTES_15,
        NOW,
        NOW + timedelta(minutes=15),
        Decimal(100),
        Decimal(101),
        Decimal(99),
        Decimal(100),
        Decimal(1),
    )

    class Source:
        def load(
            self,
            series: ExecutionSeries,
            *,
            start: datetime,
            end: datetime,
        ) -> tuple[Candle, ...]:
            return (candle,)

    backtest_fill = (
        BacktestExecutionSimulator(Source())
        .run(
            ExecutionSeries(SourceId("archive"), BTC, Timeframe.MINUTES_15),
            costs,
            (
                MarketOrderRequest(
                    "parity",
                    intent.authorized_at,
                    intent.symbol,
                    OrderSide.BUY,
                    intent.quantity,
                ),
            ),
            start=NOW,
            end=NOW + timedelta(minutes=15),
        )
        .fills[0]
    )
    paper_lifecycle = PaperOrderSimulator().observe(
        PaperOrderSimulator().start(entry, intent, costs), _observation()
    )
    assert paper_lifecycle.fill is not None

    assert paper_lifecycle.fill.fill_price == backtest_fill.fill_price
    assert paper_lifecycle.fill.fee_quote == backtest_fill.fee_quote


def test_paper_outputs_are_immutable_and_full_fill_is_exact() -> None:
    entry, intent = _authority()
    simulator = PaperOrderSimulator()
    lifecycle = simulator.start(entry, intent, SimulatedExecutionCostConfiguration())
    filled = simulator.observe(lifecycle, _observation())
    assert filled.fill is not None

    with pytest.raises(FrozenInstanceError):
        lifecycle.status = PaperOrderStatus.FILLED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        filled.fill.quantity = Decimal(999)  # type: ignore[misc]
    assert filled.fill.quantity == intent.quantity


def test_paper_and_shared_pricing_have_no_forbidden_dependencies_or_behaviors() -> None:
    forbidden = (
        "adaptive_trading_bot.backtest",
        "adaptive_trading_bot.exchange",
        "adaptive_trading_bot.storage",
        "adaptive_trading_bot.market_data",
        "sqlalchemy",
        "ccxt",
    )
    for module in (paper_module, shared_execution_module):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(name.startswith(forbidden) for name in imported)

    simulator = PaperOrderSimulator()
    for method in ("submit", "send", "cancel", "replace"):
        assert not hasattr(simulator, method)
    disallowed_fields = {
        "exchange_order_id",
        "client_order_id",
        "position",
        "account_balance",
        "realized_pnl",
        "take_profit",
        "leverage",
        "margin",
    }
    assert not disallowed_fields.intersection(
        field.name for field in fields(paper_module.PaperMarketFill)
    )
