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
import urllib.request
from datetime import date, datetime

from lovs import insp_block_assembler
from lovs import lovs_evidence
from lovs import lovs_next_zone
from lovs import lovs_live_ingest
from lovs import lovs_priors_bundibugyo
from lovs import lovs_reconciler
from lovs import release_contract
from lovs import lovs_active_queue_c2
from lovs import lovs_convergence
from lovs import sitrep_overlays
from lovs import sitrep_promotions
from lovs import lovs_transmission
from lovs import lovs_visibility
from lovs.insp_per_zone_loader import (
    INSPLoaderError,
    load_response_state,
    serialize_response_state_block,
)


REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "live-bdbv-2026-output.json"
LEDGER_PATH = DATA_DIR / "calibration-ledger.json"
# Sanitized public accountability extract for the pre-registered commitment block
# (the full pin set across all six forecast axes). This is a SEPARATE surface from
# the corridor calibration ledger (LEDGER_PATH): the ledger carries only
# 'source -> target' corridor pins with content-addressed hypothesis ids and is
# hash-guarded in carry_forward_calibration(); this file carries the honest public
# question / forecast_type / tier / baseline / resolution clause for every pin
# (corridor and non-corridor) with NO model internals. The two surfaces co-exist;
# neither guards nor mutates the other. Source of truth is the LOVS public record
# committed on origin/main; the pipeline reads whatever public block is committed
# here and carries it forward VERBATIM (never re-derives or re-levels a pin).
PUBLIC_COMMITMENTS_PATH = DATA_DIR / "public_calibration_commitments.json"
MANIFEST_PATH = DATA_DIR / "bundibugyo-2026" / "manifest.json"
TARGETS_CONFIG_PATH = DATA_DIR / "snapshot_targets.json"
PRIVATE_SOURCE_DIR = DATA_DIR / "bundibugyo-2026" / "private" / "sources"

# Plan A 2026-05-28: INRB-UMIE consortium release tarball used to populate the
# INSP per-zone surface. The path is the founder-machine development cache; CI
# resolves the same content hash via manifest.json. If the path does not exist
# the assembler falls back to data_scale_used="national" (spec §6.7).
INRB_UMIE_ARTIFACT_PATH = pathlib.Path("/tmp/build-0612-1dfdf1e.tar.gz")
INRB_UMIE_DATA_AS_OF = date(2026, 6, 11)
INRB_UMIE_SOURCE_ID = "inrb-umie-ebola-drc-2026-build-2026-06-12-1dfdf1e"
# Reference-upstream pointer (Option A): the per-health-zone counts are retained
# in this analytic output as the reconciliation-integrity substrate, but they are
# transcribed from the upstream INRB-UMIE/INSP release and explicitly attributed
# to it here. Human-facing deliverables (website per-zone table, spreadsheet
# per-zone sheet) reference this release rather than re-hosting the table.
INSP_UPSTREAM_REFERENCE = {
    "publisher": "INRB-UMIE consortium",
    "data_publisher": "INSP DRC",
    "repository": "https://github.com/INRB-UMIE/BDBV2026-Data",
    "build": "build-2026-06-12-1dfdf1e",
    "data_as_of": "2026-06-11",
    "terms": (
        "Per-health-zone series is INSP SitRep material; reuse with attribution "
        "to INSP and citation of the report number and date; confirm distribution "
        "terms with INSP before external republication."
    ),
}
_BARE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_entry(source_id: str) -> dict | None:
    if not MANIFEST_PATH.exists():
        return None
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for entry in manifest.get("entries", []):
        if entry.get("source_id") == source_id:
            return entry
    return None


def resolve_inrb_umie_artifact_path() -> pathlib.Path | None:
    """Return a verified INRB-UMIE release tarball path for per-zone surfaces."""
    entry = _manifest_entry(INRB_UMIE_SOURCE_ID) or {}
    expected_hash = str(entry.get("content_hash") or "")
    candidates = [
        INRB_UMIE_ARTIFACT_PATH,
        PRIVATE_SOURCE_DIR / f"{INRB_UMIE_SOURCE_ID}.tar.gz",
        PRIVATE_SOURCE_DIR / "build-2026-06-11-37f84e5.tar.gz",
    ]
    candidates.extend(sorted(PRIVATE_SOURCE_DIR.glob("*37f84e5*.tar.gz")))
    for path in candidates:
        if not path.exists():
            continue
        if expected_hash and _sha256_file(path) != expected_hash:
            continue
        return path

    url = str(entry.get("url") or "")
    if not url or not expected_hash:
        return None
    PRIVATE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PRIVATE_SOURCE_DIR / pathlib.Path(url).name
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "bdbv-2026-lovs/0.1.0 source-artifact-resolver",
                "Accept": "application/gzip,application/octet-stream,*/*",
            },
        )
        with urllib.request.urlopen(
            request,
            timeout=60,
            context=lovs_live_ingest._resolve_ssl_context(),
        ) as response:
            raw = response.read()
    except OSError as exc:
        print(f"INRB-UMIE artifact unavailable: {exc}")
        return None
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_hash:
        print(
            "INRB-UMIE artifact hash mismatch: "
            f"expected {expected_hash}, got {actual_hash}"
        )
        return None
    tmp_path.write_bytes(raw)
    os.replace(tmp_path, out_path)
    return out_path

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
    "inrb-umie-ebola-drc-2026-build-2026-06-01-b4cafc9",
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
    "nai-nia": "nia-nia",
    "nia-nia": "nia-nia",
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
    "nai nia": "nia-nia",
    "nai-nia": "nia-nia",
    "nia nia": "nia-nia",
    "nia-nia": "nia-nia",
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
    # Display-excluded zones (karisimbi-cod: never confirmed in any SitRep Table 1,
    # consolidated under Goma) are scrubbed from every other per-zone surface; drop
    # them here too so the prose zone count matches the rest (no 26-vs-25 drift). The
    # excluded zones carry zero confirmed, so the confirmed totals are unchanged.
    zone_counts = {
        zone_id: row
        for zone_id, row in zone_counts.items()
        if zone_id not in DISPLAY_EXCLUDED_ZONES
    }
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
        source_id.startswith(("inrb-umie", "inrb-sitrep"))
        for source_id in source_ids
    ):
        zones_with_confirmed = sum(
            1 for row in zone_counts.values() if int(row.get("confirmed") or 0) > 0
        )
        source_id_label = ", ".join(sorted(source_ids))
        published_at = max(
            str(row.get("source_published_at") or "")
            for row in zone_counts.values()
            if row.get("source_published_at")
        ) or "recorded in the source manifest"
        source_family = (
            "reviewed INSP SitRep Table 1"
            if all(source_id.startswith("inrb-sitrep") for source_id in source_ids)
            else "INRB-UMIE/INSP per-health-zone series"
        )
        return (
            f"Spatial model source zones use the {source_family} "
            f"({source_id_label}; source published through {published_at}), which "
            f"attributes {zone_confirmed_total} confirmed cases "
            f"across {source_zone_count} monitored health zones "
            f"({zones_with_confirmed} with confirmed cases). The national DRC and "
            "country-scope headline confirmed totals are higher; the difference is "
            "carried as unallocated and cross-border attribution-lag context "
            "rather than smeared across every source zone. Corridor source load "
            "therefore uses the reviewed per-health-zone confirmed vector."
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


def _confirmed_endpoint_clause(snapshot: "lovs_reconciler.OutbreakSnapshot") -> str:
    """Derive a human clause naming the confirmed headline endpoint.

    Built from the confirmed ReconciledCount (value + primary_source_id) and the
    snapshot data date, so the dependency-audit clock basis names the SitRep that
    the headline actually rides instead of a hardcoded number. Returns e.g.
    "SitRep #019 (378 confirmed, data as of 2026-06-02)". When the confirmed
    primary is not an INRB SitRep id, falls back to the source id + date.
    """
    confirmed = snapshot.reported_counts.get("confirmed")
    value = confirmed.primary_value if confirmed is not None else None
    source_id = confirmed.primary_source_id if confirmed is not None else ""
    data_date = snapshot.as_of[:10]
    match = re.search(r"inrb-sitrep-0*(\d+)", str(source_id))
    if match and value is not None:
        return (
            f"SitRep #{int(match.group(1)):03d} ({value} confirmed, data as of "
            f"{data_date})"
        )
    if value is not None and source_id:
        return f"{source_id} ({value} confirmed, data as of {data_date})"
    return f"current snapshot (data as of {data_date})"


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
    *,
    reported_counts_reason_overrides: dict[str, str] | None = None,
    reported_deaths_reason_overrides: dict[str, str] | None = None,
) -> lovs_reconciler.OutbreakSnapshot:
    """Clock a base snapshot forward to `target_as_of` with LOCF provenance.

    Used when no fresh source declarations have arrived since `base.as_of`
    but the snapshot cadence needs to advance. Every primary metric in
    `reported_counts` (e.g. confirmed, suspected_active,
    suspected_under_investigation, suspected_in_isolation) and every metric in
    `reported_deaths` (confirmed, suspected) is
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
    counts_overrides = reported_counts_reason_overrides or {}
    deaths_overrides = reported_deaths_reason_overrides or {}
    carried_counts = {
        k: v.with_carry_forward(base.as_of, counts_overrides.get(k, reason))
        for k, v in base.reported_counts.items()
    }
    carried_deaths = {
        k: v.with_carry_forward(base.as_of, deaths_overrides.get(k, reason))
        for k, v in base.reported_deaths.items()
    }
    return dataclasses.replace(
        base,
        as_of=target_as_of,
        reported_counts=carried_counts,
        reported_deaths=carried_deaths,
    )


# INRB SitRep ingest constants
# ----------------------------------------------------------------------------
# Uganda anchor: ECDC 27 May (the most recent country-scope source covering
# Uganda) reports 7 confirmed cases and 1 confirmed death. INRB SitReps are
# DRC-only and do not republish Uganda. Country-scope figures compose the
# DRC SitRep value with this Uganda anchor.
UGANDA_CONFIRMED_ANCHOR = 7
UGANDA_CONFIRMED_DEATHS_ANCHOR = 1
UGANDA_ANCHOR_SOURCE_ID = "ecdc-bdbv-drc-uga-2026-05-27"
CDC_CURRENT_SITUATION_2026_05_25_SOURCE_ID = "cdc-current-situation-2026-05-25"
CDC_CURRENT_SITUATION_2026_06_01_SOURCE_ID = "cdc-current-situation-2026-06-01"
_SITREP_PROMOTIONS_BY_NUMBER = sitrep_promotions.reviewed_promotions_by_number()


def _sitrep_promotion(number: int) -> dict:
    try:
        return _SITREP_PROMOTIONS_BY_NUMBER[number]
    except KeyError as exc:
        raise RuntimeError(f"missing reviewed SitRep promotion #{number}") from exc


def latest_c2_active_queue_inputs(as_of: str) -> dict[str, Any] | None:
    """Return the newest reviewed SitRep with a complete active queue for C2."""
    latest: tuple[str, int, dict[str, Any]] | None = None
    for number, payload in _SITREP_PROMOTIONS_BY_NUMBER.items():
        review = payload.get("review", {}) or {}
        if payload.get("status") != "reviewed":
            continue
        if review.get("source_review_status") != "reviewed":
            continue
        if review.get("ready_for_model_use") is not True:
            continue
        data_as_of = str(payload.get("data_as_of", ""))
        if not data_as_of or data_as_of > as_of:
            continue
        figures = payload.get("figures", {}) or {}
        confirmed = figures.get("country_scope_confirmed_total")
        active_suspected = figures.get("suspected_active_total")
        if not isinstance(confirmed, int) or not isinstance(active_suspected, int):
            continue
        row = (data_as_of, int(number), figures)
        if latest is None or row[:2] > latest[:2]:
            latest = row
    if latest is None:
        return None
    data_as_of, number, figures = latest
    # This path is reached only when the latest snapshot omitted its own active-
    # suspected queue, so every field surfaced here is carried forward from the
    # reused SitRep. Tag it explicitly with the originating SitRep number, its
    # data date, and the per-field carried-forward reason so the C2 fallback
    # never looks like a fresh figure.
    result: dict[str, Any] = {
        "source_data_as_of": data_as_of,
        "source_sitrep_number": number,
        "carried_forward": True,
        "carriedForwardReason": "active_queue_omitted_from_latest_sitrep",
        "confirmed_active_total": int(figures["country_scope_confirmed_total"]),
        "active_suspected_total": int(figures["suspected_active_total"]),
    }
    for source_key, target_key in (
        ("cas_suspects_en_cours_investigation", "suspected_under_investigation"),
        ("cas_suspects_en_isolement", "suspected_in_isolation"),
    ):
        value = figures.get(source_key)
        if isinstance(value, int):
            result[target_key] = value
    return result


def _c2_inputs_provenance(c2_inputs: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Build the C2 input-provenance tag from the resolved c2_inputs.

    Surfaces, on the C2 projection, whether the active-suspected queue is the
    latest snapshot's own published headline or a carried-forward reuse of an
    earlier SitRep, plus the originating SitRep number, the queue's data date,
    and the carried-forward reason. Deterministic: no wall-clock; everything is
    derived from the already-resolved c2_inputs and the snapshot as-of date.
    """
    carried_forward = bool(c2_inputs.get("carried_forward"))
    source_data_as_of = str(c2_inputs.get("source_data_as_of") or "")
    provenance: dict[str, Any] = {
        "source_sitrep_number": c2_inputs.get("source_sitrep_number"),
        "source_data_as_of": source_data_as_of,
        "carried_forward": carried_forward,
        "active_queue_basis": c2_inputs.get("active_queue_basis", "suspected_active_total"),
    }
    if carried_forward:
        provenance["carriedForwardFrom"] = source_data_as_of[:10]
        provenance["carriedForwardReason"] = str(
            c2_inputs.get("carriedForwardReason")
            or "active_queue_omitted_from_latest_sitrep"
        )
    return provenance

# SitRep #015 (published 2026-05-30, data cutoff 2026-05-29). Headline tiles
# from the PDF parsed during 2026-06-01 ingest sweep:
#   - cumul_cas_confirmes = 263 (DRC; Ituri 245 + Nord-Kivu 15 + Sud-Kivu 3)
#   - cumul_deces_parmi_confirmes = 42 (DRC; Ituri 35 + Nord-Kivu 6 + Sud-Kivu 1)
#   - cumul_cas_suspects = 349 (NOT 3491; the superscript on the PDF tile is
#     a footnote marker, not a digit. SitRep #014 reported the same field as
#     "349*" with footnote: "Revised downward; number of suspect cases was
#     revised down after investigation and sampling confirmed some and ruled
#     out others". SitRep #015 continues the same revised cumulative.)
#   - gueris = 2 (cured)
#   - 22 zones affected (Ituri 14 + Nord-Kivu 7 + Sud-Kivu 1). Six new zones
#     vs the May 28 INSP build: Aungba, Gety, Lita, Mangala (Ituri),
#     Kalunguta, Kyondo (Nord-Kivu). These are added to affected_zones but
#     NOT to the corridor watchlist (which is locked at the May 28 base).
#   - new_confirmed_29_mai = 54; new_suspected_29_mai = 60; new_deaths_suspects_29_mai = 13
# Country-scope composition (with Uganda anchor):
#   - confirmed = 263 (DRC) + 7 (UGA) = 270
#   - deaths_confirmed = 42 (DRC) + 1 (UGA) = 43
_SITREP_015 = _sitrep_promotion(15)
INRB_SITREP_015_SOURCE_ID = _SITREP_015["source_id"]
SITREP_015_NEW_ZONES = ("aungba", "gety", "lita", "mangala", "kalunguta", "kyondo")
INRB_SITREP_015_FIGURES = _SITREP_015["figures"]

# SitRep #016 (published 2026-05-31, data cutoff 2026-05-30). Headline tile
# count widened from 7 to a refined schema:
#   - cumul_cas_confirmes = 282 (DRC, with footnote *donnees en cours d'harmonisation)
#   - cumul_deces_parmi_confirmes = 42 (DRC; unchanged from #015)
#   - cas_confirmes_actifs = 238 (= cumul - deaths - cured = 282 - 42 - 2)
#   - cas_suspects_en_cours_investigation = 220 (NEW field; active stock)
#   - cas_suspects_en_isolement = 101 (NEW field; active stock under isolation)
#   - gueris = 2
#   - taux_suivi_contacts_pct = 45.2
# Suspected active total = 220 + 101 = 321. Country-scope deaths_confirmed
# remains 42 DRC + 1 UGA = 43.
_SITREP_016 = _sitrep_promotion(16)
INRB_SITREP_016_SOURCE_ID = _SITREP_016["source_id"]
INRB_SITREP_016_FIGURES = _SITREP_016["figures"]

# SitRep #017 (published 2026-06-01, data cutoff 2026-05-31; this is the
# Revised edition SitRep_MVE_RDC_N017_01_06_2026-Revised). Headline tiles:
#   - cumul_cas_confirmes = 321 (DRC; monotone over 282)
#   - cumul_deces_parmi_confirmes = 48 (DRC; first movement off 42 since #014)
#   - cas_suspects_en_cours_investigation = 116 (NEW value; was 220 in #016)
#   - cas_suspects_en_isolement = 104 (was 101 in #016)
#   - gueris = 6 (was 2)
# The active suspected stock therefore DROPS to 116 + 104 = 220 (from 321 in
# #016). That is a genuine surveillance movement (the active investigation
# queue being worked down), NOT a data error, and is logged as such.
#
# cas_confirmes_actifs: the extracted PDF cell reads 238, but 238 is provably
# the prior #016 active value lingering in the layout (the pdftotext headline
# extractor picked the stale cell). The arithmetic identity cumul - deaths -
# cured = 321 - 48 - 6 = 267 is the correct DRC active stock, so we compute it
# rather than ingest the stale 238. Country-scope active = 267 DRC + 7 UGA
# presumed-active = 274 (Uganda publishes no separate active count; the 7
# imported confirmed are carried as presumed-active at the country-scope
# composition layer, identical to the #016 treatment).
_SITREP_017 = _sitrep_promotion(17)
INRB_SITREP_017_SOURCE_ID = _SITREP_017["source_id"]
INRB_SITREP_017_FIGURES = _SITREP_017["figures"]

# SitRep #018 (published 2026-06-02, data cutoff 2026-06-01). CDC's
# 2026-06-01 public situation page is the fresh Uganda anchor and introduces a
# separate probable tier for Uganda; probable is never folded into confirmed.
_SITREP_018 = _sitrep_promotion(18)
INRB_SITREP_018_SOURCE_ID = _SITREP_018["source_id"]
INRB_SITREP_018_FIGURES = _SITREP_018["figures"]

# SitRep #019 (published 2026-06-03, data cutoff 2026-06-02). This edition
# drops the separate under-investigation stock and suspected-death headline, but
# still publishes the DRC confirmed/death headline, recovered count, Table 1
# health-zone confirmed/death distribution, and Table 4 care/isolation census.
_SITREP_019 = _sitrep_promotion(19)
INRB_SITREP_019_SOURCE_ID = _SITREP_019["source_id"]
INRB_SITREP_019_FIGURES = _SITREP_019["figures"]
# Mambasa is named in the SitRep Table 1 health-zone list (Ituri) but carries no
# INSP per-zone attribution in the vendored bridge build, so it enters as a
# named-affected display zone (confirmed-0 in the source-attributed table, with
# review_reasons), exactly as the published 2026-06-06 snapshot represents it.
SITREP_019_NEW_ZONES = ("logo", "rimba", "mambasa")

# SitRep #020 (published 2026-06-04, data cutoff 2026-06-03). Same headline
# schema as #019: DRC confirmed/death headline, recovered, Table 1 health-zone
# distribution, and Table 4 care/isolation census. No new affected zone vs #019;
# Uganda stays a 15/1 composition anchor (no fresher Uganda source this cycle).
_SITREP_020 = _sitrep_promotion(20)
INRB_SITREP_020_SOURCE_ID = _SITREP_020["source_id"]
INRB_SITREP_020_FIGURES = _SITREP_020["figures"]
SITREP_020_NEW_ZONES = ()

# SitRep #021 (published 2026-06-05, data cutoff 2026-06-04). Same headline schema
# as #020: DRC confirmed/death headline, recovered, Table 1 health-zone distribution,
# and Table 4 care/isolation census. No new affected zone vs #020; Uganda anchor is
# refreshed to the reviewed Uganda MoH 6 Jun / WHO DON606 figure (19 confirmed / 2 deaths). The DRC
# 452 headline carries an explicit "Donnees en cours d'harmonisation" caveat: the +71
# one-day jump is a retrospective harmonization back-fill, not 71 same-day incident cases.
_SITREP_021 = _sitrep_promotion(21)
INRB_SITREP_021_SOURCE_ID = _SITREP_021["source_id"]
INRB_SITREP_021_FIGURES = _SITREP_021["figures"]
SITREP_021_NEW_ZONES = ()

# Generalized SitRep #022+ promotions derive their affected-zone set directly
# from reviewed Table 1 rows. A newly named health zone enters affected_zones;
# the May-29-pinned INSP per-zone corridor source-load primitive carries it at
# confirmed=0 (no corridor shift) until an INSP per-zone table attributes it, the
# same treatment every freshly named zone has received (see the sitrep_only_zones
# carry-forward in build_snapshot). Missing zone metadata is a build blocker.
_LOVS_ZONE_IDS: set[str] | None = None


def _known_lovs_zone_ids() -> set[str]:
    global _LOVS_ZONE_IDS
    if _LOVS_ZONE_IDS is None:
        payload = json.loads((DATA_DIR / "zones.json").read_text(encoding="utf-8"))
        _LOVS_ZONE_IDS = {
            str(entry.get("id"))
            for entry in payload.get("zones", [])
            if isinstance(entry, dict) and entry.get("id")
        }
    return _LOVS_ZONE_IDS


def _promotion_table_zone_ids(number: int, figures: dict[str, Any]) -> tuple[str, ...]:
    table = figures.get("health_zone_table") or {}
    rows = table.get("rows") if isinstance(table, dict) else None
    if not rows:
        return ()
    known_zone_ids = _known_lovs_zone_ids()
    zone_ids: set[str] = set()
    missing: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("zone") or "").strip()
        if not name or "ventil" in name.lower():
            continue
        zone_id = sitrep_overlays.per_zone_canonical_id(name)
        if zone_id in DISPLAY_EXCLUDED_ZONES:
            continue
        if zone_id not in known_zone_ids:
            missing.append(f"{name} -> {zone_id}")
            continue
        zone_ids.add(zone_id)
    if missing:
        raise RuntimeError(
            f"reviewed SitRep #{number:03d} names health zones missing from "
            f"data/zones.json: {', '.join(sorted(missing))}"
        )
    return tuple(sorted(zone_ids))


def apply_sitrep_015(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #015 (May 29) headline tiles onto a May 28 baseline.

    INRB's May 29 declaration supersedes the May 28 baseline for the fields it
    publishes. Fields the SitRep does not publish (cumul deces suspects tile,
    explicit suspected_active split) are carried forward from the baseline
    with reason=source_schema_evolved: the upstream schema changed which fields
    appear on the headline dashboard between cycles. The base figures still
    exist in the prior INRB build; LOCF preserves them with explicit provenance
    rather than dropping them.
    """
    base_as_of = snapshot.as_of
    target_as_of = "2026-05-29T23:59:59Z"
    prior_confirmed = snapshot.reported_counts.get("confirmed")
    new_counts = dict(snapshot.reported_counts)
    # Country-scope confirmed = INRB SitRep #015 DRC (263) + Uganda anchor (7)
    # from ECDC 27 May (the last source covering Uganda). INRB SitReps do not
    # republish Uganda; the Uganda value carries forward at the country-scope
    # composition layer rather than living inside the SitRep itself.
    country_scope_confirmed = INRB_SITREP_015_FIGURES["country_scope_confirmed_total"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=128,
        maximum=country_scope_confirmed,
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_015_SOURCE_ID,
        conflicting_source_ids=(
            (prior_confirmed.primary_source_id,) + (
                prior_confirmed.conflicting_source_ids
                if prior_confirmed is not None
                else ()
            )
            + (UGANDA_ANCHOR_SOURCE_ID,)
        ),
    )
    # Cumulative suspected tier retired 2026-06-02: SitRep #015's cumul cas
    # suspects (349, itself a SitRep #014 downward revision from a pre-
    # investigation 1077) is no longer carried as a cumulative reported_count.
    # The revision history is preserved in the source_conflict_notes below for
    # audit provenance. Drop any inherited cumulative-suspected count.
    new_counts.pop("suspected_cumulative", None)
    # Recovered (gueris) — first introduced as a headline tile in SitRep #013;
    # surfaced here for display continuity. Not in the required schema set.
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=2, maximum=2, primary_value=2,
        primary_source_id=INRB_SITREP_015_SOURCE_ID,
        conflicting_source_ids=(),
    )
    # Suspected active: SitRep #015 does not publish active-stock fields.
    # Carry forward from baseline (if any) with source_schema_evolved.
    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_015_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=18,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_015_SOURCE_ID,
        conflicting_source_ids=(prior_d_conf.primary_source_id,) + (
            prior_d_conf.conflicting_source_ids if prior_d_conf is not None else ()
        ) + (UGANDA_ANCHOR_SOURCE_ID,),
    )
    # deaths_suspected: SitRep #015 dropped the cumul deces suspects tile.
    # Carry forward the prior 246 figure with source_schema_evolved.
    if "suspected" in new_deaths:
        new_deaths["suspected"] = new_deaths["suspected"].with_carry_forward(
            base_as_of, "source_schema_evolved"
        )
    # Extend affected_zones with the six new zones SitRep #015 added relative
    # to the May 28 INSP base. New zones are added to affected_zones but NOT
    # to the corridor watchlist (which is locked at the May 28 base).
    new_affected_zones = tuple(
        sorted(set(snapshot.affected_zones) | set(SITREP_015_NEW_ZONES))
    )
    new_notes = snapshot.source_conflict_notes + (
        "INRB SitRep #015 (data cutoff 2026-05-29, published 2026-05-30) "
        "promoted the DRC headline tiles: cumul cas confirmes 263, cumul "
        "deces parmi confirmes 42, cumul cas suspects 349 (the value on "
        "the SitRep tile carries a superscript footnote marker, not a "
        "trailing digit; the cumulative-suspect count was revised down "
        "from a pre-investigation 1077 in SitRep #014 with footnote "
        "'revised downward after investigation confirmed some and ruled "
        "out others'), gueris 2. Country-scope confirmed = 263 DRC + 7 "
        "UGA (ECDC 27 May, the last source covering Uganda) = 270. "
        "Country-scope confirmed deaths = 42 DRC + 1 UGA = 43. SitRep #015 "
        "expanded the zone-affected footprint from 16 (May 28 INSP base) "
        "to 22 zones nationally; new zones (Aungba, Gety, Lita, Mangala, "
        "Kalunguta, Kyondo) are added to affected_zones but not to the "
        "locked corridor watchlist. The cumul deces suspects tile was "
        "not included in #015; the 246 value from the prior INRB build "
        "(data-as-of 26 May) is carried forward with reason "
        "source_schema_evolved.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        affected_zones=new_affected_zones,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_015_SOURCE_ID, UGANDA_ANCHOR_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_016(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #016 (May 30) headline tiles onto a post-#015 snapshot.

    SitRep #016 is the cycle that introduced the refined schema: confirmed
    cases bifurcate into cumulative (282) and active (238); suspected cases
    bifurcate into active (under investigation 220 + in isolation 101 = 321)
    and the cumulative line continues. Deaths confirmed unchanged at 42.

    The 282 figure carries a footnote *donnees en cours d'harmonisation; this
    is preserved as a sibling note string on the new ReconciledCount rather
    than altering the primary value.
    """
    target_as_of = "2026-05-30T23:59:59Z"
    base_as_of = snapshot.as_of
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    # Country-scope confirmed = INRB SitRep #016 DRC (282) + Uganda anchor (7)
    country_scope_confirmed = INRB_SITREP_016_FIGURES["country_scope_confirmed_total"]
    country_scope_confirmed_active = INRB_SITREP_016_FIGURES["cas_confirmes_actifs_drc"] + UGANDA_CONFIRMED_ANCHOR
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=270,  # post-#015 country-scope
        maximum=country_scope_confirmed,  # 289
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_016_SOURCE_ID,
        conflicting_source_ids=(prior_conf.primary_source_id,) + (
            prior_conf.conflicting_source_ids if prior_conf is not None else ()
        ) + (UGANDA_ANCHOR_SOURCE_ID,),
    )
    # Active confirmed at the country-scope level is the DRC active (238)
    # plus the Uganda confirmed (7); for May 30 Uganda has no separate
    # active count and the 7 are presumed-active country-scope.
    new_counts["confirmed_active"] = lovs_reconciler.ReconciledCount(
        minimum=country_scope_confirmed_active,
        maximum=country_scope_confirmed_active,
        primary_value=country_scope_confirmed_active,  # 245
        primary_source_id=INRB_SITREP_016_SOURCE_ID,
        conflicting_source_ids=(UGANDA_ANCHOR_SOURCE_ID,),
    )
    new_counts["suspected_active"] = lovs_reconciler.ReconciledCount(
        minimum=321, maximum=321, primary_value=321,
        primary_source_id=INRB_SITREP_016_SOURCE_ID,
        conflicting_source_ids=(),
    )
    # Cumulative suspected tier retired 2026-06-02: drop any inherited
    # cumulative-suspected count rather than carrying it forward. #016 already
    # replaced the cumulative tile with the operational active-stock split.
    new_counts.pop("suspected_cumulative", None)
    if "recovered" in new_counts:
        # Recovered count unchanged at 2 per SitRep #016; re-stamp the source.
        new_counts["recovered"] = lovs_reconciler.ReconciledCount(
            minimum=2, maximum=2, primary_value=2,
            primary_source_id=INRB_SITREP_016_SOURCE_ID,
            conflicting_source_ids=(),
        )
    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_016_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=country_scope_deaths_confirmed,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_016_SOURCE_ID,
        conflicting_source_ids=(prior_d_conf.primary_source_id,) + (
            prior_d_conf.conflicting_source_ids if prior_d_conf is not None else ()
        ) + (UGANDA_ANCHOR_SOURCE_ID,),
    )
    # deaths_suspected: still not republished in #016. Re-stamp the LOCF
    # provenance to the most recent base_as_of so the brief shows the freshest
    # available "as of" date for the carried-forward value.
    if "suspected" in new_deaths:
        new_deaths["suspected"] = new_deaths["suspected"].with_carry_forward(
            base_as_of, "source_schema_evolved"
        )
    new_notes = snapshot.source_conflict_notes + (
        "INRB SitRep #016 (data cutoff 2026-05-30, published 2026-05-31) "
        "promoted the refined schema: cumul cas confirmes 282 (DRC, with "
        "footnote donnees en cours d'harmonisation), cas confirmes actifs "
        "238 (= 282 - 42 deaths - 2 cured), cas suspects en cours "
        "d'investigation 220, cas suspects en isolement 101, suspected_active "
        "total 321. Country-scope confirmed = 282 DRC + 7 UGA (ECDC 27 May "
        "anchor) = 289. Country-scope confirmed-active = 238 DRC + 7 UGA = "
        "245. Country-scope confirmed deaths = 42 DRC + 1 UGA = 43. The "
        "cumul deces suspects tile remains absent; the 246 May 26 value is "
        "carried forward with reason source_schema_evolved.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_016_SOURCE_ID, UGANDA_ANCHOR_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_017(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #017 (May 31) headline tiles onto a post-#016 snapshot.

    SitRep #017 (Revised edition, published 2026-06-01, data cutoff 2026-05-31)
    is the first cycle to move the confirmed-death count (42 -> 48) and the
    first to show the active suspected stock being worked DOWN: under
    investigation 220 -> 116 and in isolation 101 -> 104, so suspected_active
    falls 321 -> 220. That drop is a genuine surveillance movement (the active
    investigation queue shrinking under the active response), not a data loss,
    and is recorded as such in the source note.

    Confirmed active is recomputed from the arithmetic identity (cumul - deaths
    - cured = 321 - 48 - 6 = 267 DRC) because the PDF's printed active cell
    (238) is a stale carry of the #016 value. Country-scope confirmed deaths
    moves to 48 DRC + 1 UGA = 49; deaths_suspected remains unpublished and is
    carried forward with source_schema_evolved (never an input to Method 2).
    """
    target_as_of = "2026-05-31T23:59:59Z"
    base_as_of = snapshot.as_of
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = INRB_SITREP_017_FIGURES["country_scope_confirmed_total"]
    country_scope_confirmed_active = INRB_SITREP_017_FIGURES["country_scope_confirmed_active"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=289,  # post-#016 country-scope
        maximum=country_scope_confirmed,  # 328
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(prior_conf.primary_source_id,) + (
            prior_conf.conflicting_source_ids if prior_conf is not None else ()
        ) + (UGANDA_ANCHOR_SOURCE_ID,),
    )
    # Active confirmed = DRC active (267, recomputed via the cumul-deaths-cured
    # identity, NOT the stale 238 PDF cell) + Uganda presumed-active (7) = 274.
    new_counts["confirmed_active"] = lovs_reconciler.ReconciledCount(
        minimum=country_scope_confirmed_active,
        maximum=country_scope_confirmed_active,
        primary_value=country_scope_confirmed_active,  # 274
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(UGANDA_ANCHOR_SOURCE_ID,),
    )
    # Operational suspected axis (point-prevalence, national-only, NEVER summed
    # into confirmed). The cumulative suspected tier was retired 2026-06-02; the
    # only suspected quantities that survive are the operational caseload split
    # INRB publishes at the latest SitRep: cases under investigation and cases
    # in isolation, plus their total. These are a single-snapshot stock, not a
    # cumulative incidence count.
    #
    # Suspected active DROPS 321 -> 220 (116 under investigation + 104 in
    # isolation). Real surveillance movement, not an error. The prior #016
    # active stock (the demoted higher figure) is kept in the conflict trail so
    # the drawdown stays auditable.
    susp_under_investigation = INRB_SITREP_017_FIGURES[
        "cas_suspects_en_cours_investigation"
    ]
    susp_in_isolation = INRB_SITREP_017_FIGURES["cas_suspects_en_isolement"]
    new_counts["suspected_under_investigation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_under_investigation,
        maximum=susp_under_investigation,
        primary_value=susp_under_investigation,  # 116
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_016_SOURCE_ID,),
    )
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_in_isolation,
        maximum=susp_in_isolation,
        primary_value=susp_in_isolation,  # 104
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_016_SOURCE_ID,),
    )
    prior_susp_active = snapshot.reported_counts.get("suspected_active")
    new_counts["suspected_active"] = lovs_reconciler.ReconciledCount(
        minimum=220, maximum=220, primary_value=220,
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_susp_active.primary_source_id,) + prior_susp_active.conflicting_source_ids)
            if prior_susp_active is not None
            else (INRB_SITREP_016_SOURCE_ID,)
        ),
    )
    # Cumulative suspected tier retired 2026-06-02: if a prior cycle left a
    # suspected_cumulative count on the snapshot, drop it from the cumulative
    # surface entirely (it is not carried forward and is never republished).
    new_counts.pop("suspected_cumulative", None)
    # Recovered (gueris) advances 2 -> 6. The prior figure (the demoted lower
    # value) is kept in the conflict trail so the advance stays auditable.
    prior_recovered = snapshot.reported_counts.get("recovered")
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=6, maximum=6, primary_value=6,
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_recovered.primary_source_id,) + prior_recovered.conflicting_source_ids)
            if prior_recovered is not None
            else (INRB_SITREP_016_SOURCE_ID,)
        ),
    )
    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_017_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=country_scope_deaths_confirmed,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,  # 49
        primary_source_id=INRB_SITREP_017_SOURCE_ID,
        conflicting_source_ids=(prior_d_conf.primary_source_id,) + (
            prior_d_conf.conflicting_source_ids if prior_d_conf is not None else ()
        ) + (UGANDA_ANCHOR_SOURCE_ID,),
    )
    # deaths_suspected: still not republished in #017. Re-stamp the LOCF
    # provenance to the freshest base_as_of with source_schema_evolved. This
    # value (246) must never feed Imperial Method 2.
    if "suspected" in new_deaths:
        new_deaths["suspected"] = new_deaths["suspected"].with_carry_forward(
            base_as_of, "source_schema_evolved"
        )
    new_notes = snapshot.source_conflict_notes + (
        "INRB SitRep #017 (Revised; data cutoff 2026-05-31, published "
        "2026-06-01) promoted: cumul cas confirmes 321 (DRC), cumul deces "
        "parmi confirmes 48 (DRC; first movement off 42 since #014), cas "
        "suspects en cours d'investigation 116 (down from 220 in #016), cas "
        "suspects en isolement 104 (up from 101), so suspected_active falls "
        "321 -> 220 (a genuine surveillance movement, the active queue being "
        "worked down under the response, not data loss), gueris 6 (up from 2). "
        "Confirmed active is recomputed from the identity 321 - 48 - 6 = 267 "
        "DRC because the PDF's printed active cell (238) is a stale carry of "
        "the #016 value. Country-scope confirmed = 321 DRC + 7 UGA (ECDC 27 "
        "May anchor) = 328; confirmed-active = 267 DRC + 7 UGA presumed-active "
        "= 274; confirmed deaths = 48 DRC + 1 UGA = 49. The cumul deces "
        "suspects tile remains absent; the 246 May 26 value is carried forward "
        "with reason source_schema_evolved and is never an input to Method 2.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_017_SOURCE_ID, UGANDA_ANCHOR_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_018(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #018 (June 1) and the CDC June 1 Uganda anchor."""
    target_as_of = "2026-06-01T23:59:59Z"
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = INRB_SITREP_018_FIGURES["country_scope_confirmed_total"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=328,
        maximum=country_scope_confirmed,
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_conf.primary_source_id,) + prior_conf.conflicting_source_ids)
            if prior_conf is not None
            else (INRB_SITREP_017_SOURCE_ID,)
        ),
    )
    new_counts["probable"] = lovs_reconciler.ReconciledCount(
        minimum=1,
        maximum=1,
        primary_value=1,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(CDC_CURRENT_SITUATION_2026_05_25_SOURCE_ID,),
    )
    new_counts.pop("confirmed_active", None)
    susp_under_investigation = INRB_SITREP_018_FIGURES[
        "cas_suspects_en_cours_investigation"
    ]
    susp_in_isolation = INRB_SITREP_018_FIGURES["cas_suspects_en_isolement"]
    new_counts["suspected_under_investigation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_under_investigation,
        maximum=susp_under_investigation,
        primary_value=susp_under_investigation,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_017_SOURCE_ID,),
    )
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_in_isolation,
        maximum=susp_in_isolation,
        primary_value=susp_in_isolation,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_017_SOURCE_ID,),
    )
    prior_susp_active = snapshot.reported_counts.get("suspected_active")
    new_counts["suspected_active"] = lovs_reconciler.ReconciledCount(
        minimum=INRB_SITREP_018_FIGURES["suspected_active_total"],
        maximum=INRB_SITREP_018_FIGURES["suspected_active_total"],
        primary_value=INRB_SITREP_018_FIGURES["suspected_active_total"],
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_susp_active.primary_source_id,) + prior_susp_active.conflicting_source_ids)
            if prior_susp_active is not None
            else (INRB_SITREP_017_SOURCE_ID,)
        ),
    )
    new_counts.pop("suspected_cumulative", None)

    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_018_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=49,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_d_conf.primary_source_id,) + prior_d_conf.conflicting_source_ids)
            if prior_d_conf is not None
            else (INRB_SITREP_017_SOURCE_ID,)
        ),
    )
    new_deaths["probable"] = lovs_reconciler.ReconciledCount(
        minimum=1,
        maximum=1,
        primary_value=1,
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(CDC_CURRENT_SITUATION_2026_05_25_SOURCE_ID,),
    )
    new_deaths["suspected"] = lovs_reconciler.ReconciledCount(
        minimum=INRB_SITREP_018_FIGURES["deaths_suspected_drc"],
        maximum=INRB_SITREP_018_FIGURES["deaths_suspected_drc"],
        primary_value=INRB_SITREP_018_FIGURES["deaths_suspected_drc"],
        primary_source_id=INRB_SITREP_018_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_017_SOURCE_ID,),
    )
    new_notes = snapshot.source_conflict_notes + (
        "INRB SitRep #018 / INRB-UMIE build-2026-06-02-32e9ebd "
        "(data cutoff 2026-06-01, published 2026-06-02) promoted the DRC "
        "headline tiles: cumul cas confirmes 344, cumul deces parmi confirmes "
        "60, cas suspects en cours d'investigation 116, cas suspects en "
        "isolement 173, active suspected total 289, suspected deaths 242, "
        "country-scope confirmed 355, country-scope confirmed deaths 61, "
        "1 probable case, and 1 probable death. The probable tier is surfaced "
        "separately and is not added into confirmed. Older CDC DRC rows remain "
        "dated conflict anchors where they differ from the fresh reviewed "
        "SitRep18 promotion endpoint.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_018_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_019(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #019 (June 2) without fabricating dropped fields."""
    target_as_of = "2026-06-02T23:59:59Z"
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = INRB_SITREP_019_FIGURES["country_scope_confirmed_total"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=355,
        maximum=country_scope_confirmed,
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_019_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_conf.primary_source_id,) + prior_conf.conflicting_source_ids)
            if prior_conf is not None
            else (INRB_SITREP_018_SOURCE_ID,)
        ),
    )
    if "probable" in new_counts:
        new_counts["probable"] = new_counts["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    new_counts.pop("confirmed_active", None)
    new_counts.pop("suspected_under_investigation", None)
    new_counts.pop("suspected_active", None)
    new_counts.pop("suspected_cumulative", None)
    prior_recovered = snapshot.reported_counts.get("recovered")
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=6,
        maximum=6,
        primary_value=6,
        primary_source_id=INRB_SITREP_019_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_recovered.primary_source_id,) + prior_recovered.conflicting_source_ids)
            if prior_recovered is not None
            else (INRB_SITREP_018_SOURCE_ID,)
        ),
    )
    susp_in_isolation = INRB_SITREP_019_FIGURES["cas_suspects_en_isolement"]
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_in_isolation,
        maximum=susp_in_isolation,
        primary_value=susp_in_isolation,
        primary_source_id=INRB_SITREP_019_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_018_SOURCE_ID,),
    )

    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_019_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=61,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_019_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_d_conf.primary_source_id,) + prior_d_conf.conflicting_source_ids)
            if prior_d_conf is not None
            else (INRB_SITREP_018_SOURCE_ID,)
        ),
    )
    if "probable" in new_deaths:
        new_deaths["probable"] = new_deaths["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    # SitRep19 does not publish cumulative suspected deaths or a separate
    # suspected-death stock. Do not carry the SitRep18 suspected-death value onto
    # the current-cycle surface as if it were republished.
    new_deaths.pop("suspected", None)

    new_affected_zones = tuple(
        sorted(set(snapshot.affected_zones) | set(SITREP_019_NEW_ZONES))
    )
    new_notes = snapshot.source_conflict_notes + (
        "INRB/INSP SitRep #019 (data cutoff 2026-06-02, published "
        "2026-06-03) was visually reviewed because parser output was partial. "
        "It promotes the DRC headline tiles: cumul cas confirmes 363, cumul "
        "deces parmi confirmes 62, patients en isolement-hospitalisation 206, "
        "gueris 6, and contact follow-up 45.5%. Country-scope confirmed = 363 "
        "DRC + 15 Uganda anchor (CDC 2 Jun, refreshed from the carried-forward "
        "7) = 378; country-scope confirmed deaths = 62 DRC + 1 Uganda anchor = "
        "63. Table 4 splits the 206 patients in isolation "
        "into 47 confirmed and 159 suspected, so suspected_in_isolation advances "
        "to 159. SitRep #019 does not publish the separate cas suspects en cours "
        "d'investigation stock, the total active suspected queue, suspected "
        "deaths, or a complete national 24h lab table; those fields are omitted "
        "from the current-cycle operational/model surface rather than fabricated. "
        "Table 1 health-zone confirmed/death rows are preserved as source "
        "evidence, including the explicit unventilated Ituri row (94 confirmed, "
        "10 deaths) that must not be distributed to named zones. A parser draft "
        "that assigned two confirmed deaths to Goma is rejected: the rendered PDF "
        "Table 1 shows Goma deaths = 0 and the Nord-Kivu subtotal already sums "
        "to 13 without Goma deaths.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        affected_zones=new_affected_zones,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_019_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_020(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #020 (June 3) without fabricating dropped fields."""
    target_as_of = "2026-06-03T23:59:59Z"
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = INRB_SITREP_020_FIGURES["country_scope_confirmed_total"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=378,
        maximum=country_scope_confirmed,
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_020_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_conf.primary_source_id,) + prior_conf.conflicting_source_ids)
            if prior_conf is not None
            else (INRB_SITREP_019_SOURCE_ID,)
        ),
    )
    if "probable" in new_counts:
        new_counts["probable"] = new_counts["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    new_counts.pop("confirmed_active", None)
    new_counts.pop("suspected_under_investigation", None)
    new_counts.pop("suspected_active", None)
    new_counts.pop("suspected_cumulative", None)
    prior_recovered = snapshot.reported_counts.get("recovered")
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=7,
        maximum=7,
        primary_value=7,
        primary_source_id=INRB_SITREP_020_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_recovered.primary_source_id,) + prior_recovered.conflicting_source_ids)
            if prior_recovered is not None
            else (INRB_SITREP_019_SOURCE_ID,)
        ),
    )
    susp_in_isolation = INRB_SITREP_020_FIGURES["cas_suspects_en_isolement"]
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_in_isolation,
        maximum=susp_in_isolation,
        primary_value=susp_in_isolation,
        primary_source_id=INRB_SITREP_020_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_019_SOURCE_ID,),
    )

    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_020_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=63,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_020_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_d_conf.primary_source_id,) + prior_d_conf.conflicting_source_ids)
            if prior_d_conf is not None
            else (INRB_SITREP_019_SOURCE_ID,)
        ),
    )
    if "probable" in new_deaths:
        new_deaths["probable"] = new_deaths["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    # SitRep20 does not publish cumulative suspected deaths or a separate
    # suspected-death stock; do not carry a prior suspected-death value onto the
    # current-cycle surface as if it were republished.
    new_deaths.pop("suspected", None)

    new_affected_zones = tuple(
        sorted(set(snapshot.affected_zones) | set(SITREP_020_NEW_ZONES))
    )
    new_notes = snapshot.source_conflict_notes + (
        "INRB/INSP SitRep #020 (data cutoff 2026-06-03, published "
        "2026-06-04) was visually reviewed against the rendered PDF (page 1 "
        "headline and page 3 Table 1) because parser output was partial. It "
        "promotes the DRC headline tiles: cumul cas confirmes 381, cumul deces "
        "parmi confirmes 64, patients en isolement-hospitalisation 233, gueris "
        "7, and contact follow-up 55.5%. Country-scope confirmed = 381 DRC + 15 "
        "Uganda anchor (carried from the CDC 2 Jun anchor; no fresher Uganda "
        "source this cycle) = 396; country-scope confirmed deaths = 64 DRC + 1 "
        "Uganda anchor = 65. Eighteen new confirmed cases were notified on 3 "
        "June in Bunia (12), Rwampara (4), Damas (1) and Lita (1); the two new "
        "confirmed deaths were among already-confirmed patients (1 Bunia, 1 "
        "Rwampara). Table 4 splits the 233 patients in isolation into 62 "
        "confirmed and 171 suspected, so suspected_in_isolation advances to "
        "171. SitRep #020 does not publish the separate cas suspects en cours "
        "d'investigation stock, the total active suspected queue, suspected "
        "deaths, or a complete national 24h lab table; those fields are omitted "
        "from the current-cycle operational/model surface rather than "
        "fabricated. Table 1 health-zone confirmed/death rows are preserved as "
        "source evidence, including the explicit unventilated Ituri row (94 "
        "confirmed, 10 deaths) that must not be distributed to named zones.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        affected_zones=new_affected_zones,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_020_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def apply_sitrep_021(
    snapshot: lovs_reconciler.OutbreakSnapshot,
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote SitRep #021 (June 4) without fabricating dropped fields."""
    target_as_of = "2026-06-04T23:59:59Z"
    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = INRB_SITREP_021_FIGURES["country_scope_confirmed_total"]
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        # 396 = prior-cycle (#020) country-scope confirmed floor; cumulative never regresses.
        minimum=396,
        maximum=country_scope_confirmed,
        primary_value=country_scope_confirmed,
        primary_source_id=INRB_SITREP_021_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_conf.primary_source_id,) + prior_conf.conflicting_source_ids)
            if prior_conf is not None
            else (INRB_SITREP_020_SOURCE_ID,)
        ),
    )
    if "probable" in new_counts:
        new_counts["probable"] = new_counts["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    new_counts.pop("confirmed_active", None)
    new_counts.pop("suspected_under_investigation", None)
    new_counts.pop("suspected_active", None)
    new_counts.pop("suspected_cumulative", None)
    prior_recovered = snapshot.reported_counts.get("recovered")
    recovered_val = INRB_SITREP_021_FIGURES["gueris"]
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=recovered_val,
        maximum=recovered_val,
        primary_value=recovered_val,
        primary_source_id=INRB_SITREP_021_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_recovered.primary_source_id,) + prior_recovered.conflicting_source_ids)
            if prior_recovered is not None
            else (INRB_SITREP_020_SOURCE_ID,)
        ),
    )
    susp_in_isolation = INRB_SITREP_021_FIGURES["cas_suspects_en_isolement"]
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=susp_in_isolation,
        maximum=susp_in_isolation,
        primary_value=susp_in_isolation,
        primary_source_id=INRB_SITREP_021_SOURCE_ID,
        conflicting_source_ids=(INRB_SITREP_020_SOURCE_ID,),
    )

    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = INRB_SITREP_021_FIGURES[
        "country_scope_confirmed_deaths"
    ]
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        # 65 = prior-cycle (#020) country-scope confirmed-death floor.
        minimum=65,
        maximum=country_scope_deaths_confirmed,
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=INRB_SITREP_021_SOURCE_ID,
        conflicting_source_ids=(
            ((prior_d_conf.primary_source_id,) + prior_d_conf.conflicting_source_ids)
            if prior_d_conf is not None
            else (INRB_SITREP_020_SOURCE_ID,)
        ),
    )
    if "probable" in new_deaths:
        new_deaths["probable"] = new_deaths["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )
    # SitRep21 does not publish cumulative suspected deaths; do not carry a prior
    # suspected-death value onto the current-cycle surface as if republished.
    new_deaths.pop("suspected", None)

    new_affected_zones = tuple(
        sorted(set(snapshot.affected_zones) | set(SITREP_021_NEW_ZONES))
    )
    new_notes = snapshot.source_conflict_notes + (
        "INRB/INSP SitRep #021 (data cutoff 2026-06-04, published "
        "2026-06-05) was visually reviewed against the rendered PDF (page 1 "
        "headline and page 3 Table 1) because parser output was partial. It "
        "promotes the DRC headline tiles: cumul cas confirmes 452, cumul deces "
        "parmi confirmes 82, patients en isolement-hospitalisation 258, gueris "
        "8, and contact follow-up 57.8%. The 452 confirmed carries an explicit "
        "'Donnees en cours d'harmonisation' caveat on page 1: the one-day +71 "
        "jump (381 to 452) is a harmonization back-fill of retrospective "
        "cohort-1 (14-23 May) and cohort-2 (25 May to 3 Jun) onset cases, not "
        "71 same-day incident cases. Country-scope confirmed = 452 DRC + 19 "
        "Uganda (the reviewed Uganda MoH 6 Jun anchor, corroborated by WHO "
        "DON606 8 Jun: 19 confirmed, 2 deaths) = 471; country-scope confirmed "
        "deaths = 82 DRC + 2 Uganda = 84. Cumulative confirmed deaths "
        "advance 64 to 82 (+18), led by Mongbwalu 10 to 21 (+11); the "
        "faits-saillants 21 deces is a deaths-among-new-cases figure for the "
        "day, distinct from the +18 cumulative delta. Table 4 splits the 258 "
        "patients in isolation into 72 confirmed and 186 suspected, so "
        "suspected_in_isolation advances to 186. SitRep #021 does not publish "
        "the separate cas suspects en cours d'investigation stock, the total "
        "active suspected queue, suspected deaths, or a complete national 24h "
        "lab table; those fields are omitted from the current-cycle "
        "operational/model surface rather than fabricated. Table 1 health-zone "
        "confirmed/death rows are preserved as source evidence, including the "
        "explicit unventilated Ituri row (94 confirmed, 10 deaths) that must "
        "not be distributed to named zones.",
    )
    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        affected_zones=new_affected_zones,
        sources=tuple(sorted(
            set(snapshot.sources)
            | {INRB_SITREP_021_SOURCE_ID}
        )),
        source_conflict_notes=new_notes,
    )


def _promotion_endpoint(number: int, promotion: dict[str, Any]) -> str:
    data_as_of = str(promotion.get("data_as_of") or "")
    if not _BARE_DATE_RE.fullmatch(data_as_of):
        raise RuntimeError(f"reviewed SitRep #{number:03d} has invalid data_as_of {data_as_of!r}")
    return f"{data_as_of}T23:59:59Z"


def _promotion_figure(figures: dict[str, Any], key: str, number: int) -> int:
    value = figures.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"reviewed SitRep #{number:03d} missing integer figure {key!r}")
    return value


def _promotion_source_ids(figures: dict[str, Any], primary_source_id: str) -> tuple[str, ...]:
    """Return supporting source ids from a reviewed promotion payload.

    Same-layout SitRep promotions can carry country-scope composition anchors
    such as Uganda MoH or WHO. Keep those on the reconciled-count conflict list
    so website/workbook citations expose the full count basis.
    """
    out: list[str] = []
    for raw in figures.get("source_ids", []):
        source_id = str(raw or "")
        if source_id and source_id != primary_source_id and source_id not in out:
            out.append(source_id)
    return tuple(out)


def _promotion_note(number: int, promotion: dict[str, Any]) -> str:
    figures = promotion.get("figures") or {}
    health_table = figures.get("health_zone_table") or {}
    unvent = {}
    if isinstance(health_table, dict):
        for row in health_table.get("rows") or []:
            if isinstance(row, dict) and "ventil" in str(row.get("zone", "")).lower():
                unvent = row
                break
    country_scope_confirmed = figures.get("country_scope_confirmed_total")
    country_scope_deaths = figures.get("country_scope_confirmed_deaths")
    return (
        f"INRB/INSP SitRep #{number:03d} (data cutoff {promotion['data_as_of']}, "
        f"published {str(promotion.get('published_at', ''))[:10]}) was visually reviewed "
        "and promoted from the reviewed promotion payload. It advances the DRC headline "
        f"tiles: cumul cas confirmes {_promotion_figure(figures, 'cumul_cas_confirmes_drc', number)}, "
        f"cumul deces parmi confirmes {_promotion_figure(figures, 'cumul_deces_parmi_confirmes_drc', number)}, "
        f"patients en isolement-hospitalisation {_promotion_figure(figures, 'patients_en_isolement_hospitalisation', number)}, "
        f"gueris {_promotion_figure(figures, 'gueris', number)}, and contact follow-up "
        f"{figures.get('contact_followup_rate_pct')}%. Country-scope confirmed = "
        f"{country_scope_confirmed}; country-scope confirmed deaths = {country_scope_deaths}. "
        f"Table 4 splits the isolation census into {_promotion_figure(figures, 'cas_confirmes_en_isolement', number)} "
        f"confirmed and {_promotion_figure(figures, 'cas_suspects_en_isolement', number)} suspected, so "
        "suspected_in_isolation is used as the current operational suspected axis. "
        "The separate under-investigation stock, total active suspected queue, and suspected deaths "
        "are omitted unless the reviewed source publishes them. "
        f"Table 1 health-zone confirmed/death rows are preserved as display evidence; "
        f"the explicit unventilated row ({unvent.get('confirmed')} confirmed, "
        f"{unvent.get('confirmed_deaths')} deaths) is not distributed to named zones."
    )


def apply_reviewed_sitrep_promotion(
    snapshot: lovs_reconciler.OutbreakSnapshot,
    number: int,
    promotion: dict[str, Any],
) -> lovs_reconciler.OutbreakSnapshot:
    """Promote a reviewed same-layout SitRep without hardcoding its number.

    SitRep #022+ follows the #019-#021 source shape: DRC headline, Table 1
    health-zone display rows, and Table 4 isolation split. Country-scope totals
    are pre-composed in the reviewed promotion payload so Uganda anchoring stays
    evidence-gated outside the math kernel.
    """
    figures = promotion.get("figures") or {}
    source_id = str(promotion["source_id"])
    supporting_source_ids = _promotion_source_ids(figures, source_id)
    target_as_of = _promotion_endpoint(number, promotion)

    new_counts = dict(snapshot.reported_counts)
    prior_conf = snapshot.reported_counts.get("confirmed")
    country_scope_confirmed = _promotion_figure(figures, "country_scope_confirmed_total", number)
    prior_confirmed_value = prior_conf.primary_value if prior_conf is not None else country_scope_confirmed
    new_counts["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=min(prior_confirmed_value, country_scope_confirmed),
        maximum=max(prior_confirmed_value, country_scope_confirmed),
        primary_value=country_scope_confirmed,
        primary_source_id=source_id,
        conflicting_source_ids=(
            ((prior_conf.primary_source_id,) + prior_conf.conflicting_source_ids)
            if prior_conf is not None
            else ()
        ) + supporting_source_ids,
    )
    for stale_key in (
        "confirmed_active",
        "suspected_under_investigation",
        "suspected_active",
        "suspected_cumulative",
    ):
        new_counts.pop(stale_key, None)

    prior_recovered = snapshot.reported_counts.get("recovered")
    recovered_val = figures.get("country_scope_recovered_total")
    if not isinstance(recovered_val, int) or isinstance(recovered_val, bool):
        recovered_val = _promotion_figure(figures, "gueris", number)
    new_counts["recovered"] = lovs_reconciler.ReconciledCount(
        minimum=min(prior_recovered.primary_value if prior_recovered else recovered_val, recovered_val),
        maximum=max(prior_recovered.primary_value if prior_recovered else recovered_val, recovered_val),
        primary_value=recovered_val,
        primary_source_id=source_id,
        conflicting_source_ids=(
            ((prior_recovered.primary_source_id,) + prior_recovered.conflicting_source_ids)
            if prior_recovered is not None
            else ()
        ) + supporting_source_ids,
    )

    susp_in_isolation = _promotion_figure(figures, "cas_suspects_en_isolement", number)
    prior_susp = snapshot.reported_counts.get("suspected_in_isolation")
    new_counts["suspected_in_isolation"] = lovs_reconciler.ReconciledCount(
        minimum=min(prior_susp.primary_value if prior_susp else susp_in_isolation, susp_in_isolation),
        maximum=max(prior_susp.primary_value if prior_susp else susp_in_isolation, susp_in_isolation),
        primary_value=susp_in_isolation,
        primary_source_id=source_id,
        conflicting_source_ids=(
            ((prior_susp.primary_source_id,) + prior_susp.conflicting_source_ids)
            if prior_susp is not None
            else ()
        ),
    )

    new_deaths = dict(snapshot.reported_deaths)
    prior_d_conf = snapshot.reported_deaths.get("confirmed")
    country_scope_deaths_confirmed = _promotion_figure(
        figures, "country_scope_confirmed_deaths", number
    )
    prior_death_value = (
        prior_d_conf.primary_value if prior_d_conf is not None else country_scope_deaths_confirmed
    )
    new_deaths["confirmed"] = lovs_reconciler.ReconciledCount(
        minimum=min(prior_death_value, country_scope_deaths_confirmed),
        maximum=max(prior_death_value, country_scope_deaths_confirmed),
        primary_value=country_scope_deaths_confirmed,
        primary_source_id=source_id,
        conflicting_source_ids=(
            ((prior_d_conf.primary_source_id,) + prior_d_conf.conflicting_source_ids)
            if prior_d_conf is not None
            else ()
        ) + supporting_source_ids,
    )
    new_deaths.pop("suspected", None)
    if "probable" in new_deaths:
        new_deaths["probable"] = new_deaths["probable"].with_carry_forward(
            snapshot.as_of, "awaiting_next_publication"
        )

    # Union every reviewed Table 1 health zone into affected_zones. Rows are
    # already source-reviewed; residual/unventilated rows are excluded, and missing
    # zone metadata blocks the build instead of silently dropping a new zone.
    table_zone_ids = _promotion_table_zone_ids(number, figures)
    new_affected_zones = tuple(
        sorted(set(snapshot.affected_zones) | set(table_zone_ids))
    )

    return dataclasses.replace(
        snapshot,
        as_of=target_as_of,
        reported_counts=new_counts,
        reported_deaths=new_deaths,
        affected_zones=new_affected_zones,
        sources=tuple(sorted(set(snapshot.sources) | {source_id} | set(supporting_source_ids))),
        source_conflict_notes=snapshot.source_conflict_notes + (_promotion_note(number, promotion),),
    )


def _latest_reviewed_promotion_at_or_before(as_of: str) -> tuple[int, dict[str, Any]] | None:
    candidates = [
        (number, payload)
        for number, payload in _SITREP_PROMOTIONS_BY_NUMBER.items()
        if str(payload.get("data_as_of") or "")[:10] <= as_of[:10]
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (str(item[1].get("data_as_of") or ""), item[0]))


# Display-excluded zones (presentation layer; 2026-06-05 founder decision). The
# INSP per-zone bridge projects ``karisimbi-cod`` as a confirmed-0 monitored
# zone, but SitRep Table 1 lists "Goma" (not Karisimbi) for Nord-Kivu, so
# karisimbi surfacing as a shaded/tooltipped "confirmed case zone" carrying 0
# cases reads as a data error to a viewer. The founder directed it be scrubbed
# from every display surface. This is a PRESENTATION removal applied at
# serialization: the reconciliation kernel is unchanged because the residual is
# computed from the FULL INRB zone-sum (not the bridge projection) and karisimbi
# carries 0 confirmed, so no national/zone total moves.
DISPLAY_EXCLUDED_ZONES: tuple[str, ...] = ("karisimbi-cod",)


def _scrub_display_excluded_zones(output: dict[str, Any]) -> None:
    """Remove display-excluded zones from every per-zone surface in ``output``,
    keeping derived counts self-consistent. Call AFTER the per-zone/response
    blocks are assembled and BEFORE ``responseState`` is built, so the
    by_zone/by_province roll-ups exclude the zone automatically."""
    excluded = set(DISPLAY_EXCLUDED_ZONES)
    if not excluded:
        return
    if isinstance(output.get("affected_zones"), list):
        output["affected_zones"] = [
            z for z in output["affected_zones"] if z not in excluded
        ]
    zac = output.get("zone_attributed_counts")
    if isinstance(zac, dict):
        for zone in excluded:
            zac.pop(zone, None)
    ipz = output.get("insp_per_zone_block")
    if isinstance(ipz, dict):
        blz = ipz.get("by_lovs_zone")
        if isinstance(blz, dict):
            for zone in excluded:
                blz.pop(zone, None)
        ca = ipz.get("coverage_audit")
        if isinstance(ca, dict):
            for bucket, vals in list(ca.items()):
                if isinstance(vals, list):
                    ca[bucket] = [z for z in vals if z not in excluded]
    bands = output.get("per_zone_under_ascertainment_bands")
    if isinstance(bands, dict):
        blz = bands.get("by_lovs_zone")
        if isinstance(blz, dict):
            for zone in excluded:
                blz.pop(zone, None)
            stats = bands.get("coverage_stats")
            if isinstance(stats, dict):
                modulated = sum(
                    1
                    for row in blz.values()
                    if isinstance(row, dict)
                    and row.get("lo") is not None
                    and row.get("hi") is not None
                )
                total = len(blz)
                stats["total_zones"] = total
                stats["modulated_zones"] = modulated
                stats["species_default_fallback_zones"] = total - modulated
    rsb = output.get("response_state_block")
    if isinstance(rsb, dict):
        blz = rsb.get("by_lovs_zone")
        if isinstance(blz, dict):
            for zone in excluded:
                blz.pop(zone, None)


def _build_current_province_response(snapshot_as_of: str) -> dict[str, Any] | None:
    """Province-level CURRENT operational axis for the website's stale-aware
    per-zone cards.

    INSP stopped publishing zone-level contacts/care after 2026-05-30, so the
    per-zone response block (``responseState.by_zone``) trails the headline. This
    surfaces the LATEST reviewed SitRep's per-province and national operational
    state (contacts under follow-up, contacts seen, isolation census) so the
    website renders current province context instead of stale 30-May per-zone
    values. The website reads it as ``responseState.provinceCurrent`` and decides
    staleness there (``isPerZoneResponseStale`` = provinceCurrent.dataAsOf >
    responseState.data_as_of). Returns None when the latest promotion carries no
    reviewed province_operational block (older cycles), so the field is simply
    omitted rather than fabricated."""
    found = _latest_reviewed_promotion_at_or_before(snapshot_as_of)
    if found is None:
        return None
    _number, promotion = found
    figures = promotion.get("figures") or {}
    meta = {
        "dataAsOf": str(promotion.get("data_as_of"))[:10],
        "sourceId": str(promotion.get("source_id")),
        "scopeNote": (
            "Province-level operational axis from the latest SitRep. INSP stopped "
            "publishing zone-level contacts/care after 2026-05-30; this is the "
            "current province and national operational state, not a per-zone value."
        ),
    }

    # Schema A (SitRep <= 026): an explicit reviewed province_operational block.
    prov_op = figures.get("province_operational")
    if isinstance(prov_op, dict) and prov_op.get("byProvince"):
        national = prov_op.get("national")
        if not isinstance(national, dict) or not national:
            op_tables = figures.get("operational_tables") or {}
            contacts = op_tables.get("contacts_total") or {}
            pm = op_tables.get("patient_movement_total") or {}
            national = {
                "contactsUnderFollowUp": contacts.get("contacts_under_follow_up"),
                "contactsSeen": contacts.get("contacts_seen_24h"),
                "followUpCoveragePct": contacts.get("followup_rate_pct"),
                "patientsInIsolation": pm.get("patients_in_isolation_end_day"),
                "confirmedInIsolation": pm.get("confirmed_in_isolation"),
                "suspectsInIsolation": pm.get("suspects_in_isolation"),
                "admissions24h": pm.get("admissions_24h"),
                "escapes24h": pm.get("escaped_suspect_or_confirmed_24h"),
            }
        # National bed occupancy (SitRep isolation/care census occupancy). Per-province occupancy is
        # not published every cycle; only the national figure is surfaced, so the website Care panel
        # shows the CURRENT national occupancy (not a stale per-province roll-up) and per-province
        # census-only until INSP republishes a per-province patient-movement table.
        _pm_occ = ((figures.get("operational_tables") or {}).get("patient_movement_total") or {}).get(
            "occupancy_percent"
        )
        if isinstance(_pm_occ, (int, float)) and isinstance(national, dict):
            national = {**national, "bedOccupancyPct": _pm_occ}
        return {**meta, "byProvince": prov_op["byProvince"], "national": national}

    # Schema B (SitRep 027+): the promotion dropped province_operational and instead splits the
    # operational axis across operational_tables. Derive the SAME provinceCurrent shape from what it
    # DOES carry, so a promotion-schema change can no longer silently drop provinceCurrent (and,
    # cascading, the convergence nowcast that reads its national contact axis). Per-province isolation
    # is national-only in this schema, so byProvince carries contacts and national carries both.
    op_tables = figures.get("operational_tables")
    if isinstance(op_tables, dict) and isinstance(op_tables.get("contacts_total"), dict):
        contacts = op_tables["contacts_total"]
        pm = op_tables.get("patient_movement_total") or {}
        by_province = {
            str(row["province"]): {
                "contactsUnderFollowUp": row.get("contacts_under_follow_up"),
                "contactsSeen": row.get("contacts_seen_24h"),
                "followUpCoveragePct": row.get("followup_rate_pct"),
            }
            for row in (op_tables.get("contacts_by_province") or [])
            if isinstance(row, dict) and row.get("province")
        }
        return {
            **meta,
            "byProvince": by_province,
            "national": {
                "contactsUnderFollowUp": contacts.get("contacts_under_follow_up"),
                "contactsSeen": contacts.get("contacts_seen_24h"),
                "followUpCoveragePct": contacts.get("followup_rate_pct"),
                "patientsInIsolation": pm.get("patients_in_isolation_end_day"),
                "confirmedInIsolation": pm.get("confirmed_in_isolation"),
                "suspectsInIsolation": pm.get("suspects_in_isolation"),
                "admissions24h": pm.get("admissions_24h"),
                "escapes24h": pm.get("escaped_suspect_or_confirmed_24h"),
            },
        }

    # Neither schema present. Before 2026-06-07 the province axis genuinely did not exist, so absence
    # is legal and the field is omitted. At/after that date a snapshot MUST carry it; a missing axis is
    # a promotion defect, so fail loud rather than silently omitting it (the exact failure that shipped
    # the June-10 regression: provinceCurrent vanished -> convergence cascaded out -> degraded brief).
    if str(snapshot_as_of)[:10] >= "2026-06-07":
        raise ValueError(
            f"provinceCurrent could not be built for {promotion.get('source_id')!r} (as_of "
            f"{str(snapshot_as_of)[:10]}): the promotion carries neither figures.province_operational "
            f"nor a usable figures.operational_tables.contacts_total. Fix the promotion schema upstream; "
            f"refusing to ship a snapshot that silently drops the province + convergence axis."
        )
    return None


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
            # Cumulative suspected tier retired 2026-06-02: laboratory-confirmed
            # cases are the only cumulative case metric. The historical suspected
            # series (1077 ECDC 27 May, with the CDC 25 May 906, ECDC 25 May 904,
            # and DRC MoH 24 May 854 as dated conflict anchors) is preserved in
            # source_conflict_notes and the evidence chains for audit provenance,
            # not as a cumulative reported_count. The operational suspected
            # caseload (under investigation, in isolation) is re-housed on a
            # separate point-prevalence axis once SitRep #017 publishes it.
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
        # Recomputed at build time from the just-constructed primary values per
        # audit recommendation (see .process/2026-06-01-suspected-semantics-audit
        # current-state-mapping row for refresh_pipeline.py:976). The hardcoded
        # True predated the schema split; with deaths_confirmed (17 DRC + 1 UGA
        # = 18) over confirmed (128 country-scope) the ratio is 14% which sits
        # well below the 80% tension threshold (lovs_reconciler.py:79). Apples-
        # to-apples comparison uses confirmed deaths only.
        deaths_to_confirmed_tension_flag=(
            (
                _figure(figures, "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5", "deaths_confirmed_drc")
                + _figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "deaths_uganda")
            )
            / max(1, _figure(figures, "ecdc-bdbv-drc-uga-2026-05-27", "cases_confirmed_total"))
            >= lovs_reconciler._DEATHS_TO_CONFIRMED_TENSION_THRESHOLD
        ),
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


# Registered forecast-axis for every pin in the 2026-07-05 pre-registration block,
# taken VERBATIM from the registered snapshot commitments (all six axes). The public
# accountability extract (PUBLIC_COMMITMENTS_PATH) deliberately omits this grouping
# key, so it is re-attached here from the immutable registered mapping rather than
# re-derived from the pin_id (pin_id prefixes do not map cleanly to axis). Any pin
# in the committed public block that is missing from this map fails the carry-forward
# loud, so a future block cannot silently ship an unaxised pin.
_COMMITMENT_AXIS_BY_PIN: dict[str, str] = {
    "SP1": "spatial", "SP2": "spatial", "SP3": "spatial", "SP4": "spatial",
    "SP5": "spatial", "SP6": "spatial", "SP7": "spatial", "SP8": "spatial",
    "P4a": "crossborder", "P4b": "crossborder", "P4c": "crossborder",
    "P4d": "crossborder", "P4e": "crossborder", "P4f": "crossborder",
    "P4g": "crossborder", "P4h": "crossborder",
    "P5": "importation", "P-IMP-UGA-MECH": "importation",
    "P-AIR-SCREEN": "importation", "P-BORDER-POSTURE": "importation",
    "INT-P6": "intensity", "INT-VEL": "intensity", "INT-CFR-DIR": "intensity",
    "INT-P7": "intensity", "INT-NK-SHARE": "intensity", "INT-CFR-GAP": "intensity",
    "INT-SK-FRONT": "intensity",
    "P8": "international", "P-FRA-MAG": "international", "P9": "international",
    "INTL-FEED-GRAIN": "international",
    "D2": "detection", "D-DETLEAD": "detection", "D-POC": "detection",
    "D4": "detection", "INC-PLATEAU": "detection", "CT-COMPLETE": "detection",
    "ETU-OCC": "detection", "ONSET-ISO": "detection", "RINGVAX": "detection",
    "RWA-HOLD": "detection",
}

# Public fields carried forward verbatim from the accountability extract. This is an
# allowlist: only these keys are emitted, so no model internal (risk_adj, risk_raw,
# any probability) can leak even if the source file were to grow one. Every field is
# a public accountability field the resolver / reader needs.
_COMMITMENT_PUBLIC_FIELDS: tuple[str, ...] = (
    "ledger_id",
    "pin_id",
    "outbreak_id",
    "forecast_type",
    "control_role",
    "geography_class",
    "source_geography",
    "target_geography",
    "public_question",
    "public_value_or_tier",
    "registration_baseline",
    "notes",
    "horizon_days",
    "registered_at",
    "resolution_date",
    "resolution_source_policy",
    "status",
)


def carry_forward_commitments(as_of: str) -> dict:
    """Carry the full pre-registered commitment block forward from the public record.

    This is the PARALLEL, non-corridor surface to carry_forward_calibration(). It
    reads the sanitized public accountability extract
    (data/public_calibration_commitments.json), takes every commitment whose
    ``registered_at`` is on or before ``as_of`` and whose ``status`` is ``"open"``,
    and emits it VERBATIM (public fields only, per the _COMMITMENT_PUBLIC_FIELDS
    allowlist) with the registered forecast axis re-attached from
    _COMMITMENT_AXIS_BY_PIN.

    Pre-commitment contract: pins are carried forward, never re-derived or
    re-levelled. This function does not touch the corridor ledger, its hash guards,
    or its labels; it emits a separate ``calibration_commitments`` array plus the
    nearest open resolution date. The corridor mode_b_hypotheses surface is
    unaffected.

    Returns a dict:
      - "calibration_commitments": list of public commitment dicts (+ "axis")
      - "commitments_resolves_at": nearest upcoming resolution_date among open pins
      - "commitments_registered_at": registration date of the carried block

    Raises ValueError if the public record is missing/empty as of ``as_of``, if a
    carried pin has no registered axis, or if the file is structurally malformed.
    """
    if not PUBLIC_COMMITMENTS_PATH.exists():
        raise ValueError(
            f"Public commitments record not found at "
            f"{PUBLIC_COMMITMENTS_PATH.relative_to(REPO_ROOT)}. Sync it from the "
            f"LOVS public record (origin/main) before running the pipeline."
        )
    record = json.loads(PUBLIC_COMMITMENTS_PATH.read_text())
    commitments = record.get("commitments")
    if not isinstance(commitments, list):
        raise ValueError(
            "Public commitments record is malformed: 'commitments' must be a list."
        )
    as_of_day = as_of[:10]

    carried: list[dict] = []
    resolutions: list[str] = []
    registrations: list[str] = []
    for c in commitments:
        registered_at = c.get("registered_at", "")
        if not _BARE_DATE_RE.fullmatch(registered_at):
            raise ValueError(
                f"Public commitment {c.get('ledger_id')!r} has an invalid "
                f"registered_at {registered_at!r}; expected a bare YYYY-MM-DD."
            )
        # Only carry commitments that already exist as of this snapshot and are
        # still open (unresolved). Resolved pins live on in the public record for
        # the accountability trail but are not part of the live open block.
        if registered_at > as_of_day:
            continue
        if c.get("status") != "open":
            continue
        resolution_date = c.get("resolution_date", "")
        if not _BARE_DATE_RE.fullmatch(resolution_date):
            raise ValueError(
                f"Public commitment {c.get('ledger_id')!r} has an invalid "
                f"resolution_date {resolution_date!r}; expected a bare YYYY-MM-DD."
            )
        pin_id = c.get("pin_id")
        axis = _COMMITMENT_AXIS_BY_PIN.get(pin_id)
        if axis is None:
            raise ValueError(
                f"Public commitment pin_id {pin_id!r} has no registered forecast "
                f"axis in _COMMITMENT_AXIS_BY_PIN; refusing to ship an unaxised "
                f"pin. Add it to the registered axis map before releasing."
            )
        emitted = {
            key: c[key] for key in _COMMITMENT_PUBLIC_FIELDS if key in c
        }
        emitted["axis"] = axis
        carried.append(emitted)
        resolutions.append(resolution_date)
        registrations.append(registered_at)

    if not carried:
        raise ValueError(
            f"No open pre-registered commitments apply as of {as_of_day}. Sync "
            f"data/public_calibration_commitments.json from the LOVS public record "
            f"(origin/main) so the registered block is present before release."
        )

    return {
        "calibration_commitments": carried,
        "commitments_resolves_at": min(resolutions),
        "commitments_registered_at": max(registrations),
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
        "conflicting_source_ids": list(
            lovs_reconciler.normalized_conflicting_source_ids(rc)
        ),
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


def _reviewed_sitrep_source_load_artifacts(snapshot: lovs_reconciler.OutbreakSnapshot) -> dict[str, Any] | None:
    """Build source-load surfaces from the latest reviewed INSP SitRep Table 1.

    The INRB-UMIE GitHub build remains preferred when its CSV tables reconcile.
    If that derived bundle fails reconciliation, the reviewed SitRep promotion is
    the primary source chain for the current health-zone vector. The explicit
    unventilated SitRep row stays residual; it is never distributed to named
    zones.
    """
    latest = _latest_reviewed_promotion_at_or_before(snapshot.as_of[:10])
    if latest is None:
        return None

    number, promotion = latest
    figures = promotion.get("figures") or {}
    table = figures.get("health_zone_table") or {}
    rows = table.get("rows") if isinstance(table, dict) else None
    if not rows:
        return None

    source_id = str(promotion.get("source_id") or f"inrb-sitrep-{number:03d}")
    data_as_of = str(promotion.get("data_as_of") or snapshot.as_of[:10])[:10]
    by_lovs_zone: dict[str, dict[str, Any]] = {}
    unventilated = {"confirmed": 0, "confirmed_deaths": 0}

    for row in rows:
        if not isinstance(row, dict):
            continue
        zone_name = str(row.get("zone") or "").strip()
        if not zone_name:
            continue
        confirmed = row.get("confirmed")
        if not isinstance(confirmed, int) or isinstance(confirmed, bool):
            continue
        deaths = row.get("confirmed_deaths")
        confirmed_deaths = deaths if isinstance(deaths, int) and not isinstance(deaths, bool) else 0
        if "ventil" in zone_name.lower():
            unventilated = {
                "confirmed": confirmed,
                "confirmed_deaths": confirmed_deaths,
            }
            continue
        zone_id = sitrep_overlays.per_zone_canonical_id(zone_name)
        if zone_id in DISPLAY_EXCLUDED_ZONES:
            continue
        by_lovs_zone[zone_id] = {
            "confirmed": confirmed,
            "confirmed_deaths": confirmed_deaths,
            "inrb_collapsed_from": [zone_name],
            "present_in_insp_classification": "present_with_data",
            "province": str(row.get("province") or ""),
            "source_table_zone": zone_name,
        }

    if not by_lovs_zone:
        return None

    reconciliation = table.get("reconciliation") if isinstance(table, dict) else {}
    national_confirmed = (
        reconciliation.get("national_confirmed_total")
        if isinstance(reconciliation, dict)
        else None
    )
    national_deaths = (
        reconciliation.get("national_confirmed_deaths_total")
        if isinstance(reconciliation, dict)
        else None
    )
    if not isinstance(national_confirmed, int) or isinstance(national_confirmed, bool):
        national_confirmed = _promotion_figure(figures, "cumul_cas_confirmes_drc", number)
    if not isinstance(national_deaths, int) or isinstance(national_deaths, bool):
        national_deaths = _promotion_figure(figures, "cumul_deces_parmi_confirmes_drc", number)

    named_confirmed = sum(int(row["confirmed"]) for row in by_lovs_zone.values())
    named_deaths = sum(int(row["confirmed_deaths"]) for row in by_lovs_zone.values())
    residual = {
        "confirmed": national_confirmed - named_confirmed,
        "confirmed_deaths": national_deaths - named_deaths,
    }
    for metric, value in residual.items():
        if value < 0:
            raise RuntimeError(
                f"reviewed SitRep #{number:03d} Table 1 has negative {metric} residual: {value}"
            )

    block = {
        "as_of_data_date": data_as_of,
        "source_id": source_id,
        "method_basis": (
            f"reviewed_INSP_SitRep_{number:03d}_Table_1_per_health_zone_v1"
        ),
        "by_lovs_zone": {zone_id: by_lovs_zone[zone_id] for zone_id in sorted(by_lovs_zone)},
        "national_at_data_date": {
            "confirmed": national_confirmed,
            "confirmed_deaths": national_deaths,
        },
        "unallocated_residual": residual,
        "coverage_audit": {
            "present_with_data": sorted(by_lovs_zone),
            "present_but_zero": [],
            "structurally_absent": [],
        },
        "sitrep_residual_row": unventilated,
        "source_review_note": (
            f"Primary source-load is reviewed INSP SitRep #{number:03d} Table 1. "
            "The INRB-UMIE derived build is retained as source-review evidence "
            "where it reconciles; its confirmed-deaths table is not promoted "
            "when it fails national residual reconciliation."
        ),
    }

    attribution_lag = {
        "per_metric": [
            {
                "metric": "confirmed",
                "timeliness": "near_timely",
                "share_attributed_to_zones": round(named_confirmed / national_confirmed, 4),
            },
            {
                "metric": "confirmed_deaths",
                "timeliness": "near_timely",
                "share_attributed_to_zones": round(named_deaths / national_deaths, 4),
            },
        ],
        "narrative": (
            "Confirmed deaths can trail the national rollup by 1-3 weeks while "
            "the INRB clinical review queue catches up; for the current cycle, "
            "confirmed-case and confirmed-death zone attribution comes from the "
            "reviewed INSP SitRep Table 1. The explicit unventilated residual is "
            "disclosed and not allocated to named health zones."
        ),
    }

    # The suspected-only Jiba point remains a surveillance overlay from the
    # prior INRB-UMIE retired suspected tier; it is not a confirmed source-load row.
    surveillance_zones = {
        "as_of": "2026-05-30",
        "source_id": "inrb-umie-ebola-drc-2026-build-2026-06-11-37f84e5",
        "method_basis": "retired_INRB_UMIE_per_zone_cumulative_suspected_surveillance_overlay_v1",
        "basis": (
            "Upstream INRB-UMIE per-zone cumulative-suspected tier, retired from "
            "the reconciled model on 2026-06-02 (national-only operational axis, "
            "never summed into confirmed). Surfaced as a surveillance signal only: "
            "the suspected figure is the upstream per-zone count on its own vintage, "
            "not a current confirmed count and not part of the national total."
        ),
        "zones": [
            {
                "zone_id": "jiba",
                "zone_name": "Jiba",
                "suspected": 2,
                "confirmed": 0,
            }
        ],
    }

    return {
        "data_scale_used": "partial_per_zone",
        "insp_per_zone_block": block,
        "per_zone_under_ascertainment_bands": None,
        "attribution_lag_disclosure": attribution_lag,
        "surveillance_zones": surveillance_zones,
    }


def _generation_summary_json(summary: "lovs_transmission.GenerationSummary | None") -> dict[str, Any] | None:
    """Serialize a GenerationSummary (median + 50/95 CI + censored fraction)."""
    if summary is None:
        return None
    return {
        "median": summary.median,
        "ci_50": list(summary.ci_50),
        "ci_95": list(summary.ci_95),
        "censored_fraction": round(summary.censored_fraction, 4),
        "anchor_confirmed": summary.anchor_confirmed,
    }


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
        default="awaiting_next_publication",
        choices=sorted(lovs_reconciler.CARRIED_FORWARD_REASONS),
        help="Reason tag attached to carried-forward rows.",
    )
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help=(
            "Enrich the existing materialized snapshot with release and estimate "
            "contracts without rerunning stochastic model modules."
        ),
    )
    return parser.parse_args(argv)


def _write_output(output: dict[str, Any]) -> None:
    """Atomically replace the materialized snapshot."""
    import tempfile

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(OUT_PATH.parent), delete=False
    ) as tmp_fh:
        json.dump(output, tmp_fh, indent=2)
        tmp_path = tmp_fh.name
    os.replace(tmp_path, OUT_PATH)


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli(argv)
    if args.contract_only:
        if not OUT_PATH.exists():
            raise FileNotFoundError(f"materialized snapshot not found: {OUT_PATH}")
        materialized = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        materialized_date = str(materialized.get("as_of") or "")[:10]
        if args.as_of and args.as_of[:10] != materialized_date:
            raise ValueError(
                f"--as-of {args.as_of[:10]} does not match materialized {materialized_date}"
            )
        latest = _latest_reviewed_promotion_at_or_before(materialized_date)
        if latest is None:
            raise ValueError(
                f"no reviewed SitRep promotion at or before {materialized_date}"
            )
        enriched = release_contract.maybe_enrich_snapshot(materialized, latest[1])
        _write_output(enriched)
        if enriched == materialized:
            print(
                "Historical snapshot predates release contracts; materialized output retained"
            )
        else:
            print(
                "Enriched release and estimate contracts without rerunning model modules"
            )
        print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")
        return 0
    snapshot = build_snapshot()
    if args.as_of:
        # Normalize bare YYYY-MM-DD into the ISO end-of-day UTC stamp used
        # throughout the pipeline.
        target_as_of = args.as_of
        if len(target_as_of) == 10 and target_as_of.count("-") == 2:
            target_as_of = f"{target_as_of}T23:59:59Z"

        # SitRep promotion stages: each --as-of past May 28 sequentially
        # applies the SitRep declarations published for that cycle. Fields
        # the SitRep does not republish are carried forward at the field
        # level with reason=source_schema_evolved (see apply_sitrep_*
        # helpers above). Cycles past the last published SitRep apply a
        # full-snapshot LOCF with reason=awaiting_next_publication.
        if target_as_of >= "2026-05-29T23:59:59Z":
            snapshot = apply_sitrep_015(snapshot)
            print(
                f"Promoted INRB SitRep #015 onto base snapshot "
                f"{BASE_SNAPSHOT_AS_OF} -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-05-30T23:59:59Z":
            snapshot = apply_sitrep_016(snapshot)
            print(
                f"Promoted INRB SitRep #016 onto snapshot -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-05-31T23:59:59Z":
            snapshot = apply_sitrep_017(snapshot)
            print(
                f"Promoted INRB SitRep #017 onto snapshot -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-06-01T23:59:59Z":
            snapshot = apply_sitrep_018(snapshot)
            print(
                f"Promoted INRB SitRep #018 onto snapshot -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-06-02T23:59:59Z":
            snapshot = apply_sitrep_019(snapshot)
            print(
                f"Promoted INRB SitRep #019 onto snapshot -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-06-03T23:59:59Z":
            snapshot = apply_sitrep_020(snapshot)
            print(
                f"Promoted INRB SitRep #020 onto snapshot -> {snapshot.as_of}"
            )
        if target_as_of >= "2026-06-04T23:59:59Z":
            snapshot = apply_sitrep_021(snapshot)
            print(
                f"Promoted INRB SitRep #021 onto snapshot -> {snapshot.as_of}"
            )
        for number in sorted(n for n in _SITREP_PROMOTIONS_BY_NUMBER if n > 21):
            promotion = _SITREP_PROMOTIONS_BY_NUMBER[number]
            if target_as_of >= _promotion_endpoint(number, promotion):
                snapshot = apply_reviewed_sitrep_promotion(snapshot, number, promotion)
                print(
                    f"Promoted INRB SitRep #{number:03d} onto snapshot -> {snapshot.as_of}"
                )
        if target_as_of > snapshot.as_of:
            # Per-field reason overrides: INSP SitRep #015 (2026-05-29) and
            # #016 (2026-05-30) retired the cumul_deces_suspects tile, so the
            # deathsSuspected field carries forward under source_schema_evolved
            # rather than the default awaiting_next_publication. See the
            # 2026-06-01 suspected-semantics audit (audit.md current-state
            # mapping for snapshot 2026-05-31 deathsSuspected reason) and the
            # apply_sitrep_015 / apply_sitrep_016 helpers above which set this
            # reason on the SitRep cycles themselves.
            deaths_overrides: dict[str, str] = {}
            if target_as_of > "2026-05-29T23:59:59Z" and "suspected" in snapshot.reported_deaths:
                deaths_overrides["suspected"] = "source_schema_evolved"
            snapshot = apply_carry_forward(
                snapshot,
                target_as_of,
                reason=args.carried_forward_reason,
                reported_deaths_reason_overrides=deaths_overrides or None,
            )
            print(
                f"Carried forward snapshot to {target_as_of} "
                f"(default reason={args.carried_forward_reason}; "
                f"per-field overrides: {deaths_overrides or 'none'})"
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
    inrb_artifact_path = resolve_inrb_umie_artifact_path()
    _insp_artifacts = insp_block_assembler.assemble_insp_artifacts(
        inrb_artifact_path,
        INRB_UMIE_DATA_AS_OF,
        source_id=INRB_UMIE_SOURCE_ID,
    )
    if _insp_artifacts.get("data_scale_used") == "national":
        sitrep_artifacts = _reviewed_sitrep_source_load_artifacts(snapshot)
        if sitrep_artifacts is not None:
            print(
                "INSP per-zone surface: INRB-derived bundle unavailable or "
                "unreconciled; using reviewed SitRep Table 1 source-load."
            )
            _insp_artifacts = sitrep_artifacts
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
    # U1 (2026-05-28): re-base the corridor source-load primitive onto the
    # current reviewed per-health-zone block. The preferred source is the
    # INRB-UMIE derived bundle when it reconciles; otherwise the reviewed INSP
    # SitRep Table 1 block supplies the primary source-load and carries the
    # derived-bundle conflict as review provenance. Forward-only: the calibration
    # ledger and pinned blocks are read verbatim downstream and are never touched.
    if _block and _block.get("by_lovs_zone"):
        rebased_counts = _rebase_zone_counts_to_insp(_block)
        old_note = _source_zone_conflict_note(snapshot.zone_attributed_counts)
        # Carry forward any SitRep-mentioned zones that the INSP rebase
        # would otherwise drop. INSP per-zone confirmed attribution is the
        # corridor source-load primitive; a zone the upstream cycle textually
        # names as affected but for which the INSP rebase has not yet
        # attributed a confirmed count enters zone_attributed_counts with
        # confirmed=0 so the consistency contract (affectedZones == keys of
        # zoneAttributedCounts) holds. The corridor model already filters
        # zero-confirmed zones out of the hazard calculation (see
        # tests/test_lovs_next_zone.test_zero_confirmed_source_yields_no_corridor),
        # so adding a 0-row is informational, not corridor-shifting.
        sitrep_only_zones = set(snapshot.affected_zones) - set(rebased_counts)
        if sitrep_only_zones:
            sitrep_only_primary = snapshot.reported_counts.get("confirmed")
            sitrep_only_source_id = (
                sitrep_only_primary.primary_source_id
                if sitrep_only_primary is not None
                else INRB_SITREP_015_SOURCE_ID
            )
            for zone_id in sorted(sitrep_only_zones):
                rebased_counts[zone_id] = {
                    "confirmed": 0,
                    "source_id": sitrep_only_source_id,
                    "source_published_at": snapshot.as_of[:10],
                    "review_reasons": [
                        "named_affected_in_reviewed_sitrep_table",
                        "no_per_zone_insp_attribution_yet",
                    ],
                }
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
    _maybe_print("probable", snapshot.reported_counts.get("probable"))
    _maybe_print("suspected_active", snapshot.reported_counts.get("suspected_active"))
    _maybe_print(
        "suspected_under_investigation",
        snapshot.reported_counts.get("suspected_under_investigation"),
    )
    _maybe_print(
        "suspected_in_isolation",
        snapshot.reported_counts.get("suspected_in_isolation"),
    )
    _maybe_print("deaths_confirmed", snapshot.reported_deaths.get("confirmed"))
    _maybe_print("deaths_probable", snapshot.reported_deaths.get("probable"))
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

    # Visibility nowcast. visibility_history seeds the gamma-CDF
    # days-since-earliest-event term in lovs_visibility.nowcast; with empty
    # history the function uses the documented 7.0d prior operating point
    # (lovs_visibility.py:341-342).
    #
    # Days-policy decision (2026-06-01, C3 ascertainment audit): use EMPTY
    # history uniformly across the 2026-05-26..2026-05-31 series. A partial
    # one-entry history would set earliest_as_of to our earliest *snapshot*
    # (around 2026-05-25), making days_since_earliest about 6 days and
    # implying the outbreak is roughly a week old. That is epidemiologically
    # false: the outbreak timeline runs back to mid-May and earlier, so the
    # true delay term is near-complete. Neither the 6-day nor the 7-day proxy
    # is the true outbreak age; the 7.0d default is the documented,
    # series-consistent single-snapshot prior and is the only operating point
    # consistent with this release's method_caveat ("no prior daily snapshot
    # series is supplied"). Seeding a single comparable snapshot (the prior
    # Band-1 behavior) is reverted because it varied the completeness band on a
    # false outbreak-age proxy and contradicted that caveat.
    #
    # Completeness is delay-only by construction: the suspect-queue positivity
    # term that lovs_visibility could compute from a suspected count is blended
    # at DATA_TERM_WEIGHT = 0.0, so it never enters the completeness posterior
    # and the suspected series is never added to confirmed. With the cumulative
    # suspected tier retired 2026-06-02, lovs_visibility._get_suspected_count has
    # no cumulative denominator to resolve and degrades gracefully; the
    # completeness output is unchanged because the data term carries zero weight.
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
    corridor_snapshot = dataclasses.replace(
        snapshot,
        affected_zones=tuple(sorted(snapshot.zone_attributed_counts)),
    )
    corridors = lovs_next_zone.next_zone_risk(
        snapshot=corridor_snapshot,
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

    # PARALLEL surface (2026-07-05 block): carry the full pre-registered commitment
    # set forward from the sanitized public record. This is separate from the
    # corridor calibration above; it does not touch the corridor ledger, its hash
    # guards, or its labels. Pins are carried VERBATIM (never re-derived/re-levelled).
    carried_commitments = carry_forward_commitments(snapshot.as_of)
    calibration_commitments = carried_commitments["calibration_commitments"]
    print(
        f"Carried forward {len(calibration_commitments)} pre-registered commitment(s) "
        f"(block registered {carried_commitments['commitments_registered_at']}); "
        f"nearest resolution {carried_commitments['commitments_resolves_at']}"
    )

    # Presentation scrub (founder decision 2026-06-05): drop display-excluded
    # zones from the reconciled zone table HERE, before any source-zone count or
    # analysis_dependency_audit prose is derived from it, so the corridor count,
    # the audit prose, and the serialized zone_attributed_counts/affected_zones
    # all agree. karisimbi-cod carries 0 confirmed, so zone_attributed_confirmed
    # and every national total are unchanged. See DISPLAY_EXCLUDED_ZONES.
    if any(z in snapshot.zone_attributed_counts for z in DISPLAY_EXCLUDED_ZONES):
        snapshot = dataclasses.replace(
            snapshot,
            affected_zones=tuple(
                z
                for z in snapshot.affected_zones
                if z not in DISPLAY_EXCLUDED_ZONES
            ),
            zone_attributed_counts={
                zid: row
                for zid, row in snapshot.zone_attributed_counts.items()
                if zid not in DISPLAY_EXCLUDED_ZONES
            },
        )

    zone_attributed_confirmed = sum(
        int(row.get("confirmed") or 0)
        for row in snapshot.zone_attributed_counts.values()
    )
    # Source-zone counts DERIVED from the zone-attributed table (the corridor
    # source load), so every corridor-related note interpolates the same two
    # numbers instead of carrying a hand-typed zone count that drifts.
    _source_zone_count = len(snapshot.zone_attributed_counts)
    _source_zones_with_confirmed = sum(
        1
        for row in snapshot.zone_attributed_counts.values()
        if int(row.get("confirmed") or 0) > 0
    )
    _source_load_source_ids = sorted(
        {
            str(row.get("source_id") or "")
            for row in snapshot.zone_attributed_counts.values()
            if row.get("source_id")
        }
    )
    _source_load_label = ", ".join(_source_load_source_ids) or INRB_UMIE_SOURCE_ID
    _source_load_family = (
        "reviewed INSP SitRep Table 1"
        if _source_load_source_ids
        and all(source_id.startswith("inrb-sitrep") for source_id in _source_load_source_ids)
        else "INRB-UMIE/INSP per-health-zone source-load table"
    )

    def _headline(metric_dict: dict, key: str, fallback_key: str = "") -> int | None:
        rc = metric_dict.get(key)
        if rc is None and fallback_key:
            rc = metric_dict.get(fallback_key)
        return rc.primary_value if rc is not None else None

    def _metric_endpoint_clause(
        metric_dict: dict[str, lovs_reconciler.ReconciledCount],
        key: str,
        label: str,
    ) -> str:
        rc = metric_dict.get(key)
        if rc is None:
            return f"{label} not published on the current cycle"
        source_id = rc.primary_source_id
        match = re.search(r"inrb-sitrep-0*(\d+)", str(source_id))
        if match:
            return (
                f"{label} endpoint is SitRep #{int(match.group(1)):03d} "
                f"({rc.primary_value}, data as of {snapshot.as_of[:10]})"
            )
        return f"{label} endpoint is {source_id} ({rc.primary_value}, data as of {snapshot.as_of[:10]})"

    headline_confirmed = _headline(snapshot.reported_counts, "confirmed")
    # Operational suspected axis (point-prevalence; never summed into confirmed).
    # The cumulative suspected tier was retired 2026-06-02.
    headline_suspected_active = _headline(snapshot.reported_counts, "suspected_active")
    headline_suspected_under_investigation = _headline(
        snapshot.reported_counts, "suspected_under_investigation"
    )
    headline_suspected_in_isolation = _headline(
        snapshot.reported_counts, "suspected_in_isolation"
    )
    headline_deaths_confirmed = _headline(snapshot.reported_deaths, "confirmed")
    headline_deaths_suspected = _headline(snapshot.reported_deaths, "suspected")
    # Module C2 active-queue lab-yield projection: a SIBLING diagnostic to the C1
    # reporting-completeness nowcast, never an input to it. None when no reviewed
    # SitRep lab indicators exist at or before the as-of date (graceful no-op that
    # leaves the C1 visibility block byte-identical).
    # When the latest snapshot publishes its own active-suspected queue, C2 reads
    # the fresh headline (source_sitrep_number is None because the headline is a
    # reconciled composite, not a single SitRep tile; carried_forward is False).
    # When the latest SitRep OMITS the full active-suspected total (INSP stopped
    # publishing the under-investigation + in-isolation split after SitRep #018),
    # but still publishes the suspected-in-isolation census, C2 stays CURRENT by
    # using suspected_in_isolation as the active-queue basis paired with the live
    # confirmed headline and the most recent reviewed lab window. The lab samples
    # are drawn from the isolation/work-up queue, so suspected_in_isolation is the
    # coherent denominator for the lab-yield projection; it is flagged
    # active_queue_basis='suspected_in_isolation' so a reader knows it is the
    # in-isolation census, not the (no-longer-published) full active-suspected
    # total. Only when neither split is available does C2 fall back to the newest
    # reviewed SitRep that published a complete queue (carried_forward, tagged with
    # its originating SitRep number/date so the reuse is never read as fresh).
    if headline_confirmed is not None and headline_suspected_active is not None:
        c2_inputs: dict[str, Any] | None = {
            "source_data_as_of": snapshot.as_of[:10],
            "source_sitrep_number": None,
            "carried_forward": False,
            "active_queue_basis": "suspected_active_total",
            "confirmed_active_total": headline_confirmed,
            "active_suspected_total": headline_suspected_active,
            "suspected_under_investigation": headline_suspected_under_investigation,
            "suspected_in_isolation": headline_suspected_in_isolation,
        }
    elif headline_confirmed is not None and headline_suspected_in_isolation is not None:
        c2_inputs = {
            "source_data_as_of": snapshot.as_of[:10],
            "source_sitrep_number": None,
            "carried_forward": False,
            "active_queue_basis": "suspected_in_isolation",
            "confirmed_active_total": headline_confirmed,
            "active_suspected_total": headline_suspected_in_isolation,
            "suspected_under_investigation": headline_suspected_under_investigation,
            "suspected_in_isolation": headline_suspected_in_isolation,
        }
    else:
        c2_inputs = latest_c2_active_queue_inputs(snapshot.as_of[:10])
    c2_active_queue = None
    if c2_inputs is not None:
        c2_active_queue = lovs_active_queue_c2.c2_active_queue_projection(
            _SITREP_PROMOTIONS_BY_NUMBER,
            as_of=str(c2_inputs["source_data_as_of"]),
            confirmed_active_total=int(c2_inputs["confirmed_active_total"]),
            active_suspected_total=int(c2_inputs["active_suspected_total"]),
            suspected_under_investigation=c2_inputs.get("suspected_under_investigation"),
            suspected_in_isolation=c2_inputs.get("suspected_in_isolation"),
            inputs_provenance=_c2_inputs_provenance(c2_inputs, snapshot.as_of[:10]),
        )
    analysis_dependency_audit = [
        {
            "surface": "public_reporting_trajectory",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
                "suspected_under_investigation": headline_suspected_under_investigation,
                "suspected_in_isolation": headline_suspected_in_isolation,
                "active_suspected_total": headline_suspected_active,
                "deaths_confirmed": headline_deaths_confirmed,
                "deaths_suspected": headline_deaths_suspected,
            },
            "clock_basis": (
                f"{_confirmed_endpoint_clause(snapshot)} is the confirmed-case "
                "headline. "
                f"{_metric_endpoint_clause(snapshot.reported_deaths, 'confirmed', 'Confirmed-death')} "
                "is the laboratory-confirmed death headline. Suspected-death and "
                "under-investigation stocks remain absent unless the current "
                "reviewed SitRep explicitly republishes them."
            ),
        },
        {
            "surface": "visibility_module_c",
            "status": "updated",
            "inputs": {
                "confirmed": headline_confirmed,
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
                "Snapshot-level visibility nowcast. Reporting completeness is "
                "delay-only (the gamma reporting-delay CDF); the suspect-queue "
                "positivity term carries zero weight (DATA_TERM_WEIGHT = 0.0), "
                "so it does not drive the completeness posterior. The cumulative "
                "suspected tier was retired 2026-06-02, so no cumulative "
                "suspected denominator feeds this surface."
            ),
        },
        {
            "surface": "confirmable_underlying_trajectory",
            # C1 confirmable total (confirmed / reporting-completeness) is RETIRED under
            # the C2 active-queue regime (snapshot as_of >= 2026-05-29). The surface row
            # is KEPT (export_public_health_dataset REQUIRED_SURFACES) but its divide-by-
            # completeness output is dropped so the retired C1 band is not re-emitted.
            "status": (
                "retired_c1_regime"
                if snapshot.as_of[:10] >= "2026-05-29"
                else "updated"
            ),
            "inputs": {
                "confirmed_endpoint": headline_confirmed,
                "reporting_completeness_50": [
                    vp.reporting_completeness.lower_50,
                    vp.reporting_completeness.upper_50,
                ],
            },
            "outputs": (
                {}
                if snapshot.as_of[:10] >= "2026-05-29"
                else {
                    "endpoint_confirmable_50": [
                        round(headline_confirmed / vp.reporting_completeness.upper_50),
                        round(headline_confirmed / vp.reporting_completeness.lower_50),
                    ]
                }
            ),
            "clock_basis": (
                "C1 confirmable total retired under the C2 active-queue regime "
                "(2026-05-29 onward); the reporting-completeness fraction is kept as "
                "an input-only reference, not a current model output."
                if snapshot.as_of[:10] >= "2026-05-29"
                else (
                    f"Confirmed endpoint is the {_confirmed_endpoint_clause(snapshot)} "
                    "headline; the completeness posterior is the current snapshot "
                    "posterior applied across the displayed confirmed-case series."
                )
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
                f"{_metric_endpoint_clause(snapshot.reported_deaths, 'confirmed', 'Confirmed-death')} "
                "feeds the confirmed-only death-back-projection grid. The broader "
                "suspected-death register is not carried onto the current-cycle "
                "surface after the confirmed/suspected death-axis split unless a "
                "reviewed source republishes it."
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
                # Source-zone counts DERIVED from the zone-attributed table, so the
                # corridor prose can never drift from the actual source load (this
                # collapses the prior free-text 22/23/25 zone-count spread).
                "source_zone_count": _source_zone_count,
                "source_zones_with_confirmed": _source_zones_with_confirmed,
            },
            "blocked_by": (
                "Per-health-zone confirmed attribution uses the "
                f"{_source_load_family} ({_source_load_label}): {_source_zone_count} source "
                f"zones, of which {_source_zones_with_confirmed} carry confirmed "
                f"cases, attributing {zone_attributed_confirmed} confirmed. The "
                f"remaining {max(0, headline_confirmed - zone_attributed_confirmed)} "
                "confirmed are the unallocated headline + cross-border attribution "
                "lag, held in the residual pending a coordinated zone-alias bridge "
                "and map-geometry expansion rather than smeared across source zones."
            ),
        },
    ]

    # Module C2 sibling surface in the dependency audit, present only when C2 is
    # (so the audit list is byte-identical when no reviewed lab indicators exist).
    # C2 is a known-active-queue lab-yield diagnostic that consumes reviewed
    # SitRep lab indicators and the operational active-suspected queue; it writes
    # no Module C (C1) reporting-completeness field and is never an input to it.
    if c2_active_queue is not None:
        _c2w = c2_active_queue["primary_window"]
        _c2_inputs = c2_active_queue["inputs"]
        analysis_dependency_audit.append(
            {
                "surface": "active_queue_projection_c2",
                "status": "updated",
                "inputs": {
                    "confirmed_active_total": _c2_inputs["confirmed"],
                    "active_suspected_total": _c2_inputs["active_suspected_total"],
                    "lab_samples_analyzed_recent": _c2w["samples_analyzed"],
                    "lab_samples_positive_recent": _c2w["samples_positive"],
                    "lab_window": f"{_c2w['date_start']}/{_c2w['date_end']}",
                },
                "outputs": {
                    "recent_lab_positivity_50": _c2w["positivity_50"],
                    "expected_active_queue_confirmations_50": _c2w[
                        "expected_active_queue_confirmations_50"
                    ],
                    "confirmable_active_queue_50": _c2w["confirmable_active_queue_50"],
                },
                "inputs_provenance": _c2_inputs_provenance(
                    c2_inputs, snapshot.as_of[:10]
                ),
                "clock_basis": (
                    "Module C2 active-queue lab-yield: a SIBLING diagnostic to the "
                    "Module C (C1) reporting-completeness nowcast, never an input to "
                    "it. Recent reviewed-SitRep lab positivity (flat Beta(1,1)) "
                    "applied to the operational active-suspected queue (cases under "
                    "investigation plus cases in isolation), added to confirmed. A "
                    "known-queue yield within the response system, not reporting "
                    "completeness, hidden community incidence, deaths, or future "
                    "spread."
                ),
            }
        )

    # Plan A 2026-05-28: scale-resilience-driven INSP per-zone surfaces.
    # `_insp_artifacts` was assembled at the top of main() for the source-zone
    # expansion; reuse it here so the assembler runs once per cycle.
    print(
        "INSP per-zone surface: "
        f"data_scale_used={_insp_artifacts['data_scale_used']!r}"
    )

    # Headline evidence-chain provenance: bind the headline confirmed and
    # confirmed-deaths primaries to the reviewed chain that backs each, DERIVED
    # from each metric's primary_source_id (never hardcoded to a SitRep number).
    # This makes the embedded chain a generated consequence of the source; the
    # release gate then enforces that the embedded chain's source matches the
    # metric's primary_source_id.
    _headline_confirmed_rc = snapshot.reported_counts.get("confirmed")
    _headline_deaths_rc = snapshot.reported_deaths.get("confirmed")
    headline_evidence_chain_ids = lovs_evidence.headline_evidence_provenance(
        lovs_evidence.load_registry(),
        confirmed_primary_source_id=(
            _headline_confirmed_rc.primary_source_id
            if _headline_confirmed_rc is not None
            else None
        ),
        confirmed_deaths_primary_source_id=(
            _headline_deaths_rc.primary_source_id
            if _headline_deaths_rc is not None
            else None
        ),
    )

    # SitRep19 Phase B generation surfaces (the website sync mirrors each into its
    # own camelCased key on the published website snapshot). All three are DERIVED
    # from reviewed source-of-truth so the published surface can never go stale
    # against the headline source:
    #   * confirmed_death_series -> timeline[].deathsConfirmed / deathsBasis. The
    #     apples-to-apples country-scope confirmed-death history (26 May 18,
    #     29 May 43, 30 May 43, 31 May 49, 1 Jun 61, 2 Jun 63). The broad register
    #     (timeline[].deaths) stays a separate suspected-basis series ending 1 Jun.
    #   * province_burden -> provinceBurden: the always-fresh June-2 province
    #     confirmed/death floor from SitRep #019 Table 1.
    #   * date_semantics.source_clocks[headline_count_endpoint] -> the headline
    #     clock, derived from reported_counts.confirmed.primary_source_id.
    _overlay_figures = _load_manifest_figures()
    _base_confirmed_deaths = _figure(
        _overlay_figures,
        "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5",
        "deaths_confirmed_drc",
    ) + _figure(_overlay_figures, "ecdc-bdbv-drc-uga-2026-05-27", "deaths_uganda")
    confirmed_death_series = sitrep_overlays.confirmed_death_series(
        _overlay_figures,
        _SITREP_PROMOTIONS_BY_NUMBER,
        base_value=_base_confirmed_deaths,
    )
    _confirmed_primary_source_id = (
        _headline_confirmed_rc.primary_source_id
        if _headline_confirmed_rc is not None
        else None
    )
    source_clocks = sitrep_overlays.headline_source_clock(_confirmed_primary_source_id)
    # Generation invariant: a hand-edited or stale headline clock can never ship.
    sitrep_overlays.assert_headline_clock_matches_source(
        source_clocks, _confirmed_primary_source_id
    )
    # Province floor + per-zone display read the latest reviewed promotion at or
    # before the snapshot date. The reviewed promotion gate above prevents a
    # malformed future SitRep from retargeting these surfaces.
    _latest_display = _latest_reviewed_promotion_at_or_before(snapshot.as_of[:10])
    _sitrep_display_promotion = _latest_display[1] if _latest_display is not None else None
    province_burden = (
        sitrep_overlays.province_burden(_sitrep_display_promotion)
        if _sitrep_display_promotion is not None
        else []
    )
    # Display-only per-zone layer: the fresh SitRep21 Table-1 per-zone counts for
    # the map markers/shading. The corridor source-load (zone_attributed_counts)
    # stays the validated INSP block (U1 re-base); this never feeds corridors.
    sitrep_per_zone_display = (
        sitrep_overlays.per_zone_display(_sitrep_display_promotion)
        if _sitrep_display_promotion is not None
        else {}
    )

    output = {
        "as_of": snapshot.as_of,
        # Headline count clock. Per-zone attribution has its own trailing clock in
        # `insp_per_zone_block.as_of_data_date`; do not collapse that older
        # attribution clock into the snapshot-level headline data date.
        "data_as_of": snapshot.as_of[:10],
        "outbreak_id": snapshot.outbreak_id,
        "reported_counts": {
            case_class: _count_output(count)
            for case_class, count in sorted(snapshot.reported_counts.items())
        },
        "reported_deaths": {
            death_class: _count_output(count)
            for death_class, count in sorted(snapshot.reported_deaths.items())
        },
        # Generated headline provenance (see above): one entry per headline metric
        # binding its primary_source_id to the backing reviewed chain.
        "headline_evidence_chain_ids": headline_evidence_chain_ids,
        # SitRep19 Phase B generation surfaces (see above): the apples-to-apples
        # confirmed-death history, the always-fresh province burden floor, and the
        # headline-count-endpoint source clock, all derived from reviewed sources.
        "confirmed_death_series": confirmed_death_series,
        "province_burden": province_burden,
        "sitrep_per_zone_display": sitrep_per_zone_display,
        "date_semantics": {"source_clocks": source_clocks},
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
            # C2 sibling block (Module C2 active-queue lab-yield). Conditionally
            # present, so the visibility block is byte-identical when c2 is None.
            # Computed by a separate module; writes no C1 field.
            **(
                {"active_queue_projection": c2_active_queue}
                if c2_active_queue is not None
                else {}
            ),
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
        # PARALLEL pre-registered commitment block (2026-07-05), carried forward
        # verbatim from the public accountability record. Co-exists with the
        # corridor calibration above; the site renders a separate
        # 'Pre-registered commitments' section from this array.
        "calibration_commitments": calibration_commitments,
        "calibration_commitments_meta": {
            "registered_at": carried_commitments["commitments_registered_at"],
            "resolves_at": carried_commitments["commitments_resolves_at"],
            "count": len(calibration_commitments),
            "source": "Sanitized public accountability extract (public_calibration_commitments.json); model internals excluded.",
        },
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
            "128 total confirmed (121 DRC + 7 Uganda) and 18 country-scope "
            "confirmed deaths (17 DRC from INRB/INSP + one Uganda from ECDC/CDC). "
            "Laboratory-confirmed cases and confirmed deaths are the only "
            "cumulative epidemiological metrics; the legacy cross-class composite "
            "that summed 246 DRC suspected deaths with one Uganda confirmed death "
            "into a single 247 country-scope deaths headline is retired (it "
            "conflated confirmed deaths with under-investigation suspected "
            "deaths). The CDC 25 May page (112 confirmed, 223 "
            "suspected deaths) and the ECDC 25 May page (101 confirmed, "
            "119 suspected deaths) drop to dated conflict anchors. CDC "
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
    # Surveillance overlay (2026-06-05): suspected-only zones off the reconciled model
    # (Jiba). Emitted as a distinct top-level field, never folded into the per-zone
    # block or any national. Omitted entirely when no surveillance zone is present.
    if _insp_artifacts.get("surveillance_zones"):
        output["surveillance_zones"] = _insp_artifacts["surveillance_zones"]
    if _insp_artifacts["per_zone_under_ascertainment_bands"] is not None:
        output["per_zone_under_ascertainment_bands"] = _insp_artifacts[
            "per_zone_under_ascertainment_bands"
        ]

    # Per-zone response-state surface (2026-06-02): contacts under follow-up,
    # contacts seen, patients in care, hospital escapes, ND-aware. This block is
    # GENERATED every run from the same INRB-UMIE artifact (never a static
    # injection) so a future regen cannot silently drop the per-zone responseState
    # layer that lovs.public_exports._response_state reads back. Clock honesty: we
    # pass the cycle/headline date as the cutoff, but the serialized block's own
    # data_as_of stays the REAL latest non-ND response-data date (it trails the
    # headline by a few days), distinct from the headline and never differenced.
    # When the artifact is absent (national-fallback cycle) the block is omitted,
    # exactly as the INSP per-zone block is.
    if inrb_artifact_path is not None:
        cycle_as_of = _date_from_iso(snapshot.as_of)
        try:
            response_snapshot = load_response_state(
                inrb_artifact_path,
                cycle_as_of,
                source_id=INRB_UMIE_SOURCE_ID,
            )
        except INSPLoaderError as exc:
            # Mirror the INSP per-zone fallback: a malformed response table drops
            # the block (the consumer degrades to the national axis only) rather
            # than failing the whole snapshot.
            print(f"Response-state block omitted (loader error): {exc}")
        else:
            output["response_state_block"] = serialize_response_state_block(
                response_snapshot
            )
            print(
                "Response-state block: "
                f"{len(response_snapshot.by_lovs_zone)} zones, "
                f"data_as_of={response_snapshot.data_as_of}"
            )

    # Cross-border response posture: a cadence input for the operational corridor read.
    # Curated from the documented partner-report border evidence (the lovs-evidence-mcp
    # partner-activity layer holds the underlying citations). Per cross-border regime it
    # carries the corridor state, the model containment factor, and provenance, so a
    # downstream operational overlay tracks the border state as a data field rather than a
    # hardcoded table. A border reopening is a data edit here that rebounds the effective risk.
    output["corridor_response_posture"] = {
        "as_of": snapshot.as_of,
        "source_id": "bdbv-2026-partner-report-border-posture",
        "by_regime": {
            "cross_border_land": {
                "state": "closed",
                "containment": 0.85,
                "provenance": (
                    "Uganda land border closed + army-backed PoE screening "
                    "(UNHCR ext-update 2026-06-04; IOM SitRep 02/03; Africa CDC SitRep 11; IMC SitRep 05)"
                ),
            },
            "cross_border_air": {
                "state": "screened",
                "containment": 0.45,
                "provenance": "Uganda air PoE 21-day traveller screening (CDC returning-travellers info, 2026-05-21)",
            },
            "cross_border_ssd": {
                "state": "open",
                "containment": 0.30,
                "provenance": "South Sudan tri-border; limited/undocumented cross-border response",
            },
        },
    }

    # In-country conflict/access ratings (1 best .. 5 worst): the second cadence input
    # to the operational corridor read, mirroring corridor_response_posture. Curated
    # from the documented access/security evidence (INSP SitRep security + logistics
    # sections, OCHA/ACLED armed-group access constraints), carried as a per-target
    # data field so a deteriorating-access edit is a data change (containment falls,
    # effective risk rebounds) rather than a hardcoded website table going stale. The
    # website map reads corridorConflictAccess.by_target and falls back to its static
    # defaults only when this field is absent. Refresh the ratings each cadence as the
    # security picture shifts.
    output["corridor_conflict_access"] = {
        "as_of": snapshot.as_of,
        "source_id": "bdbv-2026-partner-report-access-security",
        "by_target": {
            # Ituri source zones: functional CTEs but active provider strike in
            # Bunia/Rwampara (SitRep security section) + Djugu/CODECO armed-group
            # presence and artisanal-mining migration around Mongbwalu (epidemic origin).
            "bunia-ituri": 3,
            "rwampara-ituri": 4,
            "mongbwalu-ituri": 4,
            # Nord-Kivu: worst in-country access — ADF security limits, lab backlog /
            # delayed diagnosis, and no functional ambulance/hearse reported in the
            # logistics subset (SitRep Nord-Kivu logistics + challenges).
            "beni-cod": 5,
            # Cross-border source zones (used for source-outflow containment): capital
            # with strong access, secondary Ugandan border districts.
            "kampala-uga": 1,
            "bundibugyo-uga": 2,
            "kasese-uga": 2,
        },
    }

    # Presentation-layer scrub (founder decision 2026-06-05): drop display-excluded
    # zones (karisimbi-cod) from every per-zone surface BEFORE the responseState
    # roll-ups are assembled, so by_zone/by_province exclude them automatically and
    # the written artifact stays internally consistent (affected_zones ==
    # zone_attributed_counts keys, recomputed coverage_stats).
    _scrub_display_excluded_zones(output)

    # Assemble the camelCased responseState the website consumes directly from THIS
    # live snapshot: the national operational axis from this cycle's reported_counts
    # plus the per-zone INRB-UMIE response tables. The website sync reads responseState
    # from the live output (this key), not the founder-gated public_snapshot package,
    # so the operational axis tracks the current SitRep instead of lagging at the last
    # public-export cycle.
    from lovs import public_exports as _public_exports

    _op_status = _public_exports._operational_status(output["reported_counts"])
    _assembled_response_state = _public_exports._response_state(output, _op_status)
    if _assembled_response_state is not None:
        # Province-level CURRENT operational axis (stale-aware per-zone fallback):
        # INSP stopped publishing zone-level contacts/care after 2026-05-30, so the
        # website hides the lapsed per-zone axis and shows this current province +
        # national state instead. Omitted when no reviewed province_operational
        # block exists for the latest promotion.
        _province_current = _build_current_province_response(snapshot.as_of)
        if _province_current is not None:
            _province_current_date = _province_current.get("dataAsOf")
            if _province_current_date:
                _prior_response_data_as_of = _assembled_response_state.get("data_as_of")
                if (
                    _prior_response_data_as_of
                    and _prior_response_data_as_of != _province_current_date
                ):
                    _assembled_response_state.setdefault(
                        "per_zone_data_as_of", _prior_response_data_as_of
                    )
                _assembled_response_state["data_as_of"] = _province_current_date
            _assembled_response_state["provinceCurrent"] = _province_current
        output["responseState"] = _assembled_response_state

    # Convergent-signal burden nowcast (rebuilt 2026-06-09; see lovs_convergence + the
    # delta-audit). The Method-2 infections validator (external), the LOVS
    # death/ascertainment nowcast, the Module-D known-chain floor, and the worked
    # methodology so the public chart is reproducible. Reads the national contact axis
    # from responseState.provinceCurrent; emitted every cycle that axis is present (a
    # regen can no longer silently drop it). Consumed by sync._translate_convergence.
    _conf_rc = snapshot.reported_counts.get("confirmed")
    _deaths_rc = snapshot.reported_deaths.get("confirmed")
    _rs = output.get("responseState")
    _nat = (
        _rs.get("provinceCurrent", {}).get("national") if isinstance(_rs, dict) else None
    )
    if _conf_rc is not None and _deaths_rc is not None and isinstance(_nat, dict):
        # Country-scope confirmed-case history (one cumulative point per reviewed SitRep
        # date, deduped by date) -> the Nishiura delay-adjusted confirmed CFR denominator.
        # Mirrors the confirmed_death_series source so case + death clocks stay aligned.
        _confirmed_case_by_date: dict[str, dict[str, Any]] = {}
        for _num in sorted(_SITREP_PROMOTIONS_BY_NUMBER):
            _figs = _SITREP_PROMOTIONS_BY_NUMBER[_num].get("figures") or {}
            _val = _figs.get("country_scope_confirmed_total")
            if not isinstance(_val, int) or isinstance(_val, bool):
                continue
            _date = str(_SITREP_PROMOTIONS_BY_NUMBER[_num].get("data_as_of") or "")[:10]
            if _date:
                _confirmed_case_by_date[_date] = {"date": _date, "value": _val}
        _confirmed_case_series = [
            _confirmed_case_by_date[_d] for _d in sorted(_confirmed_case_by_date)
        ]
        output["convergence"] = lovs_convergence.build_convergence(
            as_of=snapshot.as_of[:10],
            confirmed=int(_conf_rc.primary_value),
            confirmed_deaths=int(_deaths_rc.primary_value),
            contacts_under_follow_up=int(_nat.get("contactsUnderFollowUp") or 0),
            followup_coverage_pct=float(_nat.get("followUpCoveragePct") or 0.0),
            confirmed_series=_confirmed_case_series,
        )
    elif snapshot.as_of[:10] >= "2026-06-06":
        # Convergence (the inferred-trajectory nowcast: burden, under-ascertainment, Module-D
        # known-chain floor, worked methodology) has shipped on every cycle since 2026-06-06. If it
        # cannot be built at/after that date the brief's whole inferred-trajectory section degrades --
        # fail the build instead of silently omitting it (the June-10 regression).
        raise ValueError(
            f"Convergence block could not be built for as_of {snapshot.as_of[:10]}: confirmed/deaths or "
            f"the provinceCurrent.national contact axis is unavailable. This cycle is at/after 2026-06-06 "
            f"and MUST carry the convergence nowcast; refusing to ship a degraded inferred-trajectory section."
        )
    else:
        print("Convergence block omitted (pre-2026-06-06 cycle, by design).")

    if _sitrep_display_promotion is not None:
        output = release_contract.maybe_enrich_snapshot(
            output, _sitrep_display_promotion
        )
    _write_output(output)
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
