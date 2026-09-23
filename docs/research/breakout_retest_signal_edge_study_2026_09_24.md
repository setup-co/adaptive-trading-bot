# Issue #149 — locked-WEEX Breakout + Retest fixed-2R signal-edge screen

The architect authorized one execution from the exact clean code SHA
`4ed85e639a84d282522fc73b969e622fb67cdbdd`. Runs A, B, and C completed
once, in that order, without retry or protocol change. The process used a
network-socket guard, no credentials, no historical API, no orders, and no
deployment. Before Run A, `LockedWeexResearchCorpusStore` verified the
accepted manifest and all 107 artifact hashes. The only input was WEEX Futures
5m LAST source `weex-v3-last-e5b0c4035a19d5a80ca7`, dataset version
`weex-v1-5m-last-a629a29b580b2e103adba918`, manifest SHA-256
`98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`.

The frozen entry authority was `breakout_retest` version `1.0.0` with default
configuration. The sole exit policy was `BREAKOUT_RETEST_FIXED_2R_V1`:
target geometry from the actual entry fill, protective-stop precedence for
same-bar stop/target touches, and exit market execution through the unchanged
Backtest simulator after the completed trigger candle. The independent
research equity was 10,000 USDT, follow-up 168h, with spread/slippage/fee
2/2/10 bps and default simulator latency. The ETH excluded interval was not
bridged.

| Unit and decision window (UTC, end exclusive) | Candidates | Unfilled | Invalidated | Replayed | Closed | Unresolved | Closure rate | Stop | 2R target | Same-bar stop precedence | Exit-unfilled | Mean net R | Median net R | R profit factor | Win rate | Total descriptive net R | Max losing streak | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A BTC/USDT, 2024-07-01 → 2026-01-01 | 30 | 0 | 3 | 27 | 27 | 0 | 1 | 19 | 8 | 1 | 0 | -1.141992473406058489280494577083539 | -1.542199466027227722772277227722772 | finite 0.4150345845769817725500709804215195 | 0.2962962962962962962962962962962963 | -30.83379678196357921057335358125554 | 7 | NEGATIVE |
| B ETH/USDT, 2024-07-01 → 2024-12-25 | 16 | 0 | 1 | 15 | 15 | 0 | 1 | 11 | 4 | 1 | 0 | -0.951815793793561585394099352576312 | -0.9609985419807186678352322524101664 | finite 0.3335013286816823995453995129943445 | 0.2666666666666666666666666666666667 | -14.27723690690342378091149028864469 | 6 | NEGATIVE |
| C ETH/USDT, 2025-04-02 → 2026-01-01 | 20 | 0 | 1 | 19 | 19 | 0 | 1 | 13 | 6 | 0 | 0 | -1.153617668108956271425981081705055 | -1.222792454776119402985074626865672 | finite 0.2095876080945117260053149373342291 | 0.2631578947368421052631578947368421 | -21.91873569407016915709364055239605 | 6 | NEGATIVE |

All three units pass the frozen economic-evaluability count/closure gates.
The project-owned frozen cross-run projection returned
`BREAKOUT_RETEST_SIGNAL_EDGE_SCREEN_NEGATIVE`. This is a valid result under
the predeclared costs and windows, not grounds to tune Breakout + Retest V1 on
these same windows. The full sealed reports retain win/breakeven/loss counts,
R-space ranges and averages, side/month strata, and overlap/dependence audit.

The immutable evidence root is
`data/research_evidence/issue-149-breakout-retest-4ed85e639a84d282/`.
Its manifest SHA-256 is
`730fa5437a7487fd3dd21e4e8f7ade5185a77649fc751b751d406fb7f66505ed`
(7,913 bytes). The exact screen projection SHA-256 is
`7995f88b9c8b48d208baabdccd306e7b5599198ad045789bac5b2654231097e8`
(486 bytes).

| Run | Full sealed report SHA-256 | Report bytes | Summary record SHA-256 |
| --- | --- | ---: | --- |
| A | `99429226259cedd2f9488b75dc61c4e0d90448770cbbb4fca8a718a492faba04` | 1,339,810 | `dac8187c8a6a77ad4cf35cfdd779049b36fb57abfbff21aa13a8ef8050a767d3` |
| B | `a6484ad10073cb93e46286552a14cb9742da880594d734399854218a08e801c7` | 716,452 | `be8e576ce7d1d5830ff1ad3334b39cf83d18700096b9f6fc2566c95936a00db1` |
| C | `0f52f1cde6c6bd62d76e7505dece8693c39423bc1678d59e8f1efcbd1734067a` | 899,581 | `83309b3a30d3de4582b392396186db4ad9d08c9d253a2005de01172a41c00cff` |

This is unleveraged, pre-funding historical signal economics, not portfolio
performance or independent-trade proof. Raw candidates and overlapping
positions can share market paths; summed R is descriptive, not portfolio PnL.
The result does not establish complete historical funding, liquidation/MMR
authority, leveraged performance, future profitability, or Paper/Shadow/Live
readiness. Issue #118 remains permanently stopped; the frozen Trend Pullback
#144 evidence remains unchanged.
