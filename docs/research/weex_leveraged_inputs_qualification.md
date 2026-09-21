# WEEX leveraged-perpetual inputs qualification (#128)

**Retrieval date:** 2026-09-17
**Code baseline:** post-PR #127 `main` at
`8ce7de8e037ffe798272256f4a8b51075c576658`
**Qualification tooling commits:**
`cf4777aef7143bd13140c5d1ebda344562f6635a` (funding) and
`304a3c47e8487ccc82b7eabb4d5278aaa586a256` (partial MARK boundary)
**Scope:** public WEEX V3 data and official WEEX rules only. No private API,
strategy study, leverage choice, repair, interpolation, or runtime leveraged
accounting was performed.

## Decision

| Required input | Status | Evidence boundary |
| --- | --- | --- |
| 5m MARK | `PARTIAL` | ETH epoch B only; BTC and ETH epoch A fail exact grid integrity |
| Funding history | `PARTIAL` | BTC/ETH `[2025-09-18T00:00:00Z, 2026-01-08T00:00:00Z)` only |
| Contract/quantity/notional | `QUALIFIED_CURRENT_ONLY` | V3 public exchange-information snapshot on 2026-09-17 |
| Public maker/taker fee facts | `QUALIFIED_CURRENT_ONLY` | current public `0.0002` maker / `0.0008` taker; account tier is not historical authority |
| Historical leverage/MMR tiers | `MMR_HISTORY_NOT_QUALIFIED` | isolated official announcements exist, but no complete causal schedule |
| Deterministic liquidation replay | `NOT_QUALIFIED` | historical MMR, exact clearance-fee and partial-liquidation mechanics remain incomplete |
| Representative leveraged BTC/ETH research over locked LAST windows | `BLOCKED` | required MARK/funding/MMR/liquidation inputs are not complete |

This is a valid fail-closed outcome. It does not alter the existing LAST corpus
and does not authorize a leveraged study.

## Existing LAST authority (unchanged)

- source: `weex-v3-last-e5b0c4035a19d5a80ca7`
- dataset: `weex-v1-5m-last-a629a29b580b2e103adba918`
- manifest SHA-256:
  `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`
- BTC: `[2024-07-01T00:00:00Z, 2026-01-08T00:00:00Z)`
- ETH A: `[2024-07-01T00:00:00Z, 2025-01-01T00:00:00Z)`
- ETH excluded: `[2025-01-01T00:00:00Z, 2025-04-02T00:00:00Z)`
- ETH B: `[2025-04-02T00:00:00Z, 2026-01-08T00:00:00Z)`

`git diff -- data/research_corpus` is empty. The MARK and funding snapshots are
separate datasets under `data/research_inputs/`.

## MARK qualification

Endpoint: official V3 `GET /capi/v3/market/historyKlines`, with explicit
`symbol`, `interval=5m`, `priceType=MARK`, inclusive `startTime`/`endTime`, and
`limit=100`. The source walks backwards deterministically and validates exact
Decimal OHLC, positive/valid OHLC, the UTC 5m grid, `close_time = open_time +
5m`, ordering, duplicates and conflicts. Additive LAST-style fields are not
treated as meaningful MARK evidence.

Results:

- BTC full accepted interval: **FAIL**. First missing MARK open is
  `2024-10-08T03:45:00Z`; the whole accepted BTC interval is unavailable for
  representative liquidation research.
- ETH epoch A: **FAIL**. First missing MARK open is also
  `2024-10-08T03:45:00Z`; the whole epoch is unavailable.
- ETH epoch B: **PASS**, 80,928 exact grid rows, 810 source requests during
  materialization, one transport retry, zero identical duplicates and zero
  conflicts.
- The pre-existing ETH excluded interval was not queried into or bridged by the
  locked artifact.

Locked partial dataset:

```text
source_id = weex-v3-mark-866141a668b28fd2a6fe
dataset_version = weex-v1-5m-mark-97c879a06fc900676dc5c339
manifest_sha256 = 3e5ed9bc81877fa9c1d45fe33145d82f36487afeb5e49e9502ae087ec0749275
ETHUSDT-5m-MARK.parquet rows = 80928
ETHUSDT-5m-MARK.parquet sha256 = 8983b80b71d76a762f703a979f88f79359cab5a262ee4a0a5bd9d17a142abb82
```

There is deliberately no BTC MARK artifact. A locked read for BTC fails with
`CorpusManifestError`; it cannot fall back to the API.

## Funding qualification

Endpoint: official V3 `GET /capi/v3/market/fundingRate`. The documented request
window is at most seven days and both boundaries are inclusive. The qualifier
uses deterministic adjacent windows, preserves exact `fundingTime`,
`fundingRate`, `markPrice` and symbol, and accepts only identical duplicates at
the shared inclusive boundary.

The live public endpoint rejected January 2025 with code `-1170` and
`startTime must be within the last 365 days`; it also rejected a range longer
than seven days. Consequently the full LAST-corpus windows cannot be recovered
now. The locked suffix starts at `2025-09-18T00:00:00Z`, within the observed
retention boundary on retrieval day.

For each symbol the accepted suffix contains 336 events, 16 requests, zero
retries, 15 identical inclusive-boundary duplicates, zero conflicts, and only
28,800-second observed spacing. No missing event was filled or synthesized.

```text
source_id = weex-v3-funding-5ff91210b76e87d5dbef
dataset_version = weex-v1-funding-87c1f6e88bd64d96c015fe39
manifest_sha256 = 6ecc0c5717a0ec3e5c0c28a96c542226a48e007ccdda48934e5900d304746c52
BTCUSDT-funding.parquet rows = 336
BTCUSDT-funding.parquet sha256 = 69b4451f971a7c0e3e54618eed7a008da918f6c36f2dc8119156c6c4399e5e7c
ETHUSDT-funding.parquet rows = 336
ETHUSDT-funding.parquet sha256 = 03cb7303be2a9a12f24b7e165d871b2d3135766e3f3287a49238eeafaf39cf07
```

## Reproduction commands

The first command below is the original audit invocation, executed while the
working tree was at its recorded `cf4777a...` tooling commit; it stopped at the
first BTC MARK gap before funding ran. The explicit BTC/ETH probes establish
the rejected intervals and accepted ETH-B interval. The final two builder
commands were then run from their manifest-recorded tooling commits and require
fresh/empty output roots. Reproducing byte-identical dataset identities requires
checking out the exact recorded `source_code_commit`, not merely passing another
SHA as metadata.

```powershell
.\.venv\Scripts\python.exe -m adaptive_trading_bot.research.weex_leveraged_inputs --mark-output-root data/research_inputs/build-mark --funding-output-root data/research_inputs/build-funding --source-code-commit cf4777aef7143bd13140c5d1ebda344562f6635a --built-at 2026-09-17T08:31:05Z

.\.venv\Scripts\python.exe -c "from datetime import UTC,datetime; from adaptive_trading_bot.research.weex_leveraged_inputs import WeexV3MarkHistorySource; WeexV3MarkHistorySource().fetch_range('BTCUSDT',start=datetime(2024,7,1,tzinfo=UTC),end=datetime(2026,1,8,tzinfo=UTC))"

.\.venv\Scripts\python.exe -c "from datetime import UTC,datetime; from adaptive_trading_bot.research.weex_leveraged_inputs import WeexV3MarkHistorySource; WeexV3MarkHistorySource().fetch_range('ETHUSDT',start=datetime(2024,7,1,tzinfo=UTC),end=datetime(2025,1,1,tzinfo=UTC))"

.\.venv\Scripts\python.exe -c "from datetime import UTC,datetime; from adaptive_trading_bot.research.weex_leveraged_inputs import WeexV3MarkHistorySource; r=WeexV3MarkHistorySource().fetch_range('ETHUSDT',start=datetime(2025,4,2,tzinfo=UTC),end=datetime(2026,1,8,tzinfo=UTC)); print(len(r.records),r.request_count,r.retry_count,r.identical_duplicate_count)"

.\.venv\Scripts\python.exe -c "from datetime import UTC,datetime; from pathlib import Path; from adaptive_trading_bot.research.weex_leveraged_inputs import build_funding_dataset; print(build_funding_dataset(Path('data/research_inputs/build-funding'),source_code_commit='cf4777aef7143bd13140c5d1ebda344562f6635a',built_at=datetime(2026,9,17,8,31,5,tzinfo=UTC)).to_json())"

.\.venv\Scripts\python.exe -c "from datetime import UTC,datetime; from pathlib import Path; from adaptive_trading_bot.research.weex_leveraged_inputs import build_mark_dataset; print(build_mark_dataset(Path('data/research_inputs/build-mark'),source_code_commit='304a3c47e8487ccc82b7eabb4d5278aaa586a256',built_at=datetime(2026,9,17,8,31,5,tzinfo=UTC)).to_json())"
```

Fresh API history is builder-only. Later reads must name the exact dataset
version and pass the reviewed manifest registry in
`LockedWeexLeveragedInputDataset`.

## Contract, quantity, notional and fee semantics

The exact point-in-time V3 response subset is stored in
`weex_v3_contract_snapshot_2026-09-17.json` (SHA-256
`5cd95943d33e6a07210f981e100fe8dc77110f310f8f5f26e2fa752b09147a30`).
Official exchange-information
documentation states that `minOrderSize`, `maxOrderSize`, `maxPositionSize` and
`marketOpenLimitSize` are base-asset quantities and that `contractVal` is the
contract size.

The later canonical internal unit must therefore be base quantity:

```text
contracts_or_lots = base_quantity / contractVal  (must be an exact integer)
base_quantity = contracts_or_lots * contractVal
effective_notional_USDT = base_quantity * price
```

This exactly reconciles older help-center `face value × lots × price` wording
without treating legacy lot count as V3 order quantity. Current facts are:

| Symbol | contractVal | price precision | quantity precision | min order | max order | max position | max market open |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | 0.0001 BTC | 1 | 4 | 0.0001 BTC | 1200 BTC | 1000000 BTC | 100 BTC |
| ETHUSDT | 0.001 ETH | 2 | 3 | 0.001 ETH | 1000000 ETH | 5000000 ETH | 2300 ETH |

The captured public response reports `makerFeeRate=0.0002` and
`takerFeeRate=0.0008`. It did **not** expose `apiMakerFeeRate` or
`apiTakerFeeRate` for either BTCUSDT or ETHUSDT; absence means not returned,
not a zero rate. The returned generic fields are a retrieval-time public
schedule, not proof of the user's account/VIP/API fee or the historical
schedule. Later research must use a separately predeclared, versioned cost
configuration; it must not silently claim these current fields were
historically observed.

## MMR/leverage history

Final status: **`MMR_HISTORY_NOT_QUALIFIED`**.

Official evidence found:

| Effective time | Symbol | What the official announcement proves | Causal limitation |
| --- | --- | --- | --- |
| 2024-11-04 10:30 UTC+8 (`2024-11-04T02:30:00Z`) | BTCUSDT | An exact eleven-tier table, from `0–4,900,000 USDT` at max leverage 200/MMR 0.3% through `840,000,001–10,000,000,000 USDT` at max leverage 1/MMR 50%. | No collected announcement establishes the BTC schedule for the earlier accepted interval from 2024-07-01 to this effective time. |
| 2025-01-07 14:00 UTC+8 (`2025-01-07T06:00:00Z`) | ETHUSDT | The exact eleven-tier table below. | This starts after accepted ETH interval A ends and falls inside the excluded ETH interval. It does not establish the schedule for accepted interval A. |
| 2025-02-07 08:00 UTC+8 (`2025-02-07T00:00:00Z`) | BTCUSDT, ETHUSDT | Maximum leverage increased to 300x. | The announcement publishes no position boundaries or MMRs, so it proves a policy change but not the resulting complete tier schedule. |
| 2025-02-21 18:00 UTC+8 (`2025-02-21T10:00:00Z`) | BTCUSDT, ETHUSDT | Maximum leverage increased to 400x. | The announcement publishes no position boundaries or MMRs, so the schedule applicable to accepted ETH interval B and the later BTC interval remains under-specified. |
| 2026-02-13 18:00 UTC+8 (`2026-02-13T10:00:00Z`) | BTCUSDT, ETHUSDT | An announcement outside the locked windows publishes both previous and updated full tables. The previous tables include 400x/300x tiers. | It proves a later schedule state, but does not date when the 400x/300x position boundaries and MMRs began. It cannot be back-projected into the locked windows. |

The causal BTC chain is therefore the exact 200x-era table effective
2024-11-04, the maximum-only 300x change effective 2025-02-07, the
maximum-only 400x change effective 2025-02-21, and the full table described as
`previous` immediately before the 2026-02-13 change. The causal ETH chain is
the exact 200x-era table effective 2025-01-07, the same two maximum-only
changes, and the 2026 `previous` table. The ETH table remains relevant policy
evidence even though its effective instant is in the candle-data exclusion: it
establishes the schedule from that instant until the 300x announcement changed
the declared maximum. Neither maximum-only event discloses the replacement
boundaries/MMRs needed to bridge either chain.

The exact ETH table effective `2025-01-07T06:00:00Z` is:

| Position value (USDT) | Max leverage | MMR |
| ---: | ---: | ---: |
| 0–3,000,000 | 200 | 0.30% |
| 3,000,001–4,000,000 | 100 | 0.50% |
| 4,000,001–5,000,000 | 75 | 1% |
| 5,000,001–6,000,000 | 50 | 1.50% |
| 6,000,001–7,000,000 | 25 | 2% |
| 7,000,001–9,000,000 | 10 | 5% |
| 9,000,001–10,000,000 | 5 | 10% |
| 10,000,001–24,000,000 | 4 | 12.50% |
| 24,000,001–46,000,000 | 3 | 15% |
| 46,000,001–164,000,000 | 2 | 25% |
| 164,000,001–10,000,000,000 | 1 | 50% |

On 2026-09-20, the official WEEX Help Center archive and official-domain
search results around the accepted research windows were reviewed for BTC/ETH
leverage, position-tier and MMR changes. Beyond the dated events above, no
additional BTCUSDT- or ETHUSDT-specific tier/MMR/max-leverage announcement
effective anywhere in the accepted 2024-07-01 through 2026-01-08 timelines was
discovered. That is a bounded search result, **not** proof that no unindexed,
amended or otherwise undiscovered change occurred.

The current exchange-information snapshot reports min/max leverage `1/400`
for both symbols, but contains no MMR tiers and cannot be back-projected.
Accordingly, the known BTC and ETH events still do not form a complete causal
schedule for either accepted research timeline. No maximum-only announcement,
current snapshot, or February 2026 table may supply missing historical tier
boundaries or MMRs. `MMR_HISTORY_NOT_QUALIFIED`, deterministic liquidation
replay `NOT_QUALIFIED`, and representative leveraged research `BLOCKED` remain
the required fail-closed statuses.

## Isolated margin and liquidation semantics

The official material consistently establishes:

- initial margin depends on position notional and configured leverage;
- unrealized PnL and liquidation use MARK, not LAST;
- maintenance margin is position-size-tiered;
- isolated long/short positions have separate margin balances and tiers;
- funding transfers are based on position value, independent of leverage, and
  a payment is deducted from fixed position margin;
- a MARK breach can cancel orders and trigger partial or full close;
- the terms mention realized losses and liquidation-clearance-fee deductions.

It does not provide enough causally historical, internally complete public
facts to reproduce the exact threshold and reduction path throughout the
locked windows. Missing items include the complete historical MMR schedule,
the exact historical liquidation-clearance/closing-fee term, tier transition
deductions (if any), and deterministic intra-candle ordering between a LAST
stop and MARK liquidation. Therefore no liquidation formula is implemented.

Representative V1 research remains isolated-margin only, with cross margin
out of scope and auto margin top-up **OFF**. No leverage value was selected.

## Minimum later deterministic contract (specification only)

A separately approved implementation must carry, version and validate:

- side, entry fill, canonical base quantity and exact effective notional;
- Risk-owned configured leverage and isolated initial margin;
- causally applicable MMR tier/version and maintenance margin;
- locked LAST path for configured fill/protective-stop behavior;
- independently locked MARK path for unrealized PnL/liquidation checks;
- exact observed funding events and cumulative funding transfer;
- isolated margin balance/equity and liquidation threshold/distance, or an
  explicit unavailable state;
- protective-stop/liquidation safety-buffer policy version;
- liquidation/account-survival state; and
- full dataset/configuration provenance.

If a coarse interval permits both a LAST stop and MARK liquidation and exact
ordering cannot be observed, that later issue must predeclare a conservative
ambiguity rule. LLM output must never own any of this arithmetic or authority.

## Official sources

- [V3 historical Klines](https://www.weex.com/api-doc/contract/Market_API/GetHistoryKlines)
- [V3 funding history](https://www.weex.com/api-doc/contract/Market_API/GetFundingRateHistory)
- [V3 exchange information](https://www.weex.com/api-doc/contract/Market_API/GetContractInfo)
- [Funding fees](https://www.weex.com/help/articles/4410862743449)
- [Estimated liquidation price](https://www.weex.com/help/articles/4417905343129)
- [Maintenance margin rate](https://www.weex.com/help/articles/5491591989785)
- [Isolated margin](https://www.weex.com/help/articles/4417900072089)
- [Auto margin top-up](https://www.weex.com/help/articles/4417899831833)
- [Futures terms](https://www.weex.com/help/articles/48842594089625)
- [BTC tier change, 2024-11-04](https://www.weex.com/help/articles/39281474928921)
- [ETH tier change, 2025-01-07](https://www.weex.com/help/articles/42043870126873)
- [BTC/ETH maximum leverage 300x, 2025-02-07](https://www.weex.com/help/articles/43193704907929)
- [BTC/ETH maximum leverage 400x, 2025-02-21](https://www.weex.com/help/articles/43739584116761)
- [BTC/ETH tier change, 2026-02-13](https://www.weex.com/help/articles/55108075621273)
