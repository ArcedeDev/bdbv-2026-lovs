"""Outside checks on the death-ascertainment band.

An outside check is an estimate of deaths in one place and period from a source outside the
official line list, such as a count of new graves on satellite imagery. It is turned into the
share of those deaths that the official count captures, the implied death ascertainment, and
reported against ``DEATH_ASCERTAINMENT_BAND`` every cycle.

Rules every check follows:

* It is reported, never fitted. Nothing in the nowcast reads a check back, so no estimate
  changes when a check is added or removed.
* Its official figure is a range on the same basis as the outside estimate, built from named
  SitRep rows. A test rebuilds them from ``data/sitrep_promotions`` by this rule, from the
  zone, province and ``period_end`` alone, and requires the record to match. The low end is
  the zone row alone, from the SitRep dated ``period_end``. The high end adds the deaths the
  zone gained in the reviewed one-day table that assigned the province's deaths not assigned
  to a zone (Tableau 2, "A ventiler"), declared in that test, net of that table's own new
  deaths and capped at the deaths unassigned at ``period_end``. A table resolves only the
  run of SitReps around it in which deaths stayed unassigned without a break. Leaving the
  unassigned deaths out makes the gap look several times larger; such a gain can also hold
  adjustments and moves between zones, so the high end is likely an upper figure, not a
  strict bound. Where no deaths were unassigned, ``harmonised_gain`` is None and the two ends are
  equal.
* Its outside estimate is a range built the same way for every check. The low end is what the
  source counted against a baseline (excess deaths). The high end adds what the source reports
  without a baseline, such as graves described by officials (``outside_high_kind``, with the
  range read from the source in ``outside_unbaselined``). When the source reports nothing
  else, the two ends are equal. When the source gives only a floor ("more than 1,000"),
  ``outside_low_is_floor`` is set and the served range says "or lower". The basis states
  each flag in a sentence ("N is a floor" or "N is not a floor"; "it is derived from the
  official toll" or "it is not derived from the official toll"). A source that gives the
  high end only as a floor needs its own served wording before it is added. A figure the source gives only as a
  ratio to the official toll is marked ``outside_derived_from_official``, with the SitRep row
  and ratio it comes from (``outside_derivation``), and its served line says so.
* It is derived, not printed: the implied range is this file's arithmetic on the source's
  figures, and each end of both ranges says how it was reached.
* It is local. One zone's value is not the national value. Excess deaths include deaths that
  were not Ebola deaths, and a town's cemeteries can hold people from neighbouring zones; both
  would raise each implied range. Burial sites a source did not count would lower it.
* Its served line, often the only text a reader sees, names the source, its method, its
  publication date, the kind of source, what the outside figure is made of, whether it is
  derived in part from the official toll, and that it is local.
* It names a health zone, never a burial site, a coordinate or a person.

Adding a check is part of ingesting new sources (PIPELINE.md, step 1): add an entry, cite the
source in CITATIONS.md, and run ``tests/test_external_death_checks.py`` before the release
command.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

EXTERNAL_DEATH_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "check_id": "reuters-graves-bunia-2026-11",
        "source_name": "Reuters",
        "source_kind": "media",
        "method": "grave_count",
        "zone": "Bunia",
        "province": "Ituri",
        "period_end": "2026-11-03",
        "period_text": "to early November 2026",
        "published": "2026-11-23",
        "source": (
            "Reuters, 23 November 2026: graves dug in 2026 at Bunia's three main cemeteries on "
            "satellite imagery, against the epidemic-free year before"
        ),
        "official_rows": {
            "zone_row": {"sitrep": 173, "deaths": 707},
            "harmonised_gain": {
                "before_sitrep": 168, "after_sitrep": 169, "gross": 252, "reported_that_day": 6,
                "deaths": 246,
            },
        },
        "official_deaths": {"low": 707, "high": 953},
        "comparison_basis": (
            "low: 707 confirmed deaths in the SitRep 173 zone row (3 November), counting none of "
            "the 493 Ituri deaths not assigned to a zone. high: 953, adding the 246 deaths Bunia "
            "gained in SitRep 169's one-day harmonised table (938 against 686 in SitRep 168, net "
            "of the 6 deaths SitRep 169 reported that day, which the zone row already counts). "
            "That gain mixes Bunia's share of the unassigned deaths with part of 314 retrospective "
            "adjustments that SitRep 170 withdrew, so 953 is likely an upper figure; the same "
            "table also moved records between zones, so it is not a strict bound"
        ),
        "outside_deaths": {"low": 1000, "high": 1000},
        "outside_low_is_floor": True,
        "outside_high_kind": "excess",
        "outside_unbaselined": None,
        "outside_derived_from_official": False,
        "outside_derivation": None,
        "outside_basis": (
            "low and high: 1,000. 1,000 is a floor; it is not derived from the official toll. It "
            "is the floor of the more than 1,000 excess deaths Reuters counted at the three "
            "main cemeteries by early November, so both ends of the implied range are upper "
            "readings. Reuters also puts the excess at more than 50% above the official 707, "
            "which is more than about 1,060 and would give 0.67 to 0.90. Reuters reports no "
            "figure for the smaller burial sites it did not count, which would lower the implied "
            "range. The city's cemeteries may also hold people from neighbouring zones, which "
            "would raise it"
        ),
    },
    {
        "check_id": "reuters-graves-mongbwalu-2026-08",
        "source_name": "Reuters",
        "source_kind": "media",
        "method": "grave_count",
        "zone": "Mongbwalu",
        "province": "Ituri",
        "period_end": "2026-08-31",
        "period_text": "to August 2026",
        "published": "2026-11-23",
        "source": (
            "Reuters, 23 November 2026: graves at Mongbwalu's largest cemetery on satellite "
            "imagery, against the year before, and graves at seven other burial grounds that "
            "workers and officials described in July"
        ),
        "official_rows": {
            "zone_row": {"sitrep": 109, "deaths": 286},
            "harmonised_gain": {
                "before_sitrep": 168, "after_sitrep": 169, "gross": 22, "reported_that_day": 1,
                "deaths": 21,
            },
        },
        "official_deaths": {"low": 286, "high": 307},
        "comparison_basis": (
            "low: 286 confirmed deaths in the SitRep 109 zone row (31 August), counting none of "
            "the 339 Ituri deaths not assigned to a zone at that date. high: 307, adding the 21 "
            "deaths Mongbwalu gained in SitRep 169's harmonised table (368 against 346 in SitRep "
            "168, net of the 1 death reported that day), likely an upper figure for the same "
            "reasons as in Bunia; some of them may also "
            "be deaths after August. The August row is used, not a later one (365 at SitRep 189), "
            "because Reuters compares the cemetery by August with the toll then"
        ),
        "outside_deaths": {"low": 358, "high": 758},
        "outside_low_is_floor": False,
        "outside_high_kind": "described_graves",
        "outside_unbaselined": {"low": 200, "high": 400},
        "outside_derived_from_official": True,
        "outside_derivation": {"sitrep": 109, "ratio": 1.25},
        "outside_basis": (
            "low: about 358. 358 is not a floor; it is derived from the official toll. It is the "
            "excess deaths at the largest cemetery against the year before, derived: "
            "Reuters gives only a ratio, 25% above the official toll by August, without saying "
            "which day's toll. It is taken here as 286, the SitRep 109 row (31 August) used for "
            "the official figure, so both sides refer to the same date; the August SitReps (79 to "
            "109) print 261 to 286, and an earlier toll would give fewer excess deaths and a "
            "higher implied range. Reuters' other Mongbwalu figure, official tallies of over 350, "
            "is the toll at publication, not in August; each of its Bunia ratios uses the toll at "
            "the date it compares, so the August ratio is read against an August toll. high: "
            "758, adding 400 graves, the top of the several hundred that workers and officials "
            "described in July at seven other burial grounds, read here as 200 to 400; they were "
            "not counted on imagery or against a baseline. On the counted "
            "cemetery alone the implied range is 0.80 to 0.86; if all the described graves were "
            "excess deaths, it would be 0.38 to 0.55"
        ),
    },
)

CHECKS_NOTE = (
    "Outside estimates of deaths in one place, reported against the death-ascertainment band "
    "and never fitted. Each is local; the national blend is unknown. Excess deaths include "
    "deaths that were not Ebola deaths, and a town's cemeteries can hold people from "
    "neighbouring zones, which would raise each implied range; burial sites a source did not "
    "count would lower it."
)

# Served wording, keyed by the record's structured fields. A new kind needs its own wording.
SOURCE_KIND_WORDS = {"media": "a media estimate"}
METHOD_WORDS = {"grave_count": "grave counts"}
_DERIVED_WORDS = "derived in part from the official toll"
OUTSIDE_HIGH_KIND_WORDS = {
    "excess": "of excess deaths",
    "described_graves": "of excess deaths, up to graves described but not counted",
}
_LOCAL_WORDS = "not all from Ebola; one place, not the national value"

_RELATION_WORDS = {
    "inside": "inside the band",
    "below": "below the band",
    "above": "above the band",
    "partly_below": "partly below the band",
    "partly_above": "partly above the band",
    "spans": "wider than the band on both sides",
}


_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)


def _long_date(iso: str) -> str:
    """'2026-11-23' -> '23 November 2026', independent of the locale."""
    day = date.fromisoformat(iso)
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def implied_death_ascertainment(check: dict[str, Any]) -> tuple[float, float]:
    """Return the unrounded (low, high) implied death ascertainment of one check.

    low is the official low end over the outside high end; high is the official high end over
    the outside low end. Each is capped at 1.0, since the official count cannot capture more
    deaths than occurred. A malformed record raises ``ValueError`` naming the check.
    """
    check_id = check.get("check_id", "<no check_id>")
    try:
        official_lo = check["official_deaths"]["low"]
        official_hi = check["official_deaths"]["high"]
        outside_lo = check["outside_deaths"]["low"]
        outside_hi = check["outside_deaths"]["high"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"outside death check {check_id}: official_deaths and outside_deaths must each be "
            f"a mapping with low and high"
        ) from exc
    values = (official_lo, official_hi, outside_lo, outside_hi)
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        raise ValueError(f"outside death check {check_id}: death counts must be integers")
    if not (0 <= official_lo <= official_hi and 0 < outside_lo <= outside_hi):
        raise ValueError(
            f"outside death check {check_id}: need 0 <= official low <= official high and "
            f"0 < outside low <= outside high"
        )
    return min(1.0, official_lo / outside_hi), min(1.0, official_hi / outside_lo)


def relation_to_band(implied: Sequence[float], band: Sequence[float]) -> str:
    """Name where an implied range sits against the band's (low, central, high) edges.

    Pass the unrounded range: rounding first can move a value across an edge.
    """
    lo, hi = implied
    band_lo, band_hi = band[0], band[-1]
    if hi < band_lo:
        return "below"
    if lo > band_hi:
        return "above"
    if lo >= band_lo and hi <= band_hi:
        return "inside"
    if lo < band_lo and hi > band_hi:
        return "spans"
    return "partly_below" if lo < band_lo else "partly_above"


def label(check: dict[str, Any]) -> str:
    """The served name of a check: source, method, publication date, zone and period.

    A method with no served wording raises ``ValueError`` naming the check.
    """
    method = check.get("method")
    if method not in METHOD_WORDS:
        raise ValueError(
            f"outside death check {check.get('check_id', '<no check_id>')}: no served wording "
            f"for method {method!r}"
        )
    return (
        f"{check['source_name']} {METHOD_WORDS[method]} "
        f"(published {_long_date(check['published'])}), {check['zone']} {check['period_text']}"
    )


def caveat(check: dict[str, Any]) -> str:
    """The served caveat of a check, from its structured fields.

    A kind with no served wording raises ``ValueError`` naming the check.
    """
    kind, high_kind = check.get("source_kind"), check.get("outside_high_kind")
    if kind not in SOURCE_KIND_WORDS or high_kind not in OUTSIDE_HIGH_KIND_WORDS:
        raise ValueError(
            f"outside death check {check.get('check_id', '<no check_id>')}: no served wording "
            f"for source_kind {kind!r} or outside_high_kind {high_kind!r}"
        )
    derived = f", {_DERIVED_WORDS}" if check.get("outside_derived_from_official") else ""
    return f"{SOURCE_KIND_WORDS[kind]} {OUTSIDE_HIGH_KIND_WORDS[high_kind]}{derived}; {_LOCAL_WORDS}"


def assess_against_band(
    band: Sequence[float],
    checks: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report each check's implied death ascertainment and where it sits against ``band``.

    ``checks`` defaults to ``EXTERNAL_DEATH_CHECKS``, read at call time.
    """
    checks = EXTERNAL_DEATH_CHECKS if checks is None else checks
    rows = []
    for check in checks:
        implied = implied_death_ascertainment(check)
        rows.append({
            "check_id": check["check_id"],
            "label": label(check),
            "caveat": caveat(check),
            "zone": check["zone"],
            "province": check["province"],
            "period_end": check["period_end"],
            "published": check["published"],
            "source_kind": check["source_kind"],
            "method": check["method"],
            "source": check["source"],
            "official_deaths": dict(check["official_deaths"]),
            "comparison_basis": check["comparison_basis"],
            "outside_deaths": dict(check["outside_deaths"]),
            "outside_high_kind": check["outside_high_kind"],
            "outside_low_is_floor": check["outside_low_is_floor"],
            "outside_unbaselined": (
                dict(check["outside_unbaselined"]) if check.get("outside_unbaselined") else None
            ),
            "outside_derived_from_official": check["outside_derived_from_official"],
            "outside_derivation": (
                dict(check["outside_derivation"]) if check.get("outside_derivation") else None
            ),
            "outside_basis": check["outside_basis"],
            "implied_death_ascertainment": [round(implied[0], 2), round(implied[1], 2)],
            "relation_to_band": relation_to_band(implied, band),
            "derived": True,
        })
    return {
        "band": [band[0], band[-1]],
        "checks": rows,
        "note": CHECKS_NOTE,
    }


def source_lines(
    band: Sequence[float],
    checks: Sequence[dict[str, Any]] | None = None,
) -> list[str]:
    """One line per check for the methodology row's sources, formatted from the report rows."""
    return [
        f"Outside check, reported and not fitted: {row['label']} ({row['caveat']}): implied "
        f"death ascertainment {row['implied_death_ascertainment'][0]:.2f}-"
        f"{row['implied_death_ascertainment'][1]:.2f}"
        f"{' or lower' if row['outside_low_is_floor'] else ''}, "
        f"{_RELATION_WORDS[row['relation_to_band']]}"
        f"{' at these upper readings' if row['outside_low_is_floor'] else ''}"
        for row in assess_against_band(band, checks)["checks"]
    ]
