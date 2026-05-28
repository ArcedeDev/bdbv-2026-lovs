# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.pcr_modulator_shadow_gate (spec §7.2, Rec J)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest

from lovs import pcr_modulator_shadow_gate as gate


def _good_bands() -> dict:
    return {
        "method_basis": "africa_cdc_pcr_capacity_modulated_v1",
        "surface_role": "shadow_in_v1",
        "species_default_band": {"lo": 0.3, "hi": 0.9},
        "by_lovs_zone": {},
        "coverage_stats": {
            "modulated_zones": 0,
            "species_default_fallback_zones": 0,
            "total_zones": 0,
        },
    }


def _write(d: dict, tmp_dir: pathlib.Path) -> pathlib.Path:
    p = tmp_dir / "snap.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


class TestPCRModulatorShadowGate(unittest.TestCase):
    def test_no_bands_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write({}, pathlib.Path(td))
            self.assertEqual([], gate.check_pcr_modulator_shadow(path))

    def test_shadow_in_v1_passes(self):
        snap = {"per_zone_under_ascertainment_bands": _good_bands()}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            self.assertEqual([], gate.check_pcr_modulator_shadow(path))

    def test_primary_is_refused(self):
        bands = _good_bands()
        bands["surface_role"] = "primary"
        snap = {"per_zone_under_ascertainment_bands": bands}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_pcr_modulator_shadow(path)
            self.assertTrue(any("shadow_in_v1" in p for p in problems))

    def test_corroborating_is_refused(self):
        bands = _good_bands()
        bands["surface_role"] = "corroborating"
        snap = {"per_zone_under_ascertainment_bands": bands}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_pcr_modulator_shadow(path)
            self.assertTrue(any("shadow_in_v1" in p for p in problems))

    def test_wrong_method_basis_is_refused(self):
        bands = _good_bands()
        bands["method_basis"] = "wrong_basis_v1"
        snap = {"per_zone_under_ascertainment_bands": bands}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_pcr_modulator_shadow(path)
            self.assertTrue(any("method_basis" in p for p in problems))

    def test_runtime_under_250ms(self):
        snap = {"per_zone_under_ascertainment_bands": _good_bands()}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            start = time.monotonic()
            gate.check_pcr_modulator_shadow(path)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
