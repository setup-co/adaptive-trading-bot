# Issue #195 — Research V2 factorial final evidence

Status: `FINALIZED_RUN_VERIFIED_READ_ONLY`.

This directory publishes the completed offline Stage A + Stage B factorial evidence for the one accepted run. It is descriptive conditional simulation, not independent replication, strategy promotion, real-world latency measurement, or authorization for trading.

## Frozen authority

- Executable SHA: `7711a871bdf005c72a9033690527fc6920eea737`
- Executable tree: `39f67f44ecbddce8279becf457c2046196dd83f2`
- Run UUID: `01df2c66-92ce-459f-aede-78eaa82a3f52`
- Protocol SHA-256: `470f4ba70d967c62b0ec5e777d876a926a8f204c471b5a28a6b12e33cb05160c`
- Input-plan SHA-256: `aa1df44692d8cad8aa966c2e23c0c7d3772de8a829451c8a76f0bee89f83c792`
- Admitted-inventory SHA-256: `f9643e5f84ed131c9ee7e704e36cd00c2b0c413876cffd297988f3a4730c5968`
- Dataset manifest SHA-256: `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd`
- Original grant file SHA-256: `bd1ed263af54224b2c71cb8cc9aa43b620b2d101a6843033b241cd5834cb82fa`
- Stage-B authorization file SHA-256: `7e1374b0719f533898cccc2563e7700800a53f00f6c0d0b676ce3d0b24594b95`
- Accepted Stage-A snapshot: `data/research_evidence/issue-193-factorial-stage-a-7711a871/`; its files are a byte-identical prefix of this final append-only ledger.

## Execution and recovery record

Two host launches failed before project continuation logic and before any ledger mutation. Their stderr bytes remain published: missing application import SHA-256 `2ffd128e0ef2879e47378815afff8a8eb2f425b76231d1f9e3fb8d720ac301cd`, then missing `pyarrow` SHA-256 `2a2ccde9d20b8690d48f6fba6fa40260c0e962427b5826fe88c8a89e1cb00c0f`. They are not simulation-unit retries.

The bounded amendment reused the successful Stage-A virtual environment without installing or changing packages. Python 3.12.14, `pyarrow` 25.0.1, import checks, `pip check`, package inventory, and CLI help all passed; module origins resolved to the frozen checkout. Environment diagnosis SHA-256 is `caa793aa24248a1366e7cf19204a4540d9e74e22a5e2c8fb4060bbeed5b7a7cd`.

- Stage B: one successful conditional launch, exit 0, 2,543.470695 wall seconds.
- Finalize: one invocation, exit 0, 1,400.0822294 wall seconds.
- Read-only final verifier: one invocation, exit 0, 1,404.8080569 wall seconds.
- Network, exchange-account, order, trading, and deployment activity: none.
- Peak CPU/memory measurements: unavailable. Periodic working-set observations during finalize/verify were below 3.42 GiB and are not a measured peak.

## Coverage and authority reconciliation

The final ledger has 124 events through revision 123 `FINALIZED`, 60 completed task/cell units, zero failed or pending units, and 60 immutable new-result artifacts. The final projection is 2,601,215 bytes, file SHA-256 `68c3c1e72eecb21fe4cf129e1e731aabcc96a8dc7bb5015877b6c4b1466382a4`, and internal SHA-256 `9e1c0747ccbe3bba5d0b38dba0af89378d77cafd41fa08dc6f36100ae9b907d9`.

- 246 original admissions: 48 Stage A and 198 Stage B.
- 492 newly simulated off-diagonal outcomes: 96 Stage A and 396 Stage B.
- 492 reused diagonal records.
- 984 primary decision-cell records and 246 complete quadruples; these are not 984 independent trades.
- 969 `ORDINARY_CLOSED` and 15 `RECOVERY_CLOSED` primary records.
- 81 original-mask complete quadruples.
- Seven zero-admission Range tasks are retained explicitly through fourteen empty Stage-B unit artifacts; the two Stage-A Range tasks are also explicit zero groups.

The separate Trend hold-only audit contains 868 records: 839 closed, 9 recovery-closed, and 20 unresolved cell branches belonging to five decisions. Those unresolved branches retain their actual per-branch base quantities in `projection.json`: two BTC decisions, two XRP decisions, and one AVAX decision. This does not contradict the zero residual quantity of every primary all-fill record.

## Family totals

Totals below are exact stored net-R strings. Each cell denominator is the family's admission denominator.

| Family / population | Denominator | C0L0 | C1L0 | C0L1 | C1L1 |
|---|---:|---:|---:|---:|---:|
| Trend / full | 217 | -136.55303342199656524698185961475673356 | -245.97468347822917138261584069486552952 | -132.64077486694361334527425417005264892 | -242.62714841391240819263408031545991034 |
| Trend / original mask | 55 | -40.25747963347797759334632238531554138 | -71.5785020793520298671298260236316302 | -44.09705690699930340598088753729703952 | -75.41757070270561632447688246087747384 |
| Breakout / full | 28 | 2.5794949501832427131930727927655609 | -20.6799835906877319720054031368693256 | -18.0214066716982983789824557030793202 | -43.2645601546465647321985726540897790 |
| Breakout / original mask | 25 | 4.6788868660227034975067982829616369 | -15.1200344746526640987022357160548456 | -13.4947904648049160260412792324910842 | -35.2789882993581031937370341925513160 |
| Range / full and original mask | 1 | -0.01710027773676685077458619511688668 | -0.5432389972418082940679402146168848 | -0.0785403565731992882646233835924186 | -0.6059243777389903123491371624635188 |

## Stored factor contrasts

| Family / population | Complete closed quadruples | Cost C1−C0 at L0 | Cost C1−C0 at L1 | Eligibility L1−L0 at C0 | Eligibility L1−L0 at C1 | Interaction |
|---|---:|---:|---:|---:|---:|---:|
| Trend / full | 217 | -0.50424723528217790845914276995441841456221198156682 | -0.50684964768188384722285634168390443050691244239631 | 0.018028841267525123971002790067760758709677419354839 | 0.015426428867819185207289218338274742764976958525346 | -0.0026024123997059387637135717294860159447004608294931 |
| Trend / original mask | 55 | -0.56947313537952822315970006615120161490909090909091 | -0.56946388719466023488174536224691698763636363636364 | -0.069810495882205923866083002763299966181818181818182 | -0.069801247697337935588128298859015338909090909090909 | 0.0000092481848679882779547039042846272727272727272727273 |
| Breakout / full | 28 | -0.83069566217396338161423128320124594642857142857143 | -0.90154119581958094118628989110751638571428571428571 | -0.73574648649576932472055458913731718214285714285714 | -0.80659202014138688429261319704358762142857142857143 | -0.070845533645617559572058607906270439285714285714286 |
| Breakout / original mask | 25 | -0.7919568536270147038483613599606593 | -0.871367913382127486707830198402409272 | -0.726947093233104780941923100618108844 | -0.806358152988217563801391939059858816 | -0.079411059755112782859468838441749972 |
| Range / full and original mask | 1 | -0.52613871950504144329335401949999812 | -0.5273840211657910240845137788711002 | -0.06144007883643243749003718847553192 | -0.0626853804971820182811969478466340 | -0.00124530166074958079115975937110208 |

All contrast values are descriptive means over complete closed quadruples. Closure-only subsets, overlapping/cross-asset outcomes, and fixed recorded paths are dependent; they do not establish real-world causal effects. The accepted original research classifications remain unchanged: Trend `NEGATIVE_EVIDENCE`, Breakout `INSUFFICIENT_EVIDENCE`, and Range `INSUFFICIENT_EVIDENCE`.

The read-only verifier reconstructed and matched the saved projection, including all authorities, source/cutoff bindings, original diagonal imports, new off-diagonal outcomes, recovery accounting, grouped reports, unavailable handling, and zero groups. It is validation of the same run, not an independent repetition.
