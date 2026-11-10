from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pyarrow.parquet as pq

from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    FRAMES,
    SOURCE_ID,
    _candle,
    _MemoryStore,
    _spacing_mask,
    run_member,
)
from adaptive_trading_bot.research.last_5m_protective_execution import (
    Last5mExecutionBinding,
    Last5mParquetSource,
)
from adaptive_trading_bot.research.research_v2_corpus import Row, _table, derive, epochs

START = datetime(2026, 1, 1, tzinfo=UTC)
SIGNAL_START = START + timedelta(hours=20)
SIGNAL_END = START + timedelta(days=1)
TAIL_END = SIGNAL_END + timedelta(minutes=40)


def _rows(wire: str) -> list[Row]:
    values: list[Row] = []
    count = int((TAIL_END - START) / timedelta(minutes=5))
    short = wire == "ETHUSDT"
    for index in range(count):
        opened = START + index * timedelta(minutes=5)
        open_price = Decimal(100)
        high = Decimal(120)
        low = Decimal(80)
        close = Decimal(100)
        if SIGNAL_START <= opened < SIGNAL_START + timedelta(minutes=15):
            high = Decimal(121) if short else Decimal(110)
            low = Decimal(90) if short else Decimal(79)
            close = Decimal(110) if short else Decimal(90)
        elif opened >= SIGNAL_START + timedelta(minutes=15):
            open_price = Decimal(105) if short else Decimal(95)
            high = Decimal(110) if short else Decimal(99)
            low = Decimal(101) if short else Decimal(90)
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
