# Adaptive Trading Bot

Safety-first, explainable, adaptive crypto trading research and execution platform.

## Current status

**Version 1 development — Phase 1: Project Foundation**

Real-money live trading is **disabled by design** during V1 development.

## Source of truth

Before changing code, read:

1. [`AGENTS.md`](AGENTS.md) — mandatory operating rules for Codex/coding agents.
2. [`docs/trading_bot_master_plan.md`](docs/trading_bot_master_plan.md) — architecture, V1 scope, acceptance gates, and maturity roadmap.

## V1 baseline

- Python Modular Monolith
- PostgreSQL + Parquet
- BTC / ETH only
- 15m entry timeframe
- 1h / 4h context
- Trend Pullback
- Breakout + Retest
- Range Mean Reversion
- Independent Risk Manager
- Backtest / Replay / Paper / Shadow before controlled Live
- Statistical/Bayesian Experience Engine before ML
- Hybrid Fundamental Analysis
- `NO_TRADE` is a valid first-class decision

## Development workflow

```text
GitHub Issue
    ↓
Codex implementation branch
    ↓
Pull Request
    ↓
CI + architecture/quant review
    ↓
Merge approved commit
    ↓
Codex direct deployment to restricted Staging
    ↓
Smoke / health checks + deployed SHA record
```

GitHub is the source of truth. Direct Staging deployment is used for iteration speed; GitHub Actions remains an independent CI/quality gate.

## Local development

The project uses a `src` package layout so imports always resolve from the installed package,
preventing accidental reliance on the repository working directory. Python 3.12 or newer is
required.

Create a virtual environment and install the application with its explicit development tools:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env       # Windows PowerShell: Copy-Item .env.example .env
```

Run the API locally (the host and port shown match the safe defaults):

```bash
uvicorn adaptive_trading_bot.api:app --host 127.0.0.1 --port 8000 --reload
```

Run the same quality gates used by CI:

```bash
ruff format --check .
ruff check .
mypy src tests migrations
pytest
```

To apply or inspect migrations after configuring a reachable PostgreSQL database:

```bash
alembic upgrade head
alembic current
```

`GET /health` is process liveness only. It deliberately does not report database or exchange
readiness. Live trading is controlled by `ATB_LIVE_TRADING_ENABLED` and defaults to `false`.

## Market-data domain conventions

Internal symbols use normalized exchange-neutral `BASE/QUOTE` notation. V1 candle timeframes are
limited to `15m`, `1h`, and `4h`; all domain timestamps must be aware UTC values. Candles are
immutable, use `Decimal` for OHLCV values, and cover half-open intervals
`[open_time, close_time)`, where `close_time` is exactly one timeframe after `open_time`.

### Historical candle dataset

Historical candles use PyArrow `>=25.0,<26` and schema version `1`. Files are monthly partitions:

```text
<market_data_root>/source=<source_id>/base=<BASE>/quote=<QUOTE>/timeframe=<timeframe>/year=<YYYY>/month=<MM>/candles.parquet
```

The normalized `source_id` is included in both the path and Arrow schema metadata, so sources are
never mixed. Timestamps use `timestamp[us, tz=UTC]`, matching Python `datetime` microsecond
resolution. OHLCV columns use `decimal128(38, 18)`: 20 integer digits and 18 fractional digits,
which comfortably covers V1 BTC/ETH/USDT prices and volumes. Values outside that precision/scale
are rejected instead of rounded.

Reads select candle `open_time` with inclusive-start/exclusive-end semantics (`[start, end)`) and
return immutable `Candle` values in chronological order. Identical writes are idempotent;
conflicting values for `(source, symbol, timeframe, open_time)` fail closed. Monthly updates are
written to a same-directory temporary file before atomic replacement. Integrity scans report
schema/provenance mismatches, malformed rows, duplicates, physical ordering errors, gaps, and
overlaps without modifying, interpolating, or synthesizing data.

### Public OHLCV import

Public spot OHLCV acquisition uses CCXT `>=4.5,<5` only at the infrastructure boundary. The
adapter enables CCXT rate limiting, uses a finite timeout, loads markets, and fails closed unless
the exchange supports public OHLCV, the requested V1 timeframe, and the exact spot market. Source
identities use `ccxt-<exchange-id>-spot`; no credentials are accepted or stored.

Run a finite import with UTC-aligned, inclusive-start/exclusive-end boundaries:

```bash
atb-import-ohlcv --exchange binance --symbol BTC/USDT --timeframe 15m \
  --start 2026-01-01T00:00:00Z --end 2026-01-02T00:00:00Z
```

Only candles closed by `min(requested_end, current_time)` are persisted. The command reports its
effective closed-candle boundary and a non-mutating integrity scan; it never repairs missing data.
OHLCV values use `Decimal(str(value))` without rounding. If CCXT's JSON decoder has already
represented a wire number as a binary float, the original lexical precision cannot be recovered.

### Deterministic replay core

Replay reads immutable candles only through the historical-store contract. Every requested series
is integrity-scanned across its complete candle-open range before any series is read or any event
can be emitted; one dirty series blocks the entire replay without repair or synthesis.

A stored candle covering `[open_time, close_time)` becomes visible only at `close_time`. Replay
groups every candle with the same close into one atomic `ReplayBatch`, advances the shared replay
clock to that time before returning the batch, and orders simultaneous members canonically by
source ID, symbol, timeframe duration, then timeframe value. Sessions expose only one-batch
stepping/iteration and exhaustion state—no future-candle lookup, wall-clock sleep, or pacing.

## Safety

- No committed secrets.
- No withdrawal permission required.
- No leverage in V1.
- No self-modifying strategy code.
- No LLM direct order execution.
- No order may bypass Risk Manager.
- Production/live execution remains locked until the V1 acceptance gate is passed.
