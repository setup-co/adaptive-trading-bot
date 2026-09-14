# WEEX V1 market-data qualification

## Result

This bounded public-endpoint qualification does **not** pass the Futures historical acceptance gate for the intended study.

```text
WEEX_FUTURES_HISTORICAL = NOT_QUALIFIED
WEEX_SPOT_PUBLIC_KLINES = QUALIFIED
WEEX_SPOT_HISTORICAL_BACKFILL = UNDOCUMENTED
```

The V1 architecture policy remains WEEX-first and single-venue. This result means that a full historical importer or study must not proceed from the evidence observed here. No alternative venue is substituted.

## Provenance and scope

- Starting git SHA: `4cd3e669f7d43838c22a6759e09164edb93aaa99`
- Execution window: `2026-09-14T19:24:01Z` through `2026-09-14T19:45:15Z`
- Runtime: Windows 11 `10.0.26200`, AMD64; Python `3.12.14`; OpenSSL `3.5.8`; standard-library `urllib.request`; Git `2.45.1.windows.1`; GitHub CLI `2.96.0`
- Authentication: none
- Network behavior: ordinary HTTPS to the documented WEEX hosts; no credentials, private/account endpoints, proxy/VPN bypass, alternate domains, or alternate exchanges
- Probe scope: bounded requests only. No full 2024–2026 import, strategy/research run, private trading operation, Paper/Live activity, leverage action, or deployment occurred.

Official endpoint identities and schemas were taken from the WEEX documentation:

- Futures historical Klines: [`GET /capi/v3/market/historyKlines`](https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines)
- Futures Kline response schema: [Get Klines](https://www.weex.com/api-doc/contract/Market_API/GetKlines)
- Spot public Klines: [`GET /api/v3/market/klines`](https://www.weex.com/api-doc/spot/MarketDataAPI/GetKLineData)

The documented Futures historical request includes `symbol`, `interval`, `startTime`, `endTime`, `limit` from 1 through 100, and `priceType` (`LAST`, `MARK`, or `INDEX`). The documented historical `startTime` and `endTime` boundaries are inclusive. Kline rows contain millisecond open and close timestamps.

## Futures access and historical-depth matrix

The endpoint returned unauthenticated HTTP 200 JSON for all requests below. Each non-empty row had the documented 11 fields, descending open-time order, and `close_time = open_time + interval`.

The coverage probe requested `startTime = endTime = 2024-01-01T00:00:00Z` with `limit=2`. “Present” means the exact requested candle open appeared in the response; it is not a claim that every candle after that instant is available.

| Symbol | Interval | LAST at 2024 start | MARK at 2024 start | INDEX at 2024 start | Earliest successful point verified |
| --- | --- | --- | --- | --- | --- |
| BTCUSDT | 15m | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |
| BTCUSDT | 1h | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |
| BTCUSDT | 4h | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |
| ETHUSDT | 15m | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |
| ETHUSDT | 1h | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |
| ETHUSDT | 4h | Present | Present | Empty response | LAST/MARK: 2024-01-01; INDEX: 2026-01-01 |

`INDEX` is a documented and accepted request value—the server returned HTTP 200—but all six 2024 probes returned an empty array. Additional bounded 15m probes for both symbols were empty at `2025-01-01T00:00:00Z`. Bounded probes for both symbols and all three intervals contained the exact requested INDEX candle at `2026-01-01T00:00:00Z`. Therefore, `2026-01-01` is the earliest point successfully verified by these probes for every required INDEX series; it is not asserted to be the true first available INDEX candle.

## Boundary, ordering, and pagination observations

A BTCUSDT 15m LAST probe used the inclusive requested range `2024-01-01T00:00:00Z` through `2024-01-03T00:00:00Z`:

- `limit=100` returned 100 rows, which is the documented maximum and the practical page size verified here.
- Rows were newest-first (strict descending open time).
- The first page included the candle whose open time exactly equalled `endTime`, confirming the documented inclusive upper boundary.
- Reusing the oldest returned open time as the next `endTime` repeated that boundary candle.
- Setting the next `endTime` to `oldest_open - interval` avoided that overlap deterministically.
- The API returned candles earlier than the requested `startTime` to fill a page. `startTime` therefore cannot be treated as a hard server-side lower filter in the observed behavior; a client must filter the exact requested range itself.
- After client-side range filtering and deduplication, 191 of 193 expected grid points were present. The missing opens were `2024-01-01T02:45:00Z` and `2024-01-02T03:45:00Z`.
- Direct LAST probes ending at each missing timestamp also omitted the target, while equivalent MARK probes contained it. These are observed LAST-series gaps, not pagination overlap or client filtering artifacts.

A deterministic importer can paginate newest-first, advance by one interval before the prior oldest open, sort ascending, deduplicate defensively, filter to the requested half-open storage range, and invoke the existing integrity authority. It cannot truthfully make this sampled LAST history gap-free without repair or synthesis, which is forbidden. The existing integrity scan should reject such a result.

## Closed-candle handling

The returned close timestamp is explicit and was exactly one interval after the open timestamp in every non-empty probe. In a BTCUSDT 15m LAST request whose `endTime` was the aligned open of the currently forming interval, the endpoint returned 10 candles and all had `close_time <= observed_at`; the newest returned candle closed at that aligned boundary. No still-open candle was returned in that observation.

This observation is not used as an assumption that the service will always suppress a forming candle. A future source adapter must independently retain only candles whose authoritative `close_time` is at or before its UTC observation cutoff, validate interval alignment and duration, and fail closed on malformed data. It must never fabricate a close timestamp or persist a still-open candle.

## Spot capability and limitation

Only the officially documented Spot request shape was tested: `symbol` and `interval`. No undocumented historical parameters were sent.

| Symbol | 15m | 1h | 4h |
| --- | --- | --- | --- |
| BTCUSDT | HTTP 200, 301 rows | HTTP 200, 301 rows | HTTP 200, 301 rows |
| ETHUSDT | HTTP 200, 301 rows | HTTP 200, 301 rows | HTTP 200, 301 rows |

All six responses were descending, used the documented 11-field shape, and had interval-consistent open/close timestamps. This qualifies the documented public Kline capability only.

The Spot parameter table documents `symbol` and `interval`; it does not document historical `startTime`/`endTime`, a cursor, or pagination semantics. Although the documentation example contains a `limit` query, the parameter table does not define a historical contract. Consequently, official Spot historical backfill remains `UNDOCUMENTED`; no pagination or depth claim is made.

## Acceptance decision and required follow-up

Futures public HTTPS access and deterministic boundary handling are demonstrated, and LAST/MARK contain exact candles at the intended 2024 start for all required symbols and intervals. The acceptance-critical capability nevertheless fails because:

1. the bounded BTCUSDT 15m LAST continuity probe contains confirmed source gaps; and
2. INDEX returned no data at the required 2024 start for either symbol or any required interval.

The required deterministic gap-free historical capability is therefore not established. The issue's stop condition applies: do not run the full importer or EMA50 study, and do not switch exchanges inside this qualification.

A project code change would eventually be required because the current importer source does not implement this WEEX Futures V3 contract. That future work belongs in a separate issue only after the historical-data blocker is explicitly resolved; it should be a WEEX-specific public source adapter feeding the existing bounded importer, store, integrity, Replay, and research contracts. No WEEX dependency belongs in Strategy, Risk, Safety, Feature, Replay domain logic, or accounting.

**No strategy performance result was produced.**
