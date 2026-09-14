# Binance public CCXT initialization diagnostic

## Conclusion

The Binance initialization failure is classified as **proxy/firewall (network access policy)**
with high confidence. DNS and TCP connection establishment succeeded, but the TLS handshake to
the exact public Binance REST host was forcibly reset before any HTTP response existed. A direct
standard-library HTTPS request failed at the same boundary as CCXT. This is not evidence of a
project-code or CCXT schema/compatibility defect.

The diagnostic cannot distinguish whether the reset is enforced by an upstream provider or an
intermediate network policy device. The actionable prerequisite for a future study is a
legitimately permitted execution environment in which the normal `api.binance.com` TLS handshake
and public `/api/v3/exchangeInfo` request succeed. No VPN, proxy bypass, alternate Binance domain,
credentials, or replacement research source should be used. Any future study must be separately
predeclared after review.

The current environment is **not suitable** for a fresh Binance study rerun.

## Scope and provenance

- Git SHA: `8de848253a75bc358cb99d6027c6faa839e1ca78`
- Diagnostic interval: `2026-09-14T18:06:21Z` through `2026-09-14T18:09:01Z`
- Runtime: Windows 11 `10.0.26200`, AMD64
- Python: `3.12.14` (`MSC v.1944`, 64 bit)
- OpenSSL: `3.5.8 25 Aug 2026`
- CCXT: `4.5.78`
- Project request settings: `enableRateLimit=true`, `timeout=10000` milliseconds
- Credentials/private APIs: not used
- Proxy environment variables checked: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`
  and lowercase equivalents; all were absent

Installed CCXT configured Binance `load_markets()` for spot, linear, and inverse markets. The
corresponding normal public REST hosts were `api.binance.com`, `fapi.binance.com`, and
`dapi.binance.com`. Execution stopped on the first actual request,
`GET https://api.binance.com/api/v3/exchangeInfo`; the other hosts were not contacted.

## DNS

DNS resolution completed without an exception for all three configured market hosts at
`2026-09-14T18:06:46Z`:

| Host | Success | Families | Unique addresses | Resolved address |
|---|---|---|---:|---|
| `api.binance.com` | yes | `AF_INET` | 1 | `13.225.39.94` |
| `fapi.binance.com` | yes | `AF_INET` | 1 | `10.10.34.34` |
| `dapi.binance.com` | yes | `AF_INET` | 1 | `10.10.34.34` |

The private address returned for the derivatives hosts is consistent with environment/network
DNS policy, but it did not cause the observed spot failure because CCXT failed first against the
publicly resolved `api.binance.com` address.

## Public HTTPS reachability

A minimal unauthenticated `GET` to the exact CCXT spot market-discovery endpoint was attempted at
`2026-09-14T18:07:04Z` with a ten-second timeout:

- URL: `https://api.binance.com/api/v3/exchangeInfo`
- Result: failure before HTTP
- Exception: `urllib.error.URLError`
- Cause message: `[WinError 10054] An existing connection was forcibly closed by the remote host`
- HTTP status: unavailable
- Response content type: unavailable
- Response excerpt: unavailable because no HTTP response was received

A stage-specific probe at `2026-09-14T18:08:42Z` established TCP successfully to port 443. The
subsequent default-certificate-verifying TLS handshake failed with
`builtins.ConnectionResetError` and the same `WinError 10054`. This rules out DNS failure, TCP
connection failure, request timeout, HTTP status handling, and certificate verification as the
observed failure stage.

## Exact CCXT `load_markets()` exception chain

The public-only Binance instance used the exact project configuration. Its sanitized complete
cause/context chain at `2026-09-14T18:07:37Z` was:

1. raised — `ccxt.base.errors.NetworkError`
   - `binance GET https://api.binance.com/api/v3/exchangeInfo`
2. cause — `requests.exceptions.ConnectionError`
   - `('Connection aborted.', ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host', None, 10054, None))`
3. context — `urllib3.exceptions.ProtocolError`
   - `('Connection aborted.', ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host', None, 10054, None))`
4. context — `builtins.ConnectionResetError`
   - `[WinError 10054] An existing connection was forcibly closed by the remote host`

The complete raw Python traceback remains outside Git. It is 9,417 bytes with SHA-256
`edd5957cf1de282511d132ddebaf691be66c516a6e9feb75f60b704e8af93c3e`.

## Public connectivity control

Kraken was used once as a harmless public connectivity control only. It was configured with the
same `enableRateLimit=true` and `timeout=10000` settings and was not used as a research source.

At `2026-09-14T18:08:03Z`, Kraken's public `load_markets()` reached
`https://api.kraken.com/0/public/Assets` and received HTTP `403 Forbidden` with a short
`Access Denied; restricted country` response. Response identifiers, the observed client address,
and the full HTML body were discarded and are not committed.

This control did not initialize successfully, but receiving an HTTP response proves that general
DNS, outbound TCP, TLS certificate validation, HTTPS, and CCXT request handling function in this
environment. It also independently indicates location/access-policy restrictions on public
exchange endpoints. Kraken was not considered or used as an alternate study vendor.

## Classification matrix

| Candidate category | Finding |
|---|---|
| DNS | ruled out for the actual Binance spot host |
| TCP/connectivity | TCP connect succeeded; failure occurs later |
| TLS/certificate | handshake was reset, not rejected for certificate validation |
| Proxy/firewall | **selected: network/access policy is consistent with all observations** |
| Timeout | ruled out; reset occurred immediately |
| HTTP access restriction/status | no Binance HTTP response existed; Kraken returned explicit 403 restriction |
| CCXT compatibility/schema | ruled out by matching direct HTTPS failure before response parsing |
| UNKNOWN | not selected |

Confidence is high for the selected network-policy/access-control category and lower for the
unobservable question of exactly which network or upstream boundary enforces it.

## Code and research disposition

No project-code change is required or proposed from this evidence. The adapter's generic wrapper
accurately fails closed; direct HTTPS reproduces the underlying reset independently of project
logic.

No historical import, EMA50 study, corpus, robustness report, strategy evaluation, Paper/Live
action, Experience activation, or deployment occurred. No research result was produced.
