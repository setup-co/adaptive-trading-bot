# BTC/ETH failed-breakout reversion — fixed 15m/30m screen

Protocol: `BTC_ETH_FAILED_BREAKOUT_REVERSION_15M_30M_V1`

Protocol SHA-256: `d9bfde677b59fed95dede0eadacc0d19de716a2bd8159458988d01b65827f424`

Strategy identity: `range_failed_breakout_reversion:0.1.0`

This protocol is a research-only exception within the mean-reversion family.
It does not claim `VALID_RANGE`, alter Range Mean Reversion 2.0.0, or activate
the strategy in runtime/Paper/Shadow/Live registries.

At each complete 15m close, the strategy excludes the signal candle and uses
exactly the preceding 16 consecutive complete 15m candles. Their high and low
seal the reference range and their midpoint seals the target. A signal must
open inside the reference, make exactly one strict boundary excursion, and
close back inside on the midpoint side specified by Issue #229. The structural
stop is the signal extreme plus a fixed adverse `0.10 * ATR14` buffer. The
BASE shared-pricing unit-quantity cost-room gate requires gross midpoint room
of at least three times modeled entry/exit adverse adjustments and commissions,
and positive modeled target net PnL.

Passing candidates traverse the existing Strategy → RiskManager →
OperationalSafety → DecisionJournalEntry → EntryOrderIntentAuthorizer chain.
Reference equity is 10,000 USDT under the unchanged default Risk/Safety
configuration. No portfolio compounding or leverage selection is introduced.

Execution uses the opt-in `FAILED_BREAKOUT_FROZEN_TARGET_30M_V1` policy in the
shared 5m LAST research adapter. Entry is the first eligible observed 5m OPEN.
Protection is modeled acknowledged with zero activation latency. Known OPEN
conditions precede intrabar checks; unordered dual touches are stop-first and
remain recorded. If still open, the position exits at the OPEN exactly 30
minutes after the actual modeled entry fill; later high/low values in that
deadline candle cannot override the timer. Filled invalid geometry is retained
and recovered/accounted rather than erased. This is bar-model evidence, not
tick-accurate or guaranteed venue execution.

Scenarios are frozen before results:

- BASE: full spread 2 bps, slippage 2 bps/fill, commission 8 bps/fill,
  entry latency zero.
- STRESS: full spread 4 bps, slippage 4 bps/fill, commission 16 bps/fill,
  entry latency one second (therefore 5m-grid quantized).

Funding and other unavailable perpetual mechanics remain `NOT_MODELED`.
Results are net of modeled commission/spread/slippage but before those missing
mechanics.

Only accepted BTCUSDT/ETHUSDT 5m shards from
`weex-history-screened-196d-last-continuation-v1` are in scope. Warm-up begins
2026-04-01 UTC. Signal opens cover `[2026-04-10, 2026-10-07)` UTC, with only
the necessary execution tail through 2026-10-07 00:40 UTC. Full independent
opportunities and the fixed 35-minute pre-outcome spacing mask are reported
for both scenarios. Neither is a shared-capital portfolio.

Classification follows Issue #229 exactly. Fewer than 30 closed spaced
observations in either asset/scenario is `INSUFFICIENT_EVIDENCE`; unresolved
exposure blocks a complete economic decision. Promotion to review requires
both assets and both scenarios to pass all predeclared full/spaced mean-R,
profit-factor, count and chronological-block gates. Any label remains research
evidence only, never strategy or trading approval.
