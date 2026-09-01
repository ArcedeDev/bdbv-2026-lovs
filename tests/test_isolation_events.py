"""Tests for the isolation capacity and events module.

Three of these carry the weight.

`test_synthetic_trajectory_matches_hand_computation` and
`test_synthetic_churn_is_all_reporting_no_drift` run hand-checked series whose
answers were worked out on paper before the code ran, so they fail if the
arithmetic drifts rather than merely if the code crashes.

`test_block6_isolation_pins_reproduce_exactly` re-derives both committed Block 6
isolation probabilities from the ledger's own recorded seeds and asserts equality
to the fourth decimal. If this fails, either the frozen series moved or the
bootstrap changed, and a pinned calibration probability is no longer reproducible
-- which breaks the contract that makes the block scorable. It is paired with
`test_consistency_check_detects_a_tampered_ledger`, because a check that cannot
fail proves nothing.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from lovs.forecast import isolationevents as ie
from lovs.forecast import opsforecast as of

REPO = pathlib.Path(__file__).resolve().parent.parent
SERIES = REPO / "data" / "operational-series-2026-09-01.json"
LEDGER = REPO / "data" / "operational-calibration-ledger.json"

AS_OF = dt.date(2026, 9, 1)


def rows_from(values, *, start=dt.date(2026, 1, 1), **extra):
    """A minimal frozen-extract-shaped row list, one row per consecutive day."""
    out = []
    for i, v in enumerate(values):
        row = {"data_as_of": (start + dt.timedelta(days=i)).isoformat(),
               ie.OCCUPANCY_METRIC: v}
        for key, seq in extra.items():
            row[key] = seq[i]
        out.append(row)
    return out


def packet(day, movement=None, detail=None):
    fig = {}
    if movement is not None:
        fig["patient_movement"] = movement
    if detail is not None:
        fig["reviewed_operational_detail"] = detail
    return {"data_as_of": day, "figures": fig}


# --- hand-checked synthetic trajectory ----------------------------------------

class TrajectoryTests(unittest.TestCase):
    #: 11 daily observations. Differences worked out by hand:
    #: +10 +10 -5 +15 0 +10 +10 -5 +15 +20  -> 7 up, 2 down, 1 flat, sum 80.
    HAND = [100, 110, 120, 115, 130, 130, 140, 150, 145, 160, 180]

    def test_synthetic_trajectory_matches_hand_computation(self):
        t = ie.occupancy_trajectory(rows_from(self.HAND), as_of=dt.date(2026, 1, 21))
        self.assertEqual(t.n_observations, 11)
        self.assertEqual(t.rises, 7)
        self.assertEqual(t.falls, 2)
        self.assertEqual(t.flats, 1)
        self.assertFalse(t.monotone_nondecreasing)
        self.assertAlmostEqual(t.mean_change_per_day, 8.0)     # 80 / 10
        self.assertAlmostEqual(t.median_change_per_day, 10.0)  # midpoint of 10 and 10
        self.assertEqual(t.largest_single_rise, 20)
        self.assertEqual(t.largest_single_fall, -5)
        self.assertEqual((t.first_value, t.last_value), (100, 180))
        self.assertEqual((t.minimum, t.maximum), (100, 180))
        self.assertTrue(t.at_all_time_high)
        # 100 -> 180 over 10 days: 10 * ln2 / ln1.8
        self.assertAlmostEqual(t.occupancy_doubling_days, 11.7924, places=3)
        self.assertEqual(t.days_stale_at_as_of, 10)  # last data day 2026-01-11

    def test_monotone_series_is_reported_monotone(self):
        t = ie.occupancy_trajectory(rows_from([10, 20, 20, 30]), as_of=dt.date(2026, 1, 5))
        self.assertTrue(t.monotone_nondecreasing)
        self.assertEqual((t.rises, t.falls, t.flats), (2, 0, 1))

    def test_ols_slope_recovers_an_exact_line(self):
        self.assertAlmostEqual(ie._ols_slope([(x, 3 * x + 5) for x in range(6)]), 3.0)
        self.assertIsNone(ie._ols_slope([(2.0, 1.0), (2.0, 9.0)]))

    def test_a_shrinking_series_reports_no_doubling_time(self):
        t = ie.occupancy_trajectory(rows_from([100, 90, 80]), as_of=dt.date(2026, 1, 4))
        self.assertIsNone(t.occupancy_doubling_days)
        self.assertFalse(t.at_all_time_high)

    def test_too_short_a_series_refuses_rather_than_guesses(self):
        with self.assertRaises(ValueError):
            ie.occupancy_trajectory(rows_from([100]))


# --- hand-checked coverage churn ----------------------------------------------

class CoverageChurnTests(unittest.TestCase):
    #: Province A climbs +10 every day. Province B is a flat 50 that vanishes on
    #: day 3 and returns on day 4. Nothing clinical happens to B at all, so every
    #: bit of spread in the raw national sum is reporting, and none of the drift.
    PANEL = [
        {"A": 100, "B": 50},
        {"A": 110, "B": 50},
        {"A": 120},
        {"A": 130, "B": 50},
    ]

    def rows(self):
        return [{"data_as_of": (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
                 ie.PROVINCE_FIELD: table}
                for i, table in enumerate(self.PANEL)]

    def test_synthetic_churn_is_all_reporting_no_drift(self):
        c = ie.coverage_churn(self.rows())
        self.assertEqual(c.panel_days, 4)
        self.assertEqual(c.comparable_pairs, 3)
        self.assertEqual(c.days_with_membership_change, 2)
        # raw diffs +10, -40, +60 ; constant-panel diffs +10, +10, +10
        self.assertAlmostEqual(c.raw_mean, 10.0)
        self.assertAlmostEqual(c.constant_panel_mean, 10.0)
        self.assertAlmostEqual(c.constant_panel_sd, 0.0)
        self.assertAlmostEqual(c.raw_sd, 40.8248, places=3)
        self.assertAlmostEqual(c.variance_share_from_churn, 1.0)
        self.assertIsNotNone(c.largest_churn_artifact)
        day, raw, held, moved = c.largest_churn_artifact
        self.assertEqual((day, raw, held, moved), ("2026-01-03", -40.0, 10.0, ("B",)))

    def test_a_stable_panel_shows_no_churn(self):
        rows = [{"data_as_of": (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
                 ie.PROVINCE_FIELD: {"A": 100 + 10 * i, "B": 50 - i}} for i in range(5)]
        c = ie.coverage_churn(rows)
        self.assertEqual(c.days_with_membership_change, 0)
        self.assertAlmostEqual(c.raw_sd, c.constant_panel_sd)
        self.assertAlmostEqual(c.variance_share_from_churn, 0.0)

    def test_total_only_days_do_not_masquerade_as_a_province(self):
        """A `Total`-only table is a national figure, not a one-province panel."""
        rows = [{"data_as_of": "2026-01-01", ie.PROVINCE_FIELD: {"Total": 267}},
                {"data_as_of": "2026-01-02", ie.PROVINCE_FIELD: {"A": 5, "Total": 5}}]
        panel = ie.province_panel(rows)
        self.assertEqual(len(panel), 1)
        self.assertEqual(panel[0][1], {"A": 5.0})

    def test_a_duplicated_data_day_is_counted_once(self):
        rows = [{"data_as_of": "2026-01-01", ie.PROVINCE_FIELD: {"A": 1}},
                {"data_as_of": "2026-01-01", ie.PROVINCE_FIELD: {"A": 999}},
                {"data_as_of": "2026-01-02", ie.PROVINCE_FIELD: {"A": 2}}]
        panel = ie.province_panel(rows)
        self.assertEqual([(d.isoformat(), t) for d, t in panel],
                         [("2026-01-01", {"A": 1.0}), ("2026-01-02", {"A": 2.0})])


# --- availability census ------------------------------------------------------

class AvailabilityTests(unittest.TestCase):
    def census(self, packets, rows=None):
        rows = rows if rows is not None else rows_from([100, 110])
        return {m.metric: m for m in ie.audit_isolation_metrics(rows, packets, as_of=AS_OF)}

    def test_alias_drift_is_one_metric_not_two_discontinuations(self):
        """`escaped` -> `escaped_suspect_or_confirmed_24h` is a rename, not a stop."""
        c = self.census([
            packet("2026-08-01", {"escaped": [1, 2]}),
            packet("2026-08-20", {"escaped_suspect_or_confirmed_24h": {"Ituri": 3}}),
        ])["isolation_escapes"]
        self.assertEqual(c.packet_days_carrying, 2)
        self.assertEqual(c.last_day, "2026-08-20")
        self.assertEqual(c.days_stale_at_as_of, 12)
        self.assertEqual(c.aliases_seen,
                         ("escaped", "escaped_suspect_or_confirmed_24h"))

    def test_days_are_counted_not_packets(self):
        c = self.census([packet("2026-08-01", {"escaped": [1]}),
                         packet("2026-08-01", {"escaped": [1]})])["isolation_escapes"]
        self.assertEqual(c.packets_scanned, 2)
        self.assertEqual(c.packet_days_carrying, 1)

    def test_a_metric_absent_everywhere_is_reported_unavailable(self):
        c = self.census([packet("2026-08-01", {"escaped": [1]})])["isolation_admissions"]
        self.assertEqual(c.kind, ie.UNAVAILABLE)
        self.assertEqual(c.source, "nowhere")
        self.assertIsNone(c.last_day)
        self.assertIsNone(c.days_stale_at_as_of)

    def test_packet_only_metrics_are_flagged_as_outside_the_forecast_layer(self):
        c = self.census([packet("2026-08-01", {"escaped": [1]})])["isolation_escapes"]
        self.assertFalse(c.in_frozen_extract)
        self.assertIn("ABSENT from the frozen extract", c.note)

    def test_occupancy_span_is_read_from_the_extract_not_the_packets(self):
        c = self.census(
            [packet("2026-07-01", {"patients_in_isolation_end_of_day": {"Ituri": 5}})],
            rows=rows_from([100, 110, 120]),
        )["isolation_occupancy"]
        self.assertTrue(c.in_frozen_extract)
        self.assertEqual(c.source, "frozen extract")
        self.assertEqual(c.extract_days_carrying, 3)
        self.assertEqual(c.last_day, "2026-01-03")

    def test_prior_day_census_is_never_counted_as_bed_capacity(self):
        """`patients_at_bed_j_minus_1` is a census. Reading it as capacity would
        manufacture the exact denominator the headroom refusal exists to withhold."""
        packets = [packet("2026-06-01", {"patients_at_bed_j_minus_1": {"Ituri": 247}})]
        c = self.census(packets)
        self.assertEqual(c["isolation_prior_day_census"].packet_days_carrying, 1)
        self.assertEqual(c["isolation_bed_capacity"].packet_days_carrying, 0)
        self.assertEqual(ie.headroom(rows_from([1, 2]), packets).capacity_observations_found, 0)

    def test_real_bed_capacity_is_found_where_it_is_published(self):
        packets = [packet("2026-06-11", detail={"patient_movement_by_province": [
            {"province": "Ituri", "beds_available": 349, "patients_in_bed_j_minus_1": 247}]})]
        h = ie.headroom(rows_from([1, 2]), packets)
        self.assertEqual(h.capacity_observations_found, 1)
        self.assertEqual(h.capacity_days, ("2026-06-11",))


# --- headroom refusal ---------------------------------------------------------

class HeadroomTests(unittest.TestCase):
    def test_headroom_refuses_and_says_what_is_missing(self):
        h = ie.headroom(rows_from([100, 200]), None)
        self.assertEqual(h.kind, ie.UNAVAILABLE)
        self.assertFalse(h.computable)
        self.assertTrue(h.reason)
        self.assertGreaterEqual(len(h.what_would_be_needed), 3)

    def test_headroom_exposes_no_numeric_percentage(self):
        """Regression guard: the whole point is that no headroom figure exists."""
        h = ie.headroom(rows_from([100, 200]), None)
        numeric = [f for f in h.__slots__
                   if isinstance(getattr(h, f), float)]
        self.assertEqual(numeric, [])


# --- pressure -----------------------------------------------------------------

class PressureTests(unittest.TestCase):
    def rows(self):
        out = []
        for i in range(6):
            day = (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat()
            out.append({
                "data_as_of": day,
                # Big carries 10x the caseload and grows twice as fast in beds;
                # Small grows slower in beds but off a far smaller caseload, so
                # per-case pressure must rank Small first.
                ie.PROVINCE_FIELD: {"Big": 100 + 4 * i, "Small": 40 + 2 * i, "Tiny": 5 + i},
                ie.SPLIT_FIELD: {
                    "Big": {"confirmed": 1100, "confirmed_deaths": 100},
                    "Small": {"confirmed": 120, "confirmed_deaths": 20},
                    "Tiny": {"confirmed": 4, "confirmed_deaths": 1},
                },
            })
        return out

    def test_pressure_ranks_by_growth_per_case_not_by_size(self):
        ranked = ie.province_pressure(self.rows(), window_days=14, min_active=30)
        stable = [p for p in ranked if p.denominator_stable]
        self.assertEqual([p.province for p in stable], ["Small", "Big"])
        small = stable[0]
        self.assertAlmostEqual(small.slope_per_day, 2.0)
        self.assertAlmostEqual(small.active_confirmed, 100.0)
        self.assertAlmostEqual(small.slope_per_100_active, 2.0)
        big = stable[1]
        self.assertAlmostEqual(big.slope_per_day, 4.0)
        self.assertAlmostEqual(big.slope_per_100_active, 0.4)  # 100 * 4 / 1000

    def test_a_tiny_denominator_is_flagged_and_never_ranked_above_a_real_one(self):
        ranked = ie.province_pressure(self.rows(), window_days=14, min_active=30)
        tiny = next(p for p in ranked if p.province == "Tiny")
        self.assertFalse(tiny.denominator_stable)
        self.assertIn("below the 30 floor", tiny.flag)
        # Its per-case number is enormous; it must still sort behind the stable ones.
        self.assertGreater(tiny.slope_per_100_active, ranked[0].slope_per_100_active)
        self.assertGreater(ranked.index(tiny), ranked.index(ranked[0]))

    def test_occupancy_per_active_case_may_exceed_one(self):
        """Wards hold suspects, so the ratio has no ceiling at 1.0 and must not
        be presented as a containment share."""
        ranked = ie.province_pressure(self.rows(), window_days=14, min_active=30)
        tiny = next(p for p in ranked if p.province == "Tiny")
        self.assertGreater(tiny.occupancy_per_active_confirmed, 1.0)


# --- turnover -----------------------------------------------------------------

class TurnoverTests(unittest.TestCase):
    def rows(self):
        occ = [100, 120, 110, 140]
        rec = [10, 12, 15, 15]
        dth = [5, 8, 10, 12]
        return rows_from(occ, cumulative_recovered=rec, confirmed_deaths_total=dth,
                         new_confirmed_today=[1, 2, 3, 4])

    def test_net_flow_and_implied_admissions_match_hand_computation(self):
        t = ie.turnover(self.rows(), None)
        # net +20, -10, +30 ; implied = net + d_recovered + d_deaths
        #   day2: 20 + 2 + 3 = 25 ; day3: -10 + 3 + 2 = -5 ; day4: 30 + 0 + 2 = 32
        self.assertEqual(t.net_flow_days, 3)
        self.assertAlmostEqual(t.net_flow_mean, 40 / 3)
        self.assertEqual([d.implied_admissions for d in t.days], [25, -5, 32])
        self.assertAlmostEqual(t.implied_admissions_mean, 52 / 3)

    def test_a_reporting_gap_is_dropped_rather_than_spread_across_it(self):
        rows = self.rows()
        rows[3]["data_as_of"] = "2026-01-09"  # five-day reporting gap before the last row
        t = ie.turnover(rows, None)
        self.assertEqual([d.day for d in t.days], ["2026-01-02", "2026-01-03"])

    def test_an_unvalidated_inference_is_never_reported_as_validated(self):
        t = ie.turnover(self.rows(), None)
        self.assertFalse(t.validated)
        self.assertIsNone(t.validation_days)
        self.assertIn("NOT VALIDATED", t.verdict)

    def test_the_inference_validates_when_it_actually_tracks_observations(self):
        """The rejection must come from the data, not from a hard-coded verdict."""
        packets = [packet(d, {"admissions_24h": {"Total": v}})
                   for d, v in (("2026-01-02", 25), ("2026-01-03", -5), ("2026-01-04", 32))]
        t = ie.turnover(self.rows(), packets)
        self.assertTrue(t.validated)
        self.assertEqual(t.validation_days, 3)
        self.assertAlmostEqual(t.correlation_with_observed, 1.0)
        self.assertIn("VALIDATED", t.verdict)

    def test_a_biased_inference_is_rejected(self):
        packets = [packet(d, {"admissions_24h": {"Total": v}})
                   for d, v in (("2026-01-02", 50), ("2026-01-03", -10), ("2026-01-04", 64))]
        t = ie.turnover(self.rows(), packets)
        self.assertFalse(t.validated)
        self.assertIn("REJECTED", t.verdict)
        self.assertAlmostEqual(t.median_recovery_ratio, 0.5)

    def test_an_unbiased_but_uncorrelated_inference_is_still_rejected(self):
        """Getting the average right is not getting the days right.

        Implied admissions here run 10..50 while observed admissions run in a
        different order entirely: the median ratio is exactly 1.0, so a check that
        only tested bias would pass it, but r = -0.30 and it tracks nothing.
        """
        occ = [100, 110, 130, 160, 200, 250]          # net flow 10, 20, 30, 40, 50
        rows = rows_from(occ, cumulative_recovered=[10] * 6, confirmed_deaths_total=[5] * 6)
        observed = [30, 50, 10, 40, 20]
        packets = [packet(f"2026-01-0{i + 2}", {"admissions_24h": {"Total": v}})
                   for i, v in enumerate(observed)]
        t = ie.turnover(rows, packets)
        self.assertEqual([d.implied_admissions for d in t.days], [10, 20, 30, 40, 50])
        self.assertAlmostEqual(t.median_recovery_ratio, 1.0)
        self.assertAlmostEqual(t.correlation_with_observed, -0.30)
        self.assertFalse(t.validated)
        self.assertIn("REJECTED", t.verdict)

    def test_column_oriented_admissions_read_the_national_total(self):
        obs = ie._observed_admissions([packet("2026-01-02", {
            "total_admissions": [47, 1, 0, 48], "columns": ["Ituri", "NK", "SK", "Total"]})])
        self.assertEqual(obs, {"2026-01-02": 48.0})


# --- forecasts ----------------------------------------------------------------

class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.rows = of.load_rows(SERIES)

    def test_ladder_is_deterministic(self):
        a = ie.occupancy_threshold_forecasts(self.rows, n_paths=500)
        b = ie.occupancy_threshold_forecasts(self.rows, n_paths=500)
        self.assertEqual([f.probability for f in a], [f.probability for f in b])

    def test_probabilities_fall_as_the_threshold_rises(self):
        for shape in ("ends_above", "ever_above"):
            ladder = [f for f in ie.occupancy_threshold_forecasts(self.rows)
                      if f.shape == shape]
            ps = [f.probability for f in ladder]
            self.assertEqual(ps, sorted(ps, reverse=True), shape)

    def test_ever_above_is_never_below_ends_above_at_the_same_threshold(self):
        by = {(f.shape, f.threshold): f.probability
              for f in ie.occupancy_threshold_forecasts(self.rows)}
        for threshold in ie.DEFAULT_THRESHOLDS:
            self.assertGreaterEqual(by[("ever_above", threshold)],
                                    by[("ends_above", threshold)], threshold)

    def test_the_horizon_is_walked_from_the_last_data_day_not_the_as_of(self):
        f = ie.occupancy_threshold_forecasts(self.rows, as_of=AS_OF, n_paths=500)[0]
        self.assertEqual(f.last_observed_day, "2026-08-28")
        self.assertEqual(f.days_between_last_data_and_as_of, 4)

    def test_ladder_seeds_cannot_collide_with_the_pinned_block(self):
        """A ladder row that reused a pinned seed would look like a re-price."""
        with open(LEDGER, encoding="utf-8") as handle:
            ledger = json.load(handle)
        pinned = {p["generator"]["seed"] for b in ledger["blocks"] for p in b["points"]}
        ladder = {f.seed for f in ie.occupancy_threshold_forecasts(self.rows, n_paths=200)}
        self.assertEqual(pinned & ladder, set())


# --- Block 6 consistency ------------------------------------------------------

class Block6ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.rows = of.load_rows(SERIES)

    def test_block6_isolation_pins_reproduce_exactly(self):
        checks = {c.pin_id.rsplit(":", 1)[-1]: c for c in ie.block6_consistency(self.rows, LEDGER)}
        self.assertEqual(set(checks), {"iso-below-850", "iso-above-1000"})
        below, above = checks["iso-below-850"], checks["iso-above-1000"]
        self.assertEqual(below.reproduced_probability, 0.1285)
        self.assertEqual(above.reproduced_probability, 0.5895)
        # The doc and the pin rationale quote these to three decimals.
        self.assertEqual(round(below.reproduced_probability, 3), 0.129)
        self.assertEqual(round(above.reproduced_probability, 3), 0.590)
        for chk in checks.values():
            self.assertTrue(chk.matches, f"{chk.pin_id} drifted by {chk.delta:+.4f}")
            self.assertEqual(chk.delta, 0.0)

    def test_consistency_check_detects_a_tampered_ledger(self):
        """A check that cannot fail proves nothing."""
        with open(LEDGER, encoding="utf-8") as handle:
            ledger = copy.deepcopy(json.load(handle))
        for blk in ledger["blocks"]:
            for point in blk["points"]:
                if point["metric"] == ie.OCCUPANCY_METRIC:
                    point["probability"] = 0.4242
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "tampered.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            checks = ie.block6_consistency(self.rows, path)
        self.assertTrue(checks)
        self.assertFalse(any(c.matches for c in checks))

    def test_pinned_probabilities_survive_an_independent_seed(self):
        """An exact match on the ledger's own seed proves the pipeline, not the
        number. Re-pricing from a different seed is what tests the number."""
        agreements = ie.seed_agreement(
            ie.block6_consistency(self.rows, LEDGER),
            ie.occupancy_threshold_forecasts(self.rows),
        )
        self.assertEqual(len(agreements), 2)
        by = {a.pin_id.rsplit(":", 1)[-1]: a for a in agreements}
        # ends_below 850 has no ends_below row in the ladder, so it must be
        # matched against its complement rather than silently skipped.
        self.assertEqual(by["iso-below-850"].comparison, "complement")
        self.assertEqual(by["iso-above-1000"].comparison, "same shape")
        for a in agreements:
            self.assertTrue(a.within_two_stderr,
                            f"{a.pin_id} residual {a.residual:+.4f} exceeds 2se {2 * a.combined_stderr:.4f}")

    def test_seed_agreement_flags_a_real_disagreement(self):
        check = ie.ConsistencyCheck(
            pin_id="p", shape="ends_above", threshold=1000.0, seed=1,
            pinned_probability=0.20, reproduced_probability=0.20, delta=0.0, matches=True)
        far = ie.ThresholdForecast(
            kind=ie.FORECAST, question="q", shape="ends_above", threshold=1000.0,
            probability=0.80, monte_carlo_stderr=0.003, horizon_days=30,
            as_of="2026-09-01", last_observed_day="2026-08-28", last_observed_value=896.0,
            days_between_last_data_and_as_of=4, seed=9, n_paths=20000, block=5)
        a = ie.seed_agreement([check], [far])[0]
        self.assertAlmostEqual(a.residual, 0.60)
        self.assertFalse(a.within_two_stderr)

    def test_a_ledger_without_isolation_pins_refuses_rather_than_passing_vacuously(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "empty.json"
            path.write_text(json.dumps({"blocks": [{"points": []}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                ie.block6_consistency(self.rows, path)


# --- the real substrate -------------------------------------------------------

class RealSubstrateTests(unittest.TestCase):
    """Facts about the live extract that the companion doc asserts.

    These pin the claims in `docs/isolation-events.md` to the substrate. If the
    frozen extract is ever replaced, they fail loudly rather than letting the doc
    drift into describing data that is no longer there.
    """

    def setUp(self):
        self.rows = of.load_rows(SERIES)

    def test_occupancy_is_at_its_maximum_and_is_not_monotone(self):
        t = ie.occupancy_trajectory(self.rows, as_of=AS_OF)
        self.assertEqual((t.first_value, t.last_value), (258.0, 896.0))
        self.assertTrue(t.at_all_time_high)
        self.assertFalse(t.monotone_nondecreasing)
        self.assertGreater(t.falls, 20)

    def test_most_of_the_apparent_volatility_is_reporting_churn(self):
        c = ie.coverage_churn(self.rows)
        self.assertLess(c.constant_panel_sd, c.raw_sd)
        self.assertGreater(c.variance_share_from_churn, 0.5)
        # The drift survives the correction; only the spread collapses.
        self.assertLess(abs(c.raw_mean - c.constant_panel_mean), 1.0)

    def test_pressure_is_outside_the_epicentre(self):
        ranked = [p for p in ie.province_pressure(self.rows) if p.denominator_stable]
        self.assertEqual(ranked[-1].province, "Ituri")
        self.assertIn(ranked[0].province, {"Haut-Uele", "Nord-Kivu"})

    def test_headroom_is_not_computable_from_this_substrate(self):
        packets_dir = ie.find_packets_dir()
        packets = ie.read_packets(packets_dir) if packets_dir else None
        h = ie.headroom(self.rows, packets)
        self.assertFalse(h.computable)
        if packets:
            self.assertLessEqual(h.capacity_observations_found, 1)


class PacketSubstrateTests(unittest.TestCase):
    """Skipped where the read-only sibling checkout is not present."""

    def setUp(self):
        self.dir = ie.find_packets_dir()
        if self.dir is None:
            self.skipTest("lovs-evidence-mcp packet directory not available")
        self.packets = ie.read_packets(self.dir)
        self.rows = of.load_rows(SERIES)

    def test_the_three_lovs_flow_metrics_exist_but_are_discontinued(self):
        census = {m.metric: m for m in
                  ie.audit_isolation_metrics(self.rows, self.packets, as_of=AS_OF)}
        occupancy = census["isolation_occupancy"]
        for name in ("isolation_escapes", "isolation_admissions", "isolation_deaths"):
            m = census[name]
            self.assertGreater(m.packet_days_carrying, 0, f"{name} should exist in the packets")
            self.assertFalse(m.in_frozen_extract, name)
            self.assertGreater(m.days_stale_at_as_of, occupancy.days_stale_at_as_of,
                               f"{name} should be staler than the occupancy census")

    def test_the_flow_table_was_dropped_newest_column_last(self):
        census = {m.metric: m for m in
                  ie.audit_isolation_metrics(self.rows, self.packets, as_of=AS_OF)}
        deaths = census["isolation_deaths"].days_stale_at_as_of
        admissions = census["isolation_admissions"].days_stale_at_as_of
        escapes = census["isolation_escapes"].days_stale_at_as_of
        self.assertGreater(deaths, admissions)
        self.assertGreater(admissions, escapes)

    def test_the_turnover_inference_is_rejected_against_the_real_substrate(self):
        t = ie.turnover(self.rows, self.packets)
        self.assertGreaterEqual(t.validation_days, 20)
        self.assertFalse(t.validated)
        self.assertIn("REJECTED", t.verdict)
        self.assertLess(t.median_recovery_ratio, 0.8)

    def test_report_assembles_without_a_gap(self):
        r = ie.report(self.rows, self.dir, as_of=AS_OF)
        for key in ("availability", "trajectory", "coverage_churn", "pressure",
                    "headroom", "turnover", "forecasts", "block6"):
            self.assertIn(key, r)
        self.assertTrue(all(c.matches for c in r["block6"]))

    def test_report_runs_without_the_packet_substrate(self):
        r = ie.report(self.rows, packets_dir=tempfile.gettempdir() + "/definitely-not-here",
                      as_of=AS_OF)
        self.assertEqual(r["packets_scanned"], 0)
        self.assertFalse(r["turnover"].validated)


class DocConsistencyTests(unittest.TestCase):
    """The companion doc may not contain a figure the module does not produce.

    House rule: never author a number that should be derived. This asserts the
    load-bearing figures in `docs/isolation-events.md` are the ones
    `isolationevents` actually computes, so the doc fails loudly when the
    substrate moves instead of quietly describing data that is no longer there.
    """

    DOC = REPO / "docs" / "isolation-events.md"

    def setUp(self):
        if not self.DOC.exists():
            self.skipTest("companion doc not present")
        # The doc uses a typographic minus; normalise before substring matching.
        self.text = self.DOC.read_text(encoding="utf-8").replace("\u2212", "-")
        self.rows = of.load_rows(SERIES)
        packets_dir = ie.find_packets_dir()
        self.packets = ie.read_packets(packets_dir) if packets_dir else None

    def assertQuoted(self, label, value):
        self.assertIn(value, self.text, f"{label}: {value!r} is not in the doc")

    def test_trajectory_figures_are_quoted_as_computed(self):
        t = ie.occupancy_trajectory(self.rows, as_of=AS_OF)
        for label, value in (
            ("n_observations", str(t.n_observations)),
            ("first", f"{t.first_value:.0f}"),
            ("last", f"{t.last_value:.0f}"),
            ("moves", f"{t.rises} up / {t.falls} down / {t.flats} flat"),
            ("mean", f"{t.mean_change_per_day:+.2f}"),
            ("median", f"{t.median_change_per_day:+.2f}"),
            ("ols", f"{t.ols_slope_per_day:+.2f}"),
            ("largest rise", f"{t.largest_single_rise:+.0f} on {t.largest_single_rise_day}"),
            ("largest fall", f"{t.largest_single_fall:+.0f} on {t.largest_single_fall_day}"),
            ("doubling", f"{t.occupancy_doubling_days:.1f}"),
        ):
            self.assertQuoted(label, value)

    def test_churn_figures_are_quoted_as_computed(self):
        c = ie.coverage_churn(self.rows)
        day, raw, held, _ = c.largest_churn_artifact
        for label, value in (
            ("raw sd", f"{c.raw_sd:.1f}"),
            ("panel sd", f"{c.constant_panel_sd:.1f}"),
            ("raw mean", f"{c.raw_mean:+.1f}"),
            ("panel mean", f"{c.constant_panel_mean:+.1f}"),
            ("variance share", f"{c.variance_share_from_churn:.1%}"),
            ("pairs", str(c.comparable_pairs)),
            ("panel days", str(c.panel_days)),
            ("membership changes", str(c.days_with_membership_change)),
            ("worst day", day),
            ("worst raw", f"{raw:+.0f}"),
            ("worst held", f"{held:+.0f}"),
        ):
            self.assertQuoted(label, value)

    def test_forecast_and_pin_figures_are_quoted_as_computed(self):
        forecasts = ie.occupancy_threshold_forecasts(self.rows, as_of=AS_OF)
        for f in forecasts:
            self.assertQuoted(f"{f.shape} {f.threshold:.0f}", f"{f.probability:.4f}")
        checks = ie.block6_consistency(self.rows, LEDGER)
        for chk in checks:
            self.assertQuoted(chk.pin_id, f"{chk.pinned_probability:.4f}")
            self.assertQuoted(chk.pin_id, f"{chk.reproduced_probability:.3f}")
        for a in ie.seed_agreement(checks, forecasts):
            self.assertQuoted(f"{a.pin_id} residual", f"{a.residual:+.4f}")
            self.assertQuoted(f"{a.pin_id} 2se", f"{2 * a.combined_stderr:.4f}")

    def test_pressure_figures_are_quoted_as_computed(self):
        for p in ie.province_pressure(self.rows):
            self.assertQuoted(p.province, p.province)
            self.assertQuoted(f"{p.province} occupancy", f"{p.occupancy:.0f}")
            self.assertQuoted(f"{p.province} active", f"{p.active_confirmed:.0f}")
            self.assertQuoted(f"{p.province} per100", f"{p.slope_per_100_active:+.3f}")

    def test_availability_and_turnover_figures_are_quoted_as_computed(self):
        if self.packets is None:
            self.skipTest("packet substrate not available")
        for m in ie.audit_isolation_metrics(self.rows, self.packets, as_of=AS_OF):
            self.assertQuoted(m.metric, f"{m.packet_days_carrying}/{m.packets_scanned}")
            self.assertQuoted(f"{m.metric} last", m.last_day)
            self.assertQuoted(f"{m.metric} stale", f"{m.days_stale_at_as_of}d")
        t = ie.turnover(self.rows, self.packets)
        for label, value in (
            ("pairs", str(t.net_flow_days)),
            ("net mean", f"{t.net_flow_mean:+.1f}"),
            ("net sd", f"{t.net_flow_sd:.1f}"),
            ("implied mean", f"{t.implied_admissions_mean:.1f}"),
            ("validation days", str(t.validation_days)),
            ("ratio", f"{t.median_recovery_ratio:.0%}"),
            ("r", f"{t.correlation_with_observed:.2f}"),
            ("mae", f"{t.mean_absolute_error:.0f}"),
        ):
            self.assertQuoted(label, value)


if __name__ == "__main__":
    unittest.main()
