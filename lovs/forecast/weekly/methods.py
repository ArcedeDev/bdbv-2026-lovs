# SPDX-License-Identifier: Apache-2.0
"""The six frozen weekly methods (DESIGN.md 2.4; unchanged from spec wk1 to wk2).

Flows and N4: A = wk2.drift_bootstrap_28d, B = wk2.level_bootstrap.
N5 to N7:     A = wk2.level_bootstrap,     B = wk2.recent_levels_21d.
Every class: wk2.persistence_8w, wk2.climatology_asof and wk2.ens_pool2 (an equal linear
pool of A and B). Flows never go negative: the bootstrap drops each negative daily step
and the step before it (the SR169-170 +346, -269 pattern), then resamples max(0, step).
Percent and lag series clamp to their bounds; N4 is floored at its last level, as the
monthly blocks' cumulative series are. Paths start on the last observed day and run to
R + 28; week k's value is read on its own days, so no target day is conditioned on.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from lovs.forecast import backtest as bt
from lovs.forecast import opsforecast as of
from lovs.forecast.weekly import spec, targets

D = dt.timedelta


@dataclass
class Draws:
    samples: dict[int, list[float]]          # week k (1..4) -> one value per path
    trajectory_sha256: str
    hashed: int
    floored: int = 0
    steps: int = 0
    seed: int | None = None
    extra: dict = field(default_factory=dict)


def floored_steps(obs: Sequence[of.Observation], recent_days: int | None) -> tuple[list[float], int, int]:
    """Day-over-day steps with every fall and the step before it removed, then max(0, step)."""
    raw = of.diffs(of.recent(obs, recent_days))
    drop: set[int] = set()
    for i, s in enumerate(raw):
        if s < 0:
            drop.update((i, i - 1))
    steps = [max(0.0, s) for i, s in enumerate(raw) if i not in drop]
    return steps, len(raw) - len(steps), len(raw)


def floored_paths(obs, horizon: int, n_paths: int, seed: int, recent_days: int | None):
    steps, floored, total = floored_steps(obs, recent_days)
    if len(steps) < spec.BLOCK + 1:
        raise ValueError(f"only {len(steps)} usable steps after the floor")
    rng = random.Random(seed)
    start, block, built = obs[-1].value, spec.BLOCK, []
    for _ in range(n_paths):
        level, path = start, []
        while len(path) < horizon:
            i = rng.randrange(0, len(steps) - block + 1)
            for step in steps[i:i + block]:
                if len(path) >= horizon:
                    break
                level += step
                path.append(level)
        built.append(path)
    return built, floored, total


def _bounds(series: spec.Series, obs) -> tuple[float | None, float | None]:
    if series.bound == "percent":
        return 0.0, 100.0
    if series.bound == "lag":
        return 0.0, float(spec.LAG_CAP)
    if series.bound == "cumulative":
        return obs[-1].value, None
    return 0.0, None


def weekly_values(series: spec.Series, hist: Sequence[of.Observation], n: int) -> list[float]:
    """The series' weekly target values at the last n+1 week ends before the history ends."""
    end = hist[-1].date

    def near(day: dt.date, tol: int = 2):
        c = [o for o in hist if o.date <= day]
        return c[-1].value if c and (day - c[-1].date).days <= tol else None

    out = []
    for j in range(n, -1, -1):
        e = end - D(days=7 * j)
        if series.kind == "flow":
            a, b = near(e - D(days=7)), near(e)
            v = None if a is None or b is None else b - a
        elif series.kind == "mean":
            w = [o.value for o in hist if e - D(days=7) < o.date <= e]
            v = statistics.fmean(w) if w else None
        else:
            v = near(e)
        out.append(v)
    return [v for v in out if v is not None]


def _digest(rows: Sequence[Sequence[float]]) -> str:
    canonical = json.dumps([[round(v, 6) for v in r] for r in rows], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _week_index(R: dt.date, L: dt.date, k: int) -> tuple[int, int]:
    start, end = targets.week_bounds(R, k)
    return (start - L).days - 1, (end - L).days - 1


def simulate(series: spec.Series, method: str, hist: Sequence[of.Observation], R: dt.date, *, seed: int,
             n_paths: int) -> Draws:
    """One method's draws for weeks 1 to 4 of origin R, from the history alone."""
    L = hist[-1].date
    horizon = (R + D(days=7 * len(spec.HORIZONS)) - L).days
    ks = range(1, len(spec.HORIZONS) + 1)
    lo, hi = _bounds(series, hist)
    floored = total = 0
    if method in (spec.M_DRIFT, spec.M_LEVEL):
        recent = spec.DRIFT_DAYS if method == spec.M_DRIFT else None
        if series.kind == "flow":
            paths, floored, total = floored_paths(hist, horizon, n_paths, seed, recent)
        else:
            paths = of.simulate_paths(hist, horizon, n_paths=n_paths, block=spec.BLOCK, seed=seed,
                                      lo=lo, hi=hi, recent_days=recent)
        samples = {}
        for k in ks:
            i_s, i_e = _week_index(R, L, k)
            if series.kind == "flow":
                samples[k] = [p[i_e] - p[i_s] for p in paths]
            elif series.kind == "mean":
                samples[k] = [statistics.fmean(p[i_s + 1:i_e + 1]) for p in paths]
            else:
                samples[k] = [p[i_e] for p in paths]
        hashed = min(spec.TRAJECTORIES_HASHED, n_paths)
        return Draws(samples, _digest(paths[:hashed]), hashed, floored, total, seed)
    if method == spec.M_RECENT:
        ends = of.recent_level_ends(hist, recent_days=spec.LEVELS_DAYS, seed=seed, n_paths=n_paths)
        samples = {k: list(ends) for k in ks}
    elif method == spec.M_PERSIST:
        wk = weekly_values(series, hist, spec.PERSISTENCE_WEEKS)
        ch = [b - a for a, b in zip(wk, wk[1:])]
        sym = ch + [-c for c in ch]
        rng = random.Random(seed)
        floor = hist[-1].value if series.bound == "cumulative" else 0.0
        cap = 100.0 if series.bound == "percent" else (float(spec.LAG_CAP) if series.bound == "lag" else None)
        samples = {}
        for k in ks:
            vals = []
            for _ in range(n_paths):
                v = wk[-1] + (sum(sym[rng.randrange(len(sym))] for _ in range(k)) if sym else 0.0)
                v = max(floor, v)
                vals.append(min(cap, v) if cap is not None else v)
            samples[k] = vals
    elif method == spec.M_CLIM:
        clim = weekly_values(series, hist, 10 ** 4)
        # Flows never go negative (DESIGN.md 2.4), climatology included: one negative weekly
        # flow on record would otherwise drop the series' cells for the week.
        floor = hist[-1].value if series.bound == "cumulative" else (0.0 if series.kind == "flow" else None)
        if floor is not None:
            clim = [max(floor, v) for v in clim]
        rng = random.Random(seed)
        draws = [clim[rng.randrange(len(clim))] for _ in range(n_paths)]
        samples = {k: list(draws) for k in ks}
    else:
        raise ValueError(f"{method} has no draws of its own")
    hashed = min(spec.TRAJECTORIES_HASHED, n_paths)
    rows = [[samples[k][i] for k in ks] for i in range(hashed)]
    return Draws(samples, _digest(rows), hashed, 0, 0, seed)


def pool(a: Draws, b: Draws) -> Draws:
    """wk2.ens_pool2: the equal linear pool of the class's A and B."""
    samples = {k: a.samples[k] + b.samples[k] for k in a.samples}
    # The pool has no paths of its own: its digest covers its members' digests, each over
    # their first trajectories.
    digest = hashlib.sha256((a.trajectory_sha256 + b.trajectory_sha256).encode("ascii")).hexdigest()
    return Draws(samples, digest, a.hashed)


def quantiles(samples: Sequence[float]) -> list[list[float]]:
    xs = sorted(samples)
    return [[q, round(bt.quantile(xs, q), 6)] for q in spec.QUANTILE_LEVELS]


def mean(samples: Sequence[float]) -> float:
    return round(statistics.fmean(samples), 6)
