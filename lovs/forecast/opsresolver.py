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


def _derived_series(name: str, rows: Sequence[dict]) -> list[of.Observation]:
    """Series a pin needs that are not columns in the extract.

    Block 7 pins publication lag and per-province isolation, both derived. The
    resolver has to reconstruct them the same way the generator built them, or
    the block is unresolvable and a 30-day window is wasted. The derivations
    live in one place, `pins_block7`, and are imported here rather than
    duplicated, so they cannot drift apart.
    """
    from lovs.forecast import pins_block7 as p7

    if name == "publication_lag":
        return p7.publication_lag_series(rows)
    if name == "nordkivu_isolation":
        return p7.province_isolation_series(rows, "Nord-Kivu")
    return []


def _sitrep_days(rows: Sequence[dict]) -> list[dt.date]:
    days = set()
    for row in rows:
        stamp = row.get("data_as_of")
        if stamp:
            days.add(_date(stamp))
    return sorted(days)


def _resolve_derived(pin: dict, rows: Sequence[dict], start: dt.date,
                     end: dt.date) -> tuple[int, str] | None:
    """Outcome for a pin whose event is not a threshold on a level.

    Returns (outcome, description), or None when the rule is unknown -- never a
    guess. A rule the resolver does not recognise must fail loudly rather than
    score as NO.
    """
    rule = (pin.get("resolution_rule") or {}).get("rule")
    if rule == "no_silence_longer_than":
        limit = int(pin["resolution_rule"]["days"])
        days = [d for d in _sitrep_days(rows) if start <= d <= end]
        if len(days) < 2:
            return None
        worst = max((b - a).days for a, b in zip(days, days[1:]))
        return int(worst <= limit), f"worst in-window silence {worst}d against limit {limit}d"
    if rule == "arrivals_at_least":
        need = int(pin["resolution_rule"]["count"])
        got = len([d for d in _sitrep_days(rows) if start <= d <= end])
        return int(got >= need), f"{got} arrivals against a benchmark of {need}"
    if rule == "any_day_at_or_below":
        spec = pin["resolution_rule"]
        obs = _window_values(of.series(rows, spec["metric"]), start, end)
        if not obs:
            return None
        value = float(spec["value"])
        hit = [o for o in obs if o.value <= value]
        return int(bool(hit)), (
            f"{len(hit)} of {len(obs)} in-window days at or below {value:g}")
    return None


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

    if pin.get("shape") == "derived":
        if as_of < resolves:
            result["status"] = STATUS_PENDING
            result["reason"] = f"window open until {resolves}"
            return result
        days = _sitrep_days(rows)
        if not days or days[-1] < resolves:
            result["status"] = STATUS_STALE
            result["reason"] = (
                f"series covers only through {days[-1] if days else 'nothing'}, before the "
                f"resolution date {resolves}; not scored")
            return result
        derived = _resolve_derived(pin, rows, pinned, resolves)
        if derived is None:
            result["status"] = STATUS_NO_DATA
            result["reason"] = (
                f"no resolution rule for {pin['pin_id']}; not scored on a guess")
            return result
        outcome, note = derived
        result["status"] = STATUS_YES if outcome else STATUS_NO
        result["outcome"] = outcome
        result["observed"] = note
        result["brier"] = round((p - outcome) ** 2, 6)
        return result

    rule = pin.get("resolution_rule") or {}
    if rule.get("rule") == "derived_series":
        obs = _derived_series(rule["builder"], rows)
    else:
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
