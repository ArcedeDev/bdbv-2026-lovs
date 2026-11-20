"""Tests for the pooled forecast record and the recalibration engine.

The two properties that matter here are both about refusing to fool ourselves:
a map must not be fitted on a corpus too small to support one, and a fitted map
must be scored out of sample. An in-sample Brier on a flexible monotone fit is
always flattering and always meaningless.
"""
from __future__ import annotations

import dataclasses
import json
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lovs.forecast import recalibration as rc
from lovs.forecast import record as rec

REPO = Path(__file__).resolve().parent.parent


def _without_opt_in():
    """The environment a public reader has: no Track B record named."""
    env = {k: v for k, v in os.environ.items() if k != rec.TRACK_B_ENV}
    return mock.patch.dict(os.environ, env, clear=True)


def _track_b_file(directory: str, hypothesis_ids: list[str]) -> Path:
    path = Path(directory) / "tb.json"
    entry = {"scored_at": "2026-09-01", "per_hypothesis": [
        {"hypothesis_id": hid, "confidence": 0.7, "outcome": 1,
         "tier": "clean_binary", "hypothesis_class": "event"} for hid in hypothesis_ids]}
    path.write_text(json.dumps({"entries": [entry]}))
    return path


def _synthetic(n: int, seed: int, bias: float) -> list[rec.ScoredForecast]:
    """A forecaster with real discrimination whose prices are wrong by `bias`."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = round(rng.uniform(0.05, 0.95), 2)
        rows.append(rec.ScoredForecast(
            system="synthetic", block_id="s", forecast_id=f"s{i}", probability=p,
            outcome=int(rng.random() < p ** bias), registered_at="2026-01-01",
            resolves_at="2026-02-01", horizon_days=30, method="synthetic",
            metric="m", question_shape="q", n_observations_at_pin=80,
            bias_test=False, geography_class=None))
    return rows


class RecordTests(unittest.TestCase):
    def test_pools_every_reachable_system(self):
        built = rec.build()
        self.assertGreater(built["_meta"]["row_count"], 0)
        self.assertIn("corridor", built["_meta"]["systems"])

    def test_a_missing_source_is_reported_not_raised(self):
        """A sibling repo may be absent; a thin corpus must never look complete."""
        with tempfile.TemporaryDirectory() as tmp:
            built = rec.build(track_b=Path(tmp) / "nope.json")
            self.assertIn("idb_track_b", built["_meta"]["sources_missing"])
            self.assertFalse(built["_meta"]["sources_found"]["idb_track_b"])

    def test_the_default_record_uses_this_repository_only(self):
        """The private Track B record once joined by a path relative to the checkout,
        so a worktree beside the private repository built a different record from the
        canonical checkout, CI and a public reader. Unnamed, it never joins."""
        with _without_opt_in():
            built = rec.build()
        self.assertFalse(built["_meta"]["sources_found"]["idb_track_b"])
        self.assertIn("idb_track_b", built["_meta"]["sources_missing"])
        self.assertNotIn("idb_track_b", built["_meta"]["systems"])

    def test_track_b_joins_when_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _track_b_file(tmp, ["tb-only-h1"])
            with _without_opt_in():
                by_argument = rec.build(track_b=path)
            with _without_opt_in(), mock.patch.dict(os.environ, {rec.TRACK_B_ENV: str(path)}):
                by_environment = rec.build()
        for built in (by_argument, by_environment):
            self.assertTrue(built["_meta"]["sources_found"]["idb_track_b"])
            self.assertIn("idb_track_b", built["_meta"]["systems"])

    def test_track_b_history_is_not_double_counted(self):
        """Each Track B entry re-scores the whole set; pooling all entries would
        count one forecast once per scoring run."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tb.json"
            entry = {"scored_at": "2026-09-01", "per_hypothesis": [
                {"hypothesis_id": "h1", "confidence": 0.7, "outcome": 1,
                 "tier": "clean_binary", "hypothesis_class": "event"}]}
            path.write_text(json.dumps({"entries": [entry, entry, entry]}))
            self.assertEqual(len(rec._track_b_rows(path)), 1)

    def test_brier_and_band_are_derived_not_stored(self):
        import dataclasses

        row = _synthetic(1, 1, 1.0)[0]
        self.assertAlmostEqual(row.brier, (row.probability - row.outcome) ** 2)
        for probability, expected in ((0.34, "0.3"), (0.05, "0.0"), (0.999, "0.9")):
            moved = dataclasses.replace(row, probability=probability)
            self.assertEqual(moved.band, expected)


class DeduplicationTests(unittest.TestCase):
    """The IDB Track B snapshot was captured FROM the research store, so the two
    overlap by construction. Pooling both without dedup double-counts exactly the
    rows they share, and does it silently: the pooled n simply looks larger."""

    def test_the_overlap_is_actually_caught(self):
        """A Track B row re-stating a research-store hypothesis is dropped, and the
        research store keeps it. Runs on every checkout, with no private file."""
        with _without_opt_in():
            shared = rec.build()["rows"]
        shared_id = next(r["forecast_id"] for r in shared if r["system"] == "research_store")
        with tempfile.TemporaryDirectory() as tmp:
            built = rec.build(track_b=_track_b_file(tmp, [shared_id, "tb-only-h1"]))
        self.assertEqual(built["_meta"]["duplicate_detail"],
                         [{"forecast_id": shared_id, "dropped_from": "idb_track_b"}],
                         "catching no duplicate means the dedup is not running")
        kept = {r["forecast_id"]: r["system"] for r in built["rows"]}
        self.assertEqual(kept[shared_id], "research_store")
        self.assertEqual(kept["tb-only-h1"], "idb_track_b")

    @unittest.skipUnless(
        os.environ.get(rec.TRACK_B_ENV, "").strip(),
        f"the private IDB Track B record joins only when {rec.TRACK_B_ENV} names it; "
        "test_the_overlap_is_actually_caught covers the dedup without it.")
    def test_the_named_private_record_overlaps_the_research_store(self):
        built = rec.build()
        self.assertGreater(built["_meta"]["duplicates_dropped"], 0,
                           "the two sources are known to overlap; catching zero "
                           "duplicates means the dedup is not running")

    def test_no_forecast_id_appears_twice(self):
        ids = [r["forecast_id"] for r in rec.build()["rows"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_research_store_wins_the_overlap(self):
        """Precedence is documented; assert it rather than trusting the order."""
        built = rec.build()
        dropped = {d["forecast_id"] for d in built["_meta"]["duplicate_detail"]}
        kept = {r["forecast_id"]: r["system"] for r in built["rows"]}
        for forecast_id in dropped:
            self.assertEqual(kept.get(forecast_id), "research_store", forecast_id)

    def test_dedup_survives_a_source_being_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = rec.build(research_store=Path(tmp) / "gone.json")
            ids = [r["forecast_id"] for r in built["rows"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertIn("research_store", built["_meta"]["sources_missing"])


def _grouped(n_groups: int, rungs: int, seed: int) -> list[rec.ScoredForecast]:
    """`n_groups` outcomes, each priced at `rungs` thresholds of one realised value.

    Within a group the outcomes are nested, as threshold rungs are: one draw decides
    every rung, and a higher price never resolves NO where a lower one resolved YES.
    """
    rng = random.Random(seed)
    rows = []
    for g in range(n_groups):
        draw = rng.random()
        for k in range(rungs):
            p = round(rng.uniform(0.05, 0.95), 2)
            rows.append(_row(f"g{g}r{k}", p, int(draw < p ** 0.5), group=f"g{g}"))
    return rows


def _row(forecast_id: str, probability: float, outcome: int, group: str) -> rec.ScoredForecast:
    return rec.ScoredForecast(
        system="synthetic", block_id="s", forecast_id=forecast_id, probability=probability,
        outcome=outcome, registered_at="2026-01-01", resolves_at="2026-02-01",
        horizon_days=30, method="synthetic", metric="m", question_shape="q",
        n_observations_at_pin=80, bias_test=False, geography_class=None,
        outcome_group=group)


class PowerGateTests(unittest.TestCase):
    def test_the_live_gate_reads_outcomes_not_rows(self):
        # The live record holds threshold ladders: several rungs on one series in one
        # window, all resolved by one realised value. The gate counts those outcomes, so
        # the count must sit below the row count, and the verdict must follow the count.
        #
        # Whether the live corpus clears the floor is not asserted. It moves each time a
        # block resolves, and a test that fails on honest data invites tuning the data or
        # the threshold. Nor is it asserted that a fitted map beats the raw prices.
        rows = rec.of_record([rec.ScoredForecast(**r) for r in rec.build()["rows"]])
        power = rc.power_check(rows)
        self.assertEqual(power["n_rows"], len(rows))
        self.assertLess(power["n_outcome_groups"], power["n_rows"],
                        "threshold rungs on one realised value must count once")
        self.assertEqual(power["can_fit_map"],
                         power["n_outcome_groups"] >= rc.MIN_OUTCOMES_FOR_MAP
                         and power["n_bins_with_enough"] >= rc.MIN_BINS_WITH_ENOUGH)
        got = rc.fit(rows)
        self.assertEqual(isinstance(got, rc.CalibrationMap), power["can_fit_map"])
        if isinstance(got, rc.CalibrationMap):
            self.assertEqual(got.n_outcome_groups, power["n_outcome_groups"])
            self.assertAlmostEqual(got.raw_brier, sum(r.brier for r in rows) / len(rows),
                                   delta=1e-4)
        else:
            self.assertEqual(got.n_outcome_groups, power["n_outcome_groups"])
            self.assertEqual(got.n_needed, rc.MIN_OUTCOMES_FOR_MAP)

    def test_a_refusal_carries_no_map_to_misuse(self):
        got = rc.fit(_synthetic(20, 1, 0.5))
        self.assertIsInstance(got, rc.Refusal)
        for forbidden in ("knots", "apply", "transform", "coefficients"):
            self.assertFalse(hasattr(got, forbidden), forbidden)

    def test_the_gate_can_actually_pass(self):
        """A gate that cannot pass is not a gate, it is a wall."""
        self.assertIsInstance(rc.fit(_synthetic(300, 1, 0.5)), rc.CalibrationMap)

    def test_the_boundary_is_where_it_says_it_is(self):
        self.assertIsInstance(rc.fit(_synthetic(99, 3, 0.5)), rc.Refusal)
        self.assertIsInstance(rc.fit(_synthetic(100, 3, 0.5)), rc.CalibrationMap)

    def test_power_check_reports_the_detectable_effect(self):
        rows = rec.of_record([rec.ScoredForecast(**r) for r in rec.build()["rows"]])
        power = rc.power_check(rows)
        self.assertIn("minimum_detectable_miscalibration", power)
        self.assertGreater(power["widest_95_half_width"], 0)


class OutcomeGroupGateTests(unittest.TestCase):
    """Rungs priced on one realised value are one outcome, however many there are.

    Before this, the floor counted rows, a decile counted rows, and cross-validation
    held out one row while its sibling rungs stayed in training. A single realised
    value priced at 100 thresholds cleared every check.
    """

    def test_one_realised_value_priced_100_times_is_one_outcome(self):
        value = 0.42
        rungs = [_row(f"rung{i}", round(0.05 + 0.9 * i / 99, 4),
                      int(value <= 0.05 + 0.9 * i / 99), group="one-value")
                 for i in range(100)]
        power = rc.power_check(rungs)
        self.assertEqual((power["n_rows"], power["n_outcome_groups"]), (100, 1))
        self.assertFalse(power["can_fit_map"])
        got = rc.fit(rungs)
        self.assertIsInstance(got, rc.Refusal)
        self.assertEqual(got.n_outcome_groups, 1)

    def test_the_floor_counts_outcomes_not_rows(self):
        self.assertIsInstance(rc.fit(_grouped(99, 2, seed=5)), rc.Refusal,
                              "198 rows on 99 outcomes is below a floor of 100 outcomes")
        self.assertIsInstance(rc.fit(_grouped(100, 2, seed=5)), rc.CalibrationMap)

    def test_a_decile_counts_distinct_outcomes_not_rungs(self):
        """Seven ladders put 14 rungs into each of three deciles. That is seven
        outcomes per decile, not fourteen, and it does not fill a decile."""
        rng = random.Random(9)
        rows = [_row(f"single{i}", 0.55, int(rng.random() < 0.55), group=f"single{i}")
                for i in range(100)]
        for j in range(7):
            draw = rng.random()
            for p in (0.12, 0.17, 0.32, 0.37, 0.72, 0.77):
                rows.append(_row(f"ladder{j}-{p}", p, int(draw < p), group=f"ladder{j}"))
        power = rc.power_check(rows)
        self.assertEqual(power["n_outcome_groups"], 107)
        self.assertEqual(power["n_bins_with_enough"], 1)
        self.assertEqual(power["outcome_groups_per_bin"]["0.1"], 7)
        self.assertEqual(power["rows_per_bin"]["0.1"], 14)
        self.assertIsInstance(rc.fit(rows), rc.Refusal)

    def test_cross_validation_holds_out_every_rung_of_an_outcome(self):
        """Pricing every outcome twice adds no evidence, so it must not move the
        out-of-sample score. Leaving out one row at a time kept the twin in training
        and scored the held-out row against its own outcome."""
        once = _synthetic(300, 1, 0.5)
        twice = [dataclasses.replace(r, forecast_id=r.forecast_id + suffix,
                                     outcome_group=r.forecast_id)
                 for r in once for suffix in ("a", "b")]
        got_once, got_twice = rc.fit(once), rc.fit(twice)
        assert isinstance(got_once, rc.CalibrationMap)
        assert isinstance(got_twice, rc.CalibrationMap)
        self.assertEqual(got_twice.n_outcome_groups, 300)
        self.assertAlmostEqual(got_twice.raw_brier, got_once.raw_brier, places=4)
        self.assertAlmostEqual(got_twice.cross_validated_brier,
                               got_once.cross_validated_brier, places=4)

    def test_an_ungrouped_row_is_its_own_outcome(self):
        """Rows that name no group, including rows written before groups existed,
        count one each, which is how the gate read every row before."""
        row = _synthetic(1, 1, 1.0)[0]
        self.assertEqual(row.outcome_group, "")
        self.assertEqual(rc.outcome_group(row), row.forecast_id)
        legacy = {k: v for k, v in rec.asdict(row).items() if k != "outcome_group"}
        self.assertEqual(rec.ScoredForecast(**legacy).outcome_group, "")


class RecordOutcomeGroupTests(unittest.TestCase):
    """Every row of the pooled record names the realised outcome it resolves on."""

    @classmethod
    def setUpClass(cls):
        with _without_opt_in():
            cls.rows = [rec.ScoredForecast(**r) for r in rec.build()["rows"]]
        ledger = json.loads((REPO / "data" / "calibration-ledger.json").read_text())
        cls.target = {p["hypothesis_id"]: p["target"]
                      for b in ledger["blocks"] for p in b.get("points", [])}

    def _assert_partition(self, rows, key):
        """Rows share a group exactly when they share `key`, and the rule is not
        vacuous: at least one group holds more than one row."""
        group_of_key: dict = {}
        key_of_group: dict = {}
        for row in rows:
            self.assertEqual(group_of_key.setdefault(key(row), row.outcome_group),
                             row.outcome_group, row.forecast_id)
            self.assertEqual(key_of_group.setdefault(row.outcome_group, key(row)),
                             key(row), row.forecast_id)
        self.assertLess(len(key_of_group), len(rows))

    def test_every_row_names_its_outcome_group(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertTrue(row.outcome_group, row.forecast_id)

    def test_rungs_on_one_series_in_one_window_are_one_outcome(self):
        """Every pin on one metric in one window reads one realised path of that
        series, whichever block or shape it was registered in."""
        self._assert_partition(
            [r for r in self.rows if r.system == "operational"],
            lambda r: (r.metric, r.registered_at[:10], r.resolves_at[:10]))

    def test_corridors_into_one_target_in_one_window_are_one_outcome(self):
        """A corridor resolves on whether its target reports a confirmed case in the
        window, so every source pointing at that target shares the outcome."""
        corridor = [r for r in self.rows if r.system == "corridor"]
        self._assert_partition(
            corridor,
            lambda r: (self.target[r.forecast_id], r.registered_at[:10], r.resolves_at[:10]))

    def test_research_store_copies_of_corridor_questions_join_the_corridor_outcome(self):
        """The research store holds the first calibration block's four questions under
        its own ids and prices. They are the same four events, not four more."""
        by_id = {r.forecast_id: r for r in self.rows}
        corridor_group_outcome = {r.outcome_group: r.outcome
                                  for r in self.rows if r.system == "corridor"}
        self.assertEqual(len(rec.RESEARCH_STORE_CORRIDOR_COPIES), 4)
        for hypothesis_id, target in rec.RESEARCH_STORE_CORRIDOR_COPIES.items():
            copy = by_id[hypothesis_id]
            self.assertEqual(copy.system, "research_store")
            self.assertIn(target, copy.outcome_group)
            self.assertIn(copy.outcome_group, corridor_group_outcome,
                          "the copy must join an existing corridor outcome")
            self.assertEqual(copy.outcome, corridor_group_outcome[copy.outcome_group],
                             "one event cannot resolve two ways")


class ChallengerRowTests(unittest.TestCase):
    """A question priced by two methods counts once in the programme's record."""

    def _row(self, forecast_id, role, outcome=1):
        return rec.ScoredForecast(
            system="operational", block_id="b", forecast_id=forecast_id, probability=0.7,
            outcome=outcome, registered_at="2026-10-04", resolves_at="2026-11-01T23:59:59Z",
            horizon_days=31, method="level_bootstrap" if role != "challenger" else "levels_21d",
            metric="m", question_shape="ends_below", n_observations_at_pin=100,
            bias_test=False, geography_class=None, role=role)

    def test_challenger_rows_leave_the_headline_but_stay_in_method_slices(self):
        rows = [self._row("q1-inc", "incumbent"), self._row("q1-lev", "challenger"),
                self._row("q2-rec", "record", outcome=0)]
        out = rec.summary({"rows": [rec.asdict(r) for r in rows]})
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["challenger_rows"], 1)
        self.assertEqual(out["by_method"]["levels_21d"]["n"], 1)
        self.assertEqual([r.forecast_id for r in rec.of_record(rows)], ["q1-inc", "q2-rec"])

    def test_only_the_forecaster_of_record_enters_the_record(self):
        """A role nobody registered as the programme's forecast stays out, whatever it is
        called. Dropping only "challenger" let any new comparison role, such as a
        persistence or climatology baseline, count as the programme's own forecast."""
        roles = ("record", "incumbent", "challenger", "persistence", "climatology",
                 "ensemble", "candidate")
        rows = [self._row(f"q-{role}", role) for role in roles]
        self.assertEqual([r.role for r in rec.of_record(rows)], ["record", "incumbent"])
        self.assertEqual(rec.summary({"rows": [rec.asdict(r) for r in rows]})["n"], 2)

    def test_every_live_role_is_known(self):
        """A role the record does not name must be a decision, not a silent drop."""
        with _without_opt_in():
            seen = {r["role"] for r in rec.build()["rows"]}
        self.assertLessEqual(seen, set(rec.ROLES_OF_RECORD) | {"challenger"})

    def test_rows_written_before_roles_existed_default_to_the_record(self):
        row = self._row("old", "record")
        legacy = {k: v for k, v in rec.asdict(row).items() if k != "role"}
        self.assertEqual(rec.ScoredForecast(**legacy).role, "record")


class MapTests(unittest.TestCase):
    def test_recovers_a_real_mispricing(self):
        got = rc.fit(_synthetic(300, 1, 0.5))
        assert isinstance(got, rc.CalibrationMap)
        self.assertGreater(got.cv_improvement, 0.01,
                           "a badly mispriced forecaster should be repairable")
        self.assertGreater(got.apply(0.10), 0.10,
                           "the low end is under-priced here and should be lifted")

    def test_declines_to_credit_a_gain_that_is_not_there(self):
        """On an already-calibrated forecaster there is nothing to win, and the
        cross-validated score must say so even though the in-sample fit will
        always look like an improvement."""
        got = rc.fit(_synthetic(300, 2, 1.0))
        assert isinstance(got, rc.CalibrationMap)
        self.assertLess(got.cv_improvement, 0.01)

    def test_cross_validated_is_never_flattered_by_the_fit(self):
        got = rc.fit(_synthetic(300, 2, 1.0))
        assert isinstance(got, rc.CalibrationMap)
        self.assertGreaterEqual(got.cross_validated_brier, got.in_sample_brier - 1e-9,
                                "CV should not beat in-sample; if it does, the "
                                "leave-one-out is not actually holding anything out")

    def test_the_map_is_monotone(self):
        """Reordering forecasts would destroy the resolution term, which is the
        part that is working."""
        got = rc.fit(_synthetic(300, 1, 0.5))
        assert isinstance(got, rc.CalibrationMap)
        ys = [got.apply(x / 100) for x in range(0, 101)]
        self.assertEqual(ys, sorted(ys))


if __name__ == "__main__":
    unittest.main()
