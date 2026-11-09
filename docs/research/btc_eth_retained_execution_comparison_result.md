# BTC/ETH retained-decision comparison: bounded technical blocker

The one authorized Issue #225 real-input attempt used run UUID
`e3aac506-9704-4afe-8bec-d22795835f0c`, model integration
`90101c22d9465ba2fb41eedb3164ac0285c8b9e3`, and frozen driver commit
`d8cea515ad5bb9dd7beea31da1cfd77451b1bc2b`.

It exited `1` after 2.075996 seconds with peak recorded working set 2,740,224
bytes. During `FREEZE_INPUT_INVENTORY`, the retained Issue #220 publication
inventory did not yield one unique committed BTCUSDT task event to the bounded
selector. The terminal classification is `INCOMPLETE_TECHNICAL_BLOCKER`.

No retained result stream was decoded (`0` records), no canonical 5m Parquet
was read, no new execution profile was evaluated (`0` trades), and neither
BTC nor ETH completed. Consequently the requested old/new economic table,
paired deltas, full/masked statistics and economic classification are
**unavailable**. This is not a negative, mixed, or positive strategy result.

The attempt is terminal under the declared no-retry/no-patch-and-rerun rule.
The original Issue #220 evidence, its unknown original exit, strategies,
decisions, costs, calendar, corpus, accounting and runtime boundaries remain
unchanged. The compact retained records and hashes are under
`data/research_evidence/issue-225-btc-eth-execution-comparison-d8cea515/`.
