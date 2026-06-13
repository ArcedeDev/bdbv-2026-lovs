# SPDX-License-Identifier: Apache-2.0
"""Tests for the Plan A 2026-05-28 INSP-per-zone surface in snapshot_contract.

These tests focus on the four new fields landed by Plan A:

- `insp_per_zone_block` (spec §5.1)
- `per_zone_under_ascertainment_bands` (spec §5.2; R3 belt-and-suspenders refuses
  any `surface_role != "shadow_in_v1"` until Plan C parallel-scoring landing)
- `attribution_lag_disclosure` (spec §2.3 / §5.1)
- `data_scale_used` enum (spec §6.7 scale-resilience invariant)

Each test pairs a positive (good) case with a negative (bad shape) case so the
gate is exercised on both sides of every contract surface.
"""
from __future__ import annotations

import copy
import json
import pathlib
import unittest

from lovs import snapshot_contract


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _good_insp_block() -> dict:
    """A reconciliation-honest INSP block fixture (bunia + goma-cod)."""
    return {
        "as_of_data_date": "2026-05-26",
        "source_id": "inrb-umie-ebola-drc-2026-build-2026-05-27-e40bc9e",
        "method_basis": snapshot_contract.INSP_PER_ZONE_METHOD_BASIS,
        "by_lovs_zone": {
            "bunia": {
                "confirmed": 36,
                "confirmed_deaths": 2,
                "inrb_collapsed_from": [],
                "present_in_insp_classification": "present_with_data",
            },
            "goma-cod": {
                "confirmed": 1,
                "confirmed_deaths": 0,
                "inrb_collapsed_from": [],
                "present_in_insp_classification": "present_with_data",
            },
        },
        "national_at_data_date": {
            "confirmed": 37,
            "confirmed_deaths": 2,
        },
        "unallocated_residual": {
            "confirmed": 0,
            "confirmed_deaths": 0,
        },
        "coverage_audit": {
            "present_with_data": ["bunia", "goma-cod"],
            "present_but_zero": [],
            "structurally_absent": [],
        },
    }


def _good_per_zone_bands() -> dict:
    return {
        "method_basis": snapshot_contract.PCR_MODULATED_BANDS_METHOD_BASIS,
        "surface_role": "shadow_in_v1",
        "species_default_band": {"lo": 0.3, "hi": 0.9},
        "by_lovs_zone": {
            "bunia": {"lo": 0.55, "hi": 0.9},
            "goma-cod": {"lo": None, "hi": None},
        },
        "coverage_stats": {
            "modulated_zones": 1,
            "species_default_fallback_zones": 1,
            "total_zones": 2,
        },
    }


def _good_attribution_lag() -> dict:
    return {
        "per_metric": [
            {
                "metric": "confirmed",
                "timeliness": "near_timely",
                "share_attributed_to_zones": 0.92,
            },
            {
                "metric": "confirmed_deaths",
                "timeliness": "trailing",
                "share_attributed_to_zones": 0.29,
            },
        ],
        "narrative": (
            "Confirmed deaths trail the national rollup by 1-3 weeks while "
            "the INRB clinical review queue catches up."
        ),
    }


def _snapshot_with_insp_surface() -> dict:
    """Existing live snapshot + the 4 new fields layered on top."""
    base = json.loads(
        (REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text(encoding="utf-8")
    )
    base["data_scale_used"] = "per_zone"
    base["insp_per_zone_block"] = _good_insp_block()
    base["per_zone_under_ascertainment_bands"] = _good_per_zone_bands()
    base["attribution_lag_disclosure"] = _good_attribution_lag()
    return base


class TestDataScaleUsedEnum(unittest.TestCase):
    def test_each_legal_scale_value_validates(self):
        contract = snapshot_contract.build_contract(
            json.loads((REPO_ROOT / "data" / "live-bdbv-2026-output.json").read_text())
        )
        for scale in snapshot_contract.VALID_DATA_SCALES:
            with self.subTest(scale=scale):
                test_contract = copy.deepcopy(contract)
                test_contract["data_scale_used"] = scale
                if scale in snapshot_contract.SCALES_REQUIRING_PER_ZONE_BLOCK:
                    test_contract["insp_per_zone_block"] = _good_insp_block()
                snapshot_contract.validate_contract(test_contract)

    def test_illegal_scale_value_is_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["data_scale_used"] = "house_district"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "data_scale_used"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_scale_requiring_block_refused_without_block(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["data_scale_used"] = "per_zone"
        snapshot.pop("insp_per_zone_block", None)
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "requires an insp_per_zone_block"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_national_scale_does_not_require_per_zone_block(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["data_scale_used"] = "national"
        snapshot.pop("insp_per_zone_block", None)
        snapshot.pop("per_zone_under_ascertainment_bands", None)
        snapshot_contract.build_contract(snapshot)


class TestInspPerZoneBlockShape(unittest.TestCase):
    def test_good_block_passes(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot_contract.build_contract(snapshot)

    def test_reviewed_sitrep_block_passes(self):
        snapshot = _snapshot_with_insp_surface()
        block = snapshot["insp_per_zone_block"]
        block["source_id"] = "inrb-sitrep-028-2026-06-11"
        block["method_basis"] = (
            "reviewed_INSP_SitRep_028_Table_1_per_health_zone_v1"
        )
        snapshot_contract.build_contract(snapshot)

    def test_wrong_method_basis_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["insp_per_zone_block"]["method_basis"] = "INSP_per_zone_v0_bogus"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "method_basis"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_reconciliation_violation_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        # Break the contract by inflating national.confirmed without matching
        # the per-zone sum or residual.
        snapshot["insp_per_zone_block"]["national_at_data_date"]["confirmed"] = 999
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "reconciliation violated"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_negative_residual_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["insp_per_zone_block"]["unallocated_residual"]["confirmed"] = -1
        # Adjust national to keep arithmetic consistent at -1
        snapshot["insp_per_zone_block"]["national_at_data_date"]["confirmed"] = 36
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "must be >= 0"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_non_inrb_umie_source_id_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["insp_per_zone_block"]["source_id"] = "some-other-source-id"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "INRB-UMIE"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_reviewed_sitrep_method_requires_sitrep_source_id(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["insp_per_zone_block"]["source_id"] = "inrb-umie-derived-build"
        snapshot["insp_per_zone_block"]["method_basis"] = (
            "reviewed_INSP_SitRep_028_Table_1_per_health_zone_v1"
        )
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "reviewed INSP SitRep"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_komanda_mixed_with_metric_floor_case(self):
        """Real instance of `mixed_with_metric_floor` (Phase 2 finding).

        Komanda has 1 confirmed_death but 0 confirmed cases at as_of
        2026-05-26: present in the confirmed-deaths table but absent from the
        confirmed-cases table.
        """
        snapshot = _snapshot_with_insp_surface()
        snapshot["data_scale_used"] = "mixed_with_metric_floor"
        snapshot["insp_per_zone_block"]["by_lovs_zone"]["komanda"] = {
            "confirmed": 0,
            "confirmed_deaths": 1,
            "inrb_collapsed_from": [],
            "present_in_insp_classification": "present_with_data",
        }
        # bunia=2 + goma-cod=0 + komanda=1 = 3 zone-attributed; residual 2 gives
        # national 5 (a realistic upper-bound trailing case while INRB clinical
        # review catches up).
        snapshot["insp_per_zone_block"]["national_at_data_date"]["confirmed_deaths"] = 5
        snapshot["insp_per_zone_block"]["unallocated_residual"]["confirmed_deaths"] = 2
        snapshot_contract.build_contract(snapshot)


class TestR3SurfaceRoleBeltAndSuspenders(unittest.TestCase):
    """Rec J: contract refuses any surface_role != shadow_in_v1 until Plan C."""

    def test_shadow_in_v1_accepted(self):
        snapshot_contract.build_contract(_snapshot_with_insp_surface())

    def test_primary_role_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["per_zone_under_ascertainment_bands"]["surface_role"] = "primary"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "surface_role.*shadow_in_v1",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_corroborating_role_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["per_zone_under_ascertainment_bands"]["surface_role"] = "corroborating"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "surface_role.*shadow_in_v1",
        ):
            snapshot_contract.build_contract(snapshot)

    def test_unknown_role_rejected_by_enum(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["per_zone_under_ascertainment_bands"]["surface_role"] = "unknown_role"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError,
            "surface_role must be one of",
        ):
            snapshot_contract.build_contract(snapshot)


class TestPerZoneBandsRanges(unittest.TestCase):
    def test_band_outside_species_default_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        # Set a zone band whose lo is below the species default lo.
        snapshot["per_zone_under_ascertainment_bands"]["by_lovs_zone"]["bunia"] = {
            "lo": 0.1,
            "hi": 0.9,
        }
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "species_lo"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_band_lo_geq_hi_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["per_zone_under_ascertainment_bands"]["by_lovs_zone"]["bunia"] = {
            "lo": 0.7,
            "hi": 0.5,
        }
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "species_lo"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_wrong_method_basis_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["per_zone_under_ascertainment_bands"]["method_basis"] = (
            "wrong_basis_v1"
        )
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "method_basis"
        ):
            snapshot_contract.build_contract(snapshot)


class TestAttributionLagDisclosure(unittest.TestCase):
    def test_missing_metric_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["attribution_lag_disclosure"]["per_metric"] = [
            row
            for row in snapshot["attribution_lag_disclosure"]["per_metric"]
            if row["metric"] != "confirmed_deaths"
        ]
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "missing metrics"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_invalid_timeliness_value_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["attribution_lag_disclosure"]["per_metric"][0]["timeliness"] = "yolo"
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "timeliness must be"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_out_of_range_share_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["attribution_lag_disclosure"]["per_metric"][0][
            "share_attributed_to_zones"
        ] = 1.5
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, r"in \[0, 1\]"
        ):
            snapshot_contract.build_contract(snapshot)

    def test_narrative_missing_1_3_week_phrase_rejected(self):
        snapshot = _snapshot_with_insp_surface()
        snapshot["attribution_lag_disclosure"][
            "narrative"
        ] = "Confirmed deaths trail; INRB clinical review queue catches up."
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "1-3 week"
        ):
            snapshot_contract.build_contract(snapshot)


class TestNarrativeGate(unittest.TestCase):
    """validate_text_artifacts must require INSP per-zone framing when surface present."""

    def test_narrative_with_anchor_passes(self):
        contract = snapshot_contract.build_contract(_snapshot_with_insp_surface())
        good_narrative = (
            "Source vector includes INSP per-health-zone surveillance. "
            "Confirmed deaths trail the national rollup by 1-3 weeks."
        )
        snapshot_contract.validate_insp_per_zone_narrative(
            good_narrative, contract, "test-doc"
        )

    def test_missing_anchor_is_rejected(self):
        contract = snapshot_contract.build_contract(_snapshot_with_insp_surface())
        bad_narrative = "headline count: 128 confirmed cases this cycle. 1-3 week lag."
        with self.assertRaisesRegex(
            snapshot_contract.SnapshotContractError, "INSP"
        ):
            snapshot_contract.validate_insp_per_zone_narrative(
                bad_narrative, contract, "test-doc"
            )


if __name__ == "__main__":
    unittest.main()
