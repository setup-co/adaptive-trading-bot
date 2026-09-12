import ast
import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.experience.aggregation as aggregation_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.experience import (
    ExperienceAggregate,
    ExperienceAggregationError,
    ExperienceAggregator,
    ExperienceFallbackLevel,
    ExperienceMetricAvailability,
    ExperienceMetricUnavailableReason,
    ExperienceProjectionError,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
)
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.regime import Direction
from adaptive_trading_bot.storage import SourceId

_SAMPLE_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_sample_projection.py")))
_project_one = cast(
    Callable[..., tuple[object, object, ExperienceTradeSample]],
    _SAMPLE_HELPERS["_project_one"],
)
AGGREGATOR = ExperienceAggregator()
START = datetime(2026, 9, 11, tzinfo=UTC)


@pytest.fixture(scope="module")
def base_sample() -> ExperienceTradeSample:
    return _project_one()[2]


def _sample(
    base: ExperienceTradeSample,
    index: int,
    *,
    net: Decimal = Decimal(1),
    gross: Decimal | None = None,
    fees: Decimal = Decimal(0),
    realized_r: Decimal = Decimal(1),
    outcome: str = "PROFIT",
    decision_at: datetime | None = None,
    exit_at: datetime | None = None,
    duration: timedelta = timedelta(microseconds=10),
    source_id: SourceId | None = None,
    symbol: TradingSymbol | None = None,
    strategy_version: str | None = None,
    direction: Direction | None = None,
) -> ExperienceTradeSample:
    current_exit = exit_at or START + timedelta(days=1, minutes=index)
    entry_at = current_exit - duration
    current_decision = decision_at or entry_at - timedelta(microseconds=index + 1)
    current_source = source_id or SourceId(f"sample-{index}")
    current_symbol = symbol or base.fingerprint.symbol
    current_version = strategy_version or base.fingerprint.strategy_version
    fingerprint = replace(
        base.fingerprint,
        symbol=current_symbol,
        strategy_version=current_version,
        direction=direction or base.fingerprint.direction,
    )
    keys = tuple(
        replace(
            key,
            strategy_version=current_version,
            symbol=current_symbol if key.symbol is not None else None,
            direction=(direction or key.direction)
            if key.level is ExperienceFallbackLevel.FULL_CONTEXT
            else key.direction,
        )
        for key in base.hierarchical_keys
    )
    identity = DecisionIdentity(
        current_source,
        current_symbol,
        current_decision,
        base.decision_identity.strategy_id,
        current_version,
    )
    return replace(
        base,
        decision_identity=identity,
        source_id=current_source,
        fingerprint=fingerprint,
        hierarchical_keys=keys,
        entry_decision_at=current_decision,
        entry_fill_at=entry_at,
        exit_fill_at=current_exit,
        gross_realized_pnl=net + fees if gross is None else gross,
        total_trade_fees_quote=fees,
        net_realized_pnl=net,
        realized_r_net=realized_r,
        outcome_classification=outcome,
        duration=duration,
        accounting_entry_fill_id=f"entry-{index}",
        accounting_exit_fill_id=f"exit-{index}",
    )


def _snapshot(*samples: ExperienceTradeSample) -> ExperienceTradeSampleSnapshot:
    return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda item: item.sort_key)))


def _aggregate_at(
    snapshot: ExperienceTradeSampleSnapshot, level: ExperienceFallbackLevel
) -> ExperienceAggregate:
    matches = [
        item for item in AGGREGATOR.aggregate(snapshot).aggregates if item.key.level is level
    ]
    assert len(matches) == 1
    return matches[0]


def test_empty_snapshot_returns_empty_versioned_aggregate_snapshot() -> None:
    result = AGGREGATOR.aggregate(ExperienceTradeSampleSnapshot(()))

    assert result.aggregates == ()
    assert result.total_sample_count == 0
    assert result.aggregate_snapshot_schema_version == "1.0.0"


def test_one_sample_contributes_once_to_exactly_five_levels(
    base_sample: ExperienceTradeSample,
) -> None:
    result = AGGREGATOR.aggregate(_snapshot(_sample(base_sample, 1)))

    assert result.total_sample_count == 1
    assert len(result.aggregates) == 5
    assert tuple(item.key.level for item in result.aggregates) == tuple(ExperienceFallbackLevel)
    assert all(item.sample_count == 1 for item in result.aggregates)
    assert all(item.aggregate_schema_version == "1.0.0" for item in result.aggregates)


def test_counts_rates_exact_sums_profit_factor_expectancy_and_odd_medians(
    base_sample: ExperienceTradeSample,
) -> None:
    samples = _snapshot(
        _sample(
            base_sample,
            1,
            net=Decimal(10),
            gross=Decimal(12),
            fees=Decimal(2),
            realized_r=Decimal(1),
            outcome="PROFIT",
            duration=timedelta(microseconds=10),
        ),
        _sample(
            base_sample,
            2,
            net=Decimal(-4),
            gross=Decimal(-3),
            fees=Decimal(1),
            realized_r=Decimal("-0.5"),
            outcome="LOSS",
            duration=timedelta(microseconds=20),
        ),
        _sample(
            base_sample,
            3,
            net=Decimal(0),
            gross=Decimal(1),
            fees=Decimal(1),
            realized_r=Decimal(0),
            outcome="BREAKEVEN",
            duration=timedelta(microseconds=30),
        ),
    )
    aggregate = _aggregate_at(samples, ExperienceFallbackLevel.STRATEGY)

    assert (aggregate.sample_count, aggregate.profit_count, aggregate.loss_count) == (3, 1, 1)
    assert aggregate.breakeven_count == 1
    assert aggregate.win_rate + aggregate.loss_rate + aggregate.breakeven_rate == 1
    rate_context = Context(prec=34, rounding=ROUND_HALF_EVEN)
    expected_third = rate_context.divide(Decimal(1), Decimal(3))
    expected_breakeven = rate_context.subtract(
        rate_context.subtract(Decimal(1), expected_third), expected_third
    )
    assert aggregate.win_rate == expected_third
    assert aggregate.loss_rate == expected_third
    assert aggregate.breakeven_rate == expected_breakeven
    assert aggregate.gross_realized_pnl_sum_quote == 10
    assert aggregate.total_fees_sum_quote == 4
    assert aggregate.net_realized_pnl_sum_quote == 6
    assert aggregate.positive_net_pnl_sum_quote == 10
    assert aggregate.negative_net_pnl_abs_sum_quote == 4
    assert aggregate.profit_factor_net.availability is ExperienceMetricAvailability.AVAILABLE
    assert aggregate.profit_factor_net.value == Decimal("2.5")
    assert aggregate.realized_r_sum == Decimal("0.5")
    assert aggregate.mean_realized_r_net == rate_context.divide(Decimal("0.5"), Decimal(3))
    assert aggregate.median_realized_r_net == 0
    assert aggregate.expectancy_net_quote == 2
    assert aggregate.expectancy_r == aggregate.mean_realized_r_net
    assert aggregate.mean_duration == timedelta(microseconds=20)
    assert aggregate.median_duration == timedelta(microseconds=20)


def test_even_r_and_duration_medians_are_exact(base_sample: ExperienceTradeSample) -> None:
    aggregate = _aggregate_at(
        _snapshot(
            _sample(
                base_sample,
                1,
                realized_r=Decimal(1),
                duration=timedelta(microseconds=10),
            ),
            _sample(
                base_sample,
                2,
                realized_r=Decimal(2),
                duration=timedelta(microseconds=20),
            ),
        ),
        ExperienceFallbackLevel.STRATEGY,
    )

    assert aggregate.mean_realized_r_net == Decimal("1.5")
    assert aggregate.median_realized_r_net == Decimal("1.5")
    assert aggregate.mean_duration == timedelta(microseconds=15)
    assert aggregate.median_duration == timedelta(microseconds=15)


def test_unrepresentable_submicrosecond_duration_fails_closed(
    base_sample: ExperienceTradeSample,
) -> None:
    snapshot = _snapshot(
        _sample(base_sample, 1, duration=timedelta(microseconds=1)),
        _sample(base_sample, 2, duration=timedelta(microseconds=2)),
    )

    with pytest.raises(ExperienceAggregationError, match="sub-microsecond"):
        AGGREGATOR.aggregate(snapshot)


def test_profit_factor_unavailable_without_losses_and_zero_for_all_losses(
    base_sample: ExperienceTradeSample,
) -> None:
    winning = _aggregate_at(
        _snapshot(_sample(base_sample, 1, net=Decimal(3))),
        ExperienceFallbackLevel.STRATEGY,
    )
    losing = _aggregate_at(
        _snapshot(
            _sample(base_sample, 2, net=Decimal(-3), outcome="LOSS"),
            _sample(base_sample, 3, net=Decimal(-2), outcome="LOSS"),
        ),
        ExperienceFallbackLevel.STRATEGY,
    )

    assert winning.profit_factor_net.availability is ExperienceMetricAvailability.UNAVAILABLE
    assert winning.profit_factor_net.value is None
    assert (
        winning.profit_factor_net.unavailable_reason
        is ExperienceMetricUnavailableReason.NO_LOSS_DENOMINATOR
    )
    assert losing.profit_factor_net.availability is ExperienceMetricAvailability.AVAILABLE
    assert losing.profit_factor_net.value == 0


def test_exit_chronology_controls_streak_and_breakeven_breaks_it(
    base_sample: ExperienceTradeSample,
) -> None:
    loss_first_entry = _sample(
        base_sample,
        1,
        net=Decimal(-1),
        outcome="LOSS",
        decision_at=START,
        exit_at=START + timedelta(hours=1),
        duration=timedelta(minutes=1),
    )
    loss_second_entry = _sample(
        base_sample,
        2,
        net=Decimal(-1),
        outcome="LOSS",
        decision_at=START + timedelta(minutes=1),
        exit_at=START + timedelta(hours=3),
        duration=timedelta(minutes=1),
    )
    breakeven_last_entry = _sample(
        base_sample,
        3,
        net=Decimal(0),
        outcome="BREAKEVEN",
        decision_at=START + timedelta(minutes=2),
        exit_at=START + timedelta(hours=2),
        duration=timedelta(minutes=1),
    )

    aggregate = _aggregate_at(
        _snapshot(loss_first_entry, loss_second_entry, breakeven_last_entry),
        ExperienceFallbackLevel.STRATEGY,
    )
    assert aggregate.max_consecutive_losses == 1


def test_realized_path_drawdowns_use_exit_order_and_initial_zero(
    base_sample: ExperienceTradeSample,
) -> None:
    values = (
        (Decimal(10), Decimal(1), "PROFIT"),
        (Decimal(-4), Decimal("-0.5"), "LOSS"),
        (Decimal(-8), Decimal(-1), "LOSS"),
        (Decimal(3), Decimal("0.25"), "PROFIT"),
    )
    snapshot = _snapshot(
        *(
            _sample(
                base_sample,
                index,
                net=net,
                realized_r=realized_r,
                outcome=outcome,
                exit_at=START + timedelta(hours=index),
            )
            for index, (net, realized_r, outcome) in enumerate(values, start=1)
        )
    )
    aggregate = _aggregate_at(snapshot, ExperienceFallbackLevel.STRATEGY)

    assert aggregate.max_cumulative_net_pnl_drawdown_quote == 12
    assert aggregate.max_cumulative_realized_r_drawdown == Decimal("1.5")
    assert aggregate.max_consecutive_losses == 2


def test_drawdown_uses_exit_chronology_instead_of_decision_chronology(
    base_sample: ExperienceTradeSample,
) -> None:
    decision_ordered = (
        _sample(
            base_sample,
            1,
            net=Decimal(-10),
            outcome="LOSS",
            decision_at=START,
            exit_at=START + timedelta(hours=3),
            duration=timedelta(minutes=1),
        ),
        _sample(
            base_sample,
            2,
            net=Decimal(20),
            decision_at=START + timedelta(minutes=1),
            exit_at=START + timedelta(hours=1),
            duration=timedelta(minutes=1),
        ),
        _sample(
            base_sample,
            3,
            net=Decimal(-15),
            outcome="LOSS",
            decision_at=START + timedelta(minutes=2),
            exit_at=START + timedelta(hours=2),
            duration=timedelta(minutes=1),
        ),
    )

    aggregate = _aggregate_at(_snapshot(*decision_ordered), ExperienceFallbackLevel.STRATEGY)
    assert aggregate.max_cumulative_net_pnl_drawdown_quote == 25
    assert aggregate.max_consecutive_losses == 2


def test_winning_path_has_zero_drawdown_and_first_loss_draws_from_zero(
    base_sample: ExperienceTradeSample,
) -> None:
    winning = _aggregate_at(
        _snapshot(_sample(base_sample, 1), _sample(base_sample, 2, net=Decimal(2))),
        ExperienceFallbackLevel.STRATEGY,
    )
    first_loss = _aggregate_at(
        _snapshot(
            _sample(
                base_sample,
                3,
                net=Decimal(-5),
                realized_r=Decimal(-2),
                outcome="LOSS",
            )
        ),
        ExperienceFallbackLevel.STRATEGY,
    )

    assert winning.max_cumulative_net_pnl_drawdown_quote == 0
    assert winning.max_cumulative_realized_r_drawdown == 0
    assert first_loss.max_cumulative_net_pnl_drawdown_quote == 5
    assert first_loss.max_cumulative_realized_r_drawdown == 2


def test_same_exit_time_uses_stable_identity_tie_order(base_sample: ExperienceTradeSample) -> None:
    exit_at = START + timedelta(hours=1)
    profit = _sample(
        base_sample,
        1,
        net=Decimal(10),
        decision_at=START,
        exit_at=exit_at,
        duration=timedelta(minutes=1),
    )
    loss = _sample(
        base_sample,
        2,
        net=Decimal(-15),
        outcome="LOSS",
        decision_at=START + timedelta(microseconds=1),
        exit_at=exit_at,
        duration=timedelta(minutes=1),
    )
    forward = AGGREGATOR.aggregate(_snapshot(profit, loss))
    raw_reverse = _snapshot(profit, loss)
    object.__setattr__(raw_reverse, "samples", tuple(reversed(raw_reverse.samples)))
    reverse = AGGREGATOR.aggregate(raw_reverse)

    assert forward == reverse
    assert (
        _aggregate_at(
            _snapshot(profit, loss), ExperienceFallbackLevel.STRATEGY
        ).max_cumulative_net_pnl_drawdown_quote
        == 15
    )


def test_full_contexts_roll_up_without_double_counting(base_sample: ExperienceTradeSample) -> None:
    up = _sample(base_sample, 1, direction=Direction.UP)
    down = _sample(base_sample, 2, direction=Direction.DOWN)
    result = AGGREGATOR.aggregate(_snapshot(up, down))

    assert (
        len(
            [
                item
                for item in result.aggregates
                if item.key.level is ExperienceFallbackLevel.FULL_CONTEXT
            ]
        )
        == 2
    )
    for level in tuple(ExperienceFallbackLevel)[1:]:
        match = [item for item in result.aggregates if item.key.level is level]
        assert len(match) == 1
        assert match[0].sample_count == 2
    assert (
        sum(
            item.sample_count
            for item in result.aggregates
            if item.key.level is ExperienceFallbackLevel.FULL_CONTEXT
        )
        == 2
    )


def test_source_does_not_split_keys_but_strategy_version_does(
    base_sample: ExperienceTradeSample,
) -> None:
    source_result = AGGREGATOR.aggregate(
        _snapshot(
            _sample(base_sample, 1, source_id=SourceId("alpha")),
            _sample(base_sample, 2, source_id=SourceId("beta")),
        )
    )
    version_result = AGGREGATOR.aggregate(
        _snapshot(
            _sample(base_sample, 3, strategy_version="1.0.0"),
            _sample(base_sample, 4, strategy_version="2.0.0"),
        )
    )

    assert len(source_result.aggregates) == 5
    assert all(item.sample_count == 2 for item in source_result.aggregates)
    assert len(version_result.aggregates) == 10
    assert all(item.sample_count == 1 for item in version_result.aggregates)


def test_cross_symbol_strategy_rollup_and_mixed_quote_fail_closed(
    base_sample: ExperienceTradeSample,
) -> None:
    btc = _sample(base_sample, 1, symbol=TradingSymbol("BTC", "USDT"))
    eth = _sample(base_sample, 2, symbol=TradingSymbol("ETH", "USDT"))
    result = AGGREGATOR.aggregate(_snapshot(btc, eth))
    strategy = [
        item for item in result.aggregates if item.key.level is ExperienceFallbackLevel.STRATEGY
    ]
    assert len(strategy) == 1
    assert strategy[0].sample_count == 2
    assert strategy[0].quote_currency == "USDT"

    incompatible = _sample(base_sample, 3, symbol=TradingSymbol("ETH", "USD"))
    with pytest.raises(ExperienceAggregationError, match="incompatible quote"):
        AGGREGATOR.aggregate(_snapshot(btc, incompatible))


@pytest.mark.parametrize("malformation", ("schema", "hierarchy", "nan", "classification"))
def test_malformed_snapshot_or_sample_fails_before_aggregation(
    base_sample: ExperienceTradeSample, malformation: str
) -> None:
    sample = _sample(base_sample, 1)
    snapshot = _snapshot(sample)
    if malformation == "schema":
        object.__setattr__(snapshot, "sample_snapshot_schema_version", "forged")
    elif malformation == "hierarchy":
        object.__setattr__(sample, "hierarchical_keys", sample.hierarchical_keys[:-1])
    elif malformation == "nan":
        object.__setattr__(sample, "net_realized_pnl", Decimal("NaN"))
    else:
        object.__setattr__(sample, "outcome_classification", "LOSS")

    with pytest.raises((ExperienceAggregationError, ValueError)):
        AGGREGATOR.aggregate(snapshot)


@pytest.mark.parametrize("mutation", ("net_outcome", "realized_r_outcome", "fingerprint_keys"))
def test_coherent_post_creation_sample_mutation_fails_at_authority_boundary(
    base_sample: ExperienceTradeSample, mutation: str
) -> None:
    sample = _sample(
        base_sample,
        1,
        net=Decimal(-10),
        gross=Decimal(-9),
        fees=Decimal(1),
        realized_r=Decimal(-1),
        outcome="LOSS",
    )
    snapshot = _snapshot(sample)
    if mutation == "net_outcome":
        object.__setattr__(sample, "net_realized_pnl", Decimal(10))
        object.__setattr__(sample, "outcome_classification", "PROFIT")
    elif mutation == "realized_r_outcome":
        forged_net = sample.approved_risk_quote
        object.__setattr__(sample, "gross_realized_pnl", forged_net + sample.total_trade_fees_quote)
        object.__setattr__(sample, "net_realized_pnl", forged_net)
        object.__setattr__(sample, "realized_r_net", Decimal(1))
        object.__setattr__(sample, "outcome_classification", "PROFIT")
    else:
        forged_direction = (
            Direction.DOWN if sample.fingerprint.direction is Direction.UP else Direction.UP
        )
        object.__setattr__(sample.fingerprint, "direction", forged_direction)
        full_context = sample.hierarchical_keys[0]
        assert full_context.level is ExperienceFallbackLevel.FULL_CONTEXT
        object.__setattr__(full_context, "direction", forged_direction)

    with pytest.raises(ExperienceProjectionError, match="sample integrity seal mismatch"):
        AGGREGATOR.aggregate(snapshot)


def test_duplicate_decision_identity_fails_closed(base_sample: ExperienceTradeSample) -> None:
    sample = _sample(base_sample, 1)
    snapshot = _snapshot(sample)
    object.__setattr__(snapshot, "samples", (sample, sample))

    with pytest.raises(ExperienceAggregationError, match="duplicate DecisionIdentity"):
        AGGREGATOR.aggregate(snapshot)


def test_ambient_decimal_context_cannot_change_output(base_sample: ExperienceTradeSample) -> None:
    snapshot = _snapshot(
        _sample(base_sample, 1, net=Decimal(1), realized_r=Decimal(1)),
        _sample(base_sample, 2, net=Decimal(-3), realized_r=Decimal("-0.3"), outcome="LOSS"),
        _sample(base_sample, 3, net=Decimal(2), realized_r=Decimal("0.2")),
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        low_precision = AGGREGATOR.aggregate(snapshot)
        setcontext(Context(prec=60))
        high_precision = AGGREGATOR.aggregate(snapshot)
    finally:
        setcontext(original)

    assert low_precision == high_precision


def test_output_is_immutable_and_detached_from_input(base_sample: ExperienceTradeSample) -> None:
    sample = _sample(base_sample, 1)
    snapshot = _snapshot(sample)
    result = AGGREGATOR.aggregate(snapshot)
    before = deepcopy(result)

    object.__setattr__(sample, "net_realized_pnl", Decimal(999))
    assert result == before
    with pytest.raises(FrozenInstanceError):
        result.total_sample_count = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.aggregates[0].sample_count = 0  # type: ignore[misc]


def test_aggregation_dependency_and_future_scope_guards() -> None:
    tree = ast.parse(Path(aggregation_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden_dependencies = (
        "accounting",
        "dashboard",
        "exchange",
        "journal",
        "paper",
        "risk",
        "strategies",
    )
    assert not any(token in module for module in imported for token in forbidden_dependencies)

    aggregate_fields = {item.name for item in fields(ExperienceAggregate)}
    forbidden_metrics = (
        "bayes",
        "confidence",
        "edge",
        "fundamental",
        "mae",
        "mfe",
        "posterior",
        "process",
        "recency",
        "sharpe",
        "sortino",
    )
    assert not any(token in name for name in aggregate_fields for token in forbidden_metrics)
