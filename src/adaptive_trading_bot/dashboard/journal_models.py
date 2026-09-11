"""Detached read models for authoritative decision and completed-trade journals."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from adaptive_trading_bot.dashboard.models import DashboardValue
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import TradeOutcomeClassification
from adaptive_trading_bot.risk import OperationalSafetyStatus, RiskDecisionStatus
from adaptive_trading_bot.strategies import CandidateAction

MAX_DASHBOARD_JOURNAL_RECORDS = 500


class DashboardJournalValidationError(ValueError):
    """A journal query or projected result violates the read boundary."""


@dataclass(frozen=True, slots=True)
class DashboardDecisionTradeQuery:
    """Finite half-open journal query shared by decision and trade endpoints."""

    start: datetime
    end: datetime
    source_id: str | None = None
    symbol: str | None = None
    strategy_id: str | None = None
    action: CandidateAction | None = None
    risk_status: RiskDecisionStatus | None = None
    safety_status: OperationalSafetyStatus | None = None
    classification: TradeOutcomeClassification | None = None
    limit: int = MAX_DASHBOARD_JOURNAL_RECORDS

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        require_utc(self.start, field="dashboard journal query start")
        require_utc(self.end, field="dashboard journal query end")
        if self.start >= self.end:
            raise DashboardJournalValidationError("journal query must be a non-empty range")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool):
            raise TypeError("journal query limit must be an integer")
        if self.limit < 1 or self.limit > MAX_DASHBOARD_JOURNAL_RECORDS:
            raise DashboardJournalValidationError(
                "journal query limit is outside the supported range"
            )
        for name, value in (("source_id", self.source_id), ("strategy_id", self.strategy_id)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DashboardJournalValidationError(f"{name} filter must be non-empty")
        if self.symbol is not None:
            parsed = TradingSymbol.parse(self.symbol)
            if parsed.base not in {"BTC", "ETH"} or parsed.quote != "USDT":
                raise DashboardJournalValidationError("symbol filter is outside V1")


@dataclass(frozen=True, slots=True)
class DashboardDecisionQuery(DashboardDecisionTradeQuery):
    """Named decision-query contract for provider consumers."""


@dataclass(frozen=True, slots=True)
class DashboardTradeQuery(DashboardDecisionTradeQuery):
    """Named completed-trade-query contract for provider consumers."""


@dataclass(frozen=True, slots=True)
class DashboardDecisionIdentity:
    source_id: str
    symbol: str
    observed_at: datetime
    strategy_id: str
    strategy_version: str

    def validate(self) -> None:
        require_utc(self.observed_at, field="dashboard decision identity observed_at")

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, str]:
        return (
            self.observed_at,
            self.source_id,
            self.symbol,
            self.strategy_id,
            self.strategy_version,
        )


@dataclass(frozen=True, slots=True)
class DashboardStrategyDecision:
    action: str
    reference_price: Decimal | None
    invalidation_reference_price: Decimal | None
    reasons_for: DashboardValue[tuple[str, ...]]
    reasons_against: DashboardValue[tuple[str, ...]]
    rule_audit: DashboardValue[str]
    fundamental_context: DashboardValue[str]
    experience_metrics: DashboardValue[str]
    confidence: DashboardValue[Decimal]
    target_price: DashboardValue[Decimal]
    expected_reward_risk: DashboardValue[Decimal]

    def validate(self) -> None:
        self.reasons_for.validate()
        self.reasons_against.validate()
        self.rule_audit.validate()
        self.fundamental_context.validate()
        self.experience_metrics.validate()
        for optional_field, optional_value in (
            ("reference_price", self.reference_price),
            ("invalidation_reference_price", self.invalidation_reference_price),
        ):
            _require_finite(optional_value, field=optional_field)
        for available_field, available_value in (
            ("confidence", self.confidence),
            ("target_price", self.target_price),
            ("expected_reward_risk", self.expected_reward_risk),
        ):
            available_value.validate()
            _require_finite(available_value.value, field=available_field)


@dataclass(frozen=True, slots=True)
class DashboardRiskDecision:
    status: str
    reasons: tuple[str, ...]
    policy_version: str
    reference_price: Decimal | None
    approved_risk_stop_basis: Decimal | None
    stop_distance: Decimal | None
    quote_currency: str
    risk_snapshot_observed_at: datetime
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

    def validate(self) -> None:
        require_utc(
            self.risk_snapshot_observed_at,
            field="dashboard risk snapshot observed_at",
        )
        for decimal_field, decimal_value in (
            ("reference_price", self.reference_price),
            ("approved_risk_stop_basis", self.approved_risk_stop_basis),
            ("stop_distance", self.stop_distance),
            ("account_equity_quote", self.account_equity_quote),
            ("day_start_equity_quote", self.day_start_equity_quote),
            ("peak_equity_quote", self.peak_equity_quote),
            ("daily_loss_quote", self.daily_loss_quote),
            ("daily_loss_fraction", self.daily_loss_fraction),
            ("max_daily_loss_fraction", self.max_daily_loss_fraction),
            ("drawdown_quote", self.drawdown_quote),
            ("drawdown_fraction", self.drawdown_fraction),
            ("max_drawdown_fraction", self.max_drawdown_fraction),
            ("current_open_risk_quote", self.current_open_risk_quote),
            ("simultaneous_risk_cap_quote", self.simultaneous_risk_cap_quote),
            ("remaining_simultaneous_risk_quote", self.remaining_simultaneous_risk_quote),
            ("current_gross_exposure_quote", self.current_gross_exposure_quote),
            ("gross_exposure_cap_quote", self.gross_exposure_cap_quote),
            ("remaining_gross_exposure_quote", self.remaining_gross_exposure_quote),
            ("per_trade_risk_budget_quote", self.per_trade_risk_budget_quote),
            ("approved_quantity", self.approved_quantity),
            ("approved_notional_quote", self.approved_notional_quote),
            ("approved_risk_quote", self.approved_risk_quote),
            ("resulting_open_risk_quote", self.resulting_open_risk_quote),
            ("resulting_gross_exposure_quote", self.resulting_gross_exposure_quote),
        ):
            _require_finite(decimal_value, field=decimal_field)


@dataclass(frozen=True, slots=True)
class DashboardSafetyDecision:
    status: str
    reasons: tuple[str, ...]
    policy_version: str
    observed_at: datetime
    health_source_id: str
    health_symbol: str
    health_observed_at: datetime
    market_data_observed_at: datetime
    market_data_age: timedelta
    max_market_data_age_seconds: int
    feed_integrity_state: str
    exchange_api_health_state: str
    spread_bps: Decimal
    max_spread_bps: Decimal
    time_sync_offset_ms: Decimal
    max_abs_time_sync_offset_ms: Decimal
    volatility_safety_state: str
    suspicious_price_anomaly: bool
    consecutive_order_rejections: int
    max_consecutive_order_rejections: int
    risk_requires_halt_review: bool
    approved_risk_stop_basis: Decimal | None
    approved_quantity: Decimal | None
    approved_notional_quote: Decimal | None
    approved_risk_quote: Decimal | None
    entry_permitted: bool

    def validate(self) -> None:
        require_utc(self.observed_at, field="dashboard safety observed_at")
        require_utc(self.health_observed_at, field="dashboard safety health_observed_at")
        require_utc(
            self.market_data_observed_at,
            field="dashboard safety market_data_observed_at",
        )
        for safety_field, safety_value in (
            ("spread_bps", self.spread_bps),
            ("max_spread_bps", self.max_spread_bps),
            ("time_sync_offset_ms", self.time_sync_offset_ms),
            ("max_abs_time_sync_offset_ms", self.max_abs_time_sync_offset_ms),
            ("approved_risk_stop_basis", self.approved_risk_stop_basis),
            ("approved_quantity", self.approved_quantity),
            ("approved_notional_quote", self.approved_notional_quote),
            ("approved_risk_quote", self.approved_risk_quote),
        ):
            _require_finite(safety_value, field=safety_field)


@dataclass(frozen=True, slots=True)
class DashboardEntryIntentIdentity:
    decision_identity: DashboardDecisionIdentity
    purpose: str
    schema_version: str

    def validate(self) -> None:
        self.decision_identity.validate()


@dataclass(frozen=True, slots=True)
class DashboardPaperOrderIdentity:
    intent_identity: DashboardEntryIntentIdentity
    simulation_schema_version: str

    def validate(self) -> None:
        self.intent_identity.validate()


@dataclass(frozen=True, slots=True)
class DashboardExitIntentIdentity:
    originating_entry_intent_identity: DashboardEntryIntentIdentity
    trigger_observed_at: datetime
    purpose: str
    schema_version: str

    def validate(self) -> None:
        self.originating_entry_intent_identity.validate()
        require_utc(self.trigger_observed_at, field="dashboard exit intent trigger_observed_at")


@dataclass(frozen=True, slots=True)
class DashboardCompletedTradeRecord:
    originating_decision_identity: DashboardDecisionIdentity
    entry_intent_identity: DashboardEntryIntentIdentity
    paper_order_identity: DashboardPaperOrderIdentity
    exit_intent_identity: DashboardExitIntentIdentity
    source_id: str
    symbol: str
    strategy_id: str
    strategy_version: str
    entry_side: str
    exit_side: str
    quantity: Decimal
    approved_notional_quote: Decimal
    approved_risk_quote: Decimal
    stop_basis: Decimal
    approved_reference_price: Decimal
    entry_authorized_at: datetime
    entry_reference_price: Decimal
    entry_fill_at: datetime
    entry_fill_price: Decimal
    entry_spread_bps: Decimal
    entry_slippage_bps: Decimal
    entry_fee_bps: Decimal
    entry_fee_quote: Decimal
    exit_reason: str
    trigger_observed_at: datetime
    trigger_reference_price: Decimal
    exit_authorized_at: datetime
    exit_reference_price: Decimal
    exit_fill_at: datetime
    exit_fill_price: Decimal
    exit_spread_bps: Decimal
    exit_slippage_bps: Decimal
    exit_fee_bps: Decimal
    exit_fee_quote: Decimal
    gross_realized_pnl: Decimal
    total_trade_fees_quote: Decimal
    net_realized_pnl: Decimal
    realized_r_net: Decimal
    duration: timedelta
    risk_policy_version: str
    safety_policy_version: str
    accounting_entry_fill_id: str
    accounting_exit_fill_id: str
    outcome_classification: str
    mfe: DashboardValue[Decimal]
    mae: DashboardValue[Decimal]
    process_score: DashboardValue[Decimal]

    def validate(self) -> None:
        self.originating_decision_identity.validate()
        self.entry_intent_identity.validate()
        self.paper_order_identity.validate()
        self.exit_intent_identity.validate()
        if (
            self.entry_intent_identity.decision_identity != self.originating_decision_identity
            or self.paper_order_identity.intent_identity != self.entry_intent_identity
            or self.exit_intent_identity.originating_entry_intent_identity
            != self.entry_intent_identity
        ):
            raise DashboardJournalValidationError(
                "completed trade authorization identity chain is inconsistent"
            )
        origin = self.originating_decision_identity
        if (
            self.source_id != origin.source_id
            or self.symbol != origin.symbol
            or self.strategy_id != origin.strategy_id
            or self.strategy_version != origin.strategy_version
        ):
            raise DashboardJournalValidationError(
                "completed trade facts do not match the originating decision"
            )
        if self.exit_intent_identity.trigger_observed_at != self.trigger_observed_at:
            raise DashboardJournalValidationError(
                "completed trade trigger does not match its exit intent identity"
            )
        for time_field, time_value in (
            ("entry_authorized_at", self.entry_authorized_at),
            ("entry_fill_at", self.entry_fill_at),
            ("trigger_observed_at", self.trigger_observed_at),
            ("exit_authorized_at", self.exit_authorized_at),
            ("exit_fill_at", self.exit_fill_at),
        ):
            require_utc(time_value, field=f"dashboard trade {time_field}")
        for decimal_field, decimal_value in (
            ("quantity", self.quantity),
            ("approved_notional_quote", self.approved_notional_quote),
            ("approved_risk_quote", self.approved_risk_quote),
            ("stop_basis", self.stop_basis),
            ("approved_reference_price", self.approved_reference_price),
            ("entry_reference_price", self.entry_reference_price),
            ("entry_fill_price", self.entry_fill_price),
            ("entry_spread_bps", self.entry_spread_bps),
            ("entry_slippage_bps", self.entry_slippage_bps),
            ("entry_fee_bps", self.entry_fee_bps),
            ("entry_fee_quote", self.entry_fee_quote),
            ("trigger_reference_price", self.trigger_reference_price),
            ("exit_reference_price", self.exit_reference_price),
            ("exit_fill_price", self.exit_fill_price),
            ("exit_spread_bps", self.exit_spread_bps),
            ("exit_slippage_bps", self.exit_slippage_bps),
            ("exit_fee_bps", self.exit_fee_bps),
            ("exit_fee_quote", self.exit_fee_quote),
            ("gross_realized_pnl", self.gross_realized_pnl),
            ("total_trade_fees_quote", self.total_trade_fees_quote),
            ("net_realized_pnl", self.net_realized_pnl),
            ("realized_r_net", self.realized_r_net),
        ):
            _require_finite(decimal_value, field=decimal_field)
        for analytic_field, analytic_value in (
            ("mfe", self.mfe),
            ("mae", self.mae),
            ("process_score", self.process_score),
        ):
            analytic_value.validate()
            _require_finite(analytic_value.value, field=analytic_field)


@dataclass(frozen=True, slots=True)
class DashboardDecisionRecord:
    identity: DashboardDecisionIdentity
    strategy: DashboardStrategyDecision
    risk: DashboardRiskDecision
    safety: DashboardSafetyDecision
    completed_trade: DashboardValue[DashboardCompletedTradeRecord]

    def validate(self) -> None:
        self.identity.validate()
        self.strategy.validate()
        self.risk.validate()
        self.safety.validate()
        self.completed_trade.validate()
        if self.completed_trade.value is not None:
            self.completed_trade.value.validate()
            if self.completed_trade.value.originating_decision_identity != self.identity:
                raise DashboardJournalValidationError(
                    "completed trade does not match its decision record identity"
                )
        if (
            self.risk.risk_snapshot_observed_at != self.identity.observed_at
            or self.safety.observed_at != self.identity.observed_at
        ):
            raise DashboardJournalValidationError(
                "decision layers do not match the decision identity time"
            )


@dataclass(frozen=True, slots=True)
class DashboardDecisionPage:
    start: datetime
    end: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    records: tuple[DashboardDecisionRecord, ...]

    def validate(self) -> None:
        require_utc(self.start, field="dashboard decision page start")
        require_utc(self.end, field="dashboard decision page end")
        if not isinstance(self.records, tuple):
            raise TypeError("decision page records must be an immutable tuple")
        if self.start >= self.end or not 1 <= self.limit <= MAX_DASHBOARD_JOURNAL_RECORDS:
            raise DashboardJournalValidationError("decision page range or limit is invalid")
        if self.total_matched < 0 or self.returned_count < 0:
            raise DashboardJournalValidationError("decision page count is invalid")
        if self.returned_count != len(self.records):
            raise DashboardJournalValidationError("decision page count is inconsistent")
        if self.truncated is not (self.total_matched > self.returned_count):
            raise DashboardJournalValidationError("decision page truncation is inconsistent")
        for record in self.records:
            record.validate()
            if not self.start <= record.identity.observed_at < self.end:
                raise DashboardJournalValidationError(
                    "decision record is outside the requested range"
                )
        keys = tuple(record.identity.sort_key for record in self.records)
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise DashboardJournalValidationError(
                "decision page records are not in canonical unique order"
            )


def _require_finite(value: Decimal | None, *, field: str) -> None:
    if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
        raise DashboardJournalValidationError(f"{field} must be a finite Decimal when available")


@dataclass(frozen=True, slots=True)
class DashboardTradePage:
    start: datetime
    end: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    records: tuple[DashboardCompletedTradeRecord, ...]

    def validate(self) -> None:
        require_utc(self.start, field="dashboard trade page start")
        require_utc(self.end, field="dashboard trade page end")
        if not isinstance(self.records, tuple):
            raise TypeError("trade page records must be an immutable tuple")
        if self.start >= self.end or not 1 <= self.limit <= MAX_DASHBOARD_JOURNAL_RECORDS:
            raise DashboardJournalValidationError("trade page range or limit is invalid")
        if self.total_matched < 0 or self.returned_count < 0:
            raise DashboardJournalValidationError("trade page count is invalid")
        if self.returned_count != len(self.records):
            raise DashboardJournalValidationError("trade page count is inconsistent")
        if self.truncated is not (self.total_matched > self.returned_count):
            raise DashboardJournalValidationError("trade page truncation is inconsistent")
        for record in self.records:
            record.validate()
            if not self.start <= record.exit_fill_at < self.end:
                raise DashboardJournalValidationError(
                    "completed trade is outside the requested range"
                )
        keys = tuple(
            (record.exit_fill_at, *record.originating_decision_identity.sort_key)
            for record in self.records
        )
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise DashboardJournalValidationError(
                "trade page records are not in canonical unique order"
            )
