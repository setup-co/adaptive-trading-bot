"""Explicit, offline-only binding of the accepted C2 LAST corpus.

This is a normalized-base research input, not WEEX contract execution authority.
No generic historical root, API fallback, or trading entry point selects it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
)
from adaptive_trading_bot.domain import (
    Candle,
    FrozenClock,
    Timeframe,
    TradingSymbol,
    align_timestamp,
)
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.domain.entry_chronology import entry_source_start
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalBatch,
    DecisionJournalEntry,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.regime import (
    MultiTimeframeRegimeComposer,
    TechnicalRegimeEngine,
    TechnicalRegimeSnapshot,
)
from adaptive_trading_bot.regime.composition import CompositionSeriesError
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research import research_v2_corpus as corpus
from adaptive_trading_bot.research import research_v2_corpus_tail as tail
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillFamily,
    AllFillReplayResult,
    ResearchAllFillReplayRunner,
)
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _integrity_seal,
    _research_health,
    _revalidate_risk_configuration,
    _revalidate_safety_configuration,
    _validate_safety_assumptions,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyConfiguration,
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    HistoricalCandleStoreError,
    IntegrityReport,
    SourceId,
)
from adaptive_trading_bot.strategies import (
    BreakoutRetestStrategy,
    RangeMeanReversionStrategy,
    TrendPullbackStrategy,
    assemble_strategy_contexts,
)
from adaptive_trading_bot.strategies.core import (
    StrategyContextError,
    StrategyEvaluationContext,
)

PROFILE = "NORMALIZED_BASE_SIGNAL_V1"
ADMISSION_VERSION = "RESEARCH_V2_ISOLATED_ADMISSION_V1"
SOURCE_ID = SourceId("weex-research-v2-last-18912cf7e7f97d518a74551f")
DATASET_VERSION = "weex-research-v2-last-90d-issue-179-combined-v2"
MANIFEST_SHA256 = "18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd"
PLAN_SHA256 = "345c797e4eb740dc9e43a23847f67be15411164d4d4888103c86f64091d1ab16"
REPORT_SHA256 = "efd51747baf786ad637ec0814eb94ade83bee9a46c4fe5941f4d0fd97a9f2ca9"
C1_MANIFEST_SHA256 = "1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed"
ORIGINAL_MANIFEST_SHA256 = "870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3"
SELECTION_MANIFEST_SHA256 = "de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79"
SELECTION_SHA256 = "80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4"
SOURCE_NOTE_SHA256 = "7eaa3a08c4f570f4cde8c110e616f55d271719eb45e318b6c81e81ae808582ce"
CATALOG_ROOT = Path("data/research_evidence/issue-173-weex-public-feasibility-332db22")
CATALOG_MANIFEST_SHA256 = "88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac"
CATALOG_ATTEMPTS_SHA256 = "77a4f1cee3b390e182dadcab49592771dbf40c6f9ed7f05f2905322f6a238424"
CATALOG_RAW_SHA256 = "1aea6ae66e18e4e092cb349504951363c245ccfff4af42593e580d0c2c128ddd"
CATALOG_CAPTURE_AT = "2026-10-14T14:16:22.590927Z"
ROOT = Path("data/research_corpus/issue-179-weex-last-90d-tail-v1")
SELECTION_ROOT = Path("data/research_evidence/issue-175-weex-kas-completion-v1")
SOURCE_NOTE = Path("docs/research/research_v2_corpus_qualification.md")
FRAMES = (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)
PANEL = tuple(corpus.PANEL)
SYNTHETIC_SOURCE_ID = SourceId("synthetic-research-v2-fixture")
SYNTHETIC_DATASET_VERSION = "synthetic-research-v2-fixture-v1"


@dataclass(frozen=True, slots=True)
class ResearchV2InputBinding:
    """A fixture digest can never masquerade as the accepted C2 manifest."""

    kind: str
    source_id: SourceId
    dataset_version: str
    manifest_sha256: str
    schema_version: str = "research-v2-input-binding-v1"

    def validate(self) -> None:
        if self.schema_version != "research-v2-input-binding-v1":
            raise ResearchV2AdmissionError("unknown research input binding schema")
        accepted = (
            self.kind == "ACCEPTED_C2"
            and self.source_id == SOURCE_ID
            and self.dataset_version == DATASET_VERSION
            and self.manifest_sha256 == MANIFEST_SHA256
        )
        synthetic = (
            self.kind == "SYNTHETIC_FIXTURE"
            and self.source_id == SYNTHETIC_SOURCE_ID
            and self.dataset_version == SYNTHETIC_DATASET_VERSION
            and len(self.manifest_sha256) == 64
            and all(character in "0123456789abcdef" for character in self.manifest_sha256)
            and self.manifest_sha256 != MANIFEST_SHA256
        )
        if not accepted and not synthetic:
            raise ResearchV2AdmissionError("unknown or contradictory research input binding")


ACCEPTED_C2_BINDING = ResearchV2InputBinding(
    "ACCEPTED_C2", SOURCE_ID, DATASET_VERSION, MANIFEST_SHA256
)


class ResearchV2AdmissionError(HistoricalCandleStoreError):
    """The exact accepted research input or requested bounded epoch is unavailable."""


class ResearchV2EpochStore(HistoricalCandleStore, Protocol):
    """Testable epoch interface; the accepted production instance is hash-locked."""

    @property
    def source_id(self) -> SourceId: ...

    def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]: ...

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]: ...


@dataclass(frozen=True, slots=True)
class ResearchV2ContextAuthorization:
    """Explicit source/member authority for isolated normalized research contexts."""

    source_id: SourceId
    symbol: TradingSymbol
    dataset_version: str = DATASET_VERSION
    manifest_sha256: str = MANIFEST_SHA256
    profile: str = PROFILE
    admission_version: str = ADMISSION_VERSION
    input_binding: ResearchV2InputBinding = ACCEPTED_C2_BINDING

    def validate(self) -> None:
        self.input_binding.validate()
        if (
            self.source_id != self.input_binding.source_id
            or f"{self.symbol.base}{self.symbol.quote}" not in PANEL
            or self.symbol.quote != "USDT"
            or self.dataset_version != self.input_binding.dataset_version
            or self.manifest_sha256 != self.input_binding.manifest_sha256
            or self.profile != PROFILE
            or self.admission_version != ADMISSION_VERSION
        ):
            raise ResearchV2AdmissionError("unknown isolated research context authority")


@dataclass(frozen=True, slots=True)
class ResearchV2StrategyContext(StrategyEvaluationContext):
    """V2-only context; the normal V1 context keeps its BTC/ETH guard."""

    research_authorization: ResearchV2ContextAuthorization

    def validate(self) -> None:
        self.research_authorization.validate()
        if (
            self.source_id != self.research_authorization.source_id
            or self.symbol != self.research_authorization.symbol
        ):
            raise StrategyContextError("research context does not match its authorization")
        self._validate_causal_facts()


class ResearchV2RegimeComposer(MultiTimeframeRegimeComposer):
    """Same derivation engine with an exact isolated member/source boundary."""

    def __init__(self, authority: ResearchV2ContextAuthorization) -> None:
        authority.validate()
        super().__init__()
        self._authority = authority

    def _validate_symbol(self, snapshot: TechnicalRegimeSnapshot) -> None:
        if (
            snapshot.source_id != self._authority.source_id
            or snapshot.symbol != self._authority.symbol
        ):
            raise CompositionSeriesError("regime series outside isolated research authority")


def _hash(path: Path) -> str:
    if not path.is_file():
        raise ResearchV2AdmissionError(f"locked research input missing: {path.name}")
    return sha256(path.read_bytes()).hexdigest()


def _exact(path: Path, digest: str) -> None:
    if not compare_digest(_hash(path), digest):
        raise ResearchV2AdmissionError(f"locked research input changed: {path.name}")


def _instant(ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


def _millis(value: datetime) -> int:
    value = require_utc(value, field="research interval")
    if value.microsecond % 1000:
        raise ResearchV2AdmissionError("research interval is not on a millisecond boundary")
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


@dataclass(frozen=True, slots=True)
class ResearchV2Member:
    """Current saved instrument identity and explicitly different modeled unit."""

    wire_symbol: str
    symbol: TradingSymbol
    base_asset: str
    quote_asset: str
    margin_asset: str
    underlying_type: str
    contract_type: str
    current_contract_val_unqualified: Decimal
    catalog_file: str
    catalog_capture_at: str
    catalog_sha256: str
    selection_freeze_at: str
    profile: str = PROFILE
    modeled_quantity_unit: str = "ONE_DECLARED_BASE_ASSET_UNIT"
    historical_contract_count_equivalence: str = "UNRESOLVED"

    def validate(self) -> None:
        if (
            self.wire_symbol not in PANEL
            or self.symbol != TradingSymbol(self.base_asset, self.quote_asset)
            or self.wire_symbol != f"{self.base_asset}{self.quote_asset}"
            or self.quote_asset != "USDT"
            or self.margin_asset != "USDT"
            or self.underlying_type != "COIN"
            or self.contract_type != "PERPETUAL"
            or self.profile != PROFILE
            or self.modeled_quantity_unit != "ONE_DECLARED_BASE_ASSET_UNIT"
            or self.historical_contract_count_equivalence != "UNRESOLVED"
            or self.catalog_file != "002-exchange_info.raw.json"
            or self.catalog_capture_at != CATALOG_CAPTURE_AT
            or self.catalog_sha256 != CATALOG_RAW_SHA256
            or not self.current_contract_val_unqualified.is_finite()
            or self.current_contract_val_unqualified <= 0
        ):
            raise ResearchV2AdmissionError("contradictory normalized-base instrument identity")


class LockedResearchV2CandleStore:
    """HistoricalCandleStore adapter over the exact accepted C2 reader only."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo.resolve(strict=True)
        self._root = self._repo / ROOT
        self._check_identity()
        self._reader = tail.LockedResearchV2TailReader(
            self._repo,
            self._root,
            dataset_version=DATASET_VERSION,
            manifest_sha256=MANIFEST_SHA256,
        )
        selection = corpus.read_json(self._repo / SELECTION_ROOT / "combined_selection.json")
        if (
            selection["status"] != "SELECTED_FOR_DATA_QUALIFICATION"
            or tuple(selection["selected_symbols"]) != PANEL
        ):
            raise ResearchV2AdmissionError("accepted selection no longer matches panel")
        candidates = {item["symbol"]: item for item in selection["candidate_records"]}
        self._members = tuple(self._member(candidates[wire], selection) for wire in PANEL)
        for member in self._members:
            member.validate()

    @property
    def source_id(self) -> SourceId:
        return SOURCE_ID

    @property
    def members(self) -> tuple[ResearchV2Member, ...]:
        return self._members

    def _member(self, item: dict[str, Any], selection: dict[str, Any]) -> ResearchV2Member:
        if not item["selected"] or item["metadata_status"] != "METADATA_ELIGIBLE":
            raise ResearchV2AdmissionError("selected instrument metadata is contradictory")
        return ResearchV2Member(
            wire_symbol=item["symbol"],
            symbol=TradingSymbol(item["base"], item["quote"]),
            base_asset=item["base"],
            quote_asset=item["quote"],
            margin_asset=item["margin"],
            underlying_type=item["underlying_type"],
            contract_type=item["contract_type"],
            current_contract_val_unqualified=Decimal(item["contract_val_current_unqualified"]),
            catalog_file=item["catalog_file"],
            catalog_capture_at=CATALOG_CAPTURE_AT,
            catalog_sha256=CATALOG_RAW_SHA256,
            selection_freeze_at=selection["selection_freeze_at"],
        )

    def _check_identity(self) -> None:
        _exact(self._root / "manifest.json", MANIFEST_SHA256)
        _exact(self._root / "plan.json", PLAN_SHA256)
        _exact(self._root / "combined/report.json", REPORT_SHA256)
        _exact(self._repo / SELECTION_ROOT / "manifest.json", SELECTION_MANIFEST_SHA256)
        _exact(self._repo / SELECTION_ROOT / "combined_selection.json", SELECTION_SHA256)
        _exact(self._repo / SOURCE_NOTE, SOURCE_NOTE_SHA256)
        catalog_root = self._repo / CATALOG_ROOT
        _exact(catalog_root / "manifest.json", CATALOG_MANIFEST_SHA256)
        _exact(catalog_root / "attempts.json", CATALOG_ATTEMPTS_SHA256)
        _exact(catalog_root / "002-exchange_info.raw.json", CATALOG_RAW_SHA256)
        catalog_attempts = corpus.read_json(catalog_root / "attempts.json")
        if not any(
            attempt["file"] == "002-exchange_info.raw.json"
            and attempt["sha256"] == CATALOG_RAW_SHA256
            and attempt["captured_at"] == CATALOG_CAPTURE_AT
            and attempt["status"] == "HTTP_OK"
            for attempt in catalog_attempts
        ):
            raise ResearchV2AdmissionError("selected catalog capture provenance changed")
        selection_manifest = corpus.read_json(self._repo / SELECTION_ROOT / "manifest.json")
        original_selection_root = (
            self._repo / "data/research_evidence/issue-175-weex-current-panel-cf541a3"
        )
        _exact(
            original_selection_root / "manifest.json",
            selection_manifest["original_manifest_sha256"],
        )
        for evidence_root, manifest in (
            (self._repo / SELECTION_ROOT, selection_manifest),
            (
                original_selection_root,
                corpus.read_json(original_selection_root / "manifest.json"),
            ),
        ):
            for entry in manifest["files"]:
                evidence_file = evidence_root / entry["file"]
                if evidence_file.stat().st_size != entry["byte_length"]:
                    raise ResearchV2AdmissionError("selected-panel evidence length changed")
                _exact(evidence_file, entry["sha256"])
        _exact(
            self._repo / "data/research_corpus/issue-179-weex-last-90d-v1/manifest.json",
            ORIGINAL_MANIFEST_SHA256,
        )
        from adaptive_trading_bot.research import research_v2_corpus_continuation as c1

        _exact(self._repo / c1.ROOT / "manifest.json", C1_MANIFEST_SHA256)
        for root in (
            self._repo / "data/research_corpus/issue-179-weex-last-90d-v1",
            self._repo / c1.ROOT,
            self._root,
        ):
            manifest = corpus.read_json(root / "manifest.json")
            for relative, digest in manifest["files"].items():
                _exact(root / relative, digest)

    def _report_symbol(self, symbol: TradingSymbol) -> dict[str, Any]:
        self._check_identity()
        wire = f"{symbol.base}{symbol.quote}"
        if wire not in PANEL or self._members[PANEL.index(wire)].symbol != symbol:
            raise ResearchV2AdmissionError("foreign research symbol")
        selection = corpus.read_json(self._repo / SELECTION_ROOT / "combined_selection.json")
        candidates = {item["symbol"]: item for item in selection["candidate_records"]}
        if self._members != tuple(self._member(candidates[name], selection) for name in PANEL):
            raise ResearchV2AdmissionError("cached research member authority changed")
        report = corpus.read_json(self._root / "combined/report.json")
        return cast(dict[str, Any], report["symbols"][wire])

    def _request(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[str, int, int]:
        facts = self._report_symbol(symbol)
        wire = f"{symbol.base}{symbol.quote}"
        if (
            source_id != SOURCE_ID
            or wire not in PANEL
            or self._members[PANEL.index(wire)].symbol != symbol
        ):
            raise ResearchV2AdmissionError("foreign research source or symbol")
        if (
            not isinstance(timeframe, Timeframe)
            or timeframe not in FRAMES
            or start is None
            or end is None
        ):
            raise ResearchV2AdmissionError("explicit supported frame and finite range required")
        begin, finish = _millis(start), _millis(end)
        step = int(timeframe.duration.total_seconds() * 1000)
        if begin % step or finish % step:
            raise ResearchV2AdmissionError("research read is off the exact timeframe grid")
        if begin >= finish or not any(
            epoch_start <= begin and finish <= epoch_end
            for epoch in facts["epochs"]
            for epoch_start, epoch_end in ((epoch["start_ms"], epoch["end_ms"]),)
        ):
            raise ResearchV2AdmissionError("request crosses an unavailable research epoch")
        return wire, begin, finish

    def merge(self, source_id: SourceId, candles: Any) -> NoReturn:
        raise ResearchV2AdmissionError("accepted Research V2 corpus is immutable")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        wire, begin, finish = self._request(source_id, symbol, timeframe, start, end)
        from adaptive_trading_bot.research import research_v2_corpus_continuation as c1

        shard_root = self._root if wire in tail.TAIL_SYMBOLS else self._repo / c1.ROOT
        shard = shard_root / f"combined/parquet/{wire}-{timeframe.value}.parquet"
        current_table = pq.read_table(pa.BufferReader(shard.read_bytes()))
        if not current_table.equals(
            self._reader._tables[(wire, timeframe.value)], check_metadata=True
        ):
            raise ResearchV2AdmissionError("loaded research candle table differs from locked bytes")
        # The accepted reader checks exact grid/coverage; it never calls a market API.
        try:
            table = self._reader.read(wire, timeframe.value, begin, finish)
        except corpus.CorpusError as error:
            raise ResearchV2AdmissionError("accepted research read failed integrity") from error
        result = tuple(
            Candle(
                symbol,
                timeframe,
                _instant(row["open_time_ms"]),
                _instant(row["close_time_ms"]),
                Decimal(row["open"]),
                Decimal(row["high"]),
                Decimal(row["low"]),
                Decimal(row["close"]),
                Decimal(row["base_volume"]),
            )
            for row in table.to_pylist()
            if row["symbol"] == wire and row["timeframe"] == timeframe.value
        )
        if len(result) != table.num_rows:
            raise ResearchV2AdmissionError("Parquet candle identity is contradictory")
        return result

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        candles = self.read(source_id, symbol, timeframe, start=start, end=end)
        return IntegrityReport(source_id, symbol, timeframe, start, end, len(candles), ())

    def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        facts = self._report_symbol(symbol)
        return tuple(
            (_instant(row["start_ms"]), _instant(row["end_ms"])) for row in facts["epochs"]
        )

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        facts = self._report_symbol(symbol)
        return tuple(
            (_instant(row["start_ms"]), _instant(row["end_ms"]))
            for row in facts["usable_intervals"]
        )


@dataclass(frozen=True, slots=True)
class ResearchV2EpochBounds:
    """Separate causal warm-up, decision and follow-up windows within one epoch."""

    symbol: TradingSymbol
    context_start: datetime
    decision_start: datetime
    decision_end: datetime
    follow_up_end: datetime
    source_id: SourceId = SOURCE_ID
    profile: str = PROFILE
    admission_version: str = ADMISSION_VERSION

    def validate(self, store: ResearchV2EpochStore) -> None:
        stamps = tuple(
            require_utc(value, field=name)
            for name, value in (
                ("context_start", self.context_start),
                ("decision_start", self.decision_start),
                ("decision_end", self.decision_end),
                ("follow_up_end", self.follow_up_end),
            )
        )
        if (
            store.source_id != self.source_id
            or self.profile != PROFILE
            or self.admission_version != ADMISSION_VERSION
            or not stamps[0] < stamps[1] < stamps[2] <= stamps[3]
            or any(align_timestamp(value, Timeframe.MINUTES_15) != value for value in stamps)
            or any(
                align_timestamp(value, Timeframe.HOURS_4) != value
                for value in (stamps[0], stamps[3])
            )
        ):
            raise ResearchV2AdmissionError("research bounds or profile are invalid")
        if not any(
            epoch_start <= stamps[0] and stamps[3] <= epoch_end
            for epoch_start, epoch_end in store.data_only_epochs(self.symbol)
        ):
            raise ResearchV2AdmissionError("research bounds cross an epoch or gap")
        if not any(
            stamps[0] <= usable_start - timedelta(hours=200)
            and usable_start <= stamps[1]
            and stamps[2] <= usable_end
            for usable_start, usable_end in store.usable_intervals(self.symbol)
        ):
            raise ResearchV2AdmissionError("decision window precedes warm-up or usable coverage")


@dataclass(frozen=True, slots=True)
class ResearchV2AdmissionConfiguration:
    """Versioned research-only fixed-equity/zero-exposure epoch configuration."""

    bounds: ResearchV2EpochBounds
    family: str
    research_equity_quote: Decimal
    input_binding: ResearchV2InputBinding = ACCEPTED_C2_BINDING
    risk_configuration: RiskManagerConfiguration = field(default_factory=RiskManagerConfiguration)
    safety_configuration: OperationalSafetyConfiguration = field(
        default_factory=OperationalSafetyConfiguration
    )
    safety_assumptions: ResearchSafetyAssumptionProfile = field(
        default_factory=ResearchSafetyAssumptionProfile
    )
    schema_version: str = field(init=False, default="research-v2-admission-configuration-v2")
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name in (
            "bounds",
            "input_binding",
            "risk_configuration",
            "safety_configuration",
            "safety_assumptions",
        ):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-v2-admission-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def _validate_facts(self) -> None:
        self.input_binding.validate()
        if (
            self.schema_version != "research-v2-admission-configuration-v2"
            or self.family not in {"trend_pullback", "breakout_retest", "range_mean_reversion"}
            or not isinstance(self.research_equity_quote, Decimal)
            or not self.research_equity_quote.is_finite()
            or self.research_equity_quote <= 0
            or self.bounds.source_id != self.input_binding.source_id
        ):
            raise ResearchV2AdmissionError("unknown research V2 admission configuration")
        _revalidate_risk_configuration(self.risk_configuration)
        _revalidate_safety_configuration(self.safety_configuration)
        _validate_safety_assumptions(self.safety_assumptions)

    def validate_seal(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-v2-admission-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise ResearchV2AdmissionError("research V2 configuration changed after acceptance")
        self._validate_facts()

    def validate(self, store: ResearchV2EpochStore) -> None:
        self.validate_seal()
        if store.source_id != self.input_binding.source_id:
            raise ResearchV2AdmissionError("research store differs from input binding")
        self.bounds.validate(store)


def synthetic_input_binding(
    store: ResearchV2EpochStore, bounds: ResearchV2EpochBounds
) -> ResearchV2InputBinding:
    """Bind the complete finite fixture used by both replay and execution."""

    if isinstance(store, LockedResearchV2CandleStore) or store.source_id != SYNTHETIC_SOURCE_ID:
        raise ResearchV2AdmissionError("only a distinct synthetic fixture may be evaluated")
    bounds.validate(store)
    rows: list[tuple[Timeframe, tuple[Candle, ...]]] = []
    for frame in FRAMES:
        candles = store.read(
            store.source_id,
            bounds.symbol,
            frame,
            start=bounds.context_start,
            end=bounds.follow_up_end,
        )
        expected = bounds.context_start
        for candle in candles:
            Candle(
                candle.symbol,
                candle.timeframe,
                candle.open_time,
                candle.close_time,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
            if (
                candle.symbol != bounds.symbol
                or candle.timeframe is not frame
                or candle.open_time != expected
            ):
                raise ResearchV2AdmissionError("synthetic input is incomplete or inconsistent")
            expected = candle.close_time
        if expected != bounds.follow_up_end:
            raise ResearchV2AdmissionError("synthetic input has incomplete coverage")
        rows.append((frame, candles))
    digest = _integrity_seal(
        "research-v2-synthetic-input-v1",
        (bounds.symbol, bounds.context_start, bounds.follow_up_end, tuple(rows)),
        excluded_names=frozenset(),
    )
    return ResearchV2InputBinding(
        "SYNTHETIC_FIXTURE", SYNTHETIC_SOURCE_ID, SYNTHETIC_DATASET_VERSION, digest
    )


@dataclass(frozen=True, slots=True)
class ResearchV2DecisionPopulation:
    """Detached decisions/contexts with source, normalized unit and epoch provenance."""

    configuration: ResearchV2AdmissionConfiguration
    decisions: tuple[DecisionJournalEntry[object, object], ...]
    contexts: tuple[HistoricalStrategyContextObservation, ...]
    source_id: SourceId = SOURCE_ID
    dataset_version: str = DATASET_VERSION
    manifest_sha256: str = MANIFEST_SHA256
    profile: str = PROFILE
    schema_version: str = "research-v2-decision-population-v2"
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-v2-decision-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-v2-decision-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise ResearchV2AdmissionError("research V2 decision population changed")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self._validate_population_facts("SYNTHETIC_FIXTURE", "research-v2-decision-population-v2")

    def _validate_population_facts(self, kind: str, schema: str) -> None:
        self.configuration.validate_seal()
        binding = self.configuration.input_binding
        bounds = self.configuration.bounds
        if binding.kind != kind:
            raise ResearchV2AdmissionError(
                "accepted C2 remains data-only in the synthetic population"
            )
        if (
            self.source_id != binding.source_id
            or self.dataset_version != binding.dataset_version
            or self.manifest_sha256 != binding.manifest_sha256
            or self.profile != PROFILE
            or self.schema_version != schema
        ):
            raise ResearchV2AdmissionError("research V2 decision provenance is contradictory")
        strategy = {
            "trend_pullback": TrendPullbackStrategy,
            "breakout_retest": BreakoutRetestStrategy,
            "range_mean_reversion": RangeMeanReversionStrategy,
        }[self.configuration.family]()
        risk = RiskManager(self.configuration.risk_configuration)
        safety = OperationalSafetyGate(self.configuration.safety_configuration)
        decision_index = 0
        expected_close = bounds.context_start + Timeframe.MINUTES_15.duration
        for observation in self.contexts:
            observation.validate()
            context = observation.context
            if not isinstance(context, ResearchV2StrategyContext):
                raise ResearchV2AdmissionError("research context lacks isolated authorization")
            if (
                context.source_id != binding.source_id
                or context.symbol != bounds.symbol
                or context.research_authorization.input_binding != binding
                or context.observed_at != expected_close
            ):
                raise ResearchV2AdmissionError("context is outside its research identity")
            expected_close += Timeframe.MINUTES_15.duration
            evaluation = strategy.evaluate(context)
            if not bounds.decision_start < context.observed_at <= bounds.decision_end:
                continue
            if decision_index >= len(self.decisions):
                raise ResearchV2AdmissionError("decision population omits an observation")
            decision = self.decisions[decision_index]
            decision.validate()
            equity = self.configuration.research_equity_quote
            portfolio = RiskPortfolioSnapshot(
                context.observed_at, "USDT", equity, equity, equity, ()
            )
            expected_risk = risk.evaluate(evaluation, portfolio)
            expected_safety = safety.evaluate(
                expected_risk,
                _research_health(context, self.configuration.safety_assumptions),
            )
            if (
                decision.context != context
                or decision.strategy_evaluation != evaluation
                or decision.risk_decision != expected_risk
                or decision.safety_decision != expected_safety
            ):
                raise ResearchV2AdmissionError("decision differs from declared strategy/admission")
            decision_index += 1
        if expected_close != bounds.follow_up_end + Timeframe.MINUTES_15.duration:
            raise ResearchV2AdmissionError("research context coverage is incomplete")
        if decision_index != len(self.decisions):
            raise ResearchV2AdmissionError("decision population contains extra observations")


@dataclass(frozen=True, slots=True)
class ResearchV2StudyDecisionPopulation(ResearchV2DecisionPopulation):
    """Separate accepted-input envelope; never produced by the synthetic seam."""

    protocol_sha256: str = ""
    schema_version: str = "research-v2-study-decision-population-v1"

    def _validate_facts(self) -> None:
        if self.schema_version != "research-v2-study-decision-population-v1":
            raise ResearchV2AdmissionError("unknown study population schema")
        if len(self.protocol_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.protocol_sha256
        ):
            raise ResearchV2AdmissionError("study protocol digest is malformed")
        self._validate_population_facts("ACCEPTED_C2", self.schema_version)


def _evaluate_epoch_rows(
    store: ResearchV2EpochStore,
    configuration: ResearchV2AdmissionConfiguration,
) -> tuple[
    tuple[DecisionJournalEntry[object, object], ...],
    tuple[HistoricalStrategyContextObservation, ...],
]:
    """Shared strategy/Risk/Safety/Journal engine; caller owns input authorization."""

    configuration.validate(store)
    bounds = configuration.bounds
    specs = tuple(
        ReplaySeries(
            bounds.source_id, bounds.symbol, frame, bounds.context_start, bounds.follow_up_end
        )
        for frame in FRAMES
    )
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        specs, clock=FrozenClock(bounds.context_start)
    )
    features = FeatureEngine()
    structures = MarketStructureEngine()
    regimes = TechnicalRegimeEngine()
    authority = ResearchV2ContextAuthorization(
        bounds.source_id,
        bounds.symbol,
        configuration.input_binding.dataset_version,
        configuration.input_binding.manifest_sha256,
        input_binding=configuration.input_binding,
    )
    composer = ResearchV2RegimeComposer(authority)
    strategy = {
        "trend_pullback": TrendPullbackStrategy,
        "breakout_retest": BreakoutRetestStrategy,
        "range_mean_reversion": RangeMeanReversionStrategy,
    }[configuration.family]()
    risk = RiskManager(configuration.risk_configuration)
    safety = OperationalSafetyGate(configuration.safety_configuration)
    journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
    decisions: list[DecisionJournalEntry[object, object]] = []
    observations: list[HistoricalStrategyContextObservation] = []
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        feature_batch = features.process(observed)
        structure_batch = structures.process(observed)
        regime_batch = regimes.process(feature_batch, structure_batch)
        mtf_batch = composer.process(regime_batch)
        contexts = assemble_strategy_contexts(
            observed,
            feature_batch,
            structure_batch,
            mtf_batch,
            research_authorization=authority,
        )
        entries: list[DecisionJournalEntry[object, object]] = []
        for context in contexts:
            evaluation = strategy.evaluate(context)
            observations.append(HistoricalStrategyContextObservation(context))
            if not bounds.decision_start < context.observed_at <= bounds.decision_end:
                continue
            equity = configuration.research_equity_quote
            portfolio = RiskPortfolioSnapshot(
                context.observed_at, "USDT", equity, equity, equity, ()
            )
            risk_decision = risk.evaluate(evaluation, portfolio)
            safety_decision = safety.evaluate(
                risk_decision, _research_health(context, configuration.safety_assumptions)
            )
            entry = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
            entry.validate()
            entries.append(entry)
            decisions.append(entry)
        if entries:
            journal.append_batch(DecisionJournalBatch(batch.observed_at, tuple(entries)))
    if tuple(decisions) != journal.snapshot().entries:
        raise ResearchV2AdmissionError("research journal population mismatch")
    return tuple(decisions), tuple(observations)


def _evaluate_synthetic_epoch(
    store: ResearchV2EpochStore,
    configuration: ResearchV2AdmissionConfiguration,
) -> ResearchV2DecisionPopulation:
    """Synthetic-only integration seam; it never accepts the locked C2 store."""

    if isinstance(store, LockedResearchV2CandleStore):
        raise ResearchV2AdmissionError("real corpus evaluation requires a later activation grant")
    configuration.validate(store)
    if configuration.input_binding != synthetic_input_binding(store, configuration.bounds):
        raise ResearchV2AdmissionError("synthetic fixture differs from frozen input binding")
    decisions, observations = _evaluate_epoch_rows(store, configuration)
    result = ResearchV2DecisionPopulation(
        configuration,
        decisions,
        observations,
        source_id=configuration.bounds.source_id,
        dataset_version=configuration.input_binding.dataset_version,
        manifest_sha256=configuration.input_binding.manifest_sha256,
    )
    result.validate()
    return result


def _validate_frozen_study_configuration(configuration: ResearchV2AdmissionConfiguration) -> None:
    """Reject a changed calendar, Risk/Safety assumption, or accepted-input claim."""

    from adaptive_trading_bot.research.research_v2_exploratory_screen import frozen_tasks

    configuration.validate_seal()
    expected = ResearchV2AdmissionConfiguration(
        configuration.bounds,
        configuration.family,
        Decimal("10000"),
        input_binding=ACCEPTED_C2_BINDING,
    )
    if configuration != expected or not any(
        task.family == configuration.family
        and task.context_start == configuration.bounds.context_start
        and task.decision_start == configuration.bounds.decision_start
        and task.decision_end == configuration.bounds.decision_end
        and task.last_follow_up_end == configuration.bounds.follow_up_end
        and TradingSymbol(task.wire_symbol.removesuffix("USDT"), "USDT")
        == configuration.bounds.symbol
        for task in frozen_tasks()
    ):
        raise ResearchV2AdmissionError("study configuration differs from frozen task and defaults")


def _evaluate_granted_study_epoch(
    store: LockedResearchV2CandleStore,
    configuration: ResearchV2AdmissionConfiguration,
    *,
    protocol_sha256: str,
    grant: object,
    actual_code_sha: str,
) -> ResearchV2StudyDecisionPopulation:
    """Only the guarded study runner may call this after its external grant check."""

    from adaptive_trading_bot.research.research_v2_exploratory_screen import StudyExecutionGrant

    if type(grant) is not StudyExecutionGrant:
        raise ResearchV2AdmissionError("accepted C2 evaluation requires a concrete external grant")
    grant.validate(actual_sha=actual_code_sha)
    if grant.protocol_sha256 != protocol_sha256:
        raise ResearchV2AdmissionError("study population protocol differs from external grant")
    if (
        type(store) is not LockedResearchV2CandleStore
        or configuration.input_binding != ACCEPTED_C2_BINDING
    ):
        raise ResearchV2AdmissionError("real study requires the exact locked C2 authority")
    _validate_frozen_study_configuration(configuration)
    decisions, observations = _evaluate_epoch_rows(store, configuration)
    result = ResearchV2StudyDecisionPopulation(
        configuration,
        decisions,
        observations,
        source_id=SOURCE_ID,
        dataset_version=DATASET_VERSION,
        manifest_sha256=MANIFEST_SHA256,
        protocol_sha256=protocol_sha256,
    )
    return result


@dataclass(frozen=True, slots=True)
class ResearchV2AllFillObservation:
    """Envelope retaining normalized source/epoch meaning around Package B output."""

    population: ResearchV2DecisionPopulation
    configuration: ResearchV2AdmissionConfiguration
    result: AllFillReplayResult
    costs: ExecutionCostConfiguration
    decision_identity: DecisionIdentity
    source_id: SourceId = SOURCE_ID
    dataset_version: str = DATASET_VERSION
    profile: str = PROFILE
    admission_version: str = ADMISSION_VERSION
    schema_version: str = "research-v2-all-fill-observation-v2"
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "research-v2-all-fill-observation",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "research-v2-all-fill-observation",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise ResearchV2AdmissionError("normalized all-fill provenance changed")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.population.validate()
        self.configuration.validate_seal()
        self.result.validate()
        binding = self.configuration.input_binding
        bounds = self.configuration.bounds
        expected_family = {
            "trend_pullback": AllFillFamily.TREND_PULLBACK,
            "breakout_retest": AllFillFamily.BREAKOUT_RETEST,
            "range_mean_reversion": AllFillFamily.RANGE_MEAN_REVERSION,
        }[self.configuration.family]
        expected_start = (
            bounds.context_start
            if self.configuration.family == "trend_pullback"
            else self.result.decision.identity.observed_at
        )
        if (
            self.population.configuration != self.configuration
            or self.source_id != binding.source_id
            or self.dataset_version != binding.dataset_version
            or self.profile != PROFILE
            or self.admission_version != ADMISSION_VERSION
            or self.schema_version != "research-v2-all-fill-observation-v2"
            or self.result.decision not in self.population.decisions
            or self.result.decision.identity != self.decision_identity
            or self.result.decision.identity.source_id != binding.source_id
            or self.result.decision.identity.symbol != bounds.symbol
            or self.result.family is not expected_family
            or self.result.series
            != ExecutionSeries(binding.source_id, bounds.symbol, Timeframe.MINUTES_15)
            or self.result.costs != self.costs
            or self.result.start != expected_start
            or self.result.end != bounds.follow_up_end
        ):
            raise ResearchV2AdmissionError("all-fill result is not bound to normalized research")
        if self.result.entry_intent is not None:
            source_start = entry_source_start(
                decision_at=self.result.decision.identity.observed_at,
                authorized_at=self.result.entry_intent.authorized_at,
                requested_start=expected_start,
                end=bounds.follow_up_end,
            )
            expected_snapshot = tuple(
                item.context.candle_15m
                for item in self.population.contexts
                if source_start <= item.context.candle_15m.open_time < bounds.follow_up_end
            )
            if self.result.execution_snapshot != expected_snapshot:
                raise ResearchV2AdmissionError("execution snapshot differs from bound input")


def _replay_synthetic_all_fill(
    population: ResearchV2DecisionPopulation,
    decision: DecisionJournalEntry[object, object],
    store: ResearchV2EpochStore,
    costs: ExecutionCostConfiguration,
) -> ResearchV2AllFillObservation:
    """Use unchanged entry-authorizer, family replay, simulator and accounting."""

    population.validate()
    if population.configuration.input_binding != synthetic_input_binding(
        store, population.configuration.bounds
    ):
        raise ResearchV2AdmissionError("execution source differs from frozen fixture")
    if decision not in population.decisions:
        raise ResearchV2AdmissionError("foreign decision")
    bounds = population.configuration.bounds
    contexts = tuple(
        item
        for item in population.contexts
        if decision.identity.observed_at < item.context.observed_at < bounds.follow_up_end
    )
    result = ResearchAllFillReplayRunner(HistoricalExecutionSource(store)).run(
        decision,
        contexts,
        ExecutionSeries(bounds.source_id, bounds.symbol, Timeframe.MINUTES_15),
        costs,
        start=(
            bounds.context_start
            if population.configuration.family == "trend_pullback"
            else decision.identity.observed_at
        ),
        end=bounds.follow_up_end,
    )
    wrapped = ResearchV2AllFillObservation(
        population,
        population.configuration,
        result,
        costs,
        decision.identity,
        source_id=bounds.source_id,
        dataset_version=population.dataset_version,
    )
    wrapped.validate()
    return wrapped


def data_only_preflight(repo: Path) -> tuple[dict[str, str], ...]:
    """Verify exact accepted input and report epoch/unit facts, never strategy outcomes."""

    store = LockedResearchV2CandleStore(repo)
    return tuple(
        {
            "symbol": member.wire_symbol,
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "profile": PROFILE,
            "current_contract_val_unqualified": str(member.current_contract_val_unqualified),
            "epochs": ";".join(
                f"{a.isoformat()}/{b.isoformat()}" for a, b in store.data_only_epochs(member.symbol)
            ),
            "usable": ";".join(
                f"{a.isoformat()}/{b.isoformat()}" for a, b in store.usable_intervals(member.symbol)
            ),
        }
        for member in store.members
    )
