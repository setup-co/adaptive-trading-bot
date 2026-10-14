"""Synthetic, network-free characterization of the isolated Research V2 data boundary."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from adaptive_trading_bot.research import research_v2_corpus as c


def wire(opened: int, *, volume: str = "0", close: str = "2") -> list[object]:
    return [opened, "1", "3", "1", close, volume, opened + 300_000, volume, 0, "0", "0"]


def body(*rows: list[object]) -> bytes:
    return json.dumps(list(rows)).encode()


def test_fixed_panel_reuse_and_chunk_grid_from_accepted_evidence() -> None:
    repo = Path(__file__).resolve().parents[2]
    plan = c.make_plan(repo, "s" * 64, "revision", "d" * 64)
    assert plan["panel"] == list(c.PANEL)
    assert len(plan["requests"]) == 2704
    assert len(plan["reused"]) == 16
    assert plan["attempt_budget"] == 5408
    anchor = [item for item in plan["requests"] if item["id"].startswith("BTCUSDT-89-")]
    assert anchor == []
    chunks = [item for item in plan["requests"] if item["id"].startswith("SOLUSDT-00-")]
    assert [item["expected_rows"] for item in chunks] == [100, 100, 88]
    assert chunks[0]["query"]["startTime"] == str(c.utc_ms(c.START))
    for left, right in pairwise(chunks):
        assert int(left["query"]["endTime"]) + 1 == int(right["query"]["startTime"])
    assert int(chunks[-1]["query"]["endTime"]) + 1 == c.utc_ms(c.START + c.DAY)


@pytest.mark.parametrize(
    ("index", "value"),
    [
        (0, c.utc_ms(c.START) + 1),
        (6, c.utc_ms(c.START) + 299_999),
        (2, "0.5"),
        (5, "NaN"),
        (8, -1),
        (9, "2"),
    ],
)
def test_malformed_wire_fails_closed(index: int, value: object) -> None:
    opened = c.utc_ms(c.START)
    row = wire(opened, volume="1")
    row[index] = value
    with pytest.raises(c.CorpusError):
        c.parse_rows(body(row), c.query("BTCUSDT", "5m", c.START, c.START + c.FIVE))


def test_duplicate_missing_zero_and_high_precision() -> None:
    opened = c.utc_ms(c.START)
    query = c.query("BTCUSDT", "5m", c.START, c.START + 3 * c.FIVE)
    zero = wire(opened)
    precise = wire(opened + 600_000, volume="0.000000000000000000001")
    parsed = c.parse_rows(body(zero, precise), query)
    assert parsed[0].base_volume == Decimal(0)
    assert parsed[1].base_volume == Decimal("0.000000000000000000001")
    present = {row.open_time_ms: row for row in parsed}
    assert c.exclusions(present, {})[0] == {
        "start_ms": opened + 300_000,
        "end_ms": opened + 600_000,
        "count": 1,
        "reason": "MISSING_GRID_ROW",
    }
    assert c.epochs(present)[:2] == [
        (opened, opened + 300_000),
        (opened + 600_000, opened + 900_000),
    ]
    with pytest.raises(c.CorpusError, match="duplicate"):
        c.parse_rows(body(zero, zero), query)


def test_complete_bucket_exact_decimal_and_boundary_warmup_inputs() -> None:
    opened = c.utc_ms(c.START)
    rows = {
        opened + n * 300_000: c.parse_rows(
            body(wire(opened + n * 300_000, volume="0.000000000000000000001")),
            c.query("BTCUSDT", "5m", c.START + n * c.FIVE, c.START + (n + 1) * c.FIVE),
        )[0]
        for n in range(48)
    }
    spans = c.epochs(rows)
    assert len(c.derive(rows, spans, 3)) == 16
    assert len(c.derive(rows, spans, 12)) == 4
    assert len(c.derive(rows, spans, 48)) == 1
    assert c.derive(rows, spans, 48)[0].base_volume == Decimal("0.000000000000000000048")
    del rows[opened + 300_000]
    assert not c.derive(rows, c.epochs(rows), 48)


def test_daily_mismatch_is_explicit() -> None:
    opened = c.utc_ms(c.START)
    five = {
        opened + n * 300_000: c.parse_rows(
            body(wire(opened + n * 300_000, volume="1")),
            c.query("BTCUSDT", "5m", c.START + n * c.FIVE, c.START + (n + 1) * c.FIVE),
        )[0]
        for n in range(288)
    }
    daily = c.parse_rows(
        body([opened, "1", "3", "1", "2", "287", opened + 86_400_000, "288", 0, "0", "0"]),
        c.query("BTCUSDT", "1d", c.START, c.START + timedelta(days=1)),
    )[0]
    compared = c._daily_comparisons(five, {opened: daily})
    assert compared[0]["status"] == "MISMATCH"
    assert compared[0]["deltas"]["base_volume"] == "1"


def test_retry_classification_and_no_redirect() -> None:
    assert c.retryable(status=500)
    assert not c.retryable(status=429)
    assert c.retryable(error=urllib.error.URLError(TimeoutError()))
    assert not c.retryable(error=urllib.error.URLError(OSError("dns")))
    c._NoRedirect().redirect_request(
        urllib.request.Request(c.HOST + c.ROUTE),
        None,
        302,
        "",
        None,
        "https://other.example",
    )


def test_market_route_rejects_unauthorized_shape_before_io() -> None:
    good = c.query("BTCUSDT", "5m", c.START, c.START + 100 * c.FIVE)
    c.validate_market_query(good)
    for changed in (
        {**good, "symbol": "KASUSDT"},
        {**good, "priceType": "MARK"},
        {**good, "endTime": str(int(good["endTime"]) + 1)},
        c.query("BTCUSDT", "5m", c.END - c.DAY, c.END - c.DAY + 100 * c.FIVE),
    ):
        with pytest.raises(c.CorpusError):
            c.validate_market_query(changed)


class FakeTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.queries: list[dict[str, str]] = []

    def get(self, q: dict[str, str], timeout: float) -> tuple[int, bytes]:
        assert timeout == 20
        self.queries.append(q)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, tuple)
        return outcome


def _test_plan(tmp_path: Path) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    plan = c.make_plan(repo, "s" * 64, "revision", "d" * 64)
    c.durable_new(tmp_path / "plan.json", c.canonical(plan))
    return plan


def test_capture_retries_only_timeout_and_stops_on_denial(tmp_path: Path) -> None:
    plan = _test_plan(tmp_path)
    first = plan["requests"][0]["query"]
    transport = FakeTransport([TimeoutError(), (403, b"denied")])
    result = c.capture(tmp_path, plan, transport, sleep=lambda _: None)
    assert result == {"status": "DENIED", "new_attempts": 2}
    assert transport.queries == [first, first]
    with pytest.raises(c.CorpusError, match="ambiguous STARTED"):
        # A missing durable outcome cannot be retried under this ledger.
        (tmp_path / "working" / plan["requests"][0]["id"] / "0.result.json").unlink()
        c.capture(tmp_path, plan, FakeTransport([]), sleep=lambda _: None)


def test_capture_non_timeout_network_error_has_no_retry(tmp_path: Path) -> None:
    plan = _test_plan(tmp_path)
    transport = FakeTransport([urllib.error.URLError(OSError("dns"))])
    assert c.capture(tmp_path, plan, transport, sleep=lambda _: None) == {
        "status": "NETWORK_UNAVAILABLE",
        "new_attempts": 1,
    }
    assert len(transport.queries) == 1
    assert c.capture(tmp_path, plan, FakeTransport([]), sleep=lambda _: None) == {
        "status": "NETWORK_UNAVAILABLE",
        "new_attempts": 0,
    }


def test_redirect_and_local_persistence_failure_stop_before_next_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _test_plan(tmp_path)
    redirected = FakeTransport([(302, b"Location: https://other.example")])
    assert c.capture(tmp_path, plan, redirected, sleep=lambda _: None) == {
        "status": "POLICY_FAILURE",
        "new_attempts": 1,
    }
    assert len(redirected.queries) == 1
    other = tmp_path / "other"
    _test_plan(other)
    real_write = c.durable_new

    def fail_raw(path: Path, data: bytes) -> None:
        if path.suffix == ".raw":
            raise OSError("disk unavailable")
        real_write(path, data)

    monkeypatch.setattr(c, "durable_new", fail_raw)
    transport = FakeTransport([(200, b"[]")])
    with pytest.raises(OSError, match="disk unavailable"):
        c.capture(other, plan, transport, sleep=lambda _: None)
    assert len(transport.queries) == 1
    with pytest.raises(c.CorpusError, match="ambiguous STARTED"):
        c.capture(other, plan, FakeTransport([]), sleep=lambda _: None)


def test_reader_rejects_unknown_manifest_before_read(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        c.LockedResearchV2CorpusReader(
            tmp_path,
            tmp_path,
            dataset_version=c.DATASET_VERSION,
            manifest_sha256="bad",
            source_file=tmp_path / "source",
            docs_file=tmp_path / "doc",
        )


def test_offline_raw_to_parquet_and_rehashed_false_report_rejected(tmp_path: Path) -> None:
    """A partial synthetic projection is still verified from old and new raw, not Git ancestry."""
    repo = Path(__file__).resolve().parents[2]
    source = repo / "src/adaptive_trading_bot/research/research_v2_corpus.py"
    docs = repo / "docs/research/research_v2_corpus_qualification.md"
    root = tmp_path / "corpus"
    plan = c.make_plan(
        repo,
        c.sha(source.read_bytes().replace(b"\r\n", b"\n")),
        "historical-revision-not-required-locally",
        c.sha(docs.read_bytes().replace(b"\r\n", b"\n")),
    )
    c.durable_new(root / "plan.json", c.canonical(plan))
    result = c.build(repo, root, plan)
    assert result["status"] == "PARTIAL_OR_BLOCKED"
    verified = c.verify(repo, root, source, docs)
    assert verified["manifest_sha256"] == result["manifest_sha256"]
    assert verified["symbols"]["BTCUSDT"]["valid_5m_rows"] == 288
    assert verified["symbols"]["SOLUSDT"]["valid_5m_rows"] == 0
    reader = c.LockedResearchV2CorpusReader(
        repo,
        root,
        dataset_version=c.DATASET_VERSION,
        manifest_sha256=result["manifest_sha256"],
        source_file=source,
        docs_file=docs,
    )
    with pytest.raises(c.CorpusError, match="unqualified"):
        reader.read("BTCUSDT", "5m", c.utc_ms(c.END - c.DAY), c.utc_ms(c.END))
    report = c.read_json(root / "report.json")
    report["symbols"]["SOLUSDT"]["valid_5m_rows"] = 25920
    (root / "report.json").write_bytes(c.canonical(report))
    manifest = c.read_json(root / "manifest.json")
    manifest["files"]["report.json"] = c.sha((root / "report.json").read_bytes())
    (root / "manifest.json").write_bytes(c.canonical(manifest))
    with pytest.raises(c.CorpusError, match="report differs"):
        c.verify(repo, root, source, docs)
