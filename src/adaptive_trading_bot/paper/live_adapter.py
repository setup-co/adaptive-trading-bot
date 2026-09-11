"""Pure public-live quote translation into existing Paper observations."""

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.market_data.live import LiveMarketQuote
from adaptive_trading_bot.paper.execution import PaperExecutionObservation
from adaptive_trading_bot.storage import SourceId


class LivePaperObservationAssemblyError(ValueError):
    """A live quote cannot safely establish a Paper observation."""


def paper_observation_from_live_quote(quote: LiveMarketQuote) -> PaperExecutionObservation:
    """Use the exact quote midpoint as the detached Paper reference price."""

    if not isinstance(quote, LiveMarketQuote):
        raise TypeError("quote must be a LiveMarketQuote")
    try:
        quote.validate()
    except (AttributeError, TypeError, ValueError) as error:
        raise LivePaperObservationAssemblyError("live quote validation failed") from error
    if quote.source_timestamp is None:
        raise LivePaperObservationAssemblyError(
            "live quote source timestamp is required for a Paper observation"
        )
    return PaperExecutionObservation(
        source_id=SourceId(quote.source_id.value),
        symbol=TradingSymbol(quote.symbol.base, quote.symbol.quote),
        observed_at=quote.observed_at,
        reference_price=quote.midpoint,
    )
