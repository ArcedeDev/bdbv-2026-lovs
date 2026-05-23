#!/usr/bin/env python3
"""Sync the BDBV-2026 brief outputs into the Arcede website.

Reads the most-recent pipeline output (``data/live-bdbv-2026-output.json``),
combines it with the source provenance manifest, builds a website-shaped
snapshot JSON, writes it under the website's ``_data/snapshots/`` dir,
and copies the SVG assets and PDF into ``public/bdbv-2026/``.

Also updates the website's ``snapshots/index.ts`` to register the new
snapshot date (idempotent, safe to re-run).

Default website root is the sibling location used in the Arcede workspace:

    {brief_repo}/../../website/arcede-site/apps/site

Override with ``--website-root /path/to/apps/site`` if your layout differs.

Usage::

    python sync_to_website.py
    python sync_to_website.py --website-root /path/to/apps/site

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys
from typing import Any

from lovs import lovs_death_back_projection as dbp
from lovs import lovs_onset_to_death as otd
from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DEFAULT_WEBSITE_ROOT = (
    REPO_ROOT.parent.parent / "website" / "arcede-site" / "apps" / "site"
).resolve()


def load_pipeline_output(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Pipeline output not found at {path}. Run refresh_pipeline.py first."
        )
    return json.loads(path.read_text())


def load_archive_manifest(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def derive_as_of_date(pipeline_output: dict[str, Any]) -> str:
    """Return the date portion (yyyy-mm-dd) of the as_of timestamp."""
    as_of = pipeline_output.get("as_of")
    if not as_of:
        raise ValueError("pipeline output missing as_of field")
    return as_of.split("T", 1)[0]


def _unique_source_ids(ids: list[str | None]) -> list[str]:
    out: list[str] = []
    for source_id in ids:
        if source_id and source_id not in out:
            out.append(source_id)
    return out


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _parse_date_midnight(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value[:10]).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def build_source_latency_payload(
    manifest: dict[str, Any],
    snapshot_date: str,
) -> dict[str, Any]:
    """Build the website data-latency panel directly from manifest timestamps."""
    rows: list[dict[str, Any]] = []
    for entry in manifest.get("entries", []):
        normalized = entry.get("normalized_content") or {}
        data_as_of = normalized.get("data_as_of") or normalized.get("as_of_date")
        published_at = _parse_datetime(entry.get("published_at"))
        retrieved_at = _parse_datetime(entry.get("retrieved_at"))
        data_at = _parse_date_midnight(data_as_of)

        publication_lag = None
        if data_at is not None and published_at is not None:
            publication_lag = (published_at.date() - data_at.date()).days

        archival_lag = None
        if published_at is not None and retrieved_at is not None:
            archival_lag = round((retrieved_at - published_at).total_seconds() / 86400, 2)

        total_lag = None
        if data_at is not None and retrieved_at is not None:
            total_lag = round((retrieved_at - data_at).total_seconds() / 86400, 2)

        rows.append({
            "sourceId": canonical_source_id(entry.get("source_id", "")),
            "publisher": entry.get("publisher", ""),
            "sourceTier": entry.get("source_tier", ""),
            "dataAsOf": data_as_of or None,
            "publicationLagDays": publication_lag,
            "archivalLagDays": archival_lag,
            "totalVisibilityLagDays": total_lag,
        })

    rows.sort(
        key=lambda row: (
            row["totalVisibilityLagDays"] is None,
            -(row["totalVisibilityLagDays"] or row["archivalLagDays"] or -1),
            row["sourceId"],
        )
    )
    with_data = [row for row in rows if row["dataAsOf"]]
    publication_lags = [
        float(row["publicationLagDays"])
        for row in with_data
        if row["publicationLagDays"] is not None
    ]
    archival_lags = [
        float(row["archivalLagDays"])
        for row in rows
        if row["archivalLagDays"] is not None
    ]
    total_lags = [
        float(row["totalVisibilityLagDays"])
        for row in with_data
        if row["totalVisibilityLagDays"] is not None
    ]
    return {
        "latencySummary": {
            "snapshot": f"bdbv-2026 {snapshot_date}",
            "nEditions": len(rows),
            "nWithDataAsOf": len(with_data),
            "publicationLagDaysMedian": _round_optional(_median(publication_lags)),
            "publicationLagDaysMax": _round_optional(max(publication_lags) if publication_lags else None),
            "archivalLagDaysMedian": _round_optional(_median(archival_lags)),
            "archivalLagDaysMax": _round_optional(max(archival_lags) if archival_lags else None),
            "totalVisibilityLagDaysMedian": _round_optional(_median(total_lags)),
            "totalVisibilityLagDaysMax": _round_optional(max(total_lags) if total_lags else None),
            "headline": (
                "Official sources publish their figures promptly; most of the "
                "delay before a figure appears in this archive is in capture."
            ),
        },
        "sourceLatencyTable": rows,
    }


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def build_website_snapshot(
    pipeline_output: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Translate the brief-pipeline output into the website's snapshot shape.

    The website's BdbvSnapshot is a superset that also includes the public
    timeline (recent reported counts across dates) and source attribution.
    This translator centralises the static parts (calibration-point human
    statements, sources, timeline anchor points) so the website never has
    to maintain hand-edited duplicates.
    """
    date = derive_as_of_date(pipeline_output)
    visibility = pipeline_output["visibility"]
    transmission = pipeline_output["transmission"]
    corridors = pipeline_output["corridors"]
    mode_b = pipeline_output.get("mode_b_hypotheses", [])
    reported_counts = pipeline_output.get("reported_counts", {})
    affected_zones = list(pipeline_output.get("affected_zones") or [])
    zone_attributed_counts = pipeline_output.get("zone_attributed_counts") or {}

    calibration_points = build_calibration_points(mode_b)
    sources = build_sources(manifest)
    methodology_constants = build_methodology_constants()

    def count_range(name: str) -> dict[str, Any]:
        """Required reported-counts range for one metric; fails loudly.

        Every figure comes from the pipeline output. A missing metric, a missing
        min/max/primary field, or a non-integer value raises rather than silently
        substituting a stale literal that could ship an out-of-date number.
        """
        if name not in reported_counts:
            raise ValueError(f"pipeline reported_counts has no metric '{name}'")
        raw = reported_counts[name]
        primary_source_id = raw.get("primary_source_id")
        source_ids = _unique_source_ids(
            [primary_source_id] + list(raw.get("conflicting_source_ids", []))
        )

        def field(key: str) -> int:
            if key not in raw:
                raise ValueError(f"reported_counts['{name}'] lacks field '{key}'")
            value = raw[key]
            if not isinstance(value, int):
                raise ValueError(
                    f"reported_counts['{name}']['{key}'] is not an int: {value!r}"
                )
            return value

        out: dict[str, Any] = {
            "min": field("min"),
            "max": field("max"),
            "primary": field("primary"),
        }
        if primary_source_id:
            out["primarySourceId"] = primary_source_id
        if source_ids:
            out["sourceIds"] = source_ids
        return out

    confirmed_range = count_range("confirmed")
    suspected_range = count_range("suspected")
    deaths_range = count_range("deaths")
    timeline = build_timeline(
        manifest,
        current_date=date,
        endpoint_counts={
            "confirmed": confirmed_range,
            "suspected": suspected_range,
            "deaths": deaths_range,
        },
    )
    # Uganda/Kampala confirmed cases are an external anchor, carried by WHO
    # PHEIC/IHR/DG source text rather than a separate reported-count metric.
    uganda_confirmed = 2
    drc_confirmed = max(0, confirmed_range["primary"] - uganda_confirmed)
    visibility_payload = {
        "grade": visibility["grade"],
        "reportingCompleteness50": [
            round(visibility["reporting_completeness_50"][0], 3),
            round(visibility["reporting_completeness_50"][1], 3),
        ],
        "publicationLatencyDays50": [
            round(visibility["publication_latency_50"][0], 1),
            round(visibility["publication_latency_50"][1], 1),
        ],
        "confirmationBacklog50": [
            int(round(visibility["confirmation_backlog_50"][0])),
            int(round(visibility["confirmation_backlog_50"][1])),
        ],
    }
    if manifest is not None:
        visibility_payload.update(build_source_latency_payload(manifest, date))

    snapshot = {
        "date": date,
        "asOf": pipeline_output["as_of"],
        "outbreakId": pipeline_output["outbreak_id"],
        "pathogen": "BDBV",
        "countryScope": ["COD", "UGA"],
        "affectedZones": affected_zones,
        "zoneAttributedCounts": zone_attributed_counts,
        "reportedCounts": {
            # Official / regional anchors: WHO PHEIC (17 May), ECDC
            # (19 May), and WHO Director-General remarks (20 May).
            "confirmed": confirmed_range,
            "suspected": suspected_range,
            "deaths": deaths_range,
        },
        "confirmedByCountry": {
            # WHO Director-General remarks (22 May): 82 confirmed in DRC and
            # 2 imported confirmed in Uganda.
            "cod": drc_confirmed,
            "uga": uganda_confirmed,
            "contextNote": (
                "Uganda count: 2 confirmed in Kampala (1 death) per WHO PHEIC "
                "statement, Africa CDC PHECS declaration, WHO 20 May remarks, "
                "WHO 22 May remarks, and WHO IHR Emergency Committee temporary "
                "recommendations. WHO reported 82 confirmed cases in DRC as of "
                "22 May. The reported Kinshasa case tested negative on "
                "confirmatory INRB testing and is not counted as confirmed. "
                "WHO IHR temporary recommendations state that no onward Uganda "
                "transmission among contacts of the two confirmed imported cases "
                "was documented as of 22 May."
            ),
            "contextCitations": [
                {
                    "sourceId": "who-pheic-2026-05-17",
                    "label": "WHO PHEIC",
                },
                {
                    "sourceId": "africa-cdc-phecs-2026-05-18",
                    "label": "Africa CDC PHECS",
                },
                {
                    "sourceId": "ecdc-bdbv-drc-uga-2026-05-19",
                    "label": "ECDC, 19 May",
                },
                {
                    "sourceId": "who-dg-remarks-bdbv-2026-05-20",
                    "label": "WHO DG remarks",
                },
                {
                    "sourceId": "who-dg-remarks-bdbv-2026-05-22",
                    "label": "WHO DG, 22 May",
                },
                {
                    "sourceId": "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22",
                    "label": "WHO IHR temporary recommendations",
                },
                {
                    "sourceId": "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "label": "20 May consensus aggregator",
                },
            ],
        },
        "healthcareWorkers": {"deaths": 4},
        "timeline": timeline,
        "visibility": visibility_payload,
        "transmission": {
            # Pass through the full posterior over generations-before-detection.
            # Bins are integer keys "1".."MAX". The terminal bin (the largest
            # key) is censored: it holds the mass for "that-many or more"
            # generations because the back-to-index simulator caps at
            # MAX_GENERATIONS. The website renders all bins and labels the
            # censored upper one explicitly.
            "generations": {
                k: round(v, 4) for k, v in transmission["generations"].items()
            },
            "generationsMaxBinIsCensored": bool(
                transmission.get("generations_max_bin_is_censored", False)
            ),
            "generationsMaxBinKey": str(
                transmission.get(
                    "generations_max_bin_key",
                    max(transmission["generations"].keys(), key=int),
                )
            ),
            "latentActiveChains95": list(transmission["latent_active_chains_95"]),
        },
        "corridors": [
            {
                "source": c["source"],
                "target": c["target"],
                "horizonDays": c["horizon_days"],
                "riskRaw50": [
                    round(c["risk_raw_lower_50"], 3),
                    round(c["risk_raw_upper_50"], 3),
                ],
                "riskAdjusted50": [
                    round(c["risk_adj_lower_50"], 3),
                    round(c["risk_adj_upper_50"], 3),
                ],
                "riskAdjusted95": [
                    round(c["risk_adj_lower_95"], 3),
                    round(c["risk_adj_upper_95"], 3),
                ],
                "drivers": c.get("drivers", []),
            }
            for c in corridors
        ],
        "calibrationPoints": calibration_points,
        "calibrationClock": build_calibration_clock(pipeline_output.get("calibration_clock")),
        "calibrationBlocks": build_calibration_blocks(
            pipeline_output.get("calibration_blocks")
        ),
        "methodology_constants": methodology_constants,
        "resolvesAt": pipeline_output.get("resolves_at", "2026-06-19T23:59:59Z"),
        "sources": sources,
        "sourceConflictNotes": build_source_conflict_notes(),
        "updateExplanations": build_update_explanations(
            date=date,
            confirmed_primary=confirmed_range["primary"],
            corridors=corridors,
            zone_attributed_counts=zone_attributed_counts,
        ),
    }
    validate_public_snapshot(snapshot)
    return snapshot


def build_source_conflict_notes() -> list[dict[str, Any]]:
    """Structured reconciliation notes with source IDs for inline refs."""
    return [
        {
            "text": (
                "Suspected count spans 395 (Africa CDC PHECS, 18 May 2026) "
                "to almost 750 (WHO Director-General Member State briefing, "
                "22 May 2026). CDC Current Situation reports a lower 21 May "
                "structured tuple of 575 suspected cases; ECDC's 21 May update "
                "carries the WHO-derived approximately-600 suspected cross-check, "
                "and the archived 20 May consensus aggregator reports 653. WHO's "
                "newer official briefing is the headline endpoint."
            ),
            "sourceIds": [
                "africa-cdc-phecs-2026-05-18",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "ecdc-bdbv-drc-uga-2026-05-21",
                "cdc-current-situation-2026-05-21",
                "who-dg-remarks-bdbv-2026-05-22",
            ],
        },
        {
            "text": (
                "Deaths span 106 (Africa CDC PHECS, 18 May 2026) to 177 "
                "suspected deaths (WHO Director-General Member State briefing, "
                "22 May 2026). Earlier anchors remain in the conflict trail: "
                "ECDC 130 on 19 May, WHO/ECDC 139 on 20/21 May, the archived "
                "20 May consensus aggregator 144, and CDC 148 on 21 May."
            ),
            "sourceIds": [
                "africa-cdc-phecs-2026-05-18",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "ecdc-bdbv-drc-uga-2026-05-21",
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "cdc-current-situation-2026-05-21",
                "who-dg-remarks-bdbv-2026-05-22",
            ],
        },
        {
            "text": (
                "Confirmed count spans 10 (WHO PHEIC statement, 17 May 2026, "
                "case data as of 16 May: 8 Ituri + 2 Kampala; Kinshasa case "
                "deconfirmed) to 84 total country-scope confirmed cases (WHO "
                "Director-General Member State briefing, 22 May 2026: 82 DRC "
                "+ 2 imported Uganda). CDC's 21 May structured tuple is "
                "superseded for the headline endpoint but retained as dated "
                "conflict evidence."
            ),
            "sourceIds": [
                "who-pheic-2026-05-17",
                "who-dg-remarks-bdbv-2026-05-20",
                "cdc-current-situation-2026-05-21",
                "who-dg-remarks-bdbv-2026-05-22",
            ],
        },
        {
            "text": (
                "Spatial model source zones use the newest official "
                "per-health-zone confirmed-count table in the manifest: WHO "
                "AFRO SitRep-01 (data as of 18 May 2026) lists confirmed cases "
                "in Bunia, Butembo, Goma, Katwa, Mongbwalu, Nyankunde, and "
                "Rwampara. CDC 21 May reports the outbreak in 11 DRC health "
                "zones in Ituri and Nord-Kivu as of 20 May but does not publish "
                "a zone-attributed count table. Uganda has 2 confirmed imported "
                "cases including 1 death; WHO IHR temporary recommendations "
                "state no onward Uganda transmission among contacts was "
                "documented as of 22 May."
            ),
            "sourceIds": [
                "afro-sitrep-01-pdf-2026-05-18",
                "who-dg-remarks-bdbv-2026-05-20",
                "cdc-current-situation-2026-05-21",
                "who-dg-remarks-bdbv-2026-05-22",
                "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22",
            ],
        },
        {
            "text": (
                "Per-source archive status: all cited sources are registered "
                "in data/bundibugyo-2026/manifest.json. WHO DON 602, WHO "
                "PHEIC, WHO DG remarks, WHO IHR temporary recommendations, "
                "WHO AFRO landing page, CDC HAN, CDC "
                "Current Situation, ECDC May 19/21, and the consensus aggregator are "
                "byte-archived with SHA-256 or content-addressed raw bytes; "
                "Africa CDC, Imperial, and PAHO/WHO are hash-recorded with restricted raw "
                "publisher bytes kept private pending terms or permission "
                "confirmation."
            ),
            "sourceIds": [
                "who-don602-2026-05-15",
                "who-pheic-2026-05-17",
                "who-dg-remarks-bdbv-2026-05-20",
                "who-dg-remarks-bdbv-2026-05-22",
                "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22",
                "afro-sitrep-01-2026-05-18",
                "cdc-han-00530-2026-05",
                "cdc-current-situation-2026-05-21",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "ecdc-bdbv-drc-uga-2026-05-21",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "africa-cdc-phecs-2026-05-18",
                "imperial-mrc-gida-bdbv-2026-05-18",
                "imperial-mrc-gida-bdbv-2026-05-20",
                "paho-who-epialert-bdbv-2026-05-21-pdf",
            ],
        },
        {
            "text": (
                "May 21 context-only sources are archived separately from count truth. "
                "CDC traveler guidance and traveler information, the ECDC threat "
                "assessment, the PAHO/WHO epidemiological alert, WHO AFRO regional "
                "readiness reporting, and the UK support update inform preparedness "
                "and monitoring context; they do not change headline case/death "
                "counts unless they publish explicit count or geography evidence."
            ),
            "sourceIds": [
                "cdc-traveler-management-guidance-2026-05-21-pdf",
                "cdc-returning-travelers-info-2026-05-21",
                "ecdc-threat-assessment-bdbv-2026-05-21-pdf",
                "paho-who-epialert-bdbv-2026-05-21-pdf",
                "who-afro-zambia-readiness-2026-05-21",
                "uk-gov-ebola-eastern-drc-support-2026-05-21",
            ],
        },
    ]


def build_update_explanations(
    *,
    date: str,
    confirmed_primary: int,
    corridors: list[dict[str, Any]],
    zone_attributed_counts: dict[str, Any],
) -> dict[str, str]:
    """Snapshot-specific narrative checks that should move with the data.

    These are intentionally generated beside the data translator rather than
    hand-written in the website. The preflight gate below requires them when
    the public snapshot crosses known drift thresholds, so large numeric moves
    cannot silently ship with yesterday's explanation.
    """
    upper_bounds = [c["risk_adj_upper_50"] for c in corridors]
    lower_bounds = [c["risk_adj_lower_50"] for c in corridors]
    upper_min = min(upper_bounds) * 100
    upper_max = max(upper_bounds) * 100
    lower_min = min(lower_bounds) * 100
    lower_max = max(lower_bounds) * 100
    zone_confirmed_total = sum(
        int(row.get("confirmed") or 0)
        for row in zone_attributed_counts.values()
        if isinstance(row, dict)
    )
    source_zone_count = len(zone_attributed_counts)
    unallocated_confirmed_total = confirmed_primary - zone_confirmed_total
    return {
        "timelineCarryForward": (
            "The 20 May and 21 May endpoint rows remain in the public timeline "
            "when a later snapshot is generated. They are dated evidence points, "
            f"not replaced by the {date} headline endpoint."
        ),
        "corridorShift": (
            "This is source-attribution lag, not missing cases: the outbreak "
            "headline is "
            f"{confirmed_primary} confirmed cases. Corridor risk uses the "
            f"{zone_confirmed_total} confirmed cases that are currently "
            "officially zone-attributed across "
            f"{source_zone_count} WHO AFRO source zones. The remaining "
            f"{unallocated_confirmed_total} confirmed cases are treated as "
            "unallocated headline context until WHO, WHO AFRO, DRC MoH, "
            "Uganda MoH, or Africa CDC publishes an updated zone table. That "
            f"puts the current {len(corridors)}-corridor watchlist at "
            f"{lower_min:.1f}-{lower_max:.1f}% lower bounds and "
            f"{upper_min:.1f}-{upper_max:.1f}% upper bounds. The earlier "
            "inflated display came from aggregate smearing, not from current "
            "corridor-specific evidence; it was not a corridor-specific signal."
        ),
        "blindspotValidation": (
            "The May 22 official WHO sources and the WHO AFRO per-zone table were "
            "rechecked against the blindspot list. Butembo, Goma, Katwa, and "
            "Nyankunde are now promoted into the source-zone footprint; Arua and "
            "Nebbi remain target-side watch endpoints; Mahagi is still not "
            "promoted to a source zone because no WHO or Africa CDC source names "
            "Mahagi health zone as case-affected."
        ),
        "calibrationCarryForward": (
            "Active calibration points are historical pre-commitments, so their "
            "original pinned ranges carry forward unchanged for later scoring. "
            "They should not be read as the current corridor watchlist after the "
            "May 22 source-zone attribution correction."
        ),
    }


def _timeline_figures(manifest: dict[str, Any]) -> dict[str, dict]:
    """Canonical source_id -> normalized_content (strips the -live ingest suffix)."""
    figures: dict[str, dict] = {}
    for entry in manifest.get("entries", []):
        source_id = entry.get("source_id", "")
        canonical = canonical_source_id(source_id)
        figures[canonical] = entry.get("normalized_content", {})
    return figures


def _mf(figures: dict[str, dict], source_id: str, field: str) -> int:
    """Required integer figure from a manifest source, by canonical id; fails loudly."""
    if source_id not in figures:
        raise ValueError(f"manifest has no source '{source_id}'")
    content = figures[source_id]
    if field not in content:
        raise ValueError(f"manifest source '{source_id}' lacks field '{field}'")
    value = content[field]
    if not isinstance(value, int):
        raise ValueError(f"manifest {source_id}.{field} is not an int: {value!r}")
    return value


def build_timeline(
    manifest: dict[str, Any] | None,
    current_date: str,
    endpoint_counts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Public-reporting timeline points to display in the trajectory chart.

    Every historical count is pulled from the dated source manifest by canonical
    source id. The endpoint row is pulled from the reconciled headline counts
    produced by refresh_pipeline.py, so a new source-cadence decision cannot
    leave the trajectory chart on a stale prior-day metric.
    """
    if manifest is None:
        raise ValueError("build_timeline requires the source manifest")
    figures = _timeline_figures(manifest)
    endpoint_source_ids = _unique_source_ids(
        [
            endpoint_counts["confirmed"].get("primarySourceId"),
            endpoint_counts["suspected"].get("primarySourceId"),
            endpoint_counts["deaths"].get("primarySourceId"),
            *endpoint_counts["confirmed"].get("sourceIds", []),
            *endpoint_counts["suspected"].get("sourceIds", []),
            *endpoint_counts["deaths"].get("sourceIds", []),
        ]
    )

    # Timeline points anchored to dated, verifiable sources. Where a source
    # does not publish a count for one field, that field is null and the
    # chart draws a no-data marker.
    return [
        {
            # 15 May: WHO DON 602 reports 246 suspected, 80 deaths (4 deaths
            # among confirmed). Eight samples lab-confirmed on 15 May. The
            # confirmed-cases visible-as-of count for the DON 602 byte
            # archive is the 4 deaths-among-confirmed number reported by
            # the DON (which lab-confirmed 8 samples that day).
            "date": "2026-05-15",
            "confirmed": _mf(figures, "who-don602-2026-05-15", "cases_confirmed"),
            "suspected": _mf(figures, "who-don602-2026-05-15", "cases_suspected"),
            "deaths": _mf(figures, "who-don602-2026-05-15", "deaths"),
            "sourceLabel": "WHO DON 602 (15 May 2026)",
            "sourceId": "who-don602-2026-05-15",
            "sourceIds": ["who-don602-2026-05-15"],
        },
        {
            # 17 May: WHO PHEIC statement reports 8 Ituri + 2 Kampala = 10
            # lab-confirmed; 246 suspected; 80 deaths. The reported Kinshasa
            # case was deconfirmed by INRB and is excluded.
            "date": "2026-05-17",
            "confirmed": _mf(figures, "who-pheic-2026-05-17", "cases_confirmed"),
            "suspected": _mf(figures, "who-pheic-2026-05-17", "cases_suspected"),
            "deaths": _mf(figures, "who-pheic-2026-05-17", "deaths"),
            "sourceLabel": "WHO PHEIC statement (17 May 2026)",
            "sourceId": "who-pheic-2026-05-17",
            "sourceIds": ["who-pheic-2026-05-17"],
        },
        {
            # 18 May: Africa CDC PHECS reports approximately 395 suspected,
            # 106 deaths in DRC plus 2 cases / 1 death in Kampala.
            "date": "2026-05-18",
            "confirmed": None,
            "suspected": _mf(figures, "africa-cdc-phecs-2026-05-18", "cases_suspected_drc_approx"),
            "deaths": _mf(figures, "africa-cdc-phecs-2026-05-18", "deaths_approx"),
            "sourceLabel": "Africa CDC PHECS (18 May 2026)",
            "sourceId": "africa-cdc-phecs-2026-05-18",
            "sourceIds": ["africa-cdc-phecs-2026-05-18"],
        },
        {
            # 19 May: ECDC gives the strongest near-current official /
            # regional anchor before the 20 May aggregator-tier endpoint.
            "date": "2026-05-19",
            "confirmed": _mf(figures, "ecdc-bdbv-drc-uga-2026-05-19", "cases_confirmed"),
            "suspected": _mf(figures, "ecdc-bdbv-drc-uga-2026-05-19", "cases_suspected_min"),
            "deaths": _mf(figures, "ecdc-bdbv-drc-uga-2026-05-19", "deaths"),
            "sourceLabel": "ECDC outbreak page (19 May 2026)",
            "sourceId": "ecdc-bdbv-drc-uga-2026-05-19",
            "sourceIds": ["ecdc-bdbv-drc-uga-2026-05-19"],
        },
        {
            # 20 May: carry forward the prior public endpoint. Confirmed comes
            # from WHO DG remarks; suspected/deaths use the archived consensus
            # aggregator that the 20 May snapshot already displayed.
            "date": "2026-05-20",
            "confirmed": _mf(figures, "who-dg-remarks-bdbv-2026-05-20", "cases_confirmed"),
            "suspected": _mf(figures, "wikipedia-2026-ituri-epidemic-2026-05-20", "cases_suspected"),
            "deaths": _mf(figures, "wikipedia-2026-ituri-epidemic-2026-05-20", "deaths"),
            "sourceLabel": "WHO DG confirmed; archived consensus suspected/deaths",
            "sourceId": "who-dg-remarks-bdbv-2026-05-20",
            "sourceIds": [
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
            ],
            "metricSourceIds": {
                "confirmed": ["who-dg-remarks-bdbv-2026-05-20"],
                "suspected": ["wikipedia-2026-ituri-epidemic-2026-05-20"],
                "deaths": ["wikipedia-2026-ituri-epidemic-2026-05-20"],
            },
        },
        {
            # 21 May: carry forward the previous snapshot's reconciled endpoint.
            # CDC's fresher death count is used while the confirmed/suspected
            # headline remains the already-published 20 May endpoint until WHO's
            # newer 22 May count supersedes it.
            "date": "2026-05-21",
            "confirmed": _mf(figures, "who-dg-remarks-bdbv-2026-05-20", "cases_confirmed"),
            "suspected": _mf(figures, "wikipedia-2026-ituri-epidemic-2026-05-20", "cases_suspected"),
            "deaths": _mf(figures, "cdc-current-situation-2026-05-21", "deaths_suspected"),
            "sourceLabel": "May 21 reconciled endpoint by metric",
            "sourceId": "who-dg-remarks-bdbv-2026-05-20",
            "sourceIds": [
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "cdc-current-situation-2026-05-21",
            ],
            "metricSourceIds": {
                "confirmed": ["who-dg-remarks-bdbv-2026-05-20"],
                "suspected": ["wikipedia-2026-ituri-epidemic-2026-05-20"],
                "deaths": ["cdc-current-situation-2026-05-21"],
            },
        },
        {
            # Current endpoint: per-metric reconciled primary values. This may
            # intentionally mix publisher cadences, but it is always the same
            # tuple shown in reportedCounts.
            "date": current_date,
            "confirmed": endpoint_counts["confirmed"]["primary"],
            "suspected": endpoint_counts["suspected"]["primary"],
            "deaths": endpoint_counts["deaths"]["primary"],
            "sourceLabel": "Reconciled endpoint by metric",
            "sourceId": endpoint_counts["confirmed"].get("primarySourceId"),
            "sourceIds": endpoint_source_ids,
            "metricSourceIds": {
                "confirmed": [endpoint_counts["confirmed"].get("primarySourceId")],
                "suspected": [endpoint_counts["suspected"].get("primarySourceId")],
                "deaths": [endpoint_counts["deaths"].get("primarySourceId")],
            },
        },
    ]


def _iter_source_refs(value: Any, path: str = "$") -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in {"sourceId", "primarySourceId"}:
                if item:
                    refs.append((child_path, item))
                continue
            if key == "sourceIds":
                for idx, source_id in enumerate(item or []):
                    refs.append((f"{child_path}[{idx}]", source_id))
                continue
            if key == "metricSourceIds":
                for metric, source_ids in (item or {}).items():
                    for idx, source_id in enumerate(source_ids or []):
                        refs.append((f"{child_path}.{metric}[{idx}]", source_id))
                continue
            refs.extend(_iter_source_refs(item, child_path))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            refs.extend(_iter_source_refs(item, f"{path}[{idx}]"))
    return refs


def validate_public_snapshot(snapshot: dict[str, Any]) -> None:
    """Fail fast on public-surface drift before a snapshot JSON is written."""
    source_ids = {source["id"] for source in snapshot.get("sources", [])}
    missing_refs = [
        f"{path} -> {source_id}"
        for path, source_id in _iter_source_refs(snapshot)
        if source_id not in source_ids
    ]
    if missing_refs:
        raise ValueError(
            "public snapshot has unresolved source references: "
            + "; ".join(missing_refs[:8])
        )

    timeline = snapshot.get("timeline") or []
    endpoint = timeline[-1] if timeline else None
    if not endpoint:
        raise ValueError("public snapshot timeline is empty")
    if endpoint.get("date") != snapshot.get("date"):
        raise ValueError(
            f"timeline endpoint date {endpoint.get('date')!r} does not match "
            f"snapshot date {snapshot.get('date')!r}"
        )
    for metric in ("confirmed", "suspected", "deaths"):
        endpoint_value = endpoint.get(metric)
        primary = snapshot["reportedCounts"][metric]["primary"]
        if endpoint_value != primary:
            raise ValueError(
                f"timeline endpoint {metric}={endpoint_value!r} does not match "
                f"reportedCounts.{metric}.primary={primary!r}"
            )

    timeline_dates = {row.get("date") for row in timeline}
    source_ids = {source["id"] for source in snapshot.get("sources", [])}
    carry_forward_dates = {
        "2026-05-20": "who-dg-remarks-bdbv-2026-05-20",
        "2026-05-21": "cdc-current-situation-2026-05-21",
    }
    for required_date, source_id in carry_forward_dates.items():
        if snapshot.get("date", "") > required_date and source_id in source_ids:
            if required_date not in timeline_dates:
                raise ValueError(
                    f"public snapshot timeline dropped source-backed date {required_date}"
                )

    explanations = snapshot.get("updateExplanations") or {}
    for key in (
        "timelineCarryForward",
        "corridorShift",
        "blindspotValidation",
        "calibrationCarryForward",
    ):
        if not explanations.get(key):
            raise ValueError(f"public snapshot lacks updateExplanations.{key}")

    corridors = snapshot.get("corridors", [])
    if not corridors:
        raise ValueError("public snapshot has no corridors")
    corridor_lower_bounds = [c["riskAdjusted50"][0] for c in corridors]
    corridor_upper_bounds = [c["riskAdjusted50"][1] for c in corridors]
    corridor_lower_min = min(corridor_lower_bounds) * 100
    corridor_lower_max = max(corridor_lower_bounds) * 100
    corridor_upper_min = min(corridor_upper_bounds) * 100
    corridor_upper_max = max(corridor_upper_bounds)
    corridor_upper_max_pct = corridor_upper_max * 100
    if corridor_upper_max >= 0.60:
        text = explanations["corridorShift"].lower()
        required_terms = ("aggregate", "upper-envelope", "not new corridor-specific")
        missing_terms = [term for term in required_terms if term not in text]
        if missing_terms:
            raise ValueError(
                "corridor shift explanation does not name the aggregate/source-zone "
                f"distinction clearly enough; missing {missing_terms}"
            )

    confirmed_total = (
        snapshot.get("confirmedByCountry", {}).get("cod", 0)
        + snapshot.get("confirmedByCountry", {}).get("uga", 0)
    )
    confirmed_primary = snapshot["reportedCounts"]["confirmed"]["primary"]
    if confirmed_total != confirmed_primary:
        raise ValueError(
            f"confirmedByCountry total {confirmed_total} does not match "
            f"reportedCounts.confirmed.primary {confirmed_primary}"
        )

    affected_zones = set(snapshot.get("affectedZones") or [])
    zone_counts = snapshot.get("zoneAttributedCounts") or {}
    if zone_counts and affected_zones != set(zone_counts):
        raise ValueError(
            "affectedZones must match zoneAttributedCounts when an official "
            "per-health-zone table is available"
        )
    if set(zone_counts) - affected_zones:
        raise ValueError(
            "zoneAttributedCounts contains zones not listed in affectedZones: "
            + ", ".join(sorted(set(zone_counts) - affected_zones))
        )
    zone_confirmed_total = 0
    for zid, row in zone_counts.items():
        if not isinstance(row, dict):
            raise ValueError(f"zoneAttributedCounts.{zid} must be an object")
        if not (row.get("sourceId") or row.get("source_id")) or not (
            row.get("sourcePublishedAt") or row.get("source_published_at")
        ):
            raise ValueError(
                f"zoneAttributedCounts.{zid} lacks sourceId/sourcePublishedAt provenance"
            )
        zone_confirmed_total += int(row.get("confirmed") or 0)
    if zone_confirmed_total > confirmed_primary:
        raise ValueError(
            f"zone-attributed confirmed total {zone_confirmed_total} exceeds "
            f"headline confirmed primary {confirmed_primary}"
        )
    if zone_counts:
        explanation = explanations["corridorShift"].lower()
        unallocated_confirmed_total = confirmed_primary - zone_confirmed_total
        required_fragments = tuple(
            snapshot_contract.narrative_required_fragments_from_values(
                confirmed_headline=confirmed_primary,
                zone_confirmed=zone_confirmed_total,
                unallocated=unallocated_confirmed_total,
                source_zone_count=len(zone_counts),
                corridor_count=len(corridors),
                lower_range_pct=(corridor_lower_min, corridor_lower_max),
                upper_range_pct=(corridor_upper_min, corridor_upper_max_pct),
            )
        ) + (
            "unallocated headline context",
            "not missing cases",
            "bounds",
        )
        missing_fragments = [
            fragment
            for fragment in required_fragments
            if fragment.lower() not in explanation
        ]
        if missing_fragments:
            raise ValueError(
                "corridor shift explanation is stale relative to current data; "
                f"missing {missing_fragments}"
            )
        if corridor_upper_max < 0.60:
            stale_terms = ("high-60", "high 60", "69.", "68.", "67.", "66.", "65.", "64.")
            stale_hits = [term for term in stale_terms if term in explanation]
            if stale_hits:
                raise ValueError(
                    "corridor shift explanation still references the prior inflated "
                    f"corridor display despite current upper max {corridor_upper_max_pct:.1f}%; "
                    f"stale terms {stale_hits}"
                )
    corridor_sources = {c.get("source") for c in snapshot.get("corridors", [])}
    missing_sources = corridor_sources - affected_zones
    if missing_sources:
        raise ValueError(
            "corridor sources are not all represented in affectedZones: "
            + ", ".join(sorted(str(s) for s in missing_sources))
        )


def build_calibration_points(mode_b: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach plain-language statements to each Mode-B calibration point."""
    corridor_statements: dict[str, str] = {
        "bunia -> kampala-uga": (
            "At least one new laboratory-confirmed BDBV case appears in Kampala "
            "(Uganda) between 20 May 2026 and 19 June 2026, given continued "
            "reporting from Bunia Health Zone (Ituri Province, DRC)."
        ),
        "rwampara -> bundibugyo-uga": (
            "At least one new laboratory-confirmed BDBV case appears in Bundibugyo "
            "District (Uganda) between 20 May 2026 and 19 June 2026, given continued "
            "reporting from Rwampara Health Zone (Ituri Province, DRC)."
        ),
        "mongbwalu -> bundibugyo-uga": (
            "At least one new laboratory-confirmed BDBV case appears in Bundibugyo "
            "District (Uganda) between 20 May 2026 and 19 June 2026, given continued "
            "reporting from Mongbwalu Health Zone (Ituri Province, DRC)."
        ),
        "mongbwalu -> beni-cod": (
            "At least one new laboratory-confirmed BDBV case appears in Beni Health "
            "Zone (North Kivu Province, DRC) between 20 May 2026 and 19 June 2026, given "
            "continued reporting from Mongbwalu Health Zone (Ituri Province, DRC)."
        ),
        "rwampara -> kasese-uga": (
            "At least one new laboratory-confirmed BDBV case appears in Kasese District "
            "(Uganda) between 20 May 2026 and 19 June 2026, given continued reporting "
            "from Rwampara Health Zone (Ituri Province, DRC)."
        ),
        "mongbwalu -> kasese-uga": (
            "At least one new laboratory-confirmed BDBV case appears in Kasese District "
            "(Uganda) between 20 May 2026 and 19 June 2026, given continued reporting "
            "from Mongbwalu Health Zone (Ituri Province, DRC)."
        ),
    }
    out: list[dict[str, Any]] = []
    point_counts_by_pin: dict[str, int] = {}
    for entry in mode_b:
        pinned_at = entry.get("pinned_at") or "snapshot"
        point_counts_by_pin[pinned_at] = point_counts_by_pin.get(pinned_at, 0) + 1
        statement = corridor_statements.get(
            entry["corridor"],
            f"Calibration point for corridor {entry['corridor']}.",
        )
        out.append(
            {
                "hypothesisId": f"public-calibration-point-{pinned_at}-{point_counts_by_pin[pinned_at]:02d}",
                "corridor": entry["corridor"],
                "riskAdjusted50": list(entry["risk_adj_50"]),
                "blockId": entry.get("block_id"),
                "pinnedAt": pinned_at,
                "resolvesAt": entry.get("resolves_at"),
                "horizonDays": entry.get("horizon_days"),
                "selectionRole": entry.get("selection_role"),
                "riskTier": entry.get("risk_tier"),
                "geographyClass": entry.get("geography_class"),
                "controlRole": entry.get("control_role"),
                "statement": statement,
            }
        )
    return out


def build_calibration_clock(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Website-shaped calibration clock, keeping horizon and remaining days distinct."""
    if not raw:
        return None
    return {
        "blockId": raw.get("block_id"),
        "status": raw.get("status"),
        "pointCount": raw.get("point_count"),
        "pinnedAt": raw["pinned_at"],
        "asOf": raw["as_of"],
        "resolvesAt": raw["resolves_at"],
        "horizonDays": raw["horizon_days"],
        "elapsedDays": raw["elapsed_days"],
        "remainingDays": raw["remaining_days"],
        "equation": raw["equation"],
    }


def build_calibration_blocks(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Website-shaped calibration blocks."""
    if not raw:
        return []
    return [
        block
        for block in (build_calibration_clock(item) for item in raw)
        if block is not None
    ]


SOURCE_LABEL_OVERRIDES: dict[str, str] = {
    "who-don602-2026-05-15": "WHO Disease Outbreak News item 2026-DON602: Ebola disease caused by Bundibugyo virus, DRC and Uganda (15 May 2026)",
    "who-pheic-2026-05-17": "WHO Director-General PHEIC determination statement (17 May 2026)",
    "who-dg-remarks-bdbv-2026-05-20": "WHO Director-General opening remarks at the media briefing on Ebola outbreak in DRC and Uganda (20 May 2026)",
    "who-dg-remarks-bdbv-2026-05-22": "WHO Director-General opening remarks at the Member State information session on Ebola and hantavirus (22 May 2026), reporting 82 confirmed DRC cases, 7 confirmed DRC deaths, almost 750 suspected cases, 177 suspected deaths, and 2 imported Uganda cases including 1 death",
    "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22": "WHO IHR Emergency Committee temporary recommendations for the BDBV PHEIC (22 May 2026), including DRC very-high risk, Uganda high risk, high regional risk, and no documented onward Uganda transmission among contacts",
    "afro-sitrep-01-2026-05-18": "WHO African Region Weekly External Situation Report 01 (data as of 18 May 2026)",
    "afro-sitrep-01-pdf-2026-05-18": "WHO African Region Weekly External Situation Report 01 PDF (data as of 18 May 2026), including the official affected-health-zone count table",
    "cdc-han-00530-2026-05": "US CDC Health Alert Network notice HAN00530: Ebola Disease Outbreak in DRC and Uganda",
    "cdc-current-situation-2026-05-20": "US CDC Ebola Disease: Current Situation update (20 May 2026), reporting the 19 May structured count tuple",
    "cdc-current-situation-2026-05-21": "US CDC Ebola Disease: Current Situation update (21 May 2026), reporting 575 suspected cases, 51 confirmed cases, and 148 suspected deaths across DRC and Uganda",
    "imperial-mrc-gida-bdbv-2026-05-20": "Imperial College MRC Centre for Global Infectious Disease Analysis (with WHO HEP, WHO Uganda, WHO AFRO). Estimation of the size of the BDBV outbreak in DRC, 20 May 2026 update: 400-900 cases estimated (values over 1,000 not excluded).",
    "imperial-mrc-gida-bdbv-2026-05-18": "Imperial College MRC Centre for Global Infectious Disease Analysis (with WHO HEP, WHO Uganda, WHO AFRO). Estimation of the size of the BDBV outbreak in DRC, 18 May 2026 (superseded by the 20 May update).",
    "imperial-mrc-gida-bdbv-2026-05-18-pdf": "Imperial College MRC GIDA / WHO BDBV outbreak-size estimate PDF (18 May 2026; superseded by the 20 May update)",
    "africa-cdc-phecs-2026-05-18": "Africa CDC declaration of a Public Health Emergency of Continental Security on the Bundibugyo Ebola outbreak (18 May 2026)",
    "ecdc-bdbv-drc-uga-2026-05-19": "European Centre for Disease Prevention and Control outbreak page: Ebola virus disease outbreak in DRC and Uganda (19 May 2026)",
    "ecdc-bdbv-drc-uga-2026-05-21": "European Centre for Disease Prevention and Control outbreak page update (21 May 2026, 18:00): WHO-derived cross-check of approximately 600 suspected cases, 139 suspected deaths, 51 DRC confirmed cases, and two imported Uganda cases",
    "ecdc-threat-assessment-bdbv-2026-05-21-pdf": "European Centre for Disease Prevention and Control threat assessment brief for the Bundibugyo Ebola outbreak (21 May 2026)",
    "wikipedia-2026-ituri-epidemic-2026-05-20": "Wikipedia article '2026 Ituri Province Ebola epidemic', accessed 20 May 2026. Consensus aggregator cited only as an archived public signal.",
    "paho-who-epialert-bdbv-2026-05-21-pdf": "PAHO/WHO epidemiological alert: Ebola Disease due to Bundibugyo virus in DRC and Uganda (21 May 2026)",
    "cdc-returning-travelers-info-2026-05-21": "US CDC public information for travelers returning to the United States from DRC, Uganda, and South Sudan (21 May 2026)",
    "cdc-traveler-management-guidance-2026-05-21-pdf": "US CDC interim public-health assessment and traveler-management guidance for the 2026 Ebola outbreak (21 May 2026)",
    "uk-gov-ebola-eastern-drc-support-2026-05-21": "UK FCDO/UKHSA support and returning-worker monitoring update for the eastern DRC Ebola response (21 May 2026)",
    "who-afro-zambia-readiness-2026-05-21": "WHO AFRO Zambia readiness article tied to regional Ebola preparedness (21 May 2026)",
}


def canonical_source_id(source_id: str) -> str:
    return source_id[: -len("-live")] if source_id.endswith("-live") else source_id


def _source_archive_status(entry: dict[str, Any]) -> str:
    status = entry.get("raw_archive_status") or entry.get("archive_status")
    if status == "public_bytes" or entry.get("raw_bytes_relpath"):
        return "byte-archived"
    if status == "private_restricted_bytes":
        return "hash-recorded-private-raw"
    if entry.get("content_hash"):
        return "hash-recorded-private-raw"
    return "url-referenced"


def _source_label(entry: dict[str, Any], canonical_id: str) -> str:
    override = SOURCE_LABEL_OVERRIDES.get(canonical_id)
    if override:
        return override
    content = entry.get("normalized_content", {})
    for field in ("publication_title", "report_title", "alert_title"):
        title = content.get(field)
        if title:
            return f"{entry.get('publisher', 'Source')}. {title}"
    return f"{entry.get('publisher', 'Source')}: {canonical_id.replace('-', ' ')}"


def build_sources(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Sources cited in the snapshot. Every entry MUST be a real, dated,
    retrievable document, with a URL where one is publicly available. The
    archive_status field declares whether public bytes are shipped under
    data/bundibugyo-2026/raw/ or only hash-recorded because the raw publisher
    bytes are restricted.
    """
    if manifest is None:
        raise ValueError("build_sources requires the source manifest")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in manifest.get("entries", []):
        source_id = entry.get("source_id", "")
        if not source_id:
            continue
        canonical_id = canonical_source_id(source_id)
        if canonical_id in seen:
            continue
        seen.add(canonical_id)
        source: dict[str, Any] = {
            "id": canonical_id,
            "label": _source_label(entry, canonical_id),
            "publishedAt": str(entry.get("published_at", "")).split("T", 1)[0],
            "url": entry.get("url", ""),
            "archiveStatus": _source_archive_status(entry),
            "publisher": entry.get("publisher", ""),
            "sourceTier": entry.get("source_tier", ""),
            "license": entry.get("license", ""),
            "contentHash": entry.get("content_hash", ""),
            "retrievedAt": entry.get("retrieved_at", ""),
        }
        sources.append({k: v for k, v in source.items() if v})
    return sources


def build_methodology_constants() -> dict[str, Any]:
    """Single-source-of-truth methodology constants written into every snapshot.

    The values are pulled from the LOVS Python modules so the website never
    has to mirror them. Centralizing the constants here means that updating
    the Rosello gamma fit, the CFR scenario set, the Imperial reference
    range, or the central doubling time requires changing ONE place
    (lovs_onset_to_death.py / lovs_death_back_projection.py); the next
    snapshot build then carries the update to the website. Components
    that previously hardcoded these numbers fall back to literals only
    when the field is missing on an older snapshot.
    """
    gamma = otd.BDBV_ONSET_TO_DEATH
    cfrs = dbp.IMPERIAL_CFR_SCENARIOS
    return {
        "cfr": {
            "low_95": cfrs[0],
            "central": dbp.CENTRAL_CFR,
            "high_95": cfrs[-1],
            "source_url": dbp.CDC_BVD_HISTORY_URL,
            "source_short": dbp.CDC_BVD_HISTORY_SHORT,
        },
        "onset_to_death_gamma": {
            "alpha": gamma.alpha,
            "beta_per_day": gamma.beta_per_day,
            "mean_days": gamma.mean_days,
            "sd_days": gamma.std_days,
            "source_url": otd.BDBV_ONSET_TO_DEATH_URL,
            "source_short": otd.BDBV_ONSET_TO_DEATH_SHORT,
        },
        "imperial_reference": {
            "low": dbp.IMPERIAL_REFERENCE_LOW,
            "high": dbp.IMPERIAL_REFERENCE_HIGH,
            "as_of_date": dbp.IMPERIAL_REFERENCE_AS_OF,
            "url": dbp.IMPERIAL_REFERENCE_URL,
            "source_short": dbp.IMPERIAL_REFERENCE_SHORT,
        },
        "central_doubling_time_days": dbp.CENTRAL_DOUBLING_TIME_DAYS,
    }


def write_atomic(path: pathlib.Path, content: str) -> None:
    """Atomic write via temp file + os.replace (per feedback_atomic_csv_writes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def update_index_ts(index_path: pathlib.Path, date: str) -> bool:
    """Insert the snapshot date into the auto-managed blocks in index.ts.

    Idempotent: if the date is already present, this is a no-op. Returns
    True if a change was written.
    """
    if not index_path.exists():
        raise FileNotFoundError(f"index.ts not found at {index_path}")
    original = index_path.read_text()

    var_name = "snapshot" + date.replace("-", "")
    import_line = f"import {var_name} from './{date}.json';"
    date_line = f"  '{date}',"
    # JSON imports come back with widened types (e.g. number[] not [number, number]),
    # so cast via unknown to preserve the BdbvSnapshot tuple shape.
    map_line = f"  '{date}': {var_name} as unknown as BdbvSnapshot,"

    new_content = original
    changed = False

    # Imports block, dedupe by import filename
    new_content, did_change = _insert_into_block(
        new_content,
        begin_marker=r"/\* SNAPSHOT_IMPORTS_BEGIN[^*]*\*/",
        end_marker=r"/\* SNAPSHOT_IMPORTS_END \*/",
        new_line=import_line,
        sort=False,
        dedupe_key=f"./{date}.json",
    )
    changed = changed or did_change

    # Dates block, sorted newest-first (descending), dedupe by date string
    new_content, did_change = _insert_into_block(
        new_content,
        begin_marker=r"/\* SNAPSHOT_DATES_BEGIN[^*]*\*/",
        end_marker=r"/\* SNAPSHOT_DATES_END \*/",
        new_line=date_line,
        sort=True,
        descending=True,
        dedupe_key=f"'{date}'",
    )
    changed = changed or did_change

    # Map block, dedupe by date string
    new_content, did_change = _insert_into_block(
        new_content,
        begin_marker=r"/\* SNAPSHOT_MAP_BEGIN[^*]*\*/",
        end_marker=r"/\* SNAPSHOT_MAP_END \*/",
        new_line=map_line,
        sort=False,
        dedupe_key=f"'{date}'",
    )
    changed = changed or did_change

    if changed:
        write_atomic(index_path, new_content)
    return changed


def _insert_into_block(
    text: str,
    begin_marker: str,
    end_marker: str,
    new_line: str,
    sort: bool,
    descending: bool = False,
    dedupe_key: str | None = None,
) -> tuple[str, bool]:
    """Insert ``new_line`` into the auto-managed block, idempotent.

    The block is defined by the regex markers. Lines are de-duplicated by
    ``dedupe_key`` (or exact match if not provided). If ``sort`` is True,
    all lines are sorted (descending for newest-first date listings).
    Returns the updated text and a changed flag.
    """
    pattern = re.compile(
        rf"({begin_marker})\s*\n(.*?)\n(\s*{end_marker})",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(
            f"could not find marker block ({begin_marker} ... {end_marker}) in index.ts"
        )

    body = match.group(2).rstrip()
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if dedupe_key is None:
        already_present = new_line in lines
    else:
        already_present = any(dedupe_key in ln for ln in lines)
    if already_present:
        return text, False
    lines.append(new_line)
    if sort:
        lines.sort(reverse=descending)
    new_body = "\n".join(lines)
    updated = text[: match.start(2)] + new_body + text[match.end(2) :]
    return updated, True


def copy_assets(brief_root: pathlib.Path, website_root: pathlib.Path) -> list[str]:
    """Copy public brief assets and dataset deliverables into the website."""
    copied: list[str] = []
    public_root = website_root / "public" / "bdbv-2026"
    public_root.mkdir(parents=True, exist_ok=True)

    visuals_src = brief_root / "brief" / "visuals"
    visuals_dst = public_root / "visuals"
    if visuals_src.exists():
        visuals_dst.mkdir(parents=True, exist_ok=True)
        for svg in visuals_src.glob("*.svg"):
            shutil.copy2(svg, visuals_dst / svg.name)
            copied.append(f"visuals/{svg.name}")

    # Canonical generator output is deliverables/brief.pdf (committed, see
    # .gitignore). Ebola_2026_brief.pdf is a stray legacy name that make_brief.py
    # no longer produces, so sourcing it shipped a stale PDF to the website.
    pdf_src = brief_root / "deliverables" / "brief.pdf"
    if pdf_src.exists():
        shutil.copy2(pdf_src, public_root / "brief.pdf")
        copied.append("brief.pdf")

    dataset_root = brief_root / "deliverables" / "public-health-dataset"
    for name in (
        "lovs-public-health-dataset.xlsx",
        "lovs-public-health-dataset.schema.json",
        "lovs-public-health-dataset.manifest.json",
    ):
        src = dataset_root / name
        if src.exists():
            shutil.copy2(src, public_root / name)
            copied.append(name)
    return copied


def update_social_image_version(website_root: pathlib.Path, date: str) -> bool:
    """Align BDBV social-card cache busting with the latest snapshot date."""
    social_path = website_root / "app" / "bdbv-2026" / "_lib" / "social.ts"
    if not social_path.exists():
        return False
    text = social_path.read_text(encoding="utf-8")
    updated = re.sub(r"v=clean-\d{4}-\d{2}-\d{2}", f"v=clean-{date}", text)
    if updated == text:
        return False
    write_atomic(social_path, updated)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--website-root",
        type=pathlib.Path,
        default=DEFAULT_WEBSITE_ROOT,
        help=f"Path to apps/site (default: {DEFAULT_WEBSITE_ROOT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without modifying any files.",
    )
    args = parser.parse_args(argv)

    if not args.website_root.exists():
        print(f"error: website root not found: {args.website_root}", file=sys.stderr)
        return 2

    pipeline_output = load_pipeline_output(REPO_ROOT / "data" / "live-bdbv-2026-output.json")
    manifest = load_archive_manifest(
        REPO_ROOT / "data" / "bundibugyo-2026" / "manifest.json"
    )
    snapshot = build_website_snapshot(pipeline_output, manifest)
    date = snapshot["date"]

    bdbv_data_dir = args.website_root / "app" / "bdbv-2026" / "_data"
    snapshots_dir = bdbv_data_dir / "snapshots"
    snapshot_path = snapshots_dir / f"{date}.json"
    index_path = snapshots_dir / "index.ts"
    zones_dst_path = bdbv_data_dir / "zones.json"

    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    zones_src_path = REPO_ROOT / "data" / "zones.json"
    natural_earth_src_path = REPO_ROOT / "data" / "natural_earth_outlines.json"
    natural_earth_dst_path = bdbv_data_dir / "natural_earth_outlines.json"

    if args.dry_run:
        print(f"[dry-run] would write {snapshot_path}")
        print(f"[dry-run] would ensure {index_path} registers {date}")
        print(f"[dry-run] would mirror {zones_src_path} -> {zones_dst_path}")
        if natural_earth_src_path.exists():
            print(
                f"[dry-run] would mirror {natural_earth_src_path} -> {natural_earth_dst_path}"
            )
    else:
        write_atomic(snapshot_path, payload)
        print(f"wrote {snapshot_path.relative_to(args.website_root)}")
        if update_index_ts(index_path, date):
            print(f"registered {date} in {index_path.relative_to(args.website_root)}")
        else:
            print(f"{date} already registered in {index_path.relative_to(args.website_root)}")
        if zones_src_path.exists():
            shutil.copy2(zones_src_path, zones_dst_path)
            print(f"mirrored {zones_dst_path.relative_to(args.website_root)}")
        if natural_earth_src_path.exists():
            shutil.copy2(natural_earth_src_path, natural_earth_dst_path)
            print(
                f"mirrored {natural_earth_dst_path.relative_to(args.website_root)}"
            )
        if update_social_image_version(args.website_root, date):
            print("updated app/bdbv-2026/_lib/social.ts")

    if args.dry_run:
        asset_count = 0
        visuals_src = REPO_ROOT / "brief" / "visuals"
        if visuals_src.exists():
            asset_count += sum(1 for _ in visuals_src.glob("*.svg"))
        if (REPO_ROOT / "deliverables" / "brief.pdf").exists():
            asset_count += 1
        dataset_root = REPO_ROOT / "deliverables" / "public-health-dataset"
        asset_count += sum(
            1
            for name in (
                "lovs-public-health-dataset.xlsx",
                "lovs-public-health-dataset.schema.json",
                "lovs-public-health-dataset.manifest.json",
            )
            if (dataset_root / name).exists()
        )
        print(f"[dry-run] would copy {asset_count} assets")
    else:
        assets = copy_assets(REPO_ROOT, args.website_root)
        for a in assets:
            print(f"copied public/bdbv-2026/{a}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
