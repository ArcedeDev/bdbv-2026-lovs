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
            self.assertTrue((output_dir / "analysis_dependency_audit.csv").exists())
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
        # publication_cutoff advances to the most recent published_at across the
        # manifest. SitRep #019 was published on 2026-06-03; its DRC-only
        # metrics stay scoped in normalized_content and the snapshot composes
        # country-scope values separately.
        self.assertEqual(
            "2026-06-03",
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
        sitrep009 = "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24"
        self.assertEqual(
            "",
            by_id[f"source_data_report_date:{sitrep009}"]["date_value"],
        )
        self.assertEqual(
            "not_recorded",
            by_id[f"source_data_report_date:{sitrep009}"]["status"],
        )
        self.assertEqual(
            "2026-05-24",
            by_id[f"source_publication_date:{sitrep009}"]["date_value"],
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

    def test_timeline_omits_sources_without_data_report_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "reported_counts.csv").open() as f:
                reported_rows = list(csv.DictReader(f))
            with (output_dir / "timeline.csv").open() as f:
                timeline_rows = list(csv.DictReader(f))
            with (output_dir / "sources.csv").open() as f:
                source_rows = list(csv.DictReader(f))

        source_id = "drc-moh-epidemie-dashboard-sitrep-009-graphql-2026-05-24"
        # After the May-25 deaths reconciliation the MoH dashboard aggregate is a
        # conflict anchor, not a reconciled-count primary, so it is no longer a
        # reconciled-metric source_id; it is retained as provenance in sources.csv
        # (and in the conflict trail of the reconciled rows), not dropped.
        self.assertNotIn(
            source_id,
            {
                row["source_id"] for row in reported_rows
                if row["row_type"] == "snapshot_reconciled_metric"
            },
            "MoH aggregate is a conflict anchor, not a reconciled-count primary",
        )
        self.assertIn(
            source_id,
            {row["source_id"] for row in source_rows},
            "expected MoH aggregate to remain as conflict-anchor provenance in sources.csv",
        )
        self.assertTrue(
            all(row["date"] for row in timeline_rows),
            "every timeline point must carry a data/report date",
        )
        self.assertNotIn(source_id, {row["source_id"] for row in timeline_rows})

    def test_timeline_exports_c2_per_date_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "timeline.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_date_metric = {(row["date"], row["metric"]): row for row in rows}
        expected = {
            ("2026-05-30", "confirmable_active_queue_50_lower"): "376",
            ("2026-05-30", "confirmable_active_queue_50_upper"): "399",
            ("2026-05-31", "confirmable_active_queue_50_lower"): "388",
            ("2026-05-31", "confirmable_active_queue_50_upper"): "403",
            ("2026-06-01", "confirmable_active_queue_50_lower"): "433",
            ("2026-06-01", "confirmable_active_queue_50_upper"): "454",
        }
        for key, value in expected.items():
            self.assertEqual(value, by_date_metric[key]["value"])
            self.assertEqual("count", by_date_metric[key]["unit"])
            self.assertIn("active-queue lab-yield", by_date_metric[key]["note"])

        timeline_text = "\n".join(",".join(row.values()) for row in rows)
        self.assertNotIn("ec:lovs:", timeline_text)

    def test_analysis_dependency_audit_exports_model_use_and_holdouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "analysis_dependency_audit.csv").open() as f:
                rows = list(csv.DictReader(f))

        by_surface = {row["surface"]: row for row in rows}
        self.assertEqual(
            "updated",
            by_surface["visibility_module_c"]["status"],
        )
        self.assertIn("370", by_surface["visibility_module_c"]["input_values"])
        # The retired cumulative-suspected figure (349) must no longer appear on
        # the visibility input surface; confirmed is now the only cumulative input.
        self.assertNotIn("349", by_surface["visibility_module_c"]["input_values"])
        self.assertEqual(
            "updated",
            by_surface["active_queue_projection_c2"]["status"],
        )
        self.assertIn("355", by_surface["active_queue_projection_c2"]["input_values"])
        self.assertIn(
            "289",
            by_surface["active_queue_projection_c2"]["input_values"],
        )
        self.assertEqual(
            "updated_snapshot_level",
            by_surface["death_back_projection_and_grid"]["status"],
        )
        self.assertIn("63", by_surface["death_back_projection_and_grid"]["input_values"])
        self.assertIn(
            "two independent dated series",
            by_surface["death_back_projection_and_grid"]["clock_basis"],
        )
        self.assertEqual("", by_surface["death_back_projection_and_grid"]["held_out_reason"])
        self.assertEqual(
            "source_attribution_lag",
            by_surface["corridor_watchlist"]["status"],
        )
        # 2026-05-29 zone ingest (INRB-UMIE build-2026-06-01-b4cafc9): zone-
        # attributed confirmed is 243, so unallocated headline (370 - 243) is 127.
        self.assertIn("127", by_surface["corridor_watchlist"]["input_values"])
        self.assertIn("build-2026-06-01-b4cafc9", by_surface["corridor_watchlist"]["blocked_by"])

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

    def test_timeline_basis_column(self):
        # BINARY CHECK (Step 2): every per-point death row carries an explicit
        # basis. A death row dated on/after the 2026-06-02 cutoff is
        # confirmed_only; a death row dated before the cutoff is broad_register;
        # case rows carry an empty basis.
        #
        # The 2026-06-02 deaths_confirmed row is the snapshot-reconciled death
        # row (as_of 2026-06-02). The pre-cutoff death row is a source-extracted
        # death point carried into the timeline. Both are exercised through the
        # real emit paths with controlled inputs so the assertion is
        # deterministic and independent of the production snapshot.
        june2_deaths_row = export_public_health_dataset.build_reported_counts_rows(
            {
                "as_of": "2026-06-02T23:59:59Z",
                "country_scope": ["COD", "UGA"],
                "reported_counts": {},
                "reported_deaths": {
                    "confirmed": {
                        "primary": 63,
                        "min": 61,
                        "max": 63,
                        "primary_source_id": "inrb-sitrep-019-2026-06-02",
                        "conflicting_source_ids": [],
                    },
                },
            },
            {"entries": []},
            {},
            {},
        )
        by_metric = {row["metric"]: row for row in june2_deaths_row}
        self.assertIn("deaths_confirmed", by_metric)
        self.assertEqual("confirmed_only", by_metric["deaths_confirmed"]["basis"])

        # Pre-cutoff: a source-extracted deaths point (dated 2026-05-31) projected
        # through build_timeline_rows must carry broad_register, while a case row
        # on the same date carries an empty basis.
        count_rows = [
            {
                "row_id": "source:inrb-sitrep-017-2026-05-31:deaths",
                "row_type": "source_extracted_metric",
                "metric": "deaths",
                "as_of_date": "2026-05-31",
                "value": 49,
                "unit": "count",
                "source_id": "inrb-sitrep-017-2026-05-31",
                "evidence_ref": "PUBLIC-CLAIM-AUDIT",
                "source_url": "",
                "archive_sha256": "",
                "license": "",
                "correction_note": "",
            },
            {
                "row_id": "source:inrb-sitrep-017-2026-05-31:cases_confirmed",
                "row_type": "source_extracted_metric",
                "metric": "confirmed_cases",
                "as_of_date": "2026-05-31",
                "value": 328,
                "unit": "count",
                "source_id": "inrb-sitrep-017-2026-05-31",
                "evidence_ref": "PUBLIC-CLAIM-AUDIT",
                "source_url": "",
                "archive_sha256": "",
                "license": "",
                "correction_note": "",
            },
        ]
        timeline = export_public_health_dataset.build_timeline_rows(count_rows)
        by_id = {row["row_id"]: row for row in timeline}
        deaths_row = by_id["timeline:inrb-sitrep-017-2026-05-31:deaths"]
        case_row = by_id["timeline:inrb-sitrep-017-2026-05-31:cases_confirmed"]
        self.assertEqual("broad_register", deaths_row["basis"])
        self.assertEqual("", case_row["basis"])
        # Every emitted timeline row carries the basis column.
        for row in timeline:
            self.assertIn("basis", row)

    def test_timeline_csv_has_basis_column(self):
        # The basis column must reach the shipped timeline.csv surface.
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "timeline.csv").open() as f:
                reader = csv.DictReader(f)
                self.assertIn("basis", reader.fieldnames)
                rows = list(reader)
        # Any death-metric timeline row dated on/after the cutoff is confirmed_only;
        # any dated before is broad_register; case rows are empty.
        for row in rows:
            if "death" in row["metric"]:
                expected = (
                    "confirmed_only" if row["date"][:10] >= "2026-06-02" else "broad_register"
                )
                self.assertEqual(expected, row["basis"], msg=row["row_id"])
            else:
                self.assertEqual("", row["basis"], msg=row["row_id"])

    def test_workbook_is_byte_deterministic(self):
        """Two exports of the same snapshot must produce identical workbook bytes."""
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            w1 = export_public_health_dataset.export_package(pathlib.Path(t1))["workbook"]
            w2 = export_public_health_dataset.export_package(pathlib.Path(t2))["workbook"]
            self.assertEqual(w1.read_bytes(), w2.read_bytes())


if __name__ == "__main__":
    unittest.main()
