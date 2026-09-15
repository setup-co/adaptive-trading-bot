from pathlib import Path

import pytest

import adaptive_trading_bot.research.cli as research_cli
from adaptive_trading_bot.config import Settings

WEEX_SOURCE = "weex-v3-last-e5b0c4035a19d5a80ca7"


def configure_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        historical_market_data_root=tmp_path / "generic",
        qualified_research_corpus_root=tmp_path / "qualified",
    )  # type: ignore[call-arg]
    monkeypatch.setattr(research_cli, "get_settings", lambda: settings)


def test_research_cli_rejects_qualified_weex_without_dataset_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)

    exit_code = research_cli.main(
        [
            "--source",
            WEEX_SOURCE,
            "--symbol",
            "BTC/USDT",
            "--start",
            "2025-04-10T08:00:00Z",
            "--end",
            "2025-04-10T12:00:00Z",
            "--research-equity",
            "10000",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "explicit dataset version" in captured.err
