# Issue #161 — one-time ETH scarcity continuation evidence

Status: **COMPLETED, non-economic**. Execution used the exact frozen merged
source SHA `9fa49537f64f5c1713e4f1d49eb244d14cd50495`. No application,
research, strategy, detector, Risk, Safety, corpus, or protocol code changed.
BTC A was **not** rerun; Issue #157 was **not** resumed or mutated.

## Preflight and execution identity

- Protocol: `RANGE_MEAN_REVERSION_CANDIDATE_SCARCITY_CONTINUATION_V1`.
- Locked source: `weex-v3-last-e5b0c4035a19d5a80ca7`.
- Locked dataset: `weex-v1-5m-last-a629a29b580b2e103adba918`.
- Locked dataset manifest SHA-256: `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
- Clean frozen `HEAD`, all 107 locked corpus artifact hashes, #157 A anchor,
  exact B/C definitions, Git LFS availability and exact path-scoped LFS rules
  passed before creating the ledger. A process socket guard rejected outbound
  network access; no credential, historical API, exchange or order path was used.
- Evidence directory:
  `data/research_evidence/range-scarcity-continuation-v1-9fa49537f64f5c17/`.
- New continuation ledger UUID: `0b3788d1-d95a-443c-aa56-882694354337`.
- Stable process/session identifier:
  `issue-161-abb91b64-7b35-47a5-82c2-a81e3fff86d8-pid-10412`.
- Durable revision-0 SHA-256, recorded before B:
  `1fccb761fe2aabb2211a160c6e55e30889c2de5469ef8a14cc0d20fa081628d0`.
- One process executed B once, verified durable `COMPLETED`, then executed C
  once. Final ledger revision is 5; B and C are both `COMPLETED`. No retry or
  replacement ledger was created.
- Ledger UTC unit times: B started `2026-09-25T12:36:34.787022+00:00` and
  completed `2026-09-25T12:44:36.637274+00:00`; C started only afterward at
  `2026-09-25T12:46:01.482505+00:00` and completed at
  `2026-09-25T12:59:09.362873+00:00`.

## Authoritative unit results

| Fact | B — ETH epoch A | C — ETH epoch B |
|---|---:|---:|
| Decision window, UTC `[start,end)` | `2024-07-01` → `2024-12-25` | `2025-04-02` → `2026-01-01` |
| Total 15m observations | 16,992 | 26,304 |
| Context ready / not ready | 16,193 / 799 | 25,505 / 799 |
| `INSUFFICIENT_DATA` / `NOT_RANGE` | 2,960 / 14,032 | 5,125 / 21,179 |
| `VALID_RANGE` / `RANGE_BROKEN` / `RANGE_STALE` | 0 / 0 / 0 | 0 / 0 / 0 |
| `NO_TRADE` / LONG / SHORT candidates | 16,992 / 0 / 0 | 26,304 / 0 / 0 |
| Risk APPROVED / REJECTED | 0 / 0 | 0 / 0 |
| Safety CLEARED / SAFE_MODE / BLOCKED_BY_RISK | 0 / 0 / 0 | 0 / 0 / 0 |
| Candidate inside / outside warm-up | 0 / 0 | 0 / 0 |
| Research-admissible | 0 | 0 |
| Corpus digest | `ca677888a8ed3c3919cc7be3c744504b8c31e605c5c00600ec4c5e0223565833` | `a5df30eb80ddeef20e86ecac45e7946af8fee0be0c0eb137ec6223f91c690d77` |
| Report digest | `2e5d12ae67ecb6e094c91dc11b0620f9a4b5795498f14f4f0f73c6556543d0ec` | `8707c968d8941c1924c767f89d177b210497e4ab25f4ef29d02a46e5507125c6` |
| Compact-record digest | `55a8cd1ba5dc37fc6a99afee02680caa0f7c8422d2257673aee69e4ea8dc25aa` | `8f785a383c08c895491580a9313cd3f200101e31164886580ed898e935941f57` |

The compact records are the authoritative object-shaped projection of each
validated sealed report's counts. They retain **every** exact RangeStatus,
RangeReasonCode, strategy-reason, Risk/Safety reason, UTC-month and terminal
bucket, including zero counts. No counts were recomputed from serialized
observations for this document. Both terminal vectors are respectively
`(799,16193,0,0,0,0,0)` and `(799,25505,0,0,0,0,0)` in canonical terminal
order: context not ready, range not valid, valid-range strategy no-trade,
candidate Risk blocked, candidate Safety blocked, candidate warm-up excluded,
research admissible.

## Anchored projection and split provenance

The project-owned `finalize_continuation` authority produced one projection:

- A: immutable completed #157 anchor only — 52,704 observations, zero
  candidates/admissions, report digest
  `9a856fdb0c2600a87172c2ab795164c8c45a10a4861fb50c3b2c9e750ddd9b7f`.
- B/C: separately authorized continuation reports in the new ledger.
- A/B/C candidate counts: `(0, 0, 0)`; final research-admissible counts:
  `(0, 0, 0)`.
- Descriptive summary: `ZERO_STRATEGY_CANDIDATES`.
- Projection integrity seal / final ledger projection digest:
  `a3274fb1ae8d1292bda0c27ccce5dc51327360c866ed36e2caeca87375b8ae46`.
- The projection explicitly states the separate #157-A and B/C continuation
  lineages. It does not claim one shared process or ledger.

## Immutable artifact inventory

All paths below are relative to the evidence directory above. The SHA-256 and
byte sizes were checked again after the process exited. The two canonical full
reports use the predeclared Git LFS paths; records, ledger, manifest and
projection remain ordinary Git JSON.

| Path | Bytes | SHA-256 |
|---|---:|---|
| `anchored-projection.json` | 1,454 | `8dfb2465aae8e2bfe0d29b914bba920bccf97d5772808837e547641e3392fa1a` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0000.json` | 1,455 | `1fccb761fe2aabb2211a160c6e55e30889c2de5469ef8a14cc0d20fa081628d0` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0001.json` | 1,597 | `7f816099aeecbf9e4fc270c29e48915f7008e01a6d83dc26e2d89db7b0a59073` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0002.json` | 1,815 | `610242e09cdd14d620bca0ea665ca39ce49a3c4ea8ff0d0d4b24953388928e5d` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0003.json` | 1,895 | `007af5a3a5f5993b44212a190c38b4f3dfd3ce90be2b88a5e143e6d1feb38290` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0004.json` | 2,113 | `988a06fb6ca7a768d807aeca3bb191d221373be19cd3d54f6e46ae483f3df433` |
| `ledger/0b3788d1-d95a-443c-aa56-882694354337/0005.json` | 2,175 | `d06feae2035efbf30db810ac220271989fd107d828f46172fd5b4d58609f97af` |
| `run-b-record.json` | 5,243 | `abb84067937b9833843ec35b43268fa1b56ed9ac0435283edd6a5251d73e030f` |
| `run-b-report.json` | 44,793,551 | `5247c0c5896554a4d955f4f7eca2b0f66d73d8ae0c960a38b8e59f53d4c750d6` |
| `run-c-record.json` | 5,243 | `8f6fea0deffd2e49569efef32a787d6973ad9e4e33a2cded351fcf17468c2156` |
| `run-c-report.json` | 69,246,970 | `ef6674a9139c66f6b15fad9b48a402fa1fa5111606e18cce95bb3e1ee0af7c59` |
| `manifest.json` | 2,341 | `9d26e5fb69a83d805b6b1d2d7f9e313f5cc3478d9355846e6d3f43f252112239` |

This is **candidate-scarcity attribution only**. It contains no trades,
execution, PnL, R, win rate, profit factor, leverage, funding, liquidation,
portfolio result or trading authority. The result must not be interpreted as
an economic strategy screen or used to tune thresholds post hoc.
