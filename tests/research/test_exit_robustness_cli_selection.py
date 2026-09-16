from pathlib import Path
from types import SimpleNamespace

import pytest

import adaptive_trading_bot.research.trend_pullback_exit_robustness as robustness_cli
from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.research.weex_historical_corpus import (
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore

WEEX_SOURCE = "weex-v3-last-e5b0c4035a19d5a80ca7"
DATASET_VERSION = "weex-v1-5m-last-a629a29b580b2e103adba918"


def configure_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    qualified_root: Path | None = None,
) -> Settings:
    settings = Settings(
        _env_file=None,
        historical_market_data_root=tmp_path / "configured-generic",
        qualified_research_corpus_root=qualified_root or tmp_path / "qualified",
    )  # type: ignore[call-arg]
    monkeypatch.setattr(robustness_cli, "get_settings", lambda: settings)
    return settings


def command(*sources: str) -> list[str]:
    arguments: list[str] = []
    for source in sources:
        arguments.extend(("--source", source))
    return [
        *arguments,
        "--symbol",
        "BTC/USDT",
        "--start",
        "2025-04-10T08:00:00Z",
        "--end",
        "2025-04-10T12:00:00Z",
        "--research-equity",
        "10000",
        "--follow-up-hours",
        "4",
    ]


def install_fast_report_stubs(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, object],
) -> None:
    class FakeGenerator:
        def __init__(self, store: object) -> None:
            captured["generator_store"] = store

        def generate(self, admission: object) -> SimpleNamespace:
            captured["admission"] = admission
            return SimpleNamespace(corpus_id="corpus:test", corpus_digest="digest")

    class FakeRunner:
        def __init__(self, store: object) -> None:
            captured["runner_store"] = store

        def run(self, corpus: object, configuration: object) -> SimpleNamespace:
            captured["configuration"] = configuration
            return SimpleNamespace(to_json=lambda: '{"research_only":true}')

    monkeypatch.setattr(robustness_cli, "TrendPullbackHistoricalCorpusGenerator", FakeGenerator)
    monkeypatch.setattr(robustness_cli, "TrendPullbackExitRobustnessRunner", FakeRunner)


def test_exit_robustness_rejects_qualified_weex_without_dataset_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)

    assert robustness_cli.main(command(WEEX_SOURCE)) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit dataset version" in captured.err


def test_exit_robustness_rejects_nonexistent_dataset_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)

    assert (
        robustness_cli.main(
            [*command(WEEX_SOURCE), "--dataset-version", "weex-v1-5m-last-does-not-exist"]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "qualified WEEX corpus could not be opened" in captured.err


def test_exit_robustness_rejects_mixed_qualified_and_generic_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)

    assert (
        robustness_cli.main(
            [
                *command(WEEX_SOURCE, "generic-archive"),
                "--dataset-version",
                DATASET_VERSION,
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot be mixed" in captured.err


def test_exit_robustness_keeps_generic_source_without_dataset_version_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_roots(tmp_path, monkeypatch)
    captured_objects: dict[str, object] = {}
    install_fast_report_stubs(monkeypatch, captured_objects)
    generic_root = tmp_path / "operator-generic"

    assert robustness_cli.main([*command("generic-archive"), "--data-root", str(generic_root)]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == '{"research_only":true}\n'
    store = captured_objects["generator_store"]
    assert isinstance(store, ParquetHistoricalCandleStore)
    assert store._root == generic_root
    assert captured_objects["runner_store"] is store


def test_exit_robustness_exact_qualified_version_ignores_arbitrary_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    qualified_root = repository_root / "data" / "research_corpus"
    configure_roots(tmp_path, monkeypatch, qualified_root=qualified_root)
    captured_objects: dict[str, object] = {}
    install_fast_report_stubs(monkeypatch, captured_objects)
    arbitrary_root = tmp_path / "untrusted-parquet"

    assert (
        robustness_cli.main(
            [
                *command(WEEX_SOURCE),
                "--dataset-version",
                DATASET_VERSION,
                "--data-root",
                str(arbitrary_root),
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == '{"research_only":true}\n'
    store = captured_objects["generator_store"]
    assert isinstance(store, LockedWeexResearchCorpusStore)
    assert store._root == qualified_root / DATASET_VERSION
    assert store.source_id.value == WEEX_SOURCE
    assert captured_objects["runner_store"] is store
    assert not arbitrary_root.exists()
