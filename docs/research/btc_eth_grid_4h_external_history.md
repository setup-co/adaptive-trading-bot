# BTC/ETH Grid 4h external-history charter

**Protocol:** `BTC_ETH_GRID_4H_EXTERNAL_HISTORY_DATA_V1`  
**Scope:** data acquisition and qualification only

## Fixed source contract

- Provider: Binance official public-data archive, USD-M futures.
- Symbols: exactly `BTCUSDT`, `ETHUSDT`.
- Interval: `[2020-01-01T00:00:00Z, 2026-11-01T00:00:00Z)`.
- Inventory: January 2020–October 2026, 82 months per symbol.
- Streams: trade-price 1m klines, native 4h klines, funding events, and MARK 1m klines.
- Every ZIP and `.CHECKSUM` is a distinct planned resource: 656 + 656 = 1,312 URLs.
- Maximum three attempts per exact resource for transient transport, 429 or 5xx only; at most two
  workers, 3,936 total attempts and 5 GiB payload. No provider/host/asset/date fallback.

Raw bytes, official checksums and durable attempt records are retained externally. Normalized
monthly Parquet preserves source decimal lexemes, wire close timestamps and half-open normalized
closes. Derived 4h requires exactly 240 aligned complete 1m observations and reconciles OHLC
exactly against native 4h. Volume comparison rounds only to the native archive's reported lexical
precision. No gap is filled, shortened or mixed with another source.

Trade-price, native/derived 4h, MARK and funding qualify independently. Funding retains actual
event times and reported intervals rather than imposing an eight-hour schedule. MARK volume-like
fields are not traded volume. Binance MARK/funding are not historical WEEX facts.

## Interpretation limits

Existing BTC/ETH history and prior results already informed project decisions. This interval is
selection-informed exploration, not an untouched holdout. A qualified external-price corpus with a
later WEEX cost overlay is research evidence under that explicit model, not proof of executable
WEEX profitability. No strategy outcome, month ranking or hindsight-profitable subperiod may be
computed during this task.

Public WEEX cost references must remain distinct from account/API-specific rates. The dated public
guide lists maker 0.02% and taker 0.08% per filled side; actual user/account rates are unverified.

## Requirements for the next Grid protocol

Before any economic run, freeze one grid type and spacing rule; direction/inventory mode; causal
range initialization/reset/termination; finite capital; aggregate risk, reservation, notional and
inventory caps; range-break/drawdown behavior; no martingale or loss-recovery top-ups; conservative
standing-order eligibility/fill/replacement chronology; partial/no-fill and 1m ambiguity policy;
every fee/spread/slippage/reset/emergency/final-exit cost; funding; total equity and terminal unwind;
and chronological development, validation and protected evaluation. Closed 4h is the decision
cadence, not a holding duration or proof of low turnover. BTC and ETH report separately; a combined
result requires real shared-capital accounting.

## Completed data result

The actual corrected bounded result is documented in
[`btc_eth_grid_4h_external_history_result.md`](btc_eth_grid_4h_external_history_result.md). The
trade-price, native-4h and canonical timestamp-derived grids are complete, but exact native
reconciliation, auxiliary MARK coverage and funding schedule continuity remain partial. The v3
classification is `PRICE_CORPUS_PARTIAL_RECONCILIATION`, not a qualified combined perpetual corpus.
