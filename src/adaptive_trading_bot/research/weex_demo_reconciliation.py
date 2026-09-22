"""Read-only, bounded reconciliation of the one ambiguous WEEX Demo opening."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Final

from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.research import weex_forward_evidence as forward
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusManifestError,
)

SCHEMA_VERSION: Final = "weex-demo-reconciliation-v1"
MANIFEST_SCHEMA_VERSION: Final = "weex-demo-reconciliation-manifest-v1"
BASELINE_COMMIT: Final = "8fc066ab2b5873dae01f460213f6fee2b999052e"
SOURCE_BUNDLE_VERSION: Final = "weex-demo-calibration-08376b6b937422f89c4b4aab"
SOURCE_MANIFEST_SHA256: Final = "189f8a20747a04a022a96807d6d2d79a192f671a9068a72a34bdcc3a09270ecc"
CLIENT_ORDER_ID: Final = "atb-132-btcsusdt-long-open-v1"
SYMBOL: Final = "BTCSUSDT"
START_MS: Final = 1790097000000  # 2026-09-22T17:10:00.000Z
END_MS: Final = 1790097600000  # 2026-09-22T17:20:00.000Z
PAGE_LIMIT: Final = 100
MAX_PAGES: Final = 5
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"weex-demo-reconciliation-[0-9a-f]{24}")

# Exact evidence identity produced by the single reviewed Issue #142 GET capture.
LOCKED_RECONCILIATION_MANIFESTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "weex-demo-reconciliation-4a17f9406b037ce84d6ce288": (
            "28f2c963fa8624c73b9e00a0f7697f3c703f158495fe06ea7ba98b7707702c61"
        )
    }
)


class ReconciliationError(ValueError):
    """Malformed or inconsistent reconciliation evidence."""


class ReconciliationStatus(StrEnum):
    DEMO_AMBIGUOUS_ORDER_EXACTLY_RECORDED = "DEMO_AMBIGUOUS_ORDER_EXACTLY_RECORDED"
    DEMO_AMBIGUOUS_ORDER_NOT_FOUND = "DEMO_AMBIGUOUS_ORDER_NOT_FOUND"
    DEMO_AMBIGUOUS_ORDER_DUPLICATE = "DEMO_AMBIGUOUS_ORDER_DUPLICATE"
    DEMO_AMBIGUOUS_ORDER_CONFLICT = "DEMO_AMBIGUOUS_ORDER_CONFLICT"
    DEMO_RECONCILIATION_CURRENT_POSITION_PRESENT = "DEMO_RECONCILIATION_CURRENT_POSITION_PRESENT"
    DEMO_RECONCILIATION_UNAVAILABLE = "DEMO_RECONCILIATION_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class HistoryPage:
    observation: forward.SourceObservation
    row_count: int

    def validate(self, page: int) -> None:
        expected = tuple(
            sorted(
                (
                    ("endTime", str(END_MS)),
                    ("limit", str(PAGE_LIMIT)),
                    ("page", str(page)),
                    ("startTime", str(START_MS)),
                    ("symbol", SYMBOL),
                )
            )
        )
        if (
            self.observation.endpoint != forward.DEMO_ORDER_HISTORY_PATH
            or self.observation.parameters != expected
            or not 0 <= self.row_count <= PAGE_LIMIT
        ):
            raise ReconciliationError("history page is outside the fixed query protocol")


@dataclass(frozen=True, slots=True)
class MatchingOrder:
    client_order_id: str
    order_id_sha256: str
    response_sha256: str
    symbol: str
    side: str
    position_side: str
    order_type: str
    status: str
    original_quantity: Decimal
    executed_quantity: Decimal | None
    average_price: Decimal | None
    cumulative_quote: Decimal | None
    created_time_ms: int | None
    updated_time_ms: int | None

    def validate(self) -> None:
        if self.client_order_id != CLIENT_ORDER_ID:
            raise ReconciliationError("matching row has the wrong client identity")
        for value in (self.order_id_sha256, self.response_sha256):
            if not _SHA256.fullmatch(value):
                raise ReconciliationError("matching row digest is invalid")
        for text_value in (
            self.symbol,
            self.side,
            self.position_side,
            self.order_type,
            self.status,
        ):
            if not text_value or text_value.strip() != text_value:
                raise ReconciliationError("matching row text is invalid")
        for decimal_value in (
            self.original_quantity,
            self.executed_quantity,
            self.average_price,
            self.cumulative_quote,
        ):
            if decimal_value is not None and (not decimal_value.is_finite() or decimal_value < 0):
                raise ReconciliationError("matching row Decimal is invalid")
        for time_value in (self.created_time_ms, self.updated_time_ms):
            if time_value is not None and time_value < 0:
                raise ReconciliationError("matching row timestamp is invalid")

    @property
    def protocol_consistent(self) -> bool:
        event_in_window = any(
            timestamp is not None and START_MS <= timestamp <= END_MS
            for timestamp in (self.created_time_ms, self.updated_time_ms)
        )
        return (
            self.symbol == SYMBOL
            and self.side == "BUY"
            and self.position_side == "LONG"
            and self.order_type == "MARKET"
            and self.original_quantity == Decimal("0.01")
            and event_in_window
            and (
                self.created_time_ms is None
                or self.updated_time_ms is None
                or self.updated_time_ms >= self.created_time_ms
            )
        )


@dataclass(frozen=True, slots=True)
class ReconciliationBundle:
    schema_version: str
    bundle_version: str
    baseline_main_commit: str
    source_bundle_version: str
    source_manifest_sha256: str
    client_order_id: str
    symbol: str
    start_time_ms: int
    end_time_ms: int
    page_limit: int
    maximum_pages: int
    captured_at: datetime
    completed_at: datetime
    position_observation: forward.SourceObservation | None
    position_result_count: int | None
    current_btcsusdt_positions: tuple[forward.DemoPositionEvidence, ...]
    history_requests_attempted: int
    history_query_failed: bool
    history_pages: tuple[HistoryPage, ...]
    inspected_row_count: int
    exact_match_count: int
    matching_orders: tuple[MatchingOrder, ...]
    current_position_present: bool
    query_failure: bool
    terminal_status: ReconciliationStatus
    demo_only: bool
    state_changing_request_performed: bool
    historical_authority_claimed: bool
    production_equivalence_claimed: bool
    authority_exclusions: tuple[str, ...]
    integrity_seal: str

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION or not _VERSION.fullmatch(self.bundle_version):
            raise ReconciliationError("reconciliation schema or version is invalid")
        seed = replace(
            self,
            bundle_version="weex-demo-reconciliation-" + "0" * 24,
            integrity_seal="pending",
        )
        if (
            self.bundle_version
            != "weex-demo-reconciliation-" + sha256(_json(seed).encode()).hexdigest()[:24]
        ):
            raise ReconciliationError("reconciliation bundle identity mismatch")
        if (
            self.baseline_main_commit != BASELINE_COMMIT
            or self.source_bundle_version != SOURCE_BUNDLE_VERSION
            or self.source_manifest_sha256 != SOURCE_MANIFEST_SHA256
            or self.client_order_id != CLIENT_ORDER_ID
            or self.symbol != SYMBOL
            or self.start_time_ms != START_MS
            or self.end_time_ms != END_MS
            or self.page_limit != PAGE_LIMIT
            or self.maximum_pages != MAX_PAGES
        ):
            raise ReconciliationError("reconciliation authority or protocol changed")
        if require_utc(self.captured_at, field="capture time") > require_utc(
            self.completed_at, field="completion time"
        ):
            raise ReconciliationError("reconciliation chronology is invalid")
        if self.position_observation is None:
            if self.position_result_count is not None or self.current_btcsusdt_positions:
                raise ReconciliationError("position evidence is inconsistent")
        elif (
            self.position_observation.endpoint != forward.DEMO_POSITIONS_PATH
            or self.position_observation.parameters
            or self.position_result_count is None
            or self.position_result_count < len(self.current_btcsusdt_positions)
        ):
            raise ReconciliationError("position query evidence is inconsistent")
        if self.current_position_present != bool(self.current_btcsusdt_positions):
            raise ReconciliationError("current position flag is inconsistent")
        if any(position.symbol != SYMBOL for position in self.current_btcsusdt_positions):
            raise ReconciliationError("position symbol is inconsistent")
        if len(self.history_pages) > MAX_PAGES:
            raise ReconciliationError("history exceeds the fixed page cap")
        if (
            not 1 <= self.history_requests_attempted <= MAX_PAGES
            or self.history_requests_attempted < len(self.history_pages)
            or self.history_requests_attempted > len(self.history_pages) + 1
            or (self.history_requests_attempted != len(self.history_pages))
            != self.history_query_failed
        ):
            raise ReconciliationError("history request accounting is inconsistent")
        if self.query_failure != (self.position_observation is None or self.history_query_failed):
            raise ReconciliationError("query failure disposition is inconsistent")
        for page_number, page in enumerate(self.history_pages):
            page.validate(page_number)
            if page_number < len(self.history_pages) - 1 and page.row_count != PAGE_LIMIT:
                raise ReconciliationError("pagination continued after a short page")
        if self.inspected_row_count != sum(page.row_count for page in self.history_pages):
            raise ReconciliationError("history row count is inconsistent")
        if self.exact_match_count != len(self.matching_orders):
            raise ReconciliationError("exact match count is inconsistent")
        page_digests = {page.observation.response_sha256 for page in self.history_pages}
        for order in self.matching_orders:
            order.validate()
            if order.response_sha256 not in page_digests:
                raise ReconciliationError("matching row lacks page provenance")
        if self.exact_match_count > self.inspected_row_count:
            raise ReconciliationError("exact match count exceeds inspected rows")
        expected = _classify(
            self.position_observation is not None,
            self.history_pages,
            self.current_position_present,
            self.matching_orders,
            self.query_failure,
        )
        if self.terminal_status != expected:
            raise ReconciliationError("terminal classification is inconsistent")
        if (
            self.demo_only is not True
            or self.state_changing_request_performed is not False
            or self.historical_authority_claimed is not False
            or self.production_equivalence_claimed is not False
            or self.authority_exclusions != AUTHORITY_EXCLUSIONS
        ):
            raise ReconciliationError("reconciliation authority exclusions changed")
        if self.integrity_seal != _seal(self):
            raise ReconciliationError("reconciliation integrity seal mismatch")

    def to_json(self) -> str:
        self.validate()
        return _json(self)


AUTHORITY_EXCLUSIONS: Final = (
    "No proof that the ambiguous HTTP client received a venue response.",
    "No contract quantity, margin, MMR, liquidation, or historical policy authority.",
    "No Demo-to-production equivalence or position-mechanics recovery.",
)


@dataclass(frozen=True, slots=True)
class ReconciliationManifest:
    schema_version: str
    bundle_version: str
    artifact_path: str
    artifact_sha256: str
    artifact_size_bytes: int

    def validate(self) -> None:
        if (
            self.schema_version != MANIFEST_SCHEMA_VERSION
            or not _VERSION.fullmatch(self.bundle_version)
            or self.artifact_path != "bundle.json"
            or not _SHA256.fullmatch(self.artifact_sha256)
            or self.artifact_size_bytes <= 0
        ):
            raise ReconciliationError("reconciliation manifest is invalid")

    def to_json(self) -> str:
        self.validate()
        return _json(self)


class DemoReconciliationRunner:
    """Owns only the reviewed signed GET client; no write transport is reachable."""

    def __init__(self, client: forward.WeexDemoReadOnlyClient) -> None:
        self._client = client

    def capture(self, *, captured_at: datetime) -> ReconciliationBundle:
        captured_at = require_utc(captured_at, field="capture time")
        position_observation: forward.SourceObservation | None = None
        position_count: int | None = None
        current_positions: tuple[forward.DemoPositionEvidence, ...] = ()
        pages: list[HistoryPage] = []
        matches: list[MatchingOrder] = []
        unavailable = False
        history_query_failed = False
        history_requests_attempted = 0

        # Exactly one current position GET precedes all history requests.
        try:
            raw, position_observation = self._client.request("GET", forward.DEMO_POSITIONS_PATH)
            parsed = forward._parse_demo_positions(raw)
            position_count = len(parsed)
            current_positions = tuple(position for position in parsed if position.symbol == SYMBOL)
        except (forward.ForwardEvidenceError, ValueError, TypeError, KeyError):
            unavailable = True
            position_observation = None
            position_count = None
            current_positions = ()

        for page_number in range(MAX_PAGES):
            history_requests_attempted += 1
            parameters = {
                "symbol": SYMBOL,
                "startTime": str(START_MS),
                "endTime": str(END_MS),
                "limit": str(PAGE_LIMIT),
                "page": str(page_number),
            }
            try:
                raw, observation = self._client.request(
                    "GET", forward.DEMO_ORDER_HISTORY_PATH, parameters
                )
                rows = forward._payload_list(raw, "Demo order history")
                if len(rows) > PAGE_LIMIT:
                    raise ReconciliationError("history page exceeds requested limit")
                page_matches = _matching_rows(rows, observation.response_sha256)
            except (
                forward.ForwardEvidenceError,
                ReconciliationError,
                ValueError,
                TypeError,
                KeyError,
            ):
                unavailable = True
                history_query_failed = True
                break
            pages.append(HistoryPage(observation, len(rows)))
            matches.extend(page_matches)
            if len(rows) < PAGE_LIMIT:
                break

        finished_at = datetime.now(tz=UTC)
        seed = ReconciliationBundle(
            schema_version=SCHEMA_VERSION,
            bundle_version="weex-demo-reconciliation-" + "0" * 24,
            baseline_main_commit=BASELINE_COMMIT,
            source_bundle_version=SOURCE_BUNDLE_VERSION,
            source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            client_order_id=CLIENT_ORDER_ID,
            symbol=SYMBOL,
            start_time_ms=START_MS,
            end_time_ms=END_MS,
            page_limit=PAGE_LIMIT,
            maximum_pages=MAX_PAGES,
            captured_at=captured_at,
            completed_at=finished_at,
            position_observation=position_observation,
            position_result_count=position_count,
            current_btcsusdt_positions=current_positions,
            history_requests_attempted=history_requests_attempted,
            history_query_failed=history_query_failed,
            history_pages=tuple(pages),
            inspected_row_count=sum(page.row_count for page in pages),
            exact_match_count=len(matches),
            matching_orders=tuple(matches),
            current_position_present=bool(current_positions),
            query_failure=unavailable,
            terminal_status=_classify(
                position_observation is not None,
                tuple(pages),
                bool(current_positions),
                tuple(matches),
                unavailable,
            ),
            demo_only=True,
            state_changing_request_performed=False,
            historical_authority_claimed=False,
            production_equivalence_claimed=False,
            authority_exclusions=AUTHORITY_EXCLUSIONS,
            integrity_seal="pending",
        )
        version = "weex-demo-reconciliation-" + sha256(_json(seed).encode()).hexdigest()[:24]
        versioned = replace(seed, bundle_version=version)
        sealed = replace(versioned, integrity_seal=_seal(versioned))
        sealed.validate()
        return sealed


def _matching_rows(rows: list[object], response_sha256: str) -> tuple[MatchingOrder, ...]:
    matches: list[MatchingOrder] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ReconciliationError("history row is not an object")
        client_id = row.get("clientOrderId")
        if not isinstance(client_id, str) or not client_id:
            raise ReconciliationError("history row lacks client identity")
        if row.get("symbol") != SYMBOL:
            raise ReconciliationError("history row violates the requested symbol")
        if client_id == CLIENT_ORDER_ID:
            matches.append(_matching_order(row, response_sha256))
    return tuple(matches)


def _matching_order(row: Mapping[str, object], response_sha256: str) -> MatchingOrder:
    raw_order_id = row.get("orderId")
    if not isinstance(raw_order_id, (int, str)) or isinstance(raw_order_id, bool):
        raise ReconciliationError("matching order lacks a valid order ID")
    order_id = str(raw_order_id)
    if not order_id or not order_id.isascii() or not order_id.isdigit():
        raise ReconciliationError("matching order ID is invalid")

    def text(key: str) -> str:
        value = row.get(key)
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ReconciliationError("matching order text is unavailable")
        return value

    def number(key: str, *, required: bool = False) -> Decimal | None:
        value = row.get(key)
        if value is None and not required:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ReconciliationError("matching order numeric fact is unavailable")
        try:
            result = Decimal(str(value))
        except InvalidOperation as error:
            raise ReconciliationError("matching order numeric fact is invalid") from error
        if not result.is_finite() or result < 0:
            raise ReconciliationError("matching order numeric fact is invalid")
        return result

    def timestamp(key: str) -> int | None:
        value = row.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReconciliationError("matching order timestamp is invalid")
        return value

    original = number("origQty", required=True)
    if original is None:
        raise ReconciliationError("matching order quantity is unavailable")
    order = MatchingOrder(
        client_order_id=CLIENT_ORDER_ID,
        order_id_sha256=sha256(order_id.encode()).hexdigest(),
        response_sha256=response_sha256,
        symbol=text("symbol"),
        side=text("side"),
        position_side=text("positionSide"),
        order_type=text("type"),
        status=text("status"),
        original_quantity=original,
        executed_quantity=number("executedQty"),
        average_price=number("avgPrice"),
        cumulative_quote=number("cumQuote"),
        created_time_ms=timestamp("time"),
        updated_time_ms=timestamp("updateTime"),
    )
    order.validate()
    return order


def _classify(
    position_available: bool,
    pages: tuple[HistoryPage, ...],
    current_position_present: bool,
    orders: tuple[MatchingOrder, ...],
    query_failure: bool,
) -> ReconciliationStatus:
    if query_failure or not pages or pages[-1].row_count == PAGE_LIMIT:
        return ReconciliationStatus.DEMO_RECONCILIATION_UNAVAILABLE
    if position_available and current_position_present:
        return ReconciliationStatus.DEMO_RECONCILIATION_CURRENT_POSITION_PRESENT
    if len(orders) > 1:
        return ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_DUPLICATE
    if len(orders) == 1:
        return (
            ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_EXACTLY_RECORDED
            if orders[0].protocol_consistent
            else ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_CONFLICT
        )
    if not position_available:
        return ReconciliationStatus.DEMO_RECONCILIATION_UNAVAILABLE
    return ReconciliationStatus.DEMO_AMBIGUOUS_ORDER_NOT_FOUND


def _canonical(value: object, *, omit_seal: bool = False) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return require_utc(value, field="evidence time").isoformat().replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(
        value,
        (
            ReconciliationBundle,
            ReconciliationManifest,
            HistoryPage,
            MatchingOrder,
            forward.SourceObservation,
            forward.DemoPositionEvidence,
        ),
    ):
        return {
            field.name: _canonical(getattr(value, field.name), omit_seal=omit_seal)
            for field in fields(value)
            if not (omit_seal and field.name == "integrity_seal")
        }
    if isinstance(value, tuple):
        return [_canonical(item, omit_seal=omit_seal) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise ReconciliationError("unsupported evidence value")


def _json(value: object, *, omit_seal: bool = False) -> str:
    return json.dumps(_canonical(value, omit_seal=omit_seal), sort_keys=True, separators=(",", ":"))


def _seal(bundle: ReconciliationBundle) -> str:
    return sha256(_json(bundle, omit_seal=True).encode()).hexdigest()


def write_bundle(bundle: ReconciliationBundle, root: Path) -> ReconciliationManifest:
    bundle.validate()
    if root.exists():
        raise FileExistsError("reconciliation output root already exists")
    root.mkdir(parents=True)
    artifact = (bundle.to_json() + "\n").encode()
    (root / "bundle.json").write_bytes(artifact)
    manifest = ReconciliationManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        bundle_version=bundle.bundle_version,
        artifact_path="bundle.json",
        artifact_sha256=sha256(artifact).hexdigest(),
        artifact_size_bytes=len(artifact),
    )
    (root / "manifest.json").write_bytes((manifest.to_json() + "\n").encode())
    return manifest


def verify_bundle(root: Path, *, bundle_version: str) -> None:
    expected_manifest_sha256 = LOCKED_RECONCILIATION_MANIFESTS.get(bundle_version)
    if expected_manifest_sha256 is None:
        raise CorpusManifestError("reconciliation bundle version is not project-qualified")
    _verify_bundle(
        root, expected_version=bundle_version, expected_manifest_sha256=expected_manifest_sha256
    )


def _verify_bundle(root: Path, *, expected_version: str, expected_manifest_sha256: str) -> None:
    manifest_path = root / "manifest.json"
    if sha256(manifest_path.read_bytes()).hexdigest() != expected_manifest_sha256:
        raise CorpusHashMismatchError("reconciliation manifest hash mismatch")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("bundle_version") != expected_version:
        raise CorpusManifestError("reconciliation version is unknown")
    manifest = ReconciliationManifest(**raw)
    manifest.validate()
    artifact_path = root / manifest.artifact_path
    artifact = artifact_path.read_bytes()
    if (
        len(artifact) != manifest.artifact_size_bytes
        or sha256(artifact).hexdigest() != manifest.artifact_sha256
    ):
        raise CorpusHashMismatchError("reconciliation artifact hash mismatch")


def main() -> int:
    """One explicit read-only capture; prints only sanitized disposition metadata."""
    import argparse

    parser = argparse.ArgumentParser(description="Capture the fixed Issue #142 Demo GET evidence")
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    credentials = forward.WeexDemoCredentials.from_environment()
    if credentials is None:
        raise ReconciliationError("dedicated Demo credentials are unavailable")
    client = forward.WeexDemoReadOnlyClient(credentials)
    bundle = DemoReconciliationRunner(client).capture(captured_at=datetime.now(tz=UTC))
    manifest = write_bundle(bundle, args.output_root / bundle.bundle_version)
    print(
        json.dumps(
            {
                "bundle_version": bundle.bundle_version,
                "terminal_status": bundle.terminal_status.value,
                "current_position_present": bundle.current_position_present,
                "pages": len(bundle.history_pages),
                "rows": bundle.inspected_row_count,
                "exact_matches": bundle.exact_match_count,
                "artifact_sha256": manifest.artifact_sha256,
                "manifest_sha256": sha256(
                    (args.output_root / bundle.bundle_version / "manifest.json").read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
