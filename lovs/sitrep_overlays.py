# SPDX-License-Identifier: Apache-2.0
"""SitRep-derived overlays for the published BDBV snapshot (SitRep19 Phase B).

This module is the GENERATION half of three website-snapshot surfaces. Each
overlay is built deterministically from already-reviewed source-of-truth
(the reviewed SitRep promotion payloads + the reconciled headline source ids),
never hand-typed, so a future source advance cannot leave the published surface
stale:

  * ``confirmed_death_series`` -- the apples-to-apples country-scope confirmed
    death history (26 May 18, 29 May 43, 30 May 43, 31 May 49, 1 Jun 61,
    2 Jun 63). The website renderer projects each dated point onto its timeline
    row as ``deathsConfirmed`` and stamps ``deathsBasis`` per date. The broad
    register (``timeline[].deaths``) is a separate, suspected-basis series that
    ENDS at 1 Jun; the confirmed series rides ``deathsConfirmed``.

  * ``province_burden`` -- the always-fresh June-2 province confirmed/death
    floor from SitRep #019 Table 1 (Ituri 341, North Kivu 19, South Kivu 3).

  * ``headline_source_clock`` -- the headline-count-endpoint source clock,
    DERIVED from ``reported_counts.confirmed.primary_source_id`` so the published
    clock can never name a SitRep the headline no longer rides.

Contract field names (the website renderer reads these exact keys):
  timeline[].deathsConfirmed: number|null
  timeline[].deathsBasis: 'suspected' | 'confirmed'
  provinceBurden: [{province, confirmed, confirmedDeaths, asOf, sourceId}]
  dateSemantics.sourceClocks[headline_count_endpoint]: source id

ND-correct: a date with no confirmed figure carries ``deathsConfirmed=None``
(not reported, never zero); a province with no reported confirmed-death figure
carries ``confirmedDeaths=None``.

Stdlib only.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lovs import sitrep_promotions


# The death tier became laboratory-confirmed only on this date. Timeline rows
# dated on/after it carry the confirmed basis; earlier rows are the broad
# (suspected) register. Single source of truth shared with the gate and the
# public exporter (export_public_health_dataset._DEATH_BASIS_CUTOFF).
DEATH_BASIS_CUTOFF = "2026-06-02"

# The headline-count-endpoint clock key the website dateSemantics block exposes.
HEADLINE_COUNT_ENDPOINT = "headline_count_endpoint"

# The May-26 country-scope confirmed-death base point that precedes SitRep #015.
# Its value (17 DRC confirmed deaths + 1 Uganda confirmed death = 18) is the
# reconciled base headline; the components are sourced from the manifest below
# rather than hard-typed, so a manifest edit that moves either component is
# reflected here. The primary source id is the DRC component's build (the
# national_moh release); the Uganda anchor is recorded as a contributing source.
_BASE_DEATHS_DRC_SOURCE_ID = "inrb-umie-ebola-drc-2026-build-2026-05-28-bb8b7d5"
_BASE_DEATHS_UGANDA_SOURCE_ID = "ecdc-bdbv-drc-uga-2026-05-27"
_BASE_DEATHS_AS_OF = "2026-05-26"


def death_basis_for_date(row_date: Any) -> str:
    """Return the contract death basis ('suspected'|'confirmed') for a date.

    Death rows dated on/after the 2026-06-02 cutoff are ``confirmed`` (the
    laboratory-confirmed tier); earlier dated death rows are ``suspected`` (the
    broad register). Compared as a plain ISO-prefix string -- no wall-clock read,
    fully deterministic for a fixed date.
    """
    date_text = str(row_date or "")[:10]
    if date_text and date_text >= DEATH_BASIS_CUTOFF:
        return "confirmed"
    return "suspected"


def _canonical_source_id(source_id: str) -> str:
    """Strip the ``-live`` manifest suffix so a clock names the public id."""
    return source_id[: -len("-live")] if source_id.endswith("-live") else source_id


def _manifest_figure(
    manifest: Mapping[str, Any], source_prefix: str, field: str
) -> int | None:
    """Pull an integer ``normalized_content`` field from the first matching entry.

    Matches a source id by prefix (so the ``-live`` suffix variant resolves).
    Returns None when the source or field is absent or non-integer (ND-correct:
    a missing component is "not reported", surfaced as None rather than zero).
    """
    entries = manifest.get("entries", []) if isinstance(manifest, Mapping) else []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("source_id", "")).startswith(source_prefix):
            value = (entry.get("normalized_content") or {}).get(field)
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
    return None


def _base_confirmed_deaths(manifest: Mapping[str, Any]) -> int | None:
    """Reconstruct the 26 May country-scope confirmed-death base (17 + 1 = 18).

    DRC component from the build release, Uganda component from the ECDC 27 May
    page; summed exactly as ``refresh_pipeline`` composes the base headline. ND:
    if either component is missing, returns None (no fabricated base point).
    """
    drc = _manifest_figure(manifest, _BASE_DEATHS_DRC_SOURCE_ID, "deaths_confirmed_drc")
    uganda = _manifest_figure(manifest, _BASE_DEATHS_UGANDA_SOURCE_ID, "deaths_uganda")
    if drc is None or uganda is None:
        return None
    return drc + uganda


def confirmed_death_series(
    manifest: Mapping[str, Any],
    promotions_by_number: Mapping[int, Mapping[str, Any]] | None = None,
    *,
    base_value: int | None = None,
) -> list[dict[str, Any]]:
    """Build the apples-to-apples country-scope confirmed-death history.

    Each point is ``{date, deathsConfirmed, basis, sourceId}`` sourced from the
    reviewed SitRep promotion's ``country_scope_confirmed_deaths`` figure (plus
    the 26 May reconciled base). Points are sorted by date and de-duplicated by
    date (a later SitRep on the same data date wins).

    The 26 May base (18 = 17 DRC confirmed deaths + 1 Uganda) is composed from
    the manifest by default; the caller may pass ``base_value`` explicitly (the
    canonical generator passes the reconciled base headline so the point resolves
    even when the public manifest sanitizes the restricted death components). ND:
    when neither the manifest components nor an explicit base resolve, the base
    point is omitted rather than fabricated.

    ``basis`` is the contract death basis at that date; every confirmed point is
    therefore stamped, and the website renderer reuses it as ``deathsBasis`` for
    the confirmed series while the broad register rides ``deathsBasis='suspected'``.
    """
    if promotions_by_number is None:
        promotions_by_number = sitrep_promotions.reviewed_promotions_by_number()

    by_date: dict[str, dict[str, Any]] = {}

    if base_value is None:
        base_value = _base_confirmed_deaths(manifest)
    if base_value is not None:
        by_date[_BASE_DEATHS_AS_OF] = {
            "date": _BASE_DEATHS_AS_OF,
            "deathsConfirmed": base_value,
            "basis": death_basis_for_date(_BASE_DEATHS_AS_OF),
            "sourceId": _BASE_DEATHS_DRC_SOURCE_ID,
        }

    for number in sorted(promotions_by_number):
        promotion = promotions_by_number[number]
        figures = promotion.get("figures") or {}
        value = figures.get("country_scope_confirmed_deaths")
        if not isinstance(value, int) or isinstance(value, bool):
            # ND-correct: a SitRep that does not publish a country-scope confirmed
            # death is skipped rather than carried as zero.
            continue
        date = str(promotion.get("data_as_of") or "")[:10]
        if not date:
            continue
        by_date[date] = {
            "date": date,
            "deathsConfirmed": value,
            "basis": death_basis_for_date(date),
            "sourceId": _canonical_source_id(str(promotion.get("source_id") or "")),
        }

    return [by_date[date] for date in sorted(by_date)]


def province_burden(
    sitrep19: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the always-fresh June-2 province confirmed/death floor.

    Sourced from SitRep #019's ``figures.health_zone_table.province_totals``
    (Ituri 341, Nord-Kivu 19, Sud-Kivu 3). Each row is the contract shape
    ``{province, confirmed, confirmedDeaths, asOf, sourceId}`` with ``asOf`` the
    SitRep data date and ``sourceId`` its (public) source id. ``confirmedDeaths``
    is None when the province row does not report a confirmed-death figure (ND).

    Raises ``KeyError``/``TypeError`` via the caller if the promotion is not the
    SitRep #019 shape; the generator never invents province rows.
    """
    figures = sitrep19.get("figures") or {}
    table = figures.get("health_zone_table") or {}
    totals = table.get("province_totals") or []
    as_of = str(sitrep19.get("data_as_of") or "")[:10]
    source_id = _canonical_source_id(str(sitrep19.get("source_id") or ""))

    rows: list[dict[str, Any]] = []
    for entry in totals:
        if not isinstance(entry, Mapping):
            continue
        province = entry.get("province")
        confirmed = entry.get("confirmed")
        if province is None or not isinstance(confirmed, int) or isinstance(confirmed, bool):
            continue
        deaths = entry.get("confirmed_deaths")
        confirmed_deaths = (
            deaths if isinstance(deaths, int) and not isinstance(deaths, bool) else None
        )
        rows.append(
            {
                "province": str(province),
                "confirmed": confirmed,
                "confirmedDeaths": confirmed_deaths,
                "asOf": as_of,
                "sourceId": source_id,
            }
        )
    return rows


# health_zone_table display name -> LOVS canonical zone id. The -cod suffixes
# are explicit; everything else lowercases the name (Miti-Murhesa -> miti-murhesa).
# Makiso-Kisangani lowercases to "makiso-kisangani" but the gazetteer entry is the
# older -cod id, so it is aliased here rather than duplicating the zone.
_PER_ZONE_DISPLAY_ALIAS = {
    "Beni": "beni-cod",
    "Goma": "goma-cod",
    "Makiso-Kisangani": "makiso-kisangani-cod",
    "Nai-Nia": "nia-nia",
    "Nia Nia": "nia-nia",
    "Nia-Nia": "nia-nia",
}


def per_zone_canonical_id(name: str) -> str:
    """Return the LOVS zone id for a SitRep Table-1 display name."""
    if name in _PER_ZONE_DISPLAY_ALIAS:
        return _PER_ZONE_DISPLAY_ALIAS[name]
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _per_zone_canonical_id(name: str) -> str:
    return per_zone_canonical_id(name)


def per_zone_display(
    sitrep19: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the SitRep19 Table-1 per-zone DISPLAY layer for the map markers.

    This is a display overlay only: it surfaces the fresh 2-Jun per-health-zone
    confirmed/confirmed-death counts (Bunia 85/8, Rwampara 72/15, ... incl. Logo
    1/0 and the new Mambasa 2/1) so the map markers and shading read the current
    SitRep. It does NOT change the corridor source-load, which stays the validated
    INSP per-zone block (the U1 re-base). The unventilated Ituri residual
    ("Autres ZS") is carried as a non-zone aggregate, never smeared across zones.

    Returns {} when the promotion is not the SitRep #019 health-zone-table shape;
    the generator never invents zone rows.
    """
    figures = sitrep19.get("figures") or {}
    table = figures.get("health_zone_table") or {}
    rows = table.get("rows") or []
    if not rows:
        return {}
    as_of = str(sitrep19.get("data_as_of") or "")[:10]
    source_id = _canonical_source_id(str(sitrep19.get("source_id") or ""))

    zones: list[dict[str, Any]] = []
    unventilated: dict[str, Any] | None = None
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("zone") or "")
        confirmed = entry.get("confirmed")
        if not isinstance(confirmed, int) or isinstance(confirmed, bool):
            continue
        deaths = entry.get("confirmed_deaths")
        confirmed_deaths = (
            deaths if isinstance(deaths, int) and not isinstance(deaths, bool) else None
        )
        province = str(entry.get("province") or "")
        if "ventil" in name.lower():  # "Autres ZS (donnees non ventilees)" residual
            unventilated = {
                "confirmed": confirmed,
                "confirmedDeaths": confirmed_deaths,
                "province": province,
            }
            continue
        zones.append(
            {
                "zoneId": _per_zone_canonical_id(name),
                "zoneName": name,
                "confirmed": confirmed,
                "confirmedDeaths": confirmed_deaths,
                "province": province,
            }
        )
    # The display residual is every confirmed case not shown on a named-zone
    # marker: the explicit "Autres ZS (non ventilees)" row PLUS any province whose
    # Table 1 total exceeds the sum of its named Table 2 rows (e.g. Nord-Kivu at
    # SitRep 55, whose 158/89 province total exceeds its 155/88 named rows by 3/1).
    # Compute it as national - sum(displayed zones) so the display layer reconciles
    # to the DRC headline; fall back to the explicit ventil row when the national
    # totals are unavailable. For a coherent single-residual source (e.g. SitRep 54)
    # this equals the explicit ventil row, so the change is backward-compatible.
    recon = table.get("reconciliation") if isinstance(table.get("reconciliation"), dict) else {}
    national_conf = recon.get("national_confirmed_total")
    if not isinstance(national_conf, int) or isinstance(national_conf, bool):
        national_conf = figures.get("cumul_cas_confirmes_drc")
    national_deaths = recon.get("national_confirmed_deaths_total")
    if not isinstance(national_deaths, int) or isinstance(national_deaths, bool):
        national_deaths = figures.get("cumul_deces_parmi_confirmes_drc")
    if isinstance(national_conf, int) and not isinstance(national_conf, bool):
        zone_conf_sum = sum(z["confirmed"] for z in zones)
        zone_death_sum = sum((z["confirmedDeaths"] or 0) for z in zones)
        residual_deaths = (
            national_deaths - zone_death_sum
            if isinstance(national_deaths, int) and not isinstance(national_deaths, bool)
            else (unventilated or {}).get("confirmedDeaths")
        )
        unventilated = {
            "confirmed": national_conf - zone_conf_sum,
            "confirmedDeaths": residual_deaths,
            "province": (unventilated or {}).get("province", ""),
        }
    return {
        "asOf": as_of,
        "sourceId": source_id,
        "basis": (
            "Reviewed SitRep Table 1 health-zone rows for map markers/shading; "
            "the unventilated residual (national confirmed minus the sum of named "
            "displayed zones) is retained as residual and not allocated to named "
            "zones. It comprises the explicit 'Autres ZS' row plus any province "
            "whose Table 1 total exceeds the sum of its named Table 2 rows."
        ),
        "zones": sorted(zones, key=lambda z: (-z["confirmed"], z["zoneId"])),
        "unventilatedResidual": unventilated,
    }


def headline_source_clock(confirmed_primary_source_id: str | None) -> dict[str, Any]:
    """Build the headline-count-endpoint source clock, DERIVED from the source.

    Returns ``{HEADLINE_COUNT_ENDPOINT: <public source id>}``. The value is the
    canonical (public) form of ``reported_counts.confirmed.primary_source_id``,
    so the published clock is a generated consequence of the headline source and
    can never name a SitRep the headline no longer rides. When the headline has
    no confirmed primary, the endpoint clock is omitted (empty dict).
    """
    if not confirmed_primary_source_id:
        return {}
    return {HEADLINE_COUNT_ENDPOINT: _canonical_source_id(str(confirmed_primary_source_id))}


def assert_headline_clock_matches_source(
    source_clocks: Mapping[str, Any], confirmed_primary_source_id: str | None
) -> None:
    """Generation invariant: the endpoint clock equals the confirmed source.

    FAILs (ValueError) when ``sourceClocks[headline_count_endpoint]`` is present
    but does not equal the canonical ``reported_counts.confirmed.primary_source_id``.
    Run at generation time so a hand-edited or stale clock can never ship; the
    release gate enforces the same binding on the published artifact.
    """
    clock_value = source_clocks.get(HEADLINE_COUNT_ENDPOINT)
    expected = (
        _canonical_source_id(str(confirmed_primary_source_id))
        if confirmed_primary_source_id
        else None
    )
    if clock_value is None and expected is None:
        return
    clock_canonical = _canonical_source_id(str(clock_value)) if clock_value else None
    if clock_canonical != expected:
        raise ValueError(
            "sourceClocks[headline_count_endpoint] "
            f"({clock_canonical!r}) does not match the headline confirmed "
            f"primary_source_id ({expected!r})"
        )
