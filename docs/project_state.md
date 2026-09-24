# Adaptive Trading Bot — Current Project State

**Snapshot date:** 2026-09-24
**Purpose:** Concise handoff for fresh ChatGPT/Codex sessions.  
**Important:** This file is a snapshot, not an authority over current GitHub state. Always verify `main`, active Issues/PRs and latest architect comments before acting.

---

## 1. Trusted repository state at this snapshot

Current verified pre-PR-#150 `main` after Issue #147 / PR #148 merged:

`4ed85e639a84d282522fc73b969e622fb67cdbdd`

This commit is the squash merge of PR #148 / Issue #147. It preserves the
locked LAST, MARK, funding, forward, calibration, reconciliation, and Trend
Pullback signal-edge evidence, while adding the Breakout + Retest research
predeclaration without executing its real study.

PR #119 remains accepted evidence that the first locked-WEEX primary EMA50 study stopped correctly under fail-closed rules. PR #125 fixed the demonstrated paired-research tooling blocker with explicit `ENTRY_FILL_INVALIDATED` accounting. PR #126 then made leverage a bounded, versioned, predeclared Risk-owned V1 parameter and required representative perpetual mechanics before Live readiness. None is evidence for or against EMA50 performance.

---

## 2. Current critical-path objective

Issues #123, #124, #128, #130, #132, #134, #136, #138, #140 and #142 are closed,
and Master Plan v1.3 is merged.
Issue #128 completed with the truthful result `PARTIAL`/`BLOCKED`: historical
liquidation replay and representative leveraged research remain unqualified.
Issue #130 locked current public evidence while truthfully retaining
`DEMO_CREDENTIALS_UNAVAILABLE`, `DEMO_SYMBOL_MAPPING_UNRESOLVED`, and
`FORWARD_CALIBRATION_BLOCKED`. Issue #136 then qualified only the exact current
feed mapping. The historical WEEX leveraged-mechanics evidence pursuit is
closed. The active critical path is:

**#149 — review the one-time Breakout + Retest V1 fixed-2R signal-edge
evidence.** The #147 implementation was merged and frozen at
`4ed85e639a84d282522fc73b969e622fb67cdbdd`; the architect then
activated one A/B/C execution on the locked WEEX LAST corpus. All three runs
completed once. The frozen screen result is
`BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_NEGATIVE` (three evaluable NEGATIVE
units). See `docs/research/breakout_retest_signal_edge_study_2026_09_24.md`
and its immutable study manifest. Do not tune or rerun these units under #149.

The Trend Pullback implementation was merged and frozen at
`b07e9eac7f373c4bc1b79a286714df00ae817ec2`; the architect then activated
exactly one A/B/C execution. All three runs completed from the locked LAST
corpus. The frozen screen result is `SIGNAL_EDGE_SCREEN_NEGATIVE` (three
evaluable NEGATIVE units). See
`docs/research/trend_pullback_signal_edge_study_2026_09_23.md` and the
immutable study manifest. Do not tune or rerun these units under #144. The
Breakout + Retest predeclared protocol remains in
`docs/research/breakout_retest_fixed_2r_screen.md`.

The official documentation and small public-only V3 capture qualify a narrow
current feed reference for MARK/index/funding facts and timestamps. They do not
qualify contract value, quantity, precision, fees, leverage, margin, MMR,
liquidation, fills, historical policy, or general Demo/production equivalence.
Issue #134 remains immutable and was not rerun. Issue #140's local runner
attempted exactly one opening through the reviewed POST path under the frozen
protocol; WEEX receipt and acceptance remain `UNKNOWN`. No position was
observed in the bounded checks, the opening was not retried, and no cleanup
request was eligible. No mechanics comparison was possible. Issue #142 later
found zero current positions and zero exact client-ID matches in one bounded
Demo order-history page for the reviewed window. This is
`DEMO_AMBIGUOUS_ORDER_NOT_FOUND`, not proof the earlier request never reached
WEEX.

Merged #120 result:

- actual fills that cross the sealed invalidation are recorded as research-only `ENTRY_FILL_INVALIDATED`;
- the exact simulator fill and sealed Strategy → Risk → Safety provenance remain intact;
- no research position, accounting branch, exit or PnL is fabricated;
- robustness population accounting reports the state explicitly without including it in trade-outcome statistics.

PR #122 established the durable architect workflow and project-state snapshot pattern. Those documents do not independently authorize architecture implementation or research execution.

---

## 3. Stopped study that must not be continued retroactively

**Issue #118 — Predeclared WEEX locked-corpus study — Trend Pullback EMA50 exit robustness**

Status for scientific purposes: **STOPPED / BLOCKED**.

Run A stopped once with exit code `1` before a valid JSON report existed. Runs B and C were not executed. The run must not be resumed after code changes.

The #120 correction and #123/#124 architecture decisions are merged. Issue
#144 is a separately predeclared, explicitly unleveraged/pre-funding signal-edge
screen, not a continuation of #118 and not a claim of representative leveraged
perpetual performance or Live readiness. The old #118 run remains historical
blocker evidence only.

---

## 4. Accepted immutable WEEX research authority

Primary V1 research source remains:

```text
source_id = weex-v3-last-e5b0c4035a19d5a80ca7
dataset_version = weex-v1-5m-last-a629a29b580b2e103adba918
manifest_sha256 = 98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95
```

Canonical policy:

- WEEX Futures/perpetual `5m LAST` is canonical historical input.
- V1 `15m`, `1h`, `4h` are derived from qualified canonical 5m data.
- Backtests/research use the locked corpus, not live historical API fallback.
- Do not repair gaps with another exchange, interpolation, forward/back fill or silent deletion.
- A future WEEX repair creates a new dataset version; never mutate this version in place.

Accepted ranges:

- BTC: `[2024-07-01T00:00:00Z, 2026-01-08T00:00:00Z)`
- ETH epoch A: `[2024-07-01T00:00:00Z, 2025-01-01T00:00:00Z)`
- ETH epoch B: `[2025-04-02T00:00:00Z, 2026-01-08T00:00:00Z)`
- ETH `[2025-01-01T00:00:00Z, 2025-04-02T00:00:00Z)` remains excluded and must not be bridged.

Issue #128 also locked separate, partial leveraged-input evidence without
changing LAST:

```text
MARK dataset = weex-v1-5m-mark-97c879a06fc900676dc5c339
MARK manifest_sha256 = 3e5ed9bc81877fa9c1d45fe33145d82f36487afeb5e49e9502ae087ec0749275
funding dataset = weex-v1-funding-87c1f6e88bd64d96c015fe39
funding manifest_sha256 = 6ecc0c5717a0ec3e5c0c28a96c542226a48e007ccdda48934e5900d304746c52
```

Only ETH epoch B has qualified MARK coverage; funding is a retained suffix;
historical MMR remains unqualified. These partial artifacts do not authorize a
representative leveraged study.

Issue #130 locked the separate current/forward bundle:

```text
forward bundle = weex-v3-forward-b88af4b81510850232656be7
manifest_sha256 = a344bf9cf726b8b68070637bca1c7971eaf248bf3e56585e4b5acd712b9088ca
```

Issue #132 locked a separate non-trading blocked calibration report:

```text
calibration bundle = weex-demo-calibration-e300b52fe0e43bfe8581ca58
manifest_sha256 = 025159eb178a9e036c3fb36b531a2210cc45c5abfb7d66b153477521274285b6
statuses = DEMO_CREDENTIALS_UNAVAILABLE, DEMO_LONG_CALIBRATION_BLOCKED
```

Issue #134 locked the single credential-present, pre-POST blocked result:

```text
calibration bundle = weex-demo-calibration-d7eb4338f33b986cc54b1cb6
manifest_sha256 = 6119d7a1b7fca8624dfdb6c74b643fc46521e1e6badbd9f4b1e2d82ec8fd20f2
statuses = DEMO_CONTRACT_FACTS_UNAVAILABLE, DEMO_MARK_UNAVAILABLE,
           DEMO_CALIBRATION_BLOCKED_NO_MARK, DEMO_LONG_CALIBRATION_BLOCKED
state-changing requests transmitted = 0
```

Issue #136 adds a separate current-only feed-mapping evidence bundle:

```text
mapping = BTCSUSDT requested Demo instrument -> BTCUSDT actual public reference
mapping bundle = weex-demo-feed-mapping-5d3ade83b136ca6f04e301e9
manifest_sha256 = 46a48e421d81c05d6ed59c106651bfa5fc8bb30b548d804a8a80ea2fb9c273c0
scope = MARK/index/funding/timing/feed timestamps only
historical/mechanics/production equivalence = not qualified
```

Issue #140 locked the one mapped-feed calibration result:

```text
calibration bundle = weex-demo-calibration-08376b6b937422f89c4b4aab
manifest_sha256 = 189f8a20747a04a022a96807d6d2d79a192f671a9068a72a34bdcc3a09270ecc
execution source SHA = 47521f11069da75dfc9787732efb3c5bc90e5b4a
derivation = EVIDENCE_ONLY_CORRECTION from
             weex-demo-calibration-31f710cf17bb784da2f3ecad
predecessor manifest = 2045f1bcf95dfcfa37ad8eb1baa881f39b42d1965368d7d8be52608933fb2815
statuses = DEMO_CONTRACT_FACTS_UNAVAILABLE, DEMO_MARK_QUALIFIED,
           DEMO_OPEN_RESPONSE_UNAVAILABLE, DEMO_OPEN_NOT_OBSERVED,
           DEMO_LONG_CALIBRATION_BLOCKED
ambiguous request SHA-256 = ce0019db0d6af534b52849f0ee11b4ba489879b0fc124ef2375f2f1d04d4f178
venue receipt / acceptance = UNKNOWN / UNKNOWN
position/comparisons/cleanup = unavailable / none / not applicable
```

Issue #142 locked a separate read-only reconciliation result:

```text
reconciliation bundle = weex-demo-reconciliation-4a17f9406b037ce84d6ce288
artifact_sha256 = d23b1e3f9d2e825822ec7b2d5c4ad2afad75d6ca33b1f2640af456a2e79823fb
manifest_sha256 = 28f2c963fa8624c73b9e00a0f7697f3c703f158495fe06ea7ba98b7707702c61
position GETs / history pages / rows / exact matches = 1 / 1 / 0 / 0
terminal classification = DEMO_AMBIGUOUS_ORDER_NOT_FOUND
state-changing requests = 0
```

---

## 5. Current project discipline

Two questions govern every critical-path Issue:

1. **Is the change technically correct and safe?**
2. **Does it materially move the project toward the next validated goal/gate?**

Do not reduce quality in data integrity, Risk, Safety, execution, accounting, reproducibility, causality or auditability merely to move faster.

At the same time, prevent overengineering: new abstractions/hardening belong on the critical path only when they fix a demonstrated blocker or are required for trustworthy evidence at the next gate.

The project has not yet proven a sustainable trading edge. Engineering rigor must now convert into trustworthy economic evidence, not become an end in itself.

Negative strategy/research results are valid results and must not be tuned away after observation.

---

## 6. Workflow reminder

Detailed architect instructions live in GitHub. The user passes Codex a short handoff message; Codex implements; then the user asks the architect to review the actual GitHub result.

Codex CI policy:

- run required local validation;
- push/open/update PR;
- trigger GitHub Actions;
- **do not poll or wait for GitHub CI completion**;
- report PR URL, exact HEAD and local validation;
- architect verifies exact-head GitHub CI later during review.

Do not merge or deploy unless explicitly authorized.

See `docs/architect_workflow.md` for the durable complete workflow.

---

## 7. Immediate next expected sequence

1. Complete architect review and evidence lock for PR #150. Issue #149's
   one-time A/B/C execution is complete; the frozen Breakout + Retest V1 result
   is `BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_NEGATIVE`. Do not rerun or tune
   Breakout + Retest V1 on these same windows.
2. Keep Trend Pullback #144 frozen `SIGNAL_EDGE_SCREEN_NEGATIVE`, and keep the
   WEEX historical leveraged-mechanics pursuit closed. Neither screen is a
   leveraged, liquidation-complete, portfolio, or Live result.
3. After PR #150 locks the evidence, wait for the architect to select the next
   strategy family or research gate separately. Keep Issue #118 permanently
   stopped; do not resume its failed Run A or run its B/C commands retroactively.
