# Trend Pullback V1 signal-edge screen — implementation protocol

Issue #144 adds a deterministic, research-only economic projection over a
sealed `TrendPullbackExitRobustnessReport`. This implementation does **not** run
the real locked-corpus study. A separate architect activation must freeze the
implementation SHA before any of Runs A, B or C begins. Issue #118 stays
permanently stopped and is not resumed or reinterpreted.

The predeclared historical authority is WEEX Futures 5m LAST source
`weex-v3-last-e5b0c4035a19d5a80ca7`, locked dataset
`weex-v1-5m-last-a629a29b580b2e103adba918`, manifest SHA-256
`98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
Future execution must select `LockedWeexResearchCorpusStore` with that exact
version and verify its manifest/artifact tree. The frozen projection entry
point requires this store type and the exact accepted dataset identity;
neither the report nor its source ID alone proves a dataset version. The
generator and paired runner must use this same store in the activated study.
No network or generic Parquet fallback is authorized.

The frozen runs, in order, are BTC/USDT `[2024-07-01, 2026-01-01)`, ETH/USDT
`[2024-07-01, 2024-12-25)`, and ETH/USDT `[2025-04-02, 2026-01-01)`, all UTC.
Each has a 168-hour follow-up, 10,000 USDT independent research equity,
2-bps spread, 2-bps slippage, 10-bps fee, and the existing default execution
latency. The ETH interval `[2025-01-01, 2025-04-02)` is excluded and must not
be read, bridged or used to continue a position. The existing default Trend
Pullback strategy and Risk/Safety assumptions remain unchanged.

Only a `REPLAYED` observation with a closed EMA50 candidate branch enters the
absolute economic denominator. Its net R is the authoritative candidate
realized net PnL divided by the sealed authorized entry intent's finite,
positive approved risk quote. ENTRY_UNFILLED, ENTRY_FILL_INVALIDATED and
unresolved branches have no invented outcome. All arithmetic uses a local
34-digit Decimal context; no fill, fee, PnL, risk or paired-delta formula is
recomputed. The existing paired candidate-minus-baseline delta summary is
retained separately.

Each run is economically evaluable only with at least 10 closed candidate
outcomes and a candidate closed rate of at least 0.80. A run is POSITIVE when
mean net R > 0, R-space profit factor > 1, and median net R >= 0; NEGATIVE
when mean net R <= 0 and profit factor <= 1; otherwise MIXED. Any unevaluable
run makes the screen INSUFFICIENT. Among three evaluable runs, at least two
POSITIVE and none NEGATIVE makes the screen POSITIVE; at least two NEGATIVE
makes it NEGATIVE; all other combinations are MIXED. Infinite/unavailable
profit-factor states remain explicit rather than using a fabricated number.

The screen is historical signal economics under qualified LAST prices and
declared transaction-cost assumptions only. Raw candidates and overlapping
positions are not independent observations; total realized net R is not
portfolio PnL or return. This is not significance proof, future-profitability
proof, leveraged performance, historical liquidation realism, funding-complete
analysis, or Paper/Shadow/Live trading authority. Positive status permits only
the next predeclared validation stage.
