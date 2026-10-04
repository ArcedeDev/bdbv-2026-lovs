"""The committed walk-forward backtest and the recent-window forecaster it selects."""
from __future__ import annotations

import datetime as dt
import random
import unittest

from lovs.forecast import backtest as bt
from lovs.forecast import opsforecast as of


def _obs(values, start=dt.date(2026, 1, 1)):
    return [of.Observation(start + dt.timedelta(days=i), float(v)) for i, v in enumerate(values)]


class CrpsTests(unittest.TestCase):
    def test_a_point_ensemble_scores_the_absolute_error(self):
        self.assertAlmostEqual(bt.crps([5.0] * 10, 8.0), 3.0)

    def test_matches_the_pairwise_definition(self):
        rng = random.Random(7)
        xs = [rng.gauss(0, 1) for _ in range(60)]
        y = 0.3
        direct = sum(abs(x - y) for x in xs) / len(xs) - 0.5 * sum(
            abs(a - b) for a in xs for b in xs
        ) / (len(xs) ** 2)
        self.assertAlmostEqual(bt.crps(xs, y), direct, places=12)

    def test_a_sharper_correct_ensemble_scores_better(self):
        wide = [float(v) for v in range(-50, 51)]
        sharp = [float(v) / 10 for v in range(-50, 51)]
        self.assertLess(bt.crps(sharp, 0.0), bt.crps(wide, 0.0))


class RecentWindowTests(unittest.TestCase):
    def test_none_keeps_the_whole_history(self):
        obs = _obs(range(40))
        self.assertEqual(of.recent(obs, None), obs)

    def test_a_window_keeps_only_the_trailing_days(self):
        obs = _obs(range(40))
        kept = of.recent(obs, 10)
        self.assertEqual(kept[0].date, obs[-1].date - dt.timedelta(days=10))
        self.assertEqual(kept[-1], obs[-1])

    def test_a_plateau_window_forecasts_a_plateau(self):
        # Explosive growth, then 30 flat days: the incumbent drifts on, the window does not.
        obs = _obs(list(range(0, 600, 10)) + [590] * 30)
        flat = of.end_values(obs, 31, seed=1, n_paths=2000, recent_days=21)
        full = of.end_values(obs, 31, seed=1, n_paths=2000)
        self.assertEqual(sorted(flat)[1000], 590.0)
        self.assertGreater(sorted(full)[1000], 590.0)

    def test_end_values_are_the_final_days_of_the_same_draws(self):
        obs = _obs([3, 5, 4, 8, 9, 7, 12, 11, 15, 14, 18])
        paths = of._paths(obs, 10, 50, 3, 9, None, None)
        self.assertEqual(of.end_values(obs, 10, seed=9, n_paths=50, block=3), [p[-1] for p in paths])


class BacktestTests(unittest.TestCase):
    def test_target_is_the_last_data_day_inside_the_horizon(self):
        obs = _obs(range(60))
        self.assertEqual(bt.target(obs, obs[10].date, 31), 41.0)
        self.assertIsNone(bt.target(obs, obs[50].date, 31))

    def test_seeds_are_stable_and_distinct(self):
        d = dt.date(2026, 9, 1)
        self.assertEqual(bt.seed_for("m", d, "a"), bt.seed_for("m", d, "a"))
        self.assertNotEqual(bt.seed_for("m", d, "a"), bt.seed_for("m", d, "b"))

    def test_run_is_deterministic(self):
        obs = _obs([i + (i % 7) for i in range(90)])
        methods = [bt.INCUMBENT, bt.Method("recent_21d", 21)]
        a = bt.run_metric(obs, "m", methods, n_paths=200, step=10)
        b = bt.run_metric(obs, "m", methods, n_paths=200, step=10)
        self.assertEqual([(s.method, s.crps, s.briers) for s in a], [(s.method, s.crps, s.briers) for s in b])



class PublicationCutTests(unittest.TestCase):
    """History at an origin is only what had been published by then."""

    def test_a_late_published_day_is_absent_until_it_is_published(self):
        obs = _obs(range(10))
        published = {o.date: o.date + dt.timedelta(days=1) for o in obs}
        late = obs[5].date
        published[late] = late + dt.timedelta(days=4)
        by_origin = dict(bt.origins(obs, published))
        on_day_7 = by_origin[obs[6].date + dt.timedelta(days=1)]
        self.assertNotIn(late, [o.date for o in on_day_7])
        self.assertIn(late, [o.date for o in by_origin[late + dt.timedelta(days=4)]])

    def test_no_history_contains_a_day_published_after_its_origin(self):
        obs = _obs(range(30))
        published = {o.date: o.date + dt.timedelta(days=1 + (i % 3)) for i, o in enumerate(obs)}
        for origin, history in bt.origins(obs, published):
            self.assertTrue(all(published[o.date] <= origin for o in history))

    def test_a_day_with_no_publication_record_is_refused(self):
        obs = _obs(range(5))
        with self.assertRaises(ValueError):
            bt.origins(obs, {o.date: o.date for o in obs[:-1]}, "m")

    def test_without_publication_days_the_data_day_cut_is_kept(self):
        obs = _obs(range(5))
        self.assertEqual([len(h) for _, h in bt.origins(obs, None)], [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
