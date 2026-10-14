# Research V2 current-snapshot exploratory panel

**Protocol:** `CURRENT_SNAPSHOT_EXPLORATORY_PANEL_V1` (Issue #175; Master Plan v1.8)

**Status:** selection-only. A ten-member list, if produced, means
`SELECTED_FOR_DATA_QUALIFICATION / EXPLORATORY`, not accepted 5m history,
research execution, historical-as-of membership, Paper/Shadow/Live eligibility,
or a confirmatory finding.

The source population is the separately captured WEEX Futures V3
`exchangeInfo` and `apiTradingSymbols` responses from Issue #173 on 2026-10-14.
Their individual capture timestamps remain separate. The original and
supplementary #173 evidence directories are immutable, with manifest SHA-256
`88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac`
and `34c6e5153f7654eb6e66b004a0d57f9ce5df628229043209c48b809d5a906a87`.
The historical-as-of population remains unqualified. The ten earlier probe
names are not a selected panel. Their inspected quote values mean this
selection rule does not predate all market-data inspection; it does precede
the broader acquisition and every new strategy outcome.

The fixed ranking cutoff is `R=2026-10-14T00:00:00Z`, with thirty complete
UTC daily LAST candles in `[2026-09-14T00:00:00Z, R)`. The real selection
freeze timestamp is recorded separately and must never be called historical
selection time `T`. The current official [WEEX Futures V3 market schema](https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api)
documents `exchangeInfo` base/quote/margin and PERPETUAL/COIN fields,
`apiTradingSymbols`, inclusive millisecond history bounds with 1–100 rows,
and quote-asset volume at candle index 7 (base volume at 5). The three
older endpoint-specific documentation links in Issue #175 currently resolve
to the general WEEX API landing, not separate field definitions. No current
catalog field proves historical contract specifications or population.

Selection-input eligibility requires a unique crypto COIN/PERPETUAL contract,
saved API support, matching direct `baseAsset+USDT` symbol, `quoteAsset=USDT`,
`marginAsset=USDT`, no stablecoin/leveraged/non-crypto underlying, and no
unresolved scaled or duplicate economic exposure. The saved outer set is
exactly 181 initially label-screened records; those are not prequalified.
The five `1000*` representations are quarantined without official exposure/
scale mapping. Direct quote-asset volume at index 7 is compared as USDT;
neither `close × base volume` nor current `contractVal` is used to infer
turnover or historical quantity. Missing data is unavailable, not zero.
Incomplete valid grids are predeclared history exclusions; transport/denial/
malformed rows make the ranking incomplete rather than quietly dropping an
otherwise eligible symbol.

Among complete, uniquely qualified exposures, rank descending by exact
Decimal median of the thirty directly reported daily USDT quote volumes,
tie-breaking by canonical symbol. BTC and ETH must both qualify and appear
once; eight other highest-ranked exposures complete exactly ten. Preserve
every considered/excluded record, all thirty raw quote inputs, source hashes,
rank and reason. No return, volatility, signal count, profitability, or prior
winner enters selection. No post-result substitution is permitted.

The new one-shot public GET request plan freezes the metadata screen, reused
saved pages and exact missing queries before acquisition. The new collector
permits only `historyKlines` `1d LAST`, limit 100, at the fixed window on
`api-contract.weex.com`, with at most D distinct missing symbols and 2D
transmitted attempts (hard cap 362). It never sends auth, follows redirects,
retries non-timeout network/local errors, or refreshes catalogs/probes.
Raw response bytes, UTC capture times, plan/source identities and hashes are
preserved for independent offline raw-to-rank verification. The #173 utility
and both accepted evidence bundles remain unchanged.

All retrospective periods at or before R are selection-informed and subject
to current-survivor/selection bias, even for an asset with no previous strategy
run. A future confirmatory test requires a separately predeclared protected
evaluation after the relevant population, strategy and protocol are frozen;
no such calendar is assigned here. Selected symbols still need instrument-
specific 5m LAST/epoch integrity and research-only admission qualification;
Package B still needs all-fill economics. Runtime BTC/ETH and Risk/Safety
authority remain unchanged. This annex authorizes neither historical strategy
execution nor trading.

## Frozen Issue #175 acquisition and result

The pre-acquisition selector/tests/design revision was
`cf541a34873f743f3775943a5d39ae3ab81b59a2`; selector SHA-256
`589ab7bf0fe5bc076fd3932578d43839665a4b73b4efec21bcc1d988c578e8e0`.
The separate plan was committed at `c35492886b34128c34223435cc1201a2e365465f`
before any new market request. Its SHA-256 is
`ab7bae7844eea690599ef6b6fe5065c41e91a562adaef44d9557c2f3ca86de8f`.
The plan pins both #173 manifests, their saved catalog capture times, all
1,078 considered catalog identifiers, the 181 label-screened outer names,
the deterministic eligibility table, 5 exactly matching reused recent daily
pages, and 171 distinct missing `1d LAST` queries. It quarantines all five
unresolved `1000*` economic-scale representations before history acquisition;
176 ordinary direct-label records remain metadata-eligible. The frozen new
attempt ceiling is 342, within the absolute 362 ceiling.

The one-shot new capture sent **171 unauthenticated serial GET attempts**,
one per frozen missing query, from 2026-10-14 16:06:38Z through 16:09:30Z.
There were **zero retries**, 170 saved raw HTTP responses and one non-timeout/
non-5xx transport-unavailable result; no denied-route response or alternate
request shape was recorded. Attempt 079 for `KASUSDT` started at
`2026-10-14T16:07:56.962882Z` with exactly the fixed route, window and query.
It has a durable `NETWORK_UNAVAILABLE` result and no raw response. The frozen
collector does not retry this class. The underlying OS/network cause was not
recorded, so DNS versus TLS versus other local transport failure is **unknown**;
no causal claim is made beyond the typed recorded outcome. The other 170 new
series and five reused #173 series each independently contain exactly 30
valid UTC daily rows with directly reported USDT quote values. No missing
series is treated as zero, no further GET was sent, and no replacement
acquisition is authorized merely to complete a favorable rank.

Result: **`RANKING_INCOMPLETE` / no ten-member panel selected**. The one
otherwise metadata-eligible `KASUSDT` series lacks a response, so its median
and rank are unknown. The known 175 medians do not close the rank over all
declared eligible snapshot exposures. Even if its eventual rank would not
affect the top ten, that cannot be assumed or tested by an unauthorized retry.
BTC/ETH have complete anchor data but are not promoted to a final selection.

For orientation only, the following is the **partial ranking among 175
observed complete series**, not the frozen final ten-member selection:

| Partial rank | Symbol | Median daily direct USDT quote turnover |
| ---: | --- | ---: |
| 1 | BTCUSDT | 2573464911.10187 |
| 2 | ETHUSDT | 2343330069.468575 |
| 3 | SOLUSDT | 429577948.0365 |
| 4 | XRPUSDT | 337111425.6465 |
| 5 | ZECUSDT | 203144795.8565 |
| 6 | ZAMAUSDT | 144068926.41650 |
| 7 | BCHUSDT | 136943206.62135 |
| 8 | BTWUSDT | 114937846.5400 |
| 9 | AVAXUSDT | 86964181.388 |
| 10 | DOGEUSDT | 79123330.65350 |
| 11 | LINKUSDT | 75178577.445 |
| 12 | LTCUSDT | 65223623.8945 |

The complete 175-observed partial rank, all 181 candidate records (including
unavailable `KASUSDT` and five metadata exclusions), every exact daily value,
raw file/row references and all 1,078 considered catalog identifiers are
machine-readable in
[`selection.json`](../../data/research_evidence/issue-175-weex-current-panel-cf541a3/selection.json).
No `selected` flag is true. This is a **precise data/transport blocker**,
not a zero-turnover exclusion, not a substituted panel and not a strategy
result.

New immutable bundle:
`data/research_evidence/issue-175-weex-current-panel-cf541a3/`.
`selection.json` SHA-256
`764d3de84f9686d9bcda0785f582efbfe046a18d4f31c4db637afee6ce7e1d50`;
`manifest.json` SHA-256
`ea5378964cea4b0aa767940281bf781bac3511269ba19f25e57ef46fd642c0ff`.
The bundle is 1,765,342 bytes, below the 5 MiB repository limit. The
manifest hashes every started/result/raw/derived file, and the offline
verifier rederives metadata eligibility, row-grid validity, direct Decimal
quote values, medians, partial ranks, blockers and empty final selection
from pinned source/catalog/raw bytes; changing a rank or flag and rehashing
the derived file is rejected. Reproduce without network:

```text
python -m scripts.research.research_v2_snapshot_selection verify --repo . --evidence data/research_evidence/issue-175-weex-current-panel-cf541a3
```

No historic 5m/full-history data, strategy outcome, account data, order or
runtime symbol admission was requested or produced. The original 27 plus #173
supplementary 8 requests remain separate: 35 old + 171 new = **206 recorded
public attempts**, with no #173 request repeated. Package A is not complete.
