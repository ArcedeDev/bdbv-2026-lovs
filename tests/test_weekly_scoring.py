# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 6: WIS, relative WIS, PIT and the scale rule for negative truths."""
from __future__ import annotations

import random
import unittest

from lovs import forecast_scoring as fs
from lovs.forecast.weekly import score, spec

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


class PitTest(unittest.TestCase):
    def test_uniform_under_the_forecast_distribution(self) -> None:
        rng = random.Random(9)
        q = [(lvl, 100.0 * lvl) for lvl in spec.QUANTILE_LEVELS]  # Uniform(0, 100)
        pits = [fs.randomized_pit(q, rng.uniform(0, 100), rng.random()) for _ in range(20000)]
        counts = [0] * 10
        for p in pits:
            counts[min(9, int(p * 10))] += 1
        for c in counts:
            self.assertLess(abs(c - 2000), 200, counts)

    def test_point_mass_is_randomised(self) -> None:
        q = [(lvl, 5.0) for lvl in spec.QUANTILE_LEVELS]
        self.assertAlmostEqual(fs.randomized_pit(q, 5.0, 0.0), 0.0)
        self.assertAlmostEqual(fs.randomized_pit(q, 5.0, 1.0), 1.0)
        self.assertAlmostEqual(fs.randomized_pit(q, 5.0, 0.5), 0.5)
        self.assertEqual(fs.randomized_pit(q, 4.0, 0.7), 0.0)
        self.assertEqual(fs.randomized_pit(q, 6.0, 0.7), 1.0)


class ScaleRuleTest(unittest.TestCase):
    def test_negative_truth_scores_on_the_natural_scale_only(self) -> None:
        q = [(lvl, 100.0 * lvl) for lvl in spec.QUANTILE_LEVELS]
        neg = score.target_scores(q, -269.0, u=0.5)
        self.assertIsNotNone(neg["wis"])
        self.assertIsNone(neg["wis_log"])
        pos = score.target_scores(q, 40.0, u=0.5)
        self.assertIsNotNone(pos["wis_log"])
        self.assertEqual(set(pos["coverage"]), {"50", "80", "90", "95"})

    def test_pin_scores(self) -> None:
        got = score.pin_scores(0.8, 1)
        self.assertAlmostEqual(got["brier"], 0.04)
        self.assertLess(got["log"], 0.0)
        clipped = score.pin_scores(1.0, 0)
        self.assertTrue(clipped["log"] > -10)


if __name__ == "__main__":
    unittest.main()
