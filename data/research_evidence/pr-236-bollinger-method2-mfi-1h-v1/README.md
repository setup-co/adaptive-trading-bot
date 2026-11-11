# PR #236 Bollinger Method II hourly comparison evidence

This directory contains the compact, lossless publication of the single authorized offline run of
`BTC_ETH_BB_METHOD2_MFI_ABLATION_1H_30M_V1`. It is separate from, and does not reinterpret, the
archived 15m evidence.

## Execution identity

- Frozen executable: `706c7818573c566e9a6a78e542887e7916c03612`
- Frozen tree: `44befd5d5ef5b8c88588f4b6adbaa310e0c0e5e4`
- Protocol SHA-256: `e4948ecdc43e03b072cc62f8ab6cfbeb9dfb58f80221c2e7e7aa0a5c52431672`
- Run UUID: `63964675-0ab4-4b46-89c3-1b034b69dfbd`
- Result schema: `bb-method2-mfi-hourly-ablation-result-v1`
- Exit: `0`; elapsed `40.1836267` seconds; timeout `false`; memory limit exceeded `false`
- Owned process tree: 2 observed process identities; peak combined working set `352,595,968`
  bytes under the `1,073,741,824`-byte limit
- Projection SHA-256: `6ecc6eae280738dd800bc2076116aa4d54a79ab0109c53eddf41f612c69ac416`

The caller completed before its 30-second per-member heartbeat interval emitted a
`progress.json`; the durable member files, 5,152 scenario rows, projection and COMPLETE marker are
present. This absence is recorded, not backfilled.

## Population funnel

| Asset | Arm | Hourly observations | Signals | Risk rejected | Safety rejected | Admitted |
|---|---|---:|---:|---:|---:|---:|
| BTC | PRICE_CONTROL | 4,320 | 1,095 | 97 | 0 | 998 |
| BTC | PRICE_PLUS_MFI | 4,320 | 321 | 47 | 0 | 274 |
| ETH | PRICE_CONTROL | 4,320 | 1,042 | 52 | 0 | 990 |
| ETH | PRICE_PLUS_MFI | 4,320 | 324 | 10 | 0 | 314 |

The 35-minute control spacing rule retains all hourly control admissions: BTC 998 and ETH 990.
Consequently `FULL` and `COMMON_SPACED` are identical views here, not independent confirmation.

## Economic table

All values below are saved FULL-view fields; quote values and R values are displayed with bounded
rounding while `projection.json` retains the exact Decimal strings. Every admission filled and
closed; unfilled, recovered and unresolved counts are all zero.

| Asset | Costs | Arm | Closed | W/L | Mean R | Median R | PF | Gross quote | Fees quote | Net quote | Saved exits | Holding seconds min–max |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| BTC | BASE | PRICE | 998 | 145/853 | -0.174348 | -0.155861 | 0.099544 | -1,153.6213 | 3,190.6083 | -4,344.2297 | STOP 2; TIME 996 | 600–1,800 |
| BTC | BASE | MFI | 274 | 43/231 | -0.139973 | -0.122525 | 0.099356 | -308.6432 | 648.2900 | -956.9332 | TIME 274 | 1,800–1,800 |
| BTC | STRESS | PRICE | 998 | 52/946 | -0.340859 | -0.296703 | 0.018113 | -2,112.3134 | 6,381.5112 | -8,493.8247 | STOP 3; TIME 995 | 300–1,800 |
| BTC | STRESS | MFI | 274 | 14/260 | -0.261586 | -0.222908 | 0.021472 | -491.8875 | 1,296.7310 | -1,788.6185 | STOP 1; TIME 273 | 1,500–1,800 |
| ETH | BASE | PRICE | 990 | 169/821 | -0.134503 | -0.121002 | 0.164198 | -807.6040 | 2,521.3364 | -3,328.9404 | STOP 1; TARGET 1; TIME 988 | 300–1,800 |
| ETH | BASE | MFI | 314 | 62/252 | -0.078762 | -0.087209 | 0.292778 | -105.4611 | 512.8196 | -618.2806 | TIME 314 | 1,800–1,800 |
| ETH | STRESS | PRICE | 990 | 76/914 | -0.268590 | -0.225722 | 0.057603 | -1,604.9138 | 5,042.6871 | -6,647.6008 | STOP 3; TARGET 2; TIME 985 | 0–1,800 |
| ETH | STRESS | MFI | 314 | 30/284 | -0.168083 | -0.164722 | 0.092409 | -293.7994 | 1,025.6505 | -1,319.4499 | TIME 314 | 1,800–1,800 |

## Selection description

MFI selected a less-negative subset, but not a profitable one. Confirmed-minus-control FULL mean R
was `+0.0343745582` BTC BASE, `+0.0792729100` BTC STRESS, `+0.0557407759` ETH BASE and
`+0.1005071415` ETH STRESS. Within the control population, retained-minus-excluded mean R was
`+0.0473837141`, `+0.1092739836`, `+0.0816322013` and `+0.1471924114`, respectively. The
retained/excluded denominators were 274/724 BTC and 314/676 ETH. These are stored descriptive
partitions, not causal effects.

## Chronological blocks

The rows are saved BASE/STRESS FULL-mask blocks. `n` is the closed denominator.

| Asset | Costs | Arm | Block 1 n / mean R | Block 2 n / mean R | Block 3 n / mean R |
|---|---|---|---:|---:|---:|
| BTC | BASE | PRICE | 328 / -0.162073 | 356 / -0.164591 | 314 / -0.198232 |
| BTC | BASE | MFI | 91 / -0.127902 | 93 / -0.136473 | 90 / -0.155797 |
| BTC | STRESS | PRICE | 328 / -0.320080 | 356 / -0.312806 | 314 / -0.394369 |
| BTC | STRESS | MFI | 91 / -0.238599 | 93 / -0.247913 | 90 / -0.298957 |
| ETH | BASE | PRICE | 328 / -0.122204 | 332 / -0.113993 | 330 / -0.167361 |
| ETH | BASE | MFI | 106 / -0.051723 | 114 / -0.080662 | 94 / -0.106948 |
| ETH | STRESS | PRICE | 328 / -0.254209 | 332 / -0.216242 | 330 / -0.335548 |
| ETH | STRESS | MFI | 106 / -0.140869 | 114 / -0.159555 | 94 / -0.209113 |

Every block mean is negative. Exact block medians, profit factors, quote outcomes and fees remain in
`projection.json`.

## Classification and limits

The accepted classifier returns **`NEGATIVE_ADAPTATION / NO_PROMOTION`**. MFI improved selection
relative to the price-only arm, but every FULL/common-spaced mean R is negative and every profit
factor is below one. Funding is `NOT_MODELED`. Results are descriptive, pre-funding independent-
opportunity research, not portfolio returns, out-of-sample proof, runtime eligibility or trading
authority. No tuning, retry or timeframe selection follows from this result.

`trades.jsonl.gz` is deterministic gzip (`mtime=0`) of the complete 11,018,109-byte row authority.
Its decompressed SHA-256 is
`bac3ae504a7c9b21791b76fb05c7f209214d10e4e531c7ee4011a0e81dccaa4c`.
