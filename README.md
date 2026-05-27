# Bundibugyo virus, DRC and Uganda, 2026: public-evidence methods brief and code

This repository accompanies a public-evidence methodology brief on the 2026 Ebola disease outbreak caused by Bundibugyo virus (BDBV). It packages a stdlib-only Python pipeline called the **Latent Outbreak Visibility System (LOVS)** that estimates three quantities of interest in early-stage filovirus surveillance from open, aggregated reporting:

1. **Ascertainment gap**: the fraction of underlying cases captured by current public reporting.
2. **Detection depth**: the posterior over number of person-to-person transmission generations that had likely occurred before the outbreak became publicly visible.
3. **Inter-zone corridor risk**: descriptive watch-point intervals for movement between source and target health zones, paired with pre-committed calibration points.

The companion brief applies LOVS to the current dated snapshot and ships a published webpage at <https://www.arcede.com/bdbv-2026>. The scripts in this repository reproduce that public artifact from frozen inputs.

**What this is.** This is a reproducible public-evidence methods artifact. It shows how open outbreak reporting can be reconciled across publishers, source dates, retrieval dates, and model-use status without treating public reporting as complete line-list surveillance.

**Bottom line.** This is a methodology contribution in support of the responding authorities. It is **not** an official outbreak dashboard, a case-management system, a contact-tracing system, a forecast, a travel advisory, or a deployment recommendation. The 26 May 2026 snapshot indicates the public reporting picture captures only an estimated 40 to 46 percent of laboratory-confirmable cases and that detection occurred after multiple silent transmission generations, both intrinsic to early-stage filovirus surveillance. The corridor watchlist is a pre-committed calibration test of the method's uncertainty quality, and on historical data the method does not yet discriminate individual corridors above chance.

**Authorities and standing.** The Democratic Republic of the Congo (DRC) Ministry of Public Health, Hygiene and Social Welfare officially declared this outbreak on 15 May 2026 and is the lead authority on the DRC response, with the National Institute of Biomedical Research (INRB) confirming BDBV by polymerase chain reaction (PCR). The Uganda Ministry of Health (MoH) is the lead authority on the Uganda response and initially confirmed imported cases in Kampala on 15-16 May 2026. The World Health Organization (WHO) Director-General determined a Public Health Emergency of International Concern (PHEIC) on 16 May 2026; WHO published the public statement on 17 May 2026. The Africa Centres for Disease Control and Prevention (Africa CDC), on the recommendation of its Emergency Consultative Group, declared a Public Health Emergency of Continental Security (PHECS) on 18 May 2026. **This work is a methodology contribution in support of those authorities. It is not a substitute for them and does not speak on behalf of any of them.**

**Author:** [Frans Moore](https://www.linkedin.com/in/frans-moore/), [frans@arcede.com](mailto:frans@arcede.com).

## Start here

Different readers should use different parts of this repository:

- **Public-health readers and responders:** start with the published page, then read the headline findings, methodology caveat, public source-use policy, and "What this brief is NOT" sections below. You do not need to run the pipeline to interpret the public snapshot.
- **Partners with local or non-public data:** use [`FORKING.md`](FORKING.md) and `point_of_care_input.example.json` to run the method locally against your own figures. The local-data runner keeps your inputs on your machine.
- **Technical auditors and reproducibility reviewers:** use the "Reproduction and audit" section. `refresh_pipeline.py`, `make_brief.py`, and `export_public_health_dataset.py` are audit/rebuild tools, not prerequisites for reading the brief.

**Why a methodology brief, today.** As of the 26 May 2026 snapshot, the most prominent public quantitative output for this outbreak remains the [joint WHO-Imperial College MRC GIDA report (20 May 2026 update)](https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-update-20-05-2026/) estimating **400-900 total cases in DRC** (values over 1,000 not excluded), via population-movement extrapolation and deaths-back-projection through the case-fatality ratio. (The 20 May update supersedes the 18 May report: it corrected the CFR scenario set from 24/30/40 to 26/33/40 percent and updated the deaths input from 88 to 131.) That report does not publish a reporting-completeness posterior, a pre-committed calibration set, or a cross-border corridor-risk view with date-stamped resolution. Within the archived source set for this snapshot, the other reviewed public outputs (WHO Disease Outbreak News 2026-DON602, the WHO AFRO Weekly Sitrep, Africa CDC PHECS declaration, and US CDC HAN 00530) do not include this combination. No comparable public output from the WHO Hub for Pandemic and Epidemic Intelligence in Berlin or the US CDC's Center for Forecasting and Outbreak Analytics was identified in this review as of the snapshot date. That gap is what this brief is built to fill. It complements the WHO-Imperial estimate; it does not replace it.

## What this adds beyond the size estimate

The Imperial College MRC GIDA estimate is the academic reference for outbreak size, and this brief treats it as such. The deaths-back-projection used here follows Imperial's published Method 2 (the growth-corrected `C = D · (1 + r/β)^α / CFR`), uses the same CFR scenario set (26%, 33%, 40%, grounded in the US CDC 55-deaths / 169-cases aggregate) and the same central doubling time (14 days), and reproduces Imperial's 20 May Table 2 figures to within a few cases. Where this brief overlaps Imperial, the intent is to match a validated method, not to differ from it.

The distinct value is in being a convergence point for evidence that is otherwise scattered across many publishers, and in three outputs the size estimate does not provide:

1. **Synthesis across the full public source set, with provenance.** The snapshot reconciles WHO Disease Outbreak News, the WHO PHEIC statement, WHO Director-General remarks, the WHO AFRO weekly situation report, the Africa CDC PHECS declaration, ECDC, the US CDC HAN and situation summary, the Imperial estimate, and a consensus aggregator into one source-conflict-aware view. Every headline number names the dated source it came from and the sources it conflicts with; provenance is byte-archived where licensing permits and recorded by hash where it does not; audited methodology claims carry a machine-checkable evidence chain (`data/evidence-chains.json`).
2. **An ascertainment (visibility) nowcast.** A reporting-completeness posterior (about 40 to 46 percent for this snapshot), a publication-latency interval, and a confirmation-backlog interval. This quantifies how much of the outbreak the public picture is likely missing, which a point-in-time size estimate does not.
3. **A pre-committed, date-stamped, scored corridor-risk test.** Twelve active corridor points are pinned across two append-only blocks: four from 20 May 2026 resolving 19 June 2026, and eight from 21 May 2026 resolving 20 June 2026. They carry explicit uncertainty ranges, are scored with proper scoring rules (Brier, interval score, calibration error), and are benchmarked on the 2014 West Africa epidemic with a rolling-origin robustness layer. This is a falsifiable methodology commitment, not a forecast for the response.

In short: the size estimate is where this brief validates against the field's reference; the multi-source synthesis, the visibility nowcast, and the pre-committed corridor calibration are the work the reference does not do.

## Public source-use policy

Operational partners may hold line lists, contact-tracing records, laboratory timestamps, genomic data, field investigation notes, and internal dashboards that are more complete than public reporting. This repository is designed for the narrower public-source layer.

- Official sources can support scored public claims after archiving and evidence-chain review.
- Credible media, local reporting, and watch-list signals can trigger source-review work, but are not promoted into counts, source-load vectors, or model inputs unless independently confirmed by official or otherwise citable primary sources.
- Radio Okapi and similar local reporting are treated as source-review signals: useful for finding emerging geography or response context, not sufficient alone for confirmed counts or corridor source loads.
- Sources still under source-review, superseded raw captures, and restricted publisher bytes are kept out of public model inputs and public source clocks until promoted through the release gates.
- Every public numerical claim should remain traceable to a source ID, publication/retrieval dates where available, and a clear model-use status.

## What this brief is NOT

- **NOT a travel advisory.** NOT a recommendation to restrict cross-border movement, close markets, or redirect commercial activity.
- **NOT a deployment recommendation.** The named corridors are descriptive watch points for further investigation, not predictions of where the outbreak will spread. The corridor numbers are source-load-sensitive watchlist intervals, not validated corridor-specific probabilities; the current-outbreak constants remain transparent engineering heuristics until fitted or externally validated.
- **NOT a critique of the national response.** Ascertainment gaps and late detection of filoviruses are intrinsic to the pathogen and to the operational context (security, displacement, co-circulating pathogens). The DRC Ministry of Public Health and Uganda Ministry of Health are leading; INRB confirmed BDBV by PCR within days. This brief takes the national declarations as the authoritative timeline.

## What is in this repository

After choosing a reader path above, these are the three analysis surfaces carried side-by-side, plus the local-data path for partners:

- **Historical calibration on the 2014 West Africa Ebola epidemic.** A retrospective test of the underlying method against 2014 data where the eventual outcomes are public knowledge (Backer & Wallinga 2016 substrate; 62 prefectures × 74 weeks). Three runs are reported: without local context, with country-level local context, and with district-level local context. This is the academically grounded zone. The 2014 substrate was a Zaire-species outbreak; transferring the method to a Bundibugyo-species outbreak carries species-transfer uncertainty.
- **Pre-committed methodology calibration points for the 2026 outbreak.** Active calibration points live in append-only dated blocks. The 20 May 2026 block pins four corridors resolving 19 June 2026; the unpublished 21 May 2026 block pins eight additional designed-sample corridors resolving 20 June 2026. They resolve against publicly available reports from the DRC Ministry of Public Health, the Uganda Ministry of Health, WHO, and Africa CDC. **These calibration points are NOT recommendations for the active public-health response.**
- **Current-outbreak view.** The methodology applied to reconciled public reporting through 26 May 2026, with asynchronous publisher cadences carried as source conflicts instead of forced down-revisions. Read it for shape, not for skill: no predictive-skill claim is made for the 2026 outbreak.
- **Local-data runner for partners.** If you hold ground-truth data the public sources lack (per-zone case counts, local transport patterns), see [`FORKING.md`](FORKING.md): copy `point_of_care_input.example.json`, fill in your figures, and run `python run_local.py --input my_data.json` for a visibility-adjusted underlying-case view and a corridor watchlist ranking. Nothing leaves your machine.

## Headline findings (as of 26 May 2026)

Based on public reporting across WHO DON602, the WHO PHEIC statement, the WHO African Region Weekly External Situation Report 01 (data as of 18 May 2026), the Africa CDC PHECS declaration (18 May 2026), ECDC (19, 21, 25, 26, and 27 May 2026), WHO Director-General remarks (20 and 22 May 2026), WHO IHR Emergency Committee temporary recommendations (22 May 2026), the US CDC Current Situation page (21, 23, 24, and 25 May 2026), the INRB / INSP / UMIE Ebola_DRC_2026 GitHub release (build-2026-05-27-059661a, DRC-only national_moh, data-as-of 26 May 2026, restricted), and an archived consensus aggregator through 20 May 2026. Total counts span:

- **128 confirmed cases** as the current country-scope confirmed endpoint, anchored to the ECDC 27 May 2026 page citing "On 26 May, the Ministry of Health in DRC reported a total of 121 confirmed cases" plus Uganda's 7 confirmed cases. The DRC component (121) is cross-corroborated by the INRB build-2026-05-27 national_moh release (DRC-only, restricted). The CDC 25 May 112 total (105 DRC + 7 Uganda), ECDC 25 May 101 confirmed, and DRC MoH dashboard's 24 May all-published-bulletins aggregate of 112 confirmed DRC are retained as dated conflict anchors; CDC and WHO AFRO have not yet published an edition that catches up to the DRC MoH 26 May release. Earlier official / regional anchors are 10 confirmed in the 17 May WHO PHEIC statement (8 Ituri + 2 Kampala; the reported Kinshasa case was deconfirmed by INRB), 30 confirmed on the 19 May ECDC outbreak page, 53 total in WHO's 20 May remarks, 84 total in WHO's 22 May Member State briefing, 88 total on CDC's 23 May page, and 106 total on CDC's 24 May page.
- **395 to 1077 suspected/reported cases** spanning Africa CDC PHECS (18 May 2026: ~395), ECDC (19 May: over 500; 21 May: approximately 600), CDC Current Situation (21 May: 575), the archived 20 May consensus aggregator (653), WHO Director-General remarks on 22 May (almost 750), the DRC MoH all-published-bulletins dashboard aggregate on 24 May (854 reported cases), CDC Current Situation on 24 May (904 suspected DRC cases), ECDC on 25 May (904 suspected), US CDC Current Situation on 25 May (906 suspected DRC cases), and the ECDC 27 May 2026 page citing DRC MoH 26 May (1077 suspected DRC cases, the reported endpoint as the highest valid primary; INRB build-2026-05-27 cross-corroborates 1077).
- **106 to 238 deaths** spanning Africa CDC PHECS (18 May: 106), ECDC (19 May: 130; 21 May: WHO-derived 139), the archived 20 May consensus aggregator (144), CDC Current Situation (21 May: 148 suspected deaths), WHO Director-General remarks on 22 May (177 suspected deaths; seven confirmed deaths in DRC), the DRC MoH all-published-bulletins dashboard aggregate on 24 May (179 registered deaths), ECDC on 25 May (119 suspected DRC deaths), US CDC Current Situation on 25 May (223 suspected DRC deaths and ten confirmed DRC deaths), and the ECDC 27 May 2026 page citing DRC MoH 26 May (238 suspected DRC deaths and 17 confirmed DRC deaths, the highest valid headline primary and the reported endpoint). The INRB build-2026-05-27 (DRC-only national_moh) separately reports 246 DRC suspected deaths on the same date and is retained as a slightly-higher DRC-only conflict anchor per NUMBERS_AUDIT Rule 3 (no mixing across publication compositions). Includes **four healthcare worker deaths at Mongbwalu General Referral Hospital within a four-day span** per WHO DON 602.

Method findings:

1. **Ascertainment gap is wide.** Reporting completeness 50% uncertainty range: approximately `[40%, 46%]` for the 26 May snapshot (regenerated by `python refresh_pipeline.py`; exact endpoints rounded to whole percentage points to avoid implying precision beyond the model's resolution). Consistent with early-stage filovirus surveillance under any system: inherent reporting delay (Rosello 2015 eLife BDBV Isiro 2012 onset-to-notification default, with Camacho 2015 PLOS Currents EBOV-Zaire retained as a faster-reporting sensitivity comparator), historical late detection of Bundibugyo-species outbreaks (Wamala 2010 Emerging Infectious Diseases), Ituri-region operational realities (security context per ACLED, internally displaced populations, malaria and other febrile/GI/arboviral/influenza-like clinical differentials).
2. **Detection occurred after multiple silent transmission rounds.** Posterior probability of at least three person-to-person transmission generations before detection: essentially 100% with the current confirmed case count.
3. **Corridor watch list (descriptive, not ranked).** The current 76-corridor watchlist at a 30-day horizon spans 0.7-20.4% lower bounds and 1.8-49.3% upper bounds. The current correction is source-attribution lag, not missing cases: it separates the 128 confirmed cases in the headline aggregate from the DRC MoH cumulative per-health-zone source-load vector. Corridor risk uses 79 confirmed cases that are officially zone-attributed across 11 DRC MoH source zones, while the remaining 49 confirmed cases stay as unallocated headline context until an official zone table assigns them.
4. **Fifteen active pre-committed methodology calibration points** across three 30-day blocks. The May 20 block resolves 19 June 2026; the May 21 designed-sample block resolves 20 June 2026; the May 26 Goma-target block resolves 25 June 2026. Each point is paired with the model's ascertainment-adjusted 50% uncertainty range and will be scored against publicly available DRC MoH, Uganda MoH, WHO, and Africa CDC reports.

## Methodology caveat (load-bearing)

The snapshot carries two count concepts. The headline public count is 128 confirmed cases as of 26 May 2026 (ECDC 27 May 2026 citing DRC MoH on 26 May: 121 DRC plus Uganda's 7 confirmed cases, with INRB build-2026-05-27 cross-corroborating the DRC component). The CDC 25 May (105 DRC + 7 Uganda = 112) and DRC MoH 24 May dashboard aggregate (112 confirmed DRC) drop to dated conflict anchors; CDC and WHO AFRO have not yet published an edition that catches up to the DRC MoH 26 May release. SitRep 009 and the INRB processed health-zone layers (latest at 2026-05-24) remain source-review because no `dateRapportage` is exposed and no official cumulative PDF for the May 26 data date has been published. The corridor source-load vector is spatially attributed: the DRC MoH SitRep MVE N 007/MVB_17/2026 PDF cumulative Table IV reports 79 confirmed cases across 11 DRC MoH source zones as of 21 May. The corridor model uses that per-zone vector because it is the newest officially zone-attributed cumulative table in the archive. It does not scale the vector up to the 128 country-scope headline aggregate; the remaining 49 confirmed cases stay as unallocated headline context.

## Historical calibration: no context, country-level context, district-level context

The method has been tested against the 2014 West Africa Ebola epidemic in three runs of progressively richer local context. Local context is four variables: population density, road density, healthcare access (distance to reference hospital), and conflict intensity (political-violence intensity from the Armed Conflict Location and Event Data (ACLED) project 2014).

| Metric | No local context | Country-level | District-level | Change country vs district |
|---|---|---|---|---|
| Brier score (probability accuracy; lower is better) | 0.0586 | 0.0590 | 0.0590 | +0.0000 |
| Interval score (Bracher 2021; uncertainty quality; lower is better) | 0.1002 | 0.0649 | 0.0649 | +0.0000 |
| Calibration error (predicted vs observed frequency; lower is better) | 0.0391 | 0.0500 | 0.0500 | +0.0000 |
| Base-rate appearance | 6.10% | 6.10% | 6.10% | reference |

**Honest interpretation.** Adding local context at country level tightens uncertainty (interval score down by roughly 35%). It does NOT improve discrimination of individual corridors above chance. District-level context does not add further. The 2014 substrate is a Zaire-species outbreak, and species-transfer uncertainty is not separately quantified here.

**The structural reason** the district-level run does not improve over country-level: the realistic range of the combined-context multiplier for 2014 West Africa data sits below a safety floor for nearly all district pairs. Approximately 99.8% of (source, target) pairs hit the floor uniformly under both country-level and district-level local context. The floor absorbs the granularity distinction.

**Candidate next-levers, ranked by expected impact:**

1. Mobility data (call detail records or surveyed transport flows; Wesolowski 2016 Journal of Infectious Diseases).
2. Time-varying context (real-time ACLED political-violence intensity; real-time Ministry of Health reporting cadence).
3. Re-engineering how the four context variables combine so the realistic combined multiplier lifts above its safety floor, then revisit district-level context.
4. A richer transmission model (e.g. metapopulation Susceptible-Exposed-Infectious-Recovered (SEIR) or Hawkes process).

The calibration-error increase from 0.039 to 0.050 between no-context and context runs is small and most likely an artifact of the small evaluation set (five as-of dates).

## Known blindspots and calibration design notes

The brief is honest about what it does not yet do. The most important blindspots, surfaced during the 20-21 May 2026 validation cycle:

- **Mahagi as a source zone is still outside the model, while selected Arua/Nebbi target corridors are now pinned.** Mahagi (DRC) and Goli (Uganda) form one of DRC's busiest land border crossings on the East African Northern Corridor, with persistent trade flow and 95,000+ refugees at the Rhino Camp settlement near Arua (UNHCR, late-2025). The 21 May 2026 revision extended the candidate-target list to include Arua District and Nebbi District, and the unpublished 21 May designed block pins selected Arua/Nebbi target corridors before publication. Mahagi (DRC) is still not modeled as a source zone, because no WHO or Africa CDC source names Mahagi health zone as case-affected; a non-destructive sensitivity check (`snapshot_sensitivity.py`) shows where the omitted Mahagi-to-Arua corridor would rank under an explicit equal-burden counterfactual.

- **The active calibration corridors preserve their original pinned probabilities.** The May 20 and May 21 calibration blocks are not re-derived after the May 22 spatial correction; that is intentional. The current watchlist now uses zone-attributed source loads, while the calibration ledger remains an immutable record of what was pre-committed before the correction.

- **The corridor `mongbwalu → beni-cod` may be confounded by the outbreak's own expansion.** Beni Health Zone sits in North Kivu Province, which the WHO AFRO 18 May situation report identifies as already part of the outbreak footprint. A positive resolution on this corridor would not cleanly attribute to source-zone-to-target-zone transmission; it may simply reflect Beni already being part of the active outbreak.

- **Conflict-state coverage is qualitative, not yet quantitative.** The brief invokes CODECO and ADF activity in Ituri/North Kivu and 7.3 million IDPs in eastern DRC (UNHCR late-2025) as descriptive context for the ascertainment-gap reasoning, but the per-zone conflict-intensity input is the 2014 West Africa ACLED snapshot used in the Mode A historical calibration. A 2026 ACLED snapshot for eastern DRC is a documented next-lever.

These blindspots are surfaced honestly so that a responder reading the brief can adjust how much weight to give each output. They are tracked in the source repository's commit history; updates will land in subsequent snapshots.

## Reproduction and audit

Stdlib-only Python (no `numpy`, no `scipy`, no `requests`). Tested on Python 3.11+.

These commands are for reviewers who want to rebuild or audit the frozen public snapshot. They are not required to read the brief, use the workbook, or run the local-data path in `FORKING.md`.

PDF rendering uses headless Chrome (see `make_brief.py`); Chrome embeds run-time timestamps, so the PDF is functionally identical across runs but not byte-identical. The HTML and SVG outputs are byte-deterministic.

```bash
# 1. Carved-out tests (57 tests; <1 second)
python3 -m unittest discover -s tests -p "test_lovs_*.py"

# 1b. Grounding evidence-chain + NUMBERS_AUDIT validation
python3 -m lovs.lovs_evidence

# 2. Historical-calibration reproduction (no context / country-level / district-level)
python3 -c "
import pathlib
from lovs import lovs_validation
sp = pathlib.Path('data/west-africa-prefecture-weekly.json')
cp_country = pathlib.Path('data/covariates-wa-2014.json')
cp_district = pathlib.Path('data/covariates-wa-2014-v3.json')
no_context = lovs_validation.mode_a_backtest_wa_2014(sp)
country_context = lovs_validation.mode_a_backtest_wa_2014_t3(sp, cp_country)
district_context = lovs_validation.mode_a_backtest_wa_2014_t3(sp, cp_district)
for label, r in (
    ('no context', no_context),
    ('country-level', country_context),
    ('district-level', district_context),
):
    print(f'{label}: Brier={r.next_zone_brier:.4f} Interval={r.next_zone_wis:.4f} Calibration={r.expected_calibration_error:.4f}')
"
# Expected (deterministic seed):
# no context:     Brier=0.0586 Interval=0.1002 Calibration=0.0391
# country-level:  Brier=0.0590 Interval=0.0649 Calibration=0.0500
# district-level: Brier=0.0590 Interval=0.0649 Calibration=0.0500

# 3. Regenerate the current pipeline output snapshot
python3 refresh_pipeline.py
# Output: data/live-bdbv-2026-output.json

# 4. Regenerate the brief + visuals from frozen inputs
python3 make_brief.py
# Output: brief/brief.html, brief/visuals/*.svg, deliverables/brief.pdf

# 5. Export the public-health evidence workbook and CSV sidecars
python3 export_public_health_dataset.py
# Output: deliverables/public-health-dataset/lovs-public-health-dataset.xlsx
```

## What this repository does

- Quantifies the ascertainment gap (what is in the public picture versus what the underlying outbreak likely contains).
- Quantifies detection depth (how late detection occurred relative to silent transmission).
- Lists corridors between affected zones with ascertainment-adjusted risk intervals.
- Pre-commits methodology calibration points with explicit, machine-checkable resolution criteria.
- Reproduces all numbers from frozen inputs.
- Cites every prior and scoring choice to peer-reviewed literature (see `CITATIONS.md`).
- Carries a machine-validated evidence-chain registry for audited claims (`data/evidence-chains.json`).
- Exports a public-health evidence workbook from the pinned snapshot, source manifest,
  evidence-chain registry, and calibration ledger (`python3 export_public_health_dataset.py`).
  The workbook includes corrections/gaps and does not redistribute restricted Imperial
  Table 3 raw row data.

## What this repository does NOT do

- It is **not a forecast** that the outbreak will or will not spread. The pre-committed calibration points are checkable artifacts to evaluate the method's uncertainty quality at resolution.
- It **does not replace field epidemiology**. Line-listing, contact tracing, genomic sequencing, and clinical reasoning are where outbreak control happens.
- It **does not yet identify specific corridors better than chance**. Historical calibration shows uncertainty estimates tighten with local context, but the method's ability to single out individual corridors above chance does not improve. The next investment direction is mobility data.
- It is **pre-committed, not regulatory**. Scoring is local and against open data; no authority depends on it. Independent replication is welcomed.

## Repository structure

```
bdbv-2026-lovs/
├── README.md                          (this file)
├── LICENSE                            Apache 2.0 (code)
├── LICENSES.md                        license split: Apache 2.0 code + CC BY 4.0 docs/data
├── NOTICE                             attribution notices (Apache 2.0)
├── CITATIONS.md                       full bibliography
├── make_brief.py                      regenerates brief + visuals from frozen inputs
├── refresh_pipeline.py                regenerates pipeline output from current snapshot
├── lovs/                              the methodology package (stdlib-only Python)
│   ├── lovs_archive.py                Module A: append-only SHA-256 archive
│   ├── lovs_reconciler.py             Module B: multi-source case-state reconciler
│   ├── lovs_visibility.py             Module C: ascertainment nowcast
│   ├── lovs_transmission.py           Module D: transmission plausibility (branching process)
│   ├── lovs_next_zone.py              Module E: corridor risk (gravity-style hazard)
│   ├── lovs_spillover.py              Module F: spillover narrative
│   ├── lovs_gap.py                    Module G: visibility-gap analysis
│   ├── lovs_priors_bundibugyo.py      BDBV priors (incubation/serial: Wamala 2010, MacNeil 2010; species check: Albariño 2013)
│   ├── lovs_covariates.py             Local-context loader + edge-weight modifier
│   ├── lovs_live_ingest.py            WHO DON HTTPS fetcher (idempotent, SHA-256 dedup)
│   ├── lovs_evidence.py               Evidence-chain registry validator
│   ├── lovs_validation.py             Historical-calibration backtests + scoring (Brier, Bracher 2021 interval score, calibration error)
│   ├── lovs_onset_to_death.py         Rosello 2015 onset-to-death gamma (α = 4.42, β = 0.388 /day), reused by Methods 1 and 2
│   ├── lovs_death_back_projection.py  deaths-back-projection helper (C = D · (1 + r/β)^α / CFR), central τ₂ = 14 d, with doubling-time-prior marginalization helper
│   ├── lovs_export_back_projection.py exports-back-projection helper (Poisson on observed importations vs. point-of-entry traveler flows)
│   ├── lovs_poe_corridor.py           optional PoE-weighting helper; requires a local permission-cleared traveler-count file
│   └── lovs_report.py                 markdown report generator
├── data/
│   ├── evidence-chains.json           machine-checkable claim provenance registry
│   ├── covariates-bdbv-2026.json      9 geographies (Ituri, Kampala, etc.)
│   ├── covariates-wa-2014.json        62 prefectures (country-level local context)
│   ├── covariates-wa-2014-v3.json     62 prefectures (district-level local context)
│   ├── west-africa-prefecture-weekly.json   WA 2014 substrate (Backer & Wallinga 2016)
│   ├── live-bdbv-2026-output.json     pipeline output for the current snapshot
│   └── bundibugyo-2026/               live provenance registry
│       ├── manifest.json              source-id -> URL -> SHA-256 hash/status
│       └── raw/<sha256>               public-byte sources only
├── tests/                             carved-out tests
├── brief/                             generated by make_brief.py
│   ├── brief.html
│   └── visuals/*.svg
└── deliverables/                      generated by make_brief.py
    └── brief.pdf                      (if chromium-headless available)
```

## Reader-facing terminology

The codebase carries compact implementation labels. The reader-facing labels above are what land in the brief, the webpage, and any external communication.

| Code label | Reader-facing label |
|---|---|
| LOVS (Latent Outbreak Visibility System) | LOVS (defined on first use in the brief, webpage, and README intro) |
| Mode A | Historical calibration on the 2014 West Africa Ebola epidemic |
| Mode B | Pre-committed methodology calibration points |
| Zone 3 | Current-outbreak view |
| Mode A v1 | Without local context |
| Mode A v2 | With country-level local context |
| Mode A v3 | With district-level local context |
| T3 covariates | Local context variables (population density, road density, healthcare access, conflict intensity) |
| `edge_weight` | Per-corridor risk-weight (combined-context multiplier) |
| `[0.1, 10.0]` clamp | Safety floor and ceiling on the per-corridor risk-weight |
| WIS (Weighted Interval Score) | Interval score (Bracher 2021) |
| ECE (Expected Calibration Error) | Calibration error |
| Hypothesis ID | Calibration point identifier |
| visibility gap | Ascertainment gap |
| above base rate | Better than chance |

## Citation

If you use this work, please cite:

> Moore F. *Bundibugyo virus, DRC and Uganda, 2026: surveillance methodology brief.* Released 2026-05. <https://github.com/ArcedeDev/bdbv-2026-lovs>

And cite the underlying methodology references in `CITATIONS.md` where directly applicable.

## License

This repository is dual-licensed. Original code and configuration (the `lovs/` package, the pipeline scripts, and the tooling) are released under the Apache License 2.0. Original authored prose, methodology text, charts, and generated presentation artifacts are released under Creative Commons Attribution 4.0 International (CC BY 4.0), excluding third-party source material, extracted third-party tables, and publisher-owned excerpts. See `LICENSES.md` for the full path-by-path split and `NOTICE` for attribution notices.

Third-party archived content keeps its own license and is not covered by the above. WHO Disease Outbreak News content (under `data/bundibugyo-2026/raw/`) is included verbatim and unmodified for reproducible archival. Restricted publisher bytes and extracted restricted tables, including the Imperial Table 3 PoE dataset and Africa CDC raw page bytes, are not redistributed in the public repository; the manifest keeps URL, timestamp, hash, and extracted factual metadata. Confirm permissions before commercial reuse or redistribution of restricted third-party tables or full raw publisher archives. Per-item attribution, license, and SHA-256 hashes are preserved in `data/bundibugyo-2026/manifest.json`.

## If you have point-of-care data

This brief reports method estimates from publicly aggregated reporting only. If you are working directly in the affected zones, you almost certainly hold information that is privileged, time-sensitive, and not appropriate for a public repository. **Please do not paste line-list rows, GPS-tagged case locations, sequencing reads, or any identifying detail into a public GitHub issue.** You can reach me directly at [frans@arcede.com](mailto:frans@arcede.com) if any of the following would help your work:

- **Onset-date extract (de-identified).** Even a partial onset-date histogram for one health zone substantially narrows the latent-active-chains plausibility interval emitted by Module D. A simple two-column CSV with an anonymous row ID and onset date is sufficient input; do not email direct identifiers unless a secure handoff channel has been agreed first.
- **Updated zone-attributed case counts.** The load-bearing limitation is now narrower but still real: the model uses the 79 confirmed cases that are officially zone-attributed, while 27 confirmed cases remain unallocated headline context. If you can share a newer `{health-zone-id: confirmed_count}` cumulative table for the affected districts, the corridor model can replace the 22 May DRC MoH vector without inventing geography for the additional cases. This source-attribution lag is still the largest single discrimination lever the method is missing.
- **Validated zone GPS centroids.** The repository ships verified centroids for the zones currently in scope (`data/zones.json`). For zones the snapshot may have missed, a centroid plus a one-sentence rationale is enough to extend the corridor model and the geographic visual.
- **Mobility traces or transport-flow snapshots.** Wesolowski 2015 PNAS-class call-detail-record summaries, even at admin-2 aggregation, are the documented next-lever for moving the method above chance discrimination.
- **Case-confirmation latency.** The reporting-completeness nowcast uses the Rosello 2015 BDBV Isiro 2012 onset-to-notification prior as its default, with Camacho 2015 EBOV-Zaire retained as a faster-reporting sensitivity comparator. Field-observed delays (sample collection to PCR result, in days) are a direct prior update for Module C.

Any contribution that lands in the repository will be cited and timestamped; contributors who prefer to remain unnamed can request co-authorship by initials or pseudonym at the time of contribution.

Independent replication of any number reported in the brief is welcomed. The full pipeline is reproducible from the four frozen JSON inputs under `data/` in under five seconds on a stock laptop. Open issues are welcome for the methodology, never for raw line-list or identifying data.

## Contact

For research-use questions about the methodology itself (without sensitive data), open an issue on this repository. For anything containing patient-level, line-list, or identifying information, you can reach me directly at [frans@arcede.com](mailto:frans@arcede.com).
