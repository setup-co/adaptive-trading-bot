import inspect
import runpy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.paper.coordinator as coordinator_module
import adaptive_trading_bot.paper.exit_execution as exit_execution_module
from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFillBatch,
    PositionAccountingTransitionBatch,
)
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import (
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.orders import ExitOrderIntent, ExitSide
from adaptive_trading_bot.paper import (
    PaperExecutionObservation,
    PaperExitFillAccountingAdapterError,
    PaperExitOrderStatus,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioCoordinatorError,
    PaperPortfolioExitExecutionError,
    PaperPortfolioStartBatchStatus,
    PaperPortfolioStartError,
    PaperProtectiveExitLifecycle,
    PaperProtectiveExitSimulator,
)
from adaptive_trading_bot.paper.exit_accounting import (
    paper_exit_fill_to_position_accounting_fill,
)
from adaptive_trading_bot.risk import RiskPositionExposure
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("protective_exit_helpers.py")))
NOW = _HELPERS["NOW"]
BTC = _HELPERS["BTC"]
ETH = _HELPERS["ETH"]
OTHER_SOURCE = _HELPERS["OTHER_SOURCE"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]
observation = _HELPERS["observation"]
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def prepared_exit(
    *,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    latency: timedelta = timedelta(0),
) -> tuple[
    PaperPortfolioCoordinator,
    PortfolioAdmissionCandidate[object, object],
    ExitOrderIntent,
]:
    paper = cast(PaperPortfolioCoordinator, coordinator(latency=latency))
    item = cast(PortfolioAdmissionCandidate[object, object], candidate(action=action))
    paper.start_batch((item,))
    entry_fill_at = NOW + latency
    paper.observe_batch((observation(BTC, entry_fill_at, Decimal(100)),))
    trigger_at = entry_fill_at + timedelta(minutes=1)
    stop = Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)
    trigger = paper.evaluate_protective_stops((observation(BTC, trigger_at, stop),))
    return paper, item, trigger.triggered[0]


def first_eligible_at(intent: ExitOrderIntent, latency: timedelta) -> datetime:
    eligible_at = intent.authorized_at + latency
    if eligible_at == intent.authorized_at:
        eligible_at += timedelta(microseconds=1)
    return eligible_at


@pytest.mark.parametrize(
    ("action", "exit_price", "exit_side", "accounting_side"),
    (
        (
            CandidateAction.LONG_CANDIDATE,
            Decimal(70),
            ExitSide.SELL,
            SimulatedExecutionSide.SELL,
        ),
        (
            CandidateAction.SHORT_CANDIDATE,
            Decimal(140),
            ExitSide.BUY,
            SimulatedExecutionSide.BUY,
        ),
    ),
)
def test_exit_fill_uses_shared_pricing_and_accounting_to_close_exactly_flat(
    action: CandidateAction,
    exit_price: Decimal,
    exit_side: ExitSide,
    accounting_side: SimulatedExecutionSide,
) -> None:
    paper, _, intent = prepared_exit(action=action)
    before = paper.snapshot()
    fill_at = first_eligible_at(intent, timedelta(0))
    result = paper.observe_protective_exits((observation(BTC, fill_at, exit_price),))

    closed = result.newly_closed[0]
    transition = result.accounting_transitions[0]
    quote = price_simulated_market_fill(
        side=accounting_side,
        quantity=intent.quantity,
        reference_price=exit_price,
        costs=paper._costs,
    )
    assert closed.exit_fill.side is exit_side
    assert closed.exit_fill.quantity == intent.quantity
    assert closed.exit_fill.reference_price == exit_price
    assert closed.exit_fill.fill_price == quote.fill_price
    assert closed.exit_fill.fill_price != intent.stop_basis
    assert closed.exit_fee_quote == quote.fee_quote
    opened = before.open_positions[0]
    assert closed.originating_entry_intent_identity == opened.intent_identity
    assert closed.originating_decision_identity == opened.decision_identity
    assert closed.originating_paper_order_identity == opened.paper_order_identity
    assert closed.exit_intent_identity == intent.identity
    assert closed.entry_fill_at == opened.fill_at
    assert closed.entry_fill_price == opened.fill_price
    assert closed.entry_fee_quote == opened.fee_quote
    assert closed.stop_basis == intent.stop_basis
    assert closed.trigger_observed_at == intent.trigger_observed_at
    assert closed.trigger_reference_price == intent.trigger_reference_price
    assert closed.risk_policy_version == intent.risk_policy_version
    assert closed.safety_policy_version == intent.safety_policy_version
    assert closed.accounting_exit_fill_id == transition.fill.fill_id
    assert transition.position_quantity_after == 0
    assert transition.closed_quantity == intent.quantity
    assert transition.average_entry_after is None
    assert result.accounting_positions[0][1].quantity == 0
    assert result.accounting_positions[0][1].average_entry_price is None
    with localcontext(_DECIMAL_CONTEXT):
        expected_fees = before.open_positions[0].fee_quote + quote.fee_quote
        expected_net = transition.gross_realized_pnl_delta - expected_fees
    assert closed.trade_fees_quote == expected_fees
    assert closed.net_realized_pnl == expected_net
    assert closed.gross_realized_pnl == transition.gross_realized_pnl_delta
    assert result.open_positions == ()
    assert result.active_exit_intents == ()
    assert result.locked_open_risk_quote == 0
    assert result.locked_gross_exposure_quote == 0


@pytest.mark.parametrize(
    ("action", "execution_price", "positive"),
    (
        (CandidateAction.LONG_CANDIDATE, Decimal(110), True),
        (CandidateAction.LONG_CANDIDATE, Decimal(70), False),
        (CandidateAction.SHORT_CANDIDATE, Decimal(90), True),
        (CandidateAction.SHORT_CANDIDATE, Decimal(140), False),
    ),
)
def test_favorable_and_adverse_pnl_comes_from_shared_transition(
    action: CandidateAction, execution_price: Decimal, positive: bool
) -> None:
    paper, _, intent = prepared_exit(action=action)

    result = paper.observe_protective_exits(
        (
            observation(
                BTC,
                first_eligible_at(intent, timedelta(0)),
                execution_price,
            ),
        )
    )

    delta = result.accounting_transitions[0].gross_realized_pnl_delta
    assert (delta > 0) is positive
    assert result.newly_closed[0].gross_realized_pnl == delta


def test_preeligibility_advances_exit_without_accounting_or_release() -> None:
    latency = timedelta(minutes=10)
    paper, _, intent = prepared_exit(latency=latency)
    before = paper.snapshot()
    observed_at = intent.authorized_at + timedelta(minutes=5)

    result = paper.observe_protective_exits((observation(BTC, observed_at, Decimal(70)),))

    assert result.newly_closed == ()
    assert result.accounting_transitions == ()
    assert result.open_positions == before.open_positions
    assert result.accounting_positions == before.accounting_positions
    assert result.locked_open_risk_quote == before.locked_open_risk_quote
    assert result.locked_gross_exposure_quote == before.locked_gross_exposure_quote
    assert len(result.still_pending_exits) == 1
    pending = result.still_pending_exits[0]
    assert pending.status is PaperExitOrderStatus.PENDING
    assert pending.last_observed_at == observed_at
    assert pending.eligible_at == intent.authorized_at + latency
    assert pending.fill is None


def test_exit_lifecycle_observations_must_strictly_advance() -> None:
    latency = timedelta(minutes=10)
    paper, _, intent = prepared_exit(latency=latency)
    lifecycle = paper._active_exit_intents[0].lifecycle
    simulator = PaperProtectiveExitSimulator()
    at = intent.authorized_at + timedelta(minutes=5)
    advanced = simulator.observe(lifecycle, observation(BTC, at, Decimal(70)))

    with pytest.raises(ValueError, match="strictly advance"):
        simulator.observe(advanced, observation(BTC, at, Decimal(69)))


def test_btc_and_eth_close_in_one_canonical_atomic_accounting_batch() -> None:
    items = (candidate(symbol=ETH), candidate(symbol=BTC))
    paper = cast(PaperPortfolioCoordinator, coordinator())
    paper.start_batch(tuple(items))
    paper.observe_batch((observation(ETH, NOW, Decimal(100)), observation(BTC, NOW, Decimal(100))))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops(
        (observation(ETH, trigger_at, Decimal(75)), observation(BTC, trigger_at, Decimal(75)))
    )
    fill_at = trigger_at + timedelta(microseconds=1)

    result = paper.observe_protective_exits(
        (observation(ETH, fill_at, Decimal(70)), observation(BTC, fill_at, Decimal(70)))
    )

    assert tuple(item.symbol for item in result.newly_closed) == (BTC, ETH)
    assert tuple(item.fill.symbol for item in result.accounting_transitions) == (BTC, ETH)
    assert all(state.quantity == 0 for _, state in result.accounting_positions)
    assert result.open_positions == ()
    assert result.active_exit_intents == ()


def test_mixed_eligibility_closes_one_and_keeps_other_atomically() -> None:
    latency = timedelta(minutes=10)
    paper = cast(PaperPortfolioCoordinator, coordinator(latency=latency))
    items = (candidate(symbol=BTC), candidate(symbol=ETH))
    paper.start_batch(items)
    entry_at = NOW + latency
    paper.observe_batch(
        (observation(BTC, entry_at, Decimal(100)), observation(ETH, entry_at, Decimal(100)))
    )
    btc_trigger = entry_at + timedelta(minutes=1)
    paper.evaluate_protective_stops((observation(BTC, btc_trigger, Decimal(75)),))
    eth_trigger = entry_at + timedelta(minutes=6)
    paper.evaluate_protective_stops((observation(ETH, eth_trigger, Decimal(75)),))
    execute_at = btc_trigger + latency

    result = paper.observe_protective_exits(
        (observation(ETH, execute_at, Decimal(70)), observation(BTC, execute_at, Decimal(70)))
    )

    assert tuple(item.symbol for item in result.newly_closed) == (BTC,)
    assert tuple(item.intent.symbol for item in result.still_pending_exits) == (ETH,)
    assert tuple(item.symbol for item in result.open_positions) == (ETH,)
    remaining = result.open_positions[0]
    assert result.locked_open_risk_quote == remaining.locked_open_risk_quote
    assert result.locked_gross_exposure_quote == remaining.locked_gross_exposure_quote


def test_execution_input_permutation_produces_equal_result_and_state() -> None:
    def ready() -> PaperPortfolioCoordinator:
        paper = cast(PaperPortfolioCoordinator, coordinator())
        items = (candidate(symbol=BTC), candidate(symbol=ETH))
        paper.start_batch(items)
        paper.observe_batch(
            (observation(BTC, NOW, Decimal(100)), observation(ETH, NOW, Decimal(100)))
        )
        at = NOW + timedelta(minutes=1)
        paper.evaluate_protective_stops(
            (observation(BTC, at, Decimal(75)), observation(ETH, at, Decimal(75)))
        )
        return paper

    left = ready()
    right = ready()
    at = NOW + timedelta(minutes=1, microseconds=1)
    left_result = left.observe_protective_exits(
        (observation(ETH, at, Decimal(70)), observation(BTC, at, Decimal(70)))
    )
    right_result = right.observe_protective_exits(
        (observation(BTC, at, Decimal(70)), observation(ETH, at, Decimal(70)))
    )

    assert left_result == right_result
    assert left.snapshot() == right.snapshot()


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("quantity", Decimal(999)),
        ("side", ExitSide.BUY),
        ("stop_basis", Decimal(1)),
        ("source_id", SourceId("forged")),
        ("symbol", TradingSymbol("ETH", "USDT")),
        ("risk_policy_version", "forged"),
        ("safety_policy_version", "forged"),
        ("trigger_reference_price", Decimal(1)),
        ("trigger_observed_at", NOW + timedelta(days=1)),
    ),
)
def test_mutated_owned_exit_authority_fails_before_pricing_or_accounting(
    monkeypatch: pytest.MonkeyPatch, field_name: str, value: object
) -> None:
    paper, _, intent = prepared_exit()
    before = paper.snapshot()
    owned = paper._active_exit_intents[0].lifecycle.intent
    object.__setattr__(owned, field_name, value)
    pricing_calls = 0

    def forbidden_pricing(**kwargs: object) -> object:
        nonlocal pricing_calls
        pricing_calls += 1
        raise AssertionError("pricing must not run")

    monkeypatch.setattr(exit_execution_module, "price_simulated_market_fill", forbidden_pricing)

    with pytest.raises(PaperPortfolioCoordinatorError):
        paper.observe_protective_exits(
            (
                observation(
                    BTC,
                    first_eligible_at(intent, timedelta(0)),
                    Decimal(70),
                ),
            )
        )

    assert pricing_calls == 0
    object.__setattr__(owned, field_name, getattr(before.active_exit_intents[0], field_name))


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("quantity", Decimal(1)),
        ("side", ExitSide.BUY),
        ("fill_price", Decimal(1)),
        ("fee_quote", Decimal(1)),
    ),
)
def test_forged_exit_fill_fails_before_shared_accounting(
    monkeypatch: pytest.MonkeyPatch, field_name: str, value: object
) -> None:
    paper, _, intent = prepared_exit()
    before = paper.snapshot()
    real_observe = PaperProtectiveExitSimulator.observe
    accounting_calls = 0

    def forged_observe(
        self: PaperProtectiveExitSimulator,
        lifecycle: PaperProtectiveExitLifecycle,
        supplied: PaperExecutionObservation,
    ) -> PaperProtectiveExitLifecycle:
        advanced = real_observe(self, lifecycle, supplied)
        assert advanced.fill is not None
        object.__setattr__(advanced.fill, field_name, value)
        return advanced

    real_process = PositionAccountingCore.process

    def counted_process(
        self: PositionAccountingCore, batch: PositionAccountingFillBatch
    ) -> PositionAccountingTransitionBatch:
        nonlocal accounting_calls
        accounting_calls += 1
        return real_process(self, batch)

    monkeypatch.setattr(PaperProtectiveExitSimulator, "observe", forged_observe)
    monkeypatch.setattr(PositionAccountingCore, "process", counted_process)

    with pytest.raises(PaperPortfolioExitExecutionError):
        paper.observe_protective_exits(
            (
                observation(
                    BTC,
                    first_eligible_at(intent, timedelta(0)),
                    Decimal(70),
                ),
            )
        )

    assert accounting_calls == 0
    assert paper.snapshot() == before


def test_injected_pricing_adapter_and_accounting_failures_commit_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_with_failure(stage: str) -> None:
        paper, _, intent = prepared_exit()
        before = paper.snapshot()
        at = first_eligible_at(intent, timedelta(0))
        if stage == "simulator":
            monkeypatch.setattr(
                PaperProtectiveExitSimulator,
                "observe",
                lambda *args: (_ for _ in ()).throw(RuntimeError("simulator")),
            )
        elif stage == "pricing":
            monkeypatch.setattr(
                exit_execution_module,
                "price_simulated_market_fill",
                lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pricing")),
            )
        elif stage == "adapter":
            monkeypatch.setattr(
                coordinator_module,
                "_paper_exit_fill_to_position_accounting_fill",
                lambda *args: (_ for _ in ()).throw(PaperExitFillAccountingAdapterError("adapter")),
            )
        else:
            monkeypatch.setattr(
                PositionAccountingCore,
                "process",
                lambda *args: (_ for _ in ()).throw(RuntimeError("accounting")),
            )
        with pytest.raises(PaperPortfolioExitExecutionError, match="before atomic commit"):
            paper.observe_protective_exits((observation(BTC, at, Decimal(70)),))
        assert paper.snapshot() == before
        monkeypatch.undo()

    for stage in ("simulator", "pricing", "adapter", "accounting"):
        run_with_failure(stage)


def test_forged_partial_close_transition_causes_zero_coordinator_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper, _, intent = prepared_exit()
    before = paper.snapshot()
    real_process = PositionAccountingCore.process

    def forge_transition(
        self: PositionAccountingCore, batch: PositionAccountingFillBatch
    ) -> PositionAccountingTransitionBatch:
        result = real_process(self, batch)
        object.__setattr__(result.transitions[0], "position_quantity_after", Decimal("0.1"))
        return result

    monkeypatch.setattr(PositionAccountingCore, "process", forge_transition)

    with pytest.raises(PaperPortfolioExitExecutionError, match="full close"):
        paper.observe_protective_exits(
            (
                observation(
                    BTC,
                    first_eligible_at(intent, timedelta(0)),
                    Decimal(70),
                ),
            )
        )

    assert paper.snapshot() == before


def test_invalid_execution_observations_fail_whole_batch() -> None:
    paper, _, intent = prepared_exit()
    at = first_eligible_at(intent, timedelta(0))
    before = paper.snapshot()
    malformed = observation(BTC, at, Decimal(70))
    object.__setattr__(malformed, "reference_price", Decimal("NaN"))
    malformed_time = observation(BTC, at, Decimal(70))
    object.__setattr__(malformed_time, "observed_at", at.replace(tzinfo=None))

    for batch, error in (
        ((), PaperPortfolioBatchError),
        ((malformed,), PaperPortfolioBatchError),
        ((malformed_time,), PaperPortfolioBatchError),
        ((observation(BTC, at, Decimal(70), source_id=OTHER_SOURCE),), PaperPortfolioBatchError),
        ((observation(ETH, at, Decimal(70)),), PaperPortfolioBatchError),
        (
            (observation(BTC, at, Decimal(70)), observation(BTC, at, Decimal(71))),
            PaperPortfolioBatchError,
        ),
    ):
        with pytest.raises(error):
            paper.observe_protective_exits(batch)
        assert paper.snapshot() == before
    with pytest.raises(TypeError, match="immutable tuple"):
        paper.observe_protective_exits([])  # type: ignore[arg-type]
    with pytest.raises(PaperPortfolioBatchError, match="share one"):
        paper.observe_protective_exits(
            (
                observation(BTC, at, Decimal(70)),
                observation(ETH, at + timedelta(microseconds=1), Decimal(70)),
            )
        )
    assert paper.snapshot() == before


def test_invalid_second_exit_observation_prevents_first_close() -> None:
    paper = cast(PaperPortfolioCoordinator, coordinator())
    items = (candidate(symbol=BTC), candidate(symbol=ETH))
    paper.start_batch(items)
    paper.observe_batch((observation(BTC, NOW, Decimal(100)), observation(ETH, NOW, Decimal(100))))
    trigger_at = NOW + timedelta(minutes=1)
    paper.evaluate_protective_stops(
        (observation(BTC, trigger_at, Decimal(75)), observation(ETH, trigger_at, Decimal(75)))
    )
    execute_at = trigger_at + timedelta(microseconds=1)
    malformed = observation(ETH, execute_at, Decimal(70))
    object.__setattr__(malformed, "reference_price", Decimal(-1))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="malformed"):
        paper.observe_protective_exits((observation(BTC, execute_at, Decimal(70)), malformed))

    assert paper.snapshot() == before


def test_same_and_backward_execution_replay_and_all_later_apis_fail_closed() -> None:
    paper, _, intent = prepared_exit()
    at = first_eligible_at(intent, timedelta(0))
    paper.observe_protective_exits((observation(BTC, at, Decimal(70)),))
    before = paper.snapshot()

    for replay_at in (at, at - timedelta(microseconds=1)):
        with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
            paper.observe_protective_exits((observation(BTC, replay_at, Decimal(70)),))
        with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
            paper.evaluate_protective_stops((observation(BTC, replay_at, Decimal(70)),))
        with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
            paper.observe_batch((observation(BTC, replay_at, Decimal(70)),))
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.start_batch((candidate(observed_at=NOW),))
    assert paper.snapshot() == before


def test_capacity_and_symbol_release_enable_exact_post_close_admission() -> None:
    paper, _, intent = prepared_exit()
    at = first_eligible_at(intent, timedelta(0))
    paper.observe_protective_exits((observation(BTC, at, Decimal(70)),))
    later = NOW + timedelta(minutes=15)

    result = paper.start_batch(
        (candidate(observed_at=later, portfolio=paper.risk_portfolio_snapshot(later)),)
    )

    assert result.status is PaperPortfolioStartBatchStatus.STARTED
    assert paper.snapshot().locked_open_risk_quote == 0
    assert paper.snapshot().locked_gross_exposure_quote == 0
    reopened = paper.observe_batch((observation(BTC, later, Decimal(100)),))
    assert tuple(item.symbol for item in reopened.newly_filled) == (BTC,)
    assert reopened.accounting_positions[0][1].quantity > 0

    stale_paper, _, stale_intent = prepared_exit()
    stale_open = stale_paper.snapshot().open_positions[0]
    stale_paper.observe_protective_exits(
        (observation(BTC, first_eligible_at(stale_intent, timedelta(0)), Decimal(70)),)
    )
    stale_exposure = RiskPositionExposure(
        ETH,
        stale_open.quantity,
        stale_open.locked_gross_exposure_quote,
        stale_open.locked_open_risk_quote,
    )
    with pytest.raises(PaperPortfolioStartError, match="baseline"):
        stale_paper.start_batch(
            (
                candidate(
                    observed_at=later,
                    positions=(stale_exposure,),
                ),
            )
        )


def test_symbol_remains_blocked_until_successful_exit_fill() -> None:
    latency = timedelta(minutes=20)
    paper, _, intent = prepared_exit(latency=latency)
    opened = paper.snapshot().open_positions[0]
    later = NOW + timedelta(minutes=30)
    correct_totals_other_symbol = RiskPositionExposure(
        ETH,
        Decimal(1),
        opened.locked_gross_exposure_quote,
        opened.locked_open_risk_quote,
    )

    with pytest.raises(PaperPortfolioStartError, match="occupied"):
        paper.start_batch(
            (
                candidate(
                    observed_at=later,
                    source_id=SourceId("new-source"),
                    positions=(correct_totals_other_symbol,),
                ),
            )
        )

    assert paper.snapshot().active_exit_intents == (intent,)


def test_closed_exit_cannot_execute_or_release_capacity_twice() -> None:
    paper, _, intent = prepared_exit()
    at = first_eligible_at(intent, timedelta(0))
    paper.observe_protective_exits((observation(BTC, at, Decimal(70)),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="active"):
        paper.observe_protective_exits(
            (observation(BTC, at + timedelta(microseconds=1), Decimal(69)),)
        )

    assert paper.snapshot() == before


def test_returned_fill_closed_trade_and_accounting_are_detached() -> None:
    paper, _, intent = prepared_exit()
    result = paper.observe_protective_exits(
        (
            observation(
                BTC,
                first_eligible_at(intent, timedelta(0)),
                Decimal(70),
            ),
        )
    )
    closed = result.newly_closed[0]
    fill = closed.exit_fill
    transition = result.accounting_transitions[0]
    object.__setattr__(fill, "quantity", Decimal(999))
    object.__setattr__(closed, "net_realized_pnl", Decimal(999))
    object.__setattr__(transition.fill, "quantity", Decimal(999))

    state = paper.snapshot()
    assert state.open_positions == ()
    assert state.active_exit_intents == ()
    assert state.accounting_positions[0][1].quantity == 0


def test_mutated_returned_intent_and_old_open_snapshot_cannot_change_execution() -> None:
    paper, _, intent = prepared_exit()
    old_open = paper.snapshot().open_positions[0]
    expected_quantity = intent.quantity
    object.__setattr__(intent, "quantity", Decimal(999))
    object.__setattr__(intent, "side", ExitSide.BUY)
    object.__setattr__(old_open, "quantity", Decimal(999))
    object.__setattr__(old_open, "stop_basis", Decimal(1))
    internal_intent = paper.snapshot().active_exit_intents[0]

    result = paper.observe_protective_exits(
        (
            observation(
                BTC,
                first_eligible_at(internal_intent, timedelta(0)),
                Decimal(70),
            ),
        )
    )

    assert result.newly_closed[0].quantity == expected_quantity
    assert result.newly_closed[0].exit_fill.side is ExitSide.SELL


def test_public_api_accepts_no_caller_exit_intent_or_fill_and_lifecycle_is_sealed() -> None:
    parameters = inspect.signature(PaperPortfolioCoordinator.observe_protective_exits).parameters
    assert tuple(parameters) == ("self", "observations")
    with pytest.raises(TypeError, match="only be created"):
        PaperProtectiveExitLifecycle()  # type: ignore[call-arg]

    paper, _, returned_intent = prepared_exit()
    lifecycle = PaperProtectiveExitSimulator().start(returned_intent, paper._costs)
    original_quantity = lifecycle.intent.quantity
    object.__setattr__(returned_intent, "quantity", Decimal(999))

    assert PaperProtectiveExitSimulator().clone(lifecycle).intent.quantity == original_quantity


def test_exit_adapter_rejects_forged_fill() -> None:
    paper, _, intent = prepared_exit()
    lifecycle = paper._active_exit_intents[0].lifecycle
    advanced = PaperProtectiveExitSimulator().observe(
        lifecycle,
        observation(BTC, first_eligible_at(intent, timedelta(0)), Decimal(70)),
    )
    assert advanced.fill is not None
    object.__setattr__(advanced.fill, "fee_quote", Decimal(999))

    with pytest.raises(PaperExitFillAccountingAdapterError):
        paper_exit_fill_to_position_accounting_fill(advanced.fill, paper._costs)
