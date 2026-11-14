from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest

from adaptive_trading_bot.research import binance_grid_history as subject


def _zip(name: str, text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return output.getvalue()


def _kline(start: int, minutes: int = 1, *, header: bool = False) -> str:
    lines = []
    if header:
        lines.append(
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
            "taker_buy_volume,taker_buy_quote_volume,ignore"
        )
    for index in range(minutes):
        at = start + index * 60_000
        lines.append(f"{at},10,12,9,11,2.0,{at + 59999},21.0,3,1,10,0")
    return "\n".join(lines) + "\n"


def test_plan_is_exact_and_unique() -> None:
    plan = subject.make_plan("a" * 40)
    assert len(plan["resources"]) == 1_312
    assert len({row["url"] for row in plan["resources"]}) == 1_312
    assert plan["months"][0] == "2020-01"
    assert plan["months"][-1] == "2026-10"
    assert sum(row["kind"] == "zip" for row in plan["resources"]) == 656
    assert sum(row["kind"] == "checksum" for row in plan["resources"]) == 656


def test_kline_parser_accepts_header_and_normalizes_inclusive_close(tmp_path: Path) -> None:
    body = _zip("BTCUSDT-1m-2020-01.csv", _kline(1_577_836_800_000, header=True))
    path = tmp_path / "x.zip"
    path.write_bytes(body)
    rows = subject.parse_klines(path, 60_000)
    assert rows[0]["open"] == "10"
    assert rows[0]["wire_close_time_ms"] == 1_577_836_859_999
    assert rows[0]["close_time_ms"] == 1_577_836_860_000


def test_checksum_name_and_hash_are_strict(tmp_path: Path) -> None:
    body = b"archive"
    digest = hashlib.sha256(body).hexdigest()
    assert subject._official_checksum(f"{digest}  file.zip\n".encode(), "file.zip") == digest
    with pytest.raises(subject.CorpusError):
        subject._official_checksum(f"{digest}  other.zip\n".encode(), "file.zip")


def test_derive_requires_240_complete_minutes_and_reconciles_native(tmp_path: Path) -> None:
    start = 1_577_836_800_000
    minute_path = tmp_path / "minute.zip"
    minute_path.write_bytes(_zip("m.csv", _kline(start, 240)))
    native_path = tmp_path / "native.zip"
    native_path.write_bytes(
        _zip("n.csv", f"{start},10,12,9,11,480.0,{start + 14_399_999},5040.0,720,240,2400,0\n")
    )
    minute = subject.parse_klines(minute_path, 60_000)
    native = subject.parse_klines(native_path, 14_400_000)
    result, comparisons = subject.derive_and_reconcile(minute, native)
    assert result == [
        {
            "open_time_ms": start,
            "close_time_ms": start + 14_400_000,
            "open": "10",
            "high": "12",
            "low": "9",
            "close": "11",
            "volume": "480.0",
            "quote_volume": "5040.0",
            "source_minutes": 240,
            "available_at_ms": start + 14_400_000,
        }
    ]
    assert comparisons[0]["status"] == "MATCH"


def test_gap_only_prevents_its_utc_bucket_and_later_complete_buckets_survive(
    tmp_path: Path,
) -> None:
    start = 1_577_836_800_000
    lines = _kline(start, 720).splitlines()
    path = tmp_path / "minute.zip"
    path.write_bytes(_zip("m.csv", "\n".join(lines[:100] + lines[101:]) + "\n"))
    minute = subject.parse_klines(path, 60_000)
    native_path = tmp_path / "native.zip"
    native_path.write_bytes(
        _zip(
            "n.csv",
            "".join(
                f"{start + index * 14_400_000},10,12,9,11,480.0,"
                f"{start + (index + 1) * 14_400_000 - 1},5040.0,720,240,2400,0\n"
                for index in range(3)
            ),
        )
    )
    derived, comparisons = subject.derive_and_reconcile(
        minute, subject.parse_klines(native_path, 14_400_000)
    )
    assert [row["open_time_ms"] for row in derived] == [
        start + 14_400_000,
        start + 28_800_000,
    ]
    assert [row["status"] for row in comparisons] == [
        "INCOMPLETE_DERIVATION_BUCKET",
        "MATCH",
        "MATCH",
    ]


def test_ohlc_and_volume_mismatches_are_retained_without_dropping_other_bars(
    tmp_path: Path,
) -> None:
    start = 1_577_836_800_000
    minute_path = tmp_path / "minute.zip"
    minute_path.write_bytes(_zip("m.csv", _kline(start, 480)))
    native_path = tmp_path / "native.zip"
    native_path.write_bytes(
        _zip(
            "n.csv",
            f"{start},10,13,9,11,480.0,{start + 14_399_999},5040.0,720,240,2400,0\n"
            f"{start + 14_400_000},10,12,9,11,481.0,{start + 28_799_999},5040.0,720,240,2400,0\n",
        )
    )
    derived, comparisons = subject.derive_and_reconcile(
        subject.parse_klines(minute_path, 60_000),
        subject.parse_klines(native_path, 14_400_000),
    )
    assert len(derived) == 2
    assert comparisons[0]["status"] == "OHLC_MISMATCH"
    assert comparisons[0]["mismatched_fields"] == ["high"]
    assert comparisons[0]["native_high"] == "13"
    assert comparisons[0]["derived_high"] == "12"
    assert comparisons[1]["status"] == "VOLUME_MISMATCH"
    assert comparisons[1]["mismatched_fields"] == ["volume"]


def test_funding_preserves_event_schedule_and_decimal_lexeme(tmp_path: Path) -> None:
    path = tmp_path / "funding.zip"
    path.write_bytes(
        _zip(
            "f.csv",
            "calc_time,funding_interval_hours,last_funding_rate\n1577836800000,8,0.00010000\n",
        )
    )
    assert subject.parse_funding(path) == [
        {
            "calc_time_ms": 1_577_836_800_000,
            "funding_interval_hours": 8,
            "last_funding_rate": "0.00010000",
            "source_header": ["calc_time", "funding_interval_hours", "last_funding_rate"],
        }
    ]


def test_funding_accepts_signed_rates_without_weakening_volume_validation(tmp_path: Path) -> None:
    path = tmp_path / "funding.zip"
    path.write_bytes(_zip("f.csv", "1577836800000,8,-0.0001235900\n"))
    assert subject.parse_funding(path)[0]["last_funding_rate"] == "-0.0001235900"
    with pytest.raises(subject.CorpusError, match="outside bounds"):
        subject._decimal("-1")


def test_unknown_headers_and_nonchronological_rows_fail_closed(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.zip"
    unknown.write_bytes(_zip("x.csv", "time,open,high\n"))
    with pytest.raises(subject.CorpusError, match="unrecognized kline header"):
        subject.parse_klines(unknown, 60_000)
    start = 1_577_836_800_000
    reversed_rows = _kline(start, 2).splitlines()[::-1]
    unordered = tmp_path / "unordered.zip"
    unordered.write_bytes(_zip("x.csv", "\n".join(reversed_rows) + "\n"))
    with pytest.raises(subject.CorpusError, match="not strictly chronological"):
        subject.parse_klines(unordered, 60_000)


def test_funding_continuity_detects_missing_event_across_month_boundary() -> None:
    tracker = subject.FundingTracker()
    tracker.add(
        [
            {
                "calc_time_ms": 1_580_515_200_000,
                "funding_interval_hours": 8,
                "last_funding_rate": "0.1",
            }
        ],
        "2020-01",
    )
    tracker.add(
        [
            {
                "calc_time_ms": 1_580_572_800_000,
                "funding_interval_hours": 8,
                "last_funding_rate": "0.1",
            }
        ],
        "2020-02",
    )
    result = tracker.finish()
    assert result["continuity_status"] == "SCHEDULE_PARTIAL_OR_UNCERTAIN"
    cross_month = next(
        row for row in result["continuity_findings"] if row.get("cross_month") is True
    )
    assert cross_month["actual_delta_ms"] == 57_600_000


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers: dict[str, str] = {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class _Transport:
    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    def open(self, url: str, timeout: float) -> _Response:
        self.calls.append(url)
        return _Response(self.bodies[url])


def _mini_plan(tmp_path: Path, count: int = 1) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    full = subject.make_plan("a" * 40)
    resources = full["resources"][:count]
    plan = {**full, "resources": resources}
    subject.durable_new(tmp_path / "plan.json", subject.canonical(plan))
    return plan, resources


def test_capture_reuses_verified_success_and_never_resets_budget(tmp_path: Path) -> None:
    plan, resources = _mini_plan(tmp_path)
    # Exercise the capture core with a bounded fixture; the plan validator remains strict.
    item = resources[0]
    budget = subject._PayloadBudget(0, Lock())
    transport = _Transport({item["url"]: b"payload"})
    first = subject._capture_resource(
        tmp_path, plan, item, transport, budget, lambda _: None, Event()
    )
    second = subject._capture_resource(
        tmp_path, plan, item, transport, budget, lambda _: None, Event()
    )
    assert first["outcome"] == "HTTP_OK"
    assert second["outcome"] == "REUSED_SUCCESS"
    assert transport.calls == [item["url"]]


class _Forbidden:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, url: str, timeout: float) -> _Response:
        self.calls += 1
        return _Response(b"", status=403)


def test_global_stop_never_drains_the_queued_inventory(tmp_path: Path) -> None:
    plan, resources = _mini_plan(tmp_path, count=20)
    transport = _Forbidden()
    with pytest.raises(subject.GlobalCaptureStop):
        subject._capture_items(
            tmp_path,
            plan,
            resources,
            transport,
            subject._PayloadBudget(0, Lock()),
            lambda _: None,
        )
    assert 1 <= transport.calls <= subject.MAX_WORKERS
    assert len(list((tmp_path / "ledger").rglob("attempt-*.json"))) <= subject.MAX_WORKERS


class _Interrupted:
    def open(self, url: str, timeout: float) -> _Response:
        raise KeyboardInterrupt


def test_attempt_is_reserved_before_an_interrupted_transmission(tmp_path: Path) -> None:
    plan, resources = _mini_plan(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        subject._capture_resource(
            tmp_path,
            plan,
            resources[0],
            _Interrupted(),
            subject._PayloadBudget(0, Lock()),
            lambda _: None,
            Event(),
        )
    attempts = subject._attempts(tmp_path, resources[0]["id"])
    assert len(attempts) == 1
    assert attempts[0]["status"] == "FINISHED"
    assert attempts[0]["outcome"] == "UNKNOWN_INTERRUPTED"
    assert attempts[0]["bytes_received"] == 0
    assert attempts[0]["capture_executable_sha"] == plan["executable_sha"]


class _PartialResponse(_Response):
    def __init__(self) -> None:
        super().__init__(b"")
        self._served = False

    def read(self, size: int = -1) -> bytes:
        if not self._served:
            self._served = True
            return b"x" * 24
        raise OSError("disconnect")


class _PartialTransport:
    def open(self, url: str, timeout: float) -> _Response:
        return _PartialResponse()


def test_partial_payload_bytes_and_budget_survive_restart(tmp_path: Path) -> None:
    plan, resources = _mini_plan(tmp_path)
    result = subject._capture_resource(
        tmp_path,
        plan,
        resources[0],
        _PartialTransport(),
        subject._PayloadBudget(0, Lock()),
        lambda _: None,
        Event(),
    )
    assert result["outcome"] == "ATTEMPTS_EXHAUSTED"
    assert subject._accounted_payload_bytes(tmp_path, plan) == 72
    assert sum(path.stat().st_size for path in (tmp_path / "partials").rglob("*.bin")) == 72


def test_grid_report_preserves_gaps_and_half_open_epochs() -> None:
    start = int(subject.START.timestamp() * 1000)
    result = subject._grid_intervals([start, start + 60_000, start + 180_000], 60_000)
    assert result["gap_count"] == 2
    assert result["gaps"][0] == {
        "start": "2020-01-01T00:02:00Z",
        "end_exclusive": "2020-01-01T00:03:00Z",
        "missing_rows": 1,
    }
    assert result["maximal_valid_epochs"][0] == {
        "start": "2020-01-01T00:00:00Z",
        "end_exclusive": "2020-01-01T00:02:00Z",
    }
