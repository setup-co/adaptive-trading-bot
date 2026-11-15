"""Opt-in synthetic-only implementation of BTC/ETH Grid protocol v0.2.0.

The module has no dataset loader, exchange adapter, registry hook, or runtime entry point.
It consumes caller-supplied synthetic observations and fails closed on missing authority.
"""

from __future__ import annotations

import json
from copy import copy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    localcontext,
)
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from pathlib import Path

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFill,
    PositionAccountingFillBatch,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders.intent import OrderIntentAuthorizationStatus
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    OperationalSafetyConfiguration,
    OperationalSafetyDecision,
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecision,
    RiskDecisionStatus,
    RiskDrawdownEnforcement,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies.core import CandidateAction, StrategyEvaluation

_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)
_ONE = Decimal(1)
_PRICE_QUANTUM = Decimal("0.01")
_QUANTITY_QUANTUM = Decimal("0.000001")
_MINUTE = timedelta(minutes=1)

GRID_STRATEGY_ID = "BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1"
GRID_PROTOCOL_VERSION = "0.2.0-proposed"
GRID_ENGINE_VERSION = "0.1.0-synthetic-only"
GRID_ACCEPTED_PROTOCOL_HEAD = "58f250a86925a48ed338ef716a621a6594e768b9"
GRID_PROTOCOL_DOCUMENT_SHA256 = "f0314ed243a2f7784e13d252901802c95b994b59221a2aa67333733be86907fd"
GRID_PARAMETERS_SHA256 = "960edccc1c40ed63a780734921494646adb45f5532df8ce7872e502dc94147df"


class GridV1Error(ValueError):
    """A supplied synthetic input violates the frozen protocol."""


class _GridPreOrderRejection(GridV1Error):
    def __init__(self, reason: GridNoTradeReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class GridScenario(StrEnum):
    BASE = "BASE"
    STRESS = "STRESS"


class GridState(StrEnum):
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    TERMINATION_PENDING = "TERMINATION_PENDING"
    TERMINATED = "TERMINATED"
    INTEGRITY_BLOCKED = "INTEGRITY_BLOCKED"


class GridCompletionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL_UNRESOLVED = "PARTIAL_UNRESOLVED"
    INTEGRITY_BLOCKED = "INTEGRITY_BLOCKED"


class GridNoTradeReason(StrEnum):
    CORRIDOR_EXCEEDS_MAXIMUM = "CORRIDOR_EXCEEDS_MAXIMUM"
    ROUNDED_LEVELS_INVALID = "ROUNDED_LEVELS_INVALID"
    ADJACENT_RETURN_BELOW_MINIMUM = "ADJACENT_RETURN_BELOW_MINIMUM"
    ORDER_SIZE_BELOW_MINIMUM = "ORDER_SIZE_BELOW_MINIMUM"


class GridWeekDisposition(StrEnum):
    GRID_ACTIVE = "GRID_ACTIVE"
    NO_TRADE_CASH_ONLY = "NO_TRADE_CASH_ONLY"
    TERMINATED_CASH_ONLY = "TERMINATED_CASH_ONLY"


class GridSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class GridOrderKind(StrEnum):
    ENTRY = "ENTRY"
    OWNED_EXIT = "OWNED_EXIT"


@dataclass(frozen=True, slots=True)
class GridScenarioCosts:
    passive_fee_rate: Decimal
    forced_fee_bps: Decimal
    forced_slippage_bps: Decimal
    adverse_funding_addon: Decimal


_COSTS = {
    GridScenario.BASE: GridScenarioCosts(Decimal("0.0002"), Decimal(8), Decimal(5), Decimal(0)),
    GridScenario.STRESS: GridScenarioCosts(
        Decimal("0.0008"), Decimal(8), Decimal(15), Decimal("0.0001")
    ),
}


@dataclass(frozen=True, slots=True)
class GridContractIdentity:
    accepted_protocol_head: str
    protocol_document_sha256: str
    parameters_sha256: str
    runtime_implementation_sha256: str


def grid_runtime_implementation_sha256() -> str:
    """Hash the implementation bytes actually imported by this process."""

    return sha256(Path(__file__).read_bytes()).hexdigest()


def verified_grid_contract_identity() -> GridContractIdentity:
    """Return the exact accepted protocol binding plus observed implementation bytes."""

    return GridContractIdentity(
        GRID_ACCEPTED_PROTOCOL_HEAD,
        GRID_PROTOCOL_DOCUMENT_SHA256,
        GRID_PARAMETERS_SHA256,
        grid_runtime_implementation_sha256(),
    )


@dataclass(frozen=True, slots=True)
class GridLedgerCarry:
    symbol: TradingSymbol
    original_account_capital: Decimal
    current_cash: Decimal
    current_equity: Decimal
    cumulative_gross_realized_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_funding: Decimal
    peak_equity: Decimal
    terminated: bool
    previous_episode_start: datetime | None

    def __post_init__(self) -> None:
        if self.symbol not in {TradingSymbol("BTC", "USDT"), TradingSymbol("ETH", "USDT")}:
            raise GridV1Error("Grid carry supports only BTC/USDT and ETH/USDT")
        if self.original_account_capital != Decimal("10000"):
            raise GridV1Error("original account capital is immutable at 10000 USDT")
        values = (
            self.current_cash,
            self.current_equity,
            self.cumulative_gross_realized_pnl,
            self.cumulative_fees,
            self.cumulative_funding,
            self.peak_equity,
        )
        if any(not value.is_finite() for value in values):
            raise GridV1Error("carry economics must be finite")
        if (
            self.current_cash <= 0
            or self.current_equity != self.current_cash
            or self.peak_equity < self.current_equity
        ):
            raise GridV1Error("carry cash/peak equity is invalid")
        expected_cash = (
            self.original_account_capital
            + self.cumulative_gross_realized_pnl
            - self.cumulative_fees
            + self.cumulative_funding
        )
        if self.current_cash != expected_cash:
            raise GridV1Error("carry economics do not reconcile to current cash")
        if (
            not self.terminated
            and self.current_equity <= self.original_account_capital * Decimal("0.80")
        ):
            raise GridV1Error("carry at the original-capital floor must be permanently terminated")
        if self.previous_episode_start is not None:
            object.__setattr__(
                self,
                "previous_episode_start",
                require_utc(self.previous_episode_start, field="previous_episode_start"),
            )

    @classmethod
    def initial(cls, symbol: TradingSymbol) -> GridLedgerCarry:
        return cls(
            symbol,
            Decimal("10000"),
            Decimal("10000"),
            Decimal("10000"),
            _ZERO,
            _ZERO,
            _ZERO,
            Decimal("10000"),
            False,
            None,
        )


@dataclass(frozen=True, slots=True)
class GridEpisodeInput:
    source_id: SourceId
    symbol: TradingSymbol
    episode_start: datetime
    completed_4h_closes: tuple[Decimal, ...]
    code_sha: str
    contract_identity: GridContractIdentity
    carry_in: GridLedgerCarry

    def __post_init__(self) -> None:
        start = require_utc(self.episode_start, field="episode_start")
        object.__setattr__(self, "episode_start", start)
        if self.symbol not in {TradingSymbol("BTC", "USDT"), TradingSymbol("ETH", "USDT")}:
            raise GridV1Error("Grid V1 supports only BTC/USDT and ETH/USDT")
        if start.weekday() != 0 or start.time() != datetime.min.time():
            raise GridV1Error("episode_start must be Monday 00:00:00 UTC")
        if len(self.completed_4h_closes) != 42:
            raise GridV1Error("exactly 42 completed 4h closes are required")
        if any(not value.is_finite() or value <= 0 for value in self.completed_4h_closes):
            raise GridV1Error("4h closes must be finite and positive")
        if not self.code_sha or self.code_sha.strip() != self.code_sha:
            raise GridV1Error("code_sha must be canonical non-empty text")
        if self.carry_in.symbol != self.symbol:
            raise GridV1Error("carry symbol does not match episode symbol")
        if self.carry_in.previous_episode_start is None:
            if self.carry_in != GridLedgerCarry.initial(self.symbol):
                raise GridV1Error("first episode must use the exact initial carry")
        elif self.episode_start != self.carry_in.previous_episode_start + timedelta(days=7):
            raise GridV1Error("carry must advance by exactly one weekly episode")


@dataclass(frozen=True, slots=True)
class GridGeometry:
    centre: Decimal
    log_half_width: Decimal
    spacing_ratio: Decimal
    buys: tuple[Decimal, ...]
    sells: tuple[Decimal, ...]
    lower_boundary: Decimal
    upper_boundary: Decimal
    quantity_per_rung: Decimal
    modeled_boundary_loss: Decimal
    aggregate_pending_notional: Decimal


@dataclass(frozen=True, slots=True)
class GridFundingEvent:
    event_id: str
    observed_at: datetime
    rate: Decimal
    mark_observed_at: datetime | None
    mark_price: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_utc(self.observed_at))
        if self.mark_observed_at is not None:
            object.__setattr__(self, "mark_observed_at", require_utc(self.mark_observed_at))


@dataclass(frozen=True, slots=True)
class GridMinute:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    mark_observed_at: datetime | None
    mark_price: Decimal | None
    funding_events: tuple[GridFundingEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", require_utc(self.start))
        if self.start.second or self.start.microsecond:
            raise GridV1Error("minute start must be aligned")
        for value in (self.open, self.high, self.low, self.close):
            if not value.is_finite() or value <= 0:
                raise GridV1Error("minute OHLC must be finite and positive")
        if self.high < max(self.open, self.low, self.close):
            raise GridV1Error("minute high is inconsistent")
        if self.low > min(self.open, self.high, self.close):
            raise GridV1Error("minute low is inconsistent")
        if self.mark_observed_at is not None:
            object.__setattr__(self, "mark_observed_at", require_utc(self.mark_observed_at))
        if not isinstance(self.funding_events, tuple):
            raise TypeError("funding_events must be a tuple")


@dataclass(frozen=True, slots=True)
class GridAuditEvent:
    sequence: int
    observed_at: datetime
    event_type: str
    order_id: str | None
    lot_id: str | None
    details: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _GridOrder:
    order_id: str
    kind: GridOrderKind
    side: GridSide
    price: Decimal
    rung: int
    due_at: datetime
    active: bool = False
    owner_lot_id: str | None = None


@dataclass(frozen=True, slots=True)
class _GridLot:
    lot_id: str
    side: GridSide
    opened_at: datetime
    entry_price: Decimal
    source_order_id: str


@dataclass(frozen=True, slots=True)
class GridPlanAuthority:
    decision_identity: DecisionIdentity
    directional_risk_decisions: tuple[RiskDecision, RiskDecision]
    directional_safety_decisions: tuple[OperationalSafetyDecision, OperationalSafetyDecision]
    authorization_status: OrderIntentAuthorizationStatus
    approved_modeled_loss: Decimal
    approved_pending_notional: Decimal


@dataclass(frozen=True, slots=True)
class GridLedgerSnapshot:
    state: GridState
    cash: Decimal
    position_quantity: Decimal
    average_entry_price: Decimal | None
    cumulative_gross_realized_pnl: Decimal
    cumulative_fees: Decimal
    cumulative_funding: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    peak_equity: Decimal
    running_peak_drawdown_fraction: Decimal
    active_order_count: int
    lot_count: int


@dataclass(frozen=True, slots=True)
class GridEpisodeReport:
    schema: str
    strategy_id: str
    protocol_version: str
    engine_version: str
    supplied_code_sha: str
    verified_contract: dict[str, str]
    source_id: str
    symbol: str
    scenario: str
    episode_start: str
    completion_status: str
    week_disposition: str
    no_trade_reason: str | None
    carry_in: dict[str, object]
    carry_out: dict[str, object] | None
    input_contract: dict[str, object]
    geometry: dict[str, object]
    authority: dict[str, object]
    snapshot: dict[str, object]
    audit: tuple[dict[str, object], ...]

    def to_json_bytes(self) -> bytes:
        return (json.dumps(asdict(self), sort_keys=True, separators=(",", ":")) + "\n").encode()

    @property
    def sha256(self) -> str:
        return sha256(self.to_json_bytes()).hexdigest()


def healthy_grid_health(
    episode: GridEpisodeInput,
) -> OperationalHealthSnapshot:
    """Return an explicit synthetic healthy snapshot; tests may supply blockers."""

    return OperationalHealthSnapshot(
        source_id=episode.source_id,
        symbol=episode.symbol,
        observed_at=episode.episode_start,
        market_data_observed_at=episode.episode_start,
        feed_integrity_state=FeedIntegrityState.OK,
        exchange_api_health_state=ExchangeApiHealthState.HEALTHY,
        spread_bps=Decimal(0),
        time_sync_offset_ms=Decimal(0),
        volatility_safety_state=VolatilitySafetyState.CLASSIFIED_NORMAL,
        suspicious_price_anomaly=False,
        consecutive_order_rejections=0,
    )


def _geometry(closes: tuple[Decimal, ...], equity: Decimal) -> GridGeometry:
    with localcontext(_CONTEXT):
        returns = tuple((closes[index] / closes[index - 1]).ln() for index in range(1, 42))
        mean = sum(returns, _ZERO) / Decimal(41)
        variance = sum(((value - mean) ** 2 for value in returns), _ZERO) / Decimal(41)
        sigma = variance.sqrt()
        width = max(Decimal("1.06").ln(), Decimal("2.5") * sigma * Decimal(42).sqrt())
        if width > Decimal("1.35").ln():
            raise _GridPreOrderRejection(GridNoTradeReason.CORRIDOR_EXCEEDS_MAXIMUM)
        ratio = (width / Decimal(6)).exp()
        centre = closes[-1]
        buys = tuple(
            (centre / (ratio**index)).quantize(_PRICE_QUANTUM, rounding=ROUND_FLOOR)
            for index in range(1, 7)
        )
        sells = tuple(
            (centre * (ratio**index)).quantize(_PRICE_QUANTUM, rounding=ROUND_CEILING)
            for index in range(1, 7)
        )
        lower = centre / (ratio**7)
        upper = centre * (ratio**7)
        if (
            len(set((*buys, *sells))) != 12
            or tuple(sorted(buys, reverse=True)) != buys
            or tuple(sorted(sells)) != sells
        ):
            raise _GridPreOrderRejection(GridNoTradeReason.ROUNDED_LEVELS_INVALID)
        adjacent = tuple(buys[index - 1] / buys[index] - _ONE for index in range(1, 6)) + tuple(
            sells[index] / sells[index - 1] - _ONE for index in range(1, 6)
        )
        if any(value < Decimal("0.004") for value in adjacent):
            raise _GridPreOrderRejection(GridNoTradeReason.ADJACENT_RETURN_BELOW_MINIMUM)
        x_lower = lower * Decimal("0.9985")
        x_upper = upper * Decimal("1.0015")
        reserve = Decimal("0.003")
        d_long = sum(
            (
                price - x_lower + Decimal("0.0008") * price + Decimal("0.0008") * x_lower
                for price in buys
            ),
            _ZERO,
        ) + reserve * sum(buys, _ZERO)
        d_short = sum(
            (
                x_upper - price + Decimal("0.0008") * price + Decimal("0.0008") * x_upper
                for price in sells
            ),
            _ZERO,
        ) + reserve * sum(sells, _ZERO)
        q_risk = equity * Decimal("0.01") / max(d_long, d_short)
        all_prices = sum((*buys, *sells), _ZERO)
        q_notional = equity * Decimal("0.80") / all_prices
        quantity = min(q_risk, q_notional).quantize(_QUANTITY_QUANTUM, rounding=ROUND_FLOOR)
        if quantity <= 0 or any(quantity * price < Decimal(5) for price in (*buys, *sells)):
            raise _GridPreOrderRejection(GridNoTradeReason.ORDER_SIZE_BELOW_MINIMUM)
        return GridGeometry(
            centre,
            width,
            ratio,
            buys,
            sells,
            lower,
            upper,
            quantity,
            quantity * max(d_long, d_short),
            quantity * all_prices,
        )


def _cash_only_geometry(centre: Decimal) -> GridGeometry:
    return GridGeometry(
        centre,
        _ZERO,
        _ONE,
        (),
        (),
        centre,
        centre,
        _ZERO,
        _ZERO,
        _ZERO,
    )


def _authorize_plan(
    episode: GridEpisodeInput,
    geometry: GridGeometry,
    health: OperationalHealthSnapshot,
) -> GridPlanAuthority:
    """Authorize both directional envelopes; never disguise the ladder as one order."""

    evaluations = tuple(
        StrategyEvaluation[str, tuple[str, ...]](
            strategy_id=GRID_STRATEGY_ID,
            strategy_version=GRID_PROTOCOL_VERSION,
            source_id=episode.source_id,
            symbol=episode.symbol,
            observed_at=episode.episode_start,
            action=action,
            reference_price=geometry.centre,
            invalidation_reference_price=invalidation,
            reasons_for=("SYNTHETIC_GRID_DIRECTIONAL_ENVELOPE",),
            reasons_against=(),
            rule_audit=("BATCH_STANDING_LIMIT_ADAPTER", label),
        )
        for action, invalidation, label in (
            (CandidateAction.LONG_CANDIDATE, geometry.lower_boundary, "LONG_ENVELOPE"),
            (CandidateAction.SHORT_CANDIDATE, geometry.upper_boundary, "SHORT_ENVELOPE"),
        )
    )
    risk_manager = RiskManager(
        RiskManagerConfiguration(
            risk_per_trade_fraction=Decimal("0.01"),
            max_simultaneous_risk_fraction=Decimal("0.01"),
            max_daily_loss_fraction=Decimal("0.99"),
            max_drawdown_fraction=Decimal("0.20"),
            max_gross_exposure_fraction=Decimal("0.80"),
            drawdown_enforcement=RiskDrawdownEnforcement.REPORT_ONLY,
        )
    )
    portfolio = RiskPortfolioSnapshot(
        observed_at=episode.episode_start,
        quote_currency="USDT",
        account_equity_quote=episode.carry_in.current_equity,
        day_start_equity_quote=episode.carry_in.current_equity,
        peak_equity_quote=episode.carry_in.peak_equity,
    )
    risks = tuple(risk_manager.evaluate(evaluation, portfolio) for evaluation in evaluations)
    safety_gate = OperationalSafetyGate(OperationalSafetyConfiguration())
    safeties = tuple(safety_gate.evaluate(risk, health) for risk in risks)
    approved = (
        all(risk.status is RiskDecisionStatus.APPROVED for risk in risks)
        and all(safety.status is OperationalSafetyStatus.CLEARED for safety in safeties)
        and geometry.modeled_boundary_loss <= episode.carry_in.current_equity * Decimal("0.01")
        and geometry.aggregate_pending_notional <= episode.carry_in.current_equity * Decimal("0.80")
    )
    identity = DecisionIdentity(
        episode.source_id,
        episode.symbol,
        episode.episode_start,
        GRID_STRATEGY_ID,
        GRID_PROTOCOL_VERSION,
    )
    return GridPlanAuthority(
        identity,
        (risks[0], risks[1]),
        (safeties[0], safeties[1]),
        (
            OrderIntentAuthorizationStatus.AUTHORIZED
            if approved
            else OrderIntentAuthorizationStatus.REJECTED_INCONSISTENT
        ),
        geometry.modeled_boundary_loss,
        geometry.aggregate_pending_notional,
    )


class GridV1Engine:
    """Stateful deterministic Grid engine for caller-supplied synthetic facts only."""

    def __init__(
        self,
        episode: GridEpisodeInput,
        scenario: GridScenario,
        health: OperationalHealthSnapshot,
        *,
        synthetic_geometry: GridGeometry | None = None,
    ) -> None:
        self.episode = episode
        self.scenario = scenario
        self.costs = _COSTS[scenario]
        if not episode.source_id.value.startswith("synthetic-"):
            raise GridV1Error("Grid V1 engine accepts only explicit synthetic source identities")
        expected_contract = verified_grid_contract_identity()
        if episode.contract_identity != expected_contract:
            raise GridV1Error(
                "Grid contract identity does not match verified runtime/protocol bytes"
            )
        self.contract_identity = expected_contract
        self.no_trade_reason: GridNoTradeReason | None = None
        if episode.carry_in.terminated:
            self.geometry = _cash_only_geometry(episode.completed_4h_closes[-1])
            self.authority = None
            self.state = GridState.TERMINATED
            self.week_disposition = GridWeekDisposition.TERMINATED_CASH_ONLY
        else:
            try:
                self.geometry = synthetic_geometry or _geometry(
                    episode.completed_4h_closes, episode.carry_in.current_equity
                )
            except _GridPreOrderRejection as rejection:
                self.geometry = _cash_only_geometry(episode.completed_4h_closes[-1])
                self.authority = None
                self.state = GridState.STOPPED
                self.no_trade_reason = rejection.reason
                self.week_disposition = GridWeekDisposition.NO_TRADE_CASH_ONLY
            else:
                self.authority = _authorize_plan(episode, self.geometry, health)
                self.state = GridState.ACTIVE
                self.week_disposition = GridWeekDisposition.GRID_ACTIVE
        if self.authority is not None and (
            self.authority.authorization_status is not OrderIntentAuthorizationStatus.AUTHORIZED
        ):
            raise GridV1Error("Risk/Safety/batch authorization rejected the Grid plan")
        self.accounting = PositionAccountingCore()
        self.funding_cash = _ZERO
        self.peak_equity = episode.carry_in.peak_equity
        self.last_equity = episode.carry_in.current_cash
        self.last_mark = self.geometry.centre
        self.last_minute_start: datetime | None = None
        self.previous_close = self.geometry.centre
        self._orders: dict[str, _GridOrder] = {}
        self._lots: dict[str, _GridLot] = {}
        self._audit: list[GridAuditEvent] = []
        self._processed: dict[datetime, tuple[object, ...]] = {}
        self._funding_ids: set[str] = set()
        self._last_funding_at: datetime | None = None
        self._fill_counter = 0
        self._lot_counter = 0
        self._audit_counter = 0
        self._batch_sequences: dict[datetime, int] = {}
        self._forced_close_due: datetime | None = None
        self._termination_after_close = False
        self._required_last_minute = episode.episode_start + timedelta(days=6, hours=20, minutes=1)
        due = episode.episode_start + _MINUTE
        if self.state is GridState.ACTIVE:
            for rung, price in enumerate(self.geometry.buys, 1):
                self._orders[f"ENTRY:BUY:{rung}"] = _GridOrder(
                    f"ENTRY:BUY:{rung}", GridOrderKind.ENTRY, GridSide.BUY, price, rung, due
                )
            for rung, price in enumerate(self.geometry.sells, 1):
                self._orders[f"ENTRY:SELL:{rung}"] = _GridOrder(
                    f"ENTRY:SELL:{rung}", GridOrderKind.ENTRY, GridSide.SELL, price, rung, due
                )
            self._record(episode.episode_start, "PLAN_AUTHORIZED")
        elif self.no_trade_reason is not None:
            self._record(
                episode.episode_start,
                "EPISODE_NO_TRADE_CASH_ONLY",
                details=("reason", self.no_trade_reason.value),
            )
        else:
            self._record(episode.episode_start, "TERMINATED_CARRY_IN_CASH_ONLY")
        if synthetic_geometry is not None and self.state is GridState.ACTIVE:
            self._record(episode.episode_start, "SYNTHETIC_GEOMETRY_INJECTED")
        for order in self._orders.values():
            self._record(
                episode.episode_start,
                "ORDER_CREATED",
                order=order,
                details=("due_at", order.due_at.isoformat()),
            )

    @property
    def audit(self) -> tuple[GridAuditEvent, ...]:
        return tuple(self._audit)

    @property
    def completion_status(self) -> GridCompletionStatus:
        if self.state is GridState.INTEGRITY_BLOCKED:
            return GridCompletionStatus.INTEGRITY_BLOCKED
        if self.last_minute_start == self._required_last_minute:
            return GridCompletionStatus.COMPLETE
        return GridCompletionStatus.PARTIAL_UNRESOLVED

    def _record(
        self,
        at: datetime,
        event_type: str,
        *,
        order: _GridOrder | None = None,
        lot_id: str | None = None,
        details: tuple[str, str] | tuple[()] = (),
    ) -> None:
        self._audit_counter += 1
        owned = () if not details else (details,)
        self._audit.append(
            GridAuditEvent(
                self._audit_counter,
                require_utc(at),
                event_type,
                None if order is None else order.order_id,
                lot_id,
                owned,
            )
        )

    def _record_many(
        self,
        at: datetime,
        event_type: str,
        details: tuple[tuple[str, str], ...],
    ) -> None:
        self._audit_counter += 1
        self._audit.append(
            GridAuditEvent(
                self._audit_counter,
                require_utc(at),
                event_type,
                None,
                None,
                details,
            )
        )

    def _fingerprint(self, minute: GridMinute) -> tuple[object, ...]:
        return (
            minute.open,
            minute.high,
            minute.low,
            minute.close,
            minute.mark_observed_at,
            minute.mark_price,
            minute.funding_events,
        )

    def _block(self, at: datetime, reason: str) -> None:
        self.state = GridState.INTEGRITY_BLOCKED
        self._record(at, "INTEGRITY_BLOCKED", details=("reason", reason))

    def process_minute(self, minute: GridMinute) -> GridLedgerSnapshot:
        fingerprint = self._fingerprint(minute)
        previous = self._processed.get(minute.start)
        if previous is not None:
            if previous != fingerprint:
                self._block(minute.start, "CONFLICTING_REPEATED_MINUTE")
            return self.snapshot()
        if self.state is GridState.INTEGRITY_BLOCKED:
            return self.snapshot()
        expected_start = (
            self.episode.episode_start
            if self.last_minute_start is None
            else self.last_minute_start + _MINUTE
        )
        if minute.start != expected_start or minute.start > self._required_last_minute:
            self._block(minute.start, "NON_CONTIGUOUS_MINUTE")
            return self.snapshot()
        if (
            minute.mark_observed_at != minute.start + _MINUTE
            or minute.mark_price is None
            or not minute.mark_price.is_finite()
            or minute.mark_price <= 0
        ):
            self._block(minute.start, "MISSING_OR_MISALIGNED_COMPLETED_MARK")
            return self.snapshot()

        events = tuple(sorted(minute.funding_events, key=lambda event: event.observed_at))
        if any(
            event.observed_at < minute.start or event.observed_at >= minute.start + _MINUTE
            for event in events
        ):
            self._block(minute.start, "FUNDING_OUTSIDE_MINUTE")
            return self.snapshot()
        for event in events:
            if event.observed_at == minute.start and not self._apply_funding(event):
                return self.snapshot()

        observation_prefix = minute.start == self.episode.episode_start
        if minute.start == self._required_last_minute:
            self._cancel_all(minute.start, "SCHEDULED_FLATTEN")
            self._force_close(minute.open, minute.start, "SCHEDULED_FLATTEN")
            if self.state is not GridState.TERMINATED:
                self.state = GridState.STOPPED
        elif self._forced_close_due == minute.start:
            self._cancel_all(minute.start, "ORIGINAL_CAPITAL_FLOOR")
            self._force_close(minute.open, minute.start, "ORIGINAL_CAPITAL_FLOOR")
            self.state = GridState.TERMINATED
        elif self.state is GridState.ACTIVE and not observation_prefix:
            self._process_open_gap(minute)

        if self.state is GridState.ACTIVE and not observation_prefix:
            self._activate_due(minute.start, minute.open)
        for event in events:
            if event.observed_at > minute.start and not self._apply_funding(event):
                return self.snapshot()

        if self.state is GridState.ACTIVE and not observation_prefix:
            self._process_completed_bar(minute)

        self.last_mark = minute.mark_price
        self.last_equity = self._equity(minute.mark_price)
        self.peak_equity = max(self.peak_equity, self.last_equity)
        position = self.accounting.position(self.episode.symbol)
        unrealized = (
            _ZERO
            if not position.quantity or position.average_entry_price is None
            else position.quantity * (minute.mark_price - position.average_entry_price)
        )
        self._record_many(
            minute.start + _MINUTE,
            "MINUTE_MARKED",
            (
                ("mark_price", str(minute.mark_price)),
                ("cash", str(self._cash())),
                (
                    "gross_realized_pnl",
                    str(
                        self.episode.carry_in.cumulative_gross_realized_pnl
                        + position.cumulative_gross_realized_pnl
                    ),
                ),
                ("fees", str(self.episode.carry_in.cumulative_fees + position.cumulative_fees)),
                ("funding", str(self.episode.carry_in.cumulative_funding + self.funding_cash)),
                ("unrealized_pnl", str(unrealized)),
                ("equity", str(self.last_equity)),
                ("peak_equity", str(self.peak_equity)),
                ("state", self.state.value),
            ),
        )
        if (
            self.state is GridState.STOPPED
            and self.last_equity <= self.episode.carry_in.original_account_capital * Decimal("0.80")
        ):
            self.state = GridState.TERMINATED
            self._record(minute.start + _MINUTE, "TERMINATED_AFTER_FORCED_CLOSE")
        if (
            self.state is GridState.ACTIVE
            and self.last_equity <= self.episode.carry_in.original_account_capital * Decimal("0.80")
        ):
            self._cancel_all(minute.start + _MINUTE, "ORIGINAL_CAPITAL_FLOOR")
            self._forced_close_due = minute.start + _MINUTE
            self.state = GridState.TERMINATION_PENDING
            self._record(minute.start + _MINUTE, "TERMINATION_SCHEDULED")

        self._processed[minute.start] = fingerprint
        self.last_minute_start = minute.start
        self.previous_close = minute.close
        return self.snapshot()

    def _apply_funding(self, event: GridFundingEvent) -> bool:
        if (
            not event.event_id
            or event.event_id in self._funding_ids
            or (self._last_funding_at is not None and event.observed_at <= self._last_funding_at)
            or not event.rate.is_finite()
            or event.mark_observed_at is None
            or event.mark_price is None
            or not event.mark_price.is_finite()
            or event.mark_price <= 0
            or event.mark_observed_at > event.observed_at
            or event.observed_at - event.mark_observed_at > timedelta(seconds=120)
        ):
            self._block(event.observed_at, "UNUSABLE_FUNDING_AUTHORITY")
            return False
        quantity = self.accounting.position(self.episode.symbol).quantity
        with localcontext(_CONTEXT):
            if self.scenario is GridScenario.BASE:
                cash_change = -quantity * event.mark_price * event.rate
            else:
                sign = _ONE if quantity > 0 else (-_ONE if quantity < 0 else _ZERO)
                cash_change = (
                    -abs(quantity)
                    * event.mark_price
                    * (sign * event.rate + self.costs.adverse_funding_addon)
                )
            self.funding_cash += cash_change
        self._funding_ids.add(event.event_id)
        self._last_funding_at = event.observed_at
        self._record(
            event.observed_at,
            "FUNDING_APPLIED",
            details=("cash_change", str(cash_change)),
        )
        return True

    def _process_open_gap(self, minute: GridMinute) -> None:
        lower = self.previous_close >= self.geometry.lower_boundary > minute.open
        upper = self.previous_close <= self.geometry.upper_boundary < minute.open
        endpoint = (
            self.geometry.lower_boundary
            if lower
            else self.geometry.upper_boundary
            if upper
            else minute.open
        )
        eligible = {order_id for order_id, order in self._orders.items() if order.active}
        self._process_segment(self.previous_close, endpoint, minute.start, eligible)
        if lower or upper:
            self._force_close(minute.open, minute.start, "OPENING_GAP_BOUNDARY")
            self._cancel_all(minute.start, "OPENING_GAP_BOUNDARY")
            self.state = GridState.STOPPED

    def _activate_due(self, at: datetime, open_price: Decimal) -> None:
        for order in sorted(self._orders.values(), key=lambda item: item.order_id):
            if order.active or order.due_at > at:
                continue
            marketable = (order.side is GridSide.BUY and order.price >= open_price) or (
                order.side is GridSide.SELL and order.price <= open_price
            )
            if marketable:
                order.due_at = at + _MINUTE
                self._record(at, "POST_ONLY_REJECTED", order=order)
                continue
            if order.kind is GridOrderKind.ENTRY and not self._entry_capacity_available(order):
                order.due_at = at + _MINUTE
                self._record(at, "CAPACITY_REJECTED", order=order)
                continue
            order.active = True
            self._record(at, "ORDER_ACTIVATED", order=order)

    def _entry_capacity_available(self, order: _GridOrder) -> bool:
        quantity = self.accounting.position(self.episode.symbol).quantity
        signed = self.geometry.quantity_per_rung * (_ONE if order.side is GridSide.BUY else -_ONE)
        if quantity * signed < 0:
            resulting = quantity + signed
            if quantity * resulting < 0:
                return False
        elif abs(quantity + signed) > Decimal(6) * self.geometry.quantity_per_rung:
            return False
        pending = sum(
            (
                candidate.price * self.geometry.quantity_per_rung
                for candidate in self._orders.values()
                if candidate.kind is GridOrderKind.ENTRY
                and (candidate.active or candidate.order_id == order.order_id)
            ),
            _ZERO,
        )
        return pending <= self.episode.carry_in.current_cash * Decimal("0.80")

    def _process_completed_bar(self, minute: GridMinute) -> None:
        lower = minute.low <= self.geometry.lower_boundary
        upper = minute.high >= self.geometry.upper_boundary
        if lower and upper:
            left = self._fork()
            right = self._fork()
            left._run_path((minute.open, self.geometry.lower_boundary), minute, "LOWER")
            right._run_path((minute.open, self.geometry.upper_boundary), minute, "UPPER")
            chosen = left if left._equity(minute.close) <= right._equity(minute.close) else right
            chosen._record(
                minute.start + _MINUTE,
                "DUAL_BOUNDARY_WORST_EQUITY_SELECTED",
                details=("selected", "LOWER" if chosen is left else "UPPER"),
            )
            chosen._record_many(
                minute.start + _MINUTE,
                "DUAL_BOUNDARY_ALTERNATIVES",
                (
                    ("lower_ending_equity", str(left._equity(minute.close))),
                    ("upper_ending_equity", str(right._equity(minute.close))),
                ),
            )
            self._adopt(chosen)
            return
        if lower:
            self._run_path((minute.open, self.geometry.lower_boundary), minute, "LOWER")
            return
        if upper:
            self._run_path((minute.open, self.geometry.upper_boundary), minute, "UPPER")
            return
        quantity = self.accounting.position(self.episode.symbol).quantity
        low_first = (minute.open, minute.low, minute.high, minute.close)
        high_first = (minute.open, minute.high, minute.low, minute.close)
        if quantity > 0:
            self._run_path(low_first, minute, None)
        elif quantity < 0:
            self._run_path(high_first, minute, None)
        else:
            left = self._fork()
            right = self._fork()
            left._run_path(low_first, minute, None)
            right._run_path(high_first, minute, None)
            self._adopt(
                left if left._equity(minute.close) <= right._equity(minute.close) else right
            )

    def _run_path(
        self, points: tuple[Decimal, ...], minute: GridMinute, boundary: str | None
    ) -> None:
        eligible = {order_id for order_id, order in self._orders.items() if order.active}
        at = minute.start + _MINUTE
        for start, end in pairwise(points):
            self._process_segment(start, end, at, eligible)
        if boundary is not None:
            reference = (
                self.geometry.lower_boundary
                if boundary == "LOWER"
                else self.geometry.upper_boundary
            )
            self._force_close(reference, at, f"{boundary}_BOUNDARY")
            self._cancel_all(at, f"{boundary}_BOUNDARY")
            self.state = GridState.STOPPED

    def _process_segment(
        self,
        start: Decimal,
        end: Decimal,
        at: datetime,
        eligible: set[str],
    ) -> None:
        if end == start:
            return
        descending = end < start
        side = GridSide.BUY if descending else GridSide.SELL
        crossed = [
            order
            for order_id, order in self._orders.items()
            if order_id in eligible
            and order.active
            and order.side is side
            and (end < order.price <= start if descending else start <= order.price < end)
        ]
        crossed.sort(
            key=lambda order: (
                -order.price if descending else order.price,
                0 if order.kind is GridOrderKind.OWNED_EXIT else 1,
                order.order_id,
            )
        )
        for order in crossed:
            if order.active:
                self._fill_order(order, at)

    def _fill_order(self, order: _GridOrder, at: datetime) -> None:
        if order.kind is GridOrderKind.OWNED_EXIT:
            lot = self._lots.get(order.owner_lot_id or "")
            if lot is None:
                order.active = False
                self._record(at, "ORPHAN_EXIT_CANCELLED", order=order)
                return
            self._apply_passive_fill(order.side, order.price, at, order)
            del self._lots[lot.lot_id]
            order.active = False
            self._rearm(lot.source_order_id, at)
            return

        if not self._entry_capacity_available(order):
            order.active = False
            order.due_at = at + _MINUTE
            self._record(at, "CAPACITY_REJECTED", order=order)
            return
        quantity = self.accounting.position(self.episode.symbol).quantity
        entry_sign = _ONE if order.side is GridSide.BUY else -_ONE
        if quantity * entry_sign < 0:
            candidates = sorted(
                (lot for lot in self._lots.values() if lot.side is not order.side),
                key=lambda lot: (lot.opened_at, lot.lot_id),
            )
            if not candidates:
                self._block(at, "POSITION_LOT_OWNERSHIP_MISMATCH")
                return
            consumed = candidates[0]
            self._apply_passive_fill(order.side, order.price, at, order)
            del self._lots[consumed.lot_id]
            self._cancel_owned_exit(consumed.lot_id, at)
            order.active = False
            self._rearm(consumed.source_order_id, at)
            self._rearm(order.order_id, at)
            return

        self._apply_passive_fill(order.side, order.price, at, order)
        order.active = False
        self._lot_counter += 1
        lot_id = f"LOT:{self._lot_counter:06d}"
        lot = _GridLot(lot_id, order.side, at, order.price, order.order_id)
        self._lots[lot_id] = lot
        target = (
            self.geometry.centre
            if order.rung == 1
            else (
                self.geometry.buys[order.rung - 2]
                if order.side is GridSide.BUY
                else self.geometry.sells[order.rung - 2]
            )
        )
        exit_side = GridSide.SELL if order.side is GridSide.BUY else GridSide.BUY
        exit_order = _GridOrder(
            f"EXIT:{lot_id}",
            GridOrderKind.OWNED_EXIT,
            exit_side,
            target,
            order.rung,
            at + _MINUTE,
            owner_lot_id=lot_id,
        )
        self._orders[exit_order.order_id] = exit_order
        self._record(at, "LOT_OPENED", order=order, lot_id=lot_id)
        self._record(
            at,
            "OWNED_EXIT_CREATED",
            order=exit_order,
            lot_id=lot_id,
            details=("due_at", exit_order.due_at.isoformat()),
        )

    def _apply_passive_fill(
        self, side: GridSide, price: Decimal, at: datetime, order: _GridOrder
    ) -> None:
        quantity = self.geometry.quantity_per_rung
        fee = quantity * price * self.costs.passive_fee_rate
        self._commit_fill(side, quantity, price, fee, at, order.order_id)
        self._record(
            at,
            "ORDER_FILLED",
            order=order,
            details=("fee_quote", str(fee)),
        )
        self._record_many(
            at,
            "FILL_ECONOMICS",
            (
                ("order_id", order.order_id),
                ("side", side.value),
                ("quantity", str(quantity)),
                ("fill_price", str(price)),
                ("fee_quote", str(fee)),
                ("liquidity", "PASSIVE"),
            ),
        )

    def _commit_fill(
        self,
        side: GridSide,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        at: datetime,
        owner_id: str,
    ) -> None:
        self._fill_counter += 1
        sequence = self._batch_sequences.get(at, -1) + 1
        self._batch_sequences[at] = sequence
        fill = PositionAccountingFill(
            f"{self._fill_counter:08d}:{owner_id}",
            self.episode.symbol,
            (SimulatedExecutionSide.BUY if side is GridSide.BUY else SimulatedExecutionSide.SELL),
            quantity,
            at,
            price,
            fee,
        )
        self.accounting.process(PositionAccountingFillBatch(at, (fill,), sequence))

    def _force_close(self, reference: Decimal, at: datetime, reason: str) -> None:
        quantity = self.accounting.position(self.episode.symbol).quantity
        if quantity == 0:
            self._record(at, "FORCED_CLOSE_NO_POSITION", details=("reason", reason))
            return
        side = GridSide.SELL if quantity > 0 else GridSide.BUY
        quote = price_simulated_market_fill(
            side=(
                SimulatedExecutionSide.SELL if side is GridSide.SELL else SimulatedExecutionSide.BUY
            ),
            quantity=abs(quantity),
            reference_price=reference,
            costs=SimulatedExecutionCostConfiguration(
                slippage_bps=self.costs.forced_slippage_bps,
                fee_bps=self.costs.forced_fee_bps,
            ),
        )
        self._commit_fill(side, abs(quantity), quote.fill_price, quote.fee_quote, at, reason)
        self._lots.clear()
        self._record(
            at,
            "FORCED_CLOSE_FILLED",
            details=("reason", reason),
        )
        self._record_many(
            at,
            "FORCED_CLOSE_ECONOMICS",
            (
                ("reason", reason),
                ("side", side.value),
                ("quantity", str(abs(quantity))),
                ("reference_price", str(reference)),
                ("fill_price", str(quote.fill_price)),
                ("fee_quote", str(quote.fee_quote)),
            ),
        )

    def _cancel_owned_exit(self, lot_id: str, at: datetime) -> None:
        order = self._orders.get(f"EXIT:{lot_id}")
        if order is not None:
            order.active = False
            self._record(at, "OWNED_EXIT_CANCELLED", order=order, lot_id=lot_id)

    def _rearm(self, order_id: str, at: datetime) -> None:
        order = self._orders[order_id]
        order.active = False
        order.due_at = at + _MINUTE
        self._record(
            at,
            "ORDER_REARMED",
            order=order,
            details=("due_at", order.due_at.isoformat()),
        )

    def _cancel_all(self, at: datetime, reason: str) -> None:
        for order in self._orders.values():
            if order.active:
                order.active = False
                self._record(at, "ORDER_CANCELLED", order=order, details=("reason", reason))

    def _cash(self) -> Decimal:
        state = self.accounting.position(self.episode.symbol)
        return (
            self.episode.carry_in.current_cash
            + state.cumulative_gross_realized_pnl
            - state.cumulative_fees
            + self.funding_cash
        )

    def _equity(self, mark: Decimal) -> Decimal:
        state = self.accounting.position(self.episode.symbol)
        unrealized = _ZERO
        if state.quantity and state.average_entry_price is not None:
            unrealized = state.quantity * (mark - state.average_entry_price)
        return self._cash() + unrealized

    def snapshot(self) -> GridLedgerSnapshot:
        state = self.accounting.position(self.episode.symbol)
        unrealized = _ZERO
        if state.quantity and state.average_entry_price is not None:
            unrealized = state.quantity * (self.last_mark - state.average_entry_price)
        equity = self._cash() + unrealized
        drawdown = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else _ZERO
        return GridLedgerSnapshot(
            self.state,
            self._cash(),
            state.quantity,
            state.average_entry_price,
            self.episode.carry_in.cumulative_gross_realized_pnl
            + state.cumulative_gross_realized_pnl,
            self.episode.carry_in.cumulative_fees + state.cumulative_fees,
            self.episode.carry_in.cumulative_funding + self.funding_cash,
            unrealized,
            equity,
            self.peak_equity,
            drawdown,
            sum(order.active for order in self._orders.values()),
            len(self._lots),
        )

    def carry_out(self) -> GridLedgerCarry:
        """Return the next-week seed only after a complete flat episode."""

        state = self.accounting.position(self.episode.symbol)
        if self.completion_status is not GridCompletionStatus.COMPLETE:
            raise GridV1Error("partial or blocked episode has no authoritative carry-out")
        if state.quantity != 0 or self.state not in {GridState.STOPPED, GridState.TERMINATED}:
            raise GridV1Error("complete episode must be flat and terminal for the week")
        return GridLedgerCarry(
            self.episode.symbol,
            self.episode.carry_in.original_account_capital,
            self._cash(),
            self._cash(),
            self.episode.carry_in.cumulative_gross_realized_pnl
            + state.cumulative_gross_realized_pnl,
            self.episode.carry_in.cumulative_fees + state.cumulative_fees,
            self.episode.carry_in.cumulative_funding + self.funding_cash,
            self.peak_equity,
            self.state is GridState.TERMINATED,
            self.episode.episode_start,
        )

    def report(self) -> GridEpisodeReport:
        closes_payload = json.dumps(
            [str(value) for value in self.episode.completed_4h_closes],
            separators=(",", ":"),
        ).encode()
        processed_starts = tuple(sorted(self._processed))
        input_contract: dict[str, object] = {
            "completed_4h_close_count": len(self.episode.completed_4h_closes),
            "completed_4h_closes_sha256": sha256(closes_payload).hexdigest(),
            "processed_minute_count": len(processed_starts),
            "first_processed_minute": (
                None if not processed_starts else processed_starts[0].isoformat()
            ),
            "last_processed_minute": (
                None if not processed_starts else processed_starts[-1].isoformat()
            ),
            "source_clock": "UTC",
            "required_first_minute": self.episode.episode_start.isoformat(),
            "required_last_minute": self._required_last_minute.isoformat(),
            "required_minute_count": 6 * 24 * 60 + 20 * 60 + 2,
        }
        if self.no_trade_reason is not None:
            geometry: dict[str, object] = {
                "status": "REJECTED_PRE_ORDER",
                "rejection_reason": self.no_trade_reason.value,
            }
        elif self.week_disposition is GridWeekDisposition.TERMINATED_CASH_ONLY:
            geometry = {"status": "NOT_EVALUATED_TERMINATED_CARRY"}
        else:
            geometry = {
                "status": "VALID",
                **{key: _json_value(value) for key, value in asdict(self.geometry).items()},
            }
        if self.authority is None:
            authority_status = (
                "NO_TRADE_PRE_ORDER_REJECTION"
                if self.no_trade_reason is not None
                else "TERMINATED_CARRY_IN_NO_PLAN"
            )
            authority: dict[str, object] = {"authorization_status": authority_status}
            if self.no_trade_reason is not None:
                authority["rejection_reason"] = self.no_trade_reason.value
        else:
            identity = self.authority.decision_identity
            authority = {
                "decision_identity": {
                    "source_id": identity.source_id.value,
                    "symbol": str(identity.symbol),
                    "observed_at": identity.observed_at.isoformat(),
                    "strategy_id": identity.strategy_id,
                    "strategy_version": identity.strategy_version,
                },
                "directional_risk_status": {
                    "LONG_ENVELOPE": self.authority.directional_risk_decisions[0].status.value,
                    "SHORT_ENVELOPE": self.authority.directional_risk_decisions[1].status.value,
                },
                "directional_safety_status": {
                    "LONG_ENVELOPE": self.authority.directional_safety_decisions[0].status.value,
                    "SHORT_ENVELOPE": self.authority.directional_safety_decisions[1].status.value,
                },
                "drawdown_enforcement": self.authority.directional_risk_decisions[
                    0
                ].drawdown_enforcement.value,
                "reported_peak_equity": str(
                    self.authority.directional_risk_decisions[0].peak_equity_quote
                ),
                "reported_drawdown_fraction": str(
                    self.authority.directional_risk_decisions[0].drawdown_fraction
                ),
                "authorization_status": self.authority.authorization_status.value,
                "approved_modeled_loss": str(self.authority.approved_modeled_loss),
                "approved_pending_notional": str(self.authority.approved_pending_notional),
            }
        snapshot = {key: _json_value(value) for key, value in asdict(self.snapshot()).items()}
        audit: tuple[dict[str, object], ...] = tuple(
            {
                "sequence": item.sequence,
                "observed_at": item.observed_at.isoformat(),
                "event_type": item.event_type,
                "order_id": item.order_id,
                "lot_id": item.lot_id,
                "details": dict(item.details),
            }
            for item in self.audit
        )
        contract = {key: str(value) for key, value in asdict(self.contract_identity).items()}
        carry_in = {key: _json_value(value) for key, value in asdict(self.episode.carry_in).items()}
        carry_out = (
            {key: _json_value(value) for key, value in asdict(self.carry_out()).items()}
            if self.completion_status is GridCompletionStatus.COMPLETE
            else None
        )
        return GridEpisodeReport(
            "btc-eth-grid-v1-synthetic-report",
            GRID_STRATEGY_ID,
            GRID_PROTOCOL_VERSION,
            GRID_ENGINE_VERSION,
            self.episode.code_sha,
            contract,
            self.episode.source_id.value,
            str(self.episode.symbol),
            self.scenario.value,
            self.episode.episode_start.isoformat(),
            self.completion_status.value,
            self.week_disposition.value,
            None if self.no_trade_reason is None else self.no_trade_reason.value,
            carry_in,
            carry_out,
            input_contract,
            geometry,
            authority,
            snapshot,
            audit,
        )

    def _fork(self) -> GridV1Engine:
        fork = copy(self)
        fork.accounting = self.accounting.clone()
        fork._orders = {key: replace(value) for key, value in self._orders.items()}
        fork._lots = dict(self._lots)
        fork._audit = list(self._audit)
        fork._processed = dict(self._processed)
        fork._funding_ids = set(self._funding_ids)
        fork._batch_sequences = dict(self._batch_sequences)
        return fork

    def _adopt(self, other: GridV1Engine) -> None:
        for name in (
            "accounting",
            "state",
            "funding_cash",
            "peak_equity",
            "last_equity",
            "last_mark",
            "_orders",
            "_lots",
            "_audit",
            "_processed",
            "_funding_ids",
            "_last_funding_at",
            "_fill_counter",
            "_lot_counter",
            "_audit_counter",
            "_batch_sequences",
            "_forced_close_due",
        ):
            setattr(self, name, getattr(other, name))


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, TradingSymbol):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def run_synthetic_grid_episode(
    episode: GridEpisodeInput,
    scenario: GridScenario,
    health: OperationalHealthSnapshot,
    minutes: tuple[GridMinute, ...],
    *,
    synthetic_geometry: GridGeometry | None = None,
) -> GridEpisodeReport:
    """Drive the production Grid state machine with caller-owned synthetic facts."""

    engine = GridV1Engine(
        episode,
        scenario,
        health,
        synthetic_geometry=synthetic_geometry,
    )
    for minute in minutes:
        engine.process_minute(minute)
    return engine.report()


__all__ = [
    "GRID_ACCEPTED_PROTOCOL_HEAD",
    "GRID_ENGINE_VERSION",
    "GRID_PARAMETERS_SHA256",
    "GRID_PROTOCOL_DOCUMENT_SHA256",
    "GRID_PROTOCOL_VERSION",
    "GRID_STRATEGY_ID",
    "GridAuditEvent",
    "GridCompletionStatus",
    "GridContractIdentity",
    "GridEpisodeInput",
    "GridEpisodeReport",
    "GridFundingEvent",
    "GridGeometry",
    "GridLedgerCarry",
    "GridLedgerSnapshot",
    "GridMinute",
    "GridNoTradeReason",
    "GridPlanAuthority",
    "GridScenario",
    "GridState",
    "GridV1Engine",
    "GridV1Error",
    "GridWeekDisposition",
    "grid_runtime_implementation_sha256",
    "healthy_grid_health",
    "run_synthetic_grid_episode",
    "verified_grid_contract_identity",
]
