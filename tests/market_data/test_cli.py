import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.market_data import cli
from adaptive_trading_bot.storage import SourceId


class OnePageSource:
    source_id = SourceId("ccxt-fake-spot")

    def fetch_page(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[Candle, ...]:
        del limit
        return (
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=since,
                close_time=since + timeframe.duration,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("10"),
            ),
        )


def test_cli_outputs_structured_import_and_integrity_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(_env_file=None, historical_market_data_root=tmp_path)  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "CcxtPublicOHLCVSource",
        lambda exchange, timeout_ms: OnePageSource(),
    )

    exit_code = cli.main(
        [
            "--exchange",
            "fake",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "15m",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T00:15:00Z",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["source_id"] == "ccxt-fake-spot"
    assert output["imported"] == 1
    assert output["unchanged"] == 0
    assert output["integrity"] == {"clean": True, "issues": [], "rows_scanned": 1}


def test_cli_returns_nonzero_for_invalid_exchange(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "--exchange",
            "not-an-exchange",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "15m",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T00:15:00Z",
        ]
    )

    assert exit_code == 1
    assert "import failed" in capsys.readouterr().err


def test_cli_rejects_symbol_before_constructing_exchange(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called(exchange: str, timeout_ms: int) -> OnePageSource:
        del exchange, timeout_ms
        raise AssertionError("exchange must not be constructed for invalid input")

    monkeypatch.setattr(cli, "CcxtPublicOHLCVSource", fail_if_called)
    exit_code = cli.main(
        [
            "--exchange",
            "fake",
            "--symbol",
            "SOL/USDT",
            "--timeframe",
            "15m",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T00:15:00Z",
        ]
    )

    assert exit_code == 1
    assert "outside the V1 allow-list" in capsys.readouterr().err


def test_cli_parser_rejects_non_utc_timestamp() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "--exchange",
                "fake",
                "--symbol",
                "BTC/USDT",
                "--timeframe",
                "15m",
                "--start",
                datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "--end",
                "2026-01-01T00:15:00+01:00",
            ]
        )

    assert error.value.code != 0
