# Issue #231 corrected failed-breakout successor result

Status: **TECHNICAL_BLOCKER / INCOMPLETE_ECONOMIC_EVIDENCE**

Issue #231 authorized one fresh execution of the accepted corrected code. The
run used its own UUID and output root; it did not resume, combine, or reinterpret
the failed Issue #229 prefix. The only successor invocation completed both
member evaluations and saved compact rows, but failed while serializing the
final projection. There is therefore no complete economic table and no valid
positive, negative, mixed, fragile, or insufficient-evidence classification.

## Frozen identities and actual execution

- Executable: `cef2384568e8c007ad20139bbd736382f32002b7`
- Executable tree: `071038a19fb7d2d4258029c2ba468e98a7fa74e8`
- Protocol: `BTC_ETH_FAILED_BREAKOUT_REVERSION_15M_30M_V1`
- Protocol SHA-256: `d9bfde677b59fed95dede0eadacc0d19de716a2bd8159458988d01b65827f424`
- Run UUID: `5708360d-0b81-437d-a888-6956bda16ed6`
- Successor historical invocations: `1` of `1`
- Cumulative historical invocations including #229: `2`
- Child/wrapper exit: `1` / `1` (`CHILD_EXIT`)
- Elapsed wall time: `48.713` seconds
- Timeout / resource-limit stop: `false` / `false`
- Peak recorded process-tree working set: `345,272,320` bytes
- Retained compact scenario rows: `590`
- Final projection / complete marker: unavailable / unavailable

The external wrapper enforced the authorized 3,593-second total ceiling with a
3,588-second child termination threshold and a 1-GiB process-tree limit. A tiny
non-market wrapper check demonstrated deadline termination before the historical
command. A preceding shell-path lookup failure started no child and is retained
separately. The production command then ran exactly once.

## Concrete blocker

After both member reports and all compact scenario rows were written, the
caller failed at final projection serialization:

```text
TypeError: Object of type Decimal is not JSON serializable
```

The traceback is retained in `logs/stderr.log`. `projection.json` and
`complete.json` do not exist. No retry, recovery calculation, projection
reconstruction, source patch, replacement UUID, or budget reset was performed.

## Saved partial mechanics

These are retained mechanics counts, not a completed economic result:

| Member | Observations | Raw setups | Cost rejected | Risk rejected | Admitted | Spaced admissions |
|---|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 17,280 | 1,300 | 1,194 | 7 | 99 | 76 |
| ETHUSDT | 17,280 | 1,269 | 1,066 | 7 | 196 | 160 |

| Member/scenario | Ordinary closed | Invalid-geometry recovered | Unresolved |
|---|---:|---:|---:|
| BTCUSDT BASE | 99 | 0 | 0 |
| BTCUSDT STRESS | 95 | 4 | 0 |
| ETHUSDT BASE | 196 | 0 | 0 |
| ETHUSDT STRESS | 185 | 11 | 0 |

Saved trigger counts are:

- BTC BASE: 47 stop intrabar, 3 target intrabar, 49 time exit.
- BTC STRESS: 4 invalid-geometry recovery, 39 stop intrabar, 6 stop open
  gap, 3 target intrabar, 47 time exit.
- ETH BASE: 85 stop intrabar, 7 target intrabar, 104 time exit.
- ETH STRESS: 11 invalid-geometry recovery, 73 stop intrabar, 4 stop open
  gap, 6 target intrabar, 102 time exit.

Mean/median approved-risk R, profit factor, average win/loss, gross PnL,
commission, modeled net, FULL/SPACED aggregates, chronological blocks,
concentration and the protocol classification are unavailable because the
authoritative projection was not successfully serialized. This report does
not infer them from the incomplete publication boundary.

## Preserved predecessor and limits

Issue #229 remains a distinct failed attempt: executable
`b25a5dc9f69e45b58713fed7971273109d9520fe`, UUID
`d2ed2be1-6c9b-47ea-a56e-eb7938a758aa`, exit 1 after 6.474 seconds, 61 partial
BTC rows and ETH not started. None of those rows are included in this run's
590 rows. Both attempts are technical/incomplete evidence, not evidence for or
against profitability. Funding and other unavailable perpetual mechanics
remain `NOT_MODELED`; no trading authority follows.
