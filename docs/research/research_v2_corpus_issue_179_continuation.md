# Issue #179 C1 continuation — source-frozen separate generation

The original `issue-179-weex-last-90d-v1` plan, ledger, raw archive, Parquet,
report and manifest are immutable and remain the first-generation record. Its
capture source was `1c723a523544f1e13ab9ae5ac1b63b660b0e3343`. The
corrected offline verifier and this continuation have different source hashes;
neither rewrites the captured source identity.

The accepted first-generation manifest SHA-256 is
`870b4b5676f63c52ba9e2ccbcb1699ed5fd4222779de913dc6d8116fd1d7cee3`.
Its stopped ledger contains 15 distinct attempts: 14 HTTP 200 responses and
one completed, nonretryable `RemoteDisconnected` for `BTCUSDT-04-2/0`. That
chunk remains an explicit 88-open missing interval. No C1 request may retry
it or the 14 successes. Six BTC/ETH anchor-day and ten daily predecessor
responses remain separately reused, unchanged.

After the corrected offline tests, old-archive verification, and full quality
gates pass, the C1 plan freezes exactly the other 2,689 original request
shapes in original order. This permits at most 5,378 new attempts and 5,393
cumulative attempts, below the old 5,408-attempt ceiling. It uses only public
WEEX V3 `historyKlines`, `LAST`, the same 100/100/88 5m day partitions and
predeclared earlier-daily pages, a 20-second timeout, 2 MiB body cap, at
least one second between sends, and at most one retry of an actual timeout or
HTTP 5xx. An isolated durably completed `RemoteDisconnected` is unresolved
and advances to the next shape. Three consecutive such terminal failures
stop the route. DNS/TLS/other non-timeout transport errors, 401/403/429,
redirect/policy failures, and ambiguous/persistence failures stop the route;
there is no automatic restart after a route stop.

Working attempt records are fsync-backed before and after I/O. The immutable
continuation archives contain their exact start/result/raw bytes. The
combined report and Parquet projection are separate from both the original
partial result and the continuation raw archives. The offline verifier
reconstructs the report and every shard from both validated generations plus
the independently hashed predecessor reuse. Data-only contiguous epochs are
explicit; a partial 90-day status never becomes execution or study authority.
Each read consumes a verified in-memory snapshot and demands exact timeframe
grid coverage within one epoch. Decimal aggregation traps any inexact or
rounded operation rather than silently accepting a fixed-precision result.

Full historical contract-unit/effective-date facts and independent daily
comparisons remain visible limitations. No research study, strategy, account,
or runtime allowlist is changed by this continuation.

## Captured C1 outcome and immutable combined projection

Corrected capture/verifier source commit:
`c4e6fffe265b63dff1522abec27f45e32eee66c9`. The separate C1 plan was
committed at `d28de63` and has SHA-256
`c9911bc6917f189f37efea319dc8347d4ce8b063a82299973ba7d7531e11c2f1`.
It declared 2,689 never-attempted shapes and excluded all original attempts.
The one C1 process sent **1,904** distinct new requests: **1,903 HTTP 200**
and **one non-timeout DNS `gaierror`** at `BTWUSDT-09-0/0`, with **zero retries**.
The last record's completion timestamp is `2026-10-14T22:11:06.341916Z`;
there was no HTTP status or body. DNS is route-stopping under the frozen C1
policy, so the remaining **785** shapes were not attempted. The route-stop
record SHA-256 is
`3b2886ae62205e4309da50b8fe0d64481b4f54589c7708dac14db4e18533eb0c`.
There was no restart, new ledger, BTC gap retry, or alternate source.

The validated cumulative ledger therefore has **1,919** distinct transmitted
attempts: the original 15 plus C1's 1,904, **zero retries**, below the
5,393 C1 cumulative maximum. The 16 previously reused responses remain
separate from network attempts. The offline verifier reconstructed every
combined Parquet shard and report from exact first-generation, C1 and reused
raw bytes. Combined report SHA-256:
`b2ea8a706972e8355ceed410a73c21262e1b57d08528dbbc7f9ee52468f1ee45`.
Combined manifest SHA-256:
`1a4e38d06a1847d902645bee287613de0e913ab6d523ebfb6e2424b3cee764ed`.
The manifest binds all ten C1 raw archives, forty 5m/15m/1h/4h shards, the
combined report, both source generations and cumulative counts. Both roots
plus local fsync-backed working files total 45,641,144 bytes; the largest
single file is 1,497,233 bytes, within the 96 MiB total and 16 MiB/file
ceilings. The committed raw archives preserve the exact working ledger bytes.

| Member | Valid 5m rows / 25,920 | Contiguous epochs | Exact daily matches | Data status |
| --- | ---: | ---: | ---: | --- |
| BTCUSDT | 25,832 | 2 | 89 | PARTIAL_OR_BLOCKED — original 88-open gap retained |
| ETHUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| SOLUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| XRPUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| ZECUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| ZAMAUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| BCHUSDT | 25,920 | 1 | 90 | CORPUS_REVIEW_READY |
| BTWUSDT | 2,592 | 1 | 0 (9 unavailable) | PARTIAL_OR_BLOCKED — DNS stop |
| AVAXUSDT | 0 | 0 | 0 | PARTIAL_OR_BLOCKED — not attempted |
| DOGEUSDT | 0 | 0 | 0 | PARTIAL_OR_BLOCKED — not attempted |

BTC's exact excluded interval is
`[2026-07-20T16:40:00Z,2026-07-21T00:00:00Z)`; its two data-only epochs
must never be bridged. Its post-50-complete-4h usable interval begins only at
`2026-07-29T08:00:00Z` in the second epoch. BTW's observed 5m epoch ends at
`2026-07-25T00:00:00Z`; the failed chunk and later unattempted range remain
distinct exclusions. Six complete individual price grids do not qualify the
ten-member panel or any historical contract units. The ten-member usable
overlap is empty and the final combined data gate is `PARTIAL_OR_BLOCKED`.
No strategy study or executable research admission follows from this result.
