# Bollinger Method II price/MFI entry ablation

Status: fixed research protocol; historical result is added only after the one authorized run.

## Source mapping and adaptation boundary

This screen implements the entry-selection proposition described by John Bollinger as Method II:
price action at the bands, optionally confirmed by an unbounded volume indicator. The project uses
the published 20-period middle band, two population-standard-deviation bands, `%b > 0.8` for a
long price condition and `%b < 0.2` for a short price condition. The confirmed arm additionally
requires raw-money-flow MFI(10) strictly above 80 or below 20. Typical price is `(H+L+C)/3`; raw
money flow is typical price times the canonical base-asset volume. A flow is positive or negative
only when typical price is strictly above or below its predecessor; ties contribute to neither.

Sources consulted:

- John Bollinger, *Bollinger on Bollinger Bands*, Method II and indicator descriptions, as
  represented by the publisher listing and the author's official Method II materials:
  <https://www.bollingerbands.com/bollinger-band-rules> and
  <https://www.bollingerbands.com/method-ii>.
- Official Method II worksheet (2021), which distinguishes the price-only and price-plus-MFI
  logic: <https://www.bollingerbands.com/_files/ugd/9c2d58_0f6617b3f75c4caa9f5937603e2d129a.pdf>.

This is **not** a reproduction of the book's exit system. Both arms instead use project
adaptations: the opposite frozen band is the structural stop; the target is sealed at the 15m
decision close at fixed 2R; and the existing opt-in 5m-LAST protection profile applies a 30-minute
modeled deadline after fill. Shared Risk, Safety, authorization, pricing, costs, accounting,
recovery and journal authorities are unchanged. No runtime registry or activation boundary is
expanded.

## Fixed comparison

- Protocol: `BTC_ETH_BB_METHOD2_MFI_ABLATION_15M_30M_V1`.
- Arms: `bb_price_control:0.1.0` and `bb_mfi10_short_horizon:0.1.0`.
- Assets: BTCUSDT and ETHUSDT only; closed 15m decisions.
- Data/calendar: the accepted 196-day corpus and the same warm-up, 180-day signal calendar and
  5m follow-up tail used by Issue #233; exactly 17,280 signal observations per asset and arm.
- Costs: unchanged BASE and STRESS scenarios. Funding remains not modeled.
- Population: each arm has independent Strategy/Risk/Safety/authorization decisions. The
  confirmed signal set must be a subset of the price-control signal set. Common admitted decisions
  must have identical modeled economics.
- Views: FULL plus one pre-outcome `COMMON_SPACED` mask derived from control admissions with a
  minimum 35-minute separation and intersected with the confirmed admissions.

Selection effect is reported separately from profitability: control admissions retained by MFI,
control admissions excluded by MFI, both arms' full/masked economics, and the stored mean-R
differences. These are descriptive comparisons, not causal claims.

Any unresolved exposure is incomplete evidence. Fewer than 30 confirmed closed common-spaced
observations in any asset/scenario is insufficient. Review-only additionally requires positive
FULL and COMMON_SPACED mean net R, finite profit factor above one, and positive means in all three
fixed chronological blocks for both assets and scenarios. No result authorizes promotion or
trading.
