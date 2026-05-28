# BDBV-2026 LOVS, INSP per-zone propagation and PCR-modulated ascertainment, v1

**Spec id:** bdbv-2026-insp-per-zone-methodology-v1
**Status:** v1.2, founder sign-off recorded 2026-05-28; Phase 2 validation complete; mainline integration opened via Plan A (`.process/2026-05-28-may28-insp-per-zone-landing/`)
**Cycle of origin:** 2026-05-28 (post POC on `methodology/insp-per-zone-and-pcr-capacity-poc`)
**Authoritative wiki page:** `reference:bdbv-methodology-improvement-brief` (updated 2026-05-28)
**POC evidence:** `.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/`
**Compatibility class:** Medium-Critical, methodology change that touches the public release contract.

This is a contract document, not a plan. It describes WHAT changes, WHERE it surfaces, and the academic case for WHY. The HOW is left to per-cycle plans that consume this spec.

---

## 1. Intent

Promote the public, MIT-licensed INRB-UMIE consortium release (Institut National de Recherche Biomédicale Kinshasa, Institut National de Santé Publique, Unité de Modélisation et Intelligence Epidémique, with University of Oxford and Northeastern University) from a corroborating source-id to an authoritative per-health-zone input in the LOVS public methodology. Use INSP-transcribed per-zone case and death series to expand the corridor watchlist's source zone partition from 11 zones (WHO/ECDC-derived) to the full INSP-covered set at each snapshot's `as_of`. Adopt Africa CDC decentralisation-plan PCR capacity as a per-zone modulator of the under-ascertainment band, with explicit fallback to the BDBV species default for zones without PCR plan coverage. Disclose the per-zone confirmed-deaths attribution lag as a load-bearing methodological honesty note, not a defect.

This change is staged: description-class additions (alias bridge, per-zone source data, attribution-lag disclosure) land in the May 28 snapshot immediately. The forecast-class addition (PCR modulator changing the under-ascertainment prior that feeds the visibility nowcast) lands as a parallel shadow surface in May 28 and is promoted to primary only after at least one outcome cycle of parallel scoring shows it does not regress calibration.

---

## 2. Epidemiological and academic rationale

### 2.1 Source-zone fidelity (Finding 1)

The current 11-zone partition is derived from WHO Disease Outbreak News and ECDC outbreak reports, which aggregate to province or sub-province level for most of Ituri and Nord-Kivu. The INSP per-zone table covers 20 distinct zones at the latest INSP data date (26 May), 19 of which carry non-zero values on at least one metric. Ten of our current 11 LOVS source zones map cleanly into INSP via the alias bridge shipped on the POC branch; Katwa appears in INSP at zero values on 26 May (present_but_zero in the POC's three-state coverage audit). INSP additionally covers 11 zones not in our current source list: Aru, Damas, Fataki, Kalunguta, Karisimbi, Komanda, Kyondo, Mambasa, Nyankunde-alias-merged, Oicha, Rimba.

Three of these missing zones are epidemiologically significant:

- **Aru** (1 confirmed, 4 suspected, far NW Ituri). Borders South Sudan and Uganda directly. Its nearest cross-border targets are Arua-Uga and Nebbi-Uga, which the May 20 snapshot flagged as our highest-priority blindspot. Aru is the upstream-flow zone for that corridor. Adding it is a prerequisite for any later inclusion of Arua-Uga or Nebbi-Uga as candidate target zones in a future snapshot.
- **Komanda** (0 confirmed, 0 suspected, 1 confirmed death). The death-without-cases pattern is the late-detected-chain signal: the surveillance system caught Komanda only after a fatal outcome, indicating active transmission for at least one generation before the system detected symptomatic cases. Standard outbreak doctrine assigns higher onward-spread prior probability to such zones than to zones with only suspected cases.
- **Mambasa** (4 suspected, recently amended in the bb8b7d5 release with the Mabanga case reallocation). INSP's surveillance is reclassifying cases in real time; our frozen snapshot misses these refinements unless we re-ingest.

**Implication:** the 11-zone partition is systematically undersampled in three patterns, cross-border-upstream zones, death-first-detection zones, and recently-amended zones. Adopting the INSP partition reduces ecological-fallacy risk in corridor ranking.

### 2.2 Asymmetric measurement and the Rwampara reference case (Finding 2)

Africa CDC's decentralisation-plan PCR table covers 20 zones nationally but only 5 of our 11 current LOVS source zones (bunia, butembo, goma-cod, mongbwalu, nyankunde). The other 6 (bambu, katwa, kilo, miti-murhesa, nizi, rwampara) are absent from the plan. In the DRC operational context, this almost always means sample referral to a higher-tier lab. Rwampara is the worked reference case: 33 confirmed, 240 suspected, 2 confirmed deaths, the third-busiest zone in the outbreak, NOT in the Africa CDC plan. The most plausible explanation is sample transport to Bunia (10 machines, 5000 tests budgeted). Bunia's own load is 279 suspected. Combined load is approximately 519 suspected against 5000 tests, still adequate by WHO 5% TPR thresholds, but with degraded effective ascertainment because:

- Sample-transport turnaround time typically adds 24-72 hours per specimen.
- Specimen degradation on transported samples is real.
- Logistical capacity, not lab capacity, becomes the bottleneck.

**Implication:** Rwampara's actual ascertainment is plausibly LOWER than the species-default median, not higher. But the modulator as designed cannot express this; it is asymmetric (positive signal only). This is epistemically defensible (we do not claim worse than species without evidence) but operationally incomplete (we DO have evidence that absence from the decentralisation plan plus high suspected load implies degraded ascertainment).

**Open methodological question for v2:** bilateral modulator that lowers `hi` on absence-from-plan-plus-high-suspected-load. v1 surfaces the issue via a per-zone narrative annotation rather than changing the math. See section 9 (open questions).

### 2.3 Attribution-lag hierarchy (Finding 3)

On the 26 May INSP table, 12 of 17 confirmed deaths are unallocated to specific health zones (71%). Suspected_deaths is 100% attributed (residual 0). Confirmed cases are 92% attributed (residual 10 of 121). Suspected cases are 99% attributed (residual 14 of 1077). The confirmed-deaths attribution gap is the largest of the four metrics by a wide margin.

The pipeline from clinical death to per-zone confirmed_death classification is:

1. Clinical death (instant)
2. Sample collected (hours)
3. Specimen routed to PCR lab (hours-days)
4. PCR result (hours after receipt)
5. INRB clinical review for case classification (days to weeks)
6. Zone allocation in the INSP cumulative series (days after step 5)

Step 5 is typically the rate-limiter; INRB's clinical review queue is real and not bypassable. Total lag from death to per-zone allocation in active outbreaks is typically 1-3 weeks. National confirmed_deaths totals update at step 4 because INSP knows the count before zone assignment; per-zone allocation lags.

**Implication for modelling:**

| Metric | Attribution timeliness | Appropriate use |
|---|---|---|
| suspected_cases (per zone) | Timely (99% attribution) | Live corridor risk; source-zone identification |
| confirmed_cases (per zone) | Near-timely (92%) | Corroboration; R-prior fitting |
| confirmed_deaths (per zone) | Trailing (29%) | Retrospective Brier scoring (after 2-3 weeks); CFR calibration |

A per-zone Death-Back-Projection at the live snapshot date would systematically underestimate per-zone transmission. Honest accounting treats per-zone confirmed_deaths as a LOWER BOUND on true per-zone deaths, with the unallocated_residual as an UPPER BOUND. v1 of this spec surfaces this as a methodology disclosure field rather than recomputing any model output.

### 2.4 Zero-signal degenerate-input doctrine (Finding 4)

The POC's first-cut PCR modulator divided by `max(suspected, 1)` as a numerical convenience to avoid infinite ratios when suspected = 0. The fresh-context reviewer caught that this produced the MAXIMUM upward ascertainment boost for zones with zero suspected cases (e.g., goma-cod), inverting the semantic: a zone with no surveillance load provides no evidence of high ascertainment.

The fix landed in the POC (short-circuit zero-suspected to species default fallback). The broader doctrine for the spec: **any signal-derived modulation of a prior must treat the no-signal case as a null operation, not as a maximum-signal operation.** v1 adopts this as a checklist for every modulator we add in the methodology improvement track.

---

## 3. Description-class vs forecast-class change split

This split determines which changes land in May 28 immediately and which need pre-commitment to an outcome cycle.

| Change | Class | May 28 status |
|---|---|---|
| Alias bridge (`lovs/zone_alias_bridge.py`) | Description | Promote |
| INSP per-zone loader (`lovs/insp_per_zone_loader.py`) | Description | Promote |
| New source_zones from INSP per-zone | Description (changes WHAT we see, not the model) | Promote, with backward compat |
| Three-state coverage audit | Description | Promote |
| Per-zone reconciliation `unallocated_residual` | Description | Promote |
| Attribution-lag disclosure for per-zone confirmed_deaths | Description | Promote |
| PCR-modulated under-ascertainment band | Forecast (changes nowcast inputs) | Surface as shadow, do not promote |
| Bilateral modulator for absent-from-PCR-plan zones | Forecast (v2, partner-data dependent) | Not in scope for v1 |

Description-class changes describe the underlying reality more accurately and pose no calibration-provenance risk; they may land at any cycle. Forecast-class changes change the math feeding visibility / corridor outputs; they MUST pre-commit to a parallel-scoring outcome cycle before promotion to primary.

---

## 4. Surface map

Every file that touches the spec, classified by change kind.

### 4.1 Code (additive only, except where noted)

| Path | Change kind | Purpose |
|---|---|---|
| `lovs/zone_alias_bridge.py` | New (from POC) | Bidirectional LOVS↔INRB canonical bridge + vendored upstream aliases backstop |
| `lovs/insp_per_zone_loader.py` | New (from POC) | INSP per-zone CSV reader, two-stage alias pipeline, three-state coverage audit, reconciliation |
| `lovs/pcr_capacity_prior_modulator.py` | New (from POC) | PCR capacity → per-zone (lo, hi) band, with zero-signal short-circuit |
| `data/lovs_zone_alias_bridge.json` | New (from POC) | Maintained bridge data file |
| `tools/poc_insp_runner.py` | Promote and rename | Becomes `tools/insp_per_zone_dump.py`, used by `refresh_pipeline.py` |
| `refresh_pipeline.py` | Additive | Calls loader and modulator; populates new fields on the snapshot |
| `release_snapshot.py` | Additive | Runs new consistency gate; surfaces new method_basis variant |
| `lovs/snapshot_contract.py` | Additive | New schema for per-zone fields and ascertainment bands |
| `lovs/insp_per_zone_consistency_gate.py` | New | Asserts `sum(per_zone) + unallocated_residual == national_total` for each metric, asserts per-zone source_id is `inrb-umie-ebola-drc-2026-<tag>` style |
| `lovs/attribution_lag_disclosure_gate.py` | New | Asserts every snapshot carrying per-zone confirmed_deaths also carries the attribution-lag disclosure field |
| `lovs/lovs_priors_bundibugyo.py` | Unchanged | Species default remains authoritative; modulator wraps it |
| `lovs/lovs_visibility.py` | Additive | Accepts optional `per_zone_under_ascertainment_bands` override; falls back to species default when None |
| `lovs/lovs_transmission.py` | Additive | Same per-zone override pattern as `lovs_visibility` |
| `tests/test_insp_per_zone_consistency_gate.py` | New | Coverage for new gate |
| `tests/test_attribution_lag_disclosure_gate.py` | New | Coverage for new gate |

### 4.2 Data artifacts under `data/`

| Path | Change kind | Purpose |
|---|---|---|
| `data/snapshot_contract.json` | Additive new fields | See section 5 schema |
| `data/live-bdbv-2026-output.json` | Additive new fields | Same shape as snapshot_contract |
| `data/zones.json` | Additive entries | New LOVS zone_ids for any INSP zone we add as source_zone (Aru, Komanda, Mambasa initially) |
| `data/bundibugyo-2026/manifest.json` | Unchanged | INRB-UMIE entries already present (`e40bc9e`, `bb8b7d5`); the spec consumes them |
| `data/calibration-ledger.json` | Unchanged | Forward-only doctrine preserved; existing blocks (May 20, May 21, May 26 Goma) untouched |
| `data/evidence-chains.json` | Additive | New evidence chain entries linking the new method_basis to the INRB-UMIE source-ids |
| `data/lovs_zone_alias_bridge.json` | New (from POC) | Already covered in 4.1 |

### 4.3 Deliverables under `deliverables/public-health-dataset/` (additive only)

The exporter (`export_public_health_dataset.py`) consumes `data/live-bdbv-2026-output.json` and emits 13 CSV + 1 XLSX + manifest + schema. Each sheet's column shape is already known from the inventory; spec-driven additions:

| File | Sheet | Change |
|---|---|---|
| `lovs-public-health-dataset.xlsx` | New sheet "Per Zone Snapshot" | Per-LOVS-zone case/death values at as_of, with `inrb_collapsed_from`, `present_in_insp_classification`, `pcr_ascertainment_band_lo`, `pcr_ascertainment_band_hi`, `pcr_modulator_basis` |
| `lovs-public-health-dataset.xlsx` | New sheet "Reconciliation Residuals" | Per-metric national total, zone-attributed sum, unallocated_residual, residual_fraction, with the data_as_of and source_id |
| `lovs-public-health-dataset.xlsx` | New sheet "Attribution Lag Disclosure" | Per-metric attribution timeliness band and methodology note |
| `per_zone_snapshot.csv` | New CSV | Same columns as the new sheet |
| `reconciliation_residuals.csv` | New CSV | Same columns as the new sheet |
| `attribution_lag_disclosure.csv` | New CSV | Same columns as the new sheet |
| `lovs-public-health-dataset.schema.json` | Additive | New sheet entries + new CSV file entries + new column declarations |
| `lovs-public-health-dataset.manifest.json` | Additive | New file entries in `outputs`; existing files keep their checksums but the manifest's overall checksum changes when adding rows |
| `corridors.csv` | Additive: source_ids column extended | Existing rows unchanged; new corridor rows added if source_zones expands |
| `zones.csv` | Additive rows | New zone entries for INSP zones promoted to source_zone status |
| `sources.csv` | Already includes INRB-UMIE entries | No change |
| `analysis_dependency_audit.csv` | Additive rows | New surfaces for "insp_per_zone_block", "pcr_ascertainment_modulator", "attribution_lag_disclosure" |
| `data_dictionary.csv` | Additive rows | Documentation for every new column |
| `model_outputs.csv` | Additive rows | New per-zone model output rows if forecast surface promotes |
| `staged_observations.csv` | Additive rows | INRB-UMIE per-zone observations promoted from staged to model-eligible |
| `reported_counts.csv` | Additive rows | New rows for per-zone INSP values, with row_type="per_zone_extracted_metric" |
| `timeline.csv` | Additive rows | Per-zone timeline entries from INSP daily-cadence data |
| `snapshot_clocks.csv` | Additive rows | New clock entries if INRB-UMIE becomes a primary source-id for any field |
| `public_claim_audit.csv` | Additive rows | New claim entries for "per-zone propagation grounded in INSP" and "PCR-modulated ascertainment grounded in Africa CDC plan" |
| `calibration_ledger.csv` | Unchanged in v1 | Forward-only; new blocks pinned in future snapshots reference the new method_basis but existing blocks stay frozen |
| `corrections_gaps.csv` | Additive rows | New entries for the four findings as methodology disclosures, with `severity=disclosure` (a new severity class to add to the schema) |

### 4.4 Brief artifacts under `brief/` and `deliverables/`

| Path | Change |
|---|---|
| `brief/brief.html` | Add per-zone view section; add attribution-lag disclosure block; refresh visibility-gap and corridor-risk visuals |
| `brief/visuals/corridor_risk.{png,svg}` | Regenerate with expanded source_zones |
| `brief/visuals/visibility_gap.{png,svg}` | Regenerate; consider per-zone-stacked variant |
| `brief/visuals/detection_depth.{png,svg}` | No change in v1 |
| `brief/visuals/pre_registration_timeline.{png,svg}` | No change in v1 |
| `brief/visuals/per_zone_snapshot.{png,svg}` | New visual: per-zone case/death stacked bars with attribution-lag shading |
| `brief/visuals/ascertainment_band_per_zone.{png,svg}` | New visual: per-zone (lo, hi) band, with species default reference line |
| `deliverables/brief.pdf` | Regenerate from `brief/brief.html` |
| `deliverables/Ebola_2026_brief.pdf` | Regenerate |
| `make_brief.py` | Additive: new sections, new visual generators |

### 4.5 Website surface

Verified inventory of 62 files under `apps/site/app/bdbv-2026/` (paths are relative to that root).

**Routes:**

| Path | Change kind | Purpose |
|---|---|---|
| `page.tsx` | Additive | Main route; expose new per-zone section reference |
| `layout.tsx` | Likely unchanged | Layout shell, no data dependencies |
| `[date]/page.tsx` | Additive | Per-snapshot detail route; consume new snapshot shape |
| `[date]/map/page.tsx` | Additive | Per-snapshot map route; consume new zone overlay |
| `map/page.tsx` | Additive | Live map route; consume new zone overlay |

**Data files (the cross-LOVS-website parity boundary):**

| Path | Change kind | Purpose |
|---|---|---|
| `_data/snapshots/YYYY-MM-DD.json` | Additive new fields | One file per snapshot; mirror of `data/live-bdbv-2026-output.json` per cycle. May 28 file gets the new fields |
| `_data/zones.json` | Additive entries | Add Aru, Komanda, Mambasa if founder green-lights expanded source_zones in v1 |
| `_data/types.ts` | Additive | TypeScript type declarations for the new snapshot fields |
| `_data/natural_earth_outlines.json` | Unchanged | Map outline data |
| `_data/build_latency_block.py` | Unchanged | Build-time latency block writer |

**Components likely affected by source_zones expansion (Aru, Komanda, Mambasa promotion):**

| Path | Change kind | Reason |
|---|---|---|
| `_components/CorridorWatchlist.tsx` | Additive rows | Lists corridor risk rows; expansion adds more rows |
| `_components/CorridorWatchlistMap.tsx` | Additive markers | Renders source/target markers; expansion adds origin markers |
| `_components/GeographicMap.tsx` | Additive overlay | Zone shapes; new zone outlines |
| `_components/BdbvSpatialIntelligenceMap.tsx` | Additive overlay | Map variant |
| `_components/BdbvSpatialMapRoute.tsx` | Additive overlay | Map variant on dedicated route |
| `_components/SpatialMapPreview.tsx` | Additive | Thumbnail preview |
| `_components/SpatialSynthesisMap.tsx` | Additive | Synthesis variant |

**Components likely affected by new per-zone block surfacing:**

| Path | Change kind | Reason |
|---|---|---|
| `_components/SnapshotPage.tsx` | Additive section | Main snapshot rendering; consume insp_per_zone_block |
| `_components/InferredTrajectory.tsx` | Possible additive | Per-zone trajectory if expanded view added |
| `_components/TrajectoryChart.tsx` | Possible additive | Per-zone chart variant |
| `_components/VisibilityGap.tsx` | Additive | Surface attribution-lag disclosure visually |
| `_components/StatusStrip.tsx` | Possible copy update | Source attribution updates |
| `_components/CommitmentTimeline.tsx` | Possible update | Calibration ledger pinned-block list |
| `_components/DetectionDepthVisual.tsx` | Likely unchanged | Detection-depth math |
| `_components/DoublingTimeSensitivityGrid.tsx` | Likely unchanged | Doubling-time grid |
| `_components/InlineRefs.tsx` | Additive | Source-attribution inline links; add INRB-UMIE refs |

**New components to add:**

| Path | Change kind | Reason |
|---|---|---|
| `_components/PerZoneSnapshot.tsx` | New | Per-LOVS-zone case/death table with attribution-lag shading on confirmed_deaths column |
| `_components/PerZoneAscertainmentBands.tsx` | New (forecast surface, shadow only in v1) | Per-zone (lo, hi) bands with species default reference line |
| `_components/AttributionLagDisclosure.tsx` | New | Surfaces the metric-timeliness hierarchy and the 1-3 week confirmed_deaths lag note |

**Lib files (data transforms and view models):**

| Path | Change kind | Reason |
|---|---|---|
| `_lib/spatialViewModel.ts` | Additive | View model for the spatial map; consume new zone list |
| `_lib/deathBackProjection.ts` | Additive caveat | The death-back-projection currently uses national-rollup deaths. Document that per-zone confirmed_deaths is trailing and the DBP must not be reframed per-zone without addressing the lag. |
| `_lib/trajectoryNarrative.ts` | Additive | Narrative around the trajectory; mention INRB-UMIE upstream |
| `_lib/sources.ts` | Additive | Source-attribution catalogue; add INRB-UMIE entries |
| `_lib/format.ts` | Unchanged | Generic formatters |
| `_lib/social.ts` | Unchanged | Social-share metadata |
| `_lib/prewarm.ts` | Likely unchanged | Prefetch helpers |
| `_components/BdbvRoutePrewarm.tsx` | Likely unchanged | Route prewarm orchestration |

**Sections under `_components/sections/`:**

| Path | Change kind | Reason |
|---|---|---|
| `sections/AtAGlance.tsx` | Additive panel | New disclosure panel: "Four-metric reconciliation" (headline vs INSP-attributed vs DRC MoH dashboard vs unallocated residual). New disclosure: "Data latency" gains an attribution-lag callout for confirmed_deaths. |
| `sections/Corridors.tsx` | Replacement (critical) | Lines ~71-117 currently carry the methodology callout that reads `zoneAttributedCounts` to derive `sourceZoneCount` and prints `"Corridor risk uses only the {N} confirmed cases that are currently officially zone-attributed across {sourceZoneCount} WHO AFRO source zones"`. Replace with INSP-per-zone framing; extend math to the four-metric reconciliation. This is the **single most load-bearing methodology copy site** on the page. |
| `sections/Ascertainment.tsx` | Additive sub-panel | Add per-zone ascertainment band visualization (new component or extension of `VisibilityGap`) cited to Africa CDC PCR capacity. Update Drivers / Interpretation paragraphs to reference the new per-zone surface as shadow-only in v1. |
| `sections/Blindspots.tsx` | Replacement (critical) | The top callout (`"Mahagi is still not a source zone because no WHO or Africa CDC source names Mahagi health zone as case-affected"`) is methodology-basis copy that needs rewrite. INSP now names additional Ituri zones; Mahagi's status either changes or the explanation does. Founder decision on framing required. |
| `sections/CalibrationPoints.tsx` | Possible copy edit | "Why not every corridor is calibrated" callout references the May 20 / May 21 pin policy explicitly. Update if INSP introduces a new pin policy or new corridors are added to the calibration ledger. |
| `sections/PointOfCare.tsx` | Replacement (critical) | The `DATA_INPUTS` array currently says "Zone-attributed counts" is "the largest single discrimination lever the method is missing". Once INSP provides them, this copy is misleading. Replace with an acknowledgment + a new ask (e.g., line-list onset histograms or per-zone PCR usage counts). |
| `sections/References.tsx` | Additive entries | Add INRB-UMIE consortium release as a new `METHODOLOGY_PRIORS` entry (or promote from `CANDIDATE_INPUTS`). Add Africa CDC decentralisation plan as a new methodology prior entry. |
| `sections/ParameterProvenance.tsx` | Additive | New InsightCard or DisclosurePanel for "INSP per-zone source vector" with its evidence-chain id. The May-23-onward audit panel is the natural home. |
| `sections/Limits.tsx` | Additive `LimitCard` | New card: "Attribution-lag disclosure", per-zone confirmed_deaths is a trailing indicator lagging the national rollup by approximately 1-3 weeks; surfaces the methodological honesty note from Finding 3. |
| `sections/ImperialAdoption.tsx` | Optional additive | If INSP per-zone is framed as an additional adopted input class, the "Adopted public inputs" card gains a new line item. Founder decision. |
| `sections/Hero.tsx` | Likely unchanged | Optional copy refresh only. |
| `sections/DetectionDepth.tsx` | Unchanged | Out of scope (national-aggregate, not per-zone). |
| `sections/HistoricalCalibration.tsx` | Unchanged | Hard-coded backtest rows, orthogonal. |
| `sections/PageFooter.tsx` | Unchanged structural | Sources auto-populate from `snapshot.sources`. |

**Layout primitives and shell (likely unchanged):**

| Path | Change kind | Reason |
|---|---|---|
| `_components/BdbvShell.tsx` | Possible copy update | App shell; methodology copy review |
| `_components/BdbvSidebar.tsx` | Additive entries | `PAGE_ANCHORS` gains new section anchors if new sections are added; `ENDPOINT_LABELS` may need new entries for new corridor source zones |
| `_components/BdbvScrollFrame.tsx` | Unchanged | Scroll behavior |
| `_components/BdbvIntentLink.tsx` | Unchanged | Intent-link UX |
| `_components/OpenSpatialMapButton.tsx` | Unchanged | UX affordance |
| `_components/StatusStrip.tsx` | Unchanged | Static authorities ack |

### 4.5.1 Publisher sync surface (out of scope for the LOVS repo but critical to flag)

The website's snapshot registry uses sentinel-bounded comment markers managed by the publisher script:

| File | Sentinel | Purpose |
|---|---|---|
| `apps/site/app/bdbv-2026/_data/snapshots/index.ts` | `SNAPSHOT_IMPORTS_BEGIN/END`, `SNAPSHOT_DATES_BEGIN/END`, `SNAPSHOT_MAP_BEGIN/END` | Auto-managed list of snapshot JSON imports and date list |
| `apps/site/lib/scripts/sync-bdbv-lovs.py` | n/a | Publisher script: copies `data/live-bdbv-2026-output.json` from LOVS to `apps/site/app/bdbv-2026/_data/snapshots/<date>.json`, updates the sentinel blocks |

For the May 28 landing, the publisher script must be aware of any new top-level fields in the live output, so it copies them verbatim. The script is in `apps/site/lib/scripts/sync-bdbv-lovs.py` and lives in the website repo, NOT in this LOVS repo. The spec coordinates: changes to LOVS-side `data/live-bdbv-2026-output.json` shape must be paired with website-side sync-script awareness, or `website_bundle_parity` gate will fail post-sync. Spec assumes the publisher script ALREADY copies the file verbatim (it does, per the `cross_surface_parity` gate); new fields will pass through automatically. The website-side update is therefore confined to TYPE declarations (`_data/types.ts`) and consumer code that READS the new fields.

### 4.5.2 Load-bearing copy strings (must edit; founder review required)

Three strings on the current website are factually wrong once INSP per-zone is consumed. These are critical replacement candidates that need founder review before edit:

1. **`Corridors.tsx`:** "Corridor risk uses only the {N} confirmed cases that are currently officially zone-attributed across {sourceZoneCount} WHO AFRO source zones." Becomes inaccurate once INSP is the source authority instead of WHO AFRO.
2. **`Blindspots.tsx`:** "Mahagi is still not a source zone because no WHO or Africa CDC source names Mahagi health zone as case-affected." Becomes inaccurate if INSP names additional Ituri zones not previously surfaced.
3. **`PointOfCare.tsx`:** "Zone-attributed counts... the largest single discrimination lever the method is missing." Becomes inaccurate once INSP provides per-zone attribution.

The `BdbvSpatialIntelligenceMap.tsx` tooltip's `aggregateOnly` branch (currently `"Public sources identify this affected geography, but do not allocate confirmed, suspected, deaths, or inferred burden to it"`) is the inverse of what INSP per-zone provides; it should be replaced with the actual per-zone allocation when present.

### 4.6 Documentation under repo root

| File | Change |
|---|---|
| `README.md` | Additive: mention INRB-UMIE consortium upstream; mention the new per-zone view in deliverables |
| `NUMBERS_AUDIT.md` | Add Rule extending Rule 3: per-zone composition does not mix across publication-clock variants; per-zone unallocated_residual must be carried explicitly |
| `FORKING.md` | Additive: partners can supply INSP-style `per_zone_history` and PCR capacity via the same partner override surface that already accepts history, case_definition_version, transmission_priors_override |
| `PIPELINE.md` | Additive: new ingestion step in the per-cycle pipeline diagram, between manifest ingestion and snapshot generation |
| `CITATIONS.md` | Add INRB-UMIE consortium citation, Africa CDC decentralisation plan citation, attribution-lag literature anchor |
| `LICENSES.md` | Note INRB-UMIE MIT wrapper; note Africa CDC publisher-terms-not-confirmed status for raw workbook bytes |
| `GROUNDING_AUDIT.md` | Update with the four findings as grounding observations |
| `VISUAL_CONVENTIONS.md` | Add convention for per-zone visualisation (color, band rendering, attribution-lag shading) |
| `REFRESH_19JUNE_PLAN.md` | Update with parallel-scoring plan for the PCR modulator promotion gate |
| `STAGED_MAY21_ZONES.md` | Update with new staged-zone entries for Aru, Komanda, Mambasa |

### 4.7 Wiki pages (Earth)

| Page id | Change kind |
|---|---|
| `reference:bdbv-methodology-improvement-brief` | Already updated 2026-05-28 |
| `reference:bdbv-insp-per-zone-methodology` | New page: full doctrine carrying this spec and the academic case |
| `reference:bdbv-attribution-lag-disclosure` | New page: documents the 1-3 week lag and the lower-bound / upper-bound disclosure pattern |
| `standard:source-attribution-lag-doctrine` | New page if it does not exist: formalize the doctrine across LOVS systems |
| `reference:lovs-zone-alias-bridge` | New page: documents the bridge as standalone infrastructure |
| `project_bdbv_2026_lovs` (memory) | Already updated to reference the POC; revisit after spec promotion |

### 4.8 CI / gates

| File | Change |
|---|---|
| `.github/workflows/public-release-gates.yml` | Add invocation of `lovs.insp_per_zone_consistency_gate`, `lovs.attribution_lag_disclosure_gate`, `lovs.pcr_modulator_shadow_gate`, `lovs.zone_alias_bridge_gate` after the existing gate steps. Each gate runs at the same step granularity as `lovs.publication_clock_contract` does today. |

---

## 5. Schema contracts

### 5.1 New fields on `data/snapshot_contract.json` and `data/live-bdbv-2026-output.json`

Additive only. Existing fields preserved with the existing semantics.

```json
{
  "insp_per_zone_block": {
    "as_of_data_date": "2026-05-26",
    "source_id": "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
    "method_basis": "INRB_UMIE_INSP_per_zone_v1",
    "by_lovs_zone": {
      "bunia": {
        "confirmed": 36,
        "suspected": 279,
        "confirmed_deaths": 2,
        "suspected_deaths": 55,
        "inrb_collapsed_from": [],
        "present_in_insp_classification": "present_with_data"
      },
      "nyankunde": {
        "confirmed": 10,
        "suspected": 70,
        "confirmed_deaths": 0,
        "suspected_deaths": 15,
        "inrb_collapsed_from": ["Nyankunde"],
        "present_in_insp_classification": "present_with_data"
      },
      "katwa": {
        "confirmed": 0,
        "suspected": 0,
        "confirmed_deaths": 0,
        "suspected_deaths": 0,
        "inrb_collapsed_from": [],
        "present_in_insp_classification": "present_but_zero"
      }
    },
    "national_at_data_date": {
      "confirmed": 121,
      "suspected": 1077,
      "confirmed_deaths": 17,
      "suspected_deaths": 246
    },
    "unallocated_residual": {
      "confirmed": 10,
      "suspected": 14,
      "confirmed_deaths": 12,
      "suspected_deaths": 0
    },
    "coverage_audit": {
      "present_with_data": ["bambu", "bunia", "butembo", "goma-cod", "kilo", "miti-murhesa", "mongbwalu", "nizi", "nyankunde", "rwampara"],
      "present_but_zero": ["katwa"],
      "structurally_absent": []
    }
  },
  "per_zone_under_ascertainment_bands": {
    "method_basis": "africa_cdc_pcr_capacity_modulated_v1",
    "source_id": "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
    "surface_role": "shadow_in_v1",
    "species_default_band": {"lo": 0.3, "hi": 0.9},
    "by_lovs_zone": {
      "bunia":    {"lo": 0.57, "hi": 0.90, "basis": "modulated", "saturation_ratio": 17.92},
      "butembo":  {"lo": 0.60, "hi": 0.90, "basis": "modulated", "saturation_ratio": 200.0},
      "goma-cod": null,
      "katwa":    null,
      "rwampara": null
    },
    "coverage_stats": {"modulated_zones": 4, "species_default_fallback_zones": 7, "total_zones": 11}
  },
  "attribution_lag_disclosure": {
    "metric_timeliness": {
      "suspected_cases": "timely",
      "confirmed_cases": "near_timely",
      "confirmed_deaths": "trailing",
      "suspected_deaths": "timely_at_national_rollup"
    },
    "per_zone_confirmed_deaths_note": "Per-zone confirmed_deaths is a trailing indicator. National rollup is timely (PCR-confirmed at step 4 of the INRB pipeline). Per-zone allocation typically lags by 1-3 weeks due to INRB clinical review queue (step 5). Treat per-zone confirmed_deaths as a lower bound and the unallocated_residual as the upper bound for total per-zone deaths."
  }
}
```

### 5.2 Surface_role taxonomy

```
"surface_role": one of
  "primary"         model output uses this directly; gate-validated
  "corroborating"   used for source-attribution diversity; not load-bearing for any field
  "shadow_in_v1"    computed and surfaced but explicitly not used in primary model output;
                    used for parallel scoring against the primary surface
  "disclosure"      surfaced for transparency; not used in any computation
```

### 5.3 Compatibility with existing partner override surface

`run_local.py` partner-facing schema (`history`, `case_definition_version`, `transmission_priors_override`) is preserved unchanged. The new partner-facing fields are additive:

```json
{
  "per_zone_history": [
    {"as_of": "2026-05-26", "by_lovs_zone": { ... }, "national_at_data_date": {...}, "unallocated_residual": {...}}
  ],
  "per_zone_under_ascertainment_bands": { ... }
}
```

When a partner supplies these, the loader treats them as authoritative for that partner's local run (the same forking-pipeline doctrine as for the existing override fields). When absent, the runner consumes from the public INRB-UMIE manifest entries.

---

## 6. Compatibility and invariants

### 6.1 Forward-only calibration ledger

NO existing calibration block is mutated. The May 20, May 21, and May 26 Goma blocks remain pinned at their original snapshot states. Future blocks pinned after this spec lands may reference the new method_basis, but they pin against THEIR snapshot, not a retroactive view.

### 6.2 Backwards compatibility for existing snapshot consumers

The new fields are additive only. A consumer reading the existing fields (`reported_counts`, `corridor_watchlist`, etc.) continues to read them unchanged. New consumers can opt into reading `insp_per_zone_block`, `per_zone_under_ascertainment_bands`, and `attribution_lag_disclosure`.

### 6.3 Existing source primaries

For the May 28 cycle, the existing primary source-ids (CDC current situation, ECDC, etc.) for `reported_counts.confirmed / suspected / deaths` are PRESERVED. The INRB-UMIE entries remain in the `conflicting_source_ids` lists (corroboration role). The INSP per-zone surface is a NEW field with its OWN primary_source_id pointing at the INRB-UMIE entry, not a promotion of INRB-UMIE to the headline-count primary.

### 6.4 Public_repo_hygiene gate

The new code MUST NOT carry AI-generation markers (`Claude`, `Anthropic`, `Generated with`, `AI-Generated`, `Co-Authored-By`). The new documentation MUST NOT carry em dashes. Existing license declarations preserved: Apache 2.0 for code, CC BY 4.0 for docs and data.

### 6.5 INRB private vs INRB-UMIE public distinction

The workspace memory rule "INRB stays restricted (private_restricted_bytes), DRC-only" refers to PRIVATE partner-only bytes (line lists, identifiable data). The INRB-UMIE consortium GitHub release is PUBLIC MIT-licensed; treating it as `public_bytes` for the parsed CSV values (not the raw Africa CDC workbook, which is `publisher-terms-not-confirmed`) is consistent with the rule. Spec adopts this distinction explicitly to prevent future regression.

### 6.6 Africa CDC workbook redistribution

Raw bytes of `Plan_Decentralisation_Ebola_RDC.xlsx` MUST NOT enter the repository. The loader and modulator consume PARSED CSV values shipped inside the INRB-UMIE artifact, which has its own attribution-required terms. The manifest entry for any Africa-CDC-attributed source carries `license: publisher-terms-not-confirmed` until the data owner clarifies redistribution terms.

### 6.7 Scale-resilience invariant (added v1.1, founder sign-off 2026-05-28)

The methodology MUST produce a valid snapshot at the coarsest data scale available, AND MUST prefer the finest data scale available. The current scale axes are: `national` (rollup only), `per_zone` (INSP per-zone partition), and combinations where one metric is fine-scale and another is coarse-scale (asymmetric attribution; see Finding 3 / §2.3).

Behavioural commitments:

- If `per_zone` INSP data is unavailable for a snapshot date (network outage, upstream delay, INRB-UMIE release rotation), the snapshot remains valid and ships using the next-coarsest available scale, with no degradation of the existing primary reported counts.
- If `per_zone` INSP data IS available, the snapshot uses it for any field whose contract supports per-zone resolution (`insp_per_zone_block`, per-zone components of corridor risk, per-zone ascertainment bands when those promote out of `shadow_in_v1`).
- Every snapshot block records `data_scale_used` with one of `per_zone`, `partial_per_zone`, `national`, or `mixed_with_metric_floor` (the asymmetric attribution case). Downstream consumers MUST observe the scale field and adapt presentation accordingly.
- Cross-snapshot consistency is preserved BECAUSE scale is explicit, not because scale is fixed. Two snapshots at different scales remain comparable when both declare their scale and the consumer respects the declaration.
- `insp_per_zone_consistency_gate` (extended per §7.1) enforces: `data_scale_used` is present and valid; if `per_zone`, the reconciliation residual contract holds; if `national`, the per-zone block is empty or absent and the snapshot does not claim per-zone fields.

The invariant decouples the publication contract from upstream data-availability fluctuations and prevents silent degradation of insight-density during scale degradation. The methodology pulls UP toward the finest available scale; it does not pull DOWN to preserve uniformity with coarser snapshots.

### 6.8 Quality-over-early-calibration posture (added v1.1, founder sign-off 2026-05-28)

Snapshots MUST adopt the finest available data scale (§6.7) and the most-grounded methodology improvements as upstream data quality grows, even when this complicates cross-cycle interpretability of the early calibration ledger blocks (May 20, May 21, May 26 Goma). The methodology is forward-only with regard to calibration provenance (§6.1) AND forward-only with regard to method discipline.

Methodology quality MUST NOT be degraded to preserve cross-cycle simplicity. Specifically:

- A scale upgrade in a later snapshot (e.g., national-only May 20 → per-zone May 28) does not retroactively re-pin May 20; the May 20 block stays at its original scale, while May 28 surfaces the finer scale.
- A method-basis change in a later snapshot (e.g., addition of `INRB_UMIE_INSP_per_zone_v1`) does not invalidate prior blocks whose method_basis is the older value. Both blocks remain on the calibration ledger and are scored against their own method_basis at their own scale.
- Forecast-class changes still respect the parallel-scoring rule (§3) before promotion to primary; this posture does not bypass that requirement. It states that once parallel scoring supports promotion, we promote, even if the promotion makes early-cycle comparisons less mechanical.

Calibration continues to be tracked at whatever scale upstream supports. The underlying system is resilient to scale changes (§6.7) so calibration remains expressible in every snapshot, regardless of how the methodology has improved between the calibration anchor and the resolution.

### 6.9 Sibling-HZ doctrine (added v1.2, Phase 2 validation 2026-05-28)

When two or more INSP-named health zones share the same urban agglomeration (e.g., `karisimbi-cod` and `goma-cod` both within the city of Goma, both carrying the `CD6101` Health-Area-1 zscode prefix in the RGC.cd shapefile), each remains a separate LOVS source zone with independent INSP attribution. The doctrine for handling siblings:

- **Separate attribution.** INSP records cases per HZ jurisdiction. Karisimbi=5 suspected at 2026-05-26 is not double-counted in Goma's 0 suspected (they reflect different HZ catchments within Goma city).
- **Separate corridor entries.** Each sibling participates in corridor risk independently as both a source and a target candidate. The corridor model handles closely-spaced source zones without double-counting because INSP attribution is per-HZ.
- **Metadata-flagged.** `data/zones.json` entries for sibling HZs carry an explicit `sibling_hz_cluster` field naming the parent agglomeration so downstream consumers can group visually when rendering geographic maps.
- **Methodological honesty surface.** The brief / website per-zone view notes when multiple HZs share an urban agglomeration so readers do not misread the data as "two separate cities."
- **Precedent.** The Bunia / Nizi / Rwampara cluster in central Ituri territoire (~12-14 km centroid spacing) is the existing positive precedent; LOVS treats all three as separate source zones with separate corridor entries. Karisimbi-Goma (4.9 km centroid spacing) is the same pattern.

The doctrine generalizes: when future INSP coverage expands to additional HZs in shared agglomerations (e.g., Beni-Cod sibling HZs in N Kivu), the same rules apply automatically. No hardcoded sibling list; the rule is driven by RGC.cd zscode prefix sharing plus explicit metadata.

---

## 7. Gate plan

### 7.1 Existing gates extended

Verified inventory of release-gate modules in `lovs/`:

| Gate | Current invariants | Spec extension |
|---|---|---|
| `lovs/snapshot_contract.py:validate_contract` (line 150) | reported_counts required shape; corridor_watchlist source/target/corridor counts; visibility_method method_basis vocabulary; visibility_method narrative attribution | Add validation of `insp_per_zone_block` (shape from section 5.1), `per_zone_under_ascertainment_bands` (band None or `0 <= lo < hi <= 1`, surface_role from section 5.2 taxonomy), `attribution_lag_disclosure` (required keys). Extend method_basis vocabulary to include `INRB_UMIE_INSP_per_zone_v1`. |
| `lovs/snapshot_contract.py:validate_text_artifacts` (line 403) | Narrative required-fragments match contract | Add required fragments for new method_basis if surfaced in narrative |
| `lovs/snapshot_contract.py:validate_dataset_exports` (line 423) | Source-id canonical-form match between snapshot and dataset CSVs | Extend to also assert the new CSV files (`per_zone_snapshot.csv`, `reconciliation_residuals.csv`, `attribution_lag_disclosure.csv`) are referenced by manifest and by schema |
| `lovs/publication_clock_contract.py:validate` (line 99) | Every `reported_counts.{metric}.primary_source_id` resolves in manifest; publication-clock-only primaries have a cross-surface declaration | No change: INRB-UMIE entries carry explicit `data_as_of` so they fall on the structured-data-date branch, not the publication-clock-only branch |
| `lovs/website_bundle_parity.py:check_website_bundle_parity` (line 131) | LOVS→website byte parity on source_ids and reported counts | Extend with the new field names (`insp_per_zone_block`, `per_zone_under_ascertainment_bands`, `attribution_lag_disclosure`); assert byte parity on each |
| `lovs/cross_surface_parity.py` | Generic cross-surface byte parity | No change in v1 (it is generic; new fields ride the existing pattern) |
| `lovs/public_repo_hygiene.py:scan_tracked_files` (line 111) | No AI-generation markers (`Claude`/`Anthropic`/`Generated with`/`AI-Generated`/`Co-Authored-By`) in tracked files | No change |
| `lovs/source_registry_gate.py` | Source registry contract (monitoring registry rows) | Possible additive: declare a new monitoring-registry row for the INRB-UMIE consortium release pattern if we want recurring freshness checks; founder decision in 12.6 |

### 7.2 New gates required

| Gate | Invariant |
|---|---|
| `lovs/insp_per_zone_consistency_gate.py` | For every metric in the `insp_per_zone_block`, asserts `sum(by_lovs_zone[zone].metric) + unallocated_residual[metric] == national_at_data_date[metric]`. Also asserts the block's source_id resolves to a manifest entry with `publisher` matching "INRB" or "INSP" or "UMIE" (any of the consortium members). Also enforces the scale-resilience invariant (§6.7): `data_scale_used` is declared and valid; when `per_zone`, the reconciliation residual contract holds; when `national`, the per-zone block is empty or absent and the snapshot does not claim per-zone fields; when `mixed_with_metric_floor`, the snapshot declares per-metric scale assignment and the asymmetric contract holds. Refuses snapshots missing the scale declaration. |
| `lovs/attribution_lag_disclosure_gate.py` | Asserts that any snapshot carrying `insp_per_zone_block` with `confirmed_deaths` data ALSO carries `attribution_lag_disclosure.per_zone_confirmed_deaths_note` declaring the trailing-indicator status. Refuses to ship a snapshot that has per-zone deaths without the lag disclosure. |
| `lovs/pcr_modulator_shadow_gate.py` | Asserts that `per_zone_under_ascertainment_bands.surface_role` equals `"shadow_in_v1"` (not `"primary"`) in this spec's v1 scope. A future v2 spec promotes the role; until then, the gate refuses primary promotion to prevent silent forecast changes. |
| `lovs/zone_alias_bridge_gate.py` | Asserts that every zone in `data/snapshot_contract.json:corridor_watchlist.source_zones` has an entry in `data/lovs_zone_alias_bridge.json:lovs_to_inrb`. Refuses snapshot if any source zone is unmapped (would silently lose data on ingestion). |

### 7.3 Test fixtures

| Test file | Coverage |
|---|---|
| `tests/test_insp_per_zone_consistency_gate.py` | Pass on real e40bc9e fixture; fail on contrived sum mismatch |
| `tests/test_attribution_lag_disclosure_gate.py` | Pass on snapshot with disclosure; fail without |
| `tests/test_pcr_modulator_shadow_gate.py` | Pass on `shadow_in_v1` role; fail on `primary` |
| `tests/test_zone_alias_bridge_gate.py` | Pass when bridge covers source_zones; fail on unmapped zone |

---

## 8. Adoption gates and rollout sequencing

### 8.1 May 28 snapshot (description landings)

Goal: ship description-class additions in the May 28 public snapshot without waiting for an outcome cycle.

Landings:
- `lovs/zone_alias_bridge.py`, `lovs/insp_per_zone_loader.py`, vendored data file
- `insp_per_zone_block` populated in `data/snapshot_contract.json` and `data/live-bdbv-2026-output.json`
- `attribution_lag_disclosure` populated
- New CSV files + new XLSX sheets in deliverables
- Brief regenerated with new per-zone view section
- Website new section
- All new gates installed and passing
- All existing gates still passing

Surface_role for INSP per-zone block: `corroborating` (it is a new field with its own primary_source_id, but the BLOCK does not change any existing primary count).

PCR modulator: COMPUTED, surfaced as `shadow_in_v1`. NOT used to change any existing visibility or corridor output. Surfaced for transparency.

Source_zones list expansion (founder decision 2026-05-28: **expansive**): expanded to 14 zones for May 28. The existing 11 zones plus 3 high-priority additions (Aru, Komanda, Mambasa) per Finding 1 implications and the scale-resilience invariant (§6.7). The promotion criterion is deterministic and codified in `data/zones.json` source_zone promotion rules so the source_zones list grows monotonically as INSP coverage expands AND contracts gracefully under §6.7 if INSP coverage shrinks. Future cycles may extend further per the same threshold rule (e.g., Kalunguta, Karisimbi, Damas, Oicha, Fataki, Kyondo, Rimba as their case-load or epidemiological-significance signals cross threshold).

### 8.2 First outcome resolution post May 28

Goal: parallel-score the PCR modulator against the species-default band.

For every corridor with a calibration block resolving in this window, compute the visibility band and corridor risk TWICE: once with species default ascertainment, once with PCR-modulated. Compare brier scores and log scores. Surface both in the post-resolution methodology note.

Promotion criterion: PCR-modulated score is not WORSE than species-default by a statistically meaningful margin (specifics to define in the per-cycle plan).

### 8.3 Subsequent cycle (PCR modulator promotion if scoring supports)

Goal: promote `per_zone_under_ascertainment_bands.surface_role` from `shadow_in_v1` to `primary`. This is a forecast change and a new pre-committed claim, so it requires:
- Explicit founder go-signal
- A new calibration block pinned at the promotion cycle's snapshot
- The shadow gate refusing this promotion is REPLACED by a primary-mode gate

### 8.4 v2 spec (bilateral modulator)

Out of scope for v1. Documented in section 9 (open questions). Requires partner-supplied USAGE data (tests performed per zone, not just budgeted) to ground the negative-signal direction.

---

## 9. Open methodological questions

### 9.1 Bilateral modulator for absent-from-PCR-plan zones (Rwampara reference case)

The current modulator is asymmetric (positive signal only). For zones that are high-suspected-load AND absent from the Africa CDC decentralisation plan (Rwampara, Bambu, Katwa, Kilo, Miti-Murhesa, Nizi), the operational reality (sample referral to a hub with degraded TAT and specimen integrity) plausibly LOWERS effective ascertainment below species default. The asymmetric modulator cannot express this.

Two candidate paths:

- **Path A:** Bilateral modulator that lowers `hi` when (absent-from-plan AND high-suspected-load). Mathematically symmetric, but requires defining a threshold for "high suspected load" and risks introducing a new defect class if the threshold drifts.
- **Path B:** Per-zone narrative annotation. The snapshot carries a `pcr_absent_from_plan_zones` list with a per-zone "ascertainment likely below species median" caveat for high-load absent zones. The MATH does not change; the DISCLOSURE does.

Recommendation for v1: Path B. Defer Path A to a v2 spec that has partner USAGE data (tests performed per zone) to ground the negative signal. Founder decision required.

**Founder decision 2026-05-28:** Path B (annotation in `pcr_absent_from_plan_zones`) lands in Plan A. Path A (bilateral modulator) is queued as Plan D under standard gate/hook discipline; v2 spec to be authored when partner usage data is available. The quality-over-early-calibration posture (§6.8) governs: when partner data arrives, the bilateral modulator promotes through normal parallel-scoring discipline, even if it complicates retrospective interpretation of pre-Plan-D snapshots.

### 9.2 Attribution-lag retrospective inclusion

Should we retroactively update per-zone confirmed_deaths in past snapshots as INRB clinical review catches up? Forward-only ledger says NO, but a separate "retrospective audit" surface in deliverables could carry the corrected view without disturbing the pinned blocks. Founder decision required.

**Founder decision 2026-05-28:** Include in v1 deliverables as a separate `retrospective_attribution_audit/` surface under `deliverables/`. The audit ships per-zone confirmed_deaths reconciliations as INRB clinical review catches up, with its own gate (`retrospective_attribution_audit_gate.py`) enforcing the no-pinned-block-mutation invariant. The audit is a new surface, not a re-statement of past snapshots. Pinned blocks (May 20, May 21, May 26 Goma) remain frozen at their original per-zone attribution. Plan A implements the audit surface; the first audit run executes against the May 26 Goma block's per-zone deaths as a worked example.

### 9.3 Zero-signal audit across other priors

The Finding 4 doctrine applies broadly. Audit candidates: case_definition_version effect on uncertainty drivers, history-snapshot count effect on method_basis, ARC's serial interval prior with small-sample partner overrides. Audit to be scheduled in a future cycle.

### 9.4 INSP per-zone view scope: 11-zone vs full-INSP source_zones

Conservative: keep source_zones at the existing 11 in v1, add the INSP-derived per-zone block as a CORROBORATING surface but do not change the corridor_watchlist input set. Promotes the description without changing the model's input cardinality.

Expansive: expand source_zones to include INSP-promoted zones (Aru, Komanda, Mambasa at minimum) in v1 itself. Reduces ecological-fallacy risk immediately but is a larger surface change.

Recommendation: founder decides. Spec is written to support either choice via the schema's separation of `insp_per_zone_block.by_lovs_zone` (always full INSP coverage of the bridge) from `corridor_watchlist.source_zones` (founder-controlled subset).

**Founder decision 2026-05-28:** Expansive AND scale-resilient. The system prefers and leverages the finest available scale per §6.7; when fine-scale data is available the snapshot surfaces it via `insp_per_zone_block.by_lovs_zone` AND adds new zones to `corridor_watchlist.source_zones` per the deterministic promotion criterion; when fine-scale data is unavailable the system gracefully degrades to coarser scale without breaking the snapshot contract. The choice is not 11 vs 19 statically: it is "maximize fine-scale coverage subject to bridge maturity + zone-level epidemiological significance threshold." Plan A starts at 14 (the existing 11 plus Aru, Komanda, Mambasa) and the threshold logic governs future expansion automatically.

---

## 10. Non-goals

- Not changing the BDBV species priors themselves.
- Not promoting any private (partner-only) INRB bytes to the public manifest.
- Not committing Africa CDC raw workbook bytes.
- Not modifying or invalidating any pinned calibration block.
- Not changing the partner-facing `run_local.py` schema in a backward-incompatible way.
- Not adding a new public claim that requires retrospective restatement of any existing pre-committed forecast.
- Not changing the source_attribution_lag doctrine for HEADLINE counts (CDC remains primary for headline confirmed/suspected/deaths in v1).
- Not introducing a website surface that would require a public re-pinning of the May 20 / May 21 / May 26 calibration blocks.

---

## 11. Per-cycle plan stubs (to expand into engineering-pipeline plans after spec sign-off)

### 11.1 Plan A: May 28 description landings

Covers section 8.1 in full, plus the founder-2026-05-28 expansive source_zones decision (§8.1), the retrospective attribution audit surface (§9.2 founder decision), the Path B annotation for absent-from-PCR-plan zones (§9.1 founder decision), and the scale-resilience + quality-over-early-calibration invariants (§§6.7, 6.8). Engineering pipeline phases 1-8 apply (Large class, new public contract + multi-surface). Expected artifacts:

- `.process/2026-05-28-may28-insp-per-zone-landing/plan.md` (this Plan A)
- `.process/2026-05-28-may28-insp-per-zone-landing/glossary.md` (Large class requires ≥5 terms)
- `.process/2026-05-28-may28-insp-per-zone-landing/validation.md` (must verify every surface map entry against file:line)
- Implementation under `lovs/`, `tools/`, `tests/`, `deliverables/`, `apps/site/app/bdbv-2026/`, `brief/`, root docs
- `.process/.../review.md` (fresh-context reviewer with no implementation history)
- `.process/.../polish.md`
- `.process/.../stress.md` (production-load envelope: dataset exporter under repeated builds, gate runtime budgets)
- `.process/.../red-team.md` (threat model on the new gate surface, especially `pcr_modulator_shadow_gate` and `retrospective_attribution_audit_gate`)
- `.process/.../stage.md` (rolling release: snapshot promotion order, kill-switch, monitoring)

### 11.2 Plan B: First-outcome-cycle parallel scoring

Covers section 8.2. Engineering pipeline phases 1-7 (Small class, no production deploy of new code, only artifact production).

### 11.3 Plan C: PCR modulator promotion (post parallel scoring)

Covers section 8.3. Engineering pipeline phases 1-8 (Critical class because it is a forecast change with calibration-provenance implications).

### 11.4 Plan D: v2 bilateral modulator design (deferred)

Covers section 9.1 Path A. Requires partner usage-data agreement first.

---

## 12. Sign-off questions for the founder

1. Adopt this spec at v1? Or amend before adopting?
2. Section 8.1 source_zones scope: conservative (keep 11) or expansive (add Aru, Komanda, Mambasa) for May 28?
3. Section 9.1 bilateral modulator: Path B (annotation only) for v1, defer Path A?
4. Section 9.2 retrospective audit surface: include in v1 deliverables or defer?
5. Section 9.4: which INSP per-zone scope?
6. Is there any surface I missed? (Specifically: surfaces that already exist in your head as the founder but are not on the surface map.)

### 12.1 Founder answers (recorded 2026-05-28)

1. **Adopt at v1.** Spec adopted; v1.1 amendments capture sign-off + scale-resilience (§6.7) + quality-over-early-calibration posture (§6.8).
2. **Expansive.** Aru, Komanda, Mambasa added to source_zones in Plan A. Threshold-based promotion criterion governs future expansion automatically.
3. **Path B in v1, Path A queued as Plan D under standard gate/hook discipline.** Path A promotes to v2 when partner USAGE data is available.
4. **Include retrospective audit in v1.** New `deliverables/retrospective_attribution_audit/` surface with its own gate (`retrospective_attribution_audit_gate.py`). Pinned blocks remain frozen.
5. **Maximize fine-scale; resilient under coarser scale.** §§ 6.7 + 8.1 govern. The system prefers finest available scale; declares scale used per snapshot; degrades gracefully when fine-scale data is unavailable. Plan A starts at 14 source zones and grows monotonically.
6. **No additional surfaces identified.** v1.1 spec is the surface set Plan A targets.

Meta posture: methodology quality MUST NOT be degraded to preserve early-calibration cross-cycle simplicity. Calibration is tracked at whatever scale upstream supports; underlying system resilience (§6.7) preserves calibration expressibility in every snapshot.

---

## 13. Provenance and updates

- v1 draft authored 2026-05-28 from the POC artifacts at `.process/2026-05-28-insp-per-zone-and-pcr-capacity-poc/` plus the Earth wiki `reference:bdbv-methodology-improvement-brief`.
- Parallel agent enumerations (website surface, gate audit) folded in before sign-off; website inventory at §4.5, gate inventory at §7.1.
- v1.1 amendment 2026-05-28: founder sign-off recorded (§12.1); scale-resilience invariant added (§6.7); quality-over-early-calibration posture added (§6.8); insp_per_zone_consistency_gate extended for scale enforcement (§7.1); source_zones decision recorded expansive (§8.1); founder decisions inlined at §§ 9.1, 9.2, 9.4; Plan A scope updated (§11.1) for Large class + retrospective audit surface; mainline integration opened at `.process/2026-05-28-may28-insp-per-zone-landing/`.
- v1.2 amendment 2026-05-28: Phase 2 validation complete (validation.md gate passed); sibling-HZ doctrine added (§6.9) from Karisimbi-Goma grounding finding; Plan A scope locked at 18 source_zones per threshold criterion `THRESHOLD_SUSPECTED_LOW=4` against the e40bc9e tarball (existing 11 plus aru + damas + karisimbi-cod + komanda + mambasa + oicha + rimba, all verified in RGC.cd shapefile); Sp6 spike (sync-script public-redaction contract for normalized_content) deferred to Plan A step 5 pre-flight; 10 validation amendments folded into Plan A plan.md v2.
- Each subsequent version of this spec is appended at the bottom of this file (forward-only doctrine for the spec itself).
