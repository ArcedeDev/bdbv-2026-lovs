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

For the DRC Ministry epidemiological dashboard, the registry also records the
official GraphQL endpoint behind the web page. Pull the structured payload and
any linked official PDFs into the private dropbox with:

```bash
python3 source_ingest.py --pull-source drc-moh-epidemie-dashboard --as-of 2026-05-23
```

This writes restricted source bytes and sidecar metadata under
`data/bundibugyo-2026/private/sources/`. Review the generated sidecars before
running `python3 source_ingest.py --ingest <path>`. DRC dashboard zone rows are
official but remain `table_semantics_status=source_review` until the matching
PDF/table label confirms whether a report's rows are cumulative or daily/new.

## Scheduled source-prep cadence

The source registry owns the scheduled-prep policy. Print the UTC cron plan with:

```bash
python3 source_ingest.py --schedule
```

Use the emitted slot commands for autonomous prep, for example:

```bash
python3 daily_snapshot_prep.py --slot africa_morning_primary --as-of "$(date -u +%F)" --earth-awake --auto-pull --build-review-snapshot
```

The recommended schedule is five cron entries: three Africa/Europe-timed daily
official checks, one weekday US Eastern cross-check for CDC/US-government and
watch/context pages, and one weekly covariate/context metadata check. These jobs
write `freshness/` reports and `prep/` review packets. With `--auto-pull`, known
machine-readable sources such as the DRC MoH dashboard can be staged into the
private source dropbox with sidecars. With `--build-review-snapshot`, the
deterministic pipeline is rebuilt and the local RC website worktree receives an
unpublished dated review snapshot; with `--website-gates`, the focused BDBV
website tests, typecheck, and lint run after sync. These jobs do not update the
manifest, commit, push, or publish the website. Rows marked `needs_review=true`
must still pass byte archiving, source-date review, evidence-chain review, and
the release gates before they can affect a scored snapshot.
Each prep run also writes an ignored `health/` report that reduces freshness,
review queue, release-check, website-sync, and optional live-public parity state
to a red/yellow/green readiness signal. Before any public release, run:

```bash
python3 daily_snapshot_health.py --as-of "$(date -u +%F)" --live-public-check --write-report
```

Set `LOVS_EARTH_AGENT_ID` only in the private runtime environment if the prep
packet should also be summarized to an Earth journal; journaling is disabled by
default for portable/public runs.

Some registered channels set `extractor_backend: air_preferred`. These are pages
where ordinary HTTP text extraction is often weak, especially official social
posts and dynamically rendered media pages. AIR should be used there to capture
the full text, canonical URL/status id, publication timestamp, author identity,
and screenshot or rendered evidence. AIR output is still only an extraction
artifact: it enters the same dropbox/manifest/evidence-chain review path as any
other source and does not bypass source-tier caveats.

When Earth MCP is available, the preferred AIR route is Earth
`research_import_from_urls` into a clearly named source-capture scope. Treat
that as a capture smoke test and evidence staging lane, not a release gate:
successful imports prove the page text is retrievable, while failed imports or
seed-hygiene skips are blockers to resolve with a more precise URL, expected
page title, direct social-status URL, or rendered screenshot/hash capture. For
official social updates, a profile URL alone is insufficient for scored use; the
archive package must preserve the direct post/status identifier, timestamp,
author/verification context, extracted text, and screenshot/hash before any
claim can move from `watch.json` into the outbreak manifest or evidence chains.
