# Worked Snapshot Review

This walkthrough uses the current public files in this repository. It is a real review of the 28 May 2026 public artifact, with data represented through 26 May 2026. It is not a synthetic example and it is not a new outbreak update.

Use it as a pattern for reviewing a future public snapshot or a partner-local aggregate package.

## Inputs

Start with:

- `data/public_snapshot.json`
- `data/public_reported_counts.csv`
- `data/public_source_index.csv`
- `data/public_source_conflicts.json`
- `data/public_zone_counts_2026-05-26.csv`
- `data/public_blindspots.json`
- `data/public_latency_observatory.csv`
- `data/public_calibration_status.json`

## Step 1: Identify The Snapshot Clock

From `data/public_snapshot.json`:

- `as_of`: `2026-05-28T23:59:59Z`
- `data_as_of`: `2026-05-26`

Interpretation: this is a 28 May publication package representing public data through 26 May. Do not compare it to later sources as if they were already included in this release.

## Step 2: Read Headline Counts As Public Claims

From `data/public_snapshot.json`:

| Metric | Primary | Public range | Primary source ID |
|---|---:|---:|---|
| Confirmed cases | 128 | 10 to 128 | `ecdc-bdbv-drc-uga-2026-05-27` |
| Suspected or reported cases | 1077 | 395 to 1077 | `ecdc-bdbv-drc-uga-2026-05-27` |
| Deaths | 247 | 106 to 247 | `inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5` |

Interpretation: the public method preserves disagreement. The range is part of the evidence state, not a confidence interval and not a model output.

## Step 3: Check Health-Zone Attribution Lag

From `data/public_zone_counts_2026-05-26.csv`:

- 18 health-zone rows are present.
- Source-attributed confirmed rows sum to 109.
- Source-attributed suspected rows sum to 1058.
- Source-attributed confirmed-death rows sum to 5.

Compared with the headline confirmed total of 128, the source-attributed confirmed table is 19 cases behind the headline value.

Interpretation: the package records this as attribution lag. It does not spread the 19-case difference across zones, because that would create false geographic precision.

## Step 4: Review Source Clocks

From `data/public_source_index.csv` and `data/public_latency_observatory.csv`:

- 45 public source-index rows are present.
- 26 rows have measurable latency.
- 19 rows remain visible but have missing `data_as_of` for latency measurement.

Interpretation: missing source clocks are retained as review signals. They are not deleted just because they cannot support a lag calculation.

## Step 5: Read Blindspots As Public Evidence States

From `data/public_blindspots.json`, the current package tracks:

| Blindspot | Affected count | Public effect |
|---|---:|---|
| Restricted publisher bytes | 17 | Some source rows expose metadata but not raw publisher bytes. |
| Missing `data_as_of` for latency | 19 | Latency cannot be measured for those source rows. |
| Health-zone attribution lag | 13 | National totals may be timelier than zone attribution. |
| Open calibration resolution | 15 | Commitments are not scored until public resolution dates. |

Interpretation: these are not defects to hide. They are public evidence states that help prevent overclaiming.

## Step 6: Inspect Calibration Accountability

From `data/public_calibration_status.json`:

- 15 public calibration commitments are open.
- 0 commitments are resolved in this snapshot.
- The next public resolution date is `2026-06-19`.
- The public blocks were registered on 20 May, 21 May, and 26 May 2026.

Interpretation: the public repo shows that commitments existed before their resolution dates. It does not publish the private scoring implementation or quantitative internals behind the broader workbench.

## Step 7: Preserve The Boundary

This worked review teaches the public method:

- Keep source claims traceable.
- Preserve source-clock distinctions.
- Treat attribution lag as a documented state.
- Track blindspots instead of hiding them.
- Keep public calibration rows accountable before resolution.

It does not publish:

- model parameters;
- feature weights or thresholds;
- probability intervals;
- private source inputs;
- mutable scoring or resolver tools;
- source collection automation;
- private-data adaptation workflows.

## Run The Review Locally

For a concise command-line version of this walkthrough, run:

```bash
python3 examples/review_public_methodology.py
```
