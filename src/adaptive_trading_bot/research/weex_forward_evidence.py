"""Fail-closed, research-only WEEX V3 forward mechanics evidence.

The network boundary in this module is deliberately GET-only.  It can read a
small reviewed set of public market routes and three signed Demo routes; it has
no order, leverage, margin-mode, transfer, or arbitrary-path capability.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from argparse import ArgumentParser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, cast

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

WEEX_V3_BASE_URL: Final = "https://api-contract.weex.com"
FORWARD_EVIDENCE_SCHEMA_VERSION: Final = "weex-v3-forward-evidence-v1"
FORWARD_EVIDENCE_MANIFEST_SCHEMA_VERSION: Final = "weex-v3-forward-evidence-manifest-v1"
WEEX_V3_CONTRACT_VERSION: Final = "V3"
WEEX_DEMO_CHANGELOG_REFERENCE: Final = (
    "https://www.weex.com/api-doc/contract/changelog#2026-04-09-demo"
)

EXCHANGE_INFO_PATH: Final = "/capi/v3/market/exchangeInfo"
PREMIUM_INDEX_PATH: Final = "/capi/v3/market/premiumIndex"
SYMBOL_PRICE_PATH: Final = "/capi/v3/market/symbolPrice"
MARK_KLINES_PATH: Final = "/capi/v3/market/markPriceKlines"
DEMO_BALANCE_PATH: Final = "/capi/v3/sim/balance"
DEMO_POSITIONS_PATH: Final = "/capi/v3/sim/position/allPosition"
DEMO_ORDER_HISTORY_PATH: Final = "/capi/v3/sim/order/history"

PUBLIC_GET_PATHS: Final = frozenset(
    {EXCHANGE_INFO_PATH, PREMIUM_INDEX_PATH, SYMBOL_PRICE_PATH, MARK_KLINES_PATH}
)
DEMO_GET_PATHS: Final = frozenset({DEMO_BALANCE_PATH, DEMO_POSITIONS_PATH, DEMO_ORDER_HISTORY_PATH})

# Review-owned registry. A new locked bundle requires an evidence PR.
QUALIFIED_FORWARD_EVIDENCE_MANIFEST_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "weex-v3-forward-b88af4b81510850232656be7": (
            "a344bf9cf726b8b68070637bca1c7971eaf248bf3e56585e4b5acd712b9088ca"
        )
    }
)

_CODE_SHA = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_CANONICAL_TEXT = re.compile(r"\S(?:.*\S)?")


class ForwardEvidenceError(ValueError):
    """Base failure for malformed or unsafe forward evidence."""


class UnsafeWeexRouteError(ForwardEvidenceError):
    """Raised before I/O for any route outside the exact GET allowlists."""


class WeexForwardRequestError(ForwardEvidenceError):
    """Sanitized transport failure which never includes credentials."""


class ForwardEvidenceIntegrityError(ForwardEvidenceError):
    """Raised for malformed, inconsistent, or tampered evidence."""


class ForwardEvidenceStatus(StrEnum):
    PUBLIC_CURRENT_EVIDENCE_QUALIFIED = "PUBLIC_CURRENT_EVIDENCE_QUALIFIED"
    DEMO_SCHEMA_QUALIFIED = "DEMO_SCHEMA_QUALIFIED"
    DEMO_CREDENTIALS_UNAVAILABLE = "DEMO_CREDENTIALS_UNAVAILABLE"
    DEMO_NO_POSITIONS_OBSERVED = "DEMO_NO_POSITIONS_OBSERVED"
    DEMO_POSITION_REJECTED_NOT_ISOLATED = "DEMO_POSITION_REJECTED_NOT_ISOLATED"
    DEMO_POSITION_REJECTED_AUTO_MARGIN = "DEMO_POSITION_REJECTED_AUTO_MARGIN"
    DEMO_POSITION_EVIDENCE_QUALIFIED = "DEMO_POSITION_EVIDENCE_QUALIFIED"
    DEMO_SYMBOL_MAPPING_UNRESOLVED = "DEMO_SYMBOL_MAPPING_UNRESOLVED"
    FORWARD_CALIBRATION_READY = "FORWARD_CALIBRATION_READY"
    FORWARD_CALIBRATION_BLOCKED = "FORWARD_CALIBRATION_BLOCKED"


class SecretValue:
    """Runtime-only secret whose string and repr forms are always redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value or value.strip() != value:
            raise ValueError("demo credential values must be non-empty and unpadded")
        self.__value = value

    def reveal_for_signing(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class WeexDemoCredentials:
    """Dedicated Demo-only credentials; never accepted by bundle serialization."""

    api_key: SecretValue
    secret_key: SecretValue
    passphrase: SecretValue

    @classmethod
    def from_environment(cls) -> WeexDemoCredentials | None:
        names = (
            "ATB_WEEX_DEMO_API_KEY",
            "ATB_WEEX_DEMO_SECRET_KEY",
            "ATB_WEEX_DEMO_PASSPHRASE",
        )
        values = tuple(os.environ.get(name) for name in names)
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ForwardEvidenceError("dedicated Demo credential environment is incomplete")
        return cls(*(SecretValue(cast(str, value)) for value in values))


class GetTransport(Protocol):
    """Narrow transport that has no write-method operation."""

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object: ...


class UrlLibGetTransport:
    def __init__(self, *, timeout_seconds: float = 20) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    def get(
        self,
        *,
        base_url: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> object:
        suffix = f"?{query}" if query else ""
        request = urllib.request.Request(f"{base_url}{path}{suffix}", headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.load(response, parse_float=Decimal)
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as error:
            raise WeexForwardRequestError(f"approved WEEX GET failed for {path}") from error


@dataclass(frozen=True, slots=True)
class SourceObservation:
    endpoint: str
    parameters: tuple[tuple[str, str], ...]
    response_sha256: str

    def __post_init__(self) -> None:
        if self.endpoint not in PUBLIC_GET_PATHS | DEMO_GET_PATHS:
            raise ForwardEvidenceIntegrityError("source endpoint is not reviewed")
        if self.parameters != tuple(sorted(self.parameters)):
            raise ForwardEvidenceIntegrityError("source parameters are not canonical")
        _require_sha256(self.response_sha256, "response_sha256")


@dataclass(frozen=True, slots=True, order=True)
class PublicContractEvidence:
    symbol: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    contract_type: str
    contract_val: Decimal
    min_leverage: Decimal
    max_leverage: Decimal
    price_precision: int
    quantity_precision: int
    min_order_size: Decimal
    max_order_size: Decimal
    max_position_size: Decimal
    market_open_limit_size: Decimal
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    api_maker_fee_rate: Decimal | None
    api_taker_fee_rate: Decimal | None

    def __post_init__(self) -> None:
        _require_public_symbol(self.symbol)
        if self.quote_asset != "USDT" or self.margin_asset != "USDT":
            raise ForwardEvidenceIntegrityError("public contract is not USDT quoted/margined")
        if self.contract_type != "PERPETUAL" or self.base_asset not in {"BTC", "ETH"}:
            raise ForwardEvidenceIntegrityError("public contract identity is invalid")
        positive = (
            self.contract_val,
            self.min_leverage,
            self.max_leverage,
            self.min_order_size,
            self.max_order_size,
            self.max_position_size,
            self.market_open_limit_size,
        )
        if any(not value.is_finite() or value <= 0 for value in positive):
            raise ForwardEvidenceIntegrityError("public contract quantities must be positive")
        if self.min_leverage > self.max_leverage:
            raise ForwardEvidenceIntegrityError("public leverage bounds are inverted")
        for rate in (
            self.maker_fee_rate,
            self.taker_fee_rate,
            self.api_maker_fee_rate,
            self.api_taker_fee_rate,
        ):
            if rate is not None and (not rate.is_finite() or rate < 0):
                raise ForwardEvidenceIntegrityError("public fee rate is invalid")
        if type(self.price_precision) is not int or self.price_precision < 0:
            raise ForwardEvidenceIntegrityError("price precision is invalid")
        if type(self.quantity_precision) is not int or self.quantity_precision < 0:
            raise ForwardEvidenceIntegrityError("quantity precision is invalid")


@dataclass(frozen=True, slots=True, order=True)
class PremiumIndexEvidence:
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    last_funding_rate: Decimal
    forecast_funding_rate: Decimal
    interest_rate: Decimal
    next_funding_time_ms: int
    next_funding_time: datetime
    venue_time_ms: int
    venue_time: datetime
    collect_cycle_minutes: int

    def __post_init__(self) -> None:
        _require_public_symbol(self.symbol)
        _require_positive_decimals(self.mark_price, self.index_price)
        _require_finite_decimals(
            self.last_funding_rate, self.forecast_funding_rate, self.interest_rate
        )
        _require_timestamp_pair(
            self.next_funding_time_ms, self.next_funding_time, "next_funding_time"
        )
        _require_timestamp_pair(self.venue_time_ms, self.venue_time, "premium venue_time")
        if type(self.collect_cycle_minutes) is not int or self.collect_cycle_minutes <= 0:
            raise ForwardEvidenceIntegrityError("funding collect cycle is invalid")


@dataclass(frozen=True, slots=True, order=True)
class CurrentMarkEvidence:
    symbol: str
    price: Decimal
    venue_time_ms: int
    venue_time: datetime

    def __post_init__(self) -> None:
        _require_public_symbol(self.symbol)
        _require_positive_decimals(self.price)
        _require_timestamp_pair(self.venue_time_ms, self.venue_time, "MARK venue_time")


@dataclass(frozen=True, slots=True, order=True)
class MarkKlineEvidence:
    symbol: str
    open_time_ms: int
    open_time: datetime
    close_time_ms: int
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        _require_public_symbol(self.symbol)
        _require_timestamp_pair(self.open_time_ms, self.open_time, "kline open_time")
        _require_timestamp_pair(self.close_time_ms, self.close_time, "kline close_time")
        if self.close_time <= self.open_time:
            raise ForwardEvidenceIntegrityError("MARK kline interval is invalid")
        _require_positive_decimals(self.open, self.high, self.low, self.close)
        if self.high < max(self.open, self.low, self.close) or self.low > min(
            self.open, self.high, self.close
        ):
            raise ForwardEvidenceIntegrityError("MARK kline violates OHLC semantics")


@dataclass(frozen=True, slots=True, order=True)
class DemoBalanceEvidence:
    asset: str
    balance: Decimal
    available_balance: Decimal
    frozen: Decimal
    unrealized_pnl: Decimal

    def __post_init__(self) -> None:
        _require_text(self.asset, "Demo balance asset")
        _require_finite_decimals(
            self.balance, self.available_balance, self.frozen, self.unrealized_pnl
        )
        if min(self.balance, self.available_balance, self.frozen) < 0:
            raise ForwardEvidenceIntegrityError("Demo balance values cannot be negative")


@dataclass(frozen=True, slots=True, order=True)
class DemoPositionEvidence:
    evidence_id: str
    asset: str
    symbol: str
    side: str
    margin_type: str
    separated_mode: str
    leverage: Decimal
    size: Decimal
    open_value: Decimal
    open_fee: Decimal
    funding_fee: Decimal
    margin_size: Decimal
    isolated_margin: Decimal
    is_auto_append_isolated_margin: bool
    cumulative_open_size: Decimal
    cumulative_open_value: Decimal
    cumulative_open_fee: Decimal
    cumulative_close_size: Decimal
    cumulative_close_value: Decimal
    cumulative_close_fee: Decimal
    cumulative_funding_fee: Decimal
    cumulative_liquidation_fee: Decimal
    created_match_sequence_hash: str
    updated_match_sequence_hash: str
    created_time_ms: int
    created_time: datetime
    updated_time_ms: int
    updated_time: datetime
    unrealized_pnl: Decimal
    liquidate_price: Decimal
    eligibility_status: ForwardEvidenceStatus
    rejection_reasons: tuple[ForwardEvidenceStatus, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.evidence_id, "Demo evidence_id")
        _require_sha256(self.created_match_sequence_hash, "created sequence hash")
        _require_sha256(self.updated_match_sequence_hash, "updated sequence hash")
        for value, name in ((self.asset, "asset"), (self.symbol, "symbol")):
            _require_text(value, f"Demo position {name}")
        if self.side not in {"LONG", "SHORT"}:
            raise ForwardEvidenceIntegrityError("Demo position side is invalid")
        if self.margin_type not in {"ISOLATED", "CROSSED"}:
            raise ForwardEvidenceIntegrityError("Demo margin type is invalid")
        if self.separated_mode not in {"COMBINED", "SEPARATED"}:
            raise ForwardEvidenceIntegrityError("Demo separated mode is invalid")
        if type(self.is_auto_append_isolated_margin) is not bool:
            raise ForwardEvidenceIntegrityError("Demo auto-margin flag must be boolean")
        _require_positive_decimals(self.leverage, self.size, self.open_value)
        if self.leverage != self.leverage.to_integral_value():
            raise ForwardEvidenceIntegrityError("Demo leverage must be an explicit integer")
        non_negative = (
            self.open_fee,
            self.margin_size,
            self.isolated_margin,
            self.cumulative_open_size,
            self.cumulative_open_value,
            self.cumulative_open_fee,
            self.cumulative_close_size,
            self.cumulative_close_value,
            self.cumulative_close_fee,
            self.cumulative_liquidation_fee,
            self.liquidate_price,
        )
        _require_finite_decimals(*non_negative, self.funding_fee, self.cumulative_funding_fee)
        _require_finite_decimals(self.unrealized_pnl)
        if any(value < 0 for value in non_negative):
            raise ForwardEvidenceIntegrityError("Demo position non-negative fact is negative")
        if self.cumulative_open_size < self.size:
            raise ForwardEvidenceIntegrityError("Demo cumulative open size is below current size")
        if self.cumulative_close_size > self.cumulative_open_size:
            raise ForwardEvidenceIntegrityError("Demo cumulative close size exceeds opens")
        _require_timestamp_pair(self.created_time_ms, self.created_time, "position created_time")
        _require_timestamp_pair(self.updated_time_ms, self.updated_time, "position updated_time")
        if self.updated_time < self.created_time:
            raise ForwardEvidenceIntegrityError("Demo position chronology is invalid")
        expected_reasons: list[ForwardEvidenceStatus] = []
        if self.margin_type != "ISOLATED":
            expected_reasons.append(ForwardEvidenceStatus.DEMO_POSITION_REJECTED_NOT_ISOLATED)
        if self.is_auto_append_isolated_margin:
            expected_reasons.append(ForwardEvidenceStatus.DEMO_POSITION_REJECTED_AUTO_MARGIN)
        if self.margin_type == "ISOLATED" and (self.margin_size <= 0 or self.isolated_margin <= 0):
            raise ForwardEvidenceIntegrityError("isolated Demo margin facts must be positive")
        expected = tuple(expected_reasons)
        expected_status = (
            ForwardEvidenceStatus.DEMO_POSITION_EVIDENCE_QUALIFIED if not expected else expected[0]
        )
        if self.rejection_reasons != expected or self.eligibility_status != expected_status:
            raise ForwardEvidenceIntegrityError("Demo position eligibility is inconsistent")


@dataclass(frozen=True, slots=True, order=True)
class DemoOrderHistoryEvidence:
    order_id_hash: str
    client_order_id_hash: str
    symbol: str
    side: str
    position_side: str
    status: str
    order_type: str
    time_in_force: str
    price: Decimal
    original_quantity: Decimal
    executed_quantity: Decimal
    average_price: Decimal
    cumulative_quote: Decimal
    created_time_ms: int
    created_time: datetime
    updated_time_ms: int
    updated_time: datetime

    def __post_init__(self) -> None:
        _require_sha256(self.order_id_hash, "order id hash")
        _require_sha256(self.client_order_id_hash, "client order id hash")
        for value, name in (
            (self.symbol, "symbol"),
            (self.side, "side"),
            (self.position_side, "position_side"),
            (self.status, "status"),
            (self.order_type, "order_type"),
            (self.time_in_force, "time_in_force"),
        ):
            _require_text(value, f"Demo order {name}")
        values = (
            self.price,
            self.original_quantity,
            self.executed_quantity,
            self.average_price,
            self.cumulative_quote,
        )
        _require_finite_decimals(*values)
        if any(value < 0 for value in values):
            raise ForwardEvidenceIntegrityError("Demo order economic fact is negative")
        if self.executed_quantity > self.original_quantity:
            raise ForwardEvidenceIntegrityError("Demo executed quantity exceeds original")
        _require_timestamp_pair(self.created_time_ms, self.created_time, "order created_time")
        _require_timestamp_pair(self.updated_time_ms, self.updated_time, "order updated_time")
        if self.updated_time < self.created_time:
            raise ForwardEvidenceIntegrityError("Demo order chronology is invalid")


@dataclass(frozen=True, slots=True)
class WeexForwardEvidenceBundle:
    schema_version: str
    bundle_version: str
    captured_at: datetime
    source_code_commit: str
    endpoint_contract_version: str
    changelog_reference: str
    source_observations: tuple[SourceObservation, ...]
    public_contracts: tuple[PublicContractEvidence, ...]
    premium_indices: tuple[PremiumIndexEvidence, ...]
    current_marks: tuple[CurrentMarkEvidence, ...]
    mark_klines: tuple[MarkKlineEvidence, ...]
    demo_balances: tuple[DemoBalanceEvidence, ...]
    demo_positions: tuple[DemoPositionEvidence, ...]
    demo_order_history: tuple[DemoOrderHistoryEvidence, ...]
    statuses: tuple[ForwardEvidenceStatus, ...]
    demo_symbol_mapping_status: ForwardEvidenceStatus
    public_contract_snapshot_sha256: str
    point_in_time_scope: str
    historical_authority_claimed: bool
    integrity_seal: str

    def validate(self) -> None:
        if self.schema_version != FORWARD_EVIDENCE_SCHEMA_VERSION:
            raise ForwardEvidenceIntegrityError("unknown forward evidence schema")
        require_utc(self.captured_at, field="evidence captured_at")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise ForwardEvidenceIntegrityError("source code commit is not an exact SHA")
        if self.endpoint_contract_version != WEEX_V3_CONTRACT_VERSION:
            raise ForwardEvidenceIntegrityError("endpoint contract version is invalid")
        if self.changelog_reference != WEEX_DEMO_CHANGELOG_REFERENCE:
            raise ForwardEvidenceIntegrityError("Demo changelog provenance is invalid")
        if self.historical_authority_claimed:
            raise ForwardEvidenceIntegrityError(
                "current evidence cannot claim historical authority"
            )
        _require_text(self.point_in_time_scope, "point_in_time_scope")
        _require_sha256(self.public_contract_snapshot_sha256, "contract snapshot digest")
        _require_canonical_unique(
            self.source_observations, key=lambda item: (item.endpoint, item.parameters)
        )
        _require_canonical_unique(self.public_contracts, key=lambda item: item.symbol)
        _require_canonical_unique(self.premium_indices, key=lambda item: item.symbol)
        _require_canonical_unique(self.current_marks, key=lambda item: item.symbol)
        _require_canonical_unique(
            self.mark_klines, key=lambda item: (item.symbol, item.open_time_ms)
        )
        _require_canonical_unique(self.demo_balances, key=lambda item: item.asset)
        _require_canonical_unique(self.demo_positions, key=lambda item: item.evidence_id)
        _require_canonical_unique(self.demo_order_history, key=lambda item: item.order_id_hash)
        if tuple(item.symbol for item in self.public_contracts) != ("BTCUSDT", "ETHUSDT"):
            raise ForwardEvidenceIntegrityError("public contract set is incomplete")
        if tuple(item.symbol for item in self.premium_indices) != ("BTCUSDT", "ETHUSDT"):
            raise ForwardEvidenceIntegrityError("premium-index set is incomplete")
        if tuple(item.symbol for item in self.current_marks) != ("BTCUSDT", "ETHUSDT"):
            raise ForwardEvidenceIntegrityError("current-MARK set is incomplete")
        if not self.statuses or self.statuses != tuple(dict.fromkeys(self.statuses)):
            raise ForwardEvidenceIntegrityError("evidence statuses are empty or duplicated")
        if self.statuses[0] != ForwardEvidenceStatus.PUBLIC_CURRENT_EVIDENCE_QUALIFIED:
            raise ForwardEvidenceIntegrityError("public evidence qualification is missing")
        if self.demo_symbol_mapping_status != ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED:
            raise ForwardEvidenceIntegrityError(
                "Demo symbol mapping must remain explicitly unresolved"
            )
        demo_unavailable = ForwardEvidenceStatus.DEMO_CREDENTIALS_UNAVAILABLE in self.statuses
        if demo_unavailable and (
            self.demo_balances or self.demo_positions or self.demo_order_history
        ):
            raise ForwardEvidenceIntegrityError("unavailable Demo evidence cannot contain samples")
        ready = ForwardEvidenceStatus.FORWARD_CALIBRATION_READY in self.statuses
        blocked = ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED in self.statuses
        if ready == blocked:
            raise ForwardEvidenceIntegrityError("exactly one calibration disposition is required")
        if ready:
            raise ForwardEvidenceIntegrityError(
                "Issue #130 cannot resolve Demo symbol mapping or declare calibration ready"
            )
        expected_version = f"weex-v3-forward-{_bundle_identity_sha(self)[:24]}"
        if self.bundle_version != expected_version:
            raise ForwardEvidenceIntegrityError("bundle version does not match evidence identity")
        expected_seal = _integrity_sha(self)
        if not hmac.compare_digest(self.integrity_seal, expected_seal):
            raise ForwardEvidenceIntegrityError("forward evidence integrity seal mismatch")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


@dataclass(frozen=True, slots=True)
class ForwardEvidenceManifest:
    schema_version: str
    bundle_version: str
    source_code_commit: str
    captured_at: datetime
    artifact_path: str
    artifact_sha256: str
    artifact_size_bytes: int

    def validate(self) -> None:
        if self.schema_version != FORWARD_EVIDENCE_MANIFEST_SCHEMA_VERSION:
            raise CorpusManifestError("unknown forward evidence manifest schema")
        if not self.bundle_version.startswith("weex-v3-forward-"):
            raise CorpusManifestError("forward evidence bundle version is invalid")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise CorpusManifestError("forward evidence code SHA is invalid")
        require_utc(self.captured_at, field="manifest captured_at")
        if self.artifact_path != "bundle.json":
            raise CorpusManifestError("forward evidence artifact path is invalid")
        _require_sha256(self.artifact_sha256, "artifact_sha256")
        if type(self.artifact_size_bytes) is not int or self.artifact_size_bytes <= 0:
            raise CorpusManifestError("forward evidence artifact size is invalid")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


class WeexPublicCurrentClient:
    def __init__(self, transport: GetTransport | None = None) -> None:
        self._transport = transport or UrlLibGetTransport()

    def request(
        self, method: str, path: str, parameters: Mapping[str, str] | None = None
    ) -> tuple[object, SourceObservation]:
        if method != "GET" or path not in PUBLIC_GET_PATHS:
            raise UnsafeWeexRouteError("public collector route is not an approved GET")
        canonical = _canonical_parameters(parameters or {})
        query = urllib.parse.urlencode(canonical)
        payload = self._transport.get(
            base_url=WEEX_V3_BASE_URL,
            path=path,
            query=query,
            headers={"User-Agent": "adaptive-trading-bot-forward-evidence/1"},
        )
        return payload, SourceObservation(path, canonical, _response_sha(payload))


class WeexDemoReadOnlyClient:
    def __init__(
        self,
        credentials: WeexDemoCredentials,
        transport: GetTransport | None = None,
        *,
        timestamp_ms: Callable[[], int] | None = None,
    ) -> None:
        self._credentials = credentials
        self._transport = transport or UrlLibGetTransport()
        self._timestamp_ms = timestamp_ms or (lambda: int(datetime.now(tz=UTC).timestamp() * 1000))

    def request(
        self, method: str, path: str, parameters: Mapping[str, str] | None = None
    ) -> tuple[object, SourceObservation]:
        if method != "GET" or path not in DEMO_GET_PATHS:
            raise UnsafeWeexRouteError("Demo collector route is not an approved read-only GET")
        canonical = _canonical_parameters(parameters or {})
        query = urllib.parse.urlencode(canonical)
        timestamp = str(self._timestamp_ms())
        signature = sign_weex_get(
            self._credentials.secret_key, timestamp=timestamp, path=path, query=query
        )
        headers = {
            "ACCESS-KEY": self._credentials.api_key.reveal_for_signing(),
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self._credentials.passphrase.reveal_for_signing(),
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "User-Agent": "adaptive-trading-bot-demo-read-only-evidence/1",
        }
        payload = self._transport.get(
            base_url=WEEX_V3_BASE_URL, path=path, query=query, headers=headers
        )
        return payload, SourceObservation(path, canonical, _response_sha(payload))


def sign_weex_get(secret: SecretValue, *, timestamp: str, path: str, query: str) -> str:
    if not timestamp.isascii() or not timestamp.isdigit():
        raise ValueError("signature timestamp must be integer milliseconds")
    if path not in DEMO_GET_PATHS:
        raise UnsafeWeexRouteError("signature path is not an approved Demo GET")
    message = f"{timestamp}GET{path}{f'?{query}' if query else ''}"
    digest = hmac.new(
        secret.reveal_for_signing().encode(), message.encode(), digestmod="sha256"
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class WeexForwardEvidenceCollector:
    def __init__(
        self,
        public_client: WeexPublicCurrentClient,
        demo_client: WeexDemoReadOnlyClient | None,
    ) -> None:
        self._public = public_client
        self._demo = demo_client

    def capture(
        self,
        *,
        captured_at: datetime,
        source_code_commit: str,
        mark_kline_limit: int = 0,
    ) -> WeexForwardEvidenceBundle:
        captured_at = require_utc(captured_at, field="capture time")
        if not _CODE_SHA.fullmatch(source_code_commit):
            raise ForwardEvidenceIntegrityError("capture requires an exact code SHA")
        if type(mark_kline_limit) is not int or not 0 <= mark_kline_limit <= 12:
            raise ForwardEvidenceIntegrityError("MARK-kline snapshot limit must be 0..12")

        observations: list[SourceObservation] = []
        exchange_raw, observation = self._public.request("GET", EXCHANGE_INFO_PATH)
        observations.append(observation)
        contracts = _parse_contracts(exchange_raw)
        premiums: list[PremiumIndexEvidence] = []
        marks: list[CurrentMarkEvidence] = []
        klines: list[MarkKlineEvidence] = []
        for symbol in ("BTCUSDT", "ETHUSDT"):
            raw, observation = self._public.request("GET", PREMIUM_INDEX_PATH, {"symbol": symbol})
            observations.append(observation)
            premiums.append(_parse_premium(raw, symbol))
            raw, observation = self._public.request(
                "GET", SYMBOL_PRICE_PATH, {"priceType": "MARK", "symbol": symbol}
            )
            observations.append(observation)
            marks.append(_parse_mark(raw, symbol))
            if mark_kline_limit:
                raw, observation = self._public.request(
                    "GET",
                    MARK_KLINES_PATH,
                    {"interval": "5m", "limit": str(mark_kline_limit), "symbol": symbol},
                )
                observations.append(observation)
                klines.extend(_parse_mark_klines(raw, symbol))

        balances: tuple[DemoBalanceEvidence, ...] = ()
        positions: tuple[DemoPositionEvidence, ...] = ()
        orders: tuple[DemoOrderHistoryEvidence, ...] = ()
        statuses: list[ForwardEvidenceStatus] = [
            ForwardEvidenceStatus.PUBLIC_CURRENT_EVIDENCE_QUALIFIED
        ]
        if self._demo is None:
            statuses.extend(
                (
                    ForwardEvidenceStatus.DEMO_CREDENTIALS_UNAVAILABLE,
                    ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED,
                    ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED,
                )
            )
        else:
            balance_raw, observation = self._demo.request("GET", DEMO_BALANCE_PATH)
            observations.append(observation)
            position_raw, observation = self._demo.request("GET", DEMO_POSITIONS_PATH)
            observations.append(observation)
            order_raw, observation = self._demo.request(
                "GET", DEMO_ORDER_HISTORY_PATH, {"limit": "100", "page": "0"}
            )
            observations.append(observation)
            balances = _parse_demo_balances(balance_raw)
            positions = _parse_demo_positions(position_raw)
            orders = _parse_demo_orders(order_raw)
            statuses.append(ForwardEvidenceStatus.DEMO_SCHEMA_QUALIFIED)
            if not positions:
                statuses.append(ForwardEvidenceStatus.DEMO_NO_POSITIONS_OBSERVED)
            for position in positions:
                if position.eligibility_status not in statuses:
                    statuses.append(position.eligibility_status)
            statuses.extend(
                (
                    ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED,
                    ForwardEvidenceStatus.FORWARD_CALIBRATION_BLOCKED,
                )
            )

        ordered_observations = tuple(
            sorted(observations, key=lambda item: (item.endpoint, item.parameters))
        )
        seed = WeexForwardEvidenceBundle(
            schema_version=FORWARD_EVIDENCE_SCHEMA_VERSION,
            bundle_version="pending",
            captured_at=captured_at,
            source_code_commit=source_code_commit,
            endpoint_contract_version=WEEX_V3_CONTRACT_VERSION,
            changelog_reference=WEEX_DEMO_CHANGELOG_REFERENCE,
            source_observations=ordered_observations,
            public_contracts=tuple(sorted(contracts)),
            premium_indices=tuple(sorted(premiums)),
            current_marks=tuple(sorted(marks)),
            mark_klines=tuple(sorted(klines)),
            demo_balances=balances,
            demo_positions=positions,
            demo_order_history=orders,
            statuses=tuple(statuses),
            demo_symbol_mapping_status=(ForwardEvidenceStatus.DEMO_SYMBOL_MAPPING_UNRESOLVED),
            public_contract_snapshot_sha256=_response_sha(exchange_raw),
            point_in_time_scope=(
                "Current/forward WEEX V3 evidence captured at one instant; freshness begins "
                "at captured_at and no historical applicability is asserted."
            ),
            historical_authority_claimed=False,
            integrity_seal="pending",
        )
        version = f"weex-v3-forward-{_bundle_identity_sha(seed)[:24]}"
        versioned = _replace_bundle(seed, bundle_version=version)
        sealed = _replace_bundle(versioned, integrity_seal=_integrity_sha(versioned))
        sealed.validate()
        return sealed


def write_evidence_bundle(
    bundle: WeexForwardEvidenceBundle, output_root: Path
) -> ForwardEvidenceManifest:
    bundle.validate()
    if output_root.exists():
        raise FileExistsError("forward evidence output root must not already exist")
    output_root.mkdir(parents=True)
    artifact = output_root / "bundle.json"
    artifact_bytes = (bundle.to_json() + "\n").encode()
    _atomic_write(artifact, artifact_bytes)
    manifest = ForwardEvidenceManifest(
        schema_version=FORWARD_EVIDENCE_MANIFEST_SCHEMA_VERSION,
        bundle_version=bundle.bundle_version,
        source_code_commit=bundle.source_code_commit,
        captured_at=bundle.captured_at,
        artifact_path="bundle.json",
        artifact_sha256=sha256(artifact_bytes).hexdigest(),
        artifact_size_bytes=len(artifact_bytes),
    )
    _atomic_write(output_root / "manifest.json", (manifest.to_json() + "\n").encode())
    return manifest


class LockedWeexForwardEvidenceBundle:
    """Network-free reader for one exact project-qualified evidence bundle."""

    def __init__(self, root: Path, *, bundle_version: str) -> None:
        self._root = root
        manifest_path = root / "manifest.json"
        raw = _load_json_file(manifest_path)
        self._manifest = _parse_manifest(raw)
        if self._manifest.bundle_version != bundle_version:
            raise CorpusManifestError("requested forward evidence version is unknown")
        accepted = QUALIFIED_FORWARD_EVIDENCE_MANIFEST_SHA256.get(bundle_version)
        if accepted is None:
            raise CorpusManifestError("forward evidence version is not project-qualified")
        if _file_sha256(manifest_path) != accepted:
            raise CorpusHashMismatchError("forward evidence manifest hash mismatch")
        self._verify_artifact()

    @property
    def manifest(self) -> ForwardEvidenceManifest:
        return self._manifest

    def read(self) -> WeexForwardEvidenceBundle:
        self._verify_artifact()
        bundle = _parse_bundle(_load_json_file(self._root / self._manifest.artifact_path))
        if bundle.bundle_version != self._manifest.bundle_version:
            raise CorpusManifestError("bundle identity does not match manifest")
        if bundle.source_code_commit != self._manifest.source_code_commit:
            raise CorpusManifestError("bundle code provenance does not match manifest")
        if bundle.captured_at != self._manifest.captured_at:
            raise CorpusManifestError("bundle capture time does not match manifest")
        return bundle

    def _verify_artifact(self) -> None:
        path = self._root / self._manifest.artifact_path
        if (
            not path.is_file()
            or path.stat().st_size != self._manifest.artifact_size_bytes
            or _file_sha256(path) != self._manifest.artifact_sha256
        ):
            raise CorpusHashMismatchError("forward evidence artifact hash mismatch")


def _parse_contracts(payload: object) -> tuple[PublicContractEvidence, ...]:
    value = _unwrap_payload(payload)
    if isinstance(value, dict):
        items = value.get("symbols")
        if not isinstance(items, list):
            raise ForwardEvidenceIntegrityError("exchangeInfo payload must contain a symbols array")
    elif isinstance(value, list):
        items = value
    else:
        raise ForwardEvidenceIntegrityError("exchangeInfo payload must be an object or array")
    selected: list[PublicContractEvidence] = []
    for item in items:
        if not isinstance(item, dict):
            raise ForwardEvidenceIntegrityError("exchangeInfo item must be an object")
        symbol = item.get("symbol")
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            continue
        selected.append(
            PublicContractEvidence(
                symbol=cast(str, symbol),
                base_asset=_strict_text(item.get("baseAsset"), "baseAsset"),
                quote_asset=_strict_text(item.get("quoteAsset"), "quoteAsset"),
                margin_asset=_strict_text(item.get("marginAsset"), "marginAsset"),
                contract_type=_strict_text(item.get("contractType"), "contractType"),
                contract_val=_strict_decimal(item.get("contractVal"), "contractVal"),
                min_leverage=_strict_decimal(item.get("minLeverage"), "minLeverage"),
                max_leverage=_strict_decimal(item.get("maxLeverage"), "maxLeverage"),
                price_precision=_strict_integer(item.get("pricePrecision"), "pricePrecision"),
                quantity_precision=_strict_integer(
                    item.get("quantityPrecision"), "quantityPrecision"
                ),
                min_order_size=_strict_decimal(item.get("minOrderSize"), "minOrderSize"),
                max_order_size=_strict_decimal(item.get("maxOrderSize"), "maxOrderSize"),
                max_position_size=_strict_decimal(item.get("maxPositionSize"), "maxPositionSize"),
                market_open_limit_size=_strict_decimal(
                    item.get("marketOpenLimitSize"), "marketOpenLimitSize"
                ),
                maker_fee_rate=_strict_decimal(item.get("makerFeeRate"), "makerFeeRate"),
                taker_fee_rate=_strict_decimal(item.get("takerFeeRate"), "takerFeeRate"),
                api_maker_fee_rate=_optional_decimal(
                    item.get("apiMakerFeeRate"), "apiMakerFeeRate"
                ),
                api_taker_fee_rate=_optional_decimal(
                    item.get("apiTakerFeeRate"), "apiTakerFeeRate"
                ),
            )
        )
    result = tuple(sorted(selected))
    if tuple(item.symbol for item in result) != ("BTCUSDT", "ETHUSDT"):
        raise ForwardEvidenceIntegrityError("exchangeInfo omitted a required V1 contract")
    return result


def _parse_premium(payload: object, expected_symbol: str) -> PremiumIndexEvidence:
    items = _payload_list(payload, "premiumIndex")
    if len(items) != 1 or not isinstance(items[0], dict):
        raise ForwardEvidenceIntegrityError("premiumIndex must return exactly one object")
    item = items[0]
    symbol = _strict_text(item.get("symbol"), "premium symbol")
    if symbol != expected_symbol:
        raise ForwardEvidenceIntegrityError("premiumIndex symbol mismatch")
    next_ms = _strict_integer(item.get("nextFundingTime"), "nextFundingTime")
    venue_ms = _strict_integer(item.get("time"), "premium time")
    return PremiumIndexEvidence(
        symbol=symbol,
        mark_price=_strict_decimal(item.get("markPrice"), "premium markPrice"),
        index_price=_strict_decimal(item.get("indexPrice"), "premium indexPrice"),
        last_funding_rate=_strict_decimal(item.get("lastFundingRate"), "lastFundingRate"),
        forecast_funding_rate=_strict_decimal(
            item.get("forecastFundingRate"), "forecastFundingRate"
        ),
        interest_rate=_strict_decimal(item.get("interestRate"), "interestRate"),
        next_funding_time_ms=next_ms,
        next_funding_time=_from_milliseconds(next_ms),
        venue_time_ms=venue_ms,
        venue_time=_from_milliseconds(venue_ms),
        collect_cycle_minutes=_strict_integer(item.get("collectCycle"), "collectCycle"),
    )


def _parse_mark(payload: object, expected_symbol: str) -> CurrentMarkEvidence:
    item = _payload_object(payload, "symbolPrice")
    symbol = _strict_text(item.get("symbol"), "MARK symbol")
    if symbol != expected_symbol:
        raise ForwardEvidenceIntegrityError("current MARK symbol mismatch")
    venue_ms = _strict_integer(item.get("time"), "MARK time")
    return CurrentMarkEvidence(
        symbol=symbol,
        price=_strict_decimal(item.get("price"), "MARK price"),
        venue_time_ms=venue_ms,
        venue_time=_from_milliseconds(venue_ms),
    )


def _parse_mark_klines(payload: object, symbol: str) -> tuple[MarkKlineEvidence, ...]:
    items = _payload_list(payload, "markPriceKlines")
    result: list[MarkKlineEvidence] = []
    for row in items:
        if not isinstance(row, list) or len(row) != 11:
            raise ForwardEvidenceIntegrityError("MARK kline row must contain 11 fields")
        open_ms = _strict_integer(row[0], "MARK kline open time")
        close_ms = _strict_integer(row[6], "MARK kline close time")
        result.append(
            MarkKlineEvidence(
                symbol=symbol,
                open_time_ms=open_ms,
                open_time=_from_milliseconds(open_ms),
                close_time_ms=close_ms,
                close_time=_from_milliseconds(close_ms),
                open=_strict_decimal(row[1], "MARK kline open"),
                high=_strict_decimal(row[2], "MARK kline high"),
                low=_strict_decimal(row[3], "MARK kline low"),
                close=_strict_decimal(row[4], "MARK kline close"),
            )
        )
    ordered = tuple(sorted(result))
    if len({item.open_time_ms for item in ordered}) != len(ordered):
        raise ForwardEvidenceIntegrityError("MARK kline snapshot contains duplicates")
    return ordered


def _parse_demo_balances(payload: object) -> tuple[DemoBalanceEvidence, ...]:
    items = _payload_list(payload, "Demo balance")
    result = tuple(
        sorted(
            DemoBalanceEvidence(
                asset=_strict_text(item.get("asset"), "Demo balance asset"),
                balance=_strict_decimal(item.get("balance"), "Demo balance"),
                available_balance=_strict_decimal(
                    item.get("availableBalance"), "Demo availableBalance"
                ),
                frozen=_strict_decimal(item.get("frozen"), "Demo frozen"),
                unrealized_pnl=_strict_decimal(
                    item.get("unrealizePnl"), "Demo balance unrealizePnl"
                ),
            )
            for item in _object_items(items, "Demo balance")
        )
    )
    if len({item.asset for item in result}) != len(result):
        raise ForwardEvidenceIntegrityError("Demo balances contain duplicate assets")
    return result


def _parse_demo_positions(payload: object) -> tuple[DemoPositionEvidence, ...]:
    items = _payload_list(payload, "Demo positions")
    result: list[DemoPositionEvidence] = []
    for item in _object_items(items, "Demo position"):
        raw_id = _strict_identifier(item.get("id"), "Demo position id")
        symbol = _strict_text(item.get("symbol"), "Demo position symbol")
        created_ms = _strict_integer(item.get("createdTime"), "position createdTime")
        updated_ms = _strict_integer(item.get("updatedTime"), "position updatedTime")
        margin_type = _strict_text(item.get("marginType"), "marginType")
        auto_margin = item.get("isAutoAppendIsolatedMargin")
        if type(auto_margin) is not bool:
            raise ForwardEvidenceIntegrityError("isAutoAppendIsolatedMargin must be boolean")
        reasons: list[ForwardEvidenceStatus] = []
        if margin_type != "ISOLATED":
            reasons.append(ForwardEvidenceStatus.DEMO_POSITION_REJECTED_NOT_ISOLATED)
        if auto_margin:
            reasons.append(ForwardEvidenceStatus.DEMO_POSITION_REJECTED_AUTO_MARGIN)
        eligibility = (
            ForwardEvidenceStatus.DEMO_POSITION_EVIDENCE_QUALIFIED if not reasons else reasons[0]
        )
        result.append(
            DemoPositionEvidence(
                evidence_id=_identifier_hash(
                    "position", raw_id, symbol, str(created_ms), str(updated_ms)
                ),
                asset=_strict_text(item.get("asset"), "Demo position asset"),
                symbol=symbol,
                side=_strict_text(item.get("side"), "Demo position side"),
                margin_type=margin_type,
                separated_mode=_strict_text(item.get("separatedMode"), "Demo separatedMode"),
                leverage=_strict_decimal(item.get("leverage"), "Demo leverage"),
                size=_strict_decimal(item.get("size"), "Demo size"),
                open_value=_strict_decimal(item.get("openValue"), "Demo openValue"),
                open_fee=_strict_decimal(item.get("openFee"), "Demo openFee"),
                funding_fee=_strict_decimal(item.get("fundingFee"), "Demo fundingFee"),
                margin_size=_strict_decimal(item.get("marginSize"), "Demo marginSize"),
                isolated_margin=_strict_decimal(item.get("isolatedMargin"), "Demo isolatedMargin"),
                is_auto_append_isolated_margin=auto_margin,
                cumulative_open_size=_strict_decimal(item.get("cumOpenSize"), "Demo cumOpenSize"),
                cumulative_open_value=_strict_decimal(
                    item.get("cumOpenValue"), "Demo cumOpenValue"
                ),
                cumulative_open_fee=_strict_decimal(item.get("cumOpenFee"), "Demo cumOpenFee"),
                cumulative_close_size=_strict_decimal(
                    item.get("cumCloseSize"), "Demo cumCloseSize"
                ),
                cumulative_close_value=_strict_decimal(
                    item.get("cumCloseValue"), "Demo cumCloseValue"
                ),
                cumulative_close_fee=_strict_decimal(item.get("cumCloseFee"), "Demo cumCloseFee"),
                cumulative_funding_fee=_strict_decimal(
                    item.get("cumFundingFee"), "Demo cumFundingFee"
                ),
                cumulative_liquidation_fee=_strict_decimal(
                    item.get("cumLiquidateFee"), "Demo cumLiquidateFee"
                ),
                created_match_sequence_hash=_identifier_hash(
                    "created-sequence",
                    _strict_identifier(
                        item.get("createdMatchSequenceId"), "createdMatchSequenceId"
                    ),
                ),
                updated_match_sequence_hash=_identifier_hash(
                    "updated-sequence",
                    _strict_identifier(
                        item.get("updatedMatchSequenceId"), "updatedMatchSequenceId"
                    ),
                ),
                created_time_ms=created_ms,
                created_time=_from_milliseconds(created_ms),
                updated_time_ms=updated_ms,
                updated_time=_from_milliseconds(updated_ms),
                unrealized_pnl=_strict_decimal(item.get("unrealizePnl"), "Demo unrealizePnl"),
                liquidate_price=_strict_decimal(item.get("liquidatePrice"), "Demo liquidatePrice"),
                eligibility_status=eligibility,
                rejection_reasons=tuple(reasons),
            )
        )
    ordered = tuple(sorted(result))
    if len({item.evidence_id for item in ordered}) != len(ordered):
        raise ForwardEvidenceIntegrityError("Demo positions contain duplicate identities")
    return ordered


def _parse_demo_orders(payload: object) -> tuple[DemoOrderHistoryEvidence, ...]:
    items = _payload_list(payload, "Demo order history")
    result: list[DemoOrderHistoryEvidence] = []
    for item in _object_items(items, "Demo order"):
        order_id = _strict_identifier(item.get("orderId"), "Demo orderId")
        client_id = _strict_identifier(item.get("clientOrderId"), "Demo clientOrderId")
        created_ms = _strict_integer(item.get("time"), "Demo order time")
        updated_ms = _strict_integer(item.get("updateTime"), "Demo order updateTime")
        result.append(
            DemoOrderHistoryEvidence(
                order_id_hash=_identifier_hash("order", order_id),
                client_order_id_hash=_identifier_hash("client-order", client_id),
                symbol=_strict_text(item.get("symbol"), "Demo order symbol"),
                side=_strict_text(item.get("side"), "Demo order side"),
                position_side=_strict_text(item.get("positionSide"), "Demo order positionSide"),
                status=_strict_text(item.get("status"), "Demo order status"),
                order_type=_strict_text(item.get("type"), "Demo order type"),
                time_in_force=_strict_text(item.get("timeInForce"), "Demo order timeInForce"),
                price=_strict_decimal(item.get("price"), "Demo order price"),
                original_quantity=_strict_decimal(item.get("origQty"), "Demo order origQty"),
                executed_quantity=_strict_decimal(
                    item.get("executedQty"), "Demo order executedQty"
                ),
                average_price=_strict_decimal(item.get("avgPrice"), "Demo order avgPrice"),
                cumulative_quote=_strict_decimal(item.get("cumQuote"), "Demo order cumQuote"),
                created_time_ms=created_ms,
                created_time=_from_milliseconds(created_ms),
                updated_time_ms=updated_ms,
                updated_time=_from_milliseconds(updated_ms),
            )
        )
    ordered = tuple(sorted(result))
    if len({item.order_id_hash for item in ordered}) != len(ordered):
        raise ForwardEvidenceIntegrityError("Demo order history contains duplicates")
    return ordered


def _parse_manifest(raw: object) -> ForwardEvidenceManifest:
    item = _require_mapping(raw, "forward evidence manifest")
    try:
        manifest = ForwardEvidenceManifest(
            schema_version=_strict_text(item["schema_version"], "manifest schema_version"),
            bundle_version=_strict_text(item["bundle_version"], "manifest bundle_version"),
            source_code_commit=_strict_text(
                item["source_code_commit"], "manifest source_code_commit"
            ),
            captured_at=_parse_utc(item["captured_at"], "manifest captured_at"),
            artifact_path=_strict_text(item["artifact_path"], "manifest artifact_path"),
            artifact_sha256=_strict_text(item["artifact_sha256"], "manifest artifact_sha256"),
            artifact_size_bytes=_strict_integer(
                item["artifact_size_bytes"], "manifest artifact_size_bytes"
            ),
        )
    except KeyError as error:
        raise CorpusManifestError("forward evidence manifest is incomplete") from error
    manifest.validate()
    return manifest


def _parse_bundle(raw: object) -> WeexForwardEvidenceBundle:
    item = _require_mapping(raw, "forward evidence bundle")
    try:
        bundle = WeexForwardEvidenceBundle(
            schema_version=_strict_text(item["schema_version"], "bundle schema_version"),
            bundle_version=_strict_text(item["bundle_version"], "bundle bundle_version"),
            captured_at=_parse_utc(item["captured_at"], "bundle captured_at"),
            source_code_commit=_strict_text(
                item["source_code_commit"], "bundle source_code_commit"
            ),
            endpoint_contract_version=_strict_text(
                item["endpoint_contract_version"], "endpoint_contract_version"
            ),
            changelog_reference=_strict_text(item["changelog_reference"], "changelog_reference"),
            source_observations=tuple(
                SourceObservation(
                    endpoint=_strict_text(value["endpoint"], "observation endpoint"),
                    parameters=_parameter_pairs(value["parameters"]),
                    response_sha256=_strict_text(value["response_sha256"], "response_sha256"),
                )
                for value in _mapping_items(item["source_observations"])
            ),
            public_contracts=tuple(
                _dataclass_from_json(PublicContractEvidence, value)
                for value in _mapping_items(item["public_contracts"])
            ),
            premium_indices=tuple(
                _premium_from_json(value) for value in _mapping_items(item["premium_indices"])
            ),
            current_marks=tuple(
                _mark_from_json(value) for value in _mapping_items(item["current_marks"])
            ),
            mark_klines=tuple(
                _kline_from_json(value) for value in _mapping_items(item["mark_klines"])
            ),
            demo_balances=tuple(
                _dataclass_from_json(DemoBalanceEvidence, value)
                for value in _mapping_items(item["demo_balances"])
            ),
            demo_positions=tuple(
                _position_from_json(value) for value in _mapping_items(item["demo_positions"])
            ),
            demo_order_history=tuple(
                _order_from_json(value) for value in _mapping_items(item["demo_order_history"])
            ),
            statuses=tuple(
                ForwardEvidenceStatus(value) for value in _string_sequence(item["statuses"])
            ),
            demo_symbol_mapping_status=ForwardEvidenceStatus(
                _strict_text(item["demo_symbol_mapping_status"], "mapping status")
            ),
            public_contract_snapshot_sha256=_strict_text(
                item["public_contract_snapshot_sha256"], "contract snapshot digest"
            ),
            point_in_time_scope=_strict_text(item["point_in_time_scope"], "point_in_time_scope"),
            historical_authority_claimed=_strict_bool(
                item["historical_authority_claimed"], "historical_authority_claimed"
            ),
            integrity_seal=_strict_text(item["integrity_seal"], "integrity_seal"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ForwardEvidenceIntegrityError("forward evidence bundle cannot be decoded") from error
    bundle.validate()
    return bundle


def _premium_from_json(value: Mapping[str, object]) -> PremiumIndexEvidence:
    return PremiumIndexEvidence(
        symbol=_strict_text(value["symbol"], "premium symbol"),
        mark_price=_strict_decimal(value["mark_price"], "mark_price"),
        index_price=_strict_decimal(value["index_price"], "index_price"),
        last_funding_rate=_strict_decimal(value["last_funding_rate"], "last_funding_rate"),
        forecast_funding_rate=_strict_decimal(
            value["forecast_funding_rate"], "forecast_funding_rate"
        ),
        interest_rate=_strict_decimal(value["interest_rate"], "interest_rate"),
        next_funding_time_ms=_strict_integer(value["next_funding_time_ms"], "next_funding_time_ms"),
        next_funding_time=_parse_utc(value["next_funding_time"], "next_funding_time"),
        venue_time_ms=_strict_integer(value["venue_time_ms"], "venue_time_ms"),
        venue_time=_parse_utc(value["venue_time"], "venue_time"),
        collect_cycle_minutes=_strict_integer(
            value["collect_cycle_minutes"], "collect_cycle_minutes"
        ),
    )


def _mark_from_json(value: Mapping[str, object]) -> CurrentMarkEvidence:
    return CurrentMarkEvidence(
        symbol=_strict_text(value["symbol"], "MARK symbol"),
        price=_strict_decimal(value["price"], "MARK price"),
        venue_time_ms=_strict_integer(value["venue_time_ms"], "venue_time_ms"),
        venue_time=_parse_utc(value["venue_time"], "venue_time"),
    )


def _kline_from_json(value: Mapping[str, object]) -> MarkKlineEvidence:
    return MarkKlineEvidence(
        symbol=_strict_text(value["symbol"], "kline symbol"),
        open_time_ms=_strict_integer(value["open_time_ms"], "open_time_ms"),
        open_time=_parse_utc(value["open_time"], "open_time"),
        close_time_ms=_strict_integer(value["close_time_ms"], "close_time_ms"),
        close_time=_parse_utc(value["close_time"], "close_time"),
        open=_strict_decimal(value["open"], "open"),
        high=_strict_decimal(value["high"], "high"),
        low=_strict_decimal(value["low"], "low"),
        close=_strict_decimal(value["close"], "close"),
    )


def _position_from_json(value: Mapping[str, object]) -> DemoPositionEvidence:
    decimal_names = (
        "leverage",
        "size",
        "open_value",
        "open_fee",
        "funding_fee",
        "margin_size",
        "isolated_margin",
        "cumulative_open_size",
        "cumulative_open_value",
        "cumulative_open_fee",
        "cumulative_close_size",
        "cumulative_close_value",
        "cumulative_close_fee",
        "cumulative_funding_fee",
        "cumulative_liquidation_fee",
        "unrealized_pnl",
        "liquidate_price",
    )
    decimals = {name: _strict_decimal(value[name], name) for name in decimal_names}
    return DemoPositionEvidence(
        evidence_id=_strict_text(value["evidence_id"], "evidence_id"),
        asset=_strict_text(value["asset"], "asset"),
        symbol=_strict_text(value["symbol"], "symbol"),
        side=_strict_text(value["side"], "side"),
        margin_type=_strict_text(value["margin_type"], "margin_type"),
        separated_mode=_strict_text(value["separated_mode"], "separated_mode"),
        leverage=decimals["leverage"],
        size=decimals["size"],
        open_value=decimals["open_value"],
        open_fee=decimals["open_fee"],
        funding_fee=decimals["funding_fee"],
        margin_size=decimals["margin_size"],
        isolated_margin=decimals["isolated_margin"],
        is_auto_append_isolated_margin=_strict_bool(
            value["is_auto_append_isolated_margin"], "is_auto_append_isolated_margin"
        ),
        cumulative_open_size=decimals["cumulative_open_size"],
        cumulative_open_value=decimals["cumulative_open_value"],
        cumulative_open_fee=decimals["cumulative_open_fee"],
        cumulative_close_size=decimals["cumulative_close_size"],
        cumulative_close_value=decimals["cumulative_close_value"],
        cumulative_close_fee=decimals["cumulative_close_fee"],
        cumulative_funding_fee=decimals["cumulative_funding_fee"],
        cumulative_liquidation_fee=decimals["cumulative_liquidation_fee"],
        created_match_sequence_hash=_strict_text(
            value["created_match_sequence_hash"], "created_match_sequence_hash"
        ),
        updated_match_sequence_hash=_strict_text(
            value["updated_match_sequence_hash"], "updated_match_sequence_hash"
        ),
        created_time_ms=_strict_integer(value["created_time_ms"], "created_time_ms"),
        created_time=_parse_utc(value["created_time"], "created_time"),
        updated_time_ms=_strict_integer(value["updated_time_ms"], "updated_time_ms"),
        updated_time=_parse_utc(value["updated_time"], "updated_time"),
        unrealized_pnl=decimals["unrealized_pnl"],
        liquidate_price=decimals["liquidate_price"],
        eligibility_status=ForwardEvidenceStatus(
            _strict_text(value["eligibility_status"], "eligibility_status")
        ),
        rejection_reasons=tuple(
            ForwardEvidenceStatus(item) for item in _string_sequence(value["rejection_reasons"])
        ),
    )


def _order_from_json(value: Mapping[str, object]) -> DemoOrderHistoryEvidence:
    return DemoOrderHistoryEvidence(
        order_id_hash=_strict_text(value["order_id_hash"], "order_id_hash"),
        client_order_id_hash=_strict_text(value["client_order_id_hash"], "client_order_id_hash"),
        symbol=_strict_text(value["symbol"], "symbol"),
        side=_strict_text(value["side"], "side"),
        position_side=_strict_text(value["position_side"], "position_side"),
        status=_strict_text(value["status"], "status"),
        order_type=_strict_text(value["order_type"], "order_type"),
        time_in_force=_strict_text(value["time_in_force"], "time_in_force"),
        price=_strict_decimal(value["price"], "price"),
        original_quantity=_strict_decimal(value["original_quantity"], "original_quantity"),
        executed_quantity=_strict_decimal(value["executed_quantity"], "executed_quantity"),
        average_price=_strict_decimal(value["average_price"], "average_price"),
        cumulative_quote=_strict_decimal(value["cumulative_quote"], "cumulative_quote"),
        created_time_ms=_strict_integer(value["created_time_ms"], "created_time_ms"),
        created_time=_parse_utc(value["created_time"], "created_time"),
        updated_time_ms=_strict_integer(value["updated_time_ms"], "updated_time_ms"),
        updated_time=_parse_utc(value["updated_time"], "updated_time"),
    )


def _dataclass_from_json[T](cls: type[T], value: Mapping[str, object]) -> T:
    decimal_fields = {
        PublicContractEvidence: {
            "contract_val",
            "min_leverage",
            "max_leverage",
            "min_order_size",
            "max_order_size",
            "max_position_size",
            "market_open_limit_size",
            "maker_fee_rate",
            "taker_fee_rate",
            "api_maker_fee_rate",
            "api_taker_fee_rate",
        },
        DemoBalanceEvidence: {"balance", "available_balance", "frozen", "unrealized_pnl"},
    }.get(cls, set())
    kwargs: dict[str, object] = {}
    for field in fields(cls):  # type: ignore[arg-type]
        raw = value[field.name]
        if field.name in {"api_maker_fee_rate", "api_taker_fee_rate"} and raw is None:
            kwargs[field.name] = None
        elif field.name in decimal_fields:
            kwargs[field.name] = _strict_decimal(raw, field.name)
        elif field.name in {"price_precision", "quantity_precision"}:
            kwargs[field.name] = _strict_integer(raw, field.name)
        else:
            kwargs[field.name] = _strict_text(raw, field.name)
    return cls(**kwargs)


def _payload_list(payload: object, field: str) -> list[object]:
    value = _unwrap_payload(payload)
    if not isinstance(value, list):
        raise ForwardEvidenceIntegrityError(f"{field} payload must be an array")
    return value


def _payload_object(payload: object, field: str) -> Mapping[str, object]:
    value = _unwrap_payload(payload)
    return _require_mapping(value, f"{field} payload")


def _unwrap_payload(payload: object) -> object:
    if isinstance(payload, dict) and "data" in payload and "symbol" not in payload:
        return payload["data"]
    return payload


def _object_items(items: Sequence[object], field: str) -> tuple[Mapping[str, object], ...]:
    result: list[Mapping[str, object]] = []
    for item in items:
        result.append(_require_mapping(item, field))
    return tuple(result)


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise TypeError("canonical collection must be an array")
    return tuple(_require_mapping(item, "canonical collection item") for item in value)


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ForwardEvidenceIntegrityError("canonical string collection must be an array")
    return tuple(_strict_text(item, "canonical string item") for item in value)


def _parameter_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ForwardEvidenceIntegrityError("source parameters must be an array")
    result: list[tuple[str, str]] = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ForwardEvidenceIntegrityError("source parameter must be a key/value pair")
        result.append((_strict_text(pair[0], "parameter key"), _strict_text(pair[1], "parameter")))
    return tuple(result)


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ForwardEvidenceIntegrityError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _strict_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ForwardEvidenceIntegrityError(f"{field} must be an exact decimal token")
    if isinstance(value, str) and (not value or value.strip() != value):
        raise ForwardEvidenceIntegrityError(f"{field} is blank or padded")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ForwardEvidenceIntegrityError(f"{field} is not a decimal") from error
    if not result.is_finite():
        raise ForwardEvidenceIntegrityError(f"{field} must be finite")
    return result


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else _strict_decimal(value, field)


def _strict_integer(value: object, field: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ForwardEvidenceIntegrityError(f"{field} must be an integer token")


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ForwardEvidenceIntegrityError(f"{field} must be boolean")
    return value


def _strict_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not _CANONICAL_TEXT.fullmatch(value):
        raise ForwardEvidenceIntegrityError(f"{field} must be canonical non-empty text")
    return value


def _strict_identifier(value: object, field: str) -> str:
    if type(value) is int:
        return str(value)
    return _strict_text(value, field)


def _require_text(value: str, field: str) -> None:
    _strict_text(value, field)


def _require_public_symbol(symbol: str) -> None:
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ForwardEvidenceIntegrityError("symbol is outside the V1 public allowlist")


def _require_finite_decimals(*values: Decimal) -> None:
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ForwardEvidenceIntegrityError("economic values must be finite Decimals")


def _require_positive_decimals(*values: Decimal) -> None:
    _require_finite_decimals(*values)
    if any(value <= 0 for value in values):
        raise ForwardEvidenceIntegrityError("economic values must be positive")


def _require_timestamp_pair(milliseconds: int, value: datetime, field: str) -> None:
    if type(milliseconds) is not int or milliseconds <= 0:
        raise ForwardEvidenceIntegrityError(f"{field} milliseconds are invalid")
    normalized = require_utc(value, field=field)
    if normalized != _from_milliseconds(milliseconds):
        raise ForwardEvidenceIntegrityError(f"{field} datetime does not match milliseconds")


def _from_milliseconds(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError) as error:
        raise ForwardEvidenceIntegrityError("timestamp is outside the supported range") from error


def _parse_utc(value: object, field: str) -> datetime:
    text = _strict_text(value, field)
    try:
        return require_utc(datetime.fromisoformat(text.replace("Z", "+00:00")), field=field)
    except ValueError as error:
        raise ForwardEvidenceIntegrityError(f"{field} is not UTC") from error


def _iso(value: datetime) -> str:
    return require_utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _canonical_parameters(parameters: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for key, value in parameters.items():
        result.append((_strict_text(key, "parameter key"), _strict_text(value, "parameter")))
    return tuple(sorted(result))


def _response_sha(payload: object) -> str:
    return sha256(_canonical_json(_canonical_value(payload)).encode()).hexdigest()


def _identifier_hash(*parts: str) -> str:
    return sha256(_canonical_json(list(parts)).encode()).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if not _HEX_SHA256.fullmatch(value):
        raise ForwardEvidenceIntegrityError(f"{field} must be lowercase SHA-256")


def _require_canonical_unique[T](values: tuple[T, ...], *, key: Callable[[T], object]) -> None:
    keys = tuple(_canonical_json(_canonical_value(key(value))) for value in values)
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
        raise ForwardEvidenceIntegrityError("evidence collection is not canonical and unique")


def _canonical_value(value: object, *, excluded: frozenset[str] = frozenset()) -> object:
    if isinstance(value, (SecretValue, WeexDemoCredentials)):
        raise ForwardEvidenceIntegrityError("credentials cannot enter evidence serialization")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ForwardEvidenceIntegrityError("non-finite Decimal cannot be serialized")
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical_value(getattr(value, field.name), excluded=excluded)
            for field in fields(value)
            if field.name not in excluded
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item, excluded=excluded)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item, excluded=excluded) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise ForwardEvidenceIntegrityError("binary float cannot enter exact evidence")
    raise ForwardEvidenceIntegrityError(f"unsupported evidence type: {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bundle_identity_sha(bundle: WeexForwardEvidenceBundle) -> str:
    value = _canonical_value(bundle, excluded=frozenset({"bundle_version", "integrity_seal"}))
    return sha256(_canonical_json(value).encode()).hexdigest()


def _integrity_sha(bundle: WeexForwardEvidenceBundle) -> str:
    value = _canonical_value(bundle, excluded=frozenset({"integrity_seal"}))
    return sha256(_canonical_json(value).encode()).hexdigest()


def _replace_bundle(
    bundle: WeexForwardEvidenceBundle,
    *,
    bundle_version: str | None = None,
    integrity_seal: str | None = None,
) -> WeexForwardEvidenceBundle:
    return replace(
        bundle,
        bundle_version=(bundle.bundle_version if bundle_version is None else bundle_version),
        integrity_seal=(bundle.integrity_seal if integrity_seal is None else integrity_seal),
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusManifestError("forward evidence JSON cannot be decoded") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description="Capture one locked WEEX V3 forward evidence bundle")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-code-commit", required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--mark-kline-limit", type=int, default=0)
    arguments = parser.parse_args(argv)
    credentials = WeexDemoCredentials.from_environment()
    collector = WeexForwardEvidenceCollector(
        WeexPublicCurrentClient(),
        None if credentials is None else WeexDemoReadOnlyClient(credentials),
    )
    bundle = collector.capture(
        captured_at=_parse_utc(arguments.captured_at, "captured_at"),
        source_code_commit=arguments.source_code_commit,
        mark_kline_limit=arguments.mark_kline_limit,
    )
    manifest = write_evidence_bundle(bundle, arguments.output_root)
    print(
        _canonical_json(
            {
                "bundle_version": bundle.bundle_version,
                "manifest": _canonical_value(manifest),
                "statuses": [status.value for status in bundle.statuses],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
