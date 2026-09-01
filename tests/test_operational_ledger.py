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
from lovs.forecast import pins_block7 as pin7

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
        self.assertEqual(report["summary"]["by_status"], {ores.STATUS_PENDING: 25})

    def test_stale_series_does_not_resolve_no(self):
        """The corridor resolver's defect, closed here before it can bite."""
        report = ores.build_report(self.ledger, self.rows, dt.date(2026, 10, 2))
        # The property under test is that a stale series scores NOTHING, not that
        # it produces one particular excluded status. Asserting the exact status
        # set made this test track ledger growth instead of the guard.
        scored = [p for p in report["pins"]
                  if p["status"] in (ores.STATUS_YES, ores.STATUS_NO)]
        self.assertEqual(scored, [], "a stale series must not resolve any pin")
        self.assertIsNone(report["summary"]["mean_brier"])

    def test_resolves_when_the_series_covers_the_window(self):
        rows = list(self.rows)
        rows.append({"sitrep": 999, "data_as_of": "2026-10-01",
                     "contact_followup_percent": 60.0, "lab_positivity_percent": 30.0,
                     "hospital_isolation_total": 1200.0, "new_confirmed_today": 120.0,
                     "alerts_reported": 2400.0, "health_zones_touched": 75.0})
        report = ores.build_report(self.ledger, rows, dt.date(2026, 10, 2))
        # Assert on Block 6 specifically rather than a whole-ledger count, so
        # appending a future block does not break a test about Block 6.
        six = {p["pin_id"] for p in self.ledger["blocks"][0]["points"]}
        resolved = {p["pin_id"] for p in report["pins"]
                    if p["status"] in (ores.STATUS_YES, ores.STATUS_NO)}
        self.assertTrue(six <= resolved, f"unresolved Block 6 pins: {six - resolved}")
        self.assertIsNotNone(report["summary"]["mean_brier"])
        self.assertIn("skill_vs_base_rate", report["summary"])

    def test_resolver_never_writes_the_ledger(self):
        before = LEDGER.read_bytes()
        ores.build_report(self.ledger, self.rows, dt.date(2026, 10, 2))
        self.assertEqual(LEDGER.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()


class BlockSevenTests(unittest.TestCase):
    """Block 7: structural pins, independent series, three methods."""

    def setUp(self):
        led = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.b6 = led["blocks"][0]
        self.b7 = next(b for b in led["blocks"] if b.get("label") == "Block 7")
        self.pins = self.b7["points"]

    def test_committed_probabilities_are_generated(self):
        regenerated = {p["pin_id"]: p["probability"] for p in pin7.build_pins()}
        committed = {p["pin_id"]: p["probability"] for p in self.pins}
        self.assertEqual(committed, regenerated,
                         "a Block 7 probability does not match its generator")

    def test_only_the_stated_series_is_shared_with_block_6(self):
        """The independence claim, asserted at exactly the strength it is true.

        An earlier draft of the Block 7 rationale claimed no shared series at
        all. This test caught that as false: the termination pin reads
        new_confirmed_today, which Block 6 pins twice. The claim was corrected
        rather than the pin dropped, and the permitted overlap is pinned here so
        it cannot silently widen.
        """
        six = {p["metric"] for p in self.b6["points"]}
        shared = six & {p["metric"] for p in self.pins}
        self.assertEqual(shared, {"new_confirmed_today"},
                         f"unexpected series overlap {shared}; the rationale states "
                         "exactly one, and any other is undeclared correlation")

    def test_the_shared_series_carries_distinct_observables(self):
        """Correlated is acceptable; identical is not."""
        both = [p for p in self.b6["points"] + self.pins
                if p["metric"] == "new_confirmed_today"]
        keys = [(p["shape"], p["threshold"]) for p in both]
        self.assertEqual(len(keys), len(set(keys)),
                         "two pins on new_confirmed_today share shape and threshold; "
                         "they would resolve from one boolean")

    def test_level_bootstrap_pins_share_nothing_with_block_6(self):
        six = {p["metric"] for p in self.b6["points"]}
        level = {p["metric"] for p in self.pins if p["method"] == "level_bootstrap"}
        self.assertEqual(six & level, set())

    def test_three_methods_are_represented(self):
        methods = {p["method"] for p in self.pins}
        self.assertEqual(methods,
                         {"level_bootstrap", "gap_bootstrap", "termination_bootstrap"})

    def test_reaches_above_the_block_6_ceiling(self):
        six_max = max(p["probability"] for p in self.b6["points"])
        self.assertGreater(max(p["probability"] for p in self.pins), six_max,
                           "Block 7 exists partly to measure calibration above Block 6's "
                           "ceiling; it must actually reach past it")

    def test_spans_at_least_six_deciles(self):
        ps = [p["probability"] for p in self.pins]
        self.assertGreaterEqual(len({int(p * 10) for p in ps}), 6)

    def test_pin_ids_are_unique_across_the_whole_ledger(self):
        led = json.loads(LEDGER.read_text(encoding="utf-8"))
        ids = [p["pin_id"] for b in led["blocks"] for p in b["points"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_derived_series_are_reconstructible(self):
        rows = of.load_rows(SERIES)
        self.assertGreater(len(pin7.publication_lag_series(rows)), 50)
        self.assertGreater(len(pin7.province_isolation_series(rows, "Nord-Kivu")), 50)
        self.assertEqual(pin7.province_isolation_series(rows, "Atlantis"), [])

    def test_no_pin_is_pre_resolved(self):
        rows = of.load_rows(SERIES)
        pinned = dt.date.fromisoformat(self.b7["pinned_at"])
        for name in ("__publication_lag", "__nordkivu_isolation"):
            obs = pin7._series_for(rows, name)
            self.assertTrue(all(o.date < pinned for o in obs), name)


class WholeLedgerResolutionTests(unittest.TestCase):
    """Every pin in every block must be resolvable when the data arrives.

    Block 7 pins two derived series and three non-threshold rules. The resolver
    originally knew none of them, so all six would have returned
    unscoreable_no_series on 1 October and a 30-day window would have been spent
    for nothing. A pin nobody can score is worse than no pin, because it looks
    like a commitment.
    """

    def _extended_rows(self):
        rows = list(of.load_rows(SERIES))
        base = dt.date(2026, 8, 29)
        for i in range(34):
            day = base + dt.timedelta(days=i)
            rows.append({
                "sitrep": 900 + i, "data_as_of": day.isoformat(),
                "published_at": (day + dt.timedelta(days=1)).isoformat(),
                "contact_followup_percent": 83.0, "lab_positivity_percent": 16.0,
                "hospital_isolation_total": 980.0, "new_confirmed_today": 70.0,
                "alerts_reported": 2100.0, "health_zones_touched": 68.0,
                "cumulative_recovered": 1750.0, "new_confirmed_deaths_today": 34.0,
                "samples_analyzed": 640.0,
                "isolation_by_province": {"Ituri": 540, "Nord-Kivu": 290},
            })
        return rows

    def test_every_pin_resolves_when_the_series_covers_the_window(self):
        report = ores.build_report(ores.load_ledger(LEDGER), self._extended_rows(),
                                   dt.date(2026, 10, 2))
        unscoreable = [p["pin_id"] for p in report["pins"]
                       if p["status"] not in (ores.STATUS_YES, ores.STATUS_NO)]
        self.assertEqual(unscoreable, [], f"unresolvable pins: {unscoreable}")
        self.assertEqual(report["summary"]["resolved_count"],
                         report["summary"]["total_pins"])

    def test_an_unknown_resolution_rule_fails_loudly(self):
        """The guess-refusal, asserted rather than trusted."""
        ledger = ores.load_ledger(LEDGER)
        block = next(b for b in ledger["blocks"] if b.get("label") == "Block 7")
        pin = dict(next(p for p in block["points"] if p["shape"] == "derived"))
        pin["resolution_rule"] = {"rule": "invented_rule"}
        got = ores.resolve_pin(pin, block, self._extended_rows(), dt.date(2026, 10, 2))
        self.assertEqual(got["status"], ores.STATUS_NO_DATA)
        self.assertNotIn("outcome", got)

    def test_derived_series_resolution_matches_the_generator(self):
        """The resolver must rebuild a derived series the way the generator did."""
        from lovs.forecast import pins_block7 as p7
        rows = of.load_rows(SERIES)
        self.assertEqual(ores._derived_series("publication_lag", rows),
                         p7.publication_lag_series(rows))
        self.assertEqual(ores._derived_series("nordkivu_isolation", rows),
                         p7.province_isolation_series(rows, "Nord-Kivu"))
