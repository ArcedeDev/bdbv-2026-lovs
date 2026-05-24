# Numbers audit, snapshot series through 22 May 2026

Every figure that appears in the brief, the webpage, or the README traces back to one of the rows below. The intent is auditability: a reader can pick any number off any chart or paragraph and walk it back to the named source and the exact bytes that produced it.

This file is the **single ground-truth registry** for the dated snapshot series through 22 May 2026. If a downstream number does not appear here, or appears with a different attribution, that is a bug. Update this file in the same commit that updates the number.

Every markdown table data row carries an audit marker. `ec:...` means the row is backed by the machine-readable evidence-chain registry; `audit_gap:...` means the row is intentionally outside the current evidence-chain scope and must not be mistaken for machine-validated claim coverage.

How to use this file:
1. Pick a number you see on the webpage / brief / chart / README.
2. Search this file for that number.
3. Read the row: where it appears, what source it traces to, what the underlying bytes are.

## Public-reporting case counts

| Quantity | Value | Source | Source-id | Archive | Appears in |
|---|---|---|---|---|---|
| Confirmed cases, May 15 | 4 | WHO DON 602 (15 May 2026), "four deaths among confirmed cases" implies four lab-confirmed | who-don602-2026-05-15 | byte-archived, SHA-256 `8b7fb1e1...` | TrajectoryChart timeline point May 15; brief.html At a glance breakdown; Audit ref: `audit_gap:public-source-row` |
| Confirmed cases, May 17 | 10 | WHO PHEIC determination (17 May 2026): 8 Ituri + 2 Kampala; reported Kinshasa case tested negative on confirmatory INRB testing and is not counted | who-pheic-2026-05-17 | byte-archived, SHA-256 `e1f8ea89...` | TrajectoryChart timeline point May 17; refresh_pipeline.py reported_counts.confirmed.minimum; Audit ref: `audit_gap:public-source-row` |
| Confirmed cases, May 18 | (no figure published) | Africa CDC PHECS does not separately report confirmed total; cites 2 Uganda confirmed only | africa-cdc-phecs-2026-05-18 | hash recorded; raw publisher bytes private pending terms confirmation | TrajectoryChart timeline point May 18 confirmed=null (no-data marker); Audit ref: `audit_gap:public-source-row` |
| Confirmed cases, May 19 | 30 | ECDC outbreak page: "30 cases have been laboratory-confirmed" | ecdc-bdbv-drc-uga-2026-05-19 | byte-archived, SHA-256 `3fd9968b...` | TrajectoryChart official / regional anchor; Audit ref: `audit_gap:public-source-row` |
| Confirmed cases, May 20 | 53 | WHO Director-General remarks: 51 confirmed in DRC plus 2 confirmed in Kampala | who-dg-remarks-bdbv-2026-05-20 | byte-archived, SHA-256 `38adc406...`; official aggregate endpoint, not zone-attributed line-list data | TrajectoryChart endpoint; AtAGlance confirmed; refresh_pipeline.py reported_counts.confirmed.primary_value; Audit ref: `audit_gap:public-source-row` |
| Confirmed cases, May 22 | 84 | WHO Director-General Member State briefing: 82 confirmed in DRC plus 2 imported Uganda cases | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...`; official aggregate endpoint, not zone-attributed line-list data | Dated conflict trail; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Confirmed cases, May 23 | 88 | CDC Current Situation: 83 confirmed in DRC plus 5 confirmed in Uganda | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...`; official aggregate endpoint, not zone-attributed line-list data | TrajectoryChart endpoint; AtAGlance confirmed; refresh_pipeline.py reported_counts.confirmed.primary_value; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Confirmed cases in DRC, May 22 | 82 | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...` | confirmedByCountry.cod; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Confirmed cases in DRC, May 23 | 83 | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...` | confirmedByCountry.cod; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Confirmed cases in Uganda, May 23 | 5 | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...` | confirmedByCountry.uga; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Suspected cases, May 15 | 246 | WHO DON 602: "246 suspected cases and 80 deaths" | who-don602-2026-05-15 | byte-archived | TrajectoryChart timeline May 15 suspected; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 17 | 246 | WHO PHEIC (no update on suspected since DON 602) | who-pheic-2026-05-17 | URL-referenced | TrajectoryChart timeline May 17 suspected; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 18 | 395 | Africa CDC PHECS: "about 395 suspected cases and 106 associated deaths have been reported" | africa-cdc-phecs-2026-05-18 | hash recorded; raw publisher bytes private pending terms confirmation | TrajectoryChart timeline May 18; refresh_pipeline.py reported_counts.suspected.minimum; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 19 | >500 | ECDC outbreak page | ecdc-bdbv-drc-uga-2026-05-19 | byte-archived | TrajectoryChart official / regional anchor shown as 500 lower-bound marker; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 20 | 653 | Archived consensus aggregator infobox | wikipedia-2026-ituri-epidemic-2026-05-20 | byte-archived | TrajectoryChart endpoint; AtAGlance suspected; refresh_pipeline.py reported_counts.suspected.primary_value; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 22 | almost 750 | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...`; normalized numeric endpoint stored as 750 with approximate semantics | Dated conflict trail; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Suspected cases, May 23 | 746 | CDC Current Situation DRC count | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...`; exact official endpoint | TrajectoryChart endpoint; AtAGlance suspected; refresh_pipeline.py reported_counts.suspected.primary_value; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Deaths, May 15 | 80 | WHO DON 602: "80 deaths (four deaths among confirmed cases)" | who-don602-2026-05-15 | byte-archived | TrajectoryChart May 15 deaths; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 17 | 80 | WHO PHEIC (no update on deaths since DON 602) | who-pheic-2026-05-17 | URL-referenced | TrajectoryChart May 17 deaths; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 18 | 106 | Africa CDC PHECS reported approximate deaths | africa-cdc-phecs-2026-05-18 | hash recorded; raw publisher bytes private pending terms confirmation | TrajectoryChart May 18 deaths; refresh_pipeline.py reported_deaths.minimum; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 19 | 130 | ECDC outbreak page | ecdc-bdbv-drc-uga-2026-05-19 | byte-archived | TrajectoryChart official / regional anchor; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 20 | 144 | Archived consensus aggregator infobox | wikipedia-2026-ituri-epidemic-2026-05-20 | byte-archived | TrajectoryChart source-cadence context; source-conflict range; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 21 | 148 | CDC Current Situation: 148 suspected deaths across DRC and Uganda | cdc-current-situation-2026-05-21 | byte-archived | TrajectoryChart endpoint; AtAGlance deaths; refresh_pipeline.py reported_deaths.primary_value; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 22 | 177 suspected deaths | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...` | Dated conflict trail; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Deaths, May 23 | 176 suspected DRC deaths | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...`; exact official suspected-deaths endpoint | TrajectoryChart endpoint; AtAGlance deaths; refresh_pipeline.py reported_deaths.primary_value; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Confirmed deaths in DRC, May 22 | 7 | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...` | source manifest and public-health dataset source-extracted metrics; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| HCW deaths, Mongbwalu | 4 | WHO DON 602 narrative (four healthcare worker deaths at Mongbwalu General Referral Hospital) | who-don602-2026-05-15 | byte-archived | brief.html At a glance; AtAGlance.healthcareWorkers.deaths; Audit ref: `audit_gap:public-source-row` |
| Confirmed in Kampala | 2 (1 death) | WHO PHEIC + Africa CDC PHECS both report two Uganda cases including one death | who-pheic-2026-05-17, africa-cdc-phecs-2026-05-18 | WHO byte-archived; Africa CDC hash recorded / private raw bytes | GeographicMap Kampala chip; AtAGlance confirmedByCountry.uga; Audit ref: `audit_gap:public-source-row` |
| Uganda status, May 22 | 2 imported confirmed cases (1 death), no documented onward transmission among contacts | WHO DG Member State briefing + WHO IHR Emergency Committee temporary recommendations | who-dg-remarks-bdbv-2026-05-22, who-ihr-ec-bdbv-temporary-recommendations-2026-05-22 | both byte-archived | confirmedByCountry.uga; source conflict notes; source-review context; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Kinshasa reported case | 0 confirmed | WHO PHEIC update: reported Kinshasa case tested negative on confirmatory INRB testing and is not counted as confirmed | who-pheic-2026-05-17 | byte-archived | zones.json kinshasa-cod audit note; sync_to_website.py contextNote; Audit ref: `audit_gap:public-source-row` |
| Confirmed in Goma | 1 | Wikipedia consensus: "a positive case in Goma...after a woman infected with Ebola travelled there from Ituri" | wikipedia-2026-ituri-epidemic-2026-05-20 | byte-archived | zones.json goma-cod geographic_referent; GeographicMap orange marker; Audit ref: `audit_gap:public-source-row` |
| Affected health zones, Ituri | Mongbwalu, Rwampara, Bunia | WHO DON 602 names all three verbatim; Africa CDC PHECS uses spelling variant "Mongwalu" for Mongbwalu | who-don602-2026-05-15, africa-cdc-phecs-2026-05-18 | WHO byte-archived; Africa CDC hash recorded / private raw bytes | refresh_pipeline.py affected_zones; brief AtAGlance; webpage AtAGlance; Audit ref: `audit_gap:public-source-row` |

## Reporting completeness (LOVS Module C output)

| Quantity | Value | Computed by | Inputs | Appears in |
|---|---|---|---|---|
| Reporting completeness 50% lower | 0.397 (40%) | lovs/lovs_visibility.py nowcast() | reported_counts (above), affected_zones, snapshot.as_of | Bottom-line aside; AtAGlance visibility row; InferredTrajectory band; brief Ascertainment section "40 to 46 percent"; README headline finding; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Reporting completeness 50% upper | 0.458 (46%) | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Publication latency 50% lower | 3.03 days | same | same | brief and webpage visibility detail (when shown); Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Publication latency 50% upper | 12.88 days | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Confirmation backlog 50% lower | 434 | same | same | snapshot JSON, not surfaced as headline number; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Confirmation backlog 50% upper | 622 | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |

## Transmission generations posterior (LOVS Module B output)

| Bin | Mass | Computed by | Inputs | Appears in |
|---|---|---|---|---|
| P(exactly 1) | 0.000 | lovs/lovs_transmission.py transmission_plausibility() with BUNDIBUGYO_PRIORS_STAGE_TWO | reported_counts.confirmed, BDBV R prior gamma, under_ascertainment_uniform | DetectionDepthVisual posterior chart; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(exactly 2) | 0.000 | same | same | same; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(exactly 3) | 0.000 (rounded) | same | same | same; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(exactly 4) | 0.005 | same | same | same; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(exactly 5) | 0.031 | same | same | same; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(>= 6, censored upper bin) | 0.964 | same | same | same; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |
| P(>= 3 generations) | 1.000 (sum of 3+, 4, 5, >=6 bins) | derived | same | DetectionDepth section narrative "essentially 100%"; Audit refs: `ec:lovs:module-d:bdbv-r-prior-gamma:2026-05-20`, `ec:lovs:module-b:detection-depth-priors:2026-05-21` |

Note: Wamala 2010 and MacNeil 2010 anchor Bundibugyo-species incubation and interval context, but `lovs_transmission.py` does not currently consume incubation, serial interval, or deaths in this posterior. The R prior is explicitly tracked as an interim modeling assumption because a direct BDBV R0 estimate has not been located.

## Inferred underlying confirmable cases (LOVS Module C derived)

The InferredTrajectory chart plots two bands, each derived from a distinct input pathway.

### Confirmable band (per confirmed-case point)

| Date | Confirmed | Inferred lower | Inferred upper | Computation |
|---|---|---|---|---|
| May 15 | 4 | round(4/0.458) = 9 | round(4/0.397) = 10 | confirmed / completeness 50% endpoints; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 17 | 10 | round(10/0.458) = 22 | round(10/0.397) = 25 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 19 | 30 | round(30/0.458) = 66 | round(30/0.397) = 76 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 21 | 53 | round(53/0.458) = 116 | round(53/0.397) = 134 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 23 | 88 | round(88/0.461) = 191 | round(88/0.401) = 219 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |

Cross-references:
- TrajectoryChart and InferredTrajectory both read `confirmed.primary` and `visibility.reportingCompleteness50` from the snapshot.

### Deaths-based total-case band (Imperial Method 2, central scenario)

The deaths-back-projection follows Imperial College MRC GIDA's Method 2 (18 May 2026 report, carried forward unchanged in the 20 May 2026 update): `total_cases = deaths * (1 + r/beta)^alpha / CFR`, with `r = ln(2)/tau_2`. Parameters: `tau_2 = 14 days` (central scenario, per Imperial), Rosello et al. 2015 eLife BDBV gamma `alpha = 4.42`, `beta = 0.388/day`, CFR scenario set `{0.26, 0.33, 0.40}` with central `0.33`, from the US CDC outbreak history aggregate (55 deaths across 169 cases = 32.5 percent over the 2007-08 Uganda and 2012 DRC outbreaks; the 0.26 and 0.40 bounds are the Wilson 95% CI [25.9 percent, 39.9 percent] of that proportion). This matches the Imperial 20 May 2026 update, which corrected the 18 May 24/30/40 set to 26/33/40. Growth correction at tau_2=14: `(1 + (ln2/14)/0.388)^4.42 = 1.700`.

| Date | Deaths | Total lower (CFR 40%) | Total upper (CFR 26%) | Computation |
|---|---|---|---|---|
| May 15 | 80 | round(80 * 1.700 / 0.40) = 340 | round(80 * 1.700 / 0.26) = 523 | deaths * growth_correction / CFR endpoints; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 18 | 106 | round(106 * 1.700 / 0.40) = 451 | round(106 * 1.700 / 0.26) = 693 | same; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 20 | 144 | round(144 * 1.700 / 0.40) = 612 | round(144 * 1.700 / 0.26) = 942 | prior endpoint context; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 21 | 148 | round(148 * 1.700 / 0.40) = 629 | round(148 * 1.700 / 0.26) = 968 | current endpoint; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 23 | 176 | round(176 * 1.700 / 0.40) = 748 | round(176 * 1.700 / 0.26) = 1151 | current endpoint; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |

The DoublingTimeSensitivityGrid component plots the same formula across CFR x doubling-time scenarios (`{0.26, 0.33, 0.40} x {7, 14, 21}d`); the implementation is `lovs/lovs_death_back_projection.total_cases_from_deaths()`.

The brief Inferred-trajectory paragraph and the InferredTrajectory chart both surface the May-23 endpoint band 748-1151 alongside the joint WHO-Imperial reference range 400-900 (Imperial 20 May 2026 update). The two bands are distinct: 748-1151 is this snapshot's own deaths-back-projection at its death count (176); 400-900 is Imperial's published envelope over their two methods at their death input (131).

| Imperial edition | Reference band | Deaths input | CFR set | Audit ref |
|---|---|---|---|---|
| 18 May 2026 (superseded) | 400-800 | 88 | 24/30/40 | `ec:lovs:website:imperial-reference-range:2026-05-20` |
| 20 May 2026 (current) | 400-900 | 131 | 26/33/40 | `ec:lovs:website:imperial-reference-range:2026-05-20` |

## Corridor risk (LOVS Module D output)

66 corridors are emitted by the pipeline (11 official DRC source zones into six candidate target zones, after the 21 May 2026 revision added Arua and Nebbi to the candidate-target watch set). Across the current 66-corridor watchlist, the corrected zone-attributed run's ascertainment-adjusted 50% range spans **0.7-21.1% lower bounds** and **1.8-48.1% upper bounds**. Snapshot contract: 88 confirmed cases are the headline aggregate; 79 confirmed cases are officially zone-attributed across 11 DRC MoH source zones; 9 confirmed cases remain unallocated headline context until an official zone table assigns them. This is source-attribution lag, not missing cases. The source-load vector is the DRC MoH SitRep MVE N 007/MVB_17/2026 PDF cumulative Table IV: 79 confirmed DRC cases across Bambu, Bunia, Butembo, Goma, Katwa, Kilo/Kilo Mission, Miti-Murhesa, Mongbwalu, Nizi, Nyankunde, and Rwampara. Audit ref: `ec:lovs:method:bdbv-zone-attributed-corridors:2026-05-22`. Numbers below are the active pre-committed calibration corridors carried forward from the immutable ledger: four from the 20 May top-ranked block and eight from the unpublished 21 May designed-sample block. They are not re-derived from future corridor rankings.

| Rank | Corridor | Adjusted 50% lower | Adjusted 50% upper | Statement appears in |
|---|---|---|---|---|
| 1 | bunia -> kampala-uga | 0.229 | 0.523 | calibration_points statement; CorridorWatchlist + CorridorWatchlistMap chip; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 2 | rwampara -> bundibugyo-uga | 0.227 | 0.523 | same; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 3 | mongbwalu -> beni-cod | 0.218 | 0.522 | same; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 4 | rwampara -> kasese-uga | 0.209 | 0.515 | same; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 5 | bunia -> kasese-uga | 0.220 | 0.553 | May 21 designed block, relative-high cross-border watch corridor; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 6 | bunia -> beni-cod | 0.243 | 0.525 | May 21 designed block, relative-high in-country likely-positive control; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 7 | rwampara -> kampala-uga | 0.218 | 0.515 | May 21 designed block, mid-band cross-border imported-case positive control; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 8 | mongbwalu -> kasese-uga | 0.236 | 0.519 | May 21 designed block, mid-band cross-border watch corridor; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 9 | rwampara -> beni-cod | 0.236 | 0.518 | May 21 designed block, mid-band in-country likely-positive control; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 10 | bunia -> arua-uga | 0.236 | 0.520 | May 21 designed block, mid-band cross-border blindspot watch corridor; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 11 | rwampara -> arua-uga | 0.221 | 0.494 | May 21 designed block, relative-low cross-border likely-negative control; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |
| 12 | mongbwalu -> nebbi-uga | 0.222 | 0.510 | May 21 designed block, relative-low cross-border likely-negative control; Audit ref: `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21` |

Statements are pre-committed; the 20 May block resolves on **19 June 2026, 23:59:59 UTC** and the 21 May block resolves on **20 June 2026, 23:59:59 UTC** against publicly available DRC MoH, Uganda MoH, WHO, and Africa CDC reports.

## Conflict and displacement context (descriptive, not model inputs)

| Quantity | Value | Source |
|---|---|---|
| IDPs in eastern DRC, late 2025 | 7.3 million (UNHCR) | UNHCR Eastern DRC operations, URL-referenced in CITATIONS.md; Audit ref: `audit_gap:context-row-url-referenced` |
| Rhino Camp refugee settlement, Arua | ~95,000 | UNHCR Uganda Refugee Operations, URL-referenced in CITATIONS.md; Audit ref: `audit_gap:context-row-url-referenced` |
| Active armed groups, Ituri / N. Kivu | CODECO, ADF | HRW World Report 2026, ACAPS briefings (URL-referenced in CITATIONS.md); Audit ref: `audit_gap:context-row-url-referenced` |

These appear in the Ascertainment section narrative and the Blindspots conflict bullet. They do NOT enter Module C, D, or the calibration scorer.

## Zone GPS coordinates (data/zones.json)

All 13 zone records are listed in `data/zones.json` with a citation and confidence tag. Plotted records have point coordinates or a public boundary centroid; two model-artifact records (`ituri`, `bundibugyo`) intentionally remain unplotted. Two coordinates were corrected during cycle 4:

| Zone | Old | New | Wikipedia infobox |
|---|---|---|---|
| Kinshasa | -4.4419, 15.2663 | -4.3219, 15.3119 | 04°19'19"S 15°18'43"E / 4.32194°S 15.31194°E; Audit ref: `audit_gap:zone-coordinate-row` |
| Mongbwalu | 1.9831, 30.0331 | 1.9352, 30.0462 | 1°56'07"N 30°02'46"E / 1.935157°N 30.046234°E; Audit ref: `audit_gap:zone-coordinate-row` |

The other high-confidence plotted coordinates were cross-checked against public coordinate references and are within map-scale tolerance of the stored DMS/source citations.

## Methodology citation crosswalk

Citations that appear in the brief or webpage narrative trace to the bibliography in `CITATIONS.md`:

| Citation referenced in brief/webpage | Where the full citation lives |
|---|---|
| Wamala 2010 EID | CITATIONS.md, Bundibugyo-species transmission priors; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| MacNeil 2010 EID | CITATIONS.md, Bundibugyo-species transmission priors; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Albariño 2013 Virology | CITATIONS.md, Bundibugyo-species transmission priors; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Camacho 2015 PLOS Currents | CITATIONS.md, Transmission modeling and cross-prefecture spread; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Cori 2013 AJE | CITATIONS.md, Transmission modeling and cross-prefecture spread; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Backer & Wallinga 2016 PLOS Comp Bio | CITATIONS.md, Mode A retrospective substrate; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Wesolowski 2016 J Infect Dis | CITATIONS.md, Candidate next-lever for corridor discrimination; Audit ref: `audit_gap:bibliography-crosswalk-row` |
| Bracher 2021 PLOS Comp Bio | CITATIONS.md, Scoring rules; Audit ref: `audit_gap:bibliography-crosswalk-row` |

## Source archive ground truth

`data/bundibugyo-2026/manifest.json` is the authoritative registry of source provenance. For `raw_archive_status=public_bytes` entries, the manifest gives the URL, SHA-256, and relative path under `data/bundibugyo-2026/raw/`; `lovs/lovs_archive.load_archive()` validates SHA-256 integrity end-to-end. For `raw_archive_status=private_restricted_bytes` entries, the manifest keeps URL/timestamp/hash/facts but the publisher bytes are intentionally not redistributed.

| source_id | URL | Archive |
|---|---|---|
| who-don602-2026-05-15-live | https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON602 | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| africa-cdc-phecs-2026-05-18-live | https://africacdc.org/news-item/africa-cdc-declares-the-ongoing-bundibugyo-ebola-outbreak-a-public-health-emergency-of-continental-security/ | hash recorded; raw publisher bytes private pending terms confirmation; Audit ref: `audit_gap:archive-manifest-row` |
| wikipedia-2026-ituri-epidemic-2026-05-20-live | https://en.wikipedia.org/wiki/2026_Ituri_Province_Ebola_epidemic | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| who-pheic-2026-05-17-live | https://www.who.int/news/item/17-05-2026-epidemic-of-ebola-disease-in-the-democratic-republic-of-the-congo-and-uganda-determined-a-public-health-emergency-of-international-concern | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| who-dg-remarks-bdbv-2026-05-20 | https://www.who.int/news-room/speeches/item/who-director-general-s-opening-remarks-at-the-media-briefing-on-ebola-outbreak-in-drc-and-uganda-20-may-2026 | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| who-dg-remarks-bdbv-2026-05-22 | https://www.who.int/news-room/speeches/item/who-director-general-s-opening-remarks-at-the-member-state-information-session-on-outbreaks-of-ebola-and-hantavirus-22-may-2026 | byte-archived; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| who-ihr-ec-bdbv-temporary-recommendations-2026-05-22 | https://www.who.int/news/item/22-05-2026-first-meeting-of-the-ihr-emergency-committee-regarding-the-epidemic-of-ebola-bundibugyo-virus-disease-in-the-democratic-republic-of-the-congo-and-uganda-2026-temporary-recommendations | byte-archived; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| afro-sitrep-01-2026-05-18-live | (WHO AFRO Weekly External Situation Report 01, 18 May 2026 PDF) | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| cdc-han-00530-2026-05 | https://www.cdc.gov/han/php/notices/han00530.html | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| imperial-mrc-gida-bdbv-2026-05-18 | https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-18-05-2026/ | hash recorded; restricted raw publisher bytes private; Audit ref: `audit_gap:archive-manifest-row` |
| ecdc-bdbv-drc-uga-2026-05-19-live | https://www.ecdc.europa.eu/en/ebola-virus-disease-outbreak-democratic-republic-congo-and-uganda-19-may-2026 | byte-archived; Audit ref: `audit_gap:archive-manifest-row` |
| cdc-current-situation-2026-05-21 | https://www.cdc.gov/ebola/situation-summary/index.html | byte-archived; Audit ref: `ec:lovs:data:bdbv-denominator-reconciliation:2026-05-21` |
| ecdc-bdbv-drc-uga-2026-05-21-live | https://www.ecdc.europa.eu/en/ebola-virus-disease-outbreak-democratic-republic-congo-and-uganda | byte-archived; Audit ref: `ec:lovs:data:bdbv-denominator-reconciliation:2026-05-21` |
| ecdc-threat-assessment-bdbv-2026-05-21-pdf | https://www.ecdc.europa.eu/sites/default/files/documents/EBOLA%20TAB%20NEW%20FINAL_0.pdf | byte-archived; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |
| cdc-traveler-management-guidance-2026-05-21-pdf | https://www.cdc.gov/viral-hemorrhagic-fevers/media/pdfs/Interim-guidance-for-BVD-2026.pdf | byte-archived; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |
| cdc-returning-travelers-info-2026-05-21-live | https://www.cdc.gov/viral-hemorrhagic-fevers/travel-to-us/index.html | byte-archived; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |
| paho-who-epialert-bdbv-2026-05-21-pdf | https://www.paho.org/sites/default/files/2026/05/2026-may-20-phe-bundibugyo-virus-disease.pdf | hash recorded; restricted raw publisher bytes private pending terms confirmation; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |
| who-afro-zambia-readiness-2026-05-21-live | https://www.afro.who.int/countries/zambia/news/strengthening-zambias-readiness-health-emergencies-surveillance-pandemic-preparedness | byte-archived; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |
| uk-gov-ebola-eastern-drc-support-2026-05-21-live | https://www.gov.uk/government/news/uk-steps-up-support-to-stop-spread-of-ebola-in-eastern-drc | byte-archived; Audit ref: `ec:lovs:data:bdbv-may21-source-sweep:2026-05-21` |

## Formal evidence-chain registry (machine-readable, parallel layer)

`data/evidence-chains.json` is the structured companion to this narrative file. It encodes 18 audit claims as `chain_id` records with a `verdict` field (`supported`, `corrected`, `derived_supported`, `needs_primary_source`, `unsupported_attribution`, `pending`) and the source chain that backs the verdict. `lovs/lovs_evidence.py` validates the registry on every run; `python3 -m lovs.lovs_evidence` reports the current counts.

Current state: 18 chains validate as `supported: 1, corrected: 3, derived_supported: 8, needs_primary_source: 3, unsupported_attribution: 2, pending: 1`. The three outstanding evidentiary gaps most relevant to this narrative are tracked there and surfaced here:

1. `ec:lovs:module-d:corridor-gravity-exponents:2026-05-21`: corridor-gravity edge-weight exponents in `lovs/lovs_covariates.py` (`CovariateTable.edge_weight`, consumed by `lovs/lovs_next_zone.py`) are still heuristic (not pinned to a published Wesolowski/Tatem fit).
2. `ec:lovs:data:imperial-table-3-row-summary-discrepancy:2026-05-21`: Imperial's published Nord Kivu PoE summary total does not reconcile with the sum of its own Table 3 per-PoE rows (off by 18 daily travellers); the underlying WHO PoE passenger table that would resolve the discrepancy has not been located. The extracted PoE table and its absolute counts are not redistributed in this public repository.
3. `ec:lovs:archive:imperial-report-license:2026-05-20`: the Imperial report is treated as restricted third-party source material pending a byte-archived primary license footer or explicit permission confirmation. The repository does not relicense Imperial-derived Table 3 values.

Three other items are tracked as follow-ups outside the per-claim registry: not every public sentence carries its own evidence-chain ID (a fully machine-linked claim graph is a larger build); the Wikipedia entry's `root_provenance_chain` is empty pending an aggregator-tier evidence schema; and the CDC HAN exact publication day is a placeholder until WHO/CDC tooling exposes it.

## How this document is kept honest

Every commit that touches a snapshot number SHOULD touch this file in the same commit. If you find a downstream number that is not in the rows above, treat that as a bug and reconcile by either (a) adding the row here or (b) deleting the downstream number until it is grounded. The 2026-05-13 fabrication audit revealed that snapshots had inherited unverified figures attributed to "aggregated public reporting through 19 May" with no archive. The remedy is this registry plus the public byte archive and hash-only restricted provenance records under `data/bundibugyo-2026/`.
