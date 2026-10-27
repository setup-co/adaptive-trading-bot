"""Evidence serialization, reports and guarded task execution for Issue #218."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from statistics import median
from typing import Any, cast

from adaptive_trading_bot.backtest import ExecutionSeries
from adaptive_trading_bot.domain import Timeframe
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import RegimeLabel
from adaptive_trading_bot.research.all_fill_replay import AllFillStatus
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    PANEL,
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    ComparisonArm,
    ComparisonScenario,
    HistoryScreenedAllFillRunner,
    HistoryScreenedComparisonError,
    HistoryScreenedConfiguration,
    HistoryScreenedDecisionPopulation,
    HistoryScreenedExecutionGrant,
    HistoryScreenedReplayResult,
    LockedHistoryScreenedCandleStore,
    _canonical,
    evaluate_granted_task,
    evaluate_synthetic_task,
    report_block,
    report_week,
    spacing_mask,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import decode, encode

_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
RESULT_SCHEMA = "history-screened-180d-task-evidence-v1"
REPORT_SCHEMA = "history-screened-180d-task-report-v1"
PROJECTION_SCHEMA = "history-screened-180d-projection-v1"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_once(path: Path, payload: bytes) -> str:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
    except FileExistsError as exc:
        raise HistoryScreenedComparisonError("evidence path already exists") from exc
    return _sha(payload)


def _gzip(payload: bytes) -> bytes:
    return gzip.compress(payload, compresslevel=9, mtime=0)


def _ungzip(payload: bytes) -> bytes:
    try:
        return gzip.decompress(payload)
    except (OSError, EOFError) as exc:
        raise HistoryScreenedComparisonError("compressed evidence is malformed") from exc


def population_bytes(population: HistoryScreenedDecisionPopulation) -> bytes:
    population.validate()
    return _gzip(_canonical({"schema": RESULT_SCHEMA, "authority": encode(population)}))


def result_bytes(results: tuple[HistoryScreenedReplayResult, ...]) -> bytes:
    for result in results:
        result.validate()
    return _gzip(b"".join(_canonical(encode(result)) + b"\n" for result in results))


def read_population(payload: bytes) -> HistoryScreenedDecisionPopulation:
    try:
        raw = json.loads(_ungzip(payload))
    except (UnicodeError, ValueError) as exc:
        raise HistoryScreenedComparisonError("population evidence is malformed") from exc
    if not isinstance(raw, dict) or raw.get("schema") != RESULT_SCHEMA:
        raise HistoryScreenedComparisonError("population evidence schema changed")
    value = decode(raw.get("authority"))
    if not isinstance(value, HistoryScreenedDecisionPopulation):
        raise HistoryScreenedComparisonError("population evidence has a foreign type")
    value.validate()
    return value


def read_results(payload: bytes) -> tuple[HistoryScreenedReplayResult, ...]:
    values: list[HistoryScreenedReplayResult] = []
    try:
        lines = _ungzip(payload).splitlines()
        for line in lines:
            value = decode(json.loads(line))
            if not isinstance(value, HistoryScreenedReplayResult):
                raise HistoryScreenedComparisonError("result evidence has a foreign type")
            value.validate()
            values.append(value)
    except HistoryScreenedComparisonError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise HistoryScreenedComparisonError("result evidence is malformed") from exc
    return tuple(values)


def _decision_side(decision: DecisionJournalEntry[object, object]) -> str:
    evaluation = decision.strategy_evaluation
    action = evaluation.action.value
    if action == "NO_TRADE" and hasattr(evaluation.rule_audit, "underlying_action"):
        action = cast(Any, evaluation.rule_audit).underlying_action.value
    if action == "LONG_CANDIDATE":
        return "LONG"
    if action == "SHORT_CANDIDATE":
        return "SHORT"
    return "UNAVAILABLE"


def _opportunity(decision: DecisionJournalEntry[object, object]) -> str:
    identity = decision.identity
    side = _decision_side(decision)
    return "|".join((str(identity.symbol), identity.observed_at.isoformat(), side))


def _authorized(decision: DecisionJournalEntry[object, object]) -> bool:
    return (
        EntryOrderIntentAuthorizer().authorize(decision).status
        is OrderIntentAuthorizationStatus.AUTHORIZED
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _metrics(results: tuple[HistoryScreenedReplayResult, ...]) -> dict[str, object]:
    statuses = Counter(result.status.value for result in results)
    closed_r = tuple(
        branch.closed_net_r
        for result in results
        for branch in result.branches
        if branch.closed_net_r is not None
    )
    fees = sum(
        (branch.final_position.cumulative_fees for result in results for branch in result.branches),
        Decimal(0),
    )
    residuals: dict[str, Decimal] = {}
    for result in results:
        for branch in result.branches:
            if branch.final_position.quantity:
                symbol = str(branch.final_position.symbol)
                residuals[symbol] = residuals.get(symbol, Decimal(0)) + abs(
                    branch.final_position.quantity
                )
    with localcontext(_CONTEXT):
        mean_r = sum(closed_r, Decimal(0)) / len(closed_r) if closed_r else None
        median_r = cast(Decimal | None, median(closed_r)) if closed_r else None
        gains = sum((value for value in closed_r if value > 0), Decimal(0))
        losses = -sum((value for value in closed_r if value < 0), Decimal(0))
        profit_factor = gains / losses if losses > 0 else None
    return {
        "denominator": len(results),
        "terminal_statuses": dict(sorted(statuses.items())),
        "filled": sum(bool(result.branches) for result in results),
        "closed": len(closed_r),
        "unresolved_filled": sum(
            result.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
            for result in results
        ),
        "closed_net_r_mean": _decimal_text(mean_r),
        "closed_net_r_median": _decimal_text(median_r),
        "r_profit_factor": _decimal_text(profit_factor),
        "incurred_fees_quote": str(fees),
        "residual_base_quantity_by_asset": {
            key: str(value) for key, value in sorted(residuals.items())
        },
    }


def task_report(
    population: HistoryScreenedDecisionPopulation,
    results: tuple[HistoryScreenedReplayResult, ...],
) -> dict[str, object]:
    population.validate()
    for result in results:
        result.validate()
    expected = (len(population.baseline_decisions) + len(population.zone_decisions)) * len(
        ComparisonScenario
    )
    if len(results) != expected:
        raise HistoryScreenedComparisonError("task result population is incomplete")
    by_cell: dict[tuple[ComparisonArm, ComparisonScenario], list[HistoryScreenedReplayResult]] = {
        (arm, scenario): [] for arm in ComparisonArm for scenario in ComparisonScenario
    }
    for result in results:
        by_cell[result.arm, result.scenario].append(result)
    baseline_admitted = {
        _opportunity(decision)
        for decision in population.baseline_decisions
        if _authorized(decision)
    }
    zone_admitted = {
        _opportunity(decision) for decision in population.zone_decisions if _authorized(decision)
    }
    baseline_by_identity = {
        decision.identity: _opportunity(decision) for decision in population.baseline_decisions
    }
    zone_by_identity = {
        decision.identity: _opportunity(decision) for decision in population.zone_decisions
    }
    baseline_mask = {
        baseline_by_identity[item] for item in spacing_mask(population.baseline_decisions)
    }
    zone_mask = {zone_by_identity[item] for item in spacing_mask(population.zone_decisions)}
    candidate_counts = {
        "baseline": sum(
            _decision_side(item) != "UNAVAILABLE" for item in population.baseline_decisions
        ),
        "zone": sum(_decision_side(item) != "UNAVAILABLE" for item in population.zone_decisions),
    }
    cells: dict[str, object] = {}
    masked_cells: dict[str, object] = {}
    for (arm, scenario), values in by_cell.items():
        key = f"{arm.value}:{scenario.value}"
        cell = tuple(values)
        mask = baseline_mask if arm is ComparisonArm.BASELINE else zone_mask
        cells[key] = _metrics(cell)
        masked_cells[key] = _metrics(
            tuple(item for item in cell if _opportunity(item.decision) in mask)
        )
    grouped = _grouped_cells(population, tuple(results))
    diagnostics = _diagnostics(tuple(results))
    return {
        "schema": REPORT_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "task_id": population.configuration.task.task_id,
        "wire_symbol": population.configuration.task.wire_symbol,
        "decision_slots_per_arm": len(population.baseline_decisions),
        "candidates": candidate_counts,
        "admissions": {
            "baseline": len(baseline_admitted),
            "zone": len(zone_admitted),
            "common": len(baseline_admitted & zone_admitted),
            "baseline_only": len(baseline_admitted - zone_admitted),
            "zone_only": len(zone_admitted - baseline_admitted),
        },
        "masks": {
            "baseline": len(baseline_mask),
            "zone": len(zone_mask),
            "intersection": len(baseline_mask & zone_mask),
        },
        "full": cells,
        "masked": masked_cells,
        "grouped_zero_complete": grouped,
        "geometry_and_recovery_diagnostics": diagnostics,
        "economic_classification": None,
    }


def _regime(decision: DecisionJournalEntry[object, object]) -> str:
    return decision.context.multi_timeframe_regime.regime_15m.regime_label.value


def _grouped_cells(
    population: HistoryScreenedDecisionPopulation,
    results: tuple[HistoryScreenedReplayResult, ...],
) -> dict[str, object]:
    result_index = {(item.arm, item.scenario, item.decision.identity): item for item in results}
    grouped: dict[str, object] = {}
    for dimension, labels in (
        ("BLOCK", ("P1", "P2", "P3")),
        ("WEEK", tuple(f"W{index}" for index in range(1, 27))),
    ):
        for arm, decisions in (
            (ComparisonArm.BASELINE, population.baseline_decisions),
            (ComparisonArm.ZONE, population.zone_decisions),
        ):
            for scenario in ComparisonScenario:
                for label in labels:
                    for side in ("LONG", "SHORT", "UNAVAILABLE"):
                        for regime in tuple(item.value for item in RegimeLabel):
                            matching = tuple(
                                decision
                                for decision in decisions
                                if (
                                    report_block(
                                        decision.identity.observed_at
                                        - Timeframe.MINUTES_15.duration
                                    )
                                    if dimension == "BLOCK"
                                    else report_week(
                                        decision.identity.observed_at
                                        - Timeframe.MINUTES_15.duration
                                    )
                                )
                                == label
                                and _decision_side(decision) == side
                                and _regime(decision) == regime
                            )
                            terminal = Counter(
                                result_index[arm, scenario, decision.identity].status.value
                                for decision in matching
                            )
                            key = "|".join(
                                (
                                    arm.value,
                                    scenario.value,
                                    f"{dimension}:{label}",
                                    f"SIDE:{side}",
                                    f"REGIME:{regime}",
                                )
                            )
                            grouped[key] = {
                                "denominator": len(matching),
                                "terminal_statuses": dict(sorted(terminal.items())),
                            }
    return grouped


def _diagnostics(
    results: tuple[HistoryScreenedReplayResult, ...],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for result in results:
        replay = result.legacy_replay
        fill = None if replay is None else replay.entry_fill
        branch = result.branches[0] if len(result.branches) == 1 else None
        rows.append(
            {
                "opportunity": _opportunity(result.decision),
                "arm": result.arm.value,
                "scenario": result.scenario.value,
                "status": result.status.value,
                "approved_risk_quote": (
                    None
                    if result.entry_intent is None
                    else str(result.entry_intent.approved_risk_quote)
                ),
                "quantity": None
                if result.entry_intent is None
                else str(result.entry_intent.quantity),
                "actual_entry_fill": None if fill is None else str(fill.fill_price),
                "sealed_stop": None if replay is None else _decimal_text(replay.stop_basis),
                "fixed_2r_target": (
                    None if replay is None else _decimal_text(replay.target_trigger_price)
                ),
                "holding_seconds": (
                    None
                    if branch is None or branch.exit_fill is None
                    else str((branch.exit_fill.fill_at - branch.entry_fill.fill_at).total_seconds())
                ),
                "recovery": branch is not None and branch.recovery is not None,
                "closed_net_r": None if branch is None else _decimal_text(branch.closed_net_r),
            }
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class TaskArtifacts:
    population_sha256: str
    results_sha256: str
    report_sha256: str


def execute_task(
    store: Any,
    configuration: HistoryScreenedConfiguration,
    *,
    grant: HistoryScreenedExecutionGrant | None,
    actual_sha: str,
    directory: Path,
    task_index: int,
    synthetic: bool = False,
) -> TaskArtifacts:
    population = (
        evaluate_synthetic_task(store, configuration)
        if synthetic
        else evaluate_granted_task(
            cast(LockedHistoryScreenedCandleStore, store),
            configuration,
            cast(HistoryScreenedExecutionGrant, grant),
            actual_sha=actual_sha,
        )
    )
    population_payload = population_bytes(population)
    population_sha = _sha(population_payload)
    runner = HistoryScreenedAllFillRunner(store)
    series = ExecutionSeries(
        configuration.input_binding.source_id,
        configuration.task.symbol,
        Timeframe.MINUTES_15,
    )
    results: list[HistoryScreenedReplayResult] = []
    for arm, decisions in (
        (ComparisonArm.BASELINE, population.baseline_decisions),
        (ComparisonArm.ZONE, population.zone_decisions),
    ):
        for decision in decisions:
            for scenario in ComparisonScenario:
                results.append(
                    runner.run(
                        decision,
                        population.contexts,
                        series,
                        scenario,
                        arm=arm,
                        task_id=configuration.task.task_id,
                        population_sha256=population_sha,
                    )
                )
    frozen_results = tuple(results)
    results_payload = result_bytes(frozen_results)
    report_payload = _canonical(task_report(population, frozen_results))
    return TaskArtifacts(
        _write_once(directory / f"{task_index:02d}.population.json.gz", population_payload),
        _write_once(directory / f"{task_index:02d}.results.jsonl.gz", results_payload),
        _write_once(directory / f"{task_index:02d}.report.json", report_payload),
    )


def verify_task(directory: Path, task_index: int) -> dict[str, object]:
    population_payload = (directory / f"{task_index:02d}.population.json.gz").read_bytes()
    results_payload = (directory / f"{task_index:02d}.results.jsonl.gz").read_bytes()
    report_payload = (directory / f"{task_index:02d}.report.json").read_bytes()
    population = read_population(population_payload)
    results = read_results(results_payload)
    expected = _canonical(task_report(population, results))
    if not compare_bytes(expected, report_payload):
        raise HistoryScreenedComparisonError("saved task report differs from authorities")
    return {
        "population_sha256": _sha(population_payload),
        "results_sha256": _sha(results_payload),
        "report_sha256": _sha(report_payload),
        "task_id": population.configuration.task.task_id,
    }


def compare_bytes(left: bytes, right: bytes) -> bool:
    return (
        len(left) == len(right) and hashlib.sha256(left).digest() == hashlib.sha256(right).digest()
    )


def _closed_r(result: HistoryScreenedReplayResult) -> Decimal | None:
    return result.branches[0].closed_net_r if len(result.branches) == 1 else None


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    with localcontext(_CONTEXT):
        return sum(values, Decimal(0)) / len(values) if values else None


def _closed_values(results: tuple[HistoryScreenedReplayResult, ...]) -> tuple[Decimal, ...]:
    return tuple(value for item in results if (value := _closed_r(item)) is not None)


def _screen_arm(
    full: dict[ComparisonScenario, tuple[HistoryScreenedReplayResult, ...]],
    masked: dict[ComparisonScenario, tuple[HistoryScreenedReplayResult, ...]],
) -> str:
    if any(
        result.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
        for values in full.values()
        for result in values
    ):
        return "INSUFFICIENT_EVIDENCE"
    if any(len(_closed_values(full[scenario])) < 60 for scenario in ComparisonScenario):
        return "INSUFFICIENT_EVIDENCE"
    if any(len(_closed_values(masked[scenario])) < 30 for scenario in ComparisonScenario):
        return "INSUFFICIENT_EVIDENCE"
    asset_counts: dict[ComparisonScenario, Counter[str]] = {}
    week_counts: dict[ComparisonScenario, Counter[str]] = {}
    for scenario in ComparisonScenario:
        asset_counts[scenario] = Counter(
            str(item.decision.identity.symbol)
            for item in masked[scenario]
            if _closed_r(item) is not None
        )
        week_counts[scenario] = Counter(
            report_week(item.decision.identity.observed_at - Timeframe.MINUTES_15.duration)
            for item in masked[scenario]
            if _closed_r(item) is not None
        )
        if sum(value >= 3 for value in asset_counts[scenario].values()) < 5:
            return "INSUFFICIENT_EVIDENCE"
        if sum(value >= 3 for value in week_counts[scenario].values()) < 6:
            return "INSUFFICIENT_EVIDENCE"
    means = tuple(
        _mean(_closed_values(values))
        for population in (full, masked)
        for values in population.values()
    )
    if all(value is not None and value <= 0 for value in means):
        return "NEGATIVE_EVIDENCE"
    if not all(value is not None and value > 0 for value in means):
        return "TECHNICALLY_VALID_MIXED_EVIDENCE"
    positive_assets: list[set[str]] = []
    for scenario in ComparisonScenario:
        positive_assets.append(
            {
                asset
                for asset, count in asset_counts[scenario].items()
                if count >= 3
                and (
                    _mean(
                        tuple(
                            value
                            for item in masked[scenario]
                            if str(item.decision.identity.symbol) == asset
                            and (value := _closed_r(item)) is not None
                        )
                    )
                    or Decimal(0)
                )
                > 0
            }
        )
    if len(set.intersection(*positive_assets)) < 5:
        return "TECHNICALLY_VALID_MIXED_EVIDENCE"
    stress = ComparisonScenario.STRESS
    positive_weeks = sum(
        count >= 3
        and (
            _mean(
                tuple(
                    value
                    for item in masked[stress]
                    if report_week(
                        item.decision.identity.observed_at - Timeframe.MINUTES_15.duration
                    )
                    == week
                    and (value := _closed_r(item)) is not None
                )
            )
            or Decimal(0)
        )
        > 0
        for week, count in week_counts[stress].items()
    )
    if positive_weeks < 6:
        return "TECHNICALLY_VALID_MIXED_EVIDENCE"
    for omitted in PANEL:
        for values in (full[stress], masked[stress]):
            remaining = tuple(
                value
                for item in values
                if f"{item.decision.identity.symbol.base}{item.decision.identity.symbol.quote}"
                != omitted
                and (value := _closed_r(item)) is not None
            )
            if (_mean(remaining) or Decimal(0)) <= 0:
                return "TECHNICALLY_VALID_MIXED_EVIDENCE"
    return "CANDIDATE_FOR_FURTHER_VALIDATION"


def final_projection(directory: Path, task_count: int = 10) -> dict[str, object]:
    all_results: list[HistoryScreenedReplayResult] = []
    task_reports: list[dict[str, object]] = []
    masks: dict[ComparisonArm, set[str]] = {arm: set() for arm in ComparisonArm}
    for index in range(task_count):
        population = read_population((directory / f"{index:02d}.population.json.gz").read_bytes())
        results = read_results((directory / f"{index:02d}.results.jsonl.gz").read_bytes())
        report = task_report(population, results)
        if not compare_bytes(
            _canonical(report), (directory / f"{index:02d}.report.json").read_bytes()
        ):
            raise HistoryScreenedComparisonError("task report changed before finalization")
        all_results.extend(results)
        task_reports.append(report)
        for arm, decisions in (
            (ComparisonArm.BASELINE, population.baseline_decisions),
            (ComparisonArm.ZONE, population.zone_decisions),
        ):
            by_identity = {decision.identity: _opportunity(decision) for decision in decisions}
            masks[arm].update(by_identity[item] for item in spacing_mask(decisions))
    full: dict[
        tuple[ComparisonArm, ComparisonScenario], tuple[HistoryScreenedReplayResult, ...]
    ] = {}
    masked: dict[
        tuple[ComparisonArm, ComparisonScenario], tuple[HistoryScreenedReplayResult, ...]
    ] = {}
    for arm in ComparisonArm:
        for scenario in ComparisonScenario:
            values = tuple(
                item for item in all_results if item.arm is arm and item.scenario is scenario
            )
            full[arm, scenario] = values
            masked[arm, scenario] = tuple(
                item for item in values if _opportunity(item.decision) in masks[arm]
            )
    absolute = {
        f"{arm.value}:{scenario.value}": {
            "full": _metrics(full[arm, scenario]),
            "masked": _metrics(masked[arm, scenario]),
        }
        for arm in ComparisonArm
        for scenario in ComparisonScenario
    }
    paired: dict[str, object] = {}
    intersection = masks[ComparisonArm.BASELINE] & masks[ComparisonArm.ZONE]
    for scenario in ComparisonScenario:
        arm_index = {
            arm: {_opportunity(item.decision): item for item in full[arm, scenario]}
            for arm in ComparisonArm
        }
        common = set(arm_index[ComparisonArm.BASELINE]) & set(arm_index[ComparisonArm.ZONE])
        for label, keys in (("full", common), ("intersection_mask", common & intersection)):
            deltas: list[Decimal] = []
            unmatched = 0
            for key in sorted(keys):
                baseline_r = _closed_r(arm_index[ComparisonArm.BASELINE][key])
                zone_r = _closed_r(arm_index[ComparisonArm.ZONE][key])
                if baseline_r is None or zone_r is None:
                    unmatched += 1
                else:
                    deltas.append(zone_r - baseline_r)
            paired[f"{scenario.value}:{label}"] = {
                "denominator": len(keys),
                "both_closed": len(deltas),
                "unmatched_unfilled_or_open": unmatched,
                "mean_zone_minus_baseline_closed_net_r": _decimal_text(_mean(tuple(deltas))),
            }
    classifications = {
        arm.value: _screen_arm(
            {scenario: full[arm, scenario] for scenario in ComparisonScenario},
            {scenario: masked[arm, scenario] for scenario in ComparisonScenario},
        )
        for arm in ComparisonArm
    }
    return {
        "schema": PROJECTION_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "task_count": task_count,
        "task_reports": task_reports,
        "absolute": absolute,
        "paired": paired,
        "classifications": classifications,
        "interpretation": "SELECTION_INFORMED_EXPLORATORY_NON_PROMOTION",
    }
