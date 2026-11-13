# Adaptive Trading Bot — Architect / Codex Workflow

**Status:** Mandatory project workflow  
**Purpose:** Preserve collaboration rules, review discipline, and continuity across ChatGPT/Codex sessions.  
**Repository:** `setup-co/adaptive-trading-bot`

---

## 1. Why this document exists

This project spans many ChatGPT and Codex sessions. Critical operating rules must therefore live in the repository rather than depend on conversational memory.

This document defines **how work is planned, delegated, reviewed, validated, and advanced**. It complements, but does not replace:

- `docs/trading_bot_master_plan.md` — product/architecture source of truth;
- `AGENTS.md` — mandatory coding-agent rules;
- `docs/project_state.md` — preserved historical project snapshot;
- `docs/project_state_current.md` — concise current project handoff/status;
- the active GitHub Issue / PR and the latest architect instruction posted there.

If these sources conflict, do not guess. Stop and surface the conflict for architect review.

---

## 2. Source-of-truth hierarchy

For normal work, use this order:

1. `docs/trading_bot_master_plan.md` for product, architecture, risk and roadmap invariants.
2. `AGENTS.md` for mandatory implementation and repository rules.
3. This document for collaboration/review workflow.
4. The active Issue/PR and the **latest architect instruction** for task-specific requirements.
5. `docs/project_state.md` for required historical context only.
6. `docs/project_state_current.md` for current handoff/status.

`docs/project_state.md` is now an intentionally preserved historical snapshot. The current handoff
lives in `docs/project_state_current.md`; neither may silently override an Issue, PR instruction,
`AGENTS.md`, or the Master Plan.

---

## 3. Roles

### 3.1 Architect / Quant Lead / Reviewer — ChatGPT

The architect is responsible for:

- deciding the next justified engineering/research step;
- writing detailed task instructions in GitHub Issues or PR comments;
- preserving Master Plan, risk, data, execution and research invariants;
- independently reviewing the **actual GitHub branch/PR/files/CI**, not relying only on a Codex summary;
- approving, rejecting or requesting corrections;
- defining fresh predeclared research protocols before results are observed;
- deciding when a phase/gate has enough evidence to advance;
- preventing both unsafe shortcuts and unnecessary overengineering.

By default the architect does **not** directly implement application code. Direct implementation happens only when the user explicitly overrides the normal workflow.

### 3.2 Implementer — Codex

Codex is responsible for:

- reading `AGENTS.md`, the Master Plan, this workflow, current project state, the assigned Issue/PR and latest architect instruction;
- implementing only the approved scope;
- preserving existing authorities and invariants;
- adding/updating focused tests;
- running appropriate local validation;
- pushing a focused branch/PR and reporting exact HEAD SHA and local validation results;
- stopping and reporting when an instruction conflicts with authoritative project rules.

Codex must not merge a target PR, deploy, expand scope, change research assumptions, or continue a stopped study unless a specific architect instruction explicitly authorizes that action.

### 3.3 User

The user controls continuation. The normal handoff is:

1. architect places detailed instruction in GitHub;
2. architect gives the user a short Codex handoff message;
3. user gives that message to Codex;
4. Codex implements and reports completion;
5. user tells architect to review;
6. architect independently reviews GitHub and posts the next instruction.

---

## 4. Standard work loop

### Step A — Architect defines work

Before asking Codex to code, the architect should:

- inspect current `main`, active Issue/PR and relevant source/tests;
- define the smallest task that removes a real blocker or advances the project toward the next evidence/gate;
- make acceptance criteria and non-goals explicit;
- keep scope aligned with the Master Plan;
- predeclare research parameters before results are observed when research is involved.

Detailed implementation instructions belong in GitHub. The chat handoff to Codex should remain short and dense.

### Step B — Codex implements

Codex should:

- start from the exact requested base SHA/ref;
- create/use the Issue branch;
- implement only the requested behavior;
- run targeted tests during development;
- run the required local quality gates before handoff;
- push/open/update the PR;
- ensure GitHub Actions is triggered;
- report the PR URL, exact HEAD SHA and local validation status.

### Step C — Codex stops after publishing the PR

**Codex must not spend tokens waiting for GitHub Actions to finish.**

After the PR is published and CI has been triggered, Codex should stop. It must not repeatedly poll, watch, sleep for, or continuously re-check GitHub Actions merely to wait for completion.

A suitable final implementation note is conceptually:

> PR pushed; GitHub CI has been triggered and may still be running. I did not poll or wait for CI completion.

This rule reduces unnecessary Codex token usage without weakening the quality gate.

### Step D — Architect review

When the user asks for review, the architect must independently inspect:

- PR/base/head identity;
- changed files and patch;
- relevant contracts/tests;
- CI result for the exact PR HEAD;
- scope discipline;
- whether the change is technically safe;
- whether it actually advances the current project gate.

Do not approve based solely on the Codex narrative.

### Step E — Next instruction

If accepted, the architect posts the next GitHub instruction (including merge authorization when appropriate) and gives the user the next short Codex handoff.

If rejected, the architect posts a focused corrective instruction and Codex fixes the same PR unless a fresh Issue is required by research/integrity rules.

---

## 5. CI policy — quality without token waste

GitHub Actions remains an authoritative quality gate.

The efficient division of responsibility is:

- **Codex:** implementation + relevant/full local validation as required + push/PR + trigger CI.
- **GitHub Actions:** asynchronous repository-level CI.
- **Architect:** later verify CI for the exact reviewed HEAD and inspect failures if any.

Rules:

1. Do not remove or weaken CI merely to save tokens.
2. Do not make Codex wait/poll for CI completion.
3. Sensitive changes may still require full local Python/frontend validation before handoff.
4. Merge approval requires the expected reviewed HEAD and required CI green unless an explicit architect instruction states otherwise.
5. If CI fails, the architect reviews the failure and issues a focused correction; Codex should not guess or perform unrelated cleanup.

---

## 6. Quality and progress must both be protected

The project must avoid two opposite failures:

- **shortcut engineering:** moving faster by weakening correctness, data integrity, risk, execution, accounting, reproducibility, safety or research discipline;
- **endless hardening / overengineering:** repeatedly adding abstractions and infrastructure that do not materially improve the current evidence/gate.

Both are unacceptable.

For every proposed Issue or substantial change, the architect must ask:

1. **Is this technically correct and safe?**
2. **Does this materially move the project toward the next validated goal/gate?**

A change should normally proceed only when both answers are yes.

### Non-negotiable quality areas

Do not trade away quality in:

- market-data integrity and provenance;
- deterministic/reproducible research;
- execution semantics and costs;
- accounting/PnL correctness;
- RiskManager sovereignty;
- safety/fail-closed behavior;
- prevention of look-ahead/data leakage;
- immutable/versioned research evidence;
- auditability and explainability.

### Overengineering guard

A new abstraction, schema, subsystem, service or hardening task should enter the critical path only when it:

- fixes a demonstrated correctness/safety blocker; or
- is required to produce trustworthy evidence for the next gate; or
- is explicitly required by the Master Plan/current accepted Issue.

Otherwise defer it to backlog.

Each phase/gate should have explicit exit criteria. Once the criteria pass, do not keep expanding the phase merely because more hardening is possible.

---

## 7. Economic/research discipline

Engineering quality is a means to obtain trustworthy trading evidence, not a substitute for evidence of edge.

Rules:

- A profitable backtest alone does not prove an edge.
- A negative result is a valid and useful result.
- Do not tune parameters, dates, costs, symbols, exits or subsets after observing results merely to rescue a hypothesis.
- Do not hide censoring, invalidated admissions, unfilled orders or unresolved positions.
- Do not fabricate portfolio/equity performance from independent research experiments.
- After a code/tooling correction that can change serialized/research outcomes, do not retroactively continue an already stopped predeclared study. Create a **fresh predeclared study** with a new locked code SHA.
- Strategy lifecycle remains the Master Plan sequence: Hypothesis → Formal Rule → Backtest → Robustness → Walk Forward → Replay → Paper → Shadow → Approval → Versioned Strategy.

The desired end state is a controlled, explainable trading system whose edge and risk behavior are supported by sufficient evidence **before meaningful real capital is exposed**.

---

## 8. Merge and deployment rules

- Architect review does not itself imply merge authorization.
- Codex must not merge its own PR unless the latest architect instruction explicitly says to merge.
- If an accepted prerequisite must be merged before another task, the architect may explicitly instruct Codex to perform that merge and verify the resulting `main` SHA.
- No deployment unless explicitly requested/authorized.
- Live/real-money deployment remains governed by `AGENTS.md` and Master Plan acceptance gates.

---

## 9. Starting a new ChatGPT/Codex session

Before making a project decision in a fresh session, read at minimum:

1. `AGENTS.md`
2. `docs/trading_bot_master_plan.md`
3. `docs/architect_workflow.md`
4. `docs/project_state.md`
5. `docs/project_state_current.md`
6. the active Issue/PR and its latest architect instruction/comments

Then verify current `main` and active PR/Issue state from GitHub rather than assuming the snapshot is still current.

A fresh chat should continue the established workflow; it must not invent a new development process simply because conversational context was lost.

---

## 10. Maintaining current project state

Preserve `docs/project_state.md` byte-for-byte because predecessor evidence binds its historical
snapshot. Update `docs/project_state_current.md` at meaningful handoff points, especially after:

- an important PR is merged;
- a research study completes/stops;
- a root cause is accepted;
- a major source/dataset version is accepted;
- the active critical-path Issue changes;
- the project advances to a new validation gate.

Keep it concise. It should answer:

- What is the current trusted `main` state?
- What is the current active critical-path Issue/PR?
- What just happened and why?
- What is explicitly blocked/deferred?
- What is the next expected review/action?
- Which immutable dataset/protocol identities matter right now?

Do not turn `project_state_current.md` into a duplicate history log; detailed evidence belongs in
Issues, PRs and research documents.
