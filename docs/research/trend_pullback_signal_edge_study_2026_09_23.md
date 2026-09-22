# Issue #144 — locked-WEEX Trend Pullback signal-edge screen

The architect activated one execution from code SHA
`b07e9eac7f373c4bc1b79a286714df00ae817ec2`. Runs A, B and C completed
once, in that order, on the exact locked WEEX Futures 5m LAST corpus. The
`LockedWeexResearchCorpusStore` verified the accepted manifest and all 107
artifacts before Run A. A network-socket guard was active during execution;
no historical API, private API or order route was used. There was no retry,
parameter change, data repair, alternate source, or ETH-gap bridge.

The accepted source is `weex-v3-last-e5b0c4035a19d5a80ca7`; dataset version
`weex-v1-5m-last-a629a29b580b2e103adba918`; corpus manifest SHA-256
`98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.
Each independent run used 10,000 USDT research equity, 168-hour follow-up,
2-bps spread, 2-bps slippage, 10-bps fee and default simulator latency.

| Run | Window (UTC, end exclusive) | Candidates | Filled | Unfilled | Invalidated | Replayed | Closed | Unresolved | Mean net R | Median net R | R profit factor | Win rate | Total descriptive net R | Max losing streak | Unit |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A BTC/USDT | 2024-07-01 to 2026-01-01 | 146 | 146 | 0 | 7 | 139 | 139 | 0 | -0.7518080521485210338600983957312014 | -0.9918292297049180327868852459016392 | 0.3557357911624508352688893680287328 | 0.1510791366906474820143884892086331 | -104.5013192486444237065536770066371 | 20 | NEGATIVE |
| B ETH/USDT | 2024-07-01 to 2024-12-25 | 47 | 47 | 0 | 2 | 45 | 45 | 0 | -0.6334044403072824149482254237119444 | -0.7763310668993020937188434695912264 | 0.2895712000819311256106036891045271 | 0.2222222222222222222222222222222222 | -28.50319981382770867267014406703748 | 15 | NEGATIVE |
| C ETH/USDT | 2025-04-02 to 2026-01-01 | 80 | 80 | 0 | 4 | 76 | 76 | 0 | -0.05170468029351194704898837876875320 | -0.898613281738437001594896331738437 | 0.9435833896218618212029193282681605 | 0.1842105263157894736842105263157895 | -3.92955570230690797572311678642526 | 13 | NEGATIVE |

All three units are economically evaluable under the frozen count/closure
gates. Each has a finite R-space profit factor. The frozen cross-run result is
`SIGNAL_EDGE_SCREEN_NEGATIVE`. This is a valid scientific result under the
predeclared costs and windows; it is not a reason to tune Trend Pullback V1
against the same data. The full sealed paired reports retain the candidate-vs-
baseline exit deltas, censoring, strata, stability and overlap audit; the
absolute R projection does not replace that paired comparison.

The complete immutable evidence is under
`data/research_evidence/issue-144-signal-edge-b07e9eac7f373c4b/`. The
manifest SHA-256 is
`e4be5b89615197b9854e9137eae1a75b91c4d37b356d6e696b168c94e7e0d0c9`
(4,977 bytes). The screen projection SHA-256 is
`5a9e1ff80dbe9aeacbfe9d231f398eb9310182f71e9d77d7afabca2b99b27496`.

| Run | Full paired report SHA-256 | Economic projection SHA-256 |
| --- | --- | --- |
| A | `55efd1eae31872a14de1cb8f03aec5e5a109972ce6124794616ca363293c7f70` | `09b4ab623b438c984569338dcd8518b14da49cf244d611f96053ef759b32b511` |
| B | `e70ca2eaa39b113778f634bb3547f264ee0c515b07998f4a6066e79fb793668c` | `46226ad91767badb29e8de7930c0ac703e7bfae3a22a6639b67269fc42b9d252` |
| C | `8bf3e493a77927306a7b7c2111095ae7591b4acb525b7ddbb7544efb5e4847da` | `d0c53a7c849f307f03a1bb47b600193e458fc04f43a1161b9a5e24b443516661` |

This is unleveraged, pre-funding historical signal economics, not portfolio
performance or independent-trade proof. Overlapping trades may share market
paths; summed R is descriptive, not portfolio PnL or return. The study does
not establish historical liquidation/MMR realism, complete funding,
leveraged performance, future profitability or Paper/Shadow/Live authority.
Issue #118 remains permanently stopped and is not reinterpreted by this study.
