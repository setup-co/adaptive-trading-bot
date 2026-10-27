# Breakout + Retest history-screened 180-day comparison

Protocol: `BREAKOUT_RETEST_HISTORY_SCREENED_180D_COMPARISON_V1`

Status: implementation/predeclaration complete; historical execution not authorized

Protocol SHA-256: `15fd986946e4e4e8ec5774e9ca67ecee5b9a3e245b97fdebd5b5bddf2d723236`

Task-plan SHA-256: `1506b2568f6067d39ccebe3a4093349116cfcc92413dcbb2d961fdad52ecdd49`

This annex binds the accepted history-screened corpus to one later, separately
authorized comparison of the unchanged Breakout 1.0.0 strategy and the opt-in
zone-invalidation 0.1.0 variant. It is selection-informed exploratory research,
not a protected out-of-sample test, promotion decision, runtime activation, or
trading authority. It does not alter or supersede the executed 63-day comparison.

## Locked input

- Root: `data/research_corpus/history-screened-196d-last-continuation-v1/`
- Source/dataset: `weex-history-screened-196d-last-continuation-v1`
- Manifest: `756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d`
- Quality report: `bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66`
- Acquisition plan: `dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f`
- Original publication manifest:
  `199923afac0d51ca2e7a9b2f324dcecaf34b109d56e85694d06780136f8d3623`,
  interpreted with its accepted source-only test correction sidecar.
- Selection manifest / result / corrected reconciliation:
  `9259da6973e5c910912c6da1b69942a3f7a0edbffd583e5b4cd9ea0395301bbb`,
  `3dc81c33d3d12983973c69474e376561524b4dce1ef75723f1117851f88f1c81`,
  and `82faa7c6d78391ea43491ccf3f38b034aa4974b0864614b70ce00ad887ad6edf`.
- Accepted test-correction sidecar:
  `60326eb2794c6eb7546460bcbf12b1fce1cd55128ee7972806fd0cd47f23a1bf`.
- Ordered panel: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, ZECUSDT, ZAMAUSDT,
  BCHUSDT, AVAXUSDT, DOGEUSDT, LINKUSDT.

The binding models one declared base-asset unit, unleveraged and before funding.
Saved member authorities supply identity; current contract metadata is not used
as an historical multiplier. Every consumed Parquet file is hash-checked against
the accepted manifest and checked for its schema, exact grid, epoch, usable
interval, row count, and member identity. The adapter is immutable and has no API
fallback.

## Calendar and task plan

- Context/follow-up: `[2026-04-01T00:00:00Z, 2026-10-14T00:00:00Z)`.
- Signal candle opens: `[2026-04-10T00:00:00Z, 2026-10-07T00:00:00Z)`.
- The April 9 08:00 usable boundary is excluded and precedes the study calendar.
- Each signal decision has its own exclusive `t + 168h` cutoff. Positions are not
  force-closed.
- Each member is one chronological task containing both arms and both scenarios.
  There are 17,280 slots/member, 172,800 panel slots, 345,600 arm observations,
  and 691,200 arm/scenario status cells. These are denominators, not trade counts.
- P1/P2/P3 are three consecutive 60-day bins by signal candle open. W1-W25 are
  seven-day bins anchored at April 10; W26 is October 2-7. State and spacing masks
  do not reset at reporting boundaries.

Both stateful strategies independently consume the same causal stream, including
pre-window state. Baseline is `breakout_retest` 1.0.0. Zone is
`breakout_retest_zone_invalidation` 0.1.0 under
`BREAKOUT_RETEST_ZONE_INVALIDATION_V1`. Each arm independently traverses Strategy,
RiskManager, OperationalSafetyGate, journal and entry authorization. The zone arm
may preserve or reject a baseline opportunity; it cannot invent one. Old #187 or
#203 populations/outcomes are not imported.

## Economic model and evidence

The later authorized run uses unchanged `CAUSAL_ENTRY_CHRONOLOGY_V2`, Backtest
pricing, fixed-2R actual-fill/sealed-stop geometry, PositionAccountingEngine and
all-fill recovery. BASE uses 2/2/10 bps spread/slippage/fee at zero latency.
STRESS uses 4/4/20 bps and one-second latency. Both use 10,000 USDT fictitious
equity and zero carried exposure per independent opportunity. Trigger prices are
not fills; protective-stop precedence and all terminal/open/unfilled states remain
explicit. Fees and unresolved quantities remain evidence.

Before outcomes are inspected, each member/arm selects the earliest authorized
admission and then the next at or after 168 hours. Sides compete, the mask crosses
reporting boundaries, and unfilled/open selected opportunities are not replaced.
BASE and STRESS share an arm mask. Paired-mask analysis uses only the intersection
of the independently frozen arm masks.

Task and panel reports retain:

- complete terminal partitions and explicit denominators;
- candidates, admissions, fills, closures, common and arm-only identities;
- full and arm-specific masked BASE/STRESS economics;
- exact incurred fees and residual base quantities per asset;
- mean/median closed net approved-risk R and R-space profit factor with unavailable
  values kept unavailable;
- recovery burden, sizing, approved risk, actual-fill/stop/target geometry and
  holding duration;
- asset × block/week × side × regime zero cells; and
- paired differences only where the same opportunity has two closed authorities,
  with unmatched, unfilled and open cases separate.

Decimal calculations use a local precision-34, half-even context. Quantities from
different assets are never summed, and asset means are not averaged into a panel
mean.

## Frozen interpretation

Each arm is screened separately. After technical verification it is
`INSUFFICIENT_EVIDENCE` if either scenario has fewer than 60 closed fills, fewer
than 30 masked closures, fewer than five assets with at least three masked
closures, fewer than six weeks with at least three masked closures, or any filled
position unresolved at cutoff. With sufficiency, all four full/masked BASE/STRESS
mean net-R values at or below zero gives `NEGATIVE_EVIDENCE`.

`CANDIDATE_FOR_FURTHER_VALIDATION` requires all four means above zero, the same at
least five assets with three masked closures and positive masked means under both
scenarios, at least six positive STRESS masked weeks with three closures, and
positive leave-one-asset-out STRESS full and masked means for every omission.
Empty denominators fail. Other sufficient results are
`TECHNICALLY_VALID_MIXED_EVIDENCE`. Incomplete or unverified work has no economic
classification. No label is proof of edge or automatic promotion.

## Guard and current action

The checked-in command exposes `preflight`, `run`, and `verify`. Preflight is
strictly data-only: it checks saved hashes, metadata and calendar inventory without
constructing features or Strategy/Risk/Safety state, counting candidates, or
simulating outcomes. Its retained result is
`breakout_retest_history_screened_180d_preflight.json`.

`run` fails before real feature or strategy construction unless an external grant
binds the exact reviewed executable SHA, protocol digest, task-plan digest, input
identity and a fresh UUID. It then creates one append-only, sequential ledger.
`STARTED`, `FAILED`, and `COMPLETED` tasks never rerun. Full populations and
reconstructible all-fill authorities are retained. `verify` only accepts a fully
completed/finalized ledger and reconstructs reports from those retained bytes.

No grant or UUID exists in this PR. The immediate next action is architect review.
A later exact-SHA grant is the sole gate to the historical run. No acquisition,
historical smoke test, candidate count, strategy outcome, tuning, runtime change,
account access, trading, or deployment occurred here.
