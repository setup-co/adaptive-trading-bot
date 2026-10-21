"""Synthetic-only acceptance for BREAKOUT_RETEST_ZONE_COMPARISON_V1."""

from __future__ import annotations

import gzip
import hashlib
import json
import runpy
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.research import breakout_zone_comparison as comparison_module
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
    TASKS,
    BreakoutComparisonPopulation,
    BreakoutComparisonReplayResult,
    BreakoutZoneComparisonError,
    BreakoutZoneComparisonRunner,
    ComparisonArm,
    ComparisonExecutionGrant,
    ComparisonTask,
    ComparisonTaskProvenance,
    OpportunityKey,
    ResultOrigin,
    _canonical,
    _digest,
    _period_week,
    build_comparison_population,
    comparison_result_record,
    execute_comparison_task_from_retained_authority,
    load_granted_task_population,
    project_final_report,
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
    ResearchV2StudyDecisionPopulation,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    authority_result_record,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _research_health
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import HistoricalCandleStore
from adaptive_trading_bot.strategies import BreakoutRetestStrategy, CandidateAction
from adaptive_trading_bot.strategies.breakout_retest_zone_invalidation import (
    BreakoutRetestZoneInvalidationAudit,
    BreakoutRetestZoneInvalidationReasonCode,
)

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
    task_id = TASKS[0].task_id
    provenance = ComparisonTaskProvenance(
        "SYNTHETIC_TEST_GRANT",
        "5cb5b3d7-288b-44b1-a36f-d783c6f12b58",
        SYNTHETIC_SHA,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        configuration.bounds.source_id.value,
        baseline.dataset_version,
        baseline.manifest_sha256,
        "3" * 64,
        task_id,
        _digest(TASKS[0].to_record()),
    )
    return store, build_comparison_population(
        baseline,
        task_id=task_id,
        provenance=provenance,
    )


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
                    provenance=population.provenance,
                    population_sha256=population.integrity_seal,
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
                    provenance=population.provenance,
                    population_sha256=population.integrity_seal,
                )
            )
    return tuple(output)


def _write_retained_baseline_authority(
    path: Path,
    store: object,
    population: BreakoutComparisonPopulation,
) -> str:
    """Create one genuine retained predecessor artifact before comparison execution."""

    source = HistoricalExecutionSource(cast(HistoricalCandleStore, store))
    runner = ResearchAllFillReplayRunner(source)
    bounds = population.baseline_population.configuration.bounds
    series = ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15)
    candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
    lines: list[bytes] = []
    for scenario, costs in (
        (AllFillScenario.BASE, BASE_COSTS),
        (AllFillScenario.COMBINED_STRESS, STRESS_COSTS),
    ):
        for index, decision in enumerate(population.baseline_population.decisions):
            cutoff = decision.identity.observed_at + timedelta(hours=168)
            original = runner.run(
                decision,
                cast(
                    tuple[HistoricalStrategyContextObservation, ...],
                    _contexts(population, decision.identity.observed_at),
                ),
                series,
                costs,
                start=decision.identity.observed_at,
                end=cutoff,
            )
            lines.append(
                _canonical(
                    authority_result_record(
                        population.baseline_population,
                        index,
                        scenario,
                        original,
                        candles=candles,
                    )
                )
            )
    payload = gzip.compress(b"".join(line + b"\n" for line in lines), mtime=0)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _population_with_zone_geometry_rejection() -> tuple[object, BreakoutComparisonPopulation]:
    """Create a sealed causal population whose baseline-only admission changes the mask."""

    store, original = _population()
    baseline = original.baseline_population
    candidate_at = next(
        decision.identity.observed_at
        for decision in baseline.decisions
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    observations: list[HistoricalStrategyContextObservation] = []
    strategy = BreakoutRetestStrategy()
    risk = RiskManager(baseline.configuration.risk_configuration)
    safety = OperationalSafetyGate(baseline.configuration.safety_configuration)
    decisions: list[DecisionJournalEntry[object, object]] = []
    bounds = baseline.configuration.bounds
    for observation in baseline.contexts:
        context = observation.context
        if context.observed_at == candidate_at:
            candle = replace(
                context.candle_15m,
                open=Decimal(902),
                high=Decimal("911.2"),
                close=Decimal("911.2"),
            )
            context = replace(context, candle_15m=candle)
        observations.append(HistoricalStrategyContextObservation(context))
        evaluation = strategy.evaluate(context)
        if not bounds.decision_start < context.observed_at <= bounds.decision_end:
            continue
        equity = baseline.configuration.research_equity_quote
        portfolio = RiskPortfolioSnapshot(
            context.observed_at,
            "USDT",
            equity,
            equity,
            equity,
            (),
        )
        risk_decision = risk.evaluate(evaluation, portfolio)
        safety_decision = safety.evaluate(
            risk_decision,
            _research_health(context, baseline.configuration.safety_assumptions),
        )
        decisions.append(DecisionJournalEntry(context, evaluation, risk_decision, safety_decision))
    rebuilt = ResearchV2DecisionPopulation(
        baseline.configuration,
        tuple(decisions),
        tuple(observations),
        baseline.source_id,
        baseline.dataset_version,
        baseline.manifest_sha256,
    )
    rebuilt.validate()
    return store, build_comparison_population(
        rebuilt,
        task_id=original.task_id,
        provenance=original.provenance,
    )


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
        provenance=population.provenance,
        population_sha256=population.integrity_seal,
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
        provenance=population.provenance,
        population_sha256=population.integrity_seal,
    )
    assert recovered.status is AllFillStatus.RECOVERY_CLOSED
    assert recovered.branches[-1].recovery is not None
    assert recovered.branches[-1].exit_fill is not None
    assert recovered.branches[-1].exit_fill.fill_at > recovered.branches[-1].entry_fill.fill_at

    unfilled = BreakoutZoneComparisonRunner(
        cast(ExecutionCandleSource, _StaticExecutionSource(()))
    ).run_zone(
        candidate,
        (),
        series,
        BASE_COSTS,
        end=cutoff,
        provenance=population.provenance,
        population_sha256=population.integrity_seal,
    )
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
    ).run_zone(
        candidate,
        (),
        series,
        BASE_COSTS,
        end=cutoff,
        provenance=population.provenance,
        population_sha256=population.integrity_seal,
    )
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
            population.provenance,
        )
    zone = next(result for result in results if result.arm is ComparisonArm.ZONE)
    with pytest.raises((BreakoutZoneComparisonError, AllFillReplayError)):
        replace(zone, costs=STRESS_COSTS, scenario=AllFillScenario.COMBINED_STRESS)


def test_result_owns_scenario_and_snapshot_identity_before_and_after_storage(
    long_evidence: tuple[
        object,
        BreakoutComparisonPopulation,
        tuple[BreakoutComparisonReplayResult, ...],
    ],
) -> None:
    _, population, results = long_evidence
    zone = next(
        result
        for result in results
        if result.arm is ComparisonArm.ZONE and result.scenario is AllFillScenario.BASE
    )
    with pytest.raises(BreakoutZoneComparisonError, match="identity/origin"):
        replace(zone, scenario=AllFillScenario.COMBINED_STRESS)
    with pytest.raises(BreakoutZoneComparisonError, match="identity/origin"):
        replace(zone, execution_snapshot_digest="0" * 64)

    candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
    record = json.loads(_canonical(comparison_result_record(zone, population, candles=candles)))
    record["scenario"] = AllFillScenario.COMBINED_STRESS.value
    with pytest.raises(BreakoutZoneComparisonError, match="relabeled"):
        recover_comparison_result(record, population, candles=candles)

    snapshot_record = json.loads(
        _canonical(comparison_result_record(zone, population, candles=candles))
    )
    named = snapshot_record["result"][2]
    digest_field = next(item for item in named if item[0] == "execution_snapshot_digest")
    digest_field[1] = "0" * 64
    with pytest.raises((BreakoutZoneComparisonError, AllFillReplayError, ValueError)):
        recover_comparison_result(snapshot_record, population, candles=candles)


def test_population_reconstructs_zone_policy_risk_and_safety_authority() -> None:
    _, population = _population()
    candidate_index = next(
        index
        for index, decision in enumerate(population.zone_decisions)
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    candidate = population.zone_decisions[candidate_index]
    audit = cast(
        BreakoutRetestZoneInvalidationAudit,
        candidate.strategy_evaluation.rule_audit,
    )
    forged_evaluation = replace(
        candidate.strategy_evaluation,
        rule_audit=replace(audit, policy_id="FORGED_ZONE_POLICY"),
    )
    forged_policy = replace(candidate, strategy_evaluation=forged_evaluation)
    decisions = list(population.zone_decisions)
    decisions[candidate_index] = forged_policy
    with pytest.raises(BreakoutZoneComparisonError, match="Strategy/Risk/Safety"):
        BreakoutComparisonPopulation(
            population.task_id,
            population.baseline_population,
            tuple(decisions),
            population.opportunity_keys,
            population.provenance,
        )

    configuration = population.baseline_population.configuration
    changed_risk_configuration = replace(
        configuration.risk_configuration,
        risk_per_trade_fraction=Decimal("0.0005"),
    )
    context = candidate.context
    equity = configuration.research_equity_quote
    portfolio = RiskPortfolioSnapshot(
        context.observed_at,
        "USDT",
        equity,
        equity,
        equity,
        (),
    )
    changed_risk = RiskManager(changed_risk_configuration).evaluate(
        candidate.strategy_evaluation,
        portfolio,
    )
    changed_safety = OperationalSafetyGate(configuration.safety_configuration).evaluate(
        changed_risk,
        _research_health(context, configuration.safety_assumptions),
    )
    forged_risk = DecisionJournalEntry(
        context,
        candidate.strategy_evaluation,
        changed_risk,
        changed_safety,
    )
    decisions = list(population.zone_decisions)
    decisions[candidate_index] = forged_risk
    with pytest.raises(BreakoutZoneComparisonError, match="Strategy/Risk/Safety"):
        BreakoutComparisonPopulation(
            population.task_id,
            population.baseline_population,
            tuple(decisions),
            population.opportunity_keys,
            population.provenance,
        )

    changed_assumptions = replace(
        configuration.safety_assumptions,
        assumed_spread_bps=Decimal("999"),
    )
    changed_safety = OperationalSafetyGate(configuration.safety_configuration).evaluate(
        candidate.risk_decision,
        _research_health(context, changed_assumptions),
    )
    forged_safety = DecisionJournalEntry(
        context,
        candidate.strategy_evaluation,
        candidate.risk_decision,
        changed_safety,
    )
    decisions = list(population.zone_decisions)
    decisions[candidate_index] = forged_safety
    with pytest.raises(BreakoutZoneComparisonError, match="Strategy/Risk/Safety"):
        BreakoutComparisonPopulation(
            population.task_id,
            population.baseline_population,
            tuple(decisions),
            population.opportunity_keys,
            population.provenance,
        )


@pytest.mark.parametrize(
    ("decision_at", "expected"),
    (
        (datetime(2026, 8, 3, 0, 15, tzinfo=UTC), ("P1", "W1")),
        (datetime(2026, 8, 10, 0, 0, tzinfo=UTC), ("P1", "W1")),
        (datetime(2026, 8, 24, 0, 0, tzinfo=UTC), ("P1", "W3")),
        (datetime(2026, 8, 24, 0, 15, tzinfo=UTC), ("P2", "W4")),
        (datetime(2026, 9, 14, 0, 0, tzinfo=UTC), ("P2", "W6")),
        (datetime(2026, 9, 14, 0, 15, tzinfo=UTC), ("P3", "W7")),
        (datetime(2026, 10, 5, 0, 0, tzinfo=UTC), ("P3", "W9")),
    ),
)
def test_reporting_calendar_uses_signal_candle_open(
    decision_at: datetime,
    expected: tuple[str, str],
) -> None:
    assert _period_week(decision_at) == expected


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


def test_retained_import_execution_publication_and_final_verifier_are_one_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, population = _population_with_zone_geometry_rejection()
    baseline_mask = spacing_mask(population, ComparisonArm.BASELINE)
    zone_mask = spacing_mask(population, ComparisonArm.ZONE)
    assert len(baseline_mask) == 1
    assert zone_mask == ()
    rejected_index = next(
        index
        for index, decision in enumerate(population.baseline_population.decisions)
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    rejected = population.zone_decisions[rejected_index]
    assert rejected.strategy_evaluation.action is CandidateAction.NO_TRADE
    assert rejected.strategy_evaluation.reasons_against == (
        BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_TOO_WIDE,
    )
    assert all(
        decision.strategy_evaluation.action is CandidateAction.NO_TRADE
        for decision in population.zone_decisions[rejected_index:]
    )
    authority_path = tmp_path / "retained-baseline.jsonl.gz"
    authority_sha = _write_retained_baseline_authority(
        authority_path,
        store,
        population,
    )

    baseline_calls = 0

    def reject_baseline_rerun(
        self: ResearchAllFillReplayRunner,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        start: datetime,
        end: datetime,
    ) -> object:
        del self, contexts, series, costs, start, end
        nonlocal baseline_calls
        baseline_calls += 1
        raise AssertionError(f"retained baseline was rerun for {decision.identity.strategy_id}")

    monkeypatch.setattr(ResearchAllFillReplayRunner, "run", reject_baseline_rerun)
    results = execute_comparison_task_from_retained_authority(
        population,
        authority_path=authority_path,
        authority_sha256=authority_sha,
        source=HistoricalExecutionSource(cast(HistoricalCandleStore, store)),
    )
    assert baseline_calls == 0
    assert {result.origin for result in results if result.arm is ComparisonArm.BASELINE} == {
        ResultOrigin.REUSED_ISSUE_187
    }

    grant = ComparisonExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        population.provenance.run_uuid,
        SYNTHETIC_SHA,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        population.provenance.source_id,
        population.provenance.dataset_version,
        population.provenance.manifest_sha256,
        population.provenance.artifact_index_sha256,
    )
    ledger = ComparisonLedgerStore.create(
        tmp_path / "ledger",
        grant,
        actual_sha=SYNTHETIC_SHA,
        synthetic=True,
        synthetic_task_ids=(population.task_id,),
    )
    with pytest.raises(BreakoutZoneComparisonError, match="partial comparison"):
        ledger.finalize("0" * 64)
    ledger.start(0)
    script = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/research/breakout_zone_comparison.py")
    )
    population_bytes = cast(
        Callable[[BreakoutComparisonPopulation], bytes], script["_population_bytes"]
    )
    results_bytes = cast(
        Callable[
            [BreakoutComparisonPopulation, tuple[BreakoutComparisonReplayResult, ...]],
            bytes,
        ],
        script["_results_bytes"],
    )
    report = project_task_report(population, results)
    admissions = cast(dict[str, tuple[str, ...]], report["admission_sets"])
    masks = cast(dict[str, tuple[str, ...]], report["pre_outcome_masks"])
    assert len(admissions["baseline_only"]) == 1
    assert admissions["zone_only"] == ()
    assert masks["baseline"] != masks["zone"]
    assert masks["paired_intersection"] == ()
    payloads = (
        population_bytes(population),
        results_bytes(population, results),
        _canonical(report),
    )
    suffixes = ("population.json.gz", "results.jsonl.gz", "report.json")
    for suffix, payload in zip(suffixes, payloads, strict=True):
        (ledger.directory / f"00.{suffix}").write_bytes(payload)
    digests = tuple(hashlib.sha256(payload).hexdigest() for payload in payloads)
    ledger.complete(
        0,
        population_sha256=digests[0],
        results_sha256=digests[1],
        report_sha256=digests[2],
    )
    projection = project_final_report(
        ((population, results, report),),
        tasks=(TASKS[0],),
        enforce_real_candidate_bound=False,
    )
    projection_bytes = _canonical(projection)
    (ledger.directory / "projection.json").write_bytes(projection_bytes)
    ledger.finalize(hashlib.sha256(projection_bytes).hexdigest())
    verify_synthetic = cast(Callable[..., dict[str, object]], script["_verify_synthetic"])
    authority_reconstructions = 0
    original_reconstruct = comparison_module._reconstruct_zone_decisions

    def counted_reconstruct(
        baseline_population: ResearchV2DecisionPopulation,
    ) -> tuple[DecisionJournalEntry[object, object], ...]:
        nonlocal authority_reconstructions
        authority_reconstructions += 1
        return original_reconstruct(baseline_population)

    monkeypatch.setattr(
        comparison_module,
        "_reconstruct_zone_decisions",
        counted_reconstruct,
    )
    assert (
        verify_synthetic(
            tmp_path,
            ledger.directory,
            tasks=(TASKS[0],),
            retained_authorities=((authority_path, authority_sha),),
        )
        == projection
    )
    assert authority_reconstructions == 1
    assert cast(dict[str, object], projection["run_identity"])["input_kind"] == (
        "SYNTHETIC_TEST_GRANT"
    )
    panel_cells = cast(dict[str, dict[str, object]], projection["cells"])
    assert all(
        {"full", "masked", "grouped_full", "grouped_masked"} <= cell.keys()
        for cell in panel_cells.values()
    )
    paired = cast(dict[str, dict[str, object]], projection["paired"])
    assert all({"full", "intersection_mask"} == set(cell) for cell in paired.values())
    results_path = ledger.directory / "00.results.jsonl.gz"
    original_results = results_path.read_bytes()
    results_path.write_bytes(original_results + b"tamper")
    with pytest.raises(BreakoutZoneComparisonError, match="artifact changed"):
        verify_synthetic(
            tmp_path,
            ledger.directory,
            tasks=(TASKS[0],),
            retained_authorities=((authority_path, authority_sha),),
        )
    results_path.write_bytes(original_results)
    with pytest.raises(BreakoutZoneComparisonError, match="cannot finalize more than once"):
        ledger.finalize(hashlib.sha256(projection_bytes).hexdigest())


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


def test_invalid_grant_fails_before_population_or_strategy_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def forbidden_loader(
        repo: Path,
        task: ComparisonTask,
    ) -> ResearchV2StudyDecisionPopulation:
        del repo, task
        nonlocal calls
        calls += 1
        raise AssertionError("population loader must not run before grant validation")

    monkeypatch.setattr(comparison_module, "_load_population", forbidden_loader)
    invalid = ComparisonExecutionGrant(
        "EXTERNAL_REVIEWED_EXECUTION_GRANT",
        "5cb5b3d7-288b-44b1-a36f-d783c6f12b58",
        SYNTHETIC_SHA,
        "0" * 64,
        TASK_PLAN_SHA256,
        SOURCE_ID.value,
        DATASET_VERSION,
        MANIFEST_SHA256,
        ARTIFACT_INDEX_SHA256,
    )
    with pytest.raises(BreakoutZoneComparisonError, match="code/protocol/tasks"):
        load_granted_task_population(
            tmp_path,
            TASKS[0],
            invalid,
            actual_sha=SYNTHETIC_SHA,
        )
    assert calls == 0


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
