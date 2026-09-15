# WEEX old-history independent cloud revalidation

## Decision

```text
WEEX_OLD_HISTORY_CLOUD_RUN_1 = INCOMPLETE
WEEX_OLD_HISTORY_CLOUD_RUN_2 = INCOMPLETE
WEEX_OLD_HISTORY_REPRODUCIBILITY = STABLE_DEFECTS
WEEX_OLD_HISTORY_SOURCE_CONCLUSION = NOT_QUALIFIED
```

The earlier WEEX historical defects were **not primarily caused by the operator laptop, local Internet connection, or local geolocation**. The older WEEX Futures V3 `5m` `LAST` range was re-audited from independent GitHub-hosted Azure runners in multiple US regions, with zero transport retries and zero unrecovered request failures in the decisive parallel runs. The same major defect counts reproduced across independent runners.

A second finding is also important: the WEEX historical archive is not proven byte-for-byte immutable. Some rows that were previously absent are now returned by targeted requests, while the older range as a whole still fails strict integrity qualification. Therefore historical data must be audited at corpus-creation time and an accepted corpus must be snapshotted and hashed rather than assuming the public archive will remain unchanged forever.

## Scope and provenance

- GitHub Issue: `#113`
- Starting main SHA: `6d7fdc52c0e7a19f4521fc528ee17c0b484efed6`
- Official source: `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Symbols: `BTCUSDT`, `ETHUSDT`
- Price type: `LAST`
- Canonical audit interval: `5m`
- Old-range lock: `[2024-01-01T00:00:00Z, 2025-04-02T00:00:00Z)`
- Targeted independent probe workflow: `34990741199`
- Parallel full-range workflow: `34991087146`
- Runtime: GitHub-hosted Ubuntu 24.04 / Python 3.12
- Authentication: none

The parallel audit split the old range into five fixed UTC chunks and ran each symbol/chunk twice on separate GitHub-hosted jobs:

- `c1`: `[2024-01-01, 2024-04-01)`
- `c2`: `[2024-04-01, 2024-07-01)`
- `c3`: `[2024-07-01, 2024-10-01)`
- `c4`: `[2024-10-01, 2025-01-01)`
- `c5`: `[2025-01-01, 2025-04-02)`

The 20 jobs ran independently across several Azure regions including `eastus`, `eastus2`, `westus`, `westus2`, `westus3`, `centralus`, and `northcentralus` as allocated by GitHub Actions.

## Targeted known-defect probes

Two separate cloud jobs, including runners in Azure `westus3` and `eastus`, queried each target three times with identical bounded requests and surrounding controls.

### BTCUSDT native 15m

The two previously known native 15m gaps remained absent in all repetitions on both cloud runners:

- `2024-01-01T02:45:00Z`
- `2024-01-02T03:45:00Z`

Nearby control timestamps were returned. The repeated cloud result therefore confirms that these two 15m gaps are not explained by the operator's local connectivity.

### Rows whose current archive behavior differs from the earlier audit

Targeted requests now return open timestamps for:

- BTCUSDT 5m `2024-06-26T18:40:00Z`
- BTCUSDT 5m `2024-06-26T18:45:00Z`
- ETHUSDT 5m `2025-03-04T11:20:00Z`
- ETHUSDT 5m `2025-03-04T15:50:00Z`
- ETHUSDT 5m `2025-03-04T15:55:00Z`

However, strict row validation is decisive rather than timestamp presence alone. In the full strict audit, the ETH c5 range still contains exactly three missing canonical rows and three malformed returned rows. Thus the public endpoint can now return those open timestamps while still failing the project's 11-field canonical-row validity rules.

The changed behavior compared with the earlier audit is evidence that WEEX may repair or alter historical responses over time. It does **not** make the old range qualified.

## Parallel full-range results

The tables below show one run's counts; the second independent run reproduced the same integrity outcome for every failing chunk and independently confirmed the clean chunks.

### BTCUSDT

| Chunk | Expected | Unique valid | Missing | Duplicates | Conflicts | Malformed | Invalid additive | Retries | Unrecovered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| c1 Jan-Mar 2024 | 26,208 | 23,549 | 2,659 | 0 | 0 | 0 | 0 | 0 | 0 |
| c2 Apr-Jun 2024 | 26,208 | 26,193 | 15 | 6 | 6 | 5 | 1 | 0 | 0 |
| c3 Jul-Sep 2024 | 26,496 | 26,496 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| c4 Oct-Dec 2024 | 26,496 | 26,496 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| c5 Jan-Apr 2 2025 | 26,208 | 26,208 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

All BTC c3/c4/c5 integrity categories were zero in both independent runs. BTC c1 and c2 failures reproduced without transport retries or unrecovered requests.

### ETHUSDT

| Chunk | Expected | Unique valid | Missing | Duplicates | Conflicts | Malformed | Invalid additive | Retries | Unrecovered |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| c1 Jan-Mar 2024 | 26,208 | 26,198 | 10 | 0 | 0 | 0 | 0 | 0 | 0 |
| c2 Apr-Jun 2024 | 26,208 | 26,193 | 15 | 4 | 4 | 7 | 0 | 0 | 0 |
| c3 Jul-Sep 2024 | 26,496 | 26,496 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| c4 Oct-Dec 2024 | 26,496 | 26,496 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| c5 Jan-Apr 2 2025 | 26,208 | 26,205 | 3 | 0 | 0 | 3 | 0 | 0 | 0 |

All ETH c3 and c4 integrity categories were zero in both independent runs. ETH c1/c2/c5 failures reproduced without transport retries or unrecovered requests.

Across all audited rows, the decisive failures were content-integrity failures, not request failures. No off-grid, invalid-OHLC, non-positive-price, or close-time-misalignment issue was observed in the summarized chunks above.

## What this proves about the laptop / location hypothesis

The local-environment hypothesis is not supported as the primary explanation for the original rejection:

1. The large BTC c1 defect reproduces identically in independent cloud runs: 2,659 missing 5m candles.
2. BTC c2 reproduces 15 missing rows, six conflicting duplicates, five malformed rows, and one invalid additive row.
3. ETH c1/c2 reproduce their missing/conflicting/malformed defects independently.
4. Every decisive failing job completed with zero retry attempts and zero unrecovered transport requests.
5. The two known BTC native 15m gaps reproduce from independent cloud regions with surrounding controls present.

Therefore the major defects are attributable to the historical source/content behavior, not a transient failure to download otherwise valid rows from the operator's laptop.

## What changed since the earlier audit

The new audit also shows that the historical endpoint is not safely treated as immutable:

- some previously absent 5m timestamps are now returned;
- the BTC Apr-Jun missing count is now 15 instead of the earlier reported 16 within that period;
- ETH targeted March 2025 timestamps are returned, but strict validation still rejects three canonical rows as malformed/missing.

This supports a stronger data-governance rule: when a range passes qualification, download it into an immutable research corpus, record source/provenance and SHA-256, and use that locked corpus for reproducible research. Future API responses should not silently replace an already accepted corpus without a new qualification/version.

## Clean subranges discovered

This revalidation materially improves our knowledge of WEEX history even though the complete old range fails:

- both BTCUSDT and ETHUSDT are independently clean over `[2024-07-01, 2025-01-01)` in both cloud runs;
- BTCUSDT remains clean through `[2025-01-01, 2025-04-02)`;
- ETHUSDT has three strict integrity failures inside `[2025-01-01, 2025-04-02)`, so that entire chunk cannot be declared clean;
- Issue #111 separately qualified both symbols over `[2025-04-02, 2026-01-08)`.

A later narrowly scoped boundary audit may determine whether a larger common clean interval can be formed around the three March 2025 ETH defects. This issue does not silently redefine the authoritative corpus boundary.

## Final governance conclusion

The old range as a whole remains not qualified:

```text
WEEX_OLD_HISTORY_CLOUD_RUN_1 = INCOMPLETE
WEEX_OLD_HISTORY_CLOUD_RUN_2 = INCOMPLETE
WEEX_OLD_HISTORY_REPRODUCIBILITY = STABLE_DEFECTS
WEEX_OLD_HISTORY_SOURCE_CONCLUSION = NOT_QUALIFIED
```

`STABLE_DEFECTS` means the qualification failures themselves reproduce across independent cloud infrastructure. It does not claim that every historical response is immutable; targeted evidence shows some archive rows have changed since earlier tests.

No strategy performance result, accepted corpus import, private API operation, Paper/Live order, alternate-exchange repair, interpolation, forward-fill, or Master Plan venue-policy change was performed.