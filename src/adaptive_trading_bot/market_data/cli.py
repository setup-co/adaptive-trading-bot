"""One-shot CLI for bounded public OHLCV imports."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime

from adaptive_trading_bot.config import get_settings
from adaptive_trading_bot.domain import SystemClock, Timeframe, TradingSymbol
from adaptive_trading_bot.market_data.acquisition import (
    BoundedOHLCVImporter,
    MarketDataAcquisitionError,
)
from adaptive_trading_bot.market_data.ccxt_public import CcxtPublicOHLCVSource
from adaptive_trading_bot.storage import HistoricalCandleStoreError, ParquetHistoricalCandleStore


def _utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from error
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise argparse.ArgumentTypeError("timestamps must be timezone-aware UTC")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import bounded public spot OHLCV candles")
    parser.add_argument("--exchange", required=True, help="CCXT exchange id, for example binance")
    parser.add_argument("--symbol", required=True, help="V1 spot symbol: BTC/USDT or ETH/USDT")
    parser.add_argument("--timeframe", required=True, choices=[item.value for item in Timeframe])
    parser.add_argument("--start", required=True, type=_utc_datetime, help="inclusive UTC boundary")
    parser.add_argument("--end", required=True, type=_utc_datetime, help="exclusive UTC boundary")
    parser.add_argument("--limit", type=int, help="CCXT page limit (1-1000)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one import and return a process exit code."""

    args = _parser().parse_args(argv)
    settings = get_settings()
    limit = settings.ccxt_ohlcv_page_limit if args.limit is None else args.limit
    try:
        symbol = TradingSymbol.parse(args.symbol)
        timeframe = Timeframe(args.timeframe)
        importer = BoundedOHLCVImporter(
            ParquetHistoricalCandleStore(settings.historical_market_data_root),
            SystemClock(),
            page_limit=limit,
        )
        importer.validate_request(symbol, timeframe, args.start, args.end)
        source = CcxtPublicOHLCVSource(
            args.exchange,
            timeout_ms=settings.ccxt_request_timeout_ms,
        )
        result = importer.import_range(
            source,
            symbol,
            timeframe,
            start=args.start,
            end=args.end,
        )
    except (MarketDataAcquisitionError, HistoricalCandleStoreError, ValueError) as error:
        print(f"import failed: {error}", file=sys.stderr)
        return 1

    if not result.integrity.is_clean:
        print(
            json.dumps(
                {
                    "error": "dirty_integrity",
                    "source_id": str(result.source_id),
                    "symbol": str(result.symbol),
                    "timeframe": result.timeframe.value,
                    "requested_start": result.requested_start.isoformat(),
                    "requested_end": result.requested_end.isoformat(),
                    "effective_end": result.effective_end.isoformat(),
                    "integrity": {
                        "clean": False,
                        "rows_scanned": result.integrity.rows_scanned,
                        "issues": [
                            {"kind": issue.kind.value, "message": issue.message}
                            for issue in result.integrity.issues
                        ],
                    },
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "source_id": str(result.source_id),
                "symbol": str(result.symbol),
                "timeframe": result.timeframe.value,
                "requested_start": result.requested_start.isoformat(),
                "requested_end": result.requested_end.isoformat(),
                "effective_end": result.effective_end.isoformat(),
                "fetched": result.fetched,
                "imported": result.merge.added,
                "unchanged": result.merge.unchanged,
                "partitions_written": result.merge.partitions_written,
                "integrity": {
                    "clean": result.integrity.is_clean,
                    "rows_scanned": result.integrity.rows_scanned,
                    "issues": [
                        {"kind": issue.kind.value, "message": issue.message}
                        for issue in result.integrity.issues
                    ],
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
