# BTC/ETH impulse/pullback screen

Status: fixed research protocol; historical result is added only after the one authorized run.

## Identity and architecture boundary

- Protocol: `BTC_ETH_IMPULSE_PULLBACK_15M_30M_V1`
- Strategy: `trend_impulse_pullback:0.1.0`
- Policy: `THREE_BAR_IMPULSE_PULLBACK_RESUMPTION_V1`
- Entry decisions: closed 15m candles, BTCUSDT and ETHUSDT only.
- Execution: existing opt-in 5m LAST protective profile with the supplied frozen target and a
  30-minute deadline from the actual modeled fill.

This is the narrow research-only Trend-context exception accepted in Issue #233. It does not
claim approval from the existing HTF/EMA trend detector, change `trend_pullback:1.0.0`, register a
runtime strategy, or authorize Paper, Shadow, Live, account access, or orders. Candidate decisions
continue through the shared RiskManager, OperationalSafety, validated DecisionJournalEntry and
EntryOrderIntentAuthorizer. The existing execution pricing, position accounting, filled-entry
recovery and original-approved-risk R authorities remain unchanged.

## Frozen rule

For each closed observation, I/P/S are the immediately preceding impulse and pullback candles and
the current resumption candle. `A0` is ATR14 at the complete candle immediately before I; `AS` is
ATR14 at S. I must have body at least `1.50*A0` and at least `0.70` of its range. P is exactly one
counter-directional/doji candle contained in the impulse-body upper/lower half specified by the
issue. S must close strictly through P's high/low while preserving that half-body boundary.

The stop is the P/S extreme plus the fixed adverse `0.10*AS` buffer. Reference `E` is S close and
target `T` is sealed at decision time at fixed 2R from E; it is never recomputed from the eventual
fill. The BASE unit feasibility filter requires gross target distance at least three times both
modeled adverse adjustments and commissions, with positive modeled net target PnL. Equality at
the declared impulse thresholds is eligible; equality at the resumption confirmation boundary is
not.

The rule uses bounded causal history, resets on a gap, rejects duplicate/backward observations,
and requires warmed I/P/S contexts, ready MTF context and no existing extreme-volatility flag on
I/P/S. It adds no 1h/4h agreement, EMA, RSI, volume, session, news, side or asset filter.

## Fixed data, costs, calendar and reporting

The accepted `weex-history-screened-196d-last-continuation-v1` corpus is bound by manifest
`756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d`, report
`bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66`, plan
`dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f`, BTC shard
`8b0f88bb0bcb421c54f5c160c29dae9718802c29ae6d6059ce7bf4fa7ea07a78` and ETH shard
`41deb1ba9c0b473704316b69f51444c6c22c7ae4712038234cf422f74a5fc23d`.

Warm-up starts 2026-04-01 UTC. Signal opens are `[2026-04-10, 2026-10-07)`, exactly 17,280
decision slots per member, with the existing tail only through 00:40. BASE costs are 2/2/8 bps
spread/slippage/commission with zero configured latency. STRESS is 4/4/16 bps with one-second
configured latency and existing 5m-grid quantization. Funding and other unqualified perpetual
mechanics remain not modeled.

FULL contains every independently authorized opportunity. SPACED selects the first admitted
decision and then the first at least 35 minutes after the prior selected decision, before outcomes,
identically for BASE and STRESS. Three fixed 60-day blocks use S open. Metrics and classification
follow Issue #233, including explicit zero-loss positive-profit `UNBOUNDED_ZERO_LOSS` rather than
JSON infinity. This is selection-informed exploratory evidence, not a portfolio return or untouched
holdout.

## Interpretation boundary

Any unresolved exposure is incomplete evidence. Fewer than 30 closed SPACED observations in any
asset/scenario is insufficient. Review-only requires positive FULL and SPACED means and profit
factor above one (or the explicit unbounded case) for both assets and scenarios, plus positive BASE
SPACED mean R in at least two blocks per asset. Adequate complete samples with all eight means
nonpositive are NEGATIVE; other nonqualifying patterns are MIXED_FRAGILE. No result is automatic
promotion or trading permission.

## Historical execution result

Pending the single clean-SHA, externally bounded historical invocation authorized by Issue #233.
