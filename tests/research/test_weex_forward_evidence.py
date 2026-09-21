from __future__ import annotations

import base64
import hmac
import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest

import adaptive_trading_bot.research.weex_forward_evidence as evidence_module
from adaptive_trading_bot.research.weex_forward_evidence import (
    DEMO_BALANCE_PATH,
    DEMO_ORDER_HISTORY_PATH,
    DEMO_POSITIONS_PATH,
    EXCHANGE_INFO_PATH,
    PREMIUM_INDEX_PATH,
    SYMBOL_PRICE_PATH,
    ForwardEvidenceIntegrityError,
    ForwardEvidenceStatus,
    LockedWeexForwardEvidenceBundle,
    SecretValue,
    UnsafeWeexRouteError,
    WeexDemoCredentials,
    WeexDemoReadOnlyClient,
    WeexForwardEvidenceBundle,
    WeexForwardEvidenceCollector,
    WeexPublicCurrentClient,
    sign_weex_get,
    write_evidence_bundle,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

CAPTURED_AT = datetime(2026, 9, 21, 12, tzinfo=UTC)
CODE_SHA = "1" * 40


class RecordingGetTransport:
    def __init__(
        self,
        *,
        position_overrides: dict[str, object] | None = None,
        malformed_position: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.position_overrides = position_overrides or {}
        self.malformed_position = malformed_position

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object:
        self.calls.append((path, query, dict(headers)))
        parameters = dict(item.split("=", 1) for item in query.split("&") if item)
        symbol = parameters.get("symbol", "BTCUSDT")
        if path == EXCHANGE_INFO_PATH:
            return {"symbols": [_contract("BTCUSDT"), _contract("ETHUSDT")]}
        if path == PREMIUM_INDEX_PATH:
            return {"data": [_premium(symbol)]}
        if path == SYMBOL_PRICE_PATH:
            return {"data": {"symbol": symbol, "price": "65000.125", "time": 1789992000123}}
        if path == DEMO_BALANCE_PATH:
            return [
                {
                    "asset": "SUSDT",
                    "balance": "10000.125",
                    "availableBalance": "9900.125",
                    "frozen": "100.000",
                    "unrealizePnl": "-12.250",
                }
            ]
        if path == DEMO_POSITIONS_PATH:
            item = _position()
            item.update(self.position_overrides)
            if self.malformed_position:
                del item["leverage"]
            return [item]
        if path == DEMO_ORDER_HISTORY_PATH:
            return [_order()]
        raise AssertionError(f"unexpected test route {path}")


def _contract(symbol: str) -> dict[str, object]:
    base = "BTC" if symbol == "BTCUSDT" else "ETH"
    return {
        "symbol": symbol,
        "baseAsset": base,
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "contractType": "PERPETUAL",
        "contractVal": "0.0001" if base == "BTC" else "0.001",
        "minLeverage": 1,
        "maxLeverage": 400,
        "pricePrecision": 1 if base == "BTC" else 2,
        "quantityPrecision": 4 if base == "BTC" else 3,
        "minOrderSize": "0.0001" if base == "BTC" else "0.001",
        "maxOrderSize": "1200",
        "maxPositionSize": "1000000",
        "marketOpenLimitSize": "100",
        "makerFeeRate": "0.0002",
        "takerFeeRate": "0.0008",
        "apiMakerFeeRate": "0.00015",
        "apiTakerFeeRate": "0.0006",
    }


def _premium(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "markPrice": "65000.125",
        "indexPrice": "64999.875",
        "lastFundingRate": "0.0001",
        "forecastFundingRate": "-0.000025",
        "interestRate": "0.00001",
        "nextFundingTime": 1789996800000,
        "time": 1789992000123,
        "collectCycle": 480,
    }


def _position() -> dict[str, object]:
    return {
        "id": 689987235755328154,
        "asset": "SUSDT",
        "symbol": "BTCSUSDT",
        "side": "LONG",
        "marginType": "ISOLATED",
        "separatedMode": "SEPARATED",
        "separatedOpenOrderId": 0,
        "leverage": "10",
        "size": "0.020000",
        "openValue": "1801.0670000",
        "openFee": "0.70731060",
        "fundingFee": "-1.22618160",
        "marginSize": "180.106700",
        "isolatedMargin": "180.106700",
        "isAutoAppendIsolatedMargin": False,
        "cumOpenSize": "0.020000",
        "cumOpenValue": "1801.0670000",
        "cumOpenFee": "0.70731060",
        "cumCloseSize": "0",
        "cumCloseValue": "0",
        "cumCloseFee": "0",
        "cumFundingFee": "-1.22618160",
        "cumLiquidateFee": "0",
        "createdMatchSequenceId": 7027745116,
        "updatedMatchSequenceId": 7040274110,
        "createdTime": 1764505776347,
        "updatedTime": 1764588886461,
        "unrealizePnl": "-85.5690000",
        "liquidatePrice": "81000.25",
    }


def _order() -> dict[str, object]:
    return {
        "orderId": 702345678901234567,
        "symbol": "BTCSUSDT",
        "side": "BUY",
        "positionSide": "LONG",
        "status": "FILLED",
        "type": "LIMIT",
        "time": 1764400000000,
        "price": "65000",
        "origQty": "0.01",
        "executedQty": "0.01",
        "avgPrice": "64999.5",
        "updateTime": 1764400001234,
        "clientOrderId": "hist-1",
        "cumQuote": "649.995",
        "timeInForce": "GTC",
    }


def _credentials() -> WeexDemoCredentials:
    return WeexDemoCredentials(
        SecretValue("fake-api-key"),
        SecretValue("fake-secret-key"),
        SecretValue("fake-passphrase"),
    )


def _capture(
    *, transport: RecordingGetTransport | None = None, with_demo: bool = True
) -> tuple[WeexForwardEvidenceBundle, RecordingGetTransport]:
    selected = transport or RecordingGetTransport()
    public = WeexPublicCurrentClient(selected)
    demo = (
        WeexDemoReadOnlyClient(_credentials(), selected, timestamp_ms=lambda: 1789992000000)
        if with_demo
        else None
    )
    return (
        WeexForwardEvidenceCollector(public, demo).capture(
            captured_at=CAPTURED_AT, source_code_commit=CODE_SHA
        ),
        selected,
    )


def test_exact_decimal_projection_and_demo_identifier_sanitization() -> None:
    bundle, _ = _capture()
    contract = bundle.public_contracts[0]
    premium = bundle.premium_indices[0]
    position = bundle.demo_positions[0]
    order = bundle.demo_order_history[0]

    assert contract.contract_val == Decimal("0.0001")
    assert contract.api_maker_fee_rate == Decimal("0.00015")
    assert premium.forecast_funding_rate == Decimal("-0.000025")
    assert position.open_value == Decimal("1801.0670000")
    assert position.funding_fee == Decimal("-1.22618160")
    assert order.average_price == Decimal("64999.5")
    serialized = bundle.to_json()
    assert "689987235755328154" not in serialized
    assert "702345678901234567" not in serialized
    assert "hist-1" not in serialized
    assert '"open_value":"1801.0670000"' in serialized


def test_signature_canonicalization_uses_only_fake_secret() -> None:
    secret = SecretValue("fake-secret")
    actual = sign_weex_get(
        secret,
        timestamp="1789992000000",
        path=DEMO_ORDER_HISTORY_PATH,
        query="limit=100&page=0",
    )
    message = "1789992000000GET/capi/v3/sim/order/history?limit=100&page=0"
    expected = base64.b64encode(
        hmac.new(b"fake-secret", message.encode(), digestmod="sha256").digest()
    ).decode()
    assert actual == expected


def test_credentials_are_runtime_only_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = _credentials()
    representation = repr(credentials)
    assert "fake-api-key" not in representation
    assert "fake-secret-key" not in representation
    assert "fake-passphrase" not in representation
    assert "redacted" in representation
    with pytest.raises(ForwardEvidenceIntegrityError, match="credentials cannot"):
        evidence_module._canonical_value(credentials)

    for name in (
        "ATB_WEEX_DEMO_API_KEY",
        "ATB_WEEX_DEMO_SECRET_KEY",
        "ATB_WEEX_DEMO_PASSPHRASE",
    ):
        monkeypatch.delenv(name, raising=False)
    assert WeexDemoCredentials.from_environment() is None


@pytest.mark.parametrize(
    ("client_kind", "method", "path"),
    (
        ("public", "POST", EXCHANGE_INFO_PATH),
        ("public", "GET", "/capi/v3/order"),
        ("demo", "POST", "/capi/v3/sim/order"),
        ("demo", "GET", "/capi/v3/order"),
        ("demo", "POST", "/capi/v3/account/leverage"),
        ("demo", "POST", "/capi/v3/account/marginType"),
        ("demo", "POST", "/capi/v3/asset/transfer"),
        ("demo", "GET", "/capi/v3/sim/arbitrary"),
    ),
)
def test_unapproved_and_mutating_routes_fail_before_network(
    client_kind: str, method: str, path: str
) -> None:
    transport = RecordingGetTransport()
    client = (
        WeexPublicCurrentClient(transport)
        if client_kind == "public"
        else WeexDemoReadOnlyClient(_credentials(), transport)
    )
    with pytest.raises(UnsafeWeexRouteError):
        client.request(method, path)
    assert transport.calls == []


def test_only_reviewed_public_and_demo_gets_are_performed() -> None:
    bundle, transport = _capture()
    paths = [call[0] for call in transport.calls]
    assert set(paths) == {
        EXCHANGE_INFO_PATH,
        PREMIUM_INDEX_PATH,
        SYMBOL_PRICE_PATH,
        DEMO_BALANCE_PATH,
        DEMO_POSITIONS_PATH,
        DEMO_ORDER_HISTORY_PATH,
    }
    assert bundle.statuses[-1] is ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED
    assert bundle.demo_symbol_mapping_status is (
        ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED
    )


def test_no_credentials_is_explicit_and_does_not_call_demo() -> None:
    bundle, transport = _capture(with_demo=False)
    assert ForwardEvidenceStatus.DEMO_CREDENTIALS_UNAVAILABLE in bundle.statuses
    assert bundle.demo_balances == ()
    assert bundle.demo_positions == ()
    assert bundle.demo_order_history == ()
    assert not any(path.startswith("/capi/v3/sim/") for path, _, _ in transport.calls)


def test_isolated_position_is_eligible_but_symbol_mapping_remains_unresolved() -> None:
    bundle, _ = _capture()
    position = bundle.demo_positions[0]
    assert position.eligibility_status is (ForwardEvidenceStatus.DEMO_POSITION_EVIDENCE_QUALIFIED)
    assert position.rejection_reasons == ()
    assert position.symbol == "BTCSUSDT"
    assert bundle.demo_symbol_mapping_status is (
        ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    (
        (
            {"marginType": "CROSSED", "isolatedMargin": "0"},
            ForwardEvidenceStatus.DEMO_POSITION_REJECTED_NOT_ISOLATED,
        ),
        (
            {"isAutoAppendIsolatedMargin": True},
            ForwardEvidenceStatus.DEMO_POSITION_REJECTED_AUTO_MARGIN,
        ),
    ),
)
def test_ineligible_demo_positions_are_typed_rejections(
    overrides: dict[str, object], expected: ForwardEvidenceStatus
) -> None:
    bundle, _ = _capture(transport=RecordingGetTransport(position_overrides=overrides))
    assert bundle.demo_positions[0].eligibility_status is expected
    assert expected in bundle.demo_positions[0].rejection_reasons
    assert ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED in bundle.statuses


@pytest.mark.parametrize(
    "transport",
    (
        RecordingGetTransport(malformed_position=True),
        RecordingGetTransport(position_overrides={"leverage": "NaN"}),
        RecordingGetTransport(position_overrides={"size": 0.02}),
        RecordingGetTransport(position_overrides={"cumCloseSize": "0.03"}),
    ),
)
def test_malformed_demo_position_fails_closed(transport: RecordingGetTransport) -> None:
    with pytest.raises((ForwardEvidenceIntegrityError, KeyError)):
        _capture(transport=transport)


def test_bundle_is_deterministic_sealed_and_tamper_evident() -> None:
    first, _ = _capture()
    second, _ = _capture()
    assert first.bundle_version == second.bundle_version
    assert first.integrity_seal == second.integrity_seal
    assert first.to_json() == second.to_json()

    mutated = deepcopy(first)
    object.__setattr__(mutated.current_marks[0], "price", Decimal("1"))
    with pytest.raises(ForwardEvidenceIntegrityError, match=r"identity|seal mismatch"):
        mutated.validate()


def test_locked_reader_verifies_version_hashes_and_never_has_network_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _capture(with_demo=False)
    root = tmp_path / bundle.bundle_version
    manifest = write_evidence_bundle(bundle, root)
    manifest_sha = evidence_module._file_sha256(root / "manifest.json")
    monkeypatch.setattr(
        evidence_module,
        "QUALIFIED_FORWARD_EVIDENCE_MANIFEST_SHA256",
        MappingProxyType({bundle.bundle_version: manifest_sha}),
    )

    reader = LockedWeexForwardEvidenceBundle(root, bundle_version=bundle.bundle_version)
    assert reader.manifest == manifest
    assert reader.read() == bundle
    assert not hasattr(reader, "request")

    with pytest.raises(CorpusManifestError, match="unknown"):
        LockedWeexForwardEvidenceBundle(root, bundle_version="weex-v3-forward-wrong")

    artifact = root / "bundle.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(CorpusHashMismatchError, match="artifact hash mismatch"):
        reader.read()


def test_bundle_raw_mutation_of_status_fails_seal() -> None:
    bundle, _ = _capture(with_demo=False)
    object.__setattr__(
        bundle,
        "statuses",
        (
            ForwardEvidenceStatus.PUBLIC_CURRENT_EVIDENCE_QUALIFIED,
            ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED,
        ),
    )
    with pytest.raises(ForwardEvidenceIntegrityError, match=r"identity|seal mismatch"):
        bundle.validate()


def test_binary_float_economic_input_is_rejected() -> None:
    class FloatTransport(RecordingGetTransport):
        def get(
            self,
            *,
            base_url: str,
            path: str,
            query: str,
            headers: Mapping[str, str],
        ) -> object:
            payload = super().get(base_url=base_url, path=path, query=query, headers=headers)
            if path == SYMBOL_PRICE_PATH:
                envelope = cast(dict[str, object], payload)
                data = cast(dict[str, object], envelope["data"])
                data["price"] = 65000.125
            return payload

    with pytest.raises(ForwardEvidenceIntegrityError, match="binary float"):
        _capture(transport=FloatTransport(), with_demo=False)


def test_source_observations_and_timestamps_are_canonical() -> None:
    bundle, _ = _capture(with_demo=False)
    assert bundle.source_observations == tuple(
        sorted(bundle.source_observations, key=lambda item: (item.endpoint, item.parameters))
    )
    for premium in bundle.premium_indices:
        assert int(premium.venue_time.timestamp() * 1000) == premium.venue_time_ms
    parsed = json.loads(bundle.to_json())
    assert parsed["historical_authority_claimed"] is False
    assert "historical applicability is asserted" in parsed["point_in_time_scope"]
