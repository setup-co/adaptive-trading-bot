# Issue #237 Raschke/Fibonacci composite completion

Status: `COMPLETE`  
Classification: `SPARSE`  
Promotion: `NO_PROMOTION`

This is a composite completion of the same frozen comparison. It reuses all 174 immutable BTC
scenario outcomes from executable `c956bfb41659404f88a87f23022b4b47cb8359e6` and processes only
the previously unprocessed ETH member with completion executable
`8d38df1bf3f8749a3392a3e64d277bd3d981230a`. It is not a second BTC simulation or a new
independent sample.

## Completion identities and resources

- Completion protocol: `BTC_ETH_RASCHKE_PULLBACK_FIB_COMPLETION_V1`
- Protocol SHA-256: `78fcd7c8e08694475a7508279fe7367941b9df8313afdc37eb4e39c9a4f6f23c`
- Completion run UUID: `af5af71c-6465-48b4-a416-9ae42a0cf0d8`
- Exit: `0`; elapsed: `60.3875622` seconds
- Peak owned-process-tree working set: `269,975,552` bytes; timeout/memory enforcement: false/false
- Rows: 174 `REUSED_BTC_PARENT_OUTCOME` + 218 `NEW_ETH_OUTCOME` = 392
- Composite rows SHA-256: `63e632ef4d73ffbb94559c00bc11876ba66c15f738276998efdd17ae19f59c3a`
- Projection SHA-256: `0c4bef6e97a6042db3f4627bc3197fc6e1d3935d5a739dbf82bfe46bcdce0103`

The separately completed BTC validation is retained as
`saved-btc-validation.json` with SHA-256
`2f94985ab2a58ca0a9c9c138521e1b3545b37d97d6cff49adfb653b554f53bdb`; it was not rerun.

## Economic result

All displayed rows are naturally closed; there are no unfilled or endpoint-open rows. `PF` is the
terminal quote-return profit factor. Exact Decimal values, R-based/quote-based natural PF,
holding, blocks, selection comparisons, capacity and concentration are authoritative in
`output/projection.json`.

| Asset | Arm | Scenario | Admissions | Recovery | Gross quote | Fees | Net quote | Mean net R | PF |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | REFERENCE | BASE | 53 | 0 | -289.2309 | 668.0515 | -957.2824 | -1.1473 | 0.1552 |
| BTC | REFERENCE | STRESS | 53 | 3 | -591.4565 | 1336.1403 | -1927.5968 | -2.2573 | 0.0254 |
| BTC | FIB_ZONE | BASE | 17 | 0 | -88.7237 | 233.3011 | -322.0248 | -1.3018 | 0.1263 |
| BTC | FIB_ZONE | STRESS | 17 | 1 | -167.7616 | 466.6776 | -634.4392 | -2.5601 | 0.0044 |
| BTC | ROUND_ZONE | BASE | 17 | 0 | -88.7237 | 233.3011 | -322.0248 | -1.3018 | 0.1263 |
| BTC | ROUND_ZONE | STRESS | 17 | 1 | -167.7616 | 466.6776 | -634.4392 | -2.5601 | 0.0044 |
| ETH | REFERENCE | BASE | 59 | 0 | 12.0281 | 683.0105 | -670.9824 | -0.4811 | 0.4034 |
| ETH | REFERENCE | STRESS | 59 | 2 | -356.0477 | 1365.9960 | -1722.0437 | -1.2829 | 0.0711 |
| ETH | FIB_ZONE | BASE | 26 | 0 | 25.5104 | 289.2882 | -263.7778 | -0.4295 | 0.4289 |
| ETH | FIB_ZONE | STRESS | 26 | 2 | -118.1200 | 578.6160 | -696.7360 | -1.1197 | 0.0701 |
| ETH | ROUND_ZONE | BASE | 24 | 0 | 26.3013 | 268.2844 | -241.9831 | -0.4289 | 0.4262 |
| ETH | ROUND_ZONE | STRESS | 24 | 2 | -102.7489 | 536.6191 | -639.3680 | -1.1174 | 0.0756 |

The filtered ETH arms were less negative than REFERENCE but remained negative after commissions in
both cost scenarios. BTC FIB and ROUND selected the same retained population and economics. Every
cell is negative on mean net R with PF below one. Removing the largest winner leaves every cell
negative. This is `SPARSE / NO_PROMOTION`, not evidence of a Fibonacci-specific edge.

## Funnel and limitations

ETH recorded 17,280 observation slots per arm, 62 underlying opportunities, and admissions of
59 REFERENCE, 26 FIB_ZONE and 24 ROUND_ZONE. BTC admissions are exactly retained at 53/17/17 per
scenario, but the final BTC observation/arming/confirmation/rejection/Risk/Safety funnel was never
committed by the failed parent invocation and remains explicitly `NOT_RETAINED`; the stale heartbeat
is not used as a final summary.

Economics are PRE_FUNDING. MARK/liquidation and representative leveraged-account mechanics remain
unqualified. This exploratory history was already inspected, nearby rows/arms/scenarios are not
independent confirmations, and the capacity diagnostic is not a funded or compounding portfolio.
No strategy, threshold, cost, exit, data or runtime boundary changed.
