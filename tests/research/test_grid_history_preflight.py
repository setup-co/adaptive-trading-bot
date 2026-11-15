from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Context, Decimal, localcontext
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
    CalendarAuthorityKind,
    CalendarEpisode,
    CalendarStatus,
    GridCalendarLedger,
    GridHistoryPreflightError,
    V3GridHistoryAdapter,
    V3SourceIdentity,
    VerifiedCalendar,
    load_calendar,
    metadata_only_preflight,
    refuse_historical_execution,
)
from adaptive_trading_bot.research.grid_v1 import (
    GridEpisodeInput,
    GridLedgerCarry,
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


def _excluded_after(row: CalendarEpisode) -> CalendarEpisode:
    return CalendarEpisode(
        row.symbol,
        row.episode_start + timedelta(days=7),
        CalendarStatus.METADATA_INELIGIBLE,
        row.partition,
        row.warmup_start + timedelta(days=7),
        row.required_data_end_exclusive + timedelta(days=7),
        ("REQUIRED_ACTIVE_MARK_GAP",),
        (),
    )


def _completed_fixture(
    root: Path,
) -> tuple[CalendarEpisode, CalendarEpisode, VerifiedCalendar, GridV1Engine]:
    identity, eligible = _fixture(root)
    excluded = _excluded_after(eligible)
    calendar = VerifiedCalendar.synthetic((eligible, excluded), fixture_id="grid-history")
    adapter = V3GridHistoryAdapter(root, identity, calendar=calendar)
    assert adapter.preflight_sources()[0] == 4
    week = adapter.load_week(eligible)
    symbol = TradingSymbol("BTC", "USDT")
    episode = GridEpisodeInput(
        SourceId("synthetic-grid-history-fixture"),
        symbol,
        eligible.episode_start,
        week.completed_4h_closes,
        "fixture-code-sha",
        verified_grid_contract_identity(),
        GridLedgerCarry.initial(symbol),
    )
    engine = GridV1Engine(episode, GridScenario.BASE, healthy_grid_health(episode))
    for minute in week.minutes:
        engine.process_minute(minute)
    return eligible, excluded, calendar, engine


def test_fixture_adapter_actual_engine_and_calendar_ledger_round_trip(tmp_path: Path) -> None:
    calendar, excluded, authority, engine = _completed_fixture(tmp_path)
    symbol = TradingSymbol("BTC", "USDT")
    report = engine.report()
    assert report.completion_status == "COMPLETE"
    assert report.input_contract["processed_minute_count"] == 9_842
    assert report.carry_out is not None

    ledger = GridCalendarLedger(
        symbol,
        "BASE",
        calendar=authority,
        expected_source_id="synthetic-grid-history-fixture",
        expected_supplied_code_sha="fixture-code-sha",
    )
    completed = ledger.advance_eligible(
        calendar,
        report=report,
        carry_out=engine.carry_out(),
    )
    assert completed.included_in_weekly_statistics is True
    assert Decimal(completed.weekly_return or "NaN") == 0

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
    authority = VerifiedCalendar.synthetic((calendar,), fixture_id="hash-change")
    adapter = V3GridHistoryAdapter(tmp_path, identity, calendar=authority)
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
    authority = VerifiedCalendar.synthetic((calendar,), fixture_id="preflight-required")
    adapter = V3GridHistoryAdapter(tmp_path, identity, calendar=authority)
    with pytest.raises(GridHistoryPreflightError, match="source-wide"):
        adapter.load_week(calendar)


def test_ineligible_week_never_reads_market_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, eligible = _fixture(tmp_path)
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
    authority = VerifiedCalendar.synthetic((excluded,), fixture_id="excluded")
    adapter = V3GridHistoryAdapter(tmp_path, identity, calendar=authority)
    monkeypatch.setattr(adapter, "_rows", lambda *args, **kwargs: pytest.fail("value read"))
    with pytest.raises(GridHistoryPreflightError, match="excluded week"):
        adapter.load_week(excluded)


def test_eligible_result_rejects_mismatched_report_and_carry_before_mutation(
    tmp_path: Path,
) -> None:
    row, _, authority, engine = _completed_fixture(tmp_path)
    report = engine.report()
    carry = engine.carry_out()
    symbol = TradingSymbol("BTC", "USDT")

    def ledger() -> GridCalendarLedger:
        return GridCalendarLedger(
            symbol,
            "BASE",
            calendar=authority,
            expected_source_id="synthetic-grid-history-fixture",
            expected_supplied_code_sha="fixture-code-sha",
        )

    mismatches = (
        replace(report, scenario="STRESS"),
        replace(report, source_id="synthetic-forged"),
        replace(report, episode_start=(row.episode_start + timedelta(days=7)).isoformat()),
        replace(
            report, verified_contract={**report.verified_contract, "parameters_sha256": "0" * 64}
        ),
        replace(report, carry_in={**report.carry_in, "current_cash": "20000"}),
    )
    for mismatched in mismatches:
        candidate = ledger()
        with pytest.raises(GridHistoryPreflightError):
            candidate.advance_eligible(row, report=mismatched, carry_out=carry)
        assert candidate.records == []
        assert candidate.carry == GridLedgerCarry.initial(symbol)

    fabricated = replace(
        carry,
        current_cash=Decimal("20000"),
        current_equity=Decimal("20000"),
        cumulative_gross_realized_pnl=Decimal("10000"),
        peak_equity=Decimal("20000"),
    )
    candidate = ledger()
    with pytest.raises(GridHistoryPreflightError, match="carry-out differs"):
        candidate.advance_eligible(row, report=report, carry_out=fabricated)
    assert candidate.records == []

    wrong_symbol = replace(carry, symbol=TradingSymbol("ETH", "USDT"))
    candidate = ledger()
    with pytest.raises(GridHistoryPreflightError):
        candidate.advance_eligible(row, report=report, carry_out=wrong_symbol)
    assert candidate.records == []


def test_calendar_authority_rejects_forged_rows_before_reads_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity, eligible = _fixture(tmp_path)
    excluded = _excluded_after(eligible)
    authority = VerifiedCalendar.synthetic((eligible, excluded), fixture_id="membership")
    adapter = V3GridHistoryAdapter(tmp_path, identity, calendar=authority)
    forged_excluded = replace(
        eligible,
        status=CalendarStatus.METADATA_INELIGIBLE,
        reason_codes=("REQUIRED_ACTIVE_MARK_GAP",),
    )
    forged_eligible = replace(
        excluded,
        status=CalendarStatus.METADATA_ELIGIBLE,
        reason_codes=(),
    )
    monkeypatch.setattr(adapter, "_rows", lambda *args, **kwargs: pytest.fail("value read"))
    for forged in (forged_excluded, forged_eligible):
        with pytest.raises(GridHistoryPreflightError, match="exact authority"):
            adapter.load_week(forged)
    altered_reason = replace(excluded, reason_codes=("REQUIRED_TRADE_GAP",))
    with pytest.raises(GridHistoryPreflightError, match="exact authority"):
        adapter.load_week(altered_reason)

    ledger = GridCalendarLedger(
        TradingSymbol("BTC", "USDT"),
        "BASE",
        calendar=authority,
        expected_source_id="fixture-source",
        expected_supplied_code_sha="fixture-sha",
    )
    altered = replace(excluded, partition="ALTERED")
    with pytest.raises(GridHistoryPreflightError, match="exact authority"):
        ledger.advance_ineligible(altered)
    with pytest.raises(GridHistoryPreflightError, match="exact authority"):
        ledger.advance_ineligible(forged_excluded)
    altered_date = replace(
        eligible,
        episode_start=eligible.episode_start + timedelta(days=14),
        warmup_start=eligible.warmup_start + timedelta(days=14),
        required_data_end_exclusive=eligible.required_data_end_exclusive + timedelta(days=14),
    )
    with pytest.raises(GridHistoryPreflightError, match="exact authority"):
        ledger.advance_ineligible(replace(altered_date, status=CalendarStatus.METADATA_INELIGIBLE))
    assert ledger.records == []


def test_fixture_and_accepted_calendar_authorities_cannot_be_interchanged(tmp_path: Path) -> None:
    identity, eligible = _fixture(tmp_path)
    accepted = VerifiedCalendar.accepted(
        REPO / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    )
    assert accepted.kind.value == "ACCEPTED_V3"
    assert len(accepted.rows_for("BTCUSDT")) == 356
    assert len(accepted.rows_for("ETHUSDT")) == 356
    with pytest.raises(GridHistoryPreflightError, match="fixture-only"):
        V3GridHistoryAdapter(tmp_path, identity, calendar=accepted)

    fixture = VerifiedCalendar.synthetic((eligible,), fixture_id="mode-boundary")
    with pytest.raises(GridHistoryPreflightError, match="accepted calendar"):
        V3GridHistoryAdapter(tmp_path, ACCEPTED_V3_IDENTITY, calendar=fixture)


def test_accepted_calendar_requires_original_sha_verified_bytes(tmp_path: Path) -> None:
    path = REPO / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
    rows = load_calendar(path)
    excluded_index = next(
        index for index, row in enumerate(rows) if row.status is CalendarStatus.METADATA_INELIGIBLE
    )
    forged_rows = list(rows)
    forged_rows[excluded_index] = replace(
        forged_rows[excluded_index],
        status=CalendarStatus.METADATA_ELIGIBLE,
        reason_codes=(),
    )
    assert len(forged_rows) == 712
    altered_rows = (
        tuple(forged_rows),
        tuple(
            replace(row, reason_codes=("ALTERED_REASON",)) if index == excluded_index else row
            for index, row in enumerate(rows)
        ),
        tuple(
            replace(row, flag_codes=("ALTERED_FLAG",)) if index == excluded_index else row
            for index, row in enumerate(rows)
        ),
        tuple(
            replace(row, partition="ALTERED_PARTITION") if index == excluded_index else row
            for index, row in enumerate(rows)
        ),
    )
    for candidate_rows in altered_rows:
        with pytest.raises(GridHistoryPreflightError, match="verified factory"):
            VerifiedCalendar(
                candidate_rows,
                kind=CalendarAuthorityKind.ACCEPTED_V3,
                identity=ACCEPTED_CALENDAR_SHA256,
            )

    payload = json.loads(path.read_bytes())
    assert isinstance(payload, dict)
    episodes = payload["episodes"]
    assert isinstance(episodes, list)
    forged_payload_row = episodes[excluded_index]
    assert isinstance(forged_payload_row, dict)
    forged_payload_row["status"] = CalendarStatus.METADATA_ELIGIBLE.value
    forged_payload_row["reason_codes"] = []
    forged_path = tmp_path / "forged-calendar.json"
    forged_path.write_bytes(_canonical(payload))
    with pytest.raises(GridHistoryPreflightError, match="hash differs"):
        VerifiedCalendar.accepted(forged_path)

    genuine = VerifiedCalendar.accepted(path)
    assert genuine.identity == ACCEPTED_CALENDAR_SHA256
    assert genuine.rows == rows


def test_weekly_return_is_independent_of_ambient_decimal_context(tmp_path: Path) -> None:
    row, _, authority, engine = _completed_fixture(tmp_path)
    base_report = engine.report()
    base_carry = engine.carry_out()
    value = Decimal("10000.123456789012345678901234567890123456789")
    gain = Decimal("0.123456789012345678901234567890123456789")
    with localcontext(Context(prec=60)):
        carry = replace(
            base_carry,
            current_cash=value,
            current_equity=value,
            cumulative_gross_realized_pnl=gain,
            peak_equity=value,
        )
    assert base_report.carry_out is not None
    payload = {
        **base_report.carry_out,
        "current_cash": str(value),
        "current_equity": str(value),
        "cumulative_gross_realized_pnl": str(gain),
        "peak_equity": str(value),
    }
    report = replace(base_report, carry_out=payload)

    results: list[tuple[str, str]] = []
    for context in (Context(prec=7, rounding=ROUND_DOWN), Context(prec=55, rounding=ROUND_UP)):
        ledger = GridCalendarLedger(
            TradingSymbol("BTC", "USDT"),
            "BASE",
            calendar=authority,
            expected_source_id="synthetic-grid-history-fixture",
            expected_supplied_code_sha="fixture-code-sha",
        )
        with localcontext(context):
            record = ledger.advance_eligible(row, report=report, carry_out=carry)
        results.append((record.weekly_return or "", _canonical(asdict(record)).decode()))
    assert results[0] == results[1]


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
