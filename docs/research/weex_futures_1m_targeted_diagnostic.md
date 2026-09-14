# WEEX Futures 1m LAST targeted diagnostic

## Decision

```text
WEEX_FUTURES_1M_TARGETED_DIAGNOSTIC = FAIL
```

The known WEEX archive gaps propagate below 5m. Neither critical BTCUSDT 15m window has any of its 15 required V3 1m LAST constituents. Nineteen of the 55 predeclared windows are incomplete or malformed, including three of four nearby controls.

This targeted FAIL does not qualify 1m as canonical and does not authorize a full-window 1m audit. The finer-timeframe fallback chain stops pending an explicit historical-data governance decision.

## Provenance and scope

- Starting git SHA: `5849b856fb9873631c22483d461198704935e585`
- Endpoint: `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Locked parameters: `interval=1m`, `priceType=LAST`, unauthenticated public HTTPS
- Source-request execution: `2026-09-14T22:47:34.475971Z` through `2026-09-14T22:48:12.538024Z`
- Offline validation finalized: `2026-09-14T22:49:54.192259Z`
- Runtime: Windows 11 `10.0.26200`, AMD64; Python `3.12.14`; OpenSSL `3.5.8`; standard-library `urllib.request`
- Artifact schema: `atb.weex_futures_1m_targeted_diagnostic.v1`

The endpoint and 11-field row schema are documented by the official [WEEX Futures V3 historical Klines reference](https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines).

Only the 55 predeclared windows were requested: two critical BTCUSDT 15m windows, 20 committed BTCUSDT missing-5m samples, all 29 explicitly committed ETHUSDT missing-5m windows, and four nearby BTCUSDT 15m controls. No V2, MARK, INDEX, 1m full-window audit, alternate exchange, repair, interpolation, importer/corpus, or trading/performance path was used.

## Method and validation

Each request used the exact target start, `endTime` equal to the final expected 1m open, and `limit` equal to the expected constituent count: 15 for 15m windows and 5 for 5m windows. Because WEEX boundaries can be inclusive and responses can backfill before `startTime`, rows were client-filtered to the exact half-open target before validation. Five returned rows fell outside their target request range and were discarded.

For every retained row, the diagnostic required:

- exact UTC one-minute grid alignment and unique open time;
- an 11-field parse using exact `Decimal` values;
- finite positive OHLC and valid high/low relationships;
- finite non-negative base/quote volumes, trade count, and taker-buy volumes;
- `close_time = open_time + 1m`; and
- a close time no later than the observation time.

Only rows passing every requirement counted as present. Missing lists therefore include timestamps whose returned rows were malformed. No tolerance, coercion, repair, or content-gap retry was used.

All 55 requests returned HTTP 200 on their first attempt. Retry count and failed-request count were both zero. Across the target set:

| Metric | Result |
| --- | ---: |
| Complete, valid windows | 36 |
| Incomplete/malformed windows | 19 |
| Duplicate opens | 0 |
| Conflicting duplicates | 0 |
| Malformed rows | 10 |
| Other invalid/off-grid rows | 0 |
| Successful causal aggregations | 36 |
| Derived aggregation validation errors | 0 |

An offline post-run verification corrected the temporary validator's expected close duration for derived 5m/15m rows. It revalidated only the already-saved derived rows against each target duration and made no endpoint request or source-data change. The FAIL outcome was unchanged.

## Critical BTCUSDT 15m windows

| Window `[start,end)` | Expected | Valid | Exact missing opens | Duplicates / conflicts / malformed | Aggregated |
| --- | ---: | ---: | --- | --- | --- |
| `2024-01-01T02:45Z`–`03:00Z` | 15 | 0 | Every minute from 02:45 through 02:59 | 0 / 0 / 0 | No |
| `2024-01-02T03:45Z`–`04:00Z` | 15 | 0 | Every minute from 03:45 through 03:59 | 0 / 0 / 0 | No |

Neither critical candle can be reconstructed. No derived 15m row was created or written to storage.

## BTCUSDT committed missing-5m sample

The resolved list is exactly the first ten and last ten timestamps recorded by the merged Issue #106 evidence.

| 5m window start | Expected | Valid | Exact missing opens | Complete |
| --- | ---: | ---: | --- | --- |
| `2024-01-01T00:00Z` | 5 | 0 | 00:00–00:04 | No |
| `2024-01-01T00:10Z` | 5 | 0 | 00:10–00:14 | No |
| `2024-01-01T00:20Z` | 5 | 0 | 00:20–00:24 | No |
| `2024-01-01T00:25Z` | 5 | 0 | 00:25–00:29 | No |
| `2024-01-01T00:45Z` | 5 | 0 | 00:45–00:49 | No |
| `2024-01-01T01:00Z` | 5 | 0 | 01:00–01:04 | No |
| `2024-01-01T01:10Z` | 5 | 0 | 01:10–01:14 | No |
| `2024-01-01T01:25Z` | 5 | 0 | 01:25–01:29 | No |
| `2024-01-01T01:35Z` | 5 | 0 | 01:35–01:39 | No |
| `2024-01-01T01:50Z` | 5 | 0 | 01:50–01:54 | No |
| `2024-06-23T15:50Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:40Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:45Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:50Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:55Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:00Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:05Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:10Z` | 5 | 5 | None | Yes |
| `2024-06-26T18:40Z` | 5 | 5 | None | Yes |
| `2024-06-26T18:45Z` | 5 | 5 | None | Yes |

The first ten sample windows have none of their 50 expected constituents. The last ten are complete and aggregate deterministically, demonstrating that some missing native 5m rows can be reconstructed from 1m while the acceptance-critical older archive gaps cannot.

## ETHUSDT committed missing-5m windows

| 5m window start | Expected | Valid | Exact missing opens | Complete |
| --- | ---: | ---: | --- | --- |
| `2024-02-19T23:35Z` | 5 | 0 | 23:35–23:39 | No |
| `2024-02-26T22:50Z` | 5 | 1 | 22:50, 22:51, 22:52, 22:54 | No |
| `2024-03-19T13:20Z` | 5 | 5 | None | Yes |
| `2024-03-19T13:25Z` | 5 | 5 | None | Yes |
| `2024-03-19T13:30Z` | 5 | 5 | None | Yes |
| `2024-03-19T13:35Z` | 5 | 5 | None | Yes |
| `2024-03-20T00:40Z` | 5 | 5 | None | Yes |
| `2024-03-20T00:45Z` | 5 | 5 | None | Yes |
| `2024-03-20T00:50Z` | 5 | 5 | None | Yes |
| `2024-03-20T00:55Z` | 5 | 5 | None | Yes |
| `2024-04-07T23:00Z` | 5 | 5 | None | Yes |
| `2024-04-07T23:05Z` | 5 | 5 | None | Yes |
| `2024-04-07T23:10Z` | 5 | 5 | None | Yes |
| `2024-04-07T23:15Z` | 5 | 5 | None | Yes |
| `2024-06-23T15:40Z` | 5 | 5 | None | Yes |
| `2024-06-23T15:45Z` | 5 | 5 | None | Yes |
| `2024-06-23T15:50Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:40Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:45Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:50Z` | 5 | 5 | None | Yes |
| `2024-06-25T17:55Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:00Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:05Z` | 5 | 5 | None | Yes |
| `2024-06-25T18:10Z` | 5 | 5 | None | Yes |
| `2024-06-26T18:40Z` | 5 | 5 | None | Yes |
| `2024-06-26T18:45Z` | 5 | 5 | None | Yes |
| `2025-03-04T11:20Z` | 5 | 5 | None | Yes |
| `2025-03-04T15:50Z` | 5 | 0 | 15:50–15:54; all five returned rows malformed | No |
| `2025-03-04T15:55Z` | 5 | 0 | 15:55–15:59; all five returned rows malformed | No |

Twenty-five of 29 ETHUSDT windows are complete. The two March 2025 failing windows returned all ten open timestamps, but every row contained a scalar that failed exact Decimal parsing; none was coerced or accepted.

## Nearby BTCUSDT 15m controls

| Control window `[start,end)` | Expected | Valid | Exact missing opens | Complete |
| --- | ---: | ---: | --- | --- |
| Before first critical: `2024-01-01T02:30Z`–`02:45Z` | 15 | 3 | 02:30, 02:31, 02:32, and 02:36–02:44 | No |
| After first critical: `2024-01-01T03:00Z`–`03:15Z` | 15 | 13 | 03:01, 03:02 | No |
| Before second critical: `2024-01-02T03:30Z`–`03:45Z` | 15 | 1 | 03:30–03:42 and 03:44 | No |
| After second critical: `2024-01-02T04:00Z`–`04:15Z` | 15 | 15 | None | Yes |

The complete final control aggregated and validated successfully. The other controls show that missing 1m coverage extends around the critical target windows rather than reflecting an inability to retrieve every surrounding row.

## Aggregation conclusion

For each of the 36 complete windows, aggregation used first open, maximum high, minimum low, last close, exact sums for base/quote volume, trade count, and taker-buy fields, plus target-grid output boundaries. All 36 derived rows passed their appropriate 5m or 15m duration, OHLC, additive, and causality checks.

No aggregation was attempted for any incomplete or malformed window. No derived row was persisted or substituted for a native candle.

## External artifacts

The temporary diagnostic utility and complete machine-readable response analysis remain outside Git in the isolated Issue #108 evidence root:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `weex-1m-targeted-diagnostic-result.json` | 84,442 | `9ae55ba6edb07837c81ca2b949d23c07580da9df97e2dc8e595a56547476551d` |
| `weex_1m_targeted_diagnostic.py` | 18,634 | `2d2f86fdfd822dc3387d49060b4cbd5eaa361145a484f23f4d1b3ab94441aeaa` |

These artifacts are diagnostic evidence, not an accepted historical corpus.

## Final gate

The diagnostic fails because the two critical BTCUSDT 15m targets have 0 of 30 required constituents, half of the BTC missing-5m sample fails, four ETH missing-5m targets fail, three of four controls fail, and ten targeted ETH rows are malformed. The all-target decision gate cannot pass.

`WEEX_FUTURES_1M_TARGETED_DIAGNOSTIC = FAIL`

Do not proceed automatically to a full 2024–2026 1m audit or importer. Escalate to an explicit historical-data governance decision without weakening integrity or switching data sources implicitly.

**No corpus, importer, or strategy performance result was produced.**
