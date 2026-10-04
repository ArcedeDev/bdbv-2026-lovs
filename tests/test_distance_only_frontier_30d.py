# SPDX-License-Identifier: Apache-2.0
"""Tests for the distance-only frontier rank.

The model is a floor, so its failure modes are the quiet ones: reading a table from
after the cutoff, ranking against a stale frontier, or breaking ties in a way that
smuggles in an order nobody pre-registered.
"""
from __future__ import annotations

import math
import unittest

from lovs import distance_only_frontier_30d as dr
from lovs.health_zone_centroids import haversine_km

# A small synthetic map on the equator: one degree of longitude is about 111 km.
POINTS = {
    "old": (0.0, 20.0),
    "hot": (0.0, 25.0),
    "near": (0.0, 25.5),
    "mid": (0.0, 27.0),
    "far": (0.0, 30.0),
    "west-twin": (0.0, 24.0),
    "east-twin": (0.0, 26.0),
}


def _tables():
    return [
        {"date": "2026-08-01", "counts": {"old": 5}},
        {"date": "2026-08-25", "counts": {"old": 9, "hot": 1}},
        {"date": "2026-09-20", "counts": {"old": 9, "hot": 4}},
    ]


class TestFrontier(unittest.TestCase):
    def test_only_zones_that_rose_inside_the_window_are_frontier(self):
        # Cutoff 2026-09-24: window opens 2026-08-25; baseline is the 2026-08-01 table.
        front = dr.frontier(_tables(), cutoff="2026-09-24")
        self.assertEqual(["hot", "old"], front["zones"])
        self.assertEqual("2026-08-01", front["baseline_observation"])
        # Cutoff 2026-09-30: window opens 2026-08-31; baseline is 2026-08-25, when old was
        # already 9, so only hot rose.
        front = dr.frontier(_tables(), cutoff="2026-09-30")
        self.assertEqual(["hot"], front["zones"])
        self.assertEqual("rose_in_30_days_before_cutoff", front["basis"])

    def test_without_an_earlier_table_every_zone_counts_from_zero(self):
        front = dr.frontier(_tables()[2:], cutoff="2026-09-21")
        self.assertEqual(["hot", "old"], front["zones"])
        self.assertIsNone(front["baseline_observation"])

    def test_quiescent_outbreak_falls_back_to_the_footprint_and_says_so(self):
        flat = [
            {"date": "2026-08-01", "counts": {"old": 9, "hot": 4}},
            {"date": "2026-09-20", "counts": {"old": 9, "hot": 4}},
        ]
        front = dr.frontier(flat, cutoff="2026-09-30")
        self.assertEqual(["hot", "old"], front["zones"])
        self.assertEqual("cumulative_footprint_fallback", front["basis"])


class TestScore(unittest.TestCase):
    def test_score_is_negative_km_to_the_nearest_frontier_zone(self):
        out = dr.predict(["near", "mid", "far"], _tables(), cutoff="2026-09-30", points=POINTS)
        expected = round(haversine_km(0.0, 25.5, 0.0, 25.0), 3)
        self.assertEqual(-expected, out["predictions"]["near"])
        self.assertEqual("hot", out["nearest_frontier_zone"]["far"])
        self.assertGreater(out["predictions"]["near"], out["predictions"]["mid"])
        self.assertGreater(out["predictions"]["mid"], out["predictions"]["far"])
        self.assertTrue(all(math.isfinite(v) and v <= 0 for v in out["predictions"].values()))

    def test_exact_ties_stay_tied_and_attribution_is_first_by_zone_id(self):
        tables = [{"date": "2026-09-20", "counts": {"west-twin": 1, "east-twin": 1}}]
        out = dr.predict(["hot", "far"], tables, cutoff="2026-09-30", points=POINTS)
        # hot sits exactly halfway between the twins.
        self.assertEqual("east-twin", out["nearest_frontier_zone"]["hot"])
        tied = dr.predict(["near", "mid"], [{"date": "2026-09-20", "counts": {"hot": 1}}],
                          cutoff="2026-09-30", points={**POINTS, "mid": (0.0, 24.5)})
        self.assertEqual(tied["predictions"]["near"], tied["predictions"]["mid"])

    def test_a_target_without_a_point_is_refused(self):
        with self.assertRaises(dr.DistanceRankError):
            dr.predict(["unmapped"], _tables(), cutoff="2026-09-30", points=POINTS)

    def test_deterministic_regardless_of_input_order(self):
        a = dr.predict(["far", "near", "mid"], _tables(), cutoff="2026-09-30", points=POINTS)
        b = dr.predict(["mid", "far", "near"], list(reversed(_tables())), cutoff="2026-09-30", points=POINTS)
        self.assertEqual(a, b)


class TestLookAhead(unittest.TestCase):
    def test_observation_on_the_cutoff_is_a_leak(self):
        with self.assertRaises(dr.DistanceRankError) as ctx:
            dr.predict(["near"], _tables(), cutoff="2026-09-20", points=POINTS)
        self.assertIn("look-ahead", str(ctx.exception))

    def test_two_tables_for_one_day_are_refused(self):
        tables = _tables() + [{"date": "2026-09-20", "counts": {"hot": 5}}]
        with self.assertRaises(dr.DistanceRankError):
            dr.predict(["near"], tables, cutoff="2026-09-30", points=POINTS)

    def test_no_targets_is_refused(self):
        with self.assertRaises(dr.DistanceRankError):
            dr.predict([], _tables(), cutoff="2026-09-30", points=POINTS)


if __name__ == "__main__":
    unittest.main()
