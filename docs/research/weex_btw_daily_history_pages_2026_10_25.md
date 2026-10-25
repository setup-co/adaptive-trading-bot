# WEEX BTWUSDT corrected daily-history census

**Issue:** #210  
**Evidence contract:** `issue-210-btw-daily-history-probe-v1`  
**Helper checkout:** `4d88726994aa0d65b9470907d35b1b8161c4ef36`  
**Integrated publication base:** `0856a4faa972fe1bcfdfcdd5bf21bd91842def86`  
**Terminal classification:** `DAILY_HISTORY_MISSING_OR_PARTIAL`

This was the bounded correction to Issue #208's rejected request shape. It
kept the same `BTWUSDT` symbol and `[2026-04-01, 2026-10-14)` outer calendar,
partitioned into the authorized 90/90/16-day requests. It was not a full-history
download, strategy evaluation, panel change, or listing-date investigation.
Issue #208 remains immutable failure evidence: its first 100-day request was
rejected with HTTP 400 / `-1170`, and its second request was not transmitted.

## Frozen source and execution identity

- Route: unauthenticated `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Fixed query: `BTWUSDT`, `1d`, `LAST`, `limit=100`, literal start/end values
- Plan SHA-256: `52564a208acbea68c71a162ed048fd058ee225ae07036fbe82ef6f2805746d95`
- Launcher SHA-256: `73d2a92879239665a9512bbab82b0597f374212fa45578e70541fc9f7a116d40`
- Wrapper SHA-256: `06e3c4ce45173d22cf730dde675b3975bee8d3567fc7e9785db6fa68495db3a8`
- Unchanged parser/helper SHA-256: `23f79e89b1d1ff0194457988ced52abb490ae0c7396f5244b9c73c5f1cdb3c25`
- Summary SHA-256: `ef5b009301c47d1fc3d7421ab97122c452c72182479946fbd16b8a6f024a42bd`
- Evidence manifest SHA-256: `8359e3aa106d5a75875e7422f0d607d362f3439ed18338bc81399ff88fed9b1f`
- Foreground and retained run exit: `0`; attempts: `3`; retries: `0`
- Capture interval: `2026-10-25T17:51:07.9385436Z` through
  `2026-10-25T17:51:16.4450829Z`

Before traffic, the external launcher was formatted and linted using the
repository configuration. The clean reviewed helper checkout, interpreter,
dependency health, helper origin, literal inventory, contiguous 196-day total,
and maximum 90-day span were checked. Synthetic checks accepted valid empty
and zero-volume responses and rejected changed identities, a 91-day span,
malformed input, and an out-of-range row. All checks recorded zero network
attempts.

## Actual page results

| Page | Requested UTC interval `[start,end)` | Expected | Returned | Result | Raw SHA-256 |
| --- | --- | ---: | ---: | --- | --- |
| `BTW_DAILY_90_A` | `2026-04-01`–`2026-06-30` | 90 | 26 | `PARTIAL`; rows cover `2026-06-04`–`2026-06-29` | `a4d0e4fa787a2e242a68b0d5c53648bcb6b173a0dd3cceba9d7d2ca12d2430d6` |
| `BTW_DAILY_90_B` | `2026-06-30`–`2026-09-28` | 90 | 90 | `COMPLETE` | `0d00505f5726779934e2e6f16d1bbe31515c92059230252b29e3cea76158d896` |
| `BTW_DAILY_16_C` | `2026-09-28`–`2026-10-14` | 16 | 16 | `COMPLETE` | `93221bdad5107e83b6ece8533e1068b97be83ec255c6d18d41403affa5e622a4` |

All three requests returned HTTP 200 arrays that passed the unchanged parser.
The census received 132 of 196 expected daily opens. Because every page was
successfully observed, there are **zero unobserved days** due to request error
or an unattempted page. The 64 daily opens from `2026-04-01T00:00:00Z` through
`2026-06-04T00:00:00Z` were **not returned by the successful first request**.
The first returned daily open is `2026-06-04T00:00:00Z`; the last is
`2026-10-13T00:00:00Z`. Neither boundary is asserted to be a listing date or
retention limit.

## Reconciliation with retained evidence

- Issue #206's valid empty BTW `5m` samples on April 1 and June 1 fall inside
  the new successful daily request's 64-day not-returned interval. The recent
  #206 control remains complete. These interval-specific facts agree without
  proving why older rows were not returned.
- Issue #181 C2 remains unchanged: BTW has 25,820 of 25,920 expected `5m` rows
  from July 16 through October 14, with the retained July 25 100-open gap. The
  new daily series contains July 25, but a daily candle cannot fill or disprove
  that intraday `5m` gap.
- Issue #208's HTTP-400 bytes and classification remain unchanged. This new
  result corrects only the request partition; it does not rewrite the failed
  attempt or turn it into coverage evidence.

## Limits and handoff

Daily presence is not proof of complete canonical `5m LAST` history, and the
64 absent daily opens do not establish a listing date, delisting event, or
venue retention policy. The nine other panel members remain sample-qualified
only. This diagnostic computes no candidate counts, ranking, returns,
indicators, sizing, or PnL. The retained result answers the bounded census and
requires an explicit later data-design decision before any acquisition or
research activation.

Immutable evidence is stored under
`data/research_evidence/issue-210-btw-daily-pages-52564a2/`.
