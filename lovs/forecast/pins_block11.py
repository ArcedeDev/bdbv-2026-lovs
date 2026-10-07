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
SOURCE_CUTOFF = "2026-10-07"
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
# Every first detection outside the DRC: the history behind the three new-country questions.
# A country in today's affected set was new when it was first reported, so it counts here.
ANY_FIRST_DETECTION = {"rule": "first_report_in_country", "exclude_countries": ["COD"]}

# Filled in when the registry and reference class are frozen; a test pins both.
SUBSTRATE_SHA256 = "8d87bee46e797addc6074155d2a9b254b4eb517ad097318eec5479aaef098f1e"


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

    Every report cites a source, dates are ordered, and each country's cumulative
    count never falls, so 'new_confirmed' is recomputable from the running total.
    """
    seen: dict[str, int] = {}
    last_day: dict[str, dt.date] = {}
    for report in registry["reports"]:
        rid = report["report_id"]
        if not report.get("sources"):
            raise RegistrationError(f"{rid} cites no source")
        if len(report["country"]) != 3 or not report["country"].isupper():
            raise RegistrationError(f"{rid}: country must be an ISO-3 code")
        reported = _day(report["reported_on"])
        confirmed = _day(report.get("confirmed_on"))
        if confirmed and confirmed > reported:
            raise RegistrationError(f"{rid}: confirmed_on after reported_on")
        country = report["country"]
        if country in last_day and reported < last_day[country]:
            raise RegistrationError(f"{rid}: reports for {country} are out of date order")
        before = seen.get(country, 0)
        if report["cumulative_confirmed"] < before:
            raise RegistrationError(f"{rid}: cumulative count for {country} falls")
        if report["new_confirmed"] != report["cumulative_confirmed"] - before:
            raise RegistrationError(f"{rid}: new_confirmed does not match the running total")
        seen[country] = report["cumulative_confirmed"]
        last_day[country] = reported


def reports_through(registry: Mapping[str, Any], cutoff: str = SOURCE_CUTOFF) -> list[dict]:
    """Reports public on or before the cutoff, in registry order."""
    limit = _day(cutoff)
    return [r for r in registry["reports"] if _day(r["reported_on"]) <= limit]


def substrate_digest(registry: Mapping[str, Any], reference: Mapping[str, Any],
                     cutoff: str = SOURCE_CUTOFF) -> str:
    """sha256 of everything the prices read: reports to the cutoff, the country sets and
    the reference-class episodes. Later reports and coverage reviews do not change it, so
    the registry can grow into the resolution evidence while the substrate stays fixed."""
    payload = {
        "reports": reports_through(registry, cutoff),
        "baseline_affected_countries": registry["baseline_affected_countries"]["countries"],
        "drc_land_neighbours": registry["drc_land_neighbours"],
        "reference_episodes": [
            {k: e[k] for k in ("episode_id", "outcome_local_transmission")}
            for e in reference["episodes"]
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- Predicates: one definition for pricing and for resolution --------------------

def qualifying_reports(rule: Mapping[str, Any], reports: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Reports that would satisfy a pin's rule, ignoring dates.

    ``first_report_in_country``: a country's first report of a confirmed case, for a
    country outside ``exclude_countries`` and, when given, inside ``countries_in``.
    ``new_case_report``: any report raising the count of a country in ``countries_in``.
    Planned medical evacuations are not reports in the registry, so never qualify.
    """
    kind = rule["rule"]
    exclude = set(rule.get("exclude_countries") or ())
    within = set(rule["countries_in"]) if rule.get("countries_in") else None
    out = []
    first_seen: set[str] = set()
    for report in reports:
        country = report["country"]
        is_first = country not in first_seen
        first_seen.add(country)
        if country in exclude or (within is not None and country not in within):
            continue
        if report["new_confirmed"] < 1:
            continue
        if kind == "first_report_in_country" and is_first:
            out.append(dict(report))
        elif kind == "new_case_report":
            out.append(dict(report))
        elif kind not in ("first_report_in_country", "new_case_report"):
            raise ValueError(f"unknown international rule {kind!r}")
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


def reference_probability(reference: Mapping[str, Any]) -> dict:
    episodes = reference["episodes"]
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


def _questions(registry: Mapping[str, Any]) -> list[dict]:
    names = registry["country_names"]
    baseline = list(registry["baseline_affected_countries"]["countries"])
    neighbours = [c for c in registry["drc_land_neighbours"] if c not in baseline]
    window = f"between {WINDOW_OPENS} and {RESOLVES_AT[:10]}"
    source = ("first reported by WHO, ECDC, US CDC, Africa CDC or that country's ministry of "
              "health")
    affected = _countries(baseline, names)
    return [
        {"key": "new-country", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline},
         "split": None,
         "question": (f"Will a country outside the affected set ({affected}) report its first "
                      f"laboratory-confirmed case of this outbreak, {source}, {window}? A planned "
                      "medical evacuation of an already-confirmed patient does not count.")},
        {"key": "new-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline,
                  "countries_in": neighbours},
         "split": "neighbour",
         "question": (f"Will a DRC land neighbour not yet affected ({_countries(neighbours, names)}) "
                      f"report its first laboratory-confirmed case of this outbreak, {source}, "
                      f"{window}?")},
        {"key": "new-non-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country",
                  "exclude_countries": sorted(set(baseline) | set(registry["drc_land_neighbours"]))},
         "split": "non_neighbour",
         "question": (f"Will a country that is neither a DRC land neighbour nor in the affected set "
                      f"({affected}) report its first laboratory-confirmed case of this outbreak, "
                      f"{source}, {window}? A planned medical evacuation does not count.")},
        {"key": "uganda-case", "forecast_type": "country_case_occurrence",
         "history": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "rule": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "split": None,
         "question": (f"Will Uganda report at least one new laboratory-confirmed case (imported or "
                      f"locally acquired), {source}, {window}? Uganda's count has stood at 20 since "
                      "2026-06-21.")},
    ]


def _event_days(rule: Mapping[str, Any], reports: Sequence[Mapping[str, Any]]) -> list[dt.date]:
    return [_day(r["reported_on"]) for r in qualifying_reports(rule, reports)]


def _hazard_pin(q: Mapping[str, Any], method: str, reports, registry) -> dict:
    spans = method_spans(method)
    days = _event_days(q["history"], reports)
    share_note = None
    if q["split"]:
        share_note = neighbour_share(qualifying_reports(q["history"], reports),
                                     set(registry["drc_land_neighbours"]), spans)
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
            "Will Kenya report at least one further laboratory-confirmed case, beyond the imported "
            "case confirmed on 2026-10-06, first reported by WHO, ECDC, US CDC, Africa CDC or "
            f"Kenya's Ministry of Health, between {WINDOW_OPENS} and {RESOLVES_AT[:10]}?"),
        "probability": round(ref["probability"], 4),
        "horizon_days": HORIZON_DAYS,
        "generator": {
            "reference_class": REFERENCE_RELATIVE,
            "episodes": ref["episodes"],
            "with_local_transmission": ref["with_local_transmission"],
            "estimator": "Jeffreys-smoothed proportion (yes + 0.5) / (episodes + 1)",
        },
        "resolution_rule": {"rule": "new_case_report", "countries_in": ["KEN"]},
    }


def _registration() -> dict:
    return {
        "source_cutoff": {"reports_through": SOURCE_CUTOFF},
        "inputs": [{"path": REGISTRY_RELATIVE, "scope": f"reports through {SOURCE_CUTOFF}"},
                   {"path": REFERENCE_RELATIVE, "scope": "reference-class episodes"}],
        "substrate_sha256": SUBSTRATE_SHA256,
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "evidence_grace_days": EVIDENCE_GRACE_DAYS,
        "public_registration": (
            "Public when the pinning commit first reaches the public repository. That UTC time "
            "and commit are appended afterwards as registration_receipt. If the commit is not "
            "public by registration_deadline_utc, the block is regenerated with a new deadline "
            "before anything is published. A recorded receipt later than the deadline marks the "
            "block void: reported, never scored."),
        "designer_exposure": [
            "Design session: had read SitRep 143 (data 2026-10-04) and every public report of "
            "Kenya's imported case (WHO Regional Office for Africa, 2026-10-06) before designing. "
            "Every event public at the cutoff is in the substrate; the questions concern only "
            f"reports first published from {WINDOW_OPENS}.",
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
            "the question. The three new-country questions share one hazard and are not "
            "independent; Uganda is a separate series."),
        "primary_endpoint": (
            "mean paired Brier difference, stationary minus recent, over resolved hazard "
            "questions, pooled with later international blocks priced by the same two methods"),
        "decision_rule": (
            "once at least 12 paired hazard questions have resolved across international blocks, "
            "make the stationary method the forecaster of record if the pooled mean difference is "
            "below zero AND it wins at least 8 of 12 (ceil(8 x k / 12) of k) non-tied questions; "
            "otherwise keep the recent method. A difference of exactly zero is not a win. Evaluated "
            "by lovs/forecast_scoring.paired_block_decision with question ids as units. With fewer "
            "than 12 resolved pairs the comparison is reported descriptively only. Pins keep their "
            "registered roles permanently."),
        "void_rule": (
            "a question is void for every method when a report that would satisfy it was first "
            f"published after the source cutoff ({SOURCE_CUTOFF}) and before {WINDOW_OPENS}, or when "
            f"an in-window report states a confirmation date before {WINDOW_OPENS}; a void question "
            "is reported, never scored. Identified by opsresolver from the registry."),
        "descriptive_statistics": [
            "Brier and log score per method for this block",
            "per-period hazard actually realised in the window against each method's estimate",
            "for the Kenya question, the reference class updated with this outcome",
        ],
        "unscoreable_policy": (
            "without a coverage review through the resolution date, published at least "
            f"{EVIDENCE_GRACE_DAYS} days after it, every pin is unscoreable_unreviewed; silence "
            "never resolves a pin NO"),
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
    reports = reports_through(registry)
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
