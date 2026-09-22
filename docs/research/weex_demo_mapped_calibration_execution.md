# WEEX Demo mapped-feed calibration execution (#140)

**Execution date:** 2026-09-22

**Execution source code:** `47521f11069da75dfc9787732efb3c5bc90e5b4a`

**Locked feed mapping:** `weex-demo-feed-mapping-5d3ade83b136ca6f04e301e9`

**Final result:** `BLOCKED AFTER AMBIGUOUS OPENING REQUEST`

## Frozen execution result

The three dedicated `ATB_WEEX_DEMO_*` variables were present and non-empty.
Their values were never printed, logged, serialized, copied into evidence, or
committed. Exactly one credential-present execution attempt used the frozen
Issue #140 protocol.

The signed read-only preflight established one balance, zero open positions,
and zero bounded order-history records. Direct public `BTCSUSDT` contract and
feed probes remained unavailable. The exact locked #136 resolver then supplied
current MARK and funding evidence from actual public reference symbol
`BTCUSDT`, while retaining requested Demo symbol `BTCSUSDT`, mapping version,
endpoint/query, and venue timestamps. No `BTCUSDT exchangeInfo` fact was used.

The local runner attempted the one authorized opening through the reviewed POST
path with the existing frozen logical payload:

```text
POST /capi/v3/sim/order
symbol = BTCSUSDT
side / position side = BUY / LONG
type = MARKET
quantity = 0.01
client ID = atb-132-btcsusdt-long-open-v1
TP/SL = absent
```

No trustworthy response was available, so acceptance versus rejection is
unknown and the result is `DEMO_OPEN_RESPONSE_UNAVAILABLE`. The evidence does
not claim that WEEX received the request. The existing
bounded ambiguity path performed exactly three post-request position reads at
its reviewed schedule. No open position was observed, producing
`DEMO_OPEN_NOT_OBSERVED`. The opening was not retried or changed.

Because no position was observed, attribution and every position mechanics
field are unavailable: margin type, auto-margin, leverage, size, `openValue`,
`openFee`, `fundingFee`, `marginSize`, `isolatedMargin`, unrealized PnL, and
`liquidatePrice`. No comparison was eligible, so there are no
MATCH/MISMATCH/UNRESOLVED deltas. Cleanup was neither authorized nor required
without an observed LONG; no cleanup POST was sent.

The immutable result contains exactly:

```text
DEMO_CONTRACT_FACTS_UNAVAILABLE
DEMO_MARK_QUALIFIED
DEMO_OPEN_RESPONSE_UNAVAILABLE
DEMO_OPEN_NOT_OBSERVED
DEMO_LONG_CALIBRATION_BLOCKED
```

The corrected evidence schema preserves the request attempt separately from a
normal response-backed source observation:

```text
method / endpoint = POST /capi/v3/sim/order
phase / client ID = OPEN / atb-132-btcsusdt-long-open-v1
request-body SHA-256 = ce0019db0d6af534b52849f0ee11b4ba489879b0fc124ef2375f2f1d04d4f178
response available = false
venue receipt = UNKNOWN
venue acceptance = UNKNOWN
```

No response digest, order ID, acceptance result, submission timestamp,
credential, signature, authentication timestamp, or venue receipt was invented.

## Mapped public evidence

```text
requested Demo symbol = BTCSUSDT
actual source symbol = BTCUSDT
mapping = weex-demo-feed-mapping-5d3ade83b136ca6f04e301e9

MARK = 86396.2
MARK venue time = 2026-09-22T17:15:12.590000Z
MARK endpoint = GET /capi/v3/market/symbolPrice
MARK query = priceType=MARK, symbol=BTCUSDT

premium MARK = 86389.4
index = 86428.71625
last funding rate = 0.00002874
forecast funding rate = 0.00001152
next funding time = 2026-09-23T00:00:00Z
funding cycle = 480 minutes
premium venue time = 2026-09-22T17:15:11.512000Z
premium endpoint = GET /capi/v3/market/premiumIndex
premium query = symbol=BTCUSDT
```

These facts authorize current feed provenance only. They do not authorize
contract value, quantity units, precision, fees, leverage, margin, MMR,
liquidation, fill/slippage equivalence, historical authority, or general
Demo/production equivalence.

## Locked evidence

```text
schema = weex-demo-long-calibration-v3
protocol = issue-132-long-v1
bundle_version = weex-demo-calibration-08376b6b937422f89c4b4aab
captured_at = 2026-09-22T17:15:07.602108Z
completed_at = 2026-09-22T17:15:21.881460Z
source_code_commit (execution source) = 47521f11069da75dfc9787732efb3c5bc90e5b4a
derivation kind = EVIDENCE_ONLY_CORRECTION
derived from bundle = weex-demo-calibration-31f710cf17bb784da2f3ecad
derived from manifest SHA-256 = 2045f1bcf95dfcfa37ad8eb1baa881f39b42d1965368d7d8be52608933fb2815
bundle.json bytes = 4342
bundle.json SHA-256 = 39211ed64a17bd53b0ee6d03a78825c98301ed3d94f64d2ae467c77a000e74c3
manifest.json SHA-256 = 189f8a20747a04a022a96807d6d2d79a192f671a9068a72a34bdcc3a09270ecc
```

The artifact stores no credential, signature, authentication timestamp, raw
account identifier, or raw venue order identifier. It remains Demo-only and
claims neither production equivalence nor historical authority.

The v3 artifact was not produced by the original execution code. It is an
explicit post-execution evidence-only derivation from exact locked v2 bundle
`weex-demo-calibration-31f710cf17bb784da2f3ecad` and its registered manifest
hash. That v2 predecessor remains byte-identical and readable. Every
execution-time fact is identical between predecessor and derivation; only
schema/bundle/seal identity, ambiguous-attempt evidence, and typed derivation
metadata differ.

## Scientific disposition

The feed blocker is resolved, but this attempt produced no observable position
mechanics. Contract/quantity semantics, fees, leverage, margin, MMR,
liquidation, PnL identity, fill behavior, and Demo/production equivalence all
remain unqualified. The evidence is insufficient to implement representative
leveraged Backtest/Replay mechanics. Issue #118 remains permanently stopped.
