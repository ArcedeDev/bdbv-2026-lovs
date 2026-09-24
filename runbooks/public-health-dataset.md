# Runbook: public health dataset

The dataset in `deliverables/public-health-dataset/` is written by `export_public_health_dataset.py` and checked by `python3 -m lovs.snapshot_contract --check-dataset`, which `release_snapshot.py --check` runs. Each row starts from the message a maintainer sees. Never change a value to make a check pass: every fix below changes a label, a classification or a regeneration. When a source itself prints two different figures for one thing, stop and ask the dataset owner before choosing one.

## Export

| Message | Check | Fix |
|---|---|---|
| "source field 'X' names a case class but is not in the public dataset's metric vocabulary" (prefixed with the source id) | Read field X in the newest reviewed promotion and in `data/bundibugyo-2026/manifest.json`. | Classify X in `export_public_health_dataset.py`: `CUMULATIVE_METRIC_BY_FIELD` for a running total, `DAILY_METRIC_BY_FIELD` for a 24-hour count, `CASELOAD_METRIC_BY_FIELD` for a count on the report date, or `OWN_NAME_CASE_FIELDS` to keep its own name. A new two-country total also goes in `COUNTRY_SCOPE_TOTAL_KEYS`. A new metric name goes in `CUMULATIVE_SOURCE_METRICS` or `DAILY_SOURCE_METRICS` in `lovs/snapshot_contract.py` too. If the field's name uses a country or province word the dataset does not know, add the word to `_COUNTRY_BY_FIELD_TOKEN` there. Add a test, then rerun the release check. |

## Dataset gate (`--check-dataset`)

| Message | Check | Fix |
|---|---|---|
| "a source gives each cumulative series one value" | The message names both rows. A cumulative metric at `COD; UGA` is the same series as its `country_scope_` metric. | Usually a DRC term and a two-country term are filed together: fix the field's geography (`_COUNTRY_BY_FIELD_TOKEN`, `COUNTRY_SCOPE_TOTAL_KEYS`) or add a reviewed label with the source's evidence to `REVIEWED_SOURCE_FIELD_LABELS` in `lovs/snapshot_contract.py`. |
| "a source gives each 24-hour series one value" | Two fields of one source feed one 24-hour metric with different values (for example `new_confirmed_deaths_24h` and `total_confirmed_deaths_24h`). | Decide from the source which field is the day's count; move the other out of `DAILY_METRIC_BY_FIELD` so it keeps its own name. |
| "is an unqualified ... from a source that also reports Uganda's ...; record whether it is the DRC figure ('COD') or the two-country figure ('COD; UGA')" | Read the source: does the figure include Uganda? | Add a `REVIEWED_SOURCE_FIELD_LABELS` entry with `COD` or `COD; UGA` and a comment quoting the evidence. |
| "the same value as the source's DRC-named term, while the source also reports Uganda figures" | Is the figure DRC-only, or a two-country total that equals the DRC figure because Uganda's share is zero? | Add a reviewed label: `COD` for a DRC figure, `COD; UGA` for a two-country total, with the evidence. |
| "but its reviewed label in REVIEWED_SOURCE_FIELD_LABELS is" | The export did not apply a reviewed label, or the label no longer fits. | Regenerate the dataset. If the source changed, update the label and its evidence. |
| "is the two-country metric ... it must sit at 'COD; UGA'" or "location=...; a country-scope row must be ..." | A `country_scope_` metric or field is labelled with one country. | Fix the field's classification: a DRC or Uganda term is not a country-scope total. |
| "... but the contract's ... term is ..." or "lacks the contract's country-scope composition rows" | The latest SitRep's total, DRC and Uganda terms disagree with `data/snapshot_contract.json`. | Regenerate the snapshot contract and the dataset from the same promotion; if they still disagree, the promotion needs review. |
| "is exported as the cumulative ..., but a cumulative series takes only a top-level ... count field" | A nested, increment, caseload, rate or other-class field joined a cumulative series. | Move the field out of `CUMULATIVE_METRIC_BY_FIELD`. |
| "is a percentage field but has unit" or "is a death source metric but exported as" | A unit or metric contradicts the field's own name. | Add the field to `UNIT_BY_FIELD`, or classify it under a death metric. |
| "timeline.csv ... has ... but reported_counts.csv has ..." | The two files were written by different exports. | Regenerate both with one export. |
| "package manifest records <input> as sha256 ... but the file is ..." or "package manifest input ... is not in the repo" | An input changed after the export, or was left out of the commit. `data/live-bdbv-2026-output.json` carries a wall-clock date, so a release check run on a later day rewrites it. | Commit the regenerated input together with the dataset, or restore the committed input and rerun `python3 export_public_health_dataset.py` and `python3 -m lovs.public_exports` before committing. |
| "lovs-public-health-dataset.manifest.json is missing" | The export did not finish. | Rerun `python3 export_public_health_dataset.py`. |

## Versions

Keep the package manifest's `schema_version` at 2: `lovs/semantic_freshness_gate.py` enforces its per-artifact checks only for that value. Version the dataset's content through `lovs-public-health-dataset.schema.json`, and describe every vocabulary change in `CHANGELOG_MD` (`lovs/public_exports.py`) with counts recomputed at the final regeneration.
