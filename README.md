# Bundibugyo virus, DRC and Uganda, 2026: public evidence snapshot

This repository packages the public-facing artifacts for Arcede's 28 May 2026 BDBV evidence snapshot: the methodology brief, generated visuals, citations, manifest, and provenance metadata.

The repository is intentionally artifact-first. It does not publish the proprietary LOVS method engine, partner/private-data runner, internal engineering plans, calibration workbench, or source-ingest automation. Those surfaces remain internal to Arcede.

## Start Here

- Published page: <https://www.arcede.com/bdbv-2026>
- Browser brief: [`brief/brief.html`](brief/brief.html)
- PDF brief: [`deliverables/brief.pdf`](deliverables/brief.pdf)
- Public snapshot data: [`data/public_snapshot.json`](data/public_snapshot.json)
- Public count tables: [`data/public_reported_counts.csv`](data/public_reported_counts.csv) and [`data/public_zone_counts_2026-05-26.csv`](data/public_zone_counts_2026-05-26.csv)
- Public calibration ledger: [`data/public_calibration_ledger.csv`](data/public_calibration_ledger.csv) and [`CALIBRATION_LEDGER_PUBLIC.md`](CALIBRATION_LEDGER_PUBLIC.md)
- Source and citation context: [`CITATIONS.md`](CITATIONS.md), [`data/public_source_manifest.json`](data/public_source_manifest.json), and [`data/public_source_index.csv`](data/public_source_index.csv)
- Public methodology and field definitions: [`METHODOLOGY_PUBLIC.md`](METHODOLOGY_PUBLIC.md), [`DATA_DICTIONARY.md`](DATA_DICTIONARY.md), and [`LIMITATIONS.md`](LIMITATIONS.md)

## What This Is

This is a reproducible public-evidence publication package for a dated snapshot. It preserves the public outputs and the source trail needed to interpret them.

For public-health partners who need reusable data rather than a narrative brief, the sanitized public export contract is:

- [`data/public_snapshot.json`](data/public_snapshot.json) for headline counts, affected zones, source IDs, source-review geographies, and limitations.
- [`data/public_reported_counts.csv`](data/public_reported_counts.csv) for source-level reported count values extracted from the public source manifest.
- [`data/public_zone_counts_2026-05-26.csv`](data/public_zone_counts_2026-05-26.csv) for source-attributed health-zone counts.
- [`data/public_calibration_ledger.csv`](data/public_calibration_ledger.csv) for pre-registered accountability commitments, resolution dates, status, and commitment hashes.
- [`data/public_source_conflicts.json`](data/public_source_conflicts.json) for public-source disagreement notes.
- [`data/public_source_index.csv`](data/public_source_index.csv) and [`data/release_manifest.json`](data/release_manifest.json) for provenance and checksums.

It is not an official outbreak dashboard, case-management system, contact-tracing system, forecast, travel advisory, or deployment recommendation. It does not speak for the Democratic Republic of the Congo Ministry of Public Health, Uganda Ministry of Health, INRB, WHO, Africa CDC, CDC, ECDC, or any response authority.

## What Is Deliberately Not Published

The public repository excludes:

- The LOVS implementation and model-running scripts.
- Partner/private-data adaptation workflows.
- Internal `.process/` and `.specs/` engineering artifacts.
- Calibration workbench inputs and unpublished method-development files.
- Source-prep and release automation used by Arcede operators.
- Public-health dataset/workbook exports that expose method internals or machine-readable product surfaces.

If you need partner adaptation or private-data evaluation, contact Arcede directly rather than forking this public artifact package.

## Public Source-Use Policy

Operational partners may hold line lists, contact-tracing records, laboratory timestamps, genomic data, field investigation notes, and internal dashboards that are more complete than public reporting. This snapshot is built for the narrower public-source layer.

- Official sources can support public claims after provenance review.
- Credible media, local reporting, and watch-list signals can trigger source review, but are not treated as confirmed counts without independent confirmation.
- Restricted publisher bytes and private source captures are not redistributed here.
- Public numerical claims should remain traceable to a source ID, publication or retrieval date, and source-use status.

## License

See [`LICENSE`](LICENSE), [`LICENSES.md`](LICENSES.md), and [`NOTICE`](NOTICE). Public artifacts are provided for review and citation under the repository's stated terms; excluded Arcede methods and internal automation are not licensed by this public package.
