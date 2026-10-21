# Breakout baseline-versus-zone comparison annex

**Protocol:** `BREAKOUT_RETEST_ZONE_COMPARISON_V1`
**Protocol SHA-256:**
`bc3b4972f83d20f67967778816674e85e525d495e37e5586995ba13fbcf8e80f`
**Task-plan SHA-256:**
`664cf4189def82392bd86d85879970bd6266c1bbd8217fd7e609620fc66e1a55`
**Status:** frozen preparation; no historical execution grant
**Question:** descriptive effect of one accepted initial-stop hypothesis

## Fixed comparison

The two arms are exactly:

- baseline `breakout_retest` `1.0.0`, unchanged;
- zone `breakout_retest_zone_invalidation` `0.1.0`, policy
  `BREAKOUT_RETEST_ZONE_INVALIDATION_V1`.

Both stateful evaluators consume the complete causal Breakout context stream
independently and chronologically. The zone evaluator may only preserve a
baseline candidate or reject it; it cannot create a replacement signal. Each
arm gets its own RiskManager, Operational Safety decision, journal entry,
entry authorization, quantity, approved risk and admission set. Strategy
identity remains part of each arm's authority. Cross-arm opportunity identity
instead uses the source/input identity, symbol, epoch, decision time, original
breakout pivot identity and side. Stop, size and outcomes are intentionally
excluded from that correspondence key.

The comparison uses the complete retained Issue #187 Breakout populations,
not the admitted-trade export:

- evidence root:
  `data/research_evidence/issue-187-exploratory-screen-316744efa9c4b50a/`;
- artifact index SHA-256:
  `677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`;
- historical executable:
  `316744efa9c4b50aa8d999b3c4d11fd874b4c28c`;
- historical run UUID: `686bd4a1-ffff-4391-8562-f1e4caf7a575`;
- accepted C2 manifest SHA-256:
  `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`.

The ten tasks are ordered BTC, ETH, SOL, XRP, ZEC, ZAMA, BCH, BTW, AVAX and
DOGE against USDT. Each task pins its population, partition and all-fill
authority path, byte size and SHA-256. The calendar retains 6,048 decision
slots per member, 60,480 shared slots, 120,960 arm observations and 241,920
arm/scenario status cells. Decisions span the existing 63-day P1/P2/P3 and
W1-W9 calendar, with no state reset at reporting boundaries and no state
crossing a real epoch gap.

## Execution and accounting authority

BASE remains 2 bps spread, 2 bps slippage, 10 bps fee and zero configured
latency. STRESS remains 4/4/20 bps and one-second configured latency. Fixed
hypothetical equity is 10,000 USDT with the accepted zero-carried-exposure
research assumptions. Every decision retains its exclusive `t + 168h` cutoff;
the latest cutoff is `2026-10-12T00:00:00Z`.

The existing Backtest execution simulator, fixed-2R replay and all-fill
accounting remain sole authorities. Target geometry uses actual entry fill and
the sealed arm stop. A trigger is not a fill price, simultaneous stop/target
touches keep protective-stop precedence, and recovery is causal and strictly
post-fill. Entry-unfilled, ordinary open/closed and recovery open/closed facts,
fees and residual quantities remain evidence. No position is force-closed.

During a separately authorized run, the complete zone Strategy/Risk/Safety
chain is re-derived once per immutable task with the accepted stateful strategy
and fixed Risk/Safety engines. Baseline economic results are
then reconstructed from the exact retained #187 authority rows and labeled
`REUSED_ISSUE_187_BASELINE`; they are not simulated again or described as an
independent replication. Only the zone arm is executable through the narrow
comparison-private bridge. Legacy admission, replay, screen and factorial
entry points continue to reject the zone identity.

## Masks, reporting and interpretation

Each arm freezes its own 168-hour earliest-admission spacing mask before any
outcome is inspected. Blocks do not reset spacing, and unfilled/open selected
opportunities are not replaced. A paired-mask view is only the intersection
of those independently frozen masks.

Reports attribute each decision to the week containing its signal candle
**open**, including exact weekly and P1/P2/P3 boundaries. Directional
Risk/Safety rejections retain their audited LONG/SHORT candidate direction;
genuine NO_SIGNAL direction remains unavailable. Reports retain full arm
denominators, candidates, admissions, common and
arm-only admission identities, complete terminal statuses, admitted trade
details, full/masked outcomes and asset/block/week/side/regime zero cells.
The final projection also rebuilds whole-panel full and arm-specific masked
summaries plus paired full and exact mask-intersection views from retained
individual rows; it never averages asset means or treats unavailable outcomes
as zero.
Paired closed deltas require the same opportunity and both closed authorities;
missing, rejected, unfilled and open identities remain unavailable rather than
receiving zero R. Decimal metrics use a local precision-34 half-even context.
Residual quantities are per asset; no heterogeneous base-quantity sum,
invented mark, equity curve, portfolio claim or significance test is allowed.

The same retained window contains at most 29 underlying candidates. Therefore
this comparison is always post-result, selection-informed and ineligible for
promotion under the unchanged 60-full/30-masked closure gates, even if a
descriptive difference is favorable. Technical completion is not evidence of
edge. Zero admissions and unresolved outcomes remain explicit. There is no
automated rerun, rescue subset, parameter sweep or success threshold. Trend
NEGATIVE_EVIDENCE and the prior Breakout/Range INSUFFICIENT_EVIDENCE statuses
remain unchanged.

## Guarded future route

Preparation exposes only `preflight`, `run` and read-only `verify` commands.
Preflight verifies pinned bytes, manifest/epoch coverage and task inventory
without constructing either strategy or evaluating Risk, Safety or outcomes.
Run requires a fresh external nine-field grant binding the exact executable,
protocol digest, task-plan digest, accepted input identities and fresh UUID
before retained population construction. Old grants, synthetic grants and
unknown evidence fail closed. The one-shot append-only ledger prevents a
STARTED, FAILED or COMPLETED task from running again. Finalization requires all
ten complete tasks; verification reconstructs every stored population/result
and report before accepting the projection. Version-2 population, result and
ledger envelopes bind the run/executable/input/task identity, declared
scenario costs and latency, execution snapshot digest, and exact predecessor
row. Verification resolves every imported baseline outcome against the pinned
#187 bytes. A compact synthetic route exercises the same import, task,
publication, finalization and read-only verifier boundaries without granting a
historical run.

No grant or UUID is issued by this annex. It authorizes no historical
candidate/PnL execution, data acquisition, Paper, Shadow, Live, exchange,
account or deployment action.
