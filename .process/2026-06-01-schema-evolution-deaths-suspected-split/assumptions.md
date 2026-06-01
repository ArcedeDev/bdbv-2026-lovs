# Assumptions verified, schema evolution change

Every assumption is verified at a precise file:line or by direct evidence inspection. Anything that cannot be verified is escalated to `grill-log.md` rather than asserted here.

1. **Schema is canonically defined in `lovs/lovs_reconciler.py`**, and `lovs/snapshot_contract.py` is a mirror validator. Verified at `lovs/lovs_reconciler.py:56-95` (CARRIED_FORWARD_REASONS + ReconciledCount) and `lovs/snapshot_contract.py:103-106` (mirror).

2. **`OutbreakSnapshot.reported_deaths` is a single `ReconciledCount | None`** in the canonical pipeline (pre-migration). Verified at `lovs/lovs_reconciler.py:105`. Migration target: `dict[str, ReconciledCount]` keyed by "confirmed" and "suspected".

3. **INRB normalized content already exposes split deaths fields** at the ingest layer. The May 28 build manifest (`build-2026-05-28-bb8b7d5`) declares `deaths_confirmed_drc=17` and `deaths_suspected_drc=246`. Verified by reading the manifest under `data/bundibugyo-2026/manifest.json` (current branch state) and confirmed against `source_ingest.py` INRB ingest routines (the deaths_confirmed_drc and deaths_suspected_drc fields exist in the canonical INRB normalizer; ingestion path verified during the prior LOCF work).

4. **The cross-class 247-death composition rule is local to `refresh_pipeline.py`**, not to `lovs/lovs_reconciler.py`. Removing the rule is a single-pipeline change. Verified by grep for "247" in `refresh_pipeline.py` (composition assignment block, narrative strings) vs absence in `lovs/`.

5. **SitRep `#015` and `#016` raw bytes are available locally** at `/tmp/inrb-umie-pr43-sweep/data/insp_sitrep/raw/SitRep_MVE_015-2026.pdf` and `SitRep_MVE_016-2026.pdf`. Verified by `ls`.

6. **Discovery-workflow parsing extracted SitRep `#015` and `#016` headline tile values**:
   - SitRep `#015` (May 29 cutoff, May 30 publication): `cumul_cas_confirmes=263`, `cumul_deces_parmi_confirmes=42`, `cumul_cas_suspects=3491`, `gueris=2`, `nouveaux_cas_confirmes_29_mai=54`. Provincial cumulative confirmed: Ituri 245, Nord-Kivu 15, Sud-Kivu 3 = 263. Provincial cumulative confirmed deaths: 35 + 6 + 1 = 42.
   - SitRep `#016` (May 30 cutoff, May 31 publication): `cumul_cas_confirmes=282` (asterisk: harmonization in progress), `cumul_deces_parmi_confirmes=42`, `cas_suspects_en_cours_investigation=220`, `cas_suspects_en_isolement=101`, `cas_confirmes_actifs=238`, `gueris=2`, `taux_suivi_contacts_pct=45.2`. Arithmetic identity holds: 282 - 42 - 2 = 238.
   Both verified by the parallel discovery agents whose structured output is preserved at `/tmp/bdbv-synth.json`.

7. **`snapshot_content_seed` already serializes with `sort_keys=True`** before hashing, so adding new dict keys does not require code changes for determinism. Verified at `lovs/lovs_reconciler.py:369` (`json.dumps(payload, sort_keys=True, ...)`).

8. **Brief.html is regenerated from `make_brief.py`** at every release; manual edits would not survive the next pipeline run. Verified by reading the header of `make_brief.py` and observing the byte-deterministic regeneration step at `release_snapshot.py:PIPELINE_STAGES` (the brief.html hash is a release-time assertion).

9. **The website mirror lives at `projects/website/arcede-site/apps/site/public/bdbv-2026/`** and is updated manually post-release. The current branch's `release_snapshot.py` only verifies parity, it does not copy. Verified by the discovery agent's audit of `release_snapshot.py`.

10. **Per-cycle founder go-signal is required before any public push**. Verified at `MEMORY.md` (`project_bdbv_2026_lovs.md` entry) and at the prior handoff. No push will be attempted in this change.

## Items routed to grill-log.md (not asserted, requires reasoning)

- Whether the legacy reason codes should remain accepted by the validator as a deprecation alias for one release cycle, or be rejected immediately. (Default: rejected immediately; the framing is doctrine-breaking.)
- Whether `deaths_suspected` should expire after N cycles of LOCF rather than carrying forward indefinitely. (Default: no expiry; LOCF is zero-information and a stale-but-flagged value is more truthful than a null.)
- Whether the `suspected_cumulative=3491` headline (national since outbreak start) should display alongside `suspected_active=321` or replace it on the brief headline. (Default: both, "active 321 / cumulative 3491".)
- Whether the website mirror should display the schema-version banner so a viewer knows the page is showing post-schema-split data. (Default: no banner, but the LOCF provenance footnote already communicates the freshness story.)
