# Worked Snapshot Review

This walkthrough uses the current public files in this repository. It is a real review of the 31 May 2026 public artifact, with headline counts dated 31 May 2026 (INRB SitRep #017) and per-health-zone attribution dated 29 May 2026. It is not a synthetic example and it is not a new outbreak update.

Use it as a pattern for reviewing a future public snapshot or a partner-local aggregate package.

## Inputs

Start with:

- `data/public_snapshot.json`
- `data/public_reported_counts.csv`
- `data/public_source_index.csv`
- `data/public_source_conflicts.json`
- `data/public_zone_counts_2026-05-29.csv`
- `data/public_blindspots.json`
- `data/public_latency_observatory.csv`
- `data/public_calibration_status.json`

## Step 1: Identify The Snapshot Clock

From `data/public_snapshot.json`:

- `as_of`: `2026-05-31T23:59:59Z`
- `data_as_of`: `2026-05-29`

Interpretation: this is a 31 May publication package whose headline clock is 31 May and whose health-zone attribution clock is 29 May. Do not compare it to later sources as if they were already included in this release, and do not difference the two internal clocks against each other.

## Step 2: Read Headline Counts As Public Claims

Laboratory-confirmed cases and confirmed deaths are the only cumulative case metrics. From `data/public_snapshot.json` `reported_counts`:

| Cumulative metric | Primary | Public range | Primary source ID |
|---|---:|---:|---|
| Confirmed cases | 328 | 289 to 328 | `inrb-sitrep-017-2026-05-31` |
| Confirmed deaths | 49 | n/a (confirmed-death band) | `inrb-sitrep-017-2026-05-31` |

The confirmed headline is 328 (321 DRC + 7 Uganda) and the confirmed-death band is 49 (48 DRC + 1 Uganda).

Interpretation: the public method preserves disagreement. The range is part of the evidence state, not a confidence interval and not a model output.

A separate `operational_status` block carries the point-in-time operational caseload, not a cumulative count:

| Operational metric (point prevalence, as_of 2026-05-31) | Primary |
|---|---:|
| Suspected cases under investigation | 116 |
| Suspected cases in isolation | 104 |
| Active suspected total | 220 |

Interpretation: this operational caseload is national-only, rises and falls, and is never added to confirmed. The block is tagged `basis: point_prevalence_not_cumulative` and `summable_into_confirmed: false`. We report only lab-confirmed cumulative cases and do not reproduce the INRB dashboard "total" of confirmed plus under investigation plus in isolation, because that sum conflates a cumulative stock with a point-in-time caseload.

## Step 3: Check Health-Zone Attribution Lag

From `data/public_zone_counts_2026-05-29.csv`:

- 25 health-zone rows are present.
- 22 rows carry confirmed cases.
- Source-attributed confirmed rows sum to 243.

Compared with the 29 May zone-clock national of 263 confirmed, the source-attributed confirmed table holds 243, leaving a 20-case zone-clock residual not yet attributed to a zone. The 31 May headline of 328 is on a later clock and is deliberately not differenced against the 29 May zone table.

Interpretation: the package records this as attribution lag. It does not spread the residual across zones, because that would create false geographic precision.

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
