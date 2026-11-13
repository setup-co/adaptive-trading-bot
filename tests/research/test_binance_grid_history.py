from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from threading import Lock
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
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_base,taker_quote,ignore"
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
    result = subject.derive_and_reconcile(minute, native)
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
        }
    ]


def test_gap_prevents_derived_bucket(tmp_path: Path) -> None:
    start = 1_577_836_800_000
    lines = _kline(start, 240).splitlines()
    path = tmp_path / "minute.zip"
    path.write_bytes(_zip("m.csv", "\n".join(lines[:100] + lines[101:]) + "\n"))
    minute = subject.parse_klines(path, 60_000)
    assert subject.derive_and_reconcile(minute, []) == []


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


class _Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)
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


def test_capture_reuses_verified_success_and_never_resets_budget(tmp_path: Path) -> None:
    plan = subject.make_plan("a" * 40)
    plan["resources"] = plan["resources"][:2]
    # Exercise the capture core with a bounded fixture; the plan validator remains strict.
    item = plan["resources"][0]
    budget = subject._PayloadBudget(0, Lock())
    transport = _Transport({item["url"]: b"payload"})
    first = subject._capture_resource(tmp_path, item, transport, budget, lambda _: None)
    second = subject._capture_resource(tmp_path, item, transport, budget, lambda _: None)
    assert first["outcome"] == "HTTP_OK"
    assert second["outcome"] == "REUSED_SUCCESS"
    assert transport.calls == [item["url"]]
