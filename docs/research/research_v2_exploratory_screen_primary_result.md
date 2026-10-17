# Research V2 exploratory screen — primary result

## Status and authority boundary

Issue #187 completed the single authorized offline historical simulation under
the frozen executable and external reviewed execution grant. The run used
saved immutable WEEX research data and fictitious capital only. It made zero
market, account, order, Paper, Shadow, Live, or deployment requests.

The durable ledger finalized all 30 ordered tasks and the existing read-only
verifier returned exit code `0`. The evidence commit that adds this document
is not the simulation executable. The exact execution identities are:

| Identity | Value |
| --- | --- |
| Executable SHA | `316744efa9c4b50aa8d999b3c4d11fd874b4c28c` |
| Executable tree | `0c6626dc913457b41221af8ac6e46ddf4e0a3c75` |
| Run UUID | `686bd4a1-ffff-4391-8562-f1e4caf7a575` |
| Grant SHA-256 | `49bda69ffd9594ad4f29e7a9d568b32d927da66ba71a961feb0ac322add9eb85` |
| Protocol SHA-256 | `ab78c022616bcffdb7af8b835064a14409e1d47bdc3e31649192de9cf0c63e1c` |
| Task-plan SHA-256 | `03376c954ca51ca50754d925189cad7be55f14249cfec29e6cbbe710a1fcfca1` |
| Data-only plan digest | `ae5e7ea532e39902f571d897237f2f57bce154f64c2c638c73c2f88bf660e2b4` |
| Source | `weex-research-v2-last-18912cf7e7f97d518a74551f` |
| Dataset | `weex-research-v2-last-90d-issue-179-combined-v2` |
| Dataset manifest SHA-256 | `18912cf7e7f97d518a74551fcab28ebec9b7c62937145a2aff54a6d713fc75bd` |
| Projection file SHA-256 | `46d7e10899f06f4d16b6ded46f8cba5c62d9d25c119d05e35ca11b5af5833667` |
| Projection integrity SHA-256 | `7566025c965ae095e90f4032297e6e7c7bb6f90f18b82f0b2a2744380a50b522` |

The data-only preflight ran from `2026-10-15T21:40:50.8239981Z` through
`2026-10-15T21:42:11.6758809Z`. The historical run executed once from
`2026-10-15T21:42:32.3545321Z` through
`2026-10-16T22:31:01.2975570Z`. The verifier ran once from
`2026-10-16T22:59:33.7926568Z` through
`2026-10-16T23:51:42.0956381Z`. All three exit codes were `0`.

The executed module commands, in order, were:

```console
python -m scripts.research.research_v2_exploratory_screen preflight --repo "$REPO"
python -m scripts.research.research_v2_exploratory_screen run --repo "$REPO" --grant "$WORK/grant.json" --ledger-root "$WORK/ledgers"
python -m scripts.research.research_v2_exploratory_screen verify --ledger "$WORK/ledgers/686bd4a1-ffff-4391-8562-f1e4caf7a575"
```

`$REPO` was the clean dedicated checkout at the executable SHA and `$WORK`
was the external run root. The run command was invoked once. The verifier was
invoked once only after ledger revision 61 recorded `FINALIZED`.

## Frozen result

| Family | Frozen classification | Full closed BASE / STRESS | Masked closed BASE / STRESS | Full mean R BASE / STRESS | Masked mean R BASE / STRESS |
| --- | --- | ---: | ---: | ---: | ---: |
| Trend Pullback | `NEGATIVE_EVIDENCE` | 217 / 217 | 55 / 55 | -0.6292766517142698859307919797915078 / -1.118097458128628609182645531407651 | -0.7319541751541450471517513160966464 / -1.371228558231011205899579681106863 |
| Breakout + Retest | `INSUFFICIENT_EVIDENCE` | 28 / 28 | 25 / 25 | 0.09212481964940152547118117117019896 / -1.545162862665948740435663309074635 | 0.1871554746409081399002719313184659 / -1.411159531974324127749481367702052 |
| Range Mean Reversion | `INSUFFICIENT_EVIDENCE` | 1 / 1 | 1 / 1 | -0.01710027773676685077458619511688668 / -0.6059243777389903123491371624635188 | -0.01710027773676685077458619511688668 / -0.6059243777389903123491371624635188 |

Trend Pullback satisfies the frozen sufficiency gate and all four full/masked
BASE/STRESS means are non-positive, hence `NEGATIVE_EVIDENCE`. Breakout +
Retest and Range Mean Reversion do not satisfy the frozen minimum population
gate; their outcomes remain `INSUFFICIENT_EVIDENCE`, including the unfavorable
STRESS observations. No family is a candidate for further validation under
this screen.

## Authoritative population and economics

Each family contains 60,480 decision observations. BASE and COMBINED_STRESS
use the identical decision/admission population. `Candidates` includes Risk-
blocked candidates; `admitted`, `filled`, and `closed` are authoritative
runner outputs. The completed inventory therefore contains the predeclared
181,440 observation slots and 362,880 scenario rows. Quote amounts are exact
Decimal strings in USDT.

| Family / scenario | Candidates | Risk blocked | Admitted | Filled | Closed | Recovery closed | Open / unresolved | Gross realized | Fees | Net realized | Closed net R | Mean / median R | R profit factor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Trend / BASE | 229 | 12 | 217 | 217 | 217 | 0 | 0 | -1166.958251808029553086262747217882 | 1940.968768426206644805847075501543 | -3107.927020234236197892109822719416 | -136.5530334219965652469818596147572 | -0.6292766517142698859307919797915078 / -1.01116355234375 | 0.4077673979617147205888286182579052 |
| Trend / STRESS | 229 | 12 | 217 | 217 | 217 | 3 | 0 | -1680.291983721038524321525981764418 | 3882.007823361009195989573233495688 | -5562.299807082047720311099215260114 | -242.6271484139124081926340803154603 | -1.118097458128628609182645531407651 / -1.293495439617758311765876965956600 | 0.2662312191992686628853250098430475 |
| Breakout / BASE | 29 | 1 | 28 | 28 | 28 | 0 | 0 | 604.2725728390732038281484705826820 | 337.5232363150151025718754633238990 | 266.7493365240581012562730072587829 | 2.579494950183242713193072792765571 | 0.09212481964940152547118117117019896 / -0.3981167034164859002169197396963124 | 1.075968008027342984848284094828864 |
| Breakout / STRESS | 29 | 1 | 28 | 28 | 28 | 2 | 0 | 44.82746493348189514176665138066175 | 675.8763928961981458102460088618546 | -631.0489279627162506684793574811930 | -43.26456015464656473219857265408977 | -1.545162862665948740435663309074635 / -1.726152359816420193778684344722080 | 0.3750600298617449631403515641768694 |
| Range / BASE | 1 | 0 | 1 | 1 | 1 | 0 | 0 | 9.690550347250107787751437623640583 | 10.11805729066927905711609250156275 | -0.427506943419171269364654877922167 | -0.01710027773676685077458619511688668 | -0.01710027773676685077458619511688668 / -0.01710027773676685077458619511688668 | 0E+35 |
| Range / STRESS | 1 | 0 | 1 | 1 | 1 | 1 | 0 | 5.135893442181514958916294084849013 | 20.28400288565627276764472314643698 | -15.14810944347475780872842906158797 | -0.6059243777389903123491371624635188 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 | 0E+34 |

There were no `ENTRY_UNFILLED`, `SAFETY_BLOCKED`, `ORDINARY_OPEN`, or
`RECOVERY_OPEN` primary rows in any family/scenario. Residual absolute base
quantity is exactly zero for every one of the ten assets in every primary
summary. Base units are retained per asset in the projection and are not
summed across instruments.

## Dependence and sufficiency diagnostics

| Family | Raw admissions | Horizon-mask admissions | Same-asset 168h overlaps | Mask assets with >=3 closures in both scenarios | Mask weeks with >=3 closures in both scenarios |
| --- | ---: | ---: | ---: | --- | --- |
| Trend Pullback | 217 | 55 | 162 | BTC, ETH, SOL, XRP, ZEC, ZAMA, BCH, BTW, AVAX, DOGE | W1–W9 |
| Breakout + Retest | 28 | 25 | 3 | ETH, SOL, XRP, ZEC, BTW, AVAX | W2, W5, W6, W8, W9 |
| Range Mean Reversion | 1 | 1 | 0 | none | none |

Masked admissions by UTC week are:

- Trend Pullback: `W1:8, W2:8, W3:4, W4:5, W5:7, W6:7, W7:4, W8:4, W9:8`.
- Breakout + Retest: `W1:2, W2:4, W3:1, W4:0, W5:3, W6:5, W7:2, W8:4, W9:4`.
- Range Mean Reversion: `W1:0, W2:0, W3:0, W4:0, W5:0, W6:0, W7:0, W8:0, W9:1`.

The complete P1–P3, asset, week, side, regime, and explicit zero-cell strata
are retained in each per-task `NN.partition.json` artifact. The corresponding
task IDs are listed in `projection.json.task_artifacts`, and the raw file
SHA-256 and size for every partition are listed in `artifact-index.json`.
`projection.json` retains family-level aggregates and selected diagnostics;
it does not contain every detailed stratum. The STRESS leave-one-asset-out
full/masked mean R pairs are preserved below because the frozen positive
screen requires every pair to remain positive:

| Asset | Trend full / masked | Breakout full / masked | Range full / masked |
| --- | ---: | ---: | ---: |
| BTCUSDT | -1.148639913421857793141679374605936 / -1.623559625988912041606764654470628 | -1.251022862576100045005118711111909 / -1.074668559761093319861527780770127 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| ETHUSDT | -0.9643409706549092583566105972466160 / -1.128954184461330449467377941545475 | -1.039818266064697238017518123536737 / -0.8186311271058622459989279375854532 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| SOLUSDT | -1.004691204976872579493030250380633 / -1.274345752164162642803412060677194 | -1.406060260637030674941684431038360 / -1.226660685714298793339947042065817 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| XRPUSDT | -1.128850106048016969297143726994674 / -1.295064259805599159689307794025348 | -1.835831075458714125630284911801665 / -1.729418744141149675512758365322096 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| ZECUSDT | -1.077432886620862300388502621056296 / -1.273799093737541291108001195471448 | -1.664840152715575660954610538522294 / -1.650173568778954721732668915814564 | unavailable / unavailable |
| ZAMAUSDT | -1.104863259467809741883923823485451 / -1.350616055799207414050378634716069 | -2.146458000948678024728979393401174 / -2.082033090430243547050871048345552 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| BCHUSDT | -1.108279680759634629546991461056916 / -1.405973566870612939674985660325406 | -1.651097783311770613417182671526443 / -1.524752845588722709325099736236479 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| BTWUSDT | -1.452223998178952944229162753561113 / -1.483024869721898154197391312916180 | -1.502361129387902416817866956313342 / -1.336718821429580784055584213808655 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| AVAXUSDT | -1.087541992855309583142774608223470 / -1.494444401243126599242373883005944 | -1.326022702584722988148341766562452 / -1.143863441333164234783954804660129 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |
| DOGEUSDT | -1.120612830835711705023920442145663 / -1.389905417608758765435452680986900 | -1.608698191838761656748095283484806 / -1.477053305181587633072376424689638 | -0.6059243777389903123491371624635188 / -0.6059243777389903123491371624635188 |

## Trend hold-only diagnostic

The hold-only branch remains a paired Trend diagnostic and does not count as
a second primary opportunity. Both scenarios have 217 branches, 212 closed,
and five unresolved. BASE reports fees
`1911.750169176767973412385187683044`, mean R
`-1.373646573889703569628635874639875`, median R
`-1.384598051379765135566997235397064`, and R profit factor
`0.04715573158451359680296050294502491`. STRESS reports fees
`3824.259037600666607431213781220636`, mean R
`-1.754986064359902964537867767526106`, median R
`-1.713274812049028677150786308973173`, and R profit factor
`0.06034889720264385660131127552807125`.

The five unresolved branches leave the same per-asset absolute base quantities
in BASE and STRESS: BTCUSDT
`0.10947500751783555945913955292`, XRPUSDT
`17755.377646621171718940007315`, and AVAXUSDT
`221.2389380530973451327433628`; each of the other seven assets is exactly
zero. These quantities are sums of independent hypothetical residual
exposures within the hold-only diagnostic branch. They do not represent a
common funded account or a market-value estimate, and base units of different
assets must not be summed. The unresolved hold-only branches do not affect
EMA50 primary sufficiency or the Trend Pullback classification.

## Immutable evidence delivery

The evidence directory is
`data/research_evidence/issue-187-exploratory-screen-316744efa9c4b50a`.
Its compact `artifact-index.json` is 26,533 bytes with SHA-256
`677a20834d41ba045d20235b749daebd53338e4694ec59c1bb094320344119f2`.
The index covers 159 source evidence files totaling 625,767,111 bytes. Large
ledger artifacts use the repository's Git LFS handling and retain the raw
file SHA-256 and size in the index. The directory includes:

- every append-only ledger revision through `FINALIZED` revision 61;
- all 30 compressed population files, all-fill authority streams, and
  partitions;
- the exact final projection;
- the reviewed grant, environment record, preflight output, run output, and
  verifier output;
- a typed execution record containing timestamps, exit codes, and identities.

The final run output and verifier output are byte-identical after capture
(SHA-256 `50565d7265501447d60633fe6ff85c7019ce1952f777eb364622a94813c37a17`),
showing that the existing offline verifier reproduced the recorded projection.
This equality is not an independent implementation, data replication, or
second research run.

## Interpretation limits

This is a retrospective, survivor-selected, normalized-base, unleveraged,
pre-funding exploratory screen. It is not an untouched test, portfolio equity
curve, historical contract/margin/funding/liquidation validation, proof of
future profitability, or trading authority. Dependence remains explicit;
masked observations are not asserted independent. No parameters, families,
assets, windows, costs, or classifications were changed after observing the
result. The evidence does not expand runtime/Paper/Shadow/Live eligibility.
