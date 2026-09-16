# WEEX recent 5m clean-window audit

## Decision

```text
WEEX_FUTURES_RECENT_5M_LAST = QUALIFIED
WEEX_RECENT_HTF_DIAGNOSTIC = COMPLETE
```

Official WEEX Futures V3 `5m` `LAST` history is fully clean for both `BTCUSDT` and `ETHUSDT` over the exact bounded interval:

`[2025-04-02T00:00:00Z, 2026-01-08T00:00:00Z)`

The complete 5-minute grid was audited, not sampled. Both symbols contained every expected row and had zero conflicting duplicates, malformed rows, off-grid timestamps, invalid OHLC, non-positive prices, negative additive fields, close-time misalignment, still-open rows, transport retries, or unrecovered requests.

This qualifies only the bounded recent window above. It does **not** retroactively qualify older WEEX history, which remains rejected by the prior full-window evidence.

## Provenance and scope

- GitHub Issue: `#111`
- Starting `main` SHA: `6d7fdc52c0e7a19f4521fc528ee17c0b484efed6`
- Source: official WEEX Futures V3 historical Kline endpoint
- Endpoint: `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Symbols: `BTCUSDT`, `ETHUSDT`
- Canonical candidate interval: `5m`
- Price type: `LAST`
- Authentication: none
- Locked half-open range: `[2025-04-02T00:00:00Z, 2026-01-08T00:00:00Z)`
- Full-audit workflow run: `34986766969`
- Full-audit execution: `2026-09-15T15:11:43.486080Z` through `2026-09-15T15:22:22.191627Z`
- Runtime: GitHub-hosted Ubuntu `24.04.5`, Python `3.12.14`
- Conditional HTF diagnostic workflow run: `34988184017`
- HTF diagnostic execution: `2026-09-15T15:24:06Z` through approximately `2026-09-15T15:24:26Z`

Only public unauthenticated WEEX market-data requests were used. No API key, account/private endpoint, order placement, Paper/Live activity, leverage action, strategy performance run, alternate exchange, MARK/INDEX substitution, interpolation, repair, or accepted project corpus import occurred.

## Audit semantics

The audit used the same fail-closed integrity principles established by the earlier WEEX canonical-data qualification:

- deterministic 5-minute UTC grid;
- exact `Decimal` parsing of numeric fields;
- 11-field V3 row validation;
- newest-first response handling without treating response order as canonical storage order;
- inclusive request-boundary handling with exact client-side page/range filtering;
- defensive duplicate detection;
- finite positive OHLC and valid high/low relationships;
- finite non-negative base volume, quote volume, trade count, taker-buy base volume, and taker-buy quote volume;
- exact `close_time = open_time + 5m` validation;
- closed-candle validation;
- no repair, interpolation, forward-fill, back-fill, or cross-series/cross-venue substitution.

Each request contained at most 100 expected grid points. Retries were permitted only for transient transport failures, HTTP 429, or HTTP 5xx with identical request parameters. In the completed run, neither symbol required any retry.

## Complete 5m integrity result

| Integrity fact | BTCUSDT | ETHUSDT |
| --- | ---: | ---: |
| Expected grid points | 80,928 | 80,928 |
| Received in-range unique rows | 80,928 | 80,928 |
| Missing timestamps | 0 | 0 |
| Duplicate open times | 0 | 0 |
| Conflicting duplicates | 0 | 0 |
| Off-grid timestamps | 0 | 0 |
| Malformed rows | 0 | 0 |
| Invalid OHLC relationships | 0 | 0 |
| Non-positive prices | 0 | 0 |
| Negative/invalid additive fields | 0 | 0 |
| Close-time/interval misalignment | 0 | 0 |
| Still-open rows | 0 | 0 |
| Unrecovered requests | 0 | 0 |
| Retry attempts | 0 | 0 |
| Page requests | 810 | 810 |
| First valid open | 2025-04-02 00:00Z | 2025-04-02 00:00Z |
| Last valid open | 2026-01-07 23:55Z | 2026-01-07 23:55Z |

No offending timestamp list exists for this bounded window because every audited integrity category is zero.

The acceptance gate therefore passes:

```text
WEEX_FUTURES_RECENT_5M_LAST = QUALIFIED
```

## Relationship to the earlier failed full-history audit

This result is intentionally narrower than Issue #106.

The earlier locked range began at `2024-01-01` and failed because older WEEX history contained missing and malformed data. In particular:

- BTCUSDT missing 5m history observed by the earlier audit ended by `2024-06-26T18:45Z`;
- ETHUSDT had later missing 5m timestamps on `2025-03-04`, including `11:20Z`, `15:50Z`, and `15:55Z`;
- the prior monthly diagnostic also observed malformed native BTCUSDT `1h` rows on `2025-04-01T18:00Z` through `21:00Z`.

The new boundary begins deliberately at `2025-04-02T00:00Z`, after those known defects. The full audit now demonstrates that the selected recent 5m window itself is clean. This is a bounded qualification, not a relaxation of the old integrity standard.

## Conditional higher-timeframe consistency diagnostic

Because the complete recent 5m audit qualified, the predeclared conditional diagnostic was executed.

For each symbol, complete native WEEX `5m` data was fetched for the first UTC calendar day of each month within the usable sample sequence:

- 2025-05-01
- 2025-06-01
- 2025-07-01
- 2025-08-01
- 2025-09-01
- 2025-10-01
- 2025-11-01
- 2025-12-01
- 2026-01-01

`15m`, `1h`, and `4h` rows were then derived causally from 5m using fixed rules established before comparison:

- open = first constituent open;
- high = maximum constituent high;
- low = minimum constituent low;
- close = last constituent close;
- additive fields = exact sums;
- target boundaries = fixed UTC target grids.

Derived values were compared using exact `Decimal` equality against native WEEX `LAST` rows. OHLC and additive fields were evaluated separately.

### BTCUSDT

| Interval | Expected | Derived unavailable | Native missing | Native malformed | OHLC exact / mismatch | Additive exact / mismatch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 864 | 0 | 0 | 0 | 864 / 0 | 864 / 0 |
| 1h | 216 | 0 | 0 | 0 | 216 / 0 | 216 / 0 |
| 4h | 54 | 0 | 0 | 0 | 54 / 0 | 54 / 0 |

### ETHUSDT

| Interval | Expected | Derived unavailable | Native missing | Native malformed | OHLC exact / mismatch | Additive exact / mismatch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 864 | 0 | 0 | 0 | 864 / 0 | 864 / 0 |
| 1h | 216 | 0 | 0 | 0 | 216 / 0 | 216 / 0 |
| 4h | 54 | 0 | 0 | 0 | 54 / 0 | 54 / 0 |

Every comparable OHLC tuple and every additive tuple matched exactly in the predeclared samples. There were no derived-unavailable rows, native missing rows, or native malformed rows.

```text
WEEX_RECENT_HTF_DIAGNOSTIC = COMPLETE
```

The result supports a reproducible architecture in which clean WEEX 5m LAST history is the canonical authority and V1 `15m`/`1h`/`4h` research context can be derived causally from that same source. Native higher-timeframe data is consistent with that derivation in all predeclared recent samples, but using one canonical lower-timeframe authority avoids depending on several independently maintained historical series.

## External evidence artifacts

The audit programs were temporary qualification utilities and were removed from the branch after execution. Machine-readable results were retained as GitHub Actions artifacts rather than becoming production code or an accepted research corpus.

### Complete recent-window 5m audit

- Workflow run: `34986766969`
- Artifact ID: `10404910545`
- Uploaded artifact ZIP SHA-256: `593136ce6f8e8e5de8b1e8357097720162bf583f67f77b69e9c7b5bc9f0d3c36`
- `summary.md` SHA-256: `a33c519f92a45b37b928635825527b0bf7589162cc82c71f7b4b872a42214629`
- `weex_recent_5m_audit_result.json` SHA-256: `44794bc992a24fafe86a81034e251242e5737f6673d7cb7213f9a1fa8e279480`

### Conditional HTF diagnostic

- Workflow run: `34988184017`
- Artifact ID: `10403464859`
- Uploaded artifact ZIP SHA-256: `99f232f6b0e241394acc353a81067ca9d50dff3eb1c73cd87917348c9b447518`
- `summary.md` SHA-256: `4bce0cd27a5dd45b607606b12ce3497a24cf2d78f3ab269c1b32dfd3d0243b3d`
- `weex_recent_htf_diagnostic.json` SHA-256: `a8731bf4abbd67baf1955d8d7abb1c085fac1360519b01b768fba6e4d534efe8`

## Architecture / governance conclusion

The evidence changes the historical-data decision materially:

1. WEEX does **not** need to be abandoned as the primary V1 historical authority for all research.
2. The bounded recent window from `2025-04-02` through `2026-01-08` is qualified as a clean same-venue 5m LAST base for BTCUSDT and ETHUSDT.
3. A future implementation issue may add a WEEX V3 public historical source/import path for this exact qualified window and derive `15m`, `1h`, and `4h` causally from 5m.
4. Older WEEX data before the qualified boundary remains unqualified and must not be silently mixed into the authoritative corpus.
5. If older external-exchange history is later used for robustness or longer-regime validation, it must remain a separately identified dataset with explicit venue provenance rather than repairing or extending the canonical WEEX corpus invisibly.

This qualification does not prove strategy profitability and does not authorize Paper, Live, leverage, private API integration, or capital deployment.

**No strategy performance result was produced.**
