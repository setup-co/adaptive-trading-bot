"""Fail-closed public-live quote assembly for existing operational safety."""

from dataclasses import dataclass
from decimal import Decimal

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.market_data.live import LiveMarketQuote
from adaptive_trading_bot.risk.safety import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import SourceId


class LiveOperationalHealthAssemblyError(ValueError):
    """A live quote cannot establish complete feed-derived health facts."""


@dataclass(frozen=True, slots=True)
class LiveOperationalHealthInputs:
    """Explicit non-feed facts required by the operational safety boundary."""

    time_sync_offset_ms: Decimal
    volatility_safety_state: VolatilitySafetyState
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int


def operational_health_from_live_quote(
    quote: LiveMarketQuote,
    inputs: LiveOperationalHealthInputs,
) -> OperationalHealthSnapshot:
    """Map a successful timestamped public quote without inferring unrelated health."""

    if not isinstance(quote, LiveMarketQuote):
        raise TypeError("quote must be a LiveMarketQuote")
    if not isinstance(inputs, LiveOperationalHealthInputs):
        raise TypeError("inputs must be LiveOperationalHealthInputs")
    try:
        quote.validate()
    except (AttributeError, TypeError, ValueError) as error:
        raise LiveOperationalHealthAssemblyError("live quote validation failed") from error
    if quote.source_timestamp is None:
        raise LiveOperationalHealthAssemblyError(
            "live quote source timestamp is required for market-data freshness"
        )
    try:
        return OperationalHealthSnapshot(
            source_id=SourceId(quote.source_id.value),
            symbol=TradingSymbol(quote.symbol.base, quote.symbol.quote),
            observed_at=quote.observed_at,
            market_data_observed_at=quote.source_timestamp,
            feed_integrity_state=FeedIntegrityState.OK,
            exchange_api_health_state=ExchangeApiHealthState.HEALTHY,
            spread_bps=quote.spread_bps,
            time_sync_offset_ms=inputs.time_sync_offset_ms,
            volatility_safety_state=inputs.volatility_safety_state,
            suspicious_price_anomaly=inputs.suspicious_price_anomaly,
            consecutive_order_rejections=inputs.consecutive_order_rejections,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise LiveOperationalHealthAssemblyError(
            "explicit operational health input validation failed"
        ) from error
