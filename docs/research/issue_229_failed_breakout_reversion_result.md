# Issue #229 failed-breakout reversion screen result

Status: **TECHNICAL_BLOCKER / INCOMPLETE_ECONOMIC_EVIDENCE**

This delivery implements and synthetically validates the fixed
`BTC_ETH_FAILED_BREAKOUT_REVERSION_15M_30M_V1` hypothesis, then records the
only authorized historical invocation. The invocation did not complete, so
the saved partial rows are not an economic result and must not be classified
as positive, negative, mixed, fragile, or insufficient evidence.

## Frozen identities

- Starting main: `3095cf54cd514a0e4cf71dac8c3e9787bfad4c6c`
- Executable: `b25a5dc9f69e45b58713fed7971273109d9520fe`
- Executable tree: `10591859a6ec249212449f80364a078805d74655`
- Protocol: `BTC_ETH_FAILED_BREAKOUT_REVERSION_15M_30M_V1`
- Protocol SHA-256: `d9bfde677b59fed95dede0eadacc0d19de716a2bd8159458988d01b65827f424`
- Run UUID: `d2ed2be1-6c9b-47ea-a56e-eb7938a758aa`
- Historical invocations: `1` of `1`; no retry was made.

The executable retained the exact accepted manifest, report, plan, BTC and ETH
shard identities declared by Issue #229. Its own input validation passed before
the first saved result.

## Actual invocation outcome

| Fact | Saved value |
|---|---:|
| Exit code | 1 |
| Elapsed wall time | 6.474 seconds |
| Timed out | false |
| Peak recorded process-tree working set | 271,659,008 bytes |
| Complete marker | unavailable |
| Member reports | unavailable |
| Projection/economic table | unavailable |
| Valid classification | unavailable |

The shared `PositionAccountingCore` rejected a result while closing a filled
position:

```text
PositionAccountingCoreError: fill batches must advance strictly; submit equal-time fills together
```

The frozen caller submitted the entry and a same-timestamp exit as separate
accounting batches. That is a caller/adapter integration blocker. It is not a
finding about the hypothesis and does not establish corrupted source data.
The executable was not patched and history was not invoked again.

## Preserved partial facts

`trades.jsonl` contains 61 completed scenario rows written before the failure:

| Member | BASE | STRESS | Total |
|---|---:|---:|---:|
| BTCUSDT | 31 | 30 | 61 |
| ETHUSDT | 0 (not started) | 0 (not started) | 0 |

The partial status counts are 60 `ORDINARY_CLOSED` and one
`INVALID_GEOMETRY_RECOVERED`; triggers are 42 `TIME_EXIT`, 18
`STOP_INTRABAR`, and one `INVALID_GEOMETRY_RECOVERY`. These counts describe
only persisted prefix mechanics. No means, profit factors, decision gates,
chronological blocks, concentration result, or economic comparison is valid
for the incomplete population.

Partial trades SHA-256:
`10593d551c1316eb698d1f8c8818bc6e58326e8926f4f065280132f5602832e5`.

## Validation completed before the screen

- Focused strategy/execution/actual-path tests: 40 passed in 1.08 seconds.
- Ruff format and lint: passed.
- Mypy on the four new/changed source/CLI modules: passed.
- The synthetic actual path used canonical Parquet and exercised both members,
  both scenarios, shared Strategy/Risk/Safety/authorization, the frozen target,
  30-minute deadline, spacing and immediate per-trade persistence.
- The owned deadline wrapper's final synthetic timeout check exited 124 and
  retained its partial file. Earlier helper-validation attempts are retained
  rather than rewritten.

All command, PID, resource, exit, traceback, wrapper-validation, protocol,
run-identity and partial-output bytes are retained under
`data/research_evidence/issue-229-failed-breakout-reversion-b25a5dc9/`.
Funding and other unavailable perpetual mechanics remain `NOT_MODELED`; no
runtime activation or trading approval follows from this implementation.
