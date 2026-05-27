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

    def test_contract_captures_current_may26_partition(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        self.assertEqual(128, contract["confirmed_case_partition"]["headline_confirmed_total"])
        self.assertEqual(79, contract["confirmed_case_partition"]["zone_attributed_confirmed_total"])
        # Headline 128 - zone-attributed 79 = 49 unallocated (up from 33 on the
        # May-25 cycle: 16 new DRC MoH cases not yet zone-attributed by SitRep).
        self.assertEqual(49, contract["confirmed_case_partition"]["unallocated_confirmed_total"])
        self.assertEqual(11, contract["corridor_watchlist"]["source_zone_count"])
        # 11 source zones x 7 target zones = 77 corridors, minus 1 self-edge
        # (goma-cod is both a confirmed source zone in the SitRep007 cumulative
        # table and a candidate target after PR #20 promotion). Matches the
        # snapshot_contract.py self-edge corridor count exclusion and the
        # preflight self-edge doctrine landed on this branch.
        self.assertEqual(76, contract["corridor_watchlist"]["corridor_count"])
        self.assertEqual([0.7, 20.4], contract["corridor_watchlist"]["adjusted_50_lower_range_pct"])
        self.assertEqual([1.8, 49.3], contract["corridor_watchlist"]["adjusted_50_upper_range_pct"])
        self.assertEqual(
            "descriptive_watchlist_not_forecast",
            contract["method_status"]["corridor_interpretation"],
        )
        self.assertIn("do not scale", contract["method_status"]["source_load_policy"])
        self.assertIn("source-attribution lag", contract["method_status"]["source_load_policy"])
        self.assertEqual(0, contract["visibility_method"]["history_snapshot_count"])
        self.assertIn("single_snapshot", contract["visibility_method"]["method_basis"])
        self.assertIn("proxy", contract["visibility_method"]["method_basis"])
        self.assertEqual(
            "Rosello 2015 BDBV Isiro onset-to-notification",
            contract["visibility_method"]["delay_prior"]["label"],
        )
        self.assertEqual(
            [1.1345, 0.1285],
            contract["visibility_method"]["delay_prior"]["gamma_shape_rate"],
        )
        self.assertEqual(
            "ec:lovs:grepi:reporting-delay-update:2026-05-23",
            contract["visibility_method"]["delay_prior"]["evidence_chain_id"],
        )
        self.assertEqual(
            ["Camacho 2015 EBOV-Zaire onset-to-notification sensitivity"],
            [
                prior["label"]
                for prior in contract["visibility_method"]["sensitivity_delay_priors"]
            ],
        )

    def test_snapshot_contract_rejects_aggregate_smearing(self):
        snapshot = self._snapshot()
        smeared = copy.deepcopy(snapshot)
        for corridor in smeared["corridors"]:
            corridor["drivers"] = ["headline confirmed count 88 applied to this source zone"]

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(smeared)

    def test_snapshot_contract_allows_target_source_overlap_without_self_edge(self):
        snapshot_contract.validate_snapshot(self._snapshot())

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
            "The current 66-corridor watchlist spans 0.6-20.8% lower and "
            "1.8-47.6% upper bounds using 84 confirmed cases, 79 confirmed "
            "cases, 5 confirmed cases, officially zone-attributed, "
            "source-attribution lag, "
            "unallocated, and 11 DRC MoH source zones. This is a corridor "
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

    def test_snapshot_contract_rejects_stale_camacho_default_for_bdbv_specific_run(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["visibility"]["delay_prior"] = {
            "label": "Camacho 2015 EBOV-Zaire onset-to-notification sensitivity",
            "gamma_shape_rate": [0.81, 0.18],
            "evidence_chain_id": "ec:lovs:module-c:reporting-delay-priors:2026-05-20",
        }
        snapshot["visibility"]["sensitivity_delay_priors"] = []

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(snapshot)

    def test_narrative_rejects_stale_reporting_delay_attribution(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        rosello_default = (
            "Reporting completeness 50% range [39.7%, 45.8%]. The inherent reporting "
            "delay (Rosello 2015 eLife BDBV Isiro 2012 onset-to-notification default, "
            "with Camacho 2015 retained as a faster-reporting sensitivity comparator)."
        )
        snapshot_contract.validate_visibility_prior_attribution(rosello_default, contract, "ok")

        camacho_as_default = (
            "Reporting completeness 50% range [39.7%, 45.8%]. The inherent reporting "
            "delay (Camacho 2015 PLOS Currents, an Ebola-Zaire onset-to-notification "
            "delay applied as a Bundibugyo proxy)."
        )
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_visibility_prior_attribution(
                camacho_as_default, contract, "stale"
            )

        stale_2014_delay = (
            "The reporting-completeness nowcast assumes a delay distribution drawn "
            "from 2014 West Africa surveillance."
        )
        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_visibility_prior_attribution(
                stale_2014_delay, contract, "stale"
            )


if __name__ == "__main__":
    unittest.main()
