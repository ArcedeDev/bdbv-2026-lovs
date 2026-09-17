# Changelog

## 2026-09-17

- Published calibration Blocks 5, 6 and 7 (31 pins, all resolving
  2026-10-01T23:59:59Z):
  - Block 5, six corridor pins in `data/calibration-ledger.json`, a falsification
    test of the corridor model's saturated hazard.
  - Blocks 6 and 7, 25 operational and structural pins in
    `data/operational-calibration-ledger.json`, generated from the frozen extract
    `data/operational-series-2026-09-01.json`.
- **Publication date.** These blocks were pinned in a local commit on 2026-09-01
  and first published here on 2026-09-17. The public, verifiable pre-registration
  date is 2026-09-17. The operational pins regenerate exactly from the frozen
  extract (last data day 2026-08-28), which `tests/test_operational_ledger.py`
  enforces, so their values cannot carry later information.
- Interim Block 5 evidence recheck through 2026-09-15 in
  `data/calibration-resolution-evidence.json`. No point is resolved.
- The isolation audit in `lovs/forecast/isolationevents.py` now reads SitRep
  packets only through the extract's last data day.

## 2026-06-02

- Bumped the public snapshot `schema_version` to `1.1`.
- Added `reported_deaths` to `data/public_snapshot.json`: cumulative confirmed
  deaths as a headline metric, projected to the same min/max/primary sub-object
  shape as `reported_counts` (primary, min, max, primary_source_id,
  conflicting_source_ids). Only the `confirmed` death class is published today;
  the field is omitted entirely when no death class is present.

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
  - `data/public_zone_counts_2026-05-29.csv`
  - `data/public_source_conflicts.json`
  - `data/public_source_index.csv`
  - `data/release_manifest.json`
- Added public methodology, data dictionary, and limitations documents.
- Added a public adaptation guide and grounded public aggregate examples for self-serve partner review.
- Added public-health use cases, a calibration-resolution protocol, public JSON schemas, and a read-only public package summary script.
- Added public method cards, a worked real-snapshot review, and a read-only methodology review script.
- Added CI checks that the public export artifacts are current and do not include sensitive model-internal fields.
