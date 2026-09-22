from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from urllib.parse import parse_qs

import pytest

import adaptive_trading_bot.research.weex_demo_feed_mapping as mapping_module
from adaptive_trading_bot.research import weex_forward_evidence as forward
from adaptive_trading_bot.research.weex_demo_calibration import (
    DemoCalibrationIntegrityError,
    WeexDemoLongCalibrationRunner,
)
from adaptive_trading_bot.research.weex_demo_feed_mapping import (
    DEMO_ORDER_DOC_URL,
    DEMO_RULES_URL,
    DEMO_STRATEGY_GUIDE_URL,
    PREMIUM_INDEX_DOC_URL,
    SYMBOL_PRICE_DOC_URL,
    DemoFeedExcludedInference,
    DemoFeedFact,
    DemoFeedMapping,
    DemoFeedMappingBundle,
    DemoFeedMappingIntegrityError,
    DemoFeedMappingRequestError,
    DemoFeedMappingResolver,
    DemoFeedMappingStatus,
    LockedWeexDemoFeedMappingBundle,
    UrlLibDocumentTransport,
    WeexDemoFeedMappingCollector,
    write_mapping_bundle,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

CAPTURED_AT = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
CODE_SHA = "1" * 40


class _Documents:
    def __init__(self) -> None:
        self.content = {
            DEMO_ORDER_DOC_URL: b"POST /capi/v3/sim/order trading pair BTCSUSDT",
            DEMO_RULES_URL: (
                b"SUSDT is the simulated stablecoin used to quote WEEX demo futures pairs "
                b"such as BTC/SUSDT. It tracks the live USDT-quoted market price"
            ),
            DEMO_STRATEGY_GUIDE_URL: b"the same live price and funding feed",
            SYMBOL_PRICE_DOC_URL: b"/capi/v3/market/symbolPrice priceType MARK",
            PREMIUM_INDEX_DOC_URL: (
                b"/capi/v3/market/premiumIndex markPrice indexPrice lastFundingRate "
                b"forecastFundingRate nextFundingTime collectCycle"
            ),
        }

    def get(self, url: str) -> bytes:
        return self.content[url]


class _PublicTransport:
    def __init__(self, *, direct_demo: bool = False, missing_mark: bool = False) -> None:
        self.direct_demo = direct_demo
        self.missing_mark = missing_mark
        self.calls: list[tuple[str, str, str, Mapping[str, str]]] = []

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object:
        self.calls.append((base_url, path, query, headers))
        symbol = parse_qs(query)["symbol"][0]
        if symbol == "BTCSUSDT" and not self.direct_demo:
            raise forward.WeexForwardRequestError("unavailable")
        if path == forward.SYMBOL_PRICE_PATH:
            if self.missing_mark and symbol == "BTCUSDT":
                raise forward.WeexForwardRequestError("unavailable")
            return {
                "data": {
                    "symbol": symbol,
                    "price": "65000.125" if symbol == "BTCUSDT" else "65001.000",
                    "time": 1790035200123,
                }
            }
        if path == forward.PREMIUM_INDEX_PATH:
            return {
                "data": [
                    {
                        "symbol": symbol,
                        "markPrice": "65000.100",
                        "indexPrice": "64999.875",
                        "lastFundingRate": "0.0001",
                        "forecastFundingRate": "-0.000025",
                        "interestRate": "0.0003",
                        "nextFundingTime": 1790038800000,
                        "time": 1790035200000,
                        "collectCycle": 60,
                    }
                ]
            }
        raise AssertionError("unexpected route")


def _capture(*, transport: _PublicTransport | None = None) -> DemoFeedMappingBundle:
    return WeexDemoFeedMappingCollector(
        forward.WeexPublicCurrentClient(transport or _PublicTransport()), _Documents()
    ).capture(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)


def _locked_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    DemoFeedMappingBundle,
    LockedWeexDemoFeedMappingBundle,
    DemoFeedMappingResolver,
]:
    bundle = _capture()
    root = tmp_path / bundle.bundle_version
    write_mapping_bundle(bundle, root)
    manifest_sha = mapping_module._file_sha256(root / "manifest.json")
    monkeypatch.setattr(
        mapping_module,
        "LOCKED_DEMO_FEED_MAPPING_MANIFEST_SHA256",
        MappingProxyType({bundle.bundle_version: manifest_sha}),
    )
    locked = LockedWeexDemoFeedMappingBundle(root, bundle_version=bundle.bundle_version)
    return bundle, locked, DemoFeedMappingResolver(locked)


def test_capture_qualifies_only_exact_current_feed_facts() -> None:
    bundle = _capture()

    assert bundle.statuses == (
        DemoFeedMappingStatus.DEMO_FEED_DOCS_QUALIFIED,
        DemoFeedMappingStatus.BTCUSDT_MARK_REFERENCE_AVAILABLE,
        DemoFeedMappingStatus.BTCUSDT_FUNDING_REFERENCE_AVAILABLE,
        DemoFeedMappingStatus.DEMO_MARK_FEED_MAPPING_QUALIFIED,
        DemoFeedMappingStatus.DEMO_FUNDING_FEED_MAPPING_QUALIFIED,
    )
    assert bundle.mapping.demo_symbol == "BTCSUSDT"
    assert bundle.mapping.reference_symbol == "BTCUSDT"
    assert bundle.mapping.authorized_facts == tuple(DemoFeedFact)
    assert bundle.mapping.excluded_inferences == tuple(DemoFeedExcludedInference)
    assert bundle.mapping.contract_mechanics_qualified is False
    assert bundle.mapping.production_equivalence_claimed is False
    assert bundle.mapping.historical_authority_claimed is False
    assert bundle.reference_mark is not None
    assert bundle.reference_premium is not None
    assert isinstance(bundle.reference_mark.price, Decimal)
    assert isinstance(bundle.reference_premium.last_funding_rate, Decimal)
    assert bundle.exact_mark_delta == Decimal("0.025")
    assert bundle.mark_observation_time_delta_ms == 123
    assert bundle.reference_mark.venue_time == datetime.fromtimestamp(1790035200.123, tz=UTC)


@pytest.mark.parametrize(
    ("demo_symbol", "reference_symbol"),
    (("ETHSUSDT", "ETHUSDT"), ("BTCSUSDT", "ETHUSDT"), ("BTCUSDT", "BTCUSDT")),
)
def test_arbitrary_or_non_btc_mapping_is_rejected(demo_symbol: str, reference_symbol: str) -> None:
    mapping = DemoFeedMapping(
        demo_symbol=demo_symbol,
        reference_symbol=reference_symbol,
        evidence_version="weex-demo-feed-mapping-test",
        authorized_facts=(DemoFeedFact.MARK_PRICE,),
        excluded_inferences=tuple(DemoFeedExcludedInference),
        contract_mechanics_qualified=False,
        production_equivalence_claimed=False,
        historical_authority_claimed=False,
    )
    with pytest.raises(DemoFeedMappingIntegrityError, match="only the reviewed"):
        mapping.validate()


@pytest.mark.parametrize(
    "field",
    (
        "contract_mechanics_qualified",
        "production_equivalence_claimed",
        "historical_authority_claimed",
    ),
)
def test_mapping_cannot_claim_mechanics_or_general_authority(field: str) -> None:
    values = {
        "demo_symbol": "BTCSUSDT",
        "reference_symbol": "BTCUSDT",
        "evidence_version": "weex-demo-feed-mapping-test",
        "authorized_facts": tuple(DemoFeedFact),
        "excluded_inferences": tuple(DemoFeedExcludedInference),
        "contract_mechanics_qualified": False,
        "production_equivalence_claimed": False,
        "historical_authority_claimed": False,
    }
    values[field] = True
    with pytest.raises(DemoFeedMappingIntegrityError, match="forbidden authority"):
        DemoFeedMapping(**values).validate()  # type: ignore[arg-type]


def test_resolver_preserves_mapped_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _, resolver = _locked_resolver(tmp_path, monkeypatch)
    transport = _PublicTransport()
    mark = resolver.resolve_mark(forward.WeexPublicCurrentClient(transport))
    funding = resolver.resolve_funding(forward.WeexPublicCurrentClient(transport))

    assert mark is not None and funding is not None
    assert mark.requested_demo_symbol == "BTCSUSDT"
    assert mark.actual_source_symbol == "BTCUSDT"
    assert mark.mapping_evidence_version == bundle.bundle_version
    assert funding.requested_demo_symbol == "BTCSUSDT"
    assert funding.actual_source_symbol == "BTCUSDT"
    assert funding.mapping_evidence_version == bundle.bundle_version
    assert dict(mark.source_observation.parameters) == {
        "priceType": "MARK",
        "symbol": "BTCUSDT",
    }


def test_direct_demo_mark_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, resolver = _locked_resolver(tmp_path, monkeypatch)
    transport = _PublicTransport(direct_demo=True)
    mark = resolver.resolve_mark(forward.WeexPublicCurrentClient(transport))

    assert mark is not None
    assert mark.actual_source_symbol == "BTCSUSDT"
    assert mark.mapping_evidence_version is None
    assert len(transport.calls) == 1
    assert parse_qs(transport.calls[0][2])["symbol"] == ["BTCSUSDT"]


def test_missing_reference_mark_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, resolver = _locked_resolver(tmp_path, monkeypatch)
    assert (
        resolver.resolve_mark(forward.WeexPublicCurrentClient(_PublicTransport(missing_mark=True)))
        is None
    )


def test_missing_document_proposition_blocks_mapping() -> None:
    documents = _Documents()
    documents.content = dict(documents.content)
    documents.content[DEMO_STRATEGY_GUIDE_URL] = b"same terminal only"
    bundle = WeexDemoFeedMappingCollector(
        forward.WeexPublicCurrentClient(_PublicTransport()), documents
    ).capture(captured_at=CAPTURED_AT, source_code_commit=CODE_SHA)

    assert DemoFeedMappingStatus.DEMO_FEED_DOCS_UNQUALIFIED in bundle.statuses
    assert DemoFeedMappingStatus.DEMO_FEED_MAPPING_BLOCKED in bundle.statuses
    assert bundle.mapping.authorized_facts == ()


def test_locked_reader_rejects_unknown_and_tampered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _capture()
    root = tmp_path / bundle.bundle_version
    write_mapping_bundle(bundle, root)
    with pytest.raises(CorpusManifestError, match="not project-qualified"):
        LockedWeexDemoFeedMappingBundle(root, bundle_version=bundle.bundle_version)

    manifest_sha = mapping_module._file_sha256(root / "manifest.json")
    monkeypatch.setattr(
        mapping_module,
        "LOCKED_DEMO_FEED_MAPPING_MANIFEST_SHA256",
        MappingProxyType({bundle.bundle_version: manifest_sha}),
    )
    locked = LockedWeexDemoFeedMappingBundle(root, bundle_version=bundle.bundle_version)
    assert locked.read() == bundle
    artifact = root / "bundle.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["exact_mark_delta"] = "999"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusHashMismatchError, match="artifact hash mismatch"):
        locked.read()


def test_calibration_precondition_uses_mapped_mark_with_explicit_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, locked, _ = _locked_resolver(tmp_path, monkeypatch)
    runner = WeexDemoLongCalibrationRunner(
        public_client=forward.WeexPublicCurrentClient(_PublicTransport()),
        demo_reader=None,
        demo_writer=None,
        feed_mapping_bundle=locked,
    )

    mark, observation = runner._probe_mark()
    premium, premium_observation = runner._probe_premium()

    assert mark is not None and observation is not None
    assert mark.symbol == "BTCSUSDT"
    assert mark.actual_source_symbol == "BTCUSDT"
    assert mark.mapping_evidence_version == bundle.bundle_version
    assert dict(observation.parameters) == {"priceType": "MARK", "symbol": "BTCUSDT"}
    assert premium is not None and premium_observation is not None
    assert premium.actual_source_symbol == "BTCUSDT"
    assert premium.collect_cycle_minutes == 60


def test_forged_mapped_calibration_provenance_is_rejected() -> None:
    with pytest.raises(DemoCalibrationIntegrityError, match="mapped MARK provenance"):
        from adaptive_trading_bot.research.weex_demo_calibration import DemoSymbolMarkEvidence

        DemoSymbolMarkEvidence(
            symbol="BTCSUSDT",
            price=Decimal("1"),
            venue_time_ms=1790035200123,
            venue_time=datetime.fromtimestamp(1790035200.123, tz=UTC),
            actual_source_symbol="BTCUSDT",
            mapping_evidence_version=None,
        )


def test_document_transport_rejects_unreviewed_url_before_io() -> None:
    with pytest.raises(DemoFeedMappingRequestError, match="not reviewed"):
        UrlLibDocumentTransport().get("https://example.com/not-reviewed")


def test_mapping_capture_uses_only_public_get_routes() -> None:
    transport = _PublicTransport()
    _capture(transport=transport)

    assert {(path, parse_qs(query)["symbol"][0]) for _, path, query, _ in transport.calls} == {
        (forward.PREMIUM_INDEX_PATH, "BTCUSDT"),
        (forward.SYMBOL_PRICE_PATH, "BTCUSDT"),
    }
    assert all(
        not headers.keys() & {"ACCESS-KEY", "ACCESS-SIGN"} for *_, headers in transport.calls
    )


def test_existing_locked_evidence_manifest_hashes_are_unchanged() -> None:
    expected = {
        Path("data/research_corpus/weex-v1-5m-last-a629a29b580b2e103adba918/manifest.json"): (
            "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
        ),
        Path("data/research_inputs/weex-v1-5m-mark-97c879a06fc900676dc5c339/manifest.json"): (
            "3e5ed9bc81877fa9c1d45fe33145d82f36487afeb5e49e9502ae087ec0749275"
        ),
        Path("data/research_inputs/weex-v1-funding-87c1f6e88bd64d96c015fe39/manifest.json"): (
            "6ecc0c5717a0ec3e5c0c28a96c542226a48e007ccdda48934e5900d304746c52"
        ),
        Path("data/research_evidence/weex-v3-forward-b88af4b81510850232656be7/manifest.json"): (
            "a344bf9cf726b8b68070637bca1c7971eaf248bf3e56585e4b5acd712b9088ca"
        ),
        Path(
            "data/research_calibration/weex-demo-calibration-e300b52fe0e43bfe8581ca58/manifest.json"
        ): "025159eb178a9e036c3fb36b531a2210cc45c5abfb7d66b153477521274285b6",
        Path(
            "data/research_calibration/weex-demo-calibration-d7eb4338f33b986cc54b1cb6/manifest.json"
        ): "6119d7a1b7fca8624dfdb6c74b643fc46521e1e6badbd9f4b1e2d82ec8fd20f2",
    }
    for path, digest in expected.items():
        assert mapping_module._file_sha256(path) == digest


def test_copy_of_locked_bundle_has_no_network_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _capture()
    source = tmp_path / "source"
    write_mapping_bundle(bundle, source)
    target = tmp_path / "detached"
    shutil.copytree(source, target)
    monkeypatch.setattr(
        mapping_module,
        "LOCKED_DEMO_FEED_MAPPING_MANIFEST_SHA256",
        MappingProxyType(
            {bundle.bundle_version: mapping_module._file_sha256(target / "manifest.json")}
        ),
    )
    assert (
        LockedWeexDemoFeedMappingBundle(target, bundle_version=bundle.bundle_version).read()
        == bundle
    )
