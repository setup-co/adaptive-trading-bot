# History-screened 196-day LAST continuation result

Issue #216 completed the bounded successor to the immutable, route-stopped
Issue #214 acquisition. This is a data-eligibility result only. It does not
evaluate a strategy, count candidates, compute PnL, change a runtime allowlist,
or establish protected out-of-sample or leveraged-market validity.

## Frozen identities

| Identity | Value |
|---|---|
| Integrated Issue #214 base | `05ccf57d8499d3f295b5940377eacc875a0c3428` |
| Successor source revision | `987dd81c2ba2643d162cad91c4ab9352be9f63bf` |
| Successor execution HEAD | `682fd077ac99aa8b381d4c51e010168a333c7d98` |
| Execution tree | `9202f5ba1da49a948784e3f06b7b3e64ea4af080` |
| Successor plan SHA-256 | `dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f` |
| Parent plan SHA-256 | `0a9f48d8da7bb3af6054daa3cc565b93641c5e57a3a0e62dd2c954de9c101713` |
| Parent publication manifest SHA-256 | `d2cae3a8c1eccbed8d77dee43553b65fb7e50405040f7722ff501bee22668dbe` |
| Parent inventory SHA-256 | `606cec24dd4620386ddc9bf8cfebdfd9e01ab4e4a4a68a82ee37fb23232ae82f` |
| Successor manifest SHA-256 | `756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d` |
| Successor report SHA-256 | `bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66` |

The stopped parent remains `ROUTE_STOPPED` with
`CAPTURE_EXIT_UNKNOWN_NOT_RECORDED`. Its failed `ZAMAUSDT-0068` record and
1,784 unattempted records remain unchanged. The successor imported only the
1,648 validated parent successes and used a new root and lineage for the exact
1,785 unresolved shapes.

## Acquisition and process outcomes

The successor transmitted 1,785 distinct requests and 1,785 total attempts.
Every response was HTTP 200, parsed as `COMPLETE`, and contained its full
declared grid. There were zero retries, zero valid-response gaps, zero failed
successor requests, and zero successor unattempted requests.

| Operation | Started UTC | Ended UTC | Exit | Outcome |
|---|---|---|---:|---|
| Capture | 2026-10-27 02:05:25.490373 | 2026-10-27 03:08:19.187887 | 0 | `CAPTURE_COMPLETE` |
| Build | 2026-10-27 03:08:58.487022 | 2026-10-27 03:12:49.470136 | 0 | `QUALIFIED` |
| Verify | 2026-10-27 03:13:05.496212 | 2026-10-27 03:16:07.878320 | 0 | `QUALIFIED` |

The capture contributed exactly 171,336 new 5m rows. Combined with 234,956
earlier reused rows and 158,188 validated parent-capture rows, the canonical
union contains 564,480 rows: 56,448 for each of ten members.

## Per-member qualification

| Member | Earlier reused | Parent capture | Successor | Valid 5m | Missing | Epochs | Daily matches | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BTCUSDT | 26,028 | 30,420 | 0 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| ETHUSDT | 26,116 | 30,332 | 0 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| SOLUSDT | 26,116 | 30,332 | 0 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| XRPUSDT | 26,116 | 30,332 | 0 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| ZECUSDT | 26,116 | 30,332 | 0 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| ZAMAUSDT | 26,116 | 6,440 | 23,892 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| BCHUSDT | 26,116 | 0 | 30,332 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| AVAXUSDT | 26,116 | 0 | 30,332 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| DOGEUSDT | 26,116 | 0 | 30,332 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |
| LINKUSDT | 0 | 0 | 56,448 | 56,448 | 0 | 1 | 196/196 | `QUALIFIED` |

Every member has one continuous 5m epoch over `[2026-04-01, 2026-10-14)`.
Each has 18,816 complete 15m rows, 4,704 complete 1h rows, and 1,176 complete
4h rows. The unchanged `[2026-04-10, 2026-10-07)` signal-open calendar and
the declared 168-hour follow-up through October 14 are data-supported after
the existing per-epoch 50-complete-4h warm-up and boundary exclusion.

## Artifact anchors

The manifest binds 46 derived/output files. The five successor request
archives have these SHA-256 values:

- `ZAMAUSDT`: `4a971b86074e357236ff07576ae0172febef28767669838c2d859776236fa727`
- `BCHUSDT`: `423e22359db3e90a7bb41f360e5371aee9b3e9555eb02d66bf8c9d6309d6e36c`
- `AVAXUSDT`: `ddc3c37d76b5b16dda1134d1222b7b814186b82fd370da845c53c437f08fdecb`
- `DOGEUSDT`: `78f4c5869bf8f9c5cff208c72c87dec18170b279207e8b3ce341d2da539ed115`
- `LINKUSDT`: `299a5f0e582ea4db78d77f23d34de9e376aa3216653398f589b600c5238811d8`

The offline verifier reconstructed the report and all Parquet tables from the
validated predecessor and successor raw bytes and matched the saved artifacts.
`QUALIFIED` means only that this fixed exploratory corpus satisfies its stated
calendar, grid, daily reconciliation, warm-up, and follow-up requirements.

This document is also the narrow current-state reconciliation for Issue #216.
The accepted Issue #214 publication manifest source-hashes
`docs/project_state.md`; that predecessor source therefore remains byte-for-byte
unchanged so the published parent authority and the successor's offline verifier
remain reproducible.

