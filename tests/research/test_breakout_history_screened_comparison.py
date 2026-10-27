"""Synthetic acceptance for BREAKOUT_RETEST_HISTORY_SCREENED_180D_COMPARISON_V1."""

from __future__ import annotations

import json
import runpy
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.research import breakout_history_screened_comparison as protocol
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    ACCEPTED_BINDING,
    PANEL,
    PROTOCOL_SHA256,
    STATUS_CELLS,
    TASK_PLAN_SHA256,
    TASKS,
    HistoryScreenedComparisonError,
    HistoryScreenedConfiguration,
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
    execute_task,
    final_projection,
    read_population,
    read_results,
    verify_task,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import (
    HistoryScreenedLedgerStore,
)
from adaptive_trading_bot.storage import IntegrityReport, MergeResult, SourceId

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


def _grant(run_uuid: str = "synthetic-history-screened-run") -> HistoryScreenedExecutionGrant:
    return HistoryScreenedExecutionGrant(
        "SYNTHETIC_TEST_GRANT",
        run_uuid,
        _SHA,
        PROTOCOL_SHA256,
        TASK_PLAN_SHA256,
        protocol.SYNTHETIC_SOURCE_ID.value,
        "synthetic-history-screened-180d-v1",
        "a" * 64,
    )


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
    grant = _grant()
    ledger = HistoryScreenedLedgerStore.create(
        tmp_path, grant, actual_sha=_SHA, synthetic_task_ids=(configuration.task.task_id,)
    )
    ledger.start(0)
    artifacts = execute_task(
        store,
        configuration,
        grant=None,
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
    checked = verify_task(ledger.directory, 0)
    assert checked["task_id"] == configuration.task.task_id
    population = read_population((ledger.directory / "00.population.json.gz").read_bytes())
    results = read_results((ledger.directory / "00.results.jsonl.gz").read_bytes())
    assert len(results) == len(population.baseline_decisions) * 4
    assert {result.arm.value for result in results} == {"BASELINE", "ZONE"}
    assert {result.scenario.value for result in results} == {"BASE", "STRESS"}
    assert any(result.branches for result in results)
    assert all(result.end == result.start + timedelta(hours=168) for result in results)
    projection = final_projection(ledger.directory, task_count=1)
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


def test_grant_rejection_precedes_real_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def forbidden(*args: object, **kwargs: object) -> None:
        nonlocal called
        del args, kwargs
        called = True

    monkeypatch.setattr(protocol, "_evaluate_population", forbidden)
    bad = replace(
        _grant("not-authorized-for-real"),
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
    grant = _grant("synthetic-corruption")
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
