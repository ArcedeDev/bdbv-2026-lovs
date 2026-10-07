# SPDX-License-Identifier: Apache-2.0
"""Calibration Block 11 (international spread), generated from the reviewed registry.

International events in this outbreak are rare: three first detections outside the
DRC in 21 weeks. One block can resolve only a handful of outcomes, so the block is
built to be repeated monthly with fixed methods, and the outcomes accumulate into a
comparable series.

Every hazard question is priced twice, from the same registry, by two fixed methods:

- the recent method: the per-period probability of an event over the last 13 weekly
  periods, Jeffreys-smoothed. It is this block's forecaster of record, because the
  outbreak's own data show the cross-border hazard changing (Uganda reported no new
  case for 15 weeks after 2026-06-21 while the DRC's cumulative count rose eightfold);
- the stationary method: the same estimate over every weekly period since the outbreak
  was declared, priced beside it as the comparison.

A period is seven days counted back from the window opening, so the estimate does not
depend on how often a source was sampled. Destination questions split the hazard by the
Jeffreys-smoothed share of first detections in DRC land neighbours, from the method's
own window. The Kenya question asks whether a recognised importation is followed by a
further case, priced from a documented reference class of past importations.

The registry (data/international-events.json) is both the substrate, read only up to
the source cutoff and frozen by hash, and the resolution evidence: opsresolver reads
the same predicates (`qualifying_reports`) after the window. Nothing here is authored;
a test regenerates every pin and fails on any difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "data" / "operational-calibration-ledger.json"
REGISTRY = REPO / "data" / "international-events.json"
REFERENCE = REPO / "data" / "importation-reference-class.json"
REGISTRY_RELATIVE = "data/international-events.json"
REFERENCE_RELATIVE = "data/importation-reference-class.json"

OUTBREAK = "bdbv-uga-cod-2026"
BLOCK11_ID = f"operational-block:{OUTBREAK}:2026-10-07:international"
OUTBREAK_DECLARED = dt.date(2026, 5, 15)
SOURCE_CUTOFF = "2026-10-06"
PINNED_AT = "2026-10-07"
REGISTRATION_DEADLINE_UTC = "2026-10-07T23:59:59Z"
WINDOW_OPENS = "2026-10-08"
RESOLVES_AT = "2026-11-04T23:59:59Z"
HORIZON_DAYS = 28
PERIOD_DAYS = 7
WINDOW_PERIODS = HORIZON_DAYS // PERIOD_DAYS
RECENT_PERIODS = 13
EVIDENCE_GRACE_DAYS = 3

RECENT = "recent_hazard_13w"
STATIONARY = "stationary_hazard"
REFERENCE_METHOD = "importation_reference_class"
METHOD_TAG = {RECENT: "recent13", STATIONARY: "stationary"}
# Block 11's country sets, frozen here so a later block can define its own without moving
# these questions. The registry records the sets as of registration for readers.
BASELINE_COUNTRIES = ("COD", "UGA", "FRA", "KEN")
EVACUATION_ONLY = ("DEU", "NLD", "USA")
DRC_LAND_NEIGHBOURS = ("AGO", "BDI", "CAF", "COG", "RWA", "SSD", "TZA", "UGA", "ZMB")
COUNTRY_NAMES = {
    "AGO": "Angola", "BDI": "Burundi", "CAF": "the Central African Republic", "COD": "the DRC",
    "COG": "the Republic of the Congo", "DEU": "Germany", "FRA": "France", "KEN": "Kenya",
    "NLD": "the Netherlands", "RWA": "Rwanda", "SSD": "South Sudan", "TZA": "Tanzania",
    "UGA": "Uganda", "USA": "the United States", "ZMB": "Zambia",
}
# Every first detection outside the DRC: the history behind the three new-country questions.
# A country in today's affected set was new when it was first reported, so it counts here.
ANY_FIRST_DETECTION = {"rule": "first_report_in_country", "exclude_countries": ["COD"]}

# The substrate is frozen by report id, not by date: a report published later on the cutoff
# day, after the freeze, is not substrate. The resolver voids any question such a report
# would have decided before the window opened. A test pins the digest.
SUBSTRATE_REPORT_IDS = (
    "uga-2026-05-15", "uga-2026-05-16", "uga-2026-05-23", "uga-2026-05-25", "uga-2026-06-02",
    "uga-2026-06-06", "uga-2026-06-21", "fra-2026-06-24", "ken-2026-10-06",
)
# The reference class is frozen the same way, so episodes added after resolution (this
# block's own Kenya outcome included) never change its price or digest.
REFERENCE_EPISODE_IDS = (
    "lbr-2014-03-lofa", "nga-2014-07-lagos", "sen-2014-08-dakar", "usa-2014-09-dallas",
    "usa-2014-10-nyc", "mli-2014-10-kayes", "gbr-2014-12-glasgow", "ita-2015-05-sardinia",
    "lbr-2016-03-monrovia", "uga-2019-06-kasese", "uga-2019-08-kasese", "uga-2026-05-kampala",
    "fra-2026-06-paris",
)
SUBSTRATE_SHA256 = "4427d7c2c5cf8f56e05d425ace970e3858d5b5622bbde3669eb6272291d1b316"
SUBSTRATE_REVIEWED_AT = "2026-10-07T00:24:15Z"


class RegistrationError(RuntimeError):
    """A registration fact is inconsistent; nothing may be generated."""


# --- Registry -------------------------------------------------------------------

def _day(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value[:10]) if value else None


def load_registry(path: Path = REGISTRY) -> dict:
    registry = json.loads(path.read_text(encoding="utf-8"))
    validate_registry(registry)
    return registry


def load_reference(path: Path = REFERENCE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(registry: Mapping[str, Any]) -> None:
    """Refuse a registry whose reports could be misread.

    - Every report, retraction and attribution cites a source; report ids are unique.
    - A country's reports appear in date order in the list, because "first" is read in
      list order.
    - Every report raises the country's running total: ``new_confirmed`` is at least 1
      and equals the cumulative count minus the running total.
    - A retraction withdraws one whole report of the same country (its ``cases`` equal
      that report's ``new_confirmed``) and lowers the running total from its date.
    - An attribution adds a fact stated after a report (``new_local_confirmed`` or
      ``confirmed_on``) to that report, within the report's own counts and dates.
    - A coverage review names its sources and covers only whole days before it was made.
    """
    reports = registry["reports"]
    by_id: dict[str, Mapping[str, Any]] = {}
    last_day: dict[str, dt.date] = {}
    for report in reports:
        rid, country, day = report["report_id"], report["country"], _day(report["reported_on"])
        if rid in by_id:
            raise RegistrationError(f"report id {rid} appears twice")
        by_id[rid] = report
        if not report.get("sources"):
            raise RegistrationError(f"{rid} cites no source")
        if len(country) != 3 or not country.isupper():
            raise RegistrationError(f"{rid}: country must be an ISO-3 code")
        if country in last_day and day < last_day[country]:
            raise RegistrationError(f"{rid}: reports for {country} are out of date order in the list")
        last_day[country] = day
        _check_facts(rid, report, report.get("new_local_confirmed"), report.get("confirmed_on"))
    for retraction in registry.get("retractions", []):
        target = by_id.get(retraction["report_id"])
        if target is None or not retraction.get("sources"):
            raise RegistrationError(f"retraction of {retraction['report_id']} is unknown or uncited")
        if retraction["country"] != target["country"] or retraction["cases"] != target["new_confirmed"]:
            raise RegistrationError(f"retraction of {retraction['report_id']} must withdraw that whole report")
        if _day(retraction["retracted_on"]) < _day(target["reported_on"]):
            raise RegistrationError(f"retraction of {retraction['report_id']} predates the report")
    for attribution in registry.get("attributions", []):
        target = by_id.get(attribution["report_id"])
        if target is None or not attribution.get("sources"):
            raise RegistrationError(f"attribution to {attribution['report_id']} is unknown or uncited")
        if _day(attribution["stated_on"]) < _day(target["reported_on"]):
            raise RegistrationError(f"attribution to {attribution['report_id']} predates the report")
        _check_facts(attribution["report_id"], target, attribution.get("new_local_confirmed"),
                     attribution.get("confirmed_on"))
    running: dict[str, int] = {}
    events = [(_day(r["reported_on"]), 0, i, r) for i, r in enumerate(reports)]
    events += [(_day(r["retracted_on"]), 1, i, r) for i, r in enumerate(registry.get("retractions", []))]
    for _day_, kind, _i, entry in sorted(events, key=lambda e: (e[0], e[1], e[2])):
        country = entry["country"]
        if kind == 1:
            running[country] = running.get(country, 0) - int(entry["cases"])
            continue
        if entry["new_confirmed"] < 1:
            raise RegistrationError(f"{entry['report_id']}: a report must raise the count")
        if entry["new_confirmed"] != entry["cumulative_confirmed"] - running.get(country, 0):
            raise RegistrationError(f"{entry['report_id']}: new_confirmed does not match the running total")
        running[country] = entry["cumulative_confirmed"]
    for review in registry.get("coverage_reviews", []):
        if not (review.get("reviewed_at") and review.get("reviewed_through") and review.get("sources_checked")):
            raise RegistrationError("a coverage review needs reviewed_at, reviewed_through and sources_checked")
        if _day(review["reviewed_through"]) >= _day(review["reviewed_at"]):
            raise RegistrationError("a coverage review covers only whole days before it was made")


def _check_facts(rid: str, report: Mapping[str, Any], local: int | None, confirmed: str | None) -> None:
    if local is not None and not 0 <= local <= report["new_confirmed"]:
        raise RegistrationError(f"{rid}: new_local_confirmed outside 0..new_confirmed")
    if confirmed and _day(confirmed) > _day(report["reported_on"]):
        raise RegistrationError(f"{rid}: confirmed_on after reported_on")


def effective_reports(registry: Mapping[str, Any], by: dt.date) -> list[dict]:
    """Reports in list order, with attributions stated by ``by`` applied, retractions
    published by ``by`` honoured (the report is dropped), and nothing published later."""
    withdrawn = {r["report_id"] for r in registry.get("retractions", []) if _day(r["retracted_on"]) <= by}
    facts: dict[str, dict] = {}
    for attribution in sorted(registry.get("attributions", []), key=lambda a: a["stated_on"]):
        if _day(attribution["stated_on"]) <= by:
            facts.setdefault(attribution["report_id"], {}).update(
                {k: attribution[k] for k in ("new_local_confirmed", "confirmed_on") if attribution.get(k) is not None})
    out = []
    for report in registry["reports"]:
        if report["report_id"] in withdrawn:
            continue
        out.append({**report, **facts.get(report["report_id"], {})})
    return out


def substrate_reports(registry: Mapping[str, Any]) -> list[dict]:
    """The frozen substrate: the reports named in SUBSTRATE_REPORT_IDS, in registry order.

    Refuses a registry that lacks one of them or dates one after the cutoff. Reports
    appended later, whatever their date, are not substrate.
    """
    chosen = [r for r in registry["reports"] if r["report_id"] in SUBSTRATE_REPORT_IDS]
    missing = set(SUBSTRATE_REPORT_IDS) - {r["report_id"] for r in chosen}
    if missing:
        raise RegistrationError(f"substrate reports missing from the registry: {sorted(missing)}")
    late = [r["report_id"] for r in chosen if _day(r["reported_on"]) > _day(SOURCE_CUTOFF)]
    if late:
        raise RegistrationError(f"substrate reports dated after the cutoff: {late}")
    return chosen


def substrate_digest(registry: Mapping[str, Any], reference: Mapping[str, Any]) -> str:
    """sha256 of everything the prices read: the substrate reports, the country sets and
    the reference-class episodes. Later reports and coverage reviews do not change it, so
    the registry can grow into the resolution evidence while the substrate stays fixed."""
    payload = {
        "reports": substrate_reports(registry),
        "baseline_affected_countries": list(BASELINE_COUNTRIES),
        "evacuation_only": list(EVACUATION_ONLY),
        "drc_land_neighbours": list(DRC_LAND_NEIGHBOURS),
        "reference_episodes": [
            {k: e[k] for k in ("episode_id", "outcome_local_transmission")}
            for e in reference_episodes(reference)
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- Predicates: one definition for pricing and for resolution --------------------

def qualifying_reports(rule: Mapping[str, Any], reports: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Reports that would satisfy a pin's rule, ignoring dates.

    ``first_report_in_country``: a country's first report of a confirmed case, for a
    country outside ``exclude_countries`` and, when given, inside ``countries_in``.
    ``new_case_report``: any report raising the count of a country in ``countries_in``;
    with ``require_local``, only a report in which the authority states at least one new
    case was acquired in that country. Pass reports through ``effective_reports`` first so
    retractions and later-stated facts apply. Planned medical evacuations are not reports.
    """
    kind = rule["rule"]
    if kind not in ("first_report_in_country", "new_case_report"):
        raise ValueError(f"unknown international rule {kind!r}")
    exclude = set(rule.get("exclude_countries") or ())
    within = set(rule["countries_in"]) if rule.get("countries_in") else None
    out = []
    first_seen: set[str] = set()
    for report in reports:
        if report["new_confirmed"] < 1:
            continue
        country = report["country"]
        is_first = country not in first_seen
        first_seen.add(country)
        if country in exclude or (within is not None and country not in within):
            continue
        if kind == "first_report_in_country" and not is_first:
            continue
        if rule.get("require_local") and (report.get("new_local_confirmed") or 0) < 1:
            continue
        out.append(dict(report))
    return out


# --- Methods ----------------------------------------------------------------------

def periods(window_opens: dt.date = _day(WINDOW_OPENS), declared: dt.date = OUTBREAK_DECLARED
            ) -> list[tuple[dt.date, dt.date]]:
    """Seven-day periods counted back from the window opening, most recent first, to the
    period holding the outbreak declaration."""
    out = []
    end = window_opens - dt.timedelta(days=1)
    while True:
        start = end - dt.timedelta(days=PERIOD_DAYS - 1)
        out.append((start, end))
        if start <= declared:
            return out
        end = start - dt.timedelta(days=1)


def jeffreys(k: int, n: int) -> float:
    return (k + 0.5) / (n + 1)


def positive_periods(event_days: Sequence[dt.date], spans: Sequence[tuple[dt.date, dt.date]]) -> int:
    return sum(1 for start, end in spans if any(start <= d <= end for d in event_days))


def hazard(event_days: Sequence[dt.date], spans: Sequence[tuple[dt.date, dt.date]],
           share: float = 1.0) -> dict:
    """P(at least one event in the window's periods) from a per-period Bernoulli rate."""
    k, n = positive_periods(event_days, spans), len(spans)
    rate = jeffreys(k, n) * share
    return {"positive_periods": k, "periods": n, "per_period": rate,
            "probability": 1 - (1 - rate) ** WINDOW_PERIODS}


def method_spans(method: str) -> list[tuple[dt.date, dt.date]]:
    spans = periods()
    return spans[:RECENT_PERIODS] if method == RECENT else spans


def neighbour_share(first_reports: Sequence[Mapping[str, Any]], neighbours: set[str],
                    spans: Sequence[tuple[dt.date, dt.date]]) -> dict:
    lo, hi = spans[-1][0], spans[0][1]
    inside = [r for r in first_reports if lo <= _day(r["reported_on"]) <= hi]
    k = sum(1 for r in inside if r["country"] in neighbours)
    return {"neighbour_first_detections": k, "first_detections": len(inside),
            "share": jeffreys(k, len(inside))}


def reference_episodes(reference: Mapping[str, Any]) -> list[dict]:
    """The frozen reference class: the episodes named in REFERENCE_EPISODE_IDS."""
    chosen = [e for e in reference["episodes"] if e["episode_id"] in REFERENCE_EPISODE_IDS]
    missing = set(REFERENCE_EPISODE_IDS) - {e["episode_id"] for e in chosen}
    if missing:
        raise RegistrationError(f"reference episodes missing: {sorted(missing)}")
    return chosen


def reference_probability(reference: Mapping[str, Any]) -> dict:
    episodes = reference_episodes(reference)
    yes = sum(1 for e in episodes if e["outcome_local_transmission"])
    return {"episodes": len(episodes), "with_local_transmission": yes,
            "probability": jeffreys(yes, len(episodes))}


# --- Block ------------------------------------------------------------------------

def check_timing() -> None:
    deadline = dt.datetime.fromisoformat(REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
    opens = _day(WINDOW_OPENS)
    if opens != deadline.date() + dt.timedelta(days=1):
        raise RegistrationError(f"window_opens {opens} is not the UTC day after the deadline")
    if _day(PINNED_AT) > deadline.date() or _day(SOURCE_CUTOFF) > _day(PINNED_AT):
        raise RegistrationError("cutoff, pin date and deadline are out of order")
    if (_day(RESOLVES_AT) - opens).days + 1 != HORIZON_DAYS:
        raise RegistrationError("the window is not HORIZON_DAYS long")


def _countries(codes: Sequence[str], names: Mapping[str, str]) -> str:
    named = [names[c] for c in codes]
    return ", ".join(named[:-1]) + " and " + named[-1] if len(named) > 1 else named[0]


# Shared wording, so the nested questions cannot drift apart on what counts.
_AUTHORITY = ("first reported by WHO, ECDC, US CDC, Africa CDC, or that country's ministry of health "
              "or national public health agency (a press report alone does not count)")
_EVACUATION = ("A case in a person moved by a planned medical evacuation does not count, whether "
               "confirmed before or after the transfer; a case counts for the country whose authority "
               "reports its laboratory confirmation.")


def _questions(registry: Mapping[str, Any]) -> list[dict]:
    names = COUNTRY_NAMES
    baseline = list(BASELINE_COUNTRIES)
    neighbours = [c for c in DRC_LAND_NEIGHBOURS if c not in baseline]
    window = f"between {WINDOW_OPENS} and {RESOLVES_AT[:10]}"
    affected = _countries(baseline, names)
    evacuation_only = _countries(EVACUATION_ONLY, names)
    new_country = (f"A country that has only treated evacuated patients or appears on an affected-country "
                   f"list because of them ({evacuation_only}) can still report its first case.")
    return [
        {"key": "new-country", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline},
         "split": None,
         "question": (f"Will a country other than {affected} report its first laboratory-confirmed "
                      f"case of this outbreak, {_AUTHORITY}, {window}? {new_country} {_EVACUATION}")},
        {"key": "new-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline,
                  "countries_in": neighbours},
         "split": "neighbour",
         "question": (f"Will a DRC land neighbour not yet affected ({_countries(neighbours, names)}) "
                      f"report its first laboratory-confirmed case of this outbreak, {_AUTHORITY}, "
                      f"{window}? {_EVACUATION}")},
        {"key": "new-non-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country",
                  "exclude_countries": sorted(set(baseline) | set(DRC_LAND_NEIGHBOURS))},
         "split": "non_neighbour",
         "question": (f"Will a country that is neither a DRC land neighbour nor one of {affected} "
                      f"report its first laboratory-confirmed case of this outbreak, {_AUTHORITY}, "
                      f"{window}? {new_country} {_EVACUATION}")},
        {"key": "uganda-case", "forecast_type": "country_case_occurrence",
         "history": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "rule": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "split": None,
         "question": (f"Will Uganda report at least one new laboratory-confirmed case (imported or "
                      f"locally acquired), {_AUTHORITY}, {window}? Uganda's count has stood at 20 "
                      f"since 2026-06-21. {_EVACUATION}")},
    ]


def _event_days(rule: Mapping[str, Any], reports: Sequence[Mapping[str, Any]]) -> list[dt.date]:
    return [_day(r["reported_on"]) for r in qualifying_reports(rule, reports)]


def _hazard_pin(q: Mapping[str, Any], method: str, reports, registry) -> dict:
    spans = method_spans(method)
    days = _event_days(q["history"], reports)
    share_note = None
    if q["split"]:
        share_note = neighbour_share(qualifying_reports(q["history"], reports),
                                     set(DRC_LAND_NEIGHBOURS), spans)
        fraction = share_note["share"] if q["split"] == "neighbour" else 1 - share_note["share"]
        result = hazard(days, spans, fraction)
    else:
        result = hazard(days, spans)
    role = "incumbent" if method == RECENT else "challenger"
    first, last = spans[-1][0], spans[0][1]
    pin = {
        "pin_id": f"operational-pin:{OUTBREAK}:28d:intl-{q['key']}-{METHOD_TAG[method]}",
        "question_id": f"block11:{q['key']}",
        "method": method,
        "role": role,
        "metric": "international_case_reports",
        "shape": "event",
        "threshold": 1,
        "forecast_type": q["forecast_type"],
        "public_question": q["question"],
        "probability": round(result["probability"], 4),
        "horizon_days": HORIZON_DAYS,
        "generator": {
            "periods": result["periods"],
            "positive_periods": result["positive_periods"],
            "history": f"{first.isoformat()} to {last.isoformat()}",
            "per_period_probability": round(result["per_period"], 6),
            "window_periods": WINDOW_PERIODS,
        },
        "resolution_rule": dict(q["rule"]),
    }
    if share_note:
        pin["generator"]["neighbour_share"] = {k: (round(v, 6) if isinstance(v, float) else v)
                                               for k, v in share_note.items()}
    return pin


def _kenya_pin(reference: Mapping[str, Any]) -> dict:
    ref = reference_probability(reference)
    return {
        "pin_id": f"operational-pin:{OUTBREAK}:28d:intl-kenya-further-case",
        "question_id": "block11:kenya-further-case",
        "method": REFERENCE_METHOD,
        "role": "record",
        "metric": "international_case_reports",
        "shape": "event",
        "threshold": 1,
        "forecast_type": "onward_transmission_occurrence",
        "public_question": (
            "Will Kenya report at least one further laboratory-confirmed case acquired in Kenya (a "
            "contact, health worker or other locally acquired case, not a new importation), "
            f"{_AUTHORITY}, between {WINDOW_OPENS} and {RESOLVES_AT[:10]}? The case counts only when "
            "the reporting authority states that it was acquired in Kenya; the imported case "
            "confirmed on 2026-10-06 does not count."),
        "probability": round(ref["probability"], 4),
        "horizon_days": HORIZON_DAYS,
        "generator": {
            "reference_class": REFERENCE_RELATIVE,
            "episodes": ref["episodes"],
            "with_local_transmission": ref["with_local_transmission"],
            "estimator": "Jeffreys-smoothed proportion (yes + 0.5) / (episodes + 1)",
        },
        "resolution_rule": {"rule": "new_case_report", "countries_in": ["KEN"], "require_local": True},
    }


def _registration() -> dict:
    return {
        "source_cutoff": {"reports_through": SOURCE_CUTOFF, "substrate_reviewed_at": SUBSTRATE_REVIEWED_AT},
        "substrate_report_ids": list(SUBSTRATE_REPORT_IDS),
        "inputs": [{"path": REGISTRY_RELATIVE, "scope": "the reports named in substrate_report_ids"},
                   {"path": REFERENCE_RELATIVE, "scope": "reference-class episodes"}],
        "substrate_sha256": SUBSTRATE_SHA256,
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "evidence_grace_days": EVIDENCE_GRACE_DAYS,
        "public_registration": (
            "Public when the pinning commit first reaches the public repository. That UTC time "
            "and commit are appended afterwards as registration_receipt. If the commit is not "
            "public by registration_deadline_utc, the block is regenerated with a new deadline "
            "before anything is published. The resolver scores nothing without a receipt and "
            "voids every pin whose receipt is later than the deadline."),
        "pre_push_rule": (
            "The sources are checked once more immediately before the push. A qualifying report "
            "found then is never added to the substrate or used to reprice: it is recorded in the "
            "registry after registration and voids the questions it would decide, under the void "
            "rule."),
        "designer_exposure": [
            "Design session: had read SitRep 143 (data 2026-10-04) and every public report of "
            "Kenya's imported case (WHO Regional Office for Africa, 2026-10-06) before designing. "
            "Every event public at the substrate review is in the substrate; the questions concern "
            f"only reports first published from {WINDOW_OPENS}.",
            "The 13-period recent window and the choice of the recent method as forecaster of "
            "record were made after both methods' prices had been computed. Both are disclosed, "
            "both methods are public, and the decision rule can reverse the choice.",
            "The reference-class coding was reconsidered at review with both candidate prices "
            "visible (0.393 for the brief's separate episodes, 0.500 for merging concurrent "
            "arrivals in one country). The brief's coding was kept because merging reverses the "
            "brief's own labelling; the merged reading is reported as a sensitivity.",
            "Founder: asked for an international spread block on 2026-10-06; set no question, "
            "probability, window or method parameter.",
        ],
    }


def _pre_registration() -> dict:
    return {
        "comparison": {
            "incumbent": f"{RECENT}: forecaster of record for this block",
            "challenger": f"{STATIONARY}: priced beside it as the comparison",
            "pairing": "every hazard question is priced by both methods from the same registry",
        },
        "unit_of_inference": (
            "the question. The three new-country questions share one hazard and are nested, not "
            "independent; Uganda is a separate series."),
        "primary_endpoint": (
            "mean paired Brier difference, stationary minus recent, over resolved hazard questions, "
            "pooled across international blocks priced by the same two methods"),
        "decision_rule": (
            "evaluated once, on exactly the first 12 paired hazard questions to resolve across "
            "international blocks (ordered by resolution date, then ledger order), by "
            "lovs/forecast_scoring.paired_block_decision(need=8, of_metrics=12) with question ids "
            "as units: the stationary method becomes the forecaster of record if the mean "
            "difference is below zero and it is better on at least 8 of the 12. A question on "
            "which both methods score the same counts as not better. Until 12 pairs have resolved "
            "the comparison is descriptive only. Pins keep their registered roles permanently."),
        "known_asymmetry": (
            "the recent method prices lower than the stationary method on all four hazard "
            "questions, so a quiet window favours it on every pair; a busy window favours the "
            "stationary method. The decision rule pools across blocks for that reason."),
        "void_rule": (
            "a question is void for every method when a report that would satisfy it is not in the "
            f"frozen substrate (substrate_report_ids) but was first published before {WINDOW_OPENS}, "
            f"or when an in-window report states a confirmation date before {WINDOW_OPENS}. A void "
            "question is reported, never scored. The Kenya question is therefore conditional on no "
            "further locally acquired case being reported before the window opens."),
        "descriptive_statistics": [
            "Brier and log score per method for this block",
            "per-period hazard realised in the window against each method's estimate",
            "for the Kenya question, the reference class updated with this outcome",
        ],
        "unscoreable_policy": (
            f"without a coverage review through {EVIDENCE_GRACE_DAYS} days after the resolution "
            "date, made no earlier than that and naming the sources checked, every pin is "
            "unscoreable_unreviewed; silence never resolves a pin NO"),
    }


def build_block(registry: Mapping[str, Any] | None = None,
                reference: Mapping[str, Any] | None = None) -> dict:
    """Block 11, deterministically. Raises before generating anything doubtful."""
    check_timing()
    registry = registry if registry is not None else load_registry()
    reference = reference if reference is not None else load_reference()
    digest = substrate_digest(registry, reference)
    if SUBSTRATE_SHA256 and digest != SUBSTRATE_SHA256:
        raise RegistrationError(f"substrate digest {digest} does not match {SUBSTRATE_SHA256}")
    reports = substrate_reports(registry)
    points = []
    for q in _questions(registry):
        points.extend(_hazard_pin(q, m, reports, registry) for m in (RECENT, STATIONARY))
    points.append(_kenya_pin(reference))
    return {
        "block_id": BLOCK11_ID,
        "label": "Block 11",
        "pinned_at": PINNED_AT,
        "window_opens": WINDOW_OPENS,
        "resolves_at": RESOLVES_AT,
        "horizon_days": HORIZON_DAYS,
        "status": "active",
        "rationale": (
            "Block 11, international spread: the first of a monthly series priced by fixed methods, "
            "because international events are too rare for one block to teach much. It asks whether "
            "a new country, a new DRC land neighbour or a new non-neighbour reports its first case, "
            "whether Uganda reports any new case, and whether Kenya's 2026-10-06 importation is "
            "followed by a further case. Each hazard question is priced by the recent method "
            "(forecaster of record) and the stationary method (comparison); the methods ignore "
            "context the registry does not encode, such as the Kenya patient's transit through "
            "Uganda, and that is deliberate."),
        "registration_note": (
            "Generated by lovs/forecast/pins_block11.build_block() from data/international-events.json "
            "(reports through the cutoff) and data/importation-reference-class.json."),
        "registration": _registration(),
        "pre_registration": _pre_registration(),
        "points": points,
    }


def append_to_ledger(path: Path = LEDGER) -> str:
    """Append Block 11. Refuses to touch a ledger that already holds it or any of its pins."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    block = build_block()
    if any(b["block_id"] == block["block_id"] for b in ledger["blocks"]):
        raise RegistrationError(f"ledger already holds {block['block_id']}; pinned blocks are never rewritten")
    ids = {p["pin_id"] for b in ledger["blocks"] for p in b["points"]}
    for pin in block["points"]:
        if pin["pin_id"] in ids:
            raise RegistrationError(f"pin id {pin['pin_id']} already in the ledger")
    ledger["blocks"].append(block)
    ledger["_meta"]["generators"][block["block_id"]] = "lovs/forecast/pins_block11.py"
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    return block["block_id"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--append", action="store_true", help="append the block to the ledger")
    parser.add_argument("--digest", action="store_true", help="print the substrate digest")
    args = parser.parse_args()
    if args.digest:
        print(substrate_digest(load_registry(), load_reference()))
    elif args.append:
        print(append_to_ledger())
    else:
        print(json.dumps(build_block(), indent=1))
