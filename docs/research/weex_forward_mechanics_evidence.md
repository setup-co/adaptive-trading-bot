# WEEX V3 forward mechanics evidence (#130)

**Retrieval date:** 2026-09-21  
**Producing code:** `bd811c77d4e2f589b773cde2324848ef2fc06cd3`  
**Scope:** one small current/public capture and a research-only, read-only Demo
evidence contract. No order, leverage change, margin-mode change, transfer,
strategy study, leveraged accounting, or deployment was performed.

## Decision

| Gate | Status | Evidence |
| --- | --- | --- |
| Current public V3 contract/MARK/funding facts | `PUBLIC_CURRENT_EVIDENCE_QUALIFIED` | BTCUSDT and ETHUSDT captured once through reviewed public GET routes |
| Demo schema/client boundary | implemented and synthetically tested | exact three-route signed GET allowlist |
| Runtime Demo evidence | `DEMO_CREDENTIALS_UNAVAILABLE` | no dedicated `ATB_WEEX_DEMO_*` credential existed locally; no signed request was made |
| Demo-to-production symbol mapping | `DEMO_SYMBOL_MAPPING_UNRESOLVED` | no authority proves that documented `BTCSUSDT`/`SUSDT` mechanics equal production `BTCUSDT`/`USDT` |
| Controlled forward calibration | `FORWARD_CALIBRATION_BLOCKED` | no venue-observed eligible isolated Demo position and unresolved symbol mapping |

This is the required truthful partial result. It does not authorize a Demo
order or make current facts historical authority.

## Why historical leveraged replay remains blocked

Issue #128 established only partial locked MARK and funding histories and could
not establish a complete causal BTC/ETH MMR schedule. Exact historical
liquidation replay therefore remains `NOT_QUALIFIED`, and representative
historical leveraged research remains `BLOCKED`. Current exchange information
or a future Demo observation cannot be back-projected into those gaps.

The existing locked inputs remain byte-for-byte separate and unchanged:

- LAST `weex-v1-5m-last-a629a29b580b2e103adba918`;
- MARK `weex-v1-5m-mark-97c879a06fc900676dc5c339`;
- funding `weex-v1-funding-87c1f6e88bd64d96c015fe39`.

Demo is the preferred next evidence path because it can expose venue-reported
isolated-margin facts without risking real capital. It is calibration evidence,
not Risk, leverage, liquidation, accounting, or trading authority.

## Reviewed official V3 boundary

The following routes are the collector's complete network capability:

| Class | Allowed method and path | Captured facts |
| --- | --- | --- |
| Public | `GET /capi/v3/market/exchangeInfo` | current BTC/ETH contract, precision, quantity, public fee and leverage bounds |
| Public | `GET /capi/v3/market/premiumIndex` | MARK/index, last/forecast funding, next funding time, cycle and venue time |
| Public | `GET /capi/v3/market/symbolPrice?priceType=MARK` | current MARK and venue time |
| Public | `GET /capi/v3/market/markPriceKlines` | optional bounded current MARK-kline snapshot only |
| Demo USER_DATA | `GET /capi/v3/sim/balance` | sanitized balance facts |
| Demo USER_DATA | `GET /capi/v3/sim/position/allPosition` | sanitized position/mechanics facts |
| Demo USER_DATA | `GET /capi/v3/sim/order/history` | sanitized historical order facts |

Official documentation records the V3 Demo endpoints as introduced on
2026-04-09. The reviewed references are:

- [V3 API changelog](https://www.weex.com/api-doc/contract/changelog)
- [Demo account balance](https://www.weex.com/api-doc/contract/demo/GetAccountBalance)
- [Demo positions](https://www.weex.com/api-doc/contract/demo/GetAllPositions)
- [Demo order history](https://www.weex.com/api-doc/contract/demo/GetOrderHistory)
- [Current contract information](https://www.weex.com/api-doc/contract/Market_API/GetExchangeInfo)
- [Current funding state](https://www.weex.com/api-doc/contract/Market_API/GetCurrentFundingRate)
- [Current symbol price](https://www.weex.com/api-doc/contract/Market_API/GetSymbolPrice)
- [MARK-price klines](https://www.weex.com/api-doc/contract/Market_API/GetMarkPriceKlines)

Retrieval semantics and payloads can change; the bundle records response
digests and a point-in-time timestamp rather than claiming continuing or
historical applicability.

## Structural secret and endpoint safety

`WeexPublicCurrentClient` and `WeexDemoReadOnlyClient` depend on a transport
whose only operation is `get`. Before transport invocation, each client rejects
any non-`GET` method and any path not in its exact allowlist. The signing helper
also accepts only the three reviewed Demo GET paths. Consequently live order
routes, `POST /capi/v3/sim/order`, live leverage/margin-mode changes, transfers,
and arbitrary signed paths fail before network I/O.

The shared real transport disables redirect following. Any HTTP 30x response
fails with a sanitized typed error before a redirected request is constructed or
sent, so signed headers cannot cross to an unreviewed path or host.

Demo credentials are a separate runtime-only `WeexDemoCredentials` value read
only from `ATB_WEEX_DEMO_API_KEY`, `ATB_WEEX_DEMO_SECRET_KEY`, and
`ATB_WEEX_DEMO_PASSPHRASE`. Partial configuration fails closed. Secret string
and representation forms are redacted, and credentials are absent from all
bundle dataclasses, canonical serialization, logs, exceptions, response hashes,
fixtures, and frontend code. CI uses fake credentials only.

All economic values enter the model as exact `Decimal`; binary floats,
non-finite values, malformed timestamps, inconsistent OHLC, inconsistent
position quantities, and invalid eligibility claims fail closed. Venue integer
milliseconds are retained beside normalized UTC values. Raw position and order
identifiers are not stored; only domain-separated SHA-256 evidence identifiers
are retained when needed.

## Immutable evidence bundle

```text
schema = weex-v3-forward-evidence-v1
bundle_version = weex-v3-forward-b88af4b81510850232656be7
captured_at = 2026-09-21T17:50:33.679000Z
source_code_commit = bd811c77d4e2f589b773cde2324848ef2fc06cd3
bundle.json rows/documents = 1
bundle.json bytes = 4985
bundle.json sha256 = 68d12153932fe7b909f76189690c4ce7b794cffbe94154d5035f06f022631818
manifest.json sha256 = a344bf9cf726b8b68070637bca1c7971eaf248bf3e56585e4b5acd712b9088ca
```

The capture contains two contracts, two premium-index observations, two current
MARK observations, and two 5m MARK rows per symbol. It contains zero Demo
balances, positions, or orders because credentials were unavailable. The
exchange-information response did not expose `apiMakerFeeRate` or
`apiTakerFeeRate`; those fields are explicitly `null`, while current public
maker/taker values are retained as exact strings.

The bounded MARK-kline rows are point-in-time observations and may include the
currently forming candle whose interval closes after `captured_at`. They are not
finalized historical candle evidence.

The canonical JSON seal covers the complete sanitized bundle. The manifest
pins its SHA-256 and byte size. `LockedWeexForwardEvidenceBundle` accepts only a
review-owned bundle/version/manifest tuple, revalidates the seal and artifact on
every read, and has no network capability or fallback.

## Demo eligibility and mapping

A later observed position is calibration-eligible only when the payload itself
proves `marginType == ISOLATED`, auto-margin top-up is false, leverage is an
explicit positive integer, core quantity/open-value facts are positive, and
cumulative facts and chronology are internally consistent. Cross-margin and
auto-margin positions remain recorded only as typed rejected evidence. No
field is normalized into eligibility.

The official examples' `BTCSUSDT` and `SUSDT` names do not establish production
contract identity. This issue therefore preserves the mapping as unresolved and
does not compare Demo facts with the production BTCUSDT/ETHUSDT snapshot.

## Next controlled calibration protocol (not executed)

A separately authorized issue may predeclare a minimal Demo-only experiment.
For each eligible isolated position, it must compare deterministic project
candidates with venue-reported effective notional, initial/position margin,
MARK unrealized PnL, funding transfer, liquidation price/distance, fee terms,
and survival state. Every comparison must retain the exact bundle, code,
configuration and source provenance. Any disagreement is
`CALIBRATION_MISMATCH`; it is not permission to tune a formula to one sample.

Such an issue must first resolve the Demo/production contract mapping and may
then explicitly authorize narrowly controlled Demo orders. This issue grants no
such authority.
