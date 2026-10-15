"""Offline C2 tail authorization, bounded resume and raw projection tests."""

from __future__ import annotations

import copy
import subprocess
import sys
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_tail as tail

REPO = Path(__file__).resolve().parents[2]


class FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.queries: list[dict[str, str]] = []

    def get(self, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        assert timeout == 20
        self.queries.append(query)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, tuple)
        return outcome


@pytest.fixture(scope="module")
def frozen_plan() -> dict[str, Any]:
    return tail.make_plan(REPO, "0" * 40)


def _root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "c2"
    plan = copy.deepcopy(frozen_plan)
    plan["output_root"] = root.as_posix()
    monkeypatch.setattr(tail, "ROOT", root)
    monkeypatch.setattr(tail, "make_plan", lambda repo, revision: copy.deepcopy(plan))
    c.durable_new(root / "plan.json", c.canonical(plan))
    return root, plan


def _record(
    root: Path,
    plan: dict[str, Any],
    entry: dict[str, Any],
    *,
    status: int,
    started_at: str = "2026-10-15T00:00:00Z",
    completed_at: str = "2026-10-15T00:00:01Z",
) -> None:
    request_id = entry["id"]
    stem = root / "working" / request_id / "0"
    c.durable_new(
        stem.with_suffix(".started.json"),
        c.canonical(
            {
                "request_id": request_id,
                "attempt": 0,
                "query": entry["query"],
                "started_at": started_at,
                "plan_sha256": c.sha((root / "plan.json").read_bytes()),
            }
        ),
    )
    body = b"[]"
    c.durable_new(stem.with_suffix(".raw"), body)
    c.durable_new(
        stem.with_suffix(".result.json"),
        c.canonical(
            {
                "request_id": request_id,
                "attempt": 0,
                "completed_at": completed_at,
                "status": status,
                "outcome": "HTTP_OK" if status == 200 else "DENIED",
                "retryable": False,
                "raw_sha256": c.sha(body),
                "raw_bytes": len(body),
            }
        ),
    )


def test_real_archive_backed_predecessors_freeze_exact_tail(frozen_plan: dict[str, Any]) -> None:
    assert len(frozen_plan["requests"]) == 785
    assert frozen_plan["requests"][0]["id"] == "BTWUSDT-09-1"
    assert frozen_plan["requests"][-1]["id"] == "DOGEUSDT-earlier-daily"
    assert Counter(entry["query"]["symbol"] for entry in frozen_plan["requests"]) == {
        "BTWUSDT": 243,
        "AVAXUSDT": 271,
        "DOGEUSDT": 271,
    }
    assert sum(entry["query"]["interval"] == "5m" for entry in frozen_plan["requests"]) == 782
    assert Counter(
        entry["query"]["symbol"]
        for entry in frozen_plan["requests"]
        if entry["query"]["interval"] == "5m"
    ) == {"BTWUSDT": 242, "AVAXUSDT": 270, "DOGEUSDT": 270}
    assert Counter(
        entry["query"]["symbol"]
        for entry in frozen_plan["requests"]
        if entry["query"]["interval"] == "1d"
    ) == {"BTWUSDT": 1, "AVAXUSDT": 1, "DOGEUSDT": 1}
    assert tail.FAILED_IDS.isdisjoint(entry["id"] for entry in frozen_plan["requests"])
    assert "BTCUSDT-00-0" not in {entry["id"] for entry in frozen_plan["requests"]}
    assert frozen_plan["predecessor_attempts"] == 1919
    assert frozen_plan["attempt_budget"] == 1570
    assert frozen_plan["cumulative_attempt_budget"] == 3489


def test_working_resume_skips_success_and_stops_on_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    _record(root, plan, plan["requests"][0], status=200)
    transport = FakeTransport([(403, b"denied")])
    assert tail.capture(REPO, root, plan, transport, sleep=lambda _: None) == {
        "status": "DENIED",
        "new_attempts": 1,
    }
    assert transport.queries == [plan["requests"][1]["query"]]
    second = FakeTransport([])
    assert tail.capture(REPO, root, plan, second) == {
        "status": "ROUTE_STOPPED",
        "new_attempts": 0,
    }
    assert second.queries == []


@pytest.mark.parametrize("first", [(503, b"error"), TimeoutError("timed out")])
def test_only_timeout_or_5xx_get_one_exact_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any], first: object
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    transport = FakeTransport([first, (200, b"[]"), (403, b"denied")])
    result = tail.capture(REPO, root, plan, transport, sleep=lambda _: None)
    assert result == {"status": "DENIED", "new_attempts": 3}
    assert transport.queries == [
        plan["requests"][0]["query"],
        plan["requests"][0]["query"],
        plan["requests"][1]["query"],
    ]
    assert c.audit_ledger(root, plan)["retries"] == 1


def test_dns_stops_without_retry_or_next_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    transport = FakeTransport([urllib.error.URLError(OSError("dns unavailable"))])
    assert tail.capture(REPO, root, plan, transport, sleep=lambda _: None) == {
        "status": "NETWORK_UNAVAILABLE",
        "new_attempts": 1,
    }
    assert transport.queries == [plan["requests"][0]["query"]]
    assert c.audit_ledger(root, plan)["attempts"] == 1


@pytest.mark.parametrize("sidecar", ["missing", "wrong"])
def test_stopped_working_ledger_requires_exact_sidecar_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any], sidecar: str
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    _record(root, plan, plan["requests"][0], status=403)
    if sidecar == "wrong":
        c.durable_new(
            root / "route_stop.json",
            c.canonical({"reason": "DENIED", "last_request_id": "DOGEUSDT-earlier-daily"}),
        )
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match="route-stop sidecar"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []


def test_archive_only_layout_is_capture_closed_without_working_or_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    (root / "archives").mkdir()
    (root / "archives" / "BTWUSDT.zip").write_bytes(b"archived")
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match="capture-closed"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []


def test_archive_backed_stop_requires_its_sidecar_and_rejects_later_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    _record(root, plan, plan["requests"][0], status=403)
    c.durable_new(
        root / "route_stop.json",
        c.canonical({"reason": "DENIED", "last_request_id": plan["requests"][0]["id"]}),
    )
    tail.build(REPO, root, plan)
    assert tail.verify(REPO, root)["ledger"]["attempts"] == 1
    (root / "route_stop.json").unlink()
    with pytest.raises(c.CorpusError, match="route-stop sidecar"):
        tail.verify(REPO, root)
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match="capture-closed"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []

    # Restore the exact stop, then assert a recorded transmission after it
    # invalidates the archived lineage rather than becoming usable evidence.
    c.durable_new(
        root / "route_stop.json",
        c.canonical({"reason": "DENIED", "last_request_id": plan["requests"][0]["id"]}),
    )
    _record(
        root,
        plan,
        plan["requests"][1],
        status=200,
        started_at="2026-10-15T00:00:02Z",
        completed_at="2026-10-15T00:00:03Z",
    )
    with pytest.raises(c.CorpusError, match="working attempt missing from frozen archive"):
        tail.verify(REPO, root)


def test_completed_denial_cannot_be_followed_by_another_working_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    _record(root, plan, plan["requests"][0], status=403)
    _record(
        root,
        plan,
        plan["requests"][1],
        status=200,
        started_at="2026-10-15T00:00:02Z",
        completed_at="2026-10-15T00:00:03Z",
    )
    c.durable_new(
        root / "route_stop.json",
        c.canonical({"reason": "DENIED", "last_request_id": plan["requests"][0]["id"]}),
    )
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match=r"after|stop|sidecar"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []


def test_ambiguous_started_attempt_blocks_resume_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    entry = plan["requests"][0]
    c.durable_new(
        root / "working" / entry["id"] / "0.started.json",
        c.canonical(
            {
                "request_id": entry["id"],
                "attempt": 0,
                "query": entry["query"],
                "started_at": "2026-10-15T00:00:00Z",
                "plan_sha256": c.sha((root / "plan.json").read_bytes()),
            }
        ),
    )
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match=r"ambiguous|missing|started"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []


def test_cumulative_budget_stops_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    monkeypatch.setattr(c, "audit_ledger", lambda root, plan: {"attempts": 1571, "states": {}})
    transport = FakeTransport([])
    with pytest.raises(c.CorpusError, match="cumulative attempt budget"):
        tail.capture(REPO, root, plan, transport)
    assert transport.queries == []


def test_compact_empty_c2_projection_and_rehashed_false_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    built = tail.build(REPO, root, plan)
    assert built["status"] == "PARTIAL_OR_BLOCKED"
    checked = tail.verify(REPO, root)
    assert checked["manifest_sha256"] == built["manifest_sha256"]
    assert checked["cumulative"] == {"distinct": 1919, "attempts": 1919, "retries": 0}
    assert checked["report"]["symbols"]["BTCUSDT"]["valid_5m_rows"] == 25832
    assert checked["report"]["symbols"]["BTWUSDT"]["valid_5m_rows"] == 2592
    assert checked["report"]["ten_member_usable_overlap"] == []
    reader = tail.LockedResearchV2TailReader(
        REPO, root, dataset_version=tail.VERSION, manifest_sha256=built["manifest_sha256"]
    )
    anchor = c.utc_ms(c.END - c.DAY)
    assert reader.read("ETHUSDT", "5m", anchor, anchor + 300_000).num_rows == 1
    with pytest.raises(c.CorpusError, match="cross-gap"):
        reader.read("BTCUSDT", "5m", c.utc_ms(c.START), anchor + 300_000)
    report = c.read_json(root / "combined" / "report.json")
    report["symbols"]["BTCUSDT"]["valid_5m_rows"] += 1
    (root / "combined" / "report.json").write_bytes(c.canonical(report))
    manifest = c.read_json(root / "manifest.json")
    manifest["files"]["combined/report.json"] = c.sha(
        (root / "combined" / "report.json").read_bytes()
    )
    (root / "manifest.json").write_bytes(c.canonical(manifest))
    with pytest.raises(c.CorpusError, match="report differs from raw"):
        tail.verify(REPO, root)


def test_tail_verifier_works_outside_repo_without_git_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_plan: dict[str, Any]
) -> None:
    root, plan = _root(tmp_path, monkeypatch, frozen_plan)
    tail.build(REPO, root, plan)
    code = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(REPO / 'src')!r}); "
        "from adaptive_trading_bot.research import research_v2_corpus_tail as t; "
        f"t.ROOT=Path({str(root)!r}); "
        f"v=t.verify(Path({str(REPO)!r}),Path({str(root)!r})); "
        "assert v['cumulative']['attempts']==1919"
    )
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=180)
