# Issue #203 — Breakout zone-comparison recovery evidence

Status: `FINALIZED_ARTIFACTS_VERIFIED`, with the original simulation process exit retained as `UNKNOWN_NOT_RECORDED`.

This is evidence from the single architect-authorized recovery execution after the Issue #202 host reboot. It is not an independent market replication, does not turn the repeated prefix into new samples, and does not establish promotion eligibility.

## Bound identities

- Frozen executable: `b332eb83f392819365f3b667496fb6b01a3810ef`
- Frozen tree: `94a074615ff296208d177ae832dfd101d0031bbe`
- Protocol: `1a6c0ca3c82aec8d88fcfef367d34a65731acbf134cc7b727209327f8127c119`
- Task plan: `664cf4189def82392bd86d85879970bd6266c1bbd8217fd7e609620fc66e1a55`
- Source: `weex-research-v2-last-18912cf7e7f97d518a74551f`
- Dataset: `weex-research-v2-last-90d-issue-179-combined-v2`
- Dataset manifest: `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`
- Pinned #187 artifact index: `677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`
- Interrupted UUID: `4d8cabd6-bd43-480c-a5dd-ab1ccf83b2bf`
- Recovery UUID: `1bce148f-e5d0-4706-b440-85ee25d3ced2`

## Recovery disposition

The recovery ledger contains ten `COMPLETED` tasks and revision 21 `FINALIZED`, with no started, failed, missing, pending or lock state. The retained projection has 60,480 observations, 29 baseline candidates and 29 zone candidates. Its frozen classification is `DESCRIPTIVE_COMPLETE_PROMOTION_INELIGIBLE`; the 29-candidate ceiling and non-promotion interpretation remain unchanged.

The committed projection file is 996,738 bytes with SHA-256 `6bede98fcb29c4093469dcc225e44d81db5301afb78448f681b48bf69a6e2e88`. That exact file hash is stored by the FINALIZED event. Event `0021.json` has SHA-256 `57f0e3924f28aae0eb0b9981ada5993a151ea8a309703bad6ea45a6f906f9150`.

The launcher did not retain `run-exit.json`. Therefore:

- simulation exit code: unavailable;
- simulation exit status: `UNKNOWN_NOT_RECORDED`;
- FINALIZED does not prove the missing process exit;
- verifier success below does not backfill or prove that exit.

## Read-only verification

The existing verifier was invoked once with the recorded absolute interpreter, frozen checkout and existing ledger. It ran from `2026-10-23T10:57:56.9507362Z` through `2026-10-23T15:18:15.6743316Z` (15,618.7235954 seconds) and exited 0. Stderr is empty. The verifier reconstructed the retained task authorities/reports and matched the saved projection; this validates the current retained evidence, not an independent simulation.

All 53 ledger/projection/task files have identical pre-verifier and post-verifier SHA-256 values. The verifier did not rewrite evidence.

## Interrupted-prefix reconciliation

The interrupted attempt completed tasks 0–4 and left task 5 STARTED at the reboot. The recovery did not splice those bytes into its new ledger. A bounded, parser-only comparison reconciled the original completed prefix to recovery tasks 0–4 after excluding only declared run-provenance consequences: run UUID, integrity seals and their population/report digests, plus non-semantic gzip container bytes.

For all five tasks:

- decision populations match;
- all 24,192 result rows per task match in authoritative decision, execution and economic facts;
- task reports match;
- raw files retain their distinct run identities and hashes.

The repeated computation is reproducibility evidence only and is not counted as new market evidence.

## Historical hash-observation limitation

Two earlier host observations reported different raw SHA-256 values for both the projection and `0021.json`. The earlier raw bytes or a contemporaneous command transcript were not retained, so the discrepancy cannot be attributed reliably to a path mistake, digest-kind mistake, incomplete observation or file rewrite. It remains `UNRESOLVED`.

Current ledger-chain integrity, the FINALIZED projection-file hash, repeated stable raw hashes, the official verifier and unchanged post-verification hashes all pass. These facts validate the current retained content but do not prove an undocumented immutability history. The two observations are preserved verbatim in `lineage/hash-observations.json`.

No simulation rerun, replacement UUID, strategy change, tuning, market/account access, order, trading activation or deployment occurred during verification and publication.
