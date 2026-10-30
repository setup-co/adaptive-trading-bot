"""Bounded-memory, read-only recovery for the stopped Issue #220 evidence.

This module validates and reduces retained authorities.  It never evaluates a
strategy, simulates an order, or mutates the source ledger.
"""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import json
import os
import shutil
import stat
import sys
import tracemalloc
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path
from statistics import median
from typing import Any, BinaryIO, cast

from adaptive_trading_bot.domain import Timeframe
from adaptive_trading_bot.journal import DecisionIdentity, DecisionJournalEntry
from adaptive_trading_bot.regime.technical import RegimeLabel
from adaptive_trading_bot.research.all_fill_replay import AllFillStatus
from adaptive_trading_bot.research.breakout_history_screened_comparison import (
    ACCEPTED_BINDING,
    BASELINE_IDENTITY,
    FOLLOW_UP,
    PANEL,
    PROTOCOL_SHA256,
    TASK_PLAN_SHA256,
    TASKS,
    ZONE_IDENTITY,
    ComparisonArm,
    ComparisonScenario,
    HistoryScreenedConfiguration,
    HistoryScreenedReplayResult,
    HistoryScreenedStrategyContext,
    HistoryScreenedTask,
    _canonical,
    report_block,
    report_week,
)
from adaptive_trading_bot.research.breakout_history_screened_execution import (
    _CONTEXT,
    PROJECTION_SCHEMA,
    REPORT_SCHEMA,
    RESULT_SCHEMA,
    HistoryScreenedRunAssociation,
    _actual_side,
    _authorized,
    _decimal_text,
    _decision_side,
    _exact_sum,
    _opportunity_id,
    _opportunity_record,
    _regime,
)
from adaptive_trading_bot.research.breakout_history_screened_ledger import (
    HistoryScreenedLedgerState,
    HistoryScreenedLedgerStore,
)
from adaptive_trading_bot.research.breakout_zone_comparison import (
    OpportunityKey,
    _opportunity_key,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    HistoricalStrategyContextObservation,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import decode, encode
from adaptive_trading_bot.research.trend_pullback_corpus import _INTEGRITY_SEAL_VERSION
from adaptive_trading_bot.strategies.breakout_retest_zone_invalidation import (
    BreakoutRetestZoneInvalidationAudit,
)
from adaptive_trading_bot.strategies.core import CandidateAction

RECOVERY_PROTOCOL = "ISSUE220_BOUNDED_MEMORY_RECOVERY_V1"
RECOVERY_SCHEMA = "issue-220-bounded-memory-recovery-attestation-v1"
ORIGINAL_EXECUTABLE_SHA = "d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e"
ORIGINAL_RUN_UUID = "ec10ca5d-01e6-436f-8153-fe9c5b5439b5"
ORIGINAL_REVISION_SHA256 = "0db0661d022a8c0c4260852bfd6490a2afab45416ffceb83e8f04dfff5eacbe2"
SOURCE_INDEX_SHA256 = "e45e9a1a23f763ddba1bc4c2e087717f843eec2eaafdf325c5a9b5e8ad974ed6"
SOURCE_INVENTORY_SHA256 = "0d013aee1afe004c428df3813435c627cd78b6c34416e85920dd4b3b5db6fafd"
BLOCKED_STATUS_SHA256 = "6d574a3012316001c10455b7fe587c266b5658f31976b9d6b65cc7ccf4e282b1"
WORKING_SET_BUDGET_BYTES = 2 * 1024**3
_POPULATION_TYPE = (
    "adaptive_trading_bot.research.breakout_history_screened_comparison."
    "HistoryScreenedDecisionPopulation"
)


class BoundedRecoveryError(ValueError):
    """The retained source cannot be reconstructed without weakening an invariant."""


def _sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise BoundedRecoveryError(f"recovery output already exists: {path.name}") from exc
    _fsync_directory(path.parent)
    return _sha_bytes(payload)


def _working_set_bytes() -> int:
    if os.name == "nt":

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        windll = cast(Any, ctypes.windll)
        kernel = windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        process = kernel.GetCurrentProcess()
        get_memory = windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        get_memory.restype = ctypes.c_int
        if not get_memory(process, ctypes.byref(counters), counters.cb):
            raise BoundedRecoveryError("working-set measurement failed")
        return int(counters.WorkingSetSize)
    statm = Path("/proc/self/statm")
    if statm.is_file():
        pages = int(statm.read_text(encoding="ascii").split()[1])
        sysconf = cast(Callable[[str], int], cast(Any, os).sysconf)
        return pages * sysconf("SC_PAGE_SIZE")
    raise BoundedRecoveryError("working-set measurement is unavailable")


@dataclass(slots=True)
class ResourceTracker:
    budget_bytes: int = WORKING_SET_BUDGET_BYTES
    peak_working_set_bytes: int = 0
    peak_tracemalloc_bytes: int = 0
    samples: int = 0

    def start(self) -> None:
        tracemalloc.start()
        self.sample()

    def sample(self) -> None:
        working = _working_set_bytes()
        _current, traced_peak = tracemalloc.get_traced_memory()
        self.peak_working_set_bytes = max(self.peak_working_set_bytes, working)
        self.peak_tracemalloc_bytes = max(self.peak_tracemalloc_bytes, traced_peak)
        self.samples += 1
        if working > self.budget_bytes:
            raise BoundedRecoveryError("bounded recovery exceeded declared working-set budget")

    def stop(self) -> None:
        self.sample()
        tracemalloc.stop()


class _JsonStream:
    """Minimal exact JSON value reader over a buffered binary stream."""

    def __init__(self, source: BinaryIO, *, chunk_size: int = 4 * 1024 * 1024) -> None:
        self._source = source
        self._chunk_size = chunk_size
        self._buffer = bytearray()
        self._offset = 0
        self.observer: Callable[[bytes], None] | None = None
        self._decoder = json.JSONDecoder()

    def _compact(self) -> None:
        if self._offset:
            del self._buffer[: self._offset]
            self._offset = 0

    def _fill(self) -> bool:
        if self._offset < len(self._buffer):
            return True
        self._compact()
        chunk = self._source.read(self._chunk_size)
        if not chunk:
            return False
        self._buffer.extend(chunk)
        return True

    def _fill_more(self) -> bool:
        if self._offset:
            self._compact()
        chunk = self._source.read(self._chunk_size)
        if not chunk:
            return False
        self._buffer.extend(chunk)
        return True

    def peek(self) -> int | None:
        return self._buffer[self._offset] if self._fill() else None

    def take(self) -> int:
        if not self._fill():
            raise BoundedRecoveryError("JSON evidence ended unexpectedly")
        value = int(self._buffer[self._offset])
        self._offset += 1
        if self.observer is not None:
            self.observer(bytes((value,)))
        return value

    def expect(self, expected: bytes) -> None:
        actual = bytes(self.take() for _ in expected)
        if actual != expected:
            raise BoundedRecoveryError("JSON evidence structure changed")

    def raw_value(self) -> bytes:
        if not self._fill():
            raise BoundedRecoveryError("JSON evidence lacks a value")
        while True:
            candidate = bytes(self._buffer[self._offset :])
            try:
                _value, end = self._decoder.raw_decode(candidate.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if self._fill_more():
                    continue
                raise BoundedRecoveryError("JSON evidence is malformed or truncated") from exc
            raw = candidate[:end]
            self._offset += end
            if self.observer is not None:
                self.observer(raw)
            return raw

    def string(self) -> str:
        value = json.loads(self.raw_value())
        if not isinstance(value, str):
            raise BoundedRecoveryError("JSON string field changed")
        return value

    def finish(self) -> None:
        if self.peek() is not None:
            raise BoundedRecoveryError("JSON evidence has trailing bytes")


def _decoded(raw: bytes) -> object:
    try:
        return decode(json.loads(raw))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise BoundedRecoveryError("typed authority evidence is malformed") from exc


def _tagged_tuple(stream: _JsonStream) -> Iterator[bytes]:
    stream.expect(b"[")
    if stream.string() != "tuple":
        raise BoundedRecoveryError("population collection is not a canonical tuple")
    stream.expect(b",[")
    first = True
    while stream.peek() != ord("]"):
        if not first:
            stream.expect(b",")
        yield stream.raw_value()
        first = False
    stream.expect(b"]]")


def _authority_digest(value: object) -> str:
    return _sha_bytes(_canonical(encode(value)))


@dataclass(slots=True)
class _DecisionCompact:
    identity: DecisionIdentity
    digest: str
    context_digest: str
    action: str
    actual_side: str
    audit_side: str
    regime: str
    authorized: bool
    reasons_against: tuple[str, ...]
    expected_opportunity: OpportunityKey | None
    opportunity: OpportunityKey | None = None
    action_digest: str = ""
    reasons_for_digest: str = ""
    reasons_against_digest: str = ""
    rule_audit_digest: str = ""


@dataclass(slots=True)
class _PopulationCompact:
    configuration: HistoryScreenedConfiguration
    baseline: list[_DecisionCompact]
    zone: list[_DecisionCompact]
    baseline_mask: tuple[OpportunityKey, ...]
    zone_mask: tuple[OpportunityKey, ...]
    population_sha256: str


def _decision_compact(
    decision: DecisionJournalEntry[object, object],
    *,
    expected_opportunity: OpportunityKey | None,
) -> _DecisionCompact:
    decision.validate()
    evaluation = decision.strategy_evaluation
    return _DecisionCompact(
        decision.identity,
        _authority_digest(decision),
        _authority_digest(decision.context),
        evaluation.action.value,
        _actual_side(decision),
        _decision_side(decision),
        _regime(decision),
        _authorized(decision),
        tuple(str(cast(Any, reason).value) for reason in evaluation.reasons_against),
        expected_opportunity,
        action_digest=_authority_digest(evaluation.action),
        reasons_for_digest=_authority_digest(evaluation.reasons_for),
        reasons_against_digest=_authority_digest(evaluation.reasons_against),
        rule_audit_digest=_authority_digest(evaluation.rule_audit),
    )


def _expected_mask(decisions: list[_DecisionCompact]) -> tuple[OpportunityKey, ...]:
    selected: list[OpportunityKey] = []
    last: datetime | None = None
    for decision in decisions:
        if not decision.authorized:
            continue
        if decision.opportunity is None:
            raise BoundedRecoveryError("admitted decision lacks opportunity identity")
        if last is None or decision.identity.observed_at >= last + FOLLOW_UP:
            selected.append(decision.opportunity)
            last = decision.identity.observed_at
    return tuple(selected)


def _gzip_signature(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(2)


def _read_population(
    path: Path,
    task: HistoryScreenedTask,
    tracker: ResourceTracker,
    *,
    expected_binding: object = ACCEPTED_BINDING,
) -> _PopulationCompact:
    if _gzip_signature(path) != b"\x1f\x8b":
        raise BoundedRecoveryError("population payload is not gzip evidence")
    compressed_sha = _sha_file(path)
    try:
        handle = gzip.open(path, "rb")  # noqa: SIM115 - closed after streaming parse
        stream = _JsonStream(cast(BinaryIO, handle))
        stream.expect(b'{"authority":')
        seal_hash = hashlib.sha256()
        seal_hash.update(
            _canonical(
                (
                    "adaptive-trading-bot/history-screened-180d-population",
                    _INTEGRITY_SEAL_VERSION,
                )
            )[:-1]
            + b',["dataclass",'
            + _canonical(_POPULATION_TYPE)
            + b",["
        )
        stream.expect(b"[")
        if stream.string() != "dataclass":
            raise BoundedRecoveryError("population authority is not a dataclass")
        stream.expect(b",")
        if stream.string() != _POPULATION_TYPE:
            raise BoundedRecoveryError("population authority type changed")
        stream.expect(b",[")
        expected_fields = (
            "configuration",
            "contexts",
            "baseline_decisions",
            "zone_decisions",
            "opportunity_keys",
            "baseline_spacing_mask",
            "zone_spacing_mask",
            "schema_version",
        )
        configuration: HistoryScreenedConfiguration | None = None
        context_digests: dict[datetime, str] = {}
        baseline: list[_DecisionCompact] = []
        zone: list[_DecisionCompact] = []
        parsed_keys: list[OpportunityKey | None] = []
        parsed_masks: dict[str, tuple[OpportunityKey, ...]] = {}
        schema_version = ""
        for index, name in enumerate(expected_fields):
            if index:
                stream.expect(b",")
                seal_hash.update(b",")
            seal_hash.update(b"[" + _canonical(name) + b",")
            stream.expect(b"[")
            if stream.string() != name:
                raise BoundedRecoveryError("population field order changed")
            stream.expect(b",")
            if name == "configuration":
                value = _decoded(stream.raw_value())
                if not isinstance(value, HistoryScreenedConfiguration):
                    raise BoundedRecoveryError("population configuration type changed")
                value._validate_facts()
                if value.task != task or value.input_binding != expected_binding:
                    raise BoundedRecoveryError("population configuration differs from task")
                configuration = value
                seal_hash.update(_canonical(encode(value, exclude_seal=True)))
            elif name == "contexts":
                previous: datetime | None = None
                seal_hash.update(b'["tuple",[')
                for item_index, raw in enumerate(_tagged_tuple(stream)):
                    value = _decoded(raw)
                    if not isinstance(value, HistoricalStrategyContextObservation):
                        raise BoundedRecoveryError("population context type changed")
                    value.validate()
                    if not isinstance(value.context, HistoryScreenedStrategyContext):
                        raise BoundedRecoveryError("population context lacks input binding")
                    observed = value.context.observed_at
                    if previous is not None and observed <= previous:
                        raise BoundedRecoveryError("population contexts are not chronological")
                    if observed in context_digests:
                        raise BoundedRecoveryError("population context is duplicated")
                    context_digests[observed] = _authority_digest(value.context)
                    previous = observed
                    if item_index:
                        seal_hash.update(b",")
                    seal_hash.update(_canonical(encode(value, exclude_seal=True)))
                    if item_index % 512 == 0:
                        tracker.sample()
                seal_hash.update(b"]]")
            elif name in {"baseline_decisions", "zone_decisions"}:
                if configuration is None:
                    raise BoundedRecoveryError("population configuration is unavailable")
                destination = baseline if name == "baseline_decisions" else zone
                expected_identity = BASELINE_IDENTITY if destination is baseline else ZONE_IDENTITY
                seal_hash.update(b'["tuple",[')
                for item_index, raw in enumerate(_tagged_tuple(stream)):
                    value = _decoded(raw)
                    if not isinstance(value, DecisionJournalEntry):
                        raise BoundedRecoveryError("population decision type changed")
                    expected_at = task.signal_start + Timeframe.MINUTES_15.duration * (
                        item_index + 1
                    )
                    if (
                        value.identity.observed_at != expected_at
                        or (
                            value.identity.strategy_id,
                            value.identity.strategy_version,
                        )
                        != expected_identity
                        or value.identity.source_id != configuration.input_binding.source_id
                        or value.identity.symbol != task.symbol
                        or context_digests.get(expected_at) != _authority_digest(value.context)
                    ):
                        raise BoundedRecoveryError("population decision authority changed")
                    expected_key = (
                        _opportunity_key(
                            value,
                            dataset_version=configuration.input_binding.dataset_version,
                            manifest_sha256=configuration.input_binding.manifest_sha256,
                            epoch_start=task.context_start,
                        )
                        if destination is baseline
                        else None
                    )
                    compact = _decision_compact(value, expected_opportunity=expected_key)
                    if destination is zone:
                        if item_index >= len(baseline):
                            raise BoundedRecoveryError("zone decision precedes baseline authority")
                        base = baseline[item_index]
                        audit = value.strategy_evaluation.rule_audit
                        if (
                            compact.context_digest != base.context_digest
                            or not isinstance(audit, BreakoutRetestZoneInvalidationAudit)
                            or _authority_digest(audit.underlying_action) != base.action_digest
                            or _authority_digest(audit.underlying_reasons_for)
                            != base.reasons_for_digest
                            or _authority_digest(audit.underlying_reasons_against)
                            != base.reasons_against_digest
                            or _authority_digest(audit.underlying_rule_audit)
                            != base.rule_audit_digest
                            or (
                                value.strategy_evaluation.action is not CandidateAction.NO_TRADE
                                and base.action == CandidateAction.NO_TRADE.value
                            )
                        ):
                            raise BoundedRecoveryError("cross-arm decision authority changed")
                    destination.append(compact)
                    if item_index:
                        seal_hash.update(b",")
                    seal_hash.update(_canonical(encode(value, exclude_seal=True)))
                    if item_index % 256 == 0:
                        tracker.sample()
                seal_hash.update(b"]]")
            elif name == "opportunity_keys":
                seen: set[OpportunityKey] = set()
                seal_hash.update(b'["tuple",[')
                for item_index, raw in enumerate(_tagged_tuple(stream)):
                    value = _decoded(raw)
                    if value is not None and not isinstance(value, OpportunityKey):
                        raise BoundedRecoveryError("opportunity authority type changed")
                    if (
                        item_index >= len(baseline)
                        or value != baseline[item_index].expected_opportunity
                    ):
                        raise BoundedRecoveryError("opportunity authority differs from baseline")
                    if value is not None:
                        if value in seen:
                            raise BoundedRecoveryError("opportunity authority is duplicated")
                        seen.add(value)
                    parsed_keys.append(value)
                    if item_index:
                        seal_hash.update(b",")
                    seal_hash.update(_canonical(encode(value, exclude_seal=True)))
                seal_hash.update(b"]]")
                if len(parsed_keys) != len(baseline):
                    raise BoundedRecoveryError("opportunity coverage differs from decisions")
                for item_index, value in enumerate(parsed_keys):
                    baseline[item_index].opportunity = value
                    zone[item_index].opportunity = value
            elif name in {"baseline_spacing_mask", "zone_spacing_mask"}:
                values: list[OpportunityKey] = []
                seal_hash.update(b'["tuple",[')
                for item_index, raw in enumerate(_tagged_tuple(stream)):
                    value = _decoded(raw)
                    if not isinstance(value, OpportunityKey):
                        raise BoundedRecoveryError("spacing-mask authority type changed")
                    values.append(value)
                    if item_index:
                        seal_hash.update(b",")
                    seal_hash.update(_canonical(encode(value, exclude_seal=True)))
                seal_hash.update(b"]]")
                parsed_masks[name] = tuple(values)
            else:
                value = _decoded(stream.raw_value())
                if not isinstance(value, str):
                    raise BoundedRecoveryError("population schema value changed")
                schema_version = value
                seal_hash.update(_canonical(encode(value, exclude_seal=True)))
            stream.expect(b"]")
            seal_hash.update(b"]")
        seal_hash.update(b"]]]")
        stream.expect(b",")
        stream.expect(b"[")
        if stream.string() != "integrity_seal":
            raise BoundedRecoveryError("population seal field changed")
        stream.expect(b",")
        seal = _decoded(stream.raw_value())
        if not isinstance(seal, str):
            raise BoundedRecoveryError("population seal is malformed")
        stream.expect(b"]]]")
        stream.expect(b',"schema":')
        if stream.string() != RESULT_SCHEMA:
            raise BoundedRecoveryError("population evidence schema changed")
        stream.expect(b"}")
        stream.finish()
        handle.close()
    except (OSError, EOFError) as exc:
        raise BoundedRecoveryError("population gzip is truncated or corrupt") from exc
    if seal != seal_hash.hexdigest():
        raise BoundedRecoveryError("population integrity seal changed")
    if schema_version != "history-screened-180d-population-v2":
        raise BoundedRecoveryError("population schema version changed")
    expected_slots = int((task.signal_end - task.signal_start) / Timeframe.MINUTES_15.duration)
    if len(baseline) != expected_slots or len(zone) != expected_slots:
        raise BoundedRecoveryError("population decision-slot coverage changed")
    if parsed_masks.get("baseline_spacing_mask") != _expected_mask(baseline):
        raise BoundedRecoveryError("baseline spacing mask changed")
    if parsed_masks.get("zone_spacing_mask") != _expected_mask(zone):
        raise BoundedRecoveryError("zone spacing mask changed")
    tracker.sample()
    return _PopulationCompact(
        cast(HistoryScreenedConfiguration, configuration),
        baseline,
        zone,
        parsed_masks["baseline_spacing_mask"],
        parsed_masks["zone_spacing_mask"],
        compressed_sha,
    )


@dataclass(slots=True)
class _MetricsReducer:
    denominator: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    filled: int = 0
    unresolved: int = 0
    closed_r: list[Decimal] = field(default_factory=list)
    fees: list[Decimal] = field(default_factory=list)
    residuals: dict[str, list[Decimal]] = field(default_factory=dict)

    def add(self, result: HistoryScreenedReplayResult) -> None:
        self.denominator += 1
        self.statuses[result.status.value] += 1
        self.filled += bool(result.branches)
        self.unresolved += result.status in {
            AllFillStatus.ORDINARY_OPEN,
            AllFillStatus.RECOVERY_OPEN,
        }
        for branch in result.branches:
            if branch.closed_net_r is not None:
                self.closed_r.append(branch.closed_net_r)
            self.fees.append(branch.final_position.cumulative_fees)
            if branch.final_position.quantity:
                self.residuals.setdefault(str(branch.final_position.symbol), []).append(
                    branch.final_position.quantity.copy_abs()
                )

    def project(self) -> dict[str, object]:
        closed = tuple(self.closed_r)
        with localcontext(_CONTEXT):
            mean_r = sum(closed, Decimal(0)) / len(closed) if closed else None
            median_r = cast(Decimal | None, median(closed)) if closed else None
            gains = sum((value for value in closed if value > 0), Decimal(0))
            losses = -sum((value for value in closed if value < 0), Decimal(0))
            profit_factor = gains / losses if losses > 0 else None
        return {
            "denominator": self.denominator,
            "terminal_statuses": dict(sorted(self.statuses.items())),
            "filled": self.filled,
            "closed": len(closed),
            "unresolved_filled": self.unresolved,
            "closed_net_r_mean": _decimal_text(mean_r),
            "closed_net_r_median": _decimal_text(median_r),
            "r_profit_factor": _decimal_text(profit_factor),
            "incurred_fees_quote": str(_exact_sum(tuple(self.fees))),
            "residual_base_quantity_by_asset": {
                key: str(_exact_sum(tuple(values)))
                for key, values in sorted(self.residuals.items())
            },
        }


@dataclass(frozen=True, slots=True)
class _ScreenRow:
    status: AllFillStatus
    closed_r: Decimal | None
    asset: str
    week: str


@dataclass(slots=True)
class _PanelReducer:
    metrics: dict[tuple[ComparisonArm, ComparisonScenario, bool], _MetricsReducer] = field(
        default_factory=lambda: {
            (arm, scenario, masked): _MetricsReducer()
            for arm in ComparisonArm
            for scenario in ComparisonScenario
            for masked in (False, True)
        }
    )
    admitted: dict[ComparisonArm, set[OpportunityKey]] = field(
        default_factory=lambda: {arm: set() for arm in ComparisonArm}
    )
    masks: dict[ComparisonArm, set[OpportunityKey]] = field(
        default_factory=lambda: {arm: set() for arm in ComparisonArm}
    )
    screen: dict[tuple[ComparisonArm, ComparisonScenario, bool], list[_ScreenRow]] = field(
        default_factory=lambda: {
            (arm, scenario, masked): []
            for arm in ComparisonArm
            for scenario in ComparisonScenario
            for masked in (False, True)
        }
    )
    paired: dict[
        tuple[ComparisonArm, ComparisonScenario, OpportunityKey],
        tuple[AllFillStatus, Decimal | None],
    ] = field(default_factory=dict)

    def register_population(self, population: _PopulationCompact) -> None:
        for arm, decisions, mask in (
            (ComparisonArm.BASELINE, population.baseline, population.baseline_mask),
            (ComparisonArm.ZONE, population.zone, population.zone_mask),
        ):
            self.masks[arm].update(mask)
            self.admitted[arm].update(
                decision.opportunity
                for decision in decisions
                if decision.authorized and decision.opportunity is not None
            )

    def add(
        self,
        result: HistoryScreenedReplayResult,
        decision: _DecisionCompact,
        *,
        masked: bool,
    ) -> None:
        self.metrics[result.arm, result.scenario, False].add(result)
        if masked:
            self.metrics[result.arm, result.scenario, True].add(result)
        closed_r = result.branches[0].closed_net_r if len(result.branches) == 1 else None
        if result.branches or result.status in {
            AllFillStatus.ORDINARY_OPEN,
            AllFillStatus.RECOVERY_OPEN,
        }:
            row = _ScreenRow(
                result.status,
                closed_r,
                str(result.decision.identity.symbol),
                report_week(result.decision.identity.observed_at - Timeframe.MINUTES_15.duration),
            )
            self.screen[result.arm, result.scenario, False].append(row)
            if masked:
                self.screen[result.arm, result.scenario, True].append(row)
        if decision.opportunity is not None:
            cell = (result.arm, result.scenario, decision.opportunity)
            if cell in self.paired:
                raise BoundedRecoveryError("opportunity result is duplicated across tasks")
            self.paired[cell] = (result.status, closed_r)

    @staticmethod
    def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
        with localcontext(_CONTEXT):
            return sum(values, Decimal(0)) / len(values) if values else None

    def _classification(self, arm: ComparisonArm) -> str:
        for scenario in ComparisonScenario:
            full = tuple(self.screen[arm, scenario, False])
            masked = tuple(self.screen[arm, scenario, True])
            if any(
                item.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
                for item in full
            ):
                return "INSUFFICIENT_EVIDENCE"
            if len(tuple(item for item in full if item.closed_r is not None)) < 60:
                return "INSUFFICIENT_EVIDENCE"
            if len(tuple(item for item in masked if item.closed_r is not None)) < 30:
                return "INSUFFICIENT_EVIDENCE"
            asset_counts = Counter(item.asset for item in masked if item.closed_r is not None)
            week_counts = Counter(item.week for item in masked if item.closed_r is not None)
            if sum(value >= 3 for value in asset_counts.values()) < 5:
                return "INSUFFICIENT_EVIDENCE"
            if sum(value >= 3 for value in week_counts.values()) < 6:
                return "INSUFFICIENT_EVIDENCE"
        means = tuple(
            self._mean(
                tuple(
                    item.closed_r
                    for item in self.screen[arm, scenario, masked]
                    if item.closed_r is not None
                )
            )
            for masked in (False, True)
            for scenario in ComparisonScenario
        )
        if all(value is not None and value <= 0 for value in means):
            return "NEGATIVE_EVIDENCE"
        if not all(value is not None and value > 0 for value in means):
            return "TECHNICALLY_VALID_MIXED_EVIDENCE"
        positive_assets: list[set[str]] = []
        for scenario in ComparisonScenario:
            rows = self.screen[arm, scenario, True]
            counts = Counter(item.asset for item in rows if item.closed_r is not None)
            positive_assets.append(
                {
                    asset
                    for asset, count in counts.items()
                    if count >= 3
                    and (
                        self._mean(
                            tuple(
                                item.closed_r
                                for item in rows
                                if item.asset == asset and item.closed_r is not None
                            )
                        )
                        or Decimal(0)
                    )
                    > 0
                }
            )
        if len(set.intersection(*positive_assets)) < 5:
            return "TECHNICALLY_VALID_MIXED_EVIDENCE"
        stress_rows = self.screen[arm, ComparisonScenario.STRESS, True]
        week_counts = Counter(item.week for item in stress_rows if item.closed_r is not None)
        positive_weeks = sum(
            count >= 3
            and (
                self._mean(
                    tuple(
                        item.closed_r
                        for item in stress_rows
                        if item.week == week and item.closed_r is not None
                    )
                )
                or Decimal(0)
            )
            > 0
            for week, count in week_counts.items()
        )
        if positive_weeks < 6:
            return "TECHNICALLY_VALID_MIXED_EVIDENCE"
        for omitted in PANEL:
            for is_masked in (False, True):
                remaining = tuple(
                    item.closed_r
                    for item in self.screen[arm, ComparisonScenario.STRESS, is_masked]
                    if item.asset != omitted and item.closed_r is not None
                )
                if (self._mean(remaining) or Decimal(0)) <= 0:
                    return "TECHNICALLY_VALID_MIXED_EVIDENCE"
        return "CANDIDATE_FOR_FURTHER_VALIDATION"

    def project(
        self,
        run: HistoryScreenedRunAssociation,
        *,
        tasks: tuple[HistoryScreenedTask, ...] = TASKS,
    ) -> dict[str, object]:
        absolute = {
            f"{arm.value}:{scenario.value}": {
                "full": self.metrics[arm, scenario, False].project(),
                "masked": self.metrics[arm, scenario, True].project(),
            }
            for arm in ComparisonArm
            for scenario in ComparisonScenario
        }
        paired: dict[str, object] = {}
        intersection = self.masks[ComparisonArm.BASELINE] & self.masks[ComparisonArm.ZONE]
        admission_union = self.admitted[ComparisonArm.BASELINE] | self.admitted[ComparisonArm.ZONE]
        common = self.admitted[ComparisonArm.BASELINE] & self.admitted[ComparisonArm.ZONE]
        for scenario in ComparisonScenario:
            for label, keys in (("full", admission_union), ("intersection_mask", intersection)):
                deltas: list[Decimal] = []
                partitions: dict[str, list[str]] = {}
                for key in sorted(keys, key=_opportunity_id):
                    identity = _opportunity_id(key)
                    if key not in self.admitted[ComparisonArm.BASELINE]:
                        partitions.setdefault("BASELINE_NOT_ADMITTED", []).append(identity)
                    if key not in self.admitted[ComparisonArm.ZONE]:
                        partitions.setdefault("ZONE_NOT_ADMITTED", []).append(identity)
                    baseline_status, baseline_r = self.paired[ComparisonArm.BASELINE, scenario, key]
                    zone_status, zone_r = self.paired[ComparisonArm.ZONE, scenario, key]
                    if baseline_r is not None and zone_r is not None:
                        with localcontext(_CONTEXT):
                            deltas.append(zone_r - baseline_r)
                        partitions.setdefault("BOTH_CLOSED", []).append(identity)
                    else:
                        partitions.setdefault(
                            f"BASELINE_{baseline_status.value}|ZONE_{zone_status.value}", []
                        ).append(identity)
                paired[f"{scenario.value}:{label}"] = {
                    "admission_union_denominator": len(keys),
                    "common_admission_denominator": len(keys & common),
                    "both_closed": len(deltas),
                    "reason_partitions": dict(sorted(partitions.items())),
                    "mean_zone_minus_baseline_closed_net_r": _decimal_text(
                        self._mean(tuple(deltas))
                    ),
                }
        return {
            "schema": PROJECTION_SCHEMA,
            "protocol_sha256": PROTOCOL_SHA256,
            "task_plan_sha256": TASK_PLAN_SHA256,
            "run": run.record(),
            "task_count": len(tasks),
            "task_ids": [task.task_id for task in tasks],
            "absolute": absolute,
            "paired": paired,
            "classifications": {arm.value: self._classification(arm) for arm in ComparisonArm},
            "interpretation": "SELECTION_INFORMED_EXPLORATORY_NON_PROMOTION",
        }


def _grouped_projection(
    decisions: dict[ComparisonArm, list[_DecisionCompact]],
    statuses: dict[tuple[ComparisonArm, ComparisonScenario, int], str],
) -> dict[str, object]:
    grouped: dict[str, object] = {}
    regimes = tuple(item.value for item in RegimeLabel)
    for dimension, labels in (
        ("BLOCK", ("P1", "P2", "P3")),
        ("WEEK", tuple(f"W{index}" for index in range(1, 27))),
    ):
        for arm in ComparisonArm:
            compact = decisions[arm]
            for scenario in ComparisonScenario:
                for label in labels:
                    for side in ("LONG", "SHORT", "UNAVAILABLE"):
                        for regime in regimes:
                            indexes = [
                                index
                                for index, decision in enumerate(compact)
                                if (
                                    report_block(
                                        decision.identity.observed_at
                                        - Timeframe.MINUTES_15.duration
                                    )
                                    if dimension == "BLOCK"
                                    else report_week(
                                        decision.identity.observed_at
                                        - Timeframe.MINUTES_15.duration
                                    )
                                )
                                == label
                                and decision.audit_side == side
                                and decision.regime == regime
                            ]
                            terminal = Counter(statuses[arm, scenario, index] for index in indexes)
                            key = "|".join(
                                (
                                    arm.value,
                                    scenario.value,
                                    f"{dimension}:{label}",
                                    f"SIDE:{side}",
                                    f"REGIME:{regime}",
                                )
                            )
                            grouped[key] = {
                                "denominator": len(indexes),
                                "terminal_statuses": dict(sorted(terminal.items())),
                            }
    return grouped


def _diagnostic(
    result: HistoryScreenedReplayResult, decision: _DecisionCompact
) -> dict[str, object]:
    replay = result.legacy_replay
    fill = None if replay is None else replay.entry_fill
    branch = result.branches[0] if len(result.branches) == 1 else None
    return {
        "opportunity": (
            None if decision.opportunity is None else _opportunity_id(decision.opportunity)
        ),
        "arm": result.arm.value,
        "scenario": result.scenario.value,
        "status": result.status.value,
        "approved_risk_quote": (
            None if result.entry_intent is None else str(result.entry_intent.approved_risk_quote)
        ),
        "quantity": None if result.entry_intent is None else str(result.entry_intent.quantity),
        "actual_entry_fill": None if fill is None else str(fill.fill_price),
        "sealed_stop": None if replay is None else _decimal_text(replay.stop_basis),
        "fixed_2r_target": (None if replay is None else _decimal_text(replay.target_trigger_price)),
        "holding_seconds": (
            None
            if branch is None or branch.exit_fill is None
            else str((branch.exit_fill.fill_at - branch.entry_fill.fill_at).total_seconds())
        ),
        "recovery": branch is not None and branch.recovery is not None,
        "closed_net_r": None if branch is None else _decimal_text(branch.closed_net_r),
    }


def _task_report_from_stream(
    directory: Path,
    task: HistoryScreenedTask,
    population: _PopulationCompact,
    run: HistoryScreenedRunAssociation,
    panel: _PanelReducer,
    tracker: ResourceTracker,
) -> bytes:
    report_path = directory / f"{task.index:02d}.report.json"
    report_payload = report_path.read_bytes()
    try:
        retained = json.loads(report_payload)
    except (UnicodeError, ValueError) as exc:
        raise BoundedRecoveryError("retained task report is malformed") from exc
    if not isinstance(retained, dict) or _canonical(retained) != report_payload:
        raise BoundedRecoveryError("retained task report is not canonical")
    result_path = directory / f"{task.index:02d}.results.jsonl.gz"
    if _gzip_signature(result_path) != b"\x1f\x8b":
        raise BoundedRecoveryError("result payload is not gzip evidence")
    decisions = {
        ComparisonArm.BASELINE: population.baseline,
        ComparisonArm.ZONE: population.zone,
    }
    by_identity = {
        (arm, decision.identity): (index, decision)
        for arm in ComparisonArm
        for index, decision in enumerate(decisions[arm])
    }
    masks = {
        ComparisonArm.BASELINE: set(population.baseline_mask),
        ComparisonArm.ZONE: set(population.zone_mask),
    }
    metrics = {
        (arm, scenario, masked): _MetricsReducer()
        for arm in ComparisonArm
        for scenario in ComparisonScenario
        for masked in (False, True)
    }
    statuses: dict[tuple[ComparisonArm, ComparisonScenario, int], str] = {}
    result_status: dict[tuple[ComparisonArm, ComparisonScenario, OpportunityKey], str] = {}
    diagnostics: list[dict[str, object]] = []
    seen = bytearray(len(population.baseline) * 4)
    try:
        with gzip.open(result_path, "rb") as handle:
            for line_index, line in enumerate(handle):
                if not line.endswith(b"\n"):
                    raise BoundedRecoveryError("result JSONL line is truncated")
                value = _decoded(line[:-1])
                if not isinstance(value, HistoryScreenedReplayResult):
                    raise BoundedRecoveryError("result evidence has a foreign type")
                value.validate()
                lookup = by_identity.get((value.arm, value.decision.identity))
                if lookup is None:
                    raise BoundedRecoveryError("result decision is outside population")
                slot, decision = lookup
                if (
                    value.task_id != task.task_id
                    or value.population_sha256 != population.population_sha256
                    or _authority_digest(value.decision) != decision.digest
                ):
                    raise BoundedRecoveryError("result differs from task/population authority")
                cell_index = (
                    list(ComparisonArm).index(value.arm) * 2
                    + list(ComparisonScenario).index(value.scenario)
                ) * len(population.baseline) + slot
                if seen[cell_index]:
                    raise BoundedRecoveryError("result cell is duplicated")
                seen[cell_index] = 1
                masked = decision.opportunity in masks[value.arm]
                metrics[value.arm, value.scenario, False].add(value)
                if masked:
                    metrics[value.arm, value.scenario, True].add(value)
                statuses[value.arm, value.scenario, slot] = value.status.value
                if decision.opportunity is not None:
                    result_status[value.arm, value.scenario, decision.opportunity] = (
                        value.status.value
                    )
                diagnostics.append(_diagnostic(value, decision))
                panel.add(value, decision, masked=masked)
                if line_index % 512 == 0:
                    tracker.sample()
    except (OSError, EOFError) as exc:
        raise BoundedRecoveryError("result gzip is truncated or corrupt") from exc
    if not seen or 0 in seen:
        raise BoundedRecoveryError("result cells are missing")
    baseline_candidates = {
        item.opportunity
        for item in population.baseline
        if item.opportunity is not None and item.actual_side != "UNAVAILABLE"
    }
    zone_candidates = {
        item.opportunity
        for item in population.zone
        if item.opportunity is not None and item.actual_side != "UNAVAILABLE"
    }
    admitted = {
        ComparisonArm.BASELINE: {
            item.opportunity
            for item in population.baseline
            if item.opportunity is not None and item.authorized
        },
        ComparisonArm.ZONE: {
            item.opportunity
            for item in population.zone
            if item.opportunity is not None and item.authorized
        },
    }
    opportunity_records = {
        _opportunity_id(key): _opportunity_record(key)
        for key in {
            item.opportunity for item in population.baseline if item.opportunity is not None
        }
    }
    zone_rejections = {
        item.opportunity
        for item in population.zone
        if item.opportunity is not None and item.actual_side == "UNAVAILABLE"
    }
    rejection_reasons = Counter(
        reason
        for item in population.zone
        if item.opportunity in zone_rejections
        for reason in item.reasons_against
    )
    action_pairs = Counter(
        f"{baseline.action}|{zone.action}"
        for baseline, zone in zip(population.baseline, population.zone, strict=True)
    )
    admission_union = admitted[ComparisonArm.BASELINE] | admitted[ComparisonArm.ZONE]
    outcome_partitions: dict[str, object] = {}
    for scenario in ComparisonScenario:
        partitions: dict[str, dict[str, list[str]]] = {}
        for arm in ComparisonArm:
            values: dict[str, list[str]] = {}
            for opportunity in sorted(admission_union, key=_opportunity_id):
                label = (
                    "NOT_ADMITTED"
                    if opportunity not in admitted[arm]
                    else result_status[arm, scenario, opportunity]
                )
                values.setdefault(label, []).append(_opportunity_id(opportunity))
            partitions[arm.value] = values
        outcome_partitions[scenario.value] = partitions
    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "run": run.record(),
        "population_sha256": population.population_sha256,
        "task_id": task.task_id,
        "task_index": task.index,
        "wire_symbol": task.wire_symbol,
        "decision_slots_per_arm": len(population.baseline),
        "candidates": {
            "baseline": len(baseline_candidates),
            "zone": len(zone_candidates),
            "underlying_opportunities": len(opportunity_records),
            "zone_rejections": len(zone_rejections),
        },
        "opportunity_records": opportunity_records,
        "candidate_membership": {
            "BASELINE": sorted(_opportunity_id(key) for key in baseline_candidates),
            "ZONE": sorted(_opportunity_id(key) for key in zone_candidates),
        },
        "zone_rejection_membership": sorted(_opportunity_id(key) for key in zone_rejections),
        "zone_rejection_reasons": dict(sorted(rejection_reasons.items())),
        "observation_action_pairs": dict(sorted(action_pairs.items())),
        "both_no_signal_observations": action_pairs.get("NO_TRADE|NO_TRADE", 0),
        "admissions": {
            "baseline": len(admitted[ComparisonArm.BASELINE]),
            "zone": len(admitted[ComparisonArm.ZONE]),
            "common": len(admitted[ComparisonArm.BASELINE] & admitted[ComparisonArm.ZONE]),
            "baseline_only": len(admitted[ComparisonArm.BASELINE] - admitted[ComparisonArm.ZONE]),
            "zone_only": len(admitted[ComparisonArm.ZONE] - admitted[ComparisonArm.BASELINE]),
            "union": len(admission_union),
            "membership": {
                "common": sorted(
                    _opportunity_id(key)
                    for key in admitted[ComparisonArm.BASELINE] & admitted[ComparisonArm.ZONE]
                ),
                "baseline_only": sorted(
                    _opportunity_id(key)
                    for key in admitted[ComparisonArm.BASELINE] - admitted[ComparisonArm.ZONE]
                ),
                "zone_only": sorted(
                    _opportunity_id(key)
                    for key in admitted[ComparisonArm.ZONE] - admitted[ComparisonArm.BASELINE]
                ),
            },
        },
        "masks": {
            "baseline": sorted(_opportunity_id(key) for key in population.baseline_mask),
            "zone": sorted(_opportunity_id(key) for key in population.zone_mask),
            "intersection": sorted(
                _opportunity_id(key)
                for key in set(population.baseline_mask) & set(population.zone_mask)
            ),
        },
        "admission_outcome_partitions": outcome_partitions,
        "full": {
            f"{arm.value}:{scenario.value}": metrics[arm, scenario, False].project()
            for arm in ComparisonArm
            for scenario in ComparisonScenario
        },
        "masked": {
            f"{arm.value}:{scenario.value}": metrics[arm, scenario, True].project()
            for arm in ComparisonArm
            for scenario in ComparisonScenario
        },
        "grouped_zero_complete": _grouped_projection(decisions, statuses),
        "geometry_and_recovery_diagnostics": diagnostics,
        "economic_classification": None,
    }
    rebuilt = _canonical(report)
    if rebuilt != report_payload:
        raise BoundedRecoveryError("streamed task report differs from retained authority")
    tracker.sample()
    return report_payload


def _projection_bytes(base: dict[str, object], reports: tuple[bytes, ...]) -> bytes:
    if "task_reports" in base:
        raise BoundedRecoveryError("projection base already contains task reports")
    keys = sorted((*base, "task_reports"))
    fields: list[bytes] = []
    for key in keys:
        value = b"[" + b",".join(reports) + b"]" if key == "task_reports" else _canonical(base[key])
        fields.append(_canonical(key) + b":" + value)
    return b"{" + b",".join(fields) + b"}"


def _run_from_state(state: HistoryScreenedLedgerState) -> HistoryScreenedRunAssociation:
    run = HistoryScreenedRunAssociation(
        state.input_kind,
        state.run_uuid,
        state.executable_sha,
        state.protocol_sha256,
        state.task_plan_sha256,
        state.source_id,
        state.dataset_version,
        state.manifest_sha256,
        state.input_report_sha256,
        state.input_plan_sha256,
    )
    run.validate(ACCEPTED_BINDING, actual_sha=ORIGINAL_EXECUTABLE_SHA)
    return run


def validate_source_index(evidence_root: Path) -> dict[str, object]:
    index_path = evidence_root / "artifact-index.json"
    if _sha_file(index_path) != SOURCE_INDEX_SHA256:
        raise BoundedRecoveryError("source artifact index changed")
    index = json.loads(index_path.read_bytes())
    if (
        not isinstance(index, dict)
        or index.get("file_count") != 113
        or not isinstance(index.get("files"), list)
        or len(index["files"]) != 113
    ):
        raise BoundedRecoveryError("source artifact index is malformed")
    root = evidence_root.resolve(strict=True)
    for record in index["files"]:
        if not isinstance(record, dict):
            raise BoundedRecoveryError("source artifact record is malformed")
        path = (evidence_root / str(record.get("path"))).resolve(strict=True)
        if (
            root not in path.parents
            or not path.is_file()
            or path.stat().st_size != record.get("size")
            or _sha_file(path) != record.get("sha256")
        ):
            raise BoundedRecoveryError(f"source artifact changed: {path.name}")
    if (
        _sha_file(evidence_root / "recovery" / "original-inventory.json") != SOURCE_INVENTORY_SHA256
        or _sha_file(evidence_root / "recovery" / "recovery-status-attempt-03.json")
        != BLOCKED_STATUS_SHA256
    ):
        raise BoundedRecoveryError("accepted recovery lineage identity changed")
    return cast(dict[str, object], index)


def prepare_read_only_mirror(evidence_root: Path, mirror: Path) -> dict[str, object]:
    if mirror.name != ORIGINAL_RUN_UUID:
        raise BoundedRecoveryError("read-only mirror basename differs from original UUID")
    source = evidence_root / "original-ledger"
    expected = sorted(path for path in source.iterdir() if path.is_file())
    if mirror.exists():
        actual = sorted(path for path in mirror.iterdir() if path.is_file())
        if [path.name for path in actual] != [path.name for path in expected]:
            raise BoundedRecoveryError("existing read-only mirror inventory changed")
    else:
        mirror.mkdir(parents=True, exist_ok=False)
        for path in expected:
            target = mirror / path.name
            with path.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            target.chmod(stat.S_IREAD)
        _fsync_directory(mirror)
    mapping: list[dict[str, object]] = []
    for path in expected:
        target = mirror / path.name
        if target.stat().st_size != path.stat().st_size or _sha_file(target) != _sha_file(path):
            raise BoundedRecoveryError("read-only mirror bytes differ from source")
        mapping.append({"name": path.name, "size": path.stat().st_size, "sha256": _sha_file(path)})
    return {"source": str(source), "mirror": str(mirror), "files": mapping}


def validate_ledger(
    mirror: Path,
) -> tuple[HistoryScreenedLedgerState, HistoryScreenedRunAssociation]:
    state = HistoryScreenedLedgerStore(mirror).load()
    if (
        state.run_uuid != ORIGINAL_RUN_UUID
        or state.executable_sha != ORIGINAL_EXECUTABLE_SHA
        or state.revision != 20
        or state.task_states != ("COMPLETED",) * 10
        or state.projection_sha256 is not None
        or state.task_ids != tuple(task.task_id for task in TASKS)
    ):
        raise BoundedRecoveryError("original ledger state differs from accepted revision 20")
    if _sha_file(mirror / "0020.json") != ORIGINAL_REVISION_SHA256:
        raise BoundedRecoveryError("revision-20 event changed")
    return state, _run_from_state(state)


def reconstruct(
    mirror: Path,
    *,
    tracker: ResourceTracker,
) -> tuple[bytes, dict[str, object]]:
    _state, run = validate_ledger(mirror)
    panel = _PanelReducer()
    report_payloads: list[bytes] = []
    for task in TASKS:
        population = _read_population(
            mirror / f"{task.index:02d}.population.json.gz", task, tracker
        )
        panel.register_population(population)
        report_payloads.append(
            _task_report_from_stream(mirror, task, population, run, panel, tracker)
        )
        del population
        tracker.sample()
    base = panel.project(run)
    payload = _projection_bytes(base, tuple(report_payloads))
    return payload, base


def build(
    evidence_root: Path,
    mirror: Path,
    output_directory: Path,
    *,
    recovery_executable_sha: str,
) -> dict[str, object]:
    if output_directory.exists():
        raise BoundedRecoveryError("successor output directory already exists")
    if len(recovery_executable_sha) != 40:
        raise BoundedRecoveryError("recovery executable SHA is malformed")
    index = validate_source_index(evidence_root)
    mapping = prepare_read_only_mirror(evidence_root, mirror)
    if shutil.disk_usage(output_directory.parent).free < WORKING_SET_BUDGET_BYTES:
        raise BoundedRecoveryError("successor output filesystem lacks declared headroom")
    output_directory.mkdir(parents=True, exist_ok=False)
    tracker = ResourceTracker()
    tracker.start()
    try:
        projection, _base = reconstruct(mirror, tracker=tracker)
        projection_sha = _write_once(output_directory / "projection.json", projection)
    finally:
        tracker.stop()
    attestation = {
        "schema": RECOVERY_SCHEMA,
        "protocol": RECOVERY_PROTOCOL,
        "recovery_executable_sha": recovery_executable_sha,
        "original_executable_sha": ORIGINAL_EXECUTABLE_SHA,
        "run_uuid": ORIGINAL_RUN_UUID,
        "original_attempt_02_exit": "UNKNOWN_NOT_RECORDED",
        "original_ledger_revision": 20,
        "original_finalized": False,
        "historical_invocations": 2,
        "source_artifact_index_sha256": SOURCE_INDEX_SHA256,
        "source_inventory_sha256": SOURCE_INVENTORY_SHA256,
        "blocked_status_sha256": BLOCKED_STATUS_SHA256,
        "source_index_file_count": index["file_count"],
        "source_to_mirror": mapping,
        "projection_sha256": projection_sha,
        "projection_size": len(projection),
        "working_set_budget_bytes": tracker.budget_bytes,
        "peak_working_set_bytes": tracker.peak_working_set_bytes,
        "peak_tracemalloc_bytes": tracker.peak_tracemalloc_bytes,
        "resource_samples": tracker.samples,
        "status": "RECOVERED_TASK_EVIDENCE_BUILT_ORIGINAL_EXIT_UNKNOWN",
    }
    attestation_payload = _canonical(attestation)
    attestation_sha = _write_once(output_directory / "attestation.json", attestation_payload)
    return {
        "status": attestation["status"],
        "projection_sha256": projection_sha,
        "projection_size": len(projection),
        "attestation_sha256": attestation_sha,
        "peak_working_set_bytes": tracker.peak_working_set_bytes,
    }


def verify(
    evidence_root: Path,
    mirror: Path,
    output_directory: Path,
    *,
    recovery_executable_sha: str,
) -> dict[str, object]:
    validate_source_index(evidence_root)
    prepare_read_only_mirror(evidence_root, mirror)
    projection_path = output_directory / "projection.json"
    attestation_path = output_directory / "attestation.json"
    stored_projection_sha = _sha_file(projection_path)
    attestation = json.loads(attestation_path.read_bytes())
    if (
        not isinstance(attestation, dict)
        or attestation.get("recovery_executable_sha") != recovery_executable_sha
        or attestation.get("projection_sha256") != stored_projection_sha
        or attestation.get("original_attempt_02_exit") != "UNKNOWN_NOT_RECORDED"
    ):
        raise BoundedRecoveryError("recovery attestation differs from output/source")
    tracker = ResourceTracker()
    tracker.start()
    try:
        rebuilt, base = reconstruct(mirror, tracker=tracker)
    finally:
        tracker.stop()
    if _sha_bytes(rebuilt) != stored_projection_sha or rebuilt != projection_path.read_bytes():
        raise BoundedRecoveryError("fresh reconstruction differs from recovered projection")
    return {
        "status": "RECOVERED_TASK_EVIDENCE_VERIFIED_ORIGINAL_EXIT_UNKNOWN",
        "projection_sha256": stored_projection_sha,
        "projection_size": len(rebuilt),
        "classifications": base["classifications"],
        "peak_working_set_bytes": tracker.peak_working_set_bytes,
        "working_set_budget_bytes": tracker.budget_bytes,
    }


def source_identity() -> dict[str, object]:
    return {
        "protocol": RECOVERY_PROTOCOL,
        "original_executable_sha": ORIGINAL_EXECUTABLE_SHA,
        "run_uuid": ORIGINAL_RUN_UUID,
        "source_artifact_index_sha256": SOURCE_INDEX_SHA256,
        "source_inventory_sha256": SOURCE_INVENTORY_SHA256,
        "blocked_status_sha256": BLOCKED_STATUS_SHA256,
        "working_set_budget_bytes": WORKING_SET_BUDGET_BYTES,
        "python": sys.version,
    }
