"""Synthetic-only acceptance for BREAKOUT_RETEST_ZONE_COMPARISON_V1."""

from __future__ import annotations

import hashlib
import json
import runpy
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.backtest import (
    ExecutionCandleSource,
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillReplayError,
    AllFillScenario,
    AllFillStatus,
    ResearchAllFillReplayRunner,
)
from adaptive_trading_bot.research.breakout_retest_replay import (
    BreakoutRetestFixed2RReplayRunner,
    BreakoutRetestReplayError,
)
from adaptive_trading_bot.research.breakout_zone_comparison import (
    ARTIFACT_INDEX_SHA256,
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    BreakoutComparisonPopulation,
    BreakoutComparisonReplayResult,
    BreakoutZoneComparisonError,
    BreakoutZoneComparisonRunner,
    ComparisonArm,
    ComparisonExecutionGrant,
    OpportunityKey,
    ResultOrigin,
    _canonical,
    build_comparison_population,
    comparison_result_record,
    project_task_report,
    recover_comparison_result,
    reuse_baseline_result,
    spacing_mask,
)
from adaptive_trading_bot.research.breakout_zone_comparison_ledger import (
    ComparisonLedgerStore,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    HistoricalStrategyContextObservation,
)
from adaptive_trading_bot.research.research_v2_admission import (
    DATASET_VERSION,
    MANIFEST_SHA256,
    SOURCE_ID,
    ResearchV2AdmissionConfiguration,
    ResearchV2DecisionPopulation,
)
from adaptive_trading_bot.risk import RiskManagerConfiguration
from adaptive_trading_bot.storage import HistoricalCandleStore
from adaptive_trading_bot.strategies import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_research_v2_admission.py")))
SyntheticEpochStore = cast(type, _HELPERS["SyntheticEpochStore"])
_config = cast(Callable[..., ResearchV2AdmissionConfiguration], _HELPERS["_config"])
_evaluate = cast(
    Callable[[object, ResearchV2AdmissionConfiguration], ResearchV2DecisionPopulation],
    _HELPERS["_evaluate_synthetic_epoch"],
)
SYMBOL = cast(TradingSymbol, _HELPERS["SYMBOL"])

BASE_COSTS = ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10))
STRESS_COSTS = ExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(20), timedelta(seconds=1))
SYNTHETIC_SHA = "1" * 40


class _StaticExecutionSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self._candles = candles

    def load(
        self,
        series: ExecutionSeries,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        del series
        return tuple(candle for candle in self._candles if start <= candle.open_time < end)


def _population(
    pattern: str = "breakout",
    *,
    risk: RiskManagerConfiguration | None = None,
) -> tuple[object, BreakoutComparisonPopulation]:
    store = SyntheticEpochStore(SYMBOL, pattern=pattern, hours=380)
    configuration = _config(
        family="breakout_retest",
        store=store,
        decision_end_hours=201,
        follow_up_hours=380,
    )
    if risk is not None:
        configuration = ResearchV2AdmissionConfiguration(
            configuration.bounds,
            configuration.family,
            configuration.research_equity_quote,
            input_binding=configuration.input_binding,
            risk_configuration=risk,
            safety_configuration=configuration.safety_configuration,
            safety_assumptions=configuration.safety_assumptions,
        )
    baseline = _evaluate(store, configuration)
    return store, build_comparison_population(baseline, task_id=f"synthetic-{pattern}")


def _contexts(
    population: BreakoutComparisonPopulation,
    decision_at: datetime,
) -> tuple[object, ...]:
    cutoff = decision_at + timedelta(hours=168)
    return tuple(
        item
        for item in population.baseline_population.contexts
        if decision_at < item.context.observed_at < cutoff
    )


def _complete_results(
    store: object,
    population: BreakoutComparisonPopulation,
) -> tuple[BreakoutComparisonReplayResult, ...]:
    source = HistoricalExecutionSource(cast(HistoricalCandleStore, store))
    baseline_runner = ResearchAllFillReplayRunner(source)
    zone_runner = BreakoutZoneComparisonRunner(source)
    bounds = population.baseline_population.configuration.bounds
    series = ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15)
    output: list[BreakoutComparisonReplayResult] = []
    for scenario, costs in (
        (AllFillScenario.BASE, BASE_COSTS),
        (AllFillScenario.COMBINED_STRESS, STRESS_COSTS),
    ):
        for index, (baseline, zone) in enumerate(
            zip(
                population.baseline_population.decisions,
                population.zone_decisions,
                strict=True,
            )
        ):
            cutoff = baseline.identity.observed_at + timedelta(hours=168)
            contexts = cast(
                tuple[HistoricalStrategyContextObservation, ...],
                _contexts(population, baseline.identity.observed_at),
            )
            original = baseline_runner.run(
                baseline,
                contexts,
                series,
                costs,
                start=baseline.identity.observed_at,
                end=cutoff,
            )
            output.append(
                reuse_baseline_result(
                    original,
                    authority_sha256="a" * 64,
                    record_sha256=hashlib.sha256(f"{scenario.value}:{index}".encode()).hexdigest(),
                )
            )
            output.append(
                zone_runner.run_zone(
                    zone,
                    contexts,
                    series,
                    costs,
                    end=cutoff,
                )
            )
    return tuple(output)


@pytest.fixture(scope="module")
def long_evidence() -> tuple[
    object,
    BreakoutComparisonPopulation,
    tuple[BreakoutComparisonReplayResult, ...],
]:
    store, population = _population()
    return store, population, _complete_results(store, population)


def test_population_uses_shared_contexts_but_independent_authorities_and_sizing() -> None:
    risk = RiskManagerConfiguration(
        risk_per_trade_fraction=Decimal("0.001"),
        max_simultaneous_risk_fraction=Decimal("0.001"),
    )
    _, population = _population(risk=risk)
    pair = next(
        (baseline, zone)
        for baseline, zone in zip(
            population.baseline_population.decisions,
            population.zone_decisions,
            strict=True,
        )
        if baseline.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    baseline, zone = pair
    assert baseline.context == zone.context
    assert baseline.strategy_evaluation.strategy_id == "breakout_retest"
    assert zone.strategy_evaluation.strategy_id == "breakout_retest_zone_invalidation"
    assert baseline.risk_decision.approved_quantity is not None
    assert zone.risk_decision.approved_quantity is not None
    assert zone.risk_decision.approved_risk_stop_basis is not None
    assert baseline.risk_decision.approved_risk_stop_basis == Decimal(901)
    assert zone.risk_decision.approved_risk_stop_basis < Decimal(901)
    assert zone.risk_decision.approved_quantity < baseline.risk_decision.approved_quantity
    assert baseline.risk_decision.approved_risk_quote == Decimal(10)
    assert zone.risk_decision.approved_risk_quote == Decimal(10)
    assert population.opportunity_keys.count(None) == len(population.zone_decisions) - 1

    _, exposure_limited = _population()
    exposure_pair = next(
        (baseline, zone)
        for baseline, zone in zip(
            exposure_limited.baseline_population.decisions,
            exposure_limited.zone_decisions,
            strict=True,
        )
        if baseline.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    baseline_exposure, zone_exposure = exposure_pair
    assert baseline_exposure.risk_decision.approved_quantity is not None
    assert zone_exposure.risk_decision.approved_quantity is not None
    assert baseline_exposure.risk_decision.approved_risk_quote is not None
    assert zone_exposure.risk_decision.approved_risk_quote is not None
    assert (
        baseline_exposure.risk_decision.approved_quantity
        == zone_exposure.risk_decision.approved_quantity
    )
    assert (
        baseline_exposure.risk_decision.approved_risk_quote
        < zone_exposure.risk_decision.approved_risk_quote
    )


@pytest.mark.parametrize("pattern", ("breakout", "short"))
def test_zone_arm_uses_existing_fixed_2r_all_fill_and_accounting(pattern: str) -> None:
    store, population = _population(pattern)
    candidate = next(
        decision
        for decision in population.zone_decisions
        if decision.strategy_evaluation.action is not CandidateAction.NO_TRADE
    )
    bounds = population.baseline_population.configuration.bounds
    result = BreakoutZoneComparisonRunner(
        HistoricalExecutionSource(cast(HistoricalCandleStore, store))
    ).run_zone(
        candidate,
        cast(
            tuple[HistoricalStrategyContextObservation, ...],
            _contexts(population, candidate.identity.observed_at),
        ),
        ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15),
        BASE_COSTS,
        end=candidate.identity.observed_at + timedelta(hours=168),
    )
    branch = result.branches[-1]
    assert result.end == candidate.identity.observed_at + timedelta(hours=168)
    assert result.entry_intent is not None
    assert result.entry_intent.stop_basis is not None
    assert result.legacy_replay is not None
    assert result.legacy_replay.target_trigger_price is not None
    assert branch.entry_fill.fee_quote > 0
    assert branch.final_position.cumulative_fees > 0
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        risk_distance = (
            branch.entry_fill.fill_price - result.entry_intent.stop_basis
            if pattern == "breakout"
            else result.entry_intent.stop_basis - branch.entry_fill.fill_price
        )
        target = (
            branch.entry_fill.fill_price + Decimal(2) * risk_distance
            if pattern == "breakout"
            else branch.entry_fill.fill_price - Decimal(2) * risk_distance
        )
    assert result.legacy_replay.target_trigger_price == target
    result.validate()


def test_complete_report_round_trip_masks_pairs_and_zero_cells(
    long_evidence: tuple[
        object,
        BreakoutComparisonPopulation,
        tuple[BreakoutComparisonReplayResult, ...],
    ],
) -> None:
    _, population, results = long_evidence
    report = project_task_report(population, results)
    assert report["observation_count"] == 4
    assert report["promotion_eligibility"] == "INELIGIBLE_KNOWN_CANDIDATE_BOUND"
    masks = cast(dict[str, tuple[str, ...]], report["pre_outcome_masks"])
    assert masks["paired_intersection"] == tuple(
        sorted(set(masks["baseline"]) & set(masks["zone"]))
    )
    cells = cast(dict[str, dict[str, object]], report["cells"])
    cell = cells[f"{ComparisonArm.ZONE.value}:{AllFillScenario.BASE.value}"]
    groups = cast(dict[str, dict[str, object]], cell["grouped_full"])
    assert "BLOCK:P1" in groups and "WEEK:W9" in groups
    assert cast(dict[str, int], groups["SIDE:SHORT"]["terminal_counts"])
    paired = cast(dict[str, dict[str, object]], report["paired_closed"])
    assert "common_closed_denominator" in paired[AllFillScenario.BASE.value]
    base_zone = next(
        result
        for result in results
        if result.arm is ComparisonArm.ZONE
        and result.scenario is AllFillScenario.BASE
        and result.branches
    )
    assert base_zone.legacy_replay is not None
    assert base_zone.legacy_replay.same_bar_stop_precedence is True

    candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
    for result in results:
        record = comparison_result_record(result, population, candles=candles)
        stored_record = json.loads(_canonical(record))
        assert recover_comparison_result(stored_record, population, candles=candles) == result
    tampered = comparison_result_record(results[0], population, candles=candles)
    tampered["scenario"] = AllFillScenario.COMBINED_STRESS.value
    with pytest.raises(BreakoutZoneComparisonError, match="relabeled"):
        recover_comparison_result(json.loads(_canonical(tampered)), population, candles=candles)


def test_zone_recovery_horizon_open_and_unfilled_are_preserved() -> None:
    store, population = _population("breakout_invalid")
    candidate = next(
        item
        for item in population.zone_decisions
        if item.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    bounds = population.baseline_population.configuration.bounds
    series = ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15)
    cutoff = candidate.identity.observed_at + timedelta(hours=168)
    recovered = BreakoutZoneComparisonRunner(
        HistoricalExecutionSource(cast(HistoricalCandleStore, store))
    ).run_zone(
        candidate,
        cast(
            tuple[HistoricalStrategyContextObservation, ...],
            _contexts(population, candidate.identity.observed_at),
        ),
        series,
        BASE_COSTS,
        end=cutoff,
    )
    assert recovered.status is AllFillStatus.RECOVERY_CLOSED
    assert recovered.branches[-1].recovery is not None
    assert recovered.branches[-1].exit_fill is not None
    assert recovered.branches[-1].exit_fill.fill_at > recovered.branches[-1].entry_fill.fill_at

    unfilled = BreakoutZoneComparisonRunner(
        cast(ExecutionCandleSource, _StaticExecutionSource(()))
    ).run_zone(candidate, (), series, BASE_COSTS, end=cutoff)
    assert unfilled.status is AllFillStatus.ENTRY_UNFILLED
    assert unfilled.entry_unfilled is not None

    entry_candle = next(
        candle
        for candle in cast(HistoricalCandleStore, store).read(
            bounds.source_id,
            bounds.symbol,
            Timeframe.MINUTES_15,
            start=candidate.identity.observed_at,
            end=candidate.identity.observed_at + timedelta(minutes=15),
        )
    )
    opened = BreakoutZoneComparisonRunner(
        cast(ExecutionCandleSource, _StaticExecutionSource((entry_candle,)))
    ).run_zone(candidate, (), series, BASE_COSTS, end=cutoff)
    assert opened.status is AllFillStatus.RECOVERY_OPEN
    assert opened.branches[-1].recovery is not None
    assert opened.branches[-1].final_position.quantity != 0


def test_constructor_rejects_wrong_key_and_result_replacement(
    long_evidence: tuple[
        object,
        BreakoutComparisonPopulation,
        tuple[BreakoutComparisonReplayResult, ...],
    ],
) -> None:
    _, population, results = long_evidence
    changed = list(population.opportunity_keys)
    index = next(index for index, value in enumerate(changed) if value is not None)
    assert changed[index] is not None
    changed[index] = replace(cast(OpportunityKey, changed[index]), pivot_price=Decimal(1))
    with pytest.raises(BreakoutZoneComparisonError, match="contradictory"):
        BreakoutComparisonPopulation(
            population.task_id,
            population.baseline_population,
            population.zone_decisions,
            tuple(changed),
        )
    zone = next(result for result in results if result.arm is ComparisonArm.ZONE)
    with pytest.raises((BreakoutZoneComparisonError, AllFillReplayError)):
        replace(zone, costs=STRESS_COSTS, scenario=AllFillScenario.COMBINED_STRESS)


def test_legacy_entry_points_still_reject_zone_identity() -> None:
    store, population = _population()
    zone = next(
        item
        for item in population.zone_decisions
        if item.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    bounds = population.baseline_population.configuration.bounds
    source = HistoricalExecutionSource(cast(HistoricalCandleStore, store))
    series = ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15)
    contexts = cast(
        tuple[HistoricalStrategyContextObservation, ...],
        _contexts(population, zone.identity.observed_at),
    )
    cutoff = zone.identity.observed_at + timedelta(hours=168)
    with pytest.raises(BreakoutRetestReplayError, match="Breakout"):
        BreakoutRetestFixed2RReplayRunner(source).run(
            zone, contexts, series, BASE_COSTS, end=cutoff
        )
    with pytest.raises(AllFillReplayError, match="three-family"):
        ResearchAllFillReplayRunner(source).run(
            zone,
            contexts,
            series,
            BASE_COSTS,
            start=zone.identity.observed_at,
            end=cutoff,
        )


def test_grant_ledger_and_full_evidence_round_trip_without_rerun(
    tmp_path: Path,
    long_evidence: tuple[
        object,
        BreakoutComparisonPopulation,
        tuple[BreakoutComparisonReplayResult, ...],
    ],
) -> None:
    _, population, results = long_evidence
    grant = ComparisonExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        "5cb5b3d7-288b-44b1-a36f-d783c6f12b58",
        SYNTHETIC_SHA,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        "synthetic-zone-source",
        "synthetic-zone-dataset",
        "2" * 64,
        "3" * 64,
    )
    ledger = ComparisonLedgerStore.create(
        tmp_path,
        grant,
        actual_sha=SYNTHETIC_SHA,
        synthetic=True,
        synthetic_task_ids=(population.task_id,),
    )
    ledger.start(0)
    with pytest.raises(BreakoutZoneComparisonError, match=r"cannot start|rerun"):
        ledger.start(0)
    script = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/research/breakout_zone_comparison.py")
    )
    population_bytes = cast(
        Callable[[BreakoutComparisonPopulation], bytes], script["_population_bytes"]
    )
    results_bytes = cast(
        Callable[
            [
                BreakoutComparisonPopulation,
                tuple[BreakoutComparisonReplayResult, ...],
            ],
            bytes,
        ],
        script["_results_bytes"],
    )
    report = project_task_report(population, results)
    for suffix, payload in (
        ("population.json.gz", population_bytes(population)),
        ("results.jsonl.gz", results_bytes(population, results)),
        ("report.json", _canonical(report)),
    ):
        (ledger.directory / f"00.{suffix}").write_bytes(payload)
    digests = tuple(
        hashlib.sha256((ledger.directory / f"00.{suffix}").read_bytes()).hexdigest()
        for suffix in ("population.json.gz", "results.jsonl.gz", "report.json")
    )
    ledger.complete(
        0,
        population_sha256=digests[0],
        results_sha256=digests[1],
        report_sha256=digests[2],
    )
    read_task = cast(Callable[..., tuple[object, object, dict[str, object]]], script["_read_task"])
    rebuilt_population, rebuilt_results, rebuilt_report = read_task(ledger, 0)
    assert rebuilt_population == population
    assert rebuilt_results == results
    assert rebuilt_report == report
    projection = b"projection"
    (ledger.directory / "projection.json").write_bytes(projection)
    ledger.finalize(hashlib.sha256(projection).hexdigest())
    assert ledger.load().task_states == ("COMPLETED",)
    with pytest.raises(BreakoutZoneComparisonError, match=r"cannot start|rerun"):
        ledger.start(0)


def test_real_grant_cannot_be_replaced_by_old_or_synthetic_identity() -> None:
    old = ComparisonExecutionGrant(
        "EXTERNAL_REVIEWED_EXECUTION_GRANT",
        "686bd4a1-ffff-4391-8562-f1e4caf7a575",
        "316744efa9c4b50aa8d999b3c4d11fd874b4c28c",
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        SOURCE_ID.value,
        DATASET_VERSION,
        MANIFEST_SHA256,
        ARTIFACT_INDEX_SHA256,
    )
    with pytest.raises(BreakoutZoneComparisonError, match="code/protocol/tasks"):
        old.validate(actual_sha=SYNTHETIC_SHA)
    synthetic = replace(
        old,
        kind="SYNTHETIC_TEST_GRANT",
        executable_sha=SYNTHETIC_SHA,
    )
    with pytest.raises(BreakoutZoneComparisonError, match="impersonates"):
        synthetic.validate(actual_sha=SYNTHETIC_SHA, synthetic=True)


def test_spacing_masks_use_cross_arm_opportunity_identity() -> None:
    _, population = _population()
    baseline = spacing_mask(population, ComparisonArm.BASELINE)
    zone = spacing_mask(population, ComparisonArm.ZONE)
    assert baseline == zone
    assert len(baseline) == 1
    assert "breakout_retest" not in baseline[0]


def test_result_origin_is_not_forgeable_from_zone_as_reused(
    long_evidence: tuple[
        object,
        BreakoutComparisonPopulation,
        tuple[BreakoutComparisonReplayResult, ...],
    ],
) -> None:
    _, _, results = long_evidence
    zone = next(result for result in results if result.arm is ComparisonArm.ZONE)
    with pytest.raises(BreakoutZoneComparisonError, match="identity/origin"):
        replace(
            zone,
            origin=ResultOrigin.REUSED_ISSUE_187,
            original_authority_sha256="a" * 64,
            original_record_sha256="b" * 64,
        )
