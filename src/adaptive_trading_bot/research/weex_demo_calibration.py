"""One bounded, research-only WEEX Demo LONG calibration protocol.

This module is intentionally separate from the read-only forward-evidence
client.  Its only supported write operation is one typed opening order and one
typed cleanup order on the exact WEEX Demo endpoint.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from argparse import ArgumentParser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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

DEMO_ORDER_PATH: Final = "/capi/v3/sim/order"
DEMO_SYMBOL: Final = "BTCSUSDT"
OPEN_QUANTITY: Final = Decimal("0.01")
OPEN_CLIENT_ORDER_ID: Final = "atb-132-btcsusdt-long-open-v1"
CLEANUP_CLIENT_ORDER_ID: Final = "atb-132-btcsusdt-long-clean-v1"
CALIBRATION_SCHEMA_VERSION: Final = "weex-demo-long-calibration-v1"
CALIBRATION_MANIFEST_SCHEMA_VERSION: Final = "weex-demo-calibration-manifest-v1"
CALIBRATION_PROTOCOL_VERSION: Final = "issue-132-long-v1"
MINIMUM_CLEANUP_DELAY_SECONDS: Final = 65
MAX_POSITION_OBSERVATIONS: Final = 3
POSITION_OBSERVATION_INTERVAL_SECONDS: Final = 2

# Review-owned registry. A new accepted bundle requires an evidence PR.
LOCKED_DEMO_CALIBRATION_MANIFEST_SHA256: Final[Mapping[str, str]] = MappingProxyType(
    {
        "weex-demo-calibration-e300b52fe0e43bfe8581ca58": (
            "025159eb178a9e036c3fb36b531a2210cc45c5abfb7d66b153477521274285b6"
        )
    }
)

_CODE_SHA = re.compile(r"[0-9a-f]{40}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_CLIENT_ORDER_ID = re.compile(r"[.A-Z:/a-z0-9_-]{1,36}")
_VENUE_ERROR_CODE = re.compile(r"[A-Z0-9_-]{1,64}")


class DemoCalibrationError(ValueError):
    """Base failure for the bounded calibration protocol."""


class UnsafeDemoCalibrationOperation(DemoCalibrationError):
    """Raised before I/O for anything outside the exact order contract."""


class DemoCalibrationRequestError(DemoCalibrationError):
    """Sanitized network failure with no request secrets or untrusted data."""


class DemoCalibrationIntegrityError(DemoCalibrationError):
    """Raised when evidence is malformed, inconsistent, or tampered."""


class DemoCalibrationStatus(StrEnum):
    DEMO_CREDENTIALS_UNAVAILABLE = "DEMO_CREDENTIALS_UNAVAILABLE"
    DEMO_DIRTY_BASELINE_BLOCKED = "DEMO_DIRTY_BASELINE_BLOCKED"
    DEMO_BASELINE_INVALID = "DEMO_BASELINE_INVALID"
    DEMO_MARK_QUALIFIED = "DEMO_MARK_QUALIFIED"
    DEMO_MARK_UNAVAILABLE = "DEMO_MARK_UNAVAILABLE"
    DEMO_CONTRACT_FACTS_QUALIFIED = "DEMO_CONTRACT_FACTS_QUALIFIED"
    DEMO_CONTRACT_FACTS_UNAVAILABLE = "DEMO_CONTRACT_FACTS_UNAVAILABLE"
    DEMO_CALIBRATION_BLOCKED_NO_MARK = "DEMO_CALIBRATION_BLOCKED_NO_MARK"
    DEMO_RATE_LIMIT_WINDOW_BLOCKED = "DEMO_RATE_LIMIT_WINDOW_BLOCKED"
    DEMO_OPEN_ACCEPTED = "DEMO_OPEN_ACCEPTED"
    DEMO_OPEN_REJECTED = "DEMO_OPEN_REJECTED"
    DEMO_OPEN_RESPONSE_UNAVAILABLE = "DEMO_OPEN_RESPONSE_UNAVAILABLE"
    DEMO_OPEN_NOT_OBSERVED = "DEMO_OPEN_NOT_OBSERVED"
    DEMO_SAMPLE_REJECTED_NOT_ISOLATED = "DEMO_SAMPLE_REJECTED_NOT_ISOLATED"
    DEMO_SAMPLE_REJECTED_AUTO_MARGIN = "DEMO_SAMPLE_REJECTED_AUTO_MARGIN"
    DEMO_SAMPLE_REJECTED_ATTRIBUTION = "DEMO_SAMPLE_REJECTED_ATTRIBUTION"
    DEMO_SAMPLE_ELIGIBLE = "DEMO_SAMPLE_ELIGIBLE"
    CALIBRATION_MATCH = "CALIBRATION_MATCH"
    CALIBRATION_MISMATCH = "CALIBRATION_MISMATCH"
    CALIBRATION_UNRESOLVED = "CALIBRATION_UNRESOLVED"
    LIQUIDATION_TARGET_NOT_OBSERVED = "LIQUIDATION_TARGET_NOT_OBSERVED"
    LIQUIDATION_TARGET_OBSERVED = "LIQUIDATION_TARGET_OBSERVED"
    DEMO_CLEANUP_CONFIRMED = "DEMO_CLEANUP_CONFIRMED"
    DEMO_CLEANUP_UNCONFIRMED = "DEMO_CLEANUP_UNCONFIRMED"
    DEMO_LONG_CALIBRATION_COMPLETE = "DEMO_LONG_CALIBRATION_COMPLETE"
    DEMO_LONG_CALIBRATION_BLOCKED = "DEMO_LONG_CALIBRATION_BLOCKED"


class CalibrationOrderPhase(StrEnum):
    OPEN = "OPEN"
    CLEANUP = "CLEANUP"


class CalibrationComparisonKind(StrEnum):
    ORDER_QUANTITY = "ORDER_QUANTITY"
    ORDER_NOTIONAL = "ORDER_NOTIONAL"
    LEVERAGE_MARGIN = "LEVERAGE_MARGIN"
    MARK_UNREALIZED_PNL = "MARK_UNREALIZED_PNL"


class CalibrationDisposition(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class DemoCalibrationOrder:
    phase: CalibrationOrderPhase
    symbol: str
    side: str
    position_side: str
    order_type: str
    quantity: Decimal
    client_order_id: str
    observed_position_evidence_id: str | None

    def __post_init__(self) -> None:
        if self.symbol != DEMO_SYMBOL:
            raise UnsafeDemoCalibrationOperation("calibration symbol is not authorized")
        if self.position_side != "LONG" or self.order_type != "MARKET":
            raise UnsafeDemoCalibrationOperation("only LONG MARKET calibration is authorized")
        _require_positive_decimal(self.quantity, "order quantity")
        if not _CLIENT_ORDER_ID.fullmatch(self.client_order_id):
            raise DemoCalibrationIntegrityError("client order ID is not canonical")
        if self.phase is CalibrationOrderPhase.OPEN:
            if (
                self.side != "BUY"
                or self.quantity != OPEN_QUANTITY
                or self.client_order_id != OPEN_CLIENT_ORDER_ID
                or self.observed_position_evidence_id is not None
            ):
                raise UnsafeDemoCalibrationOperation("opening order differs from Issue #132")
        elif (
            self.side != "SELL"
            or self.client_order_id != CLEANUP_CLIENT_ORDER_ID
            or self.observed_position_evidence_id is None
        ):
            raise UnsafeDemoCalibrationOperation("cleanup order is not position-bound")
        if self.observed_position_evidence_id is not None:
            _require_sha256(self.observed_position_evidence_id, "position evidence ID")

    @classmethod
    def opening(cls) -> DemoCalibrationOrder:
        return cls(
            phase=CalibrationOrderPhase.OPEN,
            symbol=DEMO_SYMBOL,
            side="BUY",
            position_side="LONG",
            order_type="MARKET",
            quantity=OPEN_QUANTITY,
            client_order_id=OPEN_CLIENT_ORDER_ID,
            observed_position_evidence_id=None,
        )

    @classmethod
    def cleanup(cls, position: forward.DemoPositionEvidence) -> DemoCalibrationOrder:
        if position.symbol != DEMO_SYMBOL or position.side != "LONG":
            raise UnsafeDemoCalibrationOperation("cleanup position identity is not authorized")
        return cls(
            phase=CalibrationOrderPhase.CLEANUP,
            symbol=DEMO_SYMBOL,
            side="SELL",
            position_side="LONG",
            order_type="MARKET",
            quantity=position.size,
            client_order_id=CLEANUP_CLIENT_ORDER_ID,
            observed_position_evidence_id=position.evidence_id,
        )

    def canonical_body(self) -> bytes:
        payload = {
            "newClientOrderId": self.client_order_id,
            "positionSide": self.position_side,
            "quantity": str(self.quantity),
            "side": self.side,
            "symbol": self.symbol,
            "type": self.order_type,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()


class PostBytesTransport(Protocol):
    """Injection seam whose production implementation is private and POST-only."""

    def post(
        self,
        *,
        base_url: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> object: ...


class _RejectPostRedirectHandler(urllib.request.HTTPRedirectHandler):
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
        raise DemoCalibrationRequestError("approved WEEX Demo POST refused an HTTP redirect")


class _UrlLibDemoOrderPostTransport:
    def __init__(self, *, timeout_seconds: float = 20) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_RejectPostRedirectHandler())

    def post(
        self,
        *,
        base_url: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str],
    ) -> object:
        if path != DEMO_ORDER_PATH:
            raise UnsafeDemoCalibrationOperation("transport path is not the Demo order route")
        request = urllib.request.Request(
            f"{base_url}{path}", data=body, headers=dict(headers), method="POST"
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return json.load(response, parse_float=Decimal)
        except (
            DemoCalibrationRequestError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ):
            pass
        raise DemoCalibrationRequestError("approved WEEX Demo order request failed")


@dataclass(frozen=True, slots=True)
class CalibrationSourceObservation:
    method: str
    endpoint: str
    parameters: tuple[tuple[str, str], ...]
    request_body_sha256: str | None
    response_sha256: str

    def __post_init__(self) -> None:
        allowed_gets = forward.PUBLIC_GET_PATHS | forward.DEMO_GET_PATHS
        if (self.method, self.endpoint) not in (
            {("GET", item) for item in allowed_gets} | {("POST", DEMO_ORDER_PATH)}
        ):
            raise DemoCalibrationIntegrityError("calibration source route is not reviewed")
        if self.parameters != tuple(sorted(self.parameters)):
            raise DemoCalibrationIntegrityError("source parameters are not canonical")
        if self.method == "POST":
            if self.parameters or self.request_body_sha256 is None:
                raise DemoCalibrationIntegrityError("POST provenance is incomplete")
            _require_sha256(self.request_body_sha256, "request body SHA-256")
        elif self.request_body_sha256 is not None:
            raise DemoCalibrationIntegrityError("GET provenance contains a request body")
        _require_sha256(self.response_sha256, "response SHA-256")


@dataclass(frozen=True, slots=True)
class DemoOrderSubmissionEvidence:
    phase: CalibrationOrderPhase
    client_order_id: str
    order_id_hash: str
    accepted: bool
    error_code: str
    error_message: str
    submitted_at: datetime
    response_sha256: str

    def __post_init__(self) -> None:
        expected_id = (
            OPEN_CLIENT_ORDER_ID
            if self.phase is CalibrationOrderPhase.OPEN
            else CLEANUP_CLIENT_ORDER_ID
        )
        if self.client_order_id != expected_id:
            raise DemoCalibrationIntegrityError("submission client ID is not predeclared")
        _require_sha256(self.order_id_hash, "order ID hash")
        _require_sha256(self.response_sha256, "order response SHA-256")
        require_utc(self.submitted_at, field="order submitted_at")
        _require_safe_text(self.error_code, "order error code", allow_empty=True)
        _require_safe_text(self.error_message, "order error message", allow_empty=True)
        if self.accepted and (self.error_code or self.error_message):
            raise DemoCalibrationIntegrityError("accepted submission contains an error")


@dataclass(frozen=True, slots=True)
class _SubmittedOrder:
    evidence: DemoOrderSubmissionEvidence
    raw_order_id: str
    observation: CalibrationSourceObservation


def sign_demo_order_post(
    secret: forward.SecretValue, *, timestamp: str, canonical_body: bytes
) -> str:
    if not timestamp.isascii() or not timestamp.isdigit():
        raise DemoCalibrationIntegrityError("signature timestamp must be milliseconds")
    try:
        body_text = canonical_body.decode("ascii")
    except UnicodeDecodeError as error:
        raise DemoCalibrationIntegrityError("canonical order body must be ASCII") from error
    message = f"{timestamp}POST{DEMO_ORDER_PATH}{body_text}"
    digest = hmac.new(
        secret.reveal_for_signing().encode(), message.encode(), digestmod="sha256"
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class WeexDemoCalibrationWriter:
    """The only supported state-changing boundary: exact typed Demo orders."""

    def __init__(
        self,
        credentials: forward.WeexDemoCredentials,
        transport: PostBytesTransport,
        *,
        timestamp_ms: Callable[[], int] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._timestamp_ms = timestamp_ms or (lambda: int(datetime.now(tz=UTC).timestamp() * 1000))
        self._now = now or (lambda: datetime.now(tz=UTC))

    def submit_opening(self, order: DemoCalibrationOrder) -> _SubmittedOrder:
        if order.phase is not CalibrationOrderPhase.OPEN:
            raise UnsafeDemoCalibrationOperation("cleanup cannot use the opening operation")
        return self._submit(order)

    def submit_cleanup(
        self, order: DemoCalibrationOrder, *, position: forward.DemoPositionEvidence
    ) -> _SubmittedOrder:
        if order.phase is not CalibrationOrderPhase.CLEANUP:
            raise UnsafeDemoCalibrationOperation("opening cannot use the cleanup operation")
        if (
            order.observed_position_evidence_id != position.evidence_id
            or order.quantity != position.size
            or position.symbol != DEMO_SYMBOL
            or position.side != "LONG"
        ):
            raise UnsafeDemoCalibrationOperation("cleanup does not match observed LONG")
        return self._submit(order)

    def _submit(self, order: DemoCalibrationOrder) -> _SubmittedOrder:
        body = order.canonical_body()
        timestamp = str(self._timestamp_ms())
        signature = sign_demo_order_post(
            self._credentials.secret_key, timestamp=timestamp, canonical_body=body
        )
        headers = {
            "ACCESS-KEY": self._credentials.api_key.reveal_for_signing(),
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self._credentials.passphrase.reveal_for_signing(),
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "User-Agent": "adaptive-trading-bot-demo-calibration/1",
        }
        payload = self._transport.post(
            base_url=forward.WEEX_V3_BASE_URL,
            path=DEMO_ORDER_PATH,
            body=body,
            headers=headers,
        )
        response_sha = _response_sha(payload)
        item = _payload_object(payload, "Demo order response")
        raw_order_id = _strict_identifier(item.get("orderId"), "Demo order ID")
        returned_client_id = _strict_text(item.get("clientOrderId"), "clientOrderId")
        if returned_client_id != order.client_order_id:
            raise DemoCalibrationIntegrityError("Demo response client ID mismatch")
        accepted = _strict_bool(item.get("success"), "order success")
        error_code = _sanitized_venue_error_code(item.get("errorCode"), accepted=accepted)
        error_message = "" if accepted else "venue rejected Demo calibration order"
        evidence = DemoOrderSubmissionEvidence(
            phase=order.phase,
            client_order_id=returned_client_id,
            order_id_hash=_identifier_hash("demo-order", raw_order_id),
            accepted=accepted,
            error_code=error_code,
            error_message=error_message,
            submitted_at=require_utc(self._now(), field="submission time"),
            response_sha256=response_sha,
        )
        return _SubmittedOrder(
            evidence=evidence,
            raw_order_id=raw_order_id,
            observation=CalibrationSourceObservation(
                method="POST",
                endpoint=DEMO_ORDER_PATH,
                parameters=(),
                request_body_sha256=sha256(body).hexdigest(),
                response_sha256=response_sha,
            ),
        )


@dataclass(frozen=True, slots=True)
class DemoSymbolMarkEvidence:
    symbol: str
    price: Decimal
    venue_time_ms: int
    venue_time: datetime

    def __post_init__(self) -> None:
        if self.symbol != DEMO_SYMBOL:
            raise DemoCalibrationIntegrityError("MARK evidence is not for BTCSUSDT")
        _require_positive_decimal(self.price, "Demo MARK")
        _require_timestamp_pair(self.venue_time_ms, self.venue_time, "Demo MARK time")


@dataclass(frozen=True, slots=True)
class DemoSymbolPremiumEvidence:
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    last_funding_rate: Decimal
    forecast_funding_rate: Decimal
    next_funding_time_ms: int
    next_funding_time: datetime
    venue_time_ms: int
    venue_time: datetime

    def __post_init__(self) -> None:
        if self.symbol != DEMO_SYMBOL:
            raise DemoCalibrationIntegrityError("premium evidence is not for BTCSUSDT")
        _require_positive_decimal(self.mark_price, "premium MARK")
        _require_positive_decimal(self.index_price, "premium index")
        _require_finite_decimal(self.last_funding_rate, "last funding rate")
        _require_finite_decimal(self.forecast_funding_rate, "forecast funding rate")
        _require_timestamp_pair(
            self.next_funding_time_ms, self.next_funding_time, "next funding time"
        )
        _require_timestamp_pair(self.venue_time_ms, self.venue_time, "premium venue time")


@dataclass(frozen=True, slots=True)
class DemoSymbolContractEvidence:
    symbol: str
    base_asset: str
    quote_asset: str
    margin_asset: str
    contract_type: str
    contract_val: Decimal
    min_leverage: Decimal
    max_leverage: Decimal
    quantity_precision: int
    min_order_size: Decimal
    max_order_size: Decimal
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    quantity_semantics_qualified: bool

    def __post_init__(self) -> None:
        if self.symbol != DEMO_SYMBOL:
            raise DemoCalibrationIntegrityError("contract evidence is not for BTCSUSDT")
        for text_value, name in (
            (self.base_asset, "base asset"),
            (self.quote_asset, "quote asset"),
            (self.margin_asset, "margin asset"),
            (self.contract_type, "contract type"),
        ):
            _require_safe_text(text_value, name)
        for decimal_value, name in (
            (self.contract_val, "contract value"),
            (self.min_leverage, "minimum leverage"),
            (self.max_leverage, "maximum leverage"),
            (self.min_order_size, "minimum order size"),
            (self.max_order_size, "maximum order size"),
        ):
            _require_positive_decimal(decimal_value, name)
        for fee_value, name in (
            (self.maker_fee_rate, "maker fee"),
            (self.taker_fee_rate, "taker fee"),
        ):
            _require_finite_decimal(fee_value, name)
            if fee_value < 0:
                raise DemoCalibrationIntegrityError(f"{name} cannot be negative")
        if self.min_leverage > self.max_leverage:
            raise DemoCalibrationIntegrityError("Demo leverage bounds are inverted")
        if type(self.quantity_precision) is not int or self.quantity_precision < 0:
            raise DemoCalibrationIntegrityError("quantity precision is invalid")
        if self.quantity_semantics_qualified:
            raise DemoCalibrationIntegrityError(
                "Issue #132 has no authority to qualify Demo quantity semantics"
            )


@dataclass(frozen=True, slots=True)
class CalibrationComparison:
    kind: CalibrationComparisonKind
    disposition: CalibrationDisposition
    left_value: Decimal | None
    right_value: Decimal | None
    exact_delta: Decimal | None
    rationale: str

    def __post_init__(self) -> None:
        _require_safe_text(self.rationale, "comparison rationale")
        values = (self.left_value, self.right_value, self.exact_delta)
        for value in values:
            if value is not None:
                _require_finite_decimal(value, "comparison value")
        if self.disposition is CalibrationDisposition.UNRESOLVED:
            return
        if any(value is None for value in values):
            raise DemoCalibrationIntegrityError("resolved comparison lacks exact values")
        expected = (
            CalibrationDisposition.MATCH
            if self.exact_delta == Decimal("0")
            else CalibrationDisposition.MISMATCH
        )
        if self.disposition is not expected:
            raise DemoCalibrationIntegrityError("comparison disposition contradicts delta")


@dataclass(frozen=True, slots=True)
class DemoCalibrationBundle:
    schema_version: str
    bundle_version: str
    protocol_version: str
    captured_at: datetime
    completed_at: datetime
    source_code_commit: str
    authorized_open_order: DemoCalibrationOrder
    source_observations: tuple[CalibrationSourceObservation, ...]
    baseline_balance_sha256: str | None
    baseline_position_sha256: str | None
    baseline_order_history_sha256: str | None
    baseline_balance_count: int
    baseline_position_count: int
    baseline_order_history_count: int
    demo_mark: DemoSymbolMarkEvidence | None
    demo_premium: DemoSymbolPremiumEvidence | None
    demo_contract: DemoSymbolContractEvidence | None
    open_submission: DemoOrderSubmissionEvidence | None
    observed_position: forward.DemoPositionEvidence | None
    opening_order_attribution: CalibrationDisposition | None
    observed_order: forward.DemoOrderHistoryEvidence | None
    comparisons: tuple[CalibrationComparison, ...]
    liquidation_price: Decimal | None
    cleanup_order: DemoCalibrationOrder | None
    cleanup_submission: DemoOrderSubmissionEvidence | None
    cleanup_confirmed: bool | None
    statuses: tuple[DemoCalibrationStatus, ...]
    demo_only: bool
    production_equivalence_claimed: bool
    historical_authority_claimed: bool
    integrity_seal: str

    def validate(self) -> None:
        if self.schema_version != CALIBRATION_SCHEMA_VERSION:
            raise DemoCalibrationIntegrityError("unknown calibration schema")
        if self.protocol_version != CALIBRATION_PROTOCOL_VERSION:
            raise DemoCalibrationIntegrityError("unknown calibration protocol")
        require_utc(self.captured_at, field="calibration captured_at")
        require_utc(self.completed_at, field="calibration completed_at")
        if self.completed_at < self.captured_at:
            raise DemoCalibrationIntegrityError("calibration chronology is invalid")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise DemoCalibrationIntegrityError("calibration source SHA is invalid")
        if self.authorized_open_order != DemoCalibrationOrder.opening():
            raise DemoCalibrationIntegrityError("authorized opening order changed")
        if (
            not self.demo_only
            or self.production_equivalence_claimed
            or self.historical_authority_claimed
        ):
            raise DemoCalibrationIntegrityError("calibration authority claims are invalid")
        if not self.statuses or self.statuses != tuple(dict.fromkeys(self.statuses)):
            raise DemoCalibrationIntegrityError("calibration statuses are empty or duplicated")
        if self.statuses[-1] not in {
            DemoCalibrationStatus.DEMO_LONG_CALIBRATION_COMPLETE,
            DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED,
        }:
            raise DemoCalibrationIntegrityError("calibration terminal status is missing")
        for digest in (
            self.baseline_balance_sha256,
            self.baseline_position_sha256,
            self.baseline_order_history_sha256,
        ):
            if digest is not None:
                _require_sha256(digest, "baseline digest")
        for count in (
            self.baseline_balance_count,
            self.baseline_position_count,
            self.baseline_order_history_count,
        ):
            if type(count) is not int or count < 0:
                raise DemoCalibrationIntegrityError("baseline count is invalid")
        unavailable = DemoCalibrationStatus.DEMO_CREDENTIALS_UNAVAILABLE in self.statuses
        if unavailable:
            if self.statuses != (
                DemoCalibrationStatus.DEMO_CREDENTIALS_UNAVAILABLE,
                DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED,
            ):
                raise DemoCalibrationIntegrityError("credential-blocked statuses are inconsistent")
            if (
                any(
                    value is not None
                    for value in (
                        self.baseline_balance_sha256,
                        self.baseline_position_sha256,
                        self.baseline_order_history_sha256,
                        self.demo_mark,
                        self.demo_premium,
                        self.demo_contract,
                        self.open_submission,
                        self.observed_position,
                        self.opening_order_attribution,
                        self.observed_order,
                        self.liquidation_price,
                        self.cleanup_order,
                        self.cleanup_submission,
                        self.cleanup_confirmed,
                    )
                )
                or self.comparisons
                or self.source_observations
            ):
                raise DemoCalibrationIntegrityError(
                    "credential-blocked bundle contains fabricated observations"
                )
        ambiguous_open = (
            self.open_submission is None
            and DemoCalibrationStatus.DEMO_OPEN_RESPONSE_UNAVAILABLE in self.statuses
        )
        if (
            self.open_submission is None
            and not ambiguous_open
            and any(
                value is not None
                for value in (
                    self.observed_position,
                    self.opening_order_attribution,
                    self.observed_order,
                    self.liquidation_price,
                    self.cleanup_order,
                    self.cleanup_submission,
                    self.cleanup_confirmed,
                )
            )
        ):
            raise DemoCalibrationIntegrityError("post-open evidence lacks an opening submission")
        if self.observed_position is None and (self.comparisons or self.cleanup_order is not None):
            raise DemoCalibrationIntegrityError("position-dependent evidence has no position")
        if self.cleanup_order is not None:
            if self.observed_position is None:
                raise DemoCalibrationIntegrityError("cleanup lacks an observed position")
            if (
                self.cleanup_order.quantity != self.observed_position.size
                or self.cleanup_order.observed_position_evidence_id
                != self.observed_position.evidence_id
            ):
                raise DemoCalibrationIntegrityError("cleanup authorization changed")
        if self.cleanup_submission is not None and self.cleanup_order is None:
            raise DemoCalibrationIntegrityError("cleanup submission lacks authorization")
        expected_version = f"weex-demo-calibration-{_bundle_identity_sha(self)[:24]}"
        if self.bundle_version != expected_version:
            raise DemoCalibrationIntegrityError("bundle version does not match evidence identity")
        if not hmac.compare_digest(self.integrity_seal, _integrity_sha(self)):
            raise DemoCalibrationIntegrityError("calibration evidence seal mismatch")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


@dataclass(frozen=True, slots=True)
class DemoCalibrationManifest:
    schema_version: str
    bundle_version: str
    source_code_commit: str
    captured_at: datetime
    artifact_path: str
    artifact_sha256: str
    artifact_size_bytes: int

    def validate(self) -> None:
        if self.schema_version != CALIBRATION_MANIFEST_SCHEMA_VERSION:
            raise CorpusManifestError("unknown Demo calibration manifest schema")
        if not self.bundle_version.startswith("weex-demo-calibration-"):
            raise CorpusManifestError("Demo calibration bundle version is invalid")
        if not _CODE_SHA.fullmatch(self.source_code_commit):
            raise CorpusManifestError("Demo calibration code SHA is invalid")
        require_utc(self.captured_at, field="manifest captured_at")
        if self.artifact_path != "bundle.json":
            raise CorpusManifestError("Demo calibration artifact path is invalid")
        _require_sha256(self.artifact_sha256, "artifact SHA-256")
        if type(self.artifact_size_bytes) is not int or self.artifact_size_bytes <= 0:
            raise CorpusManifestError("Demo calibration artifact size is invalid")

    def to_json(self) -> str:
        self.validate()
        return _canonical_json(_canonical_value(self))


@dataclass(frozen=True, slots=True)
class _BaselineEvidence:
    balance_sha256: str
    position_sha256: str
    order_history_sha256: str
    balance_count: int
    position_count: int
    order_history_count: int


class WeexDemoLongCalibrationRunner:
    def __init__(
        self,
        *,
        public_client: forward.WeexPublicCurrentClient,
        demo_reader: forward.WeexDemoReadOnlyClient | None,
        demo_writer: WeexDemoCalibrationWriter | None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if (demo_reader is None) != (demo_writer is None):
            raise DemoCalibrationIntegrityError("Demo read/write dependencies must be paired")
        self._public = public_client
        self._reader = demo_reader
        self._writer = demo_writer
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._sleep = sleep or time.sleep

    def run(self, *, captured_at: datetime, source_code_commit: str) -> DemoCalibrationBundle:
        captured_at = require_utc(captured_at, field="calibration captured_at")
        if not _CODE_SHA.fullmatch(source_code_commit):
            raise DemoCalibrationIntegrityError("calibration requires an exact code SHA")
        if self._reader is None or self._writer is None:
            return _seal_bundle(
                _bundle_seed(
                    captured_at=captured_at,
                    completed_at=require_utc(self._now(), field="completion time"),
                    source_code_commit=source_code_commit,
                    statuses=(
                        DemoCalibrationStatus.DEMO_CREDENTIALS_UNAVAILABLE,
                        DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED,
                    ),
                )
            )

        observations: list[CalibrationSourceObservation] = []
        statuses: list[DemoCalibrationStatus] = []
        balance_raw, balance_observation = self._reader.request("GET", forward.DEMO_BALANCE_PATH)
        observations.append(_from_get_observation(balance_observation))
        balances = forward._parse_demo_balances(balance_raw)
        position_raw, position_observation = self._reader.request(
            "GET", forward.DEMO_POSITIONS_PATH
        )
        observations.append(_from_get_observation(position_observation))
        baseline_positions = forward._parse_demo_positions(position_raw)
        history_parameters = {"limit": "100", "page": "0", "symbol": DEMO_SYMBOL}
        history_raw, history_observation = self._reader.request(
            "GET", forward.DEMO_ORDER_HISTORY_PATH, history_parameters
        )
        observations.append(_from_get_observation(history_observation))
        baseline_orders = forward._parse_demo_orders(history_raw)
        baseline = _BaselineEvidence(
            balance_sha256=balance_observation.response_sha256,
            position_sha256=position_observation.response_sha256,
            order_history_sha256=history_observation.response_sha256,
            balance_count=len(balances),
            position_count=len(baseline_positions),
            order_history_count=len(baseline_orders),
        )
        if not balances:
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                [*statuses, DemoCalibrationStatus.DEMO_BASELINE_INVALID],
                baseline=baseline,
            )
        if baseline_positions:
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                [*statuses, DemoCalibrationStatus.DEMO_DIRTY_BASELINE_BLOCKED],
                baseline=baseline,
            )
        if _recent_order_exists(
            baseline_orders, require_utc(self._now(), field="rate-limit preflight time")
        ):
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                [*statuses, DemoCalibrationStatus.DEMO_RATE_LIMIT_WINDOW_BLOCKED],
                baseline=baseline,
            )

        contract, contract_observation = self._probe_contract()
        if contract_observation is not None:
            observations.append(contract_observation)
        _append_unique(
            statuses,
            DemoCalibrationStatus.DEMO_CONTRACT_FACTS_QUALIFIED
            if contract is not None
            else DemoCalibrationStatus.DEMO_CONTRACT_FACTS_UNAVAILABLE,
        )
        premium, premium_observation = self._probe_premium()
        if premium_observation is not None:
            observations.append(premium_observation)
        mark, mark_observation = self._probe_mark()
        if mark_observation is not None:
            observations.append(mark_observation)
        if mark is None:
            statuses.extend(
                (
                    DemoCalibrationStatus.DEMO_MARK_UNAVAILABLE,
                    DemoCalibrationStatus.DEMO_CALIBRATION_BLOCKED_NO_MARK,
                )
            )
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                statuses,
                demo_premium=premium,
                demo_contract=contract,
                baseline=baseline,
            )
        statuses.append(DemoCalibrationStatus.DEMO_MARK_QUALIFIED)

        open_order = DemoCalibrationOrder.opening()
        try:
            submitted = self._writer.submit_opening(open_order)
        except (DemoCalibrationRequestError, DemoCalibrationIntegrityError):
            cleanup_delay_reference = require_utc(
                self._now(), field="ambiguous opening completion time"
            )
            statuses.append(DemoCalibrationStatus.DEMO_OPEN_RESPONSE_UNAVAILABLE)
            observed, attribution, position_observations = self._observe_open_position(None)
            observations.extend(position_observations)
            if observed is None:
                statuses.append(DemoCalibrationStatus.DEMO_OPEN_NOT_OBSERVED)
                return self._blocked(
                    captured_at,
                    source_code_commit,
                    observations,
                    statuses,
                    demo_mark=mark,
                    demo_premium=premium,
                    demo_contract=contract,
                    baseline=baseline,
                )
            return self._finish_observed_position(
                captured_at=captured_at,
                source_code_commit=source_code_commit,
                observations=observations,
                statuses=statuses,
                baseline=baseline,
                mark=mark,
                premium=premium,
                contract=contract,
                submitted=None,
                observed=observed,
                attribution=attribution,
                cleanup_delay_reference=cleanup_delay_reference,
            )

        observations.append(submitted.observation)
        if not submitted.evidence.accepted:
            statuses.append(DemoCalibrationStatus.DEMO_OPEN_REJECTED)
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                statuses,
                demo_mark=mark,
                demo_premium=premium,
                demo_contract=contract,
                open_submission=submitted.evidence,
                baseline=baseline,
            )
        statuses.append(DemoCalibrationStatus.DEMO_OPEN_ACCEPTED)
        observed, attribution, position_observations = self._observe_open_position(
            submitted.raw_order_id
        )
        observations.extend(position_observations)
        if observed is None:
            statuses.append(DemoCalibrationStatus.DEMO_OPEN_NOT_OBSERVED)
            return self._blocked(
                captured_at,
                source_code_commit,
                observations,
                statuses,
                demo_mark=mark,
                demo_premium=premium,
                demo_contract=contract,
                open_submission=submitted.evidence,
                baseline=baseline,
            )
        return self._finish_observed_position(
            captured_at=captured_at,
            source_code_commit=source_code_commit,
            observations=observations,
            statuses=statuses,
            baseline=baseline,
            mark=mark,
            premium=premium,
            contract=contract,
            submitted=submitted,
            observed=observed,
            attribution=attribution,
            cleanup_delay_reference=submitted.evidence.submitted_at,
        )

    def _finish_observed_position(
        self,
        *,
        captured_at: datetime,
        source_code_commit: str,
        observations: list[CalibrationSourceObservation],
        statuses: list[DemoCalibrationStatus],
        baseline: _BaselineEvidence,
        mark: DemoSymbolMarkEvidence,
        premium: DemoSymbolPremiumEvidence | None,
        contract: DemoSymbolContractEvidence | None,
        submitted: _SubmittedOrder | None,
        observed: forward.DemoPositionEvidence,
        attribution: CalibrationDisposition,
        cleanup_delay_reference: datetime,
    ) -> DemoCalibrationBundle:
        eligible = True
        if observed.margin_type != "ISOLATED":
            statuses.append(DemoCalibrationStatus.DEMO_SAMPLE_REJECTED_NOT_ISOLATED)
            eligible = False
        if observed.is_auto_append_isolated_margin:
            statuses.append(DemoCalibrationStatus.DEMO_SAMPLE_REJECTED_AUTO_MARGIN)
            eligible = False
        if attribution is not CalibrationDisposition.MATCH:
            statuses.append(DemoCalibrationStatus.DEMO_SAMPLE_REJECTED_ATTRIBUTION)
            eligible = False
        if eligible:
            statuses.append(DemoCalibrationStatus.DEMO_SAMPLE_ELIGIBLE)

        observed_order, order_observation = self._read_observed_order()
        if order_observation is not None:
            observations.append(order_observation)
        comparisons = _build_comparisons(
            observed_order=observed_order,
            position=observed,
            mark=mark,
            contract=contract,
        )
        for comparison in comparisons:
            _append_unique(statuses, _status_for_disposition(comparison.disposition))
        statuses.append(
            DemoCalibrationStatus.LIQUIDATION_TARGET_NOT_OBSERVED
            if observed.liquidate_price == 0
            else DemoCalibrationStatus.LIQUIDATION_TARGET_OBSERVED
        )

        cleanup_order = DemoCalibrationOrder.cleanup(observed)
        elapsed = (
            require_utc(self._now(), field="cleanup clock")
            - require_utc(cleanup_delay_reference, field="cleanup delay reference")
        ).total_seconds()
        wait_seconds = max(0.0, MINIMUM_CLEANUP_DELAY_SECONDS - elapsed)
        if wait_seconds:
            self._sleep(wait_seconds)
        cleanup_confirmed = False
        cleanup_evidence: DemoOrderSubmissionEvidence | None = None
        try:
            cleanup = cast(WeexDemoCalibrationWriter, self._writer).submit_cleanup(
                cleanup_order, position=observed
            )
            cleanup_evidence = cleanup.evidence
            observations.append(cleanup.observation)
            if cleanup.evidence.accepted:
                cleanup_confirmed, closure_observations = self._observe_closed_position()
                observations.extend(closure_observations)
        except (DemoCalibrationRequestError, DemoCalibrationIntegrityError):
            cleanup_confirmed, closure_observations = self._observe_closed_position()
            observations.extend(closure_observations)
        statuses.append(
            DemoCalibrationStatus.DEMO_CLEANUP_CONFIRMED
            if cleanup_confirmed
            else DemoCalibrationStatus.DEMO_CLEANUP_UNCONFIRMED
        )
        statuses.append(
            DemoCalibrationStatus.DEMO_LONG_CALIBRATION_COMPLETE
            if eligible and cleanup_confirmed
            else DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED
        )
        return _seal_bundle(
            _bundle_seed(
                captured_at=captured_at,
                completed_at=require_utc(self._now(), field="completion time"),
                source_code_commit=source_code_commit,
                source_observations=tuple(observations),
                demo_mark=mark,
                demo_premium=premium,
                demo_contract=contract,
                open_submission=None if submitted is None else submitted.evidence,
                observed_position=observed,
                opening_order_attribution=attribution,
                observed_order=observed_order,
                comparisons=comparisons,
                liquidation_price=observed.liquidate_price,
                cleanup_order=cleanup_order,
                cleanup_submission=cleanup_evidence,
                cleanup_confirmed=cleanup_confirmed,
                statuses=tuple(statuses),
                baseline_balance_sha256=baseline.balance_sha256,
                baseline_position_sha256=baseline.position_sha256,
                baseline_order_history_sha256=baseline.order_history_sha256,
                baseline_balance_count=baseline.balance_count,
                baseline_position_count=baseline.position_count,
                baseline_order_history_count=baseline.order_history_count,
            )
        )

    def _observe_open_position(
        self, raw_open_order_id: str | None
    ) -> tuple[
        forward.DemoPositionEvidence | None,
        CalibrationDisposition,
        tuple[CalibrationSourceObservation, ...],
    ]:
        observations: list[CalibrationSourceObservation] = []
        for attempt in range(MAX_POSITION_OBSERVATIONS):
            raw, source = cast(forward.WeexDemoReadOnlyClient, self._reader).request(
                "GET", forward.DEMO_POSITIONS_PATH
            )
            observations.append(_from_get_observation(source))
            positions = forward._parse_demo_positions(raw)
            matches = tuple(
                position
                for position in positions
                if position.symbol == DEMO_SYMBOL and position.side == "LONG"
            )
            if len(matches) > 1:
                raise DemoCalibrationIntegrityError("multiple calibration LONGs observed")
            if matches:
                attribution = _position_attribution(raw, raw_open_order_id)
                return matches[0], attribution, tuple(observations)
            if attempt + 1 < MAX_POSITION_OBSERVATIONS:
                self._sleep(POSITION_OBSERVATION_INTERVAL_SECONDS)
        return None, CalibrationDisposition.UNRESOLVED, tuple(observations)

    def _observe_closed_position(
        self,
    ) -> tuple[bool, tuple[CalibrationSourceObservation, ...]]:
        observations: list[CalibrationSourceObservation] = []
        for attempt in range(MAX_POSITION_OBSERVATIONS):
            raw, source = cast(forward.WeexDemoReadOnlyClient, self._reader).request(
                "GET", forward.DEMO_POSITIONS_PATH
            )
            observations.append(_from_get_observation(source))
            positions = forward._parse_demo_positions(raw)
            if not any(
                position.symbol == DEMO_SYMBOL and position.side == "LONG" for position in positions
            ):
                return True, tuple(observations)
            if attempt + 1 < MAX_POSITION_OBSERVATIONS:
                self._sleep(POSITION_OBSERVATION_INTERVAL_SECONDS)
        return False, tuple(observations)

    def _read_observed_order(
        self,
    ) -> tuple[
        forward.DemoOrderHistoryEvidence | None,
        CalibrationSourceObservation | None,
    ]:
        parameters = {"limit": "100", "page": "0", "symbol": DEMO_SYMBOL}
        try:
            raw, source = cast(forward.WeexDemoReadOnlyClient, self._reader).request(
                "GET", forward.DEMO_ORDER_HISTORY_PATH, parameters
            )
            item = _find_order_item(raw, OPEN_CLIENT_ORDER_ID)
            return (
                None if item is None else forward._parse_demo_orders([item])[0],
                _from_get_observation(source),
            )
        except (forward.ForwardEvidenceError, DemoCalibrationIntegrityError):
            return None, None

    def _probe_mark(
        self,
    ) -> tuple[DemoSymbolMarkEvidence | None, CalibrationSourceObservation | None]:
        try:
            raw, source = self._public.request(
                "GET",
                forward.SYMBOL_PRICE_PATH,
                {"priceType": "MARK", "symbol": DEMO_SYMBOL},
            )
            return _parse_demo_mark(raw), _from_get_observation(source)
        except (forward.ForwardEvidenceError, DemoCalibrationIntegrityError):
            return None, None

    def _probe_premium(
        self,
    ) -> tuple[DemoSymbolPremiumEvidence | None, CalibrationSourceObservation | None]:
        try:
            raw, source = self._public.request(
                "GET", forward.PREMIUM_INDEX_PATH, {"symbol": DEMO_SYMBOL}
            )
            return _parse_demo_premium(raw), _from_get_observation(source)
        except (forward.ForwardEvidenceError, DemoCalibrationIntegrityError):
            return None, None

    def _probe_contract(
        self,
    ) -> tuple[DemoSymbolContractEvidence | None, CalibrationSourceObservation | None]:
        try:
            raw, source = self._public.request(
                "GET", forward.EXCHANGE_INFO_PATH, {"symbol": DEMO_SYMBOL}
            )
            return _parse_demo_contract(raw), _from_get_observation(source)
        except (forward.ForwardEvidenceError, DemoCalibrationIntegrityError):
            return None, None

    def _blocked(
        self,
        captured_at: datetime,
        source_code_commit: str,
        observations: Sequence[CalibrationSourceObservation],
        statuses: Sequence[DemoCalibrationStatus],
        *,
        baseline: _BaselineEvidence | None = None,
        demo_mark: DemoSymbolMarkEvidence | None = None,
        demo_premium: DemoSymbolPremiumEvidence | None = None,
        demo_contract: DemoSymbolContractEvidence | None = None,
        open_submission: DemoOrderSubmissionEvidence | None = None,
    ) -> DemoCalibrationBundle:
        final_statuses = list(statuses)
        _append_unique(final_statuses, DemoCalibrationStatus.DEMO_LONG_CALIBRATION_BLOCKED)
        return _seal_bundle(
            _bundle_seed(
                captured_at=captured_at,
                completed_at=require_utc(self._now(), field="completion time"),
                source_code_commit=source_code_commit,
                source_observations=tuple(observations),
                baseline_balance_sha256=None if baseline is None else baseline.balance_sha256,
                baseline_position_sha256=None if baseline is None else baseline.position_sha256,
                baseline_order_history_sha256=(
                    None if baseline is None else baseline.order_history_sha256
                ),
                baseline_balance_count=0 if baseline is None else baseline.balance_count,
                baseline_position_count=0 if baseline is None else baseline.position_count,
                baseline_order_history_count=(
                    0 if baseline is None else baseline.order_history_count
                ),
                demo_mark=demo_mark,
                demo_premium=demo_premium,
                demo_contract=demo_contract,
                open_submission=open_submission,
                statuses=tuple(final_statuses),
            )
        )


def _build_comparisons(
    *,
    observed_order: forward.DemoOrderHistoryEvidence | None,
    position: forward.DemoPositionEvidence,
    mark: DemoSymbolMarkEvidence,
    contract: DemoSymbolContractEvidence | None,
) -> tuple[CalibrationComparison, ...]:
    results: list[CalibrationComparison] = []
    if observed_order is None:
        results.extend(
            (
                _unresolved(
                    CalibrationComparisonKind.ORDER_QUANTITY, "opening history unavailable"
                ),
                _unresolved(
                    CalibrationComparisonKind.ORDER_NOTIONAL, "opening history unavailable"
                ),
            )
        )
    else:
        quantity_delta = observed_order.executed_quantity - position.size
        results.append(
            CalibrationComparison(
                kind=CalibrationComparisonKind.ORDER_QUANTITY,
                disposition=(
                    CalibrationDisposition.MATCH
                    if quantity_delta == 0
                    else CalibrationDisposition.MISMATCH
                ),
                left_value=observed_order.executed_quantity,
                right_value=position.size,
                exact_delta=quantity_delta,
                rationale="executed quantity minus venue-observed open size",
            )
        )
        candidate_notional = observed_order.average_price * observed_order.executed_quantity
        notional_delta = candidate_notional - position.open_value
        results.append(
            CalibrationComparison(
                kind=CalibrationComparisonKind.ORDER_NOTIONAL,
                disposition=(
                    CalibrationDisposition.MATCH
                    if notional_delta == 0
                    else CalibrationDisposition.UNRESOLVED
                ),
                left_value=candidate_notional,
                right_value=position.open_value,
                exact_delta=notional_delta,
                rationale=(
                    "average fill times executed quantity versus openValue; a nonzero delta "
                    "remains unresolved without authoritative Demo rounding semantics"
                ),
            )
        )
    margin_candidate = position.open_value / position.leverage
    margin_delta = margin_candidate - position.margin_size
    isolated_delta = margin_candidate - position.isolated_margin
    results.append(
        CalibrationComparison(
            kind=CalibrationComparisonKind.LEVERAGE_MARGIN,
            disposition=(
                CalibrationDisposition.MATCH
                if margin_delta == 0 and isolated_delta == 0
                else CalibrationDisposition.UNRESOLVED
            ),
            left_value=margin_candidate,
            right_value=position.margin_size,
            exact_delta=margin_delta,
            rationale=(
                "openValue/leverage versus marginSize; isolatedMargin delta="
                f"{isolated_delta}; fees and venue margin semantics are not fitted"
            ),
        )
    )
    if contract is None or not contract.quantity_semantics_qualified or observed_order is None:
        results.append(
            _unresolved(
                CalibrationComparisonKind.MARK_UNREALIZED_PNL,
                "Demo quantity/contract semantics are not authoritatively qualified",
            )
        )
    else:
        pnl_candidate = (mark.price - observed_order.average_price) * position.size
        pnl_delta = pnl_candidate - position.unrealized_pnl
        results.append(
            CalibrationComparison(
                kind=CalibrationComparisonKind.MARK_UNREALIZED_PNL,
                disposition=(
                    CalibrationDisposition.MATCH
                    if pnl_delta == 0
                    else CalibrationDisposition.MISMATCH
                ),
                left_value=pnl_candidate,
                right_value=position.unrealized_pnl,
                exact_delta=pnl_delta,
                rationale="qualified linear LONG quantity candidate versus venue unrealized PnL",
            )
        )
    return tuple(results)


def _unresolved(kind: CalibrationComparisonKind, rationale: str) -> CalibrationComparison:
    return CalibrationComparison(
        kind=kind,
        disposition=CalibrationDisposition.UNRESOLVED,
        left_value=None,
        right_value=None,
        exact_delta=None,
        rationale=rationale,
    )


def _status_for_disposition(disposition: CalibrationDisposition) -> DemoCalibrationStatus:
    return {
        CalibrationDisposition.MATCH: DemoCalibrationStatus.CALIBRATION_MATCH,
        CalibrationDisposition.MISMATCH: DemoCalibrationStatus.CALIBRATION_MISMATCH,
        CalibrationDisposition.UNRESOLVED: DemoCalibrationStatus.CALIBRATION_UNRESOLVED,
    }[disposition]


def _recent_order_exists(
    orders: Sequence[forward.DemoOrderHistoryEvidence], as_of: datetime
) -> bool:
    as_of = require_utc(as_of, field="rate-limit preflight time")
    threshold = as_of - timedelta(seconds=MINIMUM_CLEANUP_DELAY_SECONDS)
    return any(order.created_time > as_of or order.created_time >= threshold for order in orders)


def _position_attribution(payload: object, raw_order_id: str | None) -> CalibrationDisposition:
    if raw_order_id is None:
        return CalibrationDisposition.UNRESOLVED
    items = _payload_list(payload, "Demo positions")
    matching = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("symbol") == DEMO_SYMBOL
        and item.get("side") == "LONG"
    ]
    if len(matching) != 1:
        return CalibrationDisposition.UNRESOLVED
    linked = matching[0].get("separatedOpenOrderId")
    if linked in (None, 0, "0"):
        return CalibrationDisposition.UNRESOLVED
    return (
        CalibrationDisposition.MATCH
        if _strict_identifier(linked, "separatedOpenOrderId") == raw_order_id
        else CalibrationDisposition.MISMATCH
    )


def _find_order_item(payload: object, client_order_id: str) -> Mapping[str, object] | None:
    for item in _payload_list(payload, "Demo order history"):
        mapping = _require_mapping(item, "Demo order")
        if mapping.get("clientOrderId") == client_order_id:
            return mapping
    return None


def _parse_demo_mark(payload: object) -> DemoSymbolMarkEvidence:
    item = _payload_object(payload, "Demo MARK")
    symbol = _strict_text(item.get("symbol"), "Demo MARK symbol")
    if symbol != DEMO_SYMBOL:
        raise DemoCalibrationIntegrityError("Demo MARK symbol mismatch")
    venue_ms = _strict_integer(item.get("time"), "Demo MARK time")
    return DemoSymbolMarkEvidence(
        symbol=symbol,
        price=_strict_decimal(item.get("price"), "Demo MARK price"),
        venue_time_ms=venue_ms,
        venue_time=_from_milliseconds(venue_ms),
    )


def _parse_demo_premium(payload: object) -> DemoSymbolPremiumEvidence:
    items = _payload_list(payload, "Demo premium")
    if len(items) != 1:
        raise DemoCalibrationIntegrityError("Demo premium must contain one record")
    item = _require_mapping(items[0], "Demo premium")
    symbol = _strict_text(item.get("symbol"), "Demo premium symbol")
    if symbol != DEMO_SYMBOL:
        raise DemoCalibrationIntegrityError("Demo premium symbol mismatch")
    next_ms = _strict_integer(item.get("nextFundingTime"), "next funding time")
    venue_ms = _strict_integer(item.get("time"), "premium venue time")
    return DemoSymbolPremiumEvidence(
        symbol=symbol,
        mark_price=_strict_decimal(item.get("markPrice"), "premium MARK"),
        index_price=_strict_decimal(item.get("indexPrice"), "premium index"),
        last_funding_rate=_strict_decimal(item.get("lastFundingRate"), "last funding rate"),
        forecast_funding_rate=_strict_decimal(
            item.get("forecastFundingRate"), "forecast funding rate"
        ),
        next_funding_time_ms=next_ms,
        next_funding_time=_from_milliseconds(next_ms),
        venue_time_ms=venue_ms,
        venue_time=_from_milliseconds(venue_ms),
    )


def _parse_demo_contract(payload: object) -> DemoSymbolContractEvidence:
    value = forward._unwrap_payload(payload)
    rows = value.get("symbols") if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise DemoCalibrationIntegrityError("Demo contract payload has no symbols array")
    matches = [
        item for item in rows if isinstance(item, dict) and item.get("symbol") == DEMO_SYMBOL
    ]
    if len(matches) != 1:
        raise DemoCalibrationIntegrityError("Demo contract facts are unavailable")
    item = matches[0]
    return DemoSymbolContractEvidence(
        symbol=DEMO_SYMBOL,
        base_asset=_strict_text(item.get("baseAsset"), "base asset"),
        quote_asset=_strict_text(item.get("quoteAsset"), "quote asset"),
        margin_asset=_strict_text(item.get("marginAsset"), "margin asset"),
        contract_type=_strict_text(item.get("contractType"), "contract type"),
        contract_val=_strict_decimal(item.get("contractVal"), "contract value"),
        min_leverage=_strict_decimal(item.get("minLeverage"), "minimum leverage"),
        max_leverage=_strict_decimal(item.get("maxLeverage"), "maximum leverage"),
        quantity_precision=_strict_integer(item.get("quantityPrecision"), "quantity precision"),
        min_order_size=_strict_decimal(item.get("minOrderSize"), "minimum order size"),
        max_order_size=_strict_decimal(item.get("maxOrderSize"), "maximum order size"),
        maker_fee_rate=_strict_decimal(item.get("makerFeeRate"), "maker fee"),
        taker_fee_rate=_strict_decimal(item.get("takerFeeRate"), "taker fee"),
        quantity_semantics_qualified=False,
    )


def _bundle_seed(
    *,
    captured_at: datetime,
    completed_at: datetime,
    source_code_commit: str,
    source_observations: tuple[CalibrationSourceObservation, ...] = (),
    baseline_balance_sha256: str | None = None,
    baseline_position_sha256: str | None = None,
    baseline_order_history_sha256: str | None = None,
    baseline_balance_count: int = 0,
    baseline_position_count: int = 0,
    baseline_order_history_count: int = 0,
    demo_mark: DemoSymbolMarkEvidence | None = None,
    demo_premium: DemoSymbolPremiumEvidence | None = None,
    demo_contract: DemoSymbolContractEvidence | None = None,
    open_submission: DemoOrderSubmissionEvidence | None = None,
    observed_position: forward.DemoPositionEvidence | None = None,
    opening_order_attribution: CalibrationDisposition | None = None,
    observed_order: forward.DemoOrderHistoryEvidence | None = None,
    comparisons: tuple[CalibrationComparison, ...] = (),
    liquidation_price: Decimal | None = None,
    cleanup_order: DemoCalibrationOrder | None = None,
    cleanup_submission: DemoOrderSubmissionEvidence | None = None,
    cleanup_confirmed: bool | None = None,
    statuses: tuple[DemoCalibrationStatus, ...],
) -> DemoCalibrationBundle:
    return DemoCalibrationBundle(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        bundle_version="pending",
        protocol_version=CALIBRATION_PROTOCOL_VERSION,
        captured_at=captured_at,
        completed_at=completed_at,
        source_code_commit=source_code_commit,
        authorized_open_order=DemoCalibrationOrder.opening(),
        source_observations=source_observations,
        baseline_balance_sha256=baseline_balance_sha256,
        baseline_position_sha256=baseline_position_sha256,
        baseline_order_history_sha256=baseline_order_history_sha256,
        baseline_balance_count=baseline_balance_count,
        baseline_position_count=baseline_position_count,
        baseline_order_history_count=baseline_order_history_count,
        demo_mark=demo_mark,
        demo_premium=demo_premium,
        demo_contract=demo_contract,
        open_submission=open_submission,
        observed_position=observed_position,
        opening_order_attribution=opening_order_attribution,
        observed_order=observed_order,
        comparisons=comparisons,
        liquidation_price=liquidation_price,
        cleanup_order=cleanup_order,
        cleanup_submission=cleanup_submission,
        cleanup_confirmed=cleanup_confirmed,
        statuses=statuses,
        demo_only=True,
        production_equivalence_claimed=False,
        historical_authority_claimed=False,
        integrity_seal="pending",
    )


def _seal_bundle(bundle: DemoCalibrationBundle) -> DemoCalibrationBundle:
    version = f"weex-demo-calibration-{_bundle_identity_sha(bundle)[:24]}"
    versioned = replace(bundle, bundle_version=version)
    sealed = replace(versioned, integrity_seal=_integrity_sha(versioned))
    sealed.validate()
    return sealed


def write_calibration_bundle(
    bundle: DemoCalibrationBundle, output_root: Path
) -> DemoCalibrationManifest:
    bundle.validate()
    if output_root.exists():
        raise FileExistsError("Demo calibration output root must not already exist")
    output_root.mkdir(parents=True)
    artifact = output_root / "bundle.json"
    artifact_bytes = (bundle.to_json() + "\n").encode()
    _atomic_write(artifact, artifact_bytes)
    manifest = DemoCalibrationManifest(
        schema_version=CALIBRATION_MANIFEST_SCHEMA_VERSION,
        bundle_version=bundle.bundle_version,
        source_code_commit=bundle.source_code_commit,
        captured_at=bundle.captured_at,
        artifact_path="bundle.json",
        artifact_sha256=sha256(artifact_bytes).hexdigest(),
        artifact_size_bytes=len(artifact_bytes),
    )
    _atomic_write(output_root / "manifest.json", (manifest.to_json() + "\n").encode())
    return manifest


class LockedWeexDemoCalibrationBundle:
    """Network-free reader for one exact reviewed Demo calibration bundle."""

    def __init__(self, root: Path, *, bundle_version: str) -> None:
        self._root = root
        manifest_path = root / "manifest.json"
        self._manifest = _parse_manifest(_load_json_file(manifest_path))
        if self._manifest.bundle_version != bundle_version:
            raise CorpusManifestError("requested Demo calibration version is unknown")
        accepted = LOCKED_DEMO_CALIBRATION_MANIFEST_SHA256.get(bundle_version)
        if accepted is None:
            raise CorpusManifestError("Demo calibration version is not project-qualified")
        if _file_sha256(manifest_path) != accepted:
            raise CorpusHashMismatchError("Demo calibration manifest hash mismatch")
        self._verify_artifact()

    @property
    def manifest(self) -> DemoCalibrationManifest:
        return self._manifest

    def read(self) -> DemoCalibrationBundle:
        self._verify_artifact()
        bundle = _parse_bundle(_load_json_file(self._root / self._manifest.artifact_path))
        if (
            bundle.bundle_version != self._manifest.bundle_version
            or bundle.source_code_commit != self._manifest.source_code_commit
            or bundle.captured_at != self._manifest.captured_at
        ):
            raise CorpusManifestError("Demo calibration bundle provenance mismatch")
        return bundle

    def _verify_artifact(self) -> None:
        path = self._root / self._manifest.artifact_path
        if (
            not path.is_file()
            or path.stat().st_size != self._manifest.artifact_size_bytes
            or _file_sha256(path) != self._manifest.artifact_sha256
        ):
            raise CorpusHashMismatchError("Demo calibration artifact hash mismatch")


def _parse_manifest(raw: object) -> DemoCalibrationManifest:
    item = _require_mapping(raw, "Demo calibration manifest")
    try:
        manifest = DemoCalibrationManifest(
            schema_version=_strict_text(item["schema_version"], "manifest schema"),
            bundle_version=_strict_text(item["bundle_version"], "manifest version"),
            source_code_commit=_strict_text(item["source_code_commit"], "manifest code SHA"),
            captured_at=_parse_utc(item["captured_at"], "manifest captured_at"),
            artifact_path=_strict_text(item["artifact_path"], "artifact path"),
            artifact_sha256=_strict_text(item["artifact_sha256"], "artifact SHA-256"),
            artifact_size_bytes=_strict_integer(item["artifact_size_bytes"], "artifact size"),
        )
    except KeyError as error:
        raise CorpusManifestError("Demo calibration manifest is incomplete") from error
    manifest.validate()
    return manifest


def _parse_bundle(raw: object) -> DemoCalibrationBundle:
    item = _require_mapping(raw, "Demo calibration bundle")
    try:
        bundle = DemoCalibrationBundle(
            schema_version=_strict_text(item["schema_version"], "schema version"),
            bundle_version=_strict_text(item["bundle_version"], "bundle version"),
            protocol_version=_strict_text(item["protocol_version"], "protocol version"),
            captured_at=_parse_utc(item["captured_at"], "captured_at"),
            completed_at=_parse_utc(item["completed_at"], "completed_at"),
            source_code_commit=_strict_text(item["source_code_commit"], "source code SHA"),
            authorized_open_order=_order_from_json(
                _require_mapping(item["authorized_open_order"], "authorized order")
            ),
            source_observations=tuple(
                _source_observation_from_json(value)
                for value in _mapping_items(item["source_observations"])
            ),
            baseline_balance_sha256=_optional_text(item["baseline_balance_sha256"]),
            baseline_position_sha256=_optional_text(item["baseline_position_sha256"]),
            baseline_order_history_sha256=_optional_text(item["baseline_order_history_sha256"]),
            baseline_balance_count=_strict_integer(item["baseline_balance_count"], "balance count"),
            baseline_position_count=_strict_integer(
                item["baseline_position_count"], "position count"
            ),
            baseline_order_history_count=_strict_integer(
                item["baseline_order_history_count"], "history count"
            ),
            demo_mark=_optional_mapping(item["demo_mark"], _mark_from_json),
            demo_premium=_optional_mapping(item["demo_premium"], _premium_from_json),
            demo_contract=_optional_mapping(item["demo_contract"], _contract_from_json),
            open_submission=_optional_mapping(item["open_submission"], _submission_from_json),
            observed_position=_optional_mapping(
                item["observed_position"], forward._position_from_json
            ),
            opening_order_attribution=_optional_enum(
                item["opening_order_attribution"], CalibrationDisposition
            ),
            observed_order=_optional_mapping(item["observed_order"], forward._order_from_json),
            comparisons=tuple(
                _comparison_from_json(value) for value in _mapping_items(item["comparisons"])
            ),
            liquidation_price=_optional_decimal(item["liquidation_price"], "liquidation price"),
            cleanup_order=_optional_mapping(item["cleanup_order"], _order_from_json),
            cleanup_submission=_optional_mapping(item["cleanup_submission"], _submission_from_json),
            cleanup_confirmed=_optional_bool(item["cleanup_confirmed"], "cleanup_confirmed"),
            statuses=tuple(
                DemoCalibrationStatus(value) for value in _string_items(item["statuses"])
            ),
            demo_only=_strict_bool(item["demo_only"], "demo_only"),
            production_equivalence_claimed=_strict_bool(
                item["production_equivalence_claimed"], "production equivalence"
            ),
            historical_authority_claimed=_strict_bool(
                item["historical_authority_claimed"], "historical authority"
            ),
            integrity_seal=_strict_text(item["integrity_seal"], "integrity seal"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DemoCalibrationIntegrityError("Demo calibration bundle cannot be decoded") from error
    bundle.validate()
    return bundle


def _order_from_json(value: Mapping[str, object]) -> DemoCalibrationOrder:
    return DemoCalibrationOrder(
        phase=CalibrationOrderPhase(_strict_text(value["phase"], "order phase")),
        symbol=_strict_text(value["symbol"], "order symbol"),
        side=_strict_text(value["side"], "order side"),
        position_side=_strict_text(value["position_side"], "position side"),
        order_type=_strict_text(value["order_type"], "order type"),
        quantity=_strict_decimal(value["quantity"], "order quantity"),
        client_order_id=_strict_text(value["client_order_id"], "client order ID"),
        observed_position_evidence_id=_optional_text(value["observed_position_evidence_id"]),
    )


def _source_observation_from_json(
    value: Mapping[str, object],
) -> CalibrationSourceObservation:
    return CalibrationSourceObservation(
        method=_strict_text(value["method"], "source method"),
        endpoint=_strict_text(value["endpoint"], "source endpoint"),
        parameters=_parameter_pairs(value["parameters"]),
        request_body_sha256=_optional_text(value["request_body_sha256"]),
        response_sha256=_strict_text(value["response_sha256"], "response SHA-256"),
    )


def _submission_from_json(value: Mapping[str, object]) -> DemoOrderSubmissionEvidence:
    return DemoOrderSubmissionEvidence(
        phase=CalibrationOrderPhase(_strict_text(value["phase"], "submission phase")),
        client_order_id=_strict_text(value["client_order_id"], "submission client ID"),
        order_id_hash=_strict_text(value["order_id_hash"], "order ID hash"),
        accepted=_strict_bool(value["accepted"], "submission accepted"),
        error_code=_strict_text_or_empty(value["error_code"], "error code"),
        error_message=_strict_text_or_empty(value["error_message"], "error message"),
        submitted_at=_parse_utc(value["submitted_at"], "submitted_at"),
        response_sha256=_strict_text(value["response_sha256"], "response SHA-256"),
    )


def _mark_from_json(value: Mapping[str, object]) -> DemoSymbolMarkEvidence:
    return DemoSymbolMarkEvidence(
        symbol=_strict_text(value["symbol"], "MARK symbol"),
        price=_strict_decimal(value["price"], "MARK price"),
        venue_time_ms=_strict_integer(value["venue_time_ms"], "MARK time"),
        venue_time=_parse_utc(value["venue_time"], "MARK venue time"),
    )


def _premium_from_json(value: Mapping[str, object]) -> DemoSymbolPremiumEvidence:
    return DemoSymbolPremiumEvidence(
        symbol=_strict_text(value["symbol"], "premium symbol"),
        mark_price=_strict_decimal(value["mark_price"], "premium MARK"),
        index_price=_strict_decimal(value["index_price"], "premium index"),
        last_funding_rate=_strict_decimal(value["last_funding_rate"], "last funding rate"),
        forecast_funding_rate=_strict_decimal(
            value["forecast_funding_rate"], "forecast funding rate"
        ),
        next_funding_time_ms=_strict_integer(value["next_funding_time_ms"], "next funding time"),
        next_funding_time=_parse_utc(value["next_funding_time"], "next funding time"),
        venue_time_ms=_strict_integer(value["venue_time_ms"], "premium venue time"),
        venue_time=_parse_utc(value["venue_time"], "premium venue time"),
    )


def _contract_from_json(value: Mapping[str, object]) -> DemoSymbolContractEvidence:
    return DemoSymbolContractEvidence(
        symbol=_strict_text(value["symbol"], "contract symbol"),
        base_asset=_strict_text(value["base_asset"], "base asset"),
        quote_asset=_strict_text(value["quote_asset"], "quote asset"),
        margin_asset=_strict_text(value["margin_asset"], "margin asset"),
        contract_type=_strict_text(value["contract_type"], "contract type"),
        contract_val=_strict_decimal(value["contract_val"], "contract value"),
        min_leverage=_strict_decimal(value["min_leverage"], "minimum leverage"),
        max_leverage=_strict_decimal(value["max_leverage"], "maximum leverage"),
        quantity_precision=_strict_integer(value["quantity_precision"], "quantity precision"),
        min_order_size=_strict_decimal(value["min_order_size"], "minimum order size"),
        max_order_size=_strict_decimal(value["max_order_size"], "maximum order size"),
        maker_fee_rate=_strict_decimal(value["maker_fee_rate"], "maker fee"),
        taker_fee_rate=_strict_decimal(value["taker_fee_rate"], "taker fee"),
        quantity_semantics_qualified=_strict_bool(
            value["quantity_semantics_qualified"], "quantity semantics"
        ),
    )


def _comparison_from_json(value: Mapping[str, object]) -> CalibrationComparison:
    return CalibrationComparison(
        kind=CalibrationComparisonKind(_strict_text(value["kind"], "comparison kind")),
        disposition=CalibrationDisposition(
            _strict_text(value["disposition"], "comparison disposition")
        ),
        left_value=_optional_decimal(value["left_value"], "left value"),
        right_value=_optional_decimal(value["right_value"], "right value"),
        exact_delta=_optional_decimal(value["exact_delta"], "exact delta"),
        rationale=_strict_text(value["rationale"], "comparison rationale"),
    )


def _from_get_observation(
    value: forward.SourceObservation,
) -> CalibrationSourceObservation:
    return CalibrationSourceObservation(
        method="GET",
        endpoint=value.endpoint,
        parameters=value.parameters,
        request_body_sha256=None,
        response_sha256=value.response_sha256,
    )


def _payload_object(payload: object, field: str) -> Mapping[str, object]:
    return _require_mapping(forward._unwrap_payload(payload), field)


def _payload_list(payload: object, field: str) -> list[object]:
    value = forward._unwrap_payload(payload)
    if not isinstance(value, list):
        raise DemoCalibrationIntegrityError(f"{field} must be an array")
    return value


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise DemoCalibrationIntegrityError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _strict_text(value: object, field: str) -> str:
    try:
        result = forward._strict_text(value, field)
    except forward.ForwardEvidenceIntegrityError as error:
        raise DemoCalibrationIntegrityError(str(error)) from error
    return result


def _strict_text_or_empty(value: object, field: str) -> str:
    if value == "":
        return ""
    return _strict_text(value, field)


def _sanitized_venue_error_code(value: object, *, accepted: bool) -> str:
    if accepted:
        if value not in (None, ""):
            raise DemoCalibrationIntegrityError("accepted response contains a venue error code")
        return ""
    if isinstance(value, str) and _VENUE_ERROR_CODE.fullmatch(value):
        return value
    return "UNAVAILABLE"


def _require_safe_text(value: str, field: str, *, allow_empty: bool = False) -> None:
    if allow_empty and value == "":
        return
    _strict_text(value, field)


def _strict_decimal(value: object, field: str) -> Decimal:
    try:
        return forward._strict_decimal(value, field)
    except forward.ForwardEvidenceIntegrityError as error:
        raise DemoCalibrationIntegrityError(str(error)) from error


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else _strict_decimal(value, field)


def _strict_integer(value: object, field: str) -> int:
    try:
        return forward._strict_integer(value, field)
    except forward.ForwardEvidenceIntegrityError as error:
        raise DemoCalibrationIntegrityError(str(error)) from error


def _strict_identifier(value: object, field: str) -> str:
    try:
        return forward._strict_identifier(value, field)
    except forward.ForwardEvidenceIntegrityError as error:
        raise DemoCalibrationIntegrityError(str(error)) from error


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise DemoCalibrationIntegrityError(f"{field} must be boolean")
    return value


def _optional_bool(value: object, field: str) -> bool | None:
    return None if value is None else _strict_bool(value, field)


def _optional_text(value: object) -> str | None:
    return None if value is None else _strict_text(value, "optional text")


def _optional_mapping[T](value: object, parser: Callable[[Mapping[str, object]], T]) -> T | None:
    return None if value is None else parser(_require_mapping(value, "optional evidence"))


def _optional_enum[T: StrEnum](value: object, enum_type: type[T]) -> T | None:
    return None if value is None else enum_type(_strict_text(value, "optional enum"))


def _mapping_items(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise DemoCalibrationIntegrityError("canonical collection must be an array")
    return tuple(_require_mapping(item, "canonical item") for item in value)


def _string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise DemoCalibrationIntegrityError("canonical strings must be an array")
    return tuple(_strict_text(item, "canonical string") for item in value)


def _parameter_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise DemoCalibrationIntegrityError("parameters must be an array")
    result: list[tuple[str, str]] = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise DemoCalibrationIntegrityError("parameter must be a key/value pair")
        result.append((_strict_text(pair[0], "parameter key"), _strict_text(pair[1], "parameter")))
    return tuple(result)


def _require_finite_decimal(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DemoCalibrationIntegrityError(f"{field} must be a finite Decimal")


def _require_positive_decimal(value: Decimal, field: str) -> None:
    _require_finite_decimal(value, field)
    if value <= 0:
        raise DemoCalibrationIntegrityError(f"{field} must be positive")


def _require_timestamp_pair(milliseconds: int, value: datetime, field: str) -> None:
    if type(milliseconds) is not int or milliseconds <= 0:
        raise DemoCalibrationIntegrityError(f"{field} milliseconds are invalid")
    if require_utc(value, field=field) != _from_milliseconds(milliseconds):
        raise DemoCalibrationIntegrityError(f"{field} datetime mismatch")


def _from_milliseconds(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError) as error:
        raise DemoCalibrationIntegrityError("timestamp is outside the supported range") from error


def _parse_utc(value: object, field: str) -> datetime:
    text = _strict_text(value, field)
    try:
        return require_utc(datetime.fromisoformat(text.replace("Z", "+00:00")), field=field)
    except ValueError as error:
        raise DemoCalibrationIntegrityError(f"{field} is not UTC") from error


def _response_sha(payload: object) -> str:
    return sha256(_canonical_json(_canonical_value(payload)).encode()).hexdigest()


def _identifier_hash(*parts: str) -> str:
    return sha256(_canonical_json(list(parts)).encode()).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if not _HEX_SHA256.fullmatch(value):
        raise DemoCalibrationIntegrityError(f"{field} must be lowercase SHA-256")


def _canonical_value(value: object, *, excluded: frozenset[str] = frozenset()) -> object:
    try:
        return forward._canonical_value(value, excluded=excluded)
    except forward.ForwardEvidenceIntegrityError as error:
        raise DemoCalibrationIntegrityError(str(error)) from error


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bundle_identity_sha(bundle: DemoCalibrationBundle) -> str:
    value = _canonical_value(bundle, excluded=frozenset({"bundle_version", "integrity_seal"}))
    return sha256(_canonical_json(value).encode()).hexdigest()


def _integrity_sha(bundle: DemoCalibrationBundle) -> str:
    value = _canonical_value(bundle, excluded=frozenset({"integrity_seal"}))
    return sha256(_canonical_json(value).encode()).hexdigest()


def _append_unique[T](values: list[T], value: T) -> None:
    if value not in values:
        values.append(value)


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
        raise CorpusManifestError("Demo calibration JSON cannot be decoded") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = ArgumentParser(description="Run the one bounded WEEX Demo LONG calibration")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-code-commit", required=True)
    parser.add_argument("--captured-at", required=True)
    arguments = parser.parse_args(argv)
    credentials = forward.WeexDemoCredentials.from_environment()
    reader = None if credentials is None else forward.WeexDemoReadOnlyClient(credentials)
    writer = (
        None
        if credentials is None
        else WeexDemoCalibrationWriter(credentials, _UrlLibDemoOrderPostTransport())
    )
    runner = WeexDemoLongCalibrationRunner(
        public_client=forward.WeexPublicCurrentClient(),
        demo_reader=reader,
        demo_writer=writer,
    )
    bundle = runner.run(
        captured_at=_parse_utc(arguments.captured_at, "captured_at"),
        source_code_commit=arguments.source_code_commit,
    )
    manifest = write_calibration_bundle(bundle, arguments.output_root)
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
