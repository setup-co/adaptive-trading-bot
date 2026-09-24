# Issue #153 — locked-WEEX Range Mean Reversion midpoint signal-edge screen

The architect froze execution code SHA
`cd1f0858a3c6b4bc90c188b20e0c4d9f8db172c7`. Runs A, B, and C each
completed exactly once in that order. The original process continued through
an interrupted Codex turn; it was not restarted. A process network-socket guard
was active, no credentials were used, and there was no historical API, order,
or deployment activity. Before Run A, `LockedWeexResearchCorpusStore` verified
the qualified manifest and all 107 artifact hashes. The immutable WEEX Futures
5m LAST input was source `weex-v3-last-e5b0c4035a19d5a80ca7`, dataset
`weex-v1-5m-last-a629a29b580b2e103adba918`, manifest SHA-256
`98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
The manifest's fixed warm-up exclusions were used; the excluded ETH interval
was not bridged.

The frozen strategy was `range_mean_reversion` version `1.0.0` with the default
`RangeMeanReversionConfiguration` and stateful default `ValidatedRangeDetector`.
The sole exit policy was `RANGE_MEAN_REVERSION_ENTRY_MIDPOINT_V1`. Independent
research equity was 10,000 USDT, follow-up 168 hours, and spread/slippage/fee
were 2/2/10 bps with default simulator latency.

| Unit and decision window (UTC, end exclusive) | Candidates | Entry unfilled | Fill invalidated | Stop-geometry invalidated | Midpoint-geometry invalidated | Replayed | Closed | Unresolved | Closure rate | Stop exits | Midpoint exits | Same-bar stop precedence | Exit unfilled | Unit status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |
| A BTC/USDT, 2024-07-01 → 2026-01-01 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | unavailable (0/0) | 0 | 0 | 0 | 0 | INSUFFICIENT |
| B ETH/USDT, 2024-07-01 → 2024-12-25 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | unavailable (0/0) | 0 | 0 | 0 | 0 | INSUFFICIENT |
| C ETH/USDT, 2025-04-02 → 2026-01-01 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | unavailable (0/0) | 0 | 0 | 0 | 0 | INSUFFICIENT |

Each frozen report has zero closed outcomes. Mean/median net R, R-space profit
factor, win rate, and target/stop reward-risk median/min/max are therefore
**unavailable**, not zero. Positive, negative, and total descriptive net R are
all `0` as empty sums; the maximum chronological losing streak is `0`.
Win/breakeven/loss counts are `0/0/0`. No entry-side or UTC-month outcome
strata exist. There are no replayed overlap components. No position, exit,
fee, PnL, or target-geometry observation can be inferred from this empty
candidate population.

The frozen project-owned cross-run projection returned
`RANGE_MEAN_REVERSION_SIGNAL_EDGE_SCREEN_INSUFFICIENT`. This is a valid
scientific result: the exact V1 rule produced no research-admissible entry
candidate in these windows, so the screen cannot establish positive or
negative trade economics. It is not grounds to tune thresholds or rerun the
same units after seeing the result.

The immutable evidence root is
`data/research_evidence/issue-153-range-mean-reversion-cd1f0858a3c6b4bc/`.
Its manifest SHA-256 is
`7ad0be338213acca4e094eb41de4873780d16f850044de76b9423d30360e570a`
(2,854 bytes). The exact screen projection SHA-256 is
`c51aa6c37a908d55e866106e8bd9166f913f5c64173d2d2f8dbeffa399ac16af`
(512 bytes).

| Run | Full sealed report SHA-256 | Report bytes | Per-run record SHA-256 |
| --- | --- | ---: | --- |
| A | `12111c5be3a2364b0ae749c69f8238d3361ba228ad5c461645c361cace6183e9` | 3,944 | `eedd6f1ea7ef629ffe1097e3ffe8cdf88cb2eb470fa3ac4f4d98944f2f41901f` |
| B | `89dd15cbe53865f74919ae6b5a8d3646451ed43397e5d1e4f5f9b6b2587d3fc5` | 3,944 | `a765a27be34b7221835ef242b9b1808551cebb23f5b58a1bba2e90626a499b99` |
| C | `2071f21daf6197668f4a86dd294eaa47a6fceb8b4fe82633f951f664398c1983` | 3,944 | `902aa6669666d2b8e0e28ff48298f622e3e71ef8294d698c6229c0466654c0f8` |

This is research-only, unleveraged, and pre-funding. It does not supply complete
historical funding, liquidation/MMR authority, portfolio PnL, independent
candidate sampling, future-profitability proof, or Paper/Shadow/Live authority.
Prior Trend Pullback and Breakout + Retest negative evidence remains unchanged.
