"""Fixed 180-day two-arm Breakout comparison preparation.

This module is intentionally separate from the executed 63-day comparison.
The accepted history-screened corpus is data-only until an external grant binds
the exact executable revision, protocol, task plan, input and a fresh UUID.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from functools import partial
from hmac import compare_digest
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.backtest import (
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
    UnfilledOrder,
)
from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
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
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.regime.composition import CompositionSeriesError
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillBranch,
    AllFillFamily,
    AllFillReplayResult,
    AllFillStatus,
    ResearchAllFillReplayRunner,
    _snapshot_digest,
    validate_shared_all_fill_relations,
)
from adaptive_trading_bot.research.breakout_retest_replay import BreakoutRetestReplayResult
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryIntentProvenance,
    HistoricalStrategyContextObservation,
)
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
from adaptive_trading_bot.storage import HistoricalCandleStore, IntegrityReport, SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestStrategy,
    BreakoutRetestZoneInvalidationStrategy,
    assemble_strategy_contexts,
)
from adaptive_trading_bot.strategies.core import StrategyContextError, StrategyEvaluationContext

PROTOCOL_VERSION = "BREAKOUT_RETEST_HISTORY_SCREENED_180D_COMPARISON_V1"
PROFILE = "NORMALIZED_BASE_SIGNAL_V1"
ROOT = Path("data/research_corpus/history-screened-196d-last-continuation-v1")
DATASET_VERSION = "weex-history-screened-196d-last-continuation-v1"
SOURCE_ID = SourceId("weex-history-screened-196d-last-continuation-v1")
MANIFEST_SHA256 = "756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d"
REPORT_SHA256 = "bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66"
PLAN_SHA256 = "dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f"
PUBLICATION_MANIFEST_SHA256 = "199923afac0d51ca2e7a9b2f324dcecaf34b109d56e85694d06780136f8d3623"
PUBLICATION_MANIFEST = Path(
    "data/research_evidence/issue-216-history-screened-continuation-682fd077/"
    "publication-manifest.json"
)
SELECTION_MANIFEST_SHA256 = "9259da6973e5c910912c6da1b69942a3f7a0edbffd583e5b4cd9ea0395301bbb"
SELECTION_SUMMARY_SHA256 = "3dc81c33d3d12983973c69474e376561524b4dce1ef75723f1117851f88f1c81"
SELECTION_RECONCILIATION_SHA256 = "82faa7c6d78391ea43491ccf3f38b034aa4974b0864614b70ce00ad887ad6edf"
SELECTION_MANIFEST = Path(
    "data/research_evidence/issue-212-history-screened-shortlist-adbf3af/manifest.json"
)
SELECTION_SUMMARY = Path(
    "data/research_evidence/issue-212-history-screened-shortlist-adbf3af/summary.json"
)
SELECTION_RECONCILIATION = Path(
    "data/research_evidence/issue-212-overlap-reconciliation-v1/reconciliation.json"
)
CORRECTION_SIDECAR_SHA256 = "60326eb2794c6eb7546460bcbf12b1fce1cd55128ee7972806fd0cd47f23a1bf"
CORRECTION_ARCHIVE_SHA256 = "c2add2e3e7c3a2adfdba396bbca2854f2f5febe22eccb69ac15535837cc1ad13"
CORRECTED_TEST_SHA256 = "2d0e10a0aa986aef1258e3c85bd0893f32babe37cb4c0323f656bd3584dd3299"
CORRECTION_SIDECAR = Path("docs/research/issue_216_test_import_correction/correction.json")
CORRECTION_ARCHIVE = Path("docs/research/issue_216_test_import_correction/original_test.py.txt")
CORRECTED_TEST = Path("tests/research/test_history_screened_196d_continuation.py")
PANEL = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ZECUSDT",
    "ZAMAUSDT",
    "BCHUSDT",
    "AVAXUSDT",
    "DOGEUSDT",
    "LINKUSDT",
)
FRAMES = (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)
CONTEXT_START = datetime(2026, 4, 1, tzinfo=UTC)
SIGNAL_START = datetime(2026, 4, 10, tzinfo=UTC)
SIGNAL_END = datetime(2026, 10, 7, tzinfo=UTC)
FOLLOW_UP_END = datetime(2026, 10, 14, tzinfo=UTC)
USABLE_START = datetime(2026, 4, 9, 8, tzinfo=UTC)
FOLLOW_UP = timedelta(hours=168)
SLOTS_PER_MEMBER = 17_280
PANEL_SLOTS = 172_800
ARM_OBSERVATIONS = 345_600
STATUS_CELLS = 691_200
RESEARCH_EQUITY = Decimal("10000")
BASELINE_IDENTITY = ("breakout_retest", "1.0.0")
ZONE_IDENTITY = ("breakout_retest_zone_invalidation", "0.1.0")
SYNTHETIC_SOURCE_ID = SourceId("synthetic-breakout-history-screened-180d")


class HistoryScreenedComparisonError(ValueError):
    """The fixed protocol, input, evidence or execution state is contradictory."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    if not path.is_file():
        raise HistoryScreenedComparisonError(f"locked input is missing: {path}")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _exact(path: Path, expected: str) -> None:
    if not compare_digest(_sha_file(path), expected):
        raise HistoryScreenedComparisonError(f"locked input changed: {path.name}")


def _instant(milliseconds: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=milliseconds)


def _millis(value: datetime) -> int:
    value = require_utc(value, field="history-screened instant")
    epoch = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (epoch.days * 86_400 + epoch.seconds) * 1000 + epoch.microseconds // 1000


class ComparisonArm(Enum):
    BASELINE = "BASELINE"
    ZONE = "ZONE"


class ComparisonScenario(Enum):
    BASE = "BASE"
    STRESS = "STRESS"


def scenario_costs(scenario: ComparisonScenario) -> ExecutionCostConfiguration:
    if scenario is ComparisonScenario.BASE:
        return ExecutionCostConfiguration(Decimal("2"), Decimal("2"), Decimal("10"))
    if scenario is ComparisonScenario.STRESS:
        return ExecutionCostConfiguration(
            Decimal("4"), Decimal("4"), Decimal("20"), timedelta(seconds=1)
        )
    raise HistoryScreenedComparisonError("unknown comparison scenario")


@dataclass(frozen=True, slots=True)
class HistoryScreenedInputBinding:
    kind: str
    source_id: SourceId
    dataset_version: str
    manifest_sha256: str
    report_sha256: str
    plan_sha256: str
    schema_version: str = "history-screened-180d-input-binding-v1"

    def validate(self) -> None:
        accepted = (
            self.kind == "ACCEPTED_HISTORY_SCREENED_196D"
            and self.source_id == SOURCE_ID
            and self.dataset_version == DATASET_VERSION
            and self.manifest_sha256 == MANIFEST_SHA256
            and self.report_sha256 == REPORT_SHA256
            and self.plan_sha256 == PLAN_SHA256
        )
        synthetic = (
            self.kind == "SYNTHETIC_FIXTURE"
            and self.source_id == SYNTHETIC_SOURCE_ID
            and self.dataset_version.startswith("synthetic-")
            and all(
                len(value) == 64 and set(value) <= set("0123456789abcdef")
                for value in (
                    self.manifest_sha256,
                    self.report_sha256,
                    self.plan_sha256,
                )
            )
            and self.manifest_sha256 != MANIFEST_SHA256
        )
        if self.schema_version != "history-screened-180d-input-binding-v1" or not (
            accepted or synthetic
        ):
            raise HistoryScreenedComparisonError("unknown comparison input binding")


ACCEPTED_BINDING = HistoryScreenedInputBinding(
    "ACCEPTED_HISTORY_SCREENED_196D",
    SOURCE_ID,
    DATASET_VERSION,
    MANIFEST_SHA256,
    REPORT_SHA256,
    PLAN_SHA256,
)


@dataclass(frozen=True, slots=True)
class HistoryScreenedContextAuthorization:
    source_id: SourceId
    symbol: TradingSymbol
    input_binding: HistoryScreenedInputBinding
    profile: str = PROFILE
    protocol_version: str = PROTOCOL_VERSION

    def validate(self) -> None:
        self.input_binding.validate()
        if (
            self.source_id != self.input_binding.source_id
            or f"{self.symbol.base}{self.symbol.quote}" not in PANEL
            or self.symbol.quote != "USDT"
            or self.profile != PROFILE
            or self.protocol_version != PROTOCOL_VERSION
        ):
            raise HistoryScreenedComparisonError("context authorization is outside the protocol")


@dataclass(frozen=True, slots=True)
class HistoryScreenedStrategyContext(StrategyEvaluationContext):
    research_authorization: HistoryScreenedContextAuthorization

    def validate(self) -> None:
        self.research_authorization.validate()
        if (
            self.source_id != self.research_authorization.source_id
            or self.symbol != self.research_authorization.symbol
        ):
            raise StrategyContextError("history-screened context authorization mismatch")
        self._validate_causal_facts()


class HistoryScreenedRegimeComposer(MultiTimeframeRegimeComposer):
    def __init__(self, authority: HistoryScreenedContextAuthorization) -> None:
        authority.validate()
        super().__init__()
        self._authority = authority

    def _validate_symbol(self, snapshot: Any) -> None:
        if (
            snapshot.source_id != self._authority.source_id
            or snapshot.symbol != self._authority.symbol
        ):
            raise CompositionSeriesError("regime series outside history-screened authority")


class HistoryScreenedEpochStore(HistoricalCandleStore, Protocol):
    @property
    def source_id(self) -> SourceId: ...

    @property
    def input_binding(self) -> HistoryScreenedInputBinding: ...

    def data_only_epochs(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]: ...

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]: ...


class LockedHistoryScreenedCandleStore:
    """Read-only adapter over the exact accepted continuation corpus."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo.resolve(strict=True)
        self._root = self._repo / ROOT
        self._manifest = self._read_locked("manifest.json", MANIFEST_SHA256)
        self._report = self._read_locked("report.json", REPORT_SHA256)
        _exact(self._root / "plan.json", PLAN_SHA256)
        _exact(self._repo / PUBLICATION_MANIFEST, PUBLICATION_MANIFEST_SHA256)
        _exact(self._repo / SELECTION_MANIFEST, SELECTION_MANIFEST_SHA256)
        _exact(self._repo / SELECTION_SUMMARY, SELECTION_SUMMARY_SHA256)
        _exact(self._repo / SELECTION_RECONCILIATION, SELECTION_RECONCILIATION_SHA256)
        _exact(self._repo / CORRECTION_SIDECAR, CORRECTION_SIDECAR_SHA256)
        _exact(self._repo / CORRECTION_ARCHIVE, CORRECTION_ARCHIVE_SHA256)
        _exact(self._repo / CORRECTED_TEST, CORRECTED_TEST_SHA256)
        self._validate_authorities()

    @property
    def source_id(self) -> SourceId:
        return SOURCE_ID

    @property
    def input_binding(self) -> HistoryScreenedInputBinding:
        return ACCEPTED_BINDING

    def _read_locked(self, name: str, digest: str) -> dict[str, Any]:
        path = self._root / name
        _exact(path, digest)
        try:
            value = json.loads(path.read_bytes())
        except (UnicodeError, ValueError) as exc:
            raise HistoryScreenedComparisonError(f"locked {name} is malformed") from exc
        if not isinstance(value, dict):
            raise HistoryScreenedComparisonError(f"locked {name} is not an object")
        return cast(dict[str, Any], value)

    def _validate_authorities(self) -> None:
        if (
            self._manifest.get("status") != "QUALIFIED"
            or self._manifest.get("capture_status") != "CAPTURE_COMPLETE"
            or self._manifest.get("dataset_version") != DATASET_VERSION
            or self._manifest.get("plan_sha256") != PLAN_SHA256
            or self._report.get("status") != "QUALIFIED"
            or self._report.get("dataset_version") != DATASET_VERSION
            or tuple(self._report.get("panel", ())) != PANEL
            or self._report.get("start") != "2026-04-01T00:00:00Z"
            or self._report.get("end") != "2026-10-14T00:00:00Z"
            or self._report.get("signal_window")
            != {"start": "2026-04-10T00:00:00Z", "end": "2026-10-07T00:00:00Z"}
            or self._report.get("signal_and_168h_followup_supported") is not True
        ):
            raise HistoryScreenedComparisonError("accepted corpus authority is contradictory")
        files = self._manifest.get("files")
        if not isinstance(files, dict) or files.get("report.json") != REPORT_SHA256:
            raise HistoryScreenedComparisonError("accepted corpus file index is contradictory")
        for relative, digest in files.items():
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise HistoryScreenedComparisonError("accepted corpus file index is malformed")
            _exact(self._root / relative, digest)
        for wire in PANEL:
            facts = self._symbol_facts(wire)
            expected_epoch = [{"start_ms": 1775001600000, "end_ms": 1791936000000, "rows": 56448}]
            expected_usable = [
                {
                    "start_ms": 1775721600000,
                    "end_ms": 1791936000000,
                    "complete_4h_warmup": 50,
                    "boundary_observation_excluded": True,
                }
            ]
            if (
                facts.get("status") != "QUALIFIED"
                or facts.get("epochs") != expected_epoch
                or facts.get("usable_intervals") != expected_usable
                or facts.get("valid_5m_rows") != 56_448
                or facts.get("missing_5m_rows") != 0
                or facts.get("derived_rows") != {"15m": 18_816, "1h": 4_704, "4h": 1_176}
            ):
                raise HistoryScreenedComparisonError(f"{wire} accepted coverage changed")

    def _symbol_facts(self, wire: str) -> dict[str, Any]:
        symbols = self._report.get("symbols")
        if (
            not isinstance(symbols, dict)
            or wire not in symbols
            or not isinstance(symbols[wire], dict)
        ):
            raise HistoryScreenedComparisonError("accepted member report is missing")
        return cast(dict[str, Any], symbols[wire])

    def _wire(self, symbol: TradingSymbol) -> str:
        wire = f"{symbol.base}{symbol.quote}"
        if wire not in PANEL or symbol.quote != "USDT":
            raise HistoryScreenedComparisonError("foreign comparison symbol")
        return wire

    def merge(self, source_id: SourceId, candles: Any) -> NoReturn:
        raise HistoryScreenedComparisonError("accepted comparison corpus is immutable")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        wire = self._wire(symbol)
        if source_id != SOURCE_ID or timeframe not in FRAMES or start is None or end is None:
            raise HistoryScreenedComparisonError("comparison read requires an exact finite series")
        start = require_utc(start, field="comparison read start")
        end = require_utc(end, field="comparison read end")
        if not CONTEXT_START <= start < end <= FOLLOW_UP_END:
            raise HistoryScreenedComparisonError("comparison read is outside the accepted epoch")
        path = self._root / f"parquet/{wire}-{timeframe.value}.parquet"
        indexed = self._manifest["files"].get(f"parquet/{wire}-{timeframe.value}.parquet")
        if not isinstance(indexed, str):
            raise HistoryScreenedComparisonError("comparison parquet is absent from manifest")
        _exact(path, indexed)
        table = pq.read_table(pa.BufferReader(path.read_bytes()))
        begin, finish = _millis(start), _millis(end)
        rows = [row for row in table.to_pylist() if begin <= row["open_time_ms"] < finish]
        expected = begin
        step = int(timeframe.duration.total_seconds() * 1000)
        candles: list[Candle] = []
        for row in rows:
            if (
                row["symbol"] != wire
                or row["timeframe"] != timeframe.value
                or row["open_time_ms"] != expected
                or row["close_time_ms"] != expected + step
            ):
                raise HistoryScreenedComparisonError("comparison parquet grid is contradictory")
            candles.append(
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
            )
            expected += step
        if expected != finish:
            raise HistoryScreenedComparisonError("comparison parquet has incomplete coverage")
        return tuple(candles)

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
        facts = self._symbol_facts(self._wire(symbol))
        return tuple(
            (_instant(row["start_ms"]), _instant(row["end_ms"])) for row in facts["epochs"]
        )

    def usable_intervals(self, symbol: TradingSymbol) -> tuple[tuple[datetime, datetime], ...]:
        facts = self._symbol_facts(self._wire(symbol))
        return tuple(
            (_instant(row["start_ms"]), _instant(row["end_ms"]))
            for row in facts["usable_intervals"]
        )


@dataclass(frozen=True, slots=True)
class HistoryScreenedTask:
    index: int
    wire_symbol: str
    context_start: datetime = CONTEXT_START
    signal_start: datetime = SIGNAL_START
    signal_end: datetime = SIGNAL_END
    follow_up_end: datetime = FOLLOW_UP_END
    arms: tuple[str, ...] = ("breakout_retest:1.0.0", "breakout_retest_zone_invalidation:0.1.0")
    scenarios: tuple[str, ...] = ("BASE_2_2_10_ZERO_LATENCY", "STRESS_4_4_20_ONE_SECOND")

    @property
    def task_id(self) -> str:
        return f"{self.index:02d}-{self.wire_symbol}-BOTH_ARMS-BOTH_SCENARIOS"

    @property
    def symbol(self) -> TradingSymbol:
        return TradingSymbol(self.wire_symbol.removesuffix("USDT"), "USDT")

    def validate(self, *, synthetic: bool = False) -> None:
        fixed = (
            not 0 <= self.index < len(PANEL)
            or PANEL[self.index] != self.wire_symbol
            or self.context_start != CONTEXT_START
            or self.signal_start != SIGNAL_START
            or self.signal_end != SIGNAL_END
            or self.follow_up_end != FOLLOW_UP_END
            or self.arms != ("breakout_retest:1.0.0", "breakout_retest_zone_invalidation:0.1.0")
            or self.scenarios != ("BASE_2_2_10_ZERO_LATENCY", "STRESS_4_4_20_ONE_SECOND")
        )
        synthetic_valid = (
            synthetic
            and self.index == 0
            and self.wire_symbol == "BTCUSDT"
            and self.context_start < self.signal_start < self.signal_end <= self.follow_up_end
            and self.arms == ("breakout_retest:1.0.0", "breakout_retest_zone_invalidation:0.1.0")
            and self.scenarios == ("BASE_2_2_10_ZERO_LATENCY", "STRESS_4_4_20_ONE_SECOND")
        )
        if fixed and not synthetic_valid:
            raise HistoryScreenedComparisonError("comparison task differs from frozen plan")


TASKS = tuple(HistoryScreenedTask(index, wire) for index, wire in enumerate(PANEL))


def protocol_record() -> dict[str, object]:
    return {
        "protocol": PROTOCOL_VERSION,
        "input": {
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "report_sha256": REPORT_SHA256,
            "plan_sha256": PLAN_SHA256,
            "publication_manifest_sha256": PUBLICATION_MANIFEST_SHA256,
            "selection_manifest_sha256": SELECTION_MANIFEST_SHA256,
            "selection_summary_sha256": SELECTION_SUMMARY_SHA256,
            "selection_reconciliation_sha256": SELECTION_RECONCILIATION_SHA256,
            "test_correction_sidecar_sha256": CORRECTION_SIDECAR_SHA256,
            "corrected_test_sha256": CORRECTED_TEST_SHA256,
        },
        "calendar": {
            "context_start": CONTEXT_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "signal_end": SIGNAL_END.isoformat(),
            "follow_up_end": FOLLOW_UP_END.isoformat(),
            "per_decision_follow_up_hours": 168,
            "blocks_days": [60, 60, 60],
            "weeks": 26,
        },
        "population": {
            "members": list(PANEL),
            "slots_per_member": SLOTS_PER_MEMBER,
            "panel_slots": PANEL_SLOTS,
            "arm_observations": ARM_OBSERVATIONS,
            "arm_scenario_status_cells": STATUS_CELLS,
        },
        "arms": [list(BASELINE_IDENTITY), list(ZONE_IDENTITY)],
        "equity_usdt": "10000",
        "scenarios": {
            "BASE": {"spread_bps": "2", "slippage_bps": "2", "fee_bps": "10", "latency_ms": 0},
            "STRESS": {"spread_bps": "4", "slippage_bps": "4", "fee_bps": "20", "latency_ms": 1000},
        },
        "mask_hours": 168,
        "screen": "EXPLORATORY_FIXED_GATES_V1",
        "trading_authority": False,
    }


PROTOCOL_SHA256 = _sha_bytes(_canonical(protocol_record()))
TASK_PLAN_SHA256 = _sha_bytes(
    _canonical(
        [
            {
                "task_id": task.task_id,
                "wire_symbol": task.wire_symbol,
                "context_start": task.context_start.isoformat(),
                "signal_start": task.signal_start.isoformat(),
                "signal_end": task.signal_end.isoformat(),
                "follow_up_end": task.follow_up_end.isoformat(),
                "arms": list(task.arms),
                "scenarios": list(task.scenarios),
            }
            for task in TASKS
        ]
    )
)


@dataclass(frozen=True, slots=True)
class HistoryScreenedConfiguration:
    task: HistoryScreenedTask
    input_binding: HistoryScreenedInputBinding
    research_equity_quote: Decimal = RESEARCH_EQUITY
    risk_configuration: RiskManagerConfiguration = field(default_factory=RiskManagerConfiguration)
    safety_configuration: OperationalSafetyConfiguration = field(
        default_factory=OperationalSafetyConfiguration
    )
    safety_assumptions: ResearchSafetyAssumptionProfile = field(
        default_factory=ResearchSafetyAssumptionProfile
    )
    schema_version: str = field(init=False, default="history-screened-180d-configuration-v1")
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name in (
            "task",
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
                "history-screened-180d-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def _validate_facts(self) -> None:
        self.input_binding.validate()
        self.task.validate(synthetic=self.input_binding.kind == "SYNTHETIC_FIXTURE")
        if (
            self.schema_version != "history-screened-180d-configuration-v1"
            or self.research_equity_quote != RESEARCH_EQUITY
        ):
            raise HistoryScreenedComparisonError("comparison configuration changed")
        _revalidate_risk_configuration(self.risk_configuration)
        _revalidate_safety_configuration(self.safety_configuration)
        _validate_safety_assumptions(self.safety_assumptions)

    def validate(self, store: HistoryScreenedEpochStore) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "history-screened-180d-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise HistoryScreenedComparisonError("comparison configuration seal changed")
        self._validate_facts()
        if (
            store.source_id != self.input_binding.source_id
            or store.input_binding != self.input_binding
        ):
            raise HistoryScreenedComparisonError("comparison store differs from input binding")
        if (self.task.context_start, self.task.follow_up_end) not in store.data_only_epochs(
            self.task.symbol
        ):
            raise HistoryScreenedComparisonError("comparison task crosses an epoch")
        expected_usable = (
            USABLE_START if self.input_binding == ACCEPTED_BINDING else self.task.signal_start
        )
        if (expected_usable, self.task.follow_up_end) not in store.usable_intervals(
            self.task.symbol
        ):
            raise HistoryScreenedComparisonError("comparison usable interval changed")


@dataclass(frozen=True, slots=True)
class HistoryScreenedDecisionPopulation:
    configuration: HistoryScreenedConfiguration
    contexts: tuple[HistoricalStrategyContextObservation, ...]
    baseline_decisions: tuple[DecisionJournalEntry[object, object], ...]
    zone_decisions: tuple[DecisionJournalEntry[object, object], ...]
    schema_version: str = field(init=False, default="history-screened-180d-population-v1")
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "history-screened-180d-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def _validate_facts(self) -> None:
        self.configuration._validate_facts()
        if self.schema_version != "history-screened-180d-population-v1":
            raise HistoryScreenedComparisonError("unknown comparison population schema")
        for observation in self.contexts:
            observation.validate()
            if not isinstance(observation.context, HistoryScreenedStrategyContext):
                raise HistoryScreenedComparisonError("population context lacks new binding")
        expected_slots = sum(
            self.configuration.task.signal_start
            < item.context.observed_at
            <= self.configuration.task.signal_end
            for item in self.contexts
        )
        if (
            len(self.baseline_decisions) != expected_slots
            or len(self.zone_decisions) != expected_slots
        ):
            raise HistoryScreenedComparisonError("both arms must retain every decision slot")
        for arm, expected in (
            (self.baseline_decisions, BASELINE_IDENTITY),
            (self.zone_decisions, ZONE_IDENTITY),
        ):
            for decision in arm:
                decision.validate()
                if (
                    (decision.identity.strategy_id, decision.identity.strategy_version) != expected
                    or decision.identity.source_id != self.configuration.input_binding.source_id
                    or decision.identity.symbol != self.configuration.task.symbol
                ):
                    raise HistoryScreenedComparisonError("arm decision identity changed")

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "history-screened-180d-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise HistoryScreenedComparisonError("comparison population seal changed")
        self._validate_facts()


@dataclass(frozen=True, slots=True)
class HistoryScreenedReplayResult:
    """All-fill authority bound to one new task/arm/scenario/cutoff."""

    decision: DecisionJournalEntry[object, object]
    family: AllFillFamily
    series: ExecutionSeries
    costs: ExecutionCostConfiguration
    scenario: ComparisonScenario
    start: datetime
    end: datetime
    status: AllFillStatus
    entry_intent: EntryIntentProvenance | None
    entry_unfilled: UnfilledOrder | None
    branches: tuple[AllFillBranch, ...]
    execution_snapshot: tuple[Candle, ...]
    execution_snapshot_digest: str
    legacy_replay: BreakoutRetestReplayResult | None
    arm: ComparisonArm
    task_id: str
    population_sha256: str
    schema_version: str = field(init=False, default="history-screened-180d-result-v1")
    protocol_version: str = field(init=False, default=PROTOCOL_VERSION)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "history-screened-180d-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def _validate_facts(self) -> None:
        self.decision.validate()
        expected_identity = (
            BASELINE_IDENTITY if self.arm is ComparisonArm.BASELINE else ZONE_IDENTITY
        )
        if (
            self.schema_version != "history-screened-180d-result-v1"
            or self.protocol_version != PROTOCOL_VERSION
            or self.family is not AllFillFamily.BREAKOUT_RETEST
            or (self.decision.identity.strategy_id, self.decision.identity.strategy_version)
            != expected_identity
            or self.start != self.decision.identity.observed_at
            or self.end != self.start + FOLLOW_UP
            or self.series
            != ExecutionSeries(
                cast(SourceId, self.decision.identity.source_id),
                self.decision.identity.symbol,
                Timeframe.MINUTES_15,
            )
            or self.costs != scenario_costs(self.scenario)
            or not self.task_id
            or len(self.population_sha256) != 64
            or set(self.population_sha256) - set("0123456789abcdef")
            or not compare_digest(
                self.execution_snapshot_digest,
                _snapshot_digest(self.execution_snapshot),
            )
        ):
            raise HistoryScreenedComparisonError("comparison replay identity is contradictory")
        validate_shared_all_fill_relations(
            decision=self.decision,
            family=self.family,
            series=self.series,
            costs=self.costs,
            start=self.start,
            end=self.end,
            status=self.status,
            entry_intent=self.entry_intent,
            entry_unfilled=self.entry_unfilled,
            branches=self.branches,
            execution_snapshot=self.execution_snapshot,
            legacy_replay=self.legacy_replay,
            error_type=HistoryScreenedComparisonError,
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "history-screened-180d-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise HistoryScreenedComparisonError("comparison replay seal changed")
        self._validate_facts()


def _wrap_baseline_result(
    result: AllFillReplayResult,
    *,
    task_id: str,
    population_sha256: str,
) -> HistoryScreenedReplayResult:
    result.validate()
    return HistoryScreenedReplayResult(
        result.decision,
        result.family,
        result.series,
        result.costs,
        ComparisonScenario.BASE
        if result.costs == scenario_costs(ComparisonScenario.BASE)
        else ComparisonScenario.STRESS,
        result.start,
        result.end,
        result.status,
        result.entry_intent,
        result.entry_unfilled,
        result.branches,
        result.execution_snapshot,
        result.execution_snapshot_digest,
        cast(BreakoutRetestReplayResult | None, result.legacy_replay),
        ComparisonArm.BASELINE,
        task_id,
        population_sha256,
    )


class HistoryScreenedAllFillRunner:
    """Run both existing Breakout policies through the shared all-fill authority."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._runner = ResearchAllFillReplayRunner(HistoricalExecutionSource(store))

    def run(
        self,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        scenario: ComparisonScenario,
        *,
        arm: ComparisonArm,
        task_id: str,
        population_sha256: str,
    ) -> HistoryScreenedReplayResult:
        costs = scenario_costs(scenario)
        start = decision.identity.observed_at
        end = start + FOLLOW_UP
        future = tuple(item for item in contexts if start < item.context.observed_at < end)
        if arm is ComparisonArm.BASELINE:
            return _wrap_baseline_result(
                self._runner.run(
                    decision,
                    future,
                    series,
                    costs,
                    start=start,
                    end=end,
                ),
                task_id=task_id,
                population_sha256=population_sha256,
            )
        result = self._runner._run_breakout_zone_comparison(
            decision,
            future,
            series,
            costs,
            start=start,
            end=end,
            result_label=scenario,
            result_factory=partial(
                HistoryScreenedReplayResult,
                arm=ComparisonArm.ZONE,
                task_id=task_id,
                population_sha256=population_sha256,
            ),
        )
        if not isinstance(result, HistoryScreenedReplayResult):
            raise HistoryScreenedComparisonError("zone replay returned foreign evidence")
        result.validate()
        return result


def _evaluate_population(
    store: HistoryScreenedEpochStore,
    configuration: HistoryScreenedConfiguration,
) -> HistoryScreenedDecisionPopulation:
    configuration.validate(store)
    task = configuration.task
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        tuple(
            ReplaySeries(
                store.source_id, task.symbol, frame, task.context_start, task.follow_up_end
            )
            for frame in FRAMES
        ),
        clock=FrozenClock(task.context_start),
    )
    authority = HistoryScreenedContextAuthorization(
        store.source_id, task.symbol, configuration.input_binding
    )
    features = FeatureEngine()
    structures = MarketStructureEngine()
    regimes = TechnicalRegimeEngine()
    composer = HistoryScreenedRegimeComposer(authority)
    baseline = BreakoutRetestStrategy()
    zone = BreakoutRetestZoneInvalidationStrategy()
    risk = RiskManager(configuration.risk_configuration)
    safety = OperationalSafetyGate(configuration.safety_configuration)
    contexts: list[HistoricalStrategyContextObservation] = []
    baseline_decisions: list[DecisionJournalEntry[object, object]] = []
    zone_decisions: list[DecisionJournalEntry[object, object]] = []
    baseline_journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
    zone_journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        feature_batch = features.process(observed)
        structure_batch = structures.process(observed)
        regime_batch = regimes.process(feature_batch, structure_batch)
        mtf_batch = composer.process(regime_batch)
        assembled = assemble_strategy_contexts(
            observed,
            feature_batch,
            structure_batch,
            mtf_batch,
            history_screened_authorization=authority,
        )
        for context in assembled:
            contexts.append(HistoricalStrategyContextObservation(context))
            evaluations = (baseline.evaluate(context), zone.evaluate(context))
            if not task.signal_start < context.observed_at <= task.signal_end:
                continue
            equity = configuration.research_equity_quote
            portfolio = RiskPortfolioSnapshot(
                context.observed_at, "USDT", equity, equity, equity, ()
            )
            pair: list[DecisionJournalEntry[object, object]] = []
            for evaluation in evaluations:
                risk_decision = risk.evaluate(evaluation, portfolio)
                safety_decision = safety.evaluate(
                    risk_decision,
                    _research_health(context, configuration.safety_assumptions),
                )
                decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                decision.validate()
                pair.append(decision)
            baseline_journal.append_batch(DecisionJournalBatch(context.observed_at, (pair[0],)))
            zone_journal.append_batch(DecisionJournalBatch(context.observed_at, (pair[1],)))
            baseline_decisions.append(pair[0])
            zone_decisions.append(pair[1])
    if (
        tuple(baseline_decisions) != baseline_journal.snapshot().entries
        or tuple(zone_decisions) != zone_journal.snapshot().entries
    ):
        raise HistoryScreenedComparisonError("arm journal differs from evaluated decisions")
    result = HistoryScreenedDecisionPopulation(
        configuration,
        tuple(contexts),
        tuple(baseline_decisions),
        tuple(zone_decisions),
    )
    result.validate()
    return result


def synthetic_binding(
    store: HistoryScreenedEpochStore, task: HistoryScreenedTask
) -> HistoryScreenedInputBinding:
    if store.source_id != SYNTHETIC_SOURCE_ID:
        raise HistoryScreenedComparisonError("synthetic seam rejects accepted history")
    rows: list[object] = []
    for frame in FRAMES:
        candles = store.read(
            store.source_id, task.symbol, frame, start=task.context_start, end=task.follow_up_end
        )
        rows.append((frame.value, candles))
    digest = _integrity_seal(
        "history-screened-180d-synthetic-input", tuple(rows), excluded_names=frozenset()
    )
    return HistoryScreenedInputBinding(
        "SYNTHETIC_FIXTURE",
        SYNTHETIC_SOURCE_ID,
        "synthetic-history-screened-180d-v1",
        digest,
        digest,
        digest,
    )


def evaluate_synthetic_task(
    store: HistoryScreenedEpochStore,
    configuration: HistoryScreenedConfiguration,
) -> HistoryScreenedDecisionPopulation:
    if isinstance(store, LockedHistoryScreenedCandleStore):
        raise HistoryScreenedComparisonError("accepted history requires a reviewed execution grant")
    expected = synthetic_binding(store, configuration.task)
    if configuration.input_binding != expected:
        raise HistoryScreenedComparisonError("synthetic input binding differs from fixture")
    return _evaluate_population(store, configuration)


@dataclass(frozen=True, slots=True)
class HistoryScreenedExecutionGrant:
    kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    task_plan_sha256: str
    source_id: str
    dataset_version: str
    manifest_sha256: str
    schema_version: str = "history-screened-180d-execution-grant-v1"

    @classmethod
    def from_file(cls, path: Path) -> HistoryScreenedExecutionGrant:
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise HistoryScreenedComparisonError("execution grant is unreadable") from exc
        if not isinstance(value, dict) or _canonical(value) != raw:
            raise HistoryScreenedComparisonError("execution grant is not canonical JSON")
        try:
            return cls(**value)
        except TypeError as exc:
            raise HistoryScreenedComparisonError("execution grant fields differ") from exc

    def validate(self, *, actual_sha: str) -> None:
        if (
            self.schema_version != "history-screened-180d-execution-grant-v1"
            or self.kind != "EXTERNAL_REVIEWED_EXECUTION_GRANT"
            or self.executable_sha != actual_sha
            or len(actual_sha) != 40
            or self.protocol_sha256 != PROTOCOL_SHA256
            or self.task_plan_sha256 != TASK_PLAN_SHA256
            or self.source_id != SOURCE_ID.value
            or self.dataset_version != DATASET_VERSION
            or self.manifest_sha256 != MANIFEST_SHA256
            or not self.run_uuid
        ):
            raise HistoryScreenedComparisonError("real execution grant does not bind this study")


def evaluate_granted_task(
    store: LockedHistoryScreenedCandleStore,
    configuration: HistoryScreenedConfiguration,
    grant: HistoryScreenedExecutionGrant,
    *,
    actual_sha: str,
) -> HistoryScreenedDecisionPopulation:
    """Guard check deliberately precedes all real feature/strategy construction."""

    grant.validate(actual_sha=actual_sha)
    if (
        type(store) is not LockedHistoryScreenedCandleStore
        or configuration.input_binding != ACCEPTED_BINDING
    ):
        raise HistoryScreenedComparisonError("real evaluation requires the exact locked input")
    return _evaluate_population(store, configuration)


def report_block(signal_open: datetime) -> str:
    signal_open = require_utc(signal_open, field="signal open")
    if not SIGNAL_START <= signal_open < SIGNAL_END:
        raise HistoryScreenedComparisonError("signal open is outside reporting calendar")
    if signal_open < datetime(2026, 6, 9, tzinfo=UTC):
        return "P1"
    if signal_open < datetime(2026, 8, 8, tzinfo=UTC):
        return "P2"
    return "P3"


def report_week(signal_open: datetime) -> str:
    signal_open = require_utc(signal_open, field="signal open")
    if not SIGNAL_START <= signal_open < SIGNAL_END:
        raise HistoryScreenedComparisonError("signal open is outside reporting calendar")
    return f"W{((signal_open - SIGNAL_START).days // 7) + 1}"


def spacing_mask(
    decisions: tuple[DecisionJournalEntry[object, object], ...],
) -> tuple[DecisionIdentity, ...]:
    """Freeze earliest authorized admission with a shared 168-hour exclusion."""

    from adaptive_trading_bot.orders import (
        EntryOrderIntentAuthorizer,
        OrderIntentAuthorizationStatus,
    )

    selected: list[DecisionIdentity] = []
    last: datetime | None = None
    for decision in decisions:
        decision.validate()
        status = EntryOrderIntentAuthorizer().authorize(decision).status
        if status is not OrderIntentAuthorizationStatus.AUTHORIZED:
            continue
        if last is None or decision.identity.observed_at >= last + FOLLOW_UP:
            selected.append(decision.identity)
            last = decision.identity.observed_at
    return tuple(selected)


def preflight(repo: Path) -> dict[str, object]:
    """Inert accepted-data check: no feature/strategy/Risk/Safety construction."""

    store = LockedHistoryScreenedCandleStore(repo)
    inventory: list[dict[str, object]] = []
    for task in TASKS:
        task.validate()
        frames: dict[str, int] = {}
        for frame, expected in (
            (Timeframe.MINUTES_15, 18_816),
            (Timeframe.HOUR_1, 4_704),
            (Timeframe.HOURS_4, 1_176),
        ):
            path = store._root / f"parquet/{task.wire_symbol}-{frame.value}.parquet"
            metadata = pq.read_metadata(path)
            if metadata.num_rows != expected:
                raise HistoryScreenedComparisonError("preflight row count changed")
            frames[frame.value] = metadata.num_rows
        inventory.append(
            {
                "task_id": task.task_id,
                "wire_symbol": task.wire_symbol,
                "frames": frames,
                "decision_slots": SLOTS_PER_MEMBER,
                "arms": 2,
                "scenarios": 2,
                "strategy_evaluated": False,
            }
        )
    return {
        "status": "DATA_ONLY_PREFLIGHT_OK",
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "input_binding": {
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "report_sha256": REPORT_SHA256,
            "plan_sha256": PLAN_SHA256,
        },
        "calendar": {
            "context_start": CONTEXT_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "signal_end": SIGNAL_END.isoformat(),
            "follow_up_end": FOLLOW_UP_END.isoformat(),
        },
        "counts": {
            "tasks": 10,
            "slots_per_member": SLOTS_PER_MEMBER,
            "panel_slots": PANEL_SLOTS,
            "arm_observations": ARM_OBSERVATIONS,
            "status_cells": STATUS_CELLS,
        },
        "tasks": inventory,
    }
