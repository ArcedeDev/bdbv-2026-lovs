# Data Dictionary

## `data/public_calibration_status.json`

Block-level public calibration status for open commitments: registration dates, resolution dates, commitment counts, open/resolved counts, remaining days from the snapshot date, public tier/control-role counts, and resolver caveats.

## `data/public_calibration_ledger.csv`

Public accountability table for pre-registered calibration commitments.

| Column | Meaning |
|---|---|
| `ledger_id` | Stable public row identifier. |
| `registered_at` | Date the commitment was registered. |
| `outbreak_id` | Stable outbreak identifier used by this repository. |
| `public_question` | Public-facing resolution question. |
| `source_geography` | Source geography named in the commitment. |
| `target_geography` | Target geography named in the commitment. |
| `horizon_days` | Commitment horizon in days. |
| `resolution_date` | Date after which the public evidence can be reviewed for resolution. |
| `resolution_source_policy` | Public source policy used to resolve the row. |
| `geography_class` | Public geography class such as cross-border, in-country, or unspecified. |
| `forecast_type` | Public commitment type. |
| `public_value_or_tier` | Public tier label, not a probability. |
| `control_role` | Public accountability role. |
| `status` | Open, resolved, or retired. |
| `resolved_value` | Resolution value once reviewed. Blank while open. |
| `score_after_resolution` | Public score after resolution if a public scoring rule is later selected. Blank while open. |
| `notes` | Public context for the row. |
| `commitment_hash` | SHA-256 hash over the public row payload excluding this hash column. |

## `data/public_precommitment_targets.csv`

Public target-set table derived from the calibration ledger. It explains the registered source geography, target geography, public role, inclusion rationale, horizon, status, and resolution policy for each target without publishing probabilities or model components.

## `data/public_blindspots.json`

Public evidence-gap register. Blindspots include restricted publisher bytes, missing `data_as_of` values for latency measurement, health-zone attribution lag, and open calibration rows awaiting resolution.

## `data/public_latency_observatory.csv`

Per-source public latency table. Where `data_as_of`, `published_at`, and `retrieved_at` are available, it reports publication lag, archival lag, and total visibility lag in days. Rows without a usable `data_as_of` remain in the table with `latency_status=missing_data_as_of`.

## `data/public_nowcast_status.json`

Read-only nowcast status for this snapshot. It defines whether a standing scored nowcast has been issued, summarizes readiness inputs, and records which fields are intentionally excluded from the public snapshot when no nowcast is issued.

## `data/public_snapshot.json`

| Field | Meaning |
|---|---|
| `schema_version` | Public export schema version. |
| `snapshot_role` | Identifies this as the sanitized public-source snapshot. |
| `outbreak_id` | Stable outbreak identifier used by this repository. |
| `as_of` | Snapshot publication timestamp. |
| `data_as_of` | Latest data date represented by the headline snapshot. |
| `scope` | Public-use notice, country scope, and authority disclaimer. |
| `reported_counts` | Headline cumulative count ranges with source IDs. Laboratory-confirmed cases are the only cumulative case metric; the cumulative suspected tier is paused and archived (retained as dated provenance, and reactivatable in a future snapshot). |
| `reported_deaths` | Headline cumulative confirmed deaths, keyed by death class (only `confirmed` is published today), each with the same `primary`, `min`, `max`, `primary_source_id`, and `conflicting_source_ids` sub-object shape as `reported_counts`. Omitted when no death class is present. |
| `operational_status` | Point-prevalence operational suspected caseload (under investigation, in isolation, and the active total) at the latest SitRep. Non-cumulative, national-only, and never summed into confirmed. Present only when the operational split is published. |
| `affected_zones` | Health-zone identifiers represented in the snapshot. |
| `zone_attributed_counts` | Confirmed counts attributed to zones with source IDs and source dates. |
| `source_review_geographies` | Public-source health-zone rows kept for source review. |
| `source_ids` | Source IDs used or cross-checked in the snapshot. |
| `source_conflict_note_count` | Number of conflict notes published separately. |
| `reporting_context` | Qualitative context about public reporting visibility and attribution lag. |
| `limitations` | Public-source limitations relevant to interpretation. |

## `data/public_reported_counts.csv`

One row per reported count extracted from the public source manifest.

| Column | Meaning |
|---|---|
| `source_id` | Repository source identifier. |
| `publisher` | Publishing organization or aggregator. |
| `published_at` | Source publication date or timestamp. |
| `retrieved_at` | Retrieval timestamp used by the snapshot. |
| `source_tier` | Source category used for public review. |
| `country_scope` | Countries covered by the source row. |
| `metric` | Normalized count type. |
| `source_field` | Manifest field path from which the value was extracted. |
| `value` | Source-reported value. |

## `data/public_zone_counts_2026-05-29.csv`

One row per health zone in the source-attributed zone table.

| Column | Meaning |
|---|---|
| `zone_id` | Repository health-zone identifier. |
| `source_id` | Source ID for the zone table. |
| `source_data_date` | Data date represented by the source table. |
| `confirmed` | Confirmed cases in the source row. |
| `confirmed_deaths` | Confirmed deaths in the source row. |
| `source_row_status` | Whether the zone appears with data in the source classification. |

## `data/public_source_index.csv`

Public source metadata: source ID, publisher, tier, data-as-of date where available, publication date, retrieval date, license, archive status, content hash, and URL.

## `data/public_source_conflicts.json`

Human-readable conflict notes documenting how public counts differ by source and date.

## `data/release_manifest.json`

Release-level artifact inventory with SHA-256 checksums and byte sizes.

## `schemas/`

Public JSON Schemas for reusable JSON artifacts and aggregate examples. CSV artifacts are documented in this data dictionary.
