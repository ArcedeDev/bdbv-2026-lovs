# Changelog

## 2026-09-17

- **Method change with a series discontinuity, effective from the first snapshot
  dated after 2026-09-15: `death-anchored-sensitivity/v1` is superseded by
  `death-anchored-sensitivity/v2`.**
  - **What changes.** The estimator's clock is clamped to the last published data day.
    v1 measures the confirmed series against the snapshot's publication clock, so the
    delay-adjusted cCFR reweights each past day by `F(T - t)` using a `T` that can run
    past the data. When the publisher goes quiet, silent days read as observed zero
    incidence and the death-anchored burden is revised down on no new data. At the
    19 August 2026 cut, v1 returned a central of 25150 and an upper bound of 27193, down
    from 25942 and 28933, on identical counts of 4965 confirmed cases and 2327 deaths.
  - **Comparability.** Released snapshots are immutable: every snapshot dated on or before
    2026-09-15 keeps its v1 figures and is not restated. A series spanning the changeover
    should treat the death-anchored estimate as discontinuous there.
  - **Scope.** Only the death-anchored estimate changes. `care-vs-ascertainment-sensitivity/v1`
    and `imperial-method-2-cross-check/v1` keep their identifiers.
  - **Disclosure.** From v2 the convergence block publishes `analysis_as_of` and
    `analysis_clock_lag_days` beside `as_of`. When they differ, the burden figures are
    frozen on the last published data day and the difference is a reporting gap.
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
- Added Blocks 5, 6 and 7 to the public calibration record: 31 commitments,
  `bdbv-2026-cal-057` to `bdbv-2026-cal-087`, derived from the pinned ledgers by
  `lovs/forecast/public_register.py` with no model probability. Each row, and the
  2026-09-01 group in `data/public_calibration_status.json`, carries
  `first_published_at: 2026-09-17`.

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
