"""Fail-closed full-close exit fill adapter for shared position accounting."""

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
from adaptive_trading_bot.orders import ExitIntentPurpose, ExitSide
from adaptive_trading_bot.orders.exit_intent import POST_FILL_STOP_RECOVERY_POLICY_VERSION
from adaptive_trading_bot.paper.exit_execution import PaperExitMarketFill
from adaptive_trading_bot.strategies.core import SourceIdentity


class PaperExitFillAccountingAdapterError(ValueError):
    """A protective exit fill cannot safely cross into shared accounting."""


def paper_exit_fill_to_position_accounting_fill(
    fill: PaperExitMarketFill,
    costs: SimulatedExecutionCostConfiguration,
) -> PositionAccountingFill:
    """Revalidate and copy an exit fill without changing arithmetic or authority."""

    if not isinstance(fill, PaperExitMarketFill):
        raise TypeError("fill must be a PaperExitMarketFill")
    if not isinstance(costs, SimulatedExecutionCostConfiguration):
        raise TypeError("costs must be a SimulatedExecutionCostConfiguration")
    try:
        _validate_exit_fill(fill, costs)
    except (AttributeError, TypeError, ValueError) as error:
        raise PaperExitFillAccountingAdapterError(
            "Paper exit fill authority or pricing is inconsistent"
        ) from error
    return PositionAccountingFill(
        fill_id=_exit_fill_id(fill),
        symbol=TradingSymbol(fill.symbol.base, fill.symbol.quote),
        side=(
            SimulatedExecutionSide.BUY if fill.side is ExitSide.BUY else SimulatedExecutionSide.SELL
        ),
        quantity=fill.quantity,
        fill_at=fill.fill_at,
        fill_price=fill.fill_price,
        fee_quote=fill.fee_quote,
    )


def _validate_exit_fill(
    fill: PaperExitMarketFill,
    costs: SimulatedExecutionCostConfiguration,
) -> None:
    identity = fill.exit_intent_identity
    decision = fill.originating_decision_identity
    entry_identity = fill.originating_entry_intent_identity
    if (
        identity.originating_entry_intent_identity != entry_identity
        or entry_identity.decision_identity != decision
        or identity.purpose not in set(ExitIntentPurpose)
        or identity.exit_intent_schema_version != "2.0.0"
        or fill.simulation_schema_version != "2.0.0"
        or not isinstance(decision.source_id, SourceIdentity)
        or decision.source_id != fill.source_id
        or decision.symbol != fill.symbol
        or not isinstance(fill.source_id, SourceIdentity)
        or not isinstance(fill.symbol, TradingSymbol)
        or not isinstance(fill.side, ExitSide)
    ):
        raise PaperExitFillAccountingAdapterError("Paper exit fill identity is inconsistent")
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
            raise PaperExitFillAccountingAdapterError(f"Paper exit fill {name} is not canonical")
    decision_at = require_utc(decision.observed_at, field="exit decision observed_at")
    trigger_at = require_utc(fill.trigger_observed_at, field="exit trigger_observed_at")
    authorized_at = require_utc(fill.authorized_at, field="exit fill authorized_at")
    eligible_at = require_utc(fill.eligible_at, field="exit fill eligible_at")
    fill_at = require_utc(fill.fill_at, field="exit fill_at")
    if (
        identity.trigger_observed_at != trigger_at
        or authorized_at != trigger_at
        or not decision_at <= trigger_at <= eligible_at <= fill_at
        or (
            identity.purpose is ExitIntentPurpose.PROTECTIVE_STOP_FULL_CLOSE
            and decision_at >= trigger_at
        )
        or (
            identity.purpose is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
            and fill_at <= trigger_at
        )
        or (
            identity.purpose is ExitIntentPurpose.PROTECTIVE_STOP_FULL_CLOSE
            and (
                fill.originating_accounting_fill_id is not None
                or fill.recovery_policy_version is not None
            )
        )
        or (
            identity.purpose is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
            and (
                not isinstance(fill.originating_accounting_fill_id, str)
                or not fill.originating_accounting_fill_id
                or fill.originating_accounting_fill_id
                != fill.originating_accounting_fill_id.strip()
                or fill.recovery_policy_version != POST_FILL_STOP_RECOVERY_POLICY_VERSION
            )
        )
    ):
        raise PaperExitFillAccountingAdapterError("Paper exit fill chronology is inconsistent")
    for name, decimal_value in (
        ("quantity", fill.quantity),
        ("stop_basis", fill.stop_basis),
        ("trigger_reference_price", fill.trigger_reference_price),
        ("reference_price", fill.reference_price),
        ("spread_bps", fill.spread_bps),
        ("slippage_bps", fill.slippage_bps),
        ("fill_price", fill.fill_price),
        ("fee_bps", fill.fee_bps),
        ("fee_quote", fill.fee_quote),
    ):
        if not isinstance(decimal_value, Decimal) or not decimal_value.is_finite():
            raise PaperExitFillAccountingAdapterError(
                f"Paper exit fill {name} must be a finite Decimal"
            )
    costs.validate()
    quote = price_simulated_market_fill(
        side=(
            SimulatedExecutionSide.BUY if fill.side is ExitSide.BUY else SimulatedExecutionSide.SELL
        ),
        quantity=fill.quantity,
        reference_price=fill.reference_price,
        costs=costs,
    )
    if (
        fill.quantity <= 0
        or fill.stop_basis <= 0
        or fill.trigger_reference_price <= 0
        or fill.reference_price <= 0
        or fill.fill_price <= 0
        or fill.fee_quote < 0
        or fill.spread_bps != costs.spread_bps
        or fill.slippage_bps != costs.slippage_bps
        or fill.fee_bps != costs.fee_bps
        or fill.fill_price != quote.fill_price
        or fill.fee_quote != quote.fee_quote
    ):
        raise PaperExitFillAccountingAdapterError("Paper exit fill arithmetic is inconsistent")


def _exit_fill_id(fill: PaperExitMarketFill) -> str:
    decision = fill.originating_decision_identity
    canonical = "|".join(
        (
            (
                "paper-entry-stop-recovery-exit"
                if fill.exit_intent_identity.purpose
                is ExitIntentPurpose.ENTRY_STOP_INVALIDATION_FULL_CLOSE
                else "paper-protective-exit"
            ),
            fill.source_id.value,
            str(fill.symbol),
            decision.observed_at.isoformat(),
            decision.strategy_id,
            decision.strategy_version,
            fill.trigger_observed_at.isoformat(),
            fill.fill_at.isoformat(),
            fill.side.value,
            str(fill.quantity),
            str(fill.fill_price),
            str(fill.fee_quote),
        )
    )
    return f"paper-exit:{sha256(canonical.encode()).hexdigest()}"
