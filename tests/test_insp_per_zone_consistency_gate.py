# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.insp_per_zone_consistency_gate (spec §7.2)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest

from lovs import insp_per_zone_consistency_gate as gate


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _good_snapshot() -> dict:
    base = json.loads(
        (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
    )
    base["data_scale_used"] = "per_zone"
    base["insp_per_zone_block"] = {
        "as_of_data_date": "2026-05-26",
        "source_id": "inrb-umie-ebola-drc-2026-build-2026-05-27-e40bc9e",
        "method_basis": "INRB_UMIE_INSP_per_zone_v1",
        "by_lovs_zone": {
            "bunia": {
                "confirmed": 36,
                "confirmed_deaths": 2,
                "inrb_collapsed_from": [],
                "present_in_insp_classification": "present_with_data",
            },
        },
        "national_at_data_date": {
            "confirmed": 36,
            "confirmed_deaths": 2,
        },
        "unallocated_residual": {
            "confirmed": 0,
            "confirmed_deaths": 0,
        },
        "coverage_audit": {
            "present_with_data": ["bunia"],
            "present_but_zero": [],
            "structurally_absent": [],
        },
    }
    return base


def _write_snapshot(d: dict, tmp_dir: pathlib.Path) -> pathlib.Path:
    p = tmp_dir / "live.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


class TestInspPerZoneConsistencyGate(unittest.TestCase):
    def test_clean_snapshot_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(_good_snapshot(), pathlib.Path(td))
            self.assertEqual([], gate.check_insp_per_zone_consistency(path))

    def test_missing_data_scale_used_with_block_fails(self):
        snap = _good_snapshot()
        snap.pop("data_scale_used", None)
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            problems = gate.check_insp_per_zone_consistency(path)
            self.assertTrue(any("no data_scale_used" in p for p in problems))

    def test_per_zone_scale_without_block_fails(self):
        snap = _good_snapshot()
        snap.pop("insp_per_zone_block", None)
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            problems = gate.check_insp_per_zone_consistency(path)
            self.assertTrue(any("requires an insp_per_zone_block" in p for p in problems))

    def test_national_scale_without_block_passes(self):
        snap = _good_snapshot()
        snap["data_scale_used"] = "national"
        snap.pop("insp_per_zone_block", None)
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            self.assertEqual([], gate.check_insp_per_zone_consistency(path))

    def test_reconciliation_violation_caught(self):
        snap = _good_snapshot()
        snap["insp_per_zone_block"]["national_at_data_date"]["confirmed"] = 999
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            problems = gate.check_insp_per_zone_consistency(path)
            self.assertTrue(any("reconciliation violated" in p for p in problems))

    def test_non_inrb_umie_source_id_caught(self):
        snap = _good_snapshot()
        snap["insp_per_zone_block"]["source_id"] = "some-other-source"
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            problems = gate.check_insp_per_zone_consistency(path)
            self.assertTrue(any("INRB-UMIE" in p for p in problems))

    def test_mixed_with_metric_floor_requires_classification(self):
        snap = _good_snapshot()
        snap["data_scale_used"] = "mixed_with_metric_floor"
        snap["insp_per_zone_block"]["by_lovs_zone"]["bunia"].pop(
            "present_in_insp_classification"
        )
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(snap, pathlib.Path(td))
            problems = gate.check_insp_per_zone_consistency(path)
            self.assertTrue(
                any("present_in_insp_classification" in p for p in problems)
            )

    def test_runtime_under_250ms(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_snapshot(_good_snapshot(), pathlib.Path(td))
            start = time.monotonic()
            gate.check_insp_per_zone_consistency(path)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
