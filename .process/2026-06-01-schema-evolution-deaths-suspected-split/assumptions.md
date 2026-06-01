# Assumptions verified, schema evolution change

## Assumptions

Every assumption is verified at a precise file:line or by direct evidence inspection.

1. Schema is canonically defined in the reconciler module. Verified at `lovs/lovs_reconciler.py:56-95`.
2. `OutbreakSnapshot.reported_deaths` was a single `ReconciledCount | None` pre-migration. Verified at `lovs/lovs_reconciler.py:105`.
3. INRB normalized content exposes split deaths fields `deaths_confirmed_drc=17` and `deaths_suspected_drc=246` for the May 28 build. Verified at `data/bundibugyo-2026/manifest.json:1` (entry for `inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5`).
4. The cross-class 247-death composition rule was local to the pipeline orchestrator. Verified at `refresh_pipeline.py:601` (composition block, now retired).
5. SitRep #015 and #016 raw PDF bytes are present locally. Verified at `/tmp/inrb-umie-pr43-sweep/data/insp_sitrep/raw/SitRep_MVE_015-2026.pdf:1` and `SitRep_MVE_016-2026.pdf:1` (read page 1 of each).
6. SitRep #015 `cumul_cas_suspects` tile value is 349 (not 3491); the trailing character is a footnote marker. Verified at `SitRep_MVE_015-2026.pdf:1` (direct PDF read, headline tile row 3) and cross-confirmed by founder. SitRep #014 reported the same field as "349*" with footnote "Revised downward; number of suspect cases was revised down after investigation and sampling confirmed some and ruled out others".
7. SitRep #016 has no `cumul_cas_suspects` tile; the cumulative-suspect headline is replaced by an active/isolation split. Verified at `SitRep_MVE_016-2026.pdf:1` (direct PDF read, 7-tile dashboard).
8. `snapshot_content_seed` serializes with `sort_keys=True` before hashing. Verified at `lovs/lovs_reconciler.py:369`.
9. Brief HTML is regenerated from the brief renderer at every release; manual edits do not survive. Verified at `make_brief.py:1-50` (header docstring) and `release_snapshot.py:1-60`.
10. Website mirror lives under the public assets path and is updated manually post-release. Verified at `release_snapshot.py:851` (parity verification only, no copy).
11. Per-cycle founder go-signal is required before any public push. Verified at `MEMORY.md:1` (project_bdbv_2026_lovs.md entry) and the prior handoff at `.process/2026-05-31-may29-31-locf-and-pipeline-rehydration/handoff.md:5`.

## Items routed to grill-log.md (not asserted)

- Whether legacy reason codes should remain accepted as a deprecation alias for one release cycle. Default: rejected immediately.
- Whether `deaths_suspected` should expire after N cycles of LOCF. Default: no expiry.
- Whether the `suspected_cumulative=349` headline should display alongside `suspected_active=321`. Default: both inline; the small 28-case gap reveals that nearly all surviving suspect cases are currently open.
- Whether the website mirror should display a schema-version banner. Default: no banner.
