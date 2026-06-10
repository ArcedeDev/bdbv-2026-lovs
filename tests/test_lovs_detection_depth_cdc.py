"""Tests for lovs/lovs_detection_depth_cdc.py.

The CDC MMWR mm7522e1 time-based detection-depth derivation: an INDEPENDENT,
time-based estimate of the silent phase before detection and the total
transmission elapsed since the modeled spillover. Distinct from the LOVS
branching-process posterior (count-based); the two are cited separately and
never conflated.

generations = elapsed days / serial-interval mean. The CDC spillover constant
must stay in lockstep with the MCP fixture (cdc_earlier_start.json); when that
sibling repo is checked out, the cross-repo equality is asserted directly.
"""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import lovs_detection_depth_cdc as cdc
from lovs import lovs_priors_bundibugyo


# Stage-Two BDBV serial-interval mean: gamma(4.0, 0.55) -> 4.0/0.55 ~ 7.27 d.
SI_MEAN = (
    lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO.serial_interval_gamma[0]
    / lovs_priors_bundibugyo.BUNDIBUGYO_PRIORS_STAGE_TWO.serial_interval_gamma[1]
)
AS_OF = "2026-06-07T23:59:59Z"


class TestCdcConstant(unittest.TestCase):
    def test_detection_anchor_date_is_14_may(self):
        self.assertEqual(cdc.CDC_MM7522E1["detection_anchor_date"], "2026-05-14")

    def test_spillover_medians_match_published(self):
        c = cdc.CDC_MM7522E1
        self.assertEqual(c["spillover_median_50_death"], "2026-02-19")
        self.assertEqual(c["spillover_median_100_death"], "2026-02-08")
        self.assertEqual(c["spillover_median_200_death"], "2026-01-29")
        self.assertEqual(c["spillover_interval_earliest"], "2026-01-09")
        self.assertEqual(c["r0"], 2.51)

    def test_constant_matches_mcp_fixture_when_present(self):
        """Cross-repo single-source-of-truth: assert equality against the MCP
        fixture when the sibling repo is checked out (the normal dev case);
        skip gracefully when it is not, so the engine test stays portable."""
        # test file -> tests -> worktree -> .worktrees -> projects (parents[3]).
        fixture = (
            pathlib.Path(__file__).resolve().parents[3]
            / "lovs-evidence-mcp"
            / "fixtures"
            / "models"
            / "cdc_earlier_start.json"
        )
        if not fixture.exists():
            self.skipTest(f"MCP fixture not checked out at {fixture}")
        data = json.loads(fixture.read_text())
        bp = data["spillover_back_projection"]
        self.assertEqual(cdc.CDC_MM7522E1["spillover_median_50_death"], bp["median_50_death_assumption"])
        self.assertEqual(cdc.CDC_MM7522E1["spillover_median_100_death"], bp["median_100_death_assumption"])
        self.assertEqual(cdc.CDC_MM7522E1["spillover_median_200_death"], bp["median_200_death_assumption"])
        self.assertEqual(cdc.CDC_MM7522E1["spillover_interval_earliest"], bp["interval_earliest"])
        self.assertEqual(cdc.CDC_MM7522E1["r0"], data["r0"])
        self.assertEqual(cdc.CDC_MM7522E1["detection_anchor_date"], data["detection_anchor_date"])


class TestCdcDerivation(unittest.TestCase):
    def setUp(self) -> None:
        self.out = cdc.compute_cdc_detection_depth(SI_MEAN, AS_OF)

    def test_serial_interval_threaded_through(self):
        self.assertAlmostEqual(self.out["serial_interval_mean_days"], round(SI_MEAN, 2), places=2)

    def test_silent_duration_days_band(self):
        # latest median spillover (19 Feb) -> 14 May = 84 d; earliest median (29 Jan) = 105 d.
        self.assertEqual(self.out["silent_before_detection"]["duration_days"], [84, 105])

    def test_silent_earliest_bound_days(self):
        # 9 Jan -> 14 May = 125 d.
        self.assertEqual(self.out["silent_before_detection"]["earliest_bound_days"], 125)

    def test_silent_generations_band_central(self):
        gens = self.out["silent_before_detection"]["generations"]
        self.assertLess(gens[0], gens[1])
        self.assertGreaterEqual(gens[0], 11.0)
        self.assertLessEqual(gens[1], 15.0)

    def test_elapsed_duration_days_band(self):
        # 19 Feb -> 7 Jun = 108 d; 29 Jan -> 7 Jun = 129 d.
        self.assertEqual(self.out["elapsed_since_spillover"]["duration_days"], [108, 129])

    def test_elapsed_generations_deeper_than_silent(self):
        silent = self.out["silent_before_detection"]["generations"]
        elapsed = self.out["elapsed_since_spillover"]["generations"]
        self.assertGreater(elapsed[0], silent[0])
        self.assertGreater(elapsed[1], silent[1])

    def test_elapsed_carries_as_of(self):
        self.assertEqual(self.out["elapsed_since_spillover"]["as_of"], "2026-06-07")

    def test_months_present_for_duration_primary_presentation(self):
        self.assertIn("duration_months", self.out["silent_before_detection"])
        self.assertIn("duration_months", self.out["elapsed_since_spillover"])

    def test_caveats_include_separate_from_lovs(self):
        joined = " ".join(self.out["caveats"]).lower()
        self.assertIn("lovs", joined)
        self.assertIn("separate", joined)

    def test_does_not_move_detection_anchor(self):
        joined = " ".join(self.out["caveats"]).lower()
        self.assertIn("14 may", joined)


if __name__ == "__main__":
    unittest.main()
