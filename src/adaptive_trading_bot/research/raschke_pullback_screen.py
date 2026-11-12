"""Fixed BTC/ETH Raschke-informed first-pullback depth-filter ablation."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
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
            "frozen_target_price": result.target_price,
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


def _same_opportunity(rows: list[dict[str, Any]]) -> None:
    """Require common-arm admissions to retain identical geometry/economics."""

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["symbol"], row["scenario"], str(row["pair_key"][3])), []).append(row)
    fields = ("approved_risk_quote", "quantity", "stop_basis", "frozen_target_price")
    for values in groups.values():
        if len(values) < 2:
            continue
        reference = values[0]
        for value in values[1:]:
            if any(
                Decimal(str(reference["authority"][field]))
                != Decimal(str(value["authority"][field]))
                for field in fields
            ):
                raise ScreenError("shared opportunity changed its risk or geometry across arms")
            left, right = reference["entry"], value["entry"]
            if (
                left is None
                or right is None
                or any(
                    Decimal(str(left[field])) != Decimal(str(right[field]))
                    for field in ("reference_price", "fill_price", "quantity", "fee_quote")
                )
            ):
                raise ScreenError("shared opportunity changed its entry economics across arms")


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


def _capacity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    rejected = 0
    free_at: datetime | None = None
    for row in sorted(rows, key=lambda value: str(value["decision_at"])):
        entry = row["entry"]
        if entry is None:
            continue
        opened = datetime.fromisoformat(str(entry["fill_at"]))
        if free_at is not None and opened <= free_at:
            rejected += 1
            continue
        selected.append(row)
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
        "natural": _metrics(selected, terminal=False),
        "terminal": _metrics(selected, terminal=True),
    }


def _extra_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    base = _metrics(rows, terminal=True)
    closed = [row for row in rows if row["new"]["natural_closed"]]
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
        target_distances: list[Decimal] = []
        reward_risks: list[Decimal] = []
        for row in rows:
            entry = row["entry"]
            if entry is None:
                continue
            fill = Decimal(str(entry["fill_price"]))
            stop_distance = abs(fill - Decimal(str(row["authority"]["stop_basis"])))
            target_distance = abs(Decimal(str(row["authority"]["frozen_target_price"])) - fill)
            stop_distances.append(stop_distance)
            target_distances.append(target_distance)
            if stop_distance > 0:
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
                "denominator": len(stop_distances),
                "median_stop_distance": None if not stop_distances else median(stop_distances),
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
        "natural": _metrics(rows, terminal=False),
        "terminal": _metrics(rows, terminal=True),
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
                    "natural": _metrics(values, terminal=False),
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
                        "natural": _metrics(subset, terminal=False),
                        "terminal": _metrics(subset, terminal=True),
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
