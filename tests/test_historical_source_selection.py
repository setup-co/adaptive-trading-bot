from pathlib import Path

import pytest

import adaptive_trading_bot.historical_sources as source_module
from adaptive_trading_bot.historical_sources import (
    HistoricalSourceSelectionError,
    open_historical_store,
)
from adaptive_trading_bot.storage import ParquetHistoricalCandleStore, SourceId


def test_generic_source_uses_generic_store(tmp_path: Path) -> None:
    store = open_historical_store(
        source_ids=(SourceId("archive-a"),),
        generic_root=tmp_path / "generic",
        qualified_corpus_root=tmp_path / "qualified",
        dataset_version=None,
    )

    assert isinstance(store, ParquetHistoricalCandleStore)


def test_qualified_weex_source_requires_explicit_dataset_version(tmp_path: Path) -> None:
    with pytest.raises(HistoricalSourceSelectionError, match="explicit dataset version"):
        open_historical_store(
            source_ids=(SourceId("weex-v3-last-abc"),),
            generic_root=tmp_path / "generic",
            qualified_corpus_root=tmp_path / "qualified",
            dataset_version=None,
        )


def test_qualified_weex_source_cannot_mix_with_generic_source(tmp_path: Path) -> None:
    with pytest.raises(HistoricalSourceSelectionError, match="cannot be mixed"):
        open_historical_store(
            source_ids=(SourceId("weex-v3-last-abc"), SourceId("archive-a")),
            generic_root=tmp_path / "generic",
            qualified_corpus_root=tmp_path / "qualified",
            dataset_version="weex-v1-5m-last-test",
        )


def test_dataset_version_is_rejected_for_generic_source(tmp_path: Path) -> None:
    with pytest.raises(HistoricalSourceSelectionError, match="only valid"):
        open_historical_store(
            source_ids=(SourceId("archive-a"),),
            generic_root=tmp_path / "generic",
            qualified_corpus_root=tmp_path / "qualified",
            dataset_version="weex-v1-5m-last-test",
        )


def test_qualified_weex_source_opens_only_version_bound_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_source = SourceId("weex-v3-last-abc")
    captured: dict[str, object] = {}

    class FakeLockedStore:
        def __init__(self, root: Path, *, dataset_version: str) -> None:
            captured["root"] = root
            captured["dataset_version"] = dataset_version
            self.source_id = expected_source

    monkeypatch.setattr(source_module, "LockedWeexResearchCorpusStore", FakeLockedStore)
    store = open_historical_store(
        source_ids=(expected_source,),
        generic_root=tmp_path / "generic",
        qualified_corpus_root=tmp_path / "qualified",
        dataset_version="weex-v1-5m-last-test",
    )

    assert isinstance(store, FakeLockedStore)
    assert captured == {
        "root": tmp_path / "qualified" / "weex-v1-5m-last-test",
        "dataset_version": "weex-v1-5m-last-test",
    }


def test_qualified_weex_source_rejects_source_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeLockedStore:
        def __init__(self, root: Path, *, dataset_version: str) -> None:
            self.source_id = SourceId("weex-v3-last-other")

    monkeypatch.setattr(source_module, "LockedWeexResearchCorpusStore", FakeLockedStore)
    with pytest.raises(HistoricalSourceSelectionError, match="does not match"):
        open_historical_store(
            source_ids=(SourceId("weex-v3-last-abc"),),
            generic_root=tmp_path / "generic",
            qualified_corpus_root=tmp_path / "qualified",
            dataset_version="weex-v1-5m-last-test",
        )
