# Trend Pullback EMA50 Binance 2024–2025 primary study evidence

## Outcome

**STOPPED — the predeclared study did not run.** The first required import,
BTC/USDT 15m, failed during public CCXT Binance initialization with process status `1`.
One explicitly permitted, identical retry failed the same way. In accordance with Issue #99's
failure rule, the other five imports and the robustness command were not executed.

This record is failure evidence only. It contains no EMA50 result and makes no strategy,
performance, Paper, Live, or Experience claim.

## Execution provenance

- Issue: #99
- Accepted Git commit: `e3004b70fbfe86cada2e38e370140924ae7264c3`
- Study-root creation time: `2026-09-14T17:44:26.2144075Z`
- First attempt: `2026-09-14T17:44:42.1826528Z` to
  `2026-09-14T17:44:43.3041813Z`
- Identical retry: `2026-09-14T17:45:00.5739488Z` to
  `2026-09-14T17:45:01.4495585Z`
- CCXT version: `4.5.78`
- Requested exchange: Binance public spot (`binance`)
- Expected source ID: `ccxt-binance-spot` (source initialization did not complete)
- Symbols: `BTC/USDT`, `ETH/USDT`
- Timeframes: `15m`, `1h`, `4h`
- Import range: `[2024-01-01T00:00:00Z, 2026-01-08T00:00:00Z)`
- Fresh isolated historical root:
  `C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\historical`
- Historical files present after both failed attempts: `0`

The study parameters that would have applied after six clean imports remained exactly as
predeclared, but were not executed:

- Decision window: `[2024-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`
- Research equity: `10000 USDT`
- Follow-up duration: `168` hours
- Execution costs: spread `2` bps, slippage `2` bps, fee `10` bps, latency `0`

## Import gate

| Series | Attempt | Exit | Integrity | Result |
|---|---:|---:|---|---|
| BTC/USDT 15m | 1 | 1 | unavailable | public CCXT exchange initialization failed |
| BTC/USDT 15m | identical retry 1 | 1 | unavailable | public CCXT exchange initialization failed |
| BTC/USDT 1h | not run | — | unavailable | study stopped |
| BTC/USDT 4h | not run | — | unavailable | study stopped |
| ETH/USDT 15m | not run | — | unavailable | study stopped |
| ETH/USDT 1h | not run | — | unavailable | study stopped |
| ETH/USDT 4h | not run | — | unavailable | study stopped |

Neither failed attempt produced stdout. Both produced exactly this stderr line:

```text
import failed: failed to initialize public CCXT exchange binance
```

The captured files outside Git are:

| File | Bytes |
|---|---:|
| `import-btc-usdt-15m.stdout.json` | 0 |
| `import-btc-usdt-15m.stderr.txt` | 66 |
| `import-btc-usdt-15m-retry-1.stdout.json` | 0 |
| `import-btc-usdt-15m-retry-1.stderr.txt` | 66 |

## Exact commands

Environment and accepted revision:

```powershell
git switch -c issue-99-binance-ema50-primary-study e3004b70fbfe86cada2e38e370140924ae7264c3
.\.venv\Scripts\python.exe -m pip install -e .
$env:ATB_HISTORICAL_MARKET_DATA_ROOT='C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\historical'
```

First attempt:

```powershell
.\.venv\Scripts\atb-import-ohlcv.exe --exchange binance --symbol BTC/USDT --timeframe 15m --start 2024-01-01T00:00:00Z --end 2026-01-08T00:00:00Z 1> 'C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\evidence\import-btc-usdt-15m.stdout.json' 2> 'C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\evidence\import-btc-usdt-15m.stderr.txt'
```

Identical retry, with only distinct evidence filenames:

```powershell
.\.venv\Scripts\atb-import-ohlcv.exe --exchange binance --symbol BTC/USDT --timeframe 15m --start 2024-01-01T00:00:00Z --end 2026-01-08T00:00:00Z 1> 'C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\evidence\import-btc-usdt-15m-retry-1.stdout.json' 2> 'C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-99-e3004b70-20260914T174409Z\evidence\import-btc-usdt-15m-retry-1.stderr.txt'
```

No `atb-research-exit-robustness` command was executed.

## Unavailable result fields

Because the mandatory six-import clean-integrity gate did not pass, all downstream authoritative
result fields are unavailable:

- six clean integrity summaries;
- corpus ID and corpus digest;
- robustness configuration digest and report digest;
- full report JSON SHA-256 and byte size;
- population and censoring metrics;
- BOTH_CLOSED delta metrics;
- overlap-component metrics;
- symbol, side, regime, HTF, MTF, and UTC-month strata;
- chronological stability flags.

No alternate exchange, window, horizon, costs, symbols, subset, repair, imputation, code change,
Paper/Live/Experience activation, or deployment was attempted.
