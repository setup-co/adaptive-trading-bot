import runpy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import NoReturn

import pytest

import adaptive_trading_bot.paper.coordinator as coordinator_module
from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFill,
    PositionAccountingFillBatch,
    PositionAccountingTransitionBatch,
)
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, EntrySide
from adaptive_trading_bot.paper import (
    PaperExecutionObservation,
    PaperFillAccountingAdapterError,
    PaperMarketFill,
    PaperOpenPositionSnapshot,
    PaperOrderLifecycle,
    PaperOrderSimulator,
    PaperOrderStatus,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioObservationError,
    PaperPortfolioStartBatchStatus,
    PaperPortfolioStartError,
)
from adaptive_trading_bot.paper.accounting import paper_fill_to_position_accounting_fill
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    RiskPositionExposure,
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
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def candidate(
    *,
    observed_at: datetime = NOW,
    symbol: TradingSymbol = BTC,
    source_id: SourceId = SOURCE,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    positions: tuple[RiskPositionExposure, ...] = (),
    configuration: RiskManagerConfiguration | None = None,
    portfolio: RiskPortfolioSnapshot | None = None,
) -> PortfolioAdmissionCandidate[object, object]:
    context = _context(source_id=source_id, symbol=symbol, observed_at=observed_at)
    evaluation = _manual_evaluation(
        context,
        strategy_id=f"manual-{source_id.value}-{symbol.base}",
        action=action,
        invalidation=(Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)),
    )
    risk = RiskManager(configuration or RiskManagerConfiguration()).evaluate(
        evaluation,
        portfolio
        or RiskPortfolioSnapshot(
            observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
            positions,
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


def coordinator(
    *,
    latency: timedelta = timedelta(0),
    spread_bps: Decimal = Decimal(2),
    slippage_bps: Decimal = Decimal(3),
    fee_bps: Decimal = Decimal(4),
) -> PaperPortfolioCoordinator:
    return PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
            latency=latency,
        )
    )


def observation(
    symbol: TradingSymbol,
    observed_at: datetime = NOW,
    *,
    source_id: SourceId = SOURCE,
    price: Decimal = Decimal(100),
) -> PaperExecutionObservation:
    return PaperExecutionObservation(source_id, symbol, observed_at, price)


def risk_position_from_open(item: PaperOpenPositionSnapshot) -> RiskPositionExposure:
    quantity = item.quantity
    return RiskPositionExposure(
        item.symbol,
        quantity if item.side is EntrySide.BUY else -quantity,
        item.locked_gross_exposure_quote,
        item.locked_open_risk_quote,
    )


def test_one_eligible_buy_converts_reservation_to_accounted_open_position() -> None:
    item = candidate()
    paper = coordinator()
    paper.start_batch((item,))

    result = paper.observe_batch((observation(BTC),))
    state = paper.snapshot()

    assert len(result.newly_filled) == 1
    opened = result.newly_filled[0]
    assert opened.side is EntrySide.BUY
    assert opened.quantity == item.intent.quantity
    assert opened.fill_price == Decimal("100.0400")
    with localcontext(_DECIMAL_CONTEXT):
        expected_fee = item.intent.quantity * opened.fill_price * Decimal(4) / Decimal(10000)
    assert opened.fee_quote == expected_fee
    assert result.accounting_positions[0][1].quantity == item.intent.quantity
    assert result.accounting_positions[0][1].average_entry_price == opened.fill_price
    assert len(result.accounting_transitions) == 1
    assert result.active_pending_reservations.reservations == ()
    assert state.reservations == ()
    assert state.open_positions == result.open_positions == (opened,)


def test_sell_entry_creates_exact_short_from_shared_simulation() -> None:
    item = candidate(action=CandidateAction.SHORT_CANDIDATE)
    paper = coordinator()
    paper.start_batch((item,))

    result = paper.observe_batch((observation(BTC),))
    opened = result.newly_filled[0]

    assert opened.side is EntrySide.SELL
    assert opened.quantity == item.intent.quantity
    assert opened.fill_price == Decimal("99.9600")
    with localcontext(_DECIMAL_CONTEXT):
        expected_fee = opened.quantity * opened.fill_price * Decimal(4) / Decimal(10000)
        expected_quantity = -opened.quantity
    assert opened.fee_quote == expected_fee
    assert result.accounting_positions[0][1].quantity == expected_quantity


def test_btc_and_eth_fill_in_one_atomic_canonical_accounting_batch() -> None:
    paper = coordinator()
    paper.start_batch((candidate(symbol=ETH), candidate(symbol=BTC)))

    result = paper.observe_batch((observation(ETH), observation(BTC)))

    assert tuple(item.symbol for item in result.newly_filled) == (BTC, ETH)
    assert tuple(item.symbol for item in result.open_positions) == (BTC, ETH)
    assert tuple(item.fill.symbol for item in result.accounting_transitions) == (BTC, ETH)
    assert result.active_pending_reservations.reservations == ()


def test_preeligible_observation_advances_pending_without_accounting() -> None:
    paper = coordinator(latency=timedelta(minutes=15))
    paper.start_batch((candidate(),))

    result = paper.observe_batch((observation(BTC, NOW + timedelta(minutes=5)),))

    assert result.newly_filled == ()
    assert result.accounting_transitions == ()
    assert result.accounting_positions == ()
    assert len(result.still_pending) == 1
    assert len(result.active_pending_reservations.reservations) == 1
    assert result.open_positions == ()
    assert paper.snapshot().last_observed_at == NOW + timedelta(minutes=5)


def test_observation_batch_requires_a_nonempty_immutable_tuple() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="non-empty"):
        paper.observe_batch(())
    with pytest.raises(TypeError, match="immutable tuple"):
        paper.observe_batch([observation(BTC)])  # type: ignore[arg-type]

    assert paper.snapshot() == before


def test_observation_batch_rejects_mixed_timestamps_atomically() -> None:
    paper = coordinator()
    paper.start_batch((candidate(), candidate(symbol=ETH)))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="share one observed_at"):
        paper.observe_batch((observation(BTC), observation(ETH, NOW + timedelta(microseconds=1))))

    assert paper.snapshot() == before


def test_mixed_eligibility_atomically_fills_old_and_keeps_new_pending() -> None:
    paper = coordinator(latency=timedelta(minutes=20))
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC, NOW + timedelta(minutes=5)),))
    later = NOW + timedelta(minutes=15)
    paper.start_batch((candidate(observed_at=later, symbol=ETH),))

    result = paper.observe_batch(
        (
            observation(ETH, NOW + timedelta(minutes=20)),
            observation(BTC, NOW + timedelta(minutes=20)),
        )
    )

    assert tuple(item.symbol for item in result.newly_filled) == (BTC,)
    assert tuple(item.symbol for item in result.still_pending) == (ETH,)
    assert tuple(item.symbol for item in result.active_pending_reservations.reservations) == (ETH,)
    assert tuple(item.symbol for item in result.open_positions) == (BTC,)


def test_invalid_second_observation_in_multi_symbol_batch_commits_nothing() -> None:
    paper = coordinator()
    paper.start_batch((candidate(), candidate(symbol=ETH)))
    before = paper.snapshot()
    invalid = observation(ETH)
    object.__setattr__(invalid, "reference_price", Decimal(-1))

    with pytest.raises(PaperPortfolioBatchError, match="malformed"):
        paper.observe_batch((observation(BTC), invalid))

    assert paper.snapshot() == before


def test_injected_simulator_failure_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    paper = coordinator()
    paper.start_batch((candidate(), candidate(symbol=ETH)))
    before = paper.snapshot()
    original = PaperOrderSimulator.observe
    calls = 0

    def fail_second(
        self: PaperOrderSimulator,
        lifecycle: PaperOrderLifecycle,
        observed: PaperExecutionObservation,
    ) -> PaperOrderLifecycle:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected simulator failure")
        return original(self, lifecycle, observed)

    monkeypatch.setattr(PaperOrderSimulator, "observe", fail_second)
    with pytest.raises(PaperPortfolioObservationError, match="atomic commit"):
        paper.observe_batch((observation(BTC), observation(ETH)))

    assert paper.snapshot() == before


def test_injected_adapter_failure_commits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    paper = coordinator()
    paper.start_batch((candidate(), candidate(symbol=ETH)))
    before = paper.snapshot()
    original = paper_fill_to_position_accounting_fill
    calls = 0

    def fail_second(fill: PaperMarketFill) -> PositionAccountingFill:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PaperFillAccountingAdapterError("injected adapter failure")
        return original(fill)

    monkeypatch.setattr(coordinator_module, "_paper_fill_to_position_accounting_fill", fail_second)
    with pytest.raises(PaperPortfolioObservationError, match="atomic commit"):
        paper.observe_batch((observation(BTC), observation(ETH)))

    assert paper.snapshot() == before


def test_injected_shared_accounting_failure_commits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()

    def fail_accounting(
        self: PositionAccountingCore,
        batch: PositionAccountingFillBatch,
    ) -> PositionAccountingTransitionBatch:
        raise RuntimeError(f"injected accounting failure: {self}, {batch}")

    monkeypatch.setattr(PositionAccountingCore, "process", fail_accounting)
    with pytest.raises(PaperPortfolioObservationError, match="atomic commit"):
        paper.observe_batch((observation(BTC),))

    assert paper.snapshot() == before


def test_entry_accounting_requires_a_flat_symbol_before_staging_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()
    original_clone = PositionAccountingCore.clone

    def occupied_clone(self: PositionAccountingCore) -> PositionAccountingCore:
        cloned = original_clone(self)
        occupied = PositionAccountingFill(
            "injected-existing-position",
            BTC,
            SimulatedExecutionSide.BUY,
            Decimal(1),
            NOW - timedelta(minutes=1),
            Decimal(100),
            Decimal(0),
        )
        cloned.process(PositionAccountingFillBatch(occupied.fill_at, (occupied,)))
        return cloned

    monkeypatch.setattr(PositionAccountingCore, "clone", occupied_clone)
    with pytest.raises(PaperPortfolioObservationError, match="must be flat"):
        paper.observe_batch((observation(BTC),))

    assert paper.snapshot() == before


@pytest.mark.parametrize(
    "observations",
    [
        (observation(ETH),),
        (observation(BTC, source_id=SourceId("wrong")),),
        (observation(BTC), observation(BTC, source_id=SourceId("wrong"))),
    ],
)
def test_unknown_wrong_source_and_duplicate_symbol_observations_fail_closed(
    observations: tuple[PaperExecutionObservation, ...],
) -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError):
        paper.observe_batch(observations)

    assert paper.snapshot() == before


def test_observation_permutation_produces_equal_result_and_state() -> None:
    left = coordinator()
    right = coordinator()
    left.start_batch((candidate(), candidate(symbol=ETH)))
    right.start_batch((candidate(symbol=ETH), candidate()))

    left_result = left.observe_batch((observation(BTC), observation(ETH)))
    right_result = right.observe_batch((observation(ETH), observation(BTC)))

    assert left_result == right_result
    assert left.snapshot() == right.snapshot()


def test_start_and_observation_chronology_is_globally_fail_closed() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC),))
    committed = paper.snapshot()

    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.observe_batch((observation(BTC),))
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.observe_batch((observation(BTC, NOW - timedelta(microseconds=1)),))
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.start_batch((candidate(observed_at=NOW, symbol=ETH),))
    with pytest.raises(PaperPortfolioBatchError, match="strictly advance"):
        paper.start_batch((candidate(observed_at=NOW - timedelta(minutes=15), symbol=ETH),))

    assert paper.snapshot() == committed


def test_observation_cannot_precede_latest_start_but_equal_time_is_allowed() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))

    with pytest.raises(PaperPortfolioBatchError, match="cannot precede"):
        paper.observe_batch((observation(BTC, NOW - timedelta(microseconds=1)),))

    result = paper.observe_batch((observation(BTC, NOW),))
    assert len(result.newly_filled) == 1


def test_returned_and_old_snapshots_and_original_candidate_are_detached() -> None:
    original = candidate()
    expected_quantity = original.intent.quantity
    paper = coordinator()
    started = paper.start_batch((original,))
    object.__setattr__(started.started[0], "quantity", Decimal(999))
    object.__setattr__(original.intent, "quantity", Decimal(888))

    result = paper.observe_batch((observation(BTC),))
    internal = paper.snapshot()
    object.__setattr__(result.newly_filled[0], "quantity", Decimal(777))
    object.__setattr__(result.newly_filled[0].source_id, "value", "mutated")
    object.__setattr__(result.accounting_transitions[0].fill, "quantity", Decimal(666))

    assert paper.snapshot() == internal
    assert internal.open_positions[0].quantity == expected_quantity


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("quantity", Decimal(999)),
        ("side", EntrySide.SELL),
        ("fill_price", Decimal(999)),
        ("fee_quote", Decimal(999)),
    ],
)
def test_forged_coordinated_fill_never_reaches_adapter_or_accounting(
    field_name: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()
    original_observe = PaperOrderSimulator.observe

    def forge(
        self: PaperOrderSimulator,
        lifecycle: PaperOrderLifecycle,
        observed: PaperExecutionObservation,
    ) -> PaperOrderLifecycle:
        advanced = original_observe(self, lifecycle, observed)
        assert advanced.fill is not None
        object.__setattr__(advanced.fill, field_name, value)
        return advanced

    def forbidden_adapter(fill: object) -> NoReturn:
        raise AssertionError(f"forged fill reached adapter: {fill}")

    monkeypatch.setattr(PaperOrderSimulator, "observe", forge)
    monkeypatch.setattr(
        coordinator_module,
        "_paper_fill_to_position_accounting_fill",
        forbidden_adapter,
    )
    with pytest.raises(PaperPortfolioObservationError, match="diverged"):
        paper.observe_batch((observation(BTC),))

    assert paper.snapshot() == before


def test_pending_reservation_is_removed_once_and_open_symbol_remains_occupied() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    filled = paper.observe_batch((observation(BTC),))
    opened = filled.open_positions[0]
    correct_totals_on_other_symbol = (
        RiskPositionExposure(
            ETH,
            Decimal(1),
            opened.locked_gross_exposure_quote,
            opened.locked_open_risk_quote,
        ),
    )
    same_symbol = candidate(
        observed_at=NOW + timedelta(minutes=15),
        source_id=SourceId("other"),
        positions=correct_totals_on_other_symbol,
    )

    with pytest.raises(PaperPortfolioStartError, match="occupied"):
        paper.start_batch((same_symbol,))
    with pytest.raises(PaperPortfolioBatchError):
        paper.observe_batch((observation(BTC, NOW + timedelta(minutes=15)),))

    assert paper.snapshot().reservations == ()
    assert len(paper.snapshot().open_positions) == 1


def test_correct_open_baseline_allows_unrelated_symbol_and_caps_include_open() -> None:
    paper = coordinator(latency=timedelta(minutes=1))
    paper.start_batch((candidate(),))
    opened = paper.observe_batch(
        (observation(BTC, NOW + timedelta(minutes=1), price=Decimal(110)),)
    ).open_positions[0]
    baseline = (risk_position_from_open(opened),)

    result = paper.start_batch(
        (
            candidate(
                observed_at=NOW + timedelta(minutes=15),
                symbol=ETH,
                portfolio=RiskPortfolioSnapshot(
                    NOW + timedelta(minutes=15),
                    "USDT",
                    paper.account_snapshot(NOW + timedelta(minutes=15)).account_equity_quote,
                    paper.account_snapshot(NOW + timedelta(minutes=15)).day_start_equity_quote,
                    paper.account_snapshot(NOW + timedelta(minutes=15)).peak_equity_quote,
                    baseline,
                ),
            ),
        )
    )

    assert result.status is PaperPortfolioStartBatchStatus.STARTED
    with localcontext(_DECIMAL_CONTEXT):
        expected_remaining_risk = (
            paper.account_snapshot(NOW + timedelta(minutes=15)).account_equity_quote
            * Decimal("0.0050")
            - opened.locked_open_risk_quote
        )
    assert result.active_reservations.reserved_risk_quote == expected_remaining_risk


@pytest.mark.parametrize(
    ("risk_adjustment", "gross_adjustment"),
    [
        (Decimal(-1), Decimal(0)),
        (Decimal(0), Decimal(-1)),
        (Decimal(-1), Decimal(-1)),
    ],
)
def test_mismatched_open_capacity_baseline_rejects_without_admission(
    risk_adjustment: Decimal,
    gross_adjustment: Decimal,
) -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    opened = paper.observe_batch((observation(BTC, price=Decimal(110)),)).open_positions[0]
    mismatched = (
        RiskPositionExposure(
            BTC,
            opened.quantity,
            opened.locked_gross_exposure_quote + gross_adjustment,
            opened.locked_open_risk_quote + risk_adjustment,
        ),
    )
    later = candidate(
        observed_at=NOW + timedelta(minutes=15),
        symbol=ETH,
        positions=mismatched,
    )
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioStartError, match="baseline"):
        paper.start_batch((later,))

    assert paper.snapshot() == before


def test_zero_or_stale_open_capacity_baseline_fails_closed() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    paper.observe_batch((observation(BTC),))
    later_with_zero_baseline = candidate(
        observed_at=NOW + timedelta(minutes=15),
        symbol=ETH,
    )
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioStartError, match="baseline"):
        paper.start_batch((later_with_zero_baseline,))

    assert paper.snapshot() == before


def test_locked_capacity_uses_exact_adverse_fill_max_and_never_decreases_approval() -> None:
    paper = coordinator()
    authorized = candidate()
    paper.start_batch((authorized,))

    opened = paper.observe_batch((observation(BTC, price=Decimal(110)),)).open_positions[0]

    with localcontext(_DECIMAL_CONTEXT):
        expected_gross = max(
            authorized.intent.approved_notional_quote,
            opened.quantity * opened.fill_price,
        )
        expected_risk = max(
            authorized.intent.approved_risk_quote,
            opened.quantity * abs(opened.fill_price - opened.stop_basis),
        )
    assert opened.locked_gross_exposure_quote == expected_gross
    assert opened.locked_open_risk_quote == expected_risk
    assert opened.locked_gross_exposure_quote > authorized.intent.approved_notional_quote
    assert opened.locked_open_risk_quote > authorized.intent.approved_risk_quote


def test_favorable_fill_keeps_approved_risk_and_notional_capacity_locked() -> None:
    paper = coordinator(spread_bps=Decimal(0), slippage_bps=Decimal(0))
    authorized = candidate()
    paper.start_batch((authorized,))

    opened = paper.observe_batch((observation(BTC, price=Decimal(90)),)).open_positions[0]

    assert opened.quantity * opened.fill_price < authorized.intent.approved_notional_quote
    assert opened.quantity * abs(opened.fill_price - opened.stop_basis) < (
        authorized.intent.approved_risk_quote
    )
    assert opened.locked_gross_exposure_quote == authorized.intent.approved_notional_quote
    assert opened.locked_open_risk_quote == authorized.intent.approved_risk_quote


def test_invalid_post_fill_stop_geometry_fails_before_accounting_commit() -> None:
    paper = coordinator()
    paper.start_batch((candidate(),))
    before = paper.snapshot()

    with pytest.raises(PaperPortfolioObservationError, match="stop basis"):
        paper.observe_batch((observation(BTC, price=Decimal(70)),))

    assert paper.snapshot() == before


def test_lifecycle_clone_is_independent_from_raw_mutation() -> None:
    item = candidate()
    simulator = PaperOrderSimulator()
    lifecycle = simulator.start(
        item.journal_entry,
        item.intent,
        SimulatedExecutionCostConfiguration(),
    )
    cloned = simulator.clone(lifecycle)

    object.__setattr__(cloned.intent, "quantity", Decimal(999))

    assert lifecycle.intent.quantity == item.intent.quantity
    filled = simulator.observe(lifecycle, observation(BTC))
    assert filled.status is PaperOrderStatus.FILLED
