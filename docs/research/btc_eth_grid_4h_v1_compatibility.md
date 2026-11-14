# BTC/ETH Grid 4h V1 compatibility map

Status: **PROPOSED / NOT EXECUTABLE**. This map identifies later extension seams; it does not make the Grid eligible for runtime or research execution.

| Existing authority | Reusable contract | Required future narrow adapter | Incompatible shortcut |
|---|---|---|---|
| `strategies/core.py` (`StrategyEvaluationContext`, `StrategyEvaluation`) | typed strategy identity, closed-candle context, auditable decision | a Grid episode/ladder decision type carrying the frozen 4h centre, volatility, levels and episode identity | emitting twelve orders as twelve unrelated 15m single-candidate evaluations |
| `risk/core.py` (`RiskManager`, `RiskPortfolioSnapshot`) | fail-closed limits and risk reasons | a finite-capital ladder risk decision covering aggregate pending notional, lot reservations, maximum inventory, boundary loss, the original-capital floor and forced-close ownership | authorizing each rung independently or confusing report-only peak drawdown with the termination floor |
| `risk/safety.py` (`OperationalSafetyGate`) | health, freshness and emergency-stop authority | a Grid safety snapshot that proves 4h decision inputs plus 1m TRADE/MARK/funding freshness | treating missing MARK/funding as zero or healthy |
| `orders/intent.py` (`EntryOrderIntentAuthorizer`) | explicit identity and authorization result | a batch ladder authorizer with stable order/lot ownership, activation minute, post-only rejection, reduce-only priority and aggregate-cap checks | serially authorizing orders without a batch invariant or leaving orphan exits after netting |
| `execution/simulation.py` | typed execution side and cost configuration | standing-limit, strict-trade-through, timestamp/sequence identity, one-minute activation and the frozen adverse OHLC-path adapter | representing resting limits as immediate market-like fills or erasing fills before a later boundary |
| `accounting/positions.py` (`PositionAccountingCore`) | average-cost multi-fill transitions, realized PnL and fees | signed equal-lot Grid wrapper that submits ordered fill batches, consumes opposite lots FIFO and records exact-timestamp funding separately | replacing the accounting core, reversing from a reduce-only order, or netting BTC and ETH together |
| `journal/decision.py` (`DecisionIdentity`, `DecisionJournalEntry`) | immutable decision/audit chain | episode, 4h decision, ladder version, active-order snapshot and fill/replacement provenance fields | one journal row with no order-level causality |

Legacy interfaces and behavior remain unchanged. A later executable implementation must be opt-in under a distinct strategy/protocol identity, add focused compatibility tests, and pass normal Risk/Safety/authorization/accounting paths. No runtime allowlist or registry is expanded by this specification.
