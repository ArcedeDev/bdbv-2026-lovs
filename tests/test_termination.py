"""Tests for the outbreak-termination forecaster.

Three of these carry the module. `test_a_trailing_reporting_gap_does_not_start_a
_countdown` blocks the failure that would matter most in the field: a silent
SitRep outage read as days of progress toward termination. `test_nested_
probabilities_are_internally_consistent` blocks a forecast that quietly prices a
declaration above the zero-case day it requires. And `test_document_quotes_only
_generated_figures` blocks a hand-typed number in the write-up. Do not edit
these to make them pass.
"""
from __future__ import annotations

import datetime as dt
import inspect
import math
import pathlib
import unittest

from lovs.forecast import opsforecast as of
from lovs.forecast import termination as tm

REPO = pathlib.Path(__file__).resolve().parent.parent
SERIES = REPO / "data" / "operational-series-2026-09-01.json"
DOC = REPO / "docs" / "termination-forecast.md"

DAY = dt.date(2026, 1, 1)


def obs_from(values, start=DAY, step=1):
    """Consecutive-day observations, one per value."""
    return [
        of.Observation(start + dt.timedelta(days=i * step), float(v))
        for i, v in enumerate(values)
    ]


def rows_from(pairs):
    """SitRep-shaped rows. A None value is a packet that omitted the indicator."""
    return [{"data_as_of": d.isoformat(), tm.METRIC: v} for d, v in pairs]


class CountdownTests(unittest.TestCase):
    def test_hand_checked_countdown_on_a_clean_series(self):
        # 3, 2, 1 on Jan 1-3; then five reported zero days, Jan 4-8.
        obs = obs_from([3, 2, 1, 0, 0, 0, 0, 0])
        state = tm.countdown(obs, dt.date(2026, 1, 8))
        self.assertTrue(state.clock_running)
        self.assertEqual(state.last_positive_day, "2026-01-03")
        self.assertEqual(state.clock_start, "2026-01-04")
        self.assertEqual(state.observed_zero_days, 5)
        # Elapsed days on the clock, not zero days counted: Jan 4 is day 0.
        self.assertEqual(state.clock_day, 4)
        self.assertEqual(state.clock_days_remaining, 38)
        self.assertEqual(state.unobserved_days_on_clock, 0)
        # Jan 4 + 42 days.
        self.assertEqual(state.earliest_declaration, "2026-02-15")

    def test_a_trailing_reporting_gap_does_not_start_a_countdown(self):
        """A missing SitRep is not a zero-case day. The whole module rests here."""
        obs = obs_from([9, 7, 5])  # last reported day Jan 3, five cases
        state = tm.countdown(obs, dt.date(2026, 2, 2))  # 30 days of silence
        self.assertFalse(state.clock_running)
        self.assertIsNone(state.clock_day)
        self.assertEqual(state.observed_zero_days, 0)
        self.assertEqual(state.unobserved_trailing_days, 30)
        self.assertEqual(state.effective_as_of, "2026-01-03")
        # The floor is still measured from the last DATA day, not from as_of.
        self.assertEqual(state.earliest_declaration, "2026-02-15")
        self.assertEqual(state.days_from_last_data_day, 43)
        self.assertIn("unreported", state.note)

    def test_packets_that_omit_the_indicator_are_not_zero_case_days(self):
        """The same trap arriving as nulls inside published SitReps."""
        days = [DAY + dt.timedelta(days=i) for i in range(10)]
        rows = rows_from(
            [(days[0], 9), (days[1], 7), (days[2], 5)]
            + [(d, None) for d in days[3:]]
        )
        state = tm.countdown(of.series(rows, tm.METRIC), days[9])
        self.assertFalse(state.clock_running)
        self.assertEqual(state.observed_zero_days, 0)
        self.assertEqual(state.unobserved_trailing_days, 7)

    def test_a_reported_zero_does_start_the_countdown(self):
        """The mirror of the gap test: a real zero must not be discarded too."""
        days = [DAY + dt.timedelta(days=i) for i in range(5)]
        rows = rows_from([(days[0], 4), (days[1], 2), (days[2], 0), (days[3], 0),
                          (days[4], 0)])
        state = tm.countdown(of.series(rows, tm.METRIC), days[4])
        self.assertTrue(state.clock_running)
        self.assertEqual(state.observed_zero_days, 3)
        self.assertEqual(state.clock_day, 2)

    def test_elapsed_clock_is_clamped_to_the_last_data_day(self):
        """A gap during a countdown may not be credited as elapsed countdown."""
        obs = obs_from([4, 0, 0, 0])  # zeros reported Jan 2-4
        state = tm.countdown(obs, dt.date(2026, 1, 20))
        self.assertTrue(state.clock_running)
        self.assertEqual(state.clock_day, 2)  # to Jan 4, not to Jan 20
        self.assertEqual(state.unobserved_trailing_days, 16)
        self.assertEqual(state.effective_as_of, "2026-01-04")

    def test_unobserved_days_inside_a_running_clock_are_flagged(self):
        obs = obs_from([4]) + [
            of.Observation(dt.date(2026, 1, 2), 0.0),
            of.Observation(dt.date(2026, 1, 8), 0.0),
        ]
        state = tm.countdown(obs, dt.date(2026, 1, 8))
        self.assertTrue(state.clock_running)
        self.assertEqual(state.observed_days_on_clock, 2)
        self.assertEqual(state.unobserved_days_on_clock, 5)
        self.assertIn("not fully verified", state.note)

    def test_observations_after_as_of_are_invisible(self):
        """Walk-forward safety: a later SitRep cannot leak into an earlier call."""
        obs = obs_from([5, 4, 0, 0, 9, 9])
        state = tm.countdown(obs, dt.date(2026, 1, 4))
        self.assertTrue(state.clock_running)
        self.assertEqual(state.last_data_day, "2026-01-04")
        self.assertEqual(tm.countdown(obs, dt.date(2026, 1, 6)).clock_running, False)

    def test_clock_start_lag_delays_the_floor(self):
        """WHO counts from the second negative test, never earlier than the case."""
        obs = obs_from([3, 0, 0])
        base = tm.countdown(obs, dt.date(2026, 1, 3))
        lagged = tm.countdown(obs, dt.date(2026, 1, 3), clock_start_lag_days=7)
        self.assertEqual(base.earliest_declaration, "2026-02-13")
        self.assertEqual(lagged.earliest_declaration, "2026-02-20")
        self.assertFalse(lagged.clock_running)  # the lagged clock has not begun

    def test_an_empty_or_future_series_refuses_rather_than_guesses(self):
        with self.assertRaises(ValueError):
            tm.countdown([], DAY)
        with self.assertRaises(ValueError):
            tm.countdown(obs_from([1, 2]), dt.date(2025, 12, 31))


class PathShapeTests(unittest.TestCase):
    def test_outcomes_are_hand_checked(self):
        clock = 4  # a toy clock: 4 elapsed days plus the declaration day
        self.assertEqual(tm._outcomes([3, 2, 1], clock), (False, False, False))
        # A single zero day mid-path is noise, not a countdown.
        self.assertEqual(tm._outcomes([3, 0, 2, 1], clock), (True, False, False))
        # Zeros running to the horizon: a clock is standing but not complete.
        self.assertEqual(tm._outcomes([3, 0, 0, 0], clock), (True, True, False))
        # Five consecutive zeros: start day, four elapsed days, declaration day.
        self.assertEqual(tm._outcomes([3, 0, 0, 0, 0, 0], clock), (True, True, True))
        # One short.
        self.assertEqual(tm._outcomes([3, 0, 0, 0, 0, 2], clock), (True, False, False))
        # A case ON the declaration day resets the clock.
        self.assertEqual(
            tm._outcomes([0, 0, 0, 0, 9, 0], clock), (True, True, False)
        )

    def test_absorbing_freezes_a_path_at_its_first_zero(self):
        self.assertEqual(tm._absorb([3, 0, 5, 7]), [3, 0, 0, 0])
        self.assertEqual(tm._absorb([3, 2]), [3, 2])

    def test_percentile_is_nearest_rank(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(tm._percentile(values, 0.0), 1.0)
        self.assertEqual(tm._percentile(values, 0.5), 3.0)
        self.assertEqual(tm._percentile(values, 1.0), 5.0)


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = of.load_rows(SERIES)
        cls.obs = of.series(cls.rows, tm.METRIC)
        cls.state = tm.countdown(cls.obs, dt.date(2026, 9, 1))

    def _horizon(self, days, n_paths=1500):
        return tm.forecast_horizon(
            self.obs, self.state, days, seed=11, n_paths=n_paths
        )

    def test_nested_probabilities_are_internally_consistent(self):
        """Declared over cannot exceed clock standing cannot exceed a zero day."""
        for days in tm.DEFAULT_HORIZONS:
            block = self._horizon(days)
            for variant in ("reflecting", "absorbing"):
                f = block[variant]
                self.assertLessEqual(
                    f["p_declared_over"], f["p_clock_standing"],
                    f"{variant} @{days}d prices a declaration above its own clock",
                )
                self.assertLessEqual(
                    f["p_clock_standing"], f["p_first_zero_day"],
                    f"{variant} @{days}d prices a clock above its own zero day",
                )

    def test_a_declaration_inside_the_clock_is_a_structural_zero(self):
        block = self._horizon(30)
        for variant in ("reflecting", "absorbing"):
            f = block[variant]
            self.assertFalse(f["declaration_arithmetically_possible"])
            self.assertEqual(f["p_declared_over"], 0.0)
            self.assertIn("STRUCTURAL ZERO", f["caveats"]["p_declared_over"])
        self.assertTrue(self._horizon(60)["reflecting"][
            "declaration_arithmetically_possible"])

    def test_the_boundary_variants_bracket_the_answer(self):
        """Absorbing is the ceiling, reflecting the floor, on identical draws."""
        for days in tm.DEFAULT_HORIZONS:
            block = self._horizon(days)
            self.assertEqual(
                block["absorbing"]["p_first_zero_day"],
                block["reflecting"]["p_first_zero_day"],
                "the variants differ only after a path reaches zero",
            )
            self.assertGreaterEqual(
                block["absorbing"]["p_declared_over"],
                block["reflecting"]["p_declared_over"],
            )
            self.assertGreaterEqual(
                block["absorbing"]["p_clock_standing"],
                block["reflecting"]["p_clock_standing"],
            )

    def test_forecast_is_deterministic(self):
        self.assertEqual(self._horizon(60, 600), self._horizon(60, 600))

    def test_an_inherited_countdown_is_carried_into_the_paths(self):
        """A forecast made on day 40 of a clock must not restart it at zero."""
        obs = obs_from([50] * 10 + [0] * 40)
        state = tm.countdown(obs, obs[-1].date)
        self.assertEqual(state.clock_day, 39)
        block = tm.forecast_horizon(obs, state, 30, seed=5, n_paths=400)
        self.assertEqual(block["reflecting"]["head_zero_days"], 40)
        # Three more case-free days would complete this clock, so a declaration
        # inside 30 days is reachable even though 30 < 42.
        self.assertTrue(block["reflecting"]["declaration_arithmetically_possible"])
        self.assertGreater(block["reflecting"]["p_declared_over"], 0.0)


class CalibrationCaveatTests(unittest.TestCase):
    def test_the_low_band_is_derived_from_the_measured_backtest(self):
        self.assertEqual(tm.LOW_BAND_MAX, 0.15)
        for priced, _n, observed in tm.MEASURED_RELIABILITY:
            if priced <= tm.LOW_BAND_MAX:
                self.assertGreater(observed, priced, priced)

    def test_the_caveat_travels_with_small_numbers_only(self):
        self.assertIsNotNone(tm.low_band_caveat(0.05))
        self.assertIsNotNone(tm.low_band_caveat(0.15))
        self.assertIsNone(tm.low_band_caveat(0.4))
        self.assertIn("23%", tm.low_band_caveat(0.04))
        self.assertIn("42%", tm.low_band_caveat(0.15))

    def test_the_caveat_does_not_alter_the_number(self):
        """Stated bias, not silent inflation."""
        obs = of.series(of.load_rows(SERIES), tm.METRIC)
        state = tm.countdown(obs, dt.date(2026, 9, 1))
        block = tm.forecast_horizon(obs, state, 30, seed=11, n_paths=1500)
        flagged = block["reflecting"]
        self.assertIn("p_clock_standing", flagged["caveats"])
        # Recount the same seeded draws by hand: the emitted figure is the raw
        # fraction, with the bias stated beside it rather than folded into it.
        paths = of._paths(obs, 30, 1500, of.DEFAULT_BLOCK, 11, 0.0, None)
        standing = sum(1 for path in paths if tm._outcomes(path, tm.CLOCK_DAYS)[1])
        self.assertEqual(flagged["p_clock_standing"], round(standing / 1500, 4))


class DecayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.obs = of.series(of.load_rows(SERIES), tm.METRIC)
        cls.decay = tm.decay_diagnostic(cls.obs)

    def test_required_halving_time_is_the_stated_arithmetic(self):
        available = tm.DEFAULT_WITHIN_DAYS - tm.CLOCK_DAYS - 1
        self.assertEqual(self.decay.days_available_for_decline, available)
        expected = math.log(2) * available / math.log(self.decay.last_value)
        self.assertAlmostEqual(self.decay.required_halving_days, expected, places=1)

    def test_the_trend_sign_flips_across_windows(self):
        """The finding, and the reason a single window is not reported alone."""
        self.assertFalse(self.decay.trend_sign_consistent)
        signs = {w.log_slope_per_day < 0 for w in self.decay.windows}
        self.assertEqual(signs, {True, False})
        self.assertIn("flips with the window", self.decay.verdict)

    def test_a_declining_window_reports_when_it_would_terminate(self):
        fastest = min(
            (w for w in self.decay.windows if w.halving_days is not None),
            key=lambda w: w.halving_days,
        )
        implied = fastest.days_to_below_one_case + tm.CLOCK_DAYS + 1
        self.assertAlmostEqual(fastest.days_to_declaration_at_this_rate, implied, 1)
        self.assertGreater(fastest.days_to_declaration_at_this_rate, 0)

    def test_a_flat_series_reports_no_decay_phase(self):
        decay = tm.decay_diagnostic(obs_from([40] * 30))
        self.assertIsNone(decay.fastest_observed_halving_days)
        self.assertIn("no decay phase", decay.verdict)


class ReportTests(unittest.TestCase):
    def test_the_module_has_no_clock_of_its_own(self):
        for fn in (tm.report, tm.countdown):
            as_of = inspect.signature(fn).parameters["as_of"]
            self.assertIs(as_of.default, inspect.Parameter.empty, fn.__name__)

    def test_document_quotes_only_generated_figures(self):
        """Every figure in the write-up must come back out of the module."""
        text = DOC.read_text(encoding="utf-8")
        figures = tm.doc_figures(
            tm.report(of.load_rows(SERIES), dt.date(2026, 9, 1))
        )
        missing = [f"{k}={v!r}" for k, v in figures.items() if v not in text]
        self.assertEqual(missing, [], f"figures absent from {DOC.name}: {missing}")


if __name__ == "__main__":
    unittest.main()
