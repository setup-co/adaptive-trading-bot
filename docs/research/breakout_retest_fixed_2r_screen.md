# Breakout + Retest V1 fixed-2R signal-edge screen — predeclaration

Issue #147 is implementation-only. No real locked-WEEX A/B/C Breakout + Retest study was run in this issue. Architect review and a separate exact-SHA activation are required before one-time execution.

The entry authority is the existing `breakout_retest` strategy version `1.0.0` with the unchanged default `BreakoutRetestConfiguration`. The causal historical path is Replay → Feature → Structure → Technical/MTF Regime → stateful Strategy → Risk → Safety → DecisionJournal. Every 15m decision, including NO_TRADE and blocked candidates, remains in the sealed corpus. Research admission uses fixed independent 10000 USDT equity, zero carried positions, and the declared research Safety assumptions; it does not claim historical account/operational health was observed.

The only exit policy is `BREAKOUT_RETEST_FIXED_2R_V1`. After an authorized simulator entry fill, an invalid stop geometry produces `ENTRY_FILL_INVALIDATED` without a position or PnL. Otherwise, LONG distance is actual fill minus sealed stop and target is actual fill plus twice that distance; SHORT distance is sealed stop minus actual fill and target is actual fill minus twice that distance. Closed 15m candle low/high trigger stop/target. A same-bar double touch selects the protective stop. The trigger is observed at candle close, then a market request goes through the unchanged Backtest simulator and its configured costs/latency; it is never priced at the stop or target. Unfilled exits and untriggered positions remain unresolved. Existing PositionAccountingEngine alone supplies realized gross PnL, fees, and net PnL; sealed approved risk is the denominator for exact Decimal net R.

The sealed report gives candidate/unfilled/invalidated/replayed/closed/unresolved counts; closure denominator is replayed entries, while economic outcomes are only actually closed positions. It records stop/target/same-bar/exit-unfilled counts, win/breakeven/loss counts, exact R-space totals/mean/median/min/max, average winning/losing R, explicit finite/infinite/unavailable profit factor, chronological losing streak, side and UTC entry-month strata, and deterministic overlap components. Raw candidates are not independent; total R is descriptive, not portfolio PnL. No funding, leverage, margin, MARK/MMR, or liquidation model is implied.

An individual run is evaluable only with at least 10 closed outcomes and closed/replayed rate at least 0.80. POSITIVE requires mean R > 0, profit factor > 1, median R >= 0. NEGATIVE requires mean R <= 0 and profit factor <= 1. Other evaluable runs are MIXED; unevaluable runs are INSUFFICIENT. Across exact A/B/C: POSITIVE requires all evaluable, at least two POSITIVE and no NEGATIVE; NEGATIVE requires all evaluable and at least two NEGATIVE; otherwise MIXED if all evaluable, else INSUFFICIENT.

The future-only predeclared run gate requires `LockedWeexResearchCorpusStore`, source `weex-v3-last-e5b0c4035a19d5a80ca7`, dataset `weex-v1-5m-last-a629a29b580b2e103adba918`, manifest SHA-256 `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`, 168h follow-up, equity 10000 USDT, spread/slippage/fee 2/2/10 bps, and default simulator latency. No network or generic Parquet fallback is authorized.

| Unit | Symbol | Decision window `[start,end)` UTC | Maximum follow-up boundary UTC |
| --- | --- | --- | --- |
| A | BTC/USDT | 2024-07-01 → 2026-01-01 | 2026-01-08 |
| B | ETH/USDT | 2024-07-01 → 2024-12-25 | 2025-01-01 |
| C | ETH/USDT | 2025-04-02 → 2026-01-01 | 2026-01-08 |

The ETH exclusion `[2025-01-01, 2025-04-02)` is never bridged. This screen has no Paper, Shadow, Live, Experience, Risk, Safety, or order authority and cannot establish future profitability or representative leveraged-perpetual economics.
