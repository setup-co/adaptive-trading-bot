from __future__ import annotations

import base64
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import cast

import pytest

import adaptive_trading_bot.research.weex_demo_calibration as calibration_module
from adaptive_trading_bot.research import weex_forward_evidence as forward
from adaptive_trading_bot.research.weex_demo_calibration import (
    CLEANUP_CLIENT_ORDER_ID,
    DEMO_ORDER_PATH,
    OPEN_CLIENT_ORDER_ID,
    CalibrationDisposition,
    CalibrationOrderPhase,
    DemoCalibrationIntegrityError,
    DemoCalibrationOrder,
    DemoCalibrationRequestError,
    DemoCalibrationStatus,
    LockedWeexDemoCalibrationBundle,
    UnsafeDemoCalibrationOperation,
    WeexDemoCalibrationWriter,
    WeexDemoLongCalibrationRunner,
    _UrlLibDemoOrderPostTransport,
    sign_demo_order_post,
    write_calibration_bundle,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

CAPTURED_AT = datetime(2026, 9, 21, 12, tzinfo=UTC)
CODE_SHA = "1" * 40


def _credentials() -> forward.WeexDemoCredentials:
    return forward.WeexDemoCredentials(
        forward.SecretValue("demo-key"),
        forward.SecretValue("demo-secret"),
        forward.SecretValue("demo-passphrase"),
    )


class _Clock:
    def __init__(self) -> None:
        self.current = CAPTURED_AT
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class _ProtocolTransport:
    def __init__(self) -> None:
        self.position_open = False
        self.open_order_id = "712345678901234567"
        self.get_calls: list[tuple[str, str, dict[str, str]]] = []
        self.post_calls: list[tuple[str, bytes, dict[str, str]]] = []

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object:
        del base_url
        self.get_calls.append((path, query, dict(headers)))
        if path == forward.DEMO_BALANCE_PATH:
            return [
                {
                    "asset": "SUSDT",
                    "balance": "10000",
                    "availableBalance": "10000",
                    "frozen": "0",
                    "unrealizePnl": "0",
                }
            ]
        if path == forward.DEMO_POSITIONS_PATH:
            return [self._position()] if self.position_open else []
        if path == forward.DEMO_ORDER_HISTORY_PATH:
            return [self._order()] if self.position_open else []
        if path == forward.EXCHANGE_INFO_PATH:
            return {"data": {"symbols": [self._contract()]}}
        if path == forward.PREMIUM_INDEX_PATH:
            return {"data": [self._premium()]}
        if path == forward.SYMBOL_PRICE_PATH:
            return {
                "data": {
                    "symbol": "BTCSUSDT",
                    "price": "65000.125",
                    "time": 1789992000123,
                }
            }
        raise AssertionError(f"unexpected route {path}")

    def post(
        self,
        *,
        base_url: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> object:
        del base_url
        self.post_calls.append((path, body, dict(headers)))
        payload = json.loads(body)
        client_id = payload["newClientOrderId"]
        if client_id == OPEN_CLIENT_ORDER_ID:
            self.position_open = True
            order_id = self.open_order_id
        elif client_id == CLEANUP_CLIENT_ORDER_ID:
            self.position_open = False
            order_id = "712345678901234568"
        else:
            raise AssertionError("unexpected client order ID")
        return {
            "data": {
                "orderId": order_id,
                "clientOrderId": client_id,
                "success": True,
                "errorCode": "",
                "errorMessage": "",
            }
        }

    def _position(self) -> dict[str, object]:
        return {
            "id": 689987235755328154,
            "asset": "SUSDT",
            "symbol": "BTCSUSDT",
            "side": "LONG",
            "marginType": "ISOLATED",
            "separatedMode": "SEPARATED",
            "separatedOpenOrderId": self.open_order_id,
            "leverage": "10",
            "size": "0.01",
            "openValue": "650.00125",
            "openFee": "0.52",
            "fundingFee": "0",
            "marginSize": "65.000125",
            "isolatedMargin": "65.000125",
            "isAutoAppendIsolatedMargin": False,
            "cumOpenSize": "0.01",
            "cumOpenValue": "650.00125",
            "cumOpenFee": "0.52",
            "cumCloseSize": "0",
            "cumCloseValue": "0",
            "cumCloseFee": "0",
            "cumFundingFee": "0",
            "cumLiquidateFee": "0",
            "createdMatchSequenceId": 7027745116,
            "updatedMatchSequenceId": 7040274110,
            "createdTime": 1789992000000,
            "updatedTime": 1789992000123,
            "unrealizePnl": "0",
            "liquidatePrice": "59000",
        }

    def _order(self) -> dict[str, object]:
        return {
            "orderId": self.open_order_id,
            "symbol": "BTCSUSDT",
            "side": "BUY",
            "positionSide": "LONG",
            "status": "FILLED",
            "type": "MARKET",
            "time": 1789992000000,
            "price": "0",
            "origQty": "0.01",
            "executedQty": "0.01",
            "avgPrice": "65000.125",
            "updateTime": 1789992000123,
            "clientOrderId": OPEN_CLIENT_ORDER_ID,
            "cumQuote": "650.00125",
            "timeInForce": "IOC",
        }

    @staticmethod
    def _contract() -> dict[str, object]:
        return {
            "symbol": "BTCSUSDT",
            "baseAsset": "BTCS",
            "quoteAsset": "SUSDT",
            "marginAsset": "SUSDT",
            "contractType": "PERPETUAL",
            "contractVal": "0.0001",
            "minLeverage": "1",
            "maxLeverage": "400",
            "quantityPrecision": 4,
            "minOrderSize": "0.0001",
            "maxOrderSize": "100",
            "makerFeeRate": "0.0002",
            "takerFeeRate": "0.0008",
        }

    @staticmethod
    def _premium() -> dict[str, object]:
        return {
            "symbol": "BTCSUSDT",
            "markPrice": "65000.125",
            "indexPrice": "64999.875",
            "lastFundingRate": "0.0001",
            "forecastFundingRate": "-0.000025",
            "nextFundingTime": 1789996800000,
            "time": 1789992000123,
        }


def _runner(transport: _ProtocolTransport, clock: _Clock) -> WeexDemoLongCalibrationRunner:
    credentials = _credentials()
    reader = forward.WeexDemoReadOnlyClient(
        credentials, transport, timestamp_ms=lambda: 1789992000000
    )
    writer = WeexDemoCalibrationWriter(
        credentials,
        transport,
        timestamp_ms=lambda: 1789992000000,
        now=clock.now,
    )
    return WeexDemoLongCalibrationRunner(
        public_client=forward.WeexPublicCurrentClient(transport),
        demo_reader=reader,
        demo_writer=writer,
        now=clock.now,
        sleep=clock.sleep,
    )


def test_open_order_is_exact_and_signed_body_is_transmitted_unchanged() -> None:
    transport = _ProtocolTransport()
    writer = WeexDemoCalibrationWriter(
        _credentials(), transport, timestamp_ms=lambda: 1789992000000, now=lambda: CAPTURED_AT
    )
    order = DemoCalibrationOrder.opening()

    result = writer.submit_opening(order)

    path, body, headers = transport.post_calls[0]
    assert path == DEMO_ORDER_PATH
    assert body == order.canonical_body()
    message = b"1789992000000POST" + DEMO_ORDER_PATH.encode() + body
    expected = base64.b64encode(
        hmac.new(b"demo-secret", message, digestmod="sha256").digest()
    ).decode()
    assert headers["ACCESS-SIGN"] == expected
    assert (
        sign_demo_order_post(
            _credentials().secret_key,
            timestamp="1789992000000",
            canonical_body=body,
        )
        == expected
    )
    assert result.evidence.accepted


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("symbol", "BTCUSDT"),
        ("side", "SELL"),
        ("quantity", Decimal("0.02")),
        ("order_type", "LIMIT"),
        ("client_order_id", "adaptive-id"),
    ],
)
def test_forged_opening_order_is_rejected_before_io(change: str, value: object) -> None:
    fields: dict[str, object] = {
        "phase": CalibrationOrderPhase.OPEN,
        "symbol": "BTCSUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "order_type": "MARKET",
        "quantity": Decimal("0.01"),
        "client_order_id": OPEN_CLIENT_ORDER_ID,
        "observed_position_evidence_id": None,
    }
    fields[change] = value
    with pytest.raises(UnsafeDemoCalibrationOperation):
        DemoCalibrationOrder(**fields)  # type: ignore[arg-type]


def test_cleanup_must_match_exact_observed_position() -> None:
    transport = _ProtocolTransport()
    position = forward._parse_demo_positions([transport._position()])[0]
    order = DemoCalibrationOrder.cleanup(position)
    forged = object.__new__(DemoCalibrationOrder)
    for field, value in (
        ("phase", order.phase),
        ("symbol", order.symbol),
        ("side", order.side),
        ("position_side", order.position_side),
        ("order_type", order.order_type),
        ("quantity", Decimal("0.02")),
        ("client_order_id", order.client_order_id),
        ("observed_position_evidence_id", order.observed_position_evidence_id),
    ):
        object.__setattr__(forged, field, value)
    writer = WeexDemoCalibrationWriter(_credentials(), transport)
    with pytest.raises(UnsafeDemoCalibrationOperation):
        writer.submit_cleanup(forged, position=position)
    assert transport.post_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/capi/v3/order",
        "/capi/v3/account/leverage",
        "/capi/v3/account/marginType",
        "/capi/v3/asset/transfer",
        "/capi/v3/sim/order/closeAll",
        "/arbitrary",
    ],
)
def test_every_unapproved_write_route_is_rejected_before_io(path: str) -> None:
    transport = _UrlLibDemoOrderPostTransport()
    with pytest.raises(UnsafeDemoCalibrationOperation):
        transport.post(base_url="http://127.0.0.1:1", path=path, body=b"{}", headers={})


def test_missing_credentials_block_without_any_network() -> None:
    class ExplodingPublic:
        def request(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("network must not be reached")

    runner = WeexDemoLongCalibrationRunner(
        public_client=cast(forward.WeexPublicCurrentClient, ExplodingPublic()),
        demo_reader=None,
        demo_writer=None,
        now=lambda: CAPTURED_AT,
    )
    bundle = runner.run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert bundle.statuses == (
        DemoCalibrationStatus.DEMO_CREDENTIALS_UNAVAILABLE,
        DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED,
    )
    assert bundle.source_observations == ()
    assert bundle.open_submission is None
    assert bundle.cleanup_order is None


def test_complete_protocol_uses_one_open_one_cleanup_and_exact_wait() -> None:
    transport = _ProtocolTransport()
    clock = _Clock()
    bundle = _runner(transport, clock).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)

    assert [json.loads(call[1])["newClientOrderId"] for call in transport.post_calls] == [
        OPEN_CLIENT_ORDER_ID,
        CLEANUP_CLIENT_ORDER_ID,
    ]
    assert clock.sleeps == [65.0]
    assert bundle.statuses[-1] is DemoCalibrationStatus.DEMO_LONG_CALIBRATION_COMPLETE
    assert DemoCalibrationStatus.DEMO_SAMPLE_ELIGIBLE in bundle.statuses
    assert bundle.opening_order_attribution is CalibrationDisposition.MATCH
    assert bundle.cleanup_confirmed is True
    assert bundle.cleanup_order is not None
    assert bundle.cleanup_order.quantity == Decimal("0.01")
    assert bundle.demo_contract is not None
    assert not bundle.demo_contract.quantity_semantics_qualified
    assert any(
        comparison.disposition is CalibrationDisposition.UNRESOLVED
        for comparison in bundle.comparisons
    )
    serialized = bundle.to_json()
    assert "demo-key" not in serialized
    assert "demo-secret" not in serialized
    assert "demo-passphrase" not in serialized
    assert transport.open_order_id not in serialized


def test_dirty_baseline_blocks_before_public_probe_or_post() -> None:
    transport = _ProtocolTransport()
    transport.position_open = True
    clock = _Clock()
    bundle = _runner(transport, clock).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert DemoCalibrationStatus.DEMO_DIRTY_BASELINE_BLOCKED in bundle.statuses
    assert transport.post_calls == []
    assert all(path in forward.DEMO_GET_PATHS for path, _, _ in transport.get_calls)


def test_unqualified_demo_mark_blocks_before_post() -> None:
    class NoMarkTransport(_ProtocolTransport):
        def get(
            self,
            *,
            base_url: str,
            path: str,
            query: str,
            headers: Mapping[str, str],
        ) -> object:
            if path == forward.SYMBOL_PRICE_PATH:
                raise forward.WeexForwardRequestError("sanitized failure")
            return super().get(base_url=base_url, path=path, query=query, headers=headers)

    transport = NoMarkTransport()
    bundle = _runner(transport, _Clock()).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert DemoCalibrationStatus.DEMO_CALIBRATION_BLOCKED_NO_MARK in bundle.statuses
    assert transport.post_calls == []


def test_opening_is_never_retried_and_observation_is_bounded_to_five_seconds() -> None:
    class AcceptedButInvisibleTransport(_ProtocolTransport):
        def post(
            self,
            *,
            base_url: str,
            path: str,
            body: bytes,
            headers: Mapping[str, str],
        ) -> object:
            result = super().post(base_url=base_url, path=path, body=body, headers=headers)
            self.position_open = False
            return result

    transport = AcceptedButInvisibleTransport()
    clock = _Clock()
    bundle = _runner(transport, clock).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert len(transport.post_calls) == 1
    assert DemoCalibrationStatus.DEMO_OPEN_NOT_OBSERVED in bundle.statuses
    assert clock.sleeps == [2, 2]
    position_gets = [
        path for path, _, _ in transport.get_calls if path == forward.DEMO_POSITIONS_PATH
    ]
    assert len(position_gets) == 4  # one baseline plus three post-open observations


def test_rejected_opening_is_not_retried_or_adapted() -> None:
    class RejectedOrderTransport(_ProtocolTransport):
        def post(
            self,
            *,
            base_url: str,
            path: str,
            body: bytes,
            headers: Mapping[str, str],
        ) -> object:
            del base_url
            self.post_calls.append((path, body, dict(headers)))
            return {
                "data": {
                    "orderId": "712345678901234569",
                    "clientOrderId": OPEN_CLIENT_ORDER_ID,
                    "success": False,
                    "errorCode": "demo-secret",
                    "errorMessage": "demo-key demo-passphrase",
                }
            }

    transport = RejectedOrderTransport()
    bundle = _runner(transport, _Clock()).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert len(transport.post_calls) == 1
    assert json.loads(transport.post_calls[0][1])["quantity"] == "0.01"
    assert DemoCalibrationStatus.DEMO_OPEN_REJECTED in bundle.statuses
    assert bundle.statuses[-1] is DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED
    assert "demo-secret" not in bundle.to_json()
    assert "demo-key" not in bundle.to_json()
    assert "demo-passphrase" not in bundle.to_json()


def test_cleanup_is_never_retried_when_close_is_unconfirmed() -> None:
    class StickyPositionTransport(_ProtocolTransport):
        def post(
            self,
            *,
            base_url: str,
            path: str,
            body: bytes,
            headers: Mapping[str, str],
        ) -> object:
            result = super().post(base_url=base_url, path=path, body=body, headers=headers)
            if json.loads(body)["newClientOrderId"] == CLEANUP_CLIENT_ORDER_ID:
                self.position_open = True
            return result

    transport = StickyPositionTransport()
    clock = _Clock()
    bundle = _runner(transport, clock).run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    assert len(transport.post_calls) == 2
    assert DemoCalibrationStatus.DEMO_CLEANUP_UNCONFIRMED in bundle.statuses
    assert bundle.statuses[-1] is DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED
    assert clock.sleeps == [65.0, 2, 2]


def test_bundle_round_trip_tamper_and_unknown_version_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = WeexDemoLongCalibrationRunner(
        public_client=forward.WeexPublicCurrentClient(_ProtocolTransport()),
        demo_reader=None,
        demo_writer=None,
        now=lambda: CAPTURED_AT,
    )
    bundle = runner.run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    root = tmp_path / "bundle"
    manifest = write_calibration_bundle(bundle, root)
    manifest_sha = calibration_module._file_sha256(root / "manifest.json")
    monkeypatch.setattr(
        calibration_module,
        "QUALIFIED_DEMO_CALIBRATION_MANIFEST_SHA256",
        {bundle.bundle_version: manifest_sha},
    )
    assert (
        LockedWeexDemoCalibrationBundle(root, bundle_version=bundle.bundle_version).read() == bundle
    )
    with pytest.raises(CorpusManifestError):
        LockedWeexDemoCalibrationBundle(root, bundle_version="weex-demo-calibration-unknown")
    (root / "bundle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CorpusHashMismatchError):
        LockedWeexDemoCalibrationBundle(root, bundle_version=manifest.bundle_version)


def test_post_redirect_is_rejected_without_following_or_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_requests: list[str] = []
    origin_requests: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            target_requests.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target_thread = Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    target_url = f"http://127.0.0.1:{target.server_address[1]}/stolen-demo-secret"

    class OriginHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            origin_requests.append(self.path)
            self.send_response(307)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), OriginHandler)
    origin_thread = Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    monkeypatch.setattr(forward, "WEEX_V3_BASE_URL", f"http://127.0.0.1:{origin.server_address[1]}")
    writer = WeexDemoCalibrationWriter(
        _credentials(),
        _UrlLibDemoOrderPostTransport(timeout_seconds=2),
        timestamp_ms=lambda: 1789992000000,
    )
    try:
        with pytest.raises(DemoCalibrationRequestError) as captured:
            writer.submit_opening(DemoCalibrationOrder.opening())
    finally:
        origin.shutdown()
        target.shutdown()
        origin.server_close()
        target.server_close()
        origin_thread.join(timeout=2)
        target_thread.join(timeout=2)

    surfaced = str(captured.value)
    assert origin_requests == [DEMO_ORDER_PATH]
    assert target_requests == []
    assert "demo-key" not in surfaced
    assert "demo-secret" not in surfaced
    assert "demo-passphrase" not in surfaced
    assert "stolen-demo-secret" not in surfaced


def test_bundle_raw_mutation_invalidates_integrity_seal() -> None:
    runner = WeexDemoLongCalibrationRunner(
        public_client=forward.WeexPublicCurrentClient(_ProtocolTransport()),
        demo_reader=None,
        demo_writer=None,
        now=lambda: CAPTURED_AT,
    )
    bundle = runner.run(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)
    object.__setattr__(bundle, "source_code_commit", "2" * 40)
    with pytest.raises(DemoCalibrationIntegrityError, match=r"version|seal"):
        bundle.validate()
