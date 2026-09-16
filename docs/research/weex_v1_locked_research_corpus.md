# WEEX V1 locked historical research corpus

## Final qualification

```text
WEEX_BTC_V1_RESEARCH_CORPUS = QUALIFIED
WEEX_ETH_V1_RESEARCH_CORPUS = PARTIAL
WEEX_V1_HISTORICAL_SOURCE = QUALIFIED_WITH_BOUNDARIES
```

This qualification is based only on source integrity and the predeclared exclusion rules. No
strategy or profitability result was inspected or produced. The corpus uses public WEEX Futures
V3 `5m` `LAST` rows as its sole canonical authority. It contains no interpolation, repair,
synthetic candle, native higher-timeframe row, MARK/INDEX row, or data from another venue.

## Provenance

- Issue: `#115`
- Starting `main`: `6d7fdc52c0e7a19f4521fc528ee17c0b484efed6`
- Build-tool commit: `defbf035481b126f590bec093e0d5e36867bb7bc`
- Endpoint: `GET https://api-contract.weex.com/capi/v3/market/historyKlines`
- Query identity: `interval=5m`, `priceType=LAST`, `BTCUSDT` / `ETHUSDT`
- Authentication/private API use: none
- Dataset version: `weex-v1-5m-last-a629a29b580b2e103adba918`
- Version-bound source ID: `weex-v3-last-e5b0c4035a19d5a80ca7`
- Manifest schema: `weex-v1-research-corpus-v1`
- Canonical schema: `weex-futures-v3-last-5m-v1`
- Recorded build time: `2026-09-15T18:00:00Z`
- Original accepted qualification build root:
  `study-artifacts/issue-115-weex-qualified-corpus-defbf03-run3`
- Durable repository snapshot:
  `data/research_corpus/weex-v1-5m-last-a629a29b580b2e103adba918/`

The public WEEX archive has changed historically, so these hashes—not a future API response—are
the authority for this dataset version.

## ETH March 2025 boundary evidence

Two independent bounded requests were run for each relevant control window. Both repetitions
returned byte-identical JSON content for their respective windows:

- `2025-03-04T10:30Z` through `12:00Z`: response SHA-256
  `f8930b1519b9f233d2f1a3bf0361ab48952567905811be5ad0e9d7ccd05ad4c4`;
- `2025-03-04T15:00Z` through `16:30Z`: response SHA-256
  `c4d4f2eb6b152036384ece9bafdec44fe98d9621e3e09a3c75ee07b6117a2377`.

The current response at `2025-03-04T11:20:00Z` is a complete valid 11-field row. The exact
remaining contiguous malformed sequence is:

| Open time | Close time | Failure |
| --- | --- | --- |
| `2025-03-04T15:50:00Z` | `2025-03-04T15:55:00Z` | blank taker-buy base and quote volume |
| `2025-03-04T15:55:00Z` | `2025-03-04T16:00:00Z` | blank taker-buy base and quote volume |

Every 5-minute grid control in the two bounded windows was present; the adjacent rows before and
after the two-row defect were valid. There were no request retries or transport failures in these
four boundary requests.

An initial full accepted-build attempt applied the apparent minimum quarantine
`[2025-03-04T15:50Z, 2025-03-04T16:00Z)`. It failed closed during the larger Jan-Apr traversal
when WEEX returned another row with a blank taker-buy field outside the two excluded opens. The
failed builder version did not retain sufficient timestamp diagnostics to justify expanding the
narrow boundary by guesswork, and it emitted no manifest or accepted artifact. Therefore the
narrow quarantine proposal is **rejected**. In accordance with Issue #115, the already evidenced
larger ETH segment is preserved as excluded rather than weakening validation:

`[2025-01-01T00:00:00Z, 2025-04-02T00:00:00Z)`

The permanent builder now includes symbol/open-time context in such failures. No row from the
excluded segment is present in the accepted canonical or derived artifacts.

## Accepted canonical boundaries

| Symbol | Accepted canonical 5m intervals | Rows | Status |
| --- | --- | ---: | --- |
| BTCUSDT | `[2024-07-01T00:00:00Z, 2026-01-08T00:00:00Z)` | 160,128 | QUALIFIED |
| ETHUSDT | `[2024-07-01T00:00:00Z, 2025-01-01T00:00:00Z)` | 52,992 | PARTIAL |
| ETHUSDT | `[2025-04-02T00:00:00Z, 2026-01-08T00:00:00Z)` | 80,928 | PARTIAL |

For every accepted interval the completed traversal found zero missing grid points, zero
identical or conflicting duplicates, zero malformed/off-grid rows, zero invalid/non-positive
OHLC values, zero negative/invalid additive fields, and zero close-time misalignments.

The accepted build made 1,602 BTC requests and 1,340 ETH requests. It recorded one recovered BTC
transport retry and twelve recovered ETH transport retries. Every retry repeated the exact same
parameters; there were zero unrecovered requests and no retry was used to repair a content gap.

## Warm-up exclusions

The longest V1 feature seed is 50 bars. The governing causal warm-up is therefore 50 complete 4h
bars, exactly 200 hours; it is not an optimized or guessed strategy parameter.

| Symbol | Evaluation exclusion | Meaning |
| --- | --- | --- |
| BTCUSDT | `[2024-07-01T00:00Z, 2024-07-09T08:00Z]` | initial 200h warm-up |
| ETHUSDT | `[2024-07-01T00:00Z, 2024-07-09T08:00Z]` | initial 200h warm-up |
| ETHUSDT | `[2025-01-01T00:00Z, 2025-04-10T08:00Z]` | excluded segment plus fresh 200h warm-up from Apr 2 |

The stored `warmup_exclusions` use half-open interval values and the reader's observation helper
treats the ending close as excluded, so evaluation first resumes strictly after the 50th 4h close.
Feature engines may consume the clean warm-up rows, but research conclusions must exclude those
observation times.

## Deterministic derived artifacts

Each target UTC bucket is built only when all canonical constituents exist inside the same
accepted interval:

- 15m: exactly 3 canonical rows;
- 1h: exactly 12 canonical rows;
- 4h: exactly 48 canonical rows;
- open = first constituent open;
- high = exact maximum;
- low = exact minimum;
- close = last constituent close;
- volume = exact Decimal sum of canonical base volume.

No derived candle spans the ETH exclusion. Existing `ParquetHistoricalCandleStore.scan_integrity`
passed independently for every derived interval.

| Symbol | 15m rows | 1h rows | 4h rows |
| --- | ---: | ---: | ---: |
| BTCUSDT | 53,376 | 13,344 | 3,336 |
| ETHUSDT | 44,640 | 11,160 | 2,790 |

## Artifact verification

- Canonical BTC Parquet SHA-256:
  `496ff8e2b10bd1b683f4c43dc267bba4b181808c29f8a5be9cc783a2108dc653`
  (13,365,679 bytes)
- Canonical ETH Parquet SHA-256:
  `1d350cd166a92b30676dedf7661d3d4ad215d426cfd63970bb748f041980fbe2`
  (11,014,902 bytes)
- Artifact count: 107
- Total artifact bytes: 32,430,361
- Manifest SHA-256:
  `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`
  (31,042 bytes)
- Complete name-plus-NUL-plus-bytes tree SHA-256, including manifest:
  `e4558a5ace6d7a7216dc33cd5833370995bd15fcff83c7e686b99943cd5f9264`
- Committed full artifact inventory:
  `docs/research/manifests/weex-v1-5m-last-a629a29b580b2e103adba918.json`
- Committed immutable snapshot:
  `data/research_corpus/weex-v1-5m-last-a629a29b580b2e103adba918/`

An independent post-build load revalidated the manifest/version, all 107 artifact hashes and
sizes, every derived interval scan, and aggregate row counts. Result: `PASS`.

## Locked research behavior

`atb-build-weex-corpus` is the networked qualification/build tool. It uses the public endpoint
only and requires a fresh output root; it is not a fallback used by normal research reads. The
approved bytes are durably committed under the versioned repository corpus directory.
`LockedWeexResearchCorpusStore` accepts that local version root plus the exact dataset version;
it has no network/source dependency and cannot merge.

The locked store fails closed when:

- the schema or requested dataset version is unknown;
- source identity differs from the version-bound ID;
- a file is absent or its SHA-256/size differs;
- a read/scan omits finite start/end boundaries;
- a requested range crosses an excluded or unqualified boundary;
- a Parquet schema/integrity check fails.

There is no silent fresh-WEEX fallback. The shared historical-source selector is used by Replay,
primary Research Corpus, and Exit Robustness. A `weex-v3-last-*` source must supply the exact
qualified version and resolves only to `LockedWeexResearchCorpusStore` under
`ATB_QUALIFIED_RESEARCH_CORPUS_ROOT`; a generic Parquet root is ignored for that namespace.
Missing/unknown versions, source/version mismatches, mixed generic/qualified sources, changed
manifest or artifact bytes, and unavailable/excluded ranges fail closed. Strategy, Risk, Safety,
Experience, execution, Paper, and Live code remain unchanged.

### Operator-entry-point audit

The production CLI audit covered every `ParquetHistoricalCandleStore` construction and every
Replay/Research command entry point:

- `atb-replay`: shared selector; qualified namespace is locked;
- `atb-research-corpus`: shared selector; qualified namespace is locked;
- legacy `trend_pullback_corpus` module execution: delegates to the same Research Corpus CLI;
- `atb-research-exit-robustness`: shared selector; qualified namespace is locked;
- `atb-import-ohlcv`: a bounded generic public-data writer, not a qualified research reader;
- corpus builder and locked-store internals: deliberate lower-level construction used to create
  derived artifacts and then expose them read-only;
- remaining direct constructions: unit-test fixtures or deliberately generic lower-level APIs.

No other normal research/replay CLI can open a `weex-v3-last-*` source through generic Parquet.
The one-time corpus-materialization workflow was removed after the durable snapshot was committed.

## Reproduction

From build-tool commit `defbf035481b126f590bec093e0d5e36867bb7bc`, with a fresh external
directory:

```bash
atb-build-weex-corpus \
  --output-root /absolute/external/weex-v1-corpus \
  --source-code-commit defbf035481b126f590bec093e0d5e36867bb7bc \
  --built-at 2026-09-15T18:00:00Z
```

Because the public archive is mutable, a later build may correctly produce different hashes or
fail qualification. It must receive a new reviewed dataset version and must never overwrite or
silently replace the committed accepted directory. The repository marks corpus and manifest
files as byte-preserving Git content so checkout newline conversion cannot change the approved
manifest SHA-256.

## Limitations and prohibited interpretations

- ETH Jan-Apr 2025 remains excluded; the corpus is intentionally partial for ETH.
- The two accepted ETH segments are independent. Research must not treat the exclusion as a
  continuous market path or carry indicator/position state across it.
- Data qualification says nothing about profitability or strategy robustness.
- The accepted immutable corpus bytes are committed under the exact version directory; mutation
  in place invalidates the manifest/hash gate and is prohibited.
- No private endpoint, credential, order, Paper/Live behavior, leverage, Experience authority
  change, strategy/backtest performance run, or deployment occurred.
