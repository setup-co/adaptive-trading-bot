# Adaptive Trading Bot — Current Project State

**Snapshot date:** 2026-09-16  
**Purpose:** Concise handoff for fresh ChatGPT/Codex sessions.  
**Important:** This file is a snapshot, not an authority over current GitHub state. Always verify `main`, active Issues/PRs and latest architect comments before acting.

---

## 1. Trusted repository state at this snapshot

Current verified `main`:

`aa6d22402d3c462a28448c200ee5499d43fee222`

This commit is the squash merge of PR #119:

**Document stopped WEEX EMA50 locked study (#119)**

PR #119 is accepted evidence that the first locked-WEEX primary EMA50 study stopped correctly under fail-closed rules. It is **not** evidence for or against EMA50 performance.

---

## 2. Current critical-path objective

The project is currently clearing a research-tooling blocker before a fresh real-data EMA50 exit study can be predeclared.

Active critical-path Issue:

**#120 — Handle paired-replay entry fills that cross sealed invalidation**

Confirmed root cause:

- Strategy → Risk → Safety can authorize an entry with a sealed `stop_basis`.
- `BacktestExecutionSimulator` correctly fills the market order at the first causally eligible execution-candle open using shared execution costs.
- Market movement and/or execution costs can make the actual fill cross the already-approved invalidation.
- The paired research adapter previously had no explicit admission state for that case and later failed with:

```text
invalidation reference contradicts position side
```

The accepted semantic correction is research-only `ENTRY_FILL_INVALIDATED`: preserve the real simulated fill and provenance, create no research position/accounting branch, do not move the stop, do not resize, and do not fabricate an exit or PnL.

Codex is implementing #120. The next architect action is to review the resulting focused PR, exact HEAD, tests and CI. Do not rerun the full EMA50 study before #120 is reviewed and merged.

---

## 3. Stopped study that must not be continued retroactively

**Issue #118 — Predeclared WEEX locked-corpus study — Trend Pullback EMA50 exit robustness**

Status for scientific purposes: **STOPPED / BLOCKED**.

Run A stopped once with exit code `1` before a valid JSON report existed. Runs B and C were not executed. The run must not be resumed after code changes.

After the #120 correction is accepted and merged, the architect must create a **fresh predeclared study Issue with a new locked code SHA**. The old #118 run remains historical blocker evidence only.

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

1. Codex completes Issue #120 and opens/updates its focused PR.
2. User asks architect to review.
3. Architect independently checks patch, contracts, tests and exact-head CI.
4. If accepted, architect authorizes merge of the #120 PR.
5. After merge, architect updates this project state and creates a **fresh predeclared locked-WEEX EMA50 study** from the new `main` SHA.
6. Only after a valid primary study exists should the project interpret exit robustness and decide the next validation gate.

A separate documentation task, Issue #121, exists only to establish persistent workflow/state handoff and must not disturb the #120 critical path.
