"""Walk-forward backtest of the operational forecasters, committed so it reproduces.

Why this exists. The Block 6/7 validation (docs/operational-forecaster-validation.md)
reported skill +0.249 from a backtest whose script was never committed, so that figure
cannot be reproduced. Block 6/7 then resolved at +0.144. This module is the reproducible
replacement, and it is how the Block 8 challenger's window was chosen before pinning.

Design. At each origin, every method forecasts the level on the last data day at or
before (last known data day + horizon), using only what was known at the origin. A pin
is priced from data through day D and resolves 31 days later (data through 2026-10-01,
resolution 2026-11-01), so the default horizon is 31 days.

What "known" means. Given each data day's first publication day (`publication_days`),
an origin is a publication day and the history is only the data days published by
then. Without it, origins are data days and the history runs through the origin day,
which assumes figures were readable the day they describe: the one-day look-ahead the
2026-09-01 errata recorded. Either way this is a RETROSPECTIVE test on the corrected
extract: values carry later corrections, so it is not a replay of the figures as they
stood in real time.

Scores.
- CRPS of the simulated end-value ensemble against the realised level: a proper score of
  the whole predictive distribution, threshold-free, so window lengths compare fairly.
  Reported per metric as skill against the incumbent, 1 - CRPS(method) / CRPS(incumbent).
- Threshold Brier on pin-shaped questions: thresholds at the 0.1/0.3/0.5/0.7/0.9
  quantiles of a 50/50 mixture of the two methods' end values, so neither method sets
  the questions; each method prices P(end <= threshold).

Stdlib only. Deterministic: every forecast is seeded from (metric, origin).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import statistics
from dataclasses import dataclass
from typing import Sequence

from lovs.forecast import cadenceintegrity as ci
from lovs.forecast import opsforecast as of

HORIZON_DAYS = 31
LEVELS = (0.1, 0.3, 0.5, 0.7, 0.9)
BACKTEST_PATHS = 2000


@dataclass(frozen=True)
class Method:
    name: str
    recent_days: int | None
    block: int = of.DEFAULT_BLOCK
    # "walk": block bootstrap of day-over-day changes (drift). "levels": the recent spread
    # of levels persists (no drift).
    kind: str = "walk"


INCUMBENT = Method("level_bootstrap", None)


def seed_for(metric: str, origin: dt.date, method: str) -> int:
    digest = hashlib.sha256(f"{metric}|{origin.isoformat()}|{method}".encode()).hexdigest()
    return int(digest[:8], 16)


def crps(ensemble: Sequence[float], y: float) -> float:
    """Ensemble CRPS: E|X - y| - 0.5 E|X - X'|, the latter via the sorted-sample identity."""
    xs = sorted(ensemble)
    n = len(xs)
    term1 = sum(abs(x - y) for x in xs) / n
    # sum_{i<j} (x_j - x_i) = sum_i x_i * (2i - n + 1) for sorted x (0-based i)
    pair = sum(x * (2 * i - n + 1) for i, x in enumerate(xs))
    term2 = pair / (n * n)
    return term1 - term2


def quantile(sorted_xs: Sequence[float], q: float) -> float:
    k = min(len(sorted_xs) - 1, max(0, int(round(q * (len(sorted_xs) - 1)))))
    return sorted_xs[k]


def publication_days(rows: Sequence[dict]) -> dict[dt.date, dt.date]:
    """Each data day's first publication day, from the extract's SitRep packets."""
    delivered, _ = ci.packets(rows)
    out: dict[dt.date, dt.date] = {}
    for packet in delivered:
        known = out.get(packet.data_day)
        if known is None or packet.published_day < known:
            out[packet.data_day] = packet.published_day
    return out


def target(obs: Sequence[of.Observation], origin: dt.date, horizon: int, tolerance: int = 3) -> float | None:
    """The level on the last data day at or before origin + horizon, if one is close enough."""
    due = origin + dt.timedelta(days=horizon)
    candidates = [o for o in obs if o.date <= due]
    if not candidates or (due - candidates[-1].date).days > tolerance or candidates[-1].date <= origin:
        return None
    return candidates[-1].value


@dataclass
class Scored:
    metric: str
    origin: dt.date
    method: str
    crps: float | None
    briers: list[float]


def origins(
    obs: Sequence[of.Observation],
    published: dict[dt.date, dt.date] | None,
    metric: str = "",
) -> list[tuple[dt.date, list[of.Observation]]]:
    """(origin, history) pairs: what a forecaster could have read on each origin day.

    With publication days, an origin is a publication day and its history is every data
    day published on or before it. Without them, an origin is a data day and its history
    runs through that day.
    """
    if published is None:
        return [(o.date, list(obs[: i + 1])) for i, o in enumerate(obs)]
    missing = [o.date for o in obs if o.date not in published]
    if missing:
        raise ValueError(f"{metric}: no publication day for data days {missing[:3]}")
    days = sorted({published[o.date] for o in obs})
    return [(day, [o for o in obs if published[o.date] <= day]) for day in days]


def run_metric(
    obs: Sequence[of.Observation],
    metric: str,
    methods: Sequence[Method],
    *,
    lo: float | None = None,
    hi: float | None = None,
    horizon: int = HORIZON_DAYS,
    n_paths: int = BACKTEST_PATHS,
    step: int = 2,
    min_changes: int = 20,
    published: dict[dt.date, dt.date] | None = None,
) -> list[Scored]:
    """Score every method at every eligible origin of one metric's series.

    `published` maps each data day to its first publication day. When given, origins
    are publication days and each history holds only data days published by then; a
    data day with no publication day is an error rather than a silent inclusion.
    """
    longest = max((m.recent_days or 0) for m in methods)
    out: list[Scored] = []
    for origin, history in origins(obs, published, metric)[::step]:
        if not history:
            continue
        base = history[-1].date
        if len(history) < min_changes + 1 or (history[0].date > base - dt.timedelta(days=longest)):
            continue
        y = target(obs, base, horizon)
        if y is None:
            continue
        ends: dict[str, list[float]] = {}
        for m in methods:
            window = of.recent(history, m.recent_days)
            if len(window) < m.block + 2:
                break
            seed = seed_for(metric, origin, m.name)
            if m.kind == "levels":
                ends[m.name] = of.recent_level_ends(history, recent_days=m.recent_days, seed=seed, n_paths=n_paths)
            else:
                ends[m.name] = of.end_values(
                    history, horizon, seed=seed, n_paths=n_paths,
                    block=m.block, lo=lo, hi=hi, recent_days=m.recent_days,
                )
        if len(ends) != len(methods):
            continue
        for m in methods:
            out.append(Scored(metric, origin, m.name, crps(ends[m.name], y), []))
        # Pin-shaped threshold questions against the incumbent and each challenger in turn.
        incumbent = ends[methods[0].name]
        for m in methods[1:]:
            mix = sorted(incumbent + ends[m.name])
            thresholds = [quantile(mix, q) for q in LEVELS]
            for name in (methods[0].name, m.name):
                xs = ends[name]
                probs = [sum(1 for x in xs if x <= t) / len(xs) for t in thresholds]
                outcomes = [1.0 if y <= t else 0.0 for t in thresholds]
                briers = [(p - o) ** 2 for p, o in zip(probs, outcomes)]
                key = name if name != methods[0].name else f"{name}|vs|{m.name}"
                out.append(Scored(metric, origin, key, None, briers))
    return out


def summarise(scored: Sequence[Scored], incumbent: str = INCUMBENT.name) -> dict:
    """Per-metric CRPS skill against the incumbent, and paired threshold-Brier differences."""
    by: dict[tuple[str, str], list[Scored]] = {}
    for s in scored:
        by.setdefault((s.metric, s.method), []).append(s)
    metrics = sorted({s.metric for s in scored})
    challengers = sorted({s.method for s in scored if "|" not in s.method and s.method != incumbent and s.crps is not None})
    report: dict = {"metrics": {}, "challengers": {}}
    for c in challengers:
        skills, deltas, origins = [], [], 0
        for metric in metrics:
            inc = [s.crps for s in by.get((metric, incumbent), []) if s.crps is not None]
            chal = [s.crps for s in by.get((metric, c), []) if s.crps is not None]
            if not inc or len(inc) != len(chal):
                continue
            skill = 1 - statistics.fmean(chal) / statistics.fmean(inc) if statistics.fmean(inc) else 0.0
            b_inc = [b for s in by.get((metric, f"{incumbent}|vs|{c}"), []) for b in s.briers]
            b_chal = [b for s in by.get((metric, c), []) if s.briers for b in s.briers]
            delta = statistics.fmean(b_chal) - statistics.fmean(b_inc) if b_inc and b_chal else float("nan")
            report["metrics"].setdefault(metric, {})[c] = {
                "origins": len(inc), "crps_skill": round(skill, 4),
                "brier_incumbent": round(statistics.fmean(b_inc), 4) if b_inc else None,
                "brier_challenger": round(statistics.fmean(b_chal), 4) if b_chal else None,
                "brier_delta": round(delta, 4) if delta == delta else None,
            }
            skills.append(skill)
            if delta == delta:
                deltas.append(delta)
            origins += len(inc)
        report["challengers"][c] = {
            "metrics": len(skills),
            "mean_crps_skill": round(statistics.fmean(skills), 4) if skills else None,
            "metrics_improved": sum(1 for s in skills if s > 0),
            "mean_brier_delta": round(statistics.fmean(deltas), 4) if deltas else None,
            "origins": origins,
        }
    return report


# The grid fixed before the Blocks 8 and 9 challengers were chosen. Changing it after
# seeing results would be selection, so it lives here, not in a caller.
SELECTION_GRID: tuple[Method, ...] = (
    INCUMBENT,
    Method("walk_21d", 21), Method("walk_28d", 28), Method("walk_42d", 42),
    Method("levels_14d", 14, kind="levels"), Method("levels_21d", 21, kind="levels"),
    Method("levels_28d", 28, kind="levels"), Method("levels_42d", 42, kind="levels"),
)


def selection_report(rows: Sequence[dict], *, published: bool = True) -> dict:
    """The method-selection table for every Block 8 and 9 series, from one extract.

    Cumulative series are floored at zero here rather than at each origin's last value;
    the pins floor them at the last value, which constrains both walk methods equally.
    """
    from lovs.forecast import pins_block8 as p8

    days = publication_days(rows) if published else None
    scored: list[Scored] = []
    for metric in (*p8.BLOCK8_METRICS, *p8.BLOCK9_METRICS):
        obs = metric.observations(rows)
        lo, hi = (0.0, 100.0) if metric.kind == "percent" else (0.0, None)
        scored += run_metric(obs, metric.series, SELECTION_GRID, lo=lo, hi=hi, published=days)
    report = summarise(scored)
    report["_meta"] = {
        "cut": "publication day" if published else "data day",
        "horizon_days": HORIZON_DAYS, "paths": BACKTEST_PATHS, "levels": list(LEVELS),
        "grid": [m.name for m in SELECTION_GRID],
        "label": ("retrospective walk-forward test on the corrected extract; values carry later "
                  "corrections, so this is not a replay of figures as they stood in real time"),
    }
    return report


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Method-selection backtest for Blocks 8 and 9")
    parser.add_argument("--series", default=str(of.REPO / "data" / "operational-series-2026-10-01.json"))
    parser.add_argument("--data-day-cut", action="store_true",
                        help="cut history by data day (the older, optimistic cut) instead of publication day")
    args = parser.parse_args()
    print(json.dumps(selection_report(of.load_rows(args.series), published=not args.data_day_cut),
                     indent=1, sort_keys=True))
