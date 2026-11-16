# Two-tier GitHub Actions policy

Normal pull-request commits use **FAST CI**. The repository-owned classifier compares the exact PR
base and head SHAs, runs Ruff and Mypy for Python changes, selects deterministic relevant tests, and
fails unknown paths closed to the complete backend suite plus frontend checks. Frontend checks run
only for frontend, workflow or unknown changes. Truly documentation-only changes receive a minimal
scope/whitespace preflight; protocol, manifest, research and project-state documents are explicitly
source-sensitive.

FAST runs use PR-scoped concurrency with `cancel-in-progress: true`. A new commit cancels only the
obsolete FAST workflow for that PR. FULL is a distinct workflow with no cancellation policy, so a
new FAST event cannot cancel it. Every FAST run reports measured job seconds, summed runner minutes
and the longest measured job, and explicitly says that its coverage is not the full regression
gate. When every planned FAST job passes, the overall FAST workflow is green.

**FULL CI** is deliberate. An owner or reviewer adds the `ci-full` label to the PR only when the
current HEAD is ready for final validation. The workflow checks out and verifies the exact event
HEAD, then runs the complete Python suite, Ruff, Mypy and all frontend gates under the preserved
`quality` and `frontend-quality` job names. `full-suite` succeeds only if both complete jobs pass.
Because FULL is separately triggered, ordinary PR updates create no `quality`, `frontend-quality`
or `full-suite` checks at all—there is no skipped FULL job that GitHub could treat as success. After
any later commit, the old check suite remains attached to the old SHA and cannot validate the new
HEAD. Remove and re-add `ci-full` to request one new exact-head FULL run. Unrelated labels do not run
the expensive jobs and use distinct `full-not-requested / ...` check names, so they cannot satisfy
the required FULL contexts. Integration to `main` also retains the complete gate.

Repository branch-protection/ruleset details are not readable with the current GitHub plan (the API
returns HTTP 403). The owner must ensure final acceptance requires the exact-head `full-suite`
status, while `fast-ci` remains the intermediate review signal. This repository change does not
claim to alter that external setting.

## Validation and cost reference

Accepted PR #252 run `43927313674` provides the pre-change baseline: backend `quality` ran for
3,065 seconds and `frontend-quality` for 79 seconds, or 52.40 summed runner-minutes and 51.08 minutes
of wall time. Both ran even though the change was backend/public-key only. The bounded local
no-market classifier/gate regression command completed in 1.30 seconds:

```text
python -m pytest tests/ci/test_fast_ci.py -q
```

The applicable local validation commands are:

```text
python -m pytest tests/ci/test_fast_ci.py -q
ruff format --check .
ruff check .
mypy src tests migrations scripts/ci
python -m pytest --collect-only -q
```

Local timing excludes GitHub setup/install overhead and is not presented as a GitHub runner-cost
claim. Each FAST GitHub run records its actual measured job seconds and summed runner-minutes for
later exact-head review.
