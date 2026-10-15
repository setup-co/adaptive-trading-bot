# Research V2 isolated admission

**Contract:** `RESEARCH_V2_ISOLATED_ADMISSION_V1` with explicit
`NORMALIZED_BASE_SIGNAL_V1` profile. This is a synthetic-validated, offline
research integration boundary, **not** authorization to evaluate the actual
90-day price history or activate trading. A separate architect-approved study
annex must specify executable SHA, bounded epochs, folds, costs, and outcome
rules before real strategy evaluation. Issue #185 adds the prospective
[exploratory-screen annex](research_v2_exploratory_screen.md) and a separate
externally granted runner; this synthetic seam remains guarded, and no real
execution grant exists in the implementation PR.

The accepted C2 source is
`weex-research-v2-last-18912cf7e7f97d518a74551f`, dataset
`weex-research-v2-last-90d-issue-179-combined-v2`, with manifest SHA-256
`18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`.
The locked adapter revalidates the original, C1, and C2 manifest files, the
C2 plan/report, selected-panel record and source/units note. It accepts only
the ten selected USDT symbols, complete per-member epochs, and exact 15m/1h/4h
grids. It cannot merge, repair, fetch from an API, or cross an explicit gap.
The generic Parquet source selector rejects this qualified namespace.
The current instrument catalog provenance is the saved public V3
`002-exchange_info.raw.json` capture at `2026-10-14T14:16:22.590927Z`
(SHA-256 `1aea6ae66e18e4e092cb349504951363c245ccfff4af42593e580d0c2c128ddd`);
the exploratory selection froze later at its saved `selection_freeze_at`.
Neither timestamp establishes historical contract rules.

Research quantity is **one declared base-asset unit**. Current `contractVal`
is retained as unqualified metadata and is never used to convert quantity,
claim historic contract-count equivalence, or imply actual WEEX fill/margin/
liquidation authority. A V2-only typed context and regime composer admit these
selected symbols; ordinary BTC/ETH V1 context and regime guards are unchanged.
The synthetic-only integration path uses the existing Replay, Feature,
Structure, Regime, unchanged family Strategy, Risk, Safety, Decision Journal,
Entry Intent and all-fill replay/accounting authorities. Fixed research equity
and zero carried exposure are explicit assumptions; historical operational
health is not claimed as observed. All-fill outcomes remain modeled,
unleveraged and pre-funding. No genuine corpus strategy entry point is exposed.

For each declared epoch, context warm-up, decision window and follow-up stay
distinct. A decision must follow the full 200-hour warm-up inside a complete
epoch and stay inside an accepted usable interval. Gaps require fresh state and
cannot be bridged. The data-only CLI
`python -m scripts.research.research_v2_admission preflight --repo .`
verifies identities and reports epochs/usable intervals only; it computes no
signals, fills, PnL or study classification.

The accepted combined data-quality projection remains `PARTIAL_OR_BLOCKED`:
eight individually complete 90-day grids, the original BTC 88-open gap, the
C1 BTW 100-open gap, and a ten-member post-warm-up overlap beginning
`2026-08-02T20:00:00Z`. The overlap is a data fact, not a study window or
qualification of historical contract mechanics. See the separate
[C2 result](research_v2_corpus_issue_181_tail.md) and
[Research V2 charter](research_v2_charter.md).
