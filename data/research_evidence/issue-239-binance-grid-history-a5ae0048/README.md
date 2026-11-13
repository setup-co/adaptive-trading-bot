# Issue #239 retained compact evidence

The full raw and normalized corpus is retained outside Git at:

`C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset`

This directory contains exact compact copies of the frozen plan, all raw-resource hashes, both
qualification generations, the final coverage/gap report, command logs and numeric exit records.
It deliberately does not duplicate the 2.08 GB payload in Git.

## Identities

- Starting main: `2e787f3d53599bdb589e23a5d211c466b4577dd0`
- Capture executable: `0cad46fddd0d7557625c32117d59885f78400f73`
- Funding-parser correction executable: `a5ae0048f0efcccaf800042b0e0192c78e638893`
- Publication-report executable: `97b8a5ad3f6ff3717e24cddd18af58b710c9d7b5`
- Plan SHA-256: `ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace`
- Raw inventory SHA-256: `e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0`
- Original v1 qualification SHA-256:
  `d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79`
- Corrected v2 qualification SHA-256:
  `c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097`
- Publication report SHA-256:
  `10e76817db6662a51a9a8158b7661784e8236708fc98e4acae53c7b59248b241`

## Command outcomes

- Capture: exit 0; 1,312/1,312 resources; 1,312 attempts; 483,852,142 payload bytes.
- Original qualification: exit 0, retained as superseded because it rejected signed funding rates.
- Corrected v2 qualification: exit 0; reuses retained raw bytes; no network requests.
- v2 verification: exit 0, `VERIFIED`; 801 normalized partitions in the corrected generation.
- Compact report: exit 0; no strategy evaluation.

See `docs/research/btc_eth_grid_4h_external_history_result.md` for the readable result and limits.
