# Research V2 public-data feasibility — Issue #173

Status: **PARTIAL / NOT QUALIFIED** (2026-10-14). This is a finite public-data availability probe, not a selected research universe, accepted dataset, or historical strategy study. It does not amend `RESEARCH_V2_CHARTER_V1`.

## Frozen capture and verification

- Capture utility: `scripts/research/research_v2_universe_feasibility.py`, source SHA-256 `89933757701faa8e1c078cfb6eb6c5ffedf9617e02fc186dd73fe553fe960ff3`, code revision `332db2287b400cadcdcaad6973bbb413ffe3732d`.
- Bundle: `data/research_evidence/issue-173-weex-public-feasibility-332db22/`. `plan.json` SHA-256 `36ad7ba77ad52c254f48e55308aaf6e5ba77e4b5dac279934ea5d323ed0a9c71`; `summary.json` SHA-256 `496e81058cc067149c9b7b370e6d6abe0c8917cc655d928eac006fbe87dea4fe`; `manifest.json` SHA-256 `88d715eedc6e958828bb14f9df475962738c8a9cd86f044875c6b32ed7bcf8ac`.
- Actual market API traffic: **27 serial unauthenticated GET requests**, all HTTP 200, no retries, below the 64-request ceiling; 3 bootstrap, 20 daily, 4 anchor 5m. All transmitted requests, UTC capture times, queries, byte lengths, response SHA-256 and raw bytes are in `attempts.json`, `manifest.json` and the numbered `*.raw.json` files. Total bundle size: 1,020,226 bytes. No account, signed, Demo, order, or other endpoint was called.
- Reproduce offline, without network: `python scripts/research/research_v2_universe_feasibility.py verify --bundle data/research_evidence/issue-173-weex-public-feasibility-332db22`. The verifier checks the exact utility source, file set, lengths, hashes, attempts and route policy. The capture path is intentionally one-use per new directory and cannot overwrite this bundle.
- The plan was written after the three catalog/time bootstrap captures and before any history request. It freezes `H=2026-01-09T00:00:00Z`, server-date-derived `R=2026-10-14T00:00:00Z`, the two 30-day windows, the 5m anchor days, current-only probe identifiers and bounds. No symbol/date substitution followed history results.

## Official-source discovery and scope

The [official WEEX Futures V3 market documentation](https://www.weex.com/api-doc/catalog/core-trading-futures/api/rest-api/market-api) describes current `exchangeInfo`/`apiTradingSymbols` and `historyKlines` with `LAST`, `1d`/`5m`, inclusive millisecond bounds, 100-row limit and an 11-field candle array: open time at index 0, base volume at 5, close time at 6 and quote volume at 7. The four legacy market-document links named in Issue #173 resolved to the current documentation landing rather than a distinct historical catalog. The public catalog response has no documented historical as-of parameter. Current `contractVal`, precision, fees and API status are **current facts**, not historical specifications.

Discovery used three targeted official-WEEX searches and seven distinct official pages (the five issue-listed starting URLs plus an [individual delisting notice](https://www.weex.com/help/articles/44019638954905) and the [Futures delistings section](https://www.weex.com/help/sections/47660551001369)). The notices demonstrate that changes can occur; those inspected sources do **not** prove a complete, timestamped inclusion/exclusion/rename catalog as of `H`. `NOT ESTABLISHED BY THESE SOURCES` is not a claim that no such archive exists elsewhere. No undocumented parameter, alternate host, private endpoint or exchange was tested.

## Capability evidence matrix

| Required fact | Documentation support | Captured observation | Scope/time | Status | Exact artifact reference |
| --- | --- | --- | --- | --- | --- |
| Current market catalog and API-supported identities | Current `exchangeInfo` and `apiTradingSymbols` routes documented | 1,078 considered records; 181 pass the current-only screen, 780 are not currently API-supported, 65 fail crypto-perpetual classification, 51 have API identifier without matching metadata and 1 has excluded underlying | Captured 2026-10-14; not backdated | **CAPTURED_CURRENT_ONLY** | `002-exchange_info.raw.json`, `003-api_trading_symbols.raw.json`, `plan.json:considered` |
| Complete contract population, including delisted/renamed names, as of `H` | No complete historical as-of catalog found within the bounded official discovery | Current catalogs plus individual notices cannot prove all past inclusion, exclusion or contract-unit changes | `H=2026-01-09T00:00:00Z` | **NOT_ESTABLISHED_BY_THESE_SOURCES** | `plan.json:catalog_sha256`; discovery links above |
| Documented vs observed candle shape and volume fields | V3 11-field schema describes quote volume at index 7; current catalog labels quote/margin USDT | All 20 daily pages and four 5m pages parsed as exact Decimal with valid OHLC, nonnegative finite base/quote volumes, coherent zero pairs, aligned open/close timestamps, no duplicate rows within each captured page | Probe responses only; historical contract quantity/`contractVal` and full-universe comparability not established | **SHAPE_OBSERVED; HISTORICAL_UNITS_PARTIAL** | `004`–`023` daily and `024`–`027` 5m raw files; `summary.json:results` |
| Two 30-day daily-turnover inputs for fixed probe identifiers | `1d` LAST historical route documented | Each of 10 identifiers returned exactly 30 distinct closed UTC days in each window; zero missing/off-grid rows under the strict parser; quote field is not derived from close × base volume | `[2025-12-10,H)` and `[2026-09-14,R)`, 600 daily rows total | **COMPLETE_PROBE_ROWS; NOT A HISTORICAL UNIVERSE** | `004`–`023` raw files; per-symbol entries in `summary.json:results` |
| BTC/ETH anchor 5m availability and coverage | `5m` LAST route and 100-row cap documented | Each of four requests returned 100 valid rows for the *last* 8h20m of its 288-row day, from 15:40 to 23:55 UTC. Earlier 188 grid points per day were not acquired. The collector's forward cursor reached the window end after that right-anchored page; it made no follow-up page request. This capture cannot establish full-day pagination/coverage. | BTC/ETH, `[2026-01-08,H)` and `[2026-10-13,R)` | **INCOMPLETE_GRID / PAGINATION_UNRESOLVED** | `024`–`027` raw files, `attempts.json`, `summary.json:results` |
| 5m quote-volume sum vs matching 1d quote volume | Same-venue cross-resolution check is meaningful only with all 288 5m rows | No full-day 5m sum or delta was computed; all four comparisons remain `UNAVAILABLE`, not `MATCH` or `MISMATCH` | Four BTC/ETH anchor days | **UNAVAILABLE** | `summary.json:reconciliations` |
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

The `summary.json` diagnostic medians are descriptive of the 30 captured daily quote-field values only. They are **not** a comparable historical-universe rank or evidence of historical contract-specification equivalence. The data are not accepted as full-panel selection inputs. There is no published precision-based tolerance approved here; therefore reconciliation would require exact equality, and none could be evaluated from these incomplete 5m days.

## Decision and next route

**Recommend only a separately approved current-snapshot/forward or explicitly exploratory research-design route.** The current catalog and fixed daily pages are reproducible and potentially useful for a *prospective* selection design, but they cannot be backdated to `H`. Such a design would require architect approval of its selection timestamp and population semantics, contract/volume-unit qualification, 5m pagination and cross-resolution reconciliation, a new locked manifest, full historical integrity and a research-only symbol-scope change before any study. It must not imply a historically survivorship-free ten-asset panel. The incomplete 5m pagination observed here is a concrete unresolved data-path limitation, not a reason to fabricate an exact daily match or continue probing under this issue. Package B's all-fill economic prerequisite remains separate.
