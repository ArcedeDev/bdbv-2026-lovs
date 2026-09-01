"""Read-only resolver for the operational calibration ledger.

Mirrors the corridor resolver's discipline, with the two defects that resolver
shipped with already closed:

  - a pin resolves only from series data that COVERS its window. Absence of a
    later observation is not evidence the threshold was never crossed, so a
    series that stops short returns `unscoreable_stale_series`, never NO.
  - every pin is an independent observable. Pins sharing a metric sit at
    different thresholds or different shapes, so no two resolve from one
    boolean the way the corridor pins did.

It never writes the ledger. The resolution-date append is a separate,
founder-gated step, as with calibration_resolver.py.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Sequence

from lovs.forecast import opsforecast as of

REPO = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO / "data" / "operational-calibration-ledger.json"

STATUS_YES = "resolved_yes"
STATUS_NO = "resolved_no"
STATUS_PENDING = "pending"
STATUS_STALE = "unscoreable_stale_series"
STATUS_NO_DATA = "unscoreable_no_series"


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(str(value)[:10])


def load_ledger(path: Path | str = LEDGER_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _window_values(obs: Sequence[of.Observation], start: dt.date, end: dt.date):
    return [o for o in obs if start <= o.date <= end]


def resolve_pin(pin: dict, block: dict, rows: Sequence[dict], as_of: dt.date) -> dict:
    """Status and Brier for one operational pin."""
    pinned, resolves = _date(block["pinned_at"]), _date(block["resolves_at"])
    p = float(pin["probability"])
    result = {
        "pin_id": pin["pin_id"], "block_id": block["block_id"],
        "metric": pin["metric"], "shape": pin["shape"],
        "threshold": pin["threshold"], "probability": p,
        "pinned_at": block["pinned_at"], "resolves_at": block["resolves_at"],
        "bias_test": pin.get("bias_test", False),
    }

    obs = of.series(rows, pin["metric"])
    if not obs:
        result["status"] = STATUS_NO_DATA
        result["reason"] = f"no observations for metric {pin['metric']!r}"
        return result

    series_as_of = obs[-1].date
    result["series_as_of"] = series_as_of.isoformat()
    window = _window_values(obs, pinned, resolves)

    if as_of < resolves:
        result["status"] = STATUS_PENDING
        result["reason"] = (f"window open until {resolves}; "
                            f"{len(window)} in-window observations so far")
        return result

    if series_as_of < resolves:
        result["status"] = STATUS_STALE
        result["reason"] = (
            f"series covers only through {series_as_of}, before the resolution date "
            f"{resolves}. Its silence about the rest of the window is not evidence the "
            "threshold went uncrossed. Re-extract the operational series from the SitRep "
            "packets, then re-run. Not scored."
        )
        return result

    if not window:
        result["status"] = STATUS_NO_DATA
        result["reason"] = "series covers the window but carries no in-window observation"
        return result

    values = [o.value for o in window]
    thr = float(pin["threshold"])
    shape = pin["shape"]
    if shape == "ends_above":
        outcome = int(values[-1] >= thr)
    elif shape == "ends_below":
        outcome = int(values[-1] <= thr)
    elif shape == "ever_above":
        outcome = int(any(v >= thr for v in values))
    elif shape == "sustained7_below":
        outcome = int(sum(1 for v in values if v <= thr) >= 7)
    else:
        result["status"] = STATUS_NO_DATA
        result["reason"] = f"unknown shape {shape!r}; not scored on a guess"
        return result

    result["status"] = STATUS_YES if outcome else STATUS_NO
    result["outcome"] = outcome
    result["observed_final_value"] = values[-1]
    result["observed_final_date"] = window[-1].date.isoformat()
    result["in_window_observations"] = len(window)
    result["brier"] = round((p - outcome) ** 2, 6)
    return result


def build_report(ledger: dict, rows: Sequence[dict], as_of: dt.date) -> dict:
    pins = [
        resolve_pin(pin, block, rows, as_of)
        for block in ledger["blocks"] if block.get("status") == "active"
        for pin in block["points"]
    ]
    scored = [p for p in pins if p["status"] in (STATUS_YES, STATUS_NO)]
    counts: dict[str, int] = {}
    for p in pins:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    summary = {
        "total_pins": len(pins),
        "by_status": counts,
        "resolved_count": len(scored),
        "mean_brier": (round(sum(p["brier"] for p in scored) / len(scored), 6)
                       if scored else None),
    }
    if scored:
        ps = sorted(p["probability"] for p in scored)
        summary["probability_span"] = [ps[0], ps[-1]]
        base = sum(p["outcome"] for p in scored) / len(scored)
        summary["base_rate"] = round(base, 4)
        summary["base_rate_brier"] = round(
            sum((base - p["outcome"]) ** 2 for p in scored) / len(scored), 6)
        summary["skill_vs_base_rate"] = (
            round(1 - summary["mean_brier"] / summary["base_rate_brier"], 4)
            if summary["base_rate_brier"] else None)
        summary["reliability_note"] = (
            f"{len(scored)} resolved pins spanning predicted {ps[0]:.3f} to {ps[-1]:.3f} "
            f"across {len({p['metric'] for p in scored})} distinct metrics. Pins sharing a "
            "metric sit at different thresholds and are correlated but not identical; pins "
            "across metrics are independent."
        )
    return {"as_of": as_of.isoformat(), "ledger_mutated": False,
            "pins": pins, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Read-only resolver for the operational calibration ledger.")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--series", default=str(of.DEFAULT_SERIES))
    args = parser.parse_args(argv)
    report = build_report(load_ledger(), of.load_rows(args.series),
                          _date(args.as_of))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
