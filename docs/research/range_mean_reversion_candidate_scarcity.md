# Range Mean Reversion V1 candidate-scarcity diagnostic — implementation freeze

Issue #155 implements a **non-economic** attribution of the already-sealed
Range Mean Reversion research decision population. It does not execute the
locked WEEX A/B/C units. An architect must review and merge the implementation,
freeze the resulting code SHA, and separately activate one real diagnostic pass.
It must never be used to retroactively rerun Issue #153 or tune the current
strategy/detector thresholds.

## Historical and decision authority

The future diagnostic requires `LockedWeexResearchCorpusStore` for WEEX Futures
5m LAST source `weex-v3-last-e5b0c4035a19d5a80ca7`, dataset
`weex-v1-5m-last-a629a29b580b2e103adba918`, and qualified manifest
SHA-256 `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
The accepted A/B/C decision windows are exactly the #153 windows, without an
economic follow-up:

| Unit | Symbol | Decision window `[start,end)` UTC |
| --- | --- | --- |
| A | BTC/USDT | 2024-07-01 → 2026-01-01 |
| B | ETH/USDT | 2024-07-01 → 2024-12-25 |
| C | ETH/USDT | 2025-04-02 → 2026-01-01 |

The excluded ETH interval is never bridged. Warm-up intervals come only from
the locked manifest. The existing stateful `RangeMeanReversionHistoricalResearchCorpus`
validator replays the unchanged default Strategy, detector, Risk, and Safety
authority over every consecutive 15m observation before the projector reads
any facts. The projector does not evaluate a new strategy, simulate a fill, or
call execution/accounting. The explicit research Safety profile remains an
assumption, not a claim that historical operational health was observed.

## Attribution semantics

Readiness is exactly `features_15m.is_fully_warmed` **and**
`multi_timeframe_regime.is_fully_ready`. Every sealed decision contributes one
raw range status and strategy action; reason counts retain every occurrence in
their original multi-label enums. `NO_TRADE` reasons are also counted only
within `VALID_RANGE` observations. Candidate month is its UTC decision month.
Risk and Safety status/reason counts use candidate decisions only. Candidates
inside manifest warm-up remain visible in all raw funnels.

Each observation receives one terminal attribution in this order:

1. `CONTEXT_NOT_READY`
2. `RANGE_NOT_VALID` (any status other than `VALID_RANGE`)
3. `VALID_RANGE_STRATEGY_NO_TRADE`
4. `CANDIDATE_RISK_BLOCKED`
5. `CANDIDATE_SAFETY_BLOCKED`
6. `CANDIDATE_WARMUP_EXCLUDED` (otherwise admissible only)
7. `RESEARCH_ADMISSIBLE`

Terminal counts must sum to the full 15m population. Earlier-stage attribution
does not suppress the raw lower-stage status or reason counts. This is a
descriptive, ordered funnel, **not** proof that correlated filters causally
eliminated an otherwise-identical trade.

The sealed cross-run projection reports exact A/B/C populations, report
digests, terminal counts, candidate counts, and final admissions. Its only
summary is one of `ZERO_STRATEGY_CANDIDATES`,
`CANDIDATES_EXIST_BUT_NONE_ADMITTED`, or
`RESEARCH_ADMISSIBLE_CANDIDATES_EXIST`. These are not economic classes and
carry no PnL, R, win-rate, or profit-factor claim.

## Future one-shot execution provenance

Before future real Run A, a dedicated execution ledger must be created with a
stable run UUID, frozen code SHA, process/session ID, UTC creation time, and
source/dataset/manifest identities. It writes immutable numbered JSON
snapshots linked by SHA-256. Each snapshot is flushed and atomically installed;
the directory is also synced where the platform supports it. An orphan pending
file or transition lock fails closed. A unit's `STARTED` snapshot is durable
before corpus generation begins. Only `NOT_STARTED → STARTED → COMPLETED` or
`NOT_STARTED → STARTED → FAILED` transitions are allowed in A/B/C order.
The completed unit stores corpus/report digests and cannot rerun under that
ledger. A crashed `STARTED` or explicit `FAILED` unit is not automatically
retried. The final projection digest is appended only after all three units
complete, and future evidence must retain the ledger files and their hashes.

This issue only tests that contract with synthetic fixtures. It produces no
real locked-WEEX diagnostic result and gives no Paper/Shadow/Live authority.
