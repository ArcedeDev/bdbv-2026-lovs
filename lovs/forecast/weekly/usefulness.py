# SPDX-License-Identifier: Apache-2.0
"""The data-usefulness report (DUR, DESIGN.md 1), measured by script at every build.

Each measure has a value, a green, amber or red rating by the design's table, and the
table's action for red. A measure with nothing to measure yet (no settled week) is "none".
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Iterable, Mapping, Sequence

from lovs.forecast.weekly import spec

SEALED_LAG1 = 0.60          # the v2 size rule's lag-1 bound (DESIGN.md 7)
V2_WEEKS = 32


def _rag(value, green, amber=None) -> str:
    if value is None:
        return "none"
    if green(value):
        return "green"
    if amber is not None and amber(value):
        return "amber"
    return "red"


def report(*, build, origins: Sequence[Mapping], results: Sequence[Mapping], previous_flags: Iterable[str],
           quantile_scores: Sequence[Mapping], first_scored: Mapping[str, str], statuses_ok: bool) -> dict:
    """`origins`: the last 8 weeks' outcomes ({"registration_day", "phase", "cause", "on_time"}).
    `first_scored`: pin id -> registration day of the build that first scored it."""
    out: dict[str, dict] = {}
    on_time = sum(1 for o in origins if o.get("phase") == "pushed" and o.get("on_time"))
    causes: dict[str, int] = {}
    for o in origins:
        if o.get("cause"):
            causes[o["cause"]] = causes.get(o["cause"], 0) + 1
    running = 0
    for o in sorted(origins, key=lambda o: o["registration_day"], reverse=True):
        if o.get("phase") != "skipped":
            break
        running += 1
    weeks = len(origins)
    out["origins_on_time"] = {
        "value": on_time, "of": weeks, "skip_causes": causes,
        "rag": "none" if weeks < 8 else ("red" if on_time <= 5 or running >= 2 else
                                         "green" if on_time == 8 else "amber"),
        "action": "handoff" if running >= 2 else None}
    active = len(build.forecasts["active_series"])
    out["active_series"] = {"value": active, "rag": _rag(active, lambda v: v >= 12, lambda v: v >= 8),
                            "action": "report" if active < 8 else None}
    expected = active * len(spec.HORIZONS) * 5
    complete = len(build.forecasts["forecasts"])
    dropped = [m["series"] for m in build.forecasts["series"] if m["reason"] == "cells_incomplete"]
    out["cells_complete"] = {"value": 1.0 if expected and complete == expected else 0.0,
                             "series_dropped": dropped, "rag": "green" if not dropped else "red",
                             "action": "drop that series for the week" if dropped else None}
    record_prices = []
    for p in build.pins:
        s = spec.SERIES_BY_ID[p["series"]]
        record_prices.append(p[spec.price_column(spec.INITIAL_ROLES[s.cls]["record"])])
    share = (sum(1 for x in record_prices if 0.02 < x < 0.98) / len(record_prices)) if record_prices else None
    out["informative_share"] = {"value": None if share is None else round(share, 4), "record_pins": len(record_prices),
                                "rag": _rag(share, lambda v: v >= 0.70, lambda v: v >= 0.50),
                                "action": "propose steps" if share is not None and share < 0.5 else None}
    void = sum(1 for r in results if r["status"] == "void_decided_before")
    out["void"] = {"value": void, "rag": "green" if void == 0 else "red",
                   "action": "name the cause" if void else None}
    past = [r for r in results if r["value_kind"] == "settled" or r["status"] == "unscoreable_span"]
    uns = sum(1 for r in past if r["status"] == "unscoreable_span")
    ushare = (uns / len(past)) if past else None
    out["unscoreable"] = {"value": None if ushare is None else round(ushare, 4), "of": len(past),
                          "rag": _rag(ushare, lambda v: v <= 0.05, lambda v: v <= 0.10),
                          "action": "escalate" if ushare is not None and ushare > 0.10 else None}
    new_flags = sorted({f for r in build.rows for f in r.get("flags") or []} - set(previous_flags))
    out["breaks"] = {"value": len(new_flags), "codes": new_flags,
                     "rag": "green" if not new_flags else ("amber" if len(new_flags) == 1 else "red"),
                     "action": "hold, escalate" if len(new_flags) >= 2 else None}
    pairs = {}
    for r in results:
        if r["status"] in ("resolved_yes", "resolved_no"):
            pairs.setdefault(r["pin_id"], {})[r["value_kind"]] = r
    revised = [p for p in pairs.values() if "settled" in p and "first_published" in p]
    rev = (sum(1 for p in revised if p["settled"]["anomaly"] and not p["first_published"]["anomaly"])
           / len(revised)) if revised else None
    out["revisions"] = {"value": None if rev is None else round(rev, 4), "of": len(revised),
                        "rag": _rag(rev, lambda v: v <= 0.05, lambda v: v <= 0.15),
                        "action": "review the settle lag" if rev is not None and rev > 0.15 else None}
    lat = []
    for r in results:
        day = first_scored.get(r["pin_id"])
        if day and r["value_kind"] == "first_published" and r["status"] in ("resolved_yes", "resolved_no"):
            lat.append((dt.date.fromisoformat(day) - dt.date.fromisoformat(r["target_end"])).days)
    med = statistics.median(lat) if lat else None
    out["latency_days"] = {"value": med, "rag": _rag(med, lambda v: v <= 10, lambda v: v <= 17),
                           "action": "alarm" if med is not None and med > 17 else None}
    fshare = (build.floor["floored"] / build.floor["steps"]) if build.floor["steps"] else None
    out["floor_share"] = {"value": None if fshare is None else round(fshare, 4),
                          "rag": _rag(fshare, lambda v: v <= 0.02, lambda v: v <= 0.10),
                          "action": "report" if fshare is not None and fshare > 0.10 else None}
    settled_weeks = len({r["registration_day"] for r in results if r["value_kind"] == "settled"
                         and r["series"] in spec.NATIONAL and int(r["horizon_days"]) == 14})
    ess = settled_weeks * (1 - SEALED_LAG1) / (1 + SEALED_LAG1)
    lost = sum(causes.values())
    rate = lost / weeks if weeks else 0.0
    projected = V2_WEEKS / (1 - rate) if rate < 1 else None
    out["effective_sample"] = {"settled_weeks": settled_weeks, "ess": round(ess, 3),
                               "lag1_assumed": SEALED_LAG1,
                               "value": None if projected is None else round(projected, 1),
                               "rag": _rag(projected, lambda v: v <= 32, lambda v: v <= 52),
                               "action": "escalate" if projected is None or projected > 52 else None}
    out["statuses"] = {"value": 1.0 if statuses_ok else 0.0, "rag": "green" if statuses_ok else "red",
                       "action": "fail loud, no wiki writes" if not statuses_ok else None}
    cov = {}
    for level in ("50", "90"):
        vals = [q["coverage"][level] for q in quantile_scores
                if q["method"] == spec.INITIAL_ROLES[spec.SERIES_BY_ID[q["series"]].cls]["record"]]
        cov[level] = round(sum(vals) / len(vals), 4) if vals else None
    in50 = cov["50"] is None or 0.40 <= cov["50"] <= 0.60
    in90 = cov["90"] is None or 0.80 <= cov["90"] <= 0.97
    out["coverage"] = {"value": cov, "rag": "none" if cov["50"] is None else ("green" if in50 and in90 else "amber"),
                       "action": None if in50 and in90 else "report"}
    reds = [k for k, v in out.items() if v["rag"] == "red"]
    return {"registration_day": build.R.isoformat(), "measures": out, "red": reds}
