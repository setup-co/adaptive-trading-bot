"""Fixed BTC/ETH Raschke-informed first-pullback depth-filter ablation."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path
from statistics import median
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
from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    FOLLOWUP_END,
    _metrics,
    _peak_concurrency,
    _terminal_valuation,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    _CONTEXT,
    DEFAULT_INPUT_AUTHORITY,
    FRAMES,
    RESEARCH_EQUITY,
    AcceptedInput,
    Scenario,
    ScreenError,
    ScreenInputAuthority,
    _append_jsonl,
    _block,
    _canonical,
    _clean_sha,
    _file_sha,
    _MemoryStore,
    _replace,
    _sha,
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
    select_no_time_exit_prefix,
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
from adaptive_trading_bot.strategies import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import CandidateAction
from adaptive_trading_bot.strategies.raschke_pullback import (
    POLICY_ID,
    RaschkePullbackArm,
    RaschkePullbackAudit,
    RaschkePullbackStrategy,
)

PROTOCOL_ID = "BTC_ETH_RASCHKE_PULLBACK_FIB_ABLATION_15M_V1"
ARMS = tuple(arm.value for arm in RaschkePullbackArm)
EXPECTED_SLOTS = 17_280


def protocol_record(
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    *,
    followup_end: datetime = FOLLOWUP_END,
) -> dict[str, object]:
    """Return the effective, hash-bound scientific contract."""

    risk, safety, assumptions = (
        RiskManager(),
        OperationalSafetyGate(),
        ResearchSafetyAssumptionProfile(),
    )
    return {
        "protocol": PROTOCOL_ID,
        "policy": POLICY_ID,
        "strategy_arms": [
            [RaschkePullbackArm.REFERENCE.value, "0.1.0", "ALL_VALID_CONFIRMATIONS"],
            [RaschkePullbackArm.FIB_ZONE.value, "0.1.0", "0.382_LE_DEPTH_LE_0.618"],
            [RaschkePullbackArm.ROUND_ZONE.value, "0.1.0", "0.40_LE_DEPTH_LE_0.60"],
        ],
        "symbols": list(authority.shard_map),
        "calendar": {
            "context_start": authority.context_start.isoformat(),
            "decision_start_exclusive": authority.signal_start.isoformat(),
            "decision_end_inclusive": authority.signal_end.isoformat(),
            "followup_end": followup_end.isoformat(),
            "expected_slots_per_member_arm": EXPECTED_SLOTS,
        },
        "rule": {
            "decision_timeframe": "15m",
            "adx": "WILDER_14_14DM_THEN_14DX_SEED",
            "arming": "ADX_GT_30_AND_RISING_DI_DIRECTION_WHOLE_CANDLE_BEYOND_EMA20",
            "episode": "ONE_FIRST_EMA20_TOUCH_THEN_FIRST_PRIOR_BAR_RESUMPTION_CLOSE",
            "origin": "LATEST_CAUSALLY_CONFIRMED_OPPOSITE_PIVOT_AT_ARMING_OPTIONAL",
            "entry": "CONFIRMATION_CLOSE",
            "stop": "PULLBACK_EXTREME_P",
            "target": "FROZEN_PRE_TOUCH_FAVORABLE_EXTREME_B",
            "depth": "LONG_(B-P)/(B-A)_SHORT_(P-B)/(A-B)",
            "holding_timer": None,
            "terminal_valuation": "HYPOTHETICAL_DATA_END_CLOSE_SEPARATE_FROM_REALIZED",
        },
        "scenarios": {
            scenario.value: _text(asdict(scenario_costs(scenario))) for scenario in Scenario
        },
        "admission": _text(
            {
                "equity_usdt": RESEARCH_EQUITY,
                "risk_manager": asdict(risk.configuration),
                "operational_safety": asdict(safety.configuration),
                "research_safety": asdict(assumptions),
                "equity_compounding": False,
            }
        ),
        "capacity_diagnostic": "ONE_POSITION_PER_ASSET_ARM_SCENARIO_CHRONOLOGICAL",
        "classification": "PREDECLARED_NO_PROMOTION_ABLATION_V1",
        "input_hashes": {
            "manifest": authority.manifest_sha256,
            "report": authority.report_sha256,
            "plan": authority.plan_sha256,
            **{f"{wire}-5m": digest for wire, digest in authority.shards},
        },
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def _row(
    wire: str,
    arm: str,
    scenario: Scenario,
    decision: DecisionJournalEntry[object, object],
    result: Last5mProtectiveExecutionResult,
    terminal_bar: Last5mBar,
) -> dict[str, Any]:
    audit = decision.strategy_evaluation.rule_audit
    assert isinstance(audit, RaschkePullbackAudit)
    valuation = _terminal_valuation(result, terminal_bar)
    return {
        "pair_key": [wire, arm, scenario.value, audit.episode_id],
        "opportunity_key": [wire, audit.episode_id, audit.confirmation_at],
        "symbol": wire,
        "arm": arm,
        "scenario": scenario.value,
        "decision_at": decision.identity.observed_at,
        "side": result.intent_audit.side.value,
        "audit": _text(asdict(audit)),
        "entry": None if result.entry_fill is None else _text(asdict(result.entry_fill)),
        "authority": {
            "approved_risk_quote": result.approved_risk_quote,
            "quantity": result.intent_audit.quantity,
            "stop_basis": result.original_stop_basis,
            "intended_target_price": audit.frozen_target,
            "configured_target_price": result.configuration.frozen_target_price,
            "execution_target_price": result.target_price,
            "exit_policy": result.exit_policy.value,
            "decision_identity": _text(asdict(result.decision_identity)),
            "configuration": _text(asdict(result.configuration)),
        },
        "new": {
            "status": result.status.value,
            "trigger": None if result.trigger is None else result.trigger.kind.value,
            "natural_closed": result.position_closed,
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
            "series_identity": result.series_identity,
            "binding_identity": result.binding.identity,
        },
        "terminal_valuation": valuation,
    }


def _required_decimal(value: object, *, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ScreenError(f"{field} is unavailable")
    try:
        result = Decimal(str(value))
    except (DecimalException, ValueError) as error:
        raise ScreenError(f"{field} is not a Decimal value") from error
    if not result.is_finite():
        raise ScreenError(f"{field} is not finite")
    return result


def _optional_decimal(value: object, *, field: str) -> Decimal | None:
    return None if value is None else _required_decimal(value, field=field)


def _intended_target(row: dict[str, Any]) -> Decimal:
    audit = row.get("audit")
    authority = row.get("authority")
    if not isinstance(audit, dict) or not isinstance(authority, dict):
        raise ScreenError("row target authorities are malformed")
    configuration = authority.get("configuration")
    if not isinstance(configuration, dict):
        raise ScreenError("row execution configuration is malformed")
    candidates = {
        "audit.frozen_target": audit.get("frozen_target"),
        "configuration.frozen_target_price": configuration.get("frozen_target_price"),
    }
    if "intended_target_price" in authority:
        candidates["authority.intended_target_price"] = authority["intended_target_price"]
    if "configured_target_price" in authority:
        candidates["authority.configured_target_price"] = authority["configured_target_price"]
    parsed = {name: _required_decimal(value, field=name) for name, value in candidates.items()}
    values = set(parsed.values())
    if len(values) != 1:
        raise ScreenError("decision-frozen target authorities disagree")
    target = values.pop()
    if target <= 0:
        raise ScreenError("decision-frozen intended target is not positive")
    return target


def _execution_target(row: dict[str, Any]) -> Decimal | None:
    authority = row.get("authority")
    new = row.get("new")
    if not isinstance(authority, dict) or not isinstance(new, dict):
        raise ScreenError("row execution target facts are malformed")
    value = (
        authority.get("execution_target_price")
        if "execution_target_price" in authority
        else authority.get("frozen_target_price")
    )
    target = _optional_decimal(value, field="authority.execution_target_price")
    status = str(new.get("status"))
    nullable = {
        "ENTRY_UNFILLED",
        "INVALID_GEOMETRY_RECOVERED",
        "INVALID_GEOMETRY_UNRESOLVED",
    }
    if target is None and status not in nullable:
        raise ScreenError("execution target is unexpectedly unavailable")
    if target is not None and target != _intended_target(row):
        raise ScreenError("execution target differs from the decision-frozen target")
    return target


def _equal_optional_decimal(left: object, right: object, *, field: str) -> bool:
    return _optional_decimal(left, field=field) == _optional_decimal(right, field=field)


def _same_opportunity(rows: list[dict[str, Any]]) -> None:
    """Require common-arm admissions to retain identical geometry/economics."""

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["symbol"], row["scenario"], str(row["pair_key"][3])), []).append(row)
    decimal_fields = ("approved_risk_quote", "quantity", "stop_basis")
    outcome_decimal_fields = (
        "gross_pnl_quote",
        "total_fees_quote",
        "net_pnl_quote",
        "closed_net_r",
        "unresolved_quantity",
    )
    for values in groups.values():
        for row in values:
            _intended_target(row)
            _execution_target(row)
        if len(values) < 2:
            continue
        reference = values[0]
        for value in values[1:]:
            if any(
                _required_decimal(reference["authority"][field], field=field)
                != _required_decimal(value["authority"][field], field=field)
                for field in decimal_fields
            ):
                raise ScreenError("shared opportunity changed its risk or geometry across arms")
            if _intended_target(reference) != _intended_target(
                value
            ) or not _equal_optional_decimal(
                reference["authority"].get(
                    "execution_target_price", reference["authority"].get("frozen_target_price")
                ),
                value["authority"].get(
                    "execution_target_price", value["authority"].get("frozen_target_price")
                ),
                field="execution_target_price",
            ):
                raise ScreenError("shared opportunity changed its intended/execution target")
            left, right = reference["entry"], value["entry"]
            if (left is None) != (right is None):
                raise ScreenError("shared opportunity changed its entry economics across arms")
            if left is None and reference["new"]["status"] != "ENTRY_UNFILLED":
                raise ScreenError("filled outcome is missing its entry facts")
            if (
                left is not None
                and right is not None
                and (
                    any(str(left[field]) != str(right[field]) for field in ("side", "fill_at"))
                    or any(
                        _required_decimal(left[field], field=f"entry.{field}")
                        != _required_decimal(right[field], field=f"entry.{field}")
                        for field in ("reference_price", "fill_price", "quantity", "fee_quote")
                    )
                )
            ):
                raise ScreenError("shared opportunity changed its entry economics across arms")
            left_new, right_new = reference["new"], value["new"]
            if any(
                left_new[field] != right_new[field]
                for field in ("status", "trigger", "natural_closed")
            ) or any(
                not _equal_optional_decimal(
                    left_new.get(field), right_new.get(field), field=f"new.{field}"
                )
                for field in outcome_decimal_fields
            ):
                raise ScreenError("shared opportunity changed its outcome economics across arms")
            left_terminal, right_terminal = (
                reference["terminal_valuation"],
                value["terminal_valuation"],
            )
            if any(
                left_terminal.get(field) != right_terminal.get(field)
                for field in ("status", "valuation_at", "depends_on_open_position")
            ) or any(
                not _equal_optional_decimal(
                    left_terminal.get(field),
                    right_terminal.get(field),
                    field=f"terminal_valuation.{field}",
                )
                for field in (
                    "reference_price",
                    "fill_price",
                    "hypothetical_exit_fee_quote",
                    "gross_pnl_quote",
                    "total_fees_quote",
                    "net_pnl_quote",
                    "net_approved_risk_r",
                )
            ):
                raise ScreenError("shared opportunity changed its terminal valuation economics")


def validate_saved_rows(
    rows_path: Path,
    output: Path,
    *,
    expected_gzip_sha256: str,
    expected_raw_sha256: str,
    expected_rows: int,
    include_report_path_proof: bool = False,
) -> dict[str, Any]:
    """Validate one retained compact row file without loading market data or replaying outcomes."""

    rows_path = rows_path.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ScreenError("saved-row validation output already exists")
    if rows_path.stat().st_size > 2_000_000:
        raise ScreenError("saved-row validation input exceeds its bounded compressed size")
    if _file_sha(rows_path) != expected_gzip_sha256:
        raise ScreenError("saved-row compressed hash differs from its authority")
    raw_hash = hashlib.sha256()
    raw_bytes = 0
    rows: list[dict[str, Any]] = []
    with gzip.open(rows_path, "rb") as source:
        for raw_line in source:
            raw_hash.update(raw_line)
            raw_bytes += len(raw_line)
            if raw_bytes > 2_000_000 or len(rows) >= 10_000:
                raise ScreenError("saved-row validation input exceeds its bounded raw size")
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                raise ScreenError("saved-row validation encountered a non-object row")
            rows.append(value)
    if raw_hash.hexdigest() != expected_raw_sha256 or len(rows) != expected_rows:
        raise ScreenError("saved-row raw identity/count differs from its authority")
    pair_keys = [tuple(str(item) for item in row["pair_key"]) for row in rows]
    if len(set(pair_keys)) != len(rows):
        raise ScreenError("saved-row pair keys are not unique")
    _same_opportunity(rows)
    counts: Counter[str] = Counter()
    recovered_keys: set[tuple[str, str]] = set()
    for row in rows:
        label = f"{row['symbol']}:{row['arm']}:{row['scenario']}:{row['new']['status']}"
        counts[label] += 1
        if row["new"]["status"] == "INVALID_GEOMETRY_RECOVERED":
            recovered_keys.add((str(row["symbol"]), str(row["pair_key"][3])))
    report = {
        "schema": "raschke-pullback-saved-row-validation-v1",
        "status": "VALIDATED_PARTIAL_BTC_ROWS",
        "not_a_final_study_report": True,
        "compressed_sha256": expected_gzip_sha256,
        "raw_sha256": expected_raw_sha256,
        "raw_bytes": raw_bytes,
        "rows": len(rows),
        "unique_pair_keys": len(set(pair_keys)),
        "unique_opportunity_ids": len({key[3] for key in pair_keys}),
        "recovered_records": sum(
            row["new"]["status"] == "INVALID_GEOMETRY_RECOVERED" for row in rows
        ),
        "recovered_underlying_opportunities": len(recovered_keys),
        "counts": dict(sorted(counts.items())),
        "checks": {
            "intended_target_authorities_agree": True,
            "nullable_execution_target_status_semantics": True,
            "common_arm_entry_target_outcome_economics_equal": True,
            "fees_and_pnl_retained": True,
        },
        "limitations": [
            "BTC_ONLY_PARTIAL_ROWS",
            "ETH_NOT_PROCESSED",
            "NO_FINAL_PROJECTION",
            "NO_ECONOMIC_CLASSIFICATION",
        ],
    }
    if include_report_path_proof:
        report_path_proof: dict[str, Any] = {}
        for symbol, arm, scenario in sorted(
            {(str(row["symbol"]), str(row["arm"]), str(row["scenario"])) for row in rows}
        ):
            group = [
                row
                for row in rows
                if row["symbol"] == symbol and row["arm"] == arm and row["scenario"] == scenario
            ]
            report_path_proof[f"{symbol}:{arm}:{scenario}"] = {
                "metrics": _extra_metrics(group),
                "capacity": _capacity(group),
            }
        report["report_path_proof"] = report_path_proof
    _replace(output, report)
    read_back = json.loads(output.read_bytes())
    if _canonical(read_back) != _canonical(_text(report)):
        raise ScreenError("saved-row validation report write/read-back differs")
    return report


def run_member(
    wire: str,
    store: _MemoryStore,
    five: tuple[Last5mBar, ...],
    output: Path,
    *,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        tuple(
            ReplaySeries(
                store.source_id, store.symbol, frame, authority.context_start, authority.signal_end
            )
            for frame in FRAMES
        ),
        clock=FrozenClock(authority.context_start),
    )
    features, structures, regimes, composer = (
        FeatureEngine(),
        MarketStructureEngine(),
        TechnicalRegimeEngine(),
        MultiTimeframeRegimeComposer(),
    )
    strategies = {arm.value: RaschkePullbackStrategy(arm) for arm in RaschkePullbackArm}
    risk, safety, assumptions = (
        RiskManager(),
        OperationalSafetyGate(),
        ResearchSafetyAssumptionProfile(),
    )
    by_open = {bar.open_time: index for index, bar in enumerate(five)}
    counts: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    rows: list[dict[str, Any]] = []
    opportunity_sets: dict[str, set[str]] = {arm: set() for arm in ARMS}
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        feature_batch = features.process(observed)
        structure_batch = structures.process(observed)
        regime_batch = regimes.process(feature_batch, structure_batch)
        multi = composer.process(regime_batch)
        for context in assemble_strategy_contexts(observed, feature_batch, structure_batch, multi):
            evaluations = {arm: strategy.evaluate(context) for arm, strategy in strategies.items()}
            if not authority.signal_start < context.observed_at <= authority.signal_end:
                continue
            for arm, evaluation in evaluations.items():
                counter = counts[arm]
                counter["observations"] += 1
                audit = evaluation.rule_audit
                assert isinstance(audit, RaschkePullbackAudit)
                counter[f"event:{audit.event}"] += 1
                if audit.event == "CONFIRMATION" and audit.episode_id is not None:
                    counter["underlying_opportunities"] += 1
                    opportunity_sets[arm].add(audit.episode_id)
                    if audit.depth_available:
                        counter["anchor_valid_opportunities"] += 1
                if audit.rejection is not None:
                    counter[f"filter_rejected:{audit.rejection}"] += 1
                if evaluation.action is not CandidateAction.NO_TRADE:
                    counter["signals"] += 1
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
                    counter["risk_rejected"] += 1
                safety_decision = safety.evaluate(
                    risk_decision, _research_health(context, assumptions)
                )
                if (
                    risk_decision.status is RiskDecisionStatus.APPROVED
                    and safety_decision.status is not OperationalSafetyStatus.CLEARED
                ):
                    counter["safety_rejected"] += 1
                decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                decision.validate()
                authorization = EntryOrderIntentAuthorizer().authorize(decision)
                if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
                    continue
                intent = authorization.intent
                assert intent is not None
                counter["admitted"] += 1
                assert audit.frozen_target is not None
                index = by_open.get(context.observed_at)
                if index is None:
                    raise ScreenError("admission has no exact 5m follow-up start")
                suffix = five[index:]
                for scenario in Scenario:
                    configuration = Last5mProtectionConfiguration(
                        scenario_costs(scenario),
                        ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT,
                        ProtectionAcknowledgement.ACCEPTED,
                        timedelta(0),
                        frozen_target_price=audit.frozen_target,
                        holding_period=None,
                    )
                    prefix = select_no_time_exit_prefix(
                        intent=intent, bars=suffix, configuration=configuration
                    )
                    binding = Last5mExecutionBinding(
                        store.source_id,
                        store.symbol,
                        prefix[0].epoch_id,
                        prefix[0].open_time,
                        prefix[-1].close_time,
                    )
                    result = run_last_5m_protective_execution(
                        intent=intent, binding=binding, bars=prefix, configuration=configuration
                    )
                    value = _row(wire, arm, scenario, decision, result, prefix[-1])
                    rows.append(value)
                    _append_jsonl(output / "trade-rows.jsonl", value)
        if datetime.now(UTC).second % 30 == 0:
            _replace(
                output / "progress.json",
                {
                    "symbol": wire,
                    "observed_at": batch.observed_at,
                    "rows": len(rows),
                    "counts": {arm: dict(value) for arm, value in counts.items()},
                },
            )
    expected_slots = int((authority.signal_end - authority.signal_start) / timedelta(minutes=15))
    if any(value["observations"] != expected_slots for value in counts.values()):
        raise ScreenError("decision-slot population is incomplete")
    if len({frozenset(value) for value in opportunity_sets.values()}) != 1:
        raise ScreenError("arm opportunity populations are not identical")
    _same_opportunity(rows)
    return (
        {
            "symbol": wire,
            "counts": {arm: dict(sorted(value.items())) for arm, value in counts.items()},
            "underlying_opportunity_ids": sorted(opportunity_sets[ARMS[0]]),
        },
        rows,
    )


def _study_metrics(rows: list[dict[str, Any]], *, terminal: bool) -> dict[str, Any]:
    """Report admissions without treating an unfilled order as open exposure."""

    economic_rows: list[dict[str, Any]] = []
    execution_statuses: Counter[str] = Counter()
    incomplete_statuses: Counter[str] = Counter()
    filled = 0
    unfilled = 0
    endpoint_open = 0
    recovered = 0
    closed_statuses = {"ORDINARY_CLOSED", "INVALID_GEOMETRY_RECOVERED"}
    for row in rows:
        new = row["new"]
        status = str(new["status"])
        entry = row["entry"]
        natural_closed = bool(new["natural_closed"])
        execution_statuses[status] += 1
        if entry is not None:
            filled += 1
        if status == "ENTRY_UNFILLED":
            if entry is not None or natural_closed:
                raise ScreenError("ENTRY_UNFILLED row has filled or closed position facts")
            unfilled += 1
            continue
        if status == "TERMINAL_OPEN":
            if entry is None or natural_closed:
                raise ScreenError("TERMINAL_OPEN row lacks open filled-position facts")
            if not bool(row["terminal_valuation"].get("depends_on_open_position")):
                raise ScreenError("TERMINAL_OPEN row lacks dependent terminal valuation")
            endpoint_open += 1
            economic_rows.append(row)
            continue
        if status in closed_statuses:
            if entry is None or not natural_closed:
                raise ScreenError(f"{status} row lacks closed filled-position facts")
            if status == "INVALID_GEOMETRY_RECOVERED":
                recovered += 1
            economic_rows.append(row)
            continue
        incomplete_statuses[status] += 1

    result = _metrics(economic_rows, terminal=terminal)
    result.update(
        {
            "denominator": len(rows),
            "economic_denominator": len(economic_rows),
            "filled_admissions": filled,
            "entry_unfilled": unfilled,
            "ordinary_endpoint_open": endpoint_open,
            "recovered_closed": recovered,
            "incomplete_or_error": sum(incomplete_statuses.values()),
            "execution_status_counts": dict(sorted(execution_statuses.items())),
            "incomplete_or_error_status_counts": dict(sorted(incomplete_statuses.items())),
        }
    )
    return result


def _capacity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    rejected = 0
    free_at: datetime | None = None
    for row in sorted(rows, key=lambda value: str(value["decision_at"])):
        decision_at = datetime.fromisoformat(str(row["decision_at"]))
        if free_at is not None and decision_at <= free_at:
            rejected += 1
            continue
        selected.append(row)
        entry = row["entry"]
        if entry is None:
            continue
        opened = datetime.fromisoformat(str(entry["fill_at"]))
        upper = row["new"]["holding_upper_seconds"]
        free_at = (
            datetime.fromisoformat(str(row["terminal_valuation"]["valuation_at"]))
            if upper is None
            else opened + timedelta(seconds=float(upper))
        )
    return {
        "all_admissions": len(rows),
        "selected": len(selected),
        "overlap_rejected": rejected,
        "natural": _study_metrics(selected, terminal=False),
        "terminal": _study_metrics(selected, terminal=True),
    }


def _extra_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = _study_metrics(rows, terminal=True)
    closed = [
        row
        for row in rows
        if row["new"]["natural_closed"]
        and row["new"]["status"] in {"ORDINARY_CLOSED", "INVALID_GEOMETRY_RECOVERED"}
    ]
    with localcontext(_CONTEXT):
        values = [Decimal(str(row["new"]["closed_net_r"])) for row in closed]
        quote_values = [Decimal(str(row["new"]["net_pnl_quote"])) for row in closed]
        winners = sorted((value for value in values if value > 0), reverse=True)
        losses = [value for value in values if value < 0]
        gross = sum((Decimal(str(row["new"]["gross_pnl_quote"])) for row in closed), Decimal(0))
        fees = sum((Decimal(str(row["new"]["total_fees_quote"])) for row in closed), Decimal(0))
        positive_r = sum((value for value in values if value > 0), Decimal(0))
        negative_r = -sum((value for value in values if value < 0), Decimal(0))
        positive_quote = sum((value for value in quote_values if value > 0), Decimal(0))
        negative_quote = -sum((value for value in quote_values if value < 0), Decimal(0))
        stop_distances: list[Decimal] = []
        intended_target_distances: list[Decimal] = []
        target_distances: list[Decimal] = []
        reward_risks: list[Decimal] = []
        execution_target_unavailable = 0
        invalid_executed_geometry = 0
        recovered_excluded = 0
        for row in rows:
            entry = row["entry"]
            if entry is None:
                continue
            fill = _required_decimal(entry["fill_price"], field="entry.fill_price")
            stop = _required_decimal(row["authority"]["stop_basis"], field="stop_basis")
            intended = _intended_target(row)
            execution = _execution_target(row)
            stop_distance = abs(fill - stop)
            intended_target_distances.append(abs(intended - fill))
            stop_distances.append(stop_distance)
            if execution is None:
                execution_target_unavailable += 1
                if row["new"]["status"] == "INVALID_GEOMETRY_RECOVERED":
                    recovered_excluded += 1
                continue
            valid = (
                stop < fill < execution if str(row["side"]) == "BUY" else execution < fill < stop
            )
            if not valid or stop_distance <= 0:
                invalid_executed_geometry += 1
                continue
            target_distance = abs(execution - fill)
            target_distances.append(target_distance)
            reward_risks.append(target_distance / stop_distance)
    base.update(
        {
            "natural_mean_net_r": None
            if not values
            else sum(values, Decimal(0)) / Decimal(len(values)),
            "natural_median_net_r": None if not values else median(values),
            "natural_gross_pnl_quote": gross,
            "natural_fees_quote": fees,
            "natural_before_commission_pnl_quote": gross,
            "r_profit_factor": None if negative_r == 0 else positive_r / negative_r,
            "quote_profit_factor": None if negative_quote == 0 else positive_quote / negative_quote,
            "average_win_r": None
            if not winners
            else sum(winners, Decimal(0)) / Decimal(len(winners)),
            "average_loss_r": None
            if not losses
            else sum(losses, Decimal(0)) / Decimal(len(losses)),
            "geometry": {
                "entry_denominator": len(stop_distances),
                "intended_target_denominator": len(intended_target_distances),
                "valid_executed_reward_risk_denominator": len(reward_risks),
                "execution_target_unavailable": execution_target_unavailable,
                "invalid_executed_geometry": invalid_executed_geometry,
                "recovered_excluded_from_executed_reward_risk": recovered_excluded,
                "median_stop_distance": None if not stop_distances else median(stop_distances),
                "median_intended_target_distance": None
                if not intended_target_distances
                else median(intended_target_distances),
                "median_target_distance": None
                if not target_distances
                else median(target_distances),
                "median_frozen_reward_risk": None if not reward_risks else median(reward_risks),
            },
            "largest_winner_r": None if not winners else winners[0],
            "net_r_without_largest_winner": None
            if not values
            else sum(values, Decimal(0)) - (winners[0] if winners else Decimal(0)),
        }
    )
    return base


def _views(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "natural": _study_metrics(rows, terminal=False),
        "terminal": _study_metrics(rows, terminal=True),
    }


def _selection_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reference = [row for row in rows if row["arm"] == RaschkePullbackArm.REFERENCE.value]
    fib = [row for row in rows if row["arm"] == RaschkePullbackArm.FIB_ZONE.value]
    rounded = [row for row in rows if row["arm"] == RaschkePullbackArm.ROUND_ZONE.value]

    def episode(row: dict[str, Any]) -> str:
        return str(row["pair_key"][3])

    fib_ids, round_ids = {episode(row) for row in fib}, {episode(row) for row in rounded}
    valid_reference = [row for row in reference if bool(row["audit"]["depth_available"])]
    valid_ids = {episode(row) for row in valid_reference}
    return {
        "reference_all": _views(reference),
        "reference_valid_anchor": _views(valid_reference),
        "fib_retained_reference": _views([row for row in reference if episode(row) in fib_ids]),
        "fib_excluded_reference": _views(
            [row for row in valid_reference if episode(row) not in fib_ids]
        ),
        "round_common_core_reference": _views(
            [row for row in reference if episode(row) in round_ids]
        ),
        "fib_outer_strips_reference": _views(
            [row for row in reference if episode(row) in fib_ids - round_ids]
        ),
        "anchor_unavailable_reference": _views(
            [row for row in reference if episode(row) not in valid_ids]
        ),
        "nested_identity": round_ids <= fib_ids <= valid_ids,
    }


def _projection(
    run_id: str,
    executable_sha: str,
    members: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    authority: ScreenInputAuthority,
    protocol_sha256: str,
) -> dict[str, Any]:
    table, blocks, capacity, concurrency, paired_selection = {}, {}, {}, {}, {}
    for wire in authority.shard_map:
        for arm in ARMS:
            for scenario in Scenario:
                values = [
                    row
                    for row in rows
                    if row["symbol"] == wire
                    and row["arm"] == arm
                    and row["scenario"] == scenario.value
                ]
                key = f"{wire}:{arm}:{scenario.value}"
                table[key] = {
                    "natural": _study_metrics(values, terminal=False),
                    "terminal": _extra_metrics(values),
                }
                capacity[key] = _capacity(values)
                concurrency[key] = _peak_concurrency(values)
                for name in ("BLOCK_1", "BLOCK_2", "BLOCK_3"):
                    subset = [
                        row
                        for row in values
                        if _block(
                            datetime.fromisoformat(str(row["decision_at"])) - timedelta(minutes=15),
                            authority.signal_start,
                        )
                        == name
                    ]
                    blocks[f"{key}:{name}"] = {
                        "natural": _study_metrics(subset, terminal=False),
                        "terminal": _study_metrics(subset, terminal=True),
                    }
        for scenario in Scenario:
            comparison_rows = [
                row for row in rows if row["symbol"] == wire and row["scenario"] == scenario.value
            ]
            paired_selection[f"{wire}:{scenario.value}"] = _selection_comparison(comparison_rows)
    selections: dict[str, Any] = {}
    for wire in authority.shard_map:
        member = next(item for item in members if item["symbol"] == wire)
        counts = member["counts"]
        selections[wire] = {
            arm: {
                "underlying_opportunities": counts[arm].get("underlying_opportunities", 0),
                "anchor_valid": counts[arm].get("anchor_valid_opportunities", 0),
                "signals": counts[arm].get("signals", 0),
                "admitted": counts[arm].get("admitted", 0),
                "filter_rejections": {
                    key.removeprefix("filter_rejected:"): value
                    for key, value in counts[arm].items()
                    if key.startswith("filter_rejected:")
                },
            }
            for arm in ARMS
        }
    keys = [
        f"{wire}:{arm}:{scenario.value}"
        for wire in authority.shard_map
        for arm in ARMS
        for scenario in Scenario
    ]
    sparse = any(capacity[key]["natural"]["natural_closed"] < 30 for key in keys)
    review_candidate = not sparse
    for key in keys:
        all_terminal = table[key]["terminal"]
        selected_natural = capacity[key]["natural"]
        if (
            all_terminal["mean_net_r"] is None
            or all_terminal["mean_net_r"] <= 0
            or all_terminal["profit_factor"] is None
            or all_terminal["profit_factor"] <= 1
            or selected_natural["mean_net_r"] is None
            or selected_natural["mean_net_r"] <= 0
        ):
            review_candidate = False
    for wire in authority.shard_map:
        for arm in ARMS:
            positive_base_blocks = sum(
                blocks[f"{wire}:{arm}:BASE:{name}"]["natural"]["mean_net_r"] is not None
                and blocks[f"{wire}:{arm}:BASE:{name}"]["natural"]["mean_net_r"] > 0
                for name in ("BLOCK_1", "BLOCK_2", "BLOCK_3")
            )
            if positive_base_blocks < 2:
                review_candidate = False
    terminal_means = [table[key]["terminal"]["mean_net_r"] for key in keys]
    classification = (
        "SPARSE"
        if sparse
        else "REVIEW_ONLY_CANDIDATE"
        if review_candidate
        else "NEGATIVE"
        if terminal_means and all(value is not None and value <= 0 for value in terminal_means)
        else "MIXED"
    )
    return {
        "schema": "raschke-pullback-fib-ablation-result-v1",
        "protocol": PROTOCOL_ID,
        "protocol_sha256": protocol_sha256,
        "run_id": run_id,
        "executable_sha": executable_sha,
        "source_id": authority.source_id.value,
        "input_hashes": protocol_record(authority)["input_hashes"],
        "members": members,
        "selection_effects": selections,
        "paired_selection_comparisons": paired_selection,
        "economic_table": table,
        "chronological_blocks": blocks,
        "capacity_diagnostic": capacity,
        "peak_concurrency": concurrency,
        "classification": classification,
        "promotion": "NO_PROMOTION",
        "limitations": [
            "SELECTION_INFORMED_EXPLORATORY_DATA_NOT_UNTOUCHED_HOLDOUT",
            "LAST_5M_MODEL_NOT_TICK_ACCURATE",
            "TERMINAL_VALUATIONS_ARE_CENSORED_NOT_REALIZED",
            "FUNDING_NOT_MODELED",
        ],
    }


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    followup_end: datetime = FOLLOWUP_END,
    expected_protocol_sha256: str | None = None,
) -> dict[str, Any]:
    repo, output = repo.resolve(strict=True), output.resolve()
    if output.exists():
        raise ScreenError("run output already exists; retry is forbidden")
    _clean_sha(repo, executable_sha)
    protocol = protocol_record(authority, followup_end=followup_end)
    protocol_sha256 = _sha(_canonical(protocol))
    if expected_protocol_sha256 is not None and protocol_sha256 != expected_protocol_sha256:
        raise ScreenError("effective protocol differs from declared identity")
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": protocol_sha256,
            "started_at": datetime.now(UTC),
            "historical_attempt": 1,
        },
    )
    _replace(output / "protocol.json", protocol)
    accepted = AcceptedInput(repo, authority)
    members, rows = [], []
    for wire in authority.shard_map:
        store, five = accepted.load(wire, tail_end=followup_end)
        member, values = run_member(wire, store, five, output, authority=authority)
        members.append(member)
        rows.extend(values)
        _replace(output / f"{wire.lower()}-member.json", member)
    projection = _projection(run_id, executable_sha, members, rows, authority, protocol_sha256)
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
