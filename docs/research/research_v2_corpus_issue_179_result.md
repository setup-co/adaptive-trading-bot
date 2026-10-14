# Issue #179 — bounded WEEX LAST acquisition result

**Final data gate:** `PARTIAL_OR_BLOCKED` / ten-member corpus **not qualified**.
This is a finite, reproducible transport blocker, not a market-history absence,
strategy result, selection revision or authorization to retry.

The execution started from `main` `8b7d7c19697c6fc1eafcdce7666653861f8dcde3`.
The acquisition source was frozen at commit
`1c723a523544f1e13ab9ae5ac1b63b660b0e3343` and the exact plan was
committed at `e34c189cc3fe8c2576282d338727b3cd310af417`. The source
module SHA-256 is
`fb95f011706a3c00b94cebdf10f943cee62e3fb4f8866e51e811acbcd96601ed`;
the script SHA-256 is
`4a8724ea5b1c461f41d5b73bcdb8bfc257bccdd50e3607930d30cb76632c5640`;
the pre-acquisition official-source/units note SHA-256 is
`7eaa3a08c4f570f4cde8c110e616f55d271719eb45e318b6c81e81ae808582ce`.
The frozen plan SHA-256 is
`b98eb68a1d9d62f04c05a6245d2c2ddd70a85c0cde6b8b5b8559e78cb9dffac6`.
It binds the selected-panel SHA
`80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4`,
selection manifest SHA
`de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79`,
the three #173/#175 predecessor manifests, 16 exact reused raw references,
2,704 distinct new queries and an absolute 5,408-attempt ceiling. The fixed
source is unauthenticated WEEX V3 `5m/1d LAST` history only, over
`[2026-07-16T00:00:00Z,2026-10-14T00:00:00Z)`.

The collector transmitted **15** of the 2,704 planned new shapes, all on
BTCUSDT: **14 HTTP 200**, **one non-timeout `RemoteDisconnected`**, **zero
retries**. The last attempt was the original planned query
`BTCUSDT-04-2`, `5m LAST`, `startTime=1784565600000`,
`endTime=1784591999999`, `limit=100`; its durable start was
`2026-10-14T20:23:26.533622Z` and its sanitized failure record was
`2026-10-14T20:23:27.700064Z`. No response body or HTTP status was available
for it, and no claim about WEEX receipt is made. `RemoteDisconnected` is not
an actual timeout or HTTP 5xx under the frozen rule. Capture therefore stopped
without retry, later query, alternate host, new ledger or replacement plan.
The 14 saved response bodies retain their individual hashes and timestamps
in the deterministic per-symbol archives. The earlier 16 raw responses were
reused from their original #173/#175 generations without modifying them.

## Raw-to-canonical qualification

Every member's expected 5m grid is 25,920 opens. `Received` below is the
number of distinct response rows that passed full eleven-field validation;
there was no malformed row or conflicting duplicate in the saved subset.
`Unresolved` includes unrequested remainder and the failed 88-open chunk.
The original anchor day remains a separate provenance generation.

| Fixed member | New attempts | Reused responses | Received/valid 5m | Unresolved grid | Maximal epochs | Post-200h usable epochs | Fully covered daily checks | Data status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| BTCUSDT | 15 | 4 | 1,640 | 24,280 | 2 | 0 | 1 MATCH; 4 DAILY_UNAVAILABLE | PARTIAL_OR_BLOCKED |
| ETHUSDT | 0 | 4 | 288 | 25,632 | 1 | 0 | 1 MATCH | PARTIAL_OR_BLOCKED |
| SOLUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| XRPUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| ZECUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| ZAMAUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| BCHUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| BTWUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| AVAXUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |
| DOGEUSDT | 0 | 1 | 0 | 25,920 | 0 | 0 | none | PARTIAL_OR_BLOCKED |

BTC's first retained epoch is
`[2026-07-16T00:00:00Z,2026-07-20T16:40:00Z)` (1,352 rows), followed by
the separately reused
`[2026-10-13T00:00:00Z,2026-10-14T00:00:00Z)` anchor (288 rows).
ETH has only that 288-row anchor. The BTC failed chunk is excluded as
`NETWORK_UNAVAILABLE` (88 expected opens); subsequent unattempted opens are
`NOT_ATTEMPTED`, not zero-volume candles or venue-history absence. The first
BTC epoch does not reach the required 50 complete 4h warm-up bars. Neither
anchor can warm independently. Thus no member has a post-warm-up usable
interval and the ten-member overlap is empty. Derived 15m/1h/4h rows exist
only for complete available buckets and are **not** an execution-ready study
corpus. Daily checks on the two saved anchor days match exactly on the
documented OHLC/base/quote/count/taker fields. Four complete early BTC 5m
days have no matching early daily page because the one permitted earlier-1d
query was scheduled after the bounded 5m chunks and never attempted; they are
`DAILY_UNAVAILABLE`, not reconciled or guessed.

All ten symbols retain current-snapshot `COIN/PERPETUAL`, direct underlying,
USDT quote/margin and documented base/quote candle-volume identities.
Historical contract multiplier/order-quantity/fee/precision/size effective
dates remain unresolved for every member. A normalized base-unit research
quantity is only a declared model, not proven historic WEEX contract sizing.
No member is `FULLY_QUALIFIED`, and no runtime/research-execution allowlist was
expanded. The entire window remains selection-informed exploratory data.

## Immutable partial artifact

Version: `weex-research-v2-last-90d-issue-179-v1`.
Corpus root: `data/research_corpus/issue-179-weex-last-90d-v1/`.
Report SHA-256:
`d71e0cf14e3a21ab5996e7debae6808dd3e6c534dc6ee14e39d2e27f511750cd`.
Manifest SHA-256:
`870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3`.
Manifested plan/archive/Parquet/report inventory: 827,473 bytes. The 44
durable same-ledger attempt-start/result/raw files add 179,596 bytes; the
total committed inventory is 1,007,069 bytes, below the 96 MiB total and
16 MiB/file limits. Committing the stopped ledger means a checkout cannot
silently present the partial artifact as an untouched request budget. The
manifest individually hashes each lossless ZIP
archive, each exact-value Parquet shard and the report. The verifier checks
all predecessor hashes and frozen plan/source, reconstructs raw rows,
exclusions/epochs, daily comparisons, derived candles and report, then
compares every Parquet value. It does not need historical Git ancestry or a
network connection. A consistently rehashed false report is rejected by
raw-derived comparison. The explicit reader refuses all these intervals
because none has the required complete/review-ready qualification.

Reproduce without market I/O:

```text
python -m scripts.research.research_v2_corpus verify
```

This partial result is an honest stop under the originally frozen policy;
it does **not** authorize a rescue request, new 90-day budget, panel change,
strategy study, Paper/Shadow/Live admission or trading.
