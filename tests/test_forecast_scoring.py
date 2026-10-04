# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import unittest

from lovs import forecast_scoring as S


def _nan(value: float) -> bool:
    return value != value


class ForecastScoringTests(unittest.TestCase):
    def test_brier_score_validates_probability_and_outcome(self):
        self.assertEqual(S.brier_score(0.25, 1), 0.5625)
        with self.assertRaises(ValueError):
            S.brier_score(1.2, 1)
        with self.assertRaises(ValueError):
            S.brier_score(0.2, 2)

    def test_mean_brier_empty_is_nan(self):
        self.assertTrue(_nan(S.mean_brier_score((), ())))

    def test_brier_skill_score_matches_climatology(self):
        outcomes = (1, 0, 0, 1)
        self.assertEqual(S.brier_skill_score((0.5, 0.5, 0.5, 0.5), outcomes), 0.0)
        self.assertEqual(S.brier_skill_score((1.0, 0.0, 0.0, 1.0), outcomes), 1.0)

    def test_brier_skill_score_no_variation_is_nan(self):
        self.assertTrue(_nan(S.brier_skill_score((0.5, 0.6), (0, 0))))

    def test_roc_auc_tie_corrected(self):
        self.assertEqual(S.roc_auc((1, 1, 1, 1), (1, 0, 1, 0)), 0.5)
        self.assertEqual(S.roc_auc((2, 1, 0, -1), (1, 1, 0, 0)), 1.0)
        self.assertEqual(S.roc_auc((-1, 0, 1, 2), (1, 1, 0, 0)), 0.0)

    def test_roc_auc_no_variation_is_nan(self):
        self.assertTrue(_nan(S.roc_auc((0.1, 0.2), (1, 1))))

    def test_calibration_bins_and_ece_are_shared_primitives(self):
        bins = S.calibration_bins((0.1, 0.2, 0.8), (0, 0, 1), n_bins=2)
        self.assertEqual(len(bins), 2)
        self.assertEqual(bins[0]["count"], 2)
        self.assertAlmostEqual(bins[0]["predicted_mean"], 0.15)
        self.assertAlmostEqual(
            S.expected_calibration_error((0.1, 0.2, 0.8), (0, 0, 1), n_bins=2),
            0.1666666667,
        )

    def test_calibration_primitives_validate_inputs(self):
        with self.assertRaises(ValueError):
            S.calibration_bins((0.1,), (0, 1), n_bins=2)
        with self.assertRaises(ValueError):
            S.expected_calibration_error((1.2,), (1,), n_bins=2)
        with self.assertRaises(ValueError):
            S.calibration_bins((0.1,), (0,), n_bins=0)

    def test_empty_calibration_error_is_undefined(self):
        self.assertTrue(_nan(S.expected_calibration_error((), ())))



class SignFlipTests(unittest.TestCase):
    def test_four_units_all_negative_hit_the_exact_floor(self):
        got = S.sign_flip_p_value((-0.02, -0.01, -0.03, -0.04))
        self.assertEqual(got["p_value"], 2 / 16)
        self.assertEqual(got["min_attainable_p"], 0.125)
        one_sided = S.sign_flip_p_value((-0.02, -0.01, -0.03, -0.04), alternative="less")
        self.assertEqual(one_sided["p_value"], 1 / 16)

    def test_a_known_mixed_case(self):
        # Means of the 8 sign patterns of (1, 2, -3): only the observed pattern and its
        # mirror reach |mean| = 0, so every pattern is at least as extreme: p = 1.
        self.assertEqual(S.sign_flip_p_value((1.0, 2.0, -3.0))["p_value"], 1.0)
        # (-1, -2, -3): |mean| 2 is reached only by all-negative and all-positive.
        self.assertEqual(S.sign_flip_p_value((-1.0, -2.0, -3.0))["p_value"], 0.25)

    def test_all_zero_differences_carry_no_evidence(self):
        got = S.sign_flip_p_value((0.0, 0.0, 0.0))
        self.assertEqual(got["p_value"], 1.0)
        self.assertEqual(got["min_attainable_p"], 1.0)

    def test_symmetric_under_negation_two_sided(self):
        d = (0.01, -0.03, 0.02, -0.05, 0.004)
        self.assertEqual(S.sign_flip_p_value(d)["p_value"],
                         S.sign_flip_p_value(tuple(-x for x in d))["p_value"])

    def test_nine_units_floor(self):
        self.assertEqual(S.sign_flip_p_value(tuple(-0.01 * (i + 1) for i in range(9)))["min_attainable_p"], 2 / 512)

    def test_refuses_what_it_cannot_do(self):
        for bad in ((), (float("nan"),), tuple(range(21))):
            with self.assertRaises(ValueError):
                S.sign_flip_p_value(bad)
        with self.assertRaises(ValueError):
            S.sign_flip_p_value((1.0,), alternative="greater")


if __name__ == "__main__":
    unittest.main()
