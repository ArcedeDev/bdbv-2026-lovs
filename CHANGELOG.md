# Changelog

## 2026-09-23

- **Public health dataset: a metric name now says what kind of figure a value is.**
  - **What was wrong.** The exporter named source fields by keyword: any field
    containing "confirmed", "death" or "suspected" became `confirmed_cases`, `deaths`
    or `suspected_cases`, the names the Data Dictionary gives the cumulative counts.
    Those metrics also carried 24-hour increments (`new_confirmed_24h`,
    `new_confirmed_deaths_24h`, `community_deaths_24h`, `cte_deaths_24h`,
    `suspected_cases_day`, `suspected_deaths_day`), per-zone counts, zone-table
    reconciliation and row-count bookkeeping, isolation censuses, active caseload,
    health-worker infections, percentages (`cfr_suspected_pct`, a province's
    `confirmedOccupancyPct`), suspected deaths, probable deaths and death alerts. A
    filter on `confirmed_cases` at `COD` returned increments, censuses and percentages
    beside the DRC cumulative count. Percentages and SitRep numbers had unit `count`,
    and suspected and probable death rows had a `confirmed_only` basis.
  - **What changes.** A source field joins a named series only when it is listed as
    that kind of figure; nothing is matched by keyword. Cumulative counts:
    `confirmed_cases`, `deaths`, `suspected_cases`, `suspected_deaths`,
    `probable_cases`, `probable_deaths` and the `country_scope_` totals, which gain
    `country_scope_probable_cases`. 24-hour counts: `new_confirmed_cases_24h`,
    `new_confirmed_deaths_24h` (with `community_deaths_24h` and `cte_deaths_24h`),
    `new_suspected_cases_24h` and `new_suspected_deaths_24h`. Per-zone counts:
    `health_zone_confirmed_cases`, `health_zone_deaths` and
    `health_zone_suspected_cases`, all at location `COD` (WHO AFRO's first SitRep had
    labelled its DRC zones `COD; UGA`). Caseload on the report date:
    `active_confirmed_cases`, `country_scope_active_confirmed_cases` and
    `active_suspected_cases`. Every other field keeps its own name with dots as
    underscores, so bookkeeping, operational tables, subsets and source metadata read
    as what they are. In May every Uganda case was imported, so the May fields for
    Uganda's imported cases and deaths join `confirmed_cases` and `deaths` at `UGA`;
    from June Uganda's imported and local split keeps its own names. `unit` is `percent`, `proportion`, `days`, `bytes`, `GBP` or
    `identifier` where a value is not a count; `sitrep_number` is an `identifier`.
    `basis` is empty for suspected, probable and alert death counts. Reconciled
    headline rows keep their metric names; their probable-death row loses its
    `confirmed_only` basis.
  - **Comparability.** Row ids, values and row counts do not change. In
    `reported_counts.csv`, 1600 metric labels, 1279 units, 179 basis labels and 21
    locations change, and `timeline.csv` changes the same source rows.
    `confirmed_cases` falls from 1050 rows to 306, `deaths` from 1000 to 284 and
    `suspected_cases` from 164 to 31. A consumer that read 24-hour, per-zone or
    caseload figures from those metrics should switch to the new names.
  - **Gate.** `python3 -m lovs.snapshot_contract --check-dataset` fails when a
    cumulative metric takes a nested field, a field whose name marks an increment,
    caseload or rate, or a field of another case classification; when one source gives
    a cumulative series two values at one location; when a death field is exported
    under a case metric; when a percentage field is not unit `percent`; or when a
    `timeline.csv` row disagrees with its `reported_counts.csv` row. The exporter
    stops on a new top-level field that names a case class until it is classified.

- **Public health dataset: each extracted value now carries its own geography.**
  - **What was wrong.** Every value a source reported was labelled with that source's
    `country_scope`. An INSP SitRep's scope is COD, yet each SitRep also prints the
    country-scope total (DRC plus the Uganda anchor) and the Uganda anchor itself.
    SitRep 130's 7793 (7773 DRC plus 20 Uganda) and its 20 were exported as
    `confirmed_cases` at location `COD`, and its deaths terms the same way. Earlier
    SitReps did the same with `cases_confirmed_total` and `cases_confirmed_uganda`,
    and SitReps 112 to 118, recorded with a two-country scope, labelled their DRC
    figures `COD; UGA`.
  - **What changes.** In `reported_counts.csv`, `timeline.csv` and the workbook, a
    `source_extracted_metric` row takes its location from its own source field:
    `COD` for DRC terms, `UGA` for the Uganda anchor and other Uganda fields,
    `COD; UGA` for country-scope totals, and otherwise the geography the source
    reports on. Country-scope totals move to the metrics
    `country_scope_confirmed_cases` and `country_scope_deaths`. INSP's DRC cumulative
    fields `cumul_cas_confirmes_drc` and `cumul_deces_parmi_confirmes_drc` join
    `confirmed_cases` and `deaths`, so the DRC deaths rows now carry a `basis`. The
    country-scope probable death keeps its own metric, `country_scope_probable_deaths`.
    The reconciled headline rows, which had an empty location, are `COD; UGA`.
    `timeline.csv` gains a `location` column after `basis`.
  - **Comparability.** Row ids, values and row counts do not change. Across all
    SitReps, 1236 locations and 469 metric labels do. A filter on `metric` and
    `location` now returns a single geography. A consumer that read country-scope
    totals as `confirmed_cases` or `deaths` should switch to the `country_scope_`
    metrics.
  - **Gate.** `python3 -m lovs.snapshot_contract --check-dataset` fails when a
    country-scope row is exported at the wrong location, or when the latest SitRep's
    total, DRC and Uganda terms disagree with the snapshot contract.

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
