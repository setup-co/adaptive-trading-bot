# Issue #181 — bounded C2 Research V2 LAST tail

This is data-quality evidence, not executable research admission or a strategy
study. The selected current-snapshot panel and original interval remained
unchanged: ten symbols over `[2026-07-16T00:00:00Z,
2026-10-14T00:00:00Z)`. Only public WEEX V3 `historyKlines`, `LAST`, the
original 5m/earlier-1d parameters and `limit=100` were used. No credentials,
account endpoint, alternate venue, repair, interpolation or strategy run was
used.

## Frozen provenance and actual traffic

- Exact base `main`: `5e34486664a37641c3ff0b5b3827087bd91d83f5`.
- Executed C2 adapter/CLI source commit:
  `75a3e44831a68f923c1fe067d46b1b4ed65b2c2b`. The plan, committed
  separately after the tooling, pins this source revision and the SHA-256 of
  the accepted original/C1 helpers and new adapter/CLI.
- C2 plan SHA-256:
  `345c797e4eb740dc9e43a23847f67be15411164d4d4888103c86f64091d1ab16`.
  Dataset version: `weex-research-v2-last-90d-issue-179-combined-v2`.
- The original 15-attempt and C1 1,904-attempt archive-backed ledgers were
  validated before the first C2 GET. They have 1,919 distinct prior attempts,
  no retries, and their original `BTCUSDT-04-2` and C1 `BTWUSDT-09-0`
  nonretryable failures remain excluded.
- The frozen C2 difference comprised **785 never-attempted IDs**, ordered
  from `BTWUSDT-09-1` through `DOGEUSDT-earlier-daily`: BTWUSDT 242 5m + one
  earlier 1d, AVAXUSDT 270 5m + one earlier 1d, and DOGEUSDT 270 5m + one
  earlier 1d. No other symbol or failed predecessor query was transmitted.
- One serial C2 process completed all **785** GET attempts: **785 HTTP 200**,
  **zero retries**, zero route stops. The cumulative three-generation ledger
  has **2,704 distinct attempts, 2,704 attempts, zero retries**: 2,702
  HTTP-200 results and the two preserved non-HTTP predecessor failures.
  No previous generation was reopened or retried.

Immutable predecessor identities remain:

| Generation | Plan SHA-256 | Manifest SHA-256 | Report SHA-256 |
| --- | --- | --- | --- |
| Original | `b98eb68a1d9d62f04c05a6245d2c2ddd70a85c0cde6b8b5b8559e78cb9dffac6` | `870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3` | `d71e0cf14e3a21ab5996e7debae6808dd3e6c534dc6ee14e39d2e27f511750cd` |
| C1 | `c9911bc6917f189f37efea319dc8347d4ce8b063a82299973ba7d7531e11c2f1` | `1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed` | `b2ea8a706972e8355ceed410a73c21262e1b57d08528dbbc7f9ee52468f1ee45` |

C1 route-stop SHA-256 remains
`3b2886ae62205e4309da50b8fe0d64481b4f54589c7708dac14db4e18533eb0c`.
The C2 root is separate at
`data/research_corpus/issue-179-weex-last-90d-tail-v1/`; its exact durable
working records, deterministic archives, report, twelve new Parquet shards,
plan and manifest are retained. Twenty-eight unchanged Parquet shards are
referenced by the exact C1 manifest hashes rather than refetched or copied.

| C2 evidence | SHA-256 |
| --- | --- |
| Combined report | `efd51747baf786ad637ec0814eb94ade83bee9a46c4fe5941f4d0fd97a9f2ca9` |
| Manifest | `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd` |
| BTWUSDT raw archive | `c06a802e0d630a9ff0210e0cfb18362210db5a387863b183677fe2740cd1222e` |
| AVAXUSDT raw archive | `4e968c53bd3ab47eeac43de91e918f96ccdd7fdf12bb6b2e3ab20b6e9b948d51` |
| DOGEUSDT raw archive | `f0d54e0bab2a9ef122188e721b3ccf9ede9ba3cbce02d94421ae86c88a231327` |

The seven other C2 per-symbol ZIPs are deterministic empty archives with
SHA-256 `8739c76e681f900923b900c9df0ef75cf421d39cabb54650c4b9ad19b6a76d85`.
The manifest pins every new shard. The three roots together retain
**63,529,262 bytes** in **8,242 files**, including local working records;
the largest file is **1,497,233 bytes**. Thus the actual retained evidence is
under 96 MiB total and 16 MiB per file.

## Data-quality projection

The accepted parser, exact-Decimal arithmetic, grid and OHLCV checks,
complete-bucket 15m/1h/4h derivation, independent daily comparisons, epoch
splitting and 50-complete-4h warm-up were reused unchanged. Every included
daily comparison was `MATCH` with zero exact deltas. The warm-up boundary
observation is excluded. A candle open is not its completed-candle observation
timestamp.

| Symbol | Valid / expected 5m | Excluded | Derived 15m / 1h / 4h | Daily matches | Data-only status |
| --- | ---: | ---: | ---: | ---: | --- |
| BTCUSDT | 25,832 / 25,920 | 88 | 8,610 / 2,152 / 538 | 89 | `PARTIAL_OR_BLOCKED` |
| ETHUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| SOLUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| XRPUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| ZECUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| ZAMAUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| BCHUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| BTWUSDT | 25,820 / 25,920 | 100 | 8,606 / 2,151 / 537 | 89 | `PARTIAL_OR_BLOCKED` |
| AVAXUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |
| DOGEUSDT | 25,920 / 25,920 | 0 | 8,640 / 2,160 / 540 | 90 | `CORPUS_REVIEW_READY` |

The BTC failed-query exclusion is
`[2026-07-20T16:40:00Z, 2026-07-21T00:00:00Z)`; its valid epochs split there.
Its post-reset data-only usable interval starts `2026-07-29T08:00:00Z`.
The BTW failed-query exclusion is
`[2026-07-25T00:00:00Z, 2026-07-25T08:20:00Z)`; its pre-gap usable
interval is `[2026-07-24T08:00:00Z, 2026-07-25T00:00:00Z)`, and the
post-gap interval starts `2026-08-02T20:00:00Z`. Each other member's usable
interval starts `2026-07-24T08:00:00Z`. All end at
`2026-10-14T00:00:00Z` where applicable. The intersection of all ten
data-only usable intervals is
`[2026-08-02T20:00:00Z, 2026-10-14T00:00:00Z)`.

The combined manifest remains **`PARTIAL_OR_BLOCKED`** because the two
predecessor failed-query holes are explicit; their missing opens were not
repaired. `CORPUS_REVIEW_READY` on an individual row and the common data-only
overlap do not qualify historical contract multipliers/quantity semantics or
authorize an executable study. The current-catalog identity limitation and
normalized one-base-unit, unleveraged/pre-funding distinction remain as in
the accepted source/units protocol. Research admission requires a separate
reviewed manifest/epoch binding and activation annex.

## Offline verification

The verifier re-read both predecessor archives and C2's durable/archive
records, checked exact request/stop/cumulative provenance, rebuilt all grids,
daily comparisons, epochs and derived tables from raw, and matched the
published report and Parquet bytes/semantics. The locked reader has no live
fallback and rejects unknown version/hash, off-grid and cross-gap requests.
The full unchanged Python suite passed (1,899 tests); Ruff format/lint,
Mypy including the new CLI, and frontend lint/typecheck/tests/build passed
before acquisition. The real post-capture offline verifier also passed.
