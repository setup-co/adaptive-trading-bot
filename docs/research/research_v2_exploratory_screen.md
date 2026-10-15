# Research V2 exploratory screen — predeclared implementation annex

**Protocol:** `RESEARCH_V2_EXPLORATORY_SCREEN_V1`
**Protocol SHA-256:** `525d5f975ca4b58d9e8d1ca78fec2dc90d6de6f3dc1067ef77ca5f26c2ecf319`
**Thirty-task plan SHA-256:** `03376c954ca51ca50754d925189cad7be55f14249cfec29e6cbbe710a1fcfca1`
**Status (2026-10-15):** implementation, synthetic tests and real **data-only** preflight; no real execution grant and no historical strategy evaluation.

This annex makes Issue #185's retrospective, current-snapshot-selected screen
executable **only after** a separate architect handoff freezes the reviewed
executable Git SHA and supplies an external, exact-SHA grant. The present PR is
not that grant. No grant is committed. The study is exploratory and
selection-informed, not an untouched final test, survivorship-free inference,
portfolio simulation, proof of edge, or Paper/Shadow/Live authority. The three
blocks are chronological descriptions of one decision population, not separate
optimization or validation folds. Outcomes may cross block boundaries.

## Frozen input and authorities

The only historical input is accepted C2 source
`weex-research-v2-last-18912cf7e7f97d518a74551f`, dataset
`weex-research-v2-last-90d-issue-179-combined-v2`, manifest SHA-256
`18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`.
Its plan/report SHA-256 values are
`345c797e4eb740dc9e43a23847f67be15411164d4d4888103c86f64091d1ab16`
and `efd51747baf786ad637ec0814eb94ade83bee9a46c4fe5941f4d0fd97a9f2ca9`.
The original and C1 manifests remain
`870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3`
and `1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed`.
The selected-panel manifest/result remain
`de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79`
and `80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4`.
The source/units note remains
`7eaa3a08c4f570f4cde8c110e616f55d271719eb45e318b6c81e81ae808582ce`.
The original BTC 88-open and C1 BTW 100-open gaps are not repaired. The whole
90-day C2 data gate remains `PARTIAL_OR_BLOCKED`; the future study uses only
bounded, complete same-epoch intervals after each 200-hour warm-up.

Panel, in task order: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `ZECUSDT`,
`ZAMAUSDT`, `BCHUSDT`, `BTWUSDT`, `AVAXUSDT`, `DOGEUSDT`. Per member the family
order is Trend Pullback `1.0.0` (EMA50 primary, hold-only paired diagnostic),
Breakout + Retest `1.0.0` (fixed 2R) and composed Range Mean Reversion `2.0.0`
(stateful validated detector, entry-frozen midpoint). No family policy or
threshold changes. `NORMALIZED_BASE_SIGNAL_V1`, `CAUSAL_ENTRY_CHRONOLOGY_V2`,
`RESEARCH_V2_ISOLATED_ADMISSION_V1` and `RESEARCH_ALL_FILL_ECONOMICS_V1` remain
the entry, chronology and accounting authorities. Research equity is fixed
`10000 USDT`; each independent candidate assumes zero carried exposure and
the existing default Risk/Safety and explicitly hypothetical health profile.
The model is unleveraged/pre-funding, using one declared base-asset quantity
unit. Historical WEEX contract-count equivalence, true charges, margin,
funding and liquidation are not established.

## Causal calendar and costs

| Block | Retained 15m candle opens `[start,end)` | Completed decisions `(start,end]` |
| --- | --- | --- |
| P1 | 2026-08-03 00:00Z to 2026-08-24 00:00Z | 2026-08-03 00:15Z to 2026-08-24 00:00Z |
| P2 | 2026-08-24 00:00Z to 2026-09-14 00:00Z | 2026-08-24 00:15Z to 2026-09-14 00:00Z |
| P3 | 2026-09-14 00:00Z to 2026-10-05 00:00Z | 2026-09-14 00:15Z to 2026-10-05 00:00Z |

Each block has 2,016 15m observation slots per asset/family; the single
63-day population has 6,048 per task and 181,440 over all thirty tasks.
BASE/STRESS retain those exact same decisions/admissions: 362,880 scenario
rows. These are **calendar counts**, not candidates or fills. Weeks W1–W9
are consecutive seven-day bins allocated by the signal candle **open**.
One chronological family engine runs through all blocks for each member;
state never crosses an asset, family or true data gap. Pre-window context
and state warm-up never become extra decision members.

Context begins at the same-epoch complete 4h grid: BTC
`2026-07-21T00:00:00Z`, BTW `2026-07-25T12:00:00Z`, the other eight
`2026-07-16T00:00:00Z`. The earlier BTC/BTW epochs remain excluded from this
comparison, not erased from source evidence. Every retained decision at `t`
has its **own** exit/eligible-open end exclusive `t + 168h`, never fill+168h
or the common final date. The latest cutoff is `2026-10-12T00:00:00Z`, inside
the qualified epoch; October 12–14 is deliberately unused. No forced close,
last-price refund or new post-October-5 decision is permitted. A trigger
remains a completed-bar observation, not a fill price; protective stop wins a
same-bar tie. Open ordinary/recovery exposure and paid fees remain visible.

The read-only C2 preflight verified these exact context/follow-up grids. Each
member has 6,048 retained decision slots **per family**, independent of
strategy outputs; frame counts include causal context warm-up and follow-up,
not extra decision-population members.

| Member | Context start UTC | 15m / 1h / 4h rows through Oct 12 00:00Z | Decision slots/family |
| --- | --- | ---: | ---: |
| BTCUSDT | Jul 21 00:00 | 7,968 / 1,992 / 498 | 6,048 |
| ETHUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| SOLUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| XRPUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| ZECUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| ZAMAUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| BCHUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| BTWUSDT | Jul 25 12:00 | 7,536 / 1,884 / 471 | 6,048 |
| AVAXUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |
| DOGEUSDT | Jul 16 00:00 | 8,448 / 2,112 / 528 | 6,048 |

The preflight's canonical data-only plan digest is
`20277cd37f4e0c42a74bf4515b84cd93e1ad086069565341d537bc762fd4dc43`.
It binds the protocol/task hashes, all 30 ordered task records, three blocks
and verified per-member epoch/usable intervals. The compact task-plan hash
above is the future grant's exact inventory identity. No preflight field is a
candidate/fill/PnL count.

BASE models spread/slippage/fee `2/2/10` bps, zero latency. The **single**
COMBINED_STRESS models `4/4/20` bps and exactly one second latency. Risk,
Safety, strategy and candidate decisions do not change between them. The
combined stress is not an attribution of cost versus latency and is not a
grid to optimize.

## Population, dependence, and frozen interpretation

Each scenario reports the complete NO_SIGNAL, RISK_BLOCKED, SAFETY_BLOCKED,
ENTRY_UNFILLED, ORDINARY_CLOSED/OPEN and RECOVERY_CLOSED/OPEN partition.
Trend hold-only is paired diagnostic only; its separate status, sealed branch
identity, closed R, fees, residual quantity and summary never count as a
second primary trade. One EMA50 primary branch counts as one opportunity.
Report explicit decision/candidate/admission/fill/closed/
unresolved/recovery denominators, fees, residual base quantity, authoritative
impact-inclusive gross and net PnL, mean/median closed approved-risk R, and
R-space profit factor. Undefined factor stays unavailable. Asset, P1–P3,
W1–W9, side and regime zero cells remain explicit. Independent candidate
outcomes are not a funded account/equity curve.

The diagnostic mask, fixed *before outcome inspection*, selects the earliest
research-admissible decision for each asset/family, then the next one at or
after the previous selection plus 168h, continuously across P1/P2/P3. Both
sides compete and BASE/STRESS share the same mask. An unfilled/invalidated
selection is never replaced by a later winner. Raw versus masked counts,
same-asset horizon overlaps and cross-asset weekly concentration remain
descriptive; this mask does not prove independence.

Each family is classified after technical validity, in order:

1. `INSUFFICIENT_EVIDENCE` if either scenario has <60 primary closed fills,
   <30 masked closures, <5 assets with >=3 masked closures each, <6 weeks
   with >=3 masked closures each, or **any** primary unresolved fill at its
   cutoff. Fees/exposure of unresolved positions remain reported. Hold-only
   diagnostic opens do not trigger the primary gate.
2. `NEGATIVE_EVIDENCE` if sufficient and all four full/masked BASE/STRESS
   closed mean net R values are <=0.
3. `CANDIDATE_FOR_FURTHER_VALIDATION` if sufficient and those four means are
   >0; >=5 assets with >=3 masked closures and positive masked mean R in
   **both** scenarios; >=6 weeks with >=3 STRESS masked closures and positive
   STRESS masked mean; and removing **any** asset leaves positive STRESS full
   and masked means. Empty leave-one-out denominators fail. This is only
   permission to propose later validation, not evidence of a proven edge.
4. Otherwise `TECHNICALLY_VALID_MIXED_EVIDENCE`.

An incomplete, unverified, contradictory or stopped task inventory has no
economic classification. Zero candidates are an honest insufficient result.
No p-value, CI, Sharpe, portfolio drawdown, favorable subset, best-family
selection or automatic trading promotion is part of this protocol.

## One-shot operational boundary

`python -m scripts.research.research_v2_exploratory_screen preflight --repo .`
reads **only** locked C2 files, verifies hashes/epochs/15m–1h–4h grids and
prints the exact 30-task plan and coverage. It does not construct features,
count candidates, simulate or compute PnL. The `run` command exists but
requires a separately provided external grant with the later architect-
reviewed clean executable Git SHA, this protocol and task-plan hash, accepted
source/dataset/manifest and one unique UUID. No grant is present in this PR.
The code SHA is intentionally absent from the protocol hash to avoid a
self-referential commit. A missing/wrong grant fails before strategy
evaluation. The synthetic namespace cannot impersonate accepted C2.

The future run creates one new append-only UUID ledger, commits each task's
STARTED event before work, stores one immutable compressed population per
task and compact scenario partitions referencing its digest, then records
COMPLETED with checksums. An ambiguous STARTED task or any failure must not be
rerun, replaced or followed by a new task automatically; a separate architect
review is required. Final projection requires exactly thirty completed
tasks and offline verification. This preparation does **not** create a real
ledger or a historical candidate/PnL report.
