import inspect
import runpy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, getcontext, localcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.paper.coordinator as coordinator_module
from adaptive_trading_bot.accounting import PositionAccountingCore
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.paper import (
    PaperAccountConfiguration,
    PaperAccountError,
    PaperAccountSnapshot,
    PaperClosedTradeSnapshot,
    PaperOrderSimulator,
    PaperPortfolioBatchError,
    PaperPortfolioCoordinator,
    PaperPortfolioExitExecutionError,
    PaperPortfolioObservationError,
    PaperPortfolioStartError,
    PaperProtectiveExitSimulator,
)
from adaptive_trading_bot.risk import (
    RiskDecisionStatus,
    RiskManager,
    RiskManagerConfiguration,
    RiskReasonCode,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("protective_exit_helpers.py")))
NOW: datetime = _HELPERS["NOW"]
BTC: TradingSymbol = _HELPERS["BTC"]
ETH: TradingSymbol = _HELPERS["ETH"]
candidate = _HELPERS["candidate"]
observation = _HELPERS["observation"]
_context = _HELPERS["_context"]
_manual_evaluation = _HELPERS["_manual_evaluation"]


def paper(
    *,
    latency: timedelta = timedelta(0),
    spread_bps: Decimal = Decimal(2),
    slippage_bps: Decimal = Decimal(3),
    fee_bps: Decimal = Decimal(4),
    account: PaperAccountConfiguration | None = None,
) -> PaperPortfolioCoordinator:
    return PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            fee_bps=fee_bps,
            latency=latency,
        ),
        account,
    )


def authorized_candidate(
    coordinator: PaperPortfolioCoordinator,
    *,
    observed_at: datetime,
    symbol: TradingSymbol = BTC,
    configuration: RiskManagerConfiguration | None = None,
) -> PortfolioAdmissionCandidate[object, object]:
    return cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(
            observed_at=observed_at,
            symbol=symbol,
            portfolio=coordinator.risk_portfolio_snapshot(observed_at),
            configuration=configuration,
        ),
    )


def open_one(
    coordinator: PaperPortfolioCoordinator,
    *,
    item: PortfolioAdmissionCandidate[object, object] | None = None,
    fill_at: datetime = NOW,
    fill_price: Decimal = Decimal(100),
) -> None:
    owned = authorized_candidate(coordinator, observed_at=NOW) if item is None else item
    coordinator.start_batch((owned,))
    coordinator.observe_batch((observation(owned.intent.symbol, fill_at, fill_price),))


def close_one(
    coordinator: PaperPortfolioCoordinator,
    *,
    trigger_at: datetime,
    execute_at: datetime,
    exit_price: Decimal,
) -> PaperClosedTradeSnapshot:
    coordinator.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    return coordinator.observe_protective_exits(
        (observation(BTC, execute_at, exit_price),)
    ).newly_closed[0]


def account_values(snapshot: PaperAccountSnapshot) -> tuple[object, ...]:
    return (
        snapshot.quote_currency,
        snapshot.initial_equity_quote,
        snapshot.account_equity_quote,
        snapshot.day_start_equity_quote,
        snapshot.peak_equity_quote,
        snapshot.realized_gross_pnl_quote,
        snapshot.cumulative_entry_fees_quote,
        snapshot.cumulative_exit_fees_quote,
        snapshot.cumulative_fees_quote,
        snapshot.cumulative_net_pnl_quote,
        snapshot.utc_day,
        snapshot.account_policy_version,
    )


def decimal_sum(*values: Decimal) -> Decimal:
    with localcontext(Context(prec=34)):
        return sum(values, Decimal(0))


def decimal_difference(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(Context(prec=34)):
        return left - right


def test_default_and_custom_initial_account_are_exact_and_detached() -> None:
    default = paper()
    custom = paper(account=PaperAccountConfiguration(Decimal("12345.67")))

    initial = default.account_snapshot(NOW)
    configured = custom.account_snapshot(NOW)

    assert initial.account_equity_quote == initial.day_start_equity_quote == Decimal(10000)
    assert initial.peak_equity_quote == initial.initial_equity_quote == Decimal(10000)
    assert initial.cumulative_fees_quote == initial.cumulative_net_pnl_quote == 0
    assert configured.account_equity_quote == configured.peak_equity_quote == Decimal("12345.67")
    with pytest.raises(FrozenInstanceError):
        initial.account_equity_quote = Decimal(1)  # type: ignore[misc]
    object.__setattr__(initial, "account_equity_quote", Decimal(1))
    assert default.account_snapshot(NOW).account_equity_quote == Decimal(10000)


@pytest.mark.parametrize(
    "value",
    [0.1, Decimal(0), Decimal(-1), Decimal("NaN"), Decimal("Infinity")],
)
def test_invalid_initial_equity_fails_closed(value: object) -> None:
    with pytest.raises((TypeError, PaperAccountError)):
        PaperAccountConfiguration(cast(Decimal, value))
    with pytest.raises(PaperAccountError):
        PaperAccountConfiguration(Decimal(1), "USD")


def test_entry_fee_is_charged_once_and_pre_eligibility_is_free() -> None:
    coordinator = paper(latency=timedelta(minutes=1))
    item = authorized_candidate(coordinator, observed_at=NOW)
    coordinator.start_batch((item,))
    coordinator.observe_batch((observation(BTC, NOW + timedelta(seconds=30), Decimal(100)),))
    assert coordinator.account_snapshot(NOW + timedelta(seconds=30)).cumulative_fees_quote == 0

    opened = coordinator.observe_batch(
        (observation(BTC, NOW + timedelta(minutes=1), Decimal(100)),)
    ).newly_filled[0]
    account = coordinator.account_snapshot(NOW + timedelta(minutes=1))

    assert account.cumulative_entry_fees_quote == opened.fee_quote
    assert account.cumulative_exit_fees_quote == 0
    assert account.realized_gross_pnl_quote == 0
    assert account.account_equity_quote == decimal_difference(Decimal(10000), opened.fee_quote)


def test_same_time_entry_fees_sum_atomically_and_are_input_order_invariant() -> None:
    left, right = paper(), paper()
    left_items = (
        authorized_candidate(left, observed_at=NOW, symbol=BTC),
        authorized_candidate(left, observed_at=NOW, symbol=ETH),
    )
    right_items = (
        authorized_candidate(right, observed_at=NOW, symbol=ETH),
        authorized_candidate(right, observed_at=NOW, symbol=BTC),
    )
    left.start_batch(left_items)
    right.start_batch(right_items)
    left_result = left.observe_batch(
        (observation(ETH, NOW, Decimal(90)), observation(BTC, NOW, Decimal(100)))
    )
    right.observe_batch((observation(BTC, NOW, Decimal(100)), observation(ETH, NOW, Decimal(90))))

    expected = decimal_sum(*(item.fee_quote for item in left_result.newly_filled))
    assert left.account_snapshot(NOW).cumulative_entry_fees_quote == expected
    assert account_values(left.account_snapshot(NOW)) == account_values(right.account_snapshot(NOW))


@pytest.mark.parametrize(
    "target",
    ["simulator", "adapter", "accounting"],
)
def test_entry_stage_failure_leaves_account_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    coordinator = paper()
    coordinator.start_batch((authorized_candidate(coordinator, observed_at=NOW),))
    before = coordinator.account_snapshot(NOW)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected entry failure")

    if target == "simulator":
        monkeypatch.setattr(PaperOrderSimulator, "observe", fail)
    elif target == "adapter":
        monkeypatch.setattr(coordinator_module, "_paper_fill_to_position_accounting_fill", fail)
    else:
        monkeypatch.setattr(PositionAccountingCore, "process", fail)
    with pytest.raises(PaperPortfolioObservationError):
        coordinator.observe_batch((observation(BTC, NOW, Decimal(100)),))

    assert account_values(coordinator.account_snapshot(NOW)) == account_values(before)


@pytest.mark.parametrize(
    ("exit_price", "profitable"),
    [(Decimal(130), True), (Decimal(70), False)],
)
def test_exit_uses_shared_gross_delta_and_exit_fee_once(
    exit_price: Decimal,
    profitable: bool,
) -> None:
    coordinator = paper()
    open_one(coordinator)
    after_entry = coordinator.account_snapshot(NOW)
    closed = close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=exit_price,
    )
    after = coordinator.account_snapshot(NOW + timedelta(minutes=1, microseconds=1))

    assert after.realized_gross_pnl_quote == closed.gross_realized_pnl
    assert after.cumulative_entry_fees_quote == closed.entry_fee_quote
    assert after.cumulative_exit_fees_quote == closed.exit_fee_quote
    assert after.cumulative_net_pnl_quote == closed.net_realized_pnl
    assert after.account_equity_quote == decimal_sum(Decimal(10000), closed.net_realized_pnl)
    assert decimal_difference(
        after.cumulative_net_pnl_quote,
        after_entry.cumulative_net_pnl_quote,
    ) == decimal_difference(closed.gross_realized_pnl, closed.exit_fee_quote)
    if profitable:
        assert after.peak_equity_quote == after.account_equity_quote > Decimal(10000)
    else:
        assert after.account_equity_quote < Decimal(10000)
        assert after.peak_equity_quote == Decimal(10000)


def test_same_time_multi_symbol_closes_are_atomic_and_order_invariant() -> None:
    def complete(
        reverse: bool,
    ) -> tuple[PaperAccountSnapshot, tuple[PaperClosedTradeSnapshot, ...]]:
        coordinator = paper()
        items = (
            authorized_candidate(coordinator, observed_at=NOW, symbol=BTC),
            authorized_candidate(coordinator, observed_at=NOW, symbol=ETH),
        )
        coordinator.start_batch(tuple(reversed(items)) if reverse else items)
        entries = (
            observation(BTC, NOW, Decimal(100)),
            observation(ETH, NOW, Decimal(100)),
        )
        coordinator.observe_batch(tuple(reversed(entries)) if reverse else entries)
        trigger_at = NOW + timedelta(minutes=1)
        triggers = (
            observation(BTC, trigger_at, Decimal(75)),
            observation(ETH, trigger_at, Decimal(75)),
        )
        coordinator.evaluate_protective_stops(tuple(reversed(triggers)) if reverse else triggers)
        execute_at = trigger_at + timedelta(microseconds=1)
        exits = (
            observation(BTC, execute_at, Decimal(70)),
            observation(ETH, execute_at, Decimal(130)),
        )
        result = coordinator.observe_protective_exits(tuple(reversed(exits)) if reverse else exits)
        return coordinator.account_snapshot(execute_at), result.newly_closed

    left, left_closed = complete(False)
    right, right_closed = complete(True)

    assert account_values(left) == account_values(right)
    assert left.cumulative_net_pnl_quote == decimal_difference(
        left.realized_gross_pnl_quote, left.cumulative_fees_quote
    )
    assert left_closed == right_closed


@pytest.mark.parametrize(
    "target",
    ["simulator", "adapter", "accounting", "audit"],
)
def test_exit_stage_failure_leaves_account_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    coordinator = paper()
    open_one(coordinator)
    trigger_at = NOW + timedelta(minutes=1)
    coordinator.evaluate_protective_stops((observation(BTC, trigger_at, Decimal(75)),))
    before = coordinator.account_snapshot(trigger_at)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected exit failure")

    if target == "simulator":
        monkeypatch.setattr(PaperProtectiveExitSimulator, "observe", fail)
    elif target == "adapter":
        monkeypatch.setattr(
            coordinator_module, "_paper_exit_fill_to_position_accounting_fill", fail
        )
    elif target == "accounting":
        monkeypatch.setattr(PositionAccountingCore, "process", fail)
    else:
        monkeypatch.setattr(coordinator_module, "_closed_trade_snapshot", fail)
    execute_at = trigger_at + timedelta(microseconds=1)
    with pytest.raises(PaperPortfolioExitExecutionError):
        coordinator.observe_protective_exits((observation(BTC, execute_at, Decimal(70)),))

    assert account_values(coordinator.account_snapshot(execute_at)) == account_values(before)


def test_reopen_preserves_history_and_charges_only_new_entry_fee() -> None:
    coordinator = paper()
    open_one(coordinator)
    close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=Decimal(70),
    )
    before = coordinator.account_snapshot(NOW + timedelta(minutes=1, microseconds=1))
    reopen_at = NOW + timedelta(minutes=15)
    coordinator.start_batch((authorized_candidate(coordinator, observed_at=reopen_at),))
    opened = coordinator.observe_batch((observation(BTC, reopen_at, Decimal(100)),)).newly_filled[0]
    after = coordinator.account_snapshot(reopen_at)

    assert after.realized_gross_pnl_quote == before.realized_gross_pnl_quote
    assert after.cumulative_exit_fees_quote == before.cumulative_exit_fees_quote
    assert after.cumulative_entry_fees_quote == decimal_sum(
        before.cumulative_entry_fees_quote, opened.fee_quote
    )


def test_utc_day_rollover_uses_pre_event_equity_and_read_projection_is_pure() -> None:
    coordinator = paper()
    open_one(coordinator)
    after_entry = coordinator.account_snapshot(NOW)
    next_day = (NOW + timedelta(days=1)).replace(hour=0, minute=1)

    projected = coordinator.account_snapshot(next_day)
    assert projected.day_start_equity_quote == after_entry.account_equity_quote
    assert coordinator.snapshot().last_finalized_at == NOW
    assert coordinator.snapshot().last_observed_at == NOW

    coordinator.evaluate_protective_stops((observation(BTC, next_day, Decimal(100)),))
    committed = coordinator.account_snapshot(next_day)
    later = coordinator.account_snapshot(next_day + timedelta(hours=1))
    assert committed.day_start_equity_quote == after_entry.account_equity_quote
    assert later.day_start_equity_quote == committed.day_start_equity_quote
    with pytest.raises(PaperPortfolioBatchError, match="precede"):
        coordinator.account_snapshot(NOW)


def test_authoritative_risk_snapshot_uses_exact_owned_positions_not_reservations() -> None:
    coordinator = paper()
    item = authorized_candidate(coordinator, observed_at=NOW)
    coordinator.start_batch((item,))
    pending = coordinator.risk_portfolio_snapshot(NOW)
    assert pending.positions == ()

    coordinator.observe_batch((observation(BTC, NOW, Decimal(110)),))
    snapshot = coordinator.risk_portfolio_snapshot(NOW)
    opened = coordinator.snapshot().open_positions[0]
    assert snapshot.account_equity_quote == coordinator.account_snapshot(NOW).account_equity_quote
    assert snapshot.positions[0].symbol == BTC
    assert snapshot.positions[0].quantity == opened.quantity
    assert snapshot.positions[0].open_risk_quote == opened.locked_open_risk_quote
    assert snapshot.positions[0].gross_exposure_quote == opened.locked_gross_exposure_quote


@pytest.mark.parametrize(
    "field",
    [
        "account_equity_quote",
        "day_start_equity_quote",
        "peak_equity_quote",
        "current_open_risk_quote",
        "current_gross_exposure_quote",
        "risk_snapshot_observed_at",
    ],
)
def test_forged_or_stale_candidate_account_baseline_fails_closed(field: str) -> None:
    coordinator = paper()
    open_one(coordinator)
    at = NOW + timedelta(minutes=15)
    item = authorized_candidate(coordinator, observed_at=at, symbol=ETH)
    before = coordinator.snapshot()
    risk = item.journal_entry.risk_decision
    forged: object = (
        at - timedelta(microseconds=1) if field == "risk_snapshot_observed_at" else Decimal(1)
    )
    object.__setattr__(risk, field, forged)

    with pytest.raises(PaperPortfolioStartError, match="account state"):
        coordinator.start_batch((item,))
    assert coordinator.snapshot() == before


def test_correct_post_change_baseline_is_admitted_but_stale_pre_fee_is_not() -> None:
    coordinator = paper()
    open_one(coordinator)
    at = NOW + timedelta(minutes=15)
    stale = cast(
        PortfolioAdmissionCandidate[object, object],
        candidate(
            observed_at=at, symbol=ETH, positions=coordinator.risk_portfolio_snapshot(at).positions
        ),
    )
    with pytest.raises(PaperPortfolioStartError, match="account state"):
        coordinator.start_batch((stale,))

    correct = authorized_candidate(coordinator, observed_at=at, symbol=ETH)
    assert coordinator.start_batch((correct,)).started[0].symbol == ETH


def test_realized_loss_rejects_stale_pre_loss_account_baseline() -> None:
    coordinator = paper()
    open_one(coordinator)
    close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=Decimal(70),
    )
    at = NOW + timedelta(minutes=15)

    with pytest.raises(PaperPortfolioStartError, match="account state"):
        coordinator.start_batch(
            (cast(PortfolioAdmissionCandidate[object, object], candidate(observed_at=at)),)
        )


def test_realized_profit_rejects_stale_pre_profit_peak() -> None:
    coordinator = paper()
    open_one(coordinator)
    close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=Decimal(130),
    )
    at = NOW + timedelta(minutes=15)
    item = authorized_candidate(coordinator, observed_at=at)
    object.__setattr__(item.journal_entry.risk_decision, "peak_equity_quote", Decimal(10000))

    with pytest.raises(PaperPortfolioStartError, match="account state"):
        coordinator.start_batch((item,))


def test_market_observations_do_not_mark_account_to_market() -> None:
    coordinator = paper()
    open_one(coordinator)
    before = coordinator.account_snapshot(NOW)
    later = NOW + timedelta(minutes=15)
    coordinator.evaluate_protective_stops((observation(BTC, later, Decimal(130)),))

    assert account_values(coordinator.account_snapshot(later)) == account_values(before)


def test_snapshot_apis_accept_no_caller_account_or_position_overrides() -> None:
    assert tuple(inspect.signature(PaperPortfolioCoordinator.account_snapshot).parameters) == (
        "self",
        "observed_at",
    )
    assert tuple(
        inspect.signature(PaperPortfolioCoordinator.risk_portfolio_snapshot).parameters
    ) == ("self", "observed_at")


def test_risk_manager_daily_loss_boundary_uses_paper_facts() -> None:
    configuration = RiskManagerConfiguration(
        risk_per_trade_fraction=Decimal("0.10"),
        max_simultaneous_risk_fraction=Decimal("0.20"),
        max_daily_loss_fraction=Decimal("0.01"),
        max_drawdown_fraction=Decimal("0.50"),
    )
    coordinator = paper(spread_bps=Decimal(0), slippage_bps=Decimal(0), fee_bps=Decimal(0))
    item = authorized_candidate(coordinator, observed_at=NOW, configuration=configuration)
    open_one(coordinator, item=item)
    close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=Decimal(97),
    )
    at = NOW + timedelta(minutes=15)
    evaluation = _manual_evaluation(
        _context(source_id=SourceId("archive"), symbol=ETH, observed_at=at),
        strategy_id="daily-boundary",
        action=CandidateAction.LONG_CANDIDATE,
        invalidation=Decimal(75),
    )
    decision = RiskManager(configuration).evaluate(
        evaluation, coordinator.risk_portfolio_snapshot(at)
    )

    assert decision.daily_loss_quote == Decimal(100)
    assert decision.daily_loss_fraction == configuration.max_daily_loss_fraction
    assert decision.status is RiskDecisionStatus.REJECTED
    assert RiskReasonCode.DAILY_LOSS_LIMIT_REACHED in decision.reasons


def test_risk_manager_drawdown_boundary_uses_paper_facts_after_day_rollover() -> None:
    configuration = RiskManagerConfiguration(
        risk_per_trade_fraction=Decimal("0.10"),
        max_simultaneous_risk_fraction=Decimal("0.20"),
        max_daily_loss_fraction=Decimal("0.90"),
        max_drawdown_fraction=Decimal("0.05"),
    )
    coordinator = paper(spread_bps=Decimal(0), slippage_bps=Decimal(0), fee_bps=Decimal(0))
    item = authorized_candidate(coordinator, observed_at=NOW, configuration=configuration)
    open_one(coordinator, item=item)
    close_one(
        coordinator,
        trigger_at=NOW + timedelta(minutes=1),
        execute_at=NOW + timedelta(minutes=1, microseconds=1),
        exit_price=Decimal(85),
    )
    at = NOW + timedelta(days=1)
    evaluation = _manual_evaluation(
        _context(source_id=SourceId("archive"), symbol=ETH, observed_at=at),
        strategy_id="drawdown-boundary",
        action=CandidateAction.LONG_CANDIDATE,
        invalidation=Decimal(75),
    )
    snapshot = coordinator.risk_portfolio_snapshot(at)
    decision = RiskManager(configuration).evaluate(evaluation, snapshot)

    assert snapshot.day_start_equity_quote == snapshot.account_equity_quote == Decimal(9500)
    assert decision.drawdown_quote == Decimal(500)
    assert decision.drawdown_fraction == configuration.max_drawdown_fraction
    assert decision.status is RiskDecisionStatus.REJECTED
    assert RiskReasonCode.DRAWDOWN_LIMIT_REACHED in decision.reasons
    assert RiskReasonCode.DAILY_LOSS_LIMIT_REACHED not in decision.reasons


def test_nonpositive_equity_preserved_and_blocks_risk_snapshot_and_entry() -> None:
    coordinator = paper(
        spread_bps=Decimal(0),
        slippage_bps=Decimal(0),
        fee_bps=Decimal(2_000_000),
    )
    open_one(coordinator)
    account = coordinator.account_snapshot(NOW)
    assert account.account_equity_quote < 0
    at = NOW + timedelta(minutes=15)
    with pytest.raises(PaperAccountError, match="non-positive"):
        coordinator.risk_portfolio_snapshot(at)
    with pytest.raises(PaperPortfolioStartError, match="account state"):
        coordinator.start_batch(
            (
                cast(
                    PortfolioAdmissionCandidate[object, object],
                    candidate(observed_at=at, symbol=ETH),
                ),
            )
        )


def test_account_results_ignore_ambient_decimal_context() -> None:
    original = getcontext().copy()
    try:
        low = Context(prec=6)
        low.traps[InvalidOperation] = False
        setcontext(low)
        left = paper()
        open_one(left)
        close_one(
            left,
            trigger_at=NOW + timedelta(minutes=1),
            execute_at=NOW + timedelta(minutes=1, microseconds=1),
            exit_price=Decimal("70.123456789"),
        )
        low_result = account_values(left.account_snapshot(NOW + timedelta(minutes=2)))

        setcontext(Context(prec=50))
        right = paper()
        open_one(right)
        close_one(
            right,
            trigger_at=NOW + timedelta(minutes=1),
            execute_at=NOW + timedelta(minutes=1, microseconds=1),
            exit_price=Decimal("70.123456789"),
        )
        high_result = account_values(right.account_snapshot(NOW + timedelta(minutes=2)))
    finally:
        setcontext(original)

    assert low_result == high_result
