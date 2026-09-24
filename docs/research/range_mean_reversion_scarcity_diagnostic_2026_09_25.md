# Issue #157 — Range Mean Reversion scarcity diagnostic (stopped after A)

This was the separately activated, non-economic diagnostic under frozen code
`95b2ba209d121cd61a59c3730c36f197ec0690d7`. It used only the locked
WEEX Futures 5m LAST corpus (`weex-v1-5m-last-a629a29b580b2e103adba918`,
source `weex-v3-last-e5b0c4035a19d5a80ca7`, manifest SHA-256
`98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`).
The store verified all 107 artifact hashes before the one-shot process began.
The process used a network-socket guard and no exchange credentials or API
history. Strategy, detector, Risk, Safety, corpus, and source code were not
changed. Issue #153 was not rerun.

The one ledger UUID is `b6b78aa4-3687-44de-9568-b3434f97c075`.
There was **no operator/Codex interruption** during this invocation. Its
original process started Run A once, then wrote ledger revision 2 with
`A=COMPLETED`, `B=NOT_STARTED`, `C=NOT_STARTED`. After saving the full sealed A
report, the evidence-orchestration step raised `TypeError` while trying to
access the report's canonical list serialization as a JSON object. The
captured exception type and file sequence establish the stop point; the
specific expression is identified by source inspection. The process stopped
before B. No unit was retried, no replacement ledger was created, and no
cross-run projection or descriptive cross-run summary exists. This is a
technical stop, not a completed A/B/C scientific result.

Run A's sealed report covers 52,704 BTC/USDT 15m observations. The exact
serialized counts are retained in `run-a-record.json` and the full report:

| A funnel | Count |
| --- | ---: |
| Context ready / not ready | 51,905 / 799 |
| INSUFFICIENT_DATA / NOT_RANGE / VALID_RANGE / RANGE_BROKEN / RANGE_STALE | 9,890 / 42,812 / 1 / 1 / 0 |
| NO_TRADE / LONG_CANDIDATE / SHORT_CANDIDATE | 52,704 / 0 / 0 |
| Candidate inside / outside warm-up | 0 / 0 |
| Risk APPROVED / REJECTED | 0 / 0 |
| Safety CLEARED / SAFE_MODE / BLOCKED_BY_RISK | 0 / 0 / 0 |
| Otherwise admissible / warm-up excluded / final admissible | 0 / 0 / 0 |

Terminal attribution is `CONTEXT_NOT_READY=799`, `RANGE_NOT_VALID=51,904`,
`VALID_RANGE_STRATEGY_NO_TRADE=1`, and zero in all four candidate/admission
terminal buckets; these sum exactly to 52,704. The single VALID_RANGE
NO_TRADE observation carried both `RSI_EXHAUSTION_FAILED` and
`MOMENTUM_EXHAUSTION_FAILED`. The most frequent exact range reasons include
`ENTRY_TREND_TOO_STRONG=31,226`, `EXTREME_VOLATILITY=19,512`,
`HTF_DIRECTIONAL=16,699`, and `NEED_FOUR_CONFIRMED_PIVOTS=9,890`.
These are multi-label occurrences, not exclusive denominators or tuning
recommendations. All reason-code counts, including zero buckets, are in the
record and full report.

The full canonical sealed report is stored in Git LFS because its exact
138,624,150 bytes exceed GitHub's ordinary-file limit. Its SHA-256 is
`ee604aaaa068d9fc085d6e602ad0a152d7924bc03450b11001232363bce26f60`,
corpus digest is
`e89f55f5ba89869773a82f2411e295de09ba7fb6a5ef449d52fce6f1136ea60e`,
and report digest is
`9a856fdb0c2600a87172c2ab795164c8c45a10a4861fb50c3b2c9e750ddd9b7f`.
The captured execution manifest SHA-256 is
`431001f600525cb846b368ebf1a041cdbf1221bed8749ed7a6e21753a73f10e9`.
The post-execution serialization-only record and `evidence-index.json` do
not modify any ledger snapshot or the captured manifest. The evidence-index
SHA-256 is `8ef4a5cb126528729e0f25ed958564db4f0df4180292046e37733b7f16660552`.

No BacktestExecutionSimulator, PositionAccountingEngine, entry/exit replay,
PnL, R, win rate, profit factor, leverage, funding, liquidation, order,
Paper, Shadow, Live, or deployment activity occurred. The A-only counts are
diagnostic facts, not an economic or cross-run conclusion. The frozen study
must not be resumed or repaired by a new UUID; any next diagnostic protocol
requires a separate architect decision and newly frozen code identity.
