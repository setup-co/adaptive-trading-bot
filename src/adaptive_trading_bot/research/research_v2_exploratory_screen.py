"""Predeclared, research-only exploratory screen; no implicit execution grant.

Planning and preflight inspect locked data only. The actual study path requires a
separate external grant for the reviewed executable SHA; this module provides no
historical API, account, or runtime trading path.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from adaptive_trading_bot.backtest import ExecutionCostConfiguration, ExecutionSeries
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.regime.technical import RegimeLabel
from adaptive_trading_bot.research import research_v2_corpus_tail as tail
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillFamily,
    AllFillReplayResult,
    AllFillScenario,
    AllFillStatus,
    ResearchAllFillReplayRunner,
    _closed_distribution,
)
from adaptive_trading_bot.research.research_v2_admission import (
    ACCEPTED_C2_BINDING,
    ADMISSION_VERSION,
    C1_MANIFEST_SHA256,
    DATASET_VERSION,
    MANIFEST_SHA256,
    ORIGINAL_MANIFEST_SHA256,
    PANEL,
    PLAN_SHA256,
    PROFILE,
    REPORT_SHA256,
    ROOT,
    SELECTION_MANIFEST_SHA256,
    SELECTION_SHA256,
    SOURCE_ID,
    SOURCE_NOTE_SHA256,
    LockedResearchV2CandleStore,
    ResearchV2AdmissionConfiguration,
    ResearchV2DecisionPopulation,
    ResearchV2EpochBounds,
    ResearchV2StudyDecisionPopulation,
    _evaluate_granted_study_epoch,
    _validate_frozen_study_configuration,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    EVIDENCE_SCHEMA,
    ScreenEvidenceError,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    decode as decode_authority,
)
from adaptive_trading_bot.research.research_v2_screen_evidence import (
    encode as encode_authority,
)

PROTOCOL_VERSION = "RESEARCH_V2_EXPLORATORY_SCREEN_V1"
SCHEMA_VERSION = "research-v2-exploratory-screen-v4"
FOLLOW_UP = timedelta(hours=168)
RESEARCH_EQUITY = Decimal("10000")
FAMILIES = ("trend_pullback", "breakout_retest", "range_mean_reversion")
BLOCKS = (
    ("P1", datetime(2026, 8, 3, tzinfo=UTC), datetime(2026, 8, 24, tzinfo=UTC)),
    ("P2", datetime(2026, 8, 24, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC)),
    ("P3", datetime(2026, 9, 14, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)),
)
DECISION_START = BLOCKS[0][1]
DECISION_END = BLOCKS[-1][2]
FOLLOW_UP_END = DECISION_END + FOLLOW_UP
CONTEXT_START = {
    "BTCUSDT": datetime(2026, 7, 21, tzinfo=UTC),
    "BTWUSDT": datetime(2026, 7, 25, 12, tzinfo=UTC),
}
DEFAULT_CONTEXT_START = datetime(2026, 7, 16, tzinfo=UTC)
BASE_COSTS = ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10), timedelta())
STRESS_COSTS = ExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(20), timedelta(seconds=1))
SCENARIOS = (
    (AllFillScenario.BASE, BASE_COSTS),
    (AllFillScenario.COMBINED_STRESS, STRESS_COSTS),
)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class ExploratoryScreenError(ValueError):
    """The frozen exploratory protocol or submitted evidence failed closed."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _iso(value: datetime) -> str:
    return require_utc(value, field="study time").isoformat().replace("+00:00", "Z")


def protocol_facts() -> dict[str, object]:
    """Single hashable declaration; never includes a result or executable code SHA."""

    return {
        "version": PROTOCOL_VERSION,
        "schema": SCHEMA_VERSION,
        "authority_evidence_schema": EVIDENCE_SCHEMA,
        "input": {
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "root": ROOT.as_posix(),
            "manifest_sha256": MANIFEST_SHA256,
            "plan_sha256": PLAN_SHA256,
            "report_sha256": REPORT_SHA256,
            "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
            "c1_manifest_sha256": C1_MANIFEST_SHA256,
            "selection_manifest_sha256": SELECTION_MANIFEST_SHA256,
            "selection_result_sha256": SELECTION_SHA256,
            "source_note_sha256": SOURCE_NOTE_SHA256,
            "binding": ACCEPTED_C2_BINDING.schema_version,
            "admission_version": ADMISSION_VERSION,
            "profile": PROFILE,
        },
        "panel": PANEL,
        "families": (
            ("trend_pullback", "1.0.0", "EMA50_PRIMARY_HOLD_ONLY_DIAGNOSTIC"),
            ("breakout_retest", "1.0.0", "BREAKOUT_RETEST_FIXED_2R_V1"),
            ("range_mean_reversion", "2.0.0", "RANGE_MEAN_REVERSION_ENTRY_MIDPOINT_V1"),
        ),
        "blocks": tuple((name, _iso(start), _iso(end)) for name, start, end in BLOCKS),
        "decision_observation_rule": "signal_open_in_[a,b);decision_close_in_(a,b]",
        "first_decision_at": _iso(DECISION_START + Timeframe.MINUTES_15.duration),
        "last_decision_at": _iso(DECISION_END),
        "context_starts": tuple(
            (wire, _iso(CONTEXT_START.get(wire, DEFAULT_CONTEXT_START))) for wire in PANEL
        ),
        "follow_up_hours_per_decision": 168,
        "last_follow_up_end_exclusive": _iso(FOLLOW_UP_END),
        "research_equity_usdt": str(RESEARCH_EQUITY),
        "carried_exposure": "ZERO_PER_INDEPENDENT_CANDIDATE",
        "costs": (("BASE", "2", "2", "10", "0"), ("COMBINED_STRESS", "4", "4", "20", "1")),
        "scenario_population": "IDENTICAL_DECISIONS_AND_ADMISSIONS",
        "evidence": (
            "ONE_VALIDATED_POPULATION_PER_TASK",
            "ONE_RESULT_PER_DECISION_AND_SCENARIO",
            "DECISION_AND_CANDLE_SLICE_REFERENCES",
            "OFFLINE_RECONSTRUCTION_AGAINST_COMPACT_ROWS",
            "SYNTHETIC_INPUT_NEVER_ACCEPTED_AS_C2",
        ),
        "week_count": 9,
        "slots_per_asset_family_block": 2016,
        "cooldown_hours": 168,
        "sufficiency": (60, 30, 5, 3, 6, 3, "NO_PRIMARY_UNRESOLVED"),
        "initial_asset_week_sufficiency": "EACH_SCENARIO_SEPARATELY",
        "residual_base_quantity": "PER_ASSET_ONLY",
        "interpretation": "EXPLORATORY_SELECTION_INFORMED_UNLEVERAGED_PRE_FUNDING_NOT_PORTFOLIO",
    }


PROTOCOL_SHA256 = _digest(protocol_facts())


@dataclass(frozen=True, slots=True)
class StudyTask:
    index: int
    wire_symbol: str
    family: str
    context_start: datetime
    decision_start: datetime = DECISION_START
    decision_end: datetime = DECISION_END
    last_follow_up_end: datetime = FOLLOW_UP_END

    @property
    def task_id(self) -> str:
        return f"{self.index:02d}-{self.wire_symbol}-{self.family}"

    def to_record(self) -> dict[str, object]:
        return {
            "index": self.index,
            "task_id": self.task_id,
            "symbol": self.wire_symbol,
            "family": self.family,
            "context_start": _iso(self.context_start),
            "decision_start": _iso(self.decision_start),
            "decision_end": _iso(self.decision_end),
            "last_follow_up_end": _iso(self.last_follow_up_end),
            "decision_slots": 6048,
            "scenario_rows": 12096,
        }


def frozen_tasks() -> tuple[StudyTask, ...]:
    tasks = tuple(
        StudyTask(
            len(FAMILIES) * symbol_index + family_index,
            wire,
            family,
            CONTEXT_START.get(wire, DEFAULT_CONTEXT_START),
        )
        for symbol_index, wire in enumerate(PANEL)
        for family_index, family in enumerate(FAMILIES)
    )
    if len(tasks) != 30 or len({task.task_id for task in tasks}) != 30:
        raise ExploratoryScreenError("frozen task inventory is contradictory")
    return tasks


TASK_PLAN_SHA256 = _digest(tuple(task.to_record() for task in frozen_tasks()))


def observation_bin(observed_at: datetime) -> tuple[str, str]:
    """Attribute a completed decision by its 15m signal-candle open."""

    opened = require_utc(observed_at, field="decision observed_at") - Timeframe.MINUTES_15.duration
    for block_index, (name, start, end) in enumerate(BLOCKS):
        if start <= opened < end:
            week = block_index * 3 + (opened - start).days // 7 + 1
            return name, f"W{week}"
    raise ExploratoryScreenError("decision is outside the nine frozen weeks")


def decision_cutoff(observed_at: datetime) -> datetime:
    observation_bin(observed_at)
    return observed_at + FOLLOW_UP


def causal_follow_up_slice(observed_times: tuple[datetime, ...], observed_at: datetime) -> slice:
    """Only later closed observations strictly before this decision's own cutoff."""

    cutoff = decision_cutoff(observed_at)
    begin = bisect_right(observed_times, observed_at)
    finish = bisect_left(observed_times, cutoff)
    if begin > finish:
        raise ExploratoryScreenError("follow-up context is not chronological")
    return slice(begin, finish)


def data_only_plan(repo: Path) -> dict[str, object]:
    """Verify C2 artifacts/epochs/frames and calendar without strategy evaluation."""

    repo = repo.resolve(strict=True)
    checked = tail.verify(repo, repo / ROOT)
    if (
        checked["manifest_sha256"] != MANIFEST_SHA256
        or checked["status"] != "PARTIAL_OR_BLOCKED"
        or checked["cumulative"] != {"attempts": 2704, "distinct": 2704, "retries": 0}
    ):
        raise ExploratoryScreenError("accepted C2 raw-to-derived verification differs")
    store = LockedResearchV2CandleStore(repo)
    records: list[dict[str, object]] = []
    for wire in PANEL:
        symbol = TradingSymbol(wire.removesuffix("USDT"), "USDT")
        start = CONTEXT_START.get(wire, DEFAULT_CONTEXT_START)
        bounds = ResearchV2EpochBounds(symbol, start, DECISION_START, DECISION_END, FOLLOW_UP_END)
        bounds.validate(store)
        frame_counts: dict[str, int] = {}
        for frame in (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4):
            candles = store.read(SOURCE_ID, symbol, frame, start=start, end=FOLLOW_UP_END)
            expected = int((FOLLOW_UP_END - start) / frame.duration)
            if len(candles) != expected or any(
                candle.open_time != start + index * frame.duration
                for index, candle in enumerate(candles)
            ):
                raise ExploratoryScreenError("locked source lacks exact task context coverage")
            frame_counts[frame.value] = len(candles)
        records.append(
            {
                "symbol": wire,
                "context_start": _iso(start),
                "last_follow_up_end": _iso(FOLLOW_UP_END),
                "frame_counts": frame_counts,
                "usable_intervals": tuple(
                    (_iso(a), _iso(b)) for a, b in store.usable_intervals(symbol)
                ),
                "epochs": tuple((_iso(a), _iso(b)) for a, b in store.data_only_epochs(symbol)),
                "decision_slots_per_family": 6048,
            }
        )
    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "DATA_ONLY_PREFLIGHT; REAL_STUDY_NOT_GRANTED",
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "input": protocol_facts()["input"],
        "tasks": tuple(task.to_record() for task in frozen_tasks()),
        "calendar": tuple(
            {
                "block": name,
                "open_start": _iso(start),
                "open_end": _iso(end),
                "slots_per_asset_family": 2016,
            }
            for name, start, end in BLOCKS
        ),
        "members": tuple(records),
        "total_observation_slots": 181440,
        "total_scenario_rows": 362880,
    }
    result["plan_sha256"] = _digest(result)
    return result


@dataclass(frozen=True, slots=True)
class StudyExecutionGrant:
    """External, exact-SHA authorization; no real grant ships with this code."""

    kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    task_plan_sha256: str
    source_id: str
    dataset_version: str
    manifest_sha256: str

    def validate(self, *, actual_sha: str, synthetic: bool = False) -> None:
        try:
            stable = str(uuid.UUID(self.run_uuid)) == self.run_uuid
        except (TypeError, ValueError):
            stable = False
        expected_kind = "SYNTHETIC_TEST_GRANT" if synthetic else "EXTERNAL_REVIEWED_EXECUTION_GRANT"
        if (
            self.kind != expected_kind
            or not stable
            or not _HEX40.fullmatch(self.executable_sha)
            or self.executable_sha != actual_sha
            or self.protocol_sha256 != PROTOCOL_SHA256
            or self.task_plan_sha256 != TASK_PLAN_SHA256
            or (
                not synthetic
                and (self.source_id, self.dataset_version, self.manifest_sha256)
                != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
            )
            or (
                synthetic
                and (self.source_id == SOURCE_ID.value or self.manifest_sha256 == MANIFEST_SHA256)
            )
        ):
            raise ExploratoryScreenError(
                "external study grant does not bind reviewed code/protocol/input"
            )

    @classmethod
    def from_file(cls, path: Path) -> StudyExecutionGrant:
        if not path.is_file():
            raise ExploratoryScreenError("external execution grant is absent")
        try:
            value: Any = json.loads(path.read_bytes())
            if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
                raise ValueError("grant fields differ")
            return cls(**value)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ExploratoryScreenError("external execution grant is malformed") from exc


class ScreenClassification(StrEnum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEGATIVE_EVIDENCE = "NEGATIVE_EVIDENCE"
    CANDIDATE_FOR_FURTHER_VALIDATION = "CANDIDATE_FOR_FURTHER_VALIDATION"
    TECHNICALLY_VALID_MIXED_EVIDENCE = "TECHNICALLY_VALID_MIXED_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ScreenObservation:
    """Compact validated primary-branch facts; one row per scenario/decision."""

    identity: str
    observed_at: datetime
    wire_symbol: str
    family: AllFillFamily
    scenario: AllFillScenario
    status: AllFillStatus
    research_admissible: bool
    primary_closed_net_r: Decimal | None
    primary_fee_quote: Decimal
    primary_open_quantity: Decimal
    primary_gross_pnl_quote: Decimal | None
    primary_net_pnl_quote: Decimal | None
    side: str
    regime: str | None
    result_sha256: str
    hold_only_status: AllFillStatus | None = None
    hold_only_closed_net_r: Decimal | None = None
    hold_only_fee_quote: Decimal | None = None
    hold_only_open_quantity: Decimal | None = None
    hold_only_branch_sha256: str | None = None
    input_kind: str = ACCEPTED_C2_BINDING.kind
    source_id: str = SOURCE_ID.value
    dataset_version: str = DATASET_VERSION
    manifest_sha256: str = MANIFEST_SHA256

    def validate(self) -> None:
        observation_bin(self.observed_at)
        if (
            not self.identity
            or self.wire_symbol not in PANEL
            or self.observed_at
            != self.observed_at.replace(
                minute=(self.observed_at.minute // 15) * 15, second=0, microsecond=0
            )
            or not isinstance(self.family, AllFillFamily)
            or not isinstance(self.scenario, AllFillScenario)
            or not isinstance(self.status, AllFillStatus)
            or self.side not in {"NONE", "LONG", "SHORT"}
            or (self.regime is not None and self.regime not in {item.value for item in RegimeLabel})
            or not _HEX64.fullmatch(self.result_sha256)
            or self.input_kind not in {"ACCEPTED_C2", "SYNTHETIC_FIXTURE"}
            or not self.source_id
            or not self.dataset_version
            or not _HEX64.fullmatch(self.manifest_sha256)
            or (
                self.input_kind == "ACCEPTED_C2"
                and (self.source_id, self.dataset_version, self.manifest_sha256)
                != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
            )
            or (
                self.input_kind == "SYNTHETIC_FIXTURE"
                and (self.source_id == SOURCE_ID.value or self.manifest_sha256 == MANIFEST_SHA256)
            )
            or not self.primary_fee_quote.is_finite()
            or self.primary_fee_quote < 0
            or not self.primary_open_quantity.is_finite()
            or self.primary_open_quantity < 0
            or any(
                value is not None and not value.is_finite()
                for value in (
                    self.primary_closed_net_r,
                    self.primary_gross_pnl_quote,
                    self.primary_net_pnl_quote,
                )
            )
        ):
            raise ExploratoryScreenError("screen row is malformed")
        closed = self.status in {AllFillStatus.ORDINARY_CLOSED, AllFillStatus.RECOVERY_CLOSED}
        open_status = self.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
        if (
            closed != (self.primary_closed_net_r is not None)
            or (open_status and self.primary_open_quantity <= 0)
            or (not open_status and self.primary_open_quantity != 0)
            or ((self.primary_gross_pnl_quote is None) != (self.primary_net_pnl_quote is None))
            or (
                self.research_admissible
                != (
                    self.status
                    not in {
                        AllFillStatus.NO_SIGNAL,
                        AllFillStatus.RISK_BLOCKED,
                        AllFillStatus.SAFETY_BLOCKED,
                    }
                )
            )
            or (self.status is AllFillStatus.NO_SIGNAL and self.side != "NONE")
            or (self.status is not AllFillStatus.NO_SIGNAL and self.side == "NONE")
        ):
            raise ExploratoryScreenError("screen row contradicts primary outcome")
        hold = self.hold_only_status
        if hold is None:
            if any(
                value is not None
                for value in (
                    self.hold_only_closed_net_r,
                    self.hold_only_fee_quote,
                    self.hold_only_open_quantity,
                    self.hold_only_branch_sha256,
                )
            ):
                raise ExploratoryScreenError("hold-only diagnostic is incomplete")
        elif (
            self.family is not AllFillFamily.TREND_PULLBACK
            or self.status
            in {
                AllFillStatus.NO_SIGNAL,
                AllFillStatus.RISK_BLOCKED,
                AllFillStatus.SAFETY_BLOCKED,
                AllFillStatus.ENTRY_UNFILLED,
            }
            or hold
            not in {
                AllFillStatus.ORDINARY_CLOSED,
                AllFillStatus.ORDINARY_OPEN,
                AllFillStatus.RECOVERY_CLOSED,
                AllFillStatus.RECOVERY_OPEN,
            }
            or self.hold_only_fee_quote is None
            or not self.hold_only_fee_quote.is_finite()
            or self.hold_only_fee_quote < 0
            or self.hold_only_open_quantity is None
            or not self.hold_only_open_quantity.is_finite()
            or self.hold_only_open_quantity < 0
            or self.hold_only_branch_sha256 is None
            or not _HEX64.fullmatch(self.hold_only_branch_sha256)
            or (hold in {AllFillStatus.ORDINARY_CLOSED, AllFillStatus.RECOVERY_CLOSED})
            != (self.hold_only_closed_net_r is not None)
            or (
                self.hold_only_closed_net_r is not None
                and not self.hold_only_closed_net_r.is_finite()
            )
            or (hold in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN})
            != (self.hold_only_open_quantity > 0)
        ):
            raise ExploratoryScreenError("hold-only diagnostic contradicts paired branch")


def horizon_mask(rows: tuple[ScreenObservation, ...]) -> tuple[str, ...]:
    """Pre-outcome earliest-admissible mask, per asset/family across all blocks."""

    last_selected: dict[tuple[str, AllFillFamily], datetime] = {}
    last_seen: dict[tuple[str, AllFillFamily], datetime] = {}
    selected: list[str] = []
    seen: set[tuple[str, AllFillFamily, str]] = set()
    for row in rows:
        row.validate()
        key = (row.wire_symbol, row.family, row.identity)
        if key in seen:
            raise ExploratoryScreenError("duplicate decision in mask input")
        seen.add(key)
        if not row.research_admissible:
            continue
        group = (row.wire_symbol, row.family)
        seen_at = last_seen.get(group)
        if seen_at is not None and row.observed_at <= seen_at:
            raise ExploratoryScreenError("admissions are not strictly chronological")
        last_seen[group] = row.observed_at
        prior = last_selected.get(group)
        if prior is None or row.observed_at >= prior + FOLLOW_UP:
            selected.append(row.identity)
            last_selected[group] = row.observed_at
    return tuple(selected)


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    return _closed_distribution(values)[0]


def _positive_mean(values: tuple[Decimal, ...]) -> bool:
    mean = _mean(values)
    return mean is not None and mean > 0


def _primary_values(rows: tuple[ScreenObservation, ...]) -> tuple[Decimal, ...]:
    return tuple(row.primary_closed_net_r for row in rows if row.primary_closed_net_r is not None)


def classify_family(
    base: tuple[ScreenObservation, ...],
    stress: tuple[ScreenObservation, ...],
    family: AllFillFamily,
) -> tuple[ScreenClassification, dict[str, object]]:
    """Frozen exploratory rules; incomplete or mixed decision graphs fail closed."""

    for rows, scenario in ((base, AllFillScenario.BASE), (stress, AllFillScenario.COMBINED_STRESS)):
        if any(row.family is not family or row.scenario is not scenario for row in rows):
            raise ExploratoryScreenError("family or scenario differs from declared screen")
        if len({row.identity for row in rows}) != len(rows):
            raise ExploratoryScreenError("duplicate decision in screen population")
    if tuple(row.identity for row in base) != tuple(row.identity for row in stress):
        raise ExploratoryScreenError("BASE and STRESS decision populations differ")
    if any(
        (left.wire_symbol, left.observed_at, left.research_admissible)
        != (right.wire_symbol, right.observed_at, right.research_admissible)
        for left, right in zip(base, stress, strict=True)
    ):
        raise ExploratoryScreenError("execution scenario changed decision/admission authority")
    selected = frozenset(horizon_mask(base))
    if selected != frozenset(horizon_mask(stress)):
        raise ExploratoryScreenError("execution outcome changed cooldown mask")
    full = (base, stress)
    masks = tuple(tuple(row for row in rows if row.identity in selected) for rows in full)
    closed = tuple(_primary_values(rows) for rows in full)
    selected_closed = tuple(_primary_values(rows) for rows in masks)
    means = tuple(_mean(values) for values in (*closed, *selected_closed))
    unresolved = any(
        row.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
        for rows in full
        for row in rows
    )
    assets_by_scenario = tuple(
        tuple(
            wire
            for wire in PANEL
            if sum(row.wire_symbol == wire and row.primary_closed_net_r is not None for row in rows)
            >= 3
        )
        for rows in masks
    )
    weeks_by_scenario = tuple(
        tuple(
            f"W{week}"
            for week in range(1, 10)
            if sum(
                observation_bin(row.observed_at)[1] == f"W{week}"
                and row.primary_closed_net_r is not None
                for row in rows
            )
            >= 3
        )
        for rows in masks
    )
    assets = tuple(wire for wire in PANEL if all(wire in group for group in assets_by_scenario))
    weeks = tuple(
        f"W{week}"
        for week in range(1, 10)
        if all(f"W{week}" in group for group in weeks_by_scenario)
    )
    leave_one_out = tuple(
        (
            wire,
            _mean(
                tuple(
                    row.primary_closed_net_r
                    for row in stress
                    if row.wire_symbol != wire and row.primary_closed_net_r is not None
                )
            ),
            _mean(
                tuple(
                    row.primary_closed_net_r
                    for row in masks[1]
                    if row.wire_symbol != wire and row.primary_closed_net_r is not None
                )
            ),
        )
        for wire in PANEL
    )
    if (
        unresolved
        or any(
            len(values) < minimum
            for values, minimum in zip((*closed, *selected_closed), (60, 60, 30, 30), strict=True)
        )
        or any(len(group) < 5 for group in assets_by_scenario)
        or any(len(group) < 6 for group in weeks_by_scenario)
    ):
        classification = ScreenClassification.INSUFFICIENT_EVIDENCE
    elif all(value is not None and value <= 0 for value in means):
        classification = ScreenClassification.NEGATIVE_EVIDENCE
    else:
        positive_assets = sum(
            all(
                _positive_mean(
                    tuple(
                        row.primary_closed_net_r
                        for row in rows
                        if row.wire_symbol == wire and row.primary_closed_net_r is not None
                    )
                )
                for rows in masks
            )
            for wire in assets
        )
        positive_weeks = sum(
            _positive_mean(
                tuple(
                    row.primary_closed_net_r
                    for row in masks[1]
                    if observation_bin(row.observed_at)[1] == f"W{week}"
                    and row.primary_closed_net_r is not None
                )
            )
            and sum(
                observation_bin(row.observed_at)[1] == f"W{week}"
                and row.primary_closed_net_r is not None
                for row in masks[1]
            )
            >= 3
            for week in range(1, 10)
        )
        classification = (
            ScreenClassification.CANDIDATE_FOR_FURTHER_VALIDATION
            if all(value is not None and value > 0 for value in means)
            and positive_assets >= 5
            and positive_weeks >= 6
            and all(
                a is not None and b is not None and a > 0 and b > 0 for _, a, b in leave_one_out
            )
            else ScreenClassification.TECHNICALLY_VALID_MIXED_EVIDENCE
        )
    details: dict[str, object] = {
        "classification": classification.value,
        "full_closed": tuple(len(values) for values in closed),
        "mask_closed": tuple(len(values) for values in selected_closed),
        "selected_admissions": len(selected),
        "raw_admissions": sum(row.research_admissible for row in base),
        "same_asset_family_horizon_overlaps": sum(row.research_admissible for row in base)
        - len(selected),
        "cross_asset_weekly_selected_admissions": tuple(
            (
                f"W{week}",
                sum(
                    row.identity in selected and observation_bin(row.observed_at)[1] == f"W{week}"
                    for row in base
                ),
            )
            for week in range(1, 10)
        ),
        "mask_assets_with_three_closures_both": assets,
        "mask_weeks_with_three_closures_both": weeks,
        "mask_assets_with_three_closures_by_scenario": assets_by_scenario,
        "mask_weeks_with_three_closures_by_scenario": weeks_by_scenario,
        "means": tuple(None if value is None else str(value) for value in means),
        "stress_leave_one_asset_out_means": tuple(
            (wire, None if a is None else str(a), None if b is None else str(b))
            for wire, a, b in leave_one_out
        ),
        "unresolved_primary": unresolved,
    }
    return classification, details


def _screen_row(
    result: AllFillReplayResult,
    population: ResearchV2DecisionPopulation,
    decision: Any,
    *,
    synthetic: bool = False,
) -> ScreenObservation:
    """Copy accounting facts only after authoritative all-fill validation."""

    result.validate()
    binding = population.configuration.input_binding
    expected_costs = dict(SCENARIOS).get(result.scenario)
    expected_start = (
        population.configuration.bounds.context_start
        if population.configuration.family == "trend_pullback"
        else decision.identity.observed_at
    )
    if (
        (binding.kind != "SYNTHETIC_FIXTURE" if synthetic else binding != ACCEPTED_C2_BINDING)
        or expected_costs is None
        or result.costs != expected_costs
        or result.start != expected_start
        or result.end != decision_cutoff(decision.identity.observed_at)
        or result.series.source_id != binding.source_id
        or result.series.symbol != population.configuration.bounds.symbol
        or result.series.timeframe is not Timeframe.MINUTES_15
        or decision.identity.source_id != binding.source_id
        or decision.identity.symbol != population.configuration.bounds.symbol
        or result.family.value != population.configuration.family.upper()
        or result.decision != decision
    ):
        raise ExploratoryScreenError("all-fill result has incompatible study provenance")
    branch = result.branches[-1] if result.branches else None
    hold_only = result.branches[0] if len(result.branches) == 2 else None
    action = decision.strategy_evaluation.action.value
    side = (
        "LONG" if action == "LONG_CANDIDATE" else "SHORT" if action == "SHORT_CANDIDATE" else "NONE"
    )
    row = ScreenObservation(
        identity=_digest(str(decision.identity)),
        observed_at=decision.identity.observed_at,
        wire_symbol=f"{decision.identity.symbol.base}{decision.identity.symbol.quote}",
        family=result.family,
        scenario=result.scenario,
        status=result.status,
        research_admissible=result.entry_intent is not None,
        primary_closed_net_r=branch.closed_net_r if branch else None,
        primary_fee_quote=(branch.final_position.cumulative_fees if branch else Decimal(0)),
        primary_open_quantity=(abs(branch.final_position.quantity) if branch else Decimal(0)),
        primary_gross_pnl_quote=(
            branch.final_position.cumulative_gross_realized_pnl if branch else None
        ),
        primary_net_pnl_quote=(
            branch.final_position.cumulative_net_realized_pnl if branch else None
        ),
        side=side,
        regime=decision.context.multi_timeframe_regime.regime_15m.regime_label.value,
        result_sha256=result.integrity_seal,
        hold_only_status=hold_only.status if hold_only else None,
        hold_only_closed_net_r=hold_only.closed_net_r if hold_only else None,
        hold_only_fee_quote=(hold_only.final_position.cumulative_fees if hold_only else None),
        hold_only_open_quantity=(abs(hold_only.final_position.quantity) if hold_only else None),
        hold_only_branch_sha256=hold_only.integrity_seal if hold_only else None,
        input_kind=binding.kind,
        source_id=binding.source_id.value,
        dataset_version=binding.dataset_version,
        manifest_sha256=binding.manifest_sha256,
    )
    row.validate()
    return row


class _BoundTaskExecutionSource:
    """One checked task's 15m context candles; never an arbitrary named store."""

    def __init__(self, population: ResearchV2DecisionPopulation) -> None:
        self._series = ExecutionSeries(
            population.source_id,
            population.configuration.bounds.symbol,
            Timeframe.MINUTES_15,
        )
        self._candles = tuple(item.context.candle_15m for item in population.contexts)
        self._opens = tuple(candle.open_time for candle in self._candles)
        expected = population.configuration.bounds.context_start
        for candle in self._candles:
            if candle.open_time != expected:
                raise ExploratoryScreenError("task execution context has a missing candle")
            expected = candle.close_time
        if expected != population.configuration.bounds.follow_up_end:
            raise ExploratoryScreenError("task execution context lacks follow-up coverage")

    def load(
        self, series: ExecutionSeries, *, start: datetime, end: datetime
    ) -> tuple[Candle, ...]:
        if (
            series != self._series
            or start < self._opens[0]
            or end > self._candles[-1].close_time
            or start >= end
        ):
            raise ExploratoryScreenError("execution source differs from bound task")
        selected = self._candles[bisect_left(self._opens, start) : bisect_left(self._opens, end)]
        if not selected:
            raise ExploratoryScreenError("execution snapshot is unexpectedly empty")
        return selected


def execute_granted_task(
    store: LockedResearchV2CandleStore,
    task: StudyTask,
    grant: StudyExecutionGrant,
    *,
    actual_code_sha: str,
    result_sink: Callable[
        [ResearchV2DecisionPopulation, int, AllFillScenario, AllFillReplayResult], None
    ]
    | None = None,
) -> tuple[
    ResearchV2StudyDecisionPopulation, tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]
]:
    """Evaluate one asset/family once; grant check is before any strategy call."""

    grant.validate(actual_sha=actual_code_sha)
    if (
        type(store) is not LockedResearchV2CandleStore
        or not 0 <= task.index < 30
        or task != frozen_tasks()[task.index]
    ):
        raise ExploratoryScreenError("task or store differs from frozen accepted inventory")
    symbol = TradingSymbol(task.wire_symbol.removesuffix("USDT"), "USDT")
    bounds = ResearchV2EpochBounds(
        symbol, task.context_start, task.decision_start, task.decision_end, task.last_follow_up_end
    )
    configuration = ResearchV2AdmissionConfiguration(
        bounds, task.family, RESEARCH_EQUITY, input_binding=ACCEPTED_C2_BINDING
    )
    population = _evaluate_granted_study_epoch(
        store,
        configuration,
        protocol_sha256=PROTOCOL_SHA256,
        grant=grant,
        actual_code_sha=actual_code_sha,
    )
    if len(population.decisions) != 6048:
        raise ExploratoryScreenError("task omits one or more declared decisions")
    return cast(
        tuple[
            ResearchV2StudyDecisionPopulation,
            tuple[ScreenObservation, ...],
            tuple[ScreenObservation, ...],
        ],
        _execute_population(population, result_sink=result_sink),
    )


def execute_synthetic_task(
    population: ResearchV2DecisionPopulation,
    grant: StudyExecutionGrant,
    *,
    actual_code_sha: str,
    result_sink: Callable[
        [ResearchV2DecisionPopulation, int, AllFillScenario, AllFillReplayResult], None
    ]
    | None = None,
) -> tuple[
    ResearchV2DecisionPopulation, tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]
]:
    """Small fixture path through the same replay loop; never accepts C2 authority."""

    grant.validate(actual_sha=actual_code_sha, synthetic=True)
    population.validate()
    binding = population.configuration.input_binding
    if (
        type(population) is not ResearchV2DecisionPopulation
        or binding.kind != "SYNTHETIC_FIXTURE"
        or binding.source_id.value != grant.source_id
        or binding.dataset_version != grant.dataset_version
        or binding.manifest_sha256 != grant.manifest_sha256
        or not population.decisions
    ):
        raise ExploratoryScreenError("synthetic task cannot claim accepted C2 input")
    return _execute_population(population, result_sink=result_sink, synthetic=True)


def _execute_population(
    population: ResearchV2DecisionPopulation,
    *,
    result_sink: Callable[
        [ResearchV2DecisionPopulation, int, AllFillScenario, AllFillReplayResult], None
    ]
    | None = None,
    synthetic: bool = False,
) -> tuple[
    ResearchV2DecisionPopulation, tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]
]:
    source = _BoundTaskExecutionSource(population)
    runner = ResearchAllFillReplayRunner(source)
    observed_times = tuple(item.context.observed_at for item in population.contexts)
    series = ExecutionSeries(
        population.source_id, population.configuration.bounds.symbol, Timeframe.MINUTES_15
    )
    output: list[tuple[ScreenObservation, ...]] = []
    for scenario, costs in SCENARIOS:
        rows: list[ScreenObservation] = []
        for index, decision in enumerate(population.decisions):
            cutoff = decision_cutoff(decision.identity.observed_at)
            contexts = population.contexts[
                causal_follow_up_slice(observed_times, decision.identity.observed_at)
            ]
            result = runner.run(
                decision,
                contexts,
                series,
                costs,
                start=(
                    population.configuration.bounds.context_start
                    if population.configuration.family == "trend_pullback"
                    else decision.identity.observed_at
                ),
                end=cutoff,
            )
            if result.scenario is not scenario or result.end != cutoff:
                raise ExploratoryScreenError(
                    "all-fill result has wrong scenario or decision horizon"
                )
            rows.append(_screen_row(result, population, decision, synthetic=synthetic))
            if result_sink is not None:
                result_sink(population, index, scenario, result)
        output.append(tuple(rows))
    if tuple(row.identity for row in output[0]) != tuple(row.identity for row in output[1]):
        raise ExploratoryScreenError("execution scenario changed the decision population")
    return population, output[0], output[1]


def authority_result_record(
    population: ResearchV2DecisionPopulation,
    index: int,
    scenario: AllFillScenario,
    result: AllFillReplayResult,
    *,
    candles: tuple[Candle, ...] | None = None,
    candle_opens: tuple[datetime, ...] | None = None,
) -> dict[str, object]:
    """Store exact replay facts with one decision and candle-window reference."""

    if not 0 <= index < len(population.decisions) or result.decision != population.decisions[index]:
        raise ExploratoryScreenError("result decision reference differs from population")
    if result.scenario is not scenario:
        raise ExploratoryScreenError("result scenario differs from evidence slot")
    if candles is None:
        candles = tuple(item.context.candle_15m for item in population.contexts)
    if candle_opens is None:
        candle_opens = tuple(candle.open_time for candle in candles)
    snapshot = result.execution_snapshot
    if snapshot:
        first = bisect_left(candle_opens, snapshot[0].open_time)
        last = first + len(snapshot)
        if candles[first:last] != snapshot:
            raise ExploratoryScreenError("execution snapshot differs from stored population")
    else:
        first = last = 0
    named = tuple(
        (
            item.name,
            ("decision_ref",)
            if item.name == "decision"
            else ("candle_slice", first, last)
            if item.name == "execution_snapshot"
            else encode_authority(getattr(result, item.name)),
        )
        for item in fields(result)
    )
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "decision_index": index,
        "scenario": scenario.value,
        "result": ("dataclass", f"{type(result).__module__}.{type(result).__qualname__}", named),
    }


def recover_authority_result(
    record: object,
    population: ResearchV2DecisionPopulation,
    index: int,
    scenario: AllFillScenario,
    *,
    candles: tuple[Candle, ...] | None = None,
) -> AllFillReplayResult:
    """Reconstruct and validate replay/accounting from stored causal authority."""

    if (
        not isinstance(record, dict)
        or set(record) != {"schema_version", "decision_index", "scenario", "result"}
        or record.get("schema_version") != EVIDENCE_SCHEMA
        or record.get("decision_index") != index
        or record.get("scenario") != scenario.value
        or not 0 <= index < len(population.decisions)
    ):
        raise ExploratoryScreenError("replay evidence slot is missing or relabeled")
    try:
        if candles is None:
            candles = tuple(item.context.candle_15m for item in population.contexts)
        result = decode_authority(
            record["result"],
            decision=population.decisions[index],
            candles=candles,
        )
    except ScreenEvidenceError as exc:
        raise ExploratoryScreenError("stored replay evidence failed construction") from exc
    if not isinstance(result, AllFillReplayResult):
        raise ExploratoryScreenError("stored replay evidence is not a result")
    result.validate()
    return result


def _row_record(row: ScreenObservation) -> dict[str, object]:
    row.validate()
    block, week = observation_bin(row.observed_at)
    return {
        "identity": row.identity,
        "observed_at": _iso(row.observed_at),
        "symbol": row.wire_symbol,
        "family": row.family.value,
        "scenario": row.scenario.value,
        "status": row.status.value,
        "research_admissible": row.research_admissible,
        "primary_closed_net_r": (
            None if row.primary_closed_net_r is None else str(row.primary_closed_net_r)
        ),
        "primary_fee_quote": str(row.primary_fee_quote),
        "primary_open_quantity": str(row.primary_open_quantity),
        "primary_gross_pnl_quote": (
            None if row.primary_gross_pnl_quote is None else str(row.primary_gross_pnl_quote)
        ),
        "primary_net_pnl_quote": (
            None if row.primary_net_pnl_quote is None else str(row.primary_net_pnl_quote)
        ),
        "side": row.side,
        "regime": row.regime,
        "block": block,
        "week": week,
        "all_fill_result_sha256": row.result_sha256,
        "hold_only_status": row.hold_only_status.value if row.hold_only_status else None,
        "hold_only_closed_net_r": (
            None if row.hold_only_closed_net_r is None else str(row.hold_only_closed_net_r)
        ),
        "hold_only_fee_quote": (
            None if row.hold_only_fee_quote is None else str(row.hold_only_fee_quote)
        ),
        "hold_only_open_quantity": (
            None if row.hold_only_open_quantity is None else str(row.hold_only_open_quantity)
        ),
        "hold_only_branch_sha256": row.hold_only_branch_sha256,
        "input_kind": row.input_kind,
        "source_id": row.source_id,
        "dataset_version": row.dataset_version,
        "manifest_sha256": row.manifest_sha256,
    }


def _cell_summary(rows: tuple[ScreenObservation, ...]) -> dict[str, object]:
    values = _primary_values(rows)
    mean, median, factor = _closed_distribution(values)
    hold_values = tuple(
        row.hold_only_closed_net_r for row in rows if row.hold_only_closed_net_r is not None
    )
    hold_mean, hold_median, hold_factor = _closed_distribution(hold_values)
    with localcontext(_DECIMAL_CONTEXT):
        fees = sum((row.primary_fee_quote for row in rows), Decimal(0))
        gross = sum(
            (
                row.primary_gross_pnl_quote
                for row in rows
                if row.primary_gross_pnl_quote is not None
            ),
            Decimal(0),
        )
        net = sum(
            (row.primary_net_pnl_quote for row in rows if row.primary_net_pnl_quote is not None),
            Decimal(0),
        )
        total_r = sum(values, Decimal(0))
        hold_fees = sum(
            (row.hold_only_fee_quote for row in rows if row.hold_only_fee_quote is not None),
            Decimal(0),
        )
        exposure_totals = dict.fromkeys(PANEL, Decimal(0))
        hold_open_totals = dict.fromkeys(PANEL, Decimal(0))
        for row in rows:
            exposure_totals[row.wire_symbol] += row.primary_open_quantity
            if row.hold_only_open_quantity is not None:
                hold_open_totals[row.wire_symbol] += row.hold_only_open_quantity
        exposure_by_asset = {wire: str(value) for wire, value in exposure_totals.items()}
        hold_open_by_asset = {wire: str(value) for wire, value in hold_open_totals.items()}
    statuses = {status.value: sum(row.status is status for row in rows) for status in AllFillStatus}
    return {
        "observations": len(rows),
        "candidates": sum(row.side != "NONE" for row in rows),
        "research_admissible": sum(row.research_admissible for row in rows),
        "filled_primary": sum(
            row.status
            in {
                AllFillStatus.ORDINARY_CLOSED,
                AllFillStatus.ORDINARY_OPEN,
                AllFillStatus.RECOVERY_CLOSED,
                AllFillStatus.RECOVERY_OPEN,
            }
            for row in rows
        ),
        "closed_primary": len(values),
        "unresolved_primary": sum(
            row.status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN} for row in rows
        ),
        "recovery_primary": statuses[AllFillStatus.RECOVERY_CLOSED.value]
        + statuses[AllFillStatus.RECOVERY_OPEN.value],
        "status_counts": statuses,
        "incurred_fees_quote": str(fees),
        "remaining_absolute_base_quantity_by_asset": exposure_by_asset,
        "impact_inclusive_gross_realized_quote": str(gross),
        "accounted_net_realized_quote": str(net),
        "closed_net_r_total": str(total_r),
        "mean_closed_net_r": None if mean is None else str(mean),
        "median_closed_net_r": None if median is None else str(median),
        "r_profit_factor": None if factor is None else str(factor),
        "hold_only_diagnostic": {
            "branches": sum(row.hold_only_status is not None for row in rows),
            "closed": len(hold_values),
            "unresolved": sum(
                row.hold_only_status in {AllFillStatus.ORDINARY_OPEN, AllFillStatus.RECOVERY_OPEN}
                for row in rows
            ),
            "incurred_fees_quote": str(hold_fees),
            "remaining_absolute_base_quantity_by_asset": hold_open_by_asset,
            "mean_closed_net_r": None if hold_mean is None else str(hold_mean),
            "median_closed_net_r": None if hold_median is None else str(hold_median),
            "r_profit_factor": None if hold_factor is None else str(hold_factor),
        },
    }


def _declared_cell_keys() -> frozenset[str]:
    return frozenset(
        [name for name, _, _ in BLOCKS]
        + [f"W{week}" for week in range(1, 10)]
        + [f"SIDE:{side}" for side in ("NONE", "LONG", "SHORT")]
        + [f"REGIME:{regime.value}" for regime in RegimeLabel]
        + [
            f"{name}:SIDE:{side}:REGIME:{regime.value}"
            for name, _, _ in BLOCKS
            for side in ("NONE", "LONG", "SHORT")
            for regime in RegimeLabel
        ]
    )


def project_task_partition(
    task: StudyTask,
    population: ResearchV2StudyDecisionPopulation,
    base: tuple[ScreenObservation, ...],
    stress: tuple[ScreenObservation, ...],
) -> dict[str, object]:
    """Lossless compact per-task result referencing one validated population."""

    population.validate()
    _validate_task_population_facts(population, task)
    if len(population.decisions) != 6048 or len(base) != 6048 or len(stress) != 6048:
        raise ExploratoryScreenError("task partition differs from frozen population")
    decision_ids = tuple(_digest(str(decision.identity)) for decision in population.decisions)
    if (
        tuple(row.identity for row in base) != decision_ids
        or tuple(row.identity for row in stress) != decision_ids
    ):
        raise ExploratoryScreenError("task partition omits or substitutes decisions")
    for rows, scenario in ((base, AllFillScenario.BASE), (stress, AllFillScenario.COMBINED_STRESS)):
        if any(
            row.family.value != task.family.upper()
            or row.wire_symbol != task.wire_symbol
            or row.scenario is not scenario
            or row.observed_at != decision.identity.observed_at
            or row.input_kind != "ACCEPTED_C2"
            or (row.source_id, row.dataset_version, row.manifest_sha256)
            != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
            for row, decision in zip(rows, population.decisions, strict=True)
        ):
            raise ExploratoryScreenError("task partition family, scenario or chronology changed")
    if tuple(row.research_admissible for row in base) != tuple(
        row.research_admissible for row in stress
    ):
        raise ExploratoryScreenError("BASE/STRESS admission population differs")
    scenarios: dict[str, object] = {}
    for rows, scenario in ((base, AllFillScenario.BASE), (stress, AllFillScenario.COMBINED_STRESS)):
        cells: dict[str, object] = {}
        for name, _, _ in BLOCKS:
            cells[name] = _cell_summary(
                tuple(row for row in rows if observation_bin(row.observed_at)[0] == name)
            )
        for week in range(1, 10):
            key = f"W{week}"
            cells[key] = _cell_summary(
                tuple(row for row in rows if observation_bin(row.observed_at)[1] == key)
            )
        for side in ("NONE", "LONG", "SHORT"):
            cells[f"SIDE:{side}"] = _cell_summary(tuple(row for row in rows if row.side == side))
        for regime in RegimeLabel:
            cells[f"REGIME:{regime}"] = _cell_summary(
                tuple(row for row in rows if row.regime == regime.value)
            )
        for name, _, _ in BLOCKS:
            for side in ("NONE", "LONG", "SHORT"):
                for regime in RegimeLabel:
                    cells[f"{name}:SIDE:{side}:REGIME:{regime.value}"] = _cell_summary(
                        tuple(
                            row
                            for row in rows
                            if observation_bin(row.observed_at)[0] == name
                            and row.side == side
                            and row.regime == regime.value
                        )
                    )
        if set(cells) != _declared_cell_keys():
            raise ExploratoryScreenError("study projection omitted declared zero cells")
        scenarios[scenario.value] = {
            "summary": _cell_summary(rows),
            "cells": cells,
            "rows": tuple(_row_record(row) for row in rows),
        }
    partition: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "task": task.to_record(),
        "source_id": SOURCE_ID.value,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "population_sha256": population.integrity_seal,
        "population_decision_count": len(population.decisions),
        "scenarios": scenarios,
    }
    partition["partition_sha256"] = _digest(partition)
    return partition


def _validate_task_population_facts(
    population: ResearchV2StudyDecisionPopulation, task: StudyTask
) -> None:
    """Bind a constructor-validated population to exact task/configuration facts."""

    bounds = ResearchV2EpochBounds(
        TradingSymbol(task.wire_symbol.removesuffix("USDT"), "USDT"),
        task.context_start,
        task.decision_start,
        task.decision_end,
        task.last_follow_up_end,
    )
    if (
        not 0 <= task.index < 30
        or task != frozen_tasks()[task.index]
        or population.configuration.bounds != bounds
        or population.configuration.family != task.family
        or population.configuration.research_equity_quote != RESEARCH_EQUITY
        or population.configuration.input_binding != ACCEPTED_C2_BINDING
        or population.protocol_sha256 != PROTOCOL_SHA256
        or len(population.decisions) != 6048
    ):
        raise ExploratoryScreenError("population differs from frozen task or accepted input")
    _validate_frozen_study_configuration(population.configuration)


def _parse_row(raw: object) -> ScreenObservation:
    if not isinstance(raw, dict):
        raise ExploratoryScreenError("compact screen row is not an object")
    try:
        row = ScreenObservation(
            identity=str(raw["identity"]),
            observed_at=datetime.fromisoformat(raw["observed_at"].replace("Z", "+00:00")),
            wire_symbol=str(raw["symbol"]),
            family=AllFillFamily(raw["family"]),
            scenario=AllFillScenario(raw["scenario"]),
            status=AllFillStatus(raw["status"]),
            research_admissible=raw["research_admissible"],
            primary_closed_net_r=(
                None
                if raw["primary_closed_net_r"] is None
                else Decimal(raw["primary_closed_net_r"])
            ),
            primary_fee_quote=Decimal(raw["primary_fee_quote"]),
            primary_open_quantity=Decimal(raw["primary_open_quantity"]),
            primary_gross_pnl_quote=(
                None
                if raw["primary_gross_pnl_quote"] is None
                else Decimal(raw["primary_gross_pnl_quote"])
            ),
            primary_net_pnl_quote=(
                None
                if raw["primary_net_pnl_quote"] is None
                else Decimal(raw["primary_net_pnl_quote"])
            ),
            side=str(raw["side"]),
            regime=raw["regime"],
            result_sha256=str(raw["all_fill_result_sha256"]),
            hold_only_status=(
                None if raw["hold_only_status"] is None else AllFillStatus(raw["hold_only_status"])
            ),
            hold_only_closed_net_r=(
                None
                if raw["hold_only_closed_net_r"] is None
                else Decimal(raw["hold_only_closed_net_r"])
            ),
            hold_only_fee_quote=(
                None if raw["hold_only_fee_quote"] is None else Decimal(raw["hold_only_fee_quote"])
            ),
            hold_only_open_quantity=(
                None
                if raw["hold_only_open_quantity"] is None
                else Decimal(raw["hold_only_open_quantity"])
            ),
            hold_only_branch_sha256=raw["hold_only_branch_sha256"],
            input_kind=str(raw["input_kind"]),
            source_id=str(raw["source_id"]),
            dataset_version=str(raw["dataset_version"]),
            manifest_sha256=str(raw["manifest_sha256"]),
        )
        row.validate()
        if _row_record(row) != raw:
            raise ExploratoryScreenError("compact screen row has contradictory derived fields")
        return row
    except (KeyError, TypeError, ValueError) as exc:
        raise ExploratoryScreenError("compact screen row is malformed") from exc


def verify_task_partition(
    payload: bytes, task: StudyTask, *, expected_population_sha256: str
) -> tuple[tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]]:
    """Offline compact-partition check; never loads an API or infers missing rows."""

    try:
        value: Any = json.loads(payload)
        if not isinstance(value, dict) or _canonical(value) != payload:
            raise ExploratoryScreenError("task partition is not canonical JSON")
        asserted_sha = value.pop("partition_sha256")
        if (
            asserted_sha != _digest(value)
            or value["schema_version"] != SCHEMA_VERSION
            or value["protocol_sha256"] != PROTOCOL_SHA256
            or value["task_plan_sha256"] != TASK_PLAN_SHA256
            or value["task"] != task.to_record()
            or value["source_id"] != SOURCE_ID.value
            or value["dataset_version"] != DATASET_VERSION
            or value["manifest_sha256"] != MANIFEST_SHA256
            or value["population_sha256"] != expected_population_sha256
            or value["population_decision_count"] != 6048
            or set(value["scenarios"]) != {scenario.value for scenario, _ in SCENARIOS}
        ):
            raise ExploratoryScreenError("task partition differs from frozen authority")
        scenarios: list[tuple[ScreenObservation, ...]] = []
        for scenario, _ in SCENARIOS:
            scenario_value = value["scenarios"][scenario.value]
            rows = tuple(_parse_row(row) for row in scenario_value["rows"])
            if len(rows) != 6048 or any(
                row.observed_at != DECISION_START + (index + 1) * Timeframe.MINUTES_15.duration
                or row.scenario is not scenario
                or row.wire_symbol != task.wire_symbol
                or row.family.value != task.family.upper()
                or row.input_kind != "ACCEPTED_C2"
                or (row.source_id, row.dataset_version, row.manifest_sha256)
                != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
                for index, row in enumerate(rows)
            ):
                raise ExploratoryScreenError("task partition has missing or foreign observations")
            if scenario_value["summary"] != _cell_summary(rows):
                raise ExploratoryScreenError("task summary differs from compact rows")
            if set(scenario_value["cells"]) != _declared_cell_keys():
                raise ExploratoryScreenError("task partition omitted declared cells")
            # Full cell construction is checked at publication; verify every saved cell
            # from its explicitly declared row predicate without a source re-fetch.
            for key, summary in scenario_value["cells"].items():
                if key in {name for name, _, _ in BLOCKS}:
                    members = tuple(
                        row for row in rows if observation_bin(row.observed_at)[0] == key
                    )
                elif key.startswith("W") and key[1:].isdigit():
                    members = tuple(
                        row for row in rows if observation_bin(row.observed_at)[1] == key
                    )
                elif key.startswith("SIDE:"):
                    members = tuple(row for row in rows if row.side == key[5:])
                elif key.startswith("REGIME:"):
                    members = tuple(row for row in rows if row.regime == key[7:])
                else:
                    block, _, side, _, regime = key.split(":")
                    members = tuple(
                        row
                        for row in rows
                        if observation_bin(row.observed_at)[0] == block
                        and row.side == side
                        and row.regime == regime
                    )
                if summary != _cell_summary(members):
                    raise ExploratoryScreenError("task cell differs from compact rows")
            scenarios.append(rows)
        if tuple(row.identity for row in scenarios[0]) != tuple(
            row.identity for row in scenarios[1]
        ):
            raise ExploratoryScreenError("task scenarios contain different decisions")
        if tuple(row.research_admissible for row in scenarios[0]) != tuple(
            row.research_admissible for row in scenarios[1]
        ):
            raise ExploratoryScreenError("task scenarios contain different admissions")
        return scenarios[0], scenarios[1]
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ExploratoryScreenError("task partition verification failed closed") from exc


def project_frozen_screen(
    task_rows: tuple[
        tuple[StudyTask, tuple[ScreenObservation, ...], tuple[ScreenObservation, ...]], ...
    ],
) -> dict[str, object]:
    """Only thirty complete tasks may receive family-level economic labels."""

    if tuple(task for task, _, _ in task_rows) != frozen_tasks():
        raise ExploratoryScreenError("partial or reordered task inventory has no final screen")
    for task, base_rows, stress_rows in task_rows:
        if len(base_rows) != 6048 or len(stress_rows) != 6048:
            raise ExploratoryScreenError("final screen has an incomplete task population")
        for rows, scenario in (
            (base_rows, AllFillScenario.BASE),
            (stress_rows, AllFillScenario.COMBINED_STRESS),
        ):
            if any(
                row.observed_at != task.decision_start + (index + 1) * Timeframe.MINUTES_15.duration
                or row.wire_symbol != task.wire_symbol
                or row.family.value != task.family.upper()
                or row.scenario is not scenario
                or row.input_kind != "ACCEPTED_C2"
                or (row.source_id, row.dataset_version, row.manifest_sha256)
                != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256)
                for index, row in enumerate(rows)
            ):
                raise ExploratoryScreenError("final screen has a foreign or misordered row")
        if tuple(row.identity for row in base_rows) != tuple(row.identity for row in stress_rows):
            raise ExploratoryScreenError("final screen scenarios contain different decisions")
        if tuple(row.research_admissible for row in base_rows) != tuple(
            row.research_admissible for row in stress_rows
        ):
            raise ExploratoryScreenError("final screen scenarios contain different admissions")
        for rows in (base_rows, stress_rows):
            for row in rows:
                row.validate()
            if len({row.identity for row in rows}) != len(rows):
                raise ExploratoryScreenError("final screen duplicates a decision")
    families: dict[str, object] = {}
    for family_name in FAMILIES:
        family = AllFillFamily(family_name.upper())
        base = tuple(
            row for task, rows, _ in task_rows if task.family == family_name for row in rows
        )
        stress = tuple(
            row for task, _, rows in task_rows if task.family == family_name for row in rows
        )
        classification, detail = classify_family(base, stress, family)
        families[family_name] = {
            "classification": classification.value,
            "details": detail,
            "base": _cell_summary(base),
            "combined_stress": _cell_summary(stress),
        }
    projection: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "source_id": SOURCE_ID.value,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "complete_task_count": 30,
        "families": families,
        "interpretation": "EXPLORATORY_ONLY; NOT_PORTFOLIO_OR_TRADING_AUTHORITY",
    }
    projection["projection_sha256"] = _digest(projection)
    return projection
