"""Recalibration: the function from what a forecaster SAYS to what HAPPENS.

THE ARGUMENT FOR DOING THIS AT ALL. The IDB Track B scoring decomposed to
resolution 0.173 against reliability 0.166. Read plainly: the forecaster's
RANKING carries real information, and its PRICES are wrong. A Brier score is
reliability minus resolution plus uncertainty, so a reliability of 0.166 is
0.166 of Brier available to anyone who reprices the same forecasts correctly.
That is a larger and far cheaper win than building a better forecaster, and it
needs no new data beyond outcomes we are already collecting.

THE REASON THIS MODULE MOSTLY REFUSES. A calibration map fitted on a handful of
outcomes and then evaluated on those same outcomes will always look excellent
and will always be worthless. The failure is not subtle: with 29 pooled rows and
14 of them in a single decile, a flexible map can drive in-sample Brier to
almost zero by memorising. So `fit` is gated. Below the power floor it returns a
Refusal carrying no map at all -- there is no field to accidentally use -- and
states what sample size would change the answer.

This mirrors the discipline the rest of the programme already runs on: a check
that cannot fail is not a check, and an estimator that cannot refuse is not an
estimator.

WHAT UNLOCKS WHEN. Roughly, and stated in advance so it is not chosen after
seeing results:
  n >=  10   measure a pooled Brier at all
  n >=  60   read a reliability curve across deciles
  n >= 100   fit a monotone recalibration map worth applying
  n >= 300   fit CONDITIONAL maps, i.e. when this method is trustworthy
The programme stands at 29 today and reaches 60 when the three live blocks
resolve on 2026-10-01.

Stdlib only. Deterministic. No clock of its own.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# Below this, a fitted map is memorisation. Chosen before any fit was attempted:
# 100 outcomes over ~8 deciles is roughly a dozen per bin, the point at which an
# isotonic step is estimated from more than noise.
MIN_ROWS_FOR_MAP = 100
# A decile with fewer than this contributes a step fitted on almost nothing.
MIN_PER_BIN = 8


@dataclass(frozen=True, slots=True)
class Refusal:
    """A fit that did not happen, and what would change that.

    Deliberately carries no map, no coefficients and no transform. There is
    nothing here to misuse.
    """

    reason: str
    n_rows: int
    n_needed: int
    detectable_now: str
    what_would_change_it: str


@dataclass(frozen=True, slots=True)
class CalibrationMap:
    """A fitted monotone map, with its honest out-of-sample score."""

    knots: tuple[tuple[float, float], ...]
    n_rows: int
    in_sample_brier: float
    cross_validated_brier: float
    raw_brier: float
    cv_improvement: float

    def apply(self, probability: float) -> float:
        """Reprice one forecast. Linear interpolation between fitted knots."""
        xs = [k[0] for k in self.knots]
        ys = [k[1] for k in self.knots]
        if probability <= xs[0]:
            return ys[0]
        if probability >= xs[-1]:
            return ys[-1]
        for (x0, y0), (x1, y1) in zip(self.knots, self.knots[1:]):
            if x0 <= probability <= x1:
                if x1 == x0:
                    return y1
                t = (probability - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return probability


def _pool_adjacent_violators(points: Sequence[tuple[float, int]]) -> tuple[tuple[float, float], ...]:
    """Isotonic regression by PAVA: the monotone step function of best fit.

    Monotone because a recalibration must not reorder forecasts. Reordering
    would destroy the resolution term, which is the part that is working.
    """
    ordered = sorted(points, key=lambda p: p[0])
    blocks: list[list[float]] = [[float(y), 1.0, x] for x, y in ordered]
    changed = True
    while changed:
        changed = False
        merged: list[list[float]] = []
        for block in blocks:
            if merged and merged[-1][0] / merged[-1][1] > block[0] / block[1]:
                prev = merged.pop()
                merged.append([prev[0] + block[0], prev[1] + block[1], prev[2]])
                changed = True
            else:
                merged.append(block)
        blocks = merged
    return tuple((b[2], b[0] / b[1]) for b in blocks)


def _brier(pairs: Sequence[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def power_check(rows: Sequence) -> dict:
    """What this corpus can and cannot currently support."""
    n = len(rows)
    bins: dict[str, int] = {}
    for row in rows:
        bins[row.band] = bins.get(row.band, 0) + 1
    usable = sum(1 for count in bins.values() if count >= MIN_PER_BIN)
    # Half-width of a 95% interval on a rate at p=0.5, the widest case.
    half_width = 1.96 * math.sqrt(0.25 / n) if n else float("nan")
    return {
        "n_rows": n,
        "n_bins_occupied": len(bins),
        "n_bins_with_enough": usable,
        "min_per_bin": MIN_PER_BIN,
        "rows_per_bin": dict(sorted(bins.items())),
        "widest_95_half_width": round(half_width, 4),
        "minimum_detectable_miscalibration": (
            f"+/- {half_width:.2f} on an observed rate. A forecaster priced 0.15 whose true "
            f"rate is 0.42 -- the bias this programme has already measured -- is only "
            f"{'detectable' if half_width < 0.27 else 'NOT detectable'} at this sample size."),
        "can_fit_map": n >= MIN_ROWS_FOR_MAP and usable >= 4,
    }


def fit(rows: Sequence) -> CalibrationMap | Refusal:
    """Fit a monotone recalibration map, or refuse and say why."""
    power = power_check(rows)
    if not power["can_fit_map"]:
        return Refusal(
            reason=(f"{power['n_rows']} pooled outcomes across {power['n_bins_occupied']} "
                    f"deciles, of which {power['n_bins_with_enough']} carry at least "
                    f"{MIN_PER_BIN} rows. A map fitted here would memorise, not generalise."),
            n_rows=power["n_rows"], n_needed=MIN_ROWS_FOR_MAP,
            detectable_now=power["minimum_detectable_miscalibration"],
            what_would_change_it=(
                "Resolving the three live blocks on 2026-10-01 adds 31 outcomes and takes the "
                "pool to about 60. Two further monthly blocks reach the floor. Clearing the "
                "37 overdue hypotheses in the research store would reach it sooner."),
        )

    pairs = [(row.probability, row.outcome) for row in rows]
    knots = _pool_adjacent_violators(pairs)
    fitted = CalibrationMap(knots=knots, n_rows=len(pairs), in_sample_brier=0.0,
                            cross_validated_brier=0.0, raw_brier=_brier(pairs),
                            cv_improvement=0.0)
    in_sample = _brier([(fitted.apply(p), y) for p, y in pairs])

    # Leave-one-out: refit without each row and score that row. The only honest
    # way to read a map this small, and it is why in-sample is reported beside
    # it rather than alone -- the gap between them IS the overfitting.
    loo = []
    for i in range(len(pairs)):
        held = pairs[:i] + pairs[i + 1:]
        model = CalibrationMap(knots=_pool_adjacent_violators(held), n_rows=len(held),
                               in_sample_brier=0.0, cross_validated_brier=0.0,
                               raw_brier=0.0, cv_improvement=0.0)
        loo.append((model.apply(pairs[i][0]), pairs[i][1]))
    cv = _brier(loo)
    raw = _brier(pairs)
    return CalibrationMap(knots=knots, n_rows=len(pairs),
                          in_sample_brier=round(in_sample, 4),
                          cross_validated_brier=round(cv, 4),
                          raw_brier=round(raw, 4),
                          cv_improvement=round(raw - cv, 4))
