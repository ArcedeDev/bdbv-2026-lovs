#!/usr/bin/env python3
"""Refresh the LOVS pipeline output to the PHEIC-era situation as of 22 May 2026.

Constructs an OutbreakSnapshot reflecting the situation as of 2026-05-22,
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
# archived in the release-facing sources block.
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
    "who-dg-remarks-bdbv-2026-05-22",
    "who-ihr-ec-bdbv-temporary-recommendations-2026-05-22",
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


def canonical_source_id(source_id: str) -> str:
    return source_id[: -len("-live")] if source_id.endswith("-live") else source_id


def canonical_zone_id(zone_id: str) -> str:
    return ZONE_ID_ALIASES.get(zone_id, zone_id)


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
    """Construct the 22 May 2026 OutbreakSnapshot from explicitly verified sources.

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
        as_of="2026-05-22T23:59:59Z",
        pathogen="BDBV",
        country_scope=("COD", "UGA"),
        reported_counts={
            "suspected": lovs_reconciler.ReconciledCount(
                # Span: Africa CDC PHECS (18 May): 395 -> WHO DG Member State
                # briefing (22 May): almost 750. Approximate wording is retained
                # in the source metadata and public audit rows; the integer value
                # is the display/model endpoint because public release artifacts expect
                # numeric ranges.
                minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "cases_suspected_drc_approx"),
                maximum=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "cases_suspected_approx"),
                primary_value=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "cases_suspected_approx"),
                primary_source_id="who-dg-remarks-bdbv-2026-05-22",
                conflicting_source_ids=(
                    "afro-sitrep-01-2026-05-18",
                    "africa-cdc-phecs-2026-05-18",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "ecdc-bdbv-drc-uga-2026-05-21",
                    "cdc-current-situation-2026-05-21",
                ),
            ),
            "confirmed": lovs_reconciler.ReconciledCount(
                # 17 May (WHO PHEIC statement; case data as of 16 May):
                # 8 Ituri + 2 Kampala = 10. The reported Kinshasa case was
                # deconfirmed by INRB and is excluded.
                # 19 May (ECDC): 30. 20 May (WHO DG): 51 DRC + 2 Kampala = 53.
                # 22 May (WHO DG): 82 DRC + 2 imported Uganda = 84. CDC's 21 May
                # lower tuple remains a denominator/cadence conflict, now
                # superseded for the headline endpoint by WHO's newer official
                # Member State briefing.
                minimum=_figure(figures, "who-pheic-2026-05-17", "cases_confirmed"),
                maximum=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "cases_confirmed_total"),
                primary_value=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "cases_confirmed_total"),
                primary_source_id="who-dg-remarks-bdbv-2026-05-22",
                conflicting_source_ids=(
                    "who-pheic-2026-05-17",
                    "ecdc-bdbv-drc-uga-2026-05-19",
                    "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "who-dg-remarks-bdbv-2026-05-20",
                    "cdc-current-situation-2026-05-21",
                ),
            ),
        },
        reported_deaths=lovs_reconciler.ReconciledCount(
            # Span: Africa CDC PHECS (18 May): 106 -> WHO DG Member State
            # briefing (22 May): 177 suspected deaths. Values pulled from the
            # manifest.
            minimum=_figure(figures, "africa-cdc-phecs-2026-05-18", "deaths_approx"),
            maximum=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "deaths_suspected"),
            primary_value=_figure(figures, "who-dg-remarks-bdbv-2026-05-22", "deaths_suspected"),
            primary_source_id="who-dg-remarks-bdbv-2026-05-22",
            conflicting_source_ids=(
                "afro-sitrep-01-2026-05-18",
                "africa-cdc-phecs-2026-05-18",
                "ecdc-bdbv-drc-uga-2026-05-21",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "who-dg-remarks-bdbv-2026-05-20",
                "cdc-current-situation-2026-05-21",
            ),
        ),
        affected_zones=tuple(zone_counts.keys()),
        sources=snapshot_sources,
        case_definition_version=None,
        source_conflict_notes=(
            "Suspected count spans 395 (Africa CDC PHECS, 18 May 2026) to almost 750 (WHO Director-General Member State briefing, 22 May 2026). CDC's 21 May structured tuple reports 575 suspected cases, ECDC's 21 May update carries the WHO-derived approximately-600 suspected cross-check, and the archived 20 May consensus aggregator reports 653; WHO's newer official briefing is the headline endpoint.",
            "Deaths span 106 (Africa CDC PHECS, 18 May 2026) to 177 suspected deaths (WHO Director-General Member State briefing, 22 May 2026). Earlier anchors remain in the conflict trail: ECDC 130 on 19 May, WHO/ECDC 139 on 20/21 May, the archived 20 May consensus aggregator 144, and CDC 148 on 21 May.",
            "Confirmed count spans 10 (WHO PHEIC statement, 17 May 2026, case data as of 16 May: 8 Ituri + 2 Kampala; Kinshasa case deconfirmed) to 84 total country-scope confirmed cases (WHO Director-General Member State briefing, 22 May 2026: 82 DRC + 2 imported Uganda). CDC's 21 May structured tuple is superseded for the headline endpoint but retained as dated conflict evidence.",
            "Spatial model source zones use the newest official per-health-zone confirmed-count table in the manifest: WHO AFRO SitRep-01 (data as of 18 May 2026) lists confirmed cases in Bunia, Butembo, Goma, Katwa, Mongbwalu, Nyankunde, and Rwampara. The May 22 WHO headline aggregate is newer and larger, but it is not a zone-attributed line list; corridor source load therefore uses the official per-zone vector rather than applying the aggregate count to every zone.",
            "CDC 21 May reports the outbreak in 11 DRC health zones in Ituri and Nord-Kivu as of 20 May but does not publish a zone-attributed count table. WHO 22 May keeps Uganda at 2 imported cases including 1 death, and the IHR Emergency Committee temporary recommendations state that no onward Uganda transmission among contacts was documented as of 22 May. One American national was evacuated from DRC to Germany and confirmed positive; a high-risk contact was reportedly transferred to Czechia. The reported Kinshasa case was deconfirmed by INRB and is not counted as confirmed.",
            "Per-source archive status: all cited sources are registered in data/bundibugyo-2026/manifest.json. WHO DON 602, WHO PHEIC, WHO DG remarks on 20 and 22 May, WHO IHR temporary recommendations, WHO AFRO landing page, CDC HAN, CDC Current Situation, ECDC May 19/21, and the consensus aggregator are byte-archived with SHA-256; Africa CDC, Imperial, and PAHO/WHO alert PDF are hash-recorded with restricted raw publisher bytes kept private pending terms or permission confirmation.",
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
    """Serialize a ReconciledCount with public-release friendly key names."""
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
        "sources": list(snapshot.sources),
        "source_conflict_notes": list(snapshot.source_conflict_notes),
        "visibility": {
            "grade": vp.visibility_grade,
            "history_snapshot_count": len(visibility_history),
            "method_basis": "single_snapshot_prior_proxy",
            "method_caveat": (
                "No prior daily snapshot series is supplied to Module C for this release; "
                "reporting completeness and latency are prior/proxy-based, using the "
                "Camacho 2015 EBOV-Zaire onset-to-notification delay as a Bundibugyo proxy."
            ),
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
        "mode_b_hypotheses": mode_b,
        "calibration_clock": cal_clock,
        "calibration_blocks": cal_blocks,
        "scope_id": "epi:bdbv-uga-cod-2026",
        "resolves_at": carried["resolves_at"],
        "revision_note": (
            "Snapshot is as of 2026-05-22. The new surveillance inputs are the WHO "
            "Director-General Member State briefing and the WHO IHR Emergency Committee "
            "temporary recommendations, both published 2026-05-22 and byte-archived. "
            "WHO now reports 82 confirmed DRC cases, seven confirmed DRC deaths, "
            "almost 750 suspected cases, 177 suspected deaths, and two imported Uganda "
            "cases including one death. The headline confirmed aggregate is therefore "
            "84 total country-scope confirmed cases (82 DRC + 2 Uganda). CDC 21 May and "
            "ECDC 21 May remain dated conflict/cross-check evidence rather than current "
            "headline denominators. This is still not the fuller WHO AFRO/DRC line-list "
            "style release needed for zone-attributed counts. "
            "Candidate target zones include arua-uga and nebbi-uga to close the "
            "documented Mahagi/Goli<->Arua cross-border blindspot. The "
            "pre-committed calibration points are carried forward UNCHANGED from "
            "data/calibration-ledger.json; no pin was re-derived. Mobility and "
            "confirmation-latency leverages are held as situational inputs "
            "(run_local) and are not injected into this provenance-strict public "
            "snapshot. See data/external_sources/."
        ),
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
