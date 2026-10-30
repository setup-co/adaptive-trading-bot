"""Synthetic proof for ISSUE220_BOUNDED_MEMORY_RECOVERY_V1."""

from __future__ import annotations

import gzip
import importlib.util
import json
import runpy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research import breakout_history_screened_recovery as recovery
from adaptive_trading_bot.research.all_fill_replay import AllFillStatus
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    TASKS,
    ComparisonArm,
    ComparisonScenario,
    HistoryScreenedExecutionGrant,
    _canonical,
)
from adaptive_trading_bot.research.breakout_history_screened_execution import (
    HistoryScreenedRunAssociation,
    _metrics,
    execute_task,
    final_projection,
    read_results,
)

_SOURCE = runpy.run_path(
    str(Path(__file__).with_name("test_breakout_history_screened_comparison.py"))
)
_synthetic = cast(Any, _SOURCE["_synthetic"])
_grant = cast(Any, _SOURCE["_grant"])
_SHA = "1" * 40


@pytest.fixture(scope="module")
def retained_task(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[
    Path,
    object,
    HistoryScreenedExecutionGrant,
    HistoryScreenedRunAssociation,
]:
    store, configuration = _synthetic()
    grant = cast(HistoryScreenedExecutionGrant, _grant(configuration.input_binding, "recovery"))
    run = HistoryScreenedRunAssociation.from_grant(
        grant, configuration.input_binding, actual_sha=_SHA
    )
    directory = tmp_path_factory.mktemp("bounded-recovery-authority")
    execute_task(
        store,
        configuration,
        grant=grant,
        actual_sha=_SHA,
        directory=directory,
        task_index=0,
        synthetic=True,
    )
    return directory, configuration, grant, run


def _tracker() -> recovery.ResourceTracker:
    tracker = recovery.ResourceTracker(budget_bytes=2 * 1024**3)
    tracker.start()
    return tracker


def test_streamed_typed_task_and_projection_equal_frozen_reporting(
    retained_task: tuple[
        Path, object, HistoryScreenedExecutionGrant, HistoryScreenedRunAssociation
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, raw_configuration, _grant_value, run = retained_task
    configuration = cast(Any, raw_configuration)

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("recovery invoked an execution authority")

    monkeypatch.setattr(
        "adaptive_trading_bot.research.breakout_history_screened_comparison.evaluate_granted_task",
        forbidden,
    )
    monkeypatch.setattr(
        "adaptive_trading_bot.research.breakout_history_screened_execution.execute_task",
        forbidden,
    )
    tracker = _tracker()
    try:
        compact = recovery._read_population(
            directory / "00.population.json.gz",
            configuration.task,
            tracker,
            expected_binding=configuration.input_binding,
        )
        panel = recovery._PanelReducer()
        panel.register_population(compact)
        report = recovery._task_report_from_stream(
            directory,
            configuration.task,
            compact,
            run,
            panel,
            tracker,
        )
        recovered = recovery._projection_bytes(
            panel.project(run, tasks=(configuration.task,)),
            (report,),
        )
    finally:
        tracker.stop()
    expected = _canonical(final_projection(directory, run=run, tasks=(configuration.task,)))
    assert recovered == expected
    assert report == (directory / "00.report.json").read_bytes()


def test_streamed_metrics_match_frozen_metrics(
    retained_task: tuple[
        Path, object, HistoryScreenedExecutionGrant, HistoryScreenedRunAssociation
    ],
) -> None:
    directory, _configuration, _grant_value, _run = retained_task
    results = read_results((directory / "00.results.jsonl.gz").read_bytes())
    for arm in ComparisonArm:
        for scenario in ComparisonScenario:
            selected = tuple(
                item for item in results if item.arm is arm and item.scenario is scenario
            )
            reducer = recovery._MetricsReducer()
            for result in selected:
                reducer.add(result)
            assert reducer.project() == _metrics(selected)


def test_multi_member_pure_screen_reducer_has_explicit_asset_and_week_dependence() -> None:
    panel = recovery._PanelReducer()
    for arm in ComparisonArm:
        for scenario in ComparisonScenario:
            rows = panel.screen[arm, scenario, False]
            masked = panel.screen[arm, scenario, True]
            for asset_index in range(5):
                for week_index in range(6):
                    row = recovery._ScreenRow(
                        AllFillStatus.ORDINARY_CLOSED,
                        Decimal("0.25"),
                        f"A{asset_index}USDT",
                        f"W{week_index + 1}",
                    )
                    rows.extend((row, row, row))
                    masked.extend((row, row, row))
    assert panel._classification(ComparisonArm.BASELINE) == "CANDIDATE_FOR_FURTHER_VALIDATION"
    panel.screen[ComparisonArm.BASELINE, ComparisonScenario.STRESS, False][0] = recovery._ScreenRow(
        AllFillStatus.RECOVERY_OPEN,
        None,
        "A0USDT",
        "W1",
    )
    assert panel._classification(ComparisonArm.BASELINE) == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("payload", [b"version https://git-lfs.github.com/spec/v1\n", b"not-gzip"])
def test_population_rejects_pointer_or_foreign_payload(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "population.json.gz"
    path.write_bytes(payload)
    tracker = _tracker()
    try:
        with pytest.raises(recovery.BoundedRecoveryError, match="not gzip"):
            recovery._read_population(path, TASKS[0], tracker)
    finally:
        tracker.stop()


def test_population_and_results_reject_truncation(
    retained_task: tuple[
        Path, object, HistoryScreenedExecutionGrant, HistoryScreenedRunAssociation
    ],
    tmp_path: Path,
) -> None:
    directory, raw_configuration, _grant_value, run = retained_task
    configuration = cast(Any, raw_configuration)
    broken_population = tmp_path / "population.json.gz"
    payload = (directory / "00.population.json.gz").read_bytes()
    broken_population.write_bytes(payload[: len(payload) // 2])
    tracker = _tracker()
    try:
        with pytest.raises(recovery.BoundedRecoveryError):
            recovery._read_population(
                broken_population,
                configuration.task,
                tracker,
                expected_binding=configuration.input_binding,
            )
    finally:
        tracker.stop()

    copied = tmp_path / "task"
    copied.mkdir()
    for suffix in ("population.json.gz", "report.json"):
        (copied / f"00.{suffix}").write_bytes((directory / f"00.{suffix}").read_bytes())
    results = (directory / "00.results.jsonl.gz").read_bytes()
    (copied / "00.results.jsonl.gz").write_bytes(results[:-17])
    tracker = _tracker()
    try:
        compact = recovery._read_population(
            copied / "00.population.json.gz",
            configuration.task,
            tracker,
            expected_binding=configuration.input_binding,
        )
        with pytest.raises(recovery.BoundedRecoveryError):
            recovery._task_report_from_stream(
                copied,
                configuration.task,
                compact,
                run,
                recovery._PanelReducer(),
                tracker,
            )
    finally:
        tracker.stop()


def test_json_value_reader_does_not_retain_generated_scale_payload(tmp_path: Path) -> None:
    path = tmp_path / "scale.json.gz"
    item = ["str", "x" * 4096]
    count = 12_000
    with gzip.open(path, "wb") as handle:
        handle.write(b'[["tuple",[')
        encoded = _canonical(item)
        for index in range(count):
            if index:
                handle.write(b",")
            handle.write(encoded)
        handle.write(b"]]]")
    tracker = _tracker()
    try:
        with gzip.open(path, "rb") as handle:
            stream = recovery._JsonStream(cast(Any, handle), chunk_size=64 * 1024)
            stream.expect(b"[")
            observed = sum(1 for _ in recovery._tagged_tuple(stream))
            stream.expect(b"]")
            stream.finish()
            tracker.sample()
    finally:
        tracker.stop()
    assert observed == count
    assert tracker.peak_tracemalloc_bytes < count * len(encoded)


def test_task_report_detects_saved_authority_mismatch(
    retained_task: tuple[
        Path, object, HistoryScreenedExecutionGrant, HistoryScreenedRunAssociation
    ],
    tmp_path: Path,
) -> None:
    directory, raw_configuration, _grant_value, run = retained_task
    configuration = cast(Any, raw_configuration)
    copied = tmp_path / "task"
    copied.mkdir()
    for source in directory.iterdir():
        (copied / source.name).write_bytes(source.read_bytes())
    report = json.loads((copied / "00.report.json").read_bytes())
    report["economic_classification"] = "fabricated"
    (copied / "00.report.json").write_bytes(_canonical(report))
    tracker = _tracker()
    try:
        compact = recovery._read_population(
            copied / "00.population.json.gz",
            configuration.task,
            tracker,
            expected_binding=configuration.input_binding,
        )
        with pytest.raises(recovery.BoundedRecoveryError, match="differs"):
            recovery._task_report_from_stream(
                copied,
                configuration.task,
                compact,
                run,
                recovery._PanelReducer(),
                tracker,
            )
    finally:
        tracker.stop()


def test_compact_decisions_preserve_distinct_symbol_identity(
    retained_task: tuple[
        Path, object, HistoryScreenedExecutionGrant, HistoryScreenedRunAssociation
    ],
) -> None:
    directory, raw_configuration, _grant_value, _run = retained_task
    configuration = cast(Any, raw_configuration)
    tracker = _tracker()
    try:
        compact = recovery._read_population(
            directory / "00.population.json.gz",
            configuration.task,
            tracker,
            expected_binding=configuration.input_binding,
        )
    finally:
        tracker.stop()
    changed = replace(
        compact.baseline[0].identity,
        symbol=TradingSymbol("ETH", "USDT"),
    )
    assert changed != compact.baseline[0].identity
    assert changed.symbol == TradingSymbol("ETH", "USDT")


def test_operator_cli_loads_by_repository_path_and_reports_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = Path("scripts/research/breakout_history_screened_recovery.py").resolve()
    spec = importlib.util.spec_from_file_location("issue_222_recovery_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["identity"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["protocol"] == recovery.RECOVERY_PROTOCOL
    assert result["run_uuid"] == recovery.ORIGINAL_RUN_UUID
