# WEEX Demo LONG mechanics calibration (#132)

**Execution date:** 2026-09-21  
**Baseline:** `5fd26536c8c886bcdc9980883cf6bf08f5a8db53`  
**Producing code:** `43c508aff3aeaa256d80b6bb4b5bf0c8605bd12c`  
**Final result:** `BLOCKED`

## Result

No dedicated Demo credential was present in the execution environment. The
three required runtime variables were checked for presence without reading or
printing their values:

```text
ATB_WEEX_DEMO_API_KEY = unavailable
ATB_WEEX_DEMO_SECRET_KEY = unavailable
ATB_WEEX_DEMO_PASSPHRASE = unavailable
```

The run therefore stopped before any network request. No public probe, signed
Demo GET, Demo POST, opening order, position observation, cleanup order, live
request, leverage change, or margin-mode change occurred. The immutable result
contains exactly:

```text
DEMO_CREDENTIALS_UNAVAILABLE
DEMO_LONG_CALIBRATION_BLOCKED
```

Cleanup status is not applicable because no opening order was submitted and no
position existed. Demo-to-production symbol mapping remains unresolved. The
result provides no historical authority and no production-equivalence claim.

## Locked calibration evidence

This bundle is separate from every LAST, MARK, funding, and #130 forward
evidence artifact.

```text
schema = weex-demo-long-calibration-v1
protocol = issue-132-long-v1
bundle_version = weex-demo-calibration-e300b52fe0e43bfe8581ca58
captured_at = 2026-09-21T19:35:31.736523Z
source_code_commit = 43c508aff3aeaa256d80b6bb4b5bf0c8605bd12c
bundle.json bytes = 1274
bundle.json sha256 = 602a5db788274565e32d44df1bda0e845f2a0880a73d40a85d442f03c9d533cb
manifest.json sha256 = 025159eb178a9e036c3fb36b531a2210cc45c5abfb7d66b153477521274285b6
```

The network-free locked reader accepts only the reviewed bundle/version and
manifest hash, verifies artifact size/hash and the bundle integrity seal on
every read, and has no fallback capture path. The blocked bundle contains no
fabricated baseline, public, order, position, comparison, or cleanup evidence.

## Implemented calibration boundary

The research-only writer is deliberately separate from the #130 read-only
client and is not connected to ExchangeAdapter, Risk, Safety, Strategy, Paper,
Shadow, Live, or frontend code. Its only transport operation is POST and its
only allowed path is:

```text
POST /capi/v3/sim/order
```

It accepts no path or arbitrary JSON from callers. The only valid opening is
the predeclared `BTCSUSDT` MARKET BUY/LONG quantity `0.01` with deterministic
client ID and no TP/SL. The only valid cleanup is MARKET SELL/LONG for the exact
size and evidence identity of the observed position. Live order, leverage,
margin-mode, transfer, close-all, arbitrary routes, wrong symbol/type/side,
and changed opening quantity fail before I/O.

Canonical JSON is serialized once; those exact bytes are both signed and sent.
The real transport disables redirects and returns only a generic typed error,
so a 30x cannot cause a second request or disclose signed headers. Only
`ATB_WEEX_DEMO_*` credentials are read, at runtime. Keys, passphrases, secrets,
signatures, authentication timestamps, and raw venue/account identifiers are
excluded from evidence and surfaced errors; venue identifiers are retained
only as domain-separated hashes.

## Protocol retained for a credential-present environment

The runner fail-closes unless the same run establishes a valid balance, no open
Demo positions, bounded order-history/rate-limit safety, and a directly
observed current `BTCSUSDT` MARK from the reviewed public V3 boundary. It never
substitutes `BTCUSDT` facts.

After one accepted opening it performs at most three position GETs at 0, 2,
and 4 seconds and never retries or changes the order. An observed position is
mechanics-eligible only when the existing authoritative Demo position validator
and calibration identity checks prove LONG, ISOLATED, auto-margin off, explicit
integer leverage, valid economic facts, chronology, and exact opening-order
attribution. Rejected samples still proceed to cleanup without changing venue
settings.

Comparisons preserve exact Decimal inputs/deltas and keep `MATCH`, `MISMATCH`,
and `UNRESOLVED` distinct. No liquidation formula is fitted. Demo quantity
semantics remain unqualified, so MARK PnL cannot be manufactured from a future
sample without additional authority.

Cleanup is exactly one position-bound order after at least 65 seconds from the
accepted opening response, followed by at most three position GETs over four
seconds. Failure to observe closure becomes `DEMO_CLEANUP_UNCONFIRMED`; no
alternate cleanup is sent.

Official reviewed references:

- [WEEX Demo place order](https://www.weex.com/api-doc/contract/demo/PlaceOrder)
- [WEEX Demo positions](https://www.weex.com/api-doc/contract/demo/GetAllPositions)
- [WEEX Demo order history](https://www.weex.com/api-doc/contract/demo/GetOrderHistory)
- [WEEX V3 changelog](https://www.weex.com/api-doc/contract/changelog)

## Preserved authorities and blockers

The existing immutable identities remain unchanged:

```text
LAST = weex-v1-5m-last-a629a29b580b2e103adba918
MARK = weex-v1-5m-mark-97c879a06fc900676dc5c339
funding = weex-v1-funding-87c1f6e88bd64d96c015fe39
forward = weex-v3-forward-b88af4b81510850232656be7
```

Historical MMR and liquidation replay remain unqualified, representative
leveraged research remains blocked, and Issue #118 remains permanently stopped.
No primary study or leveraged Backtest/Replay implementation was started.
