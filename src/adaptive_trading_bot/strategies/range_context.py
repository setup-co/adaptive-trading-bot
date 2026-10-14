"""Deterministic causal validation of reusable 15m range context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.features import ConfirmedPivot, PivotKind
from adaptive_trading_bot.regime import HigherTimeframeBias
from adaptive_trading_bot.strategies.core import SourceIdentity, StrategyEvaluationContext

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
RANGE_PARTIAL_EVIDENCE_RETENTION_V1 = "RANGE_PARTIAL_EVIDENCE_RETENTION_V1"


class RangeStateError(Exception):
    """Range input or per-series chronology is invalid and was not committed."""


class RangeStatus(StrEnum):
    """State of validated range evidence at one observed close."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_RANGE = "NOT_RANGE"
    VALID_RANGE = "VALID_RANGE"
    RANGE_BROKEN = "RANGE_BROKEN"
    RANGE_STALE = "RANGE_STALE"


class RangeReasonCode(StrEnum):
    """Canonical evidence and rejection reasons for range detection."""

    AMBIGUOUS_DUAL_PIVOT_EXCLUDED = "AMBIGUOUS_DUAL_PIVOT_EXCLUDED"
    NEED_FOUR_CONFIRMED_PIVOTS = "NEED_FOUR_CONFIRMED_PIVOTS"
    PIVOTS_NOT_ALTERNATING = "PIVOTS_NOT_ALTERNATING"
    UPPER_TOUCHES_TOO_FAR_APART = "UPPER_TOUCHES_TOO_FAR_APART"
    LOWER_TOUCHES_TOO_FAR_APART = "LOWER_TOUCHES_TOO_FAR_APART"
    RANGE_BOUNDARIES_INCOHERENT = "RANGE_BOUNDARIES_INCOHERENT"
    RANGE_WIDTH_TOO_NARROW = "RANGE_WIDTH_TOO_NARROW"
    RANGE_WIDTH_TOO_WIDE = "RANGE_WIDTH_TOO_WIDE"
    RANGE_GEOMETRY_VALID = "RANGE_GEOMETRY_VALID"
    REGIME_COMPATIBLE = "REGIME_COMPATIBLE"
    CONTEXT_NOT_READY = "CONTEXT_NOT_READY"
    FEATURES_NOT_WARMED = "FEATURES_NOT_WARMED"
    ATR_MISSING = "ATR_MISSING"
    ATR_NOT_POSITIVE = "ATR_NOT_POSITIVE"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"
    HTF_DIRECTIONAL = "HTF_DIRECTIONAL"
    ENTRY_TREND_TOO_STRONG = "ENTRY_TREND_TOO_STRONG"
    RANGE_STALE = "RANGE_STALE"
    RANGE_BROKEN_UP = "RANGE_BROKEN_UP"
    RANGE_BROKEN_DOWN = "RANGE_BROKEN_DOWN"


@dataclass(frozen=True, slots=True)
class ValidatedRangeConfiguration:
    """Exact validated parameters for V1 range construction."""

    entry_timeframe: Timeframe = Timeframe.MINUTES_15
    pivot_history_limit: int = 12
    boundary_cluster_tolerance_atr: Decimal = Decimal("0.40")
    minimum_range_width_atr: Decimal = Decimal("2.0")
    maximum_range_width_atr: Decimal = Decimal("8.0")
    breakout_invalidation_buffer_atr: Decimal = Decimal("0.15")
    maximum_bars_since_latest_qualifying_pivot: int = 16
    maximum_15m_trend_strength: int = 40
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )

    def __post_init__(self) -> None:
        if self.partial_evidence_policy_version != RANGE_PARTIAL_EVIDENCE_RETENTION_V1:
            raise ValueError("unsupported range partial-evidence policy")
        if self.entry_timeframe is not Timeframe.MINUTES_15:
            raise ValueError("validated range entry_timeframe must be 15m")
        for name, integer_value, minimum in (
            ("pivot_history_limit", self.pivot_history_limit, 4),
            (
                "maximum_bars_since_latest_qualifying_pivot",
                self.maximum_bars_since_latest_qualifying_pivot,
                0,
            ),
            ("maximum_15m_trend_strength", self.maximum_15m_trend_strength, 0),
        ):
            if type(integer_value) is not int:
                raise TypeError(f"{name} must be an integer")
            if integer_value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if self.maximum_15m_trend_strength > 100:
            raise ValueError("maximum_15m_trend_strength must be at most 100")
        for name, decimal_value in (
            ("boundary_cluster_tolerance_atr", self.boundary_cluster_tolerance_atr),
            ("minimum_range_width_atr", self.minimum_range_width_atr),
            ("maximum_range_width_atr", self.maximum_range_width_atr),
            ("breakout_invalidation_buffer_atr", self.breakout_invalidation_buffer_atr),
        ):
            if not isinstance(decimal_value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not decimal_value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.boundary_cluster_tolerance_atr < 0:
            raise ValueError("boundary_cluster_tolerance_atr must be non-negative")
        if self.minimum_range_width_atr <= 0:
            raise ValueError("minimum_range_width_atr must be positive")
        if self.maximum_range_width_atr < self.minimum_range_width_atr:
            raise ValueError("maximum_range_width_atr must be at least the minimum")
        if self.breakout_invalidation_buffer_atr < 0:
            raise ValueError("breakout_invalidation_buffer_atr must be non-negative")


@dataclass(frozen=True, slots=True)
class RangePivotIdentity:
    """Stable identity of one causally confirmed pivot fact."""

    kind: PivotKind
    pivot_open_time: datetime
    pivot_price: Decimal


@dataclass(frozen=True, slots=True)
class RangePivotReference:
    """Confirmed pivot plus the detector sequence where it became observable."""

    identity: RangePivotIdentity
    pivot: ConfirmedPivot
    confirmation_sequence: int


@dataclass(frozen=True, slots=True)
class ValidatedRange:
    """Immutable active range derived from exactly four latest usable pivots."""

    lower_boundary: Decimal
    upper_boundary: Decimal
    qualifying_pivots: tuple[
        RangePivotReference,
        RangePivotReference,
        RangePivotReference,
        RangePivotReference,
    ]
    latest_qualifying_pivot_confirmation_sequence: int


@dataclass(frozen=True, slots=True)
class RangeDetectorSeriesState:
    """Immutable auditable state for one source/symbol detector stream."""

    last_observed_at: datetime
    sequence: int
    pivot_history: tuple[RangePivotReference, ...]
    active_range: ValidatedRange | None
    seen_pivot_identities: tuple[RangePivotIdentity, ...]
    ambiguous_pivot_open_times: tuple[datetime, ...]
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )


@dataclass(frozen=True, slots=True)
class RangeContextSnapshot:
    """Immutable range-only output; it is not a strategy candidate or order."""

    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    status: RangeStatus
    is_valid_range: bool
    lower_boundary: Decimal | None
    upper_boundary: Decimal | None
    midpoint: Decimal | None
    range_width: Decimal | None
    width_in_atr: Decimal | None
    current_atr: Decimal | None
    upper_pair_deviation: Decimal | None
    lower_pair_deviation: Decimal | None
    qualifying_pivots: tuple[RangePivotReference, ...]
    latest_qualifying_pivot_age_bars: int | None
    upper_breakout_threshold: Decimal | None
    lower_breakout_threshold: Decimal | None
    reasons: tuple[RangeReasonCode, ...]
    partial_evidence_retained: bool = False
    partial_evidence_reset: bool = False
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )


@dataclass(frozen=True, slots=True)
class _SeriesKey:
    source_value: str
    symbol: TradingSymbol


@dataclass(frozen=True, slots=True)
class _Geometry:
    lower_boundary: Decimal | None
    upper_boundary: Decimal | None
    midpoint: Decimal | None
    width: Decimal | None
    width_in_atr: Decimal | None
    upper_deviation: Decimal | None
    lower_deviation: Decimal | None
    pivots: tuple[RangePivotReference, ...]
    latest_confirmation_sequence: int | None
    reasons: tuple[RangeReasonCode, ...]


@dataclass(frozen=True, slots=True)
class ValidatedRangeDetector:
    """Process synchronized 15m contexts into isolated validated range state."""

    configuration: ValidatedRangeConfiguration = field(default_factory=ValidatedRangeConfiguration)
    _states: dict[_SeriesKey, RangeDetectorSeriesState] = field(
        init=False, default_factory=dict, repr=False, compare=False
    )

    def process(self, context: StrategyEvaluationContext) -> RangeContextSnapshot:
        """Validate, stage, evaluate, then atomically commit one context."""

        self.configuration.__post_init__()
        key = _SeriesKey(context.source_id.value, context.symbol)
        previous = self._states.get(key)
        if previous is not None:
            expected = previous.last_observed_at + Timeframe.MINUTES_15.duration
            if context.observed_at != expected:
                raise RangeStateError("observed_at must advance by exactly one 15m interval")
        sequence = 1 if previous is None else previous.sequence + 1
        self._validate_new_pivots(context, previous)
        staged, ambiguity_reason, partial_expired = self._stage_ingestion(
            context, previous, sequence
        )

        with localcontext(_DECIMAL_CONTEXT):
            breakout = self._active_breakout(context, staged)
            compatibility = self._compatibility_failures(context)
            if breakout is not None:
                snapshot = breakout
                committed = _cleared(staged)
            else:
                hard_failure = bool(compatibility) and (
                    staged.active_range is not None
                    or not self._soft_incompatibility(context, compatibility)
                )
                if hard_failure:
                    snapshot = self._empty_snapshot(
                        context,
                        compatibility,
                        partial_evidence_reset=(
                            staged.active_range is None and bool(staged.pivot_history)
                        ),
                    )
                    committed = _cleared(staged)
                else:
                    partial_breakout = self._partial_breakout(
                        context, previous, staged, partial_expired
                    )
                    if partial_breakout is not None:
                        snapshot = partial_breakout
                        committed = _cleared(staged)
                    elif compatibility:
                        snapshot = self._empty_snapshot(
                            context,
                            compatibility,
                            partial_evidence_retained=bool(staged.pivot_history),
                            partial_evidence_reset=partial_expired,
                        )
                        committed = staged
                    else:
                        snapshot, committed = self._evaluate_compatible(
                            context, staged, ambiguity_reason, partial_expired
                        )
        self._states[key] = committed
        return snapshot

    def _active_breakout(
        self,
        context: StrategyEvaluationContext,
        staged: RangeDetectorSeriesState,
    ) -> RangeContextSnapshot | None:
        active = staged.active_range
        atr = context.features_15m.atr_14
        if active is None or not isinstance(atr, Decimal) or not atr.is_finite() or atr <= 0:
            return None
        age = staged.sequence - active.latest_qualifying_pivot_confirmation_sequence
        if age > self.configuration.maximum_bars_since_latest_qualifying_pivot:
            return None
        upper_break, lower_break = self._breakout_thresholds(active, atr)
        close = context.candle_15m.close
        reason = None
        if close >= upper_break:
            reason = RangeReasonCode.RANGE_BROKEN_UP
        elif close <= lower_break:
            reason = RangeReasonCode.RANGE_BROKEN_DOWN
        if reason is None:
            return None
        geometry = _geometry_from_active(active, atr)
        return self._snapshot(
            context,
            RangeStatus.RANGE_BROKEN,
            geometry,
            age,
            (reason,),
        )

    def state_for(
        self, source_id: SourceIdentity, symbol: TradingSymbol
    ) -> RangeDetectorSeriesState | None:
        """Return immutable per-series state for audit/testing."""

        return self._states.get(_SeriesKey(source_id.value, symbol))

    def _validate_new_pivots(
        self,
        context: StrategyEvaluationContext,
        previous: RangeDetectorSeriesState | None,
    ) -> None:
        seen = set(() if previous is None else previous.seen_pivot_identities)
        incoming: set[RangePivotIdentity] = set()
        for pivot in context.structure_15m.newly_confirmed:
            if (
                pivot.pivot_open_time.tzinfo is None
                or pivot.pivot_open_time.utcoffset() != timedelta()
                or pivot.confirmed_at.tzinfo is None
                or pivot.confirmed_at.utcoffset() != timedelta()
            ):
                raise RangeStateError("newly confirmed pivot timestamps must be UTC-aware")
            if (
                pivot.source_id != context.source_id
                or pivot.symbol != context.symbol
                or pivot.timeframe is not Timeframe.MINUTES_15
                or not isinstance(pivot.kind, PivotKind)
                or pivot.confirmed_at != context.observed_at
                or pivot.pivot_open_time >= pivot.confirmed_at
            ):
                raise RangeStateError("newly confirmed pivot identity or timing is invalid")
            if (
                not isinstance(pivot.pivot_price, Decimal)
                or not pivot.pivot_price.is_finite()
                or pivot.pivot_price <= 0
            ):
                raise RangeStateError("newly confirmed pivot price is invalid")
            identity = _pivot_identity(pivot)
            if identity in seen or identity in incoming:
                raise RangeStateError("duplicate newly confirmed pivot identity")
            incoming.add(identity)

    def _stage_ingestion(
        self,
        context: StrategyEvaluationContext,
        previous: RangeDetectorSeriesState | None,
        sequence: int,
    ) -> tuple[RangeDetectorSeriesState, bool, bool]:
        history = list(() if previous is None else previous.pivot_history)
        expired = bool(history) and (
            sequence - history[-1].confirmation_sequence
            > self.configuration.maximum_bars_since_latest_qualifying_pivot
        )
        if expired:
            history = []
        seen = list(() if previous is None else previous.seen_pivot_identities)
        ambiguous = set(() if previous is None else previous.ambiguous_pivot_open_times)
        incoming = sorted(
            context.structure_15m.newly_confirmed,
            key=lambda pivot: (
                pivot.pivot_open_time,
                0 if pivot.kind is PivotKind.HIGH else 1,
                pivot.pivot_price,
            ),
        )
        occurrence_kinds: dict[datetime, set[PivotKind]] = {}
        for item in history:
            occurrence_kinds.setdefault(item.identity.pivot_open_time, set()).add(
                item.identity.kind
            )
        for pivot in incoming:
            occurrence_kinds.setdefault(pivot.pivot_open_time, set()).add(pivot.kind)
        newly_ambiguous = {
            occurrence
            for occurrence, kinds in occurrence_kinds.items()
            if kinds == {PivotKind.HIGH, PivotKind.LOW}
        }
        ambiguous.update(newly_ambiguous)
        if newly_ambiguous:
            history = [
                item for item in history if item.identity.pivot_open_time not in newly_ambiguous
            ]
        for pivot in incoming:
            identity = _pivot_identity(pivot)
            seen.append(identity)
            if pivot.pivot_open_time not in ambiguous:
                history.append(RangePivotReference(identity, pivot, sequence))
        history = history[-self.configuration.pivot_history_limit :]
        return (
            RangeDetectorSeriesState(
                context.observed_at,
                sequence,
                tuple(history),
                None if previous is None else previous.active_range,
                tuple(seen),
                tuple(sorted(ambiguous)),
            ),
            bool(newly_ambiguous),
            expired,
        )

    @staticmethod
    def _soft_incompatibility(
        context: StrategyEvaluationContext, failures: tuple[RangeReasonCode, ...]
    ) -> bool:
        soft = {RangeReasonCode.HTF_DIRECTIONAL, RangeReasonCode.ENTRY_TREND_TOO_STRONG}
        bias = context.multi_timeframe_regime.higher_timeframe_bias
        return (
            bool(failures)
            and set(failures) <= soft
            and (
                RangeReasonCode.HTF_DIRECTIONAL not in failures
                or bias in {HigherTimeframeBias.UP, HigherTimeframeBias.DOWN}
            )
        )

    def _partial_breakout(
        self,
        context: StrategyEvaluationContext,
        previous: RangeDetectorSeriesState | None,
        staged: RangeDetectorSeriesState,
        partial_expired: bool,
    ) -> RangeContextSnapshot | None:
        """Check an old complete box first, so new pivots cannot hide its break."""
        atr = context.features_15m.atr_14
        if staged.active_range is not None or not isinstance(atr, Decimal) or atr <= 0:
            return None
        histories = (
            (() if previous is None or partial_expired else previous.pivot_history),
            staged.pivot_history,
        )
        for history in histories:
            geometry = self._geometry(history, atr)
            if geometry.reasons or geometry.latest_confirmation_sequence is None:
                continue
            age = staged.sequence - geometry.latest_confirmation_sequence
            if age > self.configuration.maximum_bars_since_latest_qualifying_pivot:
                continue
            assert geometry.upper_boundary is not None and geometry.lower_boundary is not None
            buffer = self.configuration.breakout_invalidation_buffer_atr * atr
            close = context.candle_15m.close
            reason = None
            if close >= geometry.upper_boundary + buffer:
                reason = RangeReasonCode.RANGE_BROKEN_UP
            elif close <= geometry.lower_boundary - buffer:
                reason = RangeReasonCode.RANGE_BROKEN_DOWN
            if reason is not None:
                return self._snapshot(
                    context,
                    RangeStatus.RANGE_BROKEN,
                    geometry,
                    age,
                    (reason,),
                    partial_evidence_reset=True,
                )
        return None

    def _evaluate_compatible(
        self,
        context: StrategyEvaluationContext,
        staged: RangeDetectorSeriesState,
        ambiguity_reason: bool,
        partial_expired: bool,
    ) -> tuple[RangeContextSnapshot, RangeDetectorSeriesState]:
        active = staged.active_range
        atr = context.features_15m.atr_14
        assert atr is not None and atr > 0
        if active is not None:
            active_geometry = _geometry_from_active(active, atr)
            age = staged.sequence - active.latest_qualifying_pivot_confirmation_sequence
            if age > self.configuration.maximum_bars_since_latest_qualifying_pivot:
                return (
                    self._snapshot(
                        context,
                        RangeStatus.RANGE_STALE,
                        active_geometry,
                        age,
                        (RangeReasonCode.RANGE_STALE,),
                    ),
                    _cleared(staged),
                )
            upper_break, lower_break = self._breakout_thresholds(active, atr)
            close = context.candle_15m.close
            if close >= upper_break:
                return (
                    self._snapshot(
                        context,
                        RangeStatus.RANGE_BROKEN,
                        active_geometry,
                        age,
                        (RangeReasonCode.RANGE_BROKEN_UP,),
                    ),
                    _cleared(staged),
                )
            if close <= lower_break:
                return (
                    self._snapshot(
                        context,
                        RangeStatus.RANGE_BROKEN,
                        active_geometry,
                        age,
                        (RangeReasonCode.RANGE_BROKEN_DOWN,),
                    ),
                    _cleared(staged),
                )

        geometry = self._geometry(staged.pivot_history, atr)
        prefix = (RangeReasonCode.AMBIGUOUS_DUAL_PIVOT_EXCLUDED,) if ambiguity_reason else ()
        if geometry.reasons:
            status = (
                RangeStatus.INSUFFICIENT_DATA
                if RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS in geometry.reasons
                else RangeStatus.NOT_RANGE
            )
            return (
                self._snapshot(
                    context,
                    status,
                    geometry,
                    None,
                    (*prefix, *geometry.reasons),
                    partial_evidence_reset=partial_expired,
                ),
                RangeDetectorSeriesState(
                    staged.last_observed_at,
                    staged.sequence,
                    staged.pivot_history,
                    None,
                    staged.seen_pivot_identities,
                    staged.ambiguous_pivot_open_times,
                ),
            )
        assert len(geometry.pivots) == 4
        assert geometry.lower_boundary is not None
        assert geometry.upper_boundary is not None
        assert geometry.latest_confirmation_sequence is not None
        qualifying = (
            geometry.pivots[0],
            geometry.pivots[1],
            geometry.pivots[2],
            geometry.pivots[3],
        )
        active = ValidatedRange(
            geometry.lower_boundary,
            geometry.upper_boundary,
            qualifying,
            geometry.latest_confirmation_sequence,
        )
        age = staged.sequence - geometry.latest_confirmation_sequence
        committed = RangeDetectorSeriesState(
            staged.last_observed_at,
            staged.sequence,
            staged.pivot_history,
            active,
            staged.seen_pivot_identities,
            staged.ambiguous_pivot_open_times,
        )
        return (
            self._snapshot(
                context,
                RangeStatus.VALID_RANGE,
                geometry,
                age,
                (*prefix, RangeReasonCode.RANGE_GEOMETRY_VALID, RangeReasonCode.REGIME_COMPATIBLE),
            ),
            committed,
        )

    def _geometry(self, history: tuple[RangePivotReference, ...], atr: Decimal) -> _Geometry:
        if len(history) < 4:
            return _empty_geometry(history, (RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS,))
        pivots = history[-4:]
        expected = (
            (PivotKind.LOW, PivotKind.HIGH, PivotKind.LOW, PivotKind.HIGH),
            (PivotKind.HIGH, PivotKind.LOW, PivotKind.HIGH, PivotKind.LOW),
        )
        kinds = tuple(item.identity.kind for item in pivots)
        occurrences = tuple(item.identity.pivot_open_time for item in pivots)
        if kinds not in expected or len(set(occurrences)) != 4:
            return _empty_geometry(pivots, (RangeReasonCode.PIVOTS_NOT_ALTERNATING,))
        highs = tuple(
            item.identity.pivot_price for item in pivots if item.identity.kind is PivotKind.HIGH
        )
        lows = tuple(
            item.identity.pivot_price for item in pivots if item.identity.kind is PivotKind.LOW
        )
        upper_deviation = abs(highs[0] - highs[1])
        lower_deviation = abs(lows[0] - lows[1])
        upper = (highs[0] + highs[1]) / Decimal(2)
        lower = (lows[0] + lows[1]) / Decimal(2)
        width = upper - lower
        width_in_atr = width / atr
        reasons: list[RangeReasonCode] = []
        tolerance = self.configuration.boundary_cluster_tolerance_atr * atr
        if upper_deviation > tolerance:
            reasons.append(RangeReasonCode.UPPER_TOUCHES_TOO_FAR_APART)
        if lower_deviation > tolerance:
            reasons.append(RangeReasonCode.LOWER_TOUCHES_TOO_FAR_APART)
        if upper <= lower:
            reasons.append(RangeReasonCode.RANGE_BOUNDARIES_INCOHERENT)
        if width < self.configuration.minimum_range_width_atr * atr:
            reasons.append(RangeReasonCode.RANGE_WIDTH_TOO_NARROW)
        if width > self.configuration.maximum_range_width_atr * atr:
            reasons.append(RangeReasonCode.RANGE_WIDTH_TOO_WIDE)
        return _Geometry(
            lower,
            upper,
            (lower + upper) / Decimal(2),
            width,
            width_in_atr,
            upper_deviation,
            lower_deviation,
            pivots,
            max(item.confirmation_sequence for item in pivots),
            tuple(reasons),
        )

    def _compatibility_failures(
        self, context: StrategyEvaluationContext
    ) -> tuple[RangeReasonCode, ...]:
        regime = context.multi_timeframe_regime
        feature = context.features_15m
        failures: list[RangeReasonCode] = []
        if not regime.is_fully_ready:
            failures.append(RangeReasonCode.CONTEXT_NOT_READY)
        if not feature.is_fully_warmed:
            failures.append(RangeReasonCode.FEATURES_NOT_WARMED)
        if feature.atr_14 is None:
            failures.append(RangeReasonCode.ATR_MISSING)
        elif (
            not isinstance(feature.atr_14, Decimal)
            or not feature.atr_14.is_finite()
            or feature.atr_14 <= 0
        ):
            failures.append(RangeReasonCode.ATR_NOT_POSITIVE)
        if regime.has_extreme_volatility:
            failures.append(RangeReasonCode.EXTREME_VOLATILITY)
        if regime.higher_timeframe_bias not in {
            HigherTimeframeBias.NEUTRAL,
            HigherTimeframeBias.MIXED,
        }:
            failures.append(RangeReasonCode.HTF_DIRECTIONAL)
        if regime.regime_15m.trend_strength > self.configuration.maximum_15m_trend_strength:
            failures.append(RangeReasonCode.ENTRY_TREND_TOO_STRONG)
        return tuple(failures)

    def _breakout_thresholds(self, active: ValidatedRange, atr: Decimal) -> tuple[Decimal, Decimal]:
        buffer = self.configuration.breakout_invalidation_buffer_atr * atr
        return active.upper_boundary + buffer, active.lower_boundary - buffer

    def _empty_snapshot(
        self,
        context: StrategyEvaluationContext,
        reasons: tuple[RangeReasonCode, ...],
        *,
        partial_evidence_retained: bool = False,
        partial_evidence_reset: bool = False,
    ) -> RangeContextSnapshot:
        return RangeContextSnapshot(
            context.source_id,
            context.symbol,
            context.observed_at,
            RangeStatus.NOT_RANGE,
            False,
            None,
            None,
            None,
            None,
            None,
            context.features_15m.atr_14,
            None,
            None,
            (),
            None,
            None,
            None,
            reasons,
            partial_evidence_retained,
            partial_evidence_reset,
        )

    def _snapshot(
        self,
        context: StrategyEvaluationContext,
        status: RangeStatus,
        geometry: _Geometry,
        age: int | None,
        reasons: tuple[RangeReasonCode, ...],
        *,
        partial_evidence_reset: bool = False,
    ) -> RangeContextSnapshot:
        atr = context.features_15m.atr_14
        upper_break = lower_break = None
        if (
            geometry.upper_boundary is not None
            and geometry.lower_boundary is not None
            and atr is not None
        ):
            buffer = self.configuration.breakout_invalidation_buffer_atr * atr
            upper_break = geometry.upper_boundary + buffer
            lower_break = geometry.lower_boundary - buffer
        return RangeContextSnapshot(
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            status=status,
            is_valid_range=status is RangeStatus.VALID_RANGE,
            lower_boundary=geometry.lower_boundary,
            upper_boundary=geometry.upper_boundary,
            midpoint=geometry.midpoint,
            range_width=geometry.width,
            width_in_atr=geometry.width_in_atr,
            current_atr=atr,
            upper_pair_deviation=geometry.upper_deviation,
            lower_pair_deviation=geometry.lower_deviation,
            qualifying_pivots=geometry.pivots,
            latest_qualifying_pivot_age_bars=age,
            upper_breakout_threshold=upper_break,
            lower_breakout_threshold=lower_break,
            reasons=reasons,
            partial_evidence_reset=partial_evidence_reset,
        )


def _pivot_identity(pivot: ConfirmedPivot) -> RangePivotIdentity:
    return RangePivotIdentity(pivot.kind, pivot.pivot_open_time, pivot.pivot_price)


def _empty_geometry(
    pivots: tuple[RangePivotReference, ...],
    reasons: tuple[RangeReasonCode, ...],
) -> _Geometry:
    return _Geometry(None, None, None, None, None, None, None, pivots, None, reasons)


def _geometry_from_active(active: ValidatedRange, atr: Decimal) -> _Geometry:
    highs = tuple(
        item.identity.pivot_price
        for item in active.qualifying_pivots
        if item.identity.kind is PivotKind.HIGH
    )
    lows = tuple(
        item.identity.pivot_price
        for item in active.qualifying_pivots
        if item.identity.kind is PivotKind.LOW
    )
    width = active.upper_boundary - active.lower_boundary
    return _Geometry(
        active.lower_boundary,
        active.upper_boundary,
        (active.lower_boundary + active.upper_boundary) / Decimal(2),
        width,
        width / atr,
        abs(highs[0] - highs[1]),
        abs(lows[0] - lows[1]),
        active.qualifying_pivots,
        active.latest_qualifying_pivot_confirmation_sequence,
        (),
    )


def _cleared(state: RangeDetectorSeriesState) -> RangeDetectorSeriesState:
    return RangeDetectorSeriesState(
        state.last_observed_at,
        state.sequence,
        (),
        None,
        state.seen_pivot_identities,
        state.ambiguous_pivot_open_times,
    )
