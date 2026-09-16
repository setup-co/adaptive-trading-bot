# Paired research entry-fill admission

## Research-only boundary

The paired Trend Pullback exit replay applies one fail-closed admission check after the generic
`BacktestExecutionSimulator` has returned an entry fill and before any position accounting or
exit branch exists:

```text
BUY / LONG   requires sealed stop_basis < actual fill_price
SELL / SHORT requires sealed stop_basis > actual fill_price
```

Equality is invalid. A fill that fails this check is recorded as
`ENTRY_FILL_INVALIDATED` with reason `STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL`. It is not an unfilled
order, a winning or losing trade, or an automatic stop exit. The exact simulator fill remains
execution evidence, but no research position, accounting batch, protective-stop replay, EMA50
evaluation, PnL, or paired delta is created.

The result retains a creation-sealed detached snapshot of the authorized entry intent, the exact
market-order request and fill, the execution-cost configuration, and the stop/reference/fill
audit. Risk and Safety sizing and stop authority are preserved exactly; the research adapter does
not move the stop, resize the order, or re-run a post-fill risk decision. The state has no runtime
or trading authority and does not change Paper, Shadow, or Live behavior.

## Robustness population semantics

Research schemas for paired replay, robustness configuration/observation/report/artifact, and
stratification are version `2.0.0`. The population partition is exact:

```text
candidate_count
  = entry_unfilled_count
  + entry_fill_invalidated_count
  + replayed_count
```

`entry_filled_count` and `entry_filled_rate` mean simulator-filled entries and therefore include
both invalidated and replay-admitted fills. `replayed_count` is the admitted-position count.
`entry_fill_invalidated_rate` uses the complete candidate population as its denominator.

Invalidated fills may appear in admission strata, but they are excluded from branch closure and
unresolved rates, protective-stop and EMA50 counts, comparable deltas, overlap components,
exit-time comparisons, and all realized-PnL summaries. Those branch metrics use only replayed
positions as their population.

## Issue #118 relationship

The failed Issue #118 Run A remains stopped and is not retroactively continued. The diagnostic
for Issue #120 is limited to reproducing the first historical offender from the unchanged locked
WEEX corpus and proving the new admission classification. A complete robustness report and any
EMA50 performance interpretation require a separate newly predeclared study after this contract
is reviewed and merged.

## Narrow locked-corpus diagnostic

The Issue #120 diagnostic used only the unchanged Issue #118 identities and stopped immediately
after the first invalidated fill. It did not construct a robustness report, continue to later
candidates, or inspect any aggregate outcome:

- source ID: `weex-v3-last-e5b0c4035a19d5a80ca7`
- dataset version: `weex-v1-5m-last-a629a29b580b2e103adba918`
- manifest SHA-256: `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`
- Run A decision window: `[2024-07-01T00:00:00Z, 2026-01-01T00:00:00Z)`
- fixed follow-up: `168` hours
- research equity: `10000 USDT`
- costs: spread `2` bps, slippage `2` bps, fee `10` bps
- corpus ID:
  `trend-pullback-research-corpus:da96849f76d4e3d021a76ce31df21df80181c1f77495e1f8b5145d962550e871`
- corpus digest: `da96849f76d4e3d021a76ce31df21df80181c1f77495e1f8b5145d962550e871`
- admissible identities encountered: first invalidation at ordinal `17` of `146`; identities after
  ordinal `17` were not replayed by the diagnostic

The first offender reproduced with these exact facts:

| Fact | Value |
| --- | --- |
| Decision identity | `weex-v3-last-e5b0c4035a19d5a80ca7 / BTC/USDT / 2024-08-20T14:00:00Z / trend_pullback / 1.0.0` |
| Entry side | `BUY` / LONG |
| Decision reference price | `60850.600000000000000000` |
| Sealed stop basis | `60390.300000000000000000` |
| Authorized quantity | `0.05431240495329133174016945470345427` |
| Order created / fill eligible | `2024-08-20T14:00:00Z` / `2024-08-20T14:00:00Z` |
| Execution candle open / fill time | `2024-08-20T14:15:00Z` / `2024-08-20T14:15:00Z` |
| Reference open | `60362.700000000000000000` |
| Actual fill | `60380.8088100000000000000000` |
| Result | `ENTRY_FILL_INVALIDATED` |
| Reason | `STOP_NOT_BEYOND_ACTUAL_ENTRY_FILL` |

For this BUY fill, `60390.3 < 60380.80881` is false. The stored result retained the exact order,
fill, cost, source/symbol/timeframe, reference, stop, and quantity provenance while reporting no
baseline branch, no EMA50 branch, and no PnL delta. This closes the tooling reproduction only; it
is not evidence for or against the EMA50 hypothesis.
