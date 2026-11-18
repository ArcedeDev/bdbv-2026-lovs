# SPDX-License-Identifier: Apache-2.0
"""Training tables (DESIGN.md 6): long, hub-compatible, built from gated tables only.

weekly_forecasts.csv (one row per quantile value), weekly_pins.csv (one price per method),
weekly_outcomes.csv and weekly_scores.csv (append-only events with supersedes), and
weekly_origin_features.csv from each week's own vintage. Pin tables publish from 12-09;
the rest stay private until D5. Trajectories regenerate from seeds on demand.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import statistics
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lovs.forecast.weekly import methods, spec, targets, vintage


def origin_features(rows: Sequence[Mapping], R: dt.date, cutoff: dt.date) -> list[dict]:
    """Per series, what the week's vintage alone says at the origin. Leakage-tested."""
    rows = [r for r in rows if vintage.usable(r, R, cutoff)]
    out = []
    for s in spec.SERIES:
        hist = targets.history(s, rows, R, cutoff)
        if len(hist) < 2:
            continue
        weekly = methods.weekly_values(s, hist, spec.PERSISTENCE_WEEKS)
        steps = [b.value - a.value for a, b in zip(hist, hist[1:]) if (b.date - a.date).days == 1]
        out.append({
            "registration_day": R.isoformat(), "series": s.id, "as_of": hist[-1].date.isoformat(),
            "last_value": hist[-1].value, "n_obs": len(hist),
            "weekly_last": weekly[-1] if weekly else None,
            "weekly_mean_8w": round(statistics.fmean(weekly), 6) if weekly else None,
            "daily_step_mean_28d": round(statistics.fmean(steps[-28:]), 6) if steps else None,
            "negative_steps_28d": sum(1 for x in steps[-28:] if x < 0),
            "basis_flags": sorted({f for r in rows if r["data_as_of"] > (cutoff - dt.timedelta(days=28)).isoformat()
                                   for f in r.get("flags") or []}),
        })
    return out


def _csv(rows: Iterable[Mapping], columns: Sequence[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r[k]) for k in columns})
    return buf.getvalue().encode("ascii")


FORECAST_COLUMNS = ("forecast_id", "registration_day", "cutoff", "lovs_commit", "mcp_commit", "spec_version",
                    "series", "horizon_days", "lead_days", "target_start", "target_end", "method",
                    "generator_commit", "seed", "trajectory_sha256", "quantile", "value")


def forecast_rows(doc: Mapping, manifest: Mapping) -> list[dict]:
    out = []
    for f in doc["forecasts"]:
        for level, value in f["quantiles"]:
            out.append({"forecast_id": f["forecast_id"], "registration_day": doc["registration_day"],
                        "cutoff": doc["cutoff"], "lovs_commit": manifest["fetched"]["lovs"]["commit"],
                        "mcp_commit": manifest["fetched"]["mcp"]["commit"], "spec_version": doc["spec_version"],
                        "series": f["series"], "horizon_days": f["horizon_days"], "lead_days": f["lead_days"],
                        "target_start": f["target_start"], "target_end": f["target_end"], "method": f["method"],
                        "generator_commit": doc["generator"]["commit"], "seed": f["seed"],
                        "trajectory_sha256": f["trajectory_sha256"], "quantile": level, "value": value})
    return out


def outcome_events(snapshots: Sequence[tuple[str, Sequence[Mapping]]]) -> list[dict]:
    """Append-only events from successive results snapshots: a row whose status or value
    changed since the previous snapshot is a new event that supersedes the old one."""
    events, last = [], {}
    for build_day, rows in snapshots:
        for r in rows:
            key = (r["pin_id"], r["value_kind"])
            state = (r["status"], r.get("realised"), r.get("outcome"), r.get("anomaly"))
            if key in last and last[key][0] == state:
                continue
            event_id = f"{r['pin_id']}|{r['value_kind']}|{build_day}"
            events.append({"event_id": event_id, "recorded_on": build_day, **dict(r),
                           "supersedes": last[key][1] if key in last else None})
            last[key] = (state, event_id)
    return events


def write_tables(out_dir: Path, *, forecasts: Sequence[tuple[Mapping, Mapping]], pins: Sequence[Mapping],
                 snapshots: Sequence[tuple[str, Sequence[Mapping]]], features: Sequence[Mapping],
                 quantile_scores: Sequence[Mapping]) -> dict[str, int]:
    """Write the tables into `out_dir` (a private folder until D5). Returns row counts."""
    out_dir = Path(out_dir)
    tables = {
        "weekly_forecasts.csv": ([r for doc, man in forecasts for r in forecast_rows(doc, man)], FORECAST_COLUMNS),
        "weekly_pins.csv": (list(pins), list(pins[0].keys()) if pins else ["pin_id"]),
        "weekly_outcomes.csv": (outcome_events(snapshots), None),
        "weekly_scores.csv": (list(quantile_scores), None),
        "weekly_origin_features.csv": (list(features), None),
    }
    counts = {}
    for name, (rows, cols) in tables.items():
        if cols is None:
            cols = sorted({k for r in rows for k in r}) or ["none"]
        rows = [{k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()} for r in rows]
        vintage.write_exclusive(out_dir / name, _csv(rows, cols))
        counts[name] = len(rows)
    return counts
