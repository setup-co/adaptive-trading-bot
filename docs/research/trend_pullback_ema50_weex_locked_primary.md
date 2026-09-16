# Trend Pullback EMA50 WEEX locked-corpus primary study evidence

## Outcome

```text
WEEX_LOCKED_EMA50_PRIMARY_STUDY = STOPPED
RUN_A_BTC_FULL = FAILED_CLOSED
RUN_B_ETH_EPOCH_A = NOT_RUN
RUN_C_ETH_EPOCH_B = NOT_RUN
```

The predeclared study stopped during Run A before an authoritative JSON report was emitted.
The accepted research CLI exited with status `1` and the exact diagnostic:

```text
exit robustness failed: invalidation reference contradicts position side
```

Issue #118 requires the study to stop if a strategy, execution, accounting, report-validation,
or tooling assumption fails. Accordingly, Run A was not retried, Runs B and C were not started,
and no result or PnL delta was interpreted. This document is failure evidence only. It does not
approve or reject the EMA50 exit hypothesis and has no trading authority.

## Locked provenance

- Issue: `#118`
- Exact starting `main` and execution commit:
  `a15bcfe1927b2c0a0483a08ec5bfa339cc22c62f`
- Source ID: `weex-v3-last-e5b0c4035a19d5a80ca7`
- Dataset version: `weex-v1-5m-last-a629a29b580b2e103adba918`
- Manifest SHA-256:
  `98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95`
- Durable locked root:
  `data/research_corpus/weex-v1-5m-last-a629a29b580b2e103adba918/`
- Runtime: Windows `10.0.26200.0`, Python `3.12.14`
- Study-root creation: `2026-09-16T17:29:20.6678415Z`
- External evidence root:
  `C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-118-weex-locked-primary-a15bcfe`

The working tree was clean and `HEAD` matched the locked commit immediately before execution.
No WEEX API, private endpoint, alternate venue, generic Parquet fallback, corpus builder, or data
repair path was used.

## Locked corpus preflight

Before Run A, the repository manifest SHA-256 was checked against the value above. The normal
fail-closed historical-source selector was then called with the exact source and dataset version.
It resolved to `LockedWeexResearchCorpusStore`, revalidated the manifest plus all 107 artifact
hashes and sizes, and exposed source ID `weex-v3-last-e5b0c4035a19d5a80ca7`.

```text
RUN_A_PREFLIGHT = PASS
STORE_TYPE = LockedWeexResearchCorpusStore
SOURCE_ID = weex-v3-last-e5b0c4035a19d5a80ca7
```

The manifest remained unchanged after the failed run. The failure was therefore downstream of
the locked-corpus identity and artifact-integrity gate.

## Exact predeclared protocols

All protocols below retain the Issue #118 assumptions: independent replay state, research equity
`10000 USDT`, follow-up `168` hours, spread `2` bps, slippage `2` bps, fee `10` bps, and the
existing simulator latency default.

| Run | Symbol | Decision window | Maximum read-only follow-up boundary | Execution |
| --- | --- | --- | --- | --- |
| A | BTC/USDT | `[2024-07-01T00:00:00Z, 2026-01-01T00:00:00Z)` | `2026-01-08T00:00:00Z` | failed closed |
| B | ETH/USDT | `[2024-07-01T00:00:00Z, 2024-12-25T00:00:00Z)` | `2025-01-01T00:00:00Z` | not run |
| C | ETH/USDT | `[2025-04-02T00:00:00Z, 2026-01-01T00:00:00Z)` | `2026-01-08T00:00:00Z` | not run |

Run B and Run C were intentionally not executed after Run A failed. The ETH exclusion
`[2025-01-01T00:00:00Z, 2025-04-02T00:00:00Z)` was not touched or bridged.

## Exact command executed

The qualified corpus root was explicitly bound to the committed `data/research_corpus` directory.
No `--data-root` argument was passed.

```powershell
$env:ATB_QUALIFIED_RESEARCH_CORPUS_ROOT=(Resolve-Path 'data\research_corpus').Path
.\.venv\Scripts\atb-research-exit-robustness.exe `
  --source weex-v3-last-e5b0c4035a19d5a80ca7 `
  --dataset-version weex-v1-5m-last-a629a29b580b2e103adba918 `
  --symbol BTC/USDT `
  --start 2024-07-01T00:00:00Z `
  --end 2026-01-01T00:00:00Z `
  --research-equity 10000 `
  --follow-up-hours 168 `
  --spread-bps 2 `
  --slippage-bps 2 `
  --fee-bps 10 `
  --output C:\Users\elect\Documents\Codex\2026-09-09\x20\study-artifacts\issue-118-weex-locked-primary-a15bcfe\run-a-btc-full.json
```

- Exit code: `1`
- Standard error: `exit robustness failed: invalidation reference contradicts position side`
- Requested output file exists: `false`

The CLI writes the artifact only after constructing and validating the complete sealed report.
No partial JSON artifact was emitted, so there is no report SHA-256, byte size, corpus ID, corpus
digest, robustness configuration digest, or report digest to preserve.

## Warm-up/admission gate

The mandatory warm-up/admission gate is **unavailable**, not passed. Run A failed before a sealed
authoritative report was emitted, so its complete research-admissible identity population could
not be accepted for gate evaluation. No PnL, exit, censoring, overlap, stratum, or chronology fact
was interpreted.

| Required gate | Run A | Run B | Run C |
| --- | --- | --- | --- |
| No admissible identity inside applicable warm-up exclusion | unavailable | not run | not run |
| First admissible identity strictly after warm-up boundary | unavailable | not run | not run |
| No identity inside excluded ETH interval | not applicable | not run | not run |
| Fresh independent replay state | command started independently | not run | not run |

The unavailable Run A gate is itself sufficient to stop the study under Issue #118. No post-hoc
filtering or reinterpretation was attempted.

## Unavailable authoritative results

Because no validated JSON report exists, all required population, censoring, paired-delta,
protective-stop, EMA50-exit, overlap-component, LONG/SHORT, 15m-regime, HTF-bias, MTF-alignment,
UTC-month, and chronological-stability fields are unavailable. There is no cross-run descriptive
table because none of the three required independent evidence units completed.

No external script or spreadsheet recomputed fills, PnL, R, exit causes, or deltas.

## Scope and next step

This evidence records a reproducible fail-closed blocker in the accepted tooling at the locked
code and data identities. Any diagnosis or code correction requires a separate reviewed issue
and PR, followed by a new predeclared study issue. This issue did not change strategy parameters,
execution assumptions, Risk, Safety, Experience, Router, Paper, Shadow, Live, leverage, venue
policy, corpus bytes, corpus manifests, or the Master Plan. No deployment occurred.
