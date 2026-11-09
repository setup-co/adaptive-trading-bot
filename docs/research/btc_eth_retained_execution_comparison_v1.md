# BTC/ETH retained-decision execution comparison V1

`BTC_ETH_RETAINED_DECISION_EXECUTION_COMPARISON_V1` is the single bounded
Issue #225 diagnostic. It keeps the accepted 15-minute Breakout decisions,
variants, quantities, approved risk, dates and BASE/STRESS costs fixed. The
legacy arm is read from retained Issue #220 all-fill outcomes; it is never
simulated again. Only entry intents returned by the ordinary
`EntryOrderIntentAuthorizer` are passed to the opt-in
`LAST_5M_PROTECTIVE_EXECUTION_V1` adapter.

The real pass is limited to BTCUSDT and ETHUSDT, two variants and two cost
scenarios. It streams each retained result authority once, validates all
candidate/authorized records with the typed codec, reads each canonical 5m
Parquet authority once, freezes the admission-only 168-hour spacing mask
before new outcomes, and durably emits every completed trade row. The
cumulative timer starts at the first real evidence read and is capped at 3,600
seconds; output is capped at 20 MiB and the working set at 1 GiB by the owned
wrapper. A timeout, integrity error, or resource stop is terminal and cannot
be retried or resumed.

The profile is an interval model, not tick-accurate venue execution. It keeps
known-open precedence, stop-first same-bar ambiguity, LAST triggers, fixed 2R,
accepted protection with idealized zero activation delay, gaps and next-open
recovery. Results are selection-informed and not a protected holdout or a
capital-constrained portfolio. Negative complete results end this fixed round;
mixed, sparse, unresolved, or one-asset-positive results are not promotion.
