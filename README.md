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

## Safety

- No committed secrets.
- No withdrawal permission required.
- No leverage in V1.
- No self-modifying strategy code.
- No LLM direct order execution.
- No order may bypass Risk Manager.
- Production/live execution remains locked until the V1 acceptance gate is passed.
