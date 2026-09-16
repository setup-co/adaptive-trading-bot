# Adaptive Trading Bot — Master Plan

**Status:** Project source of truth  
**Document version:** 1.2
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

- Leveraged WEEX perpetual futures are the primary V1 trading mode; Spot is secondary.
- Leverage is permitted only as an explicit, bounded, versioned and predeclared execution/margin parameter controlled by the Risk Manager.
- No fixed or “optimal” leverage is selected by this plan. Leverage must not be selected or increased by a strategy, Router, Experience Engine, ML model, LLM, or post-result optimization.
- Risk budget remains independent of leverage: position size follows approved risk and stop geometry, while leverage determines margin usage within the approved exposure.
- Isolated margin is preferred for controlled V1 live readiness where WEEX supports it. Cross margin is not an implicit fallback and requires a separate explicit architecture decision.
- Live remains disabled by default until the complete validation ladder and the leveraged-realism gates in this plan pass.

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

## V1 venue policy

- WEEX is the primary V1 venue.
- Futures/Perpetual is the primary V1 trading mode; Spot is secondary.
- Research and signal data should come from authoritative WEEX public endpoints when WEEX supplies the required series.
- Future execution, account state, positions, order lifecycle, and funding/liquidation-sensitive controls will use WEEX.
- Other exchanges must not become implicit dependencies of the primary V1 signal path merely to simplify backfill.
- Strategy, Risk, Safety, Feature, Replay, and accounting contracts remain exchange-neutral. WEEX-specific transport and mapping belong only in exchange/source adapters.
- Cross-exchange data is deferred to a separately approved validation or redundancy feature; it is not part of the V1 authority chain.

Using one primary data and execution venue reduces venue-basis, candle/timestamp, mark/index/funding, and operational mismatches. This policy permits bounded Risk-owned leverage as part of the V1 architecture; it does not approve private API integration or Live trading. A public endpoint must still pass its own qualification and integrity gates before it becomes an accepted data source.

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

For representative leveraged perpetual validation, the authoritative market/account/execution path must also provide or deterministically model, as applicable:

- contract and notional semantics;
- configured leverage and effective notional;
- initial and maintenance margin;
- margin mode;
- mark price and venue liquidation semantics;
- liquidation price and distance;
- funding rates, timestamps and payments;
- fees, spread, slippage and stop-execution assumptions;
- execution latency;
- position and account survival state.

Missing, stale, unknown or inconsistent margin, mark-price, funding or liquidation facts must fail closed wherever they can materially affect a result or admission decision.

Optional derivatives intelligence may be stored when reliable data is available:

- funding rate
- open interest
- liquidations

Funding required for cost/accounting realism is mandatory for representative leveraged validation; funding used as sentiment is optional strategy context. Open interest and liquidation-flow signals are also optional context and do not replace the mandatory execution, margin and liquidation mechanics above.

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

- risk per trade around 0.25% of test account;
- simultaneous risk around 0.5%;
- daily loss limit around 1%;
- larger drawdown threshold triggers halt/review.

No default “optimal leverage” is declared. Any leverage configuration must be bounded, versioned and predeclared before research or validation.

Responsibilities:

- position sizing;
- stop validation;
- take-profit/exit validation;
- minimum expected reward/risk policy where used;
- daily loss control;
- drawdown control;
- simultaneous/correlated exposure;
- leverage and effective-notional caps;
- initial/maintenance-margin and margin-usage limits;
- margin-mode eligibility;
- liquidation-price/distance validation;
- funding-cost and funding-state validation;
- cooldown logic;
- spread/slippage tolerance;
- data/exchange health gates.

Position size follows risk budget and stop distance; risk budget does not follow desired position size.

Leverage is not a risk budget and must never increase approved risk. The Risk Manager is sovereign over leverage, effective notional, size, margin usage, liquidation-distance requirements, simultaneous exposure/risk, and account/drawdown limits. A new position must be rejected unless its liquidation boundary is safely beyond the approved protective stop plus a conservative, versioned safety buffer. The buffer is not numerically selected by this architecture decision and must be established through a separately approved implementation/validation issue.

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
- suspicious price anomaly;
- unavailable or inconsistent leverage/margin mode;
- unavailable, stale or inconsistent mark price, maintenance margin or liquidation facts;
- liquidation distance that does not clear the approved stop plus the configured safety buffer;
- funding state unavailable where it materially affects admission or representative validation;
- position/account survival state that cannot be reconciled.

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
- leverage configuration/effective notional
- initial and maintenance margin
- margin mode and margin usage
- mark price and mark source
- liquidation price/distance and safety-buffer policy version
- funding rate/timestamp and estimated funding
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
- realized funding
- realized margin/liquidation/account-survival facts
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
- configured leverage and effective notional;
- initial and maintenance margin;
- isolated-margin behavior (and cross margin only after a separate explicit approval);
- mark-price and venue liquidation behavior;
- liquidation distance relative to protective stop and the versioned safety buffer;
- funding accrual/payment timing;
- stop execution assumptions;
- execution latency assumptions where meaningful;
- partial fills if/when strategy requires them.

Representative leveraged results must include fees, spread, slippage, funding, latency, stop behavior, margin usage and liquidation/account-survival effects. An unleveraged artifact remains useful research evidence but is not sufficient proof of leveraged Live readiness. Signal quality must be reported separately from leverage/margin effects, and leverage must not be tuned together with strategy thresholds after observing results.

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
- leverage/effective-notional and margin state;
- mark-price, funding and liquidation-distance state;
- protective-stop/liquidation-buffer admission outcomes;
- position/account survival transitions.

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

Validate real data feeds, state transitions, order lifecycle simulation, failure handling and journaling. For the primary perpetual mode, Paper must exercise versioned leverage, effective notional, isolated-margin assumptions, mark price, funding, liquidation distance and account-survival behavior without sending orders.

## Shadow Trading

Produce genuine live-market decisions but do not send real orders. Compare hypothetical execution/outcomes to the live market, including observable WEEX perpetual funding/mark/liquidation and margin-state assumptions. Shadow must remain non-executable.

---

# 22. V1 acceptance gate

Real-money live execution remains disabled unless all critical gates pass:

- data integrity;
- backtest reproducibility;
- no known look-ahead/data leakage;
- risk manager coverage;
- bounded/versioned leverage and effective-notional enforcement;
- representative initial/maintenance-margin and preferred isolated-margin validation;
- funding, mark-price and venue-liquidation realism;
- protective-stop/liquidation-buffer gate coverage;
- position/account survival and reconciliation coverage;
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
- perpetual contract, mark price and funding state
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
- approved leverage/effective notional, margin mode/usage and liquidation-distance gate explanation

## Trades
- complete journal
- funding, margin, mark/liquidation and account-survival facts where applicable

## Experience
- performance by strategy/regime/context

## System
- data freshness
- exchange health
- last news update
- errors
- safe mode
- active workers/services
- leverage/margin configuration version and perpetual-mechanics data health

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
- leverage/effective-notional and margin checks;
- mark-price, funding and liquidation facts used;
- protective-stop/liquidation-buffer result;
- final decision.

Version:

- strategies;
- configurations;
- leverage, margin, funding and liquidation-safety policies;
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
- perpetual funding, margin, mark-price/liquidation and account-survival realism
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
- bounded/versioned leverage and effective-notional caps
- initial/maintenance-margin and margin-usage controls
- protective-stop/liquidation-distance safety gate
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
- non-executable perpetual leverage/margin/funding/mark/liquidation simulation

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
- leverage-independent signal reporting and predeclared perpetual-mechanics assumptions

## Phase 19 — Shadow Trading

- live decision generation
- no real orders
- reconciliation of hypothetical outcomes
- live-observed perpetual mark/funding/liquidation inputs without order submission

## Phase 20 — V1 Acceptance Review

Live remains disabled until explicitly approved after all required gates, including representative leveraged perpetual margin, funding, mark-price, liquidation, protective-stop-buffer and account-survival validation.

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

This maturity item concerns optional derivatives intelligence used as alpha/context, such as open-interest or liquidation-flow signals. It does not defer the perpetual execution mechanics already required by V1: leverage/notional, margin, mark price, venue liquidation, funding costs and account survival are core Risk/execution/validation facts whenever they can materially affect outcomes.

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
- strategy-, Router-, Experience-, ML- or LLM-selected leverage
- post-result leverage optimization or joint tuning of leverage with strategy thresholds
- treating leverage as permission to exceed the approved risk budget
- implicit cross-margin use or fallback without a separate explicit architecture decision
- admitting a position when liquidation is not safely beyond the approved stop plus its versioned safety buffer
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
- configured leverage and effective notional;
- initial/maintenance margin, margin mode and margin usage;
- mark price, funding and liquidation distance;
- protective-stop/liquidation-buffer headroom;
- position/account survival and reconciliation state;
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

### 2026-09-14 — WEEX-first V1 venue policy

- WEEX selected as the single primary V1 venue, with Futures/Perpetual primary and Spot secondary.
- Authoritative WEEX data is preferred for research and signals to reduce research/execution venue mismatch.
- WEEX-specific behavior remains confined to adapters; Strategy, Risk, Safety, Feature, Replay, and accounting remain exchange-neutral.
- Other exchanges are excluded from the primary V1 path. Any future cross-exchange validation or redundancy requires a separate explicit decision.
- Venue selection does not waive public-data qualification, historical integrity, or staged Live-readiness gates.

### 2026-09-17 — Leveraged WEEX perpetual futures as primary V1 mode

- Date: 2026-09-17
- Change ID: Issue #123 / Master Plan v1.2
- Old Decision: V1 selected WEEX Futures/Perpetual as its primary venue/mode while separately declaring leverage OFF.
- New Decision: Leveraged WEEX perpetual futures are the primary V1 trading mode. Leverage is permitted only as a bounded, versioned, predeclared Risk Manager parameter; isolated margin is preferred for controlled V1 live readiness, and representative margin/funding/mark-price/liquidation/account-survival validation is mandatory. Live remains disabled until all gates pass.
- Reason: Remove the internal architecture contradiction and define the safety authority and realism required for the already-selected perpetual venue path.
- Expected Benefit: A coherent, auditable V1 target whose research, validation and eventual execution assumptions match the intended venue and instrument.
- Risk: Leverage magnifies execution, liquidation and account-survival failures; incomplete or stale derivatives facts could make research or admission unsafe. These conditions therefore fail closed under Risk/Safety controls.
- Affected Modules: Master Plan, agent governance, future Market Data, Risk, Safety, Journal, Backtest, Replay, Paper, Shadow, Dashboard and observability work. No runtime module changes in this decision.
- Target Version: V1 / Master Plan v1.2
- Approved: Architect-approved Issue #123; implementation remains subject to subsequent scoped issues and the full validation ladder.

---

# 38. Next action

Start **Phase 1 — Project Foundation** through small GitHub Issues and PRs.

The first implementation issue must establish the minimum Python project baseline, typed configuration, testing/lint/type-check tooling, health endpoint, structured logging baseline and CI without introducing trading logic, exchange code, strategies, market-data collection or unnecessary infrastructure.

---

# Final project rule

> The system does not exist to trade frequently. It exists to trade only when market context, approved strategy evidence, historical experience, execution quality and risk controls jointly support the decision.

> If a defensible edge is not demonstrated, the correct production state is `NO_TRADE` / live disabled.
