from __future__ import annotations

import json
import socket
import sys
import urllib.error
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any

import pytest
from scripts.research import history_screened_196d_continuation_wrapper as wrapper

from adaptive_trading_bot.research import history_screened_196d_continuation as subject
from adaptive_trading_bot.research import history_screened_196d_corpus as parent
from adaptive_trading_bot.research import research_v2_corpus as core

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def frozen_plan() -> dict[str, Any]:
    return subject.make_plan(REPO, "fixture-revision")


def _wire(start: int, count: int, *, close: str = "11.0") -> bytes:
    rows = [
        [
            start + index * parent.STEP_MS,
            "10.00",
            "12.000",
            "9.0",
            close,
            "2.00",
            start + (index + 1) * parent.STEP_MS,
            "21.000",
            3,
            "1.0",
            "10.00",
        ]
        for index in range(count)
    ]
    return json.dumps(rows, separators=(",", ":")).encode()


def _entry(request_id: str, start: int, count: int) -> dict[str, Any]:
    return {
        "id": request_id,
        "query": parent.query("ZAMAUSDT", start, start + count * parent.STEP_MS),
        "expected_rows": count,
        "parent_status": "NOT_ATTEMPTED",
    }


def _plan(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": subject.SCHEMA,
        "protocol": subject.PROTOCOL,
        "requests": entries,
        "attempt_budget": len(entries) * 2,
        "parent": {"fixture": True},
    }


class _Replies:
    def __init__(self, replies: list[tuple[int, bytes] | BaseException]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        del query, timeout
        reply = self.replies[self.calls]
        self.calls += 1
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_frozen_successor_inventory_reuses_only_parent_successes(
    frozen_plan: dict[str, Any],
) -> None:
    assert len(frozen_plan["requests"]) == 1_785
    assert frozen_plan["expected_positions"] == 171_336
    assert frozen_plan["parent_reused_positions"] == 234_956
    assert frozen_plan["parent_captured_positions"] == 158_188
    assert frozen_plan["parent_available_positions"] == 393_144
    assert frozen_plan["requests"][0]["id"] == "ZAMAUSDT-0068"
    assert frozen_plan["requests"][-1]["id"] == "LINKUSDT-0588"
    assert {
        symbol: sum(row["query"]["symbol"] == symbol for row in frozen_plan["requests"])
        for symbol in subject.EXPECTED_SYMBOL_REQUESTS
    } == subject.EXPECTED_SYMBOL_REQUESTS
    assert all(row["id"] != "BTCUSDT-0001" for row in frozen_plan["requests"])


def test_valid_partial_and_empty_responses_advance_without_retry(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    plan = _plan([_entry("ZAMAUSDT-0000", start, 2), _entry("ZAMAUSDT-0001", start, 2)])
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    transport = _Replies([(200, _wire(start, 1)), (200, b"[]")])

    outcome = subject.capture_requests(
        tmp_path, plan, transport, sleep=lambda _: None, monotonic=lambda: 10.0
    )

    assert outcome == {"status": "CAPTURE_COMPLETE", "new_attempts": 2}
    assert transport.calls == 2
    first = core.read_json(tmp_path / "working/ZAMAUSDT-0000/0.result.json")
    second = core.read_json(tmp_path / "working/ZAMAUSDT-0001/0.result.json")
    assert first["validation_status"] == "PARTIAL"
    assert first["missing_open_times_ms"] == [start + parent.STEP_MS]
    assert second["validation_status"] == "EMPTY"
    assert not (tmp_path / "working/ZAMAUSDT-0000/1.started.json").exists()


def test_dns_gets_one_delayed_retry_and_exhaustion_stops(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    plan = _plan([_entry("ZAMAUSDT-0000", start, 1)])
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    sleeps: list[float] = []
    dns = urllib.error.URLError(socket.gaierror(11001, "missing"))

    outcome = subject.capture_requests(
        tmp_path,
        plan,
        _Replies([dns, dns]),
        sleep=sleeps.append,
        monotonic=lambda: 10.0,
    )

    assert outcome["outcome"] == "DNS_RETRY_EXHAUSTED"
    assert outcome["new_attempts"] == 2
    assert 5.0 in sleeps


def test_resume_reconstructs_minimum_delay_and_terminal_streak(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    entries = [
        _entry(f"ZAMAUSDT-{index:04d}", start + index * parent.STEP_MS, 1) for index in range(3)
    ]
    plan = _plan(entries)
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    plan_sha = core.sha((tmp_path / "plan.json").read_bytes())
    subject._attempt(tmp_path, plan_sha, entries[0], 0, _Replies([(500, b"server")]))
    subject._attempt(tmp_path, plan_sha, entries[0], 1, _Replies([(500, b"server")]))
    last_completed = core.read_json(tmp_path / "working/ZAMAUSDT-0000/1.result.json")[
        "completed_at"
    ]
    waits: list[float] = []

    outcome = subject.capture_requests(
        tmp_path,
        plan,
        _Replies([(500, b"server")] * 4),
        sleep=waits.append,
        monotonic=lambda: 10.0,
        wall_clock=lambda: core._utc_event(last_completed),
    )

    assert waits and waits[0] == 2.0
    assert outcome["outcome"] == "THREE_CONSECUTIVE_TERMINAL_FAILURES"


def test_ambiguous_started_attempt_and_finalized_root_fail_closed(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    plan = _plan([_entry("ZAMAUSDT-0000", start, 1)])
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    stem = tmp_path / "working/ZAMAUSDT-0000/0"
    core.durable_new(
        stem.with_suffix(".started.json"),
        core.canonical(
            {
                "request_id": "ZAMAUSDT-0000",
                "attempt": 0,
                "query": plan["requests"][0]["query"],
                "started_at": "2026-10-27T00:00:00Z",
                "plan_sha256": core.sha((tmp_path / "plan.json").read_bytes()),
            }
        ),
    )
    with pytest.raises(core.CorpusError, match="ambiguous successor"):
        subject.capture_requests(tmp_path, plan, _Replies([]))

    other = tmp_path / "closed"
    core.durable_new(other / "plan.json", core.canonical(plan))
    core.durable_new(other / "capture_complete.json", b"{}")
    with pytest.raises(core.CorpusError, match="stopped or finalized"):
        subject.capture_requests(other, plan, _Replies([]))


def test_raw_and_query_tampering_are_rejected(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    plan = _plan([_entry("ZAMAUSDT-0000", start, 1)])
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    subject.capture_requests(
        tmp_path,
        plan,
        _Replies([(200, _wire(start, 1))]),
        sleep=lambda _: None,
        monotonic=lambda: 10.0,
    )
    raw = tmp_path / "working/ZAMAUSDT-0000/0.raw"
    raw.write_bytes(b"[]")
    with pytest.raises(core.CorpusError, match="raw hash/size"):
        subject.audit_ledger(tmp_path, plan)

    raw.write_bytes(_wire(start, 1))
    started = core.read_json(tmp_path / "working/ZAMAUSDT-0000/0.started.json")
    started["query"] = {**started["query"], "symbol": "BCHUSDT"}
    (tmp_path / "working/ZAMAUSDT-0000/0.started.json").write_bytes(core.canonical(started))
    with pytest.raises(core.CorpusError, match="start differs"):
        subject.audit_ledger(tmp_path, plan)


def test_decimal_deltas_are_signed_exact_and_context_independent() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 3
        left = Decimal("12345678901234567890.00000000000000000001")
        right = Decimal("12345678901234567890.00000000000000000002")
        assert subject._exact_decimal_delta(left, right) == Decimal("-0.00000000000000000001")
        assert subject._exact_decimal_delta(right, left) == Decimal("0.00000000000000000001")
        assert subject._exact_decimal_delta(Decimal("1.0"), Decimal("1.000")) == 0
    finally:
        getcontext().prec = original


def test_partial_build_and_verify_share_production_artifact_path(tmp_path: Path) -> None:
    start = core.utc_ms(parent.START)
    plan = _plan([_entry("ZAMAUSDT-0000", start + parent.STEP_MS, 2)])
    core.durable_new(tmp_path / "plan.json", core.canonical(plan))
    subject.capture_requests(
        tmp_path,
        plan,
        _Replies([(200, _wire(start + parent.STEP_MS, 1))]),
        sleep=lambda _: None,
        monotonic=lambda: 10.0,
    )
    snapshot: dict[str, Any] = {
        "reused": {symbol: {} for symbol in parent.PANEL},
        "parent_successful": {
            symbol: (
                {
                    start: core.parse_rows(
                        _wire(start, 1),
                        parent.query(symbol, start, start + parent.STEP_MS),
                    )[0]
                }
                if symbol == "ZAMAUSDT"
                else {}
            )
            for symbol in parent.PANEL
        },
        "daily": {symbol: {} for symbol in parent.PANEL},
    }

    built = subject._build_from_snapshot(tmp_path, plan, snapshot)
    verified = subject._verify_from_snapshot(tmp_path, plan, snapshot)

    assert built["status"] == "PARTIAL_OR_BLOCKED"
    assert built["capture_status"] == "CAPTURE_COMPLETE"
    assert verified["manifest_sha256"] == built["manifest_sha256"]
    report = core.read_json(tmp_path / "report.json")
    assert report["symbols"]["ZAMAUSDT"]["valid_response_gaps"][0]["missing_open_times_ms"] == [
        start + 2 * parent.STEP_MS
    ]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_wrapper_records_exact_numeric_exit_and_refuses_reuse(
    tmp_path: Path, exit_code: int
) -> None:
    log_root = tmp_path / str(exit_code)
    actual = wrapper.run_child_once(
        log_root,
        [sys.executable, "-c", f"raise SystemExit({exit_code})"],
        {"fixture": True},
    )
    assert actual == exit_code
    assert core.read_json(log_root / "exit.json")["exit_code"] == exit_code
    with pytest.raises(core.CorpusError, match="existing/ambiguous"):
        wrapper.run_child_once(log_root, [sys.executable, "-c", "pass"], {"fixture": True})
