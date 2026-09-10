import ast
import inspect
import runpy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import adaptive_trading_bot.paper.coordinator as coordinator_module
from adaptive_trading_bot.admission import (
    PendingReservationSnapshot,
    PortfolioAdmissionBatchResult,
    PortfolioAdmissionCandidate,
    PortfolioAdmissionGate,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntent, EntryOrderIntentAuthorizer, EntrySide
from adaptive_trading_bot.paper import (
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioStartBatchStatus,
    PaperPortfolioStartError,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

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
    observed_at: datetime = NOW,
    symbol: TradingSymbol = BTC,
    source_id: SourceId = SOURCE,
    strategy_id: str = "manual",
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    configuration: RiskManagerConfiguration | None = None,
) -> PortfolioAdmissionCandidate[object, object]:
    context = _context(source_id=source_id, symbol=symbol, observed_at=observed_at)
    evaluation = _manual_evaluation(
        context,
        strategy_id=strategy_id,
        action=action,
        invalidation=(Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)),
    )
    risk = RiskManager(configuration or RiskManagerConfiguration()).evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    entry = DecisionJournalEntry(
        context,
        evaluation,
        risk,
        OperationalSafetyGate().evaluate(risk, _health(risk)),
    )
    intent = EntryOrderIntentAuthorizer().authorize(entry).intent
    assert intent is not None
    return PortfolioAdmissionCandidate(entry, intent)


def _coordinator(*, latency: timedelta = timedelta(0)) -> PaperPortfolioCoordinator:
    return PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(
            spread_bps=Decimal(2),
            slippage_bps=Decimal(3),
            fee_bps=Decimal(4),
            latency=latency,
        )
    )


def test_one_valid_candidate_starts_one_pending_lifecycle_and_reservation() -> None:
    candidate = _candidate()
    coordinator = _coordinator(latency=timedelta(seconds=7))

    result = coordinator.start_batch((candidate,))
    state = coordinator.snapshot()

    assert result.status is PaperPortfolioStartBatchStatus.STARTED
    assert len(result.admission_result.permits) == len(result.started) == 1
    assert len(state.started) == len(state.reservations) == 1
    started = result.started[0]
    reservation = result.active_reservations.reservations[0]
    assert started.status is PaperOrderStatus.PENDING
    assert started.eligible_at == NOW + timedelta(seconds=7)
    assert reservation.intent_identity == candidate.intent.identity
    assert reservation.reserved_risk_quote == candidate.intent.approved_risk_quote
    assert reservation.reserved_notional_quote == candidate.intent.approved_notional_quote


def test_btc_and_eth_start_atomically_when_combined_caps_fit() -> None:
    coordinator = _coordinator()

    result = coordinator.start_batch((_candidate(symbol=ETH), _candidate(symbol=BTC)))

    assert result.status is PaperPortfolioStartBatchStatus.STARTED
    assert tuple(item.symbol for item in result.started) == (BTC, ETH)
    assert tuple(item.symbol for item in coordinator.snapshot().reservations) == (BTC, ETH)


@pytest.mark.parametrize("cross_source", [False, True])
def test_same_symbol_conflict_starts_no_lifecycle_or_reservation(cross_source: bool) -> None:
    second_source = SourceId("other") if cross_source else SourceId("archive")
    coordinator = _coordinator()

    result = coordinator.start_batch(
        (
            _candidate(strategy_id="a"),
            _candidate(source_id=second_source, strategy_id="b"),
        )
    )

    assert result.status is PaperPortfolioStartBatchStatus.NO_START
    assert result.started == ()
    assert coordinator.snapshot().reservations == ()


def test_btc_conflict_starts_only_unrelated_eth() -> None:
    coordinator = _coordinator()

    result = coordinator.start_batch(
        (
            _candidate(strategy_id="btc-a"),
            _candidate(strategy_id="btc-b"),
            _candidate(symbol=ETH),
        )
    )

    assert tuple(item.symbol for item in result.started) == (ETH,)
    assert tuple(item.symbol for item in coordinator.snapshot().reservations) == (ETH,)


def test_over_cap_multi_symbol_batch_starts_no_subset() -> None:
    configuration = RiskManagerConfiguration(max_simultaneous_risk_fraction=Decimal("0.0049"))
    coordinator = _coordinator()

    result = coordinator.start_batch(
        (
            _candidate(symbol=BTC, configuration=configuration),
            _candidate(symbol=ETH, configuration=configuration),
        )
    )

    assert result.status is PaperPortfolioStartBatchStatus.NO_START
    assert result.started == ()
    assert coordinator.snapshot().reservations == ()


def test_owned_btc_reservation_blocks_later_btc_without_caller_facts() -> None:
    coordinator = _coordinator()
    coordinator.start_batch((_candidate(),))

    later = coordinator.start_batch(
        (_candidate(observed_at=NOW + timedelta(minutes=15), strategy_id="later"),)
    )

    assert later.status is PaperPortfolioStartBatchStatus.NO_START
    assert later.started == ()
    assert tuple(item.symbol for item in coordinator.snapshot().reservations) == (BTC,)


def test_owned_btc_reservation_allows_later_eth_at_exact_risk_capacity() -> None:
    coordinator = _coordinator()
    first = coordinator.start_batch((_candidate(),))

    later = coordinator.start_batch(
        (_candidate(observed_at=NOW + timedelta(minutes=15), symbol=ETH),)
    )

    assert first.active_reservations.reserved_risk_quote == Decimal(25)
    assert later.status is PaperPortfolioStartBatchStatus.STARTED
    assert tuple(item.symbol for item in coordinator.snapshot().reservations) == (BTC, ETH)


def test_owned_pending_capacity_can_reject_later_candidate() -> None:
    coordinator = _coordinator()
    coordinator.start_batch((_candidate(),))
    tighter = RiskManagerConfiguration(max_simultaneous_risk_fraction=Decimal("0.0049"))

    later = coordinator.start_batch(
        (
            _candidate(
                observed_at=NOW + timedelta(minutes=15),
                symbol=ETH,
                configuration=tighter,
            ),
        )
    )

    assert later.status is PaperPortfolioStartBatchStatus.NO_START
    assert tuple(item.symbol for item in coordinator.snapshot().reservations) == (BTC,)


def test_start_api_accepts_no_external_pending_snapshot() -> None:
    signature = inspect.signature(PaperPortfolioCoordinator.start_batch)

    assert tuple(signature.parameters) == ("self", "candidates")


def test_existing_admission_gate_is_called_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = PortfolioAdmissionGate.evaluate_batch
    calls = 0

    def counted_evaluate(
        self: PortfolioAdmissionGate,
        candidates: tuple[PortfolioAdmissionCandidate[object, object], ...],
        pending_reservations: PendingReservationSnapshot,
    ) -> PortfolioAdmissionBatchResult:
        nonlocal calls
        calls += 1
        return original(self, candidates, pending_reservations)

    monkeypatch.setattr(PortfolioAdmissionGate, "evaluate_batch", counted_evaluate)

    _coordinator().start_batch((_candidate(),))

    assert calls == 1


def test_same_or_earlier_timestamp_after_finalization_fails_closed() -> None:
    coordinator = _coordinator()
    coordinator.start_batch((_candidate(),))

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        coordinator.start_batch((_candidate(symbol=ETH),))
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        coordinator.start_batch((_candidate(observed_at=NOW - timedelta(minutes=15), symbol=ETH),))

    assert coordinator.snapshot().last_finalized_at == NOW


def test_no_admission_seals_timestamp_and_later_timestamp_works() -> None:
    coordinator = _coordinator()
    no_start = coordinator.start_batch((_candidate(strategy_id="a"), _candidate(strategy_id="b")))

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        coordinator.start_batch((_candidate(symbol=ETH),))
    later = coordinator.start_batch(
        (_candidate(observed_at=NOW + timedelta(minutes=15), symbol=ETH),)
    )

    assert no_start.status is PaperPortfolioStartBatchStatus.NO_START
    assert later.status is PaperPortfolioStartBatchStatus.STARTED


def test_all_candidate_times_must_match_without_mutation() -> None:
    coordinator = _coordinator()

    with pytest.raises(PaperPortfolioBatchError, match="share one observed_at"):
        coordinator.start_batch(
            (
                _candidate(),
                _candidate(observed_at=NOW + timedelta(minutes=15), symbol=ETH),
            )
        )

    assert coordinator.snapshot().last_finalized_at is None
    assert coordinator.snapshot().reservations == ()


def test_integrity_rejection_does_not_seal_time_and_corrected_retry_starts() -> None:
    malformed = _candidate()
    object.__setattr__(malformed.intent, "quantity", malformed.intent.quantity + Decimal(1))
    coordinator = _coordinator()

    rejected = coordinator.start_batch((malformed,))
    corrected = coordinator.start_batch((_candidate(),))

    assert rejected.status is PaperPortfolioStartBatchStatus.REJECTED_INCONSISTENT
    assert rejected.started == ()
    assert corrected.status is PaperPortfolioStartBatchStatus.STARTED
    assert coordinator.snapshot().last_finalized_at == NOW


def test_one_staged_start_failure_commits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = _coordinator()
    original = PaperOrderSimulator.start
    calls = 0

    def fail_second_start(
        self: PaperOrderSimulator,
        journal_entry: DecisionJournalEntry[object, object],
        intent: EntryOrderIntent,
        costs: SimulatedExecutionCostConfiguration,
    ) -> PaperOrderLifecycle:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected staging failure")
        return original(self, journal_entry, intent, costs)

    monkeypatch.setattr(PaperOrderSimulator, "start", fail_second_start)
    with pytest.raises(PaperPortfolioStartError, match="before commit"):
        coordinator.start_batch((_candidate(), _candidate(symbol=ETH)))

    assert coordinator.snapshot().last_finalized_at is None
    assert coordinator.snapshot().started == ()
    assert coordinator.snapshot().reservations == ()
    monkeypatch.undo()
    retry = coordinator.start_batch((_candidate(), _candidate(symbol=ETH)))
    assert retry.status is PaperPortfolioStartBatchStatus.STARTED


def test_input_permutation_produces_equal_result_and_state() -> None:
    btc = _candidate()
    eth = _candidate(symbol=ETH)
    left = _coordinator()
    right = _coordinator()

    left_result = left.start_batch((btc, eth))
    right_result = right.start_batch((eth, btc))

    assert left_result == right_result
    assert left.snapshot() == right.snapshot()


def test_returned_snapshots_cannot_mutate_internal_authority() -> None:
    candidate = _candidate()
    coordinator = _coordinator()
    result = coordinator.start_batch((candidate,))
    returned_started = result.started[0]
    returned_reservation = result.active_reservations.reservations[0]
    original_state = coordinator.snapshot()

    object.__setattr__(returned_started, "quantity", Decimal("1E+50"))
    object.__setattr__(returned_started.source_id, "value", "mutated-output")
    object.__setattr__(returned_reservation, "reserved_risk_quote", Decimal("1E+50"))

    assert coordinator.snapshot() == original_state
    with pytest.raises(FrozenInstanceError):
        original_state.started = ()  # type: ignore[misc]


def test_original_candidate_mutation_cannot_poison_internal_lifecycle() -> None:
    candidate = _candidate()
    coordinator = _coordinator()
    coordinator.start_batch((candidate,))
    original_state = coordinator.snapshot()

    object.__setattr__(candidate.intent, "quantity", Decimal("1E+50"))
    object.__setattr__(
        candidate.journal_entry.risk_decision,
        "approved_quantity",
        Decimal("1E+50"),
    )

    assert coordinator.snapshot() == original_state


def test_started_summary_exactly_preserves_intent_and_permit_authority() -> None:
    candidate = _candidate(action=CandidateAction.SHORT_CANDIDATE)
    result = _coordinator(latency=timedelta(seconds=2)).start_batch((candidate,))
    started = result.started[0]
    permit = result.admission_result.permits[0]

    assert started.intent_identity == permit.intent_identity == candidate.intent.identity
    assert started.side is permit.side is candidate.intent.side is EntrySide.SELL
    assert started.quantity == permit.quantity == candidate.intent.quantity
    assert started.approved_risk_quote == permit.approved_risk_quote
    assert started.approved_notional_quote == permit.approved_notional_quote
    assert started.stop_basis == permit.stop_basis == candidate.intent.stop_basis
    assert started.reference_price == permit.reference_price == candidate.intent.reference_price
    assert started.risk_policy_version == permit.risk_policy_version
    assert started.safety_policy_version == permit.safety_policy_version
    assert started.status is PaperOrderStatus.PENDING


def test_coordinator_never_observes_or_creates_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_observe(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"observe must not be called: {args}, {kwargs}")

    monkeypatch.setattr(PaperOrderSimulator, "observe", forbidden_observe)

    result = _coordinator().start_batch((_candidate(),))

    assert result.started[0].status is PaperOrderStatus.PENDING
    assert not hasattr(result.started[0], "fill")


def test_coordinator_has_no_forbidden_dependencies_or_account_behavior() -> None:
    path = Path(coordinator_module.__file__ or "")
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
        for forbidden in (
            "backtest",
            "exchange",
            "sqlalchemy",
            "database",
            "requests",
            "httpx",
        )
    )
    coordinator = _coordinator()
    for method in ("observe", "fill", "cancel", "release", "submit"):
        assert not hasattr(coordinator, method)
