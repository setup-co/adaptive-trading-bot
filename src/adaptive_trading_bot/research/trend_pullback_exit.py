"""Descriptive causal research for the Trend Pullback EMA50 exit signal."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.strategies.exit_core import (
    OpenPositionIdentity,
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitValidationError,
    PositionSide,
)
from adaptive_trading_bot.strategies.trend_pullback_exit import (
    TrendPullbackEma50ExitStrategy,
)

_RESEARCH_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_RESEARCH_SCHEMA_VERSION = "1.0.0"


class TrendPullbackExitResearchError(ValueError):
    """The supplied causal research population is malformed or contradictory."""


@dataclass(frozen=True, slots=True)
class DescriptiveResearchRate:
    """Explicit numerator/denominator provenance for one descriptive fraction."""

    numerator: int
    denominator: int
    value: Decimal | None

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 0 or self.numerator > self.denominator:
            raise TrendPullbackExitResearchError("research rate counts are invalid")
        if self.denominator == 0:
            if self.value is not None:
                raise TrendPullbackExitResearchError("empty research rate must be unavailable")
        elif self.value is None or not self.value.is_finite() or not Decimal(0) <= self.value <= 1:
            raise TrendPullbackExitResearchError("research rate value is invalid")


@dataclass(frozen=True, slots=True)
class TrendPullbackExitPositionSummary:
    """First-signal result for one authoritative open-position identity."""

    position_identity: OpenPositionIdentity
    symbol: TradingSymbol
    position_side: PositionSide
    evaluation_count: int
    hold_count: int
    first_close_signal: PositionExitEvaluation | None
    first_close_signal_at: datetime | None
    holding_duration_to_first_signal: timedelta | None
    remained_unresolved: bool

    def __post_init__(self) -> None:
        self.position_identity.validate()
        if self.evaluation_count <= 0 or not 0 <= self.hold_count <= self.evaluation_count:
            raise TrendPullbackExitResearchError("position research counts are invalid")
        if self.remained_unresolved:
            if (
                self.first_close_signal is not None
                or self.first_close_signal_at is not None
                or self.holding_duration_to_first_signal is not None
                or self.hold_count != self.evaluation_count
            ):
                raise TrendPullbackExitResearchError("unresolved position claims a close signal")
        else:
            if (
                self.first_close_signal is None
                or self.first_close_signal_at is None
                or self.holding_duration_to_first_signal is None
                or self.hold_count != self.evaluation_count - 1
            ):
                raise TrendPullbackExitResearchError("resolved position lacks its first signal")
            self.first_close_signal.validate()
            if (
                self.first_close_signal.action is not PositionExitAction.CLOSE_FULL
                or self.first_close_signal.identity.position_identity != self.position_identity
                or self.first_close_signal.observed_at != self.first_close_signal_at
                or self.first_close_signal.position_side is not self.position_side
                or self.first_close_signal.symbol != self.symbol
                or self.holding_duration_to_first_signal < timedelta()
            ):
                raise TrendPullbackExitResearchError(
                    "first close signal provenance is inconsistent"
                )


@dataclass(frozen=True, slots=True)
class TrendPullbackExitStratum:
    """Signal coverage for one exact symbol and side."""

    symbol: TradingSymbol
    position_side: PositionSide
    position_count: int
    evaluation_count: int
    hold_count: int
    close_signal_count: int
    never_close_fraction: DescriptiveResearchRate


@dataclass(frozen=True, slots=True)
class TrendPullbackExitResearchReport:
    """Signal-only report; it deliberately makes no performance or portfolio claim."""

    supplied_context_count: int
    evaluated_context_count: int
    ignored_after_first_close_count: int
    hold_count: int
    close_signal_count: int
    position_count: int
    unresolved_position_count: int
    never_close_fraction: DescriptiveResearchRate
    positions: tuple[TrendPullbackExitPositionSummary, ...]
    strata: tuple[TrendPullbackExitStratum, ...]
    exit_policy_version: str
    is_trading_authority: bool = field(init=False, default=False)
    contains_execution_or_performance_claims: bool = field(init=False, default=False)
    research_schema_version: str = field(init=False, default=_RESEARCH_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if (
            min(
                self.supplied_context_count,
                self.evaluated_context_count,
                self.ignored_after_first_close_count,
                self.hold_count,
                self.close_signal_count,
                self.position_count,
                self.unresolved_position_count,
            )
            < 0
        ):
            raise TrendPullbackExitResearchError("research report counts cannot be negative")
        if self.evaluated_context_count + self.ignored_after_first_close_count != (
            self.supplied_context_count
        ):
            raise TrendPullbackExitResearchError("research context counts are inconsistent")
        if self.hold_count + self.close_signal_count != self.evaluated_context_count:
            raise TrendPullbackExitResearchError("research action counts are inconsistent")
        if self.position_count != len(self.positions):
            raise TrendPullbackExitResearchError("research position count is inconsistent")
        if self.unresolved_position_count != sum(
            position.remained_unresolved for position in self.positions
        ):
            raise TrendPullbackExitResearchError("unresolved position count is inconsistent")
        if self.close_signal_count != self.position_count - self.unresolved_position_count:
            raise TrendPullbackExitResearchError("first close signal count is inconsistent")
        if self.is_trading_authority or self.contains_execution_or_performance_claims:
            raise TrendPullbackExitResearchError("research report cannot claim trading authority")
        if self.research_schema_version != _RESEARCH_SCHEMA_VERSION:
            raise TrendPullbackExitResearchError("research schema version mismatch")


class TrendPullbackExitResearchEvaluator:
    """Replay sealed contexts causally until each position's first close signal."""

    __slots__ = ("_strategy",)

    def __init__(self) -> None:
        self._strategy = TrendPullbackEma50ExitStrategy()

    def evaluate(
        self, contexts: tuple[PositionExitEvaluationContext, ...]
    ) -> TrendPullbackExitResearchReport:
        """Return deterministic signal coverage independent of supplied input order."""

        if not isinstance(contexts, tuple):
            raise TypeError("research contexts must be an immutable tuple")
        owned = deepcopy(contexts)
        grouped: dict[OpenPositionIdentity, list[PositionExitEvaluationContext]] = defaultdict(list)
        for context in owned:
            if not isinstance(context, PositionExitEvaluationContext):
                raise TypeError("each research item must be a PositionExitEvaluationContext")
            try:
                context.validate()
            except (TypeError, ValueError) as exc:
                raise TrendPullbackExitResearchError(
                    "invalid sealed exit research context"
                ) from exc
            if (
                context.strategy_id != self._strategy.strategy_id
                or context.strategy_version != self._strategy.strategy_version
            ):
                raise TrendPullbackExitResearchError(
                    "research context does not belong to Trend Pullback V1"
                )
            grouped[context.position_identity].append(context)

        positions: list[TrendPullbackExitPositionSummary] = []
        ignored_count = 0
        for identity in sorted(grouped, key=lambda item: item.sort_key):
            observations = sorted(grouped[identity], key=lambda item: item.observed_at)
            self._validate_position_sequence(observations)
            evaluations: list[PositionExitEvaluation] = []
            first_close: PositionExitEvaluation | None = None
            for context in observations:
                if first_close is not None:
                    ignored_count += 1
                    continue
                try:
                    evaluation = self._strategy.evaluate_exit(context)
                except PositionExitValidationError as exc:
                    raise TrendPullbackExitResearchError(
                        "Trend Pullback exit evaluation failed closed"
                    ) from exc
                evaluations.append(evaluation)
                if evaluation.action is PositionExitAction.CLOSE_FULL:
                    first_close = evaluation
            first = observations[0]
            positions.append(
                TrendPullbackExitPositionSummary(
                    position_identity=deepcopy(identity),
                    symbol=deepcopy(first.symbol),
                    position_side=first.position_side,
                    evaluation_count=len(evaluations),
                    hold_count=sum(
                        evaluation.action is PositionExitAction.HOLD for evaluation in evaluations
                    ),
                    first_close_signal=deepcopy(first_close),
                    first_close_signal_at=(
                        None if first_close is None else first_close.observed_at
                    ),
                    holding_duration_to_first_signal=(
                        None
                        if first_close is None
                        else first_close.observed_at - first.entry_fill_at
                    ),
                    remained_unresolved=first_close is None,
                )
            )

        position_tuple = tuple(positions)
        strata = self._strata(position_tuple)
        unresolved = sum(position.remained_unresolved for position in position_tuple)
        evaluated = sum(position.evaluation_count for position in position_tuple)
        holds = sum(position.hold_count for position in position_tuple)
        report = TrendPullbackExitResearchReport(
            supplied_context_count=len(owned),
            evaluated_context_count=evaluated,
            ignored_after_first_close_count=ignored_count,
            hold_count=holds,
            close_signal_count=len(position_tuple) - unresolved,
            position_count=len(position_tuple),
            unresolved_position_count=unresolved,
            never_close_fraction=_rate(unresolved, len(position_tuple)),
            positions=position_tuple,
            strata=strata,
            exit_policy_version=self._strategy.exit_policy_version,
        )
        return deepcopy(report)

    @staticmethod
    def _validate_position_sequence(
        observations: list[PositionExitEvaluationContext],
    ) -> None:
        if not observations:
            raise TrendPullbackExitResearchError("position sequence cannot be empty")
        first = observations[0]
        fixed_facts = _fixed_position_facts(first)
        previous_at: datetime | None = None
        for context in observations:
            if _fixed_position_facts(context) != fixed_facts:
                raise TrendPullbackExitResearchError(
                    "one position has contradictory authoritative facts"
                )
            if previous_at is not None and context.observed_at <= previous_at:
                raise TrendPullbackExitResearchError(
                    "position observations must have unique chronological timestamps"
                )
            previous_at = context.observed_at

    @staticmethod
    def _strata(
        positions: tuple[TrendPullbackExitPositionSummary, ...],
    ) -> tuple[TrendPullbackExitStratum, ...]:
        grouped: dict[tuple[str, PositionSide], list[TrendPullbackExitPositionSummary]] = (
            defaultdict(list)
        )
        for position in positions:
            grouped[(str(position.symbol), position.position_side)].append(position)
        result: list[TrendPullbackExitStratum] = []
        for key in sorted(grouped, key=lambda item: (item[0], item[1].value)):
            members = grouped[key]
            unresolved = sum(member.remained_unresolved for member in members)
            result.append(
                TrendPullbackExitStratum(
                    symbol=deepcopy(members[0].symbol),
                    position_side=members[0].position_side,
                    position_count=len(members),
                    evaluation_count=sum(member.evaluation_count for member in members),
                    hold_count=sum(member.hold_count for member in members),
                    close_signal_count=len(members) - unresolved,
                    never_close_fraction=_rate(unresolved, len(members)),
                )
            )
        return tuple(result)


def _fixed_position_facts(context: PositionExitEvaluationContext) -> tuple[object, ...]:
    return (
        context.position_identity,
        context.originating_candidate_action,
        context.strategy_id,
        context.strategy_version,
        context.source_id,
        context.symbol,
        context.position_side,
        context.entry_fill_at,
        context.entry_fill_price,
        context.open_quantity,
        context.invalidation_reference_price,
        context.lifecycle_status,
        context.risk_policy_version,
        context.safety_policy_version,
    )


def _rate(numerator: int, denominator: int) -> DescriptiveResearchRate:
    if denominator == 0:
        return DescriptiveResearchRate(numerator, denominator, None)
    with localcontext(_RESEARCH_DECIMAL_CONTEXT):
        return DescriptiveResearchRate(numerator, denominator, Decimal(numerator) / denominator)
