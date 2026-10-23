# Breakout + Retest zone comparison retained result

Status: `DESCRIPTIVE_COMPLETE_PROMOTION_INELIGIBLE` — **post-result
descriptive comparison; promotion ineligible**

This document makes the already-retained Issue #203 result readable. It does
not rerun either strategy, admission, execution, accounting, verification or
prefix comparison. The baseline is `BreakoutRetestStrategy` `1.0.0`; the
opt-in arm is `BREAKOUT_RETEST_ZONE_INVALIDATION_V1` `0.1.0`.

## Source map and qualifications

The authoritative panel projection is
`data/research_evidence/issue-203-breakout-zone-recovery-b332eb83/recovery-attempt/ledger/projection.json`.
The values below copy its `observation_count`, candidate counts, `cells` and
`paired` fields. Admission/mask membership and mechanism details come from
`00.report.json` through `09.report.json` in that same ledger, specifically
`candidate_counts`, `admission_sets`, `pre_outcome_masks`, `cells`,
`paired_closed` and `admitted_trade_details`.

The retained projection file remains 996,738 bytes with SHA-256
`6bede98fcb29c4093469dcc225e44d81db5301afb78448f681b48bf69a6e2e88`;
the FINALIZED `0021.json` file remains
`57f0e3924f28aae0eb0b9981ada5993a151ea8a309703bad6ea45a6f906f9150`.
The simulation process exit remains `UNKNOWN_NOT_RECORDED`. The later
read-only verifier passed, but does not establish the missing process exit.
The conflicting earlier raw-hash observations remain `UNRESOLVED`.

The ten tasks share 60,480 (`60480`) decision observations. BASE and STRESS reuse the
same opportunities and are not independent samples. This is neither protected
out-of-sample validation nor funded-portfolio, leveraged-contract or trading
validation.

## Population, admission and masks

| Measure | Baseline 1.0.0 | Zone 0.1.0 |
| --- | ---: | ---: |
| Candidate opportunities | 29 | 29 |
| Risk/Safety-authorized admissions | 28 | 26 |
| Actual fills | 28 | 26 |
| Independently selected pre-outcome mask | 25 | 23 |

The admission sets contain 25 common opportunities, three baseline-only and
one zone-only opportunity. Their union is the same 29-candidate opportunity
set. The independent pre-outcome masks intersect on 22 opportunities. Every
admitted order filled in both scenarios: `ENTRY_UNFILLED` is zero throughout.
Consequently, equal candidate counts do **not** imply equal admissions or equal
economic populations.

## Absolute outcomes

`closed n` is the denominator for the displayed mean and median net
approved-risk R. Terminal counts are ordered as
`NO_SIGNAL / RISK_BLOCKED / SAFETY_BLOCKED / ENTRY_UNFILLED /
ORDINARY_CLOSED / ORDINARY_OPEN / RECOVERY_CLOSED / RECOVERY_OPEN`.
The full population includes all 60,480 observations; each masked row includes
only that arm's independent pre-outcome mask and must not be substituted for
the other arm or for the paired intersection.

| Arm | Scenario | Population | closed n | Mean closed net R | Median closed net R | Incurred fees (quote) | Terminal counts in declared order |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Baseline | BASE | Full | 28 | `0.09212481964940152547118117117019896` | `-0.3981167034164859002169197396963124` | `337.5232363150151025718754633238990` | `60451 / 1 / 0 / 0 / 28 / 0 / 0 / 0` |
| Baseline | BASE | Masked | 25 | `0.1871554746409081399002719313184659` | `-0.1136471068329718004338394793926247` | `288.9453287844858426056587015409450` | `0 / 0 / 0 / 0 / 25 / 0 / 0 / 0` |
| Zone | BASE | Full | 26 | `0.1642288590154462464730985349186214` | `0.6765376248786765055153992421733355` | `274.7446461057322064224816947170380` | `60451 / 3 / 0 / 0 / 26 / 0 / 0 / 0` |
| Zone | BASE | Masked | 23 | `0.048105276972677637308429465926227` | `0.3504485833363995039228027576316568` | `246.4082295696625369726562859372796` | `0 / 0 / 0 / 0 / 23 / 0 / 0 / 0` |
| Baseline | STRESS | Full | 28 | `-1.545162862665948740435663309074635` | `-1.726152359816420193778684344722080` | `675.8763928961981458102460088618546` | `60451 / 1 / 0 / 0 / 26 / 0 / 2 / 0` |
| Baseline | STRESS | Masked | 25 | `-1.411159531974324127749481367702052` | `-1.589628183783783783783783783783784` | `578.7622974280128483013561079830384` | `0 / 0 / 0 / 0 / 23 / 0 / 2 / 0` |
| Zone | STRESS | Full | 26 | `-1.342661791923018378464113335645350` | `-1.476415209731697368112712210768354` | `549.8423559946124397912419671373638` | `60451 / 3 / 0 / 0 / 24 / 0 / 2 / 0` |
| Zone | STRESS | Masked | 23 | `-1.400009191946919036149079291027623` | `-1.567227503055288006972883056819618` | `493.3743150882732247560690402716646` | `0 / 0 / 0 / 0 / 21 / 0 / 2 / 0` |

Every residual quantity that is present in the saved cell summaries is exactly
`0`; no cell records nonzero unresolved exposure. An absent asset key means no
residual was reported for that asset in that cell, not an inferred zero. There
are no ordinary-open or recovery-open outcomes. STRESS records two
`RECOVERY_CLOSED` outcomes in each arm; BASE records none.

The absolute means cannot be subtracted as a paired effect: baseline has 28
admissions, zone has 26, and the independently masked denominators are 25 and
23. The zone arm also records two additional full-population risk blocks.

## Stored paired comparisons

These are copied from `projection.json` → `paired`; they are not reconstructed
by subtracting arm means.

| Scenario | Paired population | Common closed n | Unavailable or unclosed n | Stored mean zone-minus-baseline net R | Direction within this view |
| --- | --- | ---: | ---: | ---: | --- |
| BASE | Full common opportunities | 25 | 4 | `-0.07698444373299471879102983830128148` | Unfavorable to zone |
| BASE | Pre-outcome mask intersection | 22 | 0 | `-0.08115951821695183609519711823386173` | Unfavorable to zone |
| STRESS | Full common opportunities | 25 | 4 | `0.3799443214387610218652607808420803` | Favorable to zone |
| STRESS | Pre-outcome mask intersection | 22 | 0 | `0.397983911469470300774262229885549` | Favorable to zone |

The recorded paired result is therefore **mixed**, not uniformly improved:
the zone arm is worse under BASE and better under STRESS in both the full
common and intersection-mask views. Lower absolute fees or a less-negative
unpaired mean is not, by itself, success.

## Recorded mechanism consequences

The following counts compare the 25 common admitted opportunities using the
saved `admitted_trade_details`. Numeric higher/lower is literal; for stop and
target prices it is not a favorable/unfavorable label because side differs.

| Saved field | BASE: zone lower / equal / higher / unavailable | STRESS: zone lower / equal / higher / unavailable |
| --- | ---: | ---: |
| `quantity` | `20 / 5 / 0 / 0` | `20 / 5 / 0 / 0` |
| `approved_risk_quote` | `0 / 17 / 8 / 0` | `0 / 17 / 8 / 0` |
| `initial_stop` | `14 / 0 / 11 / 0` | `14 / 0 / 11 / 0` |
| `target_trigger_price` | `11 / 0 / 14 / 0` | `9 / 0 / 14 / 2` |
| `fees_quote` | `20 / 3 / 2 / 0` | `20 / 3 / 2 / 0` |
| `holding_seconds` | `0 / 20 / 5 / 0` | `0 / 18 / 7 / 0` |

Across heterogeneous assets, the saved quantities range from
`0.1177646849618030244326391890252735` to
`117288.2946281961060286183438892798` in both arms; they are base-asset
quantities and must not be summed. Baseline approved risk ranges from
`4.486834497044695230883553101862920` to
`25.00000000000000000000000000000000`; zone approved risk ranges from
`4.527550893255224646511769535371213` to the same maximum. These are copied
per-trade fields, not a recomputed risk model.

Recorded holding time ranges from 900 to 60,300 (`60300`) seconds in every
arm/scenario.
Within common admissions the zone holding time is never shorter: it is longer
for five BASE pairs and seven STRESS pairs, and equal otherwise. Status is
unchanged for all common pairs. Recovery reasons are also unchanged; under
STRESS, zone has 24 ordinary closes out of 26 admissions and baseline has 26
out of 28, with two recovery closes in each arm. All residual quantities
remain zero. The two unavailable STRESS targets correspond to retained details
where a target value is absent; they are not imputed.

## Interpretation and boundary

The accepted zone invalidation changes stop geometry, sizing and admissions.
It reduces quantity in 20 of 25 common admissions and usually reduces recorded
fees, but it also rejects two more candidates and lengthens several holdings.
The paired economic direction flips between BASE and STRESS. The saved result
therefore supports only a **mixed descriptive finding**; it does not establish
that the zone variant improved the baseline.

The same-window ceiling remains 29 candidate opportunities, below the frozen
60/30 adequacy references, so promotion is unconditionally ineligible. Prior
Trend `NEGATIVE_EVIDENCE` and Breakout/Range `INSUFFICIENT_EVIDENCE` remain
unchanged. No strategy, threshold, execution assumption or activation boundary
is changed by this report.
