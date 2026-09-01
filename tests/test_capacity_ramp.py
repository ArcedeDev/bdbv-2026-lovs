"""Tests for the response-capacity ramp library.

The load-bearing test here is `SyntheticMilestoneTests`: a twelve-observation
series built by hand, with every milestone worked out on paper before the code
ran. It is written against numbers a reader can re-derive from the fixture in
the test itself, not against whatever the module currently returns, so a silent
regression in the milestone logic -- an off-by-one in either clock, `>` where
`>=` belongs, a hold counted in days instead of observations, a crossing quietly
placed inside a reporting gap -- fails here rather than shipping as a plausible
looking table.

The second guarantee is `DocumentationTests`: every table and note in
`docs/response-capacity-ramp.md` is regenerated and compared. A figure typed
into that document by hand, or left behind when the data moved, fails the suite.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from lovs.forecast import capacityramp as cr
from lovs.forecast import opsforecast as of

REPO = pathlib.Path(__file__).resolve().parent.parent
SERIES = REPO / "data" / "operational-series-2026-09-01.json"
DOC = REPO / "docs" / "response-capacity-ramp.md"

# --- the hand-built fixture ---------------------------------------------------
# declaration 2026-01-01. The pillar is first reported on 01-05, so the two
# clocks differ by exactly 4 days everywhere below.
#
#   01-05  10.0   first observation, lag 4
#   01-06  20.0
#   01-10  55.0   first >= 50. Preceded by a 4-day gap: the crossing is
#                 somewhere in 01-07..01-10 and must NOT be dated inside it.
#   01-11  40.0   lost it again -> 01-10 was a touch, not a milestone
#   01-12  75.0   first >= 70, exactly one day after 01-11 -> exact
#   01-13  60.0
#   01-14  50.0   EXACTLY the threshold: "at or above" must count it
#   01-15  51.0
#   01-16  80.0
#   01-17  85.0
#   01-18  90.0
#   01-19  65.0   below 70 again
SYNTH = [
    ("2026-01-05", 10.0),
    ("2026-01-06", 20.0),
    ("2026-01-10", 55.0),
    ("2026-01-11", 40.0),
    ("2026-01-12", 75.0),
    ("2026-01-13", 60.0),
    ("2026-01-14", 50.0),
    ("2026-01-15", 51.0),
    ("2026-01-16", 80.0),
    ("2026-01-17", 85.0),
    ("2026-01-18", 90.0),
    ("2026-01-19", 65.0),
]
SYNTH_DECL = dt.date(2026, 1, 1)

RISING_SPEC = cr.PillarSpec(
    pillar="synthetic rising",
    metric="synth",
    direction=cr.RISING,
    thresholds=(50.0, 70.0, 95.0),
    unit="percent",
    scale_free=True,
    threshold_rationale="fixture",
    caveat="fixture",
    hold_n=3,
)


def synth_rows(pairs=SYNTH, metric="synth"):
    return [{"data_as_of": day, metric: value} for day, value in pairs]


def synth_library(spec=RISING_SPEC, pairs=SYNTH, as_of=None, **kw):
    return cr.library(
        synth_rows(pairs, spec.metric),
        outbreak="SYNTH",
        declaration=SYNTH_DECL,
        as_of=as_of,
        pillars=(spec,),
        **kw,
    )


class SyntheticMilestoneTests(unittest.TestCase):
    """Every expected value below was worked out from the fixture by hand."""

    def setUp(self):
        self.lib = synth_library()

    def test_first_reach_after_a_gap_is_the_observed_date_with_its_window(self):
        c = self.lib.curve("synth", 50.0)
        self.assertTrue(c.reached)
        self.assertEqual(c.first_reach_date, "2026-01-10")
        self.assertEqual(c.days_to_first_reach_declaration, 9)   # 01-01 -> 01-10
        self.assertEqual(c.days_to_first_reach_pillar, 5)        # 01-05 -> 01-10
        self.assertEqual(c.first_reach_window_days, 4)           # 01-06 -> 01-10
        self.assertEqual(c.first_reach_bound, "interval")

    def test_a_single_touch_is_not_the_milestone(self):
        """01-10 reaches 50 and 01-11 loses it. The hold starts on 01-12."""
        c = self.lib.curve("synth", 50.0)
        self.assertTrue(c.held)
        self.assertEqual(c.hold_start_date, "2026-01-12")
        self.assertEqual(c.hold_confirmed_date, "2026-01-14")  # start + hold_n - 1
        self.assertEqual(c.days_to_held_declaration, 11)
        self.assertEqual(c.days_to_held_pillar, 7)
        self.assertEqual(c.touch_episodes_before_hold, 1)
        self.assertNotEqual(c.first_reach_date, c.hold_start_date)

    def test_a_value_exactly_on_the_threshold_counts(self):
        """01-14 is exactly 50.0. `>` instead of `>=` would break the run."""
        c = self.lib.curve("synth", 50.0)
        self.assertEqual(c.failures_after_hold, 0)
        self.assertEqual(c.observations_after_hold, 8)  # 01-12 .. 01-19

    def test_exact_first_reach_when_there_is_no_gap(self):
        c = self.lib.curve("synth", 70.0)
        self.assertEqual(c.first_reach_date, "2026-01-12")
        self.assertEqual(c.first_reach_window_days, 1)
        self.assertEqual(c.first_reach_bound, "exact")
        self.assertEqual(c.days_to_first_reach_declaration, 11)
        self.assertEqual(c.days_to_first_reach_pillar, 7)

    def test_hold_and_post_hold_failures_are_both_reported(self):
        c = self.lib.curve("synth", 70.0)
        self.assertEqual(c.hold_start_date, "2026-01-16")
        self.assertEqual(c.days_to_held_declaration, 15)
        self.assertEqual(c.days_to_held_pillar, 11)
        self.assertEqual(c.touch_episodes_before_hold, 1)     # 01-12 alone
        self.assertEqual(c.observations_after_hold, 4)        # 01-16 .. 01-19
        self.assertEqual(c.failures_after_hold, 1)            # 01-19 at 65
        self.assertFalse(c.retained_at_last_observation)

    def test_a_threshold_never_reached_reports_none_not_zero(self):
        """A missing milestone must be absent, never a zero that reads as day 0."""
        c = self.lib.curve("synth", 95.0)
        self.assertFalse(c.reached)
        self.assertFalse(c.held)
        self.assertIsNone(c.first_reach_date)
        self.assertIsNone(c.days_to_first_reach_declaration)
        self.assertIsNone(c.days_to_held_declaration)
        self.assertEqual(c.first_reach_bound, "not_reached")

    def test_hold_length_is_load_bearing(self):
        """hold_n=6 must not find the 4-observation run that hold_n=3 finds."""
        strict = synth_library(dataclasses.replace(RISING_SPEC, hold_n=6))
        self.assertTrue(self.lib.curve("synth", 70.0).held)
        self.assertFalse(strict.curve("synth", 70.0).held)

    def test_the_two_clocks_differ_by_the_observation_lag(self):
        for c in self.lib.curves:
            if c.first_reach_bound in ("exact", "interval"):
                self.assertEqual(
                    c.days_to_first_reach_declaration - c.days_to_first_reach_pillar,
                    c.observation_lag_days,
                    c.threshold,
                )

    def test_plateau_is_measured_from_the_terminal_hold(self):
        p = self.lib.summary("synth")
        self.assertEqual(p.terminal_threshold, 70.0)
        self.assertEqual(p.plateau_from, "2026-01-16")
        self.assertEqual(p.plateau_n, 4)                     # 80, 85, 90, 65
        self.assertEqual(p.plateau_mean, 80.0)
        self.assertEqual(p.ramp_from, "2026-01-05")
        self.assertEqual(p.ramp_to, "2026-01-16")
        self.assertEqual(p.ramp_days, 11)


class LeftCensoringTests(unittest.TestCase):
    """A pillar already functional the first time anyone reports it."""

    def setUp(self):
        self.lib = synth_library(
            pairs=[("2026-01-10", 88.0), ("2026-01-11", 89.0), ("2026-01-12", 90.0),
                   ("2026-01-13", 91.0), ("2026-01-14", 92.0)]
        )

    def test_first_reach_is_an_upper_bound_not_a_measurement(self):
        c = self.lib.curve("synth", 50.0)
        self.assertEqual(c.first_reach_bound, "left_censored")
        self.assertEqual(c.days_to_first_reach_declaration, 9)  # 01-01 -> 01-10
        self.assertEqual(c.observation_lag_days, 9)
        self.assertEqual(c.days_to_first_reach_pillar, 0)
        self.assertTrue(any("upper bound" in n for n in c.notes))

    def test_no_ramp_is_claimed_where_none_was_observed(self):
        p = self.lib.summary("synth")
        self.assertIsNone(p.ramp_slope_per_day)
        self.assertTrue(any("no ramp was observed" in n for n in p.notes))


class FallingDirectionTests(unittest.TestCase):
    """A metric where improvement is downward must not be measured upside down."""

    SPEC = cr.PillarSpec(
        pillar="synthetic falling",
        metric="synth",
        direction=cr.FALLING,
        thresholds=(30.0, 20.0),
        unit="percent",
        scale_free=True,
        threshold_rationale="fixture",
        caveat="fixture",
        hold_n=3,
    )
    PAIRS = [
        ("2026-01-05", 45.0),
        ("2026-01-06", 40.0),
        ("2026-01-07", 29.0),   # first <= 30
        ("2026-01-08", 33.0),   # lost it
        ("2026-01-09", 30.0),   # exactly on the threshold
        ("2026-01-10", 28.0),
        ("2026-01-11", 26.0),   # 01-09..01-11 is the hold
    ]

    def test_milestones_run_downward(self):
        lib = synth_library(self.SPEC, self.PAIRS)
        c = lib.curve("synth", 30.0)
        self.assertEqual(c.first_reach_date, "2026-01-07")
        self.assertEqual(c.hold_start_date, "2026-01-09")
        self.assertEqual(c.touch_episodes_before_hold, 1)
        self.assertTrue(c.retained_at_last_observation)
        self.assertFalse(lib.curve("synth", 20.0).reached)

    def test_stringency_order_is_direction_aware(self):
        lib = synth_library(self.SPEC, self.PAIRS)
        self.assertEqual([c.threshold for c in lib.curves], [30.0, 20.0])

    def test_unknown_direction_refuses_rather_than_guesses(self):
        with self.assertRaises(ValueError):
            cr._meets(1.0, 2.0, "sideways")


class GapAndCensoringTests(unittest.TestCase):
    def test_a_hold_that_spans_a_gap_says_so(self):
        pairs = [("2026-01-05", 10.0), ("2026-01-06", 80.0), ("2026-01-07", 81.0),
                 ("2026-01-25", 82.0)]
        lib = synth_library(
            dataclasses.replace(RISING_SPEC, thresholds=(70.0,)), pairs
        )
        c = lib.curve("synth", 70.0)
        self.assertTrue(c.held)
        self.assertEqual(c.hold_max_gap_days, 18)
        self.assertEqual(c.hold_span_days, 19)
        self.assertTrue(any("reporting gap" in n for n in c.notes))

    def test_reporting_stopped_is_judged_against_the_pillar_own_cadence(self):
        """A four-day silence on a daily series is a pause; a month is a stop."""
        pairs = [(f"2026-01-{d:02d}", 80.0) for d in range(5, 16)]
        paused = synth_library(pairs=pairs, as_of=dt.date(2026, 1, 19))
        stopped = synth_library(pairs=pairs, as_of=dt.date(2026, 2, 15))
        self.assertFalse(paused.summary("synth").reporting_stopped)
        self.assertTrue(stopped.summary("synth").reporting_stopped)

    def test_as_of_truncates_the_record(self):
        """The as-of date is a parameter. Nothing may leak in from after it."""
        early = synth_library(as_of=dt.date(2026, 1, 13))
        self.assertEqual(early.summary("synth").last_observation_date, "2026-01-13")
        self.assertFalse(early.curve("synth", 70.0).held)   # holds only on 01-16
        self.assertTrue(synth_library().curve("synth", 70.0).held)

    def test_a_degrading_plateau_is_not_called_a_plateau(self):
        pairs = (
            [(f"2026-01-{d:02d}", 60.0 + d) for d in range(5, 12)]      # ramp up
            + [(f"2026-01-{d:02d}", 100.0 - 2 * (d - 12)) for d in range(12, 26)]
        )
        lib = synth_library(
            dataclasses.replace(RISING_SPEC, thresholds=(70.0,)), pairs
        )
        p = lib.summary("synth")
        self.assertLess(p.plateau_drift, 0)
        self.assertTrue(any("DEGRADING" in n for n in p.notes))


class SlopeTests(unittest.TestCase):
    def test_ols_recovers_a_known_line(self):
        origin = dt.date(2026, 1, 1)
        obs = [of.Observation(origin + dt.timedelta(days=i), 5.0 + 2.5 * i)
               for i in range(10)]
        self.assertAlmostEqual(cr._ols_slope(obs, origin), 2.5)

    def test_ols_needs_two_points(self):
        origin = dt.date(2026, 1, 1)
        self.assertIsNone(cr._ols_slope([of.Observation(origin, 1.0)], origin))

    def test_median_step_is_normalised_per_elapsed_day(self):
        """Reuses the forecaster's gap handling: a 4-day move is not one step."""
        obs = [of.Observation(dt.date(2026, 1, 1), 0.0),
               of.Observation(dt.date(2026, 1, 5), 40.0)]
        self.assertEqual(cr._median_step(obs), 10.0)


class RealSeriesTests(unittest.TestCase):
    """Invariants over the frozen extract. These describe the data, not a run."""

    @classmethod
    def setUpClass(cls):
        cls.rows = of.load_rows(SERIES)
        cls.lib = cr.library(path=SERIES)

    def test_as_of_comes_from_the_extract_not_from_a_clock(self):
        self.assertEqual(self.lib.as_of, cr.as_of_from_extract(SERIES).isoformat())
        self.assertEqual(self.lib.as_of, "2026-09-01")

    def test_every_pillar_and_threshold_is_present(self):
        self.assertEqual(
            len(self.lib.curves), sum(len(p.thresholds) for p in cr.PILLARS)
        )
        self.assertEqual(len(self.lib.pillars), len(cr.PILLARS))

    def test_no_milestone_is_dated_inside_a_reporting_gap(self):
        """Nothing is interpolated: every milestone date is an observed data day."""
        for c in self.lib.curves:
            days = {o.date.isoformat() for o in of.series(self.rows, c.metric)}
            for date in (c.first_reach_date, c.hold_start_date, c.hold_confirmed_date):
                if date is not None:
                    self.assertIn(date, days, f"{c.metric} {c.threshold}")

    def test_the_two_clocks_are_not_interchangeable(self):
        """They differ by 6 days for contact tracing and 20 for alert triage."""
        lags = {p.metric: p.observation_lag_days for p in self.lib.pillars}
        self.assertEqual(lags["contact_followup_percent"], 6)
        self.assertEqual(lags["alert_investigation_rate_percent"], 20)

    def test_alert_triage_is_left_censored_on_every_rung(self):
        """It was already above 85 the first time it was reported."""
        for c in self.lib.curves:
            if c.metric == "alert_investigation_rate_percent":
                self.assertEqual(c.first_reach_bound, "left_censored", c.threshold)

    def test_alert_triage_reporting_stopped_and_says_so(self):
        p = self.lib.summary("alert_investigation_rate_percent")
        self.assertTrue(p.reporting_stopped)
        self.assertEqual(p.last_observation_date, "2026-08-05")
        self.assertEqual(p.stale_days, 27)

    def test_pillars_that_are_merely_stale_are_not_called_stopped(self):
        """Four days behind the as-of date is the extract's lag, not a stop."""
        for metric in ("contact_followup_percent", "hospital_isolation_total",
                       "lab_positivity_percent", "samples_analyzed"):
            p = self.lib.summary(metric)
            self.assertEqual(p.stale_days, 4, metric)
            self.assertFalse(p.reporting_stopped, metric)

    def test_the_contact_tracing_50_milestone_carries_its_uncertainty(self):
        """A 14-day gap straddles the crossing; the row must not hide it."""
        c = self.lib.curve("contact_followup_percent", 50.0)
        self.assertEqual(c.first_reach_date, "2026-06-04")
        self.assertEqual(c.first_reach_window_days, 14)
        self.assertEqual(c.first_reach_bound, "interval")

    def test_coverage_matches_the_documented_substrate(self):
        counts = {p.metric: p.n_observations for p in self.lib.pillars}
        self.assertEqual(counts["contact_followup_percent"], 81)
        self.assertEqual(counts["alert_investigation_rate_percent"], 58)
        self.assertTrue(all(p.n_packets == 101 for p in self.lib.pillars))

    def test_run_is_deterministic(self):
        self.assertEqual(cr.library(path=SERIES), self.lib)


class LibraryShapeTests(unittest.TestCase):
    """The output has to survive being appended to by another outbreak."""

    def test_rows_are_json_serialisable_and_self_identifying(self):
        rows = cr.library(path=SERIES).to_rows()
        payload = json.loads(json.dumps(rows))
        for row in payload:
            self.assertEqual(row["outbreak"], cr.OUTBREAK)
            self.assertEqual(row["declaration"], "2026-05-15")
            self.assertIn("first_reach_bound", row)
            self.assertIn("scale_free", row)

    def test_compare_refuses_to_line_up_incomparable_rows(self):
        a = synth_library()
        b = cr.library(
            synth_rows([("2026-02-05", 60.0), ("2026-02-06", 71.0),
                        ("2026-02-07", 72.0), ("2026-02-08", 73.0)]),
            outbreak="SYNTH-2",
            declaration=dt.date(2026, 2, 1),
            pillars=(RISING_SPEC,),
        )
        rows = {(r["pillar"], r["threshold"]): r for r in cr.compare(a, b)}
        pair = rows[("synthetic rising", 70.0)]
        self.assertEqual(pair["n_outbreaks"], 2)
        self.assertTrue(pair["comparable"])
        # SYNTH-2 opens above 50, so its 50-rung is left censored and the pair
        # is two different measurements, not a difference.
        self.assertFalse(rows[("synthetic rising", 50.0)]["comparable"])

    def test_count_metrics_are_flagged_as_not_transferable(self):
        lib = cr.library(path=SERIES)
        by_metric = {c.metric: c for c in lib.curves}
        self.assertFalse(by_metric["samples_analyzed"].scale_free)
        self.assertFalse(by_metric["hospital_isolation_total"].scale_free)
        self.assertTrue(by_metric["contact_followup_percent"].scale_free)


class DocumentationTests(unittest.TestCase):
    """The document may not contain a figure this module did not produce."""

    @classmethod
    def setUpClass(cls):
        cls.lib = cr.library(path=SERIES)
        cls.doc = DOC.read_text(encoding="utf-8")

    def test_every_generated_block_matches_the_module(self):
        for name, render in cr.BLOCKS.items():
            block = (
                f"<!-- generated: {name} -->\n{render(self.lib)}\n"
                f"<!-- /generated: {name} -->"
            )
            self.assertIn(block, self.doc, f"{name} is stale; rerun the module")

    def test_the_writer_is_idempotent_and_refuses_an_unfenced_document(self):
        """`--write` refreshes the blocks and changes nothing else."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "doc.md"
            copy.write_text(self.doc, encoding="utf-8")
            self.assertEqual(cr.rewrite_doc(self.lib, copy), len(cr.BLOCKS))
            self.assertEqual(copy.read_text(encoding="utf-8"), self.doc)

            copy.write_text("no fences here", encoding="utf-8")
            with self.assertRaises(ValueError):
                cr.rewrite_doc(self.lib, copy)

    def test_inline_figures_in_the_prose_are_the_module_figures(self):
        """Every number the prose states outside a table, checked one by one.

        Whitespace is normalised so the claims survive re-wrapping, but the
        digits are not: a figure typed into the prose by hand, or left behind
        when the extract moved, fails here.
        """
        flat = " ".join(self.doc.split())
        lib = self.lib
        cft50 = lib.curve("contact_followup_percent", 50.0)
        cft70 = lib.curve("contact_followup_percent", 70.0)
        cft80 = lib.curve("contact_followup_percent", 80.0)
        iso750 = lib.curve("hospital_isolation_total", 750.0)
        alerts = lib.summary("alert_investigation_rate_percent")
        lab = lib.summary("samples_analyzed")
        pos = lib.summary("lab_positivity_percent")
        held = [c for c in lib.curves if c.held]
        unstable = [c for c in lib.curves
                    if any(n.startswith("first attainment") for n in c.notes)]
        rows = of.load_rows(SERIES)
        days = sorted(str(r["data_as_of"])[:10] for r in rows if r.get("data_as_of"))
        cft = of.series(rows, "contact_followup_percent")
        touch = next(o for o in cft if o.date.isoformat() == cft70.first_reach_date)
        collapse = next(o for o in cft if o.date > touch.date)

        claims = [
            f"extract of {lib.pillars[0].n_packets}",
            f"covering {days[0]} to {days[-1]}",
            f"Declaration is taken as {lib.declaration}",
            f"{cr.DEFAULT_HOLD} consecutive observations",
            f"{touch.value:g} on {touch.date.isoformat()}",
            f"{collapse.value:g} on {collapse.date.isoformat()}",
            f"day {cft70.days_to_first_reach_declaration} instead of day "
            f"{cft70.days_to_held_declaration}, wrong by "
            f"{cft70.days_to_held_declaration - cft70.days_to_first_reach_declaration}"
            f" days",
            f"between {min(p.observation_lag_days for p in lib.pillars)} and "
            f"{max(p.observation_lag_days for p in lib.pillars)} days",
            f"{alerts.observation_lag_days} days before the indicator",
            f"{cft50.first_reach_window_days}-day reporting gap",
            f"day {cft80.days_to_held_declaration}",
            f"{cft80.failures_after_hold} of its next {cft80.observations_after_hold}",
            f"{iso750.failures_after_hold} of {iso750.observations_after_hold}",
            f"{len(unstable)} of the {len(held)} rungs",
            f"{alerts.plateau_drift:+.1f}",
            f"ends on {alerts.last_observation_date} with {alerts.stale_days} days",
            f"{lab.ramp_slope_per_day:+g} samples per day per day",
            f"same phase is {lab.ramp_median_step_per_day:+g}",
            f"{pos.first_value:g} to {pos.last_value:g}",
            f"{lab.first_value:g} to {lab.last_value:g}",
        ]
        for claim in claims:
            self.assertIn(claim, flat, f"prose figure {claim!r} not in the doc")


if __name__ == "__main__":
    unittest.main()
