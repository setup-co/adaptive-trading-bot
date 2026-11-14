# Issue #239 corrected v3 qualification evidence

This compact publication records the separate offline v3 qualification over the already retained
Issue #239 raw archives. It contains no new market request and no strategy evaluation. The full
raw and normalized corpus remains at:

`C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset`

The original compact capture/v1/v2 publication remains byte-for-byte unchanged at
`data/research_evidence/issue-239-binance-grid-history-a5ae0048/`.

## Identities

- Starting reviewed PR HEAD: `0eaef0591d07ba76bdedf647399cf569b349b9e8`
- V3 qualification executable/tree:
  `c9331a7e284a9ec876629d999a6100474d4b85ee` /
  `ec826cb266c727675237a2ba2ab31b0e155c8a11`
- Corrected verifier/report executable/tree:
  `038361d5afda5e11c5bfa182252c46371555e0d7` /
  `72a2e9f9e84c1c7b492793d78ef14d9fc3e896d5`
- Plan: `ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace`
- Raw inventory: `e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0`
- V1 predecessor: `d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79`
- V2 predecessor: `c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097`
- V3 manifest: `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`
- V3 report: `22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`

## Actual result

Classification: `PRICE_CORPUS_PARTIAL_RECONCILIATION`.

- Both assets: 3,594,240/3,594,240 LAST minutes, 14,976/14,976 native 4h bars and
  14,976/14,976 canonical timestamp-derived 4h bars.
- BTC reconciliation: 14,961 exact, 12 volume-only, one OHLC-only and two combined mismatches.
- ETH reconciliation: 14,959 exact, 16 volume-only and one OHLC-only mismatch.
- BTC MARK: 3,581,225 rows, 13,015 missing minutes across nine gaps.
- ETH MARK: 3,589,854 rows, 4,386 missing minutes across eleven gaps.
- Funding: 7,488 parsed events per asset; exact schedule continuity remains partial/uncertain. All
  4,605 adjacent offsets per asset are within 47 ms of the reported eight-hour interval; no zero
  events are inserted and the offsets are not relabelled as missing events.

Qualification exited 0 in 158.353 seconds with a 290,021,376-byte peak process-tree working set.
The first verifier attempt exited 1 on an integer/string JSON-key comparison and remains retained.
The corrected read-only verifier exited 0 in 4.34 seconds; report generation exited 0 in 1.021
seconds. The records and wrapper sources preserve both verifier outcomes and command identities.
