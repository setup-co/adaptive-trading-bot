from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest

import adaptive_trading_bot.research.weex_leveraged_inputs as inputs_module
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusHashMismatchError,
    CorpusInterval,
    CorpusManifestError,
    WeexSourceIntegrityError,
)
from adaptive_trading_bot.research.weex_leveraged_inputs import (
    FundingEvent,
    LockedWeexLeveragedInputDataset,
    MarkFiveMinuteCandle,
    SourceFetchResult,
    WeexContractSpecification,
    WeexV3FundingHistorySource,
    WeexV3MarkHistorySource,
    build_mark_dataset,
)

START = datetime(2025, 10, 1, tzinfo=UTC)


def mark_row(index: int, *, close: str | None = None) -> list[object]:
    open_time = START + index * timedelta(minutes=5)
    price = Decimal("100") + index
    return [
        int(open_time.timestamp() * 1000),
        str(price),
        str(price + 2),
        str(price - 2),
        str(price + 1) if close is None else close,
        "not-economically-used",
        int((open_time + timedelta(minutes=5)).timestamp() * 1000),
        "not-economically-used",
        "not-economically-used",
        "not-economically-used",
        "not-economically-used",
    ]


def funding_item(index: int, *, rate: str = "0.0000123400") -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "fundingRate": rate,
        "fundingTime": int((START + index * timedelta(hours=8)).timestamp() * 1000),
        "markPrice": "123.4500",
    }


class RecordingMarkSource(WeexV3MarkHistorySource):
    def __init__(self, pages: list[list[list[object]]]) -> None:
        super().__init__(minimum_request_interval_seconds=0)
        self.pages = pages

    def _request(self, endpoint: str, parameters: dict[str, str]) -> tuple[object, int]:
        assert endpoint == inputs_module.WEEX_HISTORY_ENDPOINT
        return self.pages.pop(0), 0


class RecordingFundingSource(WeexV3FundingHistorySource):
    def __init__(self) -> None:
        super().__init__(minimum_request_interval_seconds=0)
        self.parameters: list[dict[str, str]] = []

    def _request(self, endpoint: str, parameters: dict[str, str]) -> tuple[object, int]:
        assert endpoint == inputs_module.WEEX_FUNDING_ENDPOINT
        self.parameters.append(parameters)
        start = datetime.fromtimestamp(int(parameters["startTime"]) / 1000, tz=UTC)
        end = datetime.fromtimestamp(int(parameters["endTime"]) / 1000, tz=UTC)
        rows: list[dict[str, object]] = []
        cursor = start
        while cursor <= end:
            rows.append(
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.00001",
                    "fundingTime": int(cursor.timestamp() * 1000),
                    "markPrice": "100",
                }
            )
            cursor += timedelta(hours=8)
        return list(reversed(rows)), 0


class FakeCompleteMarkSource(WeexV3MarkHistorySource):
    def __init__(self) -> None:
        super().__init__(minimum_request_interval_seconds=0)
        self.calls = 0

    def fetch_range(self, symbol: str, *, start: datetime, end: datetime) -> SourceFetchResult:
        self.calls += 1
        count = int((end - start) / timedelta(minutes=5))
        records = tuple(
            MarkFiveMinuteCandle(
                symbol=symbol,
                open_time=start + index * timedelta(minutes=5),
                close_time=start + (index + 1) * timedelta(minutes=5),
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("99"),
                close=Decimal("101"),
            )
            for index in range(count)
        )
        return SourceFetchResult(records, 1, 0, 0)


def small_required() -> dict[str, tuple[CorpusInterval, ...]]:
    return {
        "BTCUSDT": (CorpusInterval(START, START + timedelta(minutes=10)),),
        "ETHUSDT": (
            CorpusInterval(START, START + timedelta(minutes=5)),
            CorpusInterval(START + timedelta(minutes=10), START + timedelta(minutes=15)),
        ),
    }


def test_mark_parser_preserves_decimal_and_ignores_additive_fields() -> None:
    candle = MarkFiveMinuteCandle.from_wire_row("BTCUSDT", mark_row(0))

    assert candle.open == Decimal("100")
    assert candle.close == Decimal("101")
    assert isinstance(candle.open, Decimal)


@pytest.mark.parametrize(
    ("open_offset", "close_offset", "message"),
    [
        (timedelta(minutes=1), timedelta(minutes=6), "off the UTC 5m grid"),
        (timedelta(), timedelta(minutes=4), r"open_time \+ 5m"),
    ],
)
def test_mark_rejects_grid_and_close_time_mutation(
    open_offset: timedelta, close_offset: timedelta, message: str
) -> None:
    with pytest.raises(WeexSourceIntegrityError, match=message):
        MarkFiveMinuteCandle(
            symbol="BTCUSDT",
            open_time=START + open_offset,
            close_time=START + close_offset,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
        )


def test_mark_source_rejects_conflicting_duplicate() -> None:
    source = RecordingMarkSource([[mark_row(0), mark_row(0, close="100")]])

    with pytest.raises(WeexSourceIntegrityError, match="conflicting MARK duplicate"):
        source.fetch_range("BTCUSDT", start=START, end=START + timedelta(minutes=5))


def test_mark_source_fails_on_missing_candle() -> None:
    source = RecordingMarkSource([[mark_row(0), mark_row(2)]])

    with pytest.raises(WeexSourceIntegrityError, match="first missing"):
        source.fetch_range("BTCUSDT", start=START, end=START + timedelta(minutes=15))


def test_funding_pages_deterministically_and_deduplicates_boundary() -> None:
    source = RecordingFundingSource()

    result = source.fetch_range("BTCUSDT", start=START, end=START + timedelta(days=8))
    events = tuple(item for item in result.records if isinstance(item, FundingEvent))

    assert result.request_count == 2
    assert result.identical_duplicate_count == 1
    assert len(events) == 24
    assert [item.funding_time for item in events] == sorted(
        item.funding_time for item in events
    )
    assert all(parameters["limit"] == "1000" for parameters in source.parameters)


def test_funding_rejects_conflicting_duplicate() -> None:
    class ConflictSource(WeexV3FundingHistorySource):
        def __init__(self) -> None:
            super().__init__(minimum_request_interval_seconds=0)

        def _request(self, endpoint: str, parameters: dict[str, str]) -> tuple[object, int]:
            return [funding_item(0), funding_item(0, rate="0.00002")], 0

    with pytest.raises(WeexSourceIntegrityError, match="conflicting funding duplicate"):
        ConflictSource().fetch_range(
            "BTCUSDT", start=START, end=START + timedelta(hours=8)
        )


def test_funding_source_does_not_synthesize_events() -> None:
    class SparseSource(WeexV3FundingHistorySource):
        def __init__(self) -> None:
            super().__init__(minimum_request_interval_seconds=0)

        def _request(self, endpoint: str, parameters: dict[str, str]) -> tuple[object, int]:
            return [funding_item(1), funding_item(0)], 0

    result = SparseSource().fetch_range(
        "BTCUSDT", start=START, end=START + timedelta(hours=12)
    )
    events = tuple(item for item in result.records if isinstance(item, FundingEvent))

    assert len(events) == 2
    assert [item.funding_rate for item in events] == [
        Decimal("0.0000123400"),
        Decimal("0.0000123400"),
    ]


def test_manifest_rejects_eth_exclusion_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = small_required()
    monkeypatch.setattr(inputs_module, "issue_128_required_intervals", lambda: required)
    manifest = build_mark_dataset(
        tmp_path,
        source_code_commit="abc",
        built_at=START,
        source=FakeCompleteMarkSource(),
    )
    bridged = replace(
        manifest,
        required_intervals={
            "BTCUSDT": required["BTCUSDT"],
            "ETHUSDT": (
                CorpusInterval(START, START + timedelta(minutes=15)),
            ),
        },
    )

    with pytest.raises(CorpusManifestError, match="locked LAST-corpus boundaries"):
        bridged.validate()


def test_locked_dataset_rechecks_hash_and_has_no_network_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = small_required()
    monkeypatch.setattr(inputs_module, "issue_128_required_intervals", lambda: required)
    source = FakeCompleteMarkSource()
    manifest = build_mark_dataset(
        tmp_path,
        source_code_commit="abc",
        built_at=START,
        source=source,
    )
    manifest_hash = sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(
        inputs_module,
        "QUALIFIED_LEVERAGED_INPUT_MANIFEST_SHA256",
        MappingProxyType({manifest.dataset_version: manifest_hash}),
    )
    locked = LockedWeexLeveragedInputDataset(
        tmp_path, dataset_version=manifest.dataset_version
    )
    calls_after_build = source.calls

    assert len(locked.read_symbol("BTCUSDT")) == 2
    assert source.calls == calls_after_build

    artifact_path = tmp_path / manifest.artifacts[0].path
    artifact_path.chmod(0o666)
    with artifact_path.open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(CorpusHashMismatchError, match="hash mismatch"):
        locked.read_symbol(manifest.artifacts[0].symbol)


def test_manifest_bytes_are_deterministic_and_tamper_evident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = small_required()
    monkeypatch.setattr(inputs_module, "issue_128_required_intervals", lambda: required)
    manifest = build_mark_dataset(
        tmp_path,
        source_code_commit="abc",
        built_at=START,
        source=FakeCompleteMarkSource(),
    )
    raw = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    raw["source_id"] = "forged"
    (tmp_path / "manifest.json").chmod(0o666)
    (tmp_path / "manifest.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorpusManifestError, match="dataset version"):
        inputs_module.LeveragedInputManifest.from_path(tmp_path / "manifest.json")
    assert manifest.dataset_version.startswith("weex-v1-5m-mark-")


def test_manifest_rejects_forged_qualification_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = small_required()
    monkeypatch.setattr(inputs_module, "issue_128_required_intervals", lambda: required)
    manifest = build_mark_dataset(
        tmp_path,
        source_code_commit="abc",
        built_at=START,
        source=FakeCompleteMarkSource(),
    )

    with pytest.raises(CorpusManifestError, match="qualification status"):
        replace(manifest, status="QUALIFIED").validate()


def test_contract_quantity_notional_round_trip_is_exact() -> None:
    specification = WeexContractSpecification(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        margin_asset="USDT",
        contract_val=Decimal("0.0001"),
        price_precision=1,
        quantity_precision=4,
        min_order_size=Decimal("0.0001"),
        max_order_size=Decimal("1200"),
        max_position_size=Decimal("1000000"),
        market_open_limit_size=Decimal("100"),
        maker_fee_rate=Decimal("0.0002"),
        taker_fee_rate=Decimal("0.0008"),
    )

    contracts = specification.contracts_for_base_quantity(Decimal("0.0123"))

    assert contracts == Decimal("123")
    assert specification.base_quantity_for_contracts(contracts) == Decimal("0.0123")
    assert specification.effective_notional(
        Decimal("0.0123"), Decimal("100000")
    ) == Decimal("1230.0000")
