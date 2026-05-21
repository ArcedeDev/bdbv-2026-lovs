"""Smoke tests for the Phase 2 external-method modules.

Validates lovs.lovs_onset_to_death, lovs.lovs_death_back_projection,
lovs.lovs_export_back_projection, and lovs.lovs_poe_corridor against
formula-level anchors and a synthetic PoE-count fixture. The public repo
does not redistribute restricted third-party PoE table data.

Run: python -m unittest tests/test_imperial_adoption.py
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest

from lovs import lovs_death_back_projection as dbp
from lovs import lovs_export_back_projection as ebp
from lovs import lovs_onset_to_death as otd
from lovs import lovs_poe_corridor as poe


class TestOnsetToDeath(unittest.TestCase):
    def test_mean_matches_rosello_isiro_fit(self):
        self.assertAlmostEqual(otd.bdbv_onset_to_death_mean(), 11.37, delta=0.01)

    def test_variance_alpha_over_beta_squared(self):
        expected_var = 4.42 / (0.388 ** 2)
        self.assertAlmostEqual(
            otd.bdbv_onset_to_death_variance(), expected_var, delta=0.1
        )

    def test_cdf_at_mean_is_in_central_range(self):
        # For gamma(alpha=4.42), CDF at mean is somewhat above 0.5 because of
        # the right-skewed tail; expect [0.4, 0.7].
        cdf_at_mean = otd.bdbv_onset_to_death_cdf(11.37)
        self.assertGreater(cdf_at_mean, 0.4)
        self.assertLess(cdf_at_mean, 0.7)

    def test_survival_complements_cdf(self):
        for t in (5.0, 11.37, 20.0):
            cdf = otd.bdbv_onset_to_death_cdf(t)
            surv = otd.bdbv_onset_to_death_survival(t)
            self.assertAlmostEqual(cdf + surv, 1.0, places=6)

    def test_pdf_integrates_to_about_one(self):
        # Crude midpoint rule from 0 to 40 days; should be close to 1.
        total = 0.0
        n = 4000
        dt = 40.0 / n
        for i in range(n):
            t = (i + 0.5) * dt
            total += otd.bdbv_onset_to_death_pdf(t) * dt
        self.assertAlmostEqual(total, 1.0, delta=0.01)


class TestDeathBackProjection(unittest.TestCase):
    # Anchored to the Imperial 20 May 2026 update Table 2 (131 deaths, CFR
    # bands 26/33/40). Our analytical formula reproduces each published cell
    # to within about 1 percent (small alpha/beta rounding); the assertion
    # bands below bracket both our value and Imperial's published value.
    def test_imperial_main_scenario_table_2(self):
        # 20 May Table 2 main scenario: 131 deaths, CFR 33%, doubling 14 d.
        # Imperial 678; ours 675.
        result = dbp.total_cases_from_deaths(
            deaths=131, cfr=0.33, doubling_time_days=14.0
        )
        self.assertGreater(result, 650)
        self.assertLess(result, 705)

    def test_imperial_fast_growth(self):
        # 20 May Table 2 sensitivity 1: doubling 7 d, CFR 33%. Imperial 1092; ours 1084.
        result = dbp.total_cases_from_deaths(
            deaths=131, cfr=0.33, doubling_time_days=7.0
        )
        self.assertGreater(result, 1050)
        self.assertLess(result, 1130)

    def test_imperial_slow_growth(self):
        # 20 May Table 2 sensitivity 2: doubling 21 d, CFR 33%. Imperial 575; ours 569.
        result = dbp.total_cases_from_deaths(
            deaths=131, cfr=0.33, doubling_time_days=21.0
        )
        self.assertGreater(result, 545)
        self.assertLess(result, 600)

    def test_imperial_cfr_low(self):
        # 20 May Table 2 doubling 14, CFR 26% (lower band). Imperial 860; ours 857.
        result = dbp.total_cases_from_deaths(
            deaths=131, cfr=0.26, doubling_time_days=14.0
        )
        self.assertGreater(result, 825)
        self.assertLess(result, 890)

    def test_imperial_cfr_high(self):
        # 20 May Table 2 doubling 14, CFR 40% (upper band). Imperial 559; ours 557.
        result = dbp.total_cases_from_deaths(
            deaths=131, cfr=0.40, doubling_time_days=14.0
        )
        self.assertGreater(result, 535)
        self.assertLess(result, 580)

    def test_central_constants_match_imperial_20may(self):
        # Guard the grounded 20 May correction: central 33%, bands 26/33/40
        # (CDC 55/169 = 32.5% central, Wilson 95% CI [26%, 40%]).
        self.assertEqual(dbp.CENTRAL_CFR, 0.33)
        self.assertEqual(dbp.IMPERIAL_CFR_SCENARIOS, (0.26, 0.33, 0.40))

    def test_current_snapshot_is_positive_int(self):
        # 144 deaths as of 20 May 2026, central CFR 33%.
        result = dbp.total_cases_from_deaths(
            deaths=144, cfr=dbp.CENTRAL_CFR, doubling_time_days=14.0
        )
        self.assertIsInstance(result, int)
        self.assertGreater(result, 500)
        self.assertLess(result, 1500)

    def test_grid_dimensions(self):
        grid = dbp.sensitivity_grid(
            deaths=144,
            cfrs=[0.26, 0.33, 0.40],
            doubling_times=[7.0, 14.0, 21.0],
        )
        self.assertEqual(len(grid), 9)


class TestMarginalizedTotalCases(unittest.TestCase):
    def test_default_prior_is_imperial_three_scenario_uniform(self):
        # Default CFR is CENTRAL_CFR (33%, the grounded 20 May central). At
        # 144 deaths / uniform-on-{7,14,21}d the mean is the arithmetic mean
        # of the three scenarios at CFR 33%: 1192, 742, 626 -> mean ~= 853.3.
        est = dbp.marginalized_total_cases(deaths=144)
        self.assertEqual(est.cfr, dbp.CENTRAL_CFR)
        self.assertEqual(len(est.doubling_times_days), 3)
        self.assertEqual(est.doubling_times_days, (7.0, 14.0, 21.0))
        self.assertEqual(est.per_scenario_cases, (1192, 742, 626))
        self.assertAlmostEqual(sum(est.weights), 1.0, places=6)
        for w in est.weights:
            self.assertAlmostEqual(w, 1.0 / 3.0, places=6)
        self.assertAlmostEqual(est.mean_cases, (1192 + 742 + 626) / 3.0, places=1)

    def test_q25_q75_bracket_central_scenario(self):
        # The central tau_2 = 14d scenario at CFR 33% / 144 deaths is 742.
        # Under uniform-on-three weights, the (25th, 75th) percentiles are
        # the second-smallest (=626) and second-largest (=1192) values.
        est = dbp.marginalized_total_cases(deaths=144)
        self.assertEqual(est.q25_cases, 626.0)
        self.assertEqual(est.q75_cases, 1192.0)

    def test_non_uniform_weights_normalize(self):
        # Heavy weight on the 21-day (slowest) scenario pulls the mean
        # toward the 626 endpoint. Uses the default central CFR (33%).
        est = dbp.marginalized_total_cases(
            deaths=144,
            doubling_times_days=(7.0, 14.0, 21.0),
            weights=(0.1, 0.1, 0.8),
        )
        self.assertAlmostEqual(sum(est.weights), 1.0, places=6)
        expected_mean = 0.1 * 1192 + 0.1 * 742 + 0.8 * 626
        self.assertAlmostEqual(est.mean_cases, expected_mean, places=1)
        # Heavy slow-growth weight pulls below the uniform-prior mean.
        self.assertLess(est.mean_cases, (1192 + 742 + 626) / 3.0)

    def test_weights_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            dbp.marginalized_total_cases(
                deaths=144,
                doubling_times_days=(7.0, 14.0, 21.0),
                weights=(0.5, 0.5),
            )

    def test_negative_weight_raises(self):
        with self.assertRaises(ValueError):
            dbp.marginalized_total_cases(
                deaths=144,
                doubling_times_days=(7.0, 14.0, 21.0),
                weights=(0.5, -0.1, 0.6),
            )

    def test_zero_total_weight_raises(self):
        with self.assertRaises(ValueError):
            dbp.marginalized_total_cases(
                deaths=144,
                doubling_times_days=(7.0, 14.0, 21.0),
                weights=(0.0, 0.0, 0.0),
            )

    def test_empty_doubling_times_raises(self):
        with self.assertRaises(ValueError):
            dbp.marginalized_total_cases(deaths=144, doubling_times_days=())


class TestExportBackProjection(unittest.TestCase):
    def test_imperial_main_scenario_table_1(self):
        # Imperial Table 1 main scenario: 2 exports, Ituri pop 4,392,200,
        # 1,871 daily travelers, 10-day detection window -> point estimate 470.
        result = ebp.total_cases_from_exports(
            exports=2,
            source_population=4_392_200,
            daily_outbound_travelers=1_871,
            mean_detection_window_days=10.0,
        )
        self.assertGreater(result.point_estimate, 446)
        self.assertLess(result.point_estimate, 494)

    def test_imperial_table_1_ci_brackets_published_range(self):
        # Imperial's published 95% CI for the main scenario is (58, 1306).
        # Our Poisson-approx CI should be in the same neighborhood (won't
        # match exactly because of the NB-vs-Poisson approximation).
        result = ebp.total_cases_from_exports(
            exports=2,
            source_population=4_392_200,
            daily_outbound_travelers=1_871,
            mean_detection_window_days=10.0,
        )
        self.assertLess(result.ci_low_95, 200)
        self.assertGreater(result.ci_high_95, 800)


class TestPoeCorridor(unittest.TestCase):
    def _write_synthetic_poe_counts(self, directory: str) -> str:
        path = pathlib.Path(directory) / "poe_counts.json"
        payload = {
            "source": "synthetic test fixture",
            "counts": [
                {"poe": "Mpondwe", "mean_daily_passengers": 100},
                {"poe": "Busunga", "mean_daily_passengers": 200},
                {"poe": "Ntoroko Main", "mean_daily_passengers": 50},
            ],
            "totals": {
                "ituri_plus_nord_kivu_total_daily_passengers": 350,
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_load_returns_seven_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = poe.load_poe_counts(self._write_synthetic_poe_counts(tmp))
            self.assertIn("counts", data)
            self.assertEqual(len(data["counts"]), 3)

    def test_kasese_maps_to_mpondwe_and_busunga(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = poe.poe_entries_for_corridor(
                "kasese", self._write_synthetic_poe_counts(tmp)
            )
            names = sorted(e["poe"] for e in entries)
            self.assertEqual(names, ["Busunga", "Mpondwe"])

    def test_kasese_daily_passengers_sum(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                poe.corridor_daily_passengers(
                    "kasese", self._write_synthetic_poe_counts(tmp)
                ),
                300,
            )

    def test_default_restricted_path_is_not_required_for_public_tests(self):
        # The restricted PoE file is gitignored and absent in the public repo /
        # CI. A developer may place a local, permission-cleared copy at the
        # default path to use the lever; in that case this default-absence guard
        # is moot, so skip rather than fail.
        if os.path.exists(poe.DEFAULT_POE_COUNTS_PATH):
            self.skipTest("local restricted PoE file present; default-absence guard N/A")
        with self.assertRaises(FileNotFoundError):
            poe.load_poe_counts()

    def test_unknown_corridor_raises(self):
        with self.assertRaises(KeyError):
            with tempfile.TemporaryDirectory() as tmp:
                poe.poe_entries_for_corridor(
                    "nonexistent", self._write_synthetic_poe_counts(tmp)
                )


if __name__ == "__main__":
    unittest.main()
