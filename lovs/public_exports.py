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
from datetime import date, datetime
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC_EXPORT_SOURCE_PATH = pathlib.Path("data/public_export_source.json")
SOURCE_MANIFEST_PATH = pathlib.Path("data/public_source_manifest.json")
CALIBRATION_COMMITMENTS_PATH = pathlib.Path("data/public_calibration_commitments.json")

PUBLIC_CALIBRATION_STATUS_PATH = pathlib.Path("data/public_calibration_status.json")
PUBLIC_CALIBRATION_LEDGER_PATH = pathlib.Path("data/public_calibration_ledger.csv")
PUBLIC_PRECOMMITMENT_TARGETS_PATH = pathlib.Path("data/public_precommitment_targets.csv")
PUBLIC_BLINDSPOTS_PATH = pathlib.Path("data/public_blindspots.json")
PUBLIC_LATENCY_OBSERVATORY_PATH = pathlib.Path("data/public_latency_observatory.csv")
PUBLIC_NOWCAST_STATUS_PATH = pathlib.Path("data/public_nowcast_status.json")
PUBLIC_SNAPSHOT_PATH = pathlib.Path("data/public_snapshot.json")
PUBLIC_REPORTED_COUNTS_PATH = pathlib.Path("data/public_reported_counts.csv")
PUBLIC_ZONE_COUNTS_PATH = pathlib.Path("data/public_zone_counts_2026-05-26.csv")
PUBLIC_SOURCE_CONFLICTS_PATH = pathlib.Path("data/public_source_conflicts.json")
PUBLIC_SOURCE_INDEX_PATH = pathlib.Path("data/public_source_index.csv")
RELEASE_MANIFEST_PATH = pathlib.Path("data/release_manifest.json")

PUBLIC_DOC_PATHS = (
    pathlib.Path("METHODOLOGY_PUBLIC.md"),
    pathlib.Path("READONLY_INTERFACE_PUBLIC.md"),
    pathlib.Path("CALIBRATION_LEDGER_PUBLIC.md"),
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
    pathlib.Path("PUBLIC_HEALTH_USE_CASES.md"),
    pathlib.Path("PUBLIC_ADAPTATION_GUIDE.md"),
    pathlib.Path("METHOD_CARDS_PUBLIC.md"),
    pathlib.Path("WORKED_SNAPSHOT_REVIEW.md"),
    pathlib.Path("CALIBRATION_RESOLUTION_PUBLIC.md"),
    pathlib.Path("CITATIONS.md"),
    pathlib.Path("CITATION.cff"),
    pathlib.Path("GLOSSARY.md"),
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
    pathlib.Path("data/public_calibration_commitments.json"),
    pathlib.Path("data/natural_earth_outlines.json"),
    pathlib.Path("data/zones.json"),
    pathlib.Path("examples/README.md"),
    pathlib.Path("examples/local_aggregate_input.example.json"),
    pathlib.Path("examples/source_manifest_minimal.example.json"),
    pathlib.Path("examples/public_calibration_commitments.example.csv"),
    pathlib.Path("examples/review_public_methodology.py"),
    pathlib.Path("examples/review_local_aggregate.py"),
    pathlib.Path("examples/show_calibration_record.py"),
    pathlib.Path("examples/summarize_public_package.py"),
    pathlib.Path("schemas/README.md"),
    pathlib.Path("schemas/public_snapshot.schema.json"),
    pathlib.Path("schemas/public_source_manifest.schema.json"),
    pathlib.Path("schemas/public_calibration_status.schema.json"),
    pathlib.Path("schemas/public_blindspots.schema.json"),
    pathlib.Path("schemas/public_nowcast_status.schema.json"),
    pathlib.Path("schemas/local_aggregate_input.schema.json"),
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
                "data_as_of": entry.get("data_as_of"),
                "data_as_of_basis": entry.get("data_as_of_basis"),
                "country_scope": entry.get("country_scope", []),
                "license": entry.get("license"),
                "raw_archive_status": entry.get("raw_archive_status"),
                "content_hash": entry.get("content_hash"),
                "url": entry.get("url"),
            }
        )
    return sorted(rows, key=lambda row: (row.get("published_at") or "", row.get("source_id") or ""))


PUBLIC_CALIBRATION_LEDGER_FIELDS = [
    "ledger_id",
    "registered_at",
    "outbreak_id",
    "public_question",
    "source_geography",
    "target_geography",
    "horizon_days",
    "resolution_date",
    "resolution_source_policy",
    "geography_class",
    "forecast_type",
    "public_value_or_tier",
    "control_role",
    "status",
    "resolved_value",
    "score_after_resolution",
    "notes",
    "commitment_hash",
]


def _commitment_hash(row: Mapping[str, Any]) -> str:
    payload = {key: _cell(row.get(key, "")) for key in PUBLIC_CALIBRATION_LEDGER_FIELDS if key != "commitment_hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _sha256_bytes(encoded)


def _public_calibration_rows(commitments: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in commitments.get("commitments", []):
        if not isinstance(item, dict):
            continue
        row = {key: item.get(key, "") for key in PUBLIC_CALIBRATION_LEDGER_FIELDS if key != "commitment_hash"}
        row["commitment_hash"] = _commitment_hash(row)
        rows.append(row)
    return sorted(rows, key=lambda row: (row["registered_at"], row["ledger_id"]))


PUBLIC_PRECOMMITMENT_TARGET_FIELDS = [
    "target_id",
    "registered_at",
    "source_geography",
    "target_geography",
    "geography_class",
    "target_set_role",
    "public_value_or_tier",
    "inclusion_rationale",
    "horizon_days",
    "resolution_date",
    "status",
    "resolution_source_policy",
]


def _target_set_role(control_role: str) -> str:
    if "negative" in control_role:
        return "likely_negative_control"
    if "positive" in control_role:
        return "likely_positive_control"
    if "blindspot" in control_role:
        return "blindspot_watch"
    if "watchlist" in control_role or "watch" in control_role:
        return "watch_target"
    return "registered_target"


def _public_precommitment_target_rows(commitments: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _public_calibration_rows(commitments):
        rows.append(
            {
                "target_id": row["ledger_id"],
                "registered_at": row["registered_at"],
                "source_geography": row["source_geography"],
                "target_geography": row["target_geography"],
                "geography_class": row["geography_class"],
                "target_set_role": _target_set_role(row["control_role"]),
                "public_value_or_tier": row["public_value_or_tier"],
                "inclusion_rationale": row["notes"],
                "horizon_days": row["horizon_days"],
                "resolution_date": row["resolution_date"],
                "status": row["status"],
                "resolution_source_policy": row["resolution_source_policy"],
            }
        )
    return rows


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _days_between(start: Any, end: Any) -> int | str:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date is None or end_date is None:
        return ""
    return (end_date - start_date).days


def _snapshot_date(source: Mapping[str, Any]) -> str:
    as_of = source.get("as_of")
    if isinstance(as_of, str) and as_of:
        return as_of[:10]
    today = datetime.utcnow().date()
    return today.isoformat()


def _public_calibration_status(source: Mapping[str, Any], commitments: Mapping[str, Any]) -> dict[str, Any]:
    rows = _public_calibration_rows(commitments)
    snapshot_date = _snapshot_date(source)
    blocks: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["registered_at"], row["resolution_date"])
        block = blocks.setdefault(
            key,
            {
                "public_group_id": f"bdbv-2026-public-calibration-{row['registered_at']}",
                "registered_at": row["registered_at"],
                "resolution_date": row["resolution_date"],
                "horizon_days": row["horizon_days"],
                "commitment_count": 0,
                "open_count": 0,
                "resolved_count": 0,
                "remaining_days_from_snapshot_as_of": _days_between(snapshot_date, row["resolution_date"]),
                "status": "awaiting_resolution",
                "control_roles": {},
                "public_value_tiers": {},
            },
        )
        block["commitment_count"] += 1
        if row["status"] == "open":
            block["open_count"] += 1
        elif row["status"] == "resolved":
            block["resolved_count"] += 1
        block["control_roles"][row["control_role"]] = block["control_roles"].get(row["control_role"], 0) + 1
        block["public_value_tiers"][row["public_value_or_tier"]] = (
            block["public_value_tiers"].get(row["public_value_or_tier"], 0) + 1
        )

    next_resolution_dates = sorted({row["resolution_date"] for row in rows if row["status"] == "open"})
    return {
        "schema_version": "1.0",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "snapshot_date": snapshot_date,
        "status": "open_commitments_awaiting_public_resolution",
        "ledger_rows": len(rows),
        "open_commitments": sum(1 for row in rows if row["status"] == "open"),
        "resolved_commitments": sum(1 for row in rows if row["status"] == "resolved"),
        "next_resolution_date": next_resolution_dates[0] if next_resolution_dates else None,
        "blocks": sorted(blocks.values(), key=lambda item: item["registered_at"]),
        "resolver_caveats": [
            "Rows resolve only from public MOH, WHO, Africa CDC, CDC, ECDC, INRB, or cited public authority reporting.",
            "Ambiguous or unavailable public evidence remains open until documented review.",
            "This public status surface is read-only; it does not mutate ledger rows or resolution outcomes.",
        ],
    }


PUBLIC_LATENCY_OBSERVATORY_FIELDS = [
    "source_id",
    "publisher",
    "source_tier",
    "data_as_of",
    "data_as_of_basis",
    "published_at",
    "retrieved_at",
    "publication_lag_days",
    "archival_lag_days",
    "total_visibility_lag_days",
    "raw_archive_status",
    "latency_status",
]


def _public_latency_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in _source_entries(manifest):
        data_as_of = entry.get("data_as_of") or ""
        published_at = entry.get("published_at") or ""
        retrieved_at = entry.get("retrieved_at") or ""
        has_lag = bool(data_as_of and published_at and retrieved_at)
        rows.append(
            {
                "source_id": entry.get("source_id"),
                "publisher": entry.get("publisher"),
                "source_tier": entry.get("source_tier"),
                "data_as_of": data_as_of,
                "data_as_of_basis": entry.get("data_as_of_basis") or "",
                "published_at": published_at,
                "retrieved_at": retrieved_at,
                "publication_lag_days": _days_between(data_as_of, published_at) if has_lag else "",
                "archival_lag_days": _days_between(published_at, retrieved_at) if has_lag else "",
                "total_visibility_lag_days": _days_between(data_as_of, retrieved_at) if has_lag else "",
                "raw_archive_status": entry.get("raw_archive_status"),
                "latency_status": "measured" if has_lag else "missing_data_as_of",
            }
        )
    return sorted(rows, key=lambda row: (row["published_at"], row["source_id"]))


def _public_blindspots(
    source: Mapping[str, Any],
    manifest: Mapping[str, Any],
    commitments: Mapping[str, Any],
    latency_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    restricted_sources = [
        entry for entry in _source_entries(manifest) if entry.get("raw_archive_status") != "public_bytes"
    ]
    missing_latency = [row for row in latency_rows if row["latency_status"] != "measured"]
    source_review_count = len(source.get("source_review_geographies", []))
    open_commitments = [row for row in _public_calibration_rows(commitments) if row["status"] == "open"]
    return {
        "schema_version": "1.0",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "blindspots": [
            {
                "blindspot_id": "restricted-publisher-bytes",
                "status": "tracked",
                "affected_count": len(restricted_sources),
                "public_effect": "Some source rows expose URL, timestamp, archive status, and hash but not raw publisher bytes.",
                "mitigation": "Use public source index metadata and publisher URLs; do not redistribute restricted bytes.",
            },
            {
                "blindspot_id": "missing-data-as-of-for-latency",
                "status": "tracked",
                "affected_count": len(missing_latency),
                "public_effect": "Latency cannot be measured when a source lacks a public data-as-of date.",
                "mitigation": "Rows remain in the source index; latency status is marked missing_data_as_of.",
            },
            {
                "blindspot_id": "health-zone-attribution-lag",
                "status": "tracked",
                "affected_count": source_review_count,
                "public_effect": "National totals may be timelier than health-zone attribution.",
                "mitigation": source.get("attribution_lag_disclosure", {}).get("narrative") or "Disclose lag context.",
            },
            {
                "blindspot_id": "open-calibration-resolution",
                "status": "awaiting_resolution",
                "affected_count": len(open_commitments),
                "public_effect": "Open commitments are not scored until their public resolution dates.",
                "mitigation": "Keep rows open until public authority evidence is available and reviewed.",
            },
        ],
    }


def _public_nowcast_status(
    source: Mapping[str, Any],
    latency_rows: list[dict[str, Any]],
    commitments: Mapping[str, Any],
) -> dict[str, Any]:
    measured_latency = sum(1 for row in latency_rows if row["latency_status"] == "measured")
    open_commitments = sum(1 for row in _public_calibration_rows(commitments) if row["status"] == "open")
    return {
        "schema_version": "1.0",
        "outbreak_id": source.get("outbreak_id"),
        "as_of": source.get("as_of"),
        "status": "interface_defined_not_issued_for_this_snapshot",
        "public_role": "Document the standing scored-nowcast shape without publishing model internals.",
        "readiness": {
            "measured_latency_rows": measured_latency,
            "open_calibration_commitments": open_commitments,
            "headline_data_as_of": source.get("data_as_of"),
        },
        "candidate_quantities": [
            "combined_confirmed_plus_suspected_cases",
            "confirmed_cases",
        ],
        "future_public_fields": [
            "nowcast_id",
            "quantity",
            "issued_at",
            "resolution_date",
            "status",
            "resolved_value",
            "score_after_resolution",
            "commitment_hash",
        ],
        "excluded_fields": [
            "point_estimate",
            "predictive_intervals",
            "model_parameters",
            "calculation_components",
            "private_source_inputs",
        ],
    }


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
            {
                "path": CALIBRATION_COMMITMENTS_PATH.as_posix(),
                "sha256": _sha256_bytes((REPO_ROOT / CALIBRATION_COMMITMENTS_PATH).read_bytes()),
            },
        ],
        "artifacts": artifact_rows,
    }


def build_public_artifacts() -> dict[pathlib.Path, str]:
    source = _read_json(PUBLIC_EXPORT_SOURCE_PATH)
    manifest = _read_json(SOURCE_MANIFEST_PATH)
    commitments = _read_json(CALIBRATION_COMMITMENTS_PATH)
    public_snapshot = _public_snapshot(source)
    findings = public_snapshot_findings(public_snapshot)
    if findings:
        joined = "; ".join(findings)
        raise ValueError(f"public snapshot includes sensitive fields: {joined}")
    latency_rows = _public_latency_rows(manifest)

    artifacts: dict[pathlib.Path, str] = {
        PUBLIC_CALIBRATION_STATUS_PATH: _json_text(_public_calibration_status(source, commitments)),
        PUBLIC_CALIBRATION_LEDGER_PATH: _csv_text(
            PUBLIC_CALIBRATION_LEDGER_FIELDS,
            _public_calibration_rows(commitments),
        ),
        PUBLIC_PRECOMMITMENT_TARGETS_PATH: _csv_text(
            PUBLIC_PRECOMMITMENT_TARGET_FIELDS,
            _public_precommitment_target_rows(commitments),
        ),
        PUBLIC_BLINDSPOTS_PATH: _json_text(_public_blindspots(source, manifest, commitments, latency_rows)),
        PUBLIC_LATENCY_OBSERVATORY_PATH: _csv_text(PUBLIC_LATENCY_OBSERVATORY_FIELDS, latency_rows),
        PUBLIC_NOWCAST_STATUS_PATH: _json_text(_public_nowcast_status(source, latency_rows, commitments)),
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
                "data_as_of",
                "data_as_of_basis",
                "country_scope",
                "license",
                "raw_archive_status",
                "content_hash",
                "url",
            ],
            _source_index_rows(manifest),
        ),
        pathlib.Path("METHODOLOGY_PUBLIC.md"): METHODOLOGY_PUBLIC_MD,
        pathlib.Path("READONLY_INTERFACE_PUBLIC.md"): READONLY_INTERFACE_PUBLIC_MD,
        pathlib.Path("CALIBRATION_LEDGER_PUBLIC.md"): CALIBRATION_LEDGER_PUBLIC_MD,
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

This repository publishes a dated public-source evidence snapshot for the 2026 Bundibugyo virus disease event in DRC and Uganda. It is designed to help MOH, INSP, INRB, CDC, WHO, Africa CDC, ECDC, and peer analysts inspect the public evidence trail without depending on unpublished implementation details.

The public methodology is deliberately useful but bounded. It exposes the reasoning discipline, artifact shapes, and review steps that make the public package auditable. It does not expose the private LOVS implementation, source collection automation, private-data adapters, quantitative model internals, mutable resolver tools, or private scoring code.

## Public-Source Scope

The public artifacts use only source-attributed public facts and public-source review metadata. Operational partners may hold line lists, laboratory timestamps, genomic data, contact-tracing records, field investigation notes, or non-public dashboards that are more complete than this package.

Public claims should remain traceable to:

- a source ID;
- publisher;
- source tier;
- publication date;
- retrieval date;
- source-use status;
- and, where available, a source data date.

## Snapshot Dating

`as_of` is the publication snapshot timestamp. `data_as_of` is the latest data date represented by the headline snapshot. Source rows may have earlier `published_at`, `retrieved_at`, `report_date`, or `publication_date` values because public outbreak reporting is asynchronous.

The method keeps these clocks separate:

- `data_as_of`: what date the source says the data represents.
- `published_at`: when the source made the report available.
- `retrieved_at`: when this repository captured or reviewed the source.

This prevents false disagreements when two sources are actually describing different data dates.

## Count Handling

The public snapshot preserves the headline reported-count range, primary source ID, and conflict-anchor source IDs for confirmed cases, suspected cases, and deaths. It does not assert that every public source agrees. Source disagreement is documented in `data/public_source_conflicts.json`.

Counts are interpreted as public claims tied to sources, not as private surveillance records. When public sources disagree, this package preserves the disagreement instead of forcing a single blended value.

## Health-Zone Tables

`data/public_zone_counts_2026-05-26.csv` exposes source-attributed health-zone counts for public-health review. The table is a public evidence artifact, not a replacement for official health-zone reporting or case management.

Health-zone rows can lag national or country-scope headline totals. The method records the gap as attribution lag unless a later public source assigns the cases. It does not scale all zones upward to make a public map match a newer headline count.

## Public Method Cards

`METHOD_CARDS_PUBLIC.md` breaks the public method into reusable cards:

- source reconciliation;
- source clocks;
- health-zone attribution lag;
- blindspot register;
- calibration accountability;
- nowcast boundary.

These cards are the safest place to reuse the method in another public or partner-local aggregate workflow.

## Worked Snapshot Review

`WORKED_SNAPSHOT_REVIEW.md` applies the public method to the current real snapshot. It shows how to:

- identify the snapshot clock;
- read headline counts as public claims;
- compare health-zone attribution with headline totals;
- review source-clock coverage;
- interpret blindspots;
- inspect calibration-accountability status.

The same review can be run locally with:

```bash
python3 examples/review_public_methodology.py
```

## Calibration Accountability

The public calibration files expose pre-registered public questions, target roles, status summaries, resolution dates, public resolution policy, and commitment hashes. They do not publish private scoring implementation, target-generation logic, or quantitative internals.

The public rule is simple: keep rows open until citable public authority evidence supports resolution under `CALIBRATION_RESOLUTION_PUBLIC.md`.

## Blindspots And Latency

`data/public_blindspots.json` tracks evidence states that public sources cannot fully answer. `data/public_latency_observatory.csv` measures reporting latency only where source clocks allow it. Rows with missing source dates remain visible because missingness is part of the public evidence state.

## What Is Not In The Public Methodology

The public repo does not publish the LOVS implementation, calibration workbench, scoring infrastructure, source collection automation, private-data adaptation workflow, or quantitative model internals. Machine-readable public exports intentionally exclude private calibration blocks, private hypotheses, audit dependencies, under-ascertainment bands, and corridor probabilities.
"""


READONLY_INTERFACE_PUBLIC_MD = """# Read-Only Public Interface

This document defines the current public, read-only LOVS interface. It exposes stable files, not write tools. It is an artifact map so public-health partners and technical users can answer bounded questions without bypassing the immutable public record.

## Interface Map

| Question | Artifact |
|---|---|
| What is the current public snapshot? | `data/public_snapshot.json` |
| Which public sources support the snapshot? | `data/public_source_manifest.json`, `data/public_source_index.csv` |
| What counts did public sources report? | `data/public_reported_counts.csv` |
| What health-zone counts are available? | `data/public_zone_counts_2026-05-26.csv` |
| What public source conflicts are documented? | `data/public_source_conflicts.json` |
| What calibration commitments are open? | `data/public_calibration_ledger.csv` |
| What is the block-level calibration status? | `data/public_calibration_status.json` |
| What target set was precommitted? | `data/public_precommitment_targets.csv` |
| What evidence gaps or unscoreable states remain? | `data/public_blindspots.json` |
| What reporting latency can be measured from public source dates? | `data/public_latency_observatory.csv` |
| Is a standing scored nowcast issued in this snapshot? | `data/public_nowcast_status.json` |
| What public method cards can partners reuse? | `METHOD_CARDS_PUBLIC.md` |
| How does the method apply to the current real snapshot? | `WORKED_SNAPSHOT_REVIEW.md`, `examples/review_public_methodology.py` |
| How might MOH, CDC, WHO, INRB, or peer analysts use the public package? | `PUBLIC_HEALTH_USE_CASES.md` |
| How can a partner adapt the public package to aggregate local data? | `PUBLIC_ADAPTATION_GUIDE.md`, `examples/` |
| What machine-readable shapes should public JSON artifacts follow? | `schemas/` |
| How can a reader summarize the public package locally? | `examples/summarize_public_package.py` |
| How should open calibration rows be reviewed after resolution dates? | `CALIBRATION_RESOLUTION_PUBLIC.md` |
| How can I inspect and hash-verify the pre-registered calibration record? | `examples/show_calibration_record.py`, `data/public_calibration_ledger.csv` |
| What do the core public terms mean? | `GLOSSARY.md` |
| How can a partner review their own aggregate file? | `examples/review_local_aggregate.py`, `schemas/local_aggregate_input.schema.json` |
| How should this package be cited? | `CITATIONS.md`, `CITATION.cff` |
| Which artifact hashes belong to the same release? | `data/release_manifest.json` |

## Integrity Boundary

The public interface is read-only. It does not mutate snapshots, source manifests, publication state, calibration ledgers, resolution outcomes, or precommitment target sets.

## Controlled Surfaces

The public interface does not publish source collection automation, mutable resolver tools, private-data adapters, probability intervals, model parameters, scoring implementation, or private calibration code. Those surfaces remain unpublished method assets and can be shared through partner-specific agreements when appropriate.
"""


CALIBRATION_LEDGER_PUBLIC_MD = """# Public Calibration Ledger

The public calibration ledger is an accountability artifact. It records pre-registered public questions, registration dates, horizons, resolution dates, public resolution policy, status, and commitment hashes for selected 2026 BDBV corridor-watch commitments.

## What The Ledger Supports

- MOH, CDC, WHO, Africa CDC, ECDC, INRB, and peer analysts can see what was registered before outcomes resolved.
- Public readers can inspect the resolution policy and later compare open commitments with resolved public evidence.
- Each row has a `commitment_hash` so the public row payload can be checked for stability across releases.

## What The Ledger Does Not Publish

The ledger does not publish probability intervals, feature weights, prior or posterior parameters, calibration code, scoring implementation, source collection machinery, private-data adapters, or corridor-generation internals. Those remain unpublished method assets and can be shared through partner-specific agreements when useful.

## Resolution

Open commitments should be resolved from public MOH, WHO, Africa CDC, CDC, ECDC, INRB, or other cited public authority reporting available by the row's `resolution_date`. Ambiguous or unavailable public evidence should remain open until a documented review is added.
"""


DATA_DICTIONARY_MD = """# Data Dictionary

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

Public source metadata: source ID, publisher, tier, data-as-of date where available, publication date, retrieval date, license, archive status, content hash, and URL.

## `data/public_source_conflicts.json`

Human-readable conflict notes documenting how public counts differ by source and date.

## `data/release_manifest.json`

Release-level artifact inventory with SHA-256 checksums and byte sizes.

## `schemas/`

Public JSON Schemas for reusable JSON artifacts and aggregate examples. CSV artifacts are documented in this data dictionary.
"""


LIMITATIONS_MD = """# Limitations

This repository is a public evidence package, not an official outbreak dashboard.

- It does not replace MOH, WHO, CDC, Africa CDC, ECDC, INRB, or field-response reporting.
- It does not contain line lists, contact-tracing records, laboratory timestamps, genomic data, or private operational dashboards.
- Public sources may disagree because they publish on different dates, use different inclusion rules, or mix confirmed, suspected, and death-status classes differently.
- Health-zone attribution can lag national totals.
- Public-source visibility is limited during fast-moving viral hemorrhagic fever events.
- Quantitative model outputs, private calibration workbench details, scoring rules, private-data adaptation, and source collection mechanics are outside the public export contract. Public calibration-accountability doctrine is documented separately.

Use the public artifacts for source review, situational awareness, citation, and cross-checking. Do not use them as deployment orders, travel advice, clinical guidance, or case-management records.
"""


CHANGELOG_MD = """# Changelog

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
"""


if __name__ == "__main__":
    raise SystemExit(main())
