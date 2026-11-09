# BTC/ETH retained-decision comparison: bounded technical blocker

The one authorized Issue #225 real-input attempt used run UUID
`e3aac506-9704-4afe-8bec-d22795835f0c`, model integration
`90101c22d9465ba2fb41eedb3164ac0285c8b9e3`, and frozen driver commit
`d8cea515ad5bb9dd7beea31da1cfd77451b1bc2b`.

It exited `1` after 2.075996 seconds with peak recorded working set 2,740,224
bytes. During `FREEZE_INPUT_INVENTORY`, the failed driver looked for the
nonexistent event label `TASK_COMPLETED`; the retained ledger correctly uses
`COMPLETED`. It therefore selected zero BTCUSDT completion events. This was a
driver/schema mismatch, not evidence of duplicated, missing, or corrupted BTC
artifacts. The terminal classification remains `INCOMPLETE_TECHNICAL_BLOCKER`.

No retained result stream was decoded (`0` records), no canonical 5m Parquet
was read, no new execution profile was evaluated (`0` trades), and neither
BTC nor ETH completed. Consequently the requested old/new economic table,
paired deltas, full/masked statistics and economic classification are
**unavailable**. This is not a negative, mixed, or positive strategy result.

The attempt is terminal under the declared no-retry/no-patch-and-rerun rule.
The corrected offline reader now validates the canonical ledger hash chain,
uses the actual `COMPLETED` schema, distinguishes missing from duplicated
matches, and binds each result/report population identity to its committed
completion event. It was not used for a second real-input attempt.
The original Issue #220 evidence, its unknown original exit, strategies,
decisions, costs, calendar, corpus, accounting and runtime boundaries remain
unchanged. The compact retained records and hashes are under
`data/research_evidence/issue-225-btc-eth-execution-comparison-d8cea515/`.
