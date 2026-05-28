# SPDX-License-Identifier: Apache-2.0
"""Tests for lovs.retrospective_attribution_audit (spec section 9.2)."""
from __future__ import annotations

import json
import pathlib
import unittest

from lovs import retrospective_attribution_audit as audit


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _good_block() -> dict:
    return {
        "block_id": "calibration-block:test:2026-05-26",
        "pinned_at": "2026-05-26",
        "points": [
            {
                "source": "bunia",
                "target": "goma-cod",
                "risk_adj_50": [0.10, 0.27],
            },
            {
                "source": "rwampara",
                "target": "goma-cod",
                "risk_adj_50": [0.20, 0.45],
            },
        ],
    }


def _good_insp_block(deaths_by_zone: dict[str, int]) -> dict:
    return {
        "as_of_data_date": "2026-05-26",
        "source_id": "inrb-umie-fixture",
        "method_basis": "INRB_UMIE_INSP_per_zone_v1",
        "by_lovs_zone": {
            zone_id: {
                "confirmed": 0,
                "suspected": 0,
                "confirmed_deaths": deaths,
                "suspected_deaths": 0,
                "inrb_collapsed_from": [],
                "present_in_insp_classification": "present_with_data",
            }
            for zone_id, deaths in deaths_by_zone.items()
        },
    }


class TestAuditBlock(unittest.TestCase):
    def test_no_insp_block_falls_back_to_unavailable_status(self):
        rows = audit.audit_block(_good_block(), insp_per_zone_block=None)
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertEqual("insp_not_available_at_pin_date", row["attribution_status"])
            self.assertIsNone(row["insp_confirmed_deaths_at_data_as_of"])

    def test_corroborated_status_when_insp_deaths_present(self):
        rows = audit.audit_block(
            _good_block(),
            _good_insp_block({"bunia": 2, "rwampara": 2}),
        )
        for row in rows:
            self.assertEqual("pinned_corroborated", row["attribution_status"])
            self.assertGreater(row["insp_confirmed_deaths_at_data_as_of"], 0)

    def test_no_insp_deaths_status(self):
        rows = audit.audit_block(
            _good_block(),
            _good_insp_block({"bunia": 0, "rwampara": 0}),
        )
        for row in rows:
            self.assertEqual("pinned_no_insp_deaths", row["attribution_status"])
            self.assertEqual(0, row["insp_confirmed_deaths_at_data_as_of"])

    def test_mismatched_as_of_falls_back_to_unavailable(self):
        insp = _good_insp_block({"bunia": 2, "rwampara": 2})
        insp["as_of_data_date"] = "2026-05-25"
        rows = audit.audit_block(_good_block(), insp)
        for row in rows:
            self.assertEqual(
                "insp_not_available_at_pin_date", row["attribution_status"]
            )

    def test_audit_row_carries_pinned_risk_interval(self):
        rows = audit.audit_block(
            _good_block(),
            _good_insp_block({"bunia": 2, "rwampara": 2}),
        )
        self.assertEqual([0.10, 0.27], rows[0]["risk_adj_50_at_pin"])
        self.assertEqual([0.20, 0.45], rows[1]["risk_adj_50_at_pin"])


class TestAuditLedger(unittest.TestCase):
    def test_audits_every_block_in_the_ledger(self):
        ledger = {
            "blocks": [
                _good_block(),
                {
                    "block_id": "calibration-block:test:2026-05-21",
                    "pinned_at": "2026-05-21",
                    "points": [
                        {"source": "bunia", "target": "kampala-uga", "risk_adj_50": [0.05, 0.15]},
                    ],
                },
            ],
        }
        rows = audit.audit_ledger(ledger, _good_insp_block({"bunia": 2, "rwampara": 2}))
        self.assertEqual(3, len(rows))


class TestAgainstLiveLedger(unittest.TestCase):
    def test_live_may_26_goma_block_three_corridors(self):
        ledger = audit.load_default_ledger()
        insp_block = audit.load_default_insp_block()
        may26_block = next(
            b for b in ledger["blocks"] if b.get("pinned_at") == "2026-05-26"
        )
        rows = audit.audit_block(may26_block, insp_block)
        self.assertEqual(3, len(rows))
        # bunia and rwampara have INSP confirmed_deaths > 0 at 2026-05-26
        sources = {r["source"] for r in rows}
        self.assertEqual({"bunia", "rwampara", "mongbwalu"}, sources)


if __name__ == "__main__":
    unittest.main()
