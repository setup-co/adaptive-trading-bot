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
## Retained result

The one authorized batch completed with 57/57 new attempts returning valid
HTTP-200 arrays. Together with the three reused Issue #210 BTWUSDT pages, the
offline verifier reconciled all 60 page authorities and returned
`SELECTED_FOR_FINE_GRID_DATA_QUALIFICATION`. The result artifact SHA-256 is
`3dc81c33d3d12983973c69474e376561524b4dce1ef75723f1117851f88f1c81`;
the enclosing evidence-manifest SHA-256 is
`9259da6973e5c910912c6da1b69942a3f7a0edbffd583e5b4cd9ea0395301bbb`.

| Rank | Symbol | Received / expected daily opens | Missing | Unobserved | Result |
| ---: | --- | ---: | ---: | ---: | --- |
| 1 | BTCUSDT | 196 / 196 | 0 | 0 | selected |
| 2 | ETHUSDT | 196 / 196 | 0 | 0 | selected |
| 3 | SOLUSDT | 196 / 196 | 0 | 0 | selected |
| 4 | XRPUSDT | 196 / 196 | 0 | 0 | selected |
| 5 | ZECUSDT | 196 / 196 | 0 | 0 | selected |
| 6 | ZAMAUSDT | 196 / 196 | 0 | 0 | selected |
| 7 | BCHUSDT | 196 / 196 | 0 | 0 | selected |
| 8 | BTWUSDT | 132 / 196 | 64 | 0 | coverage-ineligible |
| 9 | AVAXUSDT | 196 / 196 | 0 | 0 | selected |
| 10 | DOGEUSDT | 196 / 196 | 0 | 0 | selected |
| 11 | LINKUSDT | 196 / 196 | 0 | 0 | selected |
| 12 | LTCUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 13 | BNBUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 14 | UNIUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 15 | SUIUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 16 | ARBUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 17 | DOTUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 18 | XLMUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 19 | TRXUSDT | 196 / 196 | 0 | 0 | eligible, not selected |
| 20 | HYPEUSDT | 196 / 196 | 0 | 0 | eligible, not selected |

The fixed shortlist is therefore `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`,
`ZECUSDT`, `ZAMAUSDT`, `BCHUSDT`, `AVAXUSDT`, `DOGEUSDT`, and `LINKUSDT`.
BTC/ETH passed the same criterion as every other member; BTW was not replaced
manually, and ranks after LINK entered only through the frozen rank order.

### Overlap-label erratum and offline reconciliation

For each candidate, the 30 overlapping daily opens exactly matched the saved
Issue #175 numeric candle values (600 expected, 600 compared, 600 matching,
zero different, zero unavailable). However, the captured launcher's label is
wrong: its `EXACT_NUMERIC_MATCH` branch requires
`len(overlap_points) == 16`, even though `old_daily_rows` requires the full
30-day `[2026-09-14,2026-10-14)` authority. The stored
`REVISION_OR_COVERAGE_DIFFERENCE` labels are therefore artifacts of that
erroneous 16-row condition, not intentional coverage-extent qualifications.
They remain byte-preserved but are non-authoritative for downstream identity
decisions. A separate corrected annotation reconstructs the declared 30
expected timestamps and classifies all twenty as `COMPLETE_EXACT_MATCH`.
The corrected comparator evaluates the validated Decimal and integer fields
directly: numerically equivalent wire representations such as `1`, `1.0`,
`1.00`, exponent forms, and differently formatted zero volumes compare equal.
Original `Row.record()` strings are retained only when reporting a genuine
field difference; no float conversion, rounding, tolerance, or normalization
context is used.

The supplement also directly compared the original panel's retained
`*-earlier-daily/0.raw` archive entries against the saved new/reused daily rows
over `[2026-07-16,2026-09-14)`. It did not infer equality from the C2 report or
reconstruct daily candles from 5m data.

| Symbol | Earlier source | Archive SHA-256 | Raw-entry SHA-256 | Compared / matching / different / unavailable |
| --- | --- | --- | --- | ---: |
| BTCUSDT | Issue #179 C1 | `c1e1b95d7534944614affeea21bffcc6706a4fb39d7ef058c5f2ab86812d9aad` | `1bba9f175f2b2720c228595baaaaa970de9c72c6f0bedd98ae427b42fb920819` | 60 / 60 / 0 / 0 |
| ETHUSDT | Issue #179 C1 | `31aaaec2492a22f85af9ef0ebf3e581b063cb16e6dae4eda0280a0017bef34de` | `72fd15e21c76ad8af28b9cd82ebb81d87c4561ecbddc9604d38e8753dd43b355` | 60 / 60 / 0 / 0 |
| SOLUSDT | Issue #179 C1 | `76cd488e036c0d9257bcdc519dc0a7806967fac2ed05bf69e4256c9c7facbc69` | `c80e843c95e3bea95f3b107ea1577a5a8c1676e5532a27e218006f7265d40534` | 60 / 60 / 0 / 0 |
| XRPUSDT | Issue #179 C1 | `a6ee34ee9fbf54f33c4fa162582588ba67e702e4c4e599fee4e6cb050f0d8843` | `c2cf4c19e2af0469e060ec44cbcca6460a2f03aa95c11332f295b50bbfee697d` | 60 / 60 / 0 / 0 |
| ZECUSDT | Issue #179 C1 | `9f950885f821022260a7d2d662d8410c13503d50a5adfb1bf993eaf875c92ff0` | `16be76e092a9a7bb1aa3e905c88ee983e3345dacf1a7938ba25a694a3bb15efb` | 60 / 60 / 0 / 0 |
| ZAMAUSDT | Issue #179 C1 | `94b3befe9f839d259504b09690e69e2b0e8301e1e8503a7fe94e1a7df3215a53` | `ddd80522ba42d93732e02aaebb627554eb5927cf3260b8e500868802ab0497e0` | 60 / 60 / 0 / 0 |
| BCHUSDT | Issue #179 C1 | `071ec33fae288b91e4340a85b83a03935fa07e4290117b967430931d055c8a73` | `97b806b4f81f655bd51b1b6e21f984daaa61c709af656e2ae4ae77727b9a3c7b` | 60 / 60 / 0 / 0 |
| BTWUSDT | Issue #181 C2 | `c06a802e0d630a9ff0210e0cfb18362210db5a387863b183677fe2740cd1222e` | `7259a37b12cee797d7c1347bfe9721086c6ab958731f2a95b14dc9aa848d845c` | 60 / 60 / 0 / 0 |
| AVAXUSDT | Issue #181 C2 | `4e968c53bd3ab47eeac43de91e918f96ccdd7fdf12bb6b2e3ab20b6e9b948d51` | `375ca204f078c8db5db8fe09e03440e4f884bfc30bfb0db3eeac792907b4150f` | 60 / 60 / 0 / 0 |
| DOGEUSDT | Issue #181 C2 | `f0d54e0bab2a9ef122188e721b3ccf9ede9ba3cbce02d94421ae86c88a231327` | `fe550ab628988dd464a54a7ca9b155d239771cbdd0853ef0e9bdb18c4f800770` | 60 / 60 / 0 / 0 |

The earlier-daily aggregate is 600 expected, 600 compared, 600 matching, zero
different and zero unavailable. Full archive/entry paths, both later raw-page
identities, exact field-level comparison output, the C1/C2 manifest bindings,
and the C2 report binding are retained in
`data/research_evidence/issue-212-overlap-reconciliation-v1/reconciliation.json`
(SHA-256
`82faa7c6d78391ea43491ccf3f38b034aa4974b0864614b70ce00ad887ad6edf`).
The supplement manifest SHA-256 is
`09407c83bcec9b11a8bbb751674f85bdd7aa459d3cc4165a246b4a55f64b8e22`.
These exact daily matches do not qualify 5m continuity, historical contract
units, economics, or strategy results; coverage/ranking/shortlist facts remain
unchanged.

This shortlist is ready only for a separately authorized canonical fine-grid
data-qualification step. That later step must still freeze source identity,
units, complete 5m coverage, gaps/epochs, warm-up and outcome follow-up,
selection-informed use, and acquisition budgets before traffic. It is not a
strategy-study panel, a runtime allowlist, or trading eligibility.

The complete retained evidence is under
`data/research_evidence/issue-212-history-screened-shortlist-adbf3af/`; the
pre-I/O plan bundle remains separately preserved.
