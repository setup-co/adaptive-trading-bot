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

## Safety

- No committed secrets.
- No withdrawal permission required.
- No leverage in V1.
- No self-modifying strategy code.
- No LLM direct order execution.
- No order may bypass Risk Manager.
- Production/live execution remains locked until the V1 acceptance gate is passed.
