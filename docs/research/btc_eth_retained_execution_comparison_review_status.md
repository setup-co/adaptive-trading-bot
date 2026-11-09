# BTC/ETH retained-decision comparison: post-attempt clarification

The immutable Issue #225 publication records the first and only real-input
attempt exactly as it was observed. Its terminal status remains
`INCOMPLETE_TECHNICAL_BLOCKER`; no historical comparison was retried.

Subsequent offline review identified the immediate driver defect: the failed
reader searched for `TASK_COMPLETED`, while the retained ledger schema records
successful tasks as `COMPLETED`. Selecting zero matching events therefore did
not establish duplicate, missing, or corrupted BTC evidence.

The corrected reader validates the indexed ledger bytes and hash chain, uses
the actual event schema, distinguishes missing from duplicated completion
events, and binds result/report population identities to the committed task.
Its complete CLI/core path is covered only with synthetic retained authorities
and canonical-schema Parquet fixtures. This correction does not reset the
budget, create a new UUID, or authorize a second historical attempt.
