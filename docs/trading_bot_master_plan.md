# Adaptive Trading Bot — Master Plan

**Status:** Project source of truth  
**Document version:** 1.1  
**Created:** 2026-09-09  
**Repository:** `setup-co/adaptive-trading-bot`

---

# 1. Purpose of this document

This document is the architectural and product source of truth for the Adaptive Trading Bot project. It exists to prevent scope drift, forgotten decisions, accidental overengineering, unsafe live-trading shortcuts, and AI-driven changes that cannot be reproduced or audited.

Any material architecture change must be recorded in the Change Log before it becomes the new default.

Priority order:

1. Prevent catastrophic loss.
2. Data integrity.
3. Reproducibility.
4. Risk control.
5. Explainability.
6. Robustness.
7. Learning from experience.
8. Fundamental/context intelligence.
9. Sustainable profitability if a real edge is demonstrated.
10. Expansion of assets/strategies.
11. Scale/performance.
12. Additional AI complexity.

The project is not considered successful merely because a backtest is profitable.

---

# 2. Product vision

Build an adaptive, explainable, risk-controlled crypto trading platform that can:

- collect and persist market data;
- understand current market context and regime;
- evaluate multiple strategy families;
- route each market context toward the most appropriate approved strategy;
- combine technical evidence, fundamental context, historical experience and execution quality;
- explicitly choose `NO_TRADE` when the edge is weak or risk is unacceptable;
- journal every decision and every trade;
- learn statistically from prior trades without self-modifying production code;
- replay historical markets through the same strategy interfaces used by paper/live execution;
- backtest and walk-forward validate strategies while preventing future leakage;
- provide human-readable explanations for trade and no-trade decisions;
- progressively mature into a multi-asset, multi-strategy, portfolio-aware research and trading platform.

---

# 3. Non-negotiable principles

## 3.1 No guaranteed profitability

No strategy, indicator, ML model, LLM, optimization result, or backtest is assumed profitable until validated out-of-sample and through realistic execution stages.

## 3.2 Trade count is not a target

The bot must not target “20 trades per day” or any fixed number of trades. Trading frequency is an outcome of valid setups, not a KPI.

## 3.3 `NO_TRADE` is a first-class decision

Valid outputs include:

- `LONG`
- `SHORT`
- `CLOSE`
- `REDUCE`
- `NO_TRADE`

## 3.4 Risk Manager is sovereign

No strategy, LLM, ML model, operator convenience, or news event may bypass the Risk Manager.

## 3.5 LLM is an analyst, not an execution engine

LLMs may analyze news, summarize context, generate hypotheses, and explain decisions. They may not directly authorize/send orders.

## 3.6 No autonomous self-modification

The bot must never react to a loss by editing its strategy code or raising risk.

Any strategy change follows:

`Hypothesis → Formal Rule → Backtest → Robustness → Walk Forward → Replay → Paper → Shadow → Approval → Versioned Strategy`

## 3.7 Same strategy logic across environments

Backtest, Replay, Paper and Live must share the same strategy decision implementation. Environment-specific adapters may differ, but strategy logic must not be duplicated into separate simplified implementations.

## 3.8 No curve fitting

A parameter that works only at one narrow optimized value is considered suspicious. Robust parameter regions matter more than peak backtest returns.

## 3.9 Process quality is separate from outcome quality

A valid trade can lose. A poor trade can win by chance. Journal and learning systems must distinguish decision/process quality from realized PnL.

---

# 4. Version 1 scope

## Assets

- BTC
- ETH

Do not add other assets until the V1 acceptance gate is passed.

## Timeframes

- Entry timeframe: `15m`
- Context: `1h`, `4h`

Very short timeframes such as 1m/3m/5m are not part of the V1 trading core.

## Leverage

- OFF in V1.

## Strategy families

Only three initial families:

1. Trend Pullback
2. Breakout + Retest
3. Range Mean Reversion

The project must not become a catalog of dozens of strategies before these are measurable and explainable.

---

# 5. V1 architecture

Use a **Python Modular Monolith**.

Logical modules:

```text
Market / Exchange
      ↓
Market Data Collector
      ↓
Market Storage
      ↓
Feature Engine ─────────┐
      ↓                 │
Market Regime Engine    │
      ↓                 │
Strategy Engine         │
      ↓                 │
Strategy Router         │
      ↓                 │
                       Decision Engine
Fundamental Engine ─────┤
Experience Engine ──────┤
Execution Quality ──────┘
          ↓
      Risk Manager
          ↓
     Order Manager
          ↓
   Exchange Adapter
          ↓
       Exchange
          ↓
     Trade Journal
          ↓
 Post-Trade Analyzer
          ↓
 Experience Database
```

Dashboard/API observes the system; it does not bypass core decision/risk layers.

---

# 6. Technology baseline

## Backend

- Python
- FastAPI
- Pydantic / typed settings
- NumPy
- Pandas or Polars where appropriate

## Persistence

- PostgreSQL: transactional/system records
- Parquet: larger historical/research datasets

## Exchange abstraction

Create a project-owned `ExchangeAdapter` interface. A CCXT-backed adapter may be used where appropriate, but exchange-specific behavior must remain isolated.

## Frontend

Keep V1 dashboard lightweight. React is acceptable, but dashboard sophistication must not delay the trading/research core.

## Architecture deliberately excluded from V1

- Kubernetes
- Kafka
- Redis Cluster
- Microservices
- Reinforcement Learning
- Deep Learning
- GPU training infrastructure
- autonomous strategy generation/deployment
- vector database for trading core

---

# 7. Market data V1

Collect/store as needed:

- OHLCV
- ticker
- trades
- volume
- spread

When reliable data is available, contextual derivatives data may be stored:

- funding rate
- open interest
- liquidations

Deep order-book microstructure is not a V1 requirement.

All internal timestamps are UTC.

Required data properties:

- idempotent writes where applicable;
- gap detection;
- freshness detection;
- reconnect/retry behavior;
- provenance/source metadata;
- deterministic historical ordering.

---

# 8. Feature Engine V1

Keep features intentionally small and interpretable:

- EMA 20
- EMA 50
- ATR 14
- RSI 14
- VWAP
- Volume / Volume Z-Score
- swing high / swing low
- higher high / higher low
- lower high / lower low
- price distance from important levels
- candle range relative to ATR
- basic returns/momentum
- basic volatility features

No indicator may be added merely because it is popular.

---

# 9. Market Regime Engine

Regime is multi-dimensional, not a single label.

Minimum context fields:

- direction
- trend_strength
- volatility_percentile
- volume_state
- liquidity_state
- event_risk
- regime_label

Example:

```json
{
  "direction": "up",
  "trend_strength": 82,
  "volatility_percentile": 71,
  "volume_state": "high",
  "liquidity_state": "normal",
  "event_risk": 24,
  "regime": "strong_uptrend"
}
```

Regime is used to enable/disable/rank strategies.

---

# 10. Technical Analysis Engine

Technical analysis must emit structured evidence rather than a magical number only.

Example:

```json
{
  "asset": "BTC",
  "timeframe": "15m",
  "direction": "bullish",
  "score": 74,
  "confidence": 0.81,
  "evidence": [
    "4h trend bullish",
    "1h trend bullish",
    "successful pullback",
    "volume confirmation"
  ],
  "counter_evidence": [
    "RSI elevated"
  ]
}
```

`evidence` and `counter_evidence` are both mandatory concepts.

---

# 11. Initial strategies

## 11.1 Trend Pullback

Active only when an approved trend context exists.

Typical evidence may include:

- higher-timeframe trend alignment;
- controlled pullback;
- value-zone interaction;
- momentum recovery;
- volume confirmation;
- valid stop geometry.

Exact definitions must be formally specified and testable before implementation.

## 11.2 Breakout + Retest

Raw breakout chasing is not the strategy.

Conceptual flow:

`Valid Level → Breakout → Volume/Quality Confirmation → Retest → Hold → Entry Candidate`

The specification must define:

- level validity;
- breakout validity;
- retest tolerance;
- retest expiry;
- invalidation;
- stop placement;
- target/exit behavior.

## 11.3 Range Mean Reversion

Active only in validated range contexts.

Typical evidence:

- range boundary;
- exhaustion/mean-reversion condition;
- no genuine breakout confirmation;
- no major opposing fundamental shock.

---

# 12. Strategy Router

The bot is a meta-strategy platform, not a single-indicator bot.

Strategy Router responsibilities:

1. read current market context;
2. identify strategies compatible with the regime;
3. retrieve context-aware historical performance;
4. eliminate incompatible/low-confidence candidates;
5. rank remaining candidates;
6. send candidate(s) and evidence to the Decision Engine.

A strategy’s historical success in one regime must not automatically generalize to another.

---

# 13. Fundamental architecture

Use a **Hybrid** architecture.

```text
News / Macro / On-chain / Market Context
                  ↓
             Collection
                  ↓
            Normalization
                  ↓
            Deduplication
                  ↓
          Event Clustering
                  ↓
          Importance Filter
            ┌─────┴──────────┐
            │                │
       Low importance     Important
            │                │
          Rules          Local LLM
                             │
                         Critical /
                         Ambiguous
                             │
                         Cloud LLM
                             │
                    Fundamental Event DB
                             ↓
                     Fundamental Score
```

Cloud LLM is escalation-only, not per candle/tick.

## Fundamental event output

Structured event fields should eventually include:

- assets
- direction
- impact
- confidence
- source_quality
- event_type
- time_horizon
- freshness/time decay
- summary
- risks/counterpoints

## Source quality

Sources require quality/reliability classification. Low-quality social claims must not independently create an entry.

## Deduplication

Many articles describing the same real-world event must become one canonical event with multiple confirming sources, not many independent bullish/bearish signals.

## Time decay

Conceptually:

`effective_impact = raw_impact × confidence × source_quality × time_decay`

Decay depends on event type.

## Fundamental role in V1

Fundamental context mainly:

- confirms;
- rejects;
- raises/lowers confidence;
- blocks trading during uncertain/major events.

It does not independently authorize orders.

---

# 14. Decision Engine

## Hard gates first

Examples:

- data fresh?
- exchange healthy?
- spread acceptable?
- risk limits available?
- major unknown news?
- strategy enabled?
- regime compatible?
- execution context acceptable?

A critical failure produces `NO_TRADE`/rejection.

## Soft decision after gates

Combine structured evidence from:

- technical analysis;
- fundamental context;
- historical/contextual edge;
- execution quality;
- risk context;
- confidence.

The exact weighting must be measurable/versioned and must not be an opaque LLM-only decision.

---

# 15. Risk Manager

Risk is independent of strategy.

Initial engineering defaults for controlled testing (configurable; not personal investment advice):

- leverage: 0;
- risk per trade around 0.25% of test account;
- simultaneous risk around 0.5%;
- daily loss limit around 1%;
- larger drawdown threshold triggers halt/review.

Responsibilities:

- position sizing;
- stop validation;
- take-profit/exit validation;
- minimum expected reward/risk policy where used;
- daily loss control;
- drawdown control;
- simultaneous/correlated exposure;
- cooldown logic;
- spread/slippage tolerance;
- data/exchange health gates.

Position size follows risk budget and stop distance; risk budget does not follow desired position size.

---

# 16. Circuit breaker / Safe Mode

New entries are blocked under conditions such as:

- stale market data;
- exchange/API instability;
- abnormal spread;
- extreme/unclassified volatility;
- major unknown news;
- repeated order rejection;
- risk-limit breach;
- time synchronization fault;
- corrupted/broken feed;
- suspicious price anomaly.

System state includes `SAFE_MODE` and later may mature into a richer risk-state machine.

---

# 17. Trade Journal

Every trade must store the pre-trade context and post-trade outcome.

Before entry, capture at minimum:

- trade_id
- asset/symbol
- timestamp/timeframe
- strategy + strategy version
- regime/context
- higher-timeframe context
- technical score/evidence
- fundamental score/events
- historical/context edge
- volatility/volume state
- derivatives context when available
- entry/stop/target
- position size/risk
- expected R/R
- spread/slippage/fee estimates
- reasons_for
- reasons_against
- decision confidence

After exit:

- exit price/time
- exit reason
- realized PnL
- realized R
- MFE
- MAE
- fees
- realized slippage
- duration
- process score
- outcome classification

---

# 18. Experience Engine V1

Do not use heavy ML initially.

V1 experience methods:

- context fingerprints/bucketing;
- statistics;
- Bayesian shrinkage/updating;
- recency weighting;
- minimum sample-size controls;
- confidence estimation.

Context fingerprint may include:

- asset;
- strategy/version;
- timeframe;
- higher-timeframe direction;
- trend-strength bucket;
- volatility bucket;
- volume bucket;
- fundamental bucket;
- funding/OI bucket where available;
- session;
- market regime.

Metrics per context:

- sample count;
- win rate;
- profit factor;
- average/median R;
- expectancy;
- MFE/MAE;
- losing streak;
- drawdown;
- recent performance;
- confidence;
- Bayesian-adjusted edge.

Small samples must shrink toward a broader prior rather than be treated as strong evidence.

Recent data may receive more weight, but old data is not automatically forgotten.

---

# 19. Backtest Engine

Must model relevant real-world effects:

- fees;
- spread;
- slippage;
- position sizing;
- stop execution assumptions;
- execution latency assumptions where meaningful;
- partial fills if/when strategy requires them.

Prevent:

- look-ahead bias;
- future-derived features;
- data leakage;
- hidden survivorship assumptions;
- over-optimization.

Key metrics include:

- net profit;
- maximum drawdown;
- profit factor;
- expectancy;
- average/median R;
- win rate;
- average win/loss;
- trade count;
- Sharpe/Sortino where meaningful;
- return/drawdown;
- tail loss;
- losing streak;
- stability across time periods;
- stability across regimes.

---

# 20. Replay Engine

Replay is an early V1 component, not a late debugging add-on.

Target speeds:

- 1x
- 10x
- 50x
- 100x

Replay must allow inspection of:

- market/regime state;
- strategy candidates;
- selected strategy;
- technical/fundamental evidence;
- risk rejections;
- `NO_TRADE` decisions;
- simulated entries/exits.

Use a clock abstraction so historical/replay/live time do not require strategy rewrites.

---

# 21. Validation ladder

Required progression:

`Backtest → Robustness → Walk Forward → Out-of-Sample → Replay → Paper → Shadow → Very Small Controlled Live → Controlled Scaling`

Live is not the starting point.

## Robustness

Reject parameter sets that work only at a single narrow optimum.

## Walk Forward

Preserve chronological ordering and prohibit future information from training/parameter selection.

## Paper Trading

Validate real data feeds, state transitions, order lifecycle simulation, failure handling and journaling.

## Shadow Trading

Produce genuine live-market decisions but do not send real orders. Compare hypothetical execution/outcomes to the live market.

---

# 22. V1 acceptance gate

Real-money live execution remains disabled unless all critical gates pass:

- data integrity;
- backtest reproducibility;
- no known look-ahead/data leakage;
- risk manager coverage;
- replay correctness;
- paper stability;
- shadow stability;
- explainability;
- auditability;
- fundamental pipeline integrity;
- out-of-sample edge that remains defensible after costs;
- drawdown within approved limits;
- failure recovery tested;
- safe mode tested;
- credential/security review complete.

Any critical failure means:

`LIVE_TRADING = DISABLED`

---

# 23. Dashboard V1

Minimum areas:

## Market
- BTC/ETH price
- regime
- volatility
- technical score
- fundamental score
- system state

## Chart
- candlesticks
- entry/exit
- stop/target
- key EMA/VWAP/levels
- strategy signals
- historical trades

## Decisions
- decision (`LONG/SHORT/NO_TRADE/...`)
- confidence
- technical evidence
- counter-evidence
- fundamental context
- historical edge
- risk rejection explanation

## Trades
- complete journal

## Experience
- performance by strategy/regime/context

## System
- data freshness
- exchange health
- last news update
- errors
- safe mode
- active workers/services

---

# 24. Explainability and auditability

Every decision should later be reconstructable from:

- data seen;
- feature versions;
- market regime;
- strategy version;
- configuration version;
- fundamental events;
- experience statistics;
- risk checks;
- final decision.

Version:

- strategies;
- configurations;
- ML models when introduced;
- local LLM models relevant to decision pipeline;
- fundamental prompts/schemas.

---

# 25. Repository structure target

```text
adaptive-trading-bot/
├── AGENTS.md
├── README.md
├── app/
│   ├── market_data/
│   ├── features/
│   ├── regime/
│   ├── strategies/
│   ├── technical/
│   ├── fundamental/
│   ├── experience/
│   ├── decision/
│   ├── risk/
│   ├── execution/
│   ├── exchange/
│   ├── journal/
│   ├── backtest/
│   ├── replay/
│   ├── paper/
│   ├── live/
│   ├── api/
│   └── core/
├── tests/
├── migrations/
├── config/
├── data/
├── scripts/
└── docs/
    └── trading_bot_master_plan.md
```

The actual directories should be created only as phases require them; do not create meaningless empty architecture for appearance.

---

# 26. V1 implementation roadmap

## Phase 1 — Project Foundation

- Python project/package baseline
- typed config/settings
- structured logging
- PostgreSQL integration
- migrations
- testing framework
- core interfaces
- basic API health endpoint
- CI baseline

**Done when:** application boots; config is typed; tests run; DB integration pattern exists; logs are structured; CI can validate the repository.

## Phase 2 — Market Data + Storage

- ExchangeAdapter baseline
- OHLCV collection
- ticker/spread/trade data as required
- idempotent persistence
- gap detection
- reconnect/retry
- Parquet historical writer/reader

## Phase 3 — Replay Engine

- Clock abstraction
- historical data feed
- deterministic playback
- replay speeds

## Phase 4 — Feature Engine

- approved V1 features
- no future leakage
- deterministic feature tests

## Phase 5 — Backtest Engine

- common strategy interface
- execution simulation
- fees/spread/slippage
- results/metrics

## Phase 6 — Market Regime Engine

- direction
- trend strength
- volatility
- volume state
- regime label/context

## Phase 7 — Strategy Specifications + Engine

Formalize and implement:

- Trend Pullback
- Breakout + Retest
- Range Mean Reversion

## Phase 8 — Risk Manager

- position sizing
- risk/trade
- simultaneous risk
- daily loss
- drawdown
- spread/slippage gates
- safe mode

## Phase 9 — Trade Journal

- full decision/trade snapshots
- reasons for/against
- MFE/MAE
- process/outcome quality

## Phase 10 — Paper Trading

- live market feed
- simulated execution
- paper positions/orders

## Phase 11 — Dashboard

- market/chart/decision/trades/system views

## Phase 12 — Experience Engine

- context fingerprints
- context metrics
- Bayesian adjustment
- recency weighting
- confidence

## Phase 13 — Strategy Router

- compatibility rules
- context-aware historical performance
- ranking

## Phase 14 — News Collector

- multiple sources
- normalization
- deduplication
- clustering
- source quality

## Phase 15 — Fundamental Rules Engine

- event taxonomy
- importance
- sentiment/direction
- time decay
- fundamental score

## Phase 16 — Local LLM Fundamental Analysis

- structured schema
- caching
- versioned prompts/model
- confidence/audit logs

## Phase 17 — Optional Cloud LLM Escalation

Only critical/ambiguous/multi-source events.

## Phase 18 — Walk Forward + Robustness Lab

- temporal split tooling
- parameter robustness
- out-of-sample tests
- regime stability

## Phase 19 — Shadow Trading

- live decision generation
- no real orders
- reconciliation of hypothetical outcomes

## Phase 20 — V1 Acceptance Review

Live remains disabled until explicitly approved after all required gates.

---

# 27. Deployment model selected for development

## Source of truth

GitHub repository and committed Git SHA.

## CI

GitHub Actions is retained as an independent quality gate for PRs/commits.

It may run, as appropriate:

- unit/integration tests;
- lint/format checks;
- type checks;
- security/static checks;
- package/build checks.

## Staging deployment

Preferred V1 development workflow: **direct deployment by Codex/CLI to a dedicated staging server**, using Remote SSH or Codex running in the designated development environment.

Direct deployment is chosen for iteration speed, but obeys these rules:

1. use a dedicated non-root OS deployment user;
2. server secrets remain outside Git;
3. deploy an exact committed Git SHA — never an untracked local code snapshot;
4. CI/test status should be checked before a release is treated as valid;
5. run migrations explicitly;
6. restart only project services;
7. run health/smoke checks after deploy;
8. record deployed Git SHA;
9. maintain a rollback path to a prior known-good SHA;
10. no real-money exchange credentials on early development environments;
11. no unrestricted withdrawal permissions ever;
12. direct production/live deployment is not authorized during V1 development.

The intended loop is:

```text
GitHub Issue
   ↓
Codex implementation branch
   ↓
Pull Request
   ↓
CI + ChatGPT review
   ↓
Merge / approved commit
   ↓
Codex direct deploy to Staging
   ↓
Migration / restart / smoke tests
   ↓
Record deployed SHA
```

This provides faster deployment without turning the staging server into the source of truth.

---

# 28. Post-V1 maturity roadmap

The following work happens only after V1 proves stability/edge.

## V1.1 — Hardening

- exchange reconciliation
- idempotent order lifecycle
- failure recovery
- clock-drift handling
- backups/restore tests
- monitoring/alerting
- security review
- chaos/failure testing

## V1.2 — Controlled Strategy Expansion

Candidates:

- Momentum
- Volatility Expansion
- Trend Continuation
- Event-driven setups

Every strategy follows the full research/validation ladder.

## V1.3 — Asset Expansion

Add highly liquid assets gradually (e.g. SOL) only after liquidity/spread/history/regime tests.

## V1.4 — Multi-Timeframe Intelligence

Potentially add 5m only for execution timing plus 1d context, without turning the bot into an uncontrolled high-noise scalper.

## V1.5 — Derivatives Context

- funding
- open interest
- liquidations
- basis
- reliable positioning indicators

Primarily as context/risk inputs initially.

## V1.6 — On-chain Intelligence

Only measurable/backtestable signals such as exchange flows, stablecoin flows, whale activity and selected supply metrics.

## V1.7 — Macro Intelligence

- rates
- CPI
- labor data
- central banks
- liquidity conditions
- policy/geopolitical event context

## V1.8 — Fundamental AI Improvement

- richer event taxonomy
- better source verification
- confidence calibration
- improved local models
- stricter cloud escalation/cost monitoring

---

# 29. Version 2 — ML Meta Model

Introduce ML only after enough clean journal/context data exists.

Start with interpretable/tabular approaches such as gradient boosting rather than deep neural networks.

Potential ML goals:

- setup-quality probability;
- strategy candidate ranking;
- expected R estimation;
- stop/target probability estimation;
- MFE/MAE estimation.

ML remains subordinate to risk gates.

Required validation:

- time-series split;
- walk-forward;
- out-of-sample;
- calibration;
- feature-drift monitoring;
- regime breakdown;
- ablation testing.

Future controlled adaptive methods may include:

- Contextual Bandits
- adaptive strategy weighting

but never direct uncontrolled capital authority.

---

# 30. Version 2.x — Portfolio and Execution Maturity

When assets/strategies expand:

## Portfolio Risk Engine

- correlations/clusters;
- total exposure;
- crypto beta/factor exposure;
- liquidity constraints;
- portfolio drawdown;
- VaR/CVaR where useful.

## Advanced Execution

- limit/maker-taker optimization;
- partial fills;
- smart order placement;
- execution quality score;
- slippage models.

## Microstructure

Order-book imbalance/depth/microprice is considered only if a strategy has a demonstrated need.

---

# 31. Version 3 — AI Research Layer

LLM becomes a research assistant, not a production trader.

Possible tasks:

- analyze journal failures;
- discover context patterns;
- propose features;
- generate testable hypotheses;
- suggest strategy variations.

All AI-generated research enters:

`Hypothesis → Formal Test → Backtest → Robustness → Walk Forward → Shadow → Approval`

Future components:

- automated experiment lab;
- feature ablation;
- parameter sweeps with selection-bias controls;
- drift detection;
- probabilistic regime discovery;
- richer context similarity/experience memory.

---

# 32. Version 4 — Multi-Exchange / Portfolio Platform

After sufficient maturity:

- multiple exchanges;
- exchange-health routing;
- execution redundancy;
- capital allocation across strategies/assets;
- dynamic risk-aware capital allocation;
- richer risk state machine such as:
  - NORMAL
  - CAUTIOUS
  - DEFENSIVE
  - SAFE_MODE
  - HALTED

---

# 33. Mature target state

A mature platform may eventually include:

- multi-asset;
- multi-strategy;
- multi-exchange;
- technical/fundamental/on-chain/macro context;
- context-aware experience;
- ML setup scoring;
- adaptive but controlled weighting;
- portfolio risk;
- automated research experiments;
- drift monitoring;
- strong explainability/auditability;
- reliable recovery/operations.

Even in the mature state:

- AI must not silently rewrite/deploy production strategies;
- Risk Manager remains independent;
- releases remain versioned and gated;
- no strategy gets unlimited risk because “AI confidence is high”.

---

# 34. Permanent prohibitions unless formally reconsidered

- Martingale / doubling after losses
- raising leverage to recover drawdown
- unlimited averaging-down without risk cap
- autonomous production code modification
- autonomous production strategy deployment
- bypassing risk because of news
- treating LLM confidence as trade authorization
- adding unlimited assets/strategies without validation
- deep learning/RL merely for marketing complexity

---

# 35. Security baseline

- never commit secrets;
- separate development/staging/live credentials;
- least privilege;
- withdrawal permission not required;
- IP restrictions when supported;
- audit credential/config changes;
- server DB/API ports not publicly exposed without architecture approval;
- backups and restore testing before live maturity;
- direct server deployment user is non-root.

---

# 36. Observability baseline

Eventually monitor:

- market data lag;
- order/exchange latency;
- API errors;
- strategy signal counts;
- NO_TRADE counts;
- risk rejection counts;
- exposure;
- PnL/drawdown;
- fundamental update age;
- safe-mode state;
- system/service health;
- deployed Git SHA.

Alert on critical data/exchange/risk/system failures.

---

# 37. Change control

Material changes use:

```text
Date:
Change ID:
Old Decision:
New Decision:
Reason:
Expected Benefit:
Risk:
Affected Modules:
Target Version:
Approved:
```

## Change Log

### 2026-09-09 — Initial plan

- Python Modular Monolith selected.
- PostgreSQL + Parquet selected.
- BTC/ETH, 15m entry with 1h/4h context selected for V1.
- Three initial strategy families selected.
- Hybrid fundamental architecture selected.
- Statistical/Bayesian Experience Engine selected for V1.
- ML/DL/RL/leverage excluded from V1.
- Replay/Backtest/Paper/Shadow are mandatory gates.
- Self-modification prohibited.

### 2026-09-09 — Deployment policy

- GitHub remains source of truth.
- GitHub Actions retained for CI/quality checks.
- Direct Codex deployment to a restricted Staging server is preferred for development speed.
- Direct autonomous production/live deployment remains prohibited during V1.

---

# 38. Next action

Start **Phase 1 — Project Foundation** through small GitHub Issues and PRs.

The first implementation issue must establish the minimum Python project baseline, typed configuration, testing/lint/type-check tooling, health endpoint, structured logging baseline and CI without introducing trading logic, exchange code, strategies, market-data collection or unnecessary infrastructure.

---

# Final project rule

> The system does not exist to trade frequently. It exists to trade only when market context, approved strategy evidence, historical experience, execution quality and risk controls jointly support the decision.

> If a defensible edge is not demonstrated, the correct production state is `NO_TRADE` / live disabled.
