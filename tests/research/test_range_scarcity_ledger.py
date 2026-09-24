"""Synthetic interruption, restart, atomicity, and hash-chain tests only."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from adaptive_trading_bot.research.range_scarcity_ledger import (
    ScarcityLedgerError,
    ScarcityLedgerStore,
    UnitExecutionState,
)

CODE_SHA = "a" * 40
MANIFEST_SHA = "b" * 64
CORPUS_SHA = "c" * 64
REPORT_SHA = "d" * 64
PROJECTION_SHA = "e" * 64
RUN_UUID = "12345678-1234-4234-8234-123456789abc"
START = datetime(2026, 9, 25, tzinfo=UTC)


def _create(root: Path) -> ScarcityLedgerStore:
    return ScarcityLedgerStore.create(
        root,
        frozen_code_sha=CODE_SHA,
        dataset_version="synthetic-version",
        source_id="synthetic-source",
        manifest_sha256=MANIFEST_SHA,
        process_session_id="process-1:session-1",
        created_at=START,
        run_uuid=RUN_UUID,
    )


def test_ledger_created_before_a_and_started_is_not_rerunnable(tmp_path: Path) -> None:
    store = _create(tmp_path)
    initial = store.directory / "0000.json"
    assert initial.is_file()
    assert store.load().units[0].state is UnitExecutionState.NOT_STARTED
    assert store.load().frozen_code_sha == CODE_SHA
    started = store.start_unit(
        "A", current_code_sha=CODE_SHA, process_session_id="process-1:session-1", at=START
    )
    assert (store.directory / "0001.json").is_file()
    assert started.units[0].state is UnitExecutionState.STARTED
    assert started.units[0].started_at == START
    assert started.units[0].process_session_id == "process-1:session-1"
    restarted = ScarcityLedgerStore(store.directory)
    with pytest.raises(ScarcityLedgerError, match="already started"):
        restarted.start_unit(
            "A", current_code_sha=CODE_SHA, process_session_id="process-2", at=START
        )
    with pytest.raises(ScarcityLedgerError, match="prior unit"):
        restarted.start_unit(
            "B", current_code_sha=CODE_SHA, process_session_id="process-2", at=START
        )


def test_failed_or_ambiguous_interruption_cannot_retry(tmp_path: Path) -> None:
    store = _create(tmp_path)
    with pytest.raises(ScarcityLedgerError, match="code SHA"):
        store.start_unit("A", current_code_sha="f" * 40, process_session_id="process-1", at=START)
    assert store.load().revision == 0
    store.start_unit("A", current_code_sha=CODE_SHA, process_session_id="process-1", at=START)
    # A crashed process leaves STARTED durable; no automatic retry on restart.
    restarted = ScarcityLedgerStore(store.directory)
    assert restarted.load().units[0].state is UnitExecutionState.STARTED
    with pytest.raises(ScarcityLedgerError, match="already started"):
        restarted.start_unit(
            "A", current_code_sha=CODE_SHA, process_session_id="process-2", at=START
        )
    failed = restarted.fail_unit("A", at=START + timedelta(seconds=1))
    assert failed.units[0].state is UnitExecutionState.FAILED
    with pytest.raises(ScarcityLedgerError, match="already started"):
        restarted.start_unit(
            "A", current_code_sha=CODE_SHA, process_session_id="process-2", at=START
        )
    with pytest.raises(ScarcityLedgerError, match="prior unit"):
        restarted.start_unit(
            "B", current_code_sha=CODE_SHA, process_session_id="process-2", at=START
        )


def test_completed_unit_and_projection_are_single_use(tmp_path: Path) -> None:
    store = _create(tmp_path)
    for index, run_id in enumerate(("A", "B", "C")):
        at = START + timedelta(minutes=index)
        store.start_unit(
            run_id, current_code_sha=CODE_SHA, process_session_id=f"session-{index}", at=at
        )
        completed = store.complete_unit(
            run_id,
            at=at + timedelta(seconds=1),
            corpus_digest=CORPUS_SHA,
            report_digest=REPORT_SHA,
        )
        assert completed.units[index].state is UnitExecutionState.COMPLETED
        assert completed.units[index].corpus_digest == CORPUS_SHA
        with pytest.raises(ScarcityLedgerError, match="already started"):
            store.start_unit(run_id, current_code_sha=CODE_SHA, process_session_id="retry", at=at)
    finalized = store.finalize(PROJECTION_SHA)
    assert finalized.final_projection_digest == PROJECTION_SHA
    assert finalized.revision == 7
    assert ScarcityLedgerStore(store.directory).load() == finalized
    with pytest.raises(ScarcityLedgerError, match="not ready"):
        store.finalize(PROJECTION_SHA)


def test_snapshot_bytes_hash_chain_and_tamper_detection(tmp_path: Path) -> None:
    first = _create(tmp_path / "one")
    second = _create(tmp_path / "two")
    initial_bytes = (first.directory / "0000.json").read_bytes()
    assert initial_bytes == (second.directory / "0000.json").read_bytes()
    assert first.load().to_json().encode() == initial_bytes
    first.start_unit("A", current_code_sha=CODE_SHA, process_session_id="process-1", at=START)
    started_bytes = (first.directory / "0001.json").read_bytes()
    assert first.load().previous_snapshot_sha256 == hashlib.sha256(initial_bytes).hexdigest()
    assert hashlib.sha256(started_bytes).hexdigest() != hashlib.sha256(initial_bytes).hexdigest()
    tampered = json.loads(initial_bytes)
    tampered["dataset_version"] = "forged"
    (first.directory / "0000.json").write_text(json.dumps(tampered))
    with pytest.raises(ScarcityLedgerError):
        first.load()


def test_interrupted_atomic_write_fails_closed(tmp_path: Path) -> None:
    store = _create(tmp_path)
    pending = store.directory / ".0001.pending"
    pending.write_bytes(b"incomplete")
    with pytest.raises(ScarcityLedgerError, match="sequence"):
        store.load()
    pending.unlink()
    lock = store.directory / ".transition.lock"
    lock.write_text("interrupted")
    with pytest.raises(ScarcityLedgerError, match="interrupted"):
        store.load()


def test_ledger_rejects_naive_and_unknown_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ScarcityLedgerStore.create(
            tmp_path,
            frozen_code_sha=CODE_SHA,
            dataset_version="synthetic-version",
            source_id="synthetic-source",
            manifest_sha256=MANIFEST_SHA,
            process_session_id="session",
            created_at=datetime(2026, 9, 25),
            run_uuid=RUN_UUID,
        )
    store = _create(tmp_path)
    with pytest.raises(ScarcityLedgerError, match="unknown"):
        store.start_unit("D", current_code_sha=CODE_SHA, process_session_id="session", at=START)
