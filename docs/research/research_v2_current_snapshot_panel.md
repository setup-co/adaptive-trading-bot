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
