# SPDX-License-Identifier: Apache-2.0
"""Tests for the reporting-cadence integrity monitor.

This module's failure modes are all failures of DISCRIMINATION, so that is what
these tests attack. A monitor that calls every absence a surveillance loss cries
wolf until it is ignored; a monitor that calls every absence an extraction defect
lets a surveillance loss run silently, which is the failure that cost this
programme 58 days. Almost every case below is therefore a pair: two situations
that look identical in a null-count and must come out of the monitor with
different names.

The synthetic series carry hand-checked answers written out in the comments, so a
change in behaviour has to argue with an arithmetic statement rather than with a
recorded output. The three assertions made against the real frozen extract are
DATA alarms, labelled as such: if one fails, look at the extract before looking
at the code.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import unittest

from lovs.forecast import cadenceintegrity as ci
from lovs.forecast import opsforecast

REAL_EXTRACT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "data"
    / "operational-series-2026-09-01.json"
)

DAY0 = dt.date(2026, 6, 1)


def _row(number: int, day_offset: int, *, lag: int = 1, ready: bool = True, **values):
    """One packet, `day_offset` days into a synthetic outbreak."""
    data_day = DAY0 + dt.timedelta(days=day_offset)
    return {
        "sitrep": f"{number:03d}",
        "data_as_of": data_day.isoformat(),
        "published_at": (data_day + dt.timedelta(days=lag)).isoformat(),
        "ready_for_model_use": ready,
        **values,
    }


def _daily(n: int, **constant):
    """`n` consecutive daily packets, numbered from 1, each carrying `constant`."""
    return [_row(i + 1, i, **constant) for i in range(n)]


class DeliveryGapsAreClassifiedNotJustCounted(unittest.TestCase):
    """A hole in the numbering and a hole in the calendar are different events.

    Series below, hand-built: SitReps 001-008 on days 0-7, except that 003 is
    absent AND day 2 is absent (a packet that was never published), while 006 is
    absent but days 4 and 5 are both covered by 005 and 007 (the numbering skips,
    the content stream loses nothing).
    """

    def setUp(self):
        self.rows = [
            _row(1, 0, cases=1),
            _row(2, 1, cases=2),
            # 003 and day 2 both absent
            _row(4, 3, cases=4),
            _row(5, 4, cases=5),
            # 006 absent, but day 5 is covered by 007
            _row(7, 5, cases=7),
            _row(8, 6, cases=8),
        ]
        self.result = ci.delivery_integrity(self.rows)

    def test_both_kinds_of_hole_are_found(self):
        self.assertEqual(self.result.missing_numbers, (3, 6))

    def test_a_hole_that_costs_a_data_day_is_a_missing_publication(self):
        gap = next(g for g in self.result.gaps if g.missing == (3,))
        self.assertEqual(gap.verdict, ci.NOT_PUBLISHED)
        self.assertEqual(gap.calendar_gap_days, 2)
        self.assertEqual(gap.uncovered_days, ("2026-06-03",))

    def test_a_hole_that_costs_no_data_day_is_not_called_a_missing_publication(self):
        """The distinction the whole module exists for, in its simplest form."""
        gap = next(g for g in self.result.gaps if g.missing == (6,))
        self.assertEqual(gap.verdict, ci.NUMBER_SKIPPED_CONTENT_INTACT)
        self.assertEqual(gap.calendar_gap_days, 1)
        self.assertEqual(gap.uncovered_days, ())

    def test_consecutive_absences_are_one_gap_not_two(self):
        rows = [_row(1, 0), _row(2, 1), _row(5, 4)]
        gaps = ci.delivery_integrity(rows).gaps
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].missing, (3, 4))
        self.assertEqual(gaps[0].uncovered_days, ("2026-06-03", "2026-06-04"))

    def test_ledger_arithmetic_closes(self):
        self.assertEqual(
            self.result.delivered + len(self.result.missing_numbers),
            self.result.expected,
        )


class UncoveredDaysSurviveAnIntactNumbering(unittest.TestCase):
    """A day can be lost while the SitRep numbers run unbroken.

    Days 0, 1, 3: every number 001-003 is present, so a monitor watching only the
    numbering would report a clean feed while day 2 is missing from the record.
    """

    def test_a_missing_day_with_no_missing_number_is_reported_separately(self):
        rows = [_row(1, 0), _row(2, 1), _row(3, 3)]
        result = ci.delivery_integrity(rows)
        self.assertEqual(result.missing_numbers, ())
        self.assertEqual(result.uncovered_data_days, ("2026-06-03",))
        self.assertEqual(result.uncovered_without_number_gap, ("2026-06-03",))

    def test_a_day_explained_by_a_missing_number_is_not_double_reported(self):
        rows = [_row(1, 0), _row(2, 1), _row(4, 3)]
        result = ci.delivery_integrity(rows)
        self.assertEqual(result.uncovered_data_days, ("2026-06-03",))
        self.assertEqual(result.uncovered_without_number_gap, ())


class PacketsAreDeduplicatedByNumber(unittest.TestCase):
    """A row repeated in the extract is not a SitRep delivered twice."""

    def test_a_duplicated_row_changes_no_coverage_figure(self):
        rows = _daily(10, cases=1)
        clean = ci.indicator_coverage(rows).indicators[0]
        doubled = ci.indicator_coverage(rows + [dict(rows[4])]).indicators[0]
        self.assertEqual(clean.n_populated, doubled.n_populated)
        self.assertEqual(clean.coverage_overall, doubled.coverage_overall)

    def test_the_duplicate_is_still_reported_as_an_extract_defect(self):
        rows = _daily(4, cases=1)
        anomalies = ci.delivery_integrity(rows + [dict(rows[2])]).anomalies
        self.assertEqual(anomalies.duplicate_sitreps, ("003",))

    def test_a_packet_published_before_its_own_data_day_is_flagged(self):
        rows = _daily(4, cases=1) + [_row(5, 4, lag=-2, cases=1)]
        anomalies = ci.delivery_integrity(rows).anomalies
        self.assertEqual(anomalies.negative_lag_sitreps, ("005",))

    def test_an_impossible_lag_is_excluded_from_the_lag_distribution(self):
        """One transcription error must not drag the mean lag below zero."""
        rows = _daily(8, cases=1) + [_row(9, 8, lag=-5, cases=1)]
        lag = ci.publication_lag(rows, n_permutations=200)
        self.assertEqual(lag.n, 8)
        self.assertEqual(lag.minimum, 1)


class DeliveredButEmptyIsNotUndelivered(unittest.TestCase):
    """The second of the three states, in isolation."""

    def test_a_packet_carrying_nothing_is_delivered_and_named(self):
        rows = [_row(1, 0, cases=1), _row(2, 1), _row(3, 2, cases=3)]
        result = ci.delivery_integrity(rows)
        self.assertEqual(result.missing_numbers, ())
        self.assertEqual(result.uncovered_data_days, ())
        self.assertEqual(result.empty_packets, ("002",))


class PublicationLagDrift(unittest.TestCase):
    """A feed under strain slows before it stops, so the lag series is an alarm."""

    def test_a_lag_that_doubles_halfway_through_is_called_slowing(self):
        # Ten packets at one day's lag, then ten at three: hand-checked means are
        # 1.0 and 3.0, so the drift is exactly +2.0 days.
        rows = [_row(i + 1, i, lag=1) for i in range(10)]
        rows += [_row(i + 11, i + 10, lag=3) for i in range(10)]
        lag = ci.publication_lag(rows, n_permutations=2000)
        self.assertEqual(lag.first_half_mean, 1.0)
        self.assertEqual(lag.second_half_mean, 3.0)
        self.assertEqual(lag.drift_days, 2.0)
        self.assertLess(lag.drift_p_value, 0.01)
        self.assertEqual(lag.drift_verdict, "slowing")

    def test_a_lag_that_halves_is_called_quickening_not_slowing(self):
        rows = [_row(i + 1, i, lag=3) for i in range(10)]
        rows += [_row(i + 11, i + 10, lag=1) for i in range(10)]
        lag = ci.publication_lag(rows, n_permutations=2000)
        self.assertEqual(lag.drift_verdict, "quickening")

    def test_a_constant_lag_cannot_be_called_drifting(self):
        """No difference exists, so every relabelling ties: p must be 1.0."""
        lag = ci.publication_lag(_daily(20), n_permutations=500)
        self.assertEqual(lag.drift_days, 0.0)
        self.assertEqual(lag.drift_p_value, 1.0)
        self.assertEqual(lag.drift_verdict, "stable")

    def test_the_same_lags_in_a_different_order_are_not_drift(self):
        """Two long lags scattered through the series are noise, not a trend."""
        lags = [1] * 9 + [4] + [1] * 9 + [4]
        rows = [_row(i + 1, i, lag=lags[i]) for i in range(20)]
        lag = ci.publication_lag(rows, n_permutations=4000)
        self.assertEqual(lag.drift_verdict, "stable")

    def test_the_permutation_p_can_never_be_zero(self):
        """Reporting p=0 from a finite sample claims more than was sampled."""
        rows = [_row(i + 1, i, lag=1) for i in range(20)]
        rows += [_row(i + 21, i + 20, lag=9) for i in range(20)]
        lag = ci.publication_lag(rows, n_permutations=100)
        self.assertGreater(lag.drift_p_value, 0.0)
        self.assertAlmostEqual(lag.drift_p_value, 1 / 101)

    def test_a_series_too_short_to_split_refuses_rather_than_guesses(self):
        with self.assertRaises(ci.CadenceIntegrityError):
            ci.publication_lag(_daily(3))


class CompletenessTrend(unittest.TestCase):
    """Coverage direction, hand-checked, and screened for multiplicity."""

    def test_an_indicator_that_stops_halfway_is_degrading(self):
        # 20 packets: present in 1-10, absent in 11-20. Halves are 1.00 and 0.00.
        rows = [_row(i + 1, i, alerts=5) for i in range(10)]
        rows += [_row(i + 11, i + 10, cases=1) for i in range(10)]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.first_half_coverage, 1.0)
        self.assertEqual(entry.second_half_coverage, 0.0)
        self.assertEqual(entry.coverage_delta, -1.0)
        self.assertEqual(entry.trend, ci.DEGRADING)

    def test_an_indicator_that_firms_up_is_improving_not_degrading(self):
        """The mirror case. A field that goes from sporadic to reliable is as
        much a change in the feed as one that vanishes, and a one-sided test
        would call it stable. This is the shape contact_followup_percent has.
        """
        # Present on packets 1, 5, 9 of the first ten (3/10), then on all ten of
        # the second (10/10). Hand-checked halves: 0.30 and 1.00.
        sporadic = {0, 4, 8}
        rows = [
            _row(i + 1, i, cases=1, **({"alerts": 5} if i in sporadic else {}))
            for i in range(10)
        ]
        rows += [_row(i + 11, i + 10, cases=1, alerts=5) for i in range(10)]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.first_half_coverage, 0.3)
        self.assertEqual(entry.second_half_coverage, 1.0)
        self.assertEqual(entry.trend, ci.IMPROVING)

    def test_a_late_but_complete_indicator_is_not_charged_for_its_own_youth(self):
        """Coverage since first appearance is 1.00 even though overall is 0.50.

        Charging an indicator for packets that predate the schema it lives in
        would report twenty surveillance gaps and bury the two real ones.
        """
        rows = [_row(i + 1, i, cases=1) for i in range(10)]
        rows += [_row(i + 11, i + 10, cases=1, alerts=5) for i in range(10)]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.trend, ci.STABLE)
        self.assertEqual(entry.coverage_overall, 0.5)
        self.assertEqual(entry.coverage_since_first, 1.0)
        self.assertEqual(entry.status, ci.LIVE)

    def test_scattered_absences_are_not_a_trend(self):
        # Two gaps in each half: nothing changed, and a monitor that says
        # otherwise will report a trend every time the feed hiccups.
        present = [i not in (2, 5, 12, 16) for i in range(20)]
        rows = [
            _row(i + 1, i, cases=1, **({"alerts": 5} if present[i] else {}))
            for i in range(20)
        ]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.trend, ci.STABLE)

    def test_a_field_no_packet_ever_carried_is_absence_not_loss(self):
        rows = _daily(10, cases=1)
        rows[3]["alerts"] = None
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.status, ci.NEVER_REPORTED)
        self.assertEqual(entry.trend, ci.UNTESTED)
        self.assertEqual(entry.n_populated, 0)
        # A field that never existed must not be attributed to anyone as a loss.
        self.assertNotIn("UPSTREAM", entry.attribution)
        self.assertNotIn("OURS", entry.attribution)

    def test_holm_correction_is_actually_applied(self):
        """With enough indicators, a marginal split must stop being significant.

        Present on the first 14 of 20 packets: Fisher's exact p for [[10,0],[4,6]]
        is 2 x C(10,4)/C(20,14) = 420/38760 = 0.0108. Alone (two indicators
        tested) Holm's bar is 0.05/2 = 0.025 and it clears; among 32 it is
        0.05/32 = 0.0016 and it does not. Uncorrected it would be called
        degrading in both.
        """
        pattern = [i < 14 for i in range(20)]

        def build(n_noise):
            rows = []
            for i in range(20):
                values = {"cases": 1}
                if pattern[i]:
                    values["marginal"] = 1
                for j in range(n_noise):
                    values[f"noise{j}"] = 1
                rows.append(_row(i + 1, i, **values))
            return rows

        alone = _find(ci.indicator_coverage(build(0)), "marginal")
        crowded = _find(ci.indicator_coverage(build(30)), "marginal")
        self.assertAlmostEqual(alone.trend_p_value, 420 / 38760)
        self.assertEqual(alone.trend_p_value, crowded.trend_p_value)
        self.assertEqual(alone.trend, ci.DEGRADING)
        self.assertEqual(crowded.trend, ci.STABLE)


class FisherExactIsExact(unittest.TestCase):
    """The trend test must be a property of the table, not of a seed."""

    def test_a_perfect_split_matches_the_hand_computed_hypergeometric(self):
        # [[3,0],[0,3]]: only the two extreme tables are as unlikely as observed,
        # each with density 1/C(6,3) = 1/20, so the two-sided p is exactly 0.1.
        self.assertAlmostEqual(ci._fisher_exact_two_sided(3, 0, 0, 3), 0.1)

    def test_a_balanced_table_is_p_one(self):
        self.assertAlmostEqual(ci._fisher_exact_two_sided(2, 2, 2, 2), 1.0)

    def test_the_test_is_two_sided(self):
        self.assertAlmostEqual(
            ci._fisher_exact_two_sided(3, 0, 0, 3),
            ci._fisher_exact_two_sided(0, 3, 3, 0),
        )

    def test_a_degenerate_table_returns_one_rather_than_dividing_by_zero(self):
        self.assertEqual(ci._fisher_exact_two_sided(0, 4, 0, 4), 1.0)


class DarkRunArithmetic(unittest.TestCase):
    """The bar an indicator is judged against must not include the run being judged."""

    def test_only_runs_closed_on_both_sides_count(self):
        self.assertEqual(ci._longest_internal_dark_run([1, 0, 0, 1, 0, 1]), 2)

    def test_a_still_open_run_does_not_set_its_own_bar(self):
        # Trailing zeros are the thing under judgement; if they counted, every
        # indicator would be permanently within tolerance of itself.
        self.assertEqual(ci._longest_internal_dark_run([1, 1, 0, 0, 0]), 0)

    def test_a_leading_run_before_the_first_value_is_not_a_gap(self):
        self.assertEqual(ci._longest_internal_dark_run([0, 0, 1, 1]), 0)


class LostVersusMerelyInterrupted(unittest.TestCase):
    """An indicator that has been this quiet before is not yet a loss."""

    def test_a_silence_the_indicator_has_survived_before_is_an_interruption(self):
        # Dark for 3 packets mid-series, then dark for 2 at the end: 2 <= 3, so
        # the feed has done this and come back.
        present = [True] * 5 + [False] * 3 + [True] * 5 + [False] * 2
        rows = [
            _row(i + 1, i, cases=1, **({"alerts": 5} if p else {}))
            for i, p in enumerate(present)
        ]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.longest_prior_dark_run, 3)
        self.assertEqual(entry.current_dark_run, 2)
        self.assertEqual(entry.status, ci.INTERRUPTED)

    def test_a_silence_longer_than_any_before_it_is_a_loss(self):
        present = [True] * 5 + [False] * 3 + [True] * 5 + [False] * 4
        rows = [
            _row(i + 1, i, cases=1, **({"alerts": 5} if p else {}))
            for i, p in enumerate(present)
        ]
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertEqual(entry.status, ci.LOST)


class AbsenceIsAttributedNotAssumed(unittest.TestCase):
    """The central claim: three states, three different answers."""

    def test_a_reconstructible_ratio_is_attributed_to_us_not_to_the_publisher(self):
        """Both inputs keep arriving, so the ratio was ours to compute and ours to lose."""
        rows = []
        for i in range(12):
            values = {"samples_positive": 5, "samples_analyzed": 50}
            if i < 6:
                values["lab_positivity_percent"] = 10.0
            rows.append(_row(i + 1, i, **values))
        entry = _find(ci.indicator_coverage(rows), "lab_positivity_percent")
        self.assertEqual(entry.status, ci.LOST)
        self.assertTrue(entry.attribution.startswith("OURS"))
        anomalies = ci.delivery_integrity(rows).anomalies
        self.assertEqual(len(anomalies.derived_absent_though_computable), 6)

    def test_a_ratio_whose_numerator_also_stopped_is_attributed_upstream(self):
        rows = []
        for i in range(12):
            values = {"alerts_reported": 100}
            if i < 6:
                values["alerts_investigated"] = 90
                values["alert_investigation_rate_percent"] = 90.0
            rows.append(_row(i + 1, i, **values))
        entry = _find(ci.indicator_coverage(rows), "alert_investigation_rate_percent")
        self.assertTrue(entry.attribution.startswith("UPSTREAM"))
        self.assertIn("alerts_investigated", entry.attribution)

    def test_indicators_that_stop_together_are_attributed_to_one_upstream_change(self):
        rows = []
        for i in range(12):
            values = {"cases": 1}
            if i < 6:
                values.update(fieldA=1, fieldB=2)
            rows.append(_row(i + 1, i, **values))
        report = ci.indicator_coverage(rows)
        entry = _find(report, "fieldA")
        self.assertTrue(entry.attribution.startswith("UPSTREAM (probable)"))
        self.assertIn("fieldB", entry.attribution)
        self.assertEqual(report.co_stop_cohorts, (("006", ("fieldA", "fieldB")),))

    def test_a_lone_stop_with_no_corroboration_is_left_unresolved(self):
        """The honest answer when the extract cannot tell. Guessing here is the
        difference between fixing a pipeline and reporting a surveillance loss."""
        rows = []
        for i in range(12):
            values = {"cases": 1, "deaths": 1}
            if i < 6:
                values["lonely"] = 1
            rows.append(_row(i + 1, i, **values))
        entry = _find(ci.indicator_coverage(rows), "lonely")
        self.assertTrue(entry.attribution.startswith("UNRESOLVED"))

    def test_an_indicator_dark_only_because_no_packet_arrived_is_not_its_own_loss(self):
        rows = [_row(i + 1, i, cases=1, alerts=5) for i in range(6)]
        rows += [_row(i + 7, i + 6) for i in range(4)]  # four empty packets
        entry = _find(ci.indicator_coverage(rows), "alerts")
        self.assertIn("delivery failure", entry.attribution)


class StalenessClock(unittest.TestCase):
    """Graded against what the feed has done, and taken at a date you pass in."""

    def test_severity_thresholds_come_from_the_series_not_from_an_opinion(self):
        # Gaps between data days: 1, 1, 1, 5. Nearest-rank p90 of that is 5 and
        # the worst is 5, so a five-day silence is GREEN and a six-day one is RED.
        rows = [_row(1, 0), _row(2, 1), _row(3, 2), _row(4, 3), _row(5, 8)]
        reading = ci.staleness(rows, DAY0 + dt.timedelta(days=13))
        self.assertEqual(reading.reference_max_gap_days, 5)
        self.assertEqual(reading.days_since_data_day, 5)
        self.assertEqual(reading.severity, ci.GREEN)
        later = ci.staleness(rows, DAY0 + dt.timedelta(days=14))
        self.assertEqual(later.days_since_data_day, 6)
        self.assertEqual(later.severity, ci.RED)

    def test_amber_exists_between_routine_and_unprecedented(self):
        # Twenty gaps: nineteen of 1 day and one of 4. Nearest-rank p90 is the
        # 18th of 20 in order, which is 1; the worst is 4. So a 2-day silence is
        # neither routine nor unprecedented.
        rows = [_row(i + 1, i, cases=1) for i in range(20)] + [_row(21, 23, cases=1)]
        reading = ci.staleness(rows, DAY0 + dt.timedelta(days=25))
        self.assertEqual(reading.reference_p90_gap_days, 1.0)
        self.assertEqual(reading.reference_max_gap_days, 4)
        self.assertEqual(reading.severity, ci.AMBER)

    def test_an_indicator_as_fresh_as_the_feed_does_not_get_its_own_alarm(self):
        """Twenty alarms for one stale extract would bury the real losses."""
        rows = _daily(10, cases=1, alerts=5)
        reading = ci.staleness(rows, DAY0 + dt.timedelta(days=20))
        self.assertEqual(reading.severity, ci.RED)
        owners = {r.indicator: r.owner for r in reading.indicators}
        self.assertEqual(owners["cases"], ci.FEED_OWNED)
        self.assertEqual(owners["alerts"], ci.FEED_OWNED)
        self.assertEqual(reading.indicator_specific_alarms, ())

    def test_an_indicator_staler_than_the_feed_owns_its_own_alarm(self):
        rows = [_row(i + 1, i, cases=1, alerts=5) for i in range(6)]
        rows += [_row(i + 7, i + 6, cases=1) for i in range(6)]
        reading = ci.staleness(rows, DAY0 + dt.timedelta(days=12))
        by_name = {r.indicator: r for r in reading.indicators}
        self.assertEqual(by_name["cases"].days_behind_feed, 0)
        self.assertEqual(by_name["alerts"].days_behind_feed, 6)
        self.assertEqual(by_name["alerts"].owner, ci.INDICATOR_OWNED)
        self.assertEqual(reading.indicator_specific_alarms, ("alerts",))

    def test_a_reading_taken_before_the_extract_was_published_is_refused(self):
        rows = _daily(6, cases=1)
        with self.assertRaises(ci.CadenceIntegrityError) as ctx:
            ci.staleness(rows, DAY0)
        self.assertIn("look", str(ctx.exception).lower() + " look")

    def test_an_empty_extract_refuses_rather_than_reporting_a_healthy_feed(self):
        with self.assertRaises(ci.CadenceIntegrityError):
            ci.staleness([], DAY0)


class NoLookAhead(unittest.TestCase):
    """Knowability is set by publication, not by the day the data describes."""

    def test_truncation_excludes_a_packet_not_yet_published(self):
        # Packet 006 describes day 5 but is published on day 6, so a reading
        # taken on day 5 must not see it.
        rows = _daily(6, cases=1)
        visible = ci.rows_as_of(rows, DAY0 + dt.timedelta(days=5))
        self.assertEqual([r["sitrep"] for r in visible], ["001", "002", "003", "004", "005"])

    def test_truncating_on_the_data_day_would_have_imported_the_future(self):
        """Guards the specific defect: filtering on data_as_of leaks one lag."""
        rows = _daily(6, cases=1)
        as_of = DAY0 + dt.timedelta(days=5)
        by_data_day = [r for r in rows if dt.date.fromisoformat(r["data_as_of"]) <= as_of]
        self.assertEqual(len(by_data_day), 6)
        self.assertEqual(len(ci.rows_as_of(rows, as_of)), 5)

    def test_days_since_publication_is_never_negative_on_a_truncated_view(self):
        rows = _daily(20, cases=1)
        for offset in range(2, 21):
            as_of = DAY0 + dt.timedelta(days=offset)
            reading = ci.staleness(ci.rows_as_of(rows, as_of), as_of)
            self.assertGreaterEqual(reading.days_since_publication, 0)
            self.assertGreaterEqual(reading.days_since_data_day, 0)

    def test_the_module_never_reads_a_clock(self):
        """Load-bearing: a monitor that reads the system clock cannot be backtested.

        Asserted against the source rather than against behaviour because a clock
        call added in one branch would otherwise sit undetected until the day it
        fired.
        """
        source = pathlib.Path(ci.__file__).read_text(encoding="utf-8")
        for forbidden in ("date.today", "datetime.now", "utcnow", "time.time"):
            self.assertNotIn(forbidden, source)


class ContinuityForecastBehaviour(unittest.TestCase):
    """The forecast must be reproducible, responsive, and honest about its ceiling."""

    def _regular(self, n=70):
        return _daily(n, cases=1)

    def test_the_same_seed_reproduces_the_same_probability(self):
        rows = self._regular()
        as_of = DAY0 + dt.timedelta(days=70)
        a = ci.continuity_forecast(rows, as_of, seed=7, n_paths=2000)
        b = ci.continuity_forecast(rows, as_of, seed=7, n_paths=2000)
        self.assertEqual(a.p_sustains_cadence, b.p_sustains_cadence)

    def test_a_perfectly_regular_feed_is_forecast_to_stay_regular(self):
        rows = self._regular()
        f = ci.continuity_forecast(
            rows, DAY0 + dt.timedelta(days=70), n_paths=2000, horizon_days=30
        )
        self.assertEqual(f.recent_worst_silence_days, 1)
        self.assertEqual(f.recent_arrivals, 30)
        self.assertEqual(f.p_sustains_cadence, 1.0)

    def test_a_feed_with_slow_stretches_is_forecast_less_confidently(self):
        """Same benchmark, rougher reference window: the probability must fall."""
        rough = []
        day = 0
        number = 1
        while day < 40:  # a 3-day gap every fifth arrival
            rough.append(_row(number, day, cases=1))
            day += 3 if number % 5 == 0 else 1
            number += 1
        rough += [_row(number + i, 40 + i, cases=1) for i in range(31)]
        as_of = DAY0 + dt.timedelta(days=71)
        f = ci.continuity_forecast(rough, as_of, n_paths=4000, horizon_days=30)
        self.assertEqual(f.recent_worst_silence_days, 1)
        self.assertLess(f.p_sustains_cadence, 1.0)

    def test_the_bootstrap_cannot_invent_a_silence_longer_than_it_has_seen(self):
        """Stated as a field, because it is the method's hard ceiling.

        A gap bootstrap draws from observed gaps, so the worst silence it can
        produce is the worst it has seen. That is why the forecast cannot price a
        stall, and why the number is reported beside its own upper bound.
        """
        rows = self._regular()
        f = ci.continuity_forecast(
            rows, DAY0 + dt.timedelta(days=70), n_paths=2000, horizon_days=30
        )
        self.assertEqual(f.bootstrap_max_possible_silence_days, 1)
        self.assertLessEqual(f.worst_silence_p95, f.bootstrap_max_possible_silence_days)

    def test_simulated_arrivals_never_overrun_the_horizon(self):
        gaps = [1, 1, 2, 1, 3, 1, 1, 2, 1, 1]
        for arrivals, worst in ci._simulate_arrivals(gaps, 30, 200, 5, 11):
            self.assertLessEqual(arrivals, 30)
            self.assertLessEqual(worst, max(gaps))
            self.assertGreater(arrivals, 0)

    def test_too_few_gaps_to_bootstrap_refuses_rather_than_extrapolating(self):
        rows = _daily(4, cases=1)
        with self.assertRaises(ci.CadenceIntegrityError):
            ci.continuity_forecast(rows, DAY0 + dt.timedelta(days=4), n_paths=100)

    def test_a_forecast_from_an_unpublished_future_is_refused(self):
        rows = self._regular()
        with self.assertRaises(ci.CadenceIntegrityError):
            ci.continuity_forecast(rows, DAY0 + dt.timedelta(days=10), n_paths=100)


class ReuseOfTheForecasterSeries(unittest.TestCase):
    """The monitor and the forecaster must never disagree about coverage.

    If the monitor says a series is live and opsforecast cannot find its last
    observation, one of them is wrong and both are trusted. Sharing the function
    is the fix; this test is the proof that the sharing survives.
    """

    def setUp(self):
        self.rows = opsforecast.load_rows(REAL_EXTRACT)

    def test_numeric_indicators_route_through_opsforecast_series(self):
        checked = 0
        for name in ci.indicators(self.rows):
            if not ci._is_numeric(self.rows, name):
                continue
            checked += 1
            self.assertEqual(
                ci.populated_days(self.rows, name),
                [o.date for o in opsforecast.series(self.rows, name)],
            )
        self.assertGreater(checked, 10)

    def test_structured_indicators_are_covered_too(self):
        structured = [n for n in ci.indicators(self.rows) if not ci._is_numeric(self.rows, n)]
        self.assertTrue(structured)
        for name in structured:
            days = ci.populated_days(self.rows, name)
            self.assertEqual(days, sorted(set(days)))
            self.assertTrue(days)


class RealExtractDataAlarms(unittest.TestCase):
    """DATA alarms, not code regressions.

    Each assertion below is a claim about the frozen extract that the monitor was
    built to make. If one fails, the extract changed: read the new extract before
    editing this file. Silencing one of these by relaxing it would recreate the
    exact failure the module exists to prevent.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = opsforecast.load_rows(REAL_EXTRACT)
        cls.coverage = ci.indicator_coverage(cls.rows)
        cls.delivery = ci.delivery_integrity(cls.rows)

    def test_alert_investigation_rate_stopped_on_2026_08_05_and_has_not_returned(self):
        entry = _find(self.coverage, "alert_investigation_rate_percent")
        self.assertEqual(entry.last_data_day, "2026-08-05")
        self.assertEqual(entry.status, ci.LOST)
        self.assertEqual(entry.trend, ci.DEGRADING)

    def test_that_stop_is_upstream_because_its_numerator_stopped_with_it(self):
        """Rules out our extract: the ratio is unreconstructible from later packets."""
        entry = _find(self.coverage, "alert_investigation_rate_percent")
        self.assertTrue(entry.attribution.startswith("UPSTREAM"))
        self.assertEqual(
            _find(self.coverage, "alerts_investigated").last_data_day, "2026-08-05"
        )
        self.assertEqual(_find(self.coverage, "alerts_reported").status, ci.LIVE)

    def test_no_indicator_loss_in_the_extract_is_attributable_to_extraction(self):
        """No ratio is ever missing from a packet that carried both its inputs."""
        self.assertEqual(self.delivery.anomalies.derived_absent_though_computable, ())

    def test_every_missing_sitrep_number_is_classified(self):
        classified = [n for gap in self.delivery.gaps for n in gap.missing]
        self.assertEqual(tuple(sorted(classified)), self.delivery.missing_numbers)
        for gap in self.delivery.gaps:
            self.assertIn(
                gap.verdict, (ci.NOT_PUBLISHED, ci.NUMBER_SKIPPED_CONTENT_INTACT)
            )

    def test_the_whole_report_assembles_and_renders(self):
        as_of = dt.date(2026, 9, 1)
        rendered = ci.render(
            ci.report(ci.rows_as_of(self.rows, as_of), as_of, n_paths=500)
        )
        for heading in (
            "1. DELIVERY INTEGRITY",
            "2. INDICATOR COMPLETENESS",
            "3. STALENESS CLOCK",
            "4. CONTINUITY FORECAST",
        ):
            self.assertIn(heading, rendered)


def _find(report: ci.CoverageReport, name: str) -> ci.IndicatorCoverage:
    return next(e for e in report.indicators if e.indicator == name)


if __name__ == "__main__":
    unittest.main()
