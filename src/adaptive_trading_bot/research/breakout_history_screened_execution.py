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
    TASKS,
    ComparisonArm,
    ComparisonScenario,
    HistoryScreenedAllFillRunner,
    HistoryScreenedComparisonError,
    HistoryScreenedConfiguration,
    HistoryScreenedDecisionPopulation,
    HistoryScreenedExecutionGrant,
    HistoryScreenedInputBinding,
    HistoryScreenedReplayResult,
    HistoryScreenedTask,
    LockedHistoryScreenedCandleStore,
    _canonical,
    evaluate_granted_task,
    evaluate_synthetic_task,
    report_block,
    report_week,
)
from adaptive_trading_bot.research.breakout_zone_comparison import OpportunityKey
from adaptive_trading_bot.research.research_v2_screen_evidence import decode, encode
from adaptive_trading_bot.strategies.core import CandidateAction

_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
RESULT_SCHEMA = "history-screened-180d-task-evidence-v2"
REPORT_SCHEMA = "history-screened-180d-task-report-v2"
PROJECTION_SCHEMA = "history-screened-180d-projection-v2"


@dataclass(frozen=True, slots=True)
class HistoryScreenedRunAssociation:
    input_kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    task_plan_sha256: str
    source_id: str
    dataset_version: str
    manifest_sha256: str
    report_sha256: str
    plan_sha256: str

    @classmethod
    def from_grant(
        cls,
        grant: HistoryScreenedExecutionGrant,
        binding: HistoryScreenedInputBinding,
        *,
        actual_sha: str,
    ) -> HistoryScreenedRunAssociation:
        expected_kind = (
            "EXTERNAL_REVIEWED_EXECUTION_GRANT"
            if binding.kind == "ACCEPTED_HISTORY_SCREENED_196D"
            else "SYNTHETIC_TEST_GRANT"
        )
        value = cls(
            grant.kind,
            grant.run_uuid,
            grant.executable_sha,
            grant.protocol_sha256,
            grant.task_plan_sha256,
            grant.source_id,
            grant.dataset_version,
            grant.manifest_sha256,
            binding.report_sha256,
            binding.plan_sha256,
        )
        value.validate(binding, actual_sha=actual_sha)
        if value.input_kind != expected_kind:
            raise HistoryScreenedComparisonError("run input kind differs from binding")
        return value

    def validate(self, binding: HistoryScreenedInputBinding, *, actual_sha: str) -> None:
        binding.validate()
        expected_kind = (
            "EXTERNAL_REVIEWED_EXECUTION_GRANT"
            if binding.kind == "ACCEPTED_HISTORY_SCREENED_196D"
            else "SYNTHETIC_TEST_GRANT"
        )
        if (
            self.input_kind != expected_kind
            or not self.run_uuid
            or self.executable_sha != actual_sha
            or len(actual_sha) != 40
            or self.protocol_sha256 != PROTOCOL_SHA256
            or self.task_plan_sha256 != TASK_PLAN_SHA256
            or self.source_id != binding.source_id.value
            or self.dataset_version != binding.dataset_version
            or self.manifest_sha256 != binding.manifest_sha256
            or self.report_sha256 != binding.report_sha256
            or self.plan_sha256 != binding.plan_sha256
        ):
            raise HistoryScreenedComparisonError("run association differs from exact input")

    def record(self) -> dict[str, str]:
        return {
            "input_kind": self.input_kind,
            "run_uuid": self.run_uuid,
            "executable_sha": self.executable_sha,
            "protocol_sha256": self.protocol_sha256,
            "task_plan_sha256": self.task_plan_sha256,
            "source_id": self.source_id,
            "dataset_version": self.dataset_version,
            "manifest_sha256": self.manifest_sha256,
            "report_sha256": self.report_sha256,
            "plan_sha256": self.plan_sha256,
        }


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


def _actual_side(decision: DecisionJournalEntry[object, object]) -> str:
    action = decision.strategy_evaluation.action
    if action is CandidateAction.LONG_CANDIDATE:
        return "LONG"
    if action is CandidateAction.SHORT_CANDIDATE:
        return "SHORT"
    return "UNAVAILABLE"


def _decision_side(decision: DecisionJournalEntry[object, object]) -> str:
    """Directional audit side; actual candidate counting uses _actual_side()."""

    evaluation = decision.strategy_evaluation
    action = evaluation.action.value
    if action == "NO_TRADE" and hasattr(evaluation.rule_audit, "underlying_action"):
        action = cast(Any, evaluation.rule_audit).underlying_action.value
    if action == "LONG_CANDIDATE":
        return "LONG"
    if action == "SHORT_CANDIDATE":
        return "SHORT"
    return "UNAVAILABLE"


def _opportunity_record(key: OpportunityKey) -> dict[str, object]:
    return {
        "source_id": key.source_id,
        "dataset_version": key.dataset_version,
        "manifest_sha256": key.manifest_sha256,
        "symbol": key.symbol,
        "epoch_start": key.epoch_start.isoformat(),
        "decision_at": key.decision_at.isoformat(),
        "side": key.side,
        "pivot_kind": key.pivot_kind,
        "pivot_open_time": key.pivot_open_time.isoformat(),
        "pivot_confirmed_at": key.pivot_confirmed_at.isoformat(),
        "pivot_price": str(key.pivot_price),
    }


def _opportunity_id(key: OpportunityKey) -> str:
    return _sha(_canonical(_opportunity_record(key)))


def _key_by_identity(
    population: HistoryScreenedDecisionPopulation,
    arm: ComparisonArm,
) -> dict[object, OpportunityKey | None]:
    decisions = (
        population.baseline_decisions
        if arm is ComparisonArm.BASELINE
        else population.zone_decisions
    )
    return {
        decision.identity: key
        for decision, key in zip(decisions, population.opportunity_keys, strict=True)
    }


def _decision_bytes(decision: DecisionJournalEntry[object, object]) -> bytes:
    return _canonical(encode(decision))


def validate_task_associations(
    population: HistoryScreenedDecisionPopulation,
    results: tuple[HistoryScreenedReplayResult, ...],
    *,
    expected_task: HistoryScreenedTask,
    population_sha256: str,
) -> dict[tuple[ComparisonArm, ComparisonScenario], tuple[HistoryScreenedReplayResult, ...]]:
    """Bind every result cell to the exact reviewed task and sealed population."""

    population.validate()
    expected_task.validate(
        synthetic=population.configuration.input_binding.kind == "SYNTHETIC_FIXTURE"
    )
    if population.configuration.task != expected_task:
        raise HistoryScreenedComparisonError("population is associated with another task")
    authoritative: dict[tuple[ComparisonArm, object], DecisionJournalEntry[object, object]] = {}
    for arm, decisions in (
        (ComparisonArm.BASELINE, population.baseline_decisions),
        (ComparisonArm.ZONE, population.zone_decisions),
    ):
        for decision in decisions:
            key = (arm, decision.identity)
            if key in authoritative:
                raise HistoryScreenedComparisonError("population decision identity is duplicated")
            authoritative[key] = decision
    expected_cells = {
        (arm, scenario, identity)
        for arm, identity in authoritative
        for scenario in ComparisonScenario
    }
    seen: dict[tuple[ComparisonArm, ComparisonScenario, object], HistoryScreenedReplayResult] = {}
    by_cell: dict[tuple[ComparisonArm, ComparisonScenario], list[HistoryScreenedReplayResult]] = {
        (arm, scenario): [] for arm in ComparisonArm for scenario in ComparisonScenario
    }
    for result in results:
        result.validate()
        cell = (result.arm, result.scenario, result.decision.identity)
        authority = authoritative.get((result.arm, result.decision.identity))
        if (
            result.task_id != expected_task.task_id
            or result.population_sha256 != population_sha256
            or authority is None
            or _decision_bytes(result.decision) != _decision_bytes(authority)
        ):
            raise HistoryScreenedComparisonError("result differs from task/population authority")
        if cell in seen:
            raise HistoryScreenedComparisonError("result cell is duplicated")
        seen[cell] = result
        by_cell[result.arm, result.scenario].append(result)
    if set(seen) != expected_cells:
        raise HistoryScreenedComparisonError("result cells are missing, extra, or foreign")
    return {key: tuple(value) for key, value in by_cell.items()}


def _authorized(decision: DecisionJournalEntry[object, object]) -> bool:
    return (
        EntryOrderIntentAuthorizer().authorize(decision).status
        is OrderIntentAuthorizationStatus.AUTHORIZED
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _exact_sum(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return Decimal(0)
    minimum_exponent = min(cast(int, value.as_tuple().exponent) for value in values)
    maximum_adjusted = max(value.adjusted() for value in values if value != 0) if any(values) else 0
    precision = max(34, maximum_adjusted - minimum_exponent + len(str(len(values))) + 2)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return sum(values, Decimal(0))


def _metrics(results: tuple[HistoryScreenedReplayResult, ...]) -> dict[str, object]:
    statuses = Counter(result.status.value for result in results)
    closed_r = tuple(
        branch.closed_net_r
        for result in results
        for branch in result.branches
        if branch.closed_net_r is not None
    )
    fees = _exact_sum(
        tuple(
            branch.final_position.cumulative_fees
            for result in results
            for branch in result.branches
        )
    )
    residual_values: dict[str, list[Decimal]] = {}
    for result in results:
        for branch in result.branches:
            if branch.final_position.quantity:
                symbol = str(branch.final_position.symbol)
                residual_values.setdefault(symbol, []).append(
                    branch.final_position.quantity.copy_abs()
                )
    residuals = {symbol: _exact_sum(tuple(values)) for symbol, values in residual_values.items()}
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
    *,
    expected_task: HistoryScreenedTask,
    run: HistoryScreenedRunAssociation,
) -> dict[str, object]:
    run.validate(
        population.configuration.input_binding,
        actual_sha=run.executable_sha,
    )
    population_sha = _sha(population_bytes(population))
    by_cell = validate_task_associations(
        population,
        results,
        expected_task=expected_task,
        population_sha256=population_sha,
    )
    decisions = {
        ComparisonArm.BASELINE: population.baseline_decisions,
        ComparisonArm.ZONE: population.zone_decisions,
    }
    key_maps = {arm: _key_by_identity(population, arm) for arm in ComparisonArm}
    actual_candidates: dict[ComparisonArm, set[OpportunityKey]] = {
        arm: {
            key
            for decision, key in zip(arm_decisions, population.opportunity_keys, strict=True)
            if key is not None and _actual_side(decision) != "UNAVAILABLE"
        }
        for arm, arm_decisions in decisions.items()
    }
    admitted: dict[ComparisonArm, set[OpportunityKey]] = {
        arm: {
            key
            for decision, key in zip(arm_decisions, population.opportunity_keys, strict=True)
            if key is not None and _authorized(decision)
        }
        for arm, arm_decisions in decisions.items()
    }
    masks = {
        ComparisonArm.BASELINE: set(population.baseline_spacing_mask),
        ComparisonArm.ZONE: set(population.zone_spacing_mask),
    }
    if any(not masks[arm] <= admitted[arm] for arm in ComparisonArm):
        raise HistoryScreenedComparisonError("spacing mask contains a non-admission")
    opportunity_records = {
        _opportunity_id(key): _opportunity_record(key)
        for key in population.opportunity_keys
        if key is not None
    }
    zone_rejections = {
        key
        for decision, key in zip(
            population.zone_decisions, population.opportunity_keys, strict=True
        )
        if key is not None and _actual_side(decision) == "UNAVAILABLE"
    }
    rejection_reasons = Counter(
        str(cast(Any, reason).value)
        for decision, key in zip(
            population.zone_decisions, population.opportunity_keys, strict=True
        )
        if key in zone_rejections
        for reason in decision.strategy_evaluation.reasons_against
    )
    candidate_counts = {
        "baseline": len(actual_candidates[ComparisonArm.BASELINE]),
        "zone": len(actual_candidates[ComparisonArm.ZONE]),
        "underlying_opportunities": len(opportunity_records),
        "zone_rejections": len(zone_rejections),
    }
    cells: dict[str, object] = {}
    masked_cells: dict[str, object] = {}
    for (arm, scenario), values in by_cell.items():
        key = f"{arm.value}:{scenario.value}"
        cells[key] = _metrics(values)
        masked_cells[key] = _metrics(
            tuple(item for item in values if key_maps[arm][item.decision.identity] in masks[arm])
        )
    grouped = _grouped_cells(population, tuple(results))
    diagnostics = _diagnostics(population, tuple(results))
    baseline_admitted = admitted[ComparisonArm.BASELINE]
    zone_admitted = admitted[ComparisonArm.ZONE]
    admission_union = baseline_admitted | zone_admitted
    action_pairs = Counter(
        f"{baseline.strategy_evaluation.action.value}|{zone.strategy_evaluation.action.value}"
        for baseline, zone in zip(
            population.baseline_decisions, population.zone_decisions, strict=True
        )
    )
    outcome_partitions: dict[str, object] = {}
    for scenario in ComparisonScenario:
        indexes = {
            arm: {
                key_maps[arm][result.decision.identity]: result
                for result in by_cell[arm, scenario]
                if key_maps[arm][result.decision.identity] is not None
            }
            for arm in ComparisonArm
        }
        partitions: dict[str, dict[str, list[str]]] = {}
        for arm in ComparisonArm:
            arm_parts: dict[str, list[str]] = {}
            for opportunity in sorted(admission_union, key=_opportunity_id):
                if opportunity not in admitted[arm]:
                    label = "NOT_ADMITTED"
                else:
                    result = indexes[arm][opportunity]
                    label = result.status.value
                arm_parts.setdefault(label, []).append(_opportunity_id(opportunity))
            partitions[arm.value] = arm_parts
        outcome_partitions[scenario.value] = partitions
    return {
        "schema": REPORT_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "run": run.record(),
        "population_sha256": population_sha,
        "task_id": expected_task.task_id,
        "task_index": expected_task.index,
        "wire_symbol": expected_task.wire_symbol,
        "decision_slots_per_arm": len(population.baseline_decisions),
        "candidates": candidate_counts,
        "opportunity_records": opportunity_records,
        "candidate_membership": {
            arm.value: sorted(_opportunity_id(key) for key in actual_candidates[arm])
            for arm in ComparisonArm
        },
        "zone_rejection_membership": sorted(_opportunity_id(key) for key in zone_rejections),
        "zone_rejection_reasons": dict(sorted(rejection_reasons.items())),
        "observation_action_pairs": dict(sorted(action_pairs.items())),
        "both_no_signal_observations": action_pairs.get("NO_TRADE|NO_TRADE", 0),
        "admissions": {
            "baseline": len(baseline_admitted),
            "zone": len(zone_admitted),
            "common": len(baseline_admitted & zone_admitted),
            "baseline_only": len(baseline_admitted - zone_admitted),
            "zone_only": len(zone_admitted - baseline_admitted),
            "union": len(admission_union),
            "membership": {
                "common": sorted(_opportunity_id(key) for key in baseline_admitted & zone_admitted),
                "baseline_only": sorted(
                    _opportunity_id(key) for key in baseline_admitted - zone_admitted
                ),
                "zone_only": sorted(
                    _opportunity_id(key) for key in zone_admitted - baseline_admitted
                ),
            },
        },
        "masks": {
            "baseline": sorted(_opportunity_id(key) for key in masks[ComparisonArm.BASELINE]),
            "zone": sorted(_opportunity_id(key) for key in masks[ComparisonArm.ZONE]),
            "intersection": sorted(
                _opportunity_id(key)
                for key in masks[ComparisonArm.BASELINE] & masks[ComparisonArm.ZONE]
            ),
        },
        "admission_outcome_partitions": outcome_partitions,
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
    population: HistoryScreenedDecisionPopulation,
    results: tuple[HistoryScreenedReplayResult, ...],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    key_maps = {arm: _key_by_identity(population, arm) for arm in ComparisonArm}
    for result in results:
        replay = result.legacy_replay
        fill = None if replay is None else replay.entry_fill
        branch = result.branches[0] if len(result.branches) == 1 else None
        rows.append(
            {
                "opportunity": (
                    None
                    if (key := key_maps[result.arm][result.decision.identity]) is None
                    else _opportunity_id(key)
                ),
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
    grant: HistoryScreenedExecutionGrant,
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
            grant,
            actual_sha=actual_sha,
        )
    )
    population_payload = population_bytes(population)
    population_sha = _sha(population_payload)
    run_association = HistoryScreenedRunAssociation.from_grant(
        grant,
        configuration.input_binding,
        actual_sha=actual_sha,
    )
    # Population construction sealed both masks before the first replay invocation.
    population.validate()
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
    report_payload = _canonical(
        task_report(
            population,
            frozen_results,
            expected_task=configuration.task,
            run=run_association,
        )
    )
    return TaskArtifacts(
        _write_once(directory / f"{task_index:02d}.population.json.gz", population_payload),
        _write_once(directory / f"{task_index:02d}.results.jsonl.gz", results_payload),
        _write_once(directory / f"{task_index:02d}.report.json", report_payload),
    )


def verify_task(
    directory: Path,
    task_index: int,
    *,
    expected_task: HistoryScreenedTask,
    run: HistoryScreenedRunAssociation,
) -> dict[str, object]:
    population_payload = (directory / f"{task_index:02d}.population.json.gz").read_bytes()
    results_payload = (directory / f"{task_index:02d}.results.jsonl.gz").read_bytes()
    report_payload = (directory / f"{task_index:02d}.report.json").read_bytes()
    population = read_population(population_payload)
    results = read_results(results_payload)
    if task_index != expected_task.index:
        raise HistoryScreenedComparisonError("task artifact index differs from reviewed task")
    expected = _canonical(task_report(population, results, expected_task=expected_task, run=run))
    if not compare_bytes(expected, report_payload):
        raise HistoryScreenedComparisonError("saved task report differs from authorities")
    return {
        "population_sha256": _sha(population_payload),
        "results_sha256": _sha(results_payload),
        "report_sha256": _sha(report_payload),
        "task_id": population.configuration.task.task_id,
        "task_index": population.configuration.task.index,
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


def final_projection(
    directory: Path,
    *,
    run: HistoryScreenedRunAssociation,
    tasks: tuple[HistoryScreenedTask, ...] = TASKS,
) -> dict[str, object]:
    if not tasks or tuple(task.index for task in tasks) != tuple(range(len(tasks))):
        raise HistoryScreenedComparisonError("final task inventory is not exact ordered coverage")
    if run.input_kind == "EXTERNAL_REVIEWED_EXECUTION_GRANT" and tasks != TASKS:
        raise HistoryScreenedComparisonError("reviewed finalization omits or replaces a task")
    expected_names = {
        f"{task.index:02d}.{suffix}"
        for task in tasks
        for suffix in ("population.json.gz", "results.jsonl.gz", "report.json")
    }
    actual_names = {
        path.name
        for suffix in ("population.json.gz", "results.jsonl.gz", "report.json")
        for path in directory.glob(f"*.{suffix}")
    }
    if actual_names != expected_names:
        raise HistoryScreenedComparisonError("final task artifact coverage is incomplete or extra")
    all_results: list[HistoryScreenedReplayResult] = []
    task_reports: list[dict[str, object]] = []
    masks: dict[ComparisonArm, set[OpportunityKey]] = {arm: set() for arm in ComparisonArm}
    admitted: dict[ComparisonArm, set[OpportunityKey]] = {arm: set() for arm in ComparisonArm}
    result_index: dict[
        tuple[ComparisonArm, ComparisonScenario, OpportunityKey],
        HistoryScreenedReplayResult,
    ] = {}
    decision_keys: dict[tuple[ComparisonArm, object], OpportunityKey | None] = {}
    for task in tasks:
        index = task.index
        population = read_population((directory / f"{index:02d}.population.json.gz").read_bytes())
        results = read_results((directory / f"{index:02d}.results.jsonl.gz").read_bytes())
        population_sha = _sha((directory / f"{index:02d}.population.json.gz").read_bytes())
        validate_task_associations(
            population,
            results,
            expected_task=task,
            population_sha256=population_sha,
        )
        report = task_report(population, results, expected_task=task, run=run)
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
            task_key_map = _key_by_identity(population, arm)
            for identity, opportunity in task_key_map.items():
                composite = (arm, identity)
                if composite in decision_keys:
                    raise HistoryScreenedComparisonError(
                        "decision identity is duplicated across tasks"
                    )
                decision_keys[composite] = opportunity
            masks[arm].update(
                population.baseline_spacing_mask
                if arm is ComparisonArm.BASELINE
                else population.zone_spacing_mask
            )
            admitted[arm].update(
                key
                for decision in decisions
                if (key := task_key_map[decision.identity]) is not None and _authorized(decision)
            )
            for result in results:
                if result.arm is not arm:
                    continue
                opportunity = task_key_map[result.decision.identity]
                if opportunity is None:
                    continue
                cell = (arm, result.scenario, opportunity)
                if cell in result_index:
                    raise HistoryScreenedComparisonError(
                        "opportunity result is duplicated across tasks"
                    )
                result_index[cell] = result
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
                item for item in values if decision_keys[arm, item.decision.identity] in masks[arm]
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
    admission_union = admitted[ComparisonArm.BASELINE] | admitted[ComparisonArm.ZONE]
    common_admissions = admitted[ComparisonArm.BASELINE] & admitted[ComparisonArm.ZONE]
    for scenario in ComparisonScenario:
        for label, paired_keys in (
            ("full", admission_union),
            ("intersection_mask", intersection),
        ):
            deltas: list[Decimal] = []
            partitions: dict[str, list[str]] = {}
            for key in sorted(paired_keys, key=_opportunity_id):
                identity = _opportunity_id(key)
                if key not in admitted[ComparisonArm.BASELINE]:
                    partitions.setdefault("BASELINE_NOT_ADMITTED", []).append(identity)
                if key not in admitted[ComparisonArm.ZONE]:
                    partitions.setdefault("ZONE_NOT_ADMITTED", []).append(identity)
                baseline = result_index[ComparisonArm.BASELINE, scenario, key]
                zone = result_index[ComparisonArm.ZONE, scenario, key]
                baseline_r = _closed_r(baseline)
                zone_r = _closed_r(zone)
                if baseline_r is not None and zone_r is not None:
                    with localcontext(_CONTEXT):
                        deltas.append(zone_r - baseline_r)
                    partitions.setdefault("BOTH_CLOSED", []).append(identity)
                else:
                    partitions.setdefault(
                        f"BASELINE_{baseline.status.value}|ZONE_{zone.status.value}", []
                    ).append(identity)
            paired[f"{scenario.value}:{label}"] = {
                "admission_union_denominator": len(paired_keys),
                "common_admission_denominator": len(paired_keys & common_admissions),
                "both_closed": len(deltas),
                "reason_partitions": dict(sorted(partitions.items())),
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
        "run": run.record(),
        "task_count": len(tasks),
        "task_ids": [task.task_id for task in tasks],
        "task_reports": task_reports,
        "absolute": absolute,
        "paired": paired,
        "classifications": classifications,
        "interpretation": "SELECTION_INFORMED_EXPLORATORY_NON_PROMOTION",
    }
