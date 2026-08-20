# SPDX-License-Identifier: Apache-2.0
"""Tests for the tournament base-rate benchmark.

benchmark.base_rate_30d is the bar every other competitor must clear, so its failure
modes are asymmetric: a benchmark that reads too high hands every model an inflated
bar and manufactures skill that is not there.
"""
from __future__ import annotations

import unittest

from lovs import base_rate_30d as br


def _series():
    """Three reviewed observation days. Two zones are present from the start."""
    return [
        {"date": "2026-06-02", "zones": ["bunia", "aru"]},
        {"date": "2026-06-12", "zones": ["bunia", "aru", "mahagi"]},
        {"date": "2026-06-22", "zones": ["bunia", "aru", "mahagi", "adja"]},
    ]


class TestLeftCensoring(unittest.TestCase):
    """Zones already affected when the series opens are prevalent, not incident."""

    def test_baseline_zones_are_not_counted_as_events(self):
        est = br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-07-01")
        # bunia and aru are present on day one; only mahagi and adja converted.
        self.assertEqual(est["prevalent_at_baseline"], 2)
        self.assertEqual(est["events"], 2)

    def test_prevalent_zones_still_reduce_the_at_risk_pool(self):
        small = br.estimate_base_rate(_series(), target_universe_size=10, cutoff="2026-07-01")
        large = br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-07-01")
        # Same events, smaller pool -> higher per-zone rate.
        self.assertEqual(small["events"], large["events"])
        self.assertGreater(small["probability"], large["probability"])

    def test_a_fully_prevalent_series_yields_no_events(self):
        """If nothing ever converts, the benchmark must say so rather than invent events."""
        flat = [
            {"date": "2026-06-02", "zones": ["bunia", "aru"]},
            {"date": "2026-06-12", "zones": ["bunia", "aru"]},
        ]
        est = br.estimate_base_rate(flat, target_universe_size=50, cutoff="2026-07-01")
        self.assertEqual(est["events"], 0)
        # ... and must not return exactly zero, which is unbounded under log loss.
        self.assertGreater(est["probability"], 0.0)


class TestLookAheadGuard(unittest.TestCase):
    """The benchmark may only read what was knowable at freeze time."""

    def test_observation_on_the_cutoff_is_a_leak(self):
        with self.assertRaises(br.BaseRateError) as ctx:
            br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-06-22")
        self.assertIn("look-ahead", str(ctx.exception))

    def test_observation_after_the_cutoff_is_a_leak(self):
        with self.assertRaises(br.BaseRateError):
            br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-06-10")

    def test_leaks_raise_rather_than_being_filtered_away(self):
        """Silently dropping the leak would still shift the denominator."""
        with self.assertRaises(br.BaseRateError):
            br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-06-15")


class TestDeclaredPolicies(unittest.TestCase):

    def test_smoothing_and_denominator_basis_are_declared_in_the_output(self):
        est = br.estimate_base_rate(_series(), target_universe_size=50, cutoff="2026-07-01")
        self.assertEqual(est["smoothing"], "jeffreys_beta_0.5_0.5")
        self.assertEqual(est["lookback"], "all_pre_cutoff_history")
        self.assertEqual(est["denominator_basis"], "round_frozen_target_universe")
        self.assertEqual(est["window_days"], 30)
        self.assertIs(est["is_observed"], False)

    def test_universe_must_contain_its_own_history(self):
        with self.assertRaises(br.BaseRateError):
            br.estimate_base_rate(_series(), target_universe_size=2, cutoff="2026-07-01")

    def test_probability_is_bounded(self):
        est = br.estimate_base_rate(_series(), target_universe_size=5, cutoff="2026-07-01")
        self.assertGreaterEqual(est["probability"], 0.0)
        self.assertLessEqual(est["probability"], 1.0)


class TestPredictIsMemoryless(unittest.TestCase):

    def test_every_target_gets_the_identical_probability(self):
        """Uniformity is the point: the benchmark carries no spatial information, so a
        competitor that beats it has demonstrated that its geography predicts something."""
        out = br.predict(
            ["kisangani", "goma", "beni"], _series(),
            target_universe_size=50, cutoff="2026-07-01",
        )
        values = set(out["predictions"].values())
        self.assertEqual(len(values), 1)
        self.assertEqual(out["predictions"]["goma"], out["probability"])

    def test_an_empty_target_set_raises(self):
        with self.assertRaises(br.BaseRateError):
            br.predict([], _series(), target_universe_size=50, cutoff="2026-07-01")


if __name__ == "__main__":
    unittest.main()
