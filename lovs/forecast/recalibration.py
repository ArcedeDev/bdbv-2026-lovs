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

WHAT IS COUNTED. Independent outcomes, not rows. A block prices one series at
several thresholds, and every rung resolves on the one value the series takes; a
corridor block prices several sources into one target, and every corridor
resolves on whether that target reports a case. Such rows share an
`outcome_group` and count once: towards the floor, towards each decile, and in
cross-validation, which holds out every row of an outcome together. The rung
prices of a ladder are set by the forecaster, so the deciles a ladder fills
are chosen, not observed; counting rows let one realised value priced at 100
thresholds clear every check.

WHAT UNLOCKS WHEN. Roughly, and stated in advance so it is not chosen after
seeing results, with n the number of independent outcomes:
  n >=  10   measure a pooled Brier at all
  n >=  60   read a reliability curve across deciles
  n >= 100   fit a monotone recalibration map worth applying
  n >= 300   fit CONDITIONAL maps, i.e. when this method is trustworthy

Stdlib only. Deterministic. No clock of its own.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# Below this, a fitted map is memorisation. Chosen before any fit was attempted:
# 100 outcomes over ~8 deciles is roughly a dozen per bin, the point at which an
# isotonic step is estimated from more than noise. Counted in independent
# outcomes; it was chosen in those units.
MIN_OUTCOMES_FOR_MAP = 100
# A decile with fewer distinct outcomes than this contributes a step fitted on
# almost nothing.
MIN_PER_BIN = 8
# How many deciles must clear MIN_PER_BIN.
MIN_BINS_WITH_ENOUGH = 4


def outcome_group(row) -> str:
    """The realised outcome a row resolves on. A row that names none is its own."""
    return row.outcome_group or row.forecast_id


@dataclass(frozen=True, slots=True)
class Refusal:
    """A fit that did not happen, and what would change that.

    Deliberately carries no map, no coefficients and no transform. There is
    nothing here to misuse.
    """

    reason: str
    n_rows: int
    n_outcome_groups: int
    n_needed: int
    detectable_now: str
    what_would_change_it: str


@dataclass(frozen=True, slots=True)
class CalibrationMap:
    """A fitted monotone map, with its honest out-of-sample score."""

    knots: tuple[tuple[float, float], ...]
    n_rows: int
    n_outcome_groups: int
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


def _map(knots: tuple[tuple[float, float], ...]) -> CalibrationMap:
    """A bare map for scoring inside cross-validation."""
    return CalibrationMap(knots=knots, n_rows=0, n_outcome_groups=0, in_sample_brier=0.0,
                          cross_validated_brier=0.0, raw_brier=0.0, cv_improvement=0.0)


def power_check(rows: Sequence) -> dict:
    """What this corpus can and cannot currently support, in independent outcomes."""
    groups = {outcome_group(row) for row in rows}
    n = len(groups)
    rows_per_bin: dict[str, int] = {}
    groups_per_bin: dict[str, set[str]] = {}
    for row in rows:
        rows_per_bin[row.band] = rows_per_bin.get(row.band, 0) + 1
        groups_per_bin.setdefault(row.band, set()).add(outcome_group(row))
    usable = sum(1 for members in groups_per_bin.values() if len(members) >= MIN_PER_BIN)
    # Half-width of a 95% interval on a rate at p=0.5, the widest case. Rows that
    # share an outcome add no information to a rate, so n is the outcome count.
    half_width = 1.96 * math.sqrt(0.25 / n) if n else float("nan")
    return {
        "n_rows": len(rows),
        "n_outcome_groups": n,
        "n_bins_occupied": len(rows_per_bin),
        "n_bins_with_enough": usable,
        "min_per_bin": MIN_PER_BIN,
        "rows_per_bin": dict(sorted(rows_per_bin.items())),
        "outcome_groups_per_bin": {band: len(members)
                                   for band, members in sorted(groups_per_bin.items())},
        "widest_95_half_width": round(half_width, 4),
        "minimum_detectable_miscalibration": (
            f"+/- {half_width:.2f} on an observed rate. A forecaster priced 0.15 whose true "
            f"rate is 0.42 -- the bias this programme has already measured -- is only "
            f"{'detectable' if half_width < 0.27 else 'NOT detectable'} at this sample size."),
        "can_fit_map": n >= MIN_OUTCOMES_FOR_MAP and usable >= MIN_BINS_WITH_ENOUGH,
    }


def fit(rows: Sequence) -> CalibrationMap | Refusal:
    """Fit a monotone recalibration map, or refuse and say why."""
    power = power_check(rows)
    n = power["n_outcome_groups"]
    if not power["can_fit_map"]:
        short = MIN_OUTCOMES_FOR_MAP - n
        needs = []
        if short > 0:
            needs.append(f"{short} more independent outcome{'' if short == 1 else 's'}")
        if power["n_bins_with_enough"] < MIN_BINS_WITH_ENOUGH:
            needs.append(f"at least {MIN_BINS_WITH_ENOUGH} deciles holding {MIN_PER_BIN} "
                         f"distinct outcomes each")
        return Refusal(
            reason=(f"{n} independent outcomes ({power['n_rows']} rows) across "
                    f"{power['n_bins_occupied']} deciles, of which "
                    f"{power['n_bins_with_enough']} hold at least {MIN_PER_BIN} distinct "
                    f"outcomes. A map fitted here would memorise, not generalise."),
            n_rows=power["n_rows"], n_outcome_groups=n, n_needed=MIN_OUTCOMES_FOR_MAP,
            detectable_now=power["minimum_detectable_miscalibration"],
            what_would_change_it=(
                f"Needs {' and '.join(needs)}. A further threshold on a series and window "
                f"already priced adds a row, not an outcome."),
        )

    pairs = [(row.probability, row.outcome) for row in rows]
    knots = _pool_adjacent_violators(pairs)
    in_sample = _brier([(_map(knots).apply(p), y) for p, y in pairs])

    # Leave one outcome out: refit without every row of an outcome and score those
    # rows. Holding out one row at a time would leave its sibling rungs, resolved by
    # the same value, in training. In-sample is reported beside it rather than alone
    # -- the gap between them IS the overfitting.
    keyed = [(row.probability, row.outcome, outcome_group(row)) for row in rows]
    held_out = []
    for group in dict.fromkeys(key for _, _, key in keyed):
        model = _map(_pool_adjacent_violators([(p, y) for p, y, key in keyed if key != group]))
        held_out += [(model.apply(p), y) for p, y, key in keyed if key == group]
    cv = _brier(held_out)
    raw = _brier(pairs)
    return CalibrationMap(knots=knots, n_rows=len(pairs), n_outcome_groups=n,
                          in_sample_brier=round(in_sample, 4),
                          cross_validated_brier=round(cv, 4),
                          raw_brier=round(raw, 4),
                          cv_improvement=round(raw - cv, 4))
