import runpy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, getcontext, localcontext, setcontext
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq
import pytest

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
from adaptive_trading_bot.orders import EntryOrderIntent, EntryOrderIntentAuthorizer
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mBar,
    Last5mExecutionBinding,
    Last5mExecutionStatus,
    Last5mParquetSource,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionError,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    TriggerBasis,
    TriggerKind,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.research.research_v2_corpus import Row, _table
from adaptive_trading_bot.risk import OperationalSafetyGate, RiskManager, RiskPortfolioSnapshot
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py"))
NOW: datetime = HELPERS["NOW"]
SOURCE = SourceId("archive")
BTC = TradingSymbol("BTC", "USDT")
_entry = HELPERS["_entry"]
_health = HELPERS["_health"]


def intent(*, short: bool = False, strategy_id: str = "manual") -> EntryOrderIntent:
    entry = _entry(strategy_id=strategy_id)
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
    assert result.intent is not None
    return result.intent


def bar(
    index: int,
    *,
    open_price: str = "100",
    high: str = "110",
    low: str = "90",
    close: str = "100",
    source: SourceId = SOURCE,
    epoch: str = "epoch-a",
) -> Last5mBar:
    opened = NOW + index * timedelta(minutes=5)
    return Last5mBar(
        source,
        BTC,
        epoch,
        opened,
        opened + timedelta(minutes=5),
        Decimal(open_price),
        Decimal(high),
        Decimal(low),
        Decimal(close),
        Decimal(1),
    )


def binding(count: int = 3) -> Last5mExecutionBinding:
    return Last5mExecutionBinding(SOURCE, BTC, "epoch-a", NOW, NOW + count * timedelta(minutes=5))


def run(
    bars: tuple[Last5mBar, ...],
    *,
    order_intent: EntryOrderIntent | None = None,
    latency: timedelta = timedelta(0),
    activation: timedelta = timedelta(0),
    acknowledgement: ProtectionAcknowledgement = ProtectionAcknowledgement.ACCEPTED,
    spread: str = "0",
    slippage: str = "0",
    fee: str = "0",
) -> Last5mProtectiveExecutionResult:
    return run_last_5m_protective_execution(
        intent=order_intent or intent(),
        binding=binding(len(bars)),
        bars=bars,
        configuration=Last5mProtectionConfiguration(
            SimulatedExecutionCostConfiguration(
                spread_bps=Decimal(spread),
                slippage_bps=Decimal(slippage),
                fee_bps=Decimal(fee),
                latency=latency,
            ),
            ExitPolicy.FIXED_2R,
            acknowledgement=acknowledgement,
            activation_latency=activation,
        ),
    )


@pytest.mark.parametrize(
    ("latency", "expected_fill", "expected_wait"),
    [
        (timedelta(0), NOW, timedelta(0)),
        (timedelta(seconds=1), NOW + timedelta(minutes=5), timedelta(seconds=299)),
    ],
)
def test_entry_uses_first_5m_open_and_records_grid_wait(
    latency: timedelta, expected_fill: datetime, expected_wait: timedelta
) -> None:
    result = run((bar(0), bar(1), bar(2)), latency=latency)

    assert result.entry_fill is not None
    assert result.entry_fill.fill_at == expected_fill
    assert result.entry_grid_wait == expected_wait
    assert result.authorization_at == NOW
    assert result.entry_eligible_at == NOW + latency


def test_entry_after_finite_range_is_explicitly_unfilled() -> None:
    result = run((bar(0), bar(1)), latency=timedelta(minutes=11))

    assert result.status is Last5mExecutionStatus.ENTRY_UNFILLED
    assert result.entry_fill is None
    assert result.accounting.quantity == 0
    assert result.events == ("ENTRY_ELIGIBLE_NO_LATER_5M_OPEN",)


def test_legacy_15m_simulator_keeps_899_second_wait_and_timeframes() -> None:
    candle = Candle(
        BTC,
        Timeframe.MINUTES_15,
        NOW + timedelta(minutes=15),
        NOW + timedelta(minutes=30),
        Decimal(101),
        Decimal(102),
        Decimal(100),
        Decimal(101),
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

    result = BacktestExecutionSimulator(Source()).run(
        ExecutionSeries(SOURCE, BTC, Timeframe.MINUTES_15),
        ExecutionCostConfiguration(latency=timedelta(seconds=1)),
        (MarketOrderRequest("legacy", NOW, BTC, OrderSide.BUY, Decimal(1)),),
        start=NOW,
        end=NOW + timedelta(minutes=30),
    )

    assert result.fills[0].fill_at - result.fills[0].eligible_at == timedelta(seconds=899)
    assert tuple(Timeframe) == (
        Timeframe.MINUTES_15,
        Timeframe.HOUR_1,
        Timeframe.HOURS_4,
    )


@pytest.mark.parametrize("short", [False, True])
def test_long_and_short_target_paths_use_shared_accounting(short: bool) -> None:
    bars = (
        bar(0),
        bar(1, open_price="100", high="160", low="90", close="150")
        if not short
        else bar(1, open_price="100", high="110", low="20", close="30"),
    )
    result = run(bars, order_intent=intent(short=short), fee="10")

    assert result.status is Last5mExecutionStatus.ORDINARY_CLOSED
    assert result.trigger is not None
    assert result.trigger.kind is TriggerKind.TARGET_INTRABAR
    assert result.position_closed
    assert result.accounting.quantity == 0
    assert result.total_fees > 0
    assert result.realized_gross_pnl > 0
    assert result.realized_net_pnl == result.accounting.cumulative_net_realized_pnl
    assert result.closed_net_approved_risk_r is not None and result.closed_net_approved_risk_r > 0


def test_dual_touch_is_stop_first_with_alternate_fact() -> None:
    result = run(
        (
            bar(0),
            bar(1, open_price="100", high="160", low="70", close="100"),
        )
    )

    assert result.trigger is not None
    assert result.trigger.kind is TriggerKind.DUAL_TOUCH_STOP_FIRST
    assert result.trigger.same_bar_stop_precedence
    assert result.trigger.alternate_trigger is TriggerKind.TARGET_INTRABAR
    assert result.exit_fill is not None and result.exit_fill.reference_price == Decimal(75)
    assert result.realized_gross_pnl < 0
    assert result.holding_duration_lower == timedelta(minutes=5)
    assert result.holding_duration_upper == timedelta(minutes=10)
    assert not result.holding_time_exact


def test_open_gap_through_stop_uses_open_and_adverse_costs() -> None:
    authorized = intent()
    result = run(
        (
            bar(0),
            bar(1, open_price="70", high="80", low="60", close="70"),
        ),
        spread="10",
        slippage="20",
        order_intent=authorized,
    )

    assert result.trigger is not None
    assert result.trigger.kind is TriggerKind.STOP_OPEN_GAP
    assert result.trigger.gap_through_stop
    assert result.exit_fill is not None
    assert result.entry_fill is not None
    assert result.entry_fill.fill_price == Decimal("100.25")
    assert result.exit_fill.reference_price == Decimal(70)
    assert result.exit_fill.fill_price == Decimal("69.8250")
    assert result.holding_time_exact
    assert result.holding_duration_lower == result.holding_duration_upper == timedelta(minutes=5)
    with localcontext(Context(prec=34)):
        expected_actual_risk = (
            result.entry_fill.fill_price - authorized.stop_basis
        ) * authorized.quantity
        expected_closed_r = result.realized_net_pnl / authorized.approved_risk_quote
    assert result.approved_risk_quote == authorized.approved_risk_quote
    assert result.actual_fill_risk_quote == expected_actual_risk
    assert result.actual_fill_risk_quote != result.approved_risk_quote
    assert result.closed_net_approved_risk_r == expected_closed_r


def test_stop_equality_at_open_is_not_mislabeled_as_gap() -> None:
    result = run(
        (
            bar(0),
            bar(1, open_price="75", high="80", low="70", close="75"),
        )
    )

    assert result.trigger is not None
    assert result.trigger.kind is TriggerKind.STOP_OPEN
    assert not result.trigger.gap_through_stop
    assert result.exit_fill is not None and result.exit_fill.reference_price == Decimal(75)


def test_activation_inside_touched_bar_is_unresolved_and_ignores_pre_activation_extremes() -> None:
    ambiguous = run(
        (bar(0, high="160", low="90"), bar(1)),
        activation=timedelta(minutes=1),
    )
    no_touch = run(
        (
            bar(0, high="110", low="90"),
            bar(1, open_price="151", high="155", low="149", close="152"),
        ),
        activation=timedelta(minutes=1),
    )

    assert ambiguous.status is Last5mExecutionStatus.ACTIVATION_ORDER_AMBIGUOUS_OPEN
    assert ambiguous.trigger is not None and ambiguous.trigger.activated_inside_interval
    assert ambiguous.exit_fill is None and ambiguous.unresolved_quantity > 0
    assert no_touch.status is Last5mExecutionStatus.ORDINARY_CLOSED
    assert no_touch.trigger is not None and no_touch.trigger.kind is TriggerKind.TARGET_OPEN


def test_activation_exactly_on_next_open_uses_that_known_open_first() -> None:
    result = run(
        (
            bar(0),
            bar(1, open_price="151", high="155", low="149", close="152"),
        ),
        activation=timedelta(minutes=5),
    )

    assert result.protection_active_at == NOW + timedelta(minutes=5)
    assert result.trigger is not None and result.trigger.kind is TriggerKind.TARGET_OPEN
    assert result.exit_fill is not None and result.exit_fill.fill_at == NOW + timedelta(minutes=5)


@pytest.mark.parametrize(
    ("ack", "status"),
    [
        (
            ProtectionAcknowledgement.REJECTED,
            Last5mExecutionStatus.PROTECTION_REJECTED_OPEN,
        ),
        (
            ProtectionAcknowledgement.UNACKNOWLEDGED,
            Last5mExecutionStatus.PROTECTION_UNACKNOWLEDGED_OPEN,
        ),
    ],
)
def test_inactive_protection_retains_filled_exposure(
    ack: ProtectionAcknowledgement, status: Last5mExecutionStatus
) -> None:
    result = run((bar(0), bar(1)), acknowledgement=ack)

    assert result.status is status
    assert result.entry_fill is not None and result.exit_fill is None
    assert result.total_fees == 0
    assert result.unresolved_quantity == result.entry_fill.quantity


def test_open_position_reports_fee_burden_but_no_closed_economic_r() -> None:
    authorized = intent()
    result = run(
        (bar(0), bar(1)),
        order_intent=authorized,
        acknowledgement=ProtectionAcknowledgement.REJECTED,
        fee="10",
    )

    with localcontext(Context(prec=34)):
        expected_fee_burden = result.total_fees / authorized.approved_risk_quote
    assert result.status is Last5mExecutionStatus.PROTECTION_REJECTED_OPEN
    assert result.total_fees > 0
    assert result.realized_net_pnl == result.total_fees.copy_negate()
    assert result.closed_net_pnl_quote is None
    assert result.closed_net_approved_risk_r is None
    assert result.open_realized_fees_quote == result.total_fees
    assert result.open_fee_burden_approved_risk_r == expected_fee_burden


def test_invalid_geometry_is_accounted_then_recovered_on_later_open() -> None:
    authorized = intent()
    result = run(
        (
            bar(0, open_price="70", high="80", low="60", close="70"),
            bar(1, open_price="68", high="70", low="65", close="68"),
        ),
        order_intent=authorized,
        fee="10",
    )

    assert result.status is Last5mExecutionStatus.INVALID_GEOMETRY_RECOVERED
    assert result.trigger is not None
    assert result.trigger.kind is TriggerKind.INVALID_GEOMETRY_RECOVERY
    assert result.position_closed and result.accounting.quantity == 0
    assert result.entry_fill is not None and result.exit_fill is not None
    assert result.entry_fill.fill_at < result.exit_fill.fill_at
    with localcontext(Context(prec=34)):
        expected_r = result.realized_net_pnl / authorized.approved_risk_quote
    assert result.risk_distance is None
    assert result.actual_fill_risk_quote is None
    assert result.closed_net_pnl_quote == result.realized_net_pnl
    assert result.closed_net_approved_risk_r == expected_r
    assert result.open_realized_fees_quote is None


def test_invalid_geometry_without_later_open_remains_exposed() -> None:
    result = run((bar(0, open_price="70", high="80", low="60", close="70"),))

    assert result.status is Last5mExecutionStatus.INVALID_GEOMETRY_UNRESOLVED
    assert result.unresolved_quantity > 0
    assert not result.position_closed


def test_mark_is_rejected_before_execution() -> None:
    with pytest.raises(Last5mProtectiveExecutionError, match="rejects non-LAST"):
        Last5mProtectionConfiguration(
            SimulatedExecutionCostConfiguration(),
            ExitPolicy.FIXED_2R,
            trigger_basis=TriggerBasis.MARK,
        )


def test_intent_and_execution_binding_source_must_match() -> None:
    foreign = Last5mExecutionBinding(
        SourceId("foreign"), BTC, "epoch-a", NOW, NOW + timedelta(minutes=5)
    )
    with pytest.raises(Last5mProtectiveExecutionError, match="source/symbol"):
        run_last_5m_protective_execution(
            intent=intent(),
            binding=foreign,
            bars=(bar(0, source=SourceId("foreign")),),
            configuration=Last5mProtectionConfiguration(
                SimulatedExecutionCostConfiguration(), ExitPolicy.FIXED_2R
            ),
        )


@pytest.mark.parametrize(
    "bars",
    [
        (bar(0), bar(0)),
        (bar(1), bar(0)),
        (bar(0), bar(2)),
        (bar(0), bar(1, epoch="epoch-b")),
    ],
)
def test_missing_duplicate_out_of_order_and_cross_epoch_series_fail(
    bars: tuple[Last5mBar, ...],
) -> None:
    with pytest.raises(Last5mProtectiveExecutionError):
        run_last_5m_protective_execution(
            intent=intent(),
            binding=binding(2),
            bars=bars,
            configuration=Last5mProtectionConfiguration(
                SimulatedExecutionCostConfiguration(), ExitPolicy.FIXED_2R
            ),
        )


def test_terminal_result_does_not_read_future_bars() -> None:
    left = run((bar(0), bar(1, open_price="70", high="80", low="60", close="70"), bar(2)))
    right = run(
        (
            bar(0),
            bar(1, open_price="70", high="80", low="60", close="70"),
            bar(2, open_price="1000", high="1100", low="900", close="1000"),
        )
    )

    assert left.status == right.status
    assert left.trigger == right.trigger
    assert left.exit_fill == right.exit_fill
    assert left.accounting == right.accounting


def test_arithmetic_is_decimal_context_independent() -> None:
    before = getcontext().copy()
    try:
        setcontext(Context(prec=6))
        low_precision = run((bar(0), bar(1, high="160", low="90", close="150")), fee="12.34")
        setcontext(Context(prec=48))
        high_precision = run((bar(0), bar(1, high="160", low="90", close="150")), fee="12.34")
    finally:
        setcontext(before)

    assert low_precision.realized_net_pnl == high_precision.realized_net_pnl
    assert low_precision.closed_net_approved_risk_r == high_precision.closed_net_approved_risk_r


@pytest.mark.parametrize("scenario", ["ordinary", "recovery", "unresolved"])
def test_compact_audit_survives_ordinary_recovery_and_unresolved_results(
    scenario: str,
) -> None:
    if scenario == "ordinary":
        result = run((bar(0), bar(1, high="160", low="90", close="150")), fee="10")
    elif scenario == "recovery":
        result = run(
            (
                bar(0, open_price="70", high="80", low="60", close="70"),
                bar(1, open_price="68", high="70", low="65", close="68"),
            ),
            fee="10",
        )
    else:
        result = run(
            (bar(0), bar(1)),
            acknowledgement=ProtectionAcknowledgement.UNACKNOWLEDGED,
            fee="10",
        )
    audit = result.intent_audit
    assert audit.decision_identity == result.decision_identity
    assert audit.approved_risk_quote == result.approved_risk_quote
    assert audit.original_stop_basis == result.original_stop_basis
    assert audit.quantity > 0
    assert result.configuration.costs.fee_bps == Decimal(10)
    assert result.configuration.costs.latency == timedelta(0)
    assert result.configuration.activation_latency == timedelta(0)
    assert result.configuration.trigger_basis is TriggerBasis.LAST
    assert isinstance(result.configuration.acknowledgement, ProtectionAcknowledgement)
    assert result.exit_policy is ExitPolicy.FIXED_2R
    assert not hasattr(result, "bars")
    assert not hasattr(audit, "context")


def test_fill_identifiers_are_bound_to_distinct_decisions() -> None:
    first = run(
        (bar(0), bar(1, high="160", low="90", close="150")),
        order_intent=intent(strategy_id="manual-one"),
    )
    second = run(
        (bar(0), bar(1, high="160", low="90", close="150")),
        order_intent=intent(strategy_id="manual-two"),
    )

    assert first.entry_fill is not None and second.entry_fill is not None
    assert first.exit_fill is not None and second.exit_fill is not None
    assert first.entry_fill.fill_id != second.entry_fill.fill_id
    assert first.exit_fill.fill_id != second.exit_fill.fill_id
    assert first.entry_fill.fill_id.endswith(":entry")
    assert first.exit_fill.fill_id.endswith(":protective-exit")


def corpus_row(item: Last5mBar) -> Row:
    return Row(
        int(item.open_time.timestamp() * 1000),
        item.open,
        item.high,
        item.low,
        item.close,
        item.volume,
        int(item.close_time.timestamp() * 1000),
        Decimal(100),
        1,
        Decimal("0.5"),
        Decimal(50),
    )


def write_canonical(
    path: Path,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
) -> str:
    pq.write_table(_table(symbol, timeframe, [corpus_row(bar(0)), corpus_row(bar(1))]), path)
    return sha256(path.read_bytes()).hexdigest()


def test_locked_canonical_corpus_parquet_round_trip_to_execution(tmp_path: Path) -> None:
    path = tmp_path / "canonical-5m.parquet"
    digest = write_canonical(path)

    loaded = Last5mParquetSource(path, expected_sha256=digest).read(binding(2))
    result = run_last_5m_protective_execution(
        intent=intent(),
        binding=binding(2),
        bars=loaded,
        configuration=Last5mProtectionConfiguration(
            SimulatedExecutionCostConfiguration(), ExitPolicy.FIXED_2R
        ),
    )

    assert loaded == (bar(0), bar(1))
    assert result.entry_fill is not None
    assert result.binding == binding(2)


@pytest.mark.parametrize(
    ("symbol", "timeframe", "message"),
    [
        ("ETHUSDT", "5m", "foreign symbol"),
        ("BTCUSDT", "15m", "foreign timeframe"),
    ],
)
def test_canonical_parquet_rejects_foreign_row_identity(
    tmp_path: Path, symbol: str, timeframe: str, message: str
) -> None:
    path = tmp_path / f"foreign-{symbol}-{timeframe}.parquet"
    digest = write_canonical(path, symbol=symbol, timeframe=timeframe)

    with pytest.raises(Last5mProtectiveExecutionError, match=message):
        Last5mParquetSource(path, expected_sha256=digest).read(binding(2))


def test_canonical_parquet_rejects_malformed_schema_and_hash(tmp_path: Path) -> None:
    valid_path = tmp_path / "canonical.parquet"
    digest = write_canonical(valid_path)
    malformed_path = tmp_path / "malformed.parquet"
    canonical = pq.read_table(valid_path)
    pq.write_table(canonical.drop(["quote_volume"]), malformed_path)
    malformed_digest = sha256(malformed_path.read_bytes()).hexdigest()

    with pytest.raises(Last5mProtectiveExecutionError, match="schema"):
        Last5mParquetSource(malformed_path, expected_sha256=malformed_digest).read(binding(2))
    with pytest.raises(Last5mProtectiveExecutionError, match="hash"):
        Last5mParquetSource(valid_path, expected_sha256="0" * 64).read(binding(2))
    assert len(digest) == 64
