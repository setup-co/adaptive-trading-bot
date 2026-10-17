# Research V2 execution factorial — preparation annex

**Protocol:** `RESEARCH_V2_EXECUTION_FACTORIAL_V2`

**Schema:** `research-v2-execution-factorial-v2`

**Protocol SHA-256:** `470f4ba70d967c62b0ec5e777d876a926a8f204c471b5a28a6b12e33cb05160c`

**Status:** implementation and inert retained-evidence preflight only; no historical execution grant

## Question and authority boundary

This post-result conditional simulation is limited to the 246 sealed admissions
from Issue #187. It separates the previously combined modeled-cost and
eligibility-latency changes on the same recorded decisions and paths. It does
not rediscover candidates, rerun the 181,440-decision population, change
strategies, optimize a grid, identify real exchange latency, or replace the
frozen Issue #187 classifications.

The four cells are fixed:

| Cell | spread / slippage / fee bps | latency | provenance |
| --- | --- | --- | --- |
| `C0L0` | 2 / 2 / 10 | 0 seconds | immutable Issue #187 BASE reference |
| `C1L0` | 4 / 4 / 20 | 0 seconds | new only under a later exact-SHA grant |
| `C0L1` | 2 / 2 / 10 | 1 second | new only under a later exact-SHA grant |
| `C1L1` | 4 / 4 / 20 | 1 second | immutable Issue #187 COMBINED_STRESS reference |

The old `ResearchAllFillReplayRunner.run()` facade still accepts only its two
legacy scenarios. The factorial path opts into a private result factory while
using that same orchestration, family replay, Backtest execution, recovery and
PositionAccounting authority. Imported diagonal cells are structurally
non-executable. Existing legacy schemas, seals and protocol hashes are not
reinterpreted as factorial evidence.

## Frozen input and complete matrix

The retained input is exactly:

- source `weex-research-v2-last-18912cf7e7f97d518a74551f`;
- dataset `weex-research-v2-last-90d-issue-179-combined-v2`;
- C2 manifest `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`;
- Issue #187 artifact index
  `677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`;
- Issue #187 projection file
  `46d7e10899f06f4d16b6ded46f8cba5c62d9d25c119d05e35ca11b5af5833667`;
- original protocol
  `ab78c022616bcffdb7af8b835064a14409e1d47bdc3e31649192de9cf0c63e1c`;
- original task plan
  `03376c954ca51ca50754d925189cad7be55f14249cfec29e6cbbe710a1fcfca1`;
- diagnosis-v3 manifest/summary
  `dd7da381670944844a46e46ee23bcb540707f7e466120c7dedcf493cd294e637` /
  `8b9f614655adc5481eb6329a0e9d1ff7d7b757fe6d15ba6f606c5ff92540486d`.

The inert preflight verifies every indexed population, partition and authority
artifact, validates the actual task/decision/scenario joins, derives the 246
admitted identities and original pre-outcome 168-hour mask, and freezes their
digests. Diagnosis tables are only a cross-check; they are not execution
orders. The full controls are 246 admissions, 492 imported diagonal records,
492 new off-diagonal outcomes and 984 matrix records. Zero-admission family
tasks remain explicit.

Execution later consumes a bounded retained-task view rather than constructing
the stored full decision-population authority. The view validates the indexed
envelope, partition, complete chronology and source joins as inert evidence,
then reconstructs only selected sealed admissions and the already-computed
contexts/candles required by exit and accounting authorities. It never repeats
entry strategy/Risk/Safety population evaluation. The two new cells share one
task-level parse; zero-admission tasks emit an explicit empty artifact without
constructing a population.

The accepted data-only preflight report is
`data/research_evidence/issue-191-factorial-preflight-fc3fc5096350d087/input-plan.json`
(489,029 bytes; SHA-256
`299ebc3b4b920ff78b382fa56efb86dc6975c4f0176f8803486a6cca13e95cd2`).
Its immutable identities are:

- input plan `f57577ca785ce3f97a4d35d85b039219e10f179c029336640f4a44db168a0b5b`;
- admitted inventory `50e809059022b0093d1874dca4d5cc9f8e1f8013366df499650ad723e6289307`;
- original mask `21fd62428c2d76d0f6800dfad30c4396c5096e660b186b81a8bcb201a11c12d7`;
- imported diagonals `29d745efb3c471eeded66be1167e298e41d11c3b8484d771925741249351207d`.

The separate manifest SHA-256 is
`d006b6e5f19bc990efa4010ea9ece668a7a69fe24b4ba93688935f76995daaba`.
These facts were derived from the pinned Issue-#187 bytes without invoking a
strategy, Feature/Regime engine, simulator, or PnL calculation.

No grant, UUID or ledger ships in this PR. A later grant must bind a clean
reviewed executable SHA, this protocol, the exact input-plan/admission digests,
the accepted source/dataset/manifest and one new UUID. Synthetic input cannot
claim accepted-C2 provenance.

## Mandatory staged execution

The entire ten-member experiment, all thirty task identities, admitted-ID
inventory, original mask, factors, cutoffs, output rules and executable must be
frozen before the first Stage A task starts.

### Stage A — fixed BTC/ETH prefix

Stage A contains BTCUSDT and ETHUSDT, all six family tasks, both missing cells
and their imported diagonal references:

- 48 admissions;
- 96 new off-diagonal outcomes;
- 192 four-cell matrix records;
- two explicit zero-admission Range tasks.

After all twelve task/cell units complete and verify, the ledger records one
`PAUSED_AFTER_STAGE_A` checkpoint. This is a successful intentional pause, not
an ambiguous `STARTED` unit, failure, completed ten-member study or economic
classification. Checkpoint continuation is based only on technical validity
and resource feasibility. Positive PnL, a preferred sign or factor attribution
is never a continuation condition.

The checkpoint retains expected versus actual coverage, identity bindings,
terminal/open/unfilled/recovery states, artifact hashes and timing. It separates
initialization/parsing from result execution and verification where available.
Any remaining-time estimate must be qualified: two assets have unequal family
and admission workloads and are not a reliable linear ten-asset benchmark.

### Stage B — untouched eight-asset suffix

Stage B contains SOLUSDT, XRPUSDT, ZECUSDT, ZAMAUSDT, BCHUSDT, BTWUSDT,
AVAXUSDT and DOGEUSDT:

- 198 admissions;
- 396 new off-diagonal outcomes;
- 792 four-cell matrix records;
- seven explicit zero-admission Range tasks.

It requires a separate architect authorization binding the same executable,
protocol, input plan, admitted inventory, UUID and exact Stage A checkpoint.
Only untouched suffix units may run. Stage A cells are reused and cannot be
executed twice. A changed identity, failed/ambiguous unit, missing checkpoint,
replacement UUID, or attempt to continue without the authorization fails
closed.

## Evidence and reporting

Every new result uses the versioned factorial result seal and lossless stored
decision/candle references. Offline reconstruction validates the exact
decision, cell costs/latency, family replay, entry intent, fills, branch
accounting, recovery, timestamps and terminal facts. Imported results retain
their original artifact/result hashes. Full histories are referenced once per
task rather than copied per decision/cell.

The shared all-fill relation validator remains authoritative for the bound
execution snapshot, ordered Trend branch topology, ordinary exits, causal
recovery, accounting and terminal status. The factorial envelope owns only its
separate cell namespace and seal; it cannot relabel a result, omit a hold-only
branch, substitute an unrelated unfilled record or change the exact t+168h
cutoff.

For a decision closed in all four cells, report exact local-Decimal contrasts:

- cost at L0: `Y10 - Y00`; cost at L1: `Y11 - Y01`;
- eligibility at C0: `Y01 - Y00`; eligibility at C1: `Y11 - Y10`;
- interaction: `Y11 - Y10 - Y01 + Y00`.

Open or unfilled results stay unavailable for closed-R contrasts and are never
replaced by zero or forced closed. Reports retain all admissions, status
transitions, fees, realized accounting and per-asset residual base units, plus
the original fixed mask and asset/P1–P3/week/side zero cells. Overlap,
closure-selection and correlated-market warnings remain explicit. A Stage A
checkpoint cannot produce a completed-ten-member classification, promote a
strategy or replace Issue #187.

## Commands and explicit non-authorization

The preparation-safe command is:

```console
python -m scripts.research.research_v2_execution_factorial preflight --repo .
```

`run-stage-a` requires a future external grant. `continue-stage-b` additionally
requires a future checkpoint-bound continuation authorization. No such files
are committed. Both execution paths use a network-socket guard and an external
append-only ledger. A STARTED/FAILED unit is never retried.

After Stage A, `verify-stage-a` reconstructs every new result from its indexed
decision and bounded candle slice, verifies accounting/evidence, checks all 96
imported diagonal identities, and confirms the ledger is at the clean paused
boundary. It performs no execution and grants no continuation. Only after an
authorized Stage B completes may `finalize` reconstruct all new results, join
the two immutable Issue-#187 diagonals, write the four-cell projection, and
seal the ledger. Neither verifier is an independent replication or a new
economic run. `continue-stage-b` first reopens the canonical checkpoint,
verifies its digest, 48/96/192 denominators and reconstructed prefix, and
reconciles retained grant/source/input metadata. `verify-finalized` performs
the same bounded reconstruction against the saved projection and FINALIZED
ledger without writing, appending a ledger event or replaying imported
diagonal outcomes.

This annex authorizes no historical pilot, market/API request, account access,
Paper/Shadow/Live activation, tuning, new asset, news labeling or deployment.
