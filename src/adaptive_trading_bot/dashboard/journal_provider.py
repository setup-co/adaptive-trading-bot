"""Strictly read-only projections over authoritative journal snapshots."""

from enum import StrEnum
from typing import Protocol

from adaptive_trading_bot.dashboard.journal_models import (
    DashboardCompletedTradeRecord,
    DashboardDecisionIdentity,
    DashboardDecisionPage,
    DashboardDecisionRecord,
    DashboardDecisionTradeQuery,
    DashboardEntryIntentIdentity,
    DashboardExitIntentIdentity,
    DashboardPaperOrderIdentity,
    DashboardRiskDecision,
    DashboardSafetyDecision,
    DashboardStrategyDecision,
    DashboardTradePage,
)
from adaptive_trading_bot.dashboard.models import DashboardValue
from adaptive_trading_bot.journal import (
    CompletedTradeJournalEntry,
    CompletedTradeJournalSnapshot,
    DecisionIdentity,
    DecisionJournalEntry,
    DecisionJournalSnapshot,
)

_UNAVAILABLE_RULE_AUDIT = "strategy rule audit is not safely serializable"
_UNAVAILABLE_STRATEGY_FACT = "authoritative strategy fact is unavailable"
_UNAVAILABLE_OUTCOME = "no completed trade exists for this exact decision identity"
_UNAVAILABLE_TRADE_ANALYTICS = "authoritative completed-trade analytics are unavailable"


class DashboardJournalProviderError(RuntimeError):
    """Journal facts could not be projected without ambiguity."""


class DashboardJournalProvider(Protocol):
    """Bounded observation-only access to decision and completed-trade facts."""

    def decisions(self, query: DashboardDecisionTradeQuery) -> DashboardDecisionPage: ...

    def trades(self, query: DashboardDecisionTradeQuery) -> DashboardTradePage: ...


class SnapshotDashboardJournalProvider:
    """Project detached records from the two authoritative journal snapshots."""

    __slots__ = ("_completed", "_decisions")

    def __init__(
        self,
        decisions: DecisionJournalSnapshot[object, object],
        completed: CompletedTradeJournalSnapshot,
    ) -> None:
        if not isinstance(decisions, DecisionJournalSnapshot):
            raise TypeError("decisions must be a DecisionJournalSnapshot")
        if not isinstance(completed, CompletedTradeJournalSnapshot):
            raise TypeError("completed must be a CompletedTradeJournalSnapshot")
        self._decisions = decisions
        self._completed = completed

    def decisions(self, query: DashboardDecisionTradeQuery) -> DashboardDecisionPage:
        if not isinstance(query, DashboardDecisionTradeQuery):
            raise TypeError("query must be a DashboardDecisionTradeQuery")
        query.validate()
        decisions = self._validated_decisions()
        outcomes = self._validated_outcomes()
        matched = tuple(
            entry
            for entry in decisions
            if _decision_matches(query, entry)
        )
        projected = tuple(
            _project_decision(entry, outcomes.get(entry.identity))
            for entry in matched[: query.limit]
        )
        page = DashboardDecisionPage(
            query.start,
            query.end,
            query.limit,
            len(matched),
            len(projected),
            len(matched) > len(projected),
            projected,
        )
        page.validate()
        return page

    def trades(self, query: DashboardDecisionTradeQuery) -> DashboardTradePage:
        if not isinstance(query, DashboardDecisionTradeQuery):
            raise TypeError("query must be a DashboardDecisionTradeQuery")
        query.validate()
        outcomes = tuple(
            entry
            for entry in self._validated_completed_entries()
            if _trade_matches(query, entry)
        )
        projected = tuple(_project_trade(entry) for entry in outcomes[: query.limit])
        page = DashboardTradePage(
            query.start,
            query.end,
            query.limit,
            len(outcomes),
            len(projected),
            len(outcomes) > len(projected),
            projected,
        )
        page.validate()
        return page

    def _validated_decisions(self) -> tuple[DecisionJournalEntry[object, object], ...]:
        snapshot = self._decisions
        if snapshot.journal_schema_version != "1.0.0" or not isinstance(snapshot.entries, tuple):
            raise DashboardJournalProviderError("decision journal snapshot is inconsistent")
        identities: set[DecisionIdentity] = set()
        for entry in snapshot.entries:
            if not isinstance(entry, DecisionJournalEntry):
                raise DashboardJournalProviderError("decision journal contains an invalid entry")
            entry.validate()
            if entry.identity in identities:
                raise DashboardJournalProviderError(
                    "decision journal contains a duplicate identity"
                )
            identities.add(entry.identity)
        expected_last = max((item.identity.observed_at for item in snapshot.entries), default=None)
        if snapshot.last_observed_at != expected_last:
            raise DashboardJournalProviderError("decision journal chronology is inconsistent")
        return tuple(sorted(snapshot.entries, key=lambda item: item.identity.sort_key))

    def _validated_completed_entries(self) -> tuple[CompletedTradeJournalEntry, ...]:
        snapshot = self._completed
        if snapshot.journal_schema_version != "1.0.0" or not isinstance(snapshot.entries, tuple):
            raise DashboardJournalProviderError("completed journal snapshot is inconsistent")
        identities: set[object] = set()
        origins: set[DecisionIdentity] = set()
        for entry in snapshot.entries:
            if not isinstance(entry, CompletedTradeJournalEntry):
                raise DashboardJournalProviderError("completed journal contains an invalid entry")
            entry.validate()
            if entry.identity in identities:
                raise DashboardJournalProviderError(
                    "completed journal contains a duplicate identity"
                )
            if entry.identity.originating_decision_identity in origins:
                raise DashboardJournalProviderError(
                    "completed journal has multiple outcomes for one decision"
                )
            identities.add(entry.identity)
            origins.add(entry.identity.originating_decision_identity)
        expected_last = max((item.exit_fill_at for item in snapshot.entries), default=None)
        if snapshot.last_closed_at != expected_last:
            raise DashboardJournalProviderError("completed journal chronology is inconsistent")
        return tuple(
            sorted(
                snapshot.entries,
                key=lambda item: (item.exit_fill_at, item.identity.sort_key),
            )
        )

    def _validated_outcomes(self) -> dict[DecisionIdentity, CompletedTradeJournalEntry]:
        return {
            item.identity.originating_decision_identity: item
            for item in self._validated_completed_entries()
        }


def _decision_matches(
    query: DashboardDecisionTradeQuery,
    entry: DecisionJournalEntry[object, object],
) -> bool:
    identity = entry.identity
    return (
        query.start <= identity.observed_at < query.end
        and (query.source_id is None or identity.source_id.value == query.source_id)
        and (query.symbol is None or str(identity.symbol) == query.symbol)
        and (query.strategy_id is None or identity.strategy_id == query.strategy_id)
        and (query.action is None or entry.strategy_evaluation.action is query.action)
        and (query.risk_status is None or entry.risk_decision.status is query.risk_status)
        and (query.safety_status is None or entry.safety_decision.status is query.safety_status)
    )


def _trade_matches(query: DashboardDecisionTradeQuery, entry: CompletedTradeJournalEntry) -> bool:
    return (
        query.start <= entry.exit_fill_at < query.end
        and (query.source_id is None or entry.source_id.value == query.source_id)
        and (query.symbol is None or str(entry.symbol) == query.symbol)
        and (query.strategy_id is None or entry.strategy_id == query.strategy_id)
        and (
            query.classification is None
            or entry.outcome_classification is query.classification
        )
    )


def _project_identity(identity: DecisionIdentity) -> DashboardDecisionIdentity:
    return DashboardDecisionIdentity(
        identity.source_id.value,
        str(identity.symbol),
        identity.observed_at,
        identity.strategy_id,
        identity.strategy_version,
    )


def _reason_value(reasons: tuple[object, ...]) -> DashboardValue[tuple[str, ...]]:
    values: list[str] = []
    for reason in reasons:
        if isinstance(reason, StrEnum):
            values.append(reason.value)
        elif isinstance(reason, str):
            values.append(reason)
        else:
            return DashboardValue.unavailable("strategy reasons are not safely serializable")
    return DashboardValue.available(tuple(values))


def _project_decision(
    entry: DecisionJournalEntry[object, object],
    completed: CompletedTradeJournalEntry | None,
) -> DashboardDecisionRecord:
    strategy = entry.strategy_evaluation
    risk = entry.risk_decision
    safety = entry.safety_decision
    outcome = (
        DashboardValue.available(_project_trade(completed))
        if completed is not None
        else DashboardValue.unavailable(_UNAVAILABLE_OUTCOME)
    )
    return DashboardDecisionRecord(
        _project_identity(entry.identity),
        DashboardStrategyDecision(
            strategy.action.value,
            strategy.reference_price,
            strategy.invalidation_reference_price,
            _reason_value(strategy.reasons_for),
            _reason_value(strategy.reasons_against),
            DashboardValue.unavailable(_UNAVAILABLE_RULE_AUDIT),
            DashboardValue.unavailable(_UNAVAILABLE_STRATEGY_FACT),
            DashboardValue.unavailable(_UNAVAILABLE_STRATEGY_FACT),
            DashboardValue.unavailable(_UNAVAILABLE_STRATEGY_FACT),
            DashboardValue.unavailable(_UNAVAILABLE_STRATEGY_FACT),
            DashboardValue.unavailable(_UNAVAILABLE_STRATEGY_FACT),
        ),
        DashboardRiskDecision(
            risk.status.value,
            tuple(reason.value for reason in risk.reasons),
            risk.risk_policy_version,
            risk.reference_price,
            risk.approved_risk_stop_basis,
            risk.stop_distance,
            risk.quote_currency,
            risk.risk_snapshot_observed_at,
            risk.account_equity_quote,
            risk.day_start_equity_quote,
            risk.peak_equity_quote,
            risk.daily_loss_quote,
            risk.daily_loss_fraction,
            risk.max_daily_loss_fraction,
            risk.drawdown_quote,
            risk.drawdown_fraction,
            risk.max_drawdown_fraction,
            risk.current_open_risk_quote,
            risk.simultaneous_risk_cap_quote,
            risk.remaining_simultaneous_risk_quote,
            risk.current_gross_exposure_quote,
            risk.gross_exposure_cap_quote,
            risk.remaining_gross_exposure_quote,
            risk.per_trade_risk_budget_quote,
            risk.approved_quantity,
            risk.approved_notional_quote,
            risk.approved_risk_quote,
            risk.resulting_open_risk_quote,
            risk.resulting_gross_exposure_quote,
            risk.requires_halt_review,
        ),
        DashboardSafetyDecision(
            safety.status.value,
            tuple(reason.value for reason in safety.reasons),
            safety.safety_policy_version,
            safety.observed_at,
            safety.health_source_id.value,
            str(safety.health_symbol),
            safety.health_observed_at,
            safety.market_data_observed_at,
            safety.market_data_age,
            safety.max_market_data_age_seconds,
            safety.feed_integrity_state.value,
            safety.exchange_api_health_state.value,
            safety.spread_bps,
            safety.max_spread_bps,
            safety.time_sync_offset_ms,
            safety.max_abs_time_sync_offset_ms,
            safety.volatility_safety_state.value,
            safety.suspicious_price_anomaly,
            safety.consecutive_order_rejections,
            safety.max_consecutive_order_rejections,
            safety.risk_requires_halt_review,
            safety.approved_risk_stop_basis,
            safety.approved_quantity,
            safety.approved_notional_quote,
            safety.approved_risk_quote,
            safety.entry_permitted,
        ),
        outcome,
    )


def _project_entry_identity(identity: object) -> DashboardEntryIntentIdentity:
    decision = getattr(identity, "decision_identity", None)
    purpose = getattr(identity, "purpose", None)
    schema = getattr(identity, "intent_schema_version", None)
    if (
        not isinstance(decision, DecisionIdentity)
        or not isinstance(purpose, StrEnum)
        or not isinstance(schema, str)
    ):
        raise DashboardJournalProviderError("entry intent identity is not safely serializable")
    return DashboardEntryIntentIdentity(_project_identity(decision), purpose.value, schema)


def _project_trade(entry: CompletedTradeJournalEntry) -> DashboardCompletedTradeRecord:
    identity = entry.identity
    entry_identity = _project_entry_identity(identity.entry_intent_identity)
    paper_identity = identity.paper_order_identity
    exit_identity = identity.exit_intent_identity
    paper_intent = _project_entry_identity(paper_identity.intent_identity)
    exit_entry = _project_entry_identity(exit_identity.originating_entry_intent_identity)
    return DashboardCompletedTradeRecord(
        _project_identity(identity.originating_decision_identity),
        entry_identity,
        DashboardPaperOrderIdentity(paper_intent, paper_identity.simulation_schema_version),
        DashboardExitIntentIdentity(
            exit_entry,
            exit_identity.trigger_observed_at,
            exit_identity.purpose.value,
            exit_identity.exit_intent_schema_version,
        ),
        entry.source_id.value,
        str(entry.symbol),
        entry.strategy_id,
        entry.strategy_version,
        entry.entry_side.value,
        entry.exit_side.value,
        entry.quantity,
        entry.approved_notional_quote,
        entry.approved_risk_quote,
        entry.stop_basis,
        entry.approved_reference_price,
        entry.entry_authorized_at,
        entry.entry_reference_price,
        entry.entry_fill_at,
        entry.entry_fill_price,
        entry.entry_spread_bps,
        entry.entry_slippage_bps,
        entry.entry_fee_bps,
        entry.entry_fee_quote,
        entry.exit_reason.value,
        entry.trigger_observed_at,
        entry.trigger_reference_price,
        entry.exit_authorized_at,
        entry.exit_reference_price,
        entry.exit_fill_at,
        entry.exit_fill_price,
        entry.exit_spread_bps,
        entry.exit_slippage_bps,
        entry.exit_fee_bps,
        entry.exit_fee_quote,
        entry.gross_realized_pnl,
        entry.total_trade_fees_quote,
        entry.net_realized_pnl,
        entry.realized_r_net,
        entry.duration,
        entry.risk_policy_version,
        entry.safety_policy_version,
        entry.accounting_entry_fill_id,
        entry.accounting_exit_fill_id,
        entry.outcome_classification.value,
        DashboardValue.unavailable(_UNAVAILABLE_TRADE_ANALYTICS),
        DashboardValue.unavailable(_UNAVAILABLE_TRADE_ANALYTICS),
        DashboardValue.unavailable(_UNAVAILABLE_TRADE_ANALYTICS),
    )
