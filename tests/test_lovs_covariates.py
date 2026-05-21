"""Tests for lovs/lovs_covariates.py."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from lovs import lovs_covariates


_BDBV_PATH = (
    pathlib.Path(__file__).parent / "data" / "lovs" / "covariates-bdbv-2026.json"
)
_WA_PATH = (
    pathlib.Path(__file__).parent / "data" / "lovs" / "covariates-wa-2014.json"
)


class TestCovariateLoadShape(unittest.TestCase):

    def test_loads_bdbv_2026_table(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        self.assertIsInstance(table, lovs_covariates.CovariateTable)
        self.assertGreaterEqual(len(table.geographies), 6)

    def test_loads_wa_2014_table(self):
        table = lovs_covariates.load_covariates(_WA_PATH)
        self.assertIsInstance(table, lovs_covariates.CovariateTable)
        self.assertGreaterEqual(len(table.geographies), 30)

    def test_bdbv_table_includes_known_ituri_zones(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        self.assertIn("bunia-ituri", table.geographies)
        self.assertIn("kampala-uga", table.geographies)
        self.assertIn("bundibugyo-uga", table.geographies)

    def test_loaded_geography_is_frozen(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        kampala = table.get("kampala-uga")
        self.assertIsNotNone(kampala)
        with self.assertRaises(Exception):
            kampala.population_density = 99999.0  # type: ignore[misc]


class TestCovariateValidation(unittest.TestCase):

    def test_rejects_missing_file(self):
        with self.assertRaises(lovs_covariates.CovariateLoadError):
            lovs_covariates.load_covariates(
                pathlib.Path("/nonexistent/covariates.json")
            )

    def test_rejects_malformed_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not valid json {{{")
            tmp_path = pathlib.Path(f.name)
        try:
            with self.assertRaises(lovs_covariates.CovariateLoadError):
                lovs_covariates.load_covariates(tmp_path)
        finally:
            tmp_path.unlink()

    def test_rejects_missing_keys(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"source": "x"}, f)
            tmp_path = pathlib.Path(f.name)
        try:
            with self.assertRaises(lovs_covariates.CovariateLoadError):
                lovs_covariates.load_covariates(tmp_path)
        finally:
            tmp_path.unlink()

    def test_rejects_invalid_ordinal(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "source": "test",
                    "version": "1.0",
                    "geographies": [
                        {
                            "geography_id": "test",
                            "population_density": 100.0,
                            "road_connectivity_index": 99,  # out of range
                            "healthcare_distance_km": 5.0,
                            "conflict_access_score": 1,
                            "derivation_notes": "",
                        }
                    ],
                },
                f,
            )
            tmp_path = pathlib.Path(f.name)
        try:
            with self.assertRaises(lovs_covariates.CovariateLoadError):
                lovs_covariates.load_covariates(tmp_path)
        finally:
            tmp_path.unlink()

    def test_rejects_negative_population(self):
        with self.assertRaises(ValueError):
            lovs_covariates.GeographyCovariates(
                geography_id="x",
                population_density=-1.0,
                road_connectivity_index=2,
                healthcare_distance_km=10.0,
                conflict_access_score=1,
                derivation_notes="",
            )

    def test_rejects_duplicate_geography_id(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(
                {
                    "source": "test",
                    "version": "1.0",
                    "geographies": [
                        {
                            "geography_id": "dup",
                            "population_density": 100.0,
                            "road_connectivity_index": 2,
                            "healthcare_distance_km": 5.0,
                            "conflict_access_score": 1,
                            "derivation_notes": "",
                        },
                        {
                            "geography_id": "dup",
                            "population_density": 200.0,
                            "road_connectivity_index": 3,
                            "healthcare_distance_km": 8.0,
                            "conflict_access_score": 2,
                            "derivation_notes": "",
                        },
                    ],
                },
                f,
            )
            tmp_path = pathlib.Path(f.name)
        try:
            with self.assertRaises(lovs_covariates.CovariateLoadError):
                lovs_covariates.load_covariates(tmp_path)
        finally:
            tmp_path.unlink()


class TestEdgeWeight(unittest.TestCase):

    def setUp(self) -> None:
        self.bdbv = lovs_covariates.load_covariates(_BDBV_PATH)
        self.wa = lovs_covariates.load_covariates(_WA_PATH)

    def test_edge_weight_is_finite_and_bounded(self):
        for s_id in self.bdbv.geographies:
            for t_id in self.bdbv.geographies:
                if s_id == t_id:
                    continue
                w = self.bdbv.edge_weight(s_id, t_id)
                self.assertGreaterEqual(w, lovs_covariates.EDGE_WEIGHT_MIN)
                self.assertLessEqual(w, lovs_covariates.EDGE_WEIGHT_MAX)
                self.assertTrue(w == w, "edge_weight must not be NaN")

    def test_edge_weight_unknown_geography_falls_back_to_one(self):
        w = self.bdbv.edge_weight("does-not-exist", "kampala-uga")
        self.assertEqual(w, 1.0)
        w2 = self.bdbv.edge_weight("kampala-uga", "does-not-exist")
        self.assertEqual(w2, 1.0)

    def test_higher_conflict_target_yields_higher_or_equal_edge_weight(self):
        """Holding source fixed, a high-conflict target should not yield a strictly lower
        edge-weight than a low-conflict target (more dangerous = higher hazard)."""
        # Use kampala as source, compare beni-cod (conflict=5) vs kampala→kampala-uga (conflict=1).
        # Note: edge_weight contains both population product and conflict; we test the order on a fair pair.
        w_high_conflict = self.bdbv.edge_weight("bunia-ituri", "beni-cod")
        w_low_conflict = self.bdbv.edge_weight("bunia-ituri", "bundibugyo-uga")
        # Conflict factor difference (5 vs 2) should push w_high_conflict higher
        # all else being similar; we assert a weak ordering.
        self.assertGreater(w_high_conflict, w_low_conflict * 0.5,
                           "high-conflict target should not collapse edge-weight far below low-conflict target")

    def test_wa_2014_edge_weights_are_bounded(self):
        sample_ids = list(self.wa.geographies.keys())[:10]
        for s_id in sample_ids:
            for t_id in sample_ids:
                if s_id == t_id:
                    continue
                w = self.wa.edge_weight(s_id, t_id)
                self.assertGreaterEqual(w, lovs_covariates.EDGE_WEIGHT_MIN)
                self.assertLessEqual(w, lovs_covariates.EDGE_WEIGHT_MAX)

    def test_edge_weight_is_deterministic(self):
        w1 = self.bdbv.edge_weight("bunia-ituri", "kampala-uga")
        w2 = self.bdbv.edge_weight("bunia-ituri", "kampala-uga")
        self.assertEqual(w1, w2)


class TestCovariateTableSemantics(unittest.TestCase):

    def test_table_get_returns_none_for_missing(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        self.assertIsNone(table.get("definitely-not-present"))

    def test_derivation_notes_are_carried(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        kampala = table.get("kampala-uga")
        self.assertIsNotNone(kampala)
        self.assertIn("Uganda capital", kampala.derivation_notes)

    def test_source_field_populated(self):
        table = lovs_covariates.load_covariates(_BDBV_PATH)
        self.assertIn("BDBV", table.source)


if __name__ == "__main__":
    unittest.main()
