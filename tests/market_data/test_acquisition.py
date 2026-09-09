from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.market_data import (
    BoundedOHLCVImporter,
    ConflictingSourceCandleError,
    InvalidImportRequestError,
    MalformedSourceRowError,
    PaginationError,
)
from adaptive_trading_bot.storage import IntegrityIssueKind, ParquetHistoricalCandleStore, SourceId

BTC_USDT = TradingSymbol("BTC", "USDT")
ETH_USDT = TradingSymbol("ETH", "USDT")
START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    offset: int,
    *,
    timeframe: Timeframe = Timeframe.MINUTES_15,
    symbol: TradingSymbol = BTC_USDT,
    close: str = "100",
) -> Candle:
    open_time = START + offset * timeframe.duration
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timeframe.duration,
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal(close),
        volume=Decimal("12.5"),
    )


class FakeSource:
    source_id = SourceId("ccxt-fake-spot")

    def __init__(self, pages: Sequence[Sequence[Candle]]) -> None:
        self.pages = list(pages)
        self.calls: list[tuple[TradingSymbol, Timeframe, datetime, int]] = []

    def fetch_page(
        self,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[Candle, ...]:
        self.calls.append((symbol, timeframe, since, limit))
        if not self.pages:
            return ()
        return tuple(self.pages.pop(0))


def importer(root: Path, *, now: datetime, limit: int = 2) -> BoundedOHLCVImporter:
    return BoundedOHLCVImporter(
        ParquetHistoricalCandleStore(root),
        FrozenClock(now),
        page_limit=limit,
    )


def test_single_page_import_and_idempotent_repeat(tmp_path: Path) -> None:
    service = importer(tmp_path, now=START + timedelta(hours=1), limit=10)
    first = service.import_range(
        FakeSource([[candle(0), candle(1)]]),
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=30),
    )
    second = service.import_range(
        FakeSource([[candle(0), candle(1)]]),
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert (first.fetched, first.merge.added, first.merge.unchanged) == (2, 2, 0)
    assert first.integrity.is_clean
    assert (second.merge.added, second.merge.unchanged) == (0, 2)


def test_multi_page_overlap_unordered_and_identical_duplicates_are_deterministic(
    tmp_path: Path,
) -> None:
    source = FakeSource(
        [
            [candle(1), candle(0), candle(1)],
            [candle(2), candle(1), candle(3)],
        ]
    )
    result = importer(tmp_path, now=START + timedelta(hours=1)).import_range(
        source,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(hours=1),
    )

    assert [call[2] for call in source.calls] == [START, START + timedelta(minutes=30)]
    assert result.fetched == 4
    assert result.merge.added == 4
    assert result.integrity.is_clean


def test_empty_page_before_effective_end_fails_without_writing(tmp_path: Path) -> None:
    source = FakeSource([[]])

    with pytest.raises(PaginationError, match="empty page"):
        importer(tmp_path, now=START + timedelta(hours=1)).import_range(
            source,
            BTC_USDT,
            Timeframe.MINUTES_15,
            start=START,
            end=START + timedelta(minutes=15),
        )

    assert not tuple(tmp_path.rglob("*.parquet"))


def test_repeated_page_fails_on_no_progress(tmp_path: Path) -> None:
    source = FakeSource([[candle(0)], [candle(0)]])

    with pytest.raises(PaginationError, match="no eligible forward progress"):
        importer(tmp_path, now=START + timedelta(hours=1), limit=1).import_range(
            source,
            BTC_USDT,
            Timeframe.MINUTES_15,
            start=START,
            end=START + timedelta(minutes=30),
        )


def test_conflicting_overlap_fails_without_writing(tmp_path: Path) -> None:
    source = FakeSource([[candle(0)], [candle(0, close="101"), candle(1)]])

    with pytest.raises(ConflictingSourceCandleError):
        importer(tmp_path, now=START + timedelta(hours=1), limit=1).import_range(
            source,
            BTC_USDT,
            Timeframe.MINUTES_15,
            start=START,
            end=START + timedelta(minutes=30),
        )

    assert not tuple(tmp_path.rglob("*.parquet"))


def test_rows_outside_bounds_are_ignored(tmp_path: Path) -> None:
    before = candle(-1)
    after = candle(2)
    result = importer(tmp_path, now=START + timedelta(hours=1), limit=10).import_range(
        FakeSource([[after, candle(0), before, candle(1)]]),
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=30),
    )

    assert result.fetched == 2
    assert result.integrity.is_clean


def test_current_incomplete_candle_is_dropped_and_does_not_drive_progress(tmp_path: Path) -> None:
    now = START + timedelta(minutes=37)
    source = FakeSource([[candle(0), candle(1), candle(2)]])
    result = importer(tmp_path, now=now, limit=10).import_range(
        source,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(hours=1),
    )

    assert result.effective_end == START + timedelta(minutes=30)
    assert result.fetched == 2
    assert result.integrity.is_clean
    stored = ParquetHistoricalCandleStore(tmp_path).read(
        source.source_id,
        BTC_USDT,
        Timeframe.MINUTES_15,
    )
    assert [item.open_time for item in stored] == [START, START + timedelta(minutes=15)]


def test_no_closed_candles_produces_empty_effective_range_without_fetch(tmp_path: Path) -> None:
    source = FakeSource([])
    result = importer(tmp_path, now=START + timedelta(minutes=14)).import_range(
        source,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=15),
    )

    assert result.effective_end == START
    assert result.fetched == 0
    assert source.calls == []
    assert result.integrity.is_clean


def test_entirely_future_range_is_empty_without_fetch(tmp_path: Path) -> None:
    source = FakeSource([])
    result = importer(tmp_path, now=START - timedelta(days=1)).import_range(
        source,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=15),
    )

    assert result.effective_end == START
    assert source.calls == []
    assert result.integrity.is_clean


def test_source_gap_is_reported_without_repair(tmp_path: Path) -> None:
    result = importer(tmp_path, now=START + timedelta(hours=1), limit=10).import_range(
        FakeSource([[candle(0), candle(2)]]),
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=45),
    )

    assert result.integrity.is_clean is False
    assert [issue.kind for issue in result.integrity.issues] == [IntegrityIssueKind.GAP]
    assert result.integrity.rows_scanned == 2


def test_wrong_series_from_source_fails(tmp_path: Path) -> None:
    with pytest.raises(MalformedSourceRowError, match="different series"):
        importer(tmp_path, now=START + timedelta(hours=1), limit=10).import_range(
            FakeSource([[candle(0, symbol=ETH_USDT)]]),
            BTC_USDT,
            Timeframe.MINUTES_15,
            start=START,
            end=START + timedelta(minutes=15),
        )


@pytest.mark.parametrize(
    ("symbol", "start", "end"),
    [
        (TradingSymbol("SOL", "USDT"), START, START + timedelta(minutes=15)),
        (BTC_USDT, START, START),
        (BTC_USDT, START + timedelta(minutes=1), START + timedelta(minutes=15)),
        (BTC_USDT, START, START + timedelta(minutes=16)),
        (
            BTC_USDT,
            datetime(2026, 1, 1),
            datetime(2026, 1, 1, 0, 15),
        ),
    ],
)
def test_invalid_import_boundaries_fail(
    tmp_path: Path,
    symbol: TradingSymbol,
    start: datetime,
    end: datetime,
) -> None:
    with pytest.raises(InvalidImportRequestError):
        importer(tmp_path, now=START + timedelta(hours=1)).import_range(
            FakeSource([]),
            symbol,
            Timeframe.MINUTES_15,
            start=start,
            end=end,
        )


def test_page_limit_is_validated_and_forwarded(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="page_limit"):
        importer(tmp_path, now=START, limit=0)

    source = FakeSource([[candle(0)]])
    importer(tmp_path, now=START + timedelta(hours=1), limit=37).import_range(
        source,
        BTC_USDT,
        Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(minutes=15),
    )
    assert source.calls[0][3] == 37


@pytest.mark.parametrize("symbol", [BTC_USDT, ETH_USDT])
@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_every_v1_symbol_and_timeframe_is_allowed(
    tmp_path: Path,
    symbol: TradingSymbol,
    timeframe: Timeframe,
) -> None:
    source = FakeSource([[candle(0, symbol=symbol, timeframe=timeframe)]])
    result = importer(tmp_path, now=START + timedelta(hours=5), limit=10).import_range(
        source,
        symbol,
        timeframe,
        start=START,
        end=START + timeframe.duration,
    )

    assert result.merge.added == 1
    assert result.integrity.is_clean
