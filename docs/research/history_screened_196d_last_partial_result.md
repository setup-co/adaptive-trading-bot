# Issue #214 partial corpus delivery

This is a post-capture, data-only report for `HISTORY_SCREENED_196D_LAST_CORPUS_V1`. It is not a strategy study and contains no candidate, PnL, account, or trading result.

## Immutable identities and terminal state

- Required base: `6316b9bd39d980c1965386e78d4c915907ed6c50`.
- Frozen executable: `3cc2f475cddc0466d28edd5f11587b7e8321e17a`.
- Frozen plan SHA-256: `0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713`.
- Capture: 1,649 distinct attempts, 1,648 `HTTP_OK`, zero retries, one terminal failure, and 1,784 requests never started.
- Stop: request `ZAMAUSDT-0068`, interval `[2026-04-23T16:40:00Z, 2026-04-24T00:00:00Z)`, 88 expected positions, `URLError` / `gaierror`, classified `NETWORK_TERMINAL` and non-retryable.
- Capture exit: `CAPTURE_EXIT_UNKNOWN_NOT_RECORDED`. The original wrapper's `exit_code: null` is retained byte-for-byte. `ROUTE_STOPPED` stdout and empty stderr do not establish a numeric process exit.
- The route-stopped ledger has no orphan STARTED record. No acquisition process survives, and acquisition was not restarted or resumed.

The frozen implementation's `build()` path rejects a route-stopped generation before projection, while `verify()` requires the resulting manifest and shards. They were therefore not invoked or bypassed. Canonical 5m Parquet, derived Parquet, and an immutable qualified-dataset manifest are explicitly unavailable. A separate publication-only inventory audit revalidated every retained successful raw hash, exact wire row count/grid/numeric shape, predecessor shard/reference hash, request status, and coverage set. Its successful v2 exit is 0 and its report SHA-256 is `606cec24dd4620386ddc9bf8cfebdfd9e01ab4e4a4a68a82ee37fb23232ae82f`. Earlier failed offline diagnostics are retained rather than erased.

## Validated coverage

The full target is 564,480 five-minute positions. Validated reused rows total 234,956; validated new rows total 158,188; validated available rows total 393,144. Successful responses contain zero internal missing positions after exact validation. The failed request accounts for 88 planned positions and the 1,784 unattempted requests account for 171,248 planned positions. These categories reconcile exactly, but neither failed nor unattempted positions prove that exchange history is unavailable.

| Member | Reused | New | Available | Missing | Failed requests | Unattempted requests | Daily match / incomplete | Epochs | Complete 15m / 1h / 4h rows |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | 26,028 | 30,420 | 56,448 | 0 | 0 | 0 | 196 / 0 | 1 | 18,816 / 4,704 / 1,176 |
| ETHUSDT | 26,116 | 30,332 | 56,448 | 0 | 0 | 0 | 196 / 0 | 1 | 18,816 / 4,704 / 1,176 |
| SOLUSDT | 26,116 | 30,332 | 56,448 | 0 | 0 | 0 | 196 / 0 | 1 | 18,816 / 4,704 / 1,176 |
| XRPUSDT | 26,116 | 30,332 | 56,448 | 0 | 0 | 0 | 196 / 0 | 1 | 18,816 / 4,704 / 1,176 |
| ZECUSDT | 26,116 | 30,332 | 56,448 | 0 | 0 | 0 | 196 / 0 | 1 | 18,816 / 4,704 / 1,176 |
| ZAMAUSDT | 26,116 | 6,440 | 32,556 | 23,892 | 1 | 248 | 112 / 84 | 3 | 10,851 / 2,712 / 678 |
| BCHUSDT | 26,116 | 0 | 26,116 | 30,332 | 0 | 316 | 90 / 106 | 3 | 8,705 / 2,176 / 544 |
| AVAXUSDT | 26,116 | 0 | 26,116 | 30,332 | 0 | 316 | 90 / 106 | 3 | 8,705 / 2,176 / 544 |
| DOGEUSDT | 26,116 | 0 | 26,116 | 30,332 | 0 | 316 | 90 / 106 | 3 | 8,705 / 2,176 / 544 |
| LINKUSDT | 0 | 0 | 0 | 56,448 | 0 | 588 | 0 / 196 | 0 | 0 / 0 / 0 |

There are zero daily mismatches among days with complete five-minute coverage. This does not qualify incomplete days and does not replace the unavailable canonical build.

## Disposition

Dataset qualification and fixed full-panel 180-day readiness are `BLOCKED_NOT_ESTABLISHED`. The first unattempted request is `ZAMAUSDT-0069`; the last is `LINKUSDT-0588`. Exact IDs, inclusive request bounds, expected row counts, the failed record, all retained raw bytes, and separate failed/unattempted intervals are stored in `partial-inventory-report.json` and the frozen plan.

This bounded failure must not be interpreted as a listing-date finding, exchange-history absence, strategy result, or permission to shorten the window. Any continuation requires a separate authorization that reuses these verified bytes and does not reopen this stopped ledger.
