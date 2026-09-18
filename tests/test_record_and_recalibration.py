"""Tests for the pooled forecast record and the recalibration engine.

The two properties that matter here are both about refusing to fool ourselves:
a map must not be fitted on a corpus too small to support one, and a fitted map
must be scored out of sample. An in-sample Brier on a flexible monotone fit is
always flattering and always meaningless.
"""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from lovs.forecast import recalibration as rc
from lovs.forecast import record as rec

REPO = Path(__file__).resolve().parent.parent


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

    @unittest.skipUnless(
        rec.IDB_TRACK_B.exists(),
        "the IDB Track B record lives in the private idb-validation sibling repo, absent in "
        "public CI; with one source there is no overlap to catch. Absence itself is covered "
        "by test_dedup_survives_a_source_being_absent.")
    def test_the_overlap_is_actually_caught(self):
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


class PowerGateTests(unittest.TestCase):
    def test_the_live_corpus_is_refused(self):
        rows = [rec.ScoredForecast(**r) for r in rec.build()["rows"]]
        got = rc.fit(rows)
        self.assertIsInstance(got, rc.Refusal)

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
        rows = [rec.ScoredForecast(**r) for r in rec.build()["rows"]]
        power = rc.power_check(rows)
        self.assertIn("minimum_detectable_miscalibration", power)
        self.assertGreater(power["widest_95_half_width"], 0)


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
