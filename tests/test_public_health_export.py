# SPDX-License-Identifier: Apache-2.0
"""Tests for the public-health workbook exporter."""
from __future__ import annotations

import csv
import pathlib
import tempfile
import unittest
import zipfile

import export_public_health_dataset


class TestPublicHealthDatasetExport(unittest.TestCase):

    def test_export_package_writes_valid_workbook_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)

            workbook = paths["workbook"]
            self.assertTrue(workbook.exists())
            self.assertTrue(zipfile.is_zipfile(workbook))
            with zipfile.ZipFile(workbook) as zf:
                names = set(zf.namelist())
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet11.xml", names)

            self.assertTrue((output_dir / "snapshot_clocks.csv").exists())
            self.assertTrue((output_dir / "reported_counts.csv").exists())
            self.assertTrue((output_dir / "public_claim_audit.csv").exists())
            self.assertFalse((output_dir / "evidence_chains.csv").exists())
            self.assertTrue(paths["schema"].exists())
            self.assertTrue(paths["manifest"].exists())

    def test_reported_counts_are_attributed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = list(csv.DictReader(f))

        self.assertGreater(len(rows), 10)
        required = (
            "source_id",
            "source_url",
            "archive_sha256",
            "license",
            "evidence_ref",
            "evidence_status",
        )
        for row in rows:
            missing = [field for field in required if not row[field].strip()]
            self.assertFalse(missing, msg=f"{row['row_id']} missing {missing}")

    def test_corrections_and_restricted_sources_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            text = (output_dir / "corrections_gaps.csv").read_text()
            evidence = (output_dir / "public_claim_audit.csv").read_text()

        self.assertIn("Kinshasa", text)
        self.assertIn("Imperial table 3", text)
        self.assertIn("Corridor gravity exponents", text)
        self.assertIn("restricted-local-review-not-redistributed", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/Users/", evidence)

        sensitive_needles = (
            "ec:lovs:",
            "claim:lovs:",
            "src:local-",
            "raw_bytes_relpath",
            "gamma(4.0",
            "under_ascertainment_uniform",
            "clamp [0.1",
        )
        for needle in sensitive_needles:
            self.assertNotIn(needle, evidence)

    def test_snapshot_reconciled_counts_have_values(self):
        """Reconciled headline counts must not ship blank (schema-key drift guard)."""
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = [
                    r for r in csv.DictReader(f)
                    if r["row_type"] == "snapshot_reconciled_metric"
                ]
        self.assertTrue(rows, "expected snapshot-reconciled rows")
        for row in rows:
            has_value = bool(row["value"].strip())
            has_range = bool(row["value_min"].strip() and row["value_max"].strip())
            self.assertTrue(
                has_value or has_range,
                msg=f"{row['row_id']} carries neither a value nor a min/max range",
            )

    def test_snapshot_clocks_preserve_publication_report_and_retrieval_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "snapshot_clocks.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_id = {row["row_id"]: row for row in rows}
        self.assertEqual(
            "2026-05-24",
            by_id["snapshot:publication_cutoff"]["date_value"],
        )
        self.assertEqual(
            "not_recorded",
            by_id["snapshot:generated_at"]["status"],
        )

        sitrep008 = "drc-moh-epidemie-dashboard-sitrep-008-graphql-2026-05-23"
        self.assertEqual(
            "2026-05-22",
            by_id[f"source_data_report_date:{sitrep008}"]["date_value"],
        )
        self.assertEqual(
            "2026-05-23",
            by_id[f"source_publication_date:{sitrep008}"]["date_value"],
        )
        self.assertEqual(
            "2026-05-23T18:36:26Z",
            by_id[f"source_retrieval_date:{sitrep008}"]["timestamp_value"],
        )

    def test_source_review_rows_keep_clocks_but_not_reported_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            reported = (output_dir / "reported_counts.csv").read_text()
            clocks = (output_dir / "snapshot_clocks.csv").read_text()
            sources = (output_dir / "sources.csv").read_text()

        source_id = "drc-moh-epidemie-dashboard-sitrep-008-graphql-2026-05-23"
        self.assertNotIn(f"source:{source_id}:", reported)
        self.assertIn(source_id, clocks)
        self.assertIn(source_id, sources)

    def test_public_deliverables_carry_no_source_review_status_token(self):
        """Regression gate: the internal source-review status signal must never
        reach a public surface. Sources/clocks may keep the source as provenance,
        but the structured status token (source_review / display_only / superseded)
        is redacted from every shipped CSV and the workbook XML."""
        forbidden = (
            export_public_health_dataset.PUBLIC_SUPPRESSED_TABLE_SEMANTICS
            | export_public_health_dataset.PUBLIC_SUPPRESSED_MODEL_USES
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            csv_blobs = {
                p.name: p.read_text()
                for p in output_dir.glob("*.csv")
            }
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(n).decode("utf-8", "replace")
                    for n in zf.namelist()
                    if n.endswith(".xml")
                )

        for token in forbidden:
            for name, blob in csv_blobs.items():
                self.assertNotIn(token, blob, f"{name} leaks source-review token {token!r}")
            self.assertNotIn(token, workbook_xml, f"workbook leaks source-review token {token!r}")

    def test_source_death_rows_export_as_deaths(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                rows = [
                    r for r in csv.DictReader(f)
                    if r["row_type"] == "source_extracted_metric" and ":deaths" in r["row_id"]
                ]

        self.assertTrue(rows, "expected source-level death rows")
        for row in rows:
            self.assertEqual("deaths", row["metric"], msg=row["row_id"])

    def test_workbook_is_byte_deterministic(self):
        """Two exports of the same snapshot must produce identical workbook bytes."""
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            w1 = export_public_health_dataset.export_package(pathlib.Path(t1))["workbook"]
            w2 = export_public_health_dataset.export_package(pathlib.Path(t2))["workbook"]
            self.assertEqual(w1.read_bytes(), w2.read_bytes())


if __name__ == "__main__":
    unittest.main()
