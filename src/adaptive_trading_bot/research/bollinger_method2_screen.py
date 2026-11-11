"""Fixed BTC/ETH Bollinger Method-II MFI entry ablation."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import Any

from adaptive_trading_bot.domain import FrozenClock, Timeframe
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
from adaptive_trading_bot.research.trend_impulse_pullback_screen import _study_metrics
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
from adaptive_trading_bot.strategies.bollinger_method2 import (
    CONFIRMED_ID,
    CONTROL_ID,
    BollingerMethod2Audit,
    BollingerMethod2Configuration,
    BollingerMethod2HourlyContext,
    BollingerMethod2Strategy,
)
from adaptive_trading_bot.strategies.context import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import CandidateAction

PROTOCOL_ID = "BTC_ETH_BB_METHOD2_MFI_ABLATION_1H_30M_V1"
ARMS = (CONTROL_ID, CONFIRMED_ID)
STRATEGY_VERSION = "0.2.0"
DECISION_CADENCE = timedelta(hours=1)
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


def protocol_record() -> dict[str, object]:
    return {
        "protocol": PROTOCOL_ID,
        "arms": [[CONTROL_ID, STRATEGY_VERSION], [CONFIRMED_ID, STRATEGY_VERSION]],
        "source_signal": {
            "decision_timeframe": "1h",
            "indicator_input": "ACTUAL_COMPLETED_1H_OHLCV",
            "bb_period": 20,
            "population_sigma": "2",
            "price_long": "%b>0.8",
            "price_short": "%b<0.2",
            "mfi_period": 10,
            "mfi_long": ">80",
            "mfi_short": "<20",
        },
        "project_adaptation": {
            "stop": "OPPOSITE_FROZEN_BAND",
            "target": "DECISION_FIXED_2R",
            "holding_minutes": 30,
            "execution": "LAST_5M_PROTECTIVE_EXECUTION_V1",
        },
        "calendar": {
            "context_start": CONTEXT_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "signal_end": SIGNAL_END.isoformat(),
            "decision_close_first": (SIGNAL_START + DECISION_CADENCE).isoformat(),
            "decision_close_last": SIGNAL_END.isoformat(),
            "expected_slots_per_asset_arm": 4320,
        },
        "scenarios": {s.value: _text(asdict(scenario_costs(s))) for s in Scenario},
        "equity_usdt": str(RESEARCH_EQUITY),
        "funding": "NOT_MODELED",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def _row(
    wire: str,
    arm: str,
    scenario: Scenario,
    decision: DecisionJournalEntry[object, object],
    result: Last5mProtectiveExecutionResult,
) -> dict[str, Any]:
    audit = decision.strategy_evaluation.rule_audit
    assert isinstance(audit, BollingerMethod2Audit)
    return {
        "symbol": wire,
        "arm": arm,
        "scenario": scenario.value,
        "decision_at": decision.identity.observed_at,
        "side": result.intent_audit.side.value,
        "percent_b": audit.percent_b,
        "mfi10": audit.mfi10,
        "sma20": audit.sma20,
        "upper_band": audit.upper_band,
        "lower_band": audit.lower_band,
        "reference": audit.decision_reference,
        "stop": result.original_stop_basis,
        "target": audit.frozen_target,
        "quantity": result.intent_audit.quantity,
        "approved_risk_quote": result.approved_risk_quote,
        "status": result.status.value,
        "entry_grid_wait_seconds": None
        if result.entry_grid_wait is None
        else result.entry_grid_wait.total_seconds(),
        "entry": None if result.entry_fill is None else _text(asdict(result.entry_fill)),
        "exit": None if result.exit_fill is None else _text(asdict(result.exit_fill)),
        "trigger": None if result.trigger is None else _text(asdict(result.trigger)),
        "gross_pnl_quote": result.realized_gross_pnl,
        "total_fees_quote": result.total_fees,
        "net_pnl_quote": result.realized_net_pnl,
        "closed_net_r": result.closed_net_approved_risk_r,
        "unresolved_quantity": result.unresolved_quantity,
        "holding_lower_seconds": None
        if result.holding_duration_lower is None
        else result.holding_duration_lower.total_seconds(),
        "holding_upper_seconds": None
        if result.holding_duration_upper is None
        else result.holding_duration_upper.total_seconds(),
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
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        tuple(
            ReplaySeries(store.source_id, store.symbol, f, context_start, signal_end)
            for f in FRAMES
        ),
        clock=FrozenClock(context_start),
    )
    features, structures, regimes, composer = (
        FeatureEngine(),
        MarketStructureEngine(),
        TechnicalRegimeEngine(),
        MultiTimeframeRegimeComposer(),
    )
    strategies = {
        CONTROL_ID: BollingerMethod2Strategy(BollingerMethod2Configuration(False)),
        CONFIRMED_ID: BollingerMethod2Strategy(BollingerMethod2Configuration(True)),
    }
    risk, safety, assumptions = (
        RiskManager(),
        OperationalSafetyGate(),
        ResearchSafetyAssumptionProfile(),
    )
    counts: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    reasons: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    control_admissions: list[datetime] = []
    rows: list[dict[str, Any]] = []
    five_by_open = {b.open_time: b for b in five}
    last_heartbeat = datetime.now(UTC)
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        hourly_candles = {
            event.candle.symbol: event.candle
            for event in observed.events
            if event.candle.timeframe is Timeframe.HOUR_1
        }
        fb = features.process(observed)
        sb = structures.process(observed)
        rb = regimes.process(fb, sb)
        mtf = composer.process(rb)
        for base_context in assemble_strategy_contexts(observed, fb, sb, mtf):
            hourly_candle = hourly_candles.get(base_context.symbol)
            if hourly_candle is None:
                continue
            context = BollingerMethod2HourlyContext(
                base_context.source_id,
                base_context.symbol,
                base_context.observed_at,
                base_context.candle_15m,
                base_context.features_15m,
                base_context.structure_15m,
                base_context.multi_timeframe_regime,
                hourly_candle,
            )
            evaluations = {arm: strategy.evaluate(context) for arm, strategy in strategies.items()}
            if not signal_start < context.observed_at <= signal_end:
                continue
            if (
                evaluations[CONFIRMED_ID].action is not CandidateAction.NO_TRADE
                and evaluations[CONTROL_ID].action is CandidateAction.NO_TRADE
            ):
                raise ScreenError("confirmed signal is not a control subset")
            for arm, evaluation in evaluations.items():
                counts[arm]["observations"] += 1
                for reason in evaluation.reasons_against:
                    reasons[arm][str(reason)] += 1
                if evaluation.action is not CandidateAction.NO_TRADE:
                    counts[arm]["signals"] += 1
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
                    counts[arm]["risk_rejected"] += 1
                safety_decision = safety.evaluate(
                    risk_decision, _research_health(context, assumptions)
                )
                if (
                    risk_decision.status is RiskDecisionStatus.APPROVED
                    and safety_decision.status is not OperationalSafetyStatus.CLEARED
                ):
                    counts[arm]["safety_rejected"] += 1
                decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                decision.validate()
                authorization = EntryOrderIntentAuthorizer().authorize(decision)
                if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
                    continue
                intent = authorization.intent
                assert intent is not None
                counts[arm]["admitted"] += 1
                if arm == CONTROL_ID:
                    control_admissions.append(context.observed_at)
                audit = evaluation.rule_audit
                assert isinstance(audit, BollingerMethod2Audit) and audit.frozen_target is not None
                start = context.observed_at
                bars = tuple(five_by_open[start + timedelta(minutes=5 * i)] for i in range(8))
                binding = Last5mExecutionBinding(
                    store.source_id,
                    store.symbol,
                    bars[0].epoch_id,
                    start,
                    start + timedelta(minutes=40),
                )
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
                            frozen_target_price=audit.frozen_target,
                            holding_period=timedelta(minutes=30),
                        ),
                    )
                    record = _row(wire, arm, scenario, decision, result)
                    rows.append(record)
                    _append_jsonl(output / "trades.jsonl", record)
        if datetime.now(UTC) - last_heartbeat >= timedelta(seconds=30):
            _replace(
                output / "progress.json",
                {
                    "phase": "EVALUATING",
                    "symbol": wire,
                    "observed_at": batch.observed_at,
                    "counts": {a: dict(c) for a, c in counts.items()},
                    "rows": len(rows),
                    "heartbeat_at": datetime.now(UTC),
                },
            )
            last_heartbeat = datetime.now(UTC)
    expected = int((signal_end - signal_start) / DECISION_CADENCE)
    if any(counts[a]["observations"] != expected for a in ARMS):
        raise ScreenError("observation population incomplete")
    mask = _spacing_mask(control_admissions)
    return {
        "symbol": wire,
        "arms": {
            a: {
                "counts": {
                    k: counts[a][k]
                    for k in (
                        "observations",
                        "signals",
                        "risk_rejected",
                        "safety_rejected",
                        "admitted",
                    )
                },
                "reason_counts": dict(sorted(reasons[a].items())),
            }
            for a in ARMS
        },
        "common_spacing_mask": [x.isoformat() for x in mask],
        "common_spacing_mask_count": len(mask),
    }, rows


def _projection(
    run_id: str,
    executable_sha: str,
    members: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    table: dict[str, Any] = {}
    blocks: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    by_member = {m["symbol"]: m for m in members}
    for wire in authority.shard_map:
        mask = set(by_member[wire]["common_spacing_mask"])
        for scenario in Scenario:
            arm_rows = {
                a: [
                    r
                    for r in rows
                    if r["symbol"] == wire and r["scenario"] == scenario.value and r["arm"] == a
                ]
                for a in ARMS
            }
            for arm in ARMS:
                full = arm_rows[arm]
                spaced = [r for r in full if _decision_time_identity(r["decision_at"]) in mask]
                table[f"{wire}:{scenario.value}:{arm}:FULL"] = _study_metrics(full)
                table[f"{wire}:{scenario.value}:{arm}:COMMON_SPACED"] = _study_metrics(spaced)
            control_ids = {_decision_time_identity(r["decision_at"]) for r in arm_rows[CONTROL_ID]}
            confirmed_ids = {
                _decision_time_identity(r["decision_at"]) for r in arm_rows[CONFIRMED_ID]
            }
            if not confirmed_ids <= control_ids:
                raise ScreenError("confirmed admissions are not a control subset")
            retained = [
                r
                for r in arm_rows[CONTROL_ID]
                if _decision_time_identity(r["decision_at"]) in confirmed_ids
            ]
            excluded = [
                r
                for r in arm_rows[CONTROL_ID]
                if _decision_time_identity(r["decision_at"]) not in confirmed_ids
            ]
            confirmed_by_id = {
                _decision_time_identity(r["decision_at"]): r for r in arm_rows[CONFIRMED_ID]
            }
            for row in retained:
                peer = confirmed_by_id[_decision_time_identity(row["decision_at"])]
                fields = (
                    "side",
                    "reference",
                    "stop",
                    "target",
                    "quantity",
                    "approved_risk_quote",
                    "status",
                    "entry_grid_wait_seconds",
                    "trigger",
                    "gross_pnl_quote",
                    "total_fees_quote",
                    "net_pnl_quote",
                    "closed_net_r",
                    "unresolved_quantity",
                    "holding_lower_seconds",
                    "holding_upper_seconds",
                )
                if any(_text(row[field]) != _text(peer[field]) for field in fields):
                    raise ScreenError("common admission economics differ between arms")
                for fill_name in ("entry", "exit"):
                    left_fill = row[fill_name]
                    right_fill = peer[fill_name]
                    if (left_fill is None) != (right_fill is None):
                        raise ScreenError("common admission fill status differs between arms")
                    if left_fill is not None and right_fill is not None:
                        economic_fill_fields = (
                            "side",
                            "fill_at",
                            "reference_price",
                            "fill_price",
                            "quantity",
                            "fee_quote",
                        )
                        if any(
                            _text(left_fill[name]) != _text(right_fill[name])
                            for name in economic_fill_fields
                        ):
                            raise ScreenError("common admission fill economics differ between arms")
            retained_metrics = _study_metrics(retained)
            excluded_metrics = _study_metrics(excluded)
            comparisons[f"{wire}:{scenario.value}"] = {
                "control_admissions": len(control_ids),
                "confirmed_admissions": len(confirmed_ids),
                "retained_control": retained_metrics,
                "excluded_control": excluded_metrics,
                "confirmed_minus_control_full_mean_net_r": _decimal_difference(
                    table[f"{wire}:{scenario.value}:{CONFIRMED_ID}:FULL"]["mean_net_r"],
                    table[f"{wire}:{scenario.value}:{CONTROL_ID}:FULL"]["mean_net_r"],
                ),
                "retained_minus_excluded_control_mean_net_r": _decimal_difference(
                    retained_metrics["mean_net_r"], excluded_metrics["mean_net_r"]
                ),
            }
            for arm in ARMS:
                for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3"):
                    vals = [
                        r
                        for r in arm_rows[arm]
                        if _decision_time_identity(r["decision_at"]) in mask
                        and _block(
                            datetime.fromisoformat(_decision_time_identity(r["decision_at"]))
                            - DECISION_CADENCE,
                            authority.signal_start,
                        )
                        == block
                    ]
                    blocks[f"{wire}:{scenario.value}:{arm}:{block}"] = _study_metrics(vals)
    classification = _classify_projection(table, blocks, tuple(authority.shard_map))
    return {
        "schema": "bb-method2-mfi-hourly-ablation-result-v1",
        "protocol": PROTOCOL_ID,
        "protocol_sha256": PROTOCOL_SHA256,
        "run_id": run_id,
        "executable_sha": executable_sha,
        "input_hashes": {
            "manifest": authority.manifest_sha256,
            "report": authority.report_sha256,
            "plan": authority.plan_sha256,
            **{f"{w}-5m": h for w, h in authority.shards},
        },
        "members": members,
        "economic_table": table,
        "chronological_blocks": blocks,
        "selection_comparison": comparisons,
        "classification": classification,
        "promotion": "REVIEW_ONLY"
        if classification == "REVIEW_ONLY_PRE_FUNDING"
        else "NO_PROMOTION",
        "filter_effect": "DESCRIPTIVE_NOT_CAUSAL",
    }


def _profit_factor_passes(metrics: dict[str, Any]) -> bool:
    return metrics["profit_factor_status"] == "UNBOUNDED_ZERO_LOSS" or (
        metrics["profit_factor_status"] == "NUMERIC"
        and metrics["profit_factor"] is not None
        and metrics["profit_factor"] > 1
    )


def _classify_projection(
    table: dict[str, Any], blocks: dict[str, Any], wires: tuple[str, ...]
) -> str:
    confirmed = [
        table[f"{wire}:{scenario.value}:{CONFIRMED_ID}:{view}"]
        for wire in wires
        for scenario in Scenario
        for view in ("FULL", "COMMON_SPACED")
    ]
    if any(metric["unresolved"] for metric in confirmed):
        return "INCOMPLETE_ECONOMIC_EVIDENCE"
    if any(
        table[f"{wire}:{scenario.value}:{CONFIRMED_ID}:COMMON_SPACED"]["closed"] < 30
        for wire in wires
        for scenario in Scenario
    ):
        return "INSUFFICIENT_EVIDENCE"
    candidate = all(
        metric["mean_net_r"] is not None
        and metric["mean_net_r"] > 0
        and _profit_factor_passes(metric)
        for metric in confirmed
    )
    for wire in wires:
        positive_base_blocks = sum(
            blocks[f"{wire}:BASE:{CONFIRMED_ID}:{block}"]["mean_net_r"] is not None
            and blocks[f"{wire}:BASE:{CONFIRMED_ID}:{block}"]["mean_net_r"] > 0
            for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3")
        )
        if positive_base_blocks < 2:
            candidate = False
    if candidate:
        return "REVIEW_ONLY_PRE_FUNDING"
    means = [metric["mean_net_r"] for metric in confirmed]
    return (
        "NEGATIVE_ADAPTATION"
        if all(value is not None and value <= 0 for value in means)
        else "MIXED_FRAGILE"
    )


def _decimal_difference(left: object, right: object) -> Decimal | None:
    if left is None or right is None:
        return None
    with localcontext(_DECIMAL_CONTEXT):
        return Decimal(str(left)) - Decimal(str(right))


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ScreenError("output already exists")
    _clean_sha(repo, executable_sha)
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": PROTOCOL_SHA256,
            "started_at": datetime.now(UTC),
        },
    )
    _replace(output / "protocol.json", protocol_record())
    accepted = AcceptedInput(repo, authority)
    members = []
    rows = []
    for wire in authority.shard_map:
        store, five = accepted.load(wire)
        member, new = run_member(
            wire,
            store,
            five,
            output,
            context_start=authority.context_start,
            signal_start=authority.signal_start,
            signal_end=authority.signal_end,
        )
        members.append(member)
        rows.extend(new)
        _replace(output / f"{wire.lower()}-member.json", member)
    projection = _projection(run_id, executable_sha, members, rows, authority)
    _replace(output / "projection.json", projection)
    _replace(
        output / "complete.json",
        {
            "status": "COMPLETE",
            "trade_rows": len(rows),
            "projection_sha256": _file_sha(output / "projection.json"),
            "completed_at": datetime.now(UTC),
        },
    )
    return projection
