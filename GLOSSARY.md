# Glossary

Plain-language definitions of the load-bearing terms used across this public
BDBV evidence package. The definitions describe the public methodology and
artifact shapes only; they do not describe private model internals.

| Term | Meaning |
|---|---|
| LOVS | The name of this public artifact repository (the "LOVS Public Artifact Repository"). Its public surface is a set of read-only files, not the private implementation. |
| Public snapshot | A dated, source-attributed package of public counts and review metadata (`data/public_snapshot.json`). It is a published evidence state, not a live operational feed. |
| `as_of` | The publication timestamp of the snapshot: when this package was assembled and published. |
| `data_as_of` | The latest data date the headline counts represent. It can be earlier than `as_of` because public reporting is asynchronous. |
| Source clock | The distinct dates a single source carries: `data_as_of` (what the data represents), `published_at` (when it was released), and `retrieved_at` (when this repository captured it). Keeping them separate prevents false disagreements. |
| Publication lag / visibility lag | Days between a source's `data_as_of` and `published_at` (publication lag) or between `data_as_of` and `retrieved_at` (total visibility lag). Measured only when a source exposes enough dates. |
| Attribution lag | When national or country-scope headline totals are newer than the latest health-zone table, the difference is recorded as attribution lag rather than spread across zones. |
| Source-attributed sum | The total obtained by summing the per-zone source rows. It is compared against the headline total to surface attribution lag. |
| Public range (conflict range) | The min-to-max span of values different public sources report for the same metric. It is an evidence state, not a confidence interval and not a model output. |
| Primary source id | The source ID whose value is used as the headline for a metric. |
| Conflict anchor | A source ID retained because it reports a materially different value for the same metric; it preserves the disagreement instead of hiding it. |
| Blindspot | A tracked evidence gap (for example restricted publisher bytes, missing data dates, attribution lag, or open calibration). It is recorded openly to prevent false precision. |
| Calibration commitment | A pre-registered public question with a resolution date. It stays `open` until citable public authority evidence supports resolution, then becomes `resolved`. |
| Commitment hash | A SHA-256 over a calibration row's public payload (excluding the hash itself), so a row can be checked for stability across releases. |
| Precommitment target | A target registered before outcomes are known, published with a public role (such as a watch or control role) but without probabilities. |
| Nowcast boundary | The rule that this package may publish nowcast readiness and shape but not point estimates, predictive intervals, or model parameters. |
| Source tier | A public category describing the kind of source (for example authority or aggregator) used during review. |

For the full reasoning discipline behind these terms see `METHODOLOGY_PUBLIC.md`
and `METHOD_CARDS_PUBLIC.md`. For where each artifact lives see
`READONLY_INTERFACE_PUBLIC.md`.
