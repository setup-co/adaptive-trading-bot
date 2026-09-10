"""Fail-closed Paper-fill adapter for shared position accounting."""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

from adaptive_trading_bot.accounting import PositionAccountingFill
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.orders import EntryIntentPurpose, EntrySide
from adaptive_trading_bot.paper.execution import PaperMarketFill
from adaptive_trading_bot.strategies.core import SourceIdentity


class PaperFillAccountingAdapterError(ValueError):
    """A Paper fill cannot safely cross into shared accounting."""


def paper_fill_to_position_accounting_fill(fill: PaperMarketFill) -> PositionAccountingFill:
    """Revalidate and copy one Paper fill without pricing, sizing, or rounding changes."""

    if not isinstance(fill, PaperMarketFill):
        raise TypeError("fill must be a PaperMarketFill")
    try:
        _validate_paper_fill(fill)
    except (AttributeError, TypeError, ValueError) as error:
        raise PaperFillAccountingAdapterError(
            "Paper fill authority or audit is inconsistent"
        ) from error
    side = SimulatedExecutionSide.BUY if fill.side is EntrySide.BUY else SimulatedExecutionSide.SELL
    return PositionAccountingFill(
        fill_id=_paper_fill_id(fill),
        symbol=fill.symbol,
        side=side,
        quantity=fill.quantity,
        fill_at=fill.fill_at,
        fill_price=fill.fill_price,
        fee_quote=fill.fee_quote,
    )


def _validate_paper_fill(fill: PaperMarketFill) -> None:
    identity = fill.intent_identity
    decision = fill.decision_identity
    if (
        identity.decision_identity != decision
        or identity.purpose is not EntryIntentPurpose.ENTRY
        or identity.intent_schema_version != "1.0.0"
        or fill.simulation_schema_version != "1.0.0"
        or not isinstance(decision.source_id, SourceIdentity)
        or decision.source_id != fill.source_id
        or decision.symbol != fill.symbol
        or not isinstance(fill.source_id, SourceIdentity)
        or not isinstance(fill.symbol, TradingSymbol)
        or not isinstance(fill.side, EntrySide)
    ):
        raise PaperFillAccountingAdapterError("Paper fill identity is inconsistent")
    for name, text_value in (
        ("source_id", fill.source_id.value),
        ("strategy_id", decision.strategy_id),
        ("strategy_version", decision.strategy_version),
        ("risk_policy_version", fill.risk_policy_version),
        ("safety_policy_version", fill.safety_policy_version),
    ):
        if (
            not isinstance(text_value, str)
            or not text_value.strip()
            or text_value != text_value.strip()
        ):
            raise PaperFillAccountingAdapterError(f"Paper fill {name} is not canonical")

    decision_at = require_utc(decision.observed_at, field="Paper decision observed_at")
    authorized_at = require_utc(fill.authorized_at, field="Paper fill authorized_at")
    eligible_at = require_utc(fill.eligible_at, field="Paper fill eligible_at")
    fill_at = require_utc(fill.fill_at, field="Paper fill_at")
    if decision_at != authorized_at or not authorized_at <= eligible_at <= fill_at:
        raise PaperFillAccountingAdapterError("Paper fill chronology is inconsistent")

    for name, value in (
        ("quantity", fill.quantity),
        ("reference_price", fill.reference_price),
        ("spread_bps", fill.spread_bps),
        ("slippage_bps", fill.slippage_bps),
        ("fill_price", fill.fill_price),
        ("fee_bps", fill.fee_bps),
        ("fee_quote", fill.fee_quote),
    ):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise PaperFillAccountingAdapterError(f"Paper fill {name} must be a finite Decimal")
    if fill.quantity <= 0 or fill.reference_price <= 0 or fill.fill_price <= 0:
        raise PaperFillAccountingAdapterError("Paper fill quantity and prices must be positive")
    if min(fill.spread_bps, fill.slippage_bps, fill.fee_bps, fill.fee_quote) < 0:
        raise PaperFillAccountingAdapterError("Paper fill costs must be non-negative")

    side = SimulatedExecutionSide.BUY if fill.side is EntrySide.BUY else SimulatedExecutionSide.SELL
    costs = SimulatedExecutionCostConfiguration(
        spread_bps=fill.spread_bps,
        slippage_bps=fill.slippage_bps,
        fee_bps=fill.fee_bps,
        latency=eligible_at - authorized_at,
    )
    quote = price_simulated_market_fill(
        side=side,
        quantity=fill.quantity,
        reference_price=fill.reference_price,
        costs=costs,
    )
    if quote.fill_price != fill.fill_price or quote.fee_quote != fill.fee_quote:
        raise PaperFillAccountingAdapterError("Paper fill pricing audit is inconsistent")


def _paper_fill_id(fill: PaperMarketFill) -> str:
    identity = fill.intent_identity
    decision = identity.decision_identity
    parts = (
        decision.source_id.value,
        decision.symbol.base,
        decision.symbol.quote,
        decision.observed_at.isoformat(),
        decision.strategy_id,
        decision.strategy_version,
        identity.purpose.value,
        identity.intent_schema_version,
        fill.simulation_schema_version,
    )
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return f"paper:{sha256(canonical.encode('utf-8')).hexdigest()}"
