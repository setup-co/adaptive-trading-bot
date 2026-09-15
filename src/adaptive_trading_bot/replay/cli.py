"""Operator CLI for local historical replay stepping or paced playback."""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime

from adaptive_trading_bot.config import get_settings
from adaptive_trading_bot.domain import FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.historical_sources import (
    HistoricalSourceSelectionError,
    open_historical_store,
)
from adaptive_trading_bot.replay.control import PacedReplayController, ReplaySpeed
from adaptive_trading_bot.replay.core import (
    HistoricalReplaySource,
    ReplayBatch,
    ReplayEngine,
    ReplayError,
    ReplaySeries,
)
from adaptive_trading_bot.storage import SourceId

_SERIES_FORMAT = "SOURCE_ID,SYMBOL,TIMEFRAME,START_UTC,END_UTC"


def _utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamps must be timezone-aware UTC")
    return parsed


def _series(value: str) -> ReplaySeries:
    fields = value.split(",")
    if len(fields) != 5:
        raise argparse.ArgumentTypeError(f"series must use {_SERIES_FORMAT}")
    source, symbol, timeframe, start, end = fields
    try:
        return ReplaySeries(
            source_id=SourceId(source),
            symbol=TradingSymbol.parse(symbol),
            timeframe=Timeframe(timeframe),
            start=_utc_datetime(start),
            end=_utc_datetime(end),
        )
    except (ValueError, argparse.ArgumentTypeError) as error:
        raise argparse.ArgumentTypeError(f"invalid replay series: {error}") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay local historical candles")
    parser.add_argument(
        "--series",
        action="append",
        type=_series,
        required=True,
        help=f"repeatable {_SERIES_FORMAT}",
    )
    parser.add_argument(
        "--dataset-version",
        help=(
            "required for qualified WEEX source IDs; opens the immutable version under "
            "ATB_QUALIFIED_RESEARCH_CORPUS_ROOT"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--speed", choices=[speed.label for speed in ReplaySpeed])
    mode.add_argument(
        "--step",
        action="store_true",
        help="emit each batch immediately without wall-clock pacing",
    )
    return parser


def _batch_record(batch: ReplayBatch) -> dict[str, object]:
    return {
        "observed_at": batch.observed_at.isoformat(),
        "batch_size": len(batch.events),
        "events": [
            {
                "source_id": event.source_id.value,
                **event.candle.serialize(),
            }
            for event in batch.events
        ],
    }


def _write_batches(batches: Iterable[ReplayBatch]) -> None:
    for batch in batches:
        print(json.dumps(_batch_record(batch), sort_keys=True), flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one local replay command and return a process exit code."""

    args = _parser().parse_args(argv)
    try:
        requested = tuple(args.series)
        clock = FrozenClock(min(series.start for series in requested))
        settings = get_settings()
        store = open_historical_store(
            source_ids=tuple(series.source_id for series in requested),
            generic_root=settings.historical_market_data_root,
            qualified_corpus_root=settings.qualified_research_corpus_root,
            dataset_version=args.dataset_version,
        )
        session = ReplayEngine(HistoricalReplaySource(store)).prepare(requested, clock=clock)
        speed = ReplaySpeed.X1 if args.speed is None else ReplaySpeed.parse(args.speed)
        controller = PacedReplayController(session, speed)
        if args.step:
            while (batch := controller.step()) is not None:
                _write_batches((batch,))
        else:
            _write_batches(controller.run())
    except (HistoricalSourceSelectionError, ReplayError, ValueError) as error:
        print(f"replay failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
