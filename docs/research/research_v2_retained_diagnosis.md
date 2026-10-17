# Research V2 retained diagnosis

**Protocol:** `RESEARCH_V2_RETAINED_DIAGNOSIS_V1`

**Status:** post-result, descriptive, hypothesis-generating

**Historical executable:** `316744efa9c4b50aa8d999b3c4d11fd874b4c28c`
**Historical run UUID:** `686bd4a1-ffff-4391-8562-f1e4caf7a575`

This diagnosis reads only the immutable Issue #187 evidence. It did not run a
feature, indicator, strategy, Risk/Safety admission, simulator, accounting
engine, or counterfactual. It is not independent replication, a new economic
screen, threshold selection, or trading authority. The frozen classifications
remain Trend Pullback `NEGATIVE_EVIDENCE`, Breakout + Retest
`INSUFFICIENT_EVIDENCE`, and Range Mean Reversion `INSUFFICIENT_EVIDENCE`.

## Input and reconciliation

The extractor verified the accepted artifact index
`677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`
and consumed 91 indexed files (the final projection plus all 30 population,
partition, and authority triples), totaling 625,679,711 bytes. It reconciled
30 tasks, 181,440 decisions, 362,880 scenario rows, 246 admitted decisions,
and 492 admitted scenario rows. Every required file matched its indexed size
and SHA-256; task, decision, scenario, partition, and result identities were
complete and unique.

The final corrected compact outputs are under
`data/research_evidence/issue-189-retained-diagnosis-v3/`. The v1 and v2
generations remain immutable and superseded. v2 fixed identity joins and most
Decimal arithmetic; v3 closes the remaining quantity-context boundary and
records explicit filled/open/recovery/hold-only synthetic coverage.
`summary.json`
retains family/asset/P1–P3/side/regime groupings with explicit zero terminal
cells. `trades.csv` contains the 492 scenario-level admitted records, and
`pairs.csv` contains the 246 BASE/STRESS pairs bound by the actual decision,
task, source, symbol, family, strategy version, accepted input binding and
entry authority. `hold-only.json` preserves the distinct hold-only branch
diagnostic and `reconciliation.json` records the correction lineage. The
utility source
SHA-256 is
`0c815e4a6ce913b0aaf006022e0d23540fc9c5bbbd160b1c38c5aa223f9d6a74`;
it is distinct from the historical executable and UUID.

Quantity handling has two documented meanings. Diagnostic output uses
context-free `Decimal.copy_abs()` and therefore preserves the stored primitive
exactly. Compatibility checks against the already serialized #187 compact row
use an explicit 28-digit `ROUND_HALF_EVEN` context, reproducing the historical
row producer without inheriting the caller's ambient context. This is separate
from the accepted aggregate projection's 34-digit context and the diagnosis's
50-digit descriptive arithmetic.

The detailed sufficiency/dependence strata remain authoritative only in the
accepted #187 `projection.json` under each `families.<family>.details` object.
The diagnostic grouping tables are descriptive retained-row projections; they
do not replace those accepted detailed-strata records.

## A. Recorded opportunity and admission funnel

BASE and STRESS share one population and are not double-counted:

| Family | Observations | NO_SIGNAL | Candidates | Risk blocked | Safety blocked | Admitted / filled / closed | Unresolved |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend Pullback | 60,480 | 60,251 | 229 | 12 | 0 | 217 / 217 / 217 | 0 |
| Breakout + Retest | 60,480 | 60,451 | 29 | 1 | 0 | 28 / 28 / 28 | 0 |
| Range Mean Reversion | 60,480 | 60,479 | 1 | 0 | 0 | 1 / 1 / 1 | 0 |

Range scarcity occurred mostly before entry rules. Of 60,480 observations,
2,684 were `INSUFFICIENT_DATA`, 57,690 `NOT_RANGE`, 27 `RANGE_BROKEN`, and
79 `VALID_RANGE`. Detector reasons overlap: `ENTRY_TREND_TOO_STRONG` appeared
35,678 times, `EXTREME_VOLATILITY` 21,425, and `HTF_DIRECTIONAL` 18,329.
Exact combinations and all other counts are retained in `summary.json`.

Within the 79 valid-range observations, 49 recorded
`PRICE_NOT_AT_RANGE_EDGE`. Thirty selected a side (15 LONG and 15 SHORT), but
entry checks could carry multiple failures: RSI 23, candle reversal 17,
momentum 8, boundary excursion 6, range rejection 3, midpoint already crossed
2, and range reclaim 1. Only one LONG candidate was recorded: ZECUSDT, P3,
neutral regime. The other nine assets and P1/P2 have explicit zero candidate
cells. Short-circuited later rules are unavailable as pass/fail facts; these
counts cannot estimate trades under relaxed rules.

Partial evidence was marked retained on 25,784 observations and reset on
6,160. These are bar observations, not independent ranges. The stored data do
not identify a count of independent discovered ranges.

## B. Recorded economics and exits

All ratios use stored exact Decimals and sealed approved risk/notional. They
do not recompute fills, accounting, or portfolio performance.

| Family / scenario | Closed | Exit reasons | Median fees / risk | Median fees / notional | Median net R |
| --- | ---: | --- | ---: | ---: | ---: |
| Trend BASE | 217 | 117 EMA50 thesis, 100 stop | 0.31700050 | 0.00199962 | -1.01116355 |
| Trend STRESS | 217 | 115 EMA50 thesis, 99 stop, 3 recovery | 0.63375655 | 0.00400140 | -1.29349544 |
| Breakout BASE | 28 | 13 fixed-2R target, 15 stop | 0.39266314 | 0.00200328 | -0.39811670 |
| Breakout STRESS | 28 | 8 fixed-2R target, 18 stop, 2 recovery | 0.78483414 | 0.00400465 | -1.72615236 |
| Range BASE | 1 | 1 midpoint target | 0.40472229 | 0.00200252 | -0.01710028 |
| Range STRESS | 1 | 1 recovery | 0.81136012 | 0.00401451 | -0.60592438 |

The three Trend and two Breakout STRESS recoveries recorded
`STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL`; Range STRESS recorded
`MIDPOINT_NOT_BEYOND_ACTUAL_ENTRY_FILL`. They are accounted post-fill closes,
not discarded entries. Nominal reference-to-stop ratios, actual fill
prices/times, fees, gross/net PnL, approved-risk R, recovery facts, and
remaining quantities are preserved per row in `trades.csv`.

The separate Trend hold-only diagnostic has 217 branches, 212 closed and five
unresolved in each scenario. The exact unresolved quantities are retained per
asset, not summed across unlike base units: BTCUSDT
`0.10947500751783555945913955292`, XRPUSDT
`17755.377646621171718940007315`, and AVAXUSDT
`221.2389380530973451327433628`; all other assets are zero. These are
hold-only residuals. Primary branches remain fully closed with zero residual
quantity. Closed-only hold comparisons therefore have a different denominator
and do not prove EMA50 improvement. No residual mark was invented.

## C. Paired BASE versus STRESS

Stress changed spread, slippage, fees **and** latency together. Every admitted
BASE entry and exit had a recorded zero-second wait. The one-second STRESS
eligibility shift selected the next 15m open, so every recorded STRESS entry
and exit wait was 900 seconds and all 246 entry and exit opens changed. This
is simulator bar-selection evidence, not observed exchange latency.

| Family | Pairs | Status changes | Delta gross | Delta fees | Delta net |
| --- | ---: | ---: | ---: | ---: | ---: |
| Trend | 217 | 3 | -513.33373191300897123526323454654252734 | +1941.0390549348025511837261579941533189 | -2454.372786847811522418989392540696009 |
| Breakout | 28 | 2 | -559.445107905591308686381819202020413 | +338.353156581183043238370545537955600 | -897.798264486774351924752364739976156 |
| Range | 1 | 1 | -4.554656905068592828835143538791570 | +10.16594559498699371052863064487423 | -14.720602500055586539363774183665803 |

These row-derived values use an explicit 50-digit `ROUND_HALF_EVEN` local
context. The accepted #187 projection used its own 34-digit context. The two
views reconcile within stored-scale rounding residuals, recorded explicitly
in `summary.json`; the diagnosis does **not** claim unchecked exact equality
or independent replication. Likewise, each view's displayed
`delta net = delta gross - delta fees` bridge retains a small Decimal rounding
residual rather than being asserted as algebraically exact.
Breakout shifted from 13 targets/15 stops to 8 targets/18 stops plus two
recoveries while both later opens and costs changed. This evidence cannot
separate latency, spread/slippage/fee, market-path, or interaction effects.

## Bounded interpretation

At most three hypotheses follow for future architect review:

1. Range scarcity is associated mainly with detector/regime validity before
   entry checks, but overlapping reasons, repeated bars, and short circuits
   prevent attributing scarcity to one removable rule.
2. Narrow stop geometry can make fees large relative to approved risk even
   when fee/notional ratios approximate configured round-trip rates; this does
   not identify a better threshold.
3. Breakout appears sensitive to the combined next-open/cost scenario, but
   this experiment cannot isolate which component produced the difference.

**Recommended next decision:** shelve all three V1 hypotheses on these windows
unless the architect first approves a fresh, independently predeclared design
that separates execution-cost and bar-latency factors. No tuning or rerun is
recommended from this post-result diagnosis.

## Reproduction and limits

```console
python -m scripts.research.research_v2_retained_diagnosis --repo . --output data/research_evidence/issue-189-retained-diagnosis-v3
```

The corrected manifest SHA-256 is
`dd7da381670944844a46e46ee23bcb540707f7e466120c7dedcf493cd294e637`;
the summary SHA-256 is
`8b9f614655adc5481eb6329a0e9d1ff7d7b757fe6d15ba6f606c5ff92540486d`.
The v3 `trades.csv`, `pairs.csv`, and `hold-only.json` are byte-identical to
v2; no retained historical row required an output-value correction. The v3
summary is substantively identical after excluding schema/source-policy
lineage fields. The new generation exists to make the arithmetic contract and
filled-path verification truthful and reproducible.
The finalized extraction reports zero network requests and
zero feature/strategy/simulator calls. Historical-as-of universe quality,
leveraged mechanics, funded portfolio effects, and causal component
attribution remain unavailable or outside scope. Every original evidence byte,
classification, protocol identity, and run identity remains unchanged.
