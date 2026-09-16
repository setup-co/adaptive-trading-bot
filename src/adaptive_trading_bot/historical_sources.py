"""Fail-closed selection of generic versus qualified historical research storage."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from adaptive_trading_bot.research.weex_historical_corpus import (
    LockedWeexResearchCorpusStore,
    WeexHistoricalCorpusError,
)
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    ParquetHistoricalCandleStore,
    SourceId,
)

_LOCKED_WEEX_SOURCE_PREFIX = "weex-v3-last-"


class HistoricalSourceSelectionError(ValueError):
    """Historical storage selection is ambiguous or would bypass qualification."""


def is_locked_weex_source(source_id: SourceId) -> bool:
    """Return whether a source ID belongs to the qualified WEEX corpus namespace."""

    return source_id.value.startswith(_LOCKED_WEEX_SOURCE_PREFIX)


def open_historical_store(
    *,
    source_ids: Sequence[SourceId],
    generic_root: Path,
    qualified_corpus_root: Path,
    dataset_version: str | None,
) -> HistoricalCandleStore:
    """Open the only admissible store for the requested provenance.

    Qualified WEEX source IDs are never allowed through the generic Parquet root. They require an
    exact project-qualified dataset version and the immutable version directory under the qualified
    corpus root. Generic sources remain supported and must not provide a WEEX dataset version.
    """

    unique_sources = tuple(dict.fromkeys(source_ids))
    if not unique_sources:
        raise HistoricalSourceSelectionError("at least one historical source is required")

    locked_sources = tuple(item for item in unique_sources if is_locked_weex_source(item))
    if locked_sources:
        if len(locked_sources) != len(unique_sources):
            raise HistoricalSourceSelectionError(
                "qualified WEEX history cannot be mixed with generic historical sources"
            )
        if len(unique_sources) != 1:
            raise HistoricalSourceSelectionError(
                "one qualified WEEX dataset must expose exactly one version-bound source ID"
            )
        if not dataset_version:
            raise HistoricalSourceSelectionError(
                "qualified WEEX history requires an explicit dataset version"
            )
        version_root = qualified_corpus_root / dataset_version
        try:
            store = LockedWeexResearchCorpusStore(version_root, dataset_version=dataset_version)
        except WeexHistoricalCorpusError as error:
            raise HistoricalSourceSelectionError(
                f"qualified WEEX corpus could not be opened: {error}"
            ) from error
        if store.source_id != unique_sources[0]:
            raise HistoricalSourceSelectionError(
                "requested WEEX source ID does not match the qualified dataset version"
            )
        return store

    if dataset_version is not None:
        raise HistoricalSourceSelectionError(
            "dataset version is only valid for a qualified WEEX historical source"
        )
    return ParquetHistoricalCandleStore(generic_root)
