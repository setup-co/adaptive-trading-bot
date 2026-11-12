# PR #236 selective October-14 completion evidence

This directory retains the single authorized selective completion under protocol
`BTC_ETH_BB_METHOD2_1H_NO_TIME_EXIT_COMPLETION_V1` (SHA-256
`6ac2876371acccbc92cfaf73189ccddd37dde33ad6a90b53ca8ca0e6a3cf4e18`). The frozen completion
executable is `c706baf6dc6cfb05cb46e0059bfae95adcc50ee2`; run UUID is
`79844d61-29fe-4f45-bb9c-236b2717892b`.

The immutable 30-minute comparator and shorter no-time diagnostic were not rerun. Exactly 4,970
saved natural-close rows were reused unchanged as economics. Only the shorter diagnostic's 182
`TERMINAL_OPEN` scenario keys were replayed from their retained authorized entries through
`2026-10-14T00:00:00Z`. The complete composite contains the same 5,152 unique pair keys and labels
every row with `REUSED_NATURAL_CLOSE` or `REPLAYED_PREVIOUSLY_OPEN` provenance.

## Execution facts

- Exit `0`; elapsed `25.8045814` seconds under the 300-second total ceiling.
- Peak owned-process-tree working set: `461,512,704` bytes under 1 GiB; no timeout or memory
  termination.
- Replayed partition: BTC PRICE 66 scenario keys (40 closed, 26 censored), BTC MFI 30 (12/18),
  ETH PRICE 60 (50/10), and ETH MFI 26 (16/10). Thus 118 formerly open keys closed and 64 remain
  data-end censored. These are follow-ups of prior observations, not new samples.
- Saved output: 5,152 rows; projection SHA-256
  `bcf8e71421841a2aeb59ebd136ba9afdfa815964cae375ccfffa9aea69e92f65`.

## Composite economics

`Terminal mean R` includes saved endpoint valuations for remaining open positions. `Natural
closed` is realized; `Open` remains censored and is not a realized close. `Net quote` and `Fees`
are the saved all-admission terminal-valued aggregates. The original 30-minute comparator values
remain in the parent evidence and were not recomputed here.

| Asset | Costs | Arm | N | Natural closed | Open | Terminal mean R | PF | Net quote | Fees |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| BTC | BASE | PRICE | 998 | 985 | 13 | -0.217494 | 0.724850 | -5,415.76 | 3,193.16 |
| BTC | BASE | MFI | 274 | 265 | 9 | -0.155959 | 0.791372 | -1,065.39 | 649.26 |
| BTC | STRESS | PRICE | 998 | 985 | 13 | -0.389255 | 0.568688 | -9,696.19 | 6,386.37 |
| BTC | STRESS | MFI | 274 | 265 | 9 | -0.282286 | 0.660121 | -1,929.41 | 1,298.55 |
| ETH | BASE | PRICE | 990 | 985 | 5 | +0.009853 | 1.014123 | +243.86 | 2,523.70 |
| ETH | BASE | MFI | 314 | 309 | 5 | +0.004229 | 1.006201 | +33.20 | 513.28 |
| ETH | STRESS | PRICE | 990 | 985 | 5 | -0.130295 | 0.833230 | -3,224.81 | 5,047.38 |
| ETH | STRESS | MFI | 314 | 309 | 5 | -0.084880 | 0.884894 | -666.31 | 1,026.56 |

The result is `COMPOSITE_COMPLETION_MIXED / NO_PROMOTION`. BTC remains negative in both arms and
scenarios; ETH is slightly positive only under BASE and remains negative under STRESS. Sixty-four
scenario rows are still endpoint-valued rather than realized. Funding remains not modeled, 5m
OHLC cannot establish tick order or venue execution, and the result does not authorize a strategy,
timer removal, runtime activation or trading.

`composite-rows.jsonl.gz` is a deterministic gzip copy of the complete output. Its decompressed
authority is `31,276,783` bytes, SHA-256
`9d21404b22d5ad676ec4e865ccebfcd0984dabb1f568ead7337d63ac54e1a25c`; the gzip SHA-256 is
`f52436e75b8b377d7a192362ee4fc09643bcb1bb35045fd3401d391b2cd5b258`.
