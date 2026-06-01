# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.attribution_lag_disclosure_gate (spec §7.2)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import time
import unittest

from lovs import attribution_lag_disclosure_gate as gate


def _insp_block_with_deaths() -> dict:
    return {
        "as_of_data_date": "2026-05-26",
        "source_id": "inrb-umie-ebola-drc-2026-build-2026-05-27-e40bc9e",
        "method_basis": "INRB_UMIE_INSP_per_zone_v1",
        "by_lovs_zone": {
            "bunia": {
                "confirmed": 36,
                "suspected": 279,
                "confirmed_deaths": 2,
                "suspected_deaths": 18,
                "inrb_collapsed_from": [],
                "present_in_insp_classification": "present_with_data",
            },
        },
        "national_at_data_date": {
            "confirmed": 36,
            "suspected": 279,
            "confirmed_deaths": 2,
            "suspected_deaths": 18,
        },
        "unallocated_residual": {
            "confirmed": 0,
            "suspected": 0,
            "confirmed_deaths": 0,
            "suspected_deaths": 0,
        },
        "coverage_audit": {
            "present_with_data": ["bunia"],
            "present_but_zero": [],
            "structurally_absent": [],
        },
    }


def _good_lag() -> dict:
    return {
        "per_metric": [
            {"metric": "confirmed", "timeliness": "near_timely", "share_attributed_to_zones": 0.92},
            {"metric": "suspected", "timeliness": "timely", "share_attributed_to_zones": 0.99},
            {"metric": "confirmed_deaths", "timeliness": "trailing", "share_attributed_to_zones": 0.29},
            {"metric": "suspected_deaths", "timeliness": "timely", "share_attributed_to_zones": 1.0},
        ],
        "narrative": "Confirmed deaths trail by 1-3 weeks while the INRB clinical review queue catches up.",
    }


def _write(d: dict, tmp_dir: pathlib.Path) -> pathlib.Path:
    p = tmp_dir / "snap.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


class TestAttributionLagDisclosureGate(unittest.TestCase):
    def test_no_insp_block_is_silent(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write({}, pathlib.Path(td))
            self.assertEqual([], gate.check_attribution_lag_disclosure(path))

    def test_per_zone_deaths_require_disclosure(self):
        snap = {
            "insp_per_zone_block": _insp_block_with_deaths(),
        }
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_attribution_lag_disclosure(path)
            self.assertTrue(
                any("attribution_lag_disclosure must be" in p for p in problems)
            )

    def test_disclosure_present_passes(self):
        snap = {
            "insp_per_zone_block": _insp_block_with_deaths(),
            "attribution_lag_disclosure": _good_lag(),
        }
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            self.assertEqual([], gate.check_attribution_lag_disclosure(path))

    def test_missing_confirmed_deaths_metric_caught(self):
        lag = _good_lag()
        lag["per_metric"] = [
            row for row in lag["per_metric"] if row["metric"] != "confirmed_deaths"
        ]
        snap = {
            "insp_per_zone_block": _insp_block_with_deaths(),
            "attribution_lag_disclosure": lag,
        }
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_attribution_lag_disclosure(path)
            self.assertTrue(
                any("does not include confirmed_deaths" in p for p in problems)
            )

    def test_unallocated_deaths_residual_alone_triggers_requirement(self):
        block = _insp_block_with_deaths()
        # No per-zone deaths but a non-zero residual still triggers the
        # disclosure requirement.
        for row in block["by_lovs_zone"].values():
            row["confirmed_deaths"] = 0
        block["national_at_data_date"]["confirmed_deaths"] = 2
        block["unallocated_residual"]["confirmed_deaths"] = 2
        snap = {"insp_per_zone_block": block}
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            problems = gate.check_attribution_lag_disclosure(path)
            self.assertTrue(
                any("attribution_lag_disclosure must be" in p for p in problems)
            )

    def test_runtime_under_250ms(self):
        snap = {
            "insp_per_zone_block": _insp_block_with_deaths(),
            "attribution_lag_disclosure": _good_lag(),
        }
        with tempfile.TemporaryDirectory() as td:
            path = _write(snap, pathlib.Path(td))
            start = time.monotonic()
            gate.check_attribution_lag_disclosure(path)
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.25)


if __name__ == "__main__":
    unittest.main()
