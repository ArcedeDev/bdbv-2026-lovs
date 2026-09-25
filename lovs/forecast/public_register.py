"""Public accountability rows for calibration Blocks 5, 6 and 7, and the 2026-06-04 block.

The public calibration record (data/public_calibration_commitments.json) never
carries a model probability. Each row states the question, the registered tier or
threshold, which way the registered forecast leans, and how the pin resolves.

These rows are derived from the pinned ledgers, never written by hand:
tests/test_public_register.py re-derives them and fails on any difference, so a
reworded question or a changed threshold cannot reach the public record without
also changing the ledger it came from.

The three blocks were pinned on 2026-09-01 in a local commit and first published on
2026-09-17. Every row carries ``first_published_at`` and says so in its notes: the
pin date is when the forecast was made, and the publication date is the earliest
date anyone outside can verify it.

The 2026-06-04 corridor block (four pins to yei-ssd and kisangani-cod) was pinned on
2026-06-04, first published on 2026-06-12 and resolved in the corridor ledger, but it
entered this record only on 2026-09-26. Its rows (june_block_rows) take their outcome
from the ledger point, never from a typed value, and carry no registered side, as the
Blocks 1-3 corridor rows carry none.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from lovs.forecast_scoring import brier_score

REPO_ROOT = Path(__file__).resolve().parents[2]
CORRIDOR_LEDGER = REPO_ROOT / "data" / "calibration-ledger.json"
OPERATIONAL_LEDGER = REPO_ROOT / "data" / "operational-calibration-ledger.json"
RESOLUTION_EVIDENCE = REPO_ROOT / "data" / "calibration-resolution-evidence.json"

CORRIDOR_BLOCK_ID = "calibration-block:bdbv-uga-cod-2026:2026-09-01"
OPERATIONAL_BLOCK_ID = "operational-block:bdbv-uga-cod-2026:2026-09-01"
STRUCTURAL_BLOCK_ID = "operational-block:bdbv-uga-cod-2026:2026-09-01:structural"

FIRST_LEDGER_NUMBER = 57
FIRST_PUBLISHED_AT = "2026-09-17"
PUBLICATION_NOTE = (
    "Pinned 2026-09-01 in a local commit and first published 2026-09-17 "
    "(ArcedeDev/bdbv-2026-lovs 6d710d5). The verifiable public pre-registration "
    "date is 2026-09-17."
)
CORRIDOR_RESOLUTION_POLICY = (
    "Resolve from public MOH, WHO, Africa CDC, CDC, ECDC, INRB, or other cited public "
    "authority reporting whose evidence covers the window through the resolution date. "
    "Evidence that stops short of the resolution date leaves the pin open; silence "
    "never resolves a pin NO."
)
OPERATIONAL_RESOLUTION_POLICY = (
    "Resolve from INSP/INRB situation reports for the DRC outbreak, read by data day. "
    "A series that stops short of the resolution date leaves the pin open; a missing "
    "report never resolves a pin NO."
)

_AXIS_BY_BLOCK = {OPERATIONAL_BLOCK_ID: "operational", STRUCTURAL_BLOCK_ID: "structural"}
_PIN_PREFIX_BY_BLOCK = {OPERATIONAL_BLOCK_ID: "OP6", STRUCTURAL_BLOCK_ID: "ST7"}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _block(ledger: Mapping[str, Any], block_id: str) -> Mapping[str, Any]:
    for block in ledger["blocks"]:
        if block["block_id"] == block_id:
            return block
    raise ValueError(f"block {block_id} is not in the ledger")


def lean(probability: float) -> str:
    """Direction of a registered forecast, without the number."""
    if probability > 0.55:
        return "leans YES"
    if probability < 0.45:
        return "leans NO"
    return "is close to even"


def _forecast_type(pin: Mapping[str, Any]) -> str:
    shape = pin["shape"]
    if shape in ("ends_below", "ends_above"):
        return "operational_level_threshold"
    if shape == "ever_above":
        return "operational_excursion_threshold"
    if shape == "sustained7_below":
        return "operational_sustained_threshold"
    rule = (pin.get("resolution_rule") or {}).get("rule")
    if rule in ("arrivals_at_least", "no_silence_longer_than"):
        return "reporting_cadence_commitment"
    return "operational_event_occurrence"


def _tier(pin: Mapping[str, Any]) -> str:
    shape, threshold = pin["shape"], pin["threshold"]
    rule = pin.get("resolution_rule") or {}
    if shape == "ends_below":
        return f"threshold:<={threshold:g}"
    if shape == "ends_above":
        return f"threshold:>={threshold:g}"
    if shape == "ever_above":
        return f"threshold:any>={threshold:g}"
    if shape == "sustained7_below":
        return f"sustained7:<={threshold:g}"
    if rule.get("rule") == "any_day_at_or_below":
        return f"event:any_day<={rule['value']}"
    if rule.get("rule") == "arrivals_at_least":
        return f"threshold:>={rule['count']}_reports"
    if rule.get("rule") == "no_silence_longer_than":
        return f"cadence:no_gap_over_{rule['days']}d"
    raise ValueError(f"no public tier for {pin['pin_id']}")


def _baseline(pin: Mapping[str, Any]) -> str:
    value, day = pin.get("last_observed_value"), pin.get("last_observed_date")
    rule = (pin.get("resolution_rule") or {}).get("rule")
    if value is None or not day:
        return f"frozen extract, last data day {day or '2026-08-28'}"
    if rule in ("arrivals_at_least", "no_silence_longer_than"):
        return f"{value:g} reports arrived in the 30 days to {day}"
    return f"{value:g} on {day} (last observation in the frozen extract)"


# The ledger's selection notes quote earlier blocks' probabilities ("resolved NO in
# Block 1 at p=0.375"). The public record carries no probability, so they are cut.
_PROBABILITY_PHRASE = re.compile(r"\s*at p=\d*\.\d+(?:\s*and\s*\d*\.\d+)?")


def _public_note(text: str) -> str:
    return _PROBABILITY_PHRASE.sub("", text).strip()


def _public_pin_id(block_id: str, pin: Mapping[str, Any]) -> str:
    return f"{_PIN_PREFIX_BY_BLOCK[block_id]}-{pin['pin_id'].split(':')[-1]}"


def _ledger_points() -> list[tuple[str, str, Mapping[str, Any], Mapping[str, Any]]]:
    """``(ledger_id, block_id, block, point)`` for every Blocks 5-7 pin, in ledger-id order.

    The one place the public ledger ids are assigned: the six corridor points sorted by
    target, then the operational and the structural pins in ledger order.
    """
    corridor = _block(_load(CORRIDOR_LEDGER), CORRIDOR_BLOCK_ID)
    operational = _load(OPERATIONAL_LEDGER)
    ordered = [
        (CORRIDOR_BLOCK_ID, corridor, point)
        for point in sorted(corridor["points"], key=lambda p: p["target"])
    ]
    for block_id in (OPERATIONAL_BLOCK_ID, STRUCTURAL_BLOCK_ID):
        block = _block(operational, block_id)
        ordered.extend((block_id, block, pin) for pin in block["points"])
    return [
        (f"bdbv-2026-cal-{FIRST_LEDGER_NUMBER + offset:03d}", block_id, block, point)
        for offset, (block_id, block, point) in enumerate(ordered)
    ]


def _corridor_row(
    ledger_id: str, block: Mapping[str, Any], point: Mapping[str, Any], names: Mapping[str, Any]
) -> dict[str, Any]:
    target = point["target"]
    place = names.get(target) or target
    return {
        "control_role": point["control_role"],
        "first_published_at": FIRST_PUBLISHED_AT,
        "forecast_type": "corridor_watch_commitment",
        "geography_class": point["geography_class"],
        "horizon_days": block["horizon_days"],
        "ledger_id": ledger_id,
        "notes": f"{_public_note(point['selection_role'])} {PUBLICATION_NOTE}",
        "outbreak_id": "bdbv-uga-cod-2026",
        "public_question": (
            f"Within the 30-day window (pinned 2026-09-01, resolving 2026-10-01), does "
            f"{place} record a new laboratory-confirmed BDBV case confirmed on or after "
            f"2026-09-01, on the corridor from {point['source'].title()}? Imported and "
            f"travel-linked cases confirmed there count; confirmations dated before "
            f"2026-09-01 do not. This is a falsification test, not a forecast: the "
            f"corridor model's hazard for this target had saturated at effective "
            f"certainty when pinned, so the registered forecast leans YES and a single "
            f"NO falsifies it."
        ),
        "public_value_or_tier": point["risk_tier"],
        "registered_at": block["pinned_at"],
        "registration_baseline": "no confirmation in the target dated on or after 2026-09-01",
        "resolution_date": block["resolves_at"][:10],
        "resolution_source_policy": CORRIDOR_RESOLUTION_POLICY,
        "resolved_value": "",
        "score_after_resolution": "",
        "source_geography": point["source"],
        "status": "open",
        "target_geography": target,
    }


def _operational_row(
    ledger_id: str, block_id: str, block: Mapping[str, Any], pin: Mapping[str, Any]
) -> dict[str, Any]:
    province = "nord-kivu-cod" if pin["metric"] == "nordkivu_isolation" else "cod"
    if pin.get("bias_test"):
        role = "low_band_bias_test"
    elif block_id == STRUCTURAL_BLOCK_ID:
        role = "structural_method_comparison"
    else:
        role = "operational_capacity"
    return {
        "control_role": role,
        "first_published_at": FIRST_PUBLISHED_AT,
        "forecast_type": _forecast_type(pin),
        "geography_class": "in_country",
        "horizon_days": pin["horizon_days"],
        "ledger_id": ledger_id,
        "notes": (
            f"{'Low-band bias test: one of four pins registered to measure whether low forecasts come in more often than priced. ' if pin.get('bias_test') else ''}"
            f"{PUBLICATION_NOTE}"
        ),
        "outbreak_id": "bdbv-uga-cod-2026",
        "pin_id": _public_pin_id(block_id, pin),
        "public_question": f"{pin['public_question']} The registered forecast {lean(pin['probability'])}.",
        "public_value_or_tier": _tier(pin),
        "registered_at": block["pinned_at"],
        "registration_baseline": _baseline(pin),
        "resolution_date": block["resolves_at"][:10],
        "resolution_source_policy": OPERATIONAL_RESOLUTION_POLICY,
        "resolved_value": "",
        "score_after_resolution": "",
        "source_geography": "",
        "status": "open",
        "target_geography": province,
    }


def public_rows() -> list[dict[str, Any]]:
    """The 31 public rows for Blocks 5, 6 and 7, in ledger-id order."""
    names = {e["target_zone"]: e.get("target_name") for e in _load(RESOLUTION_EVIDENCE)["evidence"]}
    return [
        _corridor_row(ledger_id, block, point, names)
        if block_id == CORRIDOR_BLOCK_ID
        else _operational_row(ledger_id, block_id, block, point)
        for ledger_id, block_id, block, point in _ledger_points()
    ]


JUNE_BLOCK_ID = "calibration-block:bdbv-uga-cod-2026:2026-06-04"
JUNE_FIRST_LEDGER_NUMBER = 88
JUNE_FIRST_PUBLISHED_AT = "2026-06-12"
JUNE_PUBLICATION_NOTE = (
    "Pinned 2026-06-04 and first published 2026-06-12: commit 571ab58 of "
    "ArcedeDev/bdbv-2026-lovs carries this pin in its brief and corridor ledger, and the "
    "GitHub activity log records its first push at 2026-06-12T22:20:12Z, when branch "
    "bdbv-sitrep25-build was created at 770327d (the branch stands restored at 4e489e7). "
    "The verifiable public pre-registration date is 2026-06-12. The pin entered this "
    "record on 2026-09-26."
)
_OUTCOME_VALUE = {1: "yes", 0: "no"}


def _long_date(iso: str) -> str:
    day = date.fromisoformat(iso[:10])
    return f"{day.day} {day.strftime('%B')} {day.year}"


def _corridor_record(ledger: Mapping[str, Any], day: str, *, before_corrections: bool) -> tuple[int, int, float]:
    """``(YES, NO, mean Brier)`` over the corridor pins resolved by ``day``.

    With ``before_corrections`` each point counts the outcome it had before any
    correction dated ``day`` or later, as kept in its superseded_outcomes. Pins
    resolved after ``day`` are left out, so the figures stay fixed once written.
    Each Brier is rounded to six places before averaging, as calibration_resolver.py does.
    """
    outcomes: list[int] = []
    scores: list[float] = []
    for block in ledger["blocks"]:
        if block.get("status") != "active":
            continue
        for point in block["points"]:
            if "outcome" not in point or point["resolved_as_of"] > day:
                continue
            outcome = point["outcome"]
            if before_corrections:
                history = point.get("superseded_outcomes", [])
                outcome = next((e["outcome"] for e in history if e["superseded_at"] >= day), outcome)
            lo, hi = point["risk_adj_50"]
            outcomes.append(outcome)
            scores.append(round(brier_score((lo + hi) / 2.0, outcome), 6))
    return outcomes.count(1), outcomes.count(0), round(sum(scores) / len(scores), 6)


def _june_resolution_note(
    ledger: Mapping[str, Any], block: Mapping[str, Any], point: Mapping[str, Any], entry: Mapping[str, Any]
) -> str:
    target = point["target"]
    window = f"between {block['pinned_at']} and {block['resolves_at'][:10]}"
    evidence = point["outcome_evidence"]
    if point["outcome"] == 1:
        note = (
            f"A laboratory-confirmed BDBV case was confirmed in {target} on "
            f"{evidence['first_in_window_confirmation_date']}, {window}."
        )
    else:
        note = f"No laboratory-confirmed BDBV case was attributed to {target} {window}."
        if entry.get("first_zone_attributed_confirmation_date"):
            note += (
                f" Its first zone-attributed confirmation is dated "
                f"{entry['first_zone_attributed_confirmation_date']}, after the window."
            )
    for earlier in point.get("superseded_outcomes", []):
        day = earlier["superseded_at"]
        yes0, no0, brier0 = _corridor_record(ledger, day, before_corrections=True)
        yes1, no1, brier1 = _corridor_record(ledger, day, before_corrections=False)
        movement = "lowers" if brier1 < brier0 else "raises"
        direction = "in the model's favour" if brier1 < brier0 else "against the model"
        note += (
            f" Corrected on {day} from {_OUTCOME_VALUE[earlier['outcome']].upper()} to "
            f"{_OUTCOME_VALUE[point['outcome']].upper()} by a dated review "
            f"({earlier['superseded_by']}); the earlier outcome and its evidence "
            f"({earlier['outcome_evidence']['source_id']}) stay on the ledger point. The "
            f"correction moves the corridor record from {yes0} YES / {no0} NO to {yes1} YES / "
            f"{no1} NO and {movement} its mean Brier score, {direction}; the rule for the "
            f"target, not the score, decides the outcome. The amendment in "
            f"data/calibration-ledger.pinned-block-hashes.json gives the figures."
        )
    return note


def june_block_rows() -> list[dict[str, Any]]:
    """The four public rows for the 2026-06-04 corridor block, in ledger order.

    Every value comes from the corridor ledger's block and point, or from the evidence
    entry the point's outcome cites. No probability and no pin_id reach the row (the
    commitment carry-forward skips rows without a pin_id, as it skips Blocks 1-3).
    """
    ledger = _load(CORRIDOR_LEDGER)
    block = _block(ledger, JUNE_BLOCK_ID)
    feed = {(e["target_zone"], e["source_id"]): e for e in _load(RESOLUTION_EVIDENCE)["evidence"]}
    window_start, window_end = _long_date(block["pinned_at"]), _long_date(block["resolves_at"])
    rows = []
    for offset, point in enumerate(block["points"]):
        entry = feed[(point["target"], point["outcome_evidence"]["source_id"])]
        rows.append(
            {
                "control_role": point["control_role"],
                "first_published_at": JUNE_FIRST_PUBLISHED_AT,
                "forecast_type": "corridor_watch_commitment",
                "geography_class": point["geography_class"],
                "horizon_days": block["horizon_days"],
                "ledger_id": f"bdbv-2026-cal-{JUNE_FIRST_LEDGER_NUMBER + offset:03d}",
                "notes": f"{_public_note(point['selection_role']).rstrip('.')}. {JUNE_PUBLICATION_NOTE}",
                "outbreak_id": "bdbv-uga-cod-2026",
                "public_question": (
                    f"Does at least one new laboratory-confirmed BDBV case appear in "
                    f"{point['target']} between {window_start} and {window_end}, given "
                    f"continued reporting from {point['source']}? This restates the "
                    f"calibration point the brief published on {JUNE_FIRST_PUBLISHED_AT}, "
                    f"without its probability range."
                ),
                "public_value_or_tier": point["risk_tier"],
                "registered_at": block["pinned_at"],
                "resolution_date": block["resolves_at"][:10],
                "resolution_evidence_source_ids": [entry["source_id"], *entry.get("source_ids", [])],
                "resolution_note": _june_resolution_note(ledger, block, point, entry),
                "resolution_source_policy": CORRIDOR_RESOLUTION_POLICY,
                "resolved_value": _OUTCOME_VALUE[point["outcome"]],
                "score_after_resolution": "",
                "source_geography": point["source"],
                "status": "resolved",
                "target_geography": point["target"],
            }
        )
    return rows


def axis_by_pin() -> dict[str, str]:
    """Registered forecast axis for each Blocks 6 and 7 public pin id."""
    ledger = _load(OPERATIONAL_LEDGER)
    return {
        _public_pin_id(block_id, pin): _AXIS_BY_BLOCK[block_id]
        for block_id in (OPERATIONAL_BLOCK_ID, STRUCTURAL_BLOCK_ID)
        for pin in _block(ledger, block_id)["points"]
    }


def lean_by_ledger_id() -> dict[str, tuple[str, str, str]]:
    """``ledger_id -> (identity field, identity value, registered lean)`` for Blocks 5-7.

    The lean is the phrase the public question registered ("leans YES", "leans NO" or
    "is close to even"), from the same ``lean()`` call that wrote it, so a reader of the
    question and a reader of lovs/forecast/registered_side.py see one registration. No
    probability leaves this module. The identity is the public row field that names the
    pin at that ledger id (``pin_id`` for Blocks 6 and 7; ``target_geography`` for
    Block 5, whose rows carry no pin id), so a caller can refuse a row that does not
    match. A Block 5 corridor point leans on its risk_adj_50 interval midpoint, the
    point probability the resolver uses (calibration_resolver.midpoint); its public
    question states that lean in fixed words.
    """
    out: dict[str, tuple[str, str, str]] = {}
    for ledger_id, block_id, _block_doc, point in _ledger_points():
        if block_id == CORRIDOR_BLOCK_ID:
            lo, hi = point["risk_adj_50"]
            out[ledger_id] = ("target_geography", point["target"], lean((lo + hi) / 2.0))
        else:
            out[ledger_id] = ("pin_id", _public_pin_id(block_id, point), lean(point["probability"]))
    return out
