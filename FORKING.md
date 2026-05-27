<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Fork this and run it on your own point-of-care data

If you have ground-truth data this project does not (per-zone case counts,
testing results, local transport patterns, line-list onset dates, a measured
serial interval), you can run the LOVS model on your own numbers, internally,
in about a minute. You get back a visibility-adjusted estimate of how many
cases you are likely missing, and a ranked list of corridors showing where
onward spread is most likely, so you can prioritise where to survey and
deploy next.

Your data never leaves your machine. `run_local.py` makes no network calls
and needs no third-party packages (only the Python standard library and this
repo's own `lovs/` modules, which you already have once you clone the repo).

## Three steps

1. **Fork the repo** (or just clone it) and make sure you have Python 3.10+.
2. **Copy the example and put in your numbers:**
   ```bash
   cp point_of_care_input.example.json my_data.json
   # edit my_data.json with your figures
   ```
3. **Run it:**
   ```bash
   python3 run_local.py --input my_data.json
   # optional: also save the full result as JSON
   python3 run_local.py --input my_data.json --json-out my_run.json
   ```

## What you put in (`my_data.json`)

### Required

| Field | What it is |
|---|---|
| `source_zones` | The affected zones you have data for. Each needs a `zone_id` and your observed `confirmed`, `suspected`, and `deaths`. Per-zone counts matter: a zone with more confirmed cases drives more onward-spread risk. This is the single biggest lever the public version is missing. |
| `candidate_target_zones` | The zones you want ranked for onward-spread risk (where cases could appear next). |
| `corridor_edge_weights` | Optional. Relative movement/transport intensity for a `"source->target"` corridor. `1.0` (the default) means no information; raise corridors you know carry more travel. The second biggest lever. |
| `horizon_days` | The look-ahead window for the risk estimate. Allowed values: `7`, `14`, or `30` (the model's validated windows; the public release uses `30`). |
| `outbreak_id`, `as_of`, `pathogen`, `country_scope` | Labels for your run. |

`zone_id` values are free-form labels. You do not need GPS coordinates for the
ranking; coordinates only matter for the map visuals in the published brief.

### Optional (recommended if you have it)

These are the levers a national partner can pull that the public release
pipeline cannot. Each is fully backwards compatible: omit it and the runner
behaves exactly as before. The visibility nowcast and the transmission model
both improve when you supply them.

| Field | What it is |
|---|---|
| `history` | A list of prior snapshots in the same `source_zones` schema. With two or more, the visibility nowcast switches from `single_snapshot` (a conservative 7-day default observation window) to `empirical_history` (your actual cadence), and the "single as-of snapshot in window" uncertainty driver is dropped. This is exactly how the public method's `method_basis` field is gated; a partner with a daily situation report can lift it without any code changes. |
| `case_definition_version` | A free-form string identifying your case definition (for example `drc-moh-bdbv-2026-v2`). When declared, the visibility nowcast drops the "case-definition version not declared by sources" uncertainty driver. |
| `transmission_priors_override` | A partial override of the species-default BDBV transmission priors (`lovs/lovs_priors_bundibugyo.py`). Use this when you have measured the 2026-outbreak serial interval, R, or under-ascertainment from your own line list. Any field you omit falls back to the species default, so a partner who has measured only a serial interval can drop in only that one field. See worked example below. |

## What you get back

- **Visibility:** a reporting-completeness band and the implied underlying
  confirmed-case range (what your observed count likely understates).
- **Transmission:** the plausible number of silent transmission chains and
  the probability that several generations have already spread undetected.
- **Corridor deployment ranking:** every `source -> target` corridor sorted
  by ascertainment-adjusted risk, so the top rows are where to look first.
- **Method block** (when you also use `--json-out`): the basis (`single_snapshot`
  or `empirical_history`), the history snapshot count and earliest as-of, the
  case-definition version, whether priors were overridden, and the active R
  and serial-interval gammas. Use this as your audit trail.

## Worked example: a partner with a line list

The public release pipeline has only one aggregate confirmed count per
publication, so its R prior cannot be fitted to the 2026 outbreak (it stays
at the species-default `gamma(4.0, 3.0)`, an interim modelling prior). A
partner with line-list data can do better in three steps.

### Step 1: roll your line list into a per-snapshot count history

If you have a two-column CSV of `case_row_id,onset_date` for one health
zone, group it by your reporting cadence (daily, every three days, weekly)
and emit one `history` entry per cadence point. A 90-second one-liner in
your favourite stats environment is enough; the schema is identical to the
top-level `source_zones`. Example:

```json
"history": [
  {
    "as_of": "2026-05-13",
    "source_zones": [
      {"zone_id": "bunia", "confirmed": 7,  "suspected": 95,  "deaths": 1}
    ]
  },
  {
    "as_of": "2026-05-17",
    "source_zones": [
      {"zone_id": "bunia", "confirmed": 11, "suspected": 142, "deaths": 3}
    ]
  }
]
```

That alone switches the visibility nowcast to `empirical_history` and
tightens the reporting-completeness band.

### Step 2: declare your case definition

```json
"case_definition_version": "drc-moh-bdbv-2026-v2"
```

This drops the "case-definition version not declared" uncertainty driver
from the visibility nowcast.

### Step 3: fit a 2026-outbreak R prior from your line list

If your line-list cadence supports a measured serial interval (the gap
between successive cases in a transmission chain), or an R from the
empirical doubling time, drop them in:

```json
"transmission_priors_override": {
  "species": "BDBV",
  "serial_interval_gamma": [5.0, 0.70],
  "r_prior_gamma": [6.0, 4.5],
  "notes": "Fitted from <your zone> line list, 2026-05-13 to 2026-05-20."
}
```

Both gammas are shape-rate form (`alpha`, `beta`) consistent with
`random.Random.gammavariate`. The default species prior values for
reference are in `lovs/lovs_priors_bundibugyo.py:BUNDIBUGYO_PRIORS_STAGE_TWO`:

| Field | Default (Stage Two) | Mean |
|---|---|---|
| `serial_interval_gamma` | `[4.0, 0.55]` | 7.27 d |
| `r_prior_gamma` | `[4.0, 3.0]` | 1.33 |
| `under_ascertainment_uniform` | `[0.3, 0.9]` | 0.6 midpoint |
| `incubation_gamma` | `[4.0, 0.6]` | 6.67 d |

Any field you omit falls back to the default. The partial-override pattern
is intentional: a partner who has measured only one of these should not be
forced to pretend to have measured all four.

### What the report tells you you got

When `history` and `transmission_priors_override` are both set, the report
header now shows the basis up front:

```
Your observed totals: 53 confirmed, 653 suspected across 3 zone(s)
Method basis: empirical_history  (2 prior snapshot(s) since 2026-05-13)
Case definition: drc-moh-bdbv-2026-v2
Transmission priors: OVERRIDE (BDBV); R gamma(6.00, 4.50), serial interval gamma(5.00, 0.70)
Visibility grade: low
  reporting completeness (50%): 39% to 45%
```

The `--json-out` artifact carries the same information in its `method`
block, so you can audit any later run against the inputs that produced it.

## Important: this is internal situational awareness, not a pinned prediction

The public release pipeline (`refresh_pipeline.py`, `release_snapshot.py`) is
deliberately strict: every number must trace to a dated, archived public
source in `data/bundibugyo-2026/manifest.json`, and forecasts are
pre-committed in an append-only calibration ledger before their resolution
date. That discipline is what makes the public methodology auditable.

`run_local.py` intentionally skips all of that. It trusts the numbers you
give it, because for your own response your point-of-care data is the
ground truth. Use it for your own situational awareness and deployment
decisions. If you want to publish scored, pre-committed predictions, follow
`PIPELINE.md` instead.

## Sharing back (optional)

If you are able to share de-identified data or findings to improve the
public methodology, reach out to frans@arcede.com. Please do not paste
sensitive or identifiable data into public GitHub issues.
