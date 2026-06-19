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

from lovs import lovs_evidence
from lovs import sitrep_overlays
from lovs import sitrep_promotions


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
PUBLIC_ZONE_COUNTS_PATH = pathlib.Path("data/public_zone_counts_2026-05-29.csv")
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
    pathlib.Path("deliverables/public-health-dataset/analysis_dependency_audit.csv"),
    pathlib.Path("deliverables/public-health-dataset/attribution_lag_disclosure.csv"),
    pathlib.Path("deliverables/public-health-dataset/calibration_ledger.csv"),
    pathlib.Path("deliverables/public-health-dataset/corrections_gaps.csv"),
    pathlib.Path("deliverables/public-health-dataset/corridors.csv"),
    pathlib.Path("deliverables/public-health-dataset/data_dictionary.csv"),
    pathlib.Path("deliverables/public-health-dataset/lovs-public-health-dataset.manifest.json"),
    pathlib.Path("deliverables/public-health-dataset/lovs-public-health-dataset.schema.json"),
    pathlib.Path("deliverables/public-health-dataset/lovs-public-health-dataset.xlsx"),
    pathlib.Path("deliverables/public-health-dataset/model_outputs.csv"),
    pathlib.Path("deliverables/public-health-dataset/per-zone_snapshot.csv"),
    pathlib.Path("deliverables/public-health-dataset/public_claim_audit.csv"),
    pathlib.Path("deliverables/public-health-dataset/reconciliation_residuals.csv"),
    pathlib.Path("deliverables/public-health-dataset/reported_counts.csv"),
    pathlib.Path("deliverables/public-health-dataset/snapshot_clocks.csv"),
    pathlib.Path("deliverables/public-health-dataset/sources.csv"),
    pathlib.Path("deliverables/public-health-dataset/staged_observations.csv"),
    pathlib.Path("deliverables/public-health-dataset/timeline.csv"),
    pathlib.Path("deliverables/public-health-dataset/zones.csv"),
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


# Operational suspected axis (point-prevalence, national-only, NEVER summed
# into confirmed). The cumulative suspected tier was retired 2026-06-02; these
# three keys carry the operational caseload split INRB publishes at the latest
# SitRep. They are routed out of the cumulative reported_counts surface into a
# distinct operational_status block.
_OPERATIONAL_SUSPECTED_KEYS: tuple[str, ...] = (
    "suspected_under_investigation",
    "suspected_in_isolation",
    "suspected_active",
)
# Retired cumulative suspected keys: stripped from the cumulative reported_counts
# surface entirely (no headline cumulative-suspected number is published).
_RETIRED_CUMULATIVE_SUSPECTED_KEYS: tuple[str, ...] = (
    "suspected",
    "suspected_cumulative",
)
# Snapshot date that anchors the operational caseload (the latest SitRep cutoff).
_OPERATIONAL_AS_OF = "2026-05-31"


def _reviewed_promotion_data_as_of(source_id: str) -> str:
    try:
        rows = sitrep_promotions.load_reviewed_promotions()
    except sitrep_promotions.SitRepPromotionError:
        return ""
    for row in rows:
        if row.get("source_id") == source_id:
            return str(row.get("data_as_of") or "")
    return ""


def _reviewed_promotion_by_number(number: int) -> dict[str, Any] | None:
    """Return the reviewed SitRep promotion payload for ``number``, or None.

    Source-of-truth for the province-burden overlay (SitRep #019 Table 1). Reads
    the same reviewed promotion store the refresh pipeline uses; degrades to None
    (overlay omitted) if no reviewed promotions exist rather than failing the
    whole public export.
    """
    try:
        by_number = sitrep_promotions.reviewed_promotions_by_number()
    except sitrep_promotions.SitRepPromotionError:
        return None
    return by_number.get(number)


def _reviewed_promotion_by_source_id(source_id: str | None) -> dict[str, Any] | None:
    if not source_id:
        return None
    try:
        rows = sitrep_promotions.load_reviewed_promotions()
    except sitrep_promotions.SitRepPromotionError:
        return None
    for row in rows:
        if row.get("source_id") == source_id:
            return row
    return None


def _operational_as_of(*rows: Mapping[str, Any] | None) -> str:
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        source_id = str(row.get("primary_source_id") or "")
        data_as_of = _reviewed_promotion_data_as_of(source_id)
        if data_as_of:
            return data_as_of
    return _OPERATIONAL_AS_OF


def _reported_count_subobject(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project a reported-count row to the public min/max/primary sub-object shape."""
    return {
        "primary": row.get("primary"),
        "min": row.get("min"),
        "max": row.get("max"),
        "primary_source_id": row.get("primary_source_id"),
        "conflicting_source_ids": list(row.get("conflicting_source_ids") or []),
    }


# Operational queue-field publication status (spec 2026-06-02 carry-forward):
#   - published       : the latest SitRep published this field fresh.
#   - carried_forward : the latest SitRep omitted the field, so the value is
#                       carried forward from a prior snapshot (its CountRange
#                       carries carried_forward_from / carried_forward_reason).
#   - omitted         : the field is not present at all (no value to surface).
# The `omitted` value is part of the published contract for completeness; a
# field with no value is dropped from the block entirely (never emitted with a
# null primary), so a surfaced sub-object is always either published or
# carried_forward.
_OPERATIONAL_STATUS_PUBLISHED = "published"
_OPERATIONAL_STATUS_CARRIED_FORWARD = "carried_forward"
_OPERATIONAL_STATUS_OMITTED = "omitted"


def _operational_subobject(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project an operational queue-field row, carrying its publication status.

    Extends the reported-count sub-object shape with a publication `status` enum
    and, when the underlying CountRange carries forward from a prior snapshot,
    explicit `carriedForwardFrom` (ISO date, truncated to YYYY-MM-DD) and
    `carriedForwardReason` provenance. `_response_state()` copies these
    sub-objects verbatim, so the carry-forward provenance mirrors onto the
    national response axis without recomputation.
    """
    sub = _reported_count_subobject(row)
    carried_from = row.get("carried_forward_from")
    if carried_from:
        sub["status"] = _OPERATIONAL_STATUS_CARRIED_FORWARD
        sub["carriedForwardFrom"] = str(carried_from)[:10]
        reason = row.get("carried_forward_reason")
        if reason:
            sub["carriedForwardReason"] = str(reason)
    else:
        sub["status"] = _OPERATIONAL_STATUS_PUBLISHED
    return sub


def _operational_status(reported_counts: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the point-prevalence operational-status block (spec 2026-06-02).

    Sourced from the three operational suspected reported_counts keys. The block
    is explicitly non-cumulative, national-only, and never summed into confirmed.
    Returns None when none of the operational keys are present.
    """
    under_investigation = reported_counts.get("suspected_under_investigation")
    in_isolation = reported_counts.get("suspected_in_isolation")
    active_total = reported_counts.get("suspected_active")
    if under_investigation is None and in_isolation is None and active_total is None:
        return None
    block: dict[str, Any] = {
        "as_of": _operational_as_of(active_total, under_investigation, in_isolation),
        "basis": "point_prevalence_not_cumulative",
        "summable_into_confirmed": False,
        "note": (
            "Suspected cases pending classification at the latest SitRep, by "
            "published response-pipeline status. Some SitReps publish only part "
            "of the split; omitted subfields are not inferred. A point-in-time "
            "operational caseload, national-only, not a cumulative case count, "
            "and never added to confirmed."
        ),
    }
    if under_investigation is not None:
        block["suspected_under_investigation"] = _operational_subobject(
            under_investigation
        )
    if in_isolation is not None:
        block["suspected_in_isolation"] = _operational_subobject(in_isolation)
    if active_total is not None:
        block["active_suspected_total"] = _operational_subobject(active_total)
    return block


# ---------------------------------------------------------------------------
# Response-state surfacing (2026-06-02): per-zone contact follow-up, care, and
# escapes plus the consumed national operational axis.
# ---------------------------------------------------------------------------
#
# The national fields CONSUME the operational_status block built above (the
# suspected-retirement's single source of truth) and are NEVER recomputed here:
# responseState references that axis by value with an explicit
# `national_axis_source: "operational_status"` provenance tag. The per-zone
# figures are the ND-aware response-operations counts surfaced from the
# INRB-UMIE per-zone tables (loader: lovs.insp_per_zone_loader.load_response_state),
# carried into the source as `response_state_block`. Province roll-ups are
# aggregations OF the per-zone source via the zone->province map below, labelled
# province scope; they are never painted back onto individual zones, and a zone
# the source marks ND renders null ("not reported"), never zero, never
# backfilled.

# LOVS zone_id -> canonical province label. Derived from data/zones.json (COD
# health zones), normalised to the French INRB province labels used by the
# SitRep source (zones.json carries both "North Kivu" and "Nord-Kivu" for the
# same province; this map collapses that inconsistency). Only zones that can
# carry response data (the INRB-bridge COD zones) need an entry; an unmapped
# zone aggregates under province `null` rather than being invented into a
# province.
_ZONE_PROVINCE: Mapping[str, str] = {
    "aru": "Ituri",
    "aungba": "Ituri",
    "bambu": "Ituri",
    "bunia": "Ituri",
    "damas": "Ituri",
    "fataki": "Ituri",
    "gety": "Ituri",
    "kilo": "Ituri",
    "kambala": "Ituri",
    "komanda": "Ituri",
    "lita": "Ituri",
    "logo": "Ituri",
    "mahagi-cod": "Ituri",
    "mambasa": "Ituri",
    "mangala": "Ituri",
    "mongbwalu": "Ituri",
    "nia-nia": "Ituri",
    "nizi": "Ituri",
    "nyankunde": "Ituri",
    "rimba": "Ituri",
    "rwampara": "Ituri",
    "tchomia": "Ituri",
    "beni-cod": "Nord-Kivu",
    "butembo": "Nord-Kivu",
    "goma-cod": "Nord-Kivu",
    "karisimbi-cod": "Nord-Kivu",
    "katwa": "Nord-Kivu",
    "kalunguta": "Nord-Kivu",
    "kyondo": "Nord-Kivu",
    "mabalako": "Nord-Kivu",
    "masereka": "Nord-Kivu",
    "musienene": "Nord-Kivu",
    "oicha": "Nord-Kivu",
    "vuhovi": "Nord-Kivu",
    "miti-murhesa": "Sud-Kivu",
}

# Contact follow-up coverage bands (spec): >=0.90 strong, 0.70-0.89 partial,
# <0.70 weak, and unknown when the ratio is not computable (either count ND).
_COVERAGE_STRONG = 0.90
_COVERAGE_PARTIAL = 0.70


def _coverage_band(coverage: float | None) -> str:
    if coverage is None:
        return "unknown"
    if coverage >= _COVERAGE_STRONG:
        return "strong"
    if coverage >= _COVERAGE_PARTIAL:
        return "partial"
    return "weak"


def _follow_up_coverage(
    contacts_seen: int | None, contacts_under_follow_up: int | None
) -> float | None:
    """seen / under-follow-up, or None when not computable (ND or zero base).

    A zero contacts-under-follow-up base yields None (no coverage is defined),
    never a divide-by-zero or a fabricated 0.0/1.0.
    """
    if (
        contacts_seen is None
        or contacts_under_follow_up is None
        or contacts_under_follow_up <= 0
    ):
        return None
    return round(contacts_seen / contacts_under_follow_up, 4)


def _response_zone_row(zone_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project one ND-aware per-zone response row for the public surface.

    `None` is preserved as JSON null ("not reported"); a real reported zero
    stays 0. `patients_in_care` is a care/isolation census and is never
    relabelled as a suspected case count.
    """
    traced = raw.get("contacts_under_follow_up")
    seen = raw.get("contacts_seen")
    care = raw.get("patients_in_care")
    escapes = raw.get("hospital_escapes")
    coverage = _follow_up_coverage(seen, traced)
    return {
        "province": _ZONE_PROVINCE.get(zone_id),
        "contacts_under_follow_up": traced,
        "contacts_seen": seen,
        "contact_follow_up_coverage": coverage,
        "coverage_band": _coverage_band(coverage),
        "patients_in_care": care,
        "hospital_escapes": escapes,
    }


def _sum_present(values: Iterable[int | None]) -> int | None:
    """Sum the non-null entries; None when every entry is null (all-ND province)."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _response_province_rollup(
    by_zone: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Aggregate per-zone response figures up to province scope.

    Each province figure is the sum of the non-null per-zone values (None when
    every contributing zone is null for that metric). Coverage is recomputed
    from the province-summed seen/under-follow-up so it stays an aggregation of
    the source, never a per-zone value smeared across the province.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for zone_id, row in by_zone.items():
        province = _ZONE_PROVINCE.get(zone_id)
        if province is None:
            continue
        grouped.setdefault(province, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for province, rows in grouped.items():
        traced = _sum_present(r.get("contacts_under_follow_up") for r in rows)
        seen = _sum_present(r.get("contacts_seen") for r in rows)
        care = _sum_present(r.get("patients_in_care") for r in rows)
        escapes = _sum_present(r.get("hospital_escapes") for r in rows)
        coverage = _follow_up_coverage(seen, traced)
        out[province] = {
            "scope": "province",
            "zone_count": len(rows),
            "contacts_under_follow_up": traced,
            "contacts_seen": seen,
            "contact_follow_up_coverage": coverage,
            "coverage_band": _coverage_band(coverage),
            "patients_in_care": care,
            "hospital_escapes": escapes,
        }
    return out


def _response_state(
    source: Mapping[str, Any], operational_status: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Assemble the responseState block (spec 2026-06-02).

    National fields CONSUME `operational_status` (built once by the suspected
    retirement) and are never recomputed; per-zone contacts/care/escapes are
    added from the source `response_state_block`; province roll-ups aggregate
    the per-zone source. Returns None when neither the national axis nor any
    per-zone response data is present.
    """
    block = source.get("response_state_block") or {}
    raw_by_zone = block.get("by_lovs_zone") or {}
    has_zone_data = isinstance(raw_by_zone, dict) and len(raw_by_zone) > 0
    if operational_status is None and not has_zone_data:
        return None

    by_zone: dict[str, dict[str, Any]] = {}
    if has_zone_data:
        for zone_id, raw in sorted(raw_by_zone.items()):
            if isinstance(raw, dict):
                by_zone[zone_id] = _response_zone_row(zone_id, raw)

    out: dict[str, Any] = {
        "as_of": (operational_status or {}).get("as_of") or block.get("as_of"),
        "scope_note": (
            "Per-zone contact follow-up, care/isolation census, and hospital "
            "escapes from the INRB-UMIE per-health-zone response tables. Province "
            "figures are aggregations of the per-zone source, labelled province "
            "scope, never painted onto individual zones. A zone the source marks "
            "ND is reported as null (not reported), never zero. patients_in_care "
            "is a care/isolation census, never a case count and never a suspected "
            "case count."
        ),
    }
    if operational_status is not None:
        # CONSUME the national operational axis by value; never recompute it.
        national: dict[str, Any] = {
            "national_axis_source": "operational_status",
            "basis": operational_status.get("basis"),
            "summable_into_confirmed": operational_status.get(
                "summable_into_confirmed", False
            ),
        }
        for key in (
            "suspected_under_investigation",
            "suspected_in_isolation",
            "active_suspected_total",
        ):
            if key in operational_status:
                national[key] = operational_status[key]
        out["national"] = national
    if by_zone:
        # CLOCK HONESTY: the per-zone response tables stop earlier than the
        # headline (they trail to the latest non-ND response date, e.g.
        # 2026-05-30 against a 2026-05-31 headline). Surface that response-data
        # date as a DISTINCT `data_as_of` on the block, sourced from the per-zone
        # block, never the headline `as_of` and never differenced against it, so
        # the per-zone layer never claims to be as current as the headline.
        out["data_as_of"] = block.get("data_as_of")
        out["source_id"] = block.get("source_id")
        out["method_basis"] = block.get("method_basis")
        out["by_zone"] = by_zone
        out["by_province"] = _response_province_rollup(by_zone)
    return out


def _headline_evidence_chain_ids(
    reported_counts: Mapping[str, Any],
    reported_deaths: Mapping[str, Any],
    registry: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Headline evidence-chain provenance for the public snapshot (redacted).

    Resolves, from each headline metric's ``primary_source_id``, the reviewed
    evidence chain that BACKS it (via ``lovs_evidence`` locator binding) and
    emits a per-metric entry. The public projection WITHHOLDS the raw
    ``ec:lovs:`` chain id (the sensitive needle the export scrubs) and instead
    publishes the chain's anchored source (``chain_source``, which the publish
    gate matches against the metric's ``primary_source_id``), the chain's review
    date, and a ``backed`` boolean. This makes the embedded chain a GENERATED
    consequence of the source: change the source and the backing chain changes.
    """
    reg = registry if registry is not None else lovs_evidence.load_registry()
    confirmed = reported_counts.get("confirmed") if isinstance(reported_counts, Mapping) else None
    deaths_confirmed = reported_deaths.get("confirmed") if isinstance(reported_deaths, Mapping) else None
    entries = lovs_evidence.headline_evidence_provenance(
        reg,
        confirmed_primary_source_id=(
            confirmed.get("primary_source_id") if isinstance(confirmed, Mapping) else None
        ),
        confirmed_deaths_primary_source_id=(
            deaths_confirmed.get("primary_source_id")
            if isinstance(deaths_confirmed, Mapping)
            else None
        ),
    )
    public_entries: list[dict[str, Any]] = []
    for entry in entries:
        chain_id = entry.get("evidence_chain_id")
        # Redact the raw ec:lovs: id; keep its trailing review date (already a
        # public source date) so the surface still names WHEN the chain reviewed
        # the source, without leaking the sensitive id.
        chain_date = None
        if isinstance(chain_id, str):
            match = lovs_evidence._CHAIN_ID_RE.fullmatch(chain_id)
            if match:
                chain_date = chain_id.rsplit(":", 1)[-1]
        public_entries.append(
            {
                "metric": entry["metric"],
                "primary_source_id": entry["primary_source_id"],
                "chain_source": entry["chain_source"],
                "chain_reviewed_date": chain_date,
                "backed": entry["backed"],
            }
        )
    return public_entries


def _public_snapshot(
    source: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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

    # Cumulative reported-counts surface: laboratory-confirmed cases are the only
    # cumulative case metric. The operational suspected caseload is routed to a
    # separate point-prevalence operational_status block and the retired
    # cumulative suspected tier is dropped entirely (2026-06-02 retirement). No
    # public surface sums confirmed and suspected.
    raw_reported_counts = source.get("reported_counts", {}) or {}
    cumulative_reported_counts = {
        key: value
        for key, value in raw_reported_counts.items()
        if key not in _OPERATIONAL_SUSPECTED_KEYS
        and key not in _RETIRED_CUMULATIVE_SUSPECTED_KEYS
    }
    operational_status = _operational_status(raw_reported_counts)

    # Confirmed deaths are a cumulative epidemiological metric on the headline
    # surface (the laboratory-anchored death tier, parallel to confirmed cases).
    # Today only the 'confirmed' death class is published; the schema guards the
    # absence of any class so a future suspected/probable death class flows
    # through automatically. Each class is projected to the same public
    # min/max/primary sub-object shape used by reported_counts.
    raw_reported_deaths = source.get("reported_deaths", {}) or {}
    reported_deaths = {
        death_class: _reported_count_subobject(row)
        for death_class, row in raw_reported_deaths.items()
        if isinstance(row, Mapping)
    }

    snapshot: dict[str, Any] = {
        "schema_version": "1.1",
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
        "reported_counts": cumulative_reported_counts,
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
            "Laboratory-confirmed cases and confirmed deaths are the only cumulative epidemiological counts; suspected counts are an operational point-prevalence snapshot, national-only, and never summed into confirmed.",
            "Quantitative model, calibration, and corridor-probability internals are not part of this public data contract.",
        ],
    }
    if reported_deaths:
        snapshot["reported_deaths"] = reported_deaths
    # Headline evidence-chain provenance: bind each headline metric's
    # primary_source_id to the reviewed chain that backs it (derived, never
    # hardcoded). Placed beside reported_counts/reported_deaths so a consumer can
    # see, on the headline surface, which chain stands behind 370/63.
    headline_chain_ids = _headline_evidence_chain_ids(
        cumulative_reported_counts, reported_deaths
    )
    if headline_chain_ids:
        snapshot["headline_evidence_chain_ids"] = headline_chain_ids

    # SitRep generation surfaces (the website renderer mirrors each into
    # its own camelCased key). All three are DERIVED from reviewed source-of-truth
    # so the published surface can never go stale against the headline source.
    #
    #  * confirmedDeathSeries -> timeline[].deathsConfirmed / deathsBasis: the
    #    apples-to-apples country-scope confirmed-death history. The broad
    #    register (timeline[].deaths) stays a separate suspected-basis series.
    #  * provinceBurden: the province confirmed/death floor from the same reviewed
    #    SitRep promotion as the headline count.
    #  * dateSemantics.sourceClocks[headline_count_endpoint]: the headline clock,
    #    derived from reported_counts.confirmed.primary_source_id; the generation
    #    invariant FAILs if a hand-edited clock ever drifts from that source.
    confirmed_primary_source_id = (
        (cumulative_reported_counts.get("confirmed") or {}).get("primary_source_id")
    )
    source_clocks = sitrep_overlays.headline_source_clock(confirmed_primary_source_id)
    sitrep_overlays.assert_headline_clock_matches_source(
        source_clocks, confirmed_primary_source_id
    )
    if source_clocks:
        snapshot.setdefault("date_semantics", {})["source_clocks"] = source_clocks

    death_series = sitrep_overlays.confirmed_death_series(manifest or {})
    if death_series:
        snapshot["confirmed_death_series"] = death_series

    burden_promotion = _reviewed_promotion_by_source_id(confirmed_primary_source_id)
    if burden_promotion is None:
        burden_promotion = _reviewed_promotion_by_number(19)
    if burden_promotion is not None:
        burden = sitrep_overlays.province_burden(burden_promotion)
        if burden:
            snapshot["province_burden"] = burden

    if operational_status is not None:
        snapshot["operational_status"] = operational_status
    response_state = _response_state(source, operational_status)
    if response_state is not None:
        snapshot["responseState"] = response_state
    return snapshot


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
    # Cumulative suspected tier retired 2026-06-02: the per-zone table carries
    # only the laboratory-confirmed cumulative metrics. The revision-cap kludge
    # (which zeroed and flagged per-zone suspected when the revised national
    # over-summed the stale per-zone table) is removed with it.
    block = source.get("insp_per_zone_block", {})
    rows: list[dict[str, Any]] = []
    for zone_id, row in sorted(block.get("by_lovs_zone", {}).items()):
        rows.append(
            {
                "zone_id": zone_id,
                "source_id": block.get("source_id"),
                "source_data_date": block.get("as_of_data_date"),
                "confirmed": row.get("confirmed"),
                "confirmed_deaths": row.get("confirmed_deaths"),
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


LIVE_OUTPUT_PATH = pathlib.Path("data/live-bdbv-2026-output.json")

# Fields copied verbatim from live-bdbv-2026-output.json into
# data/public_export_source.json by sanitize_public_export_source().
# Excludes model internals (transmission, mode_b_hypotheses, calibration_blocks,
# corridors, visibility, per_zone_under_ascertainment_bands, analysis_dependency_audit)
# and other non-public fields. Keep this list in sync with the curated source
# file shape; the export tests assert on the surface of the sanitized object.
_PUBLIC_EXPORT_SOURCE_FIELDS: tuple[str, ...] = (
    "affected_zones",
    "as_of",
    "attribution_lag_disclosure",
    "data_as_of",
    "insp_per_zone_block",
    "outbreak_id",
    "reported_counts",
    "reported_deaths",
    "response_state_block",
    "source_conflict_notes",
    "source_review_geographies",
    "sources",
    "zone_attributed_counts",
    "zone_attributed_counts_source_ids",
)


def sanitize_public_export_source(live: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Project live-bdbv-2026-output.json down to the public-safe fields.

    The result is written to data/public_export_source.json by
    write_public_export_source() and is the upstream input to
    build_public_artifacts(). Model internals (transmission, calibration,
    visibility, per-zone ascertainment) are intentionally excluded.

    The carried_forward_from / carried_forward_reason fields surface through
    reported_counts naturally because they're already inside the sub-objects
    that live-bdbv-2026-output.json emits.
    """
    if live is None:
        live = _read_json(LIVE_OUTPUT_PATH)
    out: dict[str, Any] = {
        "schema_version": "1.0",
        "snapshot_role": "public_source_snapshot",
    }
    for key in _PUBLIC_EXPORT_SOURCE_FIELDS:
        if key in live:
            out[key] = live[key]
    return out


def write_public_export_source() -> None:
    """Regenerate data/public_export_source.json from the live snapshot output."""
    sanitized = sanitize_public_export_source()
    (REPO_ROOT / PUBLIC_EXPORT_SOURCE_PATH).write_text(
        _json_text(sanitized), encoding="utf-8", newline=""
    )


def build_public_artifacts() -> dict[pathlib.Path, str]:
    source = _read_json(PUBLIC_EXPORT_SOURCE_PATH)
    manifest = _read_json(SOURCE_MANIFEST_PATH)
    commitments = _read_json(CALIBRATION_COMMITMENTS_PATH)
    public_snapshot = _public_snapshot(source, manifest)
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
                "confirmed_deaths",
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
    parser.add_argument(
        "--sanitize-source",
        action="store_true",
        help=(
            "Regenerate data/public_export_source.json from data/live-bdbv-2026-output.json "
            "(strips model internals; preserves carried_forward_from on reported_counts)."
        ),
    )
    args = parser.parse_args(argv)

    if args.sanitize_source:
        write_public_export_source()
        print("public export source sanitized from live output")
        return 0

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

The public snapshot preserves the headline reported-count range, primary source ID, and conflict-anchor source IDs for confirmed cases and confirmed deaths, which are the only cumulative metrics (see Cumulative Metrics And The Operational Axis below). It does not assert that every public source agrees. Source disagreement is documented in `data/public_source_conflicts.json`.

Counts are interpreted as public claims tied to sources, not as private surveillance records. When public sources disagree, this package preserves the disagreement instead of forcing a single blended value.

## Cumulative Metrics And The Operational Axis

Laboratory-confirmed cases and confirmed deaths are the only cumulative epidemiological metrics on the headline surface. The confirmed tier is the laboratory-anchored rung of the WHO suspected, probable, confirmed classification ladder, and a cumulative confirmed count behaves like a running incidence total that does not decrease as the event progresses.

The suspected counts INRB now publishes (cases under investigation and cases in isolation) are an operational caseload: a point-in-time count of who is currently in the response pipeline at the latest SitRep (116 under investigation plus 173 in isolation, 289 active, as of 2026-06-01). They live on a separate, clearly labeled operational axis (`operational_status` in `data/public_snapshot.json`), are national-only, are not cumulative, and are never added into the confirmed count.

This package deliberately does not reproduce a composite "total" that sums confirmed cases with the active suspected caseload. Confirmed is a cumulative incidence quantity and the active suspected caseload is a point prevalence; summing a running total with a current-state count mixes a stock with a flow, and it also conflates the diagnostic-certainty classification axis with the operational-status axis. The upstream cumulative-suspected series is additionally unreliable as a cumulative quantity because investigation re-bases it downward (the national cumulative-suspected figure fell from 1077 to 906 to 349 across consecutive reporting days), and the event has no published probable tier, so cumulative reduces to confirmed only under the standard WHO-AFRO convention.

The cumulative suspected tier (both suspected cases and suspected deaths) is paused and archived, not deleted: prior suspected figures and their source conflict trails are retained as dated provenance, and the tier can be reactivated in a future snapshot if a sound cumulative suspected or probable series becomes available upstream. The grounding references for this section are listed in `CITATIONS.md` under "Case classification and the cumulative-versus-operational distinction."

## Health-Zone Tables

`data/public_zone_counts_2026-05-29.csv` exposes source-attributed health-zone counts for public-health review. The table is a public evidence artifact, not a replacement for official health-zone reporting or case management.

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
| What health-zone counts are available? | `data/public_zone_counts_2026-05-29.csv` |
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
"""


if __name__ == "__main__":
    raise SystemExit(main())
