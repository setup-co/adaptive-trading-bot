"""Synthetic-only checks for the separate ETH scarcity continuation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.research import range_scarcity_continuation as continuation
from adaptive_trading_bot.research.range_mean_reversion_corpus import (
    RangeMeanReversionHistoricalResearchCorpus,
)
from adaptive_trading_bot.research.range_mean_reversion_scarcity import (
    FROZEN_DIAGNOSTIC_RUNS,
    SOURCE_ID,
    DiagnosticRunDefinition,
    ObservationAttribution,
    RangeScarcityReport,
    ScarcitySummary,
    TerminalStage,
)
from adaptive_trading_bot.research.range_scarcity_continuation import (
    BTC_A_ANCHOR,
    CONTINUATION_RUNS,
    RangeScarcityUnitRecord,
    ScarcityContinuationError,
    project_anchored_summary,
    verify_locked_a_evidence,
)
from adaptive_trading_bot.research.range_scarcity_continuation_ledger import (
    ContinuationLedgerError,
    ContinuationLedgerStore,
)
from adaptive_trading_bot.research.range_scarcity_ledger import UnitExecutionState
from adaptive_trading_bot.research.weex_historical_corpus import LockedWeexResearchCorpusStore
from adaptive_trading_bot.risk import OperationalSafetyStatus, RiskDecisionStatus
from adaptive_trading_bot.strategies import CandidateAction, RangeMeanReversionReasonCode
from adaptive_trading_bot.strategies.range_context import RangeReasonCode, RangeStatus

CODE_SHA = "a" * 40
DIGEST = "b" * 64
NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _report(run_id: str, *, candidate: bool = False) -> RangeScarcityReport:
    definition = next(item for item in CONTINUATION_RUNS if item.run_id == run_id)
    identity = DecisionIdentity(
        SOURCE_ID,
        definition.symbol,
        definition.start + timedelta(minutes=15),
        "range_mean_reversion",
        "1.0.0",
    )
    row = ObservationAttribution(
        identity,
        True,
        RangeStatus.VALID_RANGE,
        (RangeReasonCode.RANGE_GEOMETRY_VALID,),
        CandidateAction.LONG_CANDIDATE if candidate else CandidateAction.NO_TRADE,
        () if candidate else (RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED,),
        RiskDecisionStatus.APPROVED if candidate else RiskDecisionStatus.NO_ACTION,
        (),
        OperationalSafetyStatus.CLEARED if candidate else OperationalSafetyStatus.NO_ACTION,
        (),
        False,
    )
    return RangeScarcityReport(
        run_id, f"range-mean-reversion-research-corpus:{DIGEST}", DIGEST, (row,)
    )


def _record(report: RangeScarcityReport) -> RangeScarcityUnitRecord:
    definition = next(item for item in CONTINUATION_RUNS if item.run_id == report.run_id)
    full = report.to_json().encode("utf-8")
    return RangeScarcityUnitRecord.from_report(
        report,
        definition,
        source_code_sha=CODE_SHA,
        full_report_sha256=hashlib.sha256(full).hexdigest(),
        full_report_size_bytes=len(full),
    )


def _validate_synthetic_eth_report(
    report: RangeScarcityReport, definition: DiagnosticRunDefinition
) -> None:
    """Keep identity checks when isolating frozen serializer/projection internals."""
    if definition not in CONTINUATION_RUNS or report.run_id != definition.run_id:
        raise ScarcityContinuationError("only exact ETH B/C reports are accepted")
    report.validate()


def test_frozen_eth_boundary_rejects_new_range_policy_before_record_creation() -> None:
    report = _report("B")
    with pytest.raises(ScarcityContinuationError, match="new Range policy"):
        RangeScarcityUnitRecord.from_report(
            report,
            CONTINUATION_RUNS[0],
            source_code_sha=CODE_SHA,
            full_report_sha256="a" * 64,
            full_report_size_bytes=1,
        )


def test_compact_record_uses_report_object_and_keeps_canonical_report_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the serializer with a synthetic report; the frozen entry gate
    # separately rejects this prospective Range policy.
    monkeypatch.setattr(continuation, "_validate_eth_report", _validate_synthetic_eth_report)
    report = _report("B")
    canonical = report.to_json()
    digest = report.report_digest
    seal = report.integrity_seal

    def forbid_json_parse(_raw: str) -> None:
        raise AssertionError("report JSON must not be parsed")

    with monkeypatch.context() as json_guard:
        json_guard.setattr(json, "loads", forbid_json_parse)
        record = _record(report)
    value = json.loads(record.to_json())
    assert isinstance(value, dict)
    assert value["report_digest"] == digest
    assert value["counts"]["total_observations"] == 1
    assert value["counts"]["terminals"]["VALID_RANGE_STRATEGY_NO_TRADE"] == 1
    assert value["counts"]["terminals"]["RESEARCH_ADMISSIBLE"] == 0
    assert value["counts"]["range_reasons"]["RANGE_GEOMETRY_VALID"] == 1
    assert value["counts"]["valid_range_no_trade_reasons"]["RSI_EXHAUSTION_FAILED"] == 1
    assert value["counts"]["actions"]["LONG_CANDIDATE"] == 0
    assert value["counts"]["risk_statuses"]["APPROVED"] == 0
    assert value["counts"]["safety_statuses"]["CLEARED"] == 0
    assert set(value["counts"]["range_statuses"]) == {status.value for status in RangeStatus}
    assert set(value["counts"]["terminals"]) == {stage.value for stage in TerminalStage}
    assert report.to_json() == canonical
    assert (report.report_digest, report.integrity_seal) == (digest, seal)
    assert _record(report).to_json() == record.to_json()
    assert (
        hashlib.sha256(record.to_json().encode()).hexdigest()
        == hashlib.sha256(_record(report).to_json().encode()).hexdigest()
    )


def test_compact_record_rejects_tamper_wrong_run_and_bad_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(continuation, "_validate_eth_report", _validate_synthetic_eth_report)
    report = _report("B")
    record = _record(report)
    object.__setattr__(record, "full_report_size_bytes", 99)
    with pytest.raises(ScarcityContinuationError, match="digest"):
        record.validate()
    with pytest.raises(ScarcityContinuationError, match="ETH B/C"):
        RangeScarcityUnitRecord.from_report(
            report,
            CONTINUATION_RUNS[1],
            source_code_sha=CODE_SHA,
            full_report_sha256=DIGEST,
            full_report_size_bytes=1,
        )
    with pytest.raises(ScarcityContinuationError, match="hash or size"):
        RangeScarcityUnitRecord.from_report(
            report,
            CONTINUATION_RUNS[0],
            source_code_sha=CODE_SHA,
            full_report_sha256=DIGEST,
            full_report_size_bytes=1,
        )
    corrupted = _report("B")
    object.__setattr__(corrupted.counts, "context_ready", 0)
    with pytest.raises(ValueError):
        _record(corrupted)


def test_anchor_and_earlier_evidence_identities_remain_exact() -> None:
    BTC_A_ANCHOR.validate()
    verify_locked_a_evidence()
    assert BTC_A_ANCHOR.total_observations == 52704
    assert BTC_A_ANCHOR.strategy_candidates == 0
    assert BTC_A_ANCHOR.final_research_admissible == 0
    assert BTC_A_ANCHOR.issue_157_ledger_snapshot_sha256 == (
        "b5fe1e2e9a2414f5e5023df1743ddbc870d484c8b4d7818a9d8c8d023a1fc802"
    )
    root = Path(__file__).resolve().parents[2]
    expected = {
        "issue-144-signal-edge-b07e9eac7f373c4b/manifest.json": (
            "e4be5b89615197b9854e9137eae1a75b91c4d37b356d6e696b168c94e7e0d0c9"
        ),
        "issue-149-breakout-retest-4ed85e639a84d282/manifest.json": (
            "730fa5437a7487fd3dd21e4e8f7ade5185a77649fc751b751d406fb7f66505ed"
        ),
        "issue-153-range-mean-reversion-cd1f0858a3c6b4bc/manifest.json": (
            "7ad0be338213acca4e094eb41de4873780d16f850044de76b9423d30360e570a"
        ),
    }
    for relative, sha in expected.items():
        actual = hashlib.sha256(
            (root / "data" / "research_evidence" / relative).read_bytes()
        ).hexdigest()
        assert actual == sha


def test_eth_only_definitions_and_no_executable_btc_path(tmp_path: Path) -> None:
    assert tuple(item.run_id for item in CONTINUATION_RUNS) == ("B", "C")
    assert tuple(item.symbol.base for item in CONTINUATION_RUNS) == ("ETH", "ETH")
    assert CONTINUATION_RUNS[0].start == datetime(2024, 7, 1, tzinfo=UTC)
    assert CONTINUATION_RUNS[0].end == datetime(2024, 12, 25, tzinfo=UTC)
    assert CONTINUATION_RUNS[1].start == datetime(2025, 4, 2, tzinfo=UTC)
    assert CONTINUATION_RUNS[1].end == datetime(2026, 1, 1, tzinfo=UTC)
    ledger = ContinuationLedgerStore.create(
        tmp_path, frozen_code_sha=CODE_SHA, process_session_id="synthetic", created_at=NOW
    )
    with pytest.raises(ContinuationLedgerError, match="only B/C"):
        ledger.start_unit("A", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    with pytest.raises(ScarcityContinuationError, match="only frozen ETH B/C"):
        continuation.run_predeclared_continuation_unit(
            cast(LockedWeexResearchCorpusStore, object()),
            FROZEN_DIAGNOSTIC_RUNS[0],
            ledger,
            tmp_path,
            current_code_sha=CODE_SHA,
            process_session_id="x",
            now=lambda: NOW,
        )
    with pytest.raises(ScarcityContinuationError, match="only frozen ETH B/C"):
        continuation.run_predeclared_continuation_unit(
            cast(LockedWeexResearchCorpusStore, object()),
            DiagnosticRunDefinition(
                "B",
                CONTINUATION_RUNS[0].symbol,
                CONTINUATION_RUNS[0].start,
                CONTINUATION_RUNS[1].end,
            ),
            ledger,
            tmp_path,
            current_code_sha=CODE_SHA,
            process_session_id="x",
            now=lambda: NOW,
        )
    with pytest.raises(ScarcityContinuationError, match="only frozen ETH B/C"):
        continuation.run_predeclared_continuation_unit(
            cast(LockedWeexResearchCorpusStore, object()),
            CONTINUATION_RUNS[0],
            ledger,
            tmp_path,
            current_code_sha=CODE_SHA,
            process_session_id="x",
            now=lambda: NOW,
        )
    assert ledger.load().revision == 0


def test_bc_ledger_order_restart_and_finalization(tmp_path: Path) -> None:
    ledger = ContinuationLedgerStore.create(
        tmp_path, frozen_code_sha=CODE_SHA, process_session_id="synthetic", created_at=NOW
    )
    assert (ledger.directory / "0000.json").exists()
    assert tuple(unit.state for unit in ledger.load().units) == (
        UnitExecutionState.NOT_STARTED,
        UnitExecutionState.NOT_STARTED,
    )
    with pytest.raises(ContinuationLedgerError, match="B must complete"):
        ledger.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    ledger.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    restarted = ContinuationLedgerStore(ledger.directory)
    with pytest.raises(ContinuationLedgerError, match="no rerun"):
        restarted.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="two completed"):
        restarted.finalize(DIGEST)
    restarted.complete_unit(
        "B", at=NOW, corpus_digest=DIGEST, report_digest=DIGEST, compact_record_digest=DIGEST
    )
    assert restarted.load().units[0].compact_record_digest == DIGEST
    restarted.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="no rerun"):
        restarted.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    restarted.complete_unit(
        "C", at=NOW, corpus_digest=DIGEST, report_digest=DIGEST, compact_record_digest=DIGEST
    )
    restarted.finalize(DIGEST)
    assert restarted.load().final_projection_digest == DIGEST
    with pytest.raises(ContinuationLedgerError, match="two completed"):
        restarted.finalize(DIGEST)


def test_bc_failed_and_interrupted_ledger_never_restarts(tmp_path: Path) -> None:
    ledger = ContinuationLedgerStore.create(
        tmp_path, frozen_code_sha=CODE_SHA, process_session_id="synthetic", created_at=NOW
    )
    ledger.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    ledger.fail_unit("B", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="no rerun"):
        ledger.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="B must complete"):
        ledger.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    (ledger.directory / ".transition.lock").write_text("interrupted", encoding="utf-8")
    with pytest.raises(ContinuationLedgerError, match="interrupted"):
        ledger.load()


def test_c_failed_never_restarts(tmp_path: Path) -> None:
    ledger = ContinuationLedgerStore.create(
        tmp_path, frozen_code_sha=CODE_SHA, process_session_id="synthetic", created_at=NOW
    )
    ledger.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    ledger.complete_unit(
        "B", at=NOW, corpus_digest=DIGEST, report_digest=DIGEST, compact_record_digest=DIGEST
    )
    ledger.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    ledger.fail_unit("C", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="no rerun"):
        ledger.start_unit("C", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    with pytest.raises(ContinuationLedgerError, match="two completed"):
        ledger.finalize(DIGEST)


def test_bc_ledger_detects_tamper_and_pending(tmp_path: Path) -> None:
    ledger = ContinuationLedgerStore.create(
        tmp_path, frozen_code_sha=CODE_SHA, process_session_id="synthetic", created_at=NOW
    )
    ledger.start_unit("B", current_code_sha=CODE_SHA, process_session_id="x", at=NOW)
    snapshot = ledger.directory / "0000.json"
    original = snapshot.read_bytes()
    snapshot.write_bytes(original.replace(b"NOT_STARTED", b"STARTED"))
    with pytest.raises(ContinuationLedgerError):
        ledger.load()
    snapshot.write_bytes(original)
    (ledger.directory / ".0002.pending").write_text("interrupted", encoding="utf-8")
    with pytest.raises(ContinuationLedgerError, match="ambiguous"):
        ledger.load()


def test_anchored_projection_uses_only_anchor_for_a_and_reports_for_bc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(continuation, "_validate_eth_report", _validate_synthetic_eth_report)
    b_report = _report("B", candidate=True)
    c_report = _report("C")
    b_record = _record(b_report)
    c_record = _record(c_report)
    monkeypatch.setattr(continuation, "validate_frozen_unit", lambda *_args: None)
    store = cast(LockedWeexResearchCorpusStore, object())
    corpus = cast(RangeMeanReversionHistoricalResearchCorpus, object())
    units = ((store, corpus, b_report, b_record), (store, corpus, c_report, c_record))
    projection = project_anchored_summary(units)
    assert projection.total_observations == (52704, 1, 1)
    assert projection.strategy_candidate_counts == (0, 1, 0)
    assert projection.final_research_admissible_counts == (0, 1, 0)
    assert projection.summary is ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST
    assert "ISSUE_157_ANCHOR" in projection.provenance[0]
    assert "CONTINUATION_V1" in projection.provenance[1]
    assert projection.report_digests[0] == BTC_A_ANCHOR.report_digest
    projection.validate()
    projected = json.loads(projection.to_json())
    assert projected["provenance"] == list(projection.provenance)
    assert projected["summary"] == ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST.value
    with pytest.raises(ScarcityContinuationError):
        replace(projection, anchor_integrity_seal=DIGEST)
    with pytest.raises(ScarcityContinuationError):
        project_anchored_summary(((store, corpus, c_report, c_record), units[1]))


def test_no_economic_or_execution_dependencies_in_continuation_modules() -> None:
    source = Path(continuation.__file__).read_text(encoding="utf-8")
    ledger_source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "adaptive_trading_bot"
        / "research"
        / "range_scarcity_continuation_ledger.py"
    )
    source += ledger_source.read_text(encoding="utf-8")
    for forbidden in (
        "BacktestExecutionSimulator",
        "PositionAccountingEngine",
        "realized_pnl",
        "win_rate",
        "profit_factor",
    ):
        assert forbidden not in source
