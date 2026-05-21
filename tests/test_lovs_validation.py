# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs_validation: scoring primitives, the additive robustness layer,
and a regression test pinning the immutable pre-committed headline scorecard."""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import lovs_validation as V

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
WA_SUBSTRATE = DATA / "west-africa-prefecture-weekly.json"
COV_COUNTRY = DATA / "covariates-wa-2014.json"
COV_DISTRICT = DATA / "covariates-wa-2014-v3.json"


def _nan(x: float) -> bool:
    return x != x


class TestScoringPrimitives(unittest.TestCase):
    def test_roc_auc_separable(self):
        self.assertEqual(V.roc_auc((2, 1, 0, -1), (1, 1, 0, 0)), 1.0)

    def test_roc_auc_reversed(self):
        self.assertEqual(V.roc_auc((-1, 0, 1, 2), (1, 1, 0, 0)), 0.0)

    def test_roc_auc_all_ties(self):
        self.assertEqual(V.roc_auc((1, 1, 1, 1), (1, 0, 1, 0)), 0.5)

    def test_roc_auc_mixed_matches_hand_value(self):
        # [3,1,2,2,0] vs [1,0,1,0,0]: P(pos>neg)+0.5 P(tie) = 11/12.
        self.assertAlmostEqual(V.roc_auc((3, 1, 2, 2, 0), (1, 0, 1, 0, 0)), 11 / 12)

    def test_roc_auc_degenerate_returns_nan(self):
        self.assertTrue(_nan(V.roc_auc((1, 2, 3), (1, 1, 1))))  # all positive
        self.assertTrue(_nan(V.roc_auc((1, 2, 3), (0, 0, 0))))  # all negative
        self.assertTrue(_nan(V.roc_auc((), ())))  # empty

    def test_brier_skill_score_climatology_is_zero(self):
        outs = (1, 0, 0, 1, 0, 0, 1, 0)
        pbar = sum(outs) / len(outs)
        self.assertAlmostEqual(V.brier_skill_score((pbar,) * len(outs), outs), 0.0)

    def test_brier_skill_score_perfect_is_one(self):
        outs = (1, 0, 1, 0)
        self.assertAlmostEqual(V.brier_skill_score(tuple(float(o) for o in outs), outs), 1.0)

    def test_brier_skill_score_no_variation_is_nan(self):
        self.assertTrue(_nan(V.brier_skill_score((0.5, 0.5), (1, 1))))

class TestForecastRecords(unittest.TestCase):
    def setUp(self):
        substrate = V._load_wa_substrate(WA_SUBSTRATE)
        self.graph, self.first_active, self.weekly = V._prepare_substrate(substrate)

    def _records(self, label="no-context", weeks=(3, 5, 7, 9, 11)):
        return V._forecast_records(
            graph=self.graph,
            first_active=self.first_active,
            weekly_by_id=self.weekly,
            as_of_weeks=weeks,
            horizon_weeks=4,
            proximity_threshold_km=400.0,
            edge_weight_fn=lambda s, t: 1.0,
            config_label=label,
            n_samples=500,
        )

    def test_sparse_no_context_matches_headline_n(self):
        recs = self._records()
        self.assertEqual(len(recs), 246)  # equals the headline no-context N
        self.assertEqual(sum(r.outcome for r in recs), 15)

    def test_per_instance_seeding_is_deterministic(self):
        a = self._records()
        b = self._records()
        self.assertEqual(
            [round(r.predicted_prob, 9) for r in a],
            [round(r.predicted_prob, 9) for r in b],
        )

    def test_records_carry_baseline_signals(self):
        recs = self._records()
        # Every record exposes the distance-only and load-only baseline signals.
        self.assertTrue(all(r.nearest_active_km >= 0 for r in recs))
        self.assertTrue(all(r.source_load >= 0 for r in recs))
        self.assertTrue(all(0.0 <= r.predicted_prob <= 1.0 for r in recs))


class TestClusterBootstrap(unittest.TestCase):
    def setUp(self):
        substrate = V._load_wa_substrate(WA_SUBSTRATE)
        graph, first_active, weekly = V._prepare_substrate(substrate)
        # Dense window so the as-of-week block bootstrap has enough clusters.
        self.records = V._forecast_records(
            graph=graph, first_active=first_active, weekly_by_id=weekly,
            as_of_weeks=tuple(range(3, 31)), horizon_weeks=4,
            proximity_threshold_km=400.0, edge_weight_fn=lambda s, t: 1.0,
            config_label="no-context", n_samples=500,
        )

    def test_bootstrap_is_deterministic(self):
        m = {"bss": V._records_bss, "auc": V._records_auc_model}
        a = V.cluster_bootstrap_cis(self.records, m, n_boot=200, seed=7)
        b = V.cluster_bootstrap_cis(self.records, m, n_boot=200, seed=7)
        self.assertEqual(a, b)

    def test_cluster_key_is_configurable(self):
        # Default cluster is the target prefecture; week-block is the alternative.
        # Both are deterministic and yield a valid band.
        target_ci = V.cluster_bootstrap_cis(
            self.records, {"auc": V._records_auc_model}, n_boot=200, seed=7
        )["auc"]
        week_ci = V.cluster_bootstrap_cis(
            self.records, {"auc": V._records_auc_model}, n_boot=200, seed=7,
            cluster_key=lambda r: r.week,
        )["auc"]
        self.assertLessEqual(target_ci[0], target_ci[1])
        self.assertLessEqual(week_ci[0], week_ci[1])

    def test_ci_brackets_point_estimate(self):
        ci = V.cluster_bootstrap_cis(
            self.records, {"auc": V._records_auc_model}, n_boot=400, seed=7
        )["auc"]
        point = V._records_auc_model(self.records)
        self.assertLessEqual(ci[0], ci[1])
        self.assertLessEqual(ci[0], point + 1e-9)
        self.assertLessEqual(point - 1e-9, ci[1])

    def test_empty_records_returns_nan_ci(self):
        ci = V.cluster_bootstrap_cis([], {"auc": V._records_auc_model}, n_boot=10, seed=1)
        self.assertTrue(_nan(ci["auc"][0]) and _nan(ci["auc"][1]))


class TestRollingOriginRobustness(unittest.TestCase):
    CONFIGS = (("no-context", None), ("country", COV_COUNTRY), ("district", COV_DISTRICT))

    def _report(self):
        return V.rolling_origin_robustness(WA_SUBSTRATE, self.CONFIGS, bootstrap_iters=100)

    def test_full_grid_shape(self):
        rep = self._report()
        self.assertEqual(len(rep.cells), 3 * len(V.PREREGISTERED_WINDOWS))
        self.assertEqual(rep.config_labels, ("no-context", "country", "district"))

    def test_discrimination_is_spatial_proximity_no_calibration_skill(self):
        # Honest finding: AUC point above chance, but no lift over the trivial
        # distance/load baselines, and no calibration skill (BSS CI spans 0).
        rep = self._report()
        cell = next(
            c for c in rep.cells
            if c.config_label == "no-context" and c.window_label.startswith("sparse")
        )
        self.assertGreater(cell.auc_model, 0.55)  # point estimate above chance
        self.assertLessEqual(cell.auc_model_ci[0], cell.auc_model)  # CI band below point
        # No meaningful lift over distance-only or source-load-only ranking.
        self.assertLess(abs(cell.auc_model - cell.auc_distance_only), 0.1)
        self.assertLess(abs(cell.auc_model - cell.auc_source_load_only), 0.1)
        # No calibration skill: BSS at/below zero and its CI reaches below zero.
        self.assertLessEqual(cell.brier_skill_score, 0.1)
        self.assertLess(cell.brier_skill_score_ci[0], 0.0)

    def test_to_json_round_trips_and_is_clean(self):
        rep = self._report()
        payload = V.robustness_to_json(rep)
        text = json.dumps(payload)
        self.assertNotIn("NaN", text)  # NaN mapped to null
        reloaded = json.loads(text)
        self.assertEqual(len(reloaded["cells"]), len(rep.cells))
        self.assertEqual(reloaded["bootstrap_seed"], V.ROBUSTNESS_BOOTSTRAP_SEED)

    def test_report_is_deterministic(self):
        a = json.dumps(V.robustness_to_json(self._report()))
        b = json.dumps(V.robustness_to_json(self._report()))
        self.assertEqual(a, b)


class TestSyntheticSubstrate(unittest.TestCase):
    """Failure-injection: a tiny controlled substrate exercises the loop edges."""

    SUBSTRATE = {
        "prefectures": [
            {"prefecture": "A", "lat": 0.0, "lon": 0.0, "weekly_counts": [5, 5, 5, 5, 5, 5]},
            {"prefecture": "B", "lat": 0.5, "lon": 0.5, "weekly_counts": [0, 0, 0, 1, 2, 3]},
            {"prefecture": "Z", "lat": 40.0, "lon": 40.0, "weekly_counts": [0, 0, 0, 0, 0, 0]},
        ]
    }

    def test_isolated_prefecture_is_skipped(self):
        graph, first_active, weekly = V._prepare_substrate(self.SUBSTRATE)
        recs = V._forecast_records(
            graph=graph, first_active=first_active, weekly_by_id=weekly,
            as_of_weeks=(2,), horizon_weeks=4, proximity_threshold_km=400.0,
            edge_weight_fn=lambda s, t: 1.0, config_label="syn", n_samples=50,
        )
        targets = {r.target_id for r in recs}
        # B is near active A and gets a forecast; far-away never-active Z does not.
        self.assertIn("B", targets)
        self.assertNotIn("Z", targets)

    def test_right_censoring_skips_unobservable_weeks(self):
        graph, first_active, weekly = V._prepare_substrate(self.SUBSTRATE)
        # Substrate is 6 weeks; as-of week 4 + horizon 4 = 8 > 6 -> unobservable.
        recs = V._forecast_records(
            graph=graph, first_active=first_active, weekly_by_id=weekly,
            as_of_weeks=(4,), horizon_weeks=4, proximity_threshold_km=400.0,
            edge_weight_fn=lambda s, t: 1.0, config_label="syn", n_samples=50,
        )
        self.assertEqual(recs, [])


class TestHeadlineImmutable(unittest.TestCase):
    """Regression: the pre-committed 20 May headline scorecard must not move."""

    def test_no_context_headline_pinned(self):
        r = V.mode_a_backtest_wa_2014(WA_SUBSTRATE)
        self.assertEqual(round(r.next_zone_brier, 4), 0.0586)
        self.assertEqual(round(r.next_zone_wis, 4), 0.1002)
        self.assertEqual(round(r.expected_calibration_error, 4), 0.0391)

    def test_country_context_headline_pinned(self):
        r = V.mode_a_backtest_wa_2014_t3(WA_SUBSTRATE, COV_COUNTRY)
        self.assertEqual(round(r.next_zone_brier, 4), 0.0590)
        self.assertEqual(round(r.next_zone_wis, 4), 0.0649)
        self.assertEqual(round(r.expected_calibration_error, 4), 0.0500)

    def test_district_context_headline_pinned(self):
        r = V.mode_a_backtest_wa_2014_t3(WA_SUBSTRATE, COV_DISTRICT)
        self.assertEqual(round(r.next_zone_brier, 4), 0.0590)
        self.assertEqual(round(r.next_zone_wis, 4), 0.0649)
        self.assertEqual(round(r.expected_calibration_error, 4), 0.0500)


if __name__ == "__main__":
    unittest.main()
