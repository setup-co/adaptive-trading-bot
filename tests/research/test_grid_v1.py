from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingCoreError,
    PositionAccountingFill,
    PositionAccountingFillBatch,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionSide
from adaptive_trading_bot.research.grid_v1 import (
    GRID_ENGINE_VERSION,
    GRID_PROTOCOL_VERSION,
    GridAuditEvent,
    GridEpisodeInput,
    GridFundingEvent,
    GridGeometry,
    GridMinute,
    GridScenario,
    GridState,
    GridV1Engine,
    GridV1Error,
    healthy_grid_health,
    run_synthetic_grid_episode,
)
from adaptive_trading_bot.risk import FeedIntegrityState
from adaptive_trading_bot.storage import SourceId

START = datetime(2026, 1, 5, tzinfo=UTC)
Q = Decimal("3.972392")


def _episode(asset: str = "BTC") -> GridEpisodeInput:
    return GridEpisodeInput(
        SourceId("synthetic-grid-v1"),
        TradingSymbol(asset, "USDT"),
        START,
        (Decimal(100),) * 42,
        "synthetic-code-sha",
    )


def _geometry(engine: GridV1Engine) -> GridGeometry:
    return replace(
        engine.geometry,
        lower_boundary=Decimal("93.4278792"),
        quantity_per_rung=Q,
        modeled_boundary_loss=Decimal("100"),
        aggregate_pending_notional=Decimal("7999"),
    )


def _engine(
    scenario: GridScenario = GridScenario.BASE,
    asset: str = "BTC",
) -> GridV1Engine:
    episode = _episode(asset)
    seed = GridV1Engine(episode, scenario, healthy_grid_health(episode))
    return GridV1Engine(
        episode,
        scenario,
        healthy_grid_health(episode),
        synthetic_geometry=_geometry(seed),
    )


def _engine_with_levels(
    *,
    centre: str,
    buys: tuple[str, ...],
    sells: tuple[str, ...],
    lower: str,
    upper: str,
    quantity: str,
) -> GridV1Engine:
    episode = _episode()
    seed = GridV1Engine(episode, GridScenario.BASE, healthy_grid_health(episode))
    geometry = replace(
        seed.geometry,
        centre=Decimal(centre),
        buys=tuple(Decimal(value) for value in buys),
        sells=tuple(Decimal(value) for value in sells),
        lower_boundary=Decimal(lower),
        upper_boundary=Decimal(upper),
        quantity_per_rung=Decimal(quantity),
        modeled_boundary_loss=Decimal("100"),
        aggregate_pending_notional=Decimal("100"),
    )
    return GridV1Engine(
        episode,
        GridScenario.BASE,
        healthy_grid_health(episode),
        synthetic_geometry=geometry,
    )


def _minute(
    offset: int,
    *,
    open_: str = "100",
    high: str = "100",
    low: str = "100",
    close: str = "100",
    mark: str | None = None,
    funding: tuple[GridFundingEvent, ...] = (),
) -> GridMinute:
    start = START + timedelta(minutes=offset)
    return GridMinute(
        start,
        Decimal(open_),
        Decimal(high),
        Decimal(low),
        Decimal(close),
        start + timedelta(minutes=1) if mark is not None else None,
        None if mark is None else Decimal(mark),
        funding,
    )


def _events(engine: GridV1Engine, event_type: str) -> list[GridAuditEvent]:
    return [event for event in engine.audit if event.event_type == event_type]


def test_flat_six_rungs_are_booked_before_lower_boundary() -> None:
    engine = _engine()

    snapshot = engine.process_minute(
        _minute(1, open_="100", high="100", low="92", close="92", mark="92")
    )

    assert snapshot.state is GridState.STOPPED
    assert snapshot.position_quantity == 0
    assert len(_events(engine, "ORDER_FILLED")) == 6
    assert snapshot.cash - Decimal("10000") == Decimal("-80.509406271059975408640")
    assert _events(engine, "FORCED_CLOSE_FILLED")


@pytest.mark.parametrize(
    ("engine", "opening", "neutral", "closing"),
    (
        (
            lambda: _engine_with_levels(
                centre="101",
                buys=("100", "99", "98", "97", "96", "95"),
                sells=("102", "103", "104", "105", "106", "107"),
                lower="94",
                upper="108",
                quantity="0.01",
            ),
            _minute(1, open_="101", high="101", low="99", close="100", mark="100"),
            _minute(2, open_="100", high="100", low="100", close="100", mark="100"),
            _minute(3, open_="100", high="102", low="100", close="101", mark="101"),
        ),
        (
            lambda: _engine_with_levels(
                centre="100",
                buys=("99", "98", "97", "96", "95", "94"),
                sells=("101", "102", "103", "104", "105", "106"),
                lower="93",
                upper="107",
                quantity="0.01",
            ),
            _minute(1, open_="100", high="102", low="100", close="101", mark="101"),
            _minute(2, open_="101", high="101", low="101", close="101", mark="101"),
            _minute(3, open_="101", high="101", low="99", close="100", mark="100"),
        ),
    ),
)
def test_adjacent_rung_examples_use_shared_accounting(
    engine: Callable[[], GridV1Engine],
    opening: GridMinute,
    neutral: GridMinute,
    closing: GridMinute,
) -> None:
    state_machine = engine()
    state_machine.process_minute(opening)
    state_machine.process_minute(neutral)
    final = state_machine.process_minute(closing)
    assert final.position_quantity == 0
    assert final.cumulative_gross_realized_pnl == Decimal("0.01")
    assert final.cumulative_fees == Decimal("0.000402")
    assert final.cash - Decimal("10000") == Decimal("0.009598")


def test_existing_long_and_new_rungs_are_not_erased_by_boundary() -> None:
    engine = _engine()
    first = engine.process_minute(
        _minute(1, open_="100", high="100", low="97.5", close="98", mark="98")
    )
    assert first.position_quantity == Q * 2

    final = engine.process_minute(
        _minute(2, open_="98", high="98", low="92", close="92", mark="92")
    )

    assert final.position_quantity == 0
    assert len(_events(engine, "ORDER_FILLED")) == 6
    assert final.cash - Decimal("10000") == Decimal("-80.509406271059975408640")


def test_owned_exits_precede_short_entries_and_rearm_next_minute() -> None:
    engine = _engine()
    engine.process_minute(_minute(1, open_="100", high="100", low="97.5", close="98", mark="98"))
    engine.process_minute(_minute(2, open_="98", high="98", low="98", close="98", mark="98"))
    snapshot = engine.process_minute(
        _minute(3, open_="98", high="102.5", low="98", close="102", mark="102")
    )

    assert snapshot.position_quantity == -(Q * 2)
    fills = [event.order_id for event in _events(engine, "ORDER_FILLED")]
    assert fills[-4:] == [
        "EXIT:LOT:000002",
        "EXIT:LOT:000001",
        "ENTRY:SELL:1",
        "ENTRY:SELL:2",
    ]
    assert all(
        event.observed_at == START + timedelta(minutes=4)
        for event in _events(engine, "ORDER_REARMED")
    )


def test_marketable_post_only_activation_rejects_and_rearms() -> None:
    engine = _engine()
    snapshot = engine.process_minute(
        _minute(1, open_="98", high="98", low="98", close="98", mark="98")
    )

    rejected = {event.order_id for event in _events(engine, "POST_ONLY_REJECTED")}
    assert {"ENTRY:BUY:1", "ENTRY:BUY:2"} <= rejected
    assert snapshot.position_quantity == 0
    assert not _events(engine, "ORDER_FILLED")


def test_funding_at_open_precedes_gap_close_but_later_funding_sees_flat() -> None:
    at_open = _engine()
    at_open.process_minute(_minute(1, open_="100", high="100", low="98", close="98.5", mark="98.5"))
    open_event = GridFundingEvent(
        "open",
        START + timedelta(minutes=2),
        Decimal("0.001"),
        START + timedelta(minutes=2),
        Decimal("98.5"),
    )
    at_open.process_minute(
        _minute(2, open_="92", high="92", low="92", close="92", mark="92", funding=(open_event,))
    )
    types = [event.event_type for event in at_open.audit]
    assert types.index("FUNDING_APPLIED") < types.index("FORCED_CLOSE_FILLED")
    assert at_open.snapshot().cumulative_funding != 0

    later = _engine()
    later.process_minute(_minute(1, open_="100", high="100", low="98", close="98.5", mark="98.5"))
    later_event = replace(
        open_event,
        event_id="later",
        observed_at=open_event.observed_at + timedelta(milliseconds=20),
    )
    later.process_minute(
        _minute(2, open_="92", high="92", low="92", close="92", mark="92", funding=(later_event,))
    )
    assert later.snapshot().cumulative_funding == 0


def test_funding_base_and_stress_follow_frozen_formula() -> None:
    base = _engine(GridScenario.BASE)
    stress = _engine(GridScenario.STRESS)
    opening = _minute(1, open_="100", high="100", low="98", close="99", mark="100")
    base.process_minute(opening)
    stress.process_minute(opening)
    event = GridFundingEvent(
        "funding",
        START + timedelta(minutes=2),
        Decimal("0.0001"),
        START + timedelta(minutes=2),
        Decimal(100),
    )
    next_minute = _minute(2, mark="100", funding=(event,))
    base.process_minute(next_minute)
    stress.process_minute(next_minute)

    assert base.snapshot().cumulative_funding == -(Q * 2) * Decimal(100) * Decimal("0.0001")
    assert stress.snapshot().cumulative_funding == -(Q * 2) * Decimal(100) * Decimal("0.0002")


def test_stale_funding_authority_blocks_without_zero_filling() -> None:
    engine = _engine()
    event = GridFundingEvent(
        "stale",
        START + timedelta(minutes=1, seconds=30),
        Decimal("0.0001"),
        START - timedelta(minutes=2),
        Decimal(100),
    )
    result = engine.process_minute(_minute(1, mark="100", funding=(event,)))
    assert result.state is GridState.INTEGRITY_BLOCKED
    assert result.cumulative_funding == 0


def test_dual_boundary_selects_worst_equity_and_gap_can_exceed_budget() -> None:
    ambiguous = _engine()
    ambiguous.process_minute(_minute(1, open_="100", high="108", low="92", close="100", mark="100"))
    assert _events(ambiguous, "DUAL_BOUNDARY_WORST_EQUITY_SELECTED")
    assert ambiguous.snapshot().state is GridState.STOPPED

    gap = _engine()
    gap.process_minute(_minute(1, mark="100"))
    result = gap.process_minute(_minute(2, open_="1", high="1", low="1", close="1", mark="1"))
    assert Decimal("10000") - result.cash > Decimal("100")
    assert result.state is GridState.TERMINATED


def test_original_capital_floor_schedules_next_open_close() -> None:
    engine = _engine()
    pending = engine.process_minute(
        _minute(1, open_="100", high="100", low="93.5", close="94", mark="1")
    )
    assert pending.state is GridState.TERMINATION_PENDING

    final = engine.process_minute(
        _minute(2, open_="90", high="90", low="90", close="90", mark="90")
    )
    assert final.state is GridState.TERMINATED
    assert final.position_quantity == 0


def test_scheduled_flatten_path_is_explicit_and_terminal_for_week() -> None:
    engine = _engine()
    engine.process_minute(_minute(1, open_="100", high="100", low="98", close="98", mark="98"))
    scheduled = START + timedelta(days=6, hours=20, minutes=1)
    for offset in range(2, 6 * 24 * 60 + 20 * 60 + 1):
        engine.process_minute(
            _minute(offset, open_="98", high="98", low="98", close="98", mark="98")
        )
    snapshot = engine.process_minute(
        GridMinute(
            scheduled,
            Decimal(100),
            Decimal(100),
            Decimal(100),
            Decimal(100),
            scheduled + timedelta(minutes=1),
            Decimal(100),
        )
    )
    assert snapshot.state is GridState.STOPPED
    assert snapshot.position_quantity == 0
    assert _events(engine, "FORCED_CLOSE_FILLED")[-1].details == (("reason", "SCHEDULED_FLATTEN"),)


def test_idempotence_missing_authority_and_independent_ledgers() -> None:
    btc = _engine(asset="BTC")
    minute = _minute(1, mark="100")
    first = btc.process_minute(minute)
    audit_count = len(btc.audit)
    assert btc.process_minute(minute) == first
    assert len(btc.audit) == audit_count

    btc.process_minute(replace(minute, low=Decimal("99"), close=Decimal("99")))
    assert btc.snapshot().state is GridState.INTEGRITY_BLOCKED

    eth = _engine(asset="ETH")
    eth.process_minute(_minute(1, mark=None))
    assert eth.snapshot().state is GridState.INTEGRITY_BLOCKED
    assert btc.accounting is not eth.accounting


def test_safety_blocker_fails_closed() -> None:
    episode = _episode()
    blocked = replace(healthy_grid_health(episode), feed_integrity_state=FeedIntegrityState.CORRUPT)
    with pytest.raises(GridV1Error, match="Risk/Safety"):
        GridV1Engine(episode, GridScenario.BASE, blocked)


def test_synthetic_override_cannot_cross_into_non_synthetic_authority() -> None:
    synthetic = _engine()
    episode = replace(_episode(), source_id=SourceId("retained-real-source"))
    with pytest.raises(GridV1Error, match="synthetic source"):
        GridV1Engine(
            episode,
            GridScenario.BASE,
            healthy_grid_health(episode),
            synthetic_geometry=synthetic.geometry,
        )


def test_stress_costs_are_worse_and_report_round_trips_byte_stably() -> None:
    base = _engine(GridScenario.BASE)
    stress = _engine(GridScenario.STRESS)
    boundary = _minute(1, open_="100", high="100", low="92", close="92", mark="92")
    base.process_minute(boundary)
    stress.process_minute(boundary)
    assert stress.snapshot().cash < base.snapshot().cash

    report = run_synthetic_grid_episode(
        _episode("ETH"),
        GridScenario.BASE,
        healthy_grid_health(_episode("ETH")),
        (_minute(1, mark="100"),),
    )
    decoded = json.loads(report.to_json_bytes())
    assert decoded["protocol_version"] == GRID_PROTOCOL_VERSION
    assert decoded["engine_version"] == GRID_ENGINE_VERSION
    assert decoded["authority"]["directional_risk_status"] == {
        "LONG_ENVELOPE": "APPROVED",
        "SHORT_ENVELOPE": "APPROVED",
    }
    assert decoded["input_contract"]["processed_minute_count"] == 1
    assert report.sha256 == report.sha256


def test_accounting_equal_timestamp_sequence_is_opt_in() -> None:
    symbol = TradingSymbol("BTC", "USDT")
    first = PositionAccountingFill(
        "one", symbol, SimulatedExecutionSide.BUY, Decimal(1), START, Decimal(100), Decimal(0)
    )
    second = replace(first, fill_id="two", side=SimulatedExecutionSide.SELL)

    ordinary = PositionAccountingCore()
    ordinary.process(PositionAccountingFillBatch(START, (first,)))
    with pytest.raises(PositionAccountingCoreError, match="opt-in sequence"):
        ordinary.process(PositionAccountingFillBatch(START, (second,)))

    sequenced = PositionAccountingCore()
    sequenced.process(PositionAccountingFillBatch(START, (first,), 0))
    result = sequenced.process(PositionAccountingFillBatch(START, (second,), 1))
    assert result.sequence == 1
    assert sequenced.position(symbol).quantity == 0
