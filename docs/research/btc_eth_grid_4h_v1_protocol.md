# BTC/ETH neutral geometric Grid 4h V1

## Status, hypothesis and authority

`BTC_ETH_NEUTRAL_GEOMETRIC_GRID_4H_V1` version `0.2.0-proposed` is **PROPOSED / NOT EXECUTABLE**. It specifies one falsifiable linear-USDT-perpetual research model for BTCUSDT and ETHUSDT. It is not registered, implemented, historically evaluated, optimized, or authorized for Paper, Shadow, Live or deployment. The hypothesis is that repeated oscillation inside a fixed corridor can cover all fees, funding and directional-inventory losses; no edge is claimed.

BTC and ETH use the same formulas but independent 10,000-USDT ledgers. Cash, inventory, limits, termination and conclusions never transfer or pool. “Neutral” means only symmetric initial geometry, not persistent delta neutrality or safety. Normative inert values are in `btc_eth_grid_4h_v1_parameters.json`; the metadata calendar, exact synthetic lifecycle examples and compatibility map are adjacent files.

## Weekly decision and fixed geometry

Each episode begins Monday 00:00 UTC. Its initial orders are first considered at Monday 00:01. The scheduled exit decision is Sunday 20:00 and its forced flatten occurs at Sunday 20:01, leaving valid 1m closure data inside the week. A stopped episode stays flat until the next Monday; cash carries continuously.

At Monday 00:00 use exactly **42 completed 4h closes ending with `C`**: `C` and the preceding 41 closes. Their 41 log returns have population standard deviation `sigma41` (divisor 41). Define:

```text
w = max(ln(1.06), 2.5 * sigma41 * sqrt(42))
r = exp(w / 6)
buy[j]  = C / r^j, j=1..6
sell[j] = C * r^j, j=1..6
lower boundary = C / r^7
upper boundary = C * r^7
```

Reject before orders if `w > ln(1.35)`, rounded levels duplicate or misorder, adjacent gross return is below 0.004, or any order is below 5 USDT. Buy prices round down and sells up to 0.01 USDT; quantity rounds down to 0.000001 base units. The centre is fixed for the week. There is no recentering with inventory, martingale, capital top-up or risk increase after loss.

## Finite-capital sizing

At episode start, `E` is current symbol-ledger equity, `B=0.01E`, `rho=0.003`, `XL=lower*(1-0.0015)` and `XU=upper*(1+0.0015)`. Using STRESS fees:

```text
Dlong  = sum_j[(buy[j]-XL) + 0.0008*buy[j] + 0.0008*XL] + rho*sum_j buy[j]
Dshort = sum_j[(XU-sell[j]) + 0.0008*sell[j] + 0.0008*XU] + rho*sum_j sell[j]
qRisk  = B / max(Dlong, Dshort)
qNotional = 0.80E / sum_j(buy[j]+sell[j])
q = floor_to_0.000001(min(qRisk, qNotional))
```

All entry lots are exactly `q`; absolute inventory is capped at `6q`, twelve initial entries at `0.80E` aggregate pending notional, and every activation/fill rechecks both caps. These are modeled economic limits, not liquidation protection. A gap may exceed the 1% budget and remains visible.

## Order ownership, netting and reservations

One signed net position is represented as equal-`q` inventory lots with average-cost accounting. Each entry order owns a stable rung/order ID. A fill in the flat or same direction creates one lot and one adjacent reduce-only exit owned by that lot (rung 1 exits at `C`). An opposite entry consumes exactly one oldest opposite lot, ordered by fill timestamp then lot ID, realizes PnL, and cancels that lot's exit; it never reverses residual quantity. A state not divisible into `q` lots before a normal fill is integrity-invalid.

An owned exit consumes only its remaining owner lot and cannot reverse. If that lot was netted, its exit is canceled; if ownership shrinks unexpectedly, the run is integrity-invalid rather than inventing a partial fill. At the same rounded price, a valid owned exit precedes an entry and suppresses a colliding entry. Otherwise events follow their geometric crossing order. Thus a long `2q` crossing sell exits at 99.03 and 100 before sell entries at 100.98 and 101.97 closes the two owned lots and then creates two short lots; the deterministic result is short `2q`.

After an owned exit consumes a lot, that lot's source entry rung rearms one minute later. When an opposite entry nets a lot, both the consumed lot's source rung and the filled netting entry's rung rearm one minute later; no exit is created for the netting entry. No replacement can fill in its parent's minute. Entry orders reserve one `q` potential lot; owned exits reserve their specific lot. Before activation and again before fill, the risk batch evaluates the worst same-direction inventory and aggregate entry notional. A capacity-rejected order does not fill and is reconsidered at the next minute while the episode remains active.

## Timestamped state and event table

The execution observation for minute `[t,t+60s)` is its 1m open/high/low/close. Open actions are modeled at `t`; ordinary path crossings are a bar-based approximation booked at `t+60s` with a deterministic sequence index. They are not tick-accurate.

| Order | Timestamp | Transition |
|---:|---|---|
| 1 | exact `t` | Apply a funding event timestamped exactly `t` to inventory immediately before `t`. |
| 2 | `t` | Apply due scheduled/risk cancellations and forced closes at the minute open with forced costs. |
| 3 | `t` | For orders active before `t`, traverse the gap from prior close to current open in price order. Fill crossed resting limits at their limits; if a protective boundary is crossed, close the resulting inventory at the adverse open after forced slippage. Earlier modeled fills and their fees are never erased. |
| 4 | `t` | Consider due activations against the current open proxy. A marketable post-only buy (`limit >= open`) or sell (`limit <= open`) is rejected without fill/fee and rearms at `t+60s`. A non-marketable order becomes active and may participate in this minute's completed-bar path. |
| 5 | `(t,t+60s)` | Apply retained funding events at their exact timestamps to post-open inventory. They occur before bar-completion fills. |
| 6 | `t+60s` | Traverse the deterministic OHLC path below, process crossed snapshot orders, then any boundary close. Book every fill/fee in sequence at `t+60s`; newly rearmed orders are ineligible. |
| 7 | `t+60s` | Mark inventory with the completed MARK observation, record equity/peak drawdown, and test the original-capital floor. Funding timestamped exactly `t+60s` belongs to the next minute's step 1 and therefore observes these fills. |

Strict trade-through is required: a buy fills only after modeled price becomes `< limit`, a sell only after `> limit`; touch is unfilled. Fills are full `q` or unfilled and make no capacity/queue claim. An intraminute passive fill uses the limit and passive fee. A resting order crossed by an opening gap uses its limit (a conservative no-price-improvement convention) and passive fee. Newly marketable activations use the post-only rejection rule, not a mislabeled maker fill.

### Deterministic OHLC path and protection

- An opening gap through a boundary follows table step 3: crossed already-resting rungs fill in geometric order before the boundary close at the open. Newly due orders do not exist on that gap.
- If exactly the lower boundary is present in a non-gap minute, traverse `open -> lower`; if exactly upper, `open -> upper`. Orders strictly crossed on that segment fill before the boundary exit.
- If both boundaries occur, simulate the two terminating paths `open -> lower` and `open -> upper` from the same pre-bar snapshot, including fills, fees and forced close. Select the lower ending equity; ties choose lower first. The alternative is retained in the ambiguity audit. No episode or loss disappears.
- With no boundary: long opening inventory uses `open -> low -> high -> close`; short uses `open -> high -> low -> close`. If flat, simulate both and select the lower ending equity; ties choose low first. Each snapshot order fills at most once.

This rule is deliberately adverse and deterministic; it does not claim the unknown tick path. Replacement fills cannot occur inside the same minute.

Every protective boundary exit uses the selected scenario's forced fee and adverse slippage from the boundary price; an opening-gap boundary exit uses the worse open as its pre-slippage price.

## Costs, funding and accounting

BASE passive fills cost 2 bp; forced exits cost 8 bp plus 5 bp adverse slippage. STRESS passive fills cost 8 bp; forced exits cost 8 bp plus 15 bp adverse slippage. Each fee is `abs(qty)*fill_price*rate`. Linear-perpetual cash is initial cash plus realized PnL minus fees plus funding; notional is not exchanged. Equity is cash plus signed inventory times `(mark-average_entry)`.

Retained actual Binance funding events are used as a **source-venue research proxy under an explicit archive-completeness assumption**: every successfully retained monthly funding payload is assumed to contain the venue events it was meant to archive. This does not reconstruct WEEX funding or upgrade `SCHEDULE_PARTIAL_OR_UNCERTAIN`. Original millisecond timestamps are preserved; the observed offsets of at most 47 ms alone are not missing events or unusable records. A usable event has a unique strictly increasing timestamp, finite Decimal rate, and latest completed MARK timestamp `<= event` no older than 120,000 ms. A missing/duplicate/out-of-order event, missing/non-finite rate, raw-resource failure covering a required interval, absent/stale MARK, or uncertain source association makes the episode `INTEGRITY_BLOCKED/UNKNOWN`; payment is never zero-filled and LAST never substitutes for MARK.

```text
BASE funding   = -signed_quantity * mark * reported_rate
STRESS funding = -abs(quantity) * mark * (sign(quantity)*reported_rate + 0.0001)
```

At scheduled flatten, record pre-close marked equity, then the forced fill and costs separately. No unrealized value is counted twice.

## Risk-floor termination and drawdown

The termination trigger is equity `<= 0.80 * original 10,000 USDT`, not running-peak drawdown. At a completed-minute mark that first breaches the floor, cancel all entries/exits immediately and schedule a forced close for the next minute open with scenario forced costs; exact intervening funding still applies. After closing, the symbol ledger is permanently terminated and every later metadata-eligible week is an explicit zero-return cash-only week. If a gap/forced close itself breaches the floor, termination is recorded immediately while flat. Running-peak maximum drawdown is reported separately and has no execution effect.

## Metadata-only eligibility calendar

The calendar reads only accepted v3 quality metadata and proposed calendar fields—zero price rows and no PnL. It binds manifest `9d63123b073e35a010c130308452fd554f707de0e1967f82a752ff44b6155214`, report `22b4cde1ba87f4acdd11efd232b1d0db11f9b9ebdbc6e884cc5d6edf50f7396d`, and classification `PRICE_CORPUS_PARTIAL_RECONCILIATION`. The helper rejects any parameter lookback/timing it cannot represent.

| Symbol | eligible | ineligible | warmup outside | exit outside | active MARK gap | OHLC disagreement warmup | OHLC disagreement active/exit |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 338 | 18 | 1 | 1 | 10 | 3 | 3 |
| ETHUSDT | 345 | 11 | 1 | 1 | 7 | 1 | 1 |

Warm-up requires 42 completed derived-4h closes ending at `C`; MARK is not required there. Active/exit intervals require complete trade-price 1m and MARK 1m. Any OHLC disagreement excludes an intersecting episode, including a combined OHLC/volume discrepancy. A volume-only discrepancy is a non-exclusionary flag because volume is unused. Funding's accepted partial/uncertain status and exact offsets remain flags under the explicit proxy assumption; an actually unusable required event/price later integrity-blocks evaluation.

Partitions are DEVELOPMENT before 2023-01-02 (156 scheduled rows/symbol), VALIDATION from 2023-01-02 through 2025-01-05 (105), and RETROSPECTIVE_ASSESSMENT thereafter (95). The complete file lists every episode and reason. This retrospective mask is sample eligibility, not a causal live filter.

## Deterministic evaluation decision

Each symbol/scenario starts once with 10,000 USDT and runs chronologically across all partitions without reset. Weekly net return is `(post-unwind end equity - pre-episode start equity) / pre-episode start equity`. Every metadata-eligible week is in statistics, including no-fill weeks and post-termination cash-only weeks. Metadata-ineligible/UNKNOWN weeks are recorded with unchanged cash but excluded from return statistics and split dependence blocks; they are never silently zero observations.

Partition total net return is `(partition closing equity - partition opening equity) / partition opening equity`; the opening value is the continuously carried ledger value, never recapitalized to 10,000. Running-peak drawdown uses every completed-minute marked equity from the symbol's original start through the reported partition boundary.

For each symbol/scenario/partition, form every overlapping four-week block wholly inside a contiguous run of eligible Mondays and order blocks by start timestamp. Blocks cannot cross a partition or excluded week. Use PCG64 with seed 241 to draw zero-based block indices uniformly with replacement; retain chronological order inside each block, concatenate and truncate to the observed eligible-week count, and repeat 10,000 times. The mean-return lower bound is sorted replicate index `ceil(0.025*10000)-1` (nearest-rank 2.5%). Fewer than one four-week block is insufficient. Grid cycles, fills and scenarios are never bootstrap units.

VALIDATION passes only if, per symbol: at least 80 eligible weeks and 40 fill weeks; BASE median weekly net return `>0`; STRESS total net return `>0`; BASE bootstrap lower mean bound `>0`; running-peak maximum drawdown `<=20%`; no original-capital termination; and no integrity/accounting unknown. RETROSPECTIVE_ASSESSMENT then requires BASE and STRESS total net equity change and median eligible weekly return all `>0`, drawdown `<=20%`, no termination and no integrity unknown. Development results are descriptive only.

The final mutually exclusive classification is:

1. `TECHNICAL_OR_INTEGRITY_INVALID` for identity, accounting, unusable funding/MARK or unresolved state;
2. `INSUFFICIENT_EVIDENCE` when counts/block construction fail;
3. `NEGATIVE_ECONOMIC_EVIDENCE_NO_PROMOTION` when any economic/capital gate fails;
4. `CANDIDATE_FOR_PROSPECTIVE_CONFIRMATION` only when every validation and retrospective gate passes.

The last class permits only a separately authorized prospective confirmation whose protected period begins after protocol and executable identities are frozen. It authorizes no acquisition, Paper/Shadow/Live admission or trading. Parameters, dates, exclusions, costs and exits cannot change after results to rescue the hypothesis.

## Implementation boundary and limitations

The compatibility map names narrow future adapters through existing Strategy, Risk, Safety, authorization, accounting and journal authorities. Existing runtime behavior and allowlists are unchanged. Binance price/MARK/funding plus WEEX fee assumptions do not reproduce historical WEEX execution, margin tiers, liquidation, queue position or partial fills. The proposal remains vulnerable to gaps, trends, funding, fee drag, coarse OHLC ordering and finite-capital saturation by design.

## Mechanics references

- [WEEX contract fee guidance](https://www.weex.com/help/articles/4415852872601) supplies the declared 2 bp maker / 8 bp taker assumptions; account-specific historical rates remain unverified.
- [Bybit futures Grid P&L mechanics](https://www.bybit.com/en/help-center/article/P-L-Calculations-Futures-Grid-Bot) is a mechanics reference only, not a reproduction target or profitability source.
- Issue #239's retained v3 manifest/report are the sole accepted data-quality authority for the metadata calendar. None of these references is evidence that the proposed model has an edge.
