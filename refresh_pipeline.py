#!/usr/bin/env python3
"""Refresh the LOVS pipeline output to the PHEIC-era situation as of 23 May 2026.

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

import hashlib
import json
import pathlib
import re
from datetime import date, datetime

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
    "ecdc-bdbv-drc-uga-2026-05-25-live",
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
        as_of="2026-05-25T23:59:59Z",
        pathogen="BDBV",
        country_scope=("COD", "UGA"),
        reported_counts={
            "suspected": lovs_reconciler.ReconciledCount(
                # Reconciliation doctrine: the endpoint is the highest valid primary
                # on the same count concept on the latest date. US CDC Current
                # Situation (25 May) reports 906 suspected DRC cases, above the ECDC
                # 25 May 904, the DRC MoH all-published-bulletins aggregate of 854
                # reported/suspected cases, and the earlier WHO DG "almost 750".
                minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "cases_suspected_drc_approx"),
                maximum=_figure(figures, "cdc-current-situation-2026-05-25", "cases_suspected"),
                primary_value=_figure(figures, "cdc-current-situation-2026-05-25", "cases_suspected"),
                primary_source_id="cdc-current-situation-2026-05-25",
                conflicting_source_ids=(
                    "afro-sitrep-01-2026-05-18",
                    "africa-cdc-phecs-2026-05-18",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "ecdc-bdbv-drc-uga-2026-05-21",
                    "cdc-current-situation-2026-05-21",
                    "who-dg-remarks-bdbv-2026-05-22",
                    "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24",
                    "cdc-current-situation-2026-05-24",
                    "ecdc-bdbv-drc-uga-2026-05-25-live",
                ),
            ),
            "confirmed": lovs_reconciler.ReconciledCount(
                # 17 May (WHO PHEIC statement; case data as of 16 May):
                # 8 Ituri + 2 Kampala = 10. The reported Kinshasa case was
                # deconfirmed by INRB and is excluded.
                # 19 May (ECDC): 30. 20 May (WHO DG): 51 DRC + 2 Kampala = 53.
                # 22 May (WHO DG): 82 DRC + 2 imported Uganda = 84. 23 May
                # (CDC): 83 DRC + 5 Uganda = 88. 24 May (CDC): 101 DRC + 5 Uganda
                # = 106. 25 May (CDC): 105 DRC + 7 Uganda = 112, the highest valid
                # primary on the latest date, preserving the country split. ECDC
                # 25 May reports 101 confirmed; the DRC MoH dashboard aggregate
                # reports 112 confirmed DRC cases (a different DRC-only composition).
                minimum=_figure(figures, "who-pheic-2026-05-17", "cases_confirmed"),
                maximum=_figure(figures, "cdc-current-situation-2026-05-25", "cases_confirmed_total"),
                primary_value=_figure(figures, "cdc-current-situation-2026-05-25", "cases_confirmed_total"),
                primary_source_id="cdc-current-situation-2026-05-25",
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
                    "ecdc-bdbv-drc-uga-2026-05-25-live",
                ),
            ),
        },
        reported_deaths=lovs_reconciler.ReconciledCount(
            # Reconciliation doctrine: the endpoint is the HIGHEST VALID primary on
            # the latest date. US CDC Current Situation (25 May) reports 223 suspected
            # DRC deaths, the highest valid primary, so it promotes. The DRC MoH
            # all-published-bulletins aggregate (179 registered deaths, 24 May, held
            # source-review for the missing PDF and table semantics) and the ECDC
            # 25 May figure (119 suspected deaths) are lower or non-valid and stay
            # dated conflict anchors, not promoted even though 179 previously led.
            minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "deaths_approx"),
            maximum=_figure(figures, "cdc-current-situation-2026-05-25", "deaths_suspected"),
            primary_value=_figure(figures, "cdc-current-situation-2026-05-25", "deaths_suspected"),
            primary_source_id="cdc-current-situation-2026-05-25",
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
                "ecdc-bdbv-drc-uga-2026-05-25-live",
            ),
        ),
        affected_zones=tuple(zone_counts.keys()),
        sources=snapshot_sources,
        case_definition_version=None,
        source_conflict_notes=(
            "Suspected/reported-case count spans 395 (Africa CDC PHECS, 18 May 2026) to 906 suspected DRC cases (US CDC Current Situation, 25 May 2026), the highest valid primary on the latest date. The ECDC 25 May figure of 904 and the DRC MoH all-published-bulletins dashboard aggregate of 854 reported cases (24 May) are retained as dated conflict anchors and not used to down-revise the higher CDC suspected-case endpoint.",
            "Deaths span 106 (Africa CDC PHECS, 18 May 2026) to 223 suspected DRC deaths (US CDC Current Situation, 25 May 2026), the highest valid primary on the latest date and the reported-deaths endpoint. Lower and source-review figures are retained as dated conflict anchors: the DRC MoH all-published-bulletins dashboard aggregate reports 179 registered deaths (24 May, source-review, no official PDF or table semantics), ECDC reports 119 suspected deaths (25 May), and WHO DG reported 177 suspected deaths (22 May). CDC and ECDC report the same suspected-death concept at different values (223 vs 119); the endpoint takes the higher valid primary and does not average across sources or down-revise to a lower peer. CDC also reports ten confirmed DRC deaths and one Uganda death.",
            "Confirmed count spans 10 (WHO PHEIC statement, 17 May 2026, case data as of 16 May: 8 Ituri + 2 Kampala; Kinshasa case deconfirmed) to 112 total country-scope confirmed cases (US CDC Current Situation, 25 May 2026: 105 DRC + 7 Uganda). ECDC 25 May reports 101 confirmed; the DRC MoH 24 May dashboard aggregate reports 112 confirmed DRC cases (a different DRC-only composition). These are retained as dated conflict anchors.",
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
    """Serialize a ReconciledCount with website / brief friendly key names."""
    return {
        "min": rc.minimum,
        "max": rc.maximum,
        "primary": rc.primary_value,
        "primary_source_id": rc.primary_source_id,
        "conflicting_source_ids": list(rc.conflicting_source_ids),
    }


def main() -> int:
    snapshot = build_snapshot()
    print(f"Snapshot as of {snapshot.as_of}")
    print(f"  confirmed: {snapshot.reported_counts['confirmed'].primary_value}")
    print(f"  suspected: {snapshot.reported_counts['suspected'].primary_value}")
    print(f"  deaths: {snapshot.reported_deaths.primary_value}")
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
    headline_confirmed = snapshot.reported_counts["confirmed"].primary_value
    headline_suspected = snapshot.reported_counts["suspected"].primary_value
    headline_deaths = snapshot.reported_deaths.primary_value
    analysis_dependency_audit = [
        {
            "surface": "public_reporting_trajectory",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
                "suspected": headline_suspected,
                "deaths": headline_deaths,
            },
            "clock_basis": (
                "CDC confirmed/suspected counts carry a May 24 data/report date; "
                "DRC MoH deaths carry a May 24 publication clock with no "
                "dateRapportage, so deaths are a headline input but not an "
                "ordinary dated death-trajectory node."
            ),
        },
        {
            "surface": "visibility_module_c",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
                "suspected": headline_suspected,
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
                "Confirmed endpoint is dated May 24; the completeness posterior "
                "is the current snapshot posterior applied across the displayed "
                "confirmed-case series."
            ),
        },
        {
            "surface": "death_back_projection_and_grid",
            "status": "updated_snapshot_level",
            "inputs": {"deaths": headline_deaths},
            "clock_basis": (
                "The 223-death input comes from the US CDC Current Situation 25 "
                "May, a dated-report source, so it updates the snapshot-level "
                "sensitivity calculation as a connected dated trajectory point. "
                "The DRC MoH dashboard aggregate (179, 24 May) and the ECDC 25 "
                "May figure (119) are held as conflict anchors and are not "
                "promoted, so no publication-clock-only endpoint is rendered."
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
                "No reviewed May 24 cumulative health-zone table. The DRC MoH "
                "SitRep 009 dashboard rows remain source-review because "
                "dateRapportage and the official PDF are absent at capture."
            ),
        },
    ]

    output = {
        "as_of": snapshot.as_of,
        "outbreak_id": snapshot.outbreak_id,
        "reported_counts": {
            case_class: _count_output(count)
            for case_class, count in snapshot.reported_counts.items()
        }
        | (
            {"deaths": _count_output(snapshot.reported_deaths)}
            if snapshot.reported_deaths is not None
            else {}
        ),
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
            "Snapshot is as of 2026-05-25 and supersedes the 24 May snapshot "
            "(fix-forward under painting/immutability, not an in-place re-cut). "
            "The new surveillance input is the US CDC Current Situation page "
            "published 2026-05-25 and archived/hash-recorded. CDC reports 906 "
            "suspected DRC cases, 112 confirmed (105 DRC + 7 Uganda), 223 "
            "suspected DRC deaths, ten confirmed DRC deaths, and one Uganda "
            "death. These are the highest valid primaries on the latest date and "
            "set the reported endpoints. The ECDC 25 May page (101 confirmed, 904 "
            "suspected, 119 suspected deaths) and the 24 May DRC MoH "
            "all-published-bulletins dashboard aggregate (854 reported cases, 112 "
            "confirmed DRC cases, 179 registered deaths) are retained as dated "
            "conflict anchors; CDC and ECDC report the same suspected-death "
            "concept at different values (223 vs 119), and the endpoint takes the "
            "higher valid primary without averaging or down-revision. The deaths "
            "input is a dated-report point (CDC 25 May), so doubling-time "
            "estimation uses source data/report dates. SitRep 009 stays "
            "source-review (no dateRapportage, no official PDF at capture). This "
            "is still not the fuller WHO AFRO/DRC line-list style release needed "
            "for zone-attributed counts. Candidate target zones include arua-uga "
            "and nebbi-uga to close the documented Mahagi/Goli<->Arua "
            "cross-border blindspot. The pre-committed calibration points are "
            "carried forward UNCHANGED from data/calibration-ledger.json; no pin "
            "was re-derived. Mobility and confirmation-latency leverages are held "
            "as situational inputs (run_local) and are not injected into this "
            "provenance-strict public snapshot. See data/external_sources/."
        ),
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
