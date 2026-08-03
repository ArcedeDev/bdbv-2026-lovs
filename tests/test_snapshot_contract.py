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

    def test_contract_captures_current_partition(self):
        contract = snapshot_contract.build_contract(self._snapshot())

        self.assertEqual(3768, contract["confirmed_case_partition"]["headline_confirmed_total"])
        self.assertEqual(3747, contract["confirmed_case_partition"]["zone_attributed_confirmed_total"])
        self.assertEqual(21, contract["confirmed_case_partition"]["unallocated_confirmed_total"])
        self.assertEqual(49, contract["corridor_watchlist"]["source_zone_count"])
        # Kabondo is the 49th confirmed-carrying source zone. Crossing 49 sources
        # with nine targets and excluding the Goma/Beni self-edges yields 439.
        self.assertEqual(439, contract["corridor_watchlist"]["corridor_count"])
        # Zero-confirmed INSP-monitored zones are excluded from corridor
        # generation, so the descriptive watchlist no longer carries degenerate
        # [0,0] rows: the adjusted-50 lower-bound floor is now strictly positive.
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_lower_range_pct"][0], 0.0)
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_lower_range_pct"][1], 15.0)
        self.assertGreater(contract["corridor_watchlist"]["adjusted_50_upper_range_pct"][1], 40.0)
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
        self.assertEqual(
            {"total": 3768, "drc": 3748, "uganda": 20},
            {
                key: contract["country_scope_composition"]["confirmed"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 1659, "drc": 1657, "uganda": 2},
            {
                key: contract["country_scope_composition"]["confirmed_deaths"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 719, "drc": 708, "uganda": 11},
            {
                key: contract["country_scope_composition"]["recovered"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {
                    "national_isolation_census": 690,
                    "confirmed_in_isolation": 333,
                    "suspected_in_isolation": 357,
                    "reported_suspected_in_isolation": 357,
                    "active_queue_suspected_total": 357,
            },
            {
                key: contract["inrb_semantic_delta"][key]
                for key in (
                    "national_isolation_census",
                    "confirmed_in_isolation",
                    "suspected_in_isolation",
                    "reported_suspected_in_isolation",
                    "active_queue_suspected_total",
                )
            },
        )

    def test_snapshot_contract_rejects_aggregate_smearing(self):
        snapshot = self._snapshot()
        smeared = copy.deepcopy(snapshot)
        for corridor in smeared["corridors"]:
            corridor["drivers"] = ["headline confirmed count 88 applied to this source zone"]

        with self.assertRaises(snapshot_contract.SnapshotContractError):
            snapshot_contract.validate_snapshot(smeared)

    def test_snapshot_contract_rejects_country_scope_mismatch(self):
        snapshot = copy.deepcopy(self._snapshot())
        # The partition guard ("zone-attributed exceeds headline") runs BEFORE the
        # country-scope composition check, so the synthetic primary must be >= the
        # fixture's zone-attributed total to reach the country-scope branch, yet
        # != the promoted country-scope total so the mismatch still fires. The
        # zone-attributed total is the smallest such value, and it moves every
        # cycle (2456 at SitRep67, 2519 at SitRep68), so derive it from the
        # fixture rather than pinning a literal that silently stops exercising
        # this branch when the numbers drift.
        zone_total = sum(
            int(zone["confirmed"]) for zone in snapshot["zone_attributed_counts"].values()
        )
        country_scope_total = int(snapshot["reported_counts"]["confirmed"]["primary"])
        self.assertNotEqual(
            zone_total,
            country_scope_total,
            "fixture no longer separates zone-attributed from country-scope; this test would "
            "pass vacuously",
        )
        snapshot["reported_counts"]["confirmed"]["primary"] = zone_total

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "country-scope total",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_snapshot_contract_rejects_isolation_census_as_suspected(self):
        snapshot = copy.deepcopy(self._snapshot())
        snapshot["reported_counts"]["suspected_in_isolation"]["primary"] = 262

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "suspected-only split",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_snapshot_contract_rejects_c2_active_queue_semantic_mismatch(self):
        snapshot = copy.deepcopy(self._snapshot())
        for row in snapshot["analysis_dependency_audit"]:
            if row.get("surface") == "active_queue_projection_c2":
                row["inputs"]["active_suspected_total"] = 262
                break

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "active_queue_projection_c2",
        ):
            snapshot_contract.build_contract(snapshot)

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
