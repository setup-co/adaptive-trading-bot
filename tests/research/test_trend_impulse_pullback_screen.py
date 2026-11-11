import json
import runpy
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq

from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    SOURCE_ID,
    ScreenInputAuthority,
    _canonical,
    _text,
)
from adaptive_trading_bot.research.research_v2_corpus import Row, _table
from adaptive_trading_bot.research.trend_impulse_pullback_screen import (
    _projection,
    _study_metrics,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
SIGNAL_START = START + timedelta(days=8, hours=20)
SIGNAL_END = SIGNAL_START + timedelta(hours=4)
TAIL_END = SIGNAL_END + timedelta(minutes=40)
CLI = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "research" / "trend_impulse_pullback_screen.py")
)
cli_main = CLI["main"]


def _bar_values(wire: str, opened: datetime, index: int) -> tuple[Decimal, ...]:
    interval = (opened - START) // timedelta(minutes=15)
    phase = index % 3
    until_signal = SIGNAL_START - opened
    if until_signal > timedelta(hours=80):
        baseline = Decimal(80 if (int(interval) // 16) % 2 == 0 else 120)
    elif timedelta(hours=20) < until_signal <= timedelta(hours=40):
        baseline = Decimal(
            100 if int(interval) % 16 == 15 else (90 if (int(interval) // 4) % 2 else 110)
        )
    elif timedelta(hours=5) < until_signal <= timedelta(hours=10):
        baseline = Decimal(100 if int(interval) % 4 == 3 else (90 if int(interval) % 2 else 110))
    else:
        phase_prices = (-1, 0, 1, 0)
        baseline = Decimal(100 + phase_prices[int(interval) % 4])
    values = (baseline, baseline + Decimal(1), baseline - Decimal(1), baseline)
    pattern = int((opened - SIGNAL_START) // timedelta(minutes=15))
    short = wire == "ETHUSDT"
    if pattern == 0:
        values = (
            (Decimal(108), Decimal(109), Decimal(99), Decimal(100))
            if short
            else (Decimal(100), Decimal(109), Decimal(99), Decimal(108))
        )
    elif pattern == 1:
        values = (
            (Decimal(101), Decimal("103.5"), Decimal(101), Decimal(103))
            if short
            else (Decimal(107), Decimal(107), Decimal("104.5"), Decimal(105))
        )
    elif pattern == 2:
        values = (
            (Decimal(103), Decimal("103.5"), Decimal(99), Decimal(100))
            if short
            else (Decimal(105), Decimal(109), Decimal("104.5"), Decimal(108))
        )
    elif pattern >= 3:
        values = (
            (Decimal(100), Decimal(103), Decimal(80), Decimal(100))
            if short
            else (Decimal(108), Decimal(120), Decimal(104), Decimal(108))
        )
    opened_price, high, low, close = values
    return (
        opened_price if phase == 0 else close,
        high,
        low,
        close if phase == 2 else opened_price,
    )


def _rows(wire: str) -> list[Row]:
    values: list[Row] = []
    count = int((TAIL_END - START) / timedelta(minutes=5))
    for index in range(count):
        opened = START + index * timedelta(minutes=5)
        open_price, high, low, close = _bar_values(wire, opened, index)
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


def _write_authority(repo: Path) -> ScreenInputAuthority:
    root = repo / "data" / "synthetic-impulse-pullback"
    parquet = root / "parquet"
    parquet.mkdir(parents=True)
    shards: list[tuple[str, str]] = []
    for wire in ("BTCUSDT", "ETHUSDT"):
        path = parquet / f"{wire}-5m.parquet"
        pq.write_table(
            _table(wire, "5m", _rows(wire)), path, compression="zstd", use_dictionary=False
        )
        shards.append((wire, sha256(path.read_bytes()).hexdigest()))
    plan_body = _canonical({"fixture": "issue-233-actual-command"})
    (root / "plan.json").write_bytes(plan_body)
    plan_hash = sha256(plan_body).hexdigest()
    rows = _rows("BTCUSDT")
    report_start = START.isoformat().replace("+00:00", "Z")
    report_end = TAIL_END.isoformat().replace("+00:00", "Z")
    report = {
        "status": "QUALIFIED",
        "dataset_version": "synthetic-impulse-pullback-v1",
        "start": report_start,
        "end": report_end,
        "symbols": {
            wire: {
                "status": "QUALIFIED",
                "valid_5m_rows": len(rows),
                "missing_5m_rows": 0,
                "epochs": [
                    {
                        "start_ms": rows[0].open_time_ms,
                        "end_ms": rows[-1].close_time_ms,
                        "rows": len(rows),
                    }
                ],
            }
            for wire, _ in shards
        },
    }
    report_body = _canonical(report)
    (root / "report.json").write_bytes(report_body)
    report_hash = sha256(report_body).hexdigest()
    manifest = {
        "status": "QUALIFIED",
        "capture_status": "CAPTURE_COMPLETE",
        "dataset_version": "synthetic-impulse-pullback-v1",
        "plan_sha256": plan_hash,
        "files": {
            "report.json": report_hash,
            **{f"parquet/{wire}-5m.parquet": digest for wire, digest in shards},
        },
    }
    manifest_body = _canonical(manifest)
    (root / "manifest.json").write_bytes(manifest_body)
    return ScreenInputAuthority(
        SOURCE_ID,
        "synthetic-impulse-pullback-v1",
        Path("data/synthetic-impulse-pullback"),
        sha256(manifest_body).hexdigest(),
        report_hash,
        plan_hash,
        tuple(shards),
        len(rows),
        rows[0].open_time_ms,
        rows[-1].close_time_ms,
        report_start,
        report_end,
        START,
        SIGNAL_START,
        SIGNAL_END,
        TAIL_END,
    )


def test_actual_cli_reads_canonical_parquet_and_writes_final_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = _write_authority(repo)
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "synthetic@example.invalid"), cwd=repo, check=True
    )
    subprocess.run(("git", "config", "user.name", "Synthetic Fixture"), cwd=repo, check=True)
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "synthetic corpus"), cwd=repo, check=True)
    executable = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True
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
    assert {
        row["side"] for row in map(json.loads, (output / "trades.jsonl").read_text().splitlines())
    } == {
        "BUY",
        "SELL",
    }
    members = [
        json.loads((output / f"{wire.lower()}-member.json").read_text())
        for wire in ("BTCUSDT", "ETHUSDT")
    ]
    rows = [json.loads(line) for line in (output / "trades.jsonl").read_text().splitlines()]
    assert (
        _text(_projection("synthetic-command-path", executable, members, rows, authority))
        == projection
    )


def test_metric_arithmetic_retains_winners_when_total_is_negative() -> None:
    rows = [
        {
            "status": "ORDINARY_CLOSED",
            "trigger": {"kind": "TIME_EXIT"},
            "entry": {"fee_quote": "1"},
            "exit": {"fee_quote": "1"},
            "closed_net_r": value,
            "gross_pnl_quote": value,
            "total_fees_quote": "2",
            "net_pnl_quote": value,
            "unresolved_quantity": "0",
            "entry_grid_wait_seconds": "0",
            "holding_lower_seconds": "1800",
            "holding_upper_seconds": "1800",
        }
        for value in ("1", "-3", "0")
    ]
    metrics = _study_metrics(rows)
    assert metrics["mean_net_r"] == Decimal("-0.6666666666666666666666666666666667")
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["zeros"] == 1
    assert metrics["top_three_positive_r"] == [Decimal(1)]
