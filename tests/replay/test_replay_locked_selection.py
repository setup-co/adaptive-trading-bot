from pathlib import Path

import pytest

from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.replay import cli


WEEX_SERIES = (
    "weex-v3-last-e5b0c4035a19d5a80ca7,BTC/USDT,15m,2025-04-10T08:00:00Z,2025-04-10T08:15:00Z"
)


def configure_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        historical_market_data_root=tmp_path / "generic",
        qualified_research_corpus_root=tmp_path / "qualified",
    )  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def test_replay_cli_rejects_qualified_weex_without_dataset_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)

    exit_code = cli.main(["--series", WEEX_SERIES, "--step"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "explicit dataset version" in captured.err
