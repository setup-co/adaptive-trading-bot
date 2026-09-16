# Adaptive Trading Bot — Current Project State

**Snapshot date:** 2026-09-17
**Purpose:** Concise handoff for fresh ChatGPT/Codex sessions.  
**Important:** This file is a snapshot, not an authority over current GitHub state. Always verify `main`, active Issues/PRs and latest architect comments before acting.

---

## 1. Trusted repository state at this snapshot

Current verified `main`:

`a9464145d5306ebb369828f095c26cd32c00bcb7`

This commit is the squash merge of PR #125:

**Handle paired entry fills that invalidate sealed stops (#120)**

PR #119 remains accepted evidence that the first locked-WEEX primary EMA50 study stopped correctly under fail-closed rules. PR #125 fixes the demonstrated paired-research tooling blocker with explicit `ENTRY_FILL_INVALIDATED` accounting. Neither PR is evidence for or against EMA50 performance.

---

## 2. Current critical-path objective

The demonstrated paired-research blocker is fixed and merged. Issue #120 is closed. The project must now complete two queued architecture decisions before any fresh primary trading study is predeclared:

1. **#123 — Master Plan v1.2: make leveraged perpetual futures the V1 primary trading mode.**
2. **#124 — Master Plan v1.3: OpenAI-first AI architecture with cost-aware model routing.**

Execute and review #123 first, then #124. Do not begin either from this documentation refresh. Their accepted changes will determine the architecture and locked code state from which the architect can sequence the next primary study.

Merged #120 result:

- actual fills that cross the sealed invalidation are recorded as research-only `ENTRY_FILL_INVALIDATED`;
- the exact simulator fill and sealed Strategy → Risk → Safety provenance remain intact;
- no research position, accounting branch, exit or PnL is fabricated;
- robustness population accounting reports the state explicitly without including it in trade-outcome statistics.

PR #122 is the continuity-document task carrying the stable architect workflow and this state snapshot. It does not authorize architecture implementation or research execution.

---

## 3. Stopped study that must not be continued retroactively

**Issue #118 — Predeclared WEEX locked-corpus study — Trend Pullback EMA50 exit robustness**

Status for scientific purposes: **STOPPED / BLOCKED**.

Run A stopped once with exit code `1` before a valid JSON report existed. Runs B and C were not executed. The run must not be resumed after code changes.

The #120 correction is now merged, but a fresh study must still wait until #123 and then #124 are completed and the architect explicitly predeclares the next study from the resulting locked code SHA. The old #118 run remains historical blocker evidence only.

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

1. Refresh and review PR #122 as the continuity-doc task; do not treat it as trading authority.
2. Implement and review Issue #123 without beginning #124 concurrently.
3. After #123 is accepted and merged, implement and review Issue #124.
4. Only after both architecture decisions are merged may the architect predeclare the next primary trading study from the then-current exact `main` SHA.
5. Keep Issue #118 stopped permanently; never resume its failed Run A or run its B/C commands retroactively.
6. Only after a new valid primary study exists should the project interpret exit robustness and decide the next validation gate.
