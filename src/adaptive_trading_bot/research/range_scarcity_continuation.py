"""ETH-only, non-economic continuation of the sealed scarcity diagnostic."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hmac import compare_digest
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adaptive_trading_bot.research.range_scarcity_continuation_ledger import (
        ContinuationLedgerStore,
    )

from adaptive_trading_bot.research.range_mean_reversion_corpus import (
    RangeMeanReversionHistoricalCorpusGenerator,
    RangeMeanReversionHistoricalResearchCorpus,
)
from adaptive_trading_bot.research.range_mean_reversion_scarcity import (
    DATASET_VERSION,
    FROZEN_DIAGNOSTIC_RUNS,
    FROZEN_SCHEMA_VERSION,
    MANIFEST_SHA256,
    SOURCE_ID,
    DiagnosticRunDefinition,
    RangeScarcityReport,
    ScarcityCounts,
    ScarcitySummary,
    TerminalStage,
    _frozen_configuration,
    classify_summary,
    project_corpus,
    validate_frozen_unit,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _integrity_seal,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    QUALIFIED_DATASET_MANIFEST_SHA256,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskReasonCode,
)
from adaptive_trading_bot.strategies import CandidateAction, RangeMeanReversionReasonCode
from adaptive_trading_bot.strategies.range_context import RangeReasonCode, RangeStatus

CONTINUATION_PROTOCOL_VERSION = "RANGE_MEAN_REVERSION_CANDIDATE_SCARCITY_CONTINUATION_V1"
COMPACT_RECORD_SCHEMA_VERSION = "range-scarcity-compact-record-v1"
ANCHORED_PROJECTION_SCHEMA_VERSION = "range-scarcity-anchored-projection-v1"
CONTINUATION_RUNS = (FROZEN_DIAGNOSTIC_RUNS[1], FROZEN_DIAGNOSTIC_RUNS[2])
CONTINUATION_EVIDENCE_PREFIX = "range-scarcity-continuation-v1-"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_A_EVIDENCE_DIRECTORY = "issue-157-range-scarcity-95b2ba209d121cd6"
_A_EXPECTED: dict[str, object] = {
    "run_id": "A",
    "symbol": "BTC/USDT",
    "decision_start": "2024-07-01T00:00:00+00:00",
    "decision_end": "2026-01-01T00:00:00+00:00",
    "source_execution_sha": "95b2ba209d121cd61a59c3730c36f197ec0690d7",
    "issue_157_ledger_uuid": "b6b78aa4-3687-44de-9568-b3434f97c075",
    "issue_157_ledger_snapshot_sha256": (
        "b5fe1e2e9a2414f5e5023df1743ddbc870d484c8b4d7818a9d8c8d023a1fc802"
    ),
    "issue_157_manifest_sha256": "431001f600525cb846b368ebf1a041cdbf1221bed8749ed7a6e21753a73f10e9",
    "issue_157_index_sha256": "8ef4a5cb126528729e0f25ed958564db4f0df4180292046e37733b7f16660552",
    "corpus_digest": "e89f55f5ba89869773a82f2411e295de09ba7fb6a5ef449d52fce6f1136ea60e",
    "report_digest": "9a856fdb0c2600a87172c2ab795164c8c45a10a4861fb50c3b2c9e750ddd9b7f",
    "report_sha256": "ee604aaaa068d9fc085d6e602ad0a152d7924bc03450b11001232363bce26f60",
    "record_sha256": "a5e7977cc8485c4a13203ef06308c921c746a5ef7c54cc4c19cd0d6dbdd45e75",
    "total_observations": 52704,
    "strategy_candidates": 0,
    "final_research_admissible": 0,
    "terminal_counts": (
        (TerminalStage.CONTEXT_NOT_READY, 799),
        (TerminalStage.RANGE_NOT_VALID, 51904),
        (TerminalStage.VALID_RANGE_STRATEGY_NO_TRADE, 1),
        (TerminalStage.CANDIDATE_RISK_BLOCKED, 0),
        (TerminalStage.CANDIDATE_SAFETY_BLOCKED, 0),
        (TerminalStage.CANDIDATE_WARMUP_EXCLUDED, 0),
        (TerminalStage.RESEARCH_ADMISSIBLE, 0),
    ),
}


class ScarcityContinuationError(ValueError):
    """The ETH-only continuation or frozen BTC anchor failed closed."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenBtcAAnchor:
    """Accepted #157 A facts, not a reconstructed or executable report."""

    run_id: str = "A"
    symbol: str = "BTC/USDT"
    decision_start: str = "2024-07-01T00:00:00+00:00"
    decision_end: str = "2026-01-01T00:00:00+00:00"
    source_execution_sha: str = "95b2ba209d121cd61a59c3730c36f197ec0690d7"
    issue_157_ledger_uuid: str = "b6b78aa4-3687-44de-9568-b3434f97c075"
    issue_157_ledger_snapshot_sha256: str = (
        "b5fe1e2e9a2414f5e5023df1743ddbc870d484c8b4d7818a9d8c8d023a1fc802"
    )
    issue_157_manifest_sha256: str = (
        "431001f600525cb846b368ebf1a041cdbf1221bed8749ed7a6e21753a73f10e9"
    )
    issue_157_index_sha256: str = "8ef4a5cb126528729e0f25ed958564db4f0df4180292046e37733b7f16660552"
    corpus_digest: str = "e89f55f5ba89869773a82f2411e295de09ba7fb6a5ef449d52fce6f1136ea60e"
    report_digest: str = "9a856fdb0c2600a87172c2ab795164c8c45a10a4861fb50c3b2c9e750ddd9b7f"
    report_sha256: str = "ee604aaaa068d9fc085d6e602ad0a152d7924bc03450b11001232363bce26f60"
    record_sha256: str = "a5e7977cc8485c4a13203ef06308c921c746a5ef7c54cc4c19cd0d6dbdd45e75"
    total_observations: int = 52704
    strategy_candidates: int = 0
    final_research_admissible: int = 0
    terminal_counts: tuple[tuple[TerminalStage, int], ...] = (
        (TerminalStage.CONTEXT_NOT_READY, 799),
        (TerminalStage.RANGE_NOT_VALID, 51904),
        (TerminalStage.VALID_RANGE_STRATEGY_NO_TRADE, 1),
        (TerminalStage.CANDIDATE_RISK_BLOCKED, 0),
        (TerminalStage.CANDIDATE_SAFETY_BLOCKED, 0),
        (TerminalStage.CANDIDATE_WARMUP_EXCLUDED, 0),
        (TerminalStage.RESEARCH_ADMISSIBLE, 0),
    )
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-scarcity-btc-a-anchor", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate_facts(self) -> None:
        if any(getattr(self, name) != expected for name, expected in _A_EXPECTED.items()):
            raise ScarcityContinuationError("BTC A anchor differs from accepted #157 facts")
        if sum(count for _, count in self.terminal_counts) != self.total_observations:
            raise ScarcityContinuationError("BTC A terminal counts do not reconcile")

    def validate(self) -> None:
        self.validate_facts()
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-scarcity-btc-a-anchor", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise ScarcityContinuationError("BTC A anchor seal mismatch")


BTC_A_ANCHOR = FrozenBtcAAnchor()


def verify_locked_a_evidence(
    anchor: FrozenBtcAAnchor = BTC_A_ANCHOR, *, repository_root: Path | None = None
) -> None:
    """Hash only #157's small committed evidence, never load/re-execute A."""
    anchor.validate()
    root = repository_root or Path(__file__).resolve().parents[3]
    evidence = root / "data" / "research_evidence" / _A_EVIDENCE_DIRECTORY
    paths = {
        "issue_157_ledger_snapshot_sha256": evidence
        / "ledger"
        / anchor.issue_157_ledger_uuid
        / "0002.json",
        "issue_157_manifest_sha256": evidence / "manifest.json",
        "issue_157_index_sha256": evidence / "evidence-index.json",
        "record_sha256": evidence / "run-a-record.json",
    }
    if any(_sha256_file(path) != getattr(anchor, name) for name, path in paths.items()):
        raise ScarcityContinuationError("committed #157 A evidence hash differs")
    ledger = json.loads(paths["issue_157_ledger_snapshot_sha256"].read_text(encoding="utf-8"))
    record = json.loads(paths["record_sha256"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["issue_157_manifest_sha256"].read_text(encoding="utf-8"))
    index = json.loads(paths["issue_157_index_sha256"].read_text(encoding="utf-8"))
    if (
        ledger["run_uuid"] != anchor.issue_157_ledger_uuid
        or ledger["revision"] != 2
        or [unit["state"] for unit in ledger["units"]]
        != ["COMPLETED", "NOT_STARTED", "NOT_STARTED"]
        or ledger["units"][0]["corpus_digest"] != anchor.corpus_digest
        or ledger["units"][0]["report_digest"] != anchor.report_digest
        or record["counts"]["total_observations"] != anchor.total_observations
        or record["counts"]["final_research_admissible"] != anchor.final_research_admissible
        or tuple((TerminalStage(name), count) for name, count in record["counts"]["terminals"])
        != anchor.terminal_counts
        or record["report_sha256"] != anchor.report_sha256
        or record["report_digest"] != anchor.report_digest
        or index["captured_execution_manifest_sha256"] != anchor.issue_157_manifest_sha256
        or not any(
            item["path"] == "run-a-report.json" and item["sha256"] == anchor.report_sha256
            for item in manifest["files"]
        )
    ):
        raise ScarcityContinuationError("committed #157 A evidence contradicts anchor")


def _enum_counts(values: tuple[tuple[Enum, int], ...], kinds: tuple[Enum, ...]) -> dict[str, int]:
    if tuple(kind for kind, _ in values) != kinds or any(
        type(count) is not int or count < 0 for _, count in values
    ):
        raise ScarcityContinuationError("compact record bucket coverage differs")
    return {str(kind.value): count for kind, count in values}


def _counts_object(counts: ScarcityCounts) -> dict[str, object]:
    if (
        counts.context_ready + counts.context_not_ready != counts.total_observations
        or counts.candidates_inside_warmup + counts.candidates_outside_warmup
        != counts.strategy_candidate_count
        or counts.otherwise_admissible_warmup_excluded + counts.final_research_admissible
        != counts.otherwise_admissible_before_warmup
        or sum(count for _, count in counts.terminals) != counts.total_observations
    ):
        raise ScarcityContinuationError("compact record counts do not reconcile")
    months = counts.candidate_utc_months
    if tuple(month for month, _ in months) != tuple(sorted(set(month for month, _ in months))):
        raise ScarcityContinuationError("candidate month ordering differs")
    if any(type(count) is not int or count < 0 for _, count in months):
        raise ScarcityContinuationError("candidate month count is invalid")
    return {
        "total_observations": counts.total_observations,
        "context_ready": counts.context_ready,
        "context_not_ready": counts.context_not_ready,
        "range_statuses": _enum_counts(counts.range_statuses, tuple(RangeStatus)),
        "range_reasons": _enum_counts(counts.range_reasons, tuple(RangeReasonCode)),
        "actions": _enum_counts(counts.actions, tuple(CandidateAction)),
        "no_trade_reasons": _enum_counts(
            counts.no_trade_reasons, tuple(RangeMeanReversionReasonCode)
        ),
        "valid_range_no_trade_reasons": _enum_counts(
            counts.valid_range_no_trade_reasons, tuple(RangeMeanReversionReasonCode)
        ),
        "strategy_candidate_count": counts.strategy_candidate_count,
        "candidate_utc_months": {month: count for month, count in months},
        "candidates_inside_warmup": counts.candidates_inside_warmup,
        "candidates_outside_warmup": counts.candidates_outside_warmup,
        "risk_statuses": _enum_counts(
            counts.risk_statuses,
            (RiskDecisionStatus.APPROVED, RiskDecisionStatus.REJECTED),
        ),
        "safety_statuses": _enum_counts(
            counts.safety_statuses,
            (
                OperationalSafetyStatus.CLEARED,
                OperationalSafetyStatus.SAFE_MODE,
                OperationalSafetyStatus.BLOCKED_BY_RISK,
            ),
        ),
        "risk_reasons": _enum_counts(counts.risk_reasons, tuple(RiskReasonCode)),
        "safety_reasons": _enum_counts(counts.safety_reasons, tuple(OperationalSafetyReasonCode)),
        "otherwise_admissible_before_warmup": counts.otherwise_admissible_before_warmup,
        "otherwise_admissible_warmup_excluded": counts.otherwise_admissible_warmup_excluded,
        "final_research_admissible": counts.final_research_admissible,
        "terminals": _enum_counts(counts.terminals, tuple(TerminalStage)),
    }


@dataclass(frozen=True, slots=True)
class RangeScarcityUnitRecord:
    """Compact object-shaped evidence copied from a validated report's counts."""

    run_id: str
    symbol: str
    decision_start: str
    decision_end: str
    source_code_sha: str
    corpus_id: str
    corpus_digest: str
    report_digest: str
    full_report_sha256: str
    full_report_size_bytes: int
    counts: ScarcityCounts
    schema_version: str = COMPACT_RECORD_SCHEMA_VERSION
    protocol_version: str = CONTINUATION_PROTOCOL_VERSION
    source_id: str = SOURCE_ID.value
    dataset_version: str = DATASET_VERSION
    manifest_sha256: str = MANIFEST_SHA256
    record_digest: str = ""

    def __post_init__(self) -> None:
        self._validate_facts()
        expected = hashlib.sha256(self._body_json().encode("utf-8")).hexdigest()
        if self.record_digest and self.record_digest != expected:
            raise ScarcityContinuationError("compact record digest mismatch")
        object.__setattr__(self, "record_digest", expected)

    @classmethod
    def from_report(
        cls,
        report: RangeScarcityReport,
        definition: DiagnosticRunDefinition,
        *,
        source_code_sha: str,
        full_report_sha256: str,
        full_report_size_bytes: int,
    ) -> RangeScarcityUnitRecord:
        _validate_eth_report(report, definition)
        canonical_full_report = report.to_json().encode("utf-8")
        if (
            hashlib.sha256(canonical_full_report).hexdigest() != full_report_sha256
            or len(canonical_full_report) != full_report_size_bytes
        ):
            raise ScarcityContinuationError("full report artifact hash or size differs")
        record = cls(
            definition.run_id,
            f"{definition.symbol.base}/{definition.symbol.quote}",
            definition.start.isoformat(),
            definition.end.isoformat(),
            source_code_sha,
            report.corpus_id,
            report.corpus_digest,
            report.report_digest,
            full_report_sha256,
            full_report_size_bytes,
            report.counts,
        )
        record.validate_against(report, definition)
        return record

    def _validate_facts(self) -> None:
        definition = next((item for item in CONTINUATION_RUNS if item.run_id == self.run_id), None)
        if (
            definition is None
            or self.symbol != f"{definition.symbol.base}/{definition.symbol.quote}"
            or self.decision_start != definition.start.isoformat()
            or self.decision_end != definition.end.isoformat()
            or not _HEX_SHA1.fullmatch(self.source_code_sha)
            or not _HEX_SHA256.fullmatch(self.corpus_digest)
            or self.corpus_id != f"range-mean-reversion-research-corpus:{self.corpus_digest}"
            or not _HEX_SHA256.fullmatch(self.report_digest)
            or not _HEX_SHA256.fullmatch(self.full_report_sha256)
            or type(self.full_report_size_bytes) is not int
            or self.full_report_size_bytes <= 0
            or self.schema_version != COMPACT_RECORD_SCHEMA_VERSION
            or self.protocol_version != CONTINUATION_PROTOCOL_VERSION
            or self.source_id != SOURCE_ID.value
            or self.dataset_version != DATASET_VERSION
            or self.manifest_sha256 != MANIFEST_SHA256
        ):
            raise ScarcityContinuationError("compact record provenance is invalid")
        _counts_object(self.counts)

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "run_id": self.run_id,
            "symbol": self.symbol,
            "decision_start": self.decision_start,
            "decision_end": self.decision_end,
            "source_code_sha": self.source_code_sha,
            "source_id": self.source_id,
            "dataset_version": self.dataset_version,
            "manifest_sha256": self.manifest_sha256,
            "corpus_id": self.corpus_id,
            "corpus_digest": self.corpus_digest,
            "report_digest": self.report_digest,
            "full_report_sha256": self.full_report_sha256,
            "full_report_size_bytes": self.full_report_size_bytes,
            "counts": _counts_object(self.counts),
        }

    def _body_json(self) -> str:
        return json.dumps(self._body(), sort_keys=True, separators=(",", ":"))

    def validate(self) -> None:
        self._validate_facts()
        if not compare_digest(
            self.record_digest, hashlib.sha256(self._body_json().encode("utf-8")).hexdigest()
        ):
            raise ScarcityContinuationError("compact record digest mismatch")

    def validate_against(
        self, report: RangeScarcityReport, definition: DiagnosticRunDefinition
    ) -> None:
        self.validate()
        _validate_eth_report(report, definition)
        if (
            self.run_id != report.run_id
            or self.corpus_id != report.corpus_id
            or self.corpus_digest != report.corpus_digest
            or self.report_digest != report.report_digest
            or self.counts != report.counts
        ):
            raise ScarcityContinuationError("compact record differs from sealed report")

    def to_json(self) -> str:
        self.validate()
        return (
            json.dumps(
                {**self._body(), "record_digest": self.record_digest},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


def _validate_eth_report(report: RangeScarcityReport, definition: DiagnosticRunDefinition) -> None:
    if definition not in CONTINUATION_RUNS or report.run_id != definition.run_id:
        raise ScarcityContinuationError("only exact ETH B/C reports are accepted")
    if report.schema_version != FROZEN_SCHEMA_VERSION:
        raise ScarcityContinuationError("new Range policy is not frozen ETH evidence")
    report.validate()
    if any(
        row.identity.symbol != definition.symbol
        or row.identity.source_id != SOURCE_ID
        or not definition.start < row.identity.observed_at <= definition.end
        or row.identity.strategy_id != "range_mean_reversion"
        or row.identity.strategy_version != "1.0.0"
        for row in report.observations
    ):
        raise ScarcityContinuationError("ETH report observations differ from frozen unit")


@dataclass(frozen=True, slots=True)
class AnchoredScarcityProjection:
    """A-only #157 authority plus separately executed ETH B/C authority."""

    anchor_integrity_seal: str
    report_digests: tuple[str, str, str]
    total_observations: tuple[int, int, int]
    terminal_counts: tuple[tuple[tuple[TerminalStage, int], ...], ...]
    strategy_candidate_counts: tuple[int, int, int]
    final_research_admissible_counts: tuple[int, int, int]
    summary: ScarcitySummary
    provenance: tuple[str, str, str] = (
        "A:IMMUTABLE_COMPLETED_ISSUE_157_ANCHOR",
        "B:SEPARATELY_AUTHORIZED_CONTINUATION_V1",
        "C:SEPARATELY_AUTHORIZED_CONTINUATION_V1",
    )
    run_ids: tuple[str, str, str] = ("A", "B", "C")
    schema_version: str = ANCHORED_PROJECTION_SCHEMA_VERSION
    protocol_version: str = CONTINUATION_PROTOCOL_VERSION
    integrity_seal: str = ""

    def __post_init__(self) -> None:
        self._validate_facts()
        expected = _integrity_seal(
            "range-scarcity-anchored-projection",
            self,
            excluded_names=frozenset({"integrity_seal"}),
        )
        if self.integrity_seal and self.integrity_seal != expected:
            raise ScarcityContinuationError("anchored projection seal mismatch")
        object.__setattr__(self, "integrity_seal", expected)

    def _validate_facts(self) -> None:
        BTC_A_ANCHOR.validate()
        if (
            self.anchor_integrity_seal != BTC_A_ANCHOR.integrity_seal
            or self.run_ids != ("A", "B", "C")
            or self.provenance
            != (
                "A:IMMUTABLE_COMPLETED_ISSUE_157_ANCHOR",
                "B:SEPARATELY_AUTHORIZED_CONTINUATION_V1",
                "C:SEPARATELY_AUTHORIZED_CONTINUATION_V1",
            )
            or self.schema_version != ANCHORED_PROJECTION_SCHEMA_VERSION
            or self.protocol_version != CONTINUATION_PROTOCOL_VERSION
            or self.report_digests[0] != BTC_A_ANCHOR.report_digest
            or self.total_observations[0] != BTC_A_ANCHOR.total_observations
            or self.terminal_counts[0] != BTC_A_ANCHOR.terminal_counts
            or self.strategy_candidate_counts[0] != BTC_A_ANCHOR.strategy_candidates
            or self.final_research_admissible_counts[0] != BTC_A_ANCHOR.final_research_admissible
            or any(not _HEX_SHA256.fullmatch(item) for item in self.report_digests)
            or any(type(value) is not int or value < 0 for value in self.total_observations)
            or any(type(value) is not int or value < 0 for value in self.strategy_candidate_counts)
            or any(
                type(value) is not int or value < 0
                for value in self.final_research_admissible_counts
            )
            or any(
                tuple(stage for stage, _ in row) != tuple(TerminalStage)
                or any(type(value) is not int or value < 0 for _, value in row)
                for row in self.terminal_counts
            )
            or any(
                sum(count for _, count in row) != total
                for row, total in zip(self.terminal_counts, self.total_observations, strict=True)
            )
            or any(
                dict(row)[TerminalStage.RESEARCH_ADMISSIBLE] != admitted
                for row, admitted in zip(
                    self.terminal_counts, self.final_research_admissible_counts, strict=True
                )
            )
            or any(
                admitted > candidate
                for admitted, candidate in zip(
                    self.final_research_admissible_counts,
                    self.strategy_candidate_counts,
                    strict=True,
                )
            )
            or self.summary
            is not classify_summary(
                sum(self.strategy_candidate_counts), sum(self.final_research_admissible_counts)
            )
        ):
            raise ScarcityContinuationError("anchored projection facts do not reconcile")

    def validate(self) -> None:
        self._validate_facts()
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-scarcity-anchored-projection",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise ScarcityContinuationError("anchored projection seal mismatch")

    def to_json(self) -> str:
        self.validate()
        return (
            json.dumps(
                {
                    "schema_version": self.schema_version,
                    "protocol_version": self.protocol_version,
                    "anchor_integrity_seal": self.anchor_integrity_seal,
                    "run_ids": self.run_ids,
                    "provenance": self.provenance,
                    "report_digests": self.report_digests,
                    "total_observations": self.total_observations,
                    "terminal_counts": [
                        {stage.value: count for stage, count in row} for row in self.terminal_counts
                    ],
                    "strategy_candidate_counts": self.strategy_candidate_counts,
                    "final_research_admissible_counts": self.final_research_admissible_counts,
                    "summary": self.summary.value,
                    "integrity_seal": self.integrity_seal,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


type ContinuationUnit = tuple[
    LockedWeexResearchCorpusStore,
    RangeMeanReversionHistoricalResearchCorpus,
    RangeScarcityReport,
    RangeScarcityUnitRecord,
]


def project_anchored_summary(
    units: tuple[ContinuationUnit, ContinuationUnit],
    *,
    anchor: FrozenBtcAAnchor = BTC_A_ANCHOR,
) -> AnchoredScarcityProjection:
    anchor.validate()
    verify_locked_a_evidence(anchor)
    for definition, (store, corpus, report, record) in zip(CONTINUATION_RUNS, units, strict=True):
        validate_frozen_unit(store, corpus, report, definition)
        record.validate_against(report, definition)
        if record.source_code_sha != units[0][3].source_code_sha:
            raise ScarcityContinuationError("B/C continuation code SHA differs")
    b_report, c_report = units[0][2], units[1][2]
    candidates = (
        anchor.strategy_candidates,
        b_report.counts.strategy_candidate_count,
        c_report.counts.strategy_candidate_count,
    )
    admitted = (
        anchor.final_research_admissible,
        b_report.counts.final_research_admissible,
        c_report.counts.final_research_admissible,
    )
    return AnchoredScarcityProjection(
        anchor.integrity_seal,
        (anchor.report_digest, b_report.report_digest, c_report.report_digest),
        (
            anchor.total_observations,
            b_report.counts.total_observations,
            c_report.counts.total_observations,
        ),
        (anchor.terminal_counts, b_report.counts.terminals, c_report.counts.terminals),
        candidates,
        admitted,
        classify_summary(sum(candidates), sum(admitted)),
    )


def _durable_write_new(path: Path, payload: bytes) -> None:
    if not path.parent.is_dir() or path.exists():
        raise ScarcityContinuationError("continuation evidence target is missing or exists")
    pending = path.with_name(f".{path.name}.pending")
    if pending.exists():
        raise ScarcityContinuationError("continuation evidence has an interrupted write")
    with pending.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def run_predeclared_continuation_unit(
    store: LockedWeexResearchCorpusStore,
    definition: DiagnosticRunDefinition,
    ledger: ContinuationLedgerStore,
    evidence_directory: Path,
    *,
    current_code_sha: str,
    process_session_id: str,
    now: Callable[[], datetime],
) -> ContinuationUnit:
    """Future-only: durably start ETH B/C, write both artifacts, then complete."""
    if definition not in CONTINUATION_RUNS or not isinstance(store, LockedWeexResearchCorpusStore):
        raise ScarcityContinuationError("continuation may execute only frozen ETH B/C")
    if (
        store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
        or not evidence_directory.is_dir()
        or not evidence_directory.name.startswith(CONTINUATION_EVIDENCE_PREFIX)
    ):
        raise ScarcityContinuationError("locked continuation source or evidence path differs")
    verify_locked_a_evidence()
    state = ledger.load()
    if (
        state.anchor_integrity_seal != BTC_A_ANCHOR.integrity_seal
        or state.dataset_version != DATASET_VERSION
        or state.source_id != SOURCE_ID.value
        or state.manifest_sha256 != MANIFEST_SHA256
        or state.frozen_code_sha != current_code_sha
    ):
        raise ScarcityContinuationError("continuation ledger authority differs")
    configuration = _frozen_configuration(store, definition)
    ledger.start_unit(
        definition.run_id,
        current_code_sha=current_code_sha,
        process_session_id=process_session_id,
        at=now(),
    )
    try:
        corpus = RangeMeanReversionHistoricalCorpusGenerator(store).generate(configuration)
        report = project_corpus(corpus, definition.run_id)
        validate_frozen_unit(store, corpus, report, definition)
        full_report = report.to_json().encode("utf-8")
        report_sha = hashlib.sha256(full_report).hexdigest()
        record = RangeScarcityUnitRecord.from_report(
            report,
            definition,
            source_code_sha=current_code_sha,
            full_report_sha256=report_sha,
            full_report_size_bytes=len(full_report),
        )
        _durable_write_new(
            evidence_directory / f"run-{definition.run_id.lower()}-report.json", full_report
        )
        _durable_write_new(
            evidence_directory / f"run-{definition.run_id.lower()}-record.json",
            record.to_json().encode("utf-8"),
        )
        ledger.complete_unit(
            definition.run_id,
            at=now(),
            corpus_digest=corpus.corpus_digest,
            report_digest=report.report_digest,
            compact_record_digest=record.record_digest,
        )
        return store, corpus, report, record
    except Exception:
        # An interrupted failure write leaves STARTED durable and non-retryable.
        ledger.fail_unit(definition.run_id, at=now())
        raise


def finalize_continuation(
    ledger: ContinuationLedgerStore,
    units: tuple[ContinuationUnit, ContinuationUnit],
) -> AnchoredScarcityProjection:
    projection = project_anchored_summary(units)
    state = ledger.load()
    from adaptive_trading_bot.research.range_scarcity_ledger import UnitExecutionState

    if any(unit.state is not UnitExecutionState.COMPLETED for unit in state.units):
        raise ScarcityContinuationError("B/C continuation has unfinished units")
    for logged, (_, corpus, report, record) in zip(state.units, units, strict=True):
        if (
            logged.corpus_digest != corpus.corpus_digest
            or logged.report_digest != report.report_digest
            or logged.compact_record_digest != record.record_digest
            or record.source_code_sha != state.frozen_code_sha
        ):
            raise ScarcityContinuationError("B/C ledger digests differ from sealed units")
    ledger.finalize(projection.integrity_seal)
    return projection
