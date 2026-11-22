"""Tests for lovs.vaccine_field_effectiveness.

Two kinds of test:
  * structural: the chain multiplies the way the module docstring says, bounds
    hold, assumptions are labelled, results are deterministic;
  * validation: the estimators reproduce published Zaire numbers from
    published counts before anything is applied to Bundibugyo.
"""
from __future__ import annotations

import dataclasses
import unittest

from lovs import vaccine_field_effectiveness as vfe
from lovs.vaccine_field_effectiveness import Group, ModelInputs, Param


def fixed(name: str, v: float) -> Param:
    return Param(name, "fixed", (v,), "derived", "test fixture")


def base_inputs(**over) -> ModelInputs:
    kw = dict(
        ve_zaire=fixed("ve_zaire", 0.9),
        strain_match=fixed("strain_match", 0.5),
        potency=fixed("potency", 1.0),
        durability=fixed("durability", 1.0),
        onset_days=fixed("onset_days", 10.0),
        horizon_days=fixed("horizon_days", 100.0),
        delay_to_vaccination=fixed("delay_to_vaccination", 7.0),
        tau_index=fixed("tau_index", 5.0),
        incubation=fixed("incubation", 7.0),
        tau_next=fixed("tau_next", 5.0),
        index_share=fixed("index_share", 0.5),
        pep_effect=fixed("pep_effect", 0.0),
        indirect_multiplier=fixed("indirect_multiplier", 1.0),
    )
    kw.update(over)
    return ModelInputs(**kw)


def hcw(coverage: float = 1.0, share: float = 1.0) -> Group:
    return Group("frontline_workers", "pre_exposure", fixed("coverage", coverage),
                 fixed("denominator_completeness", 1.0), fixed("case_share", share))


class ExactIntervalTests(unittest.TestCase):
    def test_clopper_pearson_known_values(self):
        # Reference values computed with scipy.stats.beta.ppf.
        lo, hi = vfe.clopper_pearson(0, 16)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.2059072142, places=8)
        lo, hi = vfe.clopper_pearson(5, 20)
        self.assertAlmostEqual(lo, 0.0865714691, places=8)
        self.assertAlmostEqual(hi, 0.4910458717, places=8)

    def test_zero_vaccinated_cases_gives_point_one_and_finite_lower(self):
        est = vfe.ve_from_counts(0, 1000, 16, 1000)
        self.assertEqual(est.point, 1.0)
        self.assertEqual(est.upper, 1.0)
        self.assertGreater(est.lower, 0.0)
        self.assertLess(est.lower, 1.0)

    def test_no_cases_is_not_estimable(self):
        with self.assertRaises(ValueError):
            vfe.ve_from_counts(0, 10, 0, 10)

    def test_screening_method_identity(self):
        # PCV == PPV means no protection.
        self.assertAlmostEqual(vfe.screening_ve(0.4, 0.4), 0.0)
        # Farrington 1993 worked form: PCV 0.5, PPV 0.9 -> VE 8/9.
        self.assertAlmostEqual(vfe.screening_ve(0.5, 0.9), 8.0 / 9.0)

    def test_screening_method_needs_coverage_to_be_informative(self):
        # Illustration: high coverage (0.765) gives a usable
        # interval from 30 cases; low coverage (0.112) does not, even at 0/30.
        hi = vfe.screening_ve_interval(10, 30, 0.765)
        self.assertAlmostEqual(hi.point, 0.846, delta=0.001)
        self.assertAlmostEqual(hi.lower, 0.656, delta=0.002)
        lo = vfe.screening_ve_interval(0, 30, 0.112)
        self.assertLess(lo.lower, 0.0)

    def test_screening_interval_brackets_point(self):
        est = vfe.screening_ve_interval(5, 20, 0.5)
        self.assertLessEqual(est.lower, est.point)
        self.assertLessEqual(est.point, est.upper)


class ParamTests(unittest.TestCase):
    def test_assumption_must_be_labelled(self):
        with self.assertRaises(ValueError):
            Param("x", "fixed", (1.0,), "assumption", "a guess")
        Param("x", "fixed", (1.0,), "assumption", "ASSUMPTION: labelled")

    def test_unknown_basis_rejected(self):
        with self.assertRaises(ValueError):
            Param("x", "fixed", (1.0,), "vibes", "x")


class ChainTests(unittest.TestCase):
    def test_chain_multiplies_as_documented(self):
        res = vfe.simulate(base_inputs(), [hcw(coverage=0.8)], draws=50)
        st = res["stages"]
        self.assertAlmostEqual(st["e0"]["p50"], 0.9)
        self.assertAlmostEqual(st["e1"]["p50"], 0.45)
        # pre-exposure timing: (100 - 10) / 100 = 0.9
        g = res["groups"]["frontline_workers"]
        self.assertAlmostEqual(g["timing"]["p50"], 0.9)
        self.assertAlmostEqual(g["e4"]["p50"], 0.45 * 0.9)
        self.assertAlmostEqual(g["e5"]["p50"], 0.45 * 0.9 * 0.8)
        self.assertAlmostEqual(st["e6"]["p50"], 0.45 * 0.9 * 0.8)

    def test_no_strain_match_means_no_protection(self):
        res = vfe.simulate(base_inputs(strain_match=fixed("strain_match", 0.0)), [hcw()], draws=50)
        self.assertEqual(res["stages"]["e6"]["p95"], 0.0)

    def test_ring_index_exposure_before_vaccination_is_not_averted(self):
        # tau_index 5 < delay 7 + onset 10, later exposure 5+7+5=17 = 17 not > 17.
        ring = Group("contacts", "ring", fixed("coverage", 1.0),
                     fixed("denominator_completeness", 1.0), fixed("case_share", 1.0))
        res = vfe.simulate(base_inputs(), [ring], draws=20)
        self.assertEqual(res["groups"]["contacts"]["timing"]["p50"], 0.0)
        # A post-exposure effect is the only route to protection then.
        res = vfe.simulate(base_inputs(pep_effect=fixed("pep_effect", 0.2)), [ring], draws=20)
        self.assertAlmostEqual(res["groups"]["contacts"]["e4"]["p50"], 0.45 * 0.2)

    def test_ring_later_generation_is_averted(self):
        ring = Group("contacts", "ring", fixed("coverage", 1.0),
                     fixed("denominator_completeness", 1.0), fixed("case_share", 1.0))
        res = vfe.simulate(base_inputs(tau_next=fixed("tau_next", 6.0)), [ring], draws=20)
        self.assertAlmostEqual(res["groups"]["contacts"]["timing"]["p50"], 0.5)

    def test_indirect_scenario_never_enters_headline(self):
        inp = base_inputs(indirect_multiplier=fixed("indirect_multiplier", 2.0))
        res = vfe.simulate(inp, [hcw(share=0.1)], draws=20)
        st = res["stages"]
        self.assertAlmostEqual(st["e6_indirect_scenario"]["p50"], 2 * st["e6"]["p50"])

    def test_case_shares_above_one_rejected(self):
        with self.assertRaises(ValueError):
            vfe.simulate(base_inputs(), [hcw(share=0.7), dataclasses.replace(hcw(share=0.7), name="b")], draws=5)

    def test_deterministic_for_seed(self):
        inp = base_inputs(strain_match=Param("strain_match", "uniform", (0.0, 1.0), "assumption",
                                             "ASSUMPTION: test"))
        a = vfe.simulate(inp, [hcw()], draws=500, seed=7)
        b = vfe.simulate(inp, [hcw()], draws=500, seed=7)
        self.assertEqual(a["stages"], b["stages"])

    def test_sensitivity_names_the_only_varying_input(self):
        inp = base_inputs(strain_match=Param("strain_match", "uniform", (0.0, 1.0), "assumption",
                                             "ASSUMPTION: test"))
        res = vfe.simulate(inp, [hcw()], draws=4000)
        self.assertEqual(list(res["sensitivity_first_order_e6"]), ["strain_match"])
        self.assertGreater(res["sensitivity_first_order_e6"]["strain_match"], 0.95)

    def test_conditional_on_is_monotone_in_strain_match(self):
        inp = base_inputs()
        rows = vfe.conditional_on(inp, [hcw()], "strain_match", [0.0, 0.5, 1.0], draws=10)
        e6 = [r["e6"]["p50"] for r in rows]
        self.assertEqual(e6, sorted(e6))
        self.assertEqual(e6[0], 0.0)


class RecordAdapterTests(unittest.TestCase):
    """Rows shaped like the SR189 per-province vaccination record."""

    def test_pre_registered_denominator_preferred(self):
        row = dict(province="Haut-Uele", vaccinated_cumulative=3388, pre_registered=4428, coverage_pct=77,
                   doses_received=4100, doses_remaining=712, doses_used=3388)
        out = vfe.coverage_from_vaccination_record(row)
        self.assertAlmostEqual(out["coverage"], 3388 / 4428)
        self.assertEqual(out["basis"], "vaccinated_cumulative / pre_registered")
        self.assertAlmostEqual(out["doses_used_share"], 3388 / 4100)

    def test_printed_rate_used_when_no_denominator(self):
        out = vfe.coverage_from_vaccination_record(dict(province="Tshopo", vaccinated_cumulative=14823,
                                                        coverage_pct=99.1))
        self.assertAlmostEqual(out["coverage"], 0.991)
        self.assertIsNone(out["doses_used_share"])

    def test_counts_without_target_are_not_estimable(self):
        out = vfe.coverage_from_vaccination_record(dict(province="Nord-Kivu", vaccinated_cumulative=4828,
                                                        doses_received=5500, doses_remaining=671))
        self.assertIsNone(out["coverage"])
        self.assertAlmostEqual(out["doses_used_share"], 4829 / 5500)


class RecordAdapterClipTests(unittest.TestCase):
    def test_ratio_above_one_is_flagged(self):
        out = vfe.coverage_from_vaccination_record(dict(province="X", vaccinated_cumulative=588, pre_registered=489))
        self.assertEqual(out["coverage"], 1.0)
        self.assertIn("clipped", out["basis"])

    def test_rate_above_hundred_is_flagged(self):
        out = vfe.coverage_from_vaccination_record(dict(province="X", coverage_pct=104.0))
        self.assertEqual(out["coverage"], 1.0)
        self.assertIn("clipped", out["basis"])


class MixtureModeTests(unittest.TestCase):
    def _group(self, m):
        return Group("list", "mixture", fixed("coverage", 1.0), fixed("denominator_completeness", 1.0),
                     fixed("case_share", 1.0), timing=fixed("ring", 0.3), pre_exposure_weight=fixed("m", m))

    def test_mixture_interpolates_pre_exposure_and_ring(self):
        # pre-exposure share (100 - 10)/100 = 0.9; ring 0.3; m 0.25 -> 0.45
        res = vfe.simulate(base_inputs(), [self._group(0.25)], draws=20)
        self.assertAlmostEqual(res["groups"]["list"]["timing"]["p50"], 0.25 * 0.9 + 0.75 * 0.3)
        self.assertAlmostEqual(res["groups"]["list"]["e4"]["p50"], 0.45 * (0.25 * 0.9 + 0.75 * 0.3))

    def test_mixture_needs_both_parts(self):
        g = Group("list", "mixture", fixed("coverage", 1.0), fixed("denominator_completeness", 1.0),
                  fixed("case_share", 1.0), timing=fixed("ring", 0.3))
        with self.assertRaises(ValueError):
            vfe.simulate(base_inputs(), [g], draws=5)


class ValidationAgainstPublishedZaireTests(unittest.TestCase):
    """Reproduce published Zaire (EBOV) numbers before any BDBV use."""

    def test_reproduces_who_inrb_2019_ve_10_days(self):
        # WHO/INRB 12 Apr 2019: 15 cases among 93,965 vaccinated (onset >= 10 d);
        # 880 cases among unvaccinated at risk, attack rate 0.656%. Published
        # VE 97.5% (95.8-98.5) from the crude attack rates; the report's Poisson
        # approximation gives 92.4-99.1 instead.
        est = vfe.ve_from_counts(15, 93965, 880, 880 / 0.00656)
        self.assertAlmostEqual(est.point, 0.975, delta=0.002)
        self.assertAlmostEqual(est.lower, 0.958, delta=0.003)
        self.assertAlmostEqual(est.upper, 0.985, delta=0.003)

    def test_reproduces_who_inrb_2019_ve_any_onset_point(self):
        # Same report, all 71 vaccinee cases: 88.1% (79.9-92.9) by the report's
        # stated Poisson approximation (which also gives 92.4-99.1 for the
        # >= 10 d figure). The exact conditional interval does not reproduce the
        # Poisson intervals, so only the point is pinned.
        est = vfe.ve_from_counts(71, 93965, 880, 880 / 0.00656)
        self.assertAlmostEqual(est.point, 0.881, delta=0.005)
        self.assertLess(est.lower, est.point)

    def test_guinea_trial_point_and_bounded_lower_limit(self):
        # Henao-Restrepo 2017 Table 3: 0/2108 vs 16/3075, VE 100% (68.9-100).
        # The trial's interval is cluster-adjusted (Cox, ring frailty); the
        # unclustered exact interval is wider at the bottom (0.622). Pin that
        # gap so a change in method is noticed.
        est = vfe.ve_from_counts(0, 2108, 16, 3075)
        self.assertEqual(est.point, 1.0)
        self.assertAlmostEqual(est.lower, 0.622, delta=0.003)
        self.assertLess(est.lower, 0.689)
        # Interim 2015: 0 vs 16, 2014 vaccinated vs 2380 eligible; published 74.7.
        est15 = vfe.ve_from_counts(0, 2014, 16, 2380)
        self.assertAlmostEqual(est15.lower, 0.694, delta=0.003)

    def test_guinea_trial_no_effect_in_first_ten_days(self):
        # Onset days 0-9: 20/3232 immediate vs 21/3096 delayed (Table 3).
        est = vfe.ve_from_counts(20, 3232, 21, 3096)
        self.assertAlmostEqual(est.point, 0.088, delta=0.005)
        self.assertLess(est.lower, 0.0)

    def test_guinea_trial_vaccinee_first_ten_days(self):
        # Vaccinee-only contrast, Table 4: 11/2111 vaccinated vs 21/3096 delayed
        # The all-eligible 20/3232 contrast above is not a
        # vaccinee VE.
        est = vfe.ve_from_counts(11, 2111, 21, 3096)
        self.assertAlmostEqual(est.point, 0.232, delta=0.002)
        self.assertAlmostEqual(est.lower, -0.667, delta=0.003)
        self.assertAlmostEqual(est.upper, 0.666, delta=0.003)

    # Highest-weight parameter set of the ring-timing calibration: rejection
    # sampling on the two interval targets below, weighted by the Poisson
    # likelihood of the trial's 0 late vaccinee cases (0/2108 vs 16/3075).
    ROW = dict(index_share=0.3044, onset_days=3.2521, pep_effect=0.5312, ve_post=0.9902,
               inc_mean=8.4964, inc_sd=4.7825)

    def _profile(self, delay, window, draws=40000):
        def P(n, k, a):
            return Param(n, k, a, "derived", "calibration fixture")
        r = self.ROW
        return vfe.ring_cohort_profile(r["ve_post"], P("d", "gamma_mean_sd", delay), P("o", "fixed", (r["onset_days"],)),
                                       P("t", "gamma_mean_sd", (3.9, 3.0)),
                                       P("i", "gamma_mean_sd", (r["inc_mean"], r["inc_sd"])),
                                       P("s", "fixed", (r["index_share"],)), draws=draws,
                                       pep_effect=r["pep_effect"], window=window)

    def test_calibrated_row_meets_its_targets(self):
        # Targets of run 4: trial vaccinee days 0-9 VE in (-0.67, 0.67) under the
        # Guinea delay (mean 9.7, sd 5.3); WHO/INRB early share 56/71 in
        # (0.676, 0.877) under the DRC delay (mean 7.6, sd 5.5).
        g = self._profile((9.7, 5.3), (0.0, 10.0))
        d = self._profile((7.6, 5.5), (3.0, 10.0))
        self.assertTrue(-0.67 <= g["ve_window"] <= 0.67, g)
        self.assertTrue(0.676 <= d["early_share"] <= 0.877, d)

    def test_known_limits_of_the_ring_structure(self):
        # Recorded limitations of the ring structure. If any of these starts failing,
        # the structure has changed and the note must be revisited:
        #  - the trial's 0 late vaccinee cases stay unlikely (Poisson P(0) < 0.5);
        #  - the full DRC early share (Muyembe 380/434, >= 0.84) is not reached.
        import math
        g = self._profile((9.7, 5.3), (0.0, 10.0))
        lam = 16 * (2108 / 3075) * (1 - g["ve_late"])
        self.assertLess(math.exp(-lam), 0.5)
        d = self._profile((7.6, 5.5), (3.0, 10.0))
        self.assertLess(d["early_share"], 0.84)

if __name__ == "__main__":
    unittest.main()
