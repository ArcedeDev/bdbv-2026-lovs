# SPDX-License-Identifier: Apache-2.0
"""Tests for the generated snapshot contract gate."""
from __future__ import annotations

import copy
import json
import pathlib
import unittest

from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestSnapshotContract(unittest.TestCase):
    def _snapshot(self) -> dict:
        return json.loads(
            (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
        )

    def test_contract_captures_current_may22_partition(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        self.assertEqual(84, contract["confirmed_case_partition"]["headline_confirmed_total"])
        self.assertEqual(33, contract["confirmed_case_partition"]["zone_attributed_confirmed_total"])
        self.assertEqual(51, contract["confirmed_case_partition"]["unallocated_confirmed_total"])
        self.assertEqual(7, contract["corridor_watchlist"]["source_zone_count"])
        self.assertEqual(42, contract["corridor_watchlist"]["corridor_count"])
        self.assertEqual([0.4, 8.9], contract["corridor_watchlist"]["adjusted_50_lower_range_pct"])
        self.assertEqual([1.3, 23.9], contract["corridor_watchlist"]["adjusted_50_upper_range_pct"])
        self.assertEqual(
            "descriptive_watchlist_not_forecast",
            contract["method_status"]["corridor_interpretation"],
        )
        self.assertIn("do not scale", contract["method_status"]["source_load_policy"])
        self.assertIn("source-attribution lag", contract["method_status"]["source_load_policy"])
        self.assertEqual(0, contract["visibility_method"]["history_snapshot_count"])
        self.assertIn("single_snapshot_prior_proxy", contract["visibility_method"]["method_basis"])

    def test_snapshot_contract_rejects_aggregate_smearing(self):
        snapshot = self._snapshot()
        smeared = copy.deepcopy(snapshot)
        for corridor in smeared["corridors"]:
            corridor["drivers"] = ["headline confirmed count 84 applied to this source zone"]

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(smeared)

    def test_snapshot_contract_rejects_stale_narrative(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        stale = (
            "The current 42-corridor watchlist spans 64.7% to 69.5% upper bounds "
            "and applies the 84 confirmed cases to every source zone."
        )

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_narrative(stale, contract, "fixture")

    def test_snapshot_contract_rejects_corridor_overclaim(self):
        contract = snapshot_contract.build_contract(self._snapshot())
        overclaim = (
            "The current 42-corridor watchlist spans 0.4-8.9% lower and "
            "1.3-23.9% upper bounds using 84 confirmed cases, 33 confirmed "
            "cases, 51 confirmed cases, officially zone-attributed, "
            "source-attribution lag, "
            "unallocated, and 7 WHO AFRO source zones. This is a corridor "
            "deployment ranking."
        )

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_narrative(overclaim, contract, "fixture")

    def test_snapshot_contract_rejects_undisclosed_single_snapshot_visibility(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["visibility"]["history_snapshot_count"] = 0
        snapshot["visibility"]["method_basis"] = "empirical_history"
        snapshot["visibility"]["method_caveat"] = "field-observed daily cadence"

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
