"""Fixed BTC/ETH impulse/pullback/resumption screen over accepted 5m LAST data."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from adaptive_trading_bot.domain import FrozenClock
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    _CONTEXT as METRIC_CONTEXT,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    CONTEXT_START,
    DEFAULT_INPUT_AUTHORITY,
    FRAMES,
    RESEARCH_EQUITY,
    SIGNAL_END,
    SIGNAL_START,
    AcceptedInput,
    Scenario,
    ScreenError,
    ScreenInputAuthority,
    _append_jsonl,
    _block,
    _canonical,
    _clean_sha,
    _decision_time_identity,
    _file_sha,
    _MemoryStore,
    _metrics,
    _replace,
    _sha,
    _spacing_mask,
    _text,
    scenario_costs,
)
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mBar,
    Last5mExecutionBinding,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.strategies import (
    TrendImpulsePullbackReasonCode,
    TrendImpulsePullbackRuleAudit,
    TrendImpulsePullbackStrategy,
    assemble_strategy_contexts,
)
from adaptive_trading_bot.strategies.core import CandidateAction

PROTOCOL_ID = "BTC_ETH_IMPULSE_PULLBACK_15M_30M_V1"


def protocol_record() -> dict[str, object]:
    """Return the immutable prospective screen contract."""

    risk = RiskManager()
    safety = OperationalSafetyGate()
    assumptions = ResearchSafetyAssumptionProfile()
    return {
        "protocol": PROTOCOL_ID,
        "strategy": ["trend_impulse_pullback", "0.1.0"],
        "symbols": list(DEFAULT_INPUT_AUTHORITY.shard_map),
        "calendar": {
            "context_start": CONTEXT_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "signal_end_exclusive_open": SIGNAL_END.isoformat(),
            "tail_end": DEFAULT_INPUT_AUTHORITY.tail_end.isoformat(),
            "expected_decision_slots_per_member": 17_280,
        },
        "rule": {
            "decision_timeframe": "15m",
            "impulse_body_min_atr_before_impulse": "1.50",
            "impulse_body_min_range_fraction": "0.70",
            "pullback_candles": 1,
            "pullback_body_fraction": "0.50",
            "stop_buffer_atr14_at_resumption": "0.10",
            "target": "SEALED_DECISION_REFERENCE_FIXED_2R",
            "cost_room_factor": "3",
            "holding_minutes_from_actual_fill": 30,
            "spacing_minutes": 35,
        },
        "scenarios": {
            scenario.value: {
                "spread_bps": str(scenario_costs(scenario).spread_bps),
                "slippage_bps": str(scenario_costs(scenario).slippage_bps),
                "commission_bps": str(scenario_costs(scenario).fee_bps),
                "entry_latency_seconds": str(scenario_costs(scenario).latency.total_seconds()),
            }
            for scenario in Scenario
        },
        "equity_usdt": str(RESEARCH_EQUITY),
        "admission": _text(
            {
                "risk_manager": asdict(risk.configuration),
                "operational_safety": asdict(safety.configuration),
                "research_safety_assumptions": asdict(assumptions),
                "carried_positions": [],
                "equity_compounding": False,
            }
        ),
        "funding_status": "NOT_MODELED",
        "classification": "PREDECLARED_OPERATIONAL_SCREEN_V1",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def _result_record(
    wire: str,
    scenario: Scenario,
    decision: DecisionJournalEntry[object, object],
    result: Last5mProtectiveExecutionResult,
) -> dict[str, Any]:
    audit = decision.strategy_evaluation.rule_audit
    assert isinstance(audit, TrendImpulsePullbackRuleAudit)
    return {
        "symbol": wire,
        "scenario": scenario.value,
        "decision_at": decision.identity.observed_at,
        "side": result.intent_audit.side.value,
        "impulse_open_time": audit.impulse_open_time,
        "impulse": [audit.impulse_open, audit.impulse_high, audit.impulse_low, audit.impulse_close],
        "pullback_open_time": audit.pullback_open_time,
        "pullback": [
            audit.pullback_open,
            audit.pullback_high,
            audit.pullback_low,
            audit.pullback_close,
        ],
        "resumption_open_time": audit.resumption_open_time,
        "resumption": [
            audit.resumption_open,
            audit.resumption_high,
            audit.resumption_low,
            audit.resumption_close,
        ],
        "atr_before_impulse": audit.atr_before_impulse,
        "atr_at_resumption": audit.atr_at_resumption,
        "impulse_body": audit.impulse_body,
        "impulse_range": audit.impulse_range,
        "retracement_boundary": audit.retracement_boundary,
        "decision_reference": decision.strategy_evaluation.reference_price,
        "sealed_target": audit.sealed_target,
        "stop": result.original_stop_basis,
        "quantity": result.intent_audit.quantity,
        "approved_risk_quote": result.approved_risk_quote,
        "status": result.status.value,
        "entry_eligible_at": result.entry_eligible_at,
        "entry_grid_wait_seconds": (
            None if result.entry_grid_wait is None else result.entry_grid_wait.total_seconds()
        ),
        "entry": None if result.entry_fill is None else _text(asdict(result.entry_fill)),
        "exit": None if result.exit_fill is None else _text(asdict(result.exit_fill)),
        "trigger": None if result.trigger is None else _text(asdict(result.trigger)),
        "gross_pnl_quote": result.realized_gross_pnl,
        "total_fees_quote": result.total_fees,
        "net_pnl_quote": result.realized_net_pnl,
        "closed_net_r": result.closed_net_approved_risk_r,
        "unresolved_quantity": result.unresolved_quantity,
        "holding_lower_seconds": (
            None
            if result.holding_duration_lower is None
            else result.holding_duration_lower.total_seconds()
        ),
        "holding_upper_seconds": (
            None
            if result.holding_duration_upper is None
            else result.holding_duration_upper.total_seconds()
        ),
        "events": list(result.events),
        "series_identity": result.series_identity,
        "binding_identity": result.binding.identity,
    }


def run_member(
    wire: str,
    store: _MemoryStore,
    five: tuple[Last5mBar, ...],
    output: Path,
    *,
    context_start: datetime = CONTEXT_START,
    signal_start: datetime = SIGNAL_START,
    signal_end: datetime = SIGNAL_END,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate one member through shared replay/admission/execution authorities."""

    symbol = store.symbol
    source_id = store.source_id
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        tuple(
            ReplaySeries(source_id, symbol, frame, context_start, signal_end) for frame in FRAMES
        ),
        clock=FrozenClock(context_start),
    )
    features = FeatureEngine()
    structures = MarketStructureEngine()
    regimes = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    strategy = TrendImpulsePullbackStrategy()
    risk = RiskManager()
    safety = OperationalSafetyGate()
    assumptions = ResearchSafetyAssumptionProfile()
    counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    decision_count = 0
    admitted_times: list[datetime] = []
    five_by_open = {bar.open_time: bar for bar in five}
    trade_rows: list[dict[str, Any]] = []
    last_heartbeat = datetime.now(UTC)
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        feature_batch = features.process(observed)
        structure_batch = structures.process(observed)
        regime_batch = regimes.process(feature_batch, structure_batch)
        mtf = composer.process(regime_batch)
        for context in assemble_strategy_contexts(observed, feature_batch, structure_batch, mtf):
            evaluation = strategy.evaluate(context)
            if not signal_start < context.observed_at <= signal_end:
                continue
            counts["observations"] += 1
            for reason in evaluation.reasons_against:
                reason_counts[str(reason)] += 1
            if evaluation.action is not CandidateAction.NO_TRADE:
                counts["raw_setups"] += 1
            elif (
                TrendImpulsePullbackReasonCode.BASE_COST_ROOM_REJECTED in evaluation.reasons_against
            ):
                counts["raw_setups"] += 1
                counts["cost_rejected"] += 1
            portfolio = RiskPortfolioSnapshot(
                context.observed_at,
                "USDT",
                RESEARCH_EQUITY,
                RESEARCH_EQUITY,
                RESEARCH_EQUITY,
                (),
            )
            risk_decision = risk.evaluate(evaluation, portfolio)
            if (
                evaluation.action is not CandidateAction.NO_TRADE
                and risk_decision.status is RiskDecisionStatus.REJECTED
            ):
                counts["risk_rejected"] += 1
            safety_decision = safety.evaluate(risk_decision, _research_health(context, assumptions))
            if (
                risk_decision.status is RiskDecisionStatus.APPROVED
                and safety_decision.status is not OperationalSafetyStatus.CLEARED
            ):
                counts["safety_rejected"] += 1
            decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
            decision.validate()
            decision_count += 1
            authorization = EntryOrderIntentAuthorizer().authorize(decision)
            if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
                continue
            intent = authorization.intent
            assert intent is not None
            counts["admitted"] += 1
            admitted_times.append(context.observed_at)
            audit = evaluation.rule_audit
            assert isinstance(audit, TrendImpulsePullbackRuleAudit)
            assert audit.sealed_target is not None
            start = context.observed_at
            end = start + timedelta(minutes=40)
            try:
                bars = tuple(
                    five_by_open[start + timedelta(minutes=5 * index)] for index in range(8)
                )
            except KeyError as error:
                raise ScreenError(
                    "execution tail is incomplete for an admitted decision"
                ) from error
            binding = Last5mExecutionBinding(source_id, symbol, bars[0].epoch_id, start, end)
            for scenario in Scenario:
                result = run_last_5m_protective_execution(
                    intent=intent,
                    binding=binding,
                    bars=bars,
                    configuration=Last5mProtectionConfiguration(
                        scenario_costs(scenario),
                        ExitPolicy.FROZEN_TARGET_30M,
                        ProtectionAcknowledgement.ACCEPTED,
                        timedelta(0),
                        frozen_target_price=audit.sealed_target,
                        holding_period=timedelta(minutes=30),
                    ),
                )
                row = _result_record(wire, scenario, decision, result)
                trade_rows.append(row)
                _append_jsonl(output / "trades.jsonl", row)
        if datetime.now(UTC) - last_heartbeat >= timedelta(seconds=30):
            _replace(
                output / "progress.json",
                {
                    "phase": "EVALUATING",
                    "symbol": wire,
                    "observed_at": batch.observed_at,
                    "counts": dict(counts),
                    "trade_scenario_rows": len(trade_rows),
                    "heartbeat_at": datetime.now(UTC),
                },
            )
            last_heartbeat = datetime.now(UTC)
    expected_slots = int((signal_end - signal_start) / timedelta(minutes=15))
    if counts["observations"] != expected_slots:
        raise ScreenError("decision-slot population is incomplete")
    spaced = _spacing_mask(admitted_times)
    explicit_counts = {
        name: counts[name]
        for name in (
            "observations",
            "raw_setups",
            "cost_rejected",
            "risk_rejected",
            "safety_rejected",
            "admitted",
        )
    }
    return (
        {
            "symbol": wire,
            "counts": explicit_counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "decision_count": decision_count,
            "spacing_mask": [item.isoformat() for item in spaced],
            "spacing_mask_count": len(spaced),
        },
        trade_rows,
    )


def _study_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _metrics(rows)
    with localcontext(METRIC_CONTEXT):
        values = [
            Decimal(str(row["closed_net_r"])) for row in rows if row.get("closed_net_r") is not None
        ]
        positive = sum((value for value in values if value > 0), Decimal(0))
        negative = -sum((value for value in values if value < 0), Decimal(0))
    if negative > 0:
        metrics["profit_factor_status"] = "NUMERIC"
    elif positive > 0:
        metrics["profit_factor_status"] = "UNBOUNDED_ZERO_LOSS"
    else:
        metrics["profit_factor_status"] = "UNAVAILABLE_NO_POSITIVE_PROFIT"
    return metrics


def _profit_factor_passes(metrics: dict[str, Any]) -> bool:
    return metrics["profit_factor_status"] == "UNBOUNDED_ZERO_LOSS" or (
        metrics["profit_factor_status"] == "NUMERIC"
        and metrics["profit_factor"] is not None
        and metrics["profit_factor"] > 1
    )


def _projection(
    run_id: str,
    executable_sha: str,
    members: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    shards = authority.shard_map
    by_member = {str(member["symbol"]): member for member in members}
    if set(by_member) != set(shards):
        raise ScreenError("member population does not exactly match the fixed panel")
    table: dict[str, Any] = {}
    complete = True
    candidate = True
    for wire in shards:
        mask = set(by_member[wire]["spacing_mask"])
        for scenario in Scenario:
            full = [
                row for row in rows if row["symbol"] == wire and row["scenario"] == scenario.value
            ]
            spaced = [row for row in full if _decision_time_identity(row["decision_at"]) in mask]
            table[f"{wire}:{scenario.value}:FULL"] = _study_metrics(full)
            table[f"{wire}:{scenario.value}:SPACED"] = _study_metrics(spaced)
            full_metrics = table[f"{wire}:{scenario.value}:FULL"]
            spaced_metrics = table[f"{wire}:{scenario.value}:SPACED"]
            if full_metrics["unresolved"] or spaced_metrics["unresolved"]:
                complete = False
            if (
                spaced_metrics["closed"] < 30
                or full_metrics["mean_net_r"] is None
                or spaced_metrics["mean_net_r"] is None
                or full_metrics["mean_net_r"] <= 0
                or spaced_metrics["mean_net_r"] <= 0
                or not _profit_factor_passes(full_metrics)
                or not _profit_factor_passes(spaced_metrics)
            ):
                candidate = False
    blocks: dict[str, Any] = {}
    for wire in shards:
        mask = set(by_member[wire]["spacing_mask"])
        for scenario in Scenario:
            for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3"):
                values = [
                    row
                    for row in rows
                    if row["symbol"] == wire
                    and row["scenario"] == scenario.value
                    and _decision_time_identity(row["decision_at"]) in mask
                    and _block(
                        datetime.fromisoformat(_decision_time_identity(row["decision_at"]))
                        - timedelta(minutes=15),
                        authority.signal_start,
                    )
                    == block
                ]
                blocks[f"{wire}:{scenario.value}:{block}"] = _study_metrics(values)
        positive_base_blocks = sum(
            blocks[f"{wire}:BASE:{block}"]["mean_net_r"] is not None
            and blocks[f"{wire}:BASE:{block}"]["mean_net_r"] > 0
            for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3")
        )
        if positive_base_blocks < 2:
            candidate = False
    if not complete:
        classification = "INCOMPLETE_ECONOMIC_EVIDENCE"
    elif any(
        table[f"{wire}:{scenario.value}:SPACED"]["closed"] < 30
        for wire in shards
        for scenario in Scenario
    ):
        classification = "INSUFFICIENT_EVIDENCE"
    elif candidate:
        classification = "CANDIDATE_FOR_REVIEW_PRE_FUNDING"
    else:
        means = [table[key]["mean_net_r"] for key in table if table[key]["mean_net_r"] is not None]
        classification = (
            "NEGATIVE" if means and all(value <= 0 for value in means) else "MIXED_FRAGILE"
        )
    return {
        "schema": "trend-impulse-pullback-screen-result-v1",
        "protocol": PROTOCOL_ID,
        "protocol_sha256": PROTOCOL_SHA256,
        "run_id": run_id,
        "executable_sha": executable_sha,
        "source_id": authority.source_id.value,
        "dataset_version": authority.dataset_version,
        "input_hashes": {
            "manifest": authority.manifest_sha256,
            "report": authority.report_sha256,
            "plan": authority.plan_sha256,
            **{f"{wire}-5m": digest for wire, digest in shards.items()},
        },
        "members": members,
        "economic_table": table,
        "chronological_60d_blocks": blocks,
        "classification": classification,
        "promotion": (
            "REVIEW_ONLY"
            if classification == "CANDIDATE_FOR_REVIEW_PRE_FUNDING"
            else "NO_PROMOTION"
        ),
        "limitations": [
            "INDEPENDENT_OPPORTUNITIES_NOT_SHARED_CAPITAL_PORTFOLIO",
            "NET_AFTER_MODELED_COMMISSION_SPREAD_SLIPPAGE_BEFORE_FUNDING_AND_OTHER_PERPETUAL_MECHANICS",
            "LAST_5M_BAR_MODEL_NOT_TICK_ACCURATE_OR_GUARANTEED_VENUE_EXECUTION",
            "SELECTION_INFORMED_EXPLORATORY_DATA_NOT_UNTOUCHED_HOLDOUT",
        ],
    }


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    """Run the single historical screen and durably persist compact results."""

    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ScreenError("run output already exists; historical retry is forbidden")
    _clean_sha(repo, executable_sha)
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": PROTOCOL_SHA256,
            "started_at": datetime.now(UTC),
            "historical_attempt": 1,
        },
    )
    _replace(output / "protocol.json", protocol_record())
    accepted = AcceptedInput(repo, authority)
    members: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for wire in authority.shard_map:
        store, five = accepted.load(wire)
        member, member_rows = run_member(
            wire,
            store,
            five,
            output,
            context_start=authority.context_start,
            signal_start=authority.signal_start,
            signal_end=authority.signal_end,
        )
        members.append(member)
        rows.extend(member_rows)
        _replace(output / f"{wire.lower()}-member.json", member)
    projection = _projection(run_id, executable_sha, members, rows, authority)
    _replace(output / "projection.json", projection)
    _replace(
        output / "complete.json",
        {
            "completed_at": datetime.now(UTC),
            "projection_sha256": _file_sha(output / "projection.json"),
            "trade_rows": len(rows),
            "status": "COMPLETE",
        },
    )
    return projection
