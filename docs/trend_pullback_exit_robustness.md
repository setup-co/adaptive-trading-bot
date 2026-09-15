# Trend Pullback paired-exit robustness report

`atb-research-exit-robustness` is an offline, research-only command. It regenerates the
accepted Trend Pullback decision corpus, extends the same Replay → Feature → Structure →
Technical Regime → MTF Regime context path beyond the decision window, and runs the existing
paired exit replay once for every research-admissible identity.

History uses the same fail-closed selector as Replay and Research Corpus. Qualified
`weex-v3-last-*` sources require the exact `--dataset-version` and resolve only through
`LockedWeexResearchCorpusStore` under `ATB_QUALIFIED_RESEARCH_CORPUS_ROOT`; `--data-root` remains
available only for generic sources and cannot override qualified WEEX bytes.

The decision window and evaluation horizon are deliberately separate. `--follow-up-hours`
must be positive and divisible by four. Every admitted decision receives exactly that horizon,
including a decision observed at the corpus end. Missing extended 15m/1h/4h history fails closed;
an open branch at the fixed end remains unresolved and is never force-closed.

Qualified WEEX example:

```console
atb-research-exit-robustness \
  --source weex-v3-last-e5b0c4035a19d5a80ca7 \
  --dataset-version weex-v1-5m-last-a629a29b580b2e103adba918 \
  --symbol BTC/USDT --symbol ETH/USDT \
  --start 2025-04-10T08:00:00Z \
  --end 2026-01-01T00:00:00Z \
  --research-equity 10000 \
  --follow-up-hours 168 \
  --spread-bps 2 --slippage-bps 3 --fee-bps 10 \
  --output artifacts/trend-pullback-exit-robustness.json
```

Generic-source example:

```console
atb-research-exit-robustness \
  --data-root data/history \
  --source binance-public \
  --symbol BTC/USDT --symbol ETH/USDT \
  --start 2025-01-01T00:00:00Z \
  --end 2025-07-01T00:00:00Z \
  --research-equity 10000 \
  --follow-up-hours 168 \
  --spread-bps 2 --slippage-bps 3 --fee-bps 10 \
  --output artifacts/trend-pullback-exit-robustness.json
```

The deterministic JSON preserves Decimal values losslessly and contains population/closure
counts before numeric comparisons. Candidate-minus-baseline numeric deltas exist only for
`BOTH_CLOSED` pairs. Strata cover symbol, side, 15m regime, higher-timeframe bias, MTF alignment,
and UTC entry month, including strata with zero comparable pairs.

Overlap components use half-open intervals `[entry_fill, observation_end)` within each
source/symbol. Touching intervals do not overlap. Raw candidate count is not an independent
sample size. The equal-weight component summary is only a dependence sensitivity description;
it is not a confidence interval, significance result, portfolio return, or independence proof.

The report contains no Paper/Live/Experience integration and provides no trading authority or
strategy activation decision.
