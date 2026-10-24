# Issue #203 interrupted-attempt incident record

- Classification: `INTERRUPTED_NOT_FINALIZED`
- Original run UUID: `4d8cabd6-bd43-480c-a5dd-ab1ccf83b2bf`
- Frozen executable: `b332eb83f392819365f3b667496fb6b01a3810ef`
- Accepted tree: `94a074615ff296208d177ae832dfd101d0031bbe`
- Original ledger last durable event: revision 11, task 5 `STARTED`, at `2026-10-22T03:45:40.204995Z`
- Durable task state: tasks 0-4 `COMPLETED`; task 5 `STARTED`; tasks 6-9 `NOT_STARTED`; zero `FAILED`
- Original run exit record: unavailable; no exit status is inferred
- Original traceback: unavailable; stdout and stderr files are both empty
- Surviving worker, verifier, queued launcher, or follow-up automation: none found before recovery launch
- Task 5 output inventory: no population, results, report, pending, or lock file was present
- Completed artifact verification: every task 0-4 population/results/report SHA-256 matched its ledger `COMPLETED` event
- Append-only chain verification: revisions 0-11 and every `previous_sha256` link matched
- Preservation copy: original grant, 30 ledger files, and 6 log files copied byte-for-byte outside both UUID ledger directories; 37 files checked with zero mismatches
- Host evidence: Windows System log event 1074 records a planned OS service-pack restart initiated by `MoUsoCoreWorker.exe` at `2026-10-22T07:35:04+03:30`; shutdown/start events followed. This corroborates the operator-reported reboot without establishing any simulator exit outcome.

The interrupted ledger was not edited, reset, resumed, verified as a finalized run, or used as authority for a final economic result.
