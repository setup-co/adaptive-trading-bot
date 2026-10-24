# WEEX BTWUSDT daily-history availability probe

**Issue:** #208

**Evidence contract:** `issue-208-btw-daily-history-probe-v1`

**Execution base:** `7fde39838fb16f0fac223239c865bc58d9060057`

**Terminal classification:** `TRANSPORT_OR_SCHEMA_BLOCKED`

This was the final bounded coarse BTWUSDT coverage check authorized after
Issue #206. It was not a historical acquisition, strategy evaluation, panel
change, or claim about a listing date. The fixed plan allowed exactly two
public daily-history requests, serially, with zero retries. The first request
received a terminal HTTP error, so the second request was not transmitted.

## Frozen source and execution identity

- Endpoint: unauthenticated `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Plan SHA-256: `f70f4421dcddb09e3617a0725f04dc23eaa106e9de05738795420fe160f95681`
- Launcher SHA-256: `604395783ec5ba277ed0284383a06f4e270630045327604548580aa0a2a15302`
- Wrapper SHA-256: `f8eebbcb139e16c22770ccf683c24650cd1cda0616d84da306b0b5cbba19a705`
- Unchanged parser/helper SHA-256: `23f79e89b1d1ff0194457988ced52abb490ae0c7396f5244b9c73c5f1cdb3c25`
- Evidence manifest SHA-256: `c6516b75869606c100e320b975f8723e0c9eef7ea23b9973b469d3f6c9d693d7`
- Foreground process exit: `2`; retained run exit: `2`
- Attempted requests: `1`; retries: `0`; unattempted requests: `1`

Before traffic, the new external launcher was formatted and linted under the
normal repository configuration. Offline checks rejected changed symbol,
date, host, query key and interval identities; accepted one valid zero-volume
daily row; retained an empty array as valid and empty; and rejected malformed
and out-of-range data. The launcher, wrapper, exact plan, execution checkout,
interpreter and output root were then hash-bound before the one execution.

## Actual result

| ID | Requested UTC interval `[start,end)` | Expected opens | Actual request/result | Valid returned opens | Coverage conclusion |
| --- | --- | ---: | --- | ---: | --- |
| `BTW_DAILY_A` | `2026-04-01T00:00:00Z`–`2026-07-10T00:00:00Z` | 100 | Sent once; HTTP `400`; raw body records venue code `-1170` and message `Time range cannot exceed 90 days.` | unavailable | `TRANSPORT_OR_SCHEMA_BLOCKED` |
| `BTW_DAILY_B` | `2026-07-10T00:00:00Z`–`2026-10-14T00:00:00Z` | 96 | Not attempted because the first terminal response activated the stop rule | unavailable | unavailable |

The first raw response is 56 bytes with SHA-256
`9ce6f8212791329a5f6bdbfee9b4126b1c5b259c450eea8e0bc62bc505816cc7`.
No valid daily candle array was received. Therefore exact daily coverage,
first/last returned day, available spans, and missing historical intervals are
all **unavailable** from this probe. The retained machine summary inventories
the 196 planned grid positions as unobserved, but under its terminal transport
classification that inventory must not be interpreted as 196 proven missing
market days.

## Reconciliation with retained observations

- Issue #206 remains unchanged: its two BTWUSDT old `5m` samples returned the
  exact valid empty body `[]`, each with SHA-256
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.
  Its recent control was complete. Those observations remain interval-specific
  and do not establish a listing date or universal retention boundary.
- The accepted Issue #181 C2 projection remains unchanged: BTWUSDT has 25,820
  of 25,920 expected `5m` rows over `2026-07-16T00:00:00Z`–
  `2026-10-14T00:00:00Z`, with the preserved 100-row gap
  `2026-07-25T00:00:00Z`–`2026-07-25T08:20:00Z`. Its manifest SHA-256 is
  `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`.
- The new HTTP 400 neither contradicts nor confirms either retained data
  generation. Daily and `5m` availability may differ, and no daily candle can
  substitute for canonical `5m LAST` strategy input.

## Limits and handoff

The result does not prove daily absence, complete daily presence, complete
`5m` coverage, a listing date, a retention boundary, contract semantics, or
strategy viability. It does not authorize adjacent-date probing, altered
request windows, full-history acquisition, prospective collection, panel
replacement, or a historical study. The next route requires an explicit
architect data-design decision based on the retained facts and this bounded
transport result.

The immutable evidence is stored under
`data/research_evidence/issue-208-btw-daily-probe-f70f442/`.
