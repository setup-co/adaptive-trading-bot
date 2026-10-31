# LAST_5M_PROTECTIVE_EXECUTION_V1

Status: opt-in research implementation; synthetic validation only  
Version: 1.0.0  
Authority: Issue #223

## Boundary

This profile does not produce or alter a strategy decision. It accepts an existing,
fully authorized `EntryOrderIntent`, so its source, symbol, 15m decision time, strategy,
Risk sizing, Safety clearance and stop basis remain authoritative. It is not registered
for runtime use and does not add 5m or 30m to the runtime timeframe enum. Existing
replay, Paper and comparison defaults retain their prior behavior.

The input is one exact, finite, contiguous UTC 5m LAST series bound to source, symbol,
epoch and range. Missing, duplicate, out-of-order or cross-epoch observations reject
the run. The locked Parquet reader also verifies the supplied artifact hash and exact
schema. MARK triggers are outside this profile and fail closed.

## Modeled chronology

1. Entry eligibility is `authorized_at + configured_entry_latency`.
2. Entry uses the first 5m candle open at or after eligibility. Authorization,
   eligibility, fill clock and grid wait are retained separately. A one-second offset
   from a grid boundary therefore waits 299 seconds; this does not change the legacy
   15m model's corresponding 899-second wait.
3. The entry fill uses the shared spread, slippage and fee authority and is committed
   through the shared average-cost position-accounting core.
4. Protection is requested only after the fill. Rejected or unacknowledged protection
   leaves explicit unresolved exposure. Accepted protection becomes active only after
   its modeled activation latency.
5. For a fully active interval, known open crossings precede intrabar touches. A stop
   gap exits from the adverse open, with configured costs, rather than from the stop.
   If neither open crossing applies, high/low touches are evaluated. A dual touch uses
   stop-first as the primary conservative outcome while retaining target-touch as an
   alternate ambiguity fact.
6. If activation occurs inside a 5m interval and that interval touches a protective
   level, pre- versus post-activation ordering is unknowable. The adapter does not use
   the whole-bar extreme as a fill: it returns explicit ambiguous open exposure.
7. A cost-adjusted entry that invalidates fixed-2R geometry is still a real accounted
   fill. Recovery uses the first genuinely later 5m open and shared costs/accounting;
   absent that later fill, exposure remains unresolved.

An intrabar protective fill timestamp is the interval's exclusive close solely to
provide deterministic accounting chronology. The trigger audit retains the complete
interval and marks holding time as non-exact. Open-event and recovery times are exact
within this bar model. None of these conventions claim tick accuracy, standing venue
order acceptance, or guaranteed execution.

## Retained result facts

The compact result contains profile and decision/binding identities; authorization,
eligibility, grid-wait and activation clocks; entry and exit fill prices/fees; stop,
target and trigger interval/type; gap and ambiguity flags; shared accounting state;
gross/net realized PnL, actual-fill risk-normalized result, unresolved quantity and
holding-time certainty. Terminal results stop consuming later bars, and no result may
close the same position twice.

This implementation authorizes no historical evaluation. Any real-history use needs a
separate reviewed protocol and exact-SHA grant.
