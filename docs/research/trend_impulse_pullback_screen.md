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

The single authorized invocation used executable
`892a75922aef8ba62dca7c9cd3e902bbb7b98669`, protocol
`c6431087b0bac2b673012eb68375fc7b5a44ca5053bfabc6fba175fa28619ebe` and run UUID
`9d4a3e9b-81f3-471c-b12e-c84e4b70600b`. The historical child exited 0 in 48.401 seconds;
the wrapper recorded no timeout/resource stop and a 4,452,352-byte observed peak working set. A
PowerShell wrapper syntax error occurred before the child/owner/output existed; its separate record
shows zero historical invocations and it did not consume the one historical grant.

Both members completed all 17,280 decision slots. BTC recorded 8 raw setups: 7 cost-room
rejections and 1 admission. ETH recorded 5 raw setups: 2 cost-room rejections and 3 admissions.
Neither member had a Risk or Safety rejection after a strategy candidate passed the cost-room gate.
All four admissions filled and closed by the 30-minute TIME_EXIT; none recovered or remained
unresolved. FULL and SPACED are identical because every admitted decision survived the 35-minute
pre-outcome spacing mask.

| Asset | Costs | View | Closed | Mean net R | Median net R | PF | W/L/0 | Gross quote | Fees quote | Net quote |
|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|
| BTCUSDT | BASE | FULL/SPACED | 1 | 0.1290160021037099234384648973914967 | 0.1290160021037099234384648973914967 | UNBOUNDED_ZERO_LOSS | 1/0/0 | 9.656326002638214949314602382104323 | 6.430925950045466863352979947316905 | 3.225400052592748085961622434787418 |
| BTCUSDT | STRESS | FULL/SPACED | 1 | -0.2808868023805700058793744855946254 | -0.2808868023805700058793744855946254 | 0 | 0/1/0 | 5.840030112306600504770223175496426 | 12.86220017182085065175458531536206 | -7.022170059514250146984362139865634 |
| ETHUSDT | BASE | FULL/SPACED | 3 | -0.04851524657325527264656691344905577 | 0.02832369979510997072829902278190912 | 0.3969211682931076785639972846863308 | 2/1/0 | 18.40359592756569428348114655567414 | 22.04223942055983973197366506435332 | -3.638643492994145448492518508679181 |
| ETHUSDT | STRESS | FULL/SPACED | 3 | -0.4578307451640533295197698464574183 | -0.4278830569729130196601186418975004 | 0 | 0/3/0 | 9.737168276524324184409549836522891 | 44.07447416382832389839228832082927 | -34.33730588730399971398273848430638 |

The BTC admission is in BLOCK_2. ETH has two admissions in BLOCK_2 and one in BLOCK_3; BLOCK_1
is empty. BTC BASE BLOCK_2 mean is `0.1290160021037099234384648973914967`, while BTC STRESS
is `-0.2808868023805700058793744855946254`. ETH BASE means are
`-0.0869347197574378943339998815645382` in BLOCK_2 and
`0.02832369979510997072829902278190912` in BLOCK_3; ETH STRESS means are negative in both.
The two positive ETH BASE observations comprise all positive R, so the mean excluding the top
three positives is the one losing observation, `-0.2413378352342895539979486815471216`.
BTC's sole positive BASE observation likewise has no residual denominator after excluding the top
three.

Classification is **`INSUFFICIENT_EVIDENCE / NO_PROMOTION`** because each asset/scenario has only
1 or 3 closed SPACED observations, far below the fixed minimum of 30. The mixed cost sensitivity
is descriptive only: BTC is positive under BASE but negative under STRESS; ETH's mean is negative
under both. This is not an economic success, strategy approval, portfolio return, or trading
authority.

Immutable compact evidence is retained in
`data/research_evidence/issue-233-impulse-pullback-v1/`. The projection SHA-256 is
`24b5cf395b173ae3bd29a91889fc7e171c777f390dc90bb70b3ab76af4dd5977`; the trade-row SHA-256
is `12acc7740bc6283716dc9492c672479d33a2334b741ac11c06f5d93223c2b2ab`. The publication
manifest binds the remaining command, environment, resource, member and completion records.
