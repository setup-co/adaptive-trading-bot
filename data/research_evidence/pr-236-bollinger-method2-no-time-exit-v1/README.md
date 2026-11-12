# PR #236 paired hourly no-time-exit evidence

This directory retains the one authorized post-result paired diagnosis. It used frozen executable
`a392c72ac12981734717027fdf5bdb7cf393178a`, protocol
`a2e9d441a9da1b135154d82add231464e44e36926a0dc610e8be7ce3e46ecc9e` and run UUID
`2de5c8b3-afc5-4654-bad9-d5856df3f7f5`.

The immutable hourly 30-minute parent was not rerun. Its executable is
`706c7818573c566e9a6a78e542887e7916c03612`, run UUID
`63964675-0ab4-4b46-89c3-1b034b69dfbd`, projection
`6ecc6eae280738dd800bc2076116aa4d54a79ab0109c53eddf41f612c69ac416` and publication
manifest `190dd9eafdba3b924a560769b3089c8e4b8ab77a8b9566801dab4d2aa1348f5a`.

## Run facts

- Exit: `0`; elapsed: `57.7439625` seconds under a 600-second total ceiling.
- Owned process tree: four observed PIDs; peak combined working set `413,777,920` bytes under
  the 1-GiB ceiling; no timeout or memory termination.
- Exact paired rows: 5,152 = 2,576 parent admissions × BASE/STRESS.
- Parent admission counts were reproduced exactly: BTC 998 PRICE / 274 MFI; ETH 990 PRICE / 314
  MFI. Entry identity, entry fill, quantity, approved risk, frozen stop and target were exact.
- Treatment natural outcomes were 4,970 closes and 182 data-end-censored scenario rows. Endpoint
  valuations are diagnostic and explicitly include hypothetical exit fees; they are not realized
  closes.

## Terminal-valued paired economics

`Parent mean R` is the saved 30-minute comparator. Treatment values include diagnostic endpoint
valuation for the explicitly open rows shown; natural-close-only views remain in `projection.json`.

| Asset | Scenario | Arm | N | Parent mean R | Treatment mean R | PF | Net quote | Fees | Natural closed | Open |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC | BASE | PRICE | 998 | -0.174348 | -0.203704 | 0.737570 | -5,071.70 | 3,193.69 | 965 | 33 |
| BTC | BASE | MFI | 274 | -0.139973 | -0.129982 | 0.820557 | -887.45 | 649.44 | 259 | 15 |
| BTC | STRESS | PRICE | 998 | -0.340859 | -0.375494 | 0.576892 | -9,352.86 | 6,387.44 | 965 | 33 |
| BTC | STRESS | MFI | 274 | -0.261586 | -0.256344 | 0.682213 | -1,751.71 | 1,298.91 | 259 | 15 |
| ETH | BASE | PRICE | 990 | -0.134503 | +0.005236 | 1.007610 | +129.58 | 2,524.63 | 960 | 30 |
| ETH | BASE | MFI | 314 | -0.078762 | +0.051890 | 1.078875 | +407.34 | 513.58 | 301 | 13 |
| ETH | STRESS | PRICE | 990 | -0.268590 | -0.134964 | 0.825290 | -3,340.36 | 5,049.24 | 960 | 30 |
| ETH | STRESS | MFI | 314 | -0.168083 | -0.037271 | 0.947662 | -292.58 | 1,027.16 | 301 | 13 |

The mixed result is `NO_PROMOTION`: both BTC arms remain negative under both scenarios, and both
ETH arms remain negative under STRESS. Positive ETH BASE endpoint-valued results do not establish
robust profitability, and 86/2,608 ETH plus 96/2,544 BTC scenario rows still depend on endpoint
valuation. The treatment also creates large overlapping exposure: peak concurrency ranges from 20
to 49, and median upper holding duration ranges from 102,000 to 193,500 seconds.

`paired-rows.jsonl.gz` is a deterministic gzip publication of the complete uncompressed output
(`15,234,067` bytes, SHA-256
`f961e8e7e93143ecf2d1d382a96d959f05bf6df32ccb7f642c3d2d97efd56199`). The publication
manifest binds every retained file. Funding remains not modeled; 5m OHLC cannot prove intrabar
tick order or venue execution. Nothing here changes runtime eligibility or authorizes trading.
