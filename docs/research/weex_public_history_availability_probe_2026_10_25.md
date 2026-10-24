# WEEX public LAST history availability probe

**Issue:** #206  
**Evidence contract:** `issue-206-public-history-availability-probe-v1`  
**Inspected main:** `270be3d5d0d95112fa28cf31a2f14e3349db9062`  
**Inspected tree:** `d27a11efe9cae5079d9a1eb310ad0ed723352c10`  
**Terminal classification:** `OLDER_SAMPLES_MISSING_OR_PARTIAL`

This is a bounded public-data availability observation. It is not a historical
corpus, strategy evaluation, candidate count, economic study, or authorization
to acquire the intervening history. Original Breakout + Retest `1.0.0` and
zone-invalidation `0.1.0` remain frozen and unmodified.

## Frozen source and execution identity

The only route was unauthenticated `GET
https://api-contract.weex.com/capi/v3/market/historyKlines`. The fixed plan
contained the ten accepted panel members in their declared order and exactly
three `5m` / `LAST` / `limit=100` shapes per member: the October 24 recent
control, April 1 oldest-prefix anchor, and June 1 mid-prefix anchor. Redirects,
ambient proxies, retries, fallback hosts, pagination, credentials, and private
routes were disabled.

- Plan SHA-256: `3884cf7dd8b643d97dc4cdbcb90f7f4617b76cfee85da18194b8ac140c36a058`
- Diagnostic launcher SHA-256: `568d7d36eddf38e5d6d41ad43e9224ddc8cf15f18f4e28f7ad23569aef8c34b0`
- Foreground wrapper SHA-256: `5062fc100365d9f7952e9b525d31639222c39828132734c8a832ff6dbefaa51a`
- Reused parser/helper SHA-256: `23f79e89b1d1ff0194457988ced52abb490ae0c7396f5244b9c73c5f1cdb3c25`
- Evidence manifest SHA-256: `e49e61eb4e6c77b09c55381aceda61178322c37a6f1ee56e65e2adba6192012e`
- Summary SHA-256: `61e8b84243cd2af5e6caff292251b53f641b96fb383321553ab5442f8a2a8835`
- Foreground process exit: `0`; retained run exit: `0`
- Capture interval: `2026-10-24T21:05:52Z` through
  `2026-10-24T21:07:18Z`

Before traffic, offline checks proved that the literal boundary rejected an
extra symbol, changed date, changed host, and extra query key. The accepted
parser distinguished an empty array from malformed JSON and rejected an
out-of-request row. The frozen checkout was clean and Python/dependency/module
origins were recorded in `plan.json`.

## Observed coverage

All 30 planned requests were transmitted once, serially, with at least two
seconds between attempts. All 30 returned HTTP 200 arrays that passed the
existing exact eleven-field, number, OHLC, timestamp/grid, duplicate, and
request-bound parser. There were no retries, redirects, non-200 responses,
transport errors, schema failures, partial arrays, or unattempted requests.

| Symbol | Recent control, expected/received | April anchor, expected/received | June anchor, expected/received |
| --- | ---: | ---: | ---: |
| BTCUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| ETHUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| SOLUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| XRPUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| ZECUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| ZAMAUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| BCHUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| BTWUSDT | 100 / 100 `COMPLETE` | 96 / 0 `NO_ROWS_RETURNED_FOR_REQUEST` | 100 / 0 `NO_ROWS_RETURNED_FOR_REQUEST` |
| AVAXUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |
| DOGEUSDT | 100 / 100 `COMPLETE` | 96 / 96 `COMPLETE` | 100 / 100 `COMPLETE` |

The aggregate is 2,764 received rows from 2,960 expected grid positions:
28 complete samples, zero partial samples, and two valid empty arrays accounting
for 196 unreturned BTWUSDT positions. The recent BTWUSDT control was complete,
so the two old empty arrays are not a general route/schema failure. They remain
only `NO_ROWS_RETURNED_FOR_REQUEST`; they do not establish a listing date,
delisting, or universal retention boundary.

Nine members therefore have complete sampled April and June anchors. The
ten-member panel does not have older samples at both anchors because BTWUSDT
returned none. This supports `OLDER_SAMPLES_MISSING_OR_PARTIAL`, not
`OLDER_SAMPLES_AVAILABLE` for the complete panel and not
`TRANSPORT_OR_SCHEMA_BLOCKED`.

## Explicit limits

These 30 samples do not establish complete 180-day grids, daily
reconciliation, historical identity or contract units, any intervening epoch,
independence from already inspected history, or strategy viability. They do
not repair the two old BTC/BTW gaps or authorize a full-history download.
Because the panel was selected from a later current snapshot, any eventual
historical acquisition remains selection-informed exploration rather than a
survivorship-free confirmation.

The retained evidence is under
`data/research_evidence/issue-206-weex-history-probe-3884cf7/`. It preserves
the exact launcher, wrapper, plan, synthetic checks, attempt-start records,
raw response bytes, per-attempt results, stdout/stderr, run/process exits,
summary, and hash manifest. All predecessor evidence remains unchanged.
