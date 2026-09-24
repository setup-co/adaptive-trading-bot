# Range Mean Reversion V1 midpoint screen — implementation freeze

Issue #151 predeclares the unleveraged, pre-funding signal-economic screen. This
document does **not** report BTC/ETH results and does not authorize a study run.
An architect must review and merge this implementation, freeze the resulting
code SHA, and separately activate a one-time A/B/C execution.

## Frozen entry and historical authority

- `RangeMeanReversionStrategy` `range_mean_reversion` version `1.0.0`, with its
  unchanged default `RangeMeanReversionConfiguration` and stateful default
  `ValidatedRangeDetector` / `ValidatedRangeConfiguration`.
- Decision path: Replay → Features → Structure → Technical Regime → MTF Regime →
  stateful Strategy → Risk → Safety → sealed Decision Journal. All 15m decisions,
  including `NO_TRADE` and blocked candidates, remain in the corpus. The corpus
  replays the same default strategy statefully to validate its sealed decisions.
- Historical source `weex-v3-last-e5b0c4035a19d5a80ca7`, dataset
  `weex-v1-5m-last-a629a29b580b2e103adba918`, manifest SHA-256
  `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
  Qualified WEEX history requires `LockedWeexResearchCorpusStore`; no API or
  generic Parquet fallback is allowed. The excluded ETH gap is never bridged.
  The exact locked-manifest warm-up exclusions are versioned into research
  configuration: their decisions stay in the population, but their candidates
  are not eligible for economic replay. No later range state is used to repair
  or shorten the warm-up.

## Only exit hypothesis

Policy `RANGE_MEAN_REVERSION_ENTRY_MIDPOINT_V1` freezes the target at the
**exact midpoint in the entry decision's sealed strategy rule audit**. The
strategy's invalidation reference is the protective-stop basis. Neither moves
with later range state. After the actual simulator entry fill, LONG requires
`stop < fill < midpoint`; SHORT requires `midpoint < fill < stop`. Failed strict
inequalities become `ENTRY_FILL_INVALIDATED`, with ordered stop and/or midpoint
reasons, before any position or PnL is created.

On completed 15m candles, LONG stop/target touches are `low <= stop` and
`high >= midpoint`; SHORT touches are `high >= stop` and `low <= midpoint`.
The protective stop wins a same-bar dual touch. A touch creates one market
exit request at that candle's close. The existing `BacktestExecutionSimulator`
fills only at a causally eligible later 15m open under the existing cost/latency
model; the `PositionAccountingEngine` owns all PnL and fee arithmetic. A
trigger price is never treated as a guaranteed fill. Unfilled or untriggered
positions remain open/unresolved; there is no horizon force-close.

## Frozen future units and economics

| Unit | Symbol | Decision window `[start,end)` | Follow-up boundary |
| --- | --- | --- | --- |
| A | BTC/USDT | 2024-07-01 → 2026-01-01 UTC | 2026-01-08 UTC |
| B | ETH/USDT | 2024-07-01 → 2024-12-25 UTC | 2025-01-01 UTC |
| C | ETH/USDT | 2025-04-02 → 2026-01-01 UTC | 2026-01-08 UTC |

Each independent unit uses fixed research equity `10000 USDT`, a 168-hour
follow-up, 15m execution, default simulator latency, 2 bps spread, 2 bps
slippage, and 10 bps fee. It starts fresh strategy/detector/Risk/Safety/replay/
accounting state. Closed outcomes use the existing sealed accounting result
and `net_R = realized_net_pnl / authorized approved_risk_quote`, not a risk
recalculation. Population, invalidation reasons, exit causes, exact Decimal
economics, side/month strata, entry-fill target/stop geometry, and overlap
components remain descriptive. Raw candidates are not independent; total R
is not portfolio PnL or evidence of statistical significance.

A unit is evaluable only with at least 10 closed outcomes, at least 80% of
replayed entries closed, valid sealed integrity, and no synthetic outcomes.
`POSITIVE` requires mean R > 0, profit factor > 1, and median R >= 0.
`NEGATIVE` requires mean R <= 0 and profit factor <= 1. Other evaluable runs
are `MIXED`; unevaluable runs are `INSUFFICIENT`. The three-unit screen is
`POSITIVE` when all are evaluable, at least two are positive and none negative;
`NEGATIVE` when all are evaluable and at least two are negative; `MIXED` for
other fully evaluable combinations; otherwise `INSUFFICIENT`.

This screen has no leverage, historical funding, MARK/MMR/liquidation,
portfolio, Paper, Shadow, Live, or future-profitability authority. The prior
Trend Pullback and Breakout + Retest negative evidence remains locked.
