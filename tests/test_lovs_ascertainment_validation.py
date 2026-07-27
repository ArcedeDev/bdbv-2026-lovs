"""Tests for the ascertainment-correction identifiability check.

The behavioral claim under test: this module must return ``not_identifiable`` on the real
SitRep 72 corpus, and it must return ``identifiable`` on a synthetic outbreak where the
correction genuinely IS identifiable. A check that can only ever say "no" is not evidence
of anything, so both directions are pinned.
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from lovs import lovs_ascertainment_validation as av


def _flow(values_by_offset: dict[int, float], as_of: date = date(2026, 7, 25)):
    """Build a daily flow series from {days_before_as_of: value}."""
    return [
        {"date": (as_of - timedelta(days=k)).isoformat(), "value": v}
        for k, v in sorted(values_by_offset.items(), reverse=True)
    ]


def _ramp(first: float, second: float, days: int = 45, as_of: date = date(2026, 7, 25)):
    """A step series: ``first`` in the older half of every window, ``second`` in the newer."""
    out = []
    for k in range(days):
        day = as_of - timedelta(days=k)
        out.append({"date": day.isoformat(), "value": second if k < days / 2 else first})
    return out


def _exponential(start: float, r: float, days: int = 45, as_of: date = date(2026, 7, 25)):
    """A genuinely exponential flow at rate ``r``, so growth is window-INdependent."""
    return [
        {"date": (as_of - timedelta(days=k)).isoformat(), "value": start * pow(2.718281828, r * (days - k))}
        for k in range(days)
    ]


class HalfWindowLogGrowthTests(unittest.TestCase):
    def test_recovers_a_known_exponential_rate(self):
        series = _exponential(10.0, 0.05)
        est = av.half_window_log_growth(series, "2026-07-25", window_days=21)
        self.assertIsNotNone(est)
        self.assertAlmostEqual(0.05, est["r_per_day"], places=2)

    def test_does_not_floor_a_declining_signal(self):
        # Saturation detection depends on being able to SEE a falling corroborator.
        est = av.half_window_log_growth(_exponential(100.0, -0.03), "2026-07-25", window_days=21)
        self.assertIsNotNone(est)
        self.assertLess(est["r_per_day"], 0.0)

    def test_returns_none_below_the_minimum_point_count(self):
        sparse = _flow({0: 10.0, 3: 9.0})
        self.assertIsNone(av.half_window_log_growth(sparse, "2026-07-25", window_days=21))

    def test_ignores_observations_outside_the_window(self):
        est = av.half_window_log_growth(_exponential(10.0, 0.05, days=90), "2026-07-25",
                                        window_days=14)
        self.assertIsNotNone(est)
        self.assertLessEqual(est["n_first_half"] + est["n_second_half"], 15)


class ZeroMeansMissingTests(unittest.TestCase):
    """Six promotions record admissions_24h = 0 because no patient-movement table was
    published. Reading those as real zeros fabricates a collapse in the one signal the
    scope named first."""

    def _promo(self, day: str, admissions: int):
        return {
            "data_as_of": day,
            "figures": {"operational_tables": {"patient_movement_total": {
                "admissions_24h": admissions,
                "data_gap_note": "no patient-movement table published" if admissions == 0 else None,
            }}},
        }

    def test_zero_admissions_are_dropped_not_read_as_zero(self):
        promos = [self._promo("2026-07-2%d" % d, 0 if d in (1, 2) else 100) for d in range(1, 6)]
        loaded = av.load_corroborating_signals(promos)
        values = [row["value"] for row in loaded["admissions_24h"]]
        self.assertEqual([100.0, 100.0, 100.0], values)

    def test_zero_is_kept_for_signals_without_the_rule(self):
        # alerts_investigated has no zero-means-missing rule; a real zero must survive.
        promos = [{
            "data_as_of": "2026-07-25",
            "figures": {"operational_tables": {"alerts_total": {"alerts_investigated": 0}}},
        }]
        loaded = av.load_corroborating_signals(promos)
        self.assertEqual([0.0], [r["value"] for r in loaded["alerts_investigated"]])


class WindowStabilityTests(unittest.TestCase):
    def test_disagreeing_windows_are_flagged_unstable(self):
        conf = {14: 0.0565, 21: 0.0434, 28: 0.0289}
        pos = {14: 0.0590, 21: 0.0202, 28: 0.0015}
        out = av.correction_window_stability(conf, pos)
        self.assertFalse(out["window_stable"])
        self.assertGreater(out["corrected_doubling_spread_ratio"], av.IDENTIFIABILITY_SPREAD_LIMIT)

    def test_agreeing_windows_are_stable(self):
        conf = {14: 0.040, 21: 0.041, 28: 0.039}
        pos = {14: 0.020, 21: 0.021, 28: 0.019}
        out = av.correction_window_stability(conf, pos)
        self.assertTrue(out["window_stable"])

    def test_negative_positivity_growth_is_bound_incoherent(self):
        # r_pos < 0 makes the "corrected central" SLOWER than the published upper bound.
        out = av.correction_window_stability({35: 0.0192}, {35: -0.0029})
        self.assertFalse(out["bound_coherent"])
        self.assertEqual([35], out["bound_incoherent_windows"])
        row = out["per_window"][0]
        self.assertGreater(row["corrected_doubling_days"], row["published_bound_doubling_days"])


class CorroborationTests(unittest.TestCase):
    def test_slower_signals_do_not_corroborate(self):
        conf = {14: 0.0565, 21: 0.0434}
        rates = {"admissions_24h": {14: 0.0211, 21: 0.0104}}
        out = av.corroboration(rates, conf)
        self.assertFalse(out["corroborated"])

    def test_a_faster_independent_signal_corroborates(self):
        conf = {14: 0.030, 21: 0.030}
        rates = {"admissions_24h": {14: 0.060, 21: 0.058}}
        out = av.corroboration(rates, conf)
        self.assertTrue(out["corroborated"])
        self.assertIn("admissions_24h", out["corroborating_signals"])

    def test_testing_gated_signals_cannot_corroborate_alone(self):
        # Deaths beating confirmed is NOT evidence: same PCR gate.
        conf = {14: 0.030, 21: 0.030}
        rates = {"new_confirmed_deaths_24h": {14: 0.060, 21: 0.058}}
        out = av.corroboration(rates, conf)
        self.assertFalse(out["corroborated"])
        deaths = next(s for s in out["signals"] if s["signal"] == "new_confirmed_deaths_24h")
        self.assertFalse(deaths["eligible_as_validator"])
        self.assertEqual(2, deaths["windows_faster_than_confirmed"])


class VerdictTests(unittest.TestCase):
    def test_identifiable_when_all_three_conditions_hold(self):
        # Synthetic outbreak: confirmed and positivity both grow at stable exponential
        # rates, and an independent signal outpaces confirmed.
        out = av.validate_ascertainment_correction(
            as_of="2026-07-25",
            confirmed_incidence_series=_exponential(40.0, 0.030),
            testing_series=[
                {"date": r["date"], "tests": 300.0, "positivity_pct": v["value"]}
                for r, v in zip(_exponential(20.0, 0.010), _exponential(20.0, 0.010))
            ],
            signal_series={"admissions_24h": _exponential(100.0, 0.055)},
            windows_days=(14, 21, 28),
        )
        self.assertEqual("identifiable", out["verdict"])
        self.assertTrue(out["publish_corrected_central"])
        self.assertEqual([], out["blockers"])

    def test_not_identifiable_when_the_only_fast_signal_is_testing_gated(self):
        out = av.validate_ascertainment_correction(
            as_of="2026-07-25",
            confirmed_incidence_series=_exponential(40.0, 0.030),
            testing_series=[
                {"date": r["date"], "tests": 300.0, "positivity_pct": r["value"]}
                for r in _exponential(20.0, 0.010)
            ],
            signal_series={"new_confirmed_deaths_24h": _exponential(30.0, 0.060)},
            windows_days=(14, 21, 28),
        )
        self.assertEqual("not_identifiable", out["verdict"])
        self.assertIn("uncorroborated", out["blockers"])

    def test_no_testing_series_is_not_identifiable_rather_than_an_error(self):
        out = av.validate_ascertainment_correction(
            as_of="2026-07-25",
            confirmed_incidence_series=_exponential(40.0, 0.030),
            testing_series=None,
            signal_series={},
            windows_days=(14, 21),
        )
        self.assertEqual("not_identifiable", out["verdict"])
        self.assertFalse(out["publish_corrected_central"])


if __name__ == "__main__":
    unittest.main()
