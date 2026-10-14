"""Offline synthetic characterization of the bounded public-only feasibility probe."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research.research_v2_universe_feasibility import (
    BUDGET,
    Capture,
    FeasibilityError,
    candle_rows,
    capture_bundle,
    capture_history,
    complete,
    digest,
    history_query,
    ms,
    raw_json,
    select_probes,
    validate_route,
    verify_bundle,
)

START = datetime(2026, 1, 1, tzinfo=UTC)


def row(opened: datetime, step: timedelta, *, base: str = "2", quote: str = "200") -> list[object]:
    return [
        ms(opened),
        "100",
        "110",
        "90",
        "105",
        base,
        ms(opened + step),
        quote,
        2,
        "1",
        "100",
    ]


class FakeTransport:
    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
        self.calls.append((path, query, timeout))
        return self.responses.pop(0)


def test_public_route_and_method_are_hard_rejected() -> None:
    for method, path in (
        ("POST", "/capi/v3/market/time"),
        ("GET", "/capi/v3/order/history"),
        ("GET", "/capi/v3/sim/order/history"),
        ("GET", "https://another-host.example/capi/v3/market/time"),
    ):
        with pytest.raises(FeasibilityError):
            validate_route(method, path, {})
    with pytest.raises(FeasibilityError):
        validate_route("GET", "/capi/v3/market/time", {"symbol": "BTCUSDT"})
    invalid = history_query("BTCUSDT", "1d", START, START + timedelta(days=1))
    invalid["priceType"] = "MARK"
    with pytest.raises(FeasibilityError):
        validate_route("GET", "/capi/v3/market/historyKlines", invalid)
    assert BUDGET == 64


def test_exact_decimal_missing_zero_and_grid_validation() -> None:
    raw = json.dumps([row(START, timedelta(days=1), quote="200.0000000000000000001")]).encode()
    parsed = candle_rows(raw, START, START + timedelta(days=1), "1d")
    assert parsed == [(ms(START), Decimal("200.0000000000000000001"))]
    assert complete(parsed, START, START + timedelta(days=1), "1d")
    assert not complete(parsed, START, START + timedelta(days=2), "1d")
    assert raw_json(b'{"quote":0}') == {"quote": 0}
    assert raw_json(b'{"quote":null}') == {"quote": None}
    fractional = raw_json(b'{"quote":0.1}')
    assert isinstance(fractional, dict) and fractional["quote"] == Decimal("0.1")
    for rows in (
        [row(START, timedelta(days=1)), row(START, timedelta(days=1))],
        [row(START + timedelta(hours=1), timedelta(days=1))],
        [row(START, timedelta(days=1), base="0", quote="1")],
        [row(START, timedelta(days=1), quote="NaN")],
        [[ms(START), "100", "110"]],
    ):
        with pytest.raises(FeasibilityError):
            candle_rows(json.dumps(rows).encode(), START, START + timedelta(days=1), "1d")


def test_current_probe_selection_is_deterministic_not_historical_or_ranked() -> None:
    def contract(symbol: str, base: str, *, underlying: str = "COIN") -> dict[str, str]:
        return {
            "symbol": symbol,
            "baseAsset": base,
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "contractType": "PERPETUAL",
            "underlyingType": underlying,
        }

    catalog = {
        "symbols": [
            contract("XRPUSDT", "XRP"),
            contract("ETHUSDT", "ETH"),
            contract("USDCUSDT", "USDC"),
            contract("BTCUSDT", "BTC"),
            contract("SOLUSDT", "SOL"),
            contract("BNDUSDT", "BND", underlying="TRADIFI"),
        ]
    }
    api = [item["symbol"] for item in catalog["symbols"]]
    considered, probes = select_probes(json.dumps(catalog).encode(), json.dumps(api).encode())
    assert probes == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    assert {item["symbol"]: item["reason"] for item in considered}[
        "USDCUSDT"
    ] == "EXCLUDED_UNDERLYING"
    assert {item["symbol"]: item["reason"] for item in considered}[
        "BNDUSDT"
    ] == "NOT_CRYPTO_LINEAR_PERPETUAL"
    incomplete, selected = select_probes(b'{"symbols":[{"symbol":"BTCUSDT"}]}', b'["BTCUSDT"]')
    assert selected == []
    assert incomplete[0]["reason"] == "IDENTITY_INCOMPLETE"


def test_pagination_non_advance_and_request_budget(tmp_path: Path) -> None:
    first = [row(START + timedelta(minutes=5 * i), timedelta(minutes=5)) for i in range(100)]
    transport = FakeTransport(
        [(200, json.dumps(first).encode()), (200, json.dumps(first).encode())]
    )
    capture = Capture(tmp_path / "capture", transport)
    result = capture_history(capture, "BTCUSDT", "5m", START, START + timedelta(days=1), "x")
    assert result["status"] == "EMPTY_OR_NON_ADVANCING_PAGE"
    assert len(transport.calls) == 2
    assert all(call[2] <= 20 for call in transport.calls)
    assert capture.attempts[0]["sha256"] == digest(json.dumps(first).encode())
    limited = Capture(tmp_path / "limited", FakeTransport([(200, b"{}")]), budget=1)
    limited.get("/capi/v3/market/time", {}, "first")
    with pytest.raises(FeasibilityError, match="budget"):
        limited.get("/capi/v3/market/time", {}, "second")


def test_saved_bundle_verification_has_no_network_and_rejects_tamper(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    transport = FakeTransport([(403, b'{"error":"blocked"}')])
    result = capture_bundle(root, transport)
    assert result["status"] == "BOOTSTRAP_UNAVAILABLE"
    assert result["request_count"] == 1
    assert verify_bundle(root) == result
    assert len(transport.calls) == 1  # verify never invokes transport
    raw = root / "001-server_time.raw.json"
    raw.write_bytes(b"tampered")
    with pytest.raises(FeasibilityError, match=r"hash|length"):
        verify_bundle(root)
