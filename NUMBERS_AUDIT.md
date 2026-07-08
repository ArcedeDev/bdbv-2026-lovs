# Numbers audit, snapshot series through 19 June 2026

Every figure that appears in the brief, the webpage, or the README traces back to one of the rows below. The intent is auditability: a reader can pick any number off any chart or paragraph and walk it back to the named source and the exact bytes that produced it.

This file is the **single ground-truth registry** for the dated snapshot series through 19 June 2026. If a downstream number does not appear here, or appears with a different attribution, that is a bug. Update this file in the same commit that updates the number.

Every markdown table data row carries an audit marker. `ec:...` means the row is backed by the machine-readable evidence-chain registry; `audit_gap:...` means the row is intentionally outside the current evidence-chain scope and must not be mistaken for machine-validated claim coverage.

How to use this file:
1. Pick a number you see on the webpage / brief / chart / README.
2. Search this file for that number.
3. Read the row: where it appears, what source it traces to, what the underlying bytes are.

## Composition disclosure standard

**Scope note (current cumulative contract).** The rules and per-source rows in this section describe the historical 15-to-25 May reporting series, when suspected was still carried as a published cumulative tier. As of the 2026-07-06 SitRep #053 promotion, the only cumulative epidemiological metrics are laboratory-confirmed cases (1728 confirmed cases: 1708 DRC + 20 Uganda) and confirmed deaths (582: 580 DRC + 2 Uganda). The cumulative suspected tier and the confirmed-and-suspected composite are paused and archived from the headline surface (retained as dated provenance, and reactivatable in a future snapshot). Suspected cases now appear only as point-in-time operational caseloads such as suspected cases in isolation on a separate non-cumulative axis, and the per-source suspected rows below are retained as archival provenance of what each publisher reported on its own date, never summed into confirmed. SitRep #053 republishes a fresh patient-movement table (Tableau 7: 680 in isolation, split 218 confirmed / 462 suspected reconciled to the corroborated 680 census) but does not publish the under-investigation stock or the total active suspected queue, so those values are not fabricated. The present-tense "current LOVS contract" phrasing in the rules below is preserved as the historical record for that series.

Many headline outbreak numbers reported here are **derived composites**, not single published numbers. CDC, ECDC, and DRC MoH publish country-split or category-split components; the LOVS extractor sums or selects to produce the headline. The standard below makes the composition explicit so a reader can walk any headline back to its components and to the publisher's own scope.

**Rule 1: every derived two-country sum must show its components inline.** Write `112 = 105 DRC + 7 Uga (CDC 25 May country-pair endpoint)`. Do not write a bare "112 confirmed cases" as if the publisher reported 112 as a single number; CDC reports 105 and 7 separately and the LOVS extractor sums them.

**Rule 2: every single-country headline that the website displays as the global total must say so.** Write `906 DRC suspected (Uganda does not publish a suspected count; CDC tuple is DRC-scoped for this field)`. The current LOVS contract treats `suspected` as global-implied when only DRC is published, which is an honest cross-source compromise; the audit row must surface that scope.

**Rule 3: never mix categories inside a single headline.** Suspected deaths and confirmed deaths are different categories with different definitions and different denominators. Write `223 suspected DRC deaths and 1 confirmed Uganda death (categories not summed; two different surveillance funnels)`. The current LOVS `deaths.primary` field carries 223 alone; the audit must document why and what Uganda contributes separately.

**Rule 4: corroboration across authorities must be named.** When two or more sources independently report the same country-pair tuple, list them. Write `(CDC May 25 + ECDC May 26 (attributed to DRC MoH May 25) + DRC MoH 24 May dashboard, all 105 DRC + 7 Uga)`. A three-source corroborated number is materially more defensible than a single-source one and the reader should see that.

**Rule 5: derived sums in `normalized_content` must be flagged.** The extractor populates `cases_confirmed_total: 112` as a sum; the audit row should annotate this as "(derived sum; CDC publishes 105 and 7 separately)" so it is clear the publisher did not print 112.

**Rule 3 extension (Plan A 2026-05-28, per-zone composition disclosure).** Where a snapshot carries an `insp_per_zone_block` (data_scale_used in {`per_zone`, `partial_per_zone`, `mixed_with_metric_floor`}), every per-zone-attributed metric figure that the brief or website displays must surface its place in the reconciliation invariant: `sum(by_lovs_zone[zone][metric]) + unallocated_residual[metric] equals national_at_data_date[metric]`. For `confirmed_deaths` specifically, the per-zone-attributed figure is treated as a LOWER BOUND and `unallocated_residual.confirmed_deaths` as an UPPER BOUND for total per-zone deaths until the 1-3 week INRB clinical review queue lag closes (spec section 2.3 attribution-lag hierarchy). Write `5 confirmed deaths attributed to LOVS source zones (lower bound; 12 additional confirmed deaths remain in unallocated residual at as_of 2026-05-26 while clinical review catches up)`. Do not present a bare per-zone confirmed-deaths total as if it equalled the national figure; the trailing-attribution disclosure is the load-bearing context.

This standard applies prospectively to every new row from 2026-05-26 forward and retrospectively to the May 24 / May 25 rows updated below.

## Uganda reporting asymmetry (observed 2026-05-26)

CDC publishes Uganda's confirmed case count and confirmed death count but does NOT publish Uganda suspected-case or Uganda suspected-death counts in the May 25 tuple. The published Uganda fields are:

- `cases_confirmed_uganda`: 7 (May 25)
- `deaths_uganda`: 1 (May 25; this is a confirmed death)
- no `cases_suspected_uganda`, no `deaths_suspected_uganda`

This is real reporting structure, not a missing extraction. Uganda's outbreak is small enough that contact tracing has so far been able to track every probable contact; the surveillance funnel produces a confirmed count without a separate suspected pool. DRC's outbreak is larger and CDC publishes both DRC suspected (906) and DRC confirmed (105). When the LOVS headline reports "906 suspected" or "223 deaths," it is therefore reporting DRC-scoped numbers; Uganda is contributing zero to those headlines because there is no published Uganda value, not because the value is zero.

The audit rows below disclose this scope explicitly per Rule 2.

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
| Confirmed cases, May 24 | 106 = 101 DRC + 5 Uga (derived sum; CDC publishes 101 and 5 separately) | CDC Current Situation: 101 confirmed in DRC plus 5 confirmed in Uganda. Same-day DRC MoH dashboard aggregate reports 112 confirmed DRC cases (different DRC-only composition) and is retained as official conflict evidence. | cdc-current-situation-2026-05-24; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC page byte-archived, SHA-256 `b33e81ab...`; DRC MoH dashboard GraphQL hash-recorded, SHA-256 `f3d51491...` | TrajectoryChart May 24 point; conflict anchor (superseded by CDC 25 May 112); Audit ref: `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| Confirmed cases, May 25 | 112 = 105 DRC + 7 Uga (derived sum; three-source corroborated) | CDC 25 May + ECDC 26 May (attributed to DRC MoH 25 May) + DRC MoH 24 May dashboard, all three independently report 105 DRC and 7 Uga. CDC publishes the country split separately and the extractor sums; the 112 total does not appear as a single number on any source page. WHO DG remarks 25 May reports 101 DRC + 5 Uga = 106 (one-day-behind ascertainment); ECDC 25 May reports 101 confirmed; DRC MoH 24 May dashboard reports 112 DRC-only. All retained as conflict anchors. | cdc-current-situation-2026-05-25; ecdc-bdbv-drc-uga-2026-05-26 (corroborating); ecdc-bdbv-drc-uga-2026-05-25; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC byte-archived, SHA-256 `77d396c0...`; ECDC 26 May byte-archived, SHA-256 `d455dcac...`; ECDC 25 May byte-archived, SHA-256 `0636fc9b...` | TrajectoryChart endpoint; AtAGlance confirmed; refresh_pipeline.py reported_counts.confirmed.primary_value; Audit ref: `ec:lovs:data:bdbv-may25-official-release:2026-05-25` |
| Confirmed cases in DRC, May 22 | 82 | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...` | confirmedByCountry.cod; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Confirmed cases in DRC, May 23 | 83 | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...` | confirmedByCountry.cod; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Confirmed cases in DRC, May 24 | 101 | CDC Current Situation. DRC MoH same-day dashboard aggregate reports 112 confirmed DRC cases. | cdc-current-situation-2026-05-24; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC byte-archived; DRC MoH hash-recorded restricted bytes | confirmedByCountry.cod; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| Confirmed cases in Uganda, May 23 | 5 | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...` | confirmedByCountry.uga; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Confirmed cases in Uganda, May 24 | 5 | CDC Current Situation | cdc-current-situation-2026-05-24 | byte-archived, SHA-256 `b33e81ab...` | confirmedByCountry.uga; brief At a glance split; Audit ref: `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| Suspected cases, May 15 | 246 | WHO DON 602: "246 suspected cases and 80 deaths" | who-don602-2026-05-15 | byte-archived | TrajectoryChart timeline May 15 suspected; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 17 | 246 | WHO PHEIC (no update on suspected since DON 602) | who-pheic-2026-05-17 | URL-referenced | TrajectoryChart timeline May 17 suspected; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 18 | 395 | Africa CDC PHECS: "about 395 suspected cases and 106 associated deaths have been reported" | africa-cdc-phecs-2026-05-18 | hash recorded; raw publisher bytes private pending terms confirmation | TrajectoryChart timeline May 18; refresh_pipeline.py reported_counts.suspected.minimum; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 19 | >500 | ECDC outbreak page | ecdc-bdbv-drc-uga-2026-05-19 | byte-archived | TrajectoryChart official / regional anchor shown as 500 lower-bound marker; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 20 | 653 | Archived consensus aggregator infobox | wikipedia-2026-ituri-epidemic-2026-05-20 | byte-archived | TrajectoryChart endpoint; AtAGlance suspected; refresh_pipeline.py reported_counts.suspected.primary_value; Audit ref: `audit_gap:public-source-row` |
| Suspected cases, May 22 | almost 750 | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...`; normalized numeric endpoint stored as 750 with approximate semantics | refresh_pipeline.py reported_counts.suspected.primary_value (highest valid primary; defers higher than CDC 23 May 746, not a down-revision); TrajectoryChart endpoint; AtAGlance suspected; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Suspected cases, May 23 | 746 | CDC Current Situation DRC count | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...`; exact official DRC suspected endpoint | Conflict anchor: lower later figure than WHO DG 22 May 750, retained rather than treated as a down-revision; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Suspected/reported cases, May 24 | 904 | CDC Current Situation DRC suspected count. Same-day DRC MoH all-published-bulletins aggregate reports 854 reported cases. | cdc-current-situation-2026-05-24; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC byte-archived; DRC MoH hash-recorded restricted dashboard bytes | TrajectoryChart May 24 point; conflict anchor (superseded by CDC 25 May 906); Audit ref: `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| Suspected/reported cases, May 25 | 906 DRC suspected (Uganda does not publish a suspected count; CDC tuple is DRC-scoped for this field per Rule 2) | US CDC Current Situation DRC suspected count, the highest valid primary on the latest date. ECDC 25 May reports 904 DRC suspected; ECDC 26 May (attributed to DRC MoH 25 May) corroborates 906 DRC suspected; DRC MoH 24 May aggregate reports 854 reported cases. CDC publishes only a DRC-scoped suspected field; there is no Uganda contribution to this headline. | cdc-current-situation-2026-05-25; ecdc-bdbv-drc-uga-2026-05-26 (corroborating 906); ecdc-bdbv-drc-uga-2026-05-25; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC byte-archived, SHA-256 `77d396c0...`; ECDC 26 May byte-archived, SHA-256 `d455dcac...`; ECDC 25 May byte-archived, SHA-256 `0636fc9b...` | TrajectoryChart endpoint; AtAGlance suspected; refresh_pipeline.py reported_counts.suspected.primary_value; Audit ref: `ec:lovs:data:bdbv-may25-official-release:2026-05-25` |
| Deaths, May 15 | 80 | WHO DON 602: "80 deaths (four deaths among confirmed cases)" | who-don602-2026-05-15 | byte-archived | TrajectoryChart May 15 deaths; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 17 | 80 | WHO PHEIC (no update on deaths since DON 602) | who-pheic-2026-05-17 | URL-referenced | TrajectoryChart May 17 deaths; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 18 | 106 | Africa CDC PHECS reported approximate deaths | africa-cdc-phecs-2026-05-18 | hash recorded; raw publisher bytes private pending terms confirmation | TrajectoryChart May 18 deaths; refresh_pipeline.py reported_deaths.minimum; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 19 | 130 | ECDC outbreak page | ecdc-bdbv-drc-uga-2026-05-19 | byte-archived | TrajectoryChart official / regional anchor; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 20 | 144 | Archived consensus aggregator infobox | wikipedia-2026-ituri-epidemic-2026-05-20 | byte-archived | TrajectoryChart source-cadence context; source-conflict range; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 21 | 148 | CDC Current Situation: 148 suspected deaths across DRC and Uganda | cdc-current-situation-2026-05-21 | byte-archived | TrajectoryChart endpoint; AtAGlance deaths; refresh_pipeline.py reported_deaths.primary_value; Audit ref: `audit_gap:public-source-row` |
| Deaths, May 22 | 177 suspected deaths | WHO Director-General Member State briefing | who-dg-remarks-bdbv-2026-05-22 | byte-archived, SHA-256 `40367f29...` | refresh_pipeline.py reported_deaths.primary_value (highest valid primary; defers higher than CDC 23 May 176, not a down-revision); TrajectoryChart endpoint; AtAGlance deaths; Audit ref: `ec:lovs:data:bdbv-may22-official-release:2026-05-22` |
| Deaths, May 23 | 176 suspected DRC deaths | CDC Current Situation | cdc-current-situation-2026-05-23 | byte-archived, SHA-256 `1fa637ba...`; exact official suspected-deaths endpoint (DRC-specific) | Conflict anchor: lower later figure than WHO DG 22 May 177, retained rather than treated as a down-revision; Audit ref: `ec:lovs:data:bdbv-may23-cdc-official-release:2026-05-24` |
| Deaths, May 24 | 179 | DRC MoH all-published-bulletins dashboard aggregate registered deaths (24 May), retained as a dated conflict anchor; superseded as the reported endpoint by the higher valid CDC 25 May primary (223). CDC 24 May reported 119 suspected DRC deaths and ten confirmed DRC deaths. | drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24; cdc-current-situation-2026-05-24 | DRC MoH dashboard hash-recorded restricted bytes; CDC byte-archived | TrajectoryChart May 24 point; conflict anchor (registered deaths); Audit ref: `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| Deaths, May 25 | 223 suspected DRC deaths + 1 confirmed Uganda death (categories not summed per Rule 3; two surveillance funnels) | US CDC Current Situation: 223 suspected DRC deaths is the highest valid primary on the latest date and the reported deaths endpoint; carried alone as `reported_deaths.primary_value=223`. CDC also reports 10 confirmed DRC deaths and 1 confirmed Uganda death; these are confirmed-category, not summed into the 223. ECDC 25 May reports 119 DRC suspected deaths; DRC MoH 24 May aggregate reports 179 registered DRC deaths; ECDC 26 May (attributed to DRC MoH 25 May) corroborates 223 DRC suspected and 10 confirmed DRC. All conflicts retained as dated anchors. | cdc-current-situation-2026-05-25; ecdc-bdbv-drc-uga-2026-05-26 (corroborating 223 + 10); ecdc-bdbv-drc-uga-2026-05-25; drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24 | CDC byte-archived, SHA-256 `77d396c0...`; ECDC 26 May byte-archived, SHA-256 `d455dcac...`; ECDC 25 May byte-archived, SHA-256 `0636fc9b...` | TrajectoryChart endpoint; AtAGlance deaths; refresh_pipeline.py reported_deaths.primary_value; Audit ref: `ec:lovs:data:bdbv-may25-official-release:2026-05-25` |
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
| Reporting completeness 50% lower | 0.493 (49.3%) | lovs/lovs_visibility.py nowcast() | reported_counts (above), affected_zones, snapshot.as_of | Bottom-line aside; AtAGlance visibility row; InferredTrajectory band; brief Ascertainment section "49 to 57 percent"; README headline finding; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Reporting completeness 50% upper | 0.576 (57.6%) | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Publication latency 50% lower | 2.92 days | same | same | brief and webpage visibility detail (when shown); Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Publication latency 50% upper | 12.17 days | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Confirmation backlog 50% lower | 0 | same | same | snapshot JSON, not surfaced as headline number; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| Confirmation backlog 50% upper | 0 | same | same | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |

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
| May 19 | 30 | round(30/0.459) = 65 | round(30/0.397) = 75 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 20 | 53 | round(53/0.459) = 116 | round(53/0.397) = 133 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 21 | 85 | round(85/0.459) = 185 | round(85/0.397) = 214 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23` |
| May 24 | 106 | round(106/0.459) = 231 | round(106/0.397) = 267 | same; Audit ref: `ec:lovs:grepi:reporting-delay-update:2026-05-23`; `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |

Cross-references:
- TrajectoryChart and InferredTrajectory both read `confirmed.primary` and `visibility.reportingCompleteness50` from the snapshot.

### Deaths-based total-case band (Imperial Method 2, central scenario)

The deaths-back-projection follows Imperial College MRC GIDA's Method 2 (18 May 2026 report, carried forward unchanged in the 20 May 2026 update): `total_cases = deaths * (1 + r/beta)^alpha / CFR`, with `r = ln(2)/tau_2`. Parameters: `tau_2 = 7 days` (our re-grounded central from this outbreak's confirmed-case series, 2026-06-01; Imperial's borrowed 14-day central is retained only to reproduce their published 400-900 reference), Rosello et al. 2015 eLife BDBV gamma `alpha = 4.42`, `beta = 0.388/day`, CFR scenario set `{0.26, 0.33, 0.40}` with central `0.33`, from the US CDC outbreak history aggregate (55 deaths across 169 cases = 32.5 percent over the 2007-08 Uganda and 2012 DRC outbreaks; the 0.26 and 0.40 bounds are the Wilson 95% CI [25.9 percent, 39.9 percent] of that proportion). This matches the Imperial 20 May 2026 update, which corrected the 18 May 24/30/40 set to 26/33/40. Growth correction at our central tau_2=7: `(1 + (ln2/7)/0.388)^4.42 = 2.731`; at Imperial's reference tau_2=14: `(1 + (ln2/14)/0.388)^4.42 = 1.700`. The Method-2 death input is laboratory-confirmed deaths only (475 at the current SitRep #049 July 2 endpoint); the suspected-inclusive composite (247) used before the 2026-06-01 deaths-split is superseded.

| Date | Deaths | Total lower (CFR 40%) | Total upper (CFR 26%) | Computation |
|---|---|---|---|---|
| May 15 | 80 | round(80 * 1.700 / 0.40) = 340 | round(80 * 1.700 / 0.26) = 523 | deaths * growth_correction / CFR endpoints; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 18 | 106 | round(106 * 1.700 / 0.40) = 451 | round(106 * 1.700 / 0.26) = 693 | same; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 20 | 144 | round(144 * 1.700 / 0.40) = 612 | round(144 * 1.700 / 0.26) = 942 | prior endpoint context; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 21 | 148 | round(148 * 1.700 / 0.40) = 629 | round(148 * 1.700 / 0.26) = 968 | prior endpoint context; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 22 | 177 | round(177 * 1.7003 / 0.40) = 752 | round(177 * 1.7003 / 0.26) = 1158 | prior endpoint context; Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 23 | 176 | round(176 * 1.700 / 0.40) = 748 | round(176 * 1.700 / 0.26) = 1151 | conflict anchor (lower later figure than WHO DG 177; not a down-revision); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| May 24 | 179 | round(179 * 1.7003 / 0.40) = 761 | round(179 * 1.7003 / 0.26) = 1171 | conflict anchor (DRC MoH all-published-bulletins registered deaths; superseded as endpoint by CDC 25 May 223); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:bdbv-may24-official-release:2026-05-24` |
| May 25 | 223 | round(223 * 1.7003 / 0.40) = 948 | round(223 * 1.7003 / 0.26) = 1458 | prior endpoint context (US CDC Current Situation 25 May suspected DRC deaths); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:bdbv-may25-official-release:2026-05-25` |
| May 26 | 247 | round(247 * 1.7003 / 0.40) = 1050 | round(247 * 1.7003 / 0.26) = 1615 | prior-basis endpoint context (pre-2026-06-01 composite suspected-inclusive deaths at Imperial's 14-day central; superseded by the lab-confirmed + 7-day row below); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:method:drc-moh-26-may-headline-promotion:2026-05-27` |
| May 31 | 49 (lab-confirmed) | round(49 * 2.731 / 0.40) = 335 | round(49 * 2.731 / 0.26) = 515 | prior endpoint context (superseded by the June 1 lab-confirmed 61 row below); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-017-queue-drawdown:2026-05-31` |
| June 1 | 61 (lab-confirmed) | round(61 * 2.731 / 0.40) = 416 | round(61 * 2.731 / 0.26) = 641 | prior endpoint (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #018 June 1 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21` |
| June 2 | 63 (lab-confirmed) | round(63 * 2.731 / 0.40) = 430 | round(63 * 2.731 / 0.26) = 662 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #019 June 2 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-019-visual-promotion:2026-06-02` |
| June 9 | 129 (lab-confirmed) | round(129 * 2.731 / 0.40) = 881 | round(129 * 2.731 / 0.26) = 1355 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #026 June 9 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-026-visual-promotion:2026-06-09` |
| June 10 | 138 (lab-confirmed) | round(138 * 2.731 / 0.40) = 942 | round(138 * 2.731 / 0.26) = 1450 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #027 June 10 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-027-visual-promotion:2026-06-10` |
| June 11 | 141 (lab-confirmed) | round(141 * 2.731 / 0.40) = 963 | round(141 * 2.731 / 0.26) = 1481 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #028 June 11 headline; public SitRep #029 was not found); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-028-visual-promotion:2026-06-11` |
| June 13 | 183 (lab-confirmed) | round(183 * 2.731 / 0.40) = 1249 | round(183 * 2.731 / 0.26) = 1922 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #030 June 13 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-030-visual-promotion:2026-06-13` |
| June 14 | 194 (lab-confirmed) | round(194 * 2.731 / 0.40) = 1325 | round(194 * 2.731 / 0.26) = 2038 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #031 June 14 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-031-visual-promotion:2026-06-14` |
| June 15 | 198 (lab-confirmed) | round(198 * 2.731 / 0.40) = 1352 | round(198 * 2.731 / 0.26) = 2080 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #032 June 15 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-032-visual-promotion:2026-06-15` |
| June 16 | 204 (lab-confirmed) | round(204 * 2.731 / 0.40) = 1393 | round(204 * 2.731 / 0.26) = 2143 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #033 June 16 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-033-visual-promotion:2026-06-16` |
| June 17 | 234 (lab-confirmed) | round(234 * 2.731 / 0.40) = 1598 | round(234 * 2.731 / 0.26) = 2458 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #034 June 17 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-034-visual-promotion:2026-06-17` |
| June 18 | 247 (lab-confirmed) | round(247 * 2.731 / 0.40) = 1686 | round(247 * 2.731 / 0.26) = 2594 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #035 June 18 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-035-visual-promotion:2026-06-18` |
| June 19 | 249 (lab-confirmed) | round(249 * 2.731 / 0.40) = 1700 | round(249 * 2.731 / 0.26) = 2615 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #036 June 19 headline); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-036-visual-promotion:2026-06-19` |
| June 24 | 306 (lab-confirmed) | round(306 * 2.731 / 0.40) = 2089 | round(306 * 2.731 / 0.26) = 3214 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #041 June 24 country-scope headline 304 DRC + 2 Uganda); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-041-visual-promotion:2026-06-24` |
| June 25 | 323 (lab-confirmed) | round(323 * 2.731 / 0.40) = 2205 | round(323 * 2.731 / 0.26) = 3393 | prior endpoint context (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #042 June 25 country-scope headline 321 DRC + 2 Uganda); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-042-visual-promotion:2026-06-25` |
| June 27 | 362 (lab-confirmed) | round(362 * 2.731 / 0.40) = 2472 | round(362 * 2.731 / 0.26) = 3802 | current endpoint (laboratory-confirmed deaths, our re-grounded 7-day central; suspected deaths never summed; deaths input is the SitRep #044 June 27 country-scope headline 360 DRC + 2 Uganda; SitRep #043 was not published); Audit ref: `ec:lovs:website:cfr-band-correction:2026-05-20`; `ec:lovs:method:death-back-projection:2026-05-21`; `ec:lovs:data:inrb-sitrep-044-visual-promotion:2026-06-27` |

The DoublingTimeSensitivityGrid component plots the same formula across CFR x doubling-time scenarios (`{0.26, 0.33, 0.40} x {7, 14, 21}d`); the implementation is `lovs/lovs_death_back_projection.total_cases_from_deaths()`.

The brief Inferred-trajectory paragraph and the InferredTrajectory chart both surface the endpoint band 1700-2615 alongside the joint WHO-Imperial reference range 400-900 (Imperial 20 May 2026 update). The two bands are distinct: 1700-2615 is this snapshot's own deaths-back-projection at its laboratory-confirmed death input (249) and our re-grounded 7-day central; 400-900 is Imperial's published envelope over their two methods at their death input (131) and 14-day central.

| Imperial edition | Reference band | Deaths input | CFR set | Audit ref |
|---|---|---|---|---|
| 18 May 2026 (superseded) | 400-800 | 88 | 24/30/40 | `ec:lovs:website:imperial-reference-range:2026-05-20` |
| 20 May 2026 (current) | 400-900 | 131 | 26/33/40 | `ec:lovs:website:imperial-reference-range:2026-05-20` |

## Corridor risk (LOVS Module D output)

331 corridors are emitted by the pipeline (the 37 INSP per-health-zone source zones that carry confirmed cases, each crossed with nine candidate target zones, minus two self-edges because goma-cod and beni-cod are each both a confirmed source zone and a candidate target). Across the current 331-corridor watchlist for the 2026-07-06 publication-state snapshot, the zone-attributed run's ascertainment-adjusted 50% ranges span 0.5-95.3% lower and 1.5-100.0% upper. Snapshot contract: 1728 confirmed cases (1708 DRC + 20 Uganda) are the headline aggregate (INSP SitRep #053 promotion, headline clock 2026-07-06); 1691 confirmed cases are officially zone-attributed across 37 official source zones at the per-zone attribution clock (data_as_of 2026-07-06), leaving 37 confirmed cases unallocated to a public zone row. The headline clock and the per-zone attribution clock are deliberately distinct when a source publishes residual or non-health-zone rows and are not differenced as incidence. This is source-attribution lag, not missing cases. The source-load vector is the coherent reviewed INSP per-health-zone Table 2 zone list; one newly named zone (Boga, Ituri) this cycle, and 17 Ituri confirmed cases remain in the explicit residual awaiting health-zone identification. Audit refs: `ec:lovs:method:bdbv-zone-attributed-corridors:2026-05-22`; `ec:lovs:data:inrb-sitrep-053-visual-promotion:2026-07-06`. Numbers below are the active pre-committed calibration corridors carried forward from the immutable ledger: four from the 20 May top-ranked block and eight from the unpublished 21 May designed-sample block. They are not re-derived from future corridor rankings.

For prior-cycle headline context, the 26 May 2026 publication-state snapshot carried 112 confirmed cases (105 DRC + 7 Uganda per CDC 25 May), 33 confirmed cases unallocated, with a 0.6-21.8% lower-bound range and 1.8-49.4% upper-bound range across the same 76-corridor watchlist. The May-26 -> May-27 advance reflects the DRC MoH 26 May release (16 new confirmed DRC cases not yet zone-attributed), promoted via ECDC 27 May + INRB build-2026-05-27 cross-corroboration.

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
