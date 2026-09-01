"""Tests for the CFR ascertainment instrument.

The load-bearing tests here are the refusals. An instrument that turns a
twenty-two point CFR gap into an implied case count is one careless line away
from publishing a fabricated body count, so the suite spends most of its effort
on the paths where the module must decline: a three-case province that must be
flagged rather than ranked, a window whose endpoint sits inside a reporting
stall, a reference CFR that implies no shortfall at all, and a call site that
tried to inherit a reference province instead of naming one.

`test_synthetic_province_implied_range_is_hand_checked` pins the arithmetic
against numbers worked out on paper, so a refactor cannot quietly change what
the headline range means.
"""
from __future__ import annotations

import datetime as dt
import math
import pathlib
import unittest

from lovs.forecast import cfrascertainment as ca
from lovs.forecast import opsforecast as of

REPO = pathlib.Path(__file__).resolve().parent.parent
SERIES = REPO / "data" / "operational-series-2026-09-01.json"

# The reporting stall in the frozen extract: the province split stopped tracking
# the national line for four data days in August.
STALL_DAYS = ("2026-08-06", "2026-08-07", "2026-08-09", "2026-08-10")


def synthetic_rows(
    n_days: int = 30,
    *,
    subject_confirmed_per_day: int = 10,
    subject_deaths_per_day: int = 8,
    reference_confirmed_per_day: int = 250,
    reference_deaths_per_day: int = 100,
    subject_isolated: int = 60,
    reference_isolated: int = 60,
) -> list[dict]:
    """Two provinces with exactly linear growth, so every rate is a round number.

    Reference runs at a flat 40 percent CFR and Subject at a flat 80 percent, on
    both the cumulative and the interval measure, which makes the implied range
    computable by hand and any drift in the arithmetic visible immediately.
    """
    rows = []
    start = dt.date(2026, 1, 1)
    for i in range(n_days):
        sc = subject_confirmed_per_day * (i + 1)
        sd = subject_deaths_per_day * (i + 1)
        rc = reference_confirmed_per_day * (i + 1)
        rd = reference_deaths_per_day * (i + 1)
        rows.append({
            "sitrep": f"{i:03d}",
            "data_as_of": (start + dt.timedelta(days=i)).isoformat(),
            "confirmed_total": sc + rc,
            "confirmed_deaths_total": sd + rd,
            "isolation_by_province": {
                "Subject": subject_isolated,
                "Reference": reference_isolated,
                "Total": subject_isolated + reference_isolated,
            },
            "province_split": {
                "Reference": {"confirmed": rc, "confirmed_deaths": rd},
                "Subject": {"confirmed": sc, "confirmed_deaths": sd},
            },
        })
    return rows


class WilsonTests(unittest.TestCase):
    def test_endpoints_satisfy_the_score_equation(self):
        """Check the interval against its own definition, not against my arithmetic.

        A Wilson endpoint is a root of |p_hat - p| = z * sqrt(p(1-p)/n). Plugging
        the returned endpoints back into that equation tests the implementation
        against the definition rather than against a number I typed.
        """
        for k, n in ((1, 3), (8, 19), (93, 209), (546, 800), (2212, 4911)):
            lo, hi = ca.wilson(k, n)
            p_hat = k / n
            for p in (lo, hi):
                if p in (0.0, 1.0):
                    continue
                self.assertAlmostEqual(
                    abs(p_hat - p),
                    ca.Z95 * math.sqrt(p * (1 - p) / n),
                    places=9,
                    msg=f"k={k} n={n} endpoint={p}",
                )

    def test_interval_stays_inside_the_unit_range_at_zero_deaths(self):
        lo, hi = ca.wilson(0, 3)
        self.assertGreaterEqual(lo, 0.0)
        self.assertAlmostEqual(lo, 0.0, places=12)
        self.assertLess(hi, 1.0)
        # The normal approximation would put this endpoint below zero and let a
        # nonsense province look scoreable; that is the failure Wilson avoids.
        self.assertGreater(hi, 0.5)

    def test_no_trials_is_an_error_not_a_nan(self):
        with self.assertRaises(ValueError):
            ca.wilson(0, 0)


class DayAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.rows = of.load_rows(SERIES)
        self.days = ca.province_days(self.rows)

    def test_duplicate_data_day_is_counted_once(self):
        """2026-08-10 appears in two SitReps; counting it twice double-weights a day."""
        raw = [r for r in self.rows if r.get("province_split")]
        self.assertEqual(len(raw), 81)
        self.assertEqual(len(self.days), 80)
        dates = [d.date for d in self.days]
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(dates, sorted(dates))

    def test_reporting_stall_is_flagged_not_dropped(self):
        rec = ca.reconciliation(self.days)
        self.assertEqual(rec.unreconciled_days, STALL_DAYS)
        # Flagged, but still present: a hidden hole is worse than a marked one.
        present = {d.date.isoformat() for d in self.days}
        for day in STALL_DAYS:
            self.assertIn(day, present)
        self.assertEqual(rec.n_reconciled, rec.n_days - len(STALL_DAYS))

    def test_isolation_total_row_is_not_treated_as_a_province(self):
        loads = ca.isolation_load(self.rows, self.days, as_of="2026-08-05")
        self.assertNotIn("Total", [l.province for l in loads])

    def test_as_of_defaults_to_the_last_data_day_and_never_to_a_clock(self):
        self.assertEqual(self.days[-1].date, dt.date(2026, 8, 28))
        default = ca.cumulative_cfr(self.days)
        explicit = ca.cumulative_cfr(self.days, as_of="2026-08-28")
        self.assertEqual(default, explicit)

    def test_unknown_as_of_day_raises(self):
        with self.assertRaises(ValueError):
            ca.cumulative_cfr(self.days, as_of="2026-12-25")


class PrecisionGateTests(unittest.TestCase):
    def setUp(self):
        self.days = ca.province_days(of.load_rows(SERIES))
        self.table = {c.province: c for c in ca.cumulative_cfr(self.days)}

    def test_small_denominator_is_flagged_rather_than_silently_scored(self):
        """Sud-Kivu has three confirmed cases. Its CFR must not be ranked as a number."""
        thin = self.table["Sud-Kivu"]
        self.assertEqual(thin.confirmed, 3)
        self.assertFalse(thin.precision_ok)
        self.assertIn("denominator too small", thin.note)
        self.assertGreater(thin.ci_width_percent, ca.DEFAULT_MAX_CI_WIDTH_PP)
        # Flagged, not dropped.
        self.assertIn("Sud-Kivu", self.table)

    def test_large_denominators_pass_the_gate(self):
        for name in ("Ituri", "Nord-Kivu", "Haut-Uele"):
            self.assertTrue(self.table[name].precision_ok, name)

    def test_gate_moves_with_its_parameter(self):
        strict = {c.province: c for c in ca.cumulative_cfr(self.days, max_ci_width_pp=5.0)}
        self.assertTrue(strict["Ituri"].precision_ok)
        self.assertFalse(strict["Nord-Kivu"].precision_ok)

    def test_zero_denominator_does_not_divide(self):
        rows = synthetic_rows(3)
        for row in rows:
            row["province_split"]["Empty"] = {"confirmed": 0, "confirmed_deaths": 0}
        days = ca.province_days(rows)
        empty = [c for c in ca.cumulative_cfr(days) if c.province == "Empty"][0]
        self.assertFalse(empty.precision_ok)
        self.assertTrue(math.isnan(empty.cfr_percent))


class IntervalCfrTests(unittest.TestCase):
    def setUp(self):
        self.days = ca.province_days(of.load_rows(SERIES))

    def test_flat_province_yields_no_rate(self):
        """Sud-Kivu added no cases; a rate over a zero denominator is not a rate."""
        got = ca.interval_cfr(self.days, "Sud-Kivu", window_obs=14)
        self.assertFalse(got.usable)
        self.assertIsNone(got.cfr_percent)
        self.assertIn("did not increase", got.note)

    def test_window_ending_inside_the_stall_is_refused(self):
        got = ca.interval_cfr(self.days, "Ituri", window_obs=14, as_of="2026-08-09")
        self.assertFalse(got.usable)
        self.assertIn("does not reconcile", got.note)

    def test_window_spanning_the_stall_between_clean_endpoints_is_allowed(self):
        """A cumulative difference does not care that the counter stalled in between."""
        got = ca.interval_cfr(self.days, "Ituri", window_obs=28, as_of="2026-08-28")
        self.assertTrue(got.usable, got.note)
        self.assertGreater(got.new_confirmed, 0)

    def test_window_longer_than_the_history_is_refused(self):
        days = ca.province_days(synthetic_rows(10))
        got = ca.interval_cfr(days, "Subject", window_obs=14)
        self.assertFalse(got.usable)
        self.assertIn("need 14", got.note)

    def test_reclassification_makes_no_rate(self):
        rows = synthetic_rows(20)
        rows[-1]["province_split"]["Subject"] = {"confirmed": 5, "confirmed_deaths": 4}
        rows[-1]["confirmed_total"] = 5 + rows[-1]["province_split"]["Reference"]["confirmed"]
        rows[-1]["confirmed_deaths_total"] = 4 + rows[-1]["province_split"]["Reference"]["confirmed_deaths"]
        days = ca.province_days(rows)
        got = ca.interval_cfr(days, "Subject", window_obs=14)
        self.assertFalse(got.usable)
        self.assertIn("reclassification", got.note)

    def test_linear_synthetic_interval_matches_its_construction(self):
        days = ca.province_days(synthetic_rows(30))
        self.assertAlmostEqual(
            ca.interval_cfr(days, "Reference", window_obs=14).cfr_percent, 40.0, places=9
        )
        self.assertAlmostEqual(
            ca.interval_cfr(days, "Subject", window_obs=14).cfr_percent, 80.0, places=9
        )


class DivergenceTests(unittest.TestCase):
    def setUp(self):
        self.days = ca.province_days(of.load_rows(SERIES))

    def test_real_cumulative_gap_converges(self):
        d = ca.divergence(self.days, "Nord-Kivu", "Ituri", basis="cumulative")
        self.assertEqual(d.verdict, "converging")
        self.assertLess(d.last_gap_pp, d.first_gap_pp)

    def test_real_interval_gap_is_not_resolved_by_this_window(self):
        """The maturation-free measure must not inherit the cumulative verdict."""
        d = ca.divergence(self.days, "Nord-Kivu", "Ituri", basis="interval")
        self.assertEqual(d.verdict, "holding")
        self.assertIn("do not agree in sign", d.reason)

    def test_the_cost_of_the_guards_is_reported_not_hidden(self):
        """A shorter series nobody questions is how a silent drop survives review."""
        d = ca.divergence(self.days, "Nord-Kivu", "Ituri", basis="interval")
        self.assertEqual(d.n_candidates, 66)
        self.assertEqual(d.n_points, 58)
        self.assertEqual(d.n_refused, 8)
        cum = ca.divergence(self.days, "Nord-Kivu", "Ituri", basis="cumulative")
        self.assertEqual(cum.n_refused, 0)

    def test_convergence_of_the_cumulative_gap_is_mostly_the_reference_moving(self):
        """A closing gap is not the same event as the outlier coming down."""
        dc = ca.decompose_gap(self.days, "Nord-Kivu", "Ituri")
        self.assertLess(dc.gap_change_pp, 0.0)
        self.assertGreater(dc.high_last_pp, dc.high_first_pp)  # Nord-Kivu rose
        self.assertLess(dc.low_contribution_pp, dc.gap_change_pp)

    def test_flat_gap_is_held_not_called(self):
        days = ca.province_days(synthetic_rows(30))
        d = ca.divergence(days, "Subject", "Reference", basis="cumulative")
        self.assertEqual(d.verdict, "holding")
        self.assertAlmostEqual(d.first_gap_pp, 40.0, places=9)
        self.assertAlmostEqual(d.last_gap_pp, 40.0, places=9)

    def test_a_real_widening_gap_is_called(self):
        rows = synthetic_rows(30)
        for i, row in enumerate(rows):
            # Subject's deaths climb steadily against a fixed case ladder, so its
            # CFR pulls away from Reference's flat 40 percent.
            confirmed = 100 * (i + 1)
            deaths = int(round(confirmed * (0.40 + 0.015 * i)))
            row["province_split"]["Subject"] = {
                "confirmed": confirmed, "confirmed_deaths": deaths
            }
            ref = row["province_split"]["Reference"]
            row["confirmed_total"] = confirmed + ref["confirmed"]
            row["confirmed_deaths_total"] = deaths + ref["confirmed_deaths"]
        days = ca.province_days(rows)
        d = ca.divergence(days, "Subject", "Reference", basis="cumulative")
        self.assertEqual(d.verdict, "widening")
        self.assertGreater(d.slope_full_pp_per_30d, d.hold_band_pp_per_30d)

    def test_unknown_basis_is_an_error(self):
        with self.assertRaises(ValueError):
            ca.divergence(self.days, "Nord-Kivu", "Ituri", basis="vibes")


class ImpliedUnconfirmedTests(unittest.TestCase):
    def setUp(self):
        self.days = ca.province_days(of.load_rows(SERIES))

    def test_synthetic_province_implied_range_is_hand_checked(self):
        """Reference sits at a flat 40 percent, so the band collapses to a point.

        Subject ends at 300 confirmed and 240 deaths. 240 / 0.40 = 600 implied
        cases, so 300 implied unconfirmed and a 50 percent confirmed share. All
        four figures are worked out on paper, not read back off the module.
        """
        days = ca.province_days(synthetic_rows(30))
        got = ca.implied_unconfirmed(days, "Subject", reference_province="Reference")
        self.assertEqual(got.observed_confirmed, 300)
        self.assertEqual(got.observed_deaths, 240)
        self.assertAlmostEqual(got.observed_cfr_percent, 80.0, places=9)
        self.assertAlmostEqual(got.reference_low_percent, 40.0, places=9)
        self.assertAlmostEqual(got.reference_high_percent, 40.0, places=9)
        self.assertEqual(got.implied_total_low, 600)
        self.assertEqual(got.implied_total_high, 600)
        self.assertEqual(got.implied_unconfirmed_low, 300)
        self.assertEqual(got.implied_unconfirmed_high, 300)
        self.assertAlmostEqual(got.confirmed_share_low, 50.0, places=9)
        self.assertAlmostEqual(got.confirmed_share_high, 50.0, places=9)

    def test_lower_reference_cfr_implies_the_larger_total(self):
        days = ca.province_days(synthetic_rows(30))
        got = ca.implied_unconfirmed(days, "Subject", band=(40.0, 80.0))
        self.assertEqual(got.implied_total_low, 300)   # 240 / 0.80
        self.assertEqual(got.implied_total_high, 600)  # 240 / 0.40
        self.assertLessEqual(got.implied_total_low, got.implied_total_high)

    def test_reference_at_or_above_the_observed_cfr_implies_no_shortfall(self):
        """A negative shortfall is not a finding; it is a sign the assumption is spent."""
        days = ca.province_days(synthetic_rows(30))
        got = ca.implied_unconfirmed(days, "Subject", band=(80.0, 95.0))
        self.assertEqual(got.implied_unconfirmed_low, 0)
        self.assertEqual(got.implied_total_low, got.observed_confirmed)
        self.assertTrue(
            any("no missing confirmations" in c for c in got.caveats), got.caveats
        )

    def test_reference_must_be_named_at_the_call_site(self):
        days = ca.province_days(synthetic_rows(30))
        with self.assertRaises(ValueError):
            ca.implied_unconfirmed(days, "Subject")
        with self.assertRaises(ValueError):
            ca.implied_unconfirmed(
                days, "Subject", reference_province="Reference", band=(40.0, 50.0)
            )

    def test_zero_reference_cfr_is_refused(self):
        days = ca.province_days(synthetic_rows(30))
        with self.assertRaises(ValueError):
            ca.implied_unconfirmed(days, "Subject", band=(0.0, 50.0))

    def test_output_carries_its_conditional_and_its_prohibitions(self):
        got = ca.implied_unconfirmed(
            self.days, "Nord-Kivu", reference_province="Ituri"
        )
        self.assertTrue(got.is_inference)
        self.assertTrue(got.conditional.startswith("IF "))
        self.assertIn("THEN", got.conditional)
        joined = " ".join(got.caveats).lower()
        self.assertIn("not an observation", joined)
        self.assertIn("incidence", joined)

    def test_real_range_is_a_range_and_leaves_room_for_unconfirmed_cases(self):
        got = ca.implied_unconfirmed(
            self.days, "Nord-Kivu", reference_province="Ituri"
        )
        self.assertLess(got.implied_unconfirmed_low, got.implied_unconfirmed_high)
        self.assertGreater(got.implied_unconfirmed_low, 0)
        self.assertLess(got.confirmed_share_high, 100.0)

    def test_thin_province_cannot_be_used_as_a_reference(self):
        with self.assertRaises(ValueError):
            ca.reference_band(ca.province_days(synthetic_rows(3)), "Nowhere")


class DiscriminatorTests(unittest.TestCase):
    def setUp(self):
        self.rows = of.load_rows(SERIES)
        self.days = ca.province_days(self.rows)

    def test_isolation_denominator_gate_refuses_thin_provinces(self):
        loads = {l.province: l for l in ca.isolation_load(self.rows, self.days)}
        self.assertFalse(loads["Sud-Kivu"].usable)
        self.assertIn("below the", loads["Sud-Kivu"].note)
        self.assertTrue(loads["Ituri"].usable)
        self.assertTrue(loads["Nord-Kivu"].usable)

    def test_the_two_readings_make_different_predictions(self):
        d = ca.discriminator(
            self.rows, self.days, subject="Nord-Kivu", reference="Ituri"
        )
        self.assertNotEqual(d.ascertainment_predicts, d.severity_predicts)
        self.assertIn("line list", d.settles_it)
        self.assertTrue(d.caveats)

    def test_the_independent_observable_is_computed_not_asserted(self):
        d = ca.discriminator(
            self.rows, self.days, subject="Nord-Kivu", reference="Ituri"
        )
        self.assertIsNotNone(d.ratio_of_ratios)
        self.assertAlmostEqual(
            d.ratio_of_ratios, d.subject_ratio / d.reference_ratio, places=9
        )

    def test_a_province_with_no_isolation_excess_does_not_lean_ascertainment(self):
        """The observable must be able to come back against the hypothesis."""
        rows = synthetic_rows(30, subject_isolated=1, reference_isolated=10_000)
        days = ca.province_days(rows)
        d = ca.discriminator(rows, days, subject="Subject", reference="Reference")
        self.assertLess(d.ratio_of_ratios, 1.0)
        self.assertIn("does not get one here", d.leaning)


class ReportTests(unittest.TestCase):
    def test_report_is_deterministic(self):
        self.assertEqual(ca.report(SERIES), ca.report(SERIES))

    def test_report_honours_an_earlier_as_of_and_has_no_clock(self):
        early = ca.report(SERIES, as_of="2026-07-01")
        late = ca.report(SERIES)
        self.assertEqual(early["as_of"], "2026-07-01")
        self.assertEqual(late["as_of"], "2026-08-28")
        early_cfr = {c.province: c.confirmed for c in early["cfr_table"]}
        late_cfr = {c.province: c.confirmed for c in late["cfr_table"]}
        self.assertLess(early_cfr["Ituri"], late_cfr["Ituri"])

    def test_rendered_report_leads_the_estimate_with_its_status(self):
        text = ca._render(ca.report(SERIES))
        self.assertIn("IMPLIED UNCONFIRMED -- an inference, not an observation", text)
        self.assertIn("[NOT SCOREABLE]", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
