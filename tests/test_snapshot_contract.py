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

        self.assertEqual(4401, contract["confirmed_case_partition"]["headline_confirmed_total"])
        self.assertEqual(4053, contract["confirmed_case_partition"]["zone_attributed_confirmed_total"])
        self.assertEqual(348, contract["confirmed_case_partition"]["unallocated_confirmed_total"])
        self.assertEqual(53, contract["corridor_watchlist"]["source_zone_count"])
        # Lubero (first confirmed case) and Wanie-Rukula (integrated after Tshopo
        # harmonization) are the 50th and 51st confirmed-carrying source zones at
        # SitRep #080. Crossing 51 sources with nine targets and excluding the
        # Goma/Beni self-edges yields 457.
        self.assertEqual(475, contract["corridor_watchlist"]["corridor_count"])
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
            {"total": 4401, "drc": 4381, "uganda": 20},
            {
                key: contract["country_scope_composition"]["confirmed"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 2013, "drc": 2011, "uganda": 2},
            {
                key: contract["country_scope_composition"]["confirmed_deaths"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        self.assertEqual(
            {"total": 880, "drc": 869, "uganda": 11},
            {
                key: contract["country_scope_composition"]["recovered"][key]
                for key in ("total", "drc", "uganda")
            },
        )
        # Compact SR85 publishes only the 704-person isolation/CTE census,
        # without a suspected/confirmed split. It must not materialize the
        # split-only semantic-delta contract.
        self.assertNotIn("inrb_semantic_delta", contract)

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
        snapshot["reported_counts"]["suspected_in_isolation"] = {
            "min": 262,
            "max": 262,
            "primary": 262,
            "primary_source_id": "inrb-sitrep-083-2026-08-05",
            "conflicting_source_ids": [],
        }

        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "suspected-only split",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_semantic_delta_allows_source_declared_unclassified_remainder(self):
        delta = {
            "source_id": "inrb-sitrep-test",
            "national_isolation_census": 674,
            "confirmed_in_isolation": 311,
            "suspected_in_isolation": 343,
            "unclassified_in_isolation": 20,
            "reported_suspected_in_isolation": 343,
        }

        snapshot_contract._validate_inrb_semantic_delta(delta)

        delta["unclassified_in_isolation"] = 30
        with self.assertRaisesRegex(snapshot_contract.SnapshotContractError, "isolation census"):
            snapshot_contract._validate_inrb_semantic_delta(delta)

    def test_snapshot_contract_does_not_reclassify_compact_census_for_c2(self):
        snapshot = self._snapshot()
        c2 = next(
            row for row in snapshot["analysis_dependency_audit"]
            if row.get("surface") == "active_queue_projection_c2"
        )
        # The load-bearing invariant: the compact isolation census is a single
        # unsplit headcount of everyone in isolation, confirmed and suspected
        # together. It must never be promoted into C2 as if it were a suspected
        # queue. A genuine suspected-in-isolation figure from an earlier edition
        # is a legitimate carried basis; 704 is not a basis at all.
        census = 704
        self.assertNotEqual(census, c2["inputs"]["active_suspected_total"])
        self.assertTrue(c2["inputs_provenance"]["carried_forward"])
        # Whichever basis was used has to be named, and the confirmed anchor has
        # to come from the same edition as the queue, so the yield is never a
        # current confirmed count crossed with an older queue.
        basis = c2["inputs_provenance"]["active_queue_basis"]
        self.assertIn(basis, {"suspected_active_total", "suspected_in_isolation"})
        self.assertEqual(
            c2["inputs_provenance"]["source_data_as_of"],
            c2["inputs_provenance"]["carriedForwardFrom"],
        )

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
