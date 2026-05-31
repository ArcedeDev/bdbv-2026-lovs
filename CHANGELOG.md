# Changelog

## 2026-05-30

- Added a public calibration ledger lite for pre-registered accountability commitments:
  - `data/public_calibration_commitments.json`
  - `data/public_calibration_ledger.csv`
  - `data/public_calibration_status.json`
  - `data/public_precommitment_targets.csv`
  - `data/public_blindspots.json`
  - `data/public_latency_observatory.csv`
  - `data/public_nowcast_status.json`
  - `READONLY_INTERFACE_PUBLIC.md`
  - `CALIBRATION_LEDGER_PUBLIC.md`
- Added sanitized public-health exports for partner review:
  - `data/public_snapshot.json`
  - `data/public_reported_counts.csv`
  - `data/public_zone_counts_2026-05-26.csv`
  - `data/public_source_conflicts.json`
  - `data/public_source_index.csv`
  - `data/release_manifest.json`
- Added public methodology, data dictionary, and limitations documents.
- Added a public adaptation guide and grounded public aggregate examples for self-serve partner review.
- Added public-health use cases, a calibration-resolution protocol, public JSON schemas, and a read-only public package summary script.
- Added public method cards, a worked real-snapshot review, and a read-only methodology review script.
- Added CI checks that the public export artifacts are current and do not include sensitive model-internal fields.
