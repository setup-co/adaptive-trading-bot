"""Synthetic C1 continuation and actual locked-generation offline verification."""

from __future__ import annotations

import http.client
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from adaptive_trading_bot.research import research_v2_corpus as c
from adaptive_trading_bot.research import research_v2_corpus_continuation as x

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


def _plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "separate-continuation"
    monkeypatch.setattr(x, "ROOT", root)
    plan = x.make_plan(REPO, "0" * 40)
    c.durable_new(root / "plan.json", c.canonical(plan))
    x.validate_plan(REPO, root, plan)
    return root, plan


def test_exact_pending_inventory_and_frozen_original_lineage() -> None:
    plan = x.make_plan(REPO, "0" * 40)
    assert len(plan["requests"]) == 2689
    assert plan["requests"][0]["id"] == "BTCUSDT-05-0"
    assert x.FAILED_ORIGINAL_ID not in {entry["id"] for entry in plan["requests"]}
    assert plan["original_plan_sha256"] == c.ORIGINAL_PLAN_SHA
    assert plan["original_manifest_sha256"] == x.ORIGINAL_MANIFEST_SHA
    assert plan["original_attempts"] == 15
    assert plan["cumulative_attempt_budget"] == 5393
    assert plan["source_sha256"][x.SOURCE.as_posix()] != c.ORIGINAL_SOURCE_SHA


def test_successful_resume_skips_successful_shape_and_stops_on_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan = _plan(tmp_path, monkeypatch)
    first = plan["requests"][0]
    stem = root / "working" / first["id"] / "0"
    c.durable_new(
        stem.with_suffix(".started.json"),
        c.canonical(
            {
                "request_id": first["id"],
                "attempt": 0,
                "query": first["query"],
                "started_at": "2026-10-14T00:00:00Z",
                "plan_sha256": c.sha((root / "plan.json").read_bytes()),
            }
        ),
    )
    c.durable_new(stem.with_suffix(".raw"), b"[]")
    c.durable_new(
        stem.with_suffix(".result.json"),
        c.canonical(
            {
                "request_id": first["id"],
                "attempt": 0,
                "completed_at": "2026-10-14T00:00:01Z",
                "status": 200,
                "outcome": "HTTP_OK",
                "retryable": False,
                "raw_sha256": c.sha(b"[]"),
                "raw_bytes": 2,
            }
        ),
    )
    transport = FakeTransport([(403, b"blocked")])
    result = x.capture(REPO, root, plan, transport, sleep=lambda _: None)
    assert result == {"status": "DENIED", "new_attempts": 1}
    assert transport.queries == [plan["requests"][1]["query"]]
    assert (root / "route_stop.json").exists()
    assert x.capture(REPO, root, plan, FakeTransport([])) == {
        "status": "ROUTE_STOPPED",
        "new_attempts": 0,
    }


def test_three_isolated_remote_disconnects_are_bounded_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan = _plan(tmp_path, monkeypatch)
    transport = FakeTransport([http.client.RemoteDisconnected("closed")] * 3)
    assert x.capture(REPO, root, plan, transport, sleep=lambda _: None) == {
        "status": "ROUTE_STOPPED",
        "new_attempts": 3,
    }
    assert transport.queries == [entry["query"] for entry in plan["requests"][:3]]
    audit = c.audit_ledger(root, plan)
    assert (audit["distinct"], audit["attempts"], audit["retries"]) == (3, 3, 0)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 400])
def test_policy_and_denials_stop_without_second_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    root, plan = _plan(tmp_path, monkeypatch)
    transport = FakeTransport([(status, b"untrusted response")])
    result = x.capture(REPO, root, plan, transport, sleep=lambda _: None)
    assert result["new_attempts"] == 1
    assert len(transport.queries) == 1
    assert (root / "route_stop.json").exists()


def test_combined_offline_raw_to_parquet_and_partial_data_only_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, plan = _plan(tmp_path, monkeypatch)
    built = x.build(REPO, root, plan)
    assert built["status"] == "PARTIAL_OR_BLOCKED"
    verified = x.verify(REPO, root)
    assert verified["manifest_sha256"] == built["manifest_sha256"]
    assert verified["ledger"] == {"distinct": 0, "attempts": 0, "retries": 0}
    reader = x.LockedResearchV2CombinedReader(
        REPO,
        root,
        dataset_version=x.COMBINED_VERSION,
        manifest_sha256=built["manifest_sha256"],
    )
    anchor = c.utc_ms(c.END - c.DAY)
    assert reader.read("BTCUSDT", "5m", anchor, anchor + 300_000).num_rows == 1
    with pytest.raises(c.CorpusError, match="cross-gap"):
        reader.read("BTCUSDT", "5m", anchor - 300_000, anchor + 300_000)
    table_path = root / "combined" / "parquet" / "BTCUSDT-5m.parquet"
    table_path.write_bytes(b"tampered")
    with pytest.raises(c.CorpusError, match="artifact hash"):
        x.verify(REPO, root)
    assert reader.read("BTCUSDT", "5m", anchor, anchor + 300_000).num_rows == 1


def test_original_verifier_runs_outside_repository_without_git_ancestry(tmp_path: Path) -> None:
    source = REPO / x.SOURCE
    docs = REPO / x.DOCS
    code = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(REPO / 'src')!r}); "
        "from adaptive_trading_bot.research import research_v2_corpus as c; "
        f"v=c.verify(Path({str(REPO)!r}),Path({str(REPO / x.ORIGINAL_ROOT)!r}),"
        f"Path({str(source)!r}),Path({str(docs)!r})); "
        f"assert v['manifest_sha256']=={x.ORIGINAL_MANIFEST_SHA!r}"
    )
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, timeout=30)
