# Qualification of the first hourly no-time-exit invocation

Status: **completed shorter-follow-up, off-protocol diagnostic / NO_PROMOTION**.

This append-only note qualifies the immutable capture in
`data/research_evidence/pr-236-bollinger-method2-no-time-exit-v1/`. No captured file was edited,
regenerated or relabeled.

## Identity discrepancy

- Frozen executable: `a392c72ac12981734717027fdf5bdb7cf393178a`.
- Run UUID: `2de5c8b3-afc5-4654-bad9-d5856df3f7f5`.
- Actual invoked endpoint: `2026-10-07T00:40:00Z`.
- Actual saved protocol hash: `e525a2cb503abb2fc90f7f7dead79a1769e3ec759ae71b6551e6a37a613cc127`.
- Approved endpoint: `2026-10-14T00:00:00Z`.
- Approved canonical protocol hash: `a2e9d441a9da1b135154d82add231464e44e36926a0dc610e8be7ce3e46ecc9e`.

The wrapper and software both exited 0 after 57.7439625 seconds. That proves successful completion
of the invoked October-7 program, not compliance with the approved October-14 experiment. The 182
open scenario rows and their last-close valuations are censored at the actual earlier endpoint.
The missing follow-up must not be presumed favorable or unfavorable.

## Saved diagnostic result

The capture contains 5,152 paired scenario rows: 4,970 naturally closed and 182 open at the actual
boundary. MFI terminal-valued mean R was `-0.129982` BTC BASE, `-0.256344` BTC STRESS,
`+0.051890` ETH BASE and `-0.037271` ETH STRESS. ETH BASE's `+407.3397469` net quote consists of
`+256.3913541` from 301 natural closes and `+150.9483928` of diagnostic contribution from 13 open
positions. Its entry-time block net outcomes were approximately `+2218.78`, `-1522.37` and
`-289.07`; the aggregate is not stable across blocks.

The captured holding summaries mixed natural-close durations and open-position ages. They remain
immutable historical output, but are not closed-only medians. Corrected prospective output uses
separate `closed_holding` and `censored_open_age` summaries, and computes open-position concurrency
using the row's recorded terminal valuation time.

Funding remains `NOT_MODELED`. This diagnostic neither validates nor rejects the intended
October-14 hypothesis and grants no strategy promotion, runtime activation or trading authority.
