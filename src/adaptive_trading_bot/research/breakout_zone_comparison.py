"""Bounded baseline-versus-zone Breakout research preparation.

This module is deliberately finite: ten pinned Issue-187 Breakout tasks, two
accepted strategy identities, two predeclared execution scenarios, and no
implicit execution grant.  Data-only preflight never constructs a strategy.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from bisect import bisect_left
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from functools import partial
from hmac import compare_digest
from pathlib import Path
from typing import NoReturn, cast

from adaptive_trading_bot.backtest import (
    ExecutionCandleSource,
    ExecutionCostConfiguration,
    ExecutionSeries,
    HistoricalExecutionSource,
    UnfilledOrder,
)
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.journal import (
    DecisionJournalBatch,
    DecisionJournalEntry,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime.technical import RegimeLabel
from adaptive_trading_bot.research.all_fill_replay import (
    AllFillBranch,
    AllFillFamily,
    AllFillReplayResult,
    AllFillScenario,
    AllFillStatus,
    ResearchAllFillReplayRunner,
    validate_shared_all_fill_relations,
)
from adaptive_trading_bot.research.breakout_retest_replay import BreakoutRetestReplayResult
from adaptive_trading_bot.research.paired_exit_replay import (
    EntryIntentProvenance,
    HistoricalStrategyContextObservation,
)
from adaptive_trading_bot.research.research_v2_admission import (
    ACCEPTED_C2_BINDING,
    DATASET_VERSION,
    MANIFEST_SHA256,
    SOURCE_ID,
    LockedResearchV2CandleStore,
    ResearchV2DecisionPopulation,
    ResearchV2StudyDecisionPopulation,
)
from adaptive_trading_bot.research.research_v2_exploratory_screen import (
    BLOCKS,
    FOLLOW_UP,
    authority_result_record,
    recover_authority_result,
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
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _integrity_seal,
    _research_health,
)
from adaptive_trading_bot.risk import OperationalSafetyGate, RiskManager, RiskPortfolioSnapshot
from adaptive_trading_bot.strategies import (
    BreakoutRetestRuleAudit,
    BreakoutRetestZoneInvalidationAudit,
    BreakoutRetestZoneInvalidationStrategy,
    CandidateAction,
)

PROTOCOL_VERSION = "BREAKOUT_RETEST_ZONE_COMPARISON_V1"
SCHEMA_VERSION = "breakout-retest-zone-comparison-v1"
RESULT_SCHEMA_VERSION = "breakout-retest-zone-comparison-result-v1"
LEDGER_SCHEMA_VERSION = "breakout-retest-zone-comparison-ledger-v1"
BASELINE_IDENTITY = ("breakout_retest", "1.0.0")
ZONE_IDENTITY = ("breakout_retest_zone_invalidation", "0.1.0")
ZONE_POLICY = "BREAKOUT_RETEST_ZONE_INVALIDATION_V1"
FIXED_2R_POLICY = "BREAKOUT_RETEST_FIXED_2R_V1"
ORIGINAL_EXECUTABLE_SHA = "316744efa9c4b50aa8d999b3c4d11fd874b4c28c"
ORIGINAL_RUN_UUID = "686bd4a1-ffff-4391-8562-f1e4caf7a575"
ARTIFACT_INDEX_SHA256 = "677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2"
EVIDENCE_ROOT = Path("data/research_evidence/issue-187-exploratory-screen-316744efa9c4b50a")
LEDGER_RELATIVE = Path("ledger") / ORIGINAL_RUN_UUID
DECISION_START = datetime(2026, 8, 3, tzinfo=UTC)
DECISION_END = datetime(2026, 10, 5, tzinfo=UTC)
FOLLOW_UP_END = datetime(2026, 10, 12, tzinfo=UTC)
RESEARCH_EQUITY = Decimal("10000")
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_HEX40 = frozenset("0123456789abcdef")


class BreakoutZoneComparisonError(ValueError):
    """The fixed comparison authority or retained evidence failed closed."""


class ComparisonArm(StrEnum):
    BASELINE = "BASELINE_1_0_0"
    ZONE = "ZONE_0_1_0"


class ResultOrigin(StrEnum):
    REUSED_ISSUE_187 = "REUSED_ISSUE_187_BASELINE"
    NEW_ZONE_SIMULATION = "NEW_ZONE_SIMULATION"


@dataclass(frozen=True, slots=True)
class PinnedArtifact:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ComparisonTask:
    index: int
    original_task_index: int
    wire_symbol: str
    context_start: datetime
    population: PinnedArtifact
    partition: PinnedArtifact
    authority: PinnedArtifact

    @property
    def task_id(self) -> str:
        return f"{self.index:02d}-{self.wire_symbol}-breakout-zone-comparison"

    @property
    def symbol(self) -> TradingSymbol:
        return TradingSymbol(self.wire_symbol.removesuffix("USDT"), "USDT")

    def to_record(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "original_task_index": self.original_task_index,
            "symbol": self.wire_symbol,
            "context_start": _iso(self.context_start),
            "decision_start": _iso(DECISION_START),
            "decision_end": _iso(DECISION_END),
            "follow_up_end": _iso(FOLLOW_UP_END),
            "decision_slots": 6048,
            "arm_observations": 12096,
            "arm_scenario_cells": 24192,
            "population": _artifact_record(self.population),
            "partition": _artifact_record(self.partition),
            "authority": _artifact_record(self.authority),
        }


_TASK_FACTS = (
    (
        1,
        "BTCUSDT",
        "2026-07-21T00:00:00+00:00",
        "4d313c54438134caa53576b346043cf0595bd2dfbffae520184ffaaf57277867",
        8248005,
        "9175fa50e4fbebc36bd42e375668a5ce534be9be871f992ac7f945f8f472733b",
        11485765,
        "879896b249102c52282002e1e8dd26c8115d0fdd9c7563c7e34b5c1e55ce106e",
        731749,
    ),
    (
        4,
        "ETHUSDT",
        "2026-07-16T00:00:00+00:00",
        "6c678ebaed63a527a5ac564145fc840a0491a81cf3c647d1ac193da0ff9f52e6",
        8489655,
        "073016991ea102f9a44c734bea24d3b5fcf80ff4c3eb8b39642f87b1cc5cc823",
        11486580,
        "18d96929cef3595f0bf99eda31d9f587c53e8b1462a9f5b7b73ec265df835783",
        741894,
    ),
    (
        7,
        "SOLUSDT",
        "2026-07-16T00:00:00+00:00",
        "f21eb6319c7dc6ae5909910ac9d4f2164285f3f52e6c503f1c810063f644cbb9",
        8358282,
        "24aa3c27d5ee4d05d000b55e7106e534d9b1fdf368beca1d0633a2695d7ef6f6",
        11489557,
        "99df8652c929dbec45b8aea04468e125cc8f59402ca8dfde34b257ce0f3aadf0",
        745954,
    ),
    (
        10,
        "XRPUSDT",
        "2026-07-16T00:00:00+00:00",
        "324608927599cc775e1e1fb5fd4991ba0988b42e6646cbf9dd29abae9038ae72",
        8400027,
        "6a3157b470dc3857bbd3311a1aaf767b2181e6e3a805790436999d8b463d6f3f",
        11489009,
        "5bc19e3ee985a58f8fadfbca10494f71bb671ae882c79ccc462891f8e90ac175",
        745899,
    ),
    (
        13,
        "ZECUSDT",
        "2026-07-16T00:00:00+00:00",
        "4e5903a7a55df282d744089a6d3999a6caed4e5da6909e72e6336c8334c51a34",
        8484052,
        "95d168426320242de009a753ac0a1eb43af5e7f4388a6f692a1ca82135bea294",
        11490352,
        "313b70af869d90b0e015ecffcefee9b6d0c90767f761b476d1abf7c9a9f00db1",
        755012,
    ),
    (
        16,
        "ZAMAUSDT",
        "2026-07-16T00:00:00+00:00",
        "78ac25c9ae6669d2526c21f54d528c8ad96273fc7857338aa472152463aa6192",
        8325630,
        "6bb418678456d3118045a47eab84db677e83dfd8a12884f9e1515407ec4d4636",
        11499486,
        "21ed41da36e07de96c8cad55105278440641281ef5a1d98d121b38f64eb21724",
        731919,
    ),
    (
        19,
        "BCHUSDT",
        "2026-07-16T00:00:00+00:00",
        "7d6355f947caac2e0aebc1e89f67b6448ee0c5918c3e56dc6d7bb54679295b91",
        8440037,
        "882bb6765739ebbeffad24b49edd7dec810e27dd08e5acf60a40ef3b2e9fa715",
        11484784,
        "ff8ab23ec4e03983c3234fd192198163f0db6f5d31986242a8efeb1fbe5df98d",
        731779,
    ),
    (
        22,
        "BTWUSDT",
        "2026-07-25T12:00:00+00:00",
        "67e37efc1d707856f6f52da4d60a9854668ae6b9c69f6792e899988e379e3aff",
        8058437,
        "d9df352180711c9de5f799cd6c6ebb964aeb99dd48b1da4141c6b26527ee66ff",
        11489405,
        "d54121541abf2db6a09fbfb47a06dc7d7bcd8f6cfc458ec17066e3f2273f4b74",
        746528,
    ),
    (
        25,
        "AVAXUSDT",
        "2026-07-16T00:00:00+00:00",
        "e4bfe89832b1ca33cc3e3953d519c30cd8c9b2ad0afeab6715be2f15ca51deb3",
        8370542,
        "1ca94f1fd5aa87faa62e37b0fce38926d9f741d285f18051ed187b51f6817465",
        11500159,
        "881c8cd7fc35ce39ecf44a5d1da79725a3d1da955ec8601e09bd82d3a14b591e",
        741896,
    ),
    (
        28,
        "DOGEUSDT",
        "2026-07-16T00:00:00+00:00",
        "540d45ce132c35cdc01c225f2d171ab8836b95bd96c1e7aa23628263b867408c",
        8330712,
        "ceb613d17bb16fdfde7b5c19a486a0300779cd0e56e7cbc6f3d3589816803e2a",
        11496717,
        "a0ea80bf21518fe8d79542d8a41d066e77da0f8f20ea53d856163fabfa2c5282",
        732087,
    ),
)


def _build_tasks() -> tuple[ComparisonTask, ...]:
    output: list[ComparisonTask] = []
    for index, facts in enumerate(_TASK_FACTS):
        original, wire, context, pop_sha, pop_size, part_sha, part_size, auth_sha, auth_size = facts
        prefix = f"ledger/{ORIGINAL_RUN_UUID}/{original:02d}"
        output.append(
            ComparisonTask(
                index,
                original,
                wire,
                datetime.fromisoformat(context),
                PinnedArtifact(f"{prefix}.population.json.gz", pop_sha, pop_size),
                PinnedArtifact(f"{prefix}.partition.json", part_sha, part_size),
                PinnedArtifact(f"{prefix}.authority.jsonl.gz", auth_sha, auth_size),
            )
        )
    return tuple(output)


TASKS = _build_tasks()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _artifact_record(value: PinnedArtifact) -> dict[str, object]:
    return {"path": value.path, "sha256": value.sha256, "size_bytes": value.size_bytes}


def protocol_facts() -> dict[str, object]:
    return {
        "protocol": PROTOCOL_VERSION,
        "schema": SCHEMA_VERSION,
        "baseline": {"strategy_id": BASELINE_IDENTITY[0], "version": BASELINE_IDENTITY[1]},
        "zone": {
            "strategy_id": ZONE_IDENTITY[0],
            "version": ZONE_IDENTITY[1],
            "policy": ZONE_POLICY,
        },
        "exit_policy": FIXED_2R_POLICY,
        "input": {
            "source_id": SOURCE_ID.value,
            "dataset_version": DATASET_VERSION,
            "manifest_sha256": MANIFEST_SHA256,
            "artifact_index_sha256": ARTIFACT_INDEX_SHA256,
            "original_executable_sha": ORIGINAL_EXECUTABLE_SHA,
            "original_run_uuid": ORIGINAL_RUN_UUID,
        },
        "calendar": {
            "decision_start": _iso(DECISION_START),
            "decision_end": _iso(DECISION_END),
            "follow_up": "PT168H",
            "latest_cutoff": _iso(FOLLOW_UP_END),
            "periods": ("P1", "P2", "P3"),
            "weeks": tuple(f"W{index}" for index in range(1, 10)),
        },
        "costs": {
            AllFillScenario.BASE.value: ("2", "2", "10", "PT0S"),
            AllFillScenario.COMBINED_STRESS.value: ("4", "4", "20", "PT1S"),
        },
        "research_equity_quote": "10000",
        "interpretation": {
            "candidate_upper_bound": 29,
            "promotion_eligible": False,
            "selection_informed": True,
            "scenarios_are_independent_samples": False,
        },
    }


PROTOCOL_SHA256 = _digest(protocol_facts())
TASK_PLAN_SHA256 = _digest(tuple(task.to_record() for task in TASKS))


def _file_sha(path: Path) -> str:
    if not path.is_file():
        raise BreakoutZoneComparisonError(f"pinned input missing: {path}")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _verify_artifact(root: Path, artifact: PinnedArtifact) -> None:
    path = root / artifact.path
    if path.stat().st_size != artifact.size_bytes or not compare_digest(
        _file_sha(path), artifact.sha256
    ):
        raise BreakoutZoneComparisonError(f"pinned artifact changed: {artifact.path}")


def data_only_preflight(repo: Path) -> dict[str, object]:
    """Verify exact published bytes and locked calendar without strategy construction."""

    repo = repo.resolve(strict=True)
    evidence = repo / EVIDENCE_ROOT
    index_path = evidence / "artifact-index.json"
    if not compare_digest(_file_sha(index_path), ARTIFACT_INDEX_SHA256):
        raise BreakoutZoneComparisonError("Issue-187 artifact index changed")
    index = json.loads(index_path.read_bytes())
    indexed = {item["path"]: item for item in index["files"]}
    for task in TASKS:
        for artifact in (task.population, task.partition, task.authority):
            expected = indexed.get(artifact.path)
            if expected != _artifact_record(artifact):
                raise BreakoutZoneComparisonError("artifact index differs from frozen task plan")
            _verify_artifact(evidence, artifact)
        partition = json.loads((evidence / task.partition.path).read_bytes())
        retained_task = partition.get("task", {})
        if (
            partition.get("source_id") != SOURCE_ID.value
            or partition.get("dataset_version") != DATASET_VERSION
            or partition.get("manifest_sha256") != MANIFEST_SHA256
            or partition.get("population_decision_count") != 6048
            or set(partition.get("scenarios", {}))
            != {scenario.value for scenario in AllFillScenario}
            or not isinstance(retained_task, dict)
            or retained_task.get("symbol") != task.wire_symbol
            or retained_task.get("family") != "breakout_retest"
            or retained_task.get("context_start")
            != task.context_start.isoformat().replace("+00:00", "Z")
            or retained_task.get("decision_start")
            != DECISION_START.isoformat().replace("+00:00", "Z")
            or retained_task.get("decision_end") != DECISION_END.isoformat().replace("+00:00", "Z")
            or retained_task.get("last_follow_up_end")
            != FOLLOW_UP_END.isoformat().replace("+00:00", "Z")
        ):
            raise BreakoutZoneComparisonError(
                "retained Breakout partition differs from frozen task identity"
            )
    # Existing locked store performs the accepted C2 manifest/hash/epoch verification only.
    store = LockedResearchV2CandleStore(repo)
    member_names = tuple(f"{member.symbol.base}{member.symbol.quote}" for member in store.members)
    if member_names != tuple(task.wire_symbol for task in TASKS):
        raise BreakoutZoneComparisonError("locked member order differs from comparison plan")
    return {
        "protocol": PROTOCOL_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_plan_sha256": TASK_PLAN_SHA256,
        "artifact_index_sha256": ARTIFACT_INDEX_SHA256,
        "source_id": SOURCE_ID.value,
        "dataset_version": DATASET_VERSION,
        "manifest_sha256": MANIFEST_SHA256,
        "task_count": len(TASKS),
        "decision_slots": 60480,
        "arm_observations": 120960,
        "arm_scenario_cells": 241920,
        "tasks": tuple(task.to_record() for task in TASKS),
        "strategy_evaluations": 0,
        "risk_evaluations": 0,
        "outcome_simulations": 0,
    }


@dataclass(frozen=True, slots=True)
class ComparisonExecutionGrant:
    kind: str
    run_uuid: str
    executable_sha: str
    protocol_sha256: str
    task_plan_sha256: str
    source_id: str
    dataset_version: str
    manifest_sha256: str
    artifact_index_sha256: str

    def validate(self, *, actual_sha: str, synthetic: bool = False) -> None:
        expected_kind = "SYNTHETIC_TEST_GRANT" if synthetic else "EXTERNAL_REVIEWED_EXECUTION_GRANT"
        try:
            canonical_uuid = str(uuid.UUID(self.run_uuid))
        except ValueError as exc:
            raise BreakoutZoneComparisonError("comparison grant UUID is invalid") from exc
        if (
            self.kind != expected_kind
            or canonical_uuid != self.run_uuid
            or len(actual_sha) != 40
            or any(character not in _HEX40 for character in actual_sha)
            or self.executable_sha != actual_sha
            or self.protocol_sha256 != PROTOCOL_SHA256
            or self.task_plan_sha256 != TASK_PLAN_SHA256
        ):
            raise BreakoutZoneComparisonError("grant does not bind reviewed code/protocol/tasks")
        if synthetic:
            if (
                self.source_id == SOURCE_ID.value
                or self.dataset_version == DATASET_VERSION
                or self.manifest_sha256 == MANIFEST_SHA256
                or self.artifact_index_sha256 == ARTIFACT_INDEX_SHA256
            ):
                raise BreakoutZoneComparisonError("synthetic grant impersonates accepted history")
        elif (
            self.source_id,
            self.dataset_version,
            self.manifest_sha256,
            self.artifact_index_sha256,
        ) != (SOURCE_ID.value, DATASET_VERSION, MANIFEST_SHA256, ARTIFACT_INDEX_SHA256):
            raise BreakoutZoneComparisonError("grant differs from pinned Issue-187 authority")

    @classmethod
    def from_file(cls, path: Path) -> ComparisonExecutionGrant:
        try:
            raw = json.loads(path.read_bytes())
            if not isinstance(raw, dict) or set(raw) != {item.name for item in fields(cls)}:
                raise ValueError("grant fields differ")
            return cls(**raw)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise BreakoutZoneComparisonError("external comparison grant is malformed") from exc


@dataclass(frozen=True, slots=True)
class OpportunityKey:
    source_id: str
    dataset_version: str
    manifest_sha256: str
    symbol: str
    epoch_start: datetime
    decision_at: datetime
    side: str
    pivot_kind: str
    pivot_open_time: datetime
    pivot_confirmed_at: datetime
    pivot_price: Decimal


def _opportunity_key(
    decision: DecisionJournalEntry[object, object],
    *,
    dataset_version: str,
    manifest_sha256: str,
    epoch_start: datetime,
) -> OpportunityKey | None:
    evaluation = decision.strategy_evaluation
    if evaluation.action is CandidateAction.NO_TRADE:
        return None
    audit = evaluation.rule_audit
    if not isinstance(audit, BreakoutRetestRuleAudit):
        raise BreakoutZoneComparisonError("baseline candidate lacks Breakout rule audit")
    if any(
        value is None
        for value in (
            audit.breakout_pivot_kind,
            audit.breakout_pivot_open_time,
            audit.breakout_pivot_confirmed_at,
            audit.breakout_pivot_price,
        )
    ):
        raise BreakoutZoneComparisonError("baseline candidate lacks pivot identity")
    assert audit.breakout_pivot_kind is not None
    assert audit.breakout_pivot_open_time is not None
    assert audit.breakout_pivot_confirmed_at is not None
    assert audit.breakout_pivot_price is not None
    return OpportunityKey(
        decision.identity.source_id.value,
        dataset_version,
        manifest_sha256,
        str(decision.identity.symbol),
        epoch_start,
        decision.identity.observed_at,
        evaluation.action.value,
        audit.breakout_pivot_kind.value,
        audit.breakout_pivot_open_time,
        audit.breakout_pivot_confirmed_at,
        audit.breakout_pivot_price,
    )


@dataclass(frozen=True, slots=True)
class BreakoutComparisonPopulation:
    task_id: str
    baseline_population: ResearchV2DecisionPopulation
    zone_decisions: tuple[DecisionJournalEntry[object, object], ...]
    opportunity_keys: tuple[OpportunityKey | None, ...]
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-zone-comparison-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-zone-comparison-population",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutZoneComparisonError("comparison population integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.baseline_population.validate()
        config = self.baseline_population.configuration
        if (
            self.schema_version != SCHEMA_VERSION
            or config.family != "breakout_retest"
            or len(self.baseline_population.decisions) != len(self.zone_decisions)
            or len(self.zone_decisions) != len(self.opportunity_keys)
            or not self.task_id
        ):
            raise BreakoutZoneComparisonError("comparison population shape is invalid")
        seen: set[datetime] = set()
        for baseline, zone, key in zip(
            self.baseline_population.decisions,
            self.zone_decisions,
            self.opportunity_keys,
            strict=True,
        ):
            baseline.validate()
            zone.validate()
            expected_key = _opportunity_key(
                baseline,
                dataset_version=self.baseline_population.dataset_version,
                manifest_sha256=self.baseline_population.manifest_sha256,
                epoch_start=config.bounds.context_start,
            )
            zone_audit = zone.strategy_evaluation.rule_audit
            if (
                (baseline.identity.strategy_id, baseline.identity.strategy_version)
                != BASELINE_IDENTITY
                or (zone.identity.strategy_id, zone.identity.strategy_version) != ZONE_IDENTITY
                or baseline.context != zone.context
                or baseline.identity.observed_at in seen
                or key != expected_key
                or not isinstance(zone_audit, BreakoutRetestZoneInvalidationAudit)
                or zone_audit.underlying_strategy_id != BASELINE_IDENTITY[0]
                or zone_audit.underlying_strategy_version != BASELINE_IDENTITY[1]
                or zone_audit.underlying_action != baseline.strategy_evaluation.action
                or zone_audit.underlying_reasons_for != baseline.strategy_evaluation.reasons_for
                or zone_audit.underlying_reasons_against
                != baseline.strategy_evaluation.reasons_against
                or zone_audit.underlying_rule_audit != baseline.strategy_evaluation.rule_audit
                or (
                    zone.strategy_evaluation.action is not CandidateAction.NO_TRADE
                    and baseline.strategy_evaluation.action is CandidateAction.NO_TRADE
                )
                or (
                    zone.strategy_evaluation.action is not CandidateAction.NO_TRADE
                    and expected_key is None
                )
            ):
                raise BreakoutZoneComparisonError("cross-arm decision authority is contradictory")
            seen.add(baseline.identity.observed_at)
        if (
            self.baseline_population.configuration.input_binding.kind == "ACCEPTED_C2"
            and len(seen) != 6048
        ):
            raise BreakoutZoneComparisonError("accepted comparison task omits decision slots")


def build_comparison_population(
    baseline_population: ResearchV2DecisionPopulation,
    *,
    task_id: str,
) -> BreakoutComparisonPopulation:
    """Evaluate only the accepted zone strategy over the same retained contexts."""

    baseline_population.validate()
    configuration = baseline_population.configuration
    if configuration.family != "breakout_retest":
        raise BreakoutZoneComparisonError("comparison input is not a Breakout population")
    strategy = BreakoutRetestZoneInvalidationStrategy()
    risk = RiskManager(configuration.risk_configuration)
    safety = OperationalSafetyGate(configuration.safety_configuration)
    decisions: list[DecisionJournalEntry[object, object]] = []
    journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
    baseline_index = 0
    for observation in baseline_population.contexts:
        observation.validate()
        context = observation.context
        evaluation = strategy.evaluate(context)
        if (
            not configuration.bounds.decision_start
            < context.observed_at
            <= configuration.bounds.decision_end
        ):
            continue
        if baseline_index >= len(baseline_population.decisions):
            raise BreakoutZoneComparisonError("baseline population omits a decision slot")
        baseline = baseline_population.decisions[baseline_index]
        if baseline.context != context:
            raise BreakoutZoneComparisonError("baseline decision is not bound to retained context")
        equity = configuration.research_equity_quote
        portfolio = RiskPortfolioSnapshot(context.observed_at, "USDT", equity, equity, equity, ())
        risk_decision = risk.evaluate(evaluation, portfolio)
        safety_decision = safety.evaluate(
            risk_decision, _research_health(context, configuration.safety_assumptions)
        )
        decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
        decision.validate()
        journal.append_batch(DecisionJournalBatch(context.observed_at, (decision,)))
        decisions.append(decision)
        baseline_index += 1
    if baseline_index != len(baseline_population.decisions):
        raise BreakoutZoneComparisonError("zone arm omits retained decision slots")
    if tuple(decisions) != journal.snapshot().entries:
        raise BreakoutZoneComparisonError("zone journal differs from evaluated decisions")
    keys = tuple(
        _opportunity_key(
            decision,
            dataset_version=baseline_population.dataset_version,
            manifest_sha256=baseline_population.manifest_sha256,
            epoch_start=configuration.bounds.context_start,
        )
        for decision in baseline_population.decisions
    )
    result = BreakoutComparisonPopulation(task_id, baseline_population, tuple(decisions), keys)
    result.validate()
    return result


@dataclass(frozen=True, slots=True)
class BreakoutComparisonReplayResult:
    decision: DecisionJournalEntry[object, object]
    family: AllFillFamily
    series: ExecutionSeries
    costs: ExecutionCostConfiguration
    scenario: AllFillScenario
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
    origin: ResultOrigin
    original_authority_sha256: str | None = None
    original_record_sha256: str | None = None
    schema_version: str = field(init=False, default=RESULT_SCHEMA_VERSION)
    protocol_version: str = field(init=False, default=PROTOCOL_VERSION)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-zone-comparison-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-zone-comparison-result",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutZoneComparisonError("comparison result integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        expected_identity = (
            BASELINE_IDENTITY if self.arm is ComparisonArm.BASELINE else ZONE_IDENTITY
        )
        expected_origin = (
            ResultOrigin.REUSED_ISSUE_187
            if self.arm is ComparisonArm.BASELINE
            else ResultOrigin.NEW_ZONE_SIMULATION
        )
        if (
            self.schema_version != RESULT_SCHEMA_VERSION
            or self.protocol_version != PROTOCOL_VERSION
            or self.family is not AllFillFamily.BREAKOUT_RETEST
            or self.start != self.decision.identity.observed_at
            or self.end != self.decision.identity.observed_at + FOLLOW_UP
            or self.series.source_id != self.decision.identity.source_id
            or self.series.symbol != self.decision.identity.symbol
            or self.series.timeframe is not Timeframe.MINUTES_15
            or (self.decision.identity.strategy_id, self.decision.identity.strategy_version)
            != expected_identity
            or self.origin is not expected_origin
            or (self.arm is ComparisonArm.BASELINE)
            != (
                self.original_authority_sha256 is not None
                and self.original_record_sha256 is not None
            )
            or any(
                value is not None and (len(value) != 64 or any(c not in _HEX40 for c in value))
                for value in (self.original_authority_sha256, self.original_record_sha256)
            )
        ):
            raise BreakoutZoneComparisonError("comparison result identity/origin is invalid")
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
            error_type=BreakoutZoneComparisonError,
        )


def reuse_baseline_result(
    result: AllFillReplayResult,
    *,
    authority_sha256: str,
    record_sha256: str,
) -> BreakoutComparisonReplayResult:
    result.validate()
    if result.family is not AllFillFamily.BREAKOUT_RETEST:
        raise BreakoutZoneComparisonError("reused original is not Breakout authority")
    return BreakoutComparisonReplayResult(
        result.decision,
        result.family,
        result.series,
        result.costs,
        result.scenario,
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
        ResultOrigin.REUSED_ISSUE_187,
        authority_sha256,
        record_sha256,
    )


class BreakoutZoneComparisonRunner:
    """Only the zone arm is executable; baseline results must be retained originals."""

    def __init__(self, source: ExecutionCandleSource) -> None:
        self._runner = ResearchAllFillReplayRunner(source)

    def run_zone(
        self,
        decision: DecisionJournalEntry[object, object],
        contexts: tuple[HistoricalStrategyContextObservation, ...],
        series: ExecutionSeries,
        costs: ExecutionCostConfiguration,
        *,
        end: datetime,
    ) -> BreakoutComparisonReplayResult:
        scenario_facts = (
            costs.spread_bps,
            costs.slippage_bps,
            costs.fee_bps,
            costs.latency,
        )
        if scenario_facts == (Decimal(2), Decimal(2), Decimal(10), timedelta()):
            scenario = AllFillScenario.BASE
        elif scenario_facts == (
            Decimal(4),
            Decimal(4),
            Decimal(20),
            timedelta(seconds=1),
        ):
            scenario = AllFillScenario.COMBINED_STRESS
        else:
            raise BreakoutZoneComparisonError("zone runner received undeclared costs")
        result = self._runner._run_breakout_zone_comparison(
            decision,
            contexts,
            series,
            costs,
            start=decision.identity.observed_at,
            end=end,
            result_label=scenario,
            result_factory=partial(
                BreakoutComparisonReplayResult,
                arm=ComparisonArm.ZONE,
                origin=ResultOrigin.NEW_ZONE_SIMULATION,
            ),
        )
        if not isinstance(result, BreakoutComparisonReplayResult):
            raise BreakoutZoneComparisonError("zone runner returned foreign evidence")
        result.validate()
        return result


def _opportunity_id(key: OpportunityKey) -> str:
    return _integrity_seal("breakout-zone-comparison-opportunity", key, excluded_names=frozenset())


def spacing_mask(population: BreakoutComparisonPopulation, arm: ComparisonArm) -> tuple[str, ...]:
    """Earliest admission wins for 168h, before any outcome is inspected."""

    population.validate()
    decisions = (
        population.baseline_population.decisions
        if arm is ComparisonArm.BASELINE
        else population.zone_decisions
    )
    selected: list[str] = []
    last: datetime | None = None
    for decision, key in zip(decisions, population.opportunity_keys, strict=True):
        authorization = EntryOrderIntentAuthorizer().authorize(decision)
        if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
            continue
        if key is None:
            raise BreakoutZoneComparisonError("admitted decision lacks opportunity identity")
        if last is None or decision.identity.observed_at >= last + FOLLOW_UP:
            selected.append(_opportunity_id(key))
            last = decision.identity.observed_at
    return tuple(selected)


def _admitted(decision: DecisionJournalEntry[object, object]) -> bool:
    return (
        EntryOrderIntentAuthorizer().authorize(decision).status
        is OrderIntentAuthorizationStatus.AUTHORIZED
    )


def _result_metrics(results: tuple[BreakoutComparisonReplayResult, ...]) -> dict[str, object]:
    counts = {status.value: 0 for status in AllFillStatus}
    closed: list[Decimal] = []
    fees = Decimal(0)
    residual: dict[str, Decimal] = {}
    with localcontext(_DECIMAL_CONTEXT):
        for result in results:
            result.validate()
            counts[result.status.value] += 1
            if result.branches:
                branch = result.branches[-1]
                fees += branch.final_position.cumulative_fees
                residual[str(result.decision.identity.symbol)] = (
                    residual.get(str(result.decision.identity.symbol), Decimal(0))
                    + branch.final_position.quantity.copy_abs()
                )
                if branch.closed_net_r is not None:
                    closed.append(branch.closed_net_r)
        ordered = sorted(closed)
        mean = sum(closed, Decimal(0)) / Decimal(len(closed)) if closed else None
        median = (
            None
            if not ordered
            else ordered[len(ordered) // 2]
            if len(ordered) % 2
            else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / Decimal(2)
        )
    return {
        "terminal_counts": counts,
        "closed_denominator": len(closed),
        "mean_closed_net_r": None if mean is None else str(mean),
        "median_closed_net_r": None if median is None else str(median),
        "incurred_fees_quote": str(fees),
        "residual_quantity_by_asset": {key: str(value) for key, value in sorted(residual.items())},
    }


def _result_detail(
    result: BreakoutComparisonReplayResult,
    opportunity_id: str,
) -> dict[str, object]:
    result.validate()
    intent = result.entry_intent
    branch = result.branches[-1] if result.branches else None
    legacy = result.legacy_replay
    entry_fill = None if branch is None else branch.entry_fill
    exit_fill = None if branch is None else branch.exit_fill
    return {
        "opportunity_id": opportunity_id,
        "arm": result.arm.value,
        "scenario": result.scenario.value,
        "decision_at": _iso(result.decision.identity.observed_at),
        "status": result.status.value,
        "side": (None if intent is None else "LONG" if intent.side.value == "BUY" else "SHORT"),
        "reference_price": None if intent is None else str(intent.reference_price),
        "initial_stop": None if intent is None else str(intent.stop_basis),
        "quantity": None if intent is None else str(intent.quantity),
        "approved_notional_quote": (
            None if intent is None else str(intent.approved_notional_quote)
        ),
        "approved_risk_quote": None if intent is None else str(intent.approved_risk_quote),
        "actual_entry_price": None if entry_fill is None else str(entry_fill.fill_price),
        "actual_entry_at": None if entry_fill is None else _iso(entry_fill.fill_at),
        "target_trigger_price": (
            None
            if legacy is None or legacy.target_trigger_price is None
            else str(legacy.target_trigger_price)
        ),
        "actual_exit_price": None if exit_fill is None else str(exit_fill.fill_price),
        "actual_exit_at": None if exit_fill is None else _iso(exit_fill.fill_at),
        "fees_quote": (None if branch is None else str(branch.final_position.cumulative_fees)),
        "gross_realized_pnl_quote": (
            None if branch is None else str(branch.final_position.cumulative_gross_realized_pnl)
        ),
        "net_realized_pnl_quote": (
            None if branch is None else str(branch.final_position.cumulative_net_realized_pnl)
        ),
        "closed_net_approved_risk_r": (
            None if branch is None or branch.closed_net_r is None else str(branch.closed_net_r)
        ),
        "holding_seconds": (
            None
            if entry_fill is None or exit_fill is None
            else int((exit_fill.fill_at - entry_fill.fill_at).total_seconds())
        ),
        "recovery_reasons": (
            () if branch is None or branch.recovery is None else branch.recovery.reasons
        ),
        "residual_quantity": (
            None if branch is None else str(branch.final_position.quantity.copy_abs())
        ),
    }


def _period_week(observed_at: datetime) -> tuple[str, str]:
    for block, start, end in BLOCKS:
        if start < observed_at <= end:
            week = int((observed_at - DECISION_START).total_seconds() // (7 * 86400)) + 1
            return block, f"W{min(week, 9)}"
    raise BreakoutZoneComparisonError("decision falls outside the reporting calendar")


def _grouped_metrics(
    results: tuple[BreakoutComparisonReplayResult, ...],
    *,
    symbol: TradingSymbol,
) -> dict[str, object]:
    groups: dict[str, tuple[BreakoutComparisonReplayResult, ...]] = {}
    groups[f"ASSET:{symbol}"] = results
    for block, _, _ in BLOCKS:
        groups[f"BLOCK:{block}"] = tuple(
            item for item in results if _period_week(item.decision.identity.observed_at)[0] == block
        )
    for week in range(1, 10):
        groups[f"WEEK:W{week}"] = tuple(
            item
            for item in results
            if _period_week(item.decision.identity.observed_at)[1] == f"W{week}"
        )
    for side, entry_side in (("LONG", "BUY"), ("SHORT", "SELL")):
        groups[f"SIDE:{side}"] = tuple(
            item
            for item in results
            if item.entry_intent is not None and item.entry_intent.side.value == entry_side
        )
    for regime in RegimeLabel:
        groups[f"REGIME:{regime.value}"] = tuple(
            item
            for item in results
            if item.decision.context.multi_timeframe_regime.regime_15m.regime_label is regime
        )
    return {key: _result_metrics(value) for key, value in groups.items()}


def _paired_closed_summary(
    population: BreakoutComparisonPopulation,
    keyed: dict[
        tuple[ComparisonArm, AllFillScenario, datetime],
        BreakoutComparisonReplayResult,
    ],
    scenario: AllFillScenario,
) -> dict[str, object]:
    differences: list[Decimal] = []
    unavailable: list[str] = []
    details: list[dict[str, object]] = []
    with localcontext(_DECIMAL_CONTEXT):
        for index, opportunity in enumerate(population.opportunity_keys):
            if opportunity is None:
                continue
            opportunity_id = _opportunity_id(opportunity)
            baseline = keyed[
                (
                    ComparisonArm.BASELINE,
                    scenario,
                    population.baseline_population.decisions[index].identity.observed_at,
                )
            ]
            zone = keyed[
                (
                    ComparisonArm.ZONE,
                    scenario,
                    population.zone_decisions[index].identity.observed_at,
                )
            ]
            baseline_r = baseline.branches[-1].closed_net_r if baseline.branches else None
            zone_r = zone.branches[-1].closed_net_r if zone.branches else None
            if not (_admitted(baseline.decision) and _admitted(zone.decision)):
                unavailable.append(opportunity_id)
                continue
            if baseline_r is None or zone_r is None:
                unavailable.append(opportunity_id)
                continue
            delta = zone_r - baseline_r
            differences.append(delta)
            details.append(
                {
                    "opportunity_id": opportunity_id,
                    "baseline_net_r": str(baseline_r),
                    "zone_net_r": str(zone_r),
                    "zone_minus_baseline_net_r": str(delta),
                }
            )
        mean = None if not differences else sum(differences, Decimal(0)) / Decimal(len(differences))
    return {
        "common_closed_denominator": len(differences),
        "unavailable_or_unclosed_opportunity_ids": tuple(unavailable),
        "mean_zone_minus_baseline_net_r": None if mean is None else str(mean),
        "pairs": tuple(details),
    }


def project_task_report(
    population: BreakoutComparisonPopulation,
    results: tuple[BreakoutComparisonReplayResult, ...],
) -> dict[str, object]:
    """Compact descriptive report; retained result authorities remain required."""

    population.validate()
    expected = len(population.zone_decisions) * 4
    if len(results) != expected:
        raise BreakoutZoneComparisonError("comparison result population is incomplete")
    keyed: dict[
        tuple[ComparisonArm, AllFillScenario, datetime], BreakoutComparisonReplayResult
    ] = {}
    for result in results:
        result.validate()
        key = (result.arm, result.scenario, result.decision.identity.observed_at)
        if key in keyed:
            raise BreakoutZoneComparisonError("comparison result slot is duplicated")
        keyed[key] = result
    baseline = population.baseline_population.decisions
    zone = population.zone_decisions
    for arm, decisions in ((ComparisonArm.BASELINE, baseline), (ComparisonArm.ZONE, zone)):
        for scenario in AllFillScenario:
            for decision in decisions:
                matched = keyed.get((arm, scenario, decision.identity.observed_at))
                if matched is None or matched.decision != decision:
                    raise BreakoutZoneComparisonError(
                        "comparison result differs from decision slot"
                    )
    admitted: dict[ComparisonArm, tuple[str, ...]] = {}
    for arm, decisions in ((ComparisonArm.BASELINE, baseline), (ComparisonArm.ZONE, zone)):
        identities: list[str] = []
        for decision, opportunity in zip(decisions, population.opportunity_keys, strict=True):
            if _admitted(decision):
                if opportunity is None:
                    raise BreakoutZoneComparisonError(
                        "admitted decision lacks opportunity identity"
                    )
                identities.append(_opportunity_id(opportunity))
        admitted[arm] = tuple(identities)
    baseline_set, zone_set = map(set, admitted.values())
    masks = {arm: spacing_mask(population, arm) for arm in ComparisonArm}
    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "task_id": population.task_id,
        "population_sha256": population.integrity_seal,
        "observation_count": len(baseline),
        "candidate_counts": {
            ComparisonArm.BASELINE.value: sum(
                item.strategy_evaluation.action is not CandidateAction.NO_TRADE for item in baseline
            ),
            ComparisonArm.ZONE.value: sum(
                item.strategy_evaluation.action is not CandidateAction.NO_TRADE for item in zone
            ),
        },
        "admission_sets": {
            "baseline": admitted[ComparisonArm.BASELINE],
            "zone": admitted[ComparisonArm.ZONE],
            "common": tuple(sorted(baseline_set & zone_set)),
            "baseline_only": tuple(sorted(baseline_set - zone_set)),
            "zone_only": tuple(sorted(zone_set - baseline_set)),
        },
        "pre_outcome_masks": {
            "baseline": masks[ComparisonArm.BASELINE],
            "zone": masks[ComparisonArm.ZONE],
            "paired_intersection": tuple(
                sorted(set(masks[ComparisonArm.BASELINE]) & set(masks[ComparisonArm.ZONE]))
            ),
        },
        "cells": {},
        "paired_closed": {},
        "promotion_eligibility": "INELIGIBLE_KNOWN_CANDIDATE_BOUND",
        "warnings": (
            "post-result selection-informed descriptive comparison",
            "BASE/STRESS are dependent scenarios over the same decisions",
            "common-closed pairs are a selected subset, not the complete arm population",
            "no portfolio equity, heterogeneous-quantity sum, significance, or promotion claim",
        ),
    }
    cells = cast(dict[str, object], output["cells"])
    details: dict[str, object] = {}
    for arm in ComparisonArm:
        for scenario in AllFillScenario:
            decisions = baseline if arm is ComparisonArm.BASELINE else zone
            cell_results = tuple(
                keyed[(arm, scenario, decision.identity.observed_at)] for decision in decisions
            )
            cell_key = f"{arm.value}:{scenario.value}"
            selected = set(masks[arm])
            masked_results = tuple(
                result
                for result, opportunity in zip(
                    cell_results, population.opportunity_keys, strict=True
                )
                if opportunity is not None and _opportunity_id(opportunity) in selected
            )
            cells[cell_key] = {
                "full": _result_metrics(cell_results),
                "masked": _result_metrics(masked_results),
                "grouped_full": _grouped_metrics(
                    cell_results,
                    symbol=population.baseline_population.configuration.bounds.symbol,
                ),
                "grouped_masked": _grouped_metrics(
                    masked_results,
                    symbol=population.baseline_population.configuration.bounds.symbol,
                ),
            }
            details[cell_key] = tuple(
                _result_detail(result, _opportunity_id(opportunity))
                for result, opportunity in zip(
                    cell_results, population.opportunity_keys, strict=True
                )
                if opportunity is not None and _admitted(result.decision)
            )
    output["admitted_trade_details"] = details
    output["paired_closed"] = {
        scenario.value: _paired_closed_summary(population, keyed, scenario)
        for scenario in AllFillScenario
    }
    output["report_sha256"] = _digest(output)
    return output


def _load_population(repo: Path, task: ComparisonTask) -> ResearchV2StudyDecisionPopulation:
    path = repo / EVIDENCE_ROOT / task.population.path
    _verify_artifact(repo / EVIDENCE_ROOT, task.population)
    try:
        raw = gzip.decompress(path.read_bytes())
        payload = json.loads(raw)
        if _canonical(payload) != raw or payload.get("schema_version") != EVIDENCE_SCHEMA:
            raise BreakoutZoneComparisonError("stored baseline population is noncanonical")
        population = decode_authority(payload["population"])
    except (OSError, ValueError, KeyError, ScreenEvidenceError) as exc:
        raise BreakoutZoneComparisonError(
            "stored baseline population cannot be reconstructed"
        ) from exc
    if not isinstance(population, ResearchV2StudyDecisionPopulation):
        raise BreakoutZoneComparisonError("stored baseline population has foreign authority")
    population.validate()
    if (
        population.configuration.bounds.symbol != task.symbol
        or population.configuration.bounds.context_start != task.context_start
        or population.configuration.bounds.decision_start != DECISION_START
        or population.configuration.bounds.decision_end != DECISION_END
        or population.configuration.bounds.follow_up_end != FOLLOW_UP_END
        or population.configuration.input_binding != ACCEPTED_C2_BINDING
    ):
        raise BreakoutZoneComparisonError("stored baseline population differs from task plan")
    return population


def load_granted_task_population(
    repo: Path,
    task: ComparisonTask,
    grant: ComparisonExecutionGrant,
    *,
    actual_sha: str,
) -> BreakoutComparisonPopulation:
    """Grant is checked before the first retained population/strategy constructor."""

    grant.validate(actual_sha=actual_sha)
    baseline = _load_population(repo.resolve(strict=True), task)
    return build_comparison_population(baseline, task_id=task.task_id)


def _scenario_costs(scenario: AllFillScenario) -> ExecutionCostConfiguration:
    return (
        ExecutionCostConfiguration(Decimal(2), Decimal(2), Decimal(10), timedelta())
        if scenario is AllFillScenario.BASE
        else ExecutionCostConfiguration(Decimal(4), Decimal(4), Decimal(20), timedelta(seconds=1))
    )


def execute_granted_task(
    repo: Path,
    task: ComparisonTask,
    grant: ComparisonExecutionGrant,
    *,
    actual_sha: str,
) -> tuple[BreakoutComparisonPopulation, tuple[BreakoutComparisonReplayResult, ...]]:
    """Future one-shot task route; this preparation issues no real grant."""

    population = load_granted_task_population(repo, task, grant, actual_sha=actual_sha)
    candles = tuple(item.context.candle_15m for item in population.baseline_population.contexts)
    baseline_by_scenario: dict[AllFillScenario, list[AllFillReplayResult]] = {
        scenario: [] for scenario in AllFillScenario
    }
    _verify_artifact(repo / EVIDENCE_ROOT, task.authority)
    authority_path = repo / EVIDENCE_ROOT / task.authority.path
    record_sha256: dict[AllFillScenario, list[str]] = {scenario: [] for scenario in AllFillScenario}
    with gzip.open(authority_path, "rb") as handle:
        for scenario in AllFillScenario:
            for index in range(len(population.baseline_population.decisions)):
                line = handle.readline()
                if not line.endswith(b"\n"):
                    raise BreakoutZoneComparisonError("baseline authority evidence is incomplete")
                record = json.loads(line)
                if _canonical(record) + b"\n" != line:
                    raise BreakoutZoneComparisonError("baseline authority row is noncanonical")
                record_sha256[scenario].append(hashlib.sha256(line[:-1]).hexdigest())
                baseline_by_scenario[scenario].append(
                    recover_authority_result(
                        record,
                        population.baseline_population,
                        index,
                        scenario,
                        candles=candles,
                    )
                )

        if handle.read(1):
            raise BreakoutZoneComparisonError("baseline authority contains undeclared rows")
    source = HistoricalExecutionSource(LockedResearchV2CandleStore(repo))
    zone_runner = BreakoutZoneComparisonRunner(source)
    series = ExecutionSeries(SOURCE_ID, task.symbol, Timeframe.MINUTES_15)
    output: list[BreakoutComparisonReplayResult] = []
    for scenario in AllFillScenario:
        for index, (_baseline_decision, zone_decision) in enumerate(
            zip(
                population.baseline_population.decisions,
                population.zone_decisions,
                strict=True,
            )
        ):
            reconstructed = authority_result_record(
                population.baseline_population,
                index,
                scenario,
                baseline_by_scenario[scenario][index],
                candles=candles,
            )
            if _digest(reconstructed) != record_sha256[scenario][index]:
                raise BreakoutZoneComparisonError(
                    "baseline authority reconstruction differs from retained row"
                )
            output.append(
                reuse_baseline_result(
                    baseline_by_scenario[scenario][index],
                    authority_sha256=task.authority.sha256,
                    record_sha256=record_sha256[scenario][index],
                )
            )
            cutoff = zone_decision.identity.observed_at + FOLLOW_UP
            contexts = tuple(
                item
                for item in population.baseline_population.contexts
                if zone_decision.identity.observed_at < item.context.observed_at < cutoff
            )
            output.append(
                zone_runner.run_zone(
                    zone_decision,
                    contexts,
                    series,
                    _scenario_costs(scenario),
                    end=cutoff,
                )
            )
    return population, tuple(output)


def comparison_result_record(
    result: BreakoutComparisonReplayResult,
    population: BreakoutComparisonPopulation,
    *,
    candles: tuple[Candle, ...],
    candle_opens: tuple[datetime, ...] | None = None,
) -> dict[str, object]:
    result.validate()
    decisions = (
        population.baseline_population.decisions
        if result.arm is ComparisonArm.BASELINE
        else population.zone_decisions
    )
    try:
        index = decisions.index(result.decision)
    except ValueError as exc:
        raise BreakoutZoneComparisonError("result decision is absent from its arm") from exc
    snapshot = result.execution_snapshot
    if candle_opens is None:
        candle_opens = tuple(candle.open_time for candle in candles)
    if len(candle_opens) != len(candles):
        raise BreakoutZoneComparisonError("candle reference index differs from contexts")
    first = 0 if not snapshot else bisect_left(candle_opens, snapshot[0].open_time)
    if candles[first : first + len(snapshot)] != snapshot:
        raise BreakoutZoneComparisonError("result snapshot is absent from retained contexts")
    named = tuple(
        (
            item.name,
            ("decision_ref",)
            if item.name == "decision"
            else ("candle_slice", first, first + len(snapshot))
            if item.name == "execution_snapshot"
            else encode_authority(getattr(result, item.name)),
        )
        for item in fields(result)
    )
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "arm": result.arm.value,
        "scenario": result.scenario.value,
        "decision_index": index,
        "result": (
            "dataclass",
            f"{type(result).__module__}.{type(result).__qualname__}",
            named,
        ),
    }


def recover_comparison_result(
    record: object,
    population: BreakoutComparisonPopulation,
    *,
    candles: tuple[Candle, ...],
) -> BreakoutComparisonReplayResult:
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "arm",
        "scenario",
        "decision_index",
        "result",
    }:
        raise BreakoutZoneComparisonError("comparison authority record shape is invalid")
    try:
        arm = ComparisonArm(record["arm"])
        scenario = AllFillScenario(record["scenario"])
        index = record["decision_index"]
        decisions = (
            population.baseline_population.decisions
            if arm is ComparisonArm.BASELINE
            else population.zone_decisions
        )
        if type(index) is not int or not 0 <= index < len(decisions):
            raise ValueError("decision index")
        decoded = decode_authority(record["result"], decision=decisions[index], candles=candles)
    except (ValueError, KeyError, ScreenEvidenceError) as exc:
        raise BreakoutZoneComparisonError("comparison authority cannot be reconstructed") from exc
    if (
        not isinstance(decoded, BreakoutComparisonReplayResult)
        or decoded.arm is not arm
        or decoded.scenario is not scenario
    ):
        raise BreakoutZoneComparisonError("comparison authority was relabeled")
    decoded.validate()
    return decoded


def fail(message: str) -> NoReturn:
    raise BreakoutZoneComparisonError(message)
