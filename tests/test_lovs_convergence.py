"""Regression lock for lovs_convergence.build_convergence.

The original convergence generator was lost with an ephemeral working tree (see
specs/2026-06-09-june7-snapshot-delta-audit.md). These tests pin the rebuilt module to
the published 2026-06-06 snapshot byte-for-byte and assert the 2026-06-07 recompute, so
a future lineage cannot silently drop or drift the convergence block again.
"""

import unittest

from lovs import lovs_convergence

# Mirrors refresh_pipeline.build_methodology_constants() for the fields the module reads.
METHODOLOGY_CONSTANTS = {
    "cfr": {"low_95": 0.26, "central": 0.33, "high_95": 0.4},
    "onset_to_death_gamma": {"alpha": 4.42, "beta_per_day": 0.388, "mean_days": 11.37},
    "central_doubling_time_days": 7.0,
    "observed_doubling_times_days": [5.0, 7.0, 11.0],
}


class TestConvergenceReproducesJune6(unittest.TestCase):
    def setUp(self):
        # The published 2026-06-06 inputs (SitRep 23): confirmed 534, deaths 93,
        # contacts under follow-up 5040, follow-up coverage 50.3%.
        self.block = lovs_convergence.build_convergence(
            as_of="2026-06-06",
            confirmed=534,
            confirmed_deaths=93,
            contacts_under_follow_up=5040,
            followup_coverage_pct=50.3,
            methodology_constants=METHODOLOGY_CONSTANTS,
        )

    def test_estimated_total_cases(self):
        cases = self.block["true_burden_nowcast"]["estimated_total_cases"]
        self.assertEqual(cases["central"], 770)
        self.assertEqual(cases["low"], 635)
        self.assertEqual(cases["high"], 977)
        self.assertEqual(cases["provenance"], "external")

    def test_ascertainment_and_unreported(self):
        gap = self.block["true_burden_nowcast"]["ascertainment_gap"]
        self.assertEqual(gap["case_ascertainment"], 0.6935)
        self.assertEqual(gap["confirmed_vs_estimated_total_cases"], [534, 770])
        self.assertEqual(gap["estimated_unreported_cases"], 236)

    def test_estimated_total_deaths(self):
        deaths = self.block["true_burden_nowcast"]["estimated_total_deaths"]
        self.assertEqual([deaths["low"], deaths["central"], deaths["high"]], [98, 113, 134])
        self.assertEqual(deaths["death_ascertainment_band"], [0.696, 0.95])

    def test_transmission_floor(self):
        tf = self.block["transmission_floor"]
        self.assertEqual(tf["new_cases_from_roster"], {"low": 151, "spine": 186, "high": 454})
        self.assertEqual(tf["implied_cumulative_floor"], 685)
        self.assertEqual(tf["coverage_panel"], {"followup_rate_pct": 50.3, "unobserved_pct": 49.7})

    def test_methodology_shape(self):
        methodology = self.block["methodology"]
        self.assertEqual(len(methodology), 5)
        self.assertEqual(methodology[0]["provenance"], "external")
        self.assertTrue(all(m.get("equation") and m.get("sources") for m in methodology))
        # The worked central must literally reproduce the published string.
        self.assertEqual(
            methodology[0]["worked_central"],
            "(93/0.33) * (1 + (ln2/7)/0.388)^4.42 = 770",
        )


class TestConvergenceJune7(unittest.TestCase):
    def setUp(self):
        # 2026-06-07 (SitRep 24): confirmed 569, deaths 103, contacts 5418, coverage 64.4%.
        self.block = lovs_convergence.build_convergence(
            as_of="2026-06-07",
            confirmed=569,
            confirmed_deaths=103,
            contacts_under_follow_up=5418,
            followup_coverage_pct=64.4,
            methodology_constants=METHODOLOGY_CONSTANTS,
        )

    def test_june7_values(self):
        nc = self.block["true_burden_nowcast"]
        cases = nc["estimated_total_cases"]
        self.assertEqual([cases["low"], cases["central"], cases["high"]], [703, 852, 1082])
        self.assertEqual(nc["ascertainment_gap"]["estimated_unreported_cases"], 283)
        self.assertEqual(nc["estimated_total_deaths"]["central"], 125)
        self.assertEqual(
            [nc["estimated_total_deaths"]["low"], nc["estimated_total_deaths"]["high"]],
            [108, 148],
        )
        tf = self.block["transmission_floor"]
        self.assertEqual(tf["new_cases_from_roster"], {"low": 163, "spine": 200, "high": 488})
        self.assertEqual(tf["implied_cumulative_floor"], 732)
        self.assertEqual(tf["coverage_panel"]["unobserved_pct"], 35.6)


if __name__ == "__main__":
    unittest.main()
