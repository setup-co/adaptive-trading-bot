# Issue #193 — Research V2 factorial Stage A checkpoint

Status: `PAUSED_AFTER_STAGE_A` and read-only checkpoint verification passed.

This is immutable checkpoint evidence from the one authorized Stage A execution. It is not a completed ten-member experiment, an economic classification, an independent replication, or authorization for Stage B.

## Bound authority and inputs

- Executable SHA: `7711a871bdf005c72a9033690527fc6920eea737`
- Executable tree: `39f67f44ecbddce8279becf457c2046196dd83f2`
- Run UUID: `01df2c66-92ce-459f-aede-78eaa82a3f52`
- Grant canonical SHA-256: `bbc0c864e4b0ffdae5939273045196ab42edcd52d44d02a6b76e235861b07050`
- Grant file SHA-256: `bd1ed263af54224b2c71cb8cc9aa43b620b2d101a6843033b241cd5834cb82fa`
- Protocol SHA-256: `470f4ba70d967c62b0ec5e777d876a926a8f204c471b5a28a6b12e33cb05160c`
- Input-plan SHA-256 declared by the grant: `aa1df44692d8cad8aa966c2e23c0c7d3772de8a829451c8a76f0bee89f83c792`
- Accepted input-plan file SHA-256: `e9fecb2308e88900412e977fa55bfa266f2f1494c67359cc2f2aac27db5bec47` (1,157,695 bytes)
- Admitted-inventory SHA-256: `f9643e5f84ed131c9ee7e704e36cd00c2b0c413876cffd297988f3a4730c5968`
- Source: `weex-research-v2-last-18912cf7e7f97d518a74551f`
- Dataset: `weex-research-v2-last-90d-issue-179-combined-v2`
- Dataset manifest SHA-256: `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`
- Imported diagonal identity SHA-256: `534565766e304623468a22f5d617c8fab310f2f7495e9aa0392ba449140d97d5`
- Original #187 artifact index SHA-256: `677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`
- Original #187 projection SHA-256: `46d7e10899f06f4d16b6ded46f8cba5c62d9d25c119d05e35ca11b5af5833667`

## Coverage and terminal states

Expected and actual Stage A coverage match exactly:

- 48 original admissions.
- 12 completed task/cell units: six BTC/ETH family tasks, each in `C1L0` and `C0L1`.
- 96 newly simulated off-diagonal outcomes.
- 96 reused diagonal records.
- 192 total matrix records represented by the checkpoint.
- 95 `ORDINARY_CLOSED`, 1 `RECOVERY_CLOSED`, 0 open, and 0 unfilled new outcomes.
- Four explicit zero-admission Range units, each retained as a 20-byte empty gzip artifact.

| Asset / family | Admissions | New outcomes | Terminal states |
|---|---:|---:|---|
| BTCUSDT / Trend Pullback | 21 | 42 | 42 ordinary closed |
| BTCUSDT / Breakout + Retest | 1 | 2 | 2 ordinary closed |
| BTCUSDT / Range Mean Reversion | 0 | 0 | explicit zero-admission units |
| ETHUSDT / Trend Pullback | 23 | 46 | 45 ordinary closed; 1 recovery closed |
| ETHUSDT / Breakout + Retest | 3 | 6 | 6 ordinary closed |
| ETHUSDT / Range Mean Reversion | 0 | 0 | explicit zero-admission units |

The checkpoint has 12 prefix units `COMPLETED`, 48 suffix units `NOT_STARTED`, no `STARTED` or `FAILED` unit, no pending file, no Stage-B authorization, and no `FINALIZED` event. Stage B remains unexecuted regardless of partial economic facts.

## Execution and verification

- `run-stage-a` was invoked once: `2026-10-18T16:38:56.9848916Z` to `2026-10-18T16:55:59.1201153Z`; exit code 0; observed wrapper wall time 1,022.1352237 seconds.
- `verify-stage-a` was invoked once: `2026-10-18T16:56:17.9521287Z` to `2026-10-18T17:08:56.8810924Z`; exit code 0; observed wrapper wall time 758.9289637 seconds.
- Verifier disposition: `STAGE_A_VERIFIED_PAUSE; ARCHITECT_AUTHORIZATION_REQUIRED_FOR_STAGE_B`.
- Checkpoint internal digest: `bbad1f0396e8d2ccdcbeb75fab061dac52214927bdbef7f7ae04095e98d61ff1`.
- Checkpoint file SHA-256: `70d49ced0fb70db96b4d00c6f520afa40d3622773a9df48705cf528e1b54f1b0` (7,005 bytes).
- The read-only verifier reconstructed and validated the new result authorities. It was not an independent replication and did not repeat the original diagonal simulations or the full #187 accounting verification.

Runner-recorded resource observations:

- Input/preflight initialization: 607.109 wall seconds; 601.375 CPU seconds.
- New result execution: 253.625 wall seconds; 251.78125 CPU seconds.
- In-run verification: 159.937 wall seconds; 157.921875 CPU seconds.
- New artifact bytes: 1,222,545.
- Free disk before execution: 156,821,778,432 bytes.
- Total/free memory before execution: 33,271,864 / 18,628,784 KiB.
- Maximum periodically observed Python working set: 3,419,721,728 bytes; this is not a measured peak.
- Peak CPU and memory measurements are unavailable.

No network, market-data API, exchange-account, order, trading, deployment, Stage-B, or finalization activity occurred. No Stage-B runtime estimate is asserted because Stage A is an unequal two-asset prefix and initialization, family/admission workload, parsing, and verification do not scale uniformly.
