# WEEX Demo credential-present execution (#134)

**Execution date:** 2026-09-22

**Baseline and producing code:** `3840ecfbd0a99da2d58462d3135f389201ee7c07`

**Final result:** `BLOCKED BEFORE STATE CHANGE`

## Frozen execution result

The three dedicated `ATB_WEEX_DEMO_*` runtime variables were present and
non-empty. Their values were not printed, logged, serialized, copied into an
artifact, or committed. Exactly one credential-present execution of the frozen
Issue #132 protocol was attempted for Issue #134.

The signed read-only Demo preflight established one balance response, zero open
positions, and zero records in the bounded order-history query. The reviewed
public V3 boundary did not provide a directly qualified current MARK or
contract facts for `BTCSUSDT`. Production `BTCUSDT` facts were not substituted
and no Demo-to-production equivalence was claimed. The runner therefore stopped
before transmitting any POST or other state-changing request.

The immutable result contains exactly:

```text
DEMO_CONTRACT_FACTS_UNAVAILABLE
DEMO_MARK_UNAVAILABLE
DEMO_CALIBRATION_BLOCKED_NO_MARK
DEMO_LONG_CALIBRATION_BLOCKED
```

No opening order was submitted, no position was created or observed, no
comparison was eligible, and cleanup was not applicable. Consequently there
are no mechanics comparison dispositions or Decimal deltas to report. This is
the final outcome of the one authorized attempt; it was not patched, retried,
or rerun.

## Locked evidence

This evidence is separate from and does not modify the locked LAST, MARK,
funding, #130 forward-evidence, or #132 credential-unavailable artifacts.

```text
schema = weex-demo-long-calibration-v1
protocol = issue-132-long-v1
bundle_version = weex-demo-calibration-d7eb4338f33b986cc54b1cb6
captured_at = 2026-09-21T22:49:40.126996Z
completed_at = 2026-09-21T22:49:49.371646Z
source_code_commit = 3840ecfbd0a99da2d58462d3135f389201ee7c07
bundle.json bytes = 2127
bundle.json sha256 = dab84b58a26ca792dc132d6116bc6ad6094ef7e0cd520b10231cead3c93e1d4f
manifest.json sha256 = 6119d7a1b7fca8624dfdb6c74b643fc46521e1e6badbd9f4b1e2d82ec8fd20f2
```

The bundle retains only sanitized hashes and counts for the three approved
read-only Demo GET observations. It contains no API key, secret, passphrase,
signature, authentication timestamp, raw account identifier, or venue order
identifier. The network-free locked reader checks the reviewed version,
manifest hash, artifact size/hash, provenance, and bundle integrity seal.

## Preserved authorities and remaining blocker

All pre-existing immutable artifact bytes and identities remain unchanged.
The direct current `BTCSUSDT` MARK precondition remains unavailable through the
reviewed public V3 market boundary. Under the frozen protocol this is
`DEMO_CALIBRATION_BLOCKED_NO_MARK`; it is not authority to substitute another
symbol, modify venue settings, retry the order, or infer leveraged mechanics.
Historical MMR/liquidation replay and representative leveraged research remain
unqualified, and Issue #118 remains permanently stopped.
