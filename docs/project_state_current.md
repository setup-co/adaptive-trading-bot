# Current project state — 2026-11-14

This is the live handoff. [`project_state.md`](project_state.md) is a required, hash-bound historical
snapshot and must remain byte-for-byte unchanged.

## Issue #241 — proposed BTC/ETH 4h finite-capital Grid protocol

- `BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` is specified as **PROPOSED / NOT EXECUTABLE** only. It is not registered, runtime-eligible, historically evaluated, tuned, or approved for deployment.
- BTCUSDT and ETHUSDT have independent 10,000-USDT research ledgers with identical frozen formulas; cross-asset capital transfer and pooled conclusions are prohibited.
- The accepted Issue #239 v3 authority remains `PRICE_CORPUS_PARTIAL_RECONCILIATION` with manifest SHA-256 `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214` and report SHA-256 `22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`.
- A metadata-only calendar enumerates 356 fixed weekly episodes per symbol: BTC has 338 eligible/18 ineligible and ETH has 345 eligible/11 ineligible before any future execution-level funding decision. It reads no candle values and calculates no PnL.
- Funding continuity remains explicitly partial/uncertain across the accepted interval. Known price disagreements and required active MARK gaps exclude affected weeks; volume-only disagreements are non-exclusionary flags.
- Any historical execution requires a later exact-SHA grant and narrow standing-ladder adapters through existing Strategy, Risk, Safety, authorization, accounting and journal authorities.

## Verified starting state

- GitHub `main`: `2e787f3d53599bdb589e23a5d211c466b4577dd0`, tree
  `83eb4fa886e05cff22a44b5e6bb08c835f2c85a5`.
- PR #236 remains open at `d1772874cc1414a192e7b94fc296b27cf04993dc`.
- PR #238 remains open at `62a0dfe8c821e4a24b07b760be3b9d7d49106b3d`, stacked on PR #236's
  branch. Neither is part of Issue #239.
- The accepted 196-day WEEX research price corpus is complete for its fixed history-screened panel;
  it remains predecessor evidence and does not become the new Grid authority.
- Recent fixed screens remain negative, sparse or insufficient. No sustainable edge or Live
  readiness has been established.

## Approved direction

The owner selected BTC/ETH Grid research with closed-4h decisions and separately qualified
multi-year external history. WEEX remains the intended future execution venue; Risk and Safety
remain sovereign; Live stays disabled. Existing 15m/runtime strategies and all predecessor evidence
remain preserved.

## Active task and next gate

Issue #239 completed its bounded acquisition: all 1,312 fixed resources succeeded on their first
attempt. Corrected offline v3 qualification preserves every v1/v2 byte and establishes complete
BTC/ETH trade-price 1m, native 4h and canonical timestamp-derived 4h grids. Native reconciliation
remains partial with 15 BTC and 17 ETH exact per-bar discrepancies; MARK remains partial and funding
events are parsed but exact schedule continuity is partial/uncertain because retained timestamps
carry millisecond offsets. The combined classification is `PRICE_CORPUS_PARTIAL_RECONCILIATION`.
The verified v3 manifest is
`9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`. See
[`btc_eth_grid_4h_external_history_result.md`](research/btc_eth_grid_4h_external_history_result.md).
The architect accepted and froze that v3 data result. The remaining prospective capture-tool
correction now fails closed before network continuation when prior received-byte usage is
unresolved and durably charges a received block even when it crosses the payload cap; it was
validated with local fake transports only. No accepted capture or qualification evidence changed.

No strategy evaluation occurred. After data review, a separate issue must freeze the specific
Grid model, finite capital/risk/inventory rules, 4h decision and 1m standing-order chronology,
complete costs/funding, terminal valuation and chronological evaluation partitions.
