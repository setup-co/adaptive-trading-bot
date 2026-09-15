"""Fail-closed CLI for deterministic Trend Pullback research-corpus generation."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.config import get_settings
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.historical_sources import (
    HistoricalSourceSelectionError,
    open_historical_store,
)
from adaptive_trading_bot.replay import ReplayError, ReplaySpeed
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchAdmissionConfiguration,
    TrendPullbackHistoricalCorpusGenerator,
    TrendPullbackResearchCorpusError,
)
from adaptive_trading_bot.storage import SourceId


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamps must be timezone-aware UTC")
    return parsed


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Generate a research-only Trend Pullback corpus")
    parser.add_argument(
        "--data-root",
        type=Path,
        help="optional generic historical root override; never used for qualified WEEX history",
    )
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument(
        "--dataset-version",
        help=(
            "required for qualified WEEX source IDs; opens the immutable version under "
            "ATB_QUALIFIED_RESEARCH_CORPUS_ROOT"
        ),
    )
    parser.add_argument(
        "--symbol", action="append", choices=("BTC/USDT", "ETH/USDT"), required=True
    )
    parser.add_argument("--start", type=_parse_utc, required=True)
    parser.add_argument("--end", type=_parse_utc, required=True)
    parser.add_argument("--research-equity", type=Decimal, required=True)
    parser.add_argument(
        "--speed", choices=tuple(speed.label for speed in ReplaySpeed), default="100x"
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate a deterministic JSON research artifact from an explicitly selected store."""

    arguments = _parser().parse_args(argv)
    try:
        source_ids = tuple(SourceId(value) for value in arguments.source)
        symbols = tuple(TradingSymbol(*value.split("/", maxsplit=1)) for value in arguments.symbol)
        settings = get_settings()
        store = open_historical_store(
            source_ids=source_ids,
            generic_root=arguments.data_root or settings.historical_market_data_root,
            qualified_corpus_root=settings.qualified_research_corpus_root,
            dataset_version=arguments.dataset_version,
        )
        configuration = ResearchAdmissionConfiguration(
            source_ids=source_ids,
            symbols=symbols,
            start=arguments.start,
            end=arguments.end,
            research_equity_quote=arguments.research_equity,
        )
        corpus = TrendPullbackHistoricalCorpusGenerator(store).generate(
            configuration,
            replay_speed=ReplaySpeed.parse(arguments.speed),
        )
        artifact = corpus.to_json()
        if arguments.output is None:
            print(artifact)
        else:
            arguments.output.write_text(artifact + "\n", encoding="utf-8")
    except (
        HistoricalSourceSelectionError,
        ReplayError,
        TrendPullbackResearchCorpusError,
        ValueError,
    ) as error:
        print(f"research corpus failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
