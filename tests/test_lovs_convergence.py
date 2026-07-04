"""Regression lock for lovs_convergence.build_convergence.

The original convergence generator was lost with an ephemeral working tree (see
specs/2026-06-09-june7-snapshot-delta-audit.md). These tests pin the rebuilt module so a
future lineage cannot silently drop or drift the convergence block again.

2026-07-04 rev: the HEADLINE estimated_total_cases is now GROUND-DERIVED from the death
anchor each cycle (true infections = confirmed_deaths / (death_ascertainment x IFR)); the
level multiplier and case ascertainment are DERIVED outputs that float, replacing the
frozen 2.0/2.5/3.6. NOTE: these June fixtures are EXPLOSIVE-GROWTH scenarios where deaths
lag infections, so the death anchor understates the stock and reads high ascertainment (the
documented regime caveat); at the current Rt~1 plateau the anchor is well-behaved. Imperial
Method 2 is retained under estimated_total_cases.cross_check as an external validator.
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

    def test_estimated_total_cases_is_the_level_model(self):
        cases = self.block["true_burden_nowcast"]["estimated_total_cases"]
        # Headline = death-anchored: confirmed_deaths / (death_ascertainment x IFR).
        # 93 deaths / (0.667 x {0.16,0.15,0.13}) with anti-correlated death-ascertainment.
        self.assertEqual([cases["low"], cases["central"], cases["high"]], [700, 927, 1362])
        self.assertEqual(cases["provenance"], "lovs")
        # multipliers are now DERIVED (true infections / confirmed), no longer frozen.
        self.assertEqual(cases["multipliers"], {"low": 1.31, "central": 1.74, "high": 2.55})

    def test_imperial_retained_as_external_cross_check(self):
        xc = self.block["true_burden_nowcast"]["estimated_total_cases"]["cross_check"]
        # The prior headline (Imperial Method 2) is kept as an independent validator.
        self.assertEqual([xc["low"], xc["central"], xc["high"]], [635, 770, 977])
        self.assertEqual(xc["provenance"], "external")

    def test_ascertainment_and_unreported_from_level_central(self):
        gap = self.block["true_burden_nowcast"]["ascertainment_gap"]
        # Derived from the death-anchored central (927). Growth-phase artifact: deaths
        # lag here so ascertainment reads high (0.58); at the plateau it settles near 0.32.
        self.assertEqual(gap["case_ascertainment"], 0.5761)
        self.assertEqual(gap["confirmed_vs_estimated_total_cases"], [534, 927])
        self.assertEqual(gap["estimated_unreported_cases"], 393)

    def test_estimated_total_deaths(self):
        deaths = self.block["true_burden_nowcast"]["estimated_total_deaths"]
        self.assertEqual([deaths["low"], deaths["central"], deaths["high"]], [112, 139, 177])
        self.assertEqual(deaths["death_ascertainment_band"], [0.526, 0.833])

    def test_transmission_floor(self):
        tf = self.block["transmission_floor"]
        self.assertEqual(tf["new_cases_from_roster"], {"low": 151, "spine": 186, "high": 454})
        self.assertEqual(tf["implied_cumulative_floor"], 685)
        self.assertEqual(tf["coverage_panel"], {"followup_rate_pct": 50.3, "unobserved_pct": 49.7})

    def test_methodology_shape(self):
        methodology = self.block["methodology"]
        # Row 0 = LOVS level-model headline; row 1 = Imperial external cross-check.
        self.assertEqual(len(methodology), 6)
        self.assertEqual(methodology[0]["provenance"], "lovs")
        self.assertEqual(methodology[1]["provenance"], "external")
        self.assertTrue(all(m.get("equation") and m.get("sources") for m in methodology))
        self.assertEqual(methodology[0]["worked_central"], "93 / (0.667 x 0.15) = 927  (implied 1.74x on 534 confirmed)")
        self.assertEqual(
            methodology[1]["worked_central"],
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

    def test_level_model_tracks_the_death_anchor(self):
        nc = self.block["true_burden_nowcast"]
        cases = nc["estimated_total_cases"]
        # Death-anchored: 103 deaths / (0.667 x {0.16,0.15,0.13}) = 775 / 1027 / 1508;
        # the headline moved with the DEATH count (93 -> 103), proving per-cycle recompute
        # off the death anchor (no longer a fixed multiple of confirmed).
        self.assertEqual([cases["low"], cases["central"], cases["high"]], [775, 1027, 1508])
        self.assertEqual(cases["cross_check"]["central"], 852)  # Imperial Method 2 (external)
        self.assertEqual(nc["ascertainment_gap"]["estimated_unreported_cases"], 458)
        self.assertEqual(nc["estimated_total_deaths"]["central"], 154)
        self.assertEqual(
            [nc["estimated_total_deaths"]["low"], nc["estimated_total_deaths"]["high"]],
            [124, 196],
        )
        tf = self.block["transmission_floor"]
        self.assertEqual(tf["new_cases_from_roster"], {"low": 163, "spine": 200, "high": 488})
        self.assertEqual(tf["implied_cumulative_floor"], 732)
        self.assertEqual(tf["coverage_panel"]["unobserved_pct"], 35.6)


class TestDelayAdjustedCfr(unittest.TestCase):
    """Locks the Nishiura 2009 delay-adjusted confirmed CFR (cycle-invariant: the math
    + structure, not a cycle-specific headline value)."""

    def test_gammap_endpoints_and_monotonicity(self):
        g = lovs_convergence._gammap
        self.assertEqual(g(4.42, 0.0), 0.0)
        self.assertAlmostEqual(g(4.42, 300.0), 1.0, places=6)  # past all the mass
        vals = [g(4.42, x) for x in range(1, 31)]
        self.assertTrue(all(b > a for a, b in zip(vals, vals[1:])))  # strictly increasing
        self.assertAlmostEqual(g(4.42, 4.42), 0.5633, places=3)  # CDF at the mean delay

    def test_nishiura_denominator_drops_unresolved_recent_cases(self):
        # 100 fully-resolved old cases (F~1) + 100 same-day cases (F=0), 40 deaths.
        series = [
            {"date": "2026-01-01", "value": 100},
            {"date": "2026-06-25", "value": 200},
        ]
        out = lovs_convergence.delay_adjusted_cfr(
            series, 40, alpha=4.42, beta=0.388, as_of="2026-06-25"
        )
        self.assertEqual(out["confirmed_cfr_crude_pct"], 20.0)  # 40 / 200
        # same-day cases leave the resolved denominator -> 40 / ~100
        self.assertEqual(out["confirmed_cfr_delay_adjusted_pct"]["central"], 40.0)
        self.assertEqual(out["scope"], "country")
        self.assertTrue(any("Nishiura" in s for s in out["sources"]))

    def test_adjusted_exceeds_crude_during_accrual_and_band_orders(self):
        series = [
            {"date": f"2026-06-{d:02d}", "value": v}
            for d, v in [(1, 100), (10, 200), (20, 320), (25, 400)]
        ]
        out = lovs_convergence.delay_adjusted_cfr(
            series, 120, alpha=4.42, beta=0.388, as_of="2026-06-25"
        )
        adj = out["confirmed_cfr_delay_adjusted_pct"]
        self.assertGreater(adj["central"], out["confirmed_cfr_crude_pct"])
        self.assertLessEqual(adj["low"], adj["central"])
        self.assertLessEqual(adj["central"], adj["high"])

    def test_delay_adjusted_cfr_returns_none_on_empty_series(self):
        self.assertIsNone(
            lovs_convergence.delay_adjusted_cfr([], 40, alpha=4.42, beta=0.388, as_of="2026-06-25")
        )

    def test_build_convergence_emits_severity_only_with_series(self):
        kwargs = dict(
            as_of="2026-06-25", confirmed=1223, confirmed_deaths=323,
            contacts_under_follow_up=9294, followup_coverage_pct=82.8,
            methodology_constants=METHODOLOGY_CONSTANTS,
        )
        without = lovs_convergence.build_convergence(**kwargs)
        self.assertIsNone(without["severity_cfr"])
        n_rows = len(without["methodology"])
        series = [{"date": "2026-06-01", "value": 1000}, {"date": "2026-06-25", "value": 1223}]
        withs = lovs_convergence.build_convergence(confirmed_series=series, **kwargs)
        self.assertIsNotNone(withs["severity_cfr"])
        self.assertEqual(withs["severity_cfr"]["scope"], "country")
        self.assertEqual(len(withs["methodology"]), n_rows + 1)
        self.assertTrue(withs["methodology"][-1]["quantity"].startswith("Delay-adjusted"))


class TestGrowthRateEstimator(unittest.TestCase):
    """Floated growth rate from the incidence series (replaces the frozen 7-day doubling
    in the Imperial cross-check). 21-day trailing window, second-half vs first-half mean
    incidence, floored at 0 so a plateau/decline yields a growth correction of 1."""

    def test_flat_incidence_is_a_plateau(self):
        # +10 confirmed/day for 21 days -> incidence flat -> r == 0 -> plateau.
        series = [{"date": f"2026-06-{d:02d}", "value": 100 + 10 * (d - 1)} for d in range(1, 22)]
        g = lovs_convergence.estimate_growth_rate(series, as_of="2026-06-21")
        self.assertEqual(g["r_per_day"], 0.0)
        self.assertIsNone(g["doubling_time_days"])
        self.assertEqual(g["regime"], "plateau")

    def test_rising_incidence_flags_growth_with_finite_doubling(self):
        vals, cum = [], 100
        for inc in [10] * 7 + [20] * 7 + [40] * 7:  # incidence ramps up across the window
            cum += inc
            vals.append(cum)
        series = [{"date": f"2026-06-{d:02d}", "value": v} for d, v in zip(range(1, 22), vals)]
        g = lovs_convergence.estimate_growth_rate(series, as_of="2026-06-21")
        self.assertGreater(g["r_per_day"], 0.0)
        self.assertIsNotNone(g["doubling_time_days"])
        self.assertIn(g["regime"], ("growing", "slow_growth"))

    def test_insufficient_series_returns_insufficient_data(self):
        g = lovs_convergence.estimate_growth_rate(
            [{"date": "2026-06-25", "value": 200}], as_of="2026-06-25"
        )
        self.assertEqual(g["regime"], "insufficient_data")
        self.assertIsNone(g["r_per_day"])


class TestConvergenceRangeWithSeries(unittest.TestCase):
    """With the national series present, the headline is a death-TIMING bracket:
    crude death anchor (lower, deaths lag) to delay-adjusted death anchor (upper, eventual
    deaths), geometric-mean central. Uses the controlled Nishiura fixture (200 cases / 40
    deaths; 100 resolved + 100 same-day -> crude cCFR 20%, delay-adjusted 40%) so the pins
    are exact and independent of the live series shape."""

    def setUp(self):
        self.series = [
            {"date": "2026-01-01", "value": 100},   # fully resolved
            {"date": "2026-06-25", "value": 200},   # same-day, unresolved
        ]
        self.block = lovs_convergence.build_convergence(
            as_of="2026-06-25", confirmed=200, confirmed_deaths=40,
            contacts_under_follow_up=1000, followup_coverage_pct=80.0,
            methodology_constants=METHODOLOGY_CONSTANTS, confirmed_series=self.series,
        )

    def test_headline_is_the_death_timing_bracket(self):
        c = self.block["true_burden_nowcast"]["estimated_total_cases"]
        # low = crude death anchor central (400); high = delay-adjusted central (800);
        # central = geometric mean = round(sqrt(400*800)) = 566.
        self.assertEqual([c["low"], c["central"], c["high"]], [400, 566, 800])
        self.assertEqual(c["central"], round((c["low"] * c["high"]) ** 0.5))
        self.assertEqual(c["multipliers"], {"low": 2.0, "central": 2.83, "high": 4.0})

    def test_both_anchors_carry_the_full_parameter_band(self):
        c = self.block["true_burden_nowcast"]["estimated_total_cases"]
        self.assertEqual(c["crude_anchor"], {"low": 300, "central": 400, "high": 585})
        self.assertEqual(c["delay_adjusted_anchor"], {"low": 600, "central": 800, "high": 1169})

    def test_deaths_and_ascertainment_are_consistent_with_the_new_central(self):
        nc = self.block["true_burden_nowcast"]
        # true deaths bracket mirrors the infections bracket; central = round(566 * 0.15) = 85.
        d = nc["estimated_total_deaths"]
        self.assertEqual([d["low"], d["central"], d["high"]], [60, 85, 120])
        gap = nc["ascertainment_gap"]
        self.assertEqual(gap["case_ascertainment"], 0.3534)  # 200 / 566
        self.assertEqual(gap["confirmed_vs_estimated_total_cases"], [200, 566])
        self.assertEqual(gap["estimated_unreported_cases"], 366)

    def test_convergence_signals_flag_which_endpoint_to_trust(self):
        sig = self.block["true_burden_nowcast"]["convergence_signals"]
        # delay-adjusted (40%) exceeds crude (20%) -> deaths still resolving -> trust upper.
        self.assertEqual(sig["death_resolution"]["state"], "deaths_still_resolving")
        self.assertIn("growth", sig)
        self.assertEqual(sig["contact_coverage_pct"], 80.0)
        # signals must not manufacture a burden number.
        self.assertNotIn("estimated_total_cases", sig)

    def test_no_series_keeps_the_crude_only_band_unchanged(self):
        block = lovs_convergence.build_convergence(
            as_of="2026-06-25", confirmed=200, confirmed_deaths=40,
            contacts_under_follow_up=1000, followup_coverage_pct=80.0,
            methodology_constants=METHODOLOGY_CONSTANTS,  # no series
        )
        c = block["true_burden_nowcast"]["estimated_total_cases"]
        self.assertEqual([c["low"], c["central"], c["high"]], [300, 400, 585])  # crude band
        self.assertNotIn("delay_adjusted_anchor", c)
        self.assertNotIn("convergence_signals", block["true_burden_nowcast"])


class TestCareVsAscertainmentBand(unittest.TestCase):
    """National care-vs-ascertainment scenario. When the delay-adjusted confirmed lethality
    exceeds the historical BDBV CFR 95% high (0.40), the clearly-above-historical excess is
    attributed to care-strain (raising effective IFR -> fewer hidden infections), producing a
    downside burden reference + an excess-fatality decomposition. It is a SCENARIO, not the
    headline; it can only lower the burden, and it is dead-banded + capped so ordinary CFR
    variation and extreme values cannot whipsaw it."""

    def _build(self, confirmed, deaths, series=None):
        return lovs_convergence.build_convergence(
            as_of="2026-06-25", confirmed=confirmed, confirmed_deaths=deaths,
            contacts_under_follow_up=1000, followup_coverage_pct=80.0,
            methodology_constants=METHODOLOGY_CONSTANTS, confirmed_series=series,
        )

    # A controlled series: 100 fully-resolved old cases + 100 same-day unresolved cases, so the
    # Nishiura resolved denominator is ~100 and the delay-adjusted cCFR is ~deaths/100.
    SERIES = [{"date": "2026-01-01", "value": 100}, {"date": "2026-06-25", "value": 200}]

    def test_absent_without_series(self):
        nc = self._build(200, 40)["true_burden_nowcast"]
        self.assertNotIn("care_adjusted", nc)
        self.assertNotIn("excess_fatality_decomposition", nc)

    def test_dead_band_at_historical_lethality(self):
        # 40 deaths -> delay-adjusted cCFR 40.0% == historical high 0.40 -> excess 0 -> no adjustment.
        nc = self._build(200, 40, self.SERIES)["true_burden_nowcast"]
        ca = nc["care_adjusted"]
        self.assertEqual(ca["care_factor"], 1.0)
        self.assertEqual(ca["effective_ifr"], 0.15)
        self.assertEqual(ca["central"], nc["estimated_total_cases"]["crude_anchor"]["central"])

    def test_active_adjustment_lowers_burden(self):
        # 50 deaths -> delay-adjusted cCFR ~50% (> 0.40) -> care scenario active, burden falls.
        nc = self._build(200, 50, self.SERIES)["true_burden_nowcast"]
        ca = nc["care_adjusted"]
        self.assertLess(ca["central"], nc["estimated_total_cases"]["crude_anchor"]["central"])  # SC2
        self.assertEqual(ca["central"], 384)  # locked arithmetic (deaths_central 75 / ifr_care ~0.1955)
        self.assertGreater(ca["effective_ifr"], 0.15)
        self.assertLessEqual(ca["effective_ifr"], 0.20)

    def test_effective_ifr_is_capped(self):
        # Extreme lethality (95 deaths) must not push effective IFR past the 0.20 cap.
        nc = self._build(200, 95, self.SERIES)["true_burden_nowcast"]
        self.assertLessEqual(nc["care_adjusted"]["effective_ifr"], 0.20)
        self.assertLess(nc["care_adjusted"]["central"], nc["estimated_total_cases"]["crude_anchor"]["central"])

    def test_decomposition_is_positions_not_a_causal_split(self):
        # The block reports positions relative to the historical CFR band, in death-equivalents,
        # with NO causal 'ascertainment'/'care_attributed' labels (those overclaimed identified
        # mechanisms). When un-capped, over-central == beyond-CI + band-width by arithmetic.
        dec = self._build(200, 50, self.SERIES)["true_burden_nowcast"]["excess_fatality_decomposition"]
        self.assertEqual(dec["excess_deaths_over_historical_central"], 34)
        self.assertEqual(dec["beyond_historical_ci_deaths"], 20)          # care-scenario candidate
        self.assertEqual(dec["historical_ci_band_width_deaths"], 14)      # fixed reference = 200*(0.40-0.33)
        self.assertEqual(
            dec["excess_deaths_over_historical_central"],
            dec["beyond_historical_ci_deaths"] + dec["historical_ci_band_width_deaths"],
        )
        self.assertNotIn("ascertainment_attributed_deaths", dec)  # no category-error label
        self.assertNotIn("care_attributed_deaths", dec)

    def test_decomposition_care_leg_capped_like_care_adjusted(self):
        # Extreme lethality: the care-scenario candidate is capped at confirmed*cap_excess (0.11
        # -> 22 at n=200), consistent with care_adjusted's 0.20 IFR ceiling, not the raw excess.
        dec = self._build(200, 95, self.SERIES)["true_burden_nowcast"]["excess_fatality_decomposition"]
        self.assertEqual(dec["beyond_historical_ci_deaths"], 22)
        self.assertEqual(dec["historical_ci_band_width_deaths"], 14)  # still the fixed reference

    def test_scenario_is_a_sibling_not_a_headline_mutation(self):
        # SC5: additive. care_adjusted lives ALONGSIDE estimated_total_cases, never inside it,
        # and the headline central is still the geometric mean of the crude/delay anchors.
        nc = self._build(200, 50, self.SERIES)["true_burden_nowcast"]
        self.assertIn("care_adjusted", nc)
        self.assertNotIn("care_adjusted", nc["estimated_total_cases"])
        etc = nc["estimated_total_cases"]
        self.assertEqual(etc["central"], round((etc["low"] * etc["high"]) ** 0.5))


if __name__ == "__main__":
    unittest.main()
