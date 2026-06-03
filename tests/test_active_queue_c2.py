# SPDX-License-Identifier: Apache-2.0
"""Tests for Module C2 active-queue lab-yield projection (sibling to C1).

C2 estimates how many of the known operational active-suspected queue will
laboratory-confirm, from recent reviewed-SitRep lab positivity. It is a SIBLING
diagnostic to the C1 reporting-completeness nowcast and must never feed it, must
read only reviewed promotions, and must withhold its internal audit-chain ids
from the public workbook.
"""
from __future__ import annotations

import json
import unittest

import export_public_health_dataset
from lovs import lovs_active_queue_c2
from lovs import sitrep_promotions


# Reviewed SitRep #018 fixture: 76 analyzed / 23 positive, evidence chain present.
REVIEWED_18 = {
    18: {
        "status": "reviewed",
        "source_id": "inrb-sitrep-018-2026-06-01",
        "data_as_of": "2026-06-01",
        "figures": {
            "lab_indicators_24h": {"samples_analyzed": 76, "samples_positive": 23},
        },
        "review": {
            "ready_for_model_use": True,
            "source_review_status": "reviewed",
            "evidence_chain_id": "ec:lovs:data:inrb-sitrep-018-headline-promotion:2026-06-01",
        },
    },
}


class TestActiveQueueC2(unittest.TestCase):
    def test_calibration_reproduces_documented_band(self):
        result = lovs_active_queue_c2.c2_active_queue_projection(
            REVIEWED_18,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
            suspected_under_investigation=116,
            suspected_in_isolation=173,
        )
        self.assertIsNotNone(result)
        self.assertEqual("active", result["status"])
        window = result["primary_window"]
        self.assertEqual([433, 454], window["confirmable_active_queue_50"])
        self.assertEqual([78, 99], window["expected_active_queue_confirmations_50"])
        self.assertAlmostEqual(0.2715, window["positivity_50"][0], places=4)
        self.assertAlmostEqual(0.3421, window["positivity_50"][1], places=4)
        self.assertAlmostEqual(0.3026, window["positivity_point"], places=4)
        self.assertEqual(76, window["samples_analyzed"])
        self.assertEqual(23, window["samples_positive"])
        self.assertEqual("reviewed", result["review_status"])
        self.assertEqual(
            {
                "confirmed": 355,
                "active_suspected_total": 289,
                "suspected_under_investigation": 116,
                "suspected_in_isolation": 173,
            },
            result["inputs"],
        )
        # C2 must never claim to estimate C1 quantities.
        for forbidden in ("reporting completeness", "hidden community incidence"):
            self.assertIn(forbidden, result["not_estimating"])

    def test_returns_none_without_reviewed_lab_indicators(self):
        self.assertIsNone(
            lovs_active_queue_c2.c2_active_queue_projection(
                {},
                as_of="2026-06-01",
                confirmed_active_total=355,
                active_suspected_total=289,
            )
        )

    def test_excludes_unreviewed_promotion(self):
        unreviewed = {
            18: {
                **REVIEWED_18[18],
                "status": "candidate",
                "review": {
                    **REVIEWED_18[18]["review"],
                    "source_review_status": "candidate",
                    "ready_for_model_use": False,
                },
            }
        }
        self.assertIsNone(
            lovs_active_queue_c2.c2_active_queue_projection(
                unreviewed,
                as_of="2026-06-01",
                confirmed_active_total=355,
                active_suspected_total=289,
            )
        )

    def test_ignores_lab_indicators_dated_after_as_of(self):
        self.assertIsNone(
            lovs_active_queue_c2.c2_active_queue_projection(
                REVIEWED_18,
                as_of="2026-05-31",
                confirmed_active_total=355,
                active_suspected_total=289,
            )
        )

    def test_real_reviewed_promotions_reproduce_band(self):
        rows = sitrep_promotions.reviewed_promotions_by_number()
        result = lovs_active_queue_c2.c2_active_queue_projection(
            rows,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
        )
        self.assertIsNotNone(result)
        self.assertEqual(
            [433, 454], result["primary_window"]["confirmable_active_queue_50"]
        )

    def test_carryback_series_real_promotions(self):
        rows = sitrep_promotions.reviewed_promotions_by_number()
        result = lovs_active_queue_c2.c2_active_queue_projection(
            rows,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
            suspected_under_investigation=116,
            suspected_in_isolation=173,
        )
        by_date = {w["date"]: w for w in result["per_date_windows"]}
        # Carry-back band: each date's own reported queue x June 1 reviewed positivity.
        self.assertEqual([376, 399], by_date["2026-05-30"]["confirmable_active_queue_50"])
        self.assertEqual([388, 403], by_date["2026-05-31"]["confirmable_active_queue_50"])
        self.assertEqual([433, 454], by_date["2026-06-01"]["confirmable_active_queue_50"])
        self.assertEqual("carried_back", by_date["2026-05-30"]["positivity_basis"])
        self.assertEqual("carried_back", by_date["2026-05-31"]["positivity_basis"])
        self.assertEqual("reviewed", by_date["2026-06-01"]["positivity_basis"])
        # May 29 (#015) has only a cumulative suspected count, no active-queue
        # split, so it must NOT appear in the C2 series (stays confirmed-only).
        self.assertNotIn("2026-05-29", by_date)

    def test_export_rows_withhold_internal_chain(self):
        projection = lovs_active_queue_c2.c2_active_queue_projection(
            REVIEWED_18,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
        )
        rows = export_public_health_dataset.build_active_queue_projection_rows(
            projection, source_ids="src-public-test", public_claims={}
        )
        self.assertTrue(rows)
        # No raw internal audit-chain namespace may leak into the public workbook.
        self.assertNotIn("ec:lovs:", json.dumps(rows))
        by_metric = {r["metric"]: r for r in rows}
        self.assertEqual(
            "1 internal chain reference (withheld)",
            by_metric["evidence_chain_references"]["value"],
        )
        self.assertEqual(433, by_metric["confirmable_active_queue_50"]["value_lower"])
        self.assertEqual(454, by_metric["confirmable_active_queue_50"]["value_upper"])
        for row in rows:
            self.assertEqual("active_queue_lab_yield", row["module"])
            self.assertEqual("src-public-test", row["source_ids"])
            self.assertTrue(row["evidence_ref"])

    def test_export_returns_empty_for_missing_projection(self):
        self.assertEqual(
            [],
            export_public_health_dataset.build_active_queue_projection_rows(
                None, "x", {}
            ),
        )

    def test_latest_c2_inputs_carry_source_sitrep_number(self):
        # BINARY CHECK (Step 3): the C2 fallback that reuses an earlier SitRep's
        # active-suspected queue (June-1's 289) must surface the originating
        # SitRep number, its data date, and an explicit carried-forward tag.
        import refresh_pipeline

        result = refresh_pipeline.latest_c2_active_queue_inputs("2026-06-02")
        self.assertIsNotNone(result)
        self.assertIn("source_sitrep_number", result)
        self.assertEqual(18, result["source_sitrep_number"])
        self.assertEqual("2026-06-01", result["source_data_as_of"])
        self.assertTrue(result["carried_forward"])
        self.assertEqual(
            "active_queue_omitted_from_latest_sitrep",
            result["carriedForwardReason"],
        )
        # The reused June-1 active-suspected queue is 289.
        self.assertEqual(289, result["active_suspected_total"])

    def test_c2_inputs_provenance_surfaces_carry_forward_tag(self):
        # When refresh_pipeline supplies a carried-forward provenance tag, the C2
        # projection surfaces it as a sibling `inputs_provenance` block while the
        # canonical `inputs` shape stays unchanged.
        import refresh_pipeline

        fallback = refresh_pipeline.latest_c2_active_queue_inputs("2026-06-02")
        provenance = refresh_pipeline._c2_inputs_provenance(fallback, "2026-06-02")
        result = lovs_active_queue_c2.c2_active_queue_projection(
            REVIEWED_18,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
            suspected_under_investigation=116,
            suspected_in_isolation=173,
            inputs_provenance=provenance,
        )
        self.assertIsNotNone(result)
        self.assertIn("inputs_provenance", result)
        self.assertTrue(result["inputs_provenance"]["carried_forward"])
        self.assertEqual(18, result["inputs_provenance"]["source_sitrep_number"])
        self.assertEqual(
            "2026-06-01", result["inputs_provenance"]["carriedForwardFrom"]
        )
        # The canonical inputs shape (pinned by the module self-test) is untouched.
        self.assertEqual(
            {
                "confirmed": 355,
                "active_suspected_total": 289,
                "suspected_under_investigation": 116,
                "suspected_in_isolation": 173,
            },
            result["inputs"],
        )

    def test_c2_projection_without_provenance_omits_block(self):
        # No provenance supplied -> no inputs_provenance key (back-compat / the
        # module self-test path).
        result = lovs_active_queue_c2.c2_active_queue_projection(
            REVIEWED_18,
            as_of="2026-06-01",
            confirmed_active_total=355,
            active_suspected_total=289,
        )
        self.assertIsNotNone(result)
        self.assertNotIn("inputs_provenance", result)


if __name__ == "__main__":
    unittest.main()
