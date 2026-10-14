"""Post-fill stop invalidation is an accounted, causal full-close recovery."""

import runpy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.dashboard import (
    DashboardDecisionTradeQuery,
    SnapshotDashboardJournalProvider,
)
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.experience import ExperienceProjectionError, ExperienceSampleProjector
from adaptive_trading_bot.journal import DecisionJournalSnapshot, TradeExitReason
from adaptive_trading_bot.orders import ExitIntentPurpose
from adaptive_trading_bot.orders.exit_intent import POST_FILL_STOP_RECOVERY_POLICY_VERSION
from adaptive_trading_bot.paper import (
    PaperEntryInvalidationReason,
    PaperExitOrderStatus,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorError,
    PaperPortfolioObservationError,
    PaperPortfolioRecoveryPendingError,
    PaperPortfolioStartBatchStatus,
    PaperPortfolioStartBlockReason,
    PaperProtectiveStopStatus,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("protective_exit_helpers.py")))
NOW = _HELPERS["NOW"]
BTC = _HELPERS["BTC"]
ETH = _HELPERS["ETH"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]


@pytest.mark.parametrize(
    ("action", "reference", "next_reference"),
    (
        (CandidateAction.LONG_CANDIDATE, Decimal("74"), Decimal("73")),
        (CandidateAction.SHORT_CANDIDATE, Decimal("136"), Decimal("137")),
    ),
)
def test_invalidated_fill_is_accounted_then_recovered_causally(
    action: CandidateAction, reference: Decimal, next_reference: Decimal
) -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    item = candidate(action=action)
    paper.start_batch((item,))

    entry = paper.observe_batch((observation(BTC, NOW, reference),))
    opened = entry.newly_filled[0]
    intent = entry.active_exit_intents[0]
    assert opened.recovery_required
    assert opened.recovery_policy_version == POST_FILL_STOP_RECOVERY_POLICY_VERSION
    assert opened.invalidation_reason is PaperEntryInvalidationReason.STOP_AT_OR_BEYOND_FILL
    assert opened.approved_risk_is_capacity_floor
    assert opened.locked_open_risk_quote == opened.approved_risk_quote
    assert opened.locked_gross_exposure_quote >= opened.approved_notional_quote
    assert entry.active_pending_reservations.reservations == ()
    assert entry.accounting_positions[0][1].quantity != 0
    assert paper.account_snapshot(NOW).cumulative_entry_fees_quote == opened.fee_quote
    assert intent.identity.purpose is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
    assert intent.trigger_observed_at == opened.fill_at
    assert intent.trigger_reference_price == opened.fill_price
    assert intent.originating_accounting_fill_id == opened.accounting_fill_id
    assert intent.recovery_policy_version == POST_FILL_STOP_RECOVERY_POLICY_VERSION

    with pytest.raises(
        PaperPortfolioRecoveryPendingError, match="POST_FILL_STOP_RECOVERY_PENDING"
    ) as blocked:
        paper.start_batch((candidate(symbol=ETH, observed_at=NOW + timedelta(minutes=15)),))
    assert blocked.value.reason is PaperPortfolioStartBlockReason.POST_FILL_STOP_RECOVERY_PENDING

    before = paper.snapshot()
    with pytest.raises(PaperPortfolioBatchError):
        paper.observe_protective_exits((observation(BTC, NOW, next_reference),))
    assert paper.snapshot() == before

    pending = paper.evaluate_protective_stops(
        (observation(BTC, NOW + timedelta(minutes=1), next_reference),)
    )
    assert pending.evaluations[0].status is PaperProtectiveStopStatus.ALREADY_PENDING
    assert pending.triggered == ()
    assert pending.active_exit_intents[0].identity == intent.identity

    closed = paper.observe_protective_exits(
        (observation(BTC, NOW + timedelta(minutes=2), next_reference),)
    )
    assert len(closed.newly_closed) == 1
    trade = closed.completed_trade_journal_entries[0]
    trade.validate()
    assert trade.exit_reason is TradeExitReason.ENTRY_STOP_INVALIDATION
    assert trade.identity.exit_intent_identity == intent.identity
    assert trade.recovery_policy_version == POST_FILL_STOP_RECOVERY_POLICY_VERSION
    assert trade.trigger_observed_at == trade.entry_fill_at
    assert trade.exit_fill_at > trade.entry_fill_at
    assert trade.gross_realized_pnl == closed.accounting_transitions[0].gross_realized_pnl_delta
    assert trade.net_realized_pnl == closed.newly_closed[0].net_realized_pnl
    assert closed.open_positions == ()
    assert closed.active_exit_intents == ()
    assert closed.accounting_positions[0][1].quantity == 0
    later = NOW + timedelta(minutes=15)
    reopened = paper.start_batch(
        (candidate(symbol=ETH, observed_at=later, portfolio=paper.risk_portfolio_snapshot(later)),)
    )
    assert reopened.status is PaperPortfolioStartBatchStatus.STARTED


def test_mixed_batch_keeps_valid_open_and_recovers_only_invalidated_symbol() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(symbol=ETH), candidate(symbol=BTC)))
    result = paper.observe_batch(
        (observation(ETH, NOW, Decimal(100)), observation(BTC, NOW, Decimal(74)))
    )
    assert tuple(item.symbol for item in result.newly_filled) == (BTC, ETH)
    assert tuple(item.recovery_required for item in result.newly_filled) == (True, False)
    assert tuple(item.symbol for item in result.active_exit_intents) == (BTC,)
    assert len(result.accounting_transitions) == 2

    closed = paper.observe_protective_exits(
        (observation(BTC, NOW + timedelta(minutes=1), Decimal(73)),)
    )
    assert tuple(item.symbol for item in closed.open_positions) == (ETH,)
    assert closed.active_exit_intents == ()
    assert (
        paper.completed_trade_journal_snapshot().entries[0].exit_reason
        is TradeExitReason.ENTRY_STOP_INVALIDATION
    )


def test_admission_stays_blocked_until_all_recoveries_are_flat() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(symbol=BTC), candidate(symbol=ETH)))
    result = paper.observe_batch(
        (observation(ETH, NOW, Decimal(74)), observation(BTC, NOW, Decimal(74)))
    )
    assert len(result.active_exit_intents) == 2
    first = paper.observe_protective_exits(
        (observation(BTC, NOW + timedelta(minutes=1), Decimal(73)),)
    )
    assert tuple(item.symbol for item in first.open_positions) == (ETH,)
    assert len(first.active_exit_intents) == 1
    with pytest.raises(PaperPortfolioRecoveryPendingError):
        paper.start_batch((candidate(observed_at=NOW + timedelta(minutes=15), symbol=BTC),))
    second = paper.observe_protective_exits(
        (observation(ETH, NOW + timedelta(minutes=2), Decimal(73)),)
    )
    assert second.open_positions == ()
    assert second.active_exit_intents == ()
    assert len(paper.completed_trade_journal_snapshot().entries) == 2


@pytest.mark.parametrize(
    ("action", "stop"),
    (
        (CandidateAction.LONG_CANDIDATE, Decimal(75)),
        (CandidateAction.SHORT_CANDIDATE, Decimal(135)),
    ),
)
def test_fill_exactly_on_stop_requires_recovery(action: CandidateAction, stop: Decimal) -> None:
    paper = PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(Decimal(0), Decimal(0), Decimal(0))
    )
    paper.start_batch((candidate(action=action),))
    result = paper.observe_batch((observation(BTC, NOW, stop),))
    assert result.newly_filled[0].fill_price == stop
    assert result.newly_filled[0].recovery_required
    assert (
        result.active_exit_intents[0].identity.purpose
        is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
    )


def test_pending_entry_continues_while_other_symbol_is_in_recovery() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator(latency=timedelta(minutes=1)))
    paper.start_batch((candidate(symbol=BTC), candidate(symbol=ETH)))
    first = paper.observe_batch((observation(BTC, NOW + timedelta(minutes=1), Decimal(74)),))
    assert len(first.newly_filled) == 1
    assert len(first.still_pending) == 1
    assert first.newly_filled[0].recovery_required
    second = paper.observe_batch((observation(ETH, NOW + timedelta(minutes=2), Decimal(100)),))
    assert second.newly_filled[0].symbol == ETH
    assert not second.newly_filled[0].recovery_required
    assert second.newly_filled[0].invalidation_reason is None
    assert tuple(item.symbol for item in second.active_exit_intents) == (BTC,)


def test_positive_latency_preeligible_observation_keeps_recovery_pending_even_after_rebound() -> (
    None
):
    latency = timedelta(minutes=2)
    paper = cast(PaperPortfolioCoordinator, coordinator(latency=latency))
    paper.start_batch((candidate(),))
    entry_at = NOW + latency
    entry = paper.observe_batch((observation(BTC, entry_at, Decimal(74)),))
    intent = entry.active_exit_intents[0]
    assert intent.trigger_reference_price == entry.newly_filled[0].fill_price
    before = paper.snapshot()

    preeligible = paper.observe_protective_exits(
        (observation(BTC, entry_at + timedelta(minutes=1), Decimal(100)),)
    )
    assert preeligible.newly_closed == ()
    assert preeligible.still_pending_exits[0].status is PaperExitOrderStatus.PENDING
    assert preeligible.open_positions == before.open_positions
    assert preeligible.active_exit_intents[0].identity == intent.identity
    with pytest.raises(PaperPortfolioBatchError):
        paper.observe_protective_exits(
            (observation(BTC, entry_at + timedelta(minutes=1), Decimal(100)),)
        )

    closed = paper.observe_protective_exits((observation(BTC, entry_at + latency, Decimal(100)),))
    assert closed.newly_closed[0].exit_reason is TradeExitReason.ENTRY_STOP_INVALIDATION
    assert closed.newly_closed[0].exit_intent_identity == intent.identity
    assert closed.completed_trade_journal_entries[0].net_realized_pnl > 0
    assert closed.open_positions == ()


def test_ordinary_protective_journal_cannot_be_relabelled_as_recovery() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC, NOW, Decimal(100)),))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    closed = paper.observe_protective_exits(
        (observation(BTC, trigger_at + timedelta(minutes=1), Decimal(74)),)
    )
    trade = closed.completed_trade_journal_entries[0]
    object.__setattr__(trade, "exit_reason", TradeExitReason.ENTRY_STOP_INVALIDATION)
    object.__setattr__(trade, "recovery_policy_version", POST_FILL_STOP_RECOVERY_POLICY_VERSION)
    with pytest.raises(ValueError):
        trade.validate()


def test_read_only_trade_projection_preserves_distinct_recovery_reason() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    item = candidate()
    paper.start_batch((item,))
    paper.observe_batch((observation(BTC, NOW, Decimal(74)),))
    paper.observe_protective_exits((observation(BTC, NOW + timedelta(minutes=1), Decimal(73)),))
    provider = SnapshotDashboardJournalProvider(
        DecisionJournalSnapshot((item.journal_entry,), NOW),
        paper.completed_trade_journal_snapshot(),
    )
    page = provider.trades(DashboardDecisionTradeQuery(NOW, NOW + timedelta(minutes=2)))
    assert page.records[0].exit_reason == TradeExitReason.ENTRY_STOP_INVALIDATION.value
    assert page.records[0].exit_intent_identity.purpose == (
        ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE.value
    )
    with pytest.raises(ExperienceProjectionError, match="protective-stop exits only"):
        ExperienceSampleProjector().project(
            DecisionJournalSnapshot((item.journal_entry,), NOW),
            paper.completed_trade_journal_snapshot(),
        )


def test_recovery_authorization_failure_rolls_back_entry_and_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(),))
    before = paper.snapshot()
    account_before = paper.account_snapshot(NOW)

    def fail_start(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ValueError("injected recovery authorization failure")

    monkeypatch.setattr(
        "adaptive_trading_bot.paper.coordinator.PaperProtectiveExitSimulator.start", fail_start
    )
    with pytest.raises(
        PaperPortfolioObservationError, match="Paper observation batch failed before atomic commit"
    ):
        paper.observe_batch((observation(BTC, NOW, Decimal(74)),))
    assert paper.snapshot() == before
    assert paper.account_snapshot(NOW) == account_before


def test_tampered_recovery_purpose_or_fill_binding_fails_closed() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC, NOW, Decimal(74)),))
    owned = paper._active_exit_intents[0].lifecycle.intent
    object.__setattr__(owned, "originating_accounting_fill_id", "forged")
    with pytest.raises(PaperPortfolioCoordinatorError):
        paper.observe_protective_exits((observation(BTC, NOW + timedelta(minutes=1), Decimal(73)),))
    assert paper._accounting.position(BTC).quantity != 0
    assert paper._account.cumulative_exit_fees_quote == 0


@pytest.mark.parametrize(
    "field",
    (
        "quantity",
        "source_id",
        "symbol",
        "identity",
        "risk_policy_version",
        "recovery_policy_version",
        "originating_accounting_fill_id",
    ),
)
def test_forged_owned_recovery_fails_before_pricing_or_accounting(field: str) -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC, NOW, Decimal(74)),))
    owned = paper._active_exit_intents[0].lifecycle.intent
    forged: object = {
        "quantity": owned.quantity + 1,
        "source_id": SourceId("other-archive"),
        "symbol": ETH,
        "identity": replace(owned.identity, purpose=ExitIntentPurpose.PROTECTIVE_STOP_FULL_CLOSE),
        "risk_policy_version": "forged",
        "recovery_policy_version": "forged",
        "originating_accounting_fill_id": "forged",
    }[field]
    object.__setattr__(owned, field, forged)
    with pytest.raises(PaperPortfolioCoordinatorError):
        paper.observe_protective_exits((observation(BTC, NOW + timedelta(minutes=1), Decimal(73)),))
    assert paper._accounting.position(BTC).quantity != 0
    assert paper._account.cumulative_exit_fees_quote == 0
