# SPDX-License-Identifier: Apache-2.0
"""Calibration Block 12 (international spread), the second block of the monthly series.

Block 11 began a monthly series of international questions priced by fixed methods, so
that rare outcomes accumulate into a comparable record. This block repeats its four
hazard questions with the same two methods, the same 7-day periods counted back from
the window opening, the same Jeffreys smoothing, the same destination split and the same
28-day window:

- the recent method: the per-period probability of an event over the last 13 weekly
  periods, the forecaster of record;
- the stationary method: the same estimate over every weekly period since the outbreak
  was declared, priced beside it as the comparison.

Only the facts change: the country sets are this block's own, read from the registry at
the cutoff, and the substrate is the registry's reports through the cutoff, frozen by
report id. The predicates, the registry checks and the periods are imported from
lovs/forecast/pins_block11, so both blocks price and resolve by one definition.

Block 11's onward-transmission question has no counterpart here. Kenya's and Nigeria's
situations each have their own registered forecast, and a nine-case cluster is not a
member of the importation reference class.

Nothing here is authored; a test regenerates every pin and fails on any difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lovs.forecast import pins_block11 as p11

REPO = p11.REPO
LEDGER = p11.LEDGER
REGISTRY = p11.REGISTRY
REGISTRY_RELATIVE = p11.REGISTRY_RELATIVE

OUTBREAK = p11.OUTBREAK
BLOCK12_ID = f"operational-block:{OUTBREAK}:2026-11-12:international"
OUTBREAK_DECLARED = p11.OUTBREAK_DECLARED
SOURCE_CUTOFF = "2026-11-10"
PINNED_AT = "2026-11-12"
REGISTRATION_DEADLINE_UTC = "2026-11-12T23:59:59Z"
WINDOW_OPENS = "2026-11-13"
RESOLVES_AT = "2026-12-10T23:59:59Z"
HORIZON_DAYS = p11.HORIZON_DAYS
PERIOD_DAYS = p11.PERIOD_DAYS
WINDOW_PERIODS = p11.WINDOW_PERIODS
RECENT_PERIODS = p11.RECENT_PERIODS
EVIDENCE_GRACE_DAYS = p11.EVIDENCE_GRACE_DAYS
PREVIOUS_WINDOW_END = p11.RESOLVES_AT[:10]

RECENT = p11.RECENT
STATIONARY = p11.STATIONARY
METHOD_TAG = p11.METHOD_TAG

# This block's country sets, read from data/international-events.json at the cutoff and
# frozen here, so a later block can define its own without moving these questions.
BASELINE_COUNTRIES = ("COD", "UGA", "FRA", "KEN", "NGA")
EVACUATION_ONLY = ("DEU", "NLD", "USA", "CHE", "GBR")
DRC_LAND_NEIGHBOURS = p11.DRC_LAND_NEIGHBOURS
COUNTRY_NAMES = {**p11.COUNTRY_NAMES, "CHE": "Switzerland", "GBR": "the United Kingdom", "NGA": "Nigeria"}
ANY_FIRST_DETECTION = p11.ANY_FIRST_DETECTION

# Every report in the registry published by the cutoff, frozen by id. A report appended
# later, whatever its date, is not substrate; one published before the window opens
# voids the questions it would decide.
SUBSTRATE_REPORT_IDS = (
    "uga-2026-05-15", "uga-2026-05-16", "uga-2026-05-23", "uga-2026-05-25", "uga-2026-06-02",
    "uga-2026-06-06", "uga-2026-06-21", "fra-2026-06-24", "ken-2026-10-06", "ken-2026-10-14",
    "ken-2026-10-16", "ken-2026-10-17", "ken-2026-10-18", "ken-2026-10-20", "nga-2026-10-20",
)
SUBSTRATE_SHA256 = "6a9b85aacadb5b364c8facb1720e4e347cedb933748d9bc373ec1e7f9d0bfdce"
# The registry's coverage review made 2026-11-11T20:02:55Z covers every whole day through
# 2026-11-10, the cutoff.
SUBSTRATE_REVIEWED_AT = "2026-11-11T20:02:55Z"


class RegistrationError(RuntimeError):
    """A registration fact is inconsistent; nothing may be generated."""


_day = p11._day


def substrate_reports(registry: Mapping[str, Any]) -> list[dict]:
    """The frozen substrate: the reports named in SUBSTRATE_REPORT_IDS, in registry order,
    with the attributions and retractions published by the cutoff applied."""
    effective = p11.effective_reports(registry, by=_day(SOURCE_CUTOFF))
    chosen = [r for r in effective if r["report_id"] in SUBSTRATE_REPORT_IDS]
    missing = set(SUBSTRATE_REPORT_IDS) - {r["report_id"] for r in chosen}
    if missing:
        raise RegistrationError(f"substrate reports missing from the registry: {sorted(missing)}")
    late = [r["report_id"] for r in chosen if _day(r["reported_on"]) > _day(SOURCE_CUTOFF)]
    if late:
        raise RegistrationError(f"substrate reports dated after the cutoff: {late}")
    return chosen


def check_country_sets(registry: Mapping[str, Any]) -> None:
    """The frozen sets are the registry's facts at the cutoff: every country with a recorded
    first report in the substrate, in order of first report, and every country that had
    received a medical evacuation by the cutoff. The United States is the one member with no
    transfer of its own: ECDC lists it for its evacuated citizens, as Block 11 records."""
    first: list[str] = ["COD"]
    for report in substrate_reports(registry):
        if report["country"] not in first:
            first.append(report["country"])
    if tuple(first) != BASELINE_COUNTRIES:
        raise RegistrationError(f"affected countries at the cutoff are {first}, not {BASELINE_COUNTRIES}")
    received = []
    for evacuation in registry.get("medical_evacuations", []):
        # A transfer recorded after the cutoff is not a fact of this registration; one with
        # no date is counted, so an undated record fails closed rather than being skipped.
        day = str(evacuation.get("on") or evacuation.get("tested_positive") or "")
        if day and day[:10] > SOURCE_CUTOFF:
            continue
        if evacuation["country"] not in received:
            received.append(evacuation["country"])
    if set(received) - set(EVACUATION_ONLY) or set(EVACUATION_ONLY) - set(received) - {"USA"}:
        raise RegistrationError(f"evacuation-only countries at the cutoff are {received}")
    if set(EVACUATION_ONLY) & set(BASELINE_COUNTRIES):
        raise RegistrationError("a country cannot be both affected and evacuation-only")


def substrate_digest(registry: Mapping[str, Any]) -> str:
    """sha256 of everything the prices read: the substrate reports and the country sets."""
    payload = {
        "reports": substrate_reports(registry),
        "baseline_affected_countries": list(BASELINE_COUNTRIES),
        "evacuation_only": list(EVACUATION_ONLY),
        "drc_land_neighbours": list(DRC_LAND_NEIGHBOURS),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def periods() -> list[tuple[dt.date, dt.date]]:
    return p11.periods(_day(WINDOW_OPENS), OUTBREAK_DECLARED)


def method_spans(method: str) -> list[tuple[dt.date, dt.date]]:
    spans = periods()
    return spans[:RECENT_PERIODS] if method == RECENT else spans


def check_timing() -> None:
    deadline = dt.datetime.fromisoformat(REGISTRATION_DEADLINE_UTC.replace("Z", "+00:00"))
    opens = _day(WINDOW_OPENS)
    if opens != deadline.date() + dt.timedelta(days=1):
        raise RegistrationError(f"window_opens {opens} is not the UTC day after the deadline")
    if _day(PINNED_AT) > deadline.date() or _day(SOURCE_CUTOFF) > _day(PINNED_AT):
        raise RegistrationError("cutoff, pin date and deadline are out of order")
    if (_day(RESOLVES_AT) - opens).days + 1 != HORIZON_DAYS:
        raise RegistrationError("the window is not HORIZON_DAYS long")
    if _day(SUBSTRATE_REVIEWED_AT) > _day(PINNED_AT):
        raise RegistrationError("the substrate review is after the pin date")


def _uganda(registry: Mapping[str, Any]) -> tuple[int, str]:
    reports = [r for r in substrate_reports(registry) if r["country"] == "UGA"]
    return reports[-1]["cumulative_confirmed"], reports[-1]["reported_on"]


def _questions(registry: Mapping[str, Any]) -> list[dict]:
    names = COUNTRY_NAMES
    baseline = list(BASELINE_COUNTRIES)
    neighbours = [c for c in DRC_LAND_NEIGHBOURS if c not in baseline]
    window = f"between {WINDOW_OPENS} and {RESOLVES_AT[:10]}"
    affected = p11._countries(baseline, names)
    evacuation_only = p11._countries(EVACUATION_ONLY, names)
    new_country = (f"A country that has only treated evacuated patients or appears on an affected-country "
                   f"list because of them ({evacuation_only}) can still report its first case.")
    uganda_total, uganda_since = _uganda(registry)
    return [
        {"key": "new-country", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline},
         "split": None,
         "question": (f"Will a country other than {affected} report its first laboratory-confirmed "
                      f"case of this outbreak, {p11._AUTHORITY}, {window}? {new_country} {p11._EVACUATION}")},
        {"key": "new-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country", "exclude_countries": baseline,
                  "countries_in": neighbours},
         "split": "neighbour",
         "question": (f"Will a DRC land neighbour not yet affected ({p11._countries(neighbours, names)}) "
                      f"report its first laboratory-confirmed case of this outbreak, {p11._AUTHORITY}, "
                      f"{window}? {p11._EVACUATION}")},
        {"key": "new-non-neighbour", "forecast_type": "international_first_detection",
         "history": ANY_FIRST_DETECTION,
         "rule": {"rule": "first_report_in_country",
                  "exclude_countries": sorted(set(baseline) | set(DRC_LAND_NEIGHBOURS))},
         "split": "non_neighbour",
         "question": (f"Will a country that is neither a DRC land neighbour nor one of {affected} "
                      f"report its first laboratory-confirmed case of this outbreak, {p11._AUTHORITY}, "
                      f"{window}? {new_country} {p11._EVACUATION}")},
        {"key": "uganda-case", "forecast_type": "country_case_occurrence",
         "history": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "rule": {"rule": "new_case_report", "countries_in": ["UGA"]},
         "split": None,
         "question": (f"Will Uganda report at least one new laboratory-confirmed case (imported or "
                      f"locally acquired), {p11._AUTHORITY}, {window}? Uganda's count has stood at "
                      f"{uganda_total} since {uganda_since}. {p11._EVACUATION}")},
    ]


def _hazard_pin(q: Mapping[str, Any], method: str, reports: Sequence[Mapping[str, Any]]) -> dict:
    spans = method_spans(method)
    days = [_day(r["reported_on"]) for r in p11.qualifying_reports(q["history"], reports)]
    share_note = None
    if q["split"]:
        share_note = p11.neighbour_share(p11.qualifying_reports(q["history"], reports),
                                         set(DRC_LAND_NEIGHBOURS), spans)
        fraction = share_note["share"] if q["split"] == "neighbour" else 1 - share_note["share"]
        result = p11.hazard(days, spans, fraction)
    else:
        result = p11.hazard(days, spans)
    first, last = spans[-1][0], spans[0][1]
    pin = {
        "pin_id": f"operational-pin:{OUTBREAK}:{PINNED_AT}:28d:intl-{q['key']}-{METHOD_TAG[method]}",
        "question_id": f"block12:{q['key']}",
        "method": method,
        "role": "incumbent" if method == RECENT else "challenger",
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


def _asymmetry(points: Sequence[Mapping[str, Any]]) -> str:
    """Which method prices higher on each question, read from the prices, never typed."""
    by_question: dict[str, dict[str, float]] = {}
    for pin in points:
        by_question.setdefault(pin["question_id"].split(":", 1)[1], {})[pin["method"]] = pin["probability"]
    higher = sorted(k for k, p in by_question.items() if p[RECENT] > p[STATIONARY])
    lower = sorted(k for k, p in by_question.items() if p[RECENT] < p[STATIONARY])
    parts = []
    if higher:
        parts.append(f"the recent method prices higher than the stationary method on {', '.join(higher)}")
    if lower:
        parts.append(f"lower on {', '.join(lower)}")
    return ("; ".join(parts) + ". A quiet window favours the lower price on each pair and a busy "
            "window the higher, so one block's pairs lean with the window. The decision rule pools "
            "across blocks for that reason.")


def _registration() -> dict:
    return {
        "source_cutoff": {"reports_through": SOURCE_CUTOFF, "substrate_reviewed_at": SUBSTRATE_REVIEWED_AT},
        "substrate_report_ids": list(SUBSTRATE_REPORT_IDS),
        "inputs": [{"path": REGISTRY_RELATIVE, "scope": "the reports named in substrate_report_ids"}],
        "substrate_sha256": SUBSTRATE_SHA256,
        "registration_deadline_utc": REGISTRATION_DEADLINE_UTC,
        "window_opens": WINDOW_OPENS,
        "evidence_grace_days": EVIDENCE_GRACE_DAYS,
        "series_gap": (
            f"Block 11's window closed {PREVIOUS_WINDOW_END} and this block's opens {WINDOW_OPENS}, so "
            "the series asks nothing about reports first published from 2026-11-05 to 2026-11-12, "
            "beyond Block 11's evidence grace for confirmations dated in its own window. The coverage "
            f"review made {SUBSTRATE_REVIEWED_AT} found no qualifying report through {SOURCE_CUTOFF} "
            "beyond the substrate; one published on 2026-11-11 or 2026-11-12 voids the questions it "
            "would decide."),
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
            "Design session: had read every reviewed SitRep promotion through SitRep 178 (data "
            "2026-11-08), Block 11's resolution, and the registry with its coverage review made "
            f"{SUBSTRATE_REVIEWED_AT}, before generating. Every report public at that review is in "
            f"the substrate; the questions concern only reports first published from {WINDOW_OPENS}.",
            "The questions, both methods, the 13-period recent window, the 7-day periods, the "
            "smoothing, the destination split, the 28-day window and the decision rule are Block "
            "11's, fixed at its registration. This block sets only facts: the country sets and the "
            "substrate, read from the registry at the cutoff.",
            "Founder: asked on 2026-11-11 for the outstanding registrations to be completed; set no "
            "question, probability, window or method parameter.",
        ],
    }


def _pre_registration(points: Sequence[Mapping[str, Any]]) -> dict:
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
        # Block 11's rule, word for word: the pooled decision spans both blocks.
        "decision_rule": p11._pre_registration()["decision_rule"],
        "series_position": (
            "the second international block. With Block 11's 4 pairs, this block's 4 bring the "
            "series to 8 of the 12 the decision rule needs; the comparison stays descriptive until "
            "12 have resolved."),
        "known_asymmetry": _asymmetry(points),
        "void_rule": (
            "a question is void for every method when a report that would satisfy it is not in the "
            f"frozen substrate (substrate_report_ids) but was first published before {WINDOW_OPENS}, "
            "or when such a report published by the end of the evidence grace states a confirmation "
            f"date before {WINDOW_OPENS}. A void question is reported, never scored."),
        "descriptive_statistics": [
            "Brier and log score per method for this block",
            "per-period hazard realised in the window against each method's estimate",
            "the paired differences of Blocks 11 and 12 side by side",
        ],
        "unscoreable_policy": (
            f"without a coverage review covering every day through {EVIDENCE_GRACE_DAYS} days after "
            "the resolution date, made after the grace closes and naming the sources checked, every "
            "pin is unscoreable_unreviewed; silence never resolves a pin NO"),
    }


def build_block(registry: Mapping[str, Any] | None = None) -> dict:
    """Block 12, deterministically. Raises before generating anything doubtful."""
    check_timing()
    if p11.WINDOW_PERIODS != HORIZON_DAYS // PERIOD_DAYS:
        raise RegistrationError("the window is not a whole number of Block 11's periods")
    registry = registry if registry is not None else p11.load_registry()
    digest = substrate_digest(registry)
    if SUBSTRATE_SHA256 and digest != SUBSTRATE_SHA256:
        raise RegistrationError(f"substrate digest {digest} does not match {SUBSTRATE_SHA256}")
    check_country_sets(registry)
    reports = substrate_reports(registry)
    points = []
    for q in _questions(registry):
        points.extend(_hazard_pin(q, m, reports) for m in (RECENT, STATIONARY))
    return {
        "block_id": BLOCK12_ID,
        "label": "Block 12",
        "pinned_at": PINNED_AT,
        "window_opens": WINDOW_OPENS,
        "resolves_at": RESOLVES_AT,
        "horizon_days": HORIZON_DAYS,
        "status": "active",
        "rationale": (
            "Block 12, international spread: the second block of the monthly series Block 11 "
            "began, priced by the same fixed methods so the outcomes accumulate into one comparable "
            "record. It asks whether a new country, a new DRC land neighbour or a new non-neighbour "
            "reports its first case, and whether Uganda reports any new case. Each question is "
            "priced by the recent method (forecaster of record) and the stationary method "
            "(comparison). Nigeria, affected since 2026-10-20, joins the affected set; Switzerland "
            "and the United Kingdom, which received medical evacuations, join the evacuation-only "
            "set. There is no onward-transmission question: Kenya's and Nigeria's situations have "
            "their own registered forecasts."),
        "registration_note": (
            "Generated by lovs/forecast/pins_block12.build_block() from data/international-events.json "
            "(the reports named in substrate_report_ids), with the predicates and periods of "
            "lovs/forecast/pins_block11."),
        "registration": _registration(),
        "pre_registration": _pre_registration(points),
        "points": points,
    }


def append_to_ledger(path: Path = LEDGER) -> str:
    """Append Block 12. Refuses to touch a ledger that already holds it or any of its pins."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    block = build_block()
    if any(b["block_id"] == block["block_id"] for b in ledger["blocks"]):
        raise RegistrationError(f"ledger already holds {block['block_id']}; pinned blocks are never rewritten")
    ids = {p["pin_id"] for b in ledger["blocks"] for p in b["points"]}
    for pin in block["points"]:
        if pin["pin_id"] in ids:
            raise RegistrationError(f"pin id {pin['pin_id']} already in the ledger")
    ledger["blocks"].append(block)
    ledger["_meta"]["generators"][block["block_id"]] = "lovs/forecast/pins_block12.py"
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    return block["block_id"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--append", action="store_true", help="append the block to the ledger")
    parser.add_argument("--digest", action="store_true", help="print the substrate digest")
    args = parser.parse_args()
    if args.digest:
        print(substrate_digest(p11.load_registry()))
    elif args.append:
        print(append_to_ledger())
    else:
        print(json.dumps(build_block(), indent=1))
