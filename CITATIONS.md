# Citations

Every prior, scoring rule, and methodology choice in this repository should be traceable to published literature or explicitly labeled as an interim modeling assumption. Citations are carried as named constants inside the modules; this file is the consolidated bibliography.

## Bundibugyo-species transmission priors

The Stage Two transmission priors (`lovs/lovs_priors_bundibugyo.py`) anchor to, or are constrained by:

- Wamala JF, Lukwago L, Malimbo M, Nguku P, et al. *Ebola hemorrhagic fever associated with novel virus strain, Uganda, 2007-2008.* Emerging Infectious Diseases 2010; 16(7): 1087-1092. DOI: [10.3201/eid1607.091525](https://doi.org/10.3201/eid1607.091525). Discovery outbreak, Uganda 2007-2008. 116 confirmed-or-probable cases, 39 deaths (CFR 34%). Median incubation 7 days (range 2-20); transmission cycle 6 weeks with inter-case interval 3-11 days.

- MacNeil A, Farnon EC, Wamala JF, Okware S, et al. *Proportion of deaths and clinical features in Bundibugyo Ebola virus infection, Uganda.* Emerging Infectious Diseases 2010; 16(12): 1969-1972. DOI: [10.3201/eid1612.100627](https://doi.org/10.3201/eid1612.100627). Mean incubation 6.3 days (n=24); survivors 5.7 days, fatal cases 7.4 days. Bleeding prevalence 54%.

- Albariño CG, Shoemaker T, Khristova ML, Wamala JF, et al. *Genomic analysis of filoviruses associated with four viral hemorrhagic fever outbreaks in Uganda and the Democratic Republic of the Congo in 2012.* Virology 2013; 442(2): 97-100. DOI: [10.1016/j.virol.2013.05.001](https://doi.org/10.1016/j.virol.2013.05.001). 2012 DRC Isiro BDBV cluster genetic characterization; consistency check for species-stable transmission dynamics.

- US Centers for Disease Control and Prevention. *History of Ebola Outbreaks.* URL: <https://www.cdc.gov/ebola/outbreaks/index.html>. Primary source for the case-fatality ratio for Bundibugyo virus disease (BVD): the central CFR is 55/169 = 32.5 percent (about 33 percent) aggregated across the two prior BVD outbreaks (Uganda 2007-2008 plus DRC Isiro 2012), and the 26 to 40 percent scenario band is the Wilson 95% confidence interval [25.9 percent, 39.9 percent] of that proportion. The earlier 24/30/40 percent set (Imperial 18 May 2026) was superseded by 26/33/40 in the Imperial 20 May 2026 update. US public-domain.

- Van Kerkhove MD, Bento AI, Mills HL, Ferguson NM, Donnelly CA. *A review of epidemiological parameters from Ebola outbreaks to inform early public health decision-making.* Scientific Data 2015; 2:150019. DOI: [10.1038/sdata.2015.19](https://doi.org/10.1038/sdata.2015.19). Documents the parameter gap that BDBV R0 had not been estimated as of the review; therefore the Stage Two BDBV R prior is tracked as an interim modeling prior in `data/evidence-chains.json`, not as a directly source-grounded R0 estimate.

## Zaire-species priors (Stage One baseline; carried for backward compatibility)

- Faye O, Boëlle PY, Heleze E, Faye O, et al. *Chains of transmission and control of Ebola virus disease in Conakry, Guinea, in 2014: an observational study.* Lancet Infectious Diseases 2015; 15(3): 320-326. DOI: [10.1016/S1473-3099(14)71075-8](https://doi.org/10.1016/S1473-3099(14)71075-8). Zaire-species serial interval mean 11.6 days (95% CI 8.4-15.6).

- WHO Ebola Response Team. *Ebola virus disease in West Africa, the first 9 months of the epidemic and forward projections.* NEJM 2014; 371(16): 1481-1495. DOI: [10.1056/NEJMoa1411100](https://doi.org/10.1056/NEJMoa1411100). Zaire-species serial interval mean 15.3 days (13.5-17.1); early R between 1.5 and 2.0.

## Transmission modeling and cross-prefecture spread

- Glynn JR, Bower H, Johnson S, et al. *Variability in intrahousehold transmission of Ebola virus, and estimation of the household secondary attack rate.* Journal of Infectious Diseases 2018; 217(2): 232-237. DOI: [10.1093/infdis/jix579](https://doi.org/10.1093/infdis/jix579). Household secondary-attack rate ~15%; the ~100x cross-prefecture down-scaling and the resulting per-case-hazard coefficient 0.003 in `lovs/lovs_next_zone.py` are engineering heuristics, not fitted to this source.

- Lekone PE, Finkenstädt BF. *Statistical inference in a stochastic epidemic SEIR model with control intervention: Ebola as a case study.* Biometrics 2006; 62(4): 1170-1177. DOI: [10.1111/j.1541-0420.2006.00609.x](https://doi.org/10.1111/j.1541-0420.2006.00609.x). Stochastic SEIR framework reference only; it predates the 2007 Bundibugyo outbreak and does not ground a BDBV-specific R0 estimate.

- Camacho A, Kucharski A, Aki-Sawyerr Y, et al. *Temporal changes in Ebola transmission in Sierra Leone and implications for control requirements: a real-time modelling study.* PLOS Currents Outbreaks 2015. DOI: [10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2](https://doi.org/10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2).

- Cori A, Ferguson NM, Fraser C, Cauchemez S. *A new framework and software to estimate time-varying reproduction numbers during epidemics.* American Journal of Epidemiology 2013; 178(9): 1505-1512. DOI: [10.1093/aje/kwt133](https://doi.org/10.1093/aje/kwt133).

## Mode A retrospective substrate

- Backer JA, Wallinga J. *Spatiotemporal analysis of the 2014 Ebola epidemic in West Africa.* PLOS Computational Biology 2016; 12(12): e1005210. DOI: [10.1371/journal.pcbi.1005210](https://doi.org/10.1371/journal.pcbi.1005210). 62 prefectures × 74 weeks panel; the canonical substrate for `lovs/lovs_validation.mode_a_backtest_wa_2014` and `mode_a_backtest_wa_2014_t3`.

## Stage Three per-prefecture T3 covariates (v3)

- Institut National de la Statistique de Guinée. *Recensement Général de la Population et de l'Habitation (RGPH) 2014.* Per-prefecture population estimates for Guinea.
- Liberia Institute of Statistics and Geo-Information Services (LISGIS). *National Population and Housing Census 2008.* Per-county population estimates for Liberia.
- Statistics Sierra Leone. *National Population and Housing Census 2015.* Per-district population estimates for Sierra Leone.
- OpenStreetMap contributors. Road network density derived from the OSM planet snapshot.
- World Bank. *Roads, paved (% of total roads).* Used for road-connectivity index calibration.
- WHO Health Hub for Africa (HHA) and national Ministry-of-Health facility registries. Reference-hospital geocoding for `healthcare_distance_km`.
- ACLED. *Armed Conflict Location & Event Data 2014.* Per-prefecture political-violence intensity for `conflict_access_score`.

## Candidate next-lever for corridor discrimination

- Wesolowski A, Qureshi T, Boni MF, Sundsøy PR, Johansson MA, Rasheed SB, Engø-Monsen K, Buckee CO. *Impact of human mobility on the emergence of dengue epidemics in Pakistan.* Proceedings of the National Academy of Sciences 2015; 112(38): 11887-11892. DOI: [10.1073/pnas.1504964112](https://doi.org/10.1073/pnas.1504964112). Mobile-phone-derived human mobility as a quantitative predictor of epidemic spread; the primary-research basis cited for the call-detail-record next-lever.
- Wesolowski A, Buckee CO, Engø-Monsen K, Metcalf CJE. *Connecting mobility to infectious diseases: the promise and limits of mobile phone data.* Journal of Infectious Diseases 2016; 214(suppl 4): S414-S420. DOI: [10.1093/infdis/jiw273](https://doi.org/10.1093/infdis/jiw273). Mobility data (CDR or surveyed transport flows) is the most-cited candidate next-lever for cross-prefecture spread discrimination.

- Bengtsson L, Gaudart J, Lu X, Moore S, et al. *Using mobile phone data to predict the spatial spread of cholera.* Scientific Reports 2015; 5: 8923. DOI: [10.1038/srep08923](https://doi.org/10.1038/srep08923). Demonstration of CDR-mobility-informed spatial-spread prediction; blueprint reference for the corridor model's planned Stage Four extension.

## Regional corridor expansion and cross-border risk framing

- International Health Regulations (2005). *Annex 1B: Core capacity requirements* and the operational implementation guidance around points of entry and transport preparedness. The IHR framework is the normative basis for structured PoE and airport surveillance escalation.
- World Health Organization. *Temporary recommendations for ports, airports, and ground crossings* used in the 2026 BDBV context (IHR temporary recommendations statements issued during this event). These documents set the public-health rationale for why land and air corridors are monitored as separate operational channels.
- International Air Transport Association (IATA) and International Civil Aviation Organization (ICAO) technical advisories for health emergency coordination at points of departure/arrival are generally treated as operational context, not model inputs; they are useful for regional activation logic.

## Corridor groundedness (informational, not model inputs)

The brief's calibration corridors and twenty-zone watchlist are derived from
the model's candidate-target list. The following sources document real
cross-border movement infrastructure that an observer can use to sanity-check
whether the model's named corridors track real population flows. They are
descriptive context, not inputs to the model.

- Government of Uganda + Government of DRC + TradeMark Africa. *Mahagi One Stop Border Post (OSBP), feasibility and construction documents 2023-2025.* Mahagi (DRC) and Goli (Uganda) form one of DRC's busiest border crossings, on the axis Arua → Bunia → Kisangani → Mombasa (the East African Northern Corridor). The Goli-Mahagi-Bunia road is one of three priority cross-border roads jointly named by Uganda and DRC. URL: <https://magazine.feaffa.com/mahagi-osbp-to-cut-drcs-transit-time-by-30-percent/>.

- UNHCR Uganda Refugee Operations. *Rhino Camp settlement, Arua district.* Population approximately 95,929 as of late-2025 reporting; with persistent trade flows to Ariwara market in Ituri Province (DRC). Source for refugee-mobility groundedness of Ituri-to-Northern-Uganda corridors.

- ACAPS. *DRC: Conflict and displacement in Nord Kivu and Ituri.* Periodic briefing notes documenting CODECO and ADF activity in Ituri Province and surrounding eastern DRC.

## Zone boundary and centroid sources

- Référentiel Géographique Commun (RGC.cd). *Zones de Santé de la RDC, 519 zones de santé*, `zone_sante190613.zip` / `zone_sante.shp`, last update 2019-06-07, validation 2019-06-13. URL: <https://rgc.cd/images/RGCdata/zone_sante190613.zip>. Retrieval SHA-256 `e0b672d7ad6f387c26d13ad357185d1152139b91fb7ba70235def55888db9702`. Source for the Rwampara Health Zone public boundary centroid used in `data/zones.json`; RGC row reliability is `Basse`, so the coordinate is used as a boundary centroid for map placement, not a field-verified facility point.

## Conflict context (2026)

- Human Rights Watch. *World Report 2026: Democratic Republic of Congo.* Documents CODECO massacres in Ituri and ADF activity in Ituri and North Kivu Provinces in 2025-2026. URL: <https://www.hrw.org/world-report/2026/country-chapters/democratic-republic-of-congo>.

- UNHCR. *Eastern DRC displacement, late 2025.* Approximately 7.3 million internally displaced persons in eastern DRC, the highest IDP count in Africa.

- ACLED. *Armed Conflict Location & Event Data, eastern DRC 2025-2026.* Source for the conflict-intensity covariates referenced in `lovs/lovs_covariates.py` and the qualitative grounding for the ascertainment-gap reasoning in Panel 1 of the brief.

## Scoring rules

- Brier GW. *Verification of forecasts expressed in terms of probability.* Monthly Weather Review 1950; 78(1): 1-3. Brier score, used as the primary point-discrimination metric.

- Gneiting T, Raftery AE. *Strictly proper scoring rules, prediction, and estimation.* JASA 2007; 102(477): 359-378. CRPS sample-based estimator (equations 17-20).

- Bracher J, Ray EL, Gneiting T, Reich NG. *Evaluating epidemic forecasts in an interval format.* PLOS Computational Biology 2021; 17(2): e1008618. DOI: [10.1371/journal.pcbi.1008618](https://doi.org/10.1371/journal.pcbi.1008618). Weighted Interval Score (WIS), used as the primary interval-quality metric.

## Nowcasting and reporting-delay methodology

These references ground the outbreak data-latency observatory and standing scored nowcast, a LOVS public good derived from this reference instance (specification page `reference:lovs-public-goods:latency-nowcast`). They are consolidated here to keep the methodology lineage complete.

- Caleo G, et al. *Clinical and epidemiological performance of WHO Ebola case definitions: a systematic review and meta-analysis.* The Lancet Infectious Diseases 2020; 20(11): 1324-1338. DOI: [10.1016/S1473-3099(20)30193-6](https://doi.org/10.1016/S1473-3099(20)30193-6). Source for the limited specificity of the suspected-case definition (about 36 percent), which grounds the decision to nowcast the combined confirmed-plus-suspected total with confirmed tracked as a secondary series.

- Günther F, et al. *Nowcasting the COVID-19 pandemic in Bavaria.* Biometrical Journal 2021; 63(3): 490-502. DOI: [10.1002/bimj.202000112](https://doi.org/10.1002/bimj.202000112). Hierarchical Bayesian nowcasting of occurred-but-not-yet-reported counts under a time-varying reporting delay; basis for recency-weighting the estimated delay distribution.

- Cramer EY, et al. *The United States COVID-19 Forecast Hub dataset.* Scientific Data 2022; 9: 462. DOI: [10.1038/s41597-022-01517-w](https://doi.org/10.1038/s41597-022-01517-w). Reference design for a fixed-cadence, pre-committed, auto-scored public forecast registry; basis for the standing nowcast's resolution cadence.

- Lawless JF. *Adjustments for reporting delays and the prediction of occurred but not reported events.* Canadian Journal of Statistics 1994; 22(1): 15-31. The original reporting-delay adjustment and right-truncation correction underlying nowcasting (the JSTOR stable identifier 10.2307/3315826 refers to a different item, so no DOI link is given here).

- Höhle M, an der Heiden M. *Bayesian nowcasting during the STEC O104:H4 outbreak in Germany, 2011.* Biometrics 2014; 70(4): 993-1002. DOI: [10.1111/biom.12194](https://doi.org/10.1111/biom.12194). Foundational Bayesian nowcast of occurred-but-not-yet-reported case counts under a reporting-delay distribution; the methodological basis for the standing nowcast.

- McGough SF, Johansson MA, Lipsitch M, Menzies NA. *Nowcasting by Bayesian Smoothing: a flexible, generalizable model for real-time epidemic tracking.* PLOS Computational Biology 2020; 16(4): e1007735. DOI: [10.1371/journal.pcbi.1007735](https://doi.org/10.1371/journal.pcbi.1007735). Generalizable real-time nowcasting model; basis for recency-weighting the estimated delay distribution.

## Live data sources

All sources cited or cross-checked in the snapshot are registered in `data/bundibugyo-2026/manifest.json` with URL, retrieval timestamp, and SHA-256 provenance. Entries marked `public_bytes` are byte-archived under `data/bundibugyo-2026/raw/`; entries marked `private_restricted_bytes` keep hash-only provenance and do not redistribute publisher bytes.

### Registered sources

- World Health Organization. *Disease Outbreak News item 2026-DON602: Ebola disease caused by Bundibugyo virus, Democratic Republic of the Congo and Uganda.* Published 2026-05-15; retrieved 2026-05-20. URL: <https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON602>. SHA-256 `8b7fb1e1c8403b7a6015c804a3cd818c04b649ca23d791fe957e59119818218f`. WHO content licensed CC BY-NC-SA 3.0 IGO.

- Africa Centres for Disease Control and Prevention. *Africa CDC Declares the Ongoing Bundibugyo Ebola Outbreak a Public Health Emergency of Continental Security.* Published 2026-05-18; retrieved 2026-05-20. URL: <https://africacdc.org/news-item/africa-cdc-declares-the-ongoing-bundibugyo-ebola-outbreak-a-public-health-emergency-of-continental-security/>. Retrieval SHA-256 `4efa5e02df04e766353f9af62e6f87a1fca21936aa287772bae5ba5932e20c8b`. Raw publisher bytes are not redistributed because reuse terms were not confirmed. Source for the 395 suspected / 106 deaths reconciliation floor and the PHECS declaration date.

- Wikipedia contributors. *2026 Ituri Province Ebola epidemic*, revision `1355255528`. Retrieved 2026-05-20. Version URL: <https://en.wikipedia.org/w/index.php?title=2026_Ituri_Province_Ebola_epidemic&oldid=1355255528>. SHA-256 `319cdbd2b616df4301eba94171448e3081a0a1a9e701404bcdf1030a5b80e8fa`. Licensed CC BY-SA 4.0: <https://creativecommons.org/licenses/by-sa/4.0/>. Consensus aggregator citing Reuters, BBC, CDC HAN, MSF, ECDC, AP, NYT, Al Jazeera, CNN, and Imperial College London for the 19-20 May case figures. Source for the 653 suspected / 144 deaths primary values and a non-authoritative 51-confirmed cross-check.

- World Health Organization. *Director-General PHEIC determination statement: Epidemic of Ebola disease in DRC and Uganda determined a Public Health Emergency of International Concern.* Published 2026-05-17. URL: <https://www.who.int/news/item/17-05-2026-epidemic-of-ebola-disease-in-the-democratic-republic-of-the-congo-and-uganda-determined-a-public-health-emergency-of-international-concern>. SHA-256 `e1f8ea89d80de061e8f79bb8e60d96bd98d68ddf676bad9ef03469adf536dd51`. Source for the 10 confirmed minimum band (8 Ituri + 2 Kampala) and for deconfirming the reported Kinshasa case.

- World Health Organization. *WHO Director-General's opening remarks at the media briefing on Ebola outbreak in DRC and Uganda.* Published 2026-05-20; retrieved 2026-05-21. URL: <https://www.who.int/news-room/speeches/item/who-director-general-s-opening-remarks-at-the-media-briefing-on-ebola-outbreak-in-drc-and-uganda-20-may-2026>. SHA-256 `38adc40602c6609e4a727251acd8896ca6c27ca922d855d532b35a2bcf3ff067`. Source for the 53 confirmed primary value (51 DRC + 2 Kampala) and for the DRC / Uganda split.

- World Health Organization. *WHO Director-General's opening remarks at the Member State information session on outbreaks of Ebola and hantavirus.* Published 2026-05-22; retrieved 2026-05-22. URL: <https://www.who.int/news-room/speeches/item/who-director-general-s-opening-remarks-at-the-member-state-information-session-on-outbreaks-of-ebola-and-hantavirus-22-may-2026>. SHA-256 `40367f2982e766cd8d12f4f223e420d6c4739f4ed1345c8584541619ec061495`. Source for the 22 May endpoint: 82 confirmed DRC cases, seven confirmed DRC deaths, almost 750 suspected cases, 177 suspected deaths, and two imported Uganda cases including one death; risk revised to very high nationally, high regionally, and low globally.

- World Health Organization. *First meeting of the IHR Emergency Committee regarding the epidemic of Ebola Bundibugyo virus disease in the Democratic Republic of the Congo and Uganda 2026 - Temporary recommendations.* Published 2026-05-22; retrieved 2026-05-22. URL: <https://www.who.int/news/item/22-05-2026-first-meeting-of-the-ihr-emergency-committee-regarding-the-epidemic-of-ebola-bundibugyo-virus-disease-in-the-democratic-republic-of-the-congo-and-uganda-2026-temporary-recommendations>. SHA-256 `0e9c3f1bd14aca694c115df854ec2bd290f24240b29c84fc5617e7374d3ba4e4`. Source for the 22 May IHR temporary recommendations, DRC/Uganda/regional risk framing, and Uganda no-onward-transmission status among contacts of the two imported cases.

- US Centers for Disease Control and Prevention. *Ebola Disease: Current Situation.* Published 2026-05-23; retrieved 2026-05-24. URL: <https://www.cdc.gov/ebola/situation-summary/index.html>. SHA-256 `1fa637ba0cb4ab11c50438b226bf00ebeca62757eda73a5738f4e6f231cdaed8`. US public-domain. Source for the 23 May endpoint: 746 suspected DRC cases, 83 confirmed DRC cases, 176 suspected DRC deaths, nine confirmed DRC deaths, five confirmed Uganda cases, one confirmed Uganda death, and three additional Uganda cases linked to previous cases.

- World Health Organization, Regional Office for Africa. *Weekly External Situation Report 01, Ebola disease (Bundibugyo) outbreak DRC and Uganda*, data as of 2026-05-18. URL: <https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-uganda-weekly-external-situation>. SHA-256 `56a9f829090ae2823a155c04b53a8cc28a4f0e99006f1fe483245d4b02938a64`. Landing page byte-archived; the underlying versioned sitrep PDF is linked from the page.

- US Centers for Disease Control and Prevention, Health Alert Network. *Notice HAN00530: Ebola Disease Outbreak in the Democratic Republic of the Congo and Uganda.* Published 2026-05. URL: <https://www.cdc.gov/han/php/notices/han00530.html>. SHA-256 `039a988227d240a466321d79f550e3af70f1b18db60b6627e55ee15535a25c6a`. US public-domain. US CDC clinician guidance for evaluating returning travelers.

- Imperial College London, MRC Centre for Global Infectious Disease Analysis (with WHO Health Emergencies Programme, WHO Uganda, WHO Regional Office for Africa). *Estimation of the size of the outbreak of Ebola disease caused by Bundibugyo virus in the Democratic Republic of the Congo: May 20, 2026 update.* Published 2026-05-20. URL: <https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-update-20-05-2026/>. Raw publisher bytes and extracted Table 3 PoE values are not redistributed here pending permission or a primary license-footer archive; per-item hashes and archive status are in `data/bundibugyo-2026/manifest.json`. Estimates 400-900 total cases in DRC as of 20 May 2026 (values over 1,000 not excluded) via two independent approaches: population-movement extrapolation + Uganda exports, and deaths-back-projection through the case-fatality ratio. This update supersedes the 18 May 2026 report (archived landing page SHA-256 `647610bf3450407ec3313e2a17642b0ee9cbec7f14c72665318993f06acaf8b4`), correcting the CFR scenario set from 24/30/40 to 26/33/40 percent and the deaths input from 88 to 131. **This is the most prominent public quantitative output for the outbreak as of 20 May 2026.** It does not publish a reporting-completeness posterior, pre-committed calibration points, or a structured cross-border corridor-risk watch list, which is the gap LOVS Stage Two is built to fill.

- European Centre for Disease Prevention and Control. *Ebola virus disease outbreak, Democratic Republic of the Congo and Uganda, 19 May 2026.* Published 2026-05-19; retrieved 2026-05-20. URL: <https://www.ecdc.europa.eu/en/ebola-virus-disease-outbreak-democratic-republic-congo-and-uganda-19-may-2026>. SHA-256 `3fd9968b77caa5b86de241c556c0d425bbd9f4ba5a83b59dbab0fce8304382d8`. ECDC content licensed CC BY 4.0 unless otherwise stated. Corroborating source for the 19-20 May case figures.

- Institut National de Santé Publique (INSP), Institut National de Recherche Biomédicale (INRB), and Unité de Modélisation et Intelligence Épidémique (UMIE). *Ebola_DRC_2026: processed INSP SitRep MVE per-health-zone case and death series* (SitRep MVE 001, 002, 004-012, 2026; report 003 not committed); build `build-2026-05-27-e40bc9e`, data as of 2026-05-26. Consortium led by INRB Kinshasa/INOHA and INSP, in collaboration with the University of Oxford and Northeastern University. URL: <https://github.com/INRB-UMIE/Ebola_DRC_2026> (registered in `data/external_sources/source_registry.json` as `inrb-umie-ebola-drc-2026-github`). The consortium code is MIT-licensed (Kraemer Lab, University of Oxford); the per-health-zone series is INSP SitRep material, reused with attribution to INSP and citation of the specific report number and date, with distribution to be confirmed with INSP before external republication (INSP contact pierre.akilimali@insp.cd). LOVS uses the parsed per-zone confirmed/suspected/deaths values as source-attributed factual inputs to the INSP per-zone reconciliation surface; see the upstream release for the verbatim per-zone tables.

## Citation guidance

If you use this repository, please cite:

> Moore F. *LOVS Stage Two: Bundibugyo 2026 outbreak application.* Released 2026. Available at <https://github.com/ArcedeDev/bdbv-2026-lovs>.

And cite the underlying methodology references above where directly applicable.

## Full reference list

Consolidated bibliography across the brief, the LOVS pipeline priors, and the
joint WHO-Imperial College MRC GIDA report referenced as an external
cross-check. Grouped by role.

### Primary outbreak sources

- [Imperial College MRC GIDA + WHO, 20 May 2026 update](https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-update-20-05-2026/), McCabe R, Ebbarnezh L, Okware S, et al., *Estimation of the size of the outbreak of Ebola disease caused by Bundibugyo virus in the Democratic Republic of the Congo: May 20, 2026 update* (Imperial College London, 20 May 2026, superseding the 18 May 2026 report); the most prominent public quantitative output for the outbreak as of 20 May 2026 and the external cross-check on the inferred-trajectory chart.

### Methodology priors used by LOVS or Imperial

- [Imai et al. 2020, Imperial College COVID-19 Response Team Report 1](https://www.imperial.ac.uk/media/imperial-college/medicine/mrc-gida/2020-01-17-COVID19-Report-1.pdf), geographic-spread extrapolation blueprint cited by the Imperial-WHO BDBV report (20 May 2026 update).
- [Rosello et al. 2015 eLife](https://doi.org/10.7554/eLife.09015), *Ebola virus disease in the Democratic Republic of the Congo, 1976-2014*; source of the BVD onset-to-death gamma distribution used in the deaths-back-projection arm of the Imperial-WHO BDBV estimate (20 May 2026 update).
- [Wamala et al. 2010 EID](https://doi.org/10.3201/eid1607.091525), the Bundibugyo 2007-2008 Uganda discovery outbreak; source of the BDBV case-fatality-ratio range and the inter-case interval prior.
- [MacNeil et al. 2010 EID](https://doi.org/10.3201/eid1612.100627), BVD clinical features in Uganda; source of the mean incubation period and the bleeding-prevalence prior.
- [Albarino et al. 2013 Virology](https://doi.org/10.1016/j.virol.2013.05.001), BDBV genomic analysis across Uganda and DRC outbreaks; consistency check for species-stable transmission dynamics.
- [Van Kerkhove et al. 2015 Scientific Data](https://doi.org/10.1038/sdata.2015.19), Ebola epidemiological-parameter review; source for the BDBV R0 evidence gap tracked in `data/evidence-chains.json`.
- [Camacho et al. 2015 PLOS Currents Outbreaks](https://currents.plos.org/outbreaks/article/temporal-changes-in-ebola-transmission-in-sierra-leone-and-implications-for-control-requirements-a-real-time-modelling-study/), reporting-delay distribution prior used in LOVS Stage Two; DOI [10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2](https://doi.org/10.1371/currents.outbreaks.406ae55e83ec0b5193e30856b9235ed2).
- [Cori et al. 2013 American Journal of Epidemiology](https://doi.org/10.1093/aje/kwt133), the R_t estimation framework shared across the LOVS and Imperial citation lineage.
- [Backer and Wallinga 2016 PLOS Computational Biology](https://doi.org/10.1371/journal.pcbi.1005210), the 2014 West Africa Ebola spatiotemporal panel that LOVS Mode A uses as its retrospective substrate.
- [Bracher et al. 2021 PLOS Computational Biology](https://doi.org/10.1371/journal.pcbi.1008618), the Weighted Interval Score used in the LOVS calibration scoring.

### Candidate inputs not yet integrated

- [Wesolowski et al. 2016 Journal of Infectious Diseases](https://doi.org/10.1093/infdis/jiw273), the canonical reference for mobility data (call detail records or surveyed transport flows) as a candidate next-lever input for corridor-spread discrimination; not currently used in the pipeline.
- Kraemer MUG, Yang CH, Gutierrez B, Wu CH, Klein B, Pigott DM, Open COVID-19 Data Working Group, du Plessis L, Faria NR, Li R, Hanage WP, Brownstein JS, Layan M, Vespignani A, Tian H, Dye C, Pybus OG, Scarpino SV. *The effect of human mobility and control measures on the COVID-19 epidemic in China.* Science 2020; 368(6490): 493-497. DOI: [10.1126/science.abb4218](https://doi.org/10.1126/science.abb4218). Primary-source example showing that near-real-time mobility volumes can strongly predict early geographic spread before restrictions alter network behavior; useful as a general mobility-grounding reference for regional watch design.
- Brockmann D, Helbing D. *The hidden geometry of complex, network-driven contagion phenomena.* Science 2013; 342(6164): 1337-1342. DOI: [10.1126/science.1245200](https://doi.org/10.1126/science.1245200). Conceptual grounding for why effective network distance can matter more than geographic distance when prioritizing cross-border surveillance corridors.

### Cross-border and point-of-entry operational framing

- World Health Organization. *International Health Regulations (2005).* Official WHO text and annexes. URL: <https://www.who.int/ihr/en/>. Legal and operational basis for surveillance, response, and core capacities at designated points of entry.
- World Health Organization. *International Health Regulations (2005) core capacities at points of entry.* URL: <https://iris.who.int/handle/10665/181589>. Technical framing for PoE preparedness and why airports, ports, and ground crossings should be treated as distinct surveillance surfaces in regional expansion work.
- World Health Organization. *Designation of points of entry under the International Health Regulations (2005): technical brief.* URL: <https://iris.who.int/handle/10665/376842>. Practical grounding for how PoE designation and readiness should inform corridor activation logic without being mistaken for modeled transmission probability.
