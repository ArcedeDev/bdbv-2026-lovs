# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.pcr_modulator_shadow_gate (spec §7.2, Rec J)."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

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
    def test_no_bands_is_refused(self):
        """A VANISHED surface must fail, not pass silently.

        This inverts the gate's original `test_no_bands_is_silent` contract, which
        only asked "if present, is it still shadow?" and so returned green when the
        surface was missing entirely. That is exactly how the diagnostic-access
        surface disappeared for 34 cycles (2026-06-11 -> 2026-07-14) with every gate
        green: the epi bundle stopped reconciling, the bands were dropped on the
        fallback path, and the website carry-forward kept rendering a ring. The bands
        are now built from the vendored static capacity table over the reviewed
        SitRep zone set, so they no longer depend on that bundle and absence is a
        real defect rather than an expected degradation.
        """
        with tempfile.TemporaryDirectory() as td:
            path = _write({}, pathlib.Path(td))
            problems = gate.check_pcr_modulator_shadow(path)
            self.assertTrue(
                any("is absent from the snapshot" in p for p in problems),
                f"expected a presence failure, got {problems!r}",
            )

    def test_no_bands_is_silent_when_explicitly_acknowledged(self):
        """The escape hatch stays available, but must be an explicit, visible act."""
        with mock.patch.dict(
            os.environ, {"BDBV_ALLOW_MISSING_PCR_BANDS": "1"}, clear=False
        ):
            with tempfile.TemporaryDirectory() as td:
                path = _write({}, pathlib.Path(td))
                self.assertEqual([], gate.check_pcr_modulator_shadow(path))

    def test_presence_failure_is_not_triggered_by_an_unset_flag(self):
        """An empty/absent flag must NOT be read as acknowledgement."""
        for value in ("", "0", "no", "false"):
            with mock.patch.dict(
                os.environ, {"BDBV_ALLOW_MISSING_PCR_BANDS": value}, clear=False
            ):
                with tempfile.TemporaryDirectory() as td:
                    path = _write({}, pathlib.Path(td))
                    self.assertTrue(
                        gate.check_pcr_modulator_shadow(path),
                        f"flag {value!r} must not silence the presence check",
                    )

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
