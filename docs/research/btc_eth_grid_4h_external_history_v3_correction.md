# BTC/ETH Grid external-history v3 correction protocol

**Protocol:** `BTC_ETH_GRID_4H_EXTERNAL_HISTORY_QUALIFICATION_V3`  
**Scope:** offline data qualification and reporting only

This generation corrects the qualification and failure-path defects identified in the architect
review of Issue #239. It does not issue a market-data request, reopen a captured object, evaluate a
Grid strategy, or alter the frozen source interval.

## Immutable inputs and predecessors

- Retained external root:
  `C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset`
- Frozen plan SHA-256:
  `ceeca32259aa172fef6c192da110e03aa7b0a1543a21b66872e7c0e8910e7ace`
- Raw inventory SHA-256:
  `e5fd2b4fed801993308b39878ca7ab9373ee14bd84f0785e0b262209d54948b0`
- Preserved v1 manifest SHA-256:
  `d985db5df832eb6db72962f09180dfa6b2d5bdab17089a4f7d201558e9fb5a79`
- Preserved v2 manifest SHA-256:
  `c424c01b0afe92ded3d59bf614d47dde7b9f505e2a9085c27eac62dcc8944097`

The v3 manifest records the corrected executable commit. Before any derivation, every retained raw
object is checked against the accepted inventory and every ZIP is checked against its retained
official checksum authority.

## Corrected qualification rules

- Parse only the exact recognized Binance USD-M header or headerless form and reject unordered or
  duplicate observations.
- Process one symbol/month at a time. Monthly row buffers are released before the next month; only
  compact row counts, gaps, valid epochs, funding continuity and discrepancy records persist.
- Group trade-price minutes by timestamp-keyed UTC four-hour buckets. A canonical derived bar
  requires all 240 unique ordered minute opens and becomes available at its close. One incomplete
  bucket cannot suppress a later complete bucket.
- Store canonical derived rows separately from reconciliation. Compare every eligible bucket and
  retain exact native and derived OHLC/volume values, missing minute timestamps and a distinct
  status for incomplete derivation, unavailable native comparison, OHLC mismatch, volume mismatch,
  combined mismatch or exact match.
- OHLC uses exact Decimal equality. Derived volume is quantized only to the native lexical precision
  before exact comparison; this rule is not widened to obtain a pass.
- Funding retains actual events and reported interval hours. Ordering, adjacent-event continuity,
  cross-month continuity and outer interval boundaries are reported. Missing events are never
  inserted or treated as zero funding.
- LAST, native/derived 4h, MARK, funding and reconciliation receive independent statuses. A MARK or
  funding limitation cannot erase valid LAST coverage, and an unreconciled derived bar is not
  silently approved for a later study.

## Capture failure-path rules

The corrected capture path is prospective tooling only in this generation. It keeps at most two
requests in flight, stops scheduling on a global access/redirect stop, durably reserves each attempt
before transmission, retains partial bytes and restores payload and attempt budgets from durable
records. These paths are exercised only with fake transports; the accepted 1,312-object capture is
not repeated.

## Verification and interpretation

The separate read-only v3 verifier checks input/predecessor identities, every generated shard,
provenance metadata, exact saved reconciliation rows and discrepancy details, derived-bar
chronology, coverage arithmetic, and funding continuity. The publication remains data evidence.
Partial or discrepant source components remain explicit and do not authorize a Grid backtest or
trading.
