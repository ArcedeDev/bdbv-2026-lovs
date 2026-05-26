# Validation: Canonical BDBV Bundle Parity Gate

**Change-id:** 2026-05-26-canonical-bundle-parity-gate
**Date:** 2026-05-26

## Assumptions

- Verified: LOVS already defines the public asset mirror set for website review readiness. Citation: `release_snapshot.py:148-164`.
- Verified: The current `--with-website` release path checks assets but not the latest website snapshot JSON against canonical LOVS output. Citations: `release_snapshot.py:783-794`, `release_snapshot.py:661-682`.
- Verified: The `--with-website` help text overstates the action. Citation: `release_snapshot.py:742`.
- Verified: Daily prep sync uses the canonical LOVS root when it writes a website review snapshot, but its website gates do not independently prove the written latest snapshot still matches canonical LOVS. Citations: `daily_snapshot_prep.py:267-292`, `daily_snapshot_prep.py:302-337`.
- Verified: Existing asset byte parity logic can be reused instead of duplicated. Citations: `lovs/cross_surface_parity.py:27-48`, `lovs/cross_surface_parity.py:70-113`.
- Verified: The website has a deterministic latest snapshot registry that a LOVS-side gate can parse without running Next.js. Citations: `apps/site/app/bdbv-2026/_data/snapshots/index.ts:17-25`, `apps/site/app/bdbv-2026/_data/snapshots/index.ts:31-41`, `apps/site/app/bdbv-2026/_data/snapshots/index.ts:57-60`.
- Verified: The website publisher already understands self-contained source references, but only inside the generated website snapshot. Citations: `sync-bdbv-lovs.py:1538-1560`, `sync-bdbv-lovs.py:1563-1575`.
- Verified: The website publisher copies the same public asset families the LOVS gate must compare. Citation: `sync-bdbv-lovs.py:2126-2158`.
- Verified: The publisher can be pointed at any LOVS root, which is useful for review but also allows accidental stale-root generation. Citations: `sync-bdbv-lovs.py:2182-2187`, `sync-bdbv-lovs.py:2217-2226`.

| Assumption | Ground Truth | Status |
| --- | --- | --- |
| LOVS already defines the public asset mirror set for website review readiness. | `release_snapshot.py:148-164` declares `DEFAULT_WEBSITE_PUBLIC`, `DEFAULT_WEBSITE_ROOT`, and `WEBSITE_ASSETS` for visuals, brief PDF, workbook, schema, and manifest. | Verified |
| The current `--with-website` release path checks assets but not the latest website snapshot JSON against canonical LOVS output. | `release_snapshot.py:783-794` only scans website source hazards and calls `check_website_in_sync()`; `release_snapshot.py:661-682` compares only `WEBSITE_ASSETS` bytes. | Verified |
| The `--with-website` help text overstates the action. | `release_snapshot.py:742` says "Also run the website sync" even though the code path checks hazards and asset sync only. | Verified |
| Daily prep sync uses the canonical LOVS root when it writes a website review snapshot, but its website gates do not independently prove the written latest snapshot still matches canonical LOVS. | `daily_snapshot_prep.py:267-292` passes `--lovs-root REPO_ROOT` to the website sync script; `daily_snapshot_prep.py:302-337` only runs two focused website tests, TypeScript, and lint. | Verified |
| Existing asset byte parity logic can be reused instead of duplicated. | `lovs/cross_surface_parity.py:27-48` defines the canonical static and glob mirror pairs and `lovs/cross_surface_parity.py:70-113` returns structured `checked`, `mismatches`, and `missing` results. | Verified |
| The website has a deterministic latest snapshot registry that a LOVS-side gate can parse without running Next.js. | `apps/site/app/bdbv-2026/_data/snapshots/index.ts:17-25` imports frozen snapshot JSON files; `apps/site/app/bdbv-2026/_data/snapshots/index.ts:31-41` puts newest date first in `AVAILABLE_SNAPSHOT_DATES`; `apps/site/app/bdbv-2026/_data/snapshots/index.ts:57-60` returns that first entry. | Verified |
| The website publisher already understands self-contained source references, but only inside the generated website snapshot. | `sync-bdbv-lovs.py:1538-1560` walks `sourceId`, `primarySourceId`, `sourceIds`, and `metricSourceIds`; `sync-bdbv-lovs.py:1563-1575` validates references against the snapshot-local `sources` array. This does not prove references came from the canonical LOVS manifest. | Verified |
| The website publisher copies the same public asset families the LOVS gate must compare. | `sync-bdbv-lovs.py:2126-2158` copies SVG visuals, `brief.pdf`, workbook, schema, and manifest. | Verified |
| The publisher can be pointed at any LOVS root, which is useful for review but also allows accidental stale-root generation. | `sync-bdbv-lovs.py:2182-2187` exposes `--lovs-root`, then `sync-bdbv-lovs.py:2217-2226` loads `data/live-bdbv-2026-output.json` and the archive manifest from that configured root. | Verified |

## Current Local Probe

The current local website checkout has moved since the earlier stale-root finding: the registered latest website snapshot is `2026-05-26.json`, has `asOf=2026-05-26T23:59:59Z`, and currently reports `112` confirmed with primary source `cdc-current-situation-2026-05-25`. Canonical LOVS currently reports `112` confirmed with the same primary source, while `data/live-bdbv-2026-output.json` remains `as_of=2026-05-25T23:59:59Z`. This reinforces the need to compare fields and source IDs explicitly rather than relying on route date alone.

## Implementation Constraints

- The new gate should be stdlib-only.
- The new gate should call `lovs.cross_surface_parity.check_cross_surface_parity()` for asset comparisons.
- The gate should compare canonical LOVS output and manifest to the latest registered website snapshot, not merely to arbitrary JSON files on disk.
- Route date and analytic `asOf` must remain distinct; the gate should not require `snapshot.date == canonical as_of[:10]`.
- Findings should name fields, source IDs, or asset names directly so failed daily prep packets are actionable.
- This pass must not regenerate source data, website snapshots, workbook artifacts, or public previews.
