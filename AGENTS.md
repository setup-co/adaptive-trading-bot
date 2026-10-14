# AGENTS.md — Adaptive Trading Bot

This file defines mandatory operating rules for any coding agent (including Codex) working in this repository.

## 1. Read before changing code

Before implementing any issue or changing architecture:

1. Read `docs/trading_bot_master_plan.md`.
2. Read `docs/architect_workflow.md`.
3. Read `docs/project_state.md`, then verify its snapshot against current GitHub `main`, active Issues/PRs and latest architect comments.
4. Read the GitHub issue assigned to the task in full, including the latest architect instruction when present.
5. Inspect existing interfaces and tests relevant to the task.
6. Do not expand scope beyond the issue.

If an issue conflicts with the Master Plan, STOP and report the conflict. Do not silently change architecture. If workflow/state documentation conflicts with current GitHub facts, STOP and surface the mismatch rather than guessing.

## 2. Core architecture locks for Version 1

- Backend language: Python.
- Architecture: Modular Monolith.
- Primary DB: PostgreSQL.
- Historical/large research datasets: Parquet.
- Exchange access must be behind `ExchangeAdapter`.
- Current V1 runtime/execution allowlists and tested trading instruments: BTC and ETH only; do not widen them implicitly.
- A separately qualified, versioned research-only universe of at least ten assets is permitted before Live acceptance; the first bounded panel targets exactly ten. It requires a reviewed research allowlist and locked data. Membership is not Paper/Shadow/Live eligibility, ten concurrent trades, or permission to bypass Risk/Safety. See Master Plan v1.7 and `RESEARCH_V2_CHARTER_V1`.
- Entry timeframe: 15m.
- Context timeframes: 1h and 4h.
- Qualified WEEX perpetual 5m LAST may be canonical historical input to derive those research frames; 5m is not a V1 entry strategy.
- V1 strategy families only:
  - Trend Pullback
  - Breakout + Retest
  - Range Mean Reversion
- V1 primary trading mode: leveraged WEEX perpetual futures; Spot is secondary.
- Leverage is permitted only as an explicit, bounded, versioned configuration owned and capped by `RiskManager`; it is never strategy- or LLM-owned.
- Risk budget is independent of leverage. Position size follows approved risk and stop geometry; leverage must not increase approved risk.
- Isolated margin is preferred for controlled V1 live readiness where supported. Cross margin requires a separate explicit architecture decision.
- Live trading remains disabled until representative leveraged validation covers margin, funding, mark-price and liquidation behavior and every acceptance gate passes.
- Machine Learning: OFF in V1 unless a later approved issue explicitly enables a research-only experiment.
- Deep Learning: OFF in V1.
- Reinforcement Learning: OFF in V1.
- Self-modifying strategy/code: FORBIDDEN.
- Live trading: DISABLED BY DEFAULT.
- `NO_TRADE` is a first-class valid decision.

## 3. Safety invariants

These are non-negotiable:

1. No order may bypass `RiskManager`.
2. No LLM may directly submit an exchange order.
3. No strategy may dynamically increase risk to recover losses.
4. Martingale is forbidden.
5. API credentials must never be committed.
6. Exchange withdrawal permission must never be required by this project.
7. Production/live order execution must fail closed.
8. Stale or invalid market data must prevent new entries.
9. A failed risk gate results in `NO_TRADE` / rejection, never a fallback order.
10. A major unknown system/data state must prefer `SAFE_MODE`.

## 4. Backtest integrity rules

- No look-ahead bias.
- No data leakage.
- No future-derived features.
- Time-series validation must preserve chronology.
- Fees must be modeled.
- Spread/slippage assumptions must be explicit.
- Backtest/Paper/Replay/Live must share the same strategy decision logic.
- Do not create a separate simplified “backtest-only strategy implementation”.

## 5. Explainability requirements

Every trading decision must eventually be reproducible from stored context.

The design must preserve the ability to answer:

- What market data did the bot see?
- What market regime was detected?
- Which strategy/version was evaluated?
- Why was the strategy selected?
- What evidence supported the trade?
- What counter-evidence existed?
- What did fundamental context contribute?
- What did historical experience contribute?
- Which risk checks ran?
- Why was the final result TRADE or NO_TRADE?

## 6. Experience Engine rule

V1 experience is statistical and context-aware, not self-editing AI.

Initial methods:

- Context fingerprints/buckets
- Sample-size-aware statistics
- Bayesian shrinkage/updating
- Recency weighting
- Confidence estimation

Do not add autonomous strategy mutation.

## 7. Scope discipline

For every issue:

- Implement only requested behavior.
- Add or update tests.
- Do not introduce infrastructure “because it may be useful later”.
- Do not add Kafka, Kubernetes, Redis clusters, microservices, vector databases, GPU infrastructure, RL frameworks, or unrelated queues unless an approved issue explicitly requires them.
- Prefer the simplest implementation that preserves future interfaces.

## 8. Code quality

Required for changed code where applicable:

- Type hints.
- Clear module boundaries.
- Deterministic tests.
- UTC timestamps internally.
- Idempotent persistence where repeated market/exchange events are possible.
- Structured logging.
- Explicit exceptions; do not silently swallow failures.
- Configuration via typed settings/environment, never hidden constants for deployment-specific values.

## 9. Git workflow

- `main` is the integration branch.
- Work on one GitHub issue at a time unless explicitly instructed otherwise.
- Create a dedicated branch for each issue.
- Prefer small, reviewable commits.
- Open a Pull Request to `main`.
- PR description must include:
  - Issue reference
  - Summary
  - Design choices
  - Tests executed
  - Risks/limitations
  - Any follow-up intentionally left out
- Do not merge your own PR unless explicitly instructed.

## 10. CI policy

GitHub Actions is used as a quality gate even when deployment is performed directly by Codex.

At minimum as the project grows, CI should run relevant combinations of:

- unit tests
- integration tests
- lint
- formatting check
- type check
- security/static checks where appropriate

Codex must run the required local validation, publish/update the PR and ensure GitHub Actions is triggered, but must not waste tokens polling or waiting for GitHub CI merely to finish. The architect reviews the exact-head GitHub CI result later according to `docs/architect_workflow.md`.

A deployment must not be treated as successful merely because the service starts.

## 11. Deployment policy

### Staging

Preferred workflow: direct deployment by Codex/CLI via the designated staging host.

Rules:

1. Codex must connect using a dedicated non-root deployment user.
2. Never deploy uncommitted local source files as the source of truth.
3. Deploy an exact Git commit/branch from this repository.
4. Preserve server-side secrets outside Git.
5. Run migrations explicitly and safely.
6. Restart only project services.
7. Run post-deploy health/smoke checks.
8. Record the deployed Git SHA.
9. On failure, stop and report; do not improvise destructive fixes.
10. Staging must contain no credentials capable of unrestricted fund withdrawal.

### Production / real-money live trading

Direct autonomous production deployment is NOT authorized during V1 development.

Production deployment will require a separate approved release policy and explicit live-trading acceptance gate after Backtest, Walk-Forward, Paper, and Shadow stages pass.

## 12. Server command safety

Without explicit issue authorization, do not:

- run destructive filesystem commands
- modify global firewall rules
- modify unrelated services
- rotate/delete arbitrary system logs
- change SSH configuration
- install unrelated system packages
- alter database schemas outside project migrations
- delete production/staging data
- expose database/API ports publicly

If elevated privileges are required, explain why and request the smallest required operation.

## 13. Fundamental / LLM rules

- OpenAI is the V1 AI provider behind a project-owned routing boundary. Local LLM, Ollama, vLLM and GPU inference infrastructure are not required or preferred V1 dependencies.
- Deterministic Tier-0 project code remains the sole authority for indicators/features, sizing, risk, leverage/margin/liquidation, funding/fees/PnL/accounting, stop/target geometry, Risk/Safety gates, order authorization, reconciliation/state-machine invariants and backtest/replay accounting. An LLM may explain these facts but must never supply their authoritative arithmetic or execution authority.
- Logical tiers are `AI_ROUTINE`, `AI_BALANCED`, `AI_COMPLEX` and optional `AI_FRONTIER`. Concrete model IDs belong only in typed, versioned configuration, never strategy logic or permanent architecture constants.
- Use the cheapest tier validated for each task. Retry and escalation must be bounded, predeclared, versioned and auditable; a required unavailable stronger tier must not be silently replaced by a weaker tier.
- LLM outputs must be structured, validated and auditable, with explicit unavailable/unknown semantics on failure.
- LLM analysis must use caching, deduplication and explicit token/spend budgets where semantically safe.
- News deduplication/event clustering is required so multiple articles about one event do not become multiple signals.
- AI confidence is evidence, not authorization to trade, select leverage, or bypass Risk/Safety.

## 14. Definition of done for an issue

An issue is not done until:

- Acceptance criteria are met.
- Tests relevant to the change pass.
- No Master Plan invariant is knowingly violated.
- Failure behavior has been considered.
- Documentation is updated when behavior/interfaces/configuration change.
- The PR is small enough for meaningful review.

## 15. When uncertain

Prefer:

`STOP + REPORT + ASK/FLAG`

over
`GUESS + SILENT ARCHITECTURE CHANGE`

Capital preservation, data integrity, reproducibility, risk control, explainability, and robustness have higher priority than feature count or coding speed.
