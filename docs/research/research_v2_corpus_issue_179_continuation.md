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
