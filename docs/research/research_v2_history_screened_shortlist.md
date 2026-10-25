# Research V2 history-screened snapshot shortlist

**Protocol:** `HISTORY_SCREENED_SNAPSHOT_SHORTLIST_V1`  
**Issue:** #212  
**Status at freeze:** selection-only plan; no request had been transmitted  
**Execution/helper base:** `a82f1461991e52c26d25ee37b0de46beb7605d21`  
**Execution/helper tree:** `5dcf108d62e2b253b8b1e1fac4d3031ff94ace45`

This protocol ends the attempt to force the original ten-member panel into the
proposed calendar. It defines a new, current-snapshot-selected exploratory
shortlist for later fine-grid data qualification. It does not replace the old
panel, authorize a strategy study, widen a runtime/research execution registry,
or establish Paper/Shadow/Live eligibility.

## Frozen source and selection rule

The sole ranking authority is the accepted complete Issue #175 combined
selection at
`data/research_evidence/issue-175-weex-kas-completion-v1/combined_selection.json`:

- combined selection SHA-256:
  `80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4`;
- supplement manifest SHA-256:
  `de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79`;
- ranking cutoff: `2026-10-14T00:00:00Z`;
- ranked population: 176 metadata-eligible, `COMPLETE_30D` direct exposures;
- offline `kas-verify`: `SELECTED_FOR_DATA_QUALIFICATION`, zero missing queries.

The pool is the unique saved ranks 1 through 20, in saved order. The old
`selected` flags are not an input. Exact saved median turnover and raw
references remain in the frozen plan. No refreshed catalog, turnover, return,
signal, volatility, PnL, or strategy result enters this selection.

| Rank | Symbol | Saved median daily USDT quote turnover | Raw source/file |
| ---: | --- | ---: | --- |
| 1 | BTCUSDT | 2573464911.10187 | `ORIGINAL_173/005-recent-BTCUSDT-1d-1.raw.json` |
| 2 | ETHUSDT | 2343330069.468575 | `ORIGINAL_173/007-recent-ETHUSDT-1d-1.raw.json` |
| 3 | SOLUSDT | 429577948.0365 | `NEW_175/130.raw.json` |
| 4 | XRPUSDT | 337111425.6465 | `NEW_175/160.raw.json` |
| 5 | ZECUSDT | 203144795.8565 | `NEW_175/167.raw.json` |
| 6 | ZAMAUSDT | 144068926.41650 | `NEW_175/165.raw.json` |
| 7 | BCHUSDT | 136943206.62135 | `NEW_175/018.raw.json` |
| 8 | BTWUSDT | 114937846.5400 | `NEW_175/028.raw.json` |
| 9 | AVAXUSDT | 86964181.388 | `NEW_175/012.raw.json` |
| 10 | DOGEUSDT | 79123330.65350 | `NEW_175/041.raw.json` |
| 11 | LINKUSDT | 75178577.445 | `NEW_175/086.raw.json` |
| 12 | LTCUSDT | 65223623.8945 | `NEW_175/089.raw.json` |
| 13 | BNBUSDT | 61241267.36385 | `NEW_175/024.raw.json` |
| 14 | UNIUSDT | 60580610.037 | `NEW_175/149.raw.json` |
| 15 | SUIUSDT | 60546915.4220 | `NEW_175/135.raw.json` |
| 16 | ARBUSDT | 47866169.614955 | `NEW_175/007.raw.json` |
| 17 | DOTUSDT | 43377693.14165 | `NEW_175/043.raw.json` |
| 18 | XLMUSDT | 38350903.86850 | `NEW_175/156.raw.json` |
| 19 | TRXUSDT | 37238330.26800 | `NEW_175/145.raw.json` |
| 20 | HYPEUSDT | 31589292.53655 | `NEW_175/068.raw.json` |

All twenty must be evaluated against the same 196-day daily-open grid
`[2026-04-01T00:00:00Z, 2026-10-14T00:00:00Z)`. Passing requires all 196
unique aligned opens. A valid zero-volume candle is present. Failed or
unattempted pages are unavailable, not zero or failed coverage.

After a complete census, BTCUSDT and ETHUSDT are included only if both pass;
the remaining eight are the highest-ranked other passing members. A failed
anchor, fewer than ten passes, or any unavailable census produces a blocker
and no partial shortlist. Ranks 21 and later are
`NOT_EVALUATED_BY_THIS_PROTOCOL`.

## Frozen acquisition boundary

BTWUSDT reuses the three accepted Issue #210 pages without network traffic.
Their manifest SHA-256 is
`8359e3aa106d5a75875e7422f0d607d362f3439ed18338bc81399ff88fed9b1f`;
the rederived result is 132/196, 64 not returned, zero unobserved.

The other nineteen symbols have three literal public `1d LAST` requests each:
90 days, 90 days, and 16 days over the same calendar. The plan contains 57
new requests, 57 maximum attempts, zero retries, and 3,724 expected new daily
positions. Together with BTW reuse, the census has 60 page authorities and
3,920 candidate positions. Requests are serial with at least two seconds
between attempts. The fixed inventory completes even after ten passes unless
a terminal transport/schema/integrity error stops it.

- plan SHA-256:
  `adbf3af07c25e32931656c896a5947dce96f15c84e4d4af7a93143a86c7cb100`;
- launcher SHA-256:
  `334703c30748f67c095e4a83688ca3a102f35b758b43c575e8d7022f29e88bc5`;
- wrapper SHA-256:
  `5a491b0cbedf1ee38e48d430da7b5364cbc27025e440c7d98bc6eb80cfb13d3c`;
- unchanged parser/helper SHA-256:
  `23f79e89b1d1ff0194457988ced52abb490ae0c7396f5244b9c73c5f1cdb3c25`.

Pre-traffic checks validated the clean pinned checkout, accepted #175 ranking,
all source raw hashes, Issue #210 manifest/raw rederivation, exact 57-query
inventory, per-symbol contiguous 196-day coverage, maximum 90-day spans,
zero-retry transport boundary, malformed/off-grid/duplicate rejection,
zero-volume presence, and blocker semantics. At this freeze, network attempts
were zero.

## Interpretation boundary

This generation is deliberately current-snapshot-selected and
selection-informed after earlier data/results were observed. Daily presence
does not qualify canonical 5m coverage, units, epochs, historical contract
facts, economics, or a strategy. Overlapping saved/new daily rows are compared
under exact numeric semantics and retain any revision difference separately.
No daily observation overwrites predecessor bytes. Any future confirmatory
evaluation still requires separately frozen protected future data.

The pre-I/O plan bundle is retained under
`data/research_evidence/issue-212-history-screened-shortlist-plan-adbf3af/`.
The result section is intentionally deferred until the one authorized fixed
batch completes.
