# SPDX-License-Identifier: Apache-2.0
"""Read-only resolver + scorer for the BDBV 2026 corridor calibration ledger.

Reads the immutable pre-commitment ledger (``data/calibration-ledger.json``) and
a provenanced resolution-evidence feed (``data/calibration-resolution-evidence.json``),
computes a per-point resolution status and Brier score, and emits a read-only
report (``data/calibration-resolution-report.json``).

It NEVER writes the ledger. The resolution-date append of outcomes into the
ledger is a separate, founder-gated step (a human review gate, like
``release_snapshot.py --commit``). The report carries an advisory
``proposed_ledger_outcomes`` block, but this tool does not write it.

Scoring convention (validated against the epidemiology literature)
-----------------------------------------------------------------
Each calibration point is a BINARY event forecast ("at least one new
lab-confirmed case appears in the target zone within the window"), so the proper
scoring rule is the Brier score, ``(p_hat - y)**2`` (Hoessly 2025, Global
Epidemiology). The model emits ``risk_adj_50`` as a ``[low, high]`` probability
interval; the point probability ``p_hat`` is the interval midpoint, and
``brier_lo`` / ``brier_hi`` are reported from the interval bounds so the score's
interval-sensitivity is visible. The Weighted Interval Score (Bracher 2020) is
deliberately NOT used: it scores count / quantile forecasts, not a binary event.
Per Hoessly, the Brier score is reported alongside an explicit reliability note
and is only compared within this outbreak.

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

from lovs import forecast_scoring

REPO_ROOT = pathlib.Path(__file__).parent.resolve()
DATA_DIR = REPO_ROOT / "data"
LEDGER_PATH = DATA_DIR / "calibration-ledger.json"
EVIDENCE_PATH = DATA_DIR / "calibration-resolution-evidence.json"
REPORT_PATH = DATA_DIR / "calibration-resolution-report.json"

REPORT_SCHEMA_VERSION = "calibration-resolution-report/v1"

STATUS_RESOLVED_YES = "resolved_yes"
STATUS_RESOLVED_NO = "resolved_no"
STATUS_PENDING = "pending"
STATUS_UNSCOREABLE = "unscoreable_no_feed"
# A NO must be evidence of absence, not absence of evidence. When the feed's
# coverage date predates a point's resolution date, its silence says nothing
# about that window, and resolving NO from it would penalise the model for a
# stale input. Such points are excluded, not scored.
STATUS_UNSCOREABLE_STALE = "unscoreable_stale_feed"


def _date(value: str) -> dt.date:
    """Parse a YYYY-MM-DD[...] string to a date (ignoring any time suffix)."""
    return dt.date.fromisoformat(value[:10])


def _atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write text to ``path`` atomically: temp file in same dir, then os.replace."""
    path = pathlib.Path(path)
    if path.resolve() == LEDGER_PATH.resolve():
        raise RuntimeError(
            "refusing to write the immutable calibration ledger; the resolver is read-only"
        )
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_ledger(path: pathlib.Path = LEDGER_PATH) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def load_evidence(path: pathlib.Path = EVIDENCE_PATH) -> tuple[dict, dict]:
    """Return (raw evidence doc, index keyed by target_zone)."""
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for entry in doc.get("evidence", []):
        index[entry["target_zone"]] = entry
    return doc, index


def active_points(ledger: dict) -> list[dict]:
    """Flatten the points of every active (unresolved) block, in ledger order.

    ``pinned_at`` and ``resolves_at`` live on the BLOCK in the ledger, so each
    point inherits them (a point may override, as the carried-forward snapshot
    ``mode_b_hypotheses`` does). The loaded ledger dict is never mutated.
    """
    points: list[dict] = []
    for block in ledger.get("blocks", []):
        if block.get("status") != "active":
            continue
        for point in block.get("points", []):
            merged = dict(point)
            merged.setdefault("pinned_at", block.get("pinned_at"))
            merged.setdefault("resolves_at", block.get("resolves_at"))
            merged["block_id"] = block.get("block_id")
            points.append(merged)
    return points


def midpoint(interval: list) -> float:
    lo, hi = interval
    return (lo + hi) / 2.0


def brier(probability: float, outcome: int) -> float:
    return forecast_scoring.brier_score(probability, outcome)


def resolve_point(point: dict, evidence_index: dict, as_of: dt.date,
                  evidence_as_of: dt.date | None = None) -> dict:
    """Compute the resolution status (and Brier, if resolved) for one point.

    Window logic is owned here, not trusted from the feed: a point resolves YES
    only when the evidence's confirmation date falls inside [pinned_at, resolves_at].

    `evidence_as_of` is the feed's coverage date. A point can only resolve NO
    when the feed actually covers its window; otherwise the absence of a
    recorded confirmation is uninformative and the point is excluded as
    `unscoreable_stale_feed`. A YES is unaffected: an affirmative in-window
    confirmation stands regardless of how far the feed has since advanced.
    Omitting `evidence_as_of` preserves the pre-guard behaviour.
    """
    lo, hi = point["risk_adj_50"]
    mid = midpoint(point["risk_adj_50"])
    pinned = _date(point["pinned_at"])
    resolves = _date(point["resolves_at"])
    target = point["target"]

    result = {
        "hypothesis_id": point["hypothesis_id"],
        "block_id": point.get("block_id"),
        "corridor": point["corridor"],
        "target": target,
        "pinned_at": point["pinned_at"],
        "resolves_at": point["resolves_at"],
        "risk_adj_50": [lo, hi],
        "p_point": round(mid, 6),
    }

    entry = evidence_index.get(target)
    if entry is None:
        result["status"] = STATUS_UNSCOREABLE
        result["reason"] = "no resolution-evidence entry for target zone"
        return result

    result["evidence"] = {
        "confirmed_in_window": bool(entry.get("confirmed_in_window")),
        "first_in_window_confirmation_date": entry.get("first_in_window_confirmation_date"),
        "source_id": entry.get("source_id"),
        "source_url": entry.get("source_url"),
        "classification": entry.get("classification"),
    }

    conf_date_str = entry.get("first_in_window_confirmation_date")
    confirmed_flag = bool(entry.get("confirmed_in_window"))
    if confirmed_flag and not conf_date_str:
        # Malformed entry: a YES flag with no date can neither be window-checked
        # nor scored. Never let it fall through to resolved_no (which would
        # penalize the model for a data-entry error). Exclude it explicitly.
        result["status"] = STATUS_UNSCOREABLE
        result["reason"] = (
            "evidence marks confirmed_in_window=true but has no "
            "first_in_window_confirmation_date (malformed); excluded from scoring"
        )
        return result

    confirmed = confirmed_flag and bool(conf_date_str)
    date_in_window = False
    if confirmed:
        conf_date = _date(conf_date_str)
        date_in_window = pinned <= conf_date <= resolves
        if not date_in_window:
            result["reason"] = (
                f"evidence confirmation {conf_date_str} is outside the point window "
                f"[{point['pinned_at'][:10]}, {point['resolves_at'][:10]}]; not counted"
            )
        elif conf_date > as_of:
            result["reason"] = (
                f"evidence confirmation {conf_date_str} is after resolver as_of "
                f"{as_of.isoformat()}; not counted yet"
            )
            date_in_window = False

    if confirmed and date_in_window:
        outcome = 1
        result["status"] = STATUS_RESOLVED_YES
        result["outcome"] = outcome
        result["brier"] = round(brier(mid, outcome), 6)
        result["brier_lo"] = round(brier(lo, outcome), 6)
        result["brier_hi"] = round(brier(hi, outcome), 6)
        return result

    if as_of >= resolves:
        if evidence_as_of is not None and evidence_as_of < resolves:
            result["status"] = STATUS_UNSCOREABLE_STALE
            result["reason"] = (
                f"resolution-evidence feed covers only through {evidence_as_of.isoformat()}, "
                f"before this point's resolution date {point['resolves_at'][:10]}; its silence "
                "is not evidence of absence. Refresh the feed for this target's window, then "
                "re-run. Not scored."
            )
            return result
        outcome = 0
        result["status"] = STATUS_RESOLVED_NO
        result["outcome"] = outcome
        result["brier"] = round(brier(mid, outcome), 6)
        result["brier_lo"] = round(brier(lo, outcome), 6)
        result["brier_hi"] = round(brier(hi, outcome), 6)
        return result

    result["status"] = STATUS_PENDING
    result.setdefault("reason", "window open; no in-window target confirmation yet")
    return result


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None



def _reliability_note(resolved: list) -> str:
    """Describe the resolved set's spread, counting EVENTS rather than pins.

    Two things were wrong with the frozen sentence this replaces. It asserted a
    midpoint range of ~0.36-0.39 that silently stopped being true once a block
    pinned outside it. And it counted pins, when the resolution evidence is keyed
    by target zone: every pin sharing a block and a target resolves from ONE
    real-world event, so pins are not independent observations. Block 4 pinned
    `aru -> yei-ssd` at 0.022 and `bunia -> yei-ssd` at 0.561; those are two
    probabilities attached to a single event, and counting them as two points
    would overstate both the sample size and the spread.
    """
    if not resolved:
        return "No resolved points yet; reliability is not defined."
    events: dict = {}
    for point in resolved:
        events.setdefault((point.get("block_id"), point["target"]), []).append(
            point["p_point"]
        )
    per_event = [sum(v) / len(v) for v in events.values()]
    lo, hi = min(per_event), max(per_event)
    note = (
        f"{len(resolved)} resolved pins covering {len(events)} distinct "
        f"target-events; pins sharing a block and target resolve from the same "
        f"event and are not independent. Mean pinned probability per event spans "
        f"{lo:.3f} to {hi:.3f}."
    )
    if hi - lo < 0.25:
        note += (
            " That is effectively a single bin, so reliability cannot be read from "
            "this set: it gives one predicted value against one observed frequency."
        )
    else:
        note += (
            " That spans enough of the range to begin reading a reliability curve "
            "across bins, though the event count remains small."
        )
    return (
        note
        + " Brier is reported within this outbreak only and alongside this note "
        "(Hoessly 2025); it is not a standalone calibration measure."
    )

def build_report(
    ledger: dict,
    evidence_index: dict,
    as_of: dt.date,
    evidence_doc: dict | None = None,
    evidence_as_of: dt.date | None = None,
) -> dict:
    if evidence_as_of is None and evidence_doc is not None:
        stamp = (evidence_doc.get("_meta") or {}).get("as_of")
        if stamp:
            evidence_as_of = _date(stamp)
    points = [resolve_point(p, evidence_index, as_of, evidence_as_of)
              for p in active_points(ledger)]

    by_status: dict[str, int] = {
        STATUS_RESOLVED_YES: 0,
        STATUS_RESOLVED_NO: 0,
        STATUS_PENDING: 0,
        STATUS_UNSCOREABLE: 0,
        STATUS_UNSCOREABLE_STALE: 0,
    }
    for p in points:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1

    resolved = [p for p in points if p["status"] in (STATUS_RESOLVED_YES, STATUS_RESOLVED_NO)]
    yes = [p for p in points if p["status"] == STATUS_RESOLVED_YES]

    summary = {
        "total_points": len(points),
        "by_status": by_status,
        "resolved_count": len(resolved),
        "mean_brier_resolved": _mean([p["brier"] for p in resolved]),
        "mean_brier_resolved_lo": _mean([p["brier_lo"] for p in resolved]),
        "mean_brier_resolved_hi": _mean([p["brier_hi"] for p in resolved]),
        "excluded_counts": {
            STATUS_PENDING: by_status[STATUS_PENDING],
            STATUS_UNSCOREABLE: by_status[STATUS_UNSCOREABLE],
            STATUS_UNSCOREABLE_STALE: by_status[STATUS_UNSCOREABLE_STALE],
        },
        "reliability_note": _reliability_note(resolved),
    }

    proposed = [
        {
            "hypothesis_id": p["hypothesis_id"],
            "corridor": p["corridor"],
            "outcome": p["outcome"],
            "status": p["status"],
            "evidence": p.get("evidence"),
        }
        for p in resolved
    ]

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_by": "calibration_resolver.py",
        "as_of": as_of.isoformat(),
        "outbreak_id": ledger.get("_meta", {}).get("outbreak_id"),
        "scope_id": ledger.get("_meta", {}).get("scope_id"),
        "ledger_path": "data/calibration-ledger.json",
        "evidence_path": "data/calibration-resolution-evidence.json",
        "ledger_mutated": False,
        "scoring": {
            "rule": "brier",
            "point_probability": "risk_adj_50 interval midpoint",
            "interval_bounds_reported": True,
            "wis_used": False,
            "reference": "binary-event Brier (Hoessly 2025); WIS (Bracher 2020) intentionally not used (count/quantile forecasts)",
        },
        "evidence_as_of": (evidence_doc or {}).get("_meta", {}).get("as_of"),
        "points": points,
        "summary": summary,
        "proposed_ledger_outcomes": {
            "advisory_not_written": True,
            "note": (
                "Founder-gated append at resolves_at; calibration_resolver.py never writes "
                "the immutable ledger. Early-YES locks are monotonic-safe but still appended "
                "by hand under the human review gate."
            ),
            "outcomes": proposed,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="Resolution-as-of date YYYY-MM-DD.")
    parser.add_argument("--ledger-path", default=str(LEDGER_PATH))
    parser.add_argument("--evidence-path", default=str(EVIDENCE_PATH))
    parser.add_argument("--report-path", default=str(REPORT_PATH))
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write the report to --report-path (atomic). Default: print to stdout only.",
    )
    args = parser.parse_args(argv)

    as_of = _date(args.as_of)
    ledger = load_ledger(pathlib.Path(args.ledger_path))
    evidence_doc, evidence_index = load_evidence(pathlib.Path(args.evidence_path))
    report = build_report(ledger, evidence_index, as_of, evidence_doc)

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write_report:
        _atomic_write_text(pathlib.Path(args.report_path), text)
        print(f"wrote {args.report_path}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
