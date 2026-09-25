# Range Mean Reversion scarcity continuation — predeclaration only

Issue #159 implements a separate, non-economic continuation protocol,
`RANGE_MEAN_REVERSION_CANDIDATE_SCARCITY_CONTINUATION_V1`. It does **not**
authorize an ETH execution. Issue #157 is closed and permanently stopped with
BTC A complete, ETH B/C not started, and its original ledger unchanged.

The completed BTC A contribution is an immutable anchor, not a reconstructed
report or an executable unit. It binds the accepted #157 execution SHA, UUID,
final ledger snapshot, manifest/index, corpus/report/record hashes, 52,704
observations, zero strategy candidates, zero admissions, and all seven terminal
counts. Small committed #157 evidence files are SHA-256 checked against the
anchor; BTC A's 138 MB report is neither loaded nor re-executed to create it.

The continuation authorizes only exact ETH/USDT B
`[2024-07-01, 2024-12-25)` and C `[2025-04-02, 2026-01-01)` UTC, in that
order, from the same locked WEEX LAST source/dataset/manifest. The ETH
exclusion remains unbridged. The existing default strategy/detector,
Research Risk/Safety, corpus generator, and `project_corpus` remain unchanged.

The new compact record is constructed directly from a validated in-memory
`RangeScarcityReport.counts`. It is deterministic object-shaped JSON with
all exact buckets (including zeroes), reasons, terminal stages, provenance,
report digest, and full canonical report SHA-256/size. It never parses the
canonical report JSON. The full report remains the exact unchanged output of
`RangeScarcityReport.to_json()`. Only future B/C full-report paths matching
`data/research_evidence/range-scarcity-continuation-v1-*/run-[bc]-report.json`
are predeclared for Git LFS; compact records, ledger, manifests, and
projections remain normal JSON.

A future separately authorized execution must create a fresh B/C-only UUID
ledger and durably write revision 0 before B starts. The ledger binds the
frozen execution SHA, WEEX identity, #157 A anchor, and process/session.
Each STARTED unit is irrevocably consumed; a failure, interruption, pending
write, lock, or tampered hash chain fails closed. B must complete before C
starts; finalization requires both completed report and compact-record
digests. No BTC A execution path exists in this protocol.

The eventual descriptive projection combines A values only from the frozen
#157 anchor and B/C values only from their separately validated reports. Its
provenance explicitly distinguishes those two execution lineages and makes no
claim of a single process or ledger. There is no trade simulation, accounting,
PnL, R, win-rate, profit factor, or economic classification.

The scientific outcome of #157 remains A-only; B/C have **not** run under
#159. A separate architect instruction must freeze the merged implementation
SHA and authorize any one-time ETH diagnostic.
