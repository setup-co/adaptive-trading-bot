# Issue #142 — read-only WEEX Demo order reconciliation

Execution baseline: clean `main` at `8fc066ab2b5873dae01f460213f6fee2b999052e`.
This reconciles the ambiguous opening attempt preserved in the locked #140 v3
bundle `weex-demo-calibration-08376b6b937422f89c4b4aab` (manifest SHA-256
`189f8a20747a04a022a96807d6d2d79a192f671a9068a72a34bdcc3a09270ecc`).
The original attempted request's client ID is
`atb-132-btcsusdt-long-open-v1`; its request-body SHA-256 remains
`ce0019db0d6af534b52849f0ee11b4ba489879b0fc124ef2375f2f1d04d4f178`.

The #142 capture made exactly one signed Demo
`GET /capi/v3/sim/position/allPosition`, then exactly one signed Demo
`GET /capi/v3/sim/order/history`. That history query used only
`symbol=BTCSUSDT`, `startTime=1790097000000`
(`2026-09-22T17:10:00.000Z`), `endTime=1790097600000`
(`2026-09-22T17:20:00.000Z`), `limit=100`, and `page=0`. The empty first page
ended pagination. No state-changing request occurred.

| Fact | Captured result |
| --- | --- |
| Current-position response | 0 positions; no current `BTCSUSDT` position |
| Position response SHA-256 | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| History pages / rows inspected | 1 / 0 |
| History page-0 response SHA-256 | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| Exact client-ID matches | 0 |
| Terminal classification | `DEMO_AMBIGUOUS_ORDER_NOT_FOUND` |

The finding is precisely: **no venue order-history record was found in the
exact reviewed window**. It does not prove that the earlier request never
reached WEEX or that the HTTP client received a response. The #140 venue
receipt/acceptance remains `UNKNOWN`. No position mechanics, contract quantity,
margin/MMR/liquidation formula, historical policy, or Demo/production
equivalence is qualified by this result.

The immutable #142 artifact is:

```text
bundle version = weex-demo-reconciliation-4a17f9406b037ce84d6ce288
artifact SHA-256 = d23b1e3f9d2e825822ec7b2d5c4ad2afad75d6ca33b1f2640af456a2e79823fb
manifest SHA-256 = 28f2c963fa8624c73b9e00a0f7697f3c703f158495fe06ea7ba98b7707702c61
```

This is the final read-only reconciliation gate for the historical WEEX
leveraged-mechanics evidence path. After architect review, the project should
close that pursuit without another Demo opening attempt. Historical MMR and
liquidation remain unqualified absent independent authoritative historical
data. A separately predeclared signal-edge study may address the economic
question on the qualified immutable WEEX price corpus with realistic costs,
without claiming historically exact leveraged liquidation mechanics.

Official read-only endpoint contracts:

- [WEEX Demo positions](https://www.weex.com/api-doc/contract/demo/GetAllPositions)
- [WEEX Demo order history](https://www.weex.com/api-doc/contract/demo/GetOrderHistory)
