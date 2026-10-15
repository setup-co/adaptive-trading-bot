# Research V2 charter

**Identity:** `RESEARCH_V2_CHARTER_V1`

**Status:** design specified; implementation and data qualification pending; execution **NOT AUTHORIZED**.

**Dated implementation pointer (2026-10-15):** The original charter text below
records its 2026-10-14 design state. The current-snapshot panel was selected
under #175; C2 data-only evidence and the separate normalized-base admission
and all-fill packages were accepted under #181/#183/#177. Their historical
contract-mechanics limits remain. Issue #185's
[exploratory-screen annex](research_v2_exploratory_screen.md) predeclares a
bounded implementation, calendar and screen, but **does not grant historical
execution**. The older “pending” table remains the original charter finding.

**Authority:** Issue [#171](https://github.com/setup-co/adaptive-trading-bot/issues/171) and Master Plan v1.7. This is not an activated or fully locked study. A later architect-approved activation annex must lock the executable SHA, qualified data manifests, selected instruments, UTC calendar and acceptance rules before any run.

Master Plan v1.8 / Issue #175 separately permits a **current-snapshot exploratory** selection-only route; see [its annex](research_v2_current_snapshot_panel.md). This does not satisfy the historical-as-of requirement below or authorize any retrospective confirmatory claim, data import, strategy study or trading.

## Scope and universe decision

The product targets support for **at least ten assets**. The initial research panel targets **exactly ten** to bound qualification and comparison; ten is not a product ceiling. Research membership does not change today's BTC/ETH runtime/execution allowlists, admit ten simultaneous positions, create ten independent risks, prove any strategy, or confer Paper/Shadow/Live eligibility. Each instrument/strategy must separately pass the full gates. WEEX remains the sole primary source/venue policy. Decisions stay on closed 15m candles with 1h/4h context; qualified WEEX perpetual 5m LAST is historical input used to derive those frames, not a 5m entry strategy. Risk/Safety and Risk-owned bounded leverage remain sovereign; Live stays disabled.

Selection is performed **before strategy counts or outcomes are viewed**:

1. Declare selection instant `T` before the validation/test period. Verify WEEX instrument metadata as known at `T`: each eligible exposure is a distinct supported linear USDT perpetual with understood symbol, quantity and volume units. Exclude stablecoin underlyings, leveraged tokens and duplicate representations of one exposure. Today's ticker spelling is not evidence of historic listing or identity.
2. BTC and ETH are anchors but must qualify by the same evidence and appear once. Rank the remaining eligible exposures by descending median **daily USDT quote turnover** for the 30 complete UTC days immediately preceding `T`; tie-break by canonical instrument identifier. This fixed rule is a design choice, not a performance optimum.
3. Verify comparable quote units. Missing metadata or turnover is **unavailable**, not zero; never multiply an unqualified contract-volume field by price. Retain the entire considered list, metadata as-of proof, 30 daily inputs, medians, ranks and exclusion reasons. If ten cannot qualify, report a blocker. Do not substitute a loser, delisted member, low-signal member or another venue after results, shrink the panel silently, or revise membership without an explicit pre-result amendment.
4. Historical as-of membership and today's-survivor selection are different experiments. If historical metadata/ranking cannot be established, do not fabricate it. A separately proposed current-universe retrospective feasibility panel must be labeled exploratory and selection-biased, never promoted silently to the confirmatory design.

No ten-symbol list, `T`, historical unit mapping, dataset or run calendar is qualified here. The next data-qualification work may examine coverage/liquidity without running strategy outcomes.

## Data, folds and provenance

The existing [locked BTC/ETH LAST corpus](weex_v1_locked_research_corpus.md), ETH excluded epoch and frozen #144/#149/#153/#157/#161 studies remain byte-for-byte and interpretively unchanged. New assets/history need new versioned, SHA-verified manifests with explicit venue, source, contract, timeframe and interval identities; no fresh-API fallback, cross-venue splice, repair, interpolation or silent deletion. Keep development, chronological validation and protected final evaluation distinct. Examined V1 periods are development/diagnostic evidence, not untouched tests. Post-2026-01-08 history is only a *candidate* final set until coverage, integrity and prior use are checked. A new code version or unfamiliar asset does not make an already-examined market period independent.

The activation annex must specify exact UTC decision, causal warm-up and follow-up `[start,end)` intervals per instrument/epoch. Choose common comparison windows from coverage facts before outcomes. Preserve gaps as separate epochs/exclusions with fresh state and warm-up. Purge or segregate a development/validation trade whose outcome follow-up crosses the next protected boundary; its future label must not consume final-period prices while the latter is called untouched. Causal feature warm-up from earlier observations is not the same as importing future outcome labels. No historical run is approved by this charter.

## One reference execution model

Use the existing `CAUSAL_ENTRY_CHRONOLOGY_V2`: sealed decision and entry authorization precede eligibility; a historical entry uses the first eligible 15m execution **open** within the declared range. No hidden epsilon, backward rounding, just-closed-candle OHLC fill or future information. Zero configured latency can admit the newly opening bar at the decision boundary under the documented logical event order; it does not establish instant venue execution. Source: [Master Plan §19](../trading_bot_master_plan.md#19-backtest-engine) and [`entry_source_start`](../../src/adaptive_trading_bot/domain/entry_chronology.py).

The sole first reference exit convention is **completed 15m candle trigger → market request → first eligible subsequent execution open** under shared Backtest pricing/accounting. On a candle touching both stop and target, the protective stop wins. A stop/target is a trigger, never a guaranteed fill price or standing intrabar venue order. No second protective execution engine is specified. Existing strategy and research policy authorities are:

| Family | Existing sealed entry and unchanged research exit policy |
| --- | --- |
| Trend Pullback `1.0.0` | [`TrendPullbackStrategy`](../../src/adaptive_trading_bot/strategies/trend_pullback.py); paired [`HoldOnlyExitStrategy`](../../src/adaptive_trading_bot/strategies/exit_core.py) (`hold-only-exit-v1`) + protective stop versus [`TrendPullbackEma50ExitStrategy`](../../src/adaptive_trading_bot/strategies/trend_pullback_exit.py) (`trend-pullback-ema50-thesis-exit-v1`) + the same stop, via [`PairedTrendPullbackExitReplayRunner`](../../src/adaptive_trading_bot/research/paired_exit_replay.py). The EMA50 candidate branch is the family comparison reference; the hold-only branch remains a paired diagnostic, not a fourth family. |
| Breakout + Retest `1.0.0` | [`BreakoutRetestStrategy`](../../src/adaptive_trading_bot/strategies/breakout_retest.py) and [`BREAKOUT_RETEST_FIXED_2R_V1`](../../src/adaptive_trading_bot/research/breakout_retest_replay.py), target fixed from actual entry fill and sealed stop. |
| Range Mean Reversion composed `2.0.0` | [`RangeMeanReversionStrategy`](../../src/adaptive_trading_bot/strategies/range_mean_reversion.py) with default [`ValidatedRangeDetector`](../../src/adaptive_trading_bot/strategies/range_context.py) and [`RANGE_MEAN_REVERSION_ENTRY_MIDPOINT_V1`](../../src/adaptive_trading_bot/research/range_mean_reversion_replay.py), midpoint frozen from entry audit. The old `1.0.0` screen is frozen and cannot be relabeled as `2.0.0`. |

For each family use the existing Risk → Safety → sealed decision → `EntryOrderIntent` path, common simulator/cost model and `PositionAccountingEngine`; no strategy, threshold, exit-policy or sizing change. Base assumptions are `spread_bps=2`, `slippage_bps=2`, `fee_bps=10`, configured latency `0`. The **only** predeclared combined stress is `4/4/20` bps plus `+1 second` latency. These are fixed model assumptions, **not verified historical WEEX charges**. On a 15m grid, +1 second can move eligibility to the next open; the combined stress does not attribute any difference separately to cost or latency. Neither scenario may be selected after outcomes; no wider grid is authorized.

This first signal question may be expressly **unleveraged and pre-funding**. Missing historical liquidation mechanics do not block that limited question, but do block representative leveraged-perpetual and Live claims. “Net” means net of modeled fee/impact only, not all perpetual costs.

## All-fill economic population — prospective prerequisite

The current three research replays return `ENTRY_FILL_INVALIDATED` before accounting for those actual fills (see [`paired_exit_replay.py`](../../src/adaptive_trading_bot/research/paired_exit_replay.py), [`breakout_retest_replay.py`](../../src/adaptive_trading_bot/research/breakout_retest_replay.py), [`range_mean_reversion_replay.py`](../../src/adaptive_trading_bot/research/range_mean_reversion_replay.py)). #167 changed **offline Paper stop recovery**, not this research behavior. The *new* primary economic population therefore requires a prospective, versioned all-fill path before execution:

- Reconcile all decision observations: no signal, rejected authorization, unfilled entry, filled-invalidated entry, ordinary closure, recovery closure and unresolved exposure. Every legitimate admitted fill keeps actual quantity, price, UTC time, fee and accounted position facts. Do not convert a fill into a free rejection.
- Once an actual fill is known and accounted, stop-invalidating geometry requests **one** bounded opposite-side full close. For Range, actual fill at/past the sealed midpoint is a distinct invalidation reason with an accounted abort/full close—not a successful target and not a movable midpoint. This historical research contract does not expand Paper target authority.
- An abort may be authorized only **after** the fill/accounting event and executes at the first valid execution open strictly later than detection and at/after eligibility, inside the declared source range. No same-observation refund/round trip, invented price, reversal, re-entry or forced end-of-window close. Reuse shared pricing/accounting, sealed intent and original approved-risk provenance.
- If no eligible exit exists, keep the accounted position and entry fee; report remaining quantity/exposure. A valuation, only when supported by validated observed data, is separate and must not be counted as a closed trade. Distinguish gross execution PnL, entry/exit fees, modeled spread/slippage impact and net. Gross PnL on impacted fills is **not** an execution-free signal metric. Any reference-price decomposition holds actual quantities/timestamps fixed and stays diagnostic, never a competing accounting authority.

Old research exclusions were correct for their **declared** population and are not rewritten. New results need new schema/policy provenance and fresh activation; a no-outcome coverage diagnostic is not a substitute for all-fill economic evidence.

## Reports and interpretation

The first comparison concerns only the three existing versioned family policies under the same predeclared base/stress scenarios. No post-result tuning, Fibonacci, optimizer, extra family, alternate exit or rescue subset. Retention/chronology fixes are not proof of edge. For every asset × chronological period × side × relevant regime, report even zero-candidate/insufficient cells. Preserve total decisions, candidate/admission/fill counts, invalidation and closure burdens, unresolved exposure, overlapping/clustered opportunities, modeled fee/impact decomposition, mean/median net approved-risk R, profit factor and concentration. Explicit denominators and unavailable states matter. Ten correlated markets and overlapping opportunities are not ten independent replications.

Independent single-candidate replays are diagnostic, **not a funded portfolio**. Do not sum their PnL into an equity curve, Sharpe or portfolio drawdown. A later funded-account study needs common capital plus concurrent risk/exposure state. Classify reports distinctly as `technically valid`, `insufficient evidence`, `negative evidence`, or `candidate for further validation`. A positive mean, no-cost result or single winner is not approval; no family must trade every asset or win every regime. The activation annex must predeclare numerical sample/dependence sufficiency and cross-market acceptance rules before outcomes, not transplant the prior two-of-three A/B/C gate. No automatic Paper/Live promotion. Synthetic debugging is repeatable; log development revisions; protected final data is not a tuning loop. Frozen/stopped V1 protocols remain closed.

## Bounded implementation prerequisites and activation inputs

| Required fact or behavior | Existing authority | State | Acceptance proof needed |
| --- | --- | --- | --- |
| **A.** Research-only explicit symbol allowlist, as-of ten-member selection, comparable quote turnover and separate SHA-verified WEEX manifests/epochs | BTC/ETH-only [`LockedWeexResearchCorpusStore`](../../src/adaptive_trading_bot/research/weex_historical_corpus.py), Replay/Feature/Risk/Safety/Journal contracts | **Pending**; current corpus and runtime allowlists unchanged | Verified as-of metadata/units and 30 complete UTC-day turnover inputs; full considered/ranked/excluded list; ten qualified distinct exposures; exact 5m LAST grids and derived 15m/1h/4h; locked hashes, gaps, warm-ups; tests that new research symbols cannot enter runtime or bypass Risk/Safety. |
| **B.** Versioned all-fill replay/report population and accounted abort, using shared execution/accounting | Three sealed research replays; `BacktestExecutionSimulator`; `PositionAccountingEngine`; #167 offline Paper recovery only | **Pending**; existing `ENTRY_FILL_INVALIDATED` exclusions remain frozen | Synthetic LONG/SHORT, stop and Range-midpoint invalidations, causal abort/exit-unfilled, fees/exposure/remaining quantity, no fabricated close, full population reconciliation, unchanged legacy report/evidence compatibility and no Paper/runtime activation. |

Only after both packages and review may an activation annex supply: selected ten identifiers and `T` (from A's as-of ranking); exact source/dataset/manifest hashes and validated instrument quantity/turnover units (A's qualification); UTC development/validation/final decision, warm-up and follow-up intervals per epoch (A's coverage, selected before strategy outcomes); exact **future** executable SHA and report/schema identities (reviewed implementations); and numerical sufficiency/dependence/cross-market screen rules (architect predeclaration before outcomes). Until then there is no executable study, qualified panel or approval to run.
