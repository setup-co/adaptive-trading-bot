# History-screened 196-day LAST continuation

`HISTORY_SCREENED_196D_LAST_CONTINUATION_V1` is the bounded Issue #216 successor to the immutable, route-stopped Issue #214 capture. It imports the 1,648 successful parent responses and permits only the failed `ZAMAUSDT-0068` shape plus the 1,784 shapes never attempted by the parent.

The continuation preserves the original ten-member panel and `[2026-04-01, 2026-10-14)` calendar. Valid HTTP-200 partial or empty arrays are retained as explicit response gaps and never retried. Timeout, HTTP 5xx and specifically identified DNS resolution failures receive at most one retry; an exhausted DNS failure or three consecutive exhausted transport/server failures stop the route. Malformed, off-grid, duplicate, policy, denied, ambiguous or provenance failures stop immediately.

Offline build and verification preserve capture status separately from artifact integrity. Complete and partial generations both produce canonical 5m plus complete-bucket 15m/1h/4h Parquet, explicit gaps/epochs, exact signed daily comparisons, warm-up boundaries, a report and a reproducible manifest. A verifier pass is not proof of complete coverage, capture exit success, strategy quality, runtime eligibility or trading readiness.
