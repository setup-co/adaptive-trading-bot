from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from adaptive_trading_bot.research import history_screened_196d_corpus as subject
from adaptive_trading_bot.research import research_v2_corpus as core

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def frozen_plan() -> dict[str, Any]:
    return subject.make_plan(REPO, "fixture-revision")


def test_frozen_inventory_and_reuse_are_exact(frozen_plan: dict[str, Any]) -> None:
    assert frozen_plan["panel"] == list(subject.PANEL)
    assert frozen_plan["reused_position_total"] == 234_956
    assert frozen_plan["new_position_total"] == 329_524
    assert len(frozen_plan["requests"]) == 3_433
    assert sum(row["expected_rows"] for row in frozen_plan["requests"]) == 329_524
    counts = {
        symbol: sum(row["query"]["symbol"] == symbol for row in frozen_plan["requests"])
        for symbol in subject.PANEL
    }
    assert counts == {
        "BTCUSDT": 317,
        "ETHUSDT": 316,
        "SOLUSDT": 316,
        "XRPUSDT": 316,
        "ZECUSDT": 316,
        "ZAMAUSDT": 316,
        "BCHUSDT": 316,
        "AVAXUSDT": 316,
        "DOGEUSDT": 316,
        "LINKUSDT": 588,
    }


def test_inventory_excludes_control_and_contains_declared_btc_gap(
    frozen_plan: dict[str, Any],
) -> None:
    queries = [row["query"] for row in frozen_plan["requests"]]
    october_24 = str(core.utc_ms(datetime(2026, 10, 24, tzinfo=UTC)))
    assert all(row["startTime"] != october_24 for row in queries)
    gap_start = str(core.utc_ms(datetime(2026, 7, 20, 16, 40, tzinfo=UTC)))
    gap_end = str(core.utc_ms(datetime(2026, 7, 21, tzinfo=UTC)) - 1)
    assert any(
        row["symbol"] == "BTCUSDT" and row["startTime"] == gap_start and row["endTime"] == gap_end
        for row in queries
    )
    for row in queries:
        subject.validate_query(row)


def _wire(start: int, count: int) -> bytes:
    rows = [
        [
            start + index * subject.STEP_MS,
            "10",
            "12",
            "9",
            "11",
            "2",
            start + (index + 1) * subject.STEP_MS,
            "21",
            3,
            "1",
            "10",
        ]
        for index in range(count)
    ]
    return json.dumps(rows, separators=(",", ":")).encode()


def _capture_plan() -> dict[str, Any]:
    start = core.utc_ms(subject.START)
    requests = [
        {
            "id": f"BTCUSDT-{index:04d}",
            "query": subject.query("BTCUSDT", start, start + subject.STEP_MS),
            "expected_rows": 1,
        }
        for index in range(subject.EXPECTED_REQUESTS)
    ]
    return {
        "schema": subject.SCHEMA,
        "requests": requests,
        "attempt_budget": subject.EXPECTED_REQUESTS * 2,
    }


class _Terminal:
    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        return 403, b"denied"


class _TimeoutThenTerminal:
    calls = 0

    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.URLError(TimeoutError())
        if self.calls == 2:
            return 200, _wire(int(query["startTime"]), 1)
        return 403, b"denied"


def test_terminal_route_stop_is_durable_and_has_zero_retry(tmp_path: Path) -> None:
    plan = _capture_plan()
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    result = subject.capture(tmp_path, plan, _Terminal(), sleep=lambda _: None)
    assert result == {"status": "ROUTE_STOPPED", "new_attempts": 1, "outcome": "DENIED"}
    assert (tmp_path / "route_stop.json").is_file()
    assert core.attempt_state(tmp_path, "BTCUSDT-0000", 1) is None


def test_only_timeout_gets_one_retry_and_then_capture_advances(tmp_path: Path) -> None:
    plan = _capture_plan()
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    result = subject.capture(tmp_path, plan, _TimeoutThenTerminal(), sleep=lambda _: None)
    assert result["new_attempts"] == 3
    first = core.attempt_state(tmp_path, "BTCUSDT-0000", 0)
    retry = core.attempt_state(tmp_path, "BTCUSDT-0000", 1)
    second = core.attempt_state(tmp_path, "BTCUSDT-0001", 0)
    assert first is not None and first["retryable"] is True
    assert retry is not None and retry["outcome"] == "HTTP_OK"
    assert second is not None and second["outcome"] == "DENIED"


def test_started_without_result_is_ambiguous_and_never_transmitted(tmp_path: Path) -> None:
    plan = _capture_plan()
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    stem = tmp_path / "working" / "BTCUSDT-0000" / "0"
    core.durable_new(
        stem.with_suffix(".started.json"),
        core.canonical(
            {
                "request_id": "BTCUSDT-0000",
                "attempt": 0,
                "query": plan["requests"][0]["query"],
                "started_at": "2026-10-27T00:00:00Z",
                "plan_sha256": core.sha((tmp_path / "plan.json").read_bytes()),
            }
        ),
    )
    with pytest.raises(core.CorpusError, match="ambiguous STARTED"):
        subject.capture(tmp_path, plan, _Terminal(), sleep=lambda _: None)


def test_parser_and_gap_aware_derivation_preserve_exact_decimals() -> None:
    start = core.utc_ms(subject.START)
    query = subject.query("LINKUSDT", start, start + 4 * subject.STEP_MS)
    rows = core.parse_rows(_wire(start, 4), query)
    mapping = {
        row.open_time_ms: row for row in rows if row.open_time_ms != start + 2 * subject.STEP_MS
    }
    spans = core.epochs(mapping)
    assert len(spans) == 2
    assert core.derive(mapping, spans, 3) == []
    assert rows[0].quote_volume == Decimal("21")


def test_warmup_is_epoch_local_and_excludes_boundary() -> None:
    start = core.utc_ms(subject.START)
    four_hour = [
        core.Row(
            start + index * 14_400_000,
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
            Decimal("0"),
            start + (index + 1) * 14_400_000,
            Decimal("0"),
            0,
            Decimal("0"),
            Decimal("0"),
        )
        for index in range(50)
    ]
    boundary = four_hour[-1].close_time_ms
    fifteen = [
        core.Row(
            boundary,
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
            Decimal("0"),
            boundary + 900_000,
            Decimal("0"),
            0,
            Decimal("0"),
            Decimal("0"),
        )
    ]
    result = subject._warmup({"4h": four_hour, "15m": fifteen}, [(start, boundary + 900_000)])
    assert result == [
        {
            "start_ms": boundary,
            "end_ms": boundary + 900_000,
            "complete_4h_warmup": 50,
            "boundary_observation_excluded": True,
        }
    ]
