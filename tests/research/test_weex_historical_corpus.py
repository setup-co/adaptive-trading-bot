from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest

import adaptive_trading_bot.research.weex_historical_corpus as corpus_module
from adaptive_trading_bot.domain import FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.weex_historical_corpus import (
    CanonicalFiveMinuteCandle,
    CorpusHashMismatchError,
    CorpusInterval,
    CorpusManifestError,
    CorpusRangeError,
    LockedWeexResearchCorpusStore,
    WeexCorpusBuildConfiguration,
    WeexFetchResult,
    WeexQualifiedCorpusBuilder,
    WeexSourceIntegrityError,
    WeexV3PublicHistorySource,
    issue_115_configuration,
)

START = datetime(2025, 1, 1, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")


def canonical(symbol: str, open_time: datetime, index: int) -> CanonicalFiveMinuteCandle:
    price = Decimal(100 + index)
    return CanonicalFiveMinuteCandle(
        symbol=symbol,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=5),
        open=price,
        high=price + Decimal(2),
        low=price - Decimal(2),
        close=price + Decimal(1),
        base_volume=Decimal("2.5"),
        quote_volume=Decimal("250"),
        trade_count=10,
        taker_buy_base_volume=Decimal("1.25"),
        taker_buy_quote_volume=Decimal("125"),
    )


class FakeQualifiedSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime]] = []

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> WeexFetchResult:
        self.calls.append((symbol, start, end))
        count = int((end - start) / timedelta(minutes=5))
        return WeexFetchResult(
            tuple(
                canonical(symbol, start + index * timedelta(minutes=5), index)
                for index in range(count)
            ),
            request_count=1,
            retry_count=0,
        )


def wire_row(index: int, *, close: str | None = None) -> list[object]:
    candle = canonical("BTCUSDT", START + index * timedelta(minutes=5), index)
    return [
        int(candle.open_time.timestamp() * 1000),
        str(candle.open),
        str(candle.high),
        str(candle.low),
        str(candle.close) if close is None else close,
        str(candle.base_volume),
        int(candle.close_time.timestamp() * 1000),
        str(candle.quote_volume),
        candle.trade_count,
        str(candle.taker_buy_base_volume),
        str(candle.taker_buy_quote_volume),
    ]


class RecordingWireSource(WeexV3PublicHistorySource):
    def __init__(self, pages: list[list[list[object]]]) -> None:
        super().__init__()
        self.pages = pages
        self.parameters: list[dict[str, str]] = []

    def _request(self, parameters: dict[str, str]) -> tuple[object, int]:
        self.parameters.append(parameters)
        return self.pages.pop(0), 0


def configuration(root: Path, *, built_at: datetime = START) -> WeexCorpusBuildConfiguration:
    gap_start = START + timedelta(hours=8)
    gap_end = START + timedelta(hours=12)
    return WeexCorpusBuildConfiguration(
        output_root=root,
        source_code_commit="0123456789abcdef",
        built_at=built_at,
        canonical_intervals={
            "BTCUSDT": (CorpusInterval(START, START + timedelta(hours=20)),),
            "ETHUSDT": (
                CorpusInterval(START, gap_start),
                CorpusInterval(gap_end, START + timedelta(hours=20)),
            ),
        },
        quarantines={
            "BTCUSDT": (),
            "ETHUSDT": (CorpusInterval(gap_start, gap_end),),
        },
        warmup_exclusions={
            "BTCUSDT": (CorpusInterval(START, START + timedelta(hours=4)),),
            "ETHUSDT": (CorpusInterval(gap_start, gap_end + timedelta(hours=4)),),
        },
    )


def trust_test_manifest(monkeypatch: pytest.MonkeyPatch, root: Path, dataset_version: str) -> None:
    digest = sha256((root / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        corpus_module,
        "QUALIFIED_DATASET_MANIFEST_SHA256",
        MappingProxyType({dataset_version: digest}),
    )


def test_wire_row_rejects_blank_additive_field() -> None:
    row: list[object] = [
        int(START.timestamp() * 1000),
        "100",
        "102",
        "99",
        "101",
        "5",
        int((START + timedelta(minutes=5)).timestamp() * 1000),
        "500",
        10,
        "",
        "250",
    ]

    with pytest.raises(WeexSourceIntegrityError, match="malformed"):
        CanonicalFiveMinuteCandle.from_wire_row("ETHUSDT", row)


def test_public_source_uses_deterministic_inclusive_backward_pages() -> None:
    source = RecordingWireSource(
        [
            [wire_row(index) for index in reversed(range(5, 105))],
            [wire_row(index) for index in reversed(range(5))],
        ]
    )

    result = source.fetch_range(
        "BTCUSDT",
        start=START,
        end=START + 105 * timedelta(minutes=5),
    )

    assert len(result.candles) == 105
    assert result.request_count == 2
    assert result.retry_count == 0
    assert [item.open_time for item in result.candles] == [
        START + index * timedelta(minutes=5) for index in range(105)
    ]
    assert source.parameters[0]["endTime"] == str(
        int((START + 104 * timedelta(minutes=5)).timestamp() * 1000)
    )
    assert source.parameters[1]["endTime"] == str(
        int((START + 4 * timedelta(minutes=5)).timestamp() * 1000)
    )
    assert all(
        item["startTime"] == str(int(START.timestamp() * 1000)) for item in source.parameters
    )


def test_public_source_rejects_conflicting_duplicate() -> None:
    source = RecordingWireSource([[wire_row(0), wire_row(0, close="100")]])

    with pytest.raises(WeexSourceIntegrityError, match="conflicting duplicate"):
        source.fetch_range("BTCUSDT", start=START, end=START + timedelta(minutes=5))


def test_public_source_filters_older_server_spill_before_domain_validation() -> None:
    older_malformed: list[object] = [
        int((START - timedelta(minutes=5)).timestamp() * 1000),
        "",
    ]
    source = RecordingWireSource([[wire_row(0), older_malformed]])

    result = source.fetch_range("BTCUSDT", start=START, end=START + timedelta(minutes=5))

    assert len(result.candles) == 1


def test_builder_derives_only_complete_buckets_and_records_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FakeQualifiedSource()
    manifest = WeexQualifiedCorpusBuilder(source).build(configuration(tmp_path))

    assert manifest.eth_status.value == "PARTIAL"
    assert manifest.canonical_intervals["ETHUSDT"] == (
        CorpusInterval(START, START + timedelta(hours=8)),
        CorpusInterval(START + timedelta(hours=12), START + timedelta(hours=20)),
    )
    assert manifest.derived_intervals["ETHUSDT"]["4h"] == (
        CorpusInterval(START, START + timedelta(hours=8)),
        CorpusInterval(START + timedelta(hours=12), START + timedelta(hours=20)),
    )
    assert all(len(item.sha256) == 64 and item.row_count > 0 for item in manifest.artifacts)
    assert [call[0] for call in source.calls] == ["BTCUSDT", "ETHUSDT", "ETHUSDT"]

    trust_test_manifest(monkeypatch, tmp_path, manifest.dataset_version)
    locked = LockedWeexResearchCorpusStore(tmp_path, dataset_version=manifest.dataset_version)
    candles = locked.read(
        locked.source_id,
        ETH,
        Timeframe.HOUR_1,
        start=START + timedelta(hours=12),
        end=START + timedelta(hours=13),
    )
    assert len(candles) == 1
    assert candles[0].volume == Decimal("30.0")


def test_locked_store_rejects_unknown_version_and_quarantine_crossing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(configuration(tmp_path))

    with pytest.raises(CorpusManifestError, match="unknown"):
        LockedWeexResearchCorpusStore(tmp_path, dataset_version="unknown")

    with pytest.raises(CorpusManifestError, match="not project-qualified"):
        LockedWeexResearchCorpusStore(tmp_path, dataset_version=manifest.dataset_version)

    trust_test_manifest(monkeypatch, tmp_path, manifest.dataset_version)
    locked = LockedWeexResearchCorpusStore(tmp_path, dataset_version=manifest.dataset_version)
    with pytest.raises(CorpusRangeError, match="unqualified"):
        locked.read(
            locked.source_id,
            ETH,
            Timeframe.MINUTES_15,
            start=START + timedelta(hours=7),
            end=START + timedelta(hours=13),
        )
    with pytest.raises(CorpusRangeError, match="explicit finite"):
        locked.scan_integrity(locked.source_id, BTC, Timeframe.MINUTES_15)


def test_locked_store_rechecks_artifact_hash_before_every_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(configuration(tmp_path))
    trust_test_manifest(monkeypatch, tmp_path, manifest.dataset_version)
    locked = LockedWeexResearchCorpusStore(tmp_path, dataset_version=manifest.dataset_version)
    artifact_path = tmp_path / manifest.artifacts[0].path
    artifact_path.chmod(0o666)
    with artifact_path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(CorpusHashMismatchError, match="hash mismatch"):
        locked.read(
            locked.source_id,
            BTC,
            Timeframe.HOUR_1,
            start=START,
            end=START + timedelta(hours=1),
        )


def test_replay_consumes_exact_locked_version_without_a_network_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(configuration(tmp_path))
    trust_test_manifest(monkeypatch, tmp_path, manifest.dataset_version)
    locked = LockedWeexResearchCorpusStore(tmp_path, dataset_version=manifest.dataset_version)
    requested = ReplaySeries(
        source_id=locked.source_id,
        symbol=BTC,
        timeframe=Timeframe.MINUTES_15,
        start=START,
        end=START + timedelta(hours=1),
    )

    session = ReplayEngine(HistoricalReplaySource(locked)).prepare(
        (requested,), clock=FrozenClock(START)
    )

    assert [batch.observed_at for batch in session] == [
        START + index * timedelta(minutes=15) for index in range(1, 5)
    ]


def test_dataset_version_is_independent_of_build_timestamp(tmp_path: Path) -> None:
    first = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(configuration(tmp_path / "a"))
    second = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(
        configuration(tmp_path / "b", built_at=START + timedelta(days=1))
    )

    assert first.dataset_version == second.dataset_version
    assert first.built_at != second.built_at


def test_manifest_fails_closed_after_policy_mutation(tmp_path: Path) -> None:
    manifest = WeexQualifiedCorpusBuilder(FakeQualifiedSource()).build(configuration(tmp_path))
    forged = replace(manifest, quarantines={"BTCUSDT": (), "ETHUSDT": ()})

    with pytest.raises(CorpusManifestError, match="dataset version"):
        forged.validate()


def test_issue_115_policy_preserves_larger_eth_exclusion_and_fresh_warmup(
    tmp_path: Path,
) -> None:
    configured = issue_115_configuration(tmp_path, source_code_commit="abc", built_at=START)

    assert configured.quarantines["ETHUSDT"] == (
        CorpusInterval(
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 4, 2, tzinfo=UTC),
        ),
    )
    assert configured.warmup_exclusions["ETHUSDT"][-1] == CorpusInterval(
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 4, 10, 8, tzinfo=UTC),
    )
