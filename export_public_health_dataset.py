#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export the BDBV 2026 evidence dataset as a public-health workbook.

The exporter is deliberately a flattening layer. It reads the pinned snapshot,
source manifest, evidence-chain registry, zone metadata, and optional
calibration ledger; it does not fetch live data and does not rerun stochastic
model code.

Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from lovs import sitrep_promotions
from lovs import source_dates


REPO_ROOT = pathlib.Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "deliverables" / "public-health-dataset"
SNAPSHOT_PATH = DATA_DIR / "live-bdbv-2026-output.json"
MANIFEST_PATH = DATA_DIR / "bundibugyo-2026" / "manifest.json"
EVIDENCE_PATH = DATA_DIR / "evidence-chains.json"
ZONES_PATH = DATA_DIR / "zones.json"
LEDGER_PATH = DATA_DIR / "calibration-ledger.json"
OBSERVED_PATH = DATA_DIR / "external_sources" / "bdbv-2026.observed.json"
WATCH_PATH = DATA_DIR / "external_sources" / "bdbv-2026.watch.json"

WORKBOOK_NAME = "lovs-public-health-dataset.xlsx"
SCHEMA_NAME = "lovs-public-health-dataset.schema.json"
PACKAGE_MANIFEST_NAME = "lovs-public-health-dataset.manifest.json"
OBSOLETE_OUTPUT_NAMES = ("evidence_chains.csv",)

PUBLIC_CLAIM_OVERRIDES: dict[str, dict[str, str]] = {
    "claim:lovs:module-d:bdbv-r-prior-gamma": {
        "topic": "BDBV reproduction prior grounding",
        "claim": "The reproduction prior used for BDBV detection-depth modeling is not directly grounded in a BDBV-specific basic reproduction number estimate.",
        "value": "Implementation parameterization omitted from the public audit extract.",
        "public_action": "Do not cite the reproduction prior as BDBV-specific R0 evidence until a direct source or explicit derivation is added.",
    },
    "claim:lovs:module-c:reporting-delay-priors": {
        "topic": "Reporting-delay sensitivity comparator",
        "claim": "The former Camacho 2015 EBOV-Zaire onset-to-notification prior is retained as a sensitivity comparator, not the BDBV default.",
        "value": "Camacho 2015 onset-to-notification comparator; implementation constants omitted from the public audit extract.",
        "public_action": "Keep Camacho labeled as cross-species sensitivity only; do not present it as the default BDBV delay prior.",
    },
    "claim:lovs:grepi:reporting-delay-update": {
        "topic": "BDBV reporting-delay default",
        "claim": "The reporting-completeness interval uses the BDBV-specific Rosello 2015 Isiro onset-to-notification distribution as its default prior, with the single-prior-outbreak limitation disclosed.",
        "value": "Rosello 2015 BDBV onset-to-notification default; Camacho 2015 EBOV-Zaire retained as sensitivity comparator.",
        "public_action": "Keep the reporting-completeness result labeled as prior-based and historical, not a fitted 2026 reporting-delay estimate.",
    },
    "claim:lovs:module-b:detection-depth-priors": {
        "topic": "Detection-depth priors",
        "claim": "Detection-depth inputs are source-backed where possible, but under-ascertainment and species transfer remain explicit modeling limitations.",
        "value": "Implementation parameterization omitted from the public audit extract.",
        "public_action": "Keep serial-interval and incubation references source-linked; do not describe heuristic under-ascertainment as direct BDBV evidence.",
    },
    "claim:lovs:module-d:corridor-gravity-exponents": {
        "topic": "Corridor gravity exponents",
        "claim": "The corridor-gravity constants are transparent engineering heuristics, not fitted or quoted literature-grounded values.",
        "value": "Implementation parameterization omitted from the public audit extract.",
        "public_action": "Do not present the current corridor exponents as literature-grounded. Fit or calibrate them before claiming source-backed corridor discrimination.",
    },
}

MAY24_RELEASE_CHAIN = "ec:lovs:data:bdbv-may24-official-release:2026-05-24"


SHEET_COLUMNS: dict[str, list[str]] = {
    "README": ["field", "value"],
    "Snapshot Clocks": [
        "row_id",
        "clock_type",
        "date_value",
        "timestamp_value",
        "source_id",
        "source_role",
        "source_tier",
        "status",
        "note",
    ],
    "Reported Counts": [
        "row_id",
        "row_type",
        "metric",
        "location",
        "as_of_date",
        "value",
        "value_min",
        "value_max",
        "unit",
        "source_id",
        "conflicting_source_ids",
        "evidence_ref",
        "evidence_status",
        "derivation_type",
        "source_url",
        "archive_sha256",
        "raw_archive_status",
        "license",
        "correction_note",
        "basis",
    ],
    "Timeline": [
        "row_id",
        "date",
        "metric",
        "value",
        "unit",
        "source_id",
        "evidence_ref",
        "source_url",
        "archive_sha256",
        "license",
        "note",
        "basis",
    ],
    "Zones": [
        "zone_id",
        "name",
        "country",
        "province",
        "kind",
        "role",
        "lat",
        "lon",
        "confidence",
        "source_note",
        "evidence_status",
        "correction_note",
    ],
    "Corridors": [
        "row_id",
        "source",
        "target",
        "horizon_days",
        "risk_raw_lower_50",
        "risk_raw_upper_50",
        "risk_adj_lower_50",
        "risk_adj_upper_50",
        "risk_adj_lower_95",
        "risk_adj_upper_95",
        "drivers",
        "evidence_ref",
        "evidence_status",
        "derivation_type",
        "source_ids",
        "correction_note",
    ],
    "Model Outputs": [
        "row_id",
        "module",
        "metric",
        "value",
        "value_lower",
        "value_upper",
        "unit",
        "evidence_ref",
        "evidence_status",
        "derivation_type",
        "source_ids",
        "note",
    ],
    "Analysis Dependency Audit": [
        "surface",
        "status",
        "input_values",
        "output_values",
        "clock_basis",
        "model_use",
        "held_out_reason",
        "blocked_by",
    ],
    "Calibration Ledger": [
        "block_id",
        "pinned_at",
        "resolves_at",
        "status",
        "calibration_point_id",
        "corridor",
        "source",
        "target",
        "horizon_days",
        "risk_adj_lower_50",
        "risk_adj_upper_50",
        "evidence_ref",
        "evidence_status",
        "note",
    ],
    "Public Claim Audit": [
        "public_claim_id",
        "topic",
        "claim",
        "value",
        "audit_status",
        "source_refs",
        "source_urls",
        "public_action",
        "public_note",
    ],
    "Sources": [
        "source_id",
        "publisher",
        "source_tier",
        "url",
        "published_at",
        "retrieved_at",
        "content_hash",
        "raw_archive_status",
        "license",
        "license_note",
        "extraction_status",
        "country_scope",
    ],
    "Staged Observations": [
        "row_id",
        "kind",
        "source_id",
        "source_chain",
        "publisher",
        "source_tier",
        "published_at",
        "data_as_of",
        "retrieved_at",
        "metric",
        "case_status",
        "value",
        "value_kind",
        "location_scope",
        "claim_status",
        "admissibility",
        "model_use",
        "conflicts_with",
        "source_url",
        "evidence_ref",
        "note",
    ],
    "Corrections Gaps": [
        "gap_id",
        "severity",
        "topic",
        "status",
        "evidence_ref",
        "source_refs",
        "source_url",
        "archive_sha256",
        "license",
        "public_action",
        "note",
    ],
    "Per-Zone Snapshot": [
        "lovs_zone_id",
        "inrb_canonical_nom",
        "as_of_data_date",
        "confirmed",
        "confirmed_deaths",
        "present_in_insp_classification",
        "inrb_collapsed_from",
        "source_id",
        "method_basis",
    ],
    "Reconciliation Residuals": [
        "metric",
        "as_of_data_date",
        "national_at_data_date",
        "sum_per_zone",
        "unallocated_residual",
        "method_basis",
        "source_id",
        "note",
    ],
    "Attribution Lag Disclosure": [
        "metric",
        "as_of_data_date",
        "timeliness",
        "share_attributed_to_zones",
        "narrative",
        "source_id",
    ],
    "Data Dictionary": ["sheet", "column", "definition"],
}


DATA_DICTIONARY: dict[str, dict[str, str]] = {
    "Snapshot Clocks": {
        "row_id": "Stable identifier for the clock row.",
        "clock_type": "snapshot_date, analytic_as_of, source_data_report_date, source_publication_date, source_retrieval_date, or generated_at.",
        "date_value": "YYYY-MM-DD clock value when the clock is date-granular.",
        "timestamp_value": "Full timestamp when the clock is timestamp-granular.",
        "source_id": "Manifest source identifier when this clock belongs to a source edition.",
        "source_role": "How the clock is used, such as headline endpoint, corridor source load, source review, or context.",
        "source_tier": "Source tier from the manifest.",
        "status": "recorded, derived_from_manifest, or not_recorded.",
        "note": "Interpretation caveat for the clock row.",
    },
    "Reported Counts": {
        "row_id": "Stable row identifier within this export.",
        "row_type": "source_extracted_metric or snapshot_reconciled_metric.",
        "metric": "Reported quantity. Lab-confirmed cases and confirmed deaths are the cumulative epidemiological counts; suspected figures appear only as per-source historical provenance or as point-in-time operational caseload (under investigation, in isolation, active), never summed into confirmed.",
        "location": "Geographic scope represented by the value when available.",
        "as_of_date": "Data date, publication date, or snapshot date used for the row.",
        "value": "Single extracted value when the source reports one.",
        "value_min": "Lower endpoint for reconciled ranges.",
        "value_max": "Upper endpoint for reconciled ranges.",
        "unit": "Count or model unit.",
        "source_id": "Manifest source identifier, normalized to the source registry.",
        "conflicting_source_ids": "Other dated sources included in the reconciled range.",
        "evidence_ref": "Evidence-chain ID, source-manifest reference, or explicit audit-gap marker.",
        "evidence_status": "Supported, corrected, needs source, restricted, or derived status.",
        "derivation_type": "How the row was produced: extracted, reconciled, or model-derived.",
        "source_url": "Primary public source URL.",
        "archive_sha256": "SHA-256 hash for archived public bytes or hash-recorded restricted bytes.",
        "raw_archive_status": "public_bytes or private_restricted_bytes.",
        "license": "Publisher/source license recorded in the manifest.",
        "correction_note": "Known correction or limitation relevant to the row.",
        "basis": "Death-axis basis: confirmed_only for death rows dated on/after 2026-06-02 (laboratory-confirmed death tier), broad_register for earlier death rows (mixed confirmed+suspected register). Empty for non-death rows.",
    },
    "Sources": {
        "content_hash": "SHA-256 hash recorded by the source manifest.",
        "raw_archive_status": "Whether raw source bytes are redistributed or kept private/restricted.",
        "license_note": "Publisher-term caveat, especially for restricted or uncertain material.",
    },
    "Staged Observations": {
        "kind": "staged_observation or watch_signal.",
        "source_id": "Manifest-backed source identifier(s), when the source has archived manifest provenance.",
        "source_chain": "Human-readable source chain for watch/context signals.",
        "value_kind": "Whether the value is exact, approximate, bounded, range, or qualitative.",
        "admissibility": "Whether this row can feed a model run, cross-check only, context only, or is blocked.",
        "model_use": "Downstream use permission for model code.",
        "conflicts_with": "Other staged/source rows that must be reconciled before model use.",
    },
    "Analysis Dependency Audit": {
        "surface": "Analytic or display surface that should declare whether it consumed the latest count-bearing source inputs.",
        "status": "updated, updated_snapshot_level, carried_forward, source_attribution_lag, or blocked.",
        "input_values": "Public JSON summary of latest input counts consumed by this surface.",
        "output_values": "Public JSON summary of output values produced from those inputs.",
        "clock_basis": "Clock/date interpretation used for the surface.",
        "model_use": "How this surface may be used downstream.",
        "held_out_reason": "Reason a latest count was not used as an ordinary dated data point, when applicable.",
        "blocked_by": "Source review or data-availability blocker preventing full update, when applicable.",
    },
    "Public Claim Audit": {
        "public_claim_id": "Opaque public claim identifier. Detailed audit-registry IDs are intentionally withheld from this export.",
        "topic": "Human-readable audit topic.",
        "claim": "Public-health claim or methodology claim being audited.",
        "audit_status": "Public audit status: supported, corrected, needs primary source, or unsupported attribution.",
        "source_refs": "Public source citations or restricted-source placeholders. Detailed source-step IDs are intentionally withheld.",
        "source_urls": "Public source URLs where available; restricted local paths are redacted.",
        "public_action": "Action a reader should take when interpreting or correcting this claim.",
    },
    "Per-Zone Snapshot": {
        "lovs_zone_id": "LOVS canonical zone_id (lower_snake_case) matching the corridor_watchlist source_zones list.",
        "inrb_canonical_nom": "INRB-UMIE canonical Nom (post upstream aliases.csv collapse) bridged from lovs_zone_id via data/lovs_zone_alias_bridge.json.",
        "as_of_data_date": "Data date the INRB-UMIE INSP per-zone tables describe (typically earlier than the snapshot publication date).",
        "confirmed": "INSP per-zone cumulative confirmed cases attributable to this LOVS zone at as_of_data_date.",
        "confirmed_deaths": "INSP per-zone cumulative confirmed deaths attributable to this LOVS zone at as_of_data_date; spec section 2.3 attribution-lag hierarchy classifies this metric as trailing.",
        "present_in_insp_classification": "Three-state coverage audit: present_with_data, present_but_zero, or structurally_absent (spec section 2.1).",
        "inrb_collapsed_from": "INRB raw row spellings that the upstream aliases.csv collapsed into this canonical Nom.",
        "source_id": "INRB-UMIE consortium release source_id.",
        "method_basis": "Always INRB_UMIE_INSP_per_zone_v1.",
    },
    "Reconciliation Residuals": {
        "metric": "One of confirmed, confirmed_deaths.",
        "as_of_data_date": "Data date of the INSP per-zone reconciliation.",
        "national_at_data_date": "INSP national rollup value at this metric and date.",
        "sum_per_zone": "Sum of zone-attributed values across LOVS-bridged zones at this date.",
        "unallocated_residual": "national_at_data_date minus sum_per_zone; the reconciliation contract (spec section 5.1) requires this to be non-negative for every metric.",
        "method_basis": "Always INRB_UMIE_INSP_per_zone_v1.",
        "source_id": "INRB-UMIE consortium release source_id.",
        "note": "Optional interpretation note.",
    },
    "Attribution Lag Disclosure": {
        "metric": "One of confirmed, confirmed_deaths.",
        "as_of_data_date": "Data date the disclosure describes.",
        "timeliness": "Spec section 2.3 attribution-lag hierarchy: timely, near_timely, or trailing.",
        "share_attributed_to_zones": "Proportion of national rollup that is zone-attributed; complementary to unallocated_residual / national.",
        "narrative": "Cross-metric narrative; required to mention the 1-3 week INRB clinical review queue lag for confirmed_deaths.",
        "source_id": "INRB-UMIE consortium release source_id.",
    },
}


def build_snapshot_clock_rows(snapshot: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = manifest.get("entries", [])
    latest_publication_date = max(
        (
            source_dates.source_publication_date(entry) or ""
            for entry in entries
            if source_dates.source_triggers_snapshot(entry)
        ),
        default="",
    )
    rows: list[dict[str, Any]] = [
        {
            "row_id": "snapshot:analytic_as_of",
            "clock_type": "analytic_as_of",
            "date_value": source_dates.date_part(snapshot.get("as_of")) or "",
            "timestamp_value": snapshot.get("as_of", ""),
            "source_id": "",
            "source_role": "model_cutoff",
            "source_tier": "",
            "status": "recorded",
            "note": "Analytic cutoff used by the generated model outputs.",
        },
        {
            "row_id": "snapshot:publication_cutoff",
            "clock_type": "snapshot_date",
            "date_value": latest_publication_date,
            "timestamp_value": "",
            "source_id": "",
            "source_role": "knowledge_state_cutoff",
            "source_tier": "",
            "status": "derived_from_manifest",
            "note": "Latest source-publication date admitted to this working snapshot state.",
        },
        {
            "row_id": "snapshot:generated_at",
            "clock_type": "generated_at",
            "date_value": "",
            "timestamp_value": "",
            "source_id": "",
            "source_role": "artifact_generation",
            "source_tier": "",
            "status": "not_recorded",
            "note": "The current pinned pipeline output does not record an artifact-generation timestamp.",
        },
    ]

    for entry in entries:
        source_id = entry.get("source_id", "")
        source_tier = entry.get("source_tier", "")
        normalized = entry.get("normalized_content") or {}
        model_use = normalized.get("model_use") or normalized.get("capture_role") or ""
        date_rows = (
            (
                "source_data_report_date",
                source_dates.source_data_date(entry) or "",
                "",
                "Data/report date the source says the observation describes.",
            ),
            (
                "source_publication_date",
                source_dates.source_publication_date(entry) or "",
                "",
                "Date the source made the observation available.",
            ),
            (
                "source_retrieval_date",
                source_dates.source_retrieval_date(entry) or "",
                entry.get("retrieved_at", ""),
                "Archive capture timestamp for this source edition.",
            ),
        )
        for clock_type, date_value, timestamp_value, note in date_rows:
            rows.append({
                "row_id": f"{clock_type}:{source_id}",
                "clock_type": clock_type,
                "date_value": date_value,
                "timestamp_value": timestamp_value,
                "source_id": source_id,
                "source_role": public_text(model_use),
                "source_tier": source_tier,
                "status": "recorded" if date_value or timestamp_value else "not_recorded",
                "note": note,
            })
    return rows


def load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def source_lookup(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("entries", []):
        source_id = entry["source_id"]
        lookup[source_id] = entry
        if source_id.endswith("-live"):
            lookup[source_id[:-5]] = entry
    for entry in reviewed_sitrep_promotion_source_rows():
        lookup[entry["source_id"]] = entry
    return lookup


def reviewed_sitrep_promotion_source_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(sitrep_promotions.PROMOTIONS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sitrep_promotions.validate_promotion(payload, path=path, require_reviewed=True)
        rows.append({
            "source_id": payload["source_id"],
            "publisher": "INSP / INRB reviewed SitRep promotion",
            "source_tier": "national_moh_reviewed_promotion",
            "url": payload.get("source_url") or f"file:data/sitrep_promotions/{path.name}",
            "published_at": payload.get("published_at", ""),
            "retrieved_at": payload.get("review", {}).get("reviewed_at", ""),
            "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "raw_archive_status": "reviewed_promotion_json",
            "license": "INSP SitRep restricted; derived promotion metadata Apache-2.0",
            "license_note": (
                "Promotion row is a reviewed derived artifact; consult the restricted "
                "source bytes and evidence chain before redistributing source PDF content."
            ),
            "extraction_status": "reviewed",
            "country_scope": ["COD"],
        })
    return rows


# Public redaction contract for scored/reconciled public surfaces. A source
# still under source-review may remain visible as provenance (source rows and
# clocks), but its extracted numeric rows and claim-audit chains stay out of the
# public scored/reporting tables until the source semantics are reviewed.
PUBLIC_SUPPRESSED_TABLE_SEMANTICS = {
    "source_review",
    "superseded_capture_not_model_input",
}
PUBLIC_SUPPRESSED_MODEL_USES = {
    "display_only_pending_table_semantics",
    "superseded_capture_not_model_input",
}


def is_public_suppressed_entry(entry: dict[str, Any]) -> bool:
    normalized = entry.get("normalized_content") or {}
    status = normalized.get("table_semantics_status") or ""
    model_use = normalized.get("model_use") or normalized.get("capture_role") or ""
    return (
        status in PUBLIC_SUPPRESSED_TABLE_SEMANTICS
        or model_use in PUBLIC_SUPPRESSED_MODEL_USES
    )


def is_public_suppressed_chain(
    chain: dict[str, Any], lookup: dict[str, dict[str, Any]]
) -> bool:
    """A chain is suppressed if any of its sources is a suppressed manifest entry.

    Status is auto-derived from the source registry so the public claim-audit can
    never re-expose a source-review signal a maintainer forgot to scrub by hand.
    """
    for source in chain.get("sources", []):
        source_id = source.get("manifest_source_id") or source.get("locator") or ""
        entry = lookup.get(source_id)
        if entry and is_public_suppressed_entry(entry):
            return True
    return False


def public_audit_chains(
    evidence: dict[str, Any], lookup: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Chains that survive public redaction (source-review/superseded dropped)."""
    return [
        chain
        for chain in evidence.get("chains", [])
        if not is_public_suppressed_chain(chain, lookup)
    ]


def source_meta(lookup: dict[str, dict[str, Any]], source_id: str) -> dict[str, str]:
    entry = lookup.get(source_id, {})
    return {
        "source_id": entry.get("source_id", source_id),
        "source_url": entry.get("url", ""),
        "archive_sha256": entry.get("content_hash", ""),
        "raw_archive_status": entry.get("raw_archive_status", "public_bytes" if entry else ""),
        "license": entry.get("license", ""),
    }


def public_source_id(lookup: dict[str, dict[str, Any]], source_id: str) -> str:
    return source_meta(lookup, source_id)["source_id"]


def public_source_id_list(
    lookup: dict[str, dict[str, Any]],
    source_ids: list[str] | tuple[str, ...],
) -> str:
    out: list[str] = []
    for source_id in source_ids:
        public_id = public_source_id(lookup, source_id)
        if public_id and public_id not in out:
            out.append(public_id)
    return "; ".join(out)


def public_source_ids_for_urls(
    lookup: dict[str, dict[str, Any]],
    urls: list[str] | tuple[str, ...],
) -> str:
    """Return public manifest IDs whose archived URL exactly matches a URL list."""
    return "; ".join(_public_source_ids_for_urls(lookup, urls))


def _public_source_ids_for_urls(
    lookup: dict[str, dict[str, Any]],
    urls: list[str] | tuple[str, ...],
) -> list[str]:
    wanted = {url.strip() for url in urls if str(url).strip()}
    out: list[str] = []
    seen_entries: set[str] = set()
    for entry in lookup.values():
        source_id = entry.get("source_id", "")
        if not source_id or source_id in seen_entries:
            continue
        seen_entries.add(source_id)
        if entry.get("url", "").strip() in wanted:
            public_id = public_source_id(lookup, source_id)
            if public_id and public_id not in out:
                out.append(public_id)
    return out


def public_source_ids_for_watch_signal(
    lookup: dict[str, dict[str, Any]],
    signal: dict[str, Any],
) -> str:
    out = _public_source_ids_for_urls(lookup, signal.get("source_urls", []))
    chain_text = "\n".join(signal.get("source_chain", []))
    seen_entries: set[str] = set()
    for entry in lookup.values():
        source_id = entry.get("source_id", "")
        if not source_id or source_id in seen_entries:
            continue
        seen_entries.add(source_id)
        alias = source_id[:-5] if source_id.endswith("-live") else source_id
        if source_id in chain_text or alias in chain_text:
            public_id = public_source_id(lookup, source_id)
            if public_id and public_id not in out:
                out.append(public_id)
    return "; ".join(out)


def public_source_chain(signal: dict[str, Any]) -> str:
    return "; ".join(public_text(source) for source in signal.get("source_chain", []))


def public_locator(value: str) -> str:
    if value.startswith("file:") or value.startswith("private:"):
        return "restricted-local-review-not-redistributed"
    return value


def public_text(value: Any) -> str:
    text = text_value(value)
    text = re.sub(r"\bec:lovs:[A-Za-z0-9:_-]+", "PUBLIC-CLAIM-AUDIT", text)
    text = re.sub(r"\bclaim:lovs:[A-Za-z0-9:_-]+", "PUBLIC-CLAIM-AUDIT", text)
    text = re.sub(r"\bstep:[A-Za-z0-9:_-]+", "PUBLIC-AUDIT-STEP", text)
    text = re.sub(r"\bsrc:local-[A-Za-z0-9:_-]+", "restricted-local-review", text)
    return (
        text.replace("evidence chain", "public claim audit")
        .replace("evidence-chain", "public-claim-audit")
        .replace("watch_signals", "source-review rows")
        .replace("staged_observations", "admitted observation rows")
        .replace("not_model_input", "not used in model")
        .replace("blocked_pending_official_confirmation", "not admitted pending official confirmation")
        .replace("official_origin_", "official-origin ")
        .replace("promotion_criteria", "review rule")
        .replace("credibility_assessment", "source assessment")
        .replace("source_chase", "source follow-up")
        .replace("source chase", "source follow-up")
        .replace("source-chasing", "source follow-up")
        .replace("watch only", "review only")
        .replace("not a model input", "not used in model")
        .replace("carry this watch", "carry this review item")
        .replace("this chain", "this audit row")
    )


def build_public_claim_index(evidence: dict[str, Any]) -> dict[str, str]:
    """Map detailed chain/claim identifiers to opaque public claim IDs."""
    index: dict[str, str] = {}
    for idx, chain in enumerate(evidence.get("chains", []), start=1):
        public_id = f"BDBV-CLAIM-{idx:03d}"
        chain_id = chain.get("chain_id", "")
        claim_id = chain.get("claim", {}).get("claim_id", "")
        if chain_id:
            index[chain_id] = public_id
        if claim_id:
            index[claim_id] = public_id
    return index


def public_evidence_ref(value: str, public_claims: dict[str, str]) -> str:
    """Return a public reference without exposing detailed audit IDs."""
    if value in public_claims:
        return public_claims[value]
    if value.startswith("source_manifest:"):
        return value
    if value.startswith("audit_gap:"):
        return "PUBLIC-SOURCE-AUDIT"
    if value.startswith("data/calibration-ledger"):
        return "PUBLIC-CALIBRATION-LEDGER"
    if value.startswith("ec:") or value.startswith("claim:"):
        return "PUBLIC-CLAIM-AUDIT"
    return value


def snapshot_reconciled_evidence_ref(
    primary_source_id: str,
    public_claims: dict[str, str],
) -> str:
    """Return the public claim backing the current headline reconciliation."""
    if primary_source_id in {
        "cdc-current-situation-2026-05-24",
        "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
    }:
        return public_evidence_ref(MAY24_RELEASE_CHAIN, public_claims)
    return public_evidence_ref("audit_gap:public-source-row", public_claims)


def public_audit_status(verdict: str) -> str:
    return {
        "supported": "supported",
        "derived_supported": "derived from cited sources",
        "corrected": "corrected",
        "needs_primary_source": "needs primary source",
        "unsupported_attribution": "unsupported attribution",
        "pending": "pending review",
    }.get(verdict, verdict.replace("_", " "))


def public_label(value: Any) -> str:
    """Return a reader-facing label for internal enum-like values."""
    raw = text_value(value)
    labels = {
        "staged_observation": "admitted source observation",
        "watch_signal": "source under review",
        "exact_int": "exact integer",
        "approx_int": "approximate integer",
        "approx_text": "approximate text",
        "lower_bound": "lower bound",
        "model_eligible": "eligible for future model run",
        "cross_check": "cross-check only",
        "context_only": "context only",
        "blocked_pending_official_confirmation": "not admitted pending official confirmation",
        "eligible_after_release": "eligible after release review",
        "cross_check_only": "cross-check only",
        "not_model_input": "not used in model",
        "official_origin_pending_primary_artifact_archive": "official-origin report; primary artifact not yet captured",
        "local_context_pending_official_locality_confirmation": "local-context report pending official locality confirmation",
        "unconfirmed_by_public_health_authority": "not confirmed by public-health authority",
        "official_origin_reported_confirmed_cases_pending_primary_artifact": "reported confirmed cases; primary artifact not yet captured",
    }
    if raw.startswith("watch_tier_"):
        return "source under review"
    return labels.get(raw, raw.replace("_", " ") if raw else "")


def public_topic(claim: dict[str, Any]) -> str:
    override = PUBLIC_CLAIM_OVERRIDES.get(claim.get("claim_id", ""))
    if override:
        return override["topic"]
    claim_id = claim.get("claim_id", "")
    topic = claim_id.rsplit(":", 1)[-1] if claim_id else ""
    return topic.replace("-", " ").replace("_", " ").strip().capitalize()


def public_claim_statement(claim: dict[str, Any]) -> str:
    override = PUBLIC_CLAIM_OVERRIDES.get(claim.get("claim_id", ""))
    if override:
        return override["claim"]
    return public_text(claim.get("statement", ""))


def public_claim_value(claim: dict[str, Any]) -> str:
    override = PUBLIC_CLAIM_OVERRIDES.get(claim.get("claim_id", ""))
    if override:
        return override["value"]
    return public_text(claim.get("value", ""))


def public_claim_action(chain: dict[str, Any]) -> str:
    override = PUBLIC_CLAIM_OVERRIDES.get(chain.get("claim", {}).get("claim_id", ""))
    if override and "public_action" in override:
        return override["public_action"]
    return public_text(chain.get("next_action", ""))


def public_source_ref(source: dict[str, Any]) -> str:
    citation = source.get("citation", "")
    if citation:
        return public_text(citation)
    source_id = source.get("source_id", "")
    if source_id.startswith("src:local-"):
        return "restricted local review (not redistributed)"
    if source_id.startswith("src:"):
        return source_id.removeprefix("src:").replace("-", " ")
    return source_id


def public_source_ids(snapshot: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> str:
    return public_source_id_list(lookup, snapshot.get("sources", []))


def build_readme_rows(
    snapshot: dict[str, Any],
    manifest: dict[str, Any],
    evidence: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {"field": "dataset_title", "value": "LOVS BDBV 2026 public-health evidence dataset"},
        {"field": "outbreak_id", "value": snapshot.get("outbreak_id", "")},
        {"field": "as_of", "value": snapshot.get("as_of", "")},
        {"field": "source_count", "value": len(manifest.get("entries", []))},
        {"field": "public_claim_audit_count", "value": len(public_audit_chains(evidence, lookup))},
        {
            "field": "scope",
            "value": "Generated appendix over pinned snapshot, source manifest, public claim-audit extract, zones, corridors, and calibration ledger.",
        },
        {
            "field": "public_export_policy",
            "value": "This public workbook flattens the detailed audit registry into opaque public claim IDs. Detailed IDs, claim namespaces, review locators, pipeline-step IDs, and local file paths are intentionally withheld.",
        },
        {
            "field": "caution",
            "value": "Methodology artifact only. Not a forecast, travel advisory, clinical instruction, or statement by WHO, Africa CDC, DRC MoH, or Uganda MoH.",
        },
        {
            "field": "license_split",
            "value": "Original schema/annotations follow repository licensing. Third-party source material retains publisher terms; restricted raw bytes are not redistributed.",
        },
        {
            "field": "reproducer_relevance",
            "value": "Calibration rows consume data/calibration-ledger.json when present and do not re-rank live corridors.",
        },
    ]


def iter_numeric_content(prefix: str, content: dict[str, Any]) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in content.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rows.append((name, value))
        elif isinstance(value, dict):
            rows.extend(iter_numeric_content(name, value))
    return rows


def metric_from_key(key: str) -> str:
    if "death" in key:
        return "deaths"
    if "confirmed" in key:
        return "confirmed_cases"
    if "suspected" in key:
        return "suspected_cases"
    return key.replace(".", "_")


# 2026-06-02 the headline death tier became laboratory-confirmed only (the
# cumulative broad death register that mixed confirmed + suspected deaths was
# retired). Every per-point death row carries an explicit `basis` so a reader
# never silently compares a confirmed-only death figure on/after the cutoff
# against a pre-cutoff broad-register death figure. Case rows carry an empty
# basis (the column only qualifies the death axis).
_DEATH_BASIS_CUTOFF = "2026-06-02"


def death_basis(metric: Any, row_date: Any) -> str:
    """Return the death-axis basis label for a timeline/reconciled row.

    Deaths on or after the 2026-06-02 cutoff are `confirmed_only`; earlier dated
    death rows are `broad_register`. Non-death metrics return "" (the basis
    column only qualifies the death axis). The date is compared as a plain
    ISO-prefix string; there is no wall-clock read, so the label is fully
    deterministic for a fixed snapshot.
    """
    metric_text = text_value(metric)
    if "death" not in metric_text:
        return ""
    date_text = text_value(row_date)[:10]
    if date_text >= _DEATH_BASIS_CUTOFF:
        return "confirmed_only"
    return "broad_register"


def build_reported_counts_rows(
    snapshot: dict[str, Any],
    manifest: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        if is_public_suppressed_entry(entry):
            continue  # keep source-review/superseded extracted metrics out of public rows
        source_id = entry["source_id"]
        normalized = entry.get("normalized_content", {})
        source_as_of_date = source_dates.source_data_date(entry) or ""
        for key, value in iter_numeric_content("", normalized):
            meta = source_meta(lookup, source_id)
            metric = metric_from_key(key)
            rows.append({
                "row_id": f"source:{source_id}:{key}",
                "row_type": "source_extracted_metric",
                "metric": metric,
                "location": "; ".join(entry.get("country_scope", [])),
                "as_of_date": source_as_of_date,
                "value": value,
                "value_min": "",
                "value_max": "",
                "unit": "count",
                "source_id": meta["source_id"],
                "conflicting_source_ids": "",
                "evidence_ref": public_evidence_ref(f"source_manifest:{meta['source_id']}", public_claims),
                "evidence_status": "source_manifest_attested",
                "derivation_type": "source_extracted_metric",
                **meta,
                "basis": death_basis(metric, source_as_of_date),
                "correction_note": kinshasa_note(source_id, key),
            })

    for metric, count in snapshot.get("reported_counts", {}).items():
        source_id = count.get("primary_source_id", "")
        meta = source_meta(lookup, source_id)
        row_metric = "deaths" if metric == "deaths" else f"{metric}_cases"
        as_of_date = snapshot.get("as_of", "")
        rows.append({
            "row_id": f"snapshot:reported_counts:{metric}",
            "row_type": "snapshot_reconciled_metric",
            "metric": row_metric,
            "location": "; ".join(snapshot.get("country_scope", [])),
            "as_of_date": as_of_date,
            # The pipeline output serializes ReconciledCount as {min,max,primary}
            # (see refresh_pipeline._count_output); accept the dataclass-style
            # {minimum,maximum,primary_value} too so the values survive either schema.
            "value": count.get("primary", count.get("primary_value", "")),
            "value_min": count.get("min", count.get("minimum", "")),
            "value_max": count.get("max", count.get("maximum", "")),
            "unit": "count",
            "source_id": meta["source_id"],
            "conflicting_source_ids": public_source_id_list(
                lookup, count.get("conflicting_source_ids", [])
            ),
            "evidence_ref": snapshot_reconciled_evidence_ref(source_id, public_claims),
            "evidence_status": "reconciled_from_dated_sources",
            "derivation_type": "snapshot_reconciled_range",
            **meta,
            "basis": death_basis(row_metric, as_of_date),
            "correction_note": "WHO PHEIC deconfirmed the reported Kinshasa case; confirmed minimum excludes Kinshasa." if metric == "confirmed" else "",
        })

    # Post 2026-06-01 schema split: deaths now arrive as a dict keyed by
    # death-class ("confirmed", "suspected"). Emit one row per class. Legacy
    # callers that produced a bare ReconciledCount-shaped dict are routed
    # under the "unspecified" key so they survive without silently dropping.
    deaths_block = snapshot.get("reported_deaths", {}) or {}
    if isinstance(deaths_block, dict) and (
        "primary" in deaths_block or "primary_value" in deaths_block
    ):
        # Legacy shape: a single ReconciledCount dict at this level.
        deaths_block = {"unspecified": deaths_block}
    for death_class, deaths in sorted(deaths_block.items()):
        if not isinstance(deaths, dict):
            continue
        source_id = deaths.get("primary_source_id", "")
        meta = source_meta(lookup, source_id)
        metric = f"deaths_{death_class}"
        as_of_date = snapshot.get("as_of", "")
        rows.append({
            "row_id": f"snapshot:reported_deaths:{death_class}",
            "row_type": "snapshot_reconciled_metric",
            "metric": metric,
            "location": "; ".join(snapshot.get("country_scope", [])),
            "as_of_date": as_of_date,
            "value": deaths.get("primary", deaths.get("primary_value", "")),
            "value_min": deaths.get("min", deaths.get("minimum", "")),
            "value_max": deaths.get("max", deaths.get("maximum", "")),
            "unit": "count",
            "source_id": meta["source_id"],
            "conflicting_source_ids": public_source_id_list(
                lookup, deaths.get("conflicting_source_ids", [])
            ),
            "evidence_ref": snapshot_reconciled_evidence_ref(source_id, public_claims),
            "evidence_status": "reconciled_from_dated_sources",
            "derivation_type": "snapshot_reconciled_range",
            **meta,
            "basis": death_basis(metric, as_of_date),
            "correction_note": "",
        })
    return rows


def kinshasa_note(source_id: str, key: str) -> str:
    if "who-pheic" in source_id and "confirmed" in key:
        return "WHO PHEIC reports 8 Ituri + 2 Kampala; Kinshasa case tested negative and is not counted."
    if "wikipedia" in source_id and "confirmed" in key:
        return "Aggregator value retained as a cross-check; Kinshasa-specific confirmed claim conflicts with WHO deconfirmation."
    return ""


def active_queue_projection_evidence_ref(
    projection: dict[str, Any],
    public_claims: dict[str, str],
) -> str:
    chain_ids = projection.get("evidence_chain_ids", [])
    if isinstance(chain_ids, list):
        for chain in chain_ids:
            mapped = public_evidence_ref(text_value(chain), public_claims)
            if mapped and mapped != "PUBLIC-CLAIM-AUDIT":
                return mapped
    return "PUBLIC-CLAIM-AUDIT"


def build_active_queue_projection_timeline_rows(
    projection: Any,
    source_ids: str,
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(projection, dict):
        return []
    evidence_ref = active_queue_projection_evidence_ref(projection, public_claims)
    rows: list[dict[str, Any]] = []
    for window in projection.get("per_date_windows", []):
        if not isinstance(window, dict):
            continue
        date = text_value(window.get("date")).strip()
        pair = window.get("confirmable_active_queue_50")
        if not date or not (isinstance(pair, list) and len(pair) == 2):
            continue
        positivity_basis = public_text(window.get("positivity_basis", ""))
        note = (
            "Module C2 national known-active-queue lab-yield; "
            f"positivity_basis={positivity_basis}; not reporting completeness "
            "or per-zone allocation."
        )
        for suffix, value in (("lower", pair[0]), ("upper", pair[1])):
            rows.append({
                "row_id": f"timeline:active_queue_lab_yield:{date}:confirmable_active_queue_50_{suffix}",
                "date": date,
                "metric": f"confirmable_active_queue_50_{suffix}",
                "value": value,
                "unit": "count",
                "source_id": public_text(source_ids),
                "evidence_ref": evidence_ref,
                "source_url": "",
                "archive_sha256": "",
                "license": "",
                "note": note,
            })
    return rows


def build_timeline_rows(
    count_rows: list[dict[str, Any]],
    *,
    active_queue_projection: Any = None,
    source_ids: str = "",
    public_claims: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in count_rows:
        if row["row_type"] != "source_extracted_metric":
            continue
        if not text_value(row["as_of_date"]).strip():
            continue
        rows.append({
            "row_id": row["row_id"].replace("source:", "timeline:", 1),
            "date": row["as_of_date"],
            "metric": row["metric"],
            "value": row["value"],
            "unit": row["unit"],
            "source_id": row["source_id"],
            "evidence_ref": row["evidence_ref"],
            "source_url": row["source_url"],
            "archive_sha256": row["archive_sha256"],
            "license": row["license"],
            "note": row["correction_note"],
            "basis": death_basis(row["metric"], row["as_of_date"]),
        })
    rows.extend(
        # Active-queue projection rows are confirmable-queue counts, never a death
        # axis, so they carry an empty basis (death_basis returns "" for any
        # non-death metric).
        {**r, "basis": death_basis(r.get("metric"), r.get("date"))}
        for r in build_active_queue_projection_timeline_rows(
            active_queue_projection,
            source_ids,
            public_claims or {},
        )
    )
    return sorted(rows, key=lambda r: (text_value(r["date"]), text_value(r["metric"]), text_value(r["source_id"])))


def build_zone_rows(zones_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for zone in zones_payload.get("zones", []):
        note = ""
        if zone.get("id") == "kinshasa-cod":
            note = "Case-status text in zone source is stale if it says confirmed; WHO PHEIC deconfirmed the Kinshasa case."
        rows.append({
            "zone_id": zone.get("id", ""),
            "name": zone.get("name", ""),
            "country": zone.get("country", ""),
            "province": zone.get("province", ""),
            "kind": zone.get("kind", ""),
            "role": zone.get("role", ""),
            "lat": zone.get("lat", ""),
            "lon": zone.get("lon", ""),
            "confidence": zone.get("confidence", ""),
            "source_note": zone.get("source", ""),
            "evidence_status": "coordinate_source_note_present",
            "correction_note": note,
        })
    return rows


def build_corridor_rows(
    snapshot: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    source_ids = public_source_ids(snapshot, lookup)
    rows: list[dict[str, Any]] = []
    for idx, corridor in enumerate(snapshot.get("corridors", []), start=1):
        rows.append({
            "row_id": f"corridor:{idx:02d}:{corridor.get('source')}:{corridor.get('target')}",
            "source": corridor.get("source", ""),
            "target": corridor.get("target", ""),
            "horizon_days": corridor.get("horizon_days", ""),
            "risk_raw_lower_50": corridor.get("risk_raw_lower_50", ""),
            "risk_raw_upper_50": corridor.get("risk_raw_upper_50", ""),
            "risk_adj_lower_50": corridor.get("risk_adj_lower_50", ""),
            "risk_adj_upper_50": corridor.get("risk_adj_upper_50", ""),
            "risk_adj_lower_95": corridor.get("risk_adj_lower_95", ""),
            "risk_adj_upper_95": corridor.get("risk_adj_upper_95", ""),
            "drivers": "; ".join(corridor.get("drivers", [])),
            "evidence_ref": public_evidence_ref("ec:lovs:module-d:corridor-gravity-exponents:2026-05-21", public_claims),
            "evidence_status": "derived_model_output_with_unsupported_exponent_attribution",
            "derivation_type": "pinned_snapshot_model_output",
            "source_ids": source_ids,
            "correction_note": "Descriptive watch-point interval, not a forecast or response recommendation.",
        })
    return rows


def build_active_queue_projection_rows(
    projection: Any,
    source_ids: str,
    public_claims: dict[str, Any],
) -> list[dict[str, Any]]:
    """Module C2 active-queue lab-yield projection as public Model Output rows.

    C2 is a SIBLING diagnostic to the C1 reporting-completeness nowcast; it rides
    inside the snapshot ``visibility`` block only for transport, so it is lifted
    out of the generic visibility flatten and emitted as its own labelled rows
    under a distinct module. The internal ``c2_evidence_chain_ids`` list carries
    the sensitive ``ec:lovs:`` namespace, so it is withheld and replaced by a
    count (exactly as the C1 ``evidence_chain_ids`` metric is), and every string
    field is routed through ``public_text`` so no detailed audit id can leak.
    Returns ``[]`` for a missing or malformed projection, leaving the workbook
    byte-identical when C2 is absent.
    """
    if not isinstance(projection, dict):
        return []
    window = projection.get("primary_window") or {}
    inputs = projection.get("inputs") or {}

    def _pair(source: dict[str, Any], key: str) -> tuple[Any, Any]:
        pair = source.get(key)
        if isinstance(pair, list) and len(pair) == 2:
            return pair[0], pair[1]
        return "", ""

    pos_lo, pos_hi = _pair(window, "positivity_50")
    exp_lo, exp_hi = _pair(window, "expected_active_queue_confirmations_50")
    conf_lo, conf_hi = _pair(window, "confirmable_active_queue_50")
    chain_ids = projection.get("evidence_chain_ids", [])
    chain_count = len(chain_ids) if isinstance(chain_ids, list) else 0
    evidence_ref = active_queue_projection_evidence_ref(projection, public_claims)

    def _row(metric: str, *, value: Any = "", lower: Any = "", upper: Any = "",
             unit: str = "", note: str = "") -> dict[str, Any]:
        return {
            "row_id": f"model:active_queue_lab_yield:{metric}",
            "module": "active_queue_lab_yield",
            "metric": metric,
            "value": value,
            "value_lower": lower,
            "value_upper": upper,
            "unit": unit,
            "evidence_ref": evidence_ref,
            "evidence_status": "derived_model_output_sibling_to_visibility",
            "derivation_type": "pinned_snapshot_model_output",
            "source_ids": source_ids,
            "note": public_text(note) if note else "",
        }

    chain_label = (
        f"{chain_count} internal chain reference"
        f"{'' if chain_count == 1 else 's'} (withheld)"
    )
    lab_window = (
        f"{window.get('date_start', '')}/{window.get('date_end', '')}"
        if window.get("date_start") or window.get("date_end")
        else ""
    )
    return [
        _row(
            "recent_lab_positivity_50",
            lower=pos_lo,
            upper=pos_hi,
            unit="proportion",
            note="Recent reviewed-SitRep lab positivity, flat Beta(1,1) posterior; 50% interval.",
        ),
        _row(
            "expected_active_queue_confirmations_50",
            lower=exp_lo,
            upper=exp_hi,
            unit="count",
            note="Expected lab-confirmations within the known active suspected queue; 50% interval.",
        ),
        _row(
            "confirmable_active_queue_50",
            lower=conf_lo,
            upper=conf_hi,
            unit="count",
            note=(
                "Confirmed cases plus expected active-queue confirmations; 50% "
                "interval. Known-queue yield, not reporting completeness or hidden "
                "community incidence."
            ),
        ),
        _row(
            "active_suspected_queue_total",
            value=inputs.get("active_suspected_total", ""),
            unit="count",
            note="Cases under investigation plus cases in isolation (national, point-in-time).",
        ),
        _row(
            "lab_samples_analyzed_recent",
            value=window.get("samples_analyzed", ""),
            unit="count",
        ),
        _row(
            "lab_samples_positive_recent",
            value=window.get("samples_positive", ""),
            unit="count",
        ),
        _row(
            "lab_window",
            value=public_text(lab_window),
            unit="date_range",
        ),
        _row(
            "review_status",
            value=public_text(projection.get("review_status", "")),
            unit="label",
        ),
        _row(
            "method_basis",
            value=public_text(projection.get("method_basis", "")),
            unit="label",
        ),
        _row(
            "evidence_chain_references",
            value=chain_label,
            unit="count",
            note="Detailed audit chain ids intentionally withheld in the public workbook.",
        ),
    ]


def build_model_output_rows(
    snapshot: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    def _strip_internal_audit_fields(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: _strip_internal_audit_fields(item)
                for key, item in value.items()
                if key not in {"evidence_chain_id", "evidence_chain_ids"}
            }
        if isinstance(value, list):
            return [_strip_internal_audit_fields(item) for item in value]
        return value

    def _public_visibility_value(raw_value: Any, metric: str) -> tuple[Any, Any, Any]:
        if metric == "evidence_chain_ids":
            if isinstance(raw_value, list):
                return f"{len(raw_value)} internal chain references (withheld)", "", ""
            return public_text(text_value(raw_value)), "", ""

        value = _strip_internal_audit_fields(raw_value)
        if isinstance(value, list) and len(value) == 2 and all(
            isinstance(item, (int, float)) for item in value
        ):
            return "", value[0], value[1]
        if isinstance(value, (dict, list)):
            return public_text(json.dumps(value, sort_keys=True)), "", ""
        return value, "", ""

    rows: list[dict[str, Any]] = []
    source_ids = public_source_ids(snapshot, lookup)
    visibility = snapshot.get("visibility", {})
    for metric, value in visibility.items():
        if metric == "active_queue_projection":
            # Module C2 lab-yield rides inside visibility for transport only.
            # Lift it out so it is not naively flattened (which would dump the
            # internal ec:lovs: chain list into a cell and trip the sensitive-
            # needle gate); emit dedicated sibling rows with the chain withheld.
            rows.extend(
                build_active_queue_projection_rows(value, source_ids, public_claims)
            )
            continue
        display, lower, upper = _public_visibility_value(value, metric)
        rows.append({
            "row_id": f"model:visibility:{metric}",
            "module": "visibility",
            "metric": metric,
            "value": display,
            "value_lower": lower,
            "value_upper": upper,
            "unit": "proportion" if "completeness" in metric else "days_or_count",
            "evidence_ref": public_evidence_ref("ec:lovs:grepi:reporting-delay-update:2026-05-23", public_claims),
            "evidence_status": "corrected_derived_model_output",
            "derivation_type": "pinned_snapshot_model_output",
            "source_ids": source_ids,
            "note": "Generated from checked-in snapshot output; exporter does not rerun model.",
        })
    transmission = snapshot.get("transmission", {})
    for metric, value in transmission.items():
        if metric == "generations":
            for generation, probability in value.items():
                rows.append({
                    "row_id": f"model:transmission:generation:{generation}",
                    "module": "transmission",
                    "metric": f"generation_{generation}_probability",
                    "value": probability,
                    "value_lower": "",
                    "value_upper": "",
                    "unit": "probability",
                    "evidence_ref": public_evidence_ref("ec:lovs:module-b:detection-depth-priors:2026-05-21", public_claims),
                    "evidence_status": "derived_supported",
                    "derivation_type": "pinned_snapshot_model_output",
                    "source_ids": source_ids,
                    "note": "Generation max bin may be censored; see generations_max_bin_is_censored row.",
                })
        else:
            lower = upper = ""
            display = value
            if isinstance(value, list) and len(value) == 2:
                lower, upper = value
                display = ""
            rows.append({
                "row_id": f"model:transmission:{metric}",
                "module": "transmission",
                "metric": metric,
                "value": display,
                "value_lower": lower,
                "value_upper": upper,
                "unit": "count_or_flag",
                "evidence_ref": public_evidence_ref("ec:lovs:module-b:detection-depth-priors:2026-05-21", public_claims),
                "evidence_status": "derived_supported",
                "derivation_type": "pinned_snapshot_model_output",
                "source_ids": source_ids,
                "note": "",
            })
    return rows


def build_calibration_rows(ledger: dict[str, Any] | None, public_claims: dict[str, str]) -> list[dict[str, Any]]:
    if not ledger:
        return []
    rows: list[dict[str, Any]] = []
    for block in ledger.get("blocks", []):
        pinned_at = block.get("pinned_at", "")
        for index, point in enumerate(block.get("points", []), start=1):
            risk = point.get("risk_adj_50", ["", ""])
            rows.append({
                "block_id": block.get("block_id", ""),
                "pinned_at": pinned_at,
                "resolves_at": block.get("resolves_at", ""),
                "status": block.get("status", ""),
                "calibration_point_id": f"public-calibration-point-{pinned_at}-{index:02d}",
                "corridor": point.get("corridor", ""),
                "source": point.get("source", ""),
                "target": point.get("target", ""),
                "horizon_days": point.get("horizon_days", block.get("horizon_days", "")),
                "risk_adj_lower_50": risk[0] if len(risk) > 0 else "",
                "risk_adj_upper_50": risk[1] if len(risk) > 1 else "",
                "evidence_ref": public_evidence_ref("data/calibration-ledger.json", public_claims),
                "evidence_status": "pre_committed_carry_forward",
                "note": block.get("rationale", ""),
            })
    return rows


def build_public_claim_audit_rows(
    evidence: dict[str, Any],
    public_claims: dict[str, str],
    lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chain in public_audit_chains(evidence, lookup):
        claim = chain.get("claim", {})
        sources = chain.get("sources", [])
        chain_id = chain.get("chain_id", "")
        rows.append({
            "public_claim_id": public_claims.get(chain_id, "BDBV-CLAIM-UNMAPPED"),
            "topic": public_topic(claim),
            "claim": public_claim_statement(claim),
            "value": public_claim_value(claim),
            "audit_status": public_audit_status(chain.get("verdict", "")),
            "source_refs": "; ".join(public_source_ref(source) for source in sources),
            "source_urls": "; ".join(public_locator(source.get("url", "")) for source in sources),
            "public_action": public_claim_action(chain),
            "public_note": "Detailed audit IDs and review locators are withheld from this public export.",
        })
    return rows


def build_source_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        rows.append({
            "source_id": entry.get("source_id", ""),
            "publisher": entry.get("publisher", ""),
            "source_tier": entry.get("source_tier", ""),
            "url": entry.get("url", ""),
            "published_at": entry.get("published_at", ""),
            "retrieved_at": entry.get("retrieved_at", ""),
            "content_hash": entry.get("content_hash", ""),
            "raw_archive_status": entry.get("raw_archive_status", "public_bytes"),
            "license": entry.get("license", ""),
            "license_note": public_text(entry.get("license_note", "")),
            "extraction_status": entry.get("extraction_status", ""),
            "country_scope": "; ".join(entry.get("country_scope", [])),
        })
    for entry in reviewed_sitrep_promotion_source_rows():
        rows.append({
            "source_id": entry.get("source_id", ""),
            "publisher": entry.get("publisher", ""),
            "source_tier": entry.get("source_tier", ""),
            "url": entry.get("url", ""),
            "published_at": entry.get("published_at", ""),
            "retrieved_at": entry.get("retrieved_at", ""),
            "content_hash": entry.get("content_hash", ""),
            "raw_archive_status": entry.get("raw_archive_status", ""),
            "license": entry.get("license", ""),
            "license_note": public_text(entry.get("license_note", "")),
            "extraction_status": entry.get("extraction_status", ""),
            "country_scope": "; ".join(entry.get("country_scope", [])),
        })
    return rows


def build_staged_observation_rows(
    observed: dict[str, Any],
    watch: dict[str, Any],
    lookup: dict[str, dict[str, Any]],
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obs in observed.get("staged_observations", []):
        rows.append({
            "row_id": obs.get("observation_id", ""),
            "kind": "staged_observation",
            "source_id": public_source_id(lookup, obs.get("source_id", "")),
            "source_chain": "",
            "publisher": obs.get("publisher", ""),
            "source_tier": obs.get("source_tier", ""),
            "published_at": obs.get("published_at", ""),
            "data_as_of": obs.get("data_as_of", ""),
            "retrieved_at": obs.get("retrieved_at", ""),
            "metric": obs.get("metric", ""),
            "case_status": obs.get("case_status", ""),
            "value": obs.get("value", ""),
            "value_kind": obs.get("value_kind", ""),
            "location_scope": public_text(obs.get("location_scope", {})),
            "claim_status": obs.get("claim_status", ""),
            "admissibility": obs.get("admissibility", ""),
            "model_use": obs.get("model_use", ""),
            "conflicts_with": "; ".join(obs.get("conflicts_with", [])),
            "source_url": obs.get("source_url", ""),
            "evidence_ref": public_evidence_ref(obs.get("evidence_ref", ""), public_claims),
            "note": public_text(obs.get("note", "")),
        })
    for signal in watch.get("watch_signals", []):
        rows.append({
            "row_id": signal.get("signal_id", ""),
            "kind": public_label("watch_signal"),
            "source_id": public_source_ids_for_watch_signal(lookup, signal),
            "source_chain": public_source_chain(signal),
            "publisher": signal.get("publisher", ""),
            "source_tier": public_label(signal.get("confidence_tier", "")),
            "published_at": signal.get("reported_at", ""),
            "data_as_of": signal.get("reported_at", ""),
            "retrieved_at": signal.get("retrieved_at", ""),
            "metric": "geographic_expansion_signal",
            "case_status": public_label(signal.get("case_status", "")),
            "value": signal.get("claim", ""),
            "value_kind": public_label("qualitative"),
            "location_scope": public_text(signal.get("geography", {})),
            "claim_status": public_label(signal.get("claim_status", "")),
            "admissibility": public_label("blocked_pending_official_confirmation"),
            "model_use": public_label(signal.get("model_use", "")),
            "conflicts_with": "",
            "source_url": "; ".join(signal.get("source_urls", [])),
            "evidence_ref": public_evidence_ref(signal.get("evidence_ref", ""), public_claims),
            "note": public_text(signal.get("promotion_criteria", "")),
        })
    return rows


def build_corrections_gap_rows(
    manifest_lookup: dict[str, dict[str, Any]],
    evidence: dict[str, Any],
    public_claims: dict[str, str],
) -> list[dict[str, Any]]:
    who_pheic = source_meta(manifest_lookup, "who-pheic-2026-05-17")
    rows = [
        {
            "gap_id": "correction:kinshasa-deconfirmation:2026-05-17",
            "severity": "critical",
            "topic": "Kinshasa case status",
            "status": "corrected_in_source_manifest",
            "evidence_ref": public_evidence_ref("source_manifest:who-pheic-2026-05-17-live", public_claims),
            "source_refs": who_pheic["source_id"],
            "source_url": who_pheic["source_url"],
            "archive_sha256": who_pheic["archive_sha256"],
            "license": who_pheic["license"],
            "public_action": "Exclude Kinshasa from confirmed-case counts unless a later primary source reconfirms it.",
            "note": "WHO PHEIC update says the reported Kinshasa case tested negative on confirmatory INRB testing and is not a confirmed case.",
        }
    ]
    for chain in evidence.get("chains", []):
        verdict = chain.get("verdict", "")
        if verdict not in {"unsupported_attribution", "needs_primary_source", "corrected"}:
            continue
        claim = chain.get("claim", {})
        public_id = public_claims.get(chain.get("chain_id", ""), "BDBV-CLAIM-UNMAPPED")
        rows.append({
            "gap_id": public_id,
            "severity": "important" if verdict != "corrected" else "resolved",
            "topic": public_topic(claim),
            "status": public_audit_status(verdict),
            "evidence_ref": public_id,
            "source_refs": "; ".join(public_source_ref(source) for source in chain.get("sources", [])),
            "source_url": "; ".join(public_locator(source.get("url", "")) for source in chain.get("sources", [])),
            "archive_sha256": "",
            "license": "",
            "public_action": public_claim_action(chain),
            "note": public_claim_statement(claim),
        })
    return rows


def build_per_zone_snapshot_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan A 2026-05-28: one row per LOVS source zone from `insp_per_zone_block`."""
    block = snapshot.get("insp_per_zone_block")
    if not isinstance(block, dict):
        return []
    by_zone = block.get("by_lovs_zone") or {}
    # Bridge lookup so the public CSV carries the INRB canonical Nom alongside
    # the LOVS zone_id. The bridge is the load-bearing identity-resolution
    # surface (spec section 3 two-stage alias pipeline).
    try:
        from lovs.zone_alias_bridge import ZoneAliasBridge

        bridge = ZoneAliasBridge.load_default()
    except Exception:
        bridge = None
    rows: list[dict[str, Any]] = []
    for zone_id in sorted(by_zone):
        row = by_zone[zone_id]
        inrb_nom = bridge.inrb_for(zone_id) if bridge is not None else ""
        rows.append(
            {
                "lovs_zone_id": zone_id,
                "inrb_canonical_nom": inrb_nom or "",
                "as_of_data_date": str(block.get("as_of_data_date", "")),
                "confirmed": int(row.get("confirmed", 0)),
                "confirmed_deaths": int(row.get("confirmed_deaths", 0)),
                "present_in_insp_classification": str(
                    row.get("present_in_insp_classification", "")
                ),
                "inrb_collapsed_from": text_value(row.get("inrb_collapsed_from") or []),
                "source_id": str(block.get("source_id", "")),
                "method_basis": str(block.get("method_basis", "")),
            }
        )
    return rows


def build_reconciliation_residuals_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan A 2026-05-28: one row per cumulative metric.

    Post 2026-06-02 suspected-retirement: the per-zone block is confirmed-only
    on the cumulative axis (confirmed and confirmed_deaths), so two rows are
    emitted when the block is present.
    """
    block = snapshot.get("insp_per_zone_block")
    if not isinstance(block, dict):
        return []
    by_zone = block.get("by_lovs_zone") or {}
    national = block.get("national_at_data_date") or {}
    residual = block.get("unallocated_residual") or {}
    rows: list[dict[str, Any]] = []
    for metric in ("confirmed", "confirmed_deaths"):
        zone_sum = sum(int(z.get(metric, 0)) for z in by_zone.values())
        nat = int(national.get(metric, 0))
        res = int(residual.get(metric, 0))
        note = ""
        if metric == "confirmed_deaths":
            note = (
                "Confirmed deaths trail the national rollup by 1-3 weeks while "
                "the INRB clinical review queue catches up; spec section 2.3."
            )
        rows.append(
            {
                "metric": metric,
                "as_of_data_date": str(block.get("as_of_data_date", "")),
                "national_at_data_date": nat,
                "sum_per_zone": zone_sum,
                "unallocated_residual": res,
                "method_basis": str(block.get("method_basis", "")),
                "source_id": str(block.get("source_id", "")),
                "note": note,
            }
        )
    return rows


def build_attribution_lag_disclosure_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Plan A 2026-05-28: one row per metric from `attribution_lag_disclosure`."""
    lag = snapshot.get("attribution_lag_disclosure")
    if not isinstance(lag, dict):
        return []
    block = snapshot.get("insp_per_zone_block") or {}
    as_of = str(block.get("as_of_data_date", ""))
    source_id = str(block.get("source_id", ""))
    narrative = str(lag.get("narrative", ""))
    rows: list[dict[str, Any]] = []
    for entry in lag.get("per_metric") or []:
        rows.append(
            {
                "metric": str(entry.get("metric", "")),
                "as_of_data_date": as_of,
                "timeliness": str(entry.get("timeliness", "")),
                "share_attributed_to_zones": float(entry.get("share_attributed_to_zones", 0.0)),
                "narrative": narrative,
                "source_id": source_id,
            }
        )
    return rows


def build_dictionary_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sheet, columns in SHEET_COLUMNS.items():
        for column in columns:
            definition = DATA_DICTIONARY.get(sheet, {}).get(column, "")
            if not definition:
                definition = column.replace("_", " ").capitalize()
            rows.append({"sheet": sheet, "column": column, "definition": definition})
    return rows


def dependency_model_use(status: str) -> str:
    if status == "updated":
        return "current_model_or_display_input"
    if status == "updated_snapshot_level":
        return "current_snapshot_level_model_input"
    if status == "source_attribution_lag":
        return "headline_context_only_for_spatial_attribution"
    if status == "carried_forward":
        return "carried_forward_until_new_source_input"
    return "blocked_pending_source_review"


def dependency_held_out_reason(row: dict[str, Any]) -> str:
    status = row.get("status", "")
    surface = row.get("surface", "")
    clock_basis = text_value(row.get("clock_basis", ""))
    if status == "source_attribution_lag":
        return "Latest headline aggregate is not allocated into zone-attributed model rows until a reviewed cumulative health-zone table is available."
    if status == "updated_snapshot_level" and "publication" in clock_basis:
        return "Publication-clock count updates the snapshot-level model input but is not plotted as an ordinary connected dated trajectory node."
    if surface == "public_reporting_trajectory" and "publication clock" in clock_basis:
        return "Deaths headline aggregate remains in count reconciliation, but the publication-only endpoint is separated from dated death-line nodes."
    return ""


def build_analysis_dependency_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in snapshot.get("analysis_dependency_audit", []):
        clock_basis = row.get("clock_basis") or row.get("blocked_by") or "No clock basis recorded."
        public_row = {
            "surface": public_text(row.get("surface", "")),
            "status": public_text(row.get("status", "")),
            "input_values": public_text(row.get("inputs", {})),
            "output_values": public_text(row.get("outputs", {})),
            "clock_basis": public_text(clock_basis),
            "model_use": dependency_model_use(row.get("status", "")),
            "held_out_reason": public_text(dependency_held_out_reason(row)),
            "blocked_by": public_text(row.get("blocked_by", "")),
        }
        rows.append(public_row)
    return rows


def build_sheets() -> dict[str, list[dict[str, Any]]]:
    snapshot = load_json(SNAPSHOT_PATH)
    manifest = load_json(MANIFEST_PATH)
    evidence = load_json(EVIDENCE_PATH)
    zones = load_json(ZONES_PATH)
    ledger = load_json(LEDGER_PATH) if LEDGER_PATH.exists() else None
    observed = load_json(OBSERVED_PATH) if OBSERVED_PATH.exists() else {}
    watch = load_json(WATCH_PATH) if WATCH_PATH.exists() else {}
    lookup = source_lookup(manifest)
    public_claims = build_public_claim_index(evidence)
    source_ids = public_source_ids(snapshot, lookup)

    reported_counts = build_reported_counts_rows(snapshot, manifest, lookup, public_claims)
    sheets = {
        "README": build_readme_rows(snapshot, manifest, evidence, lookup),
        "Snapshot Clocks": build_snapshot_clock_rows(snapshot, manifest),
        "Reported Counts": reported_counts,
        "Timeline": build_timeline_rows(
            reported_counts,
            active_queue_projection=(
                snapshot.get("visibility", {}).get("active_queue_projection")
            ),
            source_ids=source_ids,
            public_claims=public_claims,
        ),
        "Zones": build_zone_rows(zones),
        "Corridors": build_corridor_rows(snapshot, lookup, public_claims),
        "Model Outputs": build_model_output_rows(snapshot, lookup, public_claims),
        "Analysis Dependency Audit": build_analysis_dependency_rows(snapshot),
        "Calibration Ledger": build_calibration_rows(ledger, public_claims),
        "Public Claim Audit": build_public_claim_audit_rows(evidence, public_claims, lookup),
        "Sources": build_source_rows(manifest),
        "Staged Observations": build_staged_observation_rows(observed, watch, lookup, public_claims),
        "Corrections Gaps": build_corrections_gap_rows(lookup, evidence, public_claims),
        "Per-Zone Snapshot": build_per_zone_snapshot_rows(snapshot),
        "Reconciliation Residuals": build_reconciliation_residuals_rows(snapshot),
        "Attribution Lag Disclosure": build_attribution_lag_disclosure_rows(snapshot),
        "Data Dictionary": build_dictionary_rows(),
    }
    validate_export_rows(sheets)
    return sheets


def validate_export_rows(sheets: dict[str, list[dict[str, Any]]]) -> None:
    for sheet_name, rows in sheets.items():
        expected = set(SHEET_COLUMNS[sheet_name])
        for idx, row in enumerate(rows, start=2):
            missing = expected - set(row)
            if missing:
                raise ValueError(f"{sheet_name}:{idx}: missing columns {sorted(missing)}")

    for idx, row in enumerate(sheets["Reported Counts"], start=2):
        required = ("source_id", "source_url", "archive_sha256", "license", "evidence_ref", "evidence_status")
        missing = [field for field in required if not text_value(row.get(field)).strip()]
        if missing:
            raise ValueError(f"Reported Counts:{idx}: missing attribution fields {missing}")

    source_ids = {row["source_id"] for row in sheets["Sources"]}

    def assert_known_source_refs(sheet_name: str, row_index: int, field: str, value: Any) -> None:
        for source_id in [part.strip() for part in text_value(value).split(";") if part.strip()]:
            if source_id not in source_ids:
                raise ValueError(
                    f"{sheet_name}:{row_index}: unknown source_id in {field}: {source_id!r}"
                )

    for idx, row in enumerate(sheets["Reported Counts"], start=2):
        assert_known_source_refs("Reported Counts", idx, "source_id", row["source_id"])
        assert_known_source_refs(
            "Reported Counts", idx, "conflicting_source_ids", row["conflicting_source_ids"]
        )

    for idx, row in enumerate(sheets["Timeline"], start=2):
        if not text_value(row.get("date")).strip():
            raise ValueError(f"Timeline:{idx}: missing data/report date")

    for sheet_name in ("Corridors", "Model Outputs"):
        for idx, row in enumerate(sheets[sheet_name], start=2):
            assert_known_source_refs(sheet_name, idx, "source_ids", row["source_ids"])

    dependency_rows = sheets["Analysis Dependency Audit"]
    required_surfaces = {
        "visibility_module_c",
        "confirmable_underlying_trajectory",
        "death_back_projection_and_grid",
        "corridor_watchlist",
    }
    present_surfaces = {row["surface"] for row in dependency_rows}
    missing_dependency_surfaces = required_surfaces - present_surfaces
    if missing_dependency_surfaces:
        raise ValueError(
            f"Analysis Dependency Audit missing surfaces {sorted(missing_dependency_surfaces)}"
        )
    for idx, row in enumerate(dependency_rows, start=2):
        for field in ("surface", "status", "input_values", "clock_basis", "model_use"):
            if not text_value(row.get(field)).strip():
                raise ValueError(f"Analysis Dependency Audit:{idx}: missing {field}")

    for idx, row in enumerate(sheets["Staged Observations"], start=2):
        if row.get("source_id"):
            assert_known_source_refs("Staged Observations", idx, "source_id", row["source_id"])
        if row["kind"] == "staged_observation":
            for field in ("admissibility", "model_use", "value_kind", "evidence_ref"):
                if not text_value(row.get(field)).strip():
                    raise ValueError(f"Staged Observations:{idx}: missing {field}")

    corrections_text = json.dumps(sheets["Corrections Gaps"], ensure_ascii=False)
    for needle in ("Kinshasa", "Imperial table 3", "Corridor gravity exponents"):
        if needle not in corrections_text:
            raise ValueError(f"Corrections Gaps missing required topic {needle!r}")

    public_text = json.dumps(sheets, ensure_ascii=False)
    sensitive_needles = (
        "ec:lovs:",
        "claim:lovs:",
        "step:",
        "src:local-",
        "raw_bytes_relpath",
        "Evidence Chains",
        "gamma(4.0",
        "under_ascertainment_uniform",
        "clamp [0.1",
        "watch_signals",
        "staged_observations",
        "not_model_input",
        "blocked_pending_official_confirmation",
        "official_origin_",
        "source_chase",
        "source chase",
        "source-chasing",
        "watch only",
        "not a model input",
        "promotion_criteria",
        "credibility_assessment",
        "/Users/",
    )
    for needle in sensitive_needles:
        if needle in public_text:
            raise ValueError(f"Public export still exposes nonpublic audit detail {needle!r}")


def write_csvs(sheets: dict[str, list[dict[str, Any]]], output_dir: pathlib.Path) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for sheet_name, rows in sheets.items():
        if sheet_name == "README":
            continue
        filename = sheet_name.lower().replace(" ", "_") + ".csv"
        path = output_dir / filename
        columns = SHEET_COLUMNS[sheet_name]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: text_value(row.get(column, "")) for column in columns})
        paths.append(path)
    return paths


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def cell_xml(row_idx: int, col_idx: int, value: Any, header: bool = False) -> str:
    ref = f"{column_letter(col_idx)}{row_idx}"
    style = ' s="1"' if header else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    escaped = xml_escape(text_value(value))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t>{escaped}</t></is></c>'


def sheet_xml(sheet_name: str, rows: list[dict[str, Any]]) -> str:
    columns = SHEET_COLUMNS[sheet_name]
    xml_rows = []
    header_cells = "".join(cell_xml(1, idx, column, header=True) for idx, column in enumerate(columns, start=1))
    xml_rows.append(f'<row r="1">{header_cells}</row>')
    for row_idx, row in enumerate(rows, start=2):
        cells = "".join(cell_xml(row_idx, col_idx, row.get(column, "")) for col_idx, column in enumerate(columns, start=1))
        xml_rows.append(f'<row r="{row_idx}">{cells}</row>')
    last_col = column_letter(len(columns))
    last_row = max(1, len(rows) + 1)
    widths = "".join(
        f'<col min="{idx}" max="{idx}" width="{min(max(len(column) + 4, 12), 42)}" customWidth="1"/>'
        for idx, column in enumerate(columns, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{widths}</cols>"
        f"<sheetData>{''.join(xml_rows)}</sheetData>"
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        "</worksheet>"
    )


# Reference-upstream posture (Option A): the human-facing workbook references the
# upstream INRB-UMIE/INSP release for the verbatim per-health-zone table rather
# than re-presenting it. The machine-readable per-zone_snapshot.csv in the same
# bundle retains the transcribed values as the reconciliation-integrity substrate.
PER_ZONE_XLSX_POINTER: list[tuple[str, str]] = [
    (
        "Per-health-zone counts",
        "Referenced upstream, not re-presented in this workbook (reference-upstream posture).",
    ),
    ("Upstream publisher", "INRB-UMIE consortium; per-health-zone series is INSP DRC SitRep material"),
    (
        "Upstream release",
        "https://github.com/INRB-UMIE/Ebola_DRC_2026 (build-2026-05-28-bb8b7d5, data as of 2026-05-26)",
    ),
    (
        "Terms",
        "Reuse with attribution to INSP and citation of the report number and date; "
        "confirm distribution terms with INSP before external republication.",
    ),
    (
        "Reconciliation totals",
        "See the 'Reconciliation Residuals' sheet (national, sum-of-per-zone-attributed, unallocated residual).",
    ),
    (
        "Machine-readable values",
        "per-zone_snapshot.csv in this bundle retains the transcribed values as the reconciliation-integrity substrate.",
    ),
]


def pointer_sheet_xml(field_value_rows: list[tuple[str, str]]) -> str:
    """Render a 2-column (field/value) worksheet that references an upstream source.

    Used for human-facing sheets under the reference-upstream posture, so the
    workbook points to the upstream release instead of re-hosting a verbatim
    table. Deterministic content keeps the workbook byte-stable for a snapshot.
    """
    columns = ["field", "value"]
    xml_rows = [
        '<row r="1">'
        + "".join(cell_xml(1, idx, c, header=True) for idx, c in enumerate(columns, start=1))
        + "</row>"
    ]
    for row_idx, (field, value) in enumerate(field_value_rows, start=2):
        xml_rows.append(
            f'<row r="{row_idx}">'
            + cell_xml(row_idx, 1, field)
            + cell_xml(row_idx, 2, value)
            + "</row>"
        )
    last_row = max(1, len(field_value_rows) + 1)
    widths = (
        '<col min="1" max="1" width="28" customWidth="1"/>'
        '<col min="2" max="2" width="90" customWidth="1"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{widths}</cols>"
        f"<sheetData>{''.join(xml_rows)}</sheetData>"
        f'<autoFilter ref="A1:B{last_row}"/>'
        "</worksheet>"
    )


def write_xlsx(sheets: dict[str, list[dict[str, Any]]], path: pathlib.Path) -> None:
    sheet_names = list(SHEET_COLUMNS)

    def put(z: zipfile.ZipFile, name: str, data: str) -> None:
        # Pin a fixed timestamp so the workbook is byte-deterministic for a fixed
        # snapshot. zipfile.writestr(str, ...) would otherwise stamp the current
        # wall-clock time into every entry, churning the committed deliverable and
        # invalidating the SHA-256 the package manifest records for it.
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, data)

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        put(z, "[Content_Types].xml", content_types_xml(len(sheet_names)))
        put(z, "_rels/.rels", root_rels_xml())
        put(z, "docProps/core.xml", core_xml())
        put(z, "docProps/app.xml", app_xml(sheet_names))
        put(z, "xl/workbook.xml", workbook_xml(sheet_names))
        put(z, "xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheet_names)))
        put(z, "xl/styles.xml", styles_xml())
        for idx, sheet_name in enumerate(sheet_names, start=1):
            if sheet_name == "Per-Zone Snapshot":
                # Reference-upstream: the human-facing workbook tab points to the
                # INRB-UMIE/INSP release instead of re-presenting the verbatim
                # per-zone table. The per-zone_snapshot.csv keeps the values.
                worksheet = pointer_sheet_xml(PER_ZONE_XLSX_POINTER)
            else:
                worksheet = sheet_xml(sheet_name, sheets[sheet_name])
            put(z, f"xl/worksheets/sheet{idx}.xml", worksheet)


def content_types_xml(sheet_count: int) -> str:
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{sheets}</Types>"
    )


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    rels += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def core_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>LOVS BDBV 2026 Public Health Evidence Dataset</dc:title>"
        "<dc:creator>Arcede LOVS export_public_health_dataset.py</dc:creator>"
        "</cp:coreProperties>"
    )


def app_xml(sheet_names: list[str]) -> str:
    vector = "".join(f"<vt:lpstr>{xml_escape(name)}</vt:lpstr>" for name in sheet_names)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>LOVS stdlib exporter</Application>"
        f'<TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{vector}</vt:vector></TitlesOfParts>'
        "</Properties>"
    )


def write_schema(output_dir: pathlib.Path) -> pathlib.Path:
    schema = {
        "schema_version": 1,
        "dataset": "lovs-public-health-dataset",
        "row_contract": {
            "reported_counts_required_attribution": [
                "source_id",
                "source_url",
                "archive_sha256",
                "license",
                "evidence_ref",
                "evidence_status",
            ],
            "restricted_data_policy": "Restricted third-party raw bytes and extracted PoE row data are not redistributed; only provenance metadata and audit status are exported.",
        },
        "sheets": [
            {"name": sheet_name, "columns": columns}
            for sheet_name, columns in SHEET_COLUMNS.items()
        ],
    }
    path = output_dir / SCHEMA_NAME
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _artifact_semantic_metadata(
    rel_path: str, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Per-artifact semantic-freshness contract (package manifest schema v2).

    Declares, for the semantic-freshness release gate, the date an artifact is
    expected to represent, the originating source ids, and the text it must (or
    must never) contain. Everything is anchored to the snapshot's own clocks; no
    wall clock is read.

    The death tier is laboratory-confirmed only on/after 2026-06-02, so on that
    basis the retired mixed-basis label "Deaths (reported)" must never appear on
    a count-bearing artifact.
    """
    as_of = str(snapshot.get("as_of", ""))[:10]
    block = snapshot.get("insp_per_zone_block", {}) or {}
    block_date = str(block.get("as_of_data_date", ""))[:10]
    confirmed = snapshot.get("reported_counts", {}).get("confirmed", {}) or {}
    deaths_confirmed = snapshot.get("reported_deaths", {}).get("confirmed", {}) or {}
    confirmed_primary = confirmed.get("primary", confirmed.get("primary_value"))
    deaths_primary = deaths_confirmed.get("primary", deaths_confirmed.get("primary_value"))
    confirmed_only_axis = bool(as_of) and as_of >= _DEATH_BASIS_CUTOFF

    name = pathlib.Path(rel_path).name
    meta: dict[str, Any] = {
        "semantic_as_of": as_of,
        "source_date": as_of,
        "source_ids": [],
        "must_contain_text": [],
        "must_not_contain_text": [],
    }
    source_ids: list[str] = []
    if confirmed.get("primary_source_id"):
        source_ids.append(str(confirmed["primary_source_id"]))
    if deaths_confirmed.get("primary_source_id"):
        source_ids.append(str(deaths_confirmed["primary_source_id"]))
    meta["source_ids"] = sorted(set(source_ids))

    # The per-zone snapshot trails the headline; its source_date is the per-zone
    # block date, and it must carry that date.
    if name == "per-zone_snapshot.csv":
        if block_date:
            meta["source_date"] = block_date
            meta["must_contain_text"] = [block_date]

    # Count-bearing artifacts must never carry the retired mixed-basis death label
    # once the death axis is confirmed-only.
    if confirmed_only_axis and name in {
        "reported_counts.csv",
        "timeline.csv",
        WORKBOOK_NAME,
    }:
        meta["must_not_contain_text"] = ["Deaths (reported)"]

    # The reconciled reported-counts CSV must render the headline primaries.
    if name == "reported_counts.csv":
        contains: list[str] = []
        if isinstance(confirmed_primary, int):
            contains.append(str(confirmed_primary))
        if isinstance(deaths_primary, int):
            contains.append(str(deaths_primary))
        if contains:
            meta["must_contain_text"] = sorted(set(meta["must_contain_text"]) | set(contains))
    return meta


def write_package_manifest(output_dir: pathlib.Path, output_paths: list[pathlib.Path]) -> pathlib.Path:
    inputs = [SNAPSHOT_PATH, MANIFEST_PATH, EVIDENCE_PATH, ZONES_PATH]
    if LEDGER_PATH.exists():
        inputs.append(LEDGER_PATH)

    def input_row(path: pathlib.Path) -> dict[str, str]:
        public_path = str(path.relative_to(REPO_ROOT))
        if path == EVIDENCE_PATH:
            public_path = "restricted/public-claim-audit-source"
        return {"path": public_path, "sha256": sha256_file(path)}

    snapshot = load_json(SNAPSHOT_PATH)

    def output_row(path: pathlib.Path) -> dict[str, Any]:
        rel = str(path.relative_to(output_dir))
        return {
            "path": rel,
            "sha256": sha256_file(path),
            **_artifact_semantic_metadata(rel, snapshot),
        }

    manifest = {
        "schema_version": 2,
        "package": "lovs-public-health-dataset",
        "generated_from_snapshot_as_of": snapshot.get("as_of", ""),
        "inputs": [input_row(path) for path in inputs],
        "outputs": [output_row(path) for path in output_paths],
    }
    path = output_dir / PACKAGE_MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_package(output_dir: pathlib.Path = DEFAULT_OUTPUT_DIR) -> dict[str, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in OBSOLETE_OUTPUT_NAMES:
        obsolete = output_dir / name
        if obsolete.exists():
            obsolete.unlink()
    sheets = build_sheets()
    workbook_path = output_dir / WORKBOOK_NAME
    write_xlsx(sheets, workbook_path)
    csv_paths = write_csvs(sheets, output_dir)
    schema_path = write_schema(output_dir)
    package_manifest_path = write_package_manifest(output_dir, [workbook_path, schema_path, *csv_paths])

    return {
        "workbook": workbook_path,
        "schema": schema_path,
        "manifest": package_manifest_path,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    paths = export_package(args.output_dir)
    print(f"workbook={paths['workbook']}")
    print(f"schema={paths['schema']}")
    print(f"manifest={paths['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
