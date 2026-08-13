# SPDX-License-Identifier: Apache-2.0
"""Tests for the public-health workbook exporter."""
from __future__ import annotations

import csv
import json
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
            self.assertTrue((output_dir / "sitrep_narrative.csv").exists())
            self.assertFalse((output_dir / "evidence_chains.csv").exists())
            self.assertTrue(paths["schema"].exists())
            self.assertTrue(paths["manifest"].exists())

    def test_per_zone_workbook_pointer_uses_current_inrb_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(name).decode("utf-8", "replace")
                    for name in zf.namelist()
                    if name.endswith(".xml")
                )

        self.assertIn("INRB-UMIE/BDBV2026-Data", workbook_xml)
        # The per-zone pointer cites the retained INRB-UMIE source-review build
        # (upstream_reference: build-2026-06-12-1dfdf1e, data as of 2026-06-11),
        # while the current primary per-zone source-load is the reviewed INSP
        # SitRep #042 endpoint.
        self.assertIn("build-2026-06-12-1dfdf1e", workbook_xml)
        self.assertIn("inrb-sitrep-042-2026-06-25", workbook_xml)
        self.assertIn("data as of 2026-06-11", workbook_xml)

    def test_sitrep_narrative_export_carries_reviewed_sitrep_47_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            paths = export_public_health_dataset.export_package(output_dir)
            with (output_dir / "sitrep_narrative.csv").open() as f:
                rows = list(csv.DictReader(f))
            with zipfile.ZipFile(paths["workbook"]) as zf:
                workbook_xml = "\n".join(
                    zf.read(name).decode("utf-8", "replace")
                    for name in zf.namelist()
                    if name.endswith(".xml")
                )

        self.assertGreater(len(rows), 10)
        self.assertIn("SitRep Narrative", workbook_xml)
        self.assertTrue(all(row["source_id"] == "inrb-sitrep-085-2026-08-07" for row in rows))
        sections = {row["section"] for row in rows}
        self.assertIn("highlights", sections)
        self.assertIn("care_continuity", sections)
        self.assertIn("challenges", sections)
        self.assertIn("priorities", sections)
        text = "\n".join(row["text"] for row in rows)
        self.assertIn(
            "The national isolation/CTE census is 595 people, but the compact report "
            "does not publish a national confirmed-versus-suspected status split",
            text,
        )
        self.assertIn(
            "The DRC headline is 4209 confirmed cases and 1916 confirmed deaths",
            text,
        )
        self.assertIn(
            "The published footprint remains 53 of 140 health zones, but SitRep 85 "
            "contains no per-health-zone case or death rows",
            text,
        )
        # The cycle's lead epidemiological signal must survive onto the public
        # narrative surface: positivity rising while testing volume falls.
        self.assertIn(
            "14923 of 17896 contacts were seen, or 83.4%",
            text,
        )
        notes = "\n".join(row["public_note"] for row in rows)
        self.assertIn("compact edition contains no per-health-zone table", notes)
        self.assertNotIn("frans@", text)

    def test_surveillance_zone_export_carries_jiba_as_display_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = pathlib.Path(tmp)
            export_public_health_dataset.export_package(output_dir)
            with (output_dir / "surveillance_zones.csv").open() as f:
                rows = list(csv.DictReader(f))

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("jiba", row["lovs_zone_id"])
        self.assertEqual("Jiba", row["zone_name"])
        self.assertEqual("2026-05-30", row["as_of_data_date"])
        self.assertEqual("2", row["suspected"])
        self.assertEqual("0", row["confirmed"])
        self.assertEqual("display_only_surveillance", row["model_use"])
        self.assertIn("retired", row["basis"].lower())
        self.assertIn("national", row["basis"].lower())

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
        # publication_cutoff advances to the most recent publication date across the
        # manifest; the SitRep #068 cover publication (2026-07-22) is the
        # current knowledge cutoff.
        self.assertEqual(
            "2026-08-12",
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
        # Carry-back of the most-recent reviewed lab positivity onto each
        # date's reported active-suspected queue. The series advances to the
        # current cycle on the suspected-in-isolation basis once INSP stops
        # publishing the full active-suspected total.
        expected = {
            ("2026-05-30", "confirmable_active_queue_50_lower"): "391",
            ("2026-05-30", "confirmable_active_queue_50_upper"): "402",
            ("2026-05-31", "confirmable_active_queue_50_lower"): "398",
            ("2026-05-31", "confirmable_active_queue_50_upper"): "405",
            ("2026-06-01", "confirmable_active_queue_50_lower"): "447",
            ("2026-06-01", "confirmable_active_queue_50_upper"): "456",
            ("2026-06-02", "confirmable_active_queue_50_lower"): "428",
            ("2026-06-02", "confirmable_active_queue_50_upper"): "434",
            ("2026-06-03", "confirmable_active_queue_50_lower"): "450",
            ("2026-06-03", "confirmable_active_queue_50_upper"): "456",
            ("2026-06-04", "confirmable_active_queue_50_lower"): "530",
            ("2026-06-04", "confirmable_active_queue_50_upper"): "536",
            ("2026-06-05", "confirmable_active_queue_50_lower"): "564",
            ("2026-06-05", "confirmable_active_queue_50_upper"): "569",
            ("2026-06-06", "confirmable_active_queue_50_lower"): "591",
            ("2026-06-06", "confirmable_active_queue_50_upper"): "596",
            ("2026-06-07", "confirmable_active_queue_50_lower"): "630",
            ("2026-06-07", "confirmable_active_queue_50_upper"): "637",
            ("2026-06-08", "confirmable_active_queue_50_lower"): "675",
            ("2026-06-08", "confirmable_active_queue_50_upper"): "681",
            ("2026-06-09", "confirmable_active_queue_50_lower"): "699",
            ("2026-06-09", "confirmable_active_queue_50_upper"): "704",
            ("2026-06-10", "confirmable_active_queue_50_lower"): "736",
            ("2026-06-10", "confirmable_active_queue_50_upper"): "741",
            ("2026-06-11", "confirmable_active_queue_50_lower"): "764",
            ("2026-06-11", "confirmable_active_queue_50_upper"): "770",
            ("2026-06-13", "confirmable_active_queue_50_lower"): "868",
            ("2026-06-13", "confirmable_active_queue_50_upper"): "875",
            ("2026-06-14", "confirmable_active_queue_50_lower"): "893",
            ("2026-06-14", "confirmable_active_queue_50_upper"): "900",
            ("2026-06-15", "confirmable_active_queue_50_lower"): "922",
            ("2026-06-15", "confirmable_active_queue_50_upper"): "929",
            ("2026-06-16", "confirmable_active_queue_50_lower"): "966",
            ("2026-06-16", "confirmable_active_queue_50_upper"): "974",
            ("2026-06-17", "confirmable_active_queue_50_lower"): "986",
            ("2026-06-17", "confirmable_active_queue_50_upper"): "993",
            ("2026-06-18", "confirmable_active_queue_50_lower"): "1033",
            ("2026-06-18", "confirmable_active_queue_50_upper"): "1041",
            ("2026-06-19", "confirmable_active_queue_50_lower"): "1043",
            ("2026-06-19", "confirmable_active_queue_50_upper"): "1050",
            ("2026-06-20", "confirmable_active_queue_50_lower"): "1087",
            ("2026-06-20", "confirmable_active_queue_50_upper"): "1094",
            ("2026-06-21", "confirmable_active_queue_50_lower"): "1124",
            ("2026-06-21", "confirmable_active_queue_50_upper"): "1129",
            ("2026-06-22", "confirmable_active_queue_50_lower"): "1177",
            ("2026-06-22", "confirmable_active_queue_50_upper"): "1183",
            ("2026-06-23", "confirmable_active_queue_50_lower"): "1207",
            ("2026-06-23", "confirmable_active_queue_50_upper"): "1214",
            ("2026-06-24", "confirmable_active_queue_50_lower"): "1232",
            ("2026-06-24", "confirmable_active_queue_50_upper"): "1238",
            ("2026-06-25", "confirmable_active_queue_50_lower"): "1286",
            ("2026-06-25", "confirmable_active_queue_50_upper"): "1293",
            ("2026-06-27", "confirmable_active_queue_50_lower"): "1387",
            ("2026-06-27", "confirmable_active_queue_50_upper"): "1397",
            ("2026-06-29", "confirmable_active_queue_50_lower"): "1478",
            ("2026-06-29", "confirmable_active_queue_50_upper"): "1491",
            ("2026-06-30", "confirmable_active_queue_50_lower"): "1551",
            ("2026-06-30", "confirmable_active_queue_50_upper"): "1564",
            ("2026-07-01", "confirmable_active_queue_50_lower"): "1615",
            ("2026-07-01", "confirmable_active_queue_50_upper"): "1629",
            ("2026-07-02", "confirmable_active_queue_50_lower"): "1653",
            ("2026-07-02", "confirmable_active_queue_50_upper"): "1667",
            ("2026-07-03", "confirmable_active_queue_50_lower"): "1679",
            ("2026-07-03", "confirmable_active_queue_50_upper"): "1693",
            ("2026-07-04", "confirmable_active_queue_50_lower"): "1712",
            ("2026-07-04", "confirmable_active_queue_50_upper"): "1726",
            ("2026-07-05", "confirmable_active_queue_50_lower"): "1784",
            ("2026-07-05", "confirmable_active_queue_50_upper"): "1799",
            ("2026-07-06", "confirmable_active_queue_50_lower"): "1875",
            ("2026-07-06", "confirmable_active_queue_50_upper"): "1890",
            ("2026-07-07", "confirmable_active_queue_50_lower"): "1944",
            ("2026-07-07", "confirmable_active_queue_50_upper"): "1961",
            ("2026-07-08", "confirmable_active_queue_50_lower"): "1980",
            ("2026-07-08", "confirmable_active_queue_50_upper"): "1997",
            ("2026-07-09", "confirmable_active_queue_50_lower"): "2022",
            ("2026-07-09", "confirmable_active_queue_50_upper"): "2040",
            ("2026-07-10", "confirmable_active_queue_50_lower"): "2053",
            ("2026-07-10", "confirmable_active_queue_50_upper"): "2070",
            ("2026-07-11", "confirmable_active_queue_50_lower"): "2106",
            ("2026-07-11", "confirmable_active_queue_50_upper"): "2123",
            ("2026-07-12", "confirmable_active_queue_50_lower"): "2139",
            ("2026-07-12", "confirmable_active_queue_50_upper"): "2155",
            ("2026-07-13", "confirmable_active_queue_50_lower"): "2192",
            ("2026-07-13", "confirmable_active_queue_50_upper"): "2209",
            ("2026-07-14", "confirmable_active_queue_50_lower"): "2241",
            ("2026-07-14", "confirmable_active_queue_50_upper"): "2256",
            ("2026-07-15", "confirmable_active_queue_50_lower"): "2285",
            ("2026-07-15", "confirmable_active_queue_50_upper"): "2300",
            ("2026-07-16", "confirmable_active_queue_50_lower"): "2346",
            ("2026-07-16", "confirmable_active_queue_50_upper"): "2361",
            ("2026-07-17", "confirmable_active_queue_50_lower"): "2436",
            ("2026-07-17", "confirmable_active_queue_50_upper"): "2452",
            ("2026-07-18", "confirmable_active_queue_50_lower"): "2508",
            ("2026-07-18", "confirmable_active_queue_50_upper"): "2522",
            ("2026-07-19", "confirmable_active_queue_50_lower"): "2582",
            ("2026-07-19", "confirmable_active_queue_50_upper"): "2597",
            ("2026-07-20", "confirmable_active_queue_50_lower"): "2636",
            ("2026-07-20", "confirmable_active_queue_50_upper"): "2651",
            ("2026-07-21", "confirmable_active_queue_50_lower"): "2703",
            ("2026-07-21", "confirmable_active_queue_50_upper"): "2719",
            ("2026-07-22", "confirmable_active_queue_50_lower"): "3064",
            ("2026-07-22", "confirmable_active_queue_50_upper"): "3078",
            ("2026-07-23", "confirmable_active_queue_50_lower"): "3148",
            ("2026-07-23", "confirmable_active_queue_50_upper"): "3164",
            ("2026-07-24", "confirmable_active_queue_50_lower"): "3242",
            ("2026-07-24", "confirmable_active_queue_50_upper"): "3258",
            ("2026-07-25", "confirmable_active_queue_50_lower"): "3368",
            ("2026-07-25", "confirmable_active_queue_50_upper"): "3383",
        }
        # The exporter must preserve every model-produced date window exactly.
        # Do not retain historical expectations after the queue basis was
        # corrected to require the complete active-suspected queue: SitRep 18
        # is the last eligible edition, and later isolation-only subsets are not
        # interchangeable with that denominator.
        expected = {}
        # Reading the durable snapshot avoids re-pinning the full historical
        # projection whenever a reviewed daily lab-yield endpoint is added.
        live = json.loads(
            (pathlib.Path(__file__).resolve().parents[1] / "data/live-bdbv-2026-output.json").read_text()
        )
        for window in live["visibility"]["active_queue_projection"]["per_date_windows"]:
            lower, upper = window["confirmable_active_queue_50"]
            expected[(window["date"], "confirmable_active_queue_50_lower")] = str(lower)
            expected[(window["date"], "confirmable_active_queue_50_upper")] = str(upper)
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
        self.assertIn("4401", by_surface["visibility_module_c"]["input_values"])
        # The retired cumulative-suspected figure (349) must no longer appear on
        # the visibility input surface; confirmed is now the only cumulative input.
        self.assertNotIn("349", by_surface["visibility_module_c"]["input_values"])
        # C2 is not issued this cycle: its carried queue's expected yield is
        # already inside the confirmed count, so publishing a projection built on
        # that queue's own (older) confirmed base would plot a stale total below
        # the current confirmed line and double-count what has already resolved.
        # The row must still appear, naming why nothing was published.
        self.assertEqual(
            "not_issued",
            by_surface["active_queue_projection_c2"]["status"],
        )
        self.assertIn(
            "already inside the confirmed count",
            by_surface["active_queue_projection_c2"]["clock_basis"],
        )
        self.assertEqual(
            "not_issued_this_cycle",
            by_surface["active_queue_projection_c2"]["model_use"],
        )
        # C2 tracks the most recent edition that published a usable queue, and
        # the confirmed anchor comes from that same edition. Once the full
        # active-suspected total stops being published it falls back to the
        # suspected-in-isolation basis, which the audit row must name rather than
        # silently substitute.
        c2_values = by_surface["active_queue_projection_c2"]["input_values"]
        self.assertIn("4073", c2_values)
        self.assertIn("370", c2_values)
        self.assertEqual(
            "updated_snapshot_level",
            by_surface["death_back_projection_and_grid"]["status"],
        )
        self.assertIn("2013", by_surface["death_back_projection_and_grid"]["input_values"])
        self.assertIn(
            "insp-linkedin-sitrep-086-2026-08-09",
            by_surface["death_back_projection_and_grid"]["clock_basis"],
        )
        self.assertEqual("", by_surface["death_back_projection_and_grid"]["held_out_reason"])
        self.assertEqual(
            "source_attribution_lag",
            by_surface["corridor_watchlist"]["status"],
        )
        # 2026-07-22 reviewed SitRep69 Table 2: zone-attributed confirmed is
        # 2905, so unallocated headline/cross-border attribution lag is 20
        # (Uganda only; the harmonization allocated the 17-case Ituri residual).
        self.assertIn("4053", by_surface["corridor_watchlist"]["input_values"])
        self.assertIn("348", by_surface["corridor_watchlist"]["input_values"])
        self.assertIn("inrb-sitrep-083-2026-08-05", by_surface["corridor_watchlist"]["blocked_by"])

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
