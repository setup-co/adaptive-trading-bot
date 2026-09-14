import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.market_data import cli
from adaptive_trading_bot.market_data.acquisition import BoundedOHLCVImporter, ImportResult
from adaptive_trading_bot.storage import (
    IntegrityIssue,
    IntegrityIssueKind,
    IntegrityReport,
    MergeResult,
    SourceId,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("ccxt-fake-spot")


class OnePageSource:
    source_id = SOURCE

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


def _dirty_result(*issues: IntegrityIssue) -> ImportResult:
    return ImportResult(
        source_id=SOURCE,
        symbol=SYMBOL,
        timeframe=Timeframe.MINUTES_15,
        requested_start=START,
        requested_end=END,
        effective_end=END,
        fetched=1,
        merge=MergeResult(added=1, unchanged=0, partitions_written=1),
        integrity=IntegrityReport(
            source_id=SOURCE,
            symbol=SYMBOL,
            timeframe=Timeframe.MINUTES_15,
            start=START,
            end=END,
            rows_scanned=1,
            issues=tuple(issues),
        ),
    )


def _cli_args() -> list[str]:
    return [
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

    exit_code = cli.main(_cli_args())

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert output == {
        "effective_end": "2026-01-01T00:15:00+00:00",
        "fetched": 1,
        "imported": 1,
        "integrity": {"clean": True, "issues": [], "rows_scanned": 1},
        "partitions_written": 1,
        "requested_end": "2026-01-01T00:15:00+00:00",
        "requested_start": "2026-01-01T00:00:00+00:00",
        "source_id": "ccxt-fake-spot",
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "unchanged": 0,
    }


def test_cli_dirty_integrity_fails_closed_with_deterministic_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(_env_file=None, historical_market_data_root=tmp_path)  # type: ignore[call-arg]
    result = _dirty_result(
        IntegrityIssue(
            kind=IntegrityIssueKind.GAP,
            message="historical candle coverage gap",
            partition="2026-01.parquet",
            open_time=START,
        )
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "CcxtPublicOHLCVSource", lambda exchange, timeout_ms: OnePageSource())
    monkeypatch.setattr(BoundedOHLCVImporter, "import_range", lambda *args, **kwargs: result)

    exit_code = cli.main(_cli_args())

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == (
        '{"effective_end": "2026-01-01T00:15:00+00:00", '
        '"error": "dirty_integrity", "integrity": {"clean": false, '
        '"issues": [{"kind": "gap", "message": "historical candle coverage gap"}], '
        '"rows_scanned": 1}, "requested_end": "2026-01-01T00:15:00+00:00", '
        '"requested_start": "2026-01-01T00:00:00+00:00", '
        '"source_id": "ccxt-fake-spot", "symbol": "BTC/USDT", "timeframe": "15m"}\n'
    )


def test_cli_dirty_integrity_preserves_all_issue_kinds_and_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(_env_file=None, historical_market_data_root=tmp_path)  # type: ignore[call-arg]
    result = _dirty_result(
        IntegrityIssue(
            kind=IntegrityIssueKind.GAP,
            message="missing boundary candle",
            partition="2026-01.parquet",
            open_time=START,
        ),
        IntegrityIssue(
            kind=IntegrityIssueKind.OVERLAP,
            message="candle intervals overlap",
            partition="2026-01.parquet",
            open_time=START,
        ),
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "CcxtPublicOHLCVSource", lambda exchange, timeout_ms: OnePageSource())
    monkeypatch.setattr(BoundedOHLCVImporter, "import_range", lambda *args, **kwargs: result)

    assert cli.main(_cli_args()) == 2

    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["integrity"]["issues"] == [
        {"kind": "gap", "message": "missing boundary candle"},
        {"kind": "overlap", "message": "candle intervals overlap"},
    ]
    assert diagnostic["source_id"] == "ccxt-fake-spot"
    assert diagnostic["symbol"] == "BTC/USDT"
    assert diagnostic["timeframe"] == "15m"
    assert diagnostic["requested_start"] == "2026-01-01T00:00:00+00:00"
    assert diagnostic["requested_end"] == "2026-01-01T00:15:00+00:00"


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
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "import failed" in captured.err


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
