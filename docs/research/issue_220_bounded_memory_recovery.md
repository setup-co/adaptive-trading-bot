# Issue #220 bounded-memory recovery

Protocol: `ISSUE220_BOUNDED_MEMORY_RECOVERY_V1`

Status: implementation and synthetic equivalence proof complete; retained-evidence
construction requires the separately frozen recovery executable.

This route is post-processing only. It does not evaluate either Breakout strategy,
run a historical simulator, admit a decision, mutate/finalize the original ledger,
or infer the missing historical-process exit code. The original simulation remains:

- executable `d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e`;
- UUID `ec10ca5d-01e6-436f-8153-fe9c5b5439b5`;
- ledger revision `20`, ten tasks `COMPLETED`, no projection/finalization event;
- historical command invocations `2`; and
- attempt-02 exit `UNKNOWN_NOT_RECORDED`.

## Read-only construction contract

The adapter validates the accepted 113-file artifact index, original-inventory
identity, blocked-recovery identity, revision-20 event, ledger chain, task IDs and
all committed population/result/report digests. It makes a byte-identical external
mirror named with the original UUID and reads that mirror without ledger writes.

For each task, the adapter incrementally decompresses canonical JSON. It validates
one typed context, decision or result at a time, including nested integrity seals,
cross-arm decision authority, all-fill authority, exact task/population/result
associations, complete unique arm/scenario/slot coverage, and the saved task report.
Only compact decision identity/provenance and running reducers survive a task.
Task reports are incorporated as already-validated canonical bytes, avoiding a
second in-memory copy of their large diagnostic tables.

The output projection preserves the exact frozen `final_projection` schema,
Decimal arithmetic order/precision, full/masked metrics, paired partitions and
screen classifications. Construction and independent verification each reconstruct
the projection from the source authorities. They write only outside the original
ledger. The process measures both resident working set and Python allocations and
fails closed above `2 GiB`.

## Synthetic evidence

The same typed synthetic task path proves byte-for-byte equality with the frozen
`task_report` and `final_projection` authorities. Additional tests cover:

- exact streamed metrics versus frozen metrics;
- meaningful multi-asset/week classification dependence;
- LFS-pointer/foreign, truncated, corrupt and report-mismatch rejection;
- distinct decision/source identities and complete cells; and
- a generated scale stream whose retained allocation is bounded rather than
  proportional to the full uncompressed input.

The tests make strategy evaluation and task execution fail if called by recovery.
The real retained authorities must not be processed until this implementation is
committed, its exact SHA is frozen, and the checkout is clean.

## Result interpretation

Only successful construction followed by successful independent verification may
emit `RECOVERED_TASK_EVIDENCE_VERIFIED_ORIGINAL_EXIT_UNKNOWN`. That status verifies
the retained task evidence and reconstructed report; it does not backfill a
`FINALIZED` ledger event or prove the original process exit. If either operation
fails, its external logs and numeric exit are preserved and the result is a concrete
recovery blocker.
