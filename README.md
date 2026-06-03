# Bundibugyo virus, DRC and Uganda, 2026: public evidence snapshot and adaptation package

This repository accompanies Arcede's public-evidence methodology brief on the 2026 Ebola disease outbreak caused by Bundibugyo virus (BDBV). It publishes the public-facing evidence package for the 1 June 2026 snapshot: browser/PDF brief, visuals, citations, source manifest, public count tables, calibration-accountability artifacts, schemas, and aggregate-only adaptation examples.

This is intentionally not the full private LOVS implementation. The public repo is designed to be useful to MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer analysts while keeping unpublished method assets, private-data workflows, source collection automation, and mutable scoring tools outside the public package.

**What this is.** A reproducible public-evidence publication package for one dated snapshot. It shows how open outbreak reporting can be reconciled across publishers, source dates, retrieval dates, source-use status, and health-zone attribution lag without treating public reporting as complete line-list surveillance.

**Bottom line.** This work is a methodology contribution in support of responding authorities. It is not an official outbreak dashboard, case-management system, contact-tracing system, forecast, travel advisory, or deployment recommendation. The public package is meant to help partners inspect the evidence trail, adapt the aggregate data shapes, preserve calibration accountability, and identify where public reporting is incomplete.

**Authorities and standing.** The Democratic Republic of the Congo Ministry of Public Health is the lead authority on the DRC response. Uganda Ministry of Health is the lead authority on the Uganda response. INRB confirmed BDBV by polymerase chain reaction. WHO, Africa CDC, CDC, ECDC, and other public-health institutions publish official or reference materials used in this source trail. This repository does not speak for any of them.

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

This is the useful public-good surface. It is not the private model runner.

## Current Public Snapshot

The current public artifact is a 1 June 2026 publication snapshot: headline counts are dated 1 June 2026 (reviewed INRB SitRep #018 promotion) and per-health-zone attribution is dated 29 May 2026 (the latest internally consistent INRB-UMIE build). Based on the public source package in this repo:

- **355 confirmed cases** as the current country-scope confirmed endpoint in [`data/public_snapshot.json`](data/public_snapshot.json). Laboratory-confirmed cases are the only cumulative case metric this snapshot publishes.
- **61 confirmed deaths** as the cumulative confirmed-death band.
- **243 confirmed cases officially zone-attributed across 23 INSP per-zone source zones** in [`data/public_zone_counts_2026-05-29.csv`](data/public_zone_counts_2026-05-29.csv) are the primary descriptive spread signal. The 355-case headline leaves **112 confirmed cases unallocated** to a public health-zone row because the headline clock is newer than the INRB-UMIE INSP per-health-zone attribution clock.
- **289 suspected cases active at 2026-06-01** (116 under investigation + 173 in isolation) in the separate `operational_status` block. This is a point-in-time operational caseload, national-only, not a cumulative case count, and never added to confirmed.
- **15 open public calibration commitments** in [`data/public_calibration_ledger.csv`](data/public_calibration_ledger.csv), with resolution dates spanning 19 June, 20 June, and 25 June 2026.

The snapshot keeps two count concepts on orthogonal axes that are never summed:

- **Cumulative case classification** (confirmed cases and confirmed deaths) is the only epidemiological case-count surface; it is monotonic and traceable to source.
- **Operational status** (suspected cases under investigation or in isolation) is a point-prevalence caseload that rises and falls and is reported only as a labeled non-cumulative operational item.

We report only lab-confirmed cumulative cases. We do not reproduce the INRB dashboard "total" of confirmed plus cases under investigation plus cases in isolation, because that sum conflates a cumulative stock with a point-in-time operational caseload.

The snapshot also keeps two clocks that should not be collapsed:

- **Headline public counts** summarize country-scope public reporting.
- **Health-zone attributed counts** preserve the latest source-attributed zone table available in this package.

National totals may move faster than zone attribution. This repo records that lag instead of scaling health-zone rows up to match a headline total.

For the current contract, the 112 confirmed cases not yet officially zone-attributed are treated as source-attribution lag, not as hidden model redistribution. Confirmed-death attribution has an explicit 1-3 week lag disclosure while clinical review catches up. The corridor surface is a descriptive 152-corridor watchlist over the reviewed source-load vector; its adjusted 50% interval ranges span 0.5-38.0% lower and 1.5-74.6% upper, and it is not deployment ranking or a forecast.

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
python3 -m lovs.public_repo_hygiene
python3 -m unittest discover -s tests
```

## What Is Deliberately Not Published

The public repository excludes:

- The private LOVS implementation and model-running scripts.
- Partner/private-data adaptation workflows.
- Private process and method-development artifacts.
- Calibration workbench inputs and unpublished scoring implementation.
- Source collection automation and release operations.
- Machine-readable outputs that expose probability intervals, feature weights, private adapters, model parameters, or mutable resolver tools.
- Raw restricted publisher bytes, line lists, laboratory records, genomic sample records, contact-tracing chains, and private operational dashboards.

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
|-- lovs/
|   |-- public_exports.py
|   `-- public_repo_hygiene.py
`-- tests/
    |-- test_public_exports.py
    `-- test_public_repo_hygiene.py
```

## License

See [`LICENSE`](LICENSE), [`LICENSES.md`](LICENSES.md), and [`NOTICE`](NOTICE). Public artifacts are provided for review, citation, and public-good adaptation under the repository's stated terms; excluded Arcede methods and private automation are not licensed by this public package.
