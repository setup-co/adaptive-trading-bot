from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from adaptive_trading_bot.research import weex_forward_evidence as forward
from adaptive_trading_bot.research.weex_demo_reconciliation import (
    CLIENT_ORDER_ID,
    END_MS,
    START_MS,
    DemoReconciliationRunner,
    ReconciliationBundle,
    ReconciliationError,
    ReconciliationStatus,
    _verify_bundle,
    verify_bundle,
    write_bundle,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)


def _order(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "orderId": 702345678901234567,
        "clientOrderId": CLIENT_ORDER_ID,
        "symbol": "BTCSUSDT",
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "status": "FILLED",
        "origQty": "0.01",
        "executedQty": "0.01",
        "avgPrice": "65000.125",
        "cumQuote": "650.00125",
        "time": START_MS + 5 * 60_000,
        "updateTime": START_MS + 5 * 60_000 + 1000,
    }
    row.update(changes)
    return row


class FakeGetTransport:
    def __init__(
        self,
        pages: Sequence[Sequence[object]],
        *,
        positions: list[object] | None = None,
    ) -> None:
        self.pages = pages
        self.positions = positions if positions is not None else []
        self.calls: list[tuple[str, str]] = []

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object:
        assert base_url == forward.WEEX_V3_BASE_URL
        assert headers["ACCESS-KEY"] == "fake-api-key"
        self.calls.append((path, query))
        if path == forward.DEMO_POSITIONS_PATH:
            assert query == ""
            return self.positions
        assert path == forward.DEMO_ORDER_HISTORY_PATH
        parameters = parse_qs(query)
        assert parameters == {
            "symbol": ["BTCSUSDT"],
            "startTime": [str(START_MS)],
            "endTime": [str(END_MS)],
            "limit": ["100"],
            "page": [str(len(self.calls) - 2)],
        }
        return list(self.pages[len(self.calls) - 2])


def _capture(
    pages: Sequence[Sequence[object]], *, positions: list[object] | None = None
) -> tuple[ReconciliationBundle, FakeGetTransport]:
    transport = FakeGetTransport(pages, positions=positions)
    credentials = forward.WeexDemoCredentials(
        forward.SecretValue("fake-api-key"),
        forward.SecretValue("fake-secret-key"),
        forward.SecretValue("fake-passphrase"),
    )
    client = forward.WeexDemoReadOnlyClient(
        credentials, transport, timestamp_ms=lambda: 1790100000000
    )
    return DemoReconciliationRunner(client).capture(
        captured_at=datetime(2026, 9, 22, 18, tzinfo=UTC)
    ), transport


def test_exact_fixed_query_and_recorded_match(tmp_path: Path) -> None:
    bundle, transport = _capture([[_order()]])
    assert bundle.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_EXACTLY_RECORDED
    assert bundle.inspected_row_count == 1
    assert bundle.exact_match_count == 1
    assert bundle.matching_orders[0].original_quantity == Decimal("0.01")
    assert [path for path, _ in transport.calls] == [
        forward.DEMO_POSITIONS_PATH,
        forward.DEMO_ORDER_HISTORY_PATH,
    ]
    manifest = write_bundle(bundle, tmp_path / bundle.bundle_version)
    artifact = (tmp_path / bundle.bundle_version / "bundle.json").read_text()
    assert "702345678901234567" not in artifact
    assert "fake-api-key" not in artifact
    assert "fake-secret-key" not in artifact
    assert "fake-passphrase" not in artifact
    assert "ACCESS-SIGN" not in artifact
    assert "ACCESS-TIMESTAMP" not in artifact
    assert manifest.artifact_sha256
    from hashlib import sha256

    manifest_sha = sha256((tmp_path / bundle.bundle_version / "manifest.json").read_bytes())
    _verify_bundle(
        tmp_path / bundle.bundle_version,
        expected_version=bundle.bundle_version,
        expected_manifest_sha256=manifest_sha.hexdigest(),
    )
    (tmp_path / bundle.bundle_version / "bundle.json").write_text(artifact + " ")
    with pytest.raises(CorpusHashMismatchError):
        _verify_bundle(
            tmp_path / bundle.bundle_version,
            expected_version=bundle.bundle_version,
            expected_manifest_sha256=manifest_sha.hexdigest(),
        )


def test_locked_reconciliation_and_source_identities() -> None:
    root = Path(__file__).resolve().parents[2]
    version = "weex-demo-reconciliation-4a17f9406b037ce84d6ce288"
    verify_bundle(root / "data" / "research_reconciliation" / version, bundle_version=version)
    with pytest.raises(CorpusManifestError):
        verify_bundle(root / "data" / "research_reconciliation" / version, bundle_version="unknown")
    source = (
        root / "data" / "research_calibration" / ("weex-demo-calibration-08376b6b937422f89c4b4aab")
    )
    from hashlib import sha256

    assert sha256((source / "bundle.json").read_bytes()).hexdigest() == (
        "39211ed64a17bd53b0ee6d03a78825c98301ed3d94f64d2ae467c77a000e74c3"
    )
    assert sha256((source / "manifest.json").read_bytes()).hexdigest() == (
        "189f8a20747a04a022a96807d6d2d79a192f671a9068a72a34bdcc3a09270ecc"
    )


def test_exact_match_only_and_not_found() -> None:
    bundle, _ = _capture([[dict(_order(), clientOrderId=CLIENT_ORDER_ID + "-other")]])
    assert bundle.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_NOT_FOUND
    assert bundle.exact_match_count == 0


def test_duplicate_and_conflict() -> None:
    duplicate, _ = _capture([[_order(), _order(orderId=702345678901234568)]])
    assert duplicate.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_DUPLICATE
    assert duplicate.exact_match_count == 2
    conflict, _ = _capture([[_order(side="SELL")]])
    assert conflict.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_CONFLICT
    update_only, _ = _capture([[_order(time=None)]])
    assert update_only.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_EXACTLY_RECORDED


def test_pagination_stops_on_short_page_and_caps_at_five() -> None:
    full = [dict(_order(), clientOrderId=f"other-{i}") for i in range(100)]
    stopped, transport = _capture([full, []])
    assert stopped.history_requests_attempted == 2
    assert stopped.inspected_row_count == 100
    assert len(transport.calls) == 3
    capped, transport = _capture([full] * 5)
    assert capped.history_requests_attempted == 5
    assert capped.inspected_row_count == 500
    assert len(transport.calls) == 6
    assert capped.terminal_status == ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_NOT_FOUND


def test_current_position_is_preserved_without_cleanup() -> None:
    from test_weex_forward_evidence import _position

    bundle, transport = _capture([[]], positions=[_position()])
    assert bundle.current_position_present
    assert len(bundle.current_btcsusdt_positions) == 1
    assert bundle.terminal_status == ReconciliationStatus.DEMO_RECONCILIATION_UNAVAILABLE
    assert len(transport.calls) == 2
    assert all(path in forward.DEMO_GET_PATHS for path, _ in transport.calls)
    assert not bundle.state_changing_request_performed


def test_malformed_history_fails_closed_without_serializing_raw_payload() -> None:
    bundle, _ = _capture([[{"symbol": "BTCSUSDT", "orderId": "private-order"}]])
    assert bundle.terminal_status == ReconciliationStatus.DEMO_RECONCILIATION_UNAVAILABLE
    assert bundle.history_requests_attempted == 1
    assert bundle.history_pages == ()
    assert "private-order" not in bundle.to_json()


def test_raw_mutation_of_sealed_bundle_fails() -> None:
    bundle, _ = _capture([[json.loads(json.dumps(_order()))]])
    with pytest.raises(ReconciliationError, match="bundle identity mismatch"):
        replace(bundle, inspected_row_count=2).validate()


def test_signed_client_rejects_live_and_write_routes_before_io() -> None:
    transport = FakeGetTransport([[]])
    client = forward.WeexDemoReadOnlyClient(
        forward.WeexDemoCredentials(
            forward.SecretValue("fake-api-key"),
            forward.SecretValue("fake-secret-key"),
            forward.SecretValue("fake-passphrase"),
        ),
        transport,
    )
    for method, path in (
        ("POST", "/capi/v3/sim/order"),
        ("GET", "/capi/v3/order/history"),
        ("POST", forward.DEMO_ORDER_HISTORY_PATH),
    ):
        with pytest.raises(forward.UnsafeWeexRouteError):
            client.request(method, path)
    assert not transport.calls
