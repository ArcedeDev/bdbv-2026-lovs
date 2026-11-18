# SPDX-License-Identifier: Apache-2.0
"""Weekly loop, test 2: 23 hub levels, monotone in-support quantiles, the flow floor."""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from lovs.forecast import opsforecast as of
from lovs.forecast.weekly import methods, spec
from tests.weekly_fixtures import R, fixture_week


class QuantileLevelsTest(unittest.TestCase):
    def test_twenty_three_levels_with_095(self) -> None:
        Q = spec.QUANTILE_LEVELS
        assert len(Q) == 23 and 0.95 in Q
        self.assertEqual(list(Q), sorted(Q))
        self.assertIn(0.5, Q)
        self.assertEqual((Q[0], Q[1], Q[-2], Q[-1]), (0.01, 0.025, 0.975, 0.99))
        # Symmetric pairs, as the hubs use: 11 central intervals and the median.
        for lo, hi in zip(Q, reversed(Q)):
            self.assertAlmostEqual(lo + hi, 1.0, places=12)


class BuiltQuantilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.build, *_ = fixture_week(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_cell_has_23_monotone_levels(self) -> None:
        forecasts = self.build.forecasts["forecasts"]
        self.assertTrue(forecasts)
        for f in forecasts:
            levels = [q for q, _ in f["quantiles"]]
            values = [v for _, v in f["quantiles"]]
            assert len(levels) == 23 and 0.95 in levels
            self.assertEqual(tuple(levels), spec.QUANTILE_LEVELS)
            self.assertEqual(values, sorted(values), f"{f['series']} {f['horizon_days']} {f['method']}")

    def test_in_support(self) -> None:
        last = {r["series"]: r["last_value"] for r in self.build.forecasts["series"]}
        for f in self.build.forecasts["forecasts"]:
            s = spec.SERIES_BY_ID[f["series"]]
            values = [v for _, v in f["quantiles"]] + [f["mean"]]
            if s.kind == "flow":
                self.assertGreaterEqual(min(values), 0.0, f["series"])
            if s.bound == "percent":
                self.assertTrue(all(0.0 <= v <= 100.0 for v in values))
            if s.kind == "lag":
                self.assertTrue(all(0.0 <= v <= spec.LAG_CAP for v in values))
            if s.kind == "endlevel":
                self.assertGreaterEqual(min(values), last[f["series"]])

    def test_horizons_and_methods(self) -> None:
        cells = {(f["series"], f["horizon_days"], f["method"]) for f in self.build.forecasts["forecasts"]}
        for sid in self.build.forecasts["active_series"]:
            s = spec.SERIES_BY_ID[sid]
            for h in spec.HORIZONS:
                for m in spec.methods_for(s):
                    self.assertIn((sid, h, m), cells)


class FlowFloorTest(unittest.TestCase):
    """The SR169-170 pattern (+346 then -269) must not produce a negative weekly flow."""

    def _obs(self) -> list[of.Observation]:
        start = dt.date(2026, 9, 1)
        values, level = [], 4000.0
        for i in range(60):
            level += 20 + (i % 5)
            values.append(level)
        # A retrospective harmonisation and its reversal on two consecutive days.
        values[50] += 346
        values[51] = values[50] - 269
        for i in range(52, 60):
            values[i] = values[i - 1] + 22
        return [of.Observation(start + dt.timedelta(days=i), v) for i, v in enumerate(values)]

    def test_floored_steps_drop_the_fall_and_the_step_before(self) -> None:
        obs = self._obs()
        steps, floored, total = methods.floored_steps(obs, None)
        self.assertEqual(total, len(obs) - 1)
        self.assertEqual(floored, 2)
        self.assertEqual(len(steps), total - 2)
        self.assertTrue(all(s >= 0 for s in steps))
        self.assertNotIn(346.0 + 20 + (50 % 5), steps)

    def test_probability_of_negative_flow_is_zero(self) -> None:
        obs = self._obs()
        series = spec.SERIES_BY_ID["N2"]
        Rd = obs[-1].date + dt.timedelta(days=3)
        while Rd.weekday() != 2:
            Rd += dt.timedelta(days=1)
        for method in (spec.M_DRIFT, spec.M_LEVEL):
            draws = methods.simulate(series, method, obs, Rd, seed=11, n_paths=2000)
            for k in (1, 2, 3, 4):
                negative = sum(1 for x in draws.samples[k] if x < 0)
                self.assertEqual(negative, 0, f"{method} week {k}")

    def test_unfloored_bootstrap_would_go_negative(self) -> None:
        # The reason for the floor: the block bootstrap of raw steps produces falls.
        obs = self._obs()
        paths = of.simulate_paths(obs, 14, n_paths=2000, block=5, seed=11, recent_days=28)
        self.assertTrue(any(p[13] - p[6] < 0 for p in paths))

    def test_percent_series_clamped(self) -> None:
        start = dt.date(2026, 9, 1)
        obs = [of.Observation(start + dt.timedelta(days=i), min(99.5, 80 + i * 0.5)) for i in range(60)]
        series = spec.SERIES_BY_ID["N5"]
        Rd = dt.date(2026, 11, 4)
        for method in spec.methods_for(series):
            if method == spec.M_POOL:
                continue
            draws = methods.simulate(series, method, obs, Rd, seed=5, n_paths=500)
            for k in (1, 2, 3, 4):
                self.assertTrue(all(0.0 <= x <= 100.0 for x in draws.samples[k]), method)


if __name__ == "__main__":
    unittest.main()
