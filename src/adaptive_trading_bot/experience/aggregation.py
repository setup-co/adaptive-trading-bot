"""Deterministic descriptive aggregation of authoritative Experience trade samples."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.experience.core import (
    ExperienceContextKey,
    ExperienceFallbackLevel,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
)

_AGGREGATE_SCHEMA_VERSION = "1.0.0"
_AGGREGATE_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
_SAMPLE_SNAPSHOT_SCHEMA_VERSION = "1.0.0"
_AGGREGATION_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_MICROSECONDS_PER_SECOND = 1_000_000
_SECONDS_PER_DAY = 86_400
_LEVEL_ORDER = {level: index for index, level in enumerate(ExperienceFallbackLevel)}


class ExperienceAggregationError(ValueError):
    """Experience samples cannot be reduced into trustworthy descriptive metrics."""


class ExperienceMetricAvailability(StrEnum):
    """Whether a descriptive Experience metric has a valid denominator."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class ExperienceMetricUnavailableReason(StrEnum):
    """Stable reasons why a Phase 12.2 metric cannot be calculated."""

    NO_LOSS_DENOMINATOR = "NO_LOSS_DENOMINATOR"


@dataclass(frozen=True, slots=True)
class ExperienceDecimalMetric:
    """One exact Decimal metric with explicit denominator availability."""

    availability: ExperienceMetricAvailability
    value: Decimal | None
    unavailable_reason: ExperienceMetricUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.availability, ExperienceMetricAvailability):
            raise ExperienceAggregationError("metric availability is invalid")
        if self.availability is ExperienceMetricAvailability.AVAILABLE:
            if (
                not isinstance(self.value, Decimal)
                or not self.value.is_finite()
                or self.value < 0
                or self.unavailable_reason is not None
            ):
                raise ExperienceAggregationError("available metric value is invalid")
        elif (
            self.value is not None
            or self.unavailable_reason is not ExperienceMetricUnavailableReason.NO_LOSS_DENOMINATOR
        ):
            raise ExperienceAggregationError("unavailable metric state is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceAggregate:
    """Exact descriptive statistics for one authoritative hierarchical context key.

    ``expectancy_r`` and ``mean_realized_r_net`` intentionally name the same arithmetic mean.
    Drawdown fields describe only each key's cumulative realized completed-trade path from zero;
    they are not account-equity or percentage drawdowns.
    """

    key: ExperienceContextKey
    quote_currency: str
    sample_count: int
    profit_count: int
    loss_count: int
    breakeven_count: int
    win_rate: Decimal
    loss_rate: Decimal
    breakeven_rate: Decimal
    gross_realized_pnl_sum_quote: Decimal
    total_fees_sum_quote: Decimal
    net_realized_pnl_sum_quote: Decimal
    positive_net_pnl_sum_quote: Decimal
    negative_net_pnl_abs_sum_quote: Decimal
    profit_factor_net: ExperienceDecimalMetric
    realized_r_sum: Decimal
    mean_realized_r_net: Decimal
    median_realized_r_net: Decimal
    expectancy_net_quote: Decimal
    expectancy_r: Decimal
    mean_duration: timedelta
    median_duration: timedelta
    max_consecutive_losses: int
    max_cumulative_net_pnl_drawdown_quote: Decimal
    max_cumulative_realized_r_drawdown: Decimal
    aggregate_schema_version: str = field(init=False, default=_AGGREGATE_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    @property
    def sort_key(self) -> tuple[int, str, str, str, str, str, str, str, str, str, str, str]:
        """Return the explicit stable hierarchy/dimension ordering key."""

        return _context_key_sort_key(self.key)

    def validate(self) -> None:
        if self.aggregate_schema_version != _AGGREGATE_SCHEMA_VERSION:
            raise ExperienceAggregationError("aggregate schema version mismatch")
        if not isinstance(self.key, ExperienceContextKey):
            raise TypeError("aggregate key must be an ExperienceContextKey")
        self.key.validate()
        if (
            not isinstance(self.quote_currency, str)
            or not self.quote_currency
            or self.quote_currency != self.quote_currency.strip().upper()
        ):
            raise ExperienceAggregationError("aggregate quote currency is invalid")
        if self.key.symbol is not None and self.quote_currency != self.key.symbol.quote:
            raise ExperienceAggregationError("aggregate quote currency does not match its key")
        for name, count_value in (
            ("sample_count", self.sample_count),
            ("profit_count", self.profit_count),
            ("loss_count", self.loss_count),
            ("breakeven_count", self.breakeven_count),
        ):
            if type(count_value) is not int or count_value < (1 if name == "sample_count" else 0):
                raise ExperienceAggregationError(f"aggregate {name} is invalid")
        if self.profit_count + self.loss_count + self.breakeven_count != self.sample_count:
            raise ExperienceAggregationError("aggregate outcome counts do not sum to sample_count")
        if (self.win_rate, self.loss_rate, self.breakeven_rate) != _outcome_rates(
            self.profit_count,
            self.loss_count,
            self.breakeven_count,
        ):
            raise ExperienceAggregationError("aggregate outcome rates are inconsistent")
        for name, decimal_value in (
            ("gross_realized_pnl_sum_quote", self.gross_realized_pnl_sum_quote),
            ("total_fees_sum_quote", self.total_fees_sum_quote),
            ("net_realized_pnl_sum_quote", self.net_realized_pnl_sum_quote),
            ("positive_net_pnl_sum_quote", self.positive_net_pnl_sum_quote),
            ("negative_net_pnl_abs_sum_quote", self.negative_net_pnl_abs_sum_quote),
            ("realized_r_sum", self.realized_r_sum),
            ("mean_realized_r_net", self.mean_realized_r_net),
            ("median_realized_r_net", self.median_realized_r_net),
            ("expectancy_net_quote", self.expectancy_net_quote),
            ("expectancy_r", self.expectancy_r),
            (
                "max_cumulative_net_pnl_drawdown_quote",
                self.max_cumulative_net_pnl_drawdown_quote,
            ),
            ("max_cumulative_realized_r_drawdown", self.max_cumulative_realized_r_drawdown),
        ):
            if not isinstance(decimal_value, Decimal) or not decimal_value.is_finite():
                raise ExperienceAggregationError(f"aggregate {name} must be a finite Decimal")
        if self.total_fees_sum_quote < 0:
            raise ExperienceAggregationError("aggregate total fees cannot be negative")
        if self.positive_net_pnl_sum_quote < 0 or self.negative_net_pnl_abs_sum_quote < 0:
            raise ExperienceAggregationError("aggregate profit-factor sums cannot be negative")
        if (
            self.max_cumulative_net_pnl_drawdown_quote < 0
            or self.max_cumulative_realized_r_drawdown < 0
        ):
            raise ExperienceAggregationError("aggregate path drawdowns cannot be negative")
        if not isinstance(self.profit_factor_net, ExperienceDecimalMetric):
            raise TypeError("profit_factor_net must be an ExperienceDecimalMetric")
        _validate_profit_factor(
            self.profit_factor_net,
            self.positive_net_pnl_sum_quote,
            self.negative_net_pnl_abs_sum_quote,
        )
        with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
            if self.mean_realized_r_net != self.realized_r_sum / Decimal(self.sample_count):
                raise ExperienceAggregationError("mean realized R is inconsistent")
            if self.expectancy_r != self.mean_realized_r_net:
                raise ExperienceAggregationError("R expectancy must equal mean realized R")
            if self.expectancy_net_quote != self.net_realized_pnl_sum_quote / Decimal(
                self.sample_count
            ):
                raise ExperienceAggregationError("net quote expectancy is inconsistent")
        for name, duration_value in (
            ("mean_duration", self.mean_duration),
            ("median_duration", self.median_duration),
        ):
            if not isinstance(duration_value, timedelta) or duration_value < timedelta():
                raise ExperienceAggregationError(f"aggregate {name} is invalid")
        if (
            type(self.max_consecutive_losses) is not int
            or self.max_consecutive_losses < 0
            or self.max_consecutive_losses > self.sample_count
        ):
            raise ExperienceAggregationError("aggregate max_consecutive_losses is invalid")


@dataclass(frozen=True, slots=True)
class ExperienceAggregateSnapshot:
    """Canonical immutable aggregate output for one unique input sample set."""

    aggregates: tuple[ExperienceAggregate, ...]
    total_sample_count: int
    aggregate_snapshot_schema_version: str = field(
        init=False, default=_AGGREGATE_SNAPSHOT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if self.aggregate_snapshot_schema_version != _AGGREGATE_SNAPSHOT_SCHEMA_VERSION:
            raise ExperienceAggregationError("aggregate snapshot schema version mismatch")
        if not isinstance(self.aggregates, tuple):
            raise TypeError("aggregates must be an immutable tuple")
        if type(self.total_sample_count) is not int or self.total_sample_count < 0:
            raise ExperienceAggregationError("total_sample_count is invalid")
        for aggregate in self.aggregates:
            if not isinstance(aggregate, ExperienceAggregate):
                raise TypeError("aggregate snapshot contains an invalid value")
            aggregate.validate()
        if self.aggregates != tuple(sorted(self.aggregates, key=lambda item: item.sort_key)):
            raise ExperienceAggregationError("aggregate snapshot order is not canonical")
        keys = tuple(aggregate.key for aggregate in self.aggregates)
        if len(set(keys)) != len(keys):
            raise ExperienceAggregationError("aggregate snapshot contains duplicate keys")
        for level in ExperienceFallbackLevel:
            level_count = sum(
                aggregate.sample_count
                for aggregate in self.aggregates
                if aggregate.key.level is level
            )
            if level_count != self.total_sample_count:
                raise ExperienceAggregationError(
                    "aggregate hierarchy sample counts are inconsistent"
                )


class ExperienceAggregator:
    """Pure deterministic reducer of authoritative Experience trade samples."""

    __slots__ = ()

    def aggregate(self, snapshot: ExperienceTradeSampleSnapshot) -> ExperienceAggregateSnapshot:
        if not isinstance(snapshot, ExperienceTradeSampleSnapshot):
            raise TypeError("snapshot must be an ExperienceTradeSampleSnapshot")
        owned = deepcopy(snapshot)
        samples = _validated_samples(owned)
        grouped: dict[ExperienceContextKey, list[ExperienceTradeSample]] = {}
        for sample in samples:
            for key in sample.hierarchical_keys:
                grouped.setdefault(key, []).append(sample)
        aggregates = tuple(
            _aggregate_key(deepcopy(key), tuple(grouped[key]))
            for key in sorted(grouped, key=_context_key_sort_key)
        )
        return ExperienceAggregateSnapshot(aggregates, len(samples))


def _validated_samples(
    snapshot: ExperienceTradeSampleSnapshot,
) -> tuple[ExperienceTradeSample, ...]:
    if snapshot.sample_snapshot_schema_version != _SAMPLE_SNAPSHOT_SCHEMA_VERSION:
        raise ExperienceAggregationError("sample snapshot schema version mismatch")
    if not isinstance(snapshot.samples, tuple):
        raise TypeError("sample snapshot samples must be an immutable tuple")
    identities: list[object] = []
    for sample in snapshot.samples:
        if not isinstance(sample, ExperienceTradeSample):
            raise TypeError("sample snapshot contains an invalid sample")
        sample.validate()
        expected_classification = (
            "PROFIT"
            if sample.net_realized_pnl > 0
            else "LOSS"
            if sample.net_realized_pnl < 0
            else "BREAKEVEN"
        )
        if sample.outcome_classification != expected_classification:
            raise ExperienceAggregationError(
                "sample outcome classification contradicts authoritative net PnL"
            )
        if any(sample.decision_identity == identity for identity in identities):
            raise ExperienceAggregationError("duplicate DecisionIdentity sample")
        identities.append(sample.decision_identity)
    return snapshot.samples


def _aggregate_key(
    key: ExperienceContextKey, samples: tuple[ExperienceTradeSample, ...]
) -> ExperienceAggregate:
    if not samples:
        raise ExperienceAggregationError("aggregate key requires at least one sample")
    quotes = {sample.fingerprint.symbol.quote for sample in samples}
    if len(quotes) != 1:
        raise ExperienceAggregationError("aggregate key mixes incompatible quote currencies")
    ordered = tuple(sorted(samples, key=_completed_outcome_sort_key))
    profit_count = sum(sample.outcome_classification == "PROFIT" for sample in ordered)
    loss_count = sum(sample.outcome_classification == "LOSS" for sample in ordered)
    breakeven_count = sum(sample.outcome_classification == "BREAKEVEN" for sample in ordered)
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        gross_sum = sum((sample.gross_realized_pnl for sample in ordered), Decimal(0))
        fees_sum = sum((sample.total_trade_fees_quote for sample in ordered), Decimal(0))
        net_sum = sum((sample.net_realized_pnl for sample in ordered), Decimal(0))
        positive_sum = sum(
            (sample.net_realized_pnl for sample in ordered if sample.net_realized_pnl > 0),
            Decimal(0),
        )
        negative_abs_sum = sum(
            (-sample.net_realized_pnl for sample in ordered if sample.net_realized_pnl < 0),
            Decimal(0),
        )
        realized_rs = tuple(sample.realized_r_net for sample in ordered)
        realized_r_sum = sum(realized_rs, Decimal(0))
        mean_r = realized_r_sum / Decimal(len(ordered))
        expectancy_quote = net_sum / Decimal(len(ordered))
        max_net_drawdown = _max_cumulative_drawdown(
            tuple(sample.net_realized_pnl for sample in ordered)
        )
        max_r_drawdown = _max_cumulative_drawdown(realized_rs)
    durations = tuple(sample.duration for sample in ordered)
    win_rate, loss_rate, breakeven_rate = _outcome_rates(profit_count, loss_count, breakeven_count)
    return ExperienceAggregate(
        key=key,
        quote_currency=quotes.pop(),
        sample_count=len(ordered),
        profit_count=profit_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        win_rate=win_rate,
        loss_rate=loss_rate,
        breakeven_rate=breakeven_rate,
        gross_realized_pnl_sum_quote=gross_sum,
        total_fees_sum_quote=fees_sum,
        net_realized_pnl_sum_quote=net_sum,
        positive_net_pnl_sum_quote=positive_sum,
        negative_net_pnl_abs_sum_quote=negative_abs_sum,
        profit_factor_net=_profit_factor(positive_sum, negative_abs_sum),
        realized_r_sum=realized_r_sum,
        mean_realized_r_net=mean_r,
        median_realized_r_net=_median_decimal(realized_rs),
        expectancy_net_quote=expectancy_quote,
        expectancy_r=mean_r,
        mean_duration=_mean_duration(durations),
        median_duration=_median_duration(durations),
        max_consecutive_losses=_max_consecutive_losses(ordered),
        max_cumulative_net_pnl_drawdown_quote=max_net_drawdown,
        max_cumulative_realized_r_drawdown=max_r_drawdown,
    )


def _outcome_rates(profits: int, losses: int, breakevens: int) -> tuple[Decimal, Decimal, Decimal]:
    """Divide all counts by N and deterministically close Decimal rounding to exactly one."""

    total = profits + losses + breakevens
    if total < 1:
        raise ExperienceAggregationError("outcome rates require at least one sample")
    counts = (profits, losses, breakevens)
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        rates = [Decimal(count) / Decimal(total) for count in counts]
        residual = Decimal(1) - sum(rates, Decimal(0))
        rates[max(index for index, count in enumerate(counts) if count)] += residual
        return rates[0], rates[1], rates[2]


def _profit_factor(positive_sum: Decimal, negative_abs_sum: Decimal) -> ExperienceDecimalMetric:
    if negative_abs_sum == 0:
        return ExperienceDecimalMetric(
            ExperienceMetricAvailability.UNAVAILABLE,
            None,
            ExperienceMetricUnavailableReason.NO_LOSS_DENOMINATOR,
        )
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        value = positive_sum / negative_abs_sum
    return ExperienceDecimalMetric(ExperienceMetricAvailability.AVAILABLE, value, None)


def _validate_profit_factor(
    metric: ExperienceDecimalMetric, positive_sum: Decimal, negative_abs_sum: Decimal
) -> None:
    if negative_abs_sum == 0:
        if (
            metric.availability is not ExperienceMetricAvailability.UNAVAILABLE
            or metric.value is not None
            or metric.unavailable_reason
            is not ExperienceMetricUnavailableReason.NO_LOSS_DENOMINATOR
        ):
            raise ExperienceAggregationError("profit factor availability is inconsistent")
        return
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        expected = positive_sum / negative_abs_sum
    if (
        metric.availability is not ExperienceMetricAvailability.AVAILABLE
        or metric.value != expected
        or metric.unavailable_reason is not None
    ):
        raise ExperienceAggregationError("profit factor value is inconsistent")


def _median_decimal(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        value.days * _SECONDS_PER_DAY + value.seconds
    ) * _MICROSECONDS_PER_SECOND + value.microseconds


def _exact_timedelta_average(total_microseconds: int, count: int, *, metric: str) -> timedelta:
    microseconds, remainder = divmod(total_microseconds, count)
    if remainder:
        raise ExperienceAggregationError(f"{metric} has an unrepresentable sub-microsecond value")
    return timedelta(microseconds=microseconds)


def _mean_duration(values: tuple[timedelta, ...]) -> timedelta:
    return _exact_timedelta_average(
        sum(_timedelta_microseconds(value) for value in values),
        len(values),
        metric="mean duration",
    )


def _median_duration(values: tuple[timedelta, ...]) -> timedelta:
    ordered = sorted(_timedelta_microseconds(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return timedelta(microseconds=ordered[midpoint])
    return _exact_timedelta_average(
        ordered[midpoint - 1] + ordered[midpoint],
        2,
        metric="median duration",
    )


def _completed_outcome_sort_key(sample: ExperienceTradeSample) -> tuple[object, ...]:
    return (
        sample.exit_fill_at,
        *sample.decision_identity.sort_key,
        sample.accounting_exit_fill_id,
    )


def _max_consecutive_losses(samples: tuple[ExperienceTradeSample, ...]) -> int:
    current = 0
    maximum = 0
    for sample in samples:
        if sample.outcome_classification == "LOSS":
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _max_cumulative_drawdown(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext(_AGGREGATION_DECIMAL_CONTEXT):
        cumulative = Decimal(0)
        peak = Decimal(0)
        maximum = Decimal(0)
        for value in values:
            cumulative += value
            peak = max(peak, cumulative)
            maximum = max(maximum, peak - cumulative)
        return maximum


def _optional_value(value: object | None) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _context_key_sort_key(
    key: ExperienceContextKey,
) -> tuple[int, str, str, str, str, str, str, str, str, str, str, str]:
    return (
        _LEVEL_ORDER[key.level],
        key.strategy_id,
        key.strategy_version,
        _optional_value(key.symbol),
        _optional_value(key.entry_timeframe),
        _optional_value(key.regime_label),
        _optional_value(key.direction),
        _optional_value(key.trend_strength_bucket),
        _optional_value(key.volatility_state),
        _optional_value(key.volume_state),
        _optional_value(key.higher_timeframe_bias),
        _optional_value(key.multi_timeframe_alignment),
    )
