# Public-Health Use Cases

This page describes practical ways a public-health analyst can use the public BDBV package without needing private LOVS implementation details. It is written for MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer analysts who need a clear path from public evidence to reusable artifacts.

## 1. Source Reconciliation Briefing

Use when different public sources report different counts for the same outbreak period.

Start with:

- `data/public_snapshot.json`
- `data/public_reported_counts.csv`
- `data/public_source_conflicts.json`
- `data/public_source_manifest.json`
- `data/public_source_index.csv`

Workflow:

1. Identify the headline metric in `data/public_snapshot.json`.
2. Check the `primary_source_id`, minimum, maximum, and conflict-anchor source IDs. When matching these IDs to `data/public_source_manifest.json` or `data/public_source_index.csv`, join on the bare `source_id`: some manifest and index rows carry a `-live` retrieval-variant suffix, so strip a trailing `-live` before matching.
3. Use `data/public_reported_counts.csv` to compare source-level values by publisher and date.
4. Use `data/public_source_conflicts.json` to read the interpretation notes.
5. Preserve disagreements by source date instead of forcing a single blended count.

Output you can produce:

- A source-conflict table for an internal situation report.
- A short note explaining why two public sources disagree.
- A dated evidence appendix with source IDs and retrieval dates.

## 2. Health-Zone Attribution Review

Use when headline national totals are newer than the latest public health-zone table.

Start with:

- `data/public_zone_counts_2026-05-26.csv`
- `data/public_snapshot.json`
- `data/public_blindspots.json`

Workflow:

1. Read the health-zone rows as source-attributed records, not as an official live line list.
2. Compare the sum of zone-attributed confirmed counts with the headline confirmed count.
3. Treat any difference as attribution lag unless a later official table assigns the cases.
4. Record the lag in the same style as `health-zone-attribution-lag` in `data/public_blindspots.json`.

Output you can produce:

- A map-ready aggregate health-zone table.
- A clear statement that national totals and zone attribution are running on different clocks.
- A list of zones needing updated public source review.

## 3. Public Reporting Latency Review

Use when you need to understand how long public reporting takes to move from data date to publication and retrieval.

Start with:

- `data/public_latency_observatory.csv`
- `data/public_source_index.csv`
- `DATA_DICTIONARY.md`

Workflow:

1. Filter `data/public_latency_observatory.csv` to rows with `latency_status=measured`.
2. Compare `data_as_of`, `published_at`, and `retrieved_at`.
3. Separate publication lag from archival/retrieval lag.
4. Keep rows with missing `data_as_of` in view rather than dropping them silently.

Output you can produce:

- A simple latency summary by publisher or source tier.
- A list of source types where data-as-of dates are missing.
- A methods note explaining why source clocks should remain separate.

## 4. Calibration Accountability Review

Use when you want to inspect what was publicly registered before outcomes resolved.

Start with:

- `data/public_calibration_ledger.csv`
- `data/public_calibration_status.json`
- `data/public_precommitment_targets.csv`
- `CALIBRATION_RESOLUTION_PUBLIC.md`

Workflow:

1. Read `data/public_calibration_status.json` for open/resolved counts and next resolution date.
2. Use `data/public_precommitment_targets.csv` to understand target roles and inclusion rationale.
3. Keep each ledger row open until public authority evidence supports resolution.
4. Resolve rows using the public process in `CALIBRATION_RESOLUTION_PUBLIC.md`.

Output you can produce:

- A public accountability appendix.
- A list of rows due for future resolution review.
- A resolution memo that cites public authority evidence.

## 5. Aggregate Local Adaptation

Use when a partner wants to adapt the public shapes to aggregate local data without committing private records.

Start with:

- `PUBLIC_ADAPTATION_GUIDE.md`
- `schemas/`
- `examples/local_aggregate_input.example.json`
- `examples/source_manifest_minimal.example.json`
- `examples/public_calibration_commitments.example.csv`

Workflow:

1. Fork or copy the public aggregate shapes.
2. Replace example counts only with public or internally approved aggregate rows.
3. Keep names, addresses, lab accession IDs, genomic sample IDs, contact chains, and private dashboards out of the public repo.
4. Preserve source clocks and blindspots.
5. Contact `frans@arcede.com` for private-data evaluation or implementation support.

Output you can produce:

- A local aggregate evidence package.
- A partner-specific source manifest.
- A private implementation plan that keeps sensitive records outside the public repo.
