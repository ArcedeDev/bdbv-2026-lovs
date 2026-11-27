# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 6: WIS, relative WIS, PIT and the scale rule for negative truths.

Spec wk2: the PIT is taken from the empirical CDF of the method's own draws, so it is exactly
uniform under calibration for count, lattice-mean and capped forecasts too.
"""
from __future__ import annotations

import math
import random
import statistics
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from lovs import forecast_scoring as fs
from lovs.forecast.weekly import register, resolve, score, spec
from tests.weekly_fixtures import fixture_week

# Worked by hand from Bracher et al. (2021), equations 1 and 2: the interval score
# IS_a = (u - l) + (2/a)(l - y)1(y < l) + (2/a)(y - u)1(y > u) and
# WIS = (0.5|y - m| + sum_k (a_k / 2) IS_{a_k}) / (K + 0.5).
FIVE = [(0.05, 5.0), (0.25, 8.0), (0.5, 10.0), (0.75, 12.0), (0.95, 15.0)]


class WisTest(unittest.TestCase):
    def test_median_only_is_absolute_error(self) -> None:
        self.assertAlmostEqual(fs.wis([(0.5, 10.0)], 16.0), 6.0)

    def test_worked_value_outside_both_intervals(self) -> None:
        # IS_0.5 = 4 + 4 * 4 = 20; IS_0.1 = 10 + 20 * 1 = 30; (3 + 5 + 1.5) / 2.5 = 3.8.
        self.assertAlmostEqual(fs.wis(FIVE, 16.0), 3.8)

    def test_worked_value_inside(self) -> None:
        # (0.5 * 1 + 0.25 * 4 + 0.05 * 10) / 2.5 = 0.8.
        self.assertAlmostEqual(fs.wis(FIVE, 11.0), 0.8)

    def test_worked_value_below(self) -> None:
        # y = 2: IS_0.5 = 4 + 4 * 6 = 28; IS_0.1 = 10 + 20 * 3 = 70; (4 + 7 + 3.5) / 2.5 = 5.8.
        self.assertAlmostEqual(fs.wis(FIVE, 2.0), 5.8)

    def test_interval_form_equals_quantile_form_on_23_levels(self) -> None:
        rng = random.Random(3)
        q = [(lvl, 100 * lvl + rng.uniform(0, 0.5)) for lvl in spec.QUANTILE_LEVELS]
        q = [(lvl, v) for (lvl, _), v in zip(q, sorted(v for _, v in q))]
        for y in (-5.0, 3.0, 50.0, 97.0, 140.0):
            m = dict(q)[0.5]
            total = 0.5 * abs(y - m)
            for lo, hi in zip(q[:11], reversed(q[12:])):
                a = 2 * lo[0]
                l, u = lo[1], hi[1]
                total += (a / 2) * ((u - l) + (2 / a) * max(0.0, l - y) + (2 / a) * max(0.0, y - u))
            self.assertAlmostEqual(fs.wis(q, y), total / 11.5, places=9)

    def test_requires_symmetric_levels_and_median(self) -> None:
        with self.assertRaises(ValueError):
            fs.wis([(0.1, 1.0), (0.5, 2.0)], 1.0)

    def test_coverage(self) -> None:
        self.assertTrue(fs.interval_coverage(FIVE, 11.0, 0.5))
        self.assertFalse(fs.interval_coverage(FIVE, 13.0, 0.5))
        self.assertTrue(fs.interval_coverage(FIVE, 13.0, 0.9))


class RelativeWisTest(unittest.TestCase):
    def test_symmetry_and_baseline(self) -> None:
        scores = {"a": [1.0, 2.0, 4.0], "b": [2.0, 2.0, 2.0], "c": [0.5, 4.0, 1.0]}
        rel = fs.relative_wis(scores, baseline="b")
        self.assertAlmostEqual(rel["b"], 1.0)
        ab = fs.relative_wis({"a": scores["a"], "b": scores["b"]}, baseline="b")["a"]
        ba = fs.relative_wis({"a": scores["a"], "b": scores["b"]}, baseline="a")["b"]
        self.assertAlmostEqual(ab * ba, 1.0)
        self.assertAlmostEqual(ab, (7.0 / 3) / 2.0)

    def test_unequal_targets_refused(self) -> None:
        with self.assertRaises(ValueError):
            fs.relative_wis({"a": [1.0], "b": [1.0, 2.0]}, baseline="b")


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, prod = math.exp(-lam), 0, rng.random()
    while prod > limit:
        k += 1
        prod *= rng.random()
    return k


def _from_masses(masses: dict) -> list[float]:
    return [float(v) for v, count in masses.items() for _ in range(count)]


def _share_at_or_below(cdf: fs.EmpiricalCDF, t: Fraction) -> Fraction:
    """Pr(Z <= t) exactly, for Y drawn from the forecast's own draws and u uniform."""
    total = Fraction(0)
    prev = 0
    for count in cdf.at_or_below:
        lo, hi = Fraction(prev, cdf.n), Fraction(count, cdf.n)
        total += (hi - lo) * min(Fraction(1), max(Fraction(0), (t - lo) / (hi - lo)))
        prev = count
    return total


def _cases() -> dict[str, list[float]]:
    """The statement-first pilot's cases (labs/statement-first-pilot-2026-11-26/RESULT.md), each
    as 20,000 draws. B1: masses 0.3/0.4/0.3 on 0/1/2; the wk1 PIT, interpolated through the 23
    quantiles, gave Pr(Z <= 0.25) = 0.30 and a KS distance of 0.05 on this forecast. L1 and L2:
    7-day means k/7. C2: a percent series with an atom at its cap of 100."""
    rng = random.Random(11)
    l2 = [statistics.fmean([_poisson(rng, 3.0) for _ in range(7)]) for _ in range(20000)]
    c2 = [100.0 if rng.random() < 0.3 else rng.uniform(80.0, 100.0) for _ in range(20000)]
    return {"B1": _from_masses({0: 6000, 1: 8000, 2: 6000}),
            "L1": [k / 7 for k in (3, 4) for _ in range(10000)],
            "L2": l2, "C2": c2, "point": [5.0] * 20000}


class PitTest(unittest.TestCase):
    def test_atoms_tile_the_unit_interval_so_the_pit_is_exactly_uniform(self) -> None:
        for name, draws in _cases().items():
            cdf = fs.empirical_cdf(draws)
            self.assertEqual(cdf.n, len(draws), name)
            prev = 0.0
            for value, count in zip(cdf.values, cdf.at_or_below):
                lower, upper = fs.pit_bounds(cdf, value)
                self.assertEqual(lower, prev, name)
                self.assertEqual(upper, count / cdf.n, name)
                prev = upper
            self.assertEqual(prev, 1.0, name)
            for t in [Fraction(j, 20) for j in range(21)] + [Fraction(l).limit_denominator(1000)
                                                               for l in spec.QUANTILE_LEVELS]:
                self.assertEqual(_share_at_or_below(cdf, t), t, (name, t))

    def test_b1_is_exact_where_the_wk1_pit_was_off(self) -> None:
        cdf = fs.empirical_cdf(_cases()["B1"])
        self.assertEqual(fs.pit_bounds(cdf, 0.0), (0.0, 0.3))
        self.assertEqual(fs.pit_bounds(cdf, 1.0), (0.3, 0.7))
        self.assertEqual(fs.pit_bounds(cdf, 2.0), (0.7, 1.0))
        self.assertEqual(_share_at_or_below(cdf, Fraction(1, 4)), Fraction(1, 4))   # wk1: 0.30
        self.assertEqual(_share_at_or_below(cdf, Fraction(1, 10)), Fraction(1, 10))  # wk1: 0.12

    def test_outcomes_outside_the_draws_and_the_randomisation(self) -> None:
        cdf = fs.empirical_cdf(_cases()["point"])
        self.assertEqual(fs.pit_bounds(cdf, 4.0), (0.0, 0.0))
        self.assertEqual(fs.pit_bounds(cdf, 6.0), (1.0, 1.0))
        self.assertEqual(fs.randomized_pit(cdf, 5.0, 0.0), 0.0)
        self.assertEqual(fs.randomized_pit(cdf, 5.0, 0.5), 0.5)
        self.assertEqual(fs.randomized_pit(cdf, 5.0, 1.0), 1.0)
        self.assertEqual(fs.randomized_pit(cdf, 4.0, 0.7), 0.0)
        self.assertEqual(fs.randomized_pit(cdf, 6.0, 0.7), 1.0)

    def test_a_realised_7_day_mean_lands_on_its_atom(self) -> None:
        # Outcome and draws are both statistics.fmean of 7 integers, rounded to 6 decimals.
        cdf = fs.empirical_cdf(_cases()["L1"])
        y = statistics.fmean([1, 0, 0, 0, 1, 1, 0])
        self.assertEqual(fs.pit_bounds(cdf, y), (0.0, 0.5))
        for k in range(0, 7 * 10 ** 5):
            self.assertNotEqual(round(k / 7, 6), round((k + 1) / 7, 6))

    def test_uniform_for_outcomes_from_the_population_law(self) -> None:
        # The forecast is 20,000 draws of the true law; outcomes are fresh draws of it. Decile
        # shares stay within 0.015 of 0.1 (DKW scale for both samples of 20,000).
        rng = random.Random(5)
        laws = {"Poisson(3)": lambda: float(_poisson(rng, 3.0)),
                "7-day mean of Poisson(3)": lambda: statistics.fmean([_poisson(rng, 3.0) for _ in range(7)]),
                "Uniform(0, 100)": lambda: rng.uniform(0, 100)}
        for name, draw in laws.items():
            cdf = fs.empirical_cdf([draw() for _ in range(20000)])
            counts = [0] * 10
            for _ in range(20000):
                counts[min(9, int(fs.randomized_pit(cdf, draw(), rng.random()) * 10))] += 1
            for c in counts:
                self.assertLess(abs(c - 2000), 300, (name, counts))

    def test_empty_or_non_finite_draws_are_refused(self) -> None:
        for bad in ([], [1.0, float("nan")], [float("inf")]):
            with self.assertRaises(ValueError):
                fs.empirical_cdf(bad)
        with self.assertRaises(ValueError):
            fs.pit_bounds(fs.empirical_cdf([1.0]), float("nan"))


class QuantileScoresTest(unittest.TestCase):
    """resolve.quantile_scores on a fixture week: the PIT comes from the week's rebuilt draws."""

    def test_rows_carry_the_pit_and_its_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            build, public, private, _ = fixture_week(Path(tmp))
            cdfs, _ = register.verified_cdfs(public, private / "forecasts.json", store_root=public.parent)
        doc = build.forecasts
        cells = doc["forecasts"]
        statuses = [resolve.TargetStatus(f["series"], spec.HORIZONS.index(f["horizon_days"]) + 1, "observed",
                                         f["quantiles"][11][1], "ok") for f in cells]
        statuses = list({(s.series, s.k): s for s in statuses}.values())
        rows = resolve.quantile_scores(doc, statuses, build.R, cdfs)
        self.assertEqual(len(rows), len(cells))
        for row, f in zip(rows, cells):
            lower, upper = fs.pit_bounds(cdfs[f["forecast_id"]], row["realised"])
            self.assertEqual((row["pit_lower"], row["pit_upper"]), (round(lower, 6), round(upper, 6)))
            u = random.Random(f"{f['forecast_id']}|pit").random()
            self.assertAlmostEqual(row["pit"], row["pit_lower"] + u * (row["pit_upper"] - row["pit_lower"]),
                                   delta=1e-6)
            self.assertEqual(row["wis"], round(fs.wis(f["quantiles"], row["realised"]), 6))
        with self.assertRaises(ValueError):
            resolve.quantile_scores(doc, statuses, build.R, {})
        # A week whose draws cannot be rebuilt keeps its quantile scores and has no PIT.
        self.assertTrue(all(r["pit_unscored"] is None for r in rows))
        unscored = resolve.quantile_scores(doc, statuses, build.R, None, pit_unscored=register.PIT_EARLIER_SPEC)
        self.assertEqual([r["wis"] for r in unscored], [r["wis"] for r in rows])
        self.assertEqual([r["coverage"] for r in unscored], [r["coverage"] for r in rows])
        self.assertTrue(all(r["pit"] is r["pit_lower"] is r["pit_upper"] is None for r in unscored))
        self.assertTrue(all(r["pit_unscored"] == register.PIT_EARLIER_SPEC for r in unscored))
        with self.assertRaises(ValueError):
            resolve.quantile_scores(doc, statuses, build.R, None)


class ScaleRuleTest(unittest.TestCase):
    def test_negative_truth_scores_on_the_natural_scale_only(self) -> None:
        q = [(lvl, 100.0 * lvl) for lvl in spec.QUANTILE_LEVELS]
        cdf = fs.empirical_cdf([float(v) for v in range(101)])
        neg = score.target_scores(q, -269.0, u=0.5, cdf=cdf)
        self.assertIsNotNone(neg["wis"])
        self.assertIsNone(neg["wis_log"])
        pos = score.target_scores(q, 40.0, u=0.5, cdf=cdf)
        self.assertIsNotNone(pos["wis_log"])
        self.assertEqual((pos["pit_lower"], pos["pit_upper"]), (round(40 / 101, 6), round(41 / 101, 6)))
        self.assertAlmostEqual(pos["pit"], pos["pit_lower"] + 0.5 * (pos["pit_upper"] - pos["pit_lower"]), delta=1e-6)
        self.assertEqual((neg["pit_lower"], neg["pit_upper"], neg["pit"]), (0.0, 0.0, 0.0))
        self.assertEqual(set(pos["coverage"]), {"50", "80", "90", "95"})

    def test_pin_scores(self) -> None:
        got = score.pin_scores(0.8, 1)
        self.assertAlmostEqual(got["brier"], 0.04)
        self.assertLess(got["log"], 0.0)
        clipped = score.pin_scores(1.0, 0)
        self.assertTrue(clipped["log"] > -10)


if __name__ == "__main__":
    unittest.main()
