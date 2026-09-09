from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.replay import (
    PacedReplayController,
    ReplayBatch,
    ReplayEvent,
    ReplayPacingError,
    ReplaySession,
    ReplaySpeed,
    SystemReplayTimer,
    planned_wall_delay,
)
from adaptive_trading_bot.storage import SourceId

SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("archive-a")
START = datetime(2026, 9, 1, tzinfo=UTC)


def candle(open_time: datetime, *, symbol: TradingSymbol = SYMBOL) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.MINUTES_15,
        open_time=open_time,
        close_time=open_time + Timeframe.MINUTES_15.duration,
        open=Decimal("100.000000000000000001"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("101"),
        volume=Decimal("12.5"),
    )


def batch(observed_at: datetime, *events: ReplayEvent) -> ReplayBatch:
    if not events:
        events = (ReplayEvent(SOURCE, candle(observed_at - timedelta(minutes=15))),)
    return ReplayBatch(observed_at, events)


def session_for(batches: tuple[ReplayBatch, ...]) -> tuple[ReplaySession, FrozenClock]:
    clock = FrozenClock(START)
    return ReplaySession(batches, clock), clock


class FakeTimer:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.current += seconds

    def advance(self, seconds: float) -> None:
        self.current += seconds


class FailOnceTimer(FakeTimer):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def sleep(self, seconds: float) -> None:
        if not self.failed:
            self.failed = True
            raise OSError("simulated timer failure")
        super().sleep(seconds)


def standard_batches() -> tuple[ReplayBatch, ...]:
    return tuple(batch(START + timedelta(minutes=minutes)) for minutes in (15, 30, 45))


def test_supported_speeds_are_exact_and_unsupported_values_are_rejected() -> None:
    assert [(speed.label, speed.value) for speed in ReplaySpeed] == [
        ("1x", 1),
        ("10x", 10),
        ("50x", 50),
        ("100x", 100),
    ]
    assert ReplaySpeed.parse("10X") is ReplaySpeed.X10

    with pytest.raises(ValueError):
        ReplaySpeed(5)
    with pytest.raises(ValueError, match="one of"):
        ReplaySpeed.parse("5x")


@pytest.mark.parametrize(
    ("speed", "expected_delay"),
    [
        (ReplaySpeed.X1, 900.0),
        (ReplaySpeed.X10, 90.0),
        (ReplaySpeed.X50, 18.0),
        (ReplaySpeed.X100, 9.0),
    ],
)
def test_15_minute_planned_delays_for_every_speed(
    speed: ReplaySpeed,
    expected_delay: float,
) -> None:
    assert planned_wall_delay(START, START + timedelta(minutes=15), speed) == expected_delay


def test_speed_changes_only_delays_not_batch_sequence() -> None:
    expected = standard_batches()
    outputs: list[tuple[ReplayBatch, ...]] = []
    delays: list[list[float]] = []

    for speed in ReplaySpeed:
        replay_session, _ = session_for(expected)
        timer = FakeTimer()
        outputs.append(tuple(PacedReplayController(replay_session, speed, timer=timer).run()))
        delays.append(timer.sleeps)

    assert all(output == expected for output in outputs)
    assert delays == [[900.0, 900.0], [90.0, 90.0], [18.0, 18.0], [9.0, 9.0]]


def test_irregular_gaps_use_actual_observation_deltas() -> None:
    batches = tuple(batch(START + timedelta(minutes=minutes)) for minutes in (15, 60, 75))
    replay_session, _ = session_for(batches)
    timer = FakeTimer()

    assert (
        tuple(PacedReplayController(replay_session, ReplaySpeed.X10, timer=timer).run()) == batches
    )
    assert timer.sleeps == [270.0, 90.0]


def test_first_paced_batch_is_immediate_and_run_exhausts_in_order() -> None:
    batches = standard_batches()
    replay_session, _ = session_for(batches)
    timer = FakeTimer()
    controller = PacedReplayController(replay_session, ReplaySpeed.X100, timer=timer)
    iterator = controller.run()

    assert next(iterator) == batches[0]
    assert timer.sleeps == []
    assert tuple(iterator) == batches[1:]
    assert timer.sleeps == [9.0, 9.0]
    assert controller.exhausted is True


def test_processing_lag_clamps_next_sleep_to_zero_without_drift_or_drop() -> None:
    batches = standard_batches()
    replay_session, _ = session_for(batches)
    timer = FakeTimer()
    controller = PacedReplayController(replay_session, ReplaySpeed.X100, timer=timer)
    iterator = controller.run()
    assert next(iterator) == batches[0]
    timer.advance(10.0)

    assert next(iterator) == batches[1]
    assert timer.sleeps == [0.0]
    assert next(iterator) == batches[2]
    assert timer.sleeps == [0.0, 8.0]


def test_timer_failure_keeps_batch_pending_for_deterministic_retry() -> None:
    batches = standard_batches()[:2]
    replay_session, clock = session_for(batches)
    timer = FailOnceTimer()
    controller = PacedReplayController(replay_session, ReplaySpeed.X100, timer=timer)
    assert controller.paced_step() == batches[0]

    with pytest.raises(ReplayPacingError, match="batch remains pending") as captured:
        controller.paced_step()

    assert isinstance(captured.value.__cause__, OSError)
    assert clock.now() == batches[1].observed_at
    assert controller.exhausted is False
    assert controller.paced_step() == batches[1]
    assert controller.exhausted is True
    assert timer.sleeps == [9.0]


def test_manual_step_is_immediate_and_advances_exactly_one_batch() -> None:
    batches = standard_batches()
    replay_session, clock = session_for(batches)
    timer = FakeTimer()
    controller = PacedReplayController(replay_session, ReplaySpeed.X1, timer=timer)

    delivered = controller.step()

    assert delivered == batches[0]
    assert clock.now() == batches[0].observed_at
    assert timer.sleeps == []
    assert controller.exhausted is False


def test_replay_clock_matches_each_delivered_paced_batch() -> None:
    batches = standard_batches()
    replay_session, clock = session_for(batches)
    controller = PacedReplayController(replay_session, ReplaySpeed.X100, timer=FakeTimer())

    for delivered in controller.run():
        assert clock.now() == delivered.observed_at


def test_equal_close_atomic_batch_is_unchanged_by_controller() -> None:
    btc = candle(START)
    eth = candle(START, symbol=TradingSymbol("ETH", "USDT"))
    atomic = batch(
        btc.close_time,
        ReplayEvent(SOURCE, btc),
        ReplayEvent(SOURCE, eth),
    )
    replay_session, _ = session_for((atomic,))

    delivered = PacedReplayController(
        replay_session,
        ReplaySpeed.X50,
        timer=FakeTimer(),
    ).paced_step()

    assert delivered == atomic
    assert delivered is not None
    assert delivered.events == atomic.events


def test_planned_delay_rejects_non_increasing_observations() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        planned_wall_delay(START, START, ReplaySpeed.X1)


def test_system_timer_rejects_negative_sleep_without_sleeping() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        SystemReplayTimer().sleep(-0.001)
