# SPDX-License-Identifier: Apache-2.0
"""Tests for the PCR-modulator parallel-scoring pre-commitment (spec section 8.2).

Covers the scorer (`lovs.pcr_parallel_score`) and the release gate
(`lovs.pcr_parallel_scoring_precommit_gate`).
"""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

from lovs import pcr_parallel_score as score
from lovs import pcr_parallel_scoring_precommit_gate as gate


def _snapshot(modulated: dict[str, tuple[float, float]]) -> dict:
    """Minimal snapshot carrying a modulated-bands surface for the pre-commitment."""
    by_zone: dict[str, dict] = {
        "fallback_zone": {"lo": None, "hi": None},
    }
    for zone_id, (lo, hi) in modulated.items():
        by_zone[zone_id] = {"lo": lo, "hi": hi}
    return {
        "outbreak_id": "bdbv-uga-cod-2026",
        "as_of": "2026-05-28T23:59:59Z",
        "data_as_of": "2026-05-26",
        "resolves_at": "2026-06-19T23:59:59Z",
        "per_zone_under_ascertainment_bands": {
            "method_basis": "africa_cdc_pcr_capacity_modulated_v1",
            "surface_role": "shadow_in_v1",
            "species_default_band": {"lo": 0.3, "hi": 0.9},
            "by_lovs_zone": by_zone,
        },
    }


class TestBuildPrecommit(unittest.TestCase):
    def test_in_scope_is_modulated_zones_only(self):
        artifact = score.build_precommit(_snapshot({"z1": (0.6, 0.9), "z2": (0.5, 0.9)}))
        self.assertEqual(["z1", "z2"], artifact["in_scope_zones"])

    def test_e0_is_species_default_e1_is_modulated(self):
        artifact = score.build_precommit(_snapshot({"z1": (0.6, 0.9)}))
        e0 = artifact["estimators"]["E0_species_default"]["band_by_zone"]["z1"]
        e1 = artifact["estimators"]["E1_pcr_modulated"]["band_by_zone"]["z1"]
        self.assertEqual({"lo": 0.3, "hi": 0.9}, e0)
        self.assertEqual({"lo": 0.6, "hi": 0.9}, e1)

    def test_content_hash_matches_canonical_recompute(self):
        artifact = score.build_precommit(_snapshot({"z1": (0.6, 0.9)}))
        self.assertEqual(score._canonical_hash(artifact), artifact["content_hash"])

    def test_no_modulated_zones_raises(self):
        with self.assertRaises(score.PCRParallelScoreError):
            score.build_precommit(_snapshot({}))

    def test_resolution_checkpoint_from_snapshot(self):
        artifact = score.build_precommit(_snapshot({"z1": (0.6, 0.9)}))
        self.assertEqual("2026-06-19", artifact["resolution_checkpoint"])
        self.assertEqual("shadow_in_v1", artifact["scored_surface_role_at_pin"])


class TestScoreEstimator(unittest.TestCase):
    def test_interval_score_inside_band_is_width(self):
        # alpha=0.5; outcome inside -> score == (hi - lo)
        out = score.score_estimator({"z1": {"lo": 0.5, "hi": 0.9}}, {"z1": 0.7})
        self.assertAlmostEqual(0.4, out["per_zone_interval_score"]["z1"])
        self.assertAlmostEqual(0.4, out["mean_interval_score"])
        self.assertEqual(1, out["n_scored"])

    def test_interval_score_below_band_penalised(self):
        # outcome 0.4 < lo 0.5: 0.4 width + (2/0.5)*(0.5-0.4) = 0.4 + 0.4 = 0.8
        out = score.score_estimator({"z1": {"lo": 0.5, "hi": 0.9}}, {"z1": 0.4})
        self.assertAlmostEqual(0.8, out["per_zone_interval_score"]["z1"])

    def test_none_and_missing_empirical_are_skipped(self):
        out = score.score_estimator(
            {"z1": {"lo": 0.3, "hi": 0.9}, "z2": {"lo": 0.5, "hi": 0.9}},
            {"z1": None},
        )
        self.assertEqual(0, out["n_scored"])
        self.assertIsNone(out["mean_interval_score"])

    def test_narrower_correct_band_beats_wider(self):
        # E1 narrow band (0.6,0.9) and E0 wide (0.3,0.9), outcome 0.7 inside both.
        empirical = {"z1": 0.7}
        e0 = score.score_estimator({"z1": {"lo": 0.3, "hi": 0.9}}, empirical)
        e1 = score.score_estimator({"z1": {"lo": 0.6, "hi": 0.9}}, empirical)
        self.assertLess(e1["mean_interval_score"], e0["mean_interval_score"])


class TestDecidePromotion(unittest.TestCase):
    def test_e1_better_and_replicated_promotes(self):
        decision = score.decide_promotion(0.6, 0.3, cycles_passed=2)
        self.assertTrue(decision["promote"])
        self.assertAlmostEqual(0.5, decision["relative_improvement"])

    def test_e1_better_but_not_replicated_holds(self):
        decision = score.decide_promotion(0.6, 0.3, cycles_passed=1)
        self.assertFalse(decision["promote"])
        self.assertTrue(decision["cycle_pass"])

    def test_e1_marginally_better_below_margin_holds(self):
        # 5% improvement < 10% margin
        decision = score.decide_promotion(1.00, 0.95, cycles_passed=2)
        self.assertFalse(decision["promote"])
        self.assertFalse(decision["cycle_pass"])

    def test_e1_worse_holds(self):
        decision = score.decide_promotion(0.3, 0.6, cycles_passed=2)
        self.assertFalse(decision["promote"])
        self.assertLess(decision["relative_improvement"], 0)

    def test_insufficient_data_holds(self):
        decision = score.decide_promotion(None, 0.3, cycles_passed=2)
        self.assertFalse(decision["promote"])


class TestPrecommitGate(unittest.TestCase):
    def _write_pair(self, td: str, *, snapshot: dict, artifact: dict):
        snap_path = pathlib.Path(td) / "snap.json"
        pre_path = pathlib.Path(td) / "precommit.json"
        snap_path.write_text(json.dumps(snapshot), encoding="utf-8")
        pre_path.write_text(json.dumps(artifact), encoding="utf-8")
        return pre_path, snap_path

    def test_well_formed_passes(self):
        snap = _snapshot({"z1": (0.6, 0.9), "z2": (0.5, 0.9)})
        artifact = score.build_precommit(snap)
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            self.assertEqual(
                [], gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            )

    def test_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as td:
            missing = pathlib.Path(td) / "nope.json"
            problems = gate.check_pcr_parallel_scoring_precommit(missing, missing)
            self.assertTrue(problems and "missing" in problems[0])

    def test_no_pcr_surface_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            snap_path = pathlib.Path(td) / "snap.json"
            missing = pathlib.Path(td) / "nope.json"
            snap_path.write_text(json.dumps({"as_of": "2026-06-11"}), encoding="utf-8")
            self.assertEqual(
                [], gate.check_pcr_parallel_scoring_precommit(missing, snap_path)
            )

    def test_tampered_hash_fails(self):
        snap = _snapshot({"z1": (0.6, 0.9)})
        artifact = score.build_precommit(snap)
        artifact["promotion_bar"]["relative_margin"] = 0.0  # edit after pinning
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            problems = gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            self.assertTrue(any("content_hash" in p for p in problems))

    def test_resolution_before_snapshot_fails(self):
        snap = _snapshot({"z1": (0.6, 0.9)})
        artifact = score.build_precommit(snap)
        artifact["resolution_checkpoint"] = "2026-06-01"  # before snapshot resolves_at
        artifact["content_hash"] = score._canonical_hash(artifact)
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            problems = gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            self.assertTrue(any("precedes" in p for p in problems))

    def test_e1_band_mismatch_fails(self):
        snap = _snapshot({"z1": (0.6, 0.9)})
        artifact = score.build_precommit(snap)
        # Drift the candidate band away from the snapshot, re-hash so only the
        # snapshot cross-check (not the hash check) trips.
        artifact["estimators"]["E1_pcr_modulated"]["band_by_zone"]["z1"] = {
            "lo": 0.4,
            "hi": 0.9,
        }
        artifact["content_hash"] = score._canonical_hash(artifact)
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            problems = gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            self.assertTrue(any("E1 band for z1" in p for p in problems))

    def test_non_shadow_surface_fails(self):
        snap = _snapshot({"z1": (0.6, 0.9)})
        artifact = score.build_precommit(snap)
        artifact["scored_surface_role_at_pin"] = "primary"
        artifact["content_hash"] = score._canonical_hash(artifact)
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            problems = gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            self.assertTrue(any("shadow_in_v1" in p for p in problems))

    def test_missing_required_field_fails(self):
        snap = _snapshot({"z1": (0.6, 0.9)})
        artifact = score.build_precommit(snap)
        del artifact["promotion_bar"]
        with tempfile.TemporaryDirectory() as td:
            pre_path, snap_path = self._write_pair(td, snapshot=snap, artifact=artifact)
            problems = gate.check_pcr_parallel_scoring_precommit(pre_path, snap_path)
            self.assertTrue(any("promotion_bar" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
