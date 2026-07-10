# SPDX-License-Identifier: Apache-2.0
"""Tests for reviewed SitRep promotion payloads."""
from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from lovs import sitrep_promotion_gate
from lovs import sitrep_promotions
from lovs import release_contract


SR55_RAW_PATH = (
    pathlib.Path(__file__).parents[1]
    / "data"
    / "bundibugyo-2026"
    / "raw"
    / "44706aea5157df826e05f4be706abca3acf93033d0e586a31c8e03711a94d91b"
)


class TestSitRepPromotions(unittest.TestCase):
    def test_reviewed_sitrep18_is_model_ready(self):
        rows = sitrep_promotions.reviewed_promotions_by_number()
        sitrep18 = rows[18]

        self.assertEqual("2026-06-01", sitrep18["data_as_of"])
        self.assertEqual(355, sitrep18["figures"]["country_scope_confirmed_total"])
        self.assertEqual(61, sitrep18["figures"]["country_scope_confirmed_deaths"])
        self.assertEqual(23, sitrep18["figures"]["lab_indicators_24h"]["samples_positive"])

    def test_candidate_payload_cannot_be_model_ready(self):
        payload = sitrep_promotions.candidate_payload_from_sidecar({
            "source_id": "insp-wordpress-sitrep-n019-pdf",
            "registry_id": "insp-wordpress-sitrep-feed",
            "url": "https://insp.cd/sitrep-19.pdf",
            "published_at": "2026-06-03T12:00:00Z",
            "normalized_content": {
                "sitrep_number": 19,
                "publication_date_candidates": ["2026-06-02"],
                "pdf_asset": {"sitrep_number": 19},
            },
        })
        reviewed_candidate = copy.deepcopy(payload)
        reviewed_candidate["review"]["ready_for_model_use"] = True

        with self.assertRaises(sitrep_promotions.SitRepPromotionError):
            sitrep_promotions.validate_promotion(reviewed_candidate)

    @unittest.skipUnless(SR55_RAW_PATH.is_file(), "reviewed SitRep 55 raw bytes unavailable")
    def test_sitrep55_builds_content_addressed_release_envelope(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        release = release_contract.build_release_envelope(promotion)

        self.assertEqual("bdbv-release/v1", release["schema_version"])
        self.assertEqual(
            "bdbv-sr055-2026-07-08-44706aea5157df82",
            release["release_id"],
        )
        self.assertEqual(55, release["edition"])
        self.assertEqual("2026-07-08", release["snapshot_date"])
        self.assertEqual("published", release["publication_state"])
        self.assertEqual(
            "44706aea5157df826e05f4be706abca3acf93033d0e586a31c8e03711a94d91b",
            release["source_receipt"]["sha256"],
        )

    def test_release_envelope_fails_closed_without_structured_receipt(self):
        promotion = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[55])
        promotion.pop("source_receipt")
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(promotion)

    def test_release_envelope_rejects_boolean_sizes_and_bad_calendar_dates(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        boolean_size = copy.deepcopy(promotion)
        boolean_size["source_receipt"]["byte_length"] = True
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(boolean_size)

        bad_date = copy.deepcopy(promotion)
        bad_date["data_as_of"] = "2026-02-31"
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract.build_release_envelope(bad_date)

    def test_release_envelope_rejects_receipt_not_bound_to_local_raw_archive(self):
        promotion = copy.deepcopy(sitrep_promotions.reviewed_promotions_by_number()[55])
        promotion["source_receipt"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            release_contract.ReleaseContractError, "local raw archive"
        ):
            release_contract.build_release_envelope(promotion)

    def test_historical_promotion_without_receipt_remains_unenriched(self):
        promotion = sitrep_promotions.reviewed_promotions_by_number()[54]
        snapshot = {"as_of": "2026-07-07T23:59:59Z", "convergence": {}}

        self.assertEqual(
            snapshot, release_contract.maybe_enrich_snapshot(snapshot, promotion)
        )

    def test_contract_only_retains_historical_materialization_without_receipt(self):
        import refresh_pipeline

        promotion = sitrep_promotions.reviewed_promotions_by_number()[54]
        snapshot = {"as_of": "2026-07-07T23:59:59Z", "convergence": {}}
        with tempfile.TemporaryDirectory(dir=refresh_pipeline.REPO_ROOT) as tmp:
            output_path = pathlib.Path(tmp) / "historical.json"
            output_path.write_text(json.dumps(snapshot), encoding="utf-8")
            with (
                mock.patch.object(refresh_pipeline, "OUT_PATH", output_path),
                mock.patch.object(
                    refresh_pipeline,
                    "_latest_reviewed_promotion_at_or_before",
                    return_value=(pathlib.Path("sr54.json"), promotion),
                ),
            ):
                self.assertEqual(
                    0,
                    refresh_pipeline.main(
                        ["--as-of", "2026-07-07", "--contract-only"]
                    ),
                )

            self.assertEqual(snapshot, json.loads(output_path.read_text(encoding="utf-8")))

    @unittest.skipUnless(SR55_RAW_PATH.is_file(), "reviewed SitRep 55 raw bytes unavailable")
    def test_contract_enrichment_preserves_materialized_model_values(self):
        snapshot_path = pathlib.Path(__file__).parents[1] / "data" / "live-bdbv-2026-output.json"
        original = json.loads(snapshot_path.read_text(encoding="utf-8"))
        original.pop("release", None)
        nowcast = original["convergence"]["true_burden_nowcast"]
        nowcast.pop("estimate_registry", None)
        original["convergence"]["methodology"] = [
            row
            for row in original["convergence"]["methodology"]
            if row.get("quantity")
            != "Primary sensitivity scenario (care versus ascertainment)"
        ]
        original["convergence"]["true_burden_nowcast"]["care_adjusted"][
            "method"
        ] = "legacy contradictory method"
        untouched = copy.deepcopy(original)

        promotion = sitrep_promotions.reviewed_promotions_by_number()[55]
        enriched = release_contract.enrich_snapshot(original, promotion)

        self.assertEqual(untouched, original)
        self.assertEqual(
            "bdbv-sr055-2026-07-08-44706aea5157df82",
            enriched["release"]["release_id"],
        )
        registry = enriched["convergence"]["true_burden_nowcast"][
            "estimate_registry"
        ]
        self.assertEqual("primary_sensitivity", registry[0]["display_role"])
        self.assertIn("NOT a measured care correction", registry[0]["method"])

        projected = copy.deepcopy(enriched)
        projected.pop("release")
        projected_nowcast = projected["convergence"]["true_burden_nowcast"]
        projected_nowcast.pop("estimate_registry")
        projected["convergence"]["methodology"] = [
            row
            for row in projected["convergence"]["methodology"]
            if row.get("quantity")
            != "Primary sensitivity scenario (care versus ascertainment)"
        ]
        projected_nowcast["care_adjusted"]["method"] = "legacy contradictory method"
        self.assertEqual(untouched, projected)

    def test_gate_requires_reviewed_coverage_through_release_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            source = sitrep_promotions.PROMOTIONS_DIR / "sitrep-018-2026-06-01.json"
            (directory / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

            ok = sitrep_promotion_gate.validate(directory, require_through="2026-06-01")
            self.assertEqual("2026-06-01", ok["latest_data_as_of"])

            with self.assertRaises(sitrep_promotions.SitRepPromotionError):
                sitrep_promotion_gate.validate(directory, require_through="2026-06-02")


if __name__ == "__main__":
    unittest.main()
