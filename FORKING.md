<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Fork this and run it on your own point-of-care data

If you have ground-truth data this project does not (per-zone case counts,
testing results, local transport patterns), you can run the LOVS model on your
own numbers, internally, in about a minute. You get back a visibility-adjusted
estimate of how many cases you are likely missing, and a ranked list of corridors
showing where onward spread is most likely, so you can prioritise where to survey
and deploy next.

Your data never leaves your machine. `run_local.py` makes no network calls and
needs no third-party packages (only the Python standard library and this repo's
own `lovs/` modules, which you already have once you clone the repo).

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

| Field | What it is |
|---|---|
| `source_zones` | The affected zones you have data for. Each needs a `zone_id` and your observed `confirmed`, `suspected`, and `deaths`. Per-zone counts matter: a zone with more confirmed cases drives more onward-spread risk. This is the single biggest lever the public version is missing. |
| `candidate_target_zones` | The zones you want ranked for onward-spread risk (where cases could appear next). |
| `corridor_edge_weights` | Optional. Relative movement/transport intensity for a `"source->target"` corridor. `1.0` (the default) means no information; raise corridors you know carry more travel. The second biggest lever. |
| `horizon_days` | The look-ahead window for the risk estimate. Allowed values: `7`, `14`, or `30` (the model's validated windows; the public release uses `30`). |
| `outbreak_id`, `as_of`, `pathogen`, `country_scope` | Labels for your run. |

`zone_id` values are free-form labels. You do not need GPS coordinates for the
ranking; coordinates only matter for the map visuals in the published brief.

## What you get back

- **Visibility:** a reporting-completeness band and the implied underlying
  confirmed-case range (what your observed count likely understates).
- **Transmission:** the plausible number of silent transmission chains and the
  probability that several generations have already spread undetected.
- **Corridor deployment ranking:** every `source -> target` corridor sorted by
  ascertainment-adjusted risk, so the top rows are where to look first.

## Important: this is internal situational awareness, not a pinned prediction

The public release pipeline (`refresh_pipeline.py`, `release_snapshot.py`) is
deliberately strict: every number must trace to a dated, archived public source
in `data/bundibugyo-2026/manifest.json`, and forecasts are pre-committed in an
append-only calibration ledger before their resolution date. That discipline is
what makes the public methodology auditable.

`run_local.py` intentionally skips all of that. It trusts the numbers you give it,
because for your own response your point-of-care data is the ground truth. Use it
for your own situational awareness and deployment decisions. If you want to
publish scored, pre-committed predictions, follow `PIPELINE.md` instead.

## Sharing back (optional)

If you are able to share de-identified data or findings to improve the public
methodology, reach out to frans@arcede.com. Please do not paste sensitive or
identifiable data into public GitHub issues.
