# Bollinger Method II price/MFI entry ablation

Status: the completed 15m and hourly 30-minute comparisons are retained below. A distinct paired
hourly no-time-exit diagnosis is implemented but has not yet been run on historical data.

## Source mapping and adaptation boundary

This screen implements the entry-selection proposition described by John Bollinger as Method II:
price action at the bands, optionally confirmed by MFI on its bounded 0–100 scale. It freezes the
book-version signal rather than mixing later platform defaults.

| Reference/version/page | Documented rule | Exact implemented rule | Project adaptation | Limitation |
|---|---|---|---|---|
| John Bollinger, *Bollinger on Bollinger Bands*, McGraw-Hill, ISBN 9780071373685, chapters 7/8 and 18/19, printed pp.155–159; [publisher identity/contents](https://www.mheducation.com/highered/mhp/product/bollinger-bollinger-bands); [consulted third-party transcription](https://studylib.net/doc/27097300/bollinger-on-bollinger-bands---john-bollinger) | Method II is trend following: price condition plus MFI confirmation; the book-version thresholds are BB20/2, `%b > 0.8` with MFI(10)>80 long and `%b < 0.2` with MFI(10)<20 short. | PRICE_CONTROL applies the strict `%b` condition; PRICE_PLUS_MFI adds the strict MFI(10) condition. | Shared project readiness/extreme-volatility, Risk and Safety guards remain mandatory. | The transcription is not publisher-hosted code; no book text is copied and this is not a TradeStation clone. |
| [Official 2021 Method II manual, pp.1–3](https://www.bollingerbands.com/_files/ugd/58be43_b120ddf0184540608baf19e2c0ae2019.pdf) | Confirms price-plus-MFI Method II and BB defaults, but its displayed defaults use MFI(14) and separately describe a 10-period `%MoneyFlow` normalization. | The fixed book-version MFI(10) is used; `%MoneyFlow` is not substituted. | None. | The 2021 default display is corroborating context, not the chosen MFI period authority. |
| [Official TradeStation methods page](https://www.bollingerbands.com/tradestation-methods), [official band rules](https://www.bollingerbands.com/bollinger-band-rules), and [authorized companion MFI description](https://bollingerbands.us/help-screening.php) | The companion description identifies MFI(10); the official pages identify Method II and general band conventions. | TP=`(H+L+C)/3`; raw flow=`TP * canonical base volume`; ten flows compare TP with the preceding TP; ties contribute to neither side; MFI=`100*positive/(positive+negative)`, with positive-only=100, negative-only=0 and both-zero unavailable. BB sigma is population sigma under the fixed Decimal context. | Calculation and missing-data conventions are deterministic for the accepted corpus. | These pages do not establish profitable WEEX performance or exact commercial-platform parity. |
| Issues #235 and #236 fixed experiments | The source discusses Parabolic/opposite-band exits rather than this project's short-horizon exit. | Same entry signal as above. | Frozen opposite-band stop, decision-time fixed 2R target, shared BASE/STRESS costs and existing 5m-LAST 30-minute exit. | These exits are OUR adaptation, not Bollinger's original exit system. |

This is **not** a reproduction of the book's exit system. Both arms instead use project
adaptations: the opposite frozen band is the structural stop; the target is sealed at the decision
close at fixed 2R; and the existing opt-in 5m-LAST protection profile applies a 30-minute
modeled deadline after fill. Shared Risk, Safety, authorization, pricing, costs, accounting,
recovery and journal authorities are unchanged. No runtime registry or activation boundary is
expanded.

## Hourly successor comparison (not historically executed)

- Protocol: `BTC_ETH_BB_METHOD2_MFI_ABLATION_1H_30M_V1`.
- Arms: `bb_price_control_1h:0.2.0` and `bb_mfi10_hourly:0.2.0`.
- Output schema: `bb-method2-mfi-hourly-ablation-result-v1`.
- Assets: BTCUSDT and ETHUSDT only; one decision at every completed 1h close.
- Indicators: BB(20, 2 population standard deviations) and MFI(10) consume completed hourly
  OHLCV candles aggregated from each hour's twelve accepted canonical 5m bars. They are not
  calculated from a 15m series sampled on the hour.
- Calendar: the unchanged 180-day signal window has 4,320 decision closes per asset and arm,
  from `2026-04-10T01:00:00Z` through `2026-10-07T00:00:00Z`, inclusive.
- Unchanged authorities: thresholds, Risk/Safety/authorization gates, BASE/STRESS costs,
  fixed-2R geometry, 5m execution and the 30-minute modeled holding deadline.

The new identity is research-only and does not replace or reinterpret the archived 15m result
below or broaden runtime eligibility.

### Hourly historical result

The single authorized hourly invocation used executable
`706c7818573c566e9a6a78e542887e7916c03612`, protocol
`e4948ecdc43e03b072cc62f8ab6cfbeb9dfb58f80221c2e7e7aa0a5c52431672` and run UUID
`63964675-0ab4-4b46-89c3-1b034b69dfbd`. It exited 0 after 40.1836267 seconds. The owned process
tree stayed below both bounds: no timeout, no memory termination and a peak combined working set
of 352,595,968 bytes.

| Asset | Arm | Observations | Signals | Risk rejected | Admitted |
|---|---|---:|---:|---:|---:|
| BTC | PRICE_CONTROL | 4,320 | 1,095 | 97 | 998 |
| BTC | PRICE_PLUS_MFI | 4,320 | 321 | 47 | 274 |
| ETH | PRICE_CONTROL | 4,320 | 1,042 | 52 | 990 |
| ETH | PRICE_PLUS_MFI | 4,320 | 324 | 10 | 314 |

MFI selected a less-negative subset in all asset/scenario comparisons, but every saved mean net R
remained negative and every profit factor remained below one. BASE mean R for price/MFI was
`-0.174348/-0.139973` BTC and `-0.134503/-0.078762` ETH; STRESS was
`-0.340859/-0.261586` BTC and `-0.268590/-0.168083` ETH. All 2,576 arm admissions produced both
scenario rows, filled and closed; unfilled, recovered and unresolved counts are zero.

Because hourly decisions are more than 35 minutes apart, the FULL and COMMON_SPACED views are
identical and are not independent confirmation. All three chronological block means are negative
for each asset, arm and scenario. The result is **`NEGATIVE_ADAPTATION / NO_PROMOTION`**. Full
economics, exit/holding facts, selection partitions, blocks and immutable hashes are retained in
`data/research_evidence/pr-236-bollinger-method2-mfi-1h-v1/`.

## Paired hourly no-time-exit diagnosis (not historically executed)

- Protocol: `BTC_ETH_BB_METHOD2_1H_NO_TIME_EXIT_PAIRED_V1`.
- Comparator: the immutable saved rows from the hourly 30-minute run above; that arm is never
  resimulated.
- Treatment: the exact same admitted decisions, entry fills, quantities, approved risk, frozen
  stops, decision-time targets, BASE/STRESS costs and shared authorization chain. The only change
  is opt-in `FROZEN_TARGET_NO_TIME_EXIT_V1`: follow each filled entry until stop, target or the
  fixed accepted-corpus end `2026-10-14T00:00:00Z`.
- Open treatment positions at the corpus endpoint remain `DATA_END_CENSORED`. Their last-close
  valuation and hypothetical shared-pricing exit fee are reported separately and do not become a
  realized close.
- Reports retain natural-close and terminal-valued views, parent TIME_EXIT transitions, paired
  terminal-minus-parent R, duration bins, chronological blocks, MFI retained/excluded partitions
  and peak concurrency. Funding remains unavailable/not modeled.

This diagnosis is post-result and exploratory. It neither replaces the negative parent result nor
approves the entry strategy, a timer replacement, runtime activation or trading.

## Archived 15m comparison

- Protocol: `BTC_ETH_BB_METHOD2_MFI_ABLATION_15M_30M_V1`.
- Arms: `bb_price_control:0.1.0` and `bb_mfi10_short_horizon:0.1.0`.
- Assets: BTCUSDT and ETHUSDT only; closed 15m decisions.
- Data/calendar: the accepted 196-day corpus and the same warm-up, 180-day signal calendar and
  5m follow-up tail used by Issue #233; exactly 17,280 signal observations per asset and arm.
- Costs: unchanged BASE and STRESS scenarios. Funding remains not modeled.
- Population: each arm has independent Strategy/Risk/Safety/authorization decisions. The
  confirmed signal set must be a subset of the price-control signal set. Common admitted decisions
  must have identical modeled economics.
- Views: FULL plus one pre-outcome `COMMON_SPACED` mask derived from control admissions with a
  minimum 35-minute separation and intersected with the confirmed admissions.

Selection effect is reported separately from profitability: control admissions retained by MFI,
control admissions excluded by MFI, both arms' full/masked economics, and the stored mean-R
differences. These are descriptive comparisons, not causal claims.

Any unresolved exposure is incomplete evidence. Fewer than 30 confirmed closed common-spaced
observations in any asset/scenario is insufficient. Review-only additionally requires positive
FULL and COMMON_SPACED mean net R and profit factor above one (or explicit positive-gain,
zero-loss unbounded PF) in both scenarios, plus positive BASE COMMON_SPACED mean in at least two
fixed blocks per asset. No result authorizes promotion or trading.

## Historical result

This result belongs only to the archived 15m protocol and identities immediately above. It is not
an hourly result.

The single authorized invocation used executable
`717691b7ce461f6b1ff716df381914969cf3bece`, protocol
`c0f84213ac0dc0b362b349c0b25f1a3fda971e5d5e194d0c7227d4d5c4f79051` and run UUID
`5c844e1a-6dbd-4e0a-969d-da5562c65dd7`. It exited 0 after 118.9113391 seconds, below the fixed
600-second ceiling. The owned-process-tree monitor observed two process identities, no timeout and
a peak combined working set of 414,556,160 bytes. Its separate non-market proof observed a spawned
child and exited 0.

Both arms evaluated exactly 17,280 decisions per asset. BTC price-control recorded 4,738 signals,
162 Risk rejections and 4,576 admissions; its MFI arm recorded 1,451 signals, 59 Risk rejections and
1,392 admissions. ETH recorded 4,723/392/4,331 and 1,483/97/1,386 respectively. Safety rejected
none. Thus MFI retained 30.42% of BTC and 32.00% of ETH control admissions. The control-derived
common masks contain 2,098 BTC and 2,043 ETH decisions; their intersections retain 539 and 532 MFI
admissions. Every common admission had identical arm economics.

| Asset | Costs | Arm | View | Closed | Mean net R | PF | W/L | Net quote | Fees quote |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| BTC | BASE | PRICE | FULL | 4,576 | -0.3641605077 | 0.0861306347 | 592/3,984 | -39,740.9072 | 28,869.6577 |
| BTC | BASE | MFI | FULL | 1,392 | -0.2647622170 | 0.1240598646 | 202/1,190 | -8,960.3933 | 6,692.0295 |
| BTC | BASE | PRICE | MASK | 2,098 | -0.3953490233 | 0.0779479673 | 255/1,843 | -19,630.5547 | 14,034.5486 |
| BTC | BASE | MFI | MASK | 539 | -0.2693689465 | 0.1411992185 | 86/453 | -3,492.8859 | 2,643.7790 |
| BTC | STRESS | PRICE | FULL | 4,576 | -0.7300149412 | 0.0161171446 | 183/4,393 | -79,560.9473 | 57,739.9831 |
| BTC | STRESS | MFI | FULL | 1,392 | -0.5391017432 | 0.0246075056 | 68/1,324 | -18,202.0293 | 13,384.1405 |
| BTC | STRESS | PRICE | MASK | 2,098 | -0.7898153766 | 0.0129329652 | 76/2,022 | -39,123.4005 | 28,069.1879 |
| BTC | STRESS | MFI | MASK | 539 | -0.5566540003 | 0.0244884380 | 26/513 | -7,225.4215 | 5,287.6385 |
| ETH | BASE | PRICE | FULL | 4,331 | -0.2961304673 | 0.1456453913 | 701/3,630 | -31,374.4466 | 22,694.5073 |
| ETH | BASE | MFI | FULL | 1,386 | -0.2038434551 | 0.1794270507 | 260/1,126 | -6,994.4199 | 5,131.7268 |
| ETH | BASE | PRICE | MASK | 2,043 | -0.3216425904 | 0.1421258260 | 315/1,728 | -16,020.2725 | 11,477.2745 |
| ETH | BASE | MFI | MASK | 532 | -0.2107846532 | 0.1907547966 | 100/432 | -2,772.7406 | 2,018.7583 |
| ETH | STRESS | PRICE | FULL | 4,331 | -0.5876427946 | 0.0393974270 | 307/4,024 | -62,282.5545 | 45,389.6144 |
| ETH | STRESS | MFI | FULL | 1,386 | -0.4111317963 | 0.0480447368 | 110/1,276 | -14,096.7040 | 10,263.1614 |
| ETH | STRESS | PRICE | MASK | 2,043 | -0.6298745278 | 0.0388972302 | 143/1,900 | -31,396.4826 | 22,954.8160 |
| ETH | STRESS | MFI | MASK | 532 | -0.4260392621 | 0.0464059719 | 45/487 | -5,597.1260 | 4,037.2153 |

MFI improved the FULL mean net R relative to price control by `+0.0993982907` BTC BASE,
`+0.1909131980` BTC STRESS, `+0.0922870122` ETH BASE and `+0.1765109983` ETH STRESS. Within the
control population, MFI-retained decisions also outperformed excluded decisions by
`+0.1428538248`, `+0.2743777619`, `+0.1357198811` and `+0.2595820488` mean net R respectively.
This supports only the descriptive conclusion that MFI selected a less-bad subset. Every FULL and
masked mean remained negative, every profit factor remained far below one, and no exposure was
unresolved. Classification is **`NEGATIVE_ADAPTATION / NO_PROMOTION`**. It is not a profitable
strategy, a causal claim about MFI, or trading authority.

### Prospective classifier-contract correction

The original run outputs above remain immutable. Review found that executable
`717691b7ce461f6b1ff716df381914969cf3bece` compared the inherited PF status with an unreachable
`FINITE` label and required all BASE/STRESS blocks positive. Future classification now accepts the
established `NUMERIC` PF above one or `UNBOUNDED_ZERO_LOSS`, and requires at least two positive BASE
COMMON_SPACED blocks per asset, exactly as preregistered. The observed run's eight confirmed FULL
and COMMON_SPACED means are all negative, so its `NEGATIVE_ADAPTATION / NO_PROMOTION` result does
not depend on either defective positive-candidate gate. No simulation, outcome or archived evidence
was rewritten.

Compact immutable evidence is retained under
`data/research_evidence/issue-235-bollinger-method2-mfi-v1/`. The large trade-row authority is
stored as a deterministic gzip copy while the original remains preserved externally.
