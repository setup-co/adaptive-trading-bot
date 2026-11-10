import json
import runpy
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    FRAMES,
    SOURCE_ID,
    ScreenInputAuthority,
    _candle,
    _canonical,
    _MemoryStore,
    _projection,
    _spacing_mask,
    _text,
    run_member,
)
from adaptive_trading_bot.research.last_5m_protective_execution import (
    Last5mExecutionBinding,
    Last5mParquetSource,
)
from adaptive_trading_bot.research.research_v2_corpus import Row, _table, derive, epochs

START = datetime(2026, 1, 1, tzinfo=UTC)
SIGNAL_START = START + timedelta(days=8, hours=20)
SIGNAL_END = SIGNAL_START + timedelta(hours=4)
TAIL_END = SIGNAL_END + timedelta(minutes=40)
CLI = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "research" / "failed_breakout_reversion_screen.py")
)
cli_main = CLI["main"]


def _rows(wire: str) -> list[Row]:
    values: list[Row] = []
    count = int((TAIL_END - START) / timedelta(minutes=5))
    short = wire == "ETHUSDT"
    close_pattern = tuple(
        Decimal(value) for value in ("98.6", "100.8", "99.2", "101.4", "99.7", "100.5", "98.9")
    )
    for index in range(count):
        opened = START + index * timedelta(minutes=5)
        open_price = Decimal(100)
        high = Decimal("101.5")
        low = Decimal("98.5")
        close = close_pattern[(index // 3) % len(close_pattern)]
        if opened < SIGNAL_START - timedelta(hours=4):
            high = Decimal(110)
            low = Decimal(90)
        if SIGNAL_START <= opened < SIGNAL_START + timedelta(minutes=15):
            high = Decimal("101.6") if short else Decimal("100.5")
            low = Decimal("99.5") if short else Decimal("98.4")
            close = Decimal("100.8") if short else Decimal("99.2")
        elif opened >= SIGNAL_START + timedelta(minutes=15):
            open_price = Decimal("100.2") if short else Decimal("99.8")
            high = Decimal("100.4") if short else Decimal("100.1")
            low = Decimal("99.9") if short else Decimal("99.6")
            close = open_price
        milliseconds = int(opened.timestamp()) * 1000
        values.append(
            Row(
                milliseconds,
                open_price,
                high,
                low,
                close,
                Decimal(1),
                milliseconds + 300_000,
                Decimal(100),
                1,
                Decimal("0.5"),
                Decimal(50),
            )
        )
    return values


def _fixture(tmp_path, wire: str):
    rows = _rows(wire)
    path = tmp_path / f"{wire}-5m.parquet"
    pq.write_table(_table(wire, "5m", rows), path, compression="zstd", use_dictionary=False)
    symbol = TradingSymbol(wire.removesuffix("USDT"), "USDT")
    binding = Last5mExecutionBinding(SOURCE_ID, symbol, "synthetic", START, TAIL_END)
    five = Last5mParquetSource(path, expected_sha256=sha256(path.read_bytes()).hexdigest()).read(
        binding
    )
    by_open = {row.open_time_ms: row for row in rows}
    spans = epochs(by_open)
    factors = {Timeframe.MINUTES_15: 3, Timeframe.HOUR_1: 12, Timeframe.HOURS_4: 48}
    candles = {
        frame: tuple(_candle(symbol, frame, row) for row in derive(by_open, spans, factors[frame]))
        for frame in FRAMES
    }
    return _MemoryStore(symbol, candles), five


def test_actual_canonical_parquet_path_runs_both_members_and_scenarios(tmp_path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    all_rows = []
    for wire in ("BTCUSDT", "ETHUSDT"):
        store, five = _fixture(tmp_path, wire)
        member, rows = run_member(
            wire,
            store,
            five,
            output,
            context_start=START,
            signal_start=SIGNAL_START,
            signal_end=SIGNAL_END,
        )
        assert member["counts"]["admitted"] >= 1
        assert member["counts"]["observations"] == 16
        assert member["spacing_mask_count"] == 1
        assert {row["scenario"] for row in rows} == {"BASE", "STRESS"}
        assert all(row["closed_net_r"] is not None for row in rows)
        assert all(row["decision_at"] <= SIGNAL_END for row in rows)
        all_rows.extend(rows)
    assert {row["side"] for row in all_rows} == {"BUY", "SELL"}
    assert (output / "trades.jsonl").read_text().count("\n") == len(all_rows)


def test_spacing_mask_is_pre_outcome_and_uses_inclusive_35_minutes() -> None:
    values = [
        START,
        START + timedelta(minutes=30),
        START + timedelta(minutes=35),
        START + timedelta(minutes=69),
        START + timedelta(minutes=70),
    ]
    assert _spacing_mask(values) == [
        START,
        START + timedelta(minutes=35),
        START + timedelta(minutes=70),
    ]


def _write_input_authority(repo: Path) -> ScreenInputAuthority:
    root = repo / "data" / "synthetic-failed-breakout"
    parquet = root / "parquet"
    parquet.mkdir(parents=True)
    shard_hashes: list[tuple[str, str]] = []
    for wire in ("BTCUSDT", "ETHUSDT"):
        path = parquet / f"{wire}-5m.parquet"
        pq.write_table(
            _table(wire, "5m", _rows(wire)),
            path,
            compression="zstd",
            use_dictionary=False,
        )
        shard_hashes.append((wire, sha256(path.read_bytes()).hexdigest()))
    plan_body = _canonical({"fixture": "issue-229-command-path"})
    (root / "plan.json").write_bytes(plan_body)
    plan_hash = sha256(plan_body).hexdigest()
    rows = _rows("BTCUSDT")
    epoch_start = rows[0].open_time_ms
    epoch_end = rows[-1].close_time_ms
    report = {
        "status": "QUALIFIED",
        "dataset_version": "synthetic-failed-breakout-v1",
        "start": START.isoformat().replace("+00:00", "Z"),
        "end": TAIL_END.isoformat().replace("+00:00", "Z"),
        "symbols": {
            wire: {
                "status": "QUALIFIED",
                "valid_5m_rows": len(rows),
                "missing_5m_rows": 0,
                "epochs": [{"start_ms": epoch_start, "end_ms": epoch_end, "rows": len(rows)}],
            }
            for wire, _ in shard_hashes
        },
    }
    report_body = _canonical(report)
    (root / "report.json").write_bytes(report_body)
    report_hash = sha256(report_body).hexdigest()
    manifest = {
        "status": "QUALIFIED",
        "capture_status": "CAPTURE_COMPLETE",
        "dataset_version": "synthetic-failed-breakout-v1",
        "plan_sha256": plan_hash,
        "files": {
            "report.json": report_hash,
            **{f"parquet/{wire}-5m.parquet": digest for wire, digest in shard_hashes},
        },
    }
    manifest_body = _canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_body)
    return ScreenInputAuthority(
        SOURCE_ID,
        "synthetic-failed-breakout-v1",
        Path("data/synthetic-failed-breakout"),
        sha256(manifest_body).hexdigest(),
        report_hash,
        plan_hash,
        tuple(shard_hashes),
        len(rows),
        epoch_start,
        epoch_end,
        report["start"],
        report["end"],
        START,
        SIGNAL_START,
        SIGNAL_END,
        TAIL_END,
    )


def test_actual_command_writes_complete_projection_and_canonical_time_joins(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = _write_input_authority(repo)
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "synthetic@example.invalid"),
        cwd=repo,
        check=True,
    )
    subprocess.run(("git", "config", "user.name", "Synthetic Fixture"), cwd=repo, check=True)
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "synthetic corpus"), cwd=repo, check=True)
    executable = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    output = tmp_path / "output"

    assert (
        cli_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--run-id",
                "synthetic-command-path",
                "--executable-sha",
                executable,
            ],
            authority=authority,
        )
        == 0
    )

    projection = json.loads((output / "projection.json").read_text())
    complete = json.loads((output / "complete.json").read_text())
    assert complete["status"] == "COMPLETE"
    assert complete["trade_rows"] == 4
    assert projection["classification"] == "INSUFFICIENT_EVIDENCE"
    for wire in ("BTCUSDT", "ETHUSDT"):
        for scenario in ("BASE", "STRESS"):
            assert projection["economic_table"][f"{wire}:{scenario}:FULL"]["denominator"] == 1
            assert projection["economic_table"][f"{wire}:{scenario}:SPACED"]["denominator"] == 1
            assert (
                projection["chronological_60d_blocks"][f"{wire}:{scenario}:BLOCK_1"]["denominator"]
                == 1
            )
            assert (
                projection["chronological_60d_blocks"][f"{wire}:{scenario}:BLOCK_2"]["denominator"]
                == 0
            )
            assert (
                projection["chronological_60d_blocks"][f"{wire}:{scenario}:BLOCK_3"]["denominator"]
                == 0
            )

    members = [
        json.loads((output / f"{wire.lower()}-member.json").read_text())
        for wire in ("BTCUSDT", "ETHUSDT")
    ]
    serialized_rows = [
        json.loads(line) for line in (output / "trades.jsonl").read_text().splitlines()
    ]
    recomputed = _projection(
        "synthetic-command-path", executable, members, serialized_rows, authority
    )
    assert _text(recomputed) == projection
    btc_base = projection["economic_table"]["BTCUSDT:BASE:FULL"]
    assert btc_base["gross_pnl_quote"] == "2.221957688217516783214682807205111"
    assert btc_base["total_fees_quote"] == "2.535753321652997630931238794794304"
    assert btc_base["modeled_net_pnl_quote"] == "-0.313795633435480847716555987589193"
    assert btc_base["mean_net_r"] == "-0.01255182533741923390866223950356772"


def _classification_inputs(positive_by_wire: dict[str, bool], *, unresolved: bool = False):
    classification_start = DEFAULT_INPUT_AUTHORITY.signal_start
    decision_times = [
        *(classification_start + timedelta(minutes=15 + 35 * index) for index in range(15)),
        *(
            classification_start + timedelta(days=61, minutes=15 + 35 * index)
            for index in range(15)
        ),
    ]
    members = [
        {
            "symbol": wire,
            "spacing_mask": [value.isoformat() for value in decision_times],
        }
        for wire in ("BTCUSDT", "ETHUSDT")
    ]
    rows = []
    for wire, positive in positive_by_wire.items():
        for scenario in ("BASE", "STRESS"):
            for index, decision_at in enumerate(decision_times):
                value = Decimal("0.10") if positive and index != 14 else Decimal("-0.10")
                rows.append(
                    {
                        "symbol": wire,
                        "scenario": scenario,
                        "decision_at": decision_at,
                        "status": "ORDINARY_CLOSED",
                        "trigger": {"kind": "TIME_EXIT"},
                        "entry": {"fee_quote": "0.01"},
                        "exit": {"fee_quote": "0.01"},
                        "gross_pnl_quote": str(value + Decimal("0.02")),
                        "total_fees_quote": "0.02",
                        "net_pnl_quote": str(value),
                        "closed_net_r": str(value),
                        "unresolved_quantity": "1" if unresolved and not rows else "0",
                        "entry_grid_wait_seconds": "0",
                        "holding_lower_seconds": "1800",
                        "holding_upper_seconds": "1800",
                    }
                )
    return members, rows


@pytest.mark.parametrize(
    ("positive_by_wire", "unresolved", "expected"),
    [
        ({"BTCUSDT": True, "ETHUSDT": True}, False, "CANDIDATE_FOR_REVIEW_PRE_FUNDING"),
        ({"BTCUSDT": False, "ETHUSDT": False}, False, "NEGATIVE"),
        ({"BTCUSDT": True, "ETHUSDT": False}, False, "MIXED_FRAGILE"),
        ({"BTCUSDT": True, "ETHUSDT": True}, True, "INCOMPLETE_ECONOMIC_EVIDENCE"),
    ],
)
def test_scalar_classification_branches(
    positive_by_wire: dict[str, bool], unresolved: bool, expected: str
) -> None:
    members, rows = _classification_inputs(positive_by_wire, unresolved=unresolved)
    result = _projection("synthetic-classification", "a" * 40, members, rows)
    assert result["classification"] == expected
