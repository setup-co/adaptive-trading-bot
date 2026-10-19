"""Opt-in Breakout + Retest zone-boundary invalidation hypothesis.

This strategy composes the accepted stateful Breakout evaluator.  It changes
only the initial invalidation reference of an otherwise eligible candidate;
it is deliberately absent from runtime and historical-research registries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.features import PivotKind
from adaptive_trading_bot.strategies.breakout_retest import (
    ArmedBreakout,
    BreakoutRetestConfiguration,
    BreakoutRetestReasonCode,
    BreakoutRetestRuleAudit,
    BreakoutRetestStrategy,
    BreakoutSide,
)
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    Strategy,
    StrategyEvaluation,
    StrategyEvaluationContext,
)

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)

BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID = "BREAKOUT_RETEST_ZONE_INVALIDATION_V1"


class BreakoutRetestZoneBoundary(StrEnum):
    """Audited retest-zone boundary selected for invalidation."""

    FLOOR = "RETEST_FLOOR"
    CEILING = "RETEST_CEILING"


class BreakoutRetestZoneInvalidationReasonCode(StrEnum):
    """Variant-local projection outcomes; baseline evidence remains in the audit."""

    UNDERLYING_NO_TRADE_PRESERVED = "UNDERLYING_NO_TRADE_PRESERVED"
    UNDERLYING_CANDIDATE_ACCEPTED = "UNDERLYING_CANDIDATE_ACCEPTED"
    ZONE_INVALIDATION_SELECTED = "ZONE_INVALIDATION_SELECTED"
    ZONE_INVALIDATION_GEOMETRY_VALID = "ZONE_INVALIDATION_GEOMETRY_VALID"

    UNDERLYING_CANDIDATE_AUDIT_INVALID = "UNDERLYING_CANDIDATE_AUDIT_INVALID"
    ZONE_INVALIDATION_WRONG_SIDE = "ZONE_INVALIDATION_WRONG_SIDE"
    ZONE_INVALIDATION_TOO_WIDE = "ZONE_INVALIDATION_TOO_WIDE"


@dataclass(frozen=True, slots=True)
class BreakoutRetestZoneInvalidationAudit:
    """Typed provenance for one baseline evaluation and optional zone projection."""

    policy_id: str
    decision_at: datetime
    underlying_strategy_id: str
    underlying_strategy_version: str
    underlying_action: CandidateAction
    underlying_reasons_for: tuple[BreakoutRetestReasonCode, ...]
    underlying_reasons_against: tuple[BreakoutRetestReasonCode, ...]
    underlying_rule_audit: BreakoutRetestRuleAudit
    original_candle_invalidation_reference: Decimal | None
    selected_zone_boundary: BreakoutRetestZoneBoundary | None
    selected_zone_invalidation_reference: Decimal | None
    selected_zone_invalidation_distance: Decimal | None


ZoneEvaluation = StrategyEvaluation[
    BreakoutRetestZoneInvalidationReasonCode,
    BreakoutRetestZoneInvalidationAudit,
]
UnderlyingEvaluation = StrategyEvaluation[BreakoutRetestReasonCode, BreakoutRetestRuleAudit]


class _CandidateAuditError(ValueError):
    """An underlying candidate does not prove the causal zone geometry."""


class BreakoutRetestZoneInvalidationStrategy:
    """Project a baseline candidate onto its same-decision retest-zone boundary.

    Every instance owns a fresh private :class:`BreakoutRetestStrategy` using
    the unchanged baseline configuration.
    """

    __slots__ = ("_configuration", "_underlying_strategy")

    strategy_id = "breakout_retest_zone_invalidation"
    strategy_version = "0.1.0"
    policy_id = BREAKOUT_RETEST_ZONE_INVALIDATION_POLICY_ID

    def __init__(
        self,
        configuration: BreakoutRetestConfiguration | None = None,
    ) -> None:
        effective_configuration = configuration or BreakoutRetestConfiguration()
        if not isinstance(effective_configuration, BreakoutRetestConfiguration):
            raise TypeError("configuration must be a BreakoutRetestConfiguration")
        self._configuration = effective_configuration
        self._underlying_strategy: Strategy[BreakoutRetestReasonCode, BreakoutRetestRuleAudit] = (
            BreakoutRetestStrategy(effective_configuration)
        )

    @property
    def configuration(self) -> BreakoutRetestConfiguration:
        """Return the unchanged baseline configuration used by the projection."""

        return self._configuration

    @property
    def underlying_strategy(
        self,
    ) -> Strategy[BreakoutRetestReasonCode, BreakoutRetestRuleAudit]:
        """Return the single stateful evaluator owned by this variant instance."""

        return self._underlying_strategy

    def armed_state(self, source_id: SourceIdentity, symbol: TradingSymbol) -> ArmedBreakout | None:
        """Expose real baseline armed state only for audit and synthetic tests."""

        underlying = self._underlying_strategy
        if not isinstance(underlying, BreakoutRetestStrategy):
            return None
        return underlying.armed_state(source_id, symbol)

    def evaluate(self, context: StrategyEvaluationContext) -> ZoneEvaluation:
        """Advance the baseline once and apply the alternate geometry if eligible."""

        original = self._underlying_strategy.evaluate(context)
        audit = self._audit(original, context)
        if original.action is CandidateAction.NO_TRADE:
            if original.reasons_against:
                return self._result(
                    context,
                    CandidateAction.NO_TRADE,
                    (),
                    (BreakoutRetestZoneInvalidationReasonCode.UNDERLYING_NO_TRADE_PRESERVED,),
                    audit,
                )
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (BreakoutRetestZoneInvalidationReasonCode.UNDERLYING_NO_TRADE_PRESERVED,),
                (),
                audit,
            )

        try:
            boundary, selected, distance = self._validated_zone_geometry(context, original)
        except _CandidateAuditError:
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (),
                (BreakoutRetestZoneInvalidationReasonCode.UNDERLYING_CANDIDATE_AUDIT_INVALID,),
                audit,
            )

        audit = BreakoutRetestZoneInvalidationAudit(
            policy_id=audit.policy_id,
            decision_at=audit.decision_at,
            underlying_strategy_id=audit.underlying_strategy_id,
            underlying_strategy_version=audit.underlying_strategy_version,
            underlying_action=audit.underlying_action,
            underlying_reasons_for=audit.underlying_reasons_for,
            underlying_reasons_against=audit.underlying_reasons_against,
            underlying_rule_audit=audit.underlying_rule_audit,
            original_candle_invalidation_reference=(audit.original_candle_invalidation_reference),
            selected_zone_boundary=boundary,
            selected_zone_invalidation_reference=selected,
            selected_zone_invalidation_distance=distance,
        )
        wrong_side = distance <= 0
        if wrong_side:
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (),
                (BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_WRONG_SIDE,),
                audit,
            )
        atr = original.rule_audit.atr_14
        assert isinstance(atr, Decimal)
        with localcontext(_DECIMAL_CONTEXT):
            too_wide = distance > self._configuration.max_invalidation_distance_atr * atr
        if too_wide:
            return self._result(
                context,
                CandidateAction.NO_TRADE,
                (),
                (BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_TOO_WIDE,),
                audit,
            )
        return self._result(
            context,
            original.action,
            (
                BreakoutRetestZoneInvalidationReasonCode.UNDERLYING_CANDIDATE_ACCEPTED,
                BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_SELECTED,
                BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_GEOMETRY_VALID,
            ),
            (),
            audit,
            reference_price=original.reference_price,
            invalidation_reference_price=selected,
        )

    def _validated_zone_geometry(
        self,
        context: StrategyEvaluationContext,
        original: UnderlyingEvaluation,
    ) -> tuple[BreakoutRetestZoneBoundary, Decimal, Decimal]:
        audit = original.rule_audit
        if (
            original.strategy_id != "breakout_retest"
            or original.strategy_version != "1.0.0"
            or original.source_id != context.source_id
            or original.symbol != context.symbol
            or original.observed_at != context.observed_at
            or original.action
            not in {CandidateAction.LONG_CANDIDATE, CandidateAction.SHORT_CANDIDATE}
            or not isinstance(audit, BreakoutRetestRuleAudit)
            or not _positive_finite(original.reference_price)
            or not _positive_finite(original.invalidation_reference_price)
            or not _positive_finite(audit.atr_14)
            or audit.atr_14 != context.features_15m.atr_14
            or audit.breakout_observed_at is None
            or audit.breakout_observed_at >= context.observed_at
            or audit.breakout_bar_sequence is None
            or audit.breakout_bar_sequence < 1
            or audit.bars_waited is None
            or audit.bars_waited < 1
            or not _positive_finite(audit.breakout_level)
            or audit.breakout_pivot_price != audit.breakout_level
            or audit.breakout_pivot_confirmed_at is None
            or audit.breakout_pivot_confirmed_at > audit.breakout_observed_at
        ):
            raise _CandidateAuditError("underlying candidate audit is incomplete")

        reference = original.reference_price
        original_stop = original.invalidation_reference_price
        atr = audit.atr_14
        breakout_level = audit.breakout_level
        assert isinstance(reference, Decimal)
        assert isinstance(original_stop, Decimal)
        assert isinstance(atr, Decimal)
        assert isinstance(breakout_level, Decimal)
        with localcontext(_DECIMAL_CONTEXT):
            tolerance = self._configuration.retest_tolerance_atr * atr
            expected_floor = breakout_level - tolerance
            expected_ceiling = breakout_level + tolerance

            if original.action is CandidateAction.LONG_CANDIDATE:
                boundary = BreakoutRetestZoneBoundary.FLOOR
                selected = audit.retest_floor
                expected_pivot_kind = PivotKind.HIGH
                expected_extreme = context.candle_15m.low
                distance = reference - selected if isinstance(selected, Decimal) else Decimal(0)
            else:
                boundary = BreakoutRetestZoneBoundary.CEILING
                selected = audit.retest_ceiling
                expected_pivot_kind = PivotKind.LOW
                expected_extreme = context.candle_15m.high
                distance = selected - reference if isinstance(selected, Decimal) else Decimal(0)

            expected_original_distance = (
                reference - original_stop
                if original.action is CandidateAction.LONG_CANDIDATE
                else original_stop - reference
            )

        if (
            audit.armed_side
            is not (
                BreakoutSide.LONG
                if original.action is CandidateAction.LONG_CANDIDATE
                else BreakoutSide.SHORT
            )
            or audit.breakout_pivot_kind is not expected_pivot_kind
            or audit.retest_floor != expected_floor
            or audit.retest_ceiling != expected_ceiling
            or audit.retest_extreme != expected_extreme
            or original_stop != expected_extreme
            or audit.invalidation_distance != expected_original_distance
            or reference != context.candle_15m.close
            or not _positive_finite(selected)
            or not distance.is_finite()
        ):
            raise _CandidateAuditError("underlying candidate audit is inconsistent")
        assert isinstance(selected, Decimal)
        return boundary, selected, distance

    def _audit(
        self,
        original: UnderlyingEvaluation,
        context: StrategyEvaluationContext,
    ) -> BreakoutRetestZoneInvalidationAudit:
        rule_audit = original.rule_audit
        if not isinstance(rule_audit, BreakoutRetestRuleAudit):
            rule_audit = BreakoutRetestRuleAudit(
                armed_side=None,
                breakout_level=None,
                breakout_observed_at=None,
                breakout_bar_sequence=None,
                breakout_pivot_kind=None,
                breakout_pivot_open_time=None,
                breakout_pivot_confirmed_at=None,
                breakout_pivot_price=None,
                current_pivot_price=None,
                bars_waited=None,
                atr_14=None,
                required_breakout_close=None,
                retest_floor=None,
                retest_ceiling=None,
                retest_extreme=None,
                invalidation_distance=None,
            )
        return BreakoutRetestZoneInvalidationAudit(
            policy_id=self.policy_id,
            decision_at=context.observed_at,
            underlying_strategy_id=original.strategy_id,
            underlying_strategy_version=original.strategy_version,
            underlying_action=original.action,
            underlying_reasons_for=original.reasons_for,
            underlying_reasons_against=original.reasons_against,
            underlying_rule_audit=rule_audit,
            original_candle_invalidation_reference=original.invalidation_reference_price,
            selected_zone_boundary=None,
            selected_zone_invalidation_reference=None,
            selected_zone_invalidation_distance=None,
        )

    def _result(
        self,
        context: StrategyEvaluationContext,
        action: CandidateAction,
        reasons_for: tuple[BreakoutRetestZoneInvalidationReasonCode, ...],
        reasons_against: tuple[BreakoutRetestZoneInvalidationReasonCode, ...],
        audit: BreakoutRetestZoneInvalidationAudit,
        *,
        reference_price: Decimal | None = None,
        invalidation_reference_price: Decimal | None = None,
    ) -> ZoneEvaluation:
        return StrategyEvaluation(
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            source_id=context.source_id,
            symbol=context.symbol,
            observed_at=context.observed_at,
            action=action,
            reference_price=reference_price,
            invalidation_reference_price=invalidation_reference_price,
            reasons_for=reasons_for,
            reasons_against=reasons_against,
            rule_audit=audit,
        )


def _positive_finite(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
