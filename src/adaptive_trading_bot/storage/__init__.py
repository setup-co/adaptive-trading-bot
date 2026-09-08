"""Historical-data storage contracts and implementations."""

from adaptive_trading_bot.storage.historical_candles import (
    CandleConflictError,
    DatasetIntegrityError,
    HistoricalCandleStore,
    HistoricalCandleStoreError,
    IntegrityIssue,
    IntegrityIssueKind,
    IntegrityReport,
    MergeResult,
    ParquetHistoricalCandleStore,
    SourceId,
    StorageWriteError,
    UnrepresentableDecimalError,
)

__all__ = [
    "CandleConflictError",
    "DatasetIntegrityError",
    "HistoricalCandleStore",
    "HistoricalCandleStoreError",
    "IntegrityIssue",
    "IntegrityIssueKind",
    "IntegrityReport",
    "MergeResult",
    "ParquetHistoricalCandleStore",
    "SourceId",
    "StorageWriteError",
    "UnrepresentableDecimalError",
]
