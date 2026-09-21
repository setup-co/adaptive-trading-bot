# Adaptive Trading Bot — Current Project State

**Snapshot date:** 2026-09-21
**Purpose:** Concise handoff for fresh ChatGPT/Codex sessions.  
**Important:** This file is a snapshot, not an authority over current GitHub state. Always verify `main`, active Issues/PRs and latest architect comments before acting.

---

## 1. Trusted repository state at this snapshot

Current verified `main`:

`5fd26536c8c886bcdc9980883cf6bf08f5a8db53`

This commit is the squash merge of PR #131 / Issue #130:

**Fail-closed WEEX V3 forward mechanics evidence collector.**

PR #119 remains accepted evidence that the first locked-WEEX primary EMA50 study stopped correctly under fail-closed rules. PR #125 fixed the demonstrated paired-research tooling blocker with explicit `ENTRY_FILL_INVALIDATED` accounting. PR #126 then made leverage a bounded, versioned, predeclared Risk-owned V1 parameter and required representative perpetual mechanics before Live readiness. None is evidence for or against EMA50 performance.

---

## 2. Current critical-path objective

Issues #123, #124, #128 and #130 are closed and Master Plan v1.3 is merged.
Issue #128 completed with the truthful result `PARTIAL`/`BLOCKED`: historical
liquidation replay and representative leveraged research remain unqualified.
Issue #130 locked current public evidence while truthfully retaining
`DEMO_CREDENTIALS_UNAVAILABLE`, `DEMO_SYMBOL_MAPPING_UNRESOLVED`, and
`FORWARD_CALIBRATION_BLOCKED`. The active critical path is:

**#132 — run one bounded WEEX Demo LONG mechanics calibration sample.**

Issue #132 authorizes only its exact research-only Demo opening/cleanup
protocol when every precondition passes. In the current execution environment,
dedicated Demo credentials were unavailable, so no network request or order was
made and the immutable result is truthfully `BLOCKED`.

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

The #120 correction and #123/#124 architecture decisions are merged, but a
fresh study must still wait for #128 architect review, any subsequently approved
representative leveraged-perpetual mechanics, and an explicit architect
predeclaration from the resulting locked code SHA. The old #118 run remains
historical blocker evidence only.

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

1. Complete architect review of Issue #132's bounded harness and truthful
   credential-blocked evidence.
2. Do not repeat or vary the Demo experiment without a new explicit architect
   instruction; Demo/production symbol semantics remain unresolved.
3. Do not implement representative leveraged-perpetual mechanics until the
   evidence gates required by the architect are satisfied.
4. Only after those mechanics are implemented and validated may the architect
   predeclare the next primary study from the then-current exact `main` SHA.
5. Keep Issue #118 stopped permanently; never resume its failed Run A or run
   its B/C commands retroactively.
