import runpy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import adaptive_trading_bot.paper.coordinator as coordinator_module
from adaptive_trading_bot.orders import (
    ExitOrderIntent,
    ExitSide,
    exit_order_intent_is_authoritative,
)
from adaptive_trading_bot.orders.exit_intent import _authorize_protective_stop_full_close
from adaptive_trading_bot.paper import (
    PaperPortfolioBatchError,
    PaperProtectiveExitAuthorizationError,
    PaperProtectiveStopStatus,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("protective_exit_helpers.py")))
BTC = _HELPERS["BTC"]
ETH = _HELPERS["ETH"]
NOW = _HELPERS["NOW"]
OTHER_SOURCE = _HELPERS["OTHER_SOURCE"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]
open_positions = _HELPERS["open_positions"]
risk_position = _HELPERS["risk_position"]


@pytest.mark.parametrize(
    ("action", "price", "expected_status", "expected_side"),
    (
        (
            CandidateAction.LONG_CANDIDATE,
            Decimal("75.01"),
            PaperProtectiveStopStatus.NOT_TRIGGERED,
            None,
        ),
        (
            CandidateAction.LONG_CANDIDATE,
            Decimal(75),
            PaperProtectiveStopStatus.TRIGGERED,
            ExitSide.SELL,
        ),
        (
            CandidateAction.LONG_CANDIDATE,
            Decimal("74.99"),
            PaperProtectiveStopStatus.TRIGGERED,
            ExitSide.SELL,
        ),
        (
            CandidateAction.SHORT_CANDIDATE,
            Decimal("134.99"),
            PaperProtectiveStopStatus.NOT_TRIGGERED,
            None,
        ),
        (
            CandidateAction.SHORT_CANDIDATE,
            Decimal(135),
            PaperProtectiveStopStatus.TRIGGERED,
            ExitSide.BUY,
        ),
        (
            CandidateAction.SHORT_CANDIDATE,
            Decimal("135.01"),
            PaperProtectiveStopStatus.TRIGGERED,
            ExitSide.BUY,
        ),
    ),
)
def test_exact_long_and_short_stop_trigger_semantics(
    action: CandidateAction,
    price: Decimal,
    expected_status: PaperProtectiveStopStatus,
    expected_side: ExitSide | None,
) -> None:
    item = candidate(action=action)
    paper = coordinator()
    open_positions(paper, item)
    before = paper.snapshot()
    trigger_at = NOW + timedelta(minutes=1)

    result = paper.evaluate_protective_stops((observation(BTC, trigger_at, price),))

    assert result.evaluations[0].status is expected_status
    assert len(result.triggered) == (1 if expected_side is not None else 0)
    if expected_side is not None:
        intent = result.triggered[0]
        opened = before.open_positions[0]
        assert exit_order_intent_is_authoritative(intent)
        assert intent.side is expected_side
        assert intent.quantity == opened.quantity
        assert intent.stop_basis == opened.stop_basis
        assert intent.trigger_reference_price == price
        assert intent.trigger_observed_at == intent.authorized_at == trigger_at
        assert intent.originating_entry_intent_identity == opened.intent_identity
        assert intent.originating_decision_identity == opened.decision_identity
        assert intent.risk_policy_version == opened.risk_policy_version
        assert intent.safety_policy_version == opened.safety_policy_version
    assert result.open_positions == before.open_positions
    assert result.accounting_positions == before.accounting_positions
    assert result.locked_open_risk_quote == before.locked_open_risk_quote
    assert result.locked_gross_exposure_quote == before.locked_gross_exposure_quote


def test_two_symbol_evaluation_is_canonical_order_invariant_and_atomic() -> None:
    items = (candidate(symbol=BTC), candidate(symbol=ETH))
    left = coordinator()
    right = coordinator()
    open_positions(left, *items)
    open_positions(right, *reversed(items))
    before = left.snapshot()
    at = NOW + timedelta(minutes=1)

    left_result = left.evaluate_protective_stops(
        (observation(ETH, at, Decimal(90)), observation(BTC, at, Decimal(75)))
    )
    right_result = right.evaluate_protective_stops(
        (observation(BTC, at, Decimal(75)), observation(ETH, at, Decimal(90)))
    )

    assert left_result == right_result
    assert left.snapshot() == right.snapshot()
    assert tuple(item.symbol for item in left_result.evaluations) == (BTC, ETH)
    assert tuple(item.symbol for item in left_result.triggered) == (BTC,)
    assert tuple(item.status for item in left_result.evaluations) == (
        PaperProtectiveStopStatus.TRIGGERED,
        PaperProtectiveStopStatus.NOT_TRIGGERED,
    )
    after = left.snapshot()
    assert after.open_positions == before.open_positions
    assert after.accounting_positions == before.accounting_positions
    assert after.locked_open_risk_quote == before.locked_open_risk_quote
    assert after.locked_gross_exposure_quote == before.locked_gross_exposure_quote


def test_existing_exit_is_deduped_and_capacity_remains_occupied() -> None:
    item = candidate()
    paper = coordinator()
    open_positions(paper, item)
    first = paper.evaluate_protective_stops(
        (observation(BTC, NOW + timedelta(minutes=1), Decimal(75)),)
    )
    before = paper.snapshot()

    second = paper.evaluate_protective_stops(
        (observation(BTC, NOW + timedelta(minutes=2), Decimal(70)),)
    )

    assert second.triggered == ()
    assert second.evaluations[0].status is PaperProtectiveStopStatus.ALREADY_PENDING
    assert second.active_exit_intents == first.active_exit_intents
    after = paper.snapshot()
    assert after.open_positions == before.open_positions
    assert after.accounting_positions == before.accounting_positions
    assert after.locked_open_risk_quote == before.locked_open_risk_quote
    assert after.locked_gross_exposure_quote == before.locked_gross_exposure_quote


@pytest.mark.parametrize("minutes", (0, -1))
def test_replay_or_backward_stop_timestamp_fails_without_mutation(minutes: int) -> None:
    paper = coordinator()
    open_positions(paper, candidate())
    at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, at, Decimal(80)),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.evaluate_protective_stops(
            (observation(BTC, at + timedelta(minutes=minutes), Decimal(75)),)
        )

    assert paper.snapshot() == before


def test_trigger_cannot_precede_fill() -> None:
    paper = coordinator()
    open_positions(paper, candidate())

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.evaluate_protective_stops(
            (observation(BTC, NOW - timedelta(microseconds=1), Decimal(75)),)
        )


def test_pending_only_unknown_wrong_source_and_duplicate_symbol_fail_closed() -> None:
    at = NOW + timedelta(minutes=1)
    pending = coordinator(latency=timedelta(minutes=5))
    pending.start_batch((candidate(),))
    cases = (
        (pending, (observation(BTC, at, Decimal(75)),), "match one open"),
        (coordinator(), (observation(BTC, at, Decimal(75)),), "match one open"),
    )
    for paper, batch, message in cases:
        before = paper.snapshot()
        with pytest.raises(PaperPortfolioBatchError, match=message):
            paper.evaluate_protective_stops(batch)
        assert paper.snapshot() == before

    opened = coordinator()
    open_positions(opened, candidate())
    before = opened.snapshot()
    with pytest.raises(PaperPortfolioBatchError, match="match one open"):
        opened.evaluate_protective_stops(
            (observation(BTC, at, Decimal(75), source_id=OTHER_SOURCE),)
        )
    with pytest.raises(PaperPortfolioBatchError, match="at most one"):
        opened.evaluate_protective_stops(
            (observation(BTC, at, Decimal(75)), observation(BTC, at, Decimal(74)))
        )
    assert opened.snapshot() == before


def test_malformed_or_mixed_observation_batch_fails_atomically() -> None:
    paper = coordinator()
    open_positions(paper, candidate(), candidate(symbol=ETH))
    at = NOW + timedelta(minutes=1)
    malformed = observation(ETH, at, Decimal(90))
    object.__setattr__(malformed, "reference_price", Decimal("NaN"))
    malformed_time = observation(ETH, at, Decimal(90))
    object.__setattr__(malformed_time, "observed_at", at.replace(tzinfo=None))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="malformed"):
        paper.evaluate_protective_stops((observation(BTC, at, Decimal(75)), malformed))
    with pytest.raises(PaperPortfolioBatchError, match="malformed"):
        paper.evaluate_protective_stops((observation(BTC, at, Decimal(75)), malformed_time))
    with pytest.raises(PaperPortfolioBatchError, match="share one"):
        paper.evaluate_protective_stops(
            (
                observation(BTC, at, Decimal(75)),
                observation(ETH, at + timedelta(seconds=1), Decimal(90)),
            )
        )
    with pytest.raises(PaperPortfolioBatchError, match="non-empty"):
        paper.evaluate_protective_stops(())
    with pytest.raises(TypeError, match="immutable tuple"):
        paper.evaluate_protective_stops([])
    assert paper.snapshot() == before


def test_committed_stop_evaluation_seals_start_and_entry_observation_chronology() -> None:
    paper = coordinator(latency=timedelta(minutes=10))
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC, NOW + timedelta(minutes=10), Decimal(100)),))
    opened = paper.snapshot().open_positions[0]
    eth_start = NOW + timedelta(minutes=15)
    paper.start_batch(
        (candidate(observed_at=eth_start, symbol=ETH, positions=(risk_position(opened),)),)
    )
    sealed_at = NOW + timedelta(minutes=16)
    paper.evaluate_protective_stops((observation(BTC, sealed_at, Decimal(80)),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.start_batch(
            (candidate(observed_at=NOW + timedelta(minutes=15), source_id=SourceId("new")),)
        )
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.observe_batch((observation(ETH, sealed_at, Decimal(100)),))

    assert paper.snapshot() == before


def test_returned_and_old_snapshots_cannot_mutate_owned_exit_authority() -> None:
    paper = coordinator()
    open_positions(paper, candidate())
    old_open = paper.snapshot().open_positions[0]
    result = paper.evaluate_protective_stops(
        (observation(BTC, NOW + timedelta(minutes=1), Decimal(75)),)
    )
    returned = result.triggered[0]
    expected_quantity = returned.quantity
    expected_side = returned.side
    object.__setattr__(returned, "quantity", expected_quantity * 100)
    object.__setattr__(returned, "side", ExitSide.BUY)
    object.__setattr__(old_open, "quantity", expected_quantity * 100)
    object.__setattr__(old_open, "stop_basis", Decimal(1))

    owned = paper.snapshot().active_exit_intents[0]
    assert exit_order_intent_is_authoritative(owned)
    assert owned.quantity == expected_quantity
    assert owned.side is expected_side
    assert owned.stop_basis == Decimal(75)


def test_injected_exit_authorizer_failure_commits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = coordinator()
    open_positions(paper, candidate(), candidate(symbol=ETH))
    before = paper.snapshot()
    calls = 0
    real = _authorize_protective_stop_full_close

    def fail_second(**kwargs: object) -> ExitOrderIntent:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected")
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(coordinator_module, "_authorize_protective_stop_full_close", fail_second)
    at = NOW + timedelta(minutes=1)

    with pytest.raises(PaperProtectiveExitAuthorizationError, match="before atomic commit"):
        paper.evaluate_protective_stops(
            (observation(BTC, at, Decimal(75)), observation(ETH, at, Decimal(75)))
        )

    assert paper.snapshot() == before
