# Data Dictionary

## `data/public_snapshot.json`

| Field | Meaning |
|---|---|
| `schema_version` | Public export schema version. |
| `snapshot_role` | Identifies this as the sanitized public-source snapshot. |
| `outbreak_id` | Stable outbreak identifier used by this repository. |
| `as_of` | Snapshot publication timestamp. |
| `data_as_of` | Latest data date represented by the headline snapshot. |
| `scope` | Public-use notice, country scope, and authority disclaimer. |
| `reported_counts` | Headline confirmed, suspected, and death count ranges with source IDs. |
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

## `data/public_zone_counts_2026-05-26.csv`

One row per health zone in the source-attributed zone table.

| Column | Meaning |
|---|---|
| `zone_id` | Repository health-zone identifier. |
| `source_id` | Source ID for the zone table. |
| `source_data_date` | Data date represented by the source table. |
| `confirmed` | Confirmed cases in the source row. |
| `suspected` | Suspected cases in the source row. |
| `confirmed_deaths` | Confirmed deaths in the source row. |
| `suspected_deaths` | Suspected deaths in the source row. |
| `source_row_status` | Whether the zone appears with data in the source classification. |

## `data/public_source_index.csv`

Public source metadata: source ID, publisher, tier, publication date, retrieval date, license, archive status, content hash, and URL.

## `data/public_source_conflicts.json`

Human-readable conflict notes documenting how public counts differ by source and date.

## `data/release_manifest.json`

Release-level artifact inventory with SHA-256 checksums and byte sizes.
