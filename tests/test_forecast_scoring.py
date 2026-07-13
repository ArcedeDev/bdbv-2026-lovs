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


if __name__ == "__main__":
    unittest.main()
