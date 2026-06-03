# SPDX-License-Identifier: Apache-2.0
"""Tests for reviewed SitRep promotion payloads."""
from __future__ import annotations

import copy
import pathlib
import tempfile
import unittest

from lovs import sitrep_promotion_gate
from lovs import sitrep_promotions


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
