# Breakout + Retest zone invalidation hypothesis

**Policy:** `BREAKOUT_RETEST_ZONE_INVALIDATION_V1`  
**Strategy identity:** `breakout_retest_zone_invalidation` `0.1.0`  
**Status:** opt-in synthetic implementation; untested economic hypothesis

## Question and mechanism

The accepted `breakout_retest` `1.0.0` evaluator defines a causal retest zone
around its locked breakout level. A successful baseline candidate currently
uses the retest candle low for LONG or high for SHORT as its initial
invalidation reference. This hypothesis changes exactly that reference:

- LONG uses the same decision audit's `retest_floor`;
- SHORT uses the same decision audit's `retest_ceiling`.

The variant owns one private baseline evaluator and advances it exactly once
per ordered context. It preserves every baseline `NO_TRADE`, arming, wait,
expiry, cancellation and consumed-setup transition. Only a genuine baseline
candidate can reach the projection. The original evaluator identity, complete
rule audit, original candle stop, selected boundary and distance, policy and
decision time remain in a typed audit.

The selected value must be finite, positive, causally consistent with that
decision's baseline audit and on the correct side of the unchanged reference
price. Its new distance must remain at or below the existing `3.0 * ATR` cap.
Missing or contradictory audit facts fail closed without a candle-stop
fallback. A rejected projection does not restore the setup already consumed
by the baseline evaluator. Equal candle and zone boundaries are valid and
recorded as equal.

Nothing else changes: the 15m/1h/4h inputs, `0.20 ATR` retest tolerance,
entry requirements, chronology and numerical thresholds remain baseline
authority. The stop is frozen at decision time; it is not trailed or widened
later.

## Risk and activation boundary

The strategy proposes geometry only. Its candidate flows through the existing
`RiskManager`, Operational Safety gate, decision journal and entry-intent
authorizer. Quantity is recalculated from the new stop under the unchanged
risk and exposure caps. A wider stop therefore reduces quantity in a
risk-limited case; an exposure-limited case may retain the same quantity and
different approved risk. The old quantity is never carried forward.

This class is exported only for explicit construction. It is absent from
runtime, Paper/Shadow/Live and completed research family registries. It adds no
runner, grant, dataset authority, execution rule, target, indicator, leverage
or trading permission. The original `breakout_retest` `1.0.0` implementation
and all completed evidence remain unchanged.

## Evidence and selection limits

The completed Research V2 screen classified Breakout + Retest as
`INSUFFICIENT_EVIDENCE`. The later fixed factorial showed sensitivity to the
combined modeled costs and entry eligibility, but did not establish a causal
benefit from wider invalidation. Selecting this mechanism after inspecting
those results is explicitly post-result and hypothesis-generating.

A wider stop can lower quantity while increasing loss distance, change Risk
and exposure admissions, alter target distance and holding time, increase
unresolved positions, or simply lose more. A lower fee-to-approved-risk ratio,
a positive subset or an insufficient sample cannot establish edge. Approved
risk is not protection from realized gaps or slippage.

All 2026 windows and outcomes already inspected by Issues #187, #189, #191,
#193 and #195 are development/selection-informed and cannot be called untouched
out-of-sample evidence.

## Future test boundary — not activated

Any later evaluation requires a separate frozen authorization and protocol
that compares baseline with this candidate while:

- applying each strategy's own sovereign Risk sizing;
- using the same predeclared BASE/STRESS costs and eligibility assumptions;
- using causal all-fill accounting and the existing seven-day observation
  horizon;
- retaining every candidate event, Risk/Safety rejection, changed or unmatched
  admission, fee, exposure, approved risk, recovery and unresolved position;
- retaining derived-target and holding-time consequences;
- using no invented profit target and no forced close.

Reuse of inspected windows would be exploratory. Confirmation requires
separately protected data after the candidate and protocol are frozen. This
document and implementation authorize neither historical execution nor any
Paper, Shadow, Live or exchange activity.
