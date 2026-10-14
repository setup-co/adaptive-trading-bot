# Research V2 all-fill economics — implementation contract

**Policy:** `RESEARCH_ALL_FILL_ECONOMICS_V1`

**Result/report schema:** `research-all-fill-economics-v1`

**Status:** synthetic-tested offline research implementation; no historical study or trading activation.

The new `ResearchAllFillReplayRunner` consumes a sealed `DecisionJournalEntry`,
sealed post-entry context observations, an exact 15m execution series, a bounded
UTC range, and one of the two predeclared cost scenarios. It reuses the existing
entry authorizer, the three family-specific research runners, the shared market
simulator and `PositionAccountingEngine`. The original candle source is loaded
once per admitted opportunity and detached; ordinary and recovery executions
use that same snapshot. Each sealed result retains that bounded execution
snapshot and its deterministic digest, then replays entry/recovery outcomes
through the shared simulator
during validation. The declared series, costs, bounds, decision, entry request,
and final accounting time must exactly match the underlying family replay.
Recovery also verifies the first eligible post-detection open, or its absence,
from the retained snapshot; a caller-provided reference price is insufficient.
A no-signal, Risk-blocked or Safety-blocked decision
does not load candles or place a simulated request.

An unfilled entry has no fee or position. An actual fill is recorded even when
its stop or the Range entry midpoint invalidates the entry geometry. Only after
entry accounting does the research adapter derive one opposite-side full-close
request for the exact remaining quantity. The request carries the original
decision/entry provenance, stop, frozen midpoint when applicable, and ordered
invalidation reasons. Recovery can fill only at a later candle open strictly
after detection and at or after configured latency. A bounded unfilled recovery
leaves the position and entry fee visible. No price, fee, PnL or position
accounting formula is duplicated by this policy.

Trend Pullback's HOLD_ONLY and EMA50 branches are separate hypothetical
accounting ledgers over one shared entry fill. Only the EMA50 branch enters
primary population economics; HOLD_ONLY remains a paired diagnostic. A pair
counts as one decision/opportunity. Valid-entry ordinary branches are
cross-checked exactly against the existing replay results. The new report
requires an explicit complete tuple of decisions and matching results, rejects
duplicates/missing/orphan rows, and declares **one exact scenario**. BASE and
COMBINED_STRESS require separate reports over the same supplied decision
population; their fees or R must never be pooled. It includes zero cells only
when explicitly declared. Each instrument/family/month/side/cohort/regime cell
has its own closed-only mean/median R and R-space profit factor; the whole-report
numbers are explicitly across-family descriptive totals, not family-specific
estimates. Closed R uses authoritative net accounting divided by the original
approved risk. Mean/median R and R-space profit factor use **closed rows only**;
open positions are separate, with incurred fees and signed quantity but no
fabricated closed R. These independent hypothetical opportunities are not a
portfolio equity curve or a claim about funding, margin or liquidation.

Only base `2/2/10` bps with zero latency and the combined stress `4/4/20` bps
with one second latency are accepted. These are modeled costs, not certified
historical venue charges. Old replay/report schemas and all frozen evidence
remain unchanged; the new policy does not authorize a WEEX study. Package A's
selected-instrument 5m history, identity and research-only admission must be
qualified before a separately predeclared exploratory activation annex.
