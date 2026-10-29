# Issue #220 immutable stopped-run evidence

This bundle preserves the complete revision-20 task evidence and the bounded
offline recovery assessment for executable
`d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e` and UUID
`ec10ca5d-01e6-436f-8153-fe9c5b5439b5`.

- `original-ledger/`: 21 original events and 30 original task artifacts.
- `execution/`: both grants, both historical attempt records, helper/origin
  records, wrapper sources, preflight records and the preserved missing-exit
  evidence.
- `recovery/`: original inventory, frozen-loader validation, resource
  diagnostics, owner probes, all diagnostic attempts and the accepted blocker
  status.

The original attempt-02 exit is `UNKNOWN_NOT_RECORDED`. The ledger remains
revision 20 and unfinalized. Recovery construction and projection verification
were not started because the retained raw result JSON alone exceeded available
commit headroom. Aggregate classification is
`AGGREGATE_CLASSIFICATION_UNAVAILABLE_UNVERIFIED`.
