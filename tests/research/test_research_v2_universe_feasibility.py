"""Offline synthetic characterization of the bounded public-only feasibility probe."""

from __future__ import annotations

import json
import shutil
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
    canonical,
    capture_bundle,
    capture_history,
    capture_supplement,
    complete,
    digest,
    history_query,
    ms,
    raw_json,
    reconcile_results,
    select_probes,
    supplement_request_plan,
    validate_route,
    verify_bundle,
    verify_supplement,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
ORIGINAL = (
    Path(__file__).resolve().parents[2]
    / "data/research_evidence/issue-173-weex-public-feasibility-332db22"
)


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
    first = [row(START + timedelta(minutes=5 * i), timedelta(minutes=5)) for i in range(188, 288)]
    transport = FakeTransport(
        [(200, json.dumps(first).encode()), (200, json.dumps(first).encode())]
    )
    capture = Capture(tmp_path / "capture", transport)
    result = capture_history(capture, "BTCUSDT", "5m", START, START + timedelta(days=1), "x")
    assert result["status"] == "UNSUPPORTED_OR_INVALID_HISTORY_SCHEMA"
    assert len(transport.calls) == 2
    assert transport.calls[1][1]["endTime"] == str(ms(START + timedelta(minutes=5 * 188)) - 1)
    assert all(call[2] <= 20 for call in transport.calls)
    assert capture.attempts[0]["sha256"] == digest(json.dumps(first).encode())
    limited = Capture(tmp_path / "limited", FakeTransport([(200, b"{}")]), budget=1)
    limited.get("/capi/v3/market/time", {}, "first")
    with pytest.raises(FeasibilityError, match="budget"):
        limited.get("/capi/v3/market/time", {}, "second")


def test_right_anchored_288_bar_pagination_is_exact_and_bounded(tmp_path: Path) -> None:
    bars = [row(START + timedelta(minutes=5 * i), timedelta(minutes=5)) for i in range(288)]
    transport = FakeTransport(
        [(200, json.dumps(page).encode()) for page in (bars[188:], bars[88:188], bars[:88])]
    )
    result = capture_history(
        Capture(tmp_path / "pages", transport),
        "BTCUSDT",
        "5m",
        START,
        START + timedelta(days=1),
        "historical",
    )
    assert result["status"] == "COMPLETE"
    assert result["rows"] == 288
    assert result["pages"] == 3
    assert result["open_times"] == [ms(START + timedelta(minutes=5 * i)) for i in range(288)]
    assert [call[1]["startTime"] for call in transport.calls] == [str(ms(START))] * 3
    assert [call[1]["endTime"] for call in transport.calls] == [
        str(ms(START + timedelta(days=1)) - 1),
        str(ms(START + timedelta(minutes=5 * 188)) - 1),
        str(ms(START + timedelta(minutes=5 * 88)) - 1),
    ]


def test_pagination_rejects_malformed_duplicate_and_exhausted_pages(tmp_path: Path) -> None:
    bars = [row(START + timedelta(minutes=5 * i), timedelta(minutes=5)) for i in range(400)]
    for label, pages, expected in (
        ("duplicate", (bars[188:288], bars[188:288]), "UNSUPPORTED_OR_INVALID_HISTORY_SCHEMA"),
        ("malformed", (bars[188:288], [["invalid"]]), "UNSUPPORTED_OR_INVALID_HISTORY_SCHEMA"),
        ("exhausted", (bars[300:400], bars[200:300], bars[100:200]), "PAGE_LIMIT_INCOMPLETE"),
    ):
        transport = FakeTransport([(200, json.dumps(page).encode()) for page in pages])
        end = START + timedelta(minutes=5 * (400 if label == "exhausted" else 288))
        result = capture_history(
            Capture(tmp_path / label, transport), "BTCUSDT", "5m", START, end, label
        )
        assert result["status"] == expected


def test_denied_history_route_stops_later_planned_requests(tmp_path: Path) -> None:
    transport = FakeTransport([(403, b'{"message":"denied"}')])
    capture = Capture(tmp_path / "denied", transport)
    first = capture_history(capture, "BTCUSDT", "1d", START, START + timedelta(days=1), "a")
    second = capture_history(capture, "ETHUSDT", "1d", START, START + timedelta(days=1), "b")
    assert first["status"] == "TRANSPORT_OR_HTTP_UNAVAILABLE"
    assert second["status"] == "ROUTE_BLOCKED"
    assert len(transport.calls) == 1
    assert len(capture.attempts) == 1


def test_exact_reconciliation_only_for_complete_days() -> None:
    boundary = START + timedelta(days=1)
    daily = {
        "symbol": "BTCUSDT",
        "interval": "1d",
        "end": boundary.isoformat().replace("+00:00", "Z"),
        "complete": True,
        "open_times": [ms(START)],
        "quote_volumes": ["288.000"],
    }
    five = {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "end": daily["end"],
        "complete": True,
        "quote_volumes": ["1"] * 288,
    }
    assert reconcile_results([daily, five], boundary, boundary)[0]["status"] == "EXACT_MATCH"
    five["complete"] = False
    assert reconcile_results([daily, five], boundary, boundary)[0]["status"] == "UNAVAILABLE"


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


def test_rehashed_forged_derived_summary_still_fails_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "forged"
    capture_bundle(root, FakeTransport([(403, b'{"error":"blocked"}')]))
    summary_path = root / "summary.json"
    manifest_path = root / "manifest.json"
    summary = raw_json(summary_path.read_bytes())
    manifest = raw_json(manifest_path.read_bytes())
    assert isinstance(summary, dict) and isinstance(manifest, dict)
    summary["current_catalog_considered_count"] = 999
    summary_bytes = canonical(summary)
    summary_path.write_bytes(summary_bytes)
    manifest["summary_sha256"] = digest(summary_bytes)
    files = manifest["files"]
    assert isinstance(files, dict)
    files["summary.json"] = {"bytes": len(summary_bytes), "sha256": digest(summary_bytes)}
    manifest_path.write_bytes(canonical(manifest))

    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline verification attempted network")

    monkeypatch.setattr(
        "scripts.research.research_v2_universe_feasibility.PublicGetTransport.get",
        unexpected_network,
    )
    with pytest.raises(FeasibilityError, match="derived summary"):
        verify_bundle(root)


@pytest.mark.parametrize("forgery", ["count", "complete", "quote"])
def test_original_raw_derivation_rejects_rehashed_forged_facts(
    tmp_path: Path, forgery: str
) -> None:
    root = tmp_path / "original-copy"
    shutil.copytree(ORIGINAL, root)
    summary_path, manifest_path = root / "summary.json", root / "manifest.json"
    summary = raw_json(summary_path.read_bytes())
    manifest = raw_json(manifest_path.read_bytes())
    assert isinstance(summary, dict) and isinstance(manifest, dict)
    results = summary["results"]
    assert isinstance(results, list) and isinstance(results[0], dict)
    if forgery == "count":
        results[0]["rows"] = 29
    elif forgery == "complete":
        results[0]["complete"] = False
    else:
        values = results[0]["quote_volumes"]
        assert isinstance(values, list)
        values[0] = "123456789.123"
    summary_bytes = canonical(summary)
    summary_path.write_bytes(summary_bytes)
    manifest["summary_sha256"] = digest(summary_bytes)
    files = manifest["files"]
    assert isinstance(files, dict)
    files["summary.json"] = {"bytes": len(summary_bytes), "sha256": digest(summary_bytes)}
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(FeasibilityError, match="derived summary"):
        verify_bundle(root)


def test_supplement_raw_derivation_and_exact_anchor_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = supplement_request_plan(ORIGINAL)
    requests = plan["requests"]
    assert isinstance(requests, list) and len(requests) == 8
    responses: list[tuple[int, bytes]] = []
    for request in requests:
        assert isinstance(request, dict)
        first = datetime.fromtimestamp(int(str(request["expected_first_open"])) / 1000, tz=UTC)
        count = int(str(request["expected_count"]))
        responses.append(
            (
                200,
                json.dumps(
                    [
                        row(first + i * timedelta(minutes=5), timedelta(minutes=5))
                        for i in range(count)
                    ]
                ).encode(),
            )
        )
    transport = FakeTransport(responses)
    monkeypatch.setattr(
        "scripts.research.research_v2_universe_feasibility.time.sleep", lambda _seconds: None
    )
    supplement = tmp_path / "supplement"
    analysis = capture_supplement(ORIGINAL, supplement, transport)
    assert analysis["supplement_request_count"] == 8
    assert analysis["cumulative_request_count"] == 35
    anchors = analysis["anchors"]
    reconciliations = analysis["reconciliations"]
    assert isinstance(anchors, list) and all(isinstance(anchor, dict) for anchor in anchors)
    assert isinstance(reconciliations, list) and all(
        isinstance(item, dict) for item in reconciliations
    )
    assert all(anchor["status"] == "COMPLETE" and anchor["rows"] == 288 for anchor in anchors)
    assert all(item["status"] == "MISMATCH" for item in reconciliations)
    assert [call[1] for call in transport.calls] == [request["query"] for request in requests]

    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("verification attempted network")

    monkeypatch.setattr(
        "scripts.research.research_v2_universe_feasibility.PublicGetTransport.get",
        unexpected_network,
    )
    assert verify_supplement(ORIGINAL, supplement) == analysis

    analysis_path, manifest_path = supplement / "analysis.json", supplement / "manifest.json"
    forged = raw_json(analysis_path.read_bytes())
    manifest = raw_json(manifest_path.read_bytes())
    assert isinstance(forged, dict) and isinstance(manifest, dict)
    anchors = forged["anchors"]
    assert isinstance(anchors, list) and isinstance(anchors[0], dict)
    anchors[0]["rows"] = 287
    forged_bytes = canonical(forged)
    analysis_path.write_bytes(forged_bytes)
    manifest["analysis_sha256"] = digest(forged_bytes)
    files = manifest["files"]
    assert isinstance(files, dict)
    files["analysis.json"] = {"bytes": len(forged_bytes), "sha256": digest(forged_bytes)}
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(FeasibilityError, match="derived analysis"):
        verify_supplement(ORIGINAL, supplement)


def test_supplement_stops_after_denied_route_without_followup_io(tmp_path: Path) -> None:
    transport = FakeTransport([(403, b'{"message":"denied"}')])
    supplement = tmp_path / "denied-supplement"
    analysis = capture_supplement(ORIGINAL, supplement, transport)
    assert analysis["supplement_request_count"] == 1
    assert analysis["cumulative_request_count"] == 28
    assert len(transport.calls) == 1
    reconciliations = analysis["reconciliations"]
    assert isinstance(reconciliations, list)
    assert all(
        isinstance(item, dict) and item["status"] == "UNAVAILABLE" for item in reconciliations
    )
    assert verify_supplement(ORIGINAL, supplement) == analysis
