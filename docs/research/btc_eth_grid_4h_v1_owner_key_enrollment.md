# BTC/ETH Grid historical approval: owner-key enrollment

Status: **PREPARATION ONLY / REAL HISTORY STILL BLOCKED**

This procedure prepares an owner-controlled Ed25519 trust root for a possible later Grid historical
study. It does not issue a grant, signature, run UUID, or historical-run authorization. The accepted
runner keeps `ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX = None`; no production key is stored in this
repository.

## Security boundary

The owner generates and retains the private key on an offline owner-controlled machine. The private
key, its passphrase, backups and decrypted bytes must never be supplied to Codex, Git, GitHub,
issues, pull requests, chat, logs, CI, ordinary stdout, fixtures or project artifacts. Only the
32-byte raw public key as 64 lowercase hexadecimal characters and its key ID may leave the owner's
offline boundary.

The key ID is exactly:

```text
SHA256(raw 32-byte Ed25519 public key)
```

The helper has no network code. It uses the existing `cryptography` dependency, writes an encrypted
PKCS#8 PEM with exclusive creation, refuses overwrite and refuses any private-key path inside the
verified Git checkout root. Its passphrase is requested through a hidden prompt twice; there is no passphrase
command-line argument. A passphrase should be unique and high entropy.

On Windows, the `0600` creation mode is not an ACL guarantee. Before generation, the owner must
choose an offline encrypted volume or secure key store and explicitly verify that the destination
directory and resulting private-key file ACL grant access only to the intended owner. On Unix-like
systems, verify owner-only file and directory permissions as well.

## Owner-only offline operation

These commands are documentation for the owner. They were not executed for this preparation PR.
Run them from an independently reviewed checkout on the offline machine:

```powershell
python -m adaptive_trading_bot.research.grid_approval_enrollment generate `
  --repo C:\path\to\reviewed-checkout `
  --private-key E:\owner-secure-offline\grid-owner-key.pem `
  --public-info E:\owner-secure-offline\grid-owner-public.json
```

The command prints only the canonical public-info JSON and writes the same public record to the
requested public-info file. It never prints the private key or passphrase. The public record schema
is `grid-history-ed25519-public-info-v1` and contains `algorithm`, `public_key_hex` and
`key_id_sha256`.

An independent reviewer can recompute the public fingerprint without the private key:

```powershell
python -m adaptive_trading_bot.research.grid_approval_enrollment public-info `
  --public-key-hex <64-lowercase-hex-characters>

python -m adaptive_trading_bot.research.grid_approval_enrollment verify-public-info `
  --public-key-hex <64-lowercase-hex-characters> `
  --expected-key-id <64-lowercase-sha256-hex-characters>
```

Generation is deliberately one-way and non-overwriting. Keep at least one separately encrypted,
offline backup under owner control and test the recovery procedure without exposing key material.
If the private key or passphrase is lost, no replacement signature can be made; enrollment must be
replaced by a new independently reviewed public-key pin. If compromise is suspected, stop all grant
work, disclose only the public key ID being revoked, remove trust through a reviewed fail-closed
commit, and enroll a new owner-generated key. Never reuse a compromised key.

Stage A intentionally provides no grant-signing subcommand. Signing exact canonical grant bytes is
reserved for the later separately authorized action after the public key has been reviewed and the
new pinned executable identity has been frozen.

## Two-stage checkpoint

### Stage A — this issue

- Provide the auditable offline generation and public-fingerprint helper.
- Exercise key generation and detached-signature compatibility only with ephemeral test keys.
- Keep the trusted production public key unset (`None`).
- Do not create or sign a historical grant and do not process retained history.

Acceptance of Stage A confirms tooling behavior only. It does not establish an owner trust root.

### Stage B — separate owner-approved action

1. The owner generates the private key locally and supplies **only** the raw public-key hex and key
   ID through the explicitly approved channel.
2. An independent reviewer recomputes `SHA256(raw public-key bytes)` and confirms the exact public
   value and fingerprint with the owner.
3. A separate PR replaces `ARCHITECT_APPROVAL_ED25519_PUBLIC_KEY_HEX = None` with that exact reviewed
   public hex. No private material enters the PR.
4. Because the pin changes source bytes, reviewers record and approve the new executable commit and
   tree after exact-head CI. PR #248's executable identity cannot be used for a later grant.
5. Only after that freeze may a separate action create one canonical grant naming the newly pinned
   executable SHA/tree and one new UUID. The owner signs the exact immutable canonical grant bytes
   offline. The detached approval must use the existing `GridGrantApproval` schema and matching key
   ID.
6. Historical execution still requires separate explicit authorization. A valid signature alone is
   not run authorization and does not bypass clean-checkout/imported-code verification.

No trust-on-first-use, environment-variable key, unsigned override, CI-secret shortcut, alternate
verifier or self-asserted grant is permitted.

