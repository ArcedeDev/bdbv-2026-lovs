<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# External data sources (staging layer)

This folder packages the public, citable data sources for the five LOVS data
leverages, and maps each one to the part of the system that already consumes it.
It exists so future snapshots can pull the same source types without rediscovering
them, and so newly found data can be staged with provenance before a scored
refresh.

## What is here

| File | What it is |
|---|---|
| `catalog.json` | Outbreak-agnostic registry: for each lever (zone counts, onset dates, centroids, mobility, confirmation latency) it records the public providers, the access tier (public / partner-only), and the exact system file and field that consumes it. Reuse this for any future outbreak. |
| `bdbv-2026.observed.json` | The concrete values discovered for the 2026 BDBV outbreak, with provenance: IOM DTM movement shares, the confirmation-latency datapoints, the centroid status (most already in `zones.json`; Nebbi outstanding), and the post-snapshot count escalation. |
| `bdbv-2026-05-20.sensitivity.json` | Output of `snapshot_sensitivity.py`: how the corridor ranking moves when the mobility and geography leverages are applied, run through the `run_local` engine. Regenerated, not hand-edited. |
| `freshness/` | Generated live-source freshness reports from `python3 source_ingest.py --live-check`. These record registered URLs, fetch status, page hashes, detected dates, extracted headline counts, and whether a source needs archive review before a scored refresh. |

## Where each lever plugs in (no parallel structures)

- **Validated centroids** -> `data/zones.json` (`lat`/`lon`). Most needed centroids are already present; only Nebbi (UGA) is outstanding.
- **Mobility / transport flow** -> `data/covariates-bdbv-2026.json` (alongside `road_connectivity_index`) and/or `run_local` `corridor_edge_weights`.
- **Confirmation latency** -> the `lovs_visibility` reporting-completeness / publication-latency prior.
- **Zone-attributed counts** and **onset dates** -> `data/bundibugyo-2026/manifest.json` provenance and the per-zone `confirmed` inputs. These two remain partner-only at the needed granularity; the catalog records the best public proxies.

## Important: this does not touch the pinned snapshot

The released `data/live-bdbv-2026-output.json` and `data/calibration-ledger.json`
are immutable and pre-committed (their forecasts resolve 2026-06-19). Nothing in
this folder modifies them. New sources are incorporated only at a future scored
refresh, never retroactively, so the calibration scoring contract holds.

## How a future snapshot uses this

1. Update `catalog.json` only if a new provider or source type appears.
2. Create `<outbreak>.observed.json` with the new values and their provenance.
3. Run `python3 snapshot_sensitivity.py` to see how the new leverages move the
   corridor ranking before committing them.
4. Run `python3 source_ingest.py --live-check --as-of <YYYY-MM-DD>` to compare
   every registered source's live landing page against the archived manifest.
5. At the scored refresh, fold the staged values into `zones.json`,
   `covariates-bdbv-2026.json`, and the manifest through the normal pipeline,
   then pin the new forecasts.

## How to check for fresh releases

Use the registry-backed live check:

```bash
python3 source_ingest.py --live-check --as-of 2026-05-21
```

The command writes `data/external_sources/freshness/bdbv-2026-<date>.json`.
Rows with `needs_review=true` are not automatically promoted; archive the source
bytes and extracted figures through the manifest first. To expand coverage, add a
new recurring publisher to `source_registry.json` and this check will include it
on the next run.
