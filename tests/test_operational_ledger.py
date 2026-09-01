"""Tests for the operational calibration ledger and its forecaster.

The load-bearing test here is `test_committed_probabilities_are_generated`:
it re-runs the generator and asserts the committed ledger matches. A pinned
probability that someone typed by hand, or nudged after seeing the world,
fails the suite. That is the whole guarantee this ledger rests on.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import unittest

from lovs.forecast import opsforecast as of
from lovs.forecast import opsresolver as ores
from lovs.forecast import pins as pinmod

REPO = pathlib.Path(__file__).resolve().parent.parent
LEDGER = REPO / "data" / "operational-calibration-ledger.json"
SERIES = REPO / "data" / "operational-series-2026-09-01.json"


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.rows = of.load_rows(SERIES)

    def test_series_is_chronological_and_deduplicated(self):
        for metric in ("contact_followup_percent", "lab_positivity_percent",
                       "hospital_isolation_total", "new_confirmed_today"):
            obs = of.series(self.rows, metric)
            dates = [o.date for o in obs]
            self.assertEqual(dates, sorted(dates), metric)
            self.assertEqual(len(dates), len(set(dates)), f"{metric} has duplicate days")

    def test_gapped_days_are_normalised_per_elapsed_day(self):
        """A three-day reporting gap must not enter the bootstrap as one huge move."""
        obs = [of.Observation(dt.date(2026, 1, 1), 10.0),
               of.Observation(dt.date(2026, 1, 4), 40.0)]
        self.assertEqual(of.diffs(obs), [10.0])

    def test_bootstrap_is_deterministic(self):
        obs = of.series(self.rows, "contact_followup_percent")
        a = of.probability(obs, 30, of.ends_below(75), seed=1, n_paths=500)
        b = of.probability(obs, 30, of.ends_below(75), seed=1, n_paths=500)
        self.assertEqual(a, b)

    def test_bounds_are_respected(self):
        obs = of.series(self.rows, "contact_followup_percent")
        paths = of._paths(obs, 30, 200, 5, seed=3, lo=0.0, hi=100.0)
        flat = [v for p in paths for v in p]
        self.assertGreaterEqual(min(flat), 0.0)
        self.assertLessEqual(max(flat), 100.0)

    def test_too_short_a_series_refuses_rather_than_guesses(self):
        obs = [of.Observation(dt.date(2026, 1, i + 1), float(i)) for i in range(4)]
        with self.assertRaises(ValueError):
            of.probability(obs, 30, of.ends_below(1), seed=1, n_paths=10)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.block = self.ledger["blocks"][0]
        self.pins = self.block["points"]

    def test_committed_probabilities_are_generated(self):
        """The anti-authoring guarantee. Re-run the generator; values must match."""
        regenerated = {p["pin_id"]: p["probability"] for p in pinmod.build_pins()}
        committed = {p["pin_id"]: p["probability"] for p in self.pins}
        self.assertEqual(
            committed, regenerated,
            "A committed probability does not match what the generator produces from "
            "the frozen series. Either the series changed, or a probability was edited "
            "by hand. Neither is allowed after a pin.")

    def test_every_pin_is_a_distinct_observable(self):
        keys = [(p["metric"], p["shape"], p["threshold"]) for p in self.pins]
        self.assertEqual(len(keys), len(set(keys)),
                         "two pins share metric, shape and threshold; they would "
                         "resolve from one boolean")

    def test_probabilities_span_more_than_one_bin(self):
        ps = sorted(p["probability"] for p in self.pins)
        self.assertGreaterEqual(
            ps[-1] - ps[0], 0.4,
            "the pin set must span enough of the range to read a reliability curve; "
            "a narrow band is the defect that made corridor Blocks 1-4 unreadable")
        self.assertGreaterEqual(len({int(p * 10) for p in ps}), 5,
                                "pins should occupy at least 5 distinct deciles")

    def test_no_pin_is_pre_resolved_by_the_series(self):
        rows = of.load_rows(SERIES)
        pinned = dt.date.fromisoformat(self.block["pinned_at"])
        for pin in self.pins:
            obs = of.series(rows, pin["metric"])
            self.assertTrue(all(o.date < pinned for o in obs),
                            f"{pin['pin_id']}: series carries in-window data at pin time")

    def test_bias_tests_are_the_low_band_pins(self):
        bias = sorted(p["probability"] for p in self.pins if p["bias_test"])
        rest = sorted(p["probability"] for p in self.pins if not p["bias_test"])
        self.assertTrue(bias, "the block should carry explicit low-band bias tests")
        self.assertLess(max(bias), min(rest))

    def test_every_pin_carries_a_question_and_a_rationale(self):
        for pin in self.pins:
            self.assertTrue(pin["public_question"].strip(), pin["pin_id"])
            self.assertTrue(pin["rationale"].strip(), pin["pin_id"])
            self.assertIn("seed", pin["generator"])


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.ledger = ores.load_ledger(LEDGER)
        self.rows = of.load_rows(SERIES)

    def test_all_pins_pending_before_the_window_closes(self):
        report = ores.build_report(self.ledger, self.rows, dt.date(2026, 9, 15))
        self.assertEqual(report["summary"]["by_status"], {ores.STATUS_PENDING: 12})

    def test_stale_series_does_not_resolve_no(self):
        """The corridor resolver's defect, closed here before it can bite."""
        report = ores.build_report(self.ledger, self.rows, dt.date(2026, 10, 2))
        statuses = {p["status"] for p in report["pins"]}
        self.assertEqual(statuses, {ores.STATUS_STALE})
        self.assertIsNone(report["summary"]["mean_brier"])

    def test_resolves_when_the_series_covers_the_window(self):
        rows = list(self.rows)
        rows.append({"sitrep": 999, "data_as_of": "2026-10-01",
                     "contact_followup_percent": 60.0, "lab_positivity_percent": 30.0,
                     "hospital_isolation_total": 1200.0, "new_confirmed_today": 120.0,
                     "alerts_reported": 2400.0, "health_zones_touched": 75.0})
        report = ores.build_report(self.ledger, rows, dt.date(2026, 10, 2))
        self.assertEqual(report["summary"]["resolved_count"], 12)
        self.assertIsNotNone(report["summary"]["mean_brier"])
        self.assertIn("skill_vs_base_rate", report["summary"])

    def test_resolver_never_writes_the_ledger(self):
        before = LEDGER.read_bytes()
        ores.build_report(self.ledger, self.rows, dt.date(2026, 10, 2))
        self.assertEqual(LEDGER.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
