# Research V2 public-data feasibility — Issue #173

Status: **PARTIAL / NOT QUALIFIED** (2026-10-14). The separate, later missing-prefix supplement completed all four small anchor days and found exact same-provider daily/5m quote-volume matches; historical as-of population and contract-specification comparability remain unqualified. This is a finite public-data availability probe, not a selected research universe, accepted dataset, or historical strategy study. It does not amend `RESEARCH_V2_CHARTER_V1`.

## Original frozen capture and verification (27 requests)

- Capture utility: `scripts/research/research_v2_universe_feasibility.py`, source SHA-256 `89933757701faa8e1c078cfb6eb6c5ffedf9617e02fc186dd73fe553fe960ff3`, code revision `332db2287b400cadcdcaad6973bbb413ffe3732d`.
- Bundle: `data/research_evidence/issue-173-weex-public-feasibility-332db22/`. `plan.json` SHA-256 `36ad7ba77ad52c254f48e55308aaf6e5ba77e4b5dac279934ea5d323ed0a9c71`; `summary.json` SHA-256 `496e81058cc067149c9b7b370e6d6abe0c8917cc655d928eac006fbe87dea4fe`; `manifest.json` SHA-256 `88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac`.
- Actual market API traffic: **27 serial unauthenticated GET requests**, all HTTP 200, no retries, below the 64-request ceiling; 3 bootstrap, 20 daily, 4 anchor 5m. All transmitted requests, UTC capture times, queries, byte lengths, response SHA-256 and raw bytes are in `attempts.json`, `manifest.json` and the numbered `*.raw.json` files. Total bundle size: 1,020,226 bytes. No account, signed, Demo, order, or other endpoint was called.
- Reproduce offline, without network: `python scripts/research/research_v2_universe_feasibility.py verify --bundle data/research_evidence/issue-173-weex-public-feasibility-332db22`. The verifier checks the exact utility source, file set, lengths, hashes, attempts and route policy. The capture path is intentionally one-use per new directory and cannot overwrite this bundle.
- The plan was written after the three catalog/time bootstrap captures and before any history request. It freezes `H=2026-01-09T00:00:00Z`, server-date-derived `R=2026-10-14T00:00:00Z`, the two 30-day windows, the 5m anchor days, current-only probe identifiers and bounds. No symbol/date substitution followed history results.

The original generation remains byte-identical, including its `INCOMPLETE_GRID` four-anchor result. That result was caused by the initial forward paginator exhausting the day after the endpoint returned the rightmost 100 bars; it is not a claim of a venue gap. It must not be retroactively described as a complete capture.

## Reviewed missing-prefix supplement (8 additional requests)

The architect's PR #174 correction authorized only two missing-prefix pages for each of the four saved BTC/ETH anchor days. The corrected utility was frozen at commit `2dd18db6d2586d3510b1d53b04c1bd8cf67ec9ee`, source SHA-256 `55421acaf614e46d33cdd1f936e1daa900f7fc7d1ff679eb53cd1be58cade7fa`, after synthetic right-anchored pagination, route-denial and raw-to-derived verification tests passed. Its new plan was written before any supplementary transmission and pins the original source/plan/manifest identities and four saved 100-row suffix pages. The source-aware offline verifier handles the two evidence generations without executing artifact-supplied code.

New directory: `data/research_evidence/issue-173-weex-prefix-supplement-2dd18db/`. Plan SHA-256 `deee3888f38ab22cbe2289836b81883f7cd5fba8b8113890fda96ce0075b9972`; analysis SHA-256 `1f48dbff38e0a39b6ce30a50f845c6d9a38ca269f8b4c26fe7a6ca54de8688d0`; manifest SHA-256 `34c6e5153f7654eb6e66b004a0d57f9ce5df628229043209c48b809d5a906a87`. `attempts.json` and eight numbered raw files record distinct capture timestamps, exact inclusive request bounds, byte lengths and hashes. There were **8 new serial public GET attempts**, all HTTP 200, with **0 retries**; cumulative original-plus-new attempts are **35**, below the amended maximum 43 and original ceiling 64. No bootstrap, daily, suffix or other historical request was repeated.

Verify both generations offline, without network:

`python scripts/research/research_v2_universe_feasibility.py verify-supplement --original data/research_evidence/issue-173-weex-public-feasibility-332db22 --bundle data/research_evidence/issue-173-weex-prefix-supplement-2dd18db`

This recalculates frozen probe identities, raw candle grids/quote values, the combined 288-row days and exact Decimal daily comparisons; it also checks the original manifest/plan/source lineage, the new request plan and both generations' file/attempt digests. The original capture remains independently verifiable using the `verify` command above.

| Anchor day | Saved suffix | Supplement prefix | Combined coverage | Saved 1d quote | Combined 5m quote | Exact delta / result |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT 2026-01-08 UTC | original `024` (100 rows) | supplement `001`,`002` (100+88) | 288/288 | `6754474539.90276` | `6754474539.90276` | `0.00000` / `EXACT_MATCH` |
| BTCUSDT 2026-10-13 UTC | original `025` (100 rows) | supplement `003`,`004` (100+88) | 288/288 | `3367132079.22217` | `3367132079.22217` | `0.00000` / `EXACT_MATCH` |
| ETHUSDT 2026-01-08 UTC | original `026` (100 rows) | supplement `005`,`006` (100+88) | 288/288 | `2747842888.83519` | `2747842888.83519` | `0.00000` / `EXACT_MATCH` |
| ETHUSDT 2026-10-13 UTC | original `027` (100 rows) | supplement `007`,`008` (100+88) | 288/288 | `2438892694.59460` | `2438892694.59460` | `0.00000` / `EXACT_MATCH` |

The table is a cross-generation projection. The exact filenames and separate timestamps are in both manifests and the new `analysis.json:anchors`; the original `summary.json:reconciliations` correctly remains `UNAVAILABLE`. An exact same-provider cross-resolution match checks these four days internally, not historical membership, contract units across the universe, or full-history integrity.

## Official-source discovery and scope

The [official WEEX Futures V3 market documentation](https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api) describes current `exchangeInfo`/`apiTradingSymbols` and `historyKlines` with `LAST`, `1d`/`5m`, inclusive millisecond bounds, 100-row limit and an 11-field candle array: open time at index 0, base volume at 5, close time at 6 and quote volume at 7. The four legacy market-document links named in Issue #173 resolved to the current documentation landing rather than a distinct historical catalog. The public catalog response has no documented historical as-of parameter. Current `contractVal`, precision, fees and API status are **current facts**, not historical specifications.

Discovery used three targeted official-WEEX searches and seven distinct official pages (the five issue-listed starting URLs plus an [individual delisting notice](https://www.weex.com/help/articles/44019638954905) and the [Futures delistings section](https://www.weex.com/help/sections/47660551001369)). The notices demonstrate that changes can occur; those inspected sources do **not** prove a complete, timestamped inclusion/exclusion/rename catalog as of `H`. `NOT ESTABLISHED BY THESE SOURCES` is not a claim that no such archive exists elsewhere. No undocumented parameter, alternate host, private endpoint or exchange was tested.

## Original-generation capability evidence matrix (before supplement)

| Required fact | Documentation support | Captured observation | Scope/time | Status | Exact artifact reference |
| --- | --- | --- | --- | --- | --- |
| Current market catalog and API-supported identities | Current `exchangeInfo` and `apiTradingSymbols` routes documented | 1,078 considered records; 181 pass the current-only screen, 780 are not currently API-supported, 65 fail crypto-perpetual classification, 51 have API identifier without matching metadata and 1 has excluded underlying | Captured 2026-10-14; not backdated | **CAPTURED_CURRENT_ONLY** | `002-exchange_info.raw.json`, `003-api_trading_symbols.raw.json`, `plan.json:considered` |
| Complete contract population, including delisted/renamed names, as of `H` | No complete historical as-of catalog found within the bounded official discovery | Current catalogs plus individual notices cannot prove all past inclusion, exclusion or contract-unit changes | `H=2026-01-09T00:00:00Z` | **NOT_ESTABLISHED_BY_THESE_SOURCES** | `plan.json:catalog_sha256`; discovery links above |
| Documented vs observed candle shape and volume fields | V3 11-field schema describes quote volume at index 7; current catalog labels quote/margin USDT | All 20 daily pages and four 5m pages parsed as exact Decimal with valid OHLC, nonnegative finite base/quote volumes, coherent zero pairs, aligned open/close timestamps, no duplicate rows within each captured page | Probe responses only; historical contract quantity/`contractVal` and full-universe comparability not established | **SHAPE_OBSERVED; HISTORICAL_UNITS_PARTIAL** | `004`–`023` daily and `024`–`027` 5m raw files; `summary.json:results` |
| Two 30-day daily-turnover inputs for fixed probe identifiers | `1d` LAST historical route documented | Each of 10 identifiers returned exactly 30 distinct closed UTC days in each window; zero missing/off-grid rows under the strict parser; quote field is not derived from close × base volume | `[2025-12-10,H)` and `[2026-09-14,R)`, 600 daily rows total | **COMPLETE_PROBE_ROWS; NOT A HISTORICAL UNIVERSE** | `004`–`023` raw files; per-symbol entries in `summary.json:results` |
| BTC/ETH anchor 5m availability and coverage | `5m` LAST route and 100-row cap documented | Each original request returned 100 valid rows for the *last* 8h20m of its 288-row day, from 15:40 to 23:55 UTC. The initial forward cursor exited without requesting earlier rows. This original generation alone cannot establish full-day coverage; see the separately captured supplement above. | BTC/ETH, `[2026-01-08,H)` and `[2026-10-13,R)` | **ORIGINAL: INCOMPLETE_GRID; COMBINED: COMPLETE** | Original `024`–`027` raw files and `summary.json:results`; supplement `analysis.json:anchors` |
| 5m quote-volume sum vs matching 1d quote volume | Same-venue cross-resolution check requires all 288 5m rows | Original summary remains `UNAVAILABLE`; supplement-plus-original raw projection produces four exact matches with no fitted tolerance | Four BTC/ETH anchor days | **ORIGINAL: UNAVAILABLE; COMBINED: EXACT_MATCH** | Original `summary.json:reconciliations`; supplement `analysis.json:reconciliations` |
| Full-panel ranking, multi-year integrity, research-only symbol scope | Charter requires separate qualification | Not performed; probe identifiers are diagnostic, not ranked or selected | Future separately approved package | **PENDING** | `docs/research/research_v2_charter.md` |
| Final panel / strategy execution / study activation | None authorized here | `NOT_SELECTED` / `NOT_RUN` / `NOT_AUTHORIZED` | Issue #173 | **NOT AUTHORIZED** | `summary.json`, this report |

The ten **probe identifiers only**, frozen before historical responses, were `BTCUSDT`, `ETHUSDT`, `1000BONKUSDT`, `1000FLOKIUSDT`, `1000PEPEUSDT`, `1000RATSUSDT`, `1000SHIBUSDT`, `AAVEUSDT`, `ADAUSDT`, and `AEROUSDT`. The `1000*` contract identities and current `contractVal` fields must not be silently interpreted as historical underlying units or promoted to a final panel. Current API support does not establish past API eligibility. The complete per-record screen and exact ordering are in `plan.json`; no history result was used to choose or replace a symbol.

Each row below is **30/30 complete UTC 1d candles** in each fixed probe window. The filenames are relative to the single evidence directory above; each raw byte count and digest is in `manifest.json`.

| Probe identifier | Historical `[2025-12-10,H)` raw file | Recent `[2026-09-14,R)` raw file |
| --- | --- | --- |
| `BTCUSDT` | `004-historical-BTCUSDT-1d-1.raw.json` | `005-recent-BTCUSDT-1d-1.raw.json` |
| `ETHUSDT` | `006-historical-ETHUSDT-1d-1.raw.json` | `007-recent-ETHUSDT-1d-1.raw.json` |
| `1000BONKUSDT` | `008-historical-1000BONKUSDT-1d-1.raw.json` | `009-recent-1000BONKUSDT-1d-1.raw.json` |
| `1000FLOKIUSDT` | `010-historical-1000FLOKIUSDT-1d-1.raw.json` | `011-recent-1000FLOKIUSDT-1d-1.raw.json` |
| `1000PEPEUSDT` | `012-historical-1000PEPEUSDT-1d-1.raw.json` | `013-recent-1000PEPEUSDT-1d-1.raw.json` |
| `1000RATSUSDT` | `014-historical-1000RATSUSDT-1d-1.raw.json` | `015-recent-1000RATSUSDT-1d-1.raw.json` |
| `1000SHIBUSDT` | `016-historical-1000SHIBUSDT-1d-1.raw.json` | `017-recent-1000SHIBUSDT-1d-1.raw.json` |
| `AAVEUSDT` | `018-historical-AAVEUSDT-1d-1.raw.json` | `019-recent-AAVEUSDT-1d-1.raw.json` |
| `ADAUSDT` | `020-historical-ADAUSDT-1d-1.raw.json` | `021-recent-ADAUSDT-1d-1.raw.json` |
| `AEROUSDT` | `022-historical-AEROUSDT-1d-1.raw.json` | `023-recent-AEROUSDT-1d-1.raw.json` |

Anchor files are `024-historical-BTCUSDT-5m-1.raw.json`, `025-recent-BTCUSDT-5m-1.raw.json`, `026-historical-ETHUSDT-5m-1.raw.json`, and `027-recent-ETHUSDT-5m-1.raw.json`. Each is exactly 100/288 observed grid points; none qualifies a complete anchor day.

The original `summary.json` diagnostic medians are descriptive of the 30 captured daily quote-field values only. They are **not** qualified comparable historical-universe turnover or a rank; historical contract quantity/multiplier/exposure facts are unresolved. The data are not accepted as full-panel selection inputs. No published precision-based tolerance was approved; the combined anchor comparison above used exact equality and no fitted tolerance.

## Decision and next route

**Recommend only a separately approved current-snapshot/forward or explicitly exploratory research-design route.** The current catalog, fixed daily pages and four matched anchor days are reproducible and potentially useful for a *prospective* design, but they cannot be backdated into a complete `H`-as-of contract population. Such a design would require architect approval of selection timestamp and population semantics, historical or prospective contract/volume-unit qualification, a new locked manifest, full-history integrity and research-only symbol scope before any study. The corrected anchor retrieval resolves this particular pagination/coverage question; it does not supply historical membership or justify a survivorship-free ten-asset panel. Package B's all-fill economic prerequisite remains separate.
