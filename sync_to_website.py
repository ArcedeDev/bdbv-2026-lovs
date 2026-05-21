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
import json
import os
import pathlib
import re
import shutil
import sys
from typing import Any

from lovs import lovs_death_back_projection as dbp
from lovs import lovs_onset_to_death as otd


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

    timeline = build_timeline(manifest, current_date=date)
    calibration_points = build_calibration_points(mode_b)
    sources = build_sources()
    methodology_constants = build_methodology_constants()
    reported_counts = pipeline_output.get("reported_counts", {})

    def unique_source_ids(ids: list[str | None]) -> list[str]:
        out: list[str] = []
        for source_id in ids:
            if source_id and source_id not in out:
                out.append(source_id)
        return out

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
        source_ids = unique_source_ids(
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
    # Uganda/Kampala confirmed cases are an external anchor, not carried in the
    # pipeline's reported_counts aggregate, so this stays a documented literal
    # (see confirmedByCountry.contextNote below) rather than a derived field.
    uganda_confirmed = 2
    drc_confirmed = max(0, confirmed_range["primary"] - uganda_confirmed)

    return {
        "date": date,
        "asOf": pipeline_output["as_of"],
        "outbreakId": pipeline_output["outbreak_id"],
        "pathogen": "BDBV",
        "countryScope": ["COD", "UGA"],
        # Only the three Ituri Province health zones explicitly named by
        # WHO DON 602 and Africa CDC PHECS. Goma / North Kivu is tracked
        # separately as a spillover entry, not as a source zone for the
        # corridor model. The reported Kinshasa case was deconfirmed by INRB
        # and is kept in zones.json only as an audit note.
        "affectedZones": ["mongbwalu", "bunia", "rwampara"],
        "reportedCounts": {
            # Official / regional anchors: WHO PHEIC (17 May), ECDC
            # (19 May), and WHO Director-General remarks (20 May).
            "confirmed": confirmed_range,
            "suspected": suspected_range,
            "deaths": deaths_range,
        },
        "confirmedByCountry": {
            # WHO Director-General remarks (20 May): 51 confirmed in DRC and
            # 2 confirmed in Uganda.
            "cod": drc_confirmed,
            "uga": uganda_confirmed,
            "contextNote": (
                "Uganda count: 2 confirmed in Kampala (1 death) per WHO PHEIC "
                "statement, Africa CDC PHECS declaration, and WHO 20 May "
                "remarks. WHO reported 51 confirmed cases in DRC across Ituri "
                "and North Kivu, including Bunia and Goma. The reported "
                "Kinshasa case tested negative on confirmatory INRB testing "
                "and is not counted as confirmed. No documented local Uganda "
                "transmission as of this as-of date; symptomatic contacts "
                "under investigation in Fort Portal following burial "
                "attendance in DRC were tracked in the archived 20 May "
                "consensus source."
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
                    "sourceId": "wikipedia-2026-ituri-epidemic-2026-05-20",
                    "label": "20 May consensus aggregator",
                },
            ],
        },
        "healthcareWorkers": {"deaths": 4},
        "timeline": timeline,
        "visibility": {
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
        },
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
        "methodology_constants": methodology_constants,
        "resolvesAt": pipeline_output.get("resolves_at", "2026-06-19T23:59:59Z"),
        "sources": sources,
        "sourceConflictNotes": build_source_conflict_notes(),
    }


def build_source_conflict_notes() -> list[dict[str, Any]]:
    """Structured reconciliation notes with source IDs for inline refs."""
    return [
        {
            "text": (
                "Suspected count spans 395 (Africa CDC PHECS, 18 May 2026) "
                "to 653 (archived 20 May consensus aggregator citing news and "
                "agency sources). ECDC reports over 500 on 19 May; WHO DG remarks "
                "on 20 May give the official same-day approximate anchor of "
                "almost 600 suspected cases."
            ),
            "sourceIds": [
                "africa-cdc-phecs-2026-05-18",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
            ],
        },
        {
            "text": (
                "Deaths span 106 (Africa CDC PHECS, 18 May 2026) to 144 "
                "(archived 20 May consensus aggregator). ECDC reports 130 on "
                "19 May; WHO DG remarks on 20 May report 139 suspected deaths."
            ),
            "sourceIds": [
                "africa-cdc-phecs-2026-05-18",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
            ],
        },
        {
            "text": (
                "Confirmed count spans 10 (WHO PHEIC statement, 17 May 2026, "
                "case data as of 16 May: 8 Ituri + 2 Kampala; Kinshasa case "
                "deconfirmed) to 53 (WHO Director-General remarks, 20 May "
                "2026: 51 DRC + 2 Kampala), with ECDC reporting 30 on 19 May "
                "and the archived 20 May consensus aggregator reporting 51."
            ),
            "sourceIds": [
                "who-pheic-2026-05-17",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
            ],
        },
        {
            "text": (
                "Geographic spread beyond the three Ituri Province HZ: "
                "confirmed DRC cases in North Kivu including Goma per WHO 20 "
                "May remarks; 2 confirmed in Kampala (Uganda, including 1 "
                "death); 1 American national evacuated from DRC to Germany "
                "and confirmed positive. The reported Kinshasa case was "
                "deconfirmed by INRB and is not counted as confirmed. Fort "
                "Portal Uganda had symptomatic contacts under investigation "
                "but no lab-confirmed local Uganda transmission in the "
                "archived 20 May consensus source."
            ),
            "sourceIds": [
                "who-pheic-2026-05-17",
                "who-dg-remarks-bdbv-2026-05-20",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
            ],
        },
        {
            "text": (
                "Per-source archive status: all cited sources are registered "
                "in data/bundibugyo-2026/manifest.json. WHO DON 602, WHO "
                "PHEIC, WHO DG remarks, WHO AFRO landing page, CDC HAN, ECDC, "
                "and the consensus aggregator are byte-archived with SHA-256; "
                "Africa CDC and Imperial are hash-recorded with restricted raw "
                "publisher bytes kept private pending terms or permission "
                "confirmation."
            ),
            "sourceIds": [
                "who-don602-2026-05-15",
                "who-pheic-2026-05-17",
                "who-dg-remarks-bdbv-2026-05-20",
                "afro-sitrep-01-2026-05-18",
                "cdc-han-00530-2026-05",
                "ecdc-bdbv-drc-uga-2026-05-19",
                "wikipedia-2026-ituri-epidemic-2026-05-20",
                "africa-cdc-phecs-2026-05-18",
                "imperial-mrc-gida-bdbv-2026-05-18",
            ],
        },
    ]


def _timeline_figures(manifest: dict[str, Any]) -> dict[str, dict]:
    """Canonical source_id -> normalized_content (strips the -live ingest suffix)."""
    figures: dict[str, dict] = {}
    for entry in manifest.get("entries", []):
        source_id = entry.get("source_id", "")
        canonical = source_id[: -len("-live")] if source_id.endswith("-live") else source_id
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
    manifest: dict[str, Any] | None, current_date: str
) -> list[dict[str, Any]]:
    """Public-reporting timeline points to display in the trajectory chart.

    Every count is pulled from the dated source manifest by canonical source id;
    only the per-date source-and-field selection (the reconciliation policy) lives
    here. A missing source or field fails loudly rather than shipping a stale
    number. The 20 May endpoint is a reconciled mix: confirmed from the WHO
    Director-General remarks, suspected and deaths from the archived consensus
    aggregator.
    """
    if manifest is None:
        raise ValueError("build_timeline requires the source manifest")
    figures = _timeline_figures(manifest)

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
            # 20 May: WHO Director-General remarks report 51 DRC + 2 Kampala;
            # suspected and deaths retain the archived consensus endpoint,
            # with WHO DG approximate same-day anchors noted separately.
            "date": current_date,
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
    ]


def build_calibration_points(mode_b: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach plain-language statements to each Mode-B calibration point."""
    corridor_statements: dict[str, str] = {
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
    for entry in mode_b:
        statement = corridor_statements.get(
            entry["corridor"],
            f"Calibration point for corridor {entry['corridor']}.",
        )
        out.append(
            {
                "hypothesisId": entry["hypothesis_id"],
                "corridor": entry["corridor"],
                "riskAdjusted50": list(entry["risk_adj_50"]),
                "statement": statement,
            }
        )
    return out


def build_sources() -> list[dict[str, Any]]:
    """Sources cited in the snapshot. Every entry MUST be a real, dated,
    retrievable document, with a URL where one is publicly available. The
    archive_status field declares whether public bytes are shipped under
    data/bundibugyo-2026/raw/ or only hash-recorded because the raw publisher
    bytes are restricted.
    """
    return [
        {
            "id": "who-don602-2026-05-15",
            "label": "WHO Disease Outbreak News item 2026-DON602: Ebola disease caused by Bundibugyo virus, DRC and Uganda (15 May 2026)",
            "publishedAt": "2026-05-15",
            "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON602",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "who-pheic-2026-05-17",
            "label": "WHO Director-General PHEIC determination statement (17 May 2026)",
            "publishedAt": "2026-05-17",
            "url": "https://www.who.int/news/item/17-05-2026-epidemic-of-ebola-disease-in-the-democratic-republic-of-the-congo-and-uganda-determined-a-public-health-emergency-of-international-concern",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "who-dg-remarks-bdbv-2026-05-20",
            "label": "WHO Director-General opening remarks at the media briefing on Ebola outbreak in DRC and Uganda (20 May 2026)",
            "publishedAt": "2026-05-20",
            "url": "https://www.who.int/news-room/speeches/item/who-director-general-s-opening-remarks-at-the-media-briefing-on-ebola-outbreak-in-drc-and-uganda-20-may-2026",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "afro-sitrep-01-2026-05-18",
            "label": "WHO African Region Weekly External Situation Report 01 (data as of 18 May 2026)",
            "publishedAt": "2026-05-18",
            "url": "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-uganda-weekly-external-situation",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "cdc-han-00530-2026-05",
            "label": "US CDC Health Alert Network notice HAN00530: Ebola Disease Outbreak in DRC and Uganda",
            "publishedAt": "2026-05",
            "url": "https://www.cdc.gov/han/php/notices/han00530.html",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "imperial-mrc-gida-bdbv-2026-05-20",
            "label": "Imperial College MRC Centre for Global Infectious Disease Analysis (with WHO HEP, WHO Uganda, WHO AFRO). Estimation of the size of the BDBV outbreak in DRC, 20 May 2026 update: 400-900 cases estimated (values over 1,000 not excluded). Supersedes the 18 May report; CFR bands corrected to 26/33/40 and deaths updated to 131.",
            "publishedAt": "2026-05-20",
            "url": "https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-update-20-05-2026/",
            "archiveStatus": "hash-recorded-private-raw",
        },
        {
            "id": "imperial-mrc-gida-bdbv-2026-05-18",
            "label": "Imperial College MRC Centre for Global Infectious Disease Analysis (with WHO HEP, WHO Uganda, WHO AFRO). Estimation of the size of the BDBV outbreak in DRC, 18 May 2026 (superseded by the 20 May update).",
            "publishedAt": "2026-05-18",
            "url": "https://www.imperial.ac.uk/mrc-global-infectious-disease-analysis/research-themes/preparedness-and-response-to-emerging-threats/report-ebola-18-05-2026/",
            "archiveStatus": "hash-recorded-private-raw",
        },
        {
            "id": "africa-cdc-phecs-2026-05-18",
            "label": "Africa CDC declaration of a Public Health Emergency of Continental Security on the Bundibugyo Ebola outbreak, on recommendation of the Emergency Consultative Group chaired by Prof. Salim Abdool Karim (18 May 2026)",
            "publishedAt": "2026-05-18",
            "url": "https://africacdc.org/news-item/africa-cdc-declares-the-ongoing-bundibugyo-ebola-outbreak-a-public-health-emergency-of-continental-security/",
            "archiveStatus": "hash-recorded-private-raw",
        },
        {
            "id": "ecdc-bdbv-drc-uga-2026-05-19",
            "label": "European Centre for Disease Prevention and Control outbreak page: Ebola virus disease outbreak in the Democratic Republic of the Congo and Uganda (19 May 2026)",
            "publishedAt": "2026-05-19",
            "url": "https://www.ecdc.europa.eu/en/ebola-virus-disease-outbreak-democratic-republic-congo-and-uganda-19-may-2026",
            "archiveStatus": "byte-archived",
        },
        {
            "id": "wikipedia-2026-ituri-epidemic-2026-05-20",
            "label": "Wikipedia article '2026 Ituri Province Ebola epidemic', accessed 20 May 2026. Consensus aggregator citing Reuters, BBC News, U.S. CDC Health Alert Network, MSF, ECDC, AP, NYT, Al Jazeera, CNN, Imperial College London as primary sources for the 19-20 May case figures.",
            "publishedAt": "2026-05-20",
            "url": "https://en.wikipedia.org/wiki/2026_Ituri_Province_Ebola_epidemic",
            "archiveStatus": "byte-archived",
        },
    ]


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
    """Copy SVG visuals and the brief PDF into the website's public/ dir."""
    copied: list[str] = []
    public_root = website_root / "public" / "bdbv-2026"
    public_root.mkdir(parents=True, exist_ok=True)

    visuals_src = brief_root / "deliverables" / "visuals"
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
    return copied


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

    if args.dry_run:
        asset_count = 0
        visuals_src = REPO_ROOT / "deliverables" / "visuals"
        if visuals_src.exists():
            asset_count += sum(1 for _ in visuals_src.glob("*.svg"))
        if (REPO_ROOT / "deliverables" / "brief.pdf").exists():
            asset_count += 1
        print(f"[dry-run] would copy {asset_count} assets")
    else:
        assets = copy_assets(REPO_ROOT, args.website_root)
        for a in assets:
            print(f"copied public/bdbv-2026/{a}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
