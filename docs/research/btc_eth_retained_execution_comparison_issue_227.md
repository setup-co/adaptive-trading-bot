# BTC/ETH retained-decision execution comparison — Issue #227

## Execution identity and boundary

Issue #227 authorized exactly one execution of the accepted corrected caller.
It completed without retry.

- executable: `39f72708b6d022f5f3a18e3ee2d993a3a6873cad`
- executable tree: `a4274099f0fef61b3cc82a7f667c503bf258c949`
- run UUID: `d9cbd3a7-ccfd-4f6a-9606-1aecee1b3cf3`
- compatibility protocol: `BTC_ETH_RETAINED_DECISION_EXECUTION_COMPARISON_V1`
- compatibility model SHA: `90101c22d9465ba2fb41eedb3164ac0285c8b9e3`
- exit: `0`, `NORMAL_EXIT`
- elapsed: `91.1380695` seconds of the external 3,597-second allowance
- peak working set: `4,435,968` bytes
- retained records / new outcomes / members: `138,240 / 80 / 2`
- output size: `235,530` bytes

The failed Issue #225 attempt remains separate: UUID
`e3aac506-9704-4afe-8bec-d22795835f0c`, executable
`d8cea515ad5bb9dd7beea31da1cfd77451b1bc2b`, exit 1 and zero evaluated
trades. It was not retried, rewritten or relabelled.

This comparison reuses the retained 15-minute decisions and legacy outcomes.
Only the accepted opt-in 5-minute LAST execution/protection profile is newly
evaluated. Approved-risk R is not account return, and this is not a
capital-constrained portfolio backtest or protected holdout.

## Saved result

The saved interpretation is `FIXED_NEGATIVE_ENDS_ROUND`. Every new-profile
full and masked cell has negative mean approved-risk R. Improvements in some
paired cells do not make either fixed variant positive or eligible for
promotion.

Admissions are 14 baseline and 14 zone decisions for BTC (9/9 after the
pre-outcome spacing masks), and 6/6 for ETH (6/6 masked).

### Mean approved-risk R and saved paired delta

| Asset | Variant/scenario | Population | Legacy mean R | New mean R | Paired new−old mean R |
|---|---|---:|---:|---:|---:|
| BTC | Baseline BASE | full | -1.5680434231 | -1.4816731325 | +0.0863702905 |
| BTC | Baseline BASE | mask | -1.6155638576 | -1.5048948034 | +0.1106690542 |
| BTC | Baseline STRESS | full | -3.2044837356 | -3.1837148433 | +0.0207688923 |
| BTC | Baseline STRESS | mask | -3.1574808651 | -3.2742004453 | -0.1167195802 |
| BTC | Zone BASE | full | -1.1791701857 | -1.0894394813 | +0.0897307044 |
| BTC | Zone BASE | mask | -0.8841858100 | -0.7877111806 | +0.0964746294 |
| BTC | Zone STRESS | full | -2.2948061975 | -1.9055107630 | +0.3892954345 |
| BTC | Zone STRESS | mask | -2.2315539496 | -1.8011950088 | +0.4303589407 |
| ETH | Baseline BASE | full/mask | -0.9168591788 | -1.5126645862 | -0.5958054074 |
| ETH | Baseline STRESS | full/mask | -4.0976706887 | -2.1487014353 | +1.9489692534 |
| ETH | Zone BASE | full/mask | -1.3189853870 | -1.2623568337 | +0.0566285533 |
| ETH | Zone STRESS | full/mask | -3.6398068902 | -2.7563249172 | +0.8834819730 |

All saved paired denominators equal the corresponding population denominator:
14/9 for BTC full/mask and 6/6 for ETH. Exact means, medians, profit factors
and paired partitions remain in the aggregate and projection artifacts.

### New-profile gross PnL, separate entry/exit fees and net PnL

These are quote-currency modeled values copied from the saved aggregates.

| Asset | Variant/scenario | Population | Gross | Entry fees | Exit fees | Net |
|---|---|---:|---:|---:|---:|---:|
| BTC | Baseline BASE | full | 53.895950 | 118.949889 | 119.014074 | -184.068013 |
| BTC | Baseline BASE | mask | 23.239356 | 69.548295 | 69.512133 | -115.821072 |
| BTC | Baseline STRESS | full | -21.546889 | 237.844957 | 237.991326 | -497.383172 |
| BTC | Baseline STRESS | mask | -38.053417 | 139.040218 | 139.022758 | -316.116393 |
| BTC | Zone BASE | full | -19.143062 | 115.351391 | 115.392264 | -249.886718 |
| BTC | Zone BASE | mask | 34.985243 | 67.622024 | 67.621083 | -100.257864 |
| BTC | Zone STRESS | full | 8.840056 | 230.649758 | 230.948451 | -452.758153 |
| BTC | Zone STRESS | mask | 0.532050 | 135.189800 | 135.288355 | -269.946105 |
| ETH | Baseline BASE | full/mask | -92.719707 | 46.816091 | 46.750992 | -186.286790 |
| ETH | Baseline STRESS | full/mask | -70.762514 | 93.597634 | 93.366789 | -257.726937 |
| ETH | Zone BASE | full/mask | -83.025510 | 41.750311 | 41.677076 | -166.452898 |
| ETH | Zone STRESS | full/mask | -209.491232 | 83.467792 | 83.522750 | -376.481775 |

## Terminal states and timing diagnostics

- All 80 new results filled and closed; unfilled and unresolved counts are zero.
- BTC STRESS has one recovered result in each variant, both legacy and new.
- ETH STRESS legacy has one recovery in each variant; the new profile has zero.
- Dual-touch stop-first, activation-order ambiguity and gap counts are zero in
  every actual cell.
- BASE records zero positive grid delays. Every STRESS result records a
  positive grid delay: 14 per BTC full cell, 9 per BTC mask cell and 6 per ETH
  cell.
- Legacy aggregate holding intervals are unavailable. New-profile saved upper
  holding bounds (median / p90 / maximum seconds) are:
  - BTC baseline BASE: `1950 / 16500 / 16500` full and
    `2100 / 16500 / 16500` masked;
  - BTC baseline STRESS: `1350 / 13800 / 16200` full and
    `1500 / 13800 / 13800` masked;
  - BTC zone BASE: `3600 / 16500 / 16500` full and
    `2700 / 16500 / 16500` masked;
  - BTC zone STRESS: `1950 / 13800 / 16200` full and
    `1800 / 13800 / 13800` masked;
  - ETH baseline BASE/STRESS: `2550 / 7800 / 7800` and
    `2250 / 7500 / 7500`;
  - ETH zone BASE/STRESS: `4200 / 7800 / 7800` and
    `4200 / 7500 / 7500`.

## Evidence identities

- projection SHA-256:
  `07f70c193d3815f362196e9434ca5ae095b103f432675d8f4f4afebe69063a77`
- per-trade rows SHA-256:
  `35420903408719a609ba2862057283ff669161e3462f1e4356471bf587653999`
- BTC aggregate SHA-256:
  `fe481774ae1776e952e9ac2a39b396e86853bf47dc00af624cc9e61867298efc`
- ETH aggregate SHA-256:
  `7109ba393a238ec9f3e52eb6914035c2c4c31e05cf17c820f01491807023b3f9`
- run record SHA-256:
  `f6a3db7c051adc606f644dae7ea086644e347da8457627201be63b66921ecea1`
- wrapper SHA-256:
  `c35b441f2aedd409dd7ee1dae8503a0221ce2f84cf43d7ba0e664503ac865419`
- publication index SHA-256:
  `d4097bac4a84bf7331c0c7ea7c2171abd7743ac699b393cc3a526336d2a1d4e4`

No historical rerun, verifier, tuning, other-asset evaluation or account action
is implied or authorized by this evidence.
