# BTC/ETH Grid 4h external-history result

**Issue:** #239
**Data protocol:** `BTC_ETH_GRID_4H_EXTERNAL_HISTORY_QUALIFICATION_V3`
**Final classification:** `PRICE_CORPUS_PARTIAL_RECONCILIATION`
**Strategy evaluation:** none

## Acquisition

The fixed Binance USD-M archive inventory completed without retries: 656 ZIPs and 656 official
checksum objects, 1,312 successful first attempts, 483,852,142 network-payload bytes. Capture exit
was 0 after 1,051.83 seconds. The frozen plan SHA-256 is
`ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace` and the complete raw-hash
inventory is `e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0`.

The retained external root is
`C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset`; it contains 2,077,018,894
bytes across raw, ledger, v1/v2 normalized and compact result files. Git contains only the compact
plan, hashes, reports and command evidence under
`data/research_evidence/issue-239-binance-grid-history-a5ae0048/`. The corrected v3 compact
publication is separate at
`data/research_evidence/issue-239-binance-grid-history-v3-c9331a7e/`; neither publication
duplicates the retained raw or Parquet payload.

## Observed coverage

| Asset | Component | Observed | Expected | Gaps | Status |
|---|---|---:|---:|---:|---|
| BTCUSDT | trade-price 1m | 3,594,240 | 3,594,240 | 0 | `QUALIFIED` |
| BTCUSDT | native 4h | 14,976 | 14,976 | 0 | `QUALIFIED` |
| BTCUSDT | canonical derived 4h | 14,976 | 14,976 | 0 | `QUALIFIED_CANONICAL_DERIVED` |
| BTCUSDT | native reconciliation | 14,976 | 14,976 | 15 discrepancies | `PARTIAL_DISCREPANCIES` |
| BTCUSDT | MARK 1m | 3,581,225 | 3,594,240 | 13,015 rows / 9 intervals | `PARTIAL_OR_FAILED` |
| BTCUSDT | funding events | 7,488 | actual retained events | 4,605 exact timing offsets | `EVENTS_PARSED_SCHEDULE_PARTIAL_OR_UNCERTAIN` |
| ETHUSDT | trade-price 1m | 3,594,240 | 3,594,240 | 0 | `QUALIFIED` |
| ETHUSDT | native 4h | 14,976 | 14,976 | 0 | `QUALIFIED` |
| ETHUSDT | canonical derived 4h | 14,976 | 14,976 | 0 | `QUALIFIED_CANONICAL_DERIVED` |
| ETHUSDT | native reconciliation | 14,976 | 14,976 | 17 discrepancies | `PARTIAL_DISCREPANCIES` |
| ETHUSDT | MARK 1m | 3,589,854 | 3,594,240 | 4,386 rows / 11 intervals | `PARTIAL_OR_FAILED` |
| ETHUSDT | funding events | 7,488 | actual retained events | 4,605 exact timing offsets | `EVENTS_PARSED_SCHEDULE_PARTIAL_OR_UNCERTAIN` |

The complete trade-price and native-4h grids each form one maximal valid epoch over the requested
interval. MARK gaps are source gaps: BTC's longest is 5,760 minutes and ETH's longest is 1,440
minutes. Exact half-open gap and epoch intervals are retained in `publication-report.json`.

The v1/v2 positional implementation excluded entire months after the first mismatch. Those
generations and their bytes remain immutable but are superseded for qualification. V3 derives each
timestamp-keyed UTC bucket independently, so all 14,976 canonical bars remain available for each
asset and later complete buckets survive any local defect. It then records exact per-bar native
comparisons separately. BTC has 14,961 matches, 12 volume-only mismatches, one OHLC-only mismatch
and two combined OHLC/volume mismatches. ETH has 14,959 matches, 16 volume-only mismatches and one
OHLC-only mismatch. Exact timestamps, fields and both values are retained in the v3 manifest; no
comparison tolerance was widened and unreconciled rows are not silently approved for a study.

Funding retains actual timestamps, signed rates and the reported interval field. Both archives
contain 7,488 events, all reporting an 8-hour interval; no missing event was filled with zero. Exact
continuity finds 4,605 adjacent timestamp offsets per asset (all within 47 milliseconds of the
reported eight-hour interval, including 47 cross-month offsets). This establishes parsed event
coverage but leaves exact schedule continuity qualified as partial/uncertain; it does not infer
4,605 missing events. The initial v1 qualification mistakenly rejected negative rates. It remains immutable at SHA-256
`d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79`; corrected v2 reused the
same raw bytes and is independently retained at
`c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097`.

V3 ran offline over the retained archives with executable
`c9331a7e284a9ec876629d999a6100474d4b85ee`, exited 0 in 158.353 seconds and
recorded a peak process-tree working set of 290,021,376 bytes. Its manifest SHA-256 is
`9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`. The first
read-only verifier attempt exposed a JSON key-normalization defect and exited 1; it is preserved.
The corrected verifier executable `038361d5afda5e11c5bfa182252c46371555e0d7` then exited 0 and checked all 984
shards, manifest bindings, exact reconciliation rows, coverage arithmetic and funding continuity.

## Interpretation and next action

Binance MARK and funding are Binance source-venue facts/proxies, not WEEX historical facts. The
dated public WEEX guide lists standard futures rates of 0.02% maker and 0.08% taker per filled side;
actual account/API rates were not queried or verified. No current exchange-info request was needed
or made.

Existing history and prior studies make this period selection-informed rather than an untouched
holdout. No strategy outcomes, PnL, profitable-month ranking or Grid parameters were calculated.
The v3 publication report SHA-256 is
`22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`.
The next issue must freeze one finite-capital Grid protocol, risk/inventory reservations, causal
closed-4h decisions, 1m standing-order ambiguity/fills, every cost/funding assumption, terminal
valuation and chronological development/protected evaluation before any economic run.

Primary public references:

- <https://github.com/binance/binance-public-data>
- <https://data.binance.vision/>
- <https://www.weex.com/help/articles/4415852872601>
- <https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api>
