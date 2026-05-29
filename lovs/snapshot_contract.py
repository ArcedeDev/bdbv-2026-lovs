"""Canonical snapshot contract and release gates.

The contract is the narrow, generated truth every public surface must agree
with: headline counts, zone-attributed model inputs, unallocated counts, and
current corridor-watchlist ranges.  It is derived from the pinned snapshot JSON;
operators should not hand-edit it.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from typing import Any

from lovs.source_ids import source_ids_match


SCHEMA_VERSION = 1

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "data" / "live-bdbv-2026-output.json"
DEFAULT_CONTRACT_PATH = REPO_ROOT / "data" / "snapshot_contract.json"
DEFAULT_DATASET_DIR = REPO_ROOT / "deliverables" / "public-health-dataset"


# ---------------------------------------------------------------------------
# INSP per-zone / PCR modulator / attribution lag / scale resilience vocabulary
# (Plan A landing 2026-05-28; spec §5.1, §5.2, §6.7)
# ---------------------------------------------------------------------------

INSP_METRICS: tuple[str, ...] = (
    "confirmed",
    "suspected",
    "confirmed_deaths",
    "suspected_deaths",
)

# Enum of permissible `data_scale_used` values declared on every snapshot
# carrying any of the new INSP fields (spec §6.7 scale-resilience invariant).
VALID_DATA_SCALES: tuple[str, ...] = (
    "per_zone",
    "partial_per_zone",
    "national",
    "mixed_with_metric_floor",
)

# Scales that REQUIRE an `insp_per_zone_block` (spec §6.7).
SCALES_REQUIRING_PER_ZONE_BLOCK: tuple[str, ...] = (
    "per_zone",
    "partial_per_zone",
    "mixed_with_metric_floor",
)

# Enum for `per_zone_under_ascertainment_bands.surface_role` (spec §5.2).
VALID_PER_ZONE_SURFACE_ROLES: tuple[str, ...] = (
    "primary",
    "corroborating",
    "shadow_in_v1",
    "disclosure",
)

# R3 belt-and-suspenders (Rec J): until Plan C parallel-scoring promotes the
# PCR modulator surface, the only legal `surface_role` on
# `per_zone_under_ascertainment_bands` is `shadow_in_v1`. Any other value MUST
# be refused by the contract before it reaches a release branch.
ALLOWED_PER_ZONE_BANDS_SURFACE_ROLE_THIS_CYCLE = "shadow_in_v1"

# Method-basis vocabulary additions for the new surfaces (spec §5.1, §5.2).
INSP_PER_ZONE_METHOD_BASIS = "INRB_UMIE_INSP_per_zone_v1"
PCR_MODULATED_BANDS_METHOD_BASIS = "africa_cdc_pcr_capacity_modulated_v1"

# Required `attribution_lag_disclosure` keys (spec §2.3, §5.1).
REQUIRED_ATTRIBUTION_LAG_METRIC_FIELDS: tuple[str, ...] = (
    "metric",
    "timeliness",
    "share_attributed_to_zones",
)
ATTRIBUTION_LAG_TIMELINESS_VOCABULARY: tuple[str, ...] = (
    "timely",
    "near_timely",
    "trailing",
)
# At least one metric must declare the 1-3 week confirmed_deaths trailing note.
REQUIRED_ATTRIBUTION_LAG_NARRATIVE_TERMS: tuple[str, ...] = (
    "1-3 week",
    "INRB clinical review",
)


class SnapshotContractError(ValueError):
    """Raised when a snapshot or public artifact violates the contract."""


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotContractError(f"{path}: invalid JSON: {exc}") from exc


def build_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    reported = snapshot.get("reported_counts") or {}
    reported_contract = {
        metric: {
            "primary": _required_int(row, "primary", f"reported_counts.{metric}"),
            "min": _optional_int(row, "min"),
            "max": _optional_int(row, "max"),
            "primary_source_id": str(row.get("primary_source_id", "")),
            "conflicting_source_ids": list(row.get("conflicting_source_ids") or []),
        }
        for metric, row in reported.items()
        if isinstance(row, dict)
    }
    if "confirmed" not in reported_contract:
        raise SnapshotContractError("reported_counts.confirmed is required")

    zone_counts = snapshot.get("zone_attributed_counts") or {}
    if not isinstance(zone_counts, dict):
        raise SnapshotContractError("zone_attributed_counts must be an object")
    zone_rows: dict[str, dict[str, Any]] = {}
    for zone_id, row in sorted(zone_counts.items()):
        if not isinstance(row, dict):
            raise SnapshotContractError(f"zone_attributed_counts.{zone_id} must be an object")
        zone_rows[zone_id] = {
            "confirmed": _required_int(row, "confirmed", f"zone_attributed_counts.{zone_id}"),
            "source_id": _required_str(row, "source_id", f"zone_attributed_counts.{zone_id}"),
            "source_published_at": _required_str(
                row, "source_published_at", f"zone_attributed_counts.{zone_id}"
            ),
            "province": row.get("province", ""),
            "original_zone_id": row.get("original_zone_id", zone_id),
        }

    confirmed_headline = reported_contract["confirmed"]["primary"]
    zone_confirmed = sum(row["confirmed"] for row in zone_rows.values())
    unallocated = confirmed_headline - zone_confirmed

    corridors = snapshot.get("corridors") or []
    if not isinstance(corridors, list) or not corridors:
        raise SnapshotContractError("corridors must be a non-empty list")
    lower_bounds = [_required_number(c, "risk_adj_lower_50", f"corridors[{idx}]") for idx, c in enumerate(corridors)]
    upper_bounds = [_required_number(c, "risk_adj_upper_50", f"corridors[{idx}]") for idx, c in enumerate(corridors)]
    corridor_sources = sorted({str(c.get("source", "")) for c in corridors})
    corridor_targets = sorted({str(c.get("target", "")) for c in corridors})

    zone_source_ids = sorted({row["source_id"] for row in zone_rows.values()})
    source_zone_label = _source_zone_label(zone_source_ids)
    contract = {
        "schema_version": SCHEMA_VERSION,
        "as_of": str(snapshot.get("as_of", ""))[:10],
        "outbreak_id": snapshot.get("outbreak_id"),
        "reported_counts": reported_contract,
        "confirmed_case_partition": {
            "headline_confirmed_total": confirmed_headline,
            "zone_attributed_confirmed_total": zone_confirmed,
            "unallocated_confirmed_total": unallocated,
            "zone_attribution_basis": "official per-health-zone source table"
            if zone_rows
            else "no official per-zone table in snapshot",
            "zone_attribution_source_ids": zone_source_ids,
        },
        "zone_attributed_counts": zone_rows,
        "corridor_watchlist": {
            "corridor_count": len(corridors),
            "source_zone_count": len(corridor_sources),
            "target_zone_count": len(corridor_targets),
            "source_zones": corridor_sources,
            "target_zones": corridor_targets,
            "adjusted_50_lower_range_pct": [_pct(min(lower_bounds)), _pct(max(lower_bounds))],
            "adjusted_50_upper_range_pct": [_pct(min(upper_bounds)), _pct(max(upper_bounds))],
            "top_corridor": {
                "source": str(corridors[0].get("source", "")),
                "target": str(corridors[0].get("target", "")),
                "adjusted_50_lower_pct": _pct(float(corridors[0]["risk_adj_lower_50"])),
                "adjusted_50_upper_pct": _pct(float(corridors[0]["risk_adj_upper_50"])),
            },
        },
        "method_status": {
            "corridor_interpretation": "descriptive_watchlist_not_forecast",
            "source_load_policy": (
                "use newest officially zone-attributed per-health-zone table; "
                "treat headline-vs-zone-table differences as source-attribution lag; "
                "do not scale or smear headline aggregate counts across source zones"
            ),
            "calibration_policy": (
                "active calibration points are immutable pre-commitments and are "
                "not re-derived from later current-watchlist rankings"
            ),
            "known_limitations": [
                "current-outbreak corridor constants are transparent engineering heuristics, not fitted BDBV estimates",
                "current-outbreak corridor intervals are not deployment recommendations",
            ],
        },
        "visibility_method": _visibility_method_contract(snapshot),
        "narrative_required_fragments": {
            "headline_zone_unallocated": narrative_required_fragments_from_values(
                confirmed_headline=confirmed_headline,
                zone_confirmed=zone_confirmed,
                unallocated=unallocated,
                source_zone_count=len(zone_rows),
                source_zone_label=source_zone_label,
                corridor_count=len(corridors),
                lower_range_pct=(_pct(min(lower_bounds)), _pct(max(lower_bounds))),
                upper_range_pct=(_pct(min(upper_bounds)), _pct(max(upper_bounds))),
            )
        },
    }
    # Plan A 2026-05-28 additive fields (spec §5.1, §5.2, §6.7). Each field is
    # optional in the snapshot; absent fields produce no contract entry and the
    # scale-resilience invariant in validate_contract handles the cross-field
    # rule (scales requiring an INSP block must carry one).
    data_scale_used = snapshot.get("data_scale_used")
    if data_scale_used is not None:
        contract["data_scale_used"] = data_scale_used
    insp_block = snapshot.get("insp_per_zone_block")
    if insp_block is not None:
        contract["insp_per_zone_block"] = _project_insp_per_zone_block(insp_block)
    per_zone_bands = snapshot.get("per_zone_under_ascertainment_bands")
    if per_zone_bands is not None:
        contract["per_zone_under_ascertainment_bands"] = _project_per_zone_bands(
            per_zone_bands
        )
    attribution_lag = snapshot.get("attribution_lag_disclosure")
    if attribution_lag is not None:
        contract["attribution_lag_disclosure"] = _project_attribution_lag(
            attribution_lag
        )
    validate_contract(contract)
    return contract


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotContractError(
            f"schema_version must be {SCHEMA_VERSION}, got {contract.get('schema_version')!r}"
        )
    partition = contract.get("confirmed_case_partition") or {}
    headline = _required_int(partition, "headline_confirmed_total", "confirmed_case_partition")
    zone_total = _required_int(partition, "zone_attributed_confirmed_total", "confirmed_case_partition")
    unallocated = _required_int(partition, "unallocated_confirmed_total", "confirmed_case_partition")
    if headline < zone_total:
        raise SnapshotContractError(
            f"zone-attributed confirmed total {zone_total} exceeds headline confirmed {headline}"
        )
    if headline - zone_total != unallocated:
        raise SnapshotContractError(
            "confirmed partition mismatch: headline - zone_attributed != unallocated"
        )

    corridors = contract.get("corridor_watchlist") or {}
    corridor_count = _required_int(corridors, "corridor_count", "corridor_watchlist")
    source_count = _required_int(corridors, "source_zone_count", "corridor_watchlist")
    target_count = _required_int(corridors, "target_zone_count", "corridor_watchlist")
    if source_count and target_count:
        # A zone that appears in BOTH the source and target lists has its self-edge
        # (e.g. goma-cod -> goma-cod) excluded from the corridor set, because a
        # spillover risk from a zone to itself is not a meaningful watch corridor.
        # The expected corridor count is therefore sources * targets minus the
        # intersection count. Added 2026-05-26 when goma-cod was graduated to a
        # pinned target while remaining a confirmed source zone.
        source_zones = set(corridors.get("source_zones") or [])
        target_zones = set(corridors.get("target_zones") or [])
        self_edge_count = len(source_zones & target_zones)
        expected = source_count * target_count - self_edge_count
        if corridor_count != expected:
            raise SnapshotContractError(
                f"corridor count {corridor_count} does not equal "
                f"source zones {source_count} * target zones {target_count} "
                f"minus {self_edge_count} self-edge(s) for zones in both lists"
            )
    _range_pair(corridors, "adjusted_50_lower_range_pct")
    _range_pair(corridors, "adjusted_50_upper_range_pct")
    method_status = contract.get("method_status") or {}
    if method_status.get("corridor_interpretation") != "descriptive_watchlist_not_forecast":
        raise SnapshotContractError(
            "method_status.corridor_interpretation must be descriptive_watchlist_not_forecast"
        )
    source_load_policy = str(method_status.get("source_load_policy", "")).lower()
    for required in (
        "officially zone-attributed",
        "source-attribution lag",
        "do not scale",
        "headline aggregate",
    ):
        if required not in source_load_policy:
            raise SnapshotContractError(
                "method_status.source_load_policy does not state the source-load guardrail"
            )
    visibility_method = contract.get("visibility_method") or {}
    history_count = _required_int(visibility_method, "history_snapshot_count", "visibility_method")
    method_basis = str(visibility_method.get("method_basis", "")).lower()
    method_caveat = str(visibility_method.get("method_caveat", "")).lower()
    if history_count == 0:
        for required in ("single", "prior", "proxy"):
            if required not in method_basis and required not in method_caveat:
                raise SnapshotContractError(
                    "visibility_method must disclose single-snapshot prior/proxy basis"
                )
    if "bdbv_specific" in method_basis:
        delay_prior = visibility_method.get("delay_prior") or {}
        delay_label = str(delay_prior.get("label", "")).lower()
        delay_evidence = str(delay_prior.get("evidence_chain_id", "")).lower()
        delay_gamma = delay_prior.get("gamma_shape_rate") or []
        for required in ("rosello", "bdbv", "onset-to-notification"):
            if required not in delay_label:
                raise SnapshotContractError(
                    "visibility_method.delay_prior must name the BDBV Rosello onset-to-notification prior"
                )
        if delay_evidence != "ec:lovs:grepi:reporting-delay-update:2026-05-23":
            raise SnapshotContractError(
                "visibility_method.delay_prior must carry the grEPI/Rosello evidence-chain id"
            )
        if len(delay_gamma) != 2 or abs(delay_gamma[0] - 1.1345) > 1e-6 or abs(delay_gamma[1] - 0.1285) > 1e-6:
            raise SnapshotContractError(
                "visibility_method.delay_prior gamma must match the Rosello BDBV shape-rate prior"
            )
        if "not a fitted 2026" not in method_caveat:
            raise SnapshotContractError(
                "visibility_method must caveat the Rosello prior as historical, not fitted to 2026"
            )
        sensitivity_text = " ".join(
            str(item.get("label", ""))
            for item in visibility_method.get("sensitivity_delay_priors") or []
            if isinstance(item, dict)
        ).lower()
        if "camacho" not in sensitivity_text or "sensitivity" not in sensitivity_text:
            raise SnapshotContractError(
                "visibility_method must retain Camacho as a named sensitivity comparator"
            )

    # Plan A 2026-05-28: scale-resilience and INSP-per-zone surface contracts
    # (spec §6.7, §5.1, §5.2). All four are optional; cross-field rules below.
    data_scale_used = contract.get("data_scale_used")
    if data_scale_used is not None:
        _validate_data_scale_used(data_scale_used)
    insp_block = contract.get("insp_per_zone_block")
    if insp_block is not None:
        _validate_insp_per_zone_block(insp_block)
    per_zone_bands = contract.get("per_zone_under_ascertainment_bands")
    if per_zone_bands is not None:
        _validate_per_zone_bands(per_zone_bands)
    attribution_lag = contract.get("attribution_lag_disclosure")
    if attribution_lag is not None:
        _validate_attribution_lag(attribution_lag)
    # Cross-field: scales that require an INSP block must carry one.
    if (
        data_scale_used in SCALES_REQUIRING_PER_ZONE_BLOCK
        and insp_block is None
    ):
        raise SnapshotContractError(
            f"data_scale_used={data_scale_used!r} requires an insp_per_zone_block "
            "to be present (spec §6.7 scale-resilience invariant)"
        )
    # Cross-field: if both surfaces are present, the bands surface must declare
    # the PCR modulator method_basis (no quiet method-basis substitution).
    if (
        insp_block is not None
        and per_zone_bands is not None
        and per_zone_bands.get("method_basis") != PCR_MODULATED_BANDS_METHOD_BASIS
    ):
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands.method_basis must be "
            f"{PCR_MODULATED_BANDS_METHOD_BASIS!r} when the modulator surface is "
            "present"
        )


def _validate_data_scale_used(value: Any) -> None:
    if value not in VALID_DATA_SCALES:
        raise SnapshotContractError(
            f"data_scale_used must be one of {VALID_DATA_SCALES!r}, got {value!r}"
        )


def _validate_insp_per_zone_block(block: dict[str, Any]) -> None:
    if block.get("method_basis") != INSP_PER_ZONE_METHOD_BASIS:
        raise SnapshotContractError(
            f"insp_per_zone_block.method_basis must be {INSP_PER_ZONE_METHOD_BASIS!r}"
        )
    as_of = str(block.get("as_of_data_date", ""))
    if len(as_of) != 10 or as_of[4] != "-" or as_of[7] != "-":
        raise SnapshotContractError(
            "insp_per_zone_block.as_of_data_date must be an ISO YYYY-MM-DD date"
        )
    source_id = str(block.get("source_id", ""))
    if "inrb-umie" not in source_id.lower():
        raise SnapshotContractError(
            "insp_per_zone_block.source_id must reference an INRB-UMIE consortium "
            f"release; got {source_id!r}"
        )
    # Reconciliation: sum(by_lovs_zone[metric]) + unallocated_residual[metric]
    # must equal national_at_data_date[metric] for every metric (spec §5.1).
    by_lovs_zone = block.get("by_lovs_zone") or {}
    national = block.get("national_at_data_date") or {}
    residual = block.get("unallocated_residual") or {}
    for metric in INSP_METRICS:
        zone_sum = sum(row.get(metric, 0) for row in by_lovs_zone.values())
        nat = national.get(metric, 0)
        res = residual.get(metric, 0)
        if zone_sum + res != nat:
            raise SnapshotContractError(
                f"insp_per_zone_block reconciliation violated for metric {metric!r}: "
                f"sum(by_lovs_zone)={zone_sum} + residual={res} != national={nat}"
            )
        if res < 0:
            raise SnapshotContractError(
                f"insp_per_zone_block.unallocated_residual.{metric}={res} must be >= 0"
            )


def _validate_per_zone_bands(bands: dict[str, Any]) -> None:
    if bands.get("method_basis") != PCR_MODULATED_BANDS_METHOD_BASIS:
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands.method_basis must be "
            f"{PCR_MODULATED_BANDS_METHOD_BASIS!r}"
        )
    surface_role = bands.get("surface_role")
    if surface_role not in VALID_PER_ZONE_SURFACE_ROLES:
        raise SnapshotContractError(
            f"per_zone_under_ascertainment_bands.surface_role must be one of "
            f"{VALID_PER_ZONE_SURFACE_ROLES!r}, got {surface_role!r}"
        )
    # R3 belt-and-suspenders (Rec J): until Plan C parallel-scoring landing,
    # only `shadow_in_v1` is permitted on a release branch. This duplicates the
    # `pcr_modulator_shadow_gate` so that a release attempt that bypasses the
    # gate still cannot mint a primary modulator surface via the contract.
    if surface_role != ALLOWED_PER_ZONE_BANDS_SURFACE_ROLE_THIS_CYCLE:
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands.surface_role must be "
            f"{ALLOWED_PER_ZONE_BANDS_SURFACE_ROLE_THIS_CYCLE!r} until Plan C "
            f"parallel-scoring landing; got {surface_role!r}"
        )
    default = bands.get("species_default_band") or {}
    lo = float(default.get("lo", 0.0))
    hi = float(default.get("hi", 0.0))
    if not (0.0 <= lo < hi <= 1.0):
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands.species_default_band must satisfy "
            f"0 <= lo < hi <= 1; got lo={lo}, hi={hi}"
        )
    for zone_id, row in (bands.get("by_lovs_zone") or {}).items():
        zone_lo = row.get("lo")
        zone_hi = row.get("hi")
        if zone_lo is None and zone_hi is None:
            continue  # species-default fallback
        if zone_lo is None or zone_hi is None:
            raise SnapshotContractError(
                f"per_zone_under_ascertainment_bands.by_lovs_zone.{zone_id}: "
                "lo and hi must be both null or both numeric"
            )
        if not (lo <= zone_lo < zone_hi <= hi):
            raise SnapshotContractError(
                f"per_zone_under_ascertainment_bands.by_lovs_zone.{zone_id} band "
                f"({zone_lo}, {zone_hi}) must satisfy "
                f"species_lo={lo} <= lo < hi <= species_hi={hi}"
            )


def _validate_attribution_lag(lag: dict[str, Any]) -> None:
    per_metric = lag.get("per_metric") or []
    metrics_seen: set[str] = set()
    for idx, row in enumerate(per_metric):
        for required in REQUIRED_ATTRIBUTION_LAG_METRIC_FIELDS:
            if required not in row:
                raise SnapshotContractError(
                    f"attribution_lag_disclosure.per_metric[{idx}] missing {required!r}"
                )
        metric = str(row.get("metric", ""))
        timeliness = str(row.get("timeliness", ""))
        if timeliness not in ATTRIBUTION_LAG_TIMELINESS_VOCABULARY:
            raise SnapshotContractError(
                f"attribution_lag_disclosure.per_metric[{idx}].timeliness must be "
                f"one of {ATTRIBUTION_LAG_TIMELINESS_VOCABULARY!r}, got {timeliness!r}"
            )
        share = float(row.get("share_attributed_to_zones", 0.0))
        if not (0.0 <= share <= 1.0):
            raise SnapshotContractError(
                f"attribution_lag_disclosure.per_metric[{idx}].share_attributed_to_zones "
                f"must be in [0, 1]; got {share}"
            )
        metrics_seen.add(metric)
    # All four INSP metrics must be disclosed when the attribution-lag surface
    # is present (no silent metric omissions).
    missing = sorted(set(INSP_METRICS) - metrics_seen)
    if missing:
        raise SnapshotContractError(
            f"attribution_lag_disclosure.per_metric is missing metrics {missing!r}"
        )
    # The narrative must surface the 1-3 week confirmed_deaths trailing note.
    narrative = str(lag.get("narrative", "")).lower()
    for term in REQUIRED_ATTRIBUTION_LAG_NARRATIVE_TERMS:
        if term.lower() not in narrative:
            raise SnapshotContractError(
                f"attribution_lag_disclosure.narrative must include {term!r}"
            )


def _project_insp_per_zone_block(block: Any) -> dict[str, Any]:
    """Capture only the fields the contract validates (spec §5.1)."""
    if not isinstance(block, dict):
        raise SnapshotContractError("insp_per_zone_block must be an object")
    by_lovs_zone_raw = block.get("by_lovs_zone") or {}
    if not isinstance(by_lovs_zone_raw, dict):
        raise SnapshotContractError("insp_per_zone_block.by_lovs_zone must be an object")
    by_lovs_zone: dict[str, dict[str, Any]] = {}
    for zone_id, row in sorted(by_lovs_zone_raw.items()):
        if not isinstance(row, dict):
            raise SnapshotContractError(
                f"insp_per_zone_block.by_lovs_zone.{zone_id} must be an object"
            )
        by_lovs_zone[zone_id] = {
            metric: _required_int(
                row, metric, f"insp_per_zone_block.by_lovs_zone.{zone_id}"
            )
            for metric in INSP_METRICS
        } | {
            "inrb_collapsed_from": list(row.get("inrb_collapsed_from") or []),
            "present_in_insp_classification": str(
                row.get("present_in_insp_classification", "")
            ),
        }
    national_raw = block.get("national_at_data_date") or {}
    if not isinstance(national_raw, dict):
        raise SnapshotContractError(
            "insp_per_zone_block.national_at_data_date must be an object"
        )
    national_at_data_date = {
        metric: _required_int(
            national_raw, metric, "insp_per_zone_block.national_at_data_date"
        )
        for metric in INSP_METRICS
    }
    residual_raw = block.get("unallocated_residual") or {}
    if not isinstance(residual_raw, dict):
        raise SnapshotContractError(
            "insp_per_zone_block.unallocated_residual must be an object"
        )
    unallocated_residual = {
        metric: _required_int(
            residual_raw, metric, "insp_per_zone_block.unallocated_residual"
        )
        for metric in INSP_METRICS
    }
    coverage_raw = block.get("coverage_audit") or {}
    if not isinstance(coverage_raw, dict):
        raise SnapshotContractError(
            "insp_per_zone_block.coverage_audit must be an object"
        )
    coverage_audit = {
        category: sorted(coverage_raw.get(category) or [])
        for category in ("present_with_data", "present_but_zero", "structurally_absent")
    }
    return {
        "as_of_data_date": str(block.get("as_of_data_date", ""))[:10],
        "source_id": _required_str(block, "source_id", "insp_per_zone_block"),
        "method_basis": str(block.get("method_basis", "")),
        "by_lovs_zone": by_lovs_zone,
        "national_at_data_date": national_at_data_date,
        "unallocated_residual": unallocated_residual,
        "coverage_audit": coverage_audit,
    }


def _project_per_zone_bands(bands: Any) -> dict[str, Any]:
    """Capture per-zone ascertainment bands plus surface_role (spec §5.2)."""
    if not isinstance(bands, dict):
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands must be an object"
        )
    by_zone_raw = bands.get("by_lovs_zone") or {}
    if not isinstance(by_zone_raw, dict):
        raise SnapshotContractError(
            "per_zone_under_ascertainment_bands.by_lovs_zone must be an object"
        )
    by_zone: dict[str, dict[str, Any]] = {}
    for zone_id, row in sorted(by_zone_raw.items()):
        if not isinstance(row, dict):
            raise SnapshotContractError(
                f"per_zone_under_ascertainment_bands.by_lovs_zone.{zone_id} must be an object"
            )
        lo = row.get("lo")
        hi = row.get("hi")
        if lo is None or hi is None:
            # `None` represents species-default fallback; both must be None.
            if lo is not None or hi is not None:
                raise SnapshotContractError(
                    f"per_zone_under_ascertainment_bands.by_lovs_zone.{zone_id}: "
                    "lo and hi must be both null (species fallback) or both numeric"
                )
            by_zone[zone_id] = {"lo": None, "hi": None, "fallback": "species_default"}
            continue
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise SnapshotContractError(
                f"per_zone_under_ascertainment_bands.by_lovs_zone.{zone_id}: "
                "lo and hi must be numeric or both null"
            )
        by_zone[zone_id] = {
            "lo": float(lo),
            "hi": float(hi),
            "fallback": "modulated",
        }
    return {
        "method_basis": str(bands.get("method_basis", "")),
        "surface_role": str(bands.get("surface_role", "")),
        "species_default_band": {
            "lo": float((bands.get("species_default_band") or {}).get("lo", 0.0)),
            "hi": float((bands.get("species_default_band") or {}).get("hi", 0.0)),
        },
        "by_lovs_zone": by_zone,
        "coverage_stats": {
            key: int((bands.get("coverage_stats") or {}).get(key, 0))
            for key in (
                "modulated_zones",
                "species_default_fallback_zones",
                "total_zones",
            )
        },
    }


def _project_attribution_lag(lag: Any) -> dict[str, Any]:
    """Capture per-metric attribution-lag disclosure (spec §2.3, §5.1)."""
    if not isinstance(lag, dict):
        raise SnapshotContractError("attribution_lag_disclosure must be an object")
    metrics_raw = lag.get("per_metric") or []
    if not isinstance(metrics_raw, list):
        raise SnapshotContractError(
            "attribution_lag_disclosure.per_metric must be a list"
        )
    per_metric: list[dict[str, Any]] = []
    for idx, row in enumerate(metrics_raw):
        if not isinstance(row, dict):
            raise SnapshotContractError(
                f"attribution_lag_disclosure.per_metric[{idx}] must be an object"
            )
        per_metric.append(
            {
                "metric": _required_str(
                    row, "metric", f"attribution_lag_disclosure.per_metric[{idx}]"
                ),
                "timeliness": _required_str(
                    row,
                    "timeliness",
                    f"attribution_lag_disclosure.per_metric[{idx}]",
                ),
                "share_attributed_to_zones": _required_number(
                    row,
                    "share_attributed_to_zones",
                    f"attribution_lag_disclosure.per_metric[{idx}]",
                ),
            }
        )
    return {
        "per_metric": per_metric,
        "narrative": str(lag.get("narrative", "")),
    }


def validate_snapshot(snapshot: dict[str, Any], contract: dict[str, Any] | None = None) -> None:
    generated = build_contract(snapshot)
    if contract is not None and contract != generated:
        raise SnapshotContractError("data/snapshot_contract.json is stale relative to live snapshot")
    validate_contract(generated)

    zone_counts = generated["zone_attributed_counts"]
    corridor_sources = set(generated["corridor_watchlist"]["source_zones"])
    zone_ids = set(zone_counts)
    # A zone generates corridors iff it carries confirmed cases. Zero-confirmed
    # zones have no observed transmission source, so they are retained in the
    # attribution table (and on the map) for surveillance presence but emit no
    # corridor. Corridor sources must therefore equal exactly the
    # confirmed-carrying subset of the attribution table: never a non-attributed
    # zone (extra), never a confirmed-carrying zone that failed to emit (missing).
    confirmed_sources = {
        zone_id
        for zone_id in zone_ids
        if int(zone_counts[zone_id].get("confirmed", 0)) > 0
    }
    if zone_ids and corridor_sources != confirmed_sources:
        raise SnapshotContractError(
            "corridor source zones must equal the confirmed-carrying zones in "
            "zone_attributed_counts: "
            f"missing={sorted(confirmed_sources - corridor_sources)}, "
            f"extra={sorted(corridor_sources - confirmed_sources)}"
        )
    affected = set(snapshot.get("affected_zones") or [])
    if zone_ids and affected != zone_ids:
        raise SnapshotContractError(
            "affected_zones must equal zone_attributed_counts when a per-zone table is present"
        )

    targets = set(generated["corridor_watchlist"]["target_zones"])
    by_source: dict[str, set[str]] = {zone_id: set() for zone_id in zone_ids}
    for idx, corridor in enumerate(snapshot.get("corridors") or []):
        source = str(corridor.get("source", ""))
        target = str(corridor.get("target", ""))
        if source in by_source:
            by_source[source].add(target)
            expected = f"zone-attributed confirmed count {zone_counts[source]['confirmed']}"
            drivers = " ".join(str(d) for d in corridor.get("drivers") or [])
            if expected not in drivers:
                raise SnapshotContractError(
                    f"corridors[{idx}] {source}->{target} lacks source-load driver {expected!r}"
                )
            if "headline confirmed" in drivers.lower():
                raise SnapshotContractError(
                    f"corridors[{idx}] {source}->{target} appears to use headline aggregate"
                )
    for source, seen_targets in sorted(by_source.items()):
        # Zero-confirmed zones emit no corridor (no observed transmission source).
        if source not in confirmed_sources:
            if seen_targets:
                raise SnapshotContractError(
                    f"zero-confirmed zone {source} must emit no corridor, "
                    f"got targets {sorted(seen_targets)}"
                )
            continue
        expected_targets = targets - {source} if source in targets else targets
        if seen_targets != expected_targets:
            raise SnapshotContractError(
                f"source zone {source} has target set {sorted(seen_targets)}, expected {sorted(expected_targets)}"
            )


def _visibility_method_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    visibility = snapshot.get("visibility") or {}
    if not isinstance(visibility, dict):
        raise SnapshotContractError("visibility must be an object")
    delay_prior = visibility.get("delay_prior") or {}
    sensitivity_priors = visibility.get("sensitivity_delay_priors") or []
    return {
        "history_snapshot_count": _optional_int(visibility, "history_snapshot_count") or 0,
        "method_basis": str(visibility.get("method_basis", "")),
        "method_caveat": str(visibility.get("method_caveat", "")),
        "delay_prior": {
            "label": str(delay_prior.get("label", "")),
            "gamma_shape_rate": list(delay_prior.get("gamma_shape_rate") or []),
            "evidence_chain_id": str(delay_prior.get("evidence_chain_id", "")),
        },
        "sensitivity_delay_priors": [
            {
                "label": str(item.get("label", "")),
                "gamma_shape_rate": list(item.get("gamma_shape_rate") or []),
                "evidence_chain_id": str(item.get("evidence_chain_id", "")),
            }
            for item in sensitivity_priors
            if isinstance(item, dict)
        ],
    }


def validate_narrative(text: str, contract: dict[str, Any], label: str = "narrative") -> None:
    required = contract["narrative_required_fragments"]["headline_zone_unallocated"]
    missing = [fragment for fragment in required if fragment.lower() not in text.lower()]
    if missing:
        raise SnapshotContractError(f"{label} is stale or incomplete; missing {missing}")

    upper_max = contract["corridor_watchlist"]["adjusted_50_upper_range_pct"][1]
    if upper_max < 60.0:
        stale_needles = ("69.5%", "69.2%", "68.4%", "67.6%", "65.2%", "64.7% to 69.5%")
        present = [needle for needle in stale_needles if needle in text]
        if present:
            raise SnapshotContractError(f"{label} contains stale high-corridor values: {present}")
    disallowed_claims = (
        "corridor deployment ranking",
        "deployment ranking",
        "predicts where the outbreak will spread",
    )
    lower_text = text.lower()
    for claim in disallowed_claims:
        if claim in lower_text and "not " + claim not in lower_text:
            raise SnapshotContractError(
                f"{label} contains overclaiming corridor language: {claim!r}"
            )


def validate_visibility_prior_attribution(
    text: str, contract: dict[str, Any], label: str = "narrative"
) -> None:
    """Guard the reporting-delay prior attribution in human-facing prose.

    When the snapshot runs a BDBV-specific default (Rosello), any narrative that
    discusses the reporting delay must name that default and must not present a
    sensitivity comparator (Camacho) or a superseded historical distribution as
    the default.  This is the class that the numeric narrative gate misses: the
    completeness *number* is correct but its *attribution* is stale.
    """
    visibility = contract.get("visibility_method") or {}
    if "bdbv_specific" not in str(visibility.get("method_basis", "")).lower():
        return

    lower_text = text.lower()
    delay_terms = (
        "onset-to-notification",
        "reporting delay",
        "reporting-delay",
        "reporting completeness",
        "reporting-completeness",
        "delay distribution",
    )
    if not any(term in lower_text for term in delay_terms):
        return

    default_label = str((visibility.get("delay_prior") or {}).get("label", ""))
    default_name = default_label.split()[0] if default_label else ""
    if default_name and default_name.lower() not in lower_text:
        raise SnapshotContractError(
            f"{label} discusses the reporting delay but does not name the current "
            f"default prior ({default_name})"
        )

    for prior in visibility.get("sensitivity_delay_priors") or []:
        sens_label = str(prior.get("label", "")) if isinstance(prior, dict) else ""
        sens_name = sens_label.split()[0] if sens_label else ""
        if sens_name and f"delay ({sens_name.lower()}" in lower_text:
            raise SnapshotContractError(
                f"{label} frames the sensitivity comparator {sens_name} as the "
                f"reporting-delay default"
            )

    stale_attributions = (
        "assumed 2014 west-africa delay",
        "2014 west-africa delay distribution",
        "delay distribution drawn from 2014 west africa",
    )
    present = [phrase for phrase in stale_attributions if phrase in lower_text]
    if present:
        raise SnapshotContractError(
            f"{label} carries stale reporting-delay attribution: {present}"
        )


def validate_text_artifacts(contract: dict[str, Any], repo_root: pathlib.Path = REPO_ROOT) -> None:
    """Gate the primary human-facing narrative surfaces.

    This is intentionally narrower than a full editorial pass.  It catches the
    dangerous contradiction class: public prose omitting the headline-vs-zone
    count partition, carrying stale corridor ranges, or attributing the
    reporting-delay prior to a superseded default.
    """
    paths = (
        repo_root / "README.md",
        repo_root / "NUMBERS_AUDIT.md",
        repo_root / "brief" / "brief.html",
    )
    for path in paths:
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            validate_narrative(content, contract, str(path))
            validate_visibility_prior_attribution(content, contract, str(path))
            validate_insp_per_zone_narrative(content, contract, str(path))


def validate_insp_per_zone_narrative(
    text: str, contract: dict[str, Any], label: str = "narrative"
) -> None:
    """Plan A 2026-05-28: enforce INSP per-zone narrative coverage when the
    surface is present in the contract.

    Required fragments on every narrative surface (README, NUMBERS_AUDIT,
    brief) once the INSP per-zone surface is in the contract:

    - The phrase 'INSP per-health-zone' (or 'INRB-UMIE INSP') anchoring the
      source vector.
    - The phrase '1-3 week' attached to the confirmed-deaths attribution lag.

    This complements `_validate_attribution_lag` (which checks the snapshot
    JSON) by enforcing the same disclosure on public prose.
    """
    if "insp_per_zone_block" not in contract:
        return
    lower = text.lower()
    insp_anchors = ("insp per-health-zone", "inrb-umie insp", "insp per-zone")
    if not any(anchor in lower for anchor in insp_anchors):
        raise SnapshotContractError(
            f"{label} carries an INSP per-zone surface in the contract but does "
            f"not anchor it with one of {insp_anchors!r}"
        )
    if "attribution_lag_disclosure" in contract and "1-3 week" not in lower:
        raise SnapshotContractError(
            f"{label} carries an attribution_lag_disclosure but does not surface "
            "the '1-3 week' confirmed_deaths trailing note"
        )


def validate_dataset_exports(
    contract: dict[str, Any],
    dataset_dir: pathlib.Path = DEFAULT_DATASET_DIR,
) -> None:
    reported_rows = _read_csv(dataset_dir / "reported_counts.csv")
    snapshot_rows = {
        row["row_id"].rsplit(":", 1)[-1]: row
        for row in reported_rows
        if row.get("row_id", "").startswith("snapshot:reported_counts:")
    }
    for metric, expected in contract["reported_counts"].items():
        row = snapshot_rows.get(metric)
        if not row:
            raise SnapshotContractError(f"reported_counts.csv lacks snapshot row for {metric}")
        if int(float(row["value"])) != int(expected["primary"]):
            raise SnapshotContractError(
                f"reported_counts.csv {metric}={row['value']} but contract has {expected['primary']}"
            )
        # The CSV stores manifest source_id verbatim (some carry a "-live"
        # suffix for live captures); the contract carries the canonical id
        # because the snapshot pipeline canonicalizes. The canonicalisation
        # lives in lovs.source_ids so this gate stays in sync with the
        # manifest-lookup rule in publication_clock_contract.
        if not source_ids_match(row.get("source_id"), expected.get("primary_source_id")):
            raise SnapshotContractError(
                f"reported_counts.csv {metric} source_id={row.get('source_id')!r} "
                f"but contract has {expected.get('primary_source_id')!r}"
            )

    for row in reported_rows:
        if row.get("row_type") != "source_extracted_metric":
            continue
        row_id = row.get("row_id", "")
        metric = row.get("metric", "")
        if ":deaths" in row_id and metric != "deaths":
            raise SnapshotContractError(
                f"{row_id} is a death source metric but exported as {metric!r}"
            )

    corridor_rows = _read_csv(dataset_dir / "corridors.csv")
    watch = contract["corridor_watchlist"]
    if len(corridor_rows) != watch["corridor_count"]:
        raise SnapshotContractError(
            f"corridors.csv has {len(corridor_rows)} rows but contract has {watch['corridor_count']}"
        )
    lower = [float(row["risk_adj_lower_50"]) * 100 for row in corridor_rows]
    upper = [float(row["risk_adj_upper_50"]) * 100 for row in corridor_rows]
    if [_pct(min(lower) / 100), _pct(max(lower) / 100)] != watch["adjusted_50_lower_range_pct"]:
        raise SnapshotContractError("corridors.csv lower 50% range disagrees with contract")
    if [_pct(min(upper) / 100), _pct(max(upper) / 100)] != watch["adjusted_50_upper_range_pct"]:
        raise SnapshotContractError("corridors.csv upper 50% range disagrees with contract")
    for row in corridor_rows:
        source = row.get("source", "")
        zone = contract["zone_attributed_counts"].get(source)
        if zone:
            expected_driver = f"zone-attributed confirmed count {zone['confirmed']}"
            if expected_driver not in row.get("drivers", ""):
                raise SnapshotContractError(
                    f"corridors.csv {source}->{row.get('target')} lacks {expected_driver!r}"
                )
        note = row.get("correction_note", "").lower()
        if "not a forecast" not in note or "not a forecast or response recommendation" not in note:
            raise SnapshotContractError(
                f"corridors.csv {source}->{row.get('target')} does not disclose watchlist limits"
            )

    claim_rows = _read_csv(dataset_dir / "public_claim_audit.csv")
    claim_by_id = {row.get("public_claim_id"): row for row in claim_rows}
    zone_claim = claim_by_id.get("BDBV-CLAIM-018")
    if not zone_claim:
        raise SnapshotContractError("public_claim_audit.csv lacks BDBV-CLAIM-018")
    zone_claim_text = " ".join(
        zone_claim.get(key, "")
        for key in ("claim", "value", "public_action", "public_note")
    ).lower()
    zone_partition = contract["confirmed_case_partition"]
    corridor_watchlist = contract["corridor_watchlist"]
    required_terms = {
        str(zone_partition["headline_confirmed_total"]),
        str(zone_partition["zone_attributed_confirmed_total"]),
        str(zone_partition["unallocated_confirmed_total"]),
        "unallocated",
        "not the current headline confirmed aggregate",
        str(corridor_watchlist["corridor_count"]),
        "corridor",
    }
    for required_term in required_terms:
        if required_term not in zone_claim_text:
            raise SnapshotContractError(
                f"BDBV-CLAIM-018 does not preserve source-load partition term {required_term!r}"
            )

    gap_rows = _read_csv(dataset_dir / "corrections_gaps.csv")
    gaps_by_id = {row.get("gap_id"): row for row in gap_rows}
    exponent_gap = gaps_by_id.get("BDBV-CLAIM-005")
    if not exponent_gap:
        raise SnapshotContractError("corrections_gaps.csv lacks BDBV-CLAIM-005")
    gap_text = " ".join(
        exponent_gap.get(key, "")
        for key in ("status", "public_action", "note", "topic")
    ).lower()
    for required in ("unsupported attribution", "not fitted", "heuristic"):
        if required not in gap_text:
            raise SnapshotContractError(
                f"BDBV-CLAIM-005 does not disclose corridor-constant limitation {required!r}"
            )

    # Plan A 2026-05-28: when the INSP per-zone surface is present in the
    # contract, the public dataset must carry the three new CSVs (spec §4.3).
    if "insp_per_zone_block" in contract:
        _validate_insp_per_zone_dataset_csvs(contract, dataset_dir)


def _validate_insp_per_zone_dataset_csvs(
    contract: dict[str, Any], dataset_dir: pathlib.Path
) -> None:
    """Plan A 2026-05-28: contract on the 3 new public CSVs (spec §4.3).

    `per-zone_snapshot.csv`: one row per LOVS zone with the 4 INSP metrics +
    INRB canonical Nom + `present_in_insp_classification` + `inrb_collapsed_from`.

    `reconciliation_residuals.csv`: one row per metric with national,
    sum-of-per-zone-attributed, and unallocated_residual.

    `attribution_lag_disclosure.csv`: one row per metric with timeliness and
    share_attributed_to_zones.
    """
    insp_block = contract["insp_per_zone_block"]
    per_zone_rows = _read_csv(dataset_dir / "per-zone_snapshot.csv")
    per_zone_by_zone = {row.get("lovs_zone_id", ""): row for row in per_zone_rows}
    expected_zones = set(insp_block["by_lovs_zone"])
    seen_zones = set(per_zone_by_zone) - {""}
    if seen_zones != expected_zones:
        raise SnapshotContractError(
            "per-zone_snapshot.csv zone set does not match contract: "
            f"missing={sorted(expected_zones - seen_zones)}, "
            f"extra={sorted(seen_zones - expected_zones)}"
        )
    for zone_id, expected in insp_block["by_lovs_zone"].items():
        row = per_zone_by_zone[zone_id]
        for metric in INSP_METRICS:
            csv_value = row.get(metric, "")
            try:
                csv_int = int(float(csv_value))
            except ValueError as exc:
                raise SnapshotContractError(
                    f"per-zone_snapshot.csv {zone_id}.{metric}={csv_value!r} not an int"
                ) from exc
            if csv_int != expected[metric]:
                raise SnapshotContractError(
                    f"per-zone_snapshot.csv {zone_id}.{metric}={csv_int} disagrees "
                    f"with contract {expected[metric]}"
                )

    residual_rows = _read_csv(dataset_dir / "reconciliation_residuals.csv")
    residual_by_metric = {row.get("metric", ""): row for row in residual_rows}
    for metric in INSP_METRICS:
        row = residual_by_metric.get(metric)
        if not row:
            raise SnapshotContractError(
                f"reconciliation_residuals.csv missing metric {metric!r}"
            )
        for field, value_source in (
            (
                "national_at_data_date",
                insp_block["national_at_data_date"][metric],
            ),
            (
                "unallocated_residual",
                insp_block["unallocated_residual"][metric],
            ),
        ):
            csv_value = row.get(field, "")
            try:
                csv_int = int(float(csv_value))
            except ValueError as exc:
                raise SnapshotContractError(
                    f"reconciliation_residuals.csv {metric}.{field}={csv_value!r} not an int"
                ) from exc
            if csv_int != value_source:
                raise SnapshotContractError(
                    f"reconciliation_residuals.csv {metric}.{field}={csv_int} "
                    f"disagrees with contract {value_source}"
                )

    if "attribution_lag_disclosure" in contract:
        lag_rows = _read_csv(dataset_dir / "attribution_lag_disclosure.csv")
        lag_by_metric = {row.get("metric", ""): row for row in lag_rows}
        for entry in contract["attribution_lag_disclosure"]["per_metric"]:
            metric = entry["metric"]
            row = lag_by_metric.get(metric)
            if not row:
                raise SnapshotContractError(
                    f"attribution_lag_disclosure.csv missing metric {metric!r}"
                )
            if row.get("timeliness") != entry["timeliness"]:
                raise SnapshotContractError(
                    f"attribution_lag_disclosure.csv {metric}.timeliness="
                    f"{row.get('timeliness')!r} disagrees with contract "
                    f"{entry['timeliness']!r}"
                )


def narrative_required_fragments_from_values(
    *,
    confirmed_headline: int,
    zone_confirmed: int,
    unallocated: int,
    source_zone_count: int,
    source_zone_label: str = "official source zones",
    corridor_count: int,
    lower_range_pct: tuple[float, float],
    upper_range_pct: tuple[float, float],
) -> list[str]:
    return [
        f"{confirmed_headline} confirmed cases",
        f"{zone_confirmed} confirmed cases",
        f"{unallocated} confirmed cases",
        "officially zone-attributed",
        "source-attribution lag",
        "unallocated",
        f"{source_zone_count} {source_zone_label}",
        f"{corridor_count}-corridor watchlist",
        f"{lower_range_pct[0]:.1f}-{lower_range_pct[1]:.1f}% lower",
        f"{upper_range_pct[0]:.1f}-{upper_range_pct[1]:.1f}% upper",
    ]


def _source_zone_label(source_ids: list[str]) -> str:
    if source_ids and all(
        source_id.startswith("drc-moh-epidemie-dashboard") for source_id in source_ids
    ):
        return "DRC MoH source zones"
    if source_ids and all(source_id.startswith("afro-sitrep") for source_id in source_ids):
        return "WHO AFRO source zones"
    if source_ids and all(source_id.startswith("inrb-umie") for source_id in source_ids):
        return "INSP per-zone source zones"
    return "official source zones"


def _read_csv(path: pathlib.Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SnapshotContractError(f"{path} is missing")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _pct(value: float) -> float:
    return round(float(value) * 100.0, 1)


def _required_int(row: dict[str, Any], key: str, path: str) -> int:
    value = row.get(key)
    if not isinstance(value, int):
        raise SnapshotContractError(f"{path}.{key} must be an int, got {value!r}")
    return value


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise SnapshotContractError(f"{key} must be an int when present, got {value!r}")
    return value


def _required_number(row: dict[str, Any], key: str, path: str) -> float:
    value = row.get(key)
    if not isinstance(value, (int, float)):
        raise SnapshotContractError(f"{path}.{key} must be numeric, got {value!r}")
    return float(value)


def _required_str(row: dict[str, Any], key: str, path: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotContractError(f"{path}.{key} must be a non-empty string")
    return value


def _range_pair(row: dict[str, Any], key: str) -> tuple[float, float]:
    value = row.get(key)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, (int, float)) for v in value)
        or value[0] > value[1]
    ):
        raise SnapshotContractError(f"{key} must be a numeric [min, max] pair")
    return float(value[0]), float(value[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=pathlib.Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--contract", type=pathlib.Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--dataset-dir", type=pathlib.Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--write", action="store_true", help="Write the generated contract JSON.")
    parser.add_argument("--check-text", action="store_true", help="Validate README, NUMBERS_AUDIT, and brief narrative.")
    parser.add_argument("--check-dataset", action="store_true", help="Validate public dataset CSVs against the contract.")
    args = parser.parse_args(argv)

    snapshot = load_json(args.snapshot)
    contract = build_contract(snapshot)
    if args.write:
        args.contract.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"snapshot_contract={args.contract}")
    elif args.contract.exists():
        validate_snapshot(snapshot, load_json(args.contract))
    else:
        validate_snapshot(snapshot)

    if args.check_text:
        validate_text_artifacts(contract)
    if args.check_dataset:
        validate_dataset_exports(contract, args.dataset_dir)
    print("snapshot contract ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
