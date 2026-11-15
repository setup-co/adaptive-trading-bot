# BTC/ETH Grid V1 historical-study preflight

## Status and fixed lineage

This Issue #245 deliverable is **LOCKED PROPOSED / HISTORICAL EXECUTION NOT AUTHORIZED**. It prepares a deterministic read-only boundary for a later one-shot study; it does not evaluate a Grid, generate historical fills, calculate PnL, or approve trading.

The accepted lineage is:

- synthetic engine HEAD `dc450a4a3a0f2dfe114a690459971bc79d2aba68`, engine version `0.1.0-synthetic-only`;
- protocol HEAD `58f250a86925a48ed338ef716a621a6594e768b9`, `BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` / `0.2.0-proposed`;
- protocol document SHA-256 `f0314ed243a2f7784e13d252901802c95b994b59221a2aa67333733be86907fd` and parameters SHA-256 `960edccc1c40ed63a780734921494646adb45f5532df8ce7872e502dc94147df`;
- accepted data manifest SHA-256 `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`, report SHA-256 `22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`, classification `PRICE_CORPUS_PARTIAL_RECONCILIATION`;
- eligibility calendar SHA-256 `977b21c4aa342cd3d7391a86a84e2c26ca7afb065fe65259df909652db2933f1`.

The adjacent `btc_eth_grid_4h_v1_history_study_plan.json` is the normative inert plan. Its future executable SHA and run identity are deliberately `null`: only a later architect grant may bind those values. The preparation branch or publication HEAD is not itself an execution grant.

## Deterministic source adapter

`V3GridHistoryAdapter` accepts only a root whose v3 manifest and report bytes match the expected identities. Before it reads values from a Parquet shard, it checks that shard's manifest membership, SHA-256, row count, schema, dataset version and qualification protocol. It reads at most one symbol-week at a time and has no network, repair, interpolation, fallback-source or mutation path.

For an eligible week it exposes:

1. exactly 42 canonical derived-4h closes whose `available_at_ms` values end at Monday 00:00 UTC and are spaced four hours apart;
2. the exact TRADE 1m grid from Monday 00:00 through Sunday 20:01, inclusive;
3. a separate exact MARK 1m grid for the same interval; LAST never substitutes for MARK;
4. original funding event milliseconds, Decimal rate text and the latest completed MARK at or before the event.

Duplicate/off-grid minutes, non-Decimal price text, gaps, mismatched half-open close times, non-causal 4h availability, stale/missing funding MARK, invalid funding order or any hash/provenance mismatch fail closed. The adapter does not silently turn missing funding into zero.

The accepted engine retains its synthetic-only source guard. Synthetic fixture tests may convert fixture rows into the engine's existing typed `GridMinute`/`GridFundingEvent` contract, but this issue adds no market-history engine entry point.

## Calendar and continuous ledger

Every one of the 356 scheduled Mondays per symbol remains in the ordered calendar. A future task owns one independent continuous ledger for one `(symbol, scenario)` pair.

- `METADATA_ELIGIBLE`: the week must produce a complete engine result and authoritative carry. A valid no-fill or pre-order `NO_TRADE_CASH_ONLY` week is included as a zero-return observation. A permanently terminated symbol is also an explicit eligible cash-only zero-return observation.
- `METADATA_INELIGIBLE`: no price row is loaded and no fake Grid episode is constructed. The calendar advances seven days and the exact cash/equity, cumulative realized PnL, fees, funding, peak and permanent termination state carry unchanged. The week is excluded from return denominators and breaks moving-bootstrap blocks.
- `INTEGRITY_BLOCKED/UNKNOWN`: a missing required TRADE/MARK/funding or incomplete engine result is neither an exclusion nor a zero-return observation. The task stops and no authoritative subsequent economic carry is created.

This explicit calendar-only rollover bridges the engine's seven-day carry identity without resetting capital or fabricating an observed flat market. Development, validation and retrospective partitions never reset the symbol ledger.

## Future one-shot shape and bounds

The frozen task order is BTC BASE, BTC STRESS, ETH BASE, ETH STRESS, with concurrency one. The accepted source inventory relevant to the study contains 656 monthly files and 793,405,872 bytes: 328 files / 401,502,309 bytes for each BTC task and 328 files / 391,903,563 bytes for each ETH task. A future implementation must stream one monthly shard or row group at a time, validate source hashes before value reads and record every consumed file identity in the output. Network requests, data repair and raw mutation are zero/forbidden.

The future external ledger must be append-only, reject duplicate starts and refuse continuation of an ambiguous `STARTED` task. It must bind the exact grant, executable, run identity, plan, protocol, parameters, calendar and v3 source identities. It must retain weekly denominators, all fees/funding/fills, exposure, forced exits, inventory, equity/drawdown, exclusions and integrity unknowns. The evaluation gates and four mutually exclusive classifications remain exactly those in protocol v0.2.0.

## Preflight interface and safeguards

The CLI supports only:

```text
python -m scripts.research.grid_history_preflight --repo <checkout> preflight [--data-root <retained-v3-root>]
```

Without a data root it validates repository identities/calendar and reports zero price reads. With the retained root it may validate the manifest/report and Parquet file hashes/headers only; it still reads zero price values and performs zero strategy evaluations. A deliberately exposed `run` subcommand always raises `NOT_AUTHORIZED` so this preparation cannot be mistaken for an executable study.

The optional local metadata-only preflight was run once against the retained accepted root
`C:\Users\elect\Documents\Codex\2026-09-09\x20\issue-239-run\dataset`. It validated all 656
relevant Parquet file hashes and headers (793,405,872 bytes) and the accepted manifest/report;
`price_rows_read=0`, `strategy_evaluations=0`, and `historical_pnl_calculated=false`. This local
location is an availability report, not a portable repository artifact or an execution approval.

## Limitations and next gate

Binance LAST/MARK/funding are external source-venue facts or proxies under fixed WEEX fee assumptions, not proof of WEEX fills, queue behavior, margin tiers, liquidation mechanics or profitability. One-minute OHLC retains intrabar uncertainty and the accepted funding schedule remains partial/uncertain. Prior exposure to these market dates remains selection-informed.

A later issue may authorize one bounded historical run only after independent review of an exact preparation HEAD, exact-head CI and a separate exact-SHA grant/run identity. No automatic Paper, Shadow, Live, deployment or trading authority follows from any later retrospective classification.
