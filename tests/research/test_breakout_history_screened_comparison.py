"""Synthetic acceptance for BREAKOUT_RETEST_HISTORY_SCREENED_180D_COMPARISON_V1."""

from __future__ import annotations

import json
import runpy
import shutil
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_trading_bot.backtest import ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.research import breakout_history_screened_comparison as protocol
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    ACCEPTED_BINDING,
    PANEL,
    PROTOCOL_SHA256,
    STATUS_CELLS,
    TASK_PLAN_SHA256,
    TASKS,
    ComparisonArm,
    HistoryScreenedAllFillRunner,
    HistoryScreenedComparisonError,
    HistoryScreenedConfiguration,
    HistoryScreenedDecisionPopulation,
    HistoryScreenedExecutionGrant,
    HistoryScreenedInputBinding,
    HistoryScreenedTask,
    evaluate_granted_task,
    evaluate_synthetic_task,
    preflight,
    report_block,
    report_week,
    synthetic_binding,
)
from adaptive_trading_bot.research.breakout_history_screened_execution import (
    HistoryScreenedRunAssociation,
    _exact_sum,
    _metrics,
    execute_task,
    final_projection,
    population_bytes,
    read_population,
    read_results,
    task_report,
    validate_task_associations,
    verify_task,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import (
    HistoryScreenedLedgerStore,
)
from adaptive_trading_bot.research.trend_pullback_corpus import _research_health
from adaptive_trading_bot.risk import RiskManager, RiskPortfolioSnapshot
from adaptive_trading_bot.risk.safety import OperationalSafetyGate
from adaptive_trading_bot.storage import IntegrityReport, MergeResult, SourceId
from adaptive_trading_bot.strategies.breakout_retest_zone_invalidation import (
    BreakoutRetestZoneInvalidationReasonCode,
)
from adaptive_trading_bot.strategies.core import CandidateAction

_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_research_v2_admission.py")))
_OldSyntheticStore = cast(type, _HELPERS["SyntheticEpochStore"])
_OLD_SYMBOL = cast(TradingSymbol, _HELPERS["SYMBOL"])
_START = cast(datetime, _HELPERS["START"])
_SHA = "1" * 40


class SyntheticHistoryScreenedStore:
    """Adapt the established candle fixture to the distinct new source authority."""

    source_id = protocol.SYNTHETIC_SOURCE_ID

    def __init__(self, *, pattern: str = "breakout") -> None:
        old = _OldSyntheticStore(_OLD_SYMBOL, pattern=pattern, hours=380)
        self.symbol = TradingSymbol("BTC", "USDT")
        self.start = old.start
        self.hours = old.hours
        self.candles = {
            frame: tuple(replace(candle, symbol=self.symbol) for candle in values)
            for frame, values in old.candles.items()
        }
        self._binding: HistoryScreenedInputBinding | None = None

    @property
    def input_binding(self) -> HistoryScreenedInputBinding:
        assert self._binding is not None
        return self._binding

    def bind(self, binding: HistoryScreenedInputBinding) -> None:
        if self._binding is not None:
            raise AssertionError("synthetic store binding is immutable")
        self._binding = binding

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        del source_id, candles
        raise AssertionError("synthetic store is immutable")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        assert source_id == self.source_id and symbol == self.symbol
        assert start is not None and end is not None
        return tuple(
            candle for candle in self.candles[timeframe] if start <= candle.open_time < end
        )

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        candles = self.read(source_id, symbol, timeframe, start=start, end=end)
        return IntegrityReport(source_id, symbol, timeframe, start, end, len(candles), ())

    def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        assert symbol == self.symbol
        return ((self.start, self.start + timedelta(hours=self.hours)),)

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        assert symbol == self.symbol
        return ((self.start + timedelta(hours=200), self.start + timedelta(hours=self.hours)),)


def _synthetic(
    pattern: str = "breakout",
) -> tuple[SyntheticHistoryScreenedStore, HistoryScreenedConfiguration]:
    store = SyntheticHistoryScreenedStore(pattern=pattern)
    task = HistoryScreenedTask(
        0,
        "BTCUSDT",
        context_start=_START,
        signal_start=_START + timedelta(hours=200),
        signal_end=_START + timedelta(hours=202),
        follow_up_end=_START + timedelta(hours=380),
    )
    binding = synthetic_binding(store, task)
    store.bind(binding)
    return store, HistoryScreenedConfiguration(task, binding)


def _grant(
    binding: HistoryScreenedInputBinding,
    run_uuid: str = "synthetic-history-screened-run",
) -> HistoryScreenedExecutionGrant:
    return HistoryScreenedExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        run_uuid,
        _SHA,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        binding.source_id.value,
        binding.dataset_version,
        binding.manifest_sha256,
    )


@pytest.fixture(scope="module")
def typed_authorities(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[
    SyntheticHistoryScreenedStore,
    HistoryScreenedConfiguration,
    HistoryScreenedExecutionGrant,
    HistoryScreenedRunAssociation,
    HistoryScreenedDecisionPopulation,
    tuple[protocol.HistoryScreenedReplayResult, ...],
    Path,
]:
    store, configuration = _synthetic()
    grant = _grant(configuration.input_binding, "typed-authority-run")
    run = HistoryScreenedRunAssociation.from_grant(
        grant, configuration.input_binding, actual_sha=_SHA
    )
    directory = tmp_path_factory.mktemp("typed-authorities")
    execute_task(
        store,
        configuration,
        grant=grant,
        actual_sha=_SHA,
        directory=directory,
        task_index=0,
        synthetic=True,
    )
    population = read_population((directory / "00.population.json.gz").read_bytes())
    results = read_results((directory / "00.results.jsonl.gz").read_bytes())
    return store, configuration, grant, run, population, results, directory


def _decision_with_evaluation(
    population: HistoryScreenedDecisionPopulation,
    original: DecisionJournalEntry[object, object],
    evaluation: object,
    *,
    equity: Decimal = Decimal("10000"),
    day_start: Decimal = Decimal("10000"),
    peak: Decimal = Decimal("10000"),
) -> DecisionJournalEntry[object, object]:
    risk = RiskManager(population.configuration.risk_configuration).evaluate(
        cast(Any, evaluation),
        RiskPortfolioSnapshot(
            original.identity.observed_at,
            "USDT",
            equity,
            day_start,
            peak,
            (),
        ),
    )
    safety = OperationalSafetyGate(population.configuration.safety_configuration).evaluate(
        risk,
        _research_health(
            original.context,
            population.configuration.safety_assumptions,
        ),
    )
    return DecisionJournalEntry(original.context, cast(Any, evaluation), risk, safety)


def _population_with_decisions(
    population: HistoryScreenedDecisionPopulation,
    *,
    baseline: tuple[DecisionJournalEntry[object, object], ...] | None = None,
    zone: tuple[DecisionJournalEntry[object, object], ...] | None = None,
) -> HistoryScreenedDecisionPopulation:
    baseline_decisions = baseline or population.baseline_decisions
    zone_decisions = zone or population.zone_decisions
    return HistoryScreenedDecisionPopulation(
        population.configuration,
        population.contexts,
        baseline_decisions,
        zone_decisions,
        population.opportunity_keys,
        protocol._spacing_mask_keys(baseline_decisions, population.opportunity_keys),
        protocol._spacing_mask_keys(zone_decisions, population.opportunity_keys),
    )


def _results_for_population(
    store: SyntheticHistoryScreenedStore,
    original: HistoryScreenedDecisionPopulation,
    changed: HistoryScreenedDecisionPopulation,
    results: tuple[protocol.HistoryScreenedReplayResult, ...],
) -> tuple[protocol.HistoryScreenedReplayResult, ...]:
    population_sha = protocol._sha_bytes(population_bytes(changed))
    series = ExecutionSeries(
        store.source_id, changed.configuration.task.symbol, Timeframe.MINUTES_15
    )
    runner = HistoryScreenedAllFillRunner(store)
    output: list[protocol.HistoryScreenedReplayResult] = []
    for result in results:
        original_decisions = (
            original.baseline_decisions
            if result.arm is ComparisonArm.BASELINE
            else original.zone_decisions
        )
        changed_decisions = (
            changed.baseline_decisions
            if result.arm is ComparisonArm.BASELINE
            else changed.zone_decisions
        )
        index = original_decisions.index(result.decision)
        decision = changed_decisions[index]
        if decision == result.decision:
            output.append(replace(result, population_sha256=population_sha))
        else:
            output.append(
                runner.run(
                    decision,
                    changed.contexts,
                    series,
                    result.scenario,
                    arm=result.arm,
                    task_id=changed.configuration.task.task_id,
                    population_sha256=population_sha,
                )
            )
    return tuple(output)


def test_fixed_protocol_calendar_and_distinct_old_comparator() -> None:
    from adaptive_trading_bot.research import breakout_zone_comparison as old

    assert protocol.PROTOCOL_VERSION == "BREAKOUT_RETEST_HISTORY_SCREENED_180D_COMPARISON_V1"
    assert old.PROTOCOL_VERSION == "BREAKOUT_RETEST_ZONE_COMPARISON_V1"
    assert protocol.PROTOCOL_SHA256 != old.PROTOCOL_SHA256
    assert PANEL[-1] == "LINKUSDT"
    assert len(TASKS) == 10
    assert STATUS_CELLS == 691_200
    assert len(PROTOCOL_SHA256) == len(TASK_PLAN_SHA256) == 64


def test_real_data_preflight_is_inert_and_exact() -> None:
    result = preflight(Path(__file__).resolve().parents[2])
    assert result["status"] == "DATA_ONLY_PREFLIGHT_OK"
    assert result["protocol_sha256"] == PROTOCOL_SHA256
    assert result["task_plan_sha256"] == TASK_PLAN_SHA256
    assert cast(dict[str, int], result["counts"]) == {
        "tasks": 10,
        "slots_per_member": 17_280,
        "panel_slots": 172_800,
        "arm_observations": 345_600,
        "status_cells": 691_200,
    }
    assert all(
        not item["strategy_evaluated"] for item in cast(list[dict[str, Any]], result["tasks"])
    )


def test_both_real_evaluators_traverse_same_synthetic_causal_stream() -> None:
    store, configuration = _synthetic()
    population = evaluate_synthetic_task(store, configuration)
    assert len(population.baseline_decisions) == 8
    assert len(population.zone_decisions) == 8
    assert tuple(item.context for item in population.contexts)
    assert all(
        baseline.context == zone.context
        for baseline, zone in zip(
            population.baseline_decisions, population.zone_decisions, strict=True
        )
    )
    assert {decision.identity.strategy_id for decision in population.baseline_decisions} == {
        "breakout_retest"
    }
    assert {decision.identity.strategy_id for decision in population.zone_decisions} == {
        "breakout_retest_zone_invalidation"
    }


@pytest.mark.parametrize(
    ("pattern", "candidate"),
    (("breakout", "LONG_CANDIDATE"), ("short", "SHORT_CANDIDATE")),
)
def test_both_directional_admission_paths_use_the_real_evaluators(
    pattern: str, candidate: str
) -> None:
    store, configuration = _synthetic(pattern)
    population = evaluate_synthetic_task(store, configuration)
    for decisions in (population.baseline_decisions, population.zone_decisions):
        admitted = [
            decision
            for decision in decisions
            if decision.strategy_evaluation.action.value == candidate
        ]
        assert len(admitted) == 1
        assert admitted[0].risk_decision.status.value == "APPROVED"
        assert admitted[0].safety_decision.status.value == "CLEARED"


def test_actual_task_storage_ledger_and_final_verify_round_trip(tmp_path: Path) -> None:
    store, configuration = _synthetic()
    grant = _grant(configuration.input_binding)
    run = HistoryScreenedRunAssociation.from_grant(
        grant, configuration.input_binding, actual_sha=_SHA
    )
    ledger = HistoryScreenedLedgerStore.create(
        tmp_path, grant, actual_sha=_SHA, synthetic_task_ids=(configuration.task.task_id,)
    )
    ledger.start(0)
    artifacts = execute_task(
        store,
        configuration,
        grant=grant,
        actual_sha=_SHA,
        directory=ledger.directory,
        task_index=0,
        synthetic=True,
    )
    ledger.complete(
        0,
        population_sha256=artifacts.population_sha256,
        results_sha256=artifacts.results_sha256,
        report_sha256=artifacts.report_sha256,
    )
    checked = verify_task(ledger.directory, 0, expected_task=configuration.task, run=run)
    assert checked["task_id"] == configuration.task.task_id
    population = read_population((ledger.directory / "00.population.json.gz").read_bytes())
    results = read_results((ledger.directory / "00.results.jsonl.gz").read_bytes())
    assert len(results) == len(population.baseline_decisions) * 4
    assert {result.arm.value for result in results} == {"BASELINE", "ZONE"}
    assert {result.scenario.value for result in results} == {"BASE", "STRESS"}
    assert any(result.branches for result in results)
    assert all(result.end == result.start + timedelta(hours=168) for result in results)
    projection = final_projection(ledger.directory, run=run, tasks=(configuration.task,))
    projection_payload = protocol._canonical(projection)
    projection_path = ledger.directory / "projection.json"
    projection_path.write_bytes(projection_payload)
    ledger.finalize(protocol._sha_bytes(projection_payload))
    state = ledger.load()
    assert state.task_states == ("COMPLETED",)
    assert state.projection_sha256 == protocol._sha_bytes(projection_payload)
    assert projection["classifications"] == {
        "BASELINE": "INSUFFICIENT_EVIDENCE",
        "ZONE": "INSUFFICIENT_EVIDENCE",
    }


def test_typed_task_association_rejects_foreign_and_incomplete_cells(
    typed_authorities: tuple[Any, ...],
) -> None:
    _store, configuration, _grant_value, run, population, results, _directory = typed_authorities
    population_sha = protocol._sha_bytes(population_bytes(population))
    target = results[0]
    with pytest.raises(HistoryScreenedComparisonError, match="task/population"):
        task_report(
            population,
            (replace(target, population_sha256="f" * 64), *results[1:]),
            expected_task=configuration.task,
            run=run,
        )
    with pytest.raises(HistoryScreenedComparisonError, match="task/population"):
        task_report(
            population,
            (replace(target, task_id="foreign-nonempty-task"), *results[1:]),
            expected_task=configuration.task,
            run=run,
        )

    _foreign_store, foreign_configuration = _synthetic("short")
    foreign_population = evaluate_synthetic_task(_foreign_store, foreign_configuration)
    result_index = next(
        index
        for index, result in enumerate(results)
        if result.status.value == "NO_SIGNAL"
        and result.arm.value == "BASELINE"
        and result.scenario.value == "BASE"
        and foreign_population.baseline_decisions[
            population.baseline_decisions.index(result.decision)
        ].strategy_evaluation.action
        is CandidateAction.NO_TRADE
    )
    authority_index = population.baseline_decisions.index(results[result_index].decision)
    foreign_decision = foreign_population.baseline_decisions[authority_index]
    assert foreign_decision.identity == results[result_index].decision.identity
    assert foreign_decision != results[result_index].decision
    foreign_result = replace(results[result_index], decision=foreign_decision)
    with pytest.raises(HistoryScreenedComparisonError, match="task/population"):
        task_report(
            population,
            (*results[:result_index], foreign_result, *results[result_index + 1 :]),
            expected_task=configuration.task,
            run=run,
        )

    with pytest.raises(HistoryScreenedComparisonError, match="duplicated"):
        validate_task_associations(
            population,
            (*results, results[0]),
            expected_task=configuration.task,
            population_sha256=population_sha,
        )
    with pytest.raises(HistoryScreenedComparisonError, match="missing"):
        validate_task_associations(
            population,
            results[:-1],
            expected_task=configuration.task,
            population_sha256=population_sha,
        )


def test_final_verifier_rejects_task_and_run_relabeling(
    typed_authorities: tuple[Any, ...], tmp_path: Path
) -> None:
    _store, configuration, _grant_value, run, population, _results, directory = typed_authorities
    for suffix in ("population.json.gz", "results.jsonl.gz", "report.json"):
        shutil.copyfile(directory / f"00.{suffix}", tmp_path / f"00.{suffix}")
        shutil.copyfile(directory / f"00.{suffix}", tmp_path / f"01.{suffix}")
    duplicated_task = replace(configuration.task, index=1)
    with pytest.raises(HistoryScreenedComparisonError, match=r"frozen plan|another task"):
        final_projection(
            tmp_path,
            run=run,
            tasks=(configuration.task, duplicated_task),
        )
    with pytest.raises(HistoryScreenedComparisonError, match="saved task report"):
        verify_task(
            directory,
            0,
            expected_task=configuration.task,
            run=replace(run, run_uuid="another-synthetic-run"),
        )
    with pytest.raises(HistoryScreenedComparisonError, match="run association"):
        task_report(
            population,
            read_results((directory / "00.results.jsonl.gz").read_bytes()),
            expected_task=configuration.task,
            run=replace(run, input_kind="EXTERNAL_REVIEWED_EXECUTION_GRANT"),
        )


def test_candidate_rejection_admission_and_mask_partitions_use_typed_authority(
    typed_authorities: tuple[Any, ...],
) -> None:
    store, configuration, _grant_value, run, population, results, _directory = typed_authorities
    candidate_index = next(
        index
        for index, decision in enumerate(population.baseline_decisions)
        if decision.strategy_evaluation.action is CandidateAction.LONG_CANDIDATE
    )
    zone_candidate = population.zone_decisions[candidate_index]
    rejected_evaluation = replace(
        zone_candidate.strategy_evaluation,
        action=CandidateAction.NO_TRADE,
        reasons_for=(),
        reasons_against=(BreakoutRetestZoneInvalidationReasonCode.ZONE_INVALIDATION_TOO_WIDE,),
        reference_price=None,
        invalidation_reference_price=None,
    )
    rejected_zone = list(population.zone_decisions)
    rejected_zone[candidate_index] = _decision_with_evaluation(
        population,
        zone_candidate,
        rejected_evaluation,
    )
    baseline_only = _population_with_decisions(population, zone=tuple(rejected_zone))
    baseline_only_results = _results_for_population(store, population, baseline_only, results)
    baseline_only_report = task_report(
        baseline_only,
        baseline_only_results,
        expected_task=configuration.task,
        run=run,
    )
    baseline_masks = cast(dict[str, Any], baseline_only_report["masks"])
    assert baseline_only_report["candidates"] == {
        "baseline": 1,
        "zone": 0,
        "underlying_opportunities": 1,
        "zone_rejections": 1,
    }
    assert baseline_only_report["admissions"] == {
        "baseline": 1,
        "zone": 0,
        "common": 0,
        "baseline_only": 1,
        "zone_only": 0,
        "union": 1,
        "membership": {
            "common": [],
            "baseline_only": baseline_masks["baseline"],
            "zone_only": [],
        },
    }
    assert baseline_masks["zone"] == []
    assert baseline_only_report["zone_rejection_reasons"] == {"ZONE_INVALIDATION_TOO_WIDE": 1}
    assert baseline_only_report["both_no_signal_observations"] == 7
    outcome_partitions = cast(
        dict[str, dict[str, dict[str, list[str]]]],
        baseline_only_report["admission_outcome_partitions"],
    )
    for scenario in ("BASE", "STRESS"):
        partition = outcome_partitions[scenario]
        assert partition["ZONE"] == {"NOT_ADMITTED": baseline_masks["baseline"]}

    baseline_candidate = population.baseline_decisions[candidate_index]
    blocked_baseline = list(population.baseline_decisions)
    blocked_baseline[candidate_index] = _decision_with_evaluation(
        population,
        baseline_candidate,
        baseline_candidate.strategy_evaluation,
        equity=Decimal("5000"),
        day_start=Decimal("10000"),
        peak=Decimal("10000"),
    )
    zone_only = _population_with_decisions(population, baseline=tuple(blocked_baseline))
    zone_only_report = task_report(
        zone_only,
        _results_for_population(store, population, zone_only, results),
        expected_task=configuration.task,
        run=run,
    )
    zone_candidates = cast(dict[str, Any], zone_only_report["candidates"])
    zone_admissions = cast(dict[str, Any], zone_only_report["admissions"])
    zone_masks = cast(dict[str, Any], zone_only_report["masks"])
    assert zone_candidates["baseline"] == 1
    assert zone_candidates["zone"] == 1
    assert zone_admissions["baseline"] == 0
    assert zone_admissions["zone"] == 1
    assert zone_admissions["zone_only"] == 1
    assert zone_masks["baseline"] == []
    assert zone_masks["zone"]


def test_report_arithmetic_is_independent_of_ambient_decimal_context(
    typed_authorities: tuple[Any, ...],
) -> None:
    _store, _configuration, _grant_value, run, _population, results, directory = typed_authorities
    with localcontext(Context(prec=7)):
        low_metrics = _metrics(results)
        low_projection = final_projection(directory, run=run, tasks=(_configuration.task,))
    with localcontext(Context(prec=71)):
        high_metrics = _metrics(results)
        high_projection = final_projection(directory, run=run, tasks=(_configuration.task,))
    assert low_metrics == high_metrics
    assert low_projection == high_projection
    assert low_projection["paired"]
    assert low_projection["absolute"]
    exact_values = (
        Decimal("0.0000000000000000001234567890123456789"),
        Decimal("999999999999999999.0000000000000000001"),
    )
    with localcontext(Context(prec=7)):
        low_sum = _exact_sum(exact_values)
    with localcontext(Context(prec=71)):
        high_sum = _exact_sum(exact_values)
    assert (
        low_sum == high_sum == Decimal("999999999999999999.0000000000000000002234567890123456789")
    )


def test_grant_rejection_precedes_real_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal called
        del args, kwargs
        called = True

    monkeypatch.setattr(protocol, "_evaluate_population", forbidden)
    bad = replace(
        _grant(_synthetic()[1].input_binding, "not-authorized-for-real"),
        kind="EXTERNAL_REVIEWED_EXECUTION_GRANT",
        executable_sha="2" * 40,
        source_id=ACCEPTED_BINDING.source_id.value,
        dataset_version=ACCEPTED_BINDING.dataset_version,
        manifest_sha256=ACCEPTED_BINDING.manifest_sha256,
    )
    with pytest.raises(HistoryScreenedComparisonError, match="grant"):
        evaluate_granted_task(
            cast(Any, object()),
            cast(Any, object()),
            bad,
            actual_sha="3" * 40,
        )
    assert not called


def test_ledger_refuses_duplicate_or_corrupted_task(tmp_path: Path) -> None:
    _store, configuration = _synthetic()
    grant = _grant(configuration.input_binding, "synthetic-corruption")
    ledger = HistoryScreenedLedgerStore.create(
        tmp_path, grant, actual_sha=_SHA, synthetic_task_ids=(configuration.task.task_id,)
    )
    ledger.start(0)
    with pytest.raises(HistoryScreenedComparisonError, match="rerun"):
        ledger.start(0)
    ledger.fail(0)
    with pytest.raises(HistoryScreenedComparisonError, match="rerun"):
        ledger.start(0)


@pytest.mark.parametrize(
    ("opened", "block", "week"),
    (
        (datetime(2026, 4, 10, tzinfo=UTC), "P1", "W1"),
        (datetime(2026, 6, 9, tzinfo=UTC), "P2", "W9"),
        (datetime(2026, 8, 8, tzinfo=UTC), "P3", "W18"),
        (datetime(2026, 10, 6, 23, 45, tzinfo=UTC), "P3", "W26"),
    ),
)
def test_reporting_calendar_is_open_time_based(opened: datetime, block: str, week: str) -> None:
    assert report_block(opened) == block
    assert report_week(opened) == week


def test_cli_module_load_is_repository_relative_and_self_contained() -> None:
    namespace = runpy.run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "scripts/research/breakout_history_screened_comparison.py"
        )
    )
    assert callable(cast(Callable[..., object], namespace["main"]))


def test_protocol_record_is_canonical_and_has_no_executable_self_reference() -> None:
    record = protocol.protocol_record()
    assert "executable_sha" not in json.dumps(record)
    assert protocol._sha_bytes(protocol._canonical(record)) == PROTOCOL_SHA256
