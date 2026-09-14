# WEEX Futures 5m LAST canonical audit

## Decision

```text
WEEX_FUTURES_5M_LAST_CANONICAL = NOT_QUALIFIED
```

The complete locked-window audit found missing, conflicting-duplicate, and malformed authoritative V3 5m LAST rows for both required symbols. Neither previously identified missing BTCUSDT native 15m candle can be reconstructed because all three of its required 5m constituents are absent.

This result stops the canonical-source proposal. No fallback, repair, interpolation, accepted corpus import, or strategy study was performed.

## Provenance and scope

- Starting git SHA: `e156b7cb6a93600ee8d78b880a6e6b862f5ef051`
- Final complete audit execution: `2026-09-14T21:50:12.416140Z` through `2026-09-14T21:57:24.770380Z`
- Locked half-open range: `[2024-01-01T00:00:00Z, 2026-01-08T00:00:00Z)`
- Symbols: `BTCUSDT`, `ETHUSDT`
- Canonical candidate: WEEX Futures V3, `interval=5m`, `priceType=LAST`
- Runtime: Windows 11 `10.0.26200`, AMD64; Python `3.12.14`; OpenSSL `3.5.8`; standard-library `urllib.request`
- Authentication: none
- Final audit artifact schema: `atb.weex_futures_5m_last_canonical_audit.v1`

Only public WEEX endpoints were used. No MARK/INDEX substitution, 1m fallback, alternate exchange/vendor, private endpoint, Paper/Live/Experience change, leverage action, or deployment occurred.

Official sources:

- [WEEX Futures V3 historical Klines](https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines)
- [WEEX Futures V3 Kline response schema](https://www.weex.com/api-doc/contract/Market_API/GetKlines)
- [WEEX Futures V2 candlestick data](https://www.weex.com/api-doc/contract/V2/Market_API/GetKLineData)
- [WEEX Futures V2 change log](https://www.weex.com/api-doc/contract/V2/log/changelog)

The V3 row schema used here is the documented 11-field array: open time, OHLC, base volume, close time, quote volume, trade count, taker-buy base volume, and taker-buy quote volume. Decimal fields were parsed and compared with exact `Decimal` semantics.

## Audit method

The expected grid contains 212,544 five-minute opens per symbol. The temporary audit utility remained outside Git and did not write to the project candle store.

The full range was split into deterministic, non-overlapping requests of at most 100 expected grid points, the documented V3 maximum. Each request used explicit inclusive `startTime` and `endTime`, `limit=100`, and `priceType=LAST`. There were 2,126 canonical page requests per symbol.

For every response, the auditor:

1. honored the observed newest-first ordering without treating it as canonical storage order;
2. filtered to that page's exact inclusive bounds before accepting or validating content;
3. parsed all 11 fields without normalizing or filling them;
4. de-duplicated by exact open time for counting while separately recording duplicates and conflicts;
5. verified five-minute UTC grid alignment and `close_time = open_time + 5m`;
6. verified finite positive OHLC, OHLC relationships, and finite non-negative additive fields/trade counts;
7. verified every retained candle was closed at audit time; and
8. computed the exact missing grid from the locked half-open range.

WEEX sometimes returned rows older than the explicit page `startTime` while filling a response. Those rows were counted separately and excluded before in-range domain validation: 376 for BTCUSDT and 67 for ETHUSDT.

Transport/API retries used identical request parameters, at most four total attempts, and deterministic 1s/2s/4s backoff. Only transport failures, HTTP 429, and HTTP 5xx were retryable. Content gaps were never retried as repair:

| Symbol | Retry attempts | Requests recovered after an error | Unrecovered requests | Attempt-error breakdown |
| --- | ---: | ---: | ---: | --- |
| BTCUSDT | 195 | 142 | 0 | 193 HTTP 429; 2 URL/transport errors |
| ETHUSDT | 112 | 83 | 0 | 109 HTTP 429; 1 incomplete response; 2 remote disconnects |

Two preliminary executions failed closed and were discarded before the final complete pass: the first exposed an uncaught incomplete chunked response, and the second exposed blank native comparison fields. The temporary collector was corrected to classify those conditions without coercion. No partial result was merged into the final artifacts.

## Full-window 5m integrity

| Integrity fact | BTCUSDT | ETHUSDT |
| --- | ---: | ---: |
| Expected grid points | 212,544 | 212,544 |
| Received in-range unique rows | 209,869 | 212,515 |
| Missing timestamps | 2,675 | 29 |
| Duplicate open times | 6 | 4 |
| Conflicting duplicates | 6 | 4 |
| Off-grid timestamps | 0 | 0 |
| Malformed rows | 6 | 11 |
| Invalid OHLC relationships | 0 | 0 |
| Non-positive prices | 0 | 0 |
| Negative volume/trade-count rows | 1 | 0 |
| Close-time/interval misalignment | 0 | 0 |
| Still-open rows | 0 | 0 |
| Canonical page requests | 2,126 | 2,126 |
| First parsed in-range open | 2024-01-01 00:05Z | 2024-01-01 00:00Z |
| Last parsed in-range open | 2026-01-07 23:55Z | 2026-01-07 23:55Z |

The missing counts by UTC month were:

| Symbol | Month | Missing |
| --- | --- | ---: |
| BTCUSDT | 2024-01 | 1,905 |
| BTCUSDT | 2024-02 | 672 |
| BTCUSDT | 2024-03 | 82 |
| BTCUSDT | 2024-04 | 4 |
| BTCUSDT | 2024-06 | 12 |
| ETHUSDT | 2024-02 | 2 |
| ETHUSDT | 2024-03 | 8 |
| ETHUSDT | 2024-04 | 4 |
| ETHUSDT | 2024-06 | 12 |
| ETHUSDT | 2025-03 | 3 |

The first ten missing BTCUSDT opens are `2024-01-01T00:00Z`, `00:10Z`, `00:20Z`, `00:25Z`, `00:45Z`, `01:00Z`, `01:10Z`, `01:25Z`, `01:35Z`, and `01:50Z`. The last ten are `2024-06-23T15:50Z`, `2024-06-25T17:40Z` through `18:10Z` on the five-minute grid, and `2024-06-26T18:40Z`/`18:45Z`. The full ordered list is preserved in the hashed external artifact below.

The complete ETHUSDT missing list is:

- `2024-02-19T23:35Z`
- `2024-02-26T22:50Z`
- `2024-03-19T13:20Z`, `13:25Z`, `13:30Z`, `13:35Z`
- `2024-03-20T00:40Z`, `00:45Z`, `00:50Z`, `00:55Z`
- `2024-04-07T23:00Z`, `23:05Z`, `23:10Z`, `23:15Z`
- `2024-06-23T15:40Z`, `15:45Z`, `15:50Z`
- `2024-06-25T17:40Z`, `17:45Z`, `17:50Z`, `17:55Z`, `18:00Z`, `18:05Z`, `18:10Z`
- `2024-06-26T18:40Z`, `18:45Z`
- `2025-03-04T11:20Z`, `15:50Z`, `15:55Z`

## Known missing native 15m reconstruction

The fixed causal aggregation rule was predeclared as first open, maximum high, minimum low, last close, sums for all additive fields, and boundaries from the 15m UTC grid.

| Missing native BTCUSDT 15m open | Required 5m constituents | Present | Reconstructable |
| --- | --- | --- | --- |
| `2024-01-01T02:45:00Z` | 02:45, 02:50, 02:55 | none | No |
| `2024-01-02T03:45:00Z` | 03:45, 03:50, 03:55 | none | No |

No derived candle was created for either incomplete window and nothing was written to project storage.

Eight nearby native BTCUSDT 15m control rows were checked. Four could not be derived because nearby 5m constituents were also missing. For the four complete derived controls, all four OHLC tuples matched native rows exactly. One additive tuple matched exactly and three differed under exact Decimal comparison; no tolerance or rounding adjustment was introduced.

## Predeclared monthly native comparisons

For each symbol, the first UTC calendar day of every month from 2024-01 through 2025-12 was derived independently from complete 5m constituents and compared with native WEEX LAST rows. The rules were fixed before observing results.

### BTCUSDT

| Interval | Expected rows | Derived unavailable | Native missing with complete derived | Native malformed | OHLC exact / mismatch | Additive exact / mismatch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 2,304 | 88 | 0 | 0 | 2,216 / 0 | 2,119 / 97 |
| 1h | 576 | 29 | 0 | 4 | 543 / 0 | 524 / 19 |
| 4h | 144 | 12 | 0 | 0 | 132 / 0 | 132 / 0 |

The four malformed native 1h rows were `2025-04-01T18:00Z` through `21:00Z`; each returned blank taker-buy base and quote fields and was not coerced into a comparison. All other comparable BTC OHLC tuples matched. Additive mismatches remain reported exactly and were not used to tune aggregation.

### ETHUSDT

| Interval | Expected rows | Derived unavailable | Native missing with complete derived | Native malformed | OHLC exact / mismatch | Additive exact / mismatch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 2,304 | 0 | 0 | 0 | 2,304 / 0 | 2,304 / 0 |
| 1h | 576 | 0 | 0 | 0 | 576 / 0 | 576 / 0 |
| 4h | 144 | 0 | 0 | 0 | 144 / 0 | 144 / 0 |

These fixed monthly samples show that deterministic aggregation is possible when all constituents are valid, but they do not override the full-window gaps or authorize a partial corpus.

## V2 diagnostic control

The official V2 public endpoint was reachable and returned HTTP 200 with 1,000 latest BTCUSDT 15m LAST rows, spanning `2026-09-04T12:00Z` through `2026-09-14T21:45Z` in this observation.

The official V2 parameter contract currently documents `symbol`, `granularity`, `limit`, and `priceType`, but no historical time boundary or cursor. The official change log states that Kline time filters were removed on 2025-08-25. Therefore, there is no documented mechanism to request the two 2024 targets. No undocumented parameter was invented. The targets were not in the latest-window response, so V2 could not distinguish a V3 aggregation defect from underlying historical absence.

V2 data was not merged with V3, persisted, or treated as a production/research fallback.

## External artifacts

The full normalized unique-row captures, complete missing lists, comparison details, final machine-readable result, and temporary script remain outside Git under the isolated Issue #106 audit root. They are not an accepted research corpus.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `audit-result.json` | 213,919 | `3b517f52f986a8be51258bd9c26d85d2e1322d432a921773f8556e08c5cb68ac` |
| `btcusdt-5m-last-v3.jsonl.gz` | 9,713,243 | `133e704d46ac99d443cb0b736f7ee4a1e5bd89083a1e31fa978c3d2477e38c2b` |
| `btcusdt-5m-missing-open-times.json` | 144,550 | `9c85177dcc87113abe54ee9e54309ed5424dc57cedee0962e65e8ac460e3ddbc` |
| `btcusdt-monthly-native-comparison-details.json.gz` | 10,697 | `e2400d903bf83c4a3aa20c2a23c79c3625cc08cb433c5a10673477dfedd0be15` |
| `ethusdt-5m-last-v3.jsonl.gz` | 9,711,631 | `62912fb99f416b464734ec0d3587ecdad273ac0fd3987926174322388d78eecd` |
| `ethusdt-5m-missing-open-times.json` | 1,666 | `7ff8af160970c2c2fbf21373f1905ac0f85fe8b06565f7a6197231761e67c195` |
| `ethusdt-monthly-native-comparison-details.json.gz` | 75 | `f4b6d0bcaa2273f947c8fe4a9b52727a812570cf6f73c01405ffda041fb612c6` |
| `weex_5m_audit.py` | 30,710 | `8fc72203840e24367270a926c16a29e0cb8dff197c3ac4ca66e393e287aa8b42` |

## Acceptance conclusion

The proposal fails multiple mandatory conditions:

1. BTCUSDT has 2,675 missing canonical grid points and ETHUSDT has 29.
2. Both series contain conflicting duplicates and malformed in-range rows.
3. BTCUSDT contains one negative additive-field row.
4. The two known missing native BTCUSDT 15m windows have no authoritative 5m constituents and are not reconstructable.

The result is therefore `WEEX_FUTURES_5M_LAST_CANONICAL = NOT_QUALIFIED`. Implementing a canonical 5m importer or weakening integrity requirements is not authorized by this audit. A separate explicit data-governance decision is required before further real-data research.

**No strategy performance result was produced.**
