# WEEX Demo `BTCSUSDT` feed mapping qualification (#136)

**Retrieval/capture time:** `2026-09-22T00:19:06.260446Z`

**Producing code:** `8d6cfa63023b0b41354ed60ab2eda796e49b6a7f`

**Final result:** `QUALIFIED — CURRENT FEED REFERENCE ONLY`

## Why this qualification exists

Issue #134 stopped before any Demo POST because the public V3 market routes did
not return a direct current MARK for Demo order symbol `BTCSUSDT`. That blocked
result remains immutable and was not rerun. Issue #136 asks only whether current
public `BTCUSDT` feed facts may serve as a narrowly versioned reference for the
Demo instrument; it does not ask whether their contracts or execution mechanics
are equivalent.

The accepted mapping is exactly:

```text
requested Demo instrument = BTCSUSDT
actual public reference symbol = BTCUSDT
scope = current MARK/index/funding feed facts and their timestamps only
```

No string-replacement rule or mapping for any other symbol exists. A direct
valid `BTCSUSDT` public observation remains preferred if WEEX exposes one in the
future. The `BTCUSDT` fallback is available only through the project-owned
resolver constructed from the exact locked evidence bundle below. Provenance
retains both the requested Demo symbol and the actual queried reference symbol.

## Official WEEX evidence

Only the following official sources were retrieved. The bundle stores their
URL, title/type, retrieval time, concise proposition identifiers and SHA-256;
it does not reproduce the articles.

| Official source | Supported narrow proposition | Content SHA-256 |
|---|---|---|
| [Demo Place Order](https://www.weex.com/api-doc/contract/demo/PlaceOrder) | the documented Demo order symbol example is `BTCSUSDT` | `d5b5034c148e7874601457c81eb1cf3412366480453b32e87594ca3296e36724` |
| [Demo rules and limits](https://www.weex.com/learn/articles/weex-demo-trading-50000-usdt-practice-account-rules-and-limits-ewyzdj1ds2vd4lu21nv5trn9) | Demo futures pairs use SUSDT; SUSDT tracks the live USDT-quoted market price | `23c8783dbe230fd3f091a940a5909d13d579987c1f9412b9c9b4a309b02c2f1b` |
| [Demo strategy-testing guide](https://www.weex.com/learn/articles/how-to-test-a-trading-strategy-on-weex-demo-before-going-live-e35zf87o4wbnnsbt2nkmxsau) | Demo uses the same live price and funding feed | `4d2cbd48c10f1f336995bc884291ba4a9a9bfa3be08a556713b279e3e82d0c09` |
| [Get Symbol Price](https://www.weex.com/api-doc/contract/Market_API/GetSymbolPrice) | public V3 exposes current MARK by symbol | `03d684ae4bfbfc420ebbc9d606c8904440e9daccdf35b8852b5e32fa1739b0f1` |
| [Get Current Funding Rate](https://www.weex.com/api-doc/contract/Market_API/GetCurrentFundingRate) | public V3 exposes current mark/index/funding values, timing and cycle | `15a5fa44143dc2d575fbf52296516f2e9e4f3f986c81bd55fa545192a4df1251` |

Together these sources establish the current feed-reference proposition. They
do not establish contract-mechanics equivalence.

## Public runtime confirmation

The capture made exactly two unauthenticated GET observations for `BTCUSDT`.
All economic values were parsed as exact `Decimal` and venue milliseconds were
retained with UTC datetimes.

```text
GET /capi/v3/market/symbolPrice?priceType=MARK&symbol=BTCUSDT
  MARK = 86403.5
  venue time = 2026-09-22T00:19:11.959000Z (1790036351959)
  response SHA-256 = 28e64296cb639c8edade51bb5ef65143e950033cc7c098e23899ed43e6eebc4b

GET /capi/v3/market/premiumIndex?symbol=BTCUSDT
  MARK = 86363.9
  index = 86398.44125
  last funding rate = 0.00007603
  forecast funding rate = 0.00007898
  next funding time = 2026-09-22T08:00:00Z (1790064000000)
  collect cycle = 480 minutes
  venue time = 2026-09-22T00:19:11.673000Z (1790036351673)
  response SHA-256 = 4152899f5e34cdf3be038022d3368886b7339ec6f23909db9c4b50ca72e03735

exact symbolPrice MARK - premiumIndex MARK = 39.6
symbolPrice venue time - premiumIndex venue time = 286 ms
```

The nonzero MARK delta is preserved as a time-separated observation. No
tolerance was invented, equality was not forced, and the runtime snapshot is
not historical authority.

## Qualified statuses and authority

```text
DEMO_FEED_DOCS_QUALIFIED
BTCUSDT_MARK_REFERENCE_AVAILABLE
BTCUSDT_FUNDING_REFERENCE_AVAILABLE
DEMO_MARK_FEED_MAPPING_QUALIFIED
DEMO_FUNDING_FEED_MAPPING_QUALIFIED
```

The resolver may expose only:

- MARK price;
- index price;
- last funding rate;
- forecast funding rate;
- next funding time;
- funding cycle;
- feed timestamps.

The following remain explicitly unqualified and excluded:

- contract value and quantity/lot semantics;
- precision and limits;
- fees;
- leverage;
- initial, isolated or maintenance margin and MMR;
- liquidation price/formula/buffer;
- fill, slippage, order-book or execution-quality equivalence;
- historical authority;
- general Demo/production equivalence.

`BTCUSDT exchangeInfo` is not used to qualify any `BTCSUSDT` contract fact.
`DEMO_CONTRACT_FACTS_UNAVAILABLE` from #134 remains truthful.

## Immutable evidence identity

```text
schema = weex-demo-feed-mapping-v1
bundle_version = weex-demo-feed-mapping-5d3ade83b136ca6f04e301e9
bundle.json bytes = 3945
bundle.json SHA-256 = 74217436da6bb72a33da3c813732851b3b0b2bcbb4c1993033d3c426140574ee
manifest.json SHA-256 = 46a48e421d81c05d6ed59c106651bfa5fc8bb30b548d804a8a80ea2fb9c273c0
```

The network-free reader requires this exact reviewed version and manifest hash,
then verifies artifact size/hash, source commit, capture time, semantic
consistency and integrity seal. It has no API fallback. Tampering and unknown
versions fail closed.

## Safety and next gate

The capture used no credential, private endpoint, Demo/live order, POST,
leverage or margin mutation. It did not run #134 or any strategy study. The
mapping alone does not authorize another Demo order attempt; that requires a
separate architect-defined issue. Historical MMR/liquidation replay and all
other representative leveraged mechanics remain unqualified.
