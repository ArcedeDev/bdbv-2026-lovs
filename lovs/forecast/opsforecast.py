"""Empirical forecasters over the operational indicator series.

Why this exists. The corridor model saturated: at the current burden its
target-level hazard is 1.000000 for every eligible target, so it can no longer
produce a discriminating spatial forecast. The operational indicators can.
contact_followup_percent carries 82 observations and moved 7 -> 87; lab
positivity carries 66 and fell 46 -> 14 while confirmed cases grew sixtyfold.
Those are real series with real uncertainty, and nobody forecasts them.

Method. A stationary block bootstrap over observed day-over-day changes. For a
30-day question the forecaster resamples contiguous blocks of historical
first-differences, walks them forward from the last observed level, and counts
the fraction of simulated paths in which the registered event occurs. Blocks
rather than single days because these series are autocorrelated: a contact
follow-up rate does not jump independently each morning, and resampling single
days would understate the chance of a sustained excursion.

What it can and cannot support. It can support "will this series stay in band",
"will it breach a threshold at least once", "where will it sit at the horizon" --
questions about a system continuing to behave roughly as it has. It cannot
support a structural break: a funding cliff, a security incident that closes a
province, or a sudden change in reporting definition are all outside what the
historical differences encode. A pin whose resolution turns on such an event
should say so in its own rationale rather than lean on this number.

Deterministic: every draw is seeded, so a forecast run reproduces exactly.
Stdlib only. No network. No clock -- the as-of date is passed in.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERIES = REPO / "data" / "operational-series-2026-09-01.json"

# Enough paths that the third decimal of a probability is stable across seeds,
# cheap enough that the whole pin set recomputes in under a second.
DEFAULT_PATHS = 20000
# Five-day blocks: long enough to carry a week's worth of autocorrelation,
# short enough that a 100-point series still offers many distinct blocks.
DEFAULT_BLOCK = 5


@dataclass(frozen=True, slots=True)
class Observation:
    date: dt.date
    value: float


@dataclass(frozen=True, slots=True)
class Forecast:
    """One probability, with everything needed to audit how it was produced."""

    question: str
    probability: float
    metric: str
    n_observations: int
    last_value: float
    last_date: str
    horizon_days: int
    n_paths: int
    block: int
    seed: int
    method: str


def load_rows(path: Path | str = DEFAULT_SERIES) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["rows"]


def series(rows: Sequence[dict], metric: str) -> list[Observation]:
    """The dated, non-null, chronologically ordered observations of one metric."""
    out: list[Observation] = []
    seen: set[dt.date] = set()
    for row in rows:
        raw, stamp = row.get(metric), row.get("data_as_of")
        if raw is None or not stamp:
            continue
        try:
            when = dt.date.fromisoformat(str(stamp)[:10])
        except ValueError:
            continue
        # Two SitReps occasionally carry the same data day; keep the first so a
        # duplicated day cannot double-weight one observation in the bootstrap.
        if when in seen:
            continue
        seen.add(when)
        out.append(Observation(when, float(raw)))
    out.sort(key=lambda o: o.date)
    return out


def diffs(obs: Sequence[Observation]) -> list[float]:
    """Day-over-day changes, normalised per elapsed day.

    The series has gaps (a missed SitRep, an indicator absent from one packet).
    Dividing by the elapsed day count keeps a three-day gap from entering the
    bootstrap as one enormous single-day move.
    """
    out: list[float] = []
    for prev, cur in zip(obs, obs[1:]):
        span = (cur.date - prev.date).days or 1
        out.append((cur.value - prev.value) / span)
    return out


def _paths(
    obs: Sequence[Observation],
    horizon_days: int,
    n_paths: int,
    block: int,
    seed: int,
    lo: float | None,
    hi: float | None,
) -> list[list[float]]:
    steps = diffs(obs)
    if len(steps) < block + 1:
        raise ValueError(
            f"only {len(steps)} usable day-over-day changes; need more than the "
            f"block length ({block}) to bootstrap"
        )
    rng = random.Random(seed)
    start = obs[-1].value
    built: list[list[float]] = []
    for _ in range(n_paths):
        level, path = start, []
        while len(path) < horizon_days:
            i = rng.randrange(0, len(steps) - block + 1)
            for step in steps[i : i + block]:
                if len(path) >= horizon_days:
                    break
                level += step
                if lo is not None:
                    level = max(lo, level)
                if hi is not None:
                    level = min(hi, level)
                path.append(level)
        built.append(path)
    return built


def probability(
    obs: Sequence[Observation],
    horizon_days: int,
    event: Callable[[list[float]], bool],
    *,
    seed: int,
    n_paths: int = DEFAULT_PATHS,
    block: int = DEFAULT_BLOCK,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """Fraction of simulated paths in which `event` occurs."""
    built = _paths(obs, horizon_days, n_paths, block, seed, lo, hi)
    return sum(1 for p in built if event(p)) / len(built)


# --- event shapes the pins are written against -------------------------------

def ever_below(threshold: float) -> Callable[[list[float]], bool]:
    """True if the series touches or goes under `threshold` on any day."""
    return lambda path: any(v <= threshold for v in path)


def ever_above(threshold: float) -> Callable[[list[float]], bool]:
    return lambda path: any(v >= threshold for v in path)


def ends_below(threshold: float) -> Callable[[list[float]], bool]:
    """True if the series sits under `threshold` on the final day."""
    return lambda path: path[-1] <= threshold


def ends_above(threshold: float) -> Callable[[list[float]], bool]:
    return lambda path: path[-1] >= threshold


def days_below_at_least(threshold: float, days: int) -> Callable[[list[float]], bool]:
    """True if at least `days` days sit under `threshold`. A sustained excursion."""
    return lambda path: sum(1 for v in path if v <= threshold) >= days


def stays_within(lo: float, hi: float) -> Callable[[list[float]], bool]:
    """True if every day stays inside the band. A hold, not a level."""
    return lambda path: all(lo <= v <= hi for v in path)


def forecast(
    rows: Sequence[dict],
    metric: str,
    question: str,
    horizon_days: int,
    event: Callable[[list[float]], bool],
    *,
    seed: int,
    method: str,
    n_paths: int = DEFAULT_PATHS,
    block: int = DEFAULT_BLOCK,
    lo: float | None = None,
    hi: float | None = None,
) -> Forecast:
    obs = series(rows, metric)
    p = probability(
        obs, horizon_days, event, seed=seed, n_paths=n_paths, block=block, lo=lo, hi=hi
    )
    return Forecast(
        question=question,
        probability=round(p, 4),
        metric=metric,
        n_observations=len(obs),
        last_value=obs[-1].value,
        last_date=obs[-1].date.isoformat(),
        horizon_days=horizon_days,
        n_paths=n_paths,
        block=block,
        seed=seed,
        method=method,
    )
