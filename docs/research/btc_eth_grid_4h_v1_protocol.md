# BTC/ETH neutral geometric Grid 4h V1

## Status and scope

`BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` version `0.1.0-proposed` is **PROPOSED / NOT EXECUTABLE**. It is one falsifiable research specification for linear USDT-margined BTCUSDT and ETHUSDT perpetuals. It is not a runtime strategy, backtest implementation, recommendation, or claim of profitability. No strategy evaluation, signal generation, execution simulation, optimization, or PnL calculation was performed for this delivery.

The two symbols use identical formulas and parameters but independent USDT ledgers, each initialized with 10,000 USDT. Capital, inventory, losses, risk limits and termination never transfer across symbols; results must be reported per symbol, never as a combined portfolio. “Neutral” describes symmetric initial price geometry, not delta neutrality, risklessness, or market neutrality after fills.

The normative inert parameters are in `btc_eth_grid_4h_v1_parameters.json`. The complete retrospective metadata calendar is in `btc_eth_grid_4h_v1_eligibility_calendar.json`; synthetic arithmetic is in `btc_eth_grid_4h_v1_accounting_examples.json`.

## Episode and decision chronology

An episode starts every Monday at 00:00:00 UTC. The strategy may use only information from completed candles at that instant. Initial orders activate at 00:01. The scheduled exit decision occurs Sunday at 20:00 and forced flattening activates at 20:01, inside the same fixed UTC week. After a protective stop, the symbol remains flat until its next scheduled Monday. A 20% drawdown from that symbol ledger's original capital terminates all subsequent episodes for that symbol.

The decision clock is completed 4h candles; standing-order execution and protection use 1m observations. These clocks are deliberately distinct. At episode start, use the close `C` of the completed 4h candle ending at 00:00 and the preceding 42 completed 4h closes. From their 41 log returns compute population standard deviation `sigma41` (divide by 41). Define:

```text
w = max(ln(1.06), 2.5 * sigma41 * sqrt(42))
r = exp(w / 6)
buy[j]  = C / r^j, j=1..6
sell[j] = C * r^j, j=1..6
lower boundary = C / r^7
upper boundary = C * r^7
```

Reject the episode before orders if `w > ln(1.35)`, any rounded level is duplicated or misordered, any adjacent gross return is below 0.004, or any proposed order is below 5 USDT. Prices round to 0.01 USDT toward the less aggressive limit (buy down, sell up); base quantity rounds down to 0.000001. The centre never recentres while inventory is open. There is no martingale, capital top-up, or post-loss leverage increase.

## Finite-capital sizing

At episode start let `E` be the symbol ledger equity, `B=0.01E`, `rho=0.003`, and use STRESS costs for sizing. Let `XL=lower_boundary*(1-0.0015)` and `XU=upper_boundary*(1+0.0015)`. Per one base unit:

```text
Dlong  = sum_j[(buy[j]-XL) + 0.0008*buy[j] + 0.0008*XL] + rho*sum_j buy[j]
Dshort = sum_j[(XU-sell[j]) + 0.0008*sell[j] + 0.0008*XU] + rho*sum_j sell[j]
qRisk  = B / max(Dlong, Dshort)
qNotional = 0.80E / sum_j(buy[j]+sell[j])
q = floor_to_0.000001(min(qRisk, qNotional))
```

All twelve initial entry orders have quantity `q`; maximum absolute inventory is `6q`. The total initial pending notional must not exceed `0.80E`. These controls bound the modeled corridor loss, not liquidation or gap risk. A boundary gap may lose more than 1%; the report must show that exception rather than cap it synthetically.

## Orders, inventory and replacements

The model has one signed net position per symbol and uses average-cost accounting. A same-direction entry adds a `q` lot. An opposite order reduces inventory by at most `q`; it never reverses excess quantity. Each filled entry creates a reduce-only exit one adjacent rung toward the centre (rung 1 exits at `C`). Once that exit fills, its entry rung is replaced after one full minute. A replacement cannot fill in the minute that caused its parent fill.

At any price, a reduce-only exit has priority and a colliding entry is suppressed. There may be only one active order per side and rounded price. This prevents a paired exit and a new entry from double-counting the same 1m observation.

## Execution, protection and ambiguous minutes

Standing buy limits fill only when the 1m low is strictly below the limit; sells fill only when the high is strictly above it. A touch alone is not a fill. Fills are full quantity or unfilled: partial fills and queue position are unavailable, so the model must not claim venue accuracy.

Each minute is processed in this order:

1. apply funding events at their retained timestamps to inventory immediately before the event;
2. activate/cancel orders whose one-minute delay has elapsed;
3. test protective boundaries against the minute open/high/low;
4. evaluate only the active-order snapshot that existed before this minute;
5. apply the deterministic fill batch through shared accounting;
6. mark inventory and test ledger drawdown.

If the minute opens beyond a protective boundary, close at the worse open after forced slippage. Otherwise `low <= lower` or `high >= upper` triggers the boundary before ordinary fills. If both boundaries appear in one OHLC minute, use the boundary adverse to the opening inventory; if flat, classify the episode `AMBIGUOUS_BOUNDARY` and do not invent an ordering.

When both a buy and sell are strictly crossed in one non-boundary minute, do not assume a profitable sequence. For long inventory execute only crossed buys; for short inventory execute only crossed sells; when flat execute neither. Deferred orders remain active. This conservative adverse-only rule is disclosed separately in results.

## Costs, funding and accounting

BASE charges 2 bp for passive fills, 8 bp plus 5 bp adverse slippage for forced exits. STRESS charges 8 bp for passive fills, 8 bp plus 15 bp adverse slippage for forced exits. Fees are `abs(quantity)*fill_price*rate` on every fill.

Linear-perpetual cash changes are realized PnL minus fees plus funding; opening notional is not exchanged. Equity is cash plus signed quantity times `(mark-average_entry)`. Funding at actual retained timestamp `t` uses the latest completed MARK no older than 120 seconds:

```text
BASE cash change   = -signed_quantity * mark * reported_rate
STRESS cash change = -abs(quantity) * mark * (sign(quantity)*reported_rate + 0.0001)
```

Missing funding is `UNKNOWN`, never zero; LAST cannot replace MARK. At scheduled flatten, record pre-close marked equity separately, then close at the next minute open with forced costs. This avoids counting unrealized PnL twice. The examples file fixes long/short paired exits, gap closure, ambiguity, funding and terminal valuation with exact decimal strings.

## Metadata-only eligibility calendar

The calendar reads only the accepted v3 qualification manifest (SHA-256 `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`) and the proposed parameter file. It reads zero price rows and calculates no PnL. It enumerates all 356 Monday episodes per symbol between 2020-01-01 and 2026-11-01, including exclusions:

| Symbol | eligible | ineligible | warmup outside | exit outside | active MARK gap | price disagreement in warmup | price disagreement active/exit |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 338 | 18 | 1 | 1 | 10 | 3 | 3 |
| ETHUSDT | 345 | 11 | 1 | 1 | 7 | 1 | 1 |

Known price disagreements are exclusionary wherever they intersect warmup or active windows: BTC at 2023-11-10 16:00, 2024-10-28 20:00 and 2025-01-14 12:00 UTC; ETH at 2024-10-28 20:00 UTC. Volume-only or combined discrepancies are flags, not exclusions, because volume is not an input. The accepted funding authority remains `SCHEDULE_PARTIAL_OR_UNCERTAIN`, so every episode carries that explicit flag; execution would require a later protocol-bound decision on whether the actual retained events suffice. This specification does not silently upgrade them.

The fixed partitions are 156 episodes per symbol in DEVELOPMENT (before 2023-01-02), 105 in VALIDATION (through 2025-01-05), and 95 in RETROSPECTIVE_ASSESSMENT. These retrospective labels prevent sample-selection ambiguity; they are not causal live filters.

## Predeclared evaluation and gates

A later exact-SHA grant would evaluate BASE and STRESS separately, per symbol and partition. Required outputs are episode counts/reasons, fill counts, time in market, maximum inventory/notional, realized/unrealized/funding/fee decomposition, forced closures, ambiguous minutes, maximum drawdown, terminal equity, and capacity failures. Weekly returns use every metadata-eligible week, including zero-trade weeks.

Primary gate (per symbol, never pooled): at least 80 eligible VALIDATION episodes, at least 40 episodes with a fill, positive median weekly net return under BASE, positive total net return under STRESS, 95% bootstrap lower bound of mean BASE weekly return above zero, maximum drawdown at most 20%, and no unresolved integrity/accounting state. Promotion additionally requires consistent sign in RETROSPECTIVE_ASSESSMENT. Failing any gate is `NO_PROMOTION`; sparse output is not repaired by tuning.

The model is intentionally vulnerable to trends, gaps, fee drag, funding, coarse intraminute ordering and finite-capital saturation. Those are falsification channels, not implementation defects to optimize away.

## Implementation boundary

The compatibility map documents the existing typed authorities and the later adapters they would need. The current single-candidate strategy/risk/intent and market-like execution contracts cannot truthfully represent a standing ladder without narrow extensions. `PositionAccountingCore` is reusable. No code in this delivery registers a strategy, changes runtime behavior, or authorizes historical execution.
