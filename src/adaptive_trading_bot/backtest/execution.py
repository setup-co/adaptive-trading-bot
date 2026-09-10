"""Deterministic causal market-order execution over bounded historical candles.

This is a bar-level approximation, not an order-book simulator. The sole raw fill reference is
the open of the first execution candle at or after order eligibility. Spread, slippage, and fees
are explicit deterministic Decimal costs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    InvalidSimulatedFillPriceError,
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    HistoricalCandleStoreError,
    IntegrityReport,
    SourceId,
)


class BacktestExecutionError(Exception):
    """Base error for execution configuration, data, or fill failures."""


class ExecutionConfigurationError(BacktestExecutionError):
    """The bounded run or its order collection is invalid."""


class ExecutionIntegrityError(BacktestExecutionError):
    """Historical execution candles failed their pre-run integrity gate."""

    report: IntegrityReport

    def __init__(self, report: IntegrityReport) -> None:
        self.report = report
        super().__init__("historical execution range failed integrity validation")


class ExecutionSourceError(BacktestExecutionError):
    """The historical source changed or violated its contract after validation."""


class InvalidFillPriceError(BacktestExecutionError):
    """Configured SELL costs would produce a non-positive fill price."""


@dataclass(frozen=True, slots=True)
class ExecutionSeries:
    """Exchange-neutral historical series used as the execution venue approximation."""

    source_id: SourceId
    symbol: TradingSymbol
    timeframe: Timeframe


OrderSide = SimulatedExecutionSide


@dataclass(frozen=True, slots=True)
class MarketOrderRequest:
    """Immutable strategy-neutral request for a full market fill."""

    order_id: str
    created_at: datetime
    symbol: TradingSymbol
    side: OrderSide
    quantity: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str):
            raise TypeError("order_id must be a string")
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime")
        order_id = self.order_id.strip()
        if not order_id:
            raise ValueError("order_id must not be empty")
        if not isinstance(self.side, OrderSide):
            raise TypeError("side must be an OrderSide")
        if not isinstance(self.quantity, Decimal):
            raise TypeError("quantity must be a Decimal")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise ValueError("quantity must be finite and positive")
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "created_at", require_utc(self.created_at, field="created_at"))


ExecutionCostConfiguration = SimulatedExecutionCostConfiguration


@dataclass(frozen=True, slots=True)
class MarketFill:
    """Immutable deterministic full fill at an eligible execution-candle open."""

    order_id: str
    symbol: TradingSymbol
    side: OrderSide
    quantity: Decimal
    created_at: datetime
    eligible_at: datetime
    execution_candle_open_time: datetime
    fill_at: datetime
    reference_open_price: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    fill_price: Decimal
    fee_bps: Decimal
    fee_quote: Decimal
    source_id: SourceId
    execution_timeframe: Timeframe


class UnfilledReason(StrEnum):
    """Deterministic reasons a valid order received no fill."""

    NO_ELIGIBLE_EXECUTION_CANDLE = "NO_ELIGIBLE_EXECUTION_CANDLE"


@dataclass(frozen=True, slots=True)
class UnfilledOrder:
    """A valid request that exhausted the bounded dataset without an eligible open."""

    order: MarketOrderRequest
    eligible_at: datetime
    reason: UnfilledReason


@dataclass(frozen=True, slots=True)
class BacktestExecutionResult:
    """Immutable fills and unfilled requests with complete bounded-run provenance."""

    execution_series: ExecutionSeries
    costs: ExecutionCostConfiguration
    start: datetime
    end: datetime
    fills: tuple[MarketFill, ...]
    unfilled_orders: tuple[UnfilledOrder, ...]
    requested_order_count: int
    filled_count: int
    unfilled_count: int


class HistoricalExecutionSource:
    """Integrity-gated adapter around the historical candle-store contract."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def load(
        self,
        series: ExecutionSeries,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        """Validate then read the exact bounded execution series without mutation."""

        try:
            report = self._store.scan_integrity(
                series.source_id,
                series.symbol,
                series.timeframe,
                start=start,
                end=end,
            )
        except HistoricalCandleStoreError as error:
            raise ExecutionSourceError("historical execution integrity scan failed") from error
        if not report.is_clean:
            raise ExecutionIntegrityError(report)

        try:
            candles = self._store.read(
                series.source_id,
                series.symbol,
                series.timeframe,
                start=start,
                end=end,
            )
        except HistoricalCandleStoreError as error:
            raise ExecutionSourceError("historical execution read failed") from error
        expected_opens = _expected_open_times(start, end, series.timeframe)
        if tuple(candle.open_time for candle in candles) != expected_opens:
            raise ExecutionSourceError("execution coverage changed after integrity validation")
        if any(
            candle.symbol != series.symbol or candle.timeframe is not series.timeframe
            for candle in candles
        ):
            raise ExecutionSourceError("historical store violated execution series identity")
        return candles


class ExecutionCandleSource(Protocol):
    """Narrow validated-candle boundary consumed by the execution simulator."""

    def load(
        self,
        series: ExecutionSeries,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        """Return integrity-gated chronological execution candles."""
        ...


def _expected_open_times(
    start: datetime, end: datetime, timeframe: Timeframe
) -> tuple[datetime, ...]:
    expected = align_timestamp(start, timeframe)
    if expected < start:
        expected += timeframe.duration
    opens: list[datetime] = []
    while expected < end:
        opens.append(expected)
        expected += timeframe.duration
    return tuple(opens)


class BacktestExecutionSimulator:
    """Fill scripted market orders from the first causally eligible candle open."""

    def __init__(self, source: ExecutionCandleSource) -> None:
        self._source = source

    def run(
        self,
        execution_series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        orders: Iterable[MarketOrderRequest],
        *,
        start: datetime,
        end: datetime,
    ) -> BacktestExecutionResult:
        """Run one deterministic bounded execution simulation."""

        start = require_utc(start, field="start")
        end = require_utc(end, field="end")
        if start >= end:
            raise ExecutionConfigurationError("execution start must be before end")

        requested = tuple(sorted(orders, key=lambda order: (order.created_at, order.order_id)))
        order_ids = tuple(order.order_id for order in requested)
        if len(set(order_ids)) != len(order_ids):
            raise ExecutionConfigurationError("duplicate order_id is forbidden")
        if any(order.symbol != execution_series.symbol for order in requested):
            raise ExecutionConfigurationError("every order symbol must match the execution series")

        candles = self._source.load(execution_series, start=start, end=end)
        fills: list[MarketFill] = []
        unfilled: list[UnfilledOrder] = []
        for order in requested:
            eligible_at = order.created_at + costs.latency
            execution_candle = next(
                (candle for candle in candles if candle.open_time >= eligible_at),
                None,
            )
            if execution_candle is None:
                unfilled.append(
                    UnfilledOrder(
                        order,
                        eligible_at,
                        UnfilledReason.NO_ELIGIBLE_EXECUTION_CANDLE,
                    )
                )
                continue
            fills.append(_fill(order, eligible_at, execution_candle, execution_series, costs))

        return BacktestExecutionResult(
            execution_series=execution_series,
            costs=costs,
            start=start,
            end=end,
            fills=tuple(fills),
            unfilled_orders=tuple(unfilled),
            requested_order_count=len(requested),
            filled_count=len(fills),
            unfilled_count=len(unfilled),
        )


def _fill(
    order: MarketOrderRequest,
    eligible_at: datetime,
    candle: Candle,
    series: ExecutionSeries,
    costs: ExecutionCostConfiguration,
) -> MarketFill:
    try:
        quote = price_simulated_market_fill(
            side=order.side,
            quantity=order.quantity,
            reference_price=candle.open,
            costs=costs,
        )
    except InvalidSimulatedFillPriceError as error:
        raise InvalidFillPriceError(str(error)) from error

    return MarketFill(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        created_at=order.created_at,
        eligible_at=eligible_at,
        execution_candle_open_time=candle.open_time,
        fill_at=candle.open_time,
        reference_open_price=candle.open,
        spread_bps=costs.spread_bps,
        slippage_bps=costs.slippage_bps,
        fill_price=quote.fill_price,
        fee_bps=costs.fee_bps,
        fee_quote=quote.fee_quote,
        source_id=series.source_id,
        execution_timeframe=series.timeframe,
    )
