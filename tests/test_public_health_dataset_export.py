# SPDX-License-Identifier: Apache-2.0
"""Tests for the public-health dataset export boundary."""
from __future__ import annotations

import json
import pathlib
import unittest

import export_public_health_dataset


class TestPublicHealthDatasetExport(unittest.TestCase):
    def test_public_export_does_not_publish_internal_ids(self):
        sheets = export_public_health_dataset.build_sheets()
        text = json.dumps(sheets, ensure_ascii=False)

        for needle in (
            "ec:lovs",
            "calibration-point:bdbv",
            str(pathlib.Path.home()),
            "did:web",
            "arcede.ai",
        ):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, text)

    def test_calibration_ledger_uses_public_point_ids(self):
        sheets = export_public_health_dataset.build_sheets()
        rows = sheets["Calibration Ledger"]

        self.assertNotIn(
            "hypothesis_id",
            export_public_health_dataset.SHEET_COLUMNS["Calibration Ledger"],
        )
        self.assertTrue(rows)
        self.assertTrue(
            all(
                row["calibration_point_id"].startswith("public-calibration-point-")
                for row in rows
            )
        )

    def test_staged_observation_source_ids_are_manifest_backed(self):
        sheets = export_public_health_dataset.build_sheets()
        source_ids = {row["source_id"] for row in sheets["Sources"]}

        self.assertIn(
            "source_chain",
            export_public_health_dataset.SHEET_COLUMNS["Staged Observations"],
        )
        for row in sheets["Staged Observations"]:
            for source_id in [
                part.strip()
                for part in row.get("source_id", "").split(";")
                if part.strip()
            ]:
                with self.subTest(row_id=row["row_id"], source_id=source_id):
                    self.assertIn(source_id, source_ids)
            if row["kind"] == "watch_signal":
                self.assertTrue(row["source_chain"])

        ecdc = next(
            row
            for row in sheets["Staged Observations"]
            if row["row_id"] == "watch:bdbv:ecdc-risk-assessment:2026-05-21"
        )
        self.assertIn("ecdc-threat-assessment-bdbv-2026-05-21-pdf", ecdc["source_id"])

        south_kivu = next(
            row
            for row in sheets["Staged Observations"]
            if row["row_id"] == "watch:bdbv:south-kivu-bukavu:m23-claim:2026-05-21"
        )
        self.assertIn("apnews-south-kivu-m23-claim-2026-05-21-live", south_kivu["source_id"])
        self.assertIn("enca-south-kivu-m23-claim-2026-05-21-live", south_kivu["source_id"])

        ifrc = next(
            row
            for row in sheets["Staged Observations"]
            if row["row_id"] == "watch:bdbv:ifrc-regional-risk-response:2026-05-21"
        )
        self.assertIn("ifrc-regional-risk-response-2026-05-21-live", ifrc["source_id"])


if __name__ == "__main__":
    unittest.main()
