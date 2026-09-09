"""Environment-neutral immutable strategy input, output, and protocol contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import FeatureSnapshot, PivotKind, StructureSnapshot
from adaptive_trading_bot.regime import MultiTimeframeRegimeSnapshot


class StrategyContextError(Exception):
    """Synchronized strategy inputs are incomplete or inconsistent."""


@runtime_checkable
class SourceIdentity(Protocol):
    """Environment-neutral identity contract implemented by the project source id."""

    @property
    def value(self) -> str:
        """Return the stable source identifier value."""


class CandidateAction(StrEnum):
    """A strategy evaluation result; candidates are not orders or risk approval."""

    LONG_CANDIDATE = "LONG_CANDIDATE"
    SHORT_CANDIDATE = "SHORT_CANDIDATE"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class StrategyEvaluationContext:
    """Synchronized information observable for one symbol at one closed 15m bar."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    candle_15m: Candle
    features_15m: FeatureSnapshot
    structure_15m: StructureSnapshot
    multi_timeframe_regime: MultiTimeframeRegimeSnapshot

    def __post_init__(self) -> None:
        observed_at = require_utc(self.observed_at, field="strategy context observed_at")
        candle = self.candle_15m
        feature = self.features_15m
        structure = self.structure_15m
        regime = self.multi_timeframe_regime
        if self.symbol.base not in {"BTC", "ETH"}:
            raise StrategyContextError("strategy symbol is outside V1")
        if (
            candle.symbol != self.symbol
            or candle.timeframe is not Timeframe.MINUTES_15
            or candle.close_time != observed_at
        ):
            raise StrategyContextError("15m candle identity or close time mismatch")
        if (
            feature.source_id != self.source_id
            or feature.symbol != self.symbol
            or feature.timeframe is not Timeframe.MINUTES_15
            or feature.observed_at != observed_at
            or feature.candle_open_time != candle.open_time
            or feature.candle_close_time != candle.close_time
        ):
            raise StrategyContextError("15m feature identity or candle time mismatch")
        if (
            structure.source_id != self.source_id
            or structure.symbol != self.symbol
            or structure.timeframe is not Timeframe.MINUTES_15
            or structure.observed_at != observed_at
            or structure.bars_seen != feature.bars_seen
        ):
            raise StrategyContextError("15m structure identity, time, or bars_seen mismatch")
        if (
            regime.source_id != self.source_id
            or regime.symbol != self.symbol
            or regime.observed_at != observed_at
            or regime.regime_15m.source_id != self.source_id
            or regime.regime_15m.symbol != self.symbol
            or regime.regime_15m.timeframe is not Timeframe.MINUTES_15
            or regime.regime_15m.observed_at != observed_at
        ):
            raise StrategyContextError("multi-timeframe anchor mismatch")
        for pivot, expected_kind in (
            (structure.latest_confirmed_high, PivotKind.HIGH),
            (structure.latest_confirmed_low, PivotKind.LOW),
        ):
            if pivot is not None and (
                pivot.source_id != self.source_id
                or pivot.symbol != self.symbol
                or pivot.timeframe is not Timeframe.MINUTES_15
                or pivot.kind is not expected_kind
                or pivot.confirmed_at > observed_at
            ):
                raise StrategyContextError("confirmed pivot identity, kind, or causality mismatch")


@dataclass(frozen=True, slots=True)
class StrategyEvaluation[ReasonT, AuditT]:
    """Strategy-agnostic candidate assessment with typed evidence and audit data."""

    strategy_id: str
    strategy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    action: CandidateAction
    reference_price: Decimal | None
    invalidation_reference_price: Decimal | None
    reasons_for: tuple[ReasonT, ...]
    reasons_against: tuple[ReasonT, ...]
    rule_audit: AuditT


@runtime_checkable
class Strategy[ReasonT, AuditT](Protocol):
    """Shared strategy interface usable unchanged by every environment adapter."""

    @property
    def strategy_id(self) -> str:
        """Return the immutable strategy identifier."""

    @property
    def strategy_version(self) -> str:
        """Return the immutable strategy rule version."""

    def evaluate(self, context: StrategyEvaluationContext) -> StrategyEvaluation[ReasonT, AuditT]:
        """Evaluate one already-observed context without side effects."""
