# Current project state — 2026-11-15

This is the live handoff. [`project_state.md`](project_state.md) is a required, hash-bound historical
snapshot and must remain byte-for-byte unchanged.

## Issue #241 — proposed BTC/ETH 4h finite-capital Grid protocol

- `BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` version `0.2.0-proposed` is specified as **PROPOSED / NOT EXECUTABLE** only. It is not registered, runtime-eligible, historically evaluated, tuned, or approved for deployment.
- BTCUSDT and ETHUSDT have independent 10,000-USDT research ledgers with identical frozen formulas; cross-asset capital transfer and pooled conclusions are prohibited.
- The accepted Issue #239 v3 authority remains `PRICE_CORPUS_PARTIAL_RECONCILIATION` with manifest SHA-256 `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214` and report SHA-256 `22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`.
- A metadata-only calendar enumerates 356 fixed weekly episodes per symbol: BTC has 338 eligible/18 ineligible and ETH has 345 eligible/11 ineligible under the explicit retained-event proxy assumption. Per-event rate/MARK usability remains a fail-closed execution-integrity check. The calendar reads no candle values and calculates no PnL.
- Retained actual Binance funding events are proposed only as a source-venue research proxy under an explicit archive-completeness assumption. Funding continuity remains `SCHEDULE_PARTIAL_OR_UNCERTAIN`; unusable event/rate/MARK facts integrity-block a future evaluation and are never zero-filled. Known OHLC disagreements (including combined OHLC/volume rows) and required active MARK gaps exclude affected weeks; volume-only disagreements are non-exclusionary flags.
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

## Issue #243 — synthetic-only Grid V1 implementation

- Protocol `0.2.0-proposed` now has a distinct opt-in
  `0.1.0-synthetic-only` research state machine for supplied BTC/ETH facts. It has no dataset
  loader, network client, strategy registry hook, or Paper/Shadow/Live activation path.
- The implementation preserves independent 10,000-USDT symbol ledgers and delegates plan gates,
  safety, deterministic fill costs, position accounting and decision identity to existing project
  authorities through narrow adapters. Equal-time accounting transitions require an explicit
  sequence; every legacy caller retains strict timestamp-only behavior by default.
- Focused synthetic integration covers the accepted boundary, funding, ownership/netting,
  post-only, ambiguity, gap, scheduled flatten, termination, idempotence and BASE/STRESS examples.
  Audit reports retain order/lot provenance, fill/funding/fee decomposition and marked equity.
- Whole-episode status is explicit: observation begins at Monday 00:00 before the first 00:01
  activation, proceeds contiguously through the Sunday 20:01 flatten minute, and otherwise reports
  `PARTIAL_UNRESOLVED` or `INTEGRITY_BLOCKED`. Missing minutes are never treated as flat/no-funding.
- A typed flat-week carry preserves original capital separately from current cash/equity, cumulative
  realized PnL, fees, funding, peak equity and permanent termination. Later synthetic weeks must
  consume the prior weekly carry and cannot reset capital or reopen a terminated symbol.
- Reports label caller-supplied code SHA separately from the verified runtime module hash and bind
  accepted protocol HEAD `58f250a86925a48ed338ef716a621a6594e768b9`, protocol-document SHA-256
  `f0314ed243a2f7784e13d252901802c95b994b59221a2aa67333733be86907fd` and parameter SHA-256
  `960edccc1c40ed63a780734921494646adb45f5532df8ce7872e502dc94147df`.
- This is implementation preparation only. It is not a historical Grid result, an executable
  exchange model, a profitability claim, or authorization for Paper, Shadow, Live, deployment or
  data access. OHLC paths, all-or-unfilled limits and source-venue funding remain modeled proxies.

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

No Grid strategy evaluation occurred. After independent review of the synthetic-only engine and
exact CI identity, a separate issue must freeze the executable/input identities and explicitly
authorize any bounded historical evaluation. No such authorization exists here.
