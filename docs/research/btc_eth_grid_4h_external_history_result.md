# BTC/ETH Grid 4h external-history result

**Issue:** #239  
**Data protocol:** `BTC_ETH_GRID_4H_EXTERNAL_HISTORY_DATA_V1`  
**Final classification:** `PRICE_CORPUS_PARTIAL`  
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
`data/research_evidence/issue-239-binance-grid-history-a5ae0048/`.

## Observed coverage

| Asset | Component | Observed | Expected | Gaps | Status |
|---|---|---:|---:|---:|---|
| BTCUSDT | trade-price 1m | 3,594,240 | 3,594,240 | 0 | `QUALIFIED` |
| BTCUSDT | native 4h | 14,976 | 14,976 | 0 | `QUALIFIED` |
| BTCUSDT | derived/reconciled 4h | 13,314 | 14,976 | 1,662 rows / 7 intervals | `PARTIAL_OR_FAILED` |
| BTCUSDT | MARK 1m | 3,581,225 | 3,594,240 | 13,015 rows / 9 intervals | `PARTIAL_OR_FAILED` |
| BTCUSDT | funding events | 7,488 | schedule-derived only | n/a | `QUALIFIED` |
| ETHUSDT | trade-price 1m | 3,594,240 | 3,594,240 | 0 | `QUALIFIED` |
| ETHUSDT | native 4h | 14,976 | 14,976 | 0 | `QUALIFIED` |
| ETHUSDT | derived/reconciled 4h | 13,146 | 14,976 | 1,830 rows / 7 intervals | `PARTIAL_OR_FAILED` |
| ETHUSDT | MARK 1m | 3,589,854 | 3,594,240 | 4,386 rows / 11 intervals | `PARTIAL_OR_FAILED` |
| ETHUSDT | funding events | 7,488 | schedule-derived only | n/a | `QUALIFIED` |

The complete trade-price and native-4h grids each form one maximal valid epoch over the requested
interval. MARK gaps are source gaps: BTC's longest is 5,760 minutes and ETH's longest is 1,440
minutes. Exact half-open gap and epoch intervals are retained in `publication-report.json`.

Derived/native 4h reconciliation did not qualify 9 BTC months and 10 ETH months. Failures include
source OHLC mismatches and volume differences beyond the native row's lexical precision. The
adapter does not replace native values, discard the affected months or tune a tolerance after the
result. Consequently the overall price corpus is partial even though both underlying trade-price
and native-4h grids are individually complete.

Funding retains actual timestamps, signed rates and the reported interval field. Both archives
contain 7,488 events, all reporting an 8-hour interval; no missing event was filled with zero. The
initial v1 qualification mistakenly rejected negative rates. It remains immutable at SHA-256
`d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79`; corrected v2 reused the
same raw bytes and is independently retained at
`c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097`.

## Interpretation and next action

Binance MARK and funding are Binance source-venue facts/proxies, not WEEX historical facts. The
dated public WEEX guide lists standard futures rates of 0.02% maker and 0.08% taker per filled side;
actual account/API rates were not queried or verified. No current exchange-info request was needed
or made.

Existing history and prior studies make this period selection-informed rather than an untouched
holdout. No strategy outcomes, PnL, profitable-month ranking or Grid parameters were calculated.
The next issue must freeze one finite-capital Grid protocol, risk/inventory reservations, causal
closed-4h decisions, 1m standing-order ambiguity/fills, every cost/funding assumption, terminal
valuation and chronological development/protected evaluation before any economic run.

Primary public references:

- <https://github.com/binance/binance-public-data>
- <https://data.binance.vision/>
- <https://www.weex.com/help/articles/4415852872601>
- <https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api>
