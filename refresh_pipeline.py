#!/usr/bin/env python3
"""Refresh the LOVS pipeline output to the PHEIC-era situation as of 25 May 2026.

Constructs an OutbreakSnapshot reflecting the situation as of 2026-05-25,
based on:
 - WHO Disease Outbreak News item 2026-DON602 (15 May 2026 declaration)
 - WHO AFRO Weekly External Situation Report 01 (data as of 18 May 2026)
 - Africa CDC PHECS declaration and Emergency Consultative Group 18 May 2026
 - ECDC outbreak page (19 May 2026)
 - WHO Director-General remarks and aggregator-tier reporting through 20 May 2026
 - US CDC Current Situation update (21 May 2026)
 - ECDC outbreak/risk-assessment update and May 21 guidance/context sources
 - WHO Director-General Member State briefing and IHR Emergency Committee
   temporary recommendations (22 May 2026)
 - US CDC Current Situation update (23 May 2026)

Runs the LOVS pipeline modules (visibility, transmission, corridor risk)
against the updated snapshot, and writes a refreshed pipeline output to
``data/live-bdbv-2026-output.json``.

Pre-committed methodology calibration points (mode_b_hypotheses) are carried
forward UNCHANGED from the immutable calibration ledger
(data/calibration-ledger.json); they are never re-derived from the current
run's corridors. A snapshot can carry multiple active blocks, each with its
own pin date, resolution date, and clock. See PIPELINE.md, section (c)
Calibration and resolution.

Stdlib only.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import date, datetime

from lovs import insp_block_assembler
from lovs import lovs_next_zone
from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler
from lovs import lovs_transmission
from lovs import lovs_visibility


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "live-bdbv-2026-output.json"
LEDGER_PATH = DATA_DIR / "calibration-ledger.json"
MANIFEST_PATH = DATA_DIR / "bundibugyo-2026" / "manifest.json"
TARGETS_CONFIG_PATH = DATA_DIR / "snapshot_targets.json"

# Plan A 2026-05-28: INRB-UMIE consortium release tarball used to populate the
# INSP per-zone surface. The path is the founder-machine development cache; CI
# resolves the same content hash via manifest.json. If the path does not exist
# the assembler falls back to data_scale_used="national" (spec §6.7).
INRB_UMIE_ARTIFACT_PATH = pathlib.Path("/tmp/inrb-bb8b7d5/build.tar.gz")
INRB_UMIE_DATA_AS_OF = date(2026, 5, 26)
INRB_UMIE_SOURCE_ID = "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5"
# Reference-upstream pointer (Option A): the per-health-zone counts are retained
# in this analytic output as the reconciliation-integrity substrate, but they are
# transcribed from the upstream INRB-UMIE/INSP release and explicitly attributed
# to it here. Human-facing deliverables (website per-zone table, spreadsheet
# per-zone sheet) reference this release rather than re-hosting the table.
INSP_UPSTREAM_REFERENCE = {
    "publisher": "INRB-UMIE consortium",
    "data_publisher": "INSP DRC",
    "repository": "https://github.com/INRB-UMIE/Ebola_DRC_2026",
    "build": "build-2026-05-28-bb8b7d5",
    "data_as_of": "2026-05-26",
    "terms": (
        "Per-health-zone series is INSP SitRep material; reuse with attribution "
        "to INSP and citation of the report number and date; confirm distribution "
        "terms with INSP before external republication."
    ),
}
_BARE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Source identifiers used in the refreshed snapshot. Every source id MUST
# correspond to a real, dated, retrievable document. Sources lacking a SHA
# archive in data/bundibugyo-2026/raw/ are explicitly marked as not-yet-
# archived in the website-facing sources block.
SOURCES = (
    "who-don602-2026-05-15",
    "who-pheic-2026-05-17",
    "afro-sitrep-01-2026-05-18",
    "afro-sitrep-01-pdf-2026-05-18",
    "africa-cdc-phecs-2026-05-18",
    "ecdc-bdbv-drc-uga-2026-05-19",
    "ecdc-bdbv-drc-uga-2026-05-21",
    "wikipedia-2026-ituri-epidemic-2026-05-20",
    "who-dg-remarks-bdbv-2026-05-20",
    "cdc-current-situation-2026-05-21",
    "cdc-current-situation-2026-05-23",
    "cdc-current-situation-2026-05-24",
    "who-don603-2026-05-21",
    "who-dg-remarks-bdbv-2026-05-22",
    "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22",
    "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
    "cdc-current-situation-2026-05-25",
    "ecdc-bdbv-drc-uga-2026-05-25",
    "ecdc-bdbv-drc-uga-2026-05-26",
    "ecdc-bdbv-drc-uga-2026-05-27",
    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
)

OFFICIAL_ZONE_COUNT_TIERS = frozenset(
    {
        "official_who",
        "official_who_afro",
        "official_africa_cdc",
        "official_continental_body",
        "official_cdc",
        "national_moh",
        "regional_body",
    }
)

ZONE_ID_ALIASES = {
    # The WHO AFRO table labels the health-zone row as "Goma"; the canonical
    # map/model id keeps the country suffix because Goma is also a city name.
    "goma": "goma-cod",
}

REPORTED_ZONE_ID_ALIASES = {
    "bunia": "bunia",
    "butembo": "butembo",
    "goma": "goma-cod",
    "katwa": "katwa",
    "kilo": "kilo",
    "kilo mission": "kilo",
    "miti murhesa": "miti-murhesa",
    "mongbwalu": "mongbwalu",
    "nizi": "nizi",
    "nyakunde": "nyankunde",
    "nyankunde": "nyankunde",
    "rwampara": "rwampara",
}


def canonical_source_id(source_id: str) -> str:
    return source_id[: -len("-live")] if source_id.endswith("-live") else source_id


def canonical_zone_id(zone_id: str) -> str:
    return ZONE_ID_ALIASES.get(zone_id, zone_id)


def canonical_reported_zone_id(zone_name: str) -> str:
    normalized = re.sub(r"\s+", " ", zone_name.strip().lower())
    if normalized in REPORTED_ZONE_ID_ALIASES:
        return REPORTED_ZONE_ID_ALIASES[normalized]
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def _load_manifest_entries() -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return list(manifest.get("entries", []))


def _load_manifest_figures() -> dict[str, dict]:
    """Map canonical source_id -> normalized_content from the source manifest.

    Manifest live-ingest entries carry a ``-live`` suffix on their source_id
    (e.g. ``who-don602-2026-05-15-live``); the snapshot and ledger reference the
    canonical id without that suffix. Index by the canonical (suffix-stripped)
    id so the reconciliation policy below can name sources in canonical form.
    """
    figures: dict[str, dict] = {}
    for entry in _load_manifest_entries():
        source_id = entry.get("source_id", "")
        figures[canonical_source_id(source_id)] = entry.get("normalized_content", {})
    return figures


def load_zone_attributed_counts() -> tuple[dict[str, dict], dict[str, str]]:
    """Select the newest official per-health-zone confirmed-count table.

    This is the source-zone primitive for corridor risk. The headline count can
    be newer than the per-zone table; we intentionally keep those concepts
    separate rather than smearing the aggregate across every source zone.
    """
    candidates: list[tuple[str, str, dict[str, dict], dict[str, str]]] = []
    for entry in _load_manifest_entries():
        if entry.get("source_tier") not in OFFICIAL_ZONE_COUNT_TIERS:
            continue
        content = entry.get("normalized_content") or {}
        health_zones = content.get("affected_health_zones")
        if not isinstance(health_zones, dict):
            continue
        source_id = canonical_source_id(str(entry.get("source_id", "")))
        published_at = str(entry.get("published_at", ""))
        rows: dict[str, dict] = {}
        for raw_zone_id, counts in health_zones.items():
            if not isinstance(raw_zone_id, str) or not isinstance(counts, dict):
                continue
            confirmed = counts.get("confirmed")
            if not isinstance(confirmed, int) or confirmed <= 0:
                continue
            zone_id = canonical_zone_id(raw_zone_id)
            if zone_id in rows:
                raise ValueError(
                    f"multiple affected_health_zones rows canonicalize to {zone_id!r}"
                )
            rows[zone_id] = {
                **counts,
                "confirmed": confirmed,
                "source_id": source_id,
                "source_published_at": published_at,
                "original_zone_id": raw_zone_id,
            }
        if rows:
            candidates.append(
                (
                    published_at,
                    source_id,
                    rows,
                    {
                        "source_id": source_id,
                        "published_at": published_at,
                        "basis": "official affected_health_zones table",
                    },
                )
            )
    if not candidates:
        return {}, {}
    _, _, rows, meta = max(candidates, key=lambda item: (item[0], item[1]))
    return {zone_id: rows[zone_id] for zone_id in sorted(rows)}, meta


def load_source_review_geographies(as_of: str | None = None) -> list[dict]:
    """Return official dashboard rows that are not yet corridor source load.

    These rows are first-class evidence, but they do not satisfy the corridor
    model's source-zone contract until the table semantics are verified as a
    cumulative per-zone count vector. This keeps an official new geography
    visible without smearing uncertain rows into the corridor watchlist.
    """
    geographies: list[dict] = []
    for entry in _load_manifest_entries():
        if entry.get("source_tier") not in OFFICIAL_ZONE_COUNT_TIERS:
            continue
        published_at = str(entry.get("published_at", ""))
        if as_of and published_at[:10] > as_of:
            continue
        content = entry.get("normalized_content") or {}
        if content.get("table_semantics_status") != "source_review":
            continue
        rows = content.get("reported_rows")
        if not isinstance(rows, list):
            continue
        source_id = canonical_source_id(str(entry.get("source_id", "")))
        report_date = (
            content.get("data_as_of")
            or str(content.get("date_rapportage", ""))[:10]
            or None
        )
        publication_date = (
            content.get("publication_date")
            or str(content.get("date_publication", ""))[:10]
            or published_at[:10]
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            zone_name = str(row.get("zone_sante") or "").strip()
            if not zone_name:
                continue
            confirmed = int(row.get("cas_confirmes") or 0)
            suspected = int(row.get("cas_suspects") or 0)
            deaths = int(row.get("deces") or 0)
            if confirmed <= 0 and suspected <= 0 and deaths <= 0:
                continue
            geographies.append(
                {
                    "zone_id": canonical_reported_zone_id(zone_name),
                    "zone_name": zone_name,
                    "province": row.get("province"),
                    "confirmed": confirmed,
                    "suspected": suspected,
                    "deaths": deaths,
                    "source_id": source_id,
                    "source_published_at": published_at,
                    "report_date": report_date,
                    "publication_date": publication_date,
                    "table_semantics_status": "source_review",
                    "model_use": "display_only_pending_table_semantics",
                    "review_reasons": [
                        "official_drc_moh_reported_row",
                        "table_semantics_source_review",
                    ],
                }
            )
    geographies.sort(
        key=lambda row: (
            str(row.get("publication_date") or ""),
            str(row.get("source_id") or ""),
            str(row.get("province") or ""),
            str(row.get("zone_name") or ""),
        )
    )
    return geographies


def _source_zone_conflict_note(zone_counts: dict[str, dict]) -> str:
    """Explain which official health-zone table drives corridor source load."""
    source_ids = {
        str(row.get("source_id") or "")
        for row in zone_counts.values()
        if row.get("source_id")
    }
    zone_confirmed_total = sum(int(row.get("confirmed") or 0) for row in zone_counts.values())
    source_zone_count = len(zone_counts)
    zone_names = ", ".join(
        sorted(
            str(row.get("original_zone_name") or zone_id)
            for zone_id, row in zone_counts.items()
        )
    )
    if source_ids and all(
        source_id.startswith("drc-moh-epidemie-dashboard") for source_id in source_ids
    ):
        return (
            "Spatial model source zones use the newest official per-health-zone "
            "confirmed-count table in the manifest: the DRC MoH SitRep MVE N "
            "007/MVB_17/2026 PDF cumulative Table IV (data as of 21 May 2026, "
            f"published 22 May 2026) attributes {zone_confirmed_total} confirmed "
            f"cases across {source_zone_count} DRC MoH source zones: {zone_names}. "
            "The PDF headline is 83 DRC confirmed cases because 4 confirmed samples "
            "lack case forms/health-zone attribution; newer public headline "
            "aggregates now exceed that source-load table, so the contract keeps "
            "the additional confirmed cases as unallocated headline context. "
            "Corridor source load therefore uses the "
            "attributed DRC health-zone vector rather than applying the headline "
            "aggregate to every source zone."
        )
    if source_ids and all(
        source_id.startswith("inrb-umie") for source_id in source_ids
    ):
        zones_with_confirmed = sum(
            1 for row in zone_counts.values() if int(row.get("confirmed") or 0) > 0
        )
        return (
            "Spatial model source zones use the INRB-UMIE/INSP per-health-zone "
            "series (consortium build-2026-05-28-bb8b7d5, data as of 26 May "
            f"2026), which attributes {zone_confirmed_total} confirmed cases "
            f"across {source_zone_count} monitored health zones "
            f"({zones_with_confirmed} with confirmed cases). The national DRC and "
            "country-scope headline confirmed totals are higher; the difference is "
            "carried as unallocated and cross-border attribution-lag context "
            "rather than smeared across every source zone. Corridor source load "
            "therefore uses the INSP per-health-zone confirmed vector."
        )
    return (
        "Spatial model source zones use the newest official per-health-zone "
        "confirmed-count table in the manifest: WHO AFRO SitRep-01 (data as of "
        "18 May 2026) lists confirmed cases in Bunia, Butembo, Goma, Katwa, "
        "Mongbwalu, Nyankunde, and Rwampara. The May 22 WHO headline aggregate "
        "is newer and larger, but it is not a zone-attributed line list; corridor "
        "source load therefore uses the official per-zone vector rather than "
        "applying the aggregate count to every zone."
    )


def _figure(figures: dict[str, dict], source_id: str, field: str) -> int:
    """Pull a required integer figure from a manifest source by canonical id.

    Fails loudly if the source or field is missing, so a manifest edit that
    drops or renames a figure cannot silently ship a stale hardcoded number.
    """
    if source_id not in figures:
        raise ValueError(f"manifest has no source '{source_id}'")
    content = figures[source_id]
    if field not in content:
        raise ValueError(f"manifest source '{source_id}' lacks field '{field}'")
    value = content[field]
    if not isinstance(value, int):
        raise ValueError(f"manifest {source_id}.{field} is not an int: {value!r}")
    return value


def _dashboard_aggregate_figure(figures: dict[str, dict], source_id: str, field: str) -> int:
    """Pull an integer from a source's official dashboard aggregate block."""
    if source_id not in figures:
        raise ValueError(f"manifest has no source '{source_id}'")
    aggregate = figures[source_id].get("dashboard_aggregate")
    if not isinstance(aggregate, dict):
        raise ValueError(f"manifest source '{source_id}' lacks dashboard_aggregate")
    value = aggregate.get(field)
    if not isinstance(value, int):
        raise ValueError(f"manifest {source_id}.dashboard_aggregate.{field} is not an int: {value!r}")
    return value


def load_target_zones() -> tuple[str, ...]:
    """Read candidate target zones from the geography config (single source of truth).

    Targets are the 'where could cases appear next' watch set: pure geography, not
    case assertions. Centralizing them in data/snapshot_targets.json means a future
    snapshot cannot silently omit a target. Falls back to the historical 20 May set
    if the config is absent so the pipeline never produces zero targets.
    """
    if TARGETS_CONFIG_PATH.exists():
        cfg = json.loads(TARGETS_CONFIG_PATH.read_text(encoding="utf-8"))
        targets = tuple(
            str(t["id"]) for t in cfg.get("candidate_target_zones", []) if t.get("id")
        )
        if targets:
            return targets
    return ("kasese-uga", "kampala-uga", "bundibugyo-uga", "beni-cod")


BASE_SNAPSHOT_AS_OF = "2026-05-28T23:59:59Z"


def apply_carry_forward(
    base: lovs_reconciler.OutbreakSnapshot,
    target_as_of: str,
    reason: str = "awaiting_next_publication",
) -> lovs_reconciler.OutbreakSnapshot:
    """Clock a base snapshot forward to `target_as_of` with LOCF provenance.

    Used when no fresh source declarations have arrived since `base.as_of`
    but the snapshot cadence needs to advance. Every primary metric in
    `reported_counts` (e.g. confirmed, suspected_active, suspected_cumulative,
    probable) and every metric in `reported_deaths` (confirmed, suspected) is
    tagged with `carried_forward_from=base.as_of` and
    `carried_forward_reason=reason`. Headline values, source IDs, and the
    conflict set are unchanged: LOCF preserves the prior cumulative
    attestation rather than re-deriving it.

    Default `reason="awaiting_next_publication"` reflects the most common
    operational case: the next upstream publication has not yet arrived for
    the target cycle. Callers wanting to flag a partial / per-field carry
    because the upstream schema evolved should pass
    `reason="source_schema_evolved"` and apply at the field level rather
    than via this whole-snapshot helper.

    Zero-information property: downstream trend-aware consumers must read
    `carried_forward_from` and treat the row as no-fresh-evidence for any
    delta calculation. This is the contract that keeps the calibration
    ledger uncorrupted while the snapshot cadence continues.
    """
    if target_as_of <= base.as_of:
        raise ValueError(
            f"target_as_of {target_as_of!r} must be after base.as_of {base.as_of!r}"
        )
    carried_counts = {
        k: v.with_carry_forward(base.as_of, reason)
        for k, v in base.reported_counts.items()
    }
    carried_deaths = {
        k: v.with_carry_forward(base.as_of, reason)
        for k, v in base.reported_deaths.items()
    }
    return dataclasses.replace(
        base,
        as_of=target_as_of,
        reported_counts=carried_counts,
        reported_deaths=carried_deaths,
    )


def build_snapshot() -> lovs_reconciler.OutbreakSnapshot:
    """Construct the current-cycle OutbreakSnapshot (as_of 2026-05-25) from explicitly verified sources.

    Every figure below traces to a named, dated, retrievable source. No
    "aggregated public reporting" placeholder; every conflict is between two
    named sources.

    Source timeline:
      - 15 May: WHO DON 602 reports 246 suspected and 80 deaths (4 deaths
        among confirmed) from Rwampara, Mongbwalu, Bunia HZ in Ituri DRC,
        plus 1 imported case in Kampala UG (a Congolese man, died).
      - 17 May: WHO PHEIC declaration page reports 8 lab-confirmed in Ituri
        and 2 in Kampala (1 death); a reported Kinshasa case tested negative
        on confirmatory INRB testing and is not counted as confirmed.
      - 18 May: Africa CDC PHECS declaration reports approximately 395
        suspected and 106 deaths in DRC (Mongbwalu, Rwampara, Bunia HZ)
        plus 2 cases and 1 death in Kampala.
      - 19 May: ECDC reports 30 laboratory-confirmed cases, over 500
        suspected cases, 130 deaths, most cases in Ituri Province, and one
        case in Goma, North Kivu Province.
      - 20 May: 2026 Ituri Province Ebola epidemic article on Wikipedia (a
        consensus aggregator citing Reuters, BBC, CDC HAN, MSF, ECDC, AP,
        Imperial College and other primary outlets) reports 51 confirmed,
        653 suspected and 144 deaths. WHO Director-General remarks on the
        same date report 51 confirmed in DRC, 2 confirmed in Kampala, and an
        American national confirmed positive after evacuation from DRC to
        Germany.
      - 21 May: US CDC Current Situation reports the latest structured official
        DRC/Uganda tuple: 575 suspected cases, 51 confirmed cases, and
        148 suspected deaths. CDC says those figures include 2 confirmed Uganda
        cases including 1 death, with no further Uganda spread reported. Because
        CDC does not state that the lower suspected/confirmed figures supersede
        the higher 20 May public signals, those lower values are retained as
        conflict anchors, not treated as down-revisions.
      - 21 May: ECDC updates the outbreak page/threat assessment with WHO-derived
        cross-check context as of 20 May: approximately 600 suspected cases,
        139 deaths among suspected cases, 51 DRC confirmed cases, and two
        imported Uganda cases. This is staged as cross-check evidence, not as
        a new primary denominator.
      - 22 May: WHO DG Member State briefing reports 82 confirmed cases and
        7 confirmed deaths in DRC, almost 750 suspected cases, 177 suspected
        deaths, and 2 imported Uganda cases including 1 death. WHO also revises
        risk to very high nationally in DRC, high regionally, and low globally.
        WHO IHR Emergency Committee temporary recommendations separately state
        that Uganda has 2 confirmed imported BVD cases and no documented onward
        transmission among their contacts as of 22 May.

    Every count value below is pulled from data/bundibugyo-2026/manifest.json by
    source id; only the reconciliation policy (which dated source bounds each
    metric) lives here. A manifest figure update flows through automatically.
    """
    figures = _load_manifest_figures()
    zone_counts, zone_counts_meta = load_zone_attributed_counts()
    if not zone_counts:
        raise ValueError(
            "no official affected_health_zones confirmed-count table available; "
            "refusing to build a spatial source-zone model from aggregate counts only"
        )
    snapshot_sources = tuple(
        source_id
        for source_id in SOURCES
        if source_id
    )
    if zone_counts_meta.get("source_id") and zone_counts_meta["source_id"] not in snapshot_sources:
        snapshot_sources = snapshot_sources + (zone_counts_meta["source_id"],)
    return lovs_reconciler.OutbreakSnapshot(
        outbreak_id="bdbv-uga-cod-2026",
        as_of="2026-05-28T23:59:59Z",
        pathogen="BDBV",
        country_scope=("COD", "UGA"),
        reported_counts={
            "suspected_cumulative": lovs_reconciler.ReconciledCount(
                # Reconciliation doctrine: the endpoint is the highest valid primary
                # on the same count concept on the latest date. ECDC 27 May
                # (citing "On 26 May, the Ministry of Health in DRC reported")
                # reports 1077 suspected DRC cases, cross-corroborated by the INRB
                # 27 May build asset (DRC-only national_moh, data-as-of 26 May).
                # The CDC 25 May 906, ECDC 25 May 904, and the DRC MoH all-published-
                # bulletins aggregate of 854 reported cases (24 May) are retained
                # as dated conflict anchors.
                # Schema split 2026-06-01: this is the cumulative series (all
                # cases ever classified as suspected since outbreak start). The
                # active series (currently under investigation or isolation) is
                # not published as a headline tile until SitRep 016 (May 30).
                minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "cases_suspected_drc_approx"),
                maximum=_figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "cases_suspected_drc"),
                primary_value=_figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "cases_suspected_drc"),
                primary_source_id="ecdc-bdbv-drc-uga-2026-05-27",
                conflicting_source_ids=(
                    "afro-sitrep-01-2026-05-18",
                    "africa-cdc-phecs-2026-05-18",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "ecdc-bdbv-drc-uga-2026-05-21",
                    "cdc-current-situation-2026-05-21",
                    "who-dg-remarks-bdbv-2026-05-22",
                    "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
                    "cdc-current-situation-2026-05-24",
                    "ecdc-bdbv-drc-uga-2026-05-25",
                    "cdc-current-situation-2026-05-25",
                    "ecdc-bdbv-drc-uga-2026-05-26",
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                ),
            ),
            "confirmed": lovs_reconciler.ReconciledCount(
                # 17 May (WHO PHEIC statement; case data as of 16 May):
                # 8 Ituri + 2 Kampala = 10. The reported Kinshasa case was
                # deconfirmed by INRB and is excluded.
                # 19 May (ECDC): 30. 20 May (WHO DG): 51 DRC + 2 Kampala = 53.
                # 22 May (WHO DG): 82 DRC + 2 imported Uganda = 84. 23 May
                # (CDC): 83 DRC + 5 Uganda = 88. 24 May (CDC): 101 DRC + 5 Uganda
                # = 106. 25 May (CDC): 105 DRC + 7 Uganda = 112. 27 May (ECDC,
                # citing DRC MoH on 26 May): 121 DRC + 7 Uganda = 128, the
                # highest valid primary on the latest date. INRB build-2026-05-28
                # (DRC-only national_moh, data-as-of 26 May) cross-corroborates
                # the DRC component (121). CDC 25 May (112) and ECDC 25-26 May
                # (101/112) are retained as dated conflict anchors.
                minimum=_figure(figures, "who-pheic-2026-05-17", "cases_confirmed"),
                maximum=_figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "cases_confirmed_total"),
                primary_value=_figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "cases_confirmed_total"),
                primary_source_id="ecdc-bdbv-drc-uga-2026-05-27",
                conflicting_source_ids=(
                    "who-pheic-2026-05-17",
                    "ecdc-bdbv-drc-uga-2026-05-19",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "who-dg-remarks-bdbv-2026-05-20",
                    "cdc-current-situation-2026-05-21",
                    "who-dg-remarks-bdbv-2026-05-22",
                    "cdc-current-situation-2026-05-23",
                    "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
                    "cdc-current-situation-2026-05-24",
                    "ecdc-bdbv-drc-uga-2026-05-25",
                    "cdc-current-situation-2026-05-25",
                    "ecdc-bdbv-drc-uga-2026-05-26",
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                ),
            ),
        },
        reported_deaths={
            # Post 2026-06-01 schema split: deaths are reported as two separate
            # series, each sourced from upstream fields that the INRB
            # build-2026-05-28-bb8b7d5 publishes directly:
            #   - deaths_confirmed: laboratory-confirmed deaths (DRC + Uganda).
            #     17 DRC confirmed deaths (INRB) + 1 Uganda confirmed death
            #     (ECDC 27 May) = 18 country-scope confirmed deaths.
            #   - deaths_suspected: deaths among suspected (not yet lab-cleared)
            #     cases. 246 DRC suspected deaths (INRB).
            # The legacy single-bucket 247 composition (246 suspected + 1
            # confirmed) is retired; it conflated clinically-confirmed deaths
            # with under-investigation suspected deaths under one headline.
            "confirmed": lovs_reconciler.ReconciledCount(
                minimum=_figure(
                    figures,
                    "inrb-umie-ebola-drc-2026-build-2026-05-27-059661a",
                    "deaths_confirmed_drc",
                )
                + _figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "deaths_uganda"),
                maximum=_figure(
                    figures,
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                    "deaths_confirmed_drc",
                )
                + _figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "deaths_uganda"),
                primary_value=_figure(
                    figures,
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                    "deaths_confirmed_drc",
                )
                + _figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "deaths_uganda"),
                primary_source_id="inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                conflicting_source_ids=(
                    "inrb-umie-ebola-drc-2026-build-2026-05-27-059661a",
                    "ecdc-bdbv-drc-uga-2026-05-27",
                ),
            ),
            "suspected": lovs_reconciler.ReconciledCount(
                minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "deaths_approx"),
                maximum=_figure(
                    figures,
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                    "deaths_suspected_drc",
                ),
                primary_value=_figure(
                    figures,
                    "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                    "deaths_suspected_drc",
                ),
                primary_source_id="inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
                conflicting_source_ids=(
                    "afro-sitrep-01-2026-05-18",
                    "africa-cdc-phecs-2026-05-18",
                    "ecdc-bdbv-drc-uga-2026-05-21",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "who-dg-remarks-bdbv-2026-05-20",
                    "cdc-current-situation-2026-05-21",
                    "who-dg-remarks-bdbv-2026-05-22",
                    "cdc-current-situation-2026-05-23",
                    "cdc-current-situation-2026-05-24",
                    "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
                    "ecdc-bdbv-drc-uga-2026-05-25",
                    "cdc-current-situation-2026-05-25",
                    "ecdc-bdbv-drc-uga-2026-05-26",
                    "ecdc-bdbv-drc-uga-2026-05-27",
                ),
            ),
        },
        affected_zones=tuple(zone_counts.keys()),
        sources=snapshot_sources,
        case_definition_version=None,
        source_conflict_notes=(
            "Suspected/reported-case count spans 395 (Africa CDC PHECS, 18 May 2026) to 1077 suspected DRC cases (ECDC 27 May 2026, citing 'On 26 May, the Ministry of Health in DRC reported'), the highest valid primary on the latest date. INRB build-2026-05-28 (national_moh, DRC-only restricted GitHub release with data-as-of 26 May) cross-corroborates 1077. The CDC 25 May 906, ECDC 25 May 904, and the DRC MoH all-published-bulletins dashboard aggregate of 854 reported cases (24 May) are retained as dated conflict anchors and not used to down-revise the higher endpoint.",
            "Confirmed deaths reconciled across INRB/INSP/UMIE build-2026-05-28-bb8b7d5 (17 DRC confirmed deaths, national_moh, data-as-of 26 May, DRC-only) and ECDC 27 May (1 Uganda confirmed death) = 18 country-scope confirmed deaths. Suspected deaths reconciled from INRB/INSP/UMIE build-2026-05-28-bb8b7d5 cumul deces suspects (246 DRC). ECDC's 238 suspected DRC deaths on the same data date, CDC 25 May (223), ECDC 25 May (119), Africa CDC PHECS 18 May (106), and the DRC MoH 24 May dashboard aggregate (179) drop to dated conflict anchors. The two series are kept separate per INRB's published schema; the prior cross-class composition that summed suspected and confirmed under one headline is retired.",
            "Confirmed count spans 10 (WHO PHEIC statement, 17 May 2026, case data as of 16 May: 8 Ituri + 2 Kampala; Kinshasa case deconfirmed) to 128 total country-scope confirmed cases (ECDC 27 May 2026, citing DRC MoH 26 May: 121 DRC + 7 Uganda). INRB build-2026-05-28 (DRC-only national_moh) cross-corroborates the DRC component (121). The CDC 25 May 112 (105 DRC + 7 Uganda), ECDC 25 May 101 confirmed, and the DRC MoH 24 May dashboard aggregate of 112 confirmed DRC are retained as dated conflict anchors. CDC and WHO AFRO have not yet published an edition that catches up to the DRC MoH 26 May release.",
            _source_zone_conflict_note(zone_counts),
            "CDC 24 May reports five Uganda cases, but does not publish a zone-attributed count table. The DRC MoH dashboard exposes all-published-bulletins aggregate cards and sparse SitRep 009 rows; the aggregate is carried as official count evidence, while the latest sparse rows remain source-review and are not promoted to corridor source load until a cumulative PDF/table label is verified. One American national was evacuated from DRC to Germany and confirmed positive; a high-risk contact was reportedly transferred to Czechia. The reported Kinshasa case was deconfirmed by INRB and is not counted as confirmed.",
            "Per-source archive status: all cited sources are registered in data/bundibugyo-2026/manifest.json. WHO DON 602, WHO PHEIC, WHO DG remarks on 20 and 22 May, WHO IHR temporary recommendations, WHO AFRO landing page, CDC HAN, CDC Current Situation, ECDC May 19/21, and the consensus aggregator are byte-archived with SHA-256; DRC MoH dashboard GraphQL bytes, Africa CDC, Imperial, and PAHO/WHO alert PDF are hash-recorded with restricted raw publisher bytes kept private pending terms or permission confirmation.",
        ),
        deaths_to_confirmed_tension_flag=True,
        model_version="lovs_reconciler-v0.1.0",
        zone_attributed_counts=zone_counts,
    )


def _calibration_point_id(
    source: str, target: str, horizon_days: int, pinned_at: str = "2026-05-20"
) -> str:
    """Stable, content-addressed identifier for a methodology calibration point.

    The pin date is part of the hashed payload: a corridor re-pinned on a later
    date is a NEW commitment with a NEW id, never a silent overwrite of the old
    one. This is both the generator used when a calibration block is first
    pinned, and the verifier used to check ledger integrity at load time.
    """
    payload = f"bdbv-uga-cod-2026|{source}|{target}|{horizon_days}d|{pinned_at}"
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
    return f"calibration-point:bdbv-uga-cod-2026:30d:{h}"


def carry_forward_calibration(as_of: str) -> dict:
    """Carry the pre-committed calibration points forward from the ledger.

    Pre-commitment contract (PIPELINE.md, section c): pinned calibration points
    are NEVER re-derived from the current run's corridor ranking. This reads
    every active (unresolved) block in data/calibration-ledger.json whose pin
    date is on or before ``as_of`` and returns its points verbatim, plus the
    governing resolution date (the nearest upcoming resolution among active
    blocks). Carrying forward, not re-deriving, is what keeps the calibration
    honest: the model range was committed before the outcome was known.

    Returns a dict mirroring the two snapshot-output keys it feeds:
      - "mode_b_hypotheses": list of {hypothesis_id, corridor, risk_adj_50}
      - "resolves_at": the nearest active resolution timestamp

    Raises ValueError if the ledger is internally inconsistent (an id or corridor
    label that does not match its source/target) or if no active block applies as
    of ``as_of`` (which would mean running the pipeline before anything is pinned).
    """
    ledger = json.loads(LEDGER_PATH.read_text())
    as_of_day = as_of[:10]

    mode_b: list[dict] = []
    active_resolutions: list[str] = []
    for block in ledger["blocks"]:
        # Schema guards keep the date comparisons well-defined as the ledger
        # grows. pinned_at must be a bare YYYY-MM-DD so the lexicographic compare
        # against as_of_day is correct; an ISO datetime here would sort greater
        # than its own pin day and silently drop the block.
        pinned_at = block["pinned_at"]
        if not _BARE_DATE_RE.fullmatch(pinned_at):
            raise ValueError(
                f"Calibration ledger integrity error: block "
                f"{block.get('block_id')!r} pinned_at must be a bare YYYY-MM-DD "
                f"date, got {pinned_at!r}."
            )
        if block.get("status") != "active":
            continue
        if pinned_at > as_of_day:
            # Pinned in this snapshot's future; the commitment does not exist yet.
            continue
        # resolves_at must be a UTC 'Z' timestamp so min() over multiple active
        # blocks is a correct chronological pick, not a format-sensitive one.
        resolves_at = block["resolves_at"]
        if not resolves_at.endswith("Z"):
            raise ValueError(
                f"Calibration ledger integrity error: block "
                f"{block.get('block_id')!r} resolves_at must be a UTC timestamp "
                f"ending in 'Z', got {resolves_at!r}."
            )
        active_resolutions.append(resolves_at)
        for point in block["points"]:
            # Integrity guard: a content-addressed id must match the corridor it
            # claims, so a hand-edit that desyncs id from corridor is caught here
            # rather than silently shipping a mislabeled calibration point.
            expected_id = _calibration_point_id(
                point["source"],
                point["target"],
                point["horizon_days"],
                block["pinned_at"],
            )
            if point["hypothesis_id"] != expected_id:
                raise ValueError(
                    f"Calibration ledger integrity error: id "
                    f"{point['hypothesis_id']!r} does not match corridor "
                    f"{point['source']}->{point['target']} "
                    f"({point['horizon_days']}d) pinned {block['pinned_at']}; "
                    f"expected {expected_id!r}."
                )
            if point["corridor"] != f"{point['source']} -> {point['target']}":
                raise ValueError(
                    f"Calibration ledger integrity error: corridor label "
                    f"{point['corridor']!r} does not match source/target "
                    f"{point['source']}->{point['target']}."
                )
            mode_b.append(
                {
                    "hypothesis_id": point["hypothesis_id"],
                    "corridor": point["corridor"],
                    "risk_adj_50": point["risk_adj_50"],
                    "block_id": block["block_id"],
                    "pinned_at": block["pinned_at"],
                    "resolves_at": resolves_at,
                    "horizon_days": point["horizon_days"],
                }
                | {
                    key: point[key]
                    for key in (
                        "selection_role",
                        "risk_tier",
                        "geography_class",
                        "control_role",
                    )
                    if key in point
                }
            )

    if not mode_b:
        raise ValueError(
            f"No active calibration points apply as of {as_of_day}. The ledger "
            f"must pin at least one block on or before this date before the "
            f"pipeline can carry calibration forward."
        )

    return {
        "mode_b_hypotheses": mode_b,
        "resolves_at": min(active_resolutions),
    }


def _date_from_iso(value: str) -> date:
    """Return the calendar date from a bare date or UTC ISO timestamp."""
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    return date.fromisoformat(value)


def calibration_clock(as_of: str, mode_b: list[dict]) -> dict:
    """Derive elapsed and remaining days for the active calibration block.

    The model commitment remains the original horizon pinned in the ledger:
    ``horizon_days = date(resolves_at) - date(pinned_at)``. A later snapshot
    should display the carried-forward clock separately:
    ``remaining_days = date(resolves_at) - date(as_of)``.
    """
    if not mode_b:
        raise ValueError("calibration_clock requires at least one calibration point")
    first = mode_b[0]
    pinned_at = first["pinned_at"]
    resolves_at = first["resolves_at"]
    horizon_days = int(first["horizon_days"])
    pinned_day = _date_from_iso(pinned_at)
    as_of_day = _date_from_iso(as_of)
    resolves_day = _date_from_iso(resolves_at)
    observed_horizon = (resolves_day - pinned_day).days
    if observed_horizon != horizon_days:
        raise ValueError(
            "Calibration clock integrity error: "
            f"ledger horizon_days={horizon_days} but resolves_at-pinned_at={observed_horizon}"
        )
    return {
        "pinned_at": pinned_at,
        "as_of": as_of,
        "resolves_at": resolves_at,
        "horizon_days": horizon_days,
        "elapsed_days": max(0, (as_of_day - pinned_day).days),
        "remaining_days": max(0, (resolves_day - as_of_day).days),
        "equation": "remaining_days = date(resolves_at) - date(as_of)",
    }


def calibration_blocks(as_of: str, mode_b: list[dict]) -> list[dict]:
    """Group carried-forward calibration points by immutable ledger block.

    A snapshot can carry multiple active calibration blocks. Each block has its
    own pin date, original horizon, resolution timestamp, and remaining-days
    clock. Future snapshots append new blocks; they never rewrite an older one.
    """
    grouped: dict[str, list[dict]] = {}
    for point in mode_b:
        grouped.setdefault(point["block_id"], []).append(point)

    out: list[dict] = []
    as_of_day = _date_from_iso(as_of)
    for block_id, points in sorted(grouped.items()):
        clock = calibration_clock(as_of, points)
        pinned_day = _date_from_iso(clock["pinned_at"])
        out.append(
            {
                "block_id": block_id,
                "status": (
                    "pinned_in_this_snapshot"
                    if pinned_day == as_of_day
                    else "carried_forward"
                ),
                "point_count": len(points),
                "hypothesis_ids": [p["hypothesis_id"] for p in points],
                **clock,
            }
        )
    return out


def _count_output(rc: lovs_reconciler.ReconciledCount) -> dict:
    """Serialize a ReconciledCount with website / brief friendly key names.

    When the count carries forward from a prior snapshot, surface the
    `carried_forward_from` (ISO date) and `carried_forward_reason` fields.
    Downstream trend-aware consumers must read `carried_forward_from` and
    treat the row as zero-information for any delta calculation.
    """
    out: dict = {
        "min": rc.minimum,
        "max": rc.maximum,
        "primary": rc.primary_value,
        "primary_source_id": rc.primary_source_id,
        "conflicting_source_ids": list(rc.conflicting_source_ids),
    }
    if rc.carried_forward_from:
        out["carried_forward_from"] = rc.carried_forward_from
        out["carried_forward_reason"] = rc.carried_forward_reason
    return out


def _rebase_zone_counts_to_insp(insp_block: dict) -> dict[str, dict]:
    """Re-base the corridor source-load primitive onto the INSP per-zone block.

    U1 (2026-05-28): every monitored health zone's corridor source load is the
    INSP `by_lovs_zone` confirmed count, so a single per-zone confirmed cascade
    feeds the corridor model instead of the earlier CDC/INSP hybrid. Zones the
    INSP coverage audit classes `present_with_data` or `present_but_zero` are
    kept (the latter at zero source load, preserving map/affected-zone presence);
    `structurally_absent` zones are excluded. Each row carries the INRB-UMIE
    source_id so per-zone provenance is uniform. Forward-only: this rebuilds the
    descriptive corridor input only; pinned calibration blocks are read verbatim
    downstream and are never touched here.
    """
    source_id = str(insp_block.get("source_id", ""))
    source_published_at = str(insp_block.get("as_of_data_date", ""))
    audit = insp_block.get("coverage_audit", {})
    included = set(audit.get("present_with_data", [])) | set(
        audit.get("present_but_zero", [])
    )
    rebased: dict[str, dict] = {}
    for zone_id, row in (insp_block.get("by_lovs_zone") or {}).items():
        if included and zone_id not in included:
            continue
        rebased[zone_id] = {
            "confirmed": int(row.get("confirmed", 0)),
            "source_id": source_id,
            "source_published_at": source_published_at,
            "original_zone_id": zone_id,
            "province": "",
        }
    return {zone_id: rebased[zone_id] for zone_id in sorted(rebased)}


def _parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the live BDBV 2026 snapshot output.")
    parser.add_argument(
        "--as-of",
        default=None,
        help=(
            "Target snapshot publication date (YYYY-MM-DD). If later than the "
            "base date (2026-05-28), the base snapshot is carried forward with "
            "carried_forward_from provenance on every primary metric. If equal "
            "or omitted, the base snapshot is emitted as-is."
        ),
    )
    parser.add_argument(
        "--carried-forward-reason",
        default="source_stopped_declaring",
        choices=sorted(lovs_reconciler.CARRIED_FORWARD_REASONS),
        help="Reason tag attached to carried-forward rows.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli(argv)
    snapshot = build_snapshot()
    if args.as_of:
        # Normalize bare YYYY-MM-DD into the ISO end-of-day UTC stamp used
        # throughout the pipeline.
        target_as_of = args.as_of
        if len(target_as_of) == 10 and target_as_of.count("-") == 2:
            target_as_of = f"{target_as_of}T23:59:59Z"
        if target_as_of > snapshot.as_of:
            snapshot = apply_carry_forward(
                snapshot, target_as_of, reason=args.carried_forward_reason
            )
            print(
                f"Carried forward base snapshot {BASE_SNAPSHOT_AS_OF} "
                f"to {target_as_of} (reason={args.carried_forward_reason})"
            )
        elif target_as_of < snapshot.as_of:
            sys.stderr.write(
                f"[FAIL] --as-of {target_as_of} predates base snapshot {snapshot.as_of}; "
                f"refresh_pipeline cannot back-date.\n"
            )
            return 2

    # Plan A 2026-05-28 source-zone expansion: assemble the INSP artifacts
    # FIRST so we can extend the snapshot's source-zone primitive with
    # threshold-promoted INSP-only zones (spec §8.1 v1.2).
    _insp_artifacts = insp_block_assembler.assemble_insp_artifacts(
        INRB_UMIE_ARTIFACT_PATH if INRB_UMIE_ARTIFACT_PATH.exists() else None,
        INRB_UMIE_DATA_AS_OF,
        source_id=INRB_UMIE_SOURCE_ID,
    )
    # Decorate the per-zone rows with `sibling_hz_cluster` metadata from
    # data/zones.json so downstream renderers (brief, website) can group
    # sibling-HZ entries visually (spec §6.9 sibling-HZ doctrine).
    _block = _insp_artifacts.get("insp_per_zone_block")
    if _block is not None:
        try:
            _zones_payload = json.loads(
                (REPO_ROOT / "data" / "zones.json").read_text(encoding="utf-8")
            )
            _sibling_lookup = {
                entry["id"]: entry.get("sibling_hz_cluster")
                for entry in _zones_payload.get("zones", [])
                if entry.get("sibling_hz_cluster")
            }
            for _zone_id, _row in (_block.get("by_lovs_zone") or {}).items():
                sibling = _sibling_lookup.get(_zone_id)
                if sibling:
                    _row["sibling_hz_cluster"] = sibling
        except (OSError, json.JSONDecodeError, KeyError):
            # Decoration is best-effort; sync continues with bare block.
            pass
    # U1 (2026-05-28): re-base the corridor source-load primitive onto the INSP
    # per-health-zone block, so a single per-zone confirmed cascade (by_lovs_zone,
    # summing to 109 across the monitored zones) feeds the corridor model rather
    # than the earlier CDC/INSP hybrid (81). Forward-only: the calibration ledger
    # and its pinned blocks are read verbatim downstream by
    # carry_forward_calibration and are never touched here. The source-zone
    # conflict note is rebuilt from the same basis so provenance stays cohesive.
    if _block and _block.get("by_lovs_zone"):
        rebased_counts = _rebase_zone_counts_to_insp(_block)
        old_note = _source_zone_conflict_note(snapshot.zone_attributed_counts)
        new_note = _source_zone_conflict_note(rebased_counts)
        snapshot = dataclasses.replace(
            snapshot,
            affected_zones=tuple(sorted(rebased_counts)),
            zone_attributed_counts=rebased_counts,
            source_conflict_notes=tuple(
                new_note if note == old_note else note
                for note in snapshot.source_conflict_notes
            ),
        )
        print(
            f"Corridor re-base (U1): {len(rebased_counts)} INSP source zones, "
            "confirmed sum "
            f"{sum(int(r['confirmed']) for r in rebased_counts.values())}"
        )

    def _maybe_print(key: str, rc: lovs_reconciler.ReconciledCount | None) -> None:
        if rc is not None:
            print(f"  {key}: {rc.primary_value}")

    print(f"Snapshot as of {snapshot.as_of}")
    _maybe_print("confirmed", snapshot.reported_counts.get("confirmed"))
    _maybe_print("suspected_active", snapshot.reported_counts.get("suspected_active"))
    _maybe_print(
        "suspected_cumulative", snapshot.reported_counts.get("suspected_cumulative")
    )
    _maybe_print("deaths_confirmed", snapshot.reported_deaths.get("confirmed"))
    _maybe_print("deaths_suspected", snapshot.reported_deaths.get("suspected"))
    print(f"  affected zones: {snapshot.affected_zones}")
    print(
        "  zone-attributed confirmed total: "
        f"{sum(row.get('confirmed', 0) for row in snapshot.zone_attributed_counts.values())}"
    )
    source_review_geographies = load_source_review_geographies(snapshot.as_of[:10])
    if source_review_geographies:
        print(
            "  source-review geographies excluded from corridor load: "
            + ", ".join(row["zone_id"] for row in source_review_geographies)
        )

    # Visibility nowcast.
    visibility_history: tuple[lovs_reconciler.OutbreakSnapshot, ...] = ()
    vp = lovs_visibility.nowcast(snapshot, history=visibility_history, n_samples=1000)
    print(f"Visibility grade: {vp.visibility_grade}")
    print(f"  reporting completeness 50%: [{vp.reporting_completeness.lower_50:.4f}, {vp.reporting_completeness.upper_50:.4f}]")

    # Transmission plausibility (Bundibugyo Stage Two priors).
    tp = lovs_transmission.transmission_plausibility(
        snapshot,
        n_trajectories=1000,
        priors=lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO,
    )
    print(f"Transmission generations posterior:")
    max_gens = lovs_transmission.MAX_GENERATIONS
    for k in range(1, max_gens + 1):
        label = f"{k}+ (capped at {max_gens})" if k == max_gens else f"exactly {k}"
        print(f"  P({label}): {tp.generations_before_detection.get(k, 0.0):.3f}")
    p_three_or_more = sum(
        tp.generations_before_detection.get(k, 0.0) for k in range(3, max_gens + 1)
    )
    print(f"  P(>= 3 gens): {p_three_or_more:.3f}")

    # Corridor risk: source zones x target zones, 30-day horizon.
    # Target geography comes from data/snapshot_targets.json (single source of
    # truth) so a future snapshot cannot silently miss a watch target.
    target_zones = load_target_zones()
    print(f"Candidate target zones ({len(target_zones)}): {', '.join(target_zones)}")
    corridors = lovs_next_zone.next_zone_risk(
        snapshot=snapshot,
        visibility=vp,
        candidate_targets=target_zones,
        horizon_days=30,
        edge_weights=None,
        n_samples=500,
    )
    # Sort by adjusted upper-50, descending.
    sorted_corridors = sorted(
        corridors,
        key=lambda c: c.risk_visibility_adjusted.upper_50,
        reverse=True,
    )
    top = sorted_corridors[0]
    print(f"Top corridor: {top.source_geography_id} -> {top.target_geography_id}")
    print(f"  ascertainment-adjusted 50% range: [{top.risk_visibility_adjusted.lower_50:.4f}, {top.risk_visibility_adjusted.upper_50:.4f}]")

    # Carry the pre-committed calibration points forward from the immutable
    # ledger. CRITICAL: these are NEVER re-derived from the corridor ranking
    # above. Re-deriving them would overwrite points pinned on an earlier date
    # and break the pre-commitment contract the calibration scoring rests on.
    # See PIPELINE.md (c) and data/calibration-ledger.json.
    carried = carry_forward_calibration(snapshot.as_of)
    mode_b = carried["mode_b_hypotheses"]
    cal_blocks = calibration_blocks(snapshot.as_of, mode_b)
    cal_clock = cal_blocks[0]
    print(
        f"Carried forward {len(mode_b)} pinned calibration point(s) from ledger; "
        f"resolves {carried['resolves_at']} "
        f"({cal_clock['remaining_days']} day(s) remaining)"
    )

    zone_attributed_confirmed = sum(
        int(row.get("confirmed") or 0)
        for row in snapshot.zone_attributed_counts.values()
    )

    def _headline(metric_dict: dict, key: str, fallback_key: str = "") -> int | None:
        rc = metric_dict.get(key)
        if rc is None and fallback_key:
            rc = metric_dict.get(fallback_key)
        return rc.primary_value if rc is not None else None

    headline_confirmed = _headline(snapshot.reported_counts, "confirmed")
    # Suspected: prefer cumulative when present, fall back to active.
    headline_suspected = _headline(
        snapshot.reported_counts, "suspected_cumulative", "suspected_active"
    )
    headline_suspected_active = _headline(snapshot.reported_counts, "suspected_active")
    headline_suspected_cumulative = _headline(
        snapshot.reported_counts, "suspected_cumulative"
    )
    headline_deaths_confirmed = _headline(snapshot.reported_deaths, "confirmed")
    headline_deaths_suspected = _headline(snapshot.reported_deaths, "suspected")
    analysis_dependency_audit = [
        {
            "surface": "public_reporting_trajectory",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
                "suspected_active": headline_suspected_active,
                "suspected_cumulative": headline_suspected_cumulative,
                "deaths_confirmed": headline_deaths_confirmed,
                "deaths_suspected": headline_deaths_suspected,
            },
            "clock_basis": (
                "ECDC 27 May confirmed/suspected counts carry a May 26 "
                "data/report date (attributed to DRC MoH on 26 May); INRB "
                "build-2026-05-28 cross-corroborates DRC-only cases and supplies "
                "the headline DRC death primary on the same data date. "
                "Confirmed deaths and suspected deaths are reconciled as two "
                "independent series matching the INRB-published schema; the "
                "prior cross-class composition is retired."
            ),
        },
        {
            "surface": "visibility_module_c",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
                "suspected_cumulative": headline_suspected_cumulative,
            },
            "outputs": {
                "reporting_completeness_50": [
                    vp.reporting_completeness.lower_50,
                    vp.reporting_completeness.upper_50,
                ],
                "confirmation_backlog_50": [
                    vp.confirmation_backlog.lower_50,
                    vp.confirmation_backlog.upper_50,
                ],
            },
            "clock_basis": (
                "Snapshot-level visibility nowcast; prior-weighted with a "
                "Beta-Binomial update from the current confirmed/suspected "
                "headline pair."
            ),
        },
        {
            "surface": "confirmable_underlying_trajectory",
            "status": "updated",
            "inputs": {
                "confirmed_endpoint": headline_confirmed,
                "reporting_completeness_50": [
                    vp.reporting_completeness.lower_50,
                    vp.reporting_completeness.upper_50,
                ],
            },
            "outputs": {
                "endpoint_confirmable_50": [
                    round(headline_confirmed / vp.reporting_completeness.upper_50),
                    round(headline_confirmed / vp.reporting_completeness.lower_50),
                ]
            },
            "clock_basis": (
                "Confirmed endpoint is dated May 26 (DRC MoH per ECDC + INRB); "
                "the completeness posterior is the current snapshot posterior "
                "applied across the displayed confirmed-case series."
            ),
        },
        {
            "surface": "death_back_projection_and_grid",
            "status": "updated_snapshot_level",
            "inputs": {
                "deaths_confirmed": headline_deaths_confirmed,
                "deaths_suspected": headline_deaths_suspected,
            },
            "clock_basis": (
                "Deaths-back-projection now consumes two independent dated "
                "series: deaths_confirmed (lab-confirmed only, the apples-to-"
                "apples denominator for CFR work) and deaths_suspected (the "
                "broader under-investigation total, the upper bound for "
                "outbreak-size estimation). ECDC's 238 DRC suspected deaths, "
                "CDC 25 May (223), the DRC MoH dashboard aggregate (179, 24 "
                "May), and the earlier ECDC 25 May figure (119) are held as "
                "dated conflict anchors against the suspected series."
            ),
        },
        {
            "surface": "transmission_depth",
            "status": "updated",
            "inputs": {"confirmed": headline_confirmed},
            "clock_basis": (
                "Transmission plausibility uses the current reconciled confirmed "
                "headline count."
            ),
        },
        {
            "surface": "corridor_watchlist",
            "status": "source_attribution_lag",
            "inputs": {
                "zone_attributed_confirmed": zone_attributed_confirmed,
                "headline_confirmed": headline_confirmed,
                "unallocated_headline_confirmed": max(
                    0, headline_confirmed - zone_attributed_confirmed
                ),
            },
            "blocked_by": (
                "No reviewed May 26 cumulative health-zone table. The DRC MoH "
                "SitRep 009 dashboard rows and the INRB build-2026-05-28 bb8b7d5 "
                "processed health-zone layers (latest at 2026-05-26) remain "
                "source-review until their cumulative-table semantics and source "
                "labels are reviewed against original MoH/INSP publication context."
            ),
        },
    ]

    # Plan A 2026-05-28: scale-resilience-driven INSP per-zone surfaces.
    # `_insp_artifacts` was assembled at the top of main() for the source-zone
    # expansion; reuse it here so the assembler runs once per cycle.
    print(
        "INSP per-zone surface: "
        f"data_scale_used={_insp_artifacts['data_scale_used']!r}"
    )

    output = {
        "as_of": snapshot.as_of,
        # Forward-dated versioning (Model 1): `as_of` is the analytic/publication
        # date (the method re-cut date), which can run ahead of the data. The
        # snapshot's counts are pinned to `data_as_of` (the newest source DATA
        # date), so the preflight freshness gate checks evidence against this,
        # not the publication date.
        "data_as_of": INRB_UMIE_DATA_AS_OF.isoformat(),
        "outbreak_id": snapshot.outbreak_id,
        "reported_counts": {
            case_class: _count_output(count)
            for case_class, count in sorted(snapshot.reported_counts.items())
        },
        "reported_deaths": {
            death_class: _count_output(count)
            for death_class, count in sorted(snapshot.reported_deaths.items())
        },
        "affected_zones": list(snapshot.affected_zones),
        "zone_attributed_counts": snapshot.zone_attributed_counts,
        "zone_attributed_counts_source_ids": sorted(
            {
                str(row.get("source_id", ""))
                for row in snapshot.zone_attributed_counts.values()
                if row.get("source_id")
            }
        ),
        "source_review_geographies": source_review_geographies,
        "sources": list(snapshot.sources),
        "source_conflict_notes": list(snapshot.source_conflict_notes),
        "visibility": {
            "grade": vp.visibility_grade,
            "history_snapshot_count": len(visibility_history),
            "method_basis": "single_snapshot_bdbv_specific_prior_with_proxy_sensitivity",
            "method_caveat": (
                "No prior daily snapshot series is supplied to Module C for this release; "
                "reporting completeness and latency are prior-based, using the "
                "Rosello 2015 BDBV Isiro onset-to-notification distribution as the default. "
                "This is a historical prior-outbreak estimate, not a fitted 2026 "
                "reporting-delay estimate; Camacho 2015 EBOV-Zaire remains the proxy "
                "sensitivity comparator."
            ),
            "delay_prior": {
                "label": lovs_visibility.TOTAL_DELAY_LABEL,
                "gamma_shape_rate": list(lovs_visibility.TOTAL_DELAY_GAMMA),
                "evidence_chain_id": lovs_visibility.TOTAL_DELAY_EVIDENCE_CHAIN_ID,
            },
            "sensitivity_delay_priors": [
                {
                    "label": lovs_visibility.CAMACHO_EBOV_ZAIRE_DELAY_LABEL,
                    "gamma_shape_rate": list(lovs_visibility.CAMACHO_EBOV_ZAIRE_DELAY_GAMMA),
                    "evidence_chain_id": lovs_visibility.CAMACHO_EBOV_ZAIRE_DELAY_EVIDENCE_CHAIN_ID,
                }
            ],
            "evidence_chain_ids": list(lovs_visibility.PRIOR_EVIDENCE_CHAIN_IDS),
            "reporting_completeness_50": [
                vp.reporting_completeness.lower_50,
                vp.reporting_completeness.upper_50,
            ],
            "publication_latency_50": [
                vp.publication_latency_days.lower_50,
                vp.publication_latency_days.upper_50,
            ],
            "confirmation_backlog_50": [
                vp.confirmation_backlog.lower_50,
                vp.confirmation_backlog.upper_50,
            ],
        },
        "transmission": {
            "latent_active_chains_95": [
                tp.latent_active_chains.lower_95,
                tp.latent_active_chains.upper_95,
            ],
            # Full posterior over generations-before-detection bins.
            # The terminal bin (key == MAX_GENERATIONS) is censored: it holds the
            # mass for "MAX_GENERATIONS or more generations." Older clients that
            # only read keys {"1", "2", "3"} still resolve, but the brief and
            # webpage now render the full distribution.
            "generations": {
                str(k): tp.generations_before_detection.get(k, 0.0)
                for k in range(1, lovs_transmission.MAX_GENERATIONS + 1)
            },
            "generations_max_bin_is_censored": True,
            "generations_max_bin_key": str(lovs_transmission.MAX_GENERATIONS),
        },
        "corridors": [
            {
                "source": c.source_geography_id,
                "target": c.target_geography_id,
                "horizon_days": c.horizon_days,
                "risk_raw_lower_50": c.risk_raw.lower_50,
                "risk_raw_upper_50": c.risk_raw.upper_50,
                "risk_adj_lower_50": c.risk_visibility_adjusted.lower_50,
                "risk_adj_upper_50": c.risk_visibility_adjusted.upper_50,
                "risk_adj_lower_95": c.risk_visibility_adjusted.lower_95,
                "risk_adj_upper_95": c.risk_visibility_adjusted.upper_95,
                "drivers": list(c.drivers),
            }
            for c in sorted_corridors
        ],
        "analysis_dependency_audit": analysis_dependency_audit,
        "mode_b_hypotheses": mode_b,
        "calibration_clock": cal_clock,
        "calibration_blocks": cal_blocks,
        "scope_id": "epi:bdbv-uga-cod-2026",
        "resolves_at": carried["resolves_at"],
        "revision_note": (
            "Snapshot is published 2026-05-28 over the 26 May data date and "
            "supersedes the 26 May snapshot (forward-dated method evolution under "
            "painting/immutability, not an in-place re-cut). "
            "The new surveillance inputs are the ECDC 27 May page (citing 'On 26 "
            "May, the Ministry of Health in DRC reported a total of 121 confirmed "
            "cases (including 17 deaths) and 1 077 suspected cases (including 238 "
            "deaths) in Ituri, North Kivu, and South Kivu provinces. Uganda has "
            "reported seven confirmed cases, including one death.') and the INRB "
            "build-2026-05-28 GitHub release (DRC-only national_moh, data-as-of "
            "26 May), both byte-archived/hash-recorded. The promoted endpoints are "
            "1077 suspected DRC cases, 128 total confirmed (121 DRC + 7 Uganda), "
            "247 country-scope deaths (246 DRC suspected deaths from INRB/INSP + "
            "one Uganda confirmed death from ECDC/CDC), and 17 confirmed DRC "
            "deaths. These are the highest valid primaries on the latest date "
            "with explicit composition disclosure. The CDC 25 May page (906 suspected, 112 confirmed, 223 "
            "suspected deaths) and the ECDC 25 May page (101 confirmed, 904 "
            "suspected, 119 suspected deaths) drop to dated conflict anchors. CDC "
            "and WHO AFRO have not yet published an edition that catches up to "
            "the DRC MoH 26 May release. ECDC's 238 DRC suspected-death figure is "
            "retained as a same-day conflict anchor below INRB/INSP's 246. "
            "SitRep 009 and the DRC MoH dashboard rows "
            "remain source-review (no dateRapportage, no official PDF at "
            "capture). The corridor source-load is re-based (2026-05-28, "
            "forward-only) onto the INRB-UMIE/INSP per-health-zone series "
            "(by_lovs_zone): 18 monitored health zones carrying 109 "
            "zone-attributed confirmed cases (11 with confirmed cases), "
            "superseding the earlier SitRep-007 CDC/INSP hybrid. The unified "
            "confirmed cascade is 128 country-scope -> 121 DRC INSP-attributable "
            "national -> 109 zone-attributed + 12 DRC unallocated residual; the "
            "remaining 128-121=7 is the cross-border attribution lag (Uganda "
            "cases not in the DRC national series). Candidate target zones "
            "include arua-uga and "
            "nebbi-uga to close the documented Mahagi/Goli<->Arua cross-border "
            "blindspot. Pre-committed calibration points are carried forward "
            "UNCHANGED from data/calibration-ledger.json; no pin was re-derived. "
            "Mobility and confirmation-latency leverages are held as situational "
            "inputs (run_local) and are not injected into this provenance-strict "
            "public snapshot. See data/external_sources/."
        ),
        # Plan A 2026-05-28: scale-resilience-driven INSP per-zone surfaces.
        "data_scale_used": _insp_artifacts["data_scale_used"],
        "attribution_lag_disclosure": _insp_artifacts["attribution_lag_disclosure"],
    }
    if _insp_artifacts["insp_per_zone_block"] is not None:
        insp_block_out = dict(_insp_artifacts["insp_per_zone_block"])
        insp_block_out["upstream_reference"] = INSP_UPSTREAM_REFERENCE
        output["insp_per_zone_block"] = insp_block_out
    if _insp_artifacts["per_zone_under_ascertainment_bands"] is not None:
        output["per_zone_under_ascertainment_bands"] = _insp_artifacts[
            "per_zone_under_ascertainment_bands"
        ]

    # Atomic write: tempfile + os.replace (memory feedback_atomic_csv_writes).
    import os
    import tempfile

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(OUT_PATH.parent), delete=False
    ) as tmp_fh:
        json.dump(output, tmp_fh, indent=2)
        tmp_path = tmp_fh.name
    os.replace(tmp_path, OUT_PATH)
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
