# SPDX-License-Identifier: Apache-2.0
"""Tests for the visibility reporting-completeness calibration backtest."""
from __future__ import annotations

import random
import unittest

from lovs import lovs_visibility as vis
from lovs import lovs_visibility_backtest as bt


class TestVisibilityBacktestUnits(unittest.TestCase):
    def test_cdf_table_is_monotone_in_unit_interval(self):
        table = bt._cdf_table(*vis.ROSELLO_BDBV_DELAY_GAMMA, max_lag=12)
        self.assertEqual(len(table), 13)
        for earlier, later in zip(table, table[1:]):
            self.assertLessEqual(earlier, later)
        for value in table:
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_completeness_matches_hand_convolution(self):
        counts = [0] * 5 + [10, 10, 10, 10, 10]
        table = bt._cdf_table(1.0, 1.0 / 7.0, max_lag=len(counts) - 1)
        week = len(counts) - 1
        num = sum(counts[t] * table[week - t] for t in range(week + 1))
        den = sum(counts[: week + 1])
        self.assertAlmostEqual(bt._completeness(counts, week, table), num / den, places=9)

    def test_completeness_none_below_min_cases(self):
        counts = [1, 1, 1]  # cumulative 3 < MIN_CUMULATIVE_CASES
        table = bt._cdf_table(1.0, 0.1, max_lag=2)
        self.assertIsNone(bt._completeness(counts, 2, table))

    def test_completeness_rises_with_elapsed_time(self):
        counts = [50] + [0] * 10  # a single cohort at week 0
        table = bt._cdf_table(*vis.ROSELLO_BDBV_DELAY_GAMMA, max_lag=10)
        early = bt._completeness(counts, 1, table)
        late = bt._completeness(counts, 9, table)
        self.assertLess(early, late)  # more reported as time passes


class TestVisibilityBacktestAnchor(unittest.TestCase):
    def test_anchor_prefers_rosello_over_camacho_on_real_field_delays(self):
        anchor = bt._run_anchor(random.Random(1))
        by = anchor["by_candidate"]
        self.assertLess(by["rosello"]["crps_days"], by["camacho"]["crps_days"])
        self.assertEqual(anchor["best_fit_candidate"], "rosello")
        self.assertIn(21.0, anchor["scored_points_days"])  # onset->confirmation present
        # The 4-day sample-to-result lab turnaround is a different delay segment
        # and must never be folded into the onset-to-notification anchor.
        self.assertNotIn(4.0, anchor["scored_points_days"])


class TestVisibilityBacktestEndToEnd(unittest.TestCase):
    def test_deterministic_and_merge_verdict(self):
        original = bt.N_MODEL_SAMPLES
        bt.N_MODEL_SAMPLES = 48  # keep the end-to-end run fast
        try:
            first = bt.run_backtest()
            second = bt.run_backtest()
        finally:
            bt.N_MODEL_SAMPLES = original

        self.assertEqual(first, second, "backtest must be deterministic at a fixed seed")

        # SBC soundness: interval coverage stays near nominal even at reduced N
        # (loose bounds tolerate the smaller Monte Carlo sample in this fast run).
        for model in ("rosello", "camacho", "pooled"):
            sbc = first["sbc"]["by_model"][model]
            self.assertGreaterEqual(sbc["coverage_50"], 0.35)
            self.assertLessEqual(sbc["coverage_50"], 0.65)
            self.assertGreaterEqual(sbc["coverage_95"], 0.85)

        sweep = first["misspecification_sweep"]["by_model"]
        for model in ("rosello", "camacho", "pooled"):
            self.assertIn(model, sweep)
            self.assertIsNotNone(sweep[model]["worst_case_interval_score_50"])

        # Camacho (fast, cross-species) must be the worst single under the
        # field-informed truth grid; merging in Camacho must not be optimal.
        self.assertGreater(
            sweep["camacho"]["worst_case_interval_score_50"],
            sweep["rosello"]["worst_case_interval_score_50"],
        )
        self.assertGreaterEqual(first["stacking"]["optimal_weight_worst_case_is50"], 0.5)
        self.assertIn("interpretation", first)


if __name__ == "__main__":
    unittest.main()
