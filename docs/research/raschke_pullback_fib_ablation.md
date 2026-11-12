# Raschke-informed first-pullback depth ablation

Protocol: `BTC_ETH_RASCHKE_PULLBACK_FIB_ABLATION_15M_V1`

This is a Raschke-informed **closed-bar, retest-target adaptation**, not a faithful recreation of
Linda Bradford Raschke's discretionary results or complete exit management. It is an exploratory
screen on already-inspected BTC/ETH history. All outcomes remain research-only and `NO_PROMOTION`.

## Source-to-rule boundary

| Source | Supported idea | Implemented rule | Project choice | Limitation |
|---|---|---|---|---|
| [AIQ Opening Bell, Aug. 1997, printed pp.2–3](https://lindaraschke.net/wp-content/uploads/2026/01/august1997.pdf) | ADX(14) above 30 and rising, first EMA20 retracement, prior-bar resumption trigger, structural stop | Wilder ADX/DI, one strong-trend episode, first observed EMA20 touch, first later completed close beyond the prior bar, stop at observed pullback extreme | Completed-15m close confirmation followed by next eligible 5m market fill | Does not simulate an intrabar resting stop or discretionary trailing |
| [Active Trader, Mar. 2004, printed pp.78–79](https://lindaraschke.net/wp-content/uploads/2026/03/raschke_pt2_0304.pdf) | First EMA20 retracement after strengthening and a return toward/prior-high test | Favorable extreme `B` is frozen before the touch and is the complete structural retest target | One automated causal episode; no runner, re-entry or replacement timer | The author describes discretion and examples, not this complete state machine |
| [Fidelity Fibonacci retracement guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/fibonacci-retracement) | Swing-relative retracement levels | `FIB_ZONE` retains exact Decimal depth `0.382 <= d <= 0.618` | `ROUND_ZONE` uses nested `0.40 <= d <= 0.60` as a simple rounded control | Levels do not establish an edge; 50% is shown only as a conventional midpoint |

## Frozen implementation

- Completed 15m decisions only; BTCUSDT and ETHUSDT. Existing 1h/4h facts remain readiness/safety
  inputs and add no directional filter.
- Wilder ADX uses mutually exclusive directional movement, ordinary true range, a 14-movement
  smoothed seed, 14 DX observations for the initial ADX, then Wilder updates. Gaps reset warm-up.
- A long episode arms only when ADX is above 30 and rising, `+DI > -DI`, and the whole candle is
  above EMA20; shorts mirror this exactly. Rearming requires ADX at or below 30 or DI reversal.
- Optional origin `A` is the latest opposite causal pivot already confirmed at arming. Favorable
  extreme `B` is frozen immediately before the first EMA touch. Pullback extreme `P` is observed
  from touch through confirmation. Touch candles that also create a new `B`, and gaps through EMA,
  are rejected rather than assigned an unknowable intrabar ordering.
- At first confirmation, all arms consume the episode. Entry reference is the close, stop is `P`,
  and target is frozen `B`. Long depth is `(B-P)/(B-A)`; short depth is `(P-B)/(A-B)`.
- `REFERENCE` accepts all valid geometry. `FIB_ZONE` and `ROUND_ZONE` require a valid ordered anchor
  and apply only their fixed depth predicates. Common admitted keys must retain identical risk,
  entry/fill, stop, target, quantity and per-scenario economics.
- Shared Strategy → Risk → Safety → Journal → Authorizer, BASE/STRESS costs, first-eligible 5m open,
  stop-first dual-touch handling, accounting and recovery are unchanged. There is no holding timer.
  Natural closes and endpoint-censored valuations are reported separately through
  `2026-10-14T00:00:00Z`.

The result report includes the unrestricted paired comparison, valid-anchor reference subset,
rounded common core, Fibonacci outer strips, three fixed chronological blocks, holding/censoring,
largest-winner concentration, overlap and a descriptive one-position-per-asset/arm/scenario
capacity diagnostic. That diagnostic is not a funded portfolio, compounding curve or account ROI.
