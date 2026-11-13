# Issue #237 bounded comparison — technical blocker evidence

Status: `INCOMPLETE_TECHNICAL_BLOCKER`  
Promotion: `NO_PROMOTION`  
Economic comparison: **unavailable; do not interpret partial BTC rows as a study result**

## Frozen identities

- Dependency: `d1772874cc1414a192e7b94fc296b27cf04993dc`
- Executable: `c956bfb41659404f88a87f23022b4b47cb8359e6`
- Executable tree: `3c61a332cebbeba2336c22a407faecc161dcdb72`
- Protocol: `BTC_ETH_RASCHKE_PULLBACK_FIB_ABLATION_15M_V1`
- Protocol SHA-256: `933e892b9ab448a0e906093e40e22d3d9237b72963627c9a3dde4b2be1ceb402`
- Run UUID: `e6a0bf8d-6fe4-45e3-9382-4e5e8c69192f`
- Historical invocations: **one**

The protocol file retains the accepted manifest, report, plan and BTC/ETH shard hashes, the explicit
October 14 endpoint, three arm identities and cost/admission assumptions.

## Actual outcome

The one authorized command exited `1` after 59.4202105 seconds. The owned process tree peaked at
275,308,544 bytes, below the 1-GiB ceiling; neither timeout nor memory enforcement fired. No retry,
saved-row export, ETH continuation or economic classification was performed.

The command durably wrote 174 BTC scenario rows covering 53 unique reference opportunities:

| Arm | BASE rows | STRESS rows | Ordinary closed | Invalid-geometry recovered |
|---|---:|---:|---:|---:|
| REFERENCE | 53 | 53 | 103 | 3 |
| FIB_ZONE | 17 | 17 | 33 | 1 |
| ROUND_ZONE | 17 | 17 | 33 | 1 |

These are incomplete technical artifacts, not an economic table: ETH was never processed, member
completion was not committed, and no final projection exists. The last durable heartbeat is earlier
than the final row append and is preserved rather than rewritten.

## Concrete blocker

After BTC row generation, the cross-arm authority validator compared four Decimal fields. Five
STRESS rows had shared execution status `INVALID_GEOMETRY_RECOVERED`; the shared executor correctly
represented `result.target_price` as null in that recovered result. The validator attempted
`Decimal(str(None))` for `frozen_target_price` and raised `decimal.InvalidOperation` before member
or final report publication. The original frozen target remains in each row's strategy audit, but
the failed executable bound the validator to the nullable result field.

This is a reporting/validation integration defect. It is not evidence for or against REFERENCE,
FIB_ZONE or ROUND_ZONE economics, and it does not authorize patch-and-rerun. The exact traceback,
owner/exit telemetry, partial protocol/identity/heartbeat and losslessly compressed rows are retained.

## Retained payload identity

- Raw `trade-rows.jsonl`: 624,742 bytes; SHA-256
  `f261e385d261433dee1af9ed5a40a03684ce35d32522e4c11c46aa6c2f745fca`
- Compressed `trade-rows.jsonl.gz`: 49,266 bytes; SHA-256
  `7c62c0db5e9da5af9a18cb3d87208358a67ee6eb91005ba65d83d1c3a06ade20`
- Compression is lossless; the raw file remains in the exclusive external run root.

All predecessor evidence directories, `docs/project_state.md`, CI sharding/workflow behavior,
runtime registries and accepted strategies are unchanged.
