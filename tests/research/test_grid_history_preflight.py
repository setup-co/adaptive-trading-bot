from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.research.grid_history_preflight import (
    ACCEPTED_CALENDAR_SHA256,
    ACCEPTED_QUALIFICATION_PROTOCOL,
    ACCEPTED_ROW_SCHEMA,
    ACCEPTED_V3_IDENTITY,
    STUDY_PLAN_SHA256,
    CalendarEpisode,
    CalendarStatus,
    GridCalendarLedger,
    GridHistoryPreflightError,
    V3GridHistoryAdapter,
    V3SourceIdentity,
    load_calendar,
    metadata_only_preflight,
    refuse_historical_execution,
)
from adaptive_trading_bot.research.grid_v1 import (
    GridEpisodeInput,
    GridScenario,
    GridV1Engine,
    healthy_grid_health,
    verified_grid_contract_identity,
)
from adaptive_trading_bot.storage import SourceId

REPO = Path(__file__).resolve().parents[2]
DATASET = "fixture-binance-grid-v3"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_table(
    root: Path,
    component: str,
    rows: list[dict[str, object]],
    *,
    symbol: str = "BTCUSDT",
    month: str = "2026-01",
) -> tuple[str, dict[str, object]]:
    relative = f"derived-generations/v3/normalized/{component}/{symbol}/{month}.parquet"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows).replace_schema_metadata(
        {
            b"schema": ACCEPTED_ROW_SCHEMA.encode(),
            b"dataset_version": DATASET.encode(),
            b"qualification_protocol": ACCEPTED_QUALIFICATION_PROTOCOL.encode(),
            b"component": component.encode(),
            b"symbol": symbol.encode(),
            b"month": month.encode(),
        }
    )
    pq.write_table(table, path)
    return relative, {"bytes": path.stat().st_size, "rows": len(rows), "sha256": _sha(path)}


def _fixture(root: Path) -> tuple[V3SourceIdentity, CalendarEpisode]:
    start = datetime(2026, 1, 12, tzinfo=UTC)
    end = start + timedelta(days=6, hours=20, minutes=2)
    files: dict[str, dict[str, object]] = {}
    derived: list[dict[str, object]] = []
    for index in range(42):
        available = start - timedelta(hours=4 * (41 - index))
        derived.append(
            {
                "open_time_ms": int((available - timedelta(hours=4)).timestamp() * 1000),
                "close_time_ms": int(available.timestamp() * 1000),
                "open": "100",
                "high": "100",
                "low": "100",
                "close": "100",
                "volume": "1",
                "quote_volume": "100",
                "source_minutes": 240,
                "available_at_ms": int(available.timestamp() * 1000),
            }
        )
    relative, authority = _write_table(root, "derived_4h", derived)
    files[relative] = authority

    trade: list[dict[str, object]] = []
    mark: list[dict[str, object]] = []
    cursor = start - timedelta(minutes=1)
    while cursor < end:
        row = {
            "open_time_ms": int(cursor.timestamp() * 1000),
            "open": "100",
            "high": "100",
            "low": "100",
            "close": "100",
            "close_time_ms": int((cursor + timedelta(minutes=1)).timestamp() * 1000),
        }
        mark.append(row)
        if cursor >= start:
            trade.append(row)
        cursor += timedelta(minutes=1)
    relative, authority = _write_table(root, "trade_price_1m", trade)
    files[relative] = authority
    relative, authority = _write_table(root, "mark_1m", mark)
    files[relative] = authority

    event = start + timedelta(hours=8, milliseconds=17)
    funding = [
        {
            "calc_time_ms": int(event.timestamp() * 1000),
            "funding_interval_hours": 8,
            "last_funding_rate": "0.0001",
        }
    ]
    relative, authority = _write_table(root, "funding", funding)
    files[relative] = authority

    manifest = {
        "schema": "binance-usdm-qualification-manifest-v3",
        "generation": "v3",
        "dataset_version": DATASET,
        "protocol": ACCEPTED_QUALIFICATION_PROTOCOL,
        "classification": "FIXTURE_ONLY",
        "files": files,
    }
    report = {"classification": "FIXTURE_ONLY"}
    (root / "qualification-manifest-v3.json").write_bytes(_canonical(manifest))
    (root / "publication-report-v3.json").write_bytes(_canonical(report))
    identity = V3SourceIdentity(
        _sha(root / "qualification-manifest-v3.json"),
        _sha(root / "publication-report-v3.json"),
        DATASET,
        ACCEPTED_QUALIFICATION_PROTOCOL,
        "FIXTURE_ONLY",
    )
    calendar = CalendarEpisode(
        "BTCUSDT",
        start,
        CalendarStatus.METADATA_ELIGIBLE,
        "VALIDATION",
        start - timedelta(days=7),
        end,
        (),
        ("FUNDING_CONTINUITY_PARTIAL_OR_UNCERTAIN",),
    )
    return identity, calendar


def test_fixture_adapter_actual_engine_and_calendar_ledger_round_trip(tmp_path: Path) -> None:
    identity, calendar = _fixture(tmp_path)
    adapter = V3GridHistoryAdapter(tmp_path, identity)
    assert adapter.preflight_sources()[0] == 4
    week = adapter.load_week(calendar)

    assert len(week.completed_4h_closes) == 42
    assert len(week.minutes) == 9_842
    event = next(minute.funding_events[0] for minute in week.minutes if minute.funding_events)
    assert event.observed_at.microsecond == 17_000
    assert event.mark_observed_at == event.observed_at.replace(second=0, microsecond=0)
    assert len(week.source_file_sha256) == 4

    symbol = TradingSymbol("BTC", "USDT")
    episode = GridEpisodeInput(
        SourceId("synthetic-grid-history-fixture"),
        symbol,
        calendar.episode_start,
        week.completed_4h_closes,
        "fixture-code-sha",
        verified_grid_contract_identity(),
        GridCalendarLedger(symbol, "BASE").carry,
    )
    engine = GridV1Engine(episode, GridScenario.BASE, healthy_grid_health(episode))
    for minute in week.minutes:
        engine.process_minute(minute)
    report = engine.report()
    assert report.completion_status == "COMPLETE"
    assert report.input_contract["processed_minute_count"] == 9_842
    assert report.carry_out is not None

    ledger = GridCalendarLedger(symbol, "BASE")
    completed = ledger.advance_eligible(
        calendar,
        carry_out=engine.carry_out(),
        completion_status=report.completion_status,
    )
    assert completed.included_in_weekly_statistics is True
    assert Decimal(completed.weekly_return or "NaN") == 0

    excluded = CalendarEpisode(
        "BTCUSDT",
        calendar.episode_start + timedelta(days=7),
        CalendarStatus.METADATA_INELIGIBLE,
        "VALIDATION",
        calendar.warmup_start + timedelta(days=7),
        calendar.required_data_end_exclusive + timedelta(days=7),
        ("REQUIRED_ACTIVE_MARK_GAP",),
        (),
    )
    skipped = ledger.advance_ineligible(excluded)
    assert skipped.included_in_weekly_statistics is False
    assert skipped.weekly_return is None
    assert ledger.carry.current_cash == Decimal("10000")
    assert ledger.carry.cumulative_fees == 0
    assert ledger.carry.previous_episode_start == excluded.episode_start


def test_adapter_rejects_hash_change_before_parquet_value_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, calendar = _fixture(tmp_path)
    adapter = V3GridHistoryAdapter(tmp_path, identity)
    adapter.preflight_sources()
    path = tmp_path / "derived-generations/v3/normalized/derived_4h/BTCUSDT/2026-01.parquet"
    path.write_bytes(path.read_bytes() + b"changed")
    called = False

    def forbidden(*args: object, **kwargs: object) -> pa.Table:
        nonlocal called
        called = True
        raise AssertionError("Parquet values must not be read")

    monkeypatch.setattr(pq, "read_table", forbidden)
    with pytest.raises(GridHistoryPreflightError, match="hash differs"):
        adapter.load_week(calendar)
    assert called is False


def test_eligible_value_read_requires_source_wide_preflight(tmp_path: Path) -> None:
    identity, calendar = _fixture(tmp_path)
    adapter = V3GridHistoryAdapter(tmp_path, identity)
    with pytest.raises(GridHistoryPreflightError, match="source-wide"):
        adapter.load_week(calendar)


def test_ineligible_week_never_reads_market_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, eligible = _fixture(tmp_path)
    adapter = V3GridHistoryAdapter(tmp_path, identity)
    excluded = CalendarEpisode(
        eligible.symbol,
        eligible.episode_start,
        CalendarStatus.METADATA_INELIGIBLE,
        eligible.partition,
        eligible.warmup_start,
        eligible.required_data_end_exclusive,
        ("REQUIRED_ACTIVE_MARK_GAP",),
        (),
    )
    monkeypatch.setattr(adapter, "_rows", lambda *args, **kwargs: pytest.fail("value read"))
    with pytest.raises(GridHistoryPreflightError, match="excluded week"):
        adapter.load_week(excluded)


def test_incomplete_eligible_result_blocks_continuation() -> None:
    start = datetime(2026, 1, 12, tzinfo=UTC)
    row = CalendarEpisode(
        "ETHUSDT",
        start,
        CalendarStatus.METADATA_ELIGIBLE,
        "VALIDATION",
        start - timedelta(days=7),
        start + timedelta(days=6, hours=20, minutes=2),
        (),
        (),
    )
    ledger = GridCalendarLedger(TradingSymbol("ETH", "USDT"), "STRESS")
    record = ledger.advance_eligible(
        row,
        carry_out=ledger.carry,
        completion_status="PARTIAL_UNRESOLVED",
    )
    assert record.transition == "INTEGRITY_BLOCKED"
    assert record.carry_out_sha256 is None
    with pytest.raises(GridHistoryPreflightError, match="cannot advance"):
        ledger.advance_eligible(row, carry_out=ledger.carry, completion_status="COMPLETE")


def test_accepted_calendar_and_inert_preflight_are_exact() -> None:
    calendar = load_calendar(REPO / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json")
    assert ACCEPTED_CALENDAR_SHA256 == (
        "977b21c4aa342cd3d7391a86a84e2c26ca7afb065fe65259df909652db2933f1"
    )
    assert len(calendar) == 712
    assert sum(row.status is CalendarStatus.METADATA_ELIGIBLE for row in calendar) == 683
    result = metadata_only_preflight(REPO)
    assert result["status"] == "DATA_ONLY_PREFLIGHT_STUDY_NOT_AUTHORIZED"
    assert result["historical_execution"] == "NOT_AUTHORIZED"
    assert result["price_rows_read"] == 0
    assert result["strategy_evaluations"] == 0
    assert result["v3_manifest_sha256"] == ACCEPTED_V3_IDENTITY.manifest_sha256
    assert result["study_plan_sha256"] == STUDY_PLAN_SHA256


def test_historical_execution_is_unconditionally_refused() -> None:
    with pytest.raises(GridHistoryPreflightError, match="NOT_AUTHORIZED"):
        refuse_historical_execution()
