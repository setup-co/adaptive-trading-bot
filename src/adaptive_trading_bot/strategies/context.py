"""Stateless fail-closed assembly of synchronized 15m strategy contexts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from adaptive_trading_bot.domain import Timeframe
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import (
    FeatureBatch,
    FeatureSeries,
    FeatureSnapshot,
    ObservedCandle,
    ObservedCandleBatch,
    PivotKind,
    StructureBatch,
    StructureSnapshot,
)
from adaptive_trading_bot.regime import (
    MultiTimeframeRegimeBatch,
    MultiTimeframeRegimeSnapshot,
)
from adaptive_trading_bot.strategies.core import StrategyContextError, StrategyEvaluationContext

if TYPE_CHECKING:
    from adaptive_trading_bot.research.research_v2_admission import ResearchV2ContextAuthorization


def _series_order(series: FeatureSeries) -> tuple[str, str]:
    return series.source_id.value, str(series.symbol)


def assemble_strategy_contexts(
    candle_batch: ObservedCandleBatch,
    feature_batch: FeatureBatch,
    structure_batch: StructureBatch,
    regime_batch: MultiTimeframeRegimeBatch,
    *,
    research_authorization: ResearchV2ContextAuthorization | None = None,
) -> tuple[StrategyEvaluationContext, ...]:
    """Validate all inputs before emitting canonical stateless 15m contexts."""

    observed_at = require_utc(candle_batch.observed_at, field="candle batch observed_at")
    for label, candidate in (
        ("feature", feature_batch.observed_at),
        ("structure", structure_batch.observed_at),
        ("regime", regime_batch.observed_at),
    ):
        if require_utc(candidate, field=f"{label} batch observed_at") != observed_at:
            raise StrategyContextError("all strategy input batch timestamps must match")

    if research_authorization is not None:
        research_authorization.validate()
    candles = _entry_candles(candle_batch, observed_at, research_authorization)
    features = _entry_features(feature_batch, observed_at)
    structures = _entry_structures(structure_batch, observed_at)
    regimes = _entry_regimes(regime_batch, observed_at)
    expected = set(candles)
    if not expected:
        if features or structures or regimes:
            raise StrategyContextError("15m strategy input identity sets must match exactly")
        return ()
    if set(features) != expected or set(structures) != expected or set(regimes) != expected:
        raise StrategyContextError("15m strategy input identity sets must match exactly")

    contexts: list[StrategyEvaluationContext] = []
    for series in sorted(expected, key=_series_order):
        candle = candles[series].candle
        feature = features[series]
        structure = structures[series]
        regime = regimes[series]
        if feature.bars_seen != structure.bars_seen:
            raise StrategyContextError(f"feature and structure bars_seen mismatch for {series}")
        if (
            regime.source_id != series.source_id
            or regime.symbol != series.symbol
            or regime.observed_at != observed_at
            or regime.regime_15m.source_id != series.source_id
            or regime.regime_15m.symbol != series.symbol
            or regime.regime_15m.timeframe is not Timeframe.MINUTES_15
            or regime.regime_15m.observed_at != observed_at
        ):
            raise StrategyContextError(f"multi-timeframe anchor mismatch for {series}")
        _validate_pivots(structure, series, observed_at)
        if research_authorization is None:
            contexts.append(
                StrategyEvaluationContext(
                    source_id=series.source_id,
                    symbol=series.symbol,
                    observed_at=observed_at,
                    candle_15m=candle,
                    features_15m=feature,
                    structure_15m=structure,
                    multi_timeframe_regime=regime,
                )
            )
        else:
            from adaptive_trading_bot.research.research_v2_admission import (
                ResearchV2StrategyContext,
            )

            contexts.append(
                ResearchV2StrategyContext(
                    source_id=series.source_id,
                    symbol=series.symbol,
                    observed_at=observed_at,
                    candle_15m=candle,
                    features_15m=feature,
                    structure_15m=structure,
                    multi_timeframe_regime=regime,
                    research_authorization=research_authorization,
                )
            )
    return tuple(contexts)


def _entry_candles(
    batch: ObservedCandleBatch,
    observed_at: datetime,
    research_authorization: ResearchV2ContextAuthorization | None = None,
) -> dict[FeatureSeries, ObservedCandle]:
    result: dict[FeatureSeries, ObservedCandle] = {}
    for event in batch.events:
        if event.series in result:
            raise StrategyContextError("candle batch contains duplicate series")
        if (
            event.candle.symbol != event.series.symbol
            or event.candle.timeframe is not event.series.timeframe
        ):
            raise StrategyContextError("candle identity does not match its series")
        if event.candle.close_time != observed_at:
            raise StrategyContextError("candle must close at the strategy observation time")
        if event.series.timeframe is Timeframe.MINUTES_15:
            if research_authorization is None and event.series.symbol.base not in {"BTC", "ETH"}:
                raise StrategyContextError("strategy symbol is outside V1")
            if research_authorization is not None and (
                event.series.source_id != research_authorization.source_id
                or event.series.symbol != research_authorization.symbol
            ):
                raise StrategyContextError("research symbol/source outside authorization")
            result[event.series] = event
    return result


def _entry_features(
    batch: FeatureBatch, observed_at: datetime
) -> dict[FeatureSeries, FeatureSnapshot]:
    result: dict[FeatureSeries, FeatureSnapshot] = {}
    for snapshot in batch.snapshots:
        series = FeatureSeries(snapshot.source_id, snapshot.symbol, snapshot.timeframe)
        if series in result:
            raise StrategyContextError("feature batch contains duplicate series")
        if snapshot.observed_at != observed_at:
            raise StrategyContextError("feature snapshot timestamp does not match its batch")
        if snapshot.timeframe is Timeframe.MINUTES_15:
            if snapshot.candle_close_time != observed_at:
                raise StrategyContextError("feature snapshot does not describe the current candle")
            result[series] = snapshot
    return result


def _entry_structures(
    batch: StructureBatch, observed_at: datetime
) -> dict[FeatureSeries, StructureSnapshot]:
    result: dict[FeatureSeries, StructureSnapshot] = {}
    for snapshot in batch.snapshots:
        series = FeatureSeries(snapshot.source_id, snapshot.symbol, snapshot.timeframe)
        if series in result:
            raise StrategyContextError("structure batch contains duplicate series")
        if snapshot.observed_at != observed_at:
            raise StrategyContextError("structure snapshot timestamp does not match its batch")
        if snapshot.timeframe is Timeframe.MINUTES_15:
            result[series] = snapshot
    return result


def _entry_regimes(
    batch: MultiTimeframeRegimeBatch, observed_at: datetime
) -> dict[FeatureSeries, MultiTimeframeRegimeSnapshot]:
    result: dict[FeatureSeries, MultiTimeframeRegimeSnapshot] = {}
    for snapshot in batch.snapshots:
        series = FeatureSeries(snapshot.source_id, snapshot.symbol, Timeframe.MINUTES_15)
        if series in result:
            raise StrategyContextError("regime batch contains duplicate 15m anchor")
        if snapshot.observed_at != observed_at:
            raise StrategyContextError("regime snapshot timestamp does not match its batch")
        result[series] = snapshot
    return result


def _validate_pivots(
    snapshot: StructureSnapshot,
    series: FeatureSeries,
    observed_at: datetime,
) -> None:
    for pivot, expected_kind in (
        (snapshot.latest_confirmed_high, PivotKind.HIGH),
        (snapshot.latest_confirmed_low, PivotKind.LOW),
    ):
        if pivot is None:
            continue
        if (
            pivot.source_id != series.source_id
            or pivot.symbol != series.symbol
            or pivot.timeframe is not Timeframe.MINUTES_15
            or pivot.kind is not expected_kind
            or pivot.confirmed_at > observed_at
        ):
            raise StrategyContextError("confirmed pivot identity, kind, or causality mismatch")
