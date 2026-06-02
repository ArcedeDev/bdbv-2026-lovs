# SPDX-License-Identifier: Apache-2.0
"""Scale-resilience assembler for the snapshot's INSP per-zone surfaces.

Produces the dict-shape `data_scale_used`, `insp_per_zone_block`,
`per_zone_under_ascertainment_bands`, and `attribution_lag_disclosure` fields
that `refresh_pipeline.main()` merges into the live snapshot. The function is
the single load-bearing decision for spec §6.7 scale-resilience: it picks one
of {`per_zone`, `partial_per_zone`, `national`, `mixed_with_metric_floor`}
based ONLY on what the INRB-UMIE artifact actually carries at the requested
as_of, and then assembles the snapshot-shape fields consistent with that
choice.

Scale paths:

- `national`: no INRB-UMIE artifact supplied (None or missing path), or the
  artifact's per-zone tables cannot be read. INSP block + bands omitted; an
  attribution-lag disclosure is emitted at the national-rollup-only band
  because the snapshot still discloses *that* it has no per-zone surface.
- `per_zone`: every LOVS source zone is present (with data OR zero) for every
  metric. INSP block + bands populated.
- `partial_per_zone`: at least one LOVS zone is `structurally_absent` from
  every metric table, but no metric-asymmetric zone exists. INSP block + bands
  populated.
- `mixed_with_metric_floor`: at least one LOVS zone is present in some metric
  tables and absent from others (real instance: Komanda at 2026-05-26 carries
  1 confirmed_death but 0 confirmed and 0 suspected). INSP block + bands
  populated; the `present_in_insp_classification` field on `by_lovs_zone`
  rows carries the LOVS-zone level audit category.

Stdlib only. No clock, no network. Pure function of inputs.
"""
from __future__ import annotations

import pathlib
from datetime import date
from typing import Any

from lovs.insp_per_zone_loader import (
    INSPLoaderError,
    INSPPerZoneSnapshot,
    METRICS,
    load_per_zone_snapshot,
)
from lovs.pcr_capacity_prior_modulator import (
    PCRModulatorError,
    SPECIES_HI,
    SPECIES_LO,
    coverage_stats,
    load_pcr_capacity_table,
    modulate_per_zone,
)
from lovs.zone_alias_bridge import ZoneAliasBridge


PCR_MODULATED_BANDS_METHOD_BASIS = "africa_cdc_pcr_capacity_modulated_v1"


def assemble_insp_artifacts(
    artifact_path: pathlib.Path | None,
    as_of: date,
    *,
    bridge: ZoneAliasBridge | None = None,
    source_id: str | None = None,
    revision_capped_metrics: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return the snapshot-shape INSP surfaces.

    Output keys (always present, may be None):
    - data_scale_used (str)
    - insp_per_zone_block (dict | None)
    - per_zone_under_ascertainment_bands (dict | None)
    - attribution_lag_disclosure (dict)
    """
    if bridge is None:
        bridge = ZoneAliasBridge.load_default()

    # Path 1: no artifact, or artifact missing on disk.
    if artifact_path is None or not artifact_path.exists():
        return _national_fallback()

    # Path 2: artifact exists but per-zone tables cannot be loaded.
    try:
        snap = load_per_zone_snapshot(
            artifact_path,
            as_of,
            bridge=bridge,
            source_id=source_id,
            revision_capped_metrics=revision_capped_metrics,
        )
    except INSPLoaderError:
        return _national_fallback()

    # Choose the scale from the loader's metric presence vs LOVS bridge.
    scale = _decide_scale(snap, bridge)

    insp_block = _build_insp_block(snap)

    # Build per-zone bands when the PCR capacity table is loadable.
    bands = None
    try:
        pcr_table = load_pcr_capacity_table(artifact_path)
        modulated = modulate_per_zone(snap, pcr_table, bridge=bridge)
        bands = _build_bands(snap, modulated)
    except PCRModulatorError:
        # PCR tables missing or malformed: bands stay None; consumers fall
        # back to the species-default band (spec §2.4 zero-signal doctrine).
        bands = _build_bands(snap, {lovs_id: None for lovs_id in snap.by_lovs_zone})

    lag = _build_attribution_lag(snap)

    return {
        "data_scale_used": scale,
        "insp_per_zone_block": insp_block,
        "per_zone_under_ascertainment_bands": bands,
        "attribution_lag_disclosure": lag,
    }


def _national_fallback() -> dict[str, Any]:
    return {
        "data_scale_used": "national",
        "insp_per_zone_block": None,
        "per_zone_under_ascertainment_bands": None,
        "attribution_lag_disclosure": {
            "per_metric": [
                {
                    "metric": "confirmed",
                    "timeliness": "near_timely",
                    "share_attributed_to_zones": 0.0,
                },
                {
                    "metric": "suspected",
                    "timeliness": "timely",
                    "share_attributed_to_zones": 0.0,
                },
                {
                    "metric": "confirmed_deaths",
                    "timeliness": "trailing",
                    "share_attributed_to_zones": 0.0,
                },
                {
                    "metric": "suspected_deaths",
                    "timeliness": "timely",
                    "share_attributed_to_zones": 0.0,
                },
            ],
            "narrative": (
                "Snapshot has no per-zone INSP surface this cycle; counts "
                "are at the national rollup only. Confirmed deaths still "
                "trail by 1-3 weeks while the INRB clinical review queue "
                "catches up."
            ),
        },
    }


def _decide_scale(snap: INSPPerZoneSnapshot, bridge: ZoneAliasBridge) -> str:
    """Decide the data_scale_used value from loader presence (spec §6.7).

    Returns one of: `per_zone`, `partial_per_zone`, `mixed_with_metric_floor`.
    `national` is returned only by the upstream fallback paths.
    """
    metric_set = set(METRICS)
    structurally_absent = {
        lovs_id
        for lovs_id in bridge.all_lovs_ids()
        if not snap.metric_presence.get(lovs_id, frozenset())
    }
    metric_asymmetric = any(
        0 < len(presence) < len(metric_set)
        for presence in snap.metric_presence.values()
    )
    if metric_asymmetric:
        return "mixed_with_metric_floor"
    if structurally_absent:
        return "partial_per_zone"
    return "per_zone"


def _build_insp_block(snap: INSPPerZoneSnapshot) -> dict[str, Any]:
    """Translate INSPPerZoneSnapshot into the snapshot-shape contract block.

    The residual is RECOMPUTED here per the spec invariant (spec §5.1):
    `sum(by_lovs_zone[zone][metric]) + unallocated_residual[metric] ==
    national[metric]`. The loader's residual is defined relative to the full
    INRB-side sum (which includes zones the bridge does not cover); the
    snapshot's residual is defined relative to the LOVS-bridged sum so the
    invariant is testable on the snapshot alone.
    """
    audit = snap.coverage_audit
    classification: dict[str, str] = {}
    for zone_id in audit.present_with_data:
        classification[zone_id] = "present_with_data"
    for zone_id in audit.present_but_zero:
        classification[zone_id] = "present_but_zero"
    for zone_id in audit.structurally_absent:
        classification[zone_id] = "structurally_absent"

    by_lovs_zone: dict[str, Any] = {}
    for lovs_id, zm in snap.by_lovs_zone.items():
        by_lovs_zone[lovs_id] = {
            "confirmed": zm.confirmed,
            "suspected": zm.suspected,
            "confirmed_deaths": zm.confirmed_deaths,
            "suspected_deaths": zm.suspected_deaths,
            "inrb_collapsed_from": list(zm.inrb_collapsed_from),
            "present_in_insp_classification": classification.get(
                lovs_id, "structurally_absent"
            ),
        }

    residual: dict[str, int] = {}
    for metric in METRICS:
        nat = snap.national.get(metric)
        zone_sum = sum(zm.get(metric) for zm in snap.by_lovs_zone.values())
        residual[metric] = nat - zone_sum

    return {
        "as_of_data_date": snap.as_of.isoformat(),
        "source_id": snap.source_id,
        "method_basis": snap.method_basis,
        "by_lovs_zone": by_lovs_zone,
        "national_at_data_date": {
            "confirmed": snap.national.confirmed,
            "suspected": snap.national.suspected,
            "confirmed_deaths": snap.national.confirmed_deaths,
            "suspected_deaths": snap.national.suspected_deaths,
        },
        "unallocated_residual": residual,
        "revision_capped_metrics": sorted(snap.revision_capped_metrics),
        "coverage_audit": {
            "present_with_data": list(audit.present_with_data),
            "present_but_zero": list(audit.present_but_zero),
            "structurally_absent": list(audit.structurally_absent),
        },
    }


def _build_bands(
    snap: INSPPerZoneSnapshot,
    modulated: dict[str, tuple[float, float] | None],
) -> dict[str, Any]:
    by_lovs_zone: dict[str, Any] = {}
    for lovs_id in sorted(snap.by_lovs_zone):
        band = modulated.get(lovs_id)
        if band is None:
            by_lovs_zone[lovs_id] = {"lo": None, "hi": None}
        else:
            by_lovs_zone[lovs_id] = {"lo": band[0], "hi": band[1]}
    stats = coverage_stats(modulated)
    return {
        "method_basis": PCR_MODULATED_BANDS_METHOD_BASIS,
        "surface_role": "shadow_in_v1",
        "species_default_band": {"lo": SPECIES_LO, "hi": SPECIES_HI},
        "by_lovs_zone": by_lovs_zone,
        "coverage_stats": {
            "modulated_zones": stats["modulated_zones"],
            "species_default_fallback_zones": stats["species_default_fallback_zones"],
            "total_zones": stats["total_zones"],
        },
    }


def _build_attribution_lag(snap: INSPPerZoneSnapshot) -> dict[str, Any]:
    """Compute per-metric attribution-share + canonical timeliness band.

    Timeliness vocabulary mirrors spec §2.3 attribution-lag hierarchy. The
    share is the proportion of national that is zone-attributed at this
    snapshot.
    """
    per_metric: list[dict[str, Any]] = []
    timeliness_map = {
        "confirmed": "near_timely",
        "suspected": "timely",
        "confirmed_deaths": "trailing",
        "suspected_deaths": "timely",
    }
    for metric in METRICS:
        nat = snap.national.get(metric)
        zone_sum = sum(zm.get(metric) for zm in snap.by_lovs_zone.values())
        share = (float(zone_sum) / float(nat)) if nat > 0 else 0.0
        per_metric.append(
            {
                "metric": metric,
                "timeliness": timeliness_map[metric],
                "share_attributed_to_zones": round(share, 4),
            }
        )
    return {
        "per_metric": per_metric,
        "narrative": (
            "Confirmed deaths trail the national rollup by 1-3 weeks while "
            "the INRB clinical review queue catches up; confirmed and "
            "suspected case attribution are near-timely to timely respectively."
        ),
    }
