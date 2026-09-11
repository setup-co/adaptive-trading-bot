import ast
import inspect
import runpy
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.journal.trade_outcome as outcome_module
import adaptive_trading_bot.paper.coordinator as coordinator_module
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.journal import (
    CompletedTradeJournalAppendError,
    CompletedTradeJournalBatch,
    CompletedTradeJournalEntry,
    CompletedTradeJournalValidationError,
    InMemoryCompletedTradeJournal,
    TradeOutcomeClassification,
)
from adaptive_trading_bot.paper import (
    PaperPortfolioCoordinator,
    PaperPortfolioExitExecutionError,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "paper" / "protective_exit_helpers.py"))
NOW: datetime = _HELPERS["NOW"]
BTC: TradingSymbol = _HELPERS["BTC"]
ETH: TradingSymbol = _HELPERS["ETH"]
candidate = _HELPERS["candidate"]
observation = _HELPERS["observation"]
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def _paper(*, costs: bool = True) -> PaperPortfolioCoordinator:
    value = Decimal(1) if costs else Decimal(0)
    return PaperPortfolioCoordinator(SimulatedExecutionCostConfiguration(value, value, value))


def _complete(
    *,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    exit_price: Decimal = Decimal(70),
    observed_at: datetime = NOW,
    costs: bool = True,
) -> tuple[
    PaperPortfolioCoordinator,
    PortfolioAdmissionCandidate[object, object],
    CompletedTradeJournalEntry,
]:
    paper = _paper(costs=costs)
    item = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(observed_at=observed_at, action=action),
    )
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, observed_at, Decimal(100)),))
    trigger_at = observed_at + timedelta(minutes=1)
    stop = Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, stop),))
    result = paper.observe_protective_exits(
        (observation(BTC, trigger_at + timedelta(microseconds=1), exit_price),)
    )
    assert len(result.completed_trade_journal_entries) == 1
    return paper, item, result.completed_trade_journal_entries[0]


@pytest.mark.parametrize(
    ("action", "exit_price", "classification"),
    (
        (CandidateAction.LONG_CANDIDATE, Decimal(110), TradeOutcomeClassification.PROFIT),
        (CandidateAction.LONG_CANDIDATE, Decimal(90), TradeOutcomeClassification.LOSS),
        (CandidateAction.LONG_CANDIDATE, Decimal(100), TradeOutcomeClassification.BREAKEVEN),
        (CandidateAction.SHORT_CANDIDATE, Decimal(90), TradeOutcomeClassification.PROFIT),
        (CandidateAction.SHORT_CANDIDATE, Decimal(110), TradeOutcomeClassification.LOSS),
        (CandidateAction.SHORT_CANDIDATE, Decimal(100), TradeOutcomeClassification.BREAKEVEN),
    ),
)
def test_completed_trade_preserves_authority_and_exact_net_r(
    action: CandidateAction,
    exit_price: Decimal,
    classification: TradeOutcomeClassification,
) -> None:
    paper, item, entry = _complete(action=action, exit_price=exit_price, costs=False)
    risk = item.journal_entry.risk_decision
    snapshot = paper.completed_trade_journal_snapshot()

    assert snapshot.entries == (entry,)
    assert snapshot.last_closed_at == entry.exit_fill_at
    assert entry.pre_trade_decision == item.journal_entry
    assert entry.identity.originating_decision_identity == item.journal_entry.identity
    assert entry.identity.entry_intent_identity == item.intent.identity
    assert entry.identity.paper_order_identity.intent_identity == item.intent.identity
    assert (
        entry.identity.exit_intent_identity.originating_entry_intent_identity
        == item.intent.identity
    )
    assert entry.quantity == risk.approved_quantity
    assert entry.approved_notional_quote == risk.approved_notional_quote
    assert entry.approved_risk_quote == risk.approved_risk_quote
    assert entry.stop_basis == risk.approved_risk_stop_basis
    assert entry.approved_reference_price == risk.reference_price
    with localcontext(_DECIMAL_CONTEXT):
        assert entry.total_trade_fees_quote == entry.entry_fee_quote + entry.exit_fee_quote
        assert entry.net_realized_pnl == (entry.gross_realized_pnl - entry.total_trade_fees_quote)
        assert entry.realized_r_net == entry.net_realized_pnl / risk.approved_risk_quote
    assert entry.outcome_classification is classification
    assert entry.duration == entry.exit_fill_at - entry.entry_fill_at


def test_same_time_multi_symbol_append_is_canonical_and_atomic() -> None:
    paper = _paper()
    items = (candidate(symbol=ETH), candidate(symbol=BTC))
    paper.start_batch(items)
    paper.observe_batch((observation(ETH, NOW, Decimal(100)), observation(BTC, NOW, Decimal(100))))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops(
        (observation(ETH, trigger_at, Decimal(75)), observation(BTC, trigger_at, Decimal(75)))
    )
    result = paper.observe_protective_exits(
        (
            observation(ETH, trigger_at + timedelta(microseconds=1), Decimal(70)),
            observation(BTC, trigger_at + timedelta(microseconds=1), Decimal(70)),
        )
    )

    assert tuple(entry.symbol for entry in result.completed_trade_journal_entries) == (BTC, ETH)
    assert (
        paper.completed_trade_journal_snapshot().entries == result.completed_trade_journal_entries
    )


def test_journal_copies_exact_entry_exit_accounting_and_account_facts() -> None:
    paper = _paper()
    item = candidate()
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    entry_fill = deepcopy(paper._open_positions[0].lifecycle.fill)
    assert entry_fill is not None
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    result = paper.observe_protective_exits(
        (observation(BTC, trigger_at + timedelta(microseconds=1), Decimal(70)),)
    )
    journal = result.completed_trade_journal_entries[0]
    closed = result.newly_closed[0]
    transition = result.accounting_transitions[0]

    assert journal.entry_reference_price == entry_fill.reference_price
    assert journal.entry_fill_price == entry_fill.fill_price
    assert journal.entry_spread_bps == entry_fill.spread_bps
    assert journal.entry_slippage_bps == entry_fill.slippage_bps
    assert journal.entry_fee_bps == entry_fill.fee_bps
    assert journal.entry_fee_quote == entry_fill.fee_quote
    assert journal.trigger_observed_at == closed.trigger_observed_at
    assert journal.trigger_reference_price == closed.trigger_reference_price
    assert journal.exit_reference_price == closed.exit_reference_price
    assert journal.exit_fill_price == closed.exit_fill_price
    assert journal.exit_fee_quote == closed.exit_fee_quote
    assert journal.gross_realized_pnl == transition.gross_realized_pnl_delta
    assert journal.total_trade_fees_quote == closed.trade_fees_quote
    assert journal.net_realized_pnl == closed.net_realized_pnl
    assert (
        paper.account_snapshot(journal.exit_fill_at).realized_gross_pnl_quote
        == journal.gross_realized_pnl
    )


def test_close_input_permutation_produces_identical_result_and_journal() -> None:
    def ready() -> PaperPortfolioCoordinator:
        paper = _paper()
        paper.start_batch((candidate(symbol=BTC), candidate(symbol=ETH)))
        paper.observe_batch(
            (observation(BTC, NOW, Decimal(100)), observation(ETH, NOW, Decimal(100)))
        )
        trigger_at = NOW + timedelta(minutes=1)
        paper.evaluate_protective_stops(
            (
                observation(BTC, trigger_at, Decimal(75)),
                observation(ETH, trigger_at, Decimal(75)),
            )
        )
        return paper

    left = ready()
    right = ready()
    fill_at = NOW + timedelta(minutes=1, microseconds=1)
    left_result = left.observe_protective_exits(
        (observation(ETH, fill_at, Decimal(70)), observation(BTC, fill_at, Decimal(70)))
    )
    right_result = right.observe_protective_exits(
        (observation(BTC, fill_at, Decimal(70)), observation(ETH, fill_at, Decimal(70)))
    )

    assert left_result == right_result
    assert left.completed_trade_journal_snapshot() == right.completed_trade_journal_snapshot()


def test_preeligible_exit_observation_appends_nothing() -> None:
    latency = timedelta(minutes=10)
    paper = PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(Decimal(0), Decimal(0), Decimal(0), latency)
    )
    item = candidate()
    paper.start_batch((item,))
    entry_at = NOW + latency
    paper.observe_batch((observation(BTC, entry_at, Decimal(100)),))
    trigger_at = entry_at + timedelta(minutes=1)
    intent = paper.evaluate_protective_stops(
        (observation(BTC, trigger_at, Decimal(75)),)
    ).triggered[0]

    result = paper.observe_protective_exits(
        (observation(BTC, intent.authorized_at + timedelta(minutes=5), Decimal(70)),)
    )

    assert result.completed_trade_journal_entries == ()
    assert paper.completed_trade_journal_snapshot().entries == ()


def test_owned_pretrade_record_survives_caller_mutation_after_start() -> None:
    paper = _paper(costs=False)
    item = candidate()
    original = deepcopy(item.journal_entry)
    paper.start_batch((item,))
    object.__setattr__(item.journal_entry.strategy_evaluation, "strategy_id", "forged")
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    paper.observe_protective_exits(
        (observation(BTC, trigger_at + timedelta(microseconds=1), Decimal(70)),)
    )

    assert paper.completed_trade_journal_snapshot().entries[0].pre_trade_decision == original


def test_returned_completed_entries_cannot_mutate_owned_journal() -> None:
    paper, _, result_entry = _complete()
    before = paper.completed_trade_journal_snapshot()
    returned_snapshot_entry = paper.completed_trade_journal_snapshot().entries[0]
    object.__setattr__(result_entry, "quantity", Decimal("1E+20"))
    object.__setattr__(returned_snapshot_entry.source_id, "value", "forged")

    assert paper.completed_trade_journal_snapshot() == before


@pytest.mark.parametrize("failure", ("construction", "append"))
def test_journal_failure_rolls_back_close_and_all_state(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    paper = _paper()
    item = candidate()
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    fill_at = trigger_at + timedelta(microseconds=1)
    before = paper.snapshot()
    before_account = paper.account_snapshot(fill_at)
    before_journal = paper.completed_trade_journal_snapshot()
    if failure == "construction":
        monkeypatch.setattr(
            coordinator_module,
            "_completed_trade_entry",
            lambda *args: (_ for _ in ()).throw(RuntimeError("construction")),
        )
    else:
        monkeypatch.setattr(
            InMemoryCompletedTradeJournal,
            "append_batch",
            lambda *args: (_ for _ in ()).throw(RuntimeError("append")),
        )

    with pytest.raises(PaperPortfolioExitExecutionError, match="before atomic commit"):
        paper.observe_protective_exits((observation(BTC, fill_at, Decimal(70)),))

    assert paper.snapshot() == before
    assert paper.account_snapshot(fill_at) == before_account
    assert paper.completed_trade_journal_snapshot() == before_journal


def test_gross_pnl_is_opaque_locally_and_checked_against_accounting_before_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_source = inspect.getsource(outcome_module._validate_execution_and_accounting)
    assert "exit_fill_price - entry.entry_fill_price" not in validation_source
    assert "entry.entry_fill_price - entry.exit_fill_price" not in validation_source

    paper = _paper()
    item = candidate()
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    fill_at = trigger_at + timedelta(microseconds=1)
    before = paper.snapshot()
    before_account = paper.account_snapshot(fill_at)
    before_journal = paper.completed_trade_journal_snapshot()
    real_builder = coordinator_module._completed_trade_entry
    append_calls = 0

    def forged_builder(*args: object) -> CompletedTradeJournalEntry:
        forged = real_builder(*args)  # type: ignore[arg-type]
        with localcontext(_DECIMAL_CONTEXT):
            forged_gross = forged.gross_realized_pnl + Decimal(1)
            forged_net = forged_gross - forged.total_trade_fees_quote
            forged_r = forged_net / forged.approved_risk_quote
        classification = (
            TradeOutcomeClassification.PROFIT
            if forged_net > 0
            else TradeOutcomeClassification.LOSS
            if forged_net < 0
            else TradeOutcomeClassification.BREAKEVEN
        )
        object.__setattr__(forged, "gross_realized_pnl", forged_gross)
        object.__setattr__(forged, "net_realized_pnl", forged_net)
        object.__setattr__(forged, "realized_r_net", forged_r)
        object.__setattr__(forged, "outcome_classification", classification)
        forged.validate()
        return forged

    real_append = InMemoryCompletedTradeJournal.append_batch

    def counted_append(
        self: InMemoryCompletedTradeJournal, batch: CompletedTradeJournalBatch
    ) -> object:
        nonlocal append_calls
        append_calls += 1
        return real_append(self, batch)

    monkeypatch.setattr(coordinator_module, "_completed_trade_entry", forged_builder)
    monkeypatch.setattr(InMemoryCompletedTradeJournal, "append_batch", counted_append)

    with pytest.raises(
        PaperPortfolioExitExecutionError,
        match="accounting or closed-trade authority",
    ):
        paper.observe_protective_exits((observation(BTC, fill_at, Decimal(70)),))

    assert append_calls == 0
    assert paper.snapshot() == before
    assert paper.account_snapshot(fill_at) == before_account
    assert paper.completed_trade_journal_snapshot() == before_journal


def test_completed_trade_entry_direct_construction_is_not_supported() -> None:
    _, _, valid = _complete()
    constructor_values = {
        value_field.name: getattr(valid, value_field.name)
        for value_field in fields(CompletedTradeJournalEntry)
        if value_field.init
    }

    with pytest.raises(TypeError, match="authoritative close"):
        CompletedTradeJournalEntry(**constructor_values)


@pytest.mark.parametrize(
    ("field_name", "forged"),
    (
        ("strategy_id", "forged"),
        ("source_id", SourceId("forged")),
        ("symbol", ETH),
        ("quantity", Decimal(999)),
        ("approved_risk_quote", Decimal(999)),
        ("stop_basis", Decimal(1)),
        ("risk_policy_version", "forged"),
        ("safety_policy_version", "forged"),
        ("gross_realized_pnl", Decimal(999)),
        ("entry_fee_quote", Decimal(999)),
        ("net_realized_pnl", Decimal(999)),
    ),
)
def test_forged_completed_trade_fails_closed(field_name: str, forged: object) -> None:
    _, _, valid = _complete()
    altered = deepcopy(valid)
    object.__setattr__(altered, field_name, forged)

    with pytest.raises((CompletedTradeJournalValidationError, ValueError)):
        altered.validate()


def test_duplicate_identity_and_backward_chronology_are_rejected() -> None:
    _, _, older = _complete(observed_at=NOW)
    _, _, newer = _complete(observed_at=NOW + timedelta(minutes=15))
    ledger = InMemoryCompletedTradeJournal()
    newer_batch = CompletedTradeJournalBatch(newer.exit_fill_at, (newer,))
    ledger.append_batch(newer_batch)

    with pytest.raises(CompletedTradeJournalAppendError, match="already exists"):
        ledger.append_batch(newer_batch)
    with pytest.raises(CompletedTradeJournalAppendError, match="strictly advance"):
        ledger.append_batch(CompletedTradeJournalBatch(older.exit_fill_at, (older,)))


def test_forged_originating_decision_identity_fails_closed() -> None:
    _, _, valid = _complete()
    altered = deepcopy(valid)
    forged_decision = deepcopy(altered.identity.originating_decision_identity)
    object.__setattr__(forged_decision, "strategy_id", "forged")
    object.__setattr__(altered.identity, "originating_decision_identity", forged_decision)

    with pytest.raises(CompletedTradeJournalValidationError):
        altered.validate()


def test_completed_trade_schema_excludes_deferred_analytics_and_side_effects() -> None:
    names = {field.name.lower() for field in fields(CompletedTradeJournalEntry)}
    assert not any(token in name for name in names for token in ("mfe", "mae", "process_score"))

    path = Path(outcome_module.__file__ or "")
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
        for forbidden in ("exchange", "sqlalchemy", "database", "requests", "httpx")
    )
