# Bundibugyo virus, DRC, 2026: public evidence snapshot and adaptation package

This repository accompanies Arcede's public-evidence methodology brief on the 2026 Ebola disease outbreak caused by Bundibugyo virus (BDBV). It publishes the public-facing evidence package for the 26 November 2026 snapshot: browser/PDF brief, visuals, citations, source manifest, public count tables, calibration-accountability artifacts, schemas, and aggregate-only adaptation examples.

The code that builds this package is published here under Apache 2.0 (see [`LICENSES.md`](LICENSES.md)). The repo is designed to be useful to MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer analysts; private-data workflows, private source captures, and restricted publisher bytes stay outside it.

**What this is.** A reproducible public-evidence publication package for one dated snapshot. It shows how open outbreak reporting can be reconciled across publishers, source dates, retrieval dates, source-use status, and health-zone attribution lag without treating public reporting as complete line-list surveillance.

**Bottom line.** This work is a methodology contribution in support of responding authorities. It is not an official outbreak dashboard, case-management system, contact-tracing system, forecast, travel advisory, or deployment recommendation. The public package is meant to help partners inspect the evidence trail, adapt the aggregate data shapes, preserve calibration accountability, and identify where public reporting is incomplete.

**Authorities and standing.** The Democratic Republic of the Congo Ministry of Public Health is the lead authority on the DRC response. Uganda Ministry of Health leads Uganda’s surveillance and preparedness following its closed episode. WHO and Africa CDC confirmed the end of that episode on [27 August 2026](https://www.afro.who.int/countries/uganda/news/uganda-ends-ebola-outbreak-following-completion-42-day-countdown). Its cumulative totals remain historical context. INRB confirmed BDBV by polymerase chain reaction. WHO, Africa CDC, CDC, ECDC, and other public-health institutions publish official or reference materials used in this source trail. This repository does not speak for any of them.

**Author:** [Frans Moore](https://www.linkedin.com/in/frans-moore/), [frans@arcede.com](mailto:frans@arcede.com).

## Start Here

Different readers should use different parts of this package:

- **Public-health readers and responders:** start with the published page, the browser brief, [`PUBLIC_HEALTH_USE_CASES.md`](PUBLIC_HEALTH_USE_CASES.md), [`LIMITATIONS.md`](LIMITATIONS.md), and the source-use policy below. You do not need to run anything to interpret the public snapshot.
- **Data reviewers:** start with [`data/public_snapshot.json`](data/public_snapshot.json), [`data/public_reported_counts.csv`](data/public_reported_counts.csv), [`data/public_zone_counts_2026-05-29.csv`](data/public_zone_counts_2026-05-29.csv), [`data/public_source_manifest.json`](data/public_source_manifest.json), [`data/public_source_index.csv`](data/public_source_index.csv), and [`data/public_source_conflicts.json`](data/public_source_conflicts.json).
- **Methodology and accountability reviewers:** start with [`METHODOLOGY_PUBLIC.md`](METHODOLOGY_PUBLIC.md), [`METHOD_CARDS_PUBLIC.md`](METHOD_CARDS_PUBLIC.md), [`WORKED_SNAPSHOT_REVIEW.md`](WORKED_SNAPSHOT_REVIEW.md), [`READONLY_INTERFACE_PUBLIC.md`](READONLY_INTERFACE_PUBLIC.md), [`CALIBRATION_LEDGER_PUBLIC.md`](CALIBRATION_LEDGER_PUBLIC.md), [`CALIBRATION_RESOLUTION_PUBLIC.md`](CALIBRATION_RESOLUTION_PUBLIC.md), and the calibration files under `data/`.
- **Analysts adapting the public package:** start with [`PUBLIC_ADAPTATION_GUIDE.md`](PUBLIC_ADAPTATION_GUIDE.md), [`schemas/`](schemas/), [`examples/`](examples/), `python3 examples/summarize_public_package.py`, and `python3 examples/review_public_methodology.py`.
- **Citation reviewers:** start with [`CITATIONS.md`](CITATIONS.md), [`data/release_manifest.json`](data/release_manifest.json), and [`LICENSES.md`](LICENSES.md).

Primary artifacts:

- Published page: <https://www.arcede.com/bdbv-2026>
- Browser brief: [`brief/brief.html`](brief/brief.html)
- PDF brief: [`deliverables/brief.pdf`](deliverables/brief.pdf)
- Public snapshot data: [`data/public_snapshot.json`](data/public_snapshot.json)
- Public method cards: [`METHOD_CARDS_PUBLIC.md`](METHOD_CARDS_PUBLIC.md)
- Worked snapshot review: [`WORKED_SNAPSHOT_REVIEW.md`](WORKED_SNAPSHOT_REVIEW.md)
- Public read-only interface: [`READONLY_INTERFACE_PUBLIC.md`](READONLY_INTERFACE_PUBLIC.md)
- Public adaptation guide: [`PUBLIC_ADAPTATION_GUIDE.md`](PUBLIC_ADAPTATION_GUIDE.md)
- Public schemas: [`schemas/README.md`](schemas/README.md)
- Public example consumer: [`examples/summarize_public_package.py`](examples/summarize_public_package.py)

## Why This Exists

At the 26 May 2026 reporting state, public BDBV reporting was spread across authority updates, reference reports, health-zone tables, dashboards, and publisher pages with different clocks. The public package complements outbreak size estimates and official situation reports by preserving:

1. **A source-conflict-aware public evidence trail.** Counts remain tied to source IDs, publication dates, retrieval dates, source-use status, and conflict notes.
2. **A dated public snapshot.** Headline counts, affected zones, health-zone rows, source-review geographies, limitations, and checksums are published in reusable machine-readable files.
3. **Calibration accountability.** Pre-registered public questions, target sets, status summaries, blindspots, method cards, and resolution policy are visible before outcomes resolve.
4. **Latency and blindspot tracking.** The package preserves `data_as_of`, `published_at`, and `retrieved_at` separately so analysts can see which reporting lags are measured and which are not.
5. **Aggregate-only adaptation.** Partners can fork the public shapes, map their own aggregate public or internally approved data into those shapes, and keep private records out of the repo.

This is the useful public-good surface: an evidence package, not an operational response system.

## Current Public Snapshot

The current public artifact is a 26 November 2026 analytic snapshot, based on independently reviewed INSP SitRep #196. The report date is 26 November, printed publication date 27 November, and WordPress publication 27 November at 19:29:11 UTC (the posted file is a corrected version; its pages print no correction note). SitRep #195 (data 25 November) was reviewed in the same release and is the previous edition. Retrieval and review clocks remain separate.

- **12,176 confirmed cases and 5,978 confirmed deaths in DRC.** Uganda's separately dated historical closeout adds 20 cases and two deaths: **12,196 cases and 5,980 deaths in COD|UGA**. Kenya, Nigeria and France remain separately scoped.
- **3,381 country-scope recoveries:** 3,363 DRC plus 18 Uganda. SR196 alone reports 87 confirmations, 35 confirmed deaths (25 community and 10 intra-CTE), and 14 recoveries. These daily figures are not cumulative differences.
- **69 named zones across eight provinces.** SR196 adds no zone; SR192 added Kibirizi (Nord-Kivu). Named rows sum to 12,176 cases and 5,485 deaths; 493 unallocated Ituri deaths remain separate and reconcile to 5,978. No geographic redistribution is inferred.
- **Care:** the national care census is kept this edition. SR196's cover prints 1,183, which equals the sum of the seven provinces that print a census (Nord-Kivu 625, Ituri 386, Haut-Uele 75, Bas-Uele 50, Nord-Ubangi 26, Tshopo 15, Sud-Ubangi 6). It was withheld in every edition from SR179 to SR195. Provincial censuses are kept as printed: Nord-Kivu closes against SR195 with its printed flows, and the Ituri, Haut-Uele and Bas-Uele non-closures are recorded. The national patient-status split is not printed.
- **Contacts and laboratory:** 28,109/35,472 contacts seen (79.2%) across the five provinces reporting a cohort; the cohort changes between editions (Ituri from 27,955 to 16,746 under follow-up), so the fall from 82.1% is not a matched-cohort trend. The Bas-Uele zero is withheld. The national laboratory count is kept as printed (87 positive of 750 analysed); the printed 10.9% is withheld because 87/750 is 11.6%. The Haut-Uele and Bas-Uele counts analysed are withheld because each contradicts its own breakdown.
- **Alerts and points of entry:** Table 3 prints every total (5,004 alerts, 518 validated as suspects, 261 transferred) and closes; no provincial alert trend is drawn. Table 5 prints seven province columns; counts are kept as counts. The Sud-Kivu completeness repeat and the Bas-Uele traveller count (5,409 in the table against 4,509 in the narrative) are withheld, so no national traveller total is carried.
- **Vaccination (per-province record, kept as printed):** Nord-Kivu prints 6,459 people vaccinated (5,726 front-line workers and 733 contacts); Tshopo prints 15,751 people with a printed 105.3% coverage and no denominator; Haut-Uele prints 5,474 of 6,386 pre-registered people (printed 85.7%). Provinces count different groups, no national total or cross-province coverage is made, and no daily figure is derived where none is printed. No vaccine product name is carried. No vaccine is licensed for Bundibugyo virus disease, so these counts say nothing about protection.

The country-scope headline is 12196 confirmed cases. The DRC national count is 12176 confirmed cases; 12176 confirmed cases are officially zone-attributed across 69 official source zones, leaving 0 confirmed cases unallocated. The descriptive 619-corridor watchlist is not a validated current-outbreak forecast; revised source allocations do not validate prediction.

Cumulative confirmed cases, confirmed active cases and current operational stocks remain separate concepts. Public reporting is not complete line-list surveillance. Registered forecasts, scoring dates and historical released snapshots remain unchanged; descriptive watchlists do not recommend deployment or forecast spread.

The snapshot keeps two count concepts on orthogonal axes that are never summed:

- **Cumulative case classification** (confirmed cases and confirmed deaths) is the only epidemiological case-count surface; it is monotonic and traceable to source.
- **Operational response state** (contacts and the isolation/CTE census) is point-prevalence information that rises and falls. A suspected-only axis is published only when the source supplies that classification.

We report only lab-confirmed cumulative cases. We do not reproduce the INRB dashboard "total" of confirmed plus cases under investigation plus cases in isolation, because that sum conflates a cumulative stock with a point-in-time operational caseload.

The snapshot also keeps two clocks that should not be collapsed:

- **Headline public counts** summarize country-scope public reporting.
- **Health-zone attributed counts** preserve the latest source-attributed zone table available in this package.

National totals may move faster than zone attribution. This repo records that lag instead of scaling health-zone rows up to match a headline total.

For the current contract, no DRC confirmed case remains unallocated against the INSP per-health-zone table; Uganda's 20 confirmed cases are country-scoped and are never assigned to DRC zones. Confirmed-death attribution retains the 1-3 week trailing-note disclosure. The corridor surface remains a descriptive 610-corridor watchlist; its adjusted 50% ranges span 0.5-100.0% lower and 1.5-100.0% upper. It does not recommend deployment or forecast spread.

## Public Export Contract

The sanitized public export contract is:

- [`data/public_snapshot.json`](data/public_snapshot.json) - headline counts, affected zones, source IDs, source-review geographies, reporting context, and limitations.
- [`data/public_reported_counts.csv`](data/public_reported_counts.csv) - source-level reported count values extracted from the public source manifest.
- [`data/public_zone_counts_2026-05-29.csv`](data/public_zone_counts_2026-05-29.csv) - source-attributed health-zone counts.
- [`data/public_source_manifest.json`](data/public_source_manifest.json) and [`data/public_source_index.csv`](data/public_source_index.csv) - public source metadata, clocks, URLs, archive status, and hashes.
- [`data/public_source_conflicts.json`](data/public_source_conflicts.json) - public-source disagreement notes.
- [`data/public_calibration_ledger.csv`](data/public_calibration_ledger.csv) - pre-registered public accountability commitments, resolution dates, status, and commitment hashes.
- [`data/public_calibration_status.json`](data/public_calibration_status.json) - block-level calibration status, scored/unscored counts, remaining days, and resolver caveats.
- [`data/public_precommitment_targets.csv`](data/public_precommitment_targets.csv) - public target set roles and inclusion rationale.
- [`data/public_blindspots.json`](data/public_blindspots.json) - evidence gaps and unscoreable public states.
- [`data/public_latency_observatory.csv`](data/public_latency_observatory.csv) - public reporting latency where source clocks allow measurement.
- [`data/public_nowcast_status.json`](data/public_nowcast_status.json) - standing nowcast interface status for this snapshot.
- [`data/release_manifest.json`](data/release_manifest.json) - artifact inventory with SHA-256 checksums.

## What You Can Do With This Repo

- Inspect the public source trail behind the snapshot.
- Compare conflicting public counts without forcing false agreement.
- Reuse the aggregate file shapes in a partner environment.
- Track which public-source evidence states are currently blindspots.
- Review public calibration commitments before they resolve.
- Walk through the public method on the current real snapshot with [`WORKED_SNAPSHOT_REVIEW.md`](WORKED_SNAPSHOT_REVIEW.md).
- Summarize the package locally with:

```bash
python3 examples/summarize_public_package.py
python3 examples/review_public_methodology.py
```

- Check the public artifacts and tests locally with:

```bash
python3 -m lovs.public_exports --check
python3 export_public_health_dataset.py --check
python3 -m lovs.public_repo_hygiene
python3 -m unittest discover -s tests
```

The dataset check compares bytes, including the workbook's compressed streams, so run it with a Python linked to stock zlib. [`runbooks/public-health-dataset.md`](runbooks/public-health-dataset.md) explains each message it prints.

## What Is Deliberately Not Published

The public repository excludes:

- Partner/private-data adaptation workflows.
- Private process and method-development artifacts.
- Private-data adapters.
- Private source captures, raw restricted publisher bytes, line lists, laboratory records, genomic sample records, contact-tracing chains, and private operational dashboards.
- Weekly forecast quantiles (each registered week publishes its commitment hash) and forecast registration plans that hold case detail (their rules are restated in each artifact's inputs).
- Optional external extraction backends that some sources are pulled through.

Calibration probabilities and the resolvers that score them are published; see [`CALIBRATION_LEDGER_PUBLIC.md`](CALIBRATION_LEDGER_PUBLIC.md).

For aggregate-only reuse, use [`PUBLIC_ADAPTATION_GUIDE.md`](PUBLIC_ADAPTATION_GUIDE.md), [`schemas/`](schemas/), and [`examples/`](examples/). For private-data evaluation or implementation support, contact `frans@arcede.com`.

## Public Source-Use Policy

Operational partners may hold line lists, contact-tracing records, laboratory timestamps, genomic data, field investigation notes, and non-public dashboards that are more complete than public reporting. This snapshot is built for the narrower public-source layer.

- Official sources can support public claims after provenance review.
- Credible media, local reporting, and watch-list signals can trigger source review, but are not treated as confirmed counts without independent confirmation.
- Restricted publisher bytes and private source captures are not redistributed here.
- Public numerical claims should remain traceable to a source ID, publication or retrieval date, and source-use status.
- When source clocks differ, this package preserves the distinction between `data_as_of`, `published_at`, and `retrieved_at` rather than collapsing them into one date.

## Repository Structure

```text
bdbv-2026-lovs/
|-- README.md
|-- PUBLIC_HEALTH_USE_CASES.md
|-- PUBLIC_ADAPTATION_GUIDE.md
|-- METHOD_CARDS_PUBLIC.md
|-- WORKED_SNAPSHOT_REVIEW.md
|-- READONLY_INTERFACE_PUBLIC.md
|-- CALIBRATION_LEDGER_PUBLIC.md
|-- CALIBRATION_RESOLUTION_PUBLIC.md
|-- METHODOLOGY_PUBLIC.md
|-- DATA_DICTIONARY.md
|-- LIMITATIONS.md
|-- CITATIONS.md
|-- LICENSES.md
|-- NOTICE
|-- brief/
|   |-- brief.html
|   `-- visuals/
|-- deliverables/
|   `-- brief.pdf
|-- data/
|   |-- public_snapshot.json
|   |-- public_source_manifest.json
|   |-- public_source_index.csv
|   |-- public_reported_counts.csv
|   |-- public_zone_counts_2026-05-29.csv
|   |-- public_source_conflicts.json
|   |-- public_calibration_ledger.csv
|   |-- public_calibration_status.json
|   |-- public_precommitment_targets.csv
|   |-- public_blindspots.json
|   |-- public_latency_observatory.csv
|   |-- public_nowcast_status.json
|   `-- release_manifest.json
|-- schemas/
|   |-- README.md
|   `-- *.schema.json
|-- examples/
|   |-- README.md
|   |-- local_aggregate_input.example.json
|   |-- source_manifest_minimal.example.json
|   |-- public_calibration_commitments.example.csv
|   |-- review_public_methodology.py
|   `-- summarize_public_package.py
|-- lovs/        (method modules, exports and gates)
|-- tools/       (helper scripts)
|-- docs/, runbooks/
|-- *.py         (top-level refresh, release, ingest and calibration scripts)
`-- tests/
```

The per-release vaccine scenarios are in `data/vaccine-field-effectiveness/` (see its README).

## License

See [`LICENSE`](LICENSE), [`LICENSES.md`](LICENSES.md), and [`NOTICE`](NOTICE). Code is licensed under Apache 2.0; prose, data and generated artifacts under CC BY 4.0; third-party material keeps its own terms.

Source-attribution lag remains explicitly tracked.
