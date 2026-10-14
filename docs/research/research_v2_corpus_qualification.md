# Research V2 90-day LAST corpus — frozen source/units protocol

**Protocol:** `research-v2-weex-last-90d-v1` (Issue #179). This file is
source-hashed into the acquisition plan and is not a strategy-study calendar.
The outcome and per-member qualification are recorded separately after capture.

## Current official source review, 2026-10-14 UTC

The three endpoint-specific official URLs named in Issue #179 were opened for
this task:

- `https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines`
- `https://www.weex.com/api-doc/contract/Market_API/GetKlines`
- `https://www.weex.com/api-doc/contract/Market_API/GetContractInfo`

Each currently returned the general WEEX API landing rather than a distinct
historical schema or effective-date specification. The accessible official
`https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api`
page was re-opened during `2026-10-14T20:13:25Z`–`20:13:31Z`; the three
legacy URLs were opened earlier on 2026-10-14 (exact tool-level timestamps
were not retained). These are four distinct documentation URLs, not market
API attempts. The catalog page's current `GET /capi/v3/market/historyKlines`
definition documents the `api-contract.weex.com` host, IP request weight 5,
inclusive millisecond `startTime`/`endTime`, `limit` 1–100, `5m` and `1d`
intervals, and the eleven-field array: open time, O/H/L/C, base-asset volume,
close time, quote-asset volume, trade count, taker-buy base and quote volume.
The current exchangeInfo example/field definitions identify `baseAsset`,
`quoteAsset`, `marginAsset`, `contractType`, `underlyingType` and current
precision/contract-value/fee fields. These are **current documentation**, not
proof of historical contract specifications or absence of a scale change.
No additional official page is used to infer historical effective dates.

The accepted #173 catalogs and #175 combined selection are the fixed
selection/identity inputs. Their catalog capture times are distinct from
candle event times and contract-specification effective times. All selected
members were `COIN` / `PERPETUAL`, direct base-label plus `USDT`, USDT quote
and margin in the accepted current snapshot. The saved current `contractVal`
values below are recorded only as current, unqualified metadata:

| Symbol | Underlying | Current saved contractVal, not backdated |
| --- | --- | ---: |
| BTCUSDT | BTC | 0.0001 |
| ETHUSDT | ETH | 0.001 |
| SOLUSDT | SOL | 0.1 |
| XRPUSDT | XRP | 10 |
| ZECUSDT | ZEC | 0.1 |
| ZAMAUSDT | ZAMA | 100 |
| BCHUSDT | BCH | 0.01 |
| BTWUSDT | BTW | 100 |
| AVAXUSDT | AVAX | 1 |
| DOGEUSDT | DOGE | 100 |

For a future *unleveraged, pre-funding, normalized research-only* signal
model, one quantity unit means one unit of the declared base asset; price is
USDT per that unit, hence modeled quote notional is price times normalized
base quantity. Historical WEEX order-quantity/contract-count equivalence,
contract multiplier, minimums, precision, fees, margin and effective dates
remain **unresolved**. A base-volume candle field is not proof that a WEEX
historical order quantity used base units. The selected panel is exploratory
and selection-informed; no retrospective confirmatory, execution or trading
qualification follows from this data-only protocol.

## Immutable acquisition design

The only new market route is unauthenticated GET
`https://api-contract.weex.com/capi/v3/market/historyKlines` with `LAST`.
The ten selected symbols and `[2026-07-16T00:00:00Z,
2026-10-14T00:00:00Z)` are fixed. Each missing UTC day is partitioned into
three disjoint 5m queries of 100/100/88 opens; inclusive request end is the
exclusive chunk end minus one millisecond. Six separate BTC/ETH anchor-day
responses and ten recent 1d responses are reused byte-for-byte. At most ten
earlier 1d pages are new. The frozen plan records 2,704 distinct new shapes,
16 reused responses and at most 5,408 transmitted attempts. One retry is
limited to actual timeout or HTTP 5xx. 401/403/429 and non-timeout transport
failure stop the route. No market API fallback or alternative history source
exists.

Raw 11-field values remain exact Decimal strings or integer timestamps/counts.
Mismatched rows are quarantined, not repaired. A derived 15m/1h/4h row needs
all 3/12/48 same-epoch constituents. Each epoch needs 50 complete 4h bars
before any post-boundary 15m observation is called usable. Fully covered 5m
days are compared against the independently captured same-provider daily
rows without tolerance or adjustment. The separate result record must report
all missing/contaminated intervals and unresolved historical units.
