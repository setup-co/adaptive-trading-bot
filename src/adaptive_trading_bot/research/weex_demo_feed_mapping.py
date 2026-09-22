"""Current-only WEEX Demo-to-public market-feed mapping evidence.

The authority in this module is deliberately narrow: one reviewed Demo symbol
may reference one public symbol for price/funding feed facts only.  It grants no
contract, execution, margin, leverage, liquidation, or historical authority.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from argparse import ArgumentParser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from http.client import HTTPMessage
from pathlib import Path
from types import MappingProxyType
from typing import IO, Final, Protocol, cast

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.research import weex_forward_evidence as forward
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

DEMO_SYMBOL: Final = "BTCSUSDT"
REFERENCE_SYMBOL: Final = "BTCUSDT"
SCHEMA_VERSION: Final = "weex-demo-feed-mapping-v1"
MANIFEST_SCHEMA_VERSION: Final = "weex-demo-feed-mapping-manifest-v1"

DEMO_ORDER_DOC_URL: Final = "https://www.weex.com/api-doc/contract/demo/PlaceOrder"
DEMO_RULES_URL: Final = (
    "https://www.weex.com/learn/articles/"
    "weex-demo-trading-50000-usdt-practice-account-rules-and-limits-ewyzdj1ds2vd4lu21nv5trn9"
)
DEMO_STRATEGY_GUIDE_URL: Final = (
    "https://www.weex.com/learn/articles/"
    "how-to-test-a-trading-strategy-on-weex-demo-before-going-live-e35zf87o4wbnnsbt2nkmxsau"
)
SYMBOL_PRICE_DOC_URL: Final = "https://www.weex.com/api-doc/contract/Market_API/GetSymbolPrice"
PREMIUM_INDEX_DOC_URL: Final = (
    "https://www.weex.com/api-doc/contract/Market_API/GetCurrentFundingRate"
)

# Review-owned registry. A new locked mapping requires an evidence PR.
LOCKED_DEMO_FEED_MAPPING_MANIFEST_SHA256: Final[Mapping[str, str]] = MappingProxyType({})

_CODE_SHA = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


class DemoFeedMappingError(ValueError):
    """Base failure for Demo feed-mapping evidence."""


class DemoFeedMappingRequestError(DemoFeedMappingError):
    """Sanitized read-only retrieval failure."""


class DemoFeedMappingIntegrityError(DemoFeedMappingError):
    """Malformed, inconsistent, or tampered mapping evidence."""


class DemoFeedMappingStatus(StrEnum):
    DEMO_FEED_DOCS_QUALIFIED = "DEMO_FEED_DOCS_QUALIFIED"
    DEMO_FEED_DOCS_UNQUALIFIED = "DEMO_FEED_DOCS_UNQUALIFIED"
    BTCUSDT_MARK_REFERENCE_AVAILABLE = "BTCUSDT_MARK_REFERENCE_AVAILABLE"
    BTCUSDT_MARK_REFERENCE_UNAVAILABLE = "BTCUSDT_MARK_REFERENCE_UNAVAILABLE"
    BTCUSDT_FUNDING_REFERENCE_AVAILABLE = "BTCUSDT_FUNDING_REFERENCE_AVAILABLE"
    BTCUSDT_FUNDING_REFERENCE_UNAVAILABLE = "BTCUSDT_FUNDING_REFERENCE_UNAVAILABLE"
    DEMO_MARK_FEED_MAPPING_QUALIFIED = "DEMO_MARK_FEED_MAPPING_QUALIFIED"
    DEMO_FUNDING_FEED_MAPPING_QUALIFIED = "DEMO_FUNDING_FEED_MAPPING_QUALIFIED"
    DEMO_FEED_MAPPING_PARTIAL = "DEMO_FEED_MAPPING_PARTIAL"
    DEMO_FEED_MAPPING_BLOCKED = "DEMO_FEED_MAPPING_BLOCKED"


class DemoFeedFact(StrEnum):
    MARK_PRICE = "MARK_PRICE"
    INDEX_PRICE = "INDEX_PRICE"
    LAST_FUNDING_RATE = "LAST_FUNDING_RATE"
    FORECAST_FUNDING_RATE = "FORECAST_FUNDING_RATE"
    NEXT_FUNDING_TIME = "NEXT_FUNDING_TIME"
    FUNDING_CYCLE = "FUNDING_CYCLE"
    FEED_TIMESTAMP = "FEED_TIMESTAMP"


class DemoFeedExcludedInference(StrEnum):
    CONTRACT_VALUE = "CONTRACT_VALUE"
    QUANTITY_SEMANTICS = "QUANTITY_SEMANTICS"
    PRECISION_OR_LIMITS = "PRECISION_OR_LIMITS"
    FEES = "FEES"
    LEVERAGE = "LEVERAGE"
    MARGIN = "MARGIN"
    MAINTENANCE_MARGIN = "MAINTENANCE_MARGIN"
    LIQUIDATION = "LIQUIDATION"
    FILL_OR_SLIPPAGE = "FILL_OR_SLIPPAGE"
    ORDER_BOOK_OR_EXECUTION_QUALITY = "ORDER_BOOK_OR_EXECUTION_QUALITY"
    HISTORICAL_AUTHORITY = "HISTORICAL_AUTHORITY"
    GENERAL_PRODUCTION_EQUIVALENCE = "GENERAL_PRODUCTION_EQUIVALENCE"


class DemoFeedProposition(StrEnum):
    DEMO_ORDER_SYMBOL_BTCSUSDT = "DEMO_ORDER_SYMBOL_BTCSUSDT"
    DEMO_PAIRS_USE_SUSDT = "DEMO_PAIRS_USE_SUSDT"
    SUSDT_TRACKS_LIVE_USDT_MARKET_PRICE = "SUSDT_TRACKS_LIVE_USDT_MARKET_PRICE"
    DEMO_USES_LIVE_PRICE_FEED = "DEMO_USES_LIVE_PRICE_FEED"
    DEMO_USES_LIVE_FUNDING_FEED = "DEMO_USES_LIVE_FUNDING_FEED"
    PUBLIC_V3_EXPOSES_MARK = "PUBLIC_V3_EXPOSES_MARK"
    PUBLIC_V3_EXPOSES_FUNDING = "PUBLIC_V3_EXPOSES_FUNDING"


@dataclass(frozen=True, slots=True)
class _DocumentSpecification:
    url: str
    title: str
    source_type: str
    required_tokens: tuple[str, ...]
    propositions: tuple[DemoFeedProposition, ...]


_DOCUMENT_SPECIFICATIONS: Final = (
    _DocumentSpecification(
        url=DEMO_ORDER_DOC_URL,
        title="Place Order (Demo)",
        source_type="official_api_documentation",
        required_tokens=("/capi/v3/sim/order", "BTCSUSDT"),
        propositions=(DemoFeedProposition.DEMO_ORDER_SYMBOL_BTCSUSDT,),
    ),
    _DocumentSpecification(
        url=DEMO_RULES_URL,
        title="WEEX Demo Trading: 50,000 USDT Practice Account, Rules and Limits",
        source_type="official_weex_guide",
        required_tokens=(
            "SUSDT is the simulated stablecoin used to quote WEEX demo futures pairs",
            "It tracks the live USDT-quoted market price",
        ),
        propositions=(
            DemoFeedProposition.DEMO_PAIRS_USE_SUSDT,
            DemoFeedProposition.SUSDT_TRACKS_LIVE_USDT_MARKET_PRICE,
        ),
    ),
    _DocumentSpecification(
        url=DEMO_STRATEGY_GUIDE_URL,
        title="How to Test a Trading Strategy on WEEX Demo Before Going Live",
        source_type="official_weex_guide",
        required_tokens=("the same live price and funding feed",),
        propositions=(
            DemoFeedProposition.DEMO_USES_LIVE_PRICE_FEED,
            DemoFeedProposition.DEMO_USES_LIVE_FUNDING_FEED,
        ),
    ),
    _DocumentSpecification(
        url=SYMBOL_PRICE_DOC_URL,
        title="Get Symbol Price",
        source_type="official_api_documentation",
        required_tokens=("/capi/v3/market/symbolPrice", "priceType", "MARK"),
        propositions=(DemoFeedProposition.PUBLIC_V3_EXPOSES_MARK,),
    ),
    _DocumentSpecification(
        url=PREMIUM_INDEX_DOC_URL,
        title="Get Current Funding Rate",
        source_type="official_api_documentation",
        required_tokens=(
            "/capi/v3/market/premiumIndex",
            "markPrice",
            "indexPrice",
            "lastFundingRate",
            "forecastFundingRate",
            "nextFundingTime",
            "collectCycle",
        ),
        propositions=(DemoFeedProposition.PUBLIC_V3_EXPOSES_FUNDING,),
    ),
)

_ALL_PROPOSITIONS: Final = tuple(
    proposition
    for specification in _DOCUMENT_SPECIFICATIONS
    for proposition in specification.propositions
)
_ALL_FACTS: Final = tuple(DemoFeedFact)
_ALL_EXCLUDED_INFERENCES: Final = tuple(DemoFeedExcludedInference)


class DocumentTransport(Protocol):
    def get(self, url: str) -> bytes: ...


class _RejectDocumentRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, code, msg, headers, newurl
        fp.close()
        raise DemoFeedMappingRequestError("official WEEX document refused an HTTP redirect")


class UrlLibDocumentTransport:
    def __init__(self, *, timeout_seconds: float = 30) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_RejectDocumentRedirect())

    def get(self, url: str) -> bytes:
        if url not in {item.url for item in _DOCUMENT_SPECIFICATIONS}:
            raise DemoFeedMappingRequestError("document URL is not reviewed")
        request = urllib.request.Request(
            url, headers={"User-Agent": "adaptive-trading-bot-demo-feed-evidence/1"}
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return cast(bytes, response.read())
        except (
            DemoFeedMappingRequestError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ):
            pass
        raise DemoFeedMappingRequestError("approved official WEEX document request failed")


@dataclass(frozen=True, slots=True, order=True)
class OfficialDocumentEvidence:
    url: str
    title: str
    source_type: str
    retrieved_at: datetime
    content_sha256: str
    propositions: tuple[DemoFeedProposition, ...]

    def validate(self) -> None:
        require_utc(self.retrieved_at, field="document retrieved_at")
        if not _HEX_SHA256.fullmatch(self.content_sha256):
            raise DemoFeedMappingIntegrityError("document digest is invalid")
        specification = next(
            (item for item in _DOCUMENT_SPECIFICATIONS if item.url == self.url), None
        )
        if specification is None or (
            self.title != specification.title
            or self.source_type != specification.source_type
            or self.propositions != specification.propositions
        ):
            raise DemoFeedMappingIntegrityError("document provenance is not reviewed")


@dataclass(frozen=True, slots=True)
class DemoFeedMapping:
    demo_symbol: str
    reference_symbol: str
    evidence_version: str
    authorized_facts: tuple[DemoFeedFact, ...]
    excluded_inferences: tuple[DemoFeedExcludedInference, ...]
    contract_mechanics_qualified: bool
    production_equivalence_claimed: bool
    historical_authority_claimed: bool

    def validate(self) -> None:
        if self.demo_symbol != DEMO_SYMBOL or self.reference_symbol != REFERENCE_SYMBOL:
            raise DemoFeedMappingIntegrityError("only the reviewed BTC Demo mapping is valid")
        if not self.evidence_version.startswith("weex-demo-feed-mapping-"):
            raise DemoFeedMappingIntegrityError("mapping evidence version is invalid")
        if len(set(self.authorized_facts)) != len(self.authorized_facts):
            raise DemoFeedMappingIntegrityError("authorized feed facts are duplicated")
        if self.excluded_inferences != _ALL_EXCLUDED_INFERENCES:
            raise DemoFeedMappingIntegrityError("excluded mechanics are incomplete")
        if any(
            (
                self.contract_mechanics_qualified,
                self.production_equivalence_claimed,
                self.historical_authority_claimed,
            )
        ):
            raise DemoFeedMappingIntegrityError("feed mapping claims forbidden authority")

    def authorizes(self, fact: DemoFeedFact) -> bool:
        return fact in self.authorized_facts


@dataclass(frozen=True, slots=True)
class DemoFeedMappingBundle:
    schema_version: str
    bundle_version: str
    captured_at: datetime
    source_code_commit: str
    documents: tuple[OfficialDocumentEvidence, ...]
    source_observations: tuple[forward.SourceObservation, ...]
    reference_mark: forward.CurrentMarkEvidence | None
    reference_premium: forward.PremiumIndexEvidence | None
    exact_mark_delta: Decimal | None
    mark_observation_time_delta_ms: int | None
    statuses: tuple[DemoFeedMappingStatus, ...]
    mapping: DemoFeedMapping
    integrity_seal: str

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise DemoFeedMappingIntegrityError("unknown Demo feed mapping schema")
        require_utc(self.captured_at, field="mapping captured_at")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise DemoFeedMappingIntegrityError("mapping source SHA is invalid")
        if tuple(sorted(self.documents, key=lambda item: item.url)) != self.documents:
            raise DemoFeedMappingIntegrityError("document evidence is not canonical")
        for document in self.documents:
            document.validate()
            if document.retrieved_at != self.captured_at:
                raise DemoFeedMappingIntegrityError("document retrieval chronology is inconsistent")
        if len({item.url for item in self.documents}) != len(self.documents):
            raise DemoFeedMappingIntegrityError("document evidence is duplicated")
        if tuple(sorted(self.source_observations, key=lambda item: item.endpoint)) != (
            self.source_observations
        ):
            raise DemoFeedMappingIntegrityError("runtime observations are not canonical")
        for observation in self.source_observations:
            if observation.endpoint not in {
                forward.PREMIUM_INDEX_PATH,
                forward.SYMBOL_PRICE_PATH,
            }:
                raise DemoFeedMappingIntegrityError("mapping used an unapproved market route")
            if dict(observation.parameters).get("symbol") != REFERENCE_SYMBOL:
                raise DemoFeedMappingIntegrityError("runtime observation symbol is not BTCUSDT")
        if self.reference_mark is not None and self.reference_mark.symbol != REFERENCE_SYMBOL:
            raise DemoFeedMappingIntegrityError("MARK reference symbol is invalid")
        if self.reference_premium is not None and self.reference_premium.symbol != REFERENCE_SYMBOL:
            raise DemoFeedMappingIntegrityError("funding reference symbol is invalid")
        expected_delta = (
            None
            if self.reference_mark is None or self.reference_premium is None
            else self.reference_mark.price - self.reference_premium.mark_price
        )
        expected_time_delta = (
            None
            if self.reference_mark is None or self.reference_premium is None
            else self.reference_mark.venue_time_ms - self.reference_premium.venue_time_ms
        )
        if (
            self.exact_mark_delta != expected_delta
            or self.mark_observation_time_delta_ms != expected_time_delta
        ):
            raise DemoFeedMappingIntegrityError("MARK comparison is inconsistent")
        expected_statuses, expected_facts = _expected_outcome(
            documents=self.documents,
            mark=self.reference_mark,
            premium=self.reference_premium,
        )
        if self.statuses != expected_statuses:
            raise DemoFeedMappingIntegrityError("mapping statuses are inconsistent")
        self.mapping.validate()
        if (
            self.mapping.evidence_version != self.bundle_version
            or self.mapping.authorized_facts != expected_facts
        ):
            raise DemoFeedMappingIntegrityError("mapping authority is inconsistent")
        expected_version = f"weex-demo-feed-mapping-{_bundle_identity_sha(self)[:24]}"
        if self.bundle_version != expected_version:
            raise DemoFeedMappingIntegrityError("mapping bundle version is invalid")
        if self.integrity_seal != _integrity_sha(self):
            raise DemoFeedMappingIntegrityError("mapping evidence seal mismatch")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


@dataclass(frozen=True, slots=True)
class DemoFeedMappingManifest:
    schema_version: str
    bundle_version: str
    source_code_commit: str
    captured_at: datetime
    artifact_path: str
    artifact_sha256: str
    artifact_size_bytes: int

    def validate(self) -> None:
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise CorpusManifestError("unknown Demo feed mapping manifest schema")
        if not self.bundle_version.startswith("weex-demo-feed-mapping-"):
            raise CorpusManifestError("Demo feed mapping version is invalid")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise CorpusManifestError("Demo feed mapping code SHA is invalid")
        require_utc(self.captured_at, field="mapping manifest captured_at")
        if self.artifact_path != "bundle.json":
            raise CorpusManifestError("Demo feed mapping artifact path is invalid")
        _require_sha256(self.artifact_sha256, "mapping artifact SHA-256")
        if type(self.artifact_size_bytes) is not int or self.artifact_size_bytes <= 0:
            raise CorpusManifestError("Demo feed mapping artifact size is invalid")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


class WeexDemoFeedMappingCollector:
    def __init__(
        self,
        public_client: forward.WeexPublicCurrentClient | None = None,
        document_transport: DocumentTransport | None = None,
    ) -> None:
        self._public = public_client or forward.WeexPublicCurrentClient()
        self._documents = document_transport or UrlLibDocumentTransport()

    def capture(self, *, captured_at: datetime, source_code_commit: str) -> DemoFeedMappingBundle:
        captured_at = require_utc(captured_at, field="mapping captured_at")
        if not _CODE_SHA.fullmatch(source_code_commit):
            raise DemoFeedMappingIntegrityError("capture requires an exact code SHA")
        documents: list[OfficialDocumentEvidence] = []
        for specification in _DOCUMENT_SPECIFICATIONS:
            try:
                content = self._documents.get(specification.url)
                text = content.decode("utf-8")
                if not all(
                    token.casefold() in text.casefold() for token in specification.required_tokens
                ):
                    continue
                documents.append(
                    OfficialDocumentEvidence(
                        url=specification.url,
                        title=specification.title,
                        source_type=specification.source_type,
                        retrieved_at=captured_at,
                        content_sha256=sha256(content).hexdigest(),
                        propositions=specification.propositions,
                    )
                )
            except (DemoFeedMappingRequestError, UnicodeDecodeError):
                continue

        observations: list[forward.SourceObservation] = []
        premium: forward.PremiumIndexEvidence | None = None
        mark: forward.CurrentMarkEvidence | None = None
        try:
            raw, observation = self._public.request(
                "GET", forward.PREMIUM_INDEX_PATH, {"symbol": REFERENCE_SYMBOL}
            )
            premium = forward._parse_premium(raw, REFERENCE_SYMBOL)
            observations.append(observation)
        except forward.ForwardEvidenceError:
            pass
        try:
            raw, observation = self._public.request(
                "GET",
                forward.SYMBOL_PRICE_PATH,
                {"priceType": "MARK", "symbol": REFERENCE_SYMBOL},
            )
            mark = forward._parse_mark(raw, REFERENCE_SYMBOL)
            observations.append(observation)
        except forward.ForwardEvidenceError:
            pass

        ordered_documents = tuple(sorted(documents, key=lambda item: item.url))
        statuses, facts = _expected_outcome(documents=ordered_documents, mark=mark, premium=premium)
        seed = DemoFeedMappingBundle(
            schema_version=SCHEMA_VERSION,
            bundle_version="pending",
            captured_at=captured_at,
            source_code_commit=source_code_commit,
            documents=ordered_documents,
            source_observations=tuple(sorted(observations, key=lambda item: item.endpoint)),
            reference_mark=mark,
            reference_premium=premium,
            exact_mark_delta=(
                None if mark is None or premium is None else mark.price - premium.mark_price
            ),
            mark_observation_time_delta_ms=(
                None
                if mark is None or premium is None
                else mark.venue_time_ms - premium.venue_time_ms
            ),
            statuses=statuses,
            mapping=DemoFeedMapping(
                demo_symbol=DEMO_SYMBOL,
                reference_symbol=REFERENCE_SYMBOL,
                evidence_version="pending",
                authorized_facts=facts,
                excluded_inferences=_ALL_EXCLUDED_INFERENCES,
                contract_mechanics_qualified=False,
                production_equivalence_claimed=False,
                historical_authority_claimed=False,
            ),
            integrity_seal="pending",
        )
        version = f"weex-demo-feed-mapping-{_bundle_identity_sha(seed)[:24]}"
        versioned = replace(
            seed, bundle_version=version, mapping=replace(seed.mapping, evidence_version=version)
        )
        sealed = replace(versioned, integrity_seal=_integrity_sha(versioned))
        sealed.validate()
        return sealed


class LockedWeexDemoFeedMappingBundle:
    """Network-free reader for one exact reviewed mapping bundle."""

    def __init__(self, root: Path, *, bundle_version: str) -> None:
        self._root = root
        manifest_path = root / "manifest.json"
        self._manifest = _parse_manifest(_load_json_file(manifest_path))
        if self._manifest.bundle_version != bundle_version:
            raise CorpusManifestError("requested Demo feed mapping version is unknown")
        accepted = LOCKED_DEMO_FEED_MAPPING_MANIFEST_SHA256.get(bundle_version)
        if accepted is None:
            raise CorpusManifestError("Demo feed mapping version is not project-qualified")
        if _file_sha256(manifest_path) != accepted:
            raise CorpusHashMismatchError("Demo feed mapping manifest hash mismatch")
        self._verify_artifact()

    @property
    def manifest(self) -> DemoFeedMappingManifest:
        return self._manifest

    def read(self) -> DemoFeedMappingBundle:
        self._verify_artifact()
        bundle = _parse_bundle(_load_json_file(self._root / self._manifest.artifact_path))
        if (
            bundle.bundle_version != self._manifest.bundle_version
            or bundle.source_code_commit != self._manifest.source_code_commit
            or bundle.captured_at != self._manifest.captured_at
        ):
            raise CorpusManifestError("Demo feed mapping provenance mismatch")
        return bundle

    def _verify_artifact(self) -> None:
        path = self._root / self._manifest.artifact_path
        if (
            not path.is_file()
            or path.stat().st_size != self._manifest.artifact_size_bytes
            or _file_sha256(path) != self._manifest.artifact_sha256
        ):
            raise CorpusHashMismatchError("Demo feed mapping artifact hash mismatch")


@dataclass(frozen=True, slots=True)
class ResolvedDemoMarkFeed:
    requested_demo_symbol: str
    actual_source_symbol: str
    mapping_evidence_version: str | None
    price: Decimal
    venue_time_ms: int
    venue_time: datetime
    source_observation: forward.SourceObservation


@dataclass(frozen=True, slots=True)
class _CurrentFeedMark:
    symbol: str
    price: Decimal
    venue_time_ms: int
    venue_time: datetime


@dataclass(frozen=True, slots=True)
class ResolvedDemoFundingFeed:
    requested_demo_symbol: str
    actual_source_symbol: str
    mapping_evidence_version: str | None
    mark_price: Decimal
    index_price: Decimal
    last_funding_rate: Decimal
    forecast_funding_rate: Decimal
    next_funding_time_ms: int
    next_funding_time: datetime
    venue_time_ms: int
    venue_time: datetime
    collect_cycle_minutes: int
    source_observation: forward.SourceObservation


@dataclass(frozen=True, slots=True)
class _CurrentFeedPremium:
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    last_funding_rate: Decimal
    forecast_funding_rate: Decimal
    next_funding_time_ms: int
    next_funding_time: datetime
    venue_time_ms: int
    venue_time: datetime
    collect_cycle_minutes: int


class DemoFeedMappingResolver:
    """Resolve only reviewed current feed facts; direct Demo observations win."""

    def __init__(self, locked_bundle: LockedWeexDemoFeedMappingBundle) -> None:
        self._bundle = locked_bundle.read()

    def resolve_mark(
        self, public_client: forward.WeexPublicCurrentClient
    ) -> ResolvedDemoMarkFeed | None:
        direct = _request_mark(public_client, DEMO_SYMBOL)
        if direct is not None:
            mark, observation = direct
            return ResolvedDemoMarkFeed(
                requested_demo_symbol=DEMO_SYMBOL,
                actual_source_symbol=DEMO_SYMBOL,
                mapping_evidence_version=None,
                price=mark.price,
                venue_time_ms=mark.venue_time_ms,
                venue_time=mark.venue_time,
                source_observation=observation,
            )
        if not self._bundle.mapping.authorizes(DemoFeedFact.MARK_PRICE):
            return None
        mapped = _request_mark(public_client, REFERENCE_SYMBOL)
        if mapped is None:
            return None
        mark, observation = mapped
        return ResolvedDemoMarkFeed(
            requested_demo_symbol=DEMO_SYMBOL,
            actual_source_symbol=REFERENCE_SYMBOL,
            mapping_evidence_version=self._bundle.bundle_version,
            price=mark.price,
            venue_time_ms=mark.venue_time_ms,
            venue_time=mark.venue_time,
            source_observation=observation,
        )

    def resolve_funding(
        self, public_client: forward.WeexPublicCurrentClient
    ) -> ResolvedDemoFundingFeed | None:
        direct = _request_premium(public_client, DEMO_SYMBOL)
        if direct is not None:
            premium, observation = direct
            return _resolved_funding(premium, observation, mapping_evidence_version=None)
        required = (
            DemoFeedFact.INDEX_PRICE,
            DemoFeedFact.LAST_FUNDING_RATE,
            DemoFeedFact.FORECAST_FUNDING_RATE,
            DemoFeedFact.NEXT_FUNDING_TIME,
            DemoFeedFact.FUNDING_CYCLE,
            DemoFeedFact.FEED_TIMESTAMP,
        )
        if not all(self._bundle.mapping.authorizes(fact) for fact in required):
            return None
        mapped = _request_premium(public_client, REFERENCE_SYMBOL)
        if mapped is None:
            return None
        premium, observation = mapped
        return _resolved_funding(
            premium, observation, mapping_evidence_version=self._bundle.bundle_version
        )


def write_mapping_bundle(
    bundle: DemoFeedMappingBundle, output_root: Path
) -> DemoFeedMappingManifest:
    bundle.validate()
    if output_root.exists():
        raise FileExistsError("Demo feed mapping output root must not already exist")
    output_root.mkdir(parents=True)
    artifact = output_root / "bundle.json"
    artifact_bytes = (bundle.to_json() + "\n").encode()
    _atomic_write(artifact, artifact_bytes)
    manifest = DemoFeedMappingManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        bundle_version=bundle.bundle_version,
        source_code_commit=bundle.source_code_commit,
        captured_at=bundle.captured_at,
        artifact_path="bundle.json",
        artifact_sha256=sha256(artifact_bytes).hexdigest(),
        artifact_size_bytes=len(artifact_bytes),
    )
    _atomic_write(output_root / "manifest.json", (manifest.to_json() + "\n").encode())
    return manifest


def _expected_outcome(
    *,
    documents: tuple[OfficialDocumentEvidence, ...],
    mark: forward.CurrentMarkEvidence | None,
    premium: forward.PremiumIndexEvidence | None,
) -> tuple[tuple[DemoFeedMappingStatus, ...], tuple[DemoFeedFact, ...]]:
    propositions = tuple(
        proposition for document in documents for proposition in document.propositions
    )
    docs_qualified = len(documents) == len(_DOCUMENT_SPECIFICATIONS) and set(propositions) == set(
        _ALL_PROPOSITIONS
    )
    statuses: list[DemoFeedMappingStatus] = [
        DemoFeedMappingStatus.DEMO_FEED_DOCS_QUALIFIED
        if docs_qualified
        else DemoFeedMappingStatus.DEMO_FEED_DOCS_UNQUALIFIED,
        DemoFeedMappingStatus.BTCUSDT_MARK_REFERENCE_AVAILABLE
        if mark is not None
        else DemoFeedMappingStatus.BTCUSDT_MARK_REFERENCE_UNAVAILABLE,
        DemoFeedMappingStatus.BTCUSDT_FUNDING_REFERENCE_AVAILABLE
        if premium is not None
        else DemoFeedMappingStatus.BTCUSDT_FUNDING_REFERENCE_UNAVAILABLE,
    ]
    facts: list[DemoFeedFact] = []
    if docs_qualified and mark is not None:
        statuses.append(DemoFeedMappingStatus.DEMO_MARK_FEED_MAPPING_QUALIFIED)
        facts.extend((DemoFeedFact.MARK_PRICE, DemoFeedFact.FEED_TIMESTAMP))
    if docs_qualified and premium is not None:
        statuses.append(DemoFeedMappingStatus.DEMO_FUNDING_FEED_MAPPING_QUALIFIED)
        facts.extend(
            (
                DemoFeedFact.INDEX_PRICE,
                DemoFeedFact.LAST_FUNDING_RATE,
                DemoFeedFact.FORECAST_FUNDING_RATE,
                DemoFeedFact.NEXT_FUNDING_TIME,
                DemoFeedFact.FUNDING_CYCLE,
                DemoFeedFact.FEED_TIMESTAMP,
            )
        )
    if len(facts) == 0:
        statuses.append(DemoFeedMappingStatus.DEMO_FEED_MAPPING_BLOCKED)
    elif mark is None or premium is None:
        statuses.append(DemoFeedMappingStatus.DEMO_FEED_MAPPING_PARTIAL)
    return tuple(statuses), tuple(fact for fact in _ALL_FACTS if fact in facts)


def _request_mark(
    client: forward.WeexPublicCurrentClient, symbol: str
) -> tuple[_CurrentFeedMark, forward.SourceObservation] | None:
    try:
        payload, observation = client.request(
            "GET", forward.SYMBOL_PRICE_PATH, {"priceType": "MARK", "symbol": symbol}
        )
        item = forward._payload_object(payload, "symbolPrice")
        actual_symbol = forward._strict_text(item.get("symbol"), "MARK symbol")
        if actual_symbol != symbol:
            raise forward.ForwardEvidenceIntegrityError("current MARK symbol mismatch")
        venue_ms = forward._strict_integer(item.get("time"), "MARK time")
        return (
            _CurrentFeedMark(
                symbol=actual_symbol,
                price=forward._strict_decimal(item.get("price"), "MARK price"),
                venue_time_ms=venue_ms,
                venue_time=forward._from_milliseconds(venue_ms),
            ),
            observation,
        )
    except forward.ForwardEvidenceError:
        return None


def _request_premium(
    client: forward.WeexPublicCurrentClient, symbol: str
) -> tuple[_CurrentFeedPremium, forward.SourceObservation] | None:
    try:
        payload, observation = client.request("GET", forward.PREMIUM_INDEX_PATH, {"symbol": symbol})
        items = forward._payload_list(payload, "premiumIndex")
        if len(items) != 1 or not isinstance(items[0], dict):
            raise forward.ForwardEvidenceIntegrityError(
                "premiumIndex must return exactly one object"
            )
        item = items[0]
        actual_symbol = forward._strict_text(item.get("symbol"), "premium symbol")
        if actual_symbol != symbol:
            raise forward.ForwardEvidenceIntegrityError("premiumIndex symbol mismatch")
        next_ms = forward._strict_integer(item.get("nextFundingTime"), "nextFundingTime")
        venue_ms = forward._strict_integer(item.get("time"), "premium time")
        return (
            _CurrentFeedPremium(
                symbol=actual_symbol,
                mark_price=forward._strict_decimal(item.get("markPrice"), "premium markPrice"),
                index_price=forward._strict_decimal(item.get("indexPrice"), "premium indexPrice"),
                last_funding_rate=forward._strict_decimal(
                    item.get("lastFundingRate"), "lastFundingRate"
                ),
                forecast_funding_rate=forward._strict_decimal(
                    item.get("forecastFundingRate"), "forecastFundingRate"
                ),
                next_funding_time_ms=next_ms,
                next_funding_time=forward._from_milliseconds(next_ms),
                venue_time_ms=venue_ms,
                venue_time=forward._from_milliseconds(venue_ms),
                collect_cycle_minutes=forward._strict_integer(
                    item.get("collectCycle"), "collectCycle"
                ),
            ),
            observation,
        )
    except forward.ForwardEvidenceError:
        return None


def _resolved_funding(
    premium: _CurrentFeedPremium,
    observation: forward.SourceObservation,
    *,
    mapping_evidence_version: str | None,
) -> ResolvedDemoFundingFeed:
    return ResolvedDemoFundingFeed(
        requested_demo_symbol=DEMO_SYMBOL,
        actual_source_symbol=premium.symbol,
        mapping_evidence_version=mapping_evidence_version,
        mark_price=premium.mark_price,
        index_price=premium.index_price,
        last_funding_rate=premium.last_funding_rate,
        forecast_funding_rate=premium.forecast_funding_rate,
        next_funding_time_ms=premium.next_funding_time_ms,
        next_funding_time=premium.next_funding_time,
        venue_time_ms=premium.venue_time_ms,
        venue_time=premium.venue_time,
        collect_cycle_minutes=premium.collect_cycle_minutes,
        source_observation=observation,
    )


def _bundle_identity_sha(bundle: DemoFeedMappingBundle) -> str:
    seed = replace(
        bundle,
        bundle_version="pending",
        mapping=replace(bundle.mapping, evidence_version="pending"),
        integrity_seal="pending",
    )
    return sha256(_canonical_json(_canonical_value(seed)).encode()).hexdigest()


def _integrity_sha(bundle: DemoFeedMappingBundle) -> str:
    seed = replace(bundle, integrity_seal="pending")
    return sha256(_canonical_json(_canonical_value(seed)).encode()).hexdigest()


def _parse_manifest(raw: object) -> DemoFeedMappingManifest:
    item = _mapping(raw, "mapping manifest")
    try:
        manifest = DemoFeedMappingManifest(
            schema_version=_text(item["schema_version"], "manifest schema"),
            bundle_version=_text(item["bundle_version"], "manifest version"),
            source_code_commit=_text(item["source_code_commit"], "manifest code SHA"),
            captured_at=_utc(item["captured_at"], "manifest captured_at"),
            artifact_path=_text(item["artifact_path"], "artifact path"),
            artifact_sha256=_text(item["artifact_sha256"], "artifact SHA-256"),
            artifact_size_bytes=_integer(item["artifact_size_bytes"], "artifact size"),
        )
    except KeyError as error:
        raise CorpusManifestError("Demo feed mapping manifest is incomplete") from error
    manifest.validate()
    return manifest


def _parse_bundle(raw: object) -> DemoFeedMappingBundle:
    item = _mapping(raw, "mapping bundle")
    try:
        mapping_item = _mapping(item["mapping"], "mapping authority")
        bundle = DemoFeedMappingBundle(
            schema_version=_text(item["schema_version"], "schema version"),
            bundle_version=_text(item["bundle_version"], "bundle version"),
            captured_at=_utc(item["captured_at"], "captured_at"),
            source_code_commit=_text(item["source_code_commit"], "source code SHA"),
            documents=tuple(
                _document_from_json(value) for value in _mapping_items(item["documents"])
            ),
            source_observations=tuple(
                _source_from_json(value) for value in _mapping_items(item["source_observations"])
            ),
            reference_mark=_optional_mapping(item["reference_mark"], forward._mark_from_json),
            reference_premium=_optional_mapping(
                item["reference_premium"], forward._premium_from_json
            ),
            exact_mark_delta=_optional_decimal(item["exact_mark_delta"], "MARK delta"),
            mark_observation_time_delta_ms=_optional_integer(
                item["mark_observation_time_delta_ms"], "MARK time delta"
            ),
            statuses=tuple(DemoFeedMappingStatus(value) for value in _strings(item["statuses"])),
            mapping=DemoFeedMapping(
                demo_symbol=_text(mapping_item["demo_symbol"], "Demo symbol"),
                reference_symbol=_text(mapping_item["reference_symbol"], "reference symbol"),
                evidence_version=_text(mapping_item["evidence_version"], "evidence version"),
                authorized_facts=tuple(
                    DemoFeedFact(value) for value in _strings(mapping_item["authorized_facts"])
                ),
                excluded_inferences=tuple(
                    DemoFeedExcludedInference(value)
                    for value in _strings(mapping_item["excluded_inferences"])
                ),
                contract_mechanics_qualified=_boolean(
                    mapping_item["contract_mechanics_qualified"], "contract mechanics"
                ),
                production_equivalence_claimed=_boolean(
                    mapping_item["production_equivalence_claimed"], "production equivalence"
                ),
                historical_authority_claimed=_boolean(
                    mapping_item["historical_authority_claimed"], "historical authority"
                ),
            ),
            integrity_seal=_text(item["integrity_seal"], "integrity seal"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DemoFeedMappingIntegrityError("Demo feed mapping bundle cannot be decoded") from error
    bundle.validate()
    return bundle


def _document_from_json(value: Mapping[str, object]) -> OfficialDocumentEvidence:
    return OfficialDocumentEvidence(
        url=_text(value["url"], "document URL"),
        title=_text(value["title"], "document title"),
        source_type=_text(value["source_type"], "document source type"),
        retrieved_at=_utc(value["retrieved_at"], "document retrieved_at"),
        content_sha256=_text(value["content_sha256"], "document digest"),
        propositions=tuple(DemoFeedProposition(item) for item in _strings(value["propositions"])),
    )


def _source_from_json(value: Mapping[str, object]) -> forward.SourceObservation:
    return forward.SourceObservation(
        endpoint=_text(value["endpoint"], "source endpoint"),
        parameters=_pairs(value["parameters"]),
        response_sha256=_text(value["response_sha256"], "response SHA-256"),
    )


def _canonical_value(value: object) -> object:
    return forward._canonical_value(value)


def _canonical_json(value: object) -> str:
    return forward._canonical_json(value)


def _load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusManifestError("Demo feed mapping JSON cannot be decoded") from error


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise DemoFeedMappingIntegrityError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise DemoFeedMappingIntegrityError("expected an array of objects")
    return tuple(_mapping(item, "array item") for item in value)


def _optional_mapping[T](value: object, parser: Callable[[Mapping[str, object]], T]) -> T | None:
    return None if value is None else parser(_mapping(value, "optional object"))


def _text(value: object, field: str) -> str:
    return forward._strict_text(value, field)


def _integer(value: object, field: str) -> int:
    return forward._strict_integer(value, field)


def _optional_integer(value: object, field: str) -> int | None:
    return None if value is None else _integer(value, field)


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else forward._strict_decimal(value, field)


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise DemoFeedMappingIntegrityError(f"{field} must be boolean")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DemoFeedMappingIntegrityError("expected an array of strings")
    return tuple(cast(list[str], value))


def _pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise DemoFeedMappingIntegrityError("source parameters must be an array")
    pairs: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(part, str) for part in item)
        ):
            raise DemoFeedMappingIntegrityError("source parameter is invalid")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DemoFeedMappingIntegrityError(f"{field} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise DemoFeedMappingIntegrityError(f"{field} is invalid") from error
    return require_utc(parsed, field=field)


def _require_sha256(value: str, field: str) -> None:
    if not _HEX_SHA256.fullmatch(value):
        raise DemoFeedMappingIntegrityError(f"{field} is invalid")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _parse_captured_at(value: str) -> datetime:
    return _utc(value, "captured_at")


def main() -> int:
    parser = ArgumentParser(description="Capture WEEX Demo feed-mapping evidence")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-code-commit", required=True)
    parser.add_argument("--captured-at", type=_parse_captured_at)
    arguments = parser.parse_args()
    captured_at = arguments.captured_at or datetime.now(tz=UTC)
    bundle = WeexDemoFeedMappingCollector().capture(
        captured_at=captured_at, source_code_commit=arguments.source_code_commit
    )
    manifest = write_mapping_bundle(bundle, arguments.output_root)
    print(manifest.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
