# Ourbit single-venue candidate qualification

## Decision

```text
OURBIT_FUTURES_PUBLIC_HISTORY = QUALIFIED
OURBIT_FUTURES_15M_HISTORY = NOT_QUALIFIED
OURBIT_SPOT_PUBLIC_KLINES = QUALIFIED
OURBIT_SPOT_HISTORICAL_BACKFILL = QUALIFIED
OURBIT_TRADING_API_DOCUMENTED = YES
OURBIT_SINGLE_VENUE_CANDIDATE = NOT_QUALIFIED
```

Ourbit is not adopted as the V1 venue. Its public Futures history has a complete
15-minute timestamp grid for both locked symbols, but 79 BTC_USDT rows and 82
ETH_USDT rows violate basic OHLC containment. The acceptance gate requires zero
invalid OHLC rows, so the candidate fails even though no grid points are missing.

The master plan remains unchanged. A new historical-data governance decision is
required before evaluating another venue or changing the V1 venue policy.

## Provenance and locked scope

- Starting Git SHA: `6d7fdc52c0e7a19f4521fc528ee17c0b484efed6`
- Audit range: `[2024-01-01T00:00:00Z, 2026-01-08T00:00:00Z)`
- Futures symbols: `BTC_USDT`, `ETH_USDT`
- Acceptance timeframe: `Min15`
- Conditional comparison timeframes: `Min60`, `Hour4`
- Evidence collection interval: `2026-09-14T23:28:05Z` through
  `2026-09-14T23:39:52Z`
- Accepted full-audit execution: `2026-09-14T23:36:55.435287Z` through
  `2026-09-14T23:38:01.735356Z`
- Runtime: Windows 11 `10.0.26200`, AMD64; Python `3.12.14`; OpenSSL `3.5.8`
- Audit artifact schema: `atb.ourbit_single_venue_qualification.v1`

Only public, unauthenticated Ourbit-owned HTTPS surfaces were called. No API key
was created, no private endpoint was called, and no order or account operation was
attempted.

## Official-source identity

The official Ourbit developer organization publishes the
[Futures V1 Postman collection](https://github.com/ourbitdevelop/ourbit-api-postman/blob/main/OURBIT%20V1%20contract.postman_collection.json),
the [API demo repository](https://github.com/ourbitdevelop/ourbit-api-demo), and
the [Spot API documentation](https://github.com/ourbitdevelop/apidocs/blob/main/source/index.html.md).
The exact source revisions inspected were:

| Official source | Inspected commit |
| --- | --- |
| `ourbit-api-postman` | `afc2a7312a46007a78a53b54a315b9904d275009` |
| `ourbit-api-demo` | `9ecbf4befc1c78f8ae9feccfc9eb68fbda1daeb9` |
| `apidocs` | `fb897390a85ac3e8ed16ce4da6b243d3f647a807` |

The published Futures collection and SDK define
`GET /api/v1/contract/kline/{symbol}` and show
`contract.ourbit.com` as the Futures base host. That hostname returned DNS
`SOA`/no address during this run. The current official Ourbit Futures web origin,
`futures.ourbit.com`, resolved through Cloudflare and served the same documented
public contract paths. It returned HTTP 200 for `ping`, `detail`, and `kline`.
This host drift is a documentation/operational limitation that must be rechecked
before any future adapter work; it does not excuse the failed data-integrity gate.

The live contract metadata response contained 725 instruments. Each locked
instrument had exactly one row with `state=0`, `futureType=1`,
`settleCoin=USDT`, and `apiAllowed=true`:

| Symbol | Display name | Contract size | Price scale | Volume scale | State |
| --- | --- | ---: | ---: | ---: | --- |
| `BTC_USDT` | `BTC_USDT PERPETUAL` | `0.0001` | 1 | 0 | active/API allowed |
| `ETH_USDT` | `ETH_USDT PERPETUAL` | `0.01` | 2 | 0 | active/API allowed |

These facts confirm active USDT-settled perpetual contracts, not permission to
trade them.

## Futures historical semantics

The audit used:

```text
GET https://futures.ourbit.com/api/v1/contract/kline/{symbol}
    ?interval=Min15&start={unix_seconds}&end={unix_seconds}
```

Small boundary probes and the complete traversal established these observed
semantics:

- `start` and `end` use Unix **seconds**. Millisecond inputs returned an empty
  successful data object.
- Both bounds are inclusive when aligned. An unaligned `start` selects the first
  grid open at or after it; an unaligned `end` selects the last grid open at or
  before it.
- Rows are returned in ascending open-time order.
- The observed maximum is 2,001 rows, not 2,000.
- For a range larger than 2,001 rows, the endpoint returns the latest 2,001 rows
  ending at `end`.
- Deterministic traversal must therefore paginate backward with identical lower
  `start`, followed by `next_end = first_returned_open - timeframe`.
- The project half-open end maps to request `end = project_end - timeframe`.
  This eliminates boundary overlap without dropping the final in-range candle.
- Each response has parallel `time`, `open`, `close`, `high`, `low`, `vol`,
  `amount`, `realOpen`, `realClose`, `realHigh`, and `realLow` arrays.
- The endpoint does not return a separate candle close timestamp. The deterministic
  close boundary is `open_time + timeframe`; causal consumers must retain only
  rows whose derived close boundary is no later than their observation time.

No undocumented cursor, offset, or row-count parameter was invented. Retries were
permitted only for transient transport/429/5xx failure with identical parameters;
the accepted audit needed zero retries.

## Acceptance-critical full-window Min15 audit

The validator parsed every numeric field with exact `Decimal` semantics and
checked parallel-array shape, UTC grid alignment, duplicate identity and content,
positive finite OHLC, `high >= max(open,close,low)`,
`low <= min(open,close,high)`, non-negative finite `vol` and `amount`, deterministic
close boundary, ordering, and the exact locked range. It performed no repair,
coercion, interpolation, or source substitution.

| Metric | `BTC_USDT` | `ETH_USDT` |
| --- | ---: | ---: |
| Expected 15m UTC grid count | 70,848 | 70,848 |
| Unique in-range open count | 70,848 | 70,848 |
| Valid unique row count | 70,769 | 70,766 |
| Missing timestamps | 0 | 0 |
| Duplicate opens | 0 | 0 |
| Conflicting duplicates | 0 | 0 |
| Off-grid rows | 0 | 0 |
| Malformed rows | 0 | 0 |
| Invalid/non-positive OHLC rows | **79** | **82** |
| Invalid auxiliary `real*` OHLC rows | 0 | 0 |
| Negative/invalid volume rows | 0 | 0 |
| Schema-length mismatch pages | 0 | 0 |
| Response-order violations | 0 | 0 |
| First valid open | `2024-01-01T00:00:00Z` | `2024-01-01T00:00:00Z` |
| Last valid open | `2026-01-07T23:45:00Z` | `2026-01-07T23:45:00Z` |
| Audit pages | 36 | 36 |
| Retries / unrecovered failures | 0 / 0 | 0 / 0 |

BTC_USDT has 36 `HIGH_RELATION` and 43 `LOW_RELATION` violations. Its sorted
invalid-open list SHA-256 is
`0b21e802877a0b3a67008afdf8d1df7f8b484e44ea3981d0be090553288d0a7e`.
ETH_USDT has 39 `HIGH_RELATION` and 43 `LOW_RELATION` violations. Its sorted
invalid-open list SHA-256 is
`41d6d692af9a5304c53871c1d061860f0777e33ed3238b07b45ea405dcab39bf`.
The complete timestamp lists are in the external result artifact.

Two representative source rows demonstrate the failure without any derived
calculation:

- `BTC_USDT` at `2024-04-03T14:00:00Z`: `open=65843.1`,
  `low=65843.2`; open is below low.
- `ETH_USDT` at `2024-04-02T05:15:00Z`: `open=3358.06`,
  `high=3358.05`; open is above high.

Therefore:

```text
OURBIT_FUTURES_15M_HISTORY = NOT_QUALIFIED
```

## Higher-timeframe consistency gate

The monthly 15m-derived versus native `Min60`/`Hour4` comparisons were **not
performed**. Issue #110 allows them only after both full-window Min15 audits pass.
Running them after 161 invalid source candles would violate the predeclared gate.

No 1h/4h conclusion is claimed and no aggregation rule was tuned.

## Spot public Kline and historical capability

The official Spot API uses `https://api.ourbit.com/api/v3`. Its documentation
defines `GET /api/v3/klines`, the V1-relevant interval names `15m`, `60m`, and
`4h`, optional millisecond `startTime`/`endTime` that must be supplied together,
and `limit` with default 500 and maximum 1,000.

Live public metadata reported both `BTCUSDT` and `ETHUSDT` as `ENABLED`,
Spot-allowed USDT pairs. For each symbol, unbounded requests returned 500 rows at
each of `15m`, `60m`, and `4h`; all inspected rows had the documented eight-field
shape, aligned open time, `close_time = open_time + interval`, valid OHLC, and
non-negative base/quote volume. The final row was still open at the captured
server time, proving that causal consumers must filter on the returned close time.

Bounded `15m` requests for each symbol over
`[2024-01-01T00:00:00Z, 2024-01-01T01:00:00Z)` returned exactly the four expected
opens. Additional boundary probes showed `startTime` inclusive and `endTime`
exclusive by open timestamp. The API documents no opaque cursor; deterministic
historical pagination is available through half-open time windows and `limit`.
The literal interval alias `1h` correctly returned `Invalid interval`; the
official one-hour enum is `60m` and succeeded.

This is a capability check, not a complete Spot-history integrity audit.

## Authenticated trading API viability — documentation only

No endpoint in this section was called. The official Ourbit Postman collections
document the following surface:

| Required future capability | Official documented surface | Result |
| --- | --- | --- |
| Futures create order | `POST /api/v1/private/order/submit` | documented |
| Futures cancellation | order cancel, external-ID cancel, cancel-all endpoints | documented |
| Futures open/history orders | open-orders, history-orders, query and batch-query endpoints | documented |
| Futures positions/account | assets, asset, open/history positions, funding and risk endpoints | documented |
| Futures trade/fill history | order deals and per-order deal details | documented |
| Spot create/cancel order | `POST/DELETE /api/v3/order`, cancel-all | documented |
| Spot open/history/account | open orders, all orders, account information | documented |
| Spot trade history | `GET /api/v3/myTrades` | documented |

The current official [API Creation Guide](https://www.ourbit.com/support/articles/17827791513175)
states that an account may create up to five keys, keys expire after 180 days,
key creation requires at least two of three available security-verification
methods, and read-only scopes separately cover Spot/Futures order and trade
history. The official Postman safety guide explicitly instructs users not to
enable withdrawal permission, confirming that withdrawal authority is separable
and unnecessary for this project. The official API documentation also documents
an IP allowlist with up to ten addresses; because that older page describes a
90-day unbound-key lifetime superseded by the current 180-day guide, lifecycle
details and allowlist behavior must be revalidated before any authenticated
implementation.

The documented protocol is sufficient in principle for a later, separately
approved Futures/Spot adapter. This status does not approve credentials or Live
trading.

## External evidence artifacts

Temporary scripts and raw captures remain outside Git in the isolated Issue #110
evidence root. They are not an accepted corpus.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `ourbit-qualification-result.json` | 62,718 | `7f047378315db70af5d021fab8c0829aca94be8e38c64de8484652f415ce276b` |
| `ourbit_futures_qualification.py` | 28,244 | `80c9ab21d3b1aed81ce0cb04751fffc80a7107bd906fb402514f1aabe71c38c6` |
| 15-file Spot capture bundle | 844,929 | `43a0ada7833c1758ed91114d800c32c4da0b65b50d18a55536d5cb24078f4e71` |

The Spot bundle digest was computed deterministically over lexicographically
sorted file names as `name + NUL + raw bytes`.

## Final gate

The Futures API is publicly reachable on the current official web origin and its
bounded-history semantics permit deterministic complete traversal. Spot public
market data, bounded historical access, and the future private API surface are
also documented and observable. None of those facts overrides the 161 invalid
Futures Min15 OHLC records.

```text
OURBIT_FUTURES_PUBLIC_HISTORY = QUALIFIED
OURBIT_FUTURES_15M_HISTORY = NOT_QUALIFIED
OURBIT_SPOT_PUBLIC_KLINES = QUALIFIED
OURBIT_SPOT_HISTORICAL_BACKFILL = QUALIFIED
OURBIT_TRADING_API_DOCUMENTED = YES
OURBIT_SINGLE_VENUE_CANDIDATE = NOT_QUALIFIED
```

Do not adopt Ourbit into the master-plan authority chain. Do not proceed to an
Ourbit adapter/importer, accepted corpus, strategy study, private API operation,
or deployment without a new approved issue.

**No strategy performance result, accepted corpus, private API operation, API
key, order, Paper/Live/Experience change, or deployment was produced.**
