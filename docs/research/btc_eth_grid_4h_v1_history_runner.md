# BTC/ETH Grid V1 guarded one-shot runner

## Status

This Issue #247 interface is **IMPLEMENTED / SYNTHETICALLY VALIDATED / HISTORICAL EXECUTION
BLOCKED**. No architect-approved historical grant or run UUID is included. The inert study plan
continues to say `historical_execution=NOT_AUTHORIZED`; this runner does not change that file.

The runner is stacked on accepted preflight HEAD
`bbe6a33b5ca7f20537a23e5a050d81a454f6f7c3`, accepted engine HEAD
`dc450a4a3a0f2dfe114a690459971bc79d2aba68`, protocol
`BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` / `0.2.0-proposed`, and accepted v3 manifest/report
identities. It adds no runtime registry entry, network client, exchange submission path, Paper/Live
activation, or historical result.

## Grant and admission boundary

`scripts.research.grid_history_runner` accepts genuine retained history only after reading a
canonical immutable external grant **and** a detached Ed25519 approval over those exact canonical
grant bytes. The trusted architect public key must already be pinned in the reviewed executable;
the approval key ID, grant digest and signature are all verified offline. Issue #247 deliberately
pins no key, includes no approval and therefore cannot execute accepted history. A self-authored
`EXTERNAL_ARCHITECT_APPROVED` label is not authority.

The grant binds the reviewed executable commit and tree, study-plan, preflight, engine, protocol
document, parameters, accepted calendar bytes, v3 manifest/report/dataset, fixed task order, modes,
one UUID, zero network requests, concurrency one, and a positive fixed wall budget. Before any
accepted adapter value read, the CLI also requires an entirely clean Git checkout, exact HEAD/tree,
and byte-for-byte agreement between Git blobs and the Python modules actually imported from that
checkout. Dirty/untracked files, another editable installation or a mismatched module origin fail
closed. A synthetic grant requires separate fixture-only identities and cannot carry external
approval provenance.

The narrow `GridHistoricalSourceAdmission` receipt preserves the engine's original synthetic-only
guard: non-synthetic facts require the exact source/executable receipt, while synthetic geometry
and fixtures remain confined to `synthetic-*` identities. Risk, Safety, plan authorization,
execution ordering and accounting remain the shared Grid engine authorities.

## Durable one-shot ledger

The ledger creates a new directory named by the grant UUID and refuses an existing directory. Its
canonical events are hash-linked and append-only, use exclusive transitions and record:

- exact grant/executable identities and preflight artifact;
- `NOT_STARTED`, `STARTED`, `COMPLETED`, `FAILED` or `UNKNOWN` for the four frozen tasks;
- every calendar row in order, including metadata exclusions without value reads;
- each complete engine report, source-file hashes, carry/report identities, dispositions, fills,
  funding, fees, equity, drawdown, forced close, termination and exclusion facts;
- immutable task reports, final projection and actual command exit record.

A `STARTED` task is never resumed. Missing, altered, divergent or out-of-order evidence makes the
task `UNKNOWN` or fails the invocation; it is not converted to a flat week. All 356 Mondays per
symbol must be committed before a task can complete. The fixed external wall budget and task
concurrency one are enforced by the runner.

## Reporting contract

Each task report retains explicit scheduled, eligible, excluded, no-trade and terminated
denominators, fill weeks, fill/forced-close counts, gross realized PnL, fees, funding, equity,
partition opening/closing capital and running-peak drawdown. Drawdown is derived from every saved
`MINUTE_MARKED` equity/peak observation, not the recovered terminal weekly snapshot; the exact
completed-minute count must reconcile. Metadata exclusions break bootstrap runs and never become
zero observations.

The validation BASE lower bound uses overlapping four-week blocks wholly inside contiguous
eligible runs and exact NumPy `2.3.5` `PCG64(241)` initialization. Sampling is frozen as
`Generator.integers(low=0, high=exclusive, size=(10000, ceil(n/4)), dtype=uint64)`, with 10,000
draws, four-week blocks and sorted lower index 249. Raw and bounded-integer golden vectors plus the
bootstrap output are regression-locked; ambient Decimal precision cannot change the result.

The combined report applies the frozen validation and retrospective gates across BASE/STRESS for
each symbol and emits exactly one of `TECHNICAL_OR_INTEGRITY_INVALID`, `INSUFFICIENT_EVIDENCE`,
`NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION` or
`CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION`. Even the final label has
`trading_authorization=false`; synthetic output supports no economic or trading inference.

## Synthetic validation and future operator route

Synthetic fixtures exercise the actual CLI, source admission, four-task progression, continuous
carry, excluded rows, task/report serialization and read-only final verification. Existing focused
engine/preflight tests cover funding order, incomplete/out-of-order minutes, stop/termination,
no-trade carry, calendar masks and source corruption. The test route is:

```text
python -m scripts.research.grid_history_runner --repo <checkout> synthetic-run \
  --grant <synthetic-canonical-grant.json> --fixture <fixture.json> \
  --ledger-root <new-empty-ledger-root>
python -m scripts.research.grid_history_runner --repo <checkout> verify \
  --ledger <ledger-root>/<synthetic-run-uuid>
```

The future retained-history route is deliberately **BLOCKED pending a separately published exact
SHA/tree grant and unique run UUID**. If that later authority exists, an operator must use a clean
checkout at the named commit, the exact external grant, accepted v3 root and a new external ledger
root:

```text
python -m scripts.research.grid_history_runner --repo <exact-clean-checkout> run \
  --grant <external-architect-grant.json> --approval <detached-approval.json> \
  --data-root <accepted-v3-root> \
  --ledger-root <new-external-ledger-root>
```

The read-only `verify` command checks hash-chain, identities, saved artifacts, task reports and
classification reconciliation. It explicitly does **not** replay the strategy or independently
replicate economics. It is a separate later operation and must never be treated as authority to
run, retry, resume, repair, merge, deploy or trade. No such command was invoked against retained
history in Issue #247.
