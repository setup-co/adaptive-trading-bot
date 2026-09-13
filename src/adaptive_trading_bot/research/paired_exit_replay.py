"""Execution-aware paired single-position research for the Trend Pullback exit hypothesis.

This adapter composes existing entry authorization, Backtest execution, exit strategies, and
shared accounting authorities.  It does not generate decisions, submit runtime orders, model a
portfolio, or force-close an unresolved research position.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.backtest import (
    AccountingFillBatch,
    BacktestExecutionError,
    BacktestExecutionSimulator,
    ExecutionCostConfiguration,
    ExecutionSeries,
    FillAccountingEvent,
    MarketFill,
    MarketOrderRequest,
    OrderSide,
    PositionAccountingEngine,
    PositionAccountingError,
    PositionSnapshot,
    UnfilledOrder,
)
from adaptive_trading_bot.domain import Candle
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.orders import (
    EntryOrderIntent,
    EntryOrderIntentAuthorizer,
    EntrySide,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.regime import (
    RegimeCompositionError,
    TechnicalRegimeError,
    validate_multi_timeframe_regime_snapshot,
    validate_technical_regime_snapshot,
)
from adaptive_trading_bot.strategies import (
    HoldOnlyExitStrategy,
    OpenPositionIdentity,
    PositionExitAction,
    PositionExitEvaluation,
    PositionExitEvaluationContext,
    PositionExitStrategy,
    PositionExitValidationError,
    PositionLifecycleStatus,
    PositionSide,
    StrategyContextError,
    StrategyEvaluationContext,
    TrendPullbackEma50ExitStrategy,
    TrendPullbackStrategy,
)

_REPLAY_SCHEMA_VERSION = "1.0.0"
_RESEARCH_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_AFTER_DECISION = timedelta(microseconds=1)


class PairedExitReplayError(ValueError):
    """Authoritative input, chronology, execution, or accounting failed closed."""


class PairedExitReplayStatus(StrEnum):
    """Top-level result of attempting the one authoritative entry."""

    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    REPLAYED = "REPLAYED"


class ExitReplayBranchPolicy(StrEnum):
    """The two fixed research branches."""

    HOLD_ONLY = "HOLD_ONLY"
    EMA50_THESIS_EXIT = "EMA50_THESIS_EXIT"


class HistoricalExitCause(StrEnum):
    """Winning cause for one historical branch exit request."""

    PROTECTIVE_STOP = "PROTECTIVE_STOP"
    EMA50_THESIS_FAILURE = "EMA50_THESIS_FAILURE"


class PairedExitComparisonState(StrEnum):
    """Whether branch realized outcomes are descriptively comparable."""

    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    BOTH_CLOSED = "BOTH_CLOSED"
    BOTH_OPEN = "BOTH_OPEN"
    BASELINE_CLOSED_CANDIDATE_OPEN = "BASELINE_CLOSED_CANDIDATE_OPEN"
    BASELINE_OPEN_CANDIDATE_CLOSED = "BASELINE_OPEN_CANDIDATE_CLOSED"


@dataclass(frozen=True, slots=True)
class HistoricalExitBranchResult:
    """One isolated branch using the shared fill and accounting authorities."""

    branch_policy: ExitReplayBranchPolicy
    originating_decision_identity: DecisionIdentity
    execution_series: ExecutionSeries
    execution_costs: ExecutionCostConfiguration
    entry_order: MarketOrderRequest
    entry_fill: MarketFill
    stop_basis: Decimal
    exit_evaluations: tuple[PositionExitEvaluation, ...]
    evaluated_exit_context_count: int
    hold_count: int
    discretionary_close_signal_count: int
    protective_stop_breach_at: datetime | None
    protective_stop_precedence_applied: bool
    winning_exit_cause: HistoricalExitCause | None
    exit_trigger_at: datetime | None
    exit_request: MarketOrderRequest | None
    exit_fill: MarketFill | None
    exit_unfilled: UnfilledOrder | None
    accounting_events: tuple[FillAccountingEvent, ...]
    final_position: PositionSnapshot
    position_closed: bool
    realized_gross_pnl: Decimal | None
    total_fees: Decimal | None
    realized_net_pnl: Decimal | None
    unresolved_open_quantity: Decimal
    unresolved_average_entry_price: Decimal | None
    is_trading_authority: bool = field(init=False, default=False)
    replay_schema_version: str = field(init=False, default=_REPLAY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if self.replay_schema_version != _REPLAY_SCHEMA_VERSION:
            raise PairedExitReplayError("branch replay schema version mismatch")
        if self.is_trading_authority:
            raise PairedExitReplayError("research branch cannot claim trading authority")
        if self.evaluated_exit_context_count != len(self.exit_evaluations):
            raise PairedExitReplayError("branch exit evaluation count is inconsistent")
        if self.hold_count + self.discretionary_close_signal_count != len(self.exit_evaluations):
            raise PairedExitReplayError("branch exit action counts are inconsistent")
        for evaluation in self.exit_evaluations:
            evaluation.validate()
            if evaluation.originating_decision_identity != self.originating_decision_identity:
                raise PairedExitReplayError("branch evaluation decision identity mismatch")
        if self.branch_policy is ExitReplayBranchPolicy.HOLD_ONLY and (
            self.discretionary_close_signal_count != 0
        ):
            raise PairedExitReplayError("HOLD-only branch cannot expose a discretionary close")
        if self.protective_stop_precedence_applied and (
            self.winning_exit_cause is not HistoricalExitCause.PROTECTIVE_STOP
            or self.discretionary_close_signal_count == 0
        ):
            raise PairedExitReplayError("protective-stop precedence audit is inconsistent")
        if self.winning_exit_cause is None:
            if any(
                value is not None
                for value in (
                    self.exit_trigger_at,
                    self.exit_request,
                    self.exit_fill,
                    self.exit_unfilled,
                )
            ):
                raise PairedExitReplayError("branch without an exit cause has exit provenance")
        elif self.exit_trigger_at is None or self.exit_request is None:
            raise PairedExitReplayError("winning exit cause lacks its request provenance")
        if self.exit_fill is not None and self.exit_unfilled is not None:
            raise PairedExitReplayError("exit request cannot be both filled and unfilled")
        if self.position_closed is not (self.final_position.quantity == 0):
            raise PairedExitReplayError("branch closure state contradicts accounting")
        if self.position_closed:
            if self.exit_fill is None or any(
                value is None
                for value in (
                    self.realized_gross_pnl,
                    self.total_fees,
                    self.realized_net_pnl,
                )
            ):
                raise PairedExitReplayError("closed branch lacks authoritative realized facts")
            if (
                self.realized_gross_pnl != self.final_position.cumulative_gross_realized_pnl
                or self.total_fees != self.final_position.cumulative_fees
                or self.realized_net_pnl != self.final_position.cumulative_net_realized_pnl
            ):
                raise PairedExitReplayError("branch realized facts diverge from accounting")
            if (
                self.unresolved_open_quantity != 0
                or self.unresolved_average_entry_price is not None
            ):
                raise PairedExitReplayError("closed branch cannot expose unresolved position")
        else:
            if any(
                value is not None
                for value in (
                    self.realized_gross_pnl,
                    self.total_fees,
                    self.realized_net_pnl,
                )
            ):
                raise PairedExitReplayError("open branch cannot claim a realized outcome")
            if self.unresolved_open_quantity <= 0 or self.unresolved_average_entry_price is None:
                raise PairedExitReplayError("open branch lacks its accounting state")


@dataclass(frozen=True, slots=True)
class PairedTrendPullbackExitReplayResult:
    """Paired descriptive result rooted in one authoritative entry decision."""

    status: PairedExitReplayStatus
    originating_decision_identity: DecisionIdentity
    execution_series: ExecutionSeries
    execution_costs: ExecutionCostConfiguration
    start: datetime
    end: datetime
    entry_order: MarketOrderRequest
    entry_fill: MarketFill | None
    entry_unfilled: UnfilledOrder | None
    baseline: HistoricalExitBranchResult | None
    ema50_candidate: HistoricalExitBranchResult | None
    comparison_state: PairedExitComparisonState
    realized_net_pnl_delta_candidate_minus_baseline: Decimal | None
    exit_fill_time_delta_candidate_minus_baseline: timedelta | None
    identical_starting_entry_fill: bool
    identical_execution_cost_configuration: bool
    is_trading_authority: bool = field(init=False, default=False)
    is_portfolio_performance: bool = field(init=False, default=False)
    replay_schema_version: str = field(init=False, default=_REPLAY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        require_utc(self.start, field="paired replay start")
        require_utc(self.end, field="paired replay end")
        if self.start >= self.end:
            raise PairedExitReplayError("paired replay range must be non-empty")
        if self.replay_schema_version != _REPLAY_SCHEMA_VERSION:
            raise PairedExitReplayError("paired replay schema version mismatch")
        if self.is_trading_authority or self.is_portfolio_performance:
            raise PairedExitReplayError(
                "paired research cannot claim runtime or portfolio authority"
            )
        if self.status is PairedExitReplayStatus.ENTRY_UNFILLED:
            if (
                self.entry_fill is not None
                or self.entry_unfilled is None
                or self.baseline is not None
                or self.ema50_candidate is not None
                or self.comparison_state is not PairedExitComparisonState.ENTRY_UNFILLED
                or self.realized_net_pnl_delta_candidate_minus_baseline is not None
                or self.exit_fill_time_delta_candidate_minus_baseline is not None
                or self.identical_starting_entry_fill
                or self.identical_execution_cost_configuration
            ):
                raise PairedExitReplayError("ENTRY_UNFILLED result is inconsistent")
            return
        if (
            self.entry_fill is None
            or self.entry_unfilled is not None
            or self.baseline is None
            or self.ema50_candidate is None
            or not self.identical_starting_entry_fill
            or not self.identical_execution_cost_configuration
        ):
            raise PairedExitReplayError("replayed pair lacks identical branch provenance")
        if (
            self.baseline.entry_fill != self.entry_fill
            or self.ema50_candidate.entry_fill != self.entry_fill
            or self.baseline.execution_costs != self.execution_costs
            or self.ema50_candidate.execution_costs != self.execution_costs
        ):
            raise PairedExitReplayError("paired branch start or cost configuration diverged")
        both_closed = self.baseline.position_closed and self.ema50_candidate.position_closed
        if both_closed:
            if (
                self.comparison_state is not PairedExitComparisonState.BOTH_CLOSED
                or self.realized_net_pnl_delta_candidate_minus_baseline is None
                or self.exit_fill_time_delta_candidate_minus_baseline is None
            ):
                raise PairedExitReplayError("closed pair lacks descriptive comparison")
        elif (
            self.realized_net_pnl_delta_candidate_minus_baseline is not None
            or self.exit_fill_time_delta_candidate_minus_baseline is not None
        ):
            raise PairedExitReplayError("incomparable branches cannot expose numeric deltas")
        expected_comparison = _comparison_state(self.baseline, self.ema50_candidate)
        if self.comparison_state is not expected_comparison:
            raise PairedExitReplayError("paired comparison state contradicts branch closure")


class PairedTrendPullbackExitReplayRunner:
    """Run the two exit policies from one shared authorized Backtest entry fill."""

    __slots__ = ("_execution",)

    def __init__(self, execution: BacktestExecutionSimulator) -> None:
        if not isinstance(execution, BacktestExecutionSimulator):
            raise TypeError("execution must be a BacktestExecutionSimulator")
        self._execution = execution

    def run(
        self,
        decision: DecisionJournalEntry[object, object],
        current_contexts: tuple[StrategyEvaluationContext, ...],
        execution_series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        start: datetime,
        end: datetime,
    ) -> PairedTrendPullbackExitReplayResult:
        """Execute one bounded paired replay without creating runtime authority."""

        start = require_utc(start, field="paired replay start")
        end = require_utc(end, field="paired replay end")
        if start >= end:
            raise PairedExitReplayError("paired replay start must be before end")
        if not isinstance(current_contexts, tuple):
            raise TypeError("current_contexts must be an immutable tuple")
        if not isinstance(execution_series, ExecutionSeries):
            raise TypeError("execution_series must be an ExecutionSeries")
        if not isinstance(costs, ExecutionCostConfiguration):
            raise TypeError("costs must be an ExecutionCostConfiguration")
        owned_costs = deepcopy(costs)
        owned_costs.validate()
        owned_decision = deepcopy(decision)
        owned_contexts = deepcopy(current_contexts)
        intent = self._authorize_entry(owned_decision)
        if (
            execution_series.source_id != intent.source_id
            or execution_series.symbol != intent.symbol
        ):
            raise PairedExitReplayError("execution series and entry authority differ")
        if not start <= intent.observed_at < end:
            raise PairedExitReplayError("entry decision lies outside the bounded replay range")
        series = deepcopy(execution_series)
        entry_order = _entry_order(intent)
        # The shared exit-context contract requires the accounting entry fill to be strictly
        # later than its originating decision.  Excluding the equal timestamp also prevents a
        # just-closed execution candle from being reused as post-decision entry liquidity.
        entry_execution_start = max(start, intent.observed_at + _AFTER_DECISION)
        if entry_execution_start >= end:
            raise PairedExitReplayError("bounded replay has no post-decision execution interval")
        try:
            entry_execution = self._execution.run(
                series,
                owned_costs,
                (entry_order,),
                start=entry_execution_start,
                end=end,
            )
        except (BacktestExecutionError, TypeError, ValueError) as exc:
            raise PairedExitReplayError("shared Backtest entry execution failed") from exc
        _validate_execution_result(
            entry_execution.execution_series, entry_execution.costs, series, owned_costs
        )
        if entry_execution.unfilled_count == 1:
            return PairedTrendPullbackExitReplayResult(
                status=PairedExitReplayStatus.ENTRY_UNFILLED,
                originating_decision_identity=deepcopy(owned_decision.identity),
                execution_series=series,
                execution_costs=owned_costs,
                start=start,
                end=end,
                entry_order=entry_order,
                entry_fill=None,
                entry_unfilled=deepcopy(entry_execution.unfilled_orders[0]),
                baseline=None,
                ema50_candidate=None,
                comparison_state=PairedExitComparisonState.ENTRY_UNFILLED,
                realized_net_pnl_delta_candidate_minus_baseline=None,
                exit_fill_time_delta_candidate_minus_baseline=None,
                identical_starting_entry_fill=False,
                identical_execution_cost_configuration=False,
            )
        if entry_execution.filled_count != 1 or entry_execution.unfilled_count != 0:
            raise PairedExitReplayError("entry execution returned contradictory fill counts")
        entry_fill = deepcopy(entry_execution.fills[0])
        self._validate_contexts(owned_contexts, owned_decision, entry_fill, start, end)
        baseline = self._run_branch(
            ExitReplayBranchPolicy.HOLD_ONLY,
            HoldOnlyExitStrategy(intent.strategy_id, intent.strategy_version),
            owned_decision,
            intent,
            entry_order,
            entry_fill,
            owned_contexts,
            series,
            owned_costs,
            start,
            end,
        )
        candidate = self._run_branch(
            ExitReplayBranchPolicy.EMA50_THESIS_EXIT,
            TrendPullbackEma50ExitStrategy(),
            owned_decision,
            intent,
            entry_order,
            entry_fill,
            owned_contexts,
            series,
            owned_costs,
            start,
            end,
        )
        comparison = _comparison_state(baseline, candidate)
        net_delta: Decimal | None = None
        time_delta: timedelta | None = None
        if comparison is PairedExitComparisonState.BOTH_CLOSED:
            assert baseline.realized_net_pnl is not None
            assert candidate.realized_net_pnl is not None
            assert baseline.exit_fill is not None
            assert candidate.exit_fill is not None
            with localcontext(_RESEARCH_DECIMAL_CONTEXT):
                net_delta = candidate.realized_net_pnl - baseline.realized_net_pnl
            time_delta = candidate.exit_fill.fill_at - baseline.exit_fill.fill_at
        result = PairedTrendPullbackExitReplayResult(
            status=PairedExitReplayStatus.REPLAYED,
            originating_decision_identity=deepcopy(owned_decision.identity),
            execution_series=deepcopy(series),
            execution_costs=deepcopy(owned_costs),
            start=start,
            end=end,
            entry_order=deepcopy(entry_order),
            entry_fill=deepcopy(entry_fill),
            entry_unfilled=None,
            baseline=baseline,
            ema50_candidate=candidate,
            comparison_state=comparison,
            realized_net_pnl_delta_candidate_minus_baseline=net_delta,
            exit_fill_time_delta_candidate_minus_baseline=time_delta,
            identical_starting_entry_fill=(
                baseline.entry_fill == candidate.entry_fill == entry_fill
            ),
            identical_execution_cost_configuration=(
                baseline.execution_costs == candidate.execution_costs == owned_costs
            ),
        )
        return deepcopy(result)

    @staticmethod
    def _authorize_entry(
        decision: DecisionJournalEntry[object, object],
    ) -> EntryOrderIntent:
        if not isinstance(decision, DecisionJournalEntry):
            raise TypeError("decision must be a DecisionJournalEntry")
        try:
            decision.validate()
        except (TypeError, ValueError) as exc:
            raise PairedExitReplayError("decision journal authority is invalid") from exc
        trend = TrendPullbackStrategy()
        if (
            decision.identity.strategy_id != trend.strategy_id
            or decision.identity.strategy_version != trend.strategy_version
        ):
            raise PairedExitReplayError("decision is not current Trend Pullback authority")
        authorization = EntryOrderIntentAuthorizer().authorize(decision)
        if (
            authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
            or authorization.intent is None
        ):
            raise PairedExitReplayError("decision does not authorize an entry intent")
        return authorization.intent

    @staticmethod
    def _validate_contexts(
        contexts: tuple[StrategyEvaluationContext, ...],
        decision: DecisionJournalEntry[object, object],
        entry_fill: MarketFill,
        start: datetime,
        end: datetime,
    ) -> None:
        previous: datetime | None = None
        for context in contexts:
            if not isinstance(context, StrategyEvaluationContext):
                raise TypeError("each current context must be a StrategyEvaluationContext")
            try:
                context.validate()
                _revalidate_candle(context.candle_15m)
                validate_technical_regime_snapshot(
                    context.multi_timeframe_regime.regime_15m,
                    context.features_15m,
                    context.structure_15m,
                )
                validate_multi_timeframe_regime_snapshot(context.multi_timeframe_regime)
            except (
                StrategyContextError,
                TechnicalRegimeError,
                RegimeCompositionError,
                TypeError,
                ValueError,
            ) as exc:
                raise PairedExitReplayError("current strategy context is invalid") from exc
            if (
                context.source_id != decision.identity.source_id
                or context.symbol != decision.identity.symbol
            ):
                raise PairedExitReplayError("current context and decision identity differ")
            if not start < context.observed_at < end or context.observed_at <= entry_fill.fill_at:
                raise PairedExitReplayError("current context lies outside causal replay chronology")
            if previous is not None and context.observed_at <= previous:
                raise PairedExitReplayError(
                    "current context timestamps must strictly increase without duplicates"
                )
            previous = context.observed_at

    def _run_branch(
        self,
        branch_policy: ExitReplayBranchPolicy,
        exit_strategy: PositionExitStrategy,
        decision: DecisionJournalEntry[object, object],
        intent: EntryOrderIntent,
        entry_order: MarketOrderRequest,
        entry_fill: MarketFill,
        contexts: tuple[StrategyEvaluationContext, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        start: datetime,
        end: datetime,
    ) -> HistoricalExitBranchResult:
        accounting = PositionAccountingEngine()
        try:
            entry_batch = accounting.process(
                AccountingFillBatch(entry_fill.fill_at, (deepcopy(entry_fill),))
            )
        except (PositionAccountingError, TypeError, ValueError) as exc:
            raise PairedExitReplayError("shared entry accounting failed") from exc
        evaluations: list[PositionExitEvaluation] = []
        protective_at: datetime | None = None
        precedence = False
        cause: HistoricalExitCause | None = None
        trigger_at: datetime | None = None
        exit_request: MarketOrderRequest | None = None
        exit_fill: MarketFill | None = None
        exit_unfilled: UnfilledOrder | None = None
        accounting_events = list(entry_batch.events)
        for market_context in contexts:
            position = accounting.snapshot(intent.symbol, market_context.observed_at)
            if position.quantity == 0:
                break
            exit_context = _exit_context(decision, intent, entry_fill, position, market_context)
            try:
                evaluation = exit_strategy.evaluate_exit(exit_context)
            except (PositionExitValidationError, TypeError, ValueError) as exc:
                raise PairedExitReplayError("exit policy evaluation failed closed") from exc
            evaluations.append(evaluation)
            stop_breached = _protective_stop_breached(
                exit_context.position_side,
                market_context.candle_15m,
                intent.stop_basis,
            )
            discretionary = evaluation.action is PositionExitAction.CLOSE_FULL
            if not stop_breached and not discretionary:
                continue
            if stop_breached:
                protective_at = market_context.observed_at
                cause = HistoricalExitCause.PROTECTIVE_STOP
                precedence = discretionary
            else:
                cause = HistoricalExitCause.EMA50_THESIS_FAILURE
            trigger_at = market_context.observed_at
            exit_request = MarketOrderRequest(
                order_id=_exit_order_id(decision.identity, branch_policy),
                created_at=trigger_at,
                symbol=intent.symbol,
                side=(OrderSide.SELL if intent.side is EntrySide.BUY else OrderSide.BUY),
                quantity=position.quantity.copy_abs(),
            )
            try:
                execution = self._execution.run(
                    series,
                    costs,
                    (exit_request,),
                    start=start,
                    end=end,
                )
            except (BacktestExecutionError, TypeError, ValueError) as exc:
                raise PairedExitReplayError("shared Backtest exit execution failed") from exc
            _validate_execution_result(execution.execution_series, execution.costs, series, costs)
            if execution.filled_count == 1 and execution.unfilled_count == 0:
                exit_fill = deepcopy(execution.fills[0])
                try:
                    exit_batch = accounting.process(
                        AccountingFillBatch(exit_fill.fill_at, (exit_fill,))
                    )
                except (PositionAccountingError, TypeError, ValueError) as exc:
                    raise PairedExitReplayError("shared exit accounting failed") from exc
                accounting_events.extend(exit_batch.events)
            elif execution.filled_count == 0 and execution.unfilled_count == 1:
                exit_unfilled = deepcopy(execution.unfilled_orders[0])
            else:
                raise PairedExitReplayError("exit execution returned contradictory fill counts")
            break
        final_position = accounting.snapshot(intent.symbol, end)
        closed = final_position.quantity == 0
        holds = sum(item.action is PositionExitAction.HOLD for item in evaluations)
        closes = sum(item.action is PositionExitAction.CLOSE_FULL for item in evaluations)
        return HistoricalExitBranchResult(
            branch_policy=branch_policy,
            originating_decision_identity=deepcopy(decision.identity),
            execution_series=deepcopy(series),
            execution_costs=deepcopy(costs),
            entry_order=deepcopy(entry_order),
            entry_fill=deepcopy(entry_fill),
            stop_basis=intent.stop_basis,
            exit_evaluations=tuple(deepcopy(evaluations)),
            evaluated_exit_context_count=len(evaluations),
            hold_count=holds,
            discretionary_close_signal_count=closes,
            protective_stop_breach_at=protective_at,
            protective_stop_precedence_applied=precedence,
            winning_exit_cause=cause,
            exit_trigger_at=trigger_at,
            exit_request=deepcopy(exit_request),
            exit_fill=deepcopy(exit_fill),
            exit_unfilled=deepcopy(exit_unfilled),
            accounting_events=tuple(deepcopy(accounting_events)),
            final_position=deepcopy(final_position),
            position_closed=closed,
            realized_gross_pnl=(final_position.cumulative_gross_realized_pnl if closed else None),
            total_fees=final_position.cumulative_fees if closed else None,
            realized_net_pnl=(final_position.cumulative_net_realized_pnl if closed else None),
            unresolved_open_quantity=final_position.quantity.copy_abs(),
            unresolved_average_entry_price=(None if closed else final_position.average_entry_price),
        )


def _entry_order(intent: EntryOrderIntent) -> MarketOrderRequest:
    return MarketOrderRequest(
        order_id=(
            f"paired-entry:{intent.source_id.value}:{intent.symbol}:"
            f"{intent.observed_at.isoformat()}:{intent.strategy_id}:{intent.strategy_version}"
        ),
        created_at=intent.authorized_at,
        symbol=intent.symbol,
        side=OrderSide.BUY if intent.side is EntrySide.BUY else OrderSide.SELL,
        quantity=intent.quantity,
    )


def _exit_order_id(identity: DecisionIdentity, branch: ExitReplayBranchPolicy) -> str:
    return (
        f"paired-exit:{branch.value}:{identity.source_id.value}:{identity.symbol}:"
        f"{identity.observed_at.isoformat()}:{identity.strategy_id}:{identity.strategy_version}"
    )


def _exit_context(
    decision: DecisionJournalEntry[object, object],
    intent: EntryOrderIntent,
    entry_fill: MarketFill,
    position: PositionSnapshot,
    market_context: StrategyEvaluationContext,
) -> PositionExitEvaluationContext:
    side = PositionSide.LONG if intent.side is EntrySide.BUY else PositionSide.SHORT
    return PositionExitEvaluationContext(
        position_identity=OpenPositionIdentity(decision.identity, entry_fill.order_id),
        originating_candidate_action=decision.strategy_evaluation.action,
        strategy_id=decision.identity.strategy_id,
        strategy_version=decision.identity.strategy_version,
        source_id=decision.identity.source_id,
        symbol=decision.identity.symbol,
        position_side=side,
        entry_fill_at=entry_fill.fill_at,
        entry_fill_price=entry_fill.fill_price,
        open_quantity=position.quantity.copy_abs(),
        invalidation_reference_price=intent.stop_basis,
        current_market_context=market_context,
        observed_at=market_context.observed_at,
        lifecycle_status=PositionLifecycleStatus.OPEN,
        risk_policy_version=intent.risk_policy_version,
        safety_policy_version=intent.safety_policy_version,
    )


def _protective_stop_breached(side: PositionSide, candle: Candle, stop_basis: Decimal) -> bool:
    if side is PositionSide.LONG:
        return candle.low <= stop_basis
    return candle.high >= stop_basis


def _comparison_state(
    baseline: HistoricalExitBranchResult,
    candidate: HistoricalExitBranchResult,
) -> PairedExitComparisonState:
    if baseline.position_closed and candidate.position_closed:
        return PairedExitComparisonState.BOTH_CLOSED
    if baseline.position_closed:
        return PairedExitComparisonState.BASELINE_CLOSED_CANDIDATE_OPEN
    if candidate.position_closed:
        return PairedExitComparisonState.BASELINE_OPEN_CANDIDATE_CLOSED
    return PairedExitComparisonState.BOTH_OPEN


def _revalidate_candle(candle: Candle) -> None:
    if not isinstance(candle, Candle):
        raise TypeError("current context candle must be a Candle")
    reconstructed = Candle(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )
    if reconstructed != candle:
        raise PairedExitReplayError("current context candle reconstruction changed its facts")


def _validate_execution_result(
    actual_series: ExecutionSeries,
    actual_costs: ExecutionCostConfiguration,
    expected_series: ExecutionSeries,
    expected_costs: ExecutionCostConfiguration,
) -> None:
    if actual_series != expected_series or actual_costs != expected_costs:
        raise PairedExitReplayError("Backtest execution provenance differs from request")
