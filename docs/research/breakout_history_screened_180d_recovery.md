# Breakout 180-day comparison: stopped-run recovery evidence

Issue #220 authorized one fixed ten-member, two-arm historical comparison at
executable `d09e50f3eea87a275b640ffd38c27ad8c0f8cb3e` and run UUID
`ec10ca5d-01e6-436f-8153-fe9c5b5439b5`. This document is a post-execution
recovery report, not a new study or a strategy result.

## Original execution facts

- Historical command invocations: **2**. Attempt 1 exited `1` before ledger
  creation because its displayed grant bytes were not canonical JSON. Attempt
  2 used the validated 540-byte canonical grant with SHA-256
  `f674cb1dffdd87801f51f2eba27ecebc8130497ea0c92f5ae8a0526659f4963d`.
- The frozen ledger loader validated the original ledger at revision **20**.
  All ten ordered tasks are `COMPLETED`; no task is failed, started or
  not-started.
- The ledger retains exactly 21 canonical events and all 30 committed task
  artifacts: population, results and report for each panel member. Every
  digest committed by the task completion events still matches its file.
- Revision 20 SHA-256 is
  `0db0661d022a8c0c4260852bfd6490a2afab45416ffceb83e8f04dfff5eacbe2`.
- Attempt 2 has no numeric exit record. Its exit remains
  **`UNKNOWN_NOT_RECORDED`**. The original ledger is **UNFINALIZED** and has no
  `projection.json`. Ten completed tasks do not retroactively prove a
  successful original process exit.
- Existing System/Application event records did not establish a termination
  cause. Empty stdout/stderr is not evidence of successful finalization.

## Recovery assessment

The frozen `final_projection` retains decoded results across all ten tasks.
Before any recovery construction, the retained gzip footers and host resource
snapshot showed:

| Fact | Bytes |
| --- | ---: |
| 20 compressed population/result artifacts | 691,914,998 |
| Uncompressed gzip ISIZE total | 29,814,860,661 |
| Uncompressed result JSON alone | 18,379,857,907 |
| Available commit headroom | 7,020,482,560 |
| Available physical memory | 6,273,757,184 |

Raw result JSON alone exceeded available commit headroom before Python object
expansion. The amendment required stopping rather than blindly repeating a
known memory-materializing operation when a concrete resource blocker was
present. Therefore:

- recovery construction: `NOT_STARTED_RESOURCE_BLOCKER`;
- recovery verification: `NOT_STARTED`;
- aggregate classification: `AGGREGATE_CLASSIFICATION_UNAVAILABLE_UNVERIFIED`.

No `final_projection`, historical `run`, task evaluator, strategy runner,
ordinary finalized-ledger verifier, ledger transition or market request was
invoked during recovery.

Two preliminary diagnostic recorders exited `1` before the direct
measurement succeeded: the first selector matched zero gzip files, and the
second serialized their sums as zero. Both failures and their exact sources,
stdout/stderr and exit records are retained rather than rewritten. The third
direct Python footer measurement exited `0` and preserved the original source
inventory again after assessment.

## Scientific boundary

This delivery makes all completed per-task evidence reviewable but does not
provide a verified aggregate projection. It does not establish a positive,
negative or mixed economic classification; promotion, protected out-of-sample,
portfolio and Live claims remain unavailable. Both Breakout implementations,
their parameters, the fixed panel/calendar, costs, masks and prior studies are
unchanged.

The immutable bundle is under
`data/research_evidence/issue-220-history-screened-180d-recovery-d09e50f3/`.
`artifact-index.json` records every published file size and SHA-256; the
manifest binds that index to the frozen executable, UUID, revision-20 event and
recovery status.
