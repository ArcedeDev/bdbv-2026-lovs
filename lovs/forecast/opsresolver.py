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
EVENTS_PATH = REPO / "data" / "international-events.json"

STATUS_YES = "resolved_yes"
STATUS_NO = "resolved_no"
STATUS_PENDING = "pending"
STATUS_STALE = "unscoreable_stale_series"
STATUS_NO_DATA = "unscoreable_no_series"
STATUS_UNREVIEWED = "unscoreable_unreviewed"
STATUS_UNREGISTERED = "unscoreable_unregistered"
STATUS_VOID = "void"


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


def _resolve_event(pin: dict, block: dict, events: dict | None, as_of: dt.date,
                   opens: dt.date, resolves: dt.date, result: dict) -> dict:
    """An international event pin, read from the reviewed registry.

    The predicate is the generator's own (pins_block11.qualifying_reports), so a pin is
    resolved by the definition it was priced on. A report first published by the
    resolution date counts; so does one published within the evidence grace when it
    states an in-window confirmation date. Silence resolves nothing: without a coverage
    review through the end of the grace, made after it and naming the sources checked,
    the pin is unscoreable. Retractions and later-stated facts apply only when published by
    the end of the grace, so nothing published after the grace can change a resolution. A matching report outside the frozen substrate but
    published before the window opened, or stating a confirmation before it, voids the
    question, as does a registration receipt later than the deadline; without a receipt
    nothing is scored.
    """
    from lovs.forecast import pins_block11 as p11

    registration = block.get("registration") or {}
    grace_end = resolves + dt.timedelta(days=int(registration.get("evidence_grace_days", 0)))
    if as_of <= grace_end:
        result["status"] = STATUS_PENDING
        result["reason"] = f"window and evidence grace open until {grace_end}"
        return result
    receipt = registration.get("registration_receipt")
    if not receipt:
        result["status"] = STATUS_UNREGISTERED
        result["reason"] = "no registration receipt; an unregistered pin is never scored"
        return result
    deadline = dt.datetime.fromisoformat(registration["registration_deadline_utc"].replace("Z", "+00:00"))
    public = dt.datetime.fromisoformat(receipt["public_at_utc"].replace("Z", "+00:00"))
    if public > deadline:
        result["status"] = STATUS_VOID
        result["reason"] = f"registered at {receipt['public_at_utc']}, after the deadline; reported, never scored"
        return result
    if events is None:
        result["status"] = STATUS_NO_DATA
        result["reason"] = "no international event registry supplied"
        return result
    p11.validate_registry(events)
    # A review covers whole days before the day it was made, so it must be made after the
    # grace closes and reach its last day.
    reviewed = [r for r in events.get("coverage_reviews", [])
                if _date(r["reviewed_through"]) >= grace_end and _date(r["reviewed_at"]) > grace_end
                and r.get("sources_checked")]
    if not reviewed:
        result["status"] = STATUS_UNREVIEWED
        result["reason"] = (f"no coverage review through {grace_end}, made after it and naming its "
                            "sources; silence is not evidence nothing happened. Not scored.")
        return result
    substrate = set(registration.get("substrate_report_ids") or ())
    # Facts and retractions count only when published by the end of the grace, so a
    # resolution is final: nothing published later can flip it.
    reports = p11.effective_reports(events, by=grace_end)
    hits, void = [], []
    for report in p11.qualifying_reports(pin["resolution_rule"], reports):
        reported = _date(report["reported_on"])
        confirmed = _date(report["confirmed_on"]) if report.get("confirmed_on") else None
        if reported > grace_end:
            continue  # published after the evidence grace: neither counts nor voids
        if (report["report_id"] not in substrate and reported < opens) or (
                reported >= opens and confirmed and confirmed < opens):
            void.append(report["report_id"])
        elif opens <= reported <= resolves or (
                resolves < reported <= grace_end and confirmed and opens <= confirmed <= resolves):
            hits.append(report["report_id"])
    if void:
        result["status"] = STATUS_VOID
        result["reason"] = f"decided before the window opened by {', '.join(void)}; reported, never scored"
        return result
    outcome = int(bool(hits))
    result["status"] = STATUS_YES if outcome else STATUS_NO
    result["outcome"] = outcome
    result["observed"] = (f"qualifying reports {', '.join(hits)}" if hits else
                          f"no qualifying report; coverage reviewed through {max(r['reviewed_through'] for r in reviewed)}")
    result["brier"] = round((float(pin["probability"]) - outcome) ** 2, 6)
    return result


def resolve_pin(pin: dict, block: dict, rows: Sequence[dict], as_of: dt.date,
                events: dict | None = None) -> dict:
    """Status and Brier for one operational pin.

    `resolves_at` closes at 23:59:59Z, so the resolution day itself is inside the
    window: a pin stays pending through that day and resolves only from a later
    `as_of`.
    """
    pinned, resolves = _date(block["pinned_at"]), _date(block["resolves_at"])
    # A block may open its window after the pin date: questions about a path, an event
    # or the feed must not count days before the registration was public. Absent the
    # field, the window opens on the pin date, as Blocks 6 and 7 were written.
    opens = _date(block["window_opens"]) if block.get("window_opens") else pinned
    if opens < pinned or opens > resolves:
        raise ValueError(f"{block['block_id']}: window_opens {opens} outside {pinned}..{resolves}")
    p = float(pin["probability"])
    result = {
        "pin_id": pin["pin_id"], "block_id": block["block_id"],
        "metric": pin["metric"], "shape": pin["shape"],
        "threshold": pin["threshold"], "probability": p,
        "pinned_at": block["pinned_at"], "resolves_at": block["resolves_at"],
        "window_opens": opens.isoformat(),
        "bias_test": pin.get("bias_test", False),
        "role": pin.get("role", "record"),
    }

    if pin.get("shape") == "event":
        return _resolve_event(pin, block, events, as_of, opens, resolves, result)

    if pin.get("shape") == "derived":
        if as_of <= resolves:
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
        derived = _resolve_derived(pin, rows, opens, resolves)
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
    window = _window_values(obs, opens, resolves)

    if as_of <= resolves:
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


def build_report(ledger: dict, rows: Sequence[dict], as_of: dt.date,
                 events: dict | None = None) -> dict:
    pins = [
        resolve_pin(pin, block, rows, as_of, events)
        for block in ledger["blocks"] if block.get("status") == "active"
        for pin in block["points"]
    ]
    resolved = [p for p in pins if p["status"] in (STATUS_YES, STATUS_NO)]
    # The headline is the programme's own record. A challenger pin prices a question
    # beside the incumbent for a method comparison; counting it here would score each
    # paired question twice and mix two methods into one skill figure.
    scored = [p for p in resolved if p["role"] != "challenger"]
    counts: dict[str, int] = {}
    for p in pins:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    by_block: dict[str, dict] = {}
    for p in resolved:
        cell = by_block.setdefault(p["block_id"], {}).setdefault(p["role"], {"resolved": 0, "brier_sum": 0.0})
        cell["resolved"] += 1
        cell["brier_sum"] += p["brier"]
    for roles in by_block.values():
        for cell in roles.values():
            cell["mean_brier"] = round(cell.pop("brier_sum") / cell["resolved"], 6)
    summary = {
        "total_pins": len(pins),
        "by_status": counts,
        "resolved_count": len(scored),
        "challenger_resolved_count": len(resolved) - len(scored),
        "by_block": dict(sorted(by_block.items())),
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
            f"{len(scored)} resolved pins of record (challenger pins excluded) spanning "
            f"predicted {ps[0]:.3f} to {ps[-1]:.3f} across {len({p['metric'] for p in scored})} "
            "distinct metrics. Pins sharing a metric sit at different thresholds on one "
            "realised value, and national series move together, so pins are not independent "
            "samples; read the per-block figures beside the pooled one."
        )
    return {"as_of": as_of.isoformat(), "ledger_mutated": False,
            "pins": pins, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Read-only resolver for the operational calibration ledger.")
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--series", default=str(of.DEFAULT_SERIES))
    parser.add_argument("--events", default=str(EVENTS_PATH))
    args = parser.parse_args(argv)
    events = load_ledger(args.events) if Path(args.events).exists() else None
    report = build_report(load_ledger(), of.load_rows(args.series),
                          _date(args.as_of), events)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
