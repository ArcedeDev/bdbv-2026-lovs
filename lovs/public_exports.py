# SPDX-License-Identifier: Apache-2.0
"""Build the public-health export contract for the LOVS artifact repo."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import sys
from collections.abc import Iterable, Mapping
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_EXPORT_SOURCE_PATH = pathlib.Path("data/public_export_source.json")
SOURCE_MANIFEST_PATH = pathlib.Path("data/public_source_manifest.json")

PUBLIC_SNAPSHOT_PATH = pathlib.Path("data/public_snapshot.json")
PUBLIC_REPORTED_COUNTS_PATH = pathlib.Path("data/public_reported_counts.csv")
PUBLIC_ZONE_COUNTS_PATH = pathlib.Path("data/public_zone_counts_2026-05-26.csv")
PUBLIC_SOURCE_CONFLICTS_PATH = pathlib.Path("data/public_source_conflicts.json")
PUBLIC_SOURCE_INDEX_PATH = pathlib.Path("data/public_source_index.csv")
RELEASE_MANIFEST_PATH = pathlib.Path("data/release_manifest.json")

PUBLIC_DOC_PATHS = (
    pathlib.Path("METHODOLOGY_PUBLIC.md"),
    pathlib.Path("DATA_DICTIONARY.md"),
    pathlib.Path("LIMITATIONS.md"),
    pathlib.Path("CHANGELOG.md"),
)

SENSITIVE_PUBLIC_SNAPSHOT_KEYS = {
    "analysis_dependency_audit",
    "calibration_blocks",
    "calibration_clock",
    "confirmation_backlog_50",
    "corridors",
    "delay_prior",
    "evidence_chain_id",
    "evidence_chain_ids",
    "gamma_shape_rate",
    "generations",
    "hypothesis_id",
    "mode_b_hypotheses",
    "per_zone_under_ascertainment_bands",
    "publication_latency_50",
    "reporting_completeness_50",
    "risk_adj_lower_50",
    "risk_adj_lower_95",
    "risk_adj_upper_50",
    "risk_adj_upper_95",
    "risk_raw_lower_50",
    "risk_raw_upper_50",
    "sensitivity_delay_priors",
    "transmission",
    "visibility",
}

SOURCE_COUNT_FIELD_LABELS = {
    "cases_confirmed": "confirmed_cases",
    "cases_confirmed_drc": "confirmed_cases_drc",
    "cases_confirmed_uga": "confirmed_cases_uga",
    "cases_suspected": "suspected_cases",
    "cases_suspected_drc": "suspected_cases_drc",
    "cases_suspected_drc_approx": "suspected_cases_drc_approx",
    "cases_suspected_uga": "suspected_cases_uga",
    "deaths": "deaths",
    "deaths_approx": "deaths_approx",
    "deaths_drc": "deaths_drc",
    "deaths_uga": "deaths_uga",
    "officially_reported_at_that_date": "reported_cases_at_source_date",
}

STATIC_PUBLICATION_ARTIFACTS = (
    pathlib.Path("README.md"),
    pathlib.Path("CITATIONS.md"),
    pathlib.Path("LICENSE"),
    pathlib.Path("LICENSES.md"),
    pathlib.Path("NOTICE"),
    pathlib.Path("brief/brief.html"),
    pathlib.Path("brief/visuals/ascertainment_band_per_zone.png"),
    pathlib.Path("brief/visuals/ascertainment_band_per_zone.svg"),
    pathlib.Path("brief/visuals/corridor_risk.png"),
    pathlib.Path("brief/visuals/corridor_risk.svg"),
    pathlib.Path("brief/visuals/detection_depth.png"),
    pathlib.Path("brief/visuals/detection_depth.svg"),
    pathlib.Path("brief/visuals/per_zone_snapshot.png"),
    pathlib.Path("brief/visuals/per_zone_snapshot.svg"),
    pathlib.Path("brief/visuals/pre_registration_timeline.png"),
    pathlib.Path("brief/visuals/pre_registration_timeline.svg"),
    pathlib.Path("brief/visuals/visibility_gap.png"),
    pathlib.Path("brief/visuals/visibility_gap.svg"),
    pathlib.Path("deliverables/brief.pdf"),
    pathlib.Path("data/public_export_source.json"),
    pathlib.Path("data/public_source_manifest.json"),
    pathlib.Path("data/natural_earth_outlines.json"),
    pathlib.Path("data/zones.json"),
)


def _read_json(relpath: pathlib.Path) -> Any:
    return json.loads((REPO_ROOT / relpath).read_text(encoding="utf-8"))


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _csv_text(fieldnames: list[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key, "")) for key in fieldnames})
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    return str(value)


def _source_entries(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _public_snapshot(source: Mapping[str, Any]) -> dict[str, Any]:
    attribution = source.get("attribution_lag_disclosure", {})
    zone_attributed_counts = {}
    for zone_id, row in sorted(source.get("zone_attributed_counts", {}).items()):
        zone_attributed_counts[zone_id] = {
            "confirmed": row.get("confirmed"),
            "source_id": row.get("source_id"),
            "source_published_at": row.get("source_published_at"),
            "province": row.get("province") or None,
        }

    source_review_geographies = []
    for row in source.get("source_review_geographies", []):
        source_review_geographies.append(
            {
                "zone_id": row.get("zone_id"),
                "zone_name": row.get("zone_name"),
                "province": row.get("province"),
                "confirmed": row.get("confirmed"),
                "suspected": row.get("suspected"),
                "deaths": row.get("deaths"),
                "source_id": row.get("source_id"),
                "source_published_at": row.get("source_published_at"),
                "report_date": row.get("report_date"),
                "publication_date": row.get("publication_date"),
                "review_status": "source_review",
            }
        )

    return {
        "schema_version": "1.0",
        "snapshot_role": "public_source_snapshot",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "data_as_of": source.get("data_as_of"),
        "scope": {
            "pathogen": "Bundibugyo virus",
            "countries": ["COD", "UGA"],
            "use": "Public-source situational awareness and source reconciliation.",
            "authority_notice": "Not an official dashboard, case-management system, forecast, travel advisory, or deployment recommendation.",
        },
        "reported_counts": source.get("reported_counts", {}),
        "affected_zones": source.get("affected_zones", []),
        "zone_attributed_counts": zone_attributed_counts,
        "source_review_geographies": source_review_geographies,
        "source_ids": source.get("sources", []),
        "source_conflict_note_count": len(source.get("source_conflict_notes", [])),
        "reporting_context": {
            "public_reporting_visibility": "limited",
            "zone_attribution_lag": attribution.get("narrative"),
            "machine_readable_model_outputs": "Excluded from this public export contract.",
        },
        "limitations": [
            "Public sources can disagree on confirmed, suspected, and death counts.",
            "National totals may be timelier than health-zone attribution.",
            "Source-review rows are descriptive public-source records, not new official classifications.",
            "Quantitative model, calibration, and corridor-probability internals are not part of this public data contract.",
        ],
    }


def _public_source_conflicts(source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "data_as_of": source.get("data_as_of"),
        "notes": [
            {"note_id": f"conflict-{index:02d}", "text": _sanitize_conflict_note(note)}
            for index, note in enumerate(source.get("source_conflict_notes", []), start=1)
        ],
        "interpretation": "These notes document public-source disagreements and dating issues; they are not an official correction to authority reporting.",
    }


def _sanitize_conflict_note(note: str) -> str:
    if note.startswith("Spatial model source zones use"):
        return (
            "Health-zone source counts use the INRB-UMIE/INSP per-health-zone series "
            "(consortium build-2026-05-28-bb8b7d5, data as of 26 May 2026), which attributes "
            "109 confirmed cases across 18 monitored health zones. National DRC and country-scope "
            "headline confirmed totals are higher; the difference is reported as unallocated and "
            "cross-border attribution-lag context rather than assigned to every health zone."
        )
    if note.startswith("CDC 24 May reports five Uganda cases"):
        return (
            "CDC 24 May reports five Uganda cases, but does not publish a zone-attributed count table. "
            "The DRC MoH dashboard exposes all-published-bulletins aggregate cards and sparse SitRep 009 rows; "
            "the aggregate is carried as official count evidence, while the latest sparse rows remain source-review "
            "pending cumulative PDF/table-label verification. One American national was evacuated from DRC to Germany "
            "and confirmed positive; a high-risk contact was reportedly transferred to Czechia. The reported Kinshasa "
            "case was deconfirmed by INRB and is not counted as confirmed."
        )
    return note


def _reported_count_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _source_entries(manifest):
        observations = entry.get("reported_count_observations", [])
        if isinstance(observations, list):
            for observation in observations:
                if not isinstance(observation, dict):
                    continue
                rows.append(
                    {
                        "source_id": entry.get("source_id"),
                        "publisher": entry.get("publisher"),
                        "published_at": entry.get("published_at"),
                        "retrieved_at": entry.get("retrieved_at"),
                        "source_tier": entry.get("source_tier"),
                        "country_scope": entry.get("country_scope", []),
                        "metric": observation.get("metric"),
                        "source_field": observation.get("source_field"),
                        "value": observation.get("value"),
                    }
                )
            continue
        normalized = entry.get("normalized_content", {})
        if not isinstance(normalized, dict):
            continue
        for path, key, value in _walk_count_fields(normalized):
            rows.append(
                {
                    "source_id": entry.get("source_id"),
                    "publisher": entry.get("publisher"),
                    "published_at": entry.get("published_at"),
                    "retrieved_at": entry.get("retrieved_at"),
                    "source_tier": entry.get("source_tier"),
                    "country_scope": entry.get("country_scope", []),
                    "metric": SOURCE_COUNT_FIELD_LABELS[key],
                    "source_field": ".".join(path),
                    "value": value,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            row.get("published_at") or "",
            row.get("source_id") or "",
            row.get("metric") or "",
        ),
    )


def _walk_count_fields(value: Any, prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            path = (*prefix, key)
            if key in SOURCE_COUNT_FIELD_LABELS and not isinstance(item, (dict, list)):
                yield path, key, item
            if isinstance(item, dict):
                yield from _walk_count_fields(item, path)


def _zone_count_rows(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    block = source.get("insp_per_zone_block", {})
    rows: list[dict[str, Any]] = []
    for zone_id, row in sorted(block.get("by_lovs_zone", {}).items()):
        rows.append(
            {
                "zone_id": zone_id,
                "source_id": block.get("source_id"),
                "source_data_date": block.get("as_of_data_date"),
                "confirmed": row.get("confirmed"),
                "suspected": row.get("suspected"),
                "confirmed_deaths": row.get("confirmed_deaths"),
                "suspected_deaths": row.get("suspected_deaths"),
                "source_row_status": row.get("present_in_insp_classification"),
            }
        )
    return rows


def _source_index_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for entry in _source_entries(manifest):
        rows.append(
            {
                "source_id": entry.get("source_id"),
                "publisher": entry.get("publisher"),
                "source_tier": entry.get("source_tier"),
                "published_at": entry.get("published_at"),
                "retrieved_at": entry.get("retrieved_at"),
                "country_scope": entry.get("country_scope", []),
                "license": entry.get("license"),
                "raw_archive_status": entry.get("raw_archive_status"),
                "content_hash": entry.get("content_hash"),
                "url": entry.get("url"),
            }
        )
    return sorted(rows, key=lambda row: (row.get("published_at") or "", row.get("source_id") or ""))


def public_snapshot_findings(public_snapshot: Mapping[str, Any]) -> list[str]:
    findings = []
    for key in _walk_keys(public_snapshot):
        if key in SENSITIVE_PUBLIC_SNAPSHOT_KEYS:
            findings.append(f"{key}: sensitive public snapshot field")
    return sorted(set(findings))


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_bytes(relpath: pathlib.Path, generated: Mapping[pathlib.Path, str]) -> bytes:
    if relpath in generated:
        return generated[relpath].encode("utf-8")
    return (REPO_ROOT / relpath).read_bytes()


def _release_manifest(source: Mapping[str, Any], generated: Mapping[pathlib.Path, str]) -> dict[str, Any]:
    artifact_paths = sorted(set((*STATIC_PUBLICATION_ARTIFACTS, *PUBLIC_DOC_PATHS, *generated.keys())))
    artifact_rows = []
    for relpath in artifact_paths:
        path = REPO_ROOT / relpath
        if relpath not in generated and not path.exists():
            continue
        data = _file_bytes(relpath, generated)
        artifact_rows.append(
            {
                "path": relpath.as_posix(),
                "sha256": _sha256_bytes(data),
                "size_bytes": len(data),
            }
        )
    return {
        "schema_version": "1.0",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "data_as_of": source.get("data_as_of"),
        "source_inputs": [
            {
                "path": PUBLIC_EXPORT_SOURCE_PATH.as_posix(),
                "sha256": _sha256_bytes((REPO_ROOT / PUBLIC_EXPORT_SOURCE_PATH).read_bytes()),
            },
            {
                "path": SOURCE_MANIFEST_PATH.as_posix(),
                "sha256": _sha256_bytes((REPO_ROOT / SOURCE_MANIFEST_PATH).read_bytes()),
            },
        ],
        "artifacts": artifact_rows,
    }


def build_public_artifacts() -> dict[pathlib.Path, str]:
    source = _read_json(PUBLIC_EXPORT_SOURCE_PATH)
    manifest = _read_json(SOURCE_MANIFEST_PATH)
    public_snapshot = _public_snapshot(source)
    findings = public_snapshot_findings(public_snapshot)
    if findings:
        joined = "; ".join(findings)
        raise ValueError(f"public snapshot includes sensitive fields: {joined}")

    artifacts: dict[pathlib.Path, str] = {
        PUBLIC_SNAPSHOT_PATH: _json_text(public_snapshot),
        PUBLIC_SOURCE_CONFLICTS_PATH: _json_text(_public_source_conflicts(source)),
        PUBLIC_REPORTED_COUNTS_PATH: _csv_text(
            [
                "source_id",
                "publisher",
                "published_at",
                "retrieved_at",
                "source_tier",
                "country_scope",
                "metric",
                "source_field",
                "value",
            ],
            _reported_count_rows(manifest),
        ),
        PUBLIC_ZONE_COUNTS_PATH: _csv_text(
            [
                "zone_id",
                "source_id",
                "source_data_date",
                "confirmed",
                "suspected",
                "confirmed_deaths",
                "suspected_deaths",
                "source_row_status",
            ],
            _zone_count_rows(source),
        ),
        PUBLIC_SOURCE_INDEX_PATH: _csv_text(
            [
                "source_id",
                "publisher",
                "source_tier",
                "published_at",
                "retrieved_at",
                "country_scope",
                "license",
                "raw_archive_status",
                "content_hash",
                "url",
            ],
            _source_index_rows(manifest),
        ),
        pathlib.Path("METHODOLOGY_PUBLIC.md"): METHODOLOGY_PUBLIC_MD,
        pathlib.Path("DATA_DICTIONARY.md"): DATA_DICTIONARY_MD,
        pathlib.Path("LIMITATIONS.md"): LIMITATIONS_MD,
        pathlib.Path("CHANGELOG.md"): CHANGELOG_MD,
    }
    artifacts[RELEASE_MANIFEST_PATH] = _json_text(_release_manifest(source, artifacts))
    return artifacts


def write_public_artifacts() -> None:
    for relpath, text in build_public_artifacts().items():
        path = REPO_ROOT / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")


def check_public_artifacts() -> list[str]:
    mismatches = []
    for relpath, expected in build_public_artifacts().items():
        path = REPO_ROOT / relpath
        if not path.exists():
            mismatches.append(f"{relpath.as_posix()}: missing")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            mismatches.append(f"{relpath.as_posix()}: stale")
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if public artifacts are stale.")
    args = parser.parse_args(argv)

    if args.check:
        mismatches = check_public_artifacts()
        if mismatches:
            sys.stderr.write("[FAIL] public export artifacts are stale:\n")
            for mismatch in mismatches:
                sys.stderr.write(f"    {mismatch}\n")
            return 1
        print("public export artifacts current")
        return 0

    write_public_artifacts()
    print("public export artifacts written")
    return 0


METHODOLOGY_PUBLIC_MD = """# Public Methodology

This repository publishes a dated public-source evidence snapshot for the 2026 Bundibugyo virus disease event in DRC and Uganda. It is designed to help MOH, CDC, WHO, Africa CDC, ECDC, INRB, and peer analysts inspect the public evidence trail without depending on Arcede's internal LOVS method engine.

## Public-Source Scope

The public artifacts use only source-attributed public facts and public-source review metadata. Operational partners may hold line lists, laboratory timestamps, genomic data, contact-tracing records, field investigation notes, or internal dashboards that are more complete than this package.

## Snapshot Dating

`as_of` is the publication snapshot timestamp. `data_as_of` is the latest data date represented by the headline snapshot. Source rows may have earlier `published_at`, `retrieved_at`, `report_date`, or `publication_date` values because public outbreak reporting is asynchronous.

## Count Handling

The public snapshot preserves the headline reported-count range, primary source ID, and conflict-anchor source IDs for confirmed cases, suspected cases, and deaths. It does not assert that every public source agrees. Source disagreement is documented in `data/public_source_conflicts.json`.

## Health-Zone Tables

`data/public_zone_counts_2026-05-26.csv` exposes source-attributed health-zone counts for public-health review. The table is a public evidence artifact, not a replacement for official health-zone reporting or case management.

## What Is Not In The Public Methodology

The public repo does not publish the LOVS implementation, calibration workbench, scoring infrastructure, source-ingest automation, private-data adaptation workflow, or quantitative model internals. Machine-readable public exports intentionally exclude calibration blocks, hypotheses, audit dependencies, under-ascertainment bands, and corridor probabilities.
"""


DATA_DICTIONARY_MD = """# Data Dictionary

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
"""


LIMITATIONS_MD = """# Limitations

This repository is a public evidence package, not an official outbreak dashboard.

- It does not replace MOH, WHO, CDC, Africa CDC, ECDC, INRB, or field-response reporting.
- It does not contain line lists, contact-tracing records, laboratory timestamps, genomic data, or private operational dashboards.
- Public sources may disagree because they publish on different dates, use different inclusion rules, or mix confirmed, suspected, and death-status classes differently.
- Health-zone attribution can lag national totals.
- Public-source visibility is limited during fast-moving viral hemorrhagic fever events.
- Quantitative model outputs, calibration design, scoring rules, private-data adaptation, and source-ingest mechanics are outside the public export contract.

Use the public artifacts for source review, situational awareness, citation, and cross-checking. Do not use them as deployment orders, travel advice, clinical guidance, or case-management records.
"""


CHANGELOG_MD = """# Changelog

## 2026-05-30

- Added sanitized public-health exports for partner review:
  - `data/public_snapshot.json`
  - `data/public_reported_counts.csv`
  - `data/public_zone_counts_2026-05-26.csv`
  - `data/public_source_conflicts.json`
  - `data/public_source_index.csv`
  - `data/release_manifest.json`
- Added public methodology, data dictionary, and limitations documents.
- Added CI checks that the public export artifacts are current and do not include sensitive model-internal fields.
"""


if __name__ == "__main__":
    raise SystemExit(main())
