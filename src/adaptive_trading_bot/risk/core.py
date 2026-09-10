"""Independent deterministic V1 pre-trade risk approval and sizing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.strategies.core import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluation,
)

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)


class RiskDecisionStatus(StrEnum):
    """Outcome of independent risk evaluation for a strategy assessment."""

    NO_ACTION = "NO_ACTION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RiskReasonCode(StrEnum):
    """Canonical supporting and rejection evidence for V1 risk decisions."""

    STRATEGY_CANDIDATE_ACCEPTED = "STRATEGY_CANDIDATE_ACCEPTED"
    CANDIDATE_GEOMETRY_VALID = "CANDIDATE_GEOMETRY_VALID"
    DAILY_LOSS_WITHIN_LIMIT = "DAILY_LOSS_WITHIN_LIMIT"
    DRAWDOWN_WITHIN_LIMIT = "DRAWDOWN_WITHIN_LIMIT"
    SYMBOL_FLAT = "SYMBOL_FLAT"
    SIMULTANEOUS_RISK_AVAILABLE = "SIMULTANEOUS_RISK_AVAILABLE"
    GROSS_EXPOSURE_AVAILABLE = "GROSS_EXPOSURE_AVAILABLE"
    RISK_SIZE_APPROVED = "RISK_SIZE_APPROVED"

    STRATEGY_NO_TRADE = "STRATEGY_NO_TRADE"
    RISK_SNAPSHOT_TIME_MISMATCH = "RISK_SNAPSHOT_TIME_MISMATCH"
    NON_USDT_V1_SYMBOL = "NON_USDT_V1_SYMBOL"
    REFERENCE_PRICE_INVALID = "REFERENCE_PRICE_INVALID"
    INVALIDATION_PRICE_INVALID = "INVALIDATION_PRICE_INVALID"
    INVALIDATION_WRONG_SIDE = "INVALIDATION_WRONG_SIDE"
    DAILY_LOSS_LIMIT_REACHED = "DAILY_LOSS_LIMIT_REACHED"
    DRAWDOWN_LIMIT_REACHED = "DRAWDOWN_LIMIT_REACHED"
    EXISTING_SYMBOL_POSITION = "EXISTING_SYMBOL_POSITION"
    SIMULTANEOUS_RISK_EXHAUSTED = "SIMULTANEOUS_RISK_EXHAUSTED"
    GROSS_EXPOSURE_EXHAUSTED = "GROSS_EXPOSURE_EXHAUSTED"
    APPROVED_QUANTITY_INVALID = "APPROVED_QUANTITY_INVALID"
    POSTCONDITION_RISK_BREACH = "POSTCONDITION_RISK_BREACH"


@dataclass(frozen=True, slots=True)
class RiskManagerConfiguration:
    """Versioned V1 controlled-testing capital and loss limits."""

    risk_per_trade_fraction: Decimal = Decimal("0.0025")
    max_simultaneous_risk_fraction: Decimal = Decimal("0.0050")
    max_daily_loss_fraction: Decimal = Decimal("0.0100")
    max_drawdown_fraction: Decimal = Decimal("0.0500")
    max_gross_exposure_fraction: Decimal = Decimal("1.0000")
    risk_policy_version: str = field(init=False, default="1.0.0")

    def __post_init__(self) -> None:
        for name, value in (
            ("risk_per_trade_fraction", self.risk_per_trade_fraction),
            ("max_simultaneous_risk_fraction", self.max_simultaneous_risk_fraction),
            ("max_daily_loss_fraction", self.max_daily_loss_fraction),
            ("max_drawdown_fraction", self.max_drawdown_fraction),
            ("max_gross_exposure_fraction", self.max_gross_exposure_fraction),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.risk_per_trade_fraction <= 0:
            raise ValueError("risk_per_trade_fraction must be positive")
        if self.max_simultaneous_risk_fraction <= 0:
            raise ValueError("max_simultaneous_risk_fraction must be positive")
        if self.risk_per_trade_fraction > self.max_simultaneous_risk_fraction:
            raise ValueError("risk_per_trade_fraction cannot exceed simultaneous risk")
        for name, value in (
            ("max_daily_loss_fraction", self.max_daily_loss_fraction),
            ("max_drawdown_fraction", self.max_drawdown_fraction),
        ):
            if value <= 0 or value >= 1:
                raise ValueError(f"{name} must be strictly between zero and one")
        if self.max_gross_exposure_fraction <= 0 or self.max_gross_exposure_fraction > 1:
            raise ValueError("max_gross_exposure_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class RiskPositionExposure:
    """Already-observed signed position and its supplied economic risk facts."""

    symbol: TradingSymbol
    quantity: Decimal
    gross_exposure_quote: Decimal
    open_risk_quote: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, TradingSymbol):
            raise TypeError("position symbol must be a TradingSymbol")
        for name, value in (
            ("quantity", self.quantity),
            ("gross_exposure_quote", self.gross_exposure_quote),
            ("open_risk_quote", self.open_risk_quote),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"position {name} must be a Decimal")
            if not value.is_finite():
                raise ValueError(f"position {name} must be finite")
        if self.quantity == 0:
            raise ValueError("position quantity must be nonzero")
        if self.gross_exposure_quote < 0 or self.open_risk_quote < 0:
            raise ValueError("position gross exposure and open risk must be non-negative")


@dataclass(frozen=True, slots=True)
class RiskPortfolioSnapshot:
    """Immutable marked account and portfolio facts observable at one instant."""

    observed_at: datetime
    quote_currency: str
    account_equity_quote: Decimal
    day_start_equity_quote: Decimal
    peak_equity_quote: Decimal
    positions: tuple[RiskPositionExposure, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            require_utc(self.observed_at, field="risk portfolio observed_at"),
        )
        if self.quote_currency != "USDT":
            raise ValueError("V1 risk portfolio quote_currency must be USDT")
        if not isinstance(self.positions, tuple):
            raise TypeError("positions must be an immutable tuple")
        for name, value in (
            ("account_equity_quote", self.account_equity_quote),
            ("day_start_equity_quote", self.day_start_equity_quote),
            ("peak_equity_quote", self.peak_equity_quote),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.peak_equity_quote < self.account_equity_quote:
            raise ValueError("peak equity cannot be below account equity")
        if self.peak_equity_quote < self.day_start_equity_quote:
            raise ValueError("peak equity cannot be below day-start equity")
        seen: set[TradingSymbol] = set()
        for position in self.positions:
            if not isinstance(position, RiskPositionExposure):
                raise TypeError("positions must contain RiskPositionExposure values")
            if position.symbol.quote != "USDT":
                raise ValueError("V1 position symbols must quote in USDT")
            if position.symbol in seen:
                raise ValueError("duplicate position symbol exposure")
            seen.add(position.symbol)
        object.__setattr__(
            self, "positions", tuple(sorted(self.positions, key=lambda item: str(item.symbol)))
        )

    @property
    def current_gross_exposure_quote(self) -> Decimal:
        """Derive aggregate gross exposure without trusting a caller total."""

        with localcontext(_DECIMAL_CONTEXT):
            return sum((item.gross_exposure_quote for item in self.positions), _ZERO)

    @property
    def current_open_risk_quote(self) -> Decimal:
        """Derive aggregate open risk without trusting a caller total."""

        with localcontext(_DECIMAL_CONTEXT):
            return sum((item.open_risk_quote for item in self.positions), _ZERO)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Immutable auditable risk result; it is not an order or execution command."""

    strategy_id: str
    strategy_version: str
    risk_policy_version: str
    source_id: SourceIdentity
    symbol: TradingSymbol
    observed_at: datetime
    risk_snapshot_observed_at: datetime
    quote_currency: str
    strategy_candidate_action: CandidateAction
    status: RiskDecisionStatus
    reasons: tuple[RiskReasonCode, ...]
    reference_price: Decimal | None
    approved_risk_stop_basis: Decimal | None
    stop_distance: Decimal | None
    account_equity_quote: Decimal
    day_start_equity_quote: Decimal
    peak_equity_quote: Decimal
    daily_loss_quote: Decimal
    daily_loss_fraction: Decimal
    max_daily_loss_fraction: Decimal
    drawdown_quote: Decimal
    drawdown_fraction: Decimal
    max_drawdown_fraction: Decimal
    current_open_risk_quote: Decimal
    simultaneous_risk_cap_quote: Decimal
    remaining_simultaneous_risk_quote: Decimal
    current_gross_exposure_quote: Decimal
    gross_exposure_cap_quote: Decimal
    remaining_gross_exposure_quote: Decimal
    per_trade_risk_budget_quote: Decimal
    approved_quantity: Decimal | None
    approved_notional_quote: Decimal | None
    approved_risk_quote: Decimal | None
    resulting_open_risk_quote: Decimal | None
    resulting_gross_exposure_quote: Decimal | None
    requires_halt_review: bool


@dataclass(frozen=True, slots=True)
class _PortfolioMetrics:
    daily_loss_quote: Decimal
    daily_loss_fraction: Decimal
    drawdown_quote: Decimal
    drawdown_fraction: Decimal
    current_open_risk_quote: Decimal
    simultaneous_risk_cap_quote: Decimal
    remaining_simultaneous_risk_quote: Decimal
    current_gross_exposure_quote: Decimal
    gross_exposure_cap_quote: Decimal
    remaining_gross_exposure_quote: Decimal
    per_trade_risk_budget_quote: Decimal


@dataclass(frozen=True, slots=True)
class RiskManager:
    """Apply sovereign V1 entry-risk gates and deterministic risk-based sizing."""

    configuration: RiskManagerConfiguration = field(default_factory=RiskManagerConfiguration)

    def evaluate[ReasonT, AuditT](
        self,
        strategy_evaluation: StrategyEvaluation[ReasonT, AuditT],
        risk_portfolio_snapshot: RiskPortfolioSnapshot,
    ) -> RiskDecision:
        """Evaluate one generic strategy assessment against explicit account facts."""

        with localcontext(_DECIMAL_CONTEXT):
            metrics = self._metrics(risk_portfolio_snapshot)
            requires_halt = metrics.drawdown_fraction >= self.configuration.max_drawdown_fraction
            if strategy_evaluation.action is CandidateAction.NO_TRADE:
                return self._decision(
                    strategy_evaluation,
                    risk_portfolio_snapshot,
                    metrics,
                    RiskDecisionStatus.NO_ACTION,
                    (RiskReasonCode.STRATEGY_NO_TRADE,),
                    requires_halt_review=requires_halt,
                )

            reference = strategy_evaluation.reference_price
            invalidation = strategy_evaluation.invalidation_reference_price
            failures: list[RiskReasonCode] = []
            if risk_portfolio_snapshot.observed_at != strategy_evaluation.observed_at:
                failures.append(RiskReasonCode.RISK_SNAPSHOT_TIME_MISMATCH)
            if strategy_evaluation.symbol.quote != "USDT":
                failures.append(RiskReasonCode.NON_USDT_V1_SYMBOL)
            if not _is_positive_decimal(reference):
                failures.append(RiskReasonCode.REFERENCE_PRICE_INVALID)
            if not _is_positive_decimal(invalidation):
                failures.append(RiskReasonCode.INVALIDATION_PRICE_INVALID)

            stop_distance = self._stop_distance(strategy_evaluation.action, reference, invalidation)
            if stop_distance is not None and stop_distance <= 0:
                failures.append(RiskReasonCode.INVALIDATION_WRONG_SIDE)
            if metrics.daily_loss_fraction >= self.configuration.max_daily_loss_fraction:
                failures.append(RiskReasonCode.DAILY_LOSS_LIMIT_REACHED)
            if metrics.drawdown_fraction >= self.configuration.max_drawdown_fraction:
                failures.append(RiskReasonCode.DRAWDOWN_LIMIT_REACHED)
            if any(
                position.symbol == strategy_evaluation.symbol
                for position in risk_portfolio_snapshot.positions
            ):
                failures.append(RiskReasonCode.EXISTING_SYMBOL_POSITION)
            if metrics.remaining_simultaneous_risk_quote <= 0:
                failures.append(RiskReasonCode.SIMULTANEOUS_RISK_EXHAUSTED)
            if metrics.remaining_gross_exposure_quote <= 0:
                failures.append(RiskReasonCode.GROSS_EXPOSURE_EXHAUSTED)
            if failures:
                return self._decision(
                    strategy_evaluation,
                    risk_portfolio_snapshot,
                    metrics,
                    RiskDecisionStatus.REJECTED,
                    tuple(failures),
                    reference_price=reference if _is_positive_decimal(reference) else None,
                    stop_basis=invalidation if _is_positive_decimal(invalidation) else None,
                    stop_distance=stop_distance,
                    requires_halt_review=requires_halt,
                )

            assert isinstance(reference, Decimal)
            assert isinstance(invalidation, Decimal)
            assert stop_distance is not None and stop_distance > 0
            available_risk = min(
                metrics.per_trade_risk_budget_quote,
                metrics.remaining_simultaneous_risk_quote,
            )
            quantity_by_risk = available_risk / stop_distance
            quantity_by_exposure = metrics.remaining_gross_exposure_quote / reference
            approved_quantity = min(quantity_by_risk, quantity_by_exposure)
            if not approved_quantity.is_finite() or approved_quantity <= 0:
                return self._decision(
                    strategy_evaluation,
                    risk_portfolio_snapshot,
                    metrics,
                    RiskDecisionStatus.REJECTED,
                    (RiskReasonCode.APPROVED_QUANTITY_INVALID,),
                    reference_price=reference,
                    stop_basis=invalidation,
                    stop_distance=stop_distance,
                    requires_halt_review=requires_halt,
                )

            approved_notional = approved_quantity * reference
            approved_risk = approved_quantity * stop_distance
            resulting_risk = metrics.current_open_risk_quote + approved_risk
            resulting_exposure = metrics.current_gross_exposure_quote + approved_notional
            if (
                not all(
                    value.is_finite()
                    for value in (
                        approved_notional,
                        approved_risk,
                        resulting_risk,
                        resulting_exposure,
                    )
                )
                or approved_risk > metrics.per_trade_risk_budget_quote
                or resulting_risk > metrics.simultaneous_risk_cap_quote
                or resulting_exposure > metrics.gross_exposure_cap_quote
            ):
                return self._decision(
                    strategy_evaluation,
                    risk_portfolio_snapshot,
                    metrics,
                    RiskDecisionStatus.REJECTED,
                    (RiskReasonCode.POSTCONDITION_RISK_BREACH,),
                    reference_price=reference,
                    stop_basis=invalidation,
                    stop_distance=stop_distance,
                    requires_halt_review=requires_halt,
                )
            return self._decision(
                strategy_evaluation,
                risk_portfolio_snapshot,
                metrics,
                RiskDecisionStatus.APPROVED,
                (
                    RiskReasonCode.STRATEGY_CANDIDATE_ACCEPTED,
                    RiskReasonCode.CANDIDATE_GEOMETRY_VALID,
                    RiskReasonCode.DAILY_LOSS_WITHIN_LIMIT,
                    RiskReasonCode.DRAWDOWN_WITHIN_LIMIT,
                    RiskReasonCode.SYMBOL_FLAT,
                    RiskReasonCode.SIMULTANEOUS_RISK_AVAILABLE,
                    RiskReasonCode.GROSS_EXPOSURE_AVAILABLE,
                    RiskReasonCode.RISK_SIZE_APPROVED,
                ),
                reference_price=reference,
                stop_basis=invalidation,
                stop_distance=stop_distance,
                approved_quantity=approved_quantity,
                approved_notional=approved_notional,
                approved_risk=approved_risk,
                resulting_risk=resulting_risk,
                resulting_exposure=resulting_exposure,
                requires_halt_review=requires_halt,
            )

    def _metrics(self, snapshot: RiskPortfolioSnapshot) -> _PortfolioMetrics:
        equity = snapshot.account_equity_quote
        daily_loss = max(_ZERO, snapshot.day_start_equity_quote - equity)
        drawdown = max(_ZERO, snapshot.peak_equity_quote - equity)
        current_risk = snapshot.current_open_risk_quote
        risk_cap = equity * self.configuration.max_simultaneous_risk_fraction
        current_exposure = snapshot.current_gross_exposure_quote
        exposure_cap = equity * self.configuration.max_gross_exposure_fraction
        return _PortfolioMetrics(
            daily_loss,
            daily_loss / snapshot.day_start_equity_quote,
            drawdown,
            drawdown / snapshot.peak_equity_quote,
            current_risk,
            risk_cap,
            risk_cap - current_risk,
            current_exposure,
            exposure_cap,
            exposure_cap - current_exposure,
            equity * self.configuration.risk_per_trade_fraction,
        )

    @staticmethod
    def _stop_distance(
        action: CandidateAction,
        reference: Decimal | None,
        invalidation: Decimal | None,
    ) -> Decimal | None:
        if not _is_positive_decimal(reference) or not _is_positive_decimal(invalidation):
            return None
        assert isinstance(reference, Decimal) and isinstance(invalidation, Decimal)
        if action is CandidateAction.LONG_CANDIDATE:
            return reference - invalidation
        if action is CandidateAction.SHORT_CANDIDATE:
            return invalidation - reference
        return None

    def _decision[ReasonT, AuditT](
        self,
        evaluation: StrategyEvaluation[ReasonT, AuditT],
        snapshot: RiskPortfolioSnapshot,
        metrics: _PortfolioMetrics,
        status: RiskDecisionStatus,
        reasons: tuple[RiskReasonCode, ...],
        *,
        reference_price: Decimal | None = None,
        stop_basis: Decimal | None = None,
        stop_distance: Decimal | None = None,
        approved_quantity: Decimal | None = None,
        approved_notional: Decimal | None = None,
        approved_risk: Decimal | None = None,
        resulting_risk: Decimal | None = None,
        resulting_exposure: Decimal | None = None,
        requires_halt_review: bool,
    ) -> RiskDecision:
        return RiskDecision(
            strategy_id=evaluation.strategy_id,
            strategy_version=evaluation.strategy_version,
            risk_policy_version=self.configuration.risk_policy_version,
            source_id=evaluation.source_id,
            symbol=evaluation.symbol,
            observed_at=evaluation.observed_at,
            risk_snapshot_observed_at=snapshot.observed_at,
            quote_currency=snapshot.quote_currency,
            strategy_candidate_action=evaluation.action,
            status=status,
            reasons=reasons,
            reference_price=reference_price,
            approved_risk_stop_basis=stop_basis,
            stop_distance=stop_distance,
            account_equity_quote=snapshot.account_equity_quote,
            day_start_equity_quote=snapshot.day_start_equity_quote,
            peak_equity_quote=snapshot.peak_equity_quote,
            daily_loss_quote=metrics.daily_loss_quote,
            daily_loss_fraction=metrics.daily_loss_fraction,
            max_daily_loss_fraction=self.configuration.max_daily_loss_fraction,
            drawdown_quote=metrics.drawdown_quote,
            drawdown_fraction=metrics.drawdown_fraction,
            max_drawdown_fraction=self.configuration.max_drawdown_fraction,
            current_open_risk_quote=metrics.current_open_risk_quote,
            simultaneous_risk_cap_quote=metrics.simultaneous_risk_cap_quote,
            remaining_simultaneous_risk_quote=metrics.remaining_simultaneous_risk_quote,
            current_gross_exposure_quote=metrics.current_gross_exposure_quote,
            gross_exposure_cap_quote=metrics.gross_exposure_cap_quote,
            remaining_gross_exposure_quote=metrics.remaining_gross_exposure_quote,
            per_trade_risk_budget_quote=metrics.per_trade_risk_budget_quote,
            approved_quantity=approved_quantity,
            approved_notional_quote=approved_notional,
            approved_risk_quote=approved_risk,
            resulting_open_risk_quote=resulting_risk,
            resulting_gross_exposure_quote=resulting_exposure,
            requires_halt_review=requires_halt_review,
        )


def _is_positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
