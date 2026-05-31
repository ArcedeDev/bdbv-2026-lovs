# Public Adaptation Guide

This guide is for MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer public-health analysts who want to reuse the public BDBV evidence package without needing private LOVS implementation details.

The goal is self-serve public-good use: a partner should be able to inspect the source trail, adapt the public data shapes to their own aggregate reporting, document evidence gaps, and preserve calibration accountability. Private line lists, lab records, case investigations, and operational dashboards should not be committed to this repository.

For private-data evaluation or implementation support, contact `frans@arcede.com`.

## What You Can Do From The Public Repo

- Review the dated public source trail in `data/public_source_manifest.json` and `data/public_source_index.csv`.
- Compare headline counts and public-source disagreements in `data/public_snapshot.json`, `data/public_reported_counts.csv`, and `data/public_source_conflicts.json`.
- Review source-attributed health-zone rows in `data/public_zone_counts_2026-05-26.csv`.
- Inspect public calibration commitments in `data/public_calibration_ledger.csv` and their status in `data/public_calibration_status.json`.
- Track evidence gaps and unscoreable states in `data/public_blindspots.json`.
- Measure public reporting latency where source dates allow it in `data/public_latency_observatory.csv`.
- Reuse the public method cards in `METHOD_CARDS_PUBLIC.md`.
- Follow the current real-snapshot walkthrough in `WORKED_SNAPSHOT_REVIEW.md`.
- Use the files in `examples/` as grounded public templates for a local aggregate-only adaptation.

## Minimal Public Adaptation Workflow

1. Define the snapshot.
   - Choose an `as_of` timestamp for the publication snapshot.
   - Choose a `data_as_of` date for the latest data represented by the headline counts.

2. Build an aggregate source manifest.
   - Start from `examples/source_manifest_minimal.example.json`.
   - Record public or internally approved source IDs, publishers, source dates, publication dates, retrieval dates, and source-use status.
   - Keep raw restricted publisher bytes and non-public records out of the public repo.

3. Build an aggregate count package.
   - Start from `examples/local_aggregate_input.example.json`.
   - Use aggregate national/provincial/health-zone rows only.
   - Do not include names, exact addresses, phone numbers, lab accession IDs, contact-tracing chains, genomic sample IDs, or other person-level data.

4. Preserve source-clock distinctions.
   - `data_as_of` means the date represented by the reported data.
   - `published_at` means when the source published the report.
   - `retrieved_at` means when you captured or reviewed it.
   - Keeping these separate prevents false disagreements when publishers update on different cadences.

5. Document blindspots.
   - Use `data/public_blindspots.json` as the pattern.
   - Track missing source dates, restricted source bytes, health-zone attribution lag, open calibration resolution, and any unscoreable public evidence state.

6. Preserve calibration accountability.
   - Use `examples/public_calibration_commitments.example.csv` as a safe row shape.
   - Register public questions before their resolution window.
   - Keep status read-only until public evidence supports resolution.

7. Run a method review.
   - Use `python3 examples/review_public_methodology.py` on this repository to see the method applied to the current public snapshot.
   - Recreate the same checks for your aggregate package: source reconciliation, source clocks, attribution lag, blindspots, calibration accountability, and nowcast boundary.

## What This Does Not Provide

This public package does not provide a LOVS model runner, private-data adapter, mutable resolver, scoring implementation, source collection automation, probability intervals, feature weights, or model parameters. Those boundaries protect method integrity and reduce the chance that a public repository is mistaken for an operational response system.

The public package is still useful without those pieces: it gives partners a stable way to review public evidence, adapt aggregate schemas grounded in the current snapshot, document uncertainty and blindspots, and evaluate public calibration commitments after resolution.

## When To Contact Arcede

Contact `frans@arcede.com` when:

- you want support adapting the package to non-public aggregate or line-list-derived data;
- you need implementation help inside an official or partner environment;
- you want to compare public artifacts against internal reporting clocks;
- you want to discuss licensing, attribution, or collaboration around method development.
