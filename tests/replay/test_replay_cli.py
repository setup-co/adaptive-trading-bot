import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.replay import cli
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId

SOURCE = SourceId("archive-a")
SYMBOL = TradingSymbol("BTC", "USDT")
START = datetime(2026, 9, 1, tzinfo=UTC)
SERIES = "archive-a,BTC/USDT,15m,2026-09-01T00:00:00Z,2026-09-01T00:15:00Z"
ETH_SERIES = "archive-a,ETH/USDT,15m,2026-09-01T00:00:00Z,2026-09-01T00:15:00Z"


def stored_candle() -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        open_time=START,
        close_time=datetime(2026, 9, 1, 0, 15, tzinfo=UTC),
        open=Decimal("100.000000000000000001"),
        high=Decimal("110.000000000000000002"),
        low=Decimal("90.000000000000000003"),
        close=Decimal("101.000000000000000004"),
        volume=Decimal("12.500000000000000005"),
    )


def configure_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, historical_market_data_root=tmp_path)  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def test_cli_step_outputs_structured_json_with_decimal_strings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_root(tmp_path, monkeypatch)
    ParquetHistoricalCandleStore(tmp_path).merge(SOURCE, (stored_candle(),))

    exit_code = cli.main(["--series", SERIES, "--step"])

    captured = capsys.readouterr()
    record = json.loads(captured.out)
    event = record["events"][0]
    assert exit_code == 0
    assert captured.err == ""
    assert record["observed_at"] == "2026-09-01T00:15:00+00:00"
    assert record["batch_size"] == 1
    assert event["source_id"] == "archive-a"
    assert event["symbol"] == "BTC/USDT"
    assert event["timeframe"] == "15m"
    assert event["open_time"] == "2026-09-01T00:00:00+00:00"
    assert event["close_time"] == "2026-09-01T00:15:00+00:00"
    assert event["open"] == "100.000000000000000001"
    assert event["high"] == "110.000000000000000002"
    assert event["low"] == "90.000000000000000003"
    assert event["close"] == "101.000000000000000004"
    assert event["volume"] == "12.500000000000000005"


def test_cli_integrity_failure_is_nonzero_with_no_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_root(tmp_path, monkeypatch)

    exit_code = cli.main(["--series", SERIES, "--step"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "integrity gate failed" in captured.err


def test_cli_repeated_series_preserve_equal_close_atomic_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_root(tmp_path, monkeypatch)
    btc = stored_candle()
    eth = Candle(
        symbol=TradingSymbol("ETH", "USDT"),
        timeframe=btc.timeframe,
        open_time=btc.open_time,
        close_time=btc.close_time,
        open=btc.open,
        high=btc.high,
        low=btc.low,
        close=btc.close,
        volume=btc.volume,
    )
    ParquetHistoricalCandleStore(tmp_path).merge(SOURCE, (eth, btc))

    exit_code = cli.main(["--series", ETH_SERIES, "--series", SERIES, "--step"])

    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["batch_size"] == 2
    assert [event["symbol"] for event in record["events"]] == ["BTC/USDT", "ETH/USDT"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--series", SERIES, "--speed", "5x"],
        ["--series", "not-a-series", "--step"],
        [
            "--series",
            "archive-a,BTC/USDT,15m,2026-09-01T00:01:00Z,2026-09-01T00:15:00Z",
            "--step",
        ],
    ],
)
def test_cli_invalid_speed_or_series_exits_nonzero(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(arguments)

    assert captured.value.code != 0
