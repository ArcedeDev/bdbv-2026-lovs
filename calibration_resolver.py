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
    return (probability - outcome) ** 2


def resolve_point(point: dict, evidence_index: dict, as_of: dt.date) -> dict:
    """Compute the resolution status (and Brier, if resolved) for one point.

    Window logic is owned here, not trusted from the feed: a point resolves YES
    only when the evidence's confirmation date falls inside [pinned_at, resolves_at].
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

    if confirmed and date_in_window:
        outcome = 1
        result["status"] = STATUS_RESOLVED_YES
        result["outcome"] = outcome
        result["brier"] = round(brier(mid, outcome), 6)
        result["brier_lo"] = round(brier(lo, outcome), 6)
        result["brier_hi"] = round(brier(hi, outcome), 6)
        return result

    if as_of >= resolves:
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


def build_report(
    ledger: dict,
    evidence_index: dict,
    as_of: dt.date,
    evidence_doc: dict | None = None,
) -> dict:
    points = [resolve_point(p, evidence_index, as_of) for p in active_points(ledger)]

    by_status: dict[str, int] = {
        STATUS_RESOLVED_YES: 0,
        STATUS_RESOLVED_NO: 0,
        STATUS_PENDING: 0,
        STATUS_UNSCOREABLE: 0,
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
        },
        "reliability_note": (
            "Pinned probabilities cluster narrowly (midpoints ~0.36-0.39), so with the "
            "current resolved count reliability is effectively a single bin: predicted ~0.37 "
            "vs observed frequency among resolved points. Brier is reported within this "
            "outbreak only and alongside this note (Hoessly 2025); it is not a standalone "
            "calibration measure."
        ),
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
